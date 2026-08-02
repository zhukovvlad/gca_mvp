# Парсинг УПД (PDF)

Парсинг через абстракцию LLM-провайдера (`backend/llm.py`, переключатель
`LLM_PROVIDER`; дефолт — OpenRouter/Claude Vision). Как настраивать провайдера,
менять ключ/модель и звать LLM из кода — `docs/instructions/llm-provider.md`.
Обработка документа разбита
на чистую фазу A (`pdf_parser.parse_pdf`) и транзакционную фазу B
(`processing.persist_parse_result`) — см. «Обработка документа» ниже. Спека:
`docs/superpowers/specs/2026-07-16-async-processing-design.md`; план:
`docs/superpowers/plans/2026-07-17-async-processing-stage-0.md`; итог —
`docs/devlog/2026-07-17-async-processing-stage-0.md`.

## Обработка документа: статусная модель и фазы A/B (ступень 0)

`Document.status`: `pending → processing → parsed | error`. Новый документ
создаётся в `pending` (ORM- и `server_default`). Переход в `processing` —
атомарный guard `crud.documents.try_acquire_processing`
(`UPDATE documents SET status='processing', ... WHERE id=:id AND status != 'processing'`,
**коммитится немедленно** — иначе переход не виден другим сессиям: конкурентный
запрос не получит 409, а фоновая таска на ступени 1+ не увидит `processing`).
Guard не захватил строку (документ уже обрабатывается) → эндпоинт отвечает 409
без вызова обработки. Поля статусной модели и сама миграция — `docs/agent/database.md`.

Три слоя вызова (`backend/processing.py`), неизменные между ступенями —
между ступенями меняется только внешняя обёртка (§2.2 спеки):

- **`process_document(doc_id, *, mode, pdf_bytes=None, session_factory=None, reraise=False)`**
  — обёртка ступени 0/1: приводит ЛЮБОЙ исход попытки к терминальному статусу.
  Доменная ошибка → `write_processing_error` (см. ниже); `asyncio.CancelledError`
  (обрыв клиента/отмена таски) → `error` + `'Обработка прервана'` + re-raise
  (детерминированный исход, AC-S0-2); непредвиденное исключение → `error` +
  `f"Ошибка обработки: {exc}"`. `reraise=True` пробрасывает ошибку дальше
  ТОЛЬКО если у неё задан `http_status` (ошибки ориентации deskew, AC-S0-8) —
  эндпоинт сохраняет прежний HTTP-код клиенту; ошибки парсинга
  (`http_status=None`) гасятся в `error` + HTTP 200 (поведение API upload/reparse
  на ступени 0 не меняется). Успех фиксируется внутри фазы B, до этой обёртки
  не долетает.
- **`run_processing_attempt(session_factory, doc_id, *, mode, pdf_bytes=None)`**
  — одна попытка: (скачать / `_run_deskew` при `mode="deskew"`) → фаза A
  (`parse_pdf`) → фаза B (`persist_parse_result`). Доменные ошибки НЕ гасит —
  пробрасывает наверх с накопленным учётом стоимости (составная попытка, §2.5:
  оплаченная стоимость deskew-детекта прибавляется к исходу фазы A и при
  успехе, и при ошибке парсинга, AC-S0-10). Это ядро, одинаковое для ступеней
  0/1/2 — меняется только `process_document`.
- **`parse_pdf` (фаза A, `pdf_parser.py`)** — чистая функция, без обращения к
  БД: зовёт `llm.get_provider().vision_completion(...)` (транспорт/envelope —
  в `llm_openrouter.py` за интерфейсом `llm.py`), разбирает ответ, возвращает
  `ParseOutcome` (`doc_type`, `invoices`, `cost_usd`, `paid_calls`). Ошибку
  провайдера (`LLMProviderError`) маппит в `Transient`/`PermanentError` с
  сохранением биллинга. Материалы НЕ резолвятся в id — это делает фаза B.
- **`persist_parse_result` (фаза B, `processing.py`)** — одна транзакция:
  `SELECT ... FOR UPDATE` строки документа, повторная проверка verified-СФ
  (могла появиться после guard-перехода, пока шёл длинный LLM-вызов, S0-8),
  удаление старых СФ, резолв поставщиков/классов материалов (flush), вставка
  новых СФ, `status='parsed'`, атомарный SQL-инкремент `parse_cost_usd`/`parse_count`,
  **единственный `commit`** (parse-then-swap — старые СФ живы до самого свапа,
  ошибка парсинга их не трогает). Детерминированный сбой (flush/insert) → явный
  `rollback` (без него следующая error-запись через ту же сессию упадёт
  `PendingRollbackError`) + `TransientError` с учётом стоимости; сбой самого
  `commit` (ambiguous — исход неизвестен) — тоже `TransientError`; разруливает
  условная error-запись (ниже).

### Доменные ошибки (`ProcessingError` / `TransientError` / `PermanentError`)

Определены в `processing.py`; `pdf_parser.py` и `pdf_orientation.py` их
импортируют (обратный импорт `ParseOutcome` из `pdf_parser` в `processing.py`
— только под `TYPE_CHECKING`, иначе циклический импорт в рантайме). Каждая
несёт `cost_usd`/`paid_calls` (инвариант «HTTP 200 → деньги потрачены →
стоимость учтена» держится и на error-пути) и опционально `http_status`.

- **`TransientError`** — S3 недоступен, сетевой сбой/таймаут httpx, 5xx/429/408
  OpenRouter, сбой vision-детекта ориентации. На ступени 2 получит
  retry-политику; на ступени 0/1 ведёт к терминальному `error`.
- **`PermanentError`** — невалидный JSON, провал сверки итогов
  (`_reconcile_totals`), `finish_reason=length`, `doc_type != invoice`, ноль
  разобранных СФ, слишком много страниц для deskew (>20). Не ретраится никогда.
- **`http_status`** задают ТОЛЬКО ошибки ориентации (413 слишком много страниц,
  502 сервис распознавания недоступен, AC-S0-8) — единственный случай, где
  ступень 0 обязана довести до клиента прежний HTTP-код. Ошибки парсинга
  `http_status` не задают → гасятся в `status='error'` + HTTP 200.

### Условная идемпотентная error-запись (§2.3)

`write_processing_error(session_factory, doc_id, message, *, cost_usd, paid_calls, retries=3)`
выполняет `UPDATE documents SET status='error', last_error=..., parse_cost_usd += ...,
parse_count += ... WHERE id=:id AND status='processing'`. Условие на текущий
статус — если фаза B уже успела закоммитить swap (`parsed`) раньше, чем ошибка
добралась до записи (ambiguous commit), UPDATE дожидается исхода конкурирующей
транзакции и перепроверяет предикат на свежей версии строки (Postgres
EvalPlanQual) → `rowcount=0`, запись молча пропускается — двойной статус не
пишется, без гонки. Ретраится ТОЛЬКО потеря соединения из самого `commit`
(`connection_invalidated` или SQLSTATE класса `08`, `_is_connection_error`) —
эта запись идемпотентна, повтор безопасен. Любая другая ошибка (deadlock
`40P01`, `lock_timeout`, `statement cancellation 57014`, прочий `DBAPIError`
или `Exception`) детерминирована → пробрасывается немедленно, чтобы баг падал
в тестах, а не оставлял документ в `processing` молча (F8). Исчерпание
connection-ретраев → `logger.critical`, документ остаётся `processing` до
рестарта/ручного восстановления (добирает startup-sweep на ступени 1);
стоимость этой конкретной попытки при этом теряется (at-most-once — редкий
вырожденный случай, устраняется только персистентной очередью на ступени 2).
`doc_type` на error-пути НЕ трогается — при parse-then-swap документ хранит
живые старые СФ, флип `invoice → unknown` был бы противоречив.

### Инъекция фабрики сессий (`get_processing_session_factory`, F1)

`process_document`/`run_processing_attempt`/`write_processing_error` принимают
`session_factory` вместо жёстко захардкоженного `SessionLocal()`. Причина —
тестовая инфраструктура: `db_session` в `conftest.py` открывает транзакцию с
`join_transaction_mode="create_savepoint"` (каждый тест — savepoint внутри
одной внешней транзакции, изоляция без пересоздания схемы между тестами).
Голая `SessionLocal()` открыла бы ВТОРОЕ соединение к БД, которое не видит
незакоммиченные изменения тестовой транзакции (и наоборот обработка не
увидела бы тестовые фикстуры) — обработка молча работала бы с пустой/другой
БД. `get_processing_session_factory` — FastAPI-dependency; по умолчанию
возвращает `database.SessionLocal` (поздний импорт внутри функции — не
связывается на этапе определения модуля, иначе тесты открывали бы сессию на
реальном dev `DATABASE_URL` ещё до применения патчей/оверрайдов); в тестах
переопределяется через `app.dependency_overrides` на фабрику, отдающую
тестовую сессию той же транзакции.

Deployment-инвариант S1: `workers=1, replicas=1`, no-overlap deployment
(stop-then-start). Startup-sweep в lifespan переводит все `pending|processing`
в `error` на старте. Потребность в `workers>1`/rolling — триггер Ступени 2
(advisory-lock). Guard мутаций СФ/документа (`_reject_if_busy`,
`routers/invoices.py`) отклоняет 409-м оба нетерминальных статуса —
`pending` включён осознанно (S1): в окне между commit `create_document`
(`pending`) и guard-commit (`processing`) документ иначе можно было бы
удалить, оставив S3-сироту; легитимных мутаций `pending`-документа не
существует.

## Асинхронная обработка: 202-контракт, дедуп, polling (ступень 1)

Спека: `docs/superpowers/specs/2026-07-19-async-processing-stage-1-design.md`;
план: `docs/superpowers/plans/2026-07-19-async-processing-stage-1.md`; итог —
`docs/devlog/2026-07-19-async-processing-stage-1.md`.

### 202-контракт эндпоинтов (S1-1, S1-2)

Все три эндпоинта обработки (`POST /upload`, `POST /documents/{id}/reparse`,
`POST /documents/{id}/deskew-reparse`) выполняют только быструю синхронную
часть (валидация, для upload — запись в S3 и дедуп-проверка, существующие
404/400/verified-409, guard `try_acquire_processing`) и ставят
`process_document` в `BackgroundTasks.add_task(...)` вместо `await`. Ответ —
`202 Accepted` + сериализованный документ со `status="processing"`,
`invoices: []` (СФ ещё нет). Обрыв клиента после получения 202 не влияет на
исход: Starlette выполняет `BackgroundTasks` после отправки ответа независимо
от состояния соединения (AC-S1-2, проверяется ручным смоуком — TestClient
исполняет фоновые таски ДО возврата из `client.post(...)`, поэтому wall-clock
и обрыв соединения непроверяемы в CI).

`rotations_applied` из ответа deskew удалён (фронт его не читал).
Deskew-эндпоинт больше не мапит `413`/`502` синхронно (отвечать в фоне
некому) — ошибки ориентации доезжают как `status="error"` + человекочитаемый
`last_error` через polling, ровно так же, как ошибки парсинга.

Контракт upload — единый shape независимо от кода: ключ `duplicate`
присутствует всегда (`false` на свежем 202, `true` на дубликате), ключ
`invoices` присутствует всегда (`[]` на свежем документе, реальный набор
победителя — на дубликате). Тип фронта:
`UploadResponse = DocumentDetail & { duplicate: boolean }`
(`frontend/src/types/invoice.ts`).

### Дедуп upload по file_hash (Q6)

`documents.file_hash` (`String(64)`, индекс) и констрейнт
`uq_documents_project_file_hash` — на схеме уже с ревизии `d1e2f3a4b5c6`;
ступень 1 добавляет вычисление и использование. Алгоритм `upload_pdf`
(`routers/invoices.py`):

1. `file_hash = sha256(байтов файла)` — хеш **оригинала**, до какой-либо
   коррекции. После deskew хеш НЕ пересчитывается (инвариант Q6: пересчёт
   молча сломал бы дедуп — deskew-копия того же документа перестала бы
   находиться как дубликат себя же).
2. Fast-path: `SELECT` документа по `(project_id, file_hash)`. Найден →
   `200 {"duplicate": true, ...}` с существующим документом; S3 не пишем,
   новый документ не создаём. Дубль оригинала, ещё не завершившего
   обработку, возвращает существующий документ со `status="processing"` —
   это ожидаемо, не гонка.
3. Не найден → запись в S3 → `create_document(..., file_hash=file_hash)`.
4. Гонка двух параллельных upload одного файла: commit падает
   `IntegrityError` на уникальном констрейнте → обязательный `db.rollback()`
   (без него сессия в failed state) → повторный `SELECT` победителя:
   - победитель найден → best-effort `delete_file_async` своего S3-объекта
     (осиротел) → `200 duplicate:true` с документом-победителем;
   - `winner is None` (IntegrityError не про этот констрейнт, например FK) →
     свой S3-объект всё равно удаляется, но исходная ошибка
     **перевыбрасывается** — дубликатом не маскируется.
5. Не дубликат → guard → `add_task` → `202` (§ выше).

Бэкфилл `file_hash` для исторических документов не делается — дедуп
работает только для новых загрузок.

### Polling и терминальный переход (S1-5, S1-7)

Фронт: `useDocuments`/`useDocument` (`services/queries.ts`) получают
`refetchInterval: processingRefetchInterval`
(`services/processingRefetchInterval.ts`) — 2500 мс, пока в данных квери есть
документ в нетерминальном статусе (`pending`/`processing`), иначе `false`
(polling останавливается сам). Один и тот же модуль экспортирует
`NON_TERMINAL_STATUSES`, переиспользуемый детектором и UI-дизейблами.

Переход `pending|processing → parsed|error` детектируется одним глобальным
подписчиком `QueryCache` (`services/terminalTransition.ts`,
`subscribeTerminalTransitions` вызывается один раз в `App.tsx`) — общая
`Map<documentId, status>` на весь детектор, инвалидация documents
list/detail + dashboard ровно на переходе (не на постановке). Семантика —
at-least-once: запоздалый out-of-order ответ может дать лишнюю
(идемпотентную) инвалидацию, но не пропустит терминальный переход. Тосты
мутаций reparse/deskew заменены на «Обработка запущена» (успех мутации
означает постановку в очередь, не завершение); отдельного тоста на самом
терминальном переходе нет — обратная связь идёт через смену статуса/бейджа.

### Startup-sweep — deployment-инвариант

См. «Deployment-инвариант S1» выше (в подразделе про инъекцию фабрики
сессий): sweep в lifespan переводит `pending|processing` в `error` на
старте процесса; корректность держится на no-overlap deployment
(`workers=1, replicas=1`, stop-then-start).

## Трекинг стоимости разбора

`OpenRouterProvider` (за интерфейсом `llm.py`) шлёт `usage: {include: true}` и
захватывает реальную стоимость (`paid_calls=1`/`cost` из `usage.cost`) по факту
HTTP 200 — так даже 200 с непарсящимся телом учитывается как платный вызов;
ошибка после 200 доходит до `parse_pdf` как `LLMProviderError` с накопленным
`cost_usd`/`paid_calls` (домен маппит в `ProcessingError`). `usage: null` при
этом больше не роняет разбор — успех с `cost=0` (закрытый пункт `docs/TECH_DEBT.md`). `ParseOutcome.cost_usd`/`paid_calls`
(успех) или те же атрибуты на доменной ошибке (провал) доходят до
`persist_parse_result`/`write_processing_error`, которые начисляют их
**атомарным SQL-инкрементом** (`parse_cost_usd = parse_cost_usd + v`) —
защита от гонки параллельных разборов одного документа. При `mode="deskew"`
`run_processing_attempt` дополнительно прибавляет `detect_cost`/`detect_calls`
коррекции ориентации (S0-9, ниже) к исходу фазы A — и при успехе, и при
ошибке парсинга (AC-S0-10). Инвариант, движок `native` vs OCR-плагин и детали
происхождения стоимости — `docs/superpowers/specs/2026-07-16-parse-cost-tracking-design.md`;
итог первой реализации — `docs/devlog/2026-07-16-parse-cost-tracking.md`;
перенос на фазы A/B и учёт detect-стоимости — `docs/devlog/2026-07-17-async-processing-stage-0.md`.

## Guard полноты разбора

`parse_pdf` (фаза A) бросает `PermanentError` (ничего не сохраняя — фаза A не
трогает БД) в трёх случаях:

1. `finish_reason == "length"` в ответе API — модель упёрлась в лимит токенов, ответ обрезан.
2. `_reconcile_totals` обнаруживает расхождение между `SUM(item.amount)` и извлечённым из документа `doc_total_without_vat` («Всего к оплате» без НДС) сверх допуска `max(1 ₽, 0.1%)`.
3. Ноль разобранных СФ (пустой `invoices`, либо у всех СФ кривая дата) — раньше это давало артефакт «`parsed` с 0 СФ» (Q2, класс 2); фаза A исключает его на входе.

Это предотвращает тихое сохранение неполного счёта (например 60 из 66 строк) под высоким confidence. Промпт содержит обязательный шаг самопроверки: модель сверяет `SUM(amount)` с `doc_total_without_vat` и ищет пропущенные строки перед закрытием JSON. `OPENROUTER_MAX_TOKENS=64000` (legacy-алиас `AI_MAX_TOKENS`) — верхний предел вывода claude-sonnet-4.6.

## Выбор движка

- **`OPENROUTER_PDF_ENGINE=native` (код-дефолт и рекомендуемое рабочее значение; legacy-алиас `PDF_ENGINE`):** Claude смотрит на PDF как на изображения, промпт ~10k токенов — стабильнее на длинных СФ.
- **`mistral-ocr`:** ~24k токенов промпта (повторяющиеся шапки страниц), нестабилен на СФ с 60+ одинаковыми строками — пропускает или дублирует строки даже при `finish_reason=stop`. Был код-дефолтом до 2026-07-27 — закрытая запись в `docs/TECH_DEBT.md`.

Ещё не реализовано: постраничный chunking для СФ на 100+ строк — см. `docs/TECH_DEBT.md`.

## On-demand коррекция ориентации (deskew-reparse)

Модуль `backend/pdf_orientation.py` + эндпоинт `POST /api/invoices/documents/{id}/deskew-reparse`
(кнопка «Выпрямить и переразобрать» в Review и ErrorDocsTab). Вызывается через
`process_document(mode="deskew", reraise=True, ...)`; `_run_deskew` в
`processing.py` координирует S3 (бэкап/перезапись) и `pdf_orientation.deskew_pdf`.

- **Детект:** vision-предзапрос через провайдер (`detect_rotations` → `llm.get_provider()`,
  `ImagesAttachment`, `max_tokens≈200`, `timeout=Timeout(30, connect=5.0)`). Модель возвращает
  per-page **насколько страница ПОВЁРНУТА по часовой стрелке** (0/90/180/270), а не «сколько
  докрутить». Классификация исходов (**§2.5** спеки LLM-провайдера):
  - >20 страниц → `PermanentError(http_status=413)` (детект не вызывается);
  - транспорт/таймаут/5xx/408/429 → `TransientError(http_status=502)` (детект не оплачен, `cost` не читается);
  - прочие не-2xx → `PermanentError(http_status=502)`;
  - **битый envelope платного 200** (нет `choices` и т.п.) → `PermanentError(http_status=502)` **с** учётом стоимости (`cost`/`paid_calls=1`) — НЕ тихие нули (иначе переразобрали бы оригинал под видом исправленного);
  - целый envelope, но непарсящийся МАССИВ поворотов → нули (безопасная деградация уровня контента), `cost` из `usage` возвращается — вызов оплачен (**S0-9**).
- **Коррекция:** селективный raster (`apply_rotations`) — `pypdfium2` перерисовывает ТОЛЬКО
  повёрнутые страницы с ОТМЕНЯЮЩИМ поворотом `(360 − detected) % 360` (pypdfium2 `render(rotation=)`
  крутит по часовой, поэтому страницу, повёрнутую на R°, выпрямляет рендер на 360−R°; grayscale,
  native-aware DPI: `image_px_width/(page_width_pt/72)`, кап 300, пол 150), прямые переносятся
  как есть; сборка через `pikepdf`. У пересобранной страницы `/Rotate=0`. Рендер и растеризация
  CPU-bound — уходят в поток через `anyio.to_thread` (S0-6), event loop во время deskew свободен.
- **Почему raster, а не `/Rotate`-флаг:** спайk 2026-06-15 — на mistral-ocr флаг давал
  стабильно-неверное количество (conf 0.72 > порога), raster — верное и conf ниже порога.
- **S3:** оригинал бэкапится в `{key}.orig`; deskew всегда стартует от оригинала (идемпотентно);
  исправленный PDF перезаписывает основной ключ. `_is_not_found` различает «нет бэкапа» (ожидаемо
  на первом deskew, fallback на исходный ключ) от транзиентного сбоя S3 — сбой ПОСЛЕ оплаченного
  детекта оборачивается в `TransientError` с `detect_cost`, чтобы стоимость не терялась в
  generic-ветке `process_document` (F3, §2.3).
- **Ограничение:** born-digital повёрнутый PDF деградирует от перерисовки (оригинал в `.orig`).
- **HTTP-контракт (AC-S0-8):** ошибка ориентации (413/502) доходит до клиента прежним HTTP-кодом
  через `reraise=True` + `exc.http_status`, но статус документа уже записан в `error` (с учётом
  стоимости детекта) ДО проброса — раньше при 413/502 документ не трогался. Побочный эффект:
  документ с живыми старыми СФ теперь может попасть в ErrorDocsTab из-за одной лишь ошибки
  ориентации; причина видна в `last_error`, обычный reparse (без лимита страниц) чинит.
- **Долг:** синхронный HTTP-запрос клиента длинный (detect+reparse, worst-case ~6 мин) — event
  loop сервера на ступени 0 уже не блокируется (S3 через `anyio`/S0-6 + растеризация в потоке),
  но клиент всё равно ждёт весь цикл целиком; нужно поднять read-timeout фронт-прокси для роута.
  Настоящая асинхронная обёртка (job-очередь + поллинг клиента) — предмет ступени 1/2.

## Нормализация единиц при записи

Парсер (фаза A) возвращает сырую строку `unit` и `material_type` code — **без изменений** (не нормализует). Нормализация выполняется в фазе B (`persist_parse_result`) при разборе/переразборе документа и в `PUT /invoices/{id}` (`update_invoice`) при ручном редактировании — оба места вызывают одни и те же хелперы:

1. `load_alias_map(db)` — загружает `unit_aliases` в память.
2. `normalize_item(item, alias_map)` — для каждой позиции находит `normalized_unit_id` по ключу `normalize_unit_key(raw_unit)`, вычисляет `normalized_quantity` и `normalized_unit_price`.
3. `get_or_create_material_class` резолвит `material_type` code → `material_type_id`; неизвестный code → 422 через API, в PDF-парсере — fallback на `"other"` с записью в лог (hallucinated code не обрывает обработку документа).

`normalize_unit_key` — единственный источник правды в `crud/units.py`: NFKC-нормализация (складывает м³→м3), collapse whitespace, lowercase, strip trailing dots. Используется в рантайме, миграции и тестах.

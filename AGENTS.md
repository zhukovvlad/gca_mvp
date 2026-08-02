# Проект: База расценок генподряда (MVP) — v6

Инструкция для кодового агента. Прочитай целиком до начала работы. Архитектурные решения приняты — не пересматривай их молча. **Останавливайся и задавай вопрос**, если обнаружишь: фактическое противоречие с кодом исходных репозиториев, бизнес-неоднозначность, не покрытую этим документом, риск потери данных или необратимой миграции.

**Целевой репозиторий:** `github.com/zhukovvlad/gca_mvp` (создан пустым). Вся работа ведётся в нём. Этот документ — единственный источник истины по проекту: положить в корень как `AGENTS.md` первым коммитом (его читает Codex), рядом создать `CLAUDE.md` из одной строки-указателя «Прочитай и выполняй AGENTS.md — это основная инструкция проекта» (его читает Claude Code). Полный текст держать в одном файле, не в двух, — дублирование разъедется. Поддерживать актуальным при согласованных изменениях решений.

---

## 1. Бизнес-задача

Свести расценки всех договоров генподряда (ГП) 2024–2027 в единую базу:

1. **Единая база расценок** — по объектам и крупным статьям, со сквозным сравнением одной и той же работы между объектами и годами.
2. **Паспорт объекта на одной странице** — договор, подписант, дата, ключевые расценки, отклонения. Печатная форма.
3. **Нормативы расценок по классам объектов** — превышения подсвечиваются автоматически; переутверждение нормативов с учётом инфляции, с историей по периодам действия.
4. **Применение** — предметный торг с подрядчиками, защита цифр перед банком и партнёрами (выгрузки в Excel).

Анализ текстов самих договоров — **вне скоупа MVP** (отдельный модуль позже).

## 2. Исходные материалы (ревизии зафиксированы)

| Репозиторий | Commit SHA | Что берём | Что НЕ берём |
|---|---|---|---|
| `github.com/zhukovvlad/udp-tenders` | `98fb67667c4483afd90816d303c263614fe01ed3` | **Основа (форк):** структура backend/frontend, auth (JWT, ротация refresh, CSRF), `UnitOfMeasure`+`UnitAlias`, паттерны CRUD/роутеров/тестов, страницы Review/ReferencePrices/Reports/Settings, `just`, Alembic | `llm*.py`, `pdf_parser.py`, `pdf_orientation.py`, OpenRouter; MinIO/`s3.py` (см. §8); **мультиарендность: `Organization`, `ProjectOrganization`, все `org_id`** (см. §3); доменные модели УПД (`Invoice`, `InvoiceItem`, `Document`, `Supplier`, `MaterialClass`, `CompensationCorridor` — как код удалить, как паттерн подсмотреть) |
| `github.com/zhukovvlad/tenders-go` | `121718bf45df8ea7fdc5e6346471b137d84ded88` | **Схема БД** из `cmd/internal/db/migration/` (000001 consolidated, 000002 FTS, 000007–000008 grouping): эквивалентный перенос полей, типов, CHECK-констрейнтов и индексов. Логика транзакции `ImportFullTender` — как референс для порта на Python | Go-код как таковой, RAG-эндпоинты, Google File Search, `suggested_merges`-воркфлоу, `users`/`user_sessions` (auth из udp-tenders) |
| `github.com/zhukovvlad/parser_tender_xlsx` | `0e178c097d8097bc0ea981e7b4f517518461f61e` | `app/excel_parser/` целиком, `app/constants.py` | `celery_app`, `go_module`, `gemini_module`, `rag_google_module`, `json_to_server`, `markdown_utils`, `markdown_to_chunks`, `prompts`, `main.py` |

«Эквивалентный перенос» схемы допускает: переименование `tenders` → `estimates`, слияние units с `UnitAlias`, добавление новых таблиц и колонок (в т.ч. `normalized_job_title`, §4). Не допускает: изменение типов денежных полей, ослабление CHECK-ограничений, потерю частичных индексов.

## 3. Ключевые решения (зафиксированы, не пересматривать)

- **Single-tenant.** Внутренний инструмент одной компании. `organizations` и все `org_id` выпиливаются. Роли: `admin` — управление пользователями, классами, нормативами, замена/удаление смет; `member` — чтение, загрузка смет, ручной матчинг (Review).
- **Стек:** Python 3.12, FastAPI, SQLAlchemy 2.x sync ORM, Alembic, psycopg3; PostgreSQL 16 + pgvector (extension включить первой миграцией, векторный матчинг — фаза 2); React + TS, Vite, shadcn/ui, Tailwind, TanStack Query, TanStack Table, Recharts.
- **Без брокеров:** никаких Celery/Redis. Длинные операции — FastAPI `BackgroundTasks` + таблица `import_jobs`. **MVP запускается строго с одним worker-процессом uvicorn** — это условие корректности startup-recovery (§6); зафиксировать в конфиге запуска и README. Таймаут парсинга — **мягкий**: проверка прошедшего времени между этапами пайплайна, не жёсткое прерывание.
- **Деньги:** `numeric` в БД ↔ `Decimal` в Python ↔ строки в JSON. Никаких float. Пустая стоимость в смете → `NULL`, не `0`.
- **Источник истины при импорте — карточка договора.** `contract_id` передаётся в запросе; объект, подрядчик и реквизиты договора НЕ апсертятся из XLSX. Реквизиты из шапки файла сохраняются в raw_data; при расхождении с карточкой — записи в `import_jobs.warnings`.
- **Каталог работ общий.** Единица «идентичности» работы в каталоге — **нормализованное название + единица измерения** (§4).

## 4. Доменная модель

Ключевой факт: **смета к договору ГП структурно = тендерная таблица**, но с одним подрядчиком, без колонки «Расчётная стоимость» (baseline) и с заполненным `suggested_quantity` («Предполагаемое количество»).

```
rate_classes                 # классы объектов (справочник)

objects                      # из tenders-go + rate_class_id bigint NULL → rate_classes
                             #   (значение по умолчанию для новых договоров)
contractors                  # из tenders-go

contracts                    # договор ГП
  id, object_id NOT NULL → objects
  contractor_id NOT NULL → contractors
  rate_class_id NOT NULL → rate_classes    # СНИМОК класса на момент создания договора;
                                           # переклассификация объекта не меняет прошлое
  contract_number, title, signer, signed_date date NOT NULL, total_amount numeric, notes
  # signed_date обязателен: это фолбэк даты сравнения с нормативом (§4), обе даты
  # не могут быть NULL одновременно
  UNIQUE (contract_number)

estimates                    # смета (бывш. tenders); 1 договор : N смет
  id, contract_id NOT NULL → contracts
  amendment_no int NULL      # NULL = исходная смета, иначе номер доп. соглашения
  title, data_prepared_on_date date
  import_job_id → import_jobs
  UNIQUE NULLS NOT DISTINCT (contract_id, amendment_no)   # PG16

estimate_raw_data
  estimate_id PK → estimates ON DELETE CASCADE
  raw_data jsonb NOT NULL    # полный JSON парсера, включая реквизиты из шапки XLSX
  parser_version text NOT NULL
  created_at

lots / proposals / position_items
  # перенос из tenders-go. proposals — РОВНО ОДНО на лот (единственный подрядчик);
  # слой сохраняем: JSON парсера ложится 1:1, на proposal висят summary_lines и
  # additional_info, задел на возврат тендеров в будущем.
  # position_items: quantity, suggested_quantity,
  #   unit_cost_{materials,works,indirect_costs,total}, total_cost_{...},
  #   is_chapter, chapter_ref_in_proposal, catalog_position_id NULL, unit_id,
  #   комментарии. deviation_from_baseline_cost остаётся NULL (baseline в сметах нет).
```

Каталожный контур:

```
catalog_positions
  standard_job_title text NOT NULL        # отображаемое название (человекочитаемое)
  normalized_job_title text NOT NULL      # НОВАЯ колонка: normalize(standard_job_title),
                                          # вычисляется в Python при insert/update
  unit_id NULL → units_of_measurement
  kind ('POSITION|HEADER|LOT_HEADER|TRASH|TO_REVIEW'), status,
  embedding vector(768) NULL, FTS по standard_job_title (из миграции 000002)
  # УНИКАЛЬНОСТЬ — по нормализованной паре (это и есть идентичность работы):
  #   UNIQUE (normalized_job_title, COALESCE(unit_id, -1))         -- raw SQL
  # Индекс по standard_job_title — обычный, для поиска в UI.
  # Частичные индексы (WHERE kind = 'POSITION' и т.п.) — raw SQL через op.execute().
  # Матчинг и get-or-create TO_REVIEW работают по ТОЙ ЖЕ нормализованной паре,
  # что и matcher, — никаких вторых представлений строки.

matching_cache
  cache_key text PK           # sha256(norm_version || '|' || normalized_title || '|' || unit_norm)
                              # unit_norm = каноническое имя единицы после разрешения алиасов,
                              # '' если единицы нет. ЕДИНИЦА ОБЯЗАТЕЛЬНА В КЛЮЧЕ.
  norm_version smallint NOT NULL
  job_title_text text NOT NULL           # исходное название — нужно для перевыпуска ключей
  unit_text text                          # исходная единица — то же
  catalog_position_id NOT NULL → catalog_positions
  source text NOT NULL CHECK (source IN ('auto','manual'))
  expires_at timestamptz NULL
  # ПРАВИЛО TTL: source='auto' → expires_at = now()+30 дней, продлевается при hit;
  #             source='manual' (решение из Review) → expires_at = NULL, НЕ истекает.
  # Инкремент norm_version: миграция обязана перевыпустить ключи всех source='manual'
  # записей из (job_title_text, unit_text) новой нормализацией; 'auto' можно отбросить.

units_of_measurement          # слить с UnitOfMeasure + UnitAlias из udp-tenders;
                              # алиасы «м2»/«кв.м»/«м²» обязательны
```

Нормативы:

```
rate_standards
  id, catalog_position_id NOT NULL, rate_class_id NOT NULL
  standard_unit_rate numeric NOT NULL CHECK (standard_unit_rate > 0)
  valid_from date NOT NULL, valid_to date NULL      # период = [valid_from, valid_to)
  CHECK (valid_to IS NULL OR valid_to > valid_from)
  inflation_index numeric NULL, approved_by, approved_at, note
  # Запрет пересечений (btree_gist):
  #   EXCLUDE USING gist (catalog_position_id WITH =, rate_class_id WITH =,
  #                       daterange(valid_from, valid_to, '[)') WITH &&)
  #   NULL в valid_to даёт бесконечную верхнюю границу — COALESCE не нужен.
  # Переутверждение = UPDATE valid_to старой строки + INSERT новой. Историю не мутировать.
```

Семантика отклонений (VIEW `v_position_deviations`):

- Дата сравнения: `estimates.data_prepared_on_date`, фолбэк `contracts.signed_date`.
- Норматив по `(catalog_position_id, contracts.rate_class_id, дата ∈ [valid_from, valid_to))` — благодаря EXCLUDE максимум одна строка.
- Нет норматива → `deviation_pct = NULL`, в UI бейдж «нет норматива» (не 0 и не ошибка).
- `deviation_pct = (unit_cost_total / standard_unit_rate - 1) * 100`; точный Decimal, округление до 0.1 п.п. только на слое представления.
- Исключены: `is_chapter = true`, строки с `unit_cost_total IS NULL`, позиции, чья каталожная строка имеет `kind != 'POSITION'` (HEADER/TRASH/TO_REVIEW не сравниваются с нормативами).

Служебное:

```
import_jobs
  id, contract_id NOT NULL, amendment_no int NULL, filename, file_key,
  file_sha256 text NOT NULL,
  status ('pending|parsing|importing|matching|done|error'), error_text,
  warnings jsonb NOT NULL DEFAULT '[]',
  counters (positions_total, matched_cache, matched_exact, matched_nonposition, to_review),
  created_at, started_at, finished_at
  # Лок: частичный уникальный индекс
  #   UNIQUE (contract_id, COALESCE(amendment_no,-1)) WHERE status NOT IN ('done','error')
  # Блокируется одна ПАРА (contract_id, amendment_no); параллельный импорт РАЗНЫХ
  # допсоглашений одного договора — разрешён.
```

## 5. Пайплайн импорта

`POST /api/v1/estimates/upload` (multipart: файл + `contract_id` + `amendment_no?` + `replace?=false`). Лимиты: размер ≤ 25 МБ (настройка), мягкий таймаут 10 мин (§3).

**Правила повторной загрузки для той же пары (contract_id, amendment_no):**

1. Уже есть `done`-job с **тем же** `file_sha256` → идемпотентно вернуть существующий job, не импортировать.
2. Уже существует estimate, файл **другой**, `replace` не передан → **`409 Conflict`** с понятным сообщением («смета уже загружена; для замены повторите с replace=true»).
3. `replace=true` (право: `admin`) → в доменной транзакции: удалить существующий estimate (CASCADE на lots/proposals/positions/raw_data), импортировать новый. Старые `import_jobs` и их файлы **не удаляются** — это аудит; в новом job зафиксировать warning «заменена смета estimate_id=N от <дата>».

Транзакционная модель — **две разные сессии**:

- **Сессия A (промежуточные статусы и ошибки):** переходы `pending→parsing→importing→matching` и запись `warnings` — отдельные короткие транзакции с немедленным commit (фронт видит прогресс). Статус `error` пишется сессией A **после** rollback домена и потому его переживает.
- **Сессия B (домен + финал):** шаги 3–4 и **финальный переход в `done` вместе с итоговыми счётчиками** — одна атомарная транзакция. Смета и её `done`-статус коммитятся неразделимо: не существует состояния «смета в БД, а job не done» (crash window закрыт). При исключении — полный rollback (домена и финального статуса), затем сессия A пишет `error` + текст.

Шаги:

1. Валидация, сохранение файла (§8), sha256, создание `import_job` (сессия A), ответ `job_id`. **Сохранённый файл удаляется во всех путях, где новый job не создан:** идемпотентный возврат существующего done-job (правило 1), ответ `409` (правило 2), любая ошибка создания job. Файл остаётся только у реально созданного job. Тяжёлая часть — в `BackgroundTasks`.
2. **Парсинг** (`backend/parser/` = перенесённый `excel_parser`). Адаптация под смету ГП: один подрядчик → один proposal; отсутствие baseline-блока не должно ронять `read_contractors.py` / детекцию шапки (главный технический риск — покрыть тестом на реальном файле ДО написания адаптации); `suggested_quantity` парсится штатно.
3. **Импорт** (сессия B, порт логики `ImportFullTender`): [replace: удаление старого estimate] → estimate → raw_data (+parser_version) → lots → proposal → position_items. Реквизиты из шапки сравнить с карточкой договора → расхождения в `warnings` (не блокируют).
4. **Матчинг** (та же сессия B), каскад для каждой позиции кроме `is_chapter`; везде используется одна и та же нормализованная пара `(normalize(job_title), unit_norm)`:
   1. `cache_key` в `matching_cache` → hit: проставить `catalog_position_id`; продлить TTL только для `source='auto'`; счётчик — по `kind` целевой каталожной строки: `POSITION` → `matched_cache`, `HEADER`/`TRASH` → `matched_nonposition`;
   2. SQL-поиск точного совпадения `(normalized_job_title, unit_id)` среди `kind='POSITION'` → проставить + записать кэш (`source='auto'`);
   3. miss: **атомарный get-or-create** `catalog_positions` — `INSERT ... ON CONFLICT (normalized_job_title, COALESCE(unit_id,-1)) DO NOTHING` + повторный SELECT. По `kind` найденной строки: `TO_REVIEW` → привязаться; `POSITION` → это hit ветки 2 (дубль не создавать, записать кэш); `HEADER`/`TRASH` → привязаться (строка уже вручную размечена как не-работа), позиция не попадает ни в очередь Review, ни в VIEW отклонений; учитывать отдельным счётчиком `matched_nonposition`.
   Нормализация — `sanitize_text.normalize_job_title_with_lemmatization` из парсера; `norm_version` — константа, входит в cache_key.
5. Финальный статус `done` + итоговые счётчики — **последняя операция сессии B**, в той же транзакции, что и домен (см. транзакционную модель выше).

**Recovery при рестарте** (корректно при одном worker, §3): на startup все jobs в статусах `pending|parsing|importing|matching` переводятся в `error` («прерван перезапуском, повторите загрузку»). Повторная загрузка идемпотентна/разрешена по правилам выше.

**Ручной матчинг (экран Review):** очередь позиций, привязанных к `kind='TO_REVIEW'`. Действия: «слить с существующей POSITION» (поиск FTS/ILIKE), «утвердить как новую POSITION», «пометить HEADER/TRASH». Решение применяется **ко всем** position_items, ссылающимся на данную TO_REVIEW-строку. При слиянии, в одной транзакции: `UPDATE position_items SET catalog_position_id = <target>` → запись в `matching_cache` c **`source='manual'`, `expires_at = NULL`** (ручные решения не истекают) → **`DELETE` TO_REVIEW-строки** (после переноса ссылок она пуста; удаление освобождает её уникальную нормализованную пару, иначе она перехватывала бы будущие get-or-create). «Утвердить как POSITION» / «пометить HEADER/TRASH» — смена `kind` той же строки, без удаления. Инвариант: **записи `matching_cache` никогда не указывают на строки `kind='TO_REVIEW'`** (кэш пишется только в ветках 2/HEADER/TRASH и при ручных решениях) — это условие безопасности DELETE, закрепить тестом. Векторные подсказки по embedding — фаза 2, в MVP не делать.

## 6. Семантика сквозной матрицы (зафиксировано)

- По каждому договору участвует **только последняя смета** (максимальный `amendment_no`, NULL — минимальный). Предыдущие — история в карточке договора, в матрицу не попадают.
- Вес позиции: `w = COALESCE(suggested_quantity, quantity)` — в сметах ГП заполнено «Предполагаемое количество» (см. §4, ключевой факт). **Сверить на реальных файлах в фазе 0; если там авторитетно другое поле — остановиться и спросить.**
- Работа встречается в смете несколько раз → ячейка = средневзвешенная ставка:
  `SUM(unit_cost_total * w) / SUM(w)` **только по строкам, где `unit_cost_total IS NOT NULL` и `w > 0`**; если таких строк нет — ячейка пустая. Drill-down в позиции по клику.
- Строки = `catalog_positions (kind='POSITION')`, колонки = договоры (группировка по объекту), в ячейке ставка + `deviation_pct` цветом. Фильтры: класс, период, текстовый поиск по названию работы. Фильтр «статья/раздел» — не в MVP (у него нет формального источника: разделы специфичны для каждой сметы, а строки матрицы — каталожные). Закрепление первой колонки. Min/max/разброс — не в MVP.

## 7. Экраны фронтенда

1. **Договоры/Объекты** (на базе Projects) — список, карточка: реквизиты, класс, текущие сметы, **история загрузок и замен** (список import_jobs с датами, статусами и скачиванием исходного XLSX; заменённые сметы доступны только как файл — структурированная история за скоупом MVP), drag-and-drop загрузка, статус job с поллингом, показ `warnings`.
2. **Ручной матчинг** (на базе Review) — очередь TO_REVIEW, действия из §5.
3. **Нормативы** (на базе ReferencePrices/handbook, право `admin`) — классы, ставки, периоды, форма переутверждения с коэффициентом инфляции (предзаполнение: новая ставка = старая × индекс, редактируемо); ошибки EXCLUDE (пересечение периодов) — человекочитаемый 400.
4. **Паспорт объекта** — новая страница, А4, `@media print`, PDF в MVP = Ctrl+P. Состав: реквизиты договора (номер, подписант, дата, сумма, класс), ключевые расценки, отклонения с подсветкой. **Правило «ключевых расценок»:** топ-15 позиций последней сметы по `total_cost_total`; N хранится в БД (таблица настроек, экран Settings из udp-tenders), не в конфиге приложения.
5. **Сквозная матрица** — новая страница на TanStack Table, семантика §6.
6. **Отчёты** (на базе Reports) — Excel через openpyxl: (а) свод расценок по договору с отклонениями; (б) сравнение с нормативами «для банка». Точный макет «для банка» **согласовать с пользователем перед фазой 6** — не выдумывать, остановиться и спросить.

## 8. Хранилище файлов

Локальная директория за абстракцией `Storage` (`save/get/delete`, реализация `LocalStorage`, путь из настроек). Требования: имя на диске = непрозрачный ключ (uuid), оригинальное имя — только в БД; никаких путей от клиента; выдача только через авторизованный `GET /api/v1/import-jobs/{id}/file`, не через статику. Ретенция: файлы jobs со статусом `error` старше 30 дней (настройка) удаляются проверкой при старте приложения — т.е. при первом запуске после истечения срока, не ровно в срок; файлы `done`-jobs хранятся бессрочно (аудит). S3-реализация — потом, без правки вызывающих мест.

## 9. Порядок работ (фазы; коммитить и останавливаться для проверки после каждой)

0. **Входные данные:** получить у пользователя 2–3 реальных XLSX-сметы ГП **до начала работ**. На них же: сверить семантику quantity vs suggested_quantity (§6). **Реальные файлы не коммитить**: каталог `samples/` в `.gitignore`; для тестов подготовить обезличенные fixtures (заменить наименования контрагентов/объектов, сохранить структуру и типы данных).
1. **Инициализация и чистка:** в `gca_mvp` — этот документ как `AGENTS.md`, рядом однострочный `CLAUDE.md` с указателем на `AGENTS.md`, `.gitignore` (включая `samples/`), затем перенос кода `udp-tenders@98fb676` **одним коммитом без чужой git-истории**, с текстом коммита вида `import udp-tenders @ 98fb67667c44 as boilerplate` и упоминанием источника в README. На зафиксированной ревизии udp-tenders файла LICENSE нет, а все три исходных репозитория принадлежат автору проекта; если LICENSE/copyright notices появятся в источниках — перенести их файлами, ссылки в README недостаточно. После переноса: удалить LLM/PDF/MinIO/organizations/УПД-домен, привести названия (пакеты, `application_name` в database.py, заголовки фронта) к проекту, зелёные оставшиеся тесты, запуск пустого приложения одним worker.
2. **Схема:** Alembic — pgvector + btree_gist extensions; перенос таблиц tenders-go (raw SQL через `op.execute()` для COALESCE-уникальных, частичных, EXCLUDE-индексов; проверить корректный downgrade); contracts, rate_classes, rate_standards, import_jobs, estimate_raw_data, `normalized_job_title`; VIEW отклонений; слияние units. SQLAlchemy-модели поверх.
3. **Парсер:** перенос `excel_parser` → `backend/parser/`, чистка импортов, адаптация один-подрядчик-без-baseline, юнит-тесты на fixtures из фазы 0.
4. **Импорт+матчинг:** две сессии, транзакция, каскад, recovery, идемпотентность, replace-флоу, эндпоинты. Интеграционные тесты, включая: повторный импорт того же файла (идемпотентность); другой файл без replace (409); replace=true (замена + аудит); два miss с одинаковой нормализованной парой (один TO_REVIEW, не конфликт); ручное решение переживает истечение auto-TTL; рестарт с зависшим job; смета и `done`-статус коммитятся атомарно (симуляция падения между матчингом и финалом не оставляет сметы без done-job); слияние TO_REVIEW удаляет строку и не оставляет кэш-записей на TO_REVIEW.
5. **CRUD и Review:** договоры, нормативы, ручной матчинг, роли.
6. **Аналитика:** паспорт, матрица, Excel (после согласования макета).

## 10. Definition of Done

- Реальная смета проходит конвейер целиком. Метрика матчинга: после первичного наполнения каталога и ручной обработки очереди Review те же файлы фазы 0 загружаются для **специально созданных контрольных договоров с новыми `contract_id`** (иначе сработает идемпотентность и вернутся старые jobs); по счётчикам этих новых jobs `(matched_cache + matched_exact) / positions_total ≥ 90%`, где `positions_total` = число позиций, допущенных к каскаду матчинга (`is_chapter = false`; `matched_nonposition` в знаменатель входит, в числитель — нет).
- Паспорт печатается на одну страницу А4 со всеми полями §1.2.
- Превышение норматива видно в матрице и паспорте без ручных действий; «нет норматива» отличим от «0%».
- Переутверждение норматива не меняет отклонения старых смет (сравнение по дате сметы).
- Прерванный рестартом импорт не оставляет ни зависшего job, ни половины сметы в БД.
- Ручное решение из Review продолжает матчить ту же позицию спустя >30 дней (тест с подменой времени).
- Загрузка исправленного файла даёт 409 без replace и корректную замену с replace.
- `pytest` и `vitest` зелёные; критический путь покрыт интеграционными тестами.

## 11. Известные грабли

- COALESCE-уникальные, частичные и EXCLUDE-индексы — только raw SQL в миграциях; `alembic downgrade` должен их корректно снимать.
- Парсер опирается на объединённые ячейки (`build_merged_shape_map`); не ломать досрочный выход по merged-ячейке в первой колонке (признак конца позиций / начала summary).
- `normalize_job_title_with_lemmatization` тянет NLP-зависимости (pymorphy3/natasha) — оставить только реально используемые; spaCy-модель не тащить, если не используется. Нормализация должна быть **детерминированной** (одна строка → всегда один результат) — иначе разъедутся кэш, каталог и матчер.
- Инкремент `norm_version` — это не «поменял константу»: обязательная миграция перевыпуска ключей `source='manual'` (§4) и пересчёт `normalized_job_title` в каталоге.
- `UNIQUE NULLS NOT DISTINCT` — PG16-синтаксис; в тестах та же мажорная версия PG, что и в проде.
- Деньги: Decimal end-to-end; в JSON — строки; в тестах сравнивать Decimal с Decimal.
- Один uvicorn worker — не «дефолт, который можно поменять», а условие корректности recovery; при переходе на несколько workers понадобится лизинг jobs (за скоупом MVP).

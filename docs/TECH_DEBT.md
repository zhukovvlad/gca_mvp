# Технический долг

Зафиксированные компромиссы, которые стоит устранить в будущем.

---

## Backend

- [ ] **`GET /api/projects` не возвращает `created_at`**
  Роутер `routers/projects.py:list_projects` отдаёт `{id, name, contract_number, doc_count}`, но
  фронтенд (`Project` тип в `frontend/src/types/project.ts`) объявляет `created_at` обязательным
  и читает его в `ProjectCard.tsx` («Создан {formatDate(project.created_at)}») и `ProjectPage.tsx`
  («создан {formatDate(project.created_at)}»). В живой UI отображается «Создан Invalid Date» / «—».
  Расхождение всплыло при правке фикстуры `sampleProject` для типобезопасных тестов карточки.
  **Решение:** добавить `"created_at": p.created_at.isoformat() if p.created_at else None` в dict
  ответа `list_projects` (и аналогично в `create_project_route` / `update_project_route` для
  консистентности). Альтернатива — определить Pydantic `ProjectOut` с `model_config = ConfigDict(from_attributes=True)`
  и навесить `response_model=ProjectOut` на все три endpoint'а.

- [ ] **`Invoice.vat_rate` допускает NULL в БД**
  Колонка объявлена без `nullable=False` и без `NOT NULL` в миграции. ORM-дефолт `20.0`
  применяется только при создании через ORM-объект, старые/мигрированные строки могут иметь `NULL`.
  В SQL-выражениях добавлен защитный `COALESCE(Invoice.vat_rate, 20.0)`, но правильнее закрыть
  проблему на уровне схемы.
  **Решение:** добавить `nullable=False` в `models.py` и сгенерировать миграцию
  `ALTER TABLE invoices ALTER COLUMN vat_rate SET NOT NULL`.

- [ ] **`GET /dashboard/calculations` без `project_id`: N запросов к БД**
  При вызове без `project_id` (глобальный Dashboard) функция выполняет `compute_calculations()`
  по одному разу на каждый проект. При N проектах = N × ~4 SQL-запросов на месяц × M месяцев.
  Для MVP с единицами проектов приемлемо.
  **Решение:** объединить в один SQL с `GROUP BY project_id, material_class_id, month` и
  переиспользовать delivery-аллокацию через window-функцию.

- [ ] **SQLAlchemy: синхронный движок вместо async**
  `database.py` использует `create_engine` + синхронный `Session`. FastAPI запускает синхронные
  зависимости в threadpool, что добавляет накладные расходы на переключение потоков.
  **Решение:** перейти на `asyncpg` DSN (`postgresql+asyncpg://`), `AsyncEngine`, `AsyncSession`,
  заменить `db.query()` на `await db.execute(select(...))` и все endpoint-функции сделать `async def`.

- [x] **`PDF_ENGINE`: код-дефолт расходится с документацией и рабочим значением**
  `config.py:43` — дефолт `mistral-ocr`, но `docs/agent/pdf-parsing.md:245` называет дефолтом
  `native`, и фактический `.env` использует `native` (mistral-ocr нестабилен на СФ с 60+ строками).
  Кто запустит без `.env`-оверрайда — получит худший движок. Комментарий у `AI_MAX_TOKENS`
  (`config.py:41`, «prompt от mistral-ocr съедает ~24K») — из той же устаревшей эпохи: при native
  промпт ~10K. Обнаружено при написании спеки LLM-провайдера (2026-07-23).
  **Решение:** сменить дефолт на `"native"` (+ обновить комментарий AI_MAX_TOKENS); учесть
  алиас-цепочку `OPENROUTER_PDF_ENGINE → PDF_ENGINE → дефолт` из спеки
  `2026-07-23-llm-provider-toggle-design.md` §1 — правку удобно совместить с её реализацией.
  **Закрыто 2026-07-27** (спека `2026-07-27-deploy-env-contract-design.md`, AC-9): дефолт
  сменён на `native` одним неделимым изменением вместе со снятием условия
  `!= "mistral-ocr"`, комментарий у `AI_MAX_TOKENS` обновлён, у обоих legacy-алиасов
  появилось предупреждение.

- [ ] **`AI_MODEL`: та же глушилка предупреждения, что была у `PDF_ENGINE`**
  `resolved_openrouter_model` (`config.py`) содержит условие `legacy != "anthropic/claude-sonnet-5"`
  перед выдачей deprecated-предупреждения — тот же паттерн, что чинился у `PDF_ENGINE` и
  `AI_MAX_TOKENS` в `9d900a5` и его follow-up: условие `legacy != <дефолт>` навсегда глушит
  warning ровно для того значения, которое совпадает с код-дефолтом, то есть для любого `.env`,
  который пином закрепил текущую модель по умолчанию. Лекарство то же самое — сделать `AI_MODEL`
  `Optional` (`str | None = None`), чтобы «не задано» отличалось от «задано значением-дефолтом»,
  и убрать сравнение со значением из условия warning.
  **Осознанно не тронуто в рамках задачи 4** (ревью 2026-07-29): `AI_MODEL`/`OPENROUTER_MODEL`
  — единственная пара с независимым путём к смерти (`unset_key` на `PUT /api/settings`, поле
  исчезает при первом сохранении модели из UI) и читается роутером настроек, который был
  предметом AC-0 — не лучшее место для попутного изменения. Когда до него дойдёт очередь: см.
  `resolved_openrouter_max_tokens`/`resolved_openrouter_pdf_engine` в `config.py` как готовый
  рецепт.

- [ ] **Parser: chunking для очень длинных СФ**
  `_reconcile_totals` теперь *детектирует* потерянные строки, но восстановление для СФ с 100+
  позициями требует постраничного разбора с последующей склейкой. Не реализовано. Также: prompt
  от mistral-ocr занимает ~24K токенов на 8-страничном бланке (повторяющиеся шапки/подвалы
  каждой страницы) — сжатие prompt-нагрузки оставило бы больше места для completion.

- [x] **Parser: `usage: null` в ответе OpenRouter крашил разбор (закрыто)**
  **Закрыто Task 3 плана 2026-07-23:** транспорт/envelope переехали в `OpenRouterProvider`
  (`llm_openrouter.py`), который читает usage через `data.get("usage") or {}` — явный JSON
  `null` больше не роняет разбор (раньше `usage.get("completion_tokens")` бросал `AttributeError`,
  документ уходил в ошибку, хотя платный вызов уже биллился). Теперь `usage: null` → успех с
  `cost=Decimal(0)`, `completion_tokens=None`. Зафиксировано тестом `test_usage_null_returns_success`.

- [ ] **Parser: reparse удаляет данные до валидации**
  `routers/invoices.reparse_document` удаляет существующие Invoice-строки *до* запуска нового
  разбора. Если новый разбор отклонён guard'ом completeness, документ остаётся с 0 инвойсов
  (старые корректные данные уже удалены). Правильная схема: разобрать → провалидировать → затем
  удалить старое и записать новое (parse-then-swap).

- [ ] **`GET /api/invoices/documents` — ~8 с стабильно на ~20 документах (N+1)**
  `routers/invoices.py:154-174` (`list_documents`) на каждый документ обходит его СФ/позиции:
  `len(doc.invoices)`, `_doc_has_issues(doc)` и `_avg_confidence(doc)` — без eager-loading это
  N дополнительных SQL-запросов на N документов. После Ступени 1 async processing (см.
  `docs/devlog/2026-07-19-async-processing-stage-1.md`) этот эндпоинт ещё и поллится с фронта
  каждые 2500 мс, пока в списке есть нетерминальный документ (`processingRefetchInterval`) —
  медлительность стала заметнее и усилила связанный баг детектора терминальных переходов
  (см. тот же devlog, фикс `d154115`: вечный цикл инвалидаций был спровоцирован именно
  ~8-секундной list-квери).
  **Решение:** агрегирующий SQL (`GROUP BY document_id` для count/confidence/issues) или
  `selectinload(Document.invoices).selectinload(Invoice.items)` + вычисление метрик в Python
  без дополнительных запросов на документ.

- [x] **Дрейф ORM/БД: индексы созданы raw SQL, но не объявлены в моделях**
  `alembic revision --autogenerate` устойчиво предлагает лишние диффы, не связанные с текущими
  изменениями: `drop_index('ix_invoice_items_invoice_id_item_type')`, `drop_index('ix_invoices_supplier_id')`,
  `drop_index('ix_suppliers_name_trgm')` (GIN trigram), `drop_index('uq_suppliers_name_no_inn')`
  (partial unique) и `create_index('ix_suppliers_id')`. Причина: эти индексы созданы через
  `op.create_index`/raw SQL в старых миграциях (`2026_05_15_1200-b3c7e9f12a45_add_suppliers_table.py`,
  `2026_05_21_1200-add_calc_role_to_material_classes.py`), но никогда не объявлены в SQLAlchemy-моделях
  (`Supplier`, `Invoice`, `InvoiceItem`). Дрейф предшествует ветке `feat/parse-cost-tracking`; при
  автогенерации миграции для колонок parse-cost эти диффы всплыли и были исключены вручную
  (миграция `1859523e53de` написана как ровно две column-операции). Будет всплывать при каждом
  будущем `--autogenerate`, пока не устранено.
  **Решение:** для четырёх `drop_index`-диффов — объявить недостающие индексы в моделях
  (`Supplier.name` trigram GIN, partial unique `uq_suppliers_name_no_inn`,
  `Invoice.supplier_id`, `InvoiceItem(invoice_id, item_type)`) через `Index(...)` /
  `index=True` в `__table_args__`. Именно объявление в метаданных убирает диффы (как
  только метаданные совпадут с БД, автоген перестанет предлагать drop) — отдельная
  no-op миграция для этого не нужна и не помогает. Для `create_index('ix_suppliers_id')`
  ситуация обратная: `Supplier.id` имел `index=True` (было актуально на момент фиксации
  бага — `backend/models.py:291`), но индекса нет в БД — избыточный `index=True` с PK убран
  (первичный ключ и так проиндексирован). Отдельной сфокусированной задачей, не на зелёной
  feature-ветке.
  **Решено (PR-1):** 4 индекса (`Supplier.name` trigram GIN, `uq_suppliers_name_no_inn`,
  `Invoice.supplier_id`, `InvoiceItem(invoice_id, item_type)`) объявлены как метаданные;
  избыточный `index=True` с `Supplier.id` убран; 2 новых индекса (`ix_documents_project_id`,
  `ix_invoices_document_id_date`) добавлены реальной миграцией и объявлены в моделях —
  `just db-test-check` даёт чистый `No new upgrade operations detected`.

- [ ] **Шаблон alembic script.py.mako даёт ruff I001 на каждой новой ревизии**
  The default import order emitted by `backend/alembic/script.py.mako` trips ruff `I001` on every `just db-revision`; `pyproject.toml` per-file-ignores for `alembic/versions/*` cover E402/F401/UP007/UP035 but NOT I001, so each new revision needs a manual `ruff check --fix`. Two candidate fixes: (a) preferred — reorder imports in `backend/alembic/script.py.mako` (it is a template, not a historical migration → editing allowed); (b) add `I001` to the `alembic/versions/*` per-file ignore in `pyproject.toml`. Обнаружено в PR-1 (миграция add_hot_path_indexes).

---

## Frontend

- [ ] **`ui-domain/Button` дублирует shadcn `ui/button`**
  Проект содержит два Button-компонента: shadcn `ui/button.tsx` (Base UI primitive + CVA) и
  кастомный `ui-domain/Button.tsx` (hand-rolled forwardRef). Весь приложенческий код использует
  второй. Причина: `ui-domain/Button` добавляет пропсы `leftIcon`, `rightIcon`, `loading` и
  использует проектные CSS-токены (`--color-action`, `--color-surface-hover` и др.), которых нет
  в shadcn-заготовке. `ui/button.tsx` используется только внутри shadcn-компонентов (pagination,
  alert-dialog, dialog, input-group) и содержит баг: `secondary` вариант ссылается на
  `var(--secondary)` вместо `var(--color-secondary)`.
  **Решение:** перенести логику `ui-domain/Button` (иконки, loading, проектные токены) в `ui/button.tsx`,
  добавить вариант `primary` / `danger`, обновить CSS-переменные — и удалить `ui-domain/Button.tsx`.
  Все ~11 import-точек переключить на `@/components/ui/button`.

- [ ] **`window.confirm` остаётся в `MaterialClasses.tsx`, `Materials.tsx`, `ReferencePrices.tsx`**
  Проектный паттерн подтверждающих диалогов — shadcn `AlertDialog` (см. пункт про `ui-domain/Button`
  выше и коммит `9a48aba`, которым `Review.tsx` был переведён на `AlertDialog` в рамках Ступени 1
  async processing — `docs/devlog/2026-07-19-async-processing-stage-1.md`). Эти три страницы всё
  ещё используют нативный `window.confirm` для подтверждения удаления.
  **Решение:** заменить `window.confirm` на `AlertDialog` во всех трёх файлах по образцу
  `Review.tsx` (коммит `9a48aba`).

- [ ] **`lib/api.ts`: глобальный onError показывает сырое axios-сообщение вместо серверного `detail`**
  Обработчик ошибок мутаций (QueryCache/MutationCache в `App.tsx` → `error.message` axios) не
  извлекает `detail` из тела FastAPI-ответа: пользователь видит англоязычное
  «Request failed with status code 409» вместо серверного «Документ обрабатывается — дождитесь
  завершения». После Ступени 1 async processing 409-тосты стали заметнее (guard `_reject_if_busy`
  покрывает pending|processing; UI-дизейблы зеркалят его, но гонка «кликнул до рефетча» остаётся
  легальным путём к 409). Выявлено финальным ревью S1 (`docs/devlog/2026-07-19-async-processing-stage-1.md`).
  **Решение:** interceptor в `lib/api.ts` (или хелпер `getApiErrorMessage(error)`), который берёт
  `error.response?.data?.detail` с фолбэком на `error.message`, и использование его в глобальном
  onError и точечных обработчиках.

- [ ] **Review.tsx: нет оптимистичного обновления при сохранении**
  После успешного `update.mutate` сервер возвращает обновлённый документ через `docQ` (invalidate),
  но до перезагрузки страница ненадолго показывает устаревшие данные.
  `setOverrides(null)` сбрасывает черновик сразу, что может вызвать кратковременный «прыжок» UI.

- [ ] **Review.tsx: всегда показывает и верифицирует первую СФ документа (`invoices[0]`)**
  Маршрут `/documents/:id` не содержит id СФ. `serverInv = doc.invoices[0]` — если документ содержит
  несколько СФ, переход с дашборда на любую из них откроет первую, а `verify`/`unverify` будут
  мутировать не ту СФ.
  **Решение:** изменить маршрут на `/documents/:docId/invoices/:invoiceId` (или query-параметр),
  выбирать `serverInv` по id из URL.

- [ ] **PriceChart: нет пагинации/виртуализации при большом числе точек**
  При большом диапазоне дат и десятках классов материалов recharts рендерит все точки сразу.

- [ ] **InvoiceTable: клиентская пагинация — нужна серверная при ~1000+ СФ на проекте**
  Таб «Счета» в `ProjectPage` загружает все СФ проекта за один запрос (`GET /dashboard/invoices?project_id=`)
  и пагинирует их на клиенте. Реально на одном проекте может быть 1000+ инвойсов по одному материалу,
  что делает первоначальную загрузку тяжёлой и перегружает TanStack Query кеш.
  **Решение:** добавить `?page=&page_size=&sort_by=&sort_dir=` к эндпоинту + вернуть
  `{ items: [...], total: N }`, перевести `useDashboardInvoices` на серверную пагинацию,
  убрать клиентскую логику из `InvoiceTable`. Фильтр по месяцу (сейчас клиентский) потребует
  отдельного `?month=YYYY-MM` param на бекенде.
  **Триггер:** проект с ≥300 СФ или жалобы на медленное открытие таба «Счета».

- [ ] **InvoiceTable: tooltip со статусом СФ недоступен с клавиатуры и тач-устройств**
  Уверенность ИИ и дата верификации показываются только через нативный `title` на `<span tabIndex={0}>`.
  Нативные title-тултипы браузеры показывают только при hover, не при focus — поэтому полной доступности нет.
  **Решение:** заменить на focusable-компонент с `aria-describedby` или отдельный tooltip-компонент,
  либо вынести значения в видимый текст.

- [ ] **Дублирование blob-download паттерна в трёх местах**
  `ProjectPage.tsx` (xlsx-экспорт), `MonthlyTab.tsx` (CSV) и `Reports.tsx` используют одинаковую
  последовательность: `createObjectURL` → `appendChild(a)` → `click()` → `removeChild(a)` → `revokeObjectURL`.
  Реализации немного расходятся (синхронный vs async revoke, санитизация имени файла).
  **Решение:** вынести в хелпер `src/lib/downloadBlob.ts(blob, filename)` и обновить все три вызывающих.

- [ ] **Suppliers / SupplierPage: строки таблиц кликабельны только мышью**
  `TableRow onClick={() => navigate(...)}` в `Suppliers.tsx` и `SupplierPage.tsx` не работает с клавиатурой.
  Это cross-cutting concern: тот же паттерн используется в `ProjectPage`, `InvoiceTable` и др.
  **Решение:** добавить `tabIndex={0}` + `onKeyDown` (Enter/Space) или переосмыслить паттерн в пользу
  `<a>` / `<Link>` внутри ячейки, что даёт доступность бесплатно.

- [ ] **`get_supplier_project_stats`: `volume_m3` смешивает единицы измерения**
  Колонка суммирует `InvoiceItem.quantity` для всех `item_type == "material"` позиций,
  включая арматуру (тонны/кг) и другие не-объёмные материалы. Для поставщика смешанного
  профиля число в колонке «Объём, м³» вводит в заблуждение.
  **Решение:** ограничить сумму позициями с `MaterialClass.material_type == "concrete"`,
  либо динамически скрывать колонку если у поставщика нет бетонных категорий,
  либо переименовать в «Объём / Кол-во» с указанием единиц из материала.

- [ ] **`compute_calculations`: `invoice_ids_month` через Python-список в `IN (...)`**
  При большом числе счетов за месяц функция материализует все ID в Python-список и передаёт
  их в каждый из четырёх последующих SQL-запросов как `IN (id1, id2, ...)`. Запросы
  пересылают весь список по wire. Для типичного проекта (десятки счетов в месяц) некритично,
  но при крупном объёме данных стоит заменить на subquery/CTE.

- [ ] **Нет кеширования расчётов — потенциальная проблема при ~1000+ СФ**
  Все аналитические эндпоинты (`/dashboard/calculations`, `/api/deviation-chart`, экспорт Excel)
  вычисляют агрегаты на лету при каждом запросе. При ~1000 СФ × 10 позиций = 10 000 строк
  PostgreSQL справляется, но узкие места:
  1. **Dashboard без `project_id`**: N+1 (уже зафиксирован выше) × объём каждого проекта — нагрузка растёт мультипликативно.
  2. **Excel-экспорт**: `compute_export_rows()` материализует все строки за период в Python-памяти для openpyxl. При 500+ СФ за квартал — граница комфорта.
  3. **Deviation chart**: запускается при каждом открытии карточки проекта; при большом диапазоне дат — тяжёлый запрос.
  **Решение (при достижении порога):** добавить Redis-кеш на `compute_calculations()` с инвалидацией
  по событию «загружена новая СФ / изменена базовая цена» (event-based, не TTL). Materialized view
  в Postgres — альтернатива без внешней зависимости, но требует явного `REFRESH`.
  **Триггер для реализации:** p95 latency на `/dashboard/calculations` > 2 сек или Excel-экспорт > 10 сек.

- [ ] **N+1 в `get_projects` / `get_documents` / `get_document`**
  `crud.projects.get_projects` не делает eager-loading `documents`, но роутер обращается к
  `p.documents` для подсчёта `doc_count` — один SELECT на проект. Аналогично `get_documents` /
  `get_document` не загружают `invoices`/`items`, но роутеры их обходят.
  **Решение:** добавить `selectinload(Project.documents)` в `get_projects` (или считать `doc_count`
  агрегатной колонкой через `func.count`); добавить `selectinload(Document.invoices)` и
  `selectinload(Invoice.items)` в `get_documents`/`get_document`.

- [ ] **N+1 в `get_supplier_project_stats`**
  `crud.get_supplier_project_stats` вызывает `_compute_supplier_project_deviation` в Python-цикле.
  Для поставщика на N объектах — N × ~5 SQL-запросов. При ≥20 объектах становится заметным.
  **Решение:** перенести логику deviation в один batched-запрос с GROUP BY supplier_id, project_id,
  переиспользовав агрегаты из основного SELECT и JOIN reference_prices.

- [ ] **Excel-экспорт: только интеграционные тесты, нет unit-тестов для генерации workbook**
  `routers/export.py` покрыт интеграционными тестами (`tests/integration/test_export.py`),
  которые проверяют и HTTP-слой, и структуру workbook. Чистую логику генерации Excel (формулы,
  стили, заголовки) можно вынести в отдельную функцию без зависимости от БД и покрыть unit-тестами,
  что ускорит CI и упростит отладку вёрстки файла.
  **Решение:** извлечь `_build_workbook(rows, project, period) -> openpyxl.Workbook` в отдельную
  функцию, написать unit-тесты на неё без TEST_DATABASE_URL.

- [ ] **Pydantic response_model для роутера `/api/suppliers`**
  Эндпоинты `GET /suppliers`, `GET /{id}`, `GET /{id}/projects`, `GET /{id}/invoices-list` возвращают
  raw dict/list без `response_model=`, что нарушает соглашение кодовой базы и не генерирует OpenAPI-схему.
  **Решение:** определить Pydantic-схемы в `routers/suppliers.py` и добавить `response_model=` к декораторам.

- [ ] **Backend: TOCTOU-гонки на guard-проверках verified**
  Проверки `invoice.verified` перед `UPDATE`, `DELETE` и `reparse` не атомарны — параллельный запрос
  может подтвердить СФ между проверкой и мутацией. Требует `SELECT FOR UPDATE` или условного
  `UPDATE ... WHERE verified = false` в четырёх эндпоинтах. Нецелесообразно для однопользовательского
  инструмента, но стоит устранить при масштабировании.
  (Примечание: аналогичная гонка в `crud.admin.set_user_role_and_active` уже закрыта через
  `SELECT ... FOR UPDATE` на строках superadmin'ов — см. `_count_other_active_superadmins_locked`.)

- [ ] **MSW-хендлеры некоторых admin-эндпоинтов всё ещё статичны**
  Хендлеры `/api/admin/*` в `frontend/src/test/handlers.ts` частично возвращают фикстуры.
  Например, `GET /api/admin/organizations` отдаёт фиксированный список.
  Из-за этого happy-path тесты могут пройти даже при неверной сериализации query/тела.
  Тесты, которым важна проверка контракта (поиск, пагинация, редактирование), уже переопределяют хендлер
  через `server.use` со спаем — это корректный паттерн.
  **Решение:** в дефолтных хендлерах читать `req.url.searchParams` / `await req.json()` и отражать их
  в ответе (фильтрация/echo полей).

- [ ] **Frontend: остаточные хардкоды «м³» в дашбордах**
  Бэкенд отдаёт `unit_symbol`/`volume_unit` по строкам расчётов. Ветка направлений
  (`feat/material-directions-frontend`) вычистила KPI режима направления (`ProjectPage`) и
  вкладку «По месяцам» (`MonthlyTab` берёт `volume_unit` из monthly-ответа). Остаются:
  `DeviationChart.tsx:460` (тултип «Посчитано по N м³ из M м³»), `ProjectPage.tsx:753`
  (legacy-KPI «Объём м³» на `total_qty` — уйдёт вместе с deprecated-полем, см. секцию
  «Направления материалов» ниже), `SupplierPage.tsx:304` (страница поставщика — там же).
  **Решение:** заменить литерал «м³» в тултипе `DeviationChart` на `unit_symbol` строки расчёта.

---

## Auth

- [ ] **Нет ограничения частоты запросов на `POST /api/auth/login`**
  Эндпоинт не защищён rate-limiting'ом: атака перебора паролей ничем не ограничена кроме сетевого прокси.
  **Решение:** добавить SlowAPI / custom middleware с лимитом по IP (например, 10 req/min) и логировать
  превышение. Поле для подозрительных попыток уже логируется на уровне `auth.py`.

- [ ] **Изоляция данных по организации нереализована на уровне запросов**
  Все бизнес-роутеры требуют аутентификации через `get_current_user`, но не фильтруют данные по `org_id`.
  Суперпользователь видит все объекты, обычный пользователь в данный момент тоже. Замысел `ProjectAccess`
  в `auth.py` + `ProjectOrganization` в схеме позволяет ввести изоляцию, но роутеры не используют
  `get_project_access` — только `get_current_user`.
  **Решение:** для каждого ресурс-роутера добавить фильтр `WHERE project.id IN (org's projects)` или
  использовать `get_project_access` как зависимость для эндпоинтов, принимающих `project_id`.

- [ ] **UI обещает изоляцию, которой нет**
  Форма создания организации (`frontend/src/pages/admin/AdminOrgCreate.tsx:16-19`) подписывает роли
  как «Заказчик — видит все данные проекта» / «Подрядчик — видит только свои загрузки». Пользователь
  читает это как гарантию доступа, но `kind` организации и `project_role` в `project_organizations`
  сейчас только записываются и на выборки не влияют (см. пункт об изоляции выше) — подрядчик видит
  всё. Расхождение всплыло при разборе флоу создания организации суперюзером.
  **Решение:** закрыть пункт об изоляции выше — тогда подписи станут правдой. До тех пор
  переформулировать хинты в терминах намерения, а не факта (например, «роль на проекте: заказчик /
  подрядчик» без обещаний видимости), чтобы текст не читался как действующее ограничение.

- [ ] **`POST /api/projects` создаёт проект вне организации**
  `routers/projects.py:53-56` не заполняет `customer_org_id` (в схеме `nullable=True`,
  `models.py:154`) и не создаёт строку в `project_organizations`. Проект остаётся ничьим; при этом
  `list_projects` не фильтрует по org, поэтому «осиротевший» проект видят все и симптом не заметен.
  Привязать проект к организации умеет только суперюзер через `POST /admin/organizations/{org_id}/projects`
  (`link_project`), то есть штатный флоу — «создал → суперюзер привязал». После включения изоляции
  такой проект станет невидимым вообще для всех, кроме суперюзера.
  **Решение:** в `create_project` проставлять `customer_org_id` = org вызывающего пользователя и
  заводить `ProjectOrganization` с `project_role` = `organization.kind`; для суперюзера (у него
  `org_id IS NULL`) — либо требовать явный `org_id` в теле запроса, либо запретить создание проекта
  без организации. Делать одной задачей с изоляцией, иначе появится второй флоу привязки.

- [ ] **`OrgRole` / `ProjectRole` хранятся как VARCHAR + CHECK вместо нативных PG enum'ов**
  `native_enum=False` выбран во избежание сложностей с Alembic при добавлении значений. Нативный enum
  более строг на уровне БД. При добавлении новых ролей потребуется только миграция CHECK-constraint.
  **Решение:** при стабилизации ролевой модели можно перейти на `CREATE TYPE ... AS ENUM` — отдельная
  миграция без потери данных.

- [ ] **Нет сброса пароля и верификации e-mail**
  `User.email` существует, но нет flow «забыли пароль» и нет проверки владения адресом при регистрации.
  Аккаунты создаются только через CLI или API суперпользователя, что приемлемо для закрытого B2B-продукта,
  но не масштабируется при самостоятельной регистрации.
  **Решение:** добавить `POST /api/auth/forgot-password` + `POST /api/auth/reset-password` с one-time
  token (hashed, stored in DB, TTL 1 час).

- [ ] **Письмо-приглашение при создании пользователя (обсуждается)**
  При создании пользователя через админ-консоль (`POST /api/admin/organizations/{id}/users`,
  `POST /api/orgs/users`) и при сбросе пароля (`POST /api/admin/users/{id}/reset-password`)
  суперюзер/админ сейчас вручную копирует сгенерированный пароль и передаёт его «безопасным способом».
  Хочется автоматически слать новому пользователю письмо-приглашение.
  **Контекст:** почтовой инфраструктуры в проекте нет вообще — ни SMTP-настроек в `config.py`,
  ни библиотеки отправки, ни env-ключей. `boto3` есть в зависимостях (для MinIO/S3), теоретически
  пригоден для AWS SES.
  **Развилки (решить перед реализацией):**
  1. *Содержание письма.* Слать логин+пароль в открытом виде — простой, но небезопасный паттерн
     (пароль оседает в почтовых ящиках/логах/индексах). **Предпочтительно** — ссылка-приглашение
     с one-time токеном (hashed, TTL), по которой пользователь сам задаёт пароль; пароль никогда
     не летит по почте. Это пересекается с задачей «сброс пароля и верификация e-mail» выше —
     стоит делать общий механизм one-time токенов (новая таблица `password_tokens` или подобная)
     и публичную страницу «задать пароль».
  2. *Транспорт.* SMTP через stdlib `smtplib` (без новых зависимостей, работает с любым провайдером:
     Yandex/Mailgun/свой сервер) против AWS SES через `boto3` (нужен настроенный SES + верифицированный
     домен).
  3. *Доставляемость и фоновость.* Отправку нельзя делать синхронно в request-хендлере (таймауты,
     ретраи) — нужен фоновый воркер/очередь или хотя бы `BackgroundTasks` с обработкой сбоев.
  **Триггер:** переход от ручного онбординга к самостоятельному приглашению пользователей.

- [ ] **Нет ротации `SECRET_KEY` без инвалидации всех сессий**
  При компрометации `SECRET_KEY` все access-токены надо считать недействительными и перегенерировать.
  Нет механизма версионирования ключей (kid) в JWT-заголовке.
  **Решение:** добавить `kid` в JWT header, поддерживать словарь ключей — позволит плавно ротировать
  SECRET_KEY без мгновенного логаута всех пользователей.

- [ ] **`admin.py` и `orgs.py` роутеры без `response_model=`**
  Эндпоинты возвращают raw dict без Pydantic response_model — нет OpenAPI-схемы, нет автоматической
  сериализации/валидации ответов. Несоответствие принятому стилю других роутеров.
  **Решение:** определить Pydantic-схемы (`OrgOut`, `UserOut`) в соответствующих файлах роутеров и
  добавить `response_model=` к декораторам.

---

## Инфраструктура / общее

- [ ] **Async processing Ступень 2 — отложена до открытия YAGNI-триггеров (решение 2026-07-20)**
  Ступени 0 и 1 в main (PR #36, #37): 202-контракт, polling, дедуп по file_hash, startup-sweep.
  Ступень 2 (очередь — кандидат procrastinate; `processing_run_id` ownership-token; ретраи Transient;
  stalled-детектор по `processing_started_at`; снятие no-overlap-инварианта — `workers>1`/rolling)
  сознательно НЕ реализуется, пока не сработал ни один триггер.
  **Триггеры:** потребность в `workers>1` или rolling-деплое; очередь под реальной нагрузкой
  (прод пока не развёрнут — следующая веха трека именно прод-деплой, он же даст данные для S2).
  **Порядок при старте:** brainstorming → обязательный спайк S2-0 (Neon + долгоживущие соединения:
  scale-to-zero, обрывы, ср. `pool_recycle=300` — от этого зависит судьба advisory lock) → спека →
  ревью-раунды → план → реализация. Повестка и ADR — `docs/superpowers/specs/2026-07-16-async-processing-design.md`
  (базовая), `2026-07-19-async-processing-stage-1-design.md` §3/§8,
  `docs/devlog/2026-07-19-async-processing-stage-1.md`.
  **Напоминание:** до S2 деплой строго stop-then-start (startup-sweep корректен только без overlap;
  комментарий-инвариант в justfile у `dev-backend`).

- [ ] **auto_calculate не идемпотентен при частичном сбое**
  Если транзакция прерывается на середине цикла, часть месяцев будет рассчитана,
  часть — нет. Нет механизма retry или rollback-маркера.

- [ ] **Нет ограничения размера загружаемого PDF**
  `POST /api/invoices/upload` принимает файл без проверки max-size на уровне FastAPI.
  Сейчас защиту обеспечивает только Nginx/прокси (если настроен).

---

## Units-refactoring (Spec §2 backlog + долг реализации)

### Spec §2 — отложено на следующий этап

- [ ] **Кросс-размерностная конвертация через плотность** (пог.м→т для арматуры)
  Перевод между `mass` и `length` требует коэффициента плотности, специфичного для марки материала. Сейчас такие строки получают `dimension_mismatch=True` и выпадают из расчёта.
  **Решение:** добавить таблицу `density_factors(material_class_id, kg_per_unit)` и обрабатывать cross-dimension conversion в `normalize_item`.

- [ ] **Self-learning aliases** — автоматически добавлять `unit_aliases` из новых документов
  Сейчас неизвестная единица даёт `normalized_unit_id=NULL` и флаг «проблема». Новые алиасы добавляются только вручную (через миграцию или API).
  **Решение:** после парсинга предлагать пользователю сопоставить неизвестную строку с существующей единицей и сохранять в `unit_aliases`.

- [ ] **Lazy reprocess endpoint** — перенормализовать исторические инвойсы без реразбора PDF
  Добавление нового алиаса не ретроактивно обновляет уже сохранённые `InvoiceItem`.
  **Решение:** `POST /api/invoices/renormalize?project_id=` — перезапустить `normalize_item` для всех позиций с `normalized_unit_id IS NULL`, используя обновлённый alias map.

### Чистки после завершения frontend-плана

- [ ] **Удалить legacy `unit` OUTPUT key** из `_serialize_document` и dashboard serializer, а также `InvoiceItemEdit.unit` INPUT alias (`AliasChoices`) в схемах Pydantic — после того как фронтенд перейдёт на `raw_unit` и ни один клиент не читает/пишет `unit`.

- [ ] **Backend: удалить legacy-алиас `unit` в сериализаторах роутеров**
  `backend/routers/invoices.py:106` и `backend/routers/dashboard.py:127` дублируют `raw_unit` как `"unit"` с комментарием «drop after frontend plan ships». Ветка `feat/units-refactoring-frontend` отгружена — фронтенд больше нигде не читает `unit`, алиас можно удалять.
  **Решение:** после мержа `feat/units-refactoring-frontend` удалить строки `"unit": item.raw_unit` из обоих роутеров.

### Долг, выявленный при code-review реализации

- [ ] **VAT-amount SQL expression дублируется ~6×** в `crud/calculations.py` (compute_calculations base/delivery/additive + compute_export_rows) и `crud/suppliers.py` — паттерн `coalesce(vat_amount, amount*coalesce(vat_rate, 20.0)/100)`. Расхождения при правке неизбежны.
  **Решение:** вынести в shared `_sql_vat_amount()` — SQLAlchemy-выражение без аргументов, переиспользуемое во всех трёх модулях.

- [ ] **`compute_export_rows`: ключи результирующего dict названы `*_per_m3`** (`mat_per_m3`, `delivery_per_m3` и т.д.), хотя расчёт теперь размерностно-агностичен (может быть per-ton, per-piece).
  **Решение:** переименовать в `*_per_unit` одновременно в `crud/calculations.py` (producer) и `routers/export.py` (consumer) — косметика, но вводит в заблуждение при ревью.

- [ ] **`func.max(InvoiceItem.raw_unit)` в `compute_export_rows`** — произвольный выбор, когда группа (invoice, class) содержит несколько разных `raw_unit`. Колонка «Ед. изм. по документу» может показывать не ту единицу для такой строки.
  **Решение:** рассмотреть distinct-aware логику (напр. `string_agg(DISTINCT raw_unit, '/')`) или явное предупреждение при неоднородной группе.

- [ ] **Supplier deviation не имеет dimension guard** — `crud/suppliers.py::_compute_supplier_project_deviation` не читает `contrib["dimensions"]` и может агрегировать отклонение по смешанным размерностям (например, м³ + т в одном классе). На странице проекта такие строки получили бы `dimension_mismatch=True`, на карточке поставщика — нет. Предшествует рефакторингу (старый код суммировал сырое `quantity`), не введён рефакторингом.
  **Решение:** добавить тот же intra-class dimension guard в `_compute_supplier_project_deviation` + написать `test_supplier_deviation_dimension_mismatch`.

- [ ] **`compute_export_rows`: двойное начисление доставки/присадок при intra-class mix размерностей.** `base_rows` группируется по `(invoice_id, material_class_id, symbol, dimension)`, поэтому класс, чьи позиции в одной СФ нормализованы в разные базовые единицы, даёт несколько строк; цикл применяет `share_by_inv_class[(inv,cid)]` (долю на весь класс) к КАЖДОЙ строке → доставка/присадки начисляются N-кратно. Срабатывает только на flagged-bad данных (смешанная размерность внутри класса), но в выгруженном финансовом отчёте числа будут завышены.
  **Решение:** консолидировать `base_rows` в одну строку на `(invoice_id, material_class_id)` перед эмиссией (для нормальных данных вывод идентичен — там и так одна строка на класс+СФ).

- [ ] **`_ref_price` (в `compute_export_rows` и `compute_calculations`) не проверяет размерность эталона.** Возвращает цену только по дате; не пропускает `ReferencePrice`, чья единица/размерность не совпадает с фактической базовой размерностью класса. Для классов типа `other` (где `default_unit` = NULL, любая базовая единица разрешена на create) эталон может оказаться в иной размерности, чем нормализованные позиции, и тогда отклонение в экспорте считается по несовместимому эталону. `compute_calculations` имеет такой guard (зануляет deviation), `compute_export_rows` — нет.
  **Решение:** в обоих путях возвращать `rp.price` только при совпадении даты И размерности (сравнить размерность базовой единицы класса с размерностью единицы эталона), единообразно с dimension guard в `compute_calculations`.

> Примечание: четыре пункта выше (supplier dimension guard, export double-allocation, `_ref_price` dimension guard, `func.max(raw_unit)`) — это одна тема «сделать пути export/supplier размерностно-aware, как `compute_calculations`». Все срабатывают только на flagged-bad данных со смешанной/несовпадающей размерностью. Делать их лучше одной сфокусированной задачей с тестами, а не по кускам на зелёной ветке.

---

## Направления материалов (спека §13.5)

- [ ] **`DashboardSummary.total_qty` — deprecated, удалить после стабилизации направлений**
  Сырое `SUM(quantity)` по всем material-позициям объекта: при миксе единиц (м³ + т + шт.)
  это «попугаи». Поле остаётся в ответе `GET /dashboard/summary` для обратной совместимости;
  фронтенд читает его только в legacy-режиме пустого объекта (KPI «Объём м³» в `ProjectPage.tsx`).
  Честные объёмы теперь per-direction: `directions[].volume` + `volume_unit` +
  `volume_excluded_count` (недоучёт видим).
  **Решение:** после стабилизации направлений удалить поле из summary-ответа, типа
  `DashboardSummary` и legacy-KPI на фронте.

- [ ] **Полный экспорт на «Все направления» — плоский список без колонки «Направление»**
  `GET /export/excel` без `?direction=` отдаёт классы всех направлений одним списком, как до
  мультикатегорийности. Для текущих моно-объектов это корректно; на реальном смешанном объекте
  пользователю придётся самому распознавать, где бетон, а где арматура.
  **Решение:** добавить колонку «Направление» (или секции по направлениям) в полный отчёт —
  при реальной потребности смешанных объектов, не раньше.

- [ ] **`SupplierPage.tsx:304` — хардкод «Объём, м³» на странице поставщика**
  Вне scope задачи направлений (страница поставщика, не объекта). Колонка суммирует количества
  без учёта единиц — та же проблема, что в записи «`get_supplier_project_stats`: `volume_m3`
  смешивает единицы измерения» выше; чинить их стоит вместе.
  **Решение:** при доработке страницы поставщика применить `volume_unit`-подход направлений
  (ср. `MonthlyTab.tsx`) либо скрывать/переименовывать колонку для смешанных профилей.

## Конфигурация

- [x] **`Settings.model_config` читает `.env` по CWD-относительному пути, а роутер настроек — по абсолютному**
  `config.py`: `SettingsConfigDict(env_file=".env")` — путь относительный, то есть значения
  зависят от рабочего каталога процесса. При этом `routers/settings.py` держит абсолютный
  `ENV_PATH` для записи (`set_key`/`unset_key`) и подгрузки через `load_dotenv`, а `get_settings`
  собирает свежий `Settings()`, который `ENV_PATH` игнорирует и читает `".env"` сам.
  Следствия: monkeypatch `ENV_PATH` не изолирует роутер полностью (на этом уже сломался
  `test_get_settings` — пришлось пинить `OPENROUTER_MODEL=""` прямо в тесте, см. комментарий
  там же); поведение зависит от CWD в проде; любая будущая проверка этого эндпоинта наступит
  на ту же мину.
  **Решение:** `env_file=Path(__file__).parent / ".env"` в `config.py` + `Settings(_env_file=ENV_PATH)`
  в роутере. После этого пин `OPENROUTER_MODEL=""` в тесте становится ненужным и его надо снять.

  **Закрыто 2026-07-27** (спека `2026-07-27-deploy-env-contract-design.md`, AC-0): `env_file`
  абсолютный, роутер передаёт `Settings(_env_file=ENV_PATH)`. Пин `OPENROUTER_MODEL=""` в
  `test_get_settings` на момент AC-0 снять не удалось (расхождение с планом Task 1, см.
  `task-1-report.md`): при прогоне обнаружилась ОТДЕЛЬНАЯ утечка в реальный `os.environ`, не
  связанная с CWD-относительностью — `alembic/env.py` делает модульный
  `load_dotenv(ROOT / ".env")` (override=False) при миграции тестовой БД, и на машине с
  непустым `OPENROUTER_MODEL` в боевом `backend/.env` это значение необратимо оседало в
  `os.environ` на весь процесс pytest. `Settings(_env_file=...)` тут бессилен: env-источник
  pydantic-settings всегда выигрывает у dotenv-источника. Заведена новая запись ниже.
  **Пин снят** (та же ветка, после бракетирования утечки в `db_engine`-фикстуре
  `backend/tests/conftest.py`: снапшот/восстановление `os.environ` вокруг `command.upgrade`)
  — `test_get_settings` в `backend/tests/integration/test_settings.py` больше не пинит
  `OPENROUTER_MODEL`.

- [ ] **`alembic/env.py` грузит реальный `backend/.env` в `os.environ` — забракетирован только один call site**
  `load_dotenv(ROOT / ".env")` (без `override`, т.е. `override=False`) выполняется на импорте
  модуля — при подготовке тестовой БД (integration-тесты мигрируют её через alembic) это
  подтягивает значения из боевого `backend/.env` разработчика в НАСТОЯЩИЙ `os.environ`
  процесса pytest, а не в изолированный dotenv-источник. Обнаружено при закрытии Task 1 плана
  `2026-07-27-deploy-env-contract` (см. `task-1-report.md`) — попытка снять пин
  `OPENROUTER_MODEL=""` в `test_get_settings` изначально падала именно по этой причине.
  **Частично закрыто** (та же ветка): `db_engine`-фикстура в `backend/tests/conftest.py` —
  единственный известный in-process вызов `command.upgrade` — теперь снимает снапшот
  `os.environ` до `command.upgrade` и восстанавливает его в `finally`, поэтому утечка не
  переживает тестовую сессию; пин `OPENROUTER_MODEL=""` в `test_get_settings` снят (см. запись
  выше).
  **Что остаётся открытым:** сам `load_dotenv(..., override=False)` в `alembic/env.py`
  никуда не делся и продолжает писать в настоящий `os.environ` при каждом in-process вызове
  Alembic — бракет в `conftest.py` защищает только этот один известный call site, а не сам
  модуль. Любой будущий in-process вызов Alembic вне `db_engine` (новый фикстур-хелпер,
  скрипт, вызывающий `command.upgrade`/`command.downgrade` напрямую) заново откроет ту же
  утечку без предупреждения. Кроме того, бракет закрывает только *реальный* `os.environ`
  процесса pytest — если `OPENROUTER_MODEL` (или любая другая переменная из `backend/.env`)
  экспортирована в родительский shell до запуска `pytest` (`export OPENROUTER_MODEL=...`),
  она никогда не была частью снапшот/restore-цикла conftest (снапшот снимается один раз за
  сессию, уже после того как shell её проэкспортировал) — такой путь бракетом не покрыт.
  **Решение:** либо `alembic/env.py` не грузит `.env` при тестовом прогоне (например, детект
  `TEST_DATABASE_URL`), либо `load_dotenv` заменяется на локальный dict без записи в
  `os.environ`, либо бракет переносится с одного call site на сам модуль (fixture/context
  manager вокруг любого in-process вызова Alembic).

- [ ] **`CONFIDENCE_THRESHOLD`, `ALLOWED_ORIGINS`, `TRUSTED_PROXIES` не следуют контракту имён**
  Контракт `<ДОМЕН>_<ЧТО>` введён спекой `2026-07-27-deploy-env-contract-design.md` §4; эти
  три переменные ему не соответствуют. `CONFIDENCE_THRESHOLD` осложнён тем, что роутер
  настроек **записывает** его в `.env` через `set_key`, то есть переименование требует
  миграции существующих файлов у всех, кто сохранял настройки из UI.
  **Решение:** переименовать при следующей правке роутера настроек, с чтением обоих имён
  на период миграции.

- [ ] **`db_guard.py`: набор target-defining query-ключей/env-переменных — рукописное перечисление, не выведенное из кода**
  Ревизии §14/§16/§17 спеки `2026-07-27-deploy-env-contract-design.md` закрыли структурно
  только половину проблемы: `_resolve_connect_target` больше не парсит DSN сам (`urlsplit` +
  список ключей), а строит триплет `(host, port, dbname)` через `create_connect_args` того
  же psycopg-диалекта SQLAlchemy, который реально открывает соединение, — это навсегда убрало
  класс расхождений парсеров (`#`-fragment, задвоенный `/` в пути, процент-кодированные ключи),
  потому что guard и драйвер читают URL одним и тем же парсером.

  Вторая половина не закрыта, а только сужена: *какие источники вообще способны
  перенаправить цель* — по-прежнему рукописное перечисление. `TARGET_DEFINING_QUERY_KEYS`
  (query-ключи `host`, `hostaddr`, `port`, `dbname`, `service`) и переменные окружения
  `PGHOSTADDR`/`PGSERVICE` (проверяются в `_resolve_connect_target`) и `PGPORT`
  (`_port_from_env_or_default`) не выводятся из кода психоп-диалекта или libpq — набор
  собран вручную по документации libpq и эмпирической проверкой на конкретной связке
  версий (SQLAlchemy 2.0.35, psycopg 3 — см. комментарии в самом `db_guard.py`). Новый
  libpq-параметр подключения или изменение того, как психоп-диалект SQLAlchemy маппит
  query-параметры в connect-args, тихо переоткроет тот же класс дыры: guard продолжит
  уверенно возвращать неверную цель, ничем не сигнализируя об ошибке. Пять раундов внешнего
  ревью подряд нашли пять разных входов в этот же класс (см. спеку, §13–§18) — свидетельство,
  что перечисление и есть хрупкое место, а момент, когда о нём никто не подумает, — рутинный
  апдейт зависимости.

  **Рецепт переверификации** (проделать при каждом триггере ниже):
  1. свериться с документацией libpq — разделы «Connection Parameters» и «Environment
     Variables» — для актуальной в проекте версии;
  2. для кандидата в query-ключи — проверить эмпирически, доходит ли он до драйвера:
     собрать URL с этим ключом и посмотреть `dialect.create_connect_args(url)` — если ключ
     попадает в connect-args и называет host, port или dbname, он target-defining;
  3. для кандидата в переменные окружения — проверить `psycopg.pq.Conninfo.get_defaults()`
     с выставленной переменной: он показывает, что сам libpq взял бы как значение по
     умолчанию.

  **Триггер:** обновление `psycopg`, `SQLAlchemy` или клиентских библиотек PostgreSQL.

  **Решение:** честной альтернативы «вывести автоматически» пока нет — если будущая версия
  libpq/psycopg однажды даст программный доступ к списку target-defining параметров, перейти
  на него. До тех пор — запинить текущий набор тестом, который падает громко при апдейте
  зависимости (форсируя осознанную сверку, а не тихий дрейф), и как минимум держать рецепт
  выше рядом с константами `TARGET_DEFINING_QUERY_KEYS`/проверками `PGHOSTADDR`/`PGSERVICE`/
  `PGPORT`, а не только в спеке.

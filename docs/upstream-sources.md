**Когда читать:** решаешь, откуда взялся код или схема, переносишь что-то из
исходных репозиториев, сверяешь границы «эквивалентного переноса».

Источник истины по инвариантам и процессу — [`AGENTS.md`](../AGENTS.md); адрес
«§2» продолжает означать исходные материалы, меняется лишь то, что за ним
лежит. Ревизии ниже **зафиксированы**: перенос делался с них, и обсуждать их
заново нельзя — можно только осознанной ревизией `AGENTS.md`.

| Репозиторий | Commit SHA | Что берём | Что НЕ берём |
|---|---|---|---|
| `github.com/zhukovvlad/udp-tenders` | `98fb67667c4483afd90816d303c263614fe01ed3` | **Основа (форк):** структура backend/frontend, auth (JWT, ротация refresh, CSRF), `UnitOfMeasure`+`UnitAlias`, паттерны CRUD/роутеров/тестов, страницы Review/ReferencePrices/Reports/Settings, `just`, Alembic | `llm*.py`, `pdf_parser.py`, `pdf_orientation.py`, OpenRouter; MinIO/`s3.py` (см. `AGENTS.md` §8); **мультиарендность: `Organization`, `ProjectOrganization`, все `org_id`** (см. `AGENTS.md` §3); доменные модели УПД (`Invoice`, `InvoiceItem`, `Document`, `Supplier`, `MaterialClass`, `CompensationCorridor` — как код удалить, как паттерн подсмотреть) |
| `github.com/zhukovvlad/tenders-go` | `121718bf45df8ea7fdc5e6346471b137d84ded88` | **Схема БД** из `cmd/internal/db/migration/` (000001 consolidated, 000002 FTS, 000007–000008 grouping): эквивалентный перенос полей, типов, CHECK-констрейнтов и индексов. Логика транзакции `ImportFullTender` — как референс для порта на Python | Go-код как таковой, RAG-эндпоинты, Google File Search, `suggested_merges`-воркфлоу, `users`/`user_sessions` (auth из udp-tenders) |
| `github.com/zhukovvlad/parser_tender_xlsx` | `0e178c097d8097bc0ea981e7b4f517518461f61e` | `app/excel_parser/` целиком, `app/constants.py` | `celery_app`, `go_module`, `gemini_module`, `rag_google_module`, `json_to_server`, `markdown_utils`, `markdown_to_chunks`, `prompts`, `main.py` |

«Эквивалентный перенос» схемы допускает: переименование `tenders` → `estimates`, слияние units с `UnitAlias`, добавление новых таблиц и колонок (в т.ч. `normalized_job_title`, `AGENTS.md` §4). Не допускает: изменение типов денежных полей, ослабление CHECK-ограничений, потерю частичных индексов.

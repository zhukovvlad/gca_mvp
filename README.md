# База расценок генподряда (GCA MVP)

Внутренний инструмент одной компании: единая база расценок договоров генподряда
2024–2027 со сквозным сравнением работ между объектами и годами, нормативами по
классам объектов, паспортом объекта и выгрузками в Excel.

Полная инструкция проекта — **[AGENTS.md](AGENTS.md)** (единственный источник
истины по архитектуре и порядку работ). `CLAUDE.md` — указатель на него.

## Происхождение кода

Boilerplate (структура backend/frontend, auth, единицы измерения, паттерны
CRUD/роутеров/тестов) импортирован из
[zhukovvlad/udp-tenders](https://github.com/zhukovvlad/udp-tenders) @ `98fb67667c44`
одним коммитом без переноса git-истории (LICENSE в источнике на этой ревизии
отсутствует; все исходные репозитории принадлежат автору проекта). Схема БД
переносится из [zhukovvlad/tenders-go](https://github.com/zhukovvlad/tenders-go)
@ `121718bf45df`, парсер XLSX — из
[zhukovvlad/parser_tender_xlsx](https://github.com/zhukovvlad/parser_tender_xlsx)
@ `0e178c097d80` (см. AGENTS.md §2).

## Стек

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x (sync), Alembic, psycopg3;
  PostgreSQL 16 + pgvector. Без брокеров: длинные операции — `BackgroundTasks`
  + таблица `import_jobs`.
- **Парсер смет:** openpyxl + spaCy с моделью `ru_core_news_sm` (лемматизация
  наименований работ). Обе версии закреплены точно и ставятся обычным
  `uv sync` — отдельного `spacy download` не нужно. Их смена меняет результат
  нормализации, а значит требует миграции каталога и кэша матчинга
  (AGENTS.md §11).
- **Хранилище исходных XLSX:** локальная директория за абстракцией `Storage`
  (путь — `STORAGE_DIR`, по умолчанию `./storage`, в `.gitignore`). Имя на диске —
  непрозрачный uuid, оригинальное имя только в БД; выдача — через авторизованный
  эндпоинт, статики над директорией нет (AGENTS.md §8).
- **Frontend:** React + TS, Vite, shadcn/ui, Tailwind, TanStack Query,
  TanStack Table, Recharts.
- Task runner — [`just`](https://github.com/casey/just); Python-окружение — `uv`.

## ВАЖНО: один worker

MVP запускается **строго с одним worker-процессом uvicorn** — это условие
корректности startup-recovery загрузок (`import_jobs`, AGENTS.md §3, §6).
`just dev-backend` уже настроен правильно; никаких `--workers N`.

Обслуживание при старте (recovery зависших заданий импорта + ретенция файлов
error-заданий) выполняется в `lifespan` приложения. Второй worker при подъёме
перевёл бы в `error` задания, которые первый в этот момент выполняет. Настройка
`RUN_STARTUP_MAINTENANCE=false` отключает обслуживание — она нужна тестам и не
предназначена для прода.

## Быстрый старт

```bash
just install          # backend (uv sync) + frontend (npm ci)
cp backend/.env.example backend/.env   # заполнить SECRET_KEY (openssl rand -hex 32)
cp .env.test.example .env.test         # для локальных тестов
just db-dev-init      # создать gca_dev на локальном кластере + миграции
just create-user admin@example.com admin   # первый пользователь
just dev-backend      # http://localhost:8259 (один worker!)
just dev-frontend     # http://localhost:5173
```

Локальная БД — портативный PostgreSQL 16 + pgvector на порту `5459`
(кластер общий для проектов на машине, GCA живёт в базах `gca_dev` / `gca_test`).

## Тесты и линт

```bash
just test             # backend (pytest) + frontend (vitest)
just lint             # ruff + eslint
just typecheck-frontend
```

Integration-тесты требуют `TEST_DATABASE_URL` (см. `.env.test.example`); имя
тестовой БД обязано оканчиваться на `_test` — conftest делает `DROP SCHEMA`
перед прогоном.

## Статус (фазы AGENTS.md §9)

- [x] Фаза 0 — **условно**: выполнена на одном доступном реальном образце вместо
      требуемых 2–3, покрытие форматов ограничено. Развилка §6 закрыта: вес позиции —
      `suggested_quantity` («Предлагаемое количество»), подтверждено на 1828 из 1828
      расценённых строк; вывод внесён в AGENTS.md §4/§6. Замеры, ограничения fixture
      и открытые риски — [docs/phase0-input-data.md](docs/phase0-input-data.md);
      **дополнительные образцы нужны до пилотной приёмки парсера**.
- [x] Фаза 1 — инициализация, перенос boilerplate, чистка (LLM/PDF/MinIO/организации/УПД-домен)
- [x] Фаза 2 — схема БД (contracts, estimates, каталог, нормативы, VIEW отклонений).
      Отступления от исходников — [docs/phase2-schema.md](docs/phase2-schema.md)
- [x] Фаза 3 — парсер XLSX (`backend/parser/`). Отступления от исходника, замеры
      и открытые риски — [docs/phase3-parser.md](docs/phase3-parser.md).
      Проверено на одном реальном образце — см. оговорку фазы 0
- [x] Фаза 4 — импорт + матчинг (хранилище файлов, две сессии, каскад,
      идемпотентность/409/replace, эндпоинты загрузки и поллинга).
      Решения фазы, отступления и открытые вопросы —
      [docs/phase4-import.md](docs/phase4-import.md)
- [ ] Фаза 5 — CRUD и Review. Брифинг на старт —
      [docs/phase5-start.md](docs/phase5-start.md)
- [ ] Фаза 6 — аналитика (паспорт, матрица, отчёты)

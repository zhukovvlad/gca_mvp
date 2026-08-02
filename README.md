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
- **Frontend:** React + TS, Vite, shadcn/ui, Tailwind, TanStack Query,
  TanStack Table, Recharts.
- Task runner — [`just`](https://github.com/casey/just); Python-окружение — `uv`.

## ВАЖНО: один worker

MVP запускается **строго с одним worker-процессом uvicorn** — это условие
корректности startup-recovery загрузок (`import_jobs`, AGENTS.md §3, §6).
`just dev-backend` уже настроен правильно; никаких `--workers N`.

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
      требуемых 2–3, покрытие форматов ограничено. Семантика
      `quantity`/`suggested_quantity` (§6) сверена и подтверждена, fixture
      обезличен. Детали и открытые риски — [docs/phase0-input-data.md](docs/phase0-input-data.md);
      **дополнительные образцы нужны до пилотной приёмки парсера**.
- [x] Фаза 1 — инициализация, перенос boilerplate, чистка (LLM/PDF/MinIO/организации/УПД-домен)
- [ ] Фаза 2 — схема БД (contracts, estimates, каталог, нормативы, VIEW отклонений)
- [ ] Фаза 3 — парсер XLSX
- [ ] Фаза 4 — импорт + матчинг
- [ ] Фаза 5 — CRUD и Review
- [ ] Фаза 6 — аналитика (паспорт, матрица, отчёты)

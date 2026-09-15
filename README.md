# База расценок генподряда (GCA MVP)

Внутренний инструмент одной компании: единая база расценок договоров генподряда
2024–2027 со сквозным сравнением работ между объектами и годами, нормативами по
классам объектов, паспортом объекта и выгрузками в Excel.

Полная инструкция проекта — **[AGENTS.md](AGENTS.md)** (источник истины по
архитектурным инвариантам и процессу разработки). `CLAUDE.md` — указатель на него.

## Что уже работает

Восемь экранов; полные описания — [`docs/reference/screens.md`](docs/reference/screens.md),
перечень — [AGENTS.md §7](AGENTS.md).

1. **Договоры/Объекты** — карточка договора, загрузка XLSX, история загрузок и замен.
2. **Ручной матчинг** — очередь `TO_REVIEW` для позиций, которые не сматчились автоматически.
3. **Нормативы** (`admin`) — классы, ставки, периоды действия, ряды индексов инфляции
   и переутверждение с коэффициентом.
4. **Паспорт проекта** — `/contracts/:id/passport`: ТЭП, коммерческие условия, свод
   по статьям классификатора с ₽/м², объёмами и ставками; печатная форма.
5. **Сквозная матрица** — работы × договоры, отклонения от нормативов, drill-down
   до позиций сметы.
6. **Отчёты** — три файла Excel: свод по договору, «для банка», сравнение договоров.
7. **Сравнение договоров** — `/compare`, статьи × договоры с приведением к одному
   ценовому уровню по индексам инфляции; вход из списка договоров, пункта в меню нет.
8. **Тендеры** — `/tenders`, решётка «участники × раунды», свод по этапам.

## Происхождение кода

Boilerplate (структура backend/frontend, auth, единицы измерения, паттерны
CRUD/роутеров/тестов) импортирован из
[zhukovvlad/udp-tenders](https://github.com/zhukovvlad/udp-tenders) @ `98fb67667c44`
одним коммитом без переноса git-истории (LICENSE в источнике на этой ревизии
отсутствует; все исходные репозитории принадлежат автору проекта). Схема БД
переносится из [zhukovvlad/tenders-go](https://github.com/zhukovvlad/tenders-go)
@ `121718bf45df`, парсер XLSX — из
[zhukovvlad/parser_tender_xlsx](https://github.com/zhukovvlad/parser_tender_xlsx)
@ `0e178c097d80`.

Полные SHA, что из каждого источника берётся и что не берётся, и границы
«эквивалентного переноса» схемы — [`docs/upstream-sources.md`](docs/upstream-sources.md).

## Стек

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x (sync), Alembic, psycopg3;
  PostgreSQL 16 + pgvector. Без брокеров: длинные операции — `BackgroundTasks`
  + таблица `import_jobs`.
- **Парсер смет:** openpyxl + spaCy с моделью `ru_core_news_sm` (лемматизация
  наименований работ). Версии spaCy, модели и словарей pymorphy3 закреплены
  точно и ставятся обычным `uv sync` — отдельного `spacy download` не нужно.
  Их смена меняет результат нормализации, а значит требует миграции каталога и
  кэша матчинга — обоснование в комментарии `backend/pyproject.toml`, требование
  детерминированности — в [`docs/pitfalls/parser.md`](docs/pitfalls/parser.md).
- **Хранилище исходных XLSX:** локальная директория за абстракцией `Storage`
  (путь — `STORAGE_DIR`, по умолчанию `./storage`, в `.gitignore`). Имя на диске —
  непрозрачный uuid, оригинальное имя только в БД; выдача — через авторизованный
  эндпоинт, статики над директорией нет (AGENTS.md §8).
- **Frontend:** React 19 + TS, Vite, shadcn/ui, Tailwind 4, TanStack Query,
  TanStack Table, Recharts.
- Task runner — [`just`](https://github.com/casey/just); Python-окружение — `uv`.

## ВАЖНО: один worker

MVP запускается **строго с одним worker-процессом uvicorn** — это условие
корректности startup-recovery загрузок (`import_jobs`, AGENTS.md §3, §5).
`just dev-backend` уже настроен правильно; никаких `--workers N`.

Обслуживание при старте (recovery зависших заданий импорта + ретенция файлов
error-заданий) выполняется в `lifespan` приложения. Второй worker при подъёме
перевёл бы в `error` задания, которые первый в этот момент выполняет. Настройка
`RUN_STARTUP_MAINTENANCE=false` отключает обслуживание — она нужна тестам и не
предназначена для прода.

## Требования

- **Python 3.12** и [`uv`](https://docs.astral.sh/uv/) — backend.
- **Node.js 24+** — frontend (`frontend/.nvmrc`; на node 20 vitest зависает на
  перехвате blob-ответа в MSW).
- [`just`](https://github.com/casey/just) — все команды ниже.
- **Локальный PostgreSQL 16 + pgvector** на порту `5459`: портативный кластер
  (conda-forge/micromamba) в `%LOCALAPPDATA%\Programs\udp-pgtest`, общий для
  проектов на машине (установлен как `udp-pgtest`), GCA живёт в нём базами
  `gca_dev` / `gca_test`. Репозиторий кластер **не устанавливает** — `just
  pg-test-start` поднимает уже установленный, `just pg-test-stop` гасит; без
  него рецепты падают с «Локальный Postgres не установлен».

## Быстрый старт

```bash
just install          # backend (uv sync) + frontend (npm ci)
cp backend/.env.example backend/.env   # заполнить SECRET_KEY (openssl rand -hex 32)
cp .env.test.example .env.test         # для локальных тестов
just db-dev-init      # создать gca_dev на локальном кластере + миграции
just create-user admin@example.com admin   # первый пользователь (пароль — интерактивно)
just dev-backend      # http://localhost:8259 (один worker!)
just dev-frontend     # http://localhost:5173
```

Полезное рядом: `just db-migrate` (миграции на dev-БД), `just db-web`
(веб-просмотр таблиц кластера, нужен pgweb), `just --list` (все рецепты).

## Тесты и линт

```bash
just test             # backend (pytest) + frontend (vitest)
just lint             # ruff + eslint
just typecheck-frontend
just ci               # всё в форме CI — обязательно перед пушем
```

`just ci` — это `uv lock --check` и страж документации, затем две независимые
цепочки **параллельно**: бэкенд (ruff → `alembic check` → pytest на нескольких
воркерах) и фронт (eslint → tsc → vitest). Полный прогон — 7–9 минут.
Если он упал сотнями ошибок `out of shared memory` на этапе setup — это
известный класс отказа параллельного прогона ([`docs/TECH_DEBT.md`](docs/TECH_DEBT.md),
запись 1), а не ваш код: перезапустите.

**Правки только в `docs/` и в `AGENTS.md`** освобождены от кодовых цепочек, но
не от стража документации — для них команда `just check-agents-index`
(AGENTS.md §9.3).

Integration-тесты требуют `TEST_DATABASE_URL` (см. `.env.test.example`); имя
тестовой БД обязано оканчиваться на `_test` — conftest делает `DROP SCHEMA`
перед прогоном. Реальные сметы не коммитим: `samples/` в `.gitignore`, для
тестов — обезличенный `fixtures/gp_estimate_fixture.xlsx`.

## Документация

Таксономия — [AGENTS.md §9.2](AGENTS.md); коротко, что где искать:

| Где | Что |
|---|---|
| [`AGENTS.md`](AGENTS.md) | архитектурные инварианты и процесс разработки |
| [`docs/reference/`](docs/reference/) | сегодняшнее состояние: [схема](docs/reference/schema.md), [экраны](docs/reference/screens.md), [денежные оси](docs/reference/money-axes.md) |
| [`docs/process/`](docs/process/) | протокол работы над задачей ([реализация и ревью](docs/process/implementation.md)) |
| [`docs/superpowers/specs/`](docs/superpowers/specs/), [`plans/`](docs/superpowers/plans/) | дизайн и план каждой фичи |
| [`docs/devlog/`](docs/devlog/) | что сделано фичей, замеры, отступления от плана |
| [`docs/insights/`](docs/insights/) | выстраданные правила работы (указатель — AGENTS.md §12) |
| [`docs/pitfalls/`](docs/pitfalls/) | известные грабли стека и домена по областям (маршрутизатор — AGENTS.md §11) |
| [`docs/TECH_DEBT.md`](docs/TECH_DEBT.md) | известные проблемы действующего кода |
| [`docs/proposals/`](docs/proposals/) | проработанные и осознанно отложенные решения |
| [`docs/AGENTS-revisions.md`](docs/AGENTS-revisions.md) | полный текст прошедших ревизий `AGENTS.md` |

## Статус

**Фазы 0–7 пройдены, фаза 7 — последняя.** Единица работы с 05.08.2026 —
**фича**: брейншторм → спека → план → реализация → devlog, три гейта
(AGENTS.md §9.1). Ревизия v6.19 (07.09.2026) сняла фазу как веху процесса
совсем — новых фаз он не заводит; отчёты и рамки фаз (`docs/phaseN-*.md`)
остаются как история и не поддерживаются.

Сделанное после фаз живёт в [`docs/devlog/`](docs/devlog/) по фиче на файл —
там же тендерный контур, паспорт проекта, сравнение договоров, приведение по
инфляции, разнос «Нераспределённого» и правило цены.

Открытая оговорка фазы 0 остаётся в силе: развилка «вес позиции —
`suggested_quantity`» подтверждена на 1828 из 1828 расценённых строк, но
покрытие форматов ограничено набором доступных образцов —
[`docs/phase0-input-data.md`](docs/phase0-input-data.md). Регрессия парсера
гоняется по локальному корпусу `samples/` скриптами
`backend/scripts/snapshot_parse_samples.py` и `compare_parse_snapshots.py`.

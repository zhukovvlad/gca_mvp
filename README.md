# УПД Трекер цен

B2B веб-приложение для тендерных менеджеров строительных компаний. Загружаете PDF-накладные (УПД / счёт-фактура) — ИИ извлекает поставщика, материал, цену и объём. Приложение накапливает историю цен, строит аналитику и позволяет выгружать отчёты-обоснования в Excel.

---

## Что делает приложение

- **Загрузка УПД** — drag-and-drop PDF прямо из карточки объекта, автоматический парсинг через LLM
- **Проверка** — PDF-просмотрщик + форма редактирования для документов с низкой уверенностью ИИ
- **Дашборд** — графики средней цены по месяцам, KPI-карточки по объекту
- **Объекты** — список проектов, базовые (эталонные) цены, исключение нерепрезентативных поставщиков из расчётов
- **Поставщики** — реестр с оборотом, объёмами, отклонениями в разрезе объектов; дедупликация по ИНН; слияние дублей
- **Номенклатура** — справочник классов материалов (бетон, арматура и др.) и базовые цены по объектам
- **Отчёты** — выгрузка в Excel: средние цены по периодам, отклонения от базовых, разбивка по поставщикам (16 колонок, формулы)
- **Мультиарендность** — организации, роли (superadmin / admin / member), изоляция доступа

---

## Стек

| Слой | Технология |
|------|-----------|
| Бэкенд | Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, pydantic-settings |
| Аутентификация | pyjwt (HS256), pwdlib[argon2] — httpOnly cookies, double-submit CSRF, ротация refresh-токенов |
| База данных | PostgreSQL (`postgresql+psycopg://` DSN) — локальный кластер, Docker или managed-хостинг вроде Neon |
| Хранилище PDF | MinIO (S3-совместимое), локальный бинарь `minio.exe` |
| PDF-парсинг | OpenRouter API — Mistral OCR / Claude Vision |
| Фронтенд | React 19, TypeScript, Vite, shadcn/ui, Tailwind CSS v4, Recharts |
| State / данные | TanStack Query v5, axios |
| Тесты (BE) | pytest 9, respx, factory_boy |
| Тесты (FE) | Vitest + Testing Library + MSW v2 |
| Task runner | `just` |

---

## Запуск

### Требования

- Python 3.12 и Node.js 24+
- [just](https://just.systems/) — установить по инструкции на сайте или `winget install Casey.Just`
- [uv](https://docs.astral.sh/uv/) — менеджер зависимостей/окружений Python (`winget install astral-sh.uv` или см. сайт)
- MinIO — скачать `minio.exe` со [страницы загрузки](https://min.io/download)
- Postgres 16 — локальный кластер, Docker или managed-хостинг вроде [Neon](https://neon.tech) (бесплатный tier)
- Аккаунт на [OpenRouter](https://openrouter.ai)

### 1. Настройка переменных окружения

```bash
cp backend/.env.example backend/.env
```

Обязательные переменные в `backend/.env`:

| Переменная | Описание |
|------------|---------|
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `DATABASE_URL` | DSN Postgres (`postgresql+psycopg://...`). Локальный кластер, Docker или managed-хостинг вроде Neon — на выбор |
| `APP_ENV` | `dev` (дефолт) или `prod`. В `dev` guard разрешает мутировать только loopback-цели и `DB_EXTRA_TARGETS`; в `prod` — любые. См. [docs/testing.md](docs/testing.md) |
| `SECRET_KEY` | Сгенерировать: `openssl rand -hex 32` |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Настройки MinIO (по умолчанию `http://localhost:9259` / `minioadmin`) |
| `ALLOWED_ORIGINS` | JSON-массив origin'ов фронтенда, например `["http://localhost:5173"]` |

Если для `DATABASE_URL` выбран Neon (один из вариантов, не обязательный): [docs/setup/neon-setup.md](docs/setup/neon-setup.md).

### 2. Установка зависимостей

```bash
just install
```

### 3. Запуск MinIO

```bash
minio.exe server ./minio-data --address ":9259" --console-address ":9260"
```

MinIO API: `http://localhost:9259`, веб-консоль: `http://localhost:9260`.

### 4. Локальный Postgres и миграция базы данных

`DATABASE_URL` из `.env.example` по умолчанию указывает на локальный кластер —
он не устанавливается автоматически. Разовая установка без админ-прав описана в
[docs/testing.md](docs/testing.md), раздел «Локальный тестовый Postgres»
(«Установка с нуля»).

```bash
just db-dev-init   # создаёт локальную БД udp_dev (если её ещё нет) и сразу накатывает миграции
```

Для последующих миграций локальной `udp_dev` — `just db-migrate`.
Подробнее про `db-dev-init` и переключатель `db_target` — [docs/testing.md](docs/testing.md), раздел «Локальная dev-БД».

**Если для `DATABASE_URL` выбран managed-хостинг (Neon и пр.)** — база уже
существует, шаг с локальным кластером не нужен. Все рецепты по умолчанию
работают с локальной `udp_dev` (`db_target=local`); чтобы вместо неё
использовать DSN из `.env`, добавляйте `db_target=env`:

```bash
just db_target=env db-migrate
```

Managed-БД — не loopback, поэтому при `APP_ENV=dev` guard откажет мутировать
её, пока нормализованная цель (`host:port/dbname`) не добавлена в
`DB_EXTRA_TARGETS` в `backend/.env`; сообщение об ошибке печатает эту тройку
ровно в том виде, который принимает переменная. Продакшн-базу мигрируют
отдельной осознанной командой — `APP_ENV=prod just db_target=env db-migrate` —
и **никогда** не добавляют в `DB_EXTRA_TARGETS`: это список для
долгоживущих dev-целей, а не постоянная индульгенция для прода.

### 5. Создание первого пользователя и организации

```bash
just create-org "Моя компания"
just create-superuser admin@example.com
```

### 6. Запуск сервисов

В двух отдельных терминалах:

```bash
just dev-backend   # http://localhost:8259  (Swagger: /docs)
just dev-frontend  # http://localhost:5173
```

---

## Разработка

```bash
just test                    # backend + frontend тесты
just test-backend-unit       # только unit-тесты, без БД (~1 с)
just test-backend-integration  # integration-тесты (нужен TEST_DATABASE_URL)
just test-frontend           # vitest
just lint                    # ruff + eslint
just typecheck-frontend      # tsc --noEmit
just coverage-backend        # HTML-отчёт → backend/htmlcov/index.html
```

Полный список команд: `just` (без аргументов).

---

## Структура проекта

```
UDP/
├── backend/
│   ├── main.py              — FastAPI: CORS, CSRF middleware, роутеры
│   ├── models.py            — ORM: Project, Document, Invoice, InvoiceItem,
│   │                          MaterialClass, ReferencePrice, Supplier,
│   │                          Organization, User, RefreshToken, ...
│   ├── crud/                — операции с БД (6 модулей):
│   │   ├── projects.py      — Project + ReferencePrice
│   │   ├── materials.py     — MaterialClass
│   │   ├── documents.py     — Document + Invoice
│   │   ├── calculations.py  — avg_price, deviation, export-строки
│   │   ├── suppliers.py     — Supplier + аналитика
│   │   ├── supplier_exclusions.py — исключения поставщиков из расчётов
│   │   └── admin.py         — суперпользовательский CRUD: орги, пользователи, матрица ролей
│   ├── security.py          — JWT, хэширование паролей, CSRF
│   ├── auth.py              — FastAPI-зависимости: get_current_user, роли
│   ├── pdf_parser.py        — парсинг УПД через OpenRouter API
│   ├── s3.py                — работа с MinIO
│   ├── cli.py               — CLI: create-superuser, create-org
│   ├── routers/             — REST API:
│   │   ├── auth.py          — login, logout, refresh, me
│   │   ├── projects.py      — объекты, исключения поставщиков
│   │   ├── invoices.py      — загрузка и редактирование УПД
│   │   ├── dashboard.py     — расчётная аналитика
│   │   ├── export.py        — Excel-выгрузка
│   │   ├── suppliers.py     — реестр поставщиков, слияние
│   │   ├── material_classes.py — номенклатура
│   │   ├── reference_prices.py — базовые цены
│   │   ├── orgs.py          — организации
│   │   ├── admin.py         — административные операции
│   │   └── settings.py      — настройки
│   ├── alembic/             — миграции БД
│   └── tests/               — unit/ + integration/ + fixtures/
├── frontend/src/
│   ├── pages/               — Dashboard, Projects, ProjectPage,
│   │                          Suppliers, SupplierPage,
│   │                          Materials, MaterialPage,
│   │                          Reports, Review, Settings, LoginPage,
│   │                          admin/ (AdminOrganizations, AdminOrgDetail,
│   │                            AdminOrgCreate, AdminUsers, AdminUserCreate),
│   │                          handbook/ (Handbook, HandbookArticle,
│   │                            ConcreteAveragePrice, articles.ts)
│   ├── components/          — ui/, ui-domain/, layout/,
│   │                          dashboard/, projects/, invoices/, review/,
│   │                          admin/, handbook/
│   ├── services/            — API-клиент (axios), TanStack Query, queryKeys
│   ├── lib/                 — format, constants, utils, useDebounce, password
│   └── types/               — TypeScript-типы по доменам
├── docs/
│   ├── TECH_DEBT.md         — отслеживаемый технический долг
│   ├── testing.md           — архитектура тестов, гайд по добавлению
│   ├── ui/routes-architecture.md — дизайн маршрутов и навигации
│   └── setup/neon-setup.md  — инструкция по настройке БД
└── justfile                 — команды разработки
```

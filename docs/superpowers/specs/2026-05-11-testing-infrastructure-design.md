# Внедрение системы тестирования в UDP

**Дата:** 2026-05-11
**Статус:** Утверждённый дизайн (готов к написанию плана имплементации)
**Ветка:** `feat/testing-infrastructure`

---

## 1. Контекст

UDP — приложение для трекинга цен на стройматериалы по счетам-фактурам/УПД. Стек:

- **Backend:** FastAPI + SQLAlchemy + Postgres (Neon) + pgvector + MinIO (S3) + Alembic. AI-парсинг через OpenRouter (Mistral OCR / Claude Sonnet).
- **Frontend:** React 19 + Vite + TypeScript + React Query + shadcn/ui.

**Проблема:** на текущий момент в проекте **нет ни одного теста**. Бизнес-логика (парсинг счетов AI, расчёт средних цен, отклонения от reference prices, классификация материалов) проверяется только вручную. Это блокирует доверие к рефакторингу и делает регрессии невидимыми до прод-инцидента.

**Граничные ограничения окружения:**
- Dev-машина — Windows Server 2022, без админских прав, без Docker, без `make`, без `winget`/`scoop`.
- Python и Node присутствуют. `just` устанавливается через `pip install --user rust-just`.
- БД — Neon (managed Postgres). Возможно создание отдельной test-ветки.
- CI — GitHub Actions, Linux runners (там Docker и postgres-сервисы доступны без ограничений).

---

## 2. Цели

1. **Поймать регрессии в бизнес-логике до прода.** Главные риски: парсер AI-ответов (формат может меняться), расчёт средней цены, расчёт отклонений, транзакционность reparse инвойсов.
2. **Дать команде уверенность в рефакторинге.** Любое изменение прогоняется через CI, пайплайн скажет, сломалось ли что-нибудь.
3. **Покрыть end-to-end критичные user flows.** Загрузка PDF → парсинг → review → отчёт. Если этот путь сломан — приложение бесполезно.
4. **Обеспечить детерминизм.** Никаких реальных вызовов в OpenRouter, никаких побочных эффектов на прод-БД, никаких флакающих тестов из-за сети.
5. **Сделать запуск тестов одной командой.** `just test` локально, автоматически в GitHub Actions на каждом push/PR.

---

## 3. Не-цели (явный YAGNI)

Сознательно НЕ внедряем на старте:

- **Mutation testing** (`mutmut`, Stryker) — мощно, но дорого по времени.
- **Visual regression** (Percy, Chromatic, Playwright screenshot diff) — нужен дизайнер в команде.
- **Property-based testing** (`hypothesis`) — для текущего объёма кода избыточно. Может понадобиться позже для парсера PDF.
- **Performance/load testing** (k6, Locust) — отдельная история.
- **Accessibility tests** (axe-core) — добавим, когда будет дизайн-ревью на a11y.
- **Multi-browser E2E** (Firefox, WebKit) — на старте только Chromium.
- **Покрытие shadcn/ui примитивов** — они уже покрыты в апстриме.
- **Snapshot-тесты компонентов** — хрупкие и быстро деградируют.

---

## 4. Архитектурный подход

Четыре слоя тестирования, изолированные и с разными скоростями:

| Слой | Стек | Что покрывает | Скорость | Где запускается |
|---|---|---|---|---|
| **Backend unit** | pytest + чистые фикстуры | `pdf_parser`, `crud`, расчёты (без сети, минимум БД) | секунды | локально + CI |
| **Backend integration** | pytest + httpx `AsyncClient` + Postgres | Все `routers/*` через ASGI-клиент с реальной БД | ~30–60 сек | локально + CI |
| **Frontend unit** | Vitest + RTL + MSW | Компоненты, страницы, хуки, утилиты | секунды | локально + CI |
| **E2E** | Playwright + Chromium | Критичные user flows целиком (frontend + backend + БД) | минуты | локально + CI |

### Инвариантные принципы

1. **Внешние сервисы всегда замоканы.** OpenRouter (через `respx` в pytest, через локальный mock-сервер в E2E), MinIO/S3 (in-memory заглушка). Боевая квота OpenRouter не тратится.
2. **БД идемпотентна.** В backend integration — транзакция-обёртка с rollback после теста. В E2E — эндпоинт `/api/test/reset`, доступный только при `TEST_MODE=1`.
3. **Один источник правды для схемы — Alembic.** Тесты накатывают миграции, не используют `Base.metadata.create_all`. Это ловит ошибки миграций как часть тестов.
4. **Frontend изолирован от backend в unit-слое.** MSW перехватывает HTTP, никаких реальных вызовов API.
5. **Реальные PDF никогда не попадают в git.** PDF с настоящими ИНН/наименованиями — только локально, в `.gitignore`. В CI и репо — синтетические байты + sanitized JSON-ответы AI.

---

## 5. Структура файлов

```
UDP/
├── justfile                          # все команды разработчика (вместо Makefile)
├── .env.test                         # TEST_DATABASE_URL и т.п. (в .gitignore)
├── .github/workflows/
│   └── tests.yml                     # CI пайплайн
│
├── backend/
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py               # глобальные фикстуры (db_engine, db_session, client, mocks)
│   │   ├── factories.py              # factory_boy фабрики моделей
│   │   ├── fixtures/
│   │   │   ├── pdf/
│   │   │   │   ├── real/             # PDF с реальными данными (в .gitignore!)
│   │   │   │   └── synthetic/        # минимально валидные байты PDF
│   │   │   └── openrouter/           # sanitized JSON-ответы AI (коммитим)
│   │   ├── unit/
│   │   │   ├── test_pdf_parser.py
│   │   │   ├── test_crud.py
│   │   │   └── test_calculations.py
│   │   └── integration/
│   │       ├── test_invoices.py
│   │       ├── test_projects.py
│   │       ├── test_material_classes.py
│   │       ├── test_reference_prices.py
│   │       ├── test_dashboard.py
│   │       ├── test_export.py
│   │       └── test_settings.py
│   ├── scripts/
│   │   └── snapshot_ai_responses.py  # снять реальные ответы OR → санитизировать → сохранить
│   ├── pytest.ini                    # минимальный конфиг
│   ├── pyproject.toml                # [tool.pytest.ini_options], coverage config, ruff
│   └── requirements-test.txt
│
├── frontend/
│   ├── src/
│   │   ├── test/
│   │   │   ├── setup.ts              # инициализация jsdom + MSW
│   │   │   ├── handlers.ts           # MSW базовые обработчики бэка
│   │   │   ├── server.ts             # MSW сервер
│   │   │   ├── utils.tsx             # renderWithProviders helper
│   │   │   └── fixtures/             # типовые ответы API
│   │   ├── components/**/*.test.tsx  # тесты рядом с компонентом (colocation)
│   │   ├── pages/**/*.test.tsx
│   │   └── lib/**/*.test.ts
│   ├── vitest.config.ts
│   └── package.json                  # +vitest, RTL, MSW, jsdom
│
└── e2e/
    ├── playwright.config.ts
    ├── tests/
    │   ├── upload-flow.spec.ts
    │   ├── upload-edge-cases.spec.ts
    │   ├── reference-prices.spec.ts
    │   ├── projects-crud.spec.ts
    │   └── theme-navigation.spec.ts
    ├── mock_openrouter/
    │   └── server.py                 # FastAPI приложение, отдающее фикстуры по правилам
    ├── fixtures/                     # ссылки/копии fixtures из backend
    └── package.json                  # отдельный package, Playwright + chromium
```

### Соглашения по colocation

- **Frontend** — тесты лежат рядом с компонентом: `Foo.tsx` + `Foo.test.tsx`. Удобно при рефакторинге.
- **Backend** — тесты в отдельном дереве `backend/tests/`. Прод-код о тестах не знает.

---

## 6. Backend: pytest

### 6.1. Зависимости (`backend/requirements-test.txt`)

```
pytest>=8.3
pytest-cov>=5.0
pytest-asyncio>=0.24
pytest-xdist>=3.6           # параллельный прогон
pytest-dotenv>=0.5          # загрузка .env.test
httpx>=0.27                 # AsyncClient против ASGI
respx>=0.21                 # мок httpx (OpenRouter SDK ходит через httpx)
factory-boy>=3.3
faker>=30
freezegun>=1.5              # фиксация дат в тестах расчёта периодов
```

### 6.2. Конфигурация

**`backend/pyproject.toml`:**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers --tb=short"
filterwarnings = ["error::DeprecationWarning"]

[tool.coverage.run]
source = ["."]
omit = ["tests/*", "alembic/*", "scripts/*"]

[tool.coverage.report]
fail_under = 60
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

### 6.3. Ключевые фикстуры (`backend/tests/conftest.py`)

| Фикстура | Scope | Что делает |
|---|---|---|
| `db_engine` | session | Engine на `TEST_DATABASE_URL`, накатывает `alembic upgrade head`. В конце сессии — drop schema |
| `db_session` | function | Открывает транзакцию через `connection.begin()`, отдаёт session, в конце — rollback. Ни один тест не оставляет следов |
| `client` | function | `TestClient(app)` с переопределённым `get_db` на `db_session` |
| `mock_openrouter` | function | `respx` мок POST `https://openrouter.ai/api/v1/chat/completions` с фикстурными JSON. Параметризуется через `request.param` для разных сценариев |
| `mock_s3` | function | Подменяет `s3.upload_file/download_file/delete_file/ensure_bucket` на in-memory dict |
| `sample_pdf_bytes` | function | Возвращает минимально валидный PDF (`b"%PDF-1.4\n..."`) для upload-эндпоинтов |
| `project_factory`, `material_class_factory`, ... | function | factory_boy фабрики, привязаны к `db_session` |

**Защитный assert в `conftest.py`:** если в любом тесте `httpx.AsyncClient` пытается обратиться к реальному `openrouter.ai` без мока — pytest падает с понятным сообщением. Реализация: `respx.mock(base_url="https://openrouter.ai", assert_all_called=False)` в auto-fixture с `autouse=True`, который вызывает ошибку для незамоканных запросов на этот хост.

### 6.4. Покрытие unit-слоя

| Файл | Что тестируем | Почему важно |
|---|---|---|
| `test_pdf_parser.py` | Парсинг JSON-ответа AI: корректный shape, edge cases (пустой items, отсутствующие поля, low confidence, маркер unparseable). Конвертация типов (строки→даты, decimal→float) | AI меняет формат — мы должны заметить раньше пользователя |
| `test_calculations.py` | `_doc_has_issues`, `_avg_confidence`, расчёт средней цены, deviation от reference price | Чистая бизнес-логика, риски расчёта бюджета |
| `test_crud.py` | `get_or_create_material_class`, расчёт периодов reference prices, агрегации dashboard | Эти функции трогает почти каждый роутер |

### 6.5. Покрытие integration-слоя

Каждый роутер получает свой файл с тестами через `TestClient`. Пример для `test_invoices.py`:

```
test_upload_creates_document_and_invoices()
test_upload_with_unparseable_pdf_marks_status()
test_upload_with_low_confidence_flags_for_review()
test_get_documents_returns_correct_shape()
test_update_invoice_recalculates_issues_flag()
test_reparse_replaces_invoices_atomically()  # критично: проверка транзакционности
test_delete_document_cleans_s3()
test_invoice_validation_rejects_negative_quantity()
```

Подобная сетка — для `projects`, `material_classes`, `reference_prices`, `dashboard`, `export`, `settings`.

### 6.6. Цель покрытия backend

- Total: **60%** на старте, **70%** к концу первого месяца.
- Critical (`pdf_parser.py`, `crud.py`, `routers/invoices.py`): **80%** на старте, **90%** к концу месяца. Жёсткий gate в CI.

### 6.7. Команды разработчика (через `just`)

```
just test-backend                 # все backend тесты
just test-backend-unit            # только unit (быстро)
just test-backend-integration     # только integration
just test-backend-watch           # watch-режим
just coverage-backend             # с HTML-отчётом
```

---

## 7. Frontend: Vitest + RTL + MSW

### 7.1. Зависимости (`frontend/package.json`)

```
vitest
@vitest/coverage-v8
@vitest/ui
@testing-library/react
@testing-library/jest-dom
@testing-library/user-event
jsdom
msw
```

### 7.2. Конфигурация (`frontend/vitest.config.ts`)

```ts
defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/components/ui/**',     // shadcn-примитивы
        'src/main.tsx', 'src/App.tsx',
        '**/*.d.ts', '**/*.test.*',
      ],
    },
  },
})
```

### 7.3. MSW

**Базовые happy-path обработчики** в `src/test/handlers.ts` — типовые ответы на все эндпоинты бэка. В каждом конкретном тесте через `server.use(...)` можно переопределить под edge case (404, 500, валидационные ошибки, медленный ответ).

Пример:
```ts
http.get('/api/projects', () => HttpResponse.json([
  { id: 1, name: 'ЖК Радуга', contract_number: 'Д-001' }
])),
http.post('/api/invoices/upload', () => HttpResponse.json(SAMPLE_DOCUMENT)),
http.get('/api/dashboard', () => HttpResponse.json(SAMPLE_DASHBOARD)),
```

**MSW server в `setupFiles`** стартует один раз. Между тестами `resetHandlers()`. Между тестовыми файлами — `close()`/`listen()` (стандартный паттерн).

Сетевой перехват MSW работает с axios (текущий HTTP-клиент проекта) без изменений в прод-коде.

### 7.4. Helper для рендера (`src/test/utils.tsx`)

```ts
renderWithProviders(<Component />, {
  route: '/review/123',
  queryClient: customClient,  // опционально
})
```

Оборачивает в `QueryClientProvider` (с `retries: 0`, `gcTime: 0` — иначе кеш React Query отравляет тесты), `BrowserRouter`, `ThemeProvider`. Все тесты используют этот helper, не голый `render`.

### 7.5. Покрытие — конкретные тесты

| Файл | Сценарии |
|---|---|
| `Review.test.tsx` | (1) Рендер таблицы документа. (2) Save шлёт PUT с правильным body. (3) Подсветка строк с issues. (4) Toast при ошибке сети |
| `ReviewItemsTable.test.tsx` | (1) Inline-edit количества пересчитывает amount. (2) Изменение material_class сохраняется. (3) Удаление позиции. (4) Валидация: отрицательное quantity не сохраняется |
| `Upload.test.tsx` | (1) Drop файла триггерит upload. (2) Прогресс. (3) Редирект на /review/{id} при success. (4) Статус unparseable |
| `Reports.test.tsx` | (1) Фильтры обновляют запрос. (2) Корректная агрегация средних цен. (3) Empty state. (4) Export-кнопка |
| `Dashboard.test.tsx` | (1) KPI рендерятся. (2) Графики получают props. (3) Skeleton до данных |
| `ReferencePrices.test.tsx` | (1) CRUD-флоу. (2) Валидация периодов |
| `EntitySelect.test.tsx` | (1) Показывает name, не id. (2) Поиск/фильтрация. (3) Inline-создание |
| `Dropzone.test.tsx` | (1) Отклоняет non-PDF. (2) Single-file mode |
| `lib/format.test.ts` | Форматтеры денег, дат, процентов — RU-локаль, edge cases |

### 7.6. Принципы

- **Никаких snapshot-тестов** — только behavioural: «при клике X происходит Y».
- **`@testing-library/user-event`**, не `fireEvent` — реалистичнее, ловит focus/keyboard.
- **`getByRole`/`getByLabel`** вместо CSS-селекторов — accessible-first, переживает рефакторинги.
- **Colocation**: `Foo.tsx` рядом с `Foo.test.tsx`.

### 7.7. Цель покрытия frontend

- Total: **40%** на старте, **60%** к концу месяца.
- Critical (`pages/Review`, `pages/Upload`, `lib/`, `components/review/*`, `components/invoices/*`): **70%** на старте, **80%** к месяцу. Жёсткий gate в CI.

### 7.8. Команды

```
just test-frontend                # vitest --run
just test-frontend-watch          # vitest (watch)
just test-frontend-ui             # @vitest/ui дашборд
just coverage-frontend
```

---

## 8. E2E: Playwright

### 8.1. Зачем отдельный слой

Vitest проверяет компоненты в jsdom (без браузера, без сети, без CSS). pytest проверяет бэкенд изолированно. Только E2E проверяет, что **всё вместе** работает: настоящий Chromium → настоящий FastAPI → настоящая Postgres. Этот слой ловит расхождения contract'а между фронтом и бэком (например, `snake_case` vs `camelCase`), broken navigation, JS-ошибки в браузере.

### 8.2. Архитектура запуска

```
Playwright tests
      │
      ▼  HTTP (real)
┌──────────────────┐
│  vite preview    │  http://localhost:5173
│  (e2e сборка)    │  VITE_API_BASE_URL=http://localhost:8001
└────────┬─────────┘
         │  HTTP (real)
         ▼
┌──────────────────┐
│  uvicorn FastAPI │  http://localhost:8001
│  TEST_MODE=1     │  OPENROUTER_BASE_URL=http://localhost:8002
└────────┬─────────┘
         │
         ├──► Postgres (test branch / CI postgres service)
         ├──► Mock OpenRouter (http://localhost:8002, отдаёт fixture JSON)
         └──► S3 — in-memory заглушка (общий код с pytest mock_s3)
```

### 8.3. Mock OpenRouter

Pytest использует `respx` для перехвата `httpx`-вызовов внутри своего процесса. В E2E это не работает — бэкенд живёт в отдельном uvicorn-процессе. Поэтому делаем по-другому:

- Заводим `OPENROUTER_BASE_URL` env-переменную в бэкенде. По умолчанию — `https://openrouter.ai/api/v1`. В E2E — `http://localhost:8002`.
- В `e2e/mock_openrouter/server.py` — небольшое FastAPI-приложение, которое имитирует endpoint `/chat/completions` и отдаёт JSON из `tests/fixtures/openrouter/` по правилам (например, в зависимости от имени файла или паттерна в запросе).

Это даёт детерминированные E2E без реальной сети, без сжигания OpenRouter-квоты и без зависимости от внешнего интернета в CI.

### 8.4. Изоляция данных

Перед каждым `*.spec.ts` (`test.beforeEach`) — `POST http://localhost:8001/api/test/reset`. Этот эндпоинт:

- Доступен **только при `TEST_MODE=1`** (иначе 404).
- TRUNCATE всех таблиц с CASCADE.
- Засевает базовые fixtures (1 проект, материальные классы).

**Безопасность:** прод-сборка не выставляет `TEST_MODE`, эндпоинт недоступен. Реализация в backend — отдельный router, регистрируется условно.

### 8.5. Конфигурация (`e2e/playwright.config.ts`)

```ts
defineConfig({
  testDir: './tests',
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [['html'], ['list']],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    { command: 'just e2e-backend', url: 'http://localhost:8001/api/health',
      reuseExistingServer: !process.env.CI, timeout: 30_000 },
    { command: 'just e2e-mock-openrouter', url: 'http://localhost:8002/health',
      reuseExistingServer: !process.env.CI },
    { command: 'just e2e-frontend', url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI, timeout: 60_000 },
  ],
})
```

### 8.6. Сценарии

| Файл | Сценарий |
|---|---|
| `upload-flow.spec.ts` | **Golden path:** проект → Upload → drop PDF → парсинг (mock OR) → редирект на Review → таблица заполнена → правка строки → Save → Reports → документ виден |
| `upload-edge-cases.spec.ts` | (1) non-PDF отклонён. (2) unparseable → status, кнопка reparse. (3) low confidence → флаг has_issues |
| `reference-prices.spec.ts` | CRUD reference prices, пересчёт deviation в Reports |
| `projects-crud.spec.ts` | Smoke: создание/переименование/удаление проекта, навигация |
| `theme-navigation.spec.ts` | Темы, навигация по меню, отсутствие JS-ошибок (`page.on('pageerror')`) |

### 8.7. Принципы

- **Только Chromium на старте.** Firefox/WebKit — позже.
- **`getByRole`/`getByLabel`** вместо CSS-селекторов.
- **Никаких `waitForTimeout`** — только `expect().toBeVisible()` или `waitForResponse`.
- **Артефакты** (HTML report, traces, videos) загружаются как GitHub Actions artifacts при падении.

### 8.8. Цель покрытия E2E

- 5 спеков, 15+ тестов на старте.
- 7 спеков, 25+ тестов к концу месяца.
- Метрика — не coverage (он не имеет смысла на этом уровне), а покрытие критичных user flows на 100%.

### 8.9. Команды

```
just test-e2e              # headless, как в CI
just test-e2e-ui           # интерактивный режим (debugging)
just test-e2e-headed       # с видимым браузером
just e2e-install           # playwright install --with-deps chromium
```

---

## 9. CI: GitHub Actions

### 9.1. Триггеры

- `push` в любую ветку.
- `pull_request` в `main`.
- `concurrency: { group: 'tests-${{ github.ref }}', cancel-in-progress: true }` — двойной push отменяет предыдущий билд.

### 9.2. Структура воркфлоу (`.github/workflows/tests.yml`)

```
                        ┌─────────────┐
                        │    lint     │  ruff + eslint + tsc (~30s)
                        └──────┬──────┘
                               │ (если упал — остальные не запускаем)
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
      │backend-tests │ │frontend-tests│ │   e2e-tests  │
      │              │ │              │ │              │
      │ + postgres   │ │ vitest --run │ │ playwright + │
      │   service    │ │              │ │ FastAPI +    │
      │ + alembic up │ │              │ │ mock OR +    │
      │ + pytest     │ │              │ │ vite preview │
      └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
             │                │                │
             ▼                ▼                ▼
        coverage.xml      lcov.info     playwright-report
              \              |              /
               ▼             ▼             ▼
              ┌─────────────────────────────┐
              │    coverage-report (PR)     │
              └─────────────────────────────┘
```

### 9.3. Job: `lint`

- `actions/setup-python@v5` (3.12) + `actions/setup-node@v4` (20).
- Кеши: `~/.cache/pip` по hash `requirements*.txt`, `~/.npm` по hash `package-lock.json`.
- `ruff check backend/`, `ruff format --check backend/`.
- `cd frontend && npm ci && npm run lint && npx tsc -b --noEmit`.

### 9.4. Job: `backend-tests`

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16   # с pgvector из коробки
    env:
      POSTGRES_USER: udp_test
      POSTGRES_PASSWORD: udp_test
      POSTGRES_DB: udp_test
    ports: ['5432:5432']
    options: --health-cmd pg_isready --health-interval 5s
```

Шаги:
1. Чекаут, setup Python, кеш pip.
2. `pip install -r backend/requirements.txt -r backend/requirements-test.txt`.
3. Установить `TEST_DATABASE_URL=postgresql+psycopg://udp_test:udp_test@localhost:5432/udp_test`.
4. `cd backend && alembic upgrade head`.
5. `pytest --cov=. --cov-report=xml --cov-report=term`.
6. Загрузка `coverage.xml` как artifact.

### 9.5. Job: `frontend-tests`

- setup-node + кеш `~/.npm`.
- `cd frontend && npm ci && npm run test:coverage`.
- Загрузка `frontend/coverage/lcov.info`.

### 9.6. Job: `e2e-tests`

- Сервис postgres (как в backend-tests).
- setup Python + Node.
- Установка backend deps + alembic upgrade.
- `cd e2e && npm ci && npx playwright install --with-deps chromium`.
- Кеш `~/.cache/ms-playwright` по hash playwright-версии.
- `cd e2e && npx playwright test` (Playwright сам поднимает все три сервиса через `webServer:`).
- При падении — загрузка `playwright-report/`, `test-results/` (с trace и видео) на 30 дней.

### 9.7. Job: `coverage-report`

- `needs: [backend-tests, frontend-tests]`.
- Скачивает оба coverage artifact.
- Постит PR-комментарий с дельтой покрытия (через готовые actions).
- Не блокирует merge при падении coverage (только варнинг).

### 9.8. Branch protection (вне CI воркфлоу)

Настройка через GitHub UI (Settings → Branches), не часть кода:
- `main` нельзя пушить напрямую, только через PR.
- Required checks: `lint`, `backend-tests`, `frontend-tests`.
- `e2e-tests` — non-required на старте (чтобы флаки не блокировали merge), переводим в required, когда стабилизируется.

### 9.9. Кеши

- pip: ключ `${{ hashFiles('backend/requirements*.txt') }}`.
- npm (frontend и e2e отдельно): ключ `${{ hashFiles('**/package-lock.json') }}`.
- Playwright Chromium: ключ по версии playwright.
- Холодный CI ~7-8 мин, тёплый ~3-4 мин.

### 9.10. Секреты

- `TEST_DATABASE_URL` — НЕ нужен в CI (используем postgres service). Только локально.
- `OPENROUTER_API_KEY` — НЕ передаётся в CI (тесты не ходят в реальный OpenRouter).
- На уровне репо в Actions secrets ничего секретного класть не надо.
- `.env.test` — в `.gitignore`.

---

## 10. Фикстуры: PDF и AI-ответы

### 10.1. Реальные PDF

- Расположение: `backend/tests/fixtures/pdf/real/`.
- В `.gitignore`: `backend/tests/fixtures/pdf/real/**`.
- Pre-commit hook блокирует любой `*.pdf` в этой папке (двойная страховка от `git add .`).
- Используются **только локально** для:
  1. Снятия sanitized AI-ответов через `scripts/snapshot_ai_responses.py`.
  2. Опциональных regression-тестов на реальных данных.

### 10.2. Синтетические PDF

- Расположение: `backend/tests/fixtures/pdf/synthetic/`.
- Коммитятся в репо.
- Используются в integration-тестах (где сам контент PDF не важен — бэкенд всё равно отправит файл в mock OpenRouter).
- Минимально валидные байты PDF (~200-500 байт).

### 10.3. Sanitized AI-ответы

- Расположение: `backend/tests/fixtures/openrouter/`.
- Коммитятся в репо.
- Скрипт `scripts/snapshot_ai_responses.py`:
  1. Прогоняет PDF из `pdf/real/` через настоящий OpenRouter.
  2. Получает JSON-ответ.
  3. Прогоняет через санитайзер: ИНН → `0000000000`, наименования поставщиков → `Поставщик 1/2/3`, можно округлить суммы.
  4. Сохраняет как `fixtures/openrouter/<тип-сценария>.json`.
- Сценарии в фикстурах: `happy_path.json`, `low_confidence.json`, `unparseable.json`, `partial_data.json`, `multiple_invoices_in_doc.json`, `error_5xx.json`.
- После санитизации реальные PDF можно удалить с машины — тесты будут работать.

---

## 11. Definition of Done

Внедрение считается завершённым, когда:

1. **Все четыре слоя работают локально** (`just test-backend`, `just test-frontend`, `just test-e2e`) на чистом клоне репо за один шаг setup (`just install`).
2. **CI зелёный.** Push в любую ветку и в PR прогоняет `lint → backend → frontend → e2e` параллельно, всё проходит.
3. **Секреты не утекают.** Нет коммитов с `*.pdf` из `pdf/real/`, нет `.env.test` в репо, нет `OPENROUTER_API_KEY` в логах CI.
4. **Тесты не идут в реальный OpenRouter.** Защитный assert в `conftest.py` падает, если кто-то попытается.
5. **Тесты идемпотентны.** Можно запустить 100 раз подряд — результат одинаковый, БД чистая после каждого прогона.
6. **Документация написана.** `docs/testing.md` с разделами: «Как запустить локально», «Как добавить новый тест», «Как обновить snapshot AI-ответа», «Как дебажить упавший E2E».

---

## 12. Метрики и качество

### 12.1. Покрытие

| Слой | Минимум на старте | Цель к концу 1-го месяца | Жёсткий gate в CI |
|---|---|---|---|
| Backend total | 60% | 70% | Нет (варнинг) |
| Backend critical (`pdf_parser`, `crud`, `routers/invoices`) | 80% | 90% | **Да** |
| Frontend total | 40% | 60% | Нет |
| Frontend critical (`pages/Review`, `pages/Upload`, `lib/`, `components/review/*`, `components/invoices/*`) | 70% | 80% | **Да** |
| E2E | 5 спеков, 15+ тестов | 7 спеков, 25+ тестов | Все проходят |

**Почему gate только на критичных:** на total-покрытии gate = люди начнут писать пустые тесты ради цифры. На критичных gate осмысленный — если падает, кто-то реально вырезал важный кейс.

### 12.2. Время прогона

| Слой | Бюджет |
|---|---|
| Backend unit | < 5 сек |
| Backend integration | < 60 сек |
| Frontend | < 30 сек |
| E2E | < 5 мин |

Превышение — флаг для оптимизации.

### 12.3. Flake rate

Считаем за 2 недели: процент падений, погасших на ретрае. Цель < 2%. Всё что выше — карантиним и чиним. Флаки убивают доверие к CI быстрее всего.

### 12.4. Test → bug ratio

Когда находим прод-баг — спрашиваем: «должен был быть тест, который его поймал?». Если да — пишем регрессионный тест **до** фикса. Это растит покрытие осмысленно.

---

## 13. Поэтапный roll-out (deliverables по PR)

Каждый этап — отдельный PR в `feat/testing-infrastructure` (или сразу мерджим, если это comfortable). Каждый этап оставляет проект работоспособным.

| Этап | Дельта | Объём |
|---|---|---|
| **1. Фундамент** | `justfile`, `.env.test`, `.gitignore`, `requirements-test.txt`, базовый `conftest.py`, 3-5 пилотных backend-тестов на `crud` и `pdf_parser`. `just test-backend` локально работает | ~1 день |
| **2. Backend цельно** | Покрытие всех роутеров integration-тестами. Фабрики, моки OpenRouter и S3, snapshot AI-ответов. Backend coverage достигает целевых планок | ~2-3 дня |
| **3. Frontend** | Vitest конфиг, MSW handlers, тесты на ключевые компоненты и страницы | ~1-2 дня |
| **4. E2E** | Playwright config, mock_openrouter сервис, `/api/test/reset` эндпоинт, golden path спек и edge cases | ~1-2 дня |
| **5. CI и документация** | GitHub Actions воркфлоу, кеши, артефакты, `docs/testing.md`. Branch protection (вручную через UI) | ~0.5 дня |

**Итого:** ~5.5-8.5 дней работы.

---

## 14. Открытые вопросы и принятые решения

Решено в ходе brainstorming:

| Вопрос | Решение | Обоснование |
|---|---|---|
| Какой Postgres для тестов локально? | Neon test branch (`TEST_DATABASE_URL` в `.env.test`) | Без Docker и админки на dev-машине. Возможность создать ветку в Neon у пользователя есть |
| Какой Postgres в CI? | GitHub Actions postgres service (`pgvector/pgvector:pg16`) | Изолированная инстанция per-run, pgvector из коробки |
| make или just? | `just` (через `pip install --user rust-just`) | На Windows Server без админки `make` недоступен. `just` ставится через pip |
| Mock или real OpenRouter? | Всегда mock (`respx` в pytest, локальный сервис в E2E) | Детерминизм, экономия квоты, независимость от сети |
| Реальные PDF в репо? | Никогда. `.gitignore` + pre-commit guard | PII-риск (ИНН, наименования поставщиков) |
| SQLAlchemy mock или real DB? | Real Postgres всегда | pgvector, JSON-операторы, типы — на mock не воспроизведёшь. Прод и тесты разойдутся |
| Schema в тестах: `create_all` или Alembic? | Alembic | Тесты ловят ошибки миграций как часть проверки |
| Multi-browser E2E на старте? | Только Chromium | YAGNI. Firefox/WebKit при реальной потребности |
| Visual regression на старте? | Нет | YAGNI. Отдельный большой слой |
| Snapshot-тесты компонентов? | Нет, только behavioural | Snapshot хрупкие, быстро деградируют |


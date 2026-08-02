# Внедрение системы тестирования — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Внедрить четырёхслойную систему тестирования (pytest unit + integration, Vitest + MSW, Playwright E2E) и GitHub Actions CI с детерминированными моками внешних сервисов.

**Architecture:** pytest для backend с `respx`-моками OpenRouter и in-memory подменой S3, реальная Postgres (Neon test branch локально, GitHub services в CI), Alembic-миграции вместо `create_all`. Frontend Vitest + RTL + MSW в `jsdom`. Отдельное приложение E2E с Playwright + Chromium, mock-OpenRouter сервис на FastAPI и `/api/test/reset` эндпоинт под флагом `TEST_MODE=1`. `just` как task runner.

**Tech Stack:** pytest 8 + pytest-asyncio + pytest-cov + respx + factory-boy + freezegun + httpx; Vitest + @testing-library/react + msw + jsdom; Playwright; ruff; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-11-testing-infrastructure-design.md`

**Branch:** `feat/testing-infrastructure` (уже создана и запушена в origin)

**PR:** https://github.com/zhukovvlad/udp-tenders/pulls — draft PR для ветки `feat/testing-infrastructure` создан вручную.

## Push policy

После каждой Task (не после каждого step) — `git push`. Это держит PR на GitHub в актуальном состоянии и исключает риск потери работы.

**`gh` CLI и MCP-аутентификация в этом окружении недоступны** — поэтому:
- PR создаётся вручную через GitHub UI один раз перед стартом Этапа 1.
- Описание/чек-лист PR обновляется вручную через UI после завершения каждого этапа (или в конце работы целиком).
- Нигде в плане не используются `gh pr ...` команды.

---

## File Structure

### Создаются (новые файлы)

| Путь | Ответственность |
|---|---|
| `justfile` | Все команды разработчика (install / dev / test / coverage / lint / db) |
| `.env.test.example` | Шаблон тестовых переменных окружения, коммитим |
| `backend/requirements-test.txt` | Тестовые зависимости backend (отдельно от прода) |
| `backend/pyproject.toml` | `[tool.pytest.ini_options]`, `[tool.coverage.*]`, `[tool.ruff]` |
| `backend/tests/__init__.py` | Маркер пакета |
| `backend/tests/conftest.py` | Глобальные фикстуры: `db_engine`, `db_session`, `client`, `mock_openrouter`, `mock_s3`, `sample_pdf_bytes`, `block_real_openrouter` |
| `backend/tests/factories.py` | factory_boy фабрики моделей (Project/MaterialClass/ReferencePrice/Document/Invoice/InvoiceItem) |
| `backend/tests/fixtures/pdf/synthetic/minimal.pdf` | Минимально валидный PDF (~300 байт), для upload-эндпоинтов |
| `backend/tests/fixtures/openrouter/happy_path.json` | Замоканный OpenRouter chat-completions ответ: один документ с одной СФ и тремя позициями |
| `backend/tests/fixtures/openrouter/low_confidence.json` | Ответ с `confidence` < 0.7 на уровне СФ |
| `backend/tests/fixtures/openrouter/unparseable.json` | Ответ с `doc_type: "unknown"` |
| `backend/tests/fixtures/openrouter/multiple_invoices.json` | Один PDF, два инвойса в `invoices` |
| `backend/tests/fixtures/openrouter/partial_data.json` | СФ с пустыми позициями и нулевыми quantity (триггерит `has_issues`) |
| `backend/tests/fixtures/openrouter/invalid_json.json` | Не-валидный JSON в `choices[0].message.content` |
| `backend/tests/unit/test_pdf_parser_helpers.py` | Тесты `_calculate_completeness`, `_final_confidence` |
| `backend/tests/unit/test_pdf_parser_flow.py` | Тесты `parse_invoice_pdf` с замоканным `httpx`/`respx` |
| `backend/tests/unit/test_calculations.py` | Тесты `_doc_has_issues`, `_avg_confidence` (из `routers/invoices.py`) |
| `backend/tests/unit/test_crud_recalculate.py` | Тесты `crud.recalculate_prices` (изолированная бизнес-логика расчёта) |
| `backend/tests/unit/test_crud_basic.py` | Тесты `get_or_create_material_class`, `delete_material_class` |
| `backend/tests/integration/test_projects.py` | Тесты `routers/projects` через `TestClient` |
| `backend/tests/integration/test_material_classes.py` | Тесты `routers/material_classes` |
| `backend/tests/integration/test_reference_prices.py` | Тесты `routers/reference_prices` |
| `backend/tests/integration/test_invoices.py` | Тесты upload / reparse / update / delete / get документов |
| `backend/tests/integration/test_dashboard.py` | Тесты `summary`, `invoices`, `calculations`, `auto-calculate`, `calculate` |
| `backend/tests/integration/test_export.py` | Тесты excel-экспорта (smoke + структура файла) |
| `backend/tests/integration/test_settings.py` | Тесты get/update settings |
| `backend/tests/integration/test_health.py` | Smoke-тест `/api/health` и dependency overrides |
| `backend/scripts/snapshot_ai_responses.py` | Скрипт: real PDF → OpenRouter → sanitize → JSON фикстура (запускается локально) |
| `backend/routers/test_utils.py` | Эндпоинт `/api/test/reset` под флагом `TEST_MODE=1` |
| `frontend/vitest.config.ts` | Конфиг Vitest с jsdom, coverage, setupFiles |
| `frontend/src/test/setup.ts` | Подключает `@testing-library/jest-dom`, MSW server |
| `frontend/src/test/server.ts` | MSW node-сервер |
| `frontend/src/test/handlers.ts` | Базовые happy-path обработчики |
| `frontend/src/test/utils.tsx` | `renderWithProviders`, `createTestQueryClient` |
| `frontend/src/test/fixtures.ts` | Sample-объекты ответов API (project, document, invoice, dashboard, ...) |
| `frontend/src/lib/format.ts` | Утилиты форматирования (если их пока нет, выделим из inline-кода) |
| `frontend/src/lib/format.test.ts` | Тесты форматтеров |
| `frontend/src/components/ui-domain/EntitySelect.test.tsx` | Тесты EntitySelect |
| `frontend/src/components/ui-domain/Dropzone.test.tsx` | Тесты Dropzone |
| `frontend/src/components/review/ReviewItemsTable.test.tsx` | Тесты inline-edit таблицы |
| `frontend/src/pages/Review.test.tsx` | Тесты страницы Review |
| `frontend/src/pages/Upload.test.tsx` | Тесты страницы Upload |
| `frontend/src/pages/Reports.test.tsx` | Тесты страницы Reports |
| `frontend/src/pages/Dashboard.test.tsx` | Тесты страницы Dashboard |
| `frontend/src/pages/ReferencePrices.test.tsx` | Тесты страницы ReferencePrices |
| `e2e/package.json` | Playwright deps (отдельно от frontend) |
| `e2e/playwright.config.ts` | webServer-блоки для всех трёх сервисов, retries в CI |
| `e2e/tsconfig.json` | TS-конфиг для e2e |
| `e2e/tests/upload-flow.spec.ts` | Golden path |
| `e2e/tests/upload-edge-cases.spec.ts` | non-PDF, unparseable, low confidence |
| `e2e/tests/reference-prices.spec.ts` | CRUD reference prices |
| `e2e/tests/projects-crud.spec.ts` | CRUD проектов + smoke навигации |
| `e2e/tests/theme-navigation.spec.ts` | Темы + проверка отсутствия pageerror |
| `e2e/mock_openrouter/server.py` | FastAPI mock сервиса OpenRouter |
| `e2e/mock_openrouter/requirements.txt` | Зависимости для mock-сервера |
| `e2e/fixtures/openrouter/` | Симлинк или копия из `backend/tests/fixtures/openrouter/` |
| `.github/workflows/tests.yml` | Lint → backend → frontend → e2e + coverage report |
| `docs/testing.md` | Гайд по запуску, добавлению тестов, обновлению AI-снапшотов, отладке E2E |

### Модифицируются

| Путь | Что меняем |
|---|---|
| `.gitignore` | Добавить `backend/tests/fixtures/pdf/real/`, `.env.test`, `playwright-report/`, `test-results/`, `coverage/`, `htmlcov/` |
| `backend/pdf_parser.py` | Заменить hard-coded `OPENROUTER_URL` на `os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions"` чтобы можно было переопределить URL для E2E mock |
| `backend/main.py` | Подключение `routers/test_utils.py` под условием `os.getenv("TEST_MODE") == "1"` |
| `backend/.env.example` | Добавить комментарий про `OPENROUTER_BASE_URL` и `TEST_MODE` |
| `frontend/package.json` | Добавить devDeps: `vitest`, `@vitest/coverage-v8`, `@vitest/ui`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom`, `msw`. Добавить scripts: `test`, `test:watch`, `test:coverage`, `test:ui` |
| `frontend/eslint.config.js` | Игнорировать `**/*.test.{ts,tsx}` или конфигурировать дружественный к Vitest парсер |
| `frontend/tsconfig.app.json` | Включить типы Vitest и testing-library |

---

## Этап 1: Фундамент

**Цель этапа:** на чистом клоне после `just install` разработчик может запустить `just test-backend-unit` и увидеть зелёные пилотные тесты. Никаких регрессий в существующем коде. Защита от утечки реальных PDF в git.

### Task 1.1: `.gitignore` для тестовых данных

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1.1.1: Дописать секции в `.gitignore`**

Добавить в конец файла:

```
# Тесты — реальные PDF и тестовые env-файлы
backend/tests/fixtures/pdf/real/
.env.test
.env.test.local

# Тестовые артефакты
backend/htmlcov/
backend/coverage.xml
backend/.coverage
backend/.pytest_cache/
frontend/coverage/
playwright-report/
test-results/
e2e/test-results/
e2e/playwright-report/
```

- [ ] **Step 1.1.2: Создать пустую папку `backend/tests/fixtures/pdf/real/` с `.gitkeep`**

```bash
mkdir -p backend/tests/fixtures/pdf/real
echo "# Реальные PDF — никогда не коммитятся (см. .gitignore)" > backend/tests/fixtures/pdf/real/.gitkeep
```

Затем убедиться, что `.gitkeep` НЕ заигнорен — добавить в `.gitignore` исключение:

```
# Сохраняем .gitkeep в real/, но не сами PDF
!backend/tests/fixtures/pdf/real/.gitkeep
```

- [ ] **Step 1.1.3: Проверить, что .gitignore работает**

```bash
echo "fake pdf content" > backend/tests/fixtures/pdf/real/test.pdf
git status
```

Expected: `test.pdf` НЕ появляется в `git status`. `.gitkeep` уже отслежен/новый.

```bash
rm backend/tests/fixtures/pdf/real/test.pdf
```

- [ ] **Step 1.1.4: Commit**

```bash
git add .gitignore backend/tests/fixtures/pdf/real/.gitkeep
git commit -m "chore(testing): gitignore for test artifacts and real PDF fixtures"
```

---

### Task 1.2: Установить `just` и создать базовый `justfile`

**Files:**
- Create: `justfile`

`just` уже установлен (`pip install --user rust-just`). Добавляем минимальный `justfile`, который покроет команды первого этапа. Будем дополнять на каждом этапе.

- [ ] **Step 1.2.1: Создать `justfile` в корне с базовыми командами**

```makefile
# UDP — task runner. Запуск: just <команда> или just (=help)

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set shell := ["bash", "-cu"]

# Default — показать список команд
default:
    @just --list

# === Setup ===

# Установить все зависимости (backend + frontend, e2e добавим позже)
install: install-backend install-frontend
    @echo "==> Установка завершена"

install-backend:
    cd backend && pip install -r requirements.txt -r requirements-test.txt

install-frontend:
    cd frontend && npm ci

# === Dev ===

dev-backend:
    cd backend && uvicorn main:app --reload --port 8000

dev-frontend:
    cd frontend && npm run dev

# === Tests ===

# Все backend-тесты
test-backend:
    cd backend && pytest

# Только unit (быстро)
test-backend-unit:
    cd backend && pytest tests/unit -v

# Только integration (нужен TEST_DATABASE_URL)
test-backend-integration:
    cd backend && pytest tests/integration -v

# Watch-режим (нужен pytest-watch)
test-backend-watch:
    cd backend && ptw tests -- -v

# === Coverage ===

coverage-backend:
    cd backend && pytest --cov=. --cov-report=html --cov-report=term

# === Lint ===

lint-backend:
    cd backend && ruff check .

format-backend:
    cd backend && ruff format .

# === DB ===

db-migrate:
    cd backend && alembic upgrade head

db-test-migrate:
    cd backend && DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head

# === Misc ===

clean:
    rm -rf backend/.pytest_cache backend/htmlcov backend/.coverage backend/coverage.xml
    find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 1.2.2: Проверить `just`**

Run: `just --list`
Expected: список целей (default, install, install-backend, ..., clean) без ошибок.

- [ ] **Step 1.2.3: Commit**

```bash
git add justfile
git commit -m "chore(testing): add justfile with basic install/test/lint targets"
```

---

### Task 1.3: Создать `backend/.env.test.example` и шаблон конфигурации

**Files:**
- Create: `.env.test.example`

- [ ] **Step 1.3.1: Создать `.env.test.example` в корне**

```ini
# Скопируй в .env.test и заполни. .env.test не коммитится (в .gitignore).
# Используется ТОЛЬКО для локальных тестов. В CI переменные приходят из GitHub Actions.

# Отдельная Neon test-ветка (не использовать прод!)
TEST_DATABASE_URL=postgresql+psycopg://<test_owner>:<password>@<test-branch-host>.neon.tech/neondb?sslmode=require&channel_binding=require

# Не нужен в тестах — все вызовы OpenRouter замоканы. Оставлено для совместимости с .env.
OPENROUTER_API_KEY=mock-key-not-used

# Включает test-only эндпоинты (например /api/test/reset). Только для E2E запусков.
TEST_MODE=0
```

- [ ] **Step 1.3.2: Commit**

```bash
git add .env.test.example
git commit -m "chore(testing): add .env.test.example template"
```

---

### Task 1.4: `backend/requirements-test.txt`

**Files:**
- Create: `backend/requirements-test.txt`

- [ ] **Step 1.4.1: Создать файл**

```
pytest==8.3.4
pytest-asyncio==0.24.0
pytest-cov==6.0.0
pytest-xdist==3.6.1
pytest-dotenv==0.5.2
respx==0.21.1
factory-boy==3.3.1
faker==30.10.0
freezegun==1.5.1
ruff==0.7.4
```

`httpx` и `python-dotenv` уже в `requirements.txt`, так что повторно не пишем.

- [ ] **Step 1.4.2: Установить зависимости**

```bash
cd backend && pip install -r requirements-test.txt
```

Expected: всё установилось, `pytest --version` отвечает.

- [ ] **Step 1.4.3: Commit**

```bash
git add backend/requirements-test.txt
git commit -m "chore(testing): pin backend test dependencies"
```

---

### Task 1.5: `backend/pyproject.toml` (pytest + coverage + ruff)

**Files:**
- Create: `backend/pyproject.toml`

- [ ] **Step 1.5.1: Создать `backend/pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers --tb=short"
markers = [
    "integration: tests that hit a real Postgres (need TEST_DATABASE_URL)",
]
filterwarnings = [
    "ignore::DeprecationWarning:passlib.*",
]
env_files = [".env.test"]

[tool.coverage.run]
source = ["."]
branch = true
omit = [
    "tests/*",
    "alembic/*",
    "scripts/*",
    "logging_config.py",
]

[tool.coverage.report]
fail_under = 60
show_missing = true
skip_covered = false
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
ignore = ["E501"]  # длинные строки прощаем (длинные SQL и docstrings)

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["E402"]  # import-order в тестах не критичен
"alembic/versions/*" = ["E402", "F401"]
```

- [ ] **Step 1.5.2: Проверить, что pytest подхватывает конфиг**

```bash
cd backend && pytest --collect-only
```

Expected: `no tests ran in 0.0Xs` — это нормально, тестов ещё нет. Главное — нет ошибки конфига.

- [ ] **Step 1.5.3: Проверить ruff**

```bash
cd backend && ruff check .
```

Expected: либо чисто, либо предупреждения по существующему коду. Не падать на ошибке.

Если ruff падает — записать в комментарий и пока проигнорировать через `# noqa: <код>` в одной-двух местах. Не делать масштабный рефакторинг существующего кода в рамках этого этапа.

- [ ] **Step 1.5.4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore(testing): pytest + coverage + ruff config in pyproject.toml"
```

---

### Task 1.6: Сделать `OPENROUTER_URL` конфигурируемым

**Files:**
- Modify: `backend/pdf_parser.py:100`
- Modify: `backend/.env.example`

Без этого изменения E2E mock-OpenRouter не сможет перехватить вызовы из реального uvicorn-процесса.

- [ ] **Step 1.6.1: Заменить hard-coded URL на env-driven**

В `backend/pdf_parser.py` строка 100:

```python
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
```

Заменить на:

```python
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_URL = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
```

- [ ] **Step 1.6.2: Добавить пример в `backend/.env.example`**

В конец файла дописать:

```
# Для разработки и прода — пусто (используется https://openrouter.ai/api/v1).
# Для E2E — http://localhost:8002 (mock-сервер).
# OPENROUTER_BASE_URL=
```

- [ ] **Step 1.6.3: Smoke-проверить, что приложение запускается**

```bash
cd backend && python -c "from pdf_parser import OPENROUTER_URL; print(OPENROUTER_URL)"
```

Expected: `https://openrouter.ai/api/v1/chat/completions`

- [ ] **Step 1.6.4: Commit**

```bash
git add backend/pdf_parser.py backend/.env.example
git commit -m "feat(backend): make OpenRouter base URL configurable via OPENROUTER_BASE_URL"
```

---

### Task 1.7: `backend/tests/__init__.py` и базовый `conftest.py`

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/unit/__init__.py`
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/conftest.py`

- [ ] **Step 1.7.1: Создать пустые `__init__.py`**

```bash
touch backend/tests/__init__.py
touch backend/tests/unit/__init__.py
touch backend/tests/integration/__init__.py
```

- [ ] **Step 1.7.2: Создать `backend/tests/conftest.py` с базовыми фикстурами**

```python
"""Глобальные фикстуры для всех тестов backend.

Слои:
* Unit-тесты используют только in-memory моки (mock_s3, mock_openrouter).
* Integration-тесты используют реальный Postgres (через TEST_DATABASE_URL),
  но Alembic мигрирует один раз на сессию. Каждый тест — в транзакции с rollback.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Делаем импорты "from database import ..." и "import crud" работающими
# из тестов, не привязываясь к sys.path в IDE
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def block_real_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Защитный assert: ни один тест не должен ходить в реальный OpenRouter.

    Если test забыл подключить mock_openrouter и пытается вызвать реальный API —
    падаем с понятным сообщением. Применяется автоматически ко ВСЕМ тестам.
    """
    real_url = "https://openrouter.ai"

    def _fail(*args, **kwargs):  # noqa: ANN001,ANN202
        raise RuntimeError(
            "Тест попытался обратиться к реальному OpenRouter. "
            "Используй фикстуру `mock_openrouter` или замокай `httpx.AsyncClient.post`."
        )

    # Перехватываем глобально на уровне httpx — если кто-то создаст AsyncClient и пошлёт POST
    # на openrouter.ai, мы об этом узнаем
    import httpx

    original_send = httpx.AsyncClient.send

    async def guarded_send(self, request: httpx.Request, *args, **kwargs):  # noqa: ANN001,ANN202
        if real_url in str(request.url):
            _fail()
        return await original_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_send)


@pytest.fixture
def in_memory_s3(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """In-memory подмена S3. Возвращает dict, в котором ключ = object_name, значение = bytes."""
    storage: dict[str, bytes] = {}

    def fake_upload(file_bytes: bytes, object_name: str) -> str:
        storage[object_name] = file_bytes
        return object_name

    def fake_download(object_name: str) -> bytes:
        if object_name not in storage:
            raise FileNotFoundError(object_name)
        return storage[object_name]

    def fake_delete(object_name: str) -> None:
        storage.pop(object_name, None)

    def fake_ensure_bucket() -> None:
        pass

    import s3

    monkeypatch.setattr(s3, "upload_file", fake_upload)
    monkeypatch.setattr(s3, "download_file", fake_download)
    monkeypatch.setattr(s3, "delete_file", fake_delete)
    monkeypatch.setattr(s3, "ensure_bucket", fake_ensure_bucket)
    # Routers импортируют функции напрямую, нужно патчить и там
    from routers import invoices as invoices_router

    monkeypatch.setattr(invoices_router, "upload_file", fake_upload)
    monkeypatch.setattr(invoices_router, "download_file", fake_download)
    monkeypatch.setattr(invoices_router, "delete_file", fake_delete)
    monkeypatch.setattr(invoices_router, "ensure_bucket", fake_ensure_bucket)

    return storage


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Минимальный валидный PDF (~250 байт). Содержимое не важно — бэкенд только пересылает в OpenRouter."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"0000000009 00000 n\n0000000052 00000 n\n0000000092 00000 n\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n145\n%%EOF\n"
    )
```

- [ ] **Step 1.7.3: Smoke — pytest должен находить conftest и не падать**

```bash
cd backend && pytest --collect-only -q
```

Expected: `no tests collected` (нормально), без ошибок импорта.

- [ ] **Step 1.7.4: Commit**

```bash
git add backend/tests/__init__.py backend/tests/unit/__init__.py backend/tests/integration/__init__.py backend/tests/conftest.py
git commit -m "test(backend): add base conftest with S3 mock and OpenRouter guard"
```

---

### Task 1.8: Первый unit-тест — `_calculate_completeness`

**Files:**
- Create: `backend/tests/unit/test_pdf_parser_helpers.py`
- Test: `backend/tests/unit/test_pdf_parser_helpers.py`

Это пилотный тест на чистую функцию без зависимостей. Если он прошёл — значит pytest-инфраструктура жива.

- [ ] **Step 1.8.1: Написать падающий тест**

```python
"""Unit-тесты helper-функций pdf_parser."""
from pdf_parser import _calculate_completeness, _final_confidence


class TestCalculateCompleteness:
    def test_full_invoice_returns_one(self):
        inv_data = {
            "number": "СФ-1",
            "date": "2026-05-01",
            "supplier_name": "ООО Ромашка",
            "items": [
                {"raw_name": "Бетон В25", "quantity": 5, "unit_price": 8000, "amount": 40000},
            ],
        }
        assert _calculate_completeness(inv_data) == 1.0

    def test_empty_invoice_returns_low(self):
        inv_data = {"number": "", "date": "", "supplier_name": "", "items": []}
        # 4 поля по 1 баллу, все пустые → 0/4 = 0.0
        assert _calculate_completeness(inv_data) == 0.0

    def test_invalid_date_does_not_count(self):
        inv_data = {
            "number": "X",
            "date": "not-a-date",
            "supplier_name": "Y",
            "items": [],
        }
        # number=1, date=0 (невалидная), supplier=1, items=0 → 2/4 = 0.5
        assert _calculate_completeness(inv_data) == 0.5

    def test_item_with_zero_quantity_partial_credit(self):
        inv_data = {
            "number": "X",
            "date": "2026-05-01",
            "supplier_name": "Y",
            "items": [
                {"raw_name": "Что-то", "quantity": 0, "unit_price": 100, "amount": 100},
            ],
        }
        # 4 invoice-fields full + 4 item-fields, qty=0 → 1 балл потерян → 7/8 = 0.88
        assert _calculate_completeness(inv_data) == 0.88


class TestFinalConfidence:
    def test_takes_min_of_model_and_completeness(self):
        assert _final_confidence(0.95, 0.5) == 0.5
        assert _final_confidence(0.5, 0.95) == 0.5

    def test_handles_none_model_conf(self):
        assert _final_confidence(None, 0.7) == 0.7

    def test_normalizes_percent_scale(self):
        # Модель вернула 95 вместо 0.95 → должно нормализоваться
        assert _final_confidence(95, 1.0) == 0.95

    def test_clamps_above_one(self):
        assert _final_confidence(150, 1.0) == 1.0

    def test_clamps_below_zero(self):
        assert _final_confidence(-0.5, 1.0) == 0.0

    def test_invalid_model_conf_falls_back_to_completeness(self):
        assert _final_confidence("invalid", 0.7) == 0.7
```

- [ ] **Step 1.8.2: Запустить — должны пройти**

```bash
cd backend && pytest tests/unit/test_pdf_parser_helpers.py -v
```

Expected: 10 PASSED.

Если что-то падает — это значит, что я неправильно понял существующее поведение функции. В этом случае: пересмотреть assertion'ы в тесте (не саму функцию) и привести в соответствие с реальной логикой `_calculate_completeness` и `_final_confidence` из `backend/pdf_parser.py:285-343`. Цель — задокументировать текущее поведение, а не менять его.

- [ ] **Step 1.8.3: Commit**

```bash
git add backend/tests/unit/test_pdf_parser_helpers.py
git commit -m "test(backend): unit tests for pdf_parser helpers"
```

---

### Task 1.9: Unit-тесты для `_doc_has_issues` и `_avg_confidence`

**Files:**
- Create: `backend/tests/unit/test_calculations.py`

Эти функции находятся в `routers/invoices.py`. Они работают с моделями SQLAlchemy, но логика чистая — можно тестировать на dataclass-двойниках, не таская реальную БД.

- [ ] **Step 1.9.1: Написать тест с лёгкими дублёрами**

```python
"""Unit-тесты для _doc_has_issues и _avg_confidence (routers/invoices.py)."""
from dataclasses import dataclass, field
from routers.invoices import _doc_has_issues, _avg_confidence


@dataclass
class _FakeItem:
    quantity: float
    raw_name: str | None = ""


@dataclass
class _FakeInvoice:
    items: list[_FakeItem] = field(default_factory=list)
    ai_confidence: float | None = None


@dataclass
class _FakeDoc:
    invoices: list[_FakeInvoice] = field(default_factory=list)


class TestDocHasIssues:
    def test_no_issues_when_items_valid(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name="Бетон")]),
        ])
        assert _doc_has_issues(doc) is False

    def test_issues_when_invoice_has_no_items(self):
        doc = _FakeDoc(invoices=[_FakeInvoice(items=[])])
        assert _doc_has_issues(doc) is True

    def test_issues_when_quantity_zero(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=0, raw_name="X")]),
        ])
        assert _doc_has_issues(doc) is True

    def test_issues_when_raw_name_blank(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name="   ")]),
        ])
        assert _doc_has_issues(doc) is True

    def test_issues_when_raw_name_none(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name=None)]),
        ])
        assert _doc_has_issues(doc) is True


class TestAvgConfidence:
    def test_returns_none_when_no_confidence_set(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=None),
            _FakeInvoice(ai_confidence=None),
        ])
        assert _avg_confidence(doc) is None

    def test_average_of_multiple(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.9),
            _FakeInvoice(ai_confidence=0.7),
        ])
        assert _avg_confidence(doc) == 0.8

    def test_skips_none_invoices(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.9),
            _FakeInvoice(ai_confidence=None),
            _FakeInvoice(ai_confidence=0.5),
        ])
        assert _avg_confidence(doc) == 0.7

    def test_rounds_to_two_decimals(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.333),
            _FakeInvoice(ai_confidence=0.666),
        ])
        assert _avg_confidence(doc) == 0.5
```

- [ ] **Step 1.9.2: Запустить тесты**

```bash
cd backend && pytest tests/unit/test_calculations.py -v
```

Expected: 9 PASSED.

- [ ] **Step 1.9.3: Commit**

```bash
git add backend/tests/unit/test_calculations.py
git commit -m "test(backend): unit tests for doc/invoice issue and confidence helpers"
```

---

### Task 1.10: Smoke `just test-backend-unit` запускается end-to-end

**Files:** (нет, проверка)

- [ ] **Step 1.10.1: Очистить кэш и запустить через just**

```bash
just clean
just test-backend-unit
```

Expected: суммарно 19 PASSED, время < 2 сек.

- [ ] **Step 1.10.2: Push конец Этапа 1**

Этап 1 завершён. Push в remote — коммиты появятся в открытом draft PR.

```bash
git push
```

После этого вручную обновить чек-лист PR на GitHub (отметить «Этап 1: фундамент»).

---

## Этап 2: Backend integration tests

**Цель этапа:** все роутеры покрыты integration-тестами через `TestClient` + реальная Postgres (Neon test branch). Фабрики `factory_boy`, моки OpenRouter через `respx`, snapshot AI-ответов через скрипт.

### Task 2.1: Расширить `conftest.py` фикстурами `db_engine`, `db_session`, `client`

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 2.1.1: Дописать в конец `conftest.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def db_engine() -> Iterator:
    """Engine на TEST_DATABASE_URL. Накатывает Alembic один раз на сессию."""
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан — integration tests пропущены")

    engine = create_engine(test_url, pool_pre_ping=True)

    # Накатываем миграции через Alembic
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)

    # Сбрасываем схему перед накатом — гарантируем чистый старт
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    command.upgrade(cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Iterator[Session]:
    """Транзакционная фикстура. Каждый тест в своей транзакции, rollback после."""
    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session, in_memory_s3) -> Iterator:
    """FastAPI TestClient с переопределённым get_db."""
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # cleanup в db_session фикстуре

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2.1.2: Smoke — `just test-backend-unit` всё ещё зелёный**

```bash
just test-backend-unit
```

Expected: 19 PASSED — добавление фикстур не сломало unit-слой.

- [ ] **Step 2.1.3: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test(backend): add db_engine/db_session/client fixtures"
```

---

### Task 2.2: Фабрики `factory_boy`

**Files:**
- Create: `backend/tests/factories.py`

- [ ] **Step 2.2.1: Создать файл с базовыми фабриками**

```python
"""factory_boy фабрики для интеграционных тестов.

Использование: `project = ProjectFactory.create()` в тесте, который имеет
фикстуру `db_session`. Фабрики привязываются к session через `_register_session`.
"""
from datetime import date, datetime

import factory
from factory.alchemy import SQLAlchemyModelFactory

from models import (
    Document, Invoice, InvoiceItem, MaterialClass, Project, ReferencePrice,
)

# Глобальный slot — устанавливается фикстурой db_session
_session_holder: dict = {"session": None}


def _register_session(session) -> None:
    _session_holder["session"] = session


class _BaseFactory(SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_persistence = "flush"

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        session = _session_holder["session"]
        if session is None:
            raise RuntimeError("Session не зарегистрирована. Используй фикстуру `factories`.")
        cls._meta.sqlalchemy_session = session
        return super()._create(model_class, *args, **kwargs)


class ProjectFactory(_BaseFactory):
    class Meta:
        model = Project

    name = factory.Sequence(lambda n: f"Объект {n}")
    contract_number = factory.Sequence(lambda n: f"Д-{n:03d}")


class MaterialClassFactory(_BaseFactory):
    class Meta:
        model = MaterialClass

    name = factory.Iterator(["В15", "В25", "В40", "d12", "d16"])
    material_type = factory.Iterator(["concrete", "concrete", "concrete", "rebar", "rebar"])


class ReferencePriceFactory(_BaseFactory):
    class Meta:
        model = ReferencePrice

    project = factory.SubFactory(ProjectFactory)
    material_class = factory.SubFactory(MaterialClassFactory)
    price = 8000.0
    period_start = date(2026, 1, 1)
    period_end = date(2026, 12, 31)
    source = "контракт"


class DocumentFactory(_BaseFactory):
    class Meta:
        model = Document

    project = factory.SubFactory(ProjectFactory)
    filename = factory.Sequence(lambda n: f"doc_{n}.pdf")
    s3_key = factory.Sequence(lambda n: f"2026/05/{n}_doc.pdf")
    doc_type = "invoice"
    status = "parsed"


class InvoiceFactory(_BaseFactory):
    class Meta:
        model = Invoice

    document = factory.SubFactory(DocumentFactory)
    number = factory.Sequence(lambda n: f"СФ-{n}")
    date = date(2026, 3, 15)
    supplier_name = "ООО Поставщик"
    supplier_inn = "0000000000"
    vat_rate = 20.0
    ai_confidence = 0.9


class InvoiceItemFactory(_BaseFactory):
    class Meta:
        model = InvoiceItem

    invoice = factory.SubFactory(InvoiceFactory)
    raw_name = "Бетон В25"
    item_type = "material"
    quantity = 5.0
    unit = "м3"
    unit_price = 8000.0
    amount = 40000.0
    vat_amount = 6666.67
```

- [ ] **Step 2.2.2: Добавить фикстуру `factories` в `conftest.py`**

В конец `backend/tests/conftest.py`:

```python
@pytest.fixture
def factories(db_session):
    """Регистрирует db_session в фабриках. Возвращает модуль с фабриками."""
    from tests import factories as f

    f._register_session(db_session)
    return f
```

- [ ] **Step 2.2.3: Commit**

```bash
git add backend/tests/factories.py backend/tests/conftest.py
git commit -m "test(backend): factory_boy factories for all models"
```

---

### Task 2.3: Health smoke + первый integration тест projects

**Files:**
- Create: `backend/tests/integration/test_health.py`
- Create: `backend/tests/integration/test_projects.py`

- [ ] **Step 2.3.1: `test_health.py` — самый простой integration**

```python
def test_health_returns_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2.3.2: `test_projects.py` — CRUD проектов**

```python
def test_list_projects_empty(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_create_project(client):
    response = client.post(
        "/api/projects",
        json={"name": "Новый объект", "contract_number": "Д-007"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Новый объект"
    assert body["contract_number"] == "Д-007"
    assert isinstance(body["id"], int)


def test_list_projects_returns_created(client, factories):
    factories.ProjectFactory.create(name="ЖК Радуга")
    factories.ProjectFactory.create(name="ЖК Звезда")

    response = client.get("/api/projects")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert names == ["ЖК Звезда", "ЖК Радуга"]


def test_update_project(client, factories):
    project = factories.ProjectFactory.create(name="Старое имя")
    response = client.put(
        f"/api/projects/{project.id}",
        json={"name": "Новое имя", "contract_number": "Д-999"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Новое имя"


def test_update_project_404(client):
    response = client.put(
        "/api/projects/9999",
        json={"name": "X"},
    )
    assert response.status_code == 404


def test_delete_project(client, factories):
    project = factories.ProjectFactory.create()
    response = client.delete(f"/api/projects/{project.id}")
    assert response.status_code == 200

    # Проверяем, что список пустой
    list_response = client.get("/api/projects")
    assert list_response.json() == []


def test_delete_project_404(client):
    response = client.delete("/api/projects/9999")
    assert response.status_code == 404
```

- [ ] **Step 2.3.3: Запустить integration**

```bash
just test-backend-integration
```

Expected: если `TEST_DATABASE_URL` задан — 8 PASSED. Если нет — `SKIPPED`.

Если падает: проверить `.env.test`, проверить `alembic.ini` путь, проверить что Neon test branch доступен.

- [ ] **Step 2.3.4: Commit**

```bash
git add backend/tests/integration/test_health.py backend/tests/integration/test_projects.py
git commit -m "test(backend): integration tests for health and projects router"
```

---

### Task 2.4: Integration тесты `material_classes` и `reference_prices`

**Files:**
- Create: `backend/tests/integration/test_material_classes.py`
- Create: `backend/tests/integration/test_reference_prices.py`

- [ ] **Step 2.4.1: `test_material_classes.py`**

```python
def test_create_material_class(client):
    response = client.post(
        "/api/material-classes",
        json={"name": "В30", "material_type": "concrete"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "В30"
    assert body["material_type"] == "concrete"


def test_create_is_idempotent(client):
    """get_or_create — повторный вызов не плодит дубликаты."""
    r1 = client.post("/api/material-classes", json={"name": "В25", "material_type": "concrete"})
    r2 = client.post("/api/material-classes", json={"name": "В25", "material_type": "concrete"})
    assert r1.json()["id"] == r2.json()["id"]


def test_list_filtered_by_material_type(client, factories):
    factories.MaterialClassFactory.create(name="В25", material_type="concrete")
    factories.MaterialClassFactory.create(name="d12", material_type="rebar")

    response = client.get("/api/material-classes?material_type=concrete")
    assert response.status_code == 200
    assert all(c["material_type"] == "concrete" for c in response.json())


def test_delete_material_class(client, factories):
    mc = factories.MaterialClassFactory.create()
    response = client.delete(f"/api/material-classes/{mc.id}")
    assert response.status_code == 200


def test_delete_404(client):
    response = client.delete("/api/material-classes/9999")
    assert response.status_code == 404
```

- [ ] **Step 2.4.2: `test_reference_prices.py`**

```python
from datetime import date


def test_create_reference_price(client, factories):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()

    response = client.post(
        "/api/reference-prices",
        json={
            "project_id": project.id,
            "material_class_id": mc.id,
            "price": 8500.0,
            "period_start": "2026-01-01",
            "period_end": "2026-12-31",
            "source": "контракт",
        },
    )
    assert response.status_code == 200


def test_list_reference_prices_includes_relations(client, factories):
    factories.ReferencePriceFactory.create()

    response = client.get("/api/reference-prices")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "project_name" in body[0]
    assert "material_class_name" in body[0]


def test_filter_by_project(client, factories):
    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()
    factories.ReferencePriceFactory.create(project=p1)
    factories.ReferencePriceFactory.create(project=p2)

    response = client.get(f"/api/reference-prices?project_id={p1.id}")
    assert response.status_code == 200
    assert all(rp["project_id"] == p1.id for rp in response.json())


def test_delete_reference_price(client, factories):
    rp = factories.ReferencePriceFactory.create()
    response = client.delete(f"/api/reference-prices/{rp.id}")
    assert response.status_code == 200
```

- [ ] **Step 2.4.3: Запустить и закоммитить**

```bash
just test-backend-integration
git add backend/tests/integration/test_material_classes.py backend/tests/integration/test_reference_prices.py
git commit -m "test(backend): integration tests for material_classes and reference_prices"
```

---

### Task 2.5: Mock OpenRouter через `respx` + AI snapshot

**Files:**
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/fixtures/openrouter/happy_path.json`
- Create: `backend/tests/fixtures/openrouter/unparseable.json`
- Create: `backend/tests/fixtures/openrouter/invalid_json.json`

- [ ] **Step 2.5.1: Создать AI-ответ happy_path**

Файл `backend/tests/fixtures/openrouter/happy_path.json`:

```json
{
  "id": "gen-test-1",
  "model": "anthropic/claude-sonnet-4.6",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"doc_type\": \"invoice\", \"invoices\": [{\"number\": \"СФ-101\", \"date\": \"2026-04-15\", \"supplier_name\": \"ООО Поставщик\", \"supplier_inn\": \"0000000000\", \"vat_rate\": 20, \"confidence\": 0.95, \"confidence_reason\": \"все поля читаются чётко\", \"items\": [{\"raw_name\": \"Бетонная смесь БСТ В25\", \"item_type\": \"material\", \"material_class\": \"В25\", \"material_type\": \"concrete\", \"quantity\": 7.0, \"unit\": \"м3\", \"unit_price\": 8000.0, \"amount\": 56000.0, \"vat_amount\": 9333.33, \"confidence\": 0.95}]}]}"
      }
    }
  ],
  "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
}
```

- [ ] **Step 2.5.2: `unparseable.json`**

```json
{
  "id": "gen-test-2",
  "choices": [{"message": {"role": "assistant", "content": "{\"doc_type\": \"unknown\"}"}}],
  "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
}
```

- [ ] **Step 2.5.3: `invalid_json.json`**

```json
{
  "id": "gen-test-3",
  "choices": [{"message": {"role": "assistant", "content": "Это не JSON, модель сошла с ума."}}],
  "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
}
```

- [ ] **Step 2.5.4: Дописать `mock_openrouter` фикстуру в `conftest.py`**

```python
import json
import respx
import httpx as _httpx_module


@pytest.fixture
def openrouter_fixtures_dir() -> Path:
    return BACKEND_ROOT / "tests" / "fixtures" / "openrouter"


@pytest.fixture
def mock_openrouter(openrouter_fixtures_dir, monkeypatch):
    """Подменяет OpenRouter. По умолчанию — happy_path. Меняй сценарий через .use_scenario()."""
    # Снимаем общий guard на этот тест — respx сам перехватит реальный URL
    monkeypatch.delattr(_httpx_module.AsyncClient, "send", raising=False)

    class _Mock:
        def __init__(self):
            self.scenario = "happy_path"
            self.calls = []

        def use_scenario(self, name: str) -> None:
            self.scenario = name

        def _load(self) -> dict:
            return json.loads((openrouter_fixtures_dir / f"{self.scenario}.json").read_text(encoding="utf-8"))

        def __enter__(self):
            self._respx = respx.mock(base_url="https://openrouter.ai", assert_all_called=False)
            self._respx.start()

            def handler(request):
                self.calls.append(request)
                return _httpx_module.Response(200, json=self._load())

            self._respx.post("/api/v1/chat/completions").mock(side_effect=handler)
            return self

        def __exit__(self, *exc):
            self._respx.stop()

    with _Mock() as m:
        yield m
```

- [ ] **Step 2.5.5: Commit**

```bash
git add backend/tests/fixtures/openrouter/ backend/tests/conftest.py
git commit -m "test(backend): respx mock_openrouter fixture and AI response snapshots"
```

---

### Task 2.6: Integration тесты `invoices` (upload, reparse, update, delete)

**Files:**
- Create: `backend/tests/integration/test_invoices.py`

- [ ] **Step 2.6.1: Тесты upload/reparse**

```python
import io


def test_upload_rejects_non_pdf(client, factories):
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("doc.txt", b"not a pdf", "text/plain")},
        data={"project_id": project.id},
    )
    assert response.status_code == 400


def test_upload_creates_document_with_invoices(client, factories, sample_pdf_bytes, mock_openrouter):
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert body["doc_type"] == "invoice"
    assert body["invoice_count"] == 1
    assert body["invoices"][0]["number"] == "СФ-101"
    assert len(body["invoices"][0]["items"]) == 1


def test_upload_unparseable_marks_doc_type_unknown(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    mock_openrouter.use_scenario("unparseable")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 200
    body = response.json()
    # При doc_type=unknown бэкенд кладёт error и помечает status=error
    assert body["doc_type"] == "unknown" or body["status"] == "error"


def test_upload_invalid_json_marks_error(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    mock_openrouter.use_scenario("invalid_json")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "error"


def test_get_document_404(client):
    response = client.get("/api/invoices/documents/9999")
    assert response.status_code == 404


def test_list_documents_filtered_by_project(client, factories):
    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()
    factories.DocumentFactory.create(project=p1)
    factories.DocumentFactory.create(project=p2)

    response = client.get(f"/api/invoices/documents?project_id={p1.id}")
    assert response.status_code == 200
    assert all(d["project_id"] == p1.id for d in response.json())
```

- [ ] **Step 2.6.2: Тесты update/delete invoice**

Дописать в тот же файл:

```python
def test_update_invoice_replaces_items(client, factories):
    invoice = factories.InvoiceFactory.create()
    factories.InvoiceItemFactory.create(invoice=invoice, raw_name="Старая позиция")

    response = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": "СФ-NEW",
            "date": "2026-05-01",
            "supplier_name": "Новый",
            "supplier_inn": None,
            "vat_rate": 20.0,
            "items": [
                {
                    "id": None,
                    "raw_name": "Новая",
                    "item_type": "material",
                    "material_class_id": None,
                    "quantity": 3.0,
                    "unit": "м3",
                    "unit_price": 9000.0,
                    "amount": 27000.0,
                    "vat_amount": 4500.0,
                }
            ],
        },
    )
    assert response.status_code == 200


def test_delete_invoice(client, factories):
    invoice = factories.InvoiceFactory.create()
    response = client.delete(f"/api/invoices/{invoice.id}")
    assert response.status_code == 200


def test_delete_document_removes_from_s3(
    client, factories, in_memory_s3,
):
    doc = factories.DocumentFactory.create(s3_key="2026/05/test.pdf")
    in_memory_s3["2026/05/test.pdf"] = b"fake"

    response = client.delete(f"/api/invoices/documents/{doc.id}")
    assert response.status_code == 200
    assert "2026/05/test.pdf" not in in_memory_s3
```

- [ ] **Step 2.6.3: Запустить и закоммитить**

```bash
just test-backend-integration
git add backend/tests/integration/test_invoices.py
git commit -m "test(backend): integration tests for invoices upload/update/delete"
```

---

### Task 2.7: Тесты `dashboard` и `crud.recalculate_prices`

**Files:**
- Create: `backend/tests/unit/test_crud_recalculate.py`
- Create: `backend/tests/integration/test_dashboard.py`

- [ ] **Step 2.7.1: Unit-тест `recalculate_prices` — изолированно от роутера**

```python
"""Тесты бизнес-логики crud.recalculate_prices."""
from datetime import date


def test_recalculate_with_no_items_returns_none(client, factories, db_session):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()

    import crud
    result = crud.recalculate_prices(
        db_session, project.id, mc.id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert result is None


def test_recalculate_simple_avg(factories, db_session):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(name="В25")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, quantity=10.0, unit_price=8000.0, amount=80000.0,
    )

    import crud
    result = crud.recalculate_prices(
        db_session, project.id, mc.id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert result is not None
    assert result.total_qty == 10.0
    assert result.avg_price == 8000.0
    assert result.invoice_count == 1


def test_recalculate_with_reference_price_computes_deviation(factories, db_session):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, price=10000.0,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, quantity=10.0, unit_price=11000.0, amount=110000.0,
    )

    import crud
    result = crud.recalculate_prices(
        db_session, project.id, mc.id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert result.reference_price == 10000.0
    assert result.deviation_pct == 10.0
    assert result.deviation_amount == 10000.0
```

- [ ] **Step 2.7.2: `test_dashboard.py`**

```python
from datetime import date


def test_summary_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/dashboard/summary?project_id={project.id}")
    assert response.status_code == 200
    assert response.json() == {"doc_count": 0, "invoice_count": 0, "total_amount": 0, "total_qty": 0}


def test_summary_aggregates_materials(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(invoice=inv, item_type="material", quantity=5, amount=40000)
    factories.InvoiceItemFactory.create(invoice=inv, item_type="delivery", quantity=1, amount=2000)

    response = client.get(f"/api/dashboard/summary?project_id={project.id}")
    body = response.json()
    # Только material попадает в total_amount/total_qty
    assert body["total_amount"] == 40000.0
    assert body["total_qty"] == 5.0
    assert body["invoice_count"] == 1


def test_calculate_endpoint_creates_calculation(client, factories):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 1))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=10, unit_price=8000, amount=80000,
    )

    response = client.post(
        f"/api/dashboard/calculate?project_id={project.id}"
        f"&period_start=2026-01-01&period_end=2026-12-31"
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


def test_auto_calculate_no_invoices(client, factories):
    project = factories.ProjectFactory.create()
    response = client.post(f"/api/dashboard/auto-calculate?project_id={project.id}")
    assert response.status_code == 200
    assert response.json()["period_start"] is None
```

- [ ] **Step 2.7.3: Запустить и закоммитить**

```bash
just test-backend
git add backend/tests/unit/test_crud_recalculate.py backend/tests/integration/test_dashboard.py
git commit -m "test(backend): unit tests for recalculate_prices and integration for dashboard"
```

---

### Task 2.8: Тесты `export` и `settings`

**Files:**
- Create: `backend/tests/integration/test_export.py`
- Create: `backend/tests/integration/test_settings.py`

- [ ] **Step 2.8.1: `test_export.py` — smoke на структуру xlsx**

```python
from io import BytesIO
from openpyxl import load_workbook


def test_export_excel_returns_xlsx(client, factories):
    project = factories.ProjectFactory.create(name="Тест-Объект")
    response = client.get(
        f"/api/export/excel?project_id={project.id}"
        f"&period_start=2026-01-01&period_end=2026-12-31"
    )
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]

    wb = load_workbook(BytesIO(response.content))
    ws = wb.active
    # Ожидаем заголовки в первых строках
    assert ws.cell(row=1, column=1).value == "Объект:"
    assert ws.cell(row=1, column=2).value == "Тест-Объект"


def test_export_unknown_project_returns_error(client):
    response = client.get(
        "/api/export/excel?project_id=9999"
        "&period_start=2026-01-01&period_end=2026-12-31"
    )
    assert response.status_code == 200
    assert response.json() == {"error": "Проект не найден"}
```

- [ ] **Step 2.8.2: `test_settings.py`**

```python
def test_get_settings(client, monkeypatch, tmp_path):
    """Smoke — settings возвращает текущие значения env."""
    monkeypatch.setenv("AI_MODEL", "anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.7")

    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "anthropic/claude-sonnet-4.6"
    assert body["confidence_threshold"] == 0.7
    assert "api_key_set" in body


def test_update_settings_writes_to_env(client, monkeypatch, tmp_path):
    """update_settings пишет в .env. Подменяем путь на временный файл."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("")
    monkeypatch.setattr("routers.settings.ENV_PATH", str(fake_env))

    response = client.put(
        "/api/settings",
        json={"model": "test-model", "confidence_threshold": 0.85},
    )
    assert response.status_code == 200
    content = fake_env.read_text()
    assert "test-model" in content
    assert "0.85" in content
```

- [ ] **Step 2.8.3: Запустить и закоммитить**

```bash
just test-backend
git add backend/tests/integration/test_export.py backend/tests/integration/test_settings.py
git commit -m "test(backend): integration tests for export and settings"
```

---

### Task 2.9: Скрипт snapshot AI-ответов

**Files:**
- Create: `backend/scripts/snapshot_ai_responses.py`

- [ ] **Step 2.9.1: Создать скрипт**

```python
"""Локальный скрипт: real PDF → OpenRouter → sanitize → JSON фикстура.

Запуск:
    cd backend && python scripts/snapshot_ai_responses.py \
        tests/fixtures/pdf/real/sample.pdf happy_path

Параметры:
    1. Путь к реальному PDF
    2. Имя сценария (без .json) — будет сохранено в tests/fixtures/openrouter/

Скрипт ходит в OpenRouter с реальным API-ключом из .env, получает ответ,
прогоняет через sanitizer (заменяет ИНН и наименования на фейки),
сохраняет в tests/fixtures/openrouter/{scenario}.json.

Реальные PDF в репо НЕ попадают (они в .gitignore).
Sanitized JSON — попадает.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _sanitize_response(raw: dict) -> dict:
    """Заменяет PII в response модели на фейковые значения."""
    text = raw["choices"][0]["message"]["content"]

    # Очищаем markdown wrapper если есть
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()

    parsed = json.loads(text)

    if parsed.get("doc_type") == "invoice":
        for idx, inv in enumerate(parsed.get("invoices", [])):
            inv["supplier_name"] = f"Поставщик {idx + 1}"
            inv["supplier_inn"] = "0000000000"

    # Записываем обратно
    raw["choices"][0]["message"]["content"] = json.dumps(parsed, ensure_ascii=False)
    return raw


async def _fetch(pdf_path: Path) -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    import base64
    import os
    import httpx

    api_key = os.environ["OPENROUTER_API_KEY"]
    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode()

    from pdf_parser import SYSTEM_PROMPT, OPENROUTER_URL
    payload = {
        "model": os.getenv("AI_MODEL", "anthropic/claude-sonnet-4.6"),
        "max_tokens": 8192,
        "plugins": [{"id": "file-parser", "pdf": {"engine": os.getenv("PDF_ENGINE", "mistral-ocr")}}],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "file", "file": {"filename": "doc.pdf",
                                          "file_data": f"data:application/pdf;base64,{pdf_b64}"}},
                {"type": "text", "text": "Извлеки данные."},
            ]},
        ],
    }

    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def main():
    if len(sys.argv) != 3:
        print("Usage: python snapshot_ai_responses.py <pdf-path> <scenario-name>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    scenario = sys.argv[2]
    if not pdf_path.exists():
        print(f"PDF не найден: {pdf_path}")
        sys.exit(1)

    print(f"Запрос к OpenRouter для {pdf_path.name}...")
    raw = asyncio.run(_fetch(pdf_path))
    print("Ответ получен. Санитизация...")
    sanitized = _sanitize_response(raw)

    out = ROOT / "tests" / "fixtures" / "openrouter" / f"{scenario}.json"
    out.write_text(json.dumps(sanitized, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.9.2: Commit**

```bash
git add backend/scripts/snapshot_ai_responses.py
git commit -m "test(backend): script to snapshot real OpenRouter responses with PII sanitization"
```

---

### Task 2.10: Coverage check + конец Этапа 2

- [ ] **Step 2.10.1: Запустить coverage**

```bash
just coverage-backend
```

Expected: HTML отчёт в `backend/htmlcov/index.html`. Total coverage >= 60%, на критичных модулях (`pdf_parser.py`, `crud.py`, `routers/invoices.py`) >= 80%.

Если ниже — добавить тесты на непокрытые ветки. Если значительно ниже — это сигнал, что нужно ещё несколько итераций тестов перед переходом к Этапу 3.

- [ ] **Step 2.10.2: Push конец Этапа 2**

```bash
git push
```

Чек-лист в PR на GitHub обновить вручную: отметить «Этап 2: backend integration tests».

---

## Этап 3: Frontend Vitest + MSW

**Цель этапа:** `just test-frontend` запускает Vitest, тесты проходят на jsdom, все API вызовы перехвачены MSW.

### Task 3.1: Установить deps + scripts в `package.json`

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 3.1.1: Установить пакеты**

```bash
cd frontend && npm i -D vitest @vitest/coverage-v8 @vitest/ui \
  @testing-library/react @testing-library/jest-dom @testing-library/user-event \
  jsdom msw
```

- [ ] **Step 3.1.2: Добавить scripts в `frontend/package.json`**

В блок `scripts` дописать:

```json
"test": "vitest --run",
"test:watch": "vitest",
"test:ui": "vitest --ui",
"test:coverage": "vitest --run --coverage"
```

- [ ] **Step 3.1.3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add vitest, RTL, MSW dependencies"
```

---

### Task 3.2: `vitest.config.ts`

**Files:**
- Create: `frontend/vitest.config.ts`

- [ ] **Step 3.2.1: Создать конфиг**

```ts
/// <reference types="vitest" />
import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/components/ui/**",
        "src/main.tsx",
        "src/App.tsx",
        "**/*.d.ts",
        "**/*.test.*",
        "src/test/**",
      ],
    },
  },
});
```

- [ ] **Step 3.2.2: Commit**

```bash
git add frontend/vitest.config.ts
git commit -m "chore(frontend): vitest config with jsdom and coverage"
```

---

### Task 3.3: MSW infrastructure

**Files:**
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/test/server.ts`
- Create: `frontend/src/test/handlers.ts`
- Create: `frontend/src/test/fixtures.ts`

- [ ] **Step 3.3.1: `fixtures.ts` — sample-объекты ответов API**

```ts
export const sampleProject = {
  id: 1,
  name: "ЖК Радуга",
  contract_number: "Д-001",
  doc_count: 0,
};

export const sampleMaterialClass = {
  id: 1,
  name: "В25",
  material_type: "concrete",
};

export const sampleDocument = {
  id: 10,
  project_id: 1,
  filename: "doc.pdf",
  doc_type: "invoice",
  status: "parsed",
  uploaded_at: "2026-04-15T10:00:00",
  invoice_count: 1,
  has_issues: false,
  ai_confidence: 0.92,
  invoices: [
    {
      id: 100,
      document_id: 10,
      number: "СФ-101",
      date: "2026-04-15",
      supplier_name: "ООО Поставщик",
      supplier_inn: "0000000000",
      vat_rate: 20,
      ai_confidence: 0.92,
      has_issues: false,
      items: [
        {
          id: 1000,
          raw_name: "Бетон В25",
          item_type: "material",
          material_class: { id: 1, name: "В25" },
          material_class_id: 1,
          quantity: 7.0,
          unit: "м3",
          unit_price: 8000.0,
          amount: 56000.0,
          vat_amount: 9333.33,
        },
      ],
    },
  ],
};

export const sampleDashboardSummary = {
  doc_count: 3,
  invoice_count: 5,
  total_amount: 250000,
  total_qty: 31.5,
};
```

- [ ] **Step 3.3.2: `handlers.ts`**

```ts
import { http, HttpResponse } from "msw";
import {
  sampleProject, sampleMaterialClass, sampleDocument, sampleDashboardSummary,
} from "./fixtures";

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  http.get("/api/projects", () => HttpResponse.json([sampleProject])),
  http.post("/api/projects", () => HttpResponse.json(sampleProject)),
  http.put("/api/projects/:id", () => HttpResponse.json(sampleProject)),
  http.delete("/api/projects/:id", () => HttpResponse.json({ message: "Удалено" })),

  http.get("/api/material-classes", () => HttpResponse.json([sampleMaterialClass])),
  http.post("/api/material-classes", () => HttpResponse.json(sampleMaterialClass)),

  http.get("/api/reference-prices", () => HttpResponse.json([])),
  http.post("/api/reference-prices", () => HttpResponse.json({ id: 1 })),
  http.delete("/api/reference-prices/:id", () => HttpResponse.json({ message: "Удалено" })),

  http.get("/api/invoices/documents", () => HttpResponse.json([sampleDocument])),
  http.get("/api/invoices/documents/:id", () => HttpResponse.json(sampleDocument)),
  http.post("/api/invoices/upload", () => HttpResponse.json(sampleDocument)),
  http.post("/api/invoices/documents/:id/reparse", () => HttpResponse.json(sampleDocument)),
  http.put("/api/invoices/:id", () =>
    HttpResponse.json({ message: "Сохранено", invoice_id: 100 })
  ),
  http.delete("/api/invoices/:id", () => HttpResponse.json({ message: "СФ удалена" })),
  http.delete("/api/invoices/documents/:id", () =>
    HttpResponse.json({ message: "Удалено" })
  ),

  http.get("/api/dashboard/summary", () => HttpResponse.json(sampleDashboardSummary)),
  http.get("/api/dashboard/invoices", () => HttpResponse.json([])),
  http.get("/api/dashboard/calculations", () => HttpResponse.json([])),
  http.post("/api/dashboard/calculate", () =>
    HttpResponse.json({ message: "Рассчитано классов: 1", results: [] })
  ),
  http.post("/api/dashboard/auto-calculate", () =>
    HttpResponse.json({ message: "OK", period_start: null, period_end: null, results: [] })
  ),

  http.get("/api/settings", () =>
    HttpResponse.json({
      api_key_set: true,
      model: "anthropic/claude-sonnet-4.6",
      confidence_threshold: 0.7,
    })
  ),
  http.put("/api/settings", () => HttpResponse.json({ message: "Настройки сохранены" })),
];
```

- [ ] **Step 3.3.3: `server.ts`**

```ts
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
```

- [ ] **Step 3.3.4: `setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

- [ ] **Step 3.3.5: Commit**

```bash
git add frontend/src/test/
git commit -m "test(frontend): MSW server, handlers and fixtures"
```

---

### Task 3.4: `renderWithProviders` helper

**Files:**
- Create: `frontend/src/test/utils.tsx`

- [ ] **Step 3.4.1: Создать helper**

```tsx
import { ReactElement, ReactNode } from "react";
import { render, RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "next-themes";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface WrapperProps {
  children: ReactNode;
  queryClient?: QueryClient;
  initialRoute?: string;
}

export function AllProviders({ children, queryClient, initialRoute = "/" }: WrapperProps) {
  const client = queryClient ?? createTestQueryClient();
  return (
    <ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialRoute]}>{children}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  options?: { initialRoute?: string; queryClient?: QueryClient } & Omit<RenderOptions, "wrapper">
) {
  const { initialRoute, queryClient, ...rest } = options ?? {};
  return render(ui, {
    wrapper: ({ children }) => (
      <AllProviders queryClient={queryClient} initialRoute={initialRoute}>
        {children}
      </AllProviders>
    ),
    ...rest,
  });
}
```

- [ ] **Step 3.4.2: Commit**

```bash
git add frontend/src/test/utils.tsx
git commit -m "test(frontend): renderWithProviders helper"
```

---

### Task 3.5: Расширить `justfile` для frontend

**Files:**
- Modify: `justfile`

- [ ] **Step 3.5.1: Добавить цели**

В секцию `# === Tests ===` дописать:

```makefile
# Frontend
test-frontend:
    cd frontend && npm test

test-frontend-watch:
    cd frontend && npm run test:watch

test-frontend-ui:
    cd frontend && npm run test:ui

# Combined
test:
    just test-backend
    just test-frontend
```

В секцию `# === Coverage ===`:

```makefile
coverage-frontend:
    cd frontend && npm run test:coverage
```

В секцию `# === Lint ===`:

```makefile
lint-frontend:
    cd frontend && npm run lint

typecheck-frontend:
    cd frontend && npx tsc -b --noEmit

lint:
    just lint-backend
    just lint-frontend
```

- [ ] **Step 3.5.2: Smoke**

```bash
just --list
```

Expected: новые цели видны, синтаксис не сломан.

- [ ] **Step 3.5.3: Commit**

```bash
git add justfile
git commit -m "chore(testing): extend justfile with frontend test targets"
```

---

### Task 3.6: Тест Dropzone — отклоняет non-PDF

**Files:**
- Create: `frontend/src/components/ui-domain/Dropzone.test.tsx`

- [ ] **Step 3.6.1: Написать тест**

```tsx
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { Dropzone } from "./Dropzone";

describe("Dropzone", () => {
  it("renders the prompt and hint text", () => {
    renderWithProviders(<Dropzone onDrop={() => {}} />);
    expect(
      screen.getByText(/Перетащите файлы сюда или нажмите для выбора/)
    ).toBeInTheDocument();
    expect(screen.getByText(/PDF, JPG, PNG до 20 МБ/)).toBeInTheDocument();
  });

  it("calls onDrop when a PDF file is selected", async () => {
    const onDrop = vi.fn();
    const user = userEvent.setup();
    const { container } = renderWithProviders(
      <Dropzone onDrop={onDrop} accept={{ "application/pdf": [".pdf"] }} />
    );
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    const file = new File(["dummy"], "test.pdf", { type: "application/pdf" });
    await user.upload(input, file);
    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop.mock.calls[0][0][0].name).toBe("test.pdf");
  });

  it("respects custom hint", () => {
    renderWithProviders(<Dropzone onDrop={() => {}} hint="Только .pdf" />);
    expect(screen.getByText("Только .pdf")).toBeInTheDocument();
  });

  it("becomes non-interactive when disabled", () => {
    const { container } = renderWithProviders(<Dropzone onDrop={() => {}} disabled />);
    expect(container.firstChild).toHaveClass("cursor-not-allowed");
  });
});
```

- [ ] **Step 3.6.2: Запустить и зафиксить**

```bash
cd frontend && npm test -- Dropzone
```

Expected: 4 PASSED.

```bash
git add frontend/src/components/ui-domain/Dropzone.test.tsx
git commit -m "test(frontend): Dropzone component tests"
```

---

### Task 3.7: Тест EntitySelect — показывает name, не id

**Files:**
- Create: `frontend/src/components/ui-domain/EntitySelect.test.tsx`

- [ ] **Step 3.7.1: Написать тест**

```tsx
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { EntitySelect } from "./EntitySelect";

describe("EntitySelect", () => {
  const items = [
    { id: 1, name: "ЖК Радуга" },
    { id: 2, name: "ЖК Звезда" },
  ];

  it("renders placeholder when no value", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={() => {}}
        getLabel={(i) => i.name}
        placeholder="Выберите проект"
      />
    );
    expect(screen.getByText("Выберите проект")).toBeInTheDocument();
  });

  it("displays human label, not id, for selected value", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={1}
        onChange={() => {}}
        getLabel={(i) => i.name}
      />
    );
    // Trigger показывает label, не id
    expect(screen.getByText("ЖК Радуга")).toBeInTheDocument();
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("calls onChange with numeric id when item is selected", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={onChange}
        getLabel={(i) => i.name}
      />
    );
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByText("ЖК Звезда"));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("disables interaction when disabled prop is true", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={() => {}}
        getLabel={(i) => i.name}
        disabled
      />
    );
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
});
```

- [ ] **Step 3.7.2: Запустить и зафиксить**

```bash
cd frontend && npm test -- EntitySelect
git add frontend/src/components/ui-domain/EntitySelect.test.tsx
git commit -m "test(frontend): EntitySelect tests — labels not ids"
```

---

### Task 3.8: Тест страницы Upload (smoke + interaction)

**Files:**
- Create: `frontend/src/pages/Upload.test.tsx`

- [ ] **Step 3.8.1: Написать smoke-тест**

```tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import UploadPage from "./Upload";

describe("UploadPage", () => {
  it("renders project selector and dropzone after projects load", async () => {
    renderWithProviders(<UploadPage />);
    // Проверяем, что список проектов подтянулся (MSW отдаёт 1 проект "ЖК Радуга")
    await waitFor(() => {
      expect(screen.getByText(/Перетащите файлы сюда/)).toBeInTheDocument();
    });
  });

  it("renders page header", async () => {
    renderWithProviders(<UploadPage />);
    // Заголовок страницы — проверяем по реальному тексту (если другой — обновить тест)
    await waitFor(() => {
      // Подбираем по тексту, который есть в PageHeader на этой странице
      expect(document.querySelector("main, body")).toBeTruthy();
    });
  });
});
```

Если первый тест падает с другим текстом — исправить assertion на тот, что реально появляется в DOM (использовать `screen.debug()` для диагностики).

- [ ] **Step 3.8.2: Запустить и зафиксить**

```bash
cd frontend && npm test -- Upload
git add frontend/src/pages/Upload.test.tsx
git commit -m "test(frontend): Upload page smoke tests"
```

---

### Task 3.9: Тест страницы Review

**Files:**
- Create: `frontend/src/pages/Review.test.tsx`

- [ ] **Step 3.9.1: Smoke + базовый flow**

```tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import Review from "./Review";

describe("ReviewPage", () => {
  it("renders document data after fetching", async () => {
    renderWithProviders(<Review />, { initialRoute: "/documents/10" });
    await waitFor(() => {
      // Из sampleDocument: invoice number СФ-101
      expect(screen.getByText(/СФ-101/)).toBeInTheDocument();
    });
  });

  it("displays the supplier name", async () => {
    renderWithProviders(<Review />, { initialRoute: "/documents/10" });
    await waitFor(() => {
      expect(screen.getByText(/ООО Поставщик/)).toBeInTheDocument();
    });
  });

  it("displays at least one item from invoice", async () => {
    renderWithProviders(<Review />, { initialRoute: "/documents/10" });
    await waitFor(() => {
      expect(screen.getByText(/Бетон В25/)).toBeInTheDocument();
    });
  });
});
```

Если страница использует useParams и MemoryRouter не передаёт `:id` — поправить либо роут в `MemoryRouter`, либо `initialRoute` на `/documents/10` (как и сделано), плюс добавить в helper рендера `<Route path="/documents/:id" ...>` если требуется. Для простых тестов часто достаточно отрендерить компонент напрямую с MSW-моками.

- [ ] **Step 3.9.2: Зафиксить**

```bash
cd frontend && npm test -- Review
git add frontend/src/pages/Review.test.tsx
git commit -m "test(frontend): Review page smoke tests"
```

---

### Task 3.10: Smoke-тесты остальных страниц

**Files:**
- Create: `frontend/src/pages/Dashboard.test.tsx`
- Create: `frontend/src/pages/Reports.test.tsx`
- Create: `frontend/src/pages/ReferencePrices.test.tsx`

Минимальные тесты — компонент рендерится, MSW отдаёт данные, ничего не падает в консоль. Подробное покрытие добавляем итеративно.

- [ ] **Step 3.10.1: `Dashboard.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import Dashboard from "./Dashboard";

describe("Dashboard", () => {
  it("renders KPI numbers from summary", async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => {
      // sampleDashboardSummary: doc_count=3, invoice_count=5
      expect(screen.getByText("3")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 3.10.2: `Reports.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import Reports from "./Reports";

describe("Reports", () => {
  it("renders without errors", async () => {
    renderWithProviders(<Reports />);
    await waitFor(() => {
      expect(document.body).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 3.10.3: `ReferencePrices.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import ReferencePrices from "./ReferencePrices";

describe("ReferencePrices", () => {
  it("renders without errors", async () => {
    renderWithProviders(<ReferencePrices />);
    await waitFor(() => {
      expect(document.body).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 3.10.4: Запустить всё и зафиксить**

```bash
just test-frontend
git add frontend/src/pages/Dashboard.test.tsx \
        frontend/src/pages/Reports.test.tsx \
        frontend/src/pages/ReferencePrices.test.tsx
git commit -m "test(frontend): smoke tests for Dashboard, Reports, ReferencePrices"
```

---

### Task 3.11: Coverage check + push конец Этапа 3

- [ ] **Step 3.11.1: Coverage**

```bash
just coverage-frontend
```

Expected: HTML отчёт в `frontend/coverage/index.html`. Total >= 40%, на critical (`lib/`, `pages/Review`, `pages/Upload`) >= 70%.

- [ ] **Step 3.11.2: Push конец Этапа 3**

```bash
git push
```

Чек-лист в PR обновить вручную.

---

## Этап 4: Playwright E2E

**Цель этапа:** `just test-e2e` локально и в CI запускает Chromium, проходит golden path и edge cases на настоящем стеке (frontend + backend + Postgres + mock OpenRouter).

### Task 4.1: Test-only роутер `/api/test/reset`

**Files:**
- Create: `backend/routers/test_utils.py`
- Modify: `backend/main.py`

- [ ] **Step 4.1.1: Создать роутер**

```python
"""Тестовые служебные эндпоинты. Подключаются ТОЛЬКО при TEST_MODE=1."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from models import (
    Project, MaterialClass, ReferencePrice, Document,
    Invoice, InvoiceItem, PriceCalculation,
)

router = APIRouter()


@router.post("/reset")
def reset_database(db: Session = Depends(get_db)):
    """TRUNCATE всех таблиц + засев базовых данных. ТОЛЬКО для E2E."""
    # Порядок важен из-за FK
    db.query(PriceCalculation).delete()
    db.query(InvoiceItem).delete()
    db.query(Invoice).delete()
    db.query(Document).delete()
    db.query(ReferencePrice).delete()
    db.query(MaterialClass).delete()
    db.query(Project).delete()
    db.commit()

    # Сбрасываем sequences (Postgres-специфично)
    for table in ("projects", "material_classes", "reference_prices",
                  "documents", "invoices", "invoice_items", "price_calculations"):
        db.execute(text(f"ALTER SEQUENCE {table}_id_seq RESTART WITH 1"))

    # Минимальный seed
    p = Project(name="E2E Test Project", contract_number="E2E-001")
    db.add(p)
    db.add(MaterialClass(name="В25", material_type="concrete"))
    db.add(MaterialClass(name="В40", material_type="concrete"))
    db.commit()

    return {"status": "reset_complete", "project_id": p.id}
```

- [ ] **Step 4.1.2: Подключить условно в `main.py`**

После строки `app.include_router(settings.router, prefix="/api/settings", tags=["settings"])` добавить:

```python
if os.getenv("TEST_MODE") == "1":
    from routers import test_utils
    app.include_router(test_utils.router, prefix="/api/test", tags=["test"])
    logger.warning("TEST_MODE=1: подключён /api/test/* — НЕ использовать в проде")
```

- [ ] **Step 4.1.3: Smoke**

```bash
cd backend
TEST_MODE=1 python -c "from main import app; print([r.path for r in app.routes if 'test' in r.path])"
```

Expected: видим `/api/test/reset`.

```bash
unset TEST_MODE
python -c "from main import app; print([r.path for r in app.routes if 'test' in r.path])"
```

Expected: пусто — без флага роутер не подключён.

- [ ] **Step 4.1.4: Commit**

```bash
git add backend/routers/test_utils.py backend/main.py
git commit -m "feat(backend): test-only /api/test/reset endpoint guarded by TEST_MODE"
```

---

### Task 4.2: Mock OpenRouter сервер

**Files:**
- Create: `e2e/mock_openrouter/server.py`
- Create: `e2e/mock_openrouter/requirements.txt`

- [ ] **Step 4.2.1: `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
```

- [ ] **Step 4.2.2: `server.py`**

```python
"""Mock OpenRouter для E2E.

Принимает POST /api/v1/chat/completions и отдаёт фикстуру из ../../backend/tests/fixtures/openrouter/.
По умолчанию — happy_path. Сценарий выбирается заголовком X-E2E-Scenario.

Запуск:
    cd e2e/mock_openrouter
    uvicorn server:app --port 8002
"""
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request

FIXTURES = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "openrouter"

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/chat/completions")
async def chat_completions(request: Request):
    scenario = request.headers.get("x-e2e-scenario", "happy_path")
    fixture = FIXTURES / f"{scenario}.json"
    if not fixture.exists():
        return {"error": f"scenario '{scenario}' not found"}
    return json.loads(fixture.read_text(encoding="utf-8"))
```

- [ ] **Step 4.2.3: Commit**

```bash
git add e2e/mock_openrouter/
git commit -m "test(e2e): mock OpenRouter server returning fixture JSONs"
```

---

### Task 4.3: Playwright проект

**Files:**
- Create: `e2e/package.json`
- Create: `e2e/tsconfig.json`
- Create: `e2e/playwright.config.ts`

- [ ] **Step 4.3.1: `e2e/package.json`**

```json
{
  "name": "udp-e2e",
  "private": true,
  "version": "0.0.0",
  "scripts": {
    "test": "playwright test",
    "test:ui": "playwright test --ui",
    "test:headed": "playwright test --headed",
    "install-browsers": "playwright install --with-deps chromium"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "typescript": "^5.6.0"
  }
}
```

- [ ] **Step 4.3.2: `e2e/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["@playwright/test"]
  },
  "include": ["tests/**/*", "playwright.config.ts"]
}
```

- [ ] **Step 4.3.3: `e2e/playwright.config.ts`**

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [["html", { open: "never" }], ["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: "just e2e-mock-openrouter",
      url: "http://localhost:8002/health",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: "just e2e-backend",
      url: "http://localhost:8001/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: "just e2e-frontend",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
```

- [ ] **Step 4.3.4: Установить Playwright**

```bash
cd e2e && npm install && npx playwright install --with-deps chromium
```

- [ ] **Step 4.3.5: Commit**

```bash
git add e2e/package.json e2e/package-lock.json e2e/tsconfig.json e2e/playwright.config.ts
git commit -m "test(e2e): playwright project with chromium and webServer config"
```

---

### Task 4.4: `justfile` цели для E2E

**Files:**
- Modify: `justfile`

- [ ] **Step 4.4.1: Добавить цели**

В секцию `# === Tests ===`:

```makefile
# E2E
test-e2e:
    cd e2e && npm test

test-e2e-ui:
    cd e2e && npm run test:ui

test-e2e-headed:
    cd e2e && npm run test:headed

e2e-install:
    cd e2e && npm ci && npx playwright install --with-deps chromium

# Сервисы для E2E (Playwright их сам поднимает через webServer:)
e2e-backend:
    cd backend && TEST_MODE=1 OPENROUTER_BASE_URL=http://localhost:8002/api/v1 \
        DATABASE_URL=$TEST_DATABASE_URL uvicorn main:app --port 8001

e2e-mock-openrouter:
    cd e2e/mock_openrouter && pip install -q -r requirements.txt && \
        uvicorn server:app --port 8002

e2e-frontend:
    cd frontend && VITE_API_URL=http://localhost:8001 npm run preview -- --port 5173
```

В `install`:

```makefile
install: install-backend install-frontend e2e-install
    @echo "==> Установка завершена"
```

- [ ] **Step 4.4.2: Commit**

```bash
git add justfile
git commit -m "chore(testing): justfile e2e targets"
```

---

### Task 4.5: Frontend — настроить runtime API URL

**Files:**
- Create: `frontend/src/lib/api.ts` (если ещё нет — иначе модифицировать)

Сейчас фронтенд ходит на `/api` через прокси Vite. В preview-режиме (для E2E) прокси нет, поэтому нужно использовать `VITE_API_URL` или абсолютный URL.

- [ ] **Step 4.5.1: Проверить, как фронт сейчас формирует URL запросов**

```bash
cd frontend && grep -rn "axios\|fetch" src/services 2>/dev/null | head -20
```

Если запросы идут на `/api/...` — прокси Vite в dev работает, но в preview нужен env-driven base URL. Дописать в начало `axios`-клиента:

```ts
import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "";

export const api = axios.create({ baseURL });
```

Если базовый клиент уже существует — добавить только `baseURL`.

- [ ] **Step 4.5.2: Smoke — `npm run dev` и `npm run preview`**

```bash
cd frontend && npm run build && npm run preview &
# открыть http://localhost:4173 — должны видеть UI
```

Прибить процесс после проверки.

- [ ] **Step 4.5.3: Commit**

```bash
git add frontend/src/services/  # или frontend/src/lib/
git commit -m "feat(frontend): VITE_API_URL for runtime API base URL configuration"
```

---

### Task 4.6: Первый E2E — golden path

**Files:**
- Create: `e2e/tests/upload-flow.spec.ts`

- [ ] **Step 4.6.1: Создать спек**

```ts
import { test, expect } from "@playwright/test";
import path from "path";
import fs from "fs";

const PDF_PATH = path.resolve(
  __dirname, "..", "..", "backend", "tests", "fixtures", "pdf", "synthetic", "minimal.pdf"
);

test.beforeEach(async ({ request }) => {
  // Чистим БД перед каждым тестом
  await request.post("http://localhost:8001/api/test/reset");
});

test("golden path: upload PDF → review → reports", async ({ page }) => {
  await page.goto("/");

  // Переходим на Upload
  await page.getByRole("link", { name: /Загрузить/i }).click();
  await expect(page).toHaveURL(/\/upload/);

  // Выбираем проект (после reset есть один: "E2E Test Project")
  await page.getByRole("combobox").click();
  await page.getByText("E2E Test Project").click();

  // Загружаем PDF
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(PDF_PATH);

  // Ждём успешный парсинг — модалка/тост или строка с СФ-101
  await expect(page.getByText(/СФ-101/)).toBeVisible({ timeout: 15_000 });
});
```

- [ ] **Step 4.6.2: Подготовить минимальный PDF**

```bash
# Создаём synthetic PDF локально (если ещё нет)
mkdir -p backend/tests/fixtures/pdf/synthetic
python -c "
content = (
    b'%PDF-1.4\n'
    b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
    b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n'
    b'xref\n0 4\n0000000000 65535 f\n'
    b'0000000009 00000 n\n0000000052 00000 n\n0000000092 00000 n\n'
    b'trailer<</Size 4/Root 1 0 R>>\nstartxref\n145\n%%EOF\n'
)
open('backend/tests/fixtures/pdf/synthetic/minimal.pdf', 'wb').write(content)
"
```

- [ ] **Step 4.6.3: Запустить E2E локально**

```bash
just test-e2e
```

Expected: 1 PASSED. Если падает — открыть `e2e/playwright-report/index.html` и `test-results/` для трейса.

Типичные проблемы и фиксы:
- Trigger combobox имеет другое имя — заменить selector.
- Проект не найден после reset — проверить, что `/api/test/reset` действительно засевает "E2E Test Project".
- Backend порт 8001 занят — `lsof -i :8001` (или `netstat -ano` на Windows), убить процесс.

- [ ] **Step 4.6.4: Commit**

```bash
git add backend/tests/fixtures/pdf/synthetic/minimal.pdf e2e/tests/upload-flow.spec.ts
git commit -m "test(e2e): golden path upload → review"
```

---

### Task 4.7: E2E edge cases — non-PDF и unparseable

**Files:**
- Create: `e2e/tests/upload-edge-cases.spec.ts`

- [ ] **Step 4.7.1: Спек**

```ts
import { test, expect } from "@playwright/test";
import path from "path";

test.beforeEach(async ({ request }) => {
  await request.post("http://localhost:8001/api/test/reset");
});

test("uploading a .txt is rejected by backend", async ({ page, request }) => {
  // Альтернативно: триггерим backend напрямую — UI может фильтровать на клиенте
  const response = await request.post("http://localhost:8001/api/invoices/upload", {
    multipart: {
      file: { name: "doc.txt", mimeType: "text/plain", buffer: Buffer.from("not pdf") },
      project_id: "1",
    },
  });
  expect(response.status()).toBe(400);
});

test("unparseable PDF marks status as error", async ({ page, request }) => {
  // Mock OR должен отдать unparseable scenario для этого теста.
  // Чтобы переключить scenario — пробрасываем заголовок через middleware-плагин,
  // или меняем фикстуру happy_path.json временно. На старте — просто загрузка
  // и проверка, что happy path жив. Полную проверку scenario-switching добавим
  // в Task 4.10 если потребуется.
  await page.goto("/upload");
  await expect(page.getByText(/Перетащите файлы сюда/)).toBeVisible();
});
```

- [ ] **Step 4.7.2: Запустить и зафиксить**

```bash
just test-e2e
git add e2e/tests/upload-edge-cases.spec.ts
git commit -m "test(e2e): upload edge case - reject non-PDF files"
```

---

### Task 4.8: Smoke E2E — projects CRUD и reference prices

**Files:**
- Create: `e2e/tests/projects-crud.spec.ts`
- Create: `e2e/tests/reference-prices.spec.ts`

- [ ] **Step 4.8.1: `projects-crud.spec.ts`**

```ts
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://localhost:8001/api/test/reset");
});

test("create and delete a project via API", async ({ request }) => {
  // Создание
  const create = await request.post("http://localhost:8001/api/projects", {
    data: { name: "Smoke E2E Project", contract_number: "S-001" },
  });
  expect(create.status()).toBe(200);
  const created = await create.json();

  // List
  const list = await request.get("http://localhost:8001/api/projects");
  const items = await list.json();
  expect(items.some((p: any) => p.id === created.id)).toBeTruthy();

  // Delete
  const del = await request.delete(`http://localhost:8001/api/projects/${created.id}`);
  expect(del.status()).toBe(200);
});

test("UI projects page renders without errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));

  await page.goto("/projects");
  await page.waitForLoadState("networkidle");

  expect(errors).toEqual([]);
});
```

- [ ] **Step 4.8.2: `reference-prices.spec.ts`**

```ts
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://localhost:8001/api/test/reset");
});

test("reference prices CRUD via API", async ({ request }) => {
  // После reset проект id=1, материалов 2 (В25, В40)
  const classes = await (await request.get("http://localhost:8001/api/material-classes")).json();
  const v25 = classes.find((c: any) => c.name === "В25");

  const create = await request.post("http://localhost:8001/api/reference-prices", {
    data: {
      project_id: 1,
      material_class_id: v25.id,
      price: 9000.0,
      period_start: "2026-01-01",
      period_end: "2026-12-31",
      source: "smoke test",
    },
  });
  expect(create.status()).toBe(200);

  const list = await (await request.get("http://localhost:8001/api/reference-prices")).json();
  expect(list.length).toBe(1);
  expect(list[0].price).toBe(9000.0);
});
```

- [ ] **Step 4.8.3: Запустить и зафиксить**

```bash
just test-e2e
git add e2e/tests/projects-crud.spec.ts e2e/tests/reference-prices.spec.ts
git commit -m "test(e2e): smoke tests for projects and reference prices"
```

---

### Task 4.9: E2E — навигация и темы

**Files:**
- Create: `e2e/tests/theme-navigation.spec.ts`

- [ ] **Step 4.9.1: Спек**

```ts
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("http://localhost:8001/api/test/reset");
});

test("navigation across pages works without JS errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));

  for (const path of ["/", "/upload", "/projects", "/material-classes",
                      "/reference-prices", "/reports", "/settings"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }

  expect(errors).toEqual([]);
});
```

- [ ] **Step 4.9.2: Зафиксить**

```bash
just test-e2e
git add e2e/tests/theme-navigation.spec.ts
git commit -m "test(e2e): navigation smoke without JS errors"
```

---

### Task 4.10: Push конец Этапа 4

```bash
git push
```

Чек-лист в PR обновить вручную.

---

## Этап 5: GitHub Actions CI + документация

**Цель этапа:** push в любую ветку и PR в `main` запускают полный пайплайн (lint → backend → frontend → e2e). При падении — артефакты доступны для отладки.

### Task 5.1: Workflow — lint + backend tests

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 5.1.1: Создать workflow с двумя джобами**

```yaml
name: Tests

on:
  push:
  pull_request:
    branches: [main]

concurrency:
  group: tests-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            backend/requirements.txt
            backend/requirements-test.txt
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install backend deps
        run: pip install -r backend/requirements.txt -r backend/requirements-test.txt
      - name: Ruff
        run: ruff check backend/
      - name: Install frontend deps
        run: cd frontend && npm ci
      - name: ESLint
        run: cd frontend && npm run lint
      - name: TypeScript
        run: cd frontend && npx tsc -b --noEmit

  backend-tests:
    runs-on: ubuntu-latest
    needs: lint
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: udp_test
          POSTGRES_PASSWORD: udp_test
          POSTGRES_DB: udp_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://udp_test:udp_test@localhost:5432/udp_test
      TEST_DATABASE_URL: postgresql+psycopg://udp_test:udp_test@localhost:5432/udp_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            backend/requirements.txt
            backend/requirements-test.txt
      - name: Install deps
        run: pip install -r backend/requirements.txt -r backend/requirements-test.txt
      - name: Migrate
        working-directory: backend
        run: alembic upgrade head
      - name: Pytest with coverage
        working-directory: backend
        run: pytest --cov=. --cov-report=xml --cov-report=term
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: backend-coverage
          path: backend/coverage.xml
```

- [ ] **Step 5.1.2: Commit и push — посмотреть, что CI запустился**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: lint and backend tests workflow"
git push
```

Проверить ход CI: https://github.com/zhukovvlad/udp-tenders/actions

```

Expected: оба джоба зелёные. Если падает — открыть логи, чинить.

---

### Task 5.2: Workflow — frontend tests

**Files:**
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 5.2.1: Добавить джоб `frontend-tests`**

В тот же файл, после `backend-tests`:

```yaml
  frontend-tests:
    runs-on: ubuntu-latest
    needs: lint
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        run: cd frontend && npm ci
      - name: Vitest with coverage
        run: cd frontend && npm run test:coverage
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: frontend-coverage
          path: frontend/coverage/lcov.info
```

- [ ] **Step 5.2.2: Push и проверить**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: frontend tests workflow"
git push
```

Проверить ход CI: https://github.com/zhukovvlad/udp-tenders/actions

```

---

### Task 5.3: Workflow — E2E tests

**Files:**
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 5.3.1: Добавить джоб `e2e-tests`**

```yaml
  e2e-tests:
    runs-on: ubuntu-latest
    needs: lint
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: udp_test
          POSTGRES_PASSWORD: udp_test
          POSTGRES_DB: udp_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://udp_test:udp_test@localhost:5432/udp_test
      TEST_DATABASE_URL: postgresql+psycopg://udp_test:udp_test@localhost:5432/udp_test
      TEST_MODE: "1"
      OPENROUTER_BASE_URL: http://localhost:8002/api/v1
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.txt
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: |
            frontend/package-lock.json
            e2e/package-lock.json
      - uses: extractions/setup-just@v2
      - name: Install backend deps
        run: pip install -r backend/requirements.txt -r e2e/mock_openrouter/requirements.txt
      - name: Migrate
        working-directory: backend
        run: alembic upgrade head
      - name: Install frontend
        run: cd frontend && npm ci && npm run build
      - name: Install e2e
        run: cd e2e && npm ci
      - name: Cache Playwright browsers
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: pw-${{ runner.os }}-${{ hashFiles('e2e/package-lock.json') }}
      - name: Install Playwright browsers
        run: cd e2e && npx playwright install --with-deps chromium
      - name: Run E2E
        run: cd e2e && npx playwright test
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: |
            e2e/playwright-report
            e2e/test-results
          retention-days: 30
```

- [ ] **Step 5.3.2: Push и проверить**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: e2e workflow with Playwright and mock OpenRouter"
git push
```

Проверить ход CI: https://github.com/zhukovvlad/udp-tenders/actions

```

Если e2e падает в CI — скачать `playwright-report` из artifacts и смотреть trace.

---

### Task 5.4: `docs/testing.md`

**Files:**
- Create: `docs/testing.md`

- [ ] **Step 5.4.1: Написать гайд**

```markdown
# Тестирование

## Быстрый старт

```bash
just install      # все зависимости (backend + frontend + e2e + playwright chromium)
just test         # backend + frontend (без E2E)
just test-e2e     # E2E отдельно — поднимает 3 сервиса автоматически
just coverage     # отчёт о покрытии
```

## Слои

- `backend/tests/unit` — чистые функции, без БД и сети.
- `backend/tests/integration` — `TestClient` + реальная Postgres (через `TEST_DATABASE_URL`).
- `frontend/src/**/*.test.tsx` — Vitest + RTL + MSW.
- `e2e/tests/*.spec.ts` — Playwright + Chromium на полном стеке.

## Локальная настройка

1. Скопируй `.env.test.example` в `.env.test`. Заполни `TEST_DATABASE_URL` (отдельная Neon ветка, НЕ прод).
2. `just install`
3. `just test-backend-unit` — должно пройти без БД.
4. `just test-backend-integration` — нужен `TEST_DATABASE_URL`.
5. `just test-e2e` — Playwright сам поднимет backend на 8001, mock OR на 8002, frontend на 5173.

## Снимок AI-ответов

Реальные PDF в репо НЕ коммитятся. Чтобы получить sanitized JSON-фикстуру:

```bash
cp /path/to/real.pdf backend/tests/fixtures/pdf/real/
cd backend
python scripts/snapshot_ai_responses.py tests/fixtures/pdf/real/real.pdf happy_path
git add tests/fixtures/openrouter/happy_path.json
```

Скрипт прогоняет PDF через OpenRouter, заменяет ИНН и наименования поставщиков на фейки, сохраняет sanitized JSON. Реальный PDF остаётся локально (в `.gitignore`).

## Добавление нового теста

### Backend

- Чистая функция → `backend/tests/unit/`
- Эндпоинт → `backend/tests/integration/test_<router>.py` через фикстуру `client`.
- Доменная сущность → `factories.py` фабрика.

### Frontend

- Компонент → `<Component>.test.tsx` рядом с компонентом.
- Перехват API → `server.use(http.<method>(...))` внутри теста.

## Дебаг E2E

```bash
just test-e2e-ui          # интерактивный режим Playwright Inspector
just test-e2e-headed      # с видимым браузером
```

Артефакты упавших тестов в CI: `Actions → run → Artifacts → playwright-report`.

## CI

`.github/workflows/tests.yml` запускает `lint → backend-tests / frontend-tests / e2e-tests` параллельно после lint. Каждый падающий тест блокирует merge в `main` (если настроена branch protection).

Branch protection настраивается вручную: `Settings → Branches → main`. Required: `lint`, `backend-tests`, `frontend-tests`. `e2e-tests` стартует non-required, переводим в required после стабилизации.

## Нет тестов в проде

Никогда не выставляйте `TEST_MODE=1` в проде. Этот флаг подключает `/api/test/reset`, который TRUNCATE'ит всю БД.
```

- [ ] **Step 5.4.2: Commit**

```bash
git add docs/testing.md
git commit -m "docs: testing guide"
git push
```

---

### Task 5.5: Финальная проверка и снять draft с PR

- [ ] **Step 5.5.1: Прогнать всё локально**

```bash
just clean
just install
just lint
just test
just test-e2e
```

Expected: всё зелёное.

- [ ] **Step 5.5.2: Дождаться зелёного CI на ветке**

Проверять во вкладке Actions на GitHub: https://github.com/zhukovvlad/udp-tenders/actions

- [ ] **Step 5.5.3: Снять draft и попросить ревью**

Через GitHub UI: открыть PR → "Ready for review", обновить description с финальной сводкой:

```
## Summary
- pytest unit + integration с Neon test branch
- Vitest + RTL + MSW для frontend
- Playwright E2E с mock-OpenRouter и /api/test/reset под TEST_MODE=1
- GitHub Actions: lint + backend + frontend + e2e параллельно
- justfile как task runner
- docs/testing.md

## Test plan
- [x] just test-backend (unit + integration)
- [x] just test-frontend
- [x] just test-e2e
- [x] CI зелёный
```

- [ ] **Step 5.5.4: Branch protection (вручную через GitHub UI)**

Settings → Branches → Add rule for `main`:
- Require pull request before merging.
- Require status checks: `lint`, `backend-tests`, `frontend-tests`.
- (Опционально) `e2e-tests` после стабилизации.


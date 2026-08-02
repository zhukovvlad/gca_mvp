# Миграция backend pip → uv (full project mode) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести backend с pip + плоских requirements-файлов на uv (project mode): зависимости в `pyproject.toml`, воспроизводимость через `uv.lock`, изолированный venv, Python 3.12 для dev и CI.

**Architecture:** Меняется только tooling и документация — прикладной код не трогается. Зависимости декларируются в `[project.dependencies]` / `[dependency-groups]` с `>=` границами; точные версии фиксирует `uv.lock`, создаваемый двухпроходно (сначала `==`, затем `>=`), чтобы сохранить текущие прямые версии. `justfile` и CI переходят на `uv sync` / `uv run`.

**Tech Stack:** uv (Astral), Python 3.12, FastAPI, pytest, ruff, alembic, GitHub Actions.

## Global Constraints

- **Границы прямых зависимостей:** `>=` floors в `pyproject.toml`; точные версии — только в `uv.lock`.
- **Python:** `requires-python = "==3.12.*"`; `backend/.python-version` = `3.12`.
- **`[tool.uv] package = false`** — проект non-package (flat-layout, нет build backend).
- **Прямые версии не апгрейдим** — двухпроходный `uv lock` сохраняет текущие, включая security-фиксы: `python-multipart>=0.0.31`, `pillow>=12.3.0`, `python-dotenv>=1.2.2`, `pydantic-settings>=2.14.2`.
- **Команды только через `just`** (правило AGENTS.md). Все python-рецепты идут через `uv run`.
  **Bootstrap-исключение:** в Task 1 прямые команды `uv python install`, `uv lock`,
  `uv sync` и `uv run pytest` разрешены как начальная загрузка миграции (подходящих
  just-рецептов ещё нет). После перевода justfile (Task 2) все проверки выполняются
  только через `just`. **Recovery-исключение:** единственный прямой `uv lock` в Task 6
  Step 5 — аварийное восстановление при рассинхроне lock на CI (заводить отдельный
  рецепт ради этого сценария не будем).
- **Provenance:** шаблоны коммитов ниже даны без attribution-трейлера. Исполнитель
  (человек или агент) добавляет собственный трейлер согласно своему harness — не
  приписывать коммиты чужой модели.
- **Новых зависимостей не добавляем.** Сломанный рецепт `test-backend-watch` (ptw) удаляется.
  `rapidfuzz` **не переносим** в pyproject — объявлен, но нигде не импортируется (0 вхождений
  в prod/tests/scripts); удаляется как мёртвая зависимость (решение пользователя 2026-07-21).
  Если понадобится под RP-2 (fuzzy-валидатор направлений) — добавить явно тогда же.
- **`.env` / `.env.test` не трогать.** Исторические миграции и `docs/done/**` не редактировать.
  **Исторические** также корневые `2026-05-06-udp-price-tracker.md` и
  `2026-05-06-udp-price-tracker-design.md` (git-tracked, вне `docs/done/`): их упоминания
  `pip install -r requirements.txt` — снимок прошлого, НЕ править, несмотря на миграцию.
- **CI:** `uv sync --locked` (не `--frozen`), все uv-команды с `working-directory: backend`.
- **Предусловие:** локально `uv` установлен заранее; в CI его ставит `astral-sh/setup-uv`.
- **Приёмка:** `just install && just lint && just test` — всё зелёное.

**Спека:** [docs/superpowers/specs/2026-07-21-uv-migration-design.md](../specs/2026-07-21-uv-migration-design.md)

## File Structure

| Файл | Ответственность | Действие |
|------|-----------------|----------|
| `backend/.python-version` | Пин интерпретатора для uv | Create |
| `backend/pyproject.toml` | Декларация зависимостей + tool.uv (конфиг pytest/ruff/coverage не трогаем) | Modify |
| `backend/uv.lock` | Полное воспроизводимое окружение (прямые + транзитивные + хеши) | Create (генерируется) |
| `justfile` | Dev-рецепты через uv | Modify |
| `.github/workflows/backend-tests.yml` | CI на setup-uv + uv sync --locked | Modify |
| `.gitignore` | Игнор `/backend/.venv/` | Modify |
| `backend/requirements.txt` | — | Delete |
| `backend/requirements-test.txt` | — | Delete |
| `README.md` | Требования/стек: Python 3.12, uv, pytest 9 | Modify |
| `AGENTS.md` | Стек: Python 3.12 + uv | Modify |
| `docs/testing.md` | «pytest 8» → «pytest 9», snapshot-команда на `uv run`, заметка про `backend/.venv` | Modify |
| `docs/security-audit-2026-07-21.md` | Отметка о новом источнике зависимостей | Modify (append) |

**Прерусловие исполнителю:** работаем в ветке `build/uv-migration` (уже создана). Проверить, что `uv` доступен: `uv --version` (любой вывод версии = ок). Если нет — установить по https://docs.astral.sh/uv/getting-started/installation/ и только потом начинать.

---

### Task 1: pyproject + двухпроходный lock + рабочий venv

Ядро миграции. Deliverable: `backend/uv.lock` с текущими прямыми версиями и `>=` границами в pyproject; `uv run pytest tests/unit` зелёный в изолированном venv.

**Files:**
- Create: `backend/.python-version`
- Modify: `backend/pyproject.toml` (добавить секции в начало, до `[tool.pytest.ini_options]`)
- Create (генерируется): `backend/uv.lock`

**Interfaces:**
- Consumes: текущие версии из `backend/requirements.txt` и `backend/requirements-test.txt` (файлы ещё на месте, удаляются в Task 4).
- Produces: `backend/pyproject.toml` с `[project]` (name `udp-backend`), `[dependency-groups].dev`, `[tool.uv] package = false`; `backend/uv.lock`; `backend/.python-version`. Task 2 и далее полагаются на то, что `uv sync` / `uv run` работают из каталога `backend/`.

- [ ] **Step 1: Создать `.python-version`**

Создать файл `backend/.python-version` с единственной строкой:
```
3.12
```

- [ ] **Step 2: Убедиться, что Python 3.12 доступен uv**

Run: `cd backend && uv python install`
Expected: uv скачивает/находит CPython 3.12.x (строка вида `Installed Python 3.12.x` либо `Python 3.12.x is already available`). Exit 0.

- [ ] **Step 3: Добавить секции в pyproject с версиями как `==` (проход 1)**

В начало `backend/pyproject.toml`, перед строкой `[tool.pytest.ini_options]`, вставить (версии — точно как в текущих requirements):

```toml
[project]
name = "udp-backend"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = [
    "fastapi==0.139.2",
    "uvicorn[standard]==0.30.0",
    "sqlalchemy==2.0.35",
    "python-multipart==0.0.31",
    "python-dotenv==1.2.2",
    "httpx==0.27.0",
    "openpyxl==3.1.5",
    "boto3==1.35.0",
    "psycopg[binary]==3.2.13",
    "alembic==1.14.1",
    "pgvector==0.4.2",
    "pyjwt==2.13.0",
    "pwdlib[argon2]==0.3.0",
    "pydantic-settings==2.14.2",
    "pydantic[email]==2.13.4",
    "click==8.3.3",
    "pypdfium2==5.10.1",
    "pikepdf==10.8.0",
    "pillow==12.3.0",
]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "pytest-asyncio==1.4.0",
    "pytest-cov==6.0.0",
    "pytest-xdist==3.6.1",
    "pytest-dotenv==0.5.2",
    "respx==0.21.1",
    "factory-boy==3.3.1",
    "faker==30.10.0",
    "freezegun==1.5.1",
    "ruff==0.7.4",
]

[tool.uv]
package = false

```

- [ ] **Step 4: Первый lock (проход 1)**

Run: `cd backend && uv lock`
Expected: создан `backend/uv.lock`; вывод содержит строку `Resolved N packages`. Exit 0.

- [ ] **Step 5: Заменить прямые зависимости на `>=` (проход 2)**

В `backend/pyproject.toml` во всех строках `[project].dependencies` и `[dependency-groups].dev` заменить `==` на `>=`. Результат `[project].dependencies`:

```toml
dependencies = [
    "fastapi>=0.139.2",
    "uvicorn[standard]>=0.30.0",
    "sqlalchemy>=2.0.35",
    "python-multipart>=0.0.31",
    "python-dotenv>=1.2.2",
    "httpx>=0.27.0",
    "openpyxl>=3.1.5",
    "boto3>=1.35.0",
    "psycopg[binary]>=3.2.13",
    "alembic>=1.14.1",
    "pgvector>=0.4.2",
    "pyjwt>=2.13.0",
    "pwdlib[argon2]>=0.3.0",
    "pydantic-settings>=2.14.2",
    "pydantic[email]>=2.13.4",
    "click>=8.3.3",
    "pypdfium2>=5.10.1",
    "pikepdf>=10.8.0",
    "pillow>=12.3.0",
]
```
И `dev` — так же (`pytest>=9.1.1`, `ruff>=0.7.4`, и т.д.).
`requires-python = "==3.12.*"` и `[tool.uv] package = false` оставить без изменений.

- [ ] **Step 6: Повторный lock (проход 2) — версии должны сохраниться**

Run: `cd backend && uv lock`
Expected: при существующем `uv.lock` uv сохраняет уже зафиксированные версии; в выводе нет апгрейдов прямых зависимостей (нет строк вида `Updated fastapi ...`). Exit 0.

- [ ] **Step 7: Синхронизировать окружение**

Run: `cd backend && uv sync`
Expected: создан `backend/.venv`; вывод содержит `Installed N packages` (или `Audited N packages`). Exit 0. Файл `.venv` НЕ в git (Task 4 добавит игнор).

- [ ] **Step 8: Проверить, что версии ВСЕХ прямых зависимостей не изменились**

Теперь `.venv` существует, поэтому `uv pip list` работает. Сверить установленные версии
прямых зависимостей с исходными (те же, что были в requirements — файлы ещё на месте):

Run: `cd backend && uv pip list --format=freeze | grep -Ei "^(fastapi|uvicorn|sqlalchemy|python-multipart|python-dotenv|httpx|openpyxl|boto3|psycopg|alembic|pgvector|pyjwt|pwdlib|pydantic-settings|pydantic|click|pypdfium2|pikepdf|pillow|pytest|pytest-asyncio|pytest-cov|pytest-xdist|pytest-dotenv|respx|factory-boy|faker|freezegun|ruff)=="`
Expected (прямые версии — точно исходные, включая security-фиксы):
```
alembic==1.14.1
boto3==1.35.0
click==8.3.3
factory-boy==3.3.1
faker==30.10.0
fastapi==0.139.2
freezegun==1.5.1
httpx==0.27.0
openpyxl==3.1.5
pgvector==0.4.2
pikepdf==10.8.0
pillow==12.3.0
psycopg==3.2.13
pwdlib==0.3.0
pydantic==2.13.4
pydantic-settings==2.14.2
pyjwt==2.13.0
pypdfium2==5.10.1
pytest==9.1.1
pytest-asyncio==1.4.0
pytest-cov==6.0.0
pytest-dotenv==0.5.2
pytest-xdist==3.6.1
python-dotenv==1.2.2
python-multipart==0.0.31
respx==0.21.1
ruff==0.7.4
sqlalchemy==2.0.35
uvicorn==0.30.0
```
Если версия любой прямой зависимости отличается — проход 2 подтянул апгрейд; вернуть её
явным ограничением в pyproject и переснять lock. (Транзитивные пакеты не проверяем —
их значения фиксируются lock впервые, сверять не с чем.)

- [ ] **Step 9: Прогнать unit-тесты в новом venv**

Run: `cd backend && uv run pytest tests/unit -q`
Expected: `189 passed` (или текущее число unit-тестов), exit 0.

- [ ] **Step 10: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/.python-version
git commit -m "build: ввести uv project mode (pyproject + uv.lock, Python 3.12)"
```

---

### Task 2: перевести `justfile` на uv

Deliverable: все backend-рецепты идут через `uv run` / `uv sync`; `just install`, `just lint-backend`, `just test-backend-unit` зелёные; сломанный `test-backend-watch` удалён.

**Files:**
- Modify: `justfile`

**Interfaces:**
- Consumes: `backend/pyproject.toml` + `backend/uv.lock` из Task 1.
- Produces: рецепты, вызывающие python-тулинг через `uv run`; `install-backend` через `uv sync`.

- [ ] **Step 1: `install-backend` → `uv sync`**

В `justfile` заменить:
```makefile
install-backend:
    cd backend && pip install -r requirements.txt -r requirements-test.txt
```
на:
```makefile
install-backend:
    cd backend && uv sync
```

- [ ] **Step 2: `dev-backend` → `uv run`**

Заменить строку рецепта `dev-backend`:
```makefile
    cd backend && uvicorn main:app --reload --port 8259
```
на:
```makefile
    cd backend && uv run uvicorn main:app --reload --port 8259
```
(комментарий с инвариантом S1 над рецептом сохранить без изменений.)

- [ ] **Step 3: Все pytest-рецепты → `uv run pytest`**

В `justfile` в каждом из рецептов заменить `pytest` на `uv run pytest` (и `ptw` разбирается в Step 5):
- `test-backend` (ветка else: `cd backend && pytest` → `cd backend && uv run pytest`)
- `test-backend-unit`: `cd backend && uv run pytest tests/unit -v`
- `test-backend-integration`: `cd backend && uv run pytest tests/integration -v`
- `test-int-k`: `cd backend && uv run pytest tests/integration -v -k "{{pattern}}"`
- `test-int-local`: `cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v`
- `test-int-local-k`: `cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v -k "{{pattern}}"`
- `test-backend-local`: `cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest`
- `test-unit-k`: `cd backend && uv run pytest tests/unit -v -k "{{pattern}}"`
- `coverage-backend`: `cd backend && uv run pytest --cov=. --cov-report=html --cov-report=term`

- [ ] **Step 4: ruff и alembic и cli → `uv run`**

Заменить:
- `lint-backend`: `cd backend && uv run ruff check .`
- `format-backend`: `cd backend && uv run ruff format .`
- `db-revision`: `cd backend && uv run alembic revision -m "{{message}}"`
- `db-migrate`: `cd backend && uv run alembic upgrade head`
- `db-test-migrate`: `cd backend && DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head`
- `db-test-check` (обе строки): `cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head` и `... uv run alembic check`
- `create-superuser`: `cd backend && uv run python -m cli create-superuser --email {{email}}`
- `create-org`: `cd backend && uv run python -m cli create-org --name "{{name}}"`

- [ ] **Step 5: Удалить сломанный `test-backend-watch`**

Удалить весь рецепт вместе с комментарием:
```makefile
# Watch-режим (нужен pytest-watch)
test-backend-watch:
    cd backend && ptw tests -- -v
```
(`ptw`/`pytest-watch` не объявлен в зависимостях — рецепт невоспроизводим.)

- [ ] **Step 6: Проверить install + lint + unit**

Run: `just install`
Expected: `uv sync` для backend + `npm ci` для frontend; backend-часть — exit 0, `Audited/Installed N packages`.

Run: `just lint-backend`
Expected: `All checks passed!`, exit 0.

Run: `just test-backend-unit`
Expected: `189 passed`, exit 0.

- [ ] **Step 7: Commit**

```bash
git add justfile
git commit -m "build: перевести just-рецепты backend на uv run / uv sync"
```

---

### Task 3: перевести CI на uv

Deliverable: `.github/workflows/backend-tests.yml` использует `astral-sh/setup-uv` + `uv sync --locked` + `uv run`, все uv-шаги с `working-directory: backend`. Валидируется прогоном на PR.

**Files:**
- Modify: `.github/workflows/backend-tests.yml`

**Interfaces:**
- Consumes: `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version`.
- Produces: green CI на push/PR.

- [ ] **Step 1: Подтвердить актуальную версию setup-uv и uv**

**Важно:** с v8.0.0 `setup-uv` перешёл на immutable-релизы — плавающие теги `@v8` / `@v8.0`
**не резолвятся** и публиковаться не будут. Работают только полные неизменяемые теги
(`@v8.3.2`) или SHA. Дока рекомендует SHA-пин.
Открыть релиз https://github.com/astral-sh/setup-uv/releases/tag/v8.3.2 и сверить его SHA.
Пин: **v8.3.2** → SHA `11f9893b081a58869d3b5fccaea48c9e9e46f990`, uv `0.11.29` — оба
подтверждены (setup-uv release v8.3.2; uv 0.11.29 от 2026-07-15). Задача Step 1 —
**только сверить**, что SHA всё ещё указывает на тег `v8.3.2` (immutable-релиз не меняется).
**Не** переходить на более новый релиз, если он появился: проверенный immutable-пин лучше
оставить как есть. Значения переиспользовать в Step 2.

- [ ] **Step 2: Заменить блок установки Python + зависимостей**

В `.github/workflows/backend-tests.yml` удалить шаги `actions/setup-python@v5` (с `cache: pip` и `cache-dependency-path`) и `Install backend dependencies`. Вместо них — setup-uv + sync (версии из Step 1):

```yaml
      - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          version: "0.11.29"
          enable-cache: true
          working-directory: backend

      - name: Install backend dependencies (uv, locked)
        run: uv sync --locked
        working-directory: backend
```
`actions/checkout@v4` оставить первым шагом. Python 3.12 приезжает из `backend/.python-version` — явный `python-version` не нужен.

- [ ] **Step 3: Перевести lint и test на `uv run` с working-directory**

Заменить:
```yaml
      - name: Lint (ruff)
        run: cd backend && ruff check .

      - name: Run backend tests (unit + integration)
        run: cd backend && pytest
```
на (флаг `--locked` не даёт `uv run` молча пересобрать lock в раннере при рассинхроне —
любой дрейф pyproject/lock падает явно, согласуясь с recovery-процедурой Task 6 Step 5):
```yaml
      - name: Lint (ruff)
        run: uv run --locked ruff check .
        working-directory: backend

      - name: Run backend tests (unit + integration)
        # conftest applies Alembic to head on TEST_DATABASE_URL once per session;
        # pgvector image makes CREATE EXTENSION vector succeed.
        run: uv run --locked pytest
        working-directory: backend
```
Блок `env:` (DATABASE_URL, TEST_DATABASE_URL, SECRET_KEY, OPENROUTER_API_KEY) и `services.postgres` — без изменений.

- [ ] **Step 4: Проверить workflow глазами**

`PyYAML` не входит в зависимости — локально YAML не парсим. Вместо этого визуально
сверить, что у каждого uv-шага есть `working-directory: backend`, блок `env:` и
`services.postgres` не тронуты, а `actions/setup-python` больше не упоминается:
Run: `grep -nE "setup-python|working-directory|uv (sync|run)" .github/workflows/backend-tests.yml`
Expected: строк с `setup-python` нет; присутствуют `uv sync --locked`,
`uv run --locked ruff` и `uv run --locked pytest`; у каждого uv-шага и у `setup-uv` —
`working-directory: backend`.
Полная валидность workflow подтверждается прогоном CI на PR (Task 6, Step 5).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/backend-tests.yml
git commit -m "ci: backend workflow на uv (setup-uv + uv sync --locked)"
```

---

### Task 4: удалить requirements-файлы + игнор `.venv`

Deliverable: плоские requirements удалены, `.venv` игнорируется, `uv sync` по-прежнему работает, ссылок на requirements в tooling не осталось.

**Files:**
- Delete: `backend/requirements.txt`, `backend/requirements-test.txt`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: рабочий `uv.lock` (Task 1), justfile/CI уже не ссылаются на requirements (Task 2, 3).

- [ ] **Step 1: Добавить игнор venv**

В `.gitignore` в секцию `# Python` (после строки `backend/logs/`) добавить:
```
/backend/.venv/
```
(scoped, с ведущим `/` — окружение создаётся рядом с backend `pyproject.toml`, как в спеке.)

- [ ] **Step 2: Удалить requirements-файлы**

Run: `git rm backend/requirements.txt backend/requirements-test.txt`
Expected: `rm 'backend/requirements.txt'` и `rm 'backend/requirements-test.txt'`.

- [ ] **Step 3: Убедиться, что ссылок на requirements не осталось в tooling**

Run: `grep -rn "requirements" justfile .github/workflows/ 2>/dev/null || echo "no refs"`
Expected: `no refs` (в justfile и workflows — ни одной ссылки).

- [ ] **Step 4: Проверить, что установка ещё работает**

Run: `just install-backend`
Expected: `uv sync` отрабатывает, `Audited N packages` (окружение уже синхронно с lock), exit 0.

- [ ] **Step 5: Commit**

```bash
git add .gitignore backend/requirements.txt backend/requirements-test.txt
git commit -m "build: удалить requirements*.txt, игнорировать backend/.venv"
```

---

### Task 5: обновить документацию

Deliverable: README/AGENTS/testing синхронизированы с Python 3.12 + uv + pytest 9; в датированном аудите — отметка о новом источнике зависимостей (исторический текст не переписан).

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/testing.md`, `docs/security-audit-2026-07-21.md`

- [ ] **Step 1: README — стек и требования**

В `README.md`:
- стр. 24: `| Бэкенд | Python 3.12+, FastAPI, SQLAlchemy (sync), Alembic, pydantic-settings |` → заменить `Python 3.12+` на `Python 3.12`.
- стр. 31: `| Тесты (BE) | pytest 8, respx, factory_boy |` → `pytest 9` вместо `pytest 8`.
- стр. 41: `- Python 3.12+ и Node.js 24+` → `- Python 3.12 и Node.js 24+`.
- стр. 42 (после строки про `just`, в списке «Требования»): добавить пункт:
  ```
  - [uv](https://docs.astral.sh/uv/) — менеджер зависимостей/окружений Python (`winget install astral-sh.uv` или см. сайт)
  ```

- [ ] **Step 2: AGENTS.md — стек**

В `AGENTS.md` стр. 6 заменить:
```
Backend: Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, pydantic-settings; auth — pyjwt HS256 + argon2, httpOnly cookies, CSRF. Зависимости — uv (pyproject.toml + uv.lock).
```
(было `Python 3.12+` без хвоста про uv.)

- [ ] **Step 3: testing.md — версия pytest, uv-команда, заметка про venv**

В `docs/testing.md`:
- стр. 130: `- **pytest 8** + ...` → `- **pytest 9** + ...`.
- стр. 274: `python scripts/snapshot_ai_responses.py tests/fixtures/pdf/real/real.pdf my_scenario`
  → `uv run python scripts/snapshot_ai_responses.py tests/fixtures/pdf/real/real.pdf my_scenario`
  (строка `cd backend` над ней — без изменений).
- рядом с описанием `just install` (около стр. 31–32) добавить одно предложение:
  `just install` создаёт изолированный `backend/.venv` из `uv.lock` (uv sync) — отдельный
  venv активировать не нужно, все рецепты идут через `uv run`.

- [ ] **Step 4: Отметка в аудите (не переписывая историю)**

В `docs/security-audit-2026-07-21.md` в конец раздела «## 2. Зависимости backend (pip-audit)» (после строки с рекомендацией, стр. ~49) добавить:
```

> **Обновление (миграция на uv):** источник backend-зависимостей переведён с `requirements*.txt` на `pyproject.toml` + `uv.lock`. Указанные фиксы зафиксированы как `>=` границы в pyproject; точные версии — в `uv.lock`. См. `docs/superpowers/specs/2026-07-21-uv-migration-design.md`.
```

- [ ] **Step 5: Проверить отсутствие устаревших упоминаний**

Run: `grep -rn "3.12+\|pytest 8\|pip install -r" README.md AGENTS.md docs/testing.md 2>/dev/null || echo "clean"`
Expected: `clean`.

- [ ] **Step 6: Commit**

```bash
git add README.md AGENTS.md docs/testing.md docs/security-audit-2026-07-21.md
git commit -m "docs: синхронизировать README/AGENTS/testing с uv + Python 3.12"
```

---

### Task 6: финальная приёмка

Deliverable: полная проверка по правилам репозитория — `just install && just lint && just test` зелёные; PR открыт.

**Files:** —

- [ ] **Step 1: Установка**

Run: `just install`
Expected: backend `uv sync` exit 0. (Если frontend `npm ci` падает на EPERM-блокировке `lightningcss` — закрыть Vite/редактор и повторить; это не связано с миграцией.)

- [ ] **Step 2: Lint**

Run: `just lint`
Expected: backend `All checks passed!`; frontend eslint без ошибок. Exit 0.

- [ ] **Step 3: Тесты**

Run: `just test`
Expected: backend unit+integration зелёные (`189 passed` unit, `327 passed` integration или текущие числа), frontend vitest зелёный. Exit 0.
Дополнительно (явная проверка локального Postgres): `just test-int-local` → `327 passed`.

- [ ] **Step 4: Push + PR**

```bash
git push -u origin build/uv-migration
gh pr create --base main --title "build: миграция backend на uv (project mode)" --body "См. docs/superpowers/specs/2026-07-21-uv-migration-design.md и docs/superpowers/plans/2026-07-21-uv-migration.md."
```

- [ ] **Step 5: Дождаться зелёного CI**

Проверить, что workflow `backend-tests` на PR прошёл (setup-uv + uv sync --locked + uv run pytest).
Если `--locked` падает на рассинхроне lock/pyproject — **не** переснимать lock вслепую:
1. `cd backend && uv lock` локально (recovery-исключение к правилу «только just» — см. Global Constraints);
2. `git diff backend/uv.lock` — убедиться, что изменились только метаданные/хеши, а **версии
   прямых зависимостей не поехали** (иначе разобраться, что во второй проход подтянуло апгрейд);
3. только после проверки diff — закоммитить обновлённый `uv.lock` и запушить.

---

## Self-Review

**Spec coverage:**
- §1 pyproject (`[project]`/`[dependency-groups]`/`[tool.uv] package=false`) → Task 1 ✓
- §2 двухпроходный lock (`.python-version` до lock; `==`→lock→`>=`→lock) → Task 1 Steps 1–7 ✓
- §3 файлы (uv.lock, .python-version create; requirements delete; .gitignore) → Task 1 + Task 4 ✓
- §4 justfile (uv sync/uv run, удалить ptw-рецепт) → Task 2 ✓
- §5 CI (setup-uv, --locked, working-directory: backend, пин версии) → Task 3 ✓
- §6 docs (README, AGENTS, testing, отметка в аудите; .env.example и docs/done не трогаем) → Task 5 ✓
- Предусловие (uv локально) → prerequisite-блок + Task 6 ✓
- Приёмка (`just install && just lint && just test`) → Task 6 ✓

**Placeholder scan:** setup-uv запинен на подтверждённый immutable-релиз v8.3.2 (SHA `11f9893…`) + uv 0.11.29 — конкретные проверенные значения, не placeholder; Step 1 лишь повторно сверяет соответствие `v8.3.2 ↔ SHA`. Числа passed-тестов даны с оговоркой «или текущие». Прочих TBD/TODO нет.

**Type/имена consistency:** имена файлов, рецептов и команд (`uv sync`, `uv run`, `--locked`, `backend/.venv/`, `test-int-local`) согласованы между задачами и со спекой. Прямые версии в Task 1 (`==`→`>=`) идентичны списку из требований, за исключением осознанно удалённого `rapidfuzz`.

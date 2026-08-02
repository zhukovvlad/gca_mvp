# Дизайн: миграция backend с pip на uv (full project mode)

**Дата:** 2026-07-21
**Статус:** одобрено к реализации (после ревью)
**Автор:** brainstorming-сессия (Claude + zhukovvlad)

## Проблема

1. У backend нет виртуального окружения. [justfile:19](../../../justfile#L19) выполняет
   `pip install -r requirements.txt` без активации venv, поэтому пакеты ставятся в
   глобальный user-level site-packages (`AppData\Roaming\Python\Python314\...`).
2. Рассинхрон Python: на dev-машине глобальный интерпретатор — **3.14**, а CI и
   `ruff target-version` — **3.12**. Dev и CI бегут на разных версиях.
3. Хочется современный, воспроизводимый и быстрый tooling — переход на `uv`.

## Область изменений

Только backend-tooling и сопутствующая документация. Прикладной код (`main.py`,
роутеры, crud, alembic-миграции) **не трогаем**. Логика dev-рецептов, порты,
инвариант S1 async-processing — без изменений.

## Принятые решения

- **Режим uv:** full project mode — зависимости в `pyproject.toml`, воспроизводимость
  через `uv.lock`.
- **Python:** стандартизируемся на **3.12** (выравнивание dev = CI). uv скачивает
  изолированный 3.12 в `.venv`, глобальный 3.14 не трогается.
- **Границы версий прямых зависимостей:** `>=` floors в `pyproject.toml`; точная
  фиксация — в `uv.lock`.
- **`ptw`/pytest-watch:** сломанный рецепт `test-backend-watch` **удаляется** (ptw
  никогда не был объявлен в зависимостях). Новых зависимостей не добавляем.

## Целевая структура

### 1. `backend/pyproject.toml`

В существующий файл добавляются секции `[project]`, `[dependency-groups]`, `[tool.uv]`.
Конфиг `[tool.pytest.ini_options]`, `[tool.coverage.*]`, `[tool.ruff.*]` — без изменений.

```toml
[project]
name = "udp-backend"
version = "0.1.0"
requires-python = "==3.12.*"
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

[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
    "pytest-cov>=6.0.0",
    "pytest-xdist>=3.6.1",
    "pytest-dotenv>=0.5.2",
    "respx>=0.21.1",
    "factory-boy>=3.3.1",
    "faker>=30.10.0",
    "freezegun>=1.5.1",
    "ruff>=0.7.4",
]

[tool.uv]
package = false
```

**`rapidfuzz` не переносится:** пакет был объявлен в `requirements-test.txt`, но нигде не
импортируется (0 вхождений в prod/tests/scripts) — удаляется как мёртвая зависимость
(решение пользователя 2026-07-21), симметрично удалению сломанного рецепта `ptw`. Если
понадобится под RP-2 (fuzzy-валидатор направлений) — добавить явно тогда же.

**`package = false`** объявляет проект как non-package (это flat-layout приложение:
нет build backend и каталога-пакета `udp_backend`). Настройка штатная для uv и
защищает от изменения поведения в будущих версиях uv.

**Пиннинг — почему `>=` + lockfile, а не `==`:** `==` **не** делает `uv.lock`
избыточным. Даже при точных прямых зависимостях lockfile фиксирует транзитивные
пакеты, артефакты/хеши и платформенные варианты. Разделение ответственности:

> В `pyproject.toml` храним допустимые границы прямых зависимостей, в `uv.lock` —
> полностью разрешённое воспроизводимое окружение.

### 2. Двухпроходное создание `uv.lock` (критично)

При первом `uv lock` из одних `>=` uv вправе выбрать более новые версии, чем стоят
сейчас, — это скрытое массовое обновление. Чтобы миграция не смешалась с апгрейдом,
lockfile создаётся в два прохода:

0. Создать `backend/.python-version` (`3.12`) **до** первого `uv lock`, чтобы весь
   uv-flow с самого начала явно выполнялся под Python 3.12.
1. Перенести существующие версии в `pyproject.toml` как **`==`** (точно текущие).
2. `uv lock` фиксирует существующие версии прямых зависимостей и впервые создаёт
   полный транзитивный lock. Транзитивные версии могут отличаться от текущего
   глобального окружения, поскольку прежние requirements-файлы их не фиксировали.
3. Заменить прямые зависимости на **`>=`** floors.
4. `uv lock` снова — при существующем lockfile uv **предпочитает уже
   зафиксированные версии**, пока они удовлетворяют новым ограничениям (без явного
   `--upgrade` апгрейда не происходит).

Итог: версии всех прямых зависимостей остаются прежними, включая security-фиксы
(`python-multipart 0.0.31`, `pillow 12.3.0`, `python-dotenv 1.2.2`,
`pydantic-settings 2.14.2`); транзитивное окружение впервые становится полностью
зафиксированным в `uv.lock`.

Ссылка на поведение uv: https://docs.astral.sh/uv/concepts/projects/sync/

### 3. Новые / удаляемые файлы

| Файл | Действие |
|------|----------|
| `backend/uv.lock` | создать, закоммитить |
| `backend/.python-version` | создать, содержимое `3.12` (пинит minor; patch — актуальный доступный 3.12.x) |
| `backend/requirements.txt` | удалить |
| `backend/requirements-test.txt` | удалить |
| `.gitignore` | добавить `/backend/.venv/` (scoped — окружение создаётся рядом с backend `pyproject.toml`) |

### 4. `justfile`

```makefile
install-backend:
    cd backend && uv sync

dev-backend:
    cd backend && uv run uvicorn main:app --reload --port 8259
```

Все рецепты, дёргающие python-тулинг, получают префикс `uv run`:
`pytest` (все test-* рецепты), `ruff` (lint-backend, format-backend),
`alembic` (db-*), `python -m cli` (create-superuser, create-org).
Рецепт `test-backend-watch` (`ptw`) — **удаляется**.
Порты, env-переменные, логика локального Postgres, инвариант S1 — без изменений.

### 5. CI — `.github/workflows/backend-tests.yml`

Заменить `actions/setup-python` + `pip install` на `astral-sh/setup-uv` +
`uv sync --locked` + `uv run ...`.

- **`--locked`, а не `--frozen`:** `--locked` проверяет, что `uv.lock` соответствует
  `pyproject.toml`, и падает при рассинхронизации (нужно для PR-проверки);
  `--frozen` использует lockfile без проверки актуальности.
- **`working-directory: backend`:** pyproject/`.python-version`/`uv.lock` лежат в
  `backend/`, поэтому все `uv`-команды выполняются оттуда — иначе вложенный
  `.python-version` не гарантированно подхватится.
- **Версия Python** приезжает из `.python-version`; блок `python-version: "3.12"`
  и `cache: pip` из `setup-python` уходят (кеш обеспечивает setup-uv).
- **Пиннинг action:** с v8.0.0 `setup-uv` перешёл на immutable-релизы — плавающие
  теги (`@v8`, `@v8.0`) **не резолвятся**, только полный неизменяемый тег или SHA.
  Пин: `astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2`,
  uv `version: "0.11.29"`, `enable-cache: true` (оба значения подтверждены). При
  реализации — лишь сверить соответствие `v8.3.2 ↔ SHA`; проверенный immutable-пин
  на новый релиз не менять.

Env-переменные (`DATABASE_URL`, `TEST_DATABASE_URL`, `SECRET_KEY`,
`OPENROUTER_API_KEY`) и Postgres service-container — без изменений.

Ссылки: https://docs.astral.sh/uv/guides/integration/github/ ·
https://docs.astral.sh/uv/reference/settings/

Целевые шаги job:
```yaml
- uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
  with:
    version: "0.11.29"
    enable-cache: true
    working-directory: backend
- run: uv sync --locked
  working-directory: backend
- run: uv run --locked ruff check .
  working-directory: backend
- run: uv run --locked pytest
  working-directory: backend
```

### 6. Документация

| Файл | Изменение |
|------|-----------|
| [README.md](../../../README.md) стр. 24 | «Python 3.12+» → «Python 3.12» |
| [README.md](../../../README.md) стр. 31 | «pytest 8» → «pytest 9» (сейчас неверно: в зависимостях `pytest 9.1.1`) |
| [README.md](../../../README.md) стр. 41 | «Python 3.12+ и Node.js 24+» → «Python 3.12 и Node.js 24+» |
| [README.md](../../../README.md) стр. 42 | добавить строку про `uv` в «Требования» (напр. `uv` — установить по инструкции astral.sh) |
| [AGENTS.md](../../../AGENTS.md) стр. 6 | синхронизировать «Python 3.12+» → «Python 3.12»; упомянуть uv/uv.lock в секции «Стек» |
| [docs/testing.md](../../../docs/testing.md) | обновить команды установки/запуска на `uv run`, если фигурируют |
| `docs/security-audit-2026-07-21.md` | добавить **короткую отметку** о новом источнике зависимостей (pyproject/uv.lock вместо requirements). Исторический текст аудита **не переписывать** |

**Не трогаем:** `backend/.env.example` (инструкций по установке там нет);
исторические `docs/done/**` и старые specs/plans, упоминающие `requirements.txt`.

## Предусловие

Локально `uv` должен быть установлен заранее (standalone-инсталлятор / winget /
pip). В CI его устанавливает `astral-sh/setup-uv` — готового `uv` на runner не
требуется. Это единственный новый инструмент — запрошен пользователем явно.

## Порядок работ

1. Создать `backend/.python-version` (`3.12`) — до любого `uv lock`.
2. Добавить `[project]`/`[dependency-groups]`/`[tool.uv]` в pyproject с версиями как
   `==` (шаг 1 двухпроходного lock).
3. `uv lock` (проход 1) → зафиксировать прямые версии + впервые транзитивный lock.
4. Заменить прямые зависимости на `>=` floors; `uv lock` (проход 2) → прямые версии
   сохраняются.
5. Переписать `justfile` (`uv sync` / `uv run`; удалить `test-backend-watch`).
6. Обновить CI (`setup-uv` + `uv sync --locked` + `uv run`, `working-directory: backend`).
7. Удалить `requirements.txt` / `requirements-test.txt`; добавить `/backend/.venv/`
   в `.gitignore`.
8. Обновить документацию (README, AGENTS.md, testing.md, отметка в аудите).
9. Верификация.

## Верификация (приёмка)

Согласно правилам репозитория (`just lint` + `just test`):

```text
just install     # uv sync создаёт .venv и ставит из uv.lock
just lint        # ruff (backend) + eslint (frontend)
just test        # backend (unit + integration) + frontend
```

Дополнительно (не заменяет `just test`): `just test-int-local` — явная проверка на
локальном Postgres.

Успех = все три команды зелёные.

## Откат

Тривиальный: вернуть `requirements.txt` / `requirements-test.txt` и старый
`justfile`/CI из git; удалить `uv.lock`, `.python-version` и uv-секции из pyproject.

## Открытые вопросы

Нет — все развилки закрыты в ходе ревью.

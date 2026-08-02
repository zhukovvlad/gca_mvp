# Контракт окружения: роль вместо вендора — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переключить guard от случайной записи в развёрнутую БД с оси «вендор» (`neon.tech`) на ось «роль окружения» (`APP_ENV` + `DB_EXTRA_TARGETS`), потому что прод не обязательно Neon.

**Architecture:** `backend/db_guard.py` перестаёт знать про вендоров. При `APP_ENV=dev` (дефолт) мутировать разрешено loopback-цели и нормализованные записи `DB_EXTRA_TARGETS`; при `APP_ENV=prod` цели не проверяются — декларация роли и есть разрешение. Три точки входа (`alembic/env.py`, `cli.py`, `main.py::_sweep_stuck_documents`) передают guard'у ту цель, которую собираются мутировать. Профили развёртывания остаются рецептами эксплуатации: LLM переключается существующим `LLM_PROVIDER`, БД — `DATABASE_URL`, роль — `APP_ENV`.

**Tech Stack:** Python 3.12, pydantic-settings v2, SQLAlchemy (sync), Alembic, pytest, click; task runner — `just`.

**Спека:** [docs/superpowers/specs/2026-07-27-deploy-env-contract-design.md](../specs/2026-07-27-deploy-env-contract-design.md). Ветка `feat/local-dev-db`, базовая ревизия `7edca85` (PR #45, черновик).

## Global Constraints

- Все команды — только через `just`, никогда `cd backend && ...` напрямую. Shell на Windows: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd> 2>&1"`.
- `backend/.env` и `.env.test` агент **не правит** (правило `AGENTS.md`). Правки этих файлов — отдельные MANUAL-задачи.
- Исторические файлы в `backend/alembic/versions/` не редактировать. Новых миграций эта задача не создаёт.
- Докстринг у каждой функции/метода, включая тесты и приватные `_helpers`. Цель — 100 % в изменённых файлах.
- Перед завершением каждой задачи — `just lint` и `just test` зелёные.
- Новых зависимостей не добавлять.
- Задача = коммит. Внутри задачи шаги мелкие, коммит один, в конце. Дробить коммит 2 нельзя: половинчато отрефакторенный guard оставляет дерево с красными тестами, то есть не даёт независимо проверяемого результата.
- **Порядок исполнения — 1 → 3 → 4 → 5 → 6 → 8 → 7.** Task 2 отменена ревизией §12 спеки (номер сохранён, действий не требует), Task 8 добавлена той же ревизией и идёт перед Task 7, потому что финальная проверка и снятие черновика должны видеть вычищенное состояние.
- Рефактор ложится в `feat/local-dev-db` **до мержа** PR #45 — `main` вендорную ось не видит никогда (AC-12).
- Baseline пропущенных тестов — зелёный прогон CI `backend-tests` на `7edca85` (run 30288104267): `645 passed, 6 skipped`. Счётчик skipped не должен вырасти (AC-11).

---

### Task 1: `config.py` — абсолютный `env_file`, роутер настроек, снятие пина

Предусловие для Task 3: guard читает конфиг через `Settings()`, и до этой правки резолв `.env` зависит от CWD процесса.

**Files:**
- Modify: `backend/config.py` (импорты, `model_config`, новые поля)
- Modify: `backend/routers/settings.py` (конструирование `Settings`)
- Modify: `backend/tests/integration/test_settings.py` (снятие пина)
- Modify: `docs/TECH_DEBT.md` (закрыть запись про CWD-относительный `env_file`)

**Interfaces:**
- Consumes: ничего (первая задача).
- Produces: `Settings.APP_ENV: Literal["dev","prod"]` (дефолт `"dev"`), `Settings.DB_EXTRA_TARGETS: str` (дефолт `""`). `Settings.model_config.env_file` — абсолютный `Path`. Task 3 читает оба поля.

- [ ] **Step 1: Прочитать текущее состояние**

Прочитать `backend/config.py` целиком и `backend/routers/settings.py` строки 1-50. Нужны: строка `model_config`, блок импортов, место объявления `LLM_PROVIDER` (после него добавляются новые поля), вызов `resolved_openrouter_model(Settings())` в `get_settings`.

- [ ] **Step 2: Написать падающий тест на абсолютный `env_file`**

Создать `backend/tests/unit/test_config_env_file.py`:

```python
"""Тесты резолва env_file в Settings (независимость от CWD)."""
from pathlib import Path

import pydantic
import pytest

from config import Settings


def test_env_file_is_absolute():
    """env_file — абсолютный путь, иначе значения зависят от CWD процесса."""
    env_file = Settings.model_config["env_file"]
    assert Path(env_file).is_absolute()


def test_env_file_points_at_backend_dir():
    """env_file указывает на backend/.env рядом с config.py."""
    env_file = Path(Settings.model_config["env_file"])
    assert env_file.name == ".env"
    assert env_file.parent == Path(__file__).resolve().parent.parent.parent


def test_app_env_defaults_to_dev(monkeypatch):
    """APP_ENV по умолчанию dev — fail-safe: dev защищён из коробки."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    assert Settings().APP_ENV == "dev"


def test_app_env_rejects_invalid_value(monkeypatch):
    """Невалидное значение APP_ENV падает на валидации, а не молча."""
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_db_extra_targets_empty_env_means_empty_list(monkeypatch):
    """Пустое значение в окружении означает «список пуст».

    Пиним через setenv, а не delenv: env_file абсолютный, и Settings() прочитал бы
    backend/.env, где после MANUAL-задачи (Task 2) лежит непустой список. Тогда
    тест был бы зелёным в CI (файла нет) и красным на машине разработчика.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    assert Settings().DB_EXTRA_TARGETS == ""
```

- [ ] **Step 3: Запустить тест — убедиться, что падает**

Run: `just test-unit-k "test_config_env_file"`
Expected: FAIL — `test_env_file_is_absolute` падает (`env_file` = `".env"`, относительный), `test_app_env_defaults_to_dev` падает с `AttributeError`/`ValidationError` (поля нет).

- [ ] **Step 4: Добавить `Path` в импорты `config.py`**

В блок импортов `backend/config.py` добавить:

```python
from pathlib import Path
```

- [ ] **Step 5: Сделать `env_file` абсолютным**

Заменить в `backend/config.py`:

```python
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

на:

```python
    # env_file абсолютным: относительный путь делал значения зависимыми от CWD
    # процесса (закрыто по docs/TECH_DEBT.md). Роутер настроек передаёт свой
    # ENV_PATH через Settings(_env_file=...).
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env", extra="ignore"
    )
```

- [ ] **Step 6: Добавить поля `APP_ENV` и `DB_EXTRA_TARGETS`**

В `backend/config.py`, сразу после объявления `LLM_PROVIDER`, добавить:

```python
    # Роль окружения. ИНВАРИАНТ: единственный потребитель APP_ENV — db_guard.
    # Новый потребитель обязан пересмотреть деплойную таблицу спеки: при
    # DATABASE_URL на loopback забытый APP_ENV=prod НЕ роняет старт — guard
    # разрешает loopback безусловно, и процесс молча работает в dev-режиме.
    APP_ENV: Literal["dev", "prod"] = "dev"
    # Дополнительные цели, мутируемые при APP_ENV=dev: host:port/dbname через
    # запятую. Loopback разрешён и без этого списка; каждая запись — полная
    # тройка (без порта или dbname — ошибка валидации в db_guard).
    DB_EXTRA_TARGETS: str = ""
```

`Literal` в `config.py` уже импортирован (используется для `LLM_PROVIDER`) — проверить и не дублировать импорт.

- [ ] **Step 7: Запустить тест — убедиться, что проходит**

Run: `just test-unit-k "test_config_env_file"`
Expected: PASS, 5 тестов.

- [ ] **Step 8: Передать `ENV_PATH` в `Settings` из роутера настроек**

В `backend/routers/settings.py`, в функции `get_settings`, заменить:

```python
        "model": settings.GATEWAY_MODEL if is_gateway
        else resolved_openrouter_model(Settings()),
```

на:

```python
        # Settings(_env_file=ENV_PATH): роутер держит абсолютный ENV_PATH для
        # записи через set_key, и читать он обязан тот же файл. Без этого свежий
        # Settings() читал бы backend/.env в обход подменённого в тестах ENV_PATH.
        "model": settings.GATEWAY_MODEL if is_gateway
        else resolved_openrouter_model(Settings(_env_file=ENV_PATH)),
```

- [ ] **Step 9: Снять пин `OPENROUTER_MODEL` из теста**

В `backend/tests/integration/test_settings.py`, в `test_get_settings`, удалить блок:

```python
    # Подмены ENV_PATH недостаточно: роутер собирает свежий Settings(), а у того
    # свой model_config(env_file=".env") — реальный backend/.env читается в обход
    # нашего fake_env. Если разработчик задал там OPENROUTER_MODEL (а .env.example
    # это предписывает), namespaced-значение выигрывает алиас-цепочку §1 и тест
    # падает на чужом значении. В CI файла .env нет, поэтому там было зелено.
    # Тест проверяет ветку legacy AI_MODEL → namespaced гасим явно (пустое
    # значение = отсутствие по guard §1), переменная окружения бьёт dotenv.
    monkeypatch.setenv("OPENROUTER_MODEL", "")
```

Строку `monkeypatch.setattr("routers.settings.ENV_PATH", str(fake_env))` **оставить** — она теперь работает по назначению.

- [ ] **Step 10: Проверить, что тест зелёный без пина**

Run: `just test-int-local-k "test_get_settings"`
Expected: PASS. Это и есть AC-0: пин лечил симптом при незакрытом корне, корень закрыт — пин не нужен.

- [ ] **Step 11: Закрыть запись в TECH_DEBT**

В `docs/TECH_DEBT.md` найти запись «`Settings.model_config` читает `.env` по CWD-относительному пути» и заменить `- [ ]` на `- [x]`, дописав в конец тела:

```markdown
  **Закрыто 2026-07-27** (спека `2026-07-27-deploy-env-contract-design.md`, AC-0): `env_file`
  абсолютный, роутер передаёт `Settings(_env_file=ENV_PATH)`, пин `OPENROUTER_MODEL=""` в
  `test_get_settings` снят.
```

- [ ] **Step 12: Полная проверка**

Run: `just lint` — ожидается «All checks passed!»
Run: `just test` — ожидается зелёный, `6 skipped` без роста.

- [ ] **Step 13: Коммит**

```bash
git add backend/config.py backend/routers/settings.py backend/tests/integration/test_settings.py backend/tests/unit/test_config_env_file.py docs/TECH_DEBT.md
git commit -m "$(cat <<'EOF'
refactor(config): абсолютный env_file + APP_ENV/DB_EXTRA_TARGETS

Предусловие рефактора guard'а: он читает конфиг через Settings(), а
CWD-относительный env_file делал резолв зависимым от рабочего каталога
процесса. Роутер настроек теперь передаёт свой абсолютный ENV_PATH через
Settings(_env_file=...) — до этого он держал ENV_PATH для записи через set_key,
а читал в обход него.

Следствие: пин OPENROUTER_MODEL="" в test_get_settings больше не нужен — он
лечил симптом при незакрытом корне. Формально частичная отмена a8601aa.

Добавлены поля APP_ENV (Literal dev|prod, дефолт dev) и DB_EXTRA_TARGETS —
их потребляет следующий коммит. У APP_ENV зафиксирован инвариант единственного
потребителя.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: ОТМЕНЕНА ревизией 2026-07-28 — действий не требует

> **Ничего не делать. Перейти к Task 3.**
>
> Задача была MANUAL-стопом: человек вносил цель Neon test-ветки в `DB_EXTRA_TARGETS` **до** того, как Task 3 снесёт `ALLOW_NEON_WRITES` — иначе путь «тесты против Neon test-ветки» оказывался заблокирован в окне между задачами.
>
> **Решение владельца от 2026-07-28: Neon test-ветка исключена** (спека §12). Тесты идут на локальном кластере. Значит `DB_EXTRA_TARGETS` остаётся **пустым**, окно не открывается, предусловия у Task 3 больше нет, подтверждение человека не требуется.
>
> Номер задачи сохранён намеренно: на «Task 2 между Task 1 и Task 3» ссылаются Global Constraints и таблица покрытия в Self-Review. Перенумеровка семи задач ради удаления одной — churn с риском битых ссылок.
>
> `DB_EXTRA_TARGETS` при этом **остаётся в коде** — обоснование в спеке §5 (цена отсутствия скоупленного выхода, а не Rule of Two). Задача 8 ниже вычищает Neon из тестовой инфраструктуры.

**Files:** изменений нет.

- [ ] **Step 1: Отметить задачу выполненной и идти дальше**

Коммита нет, файлов нет, вопросов человеку нет.

---

### Task 2 — историческая запись: НЕ ВЫПОЛНЯТЬ

> **Чекбоксов в этом блоке нет намеренно.** Шапка плана объявляет, что незакрытые
> `- [ ]` отслеживаются как действия; исполнитель, сканирующий их, исполнил бы отменённый
> MANUAL-стоп и запросил у человека цель несуществующей Neon-ветки. Поэтому шаги ниже
> записаны обычными буллетами — это архив рассуждения, а не работа.
>
> **Что предписывалось до ревизии §12:**
>
> - *Позиция шага.* С момента, когда Task 3 сносит `ALLOW_NEON_WRITES`, путь «тесты против
>   Neon test-ветки» (`just test-backend` без локального кластера, `just db-test-migrate`)
>   оказывался заблокирован guard'ом до появления `DB_EXTRA_TARGETS`. Окно узкое — на
>   дев-машине кластер есть, у CI обе цели loopback, — но при правильной позиции шага его
>   не было вовсе.
> - *Файл.* `backend/.env`, правит человек (правило `AGENTS.md`).
> - *Шаг 1.* Выдать человеку инструкцию: внести в `DB_EXTRA_TARGETS` нормализованную тройку
>   `host:port/dbname` цели test-ветки, например
>   `ep-example-0000.c-3.eu-central-1.aws.neon.tech:5432/neondb` — хост синтетический по той
>   же конвенции, что в тестах guard'а. Порт `5432` относился к **удалённой** цели: в
>   `TEST_DATABASE_URL` порт не указан, и `normalize_target` подставлял `DEFAULT_PG_PORT`.
>   Локальный кластер в список не попадал никогда — `localhost` это loopback, разрешён без
>   списка, и его `5459` здесь был ни при чём.
> - *Шаг 2.* Дождаться подтверждения человека и проверить, что значение читается и
>   разбирается, без вывода секретов.
> - *Шаг 3.* Коммита нет — `backend/.env` в `.gitignore`.
>
> **Почему отменено:** решение владельца от 2026-07-28 — Neon test-ветка исключена
> (спека §12). `DB_EXTRA_TARGETS` остаётся пустым, окно не открывается, останавливаться
> негде. Задача 8 вычищает Neon из тестовой инфраструктуры.

---

### Task 3: Рефактор оси guard'а

**Files:**
- Modify: `backend/db_guard.py` (переписывается целиком)
- Modify: `backend/main.py` (вызов в `_sweep_stuck_documents`)
- Modify: `backend/cli.py` (`_guard`)
- Modify: `backend/alembic/env.py` (импорт и вызов)
- Modify: `backend/tests/conftest.py` (снять грант и `is_neon_url`, сохранить `_test`-барьер)
- Modify: `backend/tests/unit/test_db_guard.py` (переписывается под новую ось)
- Modify: `backend/tests/unit/test_db_guard_wiring.py` (фикстуры + литерал в source-check)
- Modify: `justfile` (снять `ALLOW_NEON_WRITES` из `db-test-migrate` и комментариев)
- Modify: `AGENTS.md`, `docs/testing.md` (снять упоминания переменной; полное переписывание доков — Task 6)

**Interfaces:**
- Consumes: `Settings.APP_ENV`, `Settings.DB_EXTRA_TARGETS` (Task 1).
- Produces:
  - `db_guard.ensure_mutation_allowed(url: str, action: str) -> None` — заменяет `ensure_write_allowed`;
  - `db_guard.normalize_target(url: str) -> str` → `"host:port/dbname"`;
  - `db_guard.parse_extra_targets(raw: str) -> frozenset[str]`, бросает `ValueError` на неполной записи;
  - `db_guard.is_target_allowed(url: str, extra: frozenset[str]) -> bool`;
  - `db_guard.safe_host(url: str) -> str` (сохраняется);
  - `db_guard.LOOPBACK_HOSTS`, `db_guard.UNKNOWN_HOST`, `db_guard.DEFAULT_PG_PORT`.
  - Удаляются: `ALLOW_ENV`, `is_neon_url`, `neon_writes_allowed`, `ensure_write_allowed`.

- [ ] **Step 1: Написать падающие тесты нормализации и решения**

Заменить содержимое `backend/tests/unit/test_db_guard.py` на:

```python
"""Тесты guard'а от мутации незапланированной БД (db_guard)."""
import pytest

from db_guard import (
    UNKNOWN_HOST,
    ensure_mutation_allowed,
    is_target_allowed,
    normalize_target,
    parse_extra_targets,
    safe_host,
)

# Хост синтетический: реальный прод-эндпоинт в репозитории и в CI-логах при
# падении не нужен — ни один тест не зависит от его настоящего значения.
REMOTE_HOST = "ep-example-0000.c-3.eu-central-1.aws.neon.tech"
REMOTE_URL = (
    f"postgresql+psycopg://test_owner:secret-pw@{REMOTE_HOST}/neondb"
    "?sslmode=require&channel_binding=require"
)
LOCAL_URL = "postgresql+psycopg://postgres@localhost:5459/udp_dev"


@pytest.fixture(autouse=True)
def _hermetic_guard_env(monkeypatch):
    """Пин APP_ENV и DB_EXTRA_TARGETS через process env.

    Без пина тесты читали бы реальный backend/.env: env_file абсолютный, а
    инструкция плана предписывает вписать туда DB_EXTRA_TARGETS. Тогда «дефолт
    пустой» был бы зелёным в CI (файла нет) и красным у каждого, кто инструкцию
    выполнил. Process env бьёт env_file — тем же механизмом, что и пин.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")


def test_normalize_target_builds_triple():
    """DSN → host:port/dbname."""
    assert normalize_target(LOCAL_URL) == "localhost:5459/udp_dev"


def test_normalize_target_drops_query_params():
    """Query-параметры отбрасываются: различие только в них — та же цель."""
    a = "postgresql+psycopg://u@h.example.com:5432/db?sslmode=require"
    b = "postgresql+psycopg://u@h.example.com:5432/db"
    assert normalize_target(a) == normalize_target(b) == "h.example.com:5432/db"


def test_normalize_target_defaults_port():
    """Отсутствующий порт нормализуется в 5432."""
    assert normalize_target("postgresql://u@h.example.com/db") == "h.example.com:5432/db"


def test_normalize_target_lowercases_host():
    """Регистр хоста не создаёт вторую цель."""
    assert normalize_target("postgresql://u@H.Example.COM/db") == "h.example.com:5432/db"


def test_normalize_target_handles_broken_dsn():
    """Битый DSN не роняет нормализацию и даёт нераспознанный хост."""
    assert normalize_target("postgresql://u@[bad:ipv6/db").startswith(UNKNOWN_HOST)


def test_normalize_target_marks_empty_host():
    """Пустой hostname показывается явным маркером, а не пустой строкой."""
    assert normalize_target("postgresql:///db") == f"{UNKNOWN_HOST}:5432/db"


def test_safe_host_hides_credentials():
    """В хосте нет ни пользователя, ни пароля — строка уходит в логи."""
    host = safe_host(REMOTE_URL)
    assert host == REMOTE_HOST
    assert "secret-pw" not in host
    assert "test_owner" not in host


def test_parse_extra_targets_empty():
    """Пустая строка — пустой список."""
    assert parse_extra_targets("") == frozenset()


def test_parse_extra_targets_multiple():
    """Список через запятую, пробелы игнорируются, хост в нижний регистр."""
    raw = "H.Example.com:5432/db1, other.example.com:6000/db2"
    assert parse_extra_targets(raw) == frozenset(
        {"h.example.com:5432/db1", "other.example.com:6000/db2"}
    )


@pytest.mark.parametrize("entry", ["h.example.com/db", "h.example.com:5432", "h.example.com"])
def test_parse_extra_targets_rejects_partial_entry(entry):
    """Неполная тройка — ошибка: иначе allowlist расширился бы до уровня хоста."""
    with pytest.raises(ValueError, match="host:port/dbname"):
        parse_extra_targets(entry)


def test_normalize_target_handles_ipv6_literal():
    """IPv6-литерал в скобках даёт хост без скобок."""
    assert normalize_target("postgresql://u@[::1]:5459/db") == "::1:5459/db"


def test_ipv6_loopback_allowed_without_list():
    """IPv6-loopback разрешён через LOOPBACK_HOSTS, а не через allowlist.

    В DB_EXTRA_TARGETS IPv6 выразить нельзя: разбор записи режет по первому
    двоеточию, и `::1:5459/db` не разбирается. Это осознанный YAGNI — единственная
    нужная IPv6-цель это loopback, а он покрыт LOOPBACK_HOSTS. Появится реальная
    не-loopback IPv6-цель — разбор придётся усложнить.
    """
    assert is_target_allowed("postgresql://u@[::1]:5459/db", frozenset()) is True


def test_loopback_allowed_without_list():
    """Loopback разрешён безусловно — любая база, без DB_EXTRA_TARGETS."""
    assert is_target_allowed(LOCAL_URL, frozenset()) is True
    assert is_target_allowed("postgresql://u@127.0.0.1:5432/anything", frozenset()) is True


def test_remote_target_needs_list():
    """Не-loopback цель без записи в allowlist запрещена."""
    assert is_target_allowed(REMOTE_URL, frozenset()) is False


def test_remote_target_allowed_when_listed():
    """Не-loopback цель разрешена, если её нормализованная тройка в списке."""
    extra = parse_extra_targets(f"{REMOTE_HOST}:5432/neondb")
    assert is_target_allowed(REMOTE_URL, extra) is True


def test_dev_blocks_unlisted_target():
    """При APP_ENV=dev неразрешённая цель прерывает операцию."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    assert "APP_ENV=dev" in str(exc.value)


def test_dev_allows_loopback():
    """При APP_ENV=dev loopback проходит без списка."""
    ensure_mutation_allowed(LOCAL_URL, "alembic")


def test_prod_skips_target_check(monkeypatch):
    """При APP_ENV=prod цели не проверяются — роль и есть разрешение."""
    monkeypatch.setenv("APP_ENV", "prod")
    ensure_mutation_allowed(REMOTE_URL, "alembic")


def test_error_names_both_exits():
    """Текст ошибки называет оба выхода: APP_ENV=prod и DB_EXTRA_TARGETS."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    message = str(exc.value)
    assert "APP_ENV=prod" in message
    assert "DB_EXTRA_TARGETS" in message


def test_error_target_is_copy_pasteable():
    """Отвергнутая цель напечатана в формате, который принимает DB_EXTRA_TARGETS.

    Требование, не совпадение: пользователь копирует строку из ошибки в
    переменную без редактирования.
    """
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    target = normalize_target(REMOTE_URL)
    assert target in str(exc.value)
    assert parse_extra_targets(target) == frozenset({target})


def test_error_leaks_no_password():
    """Пароль из DSN не попадает в сообщение."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "cli create-superuser")
    assert "secret-pw" not in str(exc.value)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `just test-unit-k "test_db_guard"`
Expected: FAIL — `ImportError`: `normalize_target`, `parse_extra_targets`, `is_target_allowed`, `ensure_mutation_allowed`, `UNKNOWN_HOST` в `db_guard` не существуют.

- [ ] **Step 3: Переписать `backend/db_guard.py`**

Заменить содержимое файла целиком:

```python
"""Guard от мутации незапланированной БД.

Ось — **роль окружения**, а не вендор БД: прод не обязательно Neon. Развёртывание
возможно и на сервере компании (БД локальная или в докере), и на стороннем
хостинге. Вендорная ось не защищала первый случай и ломала второй.

При `APP_ENV=dev` (дефолт) мутировать разрешено loopback-цели и нормализованные
записи `DB_EXTRA_TARGETS`. При `APP_ENV=prod` цели не проверяются: декларация роли
и есть разрешение — цель прода и есть его `DATABASE_URL`.

Спека: docs/superpowers/specs/2026-07-27-deploy-env-contract-design.md
"""
from urllib.parse import urlsplit

DEFAULT_PG_PORT = 5432
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
UNKNOWN_HOST = "<нераспознанный хост>"


def safe_host(url: str) -> str:
    """Хост из DSN без креденшелов — пароль не должен попасть в текст ошибки."""
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def normalize_target(url: str) -> str:
    """DSN → нормализованная цель `host:port/dbname`.

    Единица сравнения включает имя БД, потому что CI различает свои цели только
    им: `localhost:5432/postgres` против `localhost:5432/udp_test`.
    Query-параметры отбрасываются — цели, различающиеся только ими, это одна
    цель. Пустой или неразбираемый хост даёт `UNKNOWN_HOST`: такая цель не
    loopback и ни с чем не совпадает (fail-closed).
    """
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower().rstrip(".") or UNKNOWN_HOST
        port = parts.port or DEFAULT_PG_PORT
        dbname = (parts.path or "").lstrip("/")
    except ValueError:
        return f"{UNKNOWN_HOST}:{DEFAULT_PG_PORT}/"
    return f"{host}:{port}/{dbname}"


def parse_extra_targets(raw: str) -> frozenset[str]:
    """Разобрать `DB_EXTRA_TARGETS` — список `host:port/dbname` через запятую.

    Каждая запись обязана быть полной тройкой. Запись без порта или без имени БД
    незаметно расширила бы allowlist до уровня хоста, что противоречит выбору
    единицы сравнения, поэтому это ошибка, а не «любая база на этом хосте».

    Raises:
        ValueError: запись не имеет вида `host:port/dbname`.
    """
    targets = set()
    for chunk in (raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        host, _, tail = entry.partition(":")
        port, _, dbname = tail.partition("/")
        if not host or not port.isdigit() or not dbname:
            raise ValueError(
                f"DB_EXTRA_TARGETS: запись '{entry}' должна иметь вид host:port/dbname"
            )
        targets.add(f"{host.lower().rstrip('.')}:{int(port)}/{dbname}")
    return frozenset(targets)


def is_target_allowed(url: str, extra: frozenset[str]) -> bool:
    """Разрешена ли цель в dev: loopback (любая база) или запись из allowlist."""
    if safe_host(url) in LOOPBACK_HOSTS:
        return True
    return normalize_target(url) in extra


def ensure_mutation_allowed(url: str, action: str) -> None:
    """Прервать `action`, если цель не разрешена для текущего `APP_ENV`.

    Args:
        url: DSN цели, которую собираются мутировать. Именно цель операции, а не
            `settings.DATABASE_URL`: в `db-test-migrate` она приходит из process
            env, в conftest — из `cfg.set_main_option`.
        action: что собирались сделать — попадёт в текст ошибки.

    Raises:
        RuntimeError: `APP_ENV=dev`, цель не loopback и отсутствует в
            `DB_EXTRA_TARGETS`.
    """
    # Импорт внутри функции: db_guard остаётся импортируемым без конфига, а
    # Settings() собирается на момент вызова — тестовые monkeypatch действуют.
    from config import Settings

    s = Settings()
    if s.APP_ENV == "prod":
        return
    extra = parse_extra_targets(s.DB_EXTRA_TARGETS)
    if is_target_allowed(url, extra):
        return
    target = normalize_target(url)
    listed = ", ".join(sorted(extra)) if extra else "сейчас пусто"
    raise RuntimeError(
        f"{action}: цель {target} не разрешена при APP_ENV=dev.\n"
        f"Разрешено: loopback + DB_EXTRA_TARGETS ({listed}).\n"
        "Если это развёрнутое окружение — выставьте APP_ENV=prod.\n"
        f"Если это дев-цель — добавьте {target} в DB_EXTRA_TARGETS в backend/.env."
    )
```

**Проверить при переносе:** `.rstrip(".")` в `normalize_target` и `safe_host` — снятие завершающей точки FQDN. Пустой аргумент (`.rstrip("")`) молча ничего не снимет, и `h.example.com.` станет второй целью относительно `h.example.com`.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `just test-unit-k "test_db_guard and not wiring"`
Expected: PASS, **23 item'а** — 21 тест-функция, из которых `test_parse_extra_targets_rejects_partial_entry` параметризована ×3 (20 непараметризованных + 3). Счёт получен подсчётом `def test_` в блоке Step 1, а не прибавлением к прошлой оценке: первые две редакции плана ошибались здесь именно из-за этого.

- [ ] **Step 5: Перевести три call-site на новое имя**

`backend/alembic/env.py` — заменить импорт и вызов:

```python
from db_guard import ensure_mutation_allowed  # noqa: E402
```

```python
# Fail-fast до любого DDL. Стоит выше engine_from_config/fileConfig — коннекта
# к этому моменту ещё не было. Покрывает и online-, и offline-режим: модуль
# исполняется до ветвления.
ensure_mutation_allowed(_db_url, "alembic")
```

`backend/cli.py` — заменить импорт и тело `_guard`:

```python
from db_guard import ensure_mutation_allowed
```

```python
def _guard(action: str) -> None:
    """Отказаться мутировать БД, если цель не разрешена для текущего APP_ENV."""
    ensure_mutation_allowed(settings.DATABASE_URL, f"cli {action}")
```

`backend/main.py` — в `_sweep_stuck_documents` заменить блок под `if session_factory is None:`:

```python
        from config import settings
        from database import SessionLocal
        from db_guard import ensure_mutation_allowed

        ensure_mutation_allowed(settings.DATABASE_URL, "startup-sweep")
        session_factory = SessionLocal
```

Комментарий над блоком оставить без изменений — он объясняет, почему guard только на этой ветке.

- [ ] **Step 6: Обновить тесты обвязки**

В `backend/tests/unit/test_db_guard_wiring.py`:

1. Заменить импорт `from db_guard import ALLOW_ENV` на ничего (константа удалена).
2. Заменить фикстуру `_neon_target_without_permission` на:

```python
@pytest.fixture(autouse=True)
def _unlisted_target_in_dev(monkeypatch):
    """Цель не разрешена: APP_ENV=dev, пустой allowlist, удалённый хост.

    DB_EXTRA_TARGETS пиним явно: env_file абсолютный, и в реальном backend/.env
    список может быть непустым — иначе тест был бы зелёным в CI и красным на
    машине разработчика.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.setattr(cli.settings, "DATABASE_URL", REMOTE_URL, raising=False)
```

3. Заменить константу:

```python
REMOTE_URL = (
    "postgresql+psycopg://test_owner:secret-pw@"
    "ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb"
)
```

4. **Переименовать оба cli-теста и заменить вендорную ассерцию.** `test_cli_commands_refuse_neon_target` → `test_cli_commands_refuse_unlisted_target`; `test_cli_commands_pass_guard_when_allowed` → `test_cli_commands_pass_guard_when_prod`. В первом заменить ассерцию — **новый текст ошибки слова «Neon» не содержит, без этой правки три теста красные**:

```python
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "APP_ENV=dev" in str(result.exception)
```

Докстринг первого: «Каждая мутирующая cli-команда отказывается мутировать неразрешённую цель». Во втором заменить снятие барьера с `monkeypatch.setenv(ALLOW_ENV, "1")` на `monkeypatch.setenv("APP_ENV", "prod")`, докстринг — «При APP_ENV=prod guard пропускает — падение уже на уровне БД».

5. В `test_startup_sweep_refuses_neon_target` переименовать в `test_startup_sweep_refuses_unlisted_target`, заменить `monkeypatch.delenv(ALLOW_ENV, ...)` на `monkeypatch.setenv("APP_ENV", "dev")` и `monkeypatch.setenv("DB_EXTRA_TARGETS", "")`, оставить `pytest.raises(RuntimeError, match="startup-sweep")`. Докстринг — «Sweep на старте не идёт в неразрешённую цель».

Пройти по файлу и снять оставшиеся вендорные упоминания в докстрингах (фикстура, тела тестов) — слово «Neon» в этом файле не должно остаться нигде: ось больше не про вендора.

6. **Литерал в source-check.** В `test_alembic_env_guards_before_engine` заменить:

```python
    guard_at = text.index("ensure_write_allowed(")
```

на:

```python
    guard_at = text.index("ensure_mutation_allowed(")
```

Без этой правки тест падает `ValueError` из `index`, и следующий читатель не поймёт, дефект это или хвост переименования.

- [ ] **Step 7: Добавить регрессионный тест на `override=False`**

В конец `backend/tests/unit/test_db_guard_wiring.py`:

```python
def test_alembic_env_loads_dotenv_without_override():
    """load_dotenv в alembic/env.py не перетирает process env.

    От этого зависит корректность just db-test-migrate: рецепт подаёт
    DATABASE_URL=$TEST_DATABASE_URL через process env, а backend/.env содержит
    прод-DSN. Станет override=True — рецепт начнёт мигрировать ПРОД.
    Проверка по исходнику: рантайм-запуск env.py потребовал бы живой БД.
    """
    source = Path(main.__file__).parent / "alembic" / "env.py"
    text = source.read_text(encoding="utf-8")
    call_at = text.index("load_dotenv(")
    call = text[call_at : text.index(")", call_at) + 1]
    assert "override" not in call, (
        f"load_dotenv вызван с override — это ломает db-test-migrate: {call}"
    )
```

- [ ] **Step 8: Запустить тесты обвязки**

Run: `just test-unit-k "wiring"`
Expected: PASS, **10 item'ов** — два параметризованных cli-теста по ×3, плюс два sweep-теста, source-check порядка и новый source-check `override`.

- [ ] **Step 9: Проверить обвязку мутацией**

Удалить строку `_guard("create-org")` из `backend/cli.py`, запустить `just test-unit-k "wiring"`.
Expected: FAIL на `test_cli_commands_refuse_unlisted_target[create-org-args1]` (имя после переименования из Step 6) с сообщением «SessionLocal() вызван — guard сработал слишком поздно».
Вернуть строку, перезапустить — PASS. Тесты, которые нельзя сломать, ничего не проверяют.

- [ ] **Step 10: Снять грант и `is_neon_url` из conftest**

В `backend/tests/conftest.py` удалить блок:

```python
    # db_guard: Neon test-ветка — легитимная цель для миграций (так работает
    # test-backend-integration, когда локального кластера нет). Разрешаем точечно
    # и только когда цель действительно Neon, с откатом на teardown — иначе
    # process-global переменная протекала бы в тесты самого guard'а.
    from db_guard import is_neon_url

    allow_patch = pytest.MonkeyPatch()
    if is_neon_url(test_url):
        allow_patch.setenv("ALLOW_NEON_WRITES", "1")
```

и строку `allow_patch.undo()` в конце фикстуры `db_engine`. Разрешение теперь приходит из конфига (`DB_EXTRA_TARGETS`), а не из рантайм-мутации.

**Барьер (a) — перевести на нормализованное сравнение.** Это и есть поглощение, заявленное спекой §5: сырое строковое равенство пропускает цели, различающиеся только query-параметрами. Заменить:

```python
    prod_url = Settings().DATABASE_URL
    if prod_url and test_url == prod_url:
        pytest.skip(
            "TEST_DATABASE_URL совпадает с DATABASE_URL — отказ от DROP SCHEMA на проде"
        )
```

на:

```python
    from db_guard import normalize_target

    prod_url = Settings().DATABASE_URL
    if prod_url and normalize_target(test_url) == normalize_target(prod_url):
        pytest.skip(
            "TEST_DATABASE_URL и DATABASE_URL — одна цель после нормализации "
            "(host:port/dbname); отказ от DROP SCHEMA на проде"
        )
```

Сырое равенство пропускало бы `...\/neondb?sslmode=require` против `...\/neondb` как разные цели.

**Барьер (b) — сохранить без изменений** и дописать комментарий:

```python
    # НЕ ПОГЛОЩАЕТСЯ allowlist'ом guard'а: udp_dev — легитимная цель для
    # миграций и приложения, то есть она В списке, но DROP SCHEMA на ней
    # катастрофа. Членство в allowlist выражает «можно мутировать», а не
    # «можно разрушить схему». Это разные права.
```

- [ ] **Step 11: Снять `ALLOW_NEON_WRITES` из justfile**

Заменить рецепт:

```
# Накатить миграции на тестовую БД (TEST_DATABASE_URL)
db-test-migrate:
    cd backend && DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head
```

В комментарии к `db_env` удалить фразу про `ALLOW_NEON_WRITES=1`, оставив описание значений `local` / `env`.

- [ ] **Step 12: Снять упоминания переменной из AGENTS.md и docs/testing.md**

Заменить в `AGENTS.md` строку про Neon на:

```markdown
Рецепты с данными (`dev-backend`, `db-migrate`, `create-*`) идут в **локальную** `udp_dev`. Источник строки из `.env` — `just db_target=env <рецепт>`. Мутация не-loopback цели при `APP_ENV=dev` падает на guard (`backend/db_guard.py`) — это by design.

**Прод-цель в `DB_EXTRA_TARGETS` не вносить.** Список — для долгоживущих dev-целей (Neon test-ветка, докерный `db`). Осознанная миграция прода с дев-машины — one-shot-декларацией роли: `APP_ENV=prod just db_target=env db-migrate`.
```

и строку про миграции:

```markdown
Применять — `just db-migrate` (локальная `udp_dev`) / `just db-test-migrate` (тестовая БД) / `APP_ENV=prod just db_target=env db-migrate` (прод, осознанно и разово).
```

**Почему именно так, а не «`DB_EXTRA_TARGETS` либо `APP_ENV=prod`»:** внесение прод-DSN в постоянный allowlist дев-машины навсегда глушит guard про прод — воспроизводится до-guard'овое состояние, ради ухода от которого вся задача и делается. One-shot-переменная действует только на одну команду.

В `docs/testing.md` — заменить упоминания `ALLOW_NEON_WRITES` в разделе «Guard от записи в Neon» на новую механику. Полное переписывание раздела — Task 6; здесь достаточно снять мёртвую переменную, чтобы AC-10 закрылся.

- [ ] **Step 13: Проверить AC-10**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git grep -l 'ALLOW_NEON_WRITES\|is_neon_url' -- ':!docs/superpowers' ':!docs/devlog'; echo EXIT=$?"`
Expected: пустой вывод. Архивные деревья исключены намеренно — они хранят историю решений.

- [ ] **Step 14: Полная проверка**

Run: `just lint` — «All checks passed!»
Run: `just test` — зелёный, `6 skipped` без роста.
Run: `just db-test-check` — «No new upgrade operations detected».

- [ ] **Step 15: Коммит**

```bash
git add backend/db_guard.py backend/main.py backend/cli.py backend/alembic/env.py backend/tests/conftest.py backend/tests/unit/test_db_guard.py backend/tests/unit/test_db_guard_wiring.py justfile AGENTS.md docs/testing.md
git commit -m "$(cat <<'EOF'
refactor(guard): ось — роль окружения вместо вендора БД

ALLOW_NEON_WRITES и is_neon_url удалены: ось была неверной. Прод не обязательно
Neon — при хостинге в компании вендорный guard не защищал ничего, при стороннем
хостинге с Neon блокировал легитимный старт прода, и не различал прод-Neon от
test-ветки (отсюда три разных escape hatch).

Теперь APP_ENV (dev|prod, дефолт dev) плюс allowlist DB_EXTRA_TARGETS. В dev
разрешены loopback-цели и нормализованные тройки host:port/dbname из списка;
в prod цели не проверяются — декларация роли и есть разрешение.

Имя БД входит в единицу сравнения, потому что CI различает свои цели только им.
Query-параметры отбрасываются: из-за них строковое равенство полных DSN в старой
проверке conftest было дырявым. Неполная запись в DB_EXTRA_TARGETS — ошибка, а не
«любая база на этом хосте».

Разрешение перестало быть рантайм-мутацией process env из фикстуры и стало
конфигом. Барьер «имя БД на _test» сохранён с комментарием: allowlist его не
поглощает, право на DDL-разрушение не равно праву на мутацию.

Добавлен регрессионный тест на override=False у load_dotenv в alembic/env.py —
от него зависит корректность db-test-migrate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Legacy-алиасы — одно неделимое изменение

> **AC-9, дословно из спеки:** «Снятие условия `!= "mistral-ocr"` и смена дефолта на `native` — **одно неделимое изменение**. Порознь они друг друга ломают: условие глушит warning для случая «легаси равно код-дефолту», поэтому после смены дефолта на `native` оно начнёт глушить `PDF_ENGINE=native` из легаси-`.env`.»
>
> **Это одна задача, а не две.** Не раскладывать «снять условие» и «сменить дефолт» на отдельные шаги с прогоном линта между ними: ровно в этом зазоре они ломают друг друга. Оба изменения входят в один коммит.

**Files:**
- Modify: `backend/config.py` (`resolved_openrouter_pdf_engine`, `resolved_openrouter_max_tokens`, комментарий у `AI_MAX_TOKENS`, дефолт `PDF_ENGINE`)
- Modify: `backend/tests/unit/test_config_llm.py` (тесты предупреждений)
- Modify: `docs/TECH_DEBT.md` (закрыть запись про `PDF_ENGINE`)

**Interfaces:**
- Consumes: ничего от Task 3.
- Produces: `resolved_openrouter_pdf_engine` возвращает `"native"` по умолчанию; оба legacy-алиаса (`PDF_ENGINE`, `AI_MAX_TOKENS`) при использовании пишут предупреждение через `_warn_deprecated_alias_once`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `backend/tests/unit/test_config_llm.py`:

```python
def test_pdf_engine_default_is_native():
    """Код-дефолт движка — native: mistral-ocr нестабилен на СФ с 60+ строками."""
    s = Settings(OPENROUTER_PDF_ENGINE="", PDF_ENGINE="", SECRET_KEY="x" * 32)
    assert resolved_openrouter_pdf_engine(s) == "native"


def test_pdf_engine_legacy_warns_even_when_equal_to_default(caplog):
    """Legacy PDF_ENGINE предупреждает всегда, даже если совпал с код-дефолтом.

    Раньше условие != "mistral-ocr" глушило warning ровно для значения из
    легаси-.env, из-за чего переменная не имела пути к удалению.
    """
    import config

    config._warned_deprecated_aliases.clear()
    s = Settings(OPENROUTER_PDF_ENGINE="", PDF_ENGINE="native", SECRET_KEY="x" * 32)
    with caplog.at_level("WARNING"):
        assert resolved_openrouter_pdf_engine(s) == "native"
    assert "PDF_ENGINE устарел" in caplog.text


def test_max_tokens_legacy_warns(caplog):
    """Legacy AI_MAX_TOKENS предупреждает — раньше fallback был молчаливым."""
    import config

    config._warned_deprecated_aliases.clear()
    s = Settings(OPENROUTER_MAX_TOKENS=None, AI_MAX_TOKENS=32000, SECRET_KEY="x" * 32)
    with caplog.at_level("WARNING"):
        assert resolved_openrouter_max_tokens(s) == 32000
    assert "AI_MAX_TOKENS устарел" in caplog.text
```

Проверить, что `resolved_openrouter_pdf_engine` и `resolved_openrouter_max_tokens` импортированы в файле; если нет — добавить в существующий импорт из `config`.

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `just test-unit-k "test_pdf_engine or test_max_tokens_legacy"`
Expected: FAIL — дефолт `mistral-ocr` вместо `native`; предупреждений в логе нет.

- [ ] **Step 3: Внести оба изменения в `resolved_openrouter_pdf_engine` — одним правком**

Заменить функцию целиком:

```python
def resolved_openrouter_pdf_engine(s: "Settings") -> str:
    """OPENROUTER_PDF_ENGINE → deprecated PDF_ENGINE (warning) → код-дефолт native.

    Дефолт native, а не mistral-ocr: последний нестабилен на СФ с 60+ строками,
    и фактические .env давно используют native (docs/TECH_DEBT.md).
    Предупреждение выдаётся при ЛЮБОМ использовании legacy-переменной. Прежнее
    условие `legacy != "mistral-ocr"` глушило его ровно для значения из
    легаси-.env, из-за чего переменная не имела пути к удалению; снятие условия
    и смена дефолта — одно неделимое изменение, порознь они друг друга ломают.
    """
    if s.OPENROUTER_PDF_ENGINE.strip():
        return s.OPENROUTER_PDF_ENGINE.strip()
    legacy = s.PDF_ENGINE.strip()
    if legacy:
        _warn_deprecated_alias_once(
            "PDF_ENGINE", "PDF_ENGINE устарел — используйте OPENROUTER_PDF_ENGINE")
    return legacy or "native"
```

В том же правке сменить дефолт поля:

```python
    PDF_ENGINE: str = "native"
```

- [ ] **Step 4: Добавить предупреждение в `resolved_openrouter_max_tokens`**

Сделать `AI_MAX_TOKENS` опциональным — **иначе предупреждение станет ложным для каждой чистой установки.** Сейчас поле объявлено `int = 64000`, то есть fallback-ветка это дефолтный путь любого, кто не задавал ни одной из двух переменных: безусловный warning сообщал бы про переменную, которой пользователь не касался. Глушить по `!= 64000` нельзя — это в точности бага `PDF_ENGINE`, которую чинит Step 3. Правильно — зеркалировать паттерн `OPENROUTER_MAX_TOKENS`:

```python
    AI_MAX_TOKENS: int | None = None  # deprecated-алиас OPENROUTER_MAX_TOKENS; None = не задан
```

```python
def resolved_openrouter_max_tokens(s: "Settings") -> int:
    """OPENROUTER_MAX_TOKENS → deprecated AI_MAX_TOKENS (warning) → 64000 (AC-1).

    Оба поля опциональны, поэтому «не задано» отличимо от «задано значением,
    равным дефолту» — и предупреждение выдаётся только при реальном
    использовании legacy-переменной, а не на чистой установке.
    """
    if s.OPENROUTER_MAX_TOKENS is not None:
        return s.OPENROUTER_MAX_TOKENS
    if s.AI_MAX_TOKENS is not None:
        _warn_deprecated_alias_once(
            "AI_MAX_TOKENS", "AI_MAX_TOKENS устарел — используйте OPENROUTER_MAX_TOKENS")
        return s.AI_MAX_TOKENS
    return 64000
```

Проверить `resolved_llm_parse_max_tokens` — он читает цепочку через `resolved_openrouter_max_tokens`, тип возврата `int` сохраняется. Существующие тесты (`test_config_llm.py`, строки с `_mk(AI_MAX_TOKENS=2000)` и `_mk(AI_MAX_TOKENS=64000)`) передают значение явно и остаются валидными.

Добавить парный тест в `backend/tests/unit/test_config_llm.py`:

```python
def test_max_tokens_no_warning_when_unset(caplog):
    """Чистая установка не предупреждает: ни одна из двух переменных не задана."""
    import config

    config._warned_deprecated_aliases.clear()
    s = Settings(OPENROUTER_MAX_TOKENS=None, AI_MAX_TOKENS=None, SECRET_KEY="x" * 32)
    with caplog.at_level("WARNING"):
        assert resolved_openrouter_max_tokens(s) == 64000
    assert "AI_MAX_TOKENS" not in caplog.text
```

- [ ] **Step 5: Обновить устаревший комментарий у `AI_MAX_TOKENS`**

Заменить комментарий в объявлении поля:

```python
    AI_MAX_TOKENS: int = 64000  # верхний предел вывода Claude Sonnet (~64K); при движке native промпт ~10K токенов
```

Из старого комментария убирается упоминание «prompt от mistral-ocr съедает ~24K» — оно из эпохи прежнего дефолта.

- [ ] **Step 6: Запустить тесты**

Run: `just test-unit-k "test_pdf_engine or test_max_tokens"`
Expected: PASS.

Run: `just test-backend-unit`
Expected: PASS. Смена дефолта могла задеть другие тесты `config`/`pdf_parser` — если что-то упало, читать падение: тест мог фиксировать старый дефолт `mistral-ocr` как ожидаемый, и его надо обновить, а не откатывать изменение.

- [ ] **Step 7: Закрыть запись в TECH_DEBT**

В `docs/TECH_DEBT.md` найти запись «`PDF_ENGINE`: код-дефолт расходится с документацией и рабочим значением», заменить `- [ ]` на `- [x]` и дописать:

```markdown
  **Закрыто 2026-07-27** (спека `2026-07-27-deploy-env-contract-design.md`, AC-9): дефолт
  сменён на `native` одним неделимым изменением вместе со снятием условия
  `!= "mistral-ocr"`, комментарий у `AI_MAX_TOKENS` обновлён, у обоих legacy-алиасов
  появилось предупреждение.
```

- [ ] **Step 8: Полная проверка**

Run: `just lint` и `just test` — оба зелёные.

- [ ] **Step 9: Коммит**

```bash
git add backend/config.py backend/tests/unit/test_config_llm.py docs/TECH_DEBT.md
git commit -m "$(cat <<'EOF'
fix(config): дать legacy-алиасам PDF_ENGINE и AI_MAX_TOKENS путь к удалению

Обе переменные были объявлены deprecated без механизма выхода.
resolved_openrouter_max_tokens возвращал legacy тернарником вообще без
предупреждения. У PDF_ENGINE условие `legacy != "mistral-ocr"` глушило warning
ровно для того значения, которое в легаси-.env и стоит, — то есть переменная
не предупреждала и не умирала никогда.

Снятие условия и смена код-дефолта на native — ОДНО неделимое изменение:
условие глушит случай «легаси равно код-дефолту», поэтому после смены дефолта
оно начало бы глушить PDF_ENGINE=native. Порознь они друг друга ломают.

Заодно закрыт TECH_DEBT про расхождение код-дефолта mistral-ocr с фактическим
native и обновлён комментарий у AI_MAX_TOKENS из эпохи прежнего дефолта.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Мёртвый конфиг и `.gitignore`

**Files:**
- Modify: `.env.test.example` (удалить `TEST_MODE`)
- Modify: `.gitignore` (catch-all по env-вариантам)

**Interfaces:**
- Consumes: ничего.
- Produces: ничего для последующих задач.

- [ ] **Step 1: Подтвердить, что `TEST_MODE` мёртв**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && grep -rn 'TEST_MODE' . --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git 2>&1"`
Expected: вхождения только в `.env.test`, `.env.test.example`, `docs/testing.md` (раздел отложенного E2E) и архивном плане `docs/superpowers/plans/2026-05-11-testing-infrastructure.md`. Потребителей в коде нет: `backend/routers/test_utils.py` не существует, `main.py` флаг не читает.

Если появился потребитель в коде — **остановиться** и сообщить: удаление отменяется.

- [ ] **Step 2: Удалить `TEST_MODE` из `.env.test.example`**

Удалить строку `TEST_MODE=0`. Файл `.env.test` не трогать — MANUAL-шаг ниже.

- [ ] **Step 3: Заменить точечные env-строки в `.gitignore` на catch-all**

Удалить строки `backend/.env`, `.env.test`, `.env.test.local`, `.gateway.env` и добавить в секцию Python (или отдельной секцией):

```gitignore
# env-файлы: игнорируем все варианты, кроме шаблонов .example
.env*
!.env*.example
```

Негация обязана идти **после** паттерна, иначе не действует. `backend/.env` покрывается: паттерн без слэша матчится на любом уровне.

- [ ] **Step 4: Проверить, что шаблоны остались отслеживаемыми, а рабочие файлы — нет**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git status --short && echo '--- check-ignore ---' && git check-ignore -v backend/.env .env.test backend/.env.gateway 2>&1 && echo '--- tracked examples ---' && git ls-files | grep -E '\.env.*example'"`
Expected: `backend/.env`, `.env.test`, `backend/.env.gateway` игнорируются; `backend/.env.example` и `.env.test.example` в списке отслеживаемых; в `git status` нет неожиданно появившихся env-файлов.

- [ ] **Step 5: Коммит**

```bash
git add .env.test.example .gitignore
git commit -m "$(cat <<'EOF'
chore: удалить мёртвый TEST_MODE и закрыть .gitignore по env-вариантам

TEST_MODE не имеет потребителей в коде: routers/test_utils.py не существует,
main.py флаг не читает. Единственные ссылки — .env.test(.example) и описание
ОТЛОЖЕННОГО E2E в docs/testing.md.

ВАЖНО для будущей E2E-задачи: план 2026-05-11-testing-infrastructure намеренно
вводит TEST_MODE заново вместе с /api/test/reset. Это удаление — YAGNI по
нереализованной фиче, НЕ отклонение E2E-дизайна. Строку в docs/testing.md
не трогаем: как описание плана она корректна.

.gitignore: точечные env-строки заменены на `.env*` + `!.env*.example`. Раньше
промежуточные варианты (например backend/.env.gateway) утекли бы на git add -A.
Точечная .gateway.env удалена — покрывается общим паттерном после
переименования, а мёртвая строка осталась бы приманкой для grep.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: MANUAL — сообщить человеку про два файла**

> **ВЫПОЛНЯЕТ ЧЕЛОВЕК.** Агент выводит инструкцию и продолжает — эти шаги ничего не блокируют:
>
> 1. Удалить строку `TEST_MODE=0` из корневого `.env.test`.
> 2. Переименовать `.gateway.env` → `.env.gateway-tokens` (в новой схеме имён `env` в суффиксе — выброс, и старое имя опасно похоже на шаблон `.env.gateway.example`).
>
> Оба файла в `.gitignore`, агент их не правит по правилу `AGENTS.md`.

---

### Task 6: Документация

**Files:**
- Modify: `README.md` (таблица обязательных переменных)
- Modify: `backend/.env.example` (`APP_ENV`, переформулировка `DATABASE_URL`)
- Modify: `docs/setup/neon-setup.md` (Neon — один из вариантов)
- Modify: `docs/testing.md` (раздел guard'а под новую ось)
- Modify: `docs/instructions/llm-provider.md` (сверка с контрактом имён)
- Modify: `.github/instructions/backend.instructions.md`
- Modify: `AGENTS.md` (оговорка про `.env.*.example`)
- Modify: `docs/TECH_DEBT.md` (запись про отложенные переименования)

**Interfaces:**
- Consumes: финальные имена и поведение из Task 3 и Task 4.
- Produces: ничего для последующих задач.

- [ ] **Step 1: Добавить `APP_ENV` в таблицу README**

В `README.md`, в таблицу «Обязательные переменные в `backend/.env`», добавить строку после `DATABASE_URL`:

```markdown
| `APP_ENV` | `dev` (дефолт) или `prod`. В `dev` guard разрешает мутировать только loopback-цели и `DB_EXTRA_TARGETS`; в `prod` — любые. См. [docs/testing.md](docs/testing.md) |
```

Строку `DATABASE_URL` заменить на вендор-нейтральную:

```markdown
| `DATABASE_URL` | DSN Postgres (`postgresql+psycopg://...`). Локальный кластер, Docker или managed-хостинг вроде Neon — на выбор |
```

Заодно поправить устаревший порт MinIO в строке `S3_*`: в `config.py` дефолт `http://localhost:9259`, а README называет `9000`.

- [ ] **Step 2: Добавить `APP_ENV` в `backend/.env.example` и переформулировать `DATABASE_URL`**

Заменить блок:

```dotenv
# Neon Postgres — см. docs/setup/neon-setup.md
DATABASE_URL=postgresql+psycopg://<owner>:<password>@ep-<adjective>-<noun>-<hash>.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require&connect_timeout=10
```

на:

```dotenv
# Роль окружения: dev (дефолт) | prod.
# dev  — guard разрешает мутировать только loopback и DB_EXTRA_TARGETS
# prod — цели не проверяются; на развёрнутом окружении ВЫСТАВИТЬ ЯВНО
APP_ENV=dev
# Дополнительные не-loopback цели, мутируемые в dev: host:port/dbname через запятую
# DB_EXTRA_TARGETS=

# Postgres. Любой вариант на выбор — что даёт хост:
#   локальный кластер: postgresql+psycopg://postgres@localhost:5459/udp_dev
#   managed (Neon и пр.), см. docs/setup/neon-setup.md:
#   postgresql+psycopg://<owner>:<password>@<host>/<db>?sslmode=require&channel_binding=require&connect_timeout=10
DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/udp_dev
```

- [ ] **Step 3: Переформулировать `docs/setup/neon-setup.md`**

Прочитать файл. В начало добавить абзац:

```markdown
> **Neon — один из вариантов, а не «база проекта».** Развёртывание возможно на локальном
> Postgres, в Docker или на managed-хостинге; дев-цикл по умолчанию идёт на локальном
> кластере (`docs/testing.md`, раздел «Локальная dev-БД»). Этот документ нужен, только если
> вы выбрали Neon.
```

Формулировки вида «база проекта», «наша БД» в тексте заменить на «Neon-инстанс».

- [ ] **Step 4: Переписать раздел guard'а в `docs/testing.md`**

Заменить раздел «Guard от записи в Neon» на «Guard от мутации незапланированной БД» с содержанием: ось `APP_ENV`, таблица `dev`/`prod`, нормализация `host:port/dbname` и почему имя БД входит в единицу сравнения, три call-site таблицей, формат `DB_EXTRA_TARGETS` (полная тройка), что поглощено и что нет (барьер `_test` — отдельный, с обоснованием про разные права), инварианты `db-test-migrate` (`override=False`, `-u` в shell), осознанное ужесточение (однократная запись цели вместо рантайм-гранта).

Раздел «Переключатель `db_target`» — убрать упоминание, что `env` снимает guard: теперь не снимает.

Строку про `TEST_MODE` в разделе отложенного E2E **не трогать**.

- [ ] **Step 5: Сверить `docs/instructions/llm-provider.md` и `.github/instructions/backend.instructions.md`**

Прочитать оба. Обновить всё, что называет `PDF_ENGINE`-дефолт `mistral-ocr` (теперь `native`), и добавить `APP_ENV` там, где перечисляются deploy-time переменные. В `llm-provider.md` таблица движков в §1 уже описывает `native` как рабочее значение — привести дефолт в соответствие.

- [ ] **Step 6: Добавить оговорку в `AGENTS.md`**

Заменить строку жёстких правил:

```markdown
- `.env` / `.env.test` не трогать; секреты — через переменные окружения. Шаблоны `.env.*.example` из-под правила выведены — их править можно и нужно.
```

- [ ] **Step 7: Добавить запись в TECH_DEBT про отложенные переименования**

```markdown
- [ ] **`CONFIDENCE_THRESHOLD`, `ALLOWED_ORIGINS`, `TRUSTED_PROXIES` не следуют контракту имён**
  Контракт `<ДОМЕН>_<ЧТО>` введён спекой `2026-07-27-deploy-env-contract-design.md` §4; эти
  три переменные ему не соответствуют. `CONFIDENCE_THRESHOLD` осложнён тем, что роутер
  настроек **записывает** его в `.env` через `set_key`, то есть переименование требует
  миграции существующих файлов у всех, кто сохранял настройки из UI.
  **Решение:** переименовать при следующей правке роутера настроек, с чтением обоих имён
  на период миграции.
```

- [ ] **Step 8: Проверка**

Run: `just lint` и `just test` — зелёные (доки на них не влияют, но проверить, что ничего не задето).

Проверить, что доки не разошлись с кодом:
Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && grep -rn 'mistral-ocr' docs/ README.md AGENTS.md --include='*.md' | grep -v devlog | grep -v superpowers"`
Expected: остались только упоминания `mistral-ocr` как одного из допустимых значений, не как дефолта.

- [ ] **Step 9: Коммит**

```bash
git add README.md backend/.env.example docs/setup/neon-setup.md docs/testing.md docs/instructions/llm-provider.md .github/instructions/backend.instructions.md AGENTS.md docs/TECH_DEBT.md
git commit -m "$(cat <<'EOF'
docs: контракт окружения — APP_ENV, вендор-нейтральный DATABASE_URL

Базовый .env.example подавал Neon-DSN как единственный вариант DATABASE_URL —
это и была исходная ложная привязка, из которой выросла вендорная ось guard'а.
Теперь дефолт — локальный кластер, managed-хостинг назван одним из вариантов,
а neon-setup.md открывается оговоркой «Neon — один из вариантов, а не база
проекта».

APP_ENV добавлен в таблицу обязательных README и в .env.example с указанием,
что на развёрнутом окружении его выставляют явно. Раздел guard'а в
docs/testing.md переписан под ось роли окружения.

AGENTS.md: запрет «.env / .env.test не трогать» больше не распространяется на
шаблоны .env.*.example.

Заодно: устаревший порт MinIO в README (9000 → 9259), дефолт PDF_ENGINE в
инструкциях (mistral-ocr → native), запись в TECH_DEBT про отложенные
переименования CONFIDENCE_THRESHOLD/ALLOWED_ORIGINS/TRUSTED_PROXIES.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Вычистить Neon из тестовой инфраструктуры

Ревизия спеки §12. Идёт **до** Task 7, потому что Task 7 снимает черновик с PR — финальная проверка должна видеть уже вычищенное состояние. Правит те же файлы и местами те же строки, что Task 5 и Task 6, поэтому вынос в отдельный PR гарантировал бы конфликты при нулевой экономии.

**Files:**
- Modify: `justfile` (`test-backend` → алиас, `db-test-migrate` → `{{test_db_local}}`)
- Modify: `.env.test.example` (`TEST_DATABASE_URL` на локальный `udp_test`)
- Modify: `docs/testing.md` (четыре места + снятие кавеата про недоступность Neon)
- Modify: `docs/superpowers/specs/2026-07-27-deploy-env-contract-design.md` — **уже поправлена** (§12 добавлен, четыре пометки расставлены). Задача только сверяется с ней, править не нужно.

**Interfaces:**
- Consumes: `test_db_local` из justfile (уже существует), результат Task 5 (`.env.test.example` без `TEST_MODE`) и Task 6 (доки).
- Produces: ничего для последующих задач.

- [ ] **Step 1: Свернуть fallback в `test-backend` через `pg-test-start`, не через коннект**

Заменить рецепт:

```
# Все backend-тесты. Локальный кластер обязателен: fallback на Neon свёрнут
# ревизией §12 спеки (Neon test-ветка исключена).
test-backend: test-backend-local
```

**Почему алиас, а не «нацелить TEST_DATABASE_URL на localhost»:** во втором случае контрибьютор без кластера получит psycopg-traceback, а не инструкцию. Все три `pytest.skip` в фикстуре `db_engine` проходят **раньше** первого коннекта (`create_engine` ленив), и падение случится на `DROP SCHEMA`. Алиас наследует зависимость `pg-test-start`, которая отказывает внятно: «Локальный Postgres не установлен — см. docs/testing.md».

- [ ] **Step 2: Проверить отказ без кластера — не ломая свою установку**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just --dry-run test-backend 2>&1"`
Expected: в цепочке видна зависимость `pg-test-start`. Полноценно проверить ветку «кластера нет» без его удаления нельзя — достаточно убедиться, что зависимость на месте, текст ошибки в `pg-test-start` уже покрыт Task 3 ветки `7edca85`.

- [ ] **Step 3: Перевести `db-test-migrate` на `{{test_db_local}}`**

```
# Накатить миграции на локальную тестовую БД (udp_test)
db-test-migrate: pg-test-start
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head
```

Прежний рецепт читал `$TEST_DATABASE_URL` из шелла — в justfile нет `dotenv-load`, то есть требовался ручной экспорт, которого после ухода Neon-ветки никто делать не будет. Форма совпадает с уже существующим `db-test-check`.

Инвариант `override=False` из спеки §5 **остаётся в силе**: рецепт по-прежнему подаёт `DATABASE_URL` через process env поверх `backend/.env` с прод-строкой. Регрессионный тест из Task 3 Step 7 **не трогать**. Инвариант `-u` для этого рецепта потерял объект (подстановки `$TEST_DATABASE_URL` больше нет) — флаг `set shell := ["bash", "-cu"]` из justfile **не убирать**, он защищает другие рецепты.

- [ ] **Step 4: Перевести `TEST_DATABASE_URL` в `.env.test.example`**

Заменить блок:

```dotenv
# Отдельная Neon test-ветка (не использовать прод!)
TEST_DATABASE_URL=postgresql+psycopg://<test_owner>:<password>@<test-branch-host>.neon.tech/neondb?sslmode=require&channel_binding=require
```

на:

```dotenv
# Локальный тестовый кластер (см. docs/testing.md, «Локальный тестовый Postgres»).
# Neon test-ветка исключена ревизией 2026-07-28: версии локального кластера и Neon
# совпадают точно (16.14, vector, pg_trgm), а держать недостижимую цель смысла нет.
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/udp_test
```

- [ ] **Step 5: Поправить четыре места в `docs/testing.md`**

1. Строка про запуск backend-тестов («нужен `TEST_DATABASE_URL` в `.env.test` (отдельная Neon test-ветка)») — заменить на «`TEST_DATABASE_URL` указывает на локальный `udp_test`; кластер поднимается автоматически».
2. Пункт про `.env.test` в корне репо («отдельная Neon test-ветка, **не прод**») — переформулировать на локальную цель, оговорку про «не прод» сохранить: она про класс ошибки, а не про вендора.
3. Абзац про fallback («если каталог `udp-pgtest\data` существует — локальный Postgres, иначе Neon из `.env`… у контрибьюторов без локальной установки поведение прежнее») — заменить: локальный кластер обязателен, контрибьютора без него обслуживает CI, отказ приходит от `pg-test-start` с инструкцией по установке.
4. Описание `db_engine` в разделе «Архитектура» («открывает соединение к Neon test-ветке») — на локальную.

Плюс **снять кавеат** «Внимание (2026-07-28): с dev-машины все пути в Neon недоступны…»: после ревизии тестовые пути в Neon не используются, и предупреждение вводит в заблуждение. Вместо него — одна строка в разделе про локальную dev-БД: «Neon с этой машины недостижим из-за корпоративной TLS-инспекции; для дев-цикла и тестов это неважно — обе цели локальные».

- [ ] **Step 6: Проверить, что Neon не остался тестовой целью нигде**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && grep -rn 'neon' justfile .env.test.example docs/testing.md -i | grep -viE 'devlog|superpowers' | cut -c1-140"`
Expected: упоминания Neon остались только как «один из вариантов деплоя» или в историческом контексте. Ни одно не должно утверждать, что тесты идут в Neon.

- [ ] **Step 7: Сверить `docs/setup/neon-setup.md`**

Прочитать файл. Ожидается: раздела про создание test-ветки в нём **нет** (заголовки — регистрация, connection string, `.env`, pgvector, заметки), править нечего. Если раздел всё же обнаружится — не удалять, а пометить «для тестов не используется, тесты идут на локальном кластере»: инструкция пригодится при развёртывании на стороннем хосте.

Файл остаётся в репозитории: он обслуживает живой профиль «сторонний хостинг» из §7 спеки. Переформулировку во «один из вариантов» делает Task 6.

- [ ] **Step 8: Полная проверка**

Run: `just lint` — «All checks passed!»
Run: `just test` — зелёный, `6 skipped` без роста. Теперь это идёт через алиас `test-backend-local`, то есть на локальном кластере.
Run: `just db-test-migrate` — накат на локальный `udp_test` проходит.
Run: `just db-test-check` — «No new upgrade operations detected».

- [ ] **Step 9: Коммит**

```bash
git add justfile .env.test.example docs/testing.md
git commit -m "$(cat <<'EOF'
chore(tests): убрать Neon test-ветку из тестовой инфраструктуры

Решение владельца 2026-07-28 (ревизия §12 спеки): Neon test-ветка не нужна, она
будет существовать только при развёртывании на сторонний хост. Версии локального
кластера и Neon совпадают точно (16.14, vector, pg_trgm), а с дев-машины Neon
вообще недостижим из-за корпоративной TLS-инспекции.

test-backend становится алиасом test-backend-local. Свёртка идёт через
зависимость pg-test-start, а НЕ через нацеливание TEST_DATABASE_URL на
localhost: во втором случае контрибьютор без кластера получил бы
psycopg-traceback на DROP SCHEMA (все три pytest.skip в db_engine проходят
раньше первого коннекта, create_engine ленив) вместо внятного «не установлен —
см. docs/testing.md».

db-test-migrate переведён на {{test_db_local}} по образцу db-test-check: прежний
рецепт читал $TEST_DATABASE_URL из шелла, что требовало ручного экспорта.
Инвариант override=False остаётся в силе — рецепт по-прежнему подаёт DATABASE_URL
через process env поверх backend/.env; инвариант -u для этого рецепта потерял
объект, но флаг из justfile не убран: он защищает другие рецепты.

Цена, принятая осознанно: локальный кластер обязателен для прогона тестов на
дев-машине. Контрибьютора без него обслуживает CI (он зовёт uv run pytest
напрямую, не через just).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: MANUAL — сообщить человеку про свой `.env.test`**

> **ВЫПОЛНЯЕТ ЧЕЛОВЕК.** Агент выводит инструкцию и продолжает — шаг не блокирует Task 7:
>
> В корневом `.env.test` замените `TEST_DATABASE_URL` на локальную цель:
>
> ```dotenv
> TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/udp_test
> ```
>
> **Почему это важно, хотя `just test` работает и без правки.** Рецепты `*-local` передают
> цель инлайн и `.env.test` не читают. Но `just test-backend-integration` запускает
> `uv run pytest` напрямую, и там `TEST_DATABASE_URL` берётся из `.env.test`. Если он
> остался Neon-овским, база называется `neondb` — суффикс `_test` не проходит, и фикстура
> `db_engine` делает `pytest.skip`. Интеграционный слой **молча пропустится**, а прогон
> останется зелёным: ровно тот класс ложно-зелёного, от которого защищает AC-11.
>
> `.env.test` правится только вами: правило `AGENTS.md`.

---

### Task 7: Финальная проверка и снятие черновика с PR #45

**Порядок:** выполняется **после** Task 8 — снятие черновика должно происходить на вычищенном состоянии.

**Files:** изменений нет.

**Interfaces:**
- Consumes: результат Task 1-6.
- Produces: PR готов к ревью.

- [ ] **Step 1: Прогнать все AC подряд**

```bash
just lint
just test
just db-test-check
git grep -l "ALLOW_NEON_WRITES\|is_neon_url" -- ':!docs/superpowers' ':!docs/devlog'
```

Expected: lint чист; `just test` зелёный со `6 skipped` (baseline `7edca85`, run 30288104267 — если skipped вырос, новые `pytest.skip` проглотили интеграционный слой, разбираться до пуша); `db-test-check` без дрейфа; `git grep` пустой.

- [ ] **Step 2: Проверить fail-closed вживую**

> **Сознательное исключение из Global Constraint «только `just`».** Шагу нужны три env-оверрайда на одну разовую проверку; рецепта под это нет и заводить его ради одного ручного прогона не стоит. Это единственное место в плане, где `cd backend &&` допустим.

```bash
cd backend && APP_ENV=dev DB_EXTRA_TARGETS= DATABASE_URL="postgresql+psycopg://u:p@db.example.com:5432/prod" uv run python -m cli create-org --name "Проверка"
```

Expected: `RuntimeError` с целью `db.example.com:5432/prod` в тексте, обоими выходами и без пароля `p` в сообщении. Обратите внимание: цель не-loopback и списка нет, поэтому падение произойдёт до попытки коннекта к `db.example.com` — сети не будет вовсе.

- [ ] **Step 3: Push и снятие черновика**

```bash
git push
gh pr ready 45
gh pr checks 45
```

Expected: чеки `backend-tests` и `frontend-tests` зелёные; CodeRabbit больше не пишет «Review skipped: draft pull request» и начинает ревью.

- [ ] **Step 4: Обновить описание PR**

Раздел «Известное ограничение — не мержить как есть» заменить на «Ограничение снято: ось переключена на роль окружения, см. спеку `2026-07-27-deploy-env-contract-design.md`». Добавить перечень AC и как они проверены.

- [ ] **Step 5: Дождаться CodeRabbit и разобрать замечания**

Использовать скилл `superpowers:receiving-code-review`: проверять каждое замечание против кода, а не принимать на веру.

---

## Self-Review

**1. Spec coverage.**

| Секция/AC спеки | Задача |
|---|---|
| §4 контракт имён, три формы | Task 1 (Step 6), Task 6 (Step 6-7) |
| §4 корзина legacy: `AI_MAX_TOKENS`, `PDF_ENGINE` | Task 4 |
| §4 мёртвый конфиг `TEST_MODE` + оговорка про E2E | Task 5 (Step 1-2, коммит) |
| §5 механика guard'а, нормализация, три call-site | Task 3 (Step 1-5) |
| §5 текст ошибки: три требования | Task 3 (Step 1 — три теста, Step 3 — реализация) |
| §5 инвариант единственного потребителя | Task 1 (Step 6) |
| §5 инварианты `db-test-migrate` | Task 3 (Step 7 — регрессионный тест) |
| §5 что поглощается (рубеж (a) → нормализованное сравнение), что нет (рубеж `_test`) | Task 3 (Step 10) |
| §6 герметичность | Task 1 (Step 2), Task 3 (Step 1, Step 6) |
| §6 CI без новой конфигурации | Task 7 (Step 1) |
| §7 деплойный контракт | Task 6 (Step 1-2) |
| §8 `.gitignore` + доки | Task 5 (Step 3), Task 6 |
| §9 порядок работ, manual-шаг на позиции | Task 2 — **отменена ревизией §12**, номер сохранён, действий не требует |
| §12 ревизия: Neon test-ветка исключена | Task 8 (порядок исполнения: 1 → 3 → 4 → 5 → 6 → 8 → 7) |
| AC-0…AC-12 | Task 1 (AC-0), Task 3 (AC-1…AC-8, AC-10), Task 4 (AC-9), Task 7 (AC-11, AC-12) |

**Первая редакция плана заявляла «пробелов не найдено» при трёх блокирующих расхождениях** — таблица покрытия отмечала галочки по номерам секций, не сверяя содержание задачи с текстом спеки. Исправлено по ревью:

| Что было не так | Правка |
|---|---|
| §5 обещала поглощение рубежа (a), Task 3 Step 10 предписывал «сохранить без изменений» — сырое строковое равенство и query-param-дырка оставались | Step 10 переводит рубеж на `normalize_target(...) == normalize_target(...)` |
| Task 4 выдавал безусловный warning на fallback-ветке `AI_MAX_TOKENS`, а поле объявлено `int = 64000` — то есть предупреждал бы каждую чистую установку про переменную, которой пользователь не касался | Поле становится `int \| None = None`, warning только при `is not None`, добавлен парный тест на молчание |
| Task 3 Step 6 не трогал `assert "Neon" in str(result.exception)` и вендорные имена cli-тестов → три красных теста, а node-id в Step 9 ломался при переименовании «по духу» | Step 6 предписывает переименование обоих тестов и замену ассерцции на `"APP_ENV=dev"`, Step 9 использует новое имя |
| §6 требовала тест на IPv6-литерал, в плане его не было | Добавлены два теста плюс явный YAGNI-вывод: IPv6 в `DB_EXTRA_TARGETS` не выразим (разбор режет по первому двоеточию), нужный случай — только loopback |
| AGENTS.md-текст допускал «`DB_EXTRA_TARGETS` **либо** `APP_ENV=prod`», то есть внесение прод-DSN в постоянный allowlist — это навсегда глушит guard про прод | Оставлен только one-shot путь `APP_ENV=prod just db_target=env db-migrate` плюс прямой запрет вносить прод-цель в список |
| Счётчики ожидаемых тестов: 20 вместо 23 и 13 вместо 10 | Пересчитаны подсчётом `def test_` в блоке. Ошибка выжила одну правку: во второй редакции к неверной базе (19) прибавили два новых теста и получили 22 вместо 23. Счётчик считать по файлу, не арифметикой от прошлой оценки |
| Task 1 Step 2 нёс черновую версию теста и «заменить тело на» финальную | Оставлена только финальная, `import pytest`/`pydantic` внесены в блок файла |
| Task 7 Step 2 нарушал Global Constraint «только `just`» без оговорки | Добавлено явное «сознательное исключение» с обоснованием |

**2. Placeholder scan.** Проверено: нет «TBD», «TODO», «add appropriate error handling», «similar to Task N», нет шагов без кода там, где код нужен. Все команды `just` — реальные рецепты из justfile.

**3. Type consistency.** `ensure_mutation_allowed(url: str, action: str) -> None`, `normalize_target(url: str) -> str`, `parse_extra_targets(raw: str) -> frozenset[str]`, `is_target_allowed(url: str, extra: frozenset[str]) -> bool`, `safe_host(url: str) -> str` — имена и сигнатуры совпадают между блоком Interfaces Task 3, тестами (Step 1) и реализацией (Step 3). `Settings.APP_ENV` / `Settings.DB_EXTRA_TARGETS` объявлены в Task 1 и потребляются в Task 3 под теми же именами. Константы `UNKNOWN_HOST`, `LOOPBACK_HOSTS`, `DEFAULT_PG_PORT` объявлены в Step 3 и используются в тестах Step 1.

**4. Самопроверка кода в плане.** Первая редакция содержала `.rstrip("")` вместо `.rstrip(".")` в `normalize_target` — исправлено в коде, а не оставлено предупреждением: план с заложенным багом плюс пометка хуже корректного плана. Пометка сохранена как проверка при переносе, потому что ошибка молчаливая (пустой аргумент ничего не снимает, и `h.example.com.` стал бы второй целью относительно `h.example.com`).

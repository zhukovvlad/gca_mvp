"""Тесты обвязки db_guard: что точки входа его действительно зовут.

Отдельно от test_db_guard.py, который проверяет сам модуль. Смысл именно в
обвязке: удалить вызов `_guard(...)` из команды cli или переставить
`ensure_mutation_allowed` ниже создания движка в alembic/env.py — и все тесты
чистого модуля останутся зелёными. Здесь ловится ровно это.
"""
from pathlib import Path

import pytest
from click.testing import CliRunner

import cli
import main
import tests.conftest as conftest_module

REMOTE_URL = (
    "postgresql+psycopg://test_owner:secret-pw@"
    "ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb"
)


@pytest.fixture(autouse=True)
def _unlisted_target_in_dev(monkeypatch):
    """Цель не разрешена: APP_ENV=dev, пустой allowlist, удалённый хост.

    DB_EXTRA_TARGETS пиним явно: env_file абсолютный, и в реальном backend/.env
    список может быть непустым — иначе тест был бы зелёным в CI и красным на
    машине разработчика. `PGPORT`/`PGHOSTADDR`/`PGSERVICE`/`PGHOST`/`PGDATABASE`
    тоже снимаются: REMOTE_URL без явного порта, и любая из этих переменных в
    шелле поменяла бы сообщение об ошибке (нераспознанная цель вместо обычного
    отказа dev-целью) — тесты ниже проверяют текст именно второго вида.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    monkeypatch.setattr(cli.settings, "DATABASE_URL", REMOTE_URL, raising=False)


@pytest.fixture
def no_db_session(monkeypatch):
    """Взорваться, если команда всё же дошла до открытия сессии.

    Guard обязан срабатывать ДО SessionLocal() — иначе «fail-fast» на деле
    означает коннект к прод-базе и только потом отказ.
    """
    def _explode():
        raise AssertionError("SessionLocal() вызван — guard сработал слишком поздно")

    monkeypatch.setattr(cli, "SessionLocal", _explode)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("create-superuser", ["--email", "a@b.c", "--password", "pw"]),
        ("create-org", ["--name", "Орг"]),
        ("create-user", ["--email", "a@b.c", "--org-id", "1", "--password", "pw"]),
    ],
)
def test_cli_commands_refuse_unlisted_target(command, args, no_db_session):
    """Каждая мутирующая cli-команда отказывается мутировать неразрешённую цель."""
    result = CliRunner().invoke(cli.cli, [command, *args])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "APP_ENV=dev" in str(result.exception)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("create-superuser", ["--email", "a@b.c", "--password", "pw"]),
        ("create-org", ["--name", "Орг"]),
        ("create-user", ["--email", "a@b.c", "--org-id", "1", "--password", "pw"]),
    ],
)
def test_cli_commands_pass_guard_when_prod(command, args, monkeypatch):
    """При APP_ENV=prod guard пропускает — падение уже на уровне БД.

    Проверяем именно, что барьер снят: до SessionLocal команда доходит.
    """
    monkeypatch.setenv("APP_ENV", "prod")
    reached = []

    def _record():
        reached.append(True)
        raise RuntimeError("stop-here")

    monkeypatch.setattr(cli, "SessionLocal", _record)
    CliRunner().invoke(cli.cli, [command, *args])
    assert reached, "guard не пустил дальше, хотя запись разрешена"


def test_startup_sweep_refuses_unlisted_target(monkeypatch):
    """Sweep на старте не идёт в неразрешённую цель."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.setattr(main.settings, "DATABASE_URL", REMOTE_URL, raising=False)
    with pytest.raises(RuntimeError, match="startup-sweep"):
        main._sweep_stuck_documents()


def test_startup_sweep_skips_guard_for_injected_factory():
    """С инжектированной фабрикой guard не при чём — цель заведомо тестовая.

    Иначе интеграционные тесты (они подменяют фабрику) требовали бы
    APP_ENV=prod только из-за незнакомого хоста в backend/.env.
    """
    class _Result:
        rowcount = 0

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *_args):
            return _Result()

        def commit(self):
            pass

    assert main._sweep_stuck_documents(session_factory=_Session) == 0


def test_alembic_env_guards_before_engine():
    """alembic/env.py зовёт guard до создания движка.

    Проверка по исходнику: рантайм-запуск env.py потребовал бы живой БД, а
    важен именно порядок — guard обязан стоять выше engine_from_config.
    """
    source = Path(main.__file__).parent / "alembic" / "env.py"
    text = source.read_text(encoding="utf-8")
    guard_at = text.index("ensure_mutation_allowed(")
    engine_at = text.index("engine_from_config(")
    assert guard_at < engine_at, "guard оказался ниже создания движка"


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
    # Срез до конца строки, а не до первой ")": вызов вида
    # load_dotenv(find_dotenv(), override=True) содержит закрывающую скобку
    # раньше "override", и срез по первой ")" пропустил бы регресс, который
    # тест обязан ловить.
    line_end = text.index("\n", call_at)
    call = text[call_at:line_end]
    assert "override" not in call, (
        f"load_dotenv вызван с override — это ломает db-test-migrate: {call}"
    )


def test_db_engine_fixture_fails_loudly_on_unresolvable_target(monkeypatch):
    """`db_engine` падает, а не тихо скипает, когда TEST_DATABASE_URL нераспознан.

    Без предусловия перед барьером (a) отравленное окружение (`PGHOSTADDR`/
    `PGSERVICE`) делает `normalize_target(test_url)` и `normalize_target(prod_url)`
    одной и той же `UNKNOWN_HOST`-формой (см. `_resolve_connect_target` —
    проверка этих переменных безусловна и не зависит от конкретного DSN),
    барьер (a) решил бы «цели совпадают» и молча ушёл в `pytest.skip`: съедает
    интеграционный слой незаметно, ломая инвариант «ровно 6 пропущенных», хотя
    для самой БД это безопасно. На деструктивном DROP SCHEMA-пути правильный
    ответ — громкий отказ.

    Фикстура вызывается напрямую через `__wrapped__` (сырую функцию под
    `@pytest.fixture`) — pytest фикстуры нельзя звать как обычные функции, а
    прогонять целую pytest-сессию с отравленным окружением ради одного теста
    неоправданно тяжело и рискованно для остальных integration-тестов той же
    сессии.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5459/udp_test")
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    gen = conftest_module.db_engine.__wrapped__()
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        next(gen)


def test_db_engine_fails_loudly_when_prod_unresolvable_and_same_target(monkeypatch):
    """`db_engine` падает громко, а не пропускает DROP SCHEMA, когда прод скрыт за query-ключом.

    P1 внешнего ревью: предыдущий раунд сделал `test_target` обязательно
    резолвленным (предусловие выше), но `prod_url` оставался непроверенным —
    сравнение шло по СТРОКАМ `normalize_target()`. Если ТОЛЬКО прод-сторона
    несёт цель-определяющий query-ключ (`?dbname=` в этом тесте), она даёт
    `UNKNOWN_HOST`-строку, test-сторона — реальную; строки НИКОГДА не
    совпадают, и `pytest.skip` не срабатывает — DROP SCHEMA ушёл бы вперёд,
    даже если реальная (нерезолвленная) прод-цель была той же базой, что и
    `TEST_DATABASE_URL` (в этом тесте буквально она же, `localhost:5459/udp_test`,
    только с довеском `?dbname=udp_test`, который guard обязан трактовать как
    "цель неизвестна", а не "то же самое имя БД").

    Фикс сравнивает РЕЗОЛВЛЕННЫЕ тройки, а не строки, и добавляет отдельную
    проверку резолвленности прод-стороны — при её отказе `db_engine` обязан
    упасть `RuntimeError` ДО `DROP SCHEMA`, а не молча продолжить.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5459/udp_test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@localhost:5459/udp_test?dbname=udp_test",
    )
    gen = conftest_module.db_engine.__wrapped__()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        next(gen)


def test_db_engine_fails_loudly_when_prod_unresolvable_and_different_target(monkeypatch):
    """Нераспознанная прод-цель — всегда громкий отказ, а не только при совпадении targets.

    Пиннит именно это поведение (не «падает только когда цели совпали»):
    DATABASE_URL с query-ключом целится по виду совсем на другую БД
    (`otherdb`), но раз прод-цель нельзя доверенно резолвить, guard не может
    ДОКАЗАТЬ, что она отличается от TEST_DATABASE_URL — поэтому отказ такой
    же громкий, как и в тесте на совпадающую цель выше.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5459/udp_test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@remote.example.com:5432/otherdb?dbname=otherdb",
    )
    gen = conftest_module.db_engine.__wrapped__()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        next(gen)

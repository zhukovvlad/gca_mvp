"""Тесты фичи «база на воркёра» (perf/pytest-parallel-workers).

Unit-слой — чистые функции из tests/conftest.py: `worker_database_url`
(имя базы воркёра pytest-xdist) и `test_database_refusal_reason` (решение
барьера (b)). Wiring-слой — порядок операций в `db_engine`: тем же приёмом,
что и test_db_guard_wiring.py (`__wrapped__` + `pytest.raises`), вторая
конвенция не заводится.

Кластер не нужен: каждый wiring-тест обрывается до DROP SCHEMA и миграций —
либо отказом барьера, либо шпионом на функции создания базы.

План: docs/superpowers/plans/2026-08-11-pytest-parallel-workers.md, задача 1.
"""
import pytest
from sqlalchemy.engine import make_url

import db_guard
import tests.conftest as conftest_module

BASE_URL = "postgresql+psycopg://postgres@localhost:5459/gca_test"
PROD_URL = "postgresql+psycopg://postgres@localhost:5459/gca_dev"
# Удалённая «_test»-цель: не loopback и не в DB_EXTRA_TARGETS — её обязан
# отклонить ensure_mutation_allowed, а не барьеры (a)/(b).
REMOTE_TEST_URL = (
    "postgresql+psycopg://test_owner:secret-pw@"
    "ep-example-0000.c-3.eu-central-1.aws.neon.tech/gca_test"
)


@pytest.fixture(autouse=True)
def _pinned_dev_env(monkeypatch):
    """Пиним окружение guard'а — исход не должен зависеть от шелла и backend/.env.

    Тот же смысл, что у `_unlisted_target_in_dev` в test_db_guard_wiring.py:
    DB_EXTRA_TARGETS в реальном .env может быть непустым, а libpq-переменные
    (PGHOSTADDR/PGSERVICE/PGPORT/...) меняют резолв цели — без пиннинга тест
    был бы зелёным в CI и красным на машине разработчика или наоборот.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    for name in ("PGPORT", "PGHOSTADDR", "PGSERVICE", "PGHOST", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
#  worker_database_url — чистая функция имени (задача 1, пп. 1–5)
# ---------------------------------------------------------------------------


def test_master_returns_url_unchanged():
    """П. 1: серийное поведение не тронуто — master получает URL как есть."""
    assert conftest_module.worker_database_url(BASE_URL, "master") == BASE_URL


def test_gw_worker_database_name_is_suffixed():
    """П. 2: gw0 → gca_gw0_test; двузначный номер (gw11) не ломается."""
    gw0 = conftest_module.worker_database_url(BASE_URL, "gw0")
    gw11 = conftest_module.worker_database_url(BASE_URL, "gw11")
    assert make_url(gw0).database == "gca_gw0_test"
    assert make_url(gw11).database == "gca_gw11_test"


def test_derived_name_keeps_test_suffix():
    """П. 3: суффикс `_test` сохраняется — прямое условие прохода барьера (b).

    Это не косметика имени: приставочная форма (gca_test_gw0) дала бы
    pytest.skip всех integration-тестов на каждом воркёре — зелёный прогон,
    не проверивший ничего (§2.1 спеки).
    """
    derived = make_url(conftest_module.worker_database_url(BASE_URL, "gw0")).database
    assert derived.endswith("_test"), (
        f"имя базы воркёра {derived!r} не пройдёт барьер (b) — integration-тесты "
        "молча пропустятся"
    )


@pytest.mark.parametrize("worker_id", ["", "master2", "gw", "gw-1", "../evil"])
def test_foreign_worker_id_is_loud_runtime_error(worker_id):
    """П. 4: посторонний вход — громкий RuntimeError, не skip и не фолбэк.

    Ловим BaseException и проверяем тип отдельно: pytest.raises(RuntimeError)
    пропустил бы подмену на pytest.skip (Skipped не наследует RuntimeError,
    тест стал бы «skipped», а не красным) — а именно эту подмену обязано
    ловить снятие 4 реестра.
    """
    with pytest.raises(BaseException) as exc_info:  # noqa: B017 — тип проверяется ниже
        conftest_module.worker_database_url(BASE_URL, worker_id)
    assert isinstance(exc_info.value, RuntimeError), (
        f"вход {worker_id!r} дал {type(exc_info.value).__name__}, а не RuntimeError — "
        "отказ обязан быть громким, не тихим"
    )


def test_other_dsn_parts_are_preserved():
    """П. 5: перестраивается только имя базы — драйвер, креды, хост, порт целы."""
    url = "postgresql+psycopg://alice:s3cr3t@db.example.com:6543/gca_test"
    original = make_url(url)
    derived = make_url(conftest_module.worker_database_url(url, "gw7"))
    assert derived.database == "gca_gw7_test"
    assert (
        derived.drivername,
        derived.username,
        derived.password,
        derived.host,
        derived.port,
    ) == (
        original.drivername,
        original.username,
        original.password,
        original.host,
        original.port,
    )


def test_non_test_base_name_is_loud_runtime_error():
    """База без суффикса `_test` не даёт вывести имя воркёра — громкий отказ.

    Тихие альтернативы хуже обе: передать имя как есть — pytest.skip барьера
    (b) на каждом воркёре (зелёный прогон, не проверивший ничего, §2.1 спеки);
    вставить суффикс — воркёры прошли бы барьер, который серийный прогон по
    этому же URL не проходит.
    """
    url = "postgresql+psycopg://postgres@localhost:5459/gca_dev"
    with pytest.raises(RuntimeError):
        conftest_module.worker_database_url(url, "gw0")


# ---------------------------------------------------------------------------
#  test_database_refusal_reason — решение барьера (b) (задача 1, пп. 6–7)
# ---------------------------------------------------------------------------


def test_refusal_reason_non_empty_for_non_test_names():
    """П. 6: имя без суффикса `_test` даёт непустую причину отказа."""
    for db_name in ("gca_dev", "gca", "postgres"):
        url = f"postgresql+psycopg://postgres@localhost:5459/{db_name}"
        reason = conftest_module.test_database_refusal_reason(url)
        assert reason, f"имя {db_name!r} обязано давать непустую причину отказа"
        assert "_test" in reason


def test_refusal_reason_none_for_serial_and_worker_names():
    """П. 7: и серийное имя, и имя воркёра приемлемы — правка не сломала оба пути."""
    for db_name in ("gca_test", "gca_gw0_test"):
        url = f"postgresql+psycopg://postgres@localhost:5459/{db_name}"
        assert conftest_module.test_database_refusal_reason(url) is None, (
            f"имя {db_name!r} обязано проходить барьер (b)"
        )


# ---------------------------------------------------------------------------
#  Wiring db_engine: порядок «барьеры → мутация» (задача 1, пп. 8–11)
# ---------------------------------------------------------------------------


def test_db_engine_skips_before_create_database_on_refusal(monkeypatch):
    """П. 8: имя без `_test` — skip настоящего барьера (b) ДО функции создания базы.

    Плохое имя тест подаёт сам — подменой генератора: с целым генератором имя
    воркёра всегда кончается на `_test`, и ветка отказа на gw-пути не
    исполнилась бы (а на master-пути создание базы не исполняется вовсе —
    шпион был бы вакуозным). Барьер (b) при этом НАСТОЯЩИЙ, не стаб: снятие 6
    реестра (test_database_refusal_reason всегда None) обязано ронять именно
    этот тест — стаб причины замаскировал бы снятие. Имя базы в подмене —
    заведомо несуществующее: если бы прогон прошёл дальше барьера, он упал бы
    на подключении, а не мутировал бы живую dev-базу.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", BASE_URL)
    monkeypatch.setenv("DATABASE_URL", PROD_URL)
    bad_url = "postgresql+psycopg://postgres@localhost:5459/gca_probe_suffixless"
    monkeypatch.setattr(
        conftest_module, "worker_database_url", lambda url, worker_id: bad_url
    )
    created: list[str] = []
    monkeypatch.setattr(conftest_module, "_create_worker_database", created.append)

    gen = conftest_module.db_engine.__wrapped__("gw0")
    with pytest.raises(pytest.skip.Exception):
        next(gen)
    assert not created, "барьер (b) отказал, а мутация всё равно достигнута"


def test_barrier_a_failure_precedes_create_database(monkeypatch):
    """П. 9: отказ барьера (a) не доходит до CREATE DATABASE.

    Вход тот же, что у существующего теста нерезолвимой цели в
    test_db_guard_wiring.py: отравленный PGHOSTADDR делает цель
    TEST_DATABASE_URL нераспознанной — громкий RuntimeError до мутации.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", BASE_URL)
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    created: list[str] = []
    monkeypatch.setattr(conftest_module, "_create_worker_database", created.append)

    gen = conftest_module.db_engine.__wrapped__("gw0")
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        next(gen)
    assert not created, "барьер (a) отказал, а CREATE DATABASE всё равно достигнут"


def test_mutation_guard_failure_precedes_create_database(monkeypatch):
    """П. 10: отказ ensure_mutation_allowed не доходит до CREATE DATABASE.

    Цель — удалённая `_test`-база: не loopback и не в DB_EXTRA_TARGETS, то есть
    барьеры (a)/(b) её пропускают, отклоняет только guard. CREATE DATABASE —
    мутация, и стоять она обязана строго после него.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", REMOTE_TEST_URL)
    monkeypatch.setenv("DATABASE_URL", PROD_URL)
    created: list[str] = []
    monkeypatch.setattr(conftest_module, "_create_worker_database", created.append)

    gen = conftest_module.db_engine.__wrapped__("gw0")
    with pytest.raises(RuntimeError, match="APP_ENV=dev"):
        next(gen)
    assert not created, "guard отказал, а CREATE DATABASE всё равно достигнут"


def test_data_flow_all_four_chain_points_see_derived_url(monkeypatch):
    """П. 11: все четыре точки цепочки получают ОДИН И ТОТ ЖЕ производный URL.

    Шпионы — на `_resolve_connect_target` (барьер (a)),
    `test_database_refusal_reason` (барьер (b)), `ensure_mutation_allowed` и
    функции создания базы. Трёх шпионов недостаточно: дыра осталась бы ровно
    в звене, которое разрешает мутацию, — guard авторизовал бы мутацию не той
    базы, которую потом мутируют (снятия 9 и 10 реестра). Утверждения о
    результатах функций перестановку не видят — только поток данных.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", BASE_URL)
    monkeypatch.setenv("DATABASE_URL", PROD_URL)

    resolve_calls: list[str] = []
    real_resolve = db_guard._resolve_connect_target

    def _spy_resolve(url):
        resolve_calls.append(url)
        return real_resolve(url)

    monkeypatch.setattr(db_guard, "_resolve_connect_target", _spy_resolve)

    refusal_calls: list[str] = []
    real_refusal = conftest_module.test_database_refusal_reason

    def _spy_refusal(url):
        refusal_calls.append(url)
        return real_refusal(url)

    monkeypatch.setattr(conftest_module, "test_database_refusal_reason", _spy_refusal)

    guard_calls: list[str] = []
    monkeypatch.setattr(
        db_guard, "ensure_mutation_allowed", lambda url, action: guard_calls.append(url)
    )

    class _StopBeforeSchema(Exception):
        """Сигнал шпиона создания базы: дальше (DROP SCHEMA, миграции) не идём."""

    create_calls: list[str] = []

    def _spy_create(url):
        create_calls.append(url)
        raise _StopBeforeSchema

    monkeypatch.setattr(conftest_module, "_create_worker_database", _spy_create)

    gen = conftest_module.db_engine.__wrapped__("gw0")
    with pytest.raises(_StopBeforeSchema):
        next(gen)

    assert len(refusal_calls) == 1, refusal_calls
    assert len(guard_calls) == 1, guard_calls
    assert len(create_calls) == 1, create_calls
    seen = {refusal_calls[0], guard_calls[0], create_calls[0]}
    assert len(seen) == 1, f"точки цепочки видели РАЗНЫЕ URL: {seen}"
    derived = refusal_calls[0]
    assert make_url(derived).database == "gca_gw0_test"
    # Барьер (a) судил производный URL, а не исходный: перестройка имени стоит
    # первым шагом (снятие 9 реестра). resolve зовётся и для прод-URL — поэтому
    # membership, а не равенство списка.
    assert derived in resolve_calls
    assert BASE_URL not in resolve_calls, (
        "барьер (a) резолвил исходный gca_test — защита проверила имя, "
        "по которому работа не идёт"
    )

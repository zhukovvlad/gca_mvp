"""Глобальные фикстуры для всех тестов backend.

Слои:
* Unit-тесты не требуют БД.
* Integration-тесты используют реальный Postgres (через TEST_DATABASE_URL),
  но Alembic мигрирует один раз на сессию. Каждый тест — в транзакции с rollback.
* Под pytest-xdist каждый воркёр работает со СВОЕЙ базой (`gca_gw<N>_test`),
  которую сам и создаёт; серийный прогон идёт по `gca_test` как раньше.
  Спека: docs/superpowers/specs/2026-08-11-pytest-parallel-workers-design.md.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

# Делаем импорты "from database import ..." и "import crud" работающими
# из тестов, не привязываясь к sys.path в IDE
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Гарантируем наличие SECRET_KEY (HS256) до импорта backend-модулей.
# Settings() вызывается при первом импорте config.py (на который ссылается database.py и др.).
# В CI и при локальных unit-тестах .env может отсутствовать — этот setdefault
# подставляет тестовое значение, не перетирая реальный SECRET_KEY из .env.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production-32ch!!")

# Обслуживание при старте (recovery зависших import_jobs + ретенция файлов,
# AGENTS.md §5, §8) работает на РЕАЛЬНОМ engine приложения — мимо транзакционной
# фикстуры. В тестах `TestClient(app)` поднимает lifespan, поэтому по умолчанию
# оно выключено: сами эти функции тестируются напрямую, на тестовой сессии.
# setdefault, а не присваивание: локальный прогон может включить его осознанно.
os.environ.setdefault("RUN_STARTUP_MAINTENANCE", "false")


# ---------------------------------------------------------------------------
#  Страж пропусков: в полном прогоне skip допустим только из явного реестра
# ---------------------------------------------------------------------------

#: Реестр законных пропусков ПОЛНОГО прогона (TEST_DATABASE_URL задан):
#: (префикс nodeid, фрагмент причины). Закрепляется СОСТАВ, а не число:
#: локально пропусков 6 (публичные endpoints), в CI — 13 (плюс семь тестов
#: реальных оферт: samples/ не коммитится, AGENTS.md §9). Любой пропуск вне
#: реестра — прежде всего skip барьеров db_engine — роняет прогон: зелёный
#: код возврата при молча пропущенном integration-слое и есть главный дефект,
#: который эта защита исключает.
_ALLOWED_SKIPS = (
    ("tests/test_auth_coverage.py", "Публичный endpoint — auth не требуется"),
    ("tests/unit/parser/test_estimate.py", "Каталог samples/ пуст или отсутствует"),
)


def unexpected_skips(skips: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Нарушители из списка (nodeid, причина): всё, чего нет в _ALLOWED_SKIPS.

    Разрешение требует совпадения ОБЕИХ частей — файла и причины: реестр
    закрепляет состав пропусков, а не индульгенцию файлу или тексту причины.
    """
    return [
        (nodeid, reason)
        for nodeid, reason in skips
        if not any(
            nodeid.startswith(prefix) and fragment in reason
            for prefix, fragment in _ALLOWED_SKIPS
        )
    ]


#: Пропуски, накопленные хуками за сессию. На контроллере xdist сюда попадают
#: и отчёты воркёров — их пересылает сам xdist через pytest_runtest_logreport.
_observed_skips: list[tuple[str, str]] = []


def _skip_reason(report) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])  # (файл, строка, "Skipped: причина")
    return str(longrepr)


def pytest_runtest_logreport(report):
    if report.skipped:
        _observed_skips.append((report.nodeid, _skip_reason(report)))


def pytest_collectreport(report):
    # Модульные skip'ы (allow_module_level) не доходят до runtest-хука.
    if report.skipped:
        _observed_skips.append((report.nodeid, _skip_reason(report)))


def pytest_sessionfinish(session, exitstatus):
    """Уронить полный прогон, если случился пропуск вне реестра.

    Исполняется в самом прогоне, а не в CI-обвязке: любой запуск pytest с
    заданным TEST_DATABASE_URL (локальные рецепты just, CI-воркфлоу) защищён
    одинаково. Без TEST_DATABASE_URL прогон неполный по построению (integration
    пропускается штатно) — там счёт не имеет смысла. На воркёрах xdist ничего
    не решаем: сводит контроллер, которому пересылаются все отчёты.
    """
    if hasattr(session.config, "workerinput"):
        return
    if not os.getenv("TEST_DATABASE_URL"):
        return
    offenders = unexpected_skips(_observed_skips)
    if not offenders:
        return
    lines = "\n".join(f"  {nodeid}\n    {reason}" for nodeid, reason in offenders)
    print(
        "\n[skip-guard] Пропуски вне реестра _ALLOWED_SKIPS — прогон не имеет "
        f"права выглядеть зелёным ({len(offenders)} шт.):\n{lines}",
        flush=True,
    )
    if session.exitstatus == 0:
        session.exitstatus = 1


_WORKER_ID_RE = re.compile(r"gw\d+")


def worker_database_url(url: str, worker_id: str) -> str:
    """URL базы воркёра pytest-xdist (спека §2, §2.2). Три ветки, все явные.

    `master` (серийный прогон, `-n0`, контроллер) — URL как есть, сегодняшнее
    поведение. `gw<N>` — имя базы перестраивается `gca_test` → `gca_gw<N>_test`:
    идентификатор встаёт в середину, суффикс `_test` сохраняется — иначе барьер
    (b) молча пропустил бы все integration-тесты (§2.1 спеки). Всё остальное —
    громкий RuntimeError, не skip и не фолбэк на общую базу: значение уходит в
    DDL, а пропуск здесь неотличим от зелёного прогона.
    """
    if worker_id == "master":
        return url
    if not _WORKER_ID_RE.fullmatch(worker_id):
        raise RuntimeError(
            f"Неожидаемый идентификатор воркёра pytest-xdist: {worker_id!r} — "
            "ожидается 'master' либо 'gw<N>'. Отказ от прогона: значение "
            "участвует в имени базы (DDL)."
        )
    parsed = make_url(url)
    db_name = parsed.database or ""
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Не построить имя базы воркёра из {db_name!r}: ожидается имя, "
            "оканчивающееся на '_test'. Тихие альтернативы хуже обе: передать "
            "имя как есть — pytest.skip барьера (b) на каждом воркёре, "
            "дописать суффикс — воркёры прошли бы барьер, который серийный "
            "прогон по этому URL не проходит."
        )
    worker_name = f"{db_name[: -len('_test')]}_{worker_id}_test"
    return parsed.set(database=worker_name).render_as_string(hide_password=False)


def test_database_refusal_reason(url: str) -> str | None:
    """Решение барьера (b): `None` — работать можно, строка — причина отказа.

    Вынесено из `db_engine`, чтобы живость барьера была проверяема без
    кластера (wiring-тесты test_worker_database.py): внутри фикстуры ветка
    отказа при правильном имени не исполняется вовсе. Сам отказ остаётся
    `pytest.skip` на вызывающей стороне — семантика серийного прогона не
    меняется. Имя conftest-функции с приставкой test_ pytest не собирает:
    conftest — плагин, а не тестовый модуль.
    """
    db_name = make_url(url).database or ""
    if not db_name.endswith("_test"):
        return (
            f"TEST_DATABASE_URL указывает на базу '{db_name}' — ожидается имя, "
            "оканчивающееся на '_test'; отказ от DROP SCHEMA"
        )
    return None


def _create_worker_database(url: str) -> None:
    """CREATE DATABASE базы воркёра, если её нет (спека §2.4).

    Подключение к служебной `postgres` в autocommit; имя — через
    `psycopg.sql.Identifier`, не f-строкой (§2.2 спеки). Предсоздание в
    рецепте не нужно: параллельные CREATE DATABASE сервер сериализует
    ожиданием (замер §1.3a, 8 из 8), а свою базу каждый воркёр создаёт сам.
    """
    import psycopg
    from psycopg import sql

    parsed = make_url(url)
    db_name = parsed.database or ""
    with psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


@pytest.fixture(scope="session")
def db_engine(worker_id) -> Iterator:
    """Engine на TEST_DATABASE_URL. Накатывает Alembic один раз на сессию.

    `worker_id` — штатная session-фикстура pytest-xdist: `master` при серийном
    прогоне, `gw<N>` в воркёре. Порядок операций зафиксирован планом и стережётся
    wiring-тестами (test_worker_database.py, пп. 8–11):

        worker URL → resolve + барьер (a) → барьер (b) → ensure_mutation_allowed
                   → CREATE DATABASE → engine / DROP SCHEMA / migrations

    Перестройка URL — первым шагом: иначе барьеры судили бы `gca_test`, а работа
    шла бы по `gca_gw0_test`. CREATE DATABASE — мутация, поэтому строго после
    `ensure_mutation_allowed`: раньше — и она обошла бы один из трёх барьеров.
    """
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан — integration tests пропущены")

    test_url = worker_database_url(test_url, worker_id)

    # Безопасность: отказываемся работать, если TEST_DATABASE_URL совпадает с
    # DATABASE_URL приложения. DROP SCHEMA — деструктивная операция.
    from config import Settings
    from db_guard import _resolve_connect_target, ensure_mutation_allowed

    # Предусловие для барьера (a): если TEST_DATABASE_URL нельзя доверенно
    # резолвить, нет смысла сравнивать его с прод-целью. На деструктивном пути
    # (DROP SCHEMA) правильный ответ на "не знаем цель" — громкий отказ.
    test_target = _resolve_connect_target(test_url)
    if test_target is None:
        raise RuntimeError(
            "TEST_DATABASE_URL не удалось однозначно определить: либо DSN сам по "
            "себе не разбирается или несёт host/hostaddr/port/dbname/service в "
            "query-строке, либо в окружении процесса задан PGHOSTADDR/PGSERVICE, "
            "либо PGPORT вне 0-65535. Почините TEST_DATABASE_URL или окружение "
            "процесса и повторите прогон."
        )

    # Барьер (a): сравниваем РЕЗОЛВЛЕННЫЕ тройки (host, port, dbname).
    prod_url = Settings().DATABASE_URL
    if prod_url:
        prod_target = _resolve_connect_target(prod_url)
        if prod_target is None:
            raise RuntimeError(
                "DATABASE_URL не удалось однозначно определить. Барьер (a) не "
                "может доказать, что TEST_DATABASE_URL — не БД приложения, если "
                "её цель не резолвится — почините DATABASE_URL и повторите прогон."
            )
        if test_target == prod_target:
            pytest.skip(
                "TEST_DATABASE_URL и DATABASE_URL — одна цель после резолва "
                "(host, port, dbname); отказ от DROP SCHEMA на рабочей БД"
            )

    # Барьер (b): имя БД обязано быть тестовым (суффикс _test) — цена опечатки
    # в четыре символа — DROP SCHEMA на dev-базе. Решение вынесено в
    # test_database_refusal_reason, отказ (pytest.skip) остаётся здесь.
    refusal = test_database_refusal_reason(test_url)
    if refusal is not None:
        pytest.skip(refusal)

    # Накатываем миграции через Alembic
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)

    # Guard закрывает случай, которого барьеры не ловят: удалённая "_test"-база,
    # которая не loopback и не в DB_EXTRA_TARGETS.
    ensure_mutation_allowed(test_url, "conftest DROP SCHEMA")

    # Мутации — только после всех трёх барьеров. Базу создаёт лишь воркёр:
    # master-путь ведёт себя как раньше (gca_test готовят рецепты justfile).
    if worker_id != "master":
        _create_worker_database(test_url)

    engine = create_engine(test_url, pool_pre_ping=True)

    # Сбрасываем схему перед накатом — гарантируем чистый старт
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

    # command.upgrade() исполняет alembic/env.py в этом же процессе, а тот
    # модуль-уровнево делает load_dotenv(ROOT / ".env") — снимаем побочный
    # эффект на окружение тестового процесса.
    _environ_snapshot = dict(os.environ)
    try:
        command.upgrade(cfg, "head")
    finally:
        os.environ.clear()
        os.environ.update(_environ_snapshot)

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Iterator[Session]:
    """Транзакционная фикстура. Каждый тест в своей транзакции, rollback после."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session) -> Iterator:
    """FastAPI TestClient с переопределёнными зависимостями для интеграционных тестов.

    - get_db заменяется на транзакционную сессию с rollback после теста.
    - get_current_user заменяется на мок admin-пользователя (auth-флоу тестируется отдельно).
    - CSRF-токен: клиент отправляет test-значение и в куки, и в заголовок,
      чтобы csrf_middleware пропускал все запросы.

    `client.auth_state["role"] = UserRole.member` переключает роль текущего
    пользователя — так проверяются 403 у CRUD фазы 5 (право `admin` на заведение
    карточек). Тот же приём, что у `committing_client`.
    """
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db
    from main import app
    from models import UserRole

    auth_state = {"role": UserRole.admin}

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # cleanup в db_session фикстуре

    def override_get_current_user():
        """Возвращает мок пользователя — пропускает всю логику JWT/cookie."""
        user = MagicMock()
        user.id = 1
        user.role = auth_state["role"]
        user.is_active = True
        return user

    # CSRF double-submit: одно и то же значение в куки и заголовке
    _csrf_token = "test-csrf-token"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app, headers={"X-CSRF-Token": _csrf_token}) as c:
        c.cookies.set("csrf_token", _csrf_token)
        c.auth_state = auth_state
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
#  Фикстуры для пайплайна импорта (AGENTS.md §5): настоящие commit-ы
# ---------------------------------------------------------------------------

#: Доменные таблицы, которые чистятся вокруг тестов с настоящими commit-ами.
#: Справочники, засеянные миграцией (units_of_measure, unit_aliases), и users
#: НЕ трогаем — их пересоздаёт только миграция, один раз на сессию тестов.
_DOMAIN_TABLES = (
    "matching_cache",
    "rate_standards",
    # Ряды индексов инфляции: без чистки committing-тесты правки ряда оставляли
    # бы их в базе, и соседние тесты видели бы чужой справочник.
    "inflation_index_values",
    "inflation_series",
    # Семантический контур (миграция 0017), все шесть таблиц — ЯВНО, а не в
    # расчёте на каскад от catalog_positions ниже:
    #   - work_families каскадом НЕ очистится вовсе — на неё ссылается
    #     catalog_contexts, а не наоборот, и TRUNCATE catalog_positions CASCADE
    #     до неё не доходит (семьи пережили бы тест, и частичная уникальность
    #     имени среди active сделала бы порядок тестов значимым);
    #   - остальные пять очистились бы каскадом СЛУЧАЙНО (через
    #     catalog_positions -> context_buckets -> catalog_contexts -> ...) —
    #     защитой, которой никто не объявлял, и которую снимет правка любого
    #     внешнего ключа молча. Перечисление говорит то же самое явно.
    "semantic_events",
    "context_members",
    "context_routing_rules",
    "catalog_contexts",
    "context_buckets",
    "work_families",
    "position_items",
    "estimate_additional_works",
    "proposal_summary_lines",
    "proposal_additional_info",
    "proposals",
    "lots",
    "estimate_raw_data",
    "estimates",
    "import_jobs",
    "catalog_positions",
    "contracts",
    "objects",
    "contractors",
    "rate_classes",
)


def _truncate_domain_tables(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"TRUNCATE {', '.join(_DOMAIN_TABLES)} RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def committing_session_factory(db_engine):
    """Фабрика сессий с настоящими commit-ами.

    Пайплайн импорта работает на двух независимых сессиях (AGENTS.md §5), и
    смысл теста именно в том, что сессия A видит коммиты сессии B и наоборот —
    транзакционная фикстура `db_session` этого воспроизвести не может. Цена:
    данные реально ложатся в БД, поэтому доменные таблицы чистятся до и после
    теста.
    """
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    _truncate_domain_tables(db_engine)
    try:
        yield factory
    finally:
        _truncate_domain_tables(db_engine)


@pytest.fixture
def committing_db(committing_session_factory) -> Iterator[Session]:
    """Одна сессия с настоящими commit-ами (и её фабрика — в `.info`)."""
    db = committing_session_factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def tmp_storage(tmp_path):
    """Файловое хранилище (§8) в tmp-директории теста."""
    from storage import LocalStorage

    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def committing_client(committing_session_factory, tmp_storage) -> Iterator:
    """TestClient, у которого запросы РЕАЛЬНО коммитят.

    Нужен эндпоинту загрузки: он создаёт задание в одной транзакции, а
    `BackgroundTasks` продолжает работу на своих сессиях (AGENTS.md §5) — они
    обязаны видеть закоммиченное задание. Транзакционный `client` этого не даёт.

    `client.auth_state["role"]` переключает роль текущего пользователя: право
    `replace=true` принадлежит только admin (§3).
    """
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db, get_session_factory
    from main import app
    from models import UserRole
    from storage import get_storage

    auth_state = {"role": UserRole.admin}

    def override_get_db():
        db = committing_session_factory()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user():
        user = MagicMock()
        user.id = 1
        user.role = auth_state["role"]
        user.is_active = True
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_session_factory] = lambda: committing_session_factory
    app.dependency_overrides[get_storage] = lambda: tmp_storage

    _csrf_token = "test-csrf-token"
    with TestClient(app, headers={"X-CSRF-Token": _csrf_token}) as client:
        client.cookies.set("csrf_token", _csrf_token)
        client.auth_state = auth_state
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def committing_factories(committing_db):
    """Фабрики, привязанные к сессии с настоящими commit-ами."""
    from tests import factories as f

    f._register_session(committing_db)
    yield f
    f._register_session(None)


@pytest.fixture
def factories(db_session):
    """Регистрирует db_session в фабриках. Возвращает модуль с фабриками.

    После теста сбрасываем session-holder, чтобы стейл-ссылка на закрытую
    сессию не пережила тест и не дала путаницу при следующем создании фабрики.
    """
    from tests import factories as f

    f._register_session(db_session)
    yield f
    f._register_session(None)

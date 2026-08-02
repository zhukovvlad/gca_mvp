"""Глобальные фикстуры для всех тестов backend.

Слои:
* Unit-тесты не требуют БД.
* Integration-тесты используют реальный Postgres (через TEST_DATABASE_URL),
  но Alembic мигрирует один раз на сессию. Каждый тест — в транзакции с rollback.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

# Делаем импорты "from database import ..." и "import crud" работающими
# из тестов, не привязываясь к sys.path в IDE
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Гарантируем наличие SECRET_KEY (HS256) до импорта backend-модулей.
# Settings() вызывается при первом импорте config.py (на который ссылается database.py и др.).
# В CI и при локальных unit-тестах .env может отсутствовать — этот setdefault
# подставляет тестовое значение, не перетирая реальный SECRET_KEY из .env.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production-32ch!!")


@pytest.fixture(scope="session")
def db_engine() -> Iterator:
    """Engine на TEST_DATABASE_URL. Накатывает Alembic один раз на сессию."""
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан — integration tests пропущены")

    engine = create_engine(test_url, pool_pre_ping=True)

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
    # в четыре символа — DROP SCHEMA на dev-базе.
    db_name = make_url(test_url).database or ""
    if not db_name.endswith("_test"):
        pytest.skip(
            f"TEST_DATABASE_URL указывает на базу '{db_name}' — ожидается имя, "
            "оканчивающееся на '_test'; отказ от DROP SCHEMA"
        )

    # Накатываем миграции через Alembic
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)

    # Guard закрывает случай, которого барьеры не ловят: удалённая "_test"-база,
    # которая не loopback и не в DB_EXTRA_TARGETS.
    ensure_mutation_allowed(test_url, "conftest DROP SCHEMA")

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
    """
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db
    from main import app
    from models import UserRole

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # cleanup в db_session фикстуре

    def override_get_current_user():
        """Возвращает мок admin-пользователя — пропускает всю логику JWT/cookie."""
        user = MagicMock()
        user.id = 1
        user.role = UserRole.admin
        user.is_active = True
        return user

    # CSRF double-submit: одно и то же значение в куки и заголовке
    _csrf_token = "test-csrf-token"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app, headers={"X-CSRF-Token": _csrf_token}) as c:
        c.cookies.set("csrf_token", _csrf_token)
        yield c
    app.dependency_overrides.clear()


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

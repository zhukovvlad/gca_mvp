import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Чтобы импорты "from database import ..." работали из alembic/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import models  # noqa: F401,E402  -- регистрируем модели в Base.metadata
from database import Base  # noqa: E402
from db_guard import ensure_mutation_allowed  # noqa: E402

config = context.config
_db_url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Copy backend/.env.example to backend/.env and fill in a working connection string "
        "(the example's default already points at a local Postgres)."
    )
config.set_main_option("sqlalchemy.url", _db_url)

# Fail-fast до любого DDL. Стоит выше engine_from_config/fileConfig — коннекта
# к этому моменту ещё не было. Покрывает и online-, и offline-режим: модуль
# исполняется до ветвления.
ensure_mutation_allowed(_db_url, "alembic")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Индексы, которые создаются raw SQL в миграциях (AGENTS.md §11), потому что
# декларативный слой SQLAlchemy их не выражает: индекс по выражению
# COALESCE(...), частичный уникальный индекс и UNIQUE NULLS NOT DISTINCT (PG16).
# В Base.metadata их нет, поэтому autogenerate и `alembic check` без этого
# фильтра предлагали бы их удалить как «лишние» на каждом прогоне.
#
# Плата за фильтр — их отсутствие в БД тоже не будет замечено `alembic check`,
# поэтому и наличие, и поведение каждого из них закреплены интеграционными
# тестами (tests/integration/test_schema_constraints.py).
RAW_SQL_INDEXES = {
    "uq_catalog_positions_norm_unit",   # UNIQUE (normalized_job_title, COALESCE(unit_id, -1))
    "uq_estimates_contract_amendment",  # UNIQUE NULLS NOT DISTINCT (contract_id, amendment_no)
    "uq_import_jobs_active_pair",       # UNIQUE (contract_id, COALESCE(amendment_no,-1)) WHERE ...
}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and name in RAW_SQL_INDEXES)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

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


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

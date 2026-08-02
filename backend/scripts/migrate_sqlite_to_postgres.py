"""Одноразовый перенос данных из SQLite в PostgreSQL.

Читает старый backend/database.db и переливает все таблицы в текущий
DATABASE_URL (должен указывать на Postgres). Сохраняет id-шники, чтобы
ссылки между таблицами не разъехались. После переноса синхронизирует
последовательности (sequences) Postgres, иначе следующий INSERT упадёт
с unique violation.

Запуск из backend/:
    python scripts/migrate_sqlite_to_postgres.py path/to/database.db

По умолчанию читает backend/database.db.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import SessionLocal as PgSession  # noqa: E402
from models import (  # noqa: E402
    Document,
    Invoice,
    InvoiceItem,
    MaterialClass,
    Project,
    ReferencePrice,
)

# Порядок важен: parent → child (FK constraints)
TABLES = [
    Project,
    MaterialClass,
    ReferencePrice,
    Document,
    Invoice,
    InvoiceItem,
]


def copy_table(sqlite_session, pg_session, model):
    rows = sqlite_session.query(model).all()
    if not rows:
        print(f"[skip] {model.__tablename__}: пусто в SQLite")
        return 0

    for row in rows:
        sqlite_session.expunge(row)
        pg_session.merge(row)

    pg_session.commit()
    print(f"[ok]   {model.__tablename__}: перенесено {len(rows)} строк")
    return len(rows)


def reset_sequences(pg_session, model):
    """Привести последовательность к MAX(id), иначе следующий INSERT даст конфликт."""
    table = model.__tablename__
    seq_name = f"{table}_id_seq"
    sql = text(
        f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM {table}), 1))"
    )
    pg_session.execute(sql)
    pg_session.commit()


def main(sqlite_path: str):
    if not Path(sqlite_path).exists():
        print(f"[error] SQLite файл не найден: {sqlite_path}")
        sys.exit(1)

    pg_url = os.environ.get("DATABASE_URL", "")
    if "postgresql" not in pg_url:
        print(f"[error] DATABASE_URL должен указывать на Postgres, а не: {pg_url}")
        sys.exit(1)

    sqlite_url = f"sqlite:///{sqlite_path}"
    sqlite_engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    SqliteSession = sessionmaker(bind=sqlite_engine)

    sqlite_session = SqliteSession()
    pg_session = PgSession()

    print(f"=== Migration ===")
    print(f"FROM: {sqlite_url}")
    print(f"TO:   {pg_url}")
    print()

    total = 0
    try:
        for model in TABLES:
            total += copy_table(sqlite_session, pg_session, model)

        print()
        print("=== Sync sequences ===")
        for model in TABLES:
            reset_sequences(pg_session, model)
            print(f"[ok]   {model.__tablename__}_id_seq")

        print()
        print(f"Готово. Всего перенесено строк: {total}")
    finally:
        sqlite_session.close()
        pg_session.close()


if __name__ == "__main__":
    default_path = str(ROOT / "database.db")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    main(path)

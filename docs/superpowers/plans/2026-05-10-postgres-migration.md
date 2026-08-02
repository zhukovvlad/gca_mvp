# Migration from SQLite to Neon PostgreSQL (with pgvector) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить локальный SQLite на managed PostgreSQL 16 в Neon с расширением pgvector (под будущий RAG). Backend подключается к облаку по connection string — никакой локальной установки Postgres/Docker/WSL. MinIO для S3 остаётся как есть (`minio.exe` на Windows). Существующие данные переносятся через одноразовый скрипт.

**Architecture:** Backend (FastAPI/SQLAlchemy) меняет только драйвер на `psycopg` 3 и URL подключения. Схема версионируется через Alembic. Расширение pgvector включается отдельной миграцией. Connection string Neon хранится в `backend/.env` (не коммитим). Близкий к проду переезд (свой Postgres на VPS, другой S3) делается отдельной задачей — нынешний план это явно не покрывает, но создаёт фундамент.

**Tech Stack:** Neon (managed Postgres 16 + pgvector), SQLAlchemy 2.0, psycopg 3 (binary), Alembic 1.13, pgvector Python-клиент.

---

## File Structure

**Создаются:**
- `backend/alembic.ini` — конфиг Alembic
- `backend/alembic/env.py` — окружение миграций (читает DATABASE_URL и Base.metadata)
- `backend/alembic/script.py.mako` — шаблон миграции (генерируется `alembic init`)
- `backend/alembic/versions/<hash>_initial_schema.py` — первая миграция (текущая схема)
- `backend/alembic/versions/0002_enable_pgvector.py` — включение расширения pgvector
- `backend/scripts/migrate_sqlite_to_postgres.py` — одноразовый перенос данных
- `docs/setup/neon-setup.md` — пошаговая инструкция создания Neon-проекта

**Изменяются:**
- `backend/database.py` — убираем `connect_args={"check_same_thread": False}` (SQLite-специфика), добавляем `pool_pre_ping=True`
- `backend/main.py` — убираем `Base.metadata.create_all(bind=engine)` (теперь через Alembic)
- `backend/.env.example` — меняем `DATABASE_URL` на placeholder Neon
- `backend/requirements.txt` — добавляем `psycopg[binary]`, `alembic`, `pgvector`

---

## Task 1: Создать Neon-проект и получить connection string

**Files:**
- Create: `docs/setup/neon-setup.md`

Это ручной шаг пользователя — Claude не может создать аккаунт в Neon. Документируем процесс, чтобы был воспроизводимый чеклист.

- [ ] **Step 1: Создать инструкцию**

Создать `c:\Users\zhukov_v\Projects\UDP\docs\setup\neon-setup.md`:

```markdown
# Neon Postgres setup для UDP

## 1. Регистрация и проект

1. Открыть https://neon.tech и зарегистрироваться (Google/GitHub быстрее).
2. После логина создать новый проект:
   - **Project name:** `udp`
   - **Postgres version:** 16
   - **Region:** ближайший (например, `Frankfurt` или `Stockholm`).
3. Neon создаст дефолтную базу `neondb` и пользователя.

## 2. Создать целевую БД и пользователя

В Neon Console → проект → SQL Editor:

```sql
CREATE DATABASE udp;
CREATE USER udp_app WITH PASSWORD 'СГЕНЕРИРОВАТЬ_СИЛЬНЫЙ_ПАРОЛЬ';
GRANT ALL PRIVILEGES ON DATABASE udp TO udp_app;
```

Переключиться на БД `udp` (в выпадашке наверху SQL-редактора), затем:

```sql
GRANT ALL ON SCHEMA public TO udp_app;
```

## 3. Получить connection string

В Neon Console → проект → Dashboard → Connection details:
- **Connection string** (Pooled connection НЕ берём — для backend с долгоживущими коннектами лучше прямое соединение).
- Выбрать пользователя `udp_app` и БД `udp`.

Формат:
```
postgresql://udp_app:ПАРОЛЬ@ep-xxx-xxx.eu-central-1.aws.neon.tech/udp?sslmode=require
```

Преобразовать схему URL для psycopg 3:
```
postgresql+psycopg://udp_app:ПАРОЛЬ@ep-xxx-xxx.eu-central-1.aws.neon.tech/udp?sslmode=require
```

(добавили `+psycopg`).

## 4. Сохранить в `backend/.env`

В `c:\Users\zhukov_v\Projects\UDP\backend\.env` (не коммитится) заменить старую строку:

```
DATABASE_URL=sqlite:///./database.db
```

на:

```
DATABASE_URL=postgresql+psycopg://udp_app:ПАРОЛЬ@ep-xxx-xxx.eu-central-1.aws.neon.tech/udp?sslmode=require
```

## 5. Включить pgvector

В SQL Editor (на БД `udp`):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

(Это же можно сделать миграцией Alembic в Task 7, но в Neon проще проверить факт активации руками сейчас.)

## Заметки

- **Auto-suspend:** на free tier Neon усыпляет compute после 5 минут неактивности. Первый запрос после паузы — холодный старт ~1–2 сек. Для dev нормально.
- **Лимиты free tier:** 0.5 GB storage, 191 compute hour/мес. Для проекта с парой тысяч документов — с большим запасом.
- **Backups:** Neon делает point-in-time restore (7 дней на free) автоматически, ничего настраивать не нужно.
```

- [ ] **Step 2: Закоммитить инструкцию**

```bash
git add docs/setup/neon-setup.md
git commit -m "docs: add Neon Postgres setup guide"
```

- [ ] **Step 3: Пользователь выполняет шаги 1–5 из инструкции**

Дождаться от пользователя подтверждения, что:
1. Neon-проект создан, БД `udp` существует, пользователь `udp_app` имеет права.
2. В `backend/.env` записан корректный `DATABASE_URL=postgresql+psycopg://...`.
3. Через SQL Editor выполнено `CREATE EXTENSION IF NOT EXISTS vector` (без ошибок).

**Не переходить к Task 2, пока эти 3 пункта не подтверждены.**

---

## Task 2: Обновить зависимости backend

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Обновить requirements.txt**

Прочитать `c:\Users\zhukov_v\Projects\UDP\backend\requirements.txt` и заменить полностью на:

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy==2.0.35
python-multipart==0.0.9
python-dotenv==1.0.1
httpx==0.27.0
openpyxl==3.1.5
boto3==1.35.0
psycopg[binary]==3.2.3
alembic==1.13.3
pgvector==0.3.6
```

- [ ] **Step 2: Установить зависимости**

Из директории `c:\Users\zhukov_v\Projects\UDP\backend\`:

```powershell
pip install -r requirements.txt
```

Ожидаем: установка успешна, без ошибок компиляции (psycopg[binary] поставляется готовым).

- [ ] **Step 3: Закоммитить**

```bash
git add backend/requirements.txt
git commit -m "deps: add psycopg, alembic, pgvector for postgres migration"
```

---

## Task 3: Переключить database.py на Postgres

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Обновить database.py**

Перезаписать файл `c:\Users\zhukov_v\Projects\UDP\backend\database.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://udp_app:CHANGE_ME@localhost:5432/udp",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Изменения:
- Убран `connect_args={"check_same_thread": False}` (SQLite-специфика).
- Добавлен `pool_pre_ping=True` — Neon усыпляет инстанс через 5 мин, без pre_ping первый запрос после пробуждения упадёт с broken connection.
- Явные `pool_size=5, max_overflow=10` — на free tier у Neon до ~100 соединений на compute, с запасом.

- [ ] **Step 2: Обновить backend/.env.example**

Прочитать `c:\Users\zhukov_v\Projects\UDP\backend\.env.example` и заменить строку 4:

Старое:
```
DATABASE_URL=sqlite:///./database.db
```

Новое:
```
# Neon: см. docs/setup/neon-setup.md
DATABASE_URL=postgresql+psycopg://udp_app:CHANGE_ME@ep-xxx.eu-central-1.aws.neon.tech/udp?sslmode=require
```

- [ ] **Step 3: Smoke-test подключения**

Запустить из `c:\Users\zhukov_v\Projects\UDP\backend\`:

```powershell
python -c "from database import engine; from sqlalchemy import text; conn = engine.connect(); print(conn.execute(text('select version()')).scalar()); conn.close()"
```

Ожидаем: вывод вида `PostgreSQL 16.x ... on x86_64-pc-linux-gnu`.

Если ошибка `psycopg.OperationalError: SSL connection has been closed unexpectedly` — проверить, что в URL есть `?sslmode=require` (Neon принимает только TLS).

- [ ] **Step 4: Закоммитить**

```bash
git add backend/database.py backend/.env.example
git commit -m "feat: switch database driver to postgres+psycopg"
```

---

## Task 4: Инициализировать Alembic

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/<hash>_initial_schema.py` (через autogenerate)
- Modify: `backend/main.py`

- [ ] **Step 1: Запустить alembic init**

Из `c:\Users\zhukov_v\Projects\UDP\backend\`:

```powershell
alembic init alembic
```

Ожидаем: создаются `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`.

- [ ] **Step 2: Настроить alembic.ini**

Открыть `c:\Users\zhukov_v\Projects\UDP\backend\alembic.ini`. Найти строку `sqlalchemy.url =` (примерно строка 63) и заменить на:

```
sqlalchemy.url =
```

(пустое значение — URL будем брать из env.py через переменную окружения).

- [ ] **Step 3: Настроить alembic/env.py**

Перезаписать `c:\Users\zhukov_v\Projects\UDP\backend\alembic\env.py`:

```python
from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Чтобы импорты "from database import ..." работали из alembic/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from database import Base  # noqa: E402
import models  # noqa: F401,E402  -- регистрируем модели в Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

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
```

- [ ] **Step 4: Убрать create_all из main.py**

Прочитать `c:\Users\zhukov_v\Projects\UDP\backend\main.py` и удалить строку 19:

```python
Base.metadata.create_all(bind=engine)
```

Импорт `from database import engine, Base` менять не нужно — `engine` всё ещё может использоваться где-то, а `Base` после удаления строки 19 станет неиспользуемым в этом файле; если pyflakes/линтер пожалуются — оставить только `from database import engine`.

- [ ] **Step 5: Сгенерировать первую миграцию**

Из `c:\Users\zhukov_v\Projects\UDP\backend\` с уже работающим Neon-подключением:

```powershell
alembic revision --autogenerate -m "initial schema"
```

Ожидаем: создан файл `alembic/versions/<hash>_initial_schema.py`. Открыть его и убедиться, что внутри `op.create_table(...)` для всех 7 таблиц: `projects`, `material_classes`, `reference_prices`, `documents`, `invoices`, `invoice_items`, `price_calculations`. Если какой-то таблицы нет — autogenerate не подхватил импорт, проверить что в `env.py` есть `import models`.

- [ ] **Step 6: Применить миграцию**

```powershell
alembic upgrade head
```

Ожидаем: `INFO  [alembic.runtime.migration] Running upgrade  -> <hash>, initial schema`.

Проверить таблицы:

```powershell
python -c "from database import engine; from sqlalchemy import inspect; insp = inspect(engine); print(sorted(insp.get_table_names()))"
```

Ожидаем: `['alembic_version', 'documents', 'invoice_items', 'invoices', 'material_classes', 'price_calculations', 'projects', 'reference_prices']`.

- [ ] **Step 7: Закоммитить**

```bash
git add backend/alembic.ini backend/alembic/ backend/main.py
git commit -m "feat: introduce alembic migrations for postgres schema"
```

---

## Task 5: Включить расширение pgvector через миграцию

**Files:**
- Create: `backend/alembic/versions/0002_enable_pgvector.py`

В Task 1 пользователь уже выполнил `CREATE EXTENSION IF NOT EXISTS vector` через Neon SQL Editor — это для проверки, что pgvector доступен. Но в репозитории должна быть миграция, иначе при деплое на чистую БД (например, новый Neon-проект для прода) расширение придётся включать вручную. Делаем явную миграцию.

- [ ] **Step 1: Узнать revision-id предыдущей миграции**

```powershell
alembic current
```

Запомнить hash (например `a1b2c3d4e5f6`).

- [ ] **Step 2: Создать файл миграции**

Создать `c:\Users\zhukov_v\Projects\UDP\backend\alembic\versions\0002_enable_pgvector.py`:

```python
"""enable pgvector extension

Revision ID: 0002_enable_pgvector
Revises: <PUT_PREVIOUS_REVISION_ID_HERE>
Create Date: 2026-05-10

"""
from alembic import op


revision = "0002_enable_pgvector"
down_revision = "<PUT_PREVIOUS_REVISION_ID_HERE>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
```

Заменить `<PUT_PREVIOUS_REVISION_ID_HERE>` (в двух местах) на hash, полученный командой `alembic current`.

- [ ] **Step 3: Применить миграцию**

```powershell
alembic upgrade head
```

Ожидаем: `INFO  [alembic.runtime.migration] Running upgrade <prev> -> 0002_enable_pgvector, enable pgvector extension`. Поскольку в Neon мы уже выполнили `CREATE EXTENSION` руками, реальной работы не будет (`IF NOT EXISTS`), но Alembic-история станет согласованной.

- [ ] **Step 4: Проверить, что расширение установлено**

```powershell
python -c "from database import engine; from sqlalchemy import text; conn = engine.connect(); print(conn.execute(text(\"SELECT extname, extversion FROM pg_extension WHERE extname='vector'\")).all()); conn.close()"
```

Ожидаем: `[('vector', '0.7.x')]` (версия может отличаться, главное — кортеж не пустой).

- [ ] **Step 5: Закоммитить**

```bash
git add backend/alembic/versions/0002_enable_pgvector.py
git commit -m "feat: enable pgvector extension via alembic migration"
```

---

## Task 6: Скрипт переноса данных из SQLite

**Files:**
- Create: `backend/scripts/migrate_sqlite_to_postgres.py`

Контекст: исходный `backend/database.db` (SQLite) лежит в репо. Postgres (Neon) уже пуст (только что мигрировали схему). Нужно перелить projects, material_classes, reference_prices, и опционально documents/invoices/invoice_items/price_calculations (пользователь подтверждал, что documents у него уже сброшены — на всякий случай переливаем всё, что найдём).

- [ ] **Step 1: Создать скрипт миграции**

Создать `c:\Users\zhukov_v\Projects\UDP\backend\scripts\migrate_sqlite_to_postgres.py`:

```python
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
    Project, MaterialClass, ReferencePrice,
    Document, Invoice, InvoiceItem, PriceCalculation,
)

# Порядок важен: parent → child (FK constraints)
TABLES = [
    Project,
    MaterialClass,
    ReferencePrice,
    Document,
    Invoice,
    InvoiceItem,
    PriceCalculation,
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
```

- [ ] **Step 2: Сделать резервную копию SQLite**

Из `c:\Users\zhukov_v\Projects\UDP\backend\` (PowerShell):

```powershell
Copy-Item database.db database.db.backup
```

- [ ] **Step 3: Запустить миграцию данных**

```powershell
python scripts/migrate_sqlite_to_postgres.py
```

Ожидаем вывод вида:
```
=== Migration ===
FROM: sqlite:///c:\Users\zhukov_v\Projects\UDP\backend\database.db
TO:   postgresql+psycopg://udp_app:***@ep-xxx.aws.neon.tech/udp?sslmode=require

[ok]   projects: перенесено N строк
[ok]   material_classes: перенесено M строк
[ok]   reference_prices: перенесено K строк
[skip] documents: пусто в SQLite
...

=== Sync sequences ===
[ok]   projects_id_seq
...

Готово. Всего перенесено строк: <X>
```

- [ ] **Step 4: Smoke-test данных**

```powershell
python -c "from database import SessionLocal; from models import Project, MaterialClass, ReferencePrice; db = SessionLocal(); print('projects:', db.query(Project).count()); print('material_classes:', db.query(MaterialClass).count()); print('reference_prices:', db.query(ReferencePrice).count()); db.close()"
```

Должны увидеть те же количества, что были в SQLite.

- [ ] **Step 5: Закоммитить скрипт**

```bash
git add backend/scripts/migrate_sqlite_to_postgres.py
git commit -m "feat: add one-shot SQLite-to-Postgres data migration script"
```

---

## Task 7: End-to-end проверка приложения

**Files:**
- (нет изменений, только верификация)

- [ ] **Step 1: Запустить MinIO (как обычно)**

Если ещё не запущен — из корня проекта:

```powershell
.\minio.exe server .\minio-data --console-address ":9001"
```

(или как у пользователя настроено).

- [ ] **Step 2: Запустить backend**

Из `c:\Users\zhukov_v\Projects\UDP\backend\`:

```powershell
uvicorn main:app --reload
```

Ожидаем: запускается на `http://127.0.0.1:8000`, в логах нет ошибок подключения к БД, логируется `MinIO bucket готов`.

- [ ] **Step 3: Проверить health endpoint**

В отдельном терминале:

```powershell
curl http://localhost:8000/api/health
```

Ожидаем: `{"status":"ok"}`.

- [ ] **Step 4: Проверить API объектов и классов**

```powershell
curl http://localhost:8000/api/projects
curl http://localhost:8000/api/material-classes
curl http://localhost:8000/api/reference-prices
```

Ожидаем: возвращаются те же сущности, что были в SQLite.

- [ ] **Step 5: Запустить frontend и проверить полный путь**

В отдельном терминале из `c:\Users\zhukov_v\Projects\UDP\frontend\`:

```powershell
npm run dev
```

Открыть `http://localhost:5173` (или порт, который покажет Vite). Прокликать:
- Dashboard — должны грузиться объекты, классы, эталоны.
- Reference Prices — должен видеть существующие записи.
- Upload — попробовать загрузить один PDF, дождаться парсинга, проверить что invoice появился в Review.

Если что-то отваливается — лог backend покажет конкретные SQL-ошибки. Типовые проблемы и фиксы:
- `column ... does not exist` — Alembic не сгенерировал что-то из модели; запустить `alembic revision --autogenerate -m "fix"` и `alembic upgrade head`.
- `null value in column ... violates not-null constraint` — в SQLite было NULL, в Postgres NOT NULL строже; найти строку и либо поправить модель (`nullable=True`), либо данные.
- `current transaction is aborted` — где-то в коде ловится исключение без `db.rollback()`; в Postgres это критично. Найти место и добавить rollback.
- Холодный старт (~1–2 сек) на первом запросе после паузы Neon — это нормально, не баг.

- [ ] **Step 6: Закоммитить любые фиксы, если возникли**

Если в Step 5 пришлось править код или добавлять миграции:

```bash
git add backend/
git commit -m "fix: postgres compatibility issues found during e2e test"
```

Если всё прошло без правок — коммит не нужен.

---

## Task 8: Удалить старый SQLite-файл

**Files:**
- Modify: `.gitignore` (опционально)
- Delete: `backend/database.db`, `backend/database.db.backup`

- [ ] **Step 1: Удалить SQLite файлы**

После того, как пользователь подтвердил в Task 7, что приложение работает с Neon:

```powershell
Remove-Item c:\Users\zhukov_v\Projects\UDP\backend\database.db
Remove-Item c:\Users\zhukov_v\Projects\UDP\backend\database.db.backup
```

- [ ] **Step 2: Финальный коммит**

Если `database.db` был под `git rm` (он в `.gitignore`, поэтому скорее всего нет — проверить `git status`):

```bash
git status
git add -A
git commit -m "chore: remove obsolete sqlite database files"
```

Если `git status` чист — коммит не нужен.

---

## Self-Review

**1. Spec coverage:**
- Postgres без локальной установки → Neon (Task 1). ✓
- pgvector для будущего RAG → Task 1 (CREATE EXTENSION в Neon) + Task 5 (миграция в репо). ✓
- jsonb — текущая схема не имеет JSON-полей; SQLAlchemy `JSON` в Postgres автоматически становится JSONB. Отдельной задачи не нужно. ✓
- Alembic для миграций → Task 4, 5. ✓
- Перенос существующих данных → Task 6. ✓
- MinIO остаётся как сейчас → подтверждено, ничего не трогаем. ✓
- Близкий-к-проду переезд на свой Postgres + другой S3 — явно вне scope этого плана. ✓

**2. Placeholder scan:**
- `<PUT_PREVIOUS_REVISION_ID_HERE>` в Task 5 — обоснованный плейсхолдер: revision-id Alembic генерирует случайно. В step есть инструкция, как получить (`alembic current`) и куда вставить (две строки в файле миграции).
- `СГЕНЕРИРОВАТЬ_СИЛЬНЫЙ_ПАРОЛЬ` и `CHANGE_ME` в инструкции и `.env.example` — это явные маркеры пользовательских значений, не плейсхолдеры в коде. Корректно.
- Остальное — конкретный код, конкретные команды, ожидаемый вывод.

**3. Type consistency:**
- Названия таблиц в `migrate_sqlite_to_postgres.py` (`projects`, `material_classes`, ...) совпадают с `__tablename__` в `models.py`. ✓
- Имена sequences (`{table}_id_seq`) — Postgres-конвенция SQLAlchemy при `Integer primary_key`. ✓
- `DATABASE_URL` имеет одинаковый формат во всех файлах (`postgresql+psycopg://...`). ✓
- Имя `psycopg` в requirements (`psycopg[binary]`) и в URL (`postgresql+psycopg://`) совпадают (psycopg 3, не psycopg2). ✓
- `Base` импорт в `env.py` и `models.py` — оба берут из `database.py`. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-10-postgres-migration.md`.

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import settings

DATABASE_URL = settings.DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
    connect_args={
        "application_name": "gca_backend",
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """FastAPI-зависимость: фабрика сессий для фоновой работы.

    Пайплайн импорта работает на ДВУХ независимых сессиях (AGENTS.md §5) и живёт
    дольше запроса, поэтому одной `get_db`-сессии ему недостаточно. Отдельная
    зависимость нужна ещё и для тестов: подменив её, интеграционный тест уводит
    фоновую задачу на тестовый engine вместо БД приложения.
    """
    return SessionLocal

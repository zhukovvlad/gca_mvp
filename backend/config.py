"""Централизованная конфигурация через pydantic-settings.

Основной способ читать переменные окружения в коде приложения — через объект
settings, а не через os.getenv() напрямую. Для инфраструктурных модулей
(alembic/env.py, tooling-скрипты) допустимы исключения.
"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file абсолютным: относительный путь делал значения зависимыми от CWD процесса.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env", extra="ignore"
    )

    # JWT / безопасность
    SECRET_KEY: str = Field(min_length=32)  # обязательное поле — при отсутствии в .env запуск упадёт
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # Куки
    COOKIE_SECURE: bool = False  # True в проде (HTTPS)
    COOKIE_DOMAIN: str | None = None

    # Количество доверенных reverse-proxy в цепочке (0 = прямое соединение; X-Forwarded-For игнорируется)
    TRUSTED_PROXIES: int = 0

    # CORS — wildcard "*" несовместим с credentials=True в браузере
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]

    # База данных
    DATABASE_URL: str = "postgresql+psycopg://postgres@localhost:5459/gca_dev"

    # Роль окружения. ИНВАРИАНТ: единственный потребитель APP_ENV — db_guard.
    APP_ENV: Literal["dev", "prod"] = "dev"
    # Дополнительные цели, мутируемые при APP_ENV=dev: host:port/dbname через
    # запятую. Loopback разрешён и без этого списка.
    DB_EXTRA_TARGETS: str = ""

    # Файловое хранилище (AGENTS.md §8): локальная директория за абстракцией Storage
    STORAGE_DIR: str = str(Path(__file__).parent.parent / "storage")
    # Лимит размера загружаемого XLSX, МБ (AGENTS.md §5)
    UPLOAD_MAX_SIZE_MB: int = 25
    # Мягкий таймаут пайплайна импорта, минут (AGENTS.md §3, §5)
    IMPORT_SOFT_TIMEOUT_MINUTES: int = 10
    # Ретенция файлов error-jobs, дней (AGENTS.md §8)
    ERROR_JOB_FILE_RETENTION_DAYS: int = 30

    # Логирование
    LOG_LEVEL: str = "INFO"


settings = Settings()

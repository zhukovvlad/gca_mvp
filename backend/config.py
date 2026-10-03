"""Централизованная конфигурация через pydantic-settings.

Основной способ читать переменные окружения в коде приложения — через объект
settings, а не через os.getenv() напрямую. Для инфраструктурных модулей
(alembic/env.py, tooling-скрипты) допустимы исключения.
"""
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    # Выполнять обслуживание при старте приложения: startup-recovery зависших
    # import_jobs (AGENTS.md §5) и ретенцию файлов error-jobs (§8).
    # False только в тестах: lifespan запускается на реальном engine приложения,
    # мимо транзакционной фикстуры, — иначе TestClient мутировал бы dev-БД.
    RUN_STARTUP_MAINTENANCE: bool = True

    # Логирование
    LOG_LEVEL: str = "INFO"

    # Семантические предложения (спека 2026-09-28-semantic-suggestions-design.md
    # §2.2, §2.11): ключ провайдера — секрет, не пишется в репозиторий; тарифы —
    # Decimal (§3 AGENTS.md, "деньги"), а не константы кода, потому что
    # маршрутизация провайдера может сменить цену без правки кода.
    # Числа проверяются при загрузке: ноль потоков или отрицательная цена иначе
    # молча ломают очередь уже после старта.
    OPENROUTER_API_KEY: str = ""
    RUN_SEMANTIC_WORKER: bool = False
    SEMANTIC_MODEL: str = "anthropic/claude-sonnet-5"
    SEMANTIC_MAX_TOKENS: int = Field(600, ge=1)
    SEMANTIC_CONCURRENCY: int = Field(4, ge=1)
    SEMANTIC_CALL_TIMEOUT_S: int = Field(120, ge=1)
    # Сколько ждать текущие вызовы при остановке приложения: короче таймаута вызова,
    # чтобы перезапуск (и каждый `--reload`) не висел до 120 с на зависшем запросе.
    SEMANTIC_SHUTDOWN_WAIT_S: int = Field(15, ge=0)
    SEMANTIC_MAX_ATTEMPTS: int = Field(3, ge=1)
    SEMANTIC_PRICE_INPUT_PER_M: Decimal = Field(Decimal("2"), ge=0)
    SEMANTIC_PRICE_CACHE_WRITE_PER_M: Decimal = Field(Decimal("2.5"), ge=0)
    SEMANTIC_PRICE_CACHE_READ_PER_M: Decimal = Field(Decimal("0.2"), ge=0)
    SEMANTIC_PRICE_OUTPUT_PER_M: Decimal = Field(Decimal("10"), ge=0)
    SEMANTIC_DAILY_BUDGET_USD: Decimal = Field(Decimal("30"), ge=0)
    SEMANTIC_EVENT_MAX_CONTEXTS: int = Field(3000, ge=0)
    SEMANTIC_EVENT_MAX_RESERVE_USD: Decimal = Field(Decimal("15"), ge=0)

    # Варианты работ (спека 2026-10-02-catalog-variants-design.md §2.3, §2.5):
    # профиль модели и тарифы по виду задания. Предложения семей остаются на
    # SEMANTIC_MODEL / SEMANTIC_MAX_TOKENS / SEMANTIC_PRICE_*. Рассуждение у
    # моделей этих видов обязательно (выключить нельзя), поэтому задаётся
    # уровень, а не флаг.
    SEMANTIC_SCHEMA_MODEL: str = "anthropic/claude-sonnet-5.5"
    SEMANTIC_VALUES_MODEL: str = "anthropic/claude-sonnet-5.5"
    SEMANTIC_VARIANTS_REASONING_EFFORT: Literal["low", "medium", "high"] = "low"
    SEMANTIC_SCHEMA_MAX_TOKENS: int = Field(20000, ge=1)
    SEMANTIC_VALUES_MAX_TOKENS: int = Field(600, ge=1)
    SEMANTIC_SCHEMA_PRICE_INPUT_PER_M: Decimal = Field(Decimal("2"), ge=0)
    SEMANTIC_SCHEMA_PRICE_CACHE_WRITE_PER_M: Decimal = Field(Decimal("2.5"), ge=0)
    SEMANTIC_SCHEMA_PRICE_CACHE_READ_PER_M: Decimal = Field(Decimal("0.2"), ge=0)
    SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M: Decimal = Field(Decimal("10"), ge=0)
    SEMANTIC_VALUES_PRICE_INPUT_PER_M: Decimal = Field(Decimal("2"), ge=0)
    SEMANTIC_VALUES_PRICE_CACHE_WRITE_PER_M: Decimal = Field(Decimal("2.5"), ge=0)
    SEMANTIC_VALUES_PRICE_CACHE_READ_PER_M: Decimal = Field(Decimal("0.2"), ge=0)
    SEMANTIC_VALUES_PRICE_OUTPUT_PER_M: Decimal = Field(Decimal("10"), ge=0)
    # Порог уверенности модели в предложении семьи (спека §2.5): при значении не
    # ниже порога предложенная семья принимается автоматически; None - автопринятия нет.
    # Пустая строка в окружении (`SEMANTIC_AUTO_ACCEPT_THRESHOLD=`) - тоже None.
    SEMANTIC_AUTO_ACCEPT_THRESHOLD: Decimal | None = Field(None, gt=0, le=1)

    @field_validator("SEMANTIC_AUTO_ACCEPT_THRESHOLD", mode="before")
    @classmethod
    def _empty_threshold_is_none(cls, value):
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


settings = Settings()

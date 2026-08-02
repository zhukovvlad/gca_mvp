"""Централизованная конфигурация через pydantic-settings.

Основной способ читать переменные окружения в коде приложения — через объект
settings, а не через os.getenv() напрямую. Для инфраструктурных модулей
(alembic/env.py, tooling-скрипты) допустимы исключения.
"""
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file абсолютным: относительный путь делал значения зависимыми от CWD
    # процесса (закрыто по docs/TECH_DEBT.md). Роутер настроек передаёт свой
    # ENV_PATH через Settings(_env_file=...).
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
    DATABASE_URL: str = "postgresql+psycopg://udp_app:CHANGE_ME@localhost:5432/udp"

    # Роль окружения. ИНВАРИАНТ: единственный потребитель APP_ENV — db_guard.
    # Новый потребитель обязан пересмотреть деплойную таблицу спеки: при
    # DATABASE_URL на loopback забытый APP_ENV=prod НЕ роняет старт — guard
    # разрешает loopback безусловно, и процесс молча работает в dev-режиме.
    APP_ENV: Literal["dev", "prod"] = "dev"
    # Дополнительные цели, мутируемые при APP_ENV=dev: host:port/dbname через
    # запятую. Loopback разрешён и без этого списка; каждая запись — полная
    # тройка (без порта или dbname — ошибка валидации в db_guard).
    DB_EXTRA_TARGETS: str = ""

    # MinIO / S3
    S3_ENDPOINT: str = "http://localhost:9259"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "invoices"

    # OpenRouter
    OPENROUTER_API_KEY: str = ""
    AI_MODEL: str = "anthropic/claude-sonnet-5"
    AI_MAX_TOKENS: int | None = None  # deprecated-алиас OPENROUTER_MAX_TOKENS; None = не задан
    CONFIDENCE_THRESHOLD: float = 0.7
    PDF_ENGINE: str | None = None  # deprecated-алиас OPENROUTER_PDF_ENGINE; None = не задан
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # LLM-провайдер (спека 2026-07-23): deploy-time enum + namespaced-настройки.
    LLM_PROVIDER: Literal["openrouter", "gateway"] = "openrouter"

    # namespaced openrouter; пустое значение → алиас-цепочка (resolved_* ниже)
    OPENROUTER_MODEL: str = ""
    OPENROUTER_PDF_ENGINE: str = ""
    OPENROUTER_MAX_TOKENS: int | None = None
    # namespaced gateway; auth-переменные финализируются после спайка (§7 спеки)
    GATEWAY_BASE_URL: str = ""
    GATEWAY_MODEL: str = ""

    # Логирование
    LOG_LEVEL: str = "INFO"


# Deprecation-предупреждения об алиасах логируем один раз на процесс: резолверы
# дёргаются часто (GET /api/settings собирает свежий Settings() на каждый запрос,
# parse-путь — на каждый разбор), спамить лог одинаковой строкой не нужно.
_warned_deprecated_aliases: set[str] = set()


def _warn_deprecated_alias_once(key: str, message: str) -> None:
    """Залогировать предупреждение об устаревшем алиасе не более одного раза за процесс."""
    if key not in _warned_deprecated_aliases:
        _warned_deprecated_aliases.add(key)
        logging.getLogger(__name__).warning(message)


def resolved_openrouter_model(s: "Settings") -> str:
    """OPENROUTER_MODEL → deprecated AI_MODEL (warning) → дефолт (§1 спеки).

    Пробельные значения = отсутствие — И у namespaced, И у legacy (guard §1).
    """
    if s.OPENROUTER_MODEL.strip():
        return s.OPENROUTER_MODEL.strip()
    legacy = s.AI_MODEL.strip()
    if legacy and legacy != "anthropic/claude-sonnet-5":
        _warn_deprecated_alias_once(
            "AI_MODEL", "AI_MODEL устарел — используйте OPENROUTER_MODEL")
    return legacy or "anthropic/claude-sonnet-5"


def resolved_openrouter_pdf_engine(s: "Settings") -> str:
    """OPENROUTER_PDF_ENGINE → deprecated PDF_ENGINE (warning, если задан) → native.

    Дефолт native, а не mistral-ocr: последний нестабилен на СФ с 60+ строками,
    и фактические .env давно используют native (docs/TECH_DEBT.md).
    PDF_ENGINE опционален (None = не задан), поэтому «не задано» отличимо от
    «задано значением, равным дефолту» — предупреждение выдаётся только при
    реальном использовании legacy-переменной, а не на чистой установке. Прежнее
    условие `legacy != "mistral-ocr"` глушило warning ровно для значения из
    легаси-.env, из-за чего переменная не имела пути к удалению; снятие условия
    и смена дефолта — одно неделимое изменение, порознь они друг друга ломают.
    """
    if s.OPENROUTER_PDF_ENGINE.strip():
        return s.OPENROUTER_PDF_ENGINE.strip()
    legacy = (s.PDF_ENGINE or "").strip()
    if legacy:
        _warn_deprecated_alias_once(
            "PDF_ENGINE", "PDF_ENGINE устарел — используйте OPENROUTER_PDF_ENGINE")
    return legacy or "native"


def resolved_openrouter_base_url(s: "Settings") -> str:
    """OPENROUTER_BASE_URL → дефолт; пробельная строка = отсутствие (guard §1).

    Правило живёт в config, а не внутри провайдера — единый источник нормализации.
    """
    return s.OPENROUTER_BASE_URL.strip() or "https://openrouter.ai/api/v1"


def resolved_openrouter_max_tokens(s: "Settings") -> int:
    """OPENROUTER_MAX_TOKENS → deprecated AI_MAX_TOKENS (warning) → 64000 (AC-1).

    Оба поля опциональны, поэтому «не задано» отличимо от «задано значением,
    равным дефолту» — и предупреждение выдаётся только при реальном
    использовании legacy-переменной, а не на чистой установке.
    """
    if s.OPENROUTER_MAX_TOKENS is not None:
        return s.OPENROUTER_MAX_TOKENS
    if s.AI_MAX_TOKENS is not None:
        _warn_deprecated_alias_once(
            "AI_MAX_TOKENS", "AI_MAX_TOKENS устарел — используйте OPENROUTER_MAX_TOKENS")
        return s.AI_MAX_TOKENS
    return 64000


def resolved_llm_parse_max_tokens(s: "Settings") -> int:
    """Нейтральный лимит parse-вызова: домен (pdf_parser) не знает провайдера.

    openrouter → цепочка OPENROUTER_MAX_TOKENS→AI_MAX_TOKENS→64000;
    gateway → RuntimeError до спайка (там появится GATEWAY_MAX_TOKENS, §7 спеки).
    """
    if s.LLM_PROVIDER == "openrouter":
        return resolved_openrouter_max_tokens(s)
    raise RuntimeError("LLM_PROVIDER=gateway: лимит parse-вызова определяется после gateway-спайка")


def validate_llm_settings(s: "Settings") -> None:
    """Fail-fast §1: обязательные поля ВЫБРАННОГО провайдера; openrouter-ключ НЕ проверяется.

    Пустая/пробельная строка base URL = отсутствие значения: openrouter
    лечится дефолтом, gateway — обязан быть задан (дефолта нет).
    Резолвнутые model/pdf_engine у openrouter непусты по построению (дефолты).
    """
    if s.LLM_PROVIDER == "openrouter":
        if not resolved_openrouter_model(s).strip():
            raise RuntimeError("openrouter: пустая модель после алиас-резолва")
        return
    if not s.GATEWAY_BASE_URL.strip():
        raise RuntimeError("LLM_PROVIDER=gateway: не задан GATEWAY_BASE_URL")
    if not s.GATEWAY_MODEL.strip():
        raise RuntimeError("LLM_PROVIDER=gateway: не задан GATEWAY_MODEL")


settings = Settings()

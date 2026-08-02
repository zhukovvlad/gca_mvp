"""Абстракция LLM-провайдера: типы интерфейса + service locator.

Спека: docs/superpowers/specs/2026-07-23-llm-provider-toggle-design.md (§2).
Доменный парсинг (JSON УПД, повороты) сюда НЕ заходит. ЗАПРЕЩЁН импорт из
pdf_orientation (цикл, §2.2). Локатор — осознанный service locator: тесты
кодовой базы стоят на module-level monkeypatch, BackgroundTasks не требуют
протаскивания параметра (§2.3).
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from config import Settings, validate_llm_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfAttachment:
    """PDF целиком; способ подачи выбирает провайдер."""
    data: bytes
    filename: str = "document.pdf"


@dataclass(frozen=True)
class ImagesAttachment:
    """Постраничные JPEG-рендеры (detect_rotations)."""
    images: tuple[bytes, ...]


Attachment = PdfAttachment | ImagesAttachment


@dataclass
class LLMResponse:
    """Нормализованный успешный ответ провайдера (§2.1)."""
    content: str
    finish_reason: str | None
    cost_usd: Decimal          # всегда Decimal; gateway → Decimal(0)
    completion_tokens: int | None
    paid_calls: int


class LLMProviderError(Exception):
    """Нормализованная ошибка провайдера; несёт биллинг платного 200 (§2.3).

    str(exc) — безопасное сообщение без содержимого ответа/токенов.
    """

    def __init__(self, message: str, *, retryable: bool, code: str | None = None,
                 cost_usd: Decimal = Decimal(0), paid_calls: int = 0,
                 correlation_id: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.cost_usd = cost_usd
        self.paid_calls = paid_calls
        self.correlation_id = correlation_id


class LLMProvider(Protocol):
    """Единственный метод провайдера: промпт + вложение → текст и метаданные."""

    async def vision_completion(self, *, system: str | None, user_text: str,
                                attachment: Attachment, max_tokens: int,
                                timeout: httpx.Timeout) -> LLMResponse: ...


_provider: LLMProvider | None = None


def _build(settings: Settings) -> LLMProvider:
    """Собрать провайдер по LLM_PROVIDER. Без сетевых запросов (инвариант §2.3)."""
    validate_llm_settings(settings)
    if settings.LLM_PROVIDER == "openrouter":
        from llm_openrouter import OpenRouterProvider  # локальный импорт против цикла
        return OpenRouterProvider.from_settings(settings)
    raise RuntimeError(
        "LLM_PROVIDER=gateway: GatewayProvider будет реализован после gateway-спайка (спека §7)")


def init_provider(settings: Settings) -> None:
    """Собрать и АТОМАРНО заменить ссылку локатора (lifespan и PUT /settings)."""
    global _provider
    _provider = _build(settings)


def get_provider() -> LLMProvider:
    """Текущий провайдер; до init_provider() — понятный RuntimeError."""
    if _provider is None:
        raise RuntimeError(
            "LLM-провайдер не инициализирован: init_provider() вызывается в lifespan")
    return _provider


def reset_provider() -> None:
    """Очистить локатор (lifespan teardown, scoped reset в тестах)."""
    global _provider
    _provider = None

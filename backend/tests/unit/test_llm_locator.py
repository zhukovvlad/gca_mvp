"""Тесты service locator LLM-провайдера: инварианты §2.3 спеки."""
import pytest

import llm
from config import Settings


def _settings(**kw) -> Settings:
    """Settings без .env для изоляции теста."""
    base = {"SECRET_KEY": "x" * 32}
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _clean_locator():
    """Каждый тест стартует и заканчивает с пустым локатором (scoped reset)."""
    llm.reset_provider()
    yield
    llm.reset_provider()


def test_get_before_init_raises_runtime_error():
    """До init_provider() — понятный RuntimeError (инвариант §2.3)."""
    with pytest.raises(RuntimeError, match="не инициализирован"):
        llm.get_provider()


def test_gateway_not_implemented_yet():
    """LLM_PROVIDER=gateway до спайка — понятная ошибка фабрики, не тихий сбой."""
    with pytest.raises(RuntimeError, match="спайк"):
        llm.init_provider(_settings(
            LLM_PROVIDER="gateway", GATEWAY_BASE_URL="http://gw", GATEWAY_MODEL="m"))


def test_reset_clears_provider():
    """reset_provider() возвращает локатор в неинициализированное состояние."""
    llm.reset_provider()
    with pytest.raises(RuntimeError):
        llm.get_provider()


def test_init_openrouter_and_get():
    """init_provider(openrouter) устанавливает OpenRouterProvider."""
    llm.init_provider(_settings(OPENROUTER_API_KEY="sk-t"))
    from llm_openrouter import OpenRouterProvider
    assert isinstance(llm.get_provider(), OpenRouterProvider)


def test_provider_error_carries_billing():
    """LLMProviderError несёт cost/paid/code/correlation_id (инвариант §2.3)."""
    from decimal import Decimal
    e = llm.LLMProviderError("boom", retryable=False, code="x",
                             cost_usd=Decimal("0.1"), paid_calls=1, correlation_id="cid")
    assert (e.retryable, e.code, e.cost_usd, e.paid_calls, e.correlation_id) == \
        (False, "x", Decimal("0.1"), 1, "cid")

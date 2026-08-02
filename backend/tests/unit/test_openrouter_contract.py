"""Контрактные тесты payload OpenRouter (AC-1 спеки): форма запроса = текущая, 1:1.

Порядок частей content — часть контракта. Плюс envelope-поведение:
биллинг платного 200 при битом теле (инвариант §2.3).
"""
import json
from decimal import Decimal

import httpx
import pytest
import respx

import llm
from config import Settings
from llm_openrouter import OpenRouterProvider

URL = "https://openrouter.ai/api/v1/chat/completions"
OK_BODY = {
    "choices": [{"message": {"content": "ответ"}, "finish_reason": "stop"}],
    "usage": {"cost": 0.01, "completion_tokens": 5},
}


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """Гермет: Settings в тестах не должен зависеть от env разработчика/.env.test."""
    for var in ("LLM_PROVIDER", "OPENROUTER_MODEL", "OPENROUTER_PDF_ENGINE",
                "OPENROUTER_MAX_TOKENS", "OPENROUTER_BASE_URL", "OPENROUTER_API_KEY",
                "GATEWAY_BASE_URL", "GATEWAY_MODEL",
                "AI_MODEL", "AI_MAX_TOKENS", "PDF_ENGINE"):
        monkeypatch.delenv(var, raising=False)


# Захватываем настоящий AsyncClient.send на импорте модуля (до autouse-гарда
# block_real_openrouter из conftest, который рубит любые вызовы к openrouter.ai на
# уровне send). respx работает на transport-уровне — через настоящий send проходит,
# поэтому в respx-тестах восстанавливаем его (тот же приём, что в test_pdf_orientation.py
# и фикстуре mock_openrouter).
_REAL_SEND = httpx.AsyncClient.send


@pytest.fixture(autouse=True)
def _allow_respx(monkeypatch):
    """Восстанавливает настоящий AsyncClient.send, чтобы respx.mock мог перехватывать запросы."""
    monkeypatch.setattr(httpx.AsyncClient, "send", _REAL_SEND)


def _provider(**kw) -> OpenRouterProvider:
    """Провайдер из чистых Settings (без .env) с тестовым ключом."""
    base = {"SECRET_KEY": "x" * 32, "OPENROUTER_API_KEY": "sk-test"}
    base.update(kw)
    return OpenRouterProvider.from_settings(Settings(_env_file=None, **base))


@pytest.mark.asyncio
@respx.mock
async def test_parse_form_contract():
    """parse-форма: system+file первым+текст, plugins с engine, usage.include, max_tokens, auth."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    p = _provider(OPENROUTER_MODEL="test/model", OPENROUTER_PDF_ENGINE="native",
                  OPENROUTER_MAX_TOKENS=64000)
    resp = await p.vision_completion(
        system="SYS", user_text="извлеки",
        attachment=llm.PdfAttachment(data=b"%PDF-1.4"), max_tokens=64000,
        timeout=httpx.Timeout(180))
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["model"] == "test/model"
    assert body["max_tokens"] == 64000
    assert body["usage"] == {"include": True}
    assert body["plugins"] == [{"id": "file-parser", "pdf": {"engine": "native"}}]
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    parts = body["messages"][1]["content"]
    assert parts[0]["type"] == "file" and parts[1]["type"] == "text"
    assert resp.cost_usd == Decimal("0.01") and resp.paid_calls == 1
    assert resp.finish_reason == "stop" and resp.content == "ответ"


@pytest.mark.asyncio
@respx.mock
async def test_detect_form_contract():
    """detect-форма: без system, текст первым + image_url, БЕЗ plugins, max_tokens=200."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    p = _provider(OPENROUTER_MODEL="test/model")
    await p.vision_completion(
        system=None, user_text="повороты?",
        attachment=llm.ImagesAttachment(images=(b"\xff\xd8jpeg",)), max_tokens=200,
        timeout=httpx.Timeout(30, connect=5.0))
    body = json.loads(route.calls[0].request.content)
    assert "plugins" not in body
    assert body["max_tokens"] == 200
    assert body["messages"][0]["role"] == "user"  # system отсутствует
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text" and parts[1]["type"] == "image_url"


@pytest.mark.asyncio
@respx.mock
async def test_missing_key_is_permanent_error():
    """Без ключа — нерetryable ошибка с текущим текстом (поведение 1:1)."""
    p = _provider(OPENROUTER_API_KEY="")
    with pytest.raises(llm.LLMProviderError, match="API-ключ OpenRouter не настроен") as ei:
        await p.vision_completion(system=None, user_text="x",
                                  attachment=llm.ImagesAttachment(images=(b"j",)),
                                  max_tokens=200, timeout=httpx.Timeout(30))
    assert ei.value.retryable is False and ei.value.paid_calls == 0


@pytest.mark.asyncio
@respx.mock
async def test_http_status_classification():
    """5xx/408/429 → retryable; прочие — нет (симметрично текущему parse_pdf)."""
    route = respx.post(URL)  # один роут, мок меняется в цикле — без дублей паттерна
    for status, retryable in [(500, True), (429, True), (408, True), (403, False)]:
        route.mock(return_value=httpx.Response(status, json={}))
        p = _provider()
        with pytest.raises(llm.LLMProviderError, match=f"OpenRouter API ошибка: {status}") as ei:
            await p.vision_completion(system=None, user_text="x",
                                      attachment=llm.ImagesAttachment(images=(b"j",)),
                                      max_tokens=200, timeout=httpx.Timeout(30))
        assert ei.value.retryable is retryable and ei.value.paid_calls == 0


@pytest.mark.asyncio
@respx.mock
async def test_broken_envelope_keeps_billing():
    """HTTP 200 без choices → LLMProviderError(retryable=False, paid_calls=1, cost)."""
    respx.post(URL).mock(return_value=httpx.Response(200, json={"usage": {"cost": 0.02}}))
    p = _provider()
    with pytest.raises(llm.LLMProviderError, match="Ответ модели без содержимого") as ei:
        await p.vision_completion(system=None, user_text="x",
                                  attachment=llm.ImagesAttachment(images=(b"j",)),
                                  max_tokens=200, timeout=httpx.Timeout(30))
    assert ei.value.paid_calls == 1 and ei.value.cost_usd == Decimal("0.02")
    assert ei.value.retryable is False


@pytest.mark.asyncio
@respx.mock
async def test_non_json_body_keeps_billing_and_text():
    """HTTP 200 с не-JSON телом → «Не удалось разобрать ответ модели», paid=1 (тексты 1:1)."""
    respx.post(URL).mock(return_value=httpx.Response(200, content=b"<html>oops"))
    p = _provider()
    with pytest.raises(llm.LLMProviderError, match="Не удалось разобрать ответ модели") as ei:
        await p.vision_completion(system=None, user_text="x",
                                  attachment=llm.ImagesAttachment(images=(b"j",)),
                                  max_tokens=200, timeout=httpx.Timeout(30))
    assert ei.value.paid_calls == 1 and ei.value.retryable is False


@pytest.mark.asyncio
@respx.mock
async def test_cost_clamp_nan_negative():
    """NaN и отрицательный usage.cost клэмпятся в 0 (FIX B, поведение 1:1)."""
    route = respx.post(URL)
    for bad_cost in (-5, "NaN"):
        route.mock(return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "t"}, "finish_reason": "stop"}],
                       "usage": {"cost": bad_cost}}))
        p = _provider()
        resp = await p.vision_completion(system=None, user_text="x",
                                         attachment=llm.ImagesAttachment(images=(b"j",)),
                                         max_tokens=200, timeout=httpx.Timeout(30))
        assert resp.cost_usd == Decimal(0) and resp.paid_calls == 1


@pytest.mark.asyncio
@respx.mock
async def test_usage_null_returns_success():
    """Осознанный фикс TECH_DEBT (см. Global Constraints): usage:null не роняет разбор."""
    respx.post(URL).mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "t"}, "finish_reason": "stop"}],
                   "usage": None}))
    p = _provider()
    resp = await p.vision_completion(system=None, user_text="x",
                                     attachment=llm.ImagesAttachment(images=(b"j",)),
                                     max_tokens=200, timeout=httpx.Timeout(30))
    assert resp.cost_usd == Decimal(0)
    assert resp.completion_tokens is None
    assert resp.content == "t"

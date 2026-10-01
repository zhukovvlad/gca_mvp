"""Клиент модели `OpenRouterClient` (задача 10 фичи «Семантические
предложения»): классы ошибок, разбор ответа, деньги без `float`.

Сеть не открывается: `httpx.MockTransport`. Ответ в тесте — реальная форма
`usage` из замера пилота (`tasks/catalog-pilot-2026-09-18/feature2-measure/
cache_out.jsonl`), стоимость идёт в теле JSON-числом `0.022137`.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from services.semantic_client import (
    ModelResponse,
    OpenRouterClient,
    PermanentModelError,
    TransientModelError,
)

BODY = {"model": "anthropic/claude-test", "messages": [{"role": "user", "content": "привет"}]}

REAL_SHAPE = (
    '{"id": "gen-1", "model": "anthropic/claude-sonnet-5", "provider": "Claude Platform on AWS", '
    '"choices": [{"message": {"role": "assistant", "content": "{\\"family_id\\": 23}"}, '
    '"finish_reason": "stop"}], '
    '"usage": {"prompt_tokens": 8274, "completion_tokens": 156, "total_tokens": 8430, '
    '"cost": 0.022137, "is_byok": false, '
    '"prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 8058, '
    '"audio_tokens": 0, "video_tokens": 0}}}'
)


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _respond(status, text=""):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=text.encode("utf-8"))

    return handler


class TestRequest:
    def test_posts_the_body_to_chat_completions_with_the_headers_and_timeout(self):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["Authorization"]
            seen["referer"] = request.headers["HTTP-Referer"]
            seen["title"] = request.headers["X-Title"]
            seen["json"] = json.loads(request.content)
            seen["timeout"] = request.extensions["timeout"]
            return httpx.Response(200, content=REAL_SHAPE.encode("utf-8"))

        _client(handler).complete(BODY, timeout_s=42)

        assert seen["method"] == "POST"
        assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert seen["auth"] == "Bearer sk-test"
        assert seen["referer"] == "http://localhost"
        assert seen["title"]
        assert seen["json"] == BODY
        assert seen["timeout"] == {"connect": 42, "read": 42, "write": 42, "pool": 42}

    def test_base_url_can_be_replaced(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, content=REAL_SHAPE.encode("utf-8"))

        client = OpenRouterClient(
            api_key="k",
            base_url="http://provider.test/v9/",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        client.complete(BODY, timeout_s=1)

        assert seen == ["http://provider.test/v9/chat/completions"]


class TestResponse:
    def test_real_shape_is_parsed_and_cost_is_an_exact_decimal(self):
        response = _client(_respond(200, REAL_SHAPE)).complete(BODY, timeout_s=1)

        assert response == ModelResponse(
            content='{"family_id": 23}',
            actual_model="anthropic/claude-sonnet-5",
            provider="Claude Platform on AWS",
            prompt_tokens=8274,
            completion_tokens=156,
            cache_write_tokens=8058,
            cached_tokens=0,
            cost_usd=Decimal("0.022137"),
        )
        assert isinstance(response.cost_usd, Decimal)

    def test_a_float_that_binary_cannot_hold_is_not_corrupted(self):
        text = REAL_SHAPE.replace('"cost": 0.022137', '"cost": 0.1')

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert str(response.cost_usd) == "0.1"

    def test_cost_with_more_digits_than_a_float_holds_is_kept_exactly(self):
        # 23 значащие цифры: через `float` (и даже через `Decimal(str(float))`)
        # хвост потерялся бы.
        text = REAL_SHAPE.replace('"cost": 0.022137', '"cost": 0.02213712345678901234567')

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert response.cost_usd == Decimal("0.02213712345678901234567")

    def test_integer_cost_is_a_decimal(self):
        text = REAL_SHAPE.replace('"cost": 0.022137', '"cost": 0')

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert response.cost_usd == Decimal("0") and isinstance(response.cost_usd, Decimal)

    def test_missing_cost_is_none(self):
        text = REAL_SHAPE.replace('"cost": 0.022137, ', "")

        assert _client(_respond(200, text)).complete(BODY, timeout_s=1).cost_usd is None

    def test_missing_cache_details_are_zero(self):
        text = json.dumps(
            {
                "model": "m",
                "choices": [{"message": {"content": "ответ"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        )

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert (response.cache_write_tokens, response.cached_tokens) == (0, 0)
        assert (response.prompt_tokens, response.completion_tokens) == (10, 2)
        assert response.provider is None

    @pytest.mark.parametrize("content", ["", "   ", None])
    def test_empty_content_is_transient(self, content):
        text = json.dumps({"model": "m", "choices": [{"message": {"content": content}}]})

        with pytest.raises(TransientModelError):
            _client(_respond(200, text)).complete(BODY, timeout_s=1)

    def test_non_string_content_is_transient(self):
        text = json.dumps({"model": "m", "choices": [{"message": {"content": ["часть"]}}]})

        with pytest.raises(TransientModelError) as info:
            _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert info.value.error_class == "empty_response"

    def test_missing_model_is_transient(self):
        text = json.dumps({"model": None, "choices": [{"message": {"content": "ответ"}}]})

        with pytest.raises(TransientModelError) as info:
            _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert info.value.error_class == "bad_response"

    @pytest.mark.parametrize("cost", ["true", '"0.02"', "null"])
    def test_cost_that_is_not_a_json_number_is_unknown(self, cost):
        text = REAL_SHAPE.replace('"cost": 0.022137', f'"cost": {cost}')

        assert _client(_respond(200, text)).complete(BODY, timeout_s=1).cost_usd is None

    def test_token_counts_that_are_not_integers_read_as_zero(self):
        text = json.dumps(
            {
                "model": "m",
                "provider": 5,
                "choices": [{"message": {"content": "ответ"}}],
                "usage": {
                    "prompt_tokens": True,
                    "completion_tokens": "2",
                    "prompt_tokens_details": {"cache_write_tokens": "1", "cached_tokens": None},
                },
            }
        )

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert (
            response.prompt_tokens, response.completion_tokens,
            response.cache_write_tokens, response.cached_tokens, response.provider,
        ) == (0, 0, 0, 0, None)

    def test_token_count_written_as_a_whole_json_float_is_kept(self):
        text = REAL_SHAPE.replace('"cache_write_tokens": 8058', '"cache_write_tokens": 8058.0')

        assert _client(_respond(200, text)).complete(BODY, timeout_s=1).cache_write_tokens == 8058

    @pytest.mark.parametrize(
        "usage",
        ['"мусор"', '{"prompt_tokens": 3, "prompt_tokens_details": "мусор"}'],
        ids=["usage-not-object", "details-not-object"],
    )
    def test_malformed_usage_block_reads_as_zeros(self, usage):
        text = (
            '{"model": "m", "choices": [{"message": {"content": "ответ"}}], '
            f'"usage": {usage}}}'
        )

        response = _client(_respond(200, text)).complete(BODY, timeout_s=1)

        assert (response.cache_write_tokens, response.cached_tokens, response.cost_usd) == (
            0, 0, None,
        )

    def test_no_choices_is_transient(self):
        with pytest.raises(TransientModelError):
            _client(_respond(200, json.dumps({"model": "m", "choices": []}))).complete(
                BODY, timeout_s=1
            )

    def test_body_that_is_not_json_is_transient(self):
        with pytest.raises(TransientModelError):
            _client(_respond(200, "<html>gateway</html>")).complete(BODY, timeout_s=1)


class TestErrors:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_429_and_5xx_are_transient(self, status):
        with pytest.raises(TransientModelError) as info:
            _client(_respond(status, "busy")).complete(BODY, timeout_s=1)

        assert info.value.error_class == f"http_{status}"

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
    def test_other_4xx_are_permanent(self, status):
        with pytest.raises(PermanentModelError) as info:
            _client(_respond(status, "нельзя")).complete(BODY, timeout_s=1)

        assert info.value.error_class == f"http_{status}"

    def test_permanent_error_is_not_a_transient_one(self):
        assert not issubclass(PermanentModelError, TransientModelError)
        assert not issubclass(TransientModelError, PermanentModelError)

    def test_timeout_is_transient(self):
        def handler(request):
            raise httpx.ReadTimeout("медленно", request=request)

        with pytest.raises(TransientModelError) as info:
            _client(handler).complete(BODY, timeout_s=1)

        assert info.value.error_class == "timeout"

    def test_transport_error_is_transient(self):
        def handler(request):
            raise httpx.ConnectError("нет соединения", request=request)

        with pytest.raises(TransientModelError) as info:
            _client(handler).complete(BODY, timeout_s=1)

        assert info.value.error_class == "transport"

    def test_error_text_does_not_carry_the_request_body_or_the_key(self):
        with pytest.raises(PermanentModelError) as info:
            _client(_respond(401, "bad key")).complete(BODY, timeout_s=1)

        assert "sk-test" not in str(info.value) and "привет" not in str(info.value)


class TestClose:
    def test_close_leaves_an_injected_client_open(self):
        injected = httpx.Client(transport=httpx.MockTransport(_respond(200, REAL_SHAPE)))

        OpenRouterClient(api_key="k", http_client=injected).close()

        assert injected.is_closed is False
        injected.close()

    def test_close_closes_the_client_it_created_itself(self):
        client = OpenRouterClient(api_key="k")

        client.close()

        assert client._http.is_closed is True

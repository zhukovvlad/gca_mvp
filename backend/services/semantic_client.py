"""Клиент модели семантических предложений: протокол, ответ, классы ошибок и
реализация поверх OpenRouter (спека `2026-09-28-semantic-suggestions-design.md`
§2.5, §2.6).

Задача 10 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Исполнитель
(`services/semantic_worker.py`) знает только `ModelClient` — реальный клиент
внедряется при сборке приложения, а в тестах — подставной; сети в тестах нет.

Деньги: `usage.cost` приходит JSON-числом (`0.022137`), поэтому тело ответа
разбирается `json.loads(..., parse_float=Decimal)`, а не `response.json()` —
через `float` стоимость искажается ещё до записи в базу (`AGENTS.md` §3).
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

import httpx

#: Адрес и заголовки — как в замере пилота
#: (`tasks/catalog-pilot-2026-09-18/feature2-measure/cache_probe.py`).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_REFERER = "http://localhost"
_TITLE = "gca-semantic-suggestions"

#: Сколько символов тела ответа провайдера попадает в текст ошибки.
_ERROR_BODY_LIMIT = 500


class TransientModelError(Exception):
    """Временная ошибка вызова (`429`, `5xx`, таймаут, сеть, пустой ответ) —
    задание получает повтор (спека §2.6). `error_class` — короткая метка для
    `semantic_job_attempts.error_class`; без неё исполнитель пишет имя типа."""

    def __init__(self, message: str, *, error_class: str | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class


class BudgetAction(str, enum.Enum):
    """Что делать с отказом провайдера по деньгам: ждать или остановить очередь."""

    pause = "pause"
    stop = "stop"


class ProviderBudgetError(TransientModelError):
    """Отказ провайдера по деньгам (спека `2026-11-15-provider-budget-pause-design.md`
    §2.1). Наследник временной ошибки: место, которое о нём не знает, повторит
    задание с отсрочкой, но не испортит его в «Ошибки»."""

    def __init__(
        self,
        message: str,
        *,
        action: BudgetAction,
        error_class: str | None = None,
        resets_at: datetime | None = None,
        retry_after_s: int | None = None,
        stop_reason: str | None = None,
        limit_source: str | None = None,
        limit_usd: Decimal | None = None,
        spend_usd: Decimal | None = None,
    ) -> None:
        super().__init__(message, error_class=error_class)
        self.action = action
        self.resets_at = resets_at
        self.retry_after_s = retry_after_s
        self.stop_reason = stop_reason
        self.limit_source = limit_source
        self.limit_usd = limit_usd
        self.spend_usd = spend_usd


class PermanentModelError(Exception):
    """Постоянная ошибка вызова (`4xx`, кроме `429`) — без повтора (спека
    §2.6)."""

    def __init__(self, message: str, *, error_class: str | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class


@dataclass(frozen=True)
class ModelResponse:
    """Ответ провайдера: текст и то, что нужно для учёта (спека §2.4).
    `cost_usd is None` — провайдер стоимость не вернул."""

    content: str
    actual_model: str
    provider: str | None
    prompt_tokens: int
    completion_tokens: int
    cache_write_tokens: int
    cached_tokens: int
    cost_usd: Decimal | None


class ModelClient(Protocol):
    def complete(self, body: dict, *, timeout_s: float) -> ModelResponse: ...


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return 0
    return int(value)


def _as_cost(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    return Decimal(value)


def _parse_response(raw: bytes) -> ModelResponse:
    try:
        data = json.loads(raw, parse_float=Decimal)
        content = data["choices"][0]["message"]["content"]
        actual_model = data["model"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TransientModelError(
            "ответ провайдера не разобран", error_class="bad_response"
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise TransientModelError("провайдер вернул пустой ответ", error_class="empty_response")
    if not isinstance(actual_model, str):
        raise TransientModelError("в ответе провайдера нет модели", error_class="bad_response")

    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    provider = data.get("provider")
    return ModelResponse(
        content=content,
        actual_model=actual_model,
        provider=provider if isinstance(provider, str) else None,
        prompt_tokens=_as_int(usage.get("prompt_tokens")),
        completion_tokens=_as_int(usage.get("completion_tokens")),
        cache_write_tokens=_as_int(details.get("cache_write_tokens")),
        cached_tokens=_as_int(details.get("cached_tokens")),
        cost_usd=_as_cost(usage.get("cost")),
    )


#: Источники лимита провайдера (`error.metadata.limit_source`) → `error_class`.
_IN_FLIGHT_SOURCE = "openrouter_in_flight_budget"
_CREDITS_SOURCE = "openrouter_credits"
_RESET_SOURCES = {
    "openrouter_key_limit": "budget_key_limit",
    "openrouter_guardrail_budget": "budget_guardrail",
    "openrouter_workspace_budget": "budget_workspace",
}

_DEFAULT_RETRY_AFTER_S = 60
_MAX_RETRY_AFTER_S = 3600
_MISSING = object()


def _retry_after_s(value: str | None) -> int:
    """Целое число секунд 1..3600; иначе (нет, 0, дата, мусор) — 60."""
    if value is not None:
        text = value.strip()
        if text.isascii() and text.isdigit() and 1 <= int(text) <= _MAX_RETRY_AFTER_S:
            return int(text)
    return _DEFAULT_RETRY_AFTER_S


def _parse_resets_at(value: object) -> datetime | None:
    """Строка ISO 8601 с часовым поясом → время в UTC; иначе `None`."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _budget_refusal(response: httpx.Response) -> ProviderBudgetError | None:
    """Разбор `402`/`403` по таблице спеки §2.1. `None` — отказ не по деньгам
    (для `403`: прежняя постоянная ошибка `http_403`)."""
    status = response.status_code
    metadata: dict = {}
    try:
        data = json.loads(response.content, parse_float=Decimal)
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    raw_meta = error.get("metadata") if isinstance(error, dict) else None
    if isinstance(raw_meta, dict):
        metadata = raw_meta
    source = metadata.get("limit_source")
    source = source if isinstance(source, str) else None
    message = f"HTTP {status}: {response.text[:_ERROR_BODY_LIMIT]}"
    common = {
        "limit_source": source,
        "limit_usd": _as_cost(metadata.get("limit_usd")),
        "spend_usd": _as_cost(metadata.get("spend_usd")),
    }

    if status == 402:
        if source == _IN_FLIGHT_SOURCE:
            return ProviderBudgetError(
                message,
                action=BudgetAction.pause,
                error_class="budget_in_flight",
                retry_after_s=_retry_after_s(response.headers.get("Retry-After")),
                **common,
            )
        if source == _CREDITS_SOURCE:
            return ProviderBudgetError(
                message,
                action=BudgetAction.stop,
                error_class="budget_credits",
                stop_reason="provider_credits",
                **common,
            )
        return ProviderBudgetError(
            message,
            action=BudgetAction.stop,
            error_class="budget_unknown",
            stop_reason="provider_refusal_unknown",
            **common,
        )

    error_class = _RESET_SOURCES.get(source) if source is not None else None
    if error_class is None:
        return None
    raw_resets = metadata.get("resets_at", _MISSING)
    if raw_resets is None:
        return ProviderBudgetError(
            message,
            action=BudgetAction.stop,
            error_class=error_class,
            stop_reason="provider_budget_no_reset",
            **common,
        )
    resets_at = None if raw_resets is _MISSING else _parse_resets_at(raw_resets)
    if resets_at is None:
        return ProviderBudgetError(
            message,
            action=BudgetAction.stop,
            error_class=error_class,
            stop_reason="provider_refusal_unknown",
            **common,
        )
    return ProviderBudgetError(
        message,
        action=BudgetAction.pause,
        error_class=error_class,
        resets_at=resets_at,
        **common,
    )


class OpenRouterClient:
    """`ModelClient` поверх `httpx.Client`. `http_client` внедряется в тестах
    (`httpx.MockTransport`); без него клиент создаёт свой."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = OPENROUTER_BASE_URL,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.Client()
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": _REFERER,
            "X-Title": _TITLE,
        }

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def complete(self, body: dict, *, timeout_s: float) -> ModelResponse:
        try:
            response = self._http.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers=self._headers,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise TransientModelError("таймаут вызова провайдера", error_class="timeout") from exc
        except httpx.TransportError as exc:
            raise TransientModelError(
                f"сетевая ошибка вызова провайдера: {type(exc).__name__}", error_class="transport"
            ) from exc

        status = response.status_code
        if status == 429 or status >= 500:
            raise TransientModelError(
                f"HTTP {status}: {response.text[:_ERROR_BODY_LIMIT]}", error_class=f"http_{status}"
            )
        if status in (402, 403):
            refusal = _budget_refusal(response)
            if refusal is not None:
                raise refusal
        if status >= 400:
            raise PermanentModelError(
                f"HTTP {status}: {response.text[:_ERROR_BODY_LIMIT]}", error_class=f"http_{status}"
            )
        return _parse_response(response.content)

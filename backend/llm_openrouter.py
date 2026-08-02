"""OpenRouterProvider: транспорт/envelope OpenRouter, вынесенный из pdf_parser (1:1).

Payload защищён контрактными тестами (AC-1). Доменного парсинга здесь нет.
"""
import base64
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from config import Settings, resolved_openrouter_base_url, resolved_openrouter_model, resolved_openrouter_pdf_engine
from llm import Attachment, LLMProviderError, LLMResponse, PdfAttachment

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class OpenRouterProvider:
    """Неизменяемый экземпляр (инвариант §2.3); ссылка меняется только локатором."""

    api_key: str
    completions_url: str
    model: str
    pdf_engine: str

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenRouterProvider":
        """Собрать из Settings; нормализация base URL — в config (guard §1)."""
        base = resolved_openrouter_base_url(s).rstrip("/")
        return cls(api_key=s.OPENROUTER_API_KEY,
                   completions_url=f"{base}/chat/completions",
                   model=resolved_openrouter_model(s),
                   pdf_engine=resolved_openrouter_pdf_engine(s))

    def _payload(self, *, system: str | None, user_text: str,
                 attachment: Attachment, max_tokens: int) -> dict:
        """Собрать OpenRouter-payload; порядок частей — контракт (AC-1)."""
        if isinstance(attachment, PdfAttachment):
            pdf_b64 = base64.b64encode(attachment.data).decode("utf-8")
            content: list[dict] = [
                {"type": "file", "file": {"filename": attachment.filename,
                 "file_data": f"data:application/pdf;base64,{pdf_b64}"}},
                {"type": "text", "text": user_text},
            ]
            plugins = [{"id": "file-parser", "pdf": {"engine": self.pdf_engine}}]
        else:
            content = [{"type": "text", "text": user_text}]
            for img in attachment.images:
                b64 = base64.b64encode(img).decode()
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            plugins = None
        messages: list[dict] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload = {"model": self.model, "max_tokens": max_tokens,
                   "usage": {"include": True}, "messages": messages}
        if plugins is not None:
            payload["plugins"] = plugins
        return payload

    async def vision_completion(self, *, system: str | None, user_text: str,
                                attachment: Attachment, max_tokens: int,
                                timeout: httpx.Timeout) -> LLMResponse:
        """Отправить запрос и разобрать envelope; ошибки — LLMProviderError с биллингом."""
        if not self.api_key:
            raise LLMProviderError("API-ключ OpenRouter не настроен", retryable=False)
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        payload = self._payload(system=system, user_text=user_text,
                                attachment=attachment, max_tokens=max_tokens)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.completions_url,
                                             headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            # текст 1:1 для parse-пути; detect перекрывает своим доменным текстом,
            # но в его логе warning может мелькнуть «180с» при 30с-бюджете —
            # осознанная косметика ради стабильной parse-строки
            raise LLMProviderError("Таймаут запроса к OpenRouter (180с)",
                                   retryable=True) from exc
        except httpx.RequestError as exc:
            # транспортный сбой без ответа сервера → платного вызова не было (F12)
            logger.warning(f"OpenRouter: сетевая ошибка запроса: {exc!r}")
            raise LLMProviderError("Сетевая ошибка запроса к сервису распознавания",
                                   retryable=True) from exc

        if response.status_code != 200:
            retryable = response.status_code >= 500 or response.status_code in (408, 429)
            raise LLMProviderError(f"OpenRouter API ошибка: {response.status_code}",
                                   retryable=retryable)

        # HTTP 200 ⇒ платный вызов состоялся: ВСЁ ниже несёт paid_calls=1 (§2.3).
        # Тексты ошибок — 1:1 с текущими: не-JSON/кривая форма → «Не удалось
        # разобрать ответ модели»; нет choices/message/content → «Ответ модели
        # без содержимого» (global constraint плана).
        try:
            data = response.json()
        except ValueError as exc:  # тело не-JSON
            raise LLMProviderError("Не удалось разобрать ответ модели", retryable=False,
                                   cost_usd=Decimal(0), paid_calls=1) from exc
        cost = Decimal(0)
        try:
            raw_cost = Decimal(str((data.get("usage") or {}).get("cost") or 0))
            if raw_cost.is_finite() and raw_cost >= 0:
                cost = raw_cost
            else:
                logger.warning(f"OpenRouter: usage.cost вне допустимых значений "
                               f"({raw_cost!r}) — клэмп в 0")
            usage = data.get("usage") or {}
            completion_tokens = usage.get("completion_tokens")
            finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
        except Exception as exc:  # noqa: BLE001 — кривая форма usage/choices (top-level array и т.п.)
            raise LLMProviderError("Не удалось разобрать ответ модели", retryable=False,
                                   cost_usd=cost, paid_calls=1) from exc
        try:
            content_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Ответ модели без содержимого", retryable=False,
                                   cost_usd=cost, paid_calls=1) from exc
        return LLMResponse(content=content_text, finish_reason=finish_reason,
                           cost_usd=cost, completion_tokens=completion_tokens,
                           paid_calls=1)

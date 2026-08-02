"""Вспомогательные утилиты для backend."""
from datetime import UTC, datetime

from fastapi import Request

from config import settings


def utcnow() -> datetime:
    """Текущее время в UTC как naive datetime.

    Все DateTime-колонки в БД объявлены без timezone=True, поэтому
    сохраняем naive UTC повсеместно. Функция — единое место для этого соглашения.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def get_client_ip(request: Request) -> str | None:
    """Возвращает реальный IP клиента с учётом reverse-proxy.

    Использует X-Forwarded-For только при TRUSTED_PROXIES > 0 (установить в .env при
    работе за Nginx/Caddy). Без этой настройки X-Forwarded-For игнорируется целиком,
    чтобы не доверять клиентскому заголовку.

    При TRUSTED_PROXIES=N берётся запись непосредственно перед N доверенными прокси справа в XFF.
    Может вернуть None, если приложение запущено без ASGI-сокета (unit-тесты).
    """
    if settings.TRUSTED_PROXIES > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",")]
            # С N доверенными прокси реальный IP — элемент перед ними в XFF
            index = max(0, len(parts) - settings.TRUSTED_PROXIES - 1)
            return parts[index]
    return request.client.host if request.client else None

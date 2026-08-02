"""Тест-сторож: все ручки (кроме явно публичных) должны возвращать 401 или 403 без токена.

Этот тест навсегда фиксирует инвариант: новая ручка не может случайно оказаться публичной.
Код 403 тоже допустим — CSRF middleware может сработать раньше проверки auth.
Если тест упадёт — значит добавили новый роутер без auth dependency.
"""
import re

import pytest
from fastapi.testclient import TestClient

from main import app

# Ручки, открытые намеренно — без аутентификации
PUBLIC_PATHS: set[str] = {
    "/api/auth/login",    # единственный вход до получения куки
    "/api/health",        # health-check для мониторинга
    "/docs",              # Swagger UI
    "/openapi.json",      # OpenAPI схема
    "/redoc",             # ReDoc
    "/docs/oauth2-redirect",
}


@pytest.fixture(scope="module")
def unauth_client() -> TestClient:
    """TestClient без аутентификации, один на весь модуль (lifespan запускается один раз)."""
    return TestClient(app, raise_server_exceptions=False)


def _collect_routes() -> list[tuple[str, str]]:
    """Рекурсивно собрать все ручки из дерева роутов как (method, full_path).

    FastAPI 0.139 включённые роутеры (`include_router`) больше НЕ разворачивает в
    плоский `app.routes` — каждый становится непрозрачным `_IncludedRouter`, а его
    ручки лежат в `original_router.routes` с префиксом из `include_context`. Плоский
    обход (`for route in app.routes`) поэтому видел бы лишь ~5 верхнеуровневых ручек.
    Спускаемся внутрь: `_IncludedRouter` → его `original_router.routes` (+ префикс),
    `Mount`/под-приложение → его `.routes`, лист с `.methods` → (method, prefix+path).
    Обратно совместимо со старым плоским представлением (там нет `original_router`).
    """
    def walk(routes, prefix: str = "") -> list[tuple[str, str]]:
        """Обойти список роутов, разворачивая вложенные роутеры с накоплением префикса."""
        collected: list[tuple[str, str]] = []
        for route in routes:
            included = getattr(route, "original_router", None)  # FastAPI ≥0.139 _IncludedRouter
            if included is not None:
                ctx = getattr(route, "include_context", None)
                sub_prefix = getattr(ctx, "prefix", "") or ""
                collected.extend(walk(included.routes, prefix + sub_prefix))
            elif hasattr(route, "methods") and getattr(route, "path", None) is not None:
                for method in route.methods:
                    if method in ("HEAD", "OPTIONS"):
                        continue
                    collected.append((method, prefix + route.path))
            elif hasattr(route, "routes"):  # Mount / под-приложение
                collected.extend(walk(route.routes, prefix + getattr(route, "path", "")))
        return collected

    return walk(app.routes)


def test_route_enumeration_not_silently_broken() -> None:
    """Сторож самого сборщика: если интроспекция роутов сломается (напр. смена
    внутренностей FastAPI), число ручек рухнет — падаем ЯВНО, а не теряем покрытие тихо."""
    assert len(_collect_routes()) >= 60, (
        "Сборщик ручек вернул подозрительно мало роутов — вероятно, сломалась "
        "интроспекция app.routes после обновления FastAPI (см. _collect_routes)."
    )


@pytest.mark.parametrize("method,path", _collect_routes())
def test_endpoint_requires_auth(method: str, path: str, unauth_client: TestClient) -> None:
    """Убедиться, что ручка возвращает 401 или 403 при отсутствии токена.

    Для публичных ручек тест пропускается.
    """
    if path in PUBLIC_PATHS:
        pytest.skip("Публичный endpoint — auth не требуется")

    # Подставляем валидные числовые id во все path-параметры в формате {anything}
    test_path = re.sub(r"\{[^}]+\}", "1", path)

    response = unauth_client.request(method, test_path)
    assert response.status_code in (401, 403), (
        f"{method} {path} вернул {response.status_code} без аутентификации — "
        "добавь auth dependency к роутеру."
    )

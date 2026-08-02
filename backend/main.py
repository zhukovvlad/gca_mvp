import json
import logging
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, get_current_user
from config import settings
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

import llm
import s3
from routers import admin as admin_router
from routers import auth as auth_router
from routers import dashboard, export, invoices, material_classes, projects, reference_prices, suppliers, units
from routers import orgs as orgs_router
from routers import settings as settings_router


def _sweep_stuck_documents(session_factory=None) -> int:
    """Startup-sweep (S1-4): все pending|processing → error одним UPDATE.

    На старте процесса легитимных нетерминальных документов не существует
    (однопроцессная модель, no-overlap deployment): pending — crash в окне
    create_document→guard, processing — crash посреди фоновой таски. Оба
    нетерминальны для polling'а — зомби заставил бы фронт поллиться вечно.
    session_factory=None → поздний резолв SessionLocal (паттерн F1 S0);
    в тестах инжектится тест-фабрика.
    """
    from sqlalchemy import text

    if session_factory is None:
        # Guard только на этой ветке — она и есть «приложение поднимается по-
        # настоящему». Sweep пишет в БД безусловно на каждый старт, поэтому
        # `uv run uvicorn main:app`, набранный вместо `just dev-backend`,
        # перевёл бы живые прод-документы в error. Инжектированная фабрика
        # (тесты) сюда не попадает — там цель заведомо тестовая.
        from config import settings
        from database import SessionLocal
        from db_guard import ensure_mutation_allowed

        ensure_mutation_allowed(settings.DATABASE_URL, "startup-sweep")
        session_factory = SessionLocal
    with session_factory() as db:
        result = db.execute(text(
            "UPDATE documents SET status='error', "
            "last_error='Обработка прервана перезапуском сервера' "
            "WHERE status IN ('pending', 'processing')"
        ))
        db.commit()
    if result.rowcount:
        logger.warning(f"Startup-sweep: {result.rowcount} документ(ов) переведено в error")
    return result.rowcount


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте приложения (не при импорте модуля)."""
    # LLM-провайдер: fail-fast §1 ДО любых стартовых мутаций (sweep пишет в БД).
    # Позднее связывание модуля llm — тестовые monkeypatch-и должны действовать
    # (паттерн как у s3 ниже).
    llm.init_provider(settings)
    logger.info(f"LLM-провайдер инициализирован: {settings.LLM_PROVIDER}")
    try:
        try:
            # Позднее связывание (s3.ensure_bucket, а не from-import): тестовые
            # monkeypatch-и модуля s3 должны действовать и на lifespan.
            s3.ensure_bucket()
            logger.info("MinIO bucket готов")
        except Exception as e:
            logger.warning(f"MinIO недоступен при старте: {e}")

        # Fail-fast (ревью плана, P1): sweep обязан выполниться до приёма трафика.
        # Проглотить ошибку нельзя — если БД оживёт позже, зомби-processing останутся
        # навсегда и polling не завершится. БД недоступна → приложение не стартует
        # (оно всё равно неработоспособно), рестарт повторит sweep.
        swept = _sweep_stuck_documents()
        logger.info(f"Startup-sweep выполнен: {swept} документ(ов)")

        yield
    finally:
        llm.reset_provider()


def _decimal_encoder(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class DecimalJSONResponse(JSONResponse):
    """Стандартный JSONResponse с поддержкой Decimal → float для dict-ответов."""
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), default=_decimal_encoder,
        ).encode("utf-8")


app = FastAPI(
    title="УПД Трекер цен",
    version="2.0.0",
    lifespan=lifespan,
    default_response_class=DecimalJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # НЕ wildcard — нужен конкретный origin для credentials
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-CSRF-Token"],
)


# ---------------------------------------------------------------------------
#  CSRF middleware — double-submit cookie protection
# ---------------------------------------------------------------------------

# Пути, которые НЕ требуют CSRF-токена:
# - /api/auth/login — единственный POST до выдачи куки (куки ещё нет)
# - /docs, /openapi.json, /redoc — Swagger UI
_CSRF_EXEMPT = {"/api/auth/login", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Проверяет CSRF double-submit cookie для state-changing запросов.

    /api/auth/refresh и /api/auth/logout НЕ исключены — к моменту их вызова
    CSRF-кука уже установлена. На них дополнительно навешан Depends(require_csrf)
    как defense-in-depth (middleware + dependency).
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return await call_next(request)
    if request.url.path in _CSRF_EXEMPT:
        return await call_next(request)
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if not cookie or not header or cookie != header:
        return JSONResponse({"detail": "CSRF token mismatch"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
        elapsed = (time.time() - start) * 1000
        if response.status_code >= 400:
            logger.warning(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.0f}ms)")
        else:
            logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.0f}ms)")
        return response
    except Exception:
        elapsed = (time.time() - start) * 1000
        logger.exception(f"{request.method} {request.url.path} → 500 ({elapsed:.0f}ms)")
        raise

from fastapi import Depends

# Auth router — без обязательной авторизации (login, me, refresh, logout)
app.include_router(auth_router.router)

# Список зависимостей для бизнес-роутеров
_auth_dep = [Depends(get_current_user)]

# Admin и org-management — авторизация + проверка роли на каждом endpoint внутри роутера
app.include_router(admin_router.router)
app.include_router(orgs_router.router)

# Бизнес-роутеры — требуют валидного access-токена
app.include_router(projects.router, prefix="/api/projects", tags=["projects"], dependencies=_auth_dep)
app.include_router(material_classes.router, prefix="/api/material-classes", tags=["material-classes"], dependencies=_auth_dep)
app.include_router(reference_prices.router, prefix="/api/reference-prices", tags=["reference-prices"], dependencies=_auth_dep)
app.include_router(invoices.router, prefix="/api/invoices", tags=["invoices"], dependencies=_auth_dep)
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"], dependencies=_auth_dep)
app.include_router(export.router, prefix="/api/export", tags=["export"], dependencies=_auth_dep)
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"], dependencies=_auth_dep)
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"], dependencies=_auth_dep)
app.include_router(units.router, prefix="/api/units", tags=["units"], dependencies=_auth_dep)
app.include_router(units.material_types_router, prefix="/api/material-types", tags=["material-types"], dependencies=_auth_dep)


@app.get("/api/health")
def health():
    return {"status": "ok"}

import json
import logging
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, get_current_user
from config import settings
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

from database import SessionLocal
from routers import admin as admin_router
from routers import auth as auth_router
from routers import estimates as estimates_router
from routers import import_jobs as import_jobs_router
from routers import units
from services.maintenance import run_startup_maintenance
from storage import get_storage


def _decimal_encoder(obj: Any) -> Any:
    # Деньги в JSON — строки, не float (AGENTS.md §3)
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class DecimalJSONResponse(JSONResponse):
    """Стандартный JSONResponse с поддержкой Decimal → str для dict-ответов."""
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), default=_decimal_encoder,
        ).encode("utf-8")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Обслуживание при старте: recovery зависших джобов + ретенция файлов.

    ИНВАРИАНТ (AGENTS.md §3, §5): корректно ровно при ОДНОМ worker-процессе
    uvicorn. Второй worker при подъёме перевёл бы в `error` задания, которые
    первый в этот момент выполняет.

    Отключается настройкой `RUN_STARTUP_MAINTENANCE=false` — так тесты не дают
    `TestClient` мутировать БД приложения: lifespan работает на реальном engine,
    мимо транзакционной фикстуры.
    """
    if settings.RUN_STARTUP_MAINTENANCE:
        try:
            recovered, purged = run_startup_maintenance(
                SessionLocal,
                get_storage(),
                retention_days=settings.ERROR_JOB_FILE_RETENTION_DAYS,
            )
            logger.info(
                "Обслуживание при старте: заданий восстановлено %d, файлов удалено %d",
                recovered,
                purged,
            )
        except Exception:
            # Приложение обязано подняться даже при недоступной БД: иначе
            # починить конфигурацию через тот же процесс станет невозможно.
            logger.exception("Обслуживание при старте не выполнено")
    yield


app = FastAPI(
    title="База расценок генподряда",
    version="0.1.0",
    default_response_class=DecimalJSONResponse,
    lifespan=lifespan,
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


# Auth router — без обязательной авторизации (login, me, refresh, logout)
app.include_router(auth_router.router)

# Список зависимостей для бизнес-роутеров
_auth_dep = [Depends(get_current_user)]

# Admin — авторизация + проверка роли на каждом endpoint внутри роутера
app.include_router(admin_router.router)

# Бизнес-роутеры — требуют валидного access-токена
app.include_router(units.router, prefix="/api/units", tags=["units"], dependencies=_auth_dep)

# Импорт смет (AGENTS.md §5). Префиксы /api/v1/... объявлены §5 дословно, поэтому
# у этих двух роутеров он есть, а у более раннего /api/units — нет.
app.include_router(estimates_router.router, dependencies=_auth_dep)
app.include_router(import_jobs_router.router, dependencies=_auth_dep)


@app.get("/api/health")
def health():
    return {"status": "ok"}

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, get_current_user
from config import settings
from logging_config import setup_logging
from responses import DecimalJSONResponse

setup_logging()
logger = logging.getLogger(__name__)

from database import SessionLocal
from routers import admin as admin_router
from routers import auth as auth_router
from routers import catalog as catalog_router
from routers import contracts as contracts_router
from routers import estimates as estimates_router
from routers import import_jobs as import_jobs_router
from routers import rate_standards as rate_standards_router
from routers import references as references_router
from routers import review as review_router
from routers import settings as settings_router
from routers import units
from services.maintenance import run_startup_maintenance
from storage import get_storage


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Обслуживание при старте: recovery зависших джобов + ретенция файлов.

    ИНВАРИАНТ (AGENTS.md §3, §5): корректно ровно при ОДНОМ worker-процессе
    uvicorn. Второй worker при подъёме перевёл бы в `error` задания, которые
    первый в этот момент выполняет.

    **Исключение НЕ подавляется.** Recovery — обязательное условие работы, а не
    удобство: без него незавершённые задания продолжают держать лок пары
    `uq_import_jobs_active_pair`, и повторная загрузка вечно отвечает 409. Если
    recovery не выполнился, приложение не должно подняться. Ошибку ретенции
    гасит сам `run_startup_maintenance` — она на работоспособность не влияет.

    Отключается настройкой `RUN_STARTUP_MAINTENANCE=false` — так тесты не дают
    `TestClient` мутировать БД приложения: lifespan работает на реальном engine,
    мимо транзакционной фикстуры. В проде не выключать.
    """
    if settings.RUN_STARTUP_MAINTENANCE:
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

# CRUD фазы 5 (§7, §9.5). Тот же префикс /api/v1: чтение — любому
# аутентифицированному, изменение — под require_admin внутри роутера (§6.2).
app.include_router(references_router.router, dependencies=_auth_dep)
app.include_router(contracts_router.router, dependencies=_auth_dep)
app.include_router(catalog_router.router, dependencies=_auth_dep)
# Нормативы — изменение под admin по букве §3, не по решению фазы 5.
app.include_router(rate_standards_router.router, dependencies=_auth_dep)
# Ручной матчинг — право `member` тоже (§3), поэтому только аутентификация.
app.include_router(review_router.router, dependencies=_auth_dep)

# Аналитика фазы 6 (§7.4–§7.6, §9.6). Настройки: чтение всем — `passport_top_n`
# нужен паспорту, а паспорт доступен и `member`; изменение — admin внутри роутера.
app.include_router(settings_router.router, dependencies=_auth_dep)


@app.get("/api/health")
def health():
    return {"status": "ok"}

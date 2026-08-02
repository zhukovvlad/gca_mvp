"""Роутер аутентификации: login, refresh, logout, me.

Транспорт токенов — httpOnly cookie (access_token, refresh_token).
CSRF защита — double-submit cookie pattern (csrf_token).
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth import ACCESS_COOKIE_NAME, CSRF_COOKIE_NAME, REFRESH_COOKIE_NAME, get_current_user, require_csrf
from config import settings
from database import get_db
from models import RefreshToken, User
from security import (
    create_access_token,
    generate_csrf_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from utils import get_client_ip, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
#  Вспомогательные функции куки
# ---------------------------------------------------------------------------

def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    """Установить три куки: access, refresh и CSRF.

    access_token — httpOnly, Path=/
    refresh_token — httpOnly, Path=/api/auth (ограничен только auth-эндпоинтами)
    csrf_token — НЕ httpOnly (фронт должен прочитать и вложить в заголовок)
    """
    common = {"httponly": True, "secure": settings.COOKIE_SECURE, "samesite": "lax", "domain": settings.COOKIE_DOMAIN}
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
        **common,
    )
    # CSRF — НЕ httpOnly: фронт читает и шлёт в заголовке X-CSRF-Token
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        domain=settings.COOKIE_DOMAIN,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Удалить все auth-куки (на logout)."""
    for name, path in [
        (ACCESS_COOKIE_NAME, "/"),
        (REFRESH_COOKIE_NAME, "/api/auth"),
        (CSRF_COOKIE_NAME, "/"),
    ]:
        response.delete_cookie(name, path=path, domain=settings.COOKIE_DOMAIN)


# ---------------------------------------------------------------------------
#  Схемы запросов
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------------------------------------------------------------------------
#  Эндпоинты
# ---------------------------------------------------------------------------

@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Аутентификация по email + password. Устанавливает три куки.

    Намеренно не раскрываем, что именно неверно (email или пароль) —
    возвращаем общее 401 «Invalid credentials».
    """
    client_ip = get_client_ip(request)
    user = db.query(User).filter(User.email == body.email, User.is_active.is_(True)).first()
    if not user or not verify_password(body.password, user.password_hash):
        logger.warning("login_failed email=%s ip=%s", body.email, client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    access = create_access_token({
        "sub": str(user.id),
        "org_id": user.org_id,
        "is_superuser": user.is_superuser,
        "org_role": user.org_role.value if user.org_role else None,
    })
    refresh_raw, refresh_hashed = generate_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=refresh_hashed,
        expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
    ))
    db.commit()

    csrf = generate_csrf_token()
    _set_auth_cookies(response, access, refresh_raw, csrf)
    logger.info("login_success user_id=%s ip=%s", user.id, client_ip)
    return {"status": "ok"}


@router.post("/refresh", dependencies=[Depends(require_csrf)])
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Обновить access-токен + ротация refresh-токена.

    При каждом refresh: старый токен помечается revoked_at, выдаётся новый.
    Если refresh_token отсутствует, отозван или просрочен — 401.
    """
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    hashed = hash_refresh_token(raw)
    rt = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token_hash == hashed,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > utcnow(),
        )
        .with_for_update()  # блокировка строки — предотвращает двойную выдачу при параллельных /refresh
        .first()
    )
    if not rt:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    user = rt.user
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is inactive")
    # Ротация: отзываем старый токен, создаём новый
    rt.revoked_at = utcnow()
    new_raw, new_hashed = generate_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=new_hashed,
        expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=request.headers.get("user-agent"),
        ip_address=get_client_ip(request),
    ))
    db.commit()

    access = create_access_token({
        "sub": str(user.id),
        "org_id": user.org_id,
        "is_superuser": user.is_superuser,
        "org_role": user.org_role.value if user.org_role else None,
    })
    csrf = generate_csrf_token()
    _set_auth_cookies(response, access, new_raw, csrf)
    return {"status": "ok"}


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Выход: отозвать refresh-токен в БД и удалить все auth-куки."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw:
        hashed = hash_refresh_token(raw)
        db.query(RefreshToken).filter(
            RefreshToken.token_hash == hashed,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": utcnow()})
        db.commit()
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    """Вернуть профиль текущего пользователя.

    Возвращает базовые данные пользователя + информацию об организации.
    """
    return {
        "id": current_user.id,
        "email": current_user.email,
        "org_id": current_user.org_id,
        "org_role": current_user.org_role.value if current_user.org_role else None,
        "is_superuser": current_user.is_superuser,
        "organization": {
            "id": current_user.organization.id,
            "name": current_user.organization.name,
            "inn": current_user.organization.inn,
            "kind": current_user.organization.kind.value,
        } if current_user.organization else None,
    }

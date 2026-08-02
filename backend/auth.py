"""FastAPI dependencies для аутентификации и авторизации.

Содержит:
- get_current_user — проверка access-токена из куки
- require_csrf — проверка CSRF double-submit
- require_admin — проверка роли admin (single-tenant, AGENTS.md §3)
"""
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole
from security import decode_access_token

# Имена куки и заголовка — строгие константы, совпадают с frontend
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Извлечь и валидировать access-токен из httpOnly куки.

    Raises:
        401 если куки нет, токен просрочен или пользователь неактивен.
    """
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from e

    sub = payload.get("sub")
    try:
        user_id = int(sub)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from e

    user = (
        db.query(User)
        .filter(User.id == user_id, User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_csrf(request: Request) -> None:
    """Проверить CSRF double-submit cookie для state-changing методов.

    GET/HEAD/OPTIONS пропускаются. Для остальных методов токен в куки должен
    совпасть с токеном в заголовке X-CSRF-Token.

    Raises:
        403 CSRF token mismatch если куки != заголовок.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token mismatch")


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Разрешить доступ только пользователям с ролью admin.

    Raises:
        403 если роль — member.
    """
    if current_user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")
    return current_user

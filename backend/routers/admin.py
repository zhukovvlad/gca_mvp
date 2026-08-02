"""Роутер админ-операций: управление пользователями (single-tenant).

Все эндпоинты требуют роли admin. Бизнес-логика — в crud/admin.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth import require_admin
from crud import admin as crud_admin
from crud.admin import AdminError
from database import get_db
from models import User, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
#  Схемы
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: UserRole = UserRole.member
    is_active: bool = True


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


def _raise(err: AdminError) -> None:
    raise HTTPException(err.status_code, err.detail)


def _user_response(user: User) -> dict:
    """Единый формат пользователя для admin-эндпоинтов."""
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
    }


# ---------------------------------------------------------------------------
#  Пользователи
# ---------------------------------------------------------------------------

@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Создать пользователя (только admin)."""
    try:
        user = crud_admin.create_user(
            db,
            email=body.email,
            password=body.password,
            role=body.role,
            is_active=body.is_active,
        )
    except AdminError as e:
        _raise(e)
    return _user_response(user)


@router.get("/users")
def list_users(
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Список всех пользователей с пагинацией (только admin).

    Возвращает {items, total, page, page_size}.
    """
    return crud_admin.list_users_paginated(db, q=q, page=page, page_size=page_size)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Сменить role и/или is_active. Защищает последнего активного admin."""
    fields = body.model_dump(exclude_unset=True)
    if "role" in fields and fields["role"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле role не может быть null")
    if "is_active" in fields and fields["is_active"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле is_active не может быть null")
    try:
        user = crud_admin.set_user_role_and_active(
            db,
            user_id,
            role=fields.get("role", crud_admin.UNSET),
            is_active=fields.get("is_active", crud_admin.UNSET),
        )
    except AdminError as e:
        _raise(e)
    return _user_response(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Сгенерировать новый пароль на сервере. Возвращает пароль в открытом виде один раз."""
    try:
        user, new_password = crud_admin.reset_user_password(db, user_id)
    except AdminError as e:
        _raise(e)
    return {"id": user.id, "email": user.email, "password": new_password}

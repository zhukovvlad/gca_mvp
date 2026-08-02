"""Роутер суперюзерных операций: организации и пользователи.

Все эндпоинты требуют is_superuser=True (платформенный суперюзер).
Бизнес-логика — в crud/admin.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth import require_superuser
from crud import admin as crud_admin
from crud.admin import AdminError
from database import get_db
from models import OrgRole, ProjectRole, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
#  Схемы
# ---------------------------------------------------------------------------

class OrgCreate(BaseModel):
    name: str
    inn: str | None = None
    kind: ProjectRole = ProjectRole.customer


class OrgUpdate(BaseModel):
    name: str | None = None
    inn: str | None = None
    kind: ProjectRole | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    org_role: OrgRole = OrgRole.member
    is_active: bool = True


class UserUpdate(BaseModel):
    org_role: OrgRole | None = None
    is_active: bool | None = None


class ProjectLinkCreate(BaseModel):
    project_id: int
    project_role: ProjectRole | None = None


def _raise(err: AdminError) -> None:
    raise HTTPException(err.status_code, err.detail)


def _user_response(user: User) -> dict:
    """Единый формат пользователя для admin-эндпоинтов (совместим с GET /api/admin/users).

    Включает is_superuser — фронт (тип AdminUser) на него рассчитывает.
    """
    return {
        "id": user.id,
        "email": user.email,
        "org_id": user.org_id,
        "org_role": user.org_role.value if user.org_role else None,
        "is_superuser": user.is_superuser,
        "is_active": user.is_active,
    }


# ---------------------------------------------------------------------------
#  Организации
# ---------------------------------------------------------------------------

@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    body: OrgCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Создать организацию (только суперюзер)."""
    org = crud_admin.create_organization(db, name=body.name, inn=body.inn, kind=body.kind)
    return {"id": org.id, "name": org.name, "inn": org.inn, "kind": org.kind.value}


@router.get("/organizations")
def list_organizations(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Список всех организаций с агрегатами (только суперюзер)."""
    return crud_admin.list_organizations(db)


@router.get("/organizations/{org_id}")
def get_organization(
    org_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Детальная карточка организации: поля, пользователи, доступ к проектам."""
    detail = crud_admin.get_organization_detail(db, org_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    return detail


@router.patch("/organizations/{org_id}")
def update_organization(
    org_id: int,
    body: OrgUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Изменить name / inn / kind организации."""
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле name не может быть null")
    if "kind" in fields and fields["kind"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле kind не может быть null")
    try:
        org = crud_admin.update_organization(
            db,
            org_id,
            name=fields.get("name", crud_admin.UNSET),
            inn=fields.get("inn", crud_admin.UNSET),
            kind=fields.get("kind", crud_admin.UNSET),
        )
    except AdminError as e:
        _raise(e)
    return {"id": org.id, "name": org.name, "inn": org.inn, "kind": org.kind.value}


# ---------------------------------------------------------------------------
#  Пользователи
# ---------------------------------------------------------------------------

@router.post("/organizations/{org_id}/users", status_code=status.HTTP_201_CREATED)
def create_user_in_org(
    org_id: int,
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Создать пользователя в организации (только суперюзер).

    Первый пользователь в организации автоматически получает роль superadmin.
    """
    try:
        user = crud_admin.create_user_in_org(
            db,
            org_id=org_id,
            email=body.email,
            password=body.password,
            org_role=body.org_role,
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
    _: User = Depends(require_superuser),
):
    """Список всех пользователей с названием организации и пагинацией (только суперюзер).

    Возвращает {items, total, page, page_size}. Поля элемента совместимы с прежним
    ответом (id, email, org_id, org_role, is_superuser, is_active) + org_name.
    """
    return crud_admin.list_users_paginated(db, q=q, page=page, page_size=page_size)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Сменить org_role и/или is_active. Защищает последнего superadmin'а организации."""
    fields = body.model_dump(exclude_unset=True)
    if "org_role" in fields and fields["org_role"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле org_role не может быть null")
    if "is_active" in fields and fields["is_active"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле is_active не может быть null")
    try:
        user = crud_admin.set_user_role_and_active(
            db,
            user_id,
            org_role=fields.get("org_role", crud_admin.UNSET),
            is_active=fields.get("is_active", crud_admin.UNSET),
        )
    except AdminError as e:
        _raise(e)
    return _user_response(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Сгенерировать новый пароль на сервере. Возвращает пароль в открытом виде один раз."""
    try:
        user, new_password = crud_admin.reset_user_password(db, user_id)
    except AdminError as e:
        _raise(e)
    return {"id": user.id, "email": user.email, "password": new_password}


# ---------------------------------------------------------------------------
#  Доступ к проектам
# ---------------------------------------------------------------------------

@router.post("/organizations/{org_id}/projects", status_code=status.HTTP_201_CREATED)
def link_project(
    org_id: int,
    body: ProjectLinkCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Привязать организацию к проекту. project_role по умолчанию = organization.kind."""
    try:
        link = crud_admin.link_project(
            db,
            org_id=org_id,
            project_id=body.project_id,
            project_role=body.project_role,
        )
    except AdminError as e:
        _raise(e)
    # Формат совместим с OrgProjectLink (GET /organizations/{id}): {project_id, project_name, project_role}
    return {
        "project_id": link.project_id,
        "project_name": link.project.name,
        "project_role": link.project_role.value,
    }


@router.delete("/organizations/{org_id}/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_project(
    org_id: int,
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
):
    """Снять привязку организации к проекту (idempotent)."""
    crud_admin.unlink_project(db, org_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

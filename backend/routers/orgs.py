"""Эндпоинты управления собственной организацией (для org admin/superadmin).

org_id берётся из токена текущего пользователя, не из тела запроса.

Матрица прав (enforced в crud.admin.can_set_role / can_manage_target):
- superadmin (org): управляет admin и member; назначает роли admin/member
  (superadmin — только через /api/admin платформенным суперюзером).
- admin: управляет только member; назначает только member.
- member: ничего.
Защита последнего активного superadmin'а — в crud.admin.set_user_role_and_active.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth import require_org_admin_with_org
from crud import admin as crud_admin
from crud.admin import AdminError, can_manage_target, can_set_role
from database import get_db
from models import OrgRole, User
from security import hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orgs", tags=["orgs"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    # Суперюзер org может назначить admin/member; admin org — только member (проверка через can_set_role)
    org_role: OrgRole = OrgRole.member


class UserUpdate(BaseModel):
    org_role: OrgRole | None = None
    is_active: bool | None = None


class OrgUserOut(BaseModel):
    id: int
    email: str
    org_id: int | None
    org_role: str | None
    is_active: bool


def _to_out(user: User) -> OrgUserOut:
    return OrgUserOut(
        id=user.id,
        email=user.email,
        org_id=user.org_id,
        org_role=user.org_role.value if user.org_role else None,
        is_active=user.is_active,
    )


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=OrgUserOut)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_admin_with_org),
):
    """Создать пользователя в собственной организации.

    org_id берётся из токена текущего пользователя. Назначаемая роль ограничена
    матрицей: admin → только member; superadmin (org) → admin/member.
    superadmin-роль назначается только через /api/admin.
    """
    if not can_set_role(current_user.org_role, body.org_role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Недостаточно прав для назначения этой роли",
        )
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        is_superuser=False,
        org_id=current_user.org_id,
        org_role=body.org_role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "user_created id=%s email=%s org_id=%s role=%s",
        user.id, user.email, current_user.org_id, body.org_role.value,
    )
    return _to_out(user)


@router.get("/users", response_model=list[OrgUserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_admin_with_org),
):
    """Список пользователей своей организации."""
    users = db.query(User).filter(User.org_id == current_user.org_id).order_by(User.id).all()
    return [_to_out(u) for u in users]


@router.patch("/users/{user_id}", response_model=OrgUserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_admin_with_org),
):
    """Сменить роль и/или статус пользователя своей организации (по матрице).

    Нельзя трогать пользователей других организаций, нельзя назначать superadmin,
    нельзя понизить/деактивировать последнего активного superadmin'а.
    """
    target = db.query(User).filter(User.id == user_id).first()
    if not target or target.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if target.id == current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нельзя менять собственную роль или статус")

    # Может ли actor управлять текущей ролью target?
    if not can_manage_target(current_user.org_role, target.org_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для управления этим пользователем")

    fields = body.model_dump(exclude_unset=True)
    if "org_role" in fields and fields["org_role"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле org_role не может быть null")
    if "is_active" in fields and fields["is_active"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Поле is_active не может быть null")

    # Если меняется роль — проверяем право назначить новую роль
    if "org_role" in fields and not can_set_role(current_user.org_role, fields["org_role"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для назначения этой роли")

    try:
        user = crud_admin.set_user_role_and_active(
            db,
            user_id,
            org_role=fields.get("org_role", crud_admin.UNSET),
            is_active=fields.get("is_active", crud_admin.UNSET),
        )
    except AdminError as e:
        raise HTTPException(e.status_code, e.detail) from e
    return _to_out(user)

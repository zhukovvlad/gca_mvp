"""CRUD-операции для админ-консоли суперпользователя.

Здесь сосредоточена бизнес-логика управления организациями, пользователями и
их доступом к проектам. Роутеры (routers/admin.py, routers/orgs.py) остаются
тонкими и делегируют сюда.

Ключевые инварианты:
- Первый пользователь организации автоматически получает роль superadmin.
- Нельзя деактивировать или понизить последнего активного superadmin'а организации.
- Матрица прав org-самообслуживания: см. can_manage_target / can_set_role.
"""
import logging
import secrets

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Organization, OrgRole, Project, ProjectOrganization, ProjectRole, User
from security import hash_password

logger = logging.getLogger(__name__)

# Sentinel: поле не передано в payload (отличается от явного None)
UNSET = object()


class AdminError(Exception):
    """Доменная ошибка админ-операции. Роутер транслирует в HTTP-статус.

    Attributes:
        status_code: рекомендованный HTTP-статус (404/409/403).
        detail: понятное сообщение для пользователя.
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
#  Организации
# ---------------------------------------------------------------------------

def list_organizations(db: Session) -> list[dict]:
    """Список организаций с агрегатами: число пользователей и проектов."""
    user_count = (
        db.query(User.org_id, func.count(User.id).label("cnt"))
        .group_by(User.org_id)
        .subquery()
    )
    project_count = (
        db.query(
            ProjectOrganization.org_id,
            func.count(ProjectOrganization.project_id).label("cnt"),
        )
        .group_by(ProjectOrganization.org_id)
        .subquery()
    )
    rows = (
        db.query(
            Organization,
            func.coalesce(user_count.c.cnt, 0).label("user_count"),
            func.coalesce(project_count.c.cnt, 0).label("project_count"),
        )
        .outerjoin(user_count, user_count.c.org_id == Organization.id)
        .outerjoin(project_count, project_count.c.org_id == Organization.id)
        .order_by(Organization.id)
        .all()
    )
    return [
        {
            "id": org.id,
            "name": org.name,
            "inn": org.inn,
            "kind": org.kind.value,
            "created_at": (org.created_at.isoformat() + "Z") if org.created_at else None,
            "user_count": user_count_,
            "project_count": project_count_,
        }
        for org, user_count_, project_count_ in rows
    ]


def get_organization(db: Session, org_id: int) -> Organization | None:
    return db.query(Organization).filter(Organization.id == org_id).first()


def create_organization(db: Session, name: str, inn: str | None, kind: ProjectRole) -> Organization:
    org = Organization(name=name, inn=inn or None, kind=kind)
    db.add(org)
    db.commit()
    db.refresh(org)
    logger.info("organization_created id=%s name=%s kind=%s", org.id, org.name, kind.value)
    return org


def get_organization_detail(db: Session, org_id: int) -> dict | None:
    """Детальная карточка: поля организации, пользователи, привязки к проектам."""
    org = get_organization(db, org_id)
    if not org:
        return None

    users = db.query(User).filter(User.org_id == org_id).order_by(User.id).all()

    links = (
        db.query(ProjectOrganization, Project.name)
        .join(Project, Project.id == ProjectOrganization.project_id)
        .filter(ProjectOrganization.org_id == org_id)
        .order_by(Project.name)
        .all()
    )
    return {
        "id": org.id,
        "name": org.name,
        "inn": org.inn,
        "kind": org.kind.value,
        "created_at": (org.created_at.isoformat() + "Z") if org.created_at else None,
        "users": [_user_to_dict(u) for u in users],
        "projects": [
            {
                "project_id": link.project_id,
                "project_name": project_name,
                "project_role": link.project_role.value,
            }
            for link, project_name in links
        ],
    }


def update_organization(
    db: Session,
    org_id: int,
    name=UNSET,
    inn=UNSET,
    kind=UNSET,
) -> Organization:
    org = get_organization(db, org_id)
    if not org:
        raise AdminError(404, "Организация не найдена")
    if name is not UNSET:
        org.name = name
    if inn is not UNSET:
        org.inn = inn or None
    if kind is not UNSET:
        org.kind = kind
    db.commit()
    db.refresh(org)
    logger.info("organization_updated id=%s", org.id)
    return org


# ---------------------------------------------------------------------------
#  Пользователи
# ---------------------------------------------------------------------------

def _user_to_dict(u: User, org_name=UNSET) -> dict:
    """Сериализация пользователя. Поля совместимы с прежним ответом /api/admin/users."""
    d = {
        "id": u.id,
        "email": u.email,
        "org_id": u.org_id,
        "org_role": u.org_role.value if u.org_role else None,
        "is_superuser": u.is_superuser,
        "is_active": u.is_active,
        "created_at": (u.created_at.isoformat() + "Z") if u.created_at else None,
    }
    if org_name is not UNSET:
        d["org_name"] = org_name
    return d


def create_user_in_org(
    db: Session,
    org_id: int,
    email: str,
    password: str,
    org_role: OrgRole,
    is_active: bool = True,
) -> User:
    """Создать пользователя в организации.

    Первый пользователь организации автоматически получает роль superadmin
    (переопределяет переданную роль).

    Raises:
        AdminError 404 если организации нет, 409 если email занят.
    """
    org = get_organization(db, org_id)
    if not org:
        raise AdminError(404, "Организация не найдена")
    if db.query(User).filter(User.email == email).first():
        raise AdminError(409, "Email уже зарегистрирован")

    existing_count = db.query(User).filter(User.org_id == org_id).count()
    role = OrgRole.superadmin if existing_count == 0 else org_role

    user = User(
        email=email,
        password_hash=hash_password(password),
        is_superuser=False,
        org_id=org_id,
        org_role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user_created id=%s email=%s org_id=%s role=%s", user.id, user.email, org_id, role.value)
    return user


def list_users_paginated(
    db: Session,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Список пользователей с названием организации и пагинацией.

    Поиск q — по email и названию организации (ILIKE).
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    base = (
        db.query(User, Organization.name)
        .outerjoin(Organization, Organization.id == User.org_id)
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.filter(
            func.coalesce(User.email, "").ilike(like)
            | func.coalesce(Organization.name, "").ilike(like)
        )

    total = base.with_entities(func.count(User.id)).scalar() or 0
    rows = (
        base.order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_user_to_dict(u, org_name=org_name) for u, org_name in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _count_other_active_superadmins_locked(db: Session, org_id: int, exclude_user_id: int) -> int:
    """Сколько ДРУГИХ активных superadmin'ов в организации, с блокировкой строк.

    Важно лочить *все* активные строки superadmin'ов в организации в детерминированном
    порядке (order_by User.id), иначе два конкурентных запроса на деактивацию/понижение
    разных superadmin'ов могут одновременно пройти проверку и оставить 0 активных superadmin'ов
    (каждый лочит чужую строку, оба видят count=1 и оба проходят).

    Возвращает количество *других* (а не строки) — нам нужен только факт «> 0».
    """
    rows = (
        db.query(User.id)
        .filter(
            User.org_id == org_id,
            User.org_role == OrgRole.superadmin,
            User.is_active.is_(True),
        )
        .order_by(User.id)
        .with_for_update()
        .all()
    )
    ids = [r[0] for r in rows]
    return sum(1 for uid in ids if uid != exclude_user_id)


def set_user_role_and_active(
    db: Session,
    user_id: int,
    org_role=UNSET,
    is_active=UNSET,
) -> User:
    """Сменить роль и/или статус активности пользователя (для /api/admin).

    Защищает последнего активного superadmin'а организации: нельзя его
    деактивировать или понизить роль.

    Raises:
        AdminError 404 если пользователя нет, 409 при нарушении защиты superadmin.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AdminError(404, "Пользователь не найден")
    if user.is_superuser:
        raise AdminError(409, "Платформенный суперюзер не управляется через этот эндпоинт")

    # Снимаем роль superadmin (понижение)?
    losing_superadmin = (
        org_role is not UNSET
        and user.org_role == OrgRole.superadmin
        and org_role != OrgRole.superadmin
    )
    # Деактивируем активного superadmin'а?
    deactivating_superadmin = (
        is_active is not UNSET
        and is_active is False
        and user.org_role == OrgRole.superadmin
        and user.is_active
    )
    if (losing_superadmin or deactivating_superadmin) and user.org_id is not None:
        # Атомарно: блокируем строки других активных superadmin'ов и считаем их.
        # Параллельный запрос на деактивацию/понижение дождётся коммита и увидит
        # актуальное число — инвариант «хотя бы один активный superadmin» сохранится.
        remaining = _count_other_active_superadmins_locked(db, user.org_id, exclude_user_id=user.id)
        if remaining == 0:
            raise AdminError(409, "Нельзя деактивировать или понизить последнего активного superadmin организации")

    if org_role is not UNSET:
        user.org_role = org_role
    if is_active is not UNSET:
        user.is_active = is_active
    db.commit()
    db.refresh(user)
    logger.info("user_updated id=%s role=%s active=%s", user.id, user.org_role, user.is_active)
    return user


def reset_user_password(db: Session, user_id: int) -> tuple[User, str]:
    """Сгенерировать новый пароль на сервере, сохранить хэш, вернуть plaintext.

    Пароль генерируется криптостойким secrets.token_urlsafe и возвращается
    один раз — для разовой безопасной передачи пользователю.

    Returns:
        (user, new_password_plaintext)

    Raises:
        AdminError 404 если пользователя нет.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AdminError(404, "Пользователь не найден")
    if user.is_superuser:
        raise AdminError(409, "Платформенный суперюзер не управляется через этот эндпоинт")
    new_password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(new_password)
    db.commit()
    logger.info("user_password_reset id=%s", user.id)
    return user, new_password


# ---------------------------------------------------------------------------
#  Доступ к проектам
# ---------------------------------------------------------------------------

def link_project(
    db: Session,
    org_id: int,
    project_id: int,
    project_role: ProjectRole | None = None,
) -> ProjectOrganization:
    """Привязать организацию к проекту.

    project_role по умолчанию берётся из organization.kind, но переопределяется
    явным значением.

    Raises:
        AdminError 404 если организации/проекта нет, 409 если связь уже есть.
    """
    org = get_organization(db, org_id)
    if not org:
        raise AdminError(404, "Организация не найдена")
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise AdminError(404, "Проект не найден")

    existing = (
        db.query(ProjectOrganization)
        .filter(
            ProjectOrganization.org_id == org_id,
            ProjectOrganization.project_id == project_id,
        )
        .first()
    )
    if existing:
        raise AdminError(409, "Организация уже привязана к проекту")

    role = project_role if project_role is not None else org.kind
    link = ProjectOrganization(org_id=org_id, project_id=project_id, project_role=role)
    link.project = project
    link.organization = org
    db.add(link)
    db.commit()
    logger.info("project_linked org_id=%s project_id=%s role=%s", org_id, project_id, role.value)
    return link


def unlink_project(db: Session, org_id: int, project_id: int) -> bool:
    """Снять привязку организации к проекту. Возвращает True если что-то удалено."""
    deleted = (
        db.query(ProjectOrganization)
        .filter(
            ProjectOrganization.org_id == org_id,
            ProjectOrganization.project_id == project_id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    if deleted:
        logger.info("project_unlinked org_id=%s project_id=%s", org_id, project_id)
    return bool(deleted)


# ---------------------------------------------------------------------------
#  Матрица прав org-самообслуживания (routers/orgs.py)
# ---------------------------------------------------------------------------

def can_set_role(actor_role: OrgRole, new_role: OrgRole) -> bool:
    """Может ли actor назначить роль new_role внутри своей организации.

    - superadmin (org): может назначать admin и member (НЕ superadmin —
      только через /api/admin платформенным суперюзером).
    - admin: может назначать только member.
    - member: ничего.
    """
    if new_role == OrgRole.superadmin:
        return False  # superadmin назначается только через /api/admin
    if actor_role == OrgRole.superadmin:
        return new_role in (OrgRole.admin, OrgRole.member)
    if actor_role == OrgRole.admin:
        return new_role == OrgRole.member
    return False


def can_manage_target(actor_role: OrgRole, target_role: OrgRole) -> bool:
    """Может ли actor управлять пользователем с ролью target_role (деактивация/смена роли).

    - superadmin (org): может управлять admin и member. Управление другими
      superadmin'ами — только через /api/admin (платформенным суперюзером),
      не через org-самообслуживание.
    - admin: может управлять только member.
    - member: никем.
    """
    if actor_role == OrgRole.superadmin:
        return target_role in (OrgRole.admin, OrgRole.member)
    if actor_role == OrgRole.admin:
        return target_role == OrgRole.member
    return False

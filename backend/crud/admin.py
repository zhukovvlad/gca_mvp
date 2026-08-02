"""CRUD-операции админ-консоли: управление пользователями (single-tenant).

Роутер (routers/admin.py) остаётся тонким и делегирует сюда.

Ключевой инвариант: нельзя деактивировать или понизить последнего активного admin.
"""
import logging
import secrets

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import User, UserRole
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


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "role": u.role.value,
        "is_active": u.is_active,
        "created_at": (u.created_at.isoformat() + "Z") if u.created_at else None,
    }


def create_user(
    db: Session,
    email: str,
    password: str,
    role: UserRole,
    is_active: bool = True,
) -> User:
    """Создать пользователя.

    Raises:
        AdminError 409 если email занят.
    """
    if db.query(User).filter(User.email == email).first():
        raise AdminError(409, "Email уже зарегистрирован")

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user_created id=%s email=%s role=%s", user.id, user.email, role.value)
    return user


def list_users_paginated(
    db: Session,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Список пользователей с пагинацией. Поиск q — по email (ILIKE)."""
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    base = db.query(User)
    if q and q.strip():
        base = base.filter(User.email.ilike(f"%{q.strip()}%"))

    total = base.with_entities(func.count(User.id)).scalar() or 0
    rows = (
        base.order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_user_to_dict(u) for u in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _count_other_active_admins_locked(db: Session, exclude_user_id: int) -> int:
    """Сколько ДРУГИХ активных admin'ов, с блокировкой строк.

    Важно лочить *все* активные строки admin'ов в детерминированном порядке
    (order_by User.id), иначе два конкурентных запроса на деактивацию/понижение
    разных admin'ов могут одновременно пройти проверку и оставить 0 активных
    admin'ов (каждый лочит чужую строку, оба видят count=1 и оба проходят).
    """
    rows = (
        db.query(User.id)
        .filter(
            User.role == UserRole.admin,
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
    role=UNSET,
    is_active=UNSET,
) -> User:
    """Сменить роль и/или статус активности пользователя.

    Защищает последнего активного admin'а: нельзя его деактивировать
    или понизить роль.

    Raises:
        AdminError 404 если пользователя нет, 409 при нарушении защиты admin.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AdminError(404, "Пользователь не найден")

    losing_admin = (
        role is not UNSET
        and user.role == UserRole.admin
        and role != UserRole.admin
    )
    deactivating_admin = (
        is_active is not UNSET
        and is_active is False
        and user.role == UserRole.admin
        and user.is_active
    )
    if losing_admin or deactivating_admin:
        # Атомарно: блокируем строки других активных admin'ов и считаем их.
        remaining = _count_other_active_admins_locked(db, exclude_user_id=user.id)
        if remaining == 0:
            raise AdminError(409, "Нельзя деактивировать или понизить последнего активного admin")

    if role is not UNSET:
        user.role = role
    if is_active is not UNSET:
        user.is_active = is_active
    db.commit()
    db.refresh(user)
    logger.info("user_updated id=%s role=%s active=%s", user.id, user.role, user.is_active)
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
    new_password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(new_password)
    db.commit()
    logger.info("user_password_reset id=%s", user.id)
    return user, new_password

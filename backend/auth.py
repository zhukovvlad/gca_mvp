"""FastAPI dependencies для аутентификации и авторизации.

Содержит:
- get_current_user — проверка access-токена из куки
- require_csrf — проверка CSRF double-submit
- require_superuser / require_org_admin — проверки ролей
- get_project_access / ProjectAccess — изоляция по проекту + роль org
"""
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models import OrgRole, Project, ProjectOrganization, ProjectRole, User
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


def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    """Разрешить доступ только суперюзерам системы.

    Raises:
        403 если пользователь не is_superuser.
    """
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    return current_user


def require_org_admin(current_user: User = Depends(get_current_user)) -> User:
    """Разрешить доступ суперюзерам и org-администраторам (superadmin/admin).

    Raises:
        403 если роль — member.
    """
    if current_user.is_superuser:
        return current_user
    if current_user.org_role not in (OrgRole.superadmin, OrgRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Org admin required")
    return current_user


def require_org_admin_with_org(current_user: User = Depends(get_current_user)) -> User:
    """Разрешить доступ только org-админам (superadmin/admin) с org_id.

    Суперюзеры без организации (без org_id) блокируются явно — они управляют
    пользователями через /api/admin.

    Raises:
        403 если суперюзер, роль member, или нет org_id.
    """
    if current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Суперюзеры управляют пользователями через /api/admin")
    if current_user.org_role not in (OrgRole.superadmin, OrgRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Org admin required")
    if not current_user.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no organization")
    return current_user


def require_org_superadmin(current_user: User = Depends(get_current_user)) -> User:
    """Разрешить доступ только org-superadmin'ам (с org_id).

    Используется для операций, доступных внутри организации лишь её superadmin'у:
    редактирование организации, управление admin'ами, доступ к проектам.
    Платформенный is_superuser управляет всем через /api/admin и блокируется здесь.

    Raises:
        403 если суперюзер, роль не superadmin, или нет org_id.
    """
    if current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Суперюзеры управляют организациями через /api/admin")
    if current_user.org_role != OrgRole.superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуется роль superadmin организации")
    if not current_user.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "У пользователя нет организации")
    return current_user


class ProjectAccess:
    """Контекст проверенного доступа к проекту.

    Создаётся через dependency get_project_access после проверки того,
    что текущий пользователь имеет доступ к указанному проекту.
    """

    def __init__(
        self,
        project: Project,
        project_role: ProjectRole | None,
        is_superuser: bool,
        user: User,
    ):
        self.project = project
        self.project_role = project_role  # None если суперюзер
        self.is_superuser = is_superuser
        self.user = user

    @property
    def is_customer(self) -> bool:
        """True для заказчика (customer) или суперюзера — видит все данные проекта."""
        return self.is_superuser or self.project_role == ProjectRole.customer

    @property
    def is_contractor(self) -> bool:
        """True для подрядчика — видит только свои загрузки."""
        return not self.is_superuser and self.project_role == ProjectRole.contractor


def get_project_access(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectAccess:
    """Dependency: проверить доступ к проекту и вернуть ProjectAccess.

    Возвращает 404 (а не 403) для чужих проектов — не раскрываем существование ресурса.

    Raises:
        404 если проект не найден или у org пользователя нет доступа.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    if current_user.is_superuser:
        return ProjectAccess(project, None, True, current_user)

    link = (
        db.query(ProjectOrganization)
        .filter(
            ProjectOrganization.project_id == project_id,
            ProjectOrganization.org_id == current_user.org_id,
        )
        .first()
    )
    if not link:
        # 404, не 403 — не раскрываем существование чужого проекта
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    return ProjectAccess(project, link.project_role, False, current_user)

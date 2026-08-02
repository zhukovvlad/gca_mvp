# План реализации: Аутентификация + изоляция организаций (v2.1)

**Проект:** УПД Трекер цен
**Стек:** FastAPI + PostgreSQL + React 18 + TypeScript
**Оценка:** 4–6 рабочих дней

---

## Changelog

**v2.1**

- **Этап 7.0:** убран отдельный `renderWithAuth`. Вместо этого расширяется существующий `renderWithProviders` (в нём уже есть `MemoryRouter` и `ThemeProvider`) параметром `initialUser`. Дублирующий хелпер без роутинга ломал бы тесты, использующие `useNavigate` и т.п.
- **Этап 5.2:** добавлен комментарий в CSRF middleware — почему `/api/auth/refresh` и `/api/auth/logout` НЕ в списке исключений (defense-in-depth с `Depends(require_csrf)` на самих эндпоинтах).

**v2**

- **CORS:** добавлена настройка `ALLOWED_ORIGINS` и `allow_credentials=True` — без этого браузер не отправляет cookie на wildcard origin.
- **`request.client` за прокси:** добавлен хелпер `get_client_ip` с обработкой `X-Forwarded-For` и `None`.
- **DATABASE_URL:** убираем `load_dotenv()` из main.py, всё через `settings`.
- **Полный список роутеров в Этапе 5:** `projects`, `documents`, `invoices`, `reference_prices`, `material_classes`, `suppliers`, `export`, `dashboard` — явный чек-лист.
- **MSW handlers для фронт-тестов:** добавлен подэтап 7.0 с моками auth-эндпоинтов.
- **Комментарий в миграции** про `native_enum=False` — чтобы никто не «исправил» на native PG ENUM.

---

## Архитектурные решения (зафиксированы)

- **Хеширование паролей:** `pwdlib[argon2]`. Argon2id — рекомендация OWASP, активно поддерживаемый преемник `passlib`.
- **JWT:** `pyjwt`, алгоритм HS256, секрет из env.
  - **access token** — 30 минут, payload: `user_id`, `org_id`, `is_superuser`, `org_role`, `exp`, `iat`, `jti`.
  - **refresh token** — 14 дней, хранится в БД (`refresh_tokens`), отзываемый.
- **Транспорт токенов:** httpOnly cookie.
  - `access_token`: `HttpOnly; Secure; SameSite=Lax; Path=/`
  - `refresh_token`: `HttpOnly; Secure; SameSite=Lax; Path=/api/auth`
  - **CSRF:** double-submit cookie. На логине ставится не-httpOnly cookie `csrf_token`. Фронт читает её и отправляет в заголовке `X-CSRF-Token` для всех state-changing запросов. Бэк сверяет cookie и заголовок.
- **Изоляция:** трёхслойная.
  - **Слой A — org isolation:** dependency `get_project_access`.
  - **Слой B — project-role filtering:** customer vs contractor внутри crud-функций.
  - **Слой C — org-role check:** в админских ручках.
- **Поведение при отказе доступа:** **404, а не 403** для чужих ресурсов (не палим существование). 403 — только для своих ресурсов без прав.

---

## Что включено в план

- ✅ CLI для создания первого суперюзера — без него не залогинишься.
- ✅ Тест-сторож «все ручки требуют токен» — защита от регрессий навсегда.
- ✅ Логирование событий auth через стандартный `logging` (логин, провал, создание юзера).

## Что НЕ делаем сейчас (явно)

- Rate limiting на `/login` — отдельный тикет для прод-релиза (`slowapi`).
- Полноценный аудит-лог в БД (модель `AuditEvent`) — после MVP.
- Восстановление пароля по email.
- 2FA / OAuth / SSO.
- Приглашения пользователей по email.
- UI управления org/users (только бэкенд в этом этапе).

---

## Этап 0. Подготовка (1–2 часа)

### 0.1 Зависимости

```txt
# requirements.txt — добавить
pyjwt>=2.8.0
pwdlib[argon2]>=0.2.1
pydantic-settings>=2.5.0
itsdangerous>=2.2.0
```

### 0.2 Settings (`backend/config.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    COOKIE_SECURE: bool = True
    COOKIE_DOMAIN: str | None = None

    # CORS — wildcard "*" несовместим с credentials=true в браузере
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]

    DATABASE_URL: str

settings = Settings()
```

> **Важно:** убрать `load_dotenv()` из `main.py` и перевести весь доступ к env-переменным на `settings.*`. `pydantic-settings` сам подхватывает `.env`. Иначе появится два источника правды для одних и тех же переменных.

### 0.3 .env

```env
SECRET_KEY=<openssl rand -hex 32>
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=14
COOKIE_SECURE=False    # на dev без HTTPS
ALLOWED_ORIGINS=["http://localhost:5173"]
```

`.env.example` — те же ключи без значений.

### 0.4 Хелпер для IP-адреса (`backend/utils.py`)

```python
from fastapi import Request

def get_client_ip(request: Request) -> str | None:
    """Возвращает реальный IP клиента с учётом reverse-proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
```

Использовать во всех местах, где раньше было `request.client.host`.

### 0.5 Чек-лист

- [ ] `pip install -r requirements.txt` проходит
- [ ] `from backend.config import settings` работает
- [ ] `load_dotenv()` удалён из `main.py`
- [ ] `DATABASE_URL` в main/database читается из `settings`, а не из `os.getenv`
- [ ] `.env` в `.gitignore`

---

## Этап 1. Модели и миграция (3–4 часа)

### 1.1 Новые модели

```python
# backend/models.py
import enum
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey,
    Enum as SqlEnum, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

class OrgRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"
    member = "member"

class ProjectRole(str, enum.Enum):
    customer = "customer"
    contractor = "contractor"

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())

    users = relationship("User", back_populates="organization")
    project_links = relationship("ProjectOrganization", back_populates="organization")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    is_superuser = Column(Boolean, nullable=False, default=False)
    org_role = Column(
        SqlEnum(OrgRole, name="org_role", native_enum=False),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())

    organization = relationship("Organization", back_populates="users")
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )

class ProjectOrganization(Base):
    __tablename__ = "project_organizations"
    project_id = Column(Integer, ForeignKey("projects.id"), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    project_role = Column(
        SqlEnum(ProjectRole, name="project_role", native_enum=False),
        nullable=False,
    )
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("Project", back_populates="org_links")
    organization = relationship("Organization", back_populates="project_links")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    user = relationship("User", back_populates="refresh_tokens")
```

> **Почему `native_enum=False`:** хранит значения как VARCHAR с CHECK constraint вместо PG ENUM. Расширять можно без `ALTER TYPE`, миграции автогенерируются нормально.

### 1.2 Изменения существующих моделей

```python
class Project(Base):
    # ... существующие поля
    customer_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    org_links = relationship(
        "ProjectOrganization", back_populates="project", cascade="all, delete-orphan"
    )

class Document(Base):
    # ... существующие поля
    file_hash = Column(String(64), nullable=True, index=True)  # sha256 hex
    uploaded_by_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "file_hash", name="uq_documents_project_file_hash"),
    )
```

> **Важно:** unique constraint на `invoices` из исходного плана **убираем**. Дедуп идёт через `file_hash` в `documents` — это проверяется до парсинга и экономит вызовы AI.

### 1.3 Миграция

`backend/alembic/versions/2026_05_23_1200-add_auth_schema.py` — пишем **вручную**, не через autogenerate, для контроля.

**Обязательный docstring в начале миграции:**

```python
"""add_auth_schema

Note: org_role и project_role созданы как VARCHAR с CHECK constraint
(native_enum=False в моделях). Сделано сознательно: PG ENUM требует
ALTER TYPE для добавления значений, что плохо поддерживается autogenerate
в Alembic и блокирует параллельные миграции. НЕ менять на native PG ENUM.
"""
```

Порядок операций:

1. Создать `organizations`.
2. Создать `users`.
3. Создать `project_organizations`.
4. Создать `refresh_tokens`.
5. Добавить колонки в `projects` (`customer_org_id`).
6. Добавить колонки в `documents` (`file_hash`, `uploaded_by_org_id`, `uploaded_by_user_id`).
7. Добавить unique constraint `uq_documents_project_file_hash`.

Все FK — nullable.

**Backfill (опционально):** если в dev-БД есть данные — создать одну default org и привязать к ней существующие projects/documents в той же миграции. Если данных нет — пропустить.

### 1.4 Чек-лист

- [ ] `just db-migrate` отрабатывает без ошибок
- [ ] `\d users` в psql показывает корректную схему
- [ ] Существующие endpoint-ы (пока без auth) всё ещё отвечают
- [ ] Старые тесты проходят

---

## Этап 2. Core auth (4–6 часов)

### 2.1 Низкоуровневые примитивы (`backend/security.py`)

```python
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from pwdlib import PasswordHash
from backend.config import settings

password_hash = PasswordHash.recommended()  # argon2id by default

def hash_password(plain: str) -> str:
    return password_hash.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return password_hash.verify(plain, hashed)

def create_access_token(payload: dict) -> str:
    now = datetime.now(timezone.utc)
    to_encode = {
        **payload,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")

def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload

def generate_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Store hash, give raw to user."""
    raw = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed

def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)
```

### 2.2 FastAPI dependencies (`backend/auth.py`)

```python
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
import jwt

from backend.security import decode_access_token
from backend.models import User, ProjectOrganization, OrgRole, ProjectRole, Project
from backend.database import get_db

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    user = (
        db.query(User)
        .filter(User.id == int(payload["sub"]), User.is_active == True)
        .first()
    )
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_csrf(request: Request):
    """Проверяет CSRF для state-changing методов."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token mismatch")


def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    return current_user


def require_org_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.is_superuser:
        return current_user
    if current_user.org_role not in (OrgRole.superadmin, OrgRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Org admin required")
    return current_user


class ProjectAccess:
    """Контекст доступа к проекту."""

    def __init__(self, project, project_role, is_superuser, user):
        self.project = project
        self.project_role = project_role  # None если superuser
        self.is_superuser = is_superuser
        self.user = user

    @property
    def is_customer(self) -> bool:
        return self.is_superuser or self.project_role == ProjectRole.customer

    @property
    def is_contractor(self) -> bool:
        return not self.is_superuser and self.project_role == ProjectRole.contractor


def get_project_access(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectAccess:
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
        # 404, не 403 — не палим существование чужого проекта
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    return ProjectAccess(project, link.project_role, False, current_user)
```

### 2.3 Чек-лист

- [ ] Unit-тесты на `security.py`: hash/verify, encode/decode, expired token
- [ ] Unit-тесты на dependencies: mock request, проверка 401/403/404

---

## Этап 3. Роутер /api/auth (3–4 часа)

### 3.1 `backend/routers/auth.py`

```python
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Response, Request, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User, RefreshToken
from backend.security import (
    verify_password, create_access_token,
    generate_refresh_token, hash_refresh_token, generate_csrf_token,
)
from backend.auth import (
    get_current_user, require_csrf,
    ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME,
)
from backend.config import settings
from backend.utils import get_client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str):
    common = dict(httponly=True, secure=settings.COOKIE_SECURE, samesite="lax")
    response.set_cookie(
        ACCESS_COOKIE_NAME, access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/", **common,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME, refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth", **common,
    )
    # CSRF — НЕ httpOnly: фронт должен прочитать
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/", httponly=False, secure=settings.COOKIE_SECURE, samesite="lax",
    )


def _clear_auth_cookies(response: Response):
    for name, path in [
        (ACCESS_COOKIE_NAME, "/"),
        (REFRESH_COOKIE_NAME, "/api/auth"),
        (CSRF_COOKIE_NAME, "/"),
    ]:
        response.delete_cookie(name, path=path)


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    client_ip = get_client_ip(request)
    user = db.query(User).filter(User.email == body.email, User.is_active == True).first()
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
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
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
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    hashed = hash_refresh_token(raw)
    rt = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token_hash == hashed,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if not rt:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    user = rt.user
    # Rotation: отзываем старый, выдаём новый
    rt.revoked_at = datetime.now(timezone.utc)
    new_raw, new_hashed = generate_refresh_token()
    db.add(RefreshToken(
        user_id=user.id, token_hash=new_hashed,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=request.headers.get("user-agent"),
        ip_address=get_client_ip(request),
    ))
    db.commit()

    access = create_access_token({
        "sub": str(user.id), "org_id": user.org_id,
        "is_superuser": user.is_superuser,
        "org_role": user.org_role.value if user.org_role else None,
    })
    csrf = generate_csrf_token()
    _set_auth_cookies(response, access, new_raw, csrf)
    return {"status": "ok"}


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw:
        hashed = hash_refresh_token(raw)
        db.query(RefreshToken).filter(
            RefreshToken.token_hash == hashed,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": datetime.now(timezone.utc)})
        db.commit()
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
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
        } if current_user.organization else None,
    }
```

### 3.2 Подключить в main.py

```python
from backend.routers import auth as auth_router
app.include_router(auth_router.router)
```

### 3.3 Чек-лист

- [ ] `POST /api/auth/login` с корректными credentials → 200, ставит 3 cookie
- [ ] `POST /api/auth/login` с некорректными → 401
- [ ] `GET /api/auth/me` с cookie → 200 с user info
- [ ] `GET /api/auth/me` без cookie → 401
- [ ] `POST /api/auth/refresh` ротирует токены
- [ ] `POST /api/auth/logout` чистит cookie и помечает `refresh_token.revoked_at`

---

## Этап 4. CLI и админка (3–4 часа)

### 4.1 CLI (`backend/cli.py`)

```python
import click
from backend.database import SessionLocal
from backend.models import User, Organization
from backend.security import hash_password


@click.group()
def cli():
    pass


@cli.command()
@click.option("--email", required=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_superuser(email, password):
    db = SessionLocal()
    if db.query(User).filter(User.email == email).first():
        click.echo("User already exists", err=True)
        return
    user = User(
        email=email,
        password_hash=hash_password(password),
        is_superuser=True,
        org_id=None,
        org_role=None,
    )
    db.add(user)
    db.commit()
    click.echo(f"Superuser created: id={user.id}")


@cli.command()
@click.option("--name", required=True)
@click.option("--inn", default=None)
def create_org(name, inn):
    db = SessionLocal()
    org = Organization(name=name, inn=inn)
    db.add(org)
    db.commit()
    click.echo(f"Organization created: id={org.id}")


if __name__ == "__main__":
    cli()
```

Использование:

```bash
python -m cli create-superuser --email admin@test.com
```

Добавить в `justfile`:

```
create-superuser email:
    python -m cli create-superuser --email {{email}}
```

### 4.2 `backend/routers/admin.py` — только superuser

Эндпоинты с `Depends(require_superuser)`:

- `POST /api/admin/organizations` — создать org
- `GET /api/admin/organizations` — список
- `POST /api/admin/organizations/{id}/users` — создать пользователя в org

> **Важно:** если в org пока нет пользователей — новый автоматически получает `org_role=superadmin`.

- `GET /api/admin/users` — список с org info

### 4.3 `backend/routers/orgs.py` — org superadmin/admin

Эндпоинты с `Depends(require_org_admin)`:

- `POST /api/orgs/users` — создать юзера в **своей** org. `org_id` берём из `current_user.org_id`, не из тела запроса. `org_role` может быть только `member` или `admin`.
- `GET /api/orgs/users` — список юзеров своей org

### 4.4 Чек-лист

- [ ] `just create-superuser` создаёт юзера
- [ ] Логин под суперюзером работает
- [ ] Суперюзер создаёт org через API
- [ ] Первый юзер в org → автоматически `superadmin`
- [ ] Org superadmin создаёт второго юзера своей org
- [ ] Member пытается создать юзера → 403
- [ ] User из org A пытается создать юзера в org B → не может (берётся свой `org_id`)

---

## Этап 5. Защита ручек и изоляция (1–2 дня)

### 5.0 CORS (критично — сделать ПЕРВЫМ)

Без этого фронт с `withCredentials: true` не получит cookie. Браузер по стандарту запрещает credentials с wildcard origin.

В `main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # НЕ ["*"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-CSRF-Token"],
)
```

**Чек:** в DevTools → Network → запрос с фронта должен иметь заголовок `Access-Control-Allow-Credentials: true`, а в Response — конкретный origin (не `*`).

### 5.1 Глобальная аутентификация

Каждому существующему роутеру навешиваем `dependencies=[Depends(get_current_user)]`:

```python
from backend.auth import get_current_user

app.include_router(projects.router, dependencies=[Depends(get_current_user)])
app.include_router(documents.router, dependencies=[Depends(get_current_user)])
# ... и т.д.

# Без auth: только auth_router и docs
app.include_router(auth_router.router)
```

### 5.2 CSRF middleware

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    # /api/auth/login — единственный POST без CSRF cookie (она ещё не выдана).
    # /api/auth/refresh и /api/auth/logout НЕ исключены: к моменту их вызова
    # CSRF cookie уже стоит. На них дополнительно навешан Depends(require_csrf) —
    # это сознательный defense-in-depth, не дубликат.
    if request.url.path in ("/api/auth/login", "/docs", "/openapi.json"):
        return await call_next(request)
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if not cookie or not header or cookie != header:
        return JSONResponse({"detail": "CSRF token mismatch"}, status_code=403)
    return await call_next(request)
```

### 5.3 Шаблон рефакторинга проектных ручек

**Было:**

```python
@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404)
    return project
```

**Стало:**

```python
@router.get("/projects/{project_id}")
def get_project(access: ProjectAccess = Depends(get_project_access)):
    return access.project
```

`get_project_access` уже проверила существование + доступ + вернула проект.

### 5.4 Список проектов

```python
@router.get("/projects")
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Project)
    if not current_user.is_superuser:
        q = q.join(ProjectOrganization).filter(
            ProjectOrganization.org_id == current_user.org_id
        )
    return q.all()
```

### 5.5 Создание проекта

Только org с ролью customer. В момент создания `ProjectOrganization` ещё нет — создаём вместе.

```python
@router.post("/projects")
def create_project(
    body: ProjectCreate,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    if current_user.is_superuser:
        if not body.customer_org_id:
            raise HTTPException(400, "customer_org_id required for superuser")
        org_id = body.customer_org_id
    else:
        org_id = current_user.org_id

    project = Project(
        name=body.name,
        contract_number=body.contract_number,
        customer_org_id=org_id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectOrganization(
        project_id=project.id, org_id=org_id, project_role=ProjectRole.customer,
    ))
    db.commit()
    return project
```

### 5.6 Contractor видит только свои invoices

```python
@router.get("/projects/{project_id}/invoices")
def list_invoices(
    access: ProjectAccess = Depends(get_project_access),
    db: Session = Depends(get_db),
):
    q = db.query(Invoice).join(Document).filter(Document.project_id == access.project.id)
    if access.is_contractor:
        q = q.filter(Document.uploaded_by_org_id == access.user.org_id)
    return q.all()
```

### 5.7 Чек-лист по ручкам

Для каждой ручки определить:

- какой уровень доступа нужен (любой авторизованный / project access / org admin / superuser)
- нужна ли фильтрация по `project_role` (customer vs contractor)

**Полный список роутеров** (по каждому пройти отдельно):

- [ ] `backend/routers/projects.py` — list через JOIN ProjectOrganization; одиночный — через `get_project_access`
- [ ] `backend/routers/documents.py` — `get_project_access` + фильтр contractor по `uploaded_by_org_id`
- [ ] `backend/routers/invoices.py` — `get_project_access` + фильтр contractor по `Document.uploaded_by_org_id`
- [ ] `backend/routers/reference_prices.py` — `get_project_access`, contractor скорее всего read-only (зафиксировать)
- [ ] `backend/routers/material_classes.py` — глобальный справочник, доступ для любого авторизованного на чтение; модификация — superuser
- [ ] `backend/routers/suppliers.py` — read для авторизованных, модификация — org admin
- [ ] `backend/routers/export.py` — `get_project_access` + фильтр contractor (важно: экспорт легко слить чужие данные)
- [ ] `backend/routers/dashboard.py` — **особый случай:** агрегаты по нескольким проектам. Фильтрация через явный JOIN `ProjectOrganization`, не через `get_project_access`. Для contractor дополнительно фильтр по `uploaded_by_org_id`.

> **На `dashboard.py` обратить особое внимание:** это место, где утечка чужих данных наиболее вероятна, потому что запросы агрегированные и `project_id` может вообще не быть в URL.

~30 минут на ручку в среднем.

### 5.8 Чек-лист

- [ ] Все запросы без cookie → 401
- [ ] User из org A не видит проект org B (404)
- [ ] Contractor видит в `/invoices` только свои документы
- [ ] Customer видит все invoices проекта
- [ ] Member не может создать проект (403)
- [ ] Существующие тесты обновлены (логин в `conftest.py`)

---

## Этап 6. Тест-сторож (1 час)

`tests/test_auth_coverage.py`:

```python
import pytest
from fastapi.testclient import TestClient
from backend.main import app

PUBLIC_PATHS = {
    "/api/auth/login",
    "/docs", "/openapi.json", "/redoc",
    "/docs/oauth2-redirect",
}


def _collect_routes():
    for route in app.routes:
        if not hasattr(route, "methods"):
            continue
        for method in route.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            yield method, route.path


@pytest.mark.parametrize("method,path", list(_collect_routes()))
def test_endpoint_requires_auth(method, path):
    if path in PUBLIC_PATHS:
        pytest.skip("public endpoint")
    test_path = path
    for placeholder in ["{project_id}", "{id}", "{document_id}"]:
        test_path = test_path.replace(placeholder, "1")

    client = TestClient(app)
    response = client.request(method, test_path)
    assert response.status_code in (401, 403), (
        f"{method} {path} returned {response.status_code} without auth"
    )
```

Этот тест навсегда фиксирует: новые ручки не могут случайно быть публичными.

---

## Этап 7. Frontend (1–2 дня)

### 7.0 MSW handlers (сделать ДО переключения клиента)

Без этого все существующие фронт-тесты упадут на `useCurrentUser` в `ProtectedRoute`, потому что MSW с `onUnhandledRequest: "error"` будет ругаться на необработанные `/api/auth/me`.

В `frontend/src/mocks/handlers.ts` добавить:

```typescript
import { http, HttpResponse } from 'msw';

export const authHandlers = [
  http.get('/api/auth/me', () => {
    return HttpResponse.json({
      id: 1,
      email: 'test@test.com',
      org_id: 1,
      org_role: 'admin',
      is_superuser: false,
      organization: { id: 1, name: 'Test Org', inn: null },
    });
  }),
  http.post('/api/auth/login', () => HttpResponse.json({ status: 'ok' })),
  http.post('/api/auth/logout', () => HttpResponse.json({ status: 'ok' })),
  http.post('/api/auth/refresh', () => HttpResponse.json({ status: 'ok' })),
];

export const handlers = [
  ...authHandlers,
  // ... остальные существующие хендлеры
];
```

В test setup (обычно `setupTests.ts` или подобный) убедиться, что `QueryClient` для тестов либо имеет предзаполненный кеш `currentUser`, либо тесты ждут загрузки.

**Не создавать отдельный `renderWithAuth`** — в проекте уже есть `renderWithProviders` с `MemoryRouter` и `ThemeProvider`. Дублирующая обёртка без них приведёт к падению тестов, использующих роутинг. Правильно — расширить существующий хелпер:

```typescript
// frontend/src/test/utils.tsx — расширяем существующий renderWithProviders

const mockUser = {
  id: 1, email: 'test@test.com', org_id: 1,
  org_role: 'admin' as const, is_superuser: false,
  organization: { id: 1, name: 'Test Org', inn: null },
};

interface RenderOptions {
  initialUser?: User | null;  // null = неавторизованный сценарий
  // ... остальные существующие опции
}

export function renderWithProviders(
  ui: ReactElement,
  { initialUser = mockUser, ...options }: RenderOptions = {}
) {
  const qc = createTestQueryClient();
  if (initialUser) {
    qc.setQueryData(['currentUser'], initialUser);
  }
  // ... существующая обёртка с MemoryRouter, ThemeProvider и т.д.
}
```

Передача `initialUser: null` даёт сценарий «незалогиненный пользователь» для тестов редиректа на `/login`.

### 7.1 Axios клиент с CSRF (`frontend/src/services/api/client.ts`)

```typescript
import axios from 'axios';

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

export const apiClient = axios.create({
  baseURL: '/api',
  withCredentials: true, // cookie шлются автоматически
});

// CSRF на state-changing requests
apiClient.interceptors.request.use((config) => {
  const method = config.method?.toUpperCase();
  if (method && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrf = getCookie('csrf_token');
    if (csrf) {
      config.headers['X-CSRF-Token'] = csrf;
    }
  }
  return config;
});

// Авто-refresh на 401
let refreshing: Promise<void> | null = null;

apiClient.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config;
    if (
      error.response?.status === 401 &&
      !original._retry &&
      original.url !== '/auth/refresh'
    ) {
      original._retry = true;
      try {
        refreshing = refreshing ?? apiClient.post('/auth/refresh').then(() => {
          refreshing = null;
        });
        await refreshing;
        return apiClient(original);
      } catch {
        window.location.href = '/login';
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  }
);
```

### 7.2 `frontend/src/services/api/auth.ts`

```typescript
import { apiClient } from './client';
import type { User } from '@/types/auth';

export const authApi = {
  login: (email: string, password: string) =>
    apiClient.post('/auth/login', { email, password }),
  logout: () => apiClient.post('/auth/logout'),
  me: () => apiClient.get<User>('/auth/me').then((r) => r.data),
};
```

### 7.3 `frontend/src/types/auth.ts`

```typescript
export interface User {
  id: number;
  email: string;
  org_id: number | null;
  org_role: 'superadmin' | 'admin' | 'member' | null;
  is_superuser: boolean;
  organization: { id: number; name: string; inn: string | null } | null;
}
```

### 7.4 `frontend/src/hooks/useAuth.ts`

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authApi } from '@/services/api/auth';

export function useCurrentUser() {
  return useQuery({
    queryKey: ['currentUser'],
    queryFn: authApi.me,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      authApi.login(email, password),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['currentUser'] }),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      qc.clear(); // обязательно чистим кеш
      window.location.href = '/login';
    },
  });
}
```

### 7.5 `frontend/src/pages/LoginPage.tsx`

- Форма на shadcn/ui: `Input`, `Button`, `Card`
- На submit — `useLogin`
- При 401 — `<Alert>` «Неверный email или пароль»
- На успех — `navigate('/')`

### 7.6 Route guard в `App.tsx`

```typescript
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { data: user, isLoading, isError } = useCurrentUser();
  if (isLoading) return <Spinner />;
  if (isError || !user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// В роутере:
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/*" element={<ProtectedRoute><MainLayout /></ProtectedRoute>} />
</Routes>
```

### 7.7 Чек-лист

- [ ] MSW handlers для `/api/auth/*` добавлены в `handlers.ts`
- [ ] Существующие фронт-тесты проходят (нет ошибок `onUnhandledRequest`)
- [ ] Без cookie открытие любой страницы → редирект на `/login`
- [ ] Логин → редирект на `/`
- [ ] Logout → редирект на `/login` + cache очищен
- [ ] Просроченный access token → автоматический refresh → запрос ретраится прозрачно
- [ ] Просроченный refresh token → редирект на `/login`

---

## Этап 8. Финальная проверка (2–3 часа)

### 8.1 Ручное E2E

1. Создать суперюзера через CLI.
2. Залогиниться, открыть DevTools → проверить cookie: `access_token`, `refresh_token`, `csrf_token`.
3. Создать org A и org B.
4. Создать пользователя в org A → должен быть `superadmin` org A.
5. Создать проект под org A. Залогиниться под user org B → проект A не виден.
6. Поставить временно `ACCESS_TOKEN_EXPIRE_MINUTES=1` → дёрнуть запрос через минуту → refresh должен случиться прозрачно.
7. Logout → cookie очищены, в БД `refresh_tokens.revoked_at` проставлен.

### 8.2 Автоматические тесты

- [ ] Тест-сторож проходит
- [ ] `just lint` чист
- [ ] `just typecheck-frontend` чист
- [ ] Существующие тесты обновлены (логин в `conftest.py`)

---

## Оценка по времени

| Этап | Время |
|------|-------|
| 0. Подготовка | 1–2 ч |
| 1. Модели и миграция | 3–4 ч |
| 2. Core auth | 4–6 ч |
| 3. /api/auth | 3–4 ч |
| 4. CLI и админка | 3–4 ч |
| 5. Защита ручек и изоляция | 1–2 дня |
| 6. Тест-сторож | 1 ч |
| 7. Frontend | 1–2 дня |
| 8. Финальная проверка | 2–3 ч |
| **Итого** | **4–6 рабочих дней** |

Рекомендую делать в этом порядке без перескоков — каждый следующий этап зависит от предыдущего. После каждого этапа есть осмысленная точка коммита.

"""Криптографические примитивы для аутентификации.

Этот модуль содержит только «чистые» функции без зависимостей от FastAPI/SQLAlchemy.
Всё, что связано с HTTP-запросами и сессией БД — в auth.py.
"""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from config import settings

# Argon2id — рекомендация OWASP для хэширования паролей (устойчив к GPU-атакам)
_password_hash = PasswordHash.recommended()


# ---------------------------------------------------------------------------
#  Хэширование паролей
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Хэшировать пароль через Argon2id. Возвращает PHC-string для хранения в БД."""
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против сохранённого Argon2id хэша."""
    return _password_hash.verify(plain, hashed)


# ---------------------------------------------------------------------------
#  JWT access tokens
# ---------------------------------------------------------------------------

def create_access_token(payload: dict) -> str:
    """Создать подписанный JWT access-токен.

    Args:
        payload: dict с полями: sub (user_id str), org_id, is_superuser, org_role.

    Returns:
        Подписанный JWT (алгоритм HS256, срок из settings.ACCESS_TOKEN_EXPIRE_MINUTES).
    """
    now = datetime.now(UTC)
    to_encode = {
        **payload,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
        # jti (JWT ID) — уникальный ID токена; нужен для будущего блэклиста
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Декодировать и валидировать JWT access-токен.

    Raises:
        jwt.ExpiredSignatureError: если токен просрочен.
        jwt.InvalidTokenError: если подпись неверна или тип != 'access'.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


# ---------------------------------------------------------------------------
#  Refresh tokens
# ---------------------------------------------------------------------------

def generate_refresh_token() -> tuple[str, str]:
    """Сгенерировать refresh-токен.

    Returns:
        (raw_token, sha256_hash). В куки отдаётся raw, в БД хранится только хэш.
    """
    raw = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def hash_refresh_token(raw: str) -> str:
    """Получить sha256-хэш raw refresh-токена для поиска в БД."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
#  CSRF tokens
# ---------------------------------------------------------------------------

def generate_csrf_token() -> str:
    """Сгенерировать CSRF-токен (double-submit cookie pattern)."""
    return secrets.token_urlsafe(32)

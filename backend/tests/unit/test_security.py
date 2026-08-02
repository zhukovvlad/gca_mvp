"""Unit-тесты для backend/security.py.

Проверяем все криптографические примитивы без БД и без FastAPI.
"""
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from security import (
    create_access_token,
    decode_access_token,
    generate_csrf_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

# ---------------------------------------------------------------------------
#  Хэширование паролей
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_is_not_plain(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"

    def test_verify_correct_password(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("secret123")
        assert verify_password("wrong", hashed) is False

    def test_two_hashes_differ(self):
        """Argon2 использует случайную соль — два хэша одного пароля не совпадают."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


# ---------------------------------------------------------------------------
#  JWT access tokens
# ---------------------------------------------------------------------------

class TestAccessToken:
    def _sample_payload(self) -> dict:
        return {"sub": "42", "org_id": 1, "is_superuser": False, "org_role": "admin"}

    def test_encode_decode_roundtrip(self):
        payload = self._sample_payload()
        token = create_access_token(payload)
        decoded = decode_access_token(token)
        assert decoded["sub"] == "42"
        assert decoded["org_id"] == 1
        assert decoded["type"] == "access"

    def test_expired_token_raises(self, monkeypatch):
        """Подменяем expire minutes на -1 (уже истёк)."""
        import config as cfg_module
        monkeypatch.setattr(cfg_module.settings, "ACCESS_TOKEN_EXPIRE_MINUTES", -1)
        token = create_access_token(self._sample_payload())
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(token)

    def test_invalid_signature_raises(self):
        token = create_access_token(self._sample_payload())
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(tampered)

    def test_wrong_token_type_raises(self):
        """Токен с type=refresh не должен приниматься как access."""
        from config import settings
        payload = {**self._sample_payload(), "type": "refresh", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1), "jti": "x"}
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(token)

    def test_contains_jti(self):
        """Каждый токен содержит уникальный jti."""
        t1 = create_access_token(self._sample_payload())
        t2 = create_access_token(self._sample_payload())
        d1 = decode_access_token(t1)
        d2 = decode_access_token(t2)
        assert d1["jti"] != d2["jti"]


# ---------------------------------------------------------------------------
#  Refresh tokens
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def test_generate_returns_tuple(self):
        raw, hashed = generate_refresh_token()
        assert isinstance(raw, str)
        assert isinstance(hashed, str)
        assert len(hashed) == 64  # sha256 hex = 64 chars

    def test_hash_matches(self):
        raw, hashed = generate_refresh_token()
        assert hash_refresh_token(raw) == hashed

    def test_two_tokens_differ(self):
        raw1, _ = generate_refresh_token()
        raw2, _ = generate_refresh_token()
        assert raw1 != raw2


# ---------------------------------------------------------------------------
#  CSRF tokens
# ---------------------------------------------------------------------------

class TestCsrfToken:
    def test_generates_non_empty_string(self):
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_two_tokens_differ(self):
        assert generate_csrf_token() != generate_csrf_token()

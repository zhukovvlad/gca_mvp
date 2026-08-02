"""Unit-тесты для backend/auth.py dependencies.

Мокируем Request и DB — никаких реальных сетевых вызовов.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    get_current_user,
    require_admin,
    require_csrf,
)
from models import UserRole

# ---------------------------------------------------------------------------
#  Хелперы
# ---------------------------------------------------------------------------

def _make_request(cookies: dict | None = None, headers: dict | None = None, method: str = "POST") -> MagicMock:
    """Создать мок FastAPI Request."""
    req = MagicMock()
    req.cookies = cookies or {}
    req.headers = headers or {}
    req.method = method
    return req


def _make_user(
    user_id: int = 1,
    role: UserRole = UserRole.member,
    is_active: bool = True,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.is_active = is_active
    return user


# ---------------------------------------------------------------------------
#  get_current_user
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    def test_no_cookie_raises_401(self):
        req = _make_request(cookies={})
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req, db)
        assert exc_info.value.status_code == 401

    def test_valid_token_returns_user(self):
        from security import create_access_token
        token = create_access_token({"sub": "1", "role": "admin"})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        mock_user = _make_user()
        db.query.return_value.filter.return_value.first.return_value = mock_user

        result = get_current_user(req, db)
        assert result is mock_user

    def test_expired_token_raises_401(self, monkeypatch):
        import config as cfg_module
        monkeypatch.setattr(cfg_module.settings, "ACCESS_TOKEN_EXPIRE_MINUTES", -1)
        from security import create_access_token
        token = create_access_token({"sub": "1", "role": "member"})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req, db)
        assert exc_info.value.status_code == 401

    def test_inactive_user_raises_401(self):
        from security import create_access_token
        token = create_access_token({"sub": "1", "role": "member"})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        # Пользователь не найден (is_active фильтр убрал)
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req, db)
        assert exc_info.value.status_code == 401

    def test_garbage_sub_raises_401(self):
        from security import create_access_token
        token = create_access_token({"sub": "not-an-int", "role": "member"})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req, db)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
#  require_csrf
# ---------------------------------------------------------------------------

class TestRequireCsrf:
    def test_get_method_skipped(self):
        req = _make_request(method="GET", cookies={}, headers={})
        require_csrf(req)  # не должно выбросить

    def test_missing_cookie_raises_403(self):
        req = _make_request(method="POST", cookies={}, headers={CSRF_HEADER_NAME: "token"})
        with pytest.raises(HTTPException) as exc_info:
            require_csrf(req)
        assert exc_info.value.status_code == 403

    def test_missing_header_raises_403(self):
        req = _make_request(method="POST", cookies={CSRF_COOKIE_NAME: "token"}, headers={})
        with pytest.raises(HTTPException) as exc_info:
            require_csrf(req)
        assert exc_info.value.status_code == 403

    def test_mismatch_raises_403(self):
        req = _make_request(
            method="POST",
            cookies={CSRF_COOKIE_NAME: "abc"},
            headers={CSRF_HEADER_NAME: "xyz"},
        )
        with pytest.raises(HTTPException) as exc_info:
            require_csrf(req)
        assert exc_info.value.status_code == 403

    def test_matching_tokens_pass(self):
        req = _make_request(
            method="POST",
            cookies={CSRF_COOKIE_NAME: "same"},
            headers={CSRF_HEADER_NAME: "same"},
        )
        require_csrf(req)  # не должно выбросить


# ---------------------------------------------------------------------------
#  require_admin
# ---------------------------------------------------------------------------

class TestRequireAdmin:
    def test_passes_for_admin(self):
        user = _make_user(role=UserRole.admin)
        result = require_admin(user)
        assert result is user

    def test_raises_403_for_member(self):
        user = _make_user(role=UserRole.member)
        with pytest.raises(HTTPException) as exc_info:
            require_admin(user)
        assert exc_info.value.status_code == 403

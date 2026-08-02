"""Unit-тесты для backend/auth.py dependencies.

Мокируем Request и DB — никаких реальных сетевых вызовов.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    ProjectAccess,
    get_current_user,
    get_project_access,
    require_csrf,
    require_org_admin,
    require_superuser,
)
from models import OrgRole, ProjectRole

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
    is_superuser: bool = False,
    org_id: int | None = 1,
    org_role: OrgRole | None = OrgRole.admin,
    is_active: bool = True,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.is_superuser = is_superuser
    user.org_id = org_id
    user.org_role = org_role
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
        token = create_access_token({"sub": "1", "org_id": 1, "is_superuser": False, "org_role": "admin"})
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
        token = create_access_token({"sub": "1", "org_id": 1, "is_superuser": False, "org_role": None})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(req, db)
        assert exc_info.value.status_code == 401

    def test_inactive_user_raises_401(self):
        from security import create_access_token
        token = create_access_token({"sub": "1", "org_id": 1, "is_superuser": False, "org_role": None})
        req = _make_request(cookies={"access_token": token})
        db = MagicMock()
        # Пользователь не найден (is_active фильтр убрал)
        db.query.return_value.filter.return_value.first.return_value = None
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
#  require_superuser / require_org_admin
# ---------------------------------------------------------------------------

class TestRoleChecks:
    def test_require_superuser_passes_for_superuser(self):
        user = _make_user(is_superuser=True)
        result = require_superuser(user)
        assert result is user

    def test_require_superuser_raises_403_for_regular(self):
        user = _make_user(is_superuser=False)
        with pytest.raises(HTTPException) as exc_info:
            require_superuser(user)
        assert exc_info.value.status_code == 403

    def test_require_org_admin_passes_for_superuser(self):
        user = _make_user(is_superuser=True, org_role=None)
        result = require_org_admin(user)
        assert result is user

    def test_require_org_admin_passes_for_admin(self):
        user = _make_user(is_superuser=False, org_role=OrgRole.admin)
        result = require_org_admin(user)
        assert result is user

    def test_require_org_admin_raises_for_member(self):
        user = _make_user(is_superuser=False, org_role=OrgRole.member)
        with pytest.raises(HTTPException) as exc_info:
            require_org_admin(user)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
#  ProjectAccess
# ---------------------------------------------------------------------------

class TestProjectAccess:
    def test_superuser_is_customer(self):
        access = ProjectAccess(MagicMock(), None, True, _make_user(is_superuser=True))
        assert access.is_customer is True
        assert access.is_contractor is False

    def test_customer_role(self):
        access = ProjectAccess(MagicMock(), ProjectRole.customer, False, _make_user())
        assert access.is_customer is True
        assert access.is_contractor is False

    def test_contractor_role(self):
        access = ProjectAccess(MagicMock(), ProjectRole.contractor, False, _make_user())
        assert access.is_customer is False
        assert access.is_contractor is True


# ---------------------------------------------------------------------------
#  get_project_access
# ---------------------------------------------------------------------------

class TestGetProjectAccess:
    def test_nonexistent_project_raises_404(self):
        user = _make_user(is_superuser=False)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            get_project_access(99, user, db)
        assert exc_info.value.status_code == 404

    def test_superuser_bypasses_org_check(self):
        user = _make_user(is_superuser=True)
        mock_project = MagicMock()
        mock_project.id = 1
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_project
        access = get_project_access(1, user, db)
        assert access.is_superuser is True
        assert access.project is mock_project

    def test_user_without_org_link_raises_404(self):
        user = _make_user(is_superuser=False, org_id=1)
        mock_project = MagicMock()
        db = MagicMock()
        # query(Project) → проект найден; query(ProjectOrganization) → нет линка
        db.query.return_value.filter.return_value.first.side_effect = [mock_project, None]
        with pytest.raises(HTTPException) as exc_info:
            get_project_access(1, user, db)
        assert exc_info.value.status_code == 404

"""Интеграционные тесты админ-консоли (single-tenant, AGENTS.md §3).

Покрывает:
- доступ к /api/admin/* запрещён member (403);
- создание пользователя, конфликт email (409);
- защита последнего активного admin;
- reset-password меняет хэш и логин работает с новым паролем;
- пагинация и поиск GET /api/admin/users.

`client` fixture мокает get_current_user как admin → /api/admin/* проходит
require_admin. Для проверки 403 get_current_user переопределяется на
реального member через _login_as.
"""
from contextlib import contextmanager

from auth import get_current_user
from main import app
from models import User, UserRole
from security import hash_password, verify_password


@contextmanager
def _login_as(user: User):
    """Временно переопределить get_current_user реальным пользователем.

    Сохраняет и восстанавливает предыдущий override (мок-admin из client
    fixture), чтобы запросы после выхода из контекста снова шли от admin.
    """
    prev = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = prev


# ---------------------------------------------------------------------------
#  Доступ: только admin
# ---------------------------------------------------------------------------

def test_admin_endpoints_forbidden_for_member(client, factories):
    """member получает 403 на /api/admin/*."""
    member = factories.UserFactory.create(role=UserRole.member)

    with _login_as(member):
        assert client.get("/api/admin/users").status_code == 403
        assert client.post(
            "/api/admin/users",
            json={"email": "x@example.com", "password": "pw12345678"},
        ).status_code == 403
        assert client.patch(f"/api/admin/users/{member.id}", json={"is_active": False}).status_code == 403


# ---------------------------------------------------------------------------
#  Создание пользователей
# ---------------------------------------------------------------------------

def test_create_user_defaults_to_member(client):
    response = client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "password": "pw12345678"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "member"
    assert body["is_active"] is True


def test_create_user_with_admin_role(client):
    response = client.post(
        "/api/admin/users",
        json={"email": "boss@example.com", "password": "pw12345678", "role": "admin"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_create_user_duplicate_email_409(client, factories):
    factories.UserFactory.create(email="dup@example.com")
    response = client.post(
        "/api/admin/users",
        json={"email": "dup@example.com", "password": "pw12345678"},
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
#  Смена роли/статуса, защита последнего admin
# ---------------------------------------------------------------------------

def test_promote_member_to_admin(client, factories):
    member = factories.UserFactory.create(role=UserRole.member)
    response = client.patch(f"/api/admin/users/{member.id}", json={"role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_cannot_demote_last_admin(client, factories):
    admin = factories.UserFactory.create(role=UserRole.admin)
    response = client.patch(f"/api/admin/users/{admin.id}", json={"role": "member"})
    assert response.status_code == 409


def test_cannot_deactivate_last_admin(client, factories):
    admin = factories.UserFactory.create(role=UserRole.admin)
    response = client.patch(f"/api/admin/users/{admin.id}", json={"is_active": False})
    assert response.status_code == 409


def test_can_demote_admin_when_another_exists(client, factories):
    factories.UserFactory.create(role=UserRole.admin)
    second = factories.UserFactory.create(role=UserRole.admin)
    response = client.patch(f"/api/admin/users/{second.id}", json={"role": "member"})
    assert response.status_code == 200
    assert response.json()["role"] == "member"


def test_patch_unknown_user_404(client):
    response = client.patch("/api/admin/users/999999", json={"is_active": False})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
#  reset-password
# ---------------------------------------------------------------------------

def test_reset_password_changes_hash_and_returns_plaintext(client, factories, db_session):
    user = factories.UserFactory.create(password_hash=hash_password("oldpass"))
    old_hash = user.password_hash

    response = client.post(f"/api/admin/users/{user.id}/reset-password")
    assert response.status_code == 200
    new_password = response.json()["password"]
    assert new_password

    db_session.refresh(user)
    assert user.password_hash != old_hash
    assert verify_password(new_password, user.password_hash)


# ---------------------------------------------------------------------------
#  Пагинация и поиск GET /api/admin/users
# ---------------------------------------------------------------------------

def test_list_users_pagination(client, factories):
    for i in range(5):
        factories.UserFactory.create(email=f"page{i}@example.com")

    response = client.get("/api/admin/users", params={"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 5


def test_list_users_search_by_email(client, factories):
    factories.UserFactory.create(email="findme@example.com")
    response = client.get("/api/admin/users", params={"q": "findme"})
    assert response.status_code == 200
    body = response.json()
    assert any(u["email"] == "findme@example.com" for u in body["items"])

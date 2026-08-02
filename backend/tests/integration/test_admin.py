"""Интеграционные тесты админ-консоли суперпользователя и org-самообслуживания.

Покрывает:
- доступ к /api/admin/* запрещён не-суперюзерам (403);
- создание организации с kind; привязка к проекту наследует project_role из kind
  и переопределяется явным значением;
- матрица прав org-самообслуживания (admin не трогает admin/superadmin; superadmin может);
- защита последнего активного superadmin;
- reset-password меняет хэш и логин работает с новым паролем;
- пагинация GET /api/admin/users.

`client` fixture мокает get_current_user как платформенного суперюзера → /api/admin/*
проходит require_superuser. Для матрицы org-самообслуживания get_current_user
переопределяется на реального org-пользователя через _login_as.
"""
from contextlib import contextmanager

from auth import get_current_user
from main import app
from models import OrgRole, ProjectRole, User
from security import hash_password, verify_password


@contextmanager
def _login_as(user: User):
    """Временно переопределить get_current_user реальным пользователем (для /api/orgs/*).

    Сохраняет и восстанавливает предыдущий override (мок-суперюзер из client
    fixture), чтобы запросы после выхода из контекста снова шли от суперюзера.
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
#  Доступ: только суперюзер
# ---------------------------------------------------------------------------

def test_admin_endpoints_forbidden_for_non_superuser(client, factories):
    """Не-суперюзер (org admin) получает 403 на /api/admin/*."""
    org = factories.OrganizationFactory.create()
    admin_user = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)

    with _login_as(admin_user):
        assert client.get("/api/admin/organizations").status_code == 403
        assert client.get("/api/admin/users").status_code == 403
        assert client.post("/api/admin/organizations", json={"name": "X"}).status_code == 403


# ---------------------------------------------------------------------------
#  Организации с kind
# ---------------------------------------------------------------------------

def test_create_organization_with_kind(client):
    response = client.post("/api/admin/organizations", json={"name": "ООО СтройГрад", "inn": "7705123456", "kind": "contractor"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "ООО СтройГрад"
    assert body["kind"] == "contractor"


def test_create_organization_defaults_to_customer(client):
    response = client.post("/api/admin/organizations", json={"name": "ООО Заказчик"})
    assert response.status_code == 201
    assert response.json()["kind"] == "customer"


def test_get_organization_detail(client, factories):
    org = factories.OrganizationFactory.create(kind=ProjectRole.contractor)
    factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    project = factories.ProjectFactory.create()

    # привяжем проект
    client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})

    response = client.get(f"/api/admin/organizations/{org.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "contractor"
    assert len(body["users"]) == 1
    assert len(body["projects"]) == 1
    assert body["projects"][0]["project_id"] == project.id
    # project_role наследуется из kind организации (contractor)
    assert body["projects"][0]["project_role"] == "contractor"


def test_update_organization(client, factories):
    org = factories.OrganizationFactory.create(kind=ProjectRole.customer)
    response = client.patch(f"/api/admin/organizations/{org.id}", json={"name": "Новое имя", "kind": "contractor"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Новое имя"
    assert body["kind"] == "contractor"


# ---------------------------------------------------------------------------
#  Первый пользователь = superadmin
# ---------------------------------------------------------------------------

def test_first_user_in_org_becomes_superadmin(client, factories):
    org = factories.OrganizationFactory.create()
    response = client.post(
        f"/api/admin/organizations/{org.id}/users",
        json={"email": "first@example.com", "password": "pw12345678", "org_role": "member"},
    )
    assert response.status_code == 201
    # несмотря на org_role=member — первый становится superadmin
    assert response.json()["org_role"] == "superadmin"


def test_second_user_keeps_requested_role(client, factories):
    org = factories.OrganizationFactory.create()
    factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    response = client.post(
        f"/api/admin/organizations/{org.id}/users",
        json={"email": "second@example.com", "password": "pw12345678", "org_role": "admin"},
    )
    assert response.status_code == 201
    assert response.json()["org_role"] == "admin"


# ---------------------------------------------------------------------------
#  Привязка к проекту: наследование project_role из kind
# ---------------------------------------------------------------------------

def test_link_project_inherits_role_from_kind(client, factories):
    org = factories.OrganizationFactory.create(kind=ProjectRole.contractor)
    project = factories.ProjectFactory.create()
    response = client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})
    assert response.status_code == 201
    assert response.json()["project_role"] == "contractor"


def test_link_project_explicit_role_overrides_kind(client, factories):
    org = factories.OrganizationFactory.create(kind=ProjectRole.contractor)
    project = factories.ProjectFactory.create()
    response = client.post(
        f"/api/admin/organizations/{org.id}/projects",
        json={"project_id": project.id, "project_role": "customer"},
    )
    assert response.status_code == 201
    assert response.json()["project_role"] == "customer"


def test_link_project_duplicate_returns_409(client, factories):
    org = factories.OrganizationFactory.create()
    project = factories.ProjectFactory.create()
    client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})
    response = client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})
    assert response.status_code == 409


def test_unlink_project(client, factories):
    org = factories.OrganizationFactory.create()
    project = factories.ProjectFactory.create()
    client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})
    response = client.delete(f"/api/admin/organizations/{org.id}/projects/{project.id}")
    assert response.status_code == 204
    # idempotent: повторный DELETE не падает
    assert client.delete(f"/api/admin/organizations/{org.id}/projects/{project.id}").status_code == 204


# ---------------------------------------------------------------------------
#  Защита последнего superadmin (через /api/admin)
# ---------------------------------------------------------------------------

def test_cannot_demote_last_superadmin(client, factories):
    org = factories.OrganizationFactory.create()
    superadmin = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    response = client.patch(f"/api/admin/users/{superadmin.id}", json={"org_role": "admin"})
    assert response.status_code == 409


def test_cannot_deactivate_last_superadmin(client, factories):
    org = factories.OrganizationFactory.create()
    superadmin = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    response = client.patch(f"/api/admin/users/{superadmin.id}", json={"is_active": False})
    assert response.status_code == 409


def test_can_demote_superadmin_when_another_exists(client, factories):
    org = factories.OrganizationFactory.create()
    factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    second = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    response = client.patch(f"/api/admin/users/{second.id}", json={"org_role": "admin"})
    assert response.status_code == 200
    assert response.json()["org_role"] == "admin"


# ---------------------------------------------------------------------------
#  reset-password
# ---------------------------------------------------------------------------

def test_reset_password_changes_hash_and_returns_plaintext(client, factories, db_session):
    org = factories.OrganizationFactory.create()
    user = factories.UserFactory.create(organization=org, password_hash=hash_password("oldpass"))
    old_hash = user.password_hash

    response = client.post(f"/api/admin/users/{user.id}/reset-password")
    assert response.status_code == 200
    new_password = response.json()["password"]
    assert new_password

    db_session.refresh(user)
    assert user.password_hash != old_hash
    assert verify_password(new_password, user.password_hash)


# ---------------------------------------------------------------------------
#  Пагинация GET /api/admin/users
# ---------------------------------------------------------------------------

def test_list_users_pagination(client, factories):
    org = factories.OrganizationFactory.create(name="Орг Пагинации")
    for i in range(5):
        factories.UserFactory.create(organization=org, email=f"page{i}@example.com")

    response = client.get("/api/admin/users", params={"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 5
    # org_name присутствует
    assert "org_name" in body["items"][0]


def test_list_users_search_by_email(client, factories):
    org = factories.OrganizationFactory.create()
    factories.UserFactory.create(organization=org, email="findme@example.com")
    response = client.get("/api/admin/users", params={"q": "findme"})
    assert response.status_code == 200
    body = response.json()
    assert any(u["email"] == "findme@example.com" for u in body["items"])


# ---------------------------------------------------------------------------
#  Матрица прав org-самообслуживания (/api/orgs/*)
# ---------------------------------------------------------------------------

def test_org_admin_cannot_create_admin(client, factories):
    """admin может создавать только member, не admin."""
    org = factories.OrganizationFactory.create()
    admin_user = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)
    with _login_as(admin_user):
        response = client.post(
            "/api/orgs/users",
            json={"email": "new@example.com", "password": "pw12345678", "org_role": "admin"},
        )
        assert response.status_code == 403


def test_org_admin_can_create_member(client, factories):
    org = factories.OrganizationFactory.create()
    admin_user = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)
    with _login_as(admin_user):
        response = client.post(
            "/api/orgs/users",
            json={"email": "member@example.com", "password": "pw12345678", "org_role": "member"},
        )
        assert response.status_code == 201
        assert response.json()["org_role"] == "member"


def test_org_superadmin_can_create_admin(client, factories):
    org = factories.OrganizationFactory.create()
    superadmin = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    with _login_as(superadmin):
        response = client.post(
            "/api/orgs/users",
            json={"email": "newadmin@example.com", "password": "pw12345678", "org_role": "admin"},
        )
        assert response.status_code == 201
        assert response.json()["org_role"] == "admin"


def test_org_nobody_can_create_superadmin(client, factories):
    """superadmin-роль не назначается через self-service даже org-superadmin'ом."""
    org = factories.OrganizationFactory.create()
    superadmin = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    with _login_as(superadmin):
        response = client.post(
            "/api/orgs/users",
            json={"email": "x@example.com", "password": "pw12345678", "org_role": "superadmin"},
        )
        assert response.status_code == 403


def test_org_admin_cannot_manage_admin(client, factories):
    """admin не может менять роль/статус другого admin."""
    org = factories.OrganizationFactory.create()
    admin_user = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)
    other_admin = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)
    with _login_as(admin_user):
        response = client.patch(f"/api/orgs/users/{other_admin.id}", json={"is_active": False})
        assert response.status_code == 403


def test_org_superadmin_can_manage_admin(client, factories):
    org = factories.OrganizationFactory.create()
    superadmin = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    admin_user = factories.UserFactory.create(organization=org, org_role=OrgRole.admin)
    with _login_as(superadmin):
        response = client.patch(f"/api/orgs/users/{admin_user.id}", json={"is_active": False})
        assert response.status_code == 200
        assert response.json()["is_active"] is False


def test_org_admin_cannot_manage_other_org_user(client, factories):
    """Нельзя трогать пользователя чужой организации (404)."""
    org_a = factories.OrganizationFactory.create()
    org_b = factories.OrganizationFactory.create()
    admin_a = factories.UserFactory.create(organization=org_a, org_role=OrgRole.superadmin)
    user_b = factories.UserFactory.create(organization=org_b, org_role=OrgRole.member)
    with _login_as(admin_a):
        response = client.patch(f"/api/orgs/users/{user_b.id}", json={"is_active": False})
        assert response.status_code == 404


def test_org_superadmin_cannot_manage_peer_superadmin(client, factories):
    """superadmin (org) не может управлять другим superadmin через self-service — только /api/admin."""
    org = factories.OrganizationFactory.create()
    actor = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    peer = factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    with _login_as(actor):
        response = client.patch(f"/api/orgs/users/{peer.id}", json={"is_active": False})
        assert response.status_code == 403


# ---------------------------------------------------------------------------
#  Совместимость контрактов admin-эндпоинтов
# ---------------------------------------------------------------------------

def test_patch_user_response_includes_is_superuser(client, factories):
    """PATCH /api/admin/users/{id} возвращает is_superuser (фронт на него рассчитывает)."""
    org = factories.OrganizationFactory.create()
    factories.UserFactory.create(organization=org, org_role=OrgRole.superadmin)
    member = factories.UserFactory.create(organization=org, org_role=OrgRole.member)
    response = client.patch(f"/api/admin/users/{member.id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_superuser"] is False


def test_link_project_response_includes_project_name(client, factories):
    """POST .../projects возвращает project_name (формат OrgProjectLink)."""
    org = factories.OrganizationFactory.create()
    project = factories.ProjectFactory.create()
    response = client.post(f"/api/admin/organizations/{org.id}/projects", json={"project_id": project.id})
    assert response.status_code == 201
    body = response.json()
    assert body["project_name"] == project.name
    assert body["project_id"] == project.id

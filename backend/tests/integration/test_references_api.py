"""CRUD справочников: классы объектов, объекты, подрядчики (фаза 5, §7).

Проверяется то, что решено фазой 5, а не то, что и так гарантирует схема:

* право `admin` на изменение и его отсутствие у `member` (решение §6.2);
* уникальность даёт 409 с человеческим текстом, а не 500;
* удаление отказывается, когда на запись ссылаются, и разрешено, когда нет;
* `ON DELETE SET NULL` у `objects.rate_class_id` — не отказ, а сброс дефолта.
"""
from __future__ import annotations

import pytest

from models import UserRole


@pytest.fixture
def member(client):
    """Переключает текущего пользователя на роль `member` на время теста."""
    client.auth_state["role"] = UserRole.member
    yield client
    client.auth_state["role"] = UserRole.admin


# ---------------------------------------------------------------------------
#  Классы объектов
# ---------------------------------------------------------------------------

def test_create_and_list_rate_class(client):
    response = client.post(
        "/api/v1/rate-classes", json={"title": "Жилые дома", "description": "Класс А"}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["title"] == "Жилые дома"
    assert created["contracts_count"] == 0
    assert created["standards_count"] == 0

    listed = client.get("/api/v1/rate-classes").json()
    assert [rc["title"] for rc in listed] == ["Жилые дома"]


def test_rate_class_duplicate_title_gives_409(client):
    client.post("/api/v1/rate-classes", json={"title": "Жилые дома"})
    response = client.post("/api/v1/rate-classes", json={"title": "Жилые дома"})
    assert response.status_code == 409
    assert "уже есть" in response.json()["detail"]


def test_rate_class_blank_title_gives_422(client):
    response = client.post("/api/v1/rate-classes", json={"title": "   "})
    assert response.status_code == 422
    assert "не может быть пустым" in response.json()["detail"]


def test_rate_class_counters_reflect_usage(client, factories):
    contract = factories.ContractFactory.create()
    listed = client.get("/api/v1/rate-classes").json()
    used = next(rc for rc in listed if rc["id"] == contract.rate_class_id)
    assert used["contracts_count"] == 1
    assert used["objects_count"] == 0


def test_delete_rate_class_refused_while_contract_references_it(client, factories):
    contract = factories.ContractFactory.create()
    response = client.delete(f"/api/v1/rate-classes/{contract.rate_class_id}")
    assert response.status_code == 409
    assert "снимок" in response.json()["detail"]


def test_delete_rate_class_refused_while_standard_references_it(client, factories):
    standard = factories.RateStandardFactory.create()
    response = client.delete(f"/api/v1/rate-classes/{standard.rate_class_id}")
    assert response.status_code == 409
    assert "нормативы (1)" in response.json()["detail"]


def test_delete_unused_rate_class_succeeds(client, factories):
    rate_class = factories.RateClassFactory.create()
    assert client.delete(f"/api/v1/rate-classes/{rate_class.id}").status_code == 204
    assert client.delete(f"/api/v1/rate-classes/{rate_class.id}").status_code == 404


def test_delete_rate_class_used_only_as_object_default_clears_the_default(client, factories):
    """`objects.rate_class_id` — ON DELETE SET NULL по схеме (§4).

    Класс объекта — лишь значение по умолчанию для новых договоров, поэтому
    удаление такого класса не запрещается: объект теряет дефолт, а не историю.
    Авторитетен снимок в `contracts.rate_class_id`, а его тут нет.
    """
    rate_class = factories.RateClassFactory.create()
    obj = factories.ObjectFactory.create(rate_class=rate_class)

    assert client.delete(f"/api/v1/rate-classes/{rate_class.id}").status_code == 204

    refreshed = client.get(f"/api/v1/objects/{obj.id}").json()
    assert refreshed["rate_class_id"] is None
    assert refreshed["rate_class_title"] is None


def test_update_rate_class_patches_only_passed_fields(client, factories):
    rate_class = factories.RateClassFactory.create(description="исходное")
    response = client.patch(
        f"/api/v1/rate-classes/{rate_class.id}", json={"title": "Переименован"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Переименован"
    assert body["description"] == "исходное"


def test_update_rate_class_null_title_gives_422(client, factories):
    rate_class = factories.RateClassFactory.create()
    response = client.patch(f"/api/v1/rate-classes/{rate_class.id}", json={"title": None})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
#  Объекты
# ---------------------------------------------------------------------------

def test_create_object_with_rate_class(client, factories):
    rate_class = factories.RateClassFactory.create(title="Класс П")
    response = client.post(
        "/api/v1/objects",
        json={"title": "ЖК Северный", "address": "ул. Полярная, 1", "rate_class_id": rate_class.id},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["rate_class_title"] == "Класс П"
    assert body["contracts_count"] == 0


def test_create_object_with_unknown_rate_class_gives_404(client):
    response = client.post("/api/v1/objects", json={"title": "ЖК", "rate_class_id": 999_999})
    assert response.status_code == 404


def test_create_object_without_address_stores_empty_string(client):
    """`address` NOT NULL без дефолта; объект нужен уже сейчас, адрес — позже."""
    response = client.post("/api/v1/objects", json={"title": "ЖК Без адреса"})
    assert response.status_code == 201
    assert response.json()["address"] == ""


def test_objects_list_filters_by_title_and_address(client, factories):
    factories.ObjectFactory.create(title="ЖК Северный", address="ул. Полярная")
    factories.ObjectFactory.create(title="ЖК Южный", address="ул. Солнечная")

    by_title = client.get("/api/v1/objects", params={"q": "Северный"}).json()
    assert by_title["total"] == 1
    assert by_title["items"][0]["title"] == "ЖК Северный"

    by_address = client.get("/api/v1/objects", params={"q": "Солнечная"}).json()
    assert by_address["total"] == 1
    assert by_address["items"][0]["title"] == "ЖК Южный"


def test_objects_list_total_counts_all_matches_not_just_page(client, factories):
    for n in range(5):
        factories.ObjectFactory.create(title=f"Объект пагинации {n}")

    page = client.get(
        "/api/v1/objects", params={"q": "пагинации", "page": 1, "page_size": 2}
    ).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2


def test_delete_object_refused_while_contract_references_it(client, factories):
    contract = factories.ContractFactory.create()
    response = client.delete(f"/api/v1/objects/{contract.object_id}")
    assert response.status_code == 409
    assert "договоры (1)" in response.json()["detail"]


# ---------------------------------------------------------------------------
#  Подрядчики
# ---------------------------------------------------------------------------

def test_create_contractor_and_duplicate_inn_gives_409(client):
    payload = {"title": "ООО Строй", "inn": "123456789012"}
    assert client.post("/api/v1/contractors", json=payload).status_code == 201

    duplicate = client.post("/api/v1/contractors", json={"title": "Другое имя", "inn": "123456789012"})
    assert duplicate.status_code == 409
    assert "БИН/ИНН" in duplicate.json()["detail"]


def test_contractors_list_filters_by_inn(client, factories):
    factories.ContractorFactory.create(title="ООО Первый", inn="770000000001")
    factories.ContractorFactory.create(title="ООО Второй", inn="770000000002")

    found = client.get("/api/v1/contractors", params={"q": "0002"}).json()
    assert found["total"] == 1
    assert found["items"][0]["title"] == "ООО Второй"


def test_delete_contractor_refused_while_proposal_references_it(client, factories):
    """Подрядчик остаётся в импортированных сметах даже без договора.

    `proposals.contractor_id` — FK без каскада, поэтому проверяются обе ссылки:
    договоры и предложения.
    """
    proposal = factories.ProposalFactory.create()
    response = client.delete(f"/api/v1/contractors/{proposal.contractor_id}")
    assert response.status_code == 409
    assert "предложений в сметах (1)" in response.json()["detail"]


def test_delete_unused_contractor_succeeds(client, factories):
    contractor = factories.ContractorFactory.create()
    assert client.delete(f"/api/v1/contractors/{contractor.id}").status_code == 204


# ---------------------------------------------------------------------------
#  Права (решение §6.2): изменение — только admin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/rate-classes", {"title": "Класс"}),
        ("post", "/api/v1/objects", {"title": "Объект"}),
        ("post", "/api/v1/contractors", {"title": "Подрядчик", "inn": "1"}),
        ("patch", "/api/v1/rate-classes/1", {"title": "Класс"}),
        ("patch", "/api/v1/objects/1", {"title": "Объект"}),
        ("patch", "/api/v1/contractors/1", {"title": "Подрядчик"}),
        ("delete", "/api/v1/rate-classes/1", None),
        ("delete", "/api/v1/objects/1", None),
        ("delete", "/api/v1/contractors/1", None),
    ],
)
def test_member_cannot_change_references(member, method, path, payload):
    """`member` читает, но не заводит и не правит карточки (§6.2).

    403 приходит до обращения к БД, поэтому существование записи с id=1 роли не
    играет: проверка роли — это зависимость `require_admin`.
    """
    response = getattr(member, method)(path, **({"json": payload} if payload else {}))
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin required"


@pytest.mark.parametrize(
    "path", ["/api/v1/rate-classes", "/api/v1/objects", "/api/v1/contractors"]
)
def test_member_can_read_references(member, path):
    assert member.get(path).status_code == 200

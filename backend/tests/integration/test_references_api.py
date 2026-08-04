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


# ---------------------------------------------------------------------------
#  Счётчики использования: подзапросы обязаны быть коррелированными
# ---------------------------------------------------------------------------

def test_object_counters_are_per_row_not_global(client, factories):
    """Счётчик договоров считается ДЛЯ КАЖДОГО объекта отдельно.

    Прежние тесты этого не доказывали: они проверяли счётчики там, где
    коррелированный и некоррелированный подзапрос дают одно и то же (ноль при
    отсутствии договоров вовсе). Потеряй подзапрос корреляцию — каждый объект
    показывал бы ОБЩЕЕ число договоров в базе, и заметить это было бы нечем.
    """
    with_contract = factories.ObjectFactory.create(title="Объект с договором")
    without_contract = factories.ObjectFactory.create(title="Объект без договора")
    factories.ContractFactory.create(object=with_contract)
    factories.ContractFactory.create(object=with_contract)

    items = {item["title"]: item for item in client.get("/api/v1/objects").json()["items"]}
    assert items["Объект с договором"]["contracts_count"] == 2
    assert items[without_contract.title]["contracts_count"] == 0


def test_contractor_counters_are_per_row_not_global(client, factories):
    busy = factories.ContractorFactory.create(title="Подрядчик занятый")
    idle = factories.ContractorFactory.create(title="Подрядчик свободный")
    factories.ContractFactory.create(contractor=busy)

    items = {item["title"]: item for item in client.get("/api/v1/contractors").json()["items"]}
    assert items["Подрядчик занятый"]["contracts_count"] == 1
    assert items[idle.title]["contracts_count"] == 0


def test_rate_class_counters_are_per_row_not_global(client, factories):
    """Три счётчика класса — договоры, объекты, нормативы — каждый по своей строке."""
    used = factories.RateClassFactory.create(title="Класс используемый")
    unused = factories.RateClassFactory.create(title="Класс свободный")
    factories.ObjectFactory.create(rate_class=used)
    factories.ContractFactory.create(rate_class=used)
    factories.RateStandardFactory.create(rate_class=used)

    items = {item["title"]: item for item in client.get("/api/v1/rate-classes").json()}
    assert items["Класс используемый"]["contracts_count"] == 1
    assert items["Класс используемый"]["objects_count"] == 1
    assert items["Класс используемый"]["standards_count"] == 1
    assert items[unused.title]["contracts_count"] == 0
    assert items[unused.title]["objects_count"] == 0
    assert items[unused.title]["standards_count"] == 0


def test_rejected_object_patch_leaves_nothing_behind(client):
    """Отвергнутая правка не должна быть видна даже в той же сессии.

    Найдено собственным ревью. Поля применяются по одному, а `_resolve_rate_class`
    отвергает неизвестный класс уже после присваивания названия — без явного
    отката объект оставался «грязным», и следующее чтение в этой же сессии
    показывало отвергнутое название. В проде это не приводило к записи (сессия
    живёт один запрос), но корректность держалась на времени её жизни, а не на коде.

    Объект создаётся **через API**, а не фабрикой: фабрика не коммитит, а откат в
    транзакционной фикстуре снял бы вместе с правкой и её данные — тест перестал
    бы проверять то, ради чего написан.
    """
    object_id = client.post("/api/v1/objects", json={"title": "Название исходное"}).json()["id"]

    response = client.patch(
        f"/api/v1/objects/{object_id}",
        json={"title": "Название отвергнутое", "rate_class_id": 999_999},
    )
    assert response.status_code == 404

    assert client.get(f"/api/v1/objects/{object_id}").json()["title"] == "Название исходное"


def test_rejected_contractor_patch_leaves_nothing_behind(client):
    """То же для подрядчика: пустой БИН/ИНН отвергается после присваивания названия."""
    contractor_id = client.post(
        "/api/v1/contractors", json={"title": "Подрядчик исходный", "inn": "111000111000"}
    ).json()["id"]

    response = client.patch(
        f"/api/v1/contractors/{contractor_id}",
        json={"title": "Подрядчик отвергнутый", "inn": "   "},
    )
    assert response.status_code == 422

    body = client.get(f"/api/v1/contractors/{contractor_id}").json()
    assert body["title"] == "Подрядчик исходный"
    assert body["inn"] == "111000111000"

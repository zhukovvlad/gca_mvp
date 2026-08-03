"""CRUD договоров и история загрузок (фаза 5, §7.1).

Проверяется смысл, а не форма JSON:

* класс договора — снимок: не передан → берётся у объекта; нет ни там, ни там → 422;
* деньги приходят строкой и уезжают строкой, Decimal сравнивается с Decimal (§3);
* `is_current` в истории отличает актуальную смету от вытесненной заменой (§5);
* договор с историей импорта неудаляем — задания импорта аудит (§5, правило 3);
* право `admin` на изменение (решение §6.2).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from models import Contract, ImportJobStatus, UserRole


@pytest.fixture
def member(client):
    client.auth_state["role"] = UserRole.member
    yield client
    client.auth_state["role"] = UserRole.admin


def _payload(obj_id: int, contractor_id: int, **extra) -> dict:
    body = {
        "object_id": obj_id,
        "contractor_id": contractor_id,
        "contract_number": "ГП-2026-001",
        "signed_date": "2026-03-01",
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
#  Создание и снимок класса (§4)
# ---------------------------------------------------------------------------

def test_create_contract_takes_rate_class_from_object_by_default(client, factories):
    """`objects.rate_class_id` — «значение по умолчанию для новых договоров» (§4)."""
    rate_class = factories.RateClassFactory.create(title="Класс объекта")
    obj = factories.ObjectFactory.create(rate_class=rate_class)
    contractor = factories.ContractorFactory.create()

    response = client.post("/api/v1/contracts", json=_payload(obj.id, contractor.id))
    assert response.status_code == 201
    body = response.json()
    assert body["rate_class_id"] == rate_class.id
    assert body["rate_class_title"] == "Класс объекта"


def test_create_contract_explicit_rate_class_overrides_object_default(client, factories):
    object_class = factories.RateClassFactory.create(title="Дефолт объекта")
    explicit = factories.RateClassFactory.create(title="Класс договора")
    obj = factories.ObjectFactory.create(rate_class=object_class)
    contractor = factories.ContractorFactory.create()

    body = client.post(
        "/api/v1/contracts",
        json=_payload(obj.id, contractor.id, rate_class_id=explicit.id),
    ).json()
    assert body["rate_class_title"] == "Класс договора"


def test_create_contract_without_any_rate_class_gives_422(client, factories):
    """Колонка NOT NULL, и подставить NULL нельзя — нужен внятный отказ, не 500."""
    obj = factories.ObjectFactory.create(rate_class=None)
    contractor = factories.ContractorFactory.create()

    response = client.post("/api/v1/contracts", json=_payload(obj.id, contractor.id))
    assert response.status_code == 422
    assert "класс договора обязателен" in response.json()["detail"]


def test_reclassifying_object_does_not_change_existing_contract(client, factories):
    """Снимок класса защищает прошлое от переклассификации объекта (§4)."""
    old_class = factories.RateClassFactory.create(title="Старый класс")
    new_class = factories.RateClassFactory.create(title="Новый класс")
    obj = factories.ObjectFactory.create(rate_class=old_class)
    contractor = factories.ContractorFactory.create()

    contract_id = client.post(
        "/api/v1/contracts", json=_payload(obj.id, contractor.id)
    ).json()["id"]

    assert client.patch(
        f"/api/v1/objects/{obj.id}", json={"rate_class_id": new_class.id}
    ).status_code == 200

    unchanged = client.get(f"/api/v1/contracts/{contract_id}").json()
    assert unchanged["rate_class_id"] == old_class.id
    assert unchanged["rate_class_title"] == "Старый класс"


def test_create_contract_unknown_object_gives_404(client, factories):
    contractor = factories.ContractorFactory.create()
    response = client.post("/api/v1/contracts", json=_payload(999_999, contractor.id))
    assert response.status_code == 404
    assert "Объект" in response.json()["detail"]


def test_duplicate_contract_number_gives_409(client, factories):
    # rate_class_id передаётся явно: у объекта из ContractFactory своего класса
    # нет, и без него отказ пришёл бы раньше — 422 про обязательный класс.
    contract = factories.ContractFactory.create(contract_number="ГП-ДУБЛЬ")
    response = client.post(
        "/api/v1/contracts",
        json=_payload(
            contract.object_id,
            contract.contractor_id,
            contract_number="ГП-ДУБЛЬ",
            rate_class_id=contract.rate_class_id,
        ),
    )
    assert response.status_code == 409
    assert "таким номером уже есть" in response.json()["detail"]


# ---------------------------------------------------------------------------
#  Деньги (§3): Decimal end-to-end, в JSON — строки
# ---------------------------------------------------------------------------

def test_total_amount_round_trips_as_string_and_stays_exact(client, factories, db_session):
    """Деньги уезжают из API **строкой**, а в БД лежат точным Decimal (§3).

    Строка в JSON — не косметика: `jsonable_encoder` FastAPI отдал бы `float`, и
    `1234567890.12` доехало бы до фронта как двоичная дробь. Именно этот тест
    ловит регрессию, если эндпоинт перестанет возвращать `decimal_json`
    (см. `responses.py`).
    """
    obj = factories.ObjectFactory.create(rate_class=factories.RateClassFactory.create())
    contractor = factories.ContractorFactory.create()

    response = client.post(
        "/api/v1/contracts",
        json=_payload(obj.id, contractor.id, total_amount="1234567890.12"),
    )
    body = response.json()
    assert body["total_amount"] == "1234567890.12"
    # Не только значение, но и тип в самом JSON: число здесь было бы дефектом.
    assert '"total_amount":"1234567890.12"' in response.text

    stored = db_session.get(Contract, body["id"])
    assert stored.total_amount == Decimal("1234567890.12")


def test_total_amount_is_a_string_in_list_and_card_too(client, factories):
    """`decimal_json` нужен на каждом эндпоинте с деньгами, не только на create."""
    obj = factories.ObjectFactory.create(rate_class=factories.RateClassFactory.create())
    contractor = factories.ContractorFactory.create()
    contract_id = client.post(
        "/api/v1/contracts", json=_payload(obj.id, contractor.id, total_amount="10.05")
    ).json()["id"]

    card = client.get(f"/api/v1/contracts/{contract_id}")
    assert '"total_amount":"10.05"' in card.text

    listed = client.get("/api/v1/contracts")
    assert '"total_amount":"10.05"' in listed.text

    patched = client.patch(f"/api/v1/contracts/{contract_id}", json={"total_amount": "20.10"})
    assert '"total_amount":"20.10"' in patched.text


def test_total_amount_as_float_is_rejected(client, factories):
    """§3: «никаких float». Молчаливая конверсия скрыла бы потерю копеек."""
    obj = factories.ObjectFactory.create(rate_class=factories.RateClassFactory.create())
    contractor = factories.ContractorFactory.create()

    response = client.post(
        "/api/v1/contracts",
        json=_payload(obj.id, contractor.id, total_amount=1234.56),
    )
    assert response.status_code == 422
    assert "float" in response.text


def test_negative_total_amount_gives_422_not_integrity_error(client, factories):
    obj = factories.ObjectFactory.create(rate_class=factories.RateClassFactory.create())
    contractor = factories.ContractorFactory.create()

    response = client.post(
        "/api/v1/contracts", json=_payload(obj.id, contractor.id, total_amount="-1")
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
#  Список и фильтры
# ---------------------------------------------------------------------------

def test_list_contracts_search_matches_object_title(client, factories):
    """Человек ищет договор по объекту, который помнит, а не по номеру."""
    obj = factories.ObjectFactory.create(title="ЖК Приморский")
    factories.ContractFactory.create(object=obj)
    factories.ContractFactory.create()

    found = client.get("/api/v1/contracts", params={"q": "Приморский"}).json()
    assert found["total"] == 1
    assert found["items"][0]["object_title"] == "ЖК Приморский"


def test_list_contracts_filters_by_rate_class(client, factories):
    target = factories.ContractFactory.create()
    factories.ContractFactory.create()

    found = client.get(
        "/api/v1/contracts", params={"rate_class_id": target.rate_class_id}
    ).json()
    assert [item["id"] for item in found["items"]] == [target.id]


def test_list_contracts_orders_newest_first(client, factories):
    old = factories.ContractFactory.create(signed_date=dt.date(2024, 1, 1))
    new = factories.ContractFactory.create(signed_date=dt.date(2026, 1, 1))

    items = client.get("/api/v1/contracts").json()["items"]
    assert [item["id"] for item in items] == [new.id, old.id]


# ---------------------------------------------------------------------------
#  Карточка: сметы и история загрузок (§7.1)
# ---------------------------------------------------------------------------

def test_card_lists_estimates_with_original_first(client, factories):
    contract = factories.ContractFactory.create()
    factories.EstimateFactory.create(contract=contract, amendment_no=2)
    factories.EstimateFactory.create(contract=contract, amendment_no=None)
    factories.EstimateFactory.create(contract=contract, amendment_no=1)

    estimates = client.get(f"/api/v1/contracts/{contract.id}").json()["estimates"]
    assert [e["amendment_no"] for e in estimates] == [None, 1, 2]


def test_card_counts_positions_of_estimate(client, factories):
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = factories.ProposalFactory.create(
        lot=factories.LotFactory.create(estimate=estimate)
    )
    for _ in range(3):
        factories.PositionItemFactory.create(proposal=proposal)

    estimates = client.get(f"/api/v1/contracts/{contract.id}").json()["estimates"]
    assert estimates[0]["positions_count"] == 3


def test_import_job_history_marks_only_the_current_estimate_job(client, factories):
    """`is_current` — то же условие, по которому §5 определяет идемпотентность.

    После замены (§5, правило 3) вытесненное задание остаётся в истории как
    аудит, но актуальную смету держит уже другое.
    """
    contract = factories.ContractFactory.create()
    replaced_job = factories.ImportJobFactory.create(
        contract=contract, status=ImportJobStatus.done.value
    )
    current_job = factories.ImportJobFactory.create(
        contract=contract, status=ImportJobStatus.done.value
    )
    factories.EstimateFactory.create(contract=contract, import_job_id=current_job.id)

    history = client.get(f"/api/v1/contracts/{contract.id}/import-jobs").json()
    by_id = {job["id"]: job for job in history}
    assert by_id[current_job.id]["is_current"] is True
    assert by_id[replaced_job.id]["is_current"] is False


def test_import_job_history_is_newest_first_and_carries_counters(client, factories):
    # Статус done обязателен: два незавершённых задания одной пары
    # (contract_id, amendment_no) запрещены индексом uq_import_jobs_active_pair (§4).
    contract = factories.ContractFactory.create()
    first = factories.ImportJobFactory.create(
        contract=contract, positions_total=10, status=ImportJobStatus.done.value
    )
    second = factories.ImportJobFactory.create(
        contract=contract, positions_total=20, status=ImportJobStatus.done.value
    )

    history = client.get(f"/api/v1/contracts/{contract.id}/import-jobs").json()
    assert [job["id"] for job in history] == [second.id, first.id]
    assert history[0]["counters"]["positions_total"] == 20


def test_import_job_history_of_unknown_contract_gives_404(client):
    """Пустой список соврал бы: «загрузок нет» и «договора нет» — разные ответы."""
    assert client.get("/api/v1/contracts/999999/import-jobs").status_code == 404


# ---------------------------------------------------------------------------
#  Правка и удаление
# ---------------------------------------------------------------------------

def test_patch_changes_only_passed_fields(client, factories):
    contract = factories.ContractFactory.create(signer="Иванов И.И.", title="Исходный")

    body = client.patch(
        f"/api/v1/contracts/{contract.id}", json={"signer": "Петров П.П."}
    ).json()
    assert body["signer"] == "Петров П.П."
    assert body["title"] == "Исходный"


def test_patch_null_in_not_null_field_gives_422(client, factories):
    contract = factories.ContractFactory.create()
    response = client.patch(f"/api/v1/contracts/{contract.id}", json={"signed_date": None})
    assert response.status_code == 422


def test_delete_empty_contract_succeeds(client, factories):
    contract = factories.ContractFactory.create()
    assert client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
    assert client.get(f"/api/v1/contracts/{contract.id}").status_code == 404


def test_delete_contract_with_import_history_is_refused(client, factories):
    contract = factories.ContractFactory.create()
    factories.ImportJobFactory.create(contract=contract)

    response = client.delete(f"/api/v1/contracts/{contract.id}")
    assert response.status_code == 409
    assert "аудит" in response.json()["detail"]


def test_delete_contract_with_estimate_is_refused(client, factories):
    contract = factories.ContractFactory.create()
    factories.EstimateFactory.create(contract=contract)

    response = client.delete(f"/api/v1/contracts/{contract.id}")
    assert response.status_code == 409
    assert "смет (1)" in response.json()["detail"]


# ---------------------------------------------------------------------------
#  Права (§6.2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/contracts", {"object_id": 1, "contractor_id": 1,
                                       "contract_number": "X", "signed_date": "2026-01-01"}),
        ("patch", "/api/v1/contracts/1", {"signer": "Кто-то"}),
        ("delete", "/api/v1/contracts/1", None),
    ],
)
def test_member_cannot_change_contracts(member, method, path, payload):
    response = getattr(member, method)(path, **({"json": payload} if payload else {}))
    assert response.status_code == 403


def test_member_can_read_contracts_and_history(member, factories):
    contract = factories.ContractFactory.create()
    assert member.get("/api/v1/contracts").status_code == 200
    assert member.get(f"/api/v1/contracts/{contract.id}").status_code == 200
    assert member.get(f"/api/v1/contracts/{contract.id}/import-jobs").status_code == 200

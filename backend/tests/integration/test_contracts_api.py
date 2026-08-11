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
from services.category_override import set_override

# Тестам нужен настоящий Postgres. Без маркера выборка `pytest -m integration`
# молча их не собирала бы — а это ложная уверенность при точечном прогоне.
pytestmark = pytest.mark.integration

#: Все шесть полей, а не выборка: неполный payload оставил бы часть колонок
#: без единого исполняющего теста при верной схеме.
TERMS = {
    "advance_pct": "30",
    "advance_note": "двумя траншами",
    "bank_guarantee_pct": "10",
    "bank_guarantee_note": "возврат аванса и исполнение",
    "retention_pct": "5",
    "retention_note": "возврат после подписания акта",
}


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


@pytest.fixture
def contract_payload(factories) -> dict:
    """Валидное минимальное тело создания договора (§2.5, коммерческие условия)."""
    obj = factories.ObjectFactory.create(rate_class=factories.RateClassFactory.create())
    contractor = factories.ContractorFactory.create()
    return _payload(obj.id, contractor.id)


@pytest.fixture
def contract_with_terms(factories) -> int:
    """Договор со всеми шестью коммерческими условиями, для правки/паритета.

    Заводится напрямую фабрикой (не через `client.post`): фикстура нужна и в
    тестах на права (`member`), а `member` переключает роль **того же** `client`
    — заведение через API рисковало бы попасть под уже переключённую роль в
    зависимости от порядка резолвинга фикстур. Тот же приём, что у
    `object_with_areas` в `test_references_api.py`.
    """
    contract = factories.ContractFactory.create(
        advance_pct=Decimal(TERMS["advance_pct"]),
        advance_note=TERMS["advance_note"],
        bank_guarantee_pct=Decimal(TERMS["bank_guarantee_pct"]),
        bank_guarantee_note=TERMS["bank_guarantee_note"],
        retention_pct=Decimal(TERMS["retention_pct"]),
        retention_note=TERMS["retention_note"],
    )
    return contract.id


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


def test_each_estimate_row_carries_its_own_decision_count(
    client, db_session, contract_with_amendment, chapter_of_source, chapter_of_amendment,
    category_id, admin_user,
):
    """Счётчик обязан быть ПОСМЕТНЫМ: паспорт описывает только исходную смету, а
    заменять можно любое допсоглашение (спека §2.9, задача 6).

    `chapter_of_amendment` в аргументах — не для действия, а для доказательства:
    он подтверждает, что у допсоглашения ЕСТЬ свой раздел, который МОГ БЫ
    получить решение и не получил, — а не что там просто нет раздела, на
    который решение можно было бы поставить.
    """
    assert chapter_of_amendment.is_chapter
    set_override(
        db_session,
        estimate_id=contract_with_amendment.source_estimate_id,
        position_item_id=chapter_of_source.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.commit()

    rows = client.get(f"/api/v1/contracts/{contract_with_amendment.id}").json()["estimates"]
    by_amendment = {r["amendment_no"]: r for r in rows}
    assert by_amendment[None]["category_overrides_count"] == 1
    assert by_amendment[1]["category_overrides_count"] == 0


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


def test_rejected_contract_patch_leaves_nothing_behind(client):
    """Отвергнутая правка договора не видна и в той же сессии (собственное ревью).

    Всё создаётся через API: фабрика не коммитит, а откат снял бы её данные вместе
    с правкой.
    """
    rate_class_id = client.post("/api/v1/rate-classes", json={"title": "Класс для правки"}).json()["id"]
    object_id = client.post(
        "/api/v1/objects", json={"title": "Объект исходный", "rate_class_id": rate_class_id}
    ).json()["id"]
    other_object_id = client.post(
        "/api/v1/objects", json={"title": "Объект другой", "rate_class_id": rate_class_id}
    ).json()["id"]
    contractor_id = client.post(
        "/api/v1/contractors", json={"title": "Подрядчик", "inn": "222000222000"}
    ).json()["id"]
    contract_id = client.post(
        "/api/v1/contracts",
        json=_payload(object_id, contractor_id, signer="Иванов И.И."),
    ).json()["id"]

    # Объект существует и будет присвоен, а номер договора пустой — отказ приходит
    # уже после мутации.
    response = client.patch(
        f"/api/v1/contracts/{contract_id}",
        json={"object_id": other_object_id, "contract_number": "   "},
    )
    assert response.status_code == 422

    body = client.get(f"/api/v1/contracts/{contract_id}").json()
    assert body["object_id"] == object_id
    assert body["signer"] == "Иванов И.И."


# ---------------------------------------------------------------------------
#  Коммерческие условия: три пары «процент + комментарий» (спека §2.5)
# ---------------------------------------------------------------------------

def test_commercial_terms_round_trip(client, contract_payload):
    created = client.post("/api/v1/contracts", json={**contract_payload, **TERMS})
    assert created.status_code == 201
    card = client.get(f"/api/v1/contracts/{created.json()['id']}").json()
    for field, expected in TERMS.items():
        assert card[field] == expected, field


def test_patch_touches_one_field_only(client, contract_with_terms):
    client.patch(f"/api/v1/contracts/{contract_with_terms}", json={"retention_pct": "7"})
    card = client.get(f"/api/v1/contracts/{contract_with_terms}").json()
    assert card["retention_pct"] == "7"
    assert card["advance_pct"] == "30"          # не поехало
    assert card["advance_note"] == "двумя траншами"


def test_null_clears_a_single_condition(client, contract_with_terms):
    client.patch(f"/api/v1/contracts/{contract_with_terms}", json={"advance_pct": None})
    card = client.get(f"/api/v1/contracts/{contract_with_terms}").json()
    assert card["advance_pct"] is None
    assert card["advance_note"] == "двумя траншами"   # парности нет


def test_note_without_percent_is_accepted(client, contract_payload):
    r = client.post(
        "/api/v1/contracts",
        json={**contract_payload, "advance_note": "аванс не предусмотрен"},
    )
    assert r.status_code == 201
    assert r.json()["advance_pct"] is None


def test_empty_note_becomes_null_not_empty_string(client, contract_payload):
    """Пустая строка означала бы «условие заведено», хотя заведено ничего не было."""
    r = client.post("/api/v1/contracts", json={**contract_payload, "advance_note": "   "})
    assert r.json()["advance_note"] is None


@pytest.mark.parametrize("field", ["advance_pct", "bank_guarantee_pct", "retention_pct"])
@pytest.mark.parametrize("value", ["-1", "101"])
@pytest.mark.parametrize("method", ["post", "patch"])
def test_percent_outside_range_is_422_not_500(
    client, contract_payload, contract_with_terms, field, value, method
):
    """Обе границы, все три условия, оба метода — спека §2.4 требует PATCH тоже."""
    r = (
        client.post("/api/v1/contracts", json={**contract_payload, field: value})
        if method == "post"
        else client.patch(f"/api/v1/contracts/{contract_with_terms}", json={field: value})
    )
    assert r.status_code == 422


def test_terms_are_absent_from_the_list_response(client, contract_with_terms):
    """Список — это выбор, а не карточка (спека §2.5).

    Проверяется всё множество из шести полей: утверждение про одно поле прошло
    бы, если бы в `_contract_row_dict` случайно дописали пять остальных.
    """
    item = client.get("/api/v1/contracts").json()["items"][0]
    assert set(TERMS) & set(item) == set()


def test_percent_reaches_json_as_string(client, contract_with_terms):
    r = client.get(f"/api/v1/contracts/{contract_with_terms}")
    assert '"advance_pct":"30"' in r.text.replace(" ", "")


def test_float_percent_is_rejected(client, contract_payload):
    r = client.post("/api/v1/contracts", json={**contract_payload, "advance_pct": 30.5})
    assert r.status_code == 422


def test_member_cannot_edit_commercial_terms(member, contract_with_terms):
    r = member.patch(f"/api/v1/contracts/{contract_with_terms}", json={"advance_pct": "50"})
    assert r.status_code == 403

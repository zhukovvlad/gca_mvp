"""Эндпоинт сравнения договоров `GET /api/v1/analytics/comparison` (спека
2026-08-17 §2.6, §2.9, план — задача 5).

Что здесь под контролем — только API-слой (парсинг `ids`, права, форма ответа).
Семантика агрегата повторно здесь не проверяется — иначе два набора тестов
начали бы расходиться, как расходились бы две реализации. Она покрыта
поимённо: `test_comparison_rollup.py` (роллап и нетто), `test_comparison_rows.py`
(союз строк и состояния ячеек), `test_comparison_buckets.py` (корзины, режимы
НДС, медианы), `test_comparison_selection.py` (обе формы выборки).

`pytestmark = pytest.mark.integration` — обязателен для интеграционного пакета.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from models import UserRole
from tests.comparison_fixtures import contract_with_area

pytestmark = pytest.mark.integration

COMPARISON_URL = "/api/v1/analytics/comparison"


# ---------------------------------------------------------------------------
#  DoD 1 — `ids` и `all=1` дают одинаковый ответ на одинаковом множестве
# ---------------------------------------------------------------------------

def test_ids_and_filter_selection_agree(client, factories, db_session):
    """Обе формы выборки на одном множестве договоров дают одинаковый ответ
    (DoD 1). Фикстура обязана доказать, что фильтр РЕАЛЬНО что-то исключил —
    иначе совпадение было бы пустым утверждением."""
    rate_class = factories.RateClassFactory.create()
    other_rate_class = factories.RateClassFactory.create()

    included_1 = factories.ContractFactory.create(rate_class=rate_class)
    included_2 = factories.ContractFactory.create(rate_class=rate_class)
    excluded = factories.ContractFactory.create(rate_class=other_rate_class)
    db_session.commit()

    ids_url = f"{COMPARISON_URL}?ids={included_1.id},{included_2.id}"
    filter_url = f"{COMPARISON_URL}?all=1&rate_class_id={rate_class.id}"

    resp_ids = client.get(ids_url)
    resp_filter = client.get(filter_url)

    assert resp_ids.status_code == 200
    assert resp_filter.status_code == 200
    assert resp_ids.json() == resp_filter.json()

    # Фильтр реально исключил третий договор — иначе совпадение ответов
    # ничего бы не доказывало.
    filtered_column_ids = {column["contract_id"] for column in resp_filter.json()["columns"]}
    assert filtered_column_ids == {included_1.id, included_2.id}
    assert excluded.id not in filtered_column_ids


# ---------------------------------------------------------------------------
#  DoD 3 — порядок колонок: signed_date DESC, затем id DESC
# ---------------------------------------------------------------------------

def test_column_order_is_signed_date_desc_then_id_desc(client, factories, db_session):
    """Порядок колонок (DoD 3). Две сметы делят одну дату подписания — иначе
    тест проверял бы только сортировку по дате и не трогал бы вовсе tie-break
    по `id`."""
    tied_date = dt.date(2025, 5, 1)
    older = factories.ContractFactory.create(signed_date=tied_date)
    newer_same_date = factories.ContractFactory.create(signed_date=tied_date)
    newest = factories.ContractFactory.create(signed_date=dt.date(2025, 6, 1))
    db_session.commit()

    # id newer_same_date > id older (созданы позже) — при равной дате именно
    # он обязан идти первым среди двоих, если tie-break по id DESC работает.
    assert newer_same_date.id > older.id

    ids = f"{older.id},{newer_same_date.id},{newest.id}"
    resp = client.get(f"{COMPARISON_URL}?ids={ids}")

    assert resp.status_code == 200
    column_ids = [column["contract_id"] for column in resp.json()["columns"]]
    assert column_ids == [newest.id, newer_same_date.id, older.id]


# ---------------------------------------------------------------------------
#  DoD 22 — все четыре фильтра списка, без page/page_size
# ---------------------------------------------------------------------------

def test_all_four_filters_are_accepted(client, factories, db_session):
    """`q`, `object_id`, `contractor_id`, `rate_class_id` — каждый реально
    сужает выборку (DoD 22)."""
    target_object = factories.ObjectFactory.create()
    target_contractor = factories.ContractorFactory.create()
    target_rate_class = factories.RateClassFactory.create()
    target = factories.ContractFactory.create(
        object=target_object,
        contractor=target_contractor,
        rate_class=target_rate_class,
        contract_number="УНИК-9001",
    )
    noise = factories.ContractFactory.create()
    db_session.commit()

    for query in (
        f"object_id={target_object.id}",
        f"contractor_id={target_contractor.id}",
        f"rate_class_id={target_rate_class.id}",
        "q=УНИК-9001",
    ):
        resp = client.get(f"{COMPARISON_URL}?all=1&{query}")
        assert resp.status_code == 200, query
        column_ids = {column["contract_id"] for column in resp.json()["columns"]}
        assert column_ids == {target.id}, query
        assert noise.id not in column_ids, query


def test_page_and_page_size_are_not_part_of_the_contract(client, factories, db_session):
    """`page`/`page_size` НЕ переносятся (спека §2.6): их передача не должна
    молча урезать выборку — сравнение берёт её целиком (DoD 22)."""
    target_object = factories.ObjectFactory.create()
    target = factories.ContractFactory.create(object=target_object)
    db_session.commit()

    resp = client.get(
        f"{COMPARISON_URL}?all=1&object_id={target_object.id}&page=2&page_size=1"
    )

    assert resp.status_code == 200
    column_ids = {column["contract_id"] for column in resp.json()["columns"]}
    assert column_ids == {target.id}


# ---------------------------------------------------------------------------
#  DoD 14 — чтение доступно member
# ---------------------------------------------------------------------------

def test_member_can_read_comparison(client, factories, db_session):
    """Чтение сравнения доступно и `member` (спека §2.9, DoD 14)."""
    contract = factories.ContractFactory.create()
    db_session.commit()

    client.auth_state["role"] = UserRole.member
    resp = client.get(f"{COMPARISON_URL}?ids={contract.id}")

    assert resp.status_code == 200
    assert resp.json()["columns"][0]["contract_id"] == contract.id


# ---------------------------------------------------------------------------
#  Деньги — строками в СЫРОМ теле, decimal_json обязателен
# ---------------------------------------------------------------------------

def test_money_is_returned_as_strings(client, factories, db_session):
    """`decimal_json` обязателен: утверждение на сырое тело JSON, а не на
    разбор — после `json.loads` строка и число неразличимы."""
    contract_id = contract_with_area(
        db_session, factories, {"1": ["1000.00"]}, vat_rate=Decimal("0"),
    )
    db_session.commit()

    compact = client.get(f"{COMPARISON_URL}?ids={contract_id}&vat_mode=net").text.replace(" ", "")

    assert '"net":"1000.00"' in compact
    assert '"net":1000.00' not in compact


# ---------------------------------------------------------------------------
#  Ошибки выборки: 404 / 400 доходят с HTTP-статусами
# ---------------------------------------------------------------------------

def test_unknown_id_is_404(client, factories, db_session):
    contract = factories.ContractFactory.create()
    db_session.commit()
    missing_id = contract.id + 100000

    resp = client.get(f"{COMPARISON_URL}?ids={contract.id},{missing_id}")

    assert resp.status_code == 404
    assert str(missing_id) in resp.json()["detail"]


def test_both_selection_forms_at_once_is_400(client, factories, db_session):
    contract = factories.ContractFactory.create()
    db_session.commit()

    resp = client.get(f"{COMPARISON_URL}?ids={contract.id}&all=1")

    assert resp.status_code == 400


def test_no_selection_form_is_400(client):
    resp = client.get(COMPARISON_URL)

    assert resp.status_code == 400


def test_non_numeric_ids_element_is_400_not_422(client):
    """Нечисловой элемент `ids` — понятный 400, а не трасса 422 (план,
    задача 5)."""
    resp = client.get(f"{COMPARISON_URL}?ids=1,abc,3")

    assert resp.status_code == 400
    assert "abc" in resp.json()["detail"]


def test_unknown_vat_mode_is_400(client, factories, db_session):
    contract = factories.ContractFactory.create()
    db_session.commit()

    resp = client.get(f"{COMPARISON_URL}?ids={contract.id}&vat_mode=bogus")

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
#  vat_mode по умолчанию — «своя ставка»
# ---------------------------------------------------------------------------

def test_vat_mode_defaults_to_own(client, factories, db_session):
    """Отсутствующий `vat_mode` — «своя ставка» (спека §2.3, план — задача 5)."""
    contract = factories.ContractFactory.create()
    db_session.commit()

    resp = client.get(f"{COMPARISON_URL}?ids={contract.id}")

    assert resp.status_code == 200
    assert resp.json()["vat_mode"] == "own"

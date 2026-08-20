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
    (DoD 1 сравнения, DoD 2 диаграммы). Фикстура обязана доказать, что фильтр
    РЕАЛЬНО что-то исключил — иначе совпадение было бы пустым утверждением.

    **Пара адресов исправлена ревизией §2.6, и это не ослабление теста.** Раньше
    здесь стояло `?ids=A,B` против `?all=1&rate_class_id=X`, и это было верно,
    пока класс был ФОРМОЙ выборки: второй адрес означал ровно «выборка = договоры
    класса X». После ревизии класс — СУЖЕНИЕ, и второй адрес означает «все
    договоры, суженные классом X». Выборки стали разными при одинаковом
    результате, а `available_rate_classes` эту разницу показывает — он для того и
    введён (снятый чип надо чем-то возвращать). Поэтому сравнивается пара из
    DoD 2 диаграммы: `?ids=<всё>&rate_class_id=X` против `?all=1&rate_class_id=X`
    — одинаковые выборки, одинаковое сужение. Утверждение при этом стало СИЛЬНЕЕ:
    в равенство ответов целиком теперь входит и фасет.
    """
    rate_class = factories.RateClassFactory.create()
    other_rate_class = factories.RateClassFactory.create()

    included_1 = factories.ContractFactory.create(rate_class=rate_class)
    included_2 = factories.ContractFactory.create(rate_class=rate_class)
    excluded = factories.ContractFactory.create(rate_class=other_rate_class)
    db_session.commit()

    every_id = f"{included_1.id},{included_2.id},{excluded.id}"
    ids_url = f"{COMPARISON_URL}?ids={every_id}&rate_class_id={rate_class.id}"
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

    # Предпосылка формы `all=1`: в базе РОВНО эти три договора. Без замера
    # равенство форм держалось бы на том, что чужая фикстура ничего не насеяла, —
    # а `all=1` берёт всё, что есть, и с четвёртым договором надмножества форм
    # разошлись бы, утащив за собой фасет.
    assert sum(e["count"] for e in resp_filter.json()["available_rate_classes"]) == 3


def test_narrower_enumeration_gives_a_narrower_facet(client, factories, db_session):
    """Фасет отражает ФОРМУ выборки, а не только её результат.

    `?ids=A,B` и `?all=1&rate_class_id=X` могут сойтись в колонках и разойтись в
    фасете: у первого надмножество — само перечисление, у второго — вся база.
    Это не дефект, а механизм обратимости: фасет помнит то, что сужение отсекло,
    и помнить он может только в пределах заданной выборки. Перечислив два
    договора, человек не спрашивал про третий, и предлагать чип его класса
    неоткуда.

    Тест заведён потому, что на этом споткнулись: прежняя редакция соседнего
    теста сравнивала именно эту пару адресов и стала красной. Факт закреплён,
    чтобы следующий читатель не «починил» его обратно.
    """
    rate_class = factories.RateClassFactory.create(title="Класс узкий")
    other_rate_class = factories.RateClassFactory.create(title="Класс отсечённый")

    included_1 = factories.ContractFactory.create(rate_class=rate_class)
    included_2 = factories.ContractFactory.create(rate_class=rate_class)
    excluded = factories.ContractFactory.create(rate_class=other_rate_class)
    db_session.commit()

    by_ids = client.get(f"{COMPARISON_URL}?ids={included_1.id},{included_2.id}").json()
    by_filter = client.get(f"{COMPARISON_URL}?all=1&rate_class_id={rate_class.id}").json()

    # Колонки совпадают: агрегат от формы выборки не зависит.
    assert [column["contract_id"] for column in by_ids["columns"]] == [
        column["contract_id"] for column in by_filter["columns"]
    ]

    # А фасеты — нет, и вот чем именно.
    assert [entry["id"] for entry in by_ids["available_rate_classes"]] == [rate_class.id]
    assert {entry["id"] for entry in by_filter["available_rate_classes"]} == {
        rate_class.id, other_rate_class.id,
    }
    assert excluded.id not in {column["contract_id"] for column in by_filter["columns"]}


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

@pytest.mark.parametrize("rate", ["-100", "-0.01", "100.01", "1000"])
@pytest.mark.parametrize(
    "url", ["/api/v1/analytics/comparison", "/api/v1/reports/comparison"]
)
def test_single_rate_outside_zero_hundred_is_400_on_both_routes(
    client, factories, db_session, url, rate
):
    """Ставка показа вне 0…100 — отказ, и ОДИНАКОВЫЙ у экрана и у выгрузки.

    Найдено внешним ревью (P2). Без границ ручной адрес с `single_rate=-100`
    обнулял все суммы (`net_to_gross` умножает на `(100 + ставка)/100`), а ниже
    −100 % делал их отрицательными — включая xlsx. Границы те же, что схема
    держит на `estimates.vat_rate_target`: ставка из адреса не может быть шире
    назначаемой смете.

    Оба маршрута проверяются ОДНИМ тестом намеренно: выборку и режим они уже
    делят одним правилом, и разойтись в ответе на негодную ставку им нельзя.
    """
    contract = factories.ContractFactory.create()
    db_session.commit()

    resp = client.get(f"{url}?ids={contract.id}&vat_mode=single&single_rate={rate}")

    assert resp.status_code == 400, resp.text
    assert "0…100" in resp.json()["detail"]


@pytest.mark.parametrize(
    "url", ["/api/v1/analytics/comparison", "/api/v1/reports/comparison"]
)
def test_single_rate_at_the_boundaries_is_accepted(client, factories, db_session, url):
    """0 и 100 — законные ставки, отказ обязан быть строго ВНЕ диапазона.

    Без этой пары предыдущий тест прошёл бы и на реализации, отвергающей всё
    подряд: «отказано» само по себе не доказывает, что граница на месте.
    """
    contract_id = contract_with_area(
        db_session, factories, {"1": ["1000.00"]}, vat_rate=Decimal("20")
    )
    db_session.commit()

    for rate in ("0", "100"):
        resp = client.get(f"{url}?ids={contract_id}&vat_mode=single&single_rate={rate}")
        assert resp.status_code == 200, f"{rate}: {resp.text}"


def test_vat_mode_defaults_to_own(client, factories, db_session):
    """Отсутствующий `vat_mode` — «своя ставка» (спека §2.3, план — задача 5)."""
    contract = factories.ContractFactory.create()
    db_session.commit()

    resp = client.get(f"{COMPARISON_URL}?ids={contract.id}")

    assert resp.status_code == 200
    assert resp.json()["vat_mode"] == "own"

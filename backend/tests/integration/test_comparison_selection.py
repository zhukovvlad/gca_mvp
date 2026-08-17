"""Разрешение выборки сравнения: `ids` либо фильтр (спека §2.6, DoD 1, DoD 22).

Резолвер живёт отдельным слоем и проверяется отдельно от эндпоинта потому, что им
пользуются ДВА эндпоинта — сравнение (задача 5) и выгрузка листа (задача 6). Одно
правило выборки на двоих: иначе экран и файл могли бы сравнивать разные множества
договоров, а это ровно тот класс дефектов, из-за которого спека §2.7 требует один
серверный агрегат.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import comparison as cmp
from crud.common import DomainError
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


def _contract(db, factories, *, number: str, object_title: str):
    obj = factories.ObjectFactory.create(title=object_title)
    contract = factories.ContractFactory.create(contract_number=number, object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db, factories, proposal=proposal, code="1", amounts=["100.00"])
    db.flush()
    return contract


def test_filter_form_selects_the_same_ids_as_the_contracts_list(db_session, factories):
    """Фильтр сравнения — РОВНО фильтр списка договоров (спека §2.6).

    Сверяется не с переписанным условием, а с ответом самого
    `crud.contracts.list_contracts`: только так «те же фильтры» остаётся фактом,
    а не совпадением двух реализаций.
    """
    from crud import contracts as crud_contracts

    _contract(db_session, factories, number="ГП-A-1", object_title="Башня")
    _contract(db_session, factories, number="ГП-B-2", object_title="Квартал")

    selected = cmp.resolve_selection(db_session, use_filter=True, q="Башня")
    listed = crud_contracts.list_contracts(db_session, q="Башня", page=1, page_size=100)

    assert [item["id"] for item in listed["items"]] == selected
    assert len(selected) == 1, "предпосылка: фильтр обязан отсеять второй договор"


def test_ids_form_takes_exactly_what_was_asked(db_session, factories):
    a = _contract(db_session, factories, number="ГП-C-1", object_title="Объект C")
    b = _contract(db_session, factories, number="ГП-D-2", object_title="Объект D")
    _contract(db_session, factories, number="ГП-E-3", object_title="Объект E")

    assert sorted(cmp.resolve_selection(db_session, ids=[a.id, b.id])) == sorted([a.id, b.id])


def test_unknown_id_is_named_not_silently_dropped(db_session, factories):
    """Несуществующий id — отказ с его номером, а не молчаливое выпадение.

    Молча уронив колонку, страница сравнила бы меньше договоров, чем просил
    человек, и не сказала бы об этом — хуже отказа.
    """
    a = _contract(db_session, factories, number="ГП-F-1", object_title="Объект F")
    missing = a.id + 10_000

    with pytest.raises(DomainError) as raised:
        cmp.resolve_selection(db_session, ids=[a.id, missing])

    assert raised.value.status_code == 404
    assert str(missing) in raised.value.detail, "отказ обязан называть отсутствующий id"


def test_both_forms_at_once_is_a_request_error(db_session, factories):
    """`ids` и `all=1` разом — двусмысленность, а не удобство."""
    a = _contract(db_session, factories, number="ГП-G-1", object_title="Объект G")

    with pytest.raises(DomainError) as raised:
        cmp.resolve_selection(db_session, ids=[a.id], use_filter=True)

    assert raised.value.status_code == 400


def test_no_form_at_all_is_a_request_error(db_session):
    with pytest.raises(DomainError) as raised:
        cmp.resolve_selection(db_session)

    assert raised.value.status_code == 400


def test_filter_matching_nothing_is_an_empty_selection_not_an_error(db_session, factories):
    """Пустой результат фильтра — законный ответ, а не ошибка: договоров под
    фильтр может не быть, и сказать об этом надо пустой таблицей."""
    _contract(db_session, factories, number="ГП-H-1", object_title="Объект H")

    assert cmp.resolve_selection(db_session, use_filter=True, q="такого объекта нет") == []


def test_empty_selection_builds_an_empty_aggregate(db_session):
    """Агрегат на пустой выборке собирается и не падает: ни колонок, ни строк."""
    agg = cmp.build_comparison(db_session, [], vat_mode="net")

    assert agg["columns"] == []
    assert agg["rows"] == []
    assert agg["totals"] == []
    assert agg["rate_options"] == []
    assert agg["rate_preselected"] is None


def test_duplicate_ids_do_not_duplicate_columns(db_session, factories):
    """Повторённый id не даёт вторую колонку того же договора."""
    a = _contract(db_session, factories, number="ГП-I-1", object_title="Объект I")

    selected = cmp.resolve_selection(db_session, ids=[a.id, a.id])
    agg = cmp.build_comparison(db_session, selected, vat_mode="net")

    assert selected == [a.id]
    assert [column["contract_id"] for column in agg["columns"]] == [a.id]


def test_single_rate_survives_as_exact_decimal(db_session, factories):
    """Ставка показа доезжает до агрегата точным `Decimal`, без float-посредника."""
    a = _contract(db_session, factories, number="ГП-J-1", object_title="Объект J")

    agg = cmp.build_comparison(db_session, [a.id], vat_mode="single", single_rate=Decimal("20.5"))

    assert agg["single_rate"] == Decimal("20.5")
    assert isinstance(agg["single_rate"], Decimal)

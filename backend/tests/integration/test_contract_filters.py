"""CRUD-фильтр договоров по классам ставок (задача 2 плана).

Функция `apply_contract_filters` наложит фильтр на список договоров;
`filtered_contract_ids` выберет их идентификаторы. Оба обязаны работать с
одиночным значением (сегодняшнее поведение) и со списком (новое).

Пустой список — отказ: он означает ошибку в коде вызывающей стороны, а не
«все классы», поэтому тишком не вытираем чужую ошибку.
"""

from __future__ import annotations

import pytest

from crud import contracts as crud_contracts
from crud.common import DomainError

pytestmark = pytest.mark.integration


def test_single_rate_class_id_selects_only_that_class(db_session, factories):
    """Одиночный rate_class_id — прежняя семантика цела.

    `filtered_contract_ids` ищет договоры с точным совпадением класса.
    Три договора трёх разных классов; фильтр по одному -> ровно один договор.
    """
    class1 = factories.RateClassFactory.create()
    class2 = factories.RateClassFactory.create()
    class3 = factories.RateClassFactory.create()

    factories.ContractFactory.create(rate_class=class1)
    contract2 = factories.ContractFactory.create(rate_class=class2)
    factories.ContractFactory.create(rate_class=class3)

    result = crud_contracts.filtered_contract_ids(db_session, rate_class_id=class2.id)

    assert result == [contract2.id]


def test_list_of_rate_class_ids_selects_union(db_session, factories):
    """Список классов даёт ОБЪЕДИНЕНИЕ договоров под любой из них.

    Три договора трёх разных классов; фильтр по двум id -> ровно два договора.
    Порядок — `CONTRACT_LIST_ORDER` (signed_date DESC, id DESC), а не порядок фильтра.
    """
    class1 = factories.RateClassFactory.create()
    class2 = factories.RateClassFactory.create()
    class3 = factories.RateClassFactory.create()

    contract1 = factories.ContractFactory.create(rate_class=class1)
    contract2 = factories.ContractFactory.create(rate_class=class2)
    factories.ContractFactory.create(rate_class=class3)

    result = crud_contracts.filtered_contract_ids(db_session, rate_class_id=[class1.id, class2.id])

    assert set(result) == {contract1.id, contract2.id}
    assert len(result) == 2


def test_empty_rate_class_list_raises_domain_error_400(db_session, factories):
    """Пустой список — отказ 400, а не молчаливое 'фильтра нет'.

    Пустой список означает ошибку в коде: вызывающая сторона составила
    условие, но оно оказалось пустым. Молча трактовать это как 'все классы'
    значит прятать чужую ошибку.
    """
    factories.ContractFactory.create()
    factories.ContractFactory.create()

    with pytest.raises(DomainError) as raised:
        crud_contracts.filtered_contract_ids(db_session, rate_class_id=[])

    assert raised.value.status_code == 400
    assert "пустым" in raised.value.detail.lower()

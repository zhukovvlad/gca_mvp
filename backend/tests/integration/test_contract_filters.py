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
    # Предпосылка: классы РАЗНЫЕ. Стань фабрика `get_or_create` по `title` —
    # «объединение» двух совпавших классов дало бы те же два договора, и тест
    # прошёл бы вакуумно (`docs/insights/false-test-premises.md`).
    assert len({class1.id, class2.id, class3.id}) == 3

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


def test_string_rate_class_id_is_a_request_error(db_session, factories):
    """Строка вместо числа — отказ, а не выборка по символам.

    Строка тоже последовательность, и `list("12")` даёт два СИМВОЛА: без отказа
    фильтр ушёл бы в `IN ('1','2')` и вернул 200 по чужой выборке. Найдено
    ревью; сегодня недостижимо через HTTP (формат адреса разбирает роутер), но
    разбор строки вводится следующей задачей, и один недоделанный `parse` попал
    бы сюда молча.
    """
    factories.ContractFactory.create()

    with pytest.raises(DomainError) as raised:
        crud_contracts.filtered_contract_ids(db_session, rate_class_id="12")

    assert raised.value.status_code == 400


def test_blank_search_term_means_no_filter(db_session, factories):
    """Пробельный `q` — «фильтр не задан», и это ОДНО правило на весь модуль.

    Замер до правки: `resolve_selection` считал заданным всё, что
    `not in (None, "")`, поэтому `?ids=1,2&q=%20` отвечал 400 «выборка задана
    дважды», а `?all=1&q=%20` — полной выборкой без фильтра. Один и тот же `q` в
    одном модуле значил разное. Теперь пустоту решает
    `normalized_search_term`, и оба места читают её.
    """
    factories.ContractFactory.create()
    factories.ContractFactory.create()

    assert crud_contracts.normalized_search_term("   ") is None
    assert crud_contracts.normalized_search_term(None) is None
    assert crud_contracts.normalized_search_term("  Башня  ") == "Башня"

    everything = crud_contracts.filtered_contract_ids(db_session)
    blank = crud_contracts.filtered_contract_ids(db_session, q="   ")
    assert blank == everything, "пробельный `q` не имеет права сужать выборку"

"""Форма ключа сортировки рейтинга объектов — вторая половина тай-брейка.

**Поведенческого теста мало, и это не придирка.** Порядок при равных суммах
может стабильно совпадать с ожидаемым по чистому везению: `sorted` в Python
устойчив, PostgreSQL вправе возвращать один и тот же физический порядок строк, и
сто повторных прогонов не докажут ничего. Измеритель обязан быть ФОРМОЙ, а не
исходом: снятие вторичного ключа делает ключи двух равных по сумме объектов
ОДИНАКОВЫМИ, и это видно прямо здесь, независимо от того, что дальше сделает
сортировка.

**Отступление от плана, названное фактом.** План требовал тест формы
СКОМПИЛИРОВАННОГО SQL — «в `ORDER BY` есть вторичный ключ», по образцу
`test_matching_sql.py`. Такого `ORDER BY` не существует и существовать не может:
Global Constraints той же страницы плана (и спека §2.6) требуют считать сумму
договора построчным `restate_gross` в PYTHON, а по значению, которого в базе нет,
SQL сортировать не умеет. Измеритель поэтому перенесён на ключ сортировки —
принцип «форма, а не везение планировщика» сохранён дословно, сменился только
носитель формы.
"""
from __future__ import annotations

from decimal import Decimal

from crud.dashboard import ObjectMoney, ranking_sort_key


def _row(object_id: int, amount: str) -> ObjectMoney:
    return ObjectMoney(
        object_id=object_id,
        title=f"Объект {object_id}",
        reason=None,
        amount=Decimal(amount),
        area_total_sp=None,
        area_useful_sp=None,
        per_sqm=None,
        rate_class_id=None,
        rate_class_title=None,
        contract_id=None,
        contract_number=None,
        signed_date=None,
        contractor_title=None,
        display_rate=None,
    )


class TestRankingOrderForm:
    def test_key_carries_a_secondary_component(self):
        """Ключ — кортеж больше чем из одного элемента, и второй элемент
        различает объекты."""
        key = ranking_sort_key(_row(7, "1000"))

        assert isinstance(key, tuple)
        assert len(key) > 1
        assert 7 in key[1:]

    def test_equal_sums_give_different_keys(self):
        """Главное утверждение: два объекта с ОДИНАКОВОЙ суммой обязаны получить
        РАЗНЫЕ ключи. Снятие вторичного ключа роняет именно это — детерминированно
        и без обращения к базе."""
        assert ranking_sort_key(_row(1, "1000")) != ranking_sort_key(_row(2, "1000"))

    def test_bigger_sum_sorts_first(self):
        assert ranking_sort_key(_row(1, "2000")) < ranking_sort_key(_row(2, "1000"))

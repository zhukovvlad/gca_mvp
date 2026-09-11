"""Предикат цены и предикат веса — Python-сторона (спека правила цены,
docs/superpowers/specs/2026-09-09-price-predicate-design.md, §2.1-§2.2).

`TRUTH_TABLE` ниже — единственный литеральный оракул модуля: восемь входов
(пусто, ноль, положительное целое, положительное дробное, отрицательное, NaN,
+Infinity, -Infinity) — число задано длиной кортежа, не текстом, и растёт
вместе с ним. Тот же список и тот же порядок дублирует (не импортирует)
`test_price_predicate_sql.py::TRUTH_TABLE`, чтобы расхождение между площадками
было видно по одинаковым именам входов, а не по разным фикстурам.

Синхронность двух копий не стережёт ничто, и стеречь её не требуется: каждая
самодостаточна для СВОИХ площадок — тамошняя таблица служит оракулом и
SQL-стороне, и `is_price`/`is_weight` одновременно. Уменьшись одна из копий,
её файл просто предъявит меньше входов; ложно-зелёного из этого не выйдет,
потому что оставшиеся входы по-прежнему сверяются с литералом, а не с другой
площадкой.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest

from money.price import is_price, is_weight

#: (имя, вход, ожидаемый ответ).
TRUTH_TABLE = (
    ("пусто", None, False),
    ("ноль", Decimal("0"), False),
    ("положительное целое", Decimal("100"), True),
    ("положительное дробное", Decimal("12.34"), True),
    ("отрицательное", Decimal("-100"), False),
    ("NaN", Decimal("NaN"), False),
    ("+Infinity", Decimal("Infinity"), False),
    ("-Infinity", Decimal("-Infinity"), False),
)


class TestIsPrice:
    @pytest.mark.parametrize(("name", "value", "expected"), TRUTH_TABLE)
    def test_truth_table(self, name, value, expected):
        assert is_price(value) is expected, f"вход «{name}»"

    def test_does_not_raise_on_nan(self):
        """Конечность обязана проверяться ДО сравнения с нулём: голое
        `Decimal("NaN") > 0` не возвращает `False`, а бросает `InvalidOperation`
        — это и есть причина, по которой наивное «больше нуля» непригодно
        предикатом (спека §1.2)."""
        with pytest.raises(InvalidOperation):
            _ = Decimal("NaN") > 0

        assert is_price(Decimal("NaN")) is False


class TestIsWeight:
    @pytest.mark.parametrize(("name", "value", "expected"), TRUTH_TABLE)
    def test_truth_table(self, name, value, expected):
        """Формула та же, что у `is_price`, но факт — другой (спека §2.1: свой
        носитель); проверяется отдельной функцией на той же таблице, а не
        выводится из `is_price`."""
        assert is_weight(value) is expected, f"вход «{name}»"

    def test_does_not_raise_on_nan(self):
        """Тот же довод, что у `TestIsPrice.test_does_not_raise_on_nan`: голое
        `Decimal("NaN") > 0` бросает `InvalidOperation`, а `is_weight` обязана
        проверить конечность до сравнения и вернуть `False`, а не уронить
        вызывающего."""
        with pytest.raises(InvalidOperation):
            _ = Decimal("NaN") > 0

        assert is_weight(Decimal("NaN")) is False

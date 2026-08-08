"""Ядро распознавания ставки НДС из шапки ценового блока: без листа, без файла.

Правило и согласие двух шапок проверяются на голых строках (спека Ф4б §4.1,
группы «Распознавание» и «Согласие двух шапок»). Сверка с блоком итогов —
задача 4, здесь её нет.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from parser.sheet import normalized_cell_text
from parser.vat_rate import read_label_rate, resolve_declared_rate

UNIT_COST_LABEL = "Цена за ед. изм., RUB, ОСН, с учетом НДС 20%"
TOTAL_COST_LABEL = "Стоимость всего, RUB, ОСН, с учетом НДС 20%"


class TestReadLabelRate:
    """`read_label_rate` — правило распознавания суффикса одной групповой метки."""

    @pytest.mark.parametrize("label", [UNIT_COST_LABEL, TOTAL_COST_LABEL])
    def test_both_group_labels_with_suffix_are_recognized(self, label):
        """Обе групповые метки в форме, которую заявляют 159-ТУ и 42-ТУ (спека §1.1).

        Головная часть у них разная — «Цена за ед. изм.» против «Стоимость
        всего», — и правило обязано не зависеть от неё вовсе: точное равенство
        полной метке здесь и не годится (§2.2).
        """
        reading = read_label_rate(label)
        assert reading.rate == Decimal("20")
        assert reading.problem is None

    def test_shown_is_normalized_but_keeps_original_case(self):
        """`shown` — метка после `normalized_cell_text`, регистр НЕ трогается."""
        reading = read_label_rate(UNIT_COST_LABEL)
        assert reading.shown == normalized_cell_text(UNIT_COST_LABEL)
        assert "ОСН" in reading.shown

    def test_nbsp_and_double_space_and_other_case_do_not_prevent_recognition(self):
        """Неразрывный пробел, двойной внутренний пробел, другой регистр — не мешают.

        Предпосылка «нормализация схлопывает их» проверяется замером в самом
        тесте, а не на веру: сначала убеждаемся, что `normalized_cell_text`
        действительно убрала NBSP и удвоенные пробелы, и только потом — что
        правило всё равно распознало ставку.
        """
        label = "цена  за\xa0ед. изм., RUB, ОСН,  С УЧЕТОМ\xa0НДС   20%"
        shown = normalized_cell_text(label)
        assert "\xa0" not in shown, "normalized_cell_text обязана убрать неразрывный пробел"
        assert "  " not in shown, "normalized_cell_text обязана схлопнуть двойной пробел"

        reading = read_label_rate(label)
        assert reading.rate == Decimal("20")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Стоимость всего, RUB, ОСН, с учетом НДС 20,5%", Decimal("20.5")),
            ("Стоимость всего, RUB, ОСН, с учетом НДС 20.5%", Decimal("20.5")),
        ],
    )
    def test_comma_and_dot_decimal_separator_give_the_same_value(self, raw, expected):
        assert read_label_rate(raw).rate == expected

    def test_space_before_percent_sign_is_allowed(self):
        reading = read_label_rate("Стоимость всего, RUB, ОСН, с учетом НДС 20 %")
        assert reading.rate == Decimal("20")

    def test_trailing_text_after_percent_is_not_recognized(self):
        """Тест якоря `$`: без него приписка после процента сошла бы за формат."""
        reading = read_label_rate("Стоимость всего, RUB, ОСН, с учетом НДС 20% (двадцать)")
        assert reading.rate is None
        assert reading.problem is not None

    def test_arbitrary_percent_outside_the_construction_is_not_recognized(self):
        """Против варианта «любое N%» — «скидка 5%» ставкой НДС не становится."""
        reading = read_label_rate("Стоимость всего, RUB, скидка 5%")
        assert reading.rate is None

    def test_label_without_suffix_is_not_recognized(self):
        reading = read_label_rate("Цена за ед. изм., RUB, ОСН")
        assert reading.rate is None

    def test_without_vat_wording_gives_none_not_zero(self):
        """«Без НДС» — утверждение о ценах, а не о ставке; ноль не подставляется."""
        reading = read_label_rate("Стоимость всего, RUB, без НДС")
        assert reading.rate is None

    def test_rate_above_range_is_rejected_and_problem_carries_the_actual_number(self):
        reading = read_label_rate("Стоимость всего, RUB, ОСН, с учетом НДС 120%")
        assert reading.rate is None
        assert reading.problem is not None
        assert "120" in reading.problem

    def test_zero_percent_is_a_declared_zero_not_an_absence(self):
        """Ноль ЗАЯВЛЕННЫЙ — единственный законный способ получить в колонке ноль."""
        reading = read_label_rate("Стоимость всего, RUB, ОСН, с учетом НДС 0%")
        assert reading.rate == Decimal(0)
        assert reading.problem is None


class TestResolveDeclaredRate:
    """`resolve_declared_rate` — согласие двух групповых шапок одного блока (спека §2.3)."""

    def test_both_headers_agree_on_the_same_rate(self):
        result = resolve_declared_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL)
        assert result.rate == Decimal("20")
        assert result.warnings == []

    def test_both_headers_are_silent(self):
        result = resolve_declared_rate(
            "Цена за ед. изм., RUB, ОСН",
            "Стоимость всего, RUB, ОСН",
        )
        assert result.rate is None
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert "не получена" in warning
        assert "Цена за ед. изм., RUB, ОСН" in warning
        assert "Стоимость всего, RUB, ОСН" in warning

    def test_only_unit_cost_declares_a_rate(self):
        """У ветки два входа: этот тест снимает защиту только со стороны `unit_cost`."""
        result = resolve_declared_rate(UNIT_COST_LABEL, "Стоимость всего, RUB, ОСН")
        assert result.rate is None
        assert len(result.warnings) == 1
        assert "не получена" in result.warnings[0]

    def test_only_total_cost_declares_a_rate(self):
        """Отдельный тест от предыдущего — снятие защиты со стороны `total_cost`."""
        result = resolve_declared_rate("Цена за ед. изм., RUB, ОСН", TOTAL_COST_LABEL)
        assert result.rate is None
        assert len(result.warnings) == 1
        assert "не получена" in result.warnings[0]

    def test_out_of_range_value_in_one_header_is_treated_as_not_obtained(self):
        result = resolve_declared_rate(
            UNIT_COST_LABEL,
            "Стоимость всего, RUB, ОСН, с учетом НДС 120%",
        )
        assert result.rate is None
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert "не получена" in warning
        assert "120" in warning

    def test_headers_disagree_and_the_warning_names_both_values(self):
        result = resolve_declared_rate(
            "Цена за ед. изм., RUB, ОСН, с учетом НДС 20%",
            "Стоимость всего, RUB, ОСН, с учетом НДС 18%",
        )
        assert result.rate is None
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert "разные ставки" in warning
        assert "20" in warning
        assert "18" in warning

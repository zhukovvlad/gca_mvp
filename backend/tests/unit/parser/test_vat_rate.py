"""Ядро распознавания ставки НДС из шапки ценового блока: без листа, без файла.

Правило и согласие двух шапок проверяются на голых строках (спека Ф4б §4.1,
группы «Распознавание» и «Согласие двух шапок»). Сверка с блоком итогов и
сборка итога (`check_rate_against_summary`, `build_vat_rate`) — задача 4,
группа «Сверка».
"""
from __future__ import annotations

from decimal import Decimal, getcontext

import pytest

from parser.constants import JSON_KEY_TOTAL_COST_EXCLUDING_VAT, JSON_KEY_VAT_AMOUNT
from parser.sheet import normalized_cell_text
from parser.vat_rate import (
    build_vat_rate,
    check_rate_against_summary,
    read_label_rate,
    resolve_declared_rate,
)

UNIT_COST_LABEL = "Цена за ед. изм., RUB, ОСН, с учетом НДС 20%"
TOTAL_COST_LABEL = "Стоимость всего, RUB, ОСН, с учетом НДС 20%"

#: Денежные колонки блока `total_cost`; тот же перечень и порядок, что
#: `_MONEY_COLUMNS` в `summary_block` (спека §2.4).
MONEY_COLUMNS = ("materials", "works", "indirect_costs", "total")


def _cost_line(**amounts) -> dict:
    """Строка блока итогов в форме, которую отдаёт `parse_contractor_row`."""
    return {"total_cost": {name: amounts.get(name) for name in MONEY_COLUMNS}}


def _pair(vat, excluding) -> dict:
    """Блок итогов из ДВУХ строк, нужных сверке; одно и то же значение во все колонки.

    «Пара», а не «тройка»: сверке нужны ровно два значения блока итогов, а
    заявленная ставка присоединяется к ним отдельным аргументом (спека §2.6).
    """
    return {
        JSON_KEY_VAT_AMOUNT: _cost_line(**{name: vat for name in MONEY_COLUMNS}),
        JSON_KEY_TOTAL_COST_EXCLUDING_VAT: _cost_line(**{name: excluding for name in MONEY_COLUMNS}),
    }


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


class TestCheckRateAgainstSummary:
    """`check_rate_against_summary` — сверка заявленной ставки с блоком итогов (спека §2.4, §2.6)."""

    def test_all_four_ratios_within_tolerance_are_silent(self):
        report = check_rate_against_summary(Decimal("20"), _pair("20", "100"))
        assert report.mismatched == []
        assert report.unverified == []

    def test_deviation_beyond_tolerance_names_the_column_and_carries_three_numbers(self):
        """Числа подобраны так, что заявленная (20), выведенная (32) и разница (12)
        не являются подстроками друг друга — иначе утверждение прошло бы вакуозно."""
        lines = {
            JSON_KEY_VAT_AMOUNT: _cost_line(materials="32", works="20", indirect_costs="20", total="20"),
            JSON_KEY_TOTAL_COST_EXCLUDING_VAT: _cost_line(
                materials="100", works="100", indirect_costs="100", total="100"
            ),
        }
        report = check_rate_against_summary(Decimal("20"), lines)
        assert len(report.mismatched) == 1
        assert "materials" in report.mismatched[0]
        for number in ("20", "32", "12"):
            assert number in report.mismatched[0]
        assert report.unverified == []

    def test_deviation_exactly_at_the_tolerance_boundary_is_not_a_mismatch(self):
        """База `100`, НДС `20.01` при заявленной `20` — разница ровно `0.01` (замерено)."""
        report = check_rate_against_summary(Decimal("20"), _pair("20.01", "100"))
        assert report.mismatched == []
        assert report.unverified == []

    def test_deviation_just_beyond_the_tolerance_boundary_is_a_mismatch(self):
        """Тот же вход, разница `0.0101` — на волос за границей допуска.

        Пара с предыдущим тестом: без неё допуск проверялся бы только с одной
        стороны сравнения.
        """
        report = check_rate_against_summary(Decimal("20"), _pair("20.0101", "100"))
        assert len(report.mismatched) == len(MONEY_COLUMNS)
        assert report.unverified == []

    def test_both_values_in_a_column_blank_is_silent(self):
        report = check_rate_against_summary(Decimal("20"), _pair(None, None))
        assert report.mismatched == []
        assert report.unverified == []

    def test_zero_over_zero_is_silent(self):
        """Нет ни налога, ни базы — противоречия нет."""
        report = check_rate_against_summary(Decimal("20"), _pair("0", "0"))
        assert report.mismatched == []
        assert report.unverified == []

    def test_zero_base_with_nonzero_vat_is_a_mismatch(self):
        """Налог без базы — противоречие в файле, а не «нечем проверить»."""
        report = check_rate_against_summary(Decimal("20"), _pair("50", "0"))
        assert len(report.mismatched) == len(MONEY_COLUMNS)
        assert report.unverified == []

    def test_only_one_side_of_the_pair_is_filled_is_unverified(self):
        report = check_rate_against_summary(Decimal("20"), _pair("20", None))
        assert report.mismatched == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    @pytest.mark.parametrize("bad", ["n/a", "NaN", "sNaN", "Infinity"])
    def test_unusable_value_is_unverified_with_the_actual_value_and_does_not_raise(self, bad):
        report = check_rate_against_summary(Decimal("20"), _pair(bad, "100"))
        assert report.mismatched == []
        assert len(report.unverified) == len(MONEY_COLUMNS)
        assert bad in report.unverified[0]

    def test_opposite_infinities_in_the_pair_are_unverified_and_do_not_raise(self):
        """`-Infinity` и `Infinity` в паре — оба негодны, деление их не бросает наружу."""
        report = check_rate_against_summary(Decimal("20"), _pair("Infinity", "-Infinity"))
        assert report.mismatched == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_empty_string_is_blank_not_unusable(self):
        report = check_rate_against_summary(Decimal("20"), _pair("", ""))
        assert report.mismatched == []
        assert report.unverified == []

    def test_division_overflow_is_unverified_and_does_not_raise(self):
        """Годные по `is_finite()`, но огромные значения; деление бросает `Overflow`.

        Тот же класс входа, что уронил разбор всей сметы в Ф4a
        (`check_arithmetic`, сложение `1` + `1e999999999`); здесь его роняет
        деление, а не сложение, и сверка одной ставки не имеет права унести с
        собой весь разбор. Замерено отдельным скриптом перед написанием теста:
        `Decimal("1E+999999999") / Decimal("1") * 100` в контексте `prec=100`
        с трапом на `Overflow` бросает `decimal.Overflow`.
        """
        report = check_rate_against_summary(Decimal("20"), _pair("1E+999999999", "1"))
        assert report.mismatched == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_global_decimal_context_is_left_untouched(self):
        """Сверка идёт в локальном контексте — глобальный контекст приложения не трогается."""
        before_prec = getcontext().prec
        before_traps = dict(getcontext().traps)

        check_rate_against_summary(Decimal("20"), _pair("20", "100"))
        check_rate_against_summary(Decimal("20"), _pair("1E+999999999", "1"))

        assert getcontext().prec == before_prec
        assert dict(getcontext().traps) == before_traps

    def test_missing_excluding_vat_key_means_no_check_at_all(self):
        """Двухстрочный блок итогов (ключа `total_cost_excluding_vat` нет вовсе):
        о его неполноте уже сказали предупреждения Ф4a — второе сообщение не нужно."""
        lines = {JSON_KEY_VAT_AMOUNT: _cost_line(materials="20", works="20", indirect_costs="20", total="20")}
        report = check_rate_against_summary(Decimal("20"), lines)
        assert report.mismatched == []
        assert report.unverified == []

    def test_missing_both_keys_means_no_check_at_all(self):
        report = check_rate_against_summary(Decimal("20"), {})
        assert report.mismatched == []
        assert report.unverified == []


class TestBuildVatRate:
    """`build_vat_rate` — сборка итога Ф4б: согласие шапок, затем сверка (спека §2.7)."""

    def test_all_four_ratios_within_tolerance_is_silent(self):
        result = build_vat_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL, _pair("20", "100"))
        assert result.rate == Decimal("20")
        assert result.warnings == []

    def test_mismatch_warning_is_added_when_summary_disagrees(self):
        lines = {
            JSON_KEY_VAT_AMOUNT: _cost_line(materials="32", works="20", indirect_costs="20", total="20"),
            JSON_KEY_TOTAL_COST_EXCLUDING_VAT: _cost_line(
                materials="100", works="100", indirect_costs="100", total="100"
            ),
        }
        result = build_vat_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL, lines)
        assert result.rate == Decimal("20")
        assert len(result.warnings) == 1
        assert "расходится" in result.warnings[0]
        assert "materials" in result.warnings[0]

    def test_unverified_warning_is_added_when_summary_cannot_be_checked(self):
        result = build_vat_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL, _pair("20", None))
        assert result.rate == Decimal("20")
        assert len(result.warnings) == 1
        assert "не проведена" in result.warnings[0]

    def test_mismatch_and_unverified_are_two_separate_warnings(self):
        """Спека §2.7: это ДВА предупреждения, ни одно не несёт маркер другого."""
        lines = {
            JSON_KEY_VAT_AMOUNT: _cost_line(materials="32", works=None, indirect_costs="20", total="20"),
            JSON_KEY_TOTAL_COST_EXCLUDING_VAT: _cost_line(
                materials="100", works="100", indirect_costs="100", total="100"
            ),
        }
        result = build_vat_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL, lines)
        mismatch = [text for text in result.warnings if "расходится" in text]
        unverified = [text for text in result.warnings if "не проведена" in text]
        assert len(mismatch) == 1
        assert len(unverified) == 1
        assert "не проведена" not in mismatch[0]
        assert "расходится" not in unverified[0]

    def test_rate_not_obtained_short_circuits_the_check_even_with_a_full_valid_summary(self):
        """Некаскадирование (спека §2.7): ставка не получена — сверка не запускается
        вовсе, даже когда блок итогов полон и годен. Это в точности форма fixture
        до правки §2.10, проверяемая здесь синтетически."""
        silent_unit_label = "Цена за ед. изм., RUB, ОСН"
        silent_total_label = "Стоимость всего, RUB, ОСН"
        result = build_vat_rate(silent_unit_label, silent_total_label, _pair("20", "100"))
        assert result.rate is None
        assert len(result.warnings) == 1
        assert "не получена" in result.warnings[0]
        assert "не проведена" not in result.warnings[0]

    def test_two_row_summary_block_declares_a_rate_but_gives_no_warning_at_all(self):
        """Ключа `total_cost_excluding_vat` нет вовсе (форма 449-ТУ до Ф4a), ставка
        при этом заявлена шапками — сверка не выполняется и никакого предупреждения
        не даёт (спека §2.6)."""
        lines = {JSON_KEY_VAT_AMOUNT: _cost_line(materials="20", works="20", indirect_costs="20", total="20")}
        result = build_vat_rate(UNIT_COST_LABEL, TOTAL_COST_LABEL, lines)
        assert result.rate == Decimal("20")
        assert result.warnings == []

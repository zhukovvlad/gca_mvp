"""Чистый расчёт свода по этапам без БД (спека §2.5–§2.7).

Каждая клетка матрицы §2.6 — отдельный случай; `absent`, `not_evaluated` и
`removed` — три разных факта, и тесты не сводят их к «пусто/есть».
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

import pytest

from services import stage_summary as ss
from services.category_rollup import SOURCE_ADDITIONAL_WORKS, SOURCE_POSITIONS, CategoryRef, DirectTotals

D = Decimal


class TestCellStates:
    def test_nonzero_is_amount_and_zero_after_amount_is_removed(self):
        assert ss.cell_states([D("10"), D("0")]) == [ss.STATE_AMOUNT, ss.STATE_REMOVED]

    def test_zero_never_priced_before_is_not_evaluated(self):
        assert ss.cell_states([D("0"), D("0")]) == [ss.STATE_NOT_EVALUATED, ss.STATE_NOT_EVALUATED]

    def test_second_zero_in_a_row_stays_removed_not_not_evaluated(self):
        """«По предыдущему шагу» пометило бы вторую нулевую как «не оценивалась» — §2.5."""
        assert ss.cell_states([D("10"), D("0"), D("0")]) == [ss.STATE_AMOUNT, ss.STATE_REMOVED, ss.STATE_REMOVED]

    def test_none_is_absent_and_does_not_count_as_priced(self):
        assert ss.cell_states([None, D("0"), D("5")]) == [ss.STATE_ABSENT, ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT]

    def test_negative_sum_is_amount(self):
        assert ss.cell_states([D("-3")]) == [ss.STATE_AMOUNT]

    def test_path_is_only_the_given_columns(self):
        """Путь = выбранные колонки (§2.6): история невыбранных сюда не попадает по построению."""
        assert ss.cell_states([D("0"), D("7")]) == [ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT]


_NO_AMOUNT = (ss.STATE_REMOVED, ss.STATE_NOT_EVALUATED, ss.STATE_ABSENT)


class TestChangeMatrix:
    def c(self, ps, cs, pv, cv, reason=None):
        return ss.change_between(ps, cs, pv, cv, unavailable_reason=reason)

    def test_amount_to_amount_positive_base_is_percent(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("200"), D("190"))
        assert ch.kind == ss.KIND_PERCENT and ch.value == D("-5") and ch.direction == ss.DIR_DOWN

    def test_amount_to_amount_negative_base_is_abs_only(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-100"), D("-80"))
        assert ch.kind == ss.KIND_ABS_ONLY and ch.value == D("20") and ch.direction == ss.DIR_UP

    def test_amount_to_amount_negative_base_downward(self):
        """Оба знака у величины, чей знак участвует в решении (AGENTS §11)."""
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-100"), D("-120"))
        assert ch.kind == ss.KIND_ABS_ONLY and ch.value == D("-20") and ch.direction == ss.DIR_DOWN

    def test_equal_amounts_are_flat_not_up(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("50"), D("50"))
        assert ch.kind == ss.KIND_PERCENT and ch.value == D("0") and ch.direction == ss.DIR_FLAT

    def test_amount_to_removed(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_REMOVED, D("50"), D("0"))
        assert ch.kind == ss.KIND_REMOVED and ch.value is None and ch.direction is None

    def test_amount_to_absent_is_disappeared_not_removed(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_ABSENT, D("50"), None)
        assert ch.kind == ss.KIND_DISAPPEARED and ch.value is None and ch.direction is None and ch.reason is None

    def test_not_evaluated_to_amount_and_absent_to_amount_are_appeared(self):
        ch1 = self.c(ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT, D("0"), D("5"))
        assert ch1.kind == ss.KIND_APPEARED and ch1.value is None and ch1.direction is None and ch1.reason is None
        ch2 = self.c(ss.STATE_ABSENT, ss.STATE_AMOUNT, None, D("5"))
        assert ch2.kind == ss.KIND_APPEARED and ch2.value is None and ch2.direction is None and ch2.reason is None

    def test_removed_to_amount_is_reappeared(self):
        assert self.c(ss.STATE_REMOVED, ss.STATE_AMOUNT, D("0"), D("5")).kind == ss.KIND_REAPPEARED

    @pytest.mark.parametrize("ps,cs", [(a, b) for a in _NO_AMOUNT for b in _NO_AMOUNT])
    def test_all_nine_transitions_without_amounts_are_none_with_reason(self, ps, cs):
        """Матрица §2.6 целиком: 3 × 3 переходов между состояниями без суммы — все `none`."""
        ch = self.c(ps, cs, None, None)
        assert ch.kind == ss.KIND_NONE and ch.value is None and ch.direction is None
        assert ch.reason == ss.REASON_NO_AMOUNTS

    def test_unknown_vat_base_wins_over_states(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, None, None, reason=ss.REASON_UNKNOWN_VAT_BASE)
        assert ch.kind == ss.KIND_NONE and ch.reason == ss.REASON_UNKNOWN_VAT_BASE


class TestDivisionIsUnreachableForNonPositiveBase:
    def test_percent_change_is_not_called_when_base_is_zero_or_negative(self):
        """Утверждение о ПОТОКЕ (docs/insights/data-flow-assertions-for-order.md):
        при базе ≤ 0 деление не вызывается вовсе — не «не падает», а не зовётся."""
        with patch.object(ss, "percent_change", wraps=ss.percent_change) as spy:
            ss.change_between(ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT, D("0"), D("5"), unavailable_reason=None)
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-1"), D("5"), unavailable_reason=None)
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("0"), D("5"), unavailable_reason=None)
        assert spy.call_count == 0

    def test_percent_change_is_called_exactly_once_for_positive_base(self):
        with patch.object(ss, "percent_change", wraps=ss.percent_change) as spy:
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("10"), D("5"), unavailable_reason=None)
        assert spy.call_count == 1

    def test_percent_change_refuses_non_positive_base(self):
        with pytest.raises(AssertionError):
            ss.percent_change(D("0"), D("5"))


class TestContribution:
    def test_number_when_both_ends_have_amounts(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("100"), D("80"), unavailable_reason=None)
        assert c.value == D("-20") and c.direction == ss.DIR_DOWN and c.reason is None

    def test_zero_states_count_as_zero_money(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_REMOVED, D("100"), D("0"), unavailable_reason=None)
        assert c.value == D("-100")

    def test_absent_endpoint_gives_null_with_reason(self):
        c = ss.contribution_between(ss.STATE_ABSENT, ss.STATE_AMOUNT, None, D("5"), unavailable_reason=None)
        assert c.value is None and c.direction is None and c.reason == ss.REASON_ABSENT_ENDPOINT

    def test_unknown_vat_base_gives_null_with_reason(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, None, None,
                                    unavailable_reason=ss.REASON_UNKNOWN_VAT_BASE)
        assert c.value is None and c.reason == ss.REASON_UNKNOWN_VAT_BASE

    def test_none_shown_on_removed_end_means_zero_money_not_unknown(self):
        """`removed`/`not_evaluated` несут `shown = None`, но их деньги — ровно ноль
        (§2.7): `absent` — единственное состояние, где отсутствие суммы значит
        «неизвестно», и оно уже отфильтровано веткой `absent_endpoint` выше. Здесь
        `None` на невыбывшем конце — это ноль, и `(x or Decimal(0))` обязан
        сработать на живом пути Task 2, где `removed`/`not_evaluated` реально
        приходят с `shown = None`."""
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_REMOVED, D("100"), None, unavailable_reason=None)
        assert c.value == D("-100") and c.direction == ss.DIR_DOWN and c.reason is None

    def test_none_shown_on_not_evaluated_start_means_zero_money_not_unknown(self):
        """Зеркальный случай: `not_evaluated` в начале пути — тоже ноль денег, не
        `null`; `absent` уже обработан раньше и сюда не попадает."""
        c = ss.contribution_between(ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT, None, D("30"), unavailable_reason=None)
        assert c.value == D("30") and c.direction == ss.DIR_UP and c.reason is None


def ref(id_, code, parent_id=None, sort_order=0):
    return CategoryRef(id=id_, code=code, title=f"Статья {code}", parent_id=parent_id, is_bucket=False, sort_order=sort_order)


def dt_(amount, rows=1):
    return DirectTotals(amount=None if amount is None else D(amount), row_count=rows,
                        rows_with_amount=rows if amount is not None else 0, rows_not_finite=0)


def col(offer_id, stage_no, rate, direct, file_total=None, overrides=0):
    return ss.ColumnInput(offer_id=offer_id, estimate_id=offer_id * 10, round_id=stage_no, stage_no=stage_no,
                          label=None, held_on=None, vat_rate_base=None if rate is None else D(rate), direct=direct,
                          file_total_gross=None if file_total is None else D(file_total),
                          overrides_count=overrides, overrides_last_at=None)


CATS = [ref(6, "6", sort_order=6), ref(2, "2", sort_order=2), ref(26, "2.6", parent_id=2, sort_order=1)]


class TestTaxBasis:
    def test_single_known_rate_is_gross(self):
        tb = ss.pick_tax_basis([D("20"), D("20"), None])
        assert tb.basis == ss.TAX_GROSS and tb.reason == ss.TAX_REASON_SINGLE and tb.rates_by_column is None

    def test_mixed_known_rates_is_net_with_rates_listed(self):
        tb = ss.pick_tax_basis([D("20"), D("0")])
        assert tb.basis == ss.TAX_NET and tb.reason == ss.TAX_REASON_MIXED and tb.rates_by_column == [D("20"), D("0")]

    def test_no_known_rates_is_none(self):
        tb = ss.pick_tax_basis([None, None])
        assert tb.basis == ss.TAX_NONE and tb.reason == ss.TAX_REASON_NO_KNOWN

    def test_unknown_column_is_unavailable_on_gross_axis(self):
        """20 % + неизвестная: известная валовая, неизвестная — без суммы (§2.8)."""
        tb = ss.pick_tax_basis([D("20"), None])
        assert tb.basis == ss.TAX_GROSS
        assert ss.to_shown(D("120"), D("20"), tb) == D("120")
        assert ss.to_shown(D("120"), None, tb) is None

    def test_net_axis_divides_by_own_rate(self):
        tb = ss.pick_tax_basis([D("20"), D("0")])
        assert ss.to_shown(D("120"), D("20"), tb) == D("100")
        assert ss.to_shown(D("100"), D("0"), tb) == D("100")


class TestComputeSummary:
    def two_columns(self):
        c1 = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("120")}, 2: {SOURCE_POSITIONS: dt_("60")},
                              26: {SOURCE_POSITIONS: dt_("12")}, None: {SOURCE_POSITIONS: dt_("6")}}, file_total="198")
        c2 = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("96"), SOURCE_ADDITIONAL_WORKS: dt_("24")},
                              2: {SOURCE_POSITIONS: dt_("0")}, None: {SOURCE_POSITIONS: dt_("0")}}, file_total="130")
        return [c1, c2]

    def test_total_is_sum_of_roots_plus_unallocated_not_file_total(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.columns[0].total_shown == D("198")      # 120 + 72 (60+12) + 6
        assert r.columns[1].total_shown == D("120")      # 120 + 0 + 0; файл говорит 130 → не сходится
        assert r.columns[1].convergence.converged is False and r.columns[1].convergence.delta == D("-10")
        assert r.columns[0].convergence.converged is True

    def test_convergence_null_when_file_total_missing(self):
        cols = self.two_columns()
        cols[0] = ss.ColumnInput(**{**cols[0].__dict__, "file_total_gross": None})
        r = ss.compute_summary(cols, CATS)
        assert r.columns[0].convergence.converged is None
        assert r.columns[0].convergence.reason == ss.CONV_FILE_TOTAL_UNAVAILABLE

    def test_convergence_is_computed_in_gross_even_on_net_axis(self):
        cols = self.two_columns()
        cols[1] = ss.ColumnInput(**{**cols[1].__dict__, "vat_rate_base": D("0")})
        r = ss.compute_summary(cols, CATS)
        assert r.display.basis == ss.TAX_NET
        assert r.columns[0].total_shown == D("165")      # 198 / 1.2
        assert r.columns[0].convergence.categories_sum_gross == D("198") and r.columns[0].convergence.converged is True

    def test_additional_works_amount_independent_of_state(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("-24"), SOURCE_ADDITIONAL_WORKS: dt_("24")}})
        r = ss.compute_summary([c, c], CATS)
        row6 = next(row for row in r.rows if row.ref.id == 6)
        cell = row6.cells[0]
        assert cell.state == ss.STATE_NOT_EVALUATED and cell.shown is None
        assert cell.additional_works_shown == D("24")

    def test_additional_works_at_child_are_visible_in_parent(self):
        c = col(1, 1, "20", {2: {SOURCE_POSITIONS: dt_("10")},
                              26: {SOURCE_POSITIONS: dt_("5"), SOURCE_ADDITIONAL_WORKS: dt_("7")}})
        r = ss.compute_summary([c, c], CATS)
        two = next(row for row in r.rows if row.ref.id == 2)
        assert two.cells[0].additional_works_shown == D("7")
        assert two.children[0].cells[0].additional_works_shown == D("7")
        six = next(row for row in r.rows if row.ref.id == 6)
        assert six.cells[0].additional_works_shown is None

    def test_rows_sorted_by_classifier_sort_order_within_level(self):
        """§2.13 (ревизия 28.08.2026): порядок — `sort_order` классификатора,
        вклад на него не влияет ВОВСЕ, и пустой вклад не уезжает в конец.

        Фикстура подобрана так, что прежнее правило (по убыванию |вклада|,
        пустые — после числовых) дало бы ДРУГОЙ порядок на КАЖДОМ из трёх
        утверждений ниже — иначе тест был бы зелёным при обоих правилах и не
        проверял бы ничего (`docs/insights/verifying-guards.md`, слой 12 — ложную
        зелень создаёт ВЫБОР ЧИСЕЛ в фикстуре):

        | уровень | по классификатору (сейчас) | по вкладу (прежнее правило)       |
        |---|---|---|
        | корни   | `1` (10), `3` (15), `2` (20) | `2` (|−500|), `1` (|+120|), `3` (null) |
        | дети `1`| `1.1` (10), `1.2` (20)       | `1.2` (|+110|), `1.1` (|+10|)          |
        """
        cats = [ref(1, "1", sort_order=10), ref(3, "3", sort_order=15), ref(2, "2", sort_order=20),
                ref(11, "1.1", parent_id=1, sort_order=10), ref(12, "1.2", parent_id=1, sort_order=20)]
        c1 = col(1, 1, "20", {11: {SOURCE_POSITIONS: dt_("10")}, 12: {SOURCE_POSITIONS: dt_("90")},
                              2: {SOURCE_POSITIONS: dt_("1000")}})
        c2 = col(2, 2, "20", {11: {SOURCE_POSITIONS: dt_("20")}, 12: {SOURCE_POSITIONS: dt_("200")},
                              2: {SOURCE_POSITIONS: dt_("500")}})
        r = ss.compute_summary([c1, c2], cats)

        assert [row.ref.code for row in r.rows] == ["1", "3", "2"]
        one = next(row for row in r.rows if row.ref.code == "1")
        assert [child.ref.code for child in one.children] == ["1.1", "1.2"]

        # Предпосылка самой фикстуры: вклады действительно РАЗЛИЧАЮТ два правила
        # (иначе таблица в докстроке — рассуждение, а не факт прогона).
        by_code = {row.ref.code: row.contribution.value for row in r.rows}
        assert by_code["1"] == D("120") and by_code["2"] == D("-500") and by_code["3"] is None
        assert [child.contribution.value for child in one.children] == [D("10"), D("110")]

    def test_child_order_survives_columns_that_differ_in_children(self):
        """Вход, на котором сортировка детей НАГРУЖЕНА, а не совпадает с
        порядком прихода.

        `build_tree` отдаёт детей каждой колонки уже по `sort_order`, поэтому на
        обычной фикстуре, где во всех колонках одни и те же дети, снятие
        `sorted(...)` в `_row` не поменяло бы ничего — тест был бы зелёным при
        мёртвой сортировке (`docs/insights/verifying-guards.md`, слой 7). Здесь
        колонки РАЗНЫЕ по составу детей: в первой есть только «1.2», во второй
        «1.1» и «1.2». Склейка `child_refs` идёт словарём в порядке первой
        встречи и даёт «1.2», «1.1» — порядок, которого классификатор не знает.
        """
        cats = [ref(1, "1", sort_order=10),
                ref(11, "1.1", parent_id=1, sort_order=10), ref(12, "1.2", parent_id=1, sort_order=20)]
        c1 = col(1, 1, "20", {12: {SOURCE_POSITIONS: dt_("90")}})
        c2 = col(2, 2, "20", {11: {SOURCE_POSITIONS: dt_("20")}, 12: {SOURCE_POSITIONS: dt_("70")}})
        r = ss.compute_summary([c1, c2], cats)

        one = next(row for row in r.rows if row.ref.code == "1")
        assert [child.ref.code for child in one.children] == ["1.1", "1.2"]

    def test_roots_always_present_children_only_with_nonzero_somewhere(self):
        cats = CATS + [ref(7, "7", sort_order=7), ref(27, "2.7", parent_id=2, sort_order=2)]
        r = ss.compute_summary(self.two_columns(), cats)
        assert {row.ref.code for row in r.rows} == {"2", "6", "7"}
        two = next(row for row in r.rows if row.ref.code == "2")
        assert [c.ref.code for c in two.children] == ["2.6"]        # 2.7 без строк — скрыт

    def test_child_with_explicit_zero_everywhere_is_hidden(self):
        c = col(1, 1, "20", {2: {SOURCE_POSITIONS: dt_("10")}, 26: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([c, c], CATS)
        two = next(row for row in r.rows if row.ref.code == "2")
        assert two.children == []

    def test_unallocated_row_bargain_has_no_percent_but_contribution_is_number(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.unallocated.is_unallocated
        assert r.unallocated.bargain.kind == ss.KIND_NONE and r.unallocated.bargain.reason == ss.REASON_UNALLOCATED
        assert r.unallocated.contribution.value == D("-6")

    def test_kpi_counts_nonzero_roots_of_last_column_both_signs(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("5")}, 2: {SOURCE_POSITIONS: dt_("-5")}})
        r = ss.compute_summary([c, c], CATS)
        assert r.kpi.categories_with_amount == 2 and r.kpi.categories_total == 2

    def test_track_heights_are_server_side_and_max_is_100(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.track.available is True
        assert r.columns[0].bar_height_pct == D("100")
        # RESOLUTION A: RHS `D("120") / D("198") * 100` вычислялась бы в амбиентном
        # 28-значном контексте decimal, а реализация делит под prec=ARITHMETIC_PRECISION
        # (=100, см. parser/summary_block.py:154) — хвосты расходятся. Сверяем то, что
        # реально уходит клиенту: квантование до 0.1 (§2.12).
        assert r.columns[1].bar_height_pct.quantize(D("0.1"), rounding=ROUND_HALF_UP) == D("60.6")

    def test_track_unavailable_on_non_positive_total(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("-5")}})
        r = ss.compute_summary([c, c], CATS)
        assert r.track.available is False and r.track.reason == ss.TRACK_NON_POSITIVE

    def test_unknown_column_does_not_disable_track_but_has_no_bar(self):
        cols = self.two_columns()
        cols[1] = ss.ColumnInput(**{**cols[1].__dict__, "vat_rate_base": None})
        r = ss.compute_summary(cols, CATS)
        assert r.track.available is True
        assert r.columns[1].bar_height_pct is None and r.columns[1].vat_state == ss.VAT_UNKNOWN
        assert r.columns[1].total_shown is None
        assert all(cell.unavailable_reason == ss.REASON_UNKNOWN_VAT_BASE for cell in r.rows[0].cells[1:2])
        assert r.columns[1].total_change.kind == ss.KIND_NONE

    def test_all_unknown_means_basis_none_and_no_comparable_totals(self):
        """Это проверка ФОРМЫ, не значения: состояние ячейки лежит в множестве
        валидных состояний и просто ПЕРЕЖИЛО отсутствие суммы (ни одна ставка не
        известна), а не что это состояние ПРАВИЛЬНОЕ — за правильность состояний
        отвечают тесты `TestCellStates` выше."""
        cols = [ss.ColumnInput(**{**c.__dict__, "vat_rate_base": None}) for c in self.two_columns()]
        r = ss.compute_summary(cols, CATS)
        assert r.display.basis == ss.TAX_NONE
        assert r.track.available is False and r.track.reason == ss.TRACK_NO_COMPARABLE
        assert r.rows[0].cells[0].state in (ss.STATE_AMOUNT, ss.STATE_NOT_EVALUATED)   # состояния на месте

    def test_first_column_change_is_none_with_first_column_reason(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.rows[0].cells[0].change.kind == ss.KIND_NONE
        assert r.rows[0].cells[0].change.reason == ss.REASON_FIRST_COLUMN

    def test_rows_with_no_price_are_not_absent(self):
        """RESOLUTION B: строка есть, но без цены (row_count=1, rows_with_amount=0)
        — не то же самое, что строки нет вовсе (row_count=0). `_node_inputs` обязан
        сохранять инвариант Task 1 (`gross is None` ⟺ `row_count == 0`), иначе
        «нет в файле» подменяется «не оценивалась»."""
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_(None)}})
        r = ss.compute_summary([c, c], CATS)
        row6 = next(row for row in r.rows if row.ref.id == 6)
        cell = row6.cells[0]
        assert cell.state != ss.STATE_ABSENT
        assert cell.rows.row_count == 1

    def test_total_row_counters_are_real_when_rows_exist(self):
        """Task 9 (внешнее ревью PR #34): счётчики строк «Итого» — НАСТОЯЩИЕ,
        не нули, когда за колонкой реально стоят строки. `TotalCell` не несёт
        `state` вовсе (§2.16, ревизия 28.08.2026) — инвариант «state = absent
        ⟺ row_count = 0» относится только к `Cell` (статьям); у агрегата
        проверяется прямо то, что важно: счётчик и сумма."""
        r = ss.compute_summary(self.two_columns(), CATS)
        for cell in r.total_cells:
            assert cell.rows.row_count > 0
            assert cell.shown is not None
            assert cell.unavailable_reason is None

    def test_total_amount_is_zero_not_absent_when_column_has_no_rows_at_all(self):
        """Заменяет обсолетный `test_total_is_absent_with_zero_count_when_column_has_no_rows_at_all`
        (Task 9, ревизия 28.08.2026 по внешнему ревью PR #34): у `TotalCell`
        состояния нет, и при известной оси сумма ВСЕГДА число, включая пустую
        колонку без единой строки — единственный путь к `None` — неизвестная
        база НДС, а не отсутствие строк. Зеркальная сторона того же случая —
        смета колонки без единой строки ни в одной статье, ни в
        «Нераспределённом» (`_validate_payload` не требует хотя бы одной
        позиции у предложения — вырожденный случай достижим через реальный
        импорт, интеграционный сосед — `TestZeroTotalWithRealRows` ниже и
        `test_column_without_a_single_row_still_yields_zero_total` в
        `test_stage_summary_api.py`)."""
        c = col(1, 1, "20", {})
        r = ss.compute_summary([c, c], CATS)
        for cell in r.total_cells:
            assert cell.rows.row_count == 0
            assert cell.rows.rows_with_amount == 0
            assert cell.rows.rows_not_finite == 0
            assert cell.shown == D("0")
            assert cell.unavailable_reason is None


class TestZeroTotalWithRealRows:
    """Task 9 (внешнее ревью PR #34, §2.16 ревизия 28.08.2026): дефект, который
    исправляет эта задача — на нулевом итоге с живыми строками `TotalCell`
    раньше нёс состояние («снято»/«не оценивалась») ВМЕСТЕ с суммой `"0"`,
    хотя статья в том же ответе честно отдавала пустую сумму. Два случая,
    названных ревью явно, плюс паритет колонки и `TotalCell`, плюс «единственный
    путь к `None`» — оба направления."""

    def test_first_column_zero_total_with_real_rows_is_zero_not_stateful(self):
        """Первая колонка, итог которой ноль при живых строках позади: сумма —
        ноль, а не отсутствие; состояния к ней уже неприменимы структурно (у
        `TotalCell` нет поля `state` вовсе — здесь просто нет способа его
        «перепутать»)."""
        zero_first = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("0")}, 2: {SOURCE_POSITIONS: dt_("0")}})
        priced_second = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("30")}, 2: {SOURCE_POSITIONS: dt_("20")}})
        r = ss.compute_summary([zero_first, priced_second], CATS)

        assert r.total_cells[0].shown == D("0")
        assert r.total_cells[0].unavailable_reason is None
        assert r.total_cells[0].rows.row_count > 0
        assert r.columns[0].total_shown == D("0")
        # первая колонка — всегда `first_column`, независимо от того, что сумма ноль
        assert r.total_cells[0].change.kind == ss.KIND_NONE
        assert r.total_cells[0].change.reason == ss.REASON_FIRST_COLUMN

    def test_nonzero_total_becoming_zero_is_numeric_percent_and_disables_track(self):
        """Ненулевой итог, ставший нулём: сумма — ноль, изменение считается
        ЧИСЛЕННО (положительная предыдущая величина — процент, здесь −100 %,
        не матрицей состояний), трасса выключается неположительным итогом."""
        priced_first = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("100")}})
        zero_second = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([priced_first, zero_second], CATS)

        assert r.total_cells[0].shown == D("100")
        assert r.total_cells[1].shown == D("0")
        assert r.total_cells[1].unavailable_reason is None
        assert r.total_cells[1].change.kind == ss.KIND_PERCENT
        assert r.total_cells[1].change.value == D("-100")
        assert r.total_cells[1].change.direction == ss.DIR_DOWN
        assert r.track.available is False and r.track.reason == ss.TRACK_NON_POSITIVE

    def test_negative_previous_total_becoming_zero_is_abs_only(self):
        """Третий знак базы (§2.6/§2.16, `AGENTS.md` §11 — оба знака у величины,
        чей знак участвует в решении): предыдущий итог ≤ 0 даёт абсолютную
        дельту, не процент, даже когда предыдущая величина сама отрицательна."""
        negative_first = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("-40")}})
        zero_second = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([negative_first, zero_second], CATS)

        assert r.total_cells[0].shown == D("-40")
        assert r.total_cells[1].shown == D("0")
        assert r.total_cells[1].change.kind == ss.KIND_ABS_ONLY
        assert r.total_cells[1].change.value == D("40")
        assert r.total_cells[1].change.direction == ss.DIR_UP

    def test_column_total_and_total_cell_amount_are_the_same_value_both_directions(self):
        """`columns[i].total_shown == total_cells[i].shown` — в обе стороны: как
        числом (ось известна на всех колонках), так и `None` (неизвестная база
        НДС) — паритет проверяется на каждой колонке трассы, не выборочно."""
        c1 = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("50")}})
        c2 = ss.ColumnInput(**{**col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("30")}}).__dict__,
                               "vat_rate_base": None})
        c3 = col(3, 3, "20", {6: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([c1, c2, c3], CATS)

        for idx in range(3):
            assert r.columns[idx].total_shown == r.total_cells[idx].shown
        assert r.total_cells[0].shown == D("50")
        assert r.total_cells[1].shown is None
        assert r.total_cells[2].shown == D("0")

    def test_unknown_vat_base_is_the_only_way_amount_is_absent(self):
        """Единственный путь к `None` у `TotalCell.amount` — неизвестная база
        НДС, не отсутствие строк и не нулевая сумма (обе дают число — тесты
        выше и `test_total_amount_is_zero_not_absent_when_column_has_no_rows_at_all`).
        Здесь — обратное направление: колонка БЕЗ строк вообще, но с ИЗВЕСТНОЙ
        базой, обязана остаться числом, а колонка с ИЗВЕСТНЫМИ строками, но
        НЕИЗВЕСТНОЙ базой — обязана уйти в `None` с причиной."""
        known_empty = col(1, 1, "20", {})
        unknown_priced = ss.ColumnInput(**{**col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("10")}}).__dict__,
                                           "vat_rate_base": None})
        r = ss.compute_summary([known_empty, unknown_priced], CATS)

        assert r.total_cells[0].shown == D("0") and r.total_cells[0].unavailable_reason is None
        assert r.total_cells[1].shown is None
        assert r.total_cells[1].unavailable_reason == ss.REASON_UNKNOWN_VAT_BASE

    def test_kpi_first_to_last_uses_same_numeric_rule_as_last_column_change(self):
        """Требование задачи: KPI и последняя колонка согласованы одним правилом.
        При РОВНО двух колонках путь «первый → последний» и шаг «к предыдущей»
        последней колонки — один и тот же интервал, поэтому они обязаны совпасть
        буквально, не только «использовать похожую формулу»."""
        priced_first = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("100")}})
        zero_second = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([priced_first, zero_second], CATS)

        assert r.kpi.first_to_last == r.total_cells[-1].change
        assert r.kpi.first_to_last.kind == ss.KIND_PERCENT and r.kpi.first_to_last.value == D("-100")

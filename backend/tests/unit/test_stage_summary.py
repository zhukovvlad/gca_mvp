"""Чистый расчёт свода по этапам без БД (спека §2.5–§2.7).

Каждая клетка матрицы §2.6 — отдельный случай; `absent`, `not_evaluated` и
`removed` — три разных факта, и тесты не сводят их к «пусто/есть».
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from services import stage_summary as ss

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

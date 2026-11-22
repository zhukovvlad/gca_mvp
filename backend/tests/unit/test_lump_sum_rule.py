"""Правило «строка похожа на комплект» — `services/lump_sum.py` (спека строк-сумм §1).

Чистая функция над готовыми входами; у каждого предиката есть и положительный, и
отрицательный случай, граница включена.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from services.lump_sum import (
    MEASURE_THRESHOLD,
    MEASURE_UNIT_CODES,
    PIECE_THRESHOLD,
    TYPICAL_MIN_LINES,
    LumpOverride,
    TypicalRate,
    TypicalRates,
    lump_check,
)

D = Decimal
FAMILY = 7
MEASURE_UNIT = 1   # M2
PIECE_UNIT = 2     # PCS

OVERRIDE_AT = dt.datetime(2026, 11, 22, 12, 0, tzinfo=dt.UTC)


def _rates(*, measure_typical=D("1000"), piece_typical=D("1000")) -> TypicalRates:
    rates = {}
    if measure_typical is not None:
        rates[(FAMILY, MEASURE_UNIT)] = TypicalRate(net=measure_typical, lines=TYPICAL_MIN_LINES)
    if piece_typical is not None:
        rates[(FAMILY, PIECE_UNIT)] = TypicalRate(net=piece_typical, lines=TYPICAL_MIN_LINES)
    return TypicalRates(
        rates=rates,
        unit_codes={MEASURE_UNIT: "M2", PIECE_UNIT: "PCS"},
        unit_labels={MEASURE_UNIT: "м2", PIECE_UNIT: "шт"},
    )


def _check(*, unit_id=MEASURE_UNIT, price=D("5000"), weight=D("1"), vat_base=D("0"),
           override=None, rates=None):
    return lump_check(
        family_id=FAMILY, unit_id=unit_id, unit_price=price, weight=weight,
        vat_base=vat_base, override=override, rates=rates or _rates(),
    )


def _override(verdict) -> LumpOverride:
    return LumpOverride(verdict=verdict, set_by_id=3, set_by_email="a@example.com", set_at=OVERRIDE_AT)


class TestConstants:
    def test_values_from_the_spec(self):
        assert frozenset({"M", "M2", "M3", "TON", "KG"}) == MEASURE_UNIT_CODES
        assert D(5) == MEASURE_THRESHOLD
        assert D(50) == PIECE_THRESHOLD
        assert TYPICAL_MIN_LINES == 3


class TestThresholdBoundary:
    def test_measure_unit_at_the_threshold_is_lump(self):
        result = _check(price=D("5000"))
        assert result.rule is True and result.is_lump is True
        assert result.measure is True and result.threshold == D(5)

    def test_measure_unit_just_below_the_threshold_is_rate(self):
        result = _check(price=D("4999.99"))
        assert result.rule is False and result.is_lump is False

    def test_piece_unit_at_the_threshold_is_lump(self):
        result = _check(unit_id=PIECE_UNIT, price=D("50000"))
        assert result.rule is True and result.measure is False and result.threshold == D(50)

    def test_piece_unit_just_below_the_threshold_is_rate(self):
        assert _check(unit_id=PIECE_UNIT, price=D("49999.99")).rule is False

    def test_piece_unit_at_measure_threshold_is_not_lump(self):
        assert _check(unit_id=PIECE_UNIT, price=D("5000")).rule is False


class TestWeight:
    @pytest.mark.parametrize("weight", [D("1.0001"), D("2"), D("0.9999")])
    def test_weight_other_than_one_is_rate_even_at_ratio_one_thousand(self, weight):
        assert _check(price=D("1000000"), weight=weight).rule is False

    def test_trailing_zeros_weight_is_weight_one(self):
        assert _check(weight=D("1.000")).rule is True


class TestNoTypicalRate:
    def test_measure_unit_without_rate_is_lump(self):
        result = _check(price=D("2"), rates=_rates(measure_typical=None))
        assert result.typical is None and result.rule is True

    def test_piece_unit_without_rate_is_rate(self):
        result = _check(unit_id=PIECE_UNIT, price=D("1000000"), rates=_rates(piece_typical=None))
        assert result.typical is None and result.rule is False

    def test_rate_with_weight_other_than_one_is_still_rate_without_typical(self):
        assert _check(weight=D("2"), rates=_rates(measure_typical=None)).rule is False


class TestOverride:
    def test_lump_override_beats_rate_answer(self):
        result = _check(price=D("10"), override=_override("lump"))
        assert result.rule is False and result.is_lump is True
        assert result.override.verdict == "lump"

    def test_rate_override_beats_lump_answer(self):
        result = _check(price=D("5000"), override=_override("rate"))
        assert result.rule is True and result.is_lump is False

    def test_override_agreeing_with_the_rule_changes_nothing(self):
        assert _check(price=D("5000"), override=_override("lump")).is_lump is True
        assert _check(price=D("10"), override=_override("rate")).is_lump is False

    def test_lump_override_makes_a_weighted_row_a_lump(self):
        assert _check(weight=D("2"), override=_override("lump")).is_lump is True


class TestNotApplicable:
    def test_no_unit(self):
        assert _check(unit_id=None) is None

    @pytest.mark.parametrize("price", [None, D("0"), D("-1"), D("NaN"), D("Infinity")])
    def test_not_a_price(self, price):
        assert _check(price=price) is None

    @pytest.mark.parametrize("weight", [None, D("0"), D("-1"), D("NaN"), D("Infinity")])
    def test_not_a_weight(self, weight):
        assert _check(weight=weight) is None

    def test_unknown_vat_base(self):
        assert _check(vat_base=None) is None

    def test_applicable_input_is_not_none(self):
        assert _check() is not None


class TestVatBase:
    def test_same_net_price_gives_the_same_answer_under_both_bases(self):
        rates = _rates(measure_typical=D("100"))
        twenty = _check(price=D("600"), vat_base=D("20"), rates=rates)
        zero = _check(price=D("500"), vat_base=D("0"), rates=rates)
        assert twenty.net == zero.net == D("500")
        assert twenty.rule is zero.rule is True

    def test_just_above_the_threshold_nets_are_rate_under_both_bases(self):
        rates = _rates(measure_typical=D("100.01"))
        assert _check(price=D("600"), vat_base=D("20"), rates=rates).rule is False
        assert _check(price=D("500"), vat_base=D("0"), rates=rates).rule is False

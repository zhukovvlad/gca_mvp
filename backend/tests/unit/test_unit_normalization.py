"""Unit tests for unit normalization — no DB required."""
from decimal import Decimal

import pytest

from crud.units import invariant_holds, normalize_unit_key


class TestNormalizeUnitKey:
    @pytest.mark.parametrize("raw,expected", [
        ("т", "т"),
        ("Т", "т"),
        (" Т ", "т"),
        ("тн", "тн"),
        ("м³", "м3"),          # NFKC: U+00B3 → "3"
        ("м3", "м3"),
        ("куб.м.", "куб.м"),   # trailing dot stripped
        ("кв  м", "кв м"),     # internal whitespace collapsed
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_unit_key(raw) == expected

    def test_m3_unicode_and_digit_collapse_to_same_key(self):
        assert normalize_unit_key("м³") == normalize_unit_key("м3")


class TestInvariantHolds:
    def test_exact(self):
        assert invariant_holds(Decimal("5"), Decimal("8000"), Decimal("40000")) is True

    def test_within_abs_tolerance_1_rub(self):
        # 5 * 8000 = 40000; amount off by exactly 1.00 → pass (tol = max(1, 0.1%))
        assert invariant_holds(Decimal("5"), Decimal("8000"), Decimal("40001")) is True

    def test_just_over_abs_tolerance(self):
        # Small amount so the absolute floor (1₽) dominates the relative tol:
        # expected 1*1 = 1; amount 2.01 → off by 1.01 > max(1, 0.1%·2.01) = 1 → fail
        assert invariant_holds(Decimal("1"), Decimal("1"), Decimal("2.01")) is False

    def test_within_rel_tolerance(self):
        # 1000 * 1 = 1000; 0.1% = 1.0; amount off by exactly 1.0 → pass
        assert invariant_holds(Decimal("1000"), Decimal("1"), Decimal("1001")) is True

    def test_just_over_rel_tolerance(self):
        # 1000 * 1 = 1000; 0.1% = 1.0; amount off by 1.01 → fail
        assert invariant_holds(Decimal("1000"), Decimal("1"), Decimal("1001.01")) is False

    def test_none_inputs_return_false(self):
        assert invariant_holds(None, Decimal("1"), Decimal("1")) is False
        assert invariant_holds(Decimal("1"), None, Decimal("1")) is False
        assert invariant_holds(Decimal("1"), Decimal("1"), None) is False

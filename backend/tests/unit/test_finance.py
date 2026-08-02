from decimal import Decimal

import pytest

from finance import money_round


@pytest.mark.parametrize(
    "value, expected",
    [
        (Decimal("2.345"), Decimal("2.35")),   # HALF_UP: .5 → up (banker's would give 2.34)
        (Decimal("2.355"), Decimal("2.36")),   # HALF_UP
        (Decimal("2.344"), Decimal("2.34")),   # below half → down
        (Decimal("100.00"), Decimal("100.00")),
        (Decimal("-2.345"), Decimal("-2.35")),  # HALF_UP rounds away from zero (|-2.345| → 2.35)
    ],
)
def test_money_round_half_up(value, expected):
    assert money_round(value) == expected


def test_money_round_accepts_float_via_str():
    # str() conversion avoids binary float imprecision: Decimal(0.1) != Decimal("0.1")
    assert money_round(0.1 + 0.2) == Decimal("0.30")


def test_money_round_accepts_str_and_int():
    assert money_round("5") == Decimal("5.00")
    assert money_round(5) == Decimal("5.00")


def test_money_round_places():
    assert money_round(Decimal("1.23456"), places=4) == Decimal("1.2346")
    assert money_round(Decimal("1.5"), places=0) == Decimal("2")

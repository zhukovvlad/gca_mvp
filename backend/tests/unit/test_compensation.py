from decimal import Decimal

import pytest

from crud.calculations import compute_compensation_per_unit

D = Decimal


@pytest.mark.parametrize(
    "avg_price, ref_price, corridor_pct, expected",
    [
        (D("110"), D("100"), D("5"), D("5.00")),    # overrun beyond corridor → +5
        (D("90"), D("100"), D("5"), D("-5.00")),    # saving beyond corridor → -5
        (D("103"), D("100"), D("5"), D("0")),       # inside [95;105] → 0
        (D("105"), D("100"), D("5"), D("0")),       # exactly on upper boundary → 0
        (D("95"), D("100"), D("5"), D("0")),        # exactly on lower boundary → 0
        (D("110"), D("100"), D("0"), D("10.00")),   # corridor 0% → compensation == deviation
        (D("90"), D("100"), D("0"), D("-10.00")),
    ],
)
def test_compensation_per_unit(avg_price, ref_price, corridor_pct, expected):
    assert compute_compensation_per_unit(avg_price, ref_price, corridor_pct) == expected


def test_compensation_none_when_no_ref_price():
    assert compute_compensation_per_unit(D("110"), None, D("5")) is None
    assert compute_compensation_per_unit(D("110"), D("0"), D("5")) is None


def test_compensation_none_when_corridor_not_set():
    assert compute_compensation_per_unit(D("110"), D("100"), None) is None


def test_compensation_none_when_not_compensable():
    """resolve_corridor returns (False, None) when is_compensable=False.
    The caller skips compute_compensation_per_unit entirely, but passing
    corridor_pct=None directly also returns None."""
    assert compute_compensation_per_unit(D("110"), D("100"), None) is None

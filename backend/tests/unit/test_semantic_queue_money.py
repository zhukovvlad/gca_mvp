"""Деньги очереди уходят на экран строками фиксированной записи: `str(Decimal)`
отдаёт порой экспоненту (`1E+2`), а экран разбирает деньги строкой и не знает
этой записи."""

from decimal import Decimal

import pytest

from crud.semantic_queue import money_str
from routers.semantic import _serialize_preview
from services.semantic_decisions import Preview


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1E+2"), "100"),
        (Decimal("1.5E+1"), "15"),
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("12.50"), "12.50"),
        (Decimal("0"), "0"),
        (Decimal("0.0042"), "0.0042"),
    ],
)
def test_money_str_is_fixed_point(value, expected):
    assert money_str(value) == expected


def test_preview_money_is_fixed_point():
    preview = Preview(
        context_count=3, reserve_usd=Decimal("1E+1"), expected_cached_usd=Decimal("5E-8"),
        preview_hash="h",
    )

    payload = _serialize_preview(preview)

    assert (payload["reserve_usd"], payload["expected_cached_usd"]) == ("10", "0.00000005")

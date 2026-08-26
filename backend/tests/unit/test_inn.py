"""Канон ИНН — ASCII-цифры и ничего больше (спека контура §1.5, §2.7).

`str.isdigit()` шире: полноширинные, арабско-индийские и надстрочные цифры
проходят его, а `CHECK (inn ~ '^[0-9]+$')` в PostgreSQL их отвергнет — вставка
упала бы с необъяснимым отказом. Канон обязан совпадать с тем, что проверит база.
"""
from __future__ import annotations

import pytest

from utils import canonicalize_inn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7700123456", "7700123456"),
        ("77 00 123 456", "7700123456"),
        ("7700-123-456", "7700123456"),
        ("ИНН 7700123456", "7700123456"),
        (7700123456, "7700123456"),
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_keeps_only_ascii_digits(raw, expected):
    assert canonicalize_inn(raw) == expected


def test_non_ascii_digits_are_dropped_not_converted():
    """Полноширинная 7, арабско-индийская 3 и надстрочная 2 — все `isdigit()`,
    ни одна не `[0-9]`. Канон их выбрасывает, а не «переводит»: перевод
    выдумал бы цифру, которой в документе подрядчика нет."""
    assert canonicalize_inn("７７00٣²") == "00"
    assert "７７00٣²".isdigit()  # предпосылка теста: isdigit действительно шире

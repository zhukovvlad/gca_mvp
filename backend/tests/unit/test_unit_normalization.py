"""Unit tests for unit normalization — no DB required."""
import pytest

from crud.units import normalize_unit_key


class TestNormalizeUnitKey:
    @pytest.mark.parametrize("raw,expected", [
        ("т", "т"),
        ("Т", "т"),
        (" Т ", "т"),
        ("тн", "тн"),
        ("м³", "м3"),          # NFKC: U+00B3 → "3"
        ("м3", "м3"),
        ("м²", "м2"),          # NFKC: U+00B2 → "2"
        ("м2", "м2"),
        ("кв.м.", "кв.м"),     # trailing dot stripped
        ("кв  м", "кв м"),     # internal whitespace collapsed
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_unit_key(raw) == expected

    def test_m3_unicode_and_digit_collapse_to_same_key(self):
        assert normalize_unit_key("м³") == normalize_unit_key("м3")

    def test_m2_unicode_and_digit_collapse_to_same_key(self):
        assert normalize_unit_key("м²") == normalize_unit_key("м2")

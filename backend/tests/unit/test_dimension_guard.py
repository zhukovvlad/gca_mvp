"""Unit tests for the ref-price dimension guard — no DB."""
from crud.calculations import dimension_matches


class TestDimensionMatches:
    def test_same_dimension_ok(self):
        assert dimension_matches("volume", "volume") is True

    def test_different_dimension_blocked(self):
        assert dimension_matches("volume", "mass") is False

    def test_none_class_dimension_blocked(self):
        # class with no normalized unit → cannot compare → blocked
        assert dimension_matches(None, "volume") is False

    def test_none_ref_dimension_blocked(self):
        assert dimension_matches("volume", None) is False

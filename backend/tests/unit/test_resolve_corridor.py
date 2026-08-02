"""Unit tests for corridor fallback resolution — no DB required."""
from decimal import Decimal
from types import SimpleNamespace

from crud.compensation_corridors import resolve_corridor

D = Decimal
CONCRETE = 10  # stand-in material_type_id
REBAR = 20


def _row(is_compensable: bool, corridor_pct: Decimal | None) -> SimpleNamespace:
    return SimpleNamespace(is_compensable=is_compensable, corridor_pct=corridor_pct)


class TestResolveCorridorFallback:
    def test_no_rows_returns_none(self):
        compensable, pct = resolve_corridor({}, {}, 1, CONCRETE)
        assert compensable is None
        assert pct is None

    def test_type_level_compensable(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, CONCRETE)
        assert compensable is True
        assert pct == D("5.00")

    def test_type_level_not_compensable(self):
        by_type = {REBAR: _row(False, None)}
        compensable, pct = resolve_corridor({}, by_type, 1, REBAR)
        assert compensable is False
        assert pct is None

    def test_class_override_wins_over_type(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 42, CONCRETE)
        assert compensable is True
        assert pct == D("7.00")

    def test_class_override_can_disable_over_type_enabled(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        by_class = {42: _row(False, None)}
        compensable, pct = resolve_corridor(by_class, by_type, 42, CONCRETE)
        assert compensable is False
        assert pct is None

    def test_class_override_can_enable_over_type_disabled(self):
        by_type = {REBAR: _row(False, None)}
        by_class = {55: _row(True, D("3.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 55, REBAR)
        assert compensable is True
        assert pct == D("3.00")

    def test_unrelated_type_not_matched(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, REBAR)
        assert compensable is None
        assert pct is None

    def test_unrelated_class_not_matched(self):
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, {}, 99, CONCRETE)
        assert compensable is None
        assert pct is None

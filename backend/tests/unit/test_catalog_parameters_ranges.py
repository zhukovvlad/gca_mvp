"""Числа и диапазоны значений параметров (спека 3в §2.4, §2.6): разбор одного
числа, построение значений, отнесение числа и диапазона.

Эталоны записаны вручную; вход каждого правила парный: число, которое правило
принимает, и соседнее, которое не должно приниматься.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.parameter_ranges import (
    RANGE_RULE_LINE,
    RangeValue,
    build_range_values,
    containing_range,
    parse_single_number,
    range_for_number,
)

D = Decimal
DASH = "–"


class TestParseSingleNumber:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("131,73", D("131.73")),
            ("131.73", D("131.73")),
            ("5,1", D("5.1")),
            ("4 мм", D("4")),
            ("4мм", D("4")),
            ("131,73 кг/м3", D("131.73")),
            ("12 м²", D("12")),
            ("  7  ", D("7")),
            ("0,5", D("0.5")),
        ],
    )
    def test_one_number_with_optional_unit_is_a_number(self, text, expected):
        assert parse_single_number(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["до 100 мм", "от 40 мм", "40-50", "40–50", "1/2", "", "   ", "В25", "5 и 6", "1,2,3",
         "12.", ",5", "10 мм 20 мм", "100 кг/", "кг/м3"],
    )
    def test_anything_else_is_not_a_number(self, text):
        assert parse_single_number(text) is None

    def test_result_is_decimal_not_float(self):
        assert isinstance(parse_single_number("0,1"), Decimal)
        assert parse_single_number("0,1") == D("0.1")


class TestBuildRangeValues:
    def test_step_twenty_from_120_to_200_gives_six_values_with_labels(self):
        values = build_range_values(D(120), D(200), D(20))
        assert [v.label for v in values] == [
            "до 120", f"120{DASH}140", f"140{DASH}160", f"160{DASH}180", f"180{DASH}200",
            "200 и более",
        ]
        assert len(values) == 6

    def test_edge_values_have_open_ends_and_inner_ones_have_both_bounds(self):
        values = build_range_values(D(120), D(200), D(20))
        assert (values[0].lo, values[0].hi) == (None, D(120))
        assert (values[1].lo, values[1].hi) == (D(120), D(140))
        assert (values[-1].lo, values[-1].hi) == (D(200), None)
        assert all(v.lo is not None and v.hi is not None for v in values[1:-1])

    def test_decimal_comma_and_no_trailing_zeros(self):
        values = build_range_values(D("0.5"), D("2.0"), D("0.50"))
        assert [v.label for v in values] == [
            "до 0,5", f"0,5{DASH}1", f"1{DASH}1,5", f"1,5{DASH}2", "2 и более",
        ]

    def test_last_inner_range_stops_at_the_end_when_step_does_not_divide(self):
        values = build_range_values(D(2), D(9), D(4))
        assert [v.label for v in values] == [
            "до 2", f"2{DASH}6", f"6{DASH}9", "9 и более",
        ]

    def test_single_inner_range(self):
        values = build_range_values(D(2), D(10), D(8))
        assert [(v.lo, v.hi) for v in values] == [(None, D(2)), (D(2), D(10)), (D(10), None)]

    @pytest.mark.parametrize(
        ("start", "stop", "step"),
        [(D(5), D(5), D(1)), (D(6), D(5), D(1)), (D(1), D(5), D(0)), (D(1), D(5), D(-1))],
    )
    def test_degenerate_input_is_refused(self, start, stop, step):
        with pytest.raises(ValueError):
            build_range_values(start, stop, step)


class TestRangeForNumber:
    VALUES = build_range_values(D(120), D(200), D(20))

    @pytest.mark.parametrize(
        ("number", "label"),
        [
            (D("139.99"), f"120{DASH}140"),
            (D(140), f"140{DASH}160"),
            (D(200), "200 и более"),
            (D("119.99"), "до 120"),
            (D(120), f"120{DASH}140"),
            (D(10_000), "200 и более"),
            (D(-5), "до 120"),
        ],
    )
    def test_lower_bound_is_included_and_upper_is_not(self, number, label):
        assert range_for_number(self.VALUES, number).label == label

    def test_set_that_does_not_cover_the_axis_is_refused(self):
        gap = (RangeValue("a", D(1), D(2)), RangeValue("b", D(3), D(4)))
        with pytest.raises(ValueError):
            range_for_number(gap, D("2.5"))


class TestContainingRange:
    NEW = build_range_values(D(120), D(200), D(40))

    def test_old_range_inside_one_new_range_is_found(self):
        old = RangeValue(f"120{DASH}140", D(120), D(140))
        found = containing_range(self.NEW, old.lo, old.hi)
        assert found is not None and found.label == f"120{DASH}160"

    def test_old_range_split_by_a_new_boundary_is_not_contained(self):
        # шаг 20 -> 40: «120–160» накрывает «120–140» и «140–160», а целиком её
        # не содержит ни один новый диапазон, если шаг стал 20
        fine = build_range_values(D(120), D(200), D(20))
        assert containing_range(fine, D(120), D(160)) is None

    def test_old_range_equal_to_new_one_is_contained_in_it(self):
        found = containing_range(self.NEW, D(160), D(200))
        assert found is not None and found.label == f"160{DASH}200"

    def test_left_infinite_old_range_needs_a_left_infinite_new_one(self):
        found = containing_range(self.NEW, None, D(120))
        assert found is not None and found.label == "до 120"
        # новая граница левее: «до 100» уже не лежит ни в одном новом диапазоне,
        # кроме левого бесконечного
        assert containing_range(self.NEW, None, D(100)).label == "до 120"

    def test_left_infinite_old_range_is_not_inside_a_finite_new_one(self):
        only_finite = (RangeValue("x", D(1), D(5)),)
        assert containing_range(only_finite, None, D(3)) is None

    def test_right_infinite_old_range_is_found_only_in_a_right_infinite_new_one(self):
        found = containing_range(self.NEW, D(200), None)
        assert found is not None and found.label == "200 и более"
        only_finite = (RangeValue("x", D(1), D(500)),)
        assert containing_range(only_finite, D(200), None) is None

    def test_old_range_wider_than_the_whole_new_set_is_not_contained(self):
        narrow = build_range_values(D(150), D(160), D(5))
        assert containing_range(narrow, D(100), D(200)) is None


def test_rule_line_names_the_rule_and_the_none_fallback():
    assert "диапазон" in RANGE_RULE_LINE
    assert "нижняя граница входит" in RANGE_RULE_LINE
    assert "верхняя нет" in RANGE_RULE_LINE
    assert '"kind": "none"' in RANGE_RULE_LINE

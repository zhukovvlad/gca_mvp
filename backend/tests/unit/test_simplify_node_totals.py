"""Итоги узлов дерева страницы участника (спека упрощения каталога §2.10).

Итог узла — все конечные `total_cost_total` строк узла и его потомков: правило сумм
статей (`docs/reference/money-axes.md` §7), а не предикат цены. Эталоны — числа,
посчитанные руками; названия и числа выдуманы.
"""

from decimal import Decimal

import pytest

from services.our_price_money import NodeLine, node_totals

D = Decimal


def _line(total, *, no_price=False, categories=(1,), chapters=()):
    return NodeLine(
        total=None if total is None else D(total),
        no_price=no_price,
        categories=tuple(categories),
        chapters=tuple(chapters),
    )


def _cat(result, key):
    return result["categories"][key]


def test_simplify_node_totals_negative_and_positive_lines_add_up():
    """+100 и −20 дают 80: предикат цены отбросил бы вторую строку."""
    result = node_totals([_line("100"), _line("-20")], base=D("0"))

    assert _cat(result, 1) == {"lines": 2, "no_price": 0, "kp_total": {"net": D("80.00"), "shown": D("80.00")}}


def test_simplify_node_totals_net_is_in_the_kp_base_and_shown_in_the_page_rate():
    """Валовые 120 и −24 при базе 20 % — нетто 80, показано по ставке страницы 20 % — 96."""
    result = node_totals([_line("120"), _line("-24")], base=D("20"))

    assert _cat(result, 1)["kp_total"] == {"net": D("80.00"), "shown": D("96.00")}


def test_simplify_node_totals_unknown_base_has_no_total_but_keeps_the_counts():
    result = node_totals([_line("100"), _line("5", no_price=True)], base=None)

    assert _cat(result, 1) == {"lines": 2, "no_price": 1, "kp_total": None}


def test_simplify_node_totals_zero_sum_is_zero_not_absent():
    result = node_totals([_line("50"), _line("-50")], base=D("0"))

    total = _cat(result, 1)["kp_total"]
    assert total is not None
    assert total == {"net": D("0.00"), "shown": D("0.00")}


def test_simplify_node_totals_a_line_without_a_price_counts_and_adds_its_finite_total():
    result = node_totals([_line("100"), _line("30", no_price=True)], base=D("0"))

    assert _cat(result, 1) == {"lines": 2, "no_price": 1, "kp_total": {"net": D("130.00"), "shown": D("130.00")}}


@pytest.mark.parametrize("bad", [None, "NaN", "Infinity", "-Infinity"])
def test_simplify_node_totals_a_missing_or_nonfinite_total_adds_nothing_but_counts(bad):
    result = node_totals([_line("100"), _line(bad, no_price=True)], base=D("0"))

    assert _cat(result, 1) == {"lines": 2, "no_price": 1, "kp_total": {"net": D("100.00"), "shown": D("100.00")}}


def test_simplify_node_totals_a_node_of_unpriced_lines_only_is_zero():
    result = node_totals([_line(None, no_price=True)], base=D("0"))

    assert _cat(result, 1) == {"lines": 1, "no_price": 1, "kp_total": {"net": D("0.00"), "shown": D("0.00")}}


def test_simplify_node_totals_a_line_feeds_every_node_of_its_path_and_no_other():
    lines = [
        _line("10", categories=(1, 2, 3), chapters=(7, 8)),
        _line("5", categories=(1, 4), chapters=(7, 9)),
    ]

    result = node_totals(lines, base=D("0"))

    assert {key: node["kp_total"]["net"] for key, node in result["categories"].items()} == {
        1: D("15.00"),
        2: D("10.00"),
        3: D("10.00"),
        4: D("5.00"),
    }
    assert {key: node["kp_total"]["net"] for key, node in result["chapters"].items()} == {
        7: D("15.00"),
        8: D("10.00"),
        9: D("5.00"),
    }
    assert {key: node["lines"] for key, node in result["categories"].items()} == {1: 2, 2: 1, 3: 1, 4: 1}


def test_simplify_node_totals_unallocated_lines_go_to_the_none_key():
    result = node_totals([_line("7", categories=(None,)), _line("3", categories=(1,))], base=D("0"))

    assert set(result["categories"]) == {None, 1}
    assert _cat(result, None)["kp_total"]["net"] == D("7.00")


def test_simplify_node_totals_no_lines_no_nodes():
    assert node_totals([], base=D("0")) == {"categories": {}, "chapters": {}}


def test_simplify_node_totals_money_is_decimal_never_float():
    result = node_totals([_line("0.1"), _line("0.2")], base=D("20"))

    total = _cat(result, 1)["kp_total"]
    assert isinstance(total["net"], Decimal) and isinstance(total["shown"], Decimal)
    assert total["net"] == D("0.25")

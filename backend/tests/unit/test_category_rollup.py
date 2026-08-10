"""Roll-up дерева статей классификатора — без БД и без VIEW (спека Ф6 §2.2-2.4).

Вход собирается из литералов: `direct` — это уже посчитанный `v_category_totals`,
разложенный по источникам, а не запрос к нему. Алгоритм roll-up — единственная
нетривиальная часть Task 2, и он проверяется независимо от SQL.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.category_rollup import (
    SOURCE_ADDITIONAL_WORKS,
    SOURCE_POSITIONS,
    CategoryRef,
    DirectTotals,
    build_tree,
)


def cat(
    id_: int,
    code: str,
    title: str = "Статья",
    *,
    parent_id: int | None = None,
    sort_order: int = 0,
    is_bucket: bool = False,
) -> CategoryRef:
    return CategoryRef(
        id=id_, code=code, title=title, parent_id=parent_id, is_bucket=is_bucket, sort_order=sort_order
    )


def totals(
    amount: str | None,
    *,
    row_count: int,
    rows_with_amount: int,
    rows_not_finite: int = 0,
) -> DirectTotals:
    """`amount` строкой — чтобы в тексте теста не путать Decimal с float на глаз."""
    return DirectTotals(
        amount=Decimal(amount) if amount is not None else None,
        row_count=row_count,
        rows_with_amount=rows_with_amount,
        rows_not_finite=rows_not_finite,
    )


def d(value: str) -> Decimal:
    return Decimal(value)


class TestTotalIsTheSumOfKnownSummands:
    def test_leaf_total_equals_its_own_money(self):
        leaf = cat(1, "1", sort_order=1)
        direct = {1: {SOURCE_POSITIONS: totals("100.00", row_count=2, rows_with_amount=2)}}

        (node,) = build_tree([leaf], direct)

        assert node.total == d("100.00")
        assert node.rows == 2
        assert node.rows_priced == 2

    def test_parent_total_is_own_plus_children_plus_extras(self):
        parent = cat(1, "1", sort_order=1)
        child = cat(2, "1.1", parent_id=1, sort_order=1)
        direct = {
            1: {
                SOURCE_POSITIONS: totals("10.00", row_count=1, rows_with_amount=1),
                SOURCE_ADDITIONAL_WORKS: totals("5.00", row_count=1, rows_with_amount=1),
            },
            2: {SOURCE_POSITIONS: totals("7.00", row_count=1, rows_with_amount=1)},
        }

        (node,) = build_tree([parent, child], direct)

        assert node.total == d("22.00")

    def test_parent_with_own_money_beyond_children_keeps_both_numbers(self):
        """Реальный случай §1.4: часть файла подшита прямо к разделу-родителю.

        Дети родителя суммарно НЕ дают его итог — расхождение и есть искомый факт,
        а не повод посчитать `own` через вычитание.
        """
        parent = cat(1, "1", sort_order=1)
        child = cat(2, "1.1", parent_id=1, sort_order=1)
        direct = {
            1: {SOURCE_POSITIONS: totals("50.00", row_count=1, rows_with_amount=1)},
            2: {SOURCE_POSITIONS: totals("30.00", row_count=1, rows_with_amount=1)},
        }

        (node,) = build_tree([parent, child], direct)

        assert node.own == d("50.00")
        assert node.total == d("80.00")
        assert node.own != node.total

    def test_unknown_total_is_none_not_zero(self):
        leaf = cat(1, "1", sort_order=1)

        (node,) = build_tree([leaf], {})

        assert node.total is None
        assert node.own is None
        assert node.rows == 0

    def test_zero_total_is_zero_not_none(self):
        leaf = cat(1, "1", sort_order=1)
        direct = {1: {SOURCE_POSITIONS: totals("0.00", row_count=1, rows_with_amount=1)}}

        (node,) = build_tree([leaf], direct)

        assert node.total == d("0.00")
        assert node.total is not None


class TestCountersRollUp:
    @pytest.mark.parametrize("counter", ["rows", "rows_priced", "rows_not_finite"])
    def test_counters_roll_up_over_the_whole_subtree(self, counter):
        grandparent = cat(1, "1", sort_order=1)
        parent = cat(2, "1.1", parent_id=1, sort_order=1)
        grandchild = cat(3, "1.1.1", parent_id=2, sort_order=1)
        direct = {
            1: {SOURCE_POSITIONS: totals("20.00", row_count=5, rows_with_amount=2, rows_not_finite=1)},
            2: {SOURCE_POSITIONS: totals("5.00", row_count=3, rows_with_amount=1, rows_not_finite=0)},
            3: {SOURCE_POSITIONS: totals(None, row_count=4, rows_with_amount=0, rows_not_finite=2)},
        }

        (root,) = build_tree([grandparent, parent, grandchild], direct)

        expected = {"rows": 12, "rows_priced": 3, "rows_not_finite": 3}
        assert getattr(root, counter) == expected[counter]

    def test_mixed_node_keeps_the_partial_sum_and_the_incompleteness(self):
        """Девять строк с ценой, одна без — сумма без признака неполноты недопустима."""
        leaf = cat(1, "1", sort_order=1)
        direct = {1: {SOURCE_POSITIONS: totals("900.00", row_count=10, rows_with_amount=9)}}

        (node,) = build_tree([leaf], direct)

        assert node.total == d("900.00")
        assert node.rows == 10
        assert node.rows_priced == 9

    def test_mixed_node_reports_both_causes_separately(self):
        leaf = cat(1, "1", sort_order=1)
        direct = {1: {SOURCE_POSITIONS: totals("500.00", row_count=10, rows_with_amount=5, rows_not_finite=2)}}

        (node,) = build_tree([leaf], direct)

        missing_price = node.rows - node.rows_priced - node.rows_not_finite
        assert missing_price != 0
        assert node.rows_not_finite != 0
        assert missing_price != node.rows_not_finite


class TestPresence:
    def test_all_twenty_one_roots_are_present_even_when_absent_from_the_estimate(self):
        roots = [cat(i, str(i), sort_order=i) for i in range(1, 22)]
        direct = {
            1: {SOURCE_POSITIONS: totals("10.00", row_count=1, rows_with_amount=1)},
            2: {SOURCE_POSITIONS: totals("20.00", row_count=1, rows_with_amount=1)},
        }

        nodes = build_tree(roots, direct)

        assert len(nodes) == 21
        absent = [n for n in nodes if n.ref.id not in (1, 2)]
        assert len(absent) == 19
        for node in absent:
            assert node.total is None
            assert node.rows == 0

    def test_deeper_nodes_appear_only_when_their_subtree_has_rows(self):
        root = cat(1, "1", sort_order=1)
        absent_child = cat(2, "1.1", parent_id=1, sort_order=1)
        present_child = cat(3, "1.2", parent_id=1, sort_order=2)
        direct = {3: {SOURCE_POSITIONS: totals("10.00", row_count=1, rows_with_amount=1)}}

        (node,) = build_tree([root, absent_child, present_child], direct)

        assert [c.ref.id for c in node.children] == [3]

    def test_zero_subtree_appears_because_its_rows_are_positive(self):
        root = cat(1, "1", sort_order=1)
        zero_child = cat(2, "1.1", parent_id=1, sort_order=1)
        direct = {2: {SOURCE_POSITIONS: totals("0.00", row_count=1, rows_with_amount=1)}}

        (node,) = build_tree([root, zero_child], direct)

        assert [c.ref.id for c in node.children] == [2]
        assert node.children[0].total == d("0.00")


class TestOrder:
    def test_children_follow_the_reference_sort_order(self):
        root_a = cat(10, "2", sort_order=2)
        root_b = cat(11, "1", sort_order=1)
        child_late = cat(20, "1.2", parent_id=11, sort_order=2)
        child_early = cat(21, "1.1", parent_id=11, sort_order=1)
        direct = {
            10: {SOURCE_POSITIONS: totals("1.00", row_count=1, rows_with_amount=1)},
            11: {SOURCE_POSITIONS: totals("1.00", row_count=1, rows_with_amount=1)},
            20: {SOURCE_POSITIONS: totals("1.00", row_count=1, rows_with_amount=1)},
            21: {SOURCE_POSITIONS: totals("1.00", row_count=1, rows_with_amount=1)},
        }
        # Вход намеренно перемешан: порядок вывода не должен зависеть от порядка ввода.
        shuffled = [child_late, root_a, child_early, root_b]

        nodes = build_tree(shuffled, direct)

        assert [n.ref.id for n in nodes] == [11, 10]
        by_id = {n.ref.id: n for n in nodes}
        assert [c.ref.id for c in by_id[11].children] == [21, 20]


class TestOwnMirrorsTotal:
    @pytest.mark.parametrize("state", ["unknown", "zero", "partial"])
    def test_own_follows_the_same_three_rules_as_total(self, state):
        leaf = cat(1, "1", sort_order=1)
        if state == "unknown":
            direct = {}
            expected_own, expected_rows, expected_priced, expected_not_finite = None, 0, 0, 0
        elif state == "zero":
            direct = {1: {SOURCE_POSITIONS: totals("0.00", row_count=1, rows_with_amount=1)}}
            expected_own, expected_rows, expected_priced, expected_not_finite = d("0.00"), 1, 1, 0
        else:
            direct = {1: {SOURCE_POSITIONS: totals("50.00", row_count=3, rows_with_amount=2, rows_not_finite=1)}}
            expected_own, expected_rows, expected_priced, expected_not_finite = d("50.00"), 3, 2, 1

        (node,) = build_tree([leaf], direct)

        assert node.own == expected_own
        assert node.own_rows == expected_rows
        assert node.own_rows_priced == expected_priced
        assert node.own_rows_not_finite == expected_not_finite


class TestArithmetic:
    def test_decimal_arithmetic_only(self):
        # Премиса: именно эта пара мимо float — 62399.7 + 13341.3 в float точна
        # (замер Ф5 §2.5), а 0.1 + 0.2 нет. Пара выбрана так, чтобы доказать нужное.
        assert 0.1 + 0.2 != 0.3

        root = cat(1, "1", sort_order=1)
        child_a = cat(2, "1.1", parent_id=1, sort_order=1)
        child_b = cat(3, "1.2", parent_id=1, sort_order=2)
        direct = {
            2: {SOURCE_POSITIONS: totals("0.10", row_count=1, rows_with_amount=1)},
            3: {SOURCE_POSITIONS: totals("0.20", row_count=1, rows_with_amount=1)},
        }

        (node,) = build_tree([root, child_a, child_b], direct)

        assert node.total == d("0.30")
        assert isinstance(node.total, Decimal)

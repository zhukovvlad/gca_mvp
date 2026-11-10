"""Автоделение «к делению» без базы (спека упрощения каталога §2.5): группы по уровню
расхождения цепочек разделов и выбор правила группы.

Эталоны записаны руками: заголовки, уровни, виды правил — литералами. Правило проверяется
настоящим `evaluate_predicate`, а не копией его условия.
"""
from __future__ import annotations

import pytest

from services.auto_split import SplitGroup, SplitPlan, choose_rule, plan_groups
from services.context_routing import (
    PREDICATE_CHAPTER_CHAIN_CONTAINS,
    PREDICATE_NEAREST_CHAPTER_EQUALS,
    ChapterContext,
    evaluate_predicate,
)

ROOT = "Смета"


def _chain(*top_down: str) -> ChapterContext:
    """Цепочка члена, как её строит `chapter_context`: от ближайшего раздела к корню."""
    bottom_up = tuple(reversed(top_down))
    return ChapterContext(
        chain=bottom_up, nearest=bottom_up[0] if bottom_up else None,
        category_id=None, category_source=None,
    )


# ---------------------------------------------------------------------------
#  plan_groups
# ---------------------------------------------------------------------------

class TestPlanGroups:
    def test_two_headings_on_the_third_level_give_level_two_and_two_groups(self):
        paths = {
            1: (ROOT, "Корпус 2", "Стены"),
            2: (ROOT, "Корпус 2", "Колонны"),
            3: (ROOT, "Корпус 2", "Стены"),
        }

        plan = plan_groups(paths)

        assert plan == SplitPlan(
            level=2,
            groups=(
                SplitGroup(title="Стены", member_ids=(1, 3)),
                SplitGroup(title="Колонны", member_ids=(2,)),
            ),
        )

    def test_a_trailing_colon_and_the_case_do_not_make_a_second_group(self):
        paths = {
            1: (ROOT, "Корпус 2", "Стены:"),
            2: (ROOT, "Корпус 2", "стены"),
            3: (ROOT, "Корпус 2", "Колонны"),
        }

        plan = plan_groups(paths)

        assert plan is not None
        assert [group.member_ids for group in plan.groups] == [(1, 2), (3,)]
        assert plan.groups[0].title == "Стены:"

    def test_identical_paths_give_no_plan(self):
        paths = {1: (ROOT, "Корпус 2", "Стены"), 2: (ROOT, "Корпус 2", "Стены")}

        assert plan_groups(paths) is None

    def test_paths_that_differ_only_in_the_case_give_no_plan(self):
        paths = {1: (ROOT, "Корпус 2"), 2: (ROOT, "корпус 2")}

        assert plan_groups(paths) is None

    def test_the_level_is_the_first_index_where_the_headings_differ_counted_from_the_root(self):
        paths = {1: ("А", "Б", "В1"), 2: ("А", "Б", "В2")}
        paths_shallow = {1: ("А1", "Б", "В"), 2: ("А2", "Б", "В")}

        assert plan_groups(paths).level == 2
        assert plan_groups(paths_shallow).level == 0

    def test_groups_are_ordered_by_the_smallest_member_id_whatever_the_input_order(self):
        paths = {9: (ROOT, "Б"), 2: (ROOT, "А"), 5: (ROOT, "Б"), 7: (ROOT, "А")}

        plan = plan_groups(paths)

        assert plan is not None
        assert [(g.title, g.member_ids) for g in plan.groups] == [("А", (2, 7)), ("Б", (5, 9))]

    def test_a_member_with_a_shorter_chain_goes_to_the_rest_group_which_stays_first(self):
        paths = {
            1: (ROOT, "Корпус 2", "Стены"),
            2: (ROOT, "Корпус 2", "Колонны"),
            3: (ROOT, "Корпус 2"),
        }

        plan = plan_groups(paths)

        assert plan is not None
        assert plan.level == 2
        assert [(g.title, g.member_ids) for g in plan.groups] == [
            ("", (3,)), ("Стены", (1,)), ("Колонны", (2,)),
        ]

    def test_the_rest_group_next_to_a_single_heading_is_not_a_split(self):
        paths = {1: (ROOT, "Корпус 2", "Стены"), 2: (ROOT, "Корпус 2")}

        assert plan_groups(paths) is None

    def test_a_member_without_a_chapter_is_in_the_rest_group(self):
        paths = {1: (ROOT, "А"), 2: (ROOT, "Б"), 3: ()}

        plan = plan_groups(paths)

        assert plan is not None
        assert plan.level == 1
        assert plan.groups[0] == SplitGroup(title="", member_ids=(3,))

    def test_members_without_any_chapter_give_no_plan(self):
        assert plan_groups({1: (), 2: ()}) is None

    def test_an_explicit_level_groups_on_that_level(self):
        paths = {
            1: ("А", "Б1", "В1"),
            2: ("А", "Б1", "В2"),
            3: ("А", "Б2", "В1"),
        }

        by_default = plan_groups(paths)
        by_third = plan_groups(paths, level=2)

        assert by_default is not None and by_default.level == 1
        assert [g.member_ids for g in by_default.groups] == [(1, 2), (3,)]
        assert by_third is not None and by_third.level == 2
        assert [g.member_ids for g in by_third.groups] == [(1, 3), (2,)]

    def test_an_explicit_level_where_everyone_has_the_same_heading_gives_no_plan(self):
        paths = {1: ("А", "Б1", "В1"), 2: ("А", "Б2", "В2")}

        assert plan_groups(paths, level=0) is None

    def test_an_explicit_level_deeper_than_every_chain_gives_no_plan(self):
        paths = {1: ("А", "Б1"), 2: ("А", "Б2")}

        assert plan_groups(paths, level=5) is None

    def test_a_single_member_gives_no_plan(self):
        assert plan_groups({1: (ROOT, "Стены")}) is None


# ---------------------------------------------------------------------------
#  choose_rule
# ---------------------------------------------------------------------------

def _groups(*specs: tuple[str, dict[int, tuple[str, ...]]]):
    groups = [SplitGroup(title=title, member_ids=tuple(members)) for title, members in specs]
    chains = {
        member_id: _chain(*path)
        for _title, members in specs
        for member_id, path in members.items()
    }
    return groups, chains


def _covers(rule, chains, member_ids) -> bool:
    return all(evaluate_predicate(rule, chains[member_id]) for member_id in member_ids)


class TestChooseRule:
    def test_the_heading_that_is_the_nearest_chapter_of_every_member_gives_nearest_equals(self):
        groups, chains = _groups(
            ("Стены", {1: (ROOT, "Корпус 2", "Стены"), 2: (ROOT, "Корпус 2", "Стены")}),
            ("Колонны", {3: (ROOT, "Корпус 2", "Колонны")}),
        )

        rule = choose_rule(groups[0], [groups[1]], chains, [])

        assert rule == {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Стены"}
        assert _covers(rule, chains, (1, 2))
        assert not evaluate_predicate(rule, chains[3])

    def test_a_heading_that_is_not_the_nearest_chapter_gives_chain_contains(self):
        groups, chains = _groups(
            ("Надземная часть", {
                1: (ROOT, "Корпус", "Надземная часть", "Колонны"),
                2: (ROOT, "Корпус", "Надземная часть", "Стены"),
            }),
            ("Подземная часть", {3: (ROOT, "Корпус", "Подземная часть", "Колонны")}),
        )

        rule = choose_rule(groups[0], [groups[1]], chains, [])

        assert rule == {"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Надземная часть"}
        assert _covers(rule, chains, (1, 2))
        assert not evaluate_predicate(rule, chains[3])

    def test_chains_of_different_depth_get_a_rule_that_covers_the_whole_group(self):
        groups, chains = _groups(
            ("Колонны", {1: (ROOT, "Корпус", "Колонны")}),
            ("Надземная часть", {2: (ROOT, "Корпус", "Надземная часть", "Колонны")}),
        )

        rule = choose_rule(groups[1], [groups[0]], chains, [])

        assert rule == {"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Надземная часть"}
        assert _covers(rule, chains, (2,))
        assert not evaluate_predicate(rule, chains[1])

    def test_a_heading_found_in_the_chain_of_another_group_gives_no_rule(self):
        groups, chains = _groups(
            ("Колонны", {1: (ROOT, "Корпус", "Колонны")}),
            ("Надземная часть", {2: (ROOT, "Корпус", "Надземная часть", "Колонны")}),
        )

        assert choose_rule(groups[0], [groups[1]], chains, []) is None

    def test_a_rule_that_fires_on_a_member_of_another_group_is_refused(self):
        groups, chains = _groups(
            ("Корпус", {1: (ROOT, "Корпус")}),
            ("Корпус", {2: (ROOT, "Корпус", "Стены")}),
        )

        # Раздел «Корпус» — ближайший у первой группы и лишь предок у второй: «ближайший
        # раздел» чужих не задевает, «есть в цепочке» задевает.
        assert choose_rule(groups[0], [groups[1]], chains, []) == {
            "kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Корпус",
        }
        assert choose_rule(groups[1], [groups[0]], chains, []) is None

    def test_an_earlier_rule_of_the_bucket_that_takes_a_member_first_gives_no_rule(self):
        groups, chains = _groups(
            ("Стены", {1: (ROOT, "Корпус 2", "Стены"), 2: (ROOT, "Корпус 2", "Стены")}),
            ("Колонны", {3: (ROOT, "Корпус 2", "Колонны")}),
        )
        earlier = [{"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Корпус 2"}]

        assert choose_rule(groups[0], [groups[1]], chains, earlier) is None

    def test_an_earlier_rule_that_takes_no_member_of_the_group_does_not_prevent_the_rule(self):
        groups, chains = _groups(
            ("Стены", {1: (ROOT, "Корпус 2", "Стены")}),
            ("Колонны", {3: (ROOT, "Корпус 2", "Колонны")}),
        )
        earlier = [{"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Фасады"}]

        assert choose_rule(groups[0], [groups[1]], chains, earlier) == {
            "kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Стены",
        }

    def test_the_heading_is_compared_normalized_with_the_chapter_of_the_member(self):
        groups, chains = _groups(
            ("Стены:", {1: (ROOT, "Корпус 2", "стены")}),
            ("Колонны", {3: (ROOT, "Корпус 2", "Колонны")}),
        )

        rule = choose_rule(groups[0], [groups[1]], chains, [])

        assert rule == {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Стены:"}
        assert _covers(rule, chains, (1,))

    def test_the_rest_group_has_no_heading_and_gets_no_rule(self):
        groups, chains = _groups(
            ("", {1: (ROOT, "Корпус 2")}),
            ("Стены", {2: (ROOT, "Корпус 2", "Стены")}),
        )

        assert choose_rule(groups[0], [groups[1]], chains, []) is None

    @pytest.mark.parametrize("level_from_the_root", [0, 1, 2])
    def test_the_level_rule_is_never_chosen(self, level_from_the_root):
        path_a = (ROOT, "Корпус", "Стены")
        path_b = (ROOT, "Корпус", "Колонны")
        titles = {0: (ROOT, ROOT), 1: ("Корпус", "Корпус"), 2: ("Стены", "Колонны")}
        groups, chains = _groups(
            (titles[level_from_the_root][0], {1: path_a}),
            (titles[level_from_the_root][1], {2: path_b}),
        )

        rule = choose_rule(groups[0], [groups[1]], chains, [])

        assert rule is None or rule["kind"] in (
            PREDICATE_NEAREST_CHAPTER_EQUALS, PREDICATE_CHAPTER_CHAIN_CONTAINS,
        )

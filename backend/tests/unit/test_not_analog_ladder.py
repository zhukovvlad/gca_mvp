"""«Не аналог» в ядре подбора: «ошибка в цене» в пуле, тендерная отметка у строки КП, причина `excluded`,
след исключённого, ленивый пул без отметок, предикат блока А (спека «Не аналог» §3, §5 п. 1–2).

Входы — фабрики `test_analog_ladder` (семья 1, варианты W6/W8, зона `underground`) и
`test_simplify_ladder` («по частям»). Счётчик построений пула без отметок — шпион на `_build_pool` в тесте,
не счётчик в коде.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from money.inflation import YearMonth
from services import analog_ladder as al
from services import reference_blocks as rb
from services.analog_ladder import WorkHistory, excluded_trace, ladder_for
from tests.unit.test_analog_ladder import (
    CLASS_A,
    CLASS_B,
    FAMILY,
    OTHER_VARIANT,
    RELEASED,
    REQUIRED,
    W8,
    contract,
    ids,
    info,
    kp,
    line,
    run,
)
from tests.unit.test_simplify_ladder import composite_kp, parts_of, reference, two_parts_scene

D = Decimal
UNDER = "underground"
ABOVE = "aboveground"
MONTH = YearMonth(2026, 1)
#: Другие значения обязательного параметра: ни одна ступень такую строку не берёт.
NO_MATCH = {RELEASED: 1, REQUIRED: 6}


def marked(item, *, price_error: bool = True):
    return dataclasses.replace(item, price_error=price_error)


def excl(work, *pids: int):
    return dataclasses.replace(work, excluded=frozenset(pids))


def history_of(lines, contracts, *, zone_required=frozenset()) -> WorkHistory:
    return WorkHistory(
        lines, {FAMILY: info()}, {c.contract_id: c for c in contracts}, factors=None, zone_required=zone_required
    )


def ladder(work, history: WorkHistory):
    return ladder_for(work, history, tender_class_id=CLASS_A, tender_contractor_id=99, target_month=MONTH)


def trace(work, history: WorkHistory):
    return excluded_trace(work, history, tender_class_id=CLASS_A)


def trace_ids(result) -> list[tuple[int, tuple[str, int]]]:
    return [(cand.line.position_item_id, step) for cand, step in result]


@pytest.fixture
def unmarked_builds(monkeypatch):
    """Число построений пула без отметок (`_build_pool(..., skip_price_errors=False)`)."""
    calls: list[int] = []
    original = WorkHistory._build_pool

    def spy(self, family_id, *, skip_price_errors=True):
        if not skip_price_errors:
            calls.append(family_id)
        return original(self, family_id, skip_price_errors=skip_price_errors)

    monkeypatch.setattr(WorkHistory, "_build_pool", spy)
    return calls


# ---------------------------------------------------------------------------
#  «Ошибка в цене» — в пуле: нет ни у одного читателя
# ---------------------------------------------------------------------------


class TestPriceErrorIsOutOfThePool:
    def test_the_marked_line_is_not_an_analog_and_the_neighbour_of_the_rung_is_a_fork_of_one(self):
        lines = [marked(line(1, 1, "50")), line(2, 2, "100")]

        result = run(kp(), lines, [contract(1), contract(2)])

        assert (result.rung_no, ids(result)) == (1, [2])
        assert result.lowest is not None and result.lowest.analog_id == 2
        assert result.remainder_ids == {2}

    def test_the_marked_line_does_not_make_the_neighbour_an_outlier_and_does_not_move_the_median(self):
        lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "110"), marked(line(4, 4, "1000"))]

        result = run(kp(), lines, [contract(i) for i in (1, 2, 3, 4)])

        assert ids(result) == [1, 2, 3] or sorted(ids(result)) == [1, 2, 3]
        assert result.remainder_ids == {1, 2, 3}
        assert all(a.outlier_ratio is None for a in result.analogs)

    def test_the_marked_line_is_not_the_freshest_nor_the_contractor_figure(self):
        lines = [marked(line(1, 1, "90")), line(2, 2, "100")]
        contracts = [
            contract(1, contractor=99, period=YearMonth(2026, 1)),
            contract(2, contractor=50, period=YearMonth(2025, 1)),
        ]

        result = run(kp(), lines, contracts)

        assert result.freshest is not None and result.freshest.analog_id == 2
        assert result.contractor is None

    def test_a_marked_line_with_an_unknown_vat_base_does_not_drop_its_contract_to_vat_dropped(self):
        plain = run(kp(), [line(1, 1, "100", vat=None)], [contract(1)])
        flagged = run(kp(), [marked(line(1, 1, "100", vat=None))], [contract(1)])

        assert plain.vat_dropped == (1,)
        assert flagged.vat_dropped == ()

    def test_the_marked_line_is_not_a_part_candidate(self):
        lines, contracts = two_parts_scene()
        lines = [marked(item) if item.position_item_id == 5 else item for item in lines]

        parts = parts_of(reference(composite_kp(), lines, contracts))

        assert (parts[21].analogs, parts[21].max) == (4, D("42"))

    def test_a_lump_line_stays_out_of_the_unmarked_pool_too(self):
        lines = [dataclasses.replace(marked(line(1, 1, "100")), lump=True)]

        result = run(kp(), lines, [contract(1)])

        assert result.rung_no is None
        assert result.no_history_reason == "no_family"
        assert result.base_reason == "no_family"

    def test_the_marked_line_is_out_of_the_zone_block_and_the_link_block_pool(self):
        history = history_of([marked(line(1, 1, "100", zone=ABOVE)), line(2, 2, "110", zone=ABOVE)], [contract(1), contract(2)])

        assert [c.line.position_item_id for c in history.pool(FAMILY).candidates] == [2]
        assert [c.line.position_item_id for c in history.pool_unmarked(FAMILY).candidates] == [1, 2]

    def test_the_marked_line_is_not_a_row_of_the_zone_block(self):
        # семья с флажком: своей зоны нет — блок А открыт; в другой зоне отмеченная строка и соседняя
        lines = [marked(line(1, 1, "100", zone=ABOVE)), line(2, 2, "110", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2)], zone_required=frozenset({FAMILY}))
        work = dataclasses.replace(kp(), zone_required=True)

        groups = rb.zone_groups(work, history, tender_class_id=CLASS_A, tender_contractor_id=99, target_month=MONTH)

        assert [(g.zone, [r.position_item_id for r in g.rows]) for g in groups] == [(ABOVE, [2])]

    def test_the_marked_line_is_not_a_row_of_the_link_block(self):
        from tests.unit.test_ref_analogs_blocks import _link

        link_line = marked(dataclasses.replace(line(1, 1, "100", family=2, zone=UNDER), unit_id=2))
        other_line = dataclasses.replace(line(2, 2, "120", family=2, zone=UNDER), unit_id=2)
        history = WorkHistory(
            [link_line, other_line], {FAMILY: info(), 2: info()}, {1: contract(1), 2: contract(2)}, factors=None
        )

        groups = rb.link_groups(
            kp(), 3, history, [_link()], {2: "м²", 3: "м³"}, {}, tender_class_id=CLASS_A, target_month=MONTH
        )

        assert [row.position_item_id for group in groups for row in group.rows] == [2]


# ---------------------------------------------------------------------------
#  Тендерная отметка — только для этой строки КП
# ---------------------------------------------------------------------------


class TestTenderMarkIsPerKpLine:
    def test_the_excluded_line_is_not_a_candidate_for_this_line_only(self):
        lines = [line(1, 1, "50"), line(2, 2, "100")]
        contracts = [contract(1), contract(2)]

        this = run(excl(kp(), 1), lines, contracts)
        other = run(kp(), lines, contracts)

        assert ids(this) == [2]
        assert sorted(ids(other)) == [1, 2]

    def test_the_excluded_line_does_not_drop_its_contract_to_vat_dropped(self):
        lines = [line(1, 1, "100", vat=None)]

        assert run(excl(kp(), 1), lines, [contract(1)]).vat_dropped == ()
        assert run(kp(), lines, [contract(1)]).vat_dropped == (1,)

    def test_the_excluded_line_is_not_a_part_candidate_for_this_line(self):
        lines, contracts = two_parts_scene()

        parts = parts_of(reference(excl(composite_kp(), 5), lines, contracts))
        untouched = parts_of(reference(composite_kp(), lines, contracts))

        assert (parts[21].analogs, parts[21].max) == (4, D("42"))
        assert untouched[21].analogs == 5

    def test_the_excluded_line_is_not_a_candidate_of_the_linked_family_block(self):
        link_line = dataclasses.replace(line(1, 1, "100", family=2, zone=UNDER), unit_id=2)
        other_line = dataclasses.replace(line(2, 2, "120", family=2, zone=UNDER), unit_id=2)
        families = {FAMILY: info(), 2: info()}
        history = WorkHistory([link_line, other_line], families, {1: contract(1), 2: contract(2)}, factors=None)
        from tests.unit.test_ref_analogs_blocks import _link

        def rows_of(work):
            groups = rb.link_groups(
                work, 3, history, [_link()], {2: "м²", 3: "м³"}, {}, tender_class_id=CLASS_A, target_month=MONTH
            )
            return [row.position_item_id for group in groups for row in group.rows]

        assert sorted(rows_of(kp())) == [1, 2]
        assert rows_of(excl(kp(), 1)) == [2]

    def test_the_excluded_line_of_another_zone_is_not_a_row_of_the_zone_block(self):
        # блок А берёт строки других зон через ladder_for с той же KpWork (спека §5 п. 2)
        lines = [line(1, 1, "100", zone=UNDER), line(2, 2, "110", zone=ABOVE), line(3, 3, "120", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2), contract(3)], zone_required=frozenset({FAMILY}))

        def rows_of(work):
            groups = rb.zone_groups(
                dataclasses.replace(work, zone_required=True),
                history,
                tender_class_id=CLASS_A,
                tender_contractor_id=99,
                target_month=MONTH,
            )
            return [(g.zone, sorted(r.position_item_id for r in g.rows)) for g in groups]

        assert rows_of(excl(kp(), 1, 3)) == [(ABOVE, [2])]
        assert rows_of(excl(kp(), 1)) == [(ABOVE, [2, 3])]


# ---------------------------------------------------------------------------
#  Лестница идёт дальше
# ---------------------------------------------------------------------------


class TestLadderMovesOn:
    def test_the_only_candidate_of_own_zone_is_excluded_and_the_other_zone_takes_the_rung(self):
        lines = [line(1, 1, "100", zone=UNDER), line(2, 2, "110", zone=ABOVE), line(3, 3, "120", zone=ABOVE)]
        contracts = [contract(1), contract(2), contract(3)]

        result = run(excl(kp(), 1), lines, contracts)

        assert (result.rung_no, result.zone_scope) == (1, "other")
        assert sorted(ids(result)) == [2, 3]
        assert result.remainder_ids == {2, 3}
        assert (result.lowest.value, result.lowest.analog_id) == (D("110"), 2)

    def test_the_outliers_are_recomputed_over_the_remaining(self):
        lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "100"), line(4, 4, "1000")]
        contracts = [contract(i) for i in (1, 2, 3, 4)]

        full = run(kp(), lines, contracts)
        left = run(excl(kp(), 1, 2, 3), lines, contracts)

        assert [a.position_item_id for a in full.analogs if a.outlier_ratio is not None] == [4]
        assert ids(left) == [4]
        assert left.analogs[0].outlier_ratio is None
        assert left.remainder_ids == {4}

    def test_a_rung_left_empty_hands_over_to_the_next_rung(self):
        lines = [line(1, 1, "100"), line(2, 2, "300", variant=W8, values=OTHER_VARIANT)]
        contracts = [contract(1), contract(2)]

        result = run(excl(kp(), 1), lines, contracts)

        assert result.rung_no == 2
        assert ids(result) == [2]

    def test_the_only_candidate_of_own_zone_rung_four_is_excluded_and_rung_four_of_another_zone_is_a_fork_of_two(self):
        # бриф задачи 3, «лестница идёт дальше»: пара (own, 4) пуста после исключения — (other, 4)
        rung_four = {"variant": W8, "values": {RELEASED: 2, REQUIRED: 5}}
        lines = [
            line(1, 1, "100", zone=UNDER, **rung_four),
            line(2, 2, "110", zone=ABOVE, **rung_four),
            line(3, 3, "130", zone=ABOVE, **rung_four),
        ]
        contracts = [contract(1, cls=CLASS_B), contract(2, cls=CLASS_B), contract(3, cls=CLASS_B)]

        full = run(kp(), lines, contracts)
        result = run(excl(kp(), 1), lines, contracts)

        assert (full.rung_no, full.zone_scope, ids(full)) == (4, "own", [1])
        assert (result.rung_no, result.zone_scope) == (4, "other")
        assert sorted(ids(result)) == [2, 3]
        assert result.remainder_ids == {2, 3}
        assert (result.lowest.value, result.lowest.analog_id) == (D("110"), 2)


# ---------------------------------------------------------------------------
#  Причина и base_reason
# ---------------------------------------------------------------------------


class TestExcludedReason:
    def test_no_rung_but_one_would_be_without_marks_is_excluded(self):
        for lines, work in [
            ([marked(line(1, 1, "100"))], kp()),
            ([line(1, 1, "100")], excl(kp(), 1)),
        ]:
            result = run(work, lines, [contract(1)])

            assert (result.rung_no, result.no_history_reason) == (None, "excluded")

    def test_every_candidate_of_the_family_is_marked_is_excluded_not_no_family(self):
        lines = [marked(line(1, 1, "100")), marked(line(2, 2, "110"))]

        result = run(kp(), lines, [contract(1), contract(2)])

        assert result.no_history_reason == "excluded"
        # base_reason — прежняя цепочка по ОБЫЧНОМУ пулу, где отмеченных нет (спека §3 п. 6)
        assert result.base_reason == "no_family"

    def test_excluded_comes_before_composite(self):
        # сборная строка: прежняя цепочка дала бы composite, но без отметки ступень 1 нашлась бы
        result = run(excl(composite_kp(), 1), [line(1, 1, "100")], [contract(1)])

        assert (result.rung_no, result.no_history_reason, result.base_reason) == (None, "excluded", "composite")

    def test_excluded_comes_before_no_params(self):
        result = run(excl(kp(), 1), [line(1, 1, "100")], [contract(1)])

        assert (result.no_history_reason, result.base_reason) == ("excluded", "no_params")

    def test_without_candidates_and_marks_the_old_reason_stays(self):
        assert run(kp(), [], []).no_history_reason == "no_family"
        other = [line(1, 1, "100", variant=W8, values=NO_MATCH)]
        assert run(kp(), other, [contract(1)]).no_history_reason == "no_params"

    def test_a_mark_that_does_not_bring_a_rung_back_keeps_the_old_reason(self):
        lines = [marked(line(1, 1, "100", variant=W8, values=NO_MATCH))]

        result = run(kp(), lines, [contract(1)])

        assert result.no_history_reason == result.base_reason == "no_family"

    def test_base_reason_is_the_old_chain_over_the_ordinary_pool(self):
        # семья с флажком: исключён последний кандидат своей зоны, строка другой зоны есть — прежняя причина no_zone_match
        lines = [line(1, 1, "100", zone=UNDER), line(2, 2, "110", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2)], zone_required=frozenset({FAMILY}))

        result = ladder(excl(kp(), 1), history)

        assert (result.no_history_reason, result.base_reason) == ("excluded", "no_zone_match")

    def test_base_reason_equals_the_reason_for_other_rows_without_a_rung_and_is_none_with_a_rung(self):
        plain = run(kp(), [], [])
        found = run(kp(), [line(1, 1, "100")], [contract(1)])

        assert (plain.no_history_reason, plain.base_reason) == ("no_family", "no_family")
        assert found.rung_no == 1 and found.base_reason is None


# ---------------------------------------------------------------------------
#  Блок А: предикат и группы
# ---------------------------------------------------------------------------


def _ladder_with(reason, base):
    return al.WorkLadder(
        rung_no=None, released=(), other_class=False, zone=UNDER, analogs=(), remainder_ids=frozenset(),
        lowest=None, freshest=None, all_outliers=False, kp_ratio=None, no_history_reason=reason, vat_dropped=(),
        base_reason=base,
    )


class TestOpensZoneBlock:
    @pytest.mark.parametrize(
        ("reason", "base", "expected"),
        [
            ("no_zone_match", "no_zone_match", True),
            ("excluded", "no_zone_match", True),
            ("excluded", "no_params", False),
            ("excluded", "no_family", False),
            ("no_params", "no_params", False),
            ("no_family", "no_family", False),
        ],
    )
    def test_predicate(self, reason, base, expected):
        assert rb.opens_zone_block(_ladder_with(reason, base)) is expected

    def test_a_family_with_the_flag_shows_the_other_zone_under_excluded_over_no_zone_match(self):
        lines = [line(1, 1, "100", zone=UNDER), line(2, 2, "110", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2)], zone_required=frozenset({FAMILY}))
        work = dataclasses.replace(excl(kp(), 1), zone_required=True)

        groups = rb.zone_groups(work, history, tender_class_id=CLASS_A, tender_contractor_id=99, target_month=MONTH)

        assert [(g.zone, [r.position_item_id for r in g.rows]) for g in groups] == [(ABOVE, [2])]

    def test_excluded_over_another_base_does_not_open_the_block(self):
        lines = [marked(line(1, 1, "100", zone=UNDER)), line(2, 2, "110", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2)])

        # без флажка зона не отсекает: у строки ступень найдена в «other», блок не нужен
        assert rb.zone_groups(kp(), history, tender_class_id=CLASS_A, tender_contractor_id=99, target_month=MONTH) == ()


# ---------------------------------------------------------------------------
#  След исключённого
# ---------------------------------------------------------------------------


class TestExcludedTrace:
    def test_the_i_beam_scenario_one_line_of_own_zone_excluded_the_rung_found_in_another_zone(self):
        lines = [
            line(1, 1, "100", zone=UNDER, variant=W8, values={RELEASED: 2, REQUIRED: 5}),
            line(2, 2, "110", zone=ABOVE),
        ]
        contracts = [contract(1, cls=CLASS_B), contract(2)]
        history = history_of(lines, contracts)
        work = excl(kp(), 1)

        found = ladder(work, history)
        steps = trace(work, history)

        assert (found.rung_no, found.zone_scope) == (1, "other")
        assert trace_ids(steps) == [(1, ("own", 4))]

    def test_the_price_error_mark_gives_the_same_trace(self):
        lines = [
            marked(line(1, 1, "100", zone=UNDER, variant=W8, values={RELEASED: 2, REQUIRED: 5})),
            line(2, 2, "110", zone=ABOVE),
        ]
        history = history_of(lines, [contract(1, cls=CLASS_B), contract(2)])

        assert trace_ids(trace(kp(), history)) == [(1, ("own", 4))]

    def test_all_excluded_the_trace_goes_through_all_pairs_to_the_end(self):
        lines = [line(1, 1, "100", zone=UNDER), line(2, 2, "110", zone=ABOVE)]
        history = history_of(lines, [contract(1), contract(2)])

        steps = trace(excl(kp(), 1, 2), history)

        assert trace_ids(steps) == [(1, ("own", 1)), (2, ("other", 1))]

    def test_a_mark_the_selection_did_not_touch_is_not_in_the_trace(self):
        lines = [line(1, 1, "100"), marked(line(2, 2, "120", variant=W8, values=OTHER_VARIANT), price_error=True)]
        history = history_of(lines, [contract(1), contract(2, cls=CLASS_B)])

        # S0 = S1 = (own, 1); отмеченная строка стоит в паре (own, 4), после S1
        assert trace(kp(), history) == ()

    def test_a_marked_candidate_standing_in_the_s1_pair_itself_is_in_the_trace(self):
        lines = [line(1, 1, "100"), marked(line(2, 2, "120"))]
        history = history_of(lines, [contract(1), contract(2)])

        assert trace_ids(trace(kp(), history)) == [(2, ("own", 1))]

    def test_no_s0_gives_an_empty_trace(self):
        lines = [marked(line(1, 1, "100", variant=W8, values=NO_MATCH))]
        history = history_of(lines, [contract(1)])

        assert trace(kp(), history) == ()

    def test_each_candidate_is_given_with_the_first_pair_where_it_would_stand(self):
        # строка 1 стоит и в (own, 1) (тот же вариант), и в (own, 3) (другой класс не подходит — только ступень 1)
        lines = [marked(line(1, 1, "100")), marked(line(2, 2, "110", zone=ABOVE))]
        history = history_of(lines, [contract(1), contract(2)])

        steps = trace(kp(), history)

        assert trace_ids(steps) == [(1, ("own", 1)), (2, ("other", 1))]


# ---------------------------------------------------------------------------
#  Пул без отметок строится лениво
# ---------------------------------------------------------------------------


class TestUnmarkedPoolIsLazy:
    def test_a_row_with_a_rung_never_builds_it_in_ladder_for(self, unmarked_builds):
        history = history_of([marked(line(1, 1, "100")), line(2, 2, "110")], [contract(1), contract(2)])

        assert ladder(kp(), history).rung_no == 1
        assert unmarked_builds == []

    def test_a_row_without_a_rung_in_a_family_without_marks_never_builds_it(self, unmarked_builds):
        history = history_of([line(1, 1, "100", variant=W8, values=NO_MATCH)], [contract(1)])

        assert ladder(kp(), history).rung_no is None
        assert unmarked_builds == []

    def test_a_row_without_a_rung_in_a_family_with_marks_builds_it_once(self, unmarked_builds):
        history = history_of([marked(line(1, 1, "100"))], [contract(1)])

        assert ladder(kp(), history).no_history_reason == "excluded"
        assert ladder(kp(), history).no_history_reason == "excluded"
        assert unmarked_builds == [FAMILY]

    def test_a_row_without_a_rung_with_a_tender_mark_builds_it(self, unmarked_builds):
        history = history_of([line(1, 1, "100")], [contract(1)])

        assert ladder(excl(kp(), 1), history).no_history_reason == "excluded"
        assert unmarked_builds == [FAMILY]

    def test_the_trace_builds_it_even_for_a_row_with_a_rung(self, unmarked_builds):
        lines = [
            line(1, 1, "100", zone=UNDER, variant=W8, values={RELEASED: 2, REQUIRED: 5}),
            line(2, 2, "110", zone=ABOVE),
        ]
        history = history_of(lines, [contract(1, cls=CLASS_B), contract(2)])
        work = excl(kp(), 1)

        assert ladder(work, history).rung_no == 1
        assert unmarked_builds == []
        assert trace_ids(trace(work, history)) == [(1, ("own", 4))]
        assert unmarked_builds == [FAMILY]

    def test_the_trace_without_marks_builds_nothing_and_is_empty(self, unmarked_builds):
        history = history_of([line(1, 1, "100")], [contract(1)])

        assert trace(kp(), history) == ()
        assert unmarked_builds == []

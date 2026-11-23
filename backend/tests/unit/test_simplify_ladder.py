"""Лестница аналогов: форма значения на ступенях 2 и 4, причина `composite`, «по частям» и цель
«с этим подрядчиком» (спека упрощения каталога §2.2, §2.8, §2.9).

Чистый расчёт без базы, помощники — из `test_analog_ladder`. Условные имена: параметр 10 («Тип
конструкции», отпускаемый), параметр 11 («Класс», обязательный), параметр 12 («Марка», отпускаемый,
только в трёхпараметровой семье). Значения параметра 10: плиты 21, балки 22, составное «плиты + балки»
77 и другое составное 78. Эталоны посчитаны руками.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from money.inflation import YearMonth
from services.analog_ladder import (
    CompositeValue,
    FamilyLadderInfo,
    Figure,
    PartReference,
    WorkHistory,
    parts_reference,
)
from services.our_price import LineState, TargetChoice, resolve_target, target_analog
from tests.unit.test_analog_comment import LOW, MID, comparable
from tests.unit.test_analog_comment import ladder as comment_ladder
from tests.unit.test_analog_ladder import (
    CLASS_A,
    CLASS_B,
    FAMILY,
    RELEASED,
    REQUIRED,
    SCHEMA,
    W6,
    W8,
    contract,
    ids,
    info,
    kp,
    line,
    run,
)

D = Decimal

PLATES = 21
BEAMS = 22
BOTH = 77
EITHER = 78
MARK = 12


def composite_kp(*, form: int = BOTH, **kwargs):
    """Сборная строка: отпускаемый параметр 10 равен составному значению `form`."""
    values = {RELEASED: form, REQUIRED: 5}
    return dataclasses.replace(kp(values=values, **kwargs), forms={RELEASED: form})


def simple_kp(value: int = PLATES, **kwargs):
    return kp(values={RELEASED: value, REQUIRED: 5}, **kwargs)


def hline(pid, cid, price, value, *, required=5, form=None, variant=None, **kwargs):
    """Строка истории: параметр 10 равен `value`; `form` — id составного, если `value` составное."""
    made = line(
        pid,
        cid,
        price,
        variant=W8 if variant is None else variant,
        values={RELEASED: value, REQUIRED: required},
        **kwargs,
    )
    return dataclasses.replace(made, forms={} if form is None else {RELEASED: form})


def composite_line(pid, cid, price, form=BOTH, **kwargs):
    return hline(pid, cid, price, form, form=form, **kwargs)


def contracts_of(*cids, **kwargs):
    return [contract(cid, **kwargs) for cid in cids]


# ---------------------------------------------------------------------------
#  Форма на ступенях 2 и 4 (спека §2.8, К7)
# ---------------------------------------------------------------------------


class TestFormOnRungsTwoAndFour:
    def test_a_composite_row_does_not_take_a_simple_candidate(self):
        ladder = run(composite_kp(), [hline(1, 1, "100", PLATES)], contracts_of(1))

        assert ladder.rung_no is None
        assert ladder.no_history_reason == "composite"

    def test_a_composite_row_takes_the_same_composite(self):
        ladder = run(composite_kp(), [composite_line(1, 1, "100")], contracts_of(1))

        assert (ladder.rung_no, ids(ladder)) == (2, [1])

    def test_a_composite_row_does_not_take_another_composite(self):
        ladder = run(composite_kp(), [composite_line(1, 1, "100", form=EITHER)], contracts_of(1))

        assert ladder.rung_no is None
        assert ladder.no_history_reason == "composite"

    def test_a_simple_row_takes_the_simple_candidate_and_not_the_composite(self):
        lines = [composite_line(1, 1, "100"), hline(2, 2, "110", BEAMS, variant=102)]

        ladder = run(simple_kp(), lines, contracts_of(1, 2))

        assert (ladder.rung_no, ids(ladder)) == (2, [2])

    def test_a_row_with_no_value_yet_takes_a_simple_candidate_like_a_simple_row(self):
        unspecified = dataclasses.replace(kp(values={RELEASED: None, REQUIRED: 5}), forms={})

        ladder = run(unspecified, [hline(1, 1, "100", PLATES)], contracts_of(1))

        assert (ladder.rung_no, ids(ladder)) == (2, [1])

    def test_a_conflict_on_the_released_parameter_is_the_simple_form_and_does_not_interfere(self):
        conflicted = dataclasses.replace(composite_kp(), conflicted=frozenset({RELEASED}))
        lines = [composite_line(1, 1, "100"), hline(2, 2, "110", PLATES, variant=102)]

        ladder = run(conflicted, lines, contracts_of(1, 2))

        assert (ladder.rung_no, ids(ladder)) == (2, [2])

    def test_a_candidate_with_a_conflict_on_the_released_parameter_is_the_simple_form(self):
        conflicted_candidate = dataclasses.replace(composite_line(1, 1, "100"), conflicted=frozenset({RELEASED}))

        ladder = run(simple_kp(), [conflicted_candidate], contracts_of(1))

        assert (ladder.rung_no, ids(ladder)) == (2, [1])

    def test_the_form_also_counts_on_the_fourth_rung(self):
        other = dict(cls=CLASS_B)
        simple_other = run(composite_kp(), [hline(1, 1, "100", PLATES)], contracts_of(1, **other))
        composite_other = run(composite_kp(), [composite_line(1, 1, "100")], contracts_of(1, **other))

        assert simple_other.rung_no is None
        assert (composite_other.rung_no, ids(composite_other)) == (4, [1])

    def test_a_composite_row_takes_its_own_variant_on_rungs_one_and_three(self):
        same_variant = dataclasses.replace(composite_line(1, 1, "100"), variant_id=W6)
        class_a = run(composite_kp(), [same_variant], contracts_of(1))
        class_b = run(composite_kp(), [same_variant], contracts_of(1, cls=CLASS_B))

        assert (class_a.rung_no, class_b.rung_no) == (1, 3)

    def test_a_composite_non_released_parameter_matches_by_value_id_as_before(self):
        values = {RELEASED: 1, REQUIRED: 80}
        row = dataclasses.replace(kp(values=values), forms={REQUIRED: 80})
        same = dataclasses.replace(line(1, 1, "100", variant=W8, values=values), forms={REQUIRED: 80})
        other = line(2, 2, "110", variant=102, values={RELEASED: 1, REQUIRED: 81})

        ladder = run(row, [same, other], contracts_of(1, 2))

        assert (ladder.rung_no, ids(ladder)) == (2, [1])


# ---------------------------------------------------------------------------
#  Причина «composite»: порядок no_family, path_conflict, composite, no_params, no_zone_match
# ---------------------------------------------------------------------------


class TestCompositeReason:
    def test_the_reason_goes_before_no_params(self):
        other_class_value = hline(1, 1, "100", PLATES, required=6)

        ladder = run(composite_kp(), [other_class_value], contracts_of(1))

        assert ladder.no_history_reason == "composite"

    def test_the_reason_goes_before_no_zone_match(self):
        elsewhere = hline(1, 1, "100", PLATES, zone="aboveground")

        ladder = run(composite_kp(), [elsewhere], contracts_of(1))

        assert ladder.no_history_reason == "composite"

    def test_a_simple_row_in_the_same_pool_keeps_no_params(self):
        ladder = run(simple_kp(), [hline(1, 1, "100", PLATES, required=6)], contracts_of(1))

        assert ladder.no_history_reason == "no_params"

    def test_no_family_goes_before_composite(self):
        ladder = run(composite_kp(), [], [])

        assert ladder.no_history_reason == "no_family"

    def test_path_conflict_goes_before_composite(self):
        conflicted = dataclasses.replace(composite_kp(), conflicted=frozenset({REQUIRED}))

        ladder = run(conflicted, [hline(1, 1, "100", PLATES)], contracts_of(1))

        assert ladder.no_history_reason == "path_conflict"

    def test_a_conflict_on_the_composite_parameter_is_not_a_composite_row(self):
        conflicted = dataclasses.replace(composite_kp(), conflicted=frozenset({RELEASED}))

        ladder = run(conflicted, [hline(1, 1, "100", PLATES, required=6)], contracts_of(1))

        assert ladder.no_history_reason == "no_params"


# ---------------------------------------------------------------------------
#  «По частям» (спека §2.8)
# ---------------------------------------------------------------------------

TYPE_NAME = "Тип конструкции"
COMPOSITE = CompositeValue("all", ((PLATES, "плиты"), (BEAMS, "балки")))


def type_info() -> FamilyLadderInfo:
    return dataclasses.replace(info(), names={RELEASED: TYPE_NAME, REQUIRED: "Класс"})


def three_info() -> FamilyLadderInfo:
    return FamilyLadderInfo(
        schema_id=SCHEMA,
        main_parameter_ids=(RELEASED, REQUIRED, MARK),
        releasable_ids=frozenset({RELEASED, MARK}),
        names={RELEASED: TYPE_NAME, REQUIRED: "Класс", MARK: "Марка"},
    )


def reference(work, lines, contracts, *, families=None, composites=None, cls=CLASS_A, zone_required=frozenset()):
    history = WorkHistory(
        lines,
        {FAMILY: type_info()} if families is None else families,
        {c.contract_id: c for c in contracts},
        factors=None,
        zone_required=zone_required,
    )
    return parts_reference(
        work,
        history,
        tender_class_id=cls,
        composites={RELEASED: COMPOSITE} if composites is None else composites,
        tender_contractor_id=99,
        target_month=YearMonth(2026, 1),
    )


def parts_of(result):
    (parameter,) = result
    return {part.value_id: part for part in parameter["parts"]}


def priced(first_pid, value, prices, *, first_cid, **kwargs):
    """Строки одной части: у каждой свой договор."""
    return [
        hline(first_pid + i, first_cid + i, price, value, variant=300 + first_pid + i, **kwargs)
        for i, price in enumerate(prices)
    ]


def two_parts_scene():
    """Плиты 36, 37, 40, 42 и выброс 400 (у плит два договора на пять строк), балки 45, 48, 51,
    составное 100 в счёт частей не идёт."""
    plates = [
        hline(1, 1, "36", PLATES, variant=301),
        hline(2, 1, "37", PLATES, variant=302),
        hline(3, 2, "40", PLATES, variant=303),
        hline(4, 3, "42", PLATES, variant=304),
        hline(5, 4, "400", PLATES, variant=305),
    ]
    beams = priced(11, BEAMS, ["45", "48", "51"], first_cid=11)
    return plates + beams + [composite_line(21, 21, "100")], contracts_of(1, 2, 3, 4, 11, 12, 13, 21)


class TestPartsReference:
    def test_every_part_has_its_own_fork_and_its_own_lowest_even_when_the_parameter_is_released(self):
        lines, contracts = two_parts_scene()

        (parameter,) = reference(composite_kp(), lines, contracts)
        parts = {part.value_id: part for part in parameter["parts"]}

        assert (parameter["parameter_id"], parameter["parameter_name"], parameter["kind"]) == (
            RELEASED,
            TYPE_NAME,
            "all",
        )
        assert [part.value_id for part in parameter["parts"]] == [PLATES, BEAMS]
        assert parts[PLATES] == PartReference(
            value_id=PLATES,
            value="плиты",
            analogs=5,
            contracts=4,
            min=D("36"),
            max=D("42"),
            lowest_analog_id=1,
            lowest_contract_id=1,
            other_class=False,
            zone_scope="own",
        )
        assert parts[BEAMS] == PartReference(
            value_id=BEAMS,
            value="балки",
            analogs=3,
            contracts=3,
            min=D("45"),
            max=D("51"),
            lowest_analog_id=11,
            lowest_contract_id=11,
            other_class=False,
            zone_scope="own",
        )

    def test_a_part_with_candidates_only_in_another_class_is_marked_other_class(self):
        lines = [hline(1, 1, "50", PLATES, variant=301), hline(2, 2, "60", BEAMS, variant=302)]
        contracts = [contract(1), contract(2, cls=CLASS_B)]

        parts = parts_of(reference(composite_kp(), lines, contracts))

        assert (parts[PLATES].other_class, parts[PLATES].analogs, parts[PLATES].min) == (False, 1, D("50"))
        assert (parts[BEAMS].other_class, parts[BEAMS].analogs, parts[BEAMS].min) == (True, 1, D("60"))

    def test_the_tender_class_wins_over_another_class_for_the_same_part(self):
        lines = [hline(1, 1, "50", PLATES, variant=301), hline(2, 2, "10", PLATES, variant=302)]
        contracts = [contract(1), contract(2, cls=CLASS_B)]

        part = parts_of(reference(composite_kp(), lines, contracts))[PLATES]

        assert (part.other_class, part.analogs, part.min, part.max) == (False, 1, D("50"), D("50"))

    def test_a_part_with_no_candidates_anywhere_has_no_analogs_and_no_figures(self):
        parts = parts_of(reference(composite_kp(), [hline(1, 1, "50", PLATES, variant=301)], contracts_of(1)))

        assert parts[BEAMS] == PartReference(
            value_id=BEAMS,
            value="балки",
            analogs=0,
            contracts=0,
            min=None,
            max=None,
            lowest_analog_id=None,
            lowest_contract_id=None,
            other_class=False,
            zone_scope=None,
        )

    def test_the_outlier_of_a_part_is_in_the_count_but_not_in_the_fork_or_the_lowest(self):
        lines = [
            hline(1, 1, "900", PLATES, variant=301),
            hline(2, 2, "40", PLATES, variant=302),
            hline(3, 3, "41", PLATES, variant=303),
            hline(4, 4, "42", PLATES, variant=304),
        ]

        part = parts_of(reference(composite_kp(), lines, contracts_of(1, 2, 3, 4)))[PLATES]

        assert (part.analogs, part.contracts) == (4, 4)
        assert (part.min, part.max, part.lowest_analog_id) == (D("40"), D("42"), 2)

    def test_a_part_whose_candidates_are_all_outliers_has_no_fork_but_counts_them(self):
        lines = [hline(1, 1, "1000", PLATES, variant=301), hline(2, 2, "2", PLATES, variant=302)]

        part = parts_of(reference(composite_kp(), lines, contracts_of(1, 2)))[PLATES]

        assert part.analogs == 2
        assert (part.min, part.max, part.lowest_analog_id, part.lowest_contract_id) == (None, None, None, None)

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param(dict(required=6), id="another-value-of-a-required-parameter"),
            pytest.param(dict(zone="aboveground"), id="another-zone"),
            pytest.param(dict(schema=SCHEMA - 1), id="another-schema-version"),
            pytest.param(dict(conflicted=frozenset({RELEASED})), id="conflict-on-the-searched-parameter"),
            pytest.param(dict(conflicted=frozenset({REQUIRED})), id="conflict-on-a-required-parameter"),
        ],
    )
    def test_a_candidate_that_breaks_one_condition_is_not_counted(self, bad):
        lines = [hline(1, 1, "50", PLATES, variant=301), hline(2, 2, "10", PLATES, variant=302, **bad)]

        part = parts_of(reference(composite_kp(), lines, contracts_of(1, 2)))[PLATES]

        assert (part.analogs, part.min) == (1, D("50"))

    def test_a_row_conflicted_on_a_required_parameter_finds_no_analogs_for_any_part(self):
        row = dataclasses.replace(composite_kp(), conflicted=frozenset({REQUIRED}))
        lines, contracts = two_parts_scene()

        parts = parts_of(reference(row, lines, contracts))

        assert [part.analogs for part in parts.values()] == [0, 0]

    def test_a_row_without_a_zone_finds_no_analogs(self):
        lines, contracts = two_parts_scene()

        parts = parts_of(reference(composite_kp(zone=None, zone_required=True), lines, contracts))

        assert [part.analogs for part in parts.values()] == [0, 0]

    def test_another_released_parameter_is_compared_by_form_not_by_value(self):
        row = dataclasses.replace(kp(values={RELEASED: BOTH, REQUIRED: 5, MARK: 31}), forms={RELEASED: BOTH})

        def candidate(pid, mark, form):
            made = line(pid, pid, "50", variant=300 + pid, values={RELEASED: PLATES, REQUIRED: 5, MARK: mark})
            return dataclasses.replace(made, forms={} if form is None else {MARK: form})

        lines = [candidate(1, 31, None), candidate(2, 32, None), candidate(3, 90, 90)]

        part = parts_of(reference(row, lines, contracts_of(1, 2, 3), families={FAMILY: three_info()}))[PLATES]

        assert part.analogs == 2

    def test_a_row_without_a_composite_has_no_reference(self):
        lines, contracts = two_parts_scene()

        assert reference(simple_kp(), lines, contracts) == []

    def test_a_row_conflicted_on_the_composite_parameter_has_no_reference(self):
        row = dataclasses.replace(composite_kp(), conflicted=frozenset({RELEASED}))
        lines, contracts = two_parts_scene()

        assert reference(row, lines, contracts) == []

    def test_a_family_without_a_current_schema_has_no_reference(self):
        lines, contracts = two_parts_scene()

        assert reference(composite_kp(), lines, contracts, families={}) == []

    def test_a_composite_non_released_parameter_gets_its_own_reference_by_value_id(self):
        row = dataclasses.replace(kp(values={RELEASED: 1, REQUIRED: 80}), forms={REQUIRED: 80})
        candidates = [
            line(1, 1, "50", variant=301, values={RELEASED: 1, REQUIRED: 81}),
            line(2, 2, "60", variant=302, values={RELEASED: 1, REQUIRED: 82}),
            line(3, 3, "70", variant=303, values={RELEASED: 2, REQUIRED: 82}),
        ]
        composite = CompositeValue("any", ((81, "класс А"), (82, "класс Б")))

        result = reference(row, candidates, contracts_of(1, 2, 3), composites={REQUIRED: composite})

        assert [(p["parameter_id"], p["parameter_name"], p["kind"]) for p in result] == [(REQUIRED, "Класс", "any")]
        by_part = {part.value_id: part for part in result[0]["parts"]}
        # параметр 10 отпускаемый: у него значения 1 и 2 — одна и та же простая форма
        assert (by_part[81].analogs, by_part[81].min) == (1, D("50"))
        assert (by_part[82].analogs, by_part[82].min) == (2, D("60"))


# ---------------------------------------------------------------------------
#  Цель «с этим подрядчиком» (спека §2.9, К8)
# ---------------------------------------------------------------------------

CONTRACTOR = 99


def contractor_run(rows, *, work=None, target=YearMonth(2026, 8)):
    lines = [r[0] for r in rows]
    contracts = [r[1] for r in rows]
    return run(work or kp(), lines, contracts, target=target, contractor=CONTRACTOR)


def row(pid, price, *, contractor, period, cls=CLASS_A, **line_kwargs):
    return line(pid, pid, price, **line_kwargs), contract(pid, cls=cls, contractor=contractor, period=period)


class TestContractorFigure:
    def test_it_is_the_freshest_among_the_remainder_analogs_of_the_contractor(self):
        rows = [
            row(1, "100", contractor=50, period=YearMonth(2026, 8)),
            row(2, "120", contractor=CONTRACTOR, period=YearMonth(2026, 2)),
            row(3, "130", contractor=CONTRACTOR, period=YearMonth(2026, 4)),
            row(4, "140", contractor=CONTRACTOR, period=YearMonth(2025, 1)),
        ]

        ladder = contractor_run(rows)

        assert ladder.freshest.analog_id == 1
        assert ladder.contractor == Figure(D("130"), 3, 3)

    def test_the_place_match_breaks_a_tie_of_dates_like_the_freshest(self):
        rows = [
            row(1, "100", contractor=CONTRACTOR, period=YearMonth(2026, 4), titles=("Раздел Б",)),
            row(2, "130", contractor=CONTRACTOR, period=YearMonth(2026, 4), titles=("Раздел А",)),
        ]

        ladder = contractor_run(rows, work=kp(titles=("Раздел А",)))

        assert ladder.contractor.analog_id == 2

    def test_no_analog_of_the_contractor_means_no_figure(self):
        rows = [row(1, "100", contractor=50, period=YearMonth(2026, 8))]

        assert contractor_run(rows).contractor is None

    def test_an_outlier_of_the_contractor_is_not_a_figure(self):
        rows = [
            row(1, "100", contractor=50, period=YearMonth(2026, 8)),
            row(2, "105", contractor=50, period=YearMonth(2026, 7)),
            row(3, "110", contractor=50, period=YearMonth(2026, 6)),
            row(4, "900", contractor=CONTRACTOR, period=YearMonth(2026, 6)),
        ]

        ladder = contractor_run(rows)

        assert 4 not in ladder.remainder_ids
        assert ladder.contractor is None

    def test_an_undated_analog_of_the_contractor_is_not_a_figure(self):
        rows = [
            row(1, "100", contractor=50, period=YearMonth(2026, 8)),
            row(2, "110", contractor=CONTRACTOR, period=None),
        ]

        assert contractor_run(rows).contractor is None

    def test_an_analog_of_the_contractor_outside_the_chosen_rung_is_not_a_figure(self):
        rows = [
            row(1, "100", contractor=50, period=YearMonth(2026, 8)),
            row(2, "110", contractor=CONTRACTOR, period=YearMonth(2026, 8), cls=CLASS_B),
        ]

        ladder = contractor_run(rows)

        assert ladder.rung_no == 1
        assert ladder.contractor is None

    def test_a_row_without_a_ladder_has_no_figure(self):
        rows = [row(1, "100", contractor=CONTRACTOR, period=YearMonth(2026, 8), zone="aboveground")]

        ladder = contractor_run(rows, work=kp(zone_required=True))

        assert (ladder.rung_no, ladder.contractor) == (None, None)

    def test_the_figure_is_a_value_the_lowest_and_the_freshest_do_not_move(self):
        rows = [
            row(1, "100", contractor=50, period=YearMonth(2026, 8)),
            row(2, "130", contractor=CONTRACTOR, period=YearMonth(2026, 4)),
        ]

        ladder = contractor_run(rows)

        assert (ladder.lowest.analog_id, ladder.freshest.analog_id) == (1, 1)


def with_figure(figure: Figure | None, **kwargs):
    lad = dataclasses.replace(comment_ladder(**kwargs), contractor=figure)
    return comparable(ladder=lad)


class TestResolveContractorTarget:
    def test_it_acts_while_the_ladder_has_the_figure(self):
        res = with_figure(Figure(D("120"), 102, 2))

        assert resolve_target(("contractor", None), res, is_system=False) == TargetChoice(
            "contractor", None, True, None
        )

    def test_it_resets_with_no_contractor_when_the_figure_left(self):
        res = with_figure(None)

        assert resolve_target(("contractor", None), res, is_system=False) == TargetChoice(
            "contractor", None, False, "no_contractor"
        )

    def test_the_figure_of_the_contractor_does_not_save_a_freshest_choice(self):
        res = comparable(
            freshest=None,
            ladder=dataclasses.replace(comment_ladder(freshest=None), contractor=Figure(D("120"), 102, 2)),
        )

        assert resolve_target(("freshest", None), res, is_system=False).reset_reason == "no_freshest"

    def test_the_missing_freshest_does_not_reset_a_contractor_choice(self):
        res = comparable(
            freshest=None,
            ladder=dataclasses.replace(comment_ladder(freshest=None), contractor=Figure(D("120"), 102, 2)),
        )

        assert resolve_target(("contractor", None), res, is_system=False).active is True

    def test_a_system_and_a_not_comparable_work_go_before_the_figure(self):
        res = with_figure(None)
        not_comparable = dataclasses.replace(res, state=LineState.no_history, ladder=None)

        assert resolve_target(("contractor", None), res, is_system=True).reset_reason == "kind_changed"
        assert resolve_target(("contractor", None), not_comparable, is_system=False).reset_reason == "not_comparable"

    def test_the_target_analog_of_an_acting_choice_is_the_figure_analog(self):
        res = with_figure(Figure(D("120"), 102, 2))
        choice = resolve_target(("contractor", None), res, is_system=False)

        assert target_analog(res, choice) == MID

    def test_a_reset_contractor_choice_leaves_the_lowest(self):
        res = with_figure(None)
        choice = resolve_target(("contractor", None), res, is_system=False)

        assert target_analog(res, choice) == LOW


# ---------------------------------------------------------------------------
#  Справка «по частям» по областям подбора (спека «зона как предпочтение» §1 п. 6)
# ---------------------------------------------------------------------------

ABOVE = "aboveground"
STRICT_FAMILY = frozenset({FAMILY})


class TestPartsReferenceScopes:
    def test_a_part_with_own_candidates_takes_only_them(self):
        lines = [
            hline(1, 1, "50", PLATES, variant=301),
            hline(2, 2, "10", PLATES, variant=302, zone=ABOVE),
            hline(3, 3, "10", PLATES, variant=303, zone=None),
        ]

        part = parts_of(reference(composite_kp(), lines, contracts_of(1, 2, 3)))[PLATES]

        assert (part.zone_scope, part.analogs, part.min, part.max) == ("own", 1, D("50"), D("50"))

    def test_a_part_without_own_candidates_takes_other_zones_and_zoneless_lines(self):
        lines = [
            hline(1, 1, "50", PLATES, variant=301, zone=ABOVE),
            hline(2, 2, "60", PLATES, variant=302, zone=None),
        ]

        part = parts_of(reference(composite_kp(), lines, contracts_of(1, 2)))[PLATES]

        assert (part.zone_scope, part.analogs, part.min, part.max) == ("other", 2, D("50"), D("60"))

    def test_each_part_chooses_its_scope_on_its_own(self):
        lines = [
            hline(1, 1, "50", PLATES, variant=301),
            hline(2, 2, "70", BEAMS, variant=302, zone=ABOVE),
        ]

        parts = parts_of(reference(composite_kp(), lines, contracts_of(1, 2)))

        assert (parts[PLATES].zone_scope, parts[BEAMS].zone_scope) == ("own", "other")

    def test_a_row_without_a_zone_takes_any(self):
        lines = [
            hline(1, 1, "50", PLATES, variant=301),
            hline(2, 2, "60", PLATES, variant=302, zone=ABOVE),
            hline(3, 3, "70", PLATES, variant=303, zone=None),
        ]

        part = parts_of(reference(composite_kp(zone=None), lines, contracts_of(1, 2, 3)))[PLATES]

        assert (part.zone_scope, part.analogs) == ("any", 3)

    def test_a_flagged_family_takes_only_own_and_a_part_without_them_has_no_scope(self):
        lines = [
            hline(1, 1, "50", PLATES, variant=301),
            hline(2, 2, "60", BEAMS, variant=302, zone=ABOVE),
            hline(3, 3, "70", BEAMS, variant=303, zone=None),
        ]

        parts = parts_of(reference(composite_kp(), lines, contracts_of(1, 2, 3), zone_required=STRICT_FAMILY))

        assert (parts[PLATES].zone_scope, parts[PLATES].analogs) == ("own", 1)
        assert (parts[BEAMS].zone_scope, parts[BEAMS].analogs) == (None, 0)

    def test_a_part_without_candidates_anywhere_has_no_scope(self):
        parts = parts_of(reference(composite_kp(), [hline(1, 1, "50", PLATES, variant=301)], contracts_of(1)))

        assert parts[BEAMS].zone_scope is None


class TestPartsReferenceLump:
    def test_a_lump_line_is_not_counted_in_a_part(self):
        lines, contracts = two_parts_scene()
        lines = [dataclasses.replace(ln, lump=True) if ln.position_item_id == 2 else ln for ln in lines]

        (parameter,) = reference(composite_kp(), lines, contracts)
        plates = {part.value_id: part for part in parameter["parts"]}[PLATES]

        assert (plates.analogs, plates.contracts) == (4, 4)  # строка 2 (37) выпала, договор 1 остался со строкой 1
        assert (plates.min, plates.max) == (D("36"), D("42"))
        assert plates.lowest_analog_id == 1  # соседняя ставка 36 того же договора осталась

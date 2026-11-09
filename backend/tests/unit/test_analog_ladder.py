"""Чистое ядро лестницы аналогов работ (спека лестницы аналогов §2.8, §2.9, §2.11).

Входы собираются фабриками ниже; база не нужна. Условные имена: семья 1, варианты
W6 и W8, параметр 10 («Марка», отпускаемый) и параметр 11 («Класс», обязательный).
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from money.inflation import YearMonth
from services.analog_ladder import (
    FamilyLadderInfo,
    KpWork,
    WorkHistoryLine,
    WorkLadder,
    evaluate_work_ladder,
)
from services.price_basis import HistoryContract

CLASS_A = 1
CLASS_B = 2
FAMILY = 1
W6 = 100
W8 = 101
SCHEMA = 7
OLD_SCHEMA = 6
RELEASED = 10
REQUIRED = 11
ZONE = "underground"
D = Decimal

#: Значения параметров варианта, отличного от варианта КП, но с теми же обязательными.
OTHER_VARIANT = {RELEASED: 2, REQUIRED: 5}


def kp(
    *,
    variant: int = W6,
    schema: int | None = SCHEMA,
    values: dict[int, int | None] | None = None,
    zone: str | None = ZONE,
    titles: tuple[str, ...] = (),
    net: str = "100",
    conflicted: frozenset[int] = frozenset(),
) -> KpWork:
    return KpWork(
        position_item_id=9000,
        family_id=FAMILY,
        variant_id=variant,
        schema_id=schema,
        main_values={RELEASED: 1, REQUIRED: 5} if values is None else values,
        zone=zone,  # type: ignore[arg-type]
        chain_titles=frozenset(titles),
        net=D(net),
        conflicted=conflicted,
    )


def info(*, releasable: tuple[int, ...] = (RELEASED,)) -> FamilyLadderInfo:
    return FamilyLadderInfo(
        schema_id=SCHEMA,
        main_parameter_ids=(RELEASED, REQUIRED),
        releasable_ids=frozenset(releasable),
        names={RELEASED: "Марка", REQUIRED: "Класс"},
    )


def line(
    pid: int,
    contract_id: int,
    price: str,
    *,
    variant: int = W6,
    schema: int | None = SCHEMA,
    values: dict[int, int | None] | None = None,
    zone: str | None = ZONE,
    titles: tuple[str, ...] = (),
    vat: str | None = "0",
    weight: str | None = "1",
    family: int = FAMILY,
    conflicted: frozenset[int] = frozenset(),
) -> WorkHistoryLine:
    return WorkHistoryLine(
        position_item_id=pid,
        contract_id=contract_id,
        family_id=family,
        chain_id=1,
        zone=zone,  # type: ignore[arg-type]
        chain_titles=frozenset(titles),
        variant_id=variant,
        schema_id=schema,
        main_values={RELEASED: 1, REQUIRED: 5} if values is None else values,
        unit_price=D(price),
        weight=None if weight is None else D(weight),
        vat_base=None if vat is None else D(vat),
        conflicted=conflicted,
    )


def contract(
    cid: int,
    *,
    cls: int = CLASS_A,
    contractor: int = 50,
    period: YearMonth | None = YearMonth(2026, 1),
) -> HistoryContract:
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 10,
        number=f"N{cid}",
        object_title="Объект",
        year=2026,
        rate_class_id=cls,
        rate_class_title="Класс",
        contractor_id=contractor,
        contractor_title="Подрядчик",
        area=None,
        period=period,
    )


def run(
    work: KpWork,
    lines: list[WorkHistoryLine],
    contracts: list[HistoryContract],
    *,
    families: dict[int, FamilyLadderInfo] | None = None,
    target: YearMonth | None = YearMonth(2026, 1),
    factors: dict[int, Decimal] | None = None,
    contractor: int = 99,
) -> WorkLadder:
    return evaluate_work_ladder(
        work,
        lines,
        {FAMILY: info()} if families is None else families,
        {c.contract_id: c for c in contracts},
        tender_class_id=CLASS_A,
        tender_contractor_id=contractor,
        target_month=target,
        factors=factors,
    )


def ids(ladder: WorkLadder) -> list[int]:
    return [a.position_item_id for a in ladder.analogs]


def by_nets(nets: list[str], work_net: str = "100") -> WorkLadder:
    lines = [line(i + 1, i + 1, n) for i, n in enumerate(nets)]
    return run(kp(net=work_net), lines, [contract(i + 1) for i in range(len(nets))])


# ---------------------------------------------------------------------------
#  Кандидат: пять условий §2.8, каждое по отдельности выключает строку
# ---------------------------------------------------------------------------


def test_candidate_baseline_is_an_analog() -> None:
    ladder = run(kp(), [line(1, 1, "100")], [contract(1)])
    assert ladder.rung_no == 1
    assert ids(ladder) == [1]


@pytest.mark.parametrize(
    ("bad_line", "known_contracts", "reason"),
    [
        pytest.param(line(1, 1, "100"), [], "no_family", id="1-contract-not-in-history"),
        pytest.param(line(1, 1, "0"), [1], "no_family", id="3-zero-price"),
        pytest.param(line(1, 1, "NaN"), [1], "no_family", id="3-nan-price"),
        pytest.param(line(1, 1, "100", weight="0"), [1], "no_family", id="3-zero-weight"),
        pytest.param(line(1, 1, "100", weight=None), [1], "no_family", id="3-no-weight"),
        pytest.param(line(1, 1, "0.99"), [1], "no_family", id="4-below-one-ruble"),
        pytest.param(line(1, 1, "100", zone=None), [1], "no_family", id="5-zone-undefined"),
        pytest.param(line(1, 1, "100", zone="aboveground"), [1], "no_zone_match", id="zone-differs"),
        pytest.param(line(1, 1, "100", vat=None), [1], "no_family", id="vat-base-unknown"),
        pytest.param(line(1, 1, "100", family=2), [1], "no_family", id="other-family"),
    ],
)
def test_candidate_each_condition_alone_excludes_the_line(
    bad_line: WorkHistoryLine, known_contracts: list[int], reason: str
) -> None:
    """Причина «нет истории» отличает «не кандидат» от «кандидат, но не та зона»:
    строка с неопределённой зоной у КП с зоной не попала бы в ступень и будучи
    кандидатом, поэтому одного `rung_no is None` для условия 5 мало."""
    ladder = run(kp(), [bad_line], [contract(c) for c in known_contracts])
    assert ladder.rung_no is None
    assert ladder.analogs == ()
    assert ladder.no_history_reason == reason


def test_k4_only_symbolic_analog_on_rung_one_normal_on_rung_two() -> None:
    """К4: символический аналог ступени 1 не считается; выбрана ступень 2."""
    lines = [
        line(1, 1, "0.5"),
        line(2, 2, "80", variant=W8, values=OTHER_VARIANT),
    ]
    ladder = run(kp(), lines, [contract(1), contract(2)])
    assert ladder.rung_no == 2
    assert ids(ladder) == [2]


def test_k4_price_of_exactly_one_ruble_is_an_analog() -> None:
    assert ids(run(kp(), [line(1, 1, "1")], [contract(1)])) == [1]


def test_k4_threshold_is_by_nominal_before_rung_choice() -> None:
    """Номинал 0,9 с множителем 2 даёт 1,8, но аналогом не является."""
    ladder = run(kp(), [line(1, 1, "0.9")], [contract(1)], factors={10: D(2)})
    assert ladder.rung_no is None


def test_threshold_applies_to_net_not_gross() -> None:
    assert ids(run(kp(), [line(1, 1, "1.19", vat="19")], [contract(1)])) == [1]
    assert run(kp(), [line(1, 1, "1.18", vat="19")], [contract(1)]).rung_no is None


def test_index_factor_multiplies_net_and_keeps_nominal() -> None:
    ladder = run(kp(), [line(1, 1, "100")], [contract(1)], factors={10: D("1.5")})
    analog = ladder.analogs[0]
    assert (analog.nominal, analog.net) == (D(100), D(150))
    assert ladder.lowest is not None and ladder.lowest.value == D(150)


def test_vat_is_removed_from_analog_price() -> None:
    ladder = run(kp(), [line(1, 1, "120", vat="20")], [contract(1)])
    assert ladder.analogs[0].net == D(100)


# ---------------------------------------------------------------------------
#  Ступени
# ---------------------------------------------------------------------------


def test_rung_one_is_variant_in_tender_class_and_zone() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    ladder = run(kp(), lines, [contract(1), contract(2, cls=CLASS_B)])
    assert (ladder.rung_no, ids(ladder), ladder.other_class, ladder.released) == (1, [1], False, ())


def test_rung_two_when_variant_missing_and_required_values_match() -> None:
    ladder = run(kp(), [line(1, 1, "80", variant=W8, values=OTHER_VARIANT)], [contract(1)])
    assert (ladder.rung_no, ladder.other_class, ladder.released) == (2, False, ("Марка",))


def test_rung_two_requires_required_values_to_match() -> None:
    lines = [line(1, 1, "80", variant=W8, values={RELEASED: 1, REQUIRED: 6})]
    assert run(kp(), lines, [contract(1)]).rung_no is None


def test_unspecified_matches_only_unspecified() -> None:
    work = kp(values={RELEASED: 1, REQUIRED: None})
    none_line = line(1, 1, "80", variant=W8, values={RELEASED: 1, REQUIRED: None})
    value_line = line(2, 2, "70", variant=W8, values={RELEASED: 1, REQUIRED: 5})
    ladder = run(work, [none_line, value_line], [contract(1), contract(2)])
    assert (ladder.rung_no, ids(ladder)) == (2, [1])
    assert run(kp(), [none_line], [contract(1)]).rung_no is None


def test_released_parameter_value_does_not_matter_on_rung_two() -> None:
    lines = [
        line(1, 1, "80", variant=W8, values={RELEASED: 2, REQUIRED: 5}),
        line(2, 2, "90", variant=W8, values={RELEASED: None, REQUIRED: 5}),
    ]
    assert ids(run(kp(), lines, [contract(1), contract(2)])) == [1, 2]


def test_rung_three_is_variant_of_other_class() -> None:
    ladder = run(kp(), [line(1, 1, "100")], [contract(1, cls=CLASS_B)])
    assert (ladder.rung_no, ladder.other_class, ladder.released) == (3, True, ())


def test_rung_four_is_family_of_other_class() -> None:
    lines = [line(1, 1, "80", variant=W8, values=OTHER_VARIANT)]
    ladder = run(kp(), lines, [contract(1, cls=CLASS_B)])
    assert (ladder.rung_no, ladder.other_class, ladder.released) == (4, True, ("Марка",))


def test_released_names_follow_parameter_order() -> None:
    both = FamilyLadderInfo(
        schema_id=SCHEMA,
        main_parameter_ids=(RELEASED, REQUIRED),
        releasable_ids=frozenset({REQUIRED, RELEASED}),
        names={RELEASED: "Марка", REQUIRED: "Класс"},
    )
    lines = [line(1, 1, "80", variant=W8, values={RELEASED: 7, REQUIRED: 8})]
    ladder = run(kp(), lines, [contract(1)], families={FAMILY: both})
    assert (ladder.rung_no, ladder.released) == (2, ("Марка", "Класс"))


def test_first_non_empty_rung_wins_even_with_cheaper_higher_rungs() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "10", variant=W8, values=OTHER_VARIANT)]
    ladder = run(kp(), lines, [contract(1), contract(2)])
    assert (ladder.rung_no, ids(ladder)) == (1, [1])


def test_rung_two_beats_rung_three() -> None:
    lines = [
        line(1, 1, "100", variant=W8, values=OTHER_VARIANT),
        line(2, 2, "10"),
    ]
    ladder = run(kp(), lines, [contract(1), contract(2, cls=CLASS_B)])
    assert (ladder.rung_no, ids(ladder)) == (2, [1])


def test_k10_exact_variant_of_other_class_beats_release_in_other_class() -> None:
    """К10: в своём классе аналогов нет; в другом W6 за 100 и W8 за 70; W отпускается.

    Ступень 3 раньше ступени 4: самая низкая 100, W8 в справке нет.
    """
    lines = [line(1, 1, "100"), line(2, 2, "70", variant=W8, values=OTHER_VARIANT)]
    contracts = [contract(1, cls=CLASS_B), contract(2, cls=CLASS_B)]
    ladder = run(kp(), lines, contracts)
    assert ladder.rung_no == 3
    assert ladder.lowest is not None and ladder.lowest.value == D(100)
    assert ids(ladder) == [1]


def test_k10_same_input_with_required_parameter_stays_on_rung_three() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "70", variant=W8, values=OTHER_VARIANT)]
    contracts = [contract(1, cls=CLASS_B), contract(2, cls=CLASS_B)]
    ladder = run(kp(), lines, contracts, families={FAMILY: info(releasable=())})
    assert ladder.rung_no == 3
    assert ladder.lowest is not None and ladder.lowest.value == D(100)


def test_rungs_two_and_four_absent_when_nothing_is_releasable() -> None:
    lines = [line(1, 1, "80", variant=W8, values={RELEASED: 1, REQUIRED: 5})]
    assert run(kp(), lines, [contract(1)], families={FAMILY: info(releasable=())}).rung_no is None


def test_rungs_two_and_four_absent_for_variant_on_old_schema() -> None:
    # Значения у КП совпадают с кандидатом нарочно: отказ держит сверка версии,
    # а не несовпадение значений (загрузчик значений старой версии не отдаёт).
    work = kp(schema=OLD_SCHEMA, values={RELEASED: 1, REQUIRED: 5})
    lines = [line(1, 1, "80", variant=W8, values=OTHER_VARIANT)]
    assert run(work, lines, [contract(1)]).rung_no is None
    assert run(work, lines, [contract(1, cls=CLASS_B)]).rung_no is None
    assert run(kp(values={RELEASED: 1, REQUIRED: 5}), lines, [contract(1)]).rung_no == 2
    # ступени 1 и 3 у варианта на старой версии остаются
    assert run(work, [line(2, 2, "80", variant=W6)], [contract(2)]).rung_no == 1
    assert run(work, [line(2, 2, "80", variant=W6)], [contract(2, cls=CLASS_B)]).rung_no == 3


def test_rungs_two_and_four_candidates_must_be_on_current_schema() -> None:
    # Те же обязательные значения, другая версия: id значений разных версий несопоставимы.
    lines = [line(1, 1, "80", variant=W8, schema=OLD_SCHEMA, values=OTHER_VARIANT)]
    assert run(kp(), lines, [contract(1)]).rung_no is None
    assert run(kp(), lines, [contract(1, cls=CLASS_B)]).rung_no is None
    current = [line(1, 1, "80", variant=W8, values=OTHER_VARIANT)]
    assert run(kp(), current, [contract(1)]).rung_no == 2


def test_rungs_two_and_four_absent_without_family_info() -> None:
    assert run(kp(), [line(1, 1, "80", variant=W8)], [contract(1)], families={}).rung_no is None


def test_kp_zone_is_echoed_and_undefined_zone_matches_nothing() -> None:
    assert run(kp(), [line(1, 1, "100")], [contract(1)]).zone == "underground"
    ladder = run(kp(zone=None), [line(1, 1, "100", zone=None)], [contract(1)])
    assert (ladder.zone, ladder.rung_no) == (None, None)


# ---------------------------------------------------------------------------
#  Выбросы и состояния
# ---------------------------------------------------------------------------


def test_k5_one_hundred_ten_thousand_are_all_outliers() -> None:
    ladder = by_nets(["1", "100", "10000"])
    assert ladder.all_outliers
    assert ladder.rung_no == 1
    assert ladder.remainder_ids == frozenset()
    assert ladder.lowest is None and ladder.freshest is None and ladder.kp_ratio is None
    assert len(ladder.analogs) == 3
    assert all(a.outlier_ratio is not None for a in ladder.analogs)


def test_k5_one_one_hundred_hundred_are_all_outliers_in_one_pass() -> None:
    """Итеративное исключение дало бы остаток `100, 100` или `1, 1` — здесь его нет."""
    ladder = by_nets(["1", "1", "100", "100"])
    assert ladder.all_outliers
    assert ladder.remainder_ids == frozenset()
    assert ladder.rung_no == 1


def test_k5_all_outliers_does_not_descend_to_next_rung() -> None:
    lines = [
        line(1, 1, "1"),
        line(2, 2, "1000"),
        line(3, 3, "100", variant=W8, values=OTHER_VARIANT),
    ]
    ladder = run(kp(), lines, [contract(1), contract(2), contract(3)])
    assert ladder.rung_no == 1
    assert ladder.all_outliers
    assert sorted(ids(ladder)) == [1, 2]


def test_k5_two_diverging_analogs_are_both_outliers() -> None:
    assert by_nets(["10", "100"]).all_outliers


def test_k5_outlier_ratio_is_against_median_of_others() -> None:
    ladder = by_nets(["10", "100"])
    ratios = {a.position_item_id: a.outlier_ratio for a in ladder.analogs}
    assert ratios == {1: D("0.1"), 2: D(10)}


def test_k5_boundary_five_and_one_fifth_are_inside() -> None:
    ladder = by_nets(["100", "500"])
    assert not ladder.all_outliers
    assert ladder.remainder_ids == frozenset({1, 2})
    assert all(a.outlier_ratio is None for a in ladder.analogs)
    assert not by_nets(["100", "20"]).all_outliers
    assert by_nets(["100", "501"]).all_outliers
    assert by_nets(["100", "19.9"]).all_outliers


def test_single_analog_is_never_an_outlier() -> None:
    ladder = by_nets(["1"], work_net="100")
    assert not ladder.all_outliers
    assert ladder.remainder_ids == frozenset({1})


def test_outlier_among_analogs_does_not_flip_the_line() -> None:
    ladder = by_nets(["100", "100", "100", "1000"], work_net="100")
    assert not ladder.all_outliers
    assert ladder.kp_ratio is None
    assert ladder.remainder_ids == frozenset({1, 2, 3})
    outlier = next(a for a in ladder.analogs if a.position_item_id == 4)
    assert outlier.outlier_ratio == D(10)
    assert ladder.lowest is not None and ladder.lowest.analog_id in {1, 2, 3}


def test_outlier_set_is_not_recomputed_after_exclusion() -> None:
    # 600 к медиане остальных (10) = 60; 60 к медиане остальных (10, 10, 10, 600) = 6
    ladder = by_nets(["10", "10", "10", "60", "600"])
    assert ladder.remainder_ids == frozenset({1, 2, 3})


def test_kp_in_different_units_is_judged_by_remainder_median() -> None:
    ladder = by_nets(["100", "110", "90"], work_net="1000")
    assert ladder.kp_ratio == D(10)
    assert ladder.lowest is not None  # цифры есть
    assert by_nets(["100", "110", "90"], work_net="10").kp_ratio == D("0.1")


def test_kp_ratio_boundary_is_inside() -> None:
    assert by_nets(["100", "100"], work_net="500").kp_ratio is None
    assert by_nets(["100", "100"], work_net="20").kp_ratio is None
    assert by_nets(["100", "100"], work_net="500.01").kp_ratio is not None
    assert by_nets(["100", "100"], work_net="19.99").kp_ratio is not None


def test_kp_ratio_uses_remainder_median_not_median_of_all() -> None:
    # остаток 100, 100, 100 (400 и 400 — выбросы к медиане остальных 100); медиана всех 100
    ladder = by_nets(["100", "100", "100", "10000"], work_net="600")
    assert ladder.kp_ratio == D(6)
    # остаток 100, 100, 120 (медиана 100); медиана всех, с выбросом, 110
    ladder = by_nets(["100", "120", "100", "99999"], work_net="660")
    assert ladder.remainder_ids == frozenset({1, 2, 3})
    assert ladder.kp_ratio == D("6.6")


# ---------------------------------------------------------------------------
#  Самая низкая, самая свежая
# ---------------------------------------------------------------------------


def test_lowest_is_minimum_net_of_remainder() -> None:
    ladder = by_nets(["120", "100", "110"])
    assert ladder.lowest is not None
    assert (ladder.lowest.value, ladder.lowest.analog_id, ladder.lowest.contract_id) == (D(100), 2, 2)


def test_lowest_tie_prefers_contract_with_tender_contractor() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, contractor=10), contract(2, contractor=77)]
    ladder = run(kp(), lines, contracts, contractor=77)
    assert ladder.lowest is not None and ladder.lowest.analog_id == 2


def test_lowest_tie_then_date_distance_then_id() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "100")]
    contracts = [
        contract(1, period=YearMonth(2025, 1)),
        contract(2, period=YearMonth(2026, 3)),
        contract(3, period=YearMonth(2026, 3)),
    ]
    ladder = run(kp(), lines, contracts)
    assert ladder.lowest is not None and ladder.lowest.analog_id == 2
    ladder = run(kp(), [line(5, 1, "100"), line(4, 2, "100")], [contract(1), contract(2)])
    assert ladder.lowest is not None and ladder.lowest.analog_id == 4


def test_lowest_tie_contractor_beats_date_distance() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [
        contract(1, period=YearMonth(2026, 1), contractor=10),
        contract(2, period=YearMonth(2020, 1), contractor=77),
    ]
    ladder = run(kp(), lines, contracts, contractor=77)
    assert ladder.lowest is not None and ladder.lowest.analog_id == 2


def test_lowest_tie_undated_goes_after_dated() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, period=None), contract(2, period=YearMonth(2020, 1))]
    assert run(kp(), lines, contracts).lowest.analog_id == 2  # type: ignore[union-attr]


def test_lowest_tie_without_target_prefers_later_period() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, period=YearMonth(2024, 1)), contract(2, period=YearMonth(2025, 1))]
    assert run(kp(), lines, contracts, target=None).lowest.analog_id == 2  # type: ignore[union-attr]


def test_lowest_tie_with_target_is_by_distance_not_by_lateness() -> None:
    # Цель 2026-01: 2025-12 ближе (1 мес.), чем более поздний 2026-06 (5 мес.).
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, period=YearMonth(2026, 6)), contract(2, period=YearMonth(2025, 12))]
    assert run(kp(), lines, contracts).lowest.analog_id == 2  # type: ignore[union-attr]


def test_freshest_is_nearest_month_to_target() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "100")]
    contracts = [
        contract(1, period=YearMonth(2025, 6)),
        contract(2, period=YearMonth(2026, 2)),
        contract(3, period=YearMonth(2026, 9)),
    ]
    ladder = run(kp(), lines, contracts, target=YearMonth(2026, 1))
    assert ladder.freshest is not None and ladder.freshest.analog_id == 2
    ladder = run(kp(), lines, contracts, target=YearMonth(2026, 10))
    assert ladder.freshest is not None and ladder.freshest.analog_id == 3


def test_freshest_figure_carries_net_and_ids() -> None:
    lines = [line(1, 1, "120"), line(2, 2, "100")]
    contracts = [contract(1, period=YearMonth(2026, 1)), contract(2, period=YearMonth(2025, 1))]
    ladder = run(kp(), lines, contracts)
    assert ladder.freshest is not None
    assert (ladder.freshest.value, ladder.freshest.analog_id, ladder.freshest.contract_id) == (D(120), 1, 1)


def test_freshest_tie_by_place_then_price_then_id() -> None:
    work = kp(titles=("a", "b"))
    contracts = [contract(1), contract(2), contract(3)]
    lines = [line(1, 1, "100", titles=("a",)), line(2, 2, "100", titles=("a", "b")), line(3, 3, "100")]
    assert run(work, lines, contracts).freshest.analog_id == 2  # type: ignore[union-attr]
    lines = [line(1, 1, "110"), line(2, 2, "100"), line(3, 3, "100")]
    assert run(work, lines, contracts).freshest.analog_id == 2  # type: ignore[union-attr]
    lines = [line(3, 1, "100"), line(2, 2, "100")]
    assert run(work, lines, [contract(1), contract(2)]).freshest.analog_id == 2  # type: ignore[union-attr]


def test_freshest_distance_beats_place() -> None:
    work = kp(titles=("a",))
    lines = [line(1, 1, "100", titles=("a",)), line(2, 2, "100")]
    contracts = [contract(1, period=YearMonth(2025, 1)), contract(2, period=YearMonth(2026, 1))]
    assert run(work, lines, contracts).freshest.analog_id == 2  # type: ignore[union-attr]


def test_freshest_without_target_month_is_latest() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "100")]
    contracts = [
        contract(1, period=YearMonth(2025, 6)),
        contract(2, period=YearMonth(2027, 1)),
        contract(3, period=YearMonth(2026, 9)),
    ]
    ladder = run(kp(), lines, contracts, target=None)
    assert ladder.freshest is not None and ladder.freshest.analog_id == 2


def test_freshest_without_target_month_tie_uses_place() -> None:
    work = kp(titles=("a",))
    lines = [line(1, 1, "100"), line(2, 2, "100", titles=("a",))]
    ladder = run(work, lines, [contract(1), contract(2)], target=None)
    assert ladder.freshest is not None and ladder.freshest.analog_id == 2


def test_analog_without_period_is_not_freshest_but_can_be_lowest() -> None:
    lines = [line(1, 1, "50"), line(2, 2, "100")]
    contracts = [contract(1, period=None), contract(2, period=YearMonth(2020, 1))]
    ladder = run(kp(), lines, contracts)
    assert ladder.lowest is not None and ladder.lowest.analog_id == 1
    assert ladder.freshest is not None and ladder.freshest.analog_id == 2


def test_remainder_without_any_period_has_no_freshest() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "110")]
    ladder = run(kp(), lines, [contract(1, period=None), contract(2, period=None)])
    assert ladder.freshest is None
    assert ladder.lowest is not None and not ladder.all_outliers


def test_freshest_ignores_outliers() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100"), line(3, 3, "100"), line(4, 4, "5000")]
    contracts = [
        contract(1, period=YearMonth(2025, 1)),
        contract(2, period=YearMonth(2025, 2)),
        contract(3, period=YearMonth(2024, 1)),
        contract(4, period=YearMonth(2026, 1)),
    ]
    ladder = run(kp(), lines, contracts)
    assert ladder.freshest is not None and ladder.freshest.analog_id == 2


def test_lowest_ignores_outliers() -> None:
    # 1 — выброс (к медиане остальных 100 = 0,01): самой низкой он не бывает
    ladder = by_nets(["1", "100", "100", "100"])
    assert ladder.lowest is not None and ladder.lowest.value == D(100)


# ---------------------------------------------------------------------------
#  Справка: порядок и близость места
# ---------------------------------------------------------------------------


def test_reference_order_place_then_date_then_price_then_id() -> None:
    work = kp(titles=("a", "b", "c"))
    lines = [
        line(1, 1, "100"),
        line(2, 2, "100", titles=("a",)),
        line(3, 3, "100", titles=("a", "b")),
        line(4, 4, "100", titles=("a",)),
        line(5, 5, "90", titles=("a",)),
        line(6, 6, "90", titles=("a",)),
    ]
    contracts = [
        contract(1),
        contract(2, period=YearMonth(2025, 1)),
        contract(3),
        contract(4, period=YearMonth(2026, 1)),
        contract(5, period=YearMonth(2026, 1)),
        contract(6, period=YearMonth(2026, 1)),
    ]
    ladder = run(work, lines, contracts)
    assert ids(ladder) == [3, 5, 6, 4, 2, 1]
    assert [a.place_match for a in ladder.analogs] == [2, 1, 1, 1, 1, 0]


def test_reference_date_order_is_distance_to_target_not_lateness() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, period=YearMonth(2026, 6)), contract(2, period=YearMonth(2025, 12))]
    assert ids(run(kp(), lines, contracts)) == [2, 1]
    # без цели — позднее раньше
    assert ids(run(kp(), lines, contracts, target=None)) == [1, 2]


def test_reference_undated_goes_after_dated_at_same_place() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100")]
    contracts = [contract(1, period=None), contract(2, period=YearMonth(2020, 1))]
    assert ids(run(kp(), lines, contracts)) == [2, 1]


def test_place_match_counts_shared_titles_as_sets() -> None:
    work = kp(titles=("a", "b"))
    ladder = run(work, [line(1, 1, "100", titles=("b", "z", "a"))], [contract(1)])
    assert ladder.analogs[0].place_match == 2


def test_reference_includes_outliers_and_marks_them() -> None:
    ladder = by_nets(["100", "100", "100", "5000"])
    marks = {a.position_item_id: a.outlier_ratio is not None for a in ladder.analogs}
    assert marks == {1: False, 2: False, 3: False, 4: True}
    assert ladder.remainder_ids == frozenset({1, 2, 3})


def test_reference_belongs_to_selected_rung_only() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100", variant=W8, values=OTHER_VARIANT)]
    assert ids(run(kp(), lines, [contract(1), contract(2)])) == [1]


def test_analog_carries_contract_period_and_id() -> None:
    ladder = run(kp(), [line(1, 1, "100")], [contract(1, period=YearMonth(2025, 3))])
    analog = ladder.analogs[0]
    assert (analog.period, analog.contract_id, analog.position_item_id) == (YearMonth(2025, 3), 1, 1)


def test_one_contract_gives_one_analog_per_line() -> None:
    ladder = run(kp(), [line(1, 1, "100"), line(2, 1, "110")], [contract(1)])
    assert sorted(ids(ladder)) == [1, 2]


# ---------------------------------------------------------------------------
#  Причины «нет истории»
# ---------------------------------------------------------------------------


def test_no_history_reason_no_family() -> None:
    ladder = run(kp(), [line(1, 1, "100", family=2)], [contract(1)])
    assert (ladder.rung_no, ladder.no_history_reason) == (None, "no_family")


def test_no_history_reason_no_family_when_only_symbolic_candidates() -> None:
    assert run(kp(), [line(1, 1, "0.5")], [contract(1)]).no_history_reason == "no_family"


def test_no_history_reason_no_params() -> None:
    lines = [line(1, 1, "100", variant=W8, values={RELEASED: 1, REQUIRED: 6})]
    assert run(kp(), lines, [contract(1)]).no_history_reason == "no_params"


def test_no_history_reason_no_params_without_releasable() -> None:
    lines = [line(1, 1, "100", variant=W8, values={RELEASED: 1, REQUIRED: 5})]
    ladder = run(kp(), lines, [contract(1)], families={FAMILY: info(releasable=())})
    assert ladder.no_history_reason == "no_params"


def test_no_history_reason_no_zone_match_by_variant() -> None:
    ladder = run(kp(), [line(1, 1, "100", zone="aboveground")], [contract(1)])
    assert ladder.no_history_reason == "no_zone_match"


def test_no_history_reason_no_zone_match_by_required_values() -> None:
    lines = [line(1, 1, "100", variant=W8, values=OTHER_VARIANT, zone="territory")]
    assert run(kp(), lines, [contract(1)]).no_history_reason == "no_zone_match"


def test_no_history_reason_counts_candidates_of_any_class() -> None:
    ladder = run(kp(), [line(1, 1, "100", zone="territory")], [contract(1, cls=CLASS_B)])
    assert ladder.no_history_reason == "no_zone_match"


def test_no_history_reason_is_none_when_rung_found() -> None:
    assert run(kp(), [line(1, 1, "100")], [contract(1)]).no_history_reason is None


def test_all_outliers_is_not_no_history() -> None:
    assert by_nets(["1", "100", "10000"]).no_history_reason is None


# ---------------------------------------------------------------------------
#  Выпавшие по НДС
# ---------------------------------------------------------------------------


def test_vat_dropped_contract_with_only_unknown_base_lines() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100", vat=None)]
    assert run(kp(), lines, [contract(1), contract(2)]).vat_dropped == (2,)


def test_vat_dropped_excludes_contract_with_a_valid_line() -> None:
    lines = [line(1, 1, "100"), line(2, 1, "100", vat=None)]
    assert run(kp(), lines, [contract(1)]).vat_dropped == ()


def test_vat_dropped_contract_whose_known_base_line_is_symbolic() -> None:
    # «Годная» строка — аналог ступени: символическая (< 1 ₽) аналогом не стала,
    # договор без аналогов на ступени с неизвестной базой — выпавший (толкование Task 4).
    lines = [line(1, 1, "100"), line(2, 2, "0.5"), line(3, 2, "100", vat=None)]
    assert run(kp(), lines, [contract(1), contract(2)]).vat_dropped == (2,)


def test_vat_dropped_is_by_key_of_selected_rung() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100", variant=W8, vat=None)]
    assert run(kp(), lines, [contract(1), contract(2)]).vat_dropped == ()


def test_vat_dropped_respects_class_of_rung() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100", vat=None)]
    assert run(kp(), lines, [contract(1), contract(2, cls=CLASS_B)]).vat_dropped == ()


def test_vat_dropped_respects_zone_of_rung() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "100", vat=None, zone="aboveground")]
    assert run(kp(), lines, [contract(1), contract(2)]).vat_dropped == ()


def test_vat_dropped_is_sorted_and_unique() -> None:
    lines = [
        line(1, 1, "100"),
        line(5, 5, "100", vat=None),
        line(4, 4, "100", vat=None),
        line(6, 4, "100", vat=None),
    ]
    contracts = [contract(1), contract(4), contract(5)]
    assert run(kp(), lines, contracts).vat_dropped == (4, 5)


def test_vat_dropped_on_rung_two_uses_family_key() -> None:
    lines = [
        line(1, 1, "100", variant=W8, values=OTHER_VARIANT),
        line(2, 2, "100", variant=W8, values=OTHER_VARIANT, vat=None),
    ]
    assert run(kp(), lines, [contract(1), contract(2)]).vat_dropped == (2,)


def test_vat_dropped_without_rung_names_the_only_unknown_base_contract() -> None:
    ladder = run(kp(), [line(1, 1, "100", vat=None)], [contract(1)])
    assert (ladder.rung_no, ladder.vat_dropped) == (None, (1,))


def test_vat_dropped_without_rung_uses_first_rung_where_found() -> None:
    # ступени 1-2 пусты; выпавший договор подходит только ступени 3 (другой класс)
    lines = [
        line(1, 1, "100", vat=None),
        line(2, 2, "100", vat=None, zone="aboveground"),  # не пройдёт ни одной ступени
    ]
    contracts = [contract(1, cls=CLASS_B), contract(2, cls=CLASS_B)]
    ladder = run(kp(), lines, contracts)
    assert (ladder.rung_no, ladder.vat_dropped) == (None, (1,))


def test_vat_dropped_without_rung_does_not_mix_rungs() -> None:
    # на ступени 1 выпал договор 1, на ступени 3 — договор 2: берётся первая ступень
    lines = [line(1, 1, "100", vat=None), line(2, 2, "100", vat=None)]
    contracts = [contract(1), contract(2, cls=CLASS_B)]
    assert run(kp(), lines, contracts).vat_dropped == (1,)


# ---------------------------------------------------------------------------
#  Деньги только Decimal
# ---------------------------------------------------------------------------


def _walk(value: object) -> list[object]:
    found: list[object] = [value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            found.extend(_walk(getattr(value, f.name)))
    elif isinstance(value, tuple | list | frozenset | set):
        for item in value:
            found.extend(_walk(item))
    return found


def test_results_hold_no_float() -> None:
    lines = [line(1, 1, "100"), line(2, 2, "110"), line(3, 3, "5000")]
    contracts = [contract(1), contract(2), contract(3)]
    ladder = run(kp(net="1000"), lines, contracts, factors={10: D("1.1"), 20: D("1.1"), 30: D("1.1")})
    values = _walk(ladder)
    assert not any(isinstance(v, float) for v in values)
    assert any(isinstance(v, Decimal) for v in values)


def test_module_source_has_no_float() -> None:
    import ast
    import pathlib

    import services.analog_ladder as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "float"
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float)


# ---------------------------------------------------------------------------
#  Перенос общего в price_basis без изменения поведения
# ---------------------------------------------------------------------------


def test_our_price_reexports_the_moved_names() -> None:
    import services.our_price as our_price
    import services.price_basis as basis

    assert our_price._median is basis.median
    assert our_price._outside is basis.outside
    assert our_price.HistoryContract is basis.HistoryContract
    assert our_price._CONTEXT is basis._CONTEXT
    assert our_price.MISMATCH_HIGH is basis.MISMATCH_HIGH
    assert our_price.MISMATCH_LOW is basis.MISMATCH_LOW


def test_history_contract_period_is_last_and_optional() -> None:
    last = dataclasses.fields(HistoryContract)[-1]
    assert (last.name, last.default) == ("period", None)


def test_median_and_outside_values() -> None:
    from services.price_basis import median, outside

    assert median([D(3), D(1), D(2)]) == D(2)
    assert median([D(1), D(2)]) == D("1.5")
    assert not outside(D(5)) and not outside(D("0.2"))
    assert outside(D("5.01")) and outside(D("0.19"))


# ---------------------------------------------------------------------------
#  Конфликт по разделам (`path_conflict`) — не значение и не совпадает ни с чем (спека §2.8)
# ---------------------------------------------------------------------------

CONFLICT_REQUIRED = frozenset({REQUIRED})
CONFLICT_RELEASED = frozenset({RELEASED})
EMPTY_REQUIRED = {RELEASED: 1, REQUIRED: None}


def test_conflict_on_kp_blocks_same_empty_variant_and_names_the_reason() -> None:
    cand = line(1, 1, "100", values=EMPTY_REQUIRED)
    control = run(kp(values=EMPTY_REQUIRED), [cand], [contract(1)])
    assert (control.rung_no, ids(control)) == (1, [1])
    ladder = run(kp(values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED), [cand], [contract(1)])
    assert (ladder.rung_no, ladder.no_history_reason) == (None, "path_conflict")


def test_conflict_on_candidate_is_not_a_candidate_of_rungs_one_and_three() -> None:
    conflicted = line(1, 1, "100", values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED)
    clean = line(2, 2, "90", values=EMPTY_REQUIRED)
    work = kp(values=EMPTY_REQUIRED)
    assert run(work, [conflicted], [contract(1)]).rung_no is None
    for cls_a, cls_b, rung in ((CLASS_A, CLASS_A, 1), (CLASS_B, CLASS_B, 3)):
        ladder = run(work, [conflicted, clean], [contract(1, cls=cls_a), contract(2, cls=cls_b)])
        assert (ladder.rung_no, ids(ladder)) == (rung, [2])


def test_conflict_on_released_parameter_does_not_block_rung_two() -> None:
    work = kp(values={RELEASED: None, REQUIRED: 5}, conflicted=CONFLICT_RELEASED)
    cand = line(1, 1, "80", variant=W8, values={RELEASED: None, REQUIRED: 5}, conflicted=CONFLICT_RELEASED)
    ladder = run(work, [cand], [contract(1)])
    assert (ladder.rung_no, ids(ladder)) == (2, [1])


def test_conflict_on_required_parameter_keeps_candidate_out_of_rungs_two_and_four() -> None:
    cand = line(1, 1, "80", variant=W8, values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED)
    work = kp(values=EMPTY_REQUIRED)
    for cls in (CLASS_A, CLASS_B):
        assert run(work, [cand], [contract(1, cls=cls)]).rung_no is None
    clean = line(2, 2, "90", variant=W8, values=EMPTY_REQUIRED)
    ladder = run(work, [cand, clean], [contract(1), contract(2)])
    assert (ladder.rung_no, ids(ladder)) == (2, [2])


def test_conflict_on_kp_required_parameter_blocks_rungs_two_and_four() -> None:
    work = kp(variant=W8, values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED)
    other = line(1, 1, "80", variant=W6, values=EMPTY_REQUIRED)
    assert run(kp(variant=W8, values=EMPTY_REQUIRED), [other], [contract(1)]).rung_no == 2
    for cls in (CLASS_A, CLASS_B):
        ladder = run(work, [other], [contract(1, cls=cls)])
        assert (ladder.rung_no, ladder.no_history_reason) == (None, "path_conflict")


def test_conflict_on_kp_released_parameter_only_leaves_reason_by_the_rest() -> None:
    work = kp(values={RELEASED: None, REQUIRED: 5}, conflicted=CONFLICT_RELEASED)
    far = line(1, 1, "80", variant=W8, values={RELEASED: 1, REQUIRED: 6})
    assert run(work, [far], [contract(1)]).no_history_reason == "no_params"


def test_conflict_on_kp_without_releasable_ladder_names_path_conflict() -> None:
    far = line(1, 1, "80", variant=W8, values={RELEASED: 1, REQUIRED: 6})
    work = kp(conflicted=CONFLICT_RELEASED)
    ladder = run(work, [far], [contract(1)], families={FAMILY: info(releasable=())})
    assert ladder.no_history_reason == "path_conflict"


def test_no_family_comes_before_path_conflict() -> None:
    work = kp(conflicted=CONFLICT_REQUIRED)
    assert run(work, [line(1, 1, "100", family=2)], [contract(1)]).no_history_reason == "no_family"


def test_path_conflict_comes_before_no_zone_match() -> None:
    work = kp(values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED)
    cand = line(1, 1, "100", values=EMPTY_REQUIRED, zone="aboveground")
    assert run(work, [cand], [contract(1)]).no_history_reason == "path_conflict"


def test_conflicted_candidate_does_not_hide_no_params_or_zone_reasons() -> None:
    cand = line(1, 1, "100", values=EMPTY_REQUIRED, zone="aboveground", conflicted=CONFLICT_REQUIRED)
    work = kp(values=EMPTY_REQUIRED)
    assert run(work, [cand], [contract(1)]).no_history_reason == "no_params"


def test_conflict_applies_to_unknown_vat_base_lines() -> None:
    work = kp(values=EMPTY_REQUIRED, conflicted=CONFLICT_REQUIRED)
    unknown = line(1, 1, "100", values=EMPTY_REQUIRED, vat=None)
    assert run(work, [unknown], [contract(1)]).vat_dropped == ()
    clean_work = kp(values=EMPTY_REQUIRED)
    assert run(clean_work, [unknown], [contract(1)]).vat_dropped == (1,)
    conflicted_unknown = line(1, 1, "100", values=EMPTY_REQUIRED, vat=None, conflicted=CONFLICT_REQUIRED)
    assert run(clean_work, [conflicted_unknown], [contract(1)]).vat_dropped == ()

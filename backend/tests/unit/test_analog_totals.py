"""Ядро «Нашей цены» с лестницей аналогов: состояния, цифры работы, системы, итоги с покрытием
(спека лестницы аналогов §2.9, §2.10, §2.12, §2.13).

Чистый расчёт без базы. Условные имена: семья 1, вариант W6 (параметры 10 «Марка» —
отпускаемый и 11 «Класс» — обязательный), договоры с номерами по идентификатору. База НДС 0,
поэтому нетто равно валовой цене; эталоны посчитаны руками.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

import services.our_price as core
from money.inflation import YearMonth
from services.analog_ladder import FamilyLadderInfo, WorkHistoryLine
from services.our_price import (
    HistoryContract,
    HistoryIndex,
    HistoryLine,
    KpLine,
    LineKey,
    LineState,
    OfferEvaluation,
    evaluate_offer,
    lines_totals,
    offer_totals,
)

D = Decimal

CLASS_A = 1
CLASS_B = 2
FAMILY = 1
W6 = 100
SCHEMA = 7
RELEASED = 10
REQUIRED = 11
ZONE = "underground"
TARGET = YearMonth(2026, 7)
AREA = D(100)

KEY = LineKey(family_id=FAMILY, variant_id=W6, first_main=None)
V50 = LineKey(family_id=5, variant_id=50, first_main=None)


def contract(cid: int, *, cls: int = CLASS_A, period: YearMonth | None = YearMonth(2026, 1), area=None, contractor=None):
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 10,
        number=f"N{cid}",
        object_title="Объект",
        year=2026,
        rate_class_id=cls,
        rate_class_title="Класс",
        contractor_id=contractor or cid,
        contractor_title=f"Подрядчик {cid}",
        area=area,
        period=period,
    )


def info() -> FamilyLadderInfo:
    return FamilyLadderInfo(
        schema_id=SCHEMA,
        main_parameter_ids=(RELEASED, REQUIRED),
        releasable_ids=frozenset({RELEASED}),
        names={RELEASED: "Марка", REQUIRED: "Класс"},
    )


def wline(pid: int, cid: int, price: str, *, zone: str | None = ZONE) -> WorkHistoryLine:
    return WorkHistoryLine(
        position_item_id=pid,
        contract_id=cid,
        family_id=FAMILY,
        chain_id=1,
        zone=zone,  # type: ignore[arg-type]
        chain_titles=frozenset(),
        variant_id=W6,
        schema_id=SCHEMA,
        main_values={RELEASED: 1, REQUIRED: 5},
        unit_price=D(price),
        weight=D(1),
        vat_base=D(0),
    )


def work(
    pid: int = 1,
    price: str | None = "150",
    *,
    weight: str | None = "10",
    key: LineKey | None = KEY,
    zone: str | None = ZONE,
    in_progress: bool = False,
    is_system: bool = False,
) -> KpLine:
    unit = None if price is None else D(price)
    w = None if weight is None else D(weight)
    return KpLine(
        position_item_id=pid,
        unit_price=unit,
        total=unit * w if unit is not None and w is not None else None,
        weight=w,
        key=key,
        is_system=is_system,
        in_progress=in_progress,
        waiting_admin=False,
        zone=zone,  # type: ignore[arg-type]
        schema_id=SCHEMA,
        main_values={RELEASED: 1, REQUIRED: 5},
    )


def system(pid: int, total: str | None, key: LineKey | None = V50, *, in_progress: bool = False) -> KpLine:
    value = None if total is None else D(total)
    return KpLine(
        position_item_id=pid,
        unit_price=value,
        total=value,
        weight=D(1),
        key=key,
        is_system=True,
        in_progress=in_progress,
        waiting_admin=False,
    )


def sline(cid: int, key: LineKey, total: str, *, unit_price: str | None = None) -> HistoryLine:
    return HistoryLine(cid, key, True, None if unit_price is None else D(unit_price), D(total), None, D(0))


def evaluate(
    lines: list[KpLine],
    *,
    contracts: list[HistoryContract] | None = None,
    work_lines: list[WorkHistoryLine] | None = None,
    sys_lines: list[HistoryLine] | None = None,
    base: D | None = D(0),
    area: D | None = AREA,
    target: YearMonth | None = TARGET,
) -> OfferEvaluation:
    contracts = contracts if contracts is not None else []
    return evaluate_offer(
        lines,
        HistoryIndex(contracts, sys_lines or [], factor_by_estimate=None),
        work_history=work_lines or [],
        families={FAMILY: info()},
        kp_vat_base=base,
        tender_area=area,
        tender_class_id=CLASS_A,
        tender_contractor_id=99,
        target_month=target,
        factors=None,
    )


def two_contract_history():
    """Договор 1 (январь) — 100, договор 2 (июнь) — 120; цель — июль 2026."""
    return (
        [contract(1), contract(2, period=YearMonth(2026, 6))],
        [wline(1001, 1, "100"), wline(1002, 2, "120")],
    )


# --- состояния -----------------------------------------------------------------


def test_line_state_declaration_order_puts_no_zone_between_no_variant_and_no_history():
    assert [state.value for state in LineState] == [
        "in_progress",
        "unknown_vat_base",
        "no_price",
        "no_area",
        "no_variant",
        "no_zone",
        "no_history",
        "unit_mismatch",
        "comparable",
    ]


@pytest.mark.parametrize(
    ("line", "base", "expected"),
    [
        (work(zone=None), D(0), LineState.no_zone),
        (work(zone=None, key=None), D(0), LineState.no_variant),
        (work(zone=None, price=None), D(0), LineState.no_price),
        (work(zone=None, in_progress=True), D(0), LineState.in_progress),
        (work(zone=None), None, LineState.unknown_vat_base),
        (work(zone=ZONE), D(0), LineState.no_history),
    ],
)
def test_work_state_follows_the_table_top_down(line, base, expected):
    assert evaluate([line], base=base).lines[line.position_item_id].state is expected


def test_a_system_does_not_need_a_zone():
    line = system(1, "10000")
    result = evaluate([line], contracts=[contract(1, area=AREA)], sys_lines=[sline(1, V50, "10000")]).lines[1]
    assert result.state is LineState.comparable


# --- цифры работы ---------------------------------------------------------------


def test_work_comparable_numbers_come_from_the_ladder():
    contracts, lines = two_contract_history()
    result = evaluate([work()], contracts=contracts, work_lines=lines).lines[1]

    assert result.state is LineState.comparable
    assert result.ladder is not None and result.ladder.rung_no == 1
    # прежних полей лестницы у работы нет: ступень, ступени и договоры системные
    assert result.rung is None and result.rungs == () and result.contracts == ()
    assert result.ours == D(100) and result.ours_from_all_classes is False
    assert result.freshest is not None
    assert (result.freshest.value, result.freshest.analog_id, result.freshest.contract_id) == (D(120), 1002, 2)
    assert result.to_ours == D(500)  # (150 - 100) * 10
    assert result.to_freshest == D(300)  # (150 - 120) * 10
    assert result.bargain == D(500)
    assert result.below is None
    assert result.position == "above"
    assert (result.range.min, result.range.max, result.range.median) == (D(100), D(120), D(110))
    assert result.mismatch is None


def test_work_below_the_lowest_counts_in_below_and_gives_a_negative_to_lowest():
    contracts, lines = two_contract_history()
    result = evaluate([work(price="90")], contracts=contracts, work_lines=lines).lines[1]

    assert result.state is LineState.comparable
    assert result.position == "below"
    assert result.to_ours == D(-100)
    assert result.bargain == D(0)
    assert result.below == D(-100)  # (90 - 100) * 10
    assert result.to_freshest == D(-300)


def test_work_difference_to_the_contractor_is_signed_and_null_without_a_figure():
    contracts, lines = two_contract_history()
    contracts.append(contract(3, contractor=99))
    lines.append(wline(1003, 3, "130"))
    above = evaluate([work()], contracts=contracts, work_lines=lines).lines[1]
    below = evaluate([work(price="120")], contracts=contracts, work_lines=lines).lines[1]
    alone = evaluate([work()], contracts=contracts[:2], work_lines=lines[:2]).lines[1]

    assert (above.to_contractor_unit, above.to_contractor) == (D(20), D(200))  # (150 - 130) * 10
    assert (below.to_contractor_unit, below.to_contractor) == (D(-10), D(-100))  # (120 - 130) * 10
    assert (alone.to_contractor_unit, alone.to_contractor) == (None, None)


def test_work_all_outliers_is_a_unit_mismatch_without_numbers():
    contracts = [contract(1), contract(2)]
    lines = [wline(1001, 1, "2"), wline(1002, 2, "100000")]
    result = evaluate([work()], contracts=contracts, work_lines=lines).lines[1]

    assert result.state is LineState.unit_mismatch
    assert result.ladder.all_outliers is True
    assert result.mismatch is not None and result.mismatch.kp_ratio is None
    assert result.ours is None and result.to_ours is None and result.to_freshest is None
    assert result.freshest is None and result.range is None
    assert result.bargain is None and result.below is None


def test_work_kp_in_other_units_keeps_the_numbers_but_not_the_sums():
    contracts, lines = two_contract_history()
    result = evaluate([work(price="1500")], contracts=contracts, work_lines=lines).lines[1]

    assert result.state is LineState.unit_mismatch
    assert abs(result.mismatch.kp_ratio - D(1500) / D(110)) < D("1e-20")
    assert result.ours == D(100)
    assert result.to_ours == D(14000) and result.to_freshest == D(13800)
    assert result.bargain is None and result.below is None


def test_work_without_candidates_is_no_history_with_a_reason():
    result = evaluate([work()], contracts=[contract(1)], work_lines=[wline(1001, 1, "100", zone="territory")])
    line = result.lines[1]
    assert line.state is LineState.no_history
    assert line.ladder.no_history_reason == "no_zone_match"
    assert line.ours is None and line.freshest is None


def test_the_old_work_rung_is_gone_from_the_core():
    assert not hasattr(core, "SavedWorkRung")
    assert not hasattr(core, "_work_manual")
    assert core.SavedRungs.__dataclass_fields__.keys() == {"family"}


# --- итоги: суммы и покрытие самой свежей ---------------------------------------------


def _totals(lines, contracts, work_lines, *, target=TARGET):
    return offer_totals(evaluate(lines, contracts=contracts, work_lines=work_lines, target=target))


def test_totals_over_lowest_over_freshest_and_below_are_sums_over_comparable_lines():
    contracts, lines = two_contract_history()
    kp_lines = [work(1, "150"), work(2, "110"), work(3, "90")]
    totals = _totals(kp_lines, contracts, lines)

    # к самой низкой (100): +500, +100, -100; к самой свежей (120): +300, -100, -300
    assert totals.bargain == D(600)  # Σ max(0, to_lowest)
    assert totals.over_freshest == D(300)  # Σ max(0, to_freshest)
    assert totals.below == D(-100) and totals.below_lines == 1
    assert (totals.comparable_lines, totals.freshest_lines) == (3, 3)


def test_coverage_row_1_no_comparable_lines_gives_null_and_zero_counts():
    totals = _totals([work(1, None)], [contract(1)], [wline(1001, 1, "100")])
    assert totals.over_freshest is None
    assert (totals.comparable_lines, totals.freshest_lines) == (0, 0)


def test_coverage_row_2_no_freshest_anywhere_is_null_not_zero():
    """К6: сумма пустого набора нулём не показывается."""
    contracts = [contract(1, period=None), contract(2, period=None)]
    lines = [wline(1001, 1, "100"), wline(1002, 2, "120")]
    totals = _totals([work(1, "150"), work(2, "110")], contracts, lines)

    assert totals.comparable_lines == 2 and totals.freshest_lines == 0
    assert totals.over_freshest is None
    assert totals.bargain == D(600)  # самая низкая у строк есть: итог по ней не пуст


def test_coverage_row_2_the_null_is_not_zero_even_when_the_sum_would_be_zero():
    contracts = [contract(1, period=None)]
    totals = _totals([work(1, "90")], contracts, [wline(1001, 1, "100")])
    assert totals.over_freshest is None
    assert totals.over_freshest != D(0)


def test_coverage_row_3_part_of_the_lines_have_a_freshest():
    contracts = [contract(1, period=YearMonth(2026, 6)), contract(2, period=None)]
    dated = [wline(1001, 1, "120")]
    undated_variant = [
        WorkHistoryLine(
            position_item_id=2001,
            contract_id=2,
            family_id=FAMILY,
            chain_id=1,
            zone=ZONE,  # type: ignore[arg-type]
            chain_titles=frozenset(),
            variant_id=W6 + 1,
            schema_id=SCHEMA,
            main_values={RELEASED: 1, REQUIRED: 6},
            unit_price=D(100),
            weight=D(1),
            vat_base=D(0),
        )
    ]
    first = work(1, "150")
    second = work(2, "150", key=LineKey(FAMILY, W6 + 1, None))
    second = dataclasses.replace(second, main_values={RELEASED: 1, REQUIRED: 6})
    totals = _totals([first, second], contracts, dated + undated_variant)

    assert totals.comparable_lines == 2 and totals.freshest_lines == 1
    assert totals.over_freshest == D(300)  # только первая строка: (150 - 120) * 10


def test_coverage_row_4_every_comparable_line_has_a_freshest():
    contracts, lines = two_contract_history()
    totals = _totals([work(1, "150"), work(2, "150")], contracts, lines)
    assert totals.comparable_lines == totals.freshest_lines == 2
    assert totals.over_freshest == D(600)


def test_unit_mismatch_lines_are_not_in_the_comparable_counts():
    contracts, lines = two_contract_history()
    totals = _totals([work(1, "150"), work(2, "1500")], contracts, lines)
    assert totals.comparable_lines == 1 and totals.freshest_lines == 1
    assert totals.unit_mismatch_lines == 1


def test_lines_totals_counts_coverage_over_the_subset():
    contracts, lines = two_contract_history()
    evaluation = evaluate([work(1, "150"), work(2, "110")], contracts=contracts, work_lines=lines)
    part = lines_totals(evaluation, [2])
    assert (part.comparable_lines, part.freshest_lines) == (1, 1)
    assert part.over_freshest == D(0)  # (110 - 120) * 10 < 0, но покрытие непустое
    assert part.bargain == D(100)


# --- системы: самая свежая -----------------------------------------------------------


def _system_history(periods: dict[int, YearMonth | None], totals: dict[int, str], classes=None):
    classes = classes or {}
    contracts = [contract(cid, cls=classes.get(cid, CLASS_A), period=periods[cid], area=AREA) for cid in totals]
    lines = [sline(cid, V50, total) for cid, total in totals.items()]
    return contracts, lines


def _system_one(contracts, lines, *, kp_total="20000", target=TARGET):
    return evaluate([system(1, kp_total)], contracts=contracts, sys_lines=lines, target=target).lines[1]


def test_system_freshest_is_the_nearest_class_contract_and_lowest_stays_the_minimum():
    contracts, lines = _system_history(
        {1: YearMonth(2026, 1), 2: YearMonth(2026, 5)}, {1: "10000", 2: "15000"}
    )
    result = _system_one(contracts, lines)

    assert result.ours == D(100) and result.state is LineState.comparable
    assert (result.freshest.value, result.freshest.analog_id, result.freshest.contract_id) == (D(150), None, 2)
    assert result.to_ours == D(10000) and result.to_freshest == D(5000)  # (200 - 150) * 100


def test_system_freshest_ties_take_the_lower_rate_then_the_lower_contract_id():
    contracts, lines = _system_history(
        {1: YearMonth(2026, 4), 2: YearMonth(2026, 6), 3: YearMonth(2026, 6)},
        {1: "15000", 2: "12000", 3: "12000"},
    )
    result = _system_one(contracts, lines, target=YearMonth(2026, 5))
    assert (result.freshest.contract_id, result.freshest.value) == (2, D(120))

    contracts, lines = _system_history({1: YearMonth(2026, 4), 2: YearMonth(2026, 6)}, {1: "15000", 2: "12000"})
    result = _system_one(contracts, lines, target=YearMonth(2026, 5))
    assert result.freshest.contract_id == 2  # ставка 120 меньше 150


def test_system_freshest_without_a_target_month_is_the_latest_period():
    contracts, lines = _system_history(
        {1: YearMonth(2026, 1), 2: YearMonth(2026, 5)}, {1: "10000", 2: "15000"}
    )
    assert _system_one(contracts, lines, target=None).freshest.contract_id == 2


def test_system_freshest_skips_undated_contracts_and_is_none_when_all_are_undated():
    contracts, lines = _system_history({1: YearMonth(2026, 1), 2: None}, {1: "10000", 2: "15000"})
    assert _system_one(contracts, lines).freshest.contract_id == 1

    contracts, lines = _system_history({1: None, 2: None}, {1: "10000", 2: "15000"})
    result = _system_one(contracts, lines)
    assert result.freshest is None and result.to_freshest is None
    assert result.state is LineState.comparable and result.ours == D(100)


def test_system_freshest_comes_from_the_scope_of_the_lowest():
    """Класс тендера пуст на ступени: самая низкая и самая свежая — среди всех классов."""
    contracts, lines = _system_history(
        {3: YearMonth(2026, 1), 4: YearMonth(2026, 6)},
        {3: "5000", 4: "8000"},
        classes={3: CLASS_B, 4: CLASS_B},
    )
    result = _system_one(contracts, lines)
    assert result.ours == D(50) and result.ours_from_all_classes is True
    assert (result.freshest.contract_id, result.freshest.value) == (4, D(80))


def test_system_freshest_stays_among_class_contracts_when_the_class_has_any():
    """Самая низкая — свой класс, и самая свежая тоже, хотя чужой договор свежее."""
    contracts, lines = _system_history(
        {1: YearMonth(2026, 1), 3: YearMonth(2026, 7)},
        {1: "10000", 3: "5000"},
        classes={3: CLASS_B},
    )
    # ступень (v, all) — два договора, порог достигнут; самая низкая из своего класса — договор 1
    result = _system_one(contracts, lines)
    assert result.ours_from_all_classes is False
    assert result.freshest.contract_id == 1


def test_group_money_for_freshest_is_counted_once_per_group():
    contracts, lines = _system_history({1: YearMonth(2026, 1), 2: YearMonth(2026, 5)}, {1: "10000", 2: "15000"})
    evaluation = evaluate(
        [system(1, "10000"), system(2, "10000")], contracts=contracts, sys_lines=lines
    )
    (group,) = evaluation.groups
    assert group.freshest.contract_id == 2
    assert group.to_freshest == D(5000)  # (200 - 150) * 100

    totals = offer_totals(evaluation)
    assert totals.over_freshest == D(5000)  # не 2 × 5000 и не 4 × 2500
    assert (totals.comparable_lines, totals.freshest_lines) == (2, 2)

    part = lines_totals(evaluation, [1])
    assert part.over_freshest == D(2500)
    assert (part.comparable_lines, part.freshest_lines) == (1, 1)


def test_system_unit_mismatch_has_no_over_freshest_and_does_not_count_as_comparable():
    contracts, lines = _system_history({1: YearMonth(2026, 1), 2: YearMonth(2026, 5)}, {1: "10000", 2: "15000"})
    evaluation = evaluate([system(1, "2000000")], contracts=contracts, sys_lines=lines)
    totals = offer_totals(evaluation)
    assert totals.unit_mismatch_lines == 1
    assert totals.comparable_lines == 0 and totals.over_freshest is None


# --- системы: порог 1 ₽ (К4) ------------------------------------------------------------


def _rates(contracts, lines, key=V50):
    index = HistoryIndex(contracts, lines, factor_by_estimate=None)
    return {r.contract_id: r for r in index.rates("v", key.variant_id, is_system=True, class_id=None)}


def test_a_contract_line_below_one_ruble_per_unit_does_not_enter_the_key_sum():
    """К4: 0,50 ₽ за единицу не входит в сумму ключа до деления на площадь."""
    contracts = [contract(1, area=D(1000))]
    lines = [sline(1, V50, "10000", unit_price="10000"), sline(1, V50, "0.5", unit_price="0.5")]
    rate = _rates(contracts, lines)[1]
    assert rate.nominal == D(10)  # 10000 / 1000, а не 10000.5 / 1000
    assert rate.lines == 1


def test_a_per_area_rate_of_fifty_kopecks_enters():
    """К4: ставка 0,50 ₽/м² — не строка договора, порогу не подчиняется."""
    contracts = [contract(1, area=D(1000))]
    lines = [sline(1, V50, "500", unit_price="500")]
    rate = _rates(contracts, lines)[1]
    assert rate.nominal == D("0.5")


def test_exactly_one_ruble_per_unit_enters():
    contracts = [contract(1, area=D(1000))]
    lines = [sline(1, V50, "1", unit_price="1")]
    assert _rates(contracts, lines)[1].nominal == D("0.001")


def test_the_threshold_looks_at_the_nominal_not_at_the_adjusted_price():
    contracts = [contract(1, area=D(1000))]
    lines = [sline(1, V50, "10000", unit_price="10000"), sline(1, V50, "0.9", unit_price="0.9")]
    index = HistoryIndex(contracts, lines, factor_by_estimate={10: D(3)})
    (rate,) = index.rates("v", 50, is_system=True, class_id=None)
    assert rate.nominal == D(10)  # 0,9 ₽ отсечено по номиналу, множитель на порог не влияет
    assert rate.net == D(30)


def test_a_system_line_without_a_unit_price_is_not_cut_by_the_threshold():
    contracts = [contract(1, area=D(1000))]
    lines = [sline(1, V50, "10000")]
    assert _rates(contracts, lines)[1].nominal == D(10)


def test_the_threshold_is_taken_after_the_vat_base():
    """Порог — по нетто: 1,10 ₽ с НДС 20 % — это 0,917 ₽ нетто, строка уходит."""
    contracts = [contract(1, area=D(1000))]
    lines = [
        HistoryLine(1, V50, True, D("10000"), D("12000"), None, D(20)),
        HistoryLine(1, V50, True, D("1.10"), D("1.10"), None, D(20)),
    ]
    rate = _rates(contracts, lines)[1]
    assert rate.nominal == D(10)  # 12000 / 1.2 / 1000
    assert rate.lines == 1


# --- разница за единицу (to_lowest_unit / to_freshest_unit) ----------------------


def test_work_unit_diff_is_the_kp_price_minus_the_figure_in_the_same_axis_as_the_totals():
    contracts, lines = two_contract_history()
    result = evaluate([work()], contracts=contracts, work_lines=lines).lines[1]
    assert result.to_ours_unit == D(50)  # 150 - 100
    assert result.to_freshest_unit == D(30)  # 150 - 120
    assert result.to_ours == result.to_ours_unit * D(10)

    below = evaluate([work(price="90")], contracts=contracts, work_lines=lines).lines[1]
    assert below.to_ours_unit == D(-10) and below.to_freshest_unit == D(-30)


def test_work_unit_diff_is_none_without_the_figure():
    contracts = [contract(1, period=None)]
    result = evaluate([work()], contracts=contracts, work_lines=[wline(1001, 1, "100")]).lines[1]
    assert result.to_ours_unit == D(50) and result.freshest is None and result.to_freshest_unit is None

    outliers = evaluate(
        [work()], contracts=[contract(1), contract(2)], work_lines=[wline(1001, 1, "2"), wline(1002, 2, "100000")]
    ).lines[1]
    assert outliers.to_ours_unit is None and outliers.to_freshest_unit is None

    nothing = evaluate([work()], contracts=[contract(1)], work_lines=[]).lines[1]
    assert nothing.to_ours_unit is None and nothing.to_freshest_unit is None


def test_work_unit_diff_survives_a_unit_mismatch_like_to_lowest():
    contracts, lines = two_contract_history()
    result = evaluate([work(price="1500")], contracts=contracts, work_lines=lines).lines[1]
    assert result.state is LineState.unit_mismatch
    assert result.to_ours_unit == D(1400) and result.to_freshest_unit == D(1380)


def test_system_unit_diff_is_the_rate_per_sqm_of_the_group_for_every_member():
    contracts, lines = _system_history({1: YearMonth(2026, 1), 2: YearMonth(2026, 5)}, {1: "10000", 2: "15000"})
    evaluation = evaluate([system(1, "12000"), system(2, "8000")], contracts=contracts, sys_lines=lines)
    for pid in (1, 2):
        result = evaluation.lines[pid]
        assert result.to_ours_unit == D(100)  # 20000 / 100 - 100
        assert result.to_freshest_unit == D(50)  # 200 - 150

    nofresh_contracts, nofresh_lines = _system_history({1: None, 2: None}, {1: "10000", 2: "15000"})
    one = _system_one(nofresh_contracts, nofresh_lines)
    assert one.to_ours_unit == D(100) and one.to_freshest_unit is None

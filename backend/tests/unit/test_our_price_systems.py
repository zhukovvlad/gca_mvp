"""Системы «Нашей цены»: одна ступень на семью внутри КП, деньги на группу (спека 4а §2.6–§2.8, §2.18).

Эталоны — числа, посчитанные руками. База НДС 0, площадь объекта тендера 100,
площадь договоров 100: ставка договора на м² равна его сумме по ключу / 100.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from services.our_price import (
    HistoryContract,
    HistoryIndex,
    HistoryLine,
    KpLine,
    LineKey,
    LineState,
    OfferEvaluation,
    Rung,
    RungChoice,
    evaluate_offer,
    lines_totals,
    offer_totals,
)

D = Decimal
NAN = D("NaN")

V_CLS = Rung("v", "cls")
V_ALL = Rung("v", "all")
P_CLS = Rung("p", "cls")
F_CLS = Rung("f", "cls")
F_ALL = Rung("f", "all")

TENDER_CLASS = 1
OTHER_CLASS = 2
AREA = D(100)
TOLERANCE = D("1e-20")

V50 = LineKey(family_id=5, variant_id=50, first_main=None)
V51 = LineKey(family_id=5, variant_id=51, first_main=None)
P50 = LineKey(family_id=5, variant_id=50, first_main=(7, 70))
P51 = LineKey(family_id=5, variant_id=51, first_main=(7, 70))
OTHER_FAMILY = LineKey(family_id=6, variant_id=60, first_main=None)


def contract(cid: int, *, cls: int = TENDER_CLASS, area: D | None = AREA) -> HistoryContract:
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 100,
        number=f"Договор {cid}",
        object_title="Объект Т",
        year=2025,
        rate_class_id=cls,
        rate_class_title="Класс",
        contractor_id=cid,
        contractor_title=f"Подрядчик {cid}",
        area=area,
    )


def sline(cid: int, key: LineKey, total: D) -> HistoryLine:
    return HistoryLine(cid, key, True, None, total, None, D(0))


def wline(cid: int, key: LineKey, price: D) -> HistoryLine:
    return HistoryLine(cid, key, False, price, price, D(1), D(0))


def index(contracts: list[HistoryContract], lines: list[HistoryLine]) -> HistoryIndex:
    return HistoryIndex(contracts, lines, factor_by_estimate=None)


def kp(
    pid: int,
    total: D | None,
    key: LineKey | None = V50,
    *,
    in_progress: bool = False,
    is_system: bool = True,
) -> KpLine:
    return KpLine(
        position_item_id=pid,
        unit_price=total,
        total=total,
        weight=D(1),
        key=key,
        is_system=is_system,
        in_progress=in_progress,
        waiting_admin=False,
    )


def evaluate(
    lines: list[KpLine],
    history: HistoryIndex,
    *,
    base: D | None = D(0),
    area: D | None = AREA,
    choice: RungChoice | None = None,
) -> OfferEvaluation:
    return evaluate_offer(
        lines,
        history,
        kp_vat_base=base,
        tender_area=area,
        tender_class_id=TENDER_CLASS,
        choice=choice,
    )


def close(actual: D, expected: D) -> bool:
    return abs(actual - expected) <= TOLERANCE * abs(expected)


@pytest.fixture
def buildings() -> HistoryIndex:
    """Обезличенный повтор случая с временными зданиями.

    Вариант 50: договор 1 даёт 100 ₽/м², договор 2 — 200. Вариант 51: только
    договор 1, 150 ₽/м². Вся семья: договор 1 — 250, договор 2 — 200.
    """
    return index(
        [contract(1), contract(2)],
        [sline(1, V50, D(10000)), sline(1, V51, D(15000)), sline(2, V50, D(20000))],
    )


def two_variant_lines() -> list[KpLine]:
    return [kp(1, D(15000), V50), kp(2, D(14000), V51)]


# --- повтор случая «Временные здания» -----------------------------------------------


def test_buildings_both_lines_share_one_rung_and_the_group_price_is_the_sum(buildings):
    result = evaluate(two_variant_lines(), buildings)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.rung == F_CLS
    assert group.net == D(29000)
    assert group.per_sqm == D(290)
    assert group.lines == (1, 2)
    for pid in (1, 2):
        line = result.lines[pid]
        assert line.rung == F_CLS
        assert line.group_per_sqm == D(290)
        assert line.group_index == 0
        assert line.group_lines == 2


def test_buildings_group_is_above_the_range_while_each_line_alone_is_not_compared_to_it(buildings):
    result = evaluate(two_variant_lines(), buildings)
    group_line = result.lines[1]
    assert group_line.range is not None
    assert (group_line.range.min, group_line.range.max) == (D(200), D(250))
    assert group_line.position == "above"
    assert group_line.ours == D(200)

    # Каждая строка сама по себе ниже вилки семьи: 150 и 140 против минимума 200.
    for pid in (1, 2):
        assert result_line_per_sqm(result, pid) < group_line.range.min

    # Первая строка одна: её ступень вариант, а не семья.
    alone = evaluate([kp(1, D(15000), V50)], buildings)
    assert alone.groups[0].rung == V_CLS
    assert alone.lines[1].group_per_sqm == D(150)
    assert alone.lines[1].position == "in"

    # Вторая строка одна уходит на ступень семьи и выглядит «ниже всего, что платили».
    lone = evaluate([kp(2, D(14000), V51)], buildings).lines[2]
    assert lone.rung == F_CLS
    assert lone.position == "below"
    assert lone.below is not None and lone.below < 0


def result_line_per_sqm(result: OfferEvaluation, pid: int) -> D:
    net = result.lines[pid].kp_total_net
    assert net is not None
    return net / AREA


def test_each_line_alone_is_below_the_family_range_while_the_group_is_above():
    # у каждого варианта свой единственный договор, у семьи — два: 200 и 250 ₽/м²
    history = index([contract(1), contract(2)], [sline(1, V50, D(20000)), sline(2, V51, D(25000))])
    lines = [kp(1, D(15000), V50), kp(2, D(14000), V51)]

    for line in lines:
        alone = evaluate([line], history).lines[line.position_item_id]
        assert alone.rung == F_CLS
        assert alone.position == "below"
        assert alone.below is not None and alone.below < 0

    together = evaluate(lines, history)
    group = together.groups[0]
    assert group.rung == F_CLS
    assert group.per_sqm == D(290)
    assert group.below is None
    assert group.bargain == D(9000)
    assert together.lines[1].position == together.lines[2].position == "above"
    assert together.lines[1].below is None and together.lines[2].below is None


def test_buildings_money_is_computed_once_for_the_group(buildings):
    group = evaluate(two_variant_lines(), buildings).groups[0]
    # (290 - 200) * 100
    assert group.to_ours == D(9000)
    assert group.bargain == D(9000)
    assert group.below is None
    assert group.state is LineState.comparable


def test_buildings_each_line_gets_its_share_of_the_group_money(buildings):
    result = evaluate(two_variant_lines(), buildings)
    first, second = result.lines[1], result.lines[2]
    assert close(first.share, D(15) / D(29))
    assert close(second.share, D(14) / D(29))
    assert close(first.to_ours, D(9000) * D(15) / D(29))
    assert close(second.bargain, D(9000) * D(14) / D(29))
    assert first.state is second.state is LineState.comparable


# --- состав группы и ступень -----------------------------------------------------------


def test_auto_takes_first_rung_where_every_group_has_two_contracts():
    history = index(
        [contract(1), contract(2)],
        [sline(1, V50, D(10000)), sline(1, V51, D(10000)), sline(2, V50, D(10000)), sline(2, V51, D(10000))],
    )
    result = evaluate([kp(1, D(100), V50), kp(2, D(100), V51)], history)

    assert [g.rung for g in result.groups] == [V_CLS, V_CLS]
    assert [g.key for g in result.groups] == [50, 51]
    assert result.lines[1].threshold_met and result.lines[2].threshold_met


def test_auto_without_two_everywhere_takes_first_rung_with_one_contract_per_group():
    history = index([contract(1)], [sline(1, V50, D(10000)), sline(1, V51, D(15000))])
    result = evaluate([kp(1, D(100), V50), kp(2, D(100), V51)], history)

    assert [g.rung for g in result.groups] == [V_CLS, V_CLS]
    assert not result.lines[1].threshold_met
    assert result.lines[1].auto and result.lines[2].auto


def test_auto_with_one_thin_group_moves_up_to_the_family():
    # variant 51 has a single contract: the variant rung fails the threshold of two,
    # the family rung has two contracts
    history = index(
        [contract(1), contract(2)],
        [sline(1, V50, D(10000)), sline(1, V51, D(10000)), sline(2, V50, D(10000))],
    )
    result = evaluate([kp(1, D(100), V50), kp(2, D(100), V51)], history)

    assert [g.rung for g in result.groups] == [F_CLS]
    assert result.lines[1].group_lines == 2


def test_auto_with_one_group_without_contracts_moves_to_the_first_rung_where_every_group_has_one():
    # variant 51 has no contract of its own: on the variant rungs the groups have 1 and 0,
    # the family rung has 1 for the single group
    history = index([contract(1)], [sline(1, V50, D(10000))])
    result = evaluate([kp(1, D(5000), V50), kp(2, D(5000), V51)], history)

    assert [g.rung for g in result.groups] == [F_CLS]
    assert result.groups[0].state is LineState.comparable
    assert not result.lines[1].threshold_met


def test_auto_without_any_history_ends_on_family_all_classes_with_no_history():
    history = index([contract(1)], [])
    result = evaluate([kp(1, D(100), V50), kp(2, D(100), V51)], history)

    assert [g.rung for g in result.groups] == [F_ALL]
    group = result.groups[0]
    assert group.state is LineState.no_history
    assert (group.to_ours, group.bargain, group.below) == (None, None, None)
    for pid in (1, 2):
        line = result.lines[pid]
        assert line.state is LineState.no_history
        assert line.rung == F_ALL
        assert line.auto
        assert line.contracts == ()
        assert line.bargain is None


def test_class_rung_before_all_classes_and_ours_marked_when_only_other_classes():
    history = index(
        [contract(1, cls=OTHER_CLASS), contract(2, cls=OTHER_CLASS)],
        [sline(1, V50, D(10000)), sline(2, V50, D(20000))],
    )
    line = evaluate([kp(1, D(15000), V50)], history).lines[1]

    assert line.rung == V_ALL
    assert line.ours_from_all_classes
    assert line.ours == D(100)


def test_p_rung_groups_variants_with_the_same_first_main_value():
    history = index(
        [contract(1), contract(2)],
        [sline(1, P50, D(10000)), sline(1, P51, D(15000)), sline(2, P50, D(20000))],
    )
    result = evaluate([kp(1, D(100), P50), kp(2, D(100), P51)], history)

    assert [g.rung for g in result.groups] == [P_CLS]
    assert result.groups[0].key == (5, (7, 70))
    assert result.groups[0].lines == (1, 2)
    # the group of the line counts its own contracts per rung: v50 sees 2, v51 sees 1
    assert [c.contracts for c in result.lines[1].rungs] == [2, 2, 2, 2, 2, 2]
    assert [c.contracts for c in result.lines[2].rungs] == [1, 1, 2, 2, 2, 2]


def test_one_line_without_first_main_leaves_the_whole_family_four_rungs():
    history = index([contract(1)], [sline(1, P50, D(10000)), sline(1, V51, D(10000))])
    result = evaluate([kp(1, D(100), P50), kp(2, D(100), V51)], history)

    assert len(result.lines[1].rungs) == 4
    assert all(c.rung.level != "p" for c in result.lines[1].rungs)


def test_a_line_without_a_usable_total_does_not_enter_the_group(buildings):
    result = evaluate([kp(1, D(15000), V50), kp(2, D(0), V51), kp(3, D(14000), V51)], buildings)

    assert result.lines[2].state is LineState.no_price
    assert result.lines[2].group_index is None
    assert result.groups[0].lines == (1, 3)
    assert result.groups[0].net == D(29000)


def test_contract_without_area_does_not_enter_the_rung():
    history = index(
        [contract(1), contract(2), contract(3, area=None)],
        [sline(1, V50, D(10000)), sline(2, V50, D(20000)), sline(3, V50, D(999999))],
    )
    line = evaluate([kp(1, D(15000), V50)], history).lines[1]

    assert [c.contract_id for c in line.contracts] == [1, 2]
    assert line.ours == D(100)


def test_works_and_systems_of_one_key_do_not_mix():
    history = index(
        [contract(1), contract(2)],
        [sline(1, V50, D(10000)), sline(2, V50, D(20000)), wline(1, V50, D(999999)), wline(2, V50, D(777777))],
    )
    system = evaluate([kp(1, D(15000), V50)], history).lines[1]
    work = evaluate([kp(2, D(500000), V50, is_system=False)], history).lines[2]

    assert [c.net for c in system.contracts] == [D(100), D(200)]
    assert [c.net for c in work.contracts] == [D(999999), D(777777)]
    assert work.group_index is None
    assert work.share is None


# --- деньги: знак с обеих сторон -----------------------------------------------------------


def test_price_below_ours_gives_zero_bargain_and_negative_below_once_per_group():
    history = index([contract(1), contract(2)], [sline(1, V50, D(10000)), sline(2, V50, D(20000))])
    result = evaluate([kp(1, D(2000), V50), kp(2, D(3000), V50)], history)

    group = result.groups[0]
    # price 50 against ours = min = 100, area 100
    assert group.per_sqm == D(50)
    assert group.to_ours == D(-5000)
    assert group.bargain == D(0)
    assert group.below == D(-5000)
    assert result.lines[1].position == "below"
    assert close(result.lines[1].below, D(-2000))
    assert close(result.lines[2].below, D(-3000))
    totals = offer_totals(result)
    assert totals.below == D(-5000)
    assert totals.bargain == D(0)
    # all lines of the group are counted, not the group
    assert totals.below_lines == 2


def test_price_inside_the_range_has_no_below():
    history = index([contract(1), contract(2)], [sline(1, V50, D(10000)), sline(2, V50, D(20000))])
    group = evaluate([kp(1, D(15000), V50)], history).groups[0]

    assert group.below is None
    assert group.bargain == D(5000)


def test_unit_mismatch_group_keeps_to_ours_and_blanks_the_rest_for_every_line():
    history = index([contract(1), contract(2)], [sline(1, V50, D(10000)), sline(2, V50, D(10000))])
    result = evaluate([kp(1, D(50000), V50), kp(2, D(50000), V50)], history)

    group = result.groups[0]
    # 1000 per square metre against a median of 100
    assert group.per_sqm == D(1000)
    assert group.state is LineState.unit_mismatch
    assert group.to_ours == D(90000)
    assert (group.bargain, group.below) == (None, None)
    for pid in (1, 2):
        line = result.lines[pid]
        assert line.state is LineState.unit_mismatch
        assert line.mismatch is not None
        assert line.mismatch.kp_ratio == D(10)
        assert line.bargain is None and line.below is None
        assert close(line.to_ours, D(45000))
    totals = offer_totals(result)
    assert totals.unit_mismatch_lines == 2
    assert totals.unit_mismatch_amount == D(100000)
    assert totals.bargain == D(0)


# --- суммы: группа один раз ------------------------------------------------------------------


def three_equal_lines() -> tuple[list[KpLine], HistoryIndex]:
    history = index([contract(1), contract(2)], [sline(1, V50, D(10000)), sline(2, V50, D(20000))])
    return [kp(1, D(10000), V50), kp(2, D(10000), V50), kp(3, D(10000), V50)], history


def test_three_equal_lines_shares_and_row_money_agree_with_the_group_within_tolerance():
    lines, history = three_equal_lines()
    result = evaluate(lines, history)

    group = result.groups[0]
    # 300 per square metre against ours 100, area 100
    assert group.to_ours == D(20000)
    shares = [result.lines[pid].share for pid in (1, 2, 3)]
    assert all(close(share, D(1) / D(3)) for share in shares)
    assert abs(sum(shares) - D(1)) <= TOLERANCE
    assert close(sum(result.lines[pid].to_ours for pid in (1, 2, 3)), group.to_ours)


def test_offer_total_equals_the_group_exactly_not_the_sum_of_its_shares():
    lines, history = three_equal_lines()
    result = evaluate(lines, history)

    assert offer_totals(result).bargain == result.groups[0].bargain == D(20000)


def test_offer_total_reads_the_group_even_when_the_row_shares_are_perturbed():
    lines, history = three_equal_lines()
    result = evaluate(lines, history)
    # a rounding artefact of any size in the row money must not reach the offer total
    perturbed = OfferEvaluation(
        lines={pid: replace(line, bargain=line.bargain + D("0.01")) for pid, line in result.lines.items()},
        groups=result.groups,
    )

    assert offer_totals(perturbed).bargain == D(20000)
    assert lines_totals(perturbed, [1, 2, 3]).bargain != D(20000)


def test_offer_total_is_the_group_exactly_where_the_row_shares_do_not_add_up():
    # nets 1000, 3000 and 14000: 180 per square metre against ours 100 on an area of 100
    history = index([contract(1), contract(2)], [sline(1, V50, D(10000)), sline(2, V50, D(20000))])
    lines = [kp(1, D(1000), V50), kp(2, D(3000), V50), kp(3, D(14000), V50)]
    result = evaluate(lines, history)

    group = result.groups[0]
    assert group.bargain == D(8000)
    row_sum = lines_totals(result, [1, 2, 3]).bargain
    # the input really separates the two sums: the row shares miss the group in the last digit
    assert row_sum != group.bargain
    assert close(row_sum, group.bargain)
    assert offer_totals(result).bargain == D(8000)


def test_offer_total_takes_only_comparable_groups_even_with_money_filled():
    lines, history = three_equal_lines()
    result = evaluate(lines, history)
    group = result.groups[0]
    # a group outside the comparable state must not reach the sums even if its money is filled
    mismatched = OfferEvaluation(
        lines={pid: replace(line, state=LineState.unit_mismatch) for pid, line in result.lines.items()},
        groups=(replace(group, state=LineState.unit_mismatch, below=D(-1)),),
    )
    assert group.bargain == D(20000)

    totals = offer_totals(mismatched)
    assert totals.bargain == D(0)
    assert totals.below == D(0)
    assert totals.below_lines == 0


def test_articles_split_one_group_and_sum_to_the_offer_within_tolerance():
    lines, history = three_equal_lines()
    result = evaluate(lines, history)

    first = lines_totals(result, [1, 2])
    second = lines_totals(result, [3])
    assert close(first.bargain, D(20000) * 2 / 3)
    assert close(second.bargain, D(20000) / 3)
    assert close(first.bargain + second.bargain, offer_totals(result).bargain)


def test_work_money_still_adds_per_line_next_to_a_system_group():
    history = index(
        [contract(1), contract(2)],
        [
            sline(1, V50, D(10000)),
            sline(2, V50, D(20000)),
            wline(1, OTHER_FAMILY, D(10)),
            wline(2, OTHER_FAMILY, D(20)),
        ],
    )
    work = KpLine(9, D(15), D(15), D(2), OTHER_FAMILY, False, False, False)
    result = evaluate([kp(1, D(15000), V50), work], history)

    # system group: (150 - 100) * 100; work: (15 - 10) * 2
    assert offer_totals(result).bargain == D(5010)
    # the lines keep the order of the offer, the system line before the work
    assert list(result.lines) == [1, 9]


# --- состояния системной строки -------------------------------------------------------------------


def test_family_with_every_total_unusable_is_no_price_without_groups(buildings):
    lines = [kp(1, D(0), V50), kp(2, NAN, V50), kp(3, None, V51)]
    result = evaluate(lines, buildings)

    assert result.groups == ()
    assert [result.lines[pid].state for pid in (1, 2, 3)] == [LineState.no_price] * 3
    assert all(result.lines[pid].group_index is None for pid in (1, 2, 3))


def test_no_area_makes_every_system_line_no_area_and_leaves_works_alone(buildings):
    work = KpLine(9, D(150), D(150), D(1), LineKey(5, 50, None), False, False, False)
    work_history = index(
        [contract(1), contract(2)],
        [wline(1, LineKey(5, 50, None), D(100)), wline(2, LineKey(5, 50, None), D(200))],
    )
    for area in (None, D(0), NAN, D(-100)):
        result = evaluate([kp(1, D(15000), V50), kp(2, D(14000), V51), work], buildings, area=area)
        assert result.groups == ()
        assert result.lines[1].state is LineState.no_area
        assert result.lines[2].state is LineState.no_area
        assert result.lines[9].state is LineState.no_history  # the history here holds systems only
    assert evaluate([work], work_history, area=None).lines[9].state is LineState.comparable


@pytest.mark.parametrize(
    ("line", "base", "area", "expected"),
    [
        (kp(1, D(100), V50, in_progress=True), None, None, LineState.in_progress),
        (kp(1, D(0), None), None, None, LineState.unknown_vat_base),
        (kp(1, D(0), None), D(0), None, LineState.no_price),
        (kp(1, D(100), None), D(0), None, LineState.no_area),
        (kp(1, D(100), None), D(0), AREA, LineState.no_variant),
    ],
)
def test_system_line_state_order_runs_top_down(line, base, area, expected, buildings):
    assert evaluate([line], buildings, base=base, area=area).lines[1].state is expected


# --- ручной выбор ступени ---------------------------------------------------------------------------


def test_choice_on_one_system_line_moves_the_whole_family_and_leaves_other_families_auto():
    history = index(
        [contract(1), contract(2)],
        [
            sline(1, V50, D(10000)),
            sline(1, V51, D(15000)),
            sline(2, V50, D(20000)),
            sline(1, OTHER_FAMILY, D(10000)),
            sline(2, OTHER_FAMILY, D(20000)),
        ],
    )
    lines = [kp(1, D(15000), V50), kp(2, D(14000), V51), kp(3, D(15000), OTHER_FAMILY)]
    result = evaluate(lines, history, choice=RungChoice(1, V_CLS))

    assert [(g.family_id, g.rung) for g in result.groups] == [(5, V_CLS), (5, V_CLS), (6, V_CLS)]
    assert [g.key for g in result.groups[:2]] == [50, 51]
    assert not result.lines[1].auto and not result.lines[2].auto
    assert result.lines[3].auto
    assert result.lines[2].group_per_sqm == D(140)
    # each variant is its own group: its own contracts, the whole group money, share 1
    first, second = result.lines[1], result.lines[2]
    assert [(c.contract_id, c.net) for c in first.contracts] == [(1, D(100)), (2, D(200))]
    assert [(c.contract_id, c.net) for c in second.contracts] == [(1, D(150))]
    assert (first.group_lines, second.group_lines) == (1, 1)
    assert (first.share, second.share) == (D(1), D(1))
    # 140 against ours 150 on an area of 100
    assert result.groups[1].to_ours == D(-1000)
    assert second.to_ours == D(-1000)
    assert first.to_ours == result.groups[0].to_ours == D(5000)


def test_choice_without_contracts_on_the_group_is_no_history_and_keeps_the_choice():
    history = index([contract(1, cls=OTHER_CLASS)], [sline(1, V50, D(10000))])
    result = evaluate([kp(1, D(15000), V50)], history, choice=RungChoice(1, V_CLS))

    line = result.lines[1]
    assert line.state is LineState.no_history
    assert line.rung == V_CLS
    assert not line.auto
    assert result.groups[0].state is LineState.no_history


def test_choice_outside_the_rungs_of_the_family_is_a_value_error():
    history = index([contract(1)], [sline(1, V50, D(10000))])
    with pytest.raises(ValueError):
        evaluate([kp(1, D(15000), V50)], history, choice=RungChoice(1, P_CLS))


def test_choice_on_a_work_line_does_not_touch_a_system_family(buildings):
    work = KpLine(9, D(1), D(1), D(1), LineKey(8, 80, None), False, False, False)
    result = evaluate([*two_variant_lines(), work], buildings, choice=RungChoice(9, F_ALL))

    assert result.groups[0].rung == F_CLS
    assert result.lines[1].auto and result.lines[2].auto


def test_group_order_follows_first_appearance_and_indexes_match():
    history = index([contract(1), contract(2)], [sline(1, OTHER_FAMILY, D(10000)), sline(2, OTHER_FAMILY, D(10000))])
    lines = [kp(1, D(100), OTHER_FAMILY), kp(2, D(100), V50)]
    result = evaluate(lines, history)

    assert [g.family_id for g in result.groups] == [6, 5]
    assert result.lines[1].group_index == 0
    assert result.lines[2].group_index == 1
    assert list(result.lines) == [1, 2]

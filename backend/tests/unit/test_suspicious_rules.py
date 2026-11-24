"""Ядро правил «подозрительных строк договоров» (спека 2026-11-24, §2 и §7 п. 1-4, 6)."""

from __future__ import annotations

from decimal import Decimal, Inexact, getcontext, localcontext

from services.analog_ladder import WorkHistoryLine
from services.suspicious_lines import (
    MAX_SPREAD,
    MIN_CONTRACTS,
    OUTLIER_RATIO,
    NeighborRow,
    OutlierBasis,
    OutlierFact,
    TwinBasis,
    TwinFact,
    basis_of,
    build_suspicion_index,
    quartiles,
    state_of,
)

D = Decimal


def line(
    pid: int,
    contract: int,
    price: str | None,
    *,
    family: int = 1,
    unit: int | None = 1,
    vat: str | None = "0",
    lump: bool = False,
    price_error: bool = False,
) -> WorkHistoryLine:
    return WorkHistoryLine(
        position_item_id=pid,
        contract_id=contract,
        family_id=family,
        chain_id=1,
        zone=None,
        chain_titles=frozenset(),
        variant_id=1,
        schema_id=None,
        main_values={},
        unit_price=None if price is None else D(price),
        weight=None,
        vat_base=None if vat is None else D(vat),
        unit_id=unit,
        lump=lump,
        price_error=price_error,
    )


def nrow(pid: int, place: int, *, unit: int | None = 1, cost: str | None = "10", estimate: int = 1) -> NeighborRow:
    return NeighborRow(
        position_item_id=pid,
        estimate_id=estimate,
        place=place,
        unit_id=unit,
        unit_cost_total=None if cost is None else D(cost),
    )


def group_lines(
    prices: list[str], *, family: int = 1, unit: int = 1, first_pid: int = 100, first_contract: int = 100
) -> list[WorkHistoryLine]:
    """По строке на договор: цены договоров заданы."""
    return [line(first_pid + i, first_contract + i, p, family=family, unit=unit) for i, p in enumerate(prices)]


def base_group(*, family: int = 1, unit: int = 1, first_pid: int = 100) -> list[WorkHistoryLine]:
    """Пять договоров по 100: обычная 100, Q1 = Q3 = 100, однородна."""
    return group_lines(["100"] * 5, family=family, unit=unit, first_pid=first_pid, first_contract=first_pid)


def fact_ids(index, pid: int) -> list[object]:
    return list(index.facts.get(pid, ()))


# --- константы ---


def test_constants() -> None:
    assert MIN_CONTRACTS == 3
    assert D(2) == MAX_SPREAD
    assert D(10) == OUTLIER_RATIO


# --- кандидаты ---


def test_non_candidates_are_dropped() -> None:
    lines = [
        line(1, 1, "500", lump=True),
        line(2, 2, "500", vat=None),
        line(3, 3, "500", unit=None),
        line(4, 4, None),
        line(5, 5, "0"),
        line(6, 6, "NaN"),
        line(7, 7, "0.99"),
        line(8, 8, "1"),
    ]
    index = build_suspicion_index(lines, [])
    assert set(index.group_of) == {8}
    assert set(index.net) == {8}
    assert index.net[8] == D(1)


def test_net_removes_vat() -> None:
    index = build_suspicion_index([line(1, 1, "120", vat="20")], [])
    assert index.net[1] == D(100)


def test_net_below_min_nominal_after_vat_is_dropped() -> None:
    # 1.1 с НДС 20 % -> нетто 0.9166... < 1
    index = build_suspicion_index([line(1, 1, "1.1", vat="20")], [])
    assert index.group_of == {}


def test_price_error_line_is_candidate_but_not_in_statistics() -> None:
    lines = [
        line(1, 1, "100"),
        line(2, 1, "1", price_error=True),
        line(3, 2, "100"),
        line(4, 3, "100"),
    ]
    index = build_suspicion_index(lines, [])
    assert 2 in index.group_of and 2 in index.net
    stats = index.groups[(1, 1)]
    first = stats.contracts[0]
    assert first.contract_id == 1
    assert first.lines == 1
    assert first.price == D(100)  # с отметкой было бы 50.5
    assert first.low == first.high == D(100)


def test_group_of_only_price_error_lines_exists_without_usual() -> None:
    index = build_suspicion_index([line(1, 1, "50", price_error=True)], [])
    stats = index.groups[(1, 1)]
    assert stats.contracts == ()
    assert stats.usual is None
    assert stats.homogeneous is False
    assert index.facts == {}


def test_price_error_contract_only_is_not_in_contracts() -> None:
    lines = group_lines(["100", "100", "100"]) + [line(1, 1, "5", price_error=True)]
    index = build_suspicion_index(lines, [])
    stats = index.groups[(1, 1)]
    assert [c.contract_id for c in stats.contracts] == [100, 101, 102]
    fact = index.facts[1][0]
    assert isinstance(fact, OutlierFact)
    assert fact.basis == OutlierBasis(1, 1, "down")
    assert fact.ratio == D("0.05")
    assert fact.usual == D(100)
    assert fact.net == D(5)


# --- квартили ---


def test_quartiles_inclusive_exact() -> None:
    assert quartiles([D(1), D(2), D(3)]) == (D("1.5"), D("2.5"))
    assert quartiles([D(10), D(20), D(30), D(40)]) == (D("17.5"), D("32.5"))
    q1, q3 = quartiles([D(10), D(20), D(30), D(40)])
    assert isinstance(q1, Decimal) and isinstance(q3, Decimal)


# --- голос договора («Лифты») ---


def lifts_lines() -> list[WorkHistoryLine]:
    lines = [line(i, 1, "100") for i in range(1, 36)]
    for k, price in enumerate(["300", "500", "700", "900", "1100"]):
        lines.append(line(100 + k, 2 + k, price))
    return lines


def test_lifts_one_contract_has_one_voice() -> None:
    lines = lifts_lines()
    index = build_suspicion_index(lines, [])
    stats = index.groups[(1, 1)]
    assert stats.usual == D(600)
    assert quartiles([c.price for c in stats.contracts]) == (D(350), D(850))
    assert stats.homogeneous is False
    assert index.facts == {}
    first = stats.contracts[0]
    assert (first.contract_id, first.lines, first.price) == (1, 35, D(100))


# --- границы однородности ---


def test_three_contracts_homogeneous_and_two_have_no_usual() -> None:
    three = build_suspicion_index(group_lines(["100", "150", "200"]), []).groups[(1, 1)]
    assert three.usual == D(150)
    assert three.homogeneous is True
    assert quartiles([c.price for c in three.contracts]) == (D(125), D(175))
    two = build_suspicion_index(group_lines(["100", "150"]), []).groups[(1, 1)]
    assert two.usual is None
    assert two.homogeneous is False


def test_spread_exactly_two_is_homogeneous_and_slightly_more_is_not() -> None:
    exact = build_suspicion_index(group_lines(["100", "100", "200", "200", "200"]), []).groups[(1, 1)]
    assert exact.homogeneous is True
    over = build_suspicion_index(group_lines(["100", "100", "200.01", "200.01", "200.01"]), []).groups[(1, 1)]
    assert over.usual == D("200.01")
    assert over.homogeneous is False


def test_contracts_sorted_by_price_then_id() -> None:
    lines = [line(1, 9, "200"), line(2, 5, "100"), line(3, 3, "200"), line(4, 7, "100")]
    stats = build_suspicion_index(lines, []).groups[(1, 1)]
    assert [(c.contract_id, c.price) for c in stats.contracts] == [
        (5, D(100)),
        (7, D(100)),
        (3, D(200)),
        (9, D(200)),
    ]


def test_contract_price_is_median_of_its_lines_with_low_and_high() -> None:
    lines = [line(1, 1, "10"), line(2, 1, "20"), line(3, 1, "60")]
    c = build_suspicion_index(lines, []).groups[(1, 1)].contracts[0]
    assert (c.lines, c.price, c.low, c.high) == (3, D(20), D(10), D(60))


def test_groups_split_by_family_and_unit() -> None:
    lines = [line(1, 1, "100"), line(2, 1, "100", family=2), line(3, 1, "100", unit=2)]
    index = build_suspicion_index(lines, [])
    assert set(index.groups) == {(1, 1), (2, 1), (1, 2)}
    assert index.group_of == {1: (1, 1), 2: (2, 1), 3: (1, 2)}


# --- границы выброса ---


def test_outlier_up_boundary_exactly_ten_and_below() -> None:
    at = build_suspicion_index(base_group() + [line(1, 1, "1000")], [])
    fact = at.facts[1][0]
    assert isinstance(fact, OutlierFact)
    assert fact.basis == OutlierBasis(1, 1, "up")
    assert (fact.ratio, fact.usual, fact.net) == (D(10), D(100), D(1000))
    assert 1 not in build_suspicion_index(base_group() + [line(1, 1, "999")], []).facts


def test_outlier_down_boundary_exactly_a_tenth_and_above() -> None:
    at = build_suspicion_index(base_group() + [line(1, 1, "10")], [])
    fact = at.facts[1][0]
    assert isinstance(fact, OutlierFact)
    assert fact.basis == OutlierBasis(1, 1, "down")
    assert fact.ratio == D("0.1")
    assert 1 not in build_suspicion_index(base_group() + [line(1, 1, "10.01")], []).facts


def test_no_outlier_in_non_homogeneous_group() -> None:
    lines = group_lines(["100", "100", "100", "500", "500"]) + [line(1, 1, "5000")]
    index = build_suspicion_index(lines, [])
    assert index.groups[(1, 1)].homogeneous is False
    assert index.facts == {}


def test_no_outlier_without_usual_price() -> None:
    index = build_suspicion_index(group_lines(["100", "100000"]), [])
    assert index.facts == {}


# --- двойник: пять случаев замера ---

TWIN_PAIR_NEIGHBORS = [nrow(10, 1, unit=1), nrow(11, 2, unit=2)]


def test_twin_neighbor_not_candidate_within_fold_two_no_facts() -> None:
    lines = base_group() + [line(10, 1, "150")]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {}


def test_twin_neighbor_not_candidate_far_price_suspects_the_candidate() -> None:
    lines = base_group() + [line(10, 1, "250")]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {10: (TwinFact(TwinBasis(11)),)}


def test_twin_price_error_line_alone_is_suspect() -> None:
    lines = (
        base_group()
        + base_group(family=2, unit=2, first_pid=200)
        + [line(10, 1, "300", price_error=True), line(11, 2, "900", family=2, unit=2)]
    )
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {10: (TwinFact(TwinBasis(11)),)}


def test_twin_price_error_with_non_candidate_neighbor() -> None:
    # цена отмеченной строки в пределах x2 от обычной: без приоритета price_error она не годна
    lines = base_group() + [line(10, 1, "150", price_error=True)]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {10: (TwinFact(TwinBasis(11)),)}


def test_twin_filter_fold_two_within_fold_row_is_not_suspect() -> None:
    # строка 10 без обычной цены годна всегда; строка 11 в пределах x2 - не годна.
    # Без фильтра x2 подозрительны были бы обе.
    lines = base_group(family=2, unit=2, first_pid=200) + [
        line(10, 1, "300", family=5),
        line(11, 2, "150", family=2, unit=2),
    ]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {10: (TwinFact(TwinBasis(11)),)}


def test_twin_both_without_usual_price_both_suspects() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {
        10: (TwinFact(TwinBasis(11)),),
        11: (TwinFact(TwinBasis(10)),),
    }


def test_twin_both_far_from_usual_the_farther_is_suspect() -> None:
    lines = (
        base_group()
        + base_group(family=2, unit=2, first_pid=200)
        + [line(10, 1, "300"), line(11, 2, "500", family=2, unit=2)]
    )
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {11: (TwinFact(TwinBasis(10)),)}


def test_twin_both_far_below_usual_the_farther_is_suspect() -> None:
    # 40 -> кратность 2.5, 25 -> кратность 4
    lines = (
        base_group()
        + base_group(family=2, unit=2, first_pid=200)
        + [line(10, 1, "40"), line(11, 2, "25", family=2, unit=2)]
    )
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert index.facts == {11: (TwinFact(TwinBasis(10)),)}


def test_twin_equal_fold_both_suspects() -> None:
    # 400 и 25: кратность 4 у обоих, в разные стороны
    lines = (
        base_group()
        + base_group(family=2, unit=2, first_pid=200)
        + [line(10, 1, "400"), line(11, 2, "25", family=2, unit=2)]
    )
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert set(index.facts) == {10, 11}


def test_twin_fold_exactly_two_is_eligible_and_just_under_is_not() -> None:
    at = build_suspicion_index(base_group() + [line(10, 1, "200")], TWIN_PAIR_NEIGHBORS)
    assert set(at.facts) == {10}
    below = build_suspicion_index(base_group() + [line(10, 1, "199.99")], TWIN_PAIR_NEIGHBORS)
    assert below.facts == {}


def test_twin_one_without_usual_other_with_usual_all_eligible() -> None:
    lines = base_group(family=2, unit=2, first_pid=200) + [
        line(10, 1, "300", family=5),
        line(11, 2, "500", family=2, unit=2),
    ]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    assert set(index.facts) == {10, 11}


def test_twin_deviation_uses_usual_of_non_homogeneous_group() -> None:
    # группа неоднородна (Q3/Q1 > 2), но обычная цена 250 есть; выброса нет, двойник по d работает
    lines = group_lines(["100", "100", "250", "500", "500"]) + [
        line(10, 1, "260", family=1),
    ]
    index = build_suspicion_index(lines, TWIN_PAIR_NEIGHBORS)
    stats = index.groups[(1, 1)]
    assert stats.usual is not None and stats.homogeneous is False
    assert index.facts == {}  # 260 вблизи обычной -> d < ln 2, сосед не кандидат


def test_twin_neighbor_four_places_away_is_not_a_pair() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    far = [nrow(10, 1, unit=1), nrow(11, 5, unit=2)]
    near = [nrow(10, 1, unit=1), nrow(11, 4, unit=2)]
    assert build_suspicion_index(lines, far).facts == {}
    assert set(build_suspicion_index(lines, near).facts) == {10, 11}


def test_twin_same_unit_is_not_a_pair() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    neighbors = [nrow(10, 1, unit=3), nrow(11, 2, unit=3)]
    assert build_suspicion_index(lines, neighbors).facts == {}


def test_twin_unknown_unit_is_not_a_pair() -> None:
    lines = [line(10, 1, "300", family=5)]
    neighbors = [nrow(10, 1, unit=3), nrow(11, 2, unit=None)]
    assert build_suspicion_index(lines, neighbors).facts == {}


def test_twin_costs_must_be_equal_and_usable() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    diff = [nrow(10, 1, unit=1, cost="10"), nrow(11, 2, unit=2, cost="10.01")]
    assert build_suspicion_index(lines, diff).facts == {}
    for bad in (None, "0", "-5", "NaN", "Infinity"):
        pair = [nrow(10, 1, unit=1, cost=bad), nrow(11, 2, unit=2, cost=bad)]
        assert build_suspicion_index(lines, pair).facts == {}, bad


def test_twin_chapter_between_does_not_take_a_place() -> None:
    # ids 10 и 90 разделены другими id (глав в `neighbors` нет), места подряд — пара
    lines = [line(10, 1, "300", family=5)]
    neighbors = [nrow(10, 1, unit=1), nrow(90, 2, unit=2)]
    assert set(build_suspicion_index(lines, neighbors).facts) == {10}
    # места 1 и 4 — три шага: пара; места 1 и 5 — уже нет (строки между — настоящие)
    assert set(build_suspicion_index(lines, [nrow(10, 1, unit=1), nrow(90, 4, unit=2)]).facts) == {10}


def test_twin_pairs_are_scoped_to_one_estimate() -> None:
    lines = [line(10, 1, "300", family=5)]
    neighbors = [nrow(10, 1, unit=1, estimate=1), nrow(11, 2, unit=2, estimate=2)]
    assert build_suspicion_index(lines, neighbors).facts == {}


def test_twin_candidate_without_own_row_is_in_no_pair() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    index = build_suspicion_index(lines, [nrow(11, 2, unit=2)])
    assert index.facts == {}


def test_twin_row_in_two_pairs_gets_two_facts_sorted_by_neighbor() -> None:
    lines = base_group() + [line(10, 1, "250")]
    neighbors = [nrow(50, 1, unit=2), nrow(10, 2, unit=1), nrow(40, 3, unit=3)]
    index = build_suspicion_index(lines, neighbors)
    assert index.facts == {10: (TwinFact(TwinBasis(40)), TwinFact(TwinBasis(50)))}


def test_pair_is_evaluated_once_per_unordered_pair() -> None:
    lines = [line(10, 1, "300", family=5), line(11, 2, "300", family=6)]
    index = build_suspicion_index(lines, [nrow(11, 1, unit=2), nrow(10, 2, unit=1)])
    assert index.facts[10] == (TwinFact(TwinBasis(11)),)
    assert index.facts[11] == (TwinFact(TwinBasis(10)),)


def test_facts_order_outlier_then_twins() -> None:
    lines = base_group() + [line(10, 1, "1000")]
    neighbors = [nrow(10, 1, unit=1), nrow(11, 2, unit=2)]
    facts = build_suspicion_index(lines, neighbors).facts[10]
    assert isinstance(facts[0], OutlierFact)
    assert facts[1] == TwinFact(TwinBasis(11))


def test_non_candidate_neighbor_is_never_suspect() -> None:
    index = build_suspicion_index([line(10, 1, "300", family=5)], TWIN_PAIR_NEIGHBORS)
    assert 11 not in index.facts


# --- строки по смете ---


def test_rows_by_estimate_sorted_by_place() -> None:
    neighbors = [
        nrow(3, 3, estimate=1),
        nrow(1, 1, estimate=1),
        nrow(7, 2, estimate=2),
        nrow(2, 2, estimate=1),
    ]
    index = build_suspicion_index([], neighbors)
    assert [r.position_item_id for r in index.rows[1]] == [1, 2, 3]
    assert [r.position_item_id for r in index.rows[2]] == [7]


# --- основание и состояние ---


def test_basis_of() -> None:
    ob = OutlierBasis(1, 2, "up")
    assert basis_of(OutlierFact(ob, D(10), D(1), D(10))) == ob
    assert basis_of(TwinFact(TwinBasis(5))) == TwinBasis(5)


def test_state_price_error_is_resolved_with_any_facts() -> None:
    fact = TwinFact(TwinBasis(5))
    assert state_of([], set(), True) == "resolved"
    assert state_of([fact], set(), True) == "resolved"


def test_state_fact_outside_decided_is_unresolved() -> None:
    fact = OutlierFact(OutlierBasis(1, 1, "up"), D(10), D(1), D(10))
    assert state_of([fact], set(), False) == "unresolved"
    assert state_of([fact], {OutlierBasis(1, 1, "down")}, False) == "unresolved"
    assert state_of([fact], {OutlierBasis(2, 1, "up")}, False) == "unresolved"
    assert state_of([fact], {TwinBasis(9)}, False) == "unresolved"


def test_state_one_unseen_basis_among_decided_is_unresolved() -> None:
    f1 = TwinFact(TwinBasis(1))
    f2 = TwinFact(TwinBasis(2))
    assert state_of([f1, f2], {TwinBasis(1)}, False) == "unresolved"


def test_state_all_bases_decided_is_resolved() -> None:
    f1 = OutlierFact(OutlierBasis(1, 1, "up"), D(10), D(1), D(10))
    f2 = TwinFact(TwinBasis(2))
    assert state_of([f1, f2], {basis_of(f1), basis_of(f2)}, False) == "resolved"


def test_state_decision_without_facts_is_resolved() -> None:
    assert state_of([], {TwinBasis(1)}, False) == "resolved"


def test_state_nothing_is_none() -> None:
    assert state_of([], set(), False) is None


def test_price_error_row_stays_resolved_when_rule_is_silent() -> None:
    # три договора 10, 100, 100, дешёвая строка с отметкой: правило молчит, состояние resolved
    lines = [line(1, 1, "10", price_error=True), line(2, 2, "100"), line(3, 3, "100")]
    index = build_suspicion_index(lines, [])
    assert index.groups[(1, 1)].usual is None
    assert index.facts.get(1, ()) == ()
    assert state_of(index.facts.get(1, ()), set(), True) == "resolved"


# --- границы при ненулевом НДС (точность рациональной арифметики) ---


def vat_group(prices: list[str], vat: str, *, first: int = 100) -> list[WorkHistoryLine]:
    return [line(first + i, first + i, p, vat=vat) for i, p in enumerate(prices)]


VATS = ["20", "22"]


def test_outlier_up_boundary_with_vat() -> None:
    for vat in VATS:
        base = vat_group(["200"] * 5, vat)
        at = build_suspicion_index(base + [line(1, 1, "2000", vat=vat)], [])
        fact = at.facts[1][0]
        assert isinstance(fact, OutlierFact)
        assert fact.basis.direction == "up"
        assert fact.ratio == D(10)
        assert 1 not in build_suspicion_index(base + [line(1, 1, "1999.99", vat=vat)], []).facts


def test_outlier_down_boundary_with_vat() -> None:
    for vat in VATS:
        base = vat_group(["200"] * 5, vat)
        at = build_suspicion_index(base + [line(1, 1, "20", vat=vat)], [])
        fact = at.facts[1][0]
        assert isinstance(fact, OutlierFact)
        assert fact.basis.direction == "down"
        assert fact.ratio == D("0.1")
        assert 1 not in build_suspicion_index(base + [line(1, 1, "20.01", vat=vat)], []).facts


def test_outlier_fact_net_is_gross_to_net() -> None:
    from money.vat import gross_to_net

    base = vat_group(["200"] * 5, "20")
    index = build_suspicion_index(base + [line(1, 1, "2000", vat="20")], [])
    fact = index.facts[1][0]
    assert isinstance(fact, OutlierFact)
    assert fact.net == gross_to_net(D(2000), D(20)) == index.net[1]


def test_spread_boundary_with_vat() -> None:
    for vat in VATS:
        exact = build_suspicion_index(vat_group(["100", "100", "200", "200", "200"], vat), [])
        assert exact.groups[(1, 1)].homogeneous is True
        over = build_suspicion_index(vat_group(["100", "100", "200.01", "200.01", "200.01"], vat), [])
        assert over.groups[(1, 1)].homogeneous is False


def test_twin_fold_boundary_up_with_vat() -> None:
    for vat in VATS:
        base = vat_group(["200"] * 5, vat)
        at = build_suspicion_index(base + [line(10, 1, "400", vat=vat)], TWIN_PAIR_NEIGHBORS)
        assert set(at.facts) == {10}
        below = build_suspicion_index(base + [line(10, 1, "399.99", vat=vat)], TWIN_PAIR_NEIGHBORS)
        assert below.facts == {}


def test_twin_fold_boundary_down_with_vat() -> None:
    for vat in VATS:
        base = vat_group(["200"] * 5, vat)
        at = build_suspicion_index(base + [line(10, 1, "100", vat=vat)], TWIN_PAIR_NEIGHBORS)
        assert set(at.facts) == {10}
        above = build_suspicion_index(base + [line(10, 1, "100.01", vat=vat)], TWIN_PAIR_NEIGHBORS)
        assert above.facts == {}


def test_min_nominal_boundary_is_exact_with_vat() -> None:
    assert 1 in build_suspicion_index([line(1, 1, "1.2", vat="20")], []).group_of
    assert 1 not in build_suspicion_index([line(1, 1, "1.19", vat="20")], []).group_of


# --- явный контекст ---


def test_global_decimal_context_is_not_touched_and_inexact_trap_is_harmless() -> None:
    lines = vat_group(["100", "100", "200", "200", "200"], "20") + [line(1, 1, "2000", vat="20")]
    neighbors = [nrow(1, 1, unit=1), nrow(2, 2, unit=2)]
    with localcontext() as ctx:
        ctx.traps[Inexact] = True
        before = (getcontext().prec, getcontext().traps.copy(), getcontext().rounding)
        build_suspicion_index(lines, neighbors)
        after = (getcontext().prec, getcontext().traps.copy(), getcontext().rounding)
    assert before == after

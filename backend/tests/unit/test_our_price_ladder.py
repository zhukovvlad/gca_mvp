"""Ядро «Нашей цены»: ключи, индекс истории, лестница, состояния работ, суммы.

Чистый расчёт без базы (спека 4а §2.3, §2.5, §2.7, §2.8, §2.18). Эталоны —
числа, посчитанные руками; база НДС 0 там, где нетто должно совпасть с
валовым, и 12 там, где проверяется пересчёт.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from services.our_price import (
    MISMATCH_HIGH,
    MISMATCH_LOW,
    RUNG_ORDER,
    RUNG_THRESHOLD,
    ContractRate,
    HistoryContract,
    HistoryIndex,
    HistoryLine,
    KpLine,
    LineKey,
    LineResult,
    LineState,
    OfferEvaluation,
    Rung,
    RungChoice,
    RungCount,
    StateBucket,
    auto_rung,
    available_rungs,
    evaluate_offer,
    lines_totals,
    offer_totals,
    rate_range,
    unit_mismatch,
)

D = Decimal
NAN = D("NaN")

V_CLS = Rung("v", "cls")
V_ALL = Rung("v", "all")
P_CLS = Rung("p", "cls")
P_ALL = Rung("p", "all")
F_CLS = Rung("f", "cls")
F_ALL = Rung("f", "all")

TENDER_CLASS = 1
OTHER_CLASS = 2

KEY = LineKey(family_id=5, variant_id=50, first_main=(7, 70))
KEY_NO_P = LineKey(family_id=5, variant_id=50, first_main=None)


def contract(cid: int, *, cls: int = TENDER_CLASS, area: Decimal | None = None) -> HistoryContract:
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 100,
        number=f"Договор {cid}",
        object_title="Объект Т",
        year=2025,
        rate_class_id=cls,
        rate_class_title="Класс",
        contractor_id=cid,
        area=area,
    )


def hline(
    cid: int,
    price: Decimal | None,
    *,
    key: LineKey = KEY,
    weight: Decimal | None = D(1),
    base: Decimal | None = D(0),
    total: Decimal | None = None,
    is_system: bool = False,
) -> HistoryLine:
    return HistoryLine(
        contract_id=cid,
        key=key,
        is_system=is_system,
        unit_price=price,
        total=total,
        weight=weight,
        vat_base=base,
    )


def history(
    rates_by_contract: dict[int, Decimal],
    *,
    classes: dict[int, int] | None = None,
    key: LineKey = KEY,
) -> HistoryIndex:
    """Индекс, где у каждого договора одна строка работы по ключу."""
    classes = classes or {}
    contracts = [contract(cid, cls=classes.get(cid, TENDER_CLASS)) for cid in rates_by_contract]
    lines = [hline(cid, price, key=key) for cid, price in rates_by_contract.items()]
    return HistoryIndex(contracts, lines, factor_by_estimate=None)


def kp(
    pid: int = 1,
    price: Decimal | None = D(150),
    *,
    weight: Decimal | None = D(1),
    total: Decimal | None | str = "auto",
    key: LineKey | None = KEY,
    is_system: bool = False,
    in_progress: bool = False,
    waiting_admin: bool = False,
) -> KpLine:
    if total == "auto":
        total = price * weight if price is not None and weight is not None else None
    return KpLine(
        position_item_id=pid,
        unit_price=price,
        total=total,
        weight=weight,
        key=key,
        is_system=is_system,
        in_progress=in_progress,
        waiting_admin=waiting_admin,
    )


def evaluate(
    lines: list[KpLine],
    index: HistoryIndex,
    *,
    base: Decimal | None = D(0),
    choice: RungChoice | None = None,
) -> OfferEvaluation:
    return evaluate_offer(
        lines,
        index,
        kp_vat_base=base,
        tender_area=D(100),
        tender_class_id=TENDER_CLASS,
        choice=choice,
    )


def one(lines: list[KpLine], index: HistoryIndex, **kw) -> LineResult:
    return evaluate(lines, index, **kw).lines[lines[0].position_item_id]


# --- константы, ключи, ступени -------------------------------------------------


def test_constants_are_the_spec_values():
    assert RUNG_THRESHOLD == 2
    assert D(5) == MISMATCH_HIGH
    assert D("0.2") == MISMATCH_LOW
    assert RUNG_ORDER == (V_CLS, V_ALL, P_CLS, P_ALL, F_CLS, F_ALL)


def test_line_state_declaration_order_is_the_check_order():
    assert [state.value for state in LineState] == [
        "in_progress",
        "unknown_vat_base",
        "no_price",
        "no_area",
        "no_variant",
        "no_history",
        "unit_mismatch",
        "comparable",
    ]


def test_line_key_at_each_level():
    assert KEY.at("v") == 50
    assert KEY.at("p") == (5, (7, 70))
    assert KEY.at("f") == 5


def test_line_key_without_first_main_has_no_p_level():
    assert KEY_NO_P.at("p") is None
    assert KEY_NO_P.at("v") == 50
    assert KEY_NO_P.at("f") == 5


def test_available_rungs_six_with_first_main():
    assert available_rungs(KEY) == RUNG_ORDER


def test_available_rungs_four_without_first_main():
    assert available_rungs(KEY_NO_P) == (V_CLS, V_ALL, F_CLS, F_ALL)


# --- авто-ступень --------------------------------------------------------------


def _counts(**by_rung: int) -> list[RungCount]:
    names = {
        "v_cls": V_CLS,
        "v_all": V_ALL,
        "p_cls": P_CLS,
        "p_all": P_ALL,
        "f_cls": F_CLS,
        "f_all": F_ALL,
    }
    return [RungCount(rung, by_rung.get(name, 0)) for name, rung in names.items()]


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (_counts(v_cls=2), V_CLS),
        (_counts(v_cls=5, v_all=9), V_CLS),
        (_counts(v_cls=1, v_all=2), V_ALL),
        # класс ослабляется раньше параметров
        (_counts(v_cls=1, v_all=2, p_cls=5), V_ALL),
        (_counts(v_cls=1, v_all=1, p_cls=1), V_CLS),
        (_counts(v_cls=0, v_all=1, p_cls=1), V_ALL),
        (_counts(v_cls=0, v_all=0, p_cls=1, p_all=1), P_CLS),
        (_counts(f_all=1), F_ALL),
        (_counts(v_cls=1, f_all=2), F_ALL),
        (_counts(), None),
    ],
)
def test_auto_rung_table(counts, expected):
    assert auto_rung(counts) == expected


def test_auto_rung_without_p_rungs_skips_them():
    counts = [RungCount(V_CLS, 1), RungCount(V_ALL, 1), RungCount(F_CLS, 2), RungCount(F_ALL, 3)]
    assert auto_rung(counts) == F_CLS


# --- вилка и медиана -----------------------------------------------------------


def _rates(*values: str) -> list[ContractRate]:
    return [ContractRate(i + 1, D(value)) for i, value in enumerate(values)]


def test_rate_range_odd_count_median_is_the_middle():
    result = rate_range(_rates("300", "100", "200"))
    assert (result.min, result.max, result.median) == (D(100), D(300), D(200))


def test_rate_range_even_count_median_is_mean_of_two_middle():
    result = rate_range(_rates("100", "400", "300", "200"))
    assert (result.min, result.max, result.median) == (D(100), D(400), D(250))


def test_rate_range_single_rate():
    result = rate_range(_rates("42"))
    assert (result.min, result.max, result.median) == (D(42), D(42), D(42))


# --- расхождение единиц --------------------------------------------------------


def test_unit_mismatch_kp_ratio_exactly_five_is_not_a_mismatch():
    assert unit_mismatch(D(500), _rates("100")) is None


def test_unit_mismatch_kp_ratio_just_above_five():
    result = unit_mismatch(D("500.0001"), _rates("100"))
    assert result is not None
    assert result.kp_ratio == D("500.0001") / D(100)
    assert result.contracts == ()


def test_unit_mismatch_kp_ratio_exactly_point_two_is_not_a_mismatch():
    assert unit_mismatch(D(20), _rates("100")) is None


def test_unit_mismatch_kp_ratio_just_below_point_two():
    result = unit_mismatch(D("19.99"), _rates("100"))
    assert result is not None
    assert result.kp_ratio == D("0.1999")


def test_unit_mismatch_uses_even_median_not_one_of_the_middle_two():
    rates = _rates("100", "200", "300", "400")  # медиана 250
    assert unit_mismatch(D(1250), rates) is None  # ровно 5 * 250
    assert unit_mismatch(D("1250.01"), rates) is not None


def test_unit_mismatch_contract_against_median_of_the_others():
    rates = _rates("100", "100", "100", "1000")
    result = unit_mismatch(D(100), rates)
    assert result is not None
    assert result.kp_ratio is None
    assert result.contracts == ((4, D(10)),)


def test_unit_mismatch_contract_ratio_exactly_five_and_point_two_are_not_flagged():
    assert unit_mismatch(D(300), _rates("500", "100")) is None


def test_unit_mismatch_contract_ratio_just_above_five_flags_both_sides():
    result = unit_mismatch(D(300), _rates("500.01", "100"))
    assert result is not None
    assert {cid for cid, _ in result.contracts} == {1, 2}


def test_unit_mismatch_single_contract_skips_the_contract_check():
    assert unit_mismatch(D(100), _rates("100")) is None


def test_unit_mismatch_tiny_contract_rate_next_to_hundreds():
    result = unit_mismatch(D(100), _rates("100", "150", "0.0000001"))
    assert result is not None
    flagged = dict(result.contracts)
    assert 3 in flagged
    assert flagged[3] < D("0.2")


# --- индекс истории ------------------------------------------------------------


def test_rates_weighted_average_net_by_weight():
    lines = [
        hline(1, D(100), weight=D(1)),
        hline(1, D(200), weight=D(3)),
    ]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate == ContractRate(1, D(175))  # (100*1 + 200*3) / 4


def test_rates_net_of_line_vat_base():
    index = HistoryIndex([contract(1)], [hline(1, D(112), base=D(12))], factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate.net == D(100)


def test_rates_line_without_vat_base_is_left_out_of_the_average():
    lines = [hline(1, D(100)), hline(1, D(900), base=None)]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D(100))]
    assert index.vat_dropped("v", 50, is_system=False) == []


def test_rates_contract_with_all_lines_dropped_is_in_vat_dropped_only():
    lines = [hline(1, D(100), base=None), hline(1, D(200), base=None), hline(2, D(300))]
    index = HistoryIndex([contract(1), contract(2)], lines, factor_by_estimate=None)
    assert [r.contract_id for r in index.rates("v", 50, is_system=False, class_id=None)] == [2]
    assert index.vat_dropped("v", 50, is_system=False) == [1]


@pytest.mark.parametrize(
    ("price", "weight"),
    [(D(0), D(1)), (NAN, D(1)), (None, D(1)), (D(100), D(0)), (D(100), None), (D(-5), D(1))],
)
def test_rates_line_failing_price_or_weight_predicate_does_not_count(price, weight):
    index = HistoryIndex([contract(1)], [hline(1, price, weight=weight)], factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == []
    assert index.vat_dropped("v", 50, is_system=False) == []


def test_rates_unusable_line_does_not_drag_the_average():
    lines = [hline(1, D(100), weight=D(1)), hline(1, D(0), weight=D(1000))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D(100))]


def test_rates_negative_weight_line_does_not_count():
    # вес -1 прошёл бы проверку «не пусто» и стянул бы среднее к (300 - 200) / 2 = 50
    lines = [hline(1, D(100), weight=D(3)), hline(1, D(200), weight=D(-1))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D(100))]


def test_rates_line_of_a_contract_missing_from_the_list_does_not_count():
    lines = [hline(1, D(100)), hline(2, D(900)), hline(3, D(500), base=None)]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D(100))]
    assert index.vat_dropped("v", 50, is_system=False) == []


def test_rates_penny_rate_is_kept():
    index = HistoryIndex([contract(1)], [hline(1, D("0.0000001"))], factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D("0.0000001"))]


def test_rates_rate_that_adjusts_to_zero_is_not_a_rate():
    index = HistoryIndex([contract(1)], [hline(1, D(100))], factor_by_estimate={100: D(0)})
    assert index.rates("v", 50, is_system=False, class_id=None) == []


def test_rates_rate_that_adjusts_to_nan_is_not_a_rate_and_not_counted_on_the_rung():
    lines = [hline(1, D(100)), hline(2, D(120))]
    index = HistoryIndex([contract(1), contract(2)], lines, factor_by_estimate={100: NAN, 200: D(1)})
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(2, D(120))]
    result = one([kp(price=D(130))], index)
    assert result.rungs[0] == RungCount(V_CLS, 1)
    assert result.threshold_met is False


def test_rates_adjustment_does_not_round_the_nominal_rate_first():
    # номинал (100 + 100.01) / 2 = 100.005; округление до копеек перед приведением дало бы 200.00 или 200.02
    lines = [hline(1, D(100)), hline(1, D("100.01"))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate={100: D(2)})
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate.net == D("200.01")


def test_rates_each_contract_is_adjusted_by_its_own_estimate_factor():
    lines = [hline(1, D(100)), hline(2, D(100))]
    index = HistoryIndex([contract(1), contract(2)], lines, factor_by_estimate={100: D(2), 200: D(3)})
    assert index.rates("v", 50, is_system=False, class_id=None) == [ContractRate(1, D(200)), ContractRate(2, D(300))]


def test_rates_adjustment_is_nominal_times_factor_without_rounding():
    index = HistoryIndex([contract(1)], [hline(1, D(100))], factor_by_estimate={100: D("1.0333333333")})
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate.net == D("103.33333333")


def test_rates_without_adjustment_plan_are_nominal():
    index = HistoryIndex([contract(1)], [hline(1, D(100))], factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate.net == D(100)


def test_rates_class_filter_and_all_classes():
    index = history({1: D(100), 2: D(200), 3: D(300)}, classes={3: OTHER_CLASS})
    cls = index.rates("v", 50, is_system=False, class_id=TENDER_CLASS)
    assert [r.contract_id for r in cls] == [1, 2]
    all_ = index.rates("v", 50, is_system=False, class_id=None)
    assert [r.contract_id for r in all_] == [1, 2, 3]


def test_rates_levels_are_keyed_independently():
    lines = [
        hline(1, D(100), key=LineKey(5, 50, (7, 70))),
        hline(2, D(200), key=LineKey(5, 51, (7, 70))),
        hline(3, D(300), key=LineKey(5, 52, (7, 71))),
    ]
    index = HistoryIndex([contract(1), contract(2), contract(3)], lines, factor_by_estimate=None)
    ids = lambda level, key: sorted(  # noqa: E731
        r.contract_id for r in index.rates(level, key, is_system=False, class_id=None)
    )
    assert ids("v", 50) == [1]
    assert ids("p", (5, (7, 70))) == [1, 2]
    assert ids("p", (5, (7, 71))) == [3]
    assert ids("f", 5) == [1, 2, 3]


def test_rates_history_line_without_first_main_is_not_on_the_p_rung():
    lines = [
        hline(1, D(100), key=KEY_NO_P),
        hline(2, D(200), key=LineKey(5, 51, (7, 70))),
    ]
    index = HistoryIndex([contract(1), contract(2)], lines, factor_by_estimate=None)
    ids = [r.contract_id for r in index.rates("p", (5, (7, 70)), is_system=False, class_id=None)]
    assert ids == [2]
    assert [r.contract_id for r in index.rates("f", 5, is_system=False, class_id=None)] == [1, 2]
    assert index.rates("p", None, is_system=False, class_id=None) == []


def test_rates_work_and_system_cuts_do_not_mix_on_a_shared_key():
    lines = [
        hline(1, D(100), is_system=False),
        hline(2, D(7), is_system=True, total=D(5000), weight=None),
    ]
    index = HistoryIndex([contract(1), contract(2, area=D(50))], lines, factor_by_estimate=None)
    works = index.rates("v", 50, is_system=False, class_id=None)
    systems = index.rates("v", 50, is_system=True, class_id=None)
    assert works == [ContractRate(1, D(100))]
    assert systems == [ContractRate(2, D(100))]  # 5000 / 50


def test_rates_system_rate_is_net_total_over_contract_area():
    lines = [
        hline(1, None, total=D(1120), base=D(12), weight=None, is_system=True),
        hline(1, None, total=D(2240), base=D(12), weight=None, is_system=True),
    ]
    index = HistoryIndex([contract(1, area=D(30))], lines, factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=True, class_id=None)
    assert rate.net == D(100)  # (1000 + 2000) / 30


def test_rates_system_contract_without_area_is_out_and_not_vat_dropped():
    lines = [
        hline(1, None, total=D(1000), weight=None, is_system=True),
        hline(2, None, total=D(1000), base=None, weight=None, is_system=True),
    ]
    index = HistoryIndex([contract(1, area=None), contract(2, area=None)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=True, class_id=None) == []
    assert index.vat_dropped("v", 50, is_system=True) == []


def test_rates_system_line_without_base_drops_the_contract_with_area():
    lines = [hline(1, None, total=D(1000), base=None, weight=None, is_system=True)]
    index = HistoryIndex([contract(1, area=D(10))], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=True, class_id=None) == []
    assert index.vat_dropped("v", 50, is_system=True) == [1]
    # тот же ключ в разрезе работ чист
    assert index.vat_dropped("v", 50, is_system=False) == []


def test_rates_system_line_with_unusable_total_does_not_count():
    lines = [hline(1, None, total=D(0), weight=None, is_system=True)]
    index = HistoryIndex([contract(1, area=D(10))], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=True, class_id=None) == []
    assert index.vat_dropped("v", 50, is_system=True) == []


def test_contract_lookup_returns_the_contract():
    index = history({1: D(100)})
    assert index.contract(1).contract_id == 1
    assert index.contract(1).number == "Договор 1"


# --- состояния строки КП -------------------------------------------------------


EMPTY = HistoryIndex([], [], factor_by_estimate=None)


def test_state_in_progress_beats_no_variant():
    result = one([kp(key=None, in_progress=True)], EMPTY)
    assert result.state is LineState.in_progress


def test_state_in_progress_beats_unknown_vat_base():
    result = one([kp(in_progress=True)], EMPTY, base=None)
    assert result.state is LineState.in_progress


def test_state_unknown_vat_base_for_all_but_in_progress_lines():
    index = history({1: D(100), 2: D(100)})
    lines = [kp(1, price=D(0)), kp(2, key=None), kp(3), kp(4, in_progress=True)]
    evaluation = evaluate(lines, index, base=None)
    states = {pid: line.state for pid, line in evaluation.lines.items()}
    assert states == {
        1: LineState.unknown_vat_base,
        2: LineState.unknown_vat_base,
        3: LineState.unknown_vat_base,
        4: LineState.in_progress,
    }
    assert evaluation.lines[3].kp_unit_net is None
    assert evaluation.lines[3].kp_total_net is None


@pytest.mark.parametrize(
    ("price", "weight"),
    [(D(0), D(1)), (NAN, D(1)), (None, D(1)), (D(100), D(0)), (D(100), None), (D(-1), D(1))],
)
def test_state_no_price_on_price_or_weight_failing_the_predicate(price, weight):
    index = history({1: D(100), 2: D(100)})
    result = one([kp(price=price, weight=weight, total=D(10))], index)
    assert result.state is LineState.no_price
    assert result.range is None and result.ours is None and result.position is None


def test_state_no_price_beats_no_variant():
    result = one([kp(price=D(0), key=None)], EMPTY)
    assert result.state is LineState.no_price


def test_state_no_variant_without_key():
    result = one([kp(key=None)], EMPTY)
    assert result.state is LineState.no_variant
    assert result.rungs == ()
    assert result.rung is None


def test_state_no_variant_beats_no_history():
    # ключа нет, но истории по чужому ключу тоже нет: причина — отсутствие варианта
    result = one([kp(key=None)], history({1: D(100)}))
    assert result.state is LineState.no_variant


def test_state_no_history_when_no_rung_has_contracts():
    result = one([kp()], EMPTY)
    assert result.state is LineState.no_history
    assert result.rung is None
    assert result.range is None and result.to_ours is None
    assert [count.contracts for count in result.rungs] == [0, 0, 0, 0, 0, 0]
    assert [count.rung for count in result.rungs] == list(RUNG_ORDER)


def test_state_no_history_without_p_has_four_rungs():
    result = one([kp(key=KEY_NO_P)], EMPTY)
    assert [count.rung for count in result.rungs] == [V_CLS, V_ALL, F_CLS, F_ALL]


def test_net_of_kp_uses_the_kp_vat_base_and_gross_weight():
    index = history({1: D(100), 2: D(100)})
    result = one([kp(price=D(112), weight=D(3), total=D(336))], index, base=D(12))
    assert result.kp_unit_net == D(100)
    assert result.kp_total_net == D(300)


def test_kp_total_net_is_none_for_non_finite_total():
    index = history({1: D(100), 2: D(100)})
    result = one([kp(price=D(100), total=NAN)], index)
    assert result.kp_unit_net == D(100)
    assert result.kp_total_net is None


# --- сравнение: comparable -----------------------------------------------------


def test_comparable_basic_fields():
    index = history({1: D(100), 2: D(200)})
    result = one([kp(price=D(150), weight=D(4))], index)
    assert result.state is LineState.comparable
    assert result.rung == V_CLS and result.auto is True and result.threshold_met is True
    assert result.range is not None
    assert (result.range.min, result.range.max, result.range.median) == (D(100), D(200), D(150))
    assert result.position == "in"
    assert result.ours == D(100)
    assert result.ours_from_all_classes is False
    assert result.to_ours == D(200)  # (150 - 100) * 4
    assert result.bargain == D(200)
    assert result.below is None
    assert result.mismatch is None
    assert [c.contract_id for c in result.contracts] == [1, 2]
    assert result.group_index is None and result.share is None


def test_comparable_counterexample_negative_to_ours_inside_the_range():
    # вилка 100-200, наша расценка класса 200, КП 150: разница отрицательна, торга нет
    index = history({1: D(100), 2: D(200)}, classes={1: OTHER_CLASS})
    result = one([kp(price=D(150), weight=D(1))], index, choice=RungChoice(1, V_ALL))
    assert result.ours == D(200)
    assert result.ours_from_all_classes is False
    assert result.to_ours == D(-50)
    assert result.bargain == D(0)
    assert result.below is None
    assert result.position == "in"


def test_comparable_above_the_range_has_positive_bargain():
    index = history({1: D(100), 2: D(200)})
    result = one([kp(price=D(250))], index)
    assert result.position == "above"
    assert result.to_ours == D(150)
    assert result.bargain == D(150)
    assert result.below is None


def test_comparable_below_the_minimum_has_signed_below():
    index = history({1: D(100), 2: D(200)})
    result = one([kp(price=D(90), weight=D(3))], index)
    assert result.position == "below"
    assert result.to_ours == D(-30)
    assert result.bargain == D(0)
    assert result.below == D(-30)  # (90 - 100) * 3


def test_comparable_below_is_measured_from_the_rung_minimum_not_from_ours():
    # минимум ступени 100 (чужой класс), наша расценка 200 (класс тендера)
    index = history({1: D(100), 2: D(200)}, classes={1: OTHER_CLASS})
    result = one([kp(price=D(90), weight=D(2))], index, choice=RungChoice(1, V_ALL))
    assert result.ours == D(200)
    assert result.position == "below"
    assert result.below == D(-20)  # (90 - 100) * 2
    assert result.to_ours == D(-220)  # (90 - 200) * 2
    assert result.bargain == D(0)


def test_comparable_range_borders_are_inside():
    index = history({1: D(100), 2: D(200)})
    at_min = one([kp(price=D(100))], index)
    at_max = one([kp(price=D(200))], index)
    assert at_min.position == "in" and at_min.below is None
    assert at_max.position == "in"
    assert at_max.to_ours == D(100)


def test_comparable_just_outside_the_range_borders():
    index = history({1: D(100), 2: D(200)})
    assert one([kp(price=D("99.99"))], index).position == "below"
    assert one([kp(price=D("200.01"))], index).position == "above"


def test_comparable_kp_price_equal_to_ours_has_zero_to_ours():
    index = history({1: D(100), 2: D(200)})
    result = one([kp(price=D(100))], index)
    assert result.to_ours == D(0)
    assert result.bargain == D(0)


def test_comparable_net_axis_for_position():
    index = history({1: D(100), 2: D(200)})
    # 224 с НДС 12 = 200 нетто: ровно максимум
    assert one([kp(price=D(224))], index, base=D(12)).position == "in"
    assert one([kp(price=D(225))], index, base=D(12)).position == "above"


def test_comparable_ours_from_all_classes_when_no_class_contracts_on_the_rung():
    index = history({1: D(100), 2: D(200)}, classes={1: OTHER_CLASS, 2: OTHER_CLASS})
    result = one([kp(price=D(150))], index, choice=RungChoice(1, V_ALL))
    assert result.state is LineState.comparable
    assert result.ours == D(100)
    assert result.ours_from_all_classes is True


def test_comparable_ours_is_min_of_class_contracts_on_all_scope():
    index = history({1: D(50), 2: D(120), 3: D(130)}, classes={1: OTHER_CLASS})
    result = one([kp(price=D(150))], index, choice=RungChoice(1, V_ALL))
    assert result.range is not None and result.range.min == D(50)  # вилка — все договоры
    assert result.ours == D(120)  # а наша расценка — только класс тендера
    assert result.ours_from_all_classes is False


def test_comparable_auto_weakens_class_before_parameters():
    # на v·cls один договор, на v·all два: авто берёт v·all, не p·cls
    lines = [
        hline(1, D(100)),
        hline(2, D(120)),
        hline(3, D(130), key=LineKey(5, 51, (7, 70))),
        hline(4, D(140), key=LineKey(5, 52, (7, 70))),
        hline(5, D(150), key=LineKey(5, 53, (7, 70))),
    ]
    contracts = [contract(1), contract(2, cls=OTHER_CLASS)] + [contract(i) for i in (3, 4, 5)]
    index = HistoryIndex(contracts, lines, factor_by_estimate=None)
    result = one([kp(price=D(110))], index)
    assert result.rung == V_ALL
    assert [count.contracts for count in result.rungs] == [1, 2, 4, 5, 4, 5]


def test_comparable_single_contract_rung_is_used_when_no_rung_reaches_the_threshold():
    index = history({1: D(100)})
    result = one([kp(price=D(100))], index)
    assert result.rung == V_CLS
    assert result.threshold_met is False
    assert result.state is LineState.comparable


def test_comparable_on_line_without_first_main_uses_four_rungs():
    index = history({1: D(100), 2: D(100)}, key=KEY_NO_P)
    result = one([kp(key=KEY_NO_P, price=D(100))], index)
    assert result.state is LineState.comparable
    assert [count.rung for count in result.rungs] == [V_CLS, V_ALL, F_CLS, F_ALL]


def test_unit_mismatch_single_penny_contract_does_not_divide_by_zero():
    index = history({1: D("0.0000001")})
    result = one([kp(price=D(1))], index)
    assert result.state is LineState.unit_mismatch
    assert result.mismatch is not None
    assert result.mismatch.kp_ratio == D(1) / D("0.0000001")


def test_lines_are_evaluated_independently():
    index = history({1: D(100), 2: D(200)})
    lines = [kp(1, price=D(150)), kp(2, price=D(0)), kp(3, key=None)]
    evaluation = evaluate(lines, index)
    assert [evaluation.lines[i].state for i in (1, 2, 3)] == [
        LineState.comparable,
        LineState.no_price,
        LineState.no_variant,
    ]


# --- unit_mismatch в evaluate_offer ---------------------------------------------


def test_unit_mismatch_counterexample_kp_far_above():
    index = history({1: D(100), 2: D(100)})
    result = one([kp(price=D(1000), weight=D(1))], index)
    assert result.state is LineState.unit_mismatch
    assert result.to_ours == D(900)
    assert result.bargain is None
    assert result.below is None
    assert result.position == "above"
    assert result.mismatch is not None and result.mismatch.kp_ratio == D(10)


def test_unit_mismatch_counterexample_kp_far_below():
    index = history({1: D(100), 2: D(100)})
    result = one([kp(price=D(10), weight=D(1))], index)
    assert result.state is LineState.unit_mismatch
    assert result.to_ours == D(-90)
    assert result.bargain is None
    assert result.below is None
    assert result.position == "below"
    assert result.mismatch is not None and result.mismatch.kp_ratio == D("0.1")


def test_unit_mismatch_by_contract_only_has_no_kp_ratio():
    index = history({1: D(100), 2: D(100), 3: D(100), 4: D(1000)})
    result = one([kp(price=D(100))], index)
    assert result.state is LineState.unit_mismatch
    assert result.mismatch is not None
    assert result.mismatch.kp_ratio is None
    assert result.mismatch.contracts == ((4, D(10)),)
    assert result.to_ours == D(0)
    assert result.bargain is None and result.below is None


def test_unit_mismatch_kp_ratio_boundary_five_is_comparable_in_evaluate():
    index = history({1: D(100), 2: D(100)})
    assert one([kp(price=D(500))], index).state is LineState.comparable
    assert one([kp(price=D("500.01"))], index).state is LineState.unit_mismatch
    assert one([kp(price=D(20))], index).state is LineState.comparable
    assert one([kp(price=D("19.99"))], index).state is LineState.unit_mismatch


def test_unit_mismatch_tiny_contract_next_to_hundreds_in_evaluate():
    index = history({1: D(100), 2: D(150), 3: D("0.0000001")})
    result = one([kp(price=D(120))], index)
    assert result.state is LineState.unit_mismatch
    assert result.mismatch is not None
    assert 3 in dict(result.mismatch.contracts)


def test_unit_mismatch_compares_with_the_median_of_the_selected_rung():
    # четыре договора, медиана 250: КП 1250 — ровно пять медиан
    index = history({1: D(100), 2: D(200), 3: D(300), 4: D(400)})
    assert one([kp(price=D(1250))], index).state is LineState.comparable
    assert one([kp(price=D("1250.01"))], index).state is LineState.unit_mismatch


# --- выбор ступени -------------------------------------------------------------


def _two_level_history() -> HistoryIndex:
    lines = [
        hline(1, D(100)),
        hline(2, D(100)),
        hline(3, D(300), key=LineKey(5, 51, (7, 70))),
    ]
    return HistoryIndex([contract(1), contract(2), contract(3)], lines, factor_by_estimate=None)


def test_choice_changes_only_the_chosen_line():
    index = _two_level_history()
    lines = [kp(1, price=D(200)), kp(2, price=D(200))]
    evaluation = evaluate(lines, index, choice=RungChoice(2, F_ALL))
    chosen, other = evaluation.lines[2], evaluation.lines[1]
    assert chosen.rung == F_ALL and chosen.auto is False
    assert chosen.range is not None and chosen.range.max == D(300)
    assert other.rung == V_CLS and other.auto is True
    assert other.range is not None and other.range.max == D(100)


def test_choice_threshold_met_counts_contracts_of_the_chosen_rung():
    index = _two_level_history()
    two = one([kp(price=D(200))], index, choice=RungChoice(1, V_ALL))
    assert two.threshold_met is True
    one_contract = one(
        [kp(price=D(200))],
        history({1: D(100)}),
        choice=RungChoice(1, V_ALL),
    )
    assert one_contract.threshold_met is False
    assert one_contract.state is LineState.comparable


def test_choice_threshold_met_is_about_the_chosen_rung_not_any_rung():
    # v·cls — один договор, v·all — два: выбор v·cls порога не достигает
    index = history({1: D(100), 2: D(120)}, classes={2: OTHER_CLASS})
    chosen = one([kp(price=D(110))], index, choice=RungChoice(1, V_CLS))
    assert chosen.rung == V_CLS and chosen.auto is False
    assert chosen.threshold_met is False
    automatic = one([kp(price=D(110))], index)
    assert automatic.rung == V_ALL and automatic.threshold_met is True


def test_choice_of_a_rung_the_line_does_not_have_is_value_error():
    index = history({1: D(100), 2: D(100)}, key=KEY_NO_P)
    with pytest.raises(ValueError):
        evaluate([kp(key=KEY_NO_P)], index, choice=RungChoice(1, P_CLS))


def test_choice_of_an_empty_rung_is_no_history_on_that_rung():
    index = history({1: D(100), 2: D(100)}, classes={1: OTHER_CLASS, 2: OTHER_CLASS})
    result = one([kp()], index, choice=RungChoice(1, V_CLS))
    assert result.state is LineState.no_history
    assert result.rung == V_CLS
    assert result.auto is False
    assert result.range is None and result.ours is None and result.to_ours is None
    assert [count.contracts for count in result.rungs][:2] == [0, 2]


def test_choice_for_another_line_leaves_a_line_automatic():
    index = history({1: D(100), 2: D(100)})
    evaluation = evaluate([kp(1)], index, choice=RungChoice(99, F_ALL))
    assert evaluation.lines[1].auto is True
    assert evaluation.lines[1].rung == V_CLS


def test_choice_on_a_line_that_is_not_comparable_does_not_raise_for_its_state():
    # строка без цены остаётся no_price, выбор ступени на ней ничего не меняет
    result = one([kp(price=D(0))], history({1: D(100)}), choice=RungChoice(1, V_ALL))
    assert result.state is LineState.no_price


# --- суммы ---------------------------------------------------------------------


def _mixed_offer() -> tuple[OfferEvaluation, list[int]]:
    index = history({1: D(100), 2: D(200)})
    lines = [
        kp(1, price=D(250), weight=D(2)),  # comparable above: to_ours 300
        kp(2, price=D(90), weight=D(3)),  # comparable below: below -30, bargain 0
        kp(3, price=D(150), weight=D(1)),  # comparable in: bargain 50
        kp(4, price=D(1000), weight=D(1)),  # unit_mismatch, total 1000
        kp(5, price=D(0), weight=D(1), total=D(40)),  # no_price, total 40
        kp(6, price=D(0), weight=D(1), total=D(-15)),  # no_price, total -15
        kp(7, price=D(100), key=None),  # no_variant, total 100
        kp(8, price=D(100), total=NAN, key=None),  # no_variant, total not finite
        kp(9, in_progress=True, price=D(10), total=D(10)),  # in_progress, total 10
    ]
    return evaluate(lines, index), [line.position_item_id for line in lines]


def test_offer_totals_bargain_and_below_are_sums_over_comparable():
    evaluation, _ = _mixed_offer()
    totals = offer_totals(evaluation)
    # bargain: 300 + 0 + 50; below: -30 на одной строке
    assert totals.bargain == D(350)
    assert totals.below == D(-30)
    assert totals.below_lines == 1


def test_offer_totals_unit_mismatch_is_counted_apart():
    evaluation, _ = _mixed_offer()
    totals = offer_totals(evaluation)
    assert totals.unit_mismatch_lines == 1
    assert totals.unit_mismatch_amount == D(1000)


def test_offer_totals_not_comparable_buckets_sum_finite_totals_with_sign():
    evaluation, _ = _mixed_offer()
    totals = offer_totals(evaluation)
    assert totals.not_comparable == {
        LineState.no_price: _bucket(2, D(25)),  # 40 + (-15)
        LineState.no_variant: _bucket(2, D(100)),  # NaN не входит в сумму, строка считается
        LineState.in_progress: _bucket(1, D(10)),
    }
    assert totals.not_comparable_total == D(135)


def _bucket(lines: int, amount: Decimal | None) -> StateBucket:
    return StateBucket(lines=lines, amount=amount)


def test_offer_totals_unit_mismatch_amount_keeps_sign_and_skips_non_finite():
    index = history({1: D(100), 2: D(100)})
    lines = [
        kp(1, price=D(1000), total=D(300)),
        kp(2, price=D(1000), total=D(-50)),
        kp(3, price=D(1000), total=NAN),
    ]
    totals = offer_totals(evaluate(lines, index))
    assert totals.unit_mismatch_lines == 3
    assert totals.unit_mismatch_amount == D(250)  # 300 + (-50), NaN не входит


def test_offer_totals_has_no_empty_buckets_and_excludes_comparable_states():
    index = history({1: D(100), 2: D(200)})
    totals = offer_totals(evaluate([kp(1, price=D(150))], index))
    assert totals.not_comparable == {}
    assert totals.not_comparable_total == D(0)
    assert totals.unit_mismatch_lines == 0
    assert totals.unit_mismatch_amount == D(0)


def test_offer_totals_empty_evaluation_is_all_zero():
    totals = offer_totals(OfferEvaluation(lines={}, groups=()))
    assert (totals.bargain, totals.below, totals.below_lines) == (D(0), D(0), 0)
    assert totals.not_comparable == {}


def test_offer_totals_unknown_vat_base_bucket_has_no_amount():
    index = history({1: D(100), 2: D(100)})
    evaluation = evaluate([kp(1), kp(2, in_progress=True, total=D(10))], index, base=None)
    totals = offer_totals(evaluation)
    bucket = totals.not_comparable[LineState.unknown_vat_base]
    assert bucket.lines == 1 and bucket.amount is None
    # без базы нетто нет и у строки, ждущей каталог: сумма корзины 0, а не 10
    assert totals.not_comparable[LineState.in_progress] == _bucket(1, D(0))
    assert totals.not_comparable_total == D(0)


def _hand_line(**kw) -> LineResult:
    base = LineResult(
        position_item_id=1,
        state=LineState.comparable,
        kp_unit_net=D(1),
        kp_total_net=D(1),
        rung=V_CLS,
        auto=True,
        threshold_met=True,
        rungs=(),
        contracts=(),
        range=None,
        ours=None,
        ours_from_all_classes=False,
        position=None,
        to_ours=None,
        bargain=None,
        below=None,
        group_index=None,
        group_lines=None,
        share=None,
        group_per_sqm=None,
        mismatch=None,
    )
    return replace(base, **kw)


def test_offer_totals_sum_filter_takes_only_comparable_lines():
    # строка unit_mismatch с заполненными bargain/below в обход evaluate_offer
    results = {
        1: _hand_line(
            position_item_id=1,
            state=LineState.comparable,
            kp_total_net=D(10),
            bargain=D(7),
            below=D(-2),
        ),
        2: _hand_line(
            position_item_id=2,
            state=LineState.unit_mismatch,
            kp_total_net=D(500),
            bargain=D(900),
            below=D(-90),
        ),
    }
    totals = offer_totals(OfferEvaluation(lines=results, groups=()))
    assert totals.bargain == D(7)
    assert totals.below == D(-2)
    assert totals.below_lines == 1
    assert totals.unit_mismatch_lines == 1
    assert totals.unit_mismatch_amount == D(500)


def test_offer_totals_below_lines_counts_lines_with_below():
    results = {i: _hand_line(position_item_id=i, below=D(-i) if i < 3 else None) for i in (1, 2, 3)}
    totals = offer_totals(OfferEvaluation(lines=results, groups=()))
    assert totals.below_lines == 2
    assert totals.below == D(-3)


def test_lines_totals_over_all_lines_equals_offer_totals_exactly():
    evaluation, ids = _mixed_offer()
    assert lines_totals(evaluation, ids) == offer_totals(evaluation)


def test_lines_totals_over_a_subset_sums_only_those_lines():
    evaluation, _ = _mixed_offer()
    totals = lines_totals(evaluation, [1, 4, 5])
    assert totals.bargain == D(300)
    assert totals.below == D(0)
    assert totals.below_lines == 0
    assert totals.unit_mismatch_lines == 1
    assert totals.not_comparable == {LineState.no_price: _bucket(1, D(40))}
    assert totals.not_comparable_total == D(40)


def test_lines_totals_counts_a_repeated_id_once():
    evaluation, _ = _mixed_offer()
    assert lines_totals(evaluation, [1, 1, 5, 5]) == lines_totals(evaluation, [1, 5])


def test_lines_totals_ignores_ids_that_are_not_in_the_evaluation():
    evaluation, _ = _mixed_offer()
    assert lines_totals(evaluation, [1, 12345]) == lines_totals(evaluation, [1])

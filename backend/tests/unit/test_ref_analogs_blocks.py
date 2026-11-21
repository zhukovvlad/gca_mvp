"""Блоки А и Г, пересчёт строки и пометки выбросов (спека справочных аналогов §2.4, §2.5, §2.8–§2.11).

Чистые функции без базы. Условные данные: семья 1 — «строка КП» (м³), семья 2 — связанная
(м²); параметры связи «Толщина» (семья 1) и «Толщина слоя» (семья 2). Эталоны — независимые
литералы `Decimal`.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from crud.reference_lines import ParamText
from models import FamilyLink
from money.inflation import YearMonth
from services import reference_blocks as rb
from services.analog_ladder import (
    FamilyLadderInfo,
    KpWork,
    WorkHistory,
    WorkHistoryLine,
    ladder_for,
)
from services.family_links import LinkState, SideInfo
from services.price_basis import HistoryContract

D = Decimal
KP_FAMILY = 1
REF_FAMILY = 2
M3, M2 = 3, 2
UNITS = {M2: "м²", M3: "м³"}
KP_PARAM = 10  # «Толщина», семья 1
REF_PARAM = 20  # «Толщина слоя», семья 2
RELEASED = 30  # отпускаемый главный параметр семьи 1
REQUIRED = 31  # обязательный главный параметр семьи 1
SCHEMA = 7
W1, W2 = 100, 101
TENDER_CLASS = 1
OTHER_CLASS = 2
KP_PID = 9000
MONTH = YearMonth(2026, 1)


def _text(text: str | None, name: str = "Толщина слоя", **kw) -> ParamText:
    return ParamText(
        text=text, parameter_name=name, source=kw.get("source", "manual"),
        off_list=kw.get("off_list", False), composite=kw.get("composite", False),
    )


def _link(
    kind: str = "thickness",
    *,
    active: bool = True,
    live_lo: bool = True,
    live_hi: bool = True,
    param_lo: int | None = KP_PARAM,
    param_hi: int | None = REF_PARAM,
    factor: str | None = None,
    factor_from: int | None = None,
    basis: str | None = None,
    lo: int = KP_FAMILY,
    hi: int = REF_FAMILY,
    unit_lo: int = M3,
    unit_hi: int = M2,
) -> LinkState:
    constant = kind == "constant"
    row = FamilyLink(
        id=77, family_lo_id=lo, family_hi_id=hi, unit_lo_id=unit_lo, unit_hi_id=unit_hi, kind=kind,
        param_lo_id=None if constant else param_lo, param_hi_id=None if constant else param_hi,
        factor_from_id=factor_from, factor=None if factor is None else D(factor), basis=basis,
        created_by=1, updated_by=1,
    )
    return LinkState(
        link=row, active=active, inactive_reason=None if active else "units_changed",
        live_lo=live_lo, live_hi=live_hi,
        lo=SideInfo(lo, "Семья К", UNITS[unit_lo], None if constant else param_lo, "Толщина"),
        hi=SideInfo(hi, "Семья С", UNITS[unit_hi], None if constant else param_hi, "Толщина слоя"),
    )


def _convert(
    *,
    contract_unit=M2,
    kp_unit=M3,
    contract_family=REF_FAMILY,
    kp_family=KP_FAMILY,
    links=None,
    values=None,
    pid=500,
):
    return rb.conversion_for(
        contract_unit_id=contract_unit, kp_unit_id=kp_unit,
        contract_family_id=contract_family, kp_family_id=kp_family,
        links=[_link()] if links is None else links, units=UNITS,
        values={(pid, REF_PARAM): _text("100 мм")} if values is None else values,
        position_item_id=pid,
    )


# ---------------------------------------------------------------------------
#  conversion_for (§2.8)
# ---------------------------------------------------------------------------


def test_ref_analogs_equal_units_need_no_conversion() -> None:
    assert _convert(contract_unit=M3, kp_unit=M3) == (None, None, False)


@pytest.mark.parametrize(("contract_unit", "kp_unit"), [(None, M3), (M2, None), (None, None)])
def test_ref_analogs_an_empty_unit_means_different_and_never_equal(contract_unit, kp_unit) -> None:
    conversion, reason, needed = _convert(contract_unit=contract_unit, kp_unit=kp_unit)
    assert (conversion, reason, needed) == (None, None, True)


def test_ref_analogs_a_live_link_with_a_number_gives_n_and_basis_with_the_link_id() -> None:
    conversion, reason, needed = _convert()
    assert conversion == rb.Conversion(method="link", factor=D("0.1"), basis="толщина 100 мм", link_id=77)
    assert (reason, needed) == (None, True)


def test_ref_analogs_the_larger_contract_unit_takes_the_inverse() -> None:
    """Строка договора в м³, строка КП в м² (семья КП — м²): N = 1 / x."""
    link = _link(lo=REF_FAMILY, hi=KP_FAMILY, unit_lo=M3, unit_hi=M2, param_lo=REF_PARAM, param_hi=KP_PARAM)
    conversion, _, _ = _convert(contract_unit=M3, kp_unit=M2, links=[link])
    assert conversion is not None
    assert conversion.factor == D("10")  # 1 / 0,1


def test_ref_analogs_a_value_that_is_not_a_number_gives_the_reason_with_its_text() -> None:
    values = {(500, REF_PARAM): _text("переменная")}
    assert _convert(values=values) == (None, "значение «переменная» — не число", True)


def test_ref_analogs_a_missing_value_gives_the_reason_with_the_parameter_name() -> None:
    assert _convert(values={}) == (None, "у строки нет значения «Толщина слоя»", True)


def test_ref_analogs_a_dead_side_gives_the_reason_stale() -> None:
    values = {(500, REF_PARAM): _text("100 мм")}
    assert _convert(links=[_link(live_hi=False)], values=values) == (None, "параметр устарел", True)


def test_ref_analogs_a_live_side_of_the_other_family_does_not_rescue_a_dead_one() -> None:
    """Параметр семьи договора мёртв, живой — только у семьи КП: числа у строки договора нет."""
    values = {(500, REF_PARAM): _text("100 мм")}
    conversion, reason, _ = _convert(links=[_link(live_lo=True, live_hi=False)], values=values)
    assert conversion is None
    assert reason == "параметр устарел"


def test_ref_analogs_no_link_means_manual_without_a_reason() -> None:
    assert _convert(links=[]) == (None, None, True)


def test_ref_analogs_a_link_between_other_families_means_manual() -> None:
    other = _link(lo=5, hi=6)
    assert _convert(links=[other]) == (None, None, True)


def test_ref_analogs_an_inactive_link_means_manual() -> None:
    assert _convert(links=[_link(active=False)]) == (None, None, True)


def test_ref_analogs_a_row_unit_that_is_not_the_family_unit_means_manual() -> None:
    assert _convert(contract_unit=9) == (None, None, True)


def test_ref_analogs_a_kp_unit_that_is_not_the_other_family_unit_means_manual() -> None:
    assert _convert(kp_unit=9) == (None, None, True)


def test_ref_analogs_a_side_without_a_parameter_means_manual() -> None:
    link = _link(param_hi=None, live_hi=False)
    assert _convert(links=[link]) == (None, None, True)


def test_ref_analogs_a_constant_goes_both_ways() -> None:
    """1 м³ = 1,5 т: семья КП (т) ← семья договора (м³) с N = 1,5; обратно — 1 / 1,5."""
    forward = _link("constant", lo=KP_FAMILY, hi=REF_FAMILY, factor="1.5", factor_from=REF_FAMILY,
                    basis="насыпная плотность 1,5 т/м³")
    conversion, _, _ = _convert(links=[forward], values={})
    assert conversion == rb.Conversion("link", D("1.5"), "насыпная плотность 1,5 т/м³", 77)
    backward = _link("constant", lo=KP_FAMILY, hi=REF_FAMILY, factor="1.5", factor_from=KP_FAMILY,
                     basis="насыпная плотность 1,5 т/м³")
    inverse, _, _ = _convert(links=[backward], values={})
    assert inverse is not None
    assert inverse.factor * D("1.5") == pytest.approx(D(1), abs=D("1e-20"))
    assert inverse.link_id == 77


# ---------------------------------------------------------------------------
#  Сцены блоков
# ---------------------------------------------------------------------------


def _contract(cid: int, *, cls: int = TENDER_CLASS, contractor: int = 50, period=MONTH) -> HistoryContract:
    return HistoryContract(
        contract_id=cid, estimate_id=cid * 10, number=f"N{cid}", object_title="Объект", year=2026,
        rate_class_id=cls, rate_class_title="Класс", contractor_id=contractor, contractor_title="Подрядчик",
        area=None, period=period,
    )


def _line(
    pid: int, cid: int, price: str, *, zone="aboveground", family=KP_FAMILY, variant=W1, unit=M3,
    values=None, titles: tuple[str, ...] = (),
) -> WorkHistoryLine:
    return WorkHistoryLine(
        position_item_id=pid, contract_id=cid, family_id=family, chain_id=1, zone=zone,
        chain_titles=frozenset(titles), variant_id=variant, schema_id=SCHEMA,
        main_values={RELEASED: 1, REQUIRED: 5} if values is None else values,
        unit_price=D(price), weight=D(1), vat_base=D(0), unit_id=unit,
    )


def _info() -> dict[int, FamilyLadderInfo]:
    return {
        KP_FAMILY: FamilyLadderInfo(
            schema_id=SCHEMA, main_parameter_ids=(RELEASED, REQUIRED), releasable_ids=frozenset({RELEASED}),
            names={RELEASED: "Марка", REQUIRED: "Класс"},
        )
    }


def _history(lines: list[WorkHistoryLine], contracts: list[HistoryContract]) -> WorkHistory:
    return WorkHistory(lines, _info(), {c.contract_id: c for c in contracts}, factors=None,
                       zone_required=frozenset({KP_FAMILY}))


def _kp(*, zone="aboveground", variant=W1, values=None, net="100", titles=()) -> KpWork:
    return KpWork(
        position_item_id=KP_PID, family_id=KP_FAMILY, variant_id=variant, schema_id=SCHEMA,
        main_values={RELEASED: 1, REQUIRED: 5} if values is None else values,
        zone=zone, zone_required=True, chain_titles=frozenset(titles), net=D(net),
    )


def _zones(kp: KpWork, history: WorkHistory):
    return rb.zone_groups(kp, history, tender_class_id=TENDER_CLASS, tender_contractor_id=99, target_month=MONTH)


# ---------------------------------------------------------------------------
#  Блок А (§2.4)
# ---------------------------------------------------------------------------


def test_ref_analogs_zone_groups_follow_the_zone_order_and_skip_zones_without_a_rung() -> None:
    lines = [
        _line(1, 1, "100", zone="territory"),
        _line(2, 2, "110", zone="underground"),
        _line(3, 3, "120", zone="whole"),
    ]
    history = _history(lines, [_contract(1), _contract(2), _contract(3)])
    groups = _zones(_kp(zone="aboveground"), history)
    assert [g.zone for g in groups] == ["underground", "territory", "whole"]
    assert [[r.position_item_id for r in g.rows] for g in groups] == [[2], [1], [3]]


def test_ref_analogs_a_row_with_own_analogs_has_no_block() -> None:
    lines = [_line(1, 1, "100", zone="aboveground"), _line(2, 2, "110", zone="underground")]
    history = _history(lines, [_contract(1), _contract(2)])
    assert _zones(_kp(zone="aboveground"), history) == ()


def test_ref_analogs_a_row_that_is_no_history_for_params_has_no_block() -> None:
    """Вариант и значения не совпали ни в одной зоне: причина `no_params`, блока нет."""
    lines = [_line(1, 1, "100", zone="underground", variant=W2, values={RELEASED: 1, REQUIRED: 9})]
    history = _history(lines, [_contract(1)])
    assert _zones(_kp(zone="aboveground"), history) == ()


def test_ref_analogs_a_row_without_a_zone_has_no_block() -> None:
    lines = [_line(1, 1, "100", zone="underground")]
    assert _zones(_kp(zone=None), _history(lines, [_contract(1)])) == ()


def test_ref_analogs_a_group_carries_the_rung_the_ladder_picks_on_that_zone() -> None:
    """Ступень 2 с отпущенным параметром и ступень 3 «другой класс» — как у `ladder_for` на той зоне."""
    lines = [
        _line(1, 1, "100", zone="underground", variant=W2, values={RELEASED: 2, REQUIRED: 5}),
        _line(2, 2, "100", zone="territory"),
    ]
    contracts = [_contract(1), _contract(2, cls=OTHER_CLASS)]
    history = _history(lines, contracts)
    kp = _kp(zone="aboveground")
    groups = {g.zone: g for g in _zones(kp, history)}
    for zone in ("underground", "territory"):
        ladder = ladder_for(
            dataclasses.replace(kp, zone=zone), history, tender_class_id=TENDER_CLASS,
            tender_contractor_id=99, target_month=MONTH,
        )
        assert (groups[zone].rung_no, groups[zone].released, groups[zone].other_class) == (
            ladder.rung_no, ladder.released, ladder.other_class,
        )
    assert (groups["underground"].rung_no, groups["underground"].released) == (2, ("Марка",))
    assert (groups["territory"].rung_no, groups["territory"].other_class) == (3, True)


def test_ref_analogs_a_group_lists_every_analog_of_the_rung_with_the_marks_of_that_rung() -> None:
    nets = ["100", "100", "100", "1000"]
    lines = [_line(i + 1, i + 1, n, zone="underground") for i, n in enumerate(nets)]
    history = _history(lines, [_contract(i + 1) for i in range(4)])
    (group,) = _zones(_kp(zone="aboveground"), history)
    marks = {r.position_item_id: r.outlier_ratio for r in group.rows}
    assert marks == {1: None, 2: None, 3: None, 4: D("10")}
    assert all(r.conversion is None and r.no_conversion_reason is None for r in group.rows)
    assert {r.position_item_id: (r.nominal, r.net, r.kp_unit_net) for r in group.rows}[4] == (
        D("1000"), D("1000"), D("1000"),
    )


def test_ref_analogs_a_group_keeps_the_ladder_reference_order() -> None:
    """Близость места, затем дата, затем цена: порядок справки лестницы, а не порядок строк."""
    lines = [
        _line(1, 1, "300", zone="underground", titles=("корпус",)),
        _line(2, 2, "200", zone="underground", titles=()),
        _line(3, 3, "100", zone="underground", titles=()),
    ]
    history = _history(lines, [_contract(1), _contract(2), _contract(3)])
    (group,) = _zones(_kp(zone="aboveground", titles=("корпус",)), history)
    assert [r.position_item_id for r in group.rows] == [1, 3, 2]


def test_ref_analogs_other_zones_count_lines_and_contracts_before_folding() -> None:
    lines = [
        _line(1, 1, "100", zone="underground"),
        _line(2, 1, "100", zone="underground"),
        _line(3, 2, "120", zone="underground"),
        _line(4, 3, "130", zone="territory"),
    ]
    history = _history(lines, [_contract(1), _contract(2), _contract(3)])
    groups = _zones(_kp(zone="aboveground"), history)
    assert rb.other_zones(groups) == [
        {"zone": "underground", "lines": 3, "contracts": 2},
        {"zone": "territory", "lines": 1, "contracts": 1},
    ]
    assert rb.other_zones(()) == []


# ---------------------------------------------------------------------------
#  Свёртка (§2.11)
# ---------------------------------------------------------------------------


def _row(pid: int, *, price: str = "100", contract: int = 1, zone="underground", mark=None, conv=None, reason=None):
    return rb.RefRow(
        position_item_id=pid, contract_id=contract, zone=zone, unit_id=M3, nominal=D(price), net=D(price),
        conversion=conv, no_conversion_reason=reason, kp_unit_net=D(price), outlier_ratio=mark, param_value=None,
    )


def test_ref_analogs_fold_merges_equal_rows_of_one_contract_and_keeps_the_least_id() -> None:
    rows = [_row(5), _row(3), _row(4, price="101")]
    titles = {3: "Подготовка", 4: "Подготовка", 5: "Подготовка"}
    folded = rb.fold_group_rows(rows, titles)
    assert [(r.position_item_id, ids) for r, ids in folded] == [(3, (3, 5)), (4, (4,))]


@pytest.mark.parametrize(
    "other",
    [
        pytest.param(dict(contract=2), id="contract"),
        pytest.param(dict(zone="territory"), id="zone"),
        pytest.param(dict(mark=D("10")), id="mark"),
        pytest.param(dict(reason="параметр устарел"), id="reason"),
        pytest.param(dict(conv=rb.Conversion("link", D("0.1"), "толщина 100 мм", 77)), id="conversion"),
    ],
)
def test_ref_analogs_fold_does_not_merge_rows_that_differ_in_one_field(other) -> None:
    rows = [_row(1), _row(2, **other)]
    titles = {1: "Подготовка", 2: "Подготовка"}
    assert [ids for _, ids in rb.fold_group_rows(rows, titles)] == [(1,), (2,)]


def test_ref_analogs_fold_does_not_merge_different_titles() -> None:
    rows = [_row(1), _row(2)]
    assert len(rb.fold_group_rows(rows, {1: "Подготовка", 2: "Подготовка бетонная"})) == 2


# ---------------------------------------------------------------------------
#  Блок Г (§2.9)
# ---------------------------------------------------------------------------


def _groups_g(lines, contracts, values, *, links=None, kp=None, kp_unit=M3):
    history = _history(lines, contracts)
    return rb.link_groups(
        kp or _kp(), kp_unit, history, [_link()] if links is None else links, UNITS, values,
        tender_class_id=TENDER_CLASS, target_month=MONTH,
    )


def _thick(pid: int, text: str | None) -> tuple[tuple[int, int], ParamText]:
    return (pid, REF_PARAM), _text(text)


def test_ref_analogs_subbase_scenario_converts_square_metres_into_cubic_metres() -> None:
    """Строка КП в м³, связь «толщина» с семьёй в м², значение «100 мм»: 1 107,24 ₽/м² → 11 072,40 ₽/м³."""
    lines = [_line(1, 1, "1107.24", family=REF_FAMILY, unit=M2)]
    (group,) = _groups_g(lines, [_contract(1)], dict([_thick(1, "100 мм")]))
    (row,) = group.rows
    assert (group.link_id, group.family_id, group.kind, group.other_class) == (77, REF_FAMILY, "thickness", False)
    assert row.net == D("1107.24")
    assert row.conversion == rb.Conversion("link", D("0.1"), "толщина 100 мм", 77)
    assert row.kp_unit_net == D("11072.4")
    assert row.param_value == "100 мм"


def test_ref_analogs_a_row_in_equal_units_has_no_conversion_and_its_own_price() -> None:
    lines = [_line(1, 1, "500", family=REF_FAMILY, unit=M3)]
    (group,) = _groups_g(lines, [_contract(1)], {})
    (row,) = group.rows
    assert (row.conversion, row.no_conversion_reason, row.kp_unit_net) == (None, None, D("500"))


def test_ref_analogs_an_inactive_link_gives_no_group() -> None:
    lines = [_line(1, 1, "1107.24", family=REF_FAMILY, unit=M2)]
    assert _groups_g(lines, [_contract(1)], dict([_thick(1, "100 мм")]), links=[_link(active=False)]) == ()


def test_ref_analogs_a_link_of_other_families_gives_no_group() -> None:
    lines = [_line(1, 1, "1107.24", family=REF_FAMILY, unit=M2)]
    assert _groups_g(lines, [_contract(1)], {}, links=[_link(lo=5, hi=6)]) == ()


def test_ref_analogs_only_rows_of_the_zone_of_the_kp_row_are_candidates() -> None:
    lines = [
        _line(1, 1, "100", family=REF_FAMILY, unit=M2, zone="aboveground"),
        _line(2, 2, "100", family=REF_FAMILY, unit=M2, zone="underground"),
    ]
    values = dict([_thick(1, "100 мм"), _thick(2, "100 мм")])
    (group,) = _groups_g(lines, [_contract(1), _contract(2)], values)
    assert [r.position_item_id for r in group.rows] == [1]
    assert all(r.zone == "aboveground" for r in group.rows)


def test_ref_analogs_tender_class_wins_and_the_other_class_fills_in_only_when_empty() -> None:
    lines = [
        _line(1, 1, "100", family=REF_FAMILY, unit=M2),
        _line(2, 2, "100", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "100 мм"), _thick(2, "100 мм")])
    (own,) = _groups_g(lines, [_contract(1), _contract(2, cls=OTHER_CLASS)], values)
    assert ([r.position_item_id for r in own.rows], own.other_class) == ([1], False)
    (other,) = _groups_g(lines, [_contract(1, cls=OTHER_CLASS), _contract(2, cls=OTHER_CLASS)], values)
    assert ([r.position_item_id for r in other.rows], other.other_class) == ([1, 2], True)


def test_ref_analogs_rows_without_conversion_go_last_without_a_mark_and_stay_out_of_the_set() -> None:
    """Набор выбросов — цены в единице КП только у строк с пересчётом: строка без него (1000) не
    делает выбросом ни одну из двух остальных (10 и 12)."""
    lines = [
        _line(1, 1, "1000", family=REF_FAMILY, unit=M2),
        _line(2, 2, "1", family=REF_FAMILY, unit=M2),
        _line(3, 3, "1.2", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "переменная"), _thick(2, "100 мм"), _thick(3, "100 мм")])
    (group,) = _groups_g(lines, [_contract(1), _contract(2), _contract(3)], values)
    assert [r.position_item_id for r in group.rows] == [2, 3, 1]
    last = group.rows[-1]
    assert (last.conversion, last.kp_unit_net, last.outlier_ratio) == (None, None, None)
    assert last.no_conversion_reason == "значение «переменная» — не число"
    assert [r.outlier_ratio for r in group.rows[:2]] == [None, None]


def test_ref_analogs_marks_are_computed_over_prices_in_the_unit_of_the_kp_row() -> None:
    """Номиналы 100, 200, 1000 различаются в 10 раз, а в м³ при толщинах 100, 200, 1000 мм равны
    1000: выбросов нет. Считай ядро по номиналам — строка 3 стала бы выбросом."""
    lines = [
        _line(1, 1, "100", family=REF_FAMILY, unit=M2),
        _line(2, 2, "200", family=REF_FAMILY, unit=M2),
        _line(3, 3, "1000", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "100 мм"), _thick(2, "200 мм"), _thick(3, "1000 мм")])
    (group,) = _groups_g(lines, [_contract(1), _contract(2), _contract(3)], values)
    assert [r.kp_unit_net for r in group.rows] == [D("1000")] * 3
    assert [r.outlier_ratio for r in group.rows] == [None, None, None]


def test_ref_analogs_an_outlier_in_the_kp_unit_is_marked_with_its_ratio() -> None:
    lines = [_line(i, i, p, family=REF_FAMILY, unit=M2) for i, p in ((1, "100"), (2, "100"), (3, "100"), (4, "1000"))]
    values = {key: val for key, val in (_thick(i, "100 мм") for i in range(1, 5))}
    (group,) = _groups_g(lines, [_contract(i) for i in range(1, 5)], values)
    assert {r.position_item_id: r.outlier_ratio for r in group.rows} == {1: None, 2: None, 3: None, 4: D("10")}


def test_ref_analogs_rows_with_the_same_x_as_the_kp_row_come_first_then_the_ladder_order() -> None:
    lines = [
        _line(1, 1, "50", family=REF_FAMILY, unit=M2),   # 200 мм: дешевле в м³ (250), но x другой
        _line(2, 2, "300", family=REF_FAMILY, unit=M2),  # 100 мм: в м³ 3000, x как у строки КП
    ]
    values = dict([_thick(1, "200 мм"), _thick(2, "100 мм"), ((KP_PID, KP_PARAM), _text("100 мм", "Толщина"))])
    (group,) = _groups_g(lines, [_contract(1), _contract(2)], values)
    assert [r.position_item_id for r in group.rows] == [2, 1]


def test_ref_analogs_without_the_x_of_the_kp_row_the_order_is_by_price_in_the_kp_unit() -> None:
    lines = [
        _line(1, 1, "50", family=REF_FAMILY, unit=M2),
        _line(2, 2, "300", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "200 мм"), _thick(2, "100 мм")])
    (group,) = _groups_g(lines, [_contract(1), _contract(2)], values)
    assert [r.position_item_id for r in group.rows] == [1, 2]


def test_ref_analogs_a_dead_kp_side_does_not_put_same_x_first() -> None:
    lines = [
        _line(1, 1, "50", family=REF_FAMILY, unit=M2),
        _line(2, 2, "300", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "200 мм"), _thick(2, "100 мм"), ((KP_PID, KP_PARAM), _text("100 мм", "Толщина"))])
    (group,) = _groups_g(lines, [_contract(1), _contract(2)], values, links=[_link(live_lo=False)])
    assert [r.position_item_id for r in group.rows] == [1, 2]


def test_ref_analogs_link_value_pairs_ask_only_for_live_sides_of_the_zone_candidates() -> None:
    lines = [
        _line(1, 1, "100", family=REF_FAMILY, unit=M2, zone="aboveground"),
        _line(2, 2, "100", family=REF_FAMILY, unit=M2, zone="underground"),
    ]
    history = _history(lines, [_contract(1), _contract(2)])
    kp = _kp()
    assert rb.link_value_pairs(kp, history, [_link()]) == {(1, REF_PARAM), (KP_PID, KP_PARAM)}
    assert rb.link_value_pairs(kp, history, [_link(live_lo=False)]) == {(1, REF_PARAM)}
    assert rb.link_value_pairs(kp, history, [_link(live_lo=False, live_hi=False)]) == set()
    assert rb.link_value_pairs(kp, history, [_link(active=False)]) == set()
    assert rb.link_value_pairs(kp, history, [_link("constant", factor="2", factor_from=1, basis="основание")]) == set()


# ---------------------------------------------------------------------------
#  reference_marks (§2.10)
# ---------------------------------------------------------------------------


def test_ref_analogs_marks_cover_own_rung_zones_and_links_and_nothing_else() -> None:
    other_values = {RELEASED: 1, REQUIRED: 6}
    own = [_line(i, i, p, zone="aboveground") for i, p in ((1, "100"), (2, "100"), (3, "100"), (4, "1000"))]
    zone = [
        _line(i, i, p, zone="underground", variant=W2, values=other_values)
        for i, p in ((5, "100"), (6, "100"), (7, "100"), (8, "900"))
    ]
    linked = [_line(i, i, p, family=REF_FAMILY, unit=M2) for i, p in ((9, "100"), (10, "100"), (11, "100"), (12, "1000"))]
    spare = _line(13, 13, "5000", family=REF_FAMILY, unit=M2, zone="territory")
    history = _history([*own, *zone, *linked, spare], [_contract(i) for i in range(1, 14)])
    ladder = ladder_for(_kp(), history, tender_class_id=TENDER_CLASS, tender_contractor_id=99, target_month=MONTH)
    assert ladder.rung_no == 1
    zones = _zones(_kp(variant=W2, values=other_values), history)
    assert [g.zone for g in zones] == ["underground"]
    values = {key: val for key, val in (_thick(i, "100 мм") for i in range(9, 14))}
    links = rb.link_groups(
        _kp(), M3, history, [_link()], UNITS, values, tender_class_id=TENDER_CLASS, target_month=MONTH
    )
    marks = rb.reference_marks(ladder, zones, links)
    assert marks == {
        1: None, 2: None, 3: None, 4: D("10"),
        5: None, 6: None, 7: None, 8: D("9"),
        9: None, 10: None, 11: None, 12: D("10"),
    }


def test_ref_analogs_marks_leave_rows_without_conversion_and_a_missing_ladder_out() -> None:
    lines = [
        _line(1, 1, "100", family=REF_FAMILY, unit=M2),
        _line(2, 2, "100", family=REF_FAMILY, unit=M2),
    ]
    values = dict([_thick(1, "100 мм"), _thick(2, "переменная")])
    history = _history(lines, [_contract(1), _contract(2)])
    links = rb.link_groups(
        _kp(), M3, history, [_link()], UNITS, values, tender_class_id=TENDER_CLASS, target_month=MONTH
    )
    marks = rb.reference_marks(None, (), links)
    assert marks == {1: None}


def test_ref_analogs_marks_of_the_own_rung_are_the_ladder_ratios_not_a_recomputation() -> None:
    lines = [_line(i, i, p) for i, p in ((1, "100"), (2, "100"), (3, "100"), (4, "1000"))]
    history = _history(lines, [_contract(i) for i in range(1, 5)])
    ladder = ladder_for(_kp(), history, tender_class_id=TENDER_CLASS, tender_contractor_id=99, target_month=MONTH)
    marks = rb.reference_marks(ladder, (), ())
    assert marks == {a.position_item_id: a.outlier_ratio for a in ladder.analogs}
    assert marks[4] == D("10")

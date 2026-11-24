"""Ядро «Нашей цены» для файла подрядчику: номинал ставки, сохранённые ступени, статус ручной ступени.

Чистый расчёт без базы (спека 4б §2.5, §2.6, §2.7, §2.18). Эталоны — числа,
посчитанные руками; база НДС 0, площадь объекта тендера и договоров 100.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.our_price import (
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
    SavedFamilyRung,
    SavedRungs,
    evaluate_offer,
)

D = Decimal

V_CLS = Rung("v", "cls")
V_ALL = Rung("v", "all")
P_CLS = Rung("p", "cls")
P_ALL = Rung("p", "all")
F_CLS = Rung("f", "cls")
F_ALL = Rung("f", "all")

TENDER_CLASS = 1
OTHER_CLASS = 2
AREA = D(100)

KEY50 = LineKey(family_id=5, variant_id=50, first_main=(7, 70))
KEY51 = LineKey(family_id=5, variant_id=51, first_main=(7, 70))
KEY52 = LineKey(family_id=5, variant_id=52, first_main=(7, 70))
KEY_NO_P = LineKey(family_id=5, variant_id=50, first_main=None)

V50 = LineKey(family_id=5, variant_id=50, first_main=None)
V51 = LineKey(family_id=5, variant_id=51, first_main=None)
V52 = LineKey(family_id=5, variant_id=52, first_main=None)
OTHER_FAMILY = LineKey(family_id=6, variant_id=60, first_main=None)

NO_SAVED = SavedRungs(family={})


def contract(cid: int, *, cls: int = TENDER_CLASS, area: Decimal | None = AREA) -> HistoryContract:
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


def wline(cid: int, key: LineKey, price: Decimal, weight: Decimal = D(1)) -> HistoryLine:
    return HistoryLine(cid, key, False, price, price * weight, weight, D(0))


def sline(cid: int, key: LineKey, total: Decimal) -> HistoryLine:
    return HistoryLine(cid, key, True, None, total, None, D(0))


def kp_work(pid: int, key: LineKey | None, price: Decimal | None = D(150), weight: Decimal = D(2), **kw) -> KpLine:
    return KpLine(
        position_item_id=pid,
        unit_price=price,
        total=None if price is None else price * weight,
        weight=weight,
        key=key,
        is_system=False,
        in_progress=kw.get("in_progress", False),
        waiting_admin=False,
    )


def kp_sys(pid: int, key: LineKey | None, total: Decimal | None = D(12000), **kw) -> KpLine:
    return KpLine(
        position_item_id=pid,
        unit_price=total,
        total=total,
        weight=D(1),
        key=key,
        is_system=True,
        in_progress=kw.get("in_progress", False),
        waiting_admin=False,
    )


def run(
    lines: list[KpLine],
    history: HistoryIndex,
    *,
    saved: SavedRungs | None = None,
    choice: RungChoice | None = None,
    base: Decimal | None = D(0),
    area: Decimal | None = AREA,
) -> OfferEvaluation:
    return evaluate_offer(
        lines,
        history,
        work_history=[],
        families={},
        kp_vat_base=base,
        tender_area=area,
        tender_class_id=TENDER_CLASS,
        tender_contractor_id=99,
        target_month=None,
        factors=None,
        not_analog={},
        choice=choice,
        saved=saved,
    )


def family_saved(rung: Rung, first_main_parameter_id: int | None = None, family_id: int = 5) -> SavedRungs:
    return SavedRungs(family={family_id: SavedFamilyRung(rung, first_main_parameter_id)})


@pytest.fixture
def works() -> HistoryIndex:
    """Работы семьи 5. Вариант 50: договор 1 (класс тендера) 100, договор 3 (иной класс) 300.

    Вариант 51: договоры 2 и 4 (класс тендера) 200 и 80. Вариант 52 договоров не имеет.
    Лестница строки варианта 50: v·cls 1, v·all 2, p·cls 3, p·all 4, f·cls 3, f·all 4.
    """
    return HistoryIndex(
        [contract(1), contract(2), contract(3, cls=OTHER_CLASS), contract(4)],
        [wline(1, KEY50, D(100)), wline(2, KEY51, D(200)), wline(3, KEY50, D(300)), wline(4, KEY51, D(80))],
        factor_by_estimate=None,
    )


@pytest.fixture
def buildings() -> HistoryIndex:
    """Системы семьи 5. Вариант 50: договор 1 даёт 100 за м², договор 2 — 200; вариант 51: только договор 1, 150."""
    return HistoryIndex(
        [contract(1), contract(2)],
        [sline(1, V50, D(10000)), sline(1, V51, D(15000)), sline(2, V50, D(20000))],
        factor_by_estimate=None,
    )


def only(evaluation: OfferEvaluation, pid: int = 1) -> LineResult:
    return evaluation.lines[pid]


# --- номинал и число строк у ставки договора ---------------------------------------


def test_contract_rate_nominal_is_the_rate_before_the_inflation_factor():
    # номинал (100*1 + 200*3) / 4 = 175, ставка после множителя 1.1 — 192.5
    lines = [wline(1, KEY50, D(100), D(1)), wline(1, KEY50, D(200), D(3)), wline(2, KEY50, D(100))]
    index = HistoryIndex([contract(1), contract(2)], lines, factor_by_estimate={100: D("1.1"), 200: D("1.1")})
    assert index.rates("v", 50, is_system=False, class_id=None) == [
        ContractRate(1, D("192.5"), D(175), 2),
        ContractRate(2, D("110.0"), D(100), 1),
    ]


def test_contract_rate_without_a_plan_nominal_equals_net():
    lines = [wline(1, KEY50, D(100), D(1)), wline(1, KEY50, D(200), D(3))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert (rate.net, rate.nominal, rate.lines) == (D(175), D(175), 2)


def test_contract_rate_lines_do_not_count_a_line_without_a_vat_base():
    lines = [wline(1, KEY50, D(100)), HistoryLine(1, KEY50, False, D(900), D(900), D(1), None)]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    [rate] = index.rates("v", 50, is_system=False, class_id=None)
    assert rate.lines == 1


def test_contract_rate_lines_are_counted_per_key_level():
    # у договора две строки семьи в разных вариантах: на уровне варианта по одной, на уровне семьи две
    lines = [wline(1, KEY50, D(100)), wline(1, KEY51, D(200))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate=None)
    assert index.rates("v", 50, is_system=False, class_id=None)[0].lines == 1
    assert index.rates("f", 5, is_system=False, class_id=None)[0].lines == 2


def test_contract_rate_of_a_system_counts_its_lines_and_keeps_the_nominal():
    # (6000 + 4000) / 100 = 100 за м²; множитель 1.5 даёт 150
    lines = [sline(1, V50, D(6000)), sline(1, V50, D(4000))]
    index = HistoryIndex([contract(1)], lines, factor_by_estimate={100: D("1.5")})
    assert index.rates("v", 50, is_system=True, class_id=None) == [ContractRate(1, D("150.0"), D(100), 2)]


# --- без сохранённых ступеней результат прежний ------------------------------------


def test_without_saved_the_result_is_the_4a_result_with_empty_manual(works):
    lines = [kp_work(1, KEY50), kp_work(2, KEY52), kp_work(3, None), kp_work(4, KEY50, None)]
    plain = run(lines, works)
    empty = run(lines, works, saved=NO_SAVED)
    assert plain == empty
    assert all(result.manual is None for result in plain.lines.values())


def test_without_saved_the_system_result_has_empty_manual(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V51)]
    assert run(lines, buildings) == run(lines, buildings, saved=NO_SAVED)
    assert all(result.manual is None for result in run(lines, buildings).lines.values())


# --- selectable ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        kp_work(1, KEY50, in_progress=True),
        kp_work(1, KEY50, None),
        kp_work(1, None),
    ],
)
def test_selectable_of_a_line_without_a_ladder_is_empty(works, line):
    assert only(run([line], works)).selectable == frozenset()
    assert only(run([line], works, base=None)).selectable == frozenset()


def test_selectable_of_a_system_needs_a_contract_in_every_group(buildings):
    # v·cls: группа 50 — 2 договора, группа 51 — 1; f: общая группа — 2
    result = run([kp_sys(1, V50), kp_sys(2, V51)], buildings)
    assert only(result, 1).selectable == frozenset({V_CLS, V_ALL, F_CLS, F_ALL})
    assert only(result, 2).selectable == frozenset({V_CLS, V_ALL, F_CLS, F_ALL})


def test_selectable_of_a_system_drops_a_rung_where_one_group_is_empty(buildings):
    # у варианта 52 договоров нет: пустая группа убирает v·cls и v·all у обеих строк семьи
    result = run([kp_sys(1, V50), kp_sys(2, V52)], buildings)
    assert only(result, 1).selectable == frozenset({F_CLS, F_ALL})
    assert only(result, 2).selectable == frozenset({F_CLS, F_ALL})


def test_selectable_of_a_system_line_before_the_family_is_empty(buildings):
    assert only(run([kp_sys(1, V50, None)], buildings)).selectable == frozenset()
    assert only(run([kp_sys(1, V50)], buildings, area=None)).selectable == frozenset()


# --- строки, которых состояние разводит до лестницы ------------------------------------


def test_system_line_resolved_before_the_family_ignores_a_family_record(buildings):
    result = only(run([kp_sys(1, V50, None)], buildings, saved=family_saved(F_CLS)))
    assert result.state is LineState.no_price
    assert result.manual is None


# --- система: ступень семьи -------------------------------------------------------------


def test_family_rung_applies_to_every_system_line_of_the_family(buildings):
    lines = [kp_sys(1, V50, D(12000)), kp_sys(2, V51, D(13000))]
    baseline = run(lines, buildings)
    assert only(baseline, 1).rung == F_CLS and only(baseline, 1).auto is True

    evaluation = run(lines, buildings, saved=family_saved(V_CLS))
    assert len(evaluation.groups) == 2
    for pid in (1, 2):
        result = only(evaluation, pid)
        assert (result.rung, result.auto) == (V_CLS, False)
        assert (result.manual.rung, result.manual.target, result.manual.active) == (V_CLS, "family", True)
        assert result.manual.reset_reason is None
    # группа варианта 50 на v·cls: ставки 100 и 200, наша — минимум 100; группа 51: ставка 150
    assert only(evaluation, 1).ours == D(100)
    assert only(evaluation, 2).ours == D(150)


def test_family_rung_does_not_touch_a_family_without_a_record(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, OTHER_FAMILY)]
    evaluation = run(lines, buildings, saved=family_saved(V_CLS))
    assert only(evaluation, 1).manual is not None
    assert only(evaluation, 2).manual is None


def test_family_record_of_a_family_without_system_lines_changes_nothing(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V51)]
    assert run(lines, buildings, saved=family_saved(V_CLS, family_id=9)) == run(lines, buildings)


def test_family_record_without_any_kp_lines_does_not_fail(buildings):
    assert run([], buildings, saved=family_saved(V_CLS)) == run([], buildings)


def test_family_rung_unavailable_without_first_main_on_every_line(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V51)]
    baseline = run(lines, buildings)
    result = only(run(lines, buildings, saved=family_saved(P_CLS, 7)))
    assert (result.manual.active, result.manual.reset_reason, result.manual.target) == (
        False,
        "rung_unavailable",
        "family",
    )
    assert (result.rung, result.auto) == (baseline.lines[1].rung, True)


def test_family_p_rung_is_unavailable_when_one_line_has_no_first_main():
    index = HistoryIndex(
        [contract(1)],
        [sline(1, KEY50, D(10000)), sline(1, V51, D(15000))],
        factor_by_estimate=None,
    )
    result = only(run([kp_sys(1, KEY50), kp_sys(2, V51)], index, saved=family_saved(P_CLS, 7)))
    assert result.manual.reset_reason == "rung_unavailable"


def test_family_p_rung_applies_when_the_first_parameter_is_the_snapshot_one():
    index = HistoryIndex(
        [contract(1), contract(2)], [sline(1, KEY50, D(10000)), sline(2, KEY51, D(20000))], factor_by_estimate=None
    )
    lines = [kp_sys(1, KEY50), kp_sys(2, KEY51)]
    result = only(run(lines, index, saved=family_saved(P_ALL, 7)))
    assert (result.manual.active, result.manual.reset_reason, result.rung, result.auto) == (True, None, P_ALL, False)


def test_family_p_rung_is_key_changed_when_the_first_parameter_differs():
    index = HistoryIndex(
        [contract(1), contract(2)], [sline(1, KEY50, D(10000)), sline(2, KEY51, D(20000))], factor_by_estimate=None
    )
    lines = [kp_sys(1, KEY50), kp_sys(2, KEY51)]
    baseline = run(lines, index)
    result = only(run(lines, index, saved=family_saved(P_CLS, 8)))
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")
    assert (result.rung, result.auto) == (baseline.lines[1].rung, True)


def test_family_p_rung_is_key_changed_when_only_one_line_has_another_first_parameter():
    other = LineKey(5, 51, (8, 80))
    index = HistoryIndex(
        [contract(1), contract(2)], [sline(1, KEY50, D(10000)), sline(2, other, D(20000))], factor_by_estimate=None
    )
    result = only(run([kp_sys(1, KEY50), kp_sys(2, other)], index, saved=family_saved(P_CLS, 7)))
    assert result.manual.reset_reason == "key_changed"


def test_family_non_p_rung_ignores_the_first_parameter_snapshot(buildings):
    result = only(run([kp_sys(1, V50)], buildings, saved=family_saved(F_CLS, 99)))
    assert result.manual.active is True


def test_family_rung_with_an_empty_group_is_rung_empty(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V52)]
    baseline = run(lines, buildings)
    evaluation = run(lines, buildings, saved=family_saved(V_CLS))
    for pid in (1, 2):
        result = only(evaluation, pid)
        assert (result.manual.active, result.manual.reset_reason) == (False, "rung_empty")
        assert (result.rung, result.auto) == (only(baseline, pid).rung, True)
    # вход по соседству: та же семья на ступени, где пустых групп нет, действует
    assert only(run(lines, buildings, saved=family_saved(F_CLS)), 1).manual.active is True


# --- предпросмотр побеждает сохранённое --------------------------------------------------


def test_choice_wins_over_a_saved_family_rung_and_the_manual_status_stays(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V51)]
    evaluation = run(lines, buildings, saved=family_saved(V_CLS), choice=RungChoice(2, F_ALL))
    for pid in (1, 2):
        result = only(evaluation, pid)
        assert (result.rung, result.auto) == (F_ALL, False)
        assert (result.manual.rung, result.manual.active) == (V_CLS, True)


def test_saved_unavailable_rung_never_raises(buildings):
    run([kp_sys(1, V50)], buildings, saved=family_saved(P_CLS, 7))


# --- соседние причины: вход, где нарушены обе, отдаёт первую по порядку -------------------


def test_family_p_rung_with_another_first_parameter_and_an_empty_group_reports_the_key_first():
    # группа (7, 71) на p договоров не имеет; снимок — параметр 8, у строк семьи — 7
    other_value = LineKey(5, 53, (7, 71))
    index = HistoryIndex([contract(1)], [sline(1, KEY50, D(10000))], factor_by_estimate=None)
    lines = [kp_sys(1, KEY50), kp_sys(2, other_value)]
    result = only(run(lines, index, saved=family_saved(P_CLS, 8)))
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")
    # вход по соседству: тот же параметр в снимке — остаётся только пустая группа
    assert only(run(lines, index, saved=family_saved(P_CLS, 7))).manual.reset_reason == "rung_empty"

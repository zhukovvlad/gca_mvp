"""Ядро «Нашей цены» для файла подрядчику: номинал ставки, сохранённые ступени, статус ручной ступени.

Чистый расчёт без базы (спека 4б §2.5, §2.6, §2.7, §2.18). Эталоны — числа,
посчитанные руками; база НДС 0, площадь объекта тендера и договоров 100.
"""

from __future__ import annotations

from dataclasses import replace
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
    SavedWorkRung,
    evaluate_offer,
    offer_totals,
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

NO_SAVED = SavedRungs(work={}, family={})


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
        kp_vat_base=base,
        tender_area=area,
        tender_class_id=TENDER_CLASS,
        choice=choice,
        saved=saved,
    )


def work_saved(pid: int, rung: Rung, snapshot) -> SavedRungs:
    return SavedRungs(work={pid: SavedWorkRung(rung, snapshot)}, family={})


def family_saved(rung: Rung, first_main_parameter_id: int | None = None, family_id: int = 5) -> SavedRungs:
    return SavedRungs(work={}, family={family_id: SavedFamilyRung(rung, first_main_parameter_id)})


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


def test_selectable_of_a_work_is_the_rungs_with_a_contract(works):
    assert only(run([kp_work(1, KEY50)], works)).selectable == frozenset({V_CLS, V_ALL, P_CLS, P_ALL, F_CLS, F_ALL})


def test_selectable_of_a_work_drops_empty_rungs(works):
    # у варианта 52 договоров нет, но семья и параметр общие с вариантом 50 и 51
    assert only(run([kp_work(1, KEY52)], works)).selectable == frozenset({P_CLS, P_ALL, F_CLS, F_ALL})


def test_selectable_of_a_work_without_first_main_has_no_p_rungs(works):
    assert only(run([kp_work(1, KEY_NO_P)], works)).selectable == frozenset({V_CLS, V_ALL, F_CLS, F_ALL})


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


# --- работа: четыре условия §2.5 -----------------------------------------------------

WORK_REASONS = [
    # точный вариант сменился: снимок 50, у строки теперь 51
    pytest.param(V_CLS, 50, KEY51, "key_changed", id="key_changed"),
    # ступень p при строке без первого главного и пустом снимке: ключа нет, снимок совпал с ним
    pytest.param(P_CLS, None, KEY_NO_P, "rung_unavailable", id="rung_unavailable"),
    # ключ совпал, но на ступени ноль договоров
    pytest.param(V_CLS, 52, KEY52, "rung_empty", id="rung_empty"),
]


@pytest.mark.parametrize(("rung", "snapshot", "key", "reason"), WORK_REASONS)
def test_work_rung_that_does_not_apply_goes_to_auto_and_keeps_the_reason(works, rung, snapshot, key, reason):
    baseline = only(run([kp_work(1, key)], works))
    result = only(run([kp_work(1, key)], works, saved=work_saved(1, rung, snapshot)))
    assert result.manual is not None
    assert (result.manual.rung, result.manual.target, result.manual.active) == (rung, "line", False)
    assert result.manual.reset_reason == reason
    assert result.auto is True
    # расчёт идёт как без сохранённой ступени
    assert result.rung == baseline.rung
    assert (result.ours, result.to_ours, result.bargain, result.state) == (
        baseline.ours,
        baseline.to_ours,
        baseline.bargain,
        baseline.state,
    )


def test_work_rung_with_a_changed_key_and_an_empty_rung_reports_the_key_first(works):
    # снимок 50, у строки вариант 52 без договоров: первым по порядку падает ключ
    result = only(run([kp_work(1, KEY52)], works, saved=work_saved(1, V_CLS, 50)))
    assert result.manual.reset_reason == "key_changed"


def test_work_rung_without_a_key_is_key_changed():
    index = HistoryIndex([contract(1)], [wline(1, KEY50, D(100))], factor_by_estimate=None)
    result = only(run([kp_work(1, None)], index, saved=work_saved(1, F_CLS, 5)))
    assert result.state is LineState.no_variant
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")


def test_work_rung_that_applies_changes_the_rung_the_numbers_and_the_totals(works):
    line = kp_work(1, KEY50)  # цена 150, вес 2
    baseline = only(run([line], works))
    assert (baseline.rung, baseline.auto, baseline.ours, baseline.to_ours) == (V_ALL, True, D(100), D(100))

    evaluation = run([line], works, saved=work_saved(1, F_CLS, 5))
    result = only(evaluation)
    assert (result.manual.rung, result.manual.target, result.manual.active, result.manual.reset_reason) == (
        F_CLS,
        "line",
        True,
        None,
    )
    # f·cls: ставки 100, 200, 80; наша — минимум 80; (150 - 80) * 2 = 140
    assert (result.rung, result.auto, result.ours) == (F_CLS, False, D(80))
    assert (result.to_ours, result.bargain) == (D(140), D(140))
    assert [r.contract_id for r in result.contracts] == [1, 2, 4]
    assert offer_totals(evaluation).bargain == D(140)
    assert offer_totals(run([line], works)).bargain == D(100)


def test_work_rung_of_the_same_family_survives_a_variant_change(works):
    # сохранена «вся семья» (снимок 5): вариант сменился с 50 на 51, ступень действует
    result = only(run([kp_work(1, KEY51)], works, saved=work_saved(1, F_CLS, 5)))
    assert (result.manual.active, result.manual.reset_reason, result.rung, result.auto) == (True, None, F_CLS, False)


def test_work_rung_of_the_exact_variant_does_not_survive_a_variant_change(works):
    # сохранён «точный вариант» (снимок 50): строке назначили вариант 51
    result = only(run([kp_work(1, KEY51)], works, saved=work_saved(1, V_CLS, 50)))
    assert (result.manual.active, result.manual.reset_reason, result.auto) == (False, "key_changed", True)


def test_work_rung_on_the_p_level_is_checked_by_the_family_parameter_and_value(works):
    snapshot = (5, (7, 70))
    assert only(run([kp_work(1, KEY51)], works, saved=work_saved(1, P_CLS, snapshot))).manual.active is True
    other_value = LineKey(5, 51, (7, 71))
    result = only(run([kp_work(1, other_value)], works, saved=work_saved(1, P_CLS, snapshot)))
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")


def test_saved_record_of_another_line_does_not_touch_this_one(works):
    result = only(run([kp_work(1, KEY50)], works, saved=work_saved(2, F_CLS, 5)))
    assert result.manual is None
    assert result.rung == V_ALL


# --- строки, которых состояние разводит до лестницы ------------------------------------


@pytest.mark.parametrize(
    ("line", "base", "state"),
    [
        pytest.param(kp_work(1, KEY50, in_progress=True), D(0), LineState.in_progress, id="in_progress"),
        pytest.param(kp_work(1, KEY50), None, LineState.unknown_vat_base, id="unknown_vat_base"),
        pytest.param(kp_work(1, KEY50, None), D(0), LineState.no_price, id="no_price"),
    ],
)
def test_line_resolved_before_the_ladder_keeps_its_state_and_reports_the_manual_status(works, line, base, state):
    baseline = only(run([line], works, base=base))
    result = only(run([line], works, base=base, saved=work_saved(1, F_CLS, 5)))
    assert result.state is state
    assert result.manual is not None
    assert (result.manual.rung, result.manual.target, result.manual.active) == (F_CLS, "line", True)
    assert result.manual.reset_reason is None
    assert (result.rung, result.auto, result.ours, result.selectable) == (None, True, None, frozenset())
    assert result == _with_manual(baseline, result.manual)


def _with_manual(result: LineResult, manual) -> LineResult:
    return replace(result, manual=manual)


def test_line_resolved_before_the_ladder_checks_the_key_of_the_saved_rung(works):
    line = kp_work(1, KEY51, in_progress=True)
    result = only(run([line], works, saved=work_saved(1, V_CLS, 50)))
    assert result.state is LineState.in_progress
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")


def test_line_resolved_before_the_ladder_checks_that_the_rung_has_contracts(works):
    line = kp_work(1, KEY52, None)
    result = only(run([line], works, saved=work_saved(1, V_CLS, 52)))
    assert result.state is LineState.no_price
    assert (result.manual.active, result.manual.reset_reason) == (False, "rung_empty")


def test_line_without_a_key_before_the_ladder_is_key_changed(works):
    result = only(run([kp_work(1, None)], works, saved=work_saved(1, F_CLS, 5)))
    assert result.state is LineState.no_variant
    assert result.manual.reset_reason == "key_changed"


@pytest.mark.parametrize("area", [pytest.param(D(100), id="no_price"), pytest.param(None, id="no_area")])
def test_system_line_resolved_before_the_family_has_a_line_record_as_kind_changed(buildings, area):
    total = None if area is not None else D(12000)
    line = kp_sys(1, V50, total)
    saved = SavedRungs(work={1: SavedWorkRung(V_CLS, 50)}, family={5: SavedFamilyRung(F_CLS, None)})
    result = only(run([line], buildings, saved=saved, area=area))
    assert result.state in (LineState.no_price, LineState.no_area)
    assert (result.manual.rung, result.manual.target, result.manual.active) == (V_CLS, "line", False)
    assert result.manual.reset_reason == "kind_changed"


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


def test_work_rung_of_a_line_that_became_a_system_is_kind_changed_and_the_family_rung_applies(buildings):
    saved = SavedRungs(work={1: SavedWorkRung(F_ALL, 5)}, family={5: SavedFamilyRung(V_CLS, None)})
    evaluation = run([kp_sys(1, V50), kp_sys(2, V51)], buildings, saved=saved)
    own = only(evaluation, 1)
    assert (own.manual.rung, own.manual.target, own.manual.active, own.manual.reset_reason) == (
        F_ALL,
        "line",
        False,
        "kind_changed",
    )
    assert (own.rung, own.auto) == (V_CLS, False)
    sibling = only(evaluation, 2)
    assert (sibling.manual.target, sibling.manual.active, sibling.rung) == ("family", True, V_CLS)


def test_work_rung_of_a_line_that_became_a_system_without_a_family_rung_goes_to_auto(buildings):
    saved = work_saved(1, F_ALL, 5)
    evaluation = run([kp_sys(1, V50), kp_sys(2, V51)], buildings, saved=saved)
    own = only(evaluation, 1)
    assert (own.manual.target, own.manual.active, own.manual.reset_reason) == ("line", False, "kind_changed")
    assert (own.rung, own.auto) == (F_CLS, True)
    assert only(evaluation, 2).manual is None


# --- предпросмотр побеждает сохранённое --------------------------------------------------


def test_choice_wins_over_a_saved_work_rung_and_the_manual_status_stays(works):
    line = kp_work(1, KEY50)
    result = only(run([line], works, saved=work_saved(1, F_CLS, 5), choice=RungChoice(1, V_ALL)))
    assert (result.rung, result.auto) == (V_ALL, False)
    assert (result.manual.rung, result.manual.active) == (F_CLS, True)
    assert result.ours == D(100)


def test_choice_of_another_line_does_not_win_over_this_saved_rung(works):
    lines = [kp_work(1, KEY50), kp_work(2, KEY50)]
    evaluation = run(lines, works, saved=work_saved(1, F_CLS, 5), choice=RungChoice(2, V_ALL))
    assert (only(evaluation, 1).rung, only(evaluation, 1).auto) == (F_CLS, False)
    assert (only(evaluation, 2).rung, only(evaluation, 2).auto) == (V_ALL, False)


def test_choice_wins_over_a_saved_family_rung_and_the_manual_status_stays(buildings):
    lines = [kp_sys(1, V50), kp_sys(2, V51)]
    evaluation = run(lines, buildings, saved=family_saved(V_CLS), choice=RungChoice(2, F_ALL))
    for pid in (1, 2):
        result = only(evaluation, pid)
        assert (result.rung, result.auto) == (F_ALL, False)
        assert (result.manual.rung, result.manual.active) == (V_CLS, True)


def test_choice_still_raises_for_a_rung_the_line_does_not_have(works):
    with pytest.raises(ValueError):
        run([kp_work(1, KEY_NO_P)], works, choice=RungChoice(1, P_CLS))


def test_saved_unavailable_rung_never_raises(works, buildings):
    run([kp_work(1, KEY_NO_P)], works, saved=work_saved(1, P_CLS, None))
    run([kp_sys(1, V50)], buildings, saved=family_saved(P_CLS, 7))


# --- соседние причины: вход, где нарушены обе, отдаёт первую по порядку -------------------


def test_work_rung_with_a_changed_key_on_an_unavailable_rung_reports_the_key_first(works):
    # снимок уровня p сделан при первом главном параметре; у строки его больше нет:
    # ключ на уровне p пуст и не равен снимку, и ступени p среди доступных нет
    result = only(run([kp_work(1, KEY_NO_P)], works, saved=work_saved(1, P_CLS, (5, (7, 70)))))
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")


def test_system_line_before_the_family_reports_kind_changed_before_the_key(buildings):
    # запись строки сделана на варианте 51, строка стала системой варианта 50: сменились и вид, и ключ
    result = only(run([kp_sys(1, V50, None)], buildings, saved=work_saved(1, V_CLS, 51)))
    assert result.state is LineState.no_price
    assert (result.manual.active, result.manual.reset_reason) == (False, "kind_changed")


def test_system_line_of_the_family_reports_kind_changed_before_the_key(buildings):
    evaluation = run([kp_sys(1, V50), kp_sys(2, V51)], buildings, saved=work_saved(1, V_CLS, 51))
    assert (only(evaluation, 1).manual.active, only(evaluation, 1).manual.reset_reason) == (False, "kind_changed")


def test_family_p_rung_with_another_first_parameter_and_an_empty_group_reports_the_key_first():
    # группа (7, 71) на p договоров не имеет; снимок — параметр 8, у строк семьи — 7
    other_value = LineKey(5, 53, (7, 71))
    index = HistoryIndex([contract(1)], [sline(1, KEY50, D(10000))], factor_by_estimate=None)
    lines = [kp_sys(1, KEY50), kp_sys(2, other_value)]
    result = only(run(lines, index, saved=family_saved(P_CLS, 8)))
    assert (result.manual.active, result.manual.reset_reason) == (False, "key_changed")
    # вход по соседству: тот же параметр в снимке — остаётся только пустая группа
    assert only(run(lines, index, saved=family_saved(P_CLS, 7))).manual.reset_reason == "rung_empty"


# --- счёт договоров ступени и края без истории ---------------------------------------------


def test_work_rung_count_respects_the_class_of_the_rung():
    # у варианта 50 только договор иного класса: на v·cls договоров нет, на v·all — один
    index = HistoryIndex([contract(3, cls=OTHER_CLASS)], [wline(3, KEY50, D(300))], factor_by_estimate=None)
    in_class = only(run([kp_work(1, KEY50)], index, saved=work_saved(1, V_CLS, 50)))
    assert (in_class.manual.active, in_class.manual.reset_reason) == (False, "rung_empty")
    every_class = only(run([kp_work(1, KEY50)], index, saved=work_saved(1, V_ALL, 50)))
    assert (every_class.manual.active, every_class.rung, every_class.auto) == (True, V_ALL, False)


def test_work_line_without_any_history_keeps_the_manual_status():
    # договоры есть только у другой семьи: у строки ни одной ступени с договорами
    index = HistoryIndex([contract(1)], [wline(1, OTHER_FAMILY, D(100))], factor_by_estimate=None)
    result = only(run([kp_work(1, KEY50)], index, saved=work_saved(1, F_CLS, 5)))
    assert result.state is LineState.no_history
    assert (result.manual.rung, result.manual.active, result.manual.reset_reason) == (F_CLS, False, "rung_empty")
    assert result.selectable == frozenset()


def test_choice_of_an_empty_rung_keeps_selectable_and_the_manual_status(works):
    # у варианта 52 на v·cls договоров нет: предпросмотр этой ступени даёт «нет в договорах»
    result = only(run([kp_work(1, KEY52)], works, saved=work_saved(1, F_CLS, 5), choice=RungChoice(1, V_CLS)))
    assert (result.state, result.rung, result.auto) == (LineState.no_history, V_CLS, False)
    assert result.selectable == frozenset({P_CLS, P_ALL, F_CLS, F_ALL})
    assert (result.manual.rung, result.manual.active) == (F_CLS, True)

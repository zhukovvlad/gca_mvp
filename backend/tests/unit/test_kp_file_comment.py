"""Шаблонный комментарий строки КП: случай, цель, договор-источник, текст.

Чистый расчёт без базы (спека 4б §2.2, §2.3, §2.7, §2.8). Эталоны — числа и
строки, посчитанные руками; площадь объекта тендера 100.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal

import pytest

from services.kp_comment import (
    CommentInputs,
    InflationLabel,
    SourceContract,
    comment_case,
    format_rub,
    pick_source,
    target_net,
    target_sum_net,
    template_text,
)
from services.our_price import (
    ContractRate,
    HistoryContract,
    HistoryIndex,
    LineResult,
    LineState,
    Mismatch,
    RateRange,
    Rung,
)

D = Decimal

TENDER_CLASS = 1
OTHER_CLASS = 2
TENDER_CONTRACTOR = 7
OTHER_CONTRACTOR = 8
AREA = D(100)


def hist_contract(
    cid: int,
    *,
    cls: int = TENDER_CLASS,
    contractor: int = OTHER_CONTRACTOR,
    year: int = 2024,
    number: str = "Д-1",
    title: str = "Объект Т",
) -> HistoryContract:
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 10,
        number=number,
        object_title=title,
        year=year,
        rate_class_id=cls,
        rate_class_title=f"Класс {cls}",
        contractor_id=contractor,
        contractor_title=f"Подрядчик {contractor}",
        area=AREA,
    )


def rate(cid: int, net: str, *, nominal: str | None = None, lines: int = 1) -> ContractRate:
    return ContractRate(contract_id=cid, net=D(net), nominal=D(nominal if nominal is not None else net), lines=lines)


def result(**overrides) -> LineResult:
    values = dict(
        position_item_id=1,
        state=LineState.comparable,
        kp_unit_net=D(120),
        kp_total_net=D(240),
        rung=Rung("v", "cls"),
        auto=True,
        threshold_met=True,
        rungs=(),
        contracts=(),
        range=None,
        ours=D(100),
        ours_from_all_classes=False,
        position="above",
        to_ours=None,
        bargain=None,
        below=None,
        group_index=None,
        group_lines=None,
        share=None,
        group_per_sqm=None,
        mismatch=None,
    )
    values.update(overrides)
    return LineResult(**values)


def source(**overrides) -> SourceContract:
    contract_fields = {k: overrides.pop(k) for k in ("contractor", "year", "title") if k in overrides}
    return SourceContract(
        contract=hist_contract(1, **contract_fields),
        rate=overrides.pop("rate", rate(1, "100")),
    )


def inputs(**overrides) -> CommentInputs:
    values = dict(
        result=result(),
        is_system=False,
        unit_text="м2",
        kp_unit_net=D(120),
        kp_total_net=D(240),
        quantity=D(2),
        agreed_net=None,
        level="v",
        source=source(),
        tender_contractor_id=TENDER_CONTRACTOR,
        tender_area=AREA,
        inflation=None,
        vat_base=D(0),
    )
    values.update(overrides)
    return CommentInputs(**values)


def system_inputs(**overrides) -> CommentInputs:
    """Строка системы: наша 10 руб/м2, площадь 100, доля 1/2 — целевая сумма 500,
    объём 4 — цель 125 за единицу. Сумма КП 520, цена за единицу 130."""
    res = result(
        kp_unit_net=D(130),
        kp_total_net=D(520),
        ours=D(10),
        share=D("0.5"),
        position="above",
    )
    values = dict(
        result=res,
        is_system=True,
        unit_text="шт",
        kp_unit_net=D(130),
        kp_total_net=D(520),
        quantity=D(4),
        source=source(rate=rate(1, "10")),
    )
    values.update(overrides)
    return inputs(**values)


def with_result(inp: CommentInputs, **overrides) -> CommentInputs:
    return replace(inp, result=replace(inp.result, **overrides))


def mismatch(kp_ratio: str | None) -> Mismatch:
    return Mismatch(kp_ratio=D(kp_ratio) if kp_ratio is not None else None, contracts=())


# --- format_rub -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("1228314.5"), "1 228 314,50"),
        (D("34154.004"), "34 154,00"),
        (D("34154.005"), "34 154,01"),
        (D("0.005"), "0,01"),
        (D("999.995"), "1 000,00"),
        (D(1000), "1 000,00"),
        (D(0), "0,00"),
        (D("-1234.5"), "-1 234,50"),
        (D("12345678901234567890.126"), "12 345 678 901 234 567 890,13"),
    ],
)
def test_kp_file_comment_format_rub(value, expected):
    assert format_rub(value) == expected


# --- случай комментария: таблица §2.7 ---------------------------------------------


def test_kp_file_comment_case_row1_agreed_when_kp_above_agreed():
    assert comment_case(inputs(agreed_net=D(100))) == "agreed"


def test_kp_file_comment_case_row1_applies_to_a_system_too():
    assert comment_case(system_inputs(agreed_net=D(100))) == "agreed"


def test_kp_file_comment_case_row1_beats_every_state():
    base = inputs(agreed_net=D(100), result=result(state=LineState.no_history, ours=None, position=None))
    assert comment_case(base) == "agreed"
    base = inputs(agreed_net=D(100), result=result(state=LineState.unit_mismatch, mismatch=mismatch("7")))
    assert comment_case(base) == "agreed"


@pytest.mark.parametrize(
    ("kp", "expected"),
    [
        (D("100.004"), None),  # равно в копейках
        (D("100.005"), "agreed"),  # половина вверх: 100,01
        (D("100.01"), "agreed"),
        (D("99.99"), None),
    ],
)
def test_kp_file_comment_case_agreed_compares_in_kopecks(kp, expected):
    assert comment_case(inputs(agreed_net=D("100.00"), kp_unit_net=kp)) == expected


def test_kp_file_comment_case_agreed_rounds_the_agreed_side_too():
    # 99,996 -> 100,00: КП 100,004 -> 100,00, равенство
    assert comment_case(inputs(agreed_net=D("99.996"), kp_unit_net=D("100.004"))) is None


@pytest.mark.parametrize("kp", [None, D("NaN"), D("Infinity"), D(0), D(-5)])
def test_kp_file_comment_case_agreed_needs_a_valid_kp_price(kp):
    # без годной цены за единицу комментария нет, хотя строка сравнима и выше цели
    inp = inputs(agreed_net=D(50), kp_unit_net=kp)
    assert comment_case(inp) is None


def test_kp_file_comment_case_row2_agreed_beats_over_and_below():
    # КП 120 выше нашей 100 (случай over), но договорились на 130 — комментария нет
    assert comment_case(inputs(agreed_net=D(130))) is None
    below = inputs(agreed_net=D(130), result=result(position="below"))
    assert comment_case(below) is None


def test_kp_file_comment_case_row3_kp_unit():
    inp = inputs(result=result(state=LineState.unit_mismatch, mismatch=mismatch("12.5"), ours=None, position=None))
    assert comment_case(inp) == "kp_unit"


def test_kp_file_comment_case_row4_history_unit():
    inp = inputs(result=result(state=LineState.unit_mismatch, mismatch=mismatch(None), ours=None, position=None))
    assert comment_case(inp) == "history_unit"


def test_kp_file_comment_case_row5_below():
    assert comment_case(inputs(result=result(position="below", kp_unit_net=D(50)))) == "below"


def test_kp_file_comment_case_row6_over():
    assert comment_case(inputs()) == "over"


@pytest.mark.parametrize(
    ("kp", "ours", "expected"),
    [
        (D("34154.004"), D("34154.00"), None),
        (D("34154.01"), D("34154.00"), "over"),
        (D("34154.005"), D("34154.00"), "over"),  # половина вверх
        (D("34154.00"), D("34153.996"), None),  # цель тоже округляется: 34154,00
        (D("34153.99"), D("34154.00"), None),  # знак разности в другую сторону
    ],
)
def test_kp_file_comment_case_work_compares_in_kopecks(kp, ours, expected):
    inp = inputs(kp_unit_net=kp, result=result(kp_unit_net=kp, ours=ours, position="in"))
    assert comment_case(inp) == expected


@pytest.mark.parametrize(
    "state",
    [
        LineState.in_progress,
        LineState.unknown_vat_base,
        LineState.no_price,
        LineState.no_area,
        LineState.no_variant,
        LineState.no_history,
    ],
)
def test_kp_file_comment_case_row7_states_without_comparison(state):
    inp = inputs(result=result(state=state, ours=None, position=None))
    assert comment_case(inp) is None


def test_kp_file_comment_case_row7_in_the_range_at_or_below_target():
    inp = inputs(kp_unit_net=D(90), result=result(kp_unit_net=D(90), position="in"))
    assert comment_case(inp) is None


# --- пары соседних строк §2.7: вход, где верны обе, отдаёт верхнюю ----------------


@pytest.mark.parametrize("kp_ratio", ["12", None])
def test_kp_file_comment_case_pair_rows_2_and_3_4_agreed_not_above_beats_unit_mismatch(kp_ratio):
    # договорились на 130, КП 120 не выше — строка 2; строка 3 или 4 тоже верна
    res = result(state=LineState.unit_mismatch, mismatch=mismatch(kp_ratio), ours=None, position=None)
    assert comment_case(inputs(agreed_net=D(130), result=res)) is None
    assert comment_case(inputs(agreed_net=D(130), kp_unit_net=None, result=res)) is None


@pytest.mark.parametrize("is_system", [False, True])
def test_kp_file_comment_case_pair_rows_5_and_6_below_beats_over(is_system):
    # положение below и КП выше цели одновременно: отдаётся верхняя строка 5
    inp = system_inputs() if is_system else inputs()
    inp = with_result(inp, position="below")
    assert comment_case(inp) == "below"
    assert comment_case(with_result(inp, position="above")) == "over"


@pytest.mark.parametrize(
    "state",
    [
        LineState.in_progress,
        LineState.unknown_vat_base,
        LineState.no_price,
        LineState.no_area,
        LineState.no_variant,
        LineState.no_history,
    ],
)
@pytest.mark.parametrize("is_system", [False, True])
def test_kp_file_comment_case_row5_needs_comparable_state(state, is_system):
    # строка 5 — «comparable, положение below»: положение без сравнимого состояния
    # случая не даёт
    inp = system_inputs() if is_system else inputs()
    assert comment_case(with_result(inp, state=state, position="below")) is None


# --- система: решает только сумма строки ------------------------------------------


def test_kp_file_comment_case_system_over_by_total():
    assert comment_case(system_inputs()) == "over"


def test_kp_file_comment_case_system_total_equal_in_kopecks_is_none():
    # целевая сумма 500,00; сумма КП 500,004 — равенство
    inp = system_inputs(kp_total_net=D("500.004"))
    assert comment_case(inp) is None


def test_kp_file_comment_case_system_counterexample_total_decides_not_unit():
    # объём 100, цель 1 руб/м2 * 100 * 1 = 100,00; сумма КП 100,40.
    # За единицу 1,004 и 1,00 равны в копейках, а по суммам КП выше: over.
    res = result(kp_unit_net=D("1.004"), kp_total_net=D("100.40"), ours=D(1), share=D(1))
    inp = system_inputs(result=res, kp_unit_net=D("1.004"), kp_total_net=D("100.40"), quantity=D(100))
    assert comment_case(inp) == "over"


def test_kp_file_comment_case_system_counterexample_other_side():
    # та же цель 100,00; сумма КП 100,004 — по суммам равно в копейках
    res = result(kp_unit_net=D("1.00004"), kp_total_net=D("100.004"), ours=D(1), share=D(1))
    inp = system_inputs(result=res, kp_unit_net=D("1.00004"), kp_total_net=D("100.004"), quantity=D(100))
    assert comment_case(inp) is None


def test_kp_file_comment_case_system_unit_price_above_target_does_not_make_over():
    # сумма равна цели (500,00), а цена за единицу (130 против 125) выше: не over
    res = result(kp_unit_net=D(130), kp_total_net=D(500), ours=D(10), share=D("0.5"))
    inp = system_inputs(result=res, kp_total_net=D(500))
    assert comment_case(inp) is None


@pytest.mark.parametrize("quantity", [D("NaN"), None, D(0)])
def test_kp_file_comment_case_system_without_valid_volume_still_over_by_total(quantity):
    inp = system_inputs(quantity=quantity, kp_unit_net=None)
    inp = with_result(inp, kp_unit_net=None)
    assert comment_case(inp) == "over"


def test_kp_file_comment_case_work_without_kp_price_or_ours_is_none():
    assert comment_case(inputs(kp_unit_net=None)) is None
    assert comment_case(inputs(result=result(ours=None))) is None


def test_kp_file_comment_case_system_without_tender_area_has_no_target():
    assert comment_case(system_inputs(tender_area=None)) is None


def test_kp_file_comment_case_system_with_invalid_kp_total_is_none():
    assert comment_case(system_inputs(kp_total_net=None)) is None


# --- цель строки и целевая сумма ---------------------------------------------------


def test_kp_file_comment_target_agreed_wins_in_any_state():
    inp = inputs(agreed_net=D("77.7"), result=result(state=LineState.no_history, ours=None, position=None))
    assert target_net(inp) == D("77.7")


def test_kp_file_comment_target_agreed_wins_over_ours_and_for_a_system_without_volume():
    assert target_net(inputs(agreed_net=D("77.7"))) == D("77.7")
    assert target_net(system_inputs(agreed_net=D("77.7"), quantity=D("NaN"))) == D("77.7")


def test_kp_file_comment_target_work_is_ours():
    assert target_net(inputs()) == D(100)


@pytest.mark.parametrize(
    "state",
    [
        LineState.in_progress,
        LineState.unknown_vat_base,
        LineState.no_price,
        LineState.no_area,
        LineState.no_variant,
        LineState.no_history,
        LineState.unit_mismatch,
    ],
)
def test_kp_file_comment_target_none_without_agreed_outside_comparable(state):
    inp = inputs(result=result(state=state, ours=D(100), position=None))
    assert target_net(inp) is None


def test_kp_file_comment_target_system_is_target_sum_over_volume():
    # 10 * 100 * 0,5 = 500; объём 4 -> 125
    assert target_net(system_inputs()) == D(125)


@pytest.mark.parametrize("quantity", [None, D("NaN"), D(0), D(-3)])
def test_kp_file_comment_target_system_without_valid_volume_is_none(quantity):
    assert target_net(system_inputs(quantity=quantity)) is None


def test_kp_file_comment_target_system_unit_target_is_not_rounded():
    # 500 / 3 без промежуточного округления
    value = target_net(system_inputs(quantity=D(3)))
    assert abs(value - D("166.66666666666666666666666667")) < D("1e-26")
    assert value != D("166.67")


def test_kp_file_comment_target_sum_system_comparable():
    assert target_sum_net(system_inputs()) == D(500)


def test_kp_file_comment_target_sum_independent_of_agreed():
    assert target_sum_net(system_inputs(agreed_net=D(999))) == D(500)


def test_kp_file_comment_target_sum_uses_share_and_area_exactly():
    res = result(ours=D("12.5"), share=D("0.25"), kp_total_net=D(900))
    inp = system_inputs(result=res, tender_area=D(80))
    assert target_sum_net(inp) == D(250)


def test_kp_file_comment_target_sum_none_for_work():
    assert target_sum_net(inputs()) is None


def test_kp_file_comment_target_sum_none_outside_comparable():
    assert target_sum_net(with_result(system_inputs(), state=LineState.unit_mismatch)) is None


def test_kp_file_comment_target_sum_none_without_ours():
    assert target_sum_net(with_result(system_inputs(), ours=None)) is None


@pytest.mark.parametrize("area", [None, D("NaN"), D(0)])
def test_kp_file_comment_target_sum_none_without_valid_area(area):
    assert target_sum_net(system_inputs(tender_area=area)) is None


# --- договор-источник -------------------------------------------------------------


def make_history(*contracts: HistoryContract) -> HistoryIndex:
    return HistoryIndex(list(contracts), [], factor_by_estimate=None)


def pick(res: LineResult, history: HistoryIndex) -> SourceContract | None:
    return pick_source(res, history, tender_class_id=TENDER_CLASS, tender_contractor_id=TENDER_CONTRACTOR)


def test_kp_file_comment_pick_source_takes_the_contract_that_gave_ours():
    history = make_history(hist_contract(1), hist_contract(2), hist_contract(3))
    res = result(ours=D(100), contracts=(rate(1, "120"), rate(2, "100"), rate(3, "150")))
    picked = pick(res, history)
    assert picked.contract.contract_id == 2
    assert picked.rate == rate(2, "100")


def test_kp_file_comment_pick_source_rate_is_the_ladder_rate_with_nominal_and_lines():
    history = make_history(hist_contract(1))
    res = result(ours=D(110), contracts=(rate(1, "110", nominal="100", lines=3),))
    picked = pick(res, history)
    assert picked.rate.net == D(110)
    assert picked.rate.nominal == D(100)
    assert picked.rate.lines == 3


def test_kp_file_comment_pick_source_none_without_ours():
    history = make_history(hist_contract(1))
    assert pick(result(ours=None, contracts=(rate(1, "100"),)), history) is None


def test_kp_file_comment_pick_source_none_when_no_rate_equals_ours():
    history = make_history(hist_contract(1))
    assert pick(result(ours=D(100), contracts=(rate(1, "100.01"),)), history) is None


def test_kp_file_comment_pick_source_other_class_only_with_all_classes():
    # договор другого класса — того же подрядчика, позже и с меньшим номером:
    # без «всех классов» он не кандидат, со «всеми» — выигрывает
    history = make_history(
        hist_contract(5, cls=TENDER_CLASS, year=2020),
        hist_contract(2, cls=OTHER_CLASS, year=2025, contractor=TENDER_CONTRACTOR),
    )
    contracts = (rate(5, "110"), rate(2, "110"))
    own = pick(result(ours=D(110), contracts=contracts, ours_from_all_classes=False), history)
    assert own.contract.contract_id == 5
    wide = pick(result(ours=D(110), contracts=contracts, ours_from_all_classes=True), history)
    assert wide.contract.contract_id == 2


def test_kp_file_comment_pick_source_all_classes_allows_a_cheaper_other_class():
    history = make_history(hist_contract(1, cls=TENDER_CLASS), hist_contract(2, cls=OTHER_CLASS))
    res = result(ours=D(90), contracts=(rate(1, "100"), rate(2, "90")), ours_from_all_classes=True)
    assert pick(res, history).contract.contract_id == 2


def test_kp_file_comment_pick_source_tie_prefers_the_tender_contractor():
    history = make_history(
        hist_contract(1, year=2025, contractor=OTHER_CONTRACTOR),
        hist_contract(2, year=2020, contractor=TENDER_CONTRACTOR),
    )
    res = result(ours=D(100), contracts=(rate(1, "100"), rate(2, "100")))
    assert pick(res, history).contract.contract_id == 2


def test_kp_file_comment_pick_source_tie_then_later_year():
    history = make_history(hist_contract(1, year=2021), hist_contract(2, year=2023), hist_contract(3, year=2022))
    res = result(ours=D(100), contracts=(rate(1, "100"), rate(2, "100"), rate(3, "100")))
    assert pick(res, history).contract.contract_id == 2


def test_kp_file_comment_pick_source_tie_then_smaller_contract_id():
    history = make_history(hist_contract(9, year=2023), hist_contract(4, year=2023), hist_contract(6, year=2023))
    res = result(ours=D(100), contracts=(rate(9, "100"), rate(4, "100"), rate(6, "100")))
    assert pick(res, history).contract.contract_id == 4


def test_kp_file_comment_pick_source_rate_equality_beats_the_tie_breakers():
    # договор подрядчика участника и поздний, но его ставка не равна нашей
    history = make_history(
        hist_contract(1, year=2020, contractor=OTHER_CONTRACTOR),
        hist_contract(2, year=2025, contractor=TENDER_CONTRACTOR),
    )
    res = result(ours=D(100), contracts=(rate(1, "100"), rate(2, "130")))
    assert pick(res, history).contract.contract_id == 1


# --- тексты: работа ----------------------------------------------------------------

WORK_OVER = (
    "Ваша цена — 120,00 ₽ за м2 без НДС. В нашем договоре по объекту Объект Т, 2024 г., "
    "эта работа стоила 100,00 ₽ за м2 без НДС. Просим снизить до 100,00 ₽ или обосновать разницу."
)


def test_kp_file_comment_text_work_over_exact():
    assert template_text(inputs()) == WORK_OVER


def test_kp_file_comment_text_work_over_with_you():
    inp = inputs(source=source(contractor=TENDER_CONTRACTOR))
    assert "В нашем договоре с вами по объекту Объект Т, 2024 г., эта работа стоила" in template_text(inp)


def test_kp_file_comment_text_work_over_not_with_you_for_another_contractor():
    assert "с вами" not in template_text(inputs())


def test_kp_file_comment_text_work_over_uses_year_and_object_of_the_source():
    inp = inputs(source=source(year=2019, title="Объект Ж"))
    text = template_text(inp)
    assert "по объекту Объект Ж, 2019 г.," in text


@pytest.mark.parametrize("level", ["p", "f"])
def test_kp_file_comment_text_work_over_comparable_works_on_p_and_f(level):
    text = template_text(inputs(level=level))
    assert "сопоставимые работы стоили 100,00 ₽ за м2" in text
    assert "эта работа" not in text


def test_kp_file_comment_text_work_over_this_work_on_v():
    text = template_text(inputs(level="v"))
    assert "эта работа стоила 100,00" in text
    assert "сопоставимые" not in text


def test_kp_file_comment_text_work_on_average_only_from_two_lines():
    two = template_text(inputs(source=source(rate=rate(1, "100", lines=2))))
    assert "эта работа стоила в среднем 100,00 ₽" in two
    one = template_text(inputs(source=source(rate=rate(1, "100", lines=1))))
    assert "в среднем" not in one
    many = template_text(inputs(level="p", source=source(rate=rate(1, "100", lines=5))))
    assert "сопоставимые работы стоили в среднем 100,00 ₽" in many


def test_kp_file_comment_text_work_over_inflation_tail():
    inp = inputs(
        result=result(ours=D(110)),
        source=source(rate=rate(1, "110", nominal="100")),
        inflation=InflationLabel("ИПЦ строительства", "09.2026"),
    )
    assert template_text(inp) == (
        "Ваша цена — 120,00 ₽ за м2 без НДС. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта работа стоила 100,00 ₽ за м2 без НДС; с учётом инфляции (ИПЦ строительства, к 09.2026) — "
        "110,00 ₽. Просим снизить до 110,00 ₽ или обосновать разницу."
    )


def test_kp_file_comment_text_work_no_inflation_tail_without_series():
    inp = inputs(result=result(ours=D(110)), source=source(rate=rate(1, "110", nominal="100")), inflation=None)
    text = template_text(inp)
    assert "инфляции" not in text
    assert "стоила 100,00 ₽" in text  # цитата — номинал
    assert "снизить до 110,00 ₽" in text  # цель — с рядом


def test_kp_file_comment_text_work_no_inflation_tail_at_factor_one():
    inp = inputs(source=source(rate=rate(1, "100", nominal="100")), inflation=InflationLabel("ИПЦ", "09.2026"))
    assert "инфляции" not in template_text(inp)


def test_kp_file_comment_text_work_factor_below_one_also_gets_the_tail():
    inp = inputs(
        result=result(ours=D(90), kp_unit_net=D(95)),
        kp_unit_net=D(95),
        source=source(rate=rate(1, "90", nominal="100")),
        inflation=InflationLabel("ИПЦ", "01.2026"),
    )
    text = template_text(inp)
    assert "стоила 100,00 ₽ за м2 без НДС; с учётом инфляции (ИПЦ, к 01.2026) — 90,00 ₽." in text


def test_kp_file_comment_text_agreed():
    inp = inputs(agreed_net=D("100"))
    assert template_text(inp) == "Согласовано: 100,00 ₽ за м2 без НДС. Просим отразить в следующей редакции КП."


def test_kp_file_comment_text_agreed_system_is_per_unit():
    text = template_text(system_inputs(agreed_net=D("100"), quantity=D("NaN")))
    assert text == "Согласовано: 100,00 ₽ за шт без НДС. Просим отразить в следующей редакции КП."


def test_kp_file_comment_text_agreed_uses_the_agreed_number_not_ours():
    text = template_text(inputs(agreed_net=D("98.5")))
    assert "98,50 ₽" in text
    assert "100,00" not in text


def test_kp_file_comment_text_below():
    inp = inputs(kp_unit_net=D(50), result=result(position="below", kp_unit_net=D(50)))
    assert template_text(inp) == (
        "Просим подтвердить, что в цену 50,00 ₽ за м2 без НДС включены все работы и материалы по ведомости."
    )


def test_kp_file_comment_text_kp_unit():
    inp = inputs(result=result(state=LineState.unit_mismatch, mismatch=mismatch("12"), ours=None, position=None))
    assert template_text(inp) == (
        "Цена 120,00 ₽ за м2 без НДС похожа на цену за весь объём, а не за единицу. "
        "Уточните, пожалуйста, единицу учёта."
    )


@pytest.mark.parametrize("quantity", [None, D("NaN"), D(0)])
def test_kp_file_comment_text_kp_unit_of_a_work_does_not_depend_on_volume(quantity):
    # новая фраза — только у системы; у работы текст kp_unit прежний при любом объёме
    inp = inputs(
        quantity=quantity,
        result=result(state=LineState.unit_mismatch, mismatch=mismatch("12"), ours=None, position=None),
    )
    assert template_text(inp) == (
        "Цена 120,00 ₽ за м2 без НДС похожа на цену за весь объём, а не за единицу. "
        "Уточните, пожалуйста, единицу учёта."
    )


def test_kp_file_comment_text_history_unit():
    inp = inputs(result=result(state=LineState.unit_mismatch, mismatch=mismatch(None), ours=None, position=None))
    assert template_text(inp) == (
        "Просим расшифровать, что входит в цену 120,00 ₽ за м2 без НДС и за какую единицу она дана."
    )


def test_kp_file_comment_text_none_without_a_case():
    assert template_text(inputs(kp_unit_net=D(90), result=result(kp_unit_net=D(90), position="in"))) is None


def test_kp_file_comment_text_none_for_over_without_a_source():
    assert template_text(inputs(source=None)) is None


# --- тексты: система, три формы ----------------------------------------------------

SYSTEM_TAIL = (
    "В нашем договоре по объекту Объект Т, 2024 г., эта система стоила 10,00 ₽ на м² объекта без НДС — "
    "для вашего объекта это 500,00 ₽ за весь объём"
)


def test_kp_file_comment_text_system_form1_volume_and_unit_price_valid():
    assert template_text(system_inputs()) == (
        "Ваша цена — 130,00 ₽ за шт без НДС. " + SYSTEM_TAIL + " (4 шт), то есть 125,00 ₽ за шт. "
        "Просим снизить до 125,00 ₽ за шт или обосновать разницу."
    )


def test_kp_file_comment_text_system_form2_volume_valid_unit_price_not():
    inp = system_inputs(kp_unit_net=None)
    assert template_text(inp) == (
        "Ваша цена — 520,00 ₽ за весь объём (4 шт) без НДС. " + SYSTEM_TAIL + " (4 шт), то есть 125,00 ₽ за шт. "
        "Просим снизить до 125,00 ₽ за шт или обосновать разницу."
    )


@pytest.mark.parametrize("quantity", [D("NaN"), None, D(0)])
def test_kp_file_comment_text_system_form3_volume_not_valid(quantity):
    # для формы 3 цена за единицу годна — выбор формы от неё не зависит
    inp = system_inputs(quantity=quantity)
    text = template_text(inp)
    assert text == (
        "Ваша цена — 520,00 ₽ за весь объём без НДС. " + SYSTEM_TAIL + ". "
        "Просим снизить до 500,00 ₽ за весь объём или обосновать разницу."
    )
    assert "за единицу" not in text
    assert "за шт" not in text
    assert "то есть" not in text


def test_kp_file_comment_text_system_form2_with_invalid_unit_price_values():
    for bad in (D("NaN"), D(0)):
        text = template_text(system_inputs(kp_unit_net=bad))
        assert text.startswith("Ваша цена — 520,00 ₽ за весь объём (4 шт) без НДС.")


def test_kp_file_comment_text_system_with_you_and_comparable_systems_on_p():
    inp = system_inputs(level="p", source=source(contractor=TENDER_CONTRACTOR, rate=rate(1, "10")))
    text = template_text(inp)
    assert "В нашем договоре с вами по объекту Объект Т, 2024 г., сопоставимые системы стоили 10,00 ₽ на м²" in text


def test_kp_file_comment_text_system_never_says_on_average():
    inp = system_inputs(source=source(rate=rate(1, "10", lines=4)))
    assert "в среднем" not in template_text(inp)


def test_kp_file_comment_text_system_inflation_tail_form1():
    inp = system_inputs(
        source=source(rate=rate(1, "10", nominal="8")),
        inflation=InflationLabel("ИПЦ", "09.2026"),
    )
    assert template_text(inp) == (
        "Ваша цена — 130,00 ₽ за шт без НДС. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта система стоила 8,00 ₽ на м² объекта без НДС — для вашего объекта это 400,00 ₽ "
        "за весь объём (4 шт), то есть 100,00 ₽ за шт; с учётом инфляции (ИПЦ, к 09.2026) — 125,00 ₽ за шт. "
        "Просим снизить до 125,00 ₽ за шт или обосновать разницу."
    )


def test_kp_file_comment_text_system_inflation_tail_form3():
    inp = system_inputs(
        quantity=D("NaN"),
        source=source(rate=rate(1, "10", nominal="8")),
        inflation=InflationLabel("ИПЦ", "09.2026"),
    )
    assert template_text(inp) == (
        "Ваша цена — 520,00 ₽ за весь объём без НДС. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта система стоила 8,00 ₽ на м² объекта без НДС — для вашего объекта это 400,00 ₽ "
        "за весь объём; с учётом инфляции (ИПЦ, к 09.2026) — 500,00 ₽. "
        "Просим снизить до 500,00 ₽ за весь объём или обосновать разницу."
    )


def test_kp_file_comment_text_system_no_tail_without_series_or_at_factor_one():
    nominal = source(rate=rate(1, "10", nominal="8"))
    assert "инфляции" not in template_text(system_inputs(source=nominal, inflation=None))
    one = source(rate=rate(1, "10", nominal="10"))
    assert "инфляции" not in template_text(system_inputs(source=one, inflation=InflationLabel("ИПЦ", "09.2026")))


@pytest.mark.parametrize(
    ("kwargs", "phrase"),
    [
        ({}, "в цену 130,00 ₽ за шт без НДС включены"),
        ({"kp_unit_net": None}, "в цену 520,00 ₽ за весь объём (4 шт) без НДС включены"),
        ({"quantity": D("NaN")}, "в цену 520,00 ₽ за весь объём без НДС включены"),
    ],
)
def test_kp_file_comment_text_system_below_follows_the_three_forms(kwargs, phrase):
    inp = with_result(system_inputs(**kwargs), position="below")
    assert phrase in template_text(inp)


@pytest.mark.parametrize(
    ("kwargs", "phrase"),
    [
        ({}, "Цена 130,00 ₽ за шт без НДС похожа"),
    ],
)
def test_kp_file_comment_text_system_kp_unit_form1_keeps_the_old_text(kwargs, phrase):
    inp = with_result(system_inputs(**kwargs), state=LineState.unit_mismatch, mismatch=mismatch("9"), position=None)
    assert template_text(inp) == (
        "Цена 130,00 ₽ за шт без НДС похожа на цену за весь объём, а не за единицу. "
        "Уточните, пожалуйста, единицу учёта."
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"kp_unit_net": None},
            "Ваша цена — 520,00 ₽ за весь объём (4 шт) без НДС. Уточните, пожалуйста, объём и единицу учёта.",
        ),
        (
            {"kp_unit_net": D("NaN")},
            "Ваша цена — 520,00 ₽ за весь объём (4 шт) без НДС. Уточните, пожалуйста, объём и единицу учёта.",
        ),
        (
            {"quantity": D("NaN")},
            "Ваша цена — 520,00 ₽ за весь объём без НДС. Уточните, пожалуйста, объём и единицу учёта.",
        ),
        (
            {"quantity": None},
            "Ваша цена — 520,00 ₽ за весь объём без НДС. Уточните, пожалуйста, объём и единицу учёта.",
        ),
        (
            {"quantity": D(0)},
            "Ваша цена — 520,00 ₽ за весь объём без НДС. Уточните, пожалуйста, объём и единицу учёта.",
        ),
    ],
)
def test_kp_file_comment_text_system_kp_unit_forms_2_and_3(kwargs, expected):
    inp = with_result(system_inputs(**kwargs), state=LineState.unit_mismatch, mismatch=mismatch("9"), position=None)
    text = template_text(inp)
    assert text == expected
    assert "за единицу" not in text
    assert "похожа" not in text


@pytest.mark.parametrize(
    ("kwargs", "phrase"),
    [
        ({}, "входит в цену 130,00 ₽ за шт без НДС и за какую"),
        ({"kp_unit_net": None}, "входит в цену 520,00 ₽ за весь объём (4 шт) без НДС и за какую"),
        ({"quantity": D("NaN")}, "входит в цену 520,00 ₽ за весь объём без НДС и за какую"),
    ],
)
def test_kp_file_comment_text_system_history_unit_follows_the_three_forms(kwargs, phrase):
    inp = with_result(system_inputs(**kwargs), state=LineState.unit_mismatch, mismatch=mismatch(None), position=None)
    assert phrase in template_text(inp)


@pytest.mark.parametrize(
    ("quantity", "shown"),
    [
        (D("2.50"), "2,5"),
        (D(100), "100"),
        (D("1234.5"), "1 234,5"),
        (D("0.125"), "0,125"),
        (D("1E+2"), "100"),
        (D("2.5E+3"), "2 500"),
        (D("100.000"), "100"),
    ],
)
def test_kp_file_comment_text_volume_format(quantity, shown):
    inp = system_inputs(quantity=quantity, kp_unit_net=None)
    assert f"за весь объём ({shown} шт) без НДС" in template_text(inp)


# --- запреты ------------------------------------------------------------------------


def test_kp_file_comment_text_never_names_the_fork():
    fork = RateRange(min=D(100), max=D(400), median=D(150))
    work = template_text(inputs(result=result(range=fork)))
    system = template_text(system_inputs(result=replace(system_inputs().result, range=fork)))
    for text in (work, system):
        assert "100,00" in text or "10,00" in text
        for banned in ("150", "400", "400,00", "150,00"):
            assert banned not in text


def test_kp_file_comment_text_never_names_contracts_count_or_rung():
    res = result(contracts=(rate(1, "100"), rate(2, "130"), rate(3, "150")), rung=Rung("f", "all"))
    text = template_text(inputs(result=res, level="f"))
    for banned in ("договоров", "ступен", "всех классов", "130", "150"):
        assert banned not in text


def test_kp_file_comment_text_signature_has_no_vat_switch():
    # переключателя НДС у текста нет: база КП — поле входа
    assert list(inspect.signature(template_text).parameters) == ["inp"]
    assert [name for name in inspect.signature(CommentInputs).parameters if "vat" in name] == ["vat_base"]


def all_text_cases(**extra):
    return [
        inputs(**extra),
        inputs(agreed_net=D(100), **extra),
        inputs(kp_unit_net=D(50), result=result(position="below", kp_unit_net=D(50)), **extra),
        inputs(result=result(state=LineState.unit_mismatch, mismatch=mismatch("9"), ours=None, position=None), **extra),
        inputs(
            result=result(state=LineState.unit_mismatch, mismatch=mismatch(None), ours=None, position=None), **extra
        ),
        system_inputs(**extra),
        system_inputs(kp_unit_net=None, **extra),
        system_inputs(quantity=D("NaN"), **extra),
    ]


def test_kp_file_comment_text_says_without_vat_in_a_zero_base():
    for inp in all_text_cases():
        assert "без НДС" in template_text(inp)


def test_kp_file_comment_text_names_the_kp_vat_base_in_words():
    for inp in all_text_cases(vat_base=D(20)):
        text = template_text(inp)
        assert "с НДС 20 %" in text
        assert "без НДС" not in text


def test_kp_file_comment_text_fractional_base_is_printed_like_the_header():
    assert "с НДС 7,5 %" not in template_text(inputs(vat_base=D("7.5")))
    assert "с НДС 7.5 %" in template_text(inputs(vat_base=D("7.50")))


def test_kp_file_comment_text_work_over_in_base_20_quotes_every_sum_with_vat():
    inp = inputs(
        result=result(ours=D(110)),
        source=source(rate=rate(1, "110", nominal="100")),
        inflation=InflationLabel("ИПЦ", "09.2026"),
        vat_base=D(20),
    )
    assert template_text(inp) == (
        "Ваша цена — 144,00 ₽ за м2 с НДС 20 %. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта работа стоила 120,00 ₽ за м2 с НДС 20 %; с учётом инфляции (ИПЦ, к 09.2026) — "
        "132,00 ₽. Просим снизить до 132,00 ₽ или обосновать разницу."
    )


def test_kp_file_comment_text_agreed_in_base_20():
    text = template_text(inputs(agreed_net=D("100"), vat_base=D(20)))
    assert text == "Согласовано: 120,00 ₽ за м2 с НДС 20 %. Просим отразить в следующей редакции КП."


def test_kp_file_comment_text_system_in_base_20_with_valid_volume():
    text = template_text(system_inputs(vat_base=D(20)))
    assert text.startswith("Ваша цена — 156,00 ₽ за шт с НДС 20 %. ")
    assert "на м² объекта с НДС 20 % — для вашего объекта это 600,00 ₽ за весь объём (4 шт), то есть 150,00 ₽ за шт" in text
    assert text.endswith("Просим снизить до 150,00 ₽ за шт или обосновать разницу.")


def test_kp_file_comment_text_system_in_base_20_with_invalid_volume():
    text = template_text(system_inputs(quantity=D("NaN"), vat_base=D(20)))
    assert text.startswith("Ваша цена — 624,00 ₽ за весь объём с НДС 20 %. ")
    assert "на м² объекта с НДС 20 % — для вашего объекта это 600,00 ₽ за весь объём" in text
    assert text.endswith("Просим снизить до 600,00 ₽ за весь объём или обосновать разницу.")


def test_kp_file_comment_text_has_no_float_artifacts():
    # цифры 20+ знаков: цитата и сумма не теряют точность
    inp = system_inputs(
        source=source(rate=rate(1, "10.123456789012345678901")),
        result=replace(system_inputs().result, ours=D("10.123456789012345678901")),
    )
    assert "10,12 ₽ на м²" in template_text(inp)

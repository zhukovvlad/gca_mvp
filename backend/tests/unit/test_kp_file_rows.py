"""Строки файла подрядчику и шапка листа (спека 4б §2.8).

Чистый расчёт без базы. Эталоны — числа и строки, посчитанные руками; площадь
объекта тендера 100, база НДС КП по умолчанию 0 (цены КП равны нетто), кроме
тестов, где база названа.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from money.inflation import YearMonth
from services.kp_comment import (
    InflationLabel,
    SourceContract,
    comment_case,
    format_rub,
    template_text,
)
from services.kp_file import (
    FileContext,
    FileHeader,
    FileLine,
    FileSection,
    KpSourceLine,
    build_file_rows,
    build_header,
    deviation_pct,
    inflation_years_for,
    line_comment_inputs,
)
from services.our_price import (
    ContractRate,
    HistoryContract,
    HistoryIndex,
    LineResult,
    LineState,
    Mismatch,
    OfferEvaluation,
    RateRange,
    Rung,
)

D = Decimal

TENDER_CLASS = 1
TENDER_CONTRACTOR = 7
OTHER_CONTRACTOR = 8
AREA = D(100)
LABEL = InflationLabel("Индекс ЦБ", "03.2026")
AGREED_ON = "12.10.2026"


def hist_contract(cid: int, *, title: str = "Объект Т", number: str = "Д-1", year: int = 2024) -> HistoryContract:
    return HistoryContract(
        contract_id=cid,
        estimate_id=cid * 10,
        number=number,
        object_title=title,
        year=year,
        rate_class_id=TENDER_CLASS,
        rate_class_title="Класс 1",
        contractor_id=OTHER_CONTRACTOR,
        contractor_title="Подрядчик Б",
        area=AREA,
    )


def rate(cid: int, net: str, *, nominal: str | None = None, lines: int = 1) -> ContractRate:
    return ContractRate(contract_id=cid, net=D(net), nominal=D(nominal if nominal is not None else net), lines=lines)


def result(**overrides) -> LineResult:
    """Работа: сравнима, наша 100, договор 1 с той же ставкой, КП 120 за единицу."""
    values = dict(
        position_item_id=1,
        state=LineState.comparable,
        kp_unit_net=D(120),
        kp_total_net=D(240),
        rung=Rung("v", "cls"),
        auto=True,
        threshold_met=True,
        rungs=(),
        contracts=(rate(1, "100"),),
        range=RateRange(min=D(100), max=D(100), median=D(100)),
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


def system_result(**overrides) -> LineResult:
    """Система: наша 10 руб/м2, площадь 100, доля 1/2 — целевая сумма 500."""
    values = dict(
        kp_unit_net=D(130),
        kp_total_net=D(520),
        ours=D(10),
        contracts=(rate(1, "10"),),
        range=RateRange(min=D(10), max=D(10), median=D(10)),
        group_index=0,
        group_lines=2,
        share=D("0.5"),
        group_per_sqm=D("10.4"),
    )
    values.update(overrides)
    return result(**values)


def line(**overrides) -> KpSourceLine:
    values = dict(
        position_item_id=1,
        is_chapter=False,
        is_system=False,
        number="1.1",
        title="Работа",
        unit_text="м2",
        quantity=D(2),
        unit_price=D(120),
        total=D(240),
    )
    values.update(overrides)
    return KpSourceLine(**values)


def system_line(**overrides) -> KpSourceLine:
    values = dict(is_system=True, unit_text="шт", quantity=D(4), unit_price=D(130), total=D(520))
    values.update(overrides)
    return line(**values)


def chapter(position_item_id: int, number: str, title: str) -> KpSourceLine:
    return KpSourceLine(
        position_item_id=position_item_id,
        is_chapter=True,
        is_system=False,
        number=number,
        title=title,
        unit_text="",
        quantity=None,
        unit_price=None,
        total=None,
    )


def context(*, contracts: tuple[int, ...] = (1,), **overrides) -> FileContext:
    values = dict(
        kp_vat_base=D(0),
        history=HistoryIndex([hist_contract(c) for c in contracts], [], factor_by_estimate=None),
        tender_class_id=TENDER_CLASS,
        tender_area=AREA,
        tender_contractor_id=TENDER_CONTRACTOR,
        inflation=None,
        inflation_plan=None,
        contract_periods={},
        tender_title="Тендер Т",
        object_title="Объект Т",
        stage_number=3,
        stage_date=date(2026, 10, 15),
        contractor_title="Подрядчик А",
        exported_on=date(2026, 10, 27),
    )
    values.update(overrides)
    return FileContext(**values)


def build(lines, results, *, comments=None, agreements=None, ctx=None):
    evaluation = OfferEvaluation(lines={r.position_item_id: r for r in results}, groups=())
    return build_file_rows(
        lines,
        evaluation,
        comments=comments or {},
        agreements=agreements or {},
        context=ctx or context(),
    )


def one(src_line: KpSourceLine, res: LineResult, **kwargs) -> FileLine:
    (row,) = build([src_line], [res], **kwargs)
    assert isinstance(row, FileLine)
    return row


def plan(*, used_years: list[dict], target_month: str = "2026-03", series_name: str = "Индекс ЦБ"):
    return SimpleNamespace(series_name=series_name, target_month=target_month, used_years=used_years)


def year_fact(year: int, coefficient: str = "1.05", *, forecast: bool = False) -> dict:
    return {
        "year": year,
        "coefficient": D(coefficient),
        "source": f"Источник {year}",
        "is_forecast": forecast,
        "updated_at": f"{year + 1}-01-15",
    }


# --- состав и порядок -------------------------------------------------------------------


def test_kp_file_rows_keep_input_order_with_sections_and_lines():
    rows = build(
        [chapter(10, "1", "Раздел"), line(position_item_id=11), line(position_item_id=12), chapter(13, "2", "Второй")],
        [result(position_item_id=11), result(position_item_id=12)],
    )
    assert [type(r) for r in rows] == [FileSection, FileLine, FileLine, FileSection]
    assert rows[0] == FileSection(number="1", title="Раздел")
    assert [r.position_item_id for r in rows if isinstance(r, FileLine)] == [11, 12]
    assert rows[3].title == "Второй"


def test_kp_file_rows_only_positions_and_chapters_come_out():
    rows = build([line(position_item_id=5)], [result(position_item_id=5)])
    assert len(rows) == 1


def test_kp_file_line_carries_the_kp_columns_unchanged():
    row = one(
        line(
            number="2.3", title="Кладка", unit_text="м3", quantity=D("12.5"), unit_price=D("99.9"), total=D("1248.75")
        ),
        result(),
    )
    assert (row.position_item_id, row.number, row.title, row.unit) == (1, "2.3", "Кладка", "м3")
    assert (row.quantity, row.kp_unit_price, row.kp_total) == (D("12.5"), D("99.9"), D("1248.75"))
    assert row.state == "comparable"


# --- наша расценка -----------------------------------------------------------------------


def test_kp_file_ours_is_the_target_in_the_kp_vat_base():
    row = one(line(unit_price=D("146.4")), result(), ctx=context(kp_vat_base=D(22)))
    assert row.ours == D(122)


def test_kp_file_ours_in_zero_base_equals_the_target():
    assert one(line(), result()).ours == D(100)


def test_kp_file_ours_is_not_rounded():
    row = one(line(), result(ours=D("100.0049"), contracts=(rate(1, "100.0049"),)), ctx=context(kp_vat_base=D(22)))
    assert row.ours == D("100.0049") * D(122) / D(100)


@pytest.mark.parametrize(
    "state",
    [
        LineState.in_progress,
        LineState.no_price,
        LineState.no_variant,
        LineState.no_history,
        LineState.unit_mismatch,
    ],
)
def test_kp_file_ours_is_empty_without_comparison(state):
    res = result(
        state=state, mismatch=Mismatch(kp_ratio=D(10), contracts=()) if state is LineState.unit_mismatch else None
    )
    assert one(line(), res).ours is None


@pytest.mark.parametrize(
    "state",
    [LineState.in_progress, LineState.no_price, LineState.no_history, LineState.unit_mismatch, LineState.comparable],
)
def test_kp_file_agreed_fills_ours_in_any_state(state):
    row = one(
        line(unit_price=D("146.4")),
        result(
            state=state, mismatch=Mismatch(kp_ratio=None, contracts=()) if state is LineState.unit_mismatch else None
        ),
        agreements={1: (D(100), AGREED_ON)},
        ctx=context(kp_vat_base=D(22)),
    )
    assert row.ours == D(122)


def test_kp_file_system_ours_is_the_target_sum_over_volume_in_kp_base():
    # целевая сумма 10 * 100 * 0.5 = 500, объём 4: цель 125 нетто, при 22 % — 152,5
    row = one(system_line(unit_price=D("158.6"), total=D("634.4")), system_result(), ctx=context(kp_vat_base=D(22)))
    assert row.ours == D("152.5")


def test_kp_file_system_ours_is_empty_when_volume_is_not_a_weight():
    assert one(system_line(quantity=D("NaN")), system_result()).ours is None
    assert one(system_line(quantity=D(0)), system_result()).ours is None


def test_kp_file_review_case_system_agreed_ours_is_the_agreed_net():
    row = one(
        system_line(quantity=D(2), unit_price=D(60000), total=D(120000)),
        system_result(kp_unit_net=D(60000), kp_total_net=D(120000), ours=D(80000) / AREA / D("0.5"), share=D("0.5")),
        agreements={1: (D(50000), AGREED_ON)},
    )
    assert row.ours == D(50000)
    assert row.deviation_pct == D("20.0")


# --- отклонение: таблица приоритета -------------------------------------------------------


def test_kp_file_deviation_row1_agreed_compares_the_kp_unit_price():
    assert one(line(unit_price=D(120)), result(), agreements={1: (D(100), AGREED_ON)}).deviation_pct == D("20.0")


def test_kp_file_deviation_row1_kp_price_below_the_agreement_is_negative():
    assert one(line(unit_price=D(80)), result(), agreements={1: (D(100), AGREED_ON)}).deviation_pct == D("-20.0")


def test_kp_file_deviation_row1_wins_over_the_automatic_target():
    # авто-цель 100 дала бы 20.0 от 120, договорились 150: 120 / 150 - 1 = -20.0
    assert one(line(unit_price=D(120)), result(), agreements={1: (D(150), AGREED_ON)}).deviation_pct == D("-20.0")


def test_kp_file_deviation_row1_works_for_a_system_without_a_valid_volume():
    row = one(
        system_line(quantity=D("NaN"), unit_price=D(60000), total=D(120000)),
        system_result(kp_unit_net=D(60000), kp_total_net=D(120000)),
        agreements={1: (D(50000), AGREED_ON)},
    )
    assert row.deviation_pct == D("20.0")


def test_kp_file_deviation_row1_is_empty_without_a_kp_unit_price():
    row = one(
        line(unit_price=None, total=D(240)),
        result(state=LineState.no_price, kp_unit_net=None),
        agreements={1: (D(100), AGREED_ON)},
    )
    assert row.ours == D(100)
    assert row.deviation_pct is None


@pytest.mark.parametrize("bad_price", [D(0), D("NaN")])
def test_kp_file_deviation_row1_is_empty_when_the_kp_price_is_not_a_price(bad_price):
    row = one(
        line(unit_price=bad_price),
        result(state=LineState.no_price, kp_unit_net=None),
        agreements={1: (D(100), AGREED_ON)},
    )
    assert row.deviation_pct is None


def test_kp_file_deviation_row1_in_the_kp_base_compares_nets():
    # цена КП 146,4 с НДС 22 % — нетто 120; договорились 100 нетто
    row = one(
        line(unit_price=D("146.4")), result(), agreements={1: (D(100), AGREED_ON)}, ctx=context(kp_vat_base=D(22))
    )
    assert row.deviation_pct == D("20.0")


def test_kp_file_deviation_row2_work_compares_the_unit_price_to_the_target():
    assert one(line(unit_price=D(120)), result(ours=D(100))).deviation_pct == D("20.0")


def test_kp_file_deviation_row2_work_below_the_target_is_negative():
    row = one(line(unit_price=D(80)), result(ours=D(100), position="in"))
    assert row.deviation_pct == D("-20.0")


def test_kp_file_deviation_row3_system_compares_the_line_sum_to_the_target_sum():
    # сумма КП 520 против целевой суммы 500
    assert one(system_line(), system_result()).deviation_pct == D("4.0")


def test_kp_file_deviation_row3_system_below_the_target_sum_is_negative():
    row = one(system_line(total=D(480)), system_result(kp_total_net=D(480), position="in"))
    assert row.deviation_pct == D("-4.0")


def test_kp_file_deviation_row3_system_without_a_valid_volume_uses_sums():
    row = one(system_line(quantity=D("NaN")), system_result())
    assert row.deviation_pct == D("4.0")
    assert row.ours is None


def test_kp_file_deviation_row3_counterexample_unit_equal_in_kopecks_sums_not():
    # объём 100, сумма КП 100,40 против целевой суммы 100,00: за единицу 1,004 и 1,00
    # равны в копейках, по суммам КП выше на 0,4 %
    res = system_result(
        kp_unit_net=D("1.004"), kp_total_net=D("100.40"), ours=D(2), contracts=(rate(1, "2"),), share=D("0.5")
    )
    src = system_line(quantity=D(100), unit_price=D("1.004"), total=D("100.40"))
    row = one(src, res)
    assert row.deviation_pct == D("0.4")
    assert comment_case(comment_inputs(src, res)) == "over"


@pytest.mark.parametrize(
    "state",
    [LineState.in_progress, LineState.no_price, LineState.no_variant, LineState.no_history, LineState.unit_mismatch],
)
def test_kp_file_deviation_row4_other_states_are_empty(state):
    res = result(
        state=state, mismatch=Mismatch(kp_ratio=D(10), contracts=()) if state is LineState.unit_mismatch else None
    )
    assert one(line(), res).deviation_pct is None


@pytest.mark.parametrize(
    "state", [LineState.no_history, LineState.unit_mismatch, LineState.no_variant, LineState.in_progress]
)
def test_kp_file_deviation_row4_a_system_without_comparison_is_empty(state):
    res = system_result(
        state=state, mismatch=Mismatch(kp_ratio=D(10), contracts=()) if state is LineState.unit_mismatch else None
    )
    row = one(system_line(), res)
    assert (row.ours, row.deviation_pct) == (None, None)


def test_kp_file_deviation_of_a_sub_kopeck_target_is_computed_from_the_raw_values():
    # цель 0,004: процент по исходным значениям, копеечного округления цен нет
    row = one(line(unit_price=D(1)), result(ours=D("0.004"), contracts=(rate(1, "0.004"),)))
    assert row.ours == D("0.004")
    assert row.deviation_pct == D("24900.0")


def test_kp_file_deviation_of_a_sub_kopeck_agreement_is_computed_from_the_raw_values():
    row = one(line(unit_price=D(1)), result(), agreements={1: (D("0.004"), AGREED_ON)})
    assert row.deviation_pct == D("24900.0")


# --- отклонение: округление и знак -------------------------------------------------------


@pytest.mark.parametrize(
    ("kp", "expected"),
    [
        ("100.05", "0.1"),  # половина вверх: 0,05 -> 0,1
        ("99.95", "-0.1"),  # половина от нуля: -0,05 -> -0,1
        ("100.04", "0.0"),
        ("133.33", "33.3"),
        ("66.67", "-33.3"),
    ],
)
def test_kp_file_deviation_is_rounded_half_up_to_one_digit(kp, expected):
    row = one(line(unit_price=D(kp)), result(ours=D(100), position="above"))
    assert row.deviation_pct == D(expected)
    assert str(row.deviation_pct) == expected


def test_kp_file_deviation_of_sides_equal_in_kopecks_rounds_the_raw_ratio_to_zero():
    # 100,004 и 99,996 в копейках оба 100,00; ноль даёт округление точного 0,008 %, а не копейки
    row = one(line(unit_price=D("100.004")), result(ours=D("99.996"), contracts=(rate(1, "99.996"),)))
    assert str(row.deviation_pct) == "0.0"


def test_kp_file_deviation_just_below_the_target_is_not_negative_zero():
    row = one(line(unit_price=D("99.99")), result(ours=D(100), position="below"))
    assert str(row.deviation_pct) == "0.0"
    assert not row.deviation_pct.is_signed()


def test_kp_file_deviation_uses_raw_sides_not_kopeck_rounded():
    # от сырых 2,01 / 1,994 - 1 = 0,8 %; от округлённых до копеек 2,01 / 1,99 было бы 1,0
    row = one(line(unit_price=D("2.01")), result(ours=D("1.994"), contracts=(rate(1, "1.994"),)))
    assert row.deviation_pct == D("0.8")


def test_kp_file_deviation_has_no_intermediate_rounding_of_a_long_quotient():
    # частное в 25 знаков до запятой: при точности по умолчанию (28 знаков) его хвост
    # округлился бы до сотых и перешёл бы половину — 450,2 вместо точного 450,1
    row = one(line(unit_price=D("71352671802761766527717.55")), result(ours=D("6.68"), contracts=(rate(1, "6.68"),)))
    assert row.deviation_pct == D("1068153769502421654606450.1")


def test_kp_file_deviation_does_not_round_the_kp_side_to_kopecks():
    # (2,014 / 2 - 1) * 100 = 0,7; от округлённого до копеек КП 2,01 было бы 0,5
    row = one(line(unit_price=D("2.014")), result(ours=D(2), contracts=(rate(1, "2"),)))
    assert row.deviation_pct == D("0.7")


@pytest.mark.parametrize("state", [LineState.in_progress, LineState.no_history, LineState.unit_mismatch])
def test_kp_file_deviation_row1_applies_in_a_state_without_comparison(state):
    res = result(
        state=state, mismatch=Mismatch(kp_ratio=None, contracts=()) if state is LineState.unit_mismatch else None
    )
    row = one(line(unit_price=D(120)), res, agreements={1: (D(100), AGREED_ON)})
    assert row.deviation_pct == D("20.0")


@pytest.mark.parametrize("state", [LineState.no_area, LineState.no_history, LineState.unit_mismatch])
def test_kp_file_deviation_row1_applies_to_a_system_without_comparison(state):
    # у системы без сравнения целевой суммы нет: отклонение даёт только договорённость
    res = system_result(
        state=state, mismatch=Mismatch(kp_ratio=None, contracts=()) if state is LineState.unit_mismatch else None
    )
    row = one(system_line(unit_price=D(120)), res, agreements={1: (D(100), AGREED_ON)})
    assert row.deviation_pct == D("20.0")


def _row_for(kind: str, kp: str, target: str) -> FileLine:
    """Строка таблицы приоритета: КП и цель в нетто, одна копейка решает."""
    above = D(kp) > D(target)
    if kind == "agreed":
        return one(line(unit_price=D(kp)), result(), agreements={1: (D(target), AGREED_ON)})
    if kind == "work":
        return one(
            line(unit_price=D(kp)),
            result(ours=D(target), contracts=(rate(1, target),), position="above" if above else "in"),
        )
    ours = D(target) / AREA
    return one(
        system_line(quantity=D(1), unit_price=D(kp), total=D(kp)),
        system_result(
            kp_unit_net=D(kp),
            kp_total_net=D(kp),
            ours=ours,
            contracts=(rate(1, str(ours)),),
            share=D(1),
            position="above" if above else "in",
        ),
    )


@pytest.mark.parametrize("kind", ["agreed", "work", "system"])
@pytest.mark.parametrize(
    ("kp", "target", "sign", "case"),
    [
        ("120", "100", 1, "over"),
        ("100.01", "100", 1, "over"),
        ("100.004", "100", 0, None),
        ("100", "100", 0, None),
        ("99.99", "100", -1, None),
        ("80", "100", -1, None),
        # копейки и точное отношение расходятся: «выше» решают копейки (1,01 против 1,00),
        # отклонение — точное 0,0996 % -> 0,1; знак тот же
        ("1.005", "1.004", 1, "over"),
        ("1.004", "1.005", -1, None),
    ],
)
def test_kp_file_deviation_sign_agrees_with_the_comment_case_on_every_row(kind, kp, target, sign, case):
    row = _row_for(kind, kp, target)
    expected_case = ("agreed" if kind == "agreed" else "over") if case == "over" else None
    has_template = row.comment_kind == "template"
    assert has_template == (expected_case is not None)
    if expected_case == "agreed":
        assert row.comment.startswith("Согласовано: ")
    elif expected_case == "over":
        assert row.comment.startswith("Ваша цена — ")
    if sign > 0:
        assert row.deviation_pct >= 0
    elif sign < 0:
        assert row.deviation_pct <= 0
    else:
        assert str(row.deviation_pct) == "0.0"


def test_kp_file_deviation_pct_function_takes_the_comment_inputs():
    inp = line_comment_inputs(
        line(unit_price=D(120)),
        result(),
        history=context().history,
        tender_class_id=TENDER_CLASS,
        tender_contractor_id=TENDER_CONTRACTOR,
        tender_area=AREA,
        kp_vat_base=D(0),
        inflation=None,
        agreed_net=None,
    )
    assert deviation_pct(inp) == D("20.0")


# --- источник ---------------------------------------------------------------------------


def test_kp_file_source_of_an_agreed_line_is_the_agreement_date():
    row = one(line(), result(), agreements={1: (D(100), AGREED_ON)})
    assert row.source == "Договорились 12.10.2026"


def test_kp_file_texts_of_a_line_in_base_20_agree_with_the_our_price_cell():
    # КП 1 980,00 с НДС 20 % (нетто 1 650), цитата договора 1 006,25 нетто, цель с рядом 1 037,67... нетто
    label = InflationLabel("Индекс ЦБ", "03.2026")
    row = one(
        line(unit_price=D("1980"), total=D("3960")),
        result(
            kp_unit_net=D(1650),
            kp_total_net=D(3300),
            ours=D("1037.6712"),
            contracts=(rate(1, "1037.6712", nominal="1006.25"),),
        ),
        ctx=context(kp_vat_base=D(20), inflation=label),
    )
    cell = format_rub(row.ours)
    assert cell == "1 245,21"
    assert row.comment == (
        "Ваша цена — 1 980,00 ₽ за м2 с НДС 20 %. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта работа стоила 1 207,50 ₽ за м2 с НДС 20 %; с учётом инфляции (Индекс ЦБ, к 03.2026) — "
        f"{cell} ₽. Просим снизить до {cell} ₽ или обосновать разницу."
    )
    assert row.source == "Объект Т, договор № Д-1, 2024 г.: 1 207,50 ₽ за м2 с НДС 20 %"
    assert row.inflation == f"Индекс ЦБ, к 03.2026: {cell} ₽ за м2 с НДС 20 %"
    assert "без НДС" not in row.comment + row.source + row.inflation


def test_kp_file_texts_of_a_line_in_a_zero_base_say_without_vat_and_equal_net():
    label = InflationLabel("Индекс ЦБ", "03.2026")
    row = one(
        line(),
        result(ours=D("110"), contracts=(rate(1, "110", nominal="100"),)),
        ctx=context(inflation=label),
    )
    assert row.source == "Объект Т, договор № Д-1, 2024 г.: 100,00 ₽ за м2 без НДС"
    assert row.inflation == "Индекс ЦБ, к 03.2026: 110,00 ₽ за м2 без НДС"
    assert "110,00 ₽" in row.comment and "без НДС" in row.comment


def test_kp_file_source_of_a_work_quotes_the_nominal_rate_without_vat():
    row = one(line(), result())
    assert row.source == "Объект Т, договор № Д-1, 2024 г.: 100,00 ₽ за м2 без НДС"


def test_kp_file_source_says_on_average_for_a_rate_folded_from_two_lines():
    row = one(line(), result(contracts=(rate(1, "100", lines=2),)))
    assert row.source == "Объект Т, договор № Д-1, 2024 г.: в среднем 100,00 ₽ за м2 без НДС"


def test_kp_file_source_does_not_say_on_average_for_a_single_line_rate():
    assert "в среднем" not in one(line(), result(contracts=(rate(1, "100", lines=1),))).source


def test_kp_file_source_quotes_the_nominal_rate_not_the_adjusted_one():
    res = result(ours=D("110"), contracts=(rate(1, "110", nominal="100"),))
    assert one(line(), res).source == "Объект Т, договор № Д-1, 2024 г.: 100,00 ₽ за м2 без НДС"


def test_kp_file_source_of_a_system_names_the_rate_per_object_square_metre():
    row = one(system_line(), system_result())
    assert row.source == "Объект Т, договор № Д-1, 2024 г.: 10,00 ₽ на м² объекта без НДС"


def test_kp_file_source_of_a_system_never_says_on_average():
    res = system_result(contracts=(rate(1, "10", lines=3),))
    assert "в среднем" not in one(system_line(), res).source


@pytest.mark.parametrize("quantity", [D("NaN"), D(0), None])
def test_kp_file_source_of_a_system_without_a_valid_volume_adds_the_target_sum(quantity):
    row = one(system_line(quantity=quantity), system_result())
    assert row.source == (
        "Объект Т, договор № Д-1, 2024 г.: 10,00 ₽ на м² объекта без НДС — для вашего объекта 500,00 ₽ за весь объём"
    )


def test_kp_file_source_tail_of_a_system_names_the_nominal_sum_the_inflation_cell_the_target():
    # номинал 10 -> 500,00; приведённая 12 -> 600,00
    res = system_result(ours=D(12), contracts=(rate(1, "12", nominal="10"),))
    row = one(system_line(quantity=D("NaN")), res, ctx=context(inflation=LABEL))
    assert row.source.endswith("для вашего объекта 500,00 ₽ за весь объём")
    assert row.inflation == "Индекс ЦБ, к 03.2026: 600,00 ₽ за весь объём без НДС"


def test_kp_file_source_tail_of_a_system_keeps_the_full_precision_of_the_rate():
    # ставка-среднее в 34 значащих знака: при точности по умолчанию (28 знаков) произведение
    # 5,00499…9 округлилось бы до 5,005 и в копейках стало бы 5,01
    nominal = "0.05004999999999999999999999999999999"
    res = system_result(ours=D(nominal), contracts=(rate(1, nominal),), share=D(1))
    row = one(system_line(quantity=D("NaN")), res)
    assert row.source.endswith("для вашего объекта 5,00 ₽ за весь объём")


def test_kp_file_source_tail_of_a_system_is_absent_with_a_valid_volume():
    assert "для вашего объекта" not in one(system_line(quantity=D(4)), system_result()).source


@pytest.mark.parametrize(
    "state", [LineState.in_progress, LineState.no_price, LineState.no_history, LineState.unit_mismatch]
)
def test_kp_file_source_is_empty_without_comparison_and_agreement(state):
    res = result(
        state=state, mismatch=Mismatch(kp_ratio=D(10), contracts=()) if state is LineState.unit_mismatch else None
    )
    assert one(line(), res).source is None


def test_kp_file_source_of_the_same_contractor_does_not_change_the_text():
    res = result()
    ctx = context(tender_contractor_id=OTHER_CONTRACTOR)
    assert one(line(), res, ctx=ctx).source == one(line(), res).source


# --- приведение по ИПЦ --------------------------------------------------------------------


INFLATED = dict(ours=D("110"), contracts=(rate(1, "110", nominal="100"),))


def test_kp_file_inflation_cell_of_a_work_names_the_series_month_and_target():
    row = one(line(), result(**INFLATED), ctx=context(inflation=LABEL))
    assert row.inflation == "Индекс ЦБ, к 03.2026: 110,00 ₽ за м2 без НДС"


def test_kp_file_inflation_cell_is_empty_without_a_series():
    assert one(line(), result(**INFLATED)).inflation is None


def test_kp_file_inflation_cell_is_empty_at_multiplier_one():
    row = one(line(), result(ours=D(100), contracts=(rate(1, "100", nominal="100"),)), ctx=context(inflation=LABEL))
    assert row.inflation is None


def test_kp_file_inflation_cell_is_empty_for_an_agreed_line():
    row = one(line(), result(**INFLATED), agreements={1: (D(100), AGREED_ON)}, ctx=context(inflation=LABEL))
    assert row.inflation is None


@pytest.mark.parametrize("state", [LineState.no_history, LineState.unit_mismatch])
def test_kp_file_inflation_cell_is_empty_without_a_comparison(state):
    res = result(
        state=state,
        mismatch=Mismatch(kp_ratio=D(10), contracts=()) if state is LineState.unit_mismatch else None,
        **INFLATED,
    )
    assert one(line(), res, ctx=context(inflation=LABEL)).inflation is None


def test_kp_file_inflation_cell_of_a_system_with_volume_is_per_unit():
    res = system_result(ours=D(12), contracts=(rate(1, "12", nominal="10"),))
    # целевая сумма 12 * 100 * 0,5 = 600, объём 4 -> 150
    assert (
        one(system_line(), res, ctx=context(inflation=LABEL)).inflation
        == "Индекс ЦБ, к 03.2026: 150,00 ₽ за шт без НДС"
    )


def test_kp_file_inflation_cell_of_a_system_without_volume_is_the_sum():
    res = system_result(ours=D(12), contracts=(rate(1, "12", nominal="10"),))
    row = one(system_line(quantity=D("NaN")), res, ctx=context(inflation=LABEL))
    assert row.inflation == "Индекс ЦБ, к 03.2026: 600,00 ₽ за весь объём без НДС"


@pytest.mark.parametrize("quantity", [D("NaN"), D(0), None])
def test_kp_file_inflation_cell_of_a_work_is_per_unit_whatever_the_volume(quantity):
    # суммой называет только система; работа без годного объёма — по-прежнему за единицу
    row = one(line(quantity=quantity), result(**INFLATED), ctx=context(inflation=LABEL))
    assert row.inflation == "Индекс ЦБ, к 03.2026: 110,00 ₽ за м2 без НДС"


# --- комментарий --------------------------------------------------------------------------


def test_kp_file_comment_template_over_text_is_the_template_of_the_line_inputs():
    row = one(line(), result())
    assert row.comment_kind == "template"
    assert row.comment == (
        "Ваша цена — 120,00 ₽ за м2 без НДС. В нашем договоре по объекту Объект Т, 2024 г., "
        "эта работа стоила 100,00 ₽ за м2 без НДС. Просим снизить до 100,00 ₽ или обосновать разницу."
    )


def test_kp_file_comment_agreed_template_names_the_agreed_number():
    row = one(line(), result(), agreements={1: (D(100), AGREED_ON)})
    assert row.comment_kind == "template"
    assert row.comment == "Согласовано: 100,00 ₽ за м2 без НДС. Просим отразить в следующей редакции КП."


def test_kp_file_comment_custom_text_beats_the_template():
    row = one(line(), result(), comments={1: "Свой текст"})
    assert (row.comment, row.comment_kind) == ("Свой текст", "custom")


def test_kp_file_comment_empty_custom_text_is_an_empty_cell_and_still_custom():
    row = one(line(), result(), comments={1: ""})
    assert (row.comment, row.comment_kind) == (None, "custom")


def test_kp_file_comment_custom_of_another_line_does_not_apply():
    row = one(line(), result(), comments={2: "Чужой текст"})
    assert row.comment_kind == "template"


def test_kp_file_comment_is_empty_without_text_and_template():
    row = one(line(unit_price=D(80)), result(ours=D(100), position="in"))
    assert (row.comment, row.comment_kind) == (None, None)


@pytest.mark.parametrize(
    ("res", "kp_price", "agreed"),
    [
        (result(), D(120), None),
        (result(position="in"), D(100), None),
        (result(position="below"), D(80), None),
        (result(), D(120), D(100)),
        (result(), D(90), D(100)),
        (result(state=LineState.no_history), D(120), None),
        (result(state=LineState.unit_mismatch, mismatch=Mismatch(kp_ratio=D(10), contracts=())), D(120), None),
        (result(state=LineState.unit_mismatch, mismatch=Mismatch(kp_ratio=None, contracts=())), D(120), None),
    ],
)
def test_kp_file_comment_kind_is_template_exactly_when_the_template_exists(res, kp_price, agreed):
    src = line(unit_price=kp_price)
    agreements = {1: (agreed, AGREED_ON)} if agreed is not None else {}
    row = one(src, res, agreements=agreements)
    inp = line_comment_inputs(
        src,
        res,
        history=context().history,
        tender_class_id=TENDER_CLASS,
        tender_contractor_id=TENDER_CONTRACTOR,
        tender_area=AREA,
        kp_vat_base=D(0),
        inflation=None,
        agreed_net=agreed,
    )
    expected = template_text(inp)
    assert (row.comment_kind == "template") == (expected is not None)
    assert row.comment == expected


# --- вход комментария ---------------------------------------------------------------------


def comment_inputs(src: KpSourceLine, res: LineResult, **overrides):
    values = dict(
        history=context().history,
        tender_class_id=TENDER_CLASS,
        tender_contractor_id=TENDER_CONTRACTOR,
        tender_area=AREA,
        kp_vat_base=D(0),
        inflation=None,
        agreed_net=None,
    )
    values.update(overrides)
    return line_comment_inputs(src, res, **values)


def test_kp_file_comment_inputs_convert_the_kp_prices_to_net_by_the_kp_base():
    inp = comment_inputs(line(unit_price=D("146.4"), total=D("292.8")), result(), kp_vat_base=D(22))
    assert inp.kp_unit_net == D(120)
    assert inp.kp_total_net == D(240)


@pytest.mark.parametrize("bad", [None, D("NaN"), D("Infinity")])
def test_kp_file_comment_inputs_have_no_net_for_a_missing_or_non_finite_price(bad):
    inp = comment_inputs(line(unit_price=bad, total=bad), result())
    assert (inp.kp_unit_net, inp.kp_total_net) == (None, None)


def test_kp_file_comment_inputs_carry_quantity_unit_and_agreement():
    inp = comment_inputs(line(quantity=D("3.5"), unit_text="кг"), result(), agreed_net=D(77))
    assert (inp.quantity, inp.unit_text, inp.agreed_net) == (D("3.5"), "кг", D(77))
    assert inp.tender_contractor_id == TENDER_CONTRACTOR
    assert inp.tender_area == AREA


def test_kp_file_comment_inputs_take_the_level_from_the_active_rung():
    assert comment_inputs(line(), result(rung=Rung("p", "all"))).level == "p"
    assert comment_inputs(line(), result(rung=None, state=LineState.no_history)).level is None


def test_kp_file_comment_inputs_pick_the_source_contract():
    inp = comment_inputs(line(), result())
    assert isinstance(inp.source, SourceContract)
    assert inp.source.contract.contract_id == 1
    assert inp.source.rate.nominal == D(100)


def test_kp_file_comment_inputs_tell_a_system_by_the_source_line():
    assert comment_inputs(system_line(), system_result()).is_system is True
    assert comment_inputs(line(), result()).is_system is False


@pytest.mark.parametrize("state", [LineState.no_area, LineState.no_variant, LineState.in_progress, LineState.no_price])
def test_kp_file_system_line_before_the_family_stays_a_system(state):
    res = result(state=state, group_index=None, share=None, contracts=(), ours=None, range=None)
    assert comment_inputs(system_line(), res).is_system is True


def test_kp_file_comment_inputs_pass_the_inflation_label():
    assert comment_inputs(line(), result(), inflation=LABEL).inflation == LABEL


# --- защита от формул --------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["=1+1", "+7", "-5", "@x"])
def test_kp_file_text_cells_get_the_apostrophe_in_rows_and_sections(bad):
    rows = build([chapter(10, bad, bad), line(number=bad, title=bad, unit_text=bad)], [result()])
    assert rows[0] == FileSection(number="'" + bad, title="'" + bad)
    row = rows[1]
    assert (row.number, row.title, row.unit) == ("'" + bad,) * 3


def test_kp_file_custom_comment_gets_the_apostrophe():
    assert one(line(), result(), comments={1: "=cmd"}).comment == "'=cmd"


def test_kp_file_source_and_inflation_get_the_apostrophe():
    ctx = context(inflation=InflationLabel("=ряд", "03.2026"))
    ctx = replace(ctx, history=HistoryIndex([hist_contract(1, title="-Объект")], [], factor_by_estimate=None))
    row = one(line(), result(**INFLATED), ctx=ctx)
    assert row.source.startswith("'-Объект, договор")
    assert row.inflation.startswith("'=ряд, к 03.2026")


def test_kp_file_apostrophe_is_applied_once():
    assert one(line(title="'=уже"), result()).title == "'=уже"


def test_kp_file_plain_text_is_not_changed():
    assert one(line(title="Кладка стен"), result()).title == "Кладка стен"


# --- подрядчику только цель и источник -----------------------------------------------------


def test_kp_file_row_text_has_no_range_bounds_or_median():
    res = result(
        ours=D(100),
        range=RateRange(min=D(100), max=D(400), median=D(150)),
        contracts=(rate(1, "100", nominal="90"), rate(2, "150"), rate(3, "400")),
    )
    ctx = context(contracts=(1, 2, 3), inflation=LABEL)
    row = one(line(), res, ctx=ctx)
    texts = [getattr(row, f.name) for f in fields(FileLine) if isinstance(getattr(row, f.name), str)]
    assert row.comment and row.source and row.inflation
    for text in texts:
        for forbidden in ("150", "400"):
            assert forbidden not in text


# --- годы коэффициентов -------------------------------------------------------------------


def src_contract(cid: int) -> SourceContract:
    return SourceContract(contract=hist_contract(cid), rate=rate(cid, "100"))


USED = [year_fact(y) for y in (2023, 2024, 2025, 2026, 2027)]


def test_kp_file_inflation_years_cover_the_years_of_every_source_contract():
    periods = {1: YearMonth(2024, 3), 2: YearMonth(2025, 6)}
    years = inflation_years_for([src_contract(1), src_contract(2)], plan(used_years=USED), periods)
    # 2024-03 -> 2026-03: 2024, 2025, 2026; 2025-06 -> 2026-03: 2025, 2026
    assert [item["year"] for item in years] == [2024, 2025, 2026]


def test_kp_file_inflation_years_of_one_source_are_only_its_own():
    years = inflation_years_for(
        [src_contract(2)], plan(used_years=USED), {1: YearMonth(2024, 3), 2: YearMonth(2025, 6)}
    )
    assert [item["year"] for item in years] == [2025, 2026]


def test_kp_file_inflation_years_skip_a_contract_that_is_not_a_source():
    periods = {1: YearMonth(2025, 6), 3: YearMonth(2020, 1)}
    years = inflation_years_for([src_contract(1)], plan(used_years=USED), periods)
    assert [item["year"] for item in years] == [2025, 2026]


def test_kp_file_inflation_years_keep_the_fact_dicts_of_the_plan():
    years = inflation_years_for([src_contract(1)], plan(used_years=USED), {1: YearMonth(2025, 6)})
    assert years == [year_fact(2025), year_fact(2026)]
    assert set(years[0]) == {"year", "coefficient", "source", "is_forecast", "updated_at"}


def test_kp_file_inflation_years_are_ascending_whatever_the_plan_order():
    used = [year_fact(2026), year_fact(2024), year_fact(2025)]
    years = inflation_years_for([src_contract(1)], plan(used_years=used), {1: YearMonth(2024, 3)})
    assert [item["year"] for item in years] == [2024, 2025, 2026]


def test_kp_file_inflation_years_ignore_plan_years_no_source_needs():
    years = inflation_years_for([src_contract(1)], plan(used_years=USED), {1: YearMonth(2025, 6)})
    assert 2023 not in [item["year"] for item in years]
    assert 2027 not in [item["year"] for item in years]


def test_kp_file_inflation_years_without_sources_are_empty():
    assert inflation_years_for([], plan(used_years=USED), {}) == []


def test_kp_file_inflation_years_are_empty_when_prices_are_already_at_the_target():
    assert inflation_years_for([src_contract(1)], plan(used_years=USED), {1: YearMonth(2026, 3)}) == []


def test_kp_file_inflation_years_repeated_sources_do_not_repeat_years():
    years = inflation_years_for([src_contract(1), src_contract(1)], plan(used_years=USED), {1: YearMonth(2025, 6)})
    assert [item["year"] for item in years] == [2025, 2026]


def test_kp_file_inflation_years_are_copies_and_leave_the_plan_untouched():
    used = [year_fact(2025), year_fact(2026)]
    years = inflation_years_for([src_contract(1)], plan(used_years=used), {1: YearMonth(2025, 6)})
    years[0]["coefficient"] = D(9)
    assert used[0]["coefficient"] == D("1.05")


def test_kp_file_inflation_years_use_the_target_month_of_the_plan():
    # к 2025-03 из 2024-03: 2024, 2025
    years = inflation_years_for(
        [src_contract(1)], plan(used_years=USED, target_month="2025-03"), {1: YearMonth(2024, 3)}
    )
    assert [item["year"] for item in years] == [2024, 2025]


# --- шапка --------------------------------------------------------------------------------


def header(rows=None, **overrides) -> FileHeader:
    return build_header(context(**overrides), rows if rows is not None else [])


def test_kp_file_header_texts_of_the_sheet():
    head = header()
    assert head.tender == "Тендер Т"
    assert head.object == "Объект Т"
    assert head.contractor == "Подрядчик А"
    assert head.round == "Этап 3 от 15.10.2026"
    assert head.exported_on == "27.10.2026"
    assert head.source_label == "Цены договоров — без НДС (база НДС этого КП — 0 %)"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (D(20), "Цены договоров пересчитаны в базу НДС этого КП — с НДС 20 %"),
        (D(0), "Цены договоров — без НДС (база НДС этого КП — 0 %)"),
    ],
)
def test_kp_file_header_source_label_follows_the_kp_vat_base(base, expected):
    assert header(kp_vat_base=base).source_label == expected


def test_kp_file_header_round_without_a_stage_date_is_the_number_only():
    assert header(stage_date=None).round == "Этап 3"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (D(22), "Цены КП и наши расценки — с НДС 22 % (база НДС этого КП)"),
        (D("22.0"), "Цены КП и наши расценки — с НДС 22 % (база НДС этого КП)"),
        (D(20), "Цены КП и наши расценки — с НДС 20 % (база НДС этого КП)"),
        (D(0), "Цены КП и наши расценки — без НДС (база НДС этого КП — 0 %)"),
    ],
)
def test_kp_file_header_vat_label(base, expected):
    assert header(kp_vat_base=base).vat_label == expected


def test_kp_file_header_level_label_without_a_series_is_nominal():
    assert header().level_label == "номинальный, без приведения"


def test_kp_file_header_level_label_with_a_series_names_series_and_month():
    head = header(inflation=LABEL, inflation_plan=plan(used_years=[]))
    assert head.level_label == "приведено по ИПЦ: Индекс ЦБ, к 03.2026; КП не приводится"


def test_kp_file_header_counts_lines_in_progress():
    rows = build(
        [line(position_item_id=1), line(position_item_id=2), line(position_item_id=3), chapter(4, "1", "Раздел")],
        [
            result(position_item_id=1, state=LineState.in_progress),
            result(position_item_id=2, state=LineState.in_progress),
            result(position_item_id=3),
        ],
    )
    assert header(rows).in_progress_lines == 2


def test_kp_file_header_counts_zero_in_progress_lines():
    assert header(build([line()], [result()])).in_progress_lines == 0


def test_kp_file_header_filename():
    assert header().filename == "КП Подрядчик А этап 3 — наши расценки 2026-10-27.xlsx"


def test_kp_file_header_filename_drops_path_characters_of_the_contractor():
    head = header(contractor_title='ООО "А/Б": <В>')
    assert head.filename == "КП ООО -А-Б-- -В- этап 3 — наши расценки 2026-10-27.xlsx"


def test_kp_file_header_filename_of_a_contractor_without_a_name():
    head = header(contractor_title="   ")
    assert head.filename == "КП подрядчик этап 3 — наши расценки 2026-10-27.xlsx"


def test_kp_file_header_without_a_series_has_no_inflation_block():
    head = header()
    assert (head.inflation_years, head.inflation_series, head.inflation_target_month) == (None, None, None)


def test_kp_file_header_with_a_series_names_it_and_the_target_month():
    head = header(inflation=LABEL, inflation_plan=plan(used_years=[]))
    assert head.inflation_series == "Индекс ЦБ"
    assert head.inflation_target_month == "2026-03"
    assert head.inflation_years == []


def test_kp_file_header_inflation_years_follow_the_sources_named_in_the_inflation_column():
    # договор 1 (2025-06) назван в столбце ИПЦ; договор 2 (2020-01) — источник строки с
    # договорённостью, договор 3 (2021-01) — множитель 1: их ячейки ИПЦ пусты, их лет нет
    ctx = context(
        contracts=(1, 2, 3),
        inflation=LABEL,
        inflation_plan=plan(used_years=[year_fact(y) for y in range(2019, 2028)]),
        contract_periods={1: YearMonth(2025, 6), 2: YearMonth(2020, 1), 3: YearMonth(2021, 1)},
    )
    lines_ = [line(position_item_id=1), line(position_item_id=2), line(position_item_id=3)]
    results = [
        result(position_item_id=1, ours=D(110), contracts=(rate(1, "110", nominal="100"),)),
        result(position_item_id=2, ours=D(110), contracts=(rate(2, "110", nominal="100"),)),
        result(position_item_id=3, ours=D(100), contracts=(rate(3, "100", nominal="100"),)),
    ]
    rows = build(lines_, results, agreements={2: (D(100), AGREED_ON)}, ctx=ctx)
    head = build_header(ctx, rows)
    assert [item["year"] for item in head.inflation_years] == [2025, 2026]


def test_kp_file_header_inflation_years_are_empty_when_no_line_names_the_inflation_column():
    ctx = context(
        inflation=LABEL,
        inflation_plan=plan(used_years=USED),
        contract_periods={1: YearMonth(2024, 3)},
    )
    rows = build([line()], [result(ours=D(100), contracts=(rate(1, "100", nominal="100"),))], ctx=ctx)
    assert build_header(ctx, rows).inflation_years == []


def test_kp_file_header_inflation_years_two_sources_give_both_years():
    ctx = context(
        contracts=(1, 2),
        inflation=LABEL,
        inflation_plan=plan(used_years=[year_fact(y) for y in range(2023, 2028)]),
        contract_periods={1: YearMonth(2024, 3), 2: YearMonth(2025, 6)},
    )
    lines_ = [line(position_item_id=1), line(position_item_id=2)]
    results = [
        result(position_item_id=1, ours=D(110), contracts=(rate(1, "110", nominal="100"),)),
        result(position_item_id=2, ours=D(110), contracts=(rate(2, "110", nominal="100"),)),
    ]
    head = build_header(ctx, build(lines_, results, ctx=ctx))
    assert [item["year"] for item in head.inflation_years] == [2024, 2025, 2026]


def test_kp_file_header_text_is_raw_data():
    head = header(tender_title="=Т", object_title="-О", contractor_title="@П")
    assert (head.tender, head.object, head.contractor) == ("=Т", "-О", "@П")
    assert not head.filename.startswith("'")

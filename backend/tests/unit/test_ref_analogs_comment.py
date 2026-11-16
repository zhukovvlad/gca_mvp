"""Справочная цель в файле подрядчику и комментарии (спека справочных аналогов §2.15, §2.21 п. 1, 4, 5).

Чистый расчёт без базы. Эталоны — независимые литералы: тексты записаны руками, числа посчитаны вне
проверяемого кода. Договор справочной цели назван `Jois 1` (2024), его строка — в м², строка КП — в м³.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from money.inflation import YearMonth
from services.kp_comment import InflationLabel, ReferenceSource
from services.kp_file import FileLine, build_file_rows, build_header
from services.our_price import LineState, Mismatch, OfferEvaluation, ReferenceSnapshot
from tests.unit.test_kp_file_rows import (
    AGREED_ON,
    LABEL,
    OTHER_CONTRACTOR,
    TENDER_CONTRACTOR,
    context,
    hist_contract,
    line,
    plan,
    result,
    system_line,
    system_result,
    year_fact,
)

D = Decimal

REF_ID = 555
SAVED = ("reference", REF_ID)

#: Пример §2.15: строка B12,5 в м³, цель — строка Jois 1 в м² (1 м² = 0,1 м³, толщина 100 мм), база 20 %.
EXAMPLE_COMMENT = (
    "Ваша цена — 28 381,13 ₽ за м³ с НДС 20 %. В нашем договоре по объекту Jois 1, 2024 г., "
    "сопоставимая работа стоила 1 646,90 ₽ за м² с НДС 20 %; 1 м² = 0,1 м³ (толщина 100 мм), "
    "то есть 16 469,04 ₽ за м³. Просим снизить до 16 469,04 ₽ или обосновать разницу."
)
EXAMPLE_SOURCE = (
    "Jois 1, договор № Д-1, 2024 г.: сопоставимая работа (Благоустройство → МАФ), "
    "1 646,90 ₽ за м² с НДС 20 %; 1 м² = 0,1 м³ (толщина 100 мм): 16 469,04 ₽ за м³ с НДС 20 %"
)


def snapshot(factor: str | None = None, basis: str | None = None, *, without_analogs: bool = False) -> ReferenceSnapshot:
    return ReferenceSnapshot(
        origin="search",
        method=None if factor is None else "link",
        factor=None if factor is None else D(factor),
        basis=basis,
        link_id=None if factor is None else 9,
        without_analogs=without_analogs,
    )


def source(
    nominal: str = "100",
    *,
    net: str | None = None,
    unit: str = "м²",
    path: tuple[str, ...] = ("Раздел",),
    snap: ReferenceSnapshot | None = None,
    contract=None,
) -> ReferenceSource:
    return ReferenceSource(
        contract=contract or hist_contract(1, title="Jois 1", number="Д-1", year=2024),
        chapter_path=path,
        unit_text=unit,
        nominal=D(nominal),
        net=D(net if net is not None else nominal),
        snapshot=snap or snapshot(),
    )


def example_source() -> ReferenceSource:
    # 1 372,42 нетто = 1 646,904 с НДС 20 %: цитата 1 646,90; ÷ 0,1 — 13 724,2 нетто = 16 469,04 с НДС
    return source(
        "1372.42",
        path=("Благоустройство", "МАФ"),
        snap=snapshot("0.1", "толщина 100 мм"),
    )


def build_ref(src_line, res, ref: ReferenceSource | None, *, saved=SAVED, comments=None, agreements=None, ctx=None):
    evaluation = OfferEvaluation(lines={res.position_item_id: res}, groups=())
    targets = {} if saved is None else {src_line.position_item_id: saved}
    references = {} if ref is None else {src_line.position_item_id: ref}
    (row,) = build_file_rows(
        [src_line],
        evaluation,
        comments=comments or {},
        agreements=agreements or {},
        targets=targets,
        references=references,
        context=ctx or context(),
    )
    assert isinstance(row, FileLine)
    return row


def no_history(**overrides):
    values = dict(
        state=LineState.no_history, contracts=(), range=None, ours=None, position=None, ladder=None
    )
    values.update(overrides)
    return result(**values)


# --- пример §2.15 ----------------------------------------------------------------------


def test_ref_analogs_example_of_the_spec_comes_out_literally():
    row = build_ref(
        line(unit_text="м³", unit_price=D("28381.13"), total=D("283811.30")),
        result(),
        example_source(),
        ctx=context(kp_vat_base=D(20)),
    )
    assert row.comment == EXAMPLE_COMMENT
    assert row.comment_kind == "template"
    assert row.source == EXAMPLE_SOURCE
    assert row.ours == D("16469.04")
    assert row.deviation_pct == D("72.3")


def test_ref_analogs_the_reference_target_beats_the_lowest_of_the_row_own_ladder():
    """Своя ступень строки с самой низкой 100: цель — справочная (13 724,2 нетто), а не самая низкая."""
    row = build_ref(
        line(unit_text="м³", unit_price=D("28381.13"), total=D("283811.30")),
        result(ours=D(100)),
        example_source(),
        ctx=context(kp_vat_base=D(20)),
    )
    assert row.ours == D("16469.04")
    assert row.ours != D("120")


# --- без пересчёта, с «≈», без разделов ---------------------------------------------------


def test_ref_analogs_without_a_conversion_the_text_has_no_conversion_part():
    row = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100"))
    assert row.source == "Jois 1, договор № Д-1, 2024 г.: сопоставимая работа (Раздел), 100,00 ₽ за м² без НДС"
    assert row.comment == (
        "Ваша цена — 150,00 ₽ за м² без НДС. В нашем договоре по объекту Jois 1, 2024 г., "
        "сопоставимая работа стоила 100,00 ₽ за м² без НДС. Просим снизить до 100,00 ₽ или обосновать разницу."
    )
    assert row.ours == D(100)
    assert row.deviation_pct == D("50.0")


def test_ref_analogs_an_inverted_factor_is_printed_with_the_approx_sign_and_the_money_use_the_exact_one():
    exact = D(2) / D(3)
    row = build_ref(
        line(unit_text="м³", unit_price=D(200)),
        result(),
        source("100", unit="т", snap=snapshot(format(exact, "f"), "по справке")),
    )
    assert row.source == (
        "Jois 1, договор № Д-1, 2024 г.: сопоставимая работа (Раздел), 100,00 ₽ за т без НДС; "
        "1 т ≈ 0,6667 м³ (по справке): 150,00 ₽ за м³ без НДС"
    )
    assert row.comment == (
        "Ваша цена — 200,00 ₽ за м³ без НДС. В нашем договоре по объекту Jois 1, 2024 г., "
        "сопоставимая работа стоила 100,00 ₽ за т без НДС; 1 т ≈ 0,6667 м³ (по справке), "
        "то есть 150,00 ₽ за м³. Просим снизить до 150,00 ₽ или обосновать разницу."
    )
    # по напечатанному 0,6667 вышло бы 149,99; цель не округляется, округляет формат ячейки
    assert row.ours.quantize(D("0.01")) == D("150.00")


def test_ref_analogs_an_exact_four_digit_factor_is_printed_with_the_equals_sign():
    row = build_ref(
        line(unit_text="м³", unit_price=D(200)),
        result(),
        source("100", unit="т", snap=snapshot("0.6667", "по справке")),
    )
    assert "1 т = 0,6667 м³ (по справке)" in row.source
    assert "≈" not in row.source
    assert "≈" not in row.comment


def test_ref_analogs_a_row_outside_the_sections_has_no_parentheses():
    row = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100", path=()))
    assert row.source == "Jois 1, договор № Д-1, 2024 г.: сопоставимая работа, 100,00 ₽ за м² без НДС"


def test_ref_analogs_the_comment_says_with_you_for_the_contract_with_this_contractor():
    contract = replace(hist_contract(1, title="Jois 1", number="Д-1", year=2024), contractor_id=TENDER_CONTRACTOR)
    other = hist_contract(1, title="Jois 1", number="Д-1", year=2024)
    assert other.contractor_id == OTHER_CONTRACTOR
    mine = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100", contract=contract))
    foreign = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100", contract=other))
    assert "В нашем договоре с вами по объекту Jois 1" in mine.comment
    assert "В нашем договоре по объекту Jois 1" in foreign.comment


# --- комментарий: только over ------------------------------------------------------------


def test_ref_analogs_a_price_below_the_target_has_no_template_and_keeps_the_signed_deviation():
    row = build_ref(line(unit_text="м²", unit_price=D(80)), result(position="below"), source("100"))
    assert row.comment is None
    assert row.comment_kind is None
    assert row.ours == D(100)
    assert row.deviation_pct == D("-20.0")


def test_ref_analogs_an_equal_price_has_no_template_and_the_kopeck_decides_the_over_case():
    equal = build_ref(line(unit_text="м²", unit_price=D("100.004")), result(), source("100"))
    above = build_ref(line(unit_text="м²", unit_price=D("100.01")), result(), source("100"))
    assert equal.comment is None
    assert above.comment is not None
    assert "Просим снизить до 100,00 ₽ или обосновать разницу." in above.comment


def test_ref_analogs_the_unit_mismatch_cases_do_not_apply_to_a_reference_target():
    """У строки с расхождением единиц своих аналогов шаблон `kp_unit` есть, у справочной цели его нет."""
    mismatch_result = result(
        state=LineState.unit_mismatch,
        mismatch=Mismatch(kp_ratio=D(10), contracts=((1, D(10)),)),
        contracts=(),
        range=None,
        ours=None,
        position=None,
        ladder=None,
    )
    without = build_ref(line(unit_text="м²", unit_price=D(80)), mismatch_result, None, saved=None)
    with_target = build_ref(line(unit_text="м²", unit_price=D(80)), mismatch_result, source("100"))
    assert without.comment is not None
    assert with_target.comment is None
    assert with_target.ours == D(100)


def test_ref_analogs_an_own_comment_goes_as_it_is_and_an_empty_one_removes_the_template():
    src_line = line(unit_text="м²", unit_price=D(150))
    own = build_ref(src_line, result(), source("100"), comments={1: "Свой текст"})
    empty = build_ref(src_line, result(), source("100"), comments={1: ""})
    assert (own.comment, own.comment_kind) == ("Свой текст", "custom")
    assert (empty.comment, empty.comment_kind) == (None, "custom")


# --- «Договорились», сброс, состояния, виды -----------------------------------------------


def test_ref_analogs_agreed_over_a_reference_target_keeps_the_agreed_source_and_template():
    row = build_ref(
        line(unit_text="м²", unit_price=D(150)),
        result(),
        source("100"),
        agreements={1: (D(120), AGREED_ON)},
    )
    assert row.source == f"Договорились {AGREED_ON}"
    assert row.ours == D(120)
    assert row.comment.startswith("Согласовано: 120,00 ₽ за м² без НДС.")
    assert "сопоставимая работа" not in row.comment


def test_ref_analogs_a_reset_reference_target_goes_into_the_file_as_without_a_choice():
    """Запись цели есть, строки договора среди справочных нет (источника нет): файл — как у строки без выбора."""
    src_line = line(unit_text="м²", unit_price=D(150))
    reset = build_ref(src_line, result(), None, saved=SAVED)
    clean = build_ref(src_line, result(), None, saved=None)
    assert reset == clean
    assert reset.ours == D(100)


def test_ref_analogs_a_reference_target_works_in_a_state_with_no_history():
    row = build_ref(line(unit_text="м²", unit_price=D(150)), no_history(), source("100"))
    assert row.state == LineState.no_history.value
    assert row.ours == D(100)
    assert row.deviation_pct == D("50.0")
    assert row.source.startswith("Jois 1, договор № Д-1, 2024 г.: сопоставимая работа")
    assert row.comment is not None


def test_ref_analogs_a_row_without_a_reference_target_in_no_history_has_no_target():
    row = build_ref(line(unit_text="м²", unit_price=D(150)), no_history(), None, saved=None)
    assert (row.ours, row.deviation_pct, row.source, row.comment) == (None, None, None, None)


def test_ref_analogs_a_source_without_a_saved_reference_kind_is_not_a_target():
    """Выбор — аналог: справочный источник строки цели не даёт, цель — выбор аналога."""
    row = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("70"), saved=("analog", 100))
    assert row.ours == D(100)
    assert "70,00" not in row.source


def test_ref_analogs_a_system_row_ignores_a_reference_target():
    plain = build_ref(system_line(), system_result(), None, saved=None)
    with_target = build_ref(system_line(), system_result(), source("1"))
    assert with_target == plain


# --- ИПЦ ----------------------------------------------------------------------------------


def test_ref_analogs_inflation_column_and_comment_name_the_series_when_the_price_moved():
    ref = source("100", net="110")
    row = build_ref(
        line(unit_text="м²", unit_price=D(150)),
        result(),
        ref,
        ctx=context(inflation=LABEL),
    )
    assert row.inflation == "Индекс ЦБ, к 03.2026: 110,00 ₽ за м² без НДС"
    assert row.ours == D(110)
    assert row.comment == (
        "Ваша цена — 150,00 ₽ за м² без НДС. В нашем договоре по объекту Jois 1, 2024 г., "
        "сопоставимая работа стоила 100,00 ₽ за м² без НДС; с учётом инфляции (Индекс ЦБ, к 03.2026) — "
        "110,00 ₽. Просим снизить до 110,00 ₽ или обосновать разницу."
    )
    assert row.inflation_source.contract.contract_id == 1


def test_ref_analogs_no_inflation_column_when_the_multiplier_is_one_or_the_series_is_not_chosen():
    unchanged = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100"), ctx=context(inflation=LABEL))
    no_series = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100", net="110"))
    assert (unchanged.inflation, unchanged.inflation_source) == (None, None)
    assert (no_series.inflation, no_series.inflation_source) == (None, None)
    assert "инфляции" not in unchanged.comment
    assert "инфляции" not in no_series.comment


def test_ref_analogs_inflation_with_a_conversion_divides_the_inflated_price_by_the_factor():
    ref = source("1000", net="1100", unit="м²", snap=snapshot("0.1", "толщина 100 мм"))
    row = build_ref(
        line(unit_text="м³", unit_price=D(20000)),
        result(),
        ref,
        ctx=context(inflation=InflationLabel("Индекс ЦБ", "03.2026")),
    )
    assert row.ours == D(11000)
    assert row.inflation == "Индекс ЦБ, к 03.2026: 11 000,00 ₽ за м³ без НДС"
    assert "то есть 10 000,00 ₽ за м³; с учётом инфляции (Индекс ЦБ, к 03.2026) — 11 000,00 ₽." in row.comment


def test_ref_analogs_the_years_of_the_header_include_the_year_of_the_reference_contract():
    used = [year_fact(2024), year_fact(2025), year_fact(2026)]
    ctx = context(
        inflation=LABEL, inflation_plan=plan(used_years=used), contract_periods={1: YearMonth(2025, 6)}
    )
    with_ref = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100", net="110"), ctx=ctx)
    without = build_ref(line(unit_text="м²", unit_price=D(150)), result(), source("100"), ctx=ctx)
    assert [f["year"] for f in build_header(ctx, [with_ref]).inflation_years] == [2025, 2026]
    assert build_header(ctx, [without]).inflation_years == []


def test_ref_analogs_the_quote_and_the_target_stand_in_the_vat_base_of_the_kp():
    """Цитата и цель — нетто договора в базе НДС КП: нетто 1 000 при базе 20 % — 1 200,00."""
    row = build_ref(line(unit_text="м²", unit_price=D(1800)), result(), source("1000"), ctx=context(kp_vat_base=D(20)))
    assert row.ours == D(1200)
    assert "1 200,00 ₽ за м² с НДС 20 %" in row.source


def test_ref_analogs_the_figures_of_the_kp_row_do_not_depend_on_the_target():
    """Цифры самой строки КП (цена, вес, сумма, состояние) от справочной цели не зависят."""
    src_line = line(unit_text="м²", unit_price=D(150))
    with_target = build_ref(src_line, result(), source("100"))
    clean = build_ref(src_line, result(), None, saved=None)
    keep = ("position_item_id", "number", "title", "unit", "quantity", "kp_unit_price", "kp_total", "state")
    assert {k: getattr(with_target, k) for k in keep} == {k: getattr(clean, k) for k in keep}

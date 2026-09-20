"""Чистый расчёт листа «Изменения КП» без БД (спека
2026-09-16-tender-changes-export-design.md §2.2-§2.9).

Каждый тест носит в докстроке номер утверждения задачи 2 плана
(`docs/superpowers/plans/2026-09-17-tender-changes-export.md`, раздел Task 2,
«Утверждения») в форме `A2.N` — только для трассировки отчёта задачи, не для
цитирования в самом коде продукта.

Пять входов «состав неполон» (A2.16) взяты той же формы, что синтетический
прогон гейта 2 (`docs/superpowers/specs/2026-09-16-tender-changes-export/check_diagnostic_branches.py`):
пропущенная составляющая, не сходящийся с итогом состав, ровно копейка,
накопление двух полукопеек и нераспределимость `0,004 × 3` против `0,012`.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from services import changes_export as ce
from services.stage_summary import TAX_NET, TAX_NONE

D = Decimal

ARTICLE_A = ce.ArticleRef(code="7", title="Отделка", sort_order=5)
ARTICLE_B = ce.ArticleRef(code="2.5", title="Кровля", sort_order=2)
ARTICLE_C = ce.ArticleRef(code="3.1", title="Фасад", sort_order=8)
ARTICLE_D = ce.ArticleRef(code="4.1", title="Окна", sort_order=9)
ARTICLE_E = ce.ArticleRef(code="5.1", title="Двери", sort_order=10)


def stages(n: int, *, rate="20") -> list[ce.StageInput]:
    """`n` этапов подряд с ОДНОЙ известной ставкой (ось — валовые)."""
    r = None if rate is None else D(rate)
    return [ce.StageInput(stage_no=i + 1, label=None, held_on=None, vat_rate_base=r) for i in range(n)]


def stages_rates(rates: list[str | None]) -> list[ce.StageInput]:
    return [
        ce.StageInput(stage_no=i + 1, label=None, held_on=None, vat_rate_base=None if r is None else D(r))
        for i, r in enumerate(rates)
    ]


def group_row(
    stage_no,
    *,
    article=ARTICLE_A,
    catalog_position_id=1,
    catalog_kind="POSITION",
    work_title="Работа 1",
    unit="м2",
    rows_all=1,
    rows_with_amount=1,
    rows_with_mix=1,
    rows_priced=1,
    amount="100",
    works="40",
    materials="50",
    indirect="10",
    quantity="2",
    price_num=None,
    price_den=None,
    unit_works=None,
    unit_materials=None,
    unit_indirect=None,
    numbers=("1",),
) -> ce.GroupRow:
    def d(v):
        return None if v is None else D(v)

    if price_num is None:
        price_num = amount
    if price_den is None:
        price_den = quantity
    if unit_works is None:
        unit_works = works
    if unit_materials is None:
        unit_materials = materials
    if unit_indirect is None:
        unit_indirect = indirect
    return ce.GroupRow(
        stage_no=stage_no,
        article=article,
        catalog_position_id=catalog_position_id,
        catalog_kind=catalog_kind,
        work_title=work_title,
        unit=unit,
        rows_all=rows_all,
        rows_with_amount=rows_with_amount,
        rows_with_mix=rows_with_mix,
        rows_priced=rows_priced,
        amount=d(amount),
        components=ce.Components(d(works), d(materials), d(indirect)),
        quantity=d(quantity),
        price_num=d(price_num),
        price_den=d(price_den),
        unit_components_num=ce.Components(d(unit_works), d(unit_materials), d(unit_indirect)),
        numbers=numbers,
    )


def additional_row(
    stage_no, *, article=ARTICLE_A, lot_key="ЛОТ-1", chapter_ref_raw="3.2.2",
    work_title="Допработа", amount="30", rows_all=1, rows_with_amount=1,
) -> ce.AdditionalRow:
    return ce.AdditionalRow(
        stage_no=stage_no, article=article, lot_key=lot_key, chapter_ref_raw=chapter_ref_raw,
        work_title=work_title, amount=None if amount is None else D(amount),
        rows_all=rows_all, rows_with_amount=rows_with_amount,
    )


def sheet_of(stage_list, groups=(), additional=()) -> ce.Sheet:
    return ce.build_sheet(ce.SheetInput(
        participant_title="Участник", stages=stage_list, groups=list(groups), additional=list(additional),
    ))


def find_row(sheet: ce.Sheet, kind: str, article: ce.ArticleRef, subkey) -> ce.SheetRow:
    return next(r for r in sheet.rows if r.key == (kind, article, subkey))


# --------------------------------------------------------------------------
# A2.1 — ключ строки: каталожная позиция внутри статьи, свёртка нескольких
# строк сметы в одну ячейку этапа.
# --------------------------------------------------------------------------

class TestKeyAndCollapse:
    def test_a2_1_same_catalog_position_collapses_into_one_cell(self):
        """A2.1: две строки сметы с одной каталожной позицией на одном этапе —
        одна ячейка, сумма сложена."""
        sheet = sheet_of(
            stages(2),
            groups=[
                group_row(1, amount="60", works="20", materials="30", indirect="10", numbers=("1",)),
                group_row(1, amount="40", works="20", materials="20", indirect="0", numbers=("2",)),
                group_row(2, amount="150", works="60", materials="70", indirect="20", numbers=("3",)),
            ],
        )
        matching = [r for r in sheet.rows if r.key == (ce.KIND_WORK, ARTICLE_A, 1)]
        assert len(matching) == 1
        row = matching[0]
        assert row.cells[0].amount.value == D("100")
        assert row.cells[0].rows_all == 2
        assert row.cells[0].numbers == ("1", "2")

    def test_a2_2_four_branches_have_distinct_keys(self):
        """A2.2: ветвей ровно четыре, различаются ключом."""
        sheet = sheet_of(
            stages(1),
            groups=[
                group_row(1, catalog_position_id=1, catalog_kind="POSITION"),
                group_row(1, catalog_position_id=None, catalog_kind=None, numbers=()),
                group_row(1, catalog_position_id=20, catalog_kind="HEADER", numbers=()),
            ],
            additional=[additional_row(1)],
        )
        keys = {r.key[0] for r in sheet.rows}
        assert keys == {ce.KIND_WORK, ce.KIND_UNMATCHED, ce.KIND_NONWORK, ce.KIND_ADDITIONAL}
        work_row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert work_row.key == (ce.KIND_WORK, ARTICLE_A, 1)
        additional_row_ = find_row(sheet, ce.KIND_ADDITIONAL, ARTICLE_A, ("ЛОТ-1", "3.2.2"))
        assert additional_row_.key == (ce.KIND_ADDITIONAL, ARTICLE_A, ("ЛОТ-1", "3.2.2"))
        nonwork_row = find_row(sheet, ce.KIND_NONWORK, ARTICLE_A, "HEADER")
        assert nonwork_row.key == (ce.KIND_NONWORK, ARTICLE_A, "HEADER")
        unmatched_row = find_row(sheet, ce.KIND_UNMATCHED, ARTICLE_A, ce._UNMATCHED_SUBKEY)
        assert unmatched_row.key[0] == ce.KIND_UNMATCHED

    def test_a2_3_position_and_to_review_are_work_headers_lot_trash_are_distinct_diagnostics(self):
        """A2.3: POSITION/TO_REVIEW → работа; HEADER/LOT_HEADER/TRASH — три
        РАЗНЫЕ названные группы; строка без каталожной записи — четвёртая;
        все четыре диагностических названия различны."""
        sheet = sheet_of(
            stages(1),
            groups=[
                group_row(1, catalog_position_id=1, catalog_kind="POSITION", numbers=()),
                group_row(1, catalog_position_id=2, catalog_kind="TO_REVIEW", numbers=()),
                group_row(1, catalog_position_id=20, catalog_kind="HEADER", numbers=()),
                group_row(1, catalog_position_id=21, catalog_kind="LOT_HEADER", numbers=()),
                group_row(1, catalog_position_id=22, catalog_kind="TRASH", numbers=()),
                group_row(1, catalog_position_id=None, catalog_kind=None, numbers=()),
            ],
        )
        assert find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1).kind == ce.KIND_WORK
        assert find_row(sheet, ce.KIND_WORK, ARTICLE_A, 2).kind == ce.KIND_WORK
        titles = {
            find_row(sheet, ce.KIND_NONWORK, ARTICLE_A, "HEADER").work_title,
            find_row(sheet, ce.KIND_NONWORK, ARTICLE_A, "LOT_HEADER").work_title,
            find_row(sheet, ce.KIND_NONWORK, ARTICLE_A, "TRASH").work_title,
            find_row(sheet, ce.KIND_UNMATCHED, ARTICLE_A, ce._UNMATCHED_SUBKEY).work_title,
        }
        assert len(titles) == 4

    def test_a2_4_no_input_row_is_lost(self):
        """A2.4: сумма amount по всем ячейкам этапа равна сумме amount по всем
        строкам входа этого этапа — вход со всеми четырьмя ветвями разом."""
        rows_in = [
            group_row(1, catalog_position_id=1, catalog_kind="POSITION", amount="10", numbers=()),
            group_row(1, catalog_position_id=20, catalog_kind="HEADER", amount="20", numbers=()),
            group_row(1, catalog_position_id=None, catalog_kind=None, amount="30", numbers=()),
        ]
        extra = [additional_row(1, amount="40")]
        sheet = sheet_of(stages(1), groups=rows_in, additional=extra)
        total_in = sum((r.amount for r in rows_in), D(0)) + sum((r.amount for r in extra), D(0))
        total_out = sum(
            (row.cells[0].amount.value for row in sheet.rows if row.cells[0] is not None), D(0)
        )
        assert total_out == total_in

    def test_a2_5_unallocated_group_does_not_break_assembly(self):
        """A2.5: всё без статьи идёт в «Нераспределённое», сборка не отменяется."""
        sheet = sheet_of(
            stages(1),
            groups=[group_row(1, article=ce.UNALLOCATED_ARTICLE, catalog_position_id=1, numbers=())],
            additional=[additional_row(1, article=ce.UNALLOCATED_ARTICLE)],
        )
        assert any(r.article == ce.UNALLOCATED_ARTICLE for r in sheet.rows)
        assert any(s.article == ce.UNALLOCATED_ARTICLE for s in sheet.subtotals)


# --------------------------------------------------------------------------
# A2.6-A2.10 — состояния ячейки, инвариант Money, матрица цены, состав.
# --------------------------------------------------------------------------

class TestCellStatesAndPrice:
    def test_a2_6_money_only_kinds_have_none_quantity_price_components_no_crash(self):
        """A2.6: у MONEY_ONLY_KINDS quantity/unit_price/components — None (для
        quantity/components буквально; unit_price несёт Money с непригодным
        значением), не ноль и не KeyError."""
        sheet = sheet_of(
            stages(1),
            groups=[group_row(1, catalog_position_id=20, catalog_kind="HEADER", numbers=())],
            additional=[additional_row(1)],
        )
        for kind, article, subkey in ((ce.KIND_NONWORK, ARTICLE_A, "HEADER"), (ce.KIND_ADDITIONAL, ARTICLE_A, ("ЛОТ-1", "3.2.2"))):
            row = find_row(sheet, kind, article, subkey)
            cell = row.cells[0]
            assert cell.quantity is None
            assert cell.components is None
            assert cell.unit_price.value is None and cell.unit_price.reason is not None

    def test_a2_7_money_invariant_holds_across_produced_values(self):
        """A2.7: `reason is None` ⟺ `value is not None` — на большом наборе
        произведённых Money."""
        sheet = sheet_of(
            stages(3),
            groups=[
                group_row(1, amount="100"),
                group_row(2, amount=None, rows_with_amount=0, rows_with_mix=0, price_num="0", price_den="0", rows_priced=0),
                group_row(3, amount="0", works="0", materials="0", indirect="0", price_num="0", price_den="0", rows_priced=0),
            ],
        )
        moneys: list[ce.Money] = []
        for row in sheet.rows:
            moneys.append(row.delta_amount)
            for cell in row.cells:
                if cell is not None:
                    moneys.append(cell.amount)
                    moneys.append(cell.unit_price)
        for subtotal in sheet.subtotals:
            moneys.extend(subtotal.by_stage)
            moneys.append(subtotal.delta)
        moneys.extend(sheet.grand_by_stage)
        moneys.append(sheet.grand_delta)
        for m in moneys:
            assert (m.reason is None) == (m.value is not None)

    def test_a2_8_three_amount_states(self):
        """A2.8: строки нет → ячейки нет; строка есть, rows_with_amount=0 →
        amount.value is None, reason=REASON_NO_AMOUNT; конечный ноль → число 0."""
        sheet = sheet_of(
            stages(3),
            groups=[
                group_row(1, amount="0", works="0", materials="0", indirect="0", price_num="0", price_den="0", rows_priced=0),
                group_row(2, amount=None, rows_with_amount=0, rows_with_mix=0, price_num="0", price_den="0", rows_priced=0),
                # этап 3 намеренно пуст для этого key — строки нет вовсе
            ],
        )
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.cells[0].amount.value == D("0") and row.cells[0].amount.reason is None
        assert row.cells[1].amount.value is None and row.cells[1].amount.reason == ce.REASON_NO_AMOUNT
        assert row.cells[2] is None

    def test_a2_9_price_matrix_ignores_unpriced_row_in_denominator_and_gates_on_empty(self):
        """A2.9: цена — Σ(цена×вес)/Σвес по пригодным строкам; нерасценённая
        строка вносит ноль в числитель и свой объём в знаменатель, не занижая
        цену вдвое; пустой знаменатель → unit_price недоступна с REASON_NO_PRICE."""
        # Одна пригодная строка (цена 50, вес 2) и одна непригодная (цена
        # отсутствует по факту неучастия — не добавляет свой вес в price_den).
        sheet = sheet_of(
            stages(1),
            groups=[
                group_row(1, catalog_position_id=1, amount="100", quantity="2",
                          price_num="100", price_den="2", rows_priced=1, numbers=()),
                group_row(1, catalog_position_id=1, amount="0", quantity="5",
                          price_num="0", price_den="0", rows_priced=0, rows_with_mix=0, numbers=()),
            ],
        )
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.cells[0].unit_price.value == D("50")  # не 100/(2+5)=14.28..

        empty = sheet_of(stages(1), groups=[
            group_row(1, catalog_position_id=2, price_num="0", price_den="0", rows_priced=0, numbers=())
        ])
        empty_row = find_row(empty, ce.KIND_WORK, ARTICLE_A, 2)
        assert empty_row.cells[0].unit_price.value is None
        assert empty_row.cells[0].unit_price.reason == ce.REASON_NO_PRICE

    def test_a2_10_unit_mix_same_universe_as_price_absolute_mix_same_universe_as_amount(self):
        """A2.10: состав на единицу — то же множество строк, что цена; состав
        абсолютный — множество суммы. Проверяется тем, что маршрут «состав»
        различает изменение per-unit-состава от изменения абсолютного при
        неизменном объёме (см. TestRoute), а Cell.components несёт абсолютные
        (не на единицу) величины — сумма которых равна amount."""
        sheet = sheet_of(stages(1), groups=[group_row(1, amount="100", works="40", materials="50", indirect="10")])
        cell = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1).cells[0]
        assert cell.components.works + cell.components.materials + cell.components.indirect == cell.amount.value


# --------------------------------------------------------------------------
# A2.11-A2.13 — концы Δ, процент, недоступность.
# --------------------------------------------------------------------------

class TestDeltaEndpoints:
    def test_a2_11_ends_are_first_and_last_stage_not_first_and_last_appearance(self):
        """A2.11: концы — ПЕРВЫЙ и ПОСЛЕДНИЙ этап участника; промежуточное
        отсутствие не смещает концы; отсутствие на конце — нулевой вклад."""
        sheet = sheet_of(
            stages(3),
            groups=[
                group_row(1, catalog_position_id=1, amount="100", numbers=()),
                # этап 2 — строки нет вовсе
                group_row(3, catalog_position_id=1, amount="130", numbers=()),
            ],
        )
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.cells[1] is None
        assert row.delta_amount.value == D("30")

    def test_a2_11b_absent_end_gives_zero_contribution(self):
        sheet = sheet_of(stages(2), groups=[group_row(1, catalog_position_id=1, amount="100", numbers=())])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.cells[1] is None
        assert row.delta_amount.value == D("-100")

    def test_a2_12_delta_pct_none_when_first_missing_or_non_positive(self):
        """A2.12: delta_pct недоступен, если строки на первом этапе нет либо её
        сумма неположительна — ноль и отрицательное дают тот же исход."""
        missing_first = sheet_of(stages(2), groups=[group_row(2, catalog_position_id=1, amount="50", numbers=())])
        assert find_row(missing_first, ce.KIND_WORK, ARTICLE_A, 1).delta_pct is None

        zero_first = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="0", works="0", materials="0", indirect="0", numbers=()),
            group_row(2, catalog_position_id=1, amount="50", numbers=()),
        ])
        assert find_row(zero_first, ce.KIND_WORK, ARTICLE_A, 1).delta_pct is None

        negative_first = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="-10", works="-10", materials="0", indirect="0", numbers=()),
            group_row(2, catalog_position_id=1, amount="50", numbers=()),
        ])
        assert find_row(negative_first, ce.KIND_WORK, ARTICLE_A, 1).delta_pct is None

        positive_first = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="200", numbers=()),
            group_row(2, catalog_position_id=1, amount="190", numbers=()),
        ])
        pct = find_row(positive_first, ce.KIND_WORK, ARTICLE_A, 1).delta_pct
        assert pct == D("-5")

    def test_a2_13_delta_amount_unavailable_when_either_end_lacks_final_sum(self):
        """A2.13: delta_amount.value is None, reason=REASON_NO_AMOUNT, если у
        любого из двух концов есть ячейка без конечной суммы."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount=None, rows_with_amount=0, rows_with_mix=0,
                      price_num="0", price_den="0", rows_priced=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.delta_amount.value is None
        assert row.delta_amount.reason == ce.REASON_NO_AMOUNT


# --------------------------------------------------------------------------
# A2.14, A2.15, A2.18, A2.19 — тождество состава.
# --------------------------------------------------------------------------

class TestMixIdentity:
    def test_a2_14_and_18_identity_holds_exact_rounded_numbers_stored(self):
        """A2.14/A2.18: тождество печатается ТОЛЬКО когда сумма трёх округлённых
        Δ ТОЧНО равна округлённой Δ суммы, и в `delta_components` лежат РОВНО
        эти округлённые числа."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", works="40", materials="50", indirect="10", numbers=()),
            group_row(2, catalog_position_id=1, amount="130", works="52", materials="65", indirect="13", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.components_reason is None
        assert row.delta_components == ce.Components(D("12.00"), D("15.00"), D("3.00"))
        assert row.delta_components.works + row.delta_components.materials + row.delta_components.indirect == D("30.00")

    def test_a2_15_row_level_completeness_first_condition(self):
        """A2.15: построчная полнота — `rows_with_mix` конца равен его
        `rows_with_amount`; отсутствующий конец полон по построению."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", materials=None, rows_with_mix=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE
        assert row.delta_components is None

        # отсутствующий конец: построчно полон по построению — тождество
        # проверяется только на присутствующем конце. Отсутствие строки НЕ
        # значит «Δ суммы недоступна»: по A2.11 отсутствующий конец даёт
        # НУЛЕВОЙ вклад, и Δ считается от него — здесь Δ суммы ДОСТУПНА и
        # равна -100 (0 - 100). Тождество на печатаемых Δ держится:
        # -40.00 + -50.00 + -10.00 = -100.00, поэтому components_reason is
        # None (наследование причины Δ суммы проверяется отдельным входом —
        # см. test_review_components_reason_inherits_delta_amount_reason).
        absent_end = sheet_of(stages(2), groups=[group_row(1, catalog_position_id=1, amount="100", numbers=())])
        row2 = find_row(absent_end, ce.KIND_WORK, ARTICLE_A, 1)
        assert row2.delta_amount.value == D("-100") and row2.delta_amount.reason is None
        assert row2.components_reason is None
        assert row2.delta_components == ce.Components(D("-40.00"), D("-50.00"), D("-10.00"))

    def test_review_a2_15_first_condition_catches_what_identity_would_miss(self):
        """Ревью задачи 2: `test_a2_15` и вход (а) `test_a2_16a` строят
        построчную неполноту так, что она ВСЕГДА заодно ломает и тождество на
        печатаемых Δ (второе условие) — снятие первого условия (`_mix_complete_at`)
        не роняло ни одного из сорока трёх тестов файла. Вход здесь устроен так,
        что пропущенная составляющая (`materials=None` на этапе 1) заменяется
        нулём, который СЛУЧАЙНО делает сумму трёх составляющих равной сумме
        (40 + 0 + 60 = 100) на обоих концах — тождество держится, и только
        построчная неполнота (первое условие) обязана погасить состав."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=50, amount="100", works="40", materials=None,
                      indirect="60", rows_with_mix=0, numbers=()),
            group_row(2, catalog_position_id=50, amount="100", works="40", materials="0",
                      indirect="60", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 50)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE
        assert row.delta_components is None

    def test_a2_19_money_only_kinds_have_no_mix_identity_at_all(self):
        """A2.19: на три денежные ветви тождество не распространяется:
        delta_components is None и components_reason is None — прочерк."""
        sheet = sheet_of(
            stages(2),
            groups=[
                group_row(1, catalog_position_id=20, catalog_kind="HEADER", amount="10", numbers=()),
                group_row(2, catalog_position_id=20, catalog_kind="HEADER", amount="20", numbers=()),
            ],
            additional=[additional_row(1, amount="30"), additional_row(2, amount="40")],
        )
        for kind, subkey in ((ce.KIND_NONWORK, "HEADER"), (ce.KIND_ADDITIONAL, ("ЛОТ-1", "3.2.2"))):
            row = find_row(sheet, kind, ARTICLE_A, subkey)
            assert row.delta_components is None
            assert row.components_reason is None


# --------------------------------------------------------------------------
# A2.16 — «состав неполон», пять входов (а)-(д).
# --------------------------------------------------------------------------

class TestMixIncompleteFiveInputs:
    """Пять входов A2.16, форма — `check_diagnostic_branches.py`."""

    def test_a2_16a_missing_component_caught_by_first_condition(self):
        """(а) строка с конечной суммой и пропущенной составляющей."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", materials=None, rows_with_mix=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE

    def test_a2_16b_components_finite_but_do_not_sum_to_total(self):
        """(б) все величины конечны, но печатаемые Δ не сходятся с итогом:
        построчно полно (rows_with_mix == rows_with_amount), но арифметика
        не сходится — импорт не гарантирует CHECK на согласие четырёх полей."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=31, amount="100", works="10", materials="10", indirect="10", numbers=()),
            group_row(2, catalog_position_id=31, amount="100", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 31)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE
        assert row.delta_components is None

    def test_a2_16c_exact_one_cent_off(self):
        """(в) граница копейки: расхождение ровно в копейку — уже неполнота."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=33, amount="100.00", works="40.00", materials="50.00", indirect="10.01", numbers=()),
            group_row(2, catalog_position_id=33, amount="100.00", works="40.00", materials="50.00", indirect="10.00", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 33)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE

    def test_a2_16d_accumulation_of_two_half_cents(self):
        """(г) накопление: две строки по полкопейки в одну сторону дают копейку,
        притом что построчно каждая полна."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=34, amount="100.00", works="40.00", materials="50.00", indirect="10.005", numbers=()),
            group_row(1, catalog_position_id=34, amount="100.00", works="40.00", materials="50.00", indirect="10.005", numbers=()),
            group_row(2, catalog_position_id=34, amount="100.00", works="40.00", materials="50.00", indirect="10.00", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 34)
        # построчно полны на обоих концах — второе условие ловит арифметику.
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE

    def test_a2_16e_rounding_indivisibility(self):
        """(д) 0,004×3 против 0,012: свёрнутая проверка это пропускает,
        проверка печатаемых величин ловит."""
        sheet = sheet_of(stages(2), groups=[
            group_row(2, catalog_position_id=35, amount="0.012", works="0.004", materials="0.004",
                      indirect="0.004", quantity="1", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 35)
        assert row.components_reason == ce.REASON_MIX_INCOMPLETE

    def test_a2_16_control_matching_case_is_green(self):
        """Контроль: идентично сходящийся случай остаётся зелёным (не всё
        подряд гасится)."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", works="40", materials="50", indirect="10", numbers=()),
            group_row(2, catalog_position_id=1, amount="130", works="52", materials="65", indirect="13", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.components_reason is None


# --------------------------------------------------------------------------
# A2.20-A2.27 — маршрут «Что двигалось».
# --------------------------------------------------------------------------

class TestRoute:
    def test_a2_20_no_gluing_across_missing_intermediate_stage(self):
        """A2.20: соседние — по ШКАЛЕ; пропуск промежуточного не склеивается —
        шаг Э1→Э3 при отсутствующем Э2 не появляется."""
        sheet = sheet_of(
            stages(3),
            additional=[additional_row(1, amount="10"), additional_row(3, amount="10")],
        )
        row = find_row(sheet, ce.KIND_ADDITIONAL, ARTICLE_A, ("ЛОТ-1", "3.2.2"))
        joined = " | ".join(row.route)
        assert "Э1→Э3" not in joined
        assert any("Э1→Э2" in s and "исчезла" in s for s in row.route)
        assert any("Э2→Э3" in s and "появилась" in s for s in row.route)

    def test_a2_21_numeric_bases_require_both_cells_structural_require_transition(self):
        """A2.21: числовые основания требуют обеих ячеек; структурные требуют
        перехода нет↔есть."""
        sheet = sheet_of(stages(2), additional=[additional_row(1, amount="10")])
        row = find_row(sheet, ce.KIND_ADDITIONAL, ARTICLE_A, ("ЛОТ-1", "3.2.2"))
        assert row.route == ["Э1→Э2: исчезла из КП"]
        assert "сумма" not in row.route[0]

    def test_a2_22_amount_unavailable_is_a_distinct_word(self):
        """A2.22: «сумма недоступна» — отдельное слово, не сливается с «сумма»."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount=None, rows_with_amount=0, rows_with_mix=0,
                      price_num="0", price_den="0", rows_priced=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        joined = " ".join(row.route)
        assert "сумма недоступна" in joined
        assert "сумма," not in joined and not joined.strip().startswith("сумма ")

    def test_a2_23_mix_route_basis_is_per_unit_not_absolute(self):
        """A2.23: основание «состав» считается НА ЕДИНИЦУ объёма — по абсолютам
        изменение объёма при неизменных долях выглядело бы сменой состава."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, quantity="2", amount="100", works="40", materials="50",
                      indirect="10", price_num="100", price_den="2",
                      unit_works="40", unit_materials="50", unit_indirect="10", numbers=()),
            # объём и абсолютный состав удвоились, но per-unit состав ТОТ ЖЕ
            group_row(2, catalog_position_id=1, quantity="4", amount="200", works="80", materials="100",
                      indirect="20", price_num="200", price_den="4",
                      unit_works="80", unit_materials="100", unit_indirect="20", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert not any("состав" in s for s in row.route)
        assert any("объём" in s for s in row.route)

    def test_a2_24_article_signature_is_row_local(self):
        """A2.24: на паре «источник — получатель» одного переезда тексты РАЗНЫЕ."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, article=ARTICLE_A, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, article=ARTICLE_B, catalog_position_id=1, amount="100", numbers=()),
        ])
        source = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        target = find_row(sheet, ce.KIND_WORK, ARTICLE_B, 1)
        assert any("ушла в" in s for s in source.route)
        assert any("пришла из" in s for s in target.route)
        assert source.route != target.route

    def test_a2_25_disappear_appear_counted_by_whole_work_not_within_article(self):
        """A2.25: «исчезла»/«появилась» считаются ПО РАБОТЕ ЦЕЛИКОМ — переезд
        между статьями не превращается в исчезновение+появление."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, article=ARTICLE_A, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, article=ARTICLE_B, catalog_position_id=1, amount="100", numbers=()),
        ])
        source = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        target = find_row(sheet, ce.KIND_WORK, ARTICLE_B, 1)
        assert not any("исчезла из КП" in s for s in source.route)
        assert not any("появилась в КП" in s for s in target.route)

    def test_a2_26_brief_cuts_at_brief_limit(self):
        """A2.26: перечень статей режется на BRIEF_LIMIT кодах, дальше «и ещё N»."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, article=ARTICLE_A, catalog_position_id=99, amount="100", numbers=()),
            group_row(2, article=ARTICLE_B, catalog_position_id=99, amount="25", numbers=()),
            group_row(2, article=ARTICLE_C, catalog_position_id=99, amount="25", numbers=()),
            group_row(2, article=ARTICLE_D, catalog_position_id=99, amount="25", numbers=()),
            group_row(2, article=ARTICLE_E, catalog_position_id=99, amount="25", numbers=()),
        ])
        source = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 99)
        step = next(s for s in source.route if "ушла в" in s)
        assert "и ещё 2" in step

    def test_a2_27_moves_note_counts_reclassification_not_appearance_after_empty(self):
        """A2.27: moves_note считает переклассификацией случай, когда ОБА
        множества статей непусты и различаются; появление после пустого этапа —
        не переезд."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, article=ARTICLE_A, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, article=ARTICLE_B, catalog_position_id=1, amount="100", numbers=()),
            # работа 2 появляется только на этапе 2 — не переезд
            group_row(2, article=ARTICLE_C, catalog_position_id=2, amount="50", numbers=()),
        ])
        assert "Э1→Э2: 1 из 2 (50%)" in sheet.moves_note

        no_moves = sheet_of(stages(2), groups=[group_row(1, catalog_position_id=1, amount="100", numbers=())])
        assert no_moves.moves_note == "переездов нет"

    def test_review_d2_route_price_and_mix_require_both_cells_available(self):
        """Ревью финального (Д2): числовые основания «цена» и «состав»
        обязаны требовать присутствия ОБЕИХ величин — тем же правилом, что уже
        действует для «сумма» (A2.22). Вход: ставки [20, None, 20], одна и та
        же группа на всех трёх этапах. До правки `unit_price.value != None` и
        `_per_unit_mix(...) != None` читаются как движение, хотя недоступна
        только база НДС этапа 2 — маршрут ложно утверждает «цена, состав»."""
        sheet = sheet_of(
            stages_rates(["20", None, "20"]),
            groups=[
                group_row(1, catalog_position_id=1, amount="100", quantity="2",
                          price_num="100", price_den="2", works="40", materials="50",
                          indirect="10", numbers=()),
                group_row(2, catalog_position_id=1, amount="100", quantity="2",
                          price_num="100", price_den="2", works="40", materials="50",
                          indirect="10", numbers=()),
                group_row(3, catalog_position_id=1, amount="100", quantity="2",
                          price_num="100", price_den="2", works="40", materials="50",
                          indirect="10", numbers=()),
            ],
        )
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.route == ["Э1→Э2: сумма недоступна", "Э2→Э3: сумма недоступна"]
        assert not any("цена" in s for s in row.route)
        assert not any("состав" in s for s in row.route)

    def test_review_d2_route_price_den_zero_on_one_side_is_no_price_not_change(self):
        """Родственный вход (Д2): `price_den=0` на одной стороне (все строки
        нерасценены) — это «цены нет» (`AGENTS.md`: ноль/пустая цена значит
        только «цены нет»), а не «цена изменилась». До правки отсутствие цены
        на одном конце читается как её появление и печатает «цена, состав»."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", quantity="2",
                      price_num="0", price_den="0", rows_priced=0, rows_with_mix=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", quantity="2",
                      price_num="100", price_den="2", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.route == []

    def test_review_d2_route_still_prints_price_and_mix_when_both_cells_available(self):
        """Обратная сторона Д2 (слой 14 `docs/insights/verifying-guards.md`:
        предъявлять и то состояние, которое выбранная ветка вытесняет). На
        ОБЫЧНОМ входе — обе базы НДС известны, цена за единицу и состав НА
        ЕДИНИЦУ реально различаются, объём тот же — слова «цена» и «состав»
        обязаны по-прежнему печататься. Без этого входа охрана Д2 остаётся
        зелёной и тогда, когда оба слова не печатаются НИКОГДА: замер ревью
        показал, что замена обоих условий на `if False:` не роняла ни одного
        теста выборки `changes_export`."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", quantity="2",
                      price_num="100", price_den="2", works="40", materials="50",
                      indirect="10", numbers=()),
            # цена за единицу 50 → 70; состав на единицу (20, 25, 5) → (45, 20, 5)
            group_row(2, catalog_position_id=1, amount="140", quantity="2",
                      price_num="140", price_den="2", works="90", materials="40",
                      indirect="10", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.route == ["Э1→Э2: сумма, цена, состав"]


# --------------------------------------------------------------------------
# A2.28-A2.33 — ось НДС, подытоги, общий итог.
# --------------------------------------------------------------------------

class TestVatAxisAndTotals:
    def test_a2_28_unknown_vat_base_gates_price_amount_components_on_that_stage(self):
        """A2.28: на этапе с неизвестной базой цена, сумма и компоненты
        погашены с REASON_UNKNOWN_VAT_BASE; Δ с таким концом несёт причину."""
        sheet = sheet_of(stages_rates(["20", None]), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="120", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        gated = row.cells[1]
        assert gated.amount.value is None and gated.amount.reason == ce.REASON_UNKNOWN_VAT_BASE
        assert gated.unit_price.value is None and gated.unit_price.reason == ce.REASON_UNKNOWN_VAT_BASE
        assert gated.components is None
        assert row.delta_amount.value is None
        assert row.delta_amount.reason == ce.REASON_UNKNOWN_VAT_BASE

    def test_review_components_reason_inherits_delta_amount_reason(self):
        """Ревью задачи 2 (план, «Решения плана» п.10): `components_reason`
        обязан наследовать причину `delta_amount` (здесь — REASON_UNKNOWN_VAT_BASE
        от оси), а не всегда печатать REASON_MIX_INCOMPLETE — это подменило бы
        недоступность по ОСИ подписью, которая обещает нечто другое (нарушение
        построчного/сходимостного условия там, где на деле неизвестна база НДС).
        Ни один из сорока трёх исходных тестов не различал эти две причины:
        подмена `_row_delta_components` на константный REASON_MIX_INCOMPLETE не
        роняла ни одного из них."""
        sheet = sheet_of(stages_rates(["20", None]), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="120", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.delta_amount.reason == ce.REASON_UNKNOWN_VAT_BASE
        assert row.components_reason == ce.REASON_UNKNOWN_VAT_BASE
        assert row.components_reason != ce.REASON_MIX_INCOMPLETE

    def test_a2_29_subtotal_and_grand_share_the_same_money_type(self):
        """A2.29: Subtotal.by_stage[i] и Sheet.grand_by_stage[i] — оба
        Money(None, REASON_UNKNOWN_VAT_BASE, ...) на этапе с неизвестной базой."""
        sheet = sheet_of(stages_rates(["20", None]), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="120", numbers=()),
        ])
        subtotal = next(s for s in sheet.subtotals if s.article == ARTICLE_A)
        assert subtotal.by_stage[1] == ce.Money(None, ce.REASON_UNKNOWN_VAT_BASE, False)
        assert sheet.grand_by_stage[1] == ce.Money(None, ce.REASON_UNKNOWN_VAT_BASE, False)
        assert subtotal.by_stage[0].value == D("100")
        assert sheet.grand_by_stage[0].value == D("100")

    def test_a2_30_tax_none_gates_everything_on_every_stage(self):
        """A2.30: при TAX_NONE недоступны ВСЕ суммы, ВСЕ подытоги, весь общий
        итог и все Δ на каждом этапе."""
        sheet = sheet_of(stages_rates([None, None]), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="120", numbers=()),
        ])
        assert sheet.tax_basis.basis == TAX_NONE
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert all(c.amount.value is None for c in row.cells)
        assert row.delta_amount.value is None
        for subtotal in sheet.subtotals:
            assert all(m.value is None for m in subtotal.by_stage)
            assert subtotal.delta.value is None
        assert all(m.value is None for m in sheet.grand_by_stage)
        assert sheet.grand_delta.value is None

    def test_a2_31_row_level_unavailability_zeroes_and_marks_incomplete_axis_gates_entirely(self):
        """A2.31: недоступность по СТРОКЕ (сумма отсутствует) на этапе с
        ИЗВЕСТНОЙ базой вносит в подытог ноль и не гасит, но поднимает
        incomplete; недоступность по ОСИ гасит подытог целиком."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount=None, rows_with_amount=0, rows_with_mix=0,
                      price_num="0", price_den="0", rows_priced=0, numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        subtotal = next(s for s in sheet.subtotals if s.article == ARTICLE_A)
        assert subtotal.by_stage[0].value == D("0")
        assert subtotal.by_stage[0].reason is None
        assert subtotal.by_stage[0].incomplete is True

    def test_review_d4_partial_cell_raises_subtotal_and_grand_incomplete(self):
        """Ревью финального (Д4, план — решение 10, `AGENTS.md` §6): ячейка,
        где `rows_with_amount < rows_all` (частичная свёртка, а НЕ `amount.value
        is None`), обязана поднимать `incomplete` подытога статьи и общего
        итога — тот же факт, что уже поднимает `row.delta_amount.incomplete`
        (A2.33) для строки. До правки подытог поднимает `incomplete` только
        когда сумма ячейки целиком недоступна, и частичная сумма (1 строка из
        2) вносится молча. ВАЖНО: подытог обязан остаться ЧИСЛОМ."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", rows_all=2, rows_with_amount=1,
                      rows_with_mix=1, numbers=()),
            group_row(2, catalog_position_id=1, amount="130", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.delta_amount.incomplete is True  # уже верно (A2.33), контроль входа

        subtotal = next(s for s in sheet.subtotals if s.article == ARTICLE_A)
        assert subtotal.by_stage[0].value == D("100")
        assert subtotal.by_stage[0].incomplete is True
        assert sheet.grand_by_stage[0].value == D("100")
        assert sheet.grand_by_stage[0].incomplete is True

    def test_a2_32_only_two_delta_fields_and_grand_by_stage_is_per_stage_not_delta(self):
        """A2.32: Δ считают ровно два поля — Subtotal.delta и Sheet.grand_delta;
        grand_by_stage — список этапных итогов, не Δ."""
        field_names = {f for f in ce.Subtotal.__dataclass_fields__}
        assert "delta" in field_names
        sheet_fields = set(ce.Sheet.__dataclass_fields__)
        assert "grand_delta" in sheet_fields and "grand_by_stage" in sheet_fields

        sheet = sheet_of(stages(3), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="110", numbers=()),
            group_row(3, catalog_position_id=1, amount="130", numbers=()),
        ])
        assert [m.value for m in sheet.grand_by_stage] == [D("100"), D("110"), D("130")]
        assert sheet.grand_delta.value == D("30")

    def test_a2_33_incomplete_end_propagates_to_delta_incomplete(self):
        """A2.33: неполнота любого числового конца переходит в delta.incomplete."""
        sheet = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=1, amount="100", rows_all=2, rows_with_amount=1,
                      rows_with_mix=1, numbers=()),
            group_row(2, catalog_position_id=1, amount="130", numbers=()),
        ])
        row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert row.delta_amount.incomplete is True


# --------------------------------------------------------------------------
# A2.34 — порядок операций: нетто до округления.
# --------------------------------------------------------------------------

class TestOrderOfOperations:
    def test_a2_34_net_conversion_happens_before_rounding(self):
        """A2.34: приведение к нетто выполняется ДО округления. Ставка 7%
        даёт непредставимую конечной десятичной дробью величину — если бы
        `Cell.amount` уже был округлён на этапе конвертации, он совпал бы со
        своим `money_round`; он не совпадает, значит округление отложено до
        проверки тождества/показа."""
        sheet = sheet_of(stages_rates(["7", "10"]), groups=[
            group_row(1, catalog_position_id=1, amount="100", numbers=()),
            group_row(2, catalog_position_id=1, amount="100", numbers=()),
        ])
        assert sheet.tax_basis.basis == TAX_NET
        cell = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1).cells[0]
        assert cell.amount.value != ce.money_round(cell.amount.value)


# --------------------------------------------------------------------------
# A2.35 — полнота двух множеств строк.
# --------------------------------------------------------------------------

class TestCompleteness:
    def test_a2_35_completeness_separates_amount_and_price_money_only_kinds_show_amount_only(self):
        """A2.35: «Полнота» различает сумму и цену раздельно; у MONEY_ONLY_KINDS
        бывает только «сумма»."""
        sheet = sheet_of(
            stages(1),
            groups=[
                group_row(1, catalog_position_id=1, rows_all=14, rows_with_amount=13, rows_priced=12, numbers=()),
                group_row(1, catalog_position_id=20, catalog_kind="HEADER", rows_all=3, rows_with_amount=2, numbers=()),
            ],
        )
        work_row = find_row(sheet, ce.KIND_WORK, ARTICLE_A, 1)
        assert "сумма 13/14" in work_row.completeness and "цена 12/14" in work_row.completeness

        nonwork_row = find_row(sheet, ce.KIND_NONWORK, ARTICLE_A, "HEADER")
        assert "сумма 2/3" in nonwork_row.completeness
        assert "цена" not in nonwork_row.completeness


# --------------------------------------------------------------------------
# A2.36 — sheet_names.
# --------------------------------------------------------------------------

class TestSheetNames:
    def test_a2_36_cleans_truncates_dedupes_and_replaces_empty(self):
        long_name = "А" * 40
        names = ce.sheet_names(["Обычный", "a/b\\c:d*e?f[g]h", "", long_name, long_name])
        assert names[0] == "Обычный"
        assert names[1] == "a-b-c-d-e-f-g-h"
        assert names[2] == "Участник 3"
        assert all(len(n) <= 31 for n in names)
        assert names[3] != names[4]
        assert names[3] == long_name[:31]
        assert names[4].endswith(" (2)") and len(names[4]) <= 31

    def test_h2_review_dedupes_by_casefold_not_literal_string(self):
        """Внешнее ревью (H2): Excel и openpyxl считают имена листов
        регистронезависимо. Три имени по 31 знаку, различающихся только
        регистром, обязаны разводиться суффиксами так же, как буквально
        совпадающие, а не считаться тремя разными именами."""
        upper = "X" * 31
        lower = "x" * 31
        mixed = "X" * 30 + "x"
        names = ce.sheet_names([upper, lower, mixed])
        assert len({n.casefold() for n in names}) == 3
        assert all(len(n) <= 31 for n in names)
        assert names[0] == upper
        # Д5 внешнего ревью: casefold() — инструмент СРАВНЕНИЯ, а не построения
        # имени. Мутант, который строит суффиксное имя из `base.casefold()`
        # (а не из `base`), отдал бы ['XXX…X', 'xxx…x (2)', 'xxx…x (3)'] —
        # третье имя потеряло бы РЕГИСТР — и прошёл бы прежние проверки:
        # `.endswith(" (2)")`/`.endswith(" (3)")` не смотрят на префикс, а
        # `lower` совпадает с собственным casefold и ничего не показал бы.
        # Точное совпадение строки ловит потерю регистра именно там, где
        # входное имя было в верхнем регистре («mixed»).
        assert names[1] == lower[: 31 - len(" (2)")] + " (2)"
        assert names[2] == mixed[: 31 - len(" (3)")] + " (3)"


# --------------------------------------------------------------------------
# A2.37, A2.38 — порядок строк листа, article_sort_key.
# --------------------------------------------------------------------------

class TestOrdering:
    def test_a2_37_row_order_is_deterministic_across_runs(self):
        """A2.37: два прогона на одном входе дают одинаковый порядок."""
        groups = [
            group_row(1, article=ARTICLE_A, catalog_position_id=2, work_title="Бета", numbers=()),
            group_row(1, article=ARTICLE_A, catalog_position_id=1, work_title="Альфа", numbers=()),
            group_row(1, article=ARTICLE_B, catalog_position_id=3, work_title="Гамма", numbers=()),
        ]
        sheet1 = sheet_of(stages(1), groups=groups)
        sheet2 = sheet_of(stages(1), groups=list(groups))
        assert [r.key for r in sheet1.rows] == [r.key for r in sheet2.rows]
        # первичный ключ — sort_order статьи (ARTICLE_B=2 раньше ARTICLE_A=5),
        # вторичный — наименование работы («Альфа» раньше «Бета» внутри A).
        assert [r.article for r in sheet1.rows] == [ARTICLE_B, ARTICLE_A, ARTICLE_A]
        assert [r.work_title for r in sheet1.rows if r.article == ARTICLE_A] == ["Альфа", "Бета"]

    def test_a2_38_article_sort_key_never_compares_none_with_string_and_unallocated_is_last(self):
        """A2.38: article_sort_key никогда не сравнивает None со строкой; вход
        из статьи 10.1, статьи 2.5 и «Нераспределённого» сортируется порядком
        классификатора, «Нераспределённое» — последним."""
        article_10_1 = ce.ArticleRef(code="10.1", title="X", sort_order=1)
        article_2_5 = ce.ArticleRef(code="2.5", title="Y", sort_order=2)
        groups = [
            group_row(1, article=article_10_1, catalog_position_id=1, numbers=()),
            group_row(1, article=article_2_5, catalog_position_id=2, numbers=()),
            group_row(1, article=ce.UNALLOCATED_ARTICLE, catalog_position_id=3, numbers=()),
        ]
        # не должно бросать TypeError при сравнении None и str/int.
        sheet = sheet_of(stages(1), groups=groups)
        assert [r.article for r in sheet.rows] == [article_10_1, article_2_5, ce.UNALLOCATED_ARTICLE]
        assert ce.article_sort_key(article_10_1) < ce.article_sort_key(article_2_5)
        assert ce.article_sort_key(ce.UNALLOCATED_ARTICLE) > ce.article_sort_key(article_2_5)


# --------------------------------------------------------------------------
# Снятия защиты — демонстрируют, что тесты выше действительно зависят от
# правил, а не совпадают с ними случайно (проверяется прогоном отдельно от
# набора, см. отчёт задачи). Здесь — только регрессия на подмену
# `finance.money_round`, которую можно выполнить через monkeypatch без правки
# исходника: остальные два снятия (построчный допуск, свёртка перед
# округлением) требуют временной правки кода `_row_delta_components` и
# выполнены вручную (см. отчёт).
# --------------------------------------------------------------------------

class TestRoundingRuleRegression:
    def test_half_up_not_half_even_at_the_005_boundary(self):
        """Если бы `_row_delta_components` звал `Decimal.quantize` без
        `ROUND_HALF_UP` (умолчание — ROUND_HALF_EVEN), эта граница дала бы
        другой ответ: 0,005 не переживает подмену.

        Проверено на РЕАЛЬНОМ вызываемом имени модуля (`services.changes_export.money_round`),
        а не на копии функции: подмена перехватывает именно тот путь, которым
        пользуется `_row_delta_components`.
        """
        def _half_even(value, places=2):
            from decimal import Decimal as Dec
            exp = Dec(f"0.{'0' * places}")
            return Dec(value).quantize(exp)  # ROUND_HALF_EVEN умолчания

        sheet_correct = sheet_of(stages(2), groups=[
            group_row(1, catalog_position_id=40, amount="100.00", works="40.00",
                      materials="50.00", indirect="10.005", numbers=()),
            group_row(2, catalog_position_id=40, amount="100.01", works="40.00",
                      materials="50.00", indirect="10.01", numbers=()),
        ])
        row_correct = find_row(sheet_correct, ce.KIND_WORK, ARTICLE_A, 40)

        with patch("services.changes_export.money_round", _half_even):
            sheet_buggy = sheet_of(stages(2), groups=[
                group_row(1, catalog_position_id=40, amount="100.00", works="40.00",
                          materials="50.00", indirect="10.005", numbers=()),
                group_row(2, catalog_position_id=40, amount="100.01", works="40.00",
                          materials="50.00", indirect="10.01", numbers=()),
            ])
            row_buggy = find_row(sheet_buggy, ce.KIND_WORK, ARTICLE_A, 40)

        assert row_correct.components_reason != row_buggy.components_reason or \
            row_correct.delta_components != row_buggy.delta_components

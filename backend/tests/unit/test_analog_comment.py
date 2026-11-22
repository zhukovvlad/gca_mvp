"""Цель строки-работы и её источник в комментарии и файле (спека лестницы аналогов §2.14, §2.15).

Чистый расчёт без базы: статус выбора (`resolve_target`), аналог цели (`target_analog`), вход
комментария работы (`line_comment_inputs`) и строки файла. Названия и числа выдуманы, эталоны
посчитаны руками; площадь объекта тендера 100, база НДС КП 0 (цены КП равны нетто).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from money.inflation import YearMonth
from services.analog_ladder import Analog, Figure, WorkLadder
from services.kp_comment import comment_case, target_net, template_text, work_source
from services.kp_file import build_file_rows, build_header
from services.our_price import (
    ContractRate,
    LineState,
    Mismatch,
    OfferEvaluation,
    TargetChoice,
    resolve_target,
    target_analog,
)
from tests.unit.test_kp_file_rows import (
    LABEL,
    context,
    line,
    plan,
    result,
    system_line,
    system_result,
    year_fact,
)
from tests.unit.test_kp_file_rows import comment_inputs as work_inputs
from tests.unit.test_kp_file_rows import one as one_row

D = Decimal

#: аналоги: (строка, договор, цена с приведением, номинал)
LOW = Analog(101, 1, D(100), D(100), None, 0, None)
MID = Analog(102, 2, D(120), D(120), None, 0, None)
FRESH = Analog(103, 3, D(150), D(150), None, 0, None)
OUTLIER = Analog(104, 4, D(900), D(900), None, 0, D("7.5"))
ANALOGS = (LOW, MID, FRESH, OUTLIER)


def ladder(*, rung_no=1, analogs=ANALOGS, lowest=LOW, freshest=FRESH) -> WorkLadder:
    remainder = frozenset(a.position_item_id for a in analogs if a.outlier_ratio is None)
    return WorkLadder(
        rung_no=rung_no,
        released=(),
        other_class=rung_no in (3, 4),
        zone="whole",
        analogs=analogs,
        remainder_ids=remainder,
        lowest=Figure(lowest.net, lowest.position_item_id, lowest.contract_id),
        freshest=None if freshest is None else Figure(freshest.net, freshest.position_item_id, freshest.contract_id),
        all_outliers=False,
        kp_ratio=None,
        no_history_reason=None,
        vat_dropped=(),
    )


def comparable(*, kp="160", rung_no=1, analogs=ANALOGS, freshest=FRESH, **overrides):
    """Сравнимая работа: самая низкая 100, самая свежая 150, цена КП `kp`."""
    lad = ladder(rung_no=rung_no, analogs=analogs, freshest=freshest)
    values = dict(
        kp_unit_net=D(kp),
        ours=LOW.net,
        contracts=(),
        ladder=lad,
        freshest=lad.freshest,
        position="above",
    )
    values.update(overrides)
    return result(**values)


def source_line(kp="160"):
    return line(unit_price=D(kp), total=D(kp) * 2)


# ---------------------------------------------------------------------------
#  Статус выбора: порядок причин §2.14
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_no_record_is_no_choice(self):
        assert resolve_target(None, comparable(), is_system=False) is None

    @pytest.mark.parametrize(
        ("saved", "expected_reason"),
        [
            pytest.param(("analog", 102), None, id="analog-in-remainder"),
            pytest.param(("freshest", None), None, id="freshest-exists"),
            pytest.param(("analog", 104), "analog_unavailable", id="analog-is-an-outlier"),
            pytest.param(("analog", 999), "analog_unavailable", id="analog-left-the-history"),
        ],
    )
    def test_a_comparable_work_acts_or_names_the_analog_condition(self, saved, expected_reason):
        choice = resolve_target(saved, comparable(), is_system=False)

        assert choice == TargetChoice(saved[0], saved[1], expected_reason is None, expected_reason)

    def test_freshest_without_a_freshest_figure_is_no_freshest(self):
        res = comparable(freshest=None)

        assert resolve_target(("freshest", None), res, is_system=False) == TargetChoice(
            "freshest", None, False, "no_freshest"
        )

    def test_the_record_survives_a_reset_with_its_own_kind_and_analog(self):
        res = comparable(freshest=None)

        choice = resolve_target(("analog", 999), res, is_system=False)

        assert (choice.kind, choice.analog_id, choice.active) == ("analog", 999, False)

    @pytest.mark.parametrize(
        "state", [s for s in LineState if s not in (LineState.comparable, LineState.lump_sum)]
    )
    def test_a_work_that_is_not_comparable_does_not_act(self, state):
        res = result(state=state, mismatch=None, ladder=None)

        assert resolve_target(("freshest", None), res, is_system=False).reset_reason == "not_comparable"

    def test_a_system_is_kind_changed_even_when_it_is_not_comparable_either(self):
        res = result(state=LineState.no_price, ladder=None)

        assert resolve_target(("analog", 101), res, is_system=True).reset_reason == "kind_changed"

    def test_kind_changed_goes_before_not_comparable_and_not_comparable_before_the_analog(self):
        not_comparable_with_missing_analog = result(state=LineState.no_history, ladder=None)

        assert (
            resolve_target(("analog", 999), not_comparable_with_missing_analog, is_system=True).reset_reason
            == "kind_changed"
        )
        assert (
            resolve_target(("analog", 999), not_comparable_with_missing_analog, is_system=False).reset_reason
            == "not_comparable"
        )
        assert resolve_target(("analog", 999), comparable(), is_system=False).reset_reason == "analog_unavailable"

    def test_unit_mismatch_work_is_not_comparable(self):
        res = comparable(state=LineState.unit_mismatch, mismatch=Mismatch(kp_ratio=D(10), contracts=()))

        assert resolve_target(("analog", 102), res, is_system=False).reset_reason == "not_comparable"


# ---------------------------------------------------------------------------
#  Аналог цели
# ---------------------------------------------------------------------------


class TestTargetAnalog:
    def test_without_a_choice_the_target_is_the_lowest(self):
        assert target_analog(comparable(), None) == LOW

    def test_an_acting_analog_choice_picks_that_analog(self):
        res = comparable()
        choice = resolve_target(("analog", 102), res, is_system=False)

        assert target_analog(res, choice) == MID

    def test_an_acting_freshest_choice_picks_the_freshest_analog(self):
        res = comparable()
        choice = resolve_target(("freshest", None), res, is_system=False)

        assert target_analog(res, choice) == FRESH

    def test_a_reset_choice_leaves_the_lowest(self):
        res = comparable()
        choice = resolve_target(("analog", 104), res, is_system=False)

        assert not choice.active
        assert target_analog(res, choice) == LOW

    @pytest.mark.parametrize("state", [LineState.no_history, LineState.unit_mismatch, LineState.no_price])
    def test_a_work_that_is_not_comparable_has_no_target(self, state):
        assert target_analog(result(state=state, ladder=None), None) is None


class TestWorkSource:
    def test_the_source_is_one_line_of_the_contract_with_the_nominal_kept(self):
        inflated = Analog(105, 2, D("110"), D("100"), None, 0, None)

        source = work_source(context(contracts=(1, 2)).history, inflated)

        assert source.contract.contract_id == 2
        assert source.rate == ContractRate(contract_id=2, net=D("110"), nominal=D("100"), lines=1)


# ---------------------------------------------------------------------------
#  Вход комментария: источник и цель работы
# ---------------------------------------------------------------------------

CTX = context(contracts=(1, 2, 3, 4))


def inputs_for(res, *, saved=None, **overrides):
    return work_inputs(source_line(res.kp_unit_net), res, history=CTX.history, saved_target=saved, **overrides)


class TestWorkCommentInputs:
    def test_without_a_choice_the_source_is_the_lowest_analog(self):
        inp = inputs_for(comparable())

        assert inp.source.contract.contract_id == 1
        assert target_net(inp) == D(100)

    def test_a_chosen_analog_is_the_source_and_the_target(self):
        inp = inputs_for(comparable(), saved=("analog", 102))

        assert (inp.source.contract.contract_id, inp.source.rate.nominal) == (2, D(120))
        assert target_net(inp) == D(120)

    def test_the_freshest_choice_takes_the_freshest_analog(self):
        inp = inputs_for(comparable(), saved=("freshest", None))

        assert (inp.source.contract.contract_id, target_net(inp)) == (3, D(150))

    def test_a_reset_choice_falls_back_to_the_lowest(self):
        inp = inputs_for(comparable(), saved=("analog", 104))

        assert (inp.source.contract.contract_id, target_net(inp)) == (1, D(100))

    def test_the_agreed_price_beats_the_choice(self):
        inp = inputs_for(comparable(), saved=("analog", 102), agreed_net=D(90))

        assert target_net(inp) == D(90)

    @pytest.mark.parametrize(("rung_no", "level"), [(1, "v"), (2, "p"), (3, "v"), (4, "p")])
    def test_the_level_follows_the_ladder_rung(self, rung_no, level):
        assert inputs_for(comparable(rung_no=rung_no)).level == level

    def test_a_system_ignores_the_saved_work_choice_and_keeps_its_own_source(self):
        inp = work_inputs(
            system_line(), system_result(), history=CTX.history, saved_target=("analog", 102), agreed_net=None
        )

        assert inp.source.contract.contract_id == 1
        assert inp.level == "v"


class TestCaseAgainstTheTarget:
    def test_kp_above_the_lowest_but_below_the_freshest_gets_a_comment_only_on_the_lowest_target(self):
        res = comparable(kp="120", position="in")

        assert comment_case(inputs_for(res)) == "over"
        assert comment_case(inputs_for(res, saved=("freshest", None))) is None
        assert template_text(inputs_for(res, saved=("freshest", None))) is None

    def test_kp_above_the_chosen_analog_is_over_and_names_that_contract(self):
        text = template_text(inputs_for(comparable(kp="160"), saved=("analog", 102)))

        assert "эта работа стоила 120,00 ₽ за м2" in text
        assert "Просим снизить до 120,00 ₽" in text

    @pytest.mark.parametrize(
        ("rung_no", "phrase"),
        [
            (1, "эта работа стоила 100,00"),
            (2, "сопоставимая работа стоила 100,00"),
            (3, "эта работа стоила 100,00"),
            (4, "сопоставимая работа стоила 100,00"),
        ],
    )
    def test_the_phrase_follows_the_ladder_rung_in_the_singular(self, rung_no, phrase):
        text = template_text(inputs_for(comparable(rung_no=rung_no)))

        assert phrase in text
        assert "сопоставимые" not in text

    def test_a_work_never_says_on_average(self):
        for saved in (None, ("freshest", None), ("analog", 102)):
            assert "в среднем" not in template_text(inputs_for(comparable(), saved=saved))

    def test_the_below_case_does_not_depend_on_the_target(self):
        res = comparable(kp="90", position="below")

        assert comment_case(inputs_for(res, saved=("freshest", None))) == "below"

    @pytest.mark.parametrize(
        ("mismatch", "case"),
        [
            (Mismatch(kp_ratio=D(10), contracts=()), "kp_unit"),
            (Mismatch(kp_ratio=None, contracts=()), "history_unit"),
        ],
    )
    def test_the_unit_cases_do_not_need_a_source(self, mismatch, case):
        res = comparable(state=LineState.unit_mismatch, mismatch=mismatch, ours=None, freshest=None)

        inp = inputs_for(res, saved=("analog", 102))

        assert inp.source is None
        assert comment_case(inp) == case
        assert template_text(inp)

    def test_the_inflation_tail_follows_the_multiplier_of_the_chosen_analog(self):
        inflated = Analog(105, 2, D("132"), D("120"), None, 0, None)
        plain = Analog(106, 3, D("150"), D("150"), None, 0, None)
        res = comparable(analogs=(LOW, inflated, plain))

        with_tail = template_text(inputs_for(res, saved=("analog", 105), inflation=LABEL))
        without_tail = template_text(inputs_for(res, saved=("analog", 106), inflation=LABEL))

        assert "эта работа стоила 120,00 ₽ за м2 без НДС; с учётом инфляции (Индекс ЦБ, к 03.2026) — 132,00 ₽." in with_tail
        assert "с учётом инфляции" not in without_tail


# ---------------------------------------------------------------------------
#  Файл: цель, источник, столбец ИПЦ, годы блока
# ---------------------------------------------------------------------------


def file_row(res, *, targets, ctx=CTX):
    (row,) = _rows_with_targets([source_line(res.kp_unit_net)], [res], targets, ctx)
    return row


def _rows_with_targets(lines_, results, targets, ctx):
    evaluation = OfferEvaluation(lines={r.position_item_id: r for r in results}, groups=())
    return build_file_rows(lines_, evaluation, comments={}, agreements={}, targets=targets, context=ctx)


class TestFileRows:
    def test_without_a_choice_the_file_names_the_lowest(self):
        row = file_row(comparable(), targets={})

        assert row.ours == D(100)
        assert row.source == "Объект Т, договор № Д-1, 2024 г.: 100,00 ₽ за м2 без НДС"

    def test_a_chosen_analog_sets_ours_deviation_and_source(self):
        row = file_row(comparable(), targets={1: ("analog", 102)})

        assert row.ours == D(120)
        assert row.deviation_pct == D("33.3")
        assert row.source == "Объект Т, договор № Д-1, 2024 г.: 120,00 ₽ за м2 без НДС"
        assert row.comment_kind == "template"

    def test_the_freshest_target_below_it_leaves_the_comment_empty_and_the_deviation_negative(self):
        res = comparable(kp="120", position="in")
        src = line(unit_price=D(120), total=D(240))
        row = one_row(src, res, ctx=CTX)
        assert row.comment_kind == "template"

        (fresh_row,) = _rows_with_targets([src], [res], {1: ("freshest", None)}, CTX)

        assert (fresh_row.ours, fresh_row.deviation_pct) == (D(150), D("-20.0"))
        assert (fresh_row.comment, fresh_row.comment_kind) == (None, None)

    def test_the_inflation_cell_and_the_source_follow_the_chosen_analog(self):
        inflated = Analog(105, 2, D("132"), D("120"), None, 0, None)
        res = comparable(analogs=(LOW, inflated, FRESH))
        ctx = context(contracts=(1, 2, 3), inflation=LABEL)

        row = file_row(res, targets={1: ("analog", 105)}, ctx=ctx)
        plain = file_row(res, targets={}, ctx=ctx)

        assert row.inflation == "Индекс ЦБ, к 03.2026: 132,00 ₽ за м2 без НДС"
        assert row.inflation_source.contract.contract_id == 2
        assert row.source.endswith("120,00 ₽ за м2 без НДС")
        assert plain.inflation is None

    def test_the_header_years_follow_the_analogs_the_file_names_not_the_lowest(self):
        inflated = Analog(105, 2, D("132"), D("120"), None, 0, None)
        res = comparable(analogs=(LOW, inflated, FRESH))
        ctx = context(
            contracts=(1, 2, 3),
            inflation=LABEL,
            inflation_plan=plan(used_years=[year_fact(y) for y in range(2019, 2028)]),
            contract_periods={1: YearMonth(2020, 1), 2: YearMonth(2025, 6), 3: YearMonth(2021, 1)},
        )

        chosen = build_rows_with(res, ctx, {1: ("analog", 105)})
        lowest = build_rows_with(res, ctx, {})

        assert [item["year"] for item in build_header(ctx, chosen).inflation_years] == [2025, 2026]
        # самая низкая (договор 1) без приведения: ни одна ячейка ИПЦ не названа, лет нет
        assert build_header(ctx, lowest).inflation_years == []

    def test_a_system_row_does_not_read_the_work_choice(self):
        res = system_result()
        (row,) = _rows_with_targets([system_line()], [res], {1: ("analog", 102)}, context())

        assert row.ours == D(125)


def build_rows_with(res, ctx, targets):
    return _rows_with_targets([source_line(res.kp_unit_net)], [res], targets, ctx)


# ---------------------------------------------------------------------------
#  «Комплект вместо ставки» (спека строк-сумм §5 п. 2)
# ---------------------------------------------------------------------------


class TestResolveTargetLump:
    LUMP_ROW = staticmethod(lambda: result(state=LineState.lump_sum, mismatch=None, ladder=None))

    @pytest.mark.parametrize(
        "saved",
        [
            pytest.param(("freshest", None), id="freshest"),
            pytest.param(("analog", 102), id="analog"),
            pytest.param(("contractor", None), id="contractor"),
            pytest.param(("reference", 555), id="reference"),
        ],
    )
    def test_a_lump_sum_row_resets_every_kind_of_target_even_the_reference_one(self, saved):
        choice = resolve_target(saved, self.LUMP_ROW(), is_system=False, reference_available=True)

        assert choice == TargetChoice(saved[0], saved[1], False, "lump_sum")

    def test_a_system_is_kind_changed_before_a_lump_sum_row(self):
        res = self.LUMP_ROW()

        assert resolve_target(("analog", 102), res, is_system=True, target_lump=True).reset_reason == "kind_changed"

    def test_a_lump_sum_row_beats_reference_unavailable(self):
        choice = resolve_target(("reference", 555), self.LUMP_ROW(), is_system=False, reference_available=False)

        assert choice.reset_reason == "lump_sum"

    @pytest.mark.parametrize(
        ("saved", "available"),
        [
            pytest.param(("analog", 102), False, id="analog-in-the-remainder"),
            pytest.param(("analog", 999), False, id="analog-gone-from-the-remainder"),
            pytest.param(("reference", 555), True, id="reference-available"),
            pytest.param(("reference", 555), False, id="reference-unavailable"),
        ],
    )
    def test_a_target_on_a_lump_contract_row_is_lump_sum_before_the_unavailable_reasons(self, saved, available):
        choice = resolve_target(saved, comparable(), is_system=False, reference_available=available, target_lump=True)

        assert choice == TargetChoice(saved[0], saved[1], False, "lump_sum")

    @pytest.mark.parametrize(
        ("saved", "reason"), [(("freshest", None), None), (("contractor", None), "no_contractor")]
    )
    def test_the_lump_flag_of_a_target_row_does_not_touch_targets_without_a_contract_row(self, saved, reason):
        choice = resolve_target(saved, comparable(), is_system=False, target_lump=True)

        assert choice.reset_reason == reason

    def test_without_the_flag_a_comparable_row_and_its_analog_act_as_before(self):
        assert resolve_target(("analog", 102), comparable(), is_system=False, target_lump=False).active is True
        assert resolve_target(("analog", 999), comparable(), is_system=False).reset_reason == "analog_unavailable"
        assert (
            resolve_target(("reference", 555), comparable(), is_system=False, reference_available=False).reset_reason
            == "reference_unavailable"
        )


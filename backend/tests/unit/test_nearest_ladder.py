"""Сужение отпущенных шкал в лестнице аналогов (спека ближайшего значения §2).

Чистый расчёт без базы, помощники — из `test_analog_ladder`. Условные имена: параметр 20 («Класс»,
отпускаемая шкала), параметр 21 («Марка», отпускаемая шкала), параметр 11 («Прочее», обязательный).
Значения «Класса»: B25=201, B30=202, B35=203, B40=204, B60=205, «В30» кириллицей=206; значения
«Марки»: W8=211, W12=212. Строки истории — вариант W8, строка КП — вариант W6, поэтому ступень 1
пуста, а ступень 2 берёт всех с тем же обязательным параметром. Эталоны посчитаны руками.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from money.inflation import YearMonth
from services.analog_ladder import (
    MIN_CONTRACTS,
    FamilyLadderInfo,
    KpWork,
    WorkHistory,
    WorkHistoryLine,
    WorkLadder,
    excluded_trace,
    ladder_for,
)
from services.reference_blocks import zone_groups
from services.value_scale import Scale
from tests.unit.test_analog_ladder import (
    CLASS_A,
    CLASS_B,
    FAMILY,
    REQUIRED,
    SCHEMA,
    W6,
    W8,
    contract,
    kp,
    line,
)

D = Decimal

CLS = 20
MARK = 21
CLS_NAME = "Класс"
MARK_NAME = "Марка"

B25, B30, B35, B40, B60, CYR_B30 = 201, 202, 203, 204, 205, 206
W8M, W12M = 211, 212
MERGED = 299  # значение снято или слито: в шкале его нет

CLASS_SCALE = Scale(
    numbers={B25: D(25), B30: D(30), B35: D(35), B40: D(40), B60: D(60), CYR_B30: D(30)},
    texts={B25: "B25", B30: "B30", B35: "B35", B40: "B40", B60: "B60", CYR_B30: "В30"},
)
MARK_SCALE = Scale(numbers={W8M: D(8), W12M: D(12)}, texts={W8M: "W8", W12M: "W12"})

#: Шкала «40, 60, 90» для случаев вокруг числа КП 60.
V40, V60, V90, V120 = 301, 302, 303, 304
AROUND_SCALE = Scale(
    numbers={V40: D(40), V60: D(60), V90: D(90), V120: D(120)},
    texts={V40: "40", V60: "60", V90: "90", V120: "120"},
)

NAMES = {CLS: CLS_NAME, MARK: MARK_NAME, REQUIRED: "Прочее"}


def info_of(
    scales: dict[int, Scale],
    *,
    releasable: tuple[int, ...] = (CLS,),
    order: tuple[int, ...] | None = None,
) -> FamilyLadderInfo:
    main = order if order is not None else tuple(p for p in (CLS, MARK) if p in releasable or p in scales) + (REQUIRED,)
    return FamilyLadderInfo(
        schema_id=SCHEMA,
        main_parameter_ids=main,
        releasable_ids=frozenset(releasable),
        names=NAMES,
        scales=scales,
    )


def work(cls_value: int | None, mark_value: int | None = None, **kwargs: Any) -> KpWork:
    values: dict[int, int | None] = {CLS: cls_value, REQUIRED: 5}
    if mark_value is not None:
        values[MARK] = mark_value
    return kp(values=values, **kwargs)


def row(
    pid: int, cid: int, price: str, cls_value: int | None, mark_value: int | None = None, **kwargs: Any
) -> WorkHistoryLine:
    values: dict[int, int | None] = {CLS: cls_value, REQUIRED: 5}
    if mark_value is not None:
        values[MARK] = mark_value
    kwargs.setdefault("variant", W8)
    return line(pid, cid, price, values=values, **kwargs)


def contracts_for(lines: list[WorkHistoryLine], *, cls: int = CLASS_A) -> list:
    return [contract(cid, cls=cls) for cid in sorted({ln.contract_id for ln in lines})]


def history_of(lines: list[WorkHistoryLine], info: FamilyLadderInfo, **kwargs: Any) -> WorkHistory:
    return WorkHistory(
        lines,
        {FAMILY: info},
        {c.contract_id: c for c in contracts_for(lines, **kwargs)},
        factors=None,
    )


def ladder(kp_work: KpWork, lines: list[WorkHistoryLine], info: FamilyLadderInfo, **kwargs: Any) -> WorkLadder:
    return ladder_for(
        kp_work,
        history_of(lines, info, **kwargs),
        tender_class_id=CLASS_A,
        tender_contractor_id=99,
        target_month=YearMonth(2026, 1),
    )


def plain(info: FamilyLadderInfo) -> FamilyLadderInfo:
    """Та же семья без шкал — как до ближайшего значения."""
    return dataclasses.replace(info, scales={})


def picked_ids(result: WorkLadder) -> list[int]:
    return sorted(a.position_item_id for a in result.analogs)


# ---------------------------------------------------------------------------
#  Круги (спека §2 п. 4)
# ---------------------------------------------------------------------------


class TestRounds:
    def test_the_exact_value_in_three_contracts_takes_only_that_round_and_matches(self):
        lines = [row(1, 1, "100", B25), row(2, 2, "100", B25), row(3, 3, "100", B25), row(4, 4, "100", B30)]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        assert result.rung_no == 2
        assert picked_ids(result) == [1, 2, 3]
        assert result.matched == frozenset({CLS})
        assert result.nearest == ()
        assert result.released == ()

    def test_the_exact_value_in_two_contracts_takes_the_next_round_as_well(self):
        lines = [
            row(1, 1, "100", B25),
            row(2, 2, "100", B25),
            row(3, 3, "100", B30),
            row(4, 4, "100", B35),
        ]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        assert picked_ids(result) == [1, 2, 3]
        assert result.matched == frozenset()
        (param,) = result.nearest
        assert (param.parameter_id, param.name, param.kp_value) == (CLS, CLS_NAME, "B25")
        assert param.kp_contracts == 2
        assert param.values == ("B25", "B30")
        assert param.direction == "up"
        assert param.without_value is False
        assert param.after == ()

    def test_equal_distance_on_both_sides_is_one_round(self):
        # КП 60: 40 и 90 равноудалены (1,5 и 1,5), 120 дальше. Три договора набрались в первом круге.
        lines = [
            row(1, 1, "100", V40),
            row(2, 2, "100", V40),
            row(3, 3, "100", V90),
            row(4, 4, "100", V120),
        ]

        result = ladder(work(V60), lines, info_of({CLS: AROUND_SCALE}))

        assert picked_ids(result) == [1, 2, 3]
        (param,) = result.nearest
        assert param.values == ("40", "90")
        assert param.direction == "both"
        assert param.kp_contracts == 0

    def test_when_rounds_run_out_the_candidates_without_a_number_are_added(self):
        lines = [
            row(1, 1, "100", B30),
            row(2, 2, "100", B30),
            row(3, 3, "100", None),  # не уточнено
            row(4, 4, "100", MERGED),  # значение слито
            row(5, 5, "100", B35, conflicted=frozenset({CLS})),  # конфликт
        ]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        assert picked_ids(result) == [1, 2, 3, 4, 5]
        (param,) = result.nearest
        assert param.without_value is True
        assert param.values == ("B30",)
        assert param.direction == "up"
        assert param.kp_contracts == 0

    def test_without_a_last_round_the_candidates_without_a_number_stay_out(self):
        lines = [
            row(1, 1, "100", B30),
            row(2, 2, "100", B30),
            row(3, 3, "100", B30),
            row(4, 4, "100", None),
        ]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        assert picked_ids(result) == [1, 2, 3]
        assert result.nearest[0].without_value is False

    def test_candidates_without_any_number_release_the_parameter_and_keep_the_set(self):
        lines = [row(1, 1, "100", None), row(2, 2, "100", MERGED), row(3, 3, "100", None)]
        info = info_of({CLS: CLASS_SCALE})

        result = ladder(work(B25), lines, info)

        assert result.nearest == ()
        assert result.matched == frozenset()
        assert result.released == (CLS_NAME,)
        assert result == ladder(work(B25), lines, plain(info))

    def test_contracts_are_counted_by_distinct_contract_not_by_line(self):
        # Три строки одного договора — это один договор: второй круг нужен.
        lines = [row(1, 1, "100", B25), row(2, 1, "100", B25), row(3, 1, "100", B25), row(4, 2, "100", B30)]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        assert picked_ids(result) == [1, 2, 3, 4]

    def test_min_contracts_is_three(self):
        assert MIN_CONTRACTS == 3

    def test_b30_in_latin_and_cyrillic_is_a_match(self):
        lines = [row(1, 1, "100", B30), row(2, 2, "100", CYR_B30), row(3, 3, "100", B30), row(4, 4, "100", B25)]

        result = ladder(work(B30), lines, info_of({CLS: CLASS_SCALE}))

        assert picked_ids(result) == [1, 2, 3]
        assert result.matched == frozenset({CLS})
        assert result.nearest == ()

    # Ревью: тест «по одному договору» с двух сторон не отличает один круг от двух подряд (договоров
    # меньше трёх, круги кончаются в любом случае). Здесь каждая сторона сама набирает три договора:
    # разбитый на два круга подбор взял бы только одну сторону.
    def test_equal_distance_is_one_round_even_when_one_side_alone_has_three_contracts(self):
        lines = [
            row(1, 1, "100", V40),
            row(2, 2, "100", V40),
            row(3, 3, "100", V40),
            row(4, 4, "100", V90),
            row(5, 5, "100", V90),
            row(6, 6, "100", V90),
            row(7, 7, "100", V120),
        ]

        result = ladder(work(V60), lines, info_of({CLS: AROUND_SCALE}))

        assert picked_ids(result) == [1, 2, 3, 4, 5, 6]
        (param,) = result.nearest
        assert (param.values, param.direction) == (("40", "90"), "both")

    # Ревью: в шкалах теста id растут вместе с числом, поэтому сортировка только по id проходила бы.
    # «В30» (id 206) и B60 (id 205): по числу — B30, В30, B60; по id — B30, B60, В30.
    def test_values_are_ordered_by_number_then_by_value_id(self):
        lines = [row(1, 1, "100", CYR_B30), row(2, 2, "100", B60), row(3, 3, "100", B30)]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        (param,) = result.nearest
        assert param.values == ("B30", "В30", "B60")
        assert param.direction == "up"

    # Ревью: `kp_contracts` считает договоры, а не строки.
    def test_kp_contracts_counts_distinct_contracts(self):
        lines = [row(1, 1, "100", B25), row(2, 1, "100", B25), row(3, 2, "100", B30), row(4, 3, "100", B35)]

        result = ladder(work(B25), lines, info_of({CLS: CLASS_SCALE}))

        (param,) = result.nearest
        assert param.kp_contracts == 1
        assert param.values == ("B25", "B30", "B35")


# ---------------------------------------------------------------------------
#  Пропуск шкалы (спека §2 п. 2)
# ---------------------------------------------------------------------------


def _skip_cases():
    kp_none = work(None)
    kp_conflict = work(B25, conflicted=frozenset({CLS}))
    kp_composite = dataclasses.replace(work(77), forms={CLS: 77})
    kp_outside = work(MERGED)
    return [
        pytest.param(kp_none, id="kp-value-not-specified"),
        pytest.param(kp_conflict, id="kp-conflict"),
        pytest.param(kp_composite, id="kp-composite"),
        pytest.param(kp_outside, id="kp-value-outside-the-scale"),
    ]


@pytest.mark.parametrize("kp_work", _skip_cases())
def test_a_skipped_scale_is_released_and_the_set_equals_the_set_without_scales(kp_work: KpWork) -> None:
    form = kp_work.forms
    lines = [
        dataclasses.replace(row(1, 1, "100", B25), forms=form),
        dataclasses.replace(row(2, 2, "110", B40), forms=form),
        dataclasses.replace(row(3, 3, "120", B60), forms=form),
    ]
    if form:
        lines = [dataclasses.replace(ln, main_values={CLS: 77, REQUIRED: 5}) for ln in lines]
    info = info_of({CLS: CLASS_SCALE})

    result = ladder(kp_work, lines, info)

    assert result.rung_no == 2
    assert result.nearest == ()
    assert result.matched == frozenset()
    assert result.released == (CLS_NAME,)
    assert result == ladder(kp_work, lines, plain(info))


# ---------------------------------------------------------------------------
#  Несколько шкал (спека §2 п. 5, 7)
# ---------------------------------------------------------------------------


def _slab_lines() -> list[WorkHistoryLine]:
    """Плита B35 W12: B40 W12 в двух договорах, B40 W8 в одном, B60 W12 в трёх."""
    return [
        row(1, 1, "100", B40, W12M),
        row(2, 2, "100", B40, W12M),
        row(3, 3, "100", B40, W8M),
        row(4, 4, "100", B60, W12M),
        row(5, 5, "100", B60, W12M),
        row(6, 6, "100", B60, W12M),
    ]


class TestTwoScales:
    def test_class_then_mark_narrow_in_turn_and_the_mark_follows_the_class(self):
        info = info_of({CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))

        result = ladder(work(B35, W12M), _slab_lines(), info)

        assert picked_ids(result) == [1, 2, 3]
        klass, mark = result.nearest
        assert (klass.name, klass.values, klass.direction, klass.after) == (CLS_NAME, ("B40",), "up", ())
        assert klass.kp_contracts == 0
        assert (mark.name, mark.values, mark.direction, mark.after) == (MARK_NAME, ("W8", "W12"), "down", (CLS_NAME,))
        assert mark.kp_contracts == 2
        assert result.released == ()

    def test_reversed_parameter_order_gives_another_result(self):
        info = info_of(
            {CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK), order=(MARK, CLS, REQUIRED)
        )

        result = ladder(work(B35, W12M), _slab_lines(), info)

        assert picked_ids(result) == [1, 2, 4, 5, 6]
        assert result.matched == frozenset({MARK})
        (klass,) = result.nearest
        assert (klass.name, klass.values, klass.direction, klass.after) == (CLS_NAME, ("B40", "B60"), "up", (MARK_NAME,))

    def test_a_late_scale_removes_the_value_taken_by_an_early_one(self):
        # КП 60 и W12: 40 с W8 в одном договоре, 90 с W12 в трёх. Первая шкала берёт 40 и 90, вторая
        # оставляет только 90: пометка «90 вверх», а не «40–90 в обе стороны».
        lines = [
            row(1, 1, "100", V40, W8M),
            row(2, 2, "100", V90, W12M),
            row(3, 3, "100", V90, W12M),
            row(4, 4, "100", V90, W12M),
        ]
        info = info_of({CLS: AROUND_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))

        result = ladder(work(V60, W12M), lines, info)

        assert picked_ids(result) == [2, 3, 4]
        (klass,) = result.nearest
        assert (klass.values, klass.direction) == (("90",), "up")
        assert result.matched == frozenset({MARK})

    def test_a_late_scale_that_leaves_only_rows_without_a_number_releases_the_early_one(self):
        lines = [
            row(1, 1, "100", V40, W8M),
            row(2, 2, "100", None, W12M),
            row(3, 3, "100", None, W12M),
            row(4, 4, "100", None, W12M),
        ]
        info = info_of({CLS: AROUND_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))

        result = ladder(work(V60, W12M), lines, info)

        assert picked_ids(result) == [2, 3, 4]
        assert result.nearest == ()
        assert result.matched == frozenset({MARK})
        assert result.released == (CLS_NAME,)

    # Ревью: `after` — только не пропущенные шкалы.
    def test_a_skipped_scale_is_not_in_after(self):
        lines = [
            row(1, 1, "100", B25, W12M),
            row(2, 2, "100", B40, W12M),
            row(3, 3, "100", B60, W8M),
        ]
        info = info_of({CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))

        result = ladder(work(None, W12M), lines, info)

        (mark,) = result.nearest
        assert mark.name == MARK_NAME
        assert mark.after == ()
        assert result.released == (CLS_NAME,)

    # Ревью: шкала без чисел в окончательном наборе идёт в `released`, но на своём шаге набор сузила —
    # поэтому стоит в `after` следующей (спека §2 п. 7). В имеющемся тесте вторая шкала совпала и в
    # `nearest` не попадала, так что `after` не проверялся.
    def test_a_scale_left_without_numbers_still_stands_in_after_of_the_next(self):
        w16 = 213
        mark_scale = Scale(numbers={W8M: D(8), W12M: D(12), w16: D(16)}, texts={W8M: "W8", W12M: "W12", w16: "W16"})
        lines = [
            row(1, 1, "100", V40, W8M),
            row(2, 2, "100", None, W12M),
            row(3, 3, "100", None, W12M),
            row(4, 4, "100", None, w16),
        ]
        info = info_of({CLS: AROUND_SCALE, MARK: mark_scale}, releasable=(CLS, MARK))

        result = ladder(work(V60, W12M), lines, info)

        assert picked_ids(result) == [2, 3, 4]
        (mark,) = result.nearest
        assert (mark.name, mark.values, mark.direction, mark.kp_contracts) == (MARK_NAME, ("W12", "W16"), "up", 2)
        assert mark.after == (CLS_NAME,)
        assert mark.without_value is False
        assert result.released == (CLS_NAME,)
        assert result.matched == frozenset()


# ---------------------------------------------------------------------------
#  released (спека §2 п. 7)
# ---------------------------------------------------------------------------


class TestReleased:
    def test_released_lists_the_releasable_without_nearest_and_matched(self):
        lines = [row(1, 1, "100", B30, W12M), row(2, 2, "100", B30, W12M), row(3, 3, "100", B35, W12M)]
        info = info_of({CLS: CLASS_SCALE}, releasable=(CLS, MARK))

        result = ladder(work(B25, W12M), lines, info)

        assert [n.name for n in result.nearest] == [CLS_NAME]
        assert result.released == (MARK_NAME,)

    def test_with_empty_scales_released_is_the_old_list(self):
        lines = [row(1, 1, "100", B30, W12M)]
        info = info_of({}, releasable=(CLS, MARK), order=(CLS, MARK, REQUIRED))

        result = ladder(work(B25, W12M), lines, info)

        assert result.released == (CLS_NAME, MARK_NAME)
        assert result.nearest == ()
        assert result.matched == frozenset()

    def test_rungs_one_and_three_have_no_released_and_no_narrowing(self):
        info = info_of({CLS: CLASS_SCALE})
        same_variant = [row(1, 1, "100", B30, variant=W6), row(2, 2, "100", B60, variant=W6)]

        one = ladder(work(B25), same_variant, info)
        three = ladder(work(B25), same_variant, info, cls=CLASS_B)

        assert (one.rung_no, three.rung_no) == (1, 3)
        assert one == ladder(work(B25), same_variant, plain(info))
        assert three == ladder(work(B25), same_variant, plain(info), cls=CLASS_B)
        assert one.released == three.released == ()


# ---------------------------------------------------------------------------
#  Выпавшие без НДС (спека §2 п. 5)
# ---------------------------------------------------------------------------


class TestVatDropped:
    def _lines(self, dropped_value: int) -> list[WorkHistoryLine]:
        return [
            row(1, 1, "100", B40),
            row(2, 2, "100", B40),
            row(3, 3, "100", B40),
            row(4, 4, "100", dropped_value, vat=None),
        ]

    def test_a_far_value_without_vat_is_not_dropped_and_a_taken_value_is(self):
        info = info_of({CLS: CLASS_SCALE})

        far = ladder(work(B35), self._lines(B60), info)
        taken = ladder(work(B35), self._lines(B40), info)

        assert far.vat_dropped == ()
        assert taken.vat_dropped == (4,)
        assert ladder(work(B35), self._lines(B60), plain(info)).vat_dropped == (4,)

    def test_the_same_number_in_another_text_is_accepted_as_the_taken_value(self):
        lines = [
            row(1, 1, "100", B30),
            row(2, 2, "100", B30),
            row(3, 3, "100", B30),
            row(4, 4, "100", CYR_B30, vat=None),
        ]

        result = ladder(work(B30), lines, info_of({CLS: CLASS_SCALE}))

        assert result.vat_dropped == (4,)

    def test_a_row_without_a_number_passes_only_after_the_last_round(self):
        info = info_of({CLS: CLASS_SCALE})
        with_last_round = [row(1, 1, "100", B30), row(2, 2, "100", B30), row(3, 3, "100", None, vat=None)]
        without_last_round = [
            row(1, 1, "100", B30),
            row(2, 2, "100", B30),
            row(3, 3, "100", B30),
            row(4, 4, "100", None, vat=None),
        ]

        assert ladder(work(B25), with_last_round, info).vat_dropped == (3,)
        assert ladder(work(B25), without_last_round, info).vat_dropped == ()

    # Ревью: при двух шкалах строка без НДС должна пройти каждую (спека §2 п. 5). Марка первой берёт
    # только W12; класс затем берёт B40 и B60. B40 W8 проходит класс, но не марку.
    def test_with_two_scales_a_row_must_pass_every_narrowed_parameter(self):
        lines = _slab_lines() + [row(7, 7, "100", B40, W8M, vat=None), row(8, 8, "100", B40, W12M, vat=None)]
        info = info_of({CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK), order=(MARK, CLS, REQUIRED))

        result = ladder(work(B35, W12M), lines, info)

        assert result.vat_dropped == (8,)
        assert ladder(work(B35, W12M), lines, plain(info)).vat_dropped == (7, 8)

    # Ревью: шкала, пропущенная из-за кандидатов без чисел, выпавших не фильтрует.
    def test_a_scale_skipped_for_lack_of_numbers_does_not_filter_the_dropped(self):
        lines = [
            row(1, 1, "100", None),
            row(2, 2, "100", MERGED),
            row(3, 3, "100", None),
            row(4, 4, "100", B60, vat=None),
        ]
        info = info_of({CLS: CLASS_SCALE})

        result = ladder(work(B25), lines, info)

        assert result.vat_dropped == (4,)
        assert result == ladder(work(B25), lines, plain(info))


# ---------------------------------------------------------------------------
#  Цифры по суженному набору (спека §2 п. 6)
# ---------------------------------------------------------------------------


def test_figures_are_counted_on_the_narrowed_set() -> None:
    lines = [
        row(1, 1, "100", B40),
        row(2, 2, "110", B40),
        row(3, 3, "120", B40),
        row(4, 4, "90", B60),  # самая низкая — но далёкого значения
    ]
    info = info_of({CLS: CLASS_SCALE})

    narrowed = ladder(work(B35), lines, info)
    wide = ladder(work(B35), lines, plain(info))

    assert narrowed.lowest is not None and narrowed.lowest.value == D("100")
    assert narrowed.lowest.analog_id == 1
    assert wide.lowest is not None and wide.lowest.analog_id == 4
    assert 4 not in narrowed.remainder_ids


# ---------------------------------------------------------------------------
#  Блоки по зонам (спека §2 п. 8)
# ---------------------------------------------------------------------------


def test_zone_group_carries_the_nearest_of_the_ladder_of_that_zone() -> None:
    lines = [
        row(1, 1, "100", B40, zone="aboveground"),
        row(2, 2, "100", B40, zone="aboveground"),
        row(3, 3, "100", B60, zone="aboveground"),
    ]
    info = info_of({CLS: CLASS_SCALE})
    history = history_of(lines, info)
    own = dataclasses.replace(work(B35, zone="underground"), zone_required=True)

    groups = zone_groups(own, history, tender_class_id=CLASS_A, tender_contractor_id=99, target_month=YearMonth(2026, 1))

    assert [g.zone for g in groups] == ["aboveground"]
    expected = ladder(dataclasses.replace(own, zone="aboveground"), lines, info).nearest
    assert expected
    assert groups[0].nearest == expected
    assert [r.position_item_id for r in groups[0].rows] == [1, 2, 3]


# ---------------------------------------------------------------------------
#  Критические свойства (спека §2 п. 6)
# ---------------------------------------------------------------------------


def _scenarios():
    """Входы задачи: кортеж (КП, строки, семья, класс договоров)."""
    single = info_of({CLS: CLASS_SCALE})
    two = info_of({CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))
    two_rev = info_of({CLS: CLASS_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK), order=(MARK, CLS, REQUIRED))
    around = info_of({CLS: AROUND_SCALE, MARK: MARK_SCALE}, releasable=(CLS, MARK))
    return {
        "exact-three-contracts": (
            work(B25),
            [row(1, 1, "100", B25), row(2, 2, "100", B25), row(3, 3, "100", B25), row(4, 4, "100", B30)],
            single,
            CLASS_A,
        ),
        "exact-two-plus-next": (
            work(B25),
            [row(1, 1, "100", B25), row(2, 2, "100", B25), row(3, 3, "100", B30), row(4, 4, "100", B35)],
            single,
            CLASS_A,
        ),
        "both-sides": (
            work(V60),
            [row(1, 1, "100", V40), row(2, 2, "100", V40), row(3, 3, "100", V90), row(4, 4, "100", V120)],
            info_of({CLS: AROUND_SCALE}),
            CLASS_A,
        ),
        "last-round": (
            work(B25),
            [
                row(1, 1, "100", B30),
                row(2, 2, "100", B30),
                row(3, 3, "100", None),
                row(4, 4, "100", MERGED),
                row(5, 5, "100", B35, conflicted=frozenset({CLS})),
            ],
            single,
            CLASS_A,
        ),
        "no-numbers": (
            work(B25),
            [row(1, 1, "100", None), row(2, 2, "100", MERGED)],
            single,
            CLASS_A,
        ),
        "skipped-kp-value": (
            work(None),
            [row(1, 1, "100", B25), row(2, 2, "100", B60)],
            single,
            CLASS_A,
        ),
        "two-scales-class-first": (work(B35, W12M), _slab_lines(), two, CLASS_A),
        "two-scales-mark-first": (work(B35, W12M), _slab_lines(), two_rev, CLASS_A),
        "late-scale-removes-early": (
            work(V60, W12M),
            [
                row(1, 1, "100", V40, W8M),
                row(2, 2, "100", V90, W12M),
                row(3, 3, "100", V90, W12M),
                row(4, 4, "100", V90, W12M),
            ],
            around,
            CLASS_A,
        ),
        "late-scale-only-without-numbers": (
            work(V60, W12M),
            [
                row(1, 1, "100", V40, W8M),
                row(2, 2, "100", None, W12M),
                row(3, 3, "100", None, W12M),
                row(4, 4, "100", None, W12M),
            ],
            around,
            CLASS_A,
        ),
        "two-scales-last-round": (
            work(B35, W12M),
            [
                row(1, 1, "100", B40, W12M),
                row(2, 2, "100", None, W8M),
                row(3, 3, "100", B60, None),
            ],
            two,
            CLASS_A,
        ),
        "other-class-rung-four": (
            work(B35, W12M),
            _slab_lines(),
            two,
            CLASS_B,
        ),
        "one-contract-one-line": (work(B25), [row(1, 1, "100", B60)], single, CLASS_A),
        "with-vat-unknown-rows": (
            work(B35),
            [row(1, 1, "100", B40), row(2, 2, "100", B60, vat=None)],
            single,
            CLASS_A,
        ),
    }


SCENARIOS = _scenarios()


def _build(name: str, *, scales: bool):
    kp_work, lines, info, cls = SCENARIOS[name]
    chosen = info if scales else plain(info)
    return kp_work, lines, chosen, cls


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_rung_does_not_change(name: str) -> None:
    """Ступень выбирается до сужения: пара «область, ступень», причины и след «Не аналог» те же."""
    kp_work, lines, info, cls = _build(name, scales=True)
    _, _, bare, _ = _build(name, scales=False)

    with_scales = ladder(kp_work, lines, info, cls=cls)
    without = ladder(kp_work, lines, bare, cls=cls)

    assert with_scales.rung_no == without.rung_no
    assert with_scales.zone_scope == without.zone_scope
    assert with_scales.no_history_reason == without.no_history_reason
    assert with_scales.base_reason == without.base_reason
    assert with_scales.other_class == without.other_class
    assert excluded_trace(kp_work, history_of(lines, info, cls=cls), tender_class_id=CLASS_A) == excluded_trace(
        kp_work, history_of(lines, bare, cls=cls), tender_class_id=CLASS_A
    )


def test_the_rung_and_the_reasons_do_not_change_when_there_is_no_rung() -> None:
    kp_work = kp(values={CLS: B25, REQUIRED: 9})  # обязательный параметр другой: ступеней 2 и 4 нет
    lines = [row(1, 1, "100", B25)]
    info = info_of({CLS: CLASS_SCALE})

    with_scales = ladder(kp_work, lines, info)
    without = ladder(kp_work, lines, plain(info))

    assert with_scales.rung_no is None
    assert with_scales == without


def test_the_trace_of_the_excluded_does_not_change() -> None:
    lines = [row(1, 1, "100", B25), row(2, 2, "100", B25), row(3, 3, "100", B30)]
    info = info_of({CLS: CLASS_SCALE})
    marked = dataclasses.replace(work(B25), excluded=frozenset({1}))

    with_scales = excluded_trace(marked, history_of(lines, info), tender_class_id=CLASS_A)
    without = excluded_trace(marked, history_of(lines, plain(info)), tender_class_id=CLASS_A)

    assert with_scales
    assert with_scales == without


# Ревью: в тесте выше сужение набора с отмеченной строкой ничего не убирает (B25 в двух договорах и B30
# в третьем — круги берут всех), поэтому сужение внутри `_Search.picked_in` его не роняло. Здесь
# отмеченная строка — далёкое значение B60 при трёх договорах с B40 у КП B35: суженный след её бы потерял.
@pytest.mark.parametrize("mark", ["kp-excluded", "price-error"])
def test_the_trace_keeps_a_far_marked_candidate_that_narrowing_would_drop(mark: str) -> None:
    far = row(4, 4, "100", B60)
    lines = [row(1, 1, "100", B40), row(2, 2, "100", B40), row(3, 3, "100", B40)]
    own = work(B35)
    if mark == "kp-excluded":
        lines.append(far)
        own = dataclasses.replace(own, excluded=frozenset({4}))
    else:
        lines.append(dataclasses.replace(far, price_error=True))
    info = info_of({CLS: CLASS_SCALE})

    with_scales = excluded_trace(own, history_of(lines, info), tender_class_id=CLASS_A)
    without = excluded_trace(own, history_of(lines, plain(info)), tender_class_id=CLASS_A)

    assert [(cand.line.position_item_id, step) for cand, step in with_scales] == [(4, ("own", 2))]
    assert with_scales == without
    # Сам подбор строку 4 не берёт ни с шкалами, ни без: она отмечена.
    assert 4 not in picked_ids(ladder(own, lines, info))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_set_is_never_empty_when_a_rung_is_found(name: str) -> None:
    kp_work, lines, info, cls = _build(name, scales=True)

    result = ladder(kp_work, lines, info, cls=cls)

    if result.rung_no is not None:
        assert result.analogs


def test_the_set_is_never_empty_on_a_scenario_that_found_a_rung() -> None:
    """Страховка от пустого теста: на большинстве входов ступень найдена и набор непуст."""
    found = 0
    for name in SCENARIOS:
        kp_work, lines, info, cls = _build(name, scales=True)
        result = ladder(kp_work, lines, info, cls=cls)
        if result.rung_no is not None:
            assert result.analogs, name
            found += 1
    assert found >= 10


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_narrowed_set_is_a_subset_of_the_set_without_scales(name: str) -> None:
    kp_work, lines, info, cls = _build(name, scales=True)
    _, _, bare, _ = _build(name, scales=False)

    narrowed = ladder(kp_work, lines, info, cls=cls)
    wide = ladder(kp_work, lines, bare, cls=cls)

    assert set(picked_ids(narrowed)) <= set(picked_ids(wide))


def test_the_set_stays_the_same_where_the_scale_is_not_released() -> None:
    # Класс (шкала) обязателен: сужать нечего. Марка отпущена и не шкала.
    lines = [
        row(1, 1, "100", B25, W12M),
        row(2, 2, "110", B25, W8M),
        row(3, 3, "120", B25, W12M),
    ]
    info = info_of({CLS: CLASS_SCALE}, releasable=(MARK,), order=(CLS, MARK, REQUIRED))
    own = work(B25, W12M)

    result = ladder(own, lines, info)

    assert result.rung_no == 2
    assert result == ladder(own, lines, plain(info))
    assert result.nearest == ()
    assert result.released == (MARK_NAME,)

"""Перестройка значений параметра при смене вида, границ или шага (спека 3в §2.6).

Каждое прежнее активное значение разрешается: точное число — в единственный
новый диапазон, прежний диапазон — в единственный новый, целиком содержащий его,
иначе неразрешимо. Разрешённое переносится (строки контекстов на цель с прежним
источником, значение выводится), неразрешимое выводится, а его строки
сохраняют значение и помечаются «уточняется». Цель, совпавшая с источником,
оставляет значение и строки как есть. Ни одного `merged_into_id` у значений и
вариантов; выведенное значение возвращается из истории одной отметкой.

Эталоны — независимые литералы: подписи диапазонов, номера и числа контекстов
записаны руками, а не вычислены той же функцией, что строит план.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.schema_edit as schema_edit_module
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterValue,
    ParameterValueKind,
    WorkVariant,
)
from services.schema_edit import (
    RangeSpec,
    _read_state,
    apply_schema_edit,
    preview_schema_edit,
    resolve_values,
)
from services.semantic_reconcile import reconcile_or_defer
from services.work_families import WorkFamilyError
from services.work_variants import apply_values, merge_parameter_values
from tests.integration.parameters_edit_support import draft, edit
from tests.integration.test_catalog_parameters_edit import (
    _active_variants,
    _assert_summary_is_the_outcome,
    _context_row,
    _events,
    _live_values_jobs,
    _marked_contexts_of,
    _parameter,
    _set_row,
    _update_parameter,
    _values,
    _values_jobs,
    _variants_of,
    _waiting_ids,
)
from tests.integration.test_work_variants_api import BASE, _detail
from tests.integration.test_work_variants_core import (
    _answer,
    _lock_sequence,
    _named,
    _set_pending,
)
from tests.integration.test_work_variants_material import _capturing_sql, _settings
from tests.integration.test_work_variants_schema import _family
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _all_state,
    _paths_hash,
    _second_context,
    _settle_context,
    _variant_of,
    _variant_row,
)

pytestmark = pytest.mark.integration

#: Диаметры контекстов сцены, по одному контексту на число.
NUMBERS = ("1", "2", "4", "7", "9", "10", "12")
#: Куда каждое число попадает при диапазонах 2..10 с шагом 2.
LABEL_OF = {
    "1": "до 2", "2": "2–4", "4": "4–6", "7": "6–8", "9": "8–10", "10": "10 и более",
    "12": "10 и более",
}
STEP_TWO_LABELS = ["до 2", "2–4", "4–6", "6–8", "8–10", "10 и более"]


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _numbers_scene(db, factories, numbers=NUMBERS):
    """Семья с главным точным параметром «Диаметр» из `numbers` и по одному
    контексту с вариантом на каждое число: `(мир, {число: контекст},
    {число: вариант})`."""
    world = _active_world(db, factories, ((1, "Диаметр", list(numbers)),))
    contexts: dict[str, int] = {}
    variants: dict[str, int] = {}
    for index, number in enumerate(numbers):
        context_id = world.context_id if index == 0 else _second_context(db, factories, world)
        variants[number] = _settle_context(db, world, context_id, _answer(_named(1, number)))
        contexts[number] = context_id
    return world, contexts, variants


def _number(ordinal, text):
    """Ответ модели для параметра-диапазона: число из наименования, диапазон
    система находит сама."""
    return (ordinal, "new", text, "name")


def _ranged(*bounds, **kwargs):
    return draft(1, "Диаметр", kind="range", rng=bounds, **kwargs)


def _ranged_scene(db, factories):
    """Сцена после перевода «Диаметра» в диапазоны 2..10 шаг 2."""
    world, contexts, variants = _numbers_scene(db, factories)
    actor = _actor(factories)
    edit(db, world.family.id, [_ranged(2, 10, 2)], actor)
    return world, contexts, variants, actor


def _labels(db, schema_id, *, retired):
    """Подписи значений «Диаметра» по возрастанию `id`: выведенные или активные."""
    row = _parameter(db, schema_id, 1)
    return [
        label for label, value in _values(db, row.id).items()
        if (value.retired_at is not None) == retired and (
            retired or value.merged_into_id is None
        )
    ]


def _row_value(db, context_id, schema_id) -> FamilyParameterValue:
    row = _context_row(db, context_id, _parameter(db, schema_id, 1).id)
    return db.get(FamilyParameterValue, row.value_id)


def _assert_invariants(db, schema_id, *, human_merges=(0, 0)):
    """Перестройка не пишет слияний ни у значений, ни у вариантов, а строка на
    выведенном значении всегда несёт отметку «уточняется» (спека 3в §2.2).
    `human_merges` — `(значений, вариантов)`, слитых человеком в самом тесте:
    слияний после перестройки не больше, чем их сделал человек."""
    db.expire_all()
    merged_values = db.execute(
        sa.select(sa.func.count())
        .select_from(FamilyParameterValue)
        .join(FamilyParameter, FamilyParameter.id == FamilyParameterValue.parameter_id)
        .where(
            FamilyParameter.schema_id == schema_id,
            FamilyParameterValue.merged_into_id.is_not(None),
        )
    ).scalar_one()
    merged_variants = db.execute(
        sa.select(sa.func.count())
        .select_from(WorkVariant)
        .where(WorkVariant.schema_id == schema_id, WorkVariant.merged_into_id.is_not(None))
    ).scalar_one()
    unmarked_on_retired = db.execute(
        sa.select(ContextParameterValue.context_id)
        .join(
            FamilyParameterValue,
            sa.and_(
                FamilyParameterValue.id == ContextParameterValue.value_id,
                FamilyParameterValue.parameter_id == ContextParameterValue.parameter_id,
            ),
        )
        .where(
            ContextParameterValue.schema_id == schema_id,
            FamilyParameterValue.retired_at.is_not(None),
            ContextParameterValue.refresh_requested_at.is_(None),
        )
    ).all()
    assert merged_values <= human_merges[0] and merged_variants <= human_merges[1]
    assert unmarked_on_retired == []


def _resolution_of(resolutions, value_id):
    [found] = [item for item in resolutions if item.value_id == value_id]
    return found


# ---------------------------------------------------------------------------
#  resolve_values: судьба каждого значения
# ---------------------------------------------------------------------------

class TestResolveValues:
    def test_numbers_go_to_the_range_that_holds_them_and_the_rest_is_unresolvable(
        self, db_session, factories
    ):
        world = _active_world(
            db_session, factories,
            ((1, "Диаметр", ["4", "6,5 мм", "до 100 мм", "В25", "2", "10", "1"]),),
        )
        row = _parameter(db_session, world.schema.id, 1)
        ids = {label: value.id for label, value in _values(db_session, row.id).items()}

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(Decimal(2), Decimal(10), Decimal(2)),
        )

        assert [r.value_id for r in resolutions] == sorted(ids.values())
        # цели ещё нет: `target_value_id` пуст, цель названа подписью
        got = {
            label: (_resolution_of(resolutions, vid).target_label,
                    _resolution_of(resolutions, vid).target_value_id,
                    _resolution_of(resolutions, vid).unchanged)
            for label, vid in ids.items()
        }
        assert got == {
            "4": ("4–6", None, False),
            "6,5 мм": ("6–8", None, False),
            "до 100 мм": (None, None, False),
            "В25": (None, None, False),
            # нижняя граница входит, верхняя нет
            "2": ("2–4", None, False),
            "10": ("10 и более", None, False),
            "1": ("до 2", None, False),
        }

    def test_negative_numbers_go_to_the_range_that_holds_them(self, db_session, factories):
        world = _active_world(
            db_session, factories,
            ((1, "Диаметр", ["-5", "−3", "-10", "-11", "5", "-5-3"]),),
        )
        row = _parameter(db_session, world.schema.id, 1)
        ids = {label: value.id for label, value in _values(db_session, row.id).items()}

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(Decimal(-10), Decimal(10), Decimal(10)),
        )

        got = {
            label: _resolution_of(resolutions, vid).target_label for label, vid in ids.items()
        }
        assert got == {
            "-5": "-10–0",
            "−3": "-10–0",
            "-10": "-10–0",
            "-11": "до -10",
            "5": "0–10",
            "-5-3": None,
        }

    def test_the_way_back_to_exact_resolves_nothing(self, db_session, factories):
        world, _contexts, _variants, _actor_id = _ranged_scene(db_session, factories)
        row = _parameter(db_session, world.schema.id, 1)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.exact, new_range=None
        )

        assert [(r.target_value_id, r.target_label, r.unchanged) for r in resolutions] == [
            (None, None, False)
        ] * 6

    def test_exact_numbers_asked_for_exact_values_resolve_to_nothing(
        self, db_session, factories
    ):
        """Новый вид «точный»: числа не ищут диапазон (набор пуст), всё неразрешимо
        без запроса списка из тела (ревью задачи 5, реревью 1)."""
        world = _active_world(db_session, factories, ((1, "Диаметр", ["4", "В25"]),))
        row = _parameter(db_session, world.schema.id, 1)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.exact, new_range=None
        )

        assert [(r.target_value_id, r.target_label, r.unchanged) for r in resolutions] == [
            (None, None, False)
        ] * 2

    def test_the_same_bounds_leave_the_extreme_values_unchanged_and_split_the_inner_ones(
        self, db_session, factories
    ):
        world, _contexts, _variants, _actor_id = _ranged_scene(db_session, factories)
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(Decimal(2), Decimal(10), Decimal(1)),
        )

        got = {
            label: (_resolution_of(resolutions, values[label].id).unchanged,
                    _resolution_of(resolutions, values[label].id).target_value_id)
            for label in STEP_TWO_LABELS
        }
        assert got == {
            "до 2": (True, values["до 2"].id),
            "2–4": (False, None),
            "4–6": (False, None),
            "6–8": (False, None),
            "8–10": (False, None),
            "10 и более": (True, values["10 и более"].id),
        }
        assert all(
            _resolution_of(resolutions, values[label].id).target_label is None
            for label in ("2–4", "4–6", "6–8", "8–10")
        )

    def test_a_range_inside_a_wider_one_goes_into_it_with_infinite_edges_counted(
        self, db_session, factories
    ):
        world, _contexts, _variants, _actor_id = _ranged_scene(db_session, factories)
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(Decimal(4), Decimal(8), Decimal(4)),
        )

        # новые значения: «до 4», «4–8», «8 и более»
        got = {
            label: _resolution_of(resolutions, values[label].id).target_label
            for label in STEP_TWO_LABELS
        }
        assert got == {
            "до 2": "до 4",
            "2–4": "до 4",
            "4–6": "4–8",
            "6–8": "4–8",
            "8–10": "8 и более",
            "10 и более": "8 и более",
        }

    def test_a_value_already_named_like_a_range_is_the_target_of_the_numbers_it_holds(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, ((1, "Диаметр", ["3", "2–6"]),))
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(Decimal(2), Decimal(10), Decimal(4)),
        )

        # «2–6» уже есть и сам остаётся (цель совпала с источником); число 3 идёт в него
        assert (
            _resolution_of(resolutions, values["2–6"].id).unchanged,
            _resolution_of(resolutions, values["2–6"].id).target_value_id,
        ) == (True, values["2–6"].id)
        three = _resolution_of(resolutions, values["3"].id)
        assert (three.target_value_id, three.target_label, three.unchanged) == (
            values["2–6"].id, "2–6", False,
        )


# ---------------------------------------------------------------------------
#  Точные числа в диапазоны
# ---------------------------------------------------------------------------

class TestExactToRanges:
    def test_every_number_is_carried_into_its_range_and_nothing_is_asked(
        self, db_session, factories
    ):
        world, contexts, variants = _numbers_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        old = {label: value.id for label, value in _values(db_session, row.id).items()}

        summary = edit(
            db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories)
        )

        row = _parameter(db_session, schema_id, 1)
        assert (row.value_kind, row.closed) == ("range", True)
        assert (row.range_from, row.range_to, row.range_step) == (
            Decimal(2), Decimal(10), Decimal(2),
        )
        values = _values(db_session, row.id)
        assert _labels(db_session, schema_id, retired=False) == STEP_TWO_LABELS
        assert _labels(db_session, schema_id, retired=True) == list(NUMBERS)
        assert {label: values[label].id for label in NUMBERS} == old
        for number in NUMBERS:
            context_row = _context_row(db_session, contexts[number], row.id)
            assert context_row.value_id == values[LABEL_OF[number]].id
            assert context_row.source == "name"
            assert context_row.refresh_requested_at is None
        # варианты: шесть диапазонов вместо семи чисел, число 10 и 12 вместе
        assert (summary.variants_before, summary.variants_after_now) == (7, 6)
        assert _active_variants(db_session, world.family.id) == 6
        assert _variant_of(db_session, contexts["10"]) == _variant_of(db_session, contexts["12"])
        for number in NUMBERS:
            assert _variant_row(db_session, variants[number]).merged_into_id is None
        assert _variant_row(db_session, variants["1"]).status == "archived"
        assert summary.value_moves == tuple(
            ("Диаметр", number, LABEL_OF[number], 1) for number in NUMBERS
        )
        assert summary.retired == ()
        assert summary.asked_by_parameter == ()
        assert summary.refreshing_contexts == 0
        assert summary.variants_pending_answers is False
        assert _live_values_jobs(db_session) == []
        assert _marked_contexts_of(db_session, world.family.id) == set()
        _assert_invariants(db_session, schema_id)

    def test_the_event_names_the_kind_the_bounds_and_the_moves(self, db_session, factories):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4", "В25"))

        edit(db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories))

        [event] = _events(db_session, "family_schema_edited", world.family.id)
        [change] = event.payload["changes"]
        assert change["value_kind"] == {"from": "exact", "to": "range"}
        assert change["range"] == {
            "from": None, "to": {"start": "2", "stop": "10", "step": "2"},
        }
        assert change["values_moved"] == [{"from": "4", "to": "4–6", "contexts": 1}]
        assert change["values_retired"] == [{"value": "В25", "contexts": 1}]

    def test_a_value_that_is_not_a_number_is_retired_and_its_row_says_refreshing(
        self, db_session, factories
    ):
        world, contexts, variants = _numbers_scene(
            db_session, factories, ("4", "6,5 мм", "В25")
        )
        schema_id = world.schema.id
        before = _variants_of(db_session, contexts.values())
        old_row_value = _context_row(
            db_session, contexts["В25"], _parameter(db_session, schema_id, 1).id
        ).value_id
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(
            db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories)
        )

        values = _values(db_session, _parameter(db_session, schema_id, 1).id)
        assert values["В25"].retired_at is not None
        assert _labels(db_session, schema_id, retired=True) == ["4", "6,5 мм", "В25"]
        stuck = _context_row(db_session, contexts["В25"], values["В25"].parameter_id)
        assert stuck.value_id == old_row_value == values["В25"].id
        assert stuck.refresh_requested_at is not None
        # «6,5 мм» разобрано с единицей, прочие переехали
        assert _row_value(db_session, contexts["4"], schema_id).value == "4–6"
        assert _row_value(db_session, contexts["6,5 мм"], schema_id).value == "6–8"
        # контекст без разрешения остался в прежнем варианте
        after = _variants_of(db_session, contexts.values())
        assert after[contexts["В25"]] == before[contexts["В25"]] == variants["В25"]
        assert (summary.variants_before, summary.variants_after_now) == (3, 3)
        assert summary.retired == (("Диаметр", "В25", 1),)
        assert summary.value_moves == (
            ("Диаметр", "4", "4–6", 1), ("Диаметр", "6,5 мм", "6–8", 1),
        )
        assert summary.variants_pending_answers is True
        assert summary.refreshing_contexts == 1
        assert summary.asked_by_parameter == ((1, "Диаметр", 1),)
        [job] = _live_values_jobs(db_session)
        # параметр один: запрошены все активные, подмножество не хранится
        assert (job.context_id, job.requested_ordinals) == (contexts["В25"], None)
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)

    def test_a_value_already_named_like_a_range_keeps_its_rows_and_gets_the_bounds(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("3", "2–6"))
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        named = _values(db_session, row.id)["2–6"]
        assert (named.range_lo, named.range_hi) == (None, None)

        summary = edit(
            db_session, world.family.id, [_ranged(2, 10, 4)], _actor(factories)
        )

        values = _values(db_session, row.id)
        assert values["2–6"].id == named.id and values["2–6"].retired_at is None
        assert (values["2–6"].range_lo, values["2–6"].range_hi) == (Decimal(2), Decimal(6))
        # число 3 пришло в то же значение, а строка контекста «2–6» не тронута
        assert _row_value(db_session, contexts["3"], schema_id).id == named.id
        assert _row_value(db_session, contexts["2–6"], schema_id).id == named.id
        assert summary.value_moves == (("Диаметр", "3", "2–6", 1),)
        assert summary.retired == ()
        assert _variant_of(db_session, contexts["3"]) == _variant_of(db_session, contexts["2–6"])
        _assert_invariants(db_session, schema_id)

    def test_a_secondary_parameter_keeps_its_variants(self, db_session, factories):
        world = _active_world(
            db_session, factories,
            ((1, "Материал", ["бетон", "кирпич"]), (2, "Диаметр", ["4", "6"])),
        )
        schema_id = world.schema.id
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.schema_id == schema_id, FamilyParameter.ordinal == 2)
            .values(role="secondary")
        )
        db_session.flush()
        second = _second_context(db_session, factories, world)
        first_variant = _settle_context(
            db_session, world, world.context_id, _answer(_named(1, "бетон", "path"), _named(2, "4"))
        )
        second_variant = _settle_context(
            db_session, world, second, _answer(_named(1, "кирпич", "path"), _named(2, "6"))
        )

        summary = edit(
            db_session, world.family.id,
            [
                draft(1, "Материал", "бетон", "кирпич"),
                draft(2, "Диаметр", role="secondary", kind="range", rng=(2, 10, 2)),
            ],
            _actor(factories),
        )

        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: first_variant, second: second_variant,
        }
        assert (summary.variants_before, summary.variants_after_now) == (2, 2)
        # проекция пересчёта не ждёт ответа по второстепенному (ревью задачи 5)
        assert summary.variants_pending_answers is False
        assert summary.value_moves == (
            ("Диаметр", "4", "4–6", 1), ("Диаметр", "6", "6–8", 1),
        )
        diameter = _parameter(db_session, schema_id, 2)
        assert _context_row(db_session, world.context_id, diameter.id).value_id == _values(
            db_session, diameter.id
        )["4–6"].id
        _assert_invariants(db_session, schema_id)

    def test_a_context_awaiting_another_family_is_carried_and_marked_too(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(
            db_session, factories, ("4", "6", "В25")
        )
        other = _family(db_session)
        for number in ("6", "В25"):
            _set_pending(db_session, factories, contexts[number], other)
        state = _read_state(db_session, world.family.id, world.schema.id, 0)
        # предусловие: контексты ожидают другую семью, а их строки остались в версии
        assert set(contexts.values()) - set(state.context_ids) == {
            contexts["6"], contexts["В25"],
        }
        assert [r.in_family for r in state.schema_rows if r.context_id == contexts["6"]] == [False]

        summary = edit(
            db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories)
        )

        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        # строка ожидающего контекста не может остаться на выведенном значении без отметки
        assert _row_value(db_session, contexts["6"], schema_id).value == "6–8"
        assert _row_value(db_session, contexts["4"], schema_id).value == "4–6"
        stuck = _context_row(db_session, contexts["В25"], row.id)
        assert db_session.get(FamilyParameterValue, stuck.value_id).value == "В25"
        assert stuck.refresh_requested_at is not None
        _assert_invariants(db_session, schema_id)
        assert summary.value_moves == (
            ("Диаметр", "4", "4–6", 1), ("Диаметр", "6", "6–8", 1),
        )
        assert summary.retired == (("Диаметр", "В25", 1),)


# ---------------------------------------------------------------------------
#  Диапазон в диапазон
# ---------------------------------------------------------------------------

class TestRangeToRange:
    def test_the_same_bounds_with_a_finer_step_leave_the_extremes_and_split_the_rest(
        self, db_session, factories
    ):
        world, contexts, variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        before = {label: value.id for label, value in _values(db_session, row.id).items()}
        variants_before = _variants_of(db_session, contexts.values())
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        previewed = preview_schema_edit(
            db_session, family_id=world.family.id, drafts=[_ranged(2, 10, 1)]
        )
        summary = apply_schema_edit(
            db_session, family_id=world.family.id, drafts=[_ranged(2, 10, 1)],
            preview_hash=previewed.preview_hash, actor_id=actor,
        )

        assert summary == previewed
        values = _values(db_session, row.id)
        # крайние сохранили идентификаторы, не выведены, строки без отметки
        for label, numbers in (("до 2", ["1"]), ("10 и более", ["10", "12"])):
            assert values[label].id == before[label]
            assert values[label].retired_at is None
            for number in numbers:
                context_row = _context_row(db_session, contexts[number], row.id)
                assert context_row.value_id == before[label]
                assert context_row.refresh_requested_at is None
        # внутренние делятся: выведены, строки на них «уточняются»
        for label, number in (("2–4", "2"), ("4–6", "4"), ("6–8", "7"), ("8–10", "9")):
            assert values[label].retired_at is not None
            context_row = _context_row(db_session, contexts[number], row.id)
            assert context_row.value_id == before[label]
            assert context_row.refresh_requested_at is not None
        assert _labels(db_session, schema_id, retired=False) == [
            "до 2", "10 и более", "2–3", "3–4", "4–5", "5–6", "6–7", "7–8", "8–9", "9–10",
        ]
        # варианты прежние, запрос только у делящихся
        assert _variants_of(db_session, contexts.values()) == variants_before
        assert (summary.variants_before, summary.variants_after_now) == (6, 6)
        assert summary.value_moves == ()
        assert summary.retired == (
            ("Диаметр", "2–4", 1), ("Диаметр", "4–6", 1), ("Диаметр", "6–8", 1),
            ("Диаметр", "8–10", 1),
        )
        assert summary.refreshing_contexts == 4
        assert summary.asked_by_parameter == ((1, "Диаметр", 4),)
        jobs = _live_values_jobs(db_session)
        assert sorted((job.context_id, job.requested_ordinals) for job in jobs) == sorted(
            (contexts[number], None) for number in ("2", "4", "7", "9")
        )
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)

        # после ответа строка получает новое значение, а контекст — новый вариант
        _settle_context(db_session, world, contexts["2"], _answer(_number(1, "2")))
        refreshed = _context_row(db_session, contexts["2"], row.id)
        assert refreshed.value_id == _values(db_session, row.id)["2–3"].id
        assert refreshed.refresh_requested_at is None
        assert _variant_of(db_session, contexts["2"]) not in variants.values()
        assert _variant_row(db_session, variants["2"]).status == "archived"
        _assert_invariants(db_session, schema_id)

    def test_nested_ranges_are_carried_into_the_wider_ones(self, db_session, factories):
        world, contexts, variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        before = {label: value.id for label, value in _values(db_session, row.id).items()}

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 4)], actor)

        values = _values(db_session, row.id)
        assert summary.value_moves == (
            ("Диаметр", "2–4", "2–6", 1), ("Диаметр", "4–6", "2–6", 1),
            ("Диаметр", "6–8", "6–10", 1), ("Диаметр", "8–10", "6–10", 1),
        )
        assert summary.retired == ()
        assert summary.asked_by_parameter == ()
        assert _live_values_jobs(db_session) == []
        for number, label in (("2", "2–6"), ("4", "2–6"), ("7", "6–10"), ("9", "6–10")):
            assert _row_value(db_session, contexts[number], schema_id).value == label
        # пары в одном варианте: «до 2», «2–6», «6–10», «10 и более»
        assert (summary.variants_before, summary.variants_after_now) == (6, 4)
        assert _variant_of(db_session, contexts["2"]) == _variant_of(db_session, contexts["4"])
        assert _variant_of(db_session, contexts["7"]) == _variant_of(db_session, contexts["9"])
        assert values["до 2"].id == before["до 2"] and values["до 2"].retired_at is None
        assert values["10 и более"].id == before["10 и более"]
        _assert_invariants(db_session, schema_id)

    def test_the_way_back_returns_values_and_variants_from_the_history(
        self, db_session, factories
    ):
        world, contexts, _variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        original = {label: value.id for label, value in _values(db_session, row.id).items()}
        step_two_variant = {
            number: _variant_of(db_session, contexts[number]) for number in contexts
        }
        edit(db_session, world.family.id, [_ranged(2, 10, 4)], actor)
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)

        values = _values(db_session, row.id)
        # «2–4», «4–6», «6–8», «8–10» вернулись с прежними идентификаторами
        for label in ("2–4", "4–6", "6–8", "8–10"):
            assert values[label].id == original[label]
            assert values[label].retired_at is None
        # возврат на строки не переносится: они стоят на «2–6» и «6–10», которые
        # теперь делятся и выведены
        for number in ("2", "4", "7", "9"):
            context_row = _context_row(db_session, contexts[number], row.id)
            assert context_row.refresh_requested_at is not None
            assert db_session.get(FamilyParameterValue, context_row.value_id).retired_at is not None
        assert summary.retired == (("Диаметр", "2–6", 2), ("Диаметр", "6–10", 2))
        assert summary.value_moves == ()
        assert summary.refreshing_contexts == 4
        assert summary.asked_by_parameter == ((1, "Диаметр", 4),)
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)

        # после ответов контексты возвращаются в прежние варианты, с теми же `id`
        for number in ("2", "4", "7", "9"):
            _settle_context(db_session, world, contexts[number], _answer(_number(1, number)))
        assert {
            number: _variant_of(db_session, contexts[number]) for number in ("2", "4", "7", "9")
        } == {number: step_two_variant[number] for number in ("2", "4", "7", "9")}
        assert len({step_two_variant[number] for number in ("2", "4", "7", "9")}) == 4
        assert all(
            _variant_row(db_session, step_two_variant[number]).status == "active"
            for number in ("2", "4", "7", "9")
        )
        _assert_invariants(db_session, schema_id)


# ---------------------------------------------------------------------------
#  Возврат в точные
# ---------------------------------------------------------------------------

class TestRangesToExact:
    def test_a_value_named_again_returns_from_the_history_and_is_not_canonicalised(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "6"))
        schema_id = world.schema.id
        actor = _actor(factories)
        row = _parameter(db_session, schema_id, 1)
        four = _values(db_session, row.id)["4"].id
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)
        assert _values(db_session, row.id)["4"].retired_at is not None

        summary = edit(db_session, world.family.id, [draft(1, "Диаметр", "4")], actor)

        row = _parameter(db_session, schema_id, 1)
        values = _values(db_session, row.id)
        assert (row.value_kind, row.closed) == ("exact", False)
        assert (row.range_from, row.range_to, row.range_step) == (None, None, None)
        assert values["4"].id == four and values["4"].retired_at is None
        assert values["4"].merged_into_id is None
        assert _labels(db_session, schema_id, retired=False) == ["4"]
        # возврат не переносит строки: контекст «4» остался на «4–6», выведенном и
        # помеченном, а не на возвращённом «4»
        stuck = _context_row(db_session, contexts["4"], row.id)
        assert stuck.value_id == values["4–6"].id
        assert stuck.refresh_requested_at is not None
        assert summary.retired == (
            ("Диаметр", "до 2", 0), ("Диаметр", "2–4", 0), ("Диаметр", "4–6", 1),
            ("Диаметр", "6–8", 1), ("Диаметр", "8–10", 0), ("Диаметр", "10 и более", 0),
        )
        assert summary.value_moves == ()
        assert summary.refreshing_contexts == 2
        assert summary.asked_by_parameter == ((1, "Диаметр", 2),)
        _assert_invariants(db_session, schema_id)

    def test_an_empty_exact_list_is_allowed_and_every_context_says_refreshing(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "6"))
        schema_id = world.schema.id
        actor = _actor(factories)
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)
        before = _variants_of(db_session, contexts.values())

        summary = edit(db_session, world.family.id, [draft(1, "Диаметр")], actor)

        assert _labels(db_session, schema_id, retired=False) == []
        assert _marked_contexts_of(db_session, world.family.id) == set(contexts.values())
        assert _variants_of(db_session, contexts.values()) == before
        assert summary.refreshing_contexts == 2
        assert summary.asked_by_parameter == ((1, "Диаметр", 2),)
        assert len(summary.retired) == 6
        _assert_invariants(db_session, schema_id)

    def test_the_closed_flag_follows_the_body_on_the_way_back(self, db_session, factories):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4",))
        actor = _actor(factories)
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)
        assert _parameter(db_session, world.schema.id, 1).closed is True

        edit(db_session, world.family.id, [draft(1, "Диаметр", "4", closed=True)], actor)

        assert _parameter(db_session, world.schema.id, 1).closed is True
        edit(db_session, world.family.id, [draft(1, "Диаметр", "4", closed=False)], actor)
        assert _parameter(db_session, world.schema.id, 1).closed is False


# ---------------------------------------------------------------------------
#  Предпросмотр, хэш, замки
# ---------------------------------------------------------------------------

class TestPreviewAndLocks:
    def test_a_preview_names_the_same_numbers_the_save_makes(self, db_session, factories):
        world, contexts, _variants = _numbers_scene(
            db_session, factories, ("4", "6,5 мм", "В25")
        )
        drafts = [_ranged(2, 10, 2)]
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        previewed = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        applied = apply_schema_edit(
            db_session, family_id=world.family.id, drafts=drafts,
            preview_hash=previewed.preview_hash, actor_id=_actor(factories),
        )

        assert applied == previewed
        assert (previewed.value_moves, previewed.retired) == (
            (("Диаметр", "4", "4–6", 1), ("Диаметр", "6,5 мм", "6–8", 1)),
            (("Диаметр", "В25", 1),),
        )
        # число перенесённых и выведенных — по самой базе
        moved_rows = sum(
            1 for number in ("4", "6,5 мм")
            if _row_value(db_session, contexts[number], world.schema.id).value
            in ("4–6", "6–8")
        )
        assert moved_rows == 2
        _assert_summary_is_the_outcome(
            db_session, world.family.id, world.schema.id, previewed, waiting_before, marked_before
        )

    def test_a_waiting_job_is_recomputed_because_the_render_of_the_parameter_changed(
        self, db_session, factories
    ):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4", "6"))
        third = _second_context(db_session, factories, world)
        reconcile_or_defer(db_session, [third])
        [stale] = _values_jobs(db_session, context_id=third)
        assert (stale.status, stale.requested_ordinals) == ("pending", None)

        summary = edit(
            db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories)
        )

        jobs = {job.id: job for job in _values_jobs(db_session, context_id=third)}
        assert (jobs[stale.id].status, jobs[stale.id].cancel_reason) == (
            "cancelled", "input_changed",
        )
        [fresh] = [job for job in jobs.values() if job.status == "pending"]
        assert fresh.request_hash != stale.request_hash
        assert summary.repriced_pending_jobs == 1

    def test_a_preview_writes_nothing(self, db_session, factories):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4", "В25"))
        before = _all_state(db_session)

        preview_schema_edit(
            db_session, family_id=world.family.id, drafts=[_ranged(2, 10, 2)]
        )

        assert _all_state(db_session) == before

    def test_the_hash_depends_on_the_new_bounds(self, db_session, factories):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4", "6"))

        first = preview_schema_edit(
            db_session, family_id=world.family.id, drafts=[_ranged(2, 10, 2)]
        )
        second = preview_schema_edit(
            db_session, family_id=world.family.id, drafts=[_ranged(2, 10, 4)]
        )

        assert first.preview_hash != second.preview_hash

    def test_a_row_moved_between_the_calls_is_a_stale_preview_and_nothing_is_written(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "6"))
        drafts = [_ranged(2, 10, 2)]
        previewed = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        row = _parameter(db_session, world.schema.id, 1)
        six = _values(db_session, row.id)["6"].id
        _set_row(db_session, contexts["4"], row.id, value_id=six)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=previewed.preview_hash, actor_id=_actor(factories),
            )

        assert raised.value.code == "preview_changed"
        assert _all_state(db_session) == before

    def test_the_locks_follow_the_module_order(self, db_session, factories):
        world, _contexts, _variants, actor = _ranged_scene(db_session, factories)
        drafts = [_ranged(2, 10, 4)]
        summary = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)

        with _capturing_sql(db_session) as statements:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=summary.preview_hash, actor_id=actor,
            )

        first_seen: list[tuple[str, str]] = []
        for lock in _lock_sequence(statements):
            if lock not in first_seen:
                first_seen.append(lock)
        order = [
            "work_families", "family_parameter_schemas", "family_parameters",
            "family_parameter_values", "catalog_contexts", "work_variants",
        ]
        tables = [table for table, _mode in first_seen]
        assert [mode for _table, mode in first_seen] == ["UPDATE"] * len(first_seen)
        assert tables == [table for table in order if table in tables]
        assert tables[:4] == order[:4]
        assert "catalog_contexts" in tables and "work_variants" in tables

    def test_a_rebuild_without_new_values_locks_values_and_contexts_before_the_rows(
        self, db_session, factories
    ):
        """Возврат в точные с пустым списком не создаёт и не переименовывает ни одного
        значения, но выводит их и помечает строки: значения параметра и контексты
        строк берутся `FOR UPDATE` до первой записи в строки контекстов."""
        world, _contexts, _variants, actor = _ranged_scene(db_session, factories)
        drafts = [draft(1, "Диаметр")]
        summary = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)

        with _capturing_sql(db_session) as statements:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=summary.preview_hash, actor_id=actor,
            )

        first_row_write = next(
            index for index, text in enumerate(statements)
            if text.lstrip().upper().startswith("UPDATE CONTEXT_PARAMETER_VALUES")
        )
        locked_before = {
            table
            for text in statements[:first_row_write]
            for table, _mode in _lock_sequence([text])
        }
        assert {"family_parameter_values", "catalog_contexts"} <= locked_before


# ---------------------------------------------------------------------------
#  Ревью задачи 5: входы, которых не было у исполнителя
# ---------------------------------------------------------------------------

def _mark_row(db, context_id, parameter_id):
    _set_row(db, context_id, parameter_id, refresh_requested_at=sa.func.now())


def _archive_context(db, context_id):
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(archived_at=sa.func.now())
    )
    db.flush()
    db.expire_all()


class TestInfiniteEdgesThatSplit:
    @pytest.mark.parametrize(
        ("new_range", "label"),
        [
            # «до 2» делится на «до 1» и «1–2»: бесконечный низ не вмещается в «1–2»
            pytest.param((1, 10, 1), "до 2", id="lower_edge"),
            # «10 и более» делится на «10–11» и «11 и более»: бесконечный верх не
            # вмещается в «10–11»
            pytest.param((2, 11, 1), "10 и более", id="upper_edge"),
        ],
    )
    def test_an_extreme_value_that_splits_is_unresolvable(
        self, db_session, factories, new_range, label
    ):
        world, _contexts, _variants, _actor_id = _ranged_scene(db_session, factories)
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)

        resolutions = resolve_values(
            db_session, parameter_id=row.id, new_kind=ParameterValueKind.range,
            new_range=RangeSpec(*(Decimal(n) for n in new_range)),
        )

        found = _resolution_of(resolutions, values[label].id)
        assert (found.target_value_id, found.target_label, found.unchanged) == (
            None, None, False,
        )


class TestAClosedWayBackWithNothingElseChanged:
    def test_an_empty_closed_exact_list_is_still_a_rebuild(self, db_session, factories):
        """Возврат в точные с пустым списком и «только из списка» как у диапазона: ни
        нового значения, ни переименования, ни смены `closed` — правка всё равно
        перестройка (вид меняется, значения выводятся, строки помечаются)."""
        world, contexts, _variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id

        summary = edit(db_session, world.family.id, [draft(1, "Диаметр", closed=True)], actor)

        row = _parameter(db_session, schema_id, 1)
        assert (row.value_kind, row.closed, row.range_step) == ("exact", True, None)
        assert _labels(db_session, schema_id, retired=False) == []
        assert _marked_contexts_of(db_session, world.family.id) == set(contexts.values())
        assert len(summary.retired) == 6
        _assert_invariants(db_session, schema_id)


class TestRowsTheRebuildLeavesAlone:
    def test_none_and_off_list_rows_are_not_marked_and_not_counted(
        self, db_session, factories
    ):
        """Решение 18: перестройка не трогает строки `none` и `off_list` — ни
        отметки, ни значения, ни запроса; сводка их не считает. Ветка
        «добавлено значение точного» (`_plan_rows`) пометила бы их «уточняется»."""
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "В25"))
        schema_id = world.schema.id
        parameter = _parameter(db_session, schema_id, 1)
        _update_parameter(db_session, parameter.id, closed=True)
        silent = _second_context(db_session, factories, world)
        _settle_context(db_session, world, silent, _answer((1, "none", None, None)))
        foreign = _second_context(db_session, factories, world)
        _settle_context(db_session, world, foreign, _answer((1, "new", "Пять", "name")))
        assert (
            _context_row(db_session, silent, parameter.id).source,
            _context_row(db_session, foreign, parameter.id).source,
        ) == ("none", "off_list")
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories))

        none_row = _context_row(db_session, silent, parameter.id)
        assert (none_row.source, none_row.value_id, none_row.refresh_requested_at) == (
            "none", None, None,
        )
        off_row = _context_row(db_session, foreign, parameter.id)
        assert (
            off_row.source, off_row.value_id, off_row.off_list_text,
            off_row.refresh_requested_at,
        ) == ("off_list", None, "Пять", None)
        # «уточняется» и запрос — только у неразрешимого «В25»
        assert summary.refreshing_contexts == 1
        assert summary.asked_by_parameter == ((1, "Диаметр", 1),)
        assert [job.context_id for job in _live_values_jobs(db_session)] == [contexts["В25"]]
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)

    def test_archived_contexts_are_carried_and_marked_but_not_counted(
        self, db_session, factories
    ):
        """Инвариант §2.2 — о любой строке версии, поэтому строки архивных
        контекстов переносятся и помечаются; числа сводки — только живые контексты."""
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "6", "В25"))
        schema_id = world.schema.id
        for number in ("4", "В25"):
            _archive_context(db_session, contexts[number])

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories))

        row = _parameter(db_session, schema_id, 1)
        assert _row_value(db_session, contexts["4"], schema_id).value == "4–6"
        stuck = _context_row(db_session, contexts["В25"], row.id)
        assert (
            db_session.get(FamilyParameterValue, stuck.value_id).value,
            stuck.refresh_requested_at is not None,
        ) == ("В25", True)
        _assert_invariants(db_session, schema_id)
        assert summary.value_moves == (
            ("Диаметр", "4", "4–6", 0), ("Диаметр", "6", "6–8", 1),
        )
        assert summary.retired == (("Диаметр", "В25", 0),)
        assert summary.refreshing_contexts == 0
        assert summary.asked_by_parameter == ()
        assert _live_values_jobs(db_session) == []


class TestRowsAlreadyRefreshing:
    def test_a_marked_row_on_a_split_value_is_not_counted_again(self, db_session, factories):
        world, contexts, _variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        _mark_row(db_session, contexts["4"], row.id)
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)
        assert marked_before == {contexts["4"]}

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 1)], actor)

        # делятся четыре значения, а новых отметок — три
        assert summary.retired == (
            ("Диаметр", "2–4", 1), ("Диаметр", "4–6", 1), ("Диаметр", "6–8", 1),
            ("Диаметр", "8–10", 1),
        )
        assert summary.refreshing_contexts == 3
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)

    def test_a_marked_row_carried_into_a_wider_range_keeps_waiting(
        self, db_session, factories
    ):
        """Строка с отметкой переносится вместе с отметкой: контекст ждёт ответа в
        прежнем варианте, и проекция сводки не отправляет его в новый."""
        world, contexts, _variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        variants = _variants_of(db_session, contexts.values())
        _mark_row(db_session, contexts["2"], row.id)
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 4)], actor)

        carried = _context_row(db_session, contexts["2"], row.id)
        assert carried.value_id == _values(db_session, row.id)["2–6"].id
        assert carried.refresh_requested_at is not None
        assert _variant_of(db_session, contexts["2"]) == variants[contexts["2"]]
        assert _variant_of(db_session, contexts["4"]) != variants[contexts["4"]]
        # «до 2», «2–4» (ждёт), «2–6», «6–10», «10 и более»
        assert (summary.variants_before, summary.variants_after_now) == (6, 5)
        assert summary.variants_pending_answers is True
        assert summary.refreshing_contexts == 0
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )
        _assert_invariants(db_session, schema_id)


class TestPartialRequestAfterASplit:
    def test_a_split_asks_only_the_rebuilt_parameter_and_the_answer_moves_the_context(
        self, db_session, factories
    ):
        """«Запрос одного параметра» различим только при двух параметрах: при одном
        `requested_ordinals` пуст и означает «все»."""
        world = _active_world(
            db_session, factories,
            ((1, "Материал", ["бетон"]), (2, "Диаметр", ["4", "7", "1"])),
        )
        schema_id = world.schema.id
        contexts: dict[str, int] = {}
        for index, number in enumerate(("4", "7", "1")):
            context_id = (
                world.context_id if index == 0 else _second_context(db_session, factories, world)
            )
            _settle_context(
                db_session, world, context_id,
                _answer(_named(1, "бетон", "path"), _named(2, number)),
            )
            contexts[number] = context_id
        actor = _actor(factories)

        def body(step):
            return [
                draft(1, "Материал", "бетон"),
                draft(2, "Диаметр", kind="range", rng=(2, 10, step)),
            ]

        edit(db_session, world.family.id, body(2), actor)
        before = _variants_of(db_session, contexts.values())

        summary = edit(db_session, world.family.id, body(1), actor)

        assert sorted(
            (job.context_id, job.requested_ordinals) for job in _live_values_jobs(db_session)
        ) == sorted([(contexts["4"], [2]), (contexts["7"], [2])])
        assert summary.asked_by_parameter == ((2, "Диаметр", 2),)
        assert _variants_of(db_session, contexts.values()) == before
        _assert_invariants(db_session, schema_id)

        material = _parameter(db_session, schema_id, 1).id
        diameter = _parameter(db_session, schema_id, 2).id
        material_before = _context_row(db_session, contexts["4"], material)
        material_before = (material_before.value_id, material_before.source)
        outcome = apply_values(
            db_session, context_id=contexts["4"], schema_id=schema_id,
            answer=_answer(_number(2, "4")), paths_hash=_paths_hash(db_session, contexts["4"]),
            guard=None, settings=_settings(), requested_ordinals=[2],
        )

        assert outcome.applied
        refreshed = _context_row(db_session, contexts["4"], diameter)
        assert (refreshed.value_id, refreshed.refresh_requested_at) == (
            _values(db_session, diameter)["4–5"].id, None,
        )
        after_material = _context_row(db_session, contexts["4"], material)
        assert (after_material.value_id, after_material.source) == material_before
        assert _variant_of(db_session, contexts["4"]) not in set(before.values())
        _assert_invariants(db_session, schema_id)


class TestValuesOfTheNewSet:
    def test_a_value_named_twice_on_the_way_back_is_one_value(self, db_session, factories):
        world, _contexts, _variants, actor = _ranged_scene(db_session, factories)

        edit(db_session, world.family.id, [draft(1, "Диаметр", "5", " 5 ")], actor)

        assert _labels(db_session, world.schema.id, retired=False) == ["5"]
        _assert_invariants(db_session, world.schema.id)

    def test_a_value_spelt_like_a_range_label_takes_the_label_and_stays(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "До 2"))
        schema_id = world.schema.id
        row = _parameter(db_session, schema_id, 1)
        named = _values(db_session, row.id)["До 2"]

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 2)], _actor(factories))

        kept = {value.id: value for value in _values(db_session, row.id).values()}[named.id]
        assert (kept.value, kept.retired_at, kept.range_lo, kept.range_hi) == (
            "до 2", None, None, Decimal(2),
        )
        context_row = _context_row(db_session, contexts["До 2"], row.id)
        assert (context_row.value_id, context_row.refresh_requested_at) == (named.id, None)
        assert summary.retired == ()
        _assert_invariants(db_session, schema_id)

    def test_a_value_retired_without_bounds_returns_with_the_bounds_of_its_label(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(db_session, factories, ("3", "2–6"))
        schema_id = world.schema.id
        actor = _actor(factories)
        row = _parameter(db_session, schema_id, 1)
        named = _values(db_session, row.id)["2–6"]
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)
        # «2–6» — не число и не подпись набора с шагом 2: выведено без границ
        retired = _values(db_session, row.id)["2–6"]
        assert (retired.retired_at is not None, retired.range_lo, retired.range_hi) == (
            True, None, None,
        )

        edit(db_session, world.family.id, [_ranged(2, 10, 4)], actor)

        back = _values(db_session, row.id)["2–6"]
        assert (back.id, back.retired_at, back.range_lo, back.range_hi) == (
            named.id, None, Decimal(2), Decimal(6),
        )
        # число 3 из «2–4» пришло в возвращённое «2–6»
        assert _row_value(db_session, contexts["3"], schema_id).id == named.id
        _assert_invariants(db_session, schema_id)

    def test_a_range_label_that_a_human_merged_into_another_value_is_refused(
        self, db_session, factories
    ):
        """Подпись «10 и более» слита человеком в «10»: слияние вариантов перестройка
        снять не может, поэтому правка отказывает `schema_bad_range` (предпросмотр и
        сохранение — один план), а база не меняется."""
        world, _contexts, _variants = _numbers_scene(
            db_session, factories, ("4", "10", "10 и более", "12")
        )
        actor = _actor(factories)
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)
        merge_parameter_values(
            db_session, parameter_id=row.id, source_value_id=values["10 и более"].id,
            target_value_id=values["10"].id, actor_id=actor,
        )
        before = _all_state(db_session)
        drafts = [_ranged(2, 10, 2)]

        for call in (
            lambda: preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts),
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts, preview_hash="x",
                actor_id=actor,
            ),
        ):
            with pytest.raises(WorkFamilyError) as raised:
                call()
            assert raised.value.code == "schema_bad_range"
        assert _all_state(db_session) == before
        # другие границы (подписи не совпадают со слитым) проходят
        edit(db_session, world.family.id, [_ranged(2, 9, 7)], actor)

    def test_the_routes_refuse_a_merged_range_label_with_422_and_change_nothing(
        self, admin_client, db_session, factories
    ):
        world, _contexts, _variants = _numbers_scene(
            db_session, factories, ("4", "10", "10 и более")
        )
        row = _parameter(db_session, world.schema.id, 1)
        values = _values(db_session, row.id)
        merge_parameter_values(
            db_session, parameter_id=row.id, source_value_id=values["10 и более"].id,
            target_value_id=values["10"].id, actor_id=_actor(factories),
        )
        db_session.commit()
        before = _all_state(db_session)
        body = {
            "parameters": [
                {
                    "ordinal": 1, "name": "Диаметр", "role": "main", "value_kind": "range",
                    "closed": True, "range": {"start": "2", "stop": "10", "step": "2"},
                }
            ]
        }

        preview = admin_client.post(
            f"{BASE}/families/{world.family.id}/schema/edit-preview", json=body
        )
        saved = admin_client.patch(
            f"{BASE}/families/{world.family.id}/schema", json={**body, "preview_hash": "x"}
        )

        for response in (preview, saved):
            assert response.status_code == 422, response.text
            assert _detail(response)["code"] == "schema_bad_range"
        assert _all_state(db_session) == before

    def test_a_merged_synonym_named_on_the_way_back_is_canonicalised_and_returns_its_target(
        self, db_session, factories
    ):
        world, contexts, _variants = _numbers_scene(
            db_session, factories, ("4", "4 мм", "7")
        )
        schema_id = world.schema.id
        actor = _actor(factories)
        row = _parameter(db_session, schema_id, 1)
        values = _values(db_session, row.id)
        merge_parameter_values(
            db_session, parameter_id=row.id, source_value_id=values["4 мм"].id,
            target_value_id=values["4"].id, actor_id=actor,
        )
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)
        assert _labels(db_session, schema_id, retired=True)[:2] == ["4", "7"]

        edit(db_session, world.family.id, [draft(1, "Диаметр", "4 мм")], actor)

        after = _values(db_session, row.id)
        assert _labels(db_session, schema_id, retired=False) == ["4"]
        assert (after["4"].id, after["4"].retired_at) == (values["4"].id, None)
        assert after["4 мм"].merged_into_id == values["4"].id
        _assert_invariants(db_session, schema_id, human_merges=(1, 1))

    def test_a_target_named_twice_on_the_way_back_returns_once(self, db_session, factories):
        world, _contexts, _variants = _numbers_scene(db_session, factories, ("4", "4 мм"))
        schema_id = world.schema.id
        actor = _actor(factories)
        row = _parameter(db_session, schema_id, 1)
        values = _values(db_session, row.id)
        merge_parameter_values(
            db_session, parameter_id=row.id, source_value_id=values["4 мм"].id,
            target_value_id=values["4"].id, actor_id=actor,
        )
        edit(db_session, world.family.id, [_ranged(2, 10, 2)], actor)

        edit(db_session, world.family.id, [draft(1, "Диаметр", "4", "4 мм")], actor)

        change = _events(db_session, "family_schema_edited", world.family.id)[-1].payload[
            "changes"
        ][0]
        assert change["values_revived"] == ["4"]
        assert _labels(db_session, schema_id, retired=False) == ["4"]


class TestEventsAndStaleRowsOfARebuild:
    def test_the_event_names_the_bounds_of_a_step_change_and_of_the_way_back(
        self, db_session, factories
    ):
        world, _contexts, _variants, actor = _ranged_scene(db_session, factories)
        schema_id = world.schema.id

        edit(db_session, world.family.id, [_ranged(2, 10, 4)], actor)
        rebuilt = _parameter(db_session, schema_id, 1)
        assert (rebuilt.range_from, rebuilt.range_to, rebuilt.range_step) == (
            Decimal(2), Decimal(10), Decimal(4),
        )
        edit(db_session, world.family.id, [draft(1, "Диаметр")], actor)

        step, back = (
            event.payload["changes"][0]
            for event in _events(db_session, "family_schema_edited", world.family.id)[-2:]
        )
        assert "value_kind" not in step
        assert step["range"] == {
            "from": {"start": "2", "stop": "10", "step": "2"},
            "to": {"start": "2", "stop": "10", "step": "4"},
        }
        assert back["value_kind"] == {"from": "range", "to": "exact"}
        assert back["range"] == {"from": {"start": "2", "stop": "10", "step": "4"}, "to": None}

    def test_a_row_of_a_context_awaiting_another_family_moved_between_the_calls_is_stale(
        self, db_session, factories
    ):
        """Строки контекста, ждущего другую семью, в хэш строк не входят, а числа
        переносов — входят: строка, переехавшая между вызовами, — `preview_changed`."""
        world, contexts, _variants = _numbers_scene(db_session, factories, ("4", "5", "7"))
        _set_pending(db_session, factories, contexts["5"], _family(db_session))
        drafts = [_ranged(2, 10, 2)]
        previewed = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        row = _parameter(db_session, world.schema.id, 1)
        _set_row(
            db_session, contexts["5"], row.id, value_id=_values(db_session, row.id)["4"].id
        )
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=previewed.preview_hash, actor_id=_actor(factories),
            )

        assert raised.value.code == "preview_changed"
        assert _all_state(db_session) == before


class TestThePreviewOfARebuildIsTheSave:
    @pytest.mark.parametrize(
        "second_body",
        [
            pytest.param(lambda: [_ranged(2, 10, 1)], id="finer_step"),
            pytest.param(lambda: [draft(1, "Диаметр", "4", "5")], id="back_to_exact"),
        ],
    )
    def test_the_requests_of_the_preview_are_the_jobs_of_the_save(
        self, db_session, factories, monkeypatch, second_body
    ):
        """Рендер параметра после перестройки в предпросмотре — тот же, что у
        поставленных заданий: значения в порядке `id`, вид нового набора. Набор
        шага 1 — крайние прежние, внутренние новые; возврат в точные — «4» из
        истории и новое «5»."""
        world, _contexts, _variants, actor = _ranged_scene(db_session, factories)
        drafts = second_body()
        planned = []
        real = schema_edit_module.preview_values_plan

        def spy(*args, **kwargs):
            plan = real(*args, **kwargs)
            planned.append(plan)
            return plan

        monkeypatch.setattr(schema_edit_module, "preview_values_plan", spy)
        summary = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        [previewed] = planned

        apply_schema_edit(
            db_session, family_id=world.family.id, drafts=drafts,
            preview_hash=summary.preview_hash, actor_id=actor,
        )

        queued = {(job.context_id, job.request_hash) for job in _live_values_jobs(db_session)}
        assert queued
        assert {
            (fp.context_id, fp.request_hash) for fp, _r, _o, _f in previewed.entries
        } == queued

    def test_a_number_carried_into_an_existing_value_joins_its_variant_in_the_preview_too(
        self, db_session, factories
    ):
        """Цель переноса уже есть («2–6» названо раньше): проекция пересчёта ставит
        строке её `id`, и число вариантов сводки — итог сохранения."""
        world, contexts, _variants = _numbers_scene(db_session, factories, ("3", "2–6"))
        schema_id = world.schema.id
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(db_session, world.family.id, [_ranged(2, 10, 4)], _actor(factories))

        assert (summary.variants_before, summary.variants_after_now) == (2, 1)
        assert _variant_of(db_session, contexts["3"]) == _variant_of(db_session, contexts["2–6"])
        _assert_summary_is_the_outcome(
            db_session, world.family.id, schema_id, summary, waiting_before, marked_before
        )


class TestOneNewValueIsOnePlaceholder:
    def test_two_off_list_rows_resolved_to_one_new_main_value_share_one_new_variant(
        self, db_session, factories
    ):
        """Проекция пересчёта даёт одному создаваемому значению один временный номер:
        два контекста, получившие одно новое значение, — один новый вариант, а не два."""
        world = _active_world(db_session, factories, ((1, "Материал", ["бетон"]),))
        schema_id = world.schema.id
        _update_parameter(db_session, _parameter(db_session, schema_id, 1).id, closed=True)
        _settle_context(db_session, world, world.context_id, _answer(_named(1, "бетон", "path")))
        arriving = []
        for _ in range(2):
            context_id = _second_context(db_session, factories, world)
            _settle_context(
                db_session, world, context_id, _answer((1, "new", "Газобетон", "path"))
            )
            arriving.append(context_id)
        assert _variant_of(db_session, arriving[0]) == _variant_of(db_session, arriving[1])

        summary = edit(
            db_session, world.family.id,
            [draft(1, "Материал", "бетон", "Газобетон", closed=True)], _actor(factories),
        )

        assert _variant_of(db_session, arriving[0]) == _variant_of(db_session, arriving[1])
        assert (summary.variants_before, summary.variants_after_now) == (2, 2)
        assert _active_variants(db_session, world.family.id) == 2

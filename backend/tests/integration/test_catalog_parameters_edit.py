"""Правка схемы семьи на месте: план, предпросмотр, применение (спека 3в §2.5–§2.7).

Тело правки — полное желаемое состояние активных параметров; правка пишет в
действующую версию (`revision` +1 за правку), не заводит версию, ничего не
удаляет. Здесь: по входу на каждую строку таблицы следствий, предел и номера,
проверки тела (каждая своим кодом), происхождение параметров, предпросмотр
(числа сводки против фактического итога) и `preview_hash`, охват правки одной
семьёй, порядок замков, события, маршруты и гонки на двух сессиях с настоящими
commit-ами. Перестройка значений существующего параметра (смена вида, границ,
шага) — отдельная работа и здесь не проверяется.

Гонки: пауза барьером ПОСЛЕ чтения `revision` (ответ уже у клиента), жёсткий
таймаут на каждое ожидание (`SET LOCAL lock_timeout`, `join` с `is_alive()`,
обрыв зависшего backend-а).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.schema_edit as schema_edit_module
from config import settings as app_settings
from crud.work_variants import _estimate, edit_summary_out
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticEvent,
    SemanticJob,
    SemanticJobKind,
    SemanticReconcileBatch,
    WorkVariant,
)
from services.schema_edit import EditSummary, apply_schema_edit, preview_schema_edit
from services.semantic_events import EVENT_REQUIRED_KEYS
from services.semantic_reconcile import reconcile_or_defer
from services.variant_request import render_request_for
from services.work_families import WorkFamilyError
from services.work_variants import (
    apply_values,
    freeze_schema,
    rebuild_schema,
    recompute_variants,
)
from tests.integration.parameters_edit_support import (
    PARAMS,
    draft,
    edit,
    same_drafts,
)
from tests.integration.test_work_variants_api import (
    BASE,
    _building,
    _detail,
    _family_with_params,
    _schemaless_family,
)
from tests.integration.test_work_variants_concurrency import _Scene
from tests.integration.test_work_variants_core import (
    _answer,
    _guarded,
    _lock_sequence,
    _named,
    _parameter_in,
    _schema_answer,
    _set_pending,
    _table_rows,
    _world,
)
from tests.integration.test_work_variants_material import _capturing_sql, _settings
from tests.integration.test_work_variants_schema import _param, _schema, _variant
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _all_state,
    _jobs,
    _second_context,
    _settle_context,
    _variant_of,
    _variant_row,
)

pytestmark = pytest.mark.integration



# ---------------------------------------------------------------------------
#  Помощники (их берут и наборы других точек)
# ---------------------------------------------------------------------------

def _refusal(call) -> str:
    with pytest.raises(WorkFamilyError) as raised:
        call()
    return raised.value.code


def _refused_edit(db, family_id, drafts, actor_id) -> str:
    """Код отказа предпросмотра; сохранение с любым хэшем отказывает тем же."""
    code = _refusal(lambda: preview_schema_edit(db, family_id=family_id, drafts=drafts))
    assert _refusal(
        lambda: apply_schema_edit(
            db, family_id=family_id, drafts=drafts, preview_hash="x", actor_id=actor_id
        )
    ) == code
    return code


def _revision(db, schema_id) -> int:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterSchema.revision).where(FamilyParameterSchema.id == schema_id)
    ).scalar_one()


def _parameter(db, schema_id, ordinal) -> FamilyParameter:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameter).where(
            FamilyParameter.schema_id == schema_id, FamilyParameter.ordinal == ordinal
        )
    ).scalar_one()


def _values(db, parameter_id) -> dict[str, FamilyParameterValue]:
    db.expire_all()
    return {
        row.value: row
        for row in db.execute(
            sa.select(FamilyParameterValue)
            .where(FamilyParameterValue.parameter_id == parameter_id)
            .order_by(FamilyParameterValue.id)
        ).scalars()
    }


def _context_row(db, context_id, parameter_id) -> ContextParameterValue | None:
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue).where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
    ).scalar_one_or_none()


def _events(db, event_type, family_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticEvent)
            .where(SemanticEvent.event_type == event_type, SemanticEvent.family_id == family_id)
            .order_by(SemanticEvent.id)
        ).scalars()
    )


def _values_jobs(db, **where) -> list[SemanticJob]:
    return _jobs(db, "context_values", **where)


def _live_values_jobs(db, **where) -> list[SemanticJob]:
    return [job for job in _values_jobs(db, **where) if job.status == "pending"]


def _active_variants(db, family_id) -> int:
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count())
        .select_from(WorkVariant)
        .where(WorkVariant.family_id == family_id, WorkVariant.status == "active")
    ).scalar_one()


def _variants_of(db, contexts) -> dict[int, int | None]:
    return {context_id: _variant_of(db, context_id) for context_id in contexts}


def _set_row(db, context_id, parameter_id, **values):
    db.execute(
        sa.update(ContextParameterValue)
        .where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
        .values(**values)
    )
    db.flush()
    db.expire_all()


def _update_parameter(db, parameter_id, **values):
    db.execute(sa.update(FamilyParameter).where(FamilyParameter.id == parameter_id).values(**values))
    db.flush()
    db.expire_all()


def _scene(db, factories):
    """Две строки семьи с вариантами: `(50 мм, бетон)` и `(50 мм, кирпич)`, оба
    параметра главные."""
    world = _active_world(db, factories)
    second = _second_context(db, factories, world)
    first_variant = _settle_context(
        db, world, world.context_id, _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))
    )
    second_variant = _settle_context(
        db, world, second, _answer(_named(1, "50 мм"), _named(2, "кирпич", "path"))
    )
    return world, second, first_variant, second_variant


def _job_hashes(db, kind) -> dict[int, tuple[str, str]]:
    return {job.id: (job.request_hash, job.status) for job in _jobs(db, kind)}


def _waiting_ids(db) -> set[int]:
    """Ждущие задания значений (`pending`, `privacy_hold`)."""
    return {
        job.id for job in _values_jobs(db) if job.status in ("pending", "privacy_hold")
    }


def _marked_contexts_of(db, family_id) -> set[int]:
    """Контексты, у которых хоть одна строка версии семьи помечена «уточняется»."""
    db.expire_all()
    return set(
        db.execute(
            sa.select(ContextParameterValue.context_id)
            .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
            .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
            .where(
                FamilyParameterSchema.family_id == family_id,
                ContextParameterValue.refresh_requested_at.is_not(None),
            )
        ).scalars()
    )


def _assert_summary_is_the_outcome(db, family_id, schema_id, summary, waiting_before, marked_before):
    """Числа сводки против того, что сохранение сделало на самом деле:
    действующие варианты; поставленные правкой (созданные и возвращённые) задания по
    номерам; новые отметки «уточняется»; снятые ждущие задания. Эталон считается по
    базе, а не той же функцией, что строит сводку."""
    active = sorted(
        db.execute(
            sa.select(FamilyParameter.ordinal).where(
                FamilyParameter.schema_id == schema_id, FamilyParameter.archived_at.is_(None)
            )
        ).scalars()
    )
    asked: dict[int, int] = {}
    for job in _values_jobs(db):
        if job.status not in ("pending", "privacy_hold") or job.id in waiting_before:
            continue
        for ordinal in job.requested_ordinals or active:
            asked[ordinal] = asked.get(ordinal, 0) + 1
    repriced = [
        job for job in _values_jobs(db)
        if job.id in waiting_before and job.status == "cancelled"
        and job.cancel_reason in ("input_changed", "stale_hold")
    ]
    assert summary.variants_after_now == db.execute(
        sa.select(sa.func.count())
        .select_from(WorkVariant)
        .where(WorkVariant.schema_id == schema_id, WorkVariant.status == "active")
    ).scalar_one()
    assert tuple((o, n) for o, _name, n in summary.asked_by_parameter) == tuple(
        sorted(asked.items())
    )
    assert summary.refreshing_contexts == len(_marked_contexts_of(db, family_id) - marked_before)
    assert summary.repriced_pending_jobs == len(repriced)


# ---------------------------------------------------------------------------
#  Правка на месте: что остаётся и что меняется
# ---------------------------------------------------------------------------

class TestEditInPlace:
    def test_an_edit_without_changes_writes_nothing(self, db_session, factories):
        world, _second, _v1, _v2 = _scene(db_session, factories)
        before = _all_state(db_session)
        events = len(_events(db_session, "family_schema_edited", world.family.id))

        summary = edit(db_session, world.family.id, same_drafts(), _actor(factories))

        assert _all_state(db_session) == before
        assert _revision(db_session, world.schema.id) == 0
        assert len(_events(db_session, "family_schema_edited", world.family.id)) == events
        assert (summary.variants_before, summary.variants_after_now) == (2, 2)
        assert summary.asked_by_parameter == ()

    def test_a_cosmetic_name_is_renamed_in_place_and_the_version_is_kept(
        self, db_session, factories
    ):
        world, *_ = _scene(db_session, factories)
        parameter_id = world.parameters[1]
        drafts = same_drafts()
        drafts[0] = draft(1, "  ТОЛЩИНА ", *drafts[0].values)

        edit(db_session, world.family.id, drafts, _actor(factories))

        row = _parameter(db_session, world.schema.id, 1)
        assert (row.id, row.name, row.name_norm) == (parameter_id, "ТОЛЩИНА", "толщина")
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == world.family.id
            )
        ).scalar_one() == 1
        assert _revision(db_session, world.schema.id) == 1

    def test_a_value_with_the_same_normalised_form_is_renamed_in_place(
        self, db_session, factories
    ):
        world, *_ = _scene(db_session, factories)
        value_id = world.values[(1, "50 мм")]
        drafts = same_drafts()
        drafts[0] = draft(1, "Толщина", "  50   ММ ", "100 мм")

        edit(db_session, world.family.id, drafts, _actor(factories))

        db_session.expire_all()
        assert db_session.get(FamilyParameterValue, value_id).value == "50   ММ"

    def test_merged_values_are_not_part_of_the_comparison(self, db_session, factories):
        world = _active_world(db_session, factories)
        brick = db_session.get(FamilyParameterValue, world.values[(2, "кирпич")])
        brick.merged_into_id = world.values[(2, "бетон")]
        db_session.flush()
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон")

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _revision(db_session, world.schema.id) == 0
        assert db_session.get(FamilyParameterValue, brick.id).merged_into_id == (
            world.values[(2, "бетон")]
        )

    def test_a_context_with_a_variant_gets_no_job_after_a_cosmetic_rename(
        self, db_session, factories
    ):
        world, *_ = _scene(db_session, factories)
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", "50 ММ", "100 мм")

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _values_jobs(db_session) == []
        assert _jobs(db_session, "family_schema") == []
        assert _events(db_session, "family_schema_frozen", world.family.id) == []

    def test_a_pending_job_is_replaced_by_one_with_the_new_request_hash(
        self, db_session, factories
    ):
        from services.semantic_reconcile import reconcile_or_defer

        world = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id])
        [old_job] = _values_jobs(db_session)
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", *drafts[0].values)

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        jobs = _values_jobs(db_session)
        assert {job.id: job.status for job in jobs}[old_job.id] == "cancelled"
        live = [job for job in jobs if job.status == "pending"]
        assert len(live) == 1 and live[0].id != old_job.id
        assert live[0].request_hash != old_job.request_hash
        assert summary.repriced_pending_jobs == 1


# ---------------------------------------------------------------------------
#  Таблица следствий §2.5: по входу на строку
# ---------------------------------------------------------------------------

class TestConsequenceTable:
    def test_a_secondary_parameter_changes_no_variant_and_no_main_row(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)
        main_rows = _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.parameter_id.in_([world.parameters[1], world.parameters[2]]),
        )
        before = _variants_of(db_session, [world.context_id, second])

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", "без", role="secondary")],
            _actor(factories),
        )

        assert _variants_of(db_session, [world.context_id, second]) == before == {
            world.context_id: v1, second: v2,
        }
        assert _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.parameter_id.in_([world.parameters[1], world.parameters[2]]),
        ) == main_rows
        # спрашивают только новый параметр: у обоих контекстов
        jobs = _live_values_jobs(db_session)
        assert sorted((job.context_id, job.requested_ordinals) for job in jobs) == sorted(
            [(world.context_id, [3]), (second, [3])]
        )
        assert summary.asked_by_parameter == ((3, "Армирование", 2),)
        assert summary.variants_pending_answers is False

    def test_a_main_parameter_keeps_every_context_in_its_variant_until_the_answer(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка")], _actor(factories),
        )

        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: v1, second: v2,
        }
        assert sorted(job.requested_ordinals[0] for job in _live_values_jobs(db_session)) == [3, 3]
        assert (summary.variants_before, summary.variants_after_now) == (2, 2)
        assert summary.variants_pending_answers is True

    def test_removing_a_secondary_parameter_asks_nothing_and_keeps_variants(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)
        _update_parameter(db_session, world.parameters[2], role="secondary")
        # (50 мм, бетон) и (50 мм, кирпич) теперь один вариант по главному параметру 1
        from services.work_variants import recompute_variants

        recompute_variants(db_session, family_id=world.family.id, schema_id=world.schema.id)
        shared = _variant_of(db_session, world.context_id)
        assert _variant_of(db_session, second) == shared

        summary = edit(db_session, world.family.id, same_drafts()[:1], _actor(factories))

        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: shared, second: shared,
        }
        assert _values_jobs(db_session) == []
        assert _parameter(db_session, world.schema.id, 2).archived_at is not None
        assert _context_row(db_session, world.context_id, world.parameters[2]) is not None
        assert summary.asked_by_parameter == ()

    def test_removing_a_main_parameter_recomputes_variants_and_asks_nothing(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)

        summary = edit(db_session, world.family.id, same_drafts()[:1], _actor(factories))

        shared = _variant_of(db_session, world.context_id)
        assert shared == _variant_of(db_session, second) and shared not in (v1, v2)
        assert {_variant_row(db_session, v).status for v in (v1, v2)} == {"archived"}
        assert _values_jobs(db_session) == []
        assert (summary.variants_before, summary.variants_after_now) == (2, 1)
        assert _active_variants(db_session, world.family.id) == 1

    def test_a_role_change_splits_or_merges_and_the_way_back_returns_the_same_variants(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)
        actor = _actor(factories)
        drafts = same_drafts()

        drafts[1] = draft(2, "Материал", "бетон", "кирпич", role="secondary")
        merging = edit(db_session, world.family.id, drafts, actor)
        merged = _variant_of(db_session, world.context_id)
        assert merged == _variant_of(db_session, second)
        assert (merging.variants_before, merging.variants_after_now) == (
            2, _active_variants(db_session, world.family.id),
        ) == (2, 1)

        drafts[1] = draft(2, "Материал", "бетон", "кирпич", role="main")
        splitting = edit(db_session, world.family.id, drafts, actor)

        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: v1, second: v2,
        }
        # архивные v1 и v2 возвращаются — сводка считает их, как пересчёт
        assert (splitting.variants_before, splitting.variants_after_now) == (
            1, _active_variants(db_session, world.family.id),
        ) == (1, 2)
        assert _variant_row(db_session, merged).status == "archived"
        assert _values_jobs(db_session) == []

    def test_toggling_closed_changes_no_variant_and_asks_nothing(self, db_session, factories):
        world, second, v1, v2 = _scene(db_session, factories)
        drafts = same_drafts()
        drafts[0] = draft(1, "Толщина", "50 мм", "100 мм", closed=True)

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _parameter(db_session, world.schema.id, 1).closed is True
        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: v1, second: v2,
        }
        assert _values_jobs(db_session) == []


class TestAddingAValueToAnExactParameter:
    """Строки `none` и `off_list` получают «уточняется» и запрос одного параметра;
    `off_list` с текстом той же нормы получает значение сразу, без модели."""

    def _scene(self, db, factories):
        world = _active_world(db, factories)
        second = _second_context(db, factories, world)
        third = _second_context(db, factories, world)
        fourth = _second_context(db, factories, world)
        _update_parameter(db, world.parameters[2], closed=True)
        settled = _settle_context(
            db, world, world.context_id, _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))
        )
        _settle_context(db, world, second, _answer(_named(1, "50 мм"), (2, "none", None, None)))
        _settle_context(
            db, world, third, _answer(_named(1, "50 мм"), (2, "new", "Керамзит", "path"))
        )
        _settle_context(
            db, world, fourth, _answer(_named(1, "50 мм"), (2, "new", "Газобетон", "path"))
        )
        return world, second, third, fourth, settled

    def test_rows_are_marked_and_the_matching_off_list_row_gets_the_value(
        self, db_session, factories
    ):
        world, second, third, fourth, settled = self._scene(db_session, factories)
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "ГАЗОБЕТОН", closed=True)
        before_first = _table_rows(
            db_session, ContextParameterValue, ContextParameterValue.context_id == world.context_id
        )

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        material = world.parameters[2]
        new_value = _values(db_session, material)["ГАЗОБЕТОН"]
        assert new_value.origin == "manual"
        resolved = _context_row(db_session, fourth, material)
        assert (resolved.value_id, resolved.source, resolved.off_list_text) == (
            new_value.id, "manual", None,
        )
        assert resolved.refresh_requested_at is None
        for context_id in (second, third):
            assert _context_row(db_session, context_id, material).refresh_requested_at is not None
        assert _table_rows(
            db_session, ContextParameterValue, ContextParameterValue.context_id == world.context_id
        ) == before_first
        # спрашивают только этот параметр и только у помеченных
        assert sorted((job.context_id, job.requested_ordinals) for job in _live_values_jobs(
            db_session
        )) == sorted([(second, [2]), (third, [2])])
        assert summary.refreshing_contexts == 2
        assert summary.asked_by_parameter == ((2, "Материал", 2),)

    def test_the_context_that_got_the_value_moves_to_the_new_variant(
        self, db_session, factories
    ):
        world, second, third, fourth, settled = self._scene(db_session, factories)
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "газобетон", closed=True)
        before = _variants_of(db_session, [world.context_id, second, third, fourth])

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        after = _variants_of(db_session, [world.context_id, second, third, fourth])
        assert after[fourth] != before[fourth]
        assert {c: v for c, v in after.items() if c != fourth} == {
            c: v for c, v in before.items() if c != fourth
        }
        assert (summary.variants_before, summary.variants_after_now) == (
            _active_variants_before(before), _active_variants(db_session, world.family.id),
        )

    def test_a_value_of_a_secondary_parameter_leaves_variants_alone(self, db_session, factories):
        world, second, third, fourth, settled = self._scene(db_session, factories)
        _update_parameter(db_session, world.parameters[2], role="secondary")

        recompute_variants(db_session, family_id=world.family.id, schema_id=world.schema.id)
        before = _variants_of(db_session, [world.context_id, second, third, fourth])
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "газобетон", role="secondary", closed=True)

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _variants_of(db_session, [world.context_id, second, third, fourth]) == before

    def test_a_row_that_already_says_refreshing_is_not_counted_again(self, db_session, factories):
        world, second, third, fourth, settled = self._scene(db_session, factories)
        _set_row(db_session, second, world.parameters[2], refresh_requested_at=sa.func.now())
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "новое", closed=True)

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        assert summary.refreshing_contexts == 2  # третий и четвёртый; второй уже помечен


def _active_variants_before(variants_by_context) -> int:
    return len({variant for variant in variants_by_context.values() if variant is not None})


# ---------------------------------------------------------------------------
#  Предел, номера параметров и проверки тела: каждое нарушение — свой код
# ---------------------------------------------------------------------------

_TEN = tuple((i, f"Параметр {i}", [f"значение {i}"]) for i in range(1, 11))


def _refuse(db, factories, drafts, code, *, world=None, mutate=None):
    """Отказ нужным кодом и неизменная база."""
    world = world or _active_world(db, factories)
    if mutate is not None:
        mutate(db, factories, world)
    before = _all_state(db)

    got = _refused_edit(db, world.family.id, drafts(world), _actor(factories))

    assert got == code
    assert _all_state(db) == before


class TestParameterLimit:
    def test_eleven_active_parameters_in_the_body_are_refused(self, db_session, factories):
        world = _active_world(db_session, factories, _TEN)
        drafts = [*same_drafts(_TEN), draft(None, "Одиннадцатый", "а")]

        _refuse(
            db_session, factories, lambda _w: drafts, "schema_too_many_parameters", world=world
        )

    def test_ten_active_parameters_in_the_body_pass(self, db_session, factories):
        world = _active_world(db_session, factories, _TEN[:2])
        drafts = [*same_drafts(_TEN[:2]), *(draft(None, f"Новый {i}", "а") for i in range(8))]

        edit(db_session, world.family.id, drafts, _actor(factories))

        active = db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameter).where(
                FamilyParameter.schema_id == world.schema.id,
                FamilyParameter.archived_at.is_(None),
            )
        ).scalar_one()
        assert active == 10

    def test_the_limit_counts_the_state_after_the_edit_not_the_additions(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _TEN)
        # десять в схеме, из тела уходит один и приходит один: итог десять
        drafts = [*same_drafts(_TEN[:9]), draft(None, "Взамен", "а")]

        edit(db_session, world.family.id, drafts, _actor(factories))

        row = _parameter(db_session, world.schema.id, 10)
        assert row.archived_at is not None
        assert _parameter(db_session, world.schema.id, 11).name == "Взамен"

    def test_an_archived_parameter_does_not_count_against_the_limit(self, db_session, factories):
        world = _active_world(db_session, factories, _TEN)
        edit(db_session, world.family.id, same_drafts(_TEN[:9]), _actor(factories))

        edit(
            db_session, world.family.id,
            [*same_drafts(_TEN[:9]), draft(None, "Десятый новый", "а")], _actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 11).archived_at is None


class TestOrdinals:
    def test_a_new_parameter_after_an_archived_one_gets_max_plus_one(self, db_session, factories):
        world = _active_world(db_session, factories, (*PARAMS, (3, "Класс", ["В15", "В25"])))
        edit(db_session, world.family.id, same_drafts(PARAMS), _actor(factories))
        assert _parameter(db_session, world.schema.id, 3).archived_at is not None

        edit(
            db_session, world.family.id, [*same_drafts(PARAMS), draft(None, "Армирование", "а")],
            _actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 4).name == "Армирование"
        assert _parameter(db_session, world.schema.id, 3).name == "Класс"

    def test_the_keys_of_the_new_and_the_archived_parameter_with_no_answer_differ(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, (*PARAMS, (3, "Класс", ["В15", "В25"])))
        first = _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон", "path"), (3, "none", None, None)),
        )
        edit(db_session, world.family.id, same_drafts(PARAMS), _actor(factories))
        moved = _variant_of(db_session, world.context_id)
        assert moved != first
        edit(
            db_session, world.family.id, [*same_drafts(PARAMS), draft(None, "Армирование", "а")],
            _actor(factories),
        )

        apply_values(
            db_session, context_id=world.context_id, schema_id=world.schema.id,
            answer=_answer((4, "none", None, None)), paths_hash="p", guard=None,
            settings=_settings(), requested_ordinals=(4,),
        )

        key_of = {
            row.id: row.values_key
            for row in db_session.execute(sa.select(WorkVariant.id, WorkVariant.values_key)).all()
        }
        now = _variant_of(db_session, world.context_id)
        assert now not in (first, moved)
        assert key_of[first].endswith("|3=?") and key_of[now].endswith("|4=?")
        assert key_of[now] != key_of[first]

    def test_the_last_free_ordinal_is_handed_out(self, db_session, factories):
        world = _active_world(db_session, factories)
        archived = _param(db_session, world.schema, 99, "Архивный")
        archived.archived_at = sa.func.now()
        db_session.flush()

        edit(
            db_session, world.family.id, [*same_drafts(), draft(None, "Сотый", "а")],
            _actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 100).name == "Сотый"

    def test_exhausted_ordinals_are_refused_with_the_code(self, db_session, factories):
        def archive_hundredth(db, _factories, world):
            archived = _param(db, world.schema, 100, "Архивный")
            archived.archived_at = sa.func.now()
            db.flush()

        _refuse(
            db_session, factories, lambda _w: [*same_drafts(), draft(None, "Новый", "а")],
            "schema_ordinals_exhausted", mutate=archive_hundredth,
        )

    def test_two_new_parameters_do_not_fit_after_ninety_nine(self, db_session, factories):
        def archive_ninety_ninth(db, _factories, world):
            archived = _param(db, world.schema, 99, "Архивный")
            archived.archived_at = sa.func.now()
            db.flush()

        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, "Один", "а"), draft(None, "Два", "а")],
            "schema_ordinals_exhausted", mutate=archive_ninety_ninth,
        )

    def test_a_repeated_ordinal_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(2, "Другое", "а")], "schema_bad_ordinals",
        )

    def test_an_ordinal_that_is_not_an_active_parameter_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(4, "Четвёртый", "а")], "schema_bad_ordinals",
        )

    def test_the_ordinal_of_an_archived_parameter_is_refused(self, db_session, factories):
        world = _active_world(db_session, factories)
        edit(db_session, world.family.id, same_drafts()[:1], _actor(factories))

        _refuse(
            db_session, factories, lambda _w: same_drafts(), "schema_bad_ordinals", world=world
        )


class TestBodyChecks:
    def test_a_semantic_rename_of_a_parameter_is_refused(self, db_session, factories):
        def drafts(_world):
            changed = same_drafts()
            changed[0] = draft(1, "Класс бетона", *changed[0].values)
            return changed

        _refuse(db_session, factories, drafts, "schema_parameter_renamed")

    def test_a_removed_value_is_refused(self, db_session, factories):
        def drafts(_world):
            changed = same_drafts()
            changed[0] = draft(1, "Толщина", "50 мм")
            return changed

        _refuse(db_session, factories, drafts, "schema_value_removed")

    def test_a_renamed_value_is_refused_as_a_removal(self, db_session, factories):
        def drafts(_world):
            changed = same_drafts()
            changed[0] = draft(1, "Толщина", "60 мм", "100 мм")
            return changed

        _refuse(db_session, factories, drafts, "schema_value_removed")

    def test_a_value_that_is_only_retired_may_be_absent_from_the_body(self, db_session, factories):
        world = _active_world(db_session, factories)
        value_id = world.values[(1, "100 мм")]
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == value_id)
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        drafts = same_drafts()
        drafts[0] = draft(1, "Толщина", "50 мм")

        edit(db_session, world.family.id, drafts, _actor(factories))

        db_session.expire_all()
        assert db_session.get(FamilyParameterValue, value_id).retired_at is not None

    def test_a_retired_value_named_again_returns_from_the_history(self, db_session, factories):
        world = _active_world(db_session, factories)
        value_id = world.values[(1, "100 мм")]
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == value_id)
            .values(retired_at=sa.func.now())
        )
        db_session.flush()

        edit(db_session, world.family.id, same_drafts(), _actor(factories))

        values = _values(db_session, world.parameters[1])
        assert values["100 мм"].id == value_id and values["100 мм"].retired_at is None
        assert len(values) == 2

    def test_a_merged_synonym_named_again_adds_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)
        brick = db_session.get(FamilyParameterValue, world.values[(2, "кирпич")])
        brick.merged_into_id = world.values[(2, "бетон")]
        db_session.flush()
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "Кирпич")

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert len(_values(db_session, world.parameters[2])) == 2
        assert _revision(db_session, world.schema.id) == 0

    def test_a_blank_parameter_name_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, " «» ", "сетка")], "schema_blank",
        )

    def test_a_blank_value_is_refused(self, db_session, factories):
        def drafts(_world):
            changed = same_drafts()
            changed[0] = draft(1, "Толщина", "50 мм", "100 мм", "  ")
            return changed

        _refuse(db_session, factories, drafts, "schema_blank")

    def test_a_blank_value_of_a_new_parameter_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, "Армирование", "сетка", "")], "schema_blank",
        )

    def test_a_duplicate_name_by_norm_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, " ТОЛЩИНА ", "а")],
            "schema_duplicate_parameter",
        )

    def test_a_name_of_an_archived_parameter_may_be_taken_by_a_new_one(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        edit(db_session, world.family.id, same_drafts()[:1], _actor(factories))

        edit(
            db_session, world.family.id, [*same_drafts()[:1], draft(None, "Материал", "а")],
            _actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 3).name == "Материал"

    def test_a_pending_building_version_is_refused(self, db_session, factories):
        def building(db, factories, world):
            _schema(db, factories, world.family, version=2, status="building")

        _refuse(db_session, factories, lambda _w: same_drafts(), "schema_building", mutate=building)

    def test_a_family_without_a_current_version_is_refused(self, db_session, factories):
        def drop_current(db, _factories, world):
            world.schema.status = "superseded"
            world.schema.superseded_at = world.schema.frozen_at
            db.flush()

        _refuse(
            db_session, factories, lambda _w: same_drafts(), "schema_no_current",
            mutate=drop_current,
        )

    def test_a_family_that_is_not_active_is_refused(self, db_session, factories):
        world = _world(db_session, factories)  # семья в черновике
        before = _all_state(db_session)

        code = _refused_edit(db_session, world.family.id, same_drafts(), _actor(factories))

        assert code == "family_not_active"
        assert _all_state(db_session) == before

    def test_an_unknown_family_is_refused(self, db_session, factories):
        code = _refused_edit(db_session, 987654321, same_drafts(), _actor(factories))

        assert code == "family_not_found"


class TestRangeChecks:
    @pytest.mark.parametrize(
        "rng",
        [
            pytest.param((10, 10, 2), id="start-equals-stop"),
            pytest.param((20, 10, 2), id="start-after-stop"),
            pytest.param((0, 10, 0), id="zero-step"),
            pytest.param((0, 10, -1), id="negative-step"),
            pytest.param((0, 10, 3), id="not-a-whole-number-of-steps"),
            # частное точное (2,5), но не целое: отказ даёт проверка остатка
            pytest.param((0, 1, "0.4"), id="an-exact-quotient-that-is-not-whole"),
            pytest.param(("NaN", 10, 1), id="nan"),
            pytest.param((0, "Infinity", 1), id="infinity"),
        ],
    )
    def test_a_bad_range_is_refused(self, db_session, factories, rng):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, "Диаметр", kind="range", rng=rng)],
            "schema_bad_range",
        )

    def test_a_range_without_bounds_is_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, "Диаметр", kind="range")],
            "schema_bad_range",
        )

    def test_the_range_check_applies_to_an_existing_parameter_too(self, db_session, factories):
        def drafts(_world):
            changed = same_drafts()
            changed[0] = draft(1, "Толщина", kind="range", rng=(10, 10, 2))
            return changed

        _refuse(db_session, factories, drafts, "schema_bad_range")

    def test_more_than_twenty_eight_inner_values_are_refused(self, db_session, factories):
        _refuse(
            db_session, factories,
            lambda _w: [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(0, 29, 1))],
            "schema_range_too_many_values",
        )

    def test_exactly_twenty_eight_inner_values_pass(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(0, 28, 1))],
            _actor(factories),
        )

        values = _values(db_session, _parameter(db_session, world.schema.id, 3).id)
        assert len(values) == 30
        assert "до 0" in values and "28 и более" in values


# ---------------------------------------------------------------------------
#  Чей параметр: происхождение
# ---------------------------------------------------------------------------

class TestOrigins:
    def _origins(self, db, world) -> tuple[str, str]:
        return (
            _parameter(db, world.schema.id, 1).origin,
            _parameter(db, world.schema.id, 2).origin,
        )

    def test_a_new_parameter_is_human_and_its_values_are_manual(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", "без")], _actor(factories),
        )

        row = _parameter(db_session, world.schema.id, 3)
        assert row.origin == "human"
        assert {value.origin for value in _values(db_session, row.id).values()} == {"manual"}

    @pytest.mark.parametrize(
        "touched",
        [
            pytest.param(draft(1, "ТОЛЩИНА", "50 мм", "100 мм"), id="name"),
            pytest.param(draft(1, "Толщина", "50 мм", "100 мм", "150 мм"), id="value-added"),
            pytest.param(draft(1, "Толщина", "50  ММ", "100 мм"), id="value-spelling"),
            pytest.param(draft(1, "Толщина", "50 мм", "100 мм", role="secondary"), id="role"),
            pytest.param(draft(1, "Толщина", "50 мм", "100 мм", closed=True), id="closed"),
        ],
    )
    def test_a_model_parameter_touched_by_the_edit_becomes_human(
        self, db_session, factories, touched
    ):
        world = _active_world(db_session, factories)

        edit(db_session, world.family.id, [touched, *same_drafts()[1:]], _actor(factories))

        assert self._origins(db_session, world) == ("human", "model")

    def test_an_untouched_model_parameter_stays_model(self, db_session, factories):
        world = _active_world(db_session, factories)
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", *drafts[0].values)

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _parameter(db_session, world.schema.id, 2).origin == "model"
        assert {v.origin for v in _values(db_session, world.parameters[2]).values()} == {"schema"}

    def test_archiving_does_not_change_the_origin(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(db_session, world.family.id, same_drafts()[:1], _actor(factories))

        archived = _parameter(db_session, world.schema.id, 2)
        assert (archived.archived_at is not None, archived.origin) == (True, "model")


# ---------------------------------------------------------------------------
#  Новый параметр-диапазон: значения строит система
# ---------------------------------------------------------------------------

class TestNewRangeParameter:
    def test_the_system_builds_the_values_and_closes_the_list(self, db_session, factories):
        world, second, _v1, _v2 = _scene(db_session, factories)

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(120, 200, 40))],
            _actor(factories),
        )

        row = _parameter(db_session, world.schema.id, 3)
        assert (row.value_kind, row.closed, row.origin, row.role) == (
            "range", True, "human", "main",
        )
        assert (row.range_from, row.range_to, row.range_step) == (
            Decimal(120), Decimal(200), Decimal(40),
        )
        values = _values(db_session, row.id)
        assert list(values) == ["до 120", "120–160", "160–200", "200 и более"]
        assert [(v.range_lo, v.range_hi) for v in values.values()] == [
            (None, Decimal(120)), (Decimal(120), Decimal(160)),
            (Decimal(160), Decimal(200)), (Decimal(200), None),
        ]
        assert {v.origin for v in values.values()} == {"manual"}
        assert sorted(job.requested_ordinals[0] for job in _live_values_jobs(db_session)) == [3, 3]
        assert summary.asked_by_parameter == ((3, "Диаметр", 2),)

    def test_the_closed_flag_of_the_body_does_not_matter_for_a_range(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Диаметр", kind="range", closed=False, rng=(0, 10, 5))],
            _actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 3).closed is True

    def test_an_unchanged_range_may_change_its_role_and_be_archived(self, db_session, factories):
        world = _active_world(db_session, factories)
        actor = _actor(factories)
        created = [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(120, 200, 40))]
        edit(db_session, world.family.id, created, actor)
        row = _parameter(db_session, world.schema.id, 3)
        before = _values(db_session, row.id)

        # те же границы, другая роль; значения в теле для диапазона не важны
        edit(
            db_session, world.family.id,
            [
                *same_drafts(),
                draft(3, "Диаметр", kind="range", role="secondary", rng=(120, 200, 40)),
            ],
            actor,
        )
        assert _parameter(db_session, world.schema.id, 3).role == "secondary"
        assert _values(db_session, row.id).keys() == before.keys()
        edit(db_session, world.family.id, same_drafts(), actor)

        assert _parameter(db_session, world.schema.id, 3).archived_at is not None
        assert _revision(db_session, world.schema.id) == 3

    def test_a_secondary_range_parameter_is_possible(self, db_session, factories):
        world, second, v1, v2 = _scene(db_session, factories)

        edit(
            db_session, world.family.id,
            [
                *same_drafts(),
                draft(None, "Диаметр", kind="range", role="secondary", rng=(120, 200, 40)),
            ],
            _actor(factories),
        )

        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: v1, second: v2,
        }


# ---------------------------------------------------------------------------
#  Сводка: числа предпросмотра равны итогу сохранения
# ---------------------------------------------------------------------------

class TestSummaryEqualsTheOutcome:
    def test_a_mixed_edit(self, db_session, factories):
        world, second, v1, v2 = _scene(db_session, factories)
        third = _second_context(db_session, factories, world)
        reconcile_or_defer(db_session, [third])
        [stale] = _values_jobs(db_session, context_id=third)
        assert (stale.status, stale.requested_ordinals) == ("pending", None)
        drafts = [
            draft(1, "ТОЛЩИНА", "50 мм", "100 мм"),
            draft(2, "Материал", "бетон", "кирпич"),
            draft(None, "Армирование", "сетка"),
        ]

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        # независимый итог: что на самом деле стоит в очереди после правки
        live = _live_values_jobs(db_session)
        assert sorted((job.context_id, job.requested_ordinals) for job in live) == sorted(
            [(world.context_id, [3]), (second, [3]), (third, None)]
        )
        assert {job.id: (job.status, job.cancel_reason) for job in _values_jobs(db_session)}[
            stale.id
        ] == ("cancelled", "input_changed")
        assert summary.asked_by_parameter == (
            (1, "ТОЛЩИНА", 1), (2, "Материал", 1), (3, "Армирование", 3),
        )
        assert summary.repriced_pending_jobs == 1
        assert summary.refreshing_contexts == 0
        assert (summary.variants_before, summary.variants_after_now) == (
            2, _active_variants(db_session, world.family.id),
        )
        assert summary.variants_pending_answers is True
        entries = [
            (SemanticJobKind.context_values, render_request_for(db_session, job, settings=app_settings))
            for job in live
        ]
        reserve, cached = _estimate(db_session, entries)
        assert (summary.reserve_usd, summary.expected_cached_usd) == (reserve, cached)
        assert summary.reserve_usd > 0

    def test_the_preview_numbers_are_the_same_as_the_applied_ones(self, db_session, factories):
        world, second, third, fourth, _settled = TestAddingAValueToAnExactParameter()._scene(
            db_session, factories
        )
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "газобетон", closed=True)

        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        previewed = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        applied = apply_schema_edit(
            db_session, family_id=world.family.id, drafts=drafts,
            preview_hash=previewed.preview_hash, actor_id=_actor(factories),
        )

        assert applied == previewed
        # сверка с тем, что сохранение сделало на самом деле, а не с той же
        # функцией, что построила сводку
        _assert_summary_is_the_outcome(
            db_session, world.family.id, world.schema.id, previewed, waiting_before, marked_before
        )
        assert previewed.refreshing_contexts == 2 and previewed.asked_by_parameter == (
            (2, "Материал", 2),
        )


# ---------------------------------------------------------------------------
#  preview_hash
# ---------------------------------------------------------------------------

class TestPreviewHash:
    def test_a_preview_writes_nothing_and_takes_no_locks(self, db_session, factories):
        world, *_ = _scene(db_session, factories)
        drafts = [*same_drafts(), draft(None, "Армирование", "сетка")]
        before = _all_state(db_session)

        with _capturing_sql(db_session) as statements:
            preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)

        assert _lock_sequence(statements) == []
        assert _all_state(db_session) == before
        assert _events(db_session, "family_schema_edited", world.family.id) == []

    def test_the_hash_depends_on_the_body_and_only_on_it(self, db_session, factories):
        world, *_ = _scene(db_session, factories)
        first = preview_schema_edit(db_session, family_id=world.family.id, drafts=same_drafts())
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=same_drafts())
        other = same_drafts()
        other[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")

        changed = preview_schema_edit(db_session, family_id=world.family.id, drafts=other)

        assert first.preview_hash == again.preview_hash
        assert changed.preview_hash != first.preview_hash

    def test_a_stale_hash_is_refused_and_nothing_changes(self, db_session, factories):
        world, *_ = _scene(db_session, factories)
        before = _all_state(db_session)

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id,
                drafts=[*same_drafts(), draft(None, "Армирование", "сетка")],
                preview_hash="0" * 64, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"
        assert _all_state(db_session) == before

    def test_a_cosmetic_edit_between_the_calls_that_moves_no_number_still_changes_the_hash(
        self, db_session, factories
    ):
        world, *_ = _scene(db_session, factories)
        mine = same_drafts()
        mine[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=mine)
        theirs = same_drafts()
        theirs[1] = draft(2, "МАТЕРИАЛ", "бетон", "кирпич")
        edit(db_session, world.family.id, theirs, _actor(factories))
        before = _all_state(db_session)

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=mine,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"
        assert _all_state(db_session) == before

    def test_a_row_of_an_affected_parameter_changed_between_the_calls(self, db_session, factories):
        world, second, third, fourth, _settled = TestAddingAValueToAnExactParameter()._scene(
            db_session, factories
        )
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "новое", closed=True)
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        # строка с готовым значением сменила значение: числа сводки те же
        _set_row(
            db_session, world.context_id, world.parameters[2],
            value_id=world.values[(2, "кирпич")],
        )
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (again.asked_by_parameter, again.refreshing_contexts) == (
            seen.asked_by_parameter, seen.refreshing_contexts,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_changed_tariffs_change_the_hash(self, db_session, factories, monkeypatch):
        world, *_ = _scene(db_session, factories)
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        monkeypatch.setattr(app_settings, "SEMANTIC_VALUES_PRICE_INPUT_PER_M", Decimal("3"))

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"


# ---------------------------------------------------------------------------
#  Охват правки: одна семья
# ---------------------------------------------------------------------------

def _running_job(db, context_id, schema_id, ordinals):
    """Выполняемое задание значений контекста с отпечатком рендера по `ordinals`."""
    import uuid

    from models import SemanticJob as Job
    from services.variant_request import load_values_material, paths_hash_of, render_values_request
    from services.work_variants import JobGuard

    material = load_values_material(db, [context_id], requested_ordinals=ordinals)[context_id]
    rendered = render_values_request(material, settings=app_settings)
    active = sorted(p.ordinal for p in material.parameters)
    token = uuid.uuid4()
    job = Job(
        kind="context_values", context_id=context_id, schema_id=schema_id,
        paths_hash=paths_hash_of(material.paths),
        requested_ordinals=None if sorted(ordinals) == active else sorted(ordinals),
        request_hash=rendered.request_hash, status="running", claim_token=token,
        prompt_version="1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job, JobGuard(
        job_id=job.id, claim_token=token, expected_request_hash=rendered.request_hash
    )


def _two_partial_needs(db, factories):
    """Два контекста: первому нужен только параметр 1, второму — только 2 (в обоих
    случаях строки другого параметра на месте)."""
    from services.work_variants import JobGuard  # noqa: F401

    world, second, v1, v2 = _scene(db, factories)
    db.execute(
        sa.delete(ContextParameterValue).where(
            sa.or_(
                sa.and_(
                    ContextParameterValue.context_id == world.context_id,
                    ContextParameterValue.parameter_id == world.parameters[1],
                ),
                sa.and_(
                    ContextParameterValue.context_id == second,
                    ContextParameterValue.parameter_id == world.parameters[2],
                ),
            )
        )
    )
    db.flush()
    return world, second


class TestScopeIsOneFamily:
    def test_no_suggestion_job_changes_and_another_family_is_untouched(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        second = _second_context(db_session, factories, world)
        other = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id, second, other.context_id])
        suggestions = _job_hashes(db_session, "family_suggestion")
        assert len(suggestions) == 3
        other_values = {job.id: (job.request_hash, job.status) for job in _values_jobs(
            db_session, context_id=other.context_id
        )}
        assert other_values

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка")], _actor(factories),
        )

        assert _job_hashes(db_session, "family_suggestion") == suggestions
        assert {job.id: (job.request_hash, job.status) for job in _values_jobs(
            db_session, context_id=other.context_id
        )} == other_values

    def test_a_pending_job_with_a_changed_render_is_recomputed_and_an_unchanged_one_is_not(
        self, db_session, factories
    ):
        world, second = _two_partial_needs(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id, second])
        [first_job] = _values_jobs(db_session, context_id=world.context_id)
        [second_job] = _values_jobs(db_session, context_id=second)
        assert (first_job.requested_ordinals, second_job.requested_ordinals) == ([1], [2])
        second_hash = second_job.request_hash
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        db_session.expire_all()
        assert (first_job.status, first_job.cancel_reason) == ("cancelled", "input_changed")
        assert (second_job.status, second_job.request_hash) == ("pending", second_hash)
        replaced = [j for j in _live_values_jobs(db_session, context_id=world.context_id)]
        assert len(replaced) == 1 and replaced[0].request_hash != first_job.request_hash
        assert [j.id for j in _live_values_jobs(db_session, context_id=second)] == [second_job.id]
        assert summary.repriced_pending_jobs == 1

    def test_a_running_job_goes_stale_only_when_its_render_changed(self, db_session, factories):
        world, second = _two_partial_needs(db_session, factories)
        first_job, first_guard = _running_job(db_session, world.context_id, world.schema.id, [1])
        second_job, second_guard = _running_job(db_session, second, world.schema.id, [2])
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")

        edit(db_session, world.family.id, drafts, _actor(factories))

        from tests.integration.test_work_variants_schema_life import _paths_hash

        changed = apply_values(
            db_session, context_id=world.context_id, schema_id=world.schema.id,
            answer=_answer(_named(1, "50 мм")), paths_hash=_paths_hash(db_session, world.context_id),
            guard=first_guard, settings=_settings(),
        )
        kept = apply_values(
            db_session, context_id=second, schema_id=world.schema.id,
            answer=_answer(_named(2, "кирпич", "path")),
            paths_hash=_paths_hash(db_session, second), guard=second_guard, settings=_settings(),
        )
        assert (changed.applied, changed.unapplied_reason) == (False, "stale_fingerprint")
        assert kept.applied is True

    def test_the_jobs_of_an_edit_are_not_held_by_the_event_cap(
        self, db_session, factories, monkeypatch
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка")], _actor(factories),
        )

        assert len(_live_values_jobs(db_session)) == 2
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch)
        ).scalar_one() == 0

    def test_the_same_need_through_the_capped_reconcile_is_held(
        self, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)
        world = _active_world(db_session, factories)

        reconcile_or_defer(db_session, [world.context_id])

        assert _live_values_jobs(db_session) == []
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch)
        ).scalar_one() == 1


# ---------------------------------------------------------------------------
#  Событие и счётчик правки
# ---------------------------------------------------------------------------

class TestEditEvent:
    def test_the_event_carries_the_changes_the_variants_and_the_job_count(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)
        actor = _actor(factories)
        drafts = [draft(1, "ТОЛЩИНА", "50 мм", "100 мм", "150 мм")]

        edit(db_session, world.family.id, drafts, actor)

        [event] = _events(db_session, "family_schema_edited", world.family.id)
        assert set(event.payload) == EVENT_REQUIRED_KEYS["family_schema_edited"]
        assert event.actor_id == actor
        assert event.payload["schema_id"] == world.schema.id
        assert event.payload["revision"] == 1 == _revision(db_session, world.schema.id)
        assert event.payload["variants_before"] == 2 and event.payload["variants_after"] == 1
        assert event.payload["jobs_enqueued"] == 0
        assert event.payload["changes"] == [
            {
                "ordinal": 1, "name": "ТОЛЩИНА",
                "renamed": {"from": "Толщина", "to": "ТОЛЩИНА"},
                "values_added": ["150 мм"],
            },
            {"ordinal": 2, "name": "Материал", "archived": True},
        ]

    def test_a_value_added_event_is_kept_for_each_human_value(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [
                draft(1, "Толщина", "50 мм", "100 мм", "150 мм"), draft(2, "Материал", "бетон", "кирпич"),
                draft(None, "Армирование", "сетка"),
            ],
            _actor(factories),
        )

        events = _events(db_session, "family_schema_value_added", world.family.id)
        assert sorted(e.payload["value"] for e in events) == ["150 мм", "сетка"]
        assert {(e.payload["origin"], e.payload["context_id"]) for e in events} == {
            ("manual", None)
        }
        by_value = {e.payload["value"]: e.payload for e in events}
        assert by_value["150 мм"]["parameter_id"] == world.parameters[1]
        assert by_value["150 мм"]["value_id"] == _values(db_session, world.parameters[1])[
            "150 мм"
        ].id

    def test_the_values_built_for_a_range_are_not_value_added_events(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(120, 200, 40))],
            _actor(factories),
        )

        assert _events(db_session, "family_schema_value_added", world.family.id) == []
        [event] = _events(db_session, "family_schema_edited", world.family.id)
        [added] = event.payload["changes"]
        assert added["added"] is True and added["value_kind"] == "range"
        assert added["range"] == {"start": "120", "stop": "200", "step": "40"}

    def test_revision_grows_by_one_for_an_edit_with_many_changes(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [
                draft(1, "ТОЛЩИНА", "50 мм", "100 мм", "150 мм"),
                draft(None, "Армирование", "сетка", "без"),
            ],
            _actor(factories),
        )

        assert _revision(db_session, world.schema.id) == 1

    def test_the_job_count_of_the_event_is_the_number_of_jobs_the_edit_queued(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка")], _actor(factories),
        )

        [event] = _events(db_session, "family_schema_edited", world.family.id)
        assert event.payload["jobs_enqueued"] == 2


# ---------------------------------------------------------------------------
#  Порядок блокировок
# ---------------------------------------------------------------------------

class TestLockOrder:
    def test_the_locks_follow_the_module_order(self, db_session, factories):
        world, second, third, fourth, _settled = TestAddingAValueToAnExactParameter()._scene(
            db_session, factories
        )
        fifth = _second_context(db_session, factories, world)
        reconcile_or_defer(db_session, [fifth])
        assert _live_values_jobs(db_session, context_id=fifth)
        drafts = [draft(1, "ТОЛЩИНА", "50 мм", "100 мм", "150 мм")]
        summary = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)

        with _capturing_sql(db_session) as statements:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=summary.preview_hash, actor_id=_actor(factories),
            )

        first_seen: list[tuple[str, str]] = []
        for lock in _lock_sequence(statements):
            if lock not in first_seen:
                first_seen.append(lock)
        order = [
            "work_families", "family_parameter_schemas", "family_parameters",
            "family_parameter_values", "catalog_contexts", "work_variants", "semantic_jobs",
        ]
        tables = [table for table, _mode in first_seen]
        assert [mode for _table, mode in first_seen] == ["UPDATE"] * len(first_seen)
        assert tables == [table for table in order if table in tables]
        assert tables[:3] == order[:3] and tables[-1] == "semantic_jobs"
        assert set(tables) == set(order)


# ---------------------------------------------------------------------------
#  Маршруты: edit-preview и PATCH
# ---------------------------------------------------------------------------

def _body(*parameters, preview_hash=None):
    """Тело маршрута: параметры словарями (`ordinal` может быть `None`)."""
    body = {"parameters": list(parameters)}
    if preview_hash is not None:
        body["preview_hash"] = preview_hash
    return body


def _exact(ordinal, name, *values, role="main", closed=False):
    return {
        "ordinal": ordinal, "name": name, "role": role, "value_kind": "exact",
        "closed": closed, "values": list(values),
    }


def _ranged(name, start, stop, step, *, ordinal=None):
    return {
        "ordinal": ordinal, "name": name, "role": "main", "value_kind": "range",
        "closed": True, "range": {"start": start, "stop": stop, "step": step},
    }


_SAME = (_exact(1, "Толщина", "50 мм", "100 мм"), _exact(2, "Материал", "бетон", "кирпич"))


def _preview(client, family_id, *parameters):
    return client.post(
        f"{BASE}/families/{family_id}/schema/edit-preview", json=_body(*parameters)
    )


class TestEditRoutes:
    def test_the_preview_answers_with_the_summary(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = _preview(
            admin_client, scene.family.id, *_SAME, _exact(None, "Армирование", "сетка")
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "variants_before", "variants_after_now", "variants_pending_answers",
            "asked_by_parameter", "reserve_usd", "expected_cached_usd", "value_moves",
            "retired", "refreshing_contexts", "repriced_pending_jobs", "preview_hash",
        }
        assert isinstance(body["reserve_usd"], str) and isinstance(body["preview_hash"], str)
        assert body["value_moves"] == [] and body["retired"] == []
        # контексты семьи сцены без вариантов: им нужны все три параметра
        assert body["asked_by_parameter"] == [
            {"ordinal": 1, "name": "Толщина", "contexts": len(scene.context_ids)},
            {"ordinal": 2, "name": "Материал", "contexts": len(scene.context_ids)},
            {"ordinal": 3, "name": "Армирование", "contexts": len(scene.context_ids)},
        ]

    def test_the_preview_does_not_write(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        before = _all_state(db_session)

        response = _preview(admin_client, scene.family.id, *_SAME, _exact(None, "Новый", "а"))

        assert response.status_code == 200
        assert _all_state(db_session) == before

    def test_the_patch_goes_through_the_apply_and_answers_with_the_schema(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        parameters = (*_SAME, _exact(None, "Армирование", "сетка"))
        preview_hash = _preview(admin_client, scene.family.id, *parameters).json()["preview_hash"]

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash=preview_hash),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["version"]) == ("frozen", 1)
        assert [p["name"] for p in body["parameters"]] == ["Толщина", "Материал", "Армирование"]
        [event] = _events(db_session, "family_schema_edited", scene.family.id)
        assert event.payload["revision"] == 1
        assert _revision(db_session, scene.schema.id) == 1

    def test_a_patch_without_the_preview_hash_is_422(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema", json=_body(*_SAME)
        )

        assert response.status_code == 422, response.text

    def test_a_stale_hash_is_409_and_nothing_changes(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameters = (*_SAME, _exact(None, "Армирование", "сетка"))
        stale = _preview(admin_client, scene.family.id, *parameters).json()["preview_hash"]
        other = (_exact(1, "ТОЛЩИНА", "50 мм", "100 мм"), _SAME[1])
        saved = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(
                *other,
                preview_hash=_preview(admin_client, scene.family.id, *other).json()["preview_hash"],
            ),
        )
        assert saved.status_code == 200, saved.text
        before = _all_state(db_session)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash=stale),
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "preview_changed"
        assert _all_state(db_session) == before

    def test_the_new_codes_answer_with_their_status(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        cases = [
            ((*_SAME, *(_exact(None, f"Н{i}", "а") for i in range(9))),
             422, "schema_too_many_parameters"),
            ((*_SAME, _exact(None, " ТОЛЩИНА ", "а")), 422, "schema_duplicate_parameter"),
            ((*_SAME, _ranged("Диаметр", "10", "10", "2")), 422, "schema_bad_range"),
            ((*_SAME, _ranged("Диаметр", "0", "29", "1")), 422, "schema_range_too_many_values"),
        ]
        for parameters, status, code in cases:
            for response in (
                _preview(admin_client, scene.family.id, *parameters),
                admin_client.patch(
                    f"{BASE}/families/{scene.family.id}/schema",
                    json=_body(*parameters, preview_hash="x"),
                ),
            ):
                assert response.status_code == status, (code, response.text)
                assert _detail(response)["code"] == code

    def test_exhausted_ordinals_are_409(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        archived = _param(db_session, scene.schema, 100, "Архивный")
        archived.archived_at = sa.func.now()
        db_session.commit()

        response = _preview(admin_client, scene.family.id, *_SAME, _exact(None, "Новый", "а"))

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_ordinals_exhausted"

    def test_range_numbers_may_be_strings_or_numbers(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.commit()

        as_text = _preview(
            admin_client, scene.family.id, *_SAME, _ranged("Диаметр", "120", "200", "40")
        )
        as_numbers = _preview(
            admin_client, scene.family.id, *_SAME, _ranged("Диаметр", 120, 200.0, 40)
        )

        assert as_text.status_code == as_numbers.status_code == 200, (
            as_text.text, as_numbers.text,
        )
        assert as_text.json()["preview_hash"] == as_numbers.json()["preview_hash"]

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "abc", ""])
    def test_a_range_number_that_is_not_finite_is_refused_with_the_code(
        self, admin_client, db_session, factories, bad
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()

        response = _preview(
            admin_client, scene.family.id, *_SAME, _ranged("Диаметр", bad, "200", "40")
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == "schema_bad_range"

    def test_an_unknown_role_is_a_validation_error(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = _preview(
            admin_client, scene.family.id, *_SAME, _exact(None, "Новый", "а", role="third")
        )

        assert response.status_code == 422, response.text

    def test_a_preview_of_a_family_with_a_building_version_is_409(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        _building(db_session, scene.family)

        response = _preview(admin_client, scene.family.id, *_SAME)

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_building"

    def test_a_preview_of_an_unknown_family_is_404(self, admin_client):
        response = _preview(admin_client, 999999999, *_SAME)

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"

    def test_a_preview_of_a_family_without_a_version_is_409(self, admin_client, db_session):
        family = _schemaless_family(db_session)

        response = _preview(admin_client, family.id, _exact(None, "Новый", "а"))

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_no_current"

    def test_a_member_is_rejected_by_both_routes(self, member_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        preview = _preview(member_client, scene.family.id, *_SAME)
        patch = member_client.patch(
            f"{BASE}/families/{scene.family.id}/schema", json=_body(*_SAME, preview_hash="x")
        )

        assert (preview.status_code, patch.status_code) == (403, 403)


# ---------------------------------------------------------------------------
#  Гонки: две сессии с настоящими commit-ами
# ---------------------------------------------------------------------------

def _is_revision_read(statement: str) -> bool:
    """Чтение `revision` версии под замками правки: ответ уже у клиента."""
    return " ".join(statement.split()).startswith(
        "SELECT family_parameter_schemas.revision FROM family_parameter_schemas"
    )


def _committed_world(db, factories, params=PARAMS):
    world = _active_world(db, factories, params)
    actor = factories.UserFactory.create().id
    db.commit()
    return world, actor


def _apply_code(family_id, drafts, preview_hash, actor):
    """Работа потока: сохранение правки; отказ возвращается кодом."""

    def work(db):
        try:
            apply_schema_edit(
                db, family_id=family_id, drafts=drafts, preview_hash=preview_hash, actor_id=actor
            )
        except WorkFamilyError as exc:
            db.rollback()
            return exc.code
        return "ok"

    return work


def _cosmetic(name):
    drafts = same_drafts()
    drafts[0] = draft(1, name, *drafts[0].values)
    return drafts


class TestTwoEditsWithOneHash:
    def test_the_second_waits_for_the_first_and_is_refused(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        world, actor = _committed_world(db, committing_factories)
        family_id = world.family.id
        drafts = _cosmetic("ТОЛЩИНА")
        shared = preview_schema_edit(db, family_id=family_id, drafts=drafts).preview_hash
        db.commit()
        scene = _Scene(committing_session_factory)

        try:
            scene.spawn("a", _apply_code(family_id, drafts, shared, actor), pause_on=_is_revision_read)
            assert scene.wait_paused("a"), "A не дошла до чтения revision под замками"
            scene.spawn("b", _apply_code(family_id, drafts, shared, actor))
            settled = scene.wait_settled("b", contains="FOR UPDATE")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        # Без защиты B читает revision, пока A держит правку незавершённой, и
        # сохраняет то же тело поверх неё; с защитой B встаёт на замке семьи, а
        # после commit A видит другой revision.
        assert settled == "blocked", f"B не встала на замке, пока A держит правку: {settled}"
        assert scene.results == {"a": "ok", "b": "preview_changed"}
        with committing_session_factory() as probe:
            assert _revision(probe, world.schema.id) == 1
            assert _parameter(probe, world.schema.id, 1).name == "ТОЛЩИНА"


class TestEditVersusARecordedAnswer:
    def test_the_answer_waits_for_the_edit_and_then_goes_stale(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        world, actor = _committed_world(db, committing_factories)
        job, guard = _guarded(db, context_id=world.context_id, schema_id=world.schema.id)
        drafts = _cosmetic("ТОЛЩИНА")
        shared = preview_schema_edit(db, family_id=world.family.id, drafts=drafts).preview_hash
        db.commit()
        job_id, context_id, schema_id = job.id, world.context_id, world.schema.id
        scene = _Scene(committing_session_factory)

        def record_answer(session):
            return apply_values(
                session, context_id=context_id, schema_id=schema_id,
                answer=_answer(_named(1, "50 мм"), _named(2, "бетон", "path")),
                paths_hash="paths-b", guard=guard, settings=_settings(),
            )

        try:
            scene.spawn(
                "a", _apply_code(world.family.id, drafts, shared, actor), pause_on=_is_revision_read
            )
            assert scene.wait_paused("a"), "правка не дошла до чтения revision"
            scene.spawn("b", record_answer)
            settled = scene.wait_settled("b", contains="FOR ")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        # Без замков правки запись проходит под ней: её отпечаток ещё прежний.
        assert settled == "blocked", f"запись ответа не встала на замке правки: {settled}"
        outcome = scene.results["b"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert scene.results["a"] == "ok"
        with committing_session_factory() as probe:
            row = probe.get(SemanticJob, job_id)
            assert (row.status, row.cancel_reason) == ("cancelled", "input_changed")


class TestParallelEditsOverTheLimit:
    def test_two_edits_of_six_new_parameters_leave_exactly_one(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        world, actor = _committed_world(db, committing_factories, params=())
        family_id = world.family.id
        mine = [draft(None, f"Первый {i}", "а") for i in range(6)]
        theirs = [draft(None, f"Второй {i}", "а") for i in range(6)]
        hash_a = preview_schema_edit(db, family_id=family_id, drafts=mine).preview_hash
        hash_b = preview_schema_edit(db, family_id=family_id, drafts=theirs).preview_hash
        db.commit()
        scene = _Scene(committing_session_factory)

        try:
            scene.spawn("a", _apply_code(family_id, mine, hash_a, actor), pause_on=_is_revision_read)
            assert scene.wait_paused("a"), "A не дошла до чтения revision"
            scene.spawn("b", _apply_code(family_id, theirs, hash_b, actor))
            settled = scene.wait_settled("b", contains="FOR UPDATE")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"B не встала на замке: {settled}"
        assert sorted(scene.results.values()) == ["ok", "preview_changed"]
        with committing_session_factory() as probe:
            active = probe.execute(
                sa.select(sa.func.count()).select_from(FamilyParameter).where(
                    FamilyParameter.schema_id == world.schema.id,
                    FamilyParameter.archived_at.is_(None),
                )
            ).scalar_one()
        assert active == 6


class TestEditVersusMergeOfValues:
    def test_the_merge_waits_for_the_edit_and_both_are_kept(
        self, committing_db, committing_factories, committing_session_factory
    ):
        from services.work_variants import merge_parameter_values

        db = committing_db
        world, actor = _committed_world(db, committing_factories)
        family_id = world.family.id
        drafts = _cosmetic("ТОЛЩИНА")
        shared = preview_schema_edit(db, family_id=family_id, drafts=drafts).preview_hash
        parameter, source, target = (
            world.parameters[1], world.values[(1, "50 мм")], world.values[(1, "100 мм")],
        )
        db.commit()
        scene = _Scene(committing_session_factory)

        def merge(session):
            merge_parameter_values(
                session, parameter_id=parameter, source_value_id=source,
                target_value_id=target, actor_id=actor,
            )
            return "ok"

        try:
            scene.spawn("a", _apply_code(family_id, drafts, shared, actor), pause_on=_is_revision_read)
            assert scene.wait_paused("a"), "правка не дошла до чтения revision"
            scene.spawn("b", merge)
            settled = scene.wait_settled("b", contains="FOR UPDATE")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"слияние не встало на замке правки: {settled}"
        assert scene.results == {"a": "ok", "b": "ok"}
        with committing_session_factory() as probe:
            assert _revision(probe, world.schema.id) == 2


# ---------------------------------------------------------------------------
#  Сводка, хэш, охват и событие ведут себя как сохранение: отдельный вход на
#  каждое решение
# ---------------------------------------------------------------------------

class TestMadeMainBeforeTheAnswer:
    """Строка таблицы §2.5 «сменить роль»: контексты без значения параметра — в
    прежнем варианте до ответа, новых запросов нет."""

    def test_contexts_without_the_value_keep_their_variant_and_nothing_is_asked(
        self, db_session, factories
    ):
        world, second, v1, v2 = _scene(db_session, factories)
        actor = _actor(factories)
        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", role="secondary")], actor,
        )
        waiting = {job.id: job.request_hash for job in _live_values_jobs(db_session)}
        assert len(waiting) == 2

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(3, "Армирование", "сетка", role="main")], actor,
        )

        assert _parameter(db_session, world.schema.id, 3).role == "main"
        assert _variants_of(db_session, [world.context_id, second]) == {
            world.context_id: v1, second: v2,
        }
        assert (
            summary.variants_before, summary.variants_after_now, summary.variants_pending_answers,
        ) == (2, 2, True)
        assert summary.asked_by_parameter == () and summary.repriced_pending_jobs == 0
        # роль в рендер не входит: ждущие задания по параметру 3 остались прежними
        assert {job.id: job.request_hash for job in _live_values_jobs(db_session)} == waiting


class TestProjectionFollowsTheRecompute:
    """Сводка считает варианты так же, как пересчёт: цепочка слияния и значение,
    возвращённое из истории строке «не из списка»."""

    def test_a_key_that_lands_on_a_merged_variant_is_followed_to_its_target(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        concrete, brick = world.values[(2, "бетон")], world.values[(2, "кирпич")]
        target = _variant(
            db_session, world.family, world.schema, values_key=f"2={brick}", status="archived"
        )
        _variant(
            db_session, world.family, world.schema, values_key=f"2={concrete}",
            status="archived", merged_into_id=target.id,
        )
        drafts = same_drafts()
        drafts[0] = draft(1, "Толщина", "50 мм", "100 мм", role="secondary")

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        assert _variant_of(db_session, world.context_id) == _variant_of(db_session, second) == (
            target.id
        )
        assert (summary.variants_before, summary.variants_after_now) == (
            2, _active_variants(db_session, world.family.id),
        ) == (2, 1)

    def test_an_off_list_row_resolved_to_a_revived_value_joins_the_variant_of_that_value(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        waiting = _second_context(db_session, factories, world)
        arriving = _second_context(db_session, factories, world)
        material = world.parameters[2]
        aerated = FamilyParameterValue(
            parameter_id=material, value="газобетон", value_norm="газобетон", origin="schema"
        )
        db_session.add(aerated)
        db_session.flush()
        kept = _settle_context(
            db_session, world, waiting, _answer(_named(1, "50 мм"), _named(2, "газобетон", "path"))
        )
        brick = _settle_context(
            db_session, world, arriving, _answer(_named(1, "50 мм"), _named(2, "кирпич", "path"))
        )
        # значение выведено: строка, что на него ссылается, ждёт перезапроса в прежнем
        # варианте; у второй строки тот же текст стал значением «не из списка»
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == aerated.id)
            .values(retired_at=sa.func.now())
        )
        _set_row(db_session, waiting, material, refresh_requested_at=sa.func.now())
        _set_row(
            db_session, arriving, material, value_id=None, source="off_list",
            off_list_text="Газобетон",
        )
        _update_parameter(db_session, material, closed=True)
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "Газобетон", closed=True)

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        assert _context_row(db_session, arriving, material).value_id == aerated.id
        assert _variant_of(db_session, arriving) == _variant_of(db_session, waiting) == kept
        assert _variant_row(db_session, brick).status == "archived"
        assert (summary.variants_before, summary.variants_after_now) == (
            2, _active_variants(db_session, world.family.id),
        ) == (2, 1)


class TestPreviewSeesWhatTheSaveDoes:
    """Предпросмотр: те же запросы, тот же охват и те же строки, что у сохранения."""

    def test_the_requests_of_the_preview_are_the_jobs_of_the_save(
        self, db_session, factories, monkeypatch
    ):
        world, *_rest = TestAddingAValueToAnExactParameter()._scene(db_session, factories)
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "новое", closed=True)
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
            preview_hash=summary.preview_hash, actor_id=_actor(factories),
        )

        queued = {(job.context_id, job.request_hash) for job in _live_values_jobs(db_session)}
        assert queued
        assert {(fp.context_id, fp.request_hash) for fp, _r, _o, _f in previewed.entries} == queued

    def test_a_context_with_only_a_variant_due_is_not_counted_as_asked(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        _set_pending(db_session, factories, second, world.family)

        summary = edit(db_session, world.family.id, _cosmetic("ТОЛЩИНА"), _actor(factories))

        assert [job.context_id for job in _live_values_jobs(db_session)] == [second]
        assert summary.asked_by_parameter == ()

    def test_the_values_of_a_new_parameter_are_deduplicated_by_norm(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", " СЕТКА ", "без")],
            _actor(factories),
        )

        assert list(_values(db_session, _parameter(db_session, world.schema.id, 3).id)) == [
            "сетка", "без",
        ]

    def test_an_archived_context_is_neither_marked_nor_counted(self, db_session, factories):
        world, second, third, fourth, _settled = TestAddingAValueToAnExactParameter()._scene(
            db_session, factories
        )
        db_session.get(CatalogContext, second).archived_at = sa.func.now()
        db_session.flush()
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", "новое", closed=True)

        summary = edit(db_session, world.family.id, drafts, _actor(factories))

        assert _context_row(db_session, second, world.parameters[2]).refresh_requested_at is None
        for context_id in (third, fourth):
            assert _context_row(db_session, context_id, world.parameters[2]).refresh_requested_at
        assert summary.refreshing_contexts == 2

    def test_a_context_arriving_in_the_family_is_in_the_scope(self, db_session, factories):
        world = _active_world(db_session, factories)
        other = _active_world(db_session, factories)
        _set_pending(db_session, factories, other.context_id, world.family)
        reconcile_or_defer(db_session, [other.context_id])
        [arriving] = _live_values_jobs(db_session, context_id=other.context_id)
        assert arriving.schema_id == world.schema.id

        summary = edit(db_session, world.family.id, _cosmetic("ТОЛЩИНА"), _actor(factories))

        db_session.expire_all()
        assert (arriving.status, arriving.cancel_reason) == ("cancelled", "input_changed")
        [replaced] = _live_values_jobs(db_session, context_id=other.context_id)
        assert replaced.request_hash != arriving.request_hash
        assert summary.repriced_pending_jobs == 1

    def test_rows_of_a_previous_version_are_not_known_rows(self, db_session, factories):
        world, second, _v1, _v2 = _scene(db_session, factories)
        actor = _actor(factories)
        building = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)
        freeze_schema(
            db_session, schema_id=building.id, guard=None, settings=_settings(),
            answer=_schema_answer(
                _parameter_in(1, "Толщина", "50 мм", "100 мм"),
                _parameter_in(2, "Материал", "бетон", "кирпич"),
            ),
        )
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", role="secondary")], actor,
        )

        # строк новой версии у контекстов нет: спрашиваются все три параметра
        assert summary.asked_by_parameter == (
            (1, "Толщина", 2), (2, "Материал", 2), (3, "Армирование", 2),
        )
        _assert_summary_is_the_outcome(
            db_session, world.family.id, building.id, summary, waiting_before, marked_before
        )


class TestEachPartOfTheHash:
    """Составляющие `preview_hash`, которые не сдвигают других составляющих."""

    def test_a_moved_number_alone_changes_the_hash(self, db_session, factories):
        world, second, _v1, _v2 = _scene(db_session, factories)
        drafts = same_drafts()[:1]
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (seen.variants_before, seen.variants_after_now) == (2, 1)
        # строка главного параметра, который правка не трогает, сменила значение
        _set_row(db_session, second, world.parameters[1], value_id=world.values[(1, "100 мм")])
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (again.variants_after_now, again.asked_by_parameter) == (2, ())

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_a_changed_request_alone_changes_the_hash(self, db_session, factories):
        world = _active_world(db_session, factories)
        drafts = [*same_drafts(), draft(None, "Армирование", "сетка", role="secondary")]
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        # наименование строки каталога сменилось на одну букву той же длины: тело
        # запроса значений другое, схема, строки и числа сводки (и резерв) те же
        position = db_session.get(CatalogPosition, world.catalog_id)
        last = "x" if not position.standard_job_title.endswith("x") else "y"
        position.standard_job_title = position.standard_job_title[:-1] + last
        db_session.flush()
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (again.asked_by_parameter, again.reserve_usd) == (
            seen.asked_by_parameter, seen.reserve_usd,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_the_reserve_formula_version_changes_the_hash(
        self, db_session, factories, monkeypatch
    ):
        world, *_rest = _scene(db_session, factories)
        drafts = _cosmetic("ТОЛЩИНА")
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        monkeypatch.setattr(schema_edit_module, "RESERVE_FORMULA_VERSION", "next")

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_the_hash_of_another_family_is_refused(self, db_session, factories):
        first, *_rest = _scene(db_session, factories)
        other, *_more = _scene(db_session, factories)
        # тело без изменений — ни один параметр не тронут, строки в хэш не входят
        # (переименование добавило бы строки чужих контекстов и замаскировало семью)
        drafts = same_drafts()
        seen = preview_schema_edit(db_session, family_id=first.family.id, drafts=drafts)
        theirs = preview_schema_edit(db_session, family_id=other.family.id, drafts=drafts)
        assert (theirs.variants_before, theirs.asked_by_parameter) == (
            seen.variants_before, seen.asked_by_parameter,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=other.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"


class TestEventAndLocksMore:
    def test_a_job_returned_to_the_queue_counts_as_enqueued(self, db_session, factories):
        world = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id])
        [first_job] = _values_jobs(db_session)
        actor = _actor(factories)
        edit(db_session, world.family.id, _cosmetic("ТОЛЩИНА"), actor)

        edit(db_session, world.family.id, same_drafts(), actor)

        statuses = {job.id: job.status for job in _values_jobs(db_session)}
        assert statuses[first_job.id] == "pending" and len(statuses) == 2
        assert [
            event.payload["jobs_enqueued"]
            for event in _events(db_session, "family_schema_edited", world.family.id)
        ] == [1, 1]

    def test_an_edit_that_marks_rows_locks_their_contexts_before_the_jobs(
        self, db_session, factories
    ):
        world, *_rest = TestAddingAValueToAnExactParameter()._scene(db_session, factories)
        _update_parameter(db_session, world.parameters[2], role="secondary")
        recompute_variants(db_session, family_id=world.family.id, schema_id=world.schema.id)
        drafts = same_drafts()
        drafts[1] = draft(
            2, "Материал", "бетон", "кирпич", "новое", role="secondary", closed=True
        )
        summary = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert summary.refreshing_contexts > 0

        with _capturing_sql(db_session) as statements:
            apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=summary.preview_hash, actor_id=_actor(factories),
            )

        tables: list[str] = []
        for table, _mode in _lock_sequence(statements):
            if table not in tables:
                tables.append(table)
        # пересчёта нет (главные не менялись): замок контекстов — от строк правки,
        # после параметров и значений
        assert "work_variants" not in tables
        assert tables[-1] == "catalog_contexts"
        assert tables.index("family_parameter_values") < tables.index("catalog_contexts")


class TestRouteDefaults:
    def test_omitted_fields_mean_a_main_exact_open_parameter(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        parameters = (*_SAME, {"name": "Армирование", "values": ["сетка"]})
        preview = _preview(admin_client, scene.family.id, *parameters)
        assert preview.status_code == 200, preview.text

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash=preview.json()["preview_hash"]),
        )

        assert response.status_code == 200, response.text
        row = _parameter(db_session, scene.schema.id, 3)
        assert (row.role, row.value_kind, row.closed) == ("main", "exact", False)

    def test_a_non_finite_range_number_is_refused_even_where_the_range_is_unused(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        parameter = {
            **_exact(None, "Армирование", "сетка"),
            "range": {"start": "NaN", "stop": "1", "step": "1"},
        }

        response = _preview(admin_client, scene.family.id, *_SAME, parameter)

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == "schema_bad_range"

    def test_the_summary_form_spells_out_moves_retired_values_and_money(self):
        summary = EditSummary(
            variants_before=1, variants_after_now=2, variants_pending_answers=True,
            asked_by_parameter=((3, "Диаметр", 4),), reserve_usd=Decimal("1E-7"),
            expected_cached_usd=Decimal("0.50"),
            value_moves=(("Толщина", "4–6", "4–8", 3),), retired=(("Толщина", "6–8", 2),),
            refreshing_contexts=5, repriced_pending_jobs=6, preview_hash="h",
        )

        assert edit_summary_out(summary) == {
            "variants_before": 1, "variants_after_now": 2, "variants_pending_answers": True,
            "asked_by_parameter": [{"ordinal": 3, "name": "Диаметр", "contexts": 4}],
            "reserve_usd": "0.0000001", "expected_cached_usd": "0.50",
            "value_moves": [
                {"parameter": "Толщина", "from_value": "4–6", "to_value": "4–8", "contexts": 3}
            ],
            "retired": [{"parameter": "Толщина", "value": "6–8", "contexts": 2}],
            "refreshing_contexts": 5, "repriced_pending_jobs": 6, "preview_hash": "h",
        }


class TestQueueStatesInTheSummary:
    """Ждущие задания разных состояний и неприменимый контекст семьи: сводка
    считает как сверка после сохранения."""

    def test_an_inapplicable_context_is_neither_asked_nor_repriced(self, db_session, factories):
        world = _active_world(db_session, factories)
        second = _second_context(db_session, factories, world)
        reconcile_or_defer(db_session, [world.context_id, second])
        assert len(_live_values_jobs(db_session)) == 2
        catalog_id = db_session.execute(
            sa.select(ContextBucket.catalog_position_id)
            .join(CatalogContext, CatalogContext.bucket_id == ContextBucket.id)
            .where(CatalogContext.id == second)
        ).scalar_one()
        db_session.get(CatalogPosition, catalog_id).kind = "HEADER"
        db_session.flush()
        waiting_before = _waiting_ids(db_session)
        marked_before = _marked_contexts_of(db_session, world.family.id)

        summary = edit(
            db_session, world.family.id,
            [*same_drafts(), draft(None, "Армирование", "сетка", role="secondary")],
            _actor(factories),
        )

        [gone] = _values_jobs(db_session, context_id=second)
        assert (gone.status, gone.cancel_reason) == ("cancelled", "not_applicable")
        assert summary.repriced_pending_jobs == 1
        assert summary.asked_by_parameter == (
            (1, "Толщина", 1), (2, "Материал", 1), (3, "Армирование", 1),
        )
        _assert_summary_is_the_outcome(
            db_session, world.family.id, world.schema.id, summary, waiting_before, marked_before
        )

    def test_a_held_job_whose_request_changed_is_counted_as_repriced(self, db_session, factories):
        world = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id])
        [held] = _values_jobs(db_session)
        held.status = "privacy_hold"
        held.privacy_matches = [{"match": "x"}]
        db_session.flush()

        summary = edit(db_session, world.family.id, _cosmetic("ТОЛЩИНА"), _actor(factories))

        db_session.expire_all()
        assert (held.status, held.cancel_reason) == ("cancelled", "stale_hold")
        assert summary.repriced_pending_jobs == 1

    def test_only_the_waiting_flag_moved_and_the_hash_changed(self, db_session, factories):
        world, second, _v1, _v2 = _scene(db_session, factories)
        third = _second_context(db_session, factories, world)
        _settle_context(
            db_session, world, third, _answer(_named(1, "100 мм"), _named(2, "бетон", "path"))
        )
        # строка главного параметра третьего контекста ждёт ответа; задание по ней
        # уже выполняется, поэтому сверка ничего не ставит ни до, ни после
        _set_row(db_session, third, world.parameters[1], refresh_requested_at=sa.func.now())
        _running_job(db_session, third, world.schema.id, [1])
        drafts = same_drafts()[:1]
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (seen.variants_after_now, seen.variants_pending_answers) == (2, True)
        # ответ записан мимо правки: отметки нет, число вариантов то же
        _set_row(db_session, third, world.parameters[1], refresh_requested_at=None)
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert (again.variants_after_now, again.variants_pending_answers) == (2, False)
        assert (again.asked_by_parameter, again.repriced_pending_jobs) == (
            seen.asked_by_parameter, seen.repriced_pending_jobs,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"


# ---------------------------------------------------------------------------
#  Диапазон на границе точности, хэш строк тронутых параметров,
#  проекция пересчёта против настоящего пересчёта
# ---------------------------------------------------------------------------

class TestHugeRange:
    @pytest.mark.parametrize(
        ("rng", "code"),
        [
            pytest.param(
                ("0", "999999999", "0.000001"), "schema_range_too_many_values", id="quotient"
            ),
            pytest.param(("0", "1e30", "0.001"), "schema_bad_range", id="beyond-the-limit"),
            pytest.param(("0", "1e999999999", "1"), "schema_bad_range", id="overflow"),
        ],
    )
    def test_the_route_refuses_a_range_too_big_to_divide(
        self, admin_client, db_session, factories, rng, code
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        before = _all_state(db_session)
        parameters = (*_SAME, _ranged("Диаметр", *rng))

        preview = _preview(admin_client, scene.family.id, *parameters)
        patch = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash="x"),
        )

        for response in (preview, patch):
            assert response.status_code == 422, response.text
            assert _detail(response)["code"] == code
        assert _all_state(db_session) == before


class TestHashRowsOfTouchedParameters:
    def _roles(self):
        drafts = same_drafts()
        drafts[1] = draft(2, "Материал", "бетон", "кирпич", role="secondary")
        return drafts

    def test_a_row_of_a_role_changed_parameter_changed_between_the_calls(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=self._roles())
        _set_row(db_session, second, world.parameters[2], source="name")
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=self._roles())
        assert again.asked_by_parameter == seen.asked_by_parameter
        assert (again.variants_before, again.variants_after_now) == (
            seen.variants_before, seen.variants_after_now,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=self._roles(),
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_a_row_of_an_archived_parameter_changed_between_the_calls(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        drafts = same_drafts()[:1]
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        _set_row(db_session, second, world.parameters[2], source="name")

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"

    def test_a_row_of_an_untouched_parameter_changed_between_the_calls_passes(
        self, db_session, factories
    ):
        world, second, _v1, _v2 = _scene(db_session, factories)
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=self._roles())
        _set_row(db_session, second, world.parameters[1], source="path")

        apply_schema_edit(
            db_session, family_id=world.family.id, drafts=self._roles(),
            preview_hash=seen.preview_hash, actor_id=_actor(factories),
        )

        assert _parameter(db_session, world.schema.id, 2).role == "secondary"


class TestProjectionEqualsTheRecompute:
    def test_the_projection_of_an_unchanged_state_moves_nothing_like_the_recompute(
        self, db_session, factories
    ):
        from services.work_variants import (
            answered_main_rows,
            project_recompute,
            recompute_candidates,
            recompute_variants,
        )

        world, second, _v1, _v2 = _scene(db_session, factories)
        previous_of = recompute_candidates(
            db_session, family_id=world.family.id, schema_id=world.schema.id
        )
        ordinal_of = {world.parameters[1]: 1, world.parameters[2]: 2}
        answered = {
            context_id: {ordinal_of[pid]: vid for pid, vid in rows.items()}
            for context_id, rows in answered_main_rows(
                db_session, list(previous_of), list(ordinal_of)
            ).items()
        }

        projected = project_recompute(
            db_session, schema_id=world.schema.id, previous_of=previous_of,
            main_ordinals={1, 2}, answered=answered,
        )
        actual = recompute_variants(
            db_session, family_id=world.family.id, schema_id=world.schema.id
        )

        assert projected == actual
        assert actual.moved_contexts == 0


class TestRevisionAndBodyAlone:
    """`revision` и тело в хэше — каждая составляющая своим входом: обе стороны
    трогают один и тот же параметр, поэтому строки тронутых параметров в хэше
    одинаковы и различия не дают."""

    def test_a_parallel_rename_of_the_same_parameter_is_seen_by_the_revision_alone(
        self, db_session, factories
    ):
        world, *_ = _scene(db_session, factories)
        mine = same_drafts()
        mine[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=mine)
        theirs = same_drafts()
        theirs[0] = draft(1, "толщина", "50 мм", "100 мм")
        edit(db_session, world.family.id, theirs, _actor(factories))
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=mine)
        assert (
            again.variants_before, again.variants_after_now, again.asked_by_parameter,
            again.refreshing_contexts, again.repriced_pending_jobs,
        ) == (
            seen.variants_before, seen.variants_after_now, seen.asked_by_parameter,
            seen.refreshing_contexts, seen.repriced_pending_jobs,
        )
        before = _all_state(db_session)

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=mine,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"
        assert _all_state(db_session) == before

    def test_two_renames_of_one_parameter_differ_by_the_body_alone(self, db_session, factories):
        world, *_ = _scene(db_session, factories)
        upper = same_drafts()
        upper[0] = draft(1, "ТОЛЩИНА", "50 мм", "100 мм")
        lower = same_drafts()
        lower[0] = draft(1, "толщина", "50 мм", "100 мм")

        a = preview_schema_edit(db_session, family_id=world.family.id, drafts=upper)
        b = preview_schema_edit(db_session, family_id=world.family.id, drafts=lower)

        assert (a.variants_after_now, a.asked_by_parameter, a.refreshing_contexts) == (
            b.variants_after_now, b.asked_by_parameter, b.refreshing_contexts,
        )
        assert a.preview_hash != b.preview_hash


class TestRepricedNumberAlone:
    """Снятые ждущие задания входят в хэш только числом «пересчитает»: ждущее задание взято исполнителем
    между вызовами, остальные числа и план заданий те же."""

    def test_a_waiting_job_claimed_between_the_calls_changes_the_hash(self, db_session, factories):
        import uuid

        world = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id])
        [job] = _values_jobs(db_session)
        drafts = _cosmetic("ТОЛЩИНА")
        seen = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert seen.repriced_pending_jobs == 1
        job.status = "running"
        job.claim_token = uuid.uuid4()
        db_session.flush()
        again = preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
        assert again.repriced_pending_jobs == 0
        assert (
            again.variants_before, again.variants_after_now, again.variants_pending_answers,
            again.asked_by_parameter, again.reserve_usd, again.expected_cached_usd,
            again.refreshing_contexts,
        ) == (
            seen.variants_before, seen.variants_after_now, seen.variants_pending_answers,
            seen.asked_by_parameter, seen.reserve_usd, seen.expected_cached_usd,
            seen.refreshing_contexts,
        )

        code = _refusal(
            lambda: apply_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts,
                preview_hash=seen.preview_hash, actor_id=_actor(factories),
            )
        )

        assert code == "preview_changed"


class TestRangeNumbersAreExact:
    """Границы и шаг — меньше 10^9 по модулю, не больше шести знаков после запятой;
    предпросмотр и сохранение одной проверкой (без тихого округления и 500)."""

    @pytest.mark.parametrize(
        "rng",
        [
            pytest.param(("0", "1.0000000000000000000000000001", "0.5"), id="rounded-bound"),
            pytest.param(("1e-999999999", "1", "0.5"), id="tiny-start"),
            pytest.param(("0", "1e200000", "1e199999"), id="huge-stop"),
            pytest.param(("0", "0.0000001", "0.0000001"), id="seven-fraction-digits"),
            pytest.param(("0", "1000000000", "1000000000"), id="ten-to-the-ninth"),
            pytest.param(
                ("1.00000000000000000000000000001", "2.00000000000000000000000000001", "0.5"),
                id="twenty-nine-fraction-digits",
            ),
            pytest.param(("-1000000000", "-999999999", "1"), id="minus-ten-to-the-ninth"),
        ],
    )
    def test_the_route_refuses_with_the_code_and_changes_nothing(
        self, admin_client, db_session, factories, rng
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        before = _all_state(db_session)
        parameters = (*_SAME, _ranged("Диаметр", *rng))

        preview = _preview(admin_client, scene.family.id, *parameters)
        patch = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash="x"),
        )

        for response in (preview, patch):
            assert response.status_code == 422, response.text
            assert _detail(response)["code"] == "schema_bad_range"
        assert _all_state(db_session) == before

    @pytest.mark.parametrize(
        "rng",
        [
            pytest.param(("0", "0.000006", "0.000001"), id="six-fraction-digits"),
            pytest.param(("0", "999999999", "999999999"), id="below-ten-to-the-ninth"),
            pytest.param(
                ("0", "0.0000010000000000000000000000000", "0.000001"),
                id="six-digits-and-trailing-zeros",
            ),
            pytest.param(("0", "2.0000000", "1"), id="trailing-zeros-are-not-digits"),
            pytest.param(("0.00000000", "1", "0.5"), id="zero-written-with-eight-zeros"),
        ],
    )
    def test_the_boundary_values_pass_and_are_saved(
        self, admin_client, db_session, factories, rng
    ):
        scene = _family_with_params(db_session, factories)
        parameters = (*_SAME, _ranged("Диаметр", *rng))
        preview = _preview(admin_client, scene.family.id, *parameters)
        assert preview.status_code == 200, preview.text

        saved = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash=preview.json()["preview_hash"]),
        )

        assert saved.status_code == 200, saved.text


class TestZeroStepIsRefusedAsAStep:
    """Нулевой шаг отказывает той же проверкой шага, что и отрицательный: код и
    сообщение одни, до арифметики диапазона."""

    def test_a_zero_step_is_refused_like_a_negative_step(self, db_session, factories):
        world, *_ = _scene(db_session, factories)

        def refusal(step):
            drafts = [*same_drafts(), draft(None, "Диаметр", kind="range", rng=(0, 10, step))]
            with pytest.raises(WorkFamilyError) as raised:
                preview_schema_edit(db_session, family_id=world.family.id, drafts=drafts)
            return raised.value.code, str(raised.value)

        assert refusal(0) == refusal(-1)


class TestFractionDigits:
    """Контракт помощников проверки вида числа — число знаков после
    запятой не отрицательно, хвостовые нули не знаки, ноль — без знаков."""

    @pytest.mark.parametrize(
        ("text", "digits"),
        [
            ("1E+3", 0), ("120", 0), ("0", 0), ("0.0000000", 0), ("1.50", 1),
            ("-0.000001", 6), ("0.0000010000", 6), ("1.0000001", 7),
        ],
    )
    def test_the_number_of_fraction_digits(self, text, digits):
        assert schema_edit_module._fraction_digits(Decimal(text)) == digits

    def test_stripping_an_all_zero_tuple_gives_nothing(self):
        assert schema_edit_module._strip_trailing_zeros((0, 0)) == ()


def _range_texts(db, schema_id, ordinal) -> tuple[str, str, str]:
    """Границы и шаг параметра так, как их хранит `numeric` (запись с масштабом)."""
    db.expire_all()
    return tuple(
        db.execute(
            sa.select(
                sa.cast(FamilyParameter.range_from, sa.Text),
                sa.cast(FamilyParameter.range_to, sa.Text),
                sa.cast(FamilyParameter.range_step, sa.Text),
            ).where(FamilyParameter.schema_id == schema_id, FamilyParameter.ordinal == ordinal)
        ).one()
    )


def _value_texts(db, parameter_id) -> list[tuple[str, str | None, str | None]]:
    db.expire_all()
    return [
        tuple(row)
        for row in db.execute(
            sa.select(
                FamilyParameterValue.value,
                sa.cast(FamilyParameterValue.range_lo, sa.Text),
                sa.cast(FamilyParameterValue.range_hi, sa.Text),
            )
            .where(FamilyParameterValue.parameter_id == parameter_id)
            .order_by(FamilyParameterValue.id)
        )
    ]


_LONG_ZEROS = "0" * 20000


class TestRangeIsStoredCanonically:
    """Число, допустимое по значению, но записанное с огромным показателем или
    тысячами хвостовых нулей, проходит предпросмотр и сохранение: в колонки,
    подписи, границы значений и `preview_hash` уходит каноническая запись
    (масштаб не больше шести), а не исходная. Внутренняя граница значения —
    сумма `lower + step`, её масштаб — масштаб шага (`1.0` при шаге `0.5`)."""

    @pytest.mark.parametrize(
        ("rng", "plain", "stored", "values"),
        [
            pytest.param(
                ("0E-100000", "1", "0.5"), ("0", "1", "0.5"), ("0", "1", "0.5"),
                [("до 0", None, "0"), ("0–0,5", "0", "0.5"), ("0,5–1", "0.5", "1.0"),
                 ("1 и более", "1", None)],
                id="zero-with-exponent-minus-100000",
            ),
            pytest.param(
                ("0E-999999999", "1", "0.5"), ("0", "1", "0.5"), ("0", "1", "0.5"),
                [("до 0", None, "0"), ("0–0,5", "0", "0.5"), ("0,5–1", "0.5", "1.0"),
                 ("1 и более", "1", None)],
                id="zero-with-exponent-minus-999999999",
            ),
            pytest.param(
                ("0E-16384", "1", "0.5"), ("0", "1", "0.5"), ("0", "1", "0.5"),
                [("до 0", None, "0"), ("0–0,5", "0", "0.5"), ("0,5–1", "0.5", "1.0"),
                 ("1 и более", "1", None)],
                id="zero-with-exponent-minus-16384",
            ),
            pytest.param(
                ("1." + _LONG_ZEROS, "2", "0.5"), ("1", "2", "0.5"), ("1", "2", "0.5"),
                [("до 1", None, "1"), ("1–1,5", "1", "1.5"), ("1,5–2", "1.5", "2.0"),
                 ("2 и более", "2", None)],
                id="start-with-20000-trailing-zeros",
            ),
            pytest.param(
                ("0", "1", "0.5" + _LONG_ZEROS), ("0", "1", "0.5"), ("0", "1", "0.5"),
                [("до 0", None, "0"), ("0–0,5", "0", "0.5"), ("0,5–1", "0.5", "1.0"),
                 ("1 и более", "1", None)],
                id="step-with-20000-trailing-zeros",
            ),
        ],
    )
    def test_a_long_spelling_is_saved_in_its_short_form(
        self, admin_client, db_session, factories, rng, plain, stored, values
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        parameters = (*_SAME, _ranged("Диаметр", *rng))
        preview = _preview(admin_client, scene.family.id, *parameters)
        same_range = _preview(admin_client, scene.family.id, *_SAME, _ranged("Диаметр", *plain))
        assert preview.status_code == 200, preview.text[:500]
        assert same_range.status_code == 200, same_range.text[:500]
        assert preview.json()["preview_hash"] == same_range.json()["preview_hash"]
        assert preview.json()["asked_by_parameter"][-1]["ordinal"] == 3

        saved = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*parameters, preview_hash=preview.json()["preview_hash"]),
        )

        assert saved.status_code == 200, saved.text[:500]
        assert [p["name"] for p in saved.json()["parameters"]] == [
            "Толщина", "Материал", "Диаметр",
        ]
        assert _range_texts(db_session, scene.schema.id, 3) == stored
        assert _value_texts(db_session, _parameter(db_session, scene.schema.id, 3).id) == values
        [event] = _events(db_session, "family_schema_edited", scene.family.id)
        [added] = [c for c in event.payload["changes"] if c.get("added")]
        assert added["range"] == dict(zip(("start", "stop", "step"), stored, strict=True))

    def test_minus_zero_and_zero_give_one_hash(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.commit()

        minus = _preview(admin_client, scene.family.id, *_SAME, _ranged("Диаметр", "-0", "1", "1"))
        plain = _preview(admin_client, scene.family.id, *_SAME, _ranged("Диаметр", "0", "1", "1"))

        assert minus.status_code == plain.status_code == 200
        assert minus.json()["preview_hash"] == plain.json()["preview_hash"]

    def test_a_different_range_gives_a_different_hash(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.commit()

        half = _preview(admin_client, scene.family.id, *_SAME, _ranged("Диаметр", "0", "1", "0.5"))
        whole = _preview(admin_client, scene.family.id, *_SAME, _ranged("Диаметр", "0", "1", "1"))

        assert half.status_code == whole.status_code == 200
        assert half.json()["preview_hash"] != whole.json()["preview_hash"]


class TestRangeOfAnExactParameterIsIgnored:
    """Границы в теле точного параметра не проверяются и никуда не уходят: ни в
    колонки (CHECK вида), ни в `preview_hash`."""

    _WITH_RANGE = {
        **_exact(None, "Армирование", "сетка"),
        "range": {"start": "0E-100000", "stop": "1", "step": "0.3"},
    }

    def test_the_range_of_an_exact_parameter_is_not_in_the_hash(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()

        preview = _preview(admin_client, scene.family.id, *_SAME, self._WITH_RANGE)
        without = _preview(
            admin_client, scene.family.id, *_SAME, _exact(None, "Армирование", "сетка")
        )

        assert preview.status_code == without.status_code == 200, preview.text[:500]
        assert preview.json()["preview_hash"] == without.json()["preview_hash"]

    def test_a_new_exact_parameter_with_a_range_is_saved_without_it(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        db_session.commit()
        parameter = self._WITH_RANGE
        preview = _preview(admin_client, scene.family.id, *_SAME, parameter)
        assert preview.status_code == 200, preview.text[:500]

        saved = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_body(*_SAME, parameter, preview_hash=preview.json()["preview_hash"]),
        )

        assert saved.status_code == 200, saved.text[:500]
        row = _parameter(db_session, scene.schema.id, 3)
        assert (row.value_kind, row.range_from, row.range_to, row.range_step) == (
            "exact", None, None, None,
        )
        assert list(_values(db_session, row.id)) == ["сетка"]

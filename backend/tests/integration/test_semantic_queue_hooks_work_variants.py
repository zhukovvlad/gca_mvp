"""Точки инварианта очереди: модуль `services.work_variants` (спека
`2026-10-02-catalog-variants-design.md` §2.7).

Первые две точки перечня: обработка результата `context_values`
(`apply_values`) и заморозка схемы (`freeze_schema`); затем смена семьи
(`services.family_change`: `request_family_change`, `cancel_pending_family`) и
«не работа» контексту (`mark_context_not_work`); жизнь схемы
(`rebuild_schema`, `update_schema`, `merge_parameter_values`).
Каждая зовёт сверку в той же транзакции, до коммита вызывающего. Тест точки строится входом, который
краснеет, если вызов сверки в этой точке снят: без вызова задание по
предикату не появляется. Остальные точки перечня добавляют свои тесты в этот
файл вместе со своими модулями. `cancel_schema_build` сверку не зовёт: отмена
версии сама отменяет её задания, а запросы контекстов не меняются.

Помощники цепочки «семья -> схема -> контекст» импортируются из набора ядра
варианта.
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

import services.family_change  # noqa: F401  # модуль под проверкой: его точки названы ниже
import services.work_variants  # noqa: F401  # модуль под проверкой: его точки названы ниже
from config import settings
from models import CatalogContext, SemanticJob, WorkFamily
from services.family_change import cancel_pending_family, request_family_change
from services.variant_request import load_values_material, paths_hash_of, render_values_request
from services.work_variants import (
    ParameterEdit,
    apply_values,
    mark_context_not_work,
    rebuild_schema,
    update_schema,
)
from tests.integration.test_work_variants_core import (
    _answer,
    _apply,
    _attach_variant,
    _freeze,
    _freeze_world,
    _guarded,
    _world,
)
from tests.integration.test_work_variants_family_change import (
    _current_schema,
    _two_families,
    _with_variant,
)
from tests.integration.test_work_variants_material import _bind, _chain_context, _frozen_schema, _uid
from tests.integration.test_work_variants_schema import _family, _variant
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _jobs,
    _Merge,
    _same_edits,
    _second_context,
)

pytestmark = pytest.mark.integration


def _values_jobs(db, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == "context_values")
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt.order_by(SemanticJob.id)).scalars().all())


def _current_paths(db, context_id):
    return load_values_material(db, [context_id])[context_id].paths


class TestApplyValuesReconciles:
    def test_paths_that_moved_since_the_request_put_a_new_job_in_the_queue(
        self, db_session, factories
    ):
        world = _world(db_session, factories)

        outcome = _apply(db_session, world, paths_hash="paths-of-the-request")

        assert outcome.applied
        [job] = _values_jobs(db_session, context_id=world.context_id)
        material = load_values_material(db_session, [world.context_id])[world.context_id]
        assert job.status == "pending"
        assert job.schema_id == world.schema.id
        assert job.request_hash == render_values_request(material, settings=settings).request_hash
        assert job.paths_hash == paths_hash_of(material.paths)

    def test_paths_equal_to_the_request_leave_the_queue_empty(self, db_session, factories):
        world = _world(db_session, factories)

        outcome = _apply(
            db_session, world, paths_hash=paths_hash_of(_current_paths(db_session, world.context_id))
        )

        assert outcome.applied
        assert _values_jobs(db_session) == []

    def test_the_call_happens_before_the_callers_commit_in_the_same_session(
        self, db_session, factories
    ):
        world = _world(db_session, factories)

        _apply(db_session, world, paths_hash="paths-of-the-request")

        # Задание видно в той же сессии и транзакции: коммита не было.
        assert db_session.in_transaction()
        assert len(_values_jobs(db_session, context_id=world.context_id)) == 1

    def test_own_running_job_does_not_stop_the_handler_when_reconcile_runs(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)

        outcome = _apply(
            db_session, world, guard=guard,
            paths_hash=paths_hash_of(_current_paths(db_session, world.context_id)),
        )

        assert outcome.applied
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "done"
        assert len(_values_jobs(db_session, context_id=world.context_id)) == 1

    def test_unapplied_result_does_not_reconcile(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, paths_hash="p")  # первый вызов ставит задание
        db_session.execute(sa.delete(SemanticJob))
        context = db_session.get(CatalogContext, world.context_id)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        outcome = _apply(db_session, world, paths_hash="paths-of-the-request")

        assert not outcome.applied
        assert _values_jobs(db_session) == []


class TestFreezeSchemaReconciles:
    def _world(self, db, factories):
        world = _freeze_world(db, factories)
        variant = _variant(db, world.family, world.current)
        _attach_variant(db, world.context_id, variant, paths_hash="paths-of-the-old-version")
        second_id, _ = _chain_context(
            db, factories, title=f"Вторая строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db, factories, second_id, family=world.family)
        world.second_context_id = second_id
        return world

    def test_every_context_of_the_family_gets_a_job_by_the_new_version(self, db_session, factories):
        world = self._world(db_session, factories)

        outcome = _freeze(db_session, world)

        assert outcome.applied
        jobs = _values_jobs(db_session)
        assert sorted(job.context_id for job in jobs) == sorted(
            [world.context_id, world.second_context_id]
        )
        assert {job.schema_id for job in jobs} == {world.building.id}
        for job in jobs:
            material = load_values_material(db_session, [job.context_id])[job.context_id]
            assert job.request_hash == render_values_request(material, settings=settings).request_hash

    def test_context_of_another_family_gets_no_job(self, db_session, factories):
        world = self._world(db_session, factories)
        other = _family(db_session, status="active", definition="Другая семья")
        _frozen_schema(db_session, factories, other, [(1, "Тип", ["а"])])
        other_id, _ = _chain_context(
            db_session, factories, title=f"Чужая строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, other_id, family=other)

        _freeze(db_session, world)

        assert other_id not in {job.context_id for job in _values_jobs(db_session)}

    def test_unapplied_freeze_does_not_reconcile(self, db_session, factories):
        world = self._world(db_session, factories)
        world.family.status = "archived"
        world.family.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        outcome = _freeze(db_session, world)

        assert not outcome.applied
        assert _values_jobs(db_session) == []

    def test_context_waiting_for_the_family_gets_a_job_by_the_new_version(
        self, db_session, factories
    ):
        """Семья контекста — ожидаемая, а не текущая: заморозка её схемы ставит
        задание и ему (точка зовёт сверку по `COALESCE(ожидание, семья)`)."""
        world = self._world(db_session, factories)
        source = _family(db_session, status="active", definition="Прежняя семья")
        waiting_id, _ = _chain_context(
            db_session, factories, title=f"Ожидающая строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, waiting_id, family=source, pending=world.family)

        _freeze(db_session, world)

        [job] = _values_jobs(db_session, context_id=waiting_id)
        assert job.schema_id == world.building.id


# ---------------------------------------------------------------------------
#  services.family_change: смена семьи и ожидание
# ---------------------------------------------------------------------------

class TestRequestFamilyChangeReconciles:
    """Запрос смены семьи у контекста с вариантом ставит ожидание и зовёт
    сверку в той же транзакции: без вызова задание значений по схеме новой
    семьи не появляется, а задание отозванного ожидания остаётся живым."""

    def test_a_pending_family_queues_a_values_job_by_the_schema_of_the_new_family(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        db_session.execute(sa.delete(SemanticJob))

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        [job] = _values_jobs(db_session, context_id=context_id)
        assert job.status == "pending"
        assert job.schema_id == _current_schema(db_session, scene.family_b).id

    def test_withdrawing_the_pending_family_by_the_current_one_cancels_its_job(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [job] = _values_jobs(db_session, context_id=context_id)

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id,
            actor_id=scene.user.id, source="manual",
        )

        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "cancelled"

    def test_a_replaced_pending_family_cancels_the_job_of_the_old_one(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [old_job] = _values_jobs(db_session, context_id=context_id)

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id,
            actor_id=scene.user.id, source="manual",
        )

        db_session.expire_all()
        assert db_session.get(SemanticJob, old_job.id).status == "cancelled"
        live = [j for j in _values_jobs(db_session, context_id=context_id) if j.status == "pending"]
        assert [j.schema_id for j in live] == [_current_schema(db_session, scene.family_c).id]

    def test_the_call_happens_before_the_callers_commit_in_the_same_session(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        db_session.execute(sa.delete(SemanticJob))

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        assert db_session.in_transaction()
        assert len(_values_jobs(db_session, context_id=context_id)) == 1


class TestCancelPendingFamilyReconciles:
    def test_the_values_job_of_the_cancelled_pending_family_is_cancelled(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [job] = _values_jobs(db_session, context_id=context_id)

        cancel_pending_family(db_session, context_id=context_id, actor_id=scene.user.id)

        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    def test_cancelling_without_a_pending_family_queues_nothing(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        db_session.execute(sa.delete(SemanticJob))

        cancel_pending_family(db_session, context_id=context_id, actor_id=scene.user.id)

        assert _values_jobs(db_session) == []


class TestPendingClearedByApplyValuesReconciles:
    def test_a_result_of_a_family_that_stopped_fitting_cancels_the_pending_and_reconciles(
        self, db_session, factories
    ):
        """Ожидаемая семья архивирована в обход запретов: обработчик снимает
        ожидание и сверяет контекст в той же транзакции, поэтому прочие задания
        значений по этой семье не остаются живыми."""
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [queued] = _values_jobs(db_session, context_id=context_id)
        family = db_session.get(WorkFamily, scene.family_b.id)
        family.status = "archived"
        family.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        outcome = apply_values(
            db_session, context_id=context_id,
            schema_id=_current_schema(db_session, scene.family_b).id, answer=_answer(),
            paths_hash="paths-1", guard=None, settings=settings,
        )

        assert outcome.unapplied_reason == "not_applicable"
        db_session.expire_all()
        assert db_session.get(SemanticJob, queued.id).status == "cancelled"


class TestMarkContextNotWorkReconciles:
    def test_the_values_job_of_the_marked_context_is_cancelled_in_the_same_transaction(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        assert _apply(db_session, world, paths_hash="paths-of-the-request").applied
        [job] = _values_jobs(db_session, context_id=world.context_id)
        assert job.status == "pending"
        user = factories.UserFactory.create()

        mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert db_session.in_transaction()
        [job] = _values_jobs(db_session, context_id=world.context_id)
        assert job.status == "cancelled"

    def test_the_job_of_another_context_stays_in_the_queue(self, db_session, factories):
        world = _world(db_session, factories)
        other, _ = _chain_context(
            db_session, factories, title=f"Соседняя строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, other, family=world.family)
        assert _apply(db_session, world, paths_hash="paths-of-the-request").applied
        assert _apply(
            db_session, world, paths_hash="paths-of-the-request", context_id=other
        ).applied
        user = factories.UserFactory.create()

        mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        [other_job] = _values_jobs(db_session, context_id=other)
        assert other_job.status == "pending"


# ---------------------------------------------------------------------------
#  services.work_variants: жизнь схемы
# ---------------------------------------------------------------------------

class TestRebuildSchemaReconciles:
    def test_the_new_building_version_gets_the_schema_job_in_the_same_transaction(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)

        schema = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert db_session.in_transaction()
        [job] = _jobs(db_session, "family_schema")
        assert (job.schema_id, job.status) == (schema.id, "pending")

    def test_a_family_without_contexts_gets_the_schema_job(self, db_session, factories):
        actor = _actor(factories)
        family = _family(db_session, status="active", definition="Пустая семья")
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["а"])])

        schema = rebuild_schema(db_session, family_id=family.id, actor_id=actor)

        [job] = _jobs(db_session, "family_schema")
        assert (job.schema_id, job.status) == (schema.id, "pending")


class TestUpdateSchemaReconciles:
    def test_a_new_version_queues_values_jobs_for_the_contexts_of_the_family(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        second = _second_context(db_session, factories, world)
        edits = _same_edits() + [ParameterEdit(3, "Армирование", ("сетка",))]

        schema = update_schema(
            db_session, family_id=world.family.id, parameters=edits, actor_id=actor
        )

        jobs = _values_jobs(db_session)
        assert sorted(job.context_id for job in jobs) == sorted([world.context_id, second])
        assert {job.schema_id for job in jobs} == {schema.id}

    def test_a_cosmetic_edit_replaces_a_pending_job_built_on_the_old_text(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        assert _apply_reconcile(db_session, world.context_id)
        [old_job] = _values_jobs(db_session, context_id=world.context_id)
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "ТОЛЩИНА", edits[0].values)

        update_schema(db_session, family_id=world.family.id, parameters=edits, actor_id=actor)

        jobs = _values_jobs(db_session)
        assert {job.id: job.status for job in jobs}[old_job.id] == "cancelled"
        live = [job for job in jobs if job.status == "pending"]
        assert len(live) == 1 and live[0].request_hash != old_job.request_hash


class TestMergeParameterValuesReconciles:
    def test_a_job_built_on_the_old_value_list_is_replaced_by_one_on_the_new_list(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        assert _apply_reconcile(db_session, scene.world.context_id)
        [old_job] = _values_jobs(db_session, context_id=scene.world.context_id)

        scene.merge()

        listed = _values_jobs(db_session, context_id=scene.world.context_id)
        assert sorted(job.status for job in listed) == ["cancelled", "pending"]  # живое — одно
        jobs = {job.status: job for job in listed}
        assert jobs["cancelled"].id == old_job.id
        assert jobs["pending"].request_hash != old_job.request_hash


def _apply_reconcile(db, context_id) -> bool:
    from services.semantic_reconcile import reconcile_or_defer

    reconcile_or_defer(db, [context_id])
    return bool(_values_jobs(db, context_id=context_id))

"""Точки инварианта очереди: модуль `services.work_variants` (спека
`2026-10-02-catalog-variants-design.md` §2.7).

Первые две точки перечня: обработка результата `context_values`
(`apply_values`) и заморозка схемы (`freeze_schema`). Каждая зовёт сверку в той
же транзакции, до коммита вызывающего. Тест точки строится входом, который
краснеет, если вызов сверки в этой точке снят: без вызова задание по
предикату не появляется. Остальные точки перечня (`merge_parameter_values`,
`rebuild_schema`, `mark_context_not_work` и прочие) добавляют свои тесты в этот
файл вместе со своими модулями.

Помощники цепочки «семья -> схема -> контекст» импортируются из набора ядра
варианта.
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

import services.work_variants  # noqa: F401  # модуль под проверкой: его точки названы ниже
from config import settings
from models import CatalogContext, SemanticJob
from services.variant_request import load_values_material, paths_hash_of, render_values_request
from tests.integration.test_work_variants_core import (
    _apply,
    _attach_variant,
    _freeze,
    _freeze_world,
    _guarded,
    _world,
)
from tests.integration.test_work_variants_material import _bind, _chain_context, _frozen_schema, _uid
from tests.integration.test_work_variants_schema import _family, _variant

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

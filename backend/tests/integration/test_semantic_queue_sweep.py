"""Периодическая сверка очереди «по кругу» (спека вариантов §2.7): предмет, который
сверка операции пропустила под замком (`SKIP LOCKED`), или запись, у которой не
дошёл хук, чинится следующим проходом, а не ждёт чужого события.

Настоящие сессии (`committing_session_factory`): проход открывает свои."""
from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.semantic_runner as runner_module
import services.semantic_worker as worker_module
from config import settings as app_settings
from models import SemanticJob, SemanticReconcileBatch
from services.semantic_decisions import discard_batch
from services.semantic_reconcile import reconcile_context_values
from services.semantic_runner import SemanticRunner
from services.semantic_worker import sweep_semantic_queue
from tests.integration.test_semantic_queue_decisions import (
    _make_job,
    _scene,
    _without_schema,
)
from tests.integration.test_work_variants_core import _world
from tests.integration.test_work_variants_worker import _values_world

pytestmark = pytest.mark.integration

_SETTINGS = app_settings.model_copy(
    update={"SEMANTIC_SWEEP_INTERVAL_S": 300, "SEMANTIC_EVENT_MAX_CONTEXTS": 3000}
)
_T0 = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)


def _jobs(db, kind, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == kind).order_by(SemanticJob.id)
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt).scalars().all())


def _values_needed_context(db, factories) -> int:
    """Контекст с текущей схемой семьи, без варианта и без задания: значения ему
    нужны (вариант схемы не текущий), а ставить их некому."""
    world = _world(db, factories)
    db.commit()
    return world.context_id


def _schemaless_ready_family(db, factories):
    scene = _scene(db, factories, titles=("Пол А",))
    _without_schema(db, scene.family.id)
    db.commit()
    return scene


class TestSweepFunction:
    def test_a_context_that_needs_values_and_has_no_job_gets_the_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        context_id = _values_needed_context(committing_db, committing_factories)
        assert _jobs(committing_db, "context_values") == [], "вход: задания значений нет"

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        (job,) = _jobs(committing_db, "context_values")
        assert (job.context_id, job.status) == (context_id, "pending")

    @pytest.mark.parametrize("status", ["pending", "running", "privacy_hold"])
    def test_a_context_with_a_live_job_gets_no_second_one(
        self, committing_session_factory, committing_db, committing_factories, status
    ):
        world = _values_world(committing_db, committing_factories)
        world.job.status = status
        if status == "running":
            world.job.claim_token = uuid.uuid4()
        if status == "privacy_hold":
            world.job.privacy_matches = [{"match": "x"}]
        committing_db.commit()

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        (job,) = _jobs(committing_db, "context_values")
        assert (job.id, job.status) == (world.job.id, status)

    def test_a_context_with_a_variant_on_the_current_schema_gets_nothing(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Окно берёт любой контекст с семьёй без живого задания; нужность решает
        сверка. Контексту с вариантом текущей схемы и прежними путями значения не
        нужны: выполненное задание текущего отпечатка не оживает (иначе проход
        перезапрашивал бы его модель каждые пять минут)."""
        from services.variant_request import load_values_material, paths_hash_of
        from tests.integration.test_work_variants_core import _attach_variant
        from tests.integration.test_work_variants_reconcile import _job, _values_hash
        from tests.integration.test_work_variants_schema import _variant

        db = committing_db
        world = _world(db, committing_factories)
        paths = load_values_material(db, [world.context_id])[world.context_id].paths
        _attach_variant(
            db, world.context_id, _variant(db, world.family, world.schema),
            paths_hash=paths_hash_of(paths),
        )
        done = _job(
            db, kind="context_values", request_hash=_values_hash(db, world.context_id),
            status="done", context_id=world.context_id, schema_id=world.schema.id,
            paths_hash=paths_hash_of(paths),
        )
        db.commit()

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        (job,) = _jobs(db, "context_values")
        assert (job.id, job.status) == (done.id, "done")

    def test_a_ready_family_without_a_schema_gets_the_schema_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _schemaless_ready_family(committing_db, committing_factories)
        assert _jobs(committing_db, "family_schema") == [], "вход: задания схемы нет"

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        (job,) = _jobs(committing_db, "family_schema", family_id=scene.family.id)
        assert job.status == "pending"

    def test_a_family_whose_unit_is_still_reasked_gets_no_schema_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _schemaless_ready_family(committing_db, committing_factories)
        _make_job(committing_db, scene.context_ids[0], unit_id=scene.unit_id)
        committing_db.commit()

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        assert _jobs(committing_db, "family_schema") == []

    def test_a_family_with_a_building_version_is_not_given_a_second_one(
        self, committing_session_factory, committing_db, committing_factories
    ):
        from models import FamilyParameterSchema

        scene = _schemaless_ready_family(committing_db, committing_factories)
        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)
        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        committing_db.expire_all()
        versions = committing_db.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == scene.family.id
            )
        ).scalar_one()
        assert versions == 1
        assert len(_jobs(committing_db, "family_schema", family_id=scene.family.id)) == 1

    def test_a_failure_of_the_schema_part_does_not_escape_and_the_values_part_still_runs(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        context_id = _values_needed_context(committing_db, committing_factories)
        # Семья без схемы нужна, чтобы сверка схем вообще вызывалась: без кандидатов
        # подменённая функция не зовётся, и тест прошёл бы и без перехвата.
        _schemaless_ready_family(committing_db, committing_factories)
        calls = []

        def _boom(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("schema sweep failed")

        monkeypatch.setattr(worker_module, "reconcile_family_schemas", _boom)

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        assert calls == [1], "вход: сверка схем вызвана и упала"
        (job,) = _jobs(committing_db, "context_values")
        assert job.context_id == context_id

    def test_a_failure_of_the_values_part_does_not_escape(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        context_id = _values_needed_context(committing_db, committing_factories)

        def _boom(*args, **kwargs):
            raise RuntimeError("values sweep failed")

        monkeypatch.setattr(worker_module, "reconcile_context_values", _boom)

        assert sweep_semantic_queue(
            committing_session_factory, settings=_SETTINGS, after_context_id=context_id - 1
        ) == context_id - 1
        assert _jobs(committing_db, "context_values") == []

    def test_the_candidate_window_is_bounded_and_the_cursor_walks_all_contexts(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        first = _values_needed_context(committing_db, committing_factories)
        second = _values_needed_context(committing_db, committing_factories)
        monkeypatch.setattr(worker_module, "SWEEP_CONTEXT_LIMIT", 1)

        cursor = sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)
        assert sorted(job.context_id for job in _jobs(committing_db, "context_values")) == [first]
        assert cursor == first

        cursor = sweep_semantic_queue(
            committing_session_factory, settings=_SETTINGS, after_context_id=cursor
        )
        assert sorted(job.context_id for job in _jobs(committing_db, "context_values")) == [
            first,
            second,
        ]
        assert cursor == second

        # Конец круга: окно вернулось пустым — курсор начинает с начала.
        cursor = sweep_semantic_queue(
            committing_session_factory, settings=_SETTINGS, after_context_id=cursor
        )
        assert cursor == 0


    def test_contexts_with_a_live_job_do_not_take_the_window_places(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        busy = _values_world(committing_db, committing_factories)
        committing_db.commit()
        needy = _values_needed_context(committing_db, committing_factories)
        assert busy.context_id < needy, "вход: занятый контекст стоит в окне первым"
        monkeypatch.setattr(worker_module, "SWEEP_CONTEXT_LIMIT", 1)

        sweep_semantic_queue(committing_session_factory, settings=_SETTINGS)

        assert sorted(job.context_id for job in _jobs(committing_db, "context_values")) == [
            busy.context_id,
            needy,
        ]


class TestSweepAfterDiscard:
    """«Отбросить» удержанную пачку значит «не ставить эти предметы с этим
    отпечатком»: проход не возвращает их удержанной пачкой каждые пять минут.
    Сверка операций ведёт себя как прежде."""

    _CAP_ONE = _SETTINGS.model_copy(update={"SEMANTIC_EVENT_MAX_CONTEXTS": 1})

    def _batches(self, db):
        db.expire_all()
        return list(
            db.execute(sa.select(SemanticReconcileBatch).order_by(SemanticReconcileBatch.id))
            .scalars()
            .all()
        )

    def _held_values_batch(self, db, factories, factory):
        _values_needed_context(db, factories)
        _values_needed_context(db, factories)
        sweep_semantic_queue(factory, settings=self._CAP_ONE)
        (batch,) = self._batches(db)
        assert (batch.status, _jobs(db, "context_values")) == ("held", []), "вход: пачка удержана"
        return batch

    def _discard(self, db, factories, batch):
        discard_batch(db, batch_id=batch.id, actor_id=factories.UserFactory.create().id)
        db.commit()

    def test_a_discarded_values_batch_is_not_brought_back_by_the_sweep(
        self, committing_session_factory, committing_db, committing_factories
    ):
        batch = self._held_values_batch(
            committing_db, committing_factories, committing_session_factory
        )
        self._discard(committing_db, committing_factories, batch)

        sweep_semantic_queue(committing_session_factory, settings=self._CAP_ONE)

        assert _jobs(committing_db, "context_values") == []
        assert [b.status for b in self._batches(committing_db)] == ["discarded"]

    def test_the_sweep_proceeds_when_the_fingerprint_of_the_discarded_subject_changed(
        self, committing_session_factory, committing_db, committing_factories
    ):
        batch = self._held_values_batch(
            committing_db, committing_factories, committing_session_factory
        )
        self._discard(committing_db, committing_factories, batch)
        # Отбрасывали предметы с другим отпечатком: нынешний в отброшенной пачке не значится.
        stale = [dict(element, request_hash="прежний") for element in batch.held_fingerprints]
        committing_db.execute(
            sa.update(SemanticReconcileBatch)
            .where(SemanticReconcileBatch.id == batch.id)
            .values(held_fingerprints=stale)
        )
        committing_db.commit()

        sweep_semantic_queue(committing_session_factory, settings=self._CAP_ONE)

        assert sorted(b.status for b in self._batches(committing_db)) == ["discarded", "held"]

    def test_ordinary_reconcile_still_holds_the_discarded_subjects_again(
        self, committing_session_factory, committing_db, committing_factories
    ):
        from services.semantic_cost import event_cap_from

        batch = self._held_values_batch(
            committing_db, committing_factories, committing_session_factory
        )
        self._discard(committing_db, committing_factories, batch)
        context_ids = sorted({int(e["context_id"]) for e in batch.held_fingerprints})

        report = reconcile_context_values(
            committing_db, context_ids, cap=event_cap_from(self._CAP_ONE), source="operation"
        )
        committing_db.commit()

        assert report.held_batch_id is not None

    def test_a_discarded_schema_batch_is_not_brought_back_by_the_sweep(
        self, committing_session_factory, committing_db, committing_factories
    ):
        _schemaless_ready_family(committing_db, committing_factories)
        other = _scene(committing_db, committing_factories, titles=("Труба А",), unit="M3")
        _without_schema(committing_db, other.family.id)
        committing_db.commit()
        sweep_semantic_queue(committing_session_factory, settings=self._CAP_ONE)
        (batch,) = self._batches(committing_db)
        assert (batch.status, _jobs(committing_db, "family_schema")) == ("held", []), "вход"
        self._discard(committing_db, committing_factories, batch)

        sweep_semantic_queue(committing_session_factory, settings=self._CAP_ONE)

        assert _jobs(committing_db, "family_schema") == []
        assert [b.status for b in self._batches(committing_db)] == ["discarded"]


class _Clock:
    def __init__(self):
        self.now = _T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += dt.timedelta(seconds=seconds)


def _runner(factory, clock, **over):
    settings = _SETTINGS.model_copy(update=over)
    return SemanticRunner(factory, SimpleNamespace(), settings=settings, clock=clock)


class TestRunnerSchedule:
    def test_the_sweep_runs_only_when_the_interval_has_elapsed(
        self, committing_session_factory, committing_db, committing_factories
    ):
        _values_needed_context(committing_db, committing_factories)
        clock = _Clock()
        runner = _runner(committing_session_factory, clock)

        runner._sweep_if_due()
        clock.advance(299)
        runner._sweep_if_due()
        assert _jobs(committing_db, "context_values") == [], "интервал не прошёл"

        clock.advance(1)
        runner._sweep_if_due()
        assert len(_jobs(committing_db, "context_values")) == 1

    def test_the_next_sweep_waits_for_a_full_interval_again(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        clock = _Clock()
        runner = _runner(committing_session_factory, clock)
        calls = []
        monkeypatch.setattr(
            runner_module, "sweep_semantic_queue",
            lambda *args, **kwargs: calls.append(clock.now) or 0,
        )

        runner._sweep_if_due()
        clock.advance(300)
        runner._sweep_if_due()
        clock.advance(299)
        runner._sweep_if_due()
        clock.advance(1)
        runner._sweep_if_due()

        assert calls == [_T0 + dt.timedelta(seconds=300), _T0 + dt.timedelta(seconds=600)]

    def test_zero_interval_switches_the_sweep_off(
        self, committing_session_factory, committing_db, committing_factories
    ):
        _values_needed_context(committing_db, committing_factories)
        clock = _Clock()
        runner = _runner(committing_session_factory, clock, SEMANTIC_SWEEP_INTERVAL_S=0)

        runner._sweep_if_due()
        clock.advance(10_000)
        runner._sweep_if_due()

        assert _jobs(committing_db, "context_values") == []

    def test_a_failing_sweep_does_not_escape_the_loop_step(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        clock = _Clock()
        runner = _runner(committing_session_factory, clock)

        def _boom(*args, **kwargs):
            raise RuntimeError("sweep failed")

        monkeypatch.setattr(runner_module, "sweep_semantic_queue", _boom)

        runner._sweep_if_due()
        clock.advance(300)
        runner._sweep_if_due()

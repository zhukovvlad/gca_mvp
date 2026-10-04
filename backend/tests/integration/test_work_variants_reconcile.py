"""Сверка очереди для трёх видов заданий: пути, готовность схемы, пачки нового
формата, тарифы вида, волна после расширений (спека
`2026-10-02-catalog-variants-design.md` §2.6, §2.7, §2.8, §2.4).

Каждая строка таблицы исходов фичи «Семантические предложения» предъявлена для
`family_schema` и `context_values` отдельным входом (параметр `scene`). Сцена
`schema` — активная семья с версией `building`; сцена `values` — активная
семья с текущей версией схемы и привязанным контекстом без варианта (задание
значений нужно). Помощники цепочки «семья -> схема -> контекст» импортируются
из файлов схемы и материала запросов, как это делает набор ядра варианта.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from config import settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextParameterValue,
    FamilyParameterSchema,
    SemanticJob,
    SemanticReconcileBatch,
    WorkFamily,
)
from services import semantic_reconcile as reconcile_module
from services.semantic_cost import EventCap
from services.semantic_decisions import _batch_context_ids
from services.semantic_reconcile import (
    NO_CAP,
    RECONCILE_ALLOWLIST,
    Fingerprint,
    ReconcileReport,
    _estimate_totals,
    fingerprints_hash,
    get_or_create_held_batch,
    held_fingerprints,
    reconcile_context_values,
    reconcile_family_schemas,
    reconcile_semantic_jobs,
    schedule_extension_wave,
    schema_ready_to_build,
)
from services.semantic_request import RenderedRequest
from services.variant_request import (
    load_schema_material,
    load_values_material,
    paths_hash_of,
    render_schema_request,
    render_values_request,
)
from tests.integration.test_work_variants_material import (
    _bind,
    _chain_context,
    _frozen_schema,
    _uid,
    _unit_id,
)
from tests.integration.test_work_variants_schema import (
    _batch,
    _family,
    _param,
    _schema,
    _value,
    _variant,
)

pytestmark = pytest.mark.integration

_ZERO = ReconcileReport(
    created=0, revived=0, cancelled=0, republished=0, unpublished=0, held_batch_id=None
)
_REVIVABLE = ("input_changed", "not_applicable", "stale_hold")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _active_family(db, **overrides) -> WorkFamily:
    return _family(db, status="active", definition="Определение семьи", **overrides)


def _context(db, factories, *, family=None, pending=None, unit_id=None, title=None,
             path_specs=None, catalog_kind="POSITION") -> int:
    """Контекст с наименованием; семья и ожидание — через провенанс-совместимую
    привязку; вид строки каталога — заданный."""
    context_id, _ = _chain_context(
        db, factories, title=title or f"Стяжка {_uid()}",
        path_specs=path_specs or [(("Секция", "Полы"), 2)], unit_id=unit_id,
    )
    context = _bind(db, factories, context_id, family=family, pending=pending)
    catalog_id = db.execute(
        sa.select(ContextBucket.catalog_position_id).where(ContextBucket.id == context.bucket_id)
    ).scalar_one()
    db.get(CatalogPosition, catalog_id).kind = catalog_kind
    db.flush()
    return context_id


def _job(db, *, kind, request_hash, status="pending", context_id=None, family_id=None,
         schema_id=None, cancel_reason=None, unit_id=None, paths_hash=None,
         retry_generation=0) -> SemanticJob:
    extra: dict = {}
    if status == "cancelled":
        cancel_reason = cancel_reason or "input_changed"
    if status == "running":
        extra["claim_token"] = uuid.uuid4()
    if status == "privacy_hold":
        extra["privacy_matches"] = [{"match": "x"}]
    job = SemanticJob(
        kind=kind, context_id=context_id, family_id=family_id, schema_id=schema_id,
        paths_hash=paths_hash, request_hash=request_hash, status=status,
        cancel_reason=cancel_reason, unit_id=unit_id, retry_generation=retry_generation,
        prompt_version="1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="1",
        serialization_version="1", **extra,
    )
    db.add(job)
    db.flush()
    return job


def _attach(db, context_id, variant, *, paths_hash):
    context = db.get(CatalogContext, context_id)
    context.work_variant_id = variant.id
    context.variant_at = _now()
    context.variant_paths_hash = paths_hash
    db.flush()


def _schema_hash(db, family_id, schema_id) -> str:
    return render_schema_request(
        load_schema_material(db, family_id, schema_id), settings=settings
    ).request_hash


def _values_hash(db, context_id) -> str:
    material = load_values_material(db, [context_id])[context_id]
    return render_values_request(material, settings=settings).request_hash


def _jobs(db, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).order_by(SemanticJob.id)
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt).scalars().all())


def _held(db, factories, fingerprints, *, source="import") -> SemanticReconcileBatch:
    return _batch(
        db, factories, source=source, held_fingerprints=[f.as_dict() for f in fingerprints],
        fingerprints_hash=fingerprints_hash(fingerprints), contexts_count=len(fingerprints),
    )


_SUGGESTION = "family_suggestion"
_SCHEMA = "family_schema"
_VALUES = "context_values"


def _fp(kind, *, context_id=None, family_id=None, schema_id=None, request_hash="h"):
    return Fingerprint(kind, context_id, family_id, schema_id, request_hash)


# ---------------------------------------------------------------------------
#  Сцены двух видов для таблицы исходов
# ---------------------------------------------------------------------------

def _schema_scene(db, factories):
    family = _active_family(db)
    schema = _schema(db, factories, family, version=1, status="building")
    _context(db, factories, family=family)
    return SimpleNamespace(
        kind=_SCHEMA, family=family, schema=schema,
        current_hash=_schema_hash(db, family.id, schema.id),
        subject=dict(family_id=family.id, schema_id=schema.id),
        reconcile=lambda: reconcile_family_schemas(
            db, [family.id], cap=NO_CAP, source="operation"
        ),
        make_inapplicable=lambda: setattr(family, "status", "draft") or db.flush(),
        listing=lambda: _jobs(db, kind=_SCHEMA, family_id=family.id),
    )


def _values_scene(db, factories):
    family = _active_family(db)
    schema = _frozen_schema(db, factories, family, [(1, "Толщина", ["50 мм", "100 мм"])])
    context_id = _context(db, factories, family=family)
    paths = paths_hash_of(load_values_material(db, [context_id])[context_id].paths)

    def make_inapplicable():
        db.get(CatalogContext, context_id).archived_at = _now()
        db.flush()

    return SimpleNamespace(
        kind=_VALUES, family=family, schema=schema, context_id=context_id,
        current_hash=_values_hash(db, context_id),
        subject=dict(context_id=context_id, schema_id=schema.id, paths_hash=paths),
        reconcile=lambda: reconcile_context_values(
            db, [context_id], cap=NO_CAP, source="operation"
        ),
        make_inapplicable=make_inapplicable,
        listing=lambda: _jobs(db, kind=_VALUES, context_id=context_id),
    )


@pytest.fixture(params=["schema", "values"])
def scene(request, db_session, factories):
    build = _schema_scene if request.param == "schema" else _values_scene
    return build(db_session, factories)


def _make(db, scene, status="pending", *, hash=None, **kw):
    return _job(
        db, kind=scene.kind, request_hash=hash or scene.current_hash, status=status,
        **scene.subject, **kw,
    )


# ---------------------------------------------------------------------------
#  Таблица исходов фичи 2 — задание ТЕКУЩЕГО отпечатка
# ---------------------------------------------------------------------------

class TestCurrentFingerprintOutcomes:
    def test_no_job_creates_pending_with_the_subject_columns(self, db_session, scene):
        report = scene.reconcile()

        assert report == ReconcileReport(1, 0, 0, 0, 0, None)
        [job] = scene.listing()
        assert job.status == "pending"
        assert job.kind == scene.kind
        assert job.request_hash == scene.current_hash
        assert job.schema_id == scene.schema.id
        if scene.kind == _SCHEMA:
            assert (job.family_id, job.context_id, job.paths_hash) == (scene.family.id, None, None)
        else:
            assert (job.family_id, job.context_id) == (None, scene.context_id)
            expected = paths_hash_of(
                load_values_material(db_session, [scene.context_id])[scene.context_id].paths
            )
            assert job.paths_hash == expected

    @pytest.mark.parametrize("status", ["pending", "running", "error", "privacy_hold"])
    def test_job_in_other_statuses_is_left_alone(self, db_session, scene, status):
        job = _make(db_session, scene, status)
        before = job.status

        report = scene.reconcile()

        assert report == _ZERO
        assert [j.status for j in scene.listing()] == [before]

    def test_done_job_is_left_alone_for_schema_and_revived_for_needed_values(
        self, db_session, scene
    ):
        job = _make(db_session, scene, "done", retry_generation=1)

        report = scene.reconcile()

        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        if scene.kind == _SCHEMA:
            assert report == _ZERO and job.status == "done"
        else:
            assert report == ReconcileReport(0, 1, 0, 0, 0, None)
            assert (job.status, job.retry_generation, job.attempts_in_generation) == (
                "pending", 2, 0,
            )

    @pytest.mark.parametrize("reason", _REVIVABLE)
    def test_cancelled_by_revivable_reason_returns_to_pending(self, db_session, scene, reason):
        job = _make(db_session, scene, "cancelled", cancel_reason=reason, retry_generation=2)

        report = scene.reconcile()

        assert report == ReconcileReport(0, 1, 0, 0, 0, None)
        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason, job.retry_generation) == ("pending", None, 3)

    def test_cancelled_by_privacy_decline_stays_cancelled(self, db_session, scene):
        job = _make(db_session, scene, "cancelled", cancel_reason="privacy_declined")

        report = scene.reconcile()

        assert report == _ZERO
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).cancel_reason == "privacy_declined"


class TestOldFingerprintOutcomes:
    def test_old_pending_is_cancelled_input_changed_and_current_is_created(
        self, db_session, scene
    ):
        old = _make(db_session, scene, "pending", hash="old-hash")

        report = scene.reconcile()

        assert report == ReconcileReport(1, 0, 1, 0, 0, None)
        db_session.expire_all()
        old = db_session.get(SemanticJob, old.id)
        assert (old.status, old.cancel_reason) == ("cancelled", "input_changed")

    def test_old_privacy_hold_is_cancelled_stale_hold(self, db_session, scene):
        old = _make(db_session, scene, "privacy_hold", hash="old-hash")

        scene.reconcile()

        db_session.expire_all()
        old = db_session.get(SemanticJob, old.id)
        assert (old.status, old.cancel_reason) == ("cancelled", "stale_hold")

    @pytest.mark.parametrize("status", ["running", "done", "error"])
    def test_old_job_in_other_statuses_is_left_alone(self, db_session, scene, status):
        old = _make(db_session, scene, status, hash="old-hash")

        scene.reconcile()

        db_session.expire_all()
        assert db_session.get(SemanticJob, old.id).status == status


class TestInapplicableSubject:
    @pytest.mark.parametrize(
        "status,reason",
        [("pending", "not_applicable"), ("privacy_hold", "not_applicable")],
    )
    def test_open_jobs_are_cancelled_not_applicable(self, db_session, scene, status, reason):
        job = _make(db_session, scene, status)
        scene.make_inapplicable()

        report = scene.reconcile()

        assert report == ReconcileReport(0, 0, 1, 0, 0, None)
        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", reason)

    @pytest.mark.parametrize("status", ["running", "done", "error"])
    def test_other_statuses_are_left_alone(self, db_session, scene, status):
        job = _make(db_session, scene, status)
        scene.make_inapplicable()

        report = scene.reconcile()

        assert report == _ZERO
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == status

    def test_nothing_new_is_created_for_inapplicable_subject(self, db_session, scene):
        scene.make_inapplicable()
        assert scene.reconcile() == _ZERO
        assert scene.listing() == []


class TestSchemaJobOnVersionThatIsNotBuilding:
    def test_pending_job_of_a_frozen_version_is_cancelled_not_applicable(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        frozen = _schema(db_session, factories, family, version=1, status="frozen")
        job = _job(
            db_session, kind=_SCHEMA, request_hash="x", family_id=family.id, schema_id=frozen.id
        )

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(0, 0, 1, 0, 0, None)
        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    @pytest.mark.parametrize("status", ["draft", "archived"])
    def test_family_that_is_not_active_gets_no_building_version(
        self, db_session, factories, status
    ):
        family = _family(db_session, status=status, definition="Определение")
        if status == "archived":
            family.archived_at = _now()
            db_session.flush()

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == _ZERO
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == family.id
            )
        ).scalar_one() == 0


class TestContextValuesOnAnOldSchemaVersion:
    def test_pending_job_of_a_superseded_version_is_input_changed_and_current_is_created(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        old = _schema(db_session, factories, family, version=1, status="superseded")
        current = _frozen_schema_version(db_session, factories, family, 2)
        context_id = _context(db_session, factories, family=family)
        stale = _job(
            db_session, kind=_VALUES, request_hash=_values_hash(db_session, context_id),
            context_id=context_id, schema_id=old.id, paths_hash="p",
        )

        report = reconcile_context_values(
            db_session, [context_id], cap=NO_CAP, source="operation"
        )

        assert report == ReconcileReport(1, 0, 1, 0, 0, None)
        db_session.expire_all()
        stale = db_session.get(SemanticJob, stale.id)
        assert (stale.status, stale.cancel_reason) == ("cancelled", "input_changed")
        fresh = [j for j in _jobs(db_session, context_id=context_id) if j.id != stale.id]
        assert [(j.schema_id, j.status) for j in fresh] == [(current.id, "pending")]


def _frozen_schema_version(db, factories, family, version):
    schema = _schema(db, factories, family, version=version, status="frozen")
    _value(db, _param(db, schema, 1, "Толщина"), "50 мм")
    return schema


# ---------------------------------------------------------------------------
#  Версия схемы и готовность
# ---------------------------------------------------------------------------

class TestBuildingVersionCreation:
    def test_active_family_without_versions_gets_building_version_and_job(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        _context(db_session, factories, family=family)

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(1, 0, 0, 0, 0, None)
        [version] = db_session.execute(
            sa.select(FamilyParameterSchema).where(FamilyParameterSchema.family_id == family.id)
        ).scalars().all()
        assert (version.version, version.status, version.origin) == (1, "building", "model")
        [job] = _jobs(db_session, kind=_SCHEMA)
        assert (job.family_id, job.schema_id) == (family.id, version.id)

    def test_version_number_is_max_plus_one_counting_cancelled(self, db_session, factories):
        family = _active_family(db_session)
        _schema(db_session, factories, family, version=3, status="cancelled")

        reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        versions = db_session.execute(
            sa.select(FamilyParameterSchema.version, FamilyParameterSchema.status)
            .where(FamilyParameterSchema.family_id == family.id)
            .order_by(FamilyParameterSchema.version)
        ).all()
        assert [tuple(v) for v in versions] == [(3, "cancelled"), (4, "building")]

    def test_family_without_living_rows_still_gets_version_and_job(self, db_session, factories):
        family = _active_family(db_session)
        assert load_schema_material(db_session, family.id, 0).names == ()

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report.created == 1
        assert len(_jobs(db_session, kind=_SCHEMA)) == 1

    def test_family_with_a_single_row_gets_version_and_job(self, db_session, factories):
        family = _active_family(db_session)
        _context(db_session, factories, family=family, title="Единственная строка")
        assert load_schema_material(db_session, family.id, 0).names == ("Единственная строка",)

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report.created == 1

    def test_family_with_current_version_gets_nothing(self, db_session, factories):
        family = _active_family(db_session)
        _schema(db_session, factories, family, version=1, status="frozen")

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == _ZERO
        assert _jobs(db_session, kind=_SCHEMA) == []

    def test_second_call_changes_nothing(self, db_session, factories):
        family = _active_family(db_session)
        reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        second = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert second == _ZERO
        assert len(_jobs(db_session, kind=_SCHEMA)) == 1

    def test_unit_that_is_still_being_asked_gets_no_version(self, db_session, factories):
        unit = _unit_id(db_session, "M2")
        family = _active_family(db_session, unit_id=unit)
        context_id = _context(db_session, factories, unit_id=unit)
        _job(
            db_session, kind=_SUGGESTION, request_hash="s", context_id=context_id, unit_id=unit
        )

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == _ZERO
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema)
        ).scalar_one() == 0

    def test_existing_building_version_is_reused_when_another_session_made_it(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        building = _schema(db_session, factories, family, version=1, status="building")

        found = reconcile_module._ensure_building_version(db_session, family.id)

        assert found == building.id

    def test_over_cap_holds_the_subject_without_creating_the_version(self, db_session, factories):
        family = _active_family(db_session)
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        report = reconcile_family_schemas(db_session, [family.id], cap=cap, source="operation")

        assert report.created == 0 and report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        [element] = batch.held_fingerprints
        assert (element["kind"], element["family_id"], element["schema_id"]) == (
            _SCHEMA, family.id, None,
        )
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema)
        ).scalar_one() == 0


class TestSchemaReadyToBuild:
    """Пять входов плана плюс соседние: единица, окончен ли её перезапрос."""

    def _scene(self, db, factories):
        unit = _unit_id(db, "M2")
        other = _unit_id(db, "PCS")
        family = _active_family(db, unit_id=unit)
        return SimpleNamespace(
            unit=unit, other=other, family=family,
            ctx=_context(db, factories, unit_id=unit),
            other_ctx=_context(db, factories, unit_id=other),
        )

    def test_no_jobs_and_no_batches_is_ready(self, db_session, factories):
        world = self._scene(db_session, factories)
        assert schema_ready_to_build(db_session, world.family.id) is True

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_open_suggestion_job_of_the_unit_blocks(self, db_session, factories, status):
        world = self._scene(db_session, factories)
        _job(db_session, kind=_SUGGESTION, request_hash="s", status=status,
             context_id=world.ctx, unit_id=world.unit)
        assert schema_ready_to_build(db_session, world.family.id) is False

    def test_open_suggestion_job_of_another_unit_does_not_block(self, db_session, factories):
        world = self._scene(db_session, factories)
        _job(db_session, kind=_SUGGESTION, request_hash="s", context_id=world.other_ctx,
             unit_id=world.other)
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_held_batch_with_a_suggestion_of_the_unit_blocks(self, db_session, factories):
        world = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_SUGGESTION, context_id=world.ctx)])
        assert schema_ready_to_build(db_session, world.family.id) is False

    def test_mass_batch_with_a_suggestion_blocks_every_unit(self, db_session, factories):
        world = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_SUGGESTION, context_id=world.other_ctx)], source="mass")
        assert schema_ready_to_build(db_session, world.family.id) is False

    def test_import_batch_of_another_unit_does_not_block(self, db_session, factories):
        world = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_SUGGESTION, context_id=world.other_ctx)])
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_batch_of_values_only_of_the_unit_does_not_block(self, db_session, factories):
        world = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_VALUES, context_id=world.ctx, schema_id=5)])
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_mass_batch_of_values_only_does_not_block(self, db_session, factories):
        world = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_VALUES, context_id=world.ctx, schema_id=5)],
              source="mass")
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_only_privacy_hold_and_error_remain_is_ready(self, db_session, factories):
        world = self._scene(db_session, factories)
        _job(db_session, kind=_SUGGESTION, request_hash="a", status="privacy_hold",
             context_id=world.ctx, unit_id=world.unit)
        _job(db_session, kind=_SUGGESTION, request_hash="b", status="error",
             context_id=world.ctx, unit_id=world.unit)
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_discarded_batch_does_not_block(self, db_session, factories):
        world = self._scene(db_session, factories)
        batch = _held(db_session, factories, [_fp(_SUGGESTION, context_id=world.ctx)])
        decider = factories.UserFactory.create().id
        db_session.execute(
            sa.update(SemanticReconcileBatch)
            .where(SemanticReconcileBatch.id == batch.id)
            .values(status="discarded", decided_by=decider, decided_at=_now())
        )
        assert schema_ready_to_build(db_session, world.family.id) is True

    def test_unknown_family_is_not_ready(self, db_session):
        assert schema_ready_to_build(db_session, 987654321) is False


# ---------------------------------------------------------------------------
#  Нужность задания значений
# ---------------------------------------------------------------------------

_MANY_PATHS = [((f"Р{i:02d}",), i) for i in range(1, 13)]


class TestValuesNeeded:
    def _world(self, db, factories, *, params=((1, "Толщина", ["50 мм", "100 мм"]),)):
        family = _active_family(db)
        schema = _frozen_schema(db, factories, family, [list(p) for p in params])
        context_id = _context(db, factories, family=family, path_specs=_MANY_PATHS)
        variant = _variant(db, family, schema)
        paths = load_values_material(db, [context_id])[context_id].paths
        return SimpleNamespace(
            family=family, schema=schema, context_id=context_id, variant=variant, paths=paths
        )

    def _reconcile(self, db, world):
        return reconcile_context_values(
            db, [world.context_id], cap=NO_CAP, source="operation"
        )

    def test_there_are_exactly_ten_paths(self, db_session, factories):
        world = self._world(db_session, factories)
        assert len(world.paths) == 10

    def test_variant_with_unchanged_paths_needs_no_job(self, db_session, factories):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths))

        assert self._reconcile(db_session, world) == _ZERO
        assert _jobs(db_session, kind=_VALUES) == []

    def test_changed_set_of_ten_paths_needs_a_job(self, db_session, factories):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths[:9] + ("Другой путь",)))

        assert self._reconcile(db_session, world).created == 1

    def test_changed_order_of_the_same_ten_paths_needs_a_job(self, db_session, factories):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(tuple(reversed(world.paths))))

        assert self._reconcile(db_session, world).created == 1

    def test_context_without_variant_needs_a_job(self, db_session, factories):
        world = self._world(db_session, factories)
        assert self._reconcile(db_session, world).created == 1

    def test_variant_on_a_schema_that_is_not_current_needs_a_job(self, db_session, factories):
        world = self._world(db_session, factories)
        old = _schema(db_session, factories, world.family, version=0, status="superseded")
        stale_variant = _variant(db_session, world.family, old)
        _attach(db_session, world.context_id, stale_variant,
                paths_hash=paths_hash_of(world.paths))

        assert self._reconcile(db_session, world).created == 1

    def test_pending_family_needs_a_job_even_when_paths_match(self, db_session, factories):
        world = self._world(db_session, factories)
        target = _active_family(db_session)
        _frozen_schema(db_session, factories, target, [(1, "Тип", ["а"])])
        context = _bind(db_session, factories, world.context_id, pending=target)
        schema_variant = _variant(db_session, world.family, world.schema)
        _attach(db_session, world.context_id, schema_variant,
                paths_hash=paths_hash_of(world.paths))
        assert context.pending_family_id == target.id

        assert self._reconcile(db_session, world).created == 1

    def test_pending_family_equal_to_the_current_one_still_needs_a_job(
        self, db_session, factories
    ):
        """Ожидание — самостоятельная причина нужности: вариант текущей версии,
        пути совпадают, ожидаемая семья — та же. Любой другой вход с ожиданием
        нужен ещё и по несовпадению версии варианта (вариант принадлежит
        текущей семье), и снятие этой ветви не видел бы."""
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths))
        _bind(db_session, factories, world.context_id, pending=world.family)

        assert self._reconcile(db_session, world).created == 1

    def test_zero_parameter_schema_ignores_the_path_hash(self, db_session, factories):
        world = self._world(db_session, factories, params=())
        _attach(db_session, world.context_id, world.variant, paths_hash=paths_hash_of(()))

        assert self._reconcile(db_session, world) == _ZERO

    def test_zero_parameter_schema_without_variant_still_needs_a_job(self, db_session, factories):
        world = self._world(db_session, factories, params=())
        assert self._reconcile(db_session, world).created == 1

    def test_context_of_a_family_without_current_schema_gets_nothing(self, db_session, factories):
        family = _active_family(db_session)
        context_id = _context(db_session, factories, family=family)

        report = reconcile_context_values(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO

    def test_not_needed_context_does_not_revive_its_current_cancelled_job(
        self, db_session, factories
    ):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths))
        job = _job(
            db_session, kind=_VALUES, request_hash=_values_hash(db_session, world.context_id),
            status="cancelled", cancel_reason="input_changed", context_id=world.context_id,
            schema_id=world.schema.id, paths_hash="p",
        )

        assert self._reconcile(db_session, world) == _ZERO
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "cancelled"

    def test_not_needed_context_still_cancels_its_old_pending_job(self, db_session, factories):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths))
        old = _job(
            db_session, kind=_VALUES, request_hash="old", context_id=world.context_id,
            schema_id=world.schema.id, paths_hash="p",
        )

        report = self._reconcile(db_session, world)

        assert report == ReconcileReport(0, 0, 1, 0, 0, None)
        db_session.expire_all()
        assert db_session.get(SemanticJob, old.id).cancel_reason == "input_changed"

    def test_not_needed_context_keeps_its_current_pending_job(self, db_session, factories):
        world = self._world(db_session, factories)
        _attach(db_session, world.context_id, world.variant,
                paths_hash=paths_hash_of(world.paths))
        job = _job(
            db_session, kind=_VALUES, request_hash=_values_hash(db_session, world.context_id),
            context_id=world.context_id, schema_id=world.schema.id, paths_hash="p",
        )

        assert self._reconcile(db_session, world) == _ZERO
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "pending"


class TestValuesApplicability:
    @pytest.mark.parametrize(
        "spoil",
        ["archived", "not_applicable", "system", "header", "trash", "no_members"],
    )
    def test_each_inapplicable_condition_blocks_creation_alone(
        self, db_session, factories, spoil
    ):
        family = _active_family(db_session)
        _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        context_id = _context(db_session, factories, family=family)
        context = db_session.get(CatalogContext, context_id)
        if spoil == "archived":
            context.archived_at = _now()
        elif spoil == "not_applicable":
            context.semantic_state = "NOT_APPLICABLE"
        elif spoil == "system":
            context.semantic_kind = "SYSTEM"
        elif spoil in ("header", "trash"):
            catalog_id = db_session.execute(
                sa.select(ContextBucket.catalog_position_id).where(
                    ContextBucket.id == context.bucket_id
                )
            ).scalar_one()
            db_session.get(CatalogPosition, catalog_id).kind = spoil.upper()
        else:
            db_session.execute(
                sa.text("DELETE FROM context_members WHERE context_id = :c"), {"c": context_id}
            )
        db_session.flush()

        report = reconcile_context_values(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO

    @pytest.mark.parametrize("kind", ["TO_REVIEW", "POSITION"])
    def test_review_and_position_rows_are_applicable(self, db_session, factories, kind):
        family = _active_family(db_session)
        _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        context_id = _context(db_session, factories, family=family, catalog_kind=kind)

        report = reconcile_context_values(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report.created == 1


# ---------------------------------------------------------------------------
#  Все три вида вместе
# ---------------------------------------------------------------------------

def _joint_scene(db, factories):
    """Предложение для A (единица M2), значения для B, схема для F3. Семьи F2
    и F3 без единицы, контекст B — тоже: схему F3 находит единица контекста."""
    m2 = _unit_id(db, "M2")
    f1 = _active_family(db, unit_id=m2)
    f2 = _active_family(db)
    _frozen_schema(db, factories, f2, [(1, "Толщина", ["50 мм"])])
    f3 = _active_family(db)
    a = _context(db, factories, unit_id=m2, title="Предложение")
    b = _context(db, factories, family=f2, title="Значения")
    return SimpleNamespace(f1=f1, f2=f2, f3=f3, a=a, b=b)


class TestAllKindsTogether:
    def test_one_call_creates_a_job_of_each_kind(self, db_session, factories):
        world = _joint_scene(db_session, factories)

        report = reconcile_semantic_jobs(
            db_session, [world.a, world.b], cap=NO_CAP, source="operation"
        )

        assert report.created == 3
        kinds = sorted(job.kind for job in _jobs(db_session))
        assert kinds == [_VALUES, _SCHEMA, _SUGGESTION]
        [schema_job] = _jobs(db_session, kind=_SCHEMA)
        assert schema_job.family_id == world.f3.id

    def test_unit_of_the_contexts_that_is_being_asked_gets_no_schema(self, db_session, factories):
        world = _joint_scene(db_session, factories)

        reconcile_semantic_jobs(db_session, [world.a, world.b], cap=NO_CAP, source="operation")

        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == world.f1.id
            )
        ).scalar_one() == 0

    def test_over_cap_holds_everything_in_one_batch_and_writes_no_job_or_version(
        self, db_session, factories
    ):
        world = _joint_scene(db_session, factories)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(
            db_session, [world.a, world.b], cap=cap, source="operation"
        )

        assert report.created == 0 and report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert sorted(e["kind"] for e in batch.held_fingerprints) == [
            _VALUES, _SCHEMA, _SUGGESTION,
        ]
        assert batch.contexts_count == 3
        assert _jobs(db_session) == []
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == world.f3.id
            )
        ).scalar_one() == 0

    def test_exactly_at_the_cap_everything_is_created(self, db_session, factories):
        world = _joint_scene(db_session, factories)
        cap = EventCap(max_contexts=3, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(
            db_session, [world.a, world.b], cap=cap, source="operation"
        )

        assert report.created == 3 and report.held_batch_id is None

    def test_repeated_over_cap_call_reuses_the_batch(self, db_session, factories):
        world = _joint_scene(db_session, factories)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))

        first = reconcile_semantic_jobs(db_session, [world.a, world.b], cap=cap, source="operation")
        second = reconcile_semantic_jobs(
            db_session, [world.b, world.a], cap=cap, source="operation"
        )

        assert first.held_batch_id == second.held_batch_id

    def test_held_fingerprints_preview_equals_the_batch_content(self, db_session, factories):
        world = _joint_scene(db_session, factories)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))
        preview = held_fingerprints(db_session, [world.a, world.b])

        report = reconcile_semantic_jobs(
            db_session, [world.a, world.b], cap=cap, source="operation"
        )

        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert [Fingerprint.from_dict(e) for e in batch.held_fingerprints] == preview
        assert batch.fingerprints_hash == fingerprints_hash(preview)

    def test_values_branch_is_called_for_the_same_contexts(self, db_session, factories):
        world = _joint_scene(db_session, factories)
        reconcile_semantic_jobs(db_session, [world.b], cap=NO_CAP, source="operation")
        [job] = _jobs(db_session, kind=_VALUES)
        assert job.context_id == world.b

    def test_active_families_of_the_context_unit_are_scoped_in(self, db_session, factories):
        """Контекст не привязан к F3, но F3 — активная семья его единицы."""
        world = _joint_scene(db_session, factories)
        reconcile_semantic_jobs(db_session, [world.b], cap=NO_CAP, source="operation")
        assert [j.family_id for j in _jobs(db_session, kind=_SCHEMA)] == [world.f3.id]


# ---------------------------------------------------------------------------
#  Пачка нового формата
# ---------------------------------------------------------------------------

def _migration_0019():
    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0019-work_variants.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIXED = [
    _fp(_VALUES, context_id=7, schema_id=3, request_hash="v2"),
    _fp(_SUGGESTION, context_id=7, request_hash="a"),
    _fp(_SCHEMA, family_id=2, schema_id=9, request_hash="s"),
    _fp(_SCHEMA, family_id=2, schema_id=None, request_hash="s"),
    _fp(_VALUES, context_id=5, schema_id=3, request_hash="v1"),
]


class TestFingerprintsHash:
    def test_hash_does_not_depend_on_input_order(self):
        assert fingerprints_hash(_MIXED) == fingerprints_hash(list(reversed(_MIXED)))
        assert fingerprints_hash(_MIXED) == fingerprints_hash(_MIXED[2:] + _MIXED[:2])

    @pytest.mark.parametrize(
        "field,value",
        [
            ("kind", _SCHEMA), ("context_id", 99), ("family_id", 99), ("schema_id", 99),
            ("request_hash", "other"),
        ],
    )
    def test_each_field_of_a_fingerprint_changes_the_hash(self, field, value):
        base = _fp(_VALUES, context_id=1, family_id=2, schema_id=3, request_hash="h")
        changed = Fingerprint(**{**base.__dict__, field: value})
        assert fingerprints_hash([base]) != fingerprints_hash([changed])

    def test_hash_equals_the_migration_translation_of_the_same_batch(self):
        migration = _migration_0019()
        elements = [fp.as_dict() for fp in _MIXED]
        translated = migration._fingerprints_to_objects(elements)
        assert fingerprints_hash(_MIXED) == migration._canonical_hash(translated)

    def test_hash_equals_the_migration_translation_of_legacy_pairs(self):
        migration = _migration_0019()
        pairs = [[3, "x"], [1, "y"], [3, "a"]]
        translated = migration._fingerprints_to_objects(pairs)
        built = [_fp(_SUGGESTION, context_id=c, request_hash=h) for c, h in pairs]
        assert fingerprints_hash(built) == migration._canonical_hash(translated)

    def test_batch_translated_by_the_migration_is_the_same_held_record(
        self, db_session, factories
    ):
        migration = _migration_0019()
        pairs = [[3, "x"], [1, "y"]]
        translated = migration._fingerprints_to_objects(pairs)
        existing = _batch(
            db_session, factories, held_fingerprints=translated,
            fingerprints_hash=migration._canonical_hash(translated), contexts_count=2,
        )
        built = [_fp(_SUGGESTION, context_id=1, request_hash="y"),
                 _fp(_SUGGESTION, context_id=3, request_hash="x")]

        found = get_or_create_held_batch(
            db_session, fingerprints=built, source="import", import_job_id=None, unit_id=None,
            reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
        )

        assert found == existing.id
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch)
        ).scalar_one() == 1

    def test_created_batch_stores_sorted_objects(self, db_session):
        batch_id = get_or_create_held_batch(
            db_session, fingerprints=_MIXED, source="operation", import_job_id=None,
            unit_id=None, reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
        )
        batch = db_session.get(SemanticReconcileBatch, batch_id)
        assert [(e["kind"], e["context_id"], e["family_id"], e["schema_id"], e["request_hash"])
                for e in batch.held_fingerprints] == [
            (_VALUES, 5, None, 3, "v1"),
            (_VALUES, 7, None, 3, "v2"),
            (_SCHEMA, None, 2, None, "s"),
            (_SCHEMA, None, 2, 9, "s"),
            (_SUGGESTION, 7, None, None, "a"),
        ]
        assert batch.contexts_count == 5
        assert batch.fingerprints_hash == fingerprints_hash(_MIXED)


class TestBatchContextIds:
    def test_suggestion_and_values_contexts_are_read_and_schema_items_are_not(
        self, db_session, factories
    ):
        batch = _held(db_session, factories, _MIXED)
        assert _batch_context_ids(batch) == [5, 7]


# ---------------------------------------------------------------------------
#  Тарифы вида в оценке потолка
# ---------------------------------------------------------------------------

def _rendered(prefix_hash, prefix_bytes, user_bytes, max_tokens) -> RenderedRequest:
    return RenderedRequest(
        body={"max_tokens": max_tokens}, request_hash="r", prefix_hash=prefix_hash,
        candidates_hash="c", input_hash="i", prefix_bytes=prefix_bytes, user_bytes=user_bytes,
        place_dictionary_version=1,
    )


class TestEstimateUsesTheTariffsOfTheKind:
    def test_sum_equals_the_sum_by_kind(self, db_session, monkeypatch):
        for prefix, prices in (
            ("SEMANTIC_PRICE", (1, 2, 3, 4)),
            ("SEMANTIC_SCHEMA_PRICE", (10, 20, 30, 40)),
            ("SEMANTIC_VALUES_PRICE", (100, 200, 300, 400)),
        ):
            for suffix, price in zip(
                ("INPUT_PER_M", "CACHE_WRITE_PER_M", "CACHE_READ_PER_M", "OUTPUT_PER_M"), prices,
                strict=True,
            ):
                monkeypatch.setattr(settings, f"{prefix}_{suffix}", Decimal(price))
        entries = [
            ("family_suggestion", _rendered("p1", 1000, 50, 20)),
            ("family_schema", _rendered("p2", 2000, 70, 30)),
            ("context_values", _rendered("p3", 3000, 90, 40)),
        ]

        reserve, cached = _estimate_totals(db_session, entries)

        # (префикс * запись кэша + строка * вход + ответ * выход) / 1e6 по каждому виду
        assert reserve == Decimal("0.002130") + Decimal("0.041900") + Decimal("0.625000")
        assert cached == Decimal("0.003130") + Decimal("0.061900") + Decimal("0.925000")


# ---------------------------------------------------------------------------
#  Волна после расширений
# ---------------------------------------------------------------------------

def _wave_world(db, factories, *, contexts=3):
    family = _active_family(db)
    schema = _frozen_schema(db, factories, family, [(1, "Толщина", ["50 мм", "100 мм"])])
    parameter_id = db.execute(
        sa.text("SELECT id FROM family_parameters WHERE schema_id = :s"), {"s": schema.id}
    ).scalar_one()
    value_id = db.execute(
        sa.text("SELECT id FROM family_parameter_values WHERE parameter_id = :p ORDER BY id LIMIT 1"),
        {"p": parameter_id},
    ).scalar_one()
    rows = []
    for _ in range(contexts):
        context_id = _context(db, factories, family=family)
        variant = _variant(db, family, schema)
        paths = load_values_material(db, [context_id])[context_id].paths
        _attach(db, context_id, variant, paths_hash=paths_hash_of(paths))
        rows.append(context_id)
    return SimpleNamespace(
        family=family, schema=schema, parameter_id=parameter_id, value_id=value_id, contexts=rows
    )


def _set_cpv(db, world, context_id, value_id):
    db.add(
        ContextParameterValue(
            context_id=context_id, schema_id=world.schema.id, parameter_id=world.parameter_id,
            value_id=value_id, source="none" if value_id is None else "name",
        )
    )
    db.flush()


class TestExtensionWave:
    def test_empty_value_contexts_get_jobs_and_filled_ones_do_not(self, db_session, factories):
        world = _wave_world(db_session, factories)
        empty, filled, another_empty = world.contexts
        _set_cpv(db_session, world, empty, None)
        _set_cpv(db_session, world, filled, world.value_id)
        _set_cpv(db_session, world, another_empty, None)

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 2
        jobs = _jobs(db_session, kind=_VALUES)
        assert sorted(j.context_id for j in jobs) == sorted([empty, another_empty])
        assert all(j.status == "pending" for j in jobs)

    def test_path_conflict_context_gets_no_wave_job_but_a_none_context_does(
        self, db_session, factories
    ):
        world = _wave_world(db_session, factories, contexts=2)
        conflict, absent = world.contexts
        for context_id, source in ((conflict, "path_conflict"), (absent, "none")):
            db_session.add(
                ContextParameterValue(
                    context_id=context_id, schema_id=world.schema.id,
                    parameter_id=world.parameter_id, value_id=None, source=source,
                )
            )
        db_session.flush()

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 1
        assert [j.context_id for j in _jobs(db_session, kind=_VALUES)] == [absent]

    def test_wave_ignores_need_and_uses_the_current_fingerprint(self, db_session, factories):
        world = _wave_world(db_session, factories, contexts=1)
        [context_id] = world.contexts
        _set_cpv(db_session, world, context_id, None)

        schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        [job] = _jobs(db_session, kind=_VALUES)
        assert job.request_hash == _values_hash(db_session, context_id)
        assert job.schema_id == world.schema.id

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_open_job_of_the_family_stops_the_wave(self, db_session, factories, status):
        world = _wave_world(db_session, factories, contexts=2)
        busy, empty = world.contexts
        _set_cpv(db_session, world, empty, None)
        _job(db_session, kind=_VALUES, request_hash="other", status=status, context_id=busy,
             schema_id=world.schema.id, paths_hash="p")

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 0
        assert [j.context_id for j in _jobs(db_session, kind=_VALUES)] == [busy]

    @pytest.mark.parametrize("status", ["done", "error", "privacy_hold"])
    def test_jobs_that_are_not_pending_or_running_do_not_stop_the_wave(
        self, db_session, factories, status
    ):
        world = _wave_world(db_session, factories, contexts=2)
        other, empty = world.contexts
        _set_cpv(db_session, world, empty, None)
        _job(db_session, kind=_VALUES, request_hash="other", status=status, context_id=other,
             schema_id=world.schema.id, paths_hash="p")

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 1

    def test_open_job_of_another_family_does_not_stop_the_wave(self, db_session, factories):
        world = _wave_world(db_session, factories, contexts=1)
        [empty] = world.contexts
        _set_cpv(db_session, world, empty, None)
        foreign = _wave_world(db_session, factories, contexts=1)
        _job(db_session, kind=_VALUES, request_hash="other", status="running",
             context_id=foreign.contexts[0], schema_id=foreign.schema.id, paths_hash="p")

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 1

    def test_second_wave_with_unchanged_state_posts_nothing(self, db_session, factories):
        world = _wave_world(db_session, factories, contexts=1)
        _set_cpv(db_session, world, world.contexts[0], None)
        args = dict(family_id=world.family.id, parameter_id=world.parameter_id)
        schedule_extension_wave(db_session, **args)

        assert schedule_extension_wave(db_session, **args) == 0


class TestApplyValuesSchedulesTheWaveOnce:
    def test_only_the_last_handler_of_the_family_posts_the_wave(self, db_session, factories):
        from services.variant_answer import ValueItem, ValuesAnswer
        from services.work_variants import JobGuard, apply_values
        from tests.integration.test_work_variants_core import _guarded, _settings

        world = _wave_world(db_session, factories, contexts=3)
        first, second, empty = world.contexts
        _set_cpv(db_session, world, empty, None)
        for context_id in (first, second):
            _set_cpv(db_session, world, context_id, None)
        first_job, first_guard = _guarded(db_session, context_id=first, schema_id=world.schema.id)
        second_job, second_guard = _guarded(db_session, context_id=second, schema_id=world.schema.id)
        guards = {first: first_guard, second: second_guard}

        def handle(context_id, text):
            answer = ValuesAnswer(items=(ValueItem(1, "new", text, "name"),))
            return apply_values(
                db_session, context_id=context_id, schema_id=world.schema.id, answer=answer,
                paths_hash=paths_hash_of(
                    load_values_material(db_session, [context_id])[context_id].paths
                ),
                guard=guards[context_id], settings=_settings(),
            )

        handle(first, "150 мм")
        assert [j.context_id for j in _jobs(db_session, kind=_VALUES, status="pending")] == []

        # Расширение первого обработчика изменило схему в теле запроса второго.
        fresh = _values_hash(db_session, second)
        second_job.request_hash = fresh
        guards[second] = JobGuard(
            job_id=second_job.id, claim_token=second_job.claim_token, expected_request_hash=fresh
        )
        db_session.flush()

        handle(second, "200 мм")
        pending = _jobs(db_session, kind=_VALUES, status="pending")
        assert [j.context_id for j in pending] == [empty]
        assert first_job.status == "done" and second_job.status == "done"


# ---------------------------------------------------------------------------
#  Список модулей, которым разрешена запись
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_work_variants_is_allowed(self):
        assert "services.work_variants" in RECONCILE_ALLOWLIST


# ---------------------------------------------------------------------------
#  Схема без параметров: модель не вызывается, резерв 0
# ---------------------------------------------------------------------------

class TestZeroParameterSchemaCostsNothing:
    def _contexts(self, db, factories, params, count=5):
        family = _active_family(db)
        _frozen_schema(db, factories, family, [list(p) for p in params])
        return [_context(db, factories, family=family) for _ in range(count)]

    def test_many_contexts_of_a_zero_parameter_version_reserve_nothing(
        self, db_session, factories
    ):
        ids = self._contexts(db_session, factories, params=())

        fingerprints, reserve, cached = reconcile_module.estimate_enqueue(db_session, ids)

        assert len(fingerprints) == 5
        assert (reserve, cached) == (Decimal("0"), Decimal("0"))

    def test_the_same_contexts_with_a_parameter_reserve_money(self, db_session, factories):
        ids = self._contexts(db_session, factories, params=((1, "Толщина", ["50 мм"]),))

        _fingerprints, reserve, cached = reconcile_module.estimate_enqueue(db_session, ids)

        assert reserve > 0 and cached > 0

    def test_they_still_count_toward_the_context_cap(self, db_session, factories):
        ids = self._contexts(db_session, factories, params=())
        cap = EventCap(max_contexts=4, max_reserve_usd=Decimal("1000000"))

        report = reconcile_context_values(db_session, ids, cap=cap, source="operation")

        assert report.created == 0 and report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert (batch.contexts_count, batch.reserve_estimate_usd) == (5, Decimal("0"))

    def test_three_element_entry_marks_a_free_job(self, db_session):
        rendered = _rendered("pz", 1000, 50, 20)
        paid, _ = _estimate_totals(db_session, [("context_values", rendered)])
        free, _ = _estimate_totals(db_session, [("context_values", rendered, True)])
        assert paid > 0 and free == 0


# ---------------------------------------------------------------------------
#  Пути A -> B -> A: выполненное задание текущего отпечатка ставится заново
# ---------------------------------------------------------------------------

class TestPathsGoBackToAnEarlierSet:
    def _run(self, db, context_id, schema_id):
        """Сверка, затем исполнитель берёт созданное задание и обработчик
        применяет ответ."""
        from services.variant_answer import ValueItem, ValuesAnswer
        from services.work_variants import JobGuard, apply_values
        from tests.integration.test_work_variants_core import _settings

        reconcile_context_values(db, [context_id], cap=NO_CAP, source="operation")
        [job] = _jobs(db, kind=_VALUES, context_id=context_id, status="pending")
        token = uuid.uuid4()
        job.status = "running"
        job.claim_token = token
        db.flush()
        outcome = apply_values(
            db, context_id=context_id, schema_id=schema_id,
            answer=ValuesAnswer(items=(ValueItem(1, "value", "50 мм", "name"),)),
            paths_hash=job.paths_hash,
            guard=JobGuard(job_id=job.id, claim_token=token, expected_request_hash=job.request_hash),
            settings=_settings(),
        )
        assert outcome.applied
        return job.id

    def _rename(self, db, old, new):
        db.execute(
            sa.text(
                "UPDATE position_items SET job_title_in_proposal = :new "
                "WHERE is_chapter AND job_title_in_proposal = :old"
            ),
            {"new": new, "old": old},
        )
        db.flush()

    def test_returning_to_the_first_paths_revives_the_done_job_of_that_fingerprint(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        schema = _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        title_a, title_b = f"Путь-А-{_uid()}", f"Путь-Б-{_uid()}"
        context_id = _context(
            db_session, factories, family=family, path_specs=[(("Секция", title_a), 1)]
        )
        first = self._run(db_session, context_id, schema.id)
        hash_a = db_session.get(SemanticJob, first).request_hash
        self._rename(db_session, title_a, title_b)
        second = self._run(db_session, context_id, schema.id)
        assert second != first
        assert _jobs(db_session, kind=_VALUES, context_id=context_id, status="pending") == []
        self._rename(db_session, title_b, title_a)

        report = reconcile_context_values(
            db_session, [context_id], cap=NO_CAP, source="operation"
        )

        assert report == ReconcileReport(0, 1, 0, 0, 0, None)
        [revived] = _jobs(db_session, kind=_VALUES, context_id=context_id, status="pending")
        assert revived.id == first and revived.request_hash == hash_a
        assert revived.retry_generation == 1 and revived.attempts_in_generation == 0

    def test_not_needed_context_keeps_its_done_job(self, db_session, factories):
        family = _active_family(db_session)
        schema = _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        context_id = _context(db_session, factories, family=family)
        first = self._run(db_session, context_id, schema.id)

        report = reconcile_context_values(
            db_session, [context_id], cap=NO_CAP, source="operation"
        )

        assert report == _ZERO
        assert _jobs(db_session, kind=_VALUES, context_id=context_id)[0].id == first
        assert _jobs(db_session, kind=_VALUES, status="pending") == []


# ---------------------------------------------------------------------------
#  Дополнения ревью задачи 6: входы, которые снятия защит оставляли зелёными
# ---------------------------------------------------------------------------

class TestSchemaJobOfAnotherVersionWhileBuilding:
    def test_pending_job_of_the_frozen_version_is_cancelled_while_a_building_version_exists(
        self, db_session, factories
    ):
        """Семья активна и уже пересобирается: задание прежней (не `building`)
        версии неприменимо, задание `building`-версии — нет. Хэш у обоих один,
        различает их только версия."""
        family = _active_family(db_session)
        frozen = _schema(db_session, factories, family, version=1, status="frozen")
        building = _schema(db_session, factories, family, version=2, status="building")
        current_hash = _schema_hash(db_session, family.id, building.id)
        stale = _job(db_session, kind=_SCHEMA, request_hash=current_hash,
                     family_id=family.id, schema_id=frozen.id)
        current = _job(db_session, kind=_SCHEMA, request_hash=current_hash,
                       family_id=family.id, schema_id=building.id)

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(0, 0, 1, 0, 0, None)
        db_session.expire_all()
        stale = db_session.get(SemanticJob, stale.id)
        assert (stale.status, stale.cancel_reason) == ("cancelled", "not_applicable")
        assert db_session.get(SemanticJob, current.id).status == "pending"


class TestReadinessCountsOnlySuggestionJobs:
    @pytest.mark.parametrize("kind", [_VALUES, _SCHEMA])
    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_open_job_of_another_kind_in_the_unit_does_not_block(
        self, db_session, factories, kind, status
    ):
        unit = _unit_id(db_session, "M2")
        family = _active_family(db_session, unit_id=unit)
        neighbour = _active_family(db_session, unit_id=unit)
        schema = _schema(db_session, factories, neighbour, version=1,
                         status="frozen" if kind == _VALUES else "building")
        context_id = _context(db_session, factories, unit_id=unit)
        subject = (
            dict(context_id=context_id, paths_hash="p") if kind == _VALUES
            else dict(family_id=neighbour.id)
        )
        _job(db_session, kind=kind, request_hash="o", status=status, schema_id=schema.id,
             unit_id=unit, **subject)

        assert schema_ready_to_build(db_session, family.id) is True


class TestSchemaVersionThatCouldNotBeObtained:
    def test_no_schema_job_is_written_without_a_version(self, db_session, monkeypatch):
        """Параллельная сверка заняла номер версии и её `building` уже нет —
        задание схемы без версии не пишется (CHECK требует `schema_id`)."""
        family = _active_family(db_session)
        monkeypatch.setattr(reconcile_module, "_ensure_building_version", lambda db, fid: None)

        report = reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        assert report.created == 0
        assert _jobs(db_session, kind=_SCHEMA) == []


class TestExtensionWaveScope:
    def test_context_waiting_for_another_family_is_not_in_the_wave(self, db_session, factories):
        world = _wave_world(db_session, factories, contexts=2)
        leaving, empty = world.contexts
        _set_cpv(db_session, world, leaving, None)
        _set_cpv(db_session, world, empty, None)
        target = _active_family(db_session)
        _frozen_schema(db_session, factories, target, [(1, "Тип", ["а"])])
        _bind(db_session, factories, leaving, pending=target)

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 1
        assert [j.context_id for j in _jobs(db_session, kind=_VALUES)] == [empty]

    def test_wave_over_the_event_cap_is_held_and_posts_nothing(
        self, db_session, factories, monkeypatch
    ):
        world = _wave_world(db_session, factories, contexts=2)
        for context_id in world.contexts:
            _set_cpv(db_session, world, context_id, None)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 0
        assert _jobs(db_session, kind=_VALUES) == []
        [batch] = db_session.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert (batch.status, batch.contexts_count) == ("held", 2)

    def test_revived_job_of_the_current_fingerprint_counts_as_posted(
        self, db_session, factories
    ):
        world = _wave_world(db_session, factories, contexts=1)
        [context_id] = world.contexts
        _set_cpv(db_session, world, context_id, None)
        job = _job(
            db_session, kind=_VALUES, request_hash=_values_hash(db_session, context_id),
            status="cancelled", cancel_reason="input_changed", context_id=context_id,
            schema_id=world.schema.id, paths_hash="p",
        )

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameter_id
        )

        assert posted == 1
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "pending"


class TestApplyValuesWithoutExtension:
    def test_handler_answering_from_the_list_posts_no_wave(self, db_session, factories):
        from services.variant_answer import ValueItem, ValuesAnswer
        from services.work_variants import apply_values
        from tests.integration.test_work_variants_core import _guarded, _settings

        world = _wave_world(db_session, factories, contexts=2)
        handled, empty = world.contexts
        _set_cpv(db_session, world, handled, None)
        _set_cpv(db_session, world, empty, None)
        _job_row, guard = _guarded(db_session, context_id=handled, schema_id=world.schema.id)

        outcome = apply_values(
            db_session, context_id=handled, schema_id=world.schema.id,
            answer=ValuesAnswer(items=(ValueItem(1, "value", "50 мм", "name"),)),
            paths_hash=paths_hash_of(load_values_material(db_session, [handled])[handled].paths),
            guard=guard, settings=_settings(),
        )

        assert outcome.applied and outcome.values_added == ()
        assert _jobs(db_session, kind=_VALUES, status="pending") == []


class TestApproveBatchWithASchemaSubject:
    def test_context_jobs_get_the_batch_and_the_schema_job_is_created(
        self, db_session, factories
    ):
        """Пачка из трёх видов подтверждается: задания предметов-контекстов
        получают `batch_id`, задание схемы ставит сверка подтверждения (его
        `batch_id` — задача 7: подтверждение по предмету каждого вида)."""
        from services.semantic_decisions import approve_batch, preview_batch

        world = _joint_scene(db_session, factories)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))
        held = reconcile_semantic_jobs(
            db_session, [world.a, world.b], cap=cap, source="operation"
        ).held_batch_id
        actor = factories.UserFactory.create().id
        preview = preview_batch(db_session, batch_id=held)

        approve_batch(db_session, batch_id=held, preview_hash=preview.preview_hash, actor_id=actor)

        jobs = {(j.kind, j.context_id, j.family_id): j.batch_id for j in _jobs(db_session)}
        assert jobs == {
            (_SUGGESTION, world.a, None): held,
            (_VALUES, world.b, None): held,
            (_SCHEMA, None, world.f3.id): None,
        }


class TestJobAuditColumnsByKind:
    """Колонки журнала задания нового вида — от его вида: модель и версия промпта
    профиля вида, единица — контекста (значения) или семьи (схема)."""

    def test_values_job_carries_the_values_profile_and_the_context_unit(
        self, db_session, factories, monkeypatch
    ):
        from services.variant_request import VALUES_PROMPT_VERSION

        monkeypatch.setattr(settings, "SEMANTIC_VALUES_MODEL", "values-model-under-test")

        unit = _unit_id(db_session, "M2")
        family = _active_family(db_session, unit_id=unit)
        _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        context_id = _context(db_session, factories, family=family, unit_id=unit)

        reconcile_context_values(db_session, [context_id], cap=NO_CAP, source="operation")

        [job] = _jobs(db_session, kind=_VALUES)
        assert (job.model_requested, job.prompt_version, job.unit_id) == (
            settings.SEMANTIC_VALUES_MODEL, str(VALUES_PROMPT_VERSION), unit,
        )

    def test_schema_job_carries_the_schema_profile_and_the_family_unit(
        self, db_session, factories, monkeypatch
    ):
        from services.variant_request import SCHEMA_PROMPT_VERSION

        monkeypatch.setattr(settings, "SEMANTIC_SCHEMA_MODEL", "schema-model-under-test")

        unit = _unit_id(db_session, "M2")
        family = _active_family(db_session, unit_id=unit)

        reconcile_family_schemas(db_session, [family.id], cap=NO_CAP, source="operation")

        [job] = _jobs(db_session, kind=_SCHEMA)
        assert (job.model_requested, job.prompt_version, job.unit_id) == (
            settings.SEMANTIC_SCHEMA_MODEL, str(SCHEMA_PROMPT_VERSION), unit,
        )


class TestSchemaScopeOfAUnitWithACode:
    def test_family_of_the_context_unit_gets_its_schema_by_the_unit_branch(
        self, db_session, factories
    ):
        """Единица контекста задана (не `NULL`): семья этой единицы попадает в
        область только ветвью «активные семьи единиц» — после правки 2 своих
        семей контекста в области нет. Соседняя семья другой единицы — нет."""
        unit = _unit_id(db_session, "M2")
        other = _unit_id(db_session, "PCS")
        family = _active_family(db_session, unit_id=unit)
        _active_family(db_session, unit_id=other)
        context_id = _context(db_session, factories, family=family, unit_id=unit)

        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert [j.family_id for j in _jobs(db_session, kind=_SCHEMA)] == [family.id]

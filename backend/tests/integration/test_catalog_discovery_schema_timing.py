"""Схема семьи после открытия (спека 3б §2.16): схема не строится из нуля
наименований; «не построенная» пустая версия пересобирается сверкой; опустевший
материал строящейся версии не даёт пустого задания и пустой заморозки; отменённое
восстановление само не повторяется.

У каждого правила предъявлены обе стороны: вход, где оно срабатывает, и соседний
вход, где не должно. Хэш пустого списка наименований сверяется независимым
литералом sha256, а не кодом, который его вычисляет.
"""
# ruff: noqa: F811 — `world`, `act` — фикстуры, импортированные из соседних наборов
from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from models import (
    CatalogContext,
    FamilyParameter,
    FamilyParameterSchema,
    SemanticEvent,
    SemanticJob,
    SemanticReconcileBatch,
    WorkFamily,
)
from services.semantic_decisions import confirm_unit_reask, preview_unit_reask
from services.semantic_reconcile import (
    EMPTY_NAMES_CANDIDATES_HASH,
    NO_CAP,
    _unbuilt_frozen_exists,
    families_awaiting_schema,
    reconcile_family_schemas,
    without_discarded_schemas,
)
from services.semantic_worker import sweep_semantic_queue
from services.variant_request import load_schema_material, render_schema_request
from services.work_variants import (
    JobGuard,
    cancel_schema_build,
    freeze_schema,
    rebuild_schema,
)
from tests.integration.test_catalog_discovery_activate import _call, act  # noqa: F401
from tests.integration.test_catalog_discovery_drafts import world  # noqa: F401
from tests.integration.test_semantic_queue_api import _published as _published_suggestion
from tests.integration.test_work_variants_concurrency import _freeze_answer
from tests.integration.test_work_variants_material import _context as _row
from tests.integration.test_work_variants_material import _settings, _unit_id
from tests.integration.test_work_variants_reconcile import _active_family, _job, _jobs
from tests.integration.test_work_variants_reconcile import _context as _values_context
from tests.integration.test_work_variants_schema import _param, _schema, _value
from tests.integration.test_work_variants_worker import (
    _SCHEMA_ANSWER,
    NOW,
    S,
    _attempts,
    _claim_one,
    _reload,
    _response,
    _schema_world,
)

pytestmark = pytest.mark.integration

_SCHEMA = "family_schema"

#: sha256 канонического JSON пустого списка `[]` — независимый от кода литерал.
_EMPTY_LIST_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

#: Другой `candidates_hash`: список наименований у задания был непуст.
_NON_EMPTY_LIST_HASH = "a" * 64

_SWEEP_SETTINGS = app_settings.model_copy(
    update={"SEMANTIC_SWEEP_INTERVAL_S": 300, "SEMANTIC_EVENT_MAX_CONTEXTS": 3000}
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _reconcile(db, family) -> object:
    return reconcile_family_schemas(db, [family.id], cap=NO_CAP, source="operation")


def _versions(db, family) -> list[tuple[int, str]]:
    db.expire_all()
    return [
        (row.version, row.status)
        for row in db.execute(
            sa.select(FamilyParameterSchema.version, FamilyParameterSchema.status)
            .where(FamilyParameterSchema.family_id == family.id)
            .order_by(FamilyParameterSchema.version)
        ).all()
    ]


def _schema_jobs(db, family) -> list[SemanticJob]:
    return _jobs(db, kind=_SCHEMA, family_id=family.id)


def _unbuilt_version(
    db, factories, family, *, version=1, origin="model", job_hashes=(_EMPTY_LIST_SHA256,),
    parameters=False,
):
    """Текущая `frozen` версия и её выполненные задания схемы: `job_hashes` —
    `candidates_hash` каждого, по порядку выполнения (последний — «последнее
    выполненное»)."""
    schema = _schema(db, factories, family, version=version, status="frozen", origin=origin)
    if parameters:
        _value(db, _param(db, schema, 1, "Толщина"), "50 мм")
    for candidates_hash in job_hashes:
        job = _job(
            db, kind=_SCHEMA, request_hash=f"done-{uuid.uuid4().hex}", status="done",
            family_id=family.id, schema_id=schema.id,
        )
        job.candidates_hash = candidates_hash
    db.flush()
    return schema


def _named_family(db, factories, *, title=None, unit_id=None):
    """Активная семья и одна строка-член: в материале схемы есть наименование."""
    family = _active_family(db, unit_id=unit_id)
    _row(db, factories, title or f"Стяжка {uuid.uuid4().hex[:8]}", work=family, unit_id=unit_id)
    return family


def _unbuilt_family(db, factories, **kwargs):
    family = _named_family(db, factories)
    _unbuilt_version(db, factories, family, **kwargs)
    return family


def _pub(db, factories, context, family, decision=None):
    """Опубликованное предложение семьи на контекст; задание предложения —
    выполненное (единица не занята)."""
    decided_by = None if decision is None else factories.UserFactory.create().id
    return _published_suggestion(
        db, context.id, family_id=family.id, decision=decision, decided_by=decided_by
    )


def _reject(db, factories, suggestion) -> None:
    """Решение «отклонено»: имя строки из материала схемы уходит."""
    author_id = factories.UserFactory.create().id
    suggestion.decision = "rejected"
    suggestion.decided_at = dt.datetime.now(dt.UTC)
    suggestion.decided_by = author_id
    db.flush()


def _restore(db, suggestion) -> None:
    suggestion.decision = None
    suggestion.decided_at = None
    suggestion.decided_by = None
    db.flush()


def _awaiting(db) -> list[int]:
    return families_awaiting_schema(db)


def _guard_hash(db, family, schema) -> str:
    return render_schema_request(
        load_schema_material(db, family.id, schema.id), settings=S
    ).request_hash


# ---------------------------------------------------------------------------
#  Хэш пустого списка
# ---------------------------------------------------------------------------

class TestEmptyNamesHash:
    def test_constant_is_the_sha256_of_the_empty_json_list(self):
        assert EMPTY_NAMES_CANDIDATES_HASH == _EMPTY_LIST_SHA256

    def test_a_rendered_schema_request_without_names_carries_it(self, db_session, factories):
        family = _active_family(db_session)

        rendered = render_schema_request(
            load_schema_material(db_session, family.id, 0), settings=app_settings
        )

        assert rendered.candidates_hash == _EMPTY_LIST_SHA256

    def test_a_rendered_schema_request_with_a_name_does_not(self, db_session, factories):
        family = _named_family(db_session, factories)

        rendered = render_schema_request(
            load_schema_material(db_session, family.id, 0), settings=app_settings
        )

        assert rendered.candidates_hash != _EMPTY_LIST_SHA256


# ---------------------------------------------------------------------------
#  Правило 1: схема не строится из нуля наименований
# ---------------------------------------------------------------------------

class TestSchemaIsNotBuiltFromZeroNames:
    def test_a_family_without_any_rows_gets_no_version_and_no_job(self, db_session, factories):
        family = _active_family(db_session)

        report = _reconcile(db_session, family)

        assert (report.created, report.held_batch_id) == (0, None)
        assert _versions(db_session, family) == []
        assert _schema_jobs(db_session, family) == []

    def test_it_stays_in_the_waiting_list(self, db_session, factories):
        family = _active_family(db_session)

        _reconcile(db_session, family)

        assert family.id in _awaiting(db_session)

    @pytest.mark.parametrize("source", ["member", "pending_family", "published_suggestion"])
    def test_each_source_of_names_makes_the_family_get_its_version_and_job(
        self, db_session, factories, source
    ):
        family = _active_family(db_session)
        assert _reconcile(db_session, family).created == 0, "вход: без строк схемы нет"
        if source == "member":
            _row(db_session, factories, "Строка", work=family)
        elif source == "pending_family":
            _row(db_session, factories, "Строка", pending=family)
        else:
            _pub(db_session, factories, _row(db_session, factories, "Строка"), family)

        report = _reconcile(db_session, family)

        assert report.created == 1
        assert _versions(db_session, family) == [(1, "building")]
        (job,) = _schema_jobs(db_session, family)
        assert (job.status, job.schema_id is not None) == ("pending", True)
        assert job.candidates_hash != _EMPTY_LIST_SHA256

    def test_a_rejected_suggestion_is_not_a_name(self, db_session, factories):
        family = _active_family(db_session)
        _pub(
            db_session, factories, _row(db_session, factories, "Строка"), family,
            decision="rejected",
        )

        report = _reconcile(db_session, family)

        assert report.created == 0
        assert _versions(db_session, family) == []


class TestActivationThenReaskBuildsTheSchemaFromNames:
    def test_the_schema_job_of_a_new_family_waits_for_published_suggestions(self, act):
        db = act.db
        outcome = _call(act, drafts=[act.d1.id])
        (family_id,) = outcome.created_family_ids
        family = db.get(WorkFamily, family_id)
        assert _versions(db, family) == [] and _schema_jobs(db, family) == []

        preview = preview_unit_reask(db, unit_id=act.unit)
        confirm_unit_reask(
            db, unit_id=act.unit, preview_hash=preview.preview_hash, actor_id=act.admin
        )

        # Перезапрос единицы окончен, а предложения ещё не опубликованы:
        # схема из нуля наименований не строится.
        assert _schema_jobs(db, family) == []
        assert _versions(db, family) == []

        # Задания предложений единицы закрыты, а имён у семьи всё ещё нет.
        db.execute(
            sa.update(SemanticJob)
            .where(SemanticJob.kind == "family_suggestion")
            .values(status="done", cancel_reason=None)
        )
        _reconcile(db, family)
        assert _schema_jobs(db, family) == [] and _versions(db, family) == []

        _published_suggestion(db, act.s[0], family_id=family_id, superseded=True)
        _reconcile(db, family)

        (job,) = _schema_jobs(db, family)
        assert job.status == "pending"
        assert job.candidates_hash != _EMPTY_LIST_SHA256
        material = load_schema_material(db, family_id, job.schema_id)
        assert len(material.names) == 1


# ---------------------------------------------------------------------------
#  Правило 2: «не построенная» версия пересобирается
# ---------------------------------------------------------------------------

class TestUnbuiltVersionIsRebuilt:
    def test_a_frozen_empty_version_built_from_no_names_gets_a_building_version_and_a_job(
        self, db_session, factories
    ):
        family = _unbuilt_family(db_session, factories)

        report = _reconcile(db_session, family)

        assert report.created == 1
        assert _versions(db_session, family) == [(1, "frozen"), (2, "building")]
        new = db_session.execute(
            sa.select(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == family.id, FamilyParameterSchema.version == 2
            )
        ).scalar_one()
        assert new.origin == "model"
        [job] = [j for j in _schema_jobs(db_session, family) if j.status == "pending"]
        assert job.schema_id == new.id
        assert job.candidates_hash != _EMPTY_LIST_SHA256

    def test_the_rebuild_makes_the_same_versions_as_the_manual_one(self, db_session, factories):
        manual = _named_family(db_session, factories, title="Ручная")
        _unbuilt_version(db_session, factories, manual)
        auto = _unbuilt_family(db_session, factories)
        actor = factories.UserFactory.create().id

        rebuild_schema(db_session, family_id=manual.id, actor_id=actor)
        _reconcile(db_session, auto)

        assert _versions(db_session, manual) == _versions(db_session, auto)
        assert [(j.status, j.cancel_reason) for j in _schema_jobs(db_session, manual)][-1] == [
            (j.status, j.cancel_reason) for j in _schema_jobs(db_session, auto)
        ][-1]

    def test_the_old_version_stays_current_until_the_new_one_is_frozen(
        self, db_session, factories
    ):
        family = _unbuilt_family(db_session, factories)
        old = db_session.execute(
            sa.select(FamilyParameterSchema).where(FamilyParameterSchema.family_id == family.id)
        ).scalar_one()
        _reconcile(db_session, family)
        db_session.expire_all()
        assert db_session.get(FamilyParameterSchema, old.id).status == "frozen"
        new = db_session.execute(
            sa.select(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == family.id,
                FamilyParameterSchema.status == "building",
            )
        ).scalar_one()

        outcome = freeze_schema(
            db_session, schema_id=new.id, answer=_freeze_answer(), guard=None, settings=_settings()
        )

        assert outcome.applied is True
        assert _versions(db_session, family) == [(1, "superseded"), (2, "frozen")]
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameter).where(
                FamilyParameter.schema_id == new.id
            )
        ).scalar_one() > 0

    def test_values_jobs_follow_the_rebuilt_schema_as_after_a_manual_rebuild(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        context_id = _values_context(db_session, factories, family=family)
        _unbuilt_version(db_session, factories, family)
        _reconcile(db_session, family)
        new = db_session.execute(
            sa.select(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == family.id,
                FamilyParameterSchema.status == "building",
            )
        ).scalar_one()

        freeze_schema(
            db_session, schema_id=new.id, answer=_freeze_answer(), guard=None, settings=_settings()
        )

        values_jobs = _jobs(db_session, kind="context_values", context_id=context_id)
        assert [job.schema_id for job in values_jobs] == [new.id]

    def test_a_second_pass_changes_nothing(self, db_session, factories):
        family = _unbuilt_family(db_session, factories)
        _reconcile(db_session, family)

        again = _reconcile(db_session, family)

        assert (again.created, again.revived, again.cancelled) == (0, 0, 0)
        assert _versions(db_session, family) == [(1, "frozen"), (2, "building")]

    @pytest.mark.parametrize(
        ("case", "kwargs"),
        [
            ("built_from_a_non_empty_list", dict(job_hashes=(_NON_EMPTY_LIST_HASH,))),
            ("has_parameters", dict(parameters=True)),
            ("manual_origin", dict(origin="manual")),
            ("no_done_job_at_all", dict(job_hashes=())),
        ],
    )
    def test_other_empty_or_current_versions_are_not_rebuilt(
        self, db_session, factories, case, kwargs
    ):
        family = _unbuilt_family(db_session, factories, **kwargs)

        report = _reconcile(db_session, family)

        assert (report.created, report.revived) == (0, 0), case
        assert _versions(db_session, family) == [(1, "frozen")]
        assert family.id not in _awaiting(db_session)

    def test_the_last_done_job_decides(self, db_session, factories):
        """Два выполненных задания у версии: решает последнее."""
        older_empty = _unbuilt_family(
            db_session, factories, job_hashes=(_EMPTY_LIST_SHA256, _NON_EMPTY_LIST_HASH)
        )
        older_filled = _unbuilt_family(
            db_session, factories, job_hashes=(_NON_EMPTY_LIST_HASH, _EMPTY_LIST_SHA256)
        )

        _reconcile(db_session, older_empty)
        _reconcile(db_session, older_filled)

        assert _versions(db_session, older_empty) == [(1, "frozen")]
        assert _versions(db_session, older_filled) == [(1, "frozen"), (2, "building")]

    def test_a_cancelled_job_of_the_version_is_not_the_marker(self, db_session, factories):
        family = _named_family(db_session, factories)
        schema = _schema(db_session, factories, family, version=1, status="frozen")
        job = _job(
            db_session, kind=_SCHEMA, request_hash="x", status="cancelled",
            family_id=family.id, schema_id=schema.id,
        )
        job.candidates_hash = _EMPTY_LIST_SHA256
        db_session.flush()

        report = _reconcile(db_session, family)

        assert report.created == 0 and _versions(db_session, family) == [(1, "frozen")]

    def test_a_unit_that_is_still_reasked_waits_and_then_gets_the_rebuild(
        self, db_session, factories
    ):
        unit = _unit_id(db_session, "M2")
        family = _named_family(db_session, factories, unit_id=unit)
        _unbuilt_version(db_session, factories, family)
        context_id = db_session.execute(
            sa.select(CatalogContext.id).where(CatalogContext.work_family_id == family.id)
        ).scalar_one()
        busy = _job(
            db_session, kind="family_suggestion", request_hash="s", context_id=context_id,
            unit_id=unit,
        )

        waiting = _reconcile(db_session, family)

        assert waiting.created == 0 and _versions(db_session, family) == [(1, "frozen")]
        busy.status = "done"
        db_session.flush()
        ready = _reconcile(db_session, family)
        assert ready.created == 1
        assert _versions(db_session, family) == [(1, "frozen"), (2, "building")]

    def test_an_unbuilt_family_without_names_waits(self, db_session, factories):
        family = _active_family(db_session)
        _unbuilt_version(db_session, factories, family)

        report = _reconcile(db_session, family)

        assert report.created == 0 and _versions(db_session, family) == [(1, "frozen")]
        assert family.id in _awaiting(db_session)


class TestWaitingListIncludesUnbuiltFamilies:
    def test_unbuilt_family_is_in_and_the_neighbours_are_out(self, db_session, factories):
        unbuilt = _unbuilt_family(db_session, factories)
        filled = _unbuilt_family(db_session, factories, job_hashes=(_NON_EMPTY_LIST_HASH,))
        with_parameters = _unbuilt_family(db_session, factories, parameters=True)
        manual = _unbuilt_family(db_session, factories, origin="manual")
        no_version = _active_family(db_session)
        rebuilding = _unbuilt_family(db_session, factories)
        _schema(db_session, factories, rebuilding, version=2, status="building")

        waiting = set(_awaiting(db_session))

        assert unbuilt.id in waiting and no_version.id in waiting
        assert not {filled.id, with_parameters.id, manual.id, rebuilding.id} & waiting

    def test_the_predicate_agrees_with_the_waiting_list_for_a_frozen_version(
        self, db_session, factories
    ):
        unbuilt = _unbuilt_family(db_session, factories)
        filled = _unbuilt_family(db_session, factories, job_hashes=(_NON_EMPTY_LIST_HASH,))

        found = set(
            db_session.execute(
                sa.select(WorkFamily.id).where(_unbuilt_frozen_exists())
            ).scalars()
        )

        assert unbuilt.id in found and filled.id not in found


class TestSweepRebuildsUnbuiltVersions:
    def test_a_sweep_rebuilds_without_a_manual_action(
        self, committing_session_factory, committing_db, committing_factories
    ):
        family = _unbuilt_family(committing_db, committing_factories)
        family_id = family.id
        committing_db.commit()
        assert _versions(committing_db, family) == [(1, "frozen")]

        sweep_semantic_queue(committing_session_factory, settings=_SWEEP_SETTINGS)

        family = committing_db.get(WorkFamily, family_id)
        assert _versions(committing_db, family) == [(1, "frozen"), (2, "building")]
        [job] = [j for j in _schema_jobs(committing_db, family) if j.status == "pending"]
        assert job.candidates_hash != _EMPTY_LIST_SHA256

    def test_a_sweep_leaves_a_version_built_from_names_alone(
        self, committing_session_factory, committing_db, committing_factories
    ):
        family = _unbuilt_family(
            committing_db, committing_factories, job_hashes=(_NON_EMPTY_LIST_HASH,)
        )
        family_id = family.id
        committing_db.commit()

        sweep_semantic_queue(committing_session_factory, settings=_SWEEP_SETTINGS)

        family = committing_db.get(WorkFamily, family_id)
        assert _versions(committing_db, family) == [(1, "frozen")]


# ---------------------------------------------------------------------------
#  Правило 2а: опустевший материал строящейся версии
# ---------------------------------------------------------------------------

def _building_family_with_a_suggestion(db, factories):
    """Версия `building` и её задание `pending`; наименование — из
    опубликованного предложения (его можно отклонить, и материал опустеет)."""
    family = _active_family(db)
    suggestion = _pub(db, factories, _row(db, factories, "Строка"), family)
    _reconcile(db, family)
    [job] = _schema_jobs(db, family)
    assert (job.status, _versions(db, family)) == ("pending", [(1, "building")])
    return family, suggestion, job


class TestEmptiedMaterialOfABuildingVersion:
    def test_the_pending_job_is_cancelled_no_empty_job_is_posted_and_the_version_waits(
        self, db_session, factories
    ):
        family, suggestion, job = _building_family_with_a_suggestion(db_session, factories)
        _reject(db_session, factories, suggestion)

        _reconcile(db_session, family)

        jobs = _schema_jobs(db_session, family)
        assert [(j.id, j.status, j.cancel_reason) for j in jobs] == [
            (job.id, "cancelled", "not_applicable")
        ]
        assert all(j.candidates_hash != _EMPTY_LIST_SHA256 for j in jobs)
        assert _versions(db_session, family) == [(1, "building")]

    def test_the_names_coming_back_post_a_job_with_them(self, db_session, factories):
        family, suggestion, job = _building_family_with_a_suggestion(db_session, factories)
        _reject(db_session, factories, suggestion)
        _reconcile(db_session, family)
        _restore(db_session, suggestion)

        _reconcile(db_session, family)

        pending = [j for j in _schema_jobs(db_session, family) if j.status == "pending"]
        assert len(pending) == 1
        assert pending[0].candidates_hash != _EMPTY_LIST_SHA256
        assert _versions(db_session, family) == [(1, "building")]

    def test_a_running_job_is_left_to_its_own_result(self, db_session, factories):
        family, suggestion, job = _building_family_with_a_suggestion(db_session, factories)
        job.status = "running"
        job.claim_token = uuid.uuid4()
        _reject(db_session, factories, suggestion)

        _reconcile(db_session, family)

        assert _reload(db_session, job.id).status == "running"

    def test_the_manual_rebuild_of_a_family_without_names_waits_the_same_way(
        self, db_session, factories
    ):
        family = _active_family(db_session)
        _schema(db_session, factories, family, version=1, status="frozen")
        actor = factories.UserFactory.create().id

        schema = rebuild_schema(db_session, family_id=family.id, actor_id=actor)

        assert schema.status == "building"
        assert _schema_jobs(db_session, family) == []
        _row(db_session, factories, "Строка", work=family)
        _reconcile(db_session, family)
        [job] = _schema_jobs(db_session, family)
        assert (job.schema_id, job.status) == (schema.id, "pending")

    def test_the_claim_cancels_a_job_whose_material_is_empty_and_leaves_the_version(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        db_session.get(CatalogContext, scene.context_id).archived_at = NOW
        db_session.flush()
        scene.job.request_hash = _guard_hash(db_session, scene.family, scene.schema)
        scene.job.candidates_hash = _EMPTY_LIST_SHA256
        db_session.flush()

        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is None
        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "not_applicable", None,
        )
        assert _versions(db_session, scene.family) == [(1, "building")]
        assert _attempts(db_session, job.id) == []

    def test_the_claim_takes_the_same_job_while_the_name_is_there(self, db_session, factories):
        scene = _schema_world(db_session, factories)

        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.job_id == scene.job.id

    def test_a_result_of_a_job_that_left_with_no_names_does_not_freeze_the_version(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        db_session.get(CatalogContext, scene.context_id).archived_at = NOW
        db_session.flush()
        emptied = render_schema_request(
            load_schema_material(db_session, scene.family.id, scene.schema.id), settings=S
        )
        assert emptied.candidates_hash == _EMPTY_LIST_SHA256, "вход: материал пуст"
        # Задание ушло с пустым списком: ответ ждёт тот же пустой запрос.
        claim = dataclasses.replace(claim, rendered=emptied)

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        db_session.expire_all()
        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "not_applicable", None,
        )
        assert _versions(db_session, scene.family) == [(1, "building")]
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameter)
        ).scalar_one() == 0
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticEvent).where(
                SemanticEvent.event_type == "family_schema_frozen"
            )
        ).scalar_one() == 0
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "ok"

    def test_freeze_with_a_guard_over_an_empty_material_is_not_applicable(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        db_session.get(CatalogContext, scene.context_id).archived_at = NOW
        db_session.flush()
        emptied = _guard_hash(db_session, scene.family, scene.schema)

        outcome = freeze_schema(
            db_session, schema_id=scene.schema.id, answer=_freeze_answer(),
            guard=JobGuard(
                job_id=scene.job.id, claim_token=claim.claim_token,
                expected_request_hash=emptied,
            ),
            settings=S,
        )

        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _versions(db_session, scene.family) == [(1, "building")]

    def test_a_paid_result_of_an_outdated_input_is_still_not_published(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        db_session.get(CatalogContext, scene.context_id).archived_at = NOW
        db_session.flush()

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert _versions(db_session, scene.family) == [(1, "building")]

    def test_a_result_over_names_still_freezes(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        assert _versions(db_session, scene.family) == [(1, "frozen")]


# ---------------------------------------------------------------------------
#  Правило 2б: отмена восстановления человеком
# ---------------------------------------------------------------------------

def _rebuilt_and_cancelled(db, factories):
    """«Не построенная» семья: сверка ставит восстановление, человек отменяет
    пересборку. Возвращает семью."""
    family = _unbuilt_family(db, factories)
    _reconcile(db, family)
    cancel_schema_build(db, family_id=family.id, actor_id=factories.UserFactory.create().id)
    assert _versions(db, family) == [(1, "frozen"), (2, "cancelled")]
    return family


class TestCancelledRestorationIsNotRepeated:
    def test_several_passes_without_a_change_of_input_add_nothing(self, db_session, factories):
        family = _rebuilt_and_cancelled(db_session, factories)
        jobs_before = [j.id for j in _schema_jobs(db_session, family)]

        reports = [_reconcile(db_session, family) for _ in range(3)]

        assert all((r.created, r.revived) == (0, 0) for r in reports)
        assert _versions(db_session, family) == [(1, "frozen"), (2, "cancelled")]
        assert [j.id for j in _schema_jobs(db_session, family)] == jobs_before

    def test_the_sweep_does_not_repeat_it_either(
        self, committing_session_factory, committing_db, committing_factories
    ):
        family = _rebuilt_and_cancelled(committing_db, committing_factories)
        family_id = family.id
        committing_db.commit()

        for _ in range(3):
            sweep_semantic_queue(committing_session_factory, settings=_SWEEP_SETTINGS)

        family = committing_db.get(WorkFamily, family_id)
        assert _versions(committing_db, family) == [(1, "frozen"), (2, "cancelled")]

    def test_a_cancel_by_another_path_blocks_the_same_input(self, db_session, factories):
        """Отмена считается любым путём в `cancelled`: версия `cancelled` с
        заданием того же входа, без `cancel_schema_build`."""
        family = _unbuilt_family(db_session, factories)
        current = render_schema_request(
            load_schema_material(db_session, family.id, 0), settings=app_settings
        )
        cancelled = _schema(db_session, factories, family, version=2, status="cancelled")
        job = _job(
            db_session, kind=_SCHEMA, request_hash=current.request_hash, status="cancelled",
            family_id=family.id, schema_id=cancelled.id,
        )
        job.candidates_hash = current.candidates_hash
        db_session.flush()

        report = _reconcile(db_session, family)

        assert (report.created, report.revived) == (0, 0)
        assert _versions(db_session, family) == [(1, "frozen"), (2, "cancelled")]

    def test_a_changed_input_sets_the_restoration_again(self, db_session, factories):
        family = _rebuilt_and_cancelled(db_session, factories)
        _row(db_session, factories, "Другая строка семьи", work=family)

        report = _reconcile(db_session, family)

        assert report.created == 1
        assert _versions(db_session, family) == [
            (1, "frozen"), (2, "cancelled"), (3, "building"),
        ]

    def test_an_edit_of_the_family_changes_the_input_too(self, db_session, factories):
        family = _rebuilt_and_cancelled(db_session, factories)
        family.definition = "Другое определение семьи"
        db_session.flush()

        report = _reconcile(db_session, family)

        assert report.created == 1
        assert _versions(db_session, family)[-1] == (3, "building")

    def test_an_explicit_rebuild_is_always_allowed(self, db_session, factories):
        family = _rebuilt_and_cancelled(db_session, factories)

        schema = rebuild_schema(
            db_session, family_id=family.id, actor_id=factories.UserFactory.create().id
        )

        assert (schema.version, schema.status) == (3, "building")
        assert len([j for j in _schema_jobs(db_session, family) if j.status == "pending"]) == 1

    def test_a_cancelled_version_older_than_the_current_one_does_not_block(
        self, db_session, factories
    ):
        family = _named_family(db_session, factories)
        current = render_schema_request(
            load_schema_material(db_session, family.id, 0), settings=app_settings
        )
        cancelled = _schema(db_session, factories, family, version=1, status="cancelled")
        job = _job(
            db_session, kind=_SCHEMA, request_hash=current.request_hash, status="cancelled",
            family_id=family.id, schema_id=cancelled.id,
        )
        job.candidates_hash = current.candidates_hash
        _unbuilt_version(db_session, factories, family, version=2)

        report = _reconcile(db_session, family)

        assert report.created == 1
        assert _versions(db_session, family) == [(1, "cancelled"), (2, "frozen"), (3, "building")]


def _discarded_batch(db, factories, family, request_hash):
    from tests.integration.test_semantic_queue_schema import _batch

    return _batch(
        db, factories, source="operation",
        held_fingerprints=[
            {
                "kind": _SCHEMA, "context_id": None, "family_id": family.id,
                "schema_id": None, "request_hash": request_hash,
            }
        ],
        fingerprints_hash=f"fp-{uuid.uuid4().hex}", contexts_count=1, status="discarded",
        decided_by=factories.UserFactory.create().id, decided_at=dt.datetime.now(dt.UTC),
    )


class TestDiscardedHeldBatchOfAnUnbuiltFamily:
    def _current_hash(self, db, family) -> str:
        return render_schema_request(
            load_schema_material(db, family.id, 0), settings=app_settings
        ).request_hash

    def test_the_same_input_is_not_brought_back_by_the_sweep(
        self, committing_session_factory, committing_db, committing_factories
    ):
        family = _unbuilt_family(committing_db, committing_factories)
        family_id = family.id
        _discarded_batch(
            committing_db, committing_factories, family, self._current_hash(committing_db, family)
        )
        committing_db.commit()
        assert without_discarded_schemas(committing_db, [family_id]) == []

        sweep_semantic_queue(committing_session_factory, settings=_SWEEP_SETTINGS)

        family = committing_db.get(WorkFamily, family_id)
        assert _versions(committing_db, family) == [(1, "frozen")]
        assert committing_db.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch)
        ).scalar_one() == 1

    def test_another_input_is_not_held_by_the_discard(
        self, committing_session_factory, committing_db, committing_factories
    ):
        family = _unbuilt_family(committing_db, committing_factories)
        family_id = family.id
        _discarded_batch(committing_db, committing_factories, family, "прежний-вход")
        committing_db.commit()

        sweep_semantic_queue(committing_session_factory, settings=_SWEEP_SETTINGS)

        family = committing_db.get(WorkFamily, family_id)
        assert _versions(committing_db, family) == [(1, "frozen"), (2, "building")]


# ---------------------------------------------------------------------------
#  Нет цикла: пустой ответ модели по непустому списку — окончательный
# ---------------------------------------------------------------------------

class TestNoRestorationLoop:
    def test_an_empty_answer_over_names_ends_the_restoration(self, db_session, factories):
        """Восстановление «не построенной» версии уходит с наименованиями, модель
        отвечает пустой схемой: новая пустая версия построена по непустому списку
        и больше не пересобирается — ни сверкой, ни селектором прохода."""
        family = _unbuilt_family(db_session, factories)
        _reconcile(db_session, family)
        claim = _claim_one(db_session)
        assert claim.rendered.candidates_hash != _EMPTY_LIST_SHA256, "вход: ушло с именами"

        worker.record_result(
            db_session, claim, _response('{"parameters": []}'), now=NOW, settings=S
        )
        assert _versions(db_session, family) == [(1, "superseded"), (2, "frozen")]

        reports = [_reconcile(db_session, family) for _ in range(3)]

        assert all((r.created, r.revived) == (0, 0) for r in reports)
        assert _versions(db_session, family) == [(1, "superseded"), (2, "frozen")]
        assert family.id not in _awaiting(db_session)


# ---------------------------------------------------------------------------
#  Правило 2а: имена возвращаются настоящими путями продукта, без прямой сверки
# ---------------------------------------------------------------------------

class TestNamesComeBackThroughProductPaths:
    def test_an_assigned_member_gives_the_waiting_version_its_job(self, db_session, factories):
        from services.work_families import assign_family

        family, suggestion, _job_before = _building_family_with_a_suggestion(
            db_session, factories
        )
        _reject(db_session, factories, suggestion)
        _reconcile(db_session, family)
        assert [j.status for j in _schema_jobs(db_session, family)] == ["cancelled"], (
            "вход: версия ждёт имён"
        )
        context = _row(db_session, factories, "Новая строка семьи")
        actor = factories.UserFactory.create().id

        assign_family(db_session, context_id=context.id, family_id=family.id, actor_id=actor)

        building_id = db_session.execute(
            sa.select(FamilyParameterSchema.id).where(
                FamilyParameterSchema.family_id == family.id,
                FamilyParameterSchema.status == "building",
            )
        ).scalar_one()
        pending = [j for j in _schema_jobs(db_session, family) if j.status == "pending"]
        assert [(j.schema_id, j.candidates_hash != _EMPTY_LIST_SHA256) for j in pending] == [
            (building_id, True)
        ]

    def test_a_published_suggestion_gives_the_waiting_version_its_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Модель публикует предложение семьи, чья версия `building` ждёт имён:
        сверка схем единицы после задания предложения ставит задание схемы."""
        from services.semantic_reconcile import _ensure_building_version
        from tests.integration.test_semantic_queue_worker import (
            _answer as _suggestion_answer,
        )
        from tests.integration.test_semantic_queue_worker import (
            _clock,
            _committed_scene,
            _FakeClient,
        )
        from tests.integration.test_semantic_queue_worker import (
            _response as _suggestion_response,
        )
        from tests.integration.test_work_variants_family_change import _SETTINGS_ON

        scene = _committed_scene(committing_db, committing_factories)
        committing_db.execute(
            sa.delete(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == scene.family.id
            )
        )
        building_id = _ensure_building_version(committing_db, scene.family.id)
        committing_db.commit()
        assert load_schema_material(committing_db, scene.family.id, building_id).names == ()
        assert _schema_jobs(committing_db, scene.family) == [], "вход: версия ждёт имён"
        client = _FakeClient(_suggestion_response(_suggestion_answer(scene.family.id, "0.1")))

        worker.process_one(committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock)

        pending = [j for j in _schema_jobs(committing_db, scene.family) if j.status == "pending"]
        assert [(j.schema_id, j.candidates_hash != _EMPTY_LIST_SHA256) for j in pending] == [
            (building_id, True)
        ]

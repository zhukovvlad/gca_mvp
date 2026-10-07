"""Жизнь схемы семьи: пересборка, отмена, ручная правка и слияние синонимов
(спека `2026-10-02-catalog-variants-design.md` §2.8).

Все четыре операции берут семью `FOR UPDATE` первой, ничего не коммитят и
отказывают доменной ошибкой `WorkFamilyError` с кодом. Гонка двух пересборок —
на двух сессиях с настоящими commit-ами: пауза ПОСЛЕ чтения номера версии
(ответ уже у клиента), вторая сессия обязана встать на замке семьи.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticJob,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.semantic_reconcile import reconcile_or_defer
from services.variant_request import load_values_material, paths_hash_of
from services.work_families import WorkFamilyError, archive_family, merge_families
from services.work_variants import (
    JobGuard,
    ParameterEdit,
    apply_values,
    archive_variant_if_empty,
    cancel_schema_build,
    freeze_schema,
    get_or_create_variant,
    merge_parameter_values,
    rebuild_schema,
    update_schema,
)
from tests.integration.test_work_variants_concurrency import _Scene
from tests.integration.test_work_variants_core import (
    _answer,
    _events,
    _lock_sequence,
    _named,
    _parameter_in,
    _schema_answer,
    _schema_snapshot,
    _table_rows,
    _world,
)
from tests.integration.test_work_variants_material import (
    _bind,
    _capturing_sql,
    _chain_context,
    _frozen_schema,
    _settings,
    _uid,
)
from tests.integration.test_work_variants_schema import _family, _schema, _value

pytestmark = pytest.mark.integration

_PARAMS = (
    (1, "Толщина", ["50 мм", "100 мм"]),
    (2, "Материал", ["бетон", "кирпич"]),
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _actor(factories) -> int:
    return factories.UserFactory.create().id


def _active_world(db, factories, params=_PARAMS):
    """Активная семья с текущей версией 1 и одним контекстом."""
    world = _world(db, factories, params=params)
    world.family.status = "active"
    db.flush()
    return world


def _second_context(db, factories, world):
    context_id, _ = _chain_context(
        db, factories, title=f"Вторая строка {_uid()}", path_specs=[((), 1)]
    )
    _bind(db, factories, context_id, family=world.family)
    return context_id


def _jobs(db, kind, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == kind)
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt.order_by(SemanticJob.id)).scalars().all())


def _schemas(db, family):
    db.expire_all()
    return list(
        db.execute(
            sa.select(FamilyParameterSchema)
            .where(FamilyParameterSchema.family_id == family.id)
            .order_by(FamilyParameterSchema.version)
        )
        .scalars()
        .all()
    )


def _parameters_of(db, schema_id):
    db.expire_all()
    return [
        (row.ordinal, row.name)
        for row in db.execute(
            sa.select(FamilyParameter.ordinal, FamilyParameter.name)
            .where(FamilyParameter.schema_id == schema_id)
            .order_by(FamilyParameter.ordinal)
        ).all()
    ]


def _values_of(db, schema_id):
    """`{(ordinal, текст): origin}` значений версии, слитые исключены."""
    db.expire_all()
    return {
        (row.ordinal, row.value): row.origin
        for row in db.execute(
            sa.select(FamilyParameter.ordinal, FamilyParameterValue.value, FamilyParameterValue.origin)
            .join(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
            .where(
                FamilyParameter.schema_id == schema_id,
                FamilyParameterValue.merged_into_id.is_(None),
            )
        ).all()
    }


def _current(db, family):
    [frozen] = [s for s in _schemas(db, family) if s.status == "frozen"]
    return frozen


def _same_edits(params=_PARAMS):
    return [ParameterEdit(ordinal, name, tuple(values)) for ordinal, name, values in params]


def _claim(db, job) -> JobGuard:
    token = uuid.uuid4()
    job.status = "running"
    job.claim_token = token
    db.flush()
    return JobGuard(job_id=job.id, claim_token=token, expected_request_hash=job.request_hash)


def _paths_hash(db, context_id) -> str:
    return paths_hash_of(load_values_material(db, [context_id])[context_id].paths)


def _settle_context(db, world, context_id, answer):
    """Контекст получает вариант и значения настоящим обработчиком."""
    outcome = apply_values(
        db, context_id=context_id, schema_id=world.schema.id, answer=answer,
        paths_hash=_paths_hash(db, context_id), guard=None, settings=_settings(),
    )
    assert outcome.applied
    return outcome.variant_id


def _variant_of(db, context_id) -> int | None:
    db.expire_all()
    return db.get(CatalogContext, context_id).work_variant_id


def _variant_row(db, variant_id) -> WorkVariant:
    db.expire_all()
    return db.get(WorkVariant, variant_id)


def _variant_value_rows(db, variant_id):
    return _table_rows(db, WorkVariantValue, WorkVariantValue.variant_id == variant_id)


def _all_state(db):
    """Всё, что операции жизни схемы могут записать, одним сравнимым значением."""
    return {
        **_schema_snapshot(db),
        "variants": _table_rows(db, WorkVariant),
        "variant_values": _table_rows(db, WorkVariantValue),
        "context_values": _table_rows(db, ContextParameterValue),
        "contexts": _table_rows(db, CatalogContext),
        "jobs": _table_rows(db, SemanticJob),
    }


def _refusal(call) -> str:
    with pytest.raises(WorkFamilyError) as raised:
        call()
    return raised.value.code


# ---------------------------------------------------------------------------
#  rebuild_schema
# ---------------------------------------------------------------------------

class TestRebuildSchema:
    def test_creates_a_building_version_and_queues_the_schema_job(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)

        schema = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert (schema.status, schema.origin, schema.version) == ("building", "model", 2)
        [job] = _jobs(db_session, "family_schema")
        assert (job.family_id, job.schema_id, job.status) == (world.family.id, schema.id, "pending")
        assert [s.status for s in _schemas(db_session, world.family)] == ["frozen", "building"]

    def test_a_family_without_contexts_gets_its_schema_job(self, db_session, factories):
        actor = _actor(factories)
        family = _family(db_session, status="active", definition="Пустая семья")
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["а"])])

        schema = rebuild_schema(db_session, family_id=family.id, actor_id=actor)

        [job] = _jobs(db_session, "family_schema")
        assert (job.family_id, job.schema_id, job.status) == (family.id, schema.id, "pending")

    def test_a_building_version_that_exists_is_returned_and_no_second_one_appears(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        first = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        second = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert second.id == first.id
        assert len(_schemas(db_session, world.family)) == 2
        assert len(_jobs(db_session, "family_schema")) == 1

    def test_a_building_version_without_a_job_gets_its_job(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        building = _schema(db_session, factories, world.family, version=2, status="building")

        schema = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert schema.id == building.id
        [job] = _jobs(db_session, "family_schema")
        assert (job.schema_id, job.status) == (building.id, "pending")

    def test_a_cancelled_version_does_not_block_a_new_rebuild(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        _schema(db_session, factories, world.family, version=2, status="cancelled")

        schema = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert (schema.status, schema.version) == ("building", 3)

    def test_a_family_that_is_not_active_is_refused_without_writes(self, db_session, factories):
        actor = _actor(factories)
        world = _world(db_session, factories)  # семья в черновике
        before = _all_state(db_session)

        code = _refusal(
            lambda: rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)
        )

        assert code == "family_not_active"
        assert _all_state(db_session) == before

    def test_an_unknown_family_is_refused(self, db_session, factories):
        actor = _actor(factories)

        code = _refusal(lambda: rebuild_schema(db_session, family_id=987654321, actor_id=actor))

        assert code == "family_not_found"

    def test_the_family_is_the_first_lock_and_it_is_for_update(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)

        with _capturing_sql(db_session) as statements:
            rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        # Семья, затем версия `building` — порядок замков жизни схемы.
        assert _lock_sequence(statements)[:2] == [
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "UPDATE"),
        ]

    def test_the_call_does_not_commit(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)

        rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert db_session.in_transaction()


class TestRebuildThenFreeze:
    """Следствия заморозки на пути пересборки: прежняя текущая -> `superseded`,
    контекст держит старый вариант до готовности, опустевший старый вариант
    архивируется."""

    def _scene(self, db, factories):
        actor = _actor(factories)
        world = _active_world(db, factories)
        second = _second_context(db, factories, world)
        old_variant = _settle_context(
            db, world, world.context_id, _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))
        )
        _settle_context(db, world, second, _answer(_named(1, "50 мм"), _named(2, "бетон", "path")))
        assert _variant_of(db, second) == old_variant
        new = rebuild_schema(db, family_id=world.family.id, actor_id=actor)
        [job] = _jobs(db, "family_schema")
        guard = _claim(db, job)
        answer = _schema_answer(
            _parameter_in(1, "Толщина", "50 мм", "100 мм"),
            _parameter_in(2, "Материал", "бетон", "кирпич"),
        )
        outcome = freeze_schema(
            db, schema_id=new.id, answer=answer, guard=guard, settings=_settings()
        )
        assert outcome.applied
        return SimpleNamespace(
            world=world, second=second, old_variant=old_variant, new=new, actor=actor
        )

    def test_the_old_version_is_superseded_and_the_new_one_is_current(self, db_session, factories):
        scene = self._scene(db_session, factories)

        statuses = {s.id: s.status for s in _schemas(db_session, scene.world.family)}

        assert statuses == {scene.world.schema.id: "superseded", scene.new.id: "frozen"}

    def test_contexts_keep_the_old_variant_and_get_a_values_job_by_the_new_version(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        assert _variant_of(db_session, scene.world.context_id) == scene.old_variant
        assert _variant_of(db_session, scene.second) == scene.old_variant
        jobs = [j for j in _jobs(db_session, "context_values") if j.status == "pending"]
        assert sorted(j.context_id for j in jobs) == sorted([scene.world.context_id, scene.second])
        assert {j.schema_id for j in jobs} == {scene.new.id}

    def test_the_old_variant_is_archived_when_the_last_context_leaves_it(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)
        world = scene.world
        answer = _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))

        for context_id in (world.context_id, scene.second):
            outcome = apply_values(
                db_session, context_id=context_id, schema_id=scene.new.id, answer=answer,
                paths_hash=_paths_hash(db_session, context_id), guard=None, settings=_settings(),
            )
            assert outcome.applied
            status_after = _variant_row(db_session, scene.old_variant).status
            if context_id == world.context_id:
                assert status_after == "active"  # второй контекст ещё на нём
        assert _variant_row(db_session, scene.old_variant).status == "archived"


class TestRebuildRace:
    def test_two_parallel_rebuilds_give_one_version(
        self, committing_db, committing_factories, committing_session_factory
    ):
        actor = _actor(committing_factories)
        world = _active_world(committing_db, committing_factories)
        committing_db.commit()
        scene = _Scene(committing_session_factory)

        def rebuild(db):
            return rebuild_schema(db, family_id=world.family.id, actor_id=actor).id

        def after_the_version_number_is_read(statement: str) -> bool:
            return "max(family_parameter_schemas.version)" in statement

        try:
            scene.spawn("a", rebuild, pause_on=after_the_version_number_is_read)
            assert scene.wait_paused("a"), "A не дошла до чтения номера версии"
            scene.spawn("b", rebuild)
            settled = scene.wait_settled("b", contains="work_families")
        finally:
            scene.finish()

        scene.assert_clean()
        # Без замка семьи B читает тот же номер и ждёт не её, а чужую вставку.
        assert settled == "blocked", f"B не встала на замок семьи: {settled}"
        assert scene.results["a"] == scene.results["b"]
        with committing_session_factory() as db:
            versions = db.execute(
                sa.select(FamilyParameterSchema.version, FamilyParameterSchema.status)
                .where(FamilyParameterSchema.family_id == world.family.id)
                .order_by(FamilyParameterSchema.version)
            ).all()
        assert [tuple(v) for v in versions] == [(1, "frozen"), (2, "building")]


# ---------------------------------------------------------------------------
#  cancel_schema_build
# ---------------------------------------------------------------------------

class TestCancelSchemaBuild:
    def _building(self, db, factories):
        actor = _actor(factories)
        world = _active_world(db, factories)
        building = rebuild_schema(db, family_id=world.family.id, actor_id=actor)
        return actor, world, building

    def test_the_version_and_its_job_are_cancelled_in_one_transaction(self, db_session, factories):
        actor, world, building = self._building(db_session, factories)
        [job] = _jobs(db_session, "family_schema")

        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        db_session.expire_all()
        schema = db_session.get(FamilyParameterSchema, building.id)
        assert schema.status == "cancelled" and schema.cancelled_at is not None
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert db_session.in_transaction()

    def test_the_current_version_is_not_touched(self, db_session, factories):
        actor, world, _building = self._building(db_session, factories)

        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        assert _current(db_session, world.family).id == world.schema.id

    def test_a_running_job_is_left_alone_and_its_result_is_rejected(self, db_session, factories):
        actor, world, building = self._building(db_session, factories)
        [job] = _jobs(db_session, "family_schema")
        guard = _claim(db_session, job)

        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "running"
        outcome = freeze_schema(
            db_session, schema_id=building.id,
            answer=_schema_answer(_parameter_in(1, "Толщина", "50 мм")),
            guard=guard, settings=_settings(),
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _current(db_session, world.family).id == world.schema.id

    def test_a_job_in_error_is_cancelled_with_the_version(self, db_session, factories):
        actor, world, _building = self._building(db_session, factories)
        [job] = _jobs(db_session, "family_schema")
        job.status = "error"
        db_session.flush()

        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "cancelled"

    def test_without_a_building_version_the_call_is_refused_and_writes_nothing(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        before = _all_state(db_session)

        code = _refusal(
            lambda: cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)
        )

        assert code == "schema_no_building"
        assert _all_state(db_session) == before

    def test_an_unknown_family_is_refused(self, db_session, factories):
        actor = _actor(factories)

        code = _refusal(
            lambda: cancel_schema_build(db_session, family_id=987654321, actor_id=actor)
        )

        assert code == "family_not_found"

    def test_a_family_archived_during_the_rebuild_can_still_cancel_it(
        self, db_session, factories
    ):
        # Семья без контекстов архивируется, пока версия `building`: отмена —
        # единственный выход из неё, поэтому активность семьи не требуется.
        actor = _actor(factories)
        family = _family(db_session, status="active", definition="Семья без контекстов")
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["а"])])
        building = rebuild_schema(db_session, family_id=family.id, actor_id=actor)
        archive_family(db_session, family_id=family.id, actor_id=actor)

        cancel_schema_build(db_session, family_id=family.id, actor_id=actor)

        db_session.expire_all()
        assert db_session.get(FamilyParameterSchema, building.id).status == "cancelled"
        [job] = _jobs(db_session, "family_schema")
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_a_cancelled_version_does_not_block_a_new_rebuild(self, db_session, factories):
        actor, world, building = self._building(db_session, factories)
        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        again = rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)

        assert (again.status, again.version) == ("building", building.version + 1)

    def test_a_family_with_a_cancelled_version_merges_into_another(self, db_session, factories):
        actor, world, _building = self._building(db_session, factories)
        cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)
        target = _family(db_session, status="active", definition="Другая семья")

        moved = merge_families(
            db_session, source_family_id=world.family.id, target_family_id=target.id,
            actor_id=actor,
        )

        assert moved == 1
        db_session.expire_all()
        assert db_session.get(WorkFamily, world.family.id).status == "archived"

    def test_the_family_is_the_first_lock_and_it_is_for_update(self, db_session, factories):
        actor, world, _building = self._building(db_session, factories)

        with _capturing_sql(db_session) as statements:
            cancel_schema_build(db_session, family_id=world.family.id, actor_id=actor)

        assert _lock_sequence(statements)[:2] == [
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "UPDATE"),
        ]


class TestOneTransitionForTheCancelledVersion:
    def test_the_decisions_module_does_not_write_the_cancelled_status_itself(self):
        source = Path(__file__).resolve().parents[2].joinpath(
            "services", "semantic_decisions.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        writes = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"cancelled_at"}
            and isinstance(node.ctx, ast.Store)
        ]
        assert writes == [], "версия отменяется общим помощником, а не второй копией перехода"


# ---------------------------------------------------------------------------
#  update_schema
# ---------------------------------------------------------------------------

class TestUpdateSchemaCosmetic:
    def test_a_name_with_the_same_normalised_form_is_renamed_in_place(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        parameter_ids = dict(world.parameters)
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "  ТОЛЩИНА ", edits[0].values)

        schema = update_schema(
            db_session, family_id=world.family.id, parameters=edits, actor_id=actor
        )

        assert schema.id == world.schema.id
        assert [s.version for s in _schemas(db_session, world.family)] == [1]
        assert _parameters_of(db_session, schema.id)[0] == (1, "ТОЛЩИНА")
        rows = dict(
            db_session.execute(sa.select(FamilyParameter.ordinal, FamilyParameter.id)).all()
        )
        assert rows[1] == parameter_ids[1]

    def test_a_value_with_the_same_normalised_form_is_renamed_in_place(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        value_id = world.values[(1, "50 мм")]
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "Толщина", ("  50   ММ ", "100 мм"))

        schema = update_schema(
            db_session, family_id=world.family.id, parameters=edits, actor_id=actor
        )

        assert schema.id == world.schema.id
        db_session.expire_all()
        assert db_session.get(FamilyParameterValue, value_id).value == "50   ММ"
        assert len(_schemas(db_session, world.family)) == 1

    def test_a_context_with_a_variant_on_the_current_schema_gets_no_job_and_no_event(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон", "path")),
        )
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "ТОЛЩИНА", ("50 ММ", "100 мм"))
        events_before = _schema_snapshot(db_session)["events"]

        update_schema(db_session, family_id=world.family.id, parameters=edits, actor_id=actor)

        assert _jobs(db_session, "context_values") == []
        assert _jobs(db_session, "family_schema") == []
        assert _schema_snapshot(db_session)["events"] == events_before

    def test_a_pending_values_job_is_replaced_by_one_with_the_new_request_hash(
        self, db_session, factories
    ):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        reconcile_or_defer(db_session, [world.context_id])
        [old_job] = _jobs(db_session, "context_values")
        assert old_job.status == "pending"
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "ТОЛЩИНА", edits[0].values)

        update_schema(db_session, family_id=world.family.id, parameters=edits, actor_id=actor)

        jobs = _jobs(db_session, "context_values")
        live = [job for job in jobs if job.status == "pending"]
        assert [job.status for job in jobs if job.id == old_job.id] == ["cancelled"]
        assert len(live) == 1 and live[0].id != old_job.id
        assert live[0].request_hash != old_job.request_hash

    def test_the_same_edits_change_nothing(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        before = _all_state(db_session)

        schema = update_schema(
            db_session, family_id=world.family.id, parameters=_same_edits(), actor_id=actor
        )

        assert schema.id == world.schema.id
        assert _all_state(db_session) == before

    def test_merged_values_are_not_part_of_the_comparison(self, db_session, factories):
        actor = _actor(factories)
        world = _active_world(db_session, factories)
        brick = db_session.get(FamilyParameterValue, world.values[(2, "кирпич")])
        brick.merged_into_id = world.values[(2, "бетон")]
        db_session.flush()
        edits = _same_edits()
        edits[1] = ParameterEdit(2, "Материал", ("бетон",))

        schema = update_schema(
            db_session, family_id=world.family.id, parameters=edits, actor_id=actor
        )

        assert schema.id == world.schema.id
        assert len(_schemas(db_session, world.family)) == 1


class TestUpdateSchemaNewVersion:
    """Добавление параметра, удаление параметра и добавление значения — каждое
    своя новая версия `manual`, замороженная сразу, с заданиями значений всем
    контекстам семьи; контексты держат прежние варианты."""

    def _scene(self, db, factories):
        actor = _actor(factories)
        world = _active_world(db, factories)
        second = _second_context(db, factories, world)
        answer = _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))
        variant = _settle_context(db, world, world.context_id, answer)
        _settle_context(db, world, second, answer)
        return SimpleNamespace(world=world, second=second, actor=actor, variant=variant)

    def _check_new_version(self, db, scene, new, expected_parameters):
        world = scene.world
        assert new.id != world.schema.id
        assert (new.version, new.status, new.origin, new.frozen_by) == (
            2, "frozen", "manual", scene.actor,
        )
        db.expire_all()
        assert db.get(FamilyParameterSchema, world.schema.id).status == "superseded"
        assert _parameters_of(db, new.id) == expected_parameters
        assert _variant_of(db, world.context_id) == scene.variant
        assert _variant_of(db, scene.second) == scene.variant
        jobs = [j for j in _jobs(db, "context_values") if j.status == "pending"]
        assert sorted(j.context_id for j in jobs) == sorted([world.context_id, scene.second])
        assert {j.schema_id for j in jobs} == {new.id}
        [event] = _events(db, "family_schema_frozen", family_id=world.family.id)
        assert event.payload["origin"] == "manual"
        assert (event.payload["schema_id"], event.payload["version"]) == (new.id, 2)
        assert event.payload["job_id"] is None

    def test_a_new_parameter_makes_a_manual_version(self, db_session, factories):
        scene = self._scene(db_session, factories)
        edits = _same_edits() + [ParameterEdit(3, "Армирование", ("сетка",))]

        new = update_schema(
            db_session, family_id=scene.world.family.id, parameters=edits, actor_id=scene.actor
        )

        self._check_new_version(
            db_session, scene, new, [(1, "Толщина"), (2, "Материал"), (3, "Армирование")]
        )
        assert _values_of(db_session, new.id)[(3, "сетка")] == "manual"

    def test_a_removed_parameter_makes_a_manual_version(self, db_session, factories):
        scene = self._scene(db_session, factories)

        new = update_schema(
            db_session, family_id=scene.world.family.id, parameters=_same_edits()[:1],
            actor_id=scene.actor,
        )

        self._check_new_version(db_session, scene, new, [(1, "Толщина")])
        assert set(_values_of(db_session, new.id)) == {(1, "50 мм"), (1, "100 мм")}

    def test_a_new_value_makes_a_manual_version_and_an_event(self, db_session, factories):
        scene = self._scene(db_session, factories)
        edits = _same_edits()
        edits[0] = ParameterEdit(1, "Толщина", ("50 мм", "100 мм", " 150 мм "))

        new = update_schema(
            db_session, family_id=scene.world.family.id, parameters=edits, actor_id=scene.actor
        )

        self._check_new_version(db_session, scene, new, [(1, "Толщина"), (2, "Материал")])
        values = _values_of(db_session, new.id)
        assert values[(1, "150 мм")] == "manual"
        assert values[(1, "50 мм")] == "schema"  # перенесённое сохраняет происхождение
        [event] = _events(db_session, "family_schema_value_added", family_id=scene.world.family.id)
        new_parameter_id = db_session.execute(
            sa.select(FamilyParameter.id).where(
                FamilyParameter.schema_id == new.id, FamilyParameter.ordinal == 1
            )
        ).scalar_one()
        new_value_id = db_session.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.parameter_id == new_parameter_id,
                FamilyParameterValue.value == "150 мм",
            )
        ).scalar_one()
        assert event.payload == {
            "parameter_id": new_parameter_id, "value_id": new_value_id, "value": "150 мм",
            "origin": "manual", "context_id": None,
        }

    def test_the_new_version_takes_the_edited_display_names(self, db_session, factories):
        scene = self._scene(db_session, factories)
        edits = _same_edits() + [ParameterEdit(3, " Армирование ", ("сетка",))]
        edits[0] = ParameterEdit(1, "ТОЛЩИНА", edits[0].values)

        new = update_schema(
            db_session, family_id=scene.world.family.id, parameters=edits, actor_id=scene.actor
        )

        assert _parameters_of(db_session, new.id)[0] == (1, "ТОЛЩИНА")
        assert _parameters_of(db_session, new.id)[2] == (3, "Армирование")

    def test_merged_values_are_not_carried_into_the_new_version(self, db_session, factories):
        scene = self._scene(db_session, factories)
        world = scene.world
        db_session.get(FamilyParameterValue, world.values[(2, "кирпич")]).merged_into_id = (
            world.values[(2, "бетон")]
        )
        db_session.flush()
        edits = _same_edits()
        edits[1] = ParameterEdit(2, "Материал", ("бетон", "керамзит"))

        new = update_schema(
            db_session, family_id=world.family.id, parameters=edits, actor_id=scene.actor
        )

        assert {v for (o, v) in _values_of(db_session, new.id) if o == 2} == {"бетон", "керамзит"}

    def test_the_family_is_the_first_lock_and_it_is_for_update(self, db_session, factories):
        scene = self._scene(db_session, factories)
        edits = _same_edits() + [ParameterEdit(3, "Армирование", ("сетка",))]

        with _capturing_sql(db_session) as statements:
            update_schema(
                db_session, family_id=scene.world.family.id, parameters=edits,
                actor_id=scene.actor,
            )

        assert _lock_sequence(statements)[:2] == [
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "UPDATE"),
        ]


class TestUpdateSchemaRefusals:
    def _refuse(self, db, factories, edits, expected, *, mutate=None):
        actor = _actor(factories)
        world = _active_world(db, factories)
        if mutate is not None:
            mutate(db, factories, world)
        before = _all_state(db)

        code = _refusal(
            lambda: update_schema(
                db, family_id=world.family.id, parameters=edits(world), actor_id=actor
            )
        )

        assert code == expected
        assert _all_state(db) == before

    def test_a_semantic_rename_of_a_parameter_is_refused(self, db_session, factories):
        def edits(_world):
            changed = _same_edits()
            changed[0] = ParameterEdit(1, "Класс бетона", changed[0].values)
            return changed

        self._refuse(db_session, factories, edits, "schema_parameter_renamed")

    def test_a_removed_value_is_refused(self, db_session, factories):
        def edits(_world):
            changed = _same_edits()
            changed[0] = ParameterEdit(1, "Толщина", ("50 мм",))
            return changed

        self._refuse(db_session, factories, edits, "schema_value_removed")

    def test_a_renamed_value_is_refused_as_a_removal(self, db_session, factories):
        def edits(_world):
            changed = _same_edits()
            changed[0] = ParameterEdit(1, "Толщина", ("60 мм", "100 мм"))
            return changed

        self._refuse(db_session, factories, edits, "schema_value_removed")

    def test_a_structural_change_with_a_building_version_is_refused(self, db_session, factories):
        def building(db, factories, world):
            _schema(db, factories, world.family, version=2, status="building")

        def edits(_world):
            return _same_edits() + [ParameterEdit(3, "Армирование", ("сетка",))]

        self._refuse(db_session, factories, edits, "schema_building", mutate=building)

    def test_a_cosmetic_edit_with_a_building_version_is_refused_too(self, db_session, factories):
        def building(db, factories, world):
            _schema(db, factories, world.family, version=2, status="building")

        def edits(_world):
            changed = _same_edits()
            changed[0] = ParameterEdit(1, "ТОЛЩИНА", changed[0].values)
            return changed

        self._refuse(db_session, factories, edits, "schema_building", mutate=building)

    def test_a_blank_parameter_name_is_refused(self, db_session, factories):
        def edits(_world):
            return _same_edits() + [ParameterEdit(3, " «» ", ("сетка",))]

        self._refuse(db_session, factories, edits, "schema_blank")

    def test_a_blank_value_is_refused(self, db_session, factories):
        def edits(_world):
            changed = _same_edits()
            changed[0] = ParameterEdit(1, "Толщина", ("50 мм", "100 мм", "  "))
            return changed

        self._refuse(db_session, factories, edits, "schema_blank")

    def test_a_repeated_ordinal_is_refused(self, db_session, factories):
        def edits(_world):
            return _same_edits() + [ParameterEdit(2, "Другое", ("а",))]

        self._refuse(db_session, factories, edits, "schema_bad_ordinals")

    def test_an_ordinal_outside_the_range_is_refused(self, db_session, factories):
        def edits(_world):
            return _same_edits() + [ParameterEdit(4, "Четвёртый", ("а",))]

        self._refuse(db_session, factories, edits, "schema_bad_ordinals")

    def test_a_family_without_a_current_version_is_refused(self, db_session, factories):
        def drop_current(db, factories, world):
            world.schema.status = "superseded"
            world.schema.superseded_at = world.schema.frozen_at
            db.flush()

        self._refuse(
            db_session, factories, lambda _w: _same_edits(), "schema_no_current",
            mutate=drop_current,
        )

    def test_a_family_that_is_not_active_is_refused(self, db_session, factories):
        actor = _actor(factories)
        world = _world(db_session, factories)  # семья в черновике
        before = _all_state(db_session)

        code = _refusal(
            lambda: update_schema(
                db_session, family_id=world.family.id, parameters=_same_edits(), actor_id=actor
            )
        )

        assert code == "family_not_active"
        assert _all_state(db_session) == before

    def test_an_unknown_family_is_refused(self, db_session, factories):
        actor = _actor(factories)

        code = _refusal(
            lambda: update_schema(
                db_session, family_id=987654321, parameters=_same_edits(), actor_id=actor
            )
        )

        assert code == "family_not_found"


# ---------------------------------------------------------------------------
#  merge_parameter_values
# ---------------------------------------------------------------------------

class _Merge:
    """Сцена слияния: параметр 1 схемы (`50 мм` -> источник, `100 мм` -> цель),
    два контекста с настоящими вариантами и значениями контекста."""

    def __init__(self, db, factories):
        self.db = db
        self.actor = _actor(factories)
        self.world = _active_world(db, factories)
        self.second = _second_context(db, factories, self.world)
        self.parameter_id = self.world.parameters[1]
        self.source = self.world.values[(1, "50 мм")]
        self.target = self.world.values[(1, "100 мм")]

    def settle(self, context_id, thickness, material="бетон"):
        return _settle_context(
            self.db, self.world, context_id,
            _answer(_named(1, thickness), _named(2, material, "path")),
        )

    def variant(self, thickness, material="бетон"):
        ids = {
            1: self.world.values[(1, thickness)],
            2: self.world.values[(2, material)],
        }
        variant, _created = get_or_create_variant(
            self.db, family_id=self.world.family.id, schema_id=self.world.schema.id,
            value_ids_by_ordinal=ids,
        )
        return variant

    def merge(self, source=None, target=None):
        return merge_parameter_values(
            self.db, parameter_id=self.parameter_id,
            source_value_id=source or self.source, target_value_id=target or self.target,
            actor_id=self.actor,
        )

    def value(self, value_id) -> FamilyParameterValue:
        self.db.expire_all()
        return self.db.get(FamilyParameterValue, value_id)

    def context_value(self, context_id, parameter_ordinal=1):
        self.db.expire_all()
        return self.db.execute(
            sa.select(ContextParameterValue.value_id).where(
                ContextParameterValue.context_id == context_id,
                ContextParameterValue.parameter_id == self.world.parameters[parameter_ordinal],
            )
        ).scalar_one()


class TestMergeWithCollision:
    def _scene(self, db, factories):
        scene = _Merge(db, factories)
        scene.va = scene.settle(scene.world.context_id, "50 мм")
        scene.vb = scene.settle(scene.second, "100 мм")
        scene.history = (
            _variant_row(db, scene.va).values_key, _variant_value_rows(db, scene.va)
        )
        return scene

    def test_contexts_move_and_the_source_variant_is_archived_as_history(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        merged = scene.merge()

        assert merged == {scene.va: scene.vb}
        assert _variant_of(db_session, scene.world.context_id) == scene.vb
        assert _variant_of(db_session, scene.second) == scene.vb
        source_variant = _variant_row(db_session, scene.va)
        assert (source_variant.status, source_variant.merged_into_id) == ("archived", scene.vb)
        assert source_variant.archived_at is not None
        assert (source_variant.values_key, _variant_value_rows(db_session, scene.va)) == scene.history
        assert _variant_row(db_session, scene.vb).status == "active"

    def test_context_values_of_the_source_point_at_the_target(self, db_session, factories):
        scene = self._scene(db_session, factories)

        scene.merge()

        assert scene.context_value(scene.world.context_id) == scene.target
        assert scene.context_value(scene.second) == scene.target

    def test_the_source_value_is_archived_by_a_link_and_stays_a_row(self, db_session, factories):
        scene = self._scene(db_session, factories)

        scene.merge()

        row = scene.value(scene.source)
        assert row.merged_into_id == scene.target and row.value == "50 мм"

    def test_the_event_carries_the_pairs_of_merged_variants(self, db_session, factories):
        scene = self._scene(db_session, factories)

        scene.merge()

        [event] = _events(db_session, "family_variants_merged", family_id=scene.world.family.id)
        assert event.payload == {
            "parameter_id": scene.parameter_id, "source_value_id": scene.source,
            "target_value_id": scene.target, "merged_variants": [[scene.va, scene.vb]],
        }

    def test_an_archived_target_variant_is_reactivated_with_the_arriving_contexts(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        scene.va = scene.settle(scene.world.context_id, "50 мм")
        scene.vb = scene.variant("100 мм").id
        assert archive_variant_if_empty(db_session, scene.vb)

        merged = scene.merge()

        assert merged == {scene.va: scene.vb}
        target_variant = _variant_row(db_session, scene.vb)
        assert (target_variant.status, target_variant.archived_at) == ("active", None)
        assert _variant_of(db_session, scene.world.context_id) == scene.vb

    def test_a_source_variant_without_contexts_does_not_reactivate_an_archived_target(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        scene.va = scene.variant("50 мм").id  # вариант без контекстов
        scene.vb = scene.variant("100 мм").id
        assert archive_variant_if_empty(db_session, scene.vb)

        merged = scene.merge()

        assert merged == {scene.va: scene.vb}
        assert _variant_row(db_session, scene.vb).status == "archived"
        source_variant = _variant_row(db_session, scene.va)
        assert (source_variant.status, source_variant.merged_into_id) == ("archived", scene.vb)

    def test_a_variant_merged_by_an_earlier_merge_stays_history(self, db_session, factories):
        # `(50, кирпич)` слит в `(50, бетон)` первым слиянием и несёт источник
        # второго (`50 мм`): история не переписывается и не сливается заново.
        scene = _Merge(db_session, factories)
        history = scene.settle(scene.world.context_id, "50 мм", "кирпич")
        live = scene.settle(scene.second, "50 мм", "бетон")
        merge_parameter_values(
            db_session, parameter_id=scene.world.parameters[2],
            source_value_id=scene.world.values[(2, "кирпич")],
            target_value_id=scene.world.values[(2, "бетон")], actor_id=scene.actor,
        )
        assert _variant_row(db_session, history).merged_into_id == live
        before = (
            _variant_row(db_session, history).values_key, _variant_value_rows(db_session, history)
        )

        merged = scene.merge()

        assert merged == {}
        row = _variant_row(db_session, history)
        assert (row.values_key, _variant_value_rows(db_session, history)) == before
        assert (row.status, row.merged_into_id) == ("archived", live)

    def test_variants_of_other_values_are_not_touched(self, db_session, factories):
        scene = self._scene(db_session, factories)
        other = scene.variant("100 мм", "кирпич").id
        before = (_variant_row(db_session, other).values_key, _variant_value_rows(db_session, other))

        scene.merge()

        assert (
            _variant_row(db_session, other).values_key, _variant_value_rows(db_session, other)
        ) == before
        assert _variant_row(db_session, other).status == "active"


class TestMergeWithoutCollision:
    def test_the_key_and_the_rows_are_rewritten_and_the_variant_stays_the_same(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        va = scene.settle(scene.world.context_id, "50 мм")
        concrete = scene.world.values[(2, "бетон")]

        merged = scene.merge()

        assert merged == {}
        variant = _variant_row(db_session, va)
        assert variant.values_key == f"1={scene.target}|2={concrete}"
        assert (variant.status, variant.merged_into_id) == ("active", None)
        values = {
            (row.parameter_id, row.value_id)
            for row in db_session.execute(
                sa.select(WorkVariantValue).where(WorkVariantValue.variant_id == va)
            ).scalars()
        }
        assert values == {
            (scene.world.parameters[1], scene.target), (scene.world.parameters[2], concrete),
        }
        assert _variant_of(db_session, scene.world.context_id) == va
        assert scene.context_value(scene.world.context_id) == scene.target
        assert scene.value(scene.source).merged_into_id == scene.target

    def test_the_event_has_no_variant_pairs(self, db_session, factories):
        scene = _Merge(db_session, factories)
        scene.settle(scene.world.context_id, "50 мм")

        scene.merge()

        [event] = _events(db_session, "family_variants_merged", family_id=scene.world.family.id)
        assert event.payload["merged_variants"] == []

    def test_an_archived_variant_with_the_source_is_rewritten_in_place(self, db_session, factories):
        scene = _Merge(db_session, factories)
        old = scene.variant("50 мм").id
        assert archive_variant_if_empty(db_session, old)

        merged = scene.merge()

        assert merged == {}
        row = _variant_row(db_session, old)
        assert row.status == "archived"
        assert row.values_key == f"1={scene.target}|2={scene.world.values[(2, 'бетон')]}"


class TestMergeTargets:
    def test_the_source_as_its_own_target_is_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)
        before = _all_state(db_session)

        code = _refusal(lambda: scene.merge(target=scene.source))

        assert code == "merge_value_cycle"
        assert _all_state(db_session) == before

    def test_an_ancestor_of_the_source_is_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)
        scene.merge()  # источник -> цель
        before = _all_state(db_session)

        code = _refusal(lambda: scene.merge(source=scene.target, target=scene.source))

        assert code == "merge_value_cycle"
        assert _all_state(db_session) == before

    def test_a_synonym_target_is_resolved_to_the_canonical_value(self, db_session, factories):
        scene = _Merge(db_session, factories)
        third = _value(db_session, db_session.get(FamilyParameter, scene.parameter_id), "150 мм")
        scene.merge()  # `50 мм` -> `100 мм`

        # Цель `50 мм` сама слита в `100 мм`: `150 мм` уходит к каноническому.
        merged = scene.merge(source=third.id, target=scene.source)

        assert merged == {}
        assert scene.value(third.id).merged_into_id == scene.target
        events = _events(db_session, "family_variants_merged", family_id=scene.world.family.id)
        assert events[-1].payload["target_value_id"] == scene.target  # каноническая цель

    def test_context_values_of_a_synonym_target_go_to_the_canonical_value(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        third = _value(db_session, db_session.get(FamilyParameter, scene.parameter_id), "150 мм")
        scene.merge()  # `50 мм` -> `100 мм`
        scene.db.execute(
            sa.insert(ContextParameterValue),
            [{
                "context_id": scene.world.context_id, "schema_id": scene.world.schema.id,
                "parameter_id": scene.parameter_id, "value_id": third.id, "source": "name",
                "job_id": None,
            }],
        )

        scene.merge(source=third.id, target=scene.source)

        assert scene.context_value(scene.world.context_id) == scene.target

    def test_a_source_that_is_already_merged_is_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)
        scene.merge()
        before = _all_state(db_session)

        code = _refusal(lambda: scene.merge())

        assert code == "merge_source_merged"
        assert _all_state(db_session) == before

    def test_values_of_different_parameters_are_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)
        other_parameter_value = scene.world.values[(2, "бетон")]
        before = _all_state(db_session)

        code = _refusal(lambda: scene.merge(target=other_parameter_value))

        assert code == "merge_values_other_parameter"
        assert _all_state(db_session) == before

    def test_an_unknown_value_and_an_unknown_parameter_are_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)

        assert _refusal(lambda: scene.merge(target=987654321)) == "value_not_found"
        assert _refusal(
            lambda: merge_parameter_values(
                db_session, parameter_id=987654321, source_value_id=scene.source,
                target_value_id=scene.target, actor_id=scene.actor,
            )
        ) == "parameter_not_found"


class TestMergeReconciles:
    def test_a_values_job_with_the_old_value_list_is_replaced(self, db_session, factories):
        scene = _Merge(db_session, factories)
        reconcile_or_defer(db_session, [scene.world.context_id])
        [old_job] = _jobs(db_session, "context_values")
        assert old_job.status == "pending"

        scene.merge()

        jobs = _jobs(db_session, "context_values", context_id=scene.world.context_id)
        assert sorted(job.status for job in jobs) == ["cancelled", "pending"]  # живое — одно
        by_status = {job.status: job for job in jobs}
        assert by_status["cancelled"].id == old_job.id
        assert by_status["pending"].request_hash != old_job.request_hash

    def test_the_call_happens_before_the_callers_commit(self, db_session, factories):
        scene = _Merge(db_session, factories)

        scene.merge()

        assert db_session.in_transaction()


class TestMergeLockOrder:
    def test_locks_follow_the_order_family_schema_values_variants_contexts(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        scene.settle(scene.world.context_id, "50 мм")
        scene.settle(scene.second, "100 мм")

        with _capturing_sql(db_session) as statements:
            scene.merge()

        sequence = _lock_sequence(statements)
        assert sequence[:5] == [
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "SHARE"),
            ("family_parameter_values", "UPDATE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
        ]
        assert not [t for t in sequence if t == ("work_families", "SHARE")]


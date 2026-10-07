"""Слияние семей с вариантами и схемами (спека
`2026-10-02-catalog-variants-design.md` §2.9).

Три ветви переезда версий схемы источника: у цели есть текущая версия, у цели
текущей нет, у обеих схемы нет. В каждой после слияния истинны оба составных
FK (контекст -> вариант, вариант -> версия схемы): `merge_families` проверяет
их до возврата, а не на чужом `commit`, — `SET CONSTRAINTS` этих двух FK по
имени `IMMEDIATE` и сразу обратно `DEFERRED`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import services.work_families as work_families_module
from models import (
    CatalogContext,
    FamilyParameterSchema,
    FamilySuggestion,
    SemanticJob,
    WorkFamily,
    WorkVariant,
)
from services.semantic_reconcile import reconcile_or_defer
from services.work_families import (
    REFUSE_MERGE_SCHEMA_BUILDING,
    WorkFamilyError,
    merge_families,
)
from services.work_variants import rebuild_schema
from tests.integration.test_work_variants_core import (
    _answer,
    _events,
    _lock_sequence,
    _named,
    _set_pending,
)
from tests.integration.test_work_variants_material import _capturing_sql, _frozen_schema, _uid
from tests.integration.test_work_variants_schema import _family, _schema
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _all_state,
    _jobs,
    _schemas,
    _second_context,
    _settle_context,
    _variant_of,
)

pytestmark = pytest.mark.integration

_TARGET_PARAMS = [(1, "Группа", ["а", "б"])]


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _settled_world(db, factories):
    """Активная семья с текущей версией; контекст уже получил вариант."""
    world = _active_world(db, factories)
    _settle_context(
        db, world, world.context_id, _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))
    )
    return world


def _target_family(db, factories, *, with_current):
    target = _family(db, status="active", definition=f"Цель {_uid()}")
    schema = _frozen_schema(db, factories, target, _TARGET_PARAMS) if with_current else None
    return target, schema


def _target_context(db, factories, target):
    return _second_context(db, factories, SimpleNamespace(family=target))


def _merge(db, source, target, actor):
    return merge_families(
        db, source_family_id=source.id, target_family_id=target.id, actor_id=actor
    )


def _versions(db, family):
    """`[(version, status, id)]` версий семьи по номеру."""
    return [(s.version, s.status, s.id) for s in _schemas(db, family)]


def _composite_violations(db):
    """Контексты и варианты, нарушающие составные FK независимым запросом."""
    db.expire_all()
    contexts = db.execute(
        sa.text(
            "SELECT c.id FROM catalog_contexts c JOIN work_variants v ON v.id = c.work_variant_id "
            "WHERE v.family_id IS DISTINCT FROM c.work_family_id"
        )
    ).all()
    variants = db.execute(
        sa.text(
            "SELECT v.id FROM work_variants v JOIN family_parameter_schemas s "
            "ON s.id = v.schema_id WHERE s.family_id <> v.family_id"
        )
    ).all()
    return contexts, variants


def _suggestion_jobs(db):
    db.expire_all()
    return sorted(
        (job.id, job.status)
        for job in db.execute(
            sa.select(SemanticJob).where(SemanticJob.kind == "family_suggestion")
        ).scalars()
    )


def _values_jobs(db, context_id):
    return _jobs(db, "context_values", context_id=context_id)


# ---------------------------------------------------------------------------
#  Отказ при версии building
# ---------------------------------------------------------------------------

class TestRefusesWhileASchemaIsBuilding:
    def test_a_building_version_of_the_source_refuses_the_merge(self, db_session, factories):
        actor = _actor(factories)
        world = _settled_world(db_session, factories)
        rebuild_schema(db_session, family_id=world.family.id, actor_id=actor)
        target, _schema_row = _target_family(db_session, factories, with_current=True)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            _merge(db_session, world.family, target, actor)

        # Значение кода — контракт плана (его читают API и фронтенд), не только имя.
        assert raised.value.code == REFUSE_MERGE_SCHEMA_BUILDING == "merge_schema_building"
        assert (raised.value.role, raised.value.family_id) == ("source", world.family.id)
        assert _all_state(db_session) == before
        assert db_session.get(WorkFamily, world.family.id).status == "active"

    def test_a_building_version_of_the_target_refuses_the_merge(self, db_session, factories):
        actor = _actor(factories)
        world = _settled_world(db_session, factories)
        target, _schema_row = _target_family(db_session, factories, with_current=True)
        rebuild_schema(db_session, family_id=target.id, actor_id=actor)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            _merge(db_session, world.family, target, actor)

        assert raised.value.code == REFUSE_MERGE_SCHEMA_BUILDING
        assert (raised.value.role, raised.value.family_id) == ("target", target.id)
        assert _all_state(db_session) == before
        assert db_session.get(WorkFamily, world.family.id).status == "active"

    def test_a_cancelled_version_does_not_block_the_merge(self, db_session, factories):
        actor = _actor(factories)
        world = _settled_world(db_session, factories)
        _schema(db_session, factories, world.family, version=2, status="cancelled")
        target, _schema_row = _target_family(db_session, factories, with_current=True)
        _schema(db_session, factories, target, version=2, status="cancelled")

        moved = _merge(db_session, world.family, target, actor)

        assert moved == 1
        assert db_session.get(WorkFamily, world.family.id).status == "archived"


# ---------------------------------------------------------------------------
#  Ветвь 1: у цели есть текущая версия
# ---------------------------------------------------------------------------

class TestTargetHasACurrentVersion:
    def _scene(self, db, factories):
        actor = _actor(factories)
        world = _settled_world(db, factories)
        second = _second_context(db, factories, world)
        cancelled = _schema(db, factories, world.family, version=2, status="cancelled")
        target, target_schema = _target_family(db, factories, with_current=True)
        return SimpleNamespace(
            actor=actor, world=world, second=second, cancelled=cancelled, target=target,
            target_schema=target_schema, variant_id=_variant_of(db, world.context_id),
        )

    def test_source_versions_move_with_new_numbers_and_statuses(self, db_session, factories):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        assert _versions(db_session, scene.target) == [
            (1, "frozen", scene.target_schema.id),
            (2, "superseded", scene.world.schema.id),
            (3, "cancelled", scene.cancelled.id),
        ]
        assert _schemas(db_session, scene.world.family) == []
        moved = db_session.get(FamilyParameterSchema, scene.world.schema.id)
        assert moved.frozen_at is not None and moved.superseded_at is not None
        assert db_session.get(FamilyParameterSchema, scene.cancelled.id).cancelled_at is not None

    def test_variants_and_contexts_follow_and_both_composite_keys_hold(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        moved = _merge(db_session, scene.world.family, scene.target, scene.actor)

        assert moved == 2
        db_session.expire_all()
        variant = db_session.get(WorkVariant, scene.variant_id)
        assert (variant.family_id, variant.schema_id) == (scene.target.id, scene.world.schema.id)
        context = db_session.get(CatalogContext, scene.world.context_id)
        assert (context.work_family_id, context.work_variant_id) == (
            scene.target.id, scene.variant_id,
        )
        assert db_session.get(CatalogContext, scene.second).work_family_id == scene.target.id
        assert _composite_violations(db_session) == ([], [])
        db_session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))

    def test_moved_contexts_get_value_jobs_on_the_current_version_of_the_target(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        for context_id in (scene.world.context_id, scene.second):
            [job] = _values_jobs(db_session, context_id)
            assert (job.schema_id, job.status) == (scene.target_schema.id, "pending")

    def test_the_merge_event_counts_the_moved_contexts(self, db_session, factories):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        [event] = _events(db_session, "family_merged", family_id=scene.world.family.id)
        assert event.payload["moved_contexts"] == 2

    def test_the_lock_order_is_families_schemas_variants_contexts(self, db_session, factories):
        from tests.integration.test_semantic_queue_schema import _suggestion

        scene = self._scene(db_session, factories)
        # Предложение с семьёй-источником и задание значений контекста по
        # схеме источника: слияние блокирует и предложения, и (сверкой) задание.
        _suggestion(
            db_session, factories,
            context=db_session.get(CatalogContext, scene.world.context_id),
            family_id=scene.world.family.id, new_family_name=None,
        )
        reconcile_or_defer(db_session, [scene.second])
        assert [j.schema_id for j in _values_jobs(db_session, scene.second)] == [
            scene.world.schema.id
        ]

        with _capturing_sql(db_session) as statements:
            _merge(db_session, scene.world.family, scene.target, scene.actor)

        tables = [table for table, _mode in _lock_sequence(statements)]
        order = [
            "work_families", "family_parameter_schemas", "work_variants", "catalog_contexts",
            "family_suggestions", "semantic_jobs",
        ]
        assert [tables.index(name) for name in order] == sorted(tables.index(n) for n in order)
        assert all(mode == "UPDATE" for _table, mode in _lock_sequence(statements)[:4])
        # Задание — после доменных строк (Global Constraints): ни одного замка
        # задания раньше последнего замка контекста.
        last_context = max(i for i, t in enumerate(tables) if t == "catalog_contexts")
        assert all(i > last_context for i, t in enumerate(tables) if t == "semantic_jobs")

    def test_a_moved_context_switches_to_a_target_variant_when_its_values_arrive(
        self, db_session, factories
    ):
        """Спека §2.9 (3), приёмка п. 11 «переключение по готовности»: ответ
        значений по текущей версии цели переключает вариант, старый вариант
        опустел и архивирован."""
        from services.work_variants import apply_values
        from tests.integration.test_work_variants_material import _settings
        from tests.integration.test_work_variants_schema_life import _paths_hash

        scene = self._scene(db_session, factories)
        _merge(db_session, scene.world.family, scene.target, scene.actor)

        outcome = apply_values(
            db_session, context_id=scene.world.context_id, schema_id=scene.target_schema.id,
            answer=_answer(_named(1, "а")),
            paths_hash=_paths_hash(db_session, scene.world.context_id), guard=None,
            settings=_settings(),
        )

        assert outcome.applied
        new_variant = db_session.get(WorkVariant, _variant_of(db_session, scene.world.context_id))
        assert new_variant.id != scene.variant_id
        assert (new_variant.family_id, new_variant.schema_id) == (
            scene.target.id, scene.target_schema.id,
        )
        assert db_session.get(WorkVariant, scene.variant_id).status == "archived"
        assert _composite_violations(db_session) == ([], [])


# ---------------------------------------------------------------------------
#  Ветвь 2: у цели текущей версии нет
# ---------------------------------------------------------------------------

class TestTargetHasNoCurrentVersion:
    def _scene(self, db, factories):
        actor = _actor(factories)
        world = _settled_world(db, factories)
        # Версия 3 заведена раньше версии 2: порядок `id` расходится с порядком
        # номеров, и переезд обязан нумеровать по номерам, а не по `id`.
        older = _schema(db, factories, world.family, version=3, status="superseded")
        cancelled = _schema(db, factories, world.family, version=2, status="cancelled")
        target, _none = _target_family(db, factories, with_current=False)
        target_context = _target_context(db, factories, target)
        return SimpleNamespace(
            actor=actor, world=world, cancelled=cancelled, older=older, target=target,
            target_context=target_context, variant_id=_variant_of(db, world.context_id),
        )

    def test_the_frozen_version_of_the_source_becomes_the_current_of_the_target(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        assert _versions(db_session, scene.target) == [
            (1, "frozen", scene.world.schema.id),
            (2, "cancelled", scene.cancelled.id),
            (3, "superseded", scene.older.id),
        ]

    def test_source_contexts_and_variants_keep_their_variant_and_get_no_jobs(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)
        before = db_session.get(CatalogContext, scene.world.context_id)
        variant_at = before.variant_at

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        db_session.expire_all()
        context = db_session.get(CatalogContext, scene.world.context_id)
        assert (context.work_family_id, context.work_variant_id, context.variant_at) == (
            scene.target.id, scene.variant_id, variant_at,
        )
        variant = db_session.get(WorkVariant, scene.variant_id)
        assert (variant.family_id, variant.schema_id) == (scene.target.id, scene.world.schema.id)
        assert _values_jobs(db_session, scene.world.context_id) == []
        assert _composite_violations(db_session) == ([], [])
        # «Заданий не получают» — свойство состояния, а не отсутствие вызова
        # сверки: и явная сверка контекста задания значений не ставит.
        reconcile_or_defer(db_session, [scene.world.context_id])
        assert _values_jobs(db_session, scene.world.context_id) == []

    def test_the_former_contexts_of_the_target_get_the_value_jobs(self, db_session, factories):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.world.family, scene.target, scene.actor)

        [job] = _values_jobs(db_session, scene.target_context)
        assert (job.schema_id, job.status) == (scene.world.schema.id, "pending")


# ---------------------------------------------------------------------------
#  Ветвь 3: схемы нет ни у одной
# ---------------------------------------------------------------------------

class TestNeitherFamilyHasASchema:
    def _scene(self, db, factories):
        actor = _actor(factories)
        source = _family(db, status="active", definition=f"Источник {_uid()}")
        cancelled = _schema(db, factories, source, version=1, status="cancelled")
        target, _none = _target_family(db, factories, with_current=False)
        return SimpleNamespace(
            actor=actor, source=source, cancelled=cancelled, target=target,
        )

    def test_the_target_gets_a_building_version_and_the_schema_job(self, db_session, factories):
        scene = self._scene(db_session, factories)

        _merge(db_session, scene.source, scene.target, scene.actor)

        versions = _versions(db_session, scene.target)
        assert [(v, s) for v, s, _id in versions] == [(1, "cancelled"), (2, "building")]
        assert versions[0][2] == scene.cancelled.id
        [job] = _jobs(db_session, "family_schema", family_id=scene.target.id)
        assert (job.schema_id, job.status) == (versions[1][2], "pending")
        assert _jobs(db_session, "family_schema", family_id=scene.source.id) == []
        assert _composite_violations(db_session) == ([], [])

    def test_the_version_and_the_job_appear_while_the_unit_is_being_requeried(
        self, db_session, factories
    ):
        from tests.integration.test_semantic_queue_schema import _job

        scene = self._scene(db_session, factories)
        busy = _active_world(db_session, factories)
        _job(db_session, factories, context=db_session.get(CatalogContext, busy.context_id))

        _merge(db_session, scene.source, scene.target, scene.actor)

        versions = _versions(db_session, scene.target)
        assert [(v, s) for v, s, _id in versions] == [(1, "cancelled"), (2, "building")]
        [job] = _jobs(db_session, "family_schema", family_id=scene.target.id)
        assert (job.schema_id, job.status) == (versions[1][2], "pending")


# ---------------------------------------------------------------------------
#  Ожидания и предложения
# ---------------------------------------------------------------------------

class TestPendingAndSuggestionsFollowTheMerge:
    def _scene(self, db, factories):
        actor = _actor(factories)
        source = _settled_world(db, factories)
        target, target_schema = _target_family(db, factories, with_current=True)
        waiting = _settled_world(db, factories)
        _set_pending(db, factories, waiting.context_id, source.family)
        reconcile_or_defer(db, [waiting.context_id])
        return SimpleNamespace(
            actor=actor, source=source, target=target, target_schema=target_schema,
            waiting=waiting,
        )

    def test_a_foreign_pending_is_redirected_with_an_event_and_a_new_job(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)
        [old_job] = _values_jobs(db_session, scene.waiting.context_id)
        assert (old_job.schema_id, old_job.status) == (scene.source.schema.id, "pending")

        moved = _merge(db_session, scene.source.family, scene.target, scene.actor)

        # Перенаправленное ожидание — не переехавший контекст: в счёт идёт
        # только контекст источника.
        assert moved == 1
        db_session.expire_all()
        context = db_session.get(CatalogContext, scene.waiting.context_id)
        assert context.pending_family_id == scene.target.id
        assert context.work_family_id == scene.waiting.family.id
        [event] = [
            e for e in _events(db_session, "context_family_pending", context_id=context.id)
        ]
        assert event.payload["outcome"] == "redirected"
        assert event.payload["source"] == "manual"
        # Событие несёт прежнюю ожидаемую семью (источник), как и прочие исходы;
        # новая — в колонке контекста.
        assert event.payload["pending_family_id"] == scene.source.family.id
        jobs = _values_jobs(db_session, scene.waiting.context_id)
        by_schema = {job.schema_id: job.status for job in jobs}
        assert by_schema[scene.source.schema.id] == "cancelled"
        assert by_schema[scene.target_schema.id] == "pending"

    def test_a_pending_for_another_family_is_left_alone(self, db_session, factories):
        scene = self._scene(db_session, factories)
        elsewhere = _settled_world(db_session, factories)
        third, _none = _target_family(db_session, factories, with_current=False)
        _set_pending(db_session, factories, elsewhere.context_id, third)

        _merge(db_session, scene.source.family, scene.target, scene.actor)

        db_session.expire_all()
        assert db_session.get(CatalogContext, elsewhere.context_id).pending_family_id == third.id
        assert _events(db_session, "context_family_pending", context_id=elsewhere.context_id) == []

    def test_a_pending_that_left_the_source_before_its_lock_is_not_redirected(
        self, db_session, factories, monkeypatch
    ):
        """Ожидание сменилось на третью семью между списком кандидатов и их
        замком: под замком контекст перечитывается, и его ожидание слияние не
        трогает. Смена имитируется в той же транзакции прямо перед замком —
        перечитанное под замком видит её так же, как чужой `commit`."""
        scene = self._scene(db_session, factories)
        third, _none = _target_family(db_session, factories, with_current=False)
        real_lock = work_families_module._lock_contexts

        def _switched_meanwhile(db, context_ids):
            db.execute(
                sa.update(CatalogContext)
                .where(CatalogContext.id == scene.waiting.context_id)
                .values(pending_family_id=third.id)
            )
            real_lock(db, context_ids)

        monkeypatch.setattr(work_families_module, "_lock_contexts", _switched_meanwhile)

        _merge(db_session, scene.source.family, scene.target, scene.actor)

        db_session.expire_all()
        assert db_session.get(CatalogContext, scene.waiting.context_id).pending_family_id == (
            third.id
        )
        assert _events(
            db_session, "context_family_pending", context_id=scene.waiting.context_id
        ) == []

    def test_suggestions_of_the_source_now_name_the_target_and_keep_their_decision(
        self, db_session, factories
    ):
        from tests.integration.test_semantic_queue_schema import _suggestion

        scene = self._scene(db_session, factories)
        suggested = _settled_world(db_session, factories)
        pending = _set_pending(
            db_session, factories, suggested.context_id, scene.source.family, source="suggestion"
        )
        user_id = factories.UserFactory.create().id
        pending.decision = "accepted_pending"
        pending.decided_by = user_id
        pending.decided_at = sa.func.now()
        db_session.flush()
        published = _suggestion(
            db_session, factories, context=db_session.get(CatalogContext, scene.waiting.context_id),
            family_id=scene.source.family.id, new_family_name=None, is_published=True,
        )
        unrelated = _suggestion(
            db_session, factories, context=db_session.get(CatalogContext, suggested.context_id),
            family_id=scene.waiting.family.id, new_family_name=None,
        )
        # Неопубликованное предложение источника тоже получает цель: правило
        # «у всех предложений с семьёй-источником», а не только у видимых.
        unpublished = _suggestion(
            db_session, factories, context=db_session.get(CatalogContext, suggested.context_id),
            family_id=scene.source.family.id, new_family_name=None,
        )

        _merge(db_session, scene.source.family, scene.target, scene.actor)

        db_session.expire_all()
        assert db_session.get(FamilySuggestion, unpublished.id).family_id == scene.target.id
        assert db_session.get(FamilySuggestion, pending.id).family_id == scene.target.id
        assert db_session.get(FamilySuggestion, pending.id).decision == "accepted_pending"
        assert db_session.get(FamilySuggestion, pending.id).decided_by == user_id
        assert db_session.get(FamilySuggestion, published.id).family_id == scene.target.id
        assert db_session.get(FamilySuggestion, unrelated.id).family_id == scene.waiting.family.id
        context = db_session.get(CatalogContext, suggested.context_id)
        assert context.pending_family_id == scene.target.id
        assert context.pending_suggestion_id == pending.id
        [event] = _events(db_session, "context_family_pending", context_id=context.id)
        assert event.payload["outcome"] == "redirected"


# ---------------------------------------------------------------------------
#  Составные FK проверяются внутри слияния
# ---------------------------------------------------------------------------

class TestCompositeKeysAreCheckedInsideTheMerge:
    def test_a_version_that_stays_behind_fails_the_merge_itself(
        self, db_session, factories, monkeypatch
    ):
        scene = TestTargetHasACurrentVersion()._scene(db_session, factories)
        monkeypatch.setattr(
            work_families_module, "_move_schema_versions", lambda *args, **kwargs: False
        )

        with pytest.raises(IntegrityError) as raised:
            _merge(db_session, scene.world.family, scene.target, scene.actor)

        assert "fk_work_variants_schema_family" in str(raised.value)

    def test_a_context_left_behind_its_variant_fails_the_merge_itself(
        self, db_session, factories, monkeypatch
    ):
        """Второй составной FK (контекст -> вариант) тоже проверяется внутри
        слияния: контекст с вариантом источника выпадает из переноса (уходит к
        третьей семье между списком и замком, вариант при этом остаётся), вариант
        переезжает к цели — нарушение всплывает здесь, а не на чужом `commit`."""
        scene = TestTargetHasACurrentVersion()._scene(db_session, factories)
        third, _none = _target_family(db_session, factories, with_current=False)
        real_lock = work_families_module._lock_contexts

        def _left_meanwhile(db, context_ids):
            db.execute(
                sa.update(CatalogContext)
                .where(CatalogContext.id == scene.world.context_id)
                .values(work_family_id=third.id)
            )
            real_lock(db, context_ids)

        monkeypatch.setattr(work_families_module, "_lock_contexts", _left_meanwhile)

        with pytest.raises(IntegrityError) as raised:
            _merge(db_session, scene.world.family, scene.target, scene.actor)

        assert "fk_catalog_contexts_work_variant_family" in str(raised.value)


# ---------------------------------------------------------------------------
#  Ожидание, ставшее равным текущей семье
# ---------------------------------------------------------------------------

def _decide(db, factories, suggestion, decision):
    author_id = None if decision.startswith("auto") else factories.UserFactory.create().id
    suggestion.decision = decision
    suggestion.decided_by = author_id
    suggestion.decided_at = sa.func.now()
    db.flush()


def _pending_columns(context):
    return (
        context.pending_family_id, context.pending_family_source, context.pending_suggestion_id,
        context.pending_by, context.pending_threshold, context.pending_at,
    )


class TestPendingThatBecomesTheCurrentFamilyIsApplied:
    def test_a_target_context_waiting_for_the_source_is_applied(self, db_session, factories):
        actor = _actor(factories)
        source = _settled_world(db_session, factories)
        target = _settled_world(db_session, factories)
        suggestion = _set_pending(
            db_session, factories, target.context_id, source.family, source="suggestion"
        )
        _decide(db_session, factories, suggestion, "accepted_pending")
        reconcile_or_defer(db_session, [target.context_id])
        variant_id = _variant_of(db_session, target.context_id)

        _merge(db_session, source.family, target.family, actor)

        db_session.expire_all()
        context = db_session.get(CatalogContext, target.context_id)
        assert _pending_columns(context) == (None,) * 6
        assert (context.work_family_id, context.work_variant_id) == (target.family.id, variant_id)
        outcomes = [
            e.payload["outcome"]
            for e in _events(db_session, "context_family_pending", context_id=context.id)
        ]
        assert outcomes == ["redirected", "applied"]
        assert db_session.get(FamilySuggestion, suggestion.id).decision == "accepted"
        statuses = [job.status for job in _values_jobs(db_session, target.context_id)]
        assert "pending" not in statuses and "running" not in statuses

    def test_a_source_context_waiting_for_the_target_is_applied(self, db_session, factories):
        actor = _actor(factories)
        source = _settled_world(db_session, factories)
        target, target_schema = _target_family(db_session, factories, with_current=True)
        suggestion = _set_pending(
            db_session, factories, source.context_id, target, source="auto_suggestion"
        )
        _decide(db_session, factories, suggestion, "auto_pending")

        _merge(db_session, source.family, target, actor)

        db_session.expire_all()
        context = db_session.get(CatalogContext, source.context_id)
        assert _pending_columns(context) == (None,) * 6
        assert context.work_family_id == target.id
        [event] = _events(db_session, "context_family_pending", context_id=context.id)
        assert event.payload["outcome"] == "applied"
        assert event.payload["source"] == "auto_suggestion"
        assert db_session.get(FamilySuggestion, suggestion.id).decision == "auto_accepted"
        [job] = _values_jobs(db_session, source.context_id)
        assert (job.schema_id, job.status) == (target_schema.id, "pending")

    def test_a_manual_pending_is_cleared_without_a_suggestion(self, db_session, factories):
        actor = _actor(factories)
        source = _settled_world(db_session, factories)
        target, _schema_row = _target_family(db_session, factories, with_current=True)
        _set_pending(db_session, factories, source.context_id, target)

        _merge(db_session, source.family, target, actor)

        db_session.expire_all()
        context = db_session.get(CatalogContext, source.context_id)
        assert _pending_columns(context) == (None,) * 6
        [event] = _events(db_session, "context_family_pending", context_id=context.id)
        assert (event.payload["outcome"], event.payload["suggestion_id"]) == ("applied", None)


class TestTheTransactionKeepsDeferredKeysAfterTheMerge:
    def test_a_second_merge_in_the_same_transaction_is_not_blocked_by_immediate_checks(
        self, db_session, factories
    ):
        actor = _actor(factories)
        first = _settled_world(db_session, factories)
        middle, _schema_row = _target_family(db_session, factories, with_current=True)
        last, _none = _target_family(db_session, factories, with_current=True)
        _merge(db_session, first.family, middle, actor)

        _merge(db_session, middle, last, actor)

        db_session.expire_all()
        assert db_session.get(CatalogContext, first.context_id).work_family_id == last.id
        assert _composite_violations(db_session) == ([], [])


class TestOnlyValueJobsAreReconciled:
    def test_a_third_family_context_waiting_for_the_target_gets_its_value_job(
        self, db_session, factories
    ):
        actor = _actor(factories)
        source = _settled_world(db_session, factories)
        target, _none = _target_family(db_session, factories, with_current=False)
        waiting = _settled_world(db_session, factories)
        _set_pending(db_session, factories, waiting.context_id, target)
        reconcile_or_defer(db_session, [waiting.context_id])
        assert _values_jobs(db_session, waiting.context_id) == []

        _merge(db_session, source.family, target, actor)

        [job] = _values_jobs(db_session, waiting.context_id)
        assert (job.schema_id, job.status) == (source.schema.id, "pending")

    def test_no_paid_suggestion_job_is_queued_for_moved_or_target_contexts(
        self, db_session, factories
    ):
        from tests.integration.test_semantic_queue_schema import _job

        actor = _actor(factories)
        source = _settled_world(db_session, factories)
        target, _none = _target_family(db_session, factories, with_current=False)
        target_context = _target_context(db_session, factories, target)
        contexts = [source.context_id, target_context]
        for context_id in contexts:
            _job(
                db_session, factories, context=db_session.get(CatalogContext, context_id),
                status="done",
            )
        before = _suggestion_jobs(db_session)

        _merge(db_session, source.family, target, actor)

        assert _suggestion_jobs(db_session) == before

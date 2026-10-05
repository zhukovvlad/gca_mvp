"""Снятие и смена семьи, «не работа» и слияние в Review: что происходит с
вариантом, значениями и ожиданием контекста (спека
`2026-10-02-catalog-variants-design.md` §2.5, §2.10).

Каждый путь снятия проверяется своим входом: «не работа», снятие семьи,
слияние в Review, `HEADER`/`TRASH` в Review. У каждого есть соседний вход, на
котором правило не срабатывает: вариант, на котором остался другой контекст,
не архивируется. Гонки двух сессий — в конце файла: две сессии с настоящими
commit-ами, пауза после чтения (`after_cursor_execute`), жёсткие таймауты.
"""
from __future__ import annotations

import re

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    CatalogPosition,
    ContextMember,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterValue,
    SemanticEvent,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services import work_variants as wv
from services.family_change import acquire_family_locks, request_family_change
from services.review import merge_into_position_outcome, set_kind
from services.work_families import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_APPLICABLE,
    REFUSE_CONTEXT_NOT_FOUND,
    WorkFamilyError,
    assign_family,
)
from tests.integration.test_review_contexts import _simple_scene
from tests.integration.test_work_variants_concurrency import (
    _final_variant,
    _is_variant_count,
    _Scene,
    _two_contexts_on_one_variant,
)
from tests.integration.test_work_variants_core import (
    _answer,
    _apply,
    _attach_variant,
    _capturing_sql,
    _events,
    _lock_sequence,
    _named,
    _now,
    _set_pending,
    _sql,
    _world,
)
from tests.integration.test_work_variants_material import _bind, _chain_context, _frozen_schema, _uid
from tests.integration.test_work_variants_schema import _family, _variant

pytestmark = pytest.mark.integration

_CLEARED_VARIANT_COLUMNS = ("work_variant_id", "variant_at", "variant_paths_hash", "variant_split_hint")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _applied(db, factories, **kwargs):
    """Контекст с вариантом и значениями, как их оставляет обработчик значений;
    строка каталога становится `POSITION`."""
    world = _world(db, factories, **kwargs)
    outcome = _apply(db, world)
    assert outcome.applied
    world.variant_id = outcome.variant_id
    return world


def _fresh(db, model, key):
    db.flush()
    db.expire_all()
    return db.get(model, key)


def _context(db, context_id) -> CatalogContext:
    return _fresh(db, CatalogContext, context_id)


def _value_rows(db, context_id) -> int:
    db.flush()
    return db.execute(
        sa.select(sa.func.count()).select_from(ContextParameterValue).where(
            ContextParameterValue.context_id == context_id
        )
    ).scalar_one()


def _variant_status(db, variant_id) -> str:
    return _fresh(db, WorkVariant, variant_id).status


def _neighbour(db, factories, world):
    """Второй контекст той же семьи на том же варианте, со своими значениями."""
    other, _ = _chain_context(db, factories, title=f"Соседняя строка {_uid()}", path_specs=[((), 1)])
    _bind(db, factories, other, family=world.family)
    _attach_variant(db, other, db.get(WorkVariant, world.variant_id))
    parameter_id = next(iter(world.parameters.values()))
    db.add(
        ContextParameterValue(
            context_id=other, schema_id=world.schema.id, parameter_id=parameter_id,
            value_id=None, source="none",
        )
    )
    db.flush()
    return other


def _set_hint(db, context_id):
    db.get(CatalogContext, context_id).variant_split_hint = "path_conflict"
    db.flush()


def _assert_variant_taken_off(db, context_id):
    context = _context(db, context_id)
    for column in _CLEARED_VARIANT_COLUMNS:
        assert getattr(context, column) is None, column
    assert _value_rows(db, context_id) == 0


def _assert_pending_cleared(db, context_id):
    context = _context(db, context_id)
    for column in (
        "pending_family_id", "pending_family_source", "pending_suggestion_id", "pending_by",
        "pending_threshold", "pending_at",
    ):
        assert getattr(context, column) is None, column


def _other_family(db, factories):
    family = _family(db, status="active", definition="Другая семья")
    _frozen_schema(db, factories, family, [[1, "Тип", ["а", "б"]]])
    return family


# ---------------------------------------------------------------------------
#  clear_variant
# ---------------------------------------------------------------------------

class TestClearVariant:
    def test_variant_columns_and_values_are_taken_off_and_the_previous_variant_returned(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        assert _value_rows(db_session, world.context_id) == 2

        previous = wv.clear_variant(db_session, context_id=world.context_id)

        assert previous == world.variant_id
        _assert_variant_taken_off(db_session, world.context_id)

    def test_the_family_of_the_context_is_left_alone(self, db_session, factories):
        world = _applied(db_session, factories)

        wv.clear_variant(db_session, context_id=world.context_id)

        assert _context(db_session, world.context_id).work_family_id == world.family.id

    def test_a_context_without_a_variant_returns_none_and_changes_nothing(
        self, db_session, factories
    ):
        world = _world(db_session, factories)

        assert wv.clear_variant(db_session, context_id=world.context_id) is None
        assert _context(db_session, world.context_id).work_family_id == world.family.id

    def test_the_emptied_variant_is_archived(self, db_session, factories):
        world = _applied(db_session, factories)

        wv.clear_variant(db_session, context_id=world.context_id)

        assert _variant_status(db_session, world.variant_id) == "archived"

    def test_a_variant_with_another_context_stays_active_and_keeps_its_values(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        other = _neighbour(db_session, factories, world)

        wv.clear_variant(db_session, context_id=world.context_id)

        assert _variant_status(db_session, world.variant_id) == "active"
        assert _context(db_session, other).work_variant_id == world.variant_id
        assert _value_rows(db_session, other) == 1


class TestClearVariantRereadsTheContext:
    """`clear_variant` перечитывает контекст под блокировками вызывающего:
    несброшенные правки вызывающего не теряются (сессия без autoflush), а
    прежний вариант берётся из базы, а не из устаревшего объекта сессии —
    захват `acquire_family_locks` сессию не сбрасывает."""

    def test_unflushed_edits_of_the_caller_survive(self, db_session, factories):
        world = _applied(db_session, factories)
        context = db_session.get(CatalogContext, world.context_id)
        assert context.semantic_state != "NOT_APPLICABLE"
        context.semantic_state = "NOT_APPLICABLE"  # не сброшено

        wv.clear_variant(db_session, context_id=world.context_id)

        assert _context(db_session, world.context_id).semantic_state == "NOT_APPLICABLE"

    def test_the_previous_variant_is_read_from_the_database_not_from_the_session(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, variant, _second = _two_contexts_on_one_variant(committing_db, committing_factories)
        stale = committing_db.get(CatalogContext, world.context_id)
        assert stale.work_variant_id == variant.id
        with committing_session_factory() as other:
            assert wv.clear_variant(other, context_id=world.context_id) == variant.id
            other.commit()

        assert acquire_family_locks(
            committing_db, [(world.context_id, None)], release_on_failure=True, lock_variants=True
        ) == set()
        previous = wv.clear_variant(committing_db, context_id=world.context_id)
        committing_db.commit()

        assert previous is None


# ---------------------------------------------------------------------------
#  mark_context_not_work
# ---------------------------------------------------------------------------

class TestMarkContextNotWork:
    def test_state_becomes_not_applicable_and_family_variant_values_are_taken_off(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        context = _context(db_session, world.context_id)
        assert context.semantic_state == "NOT_APPLICABLE"
        for column in ("work_family_id", "family_source", "family_by", "family_at"):
            assert getattr(context, column) is None, column
        _assert_variant_taken_off(db_session, world.context_id)

    def test_the_event_names_the_cleared_family_and_variant(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        [event] = _events(db_session, "context_not_work", context_id=world.context_id)
        assert event.actor_id == user.id
        assert event.payload == {
            "reason": "manual", "cleared_family_id": world.family.id,
            "cleared_variant_id": world.variant_id,
        }

    def test_a_context_without_family_and_variant_is_marked_with_empty_clearings(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        context = db_session.get(CatalogContext, world.context_id)
        context.work_family_id = None
        context.family_source = None
        context.family_by = None
        context.family_at = None
        db_session.flush()
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert _context(db_session, world.context_id).semantic_state == "NOT_APPLICABLE"
        [event] = _events(db_session, "context_not_work", context_id=world.context_id)
        assert event.payload["cleared_family_id"] is None
        assert event.payload["cleared_variant_id"] is None

    @pytest.mark.parametrize("kind", ["POSITION", "TO_REVIEW"])
    def test_the_kind_of_the_catalog_row_is_not_changed(self, db_session, factories, kind):
        world = _world(db_session, factories, catalog_kind=kind)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert _fresh(db_session, CatalogPosition, world.catalog_id).kind == kind

    def test_a_pending_family_is_cancelled_with_its_event(self, db_session, factories):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        _assert_pending_cleared(db_session, world.context_id)
        [event] = _events(db_session, "context_family_pending", context_id=world.context_id)
        assert event.payload["outcome"] == "cancelled"
        assert event.payload["pending_family_id"] == target.id

    def test_the_emptied_variant_is_archived(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert _variant_status(db_session, world.variant_id) == "archived"

    def test_a_variant_with_another_context_stays_active(self, db_session, factories):
        world = _applied(db_session, factories)
        other = _neighbour(db_session, factories, world)
        user = factories.UserFactory.create()

        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert _variant_status(db_session, world.variant_id) == "active"
        neighbour = _context(db_session, other)
        assert neighbour.work_variant_id == world.variant_id
        assert neighbour.semantic_state != "NOT_APPLICABLE"
        assert _value_rows(db_session, other) == 1

    def test_the_second_call_is_refused_and_writes_no_second_event(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()
        wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        with pytest.raises(WorkFamilyError) as caught:
            wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert caught.value.code == REFUSE_CONTEXT_NOT_APPLICABLE
        assert len(_events(db_session, "context_not_work", context_id=world.context_id)) == 1

    def test_an_archived_context_is_refused_and_left_as_it_was(self, db_session, factories):
        world = _applied(db_session, factories)
        db_session.get(CatalogContext, world.context_id).archived_at = _now()
        db_session.flush()
        user = factories.UserFactory.create()

        with pytest.raises(WorkFamilyError) as caught:
            wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        assert caught.value.code == REFUSE_CONTEXT_ARCHIVED
        context = _context(db_session, world.context_id)
        assert context.work_variant_id == world.variant_id
        assert context.semantic_state != "NOT_APPLICABLE"

    def test_an_unknown_context_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()

        with pytest.raises(WorkFamilyError) as caught:
            wv.mark_context_not_work(db_session, context_id=999_999_999, actor_id=user.id)

        assert caught.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_locks_follow_the_feature_order_with_modes(self, db_session, factories):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        with _capturing_sql(db_session) as statements:
            wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

        sequence = _lock_sequence(statements)
        # Строка каталога не нужна (вид не меняется): семьи — общим замком,
        # затем вариант и контекст.
        assert sequence[:3] == [
            ("work_families", "SHARE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
        ]
        assert not any(table == "catalog_positions" for table, _ in sequence)

    def test_the_current_and_the_pending_family_are_locked_by_one_statement_in_id_order(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()
        captured: list[tuple[str, dict]] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            if "FROM work_families" in statement and "FOR SHARE" in statement:
                captured.append((statement, parameters))

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert len(captured) == 1
        statement, parameters = captured[0]
        assert "ORDER BY work_families.id" in statement
        assert sorted(v for v in parameters.values() if isinstance(v, int)) == sorted(
            [world.family.id, target.id]
        )


# ---------------------------------------------------------------------------
#  Снятие семьи
# ---------------------------------------------------------------------------

class TestAssignFamilyNone:
    def _remove(self, db, world, user):
        return assign_family(db, context_id=world.context_id, family_id=None, actor_id=user.id)

    def test_variant_values_hint_and_family_are_taken_off(self, db_session, factories):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        context = _context(db_session, world.context_id)
        for column in ("work_family_id", "family_source", "family_by", "family_at"):
            assert getattr(context, column) is None, column
        _assert_variant_taken_off(db_session, world.context_id)

    def test_a_context_with_a_path_conflict_hint_is_not_stopped_by_the_check(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        outcome = _apply(
            db_session, world, _answer(_named(1, "50 мм"), (2, "conflict", None, None))
        )
        assert outcome.applied
        assert _context(db_session, world.context_id).variant_split_hint == "path_conflict"
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _context(db_session, world.context_id).variant_split_hint is None
        assert _value_rows(db_session, world.context_id) == 0

    def test_the_kind_of_the_catalog_row_stays_position(self, db_session, factories):
        world = _applied(db_session, factories)
        assert _fresh(db_session, CatalogPosition, world.catalog_id).kind == "POSITION"
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _fresh(db_session, CatalogPosition, world.catalog_id).kind == "POSITION"

    def test_a_pending_family_is_cancelled_with_its_event(self, db_session, factories):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        _assert_pending_cleared(db_session, world.context_id)
        [event] = _events(db_session, "context_family_pending", context_id=world.context_id)
        assert event.payload["outcome"] == "cancelled"

    def test_the_emptied_variant_is_archived(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _variant_status(db_session, world.variant_id) == "archived"

    def test_a_variant_with_another_context_stays_active(self, db_session, factories):
        world = _applied(db_session, factories)
        other = _neighbour(db_session, factories, world)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _variant_status(db_session, world.variant_id) == "active"
        assert _context(db_session, other).work_variant_id == world.variant_id
        assert _value_rows(db_session, other) == 1

    def test_the_event_of_the_removal_names_the_old_family(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        [event] = _events(db_session, "context_family_assigned", context_id=world.context_id)
        assert event.payload["from_family_id"] == world.family.id
        assert event.payload["to_family_id"] is None

    def test_locks_follow_the_feature_order_with_modes(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        with _capturing_sql(db_session) as statements:
            self._remove(db_session, world, user)

        assert _lock_sequence(statements)[:3] == [
            ("work_families", "SHARE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
        ]


class TestRequestFamilyChangeRemoval:
    """`family_id=None` у `request_family_change` — снятие семьи (путь 1) и у
    контекста с вариантом: вариант, ожидание и значения уходят вместе с ней."""

    def _remove(self, db, world, user):
        return request_family_change(
            db, context_id=world.context_id, family_id=None, actor_id=user.id, source="manual"
        )

    def test_variant_values_hint_and_family_are_taken_off(self, db_session, factories):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        user = factories.UserFactory.create()

        outcome = self._remove(db_session, world, user)

        assert outcome.kind == "assigned" and outcome.family_id is None
        context = _context(db_session, world.context_id)
        assert context.work_family_id is None
        _assert_variant_taken_off(db_session, world.context_id)
        assert _fresh(db_session, CatalogPosition, world.catalog_id).kind == "POSITION"

    def test_the_emptied_variant_is_archived(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _variant_status(db_session, world.variant_id) == "archived"

    def test_a_variant_with_another_context_stays_active(self, db_session, factories):
        world = _applied(db_session, factories)
        other = _neighbour(db_session, factories, world)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        assert _variant_status(db_session, world.variant_id) == "active"
        assert _context(db_session, other).work_variant_id == world.variant_id
        assert _value_rows(db_session, other) == 1

    def test_a_pending_family_is_cleared_with_its_event(self, db_session, factories):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        self._remove(db_session, world, user)

        _assert_pending_cleared(db_session, world.context_id)
        [event] = _events(db_session, "context_family_pending", context_id=world.context_id)
        assert event.payload["pending_family_id"] == target.id
        assert event.payload["outcome"] == "cancelled"

    def test_displacement_by_another_pending_stays_superseded(self, db_session, factories):
        world = _applied(db_session, factories)
        first = _other_family(db_session, factories)
        second = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, first)
        user = factories.UserFactory.create()

        request_family_change(
            db_session, context_id=world.context_id, family_id=second.id,
            actor_id=user.id, source="manual",
        )

        outcomes = [
            e.payload["outcome"]
            for e in _events(db_session, "context_family_pending", context_id=world.context_id)
        ]
        assert outcomes == ["superseded", "set"]
        assert _context(db_session, world.context_id).pending_family_id == second.id

    def test_locks_follow_the_feature_order_with_modes(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        with _capturing_sql(db_session) as statements:
            self._remove(db_session, world, user)

        assert _lock_sequence(statements)[:3] == [
            ("work_families", "SHARE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
        ]


# ---------------------------------------------------------------------------
#  Слияние в Review
# ---------------------------------------------------------------------------

def _review_scene(db, factories, *, source_variant=True, target_variant=None, params=None):
    """Сцена слияния: контексты источника и цели одной семьи; `target_variant`
    — `"same"` (тот же вариант, что у источника), `"other"` или `None`;
    `params` — параметры схемы (по умолчанию один «Тип»)."""
    scene = _simple_scene(db, factories)
    family = _family(db, status="active", definition="Стяжка пола")
    schema = _frozen_schema(db, factories, family, params or [[1, "Тип", ["а", "б"]]])
    parameter_id = db.execute(
        sa.text("SELECT id FROM family_parameters WHERE schema_id = :s ORDER BY ordinal LIMIT 1"),
        {"s": schema.id},
    ).scalar_one()
    scene.family, scene.schema = family, schema
    _bind(db, factories, scene.source_context_id, family=family)
    _bind(db, factories, scene.target_context_id, family=family)
    scene.source_variant_id = scene.target_variant_id = None
    if source_variant:
        source = _variant(db, family, schema, values_key=f"source-{_uid()}")
        _attach_variant(db, scene.source_context_id, source)
        db.add(
            ContextParameterValue(
                context_id=scene.source_context_id, schema_id=schema.id,
                parameter_id=parameter_id, value_id=None, source="none",
            )
        )
        scene.source_variant_id = source.id
    if target_variant == "same":
        _attach_variant(db, scene.target_context_id, db.get(WorkVariant, scene.source_variant_id))
        scene.target_variant_id = scene.source_variant_id
    elif target_variant == "other":
        other = _variant(db, family, schema, values_key=f"target-{_uid()}")
        _attach_variant(db, scene.target_context_id, other)
        scene.target_variant_id = other.id
    db.flush()
    return scene


def _merge(db, scene):
    return merge_into_position_outcome(
        db, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
    )


def _variant_warnings(outcome):
    return [text for text in outcome.warnings if "вариант" in text]


class TestReviewMerge:
    def test_the_variant_of_the_archived_source_context_is_taken_off(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        _merge(db_session, scene)

        context = _context(db_session, scene.source_context_id)
        assert context.archived_at is not None
        _assert_variant_taken_off(db_session, scene.source_context_id)

    def test_the_emptied_variant_of_the_source_is_archived(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        _merge(db_session, scene)

        assert _variant_status(db_session, scene.source_variant_id) == "archived"

    def test_a_source_variant_with_another_context_stays_active(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")
        other, _ = _chain_context(
            db_session, factories, title=f"Соседняя строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, other, family=scene.family)
        _attach_variant(db_session, other, db_session.get(WorkVariant, scene.source_variant_id))

        _merge(db_session, scene)

        assert _variant_status(db_session, scene.source_variant_id) == "active"
        assert _context(db_session, other).work_variant_id == scene.source_variant_id

    def test_the_target_context_keeps_its_variant(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        _merge(db_session, scene)

        assert _context(db_session, scene.target_context_id).work_variant_id == scene.target_variant_id
        assert _variant_status(db_session, scene.target_variant_id) == "active"

    def test_different_variants_add_one_warning(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        outcome = _merge(db_session, scene)

        assert len(_variant_warnings(outcome)) == 1
        assert len(outcome.warnings) == 1  # семьи равны: строки о семьях нет

    def test_equal_variants_add_no_warning(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="same")

        outcome = _merge(db_session, scene)

        assert outcome.warnings == []
        # Общий вариант остаётся за контекстом цели, поэтому не архивируется.
        assert _variant_status(db_session, scene.source_variant_id) == "active"

    def test_a_target_without_a_variant_is_a_divergence_too(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant=None)

        outcome = _merge(db_session, scene)

        assert len(_variant_warnings(outcome)) == 1

    def test_a_source_without_a_variant_adds_no_warning(self, db_session, factories):
        scene = _review_scene(db_session, factories, source_variant=False, target_variant="other")

        outcome = _merge(db_session, scene)

        assert outcome.warnings == []

    def test_a_divergence_of_families_keeps_its_own_warning_beside_the_variant_one(
        self, db_session, factories
    ):
        scene = _review_scene(db_session, factories, target_variant="other")
        another = _other_family(db_session, factories)
        target = db_session.get(CatalogContext, scene.target_context_id)
        target.work_variant_id = None
        target.variant_at = None
        target.variant_paths_hash = None
        db_session.flush()
        _bind(db_session, factories, scene.target_context_id, family=another)

        outcome = _merge(db_session, scene)

        assert len(outcome.warnings) == 2
        assert len(_variant_warnings(outcome)) == 1

    def test_the_variant_is_read_before_it_is_taken_off(self, db_session, factories):
        # Строка о расхождении называет вариант источника, уже снятый с
        # контекста к моменту переноса членств.
        scene = _review_scene(db_session, factories, target_variant="other")

        outcome = _merge(db_session, scene)

        assert _context(db_session, scene.source_context_id).work_variant_id is None
        assert len(_variant_warnings(outcome)) == 1

    def test_locks_follow_the_feature_order(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        with _capturing_sql(db_session) as statements:
            _merge(db_session, scene)

        tables = [table for table, _mode in _lock_sequence(statements)]
        first = {name: tables.index(name) for name in set(tables)}
        assert (
            first["catalog_positions"] < first["work_families"]
            < first["work_variants"] < first["catalog_contexts"]
        ), tables

    def test_source_and_target_contexts_are_locked_by_one_statement_in_id_order(
        self, db_session, factories
    ):
        # Контексты цели берёт тот же захват, что и контексты источника
        # (`extra_context_ids`): один оператор по возрастанию `id`, а не
        # источник сейчас и цель отдельным замком позже.
        scene = _review_scene(db_session, factories, target_variant="other")
        captured: list[tuple[str, dict]] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            captured.append((statement, parameters))

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            _merge(db_session, scene)
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        lock_index = next(
            i for i, (text, _) in enumerate(captured)
            if "FROM catalog_contexts" in text and re.search(r"FOR UPDATE\s*$", text)
        )
        statement, parameters = captured[lock_index]
        assert "ORDER BY catalog_contexts.id" in statement
        locked = {v for v in parameters.values() if isinstance(v, int)}
        assert {scene.source_context_id, scene.target_context_id} <= locked, (locked, scene)
        # Ни одной записи в контексты до этого замка: другого замка контекстов
        # на пути слияния нет.
        first_write = next(
            i for i, (text, _) in enumerate(captured)
            if text.lstrip().startswith("UPDATE catalog_contexts")
        )
        assert lock_index < first_write


class TestReviewMergeWarningText:
    def test_variants_are_named_by_their_values_in_parameter_order(self, db_session, factories):
        # «Не уточнено» стоит ПЕРВЫМ параметром: соединение без сортировки
        # выводит строки без значения последними (внешнее соединение по
        # хэшу), а параметр с `ordinal = 2` заводится раньше (меньший `id`) —
        # ни `id`, ни порядок вставки не совпадают с порядком параметров.
        scene = _review_scene(
            db_session, factories, source_variant=False,
            params=[[2, "Материал", ["бетон"]], [1, "Тип", ["а", "б"]]],
        )
        parameters = {
            row.ordinal: row.id
            for row in db_session.execute(
                sa.select(FamilyParameter.id, FamilyParameter.ordinal).where(
                    FamilyParameter.schema_id == scene.schema.id
                )
            )
        }
        concrete = db_session.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.parameter_id == parameters[2],
                FamilyParameterValue.value == "бетон",
            )
        ).scalar_one()
        variant = _variant(db_session, scene.family, scene.schema, values_key=f"1=?|2={concrete}")
        for ordinal, value_id in ((2, concrete), (1, None)):
            db_session.add(
                WorkVariantValue(
                    variant_id=variant.id, schema_id=scene.schema.id,
                    parameter_id=parameters[ordinal], value_id=value_id,
                )
            )
            db_session.flush()
        _attach_variant(db_session, scene.source_context_id, variant)

        outcome = _merge(db_session, scene)

        [text] = _variant_warnings(outcome)
        assert "«не уточнено, бетон»" in text, text
        assert "«без варианта»" in text, text

    def test_a_variant_without_value_rows_is_named_by_its_number(self, db_session, factories):
        scene = _review_scene(db_session, factories, target_variant="other")

        outcome = _merge(db_session, scene)

        [text] = _variant_warnings(outcome)
        assert f"«#{scene.source_variant_id}»" in text, text
        assert f"«#{scene.target_variant_id}»" in text, text


# ---------------------------------------------------------------------------
#  HEADER / TRASH в Review
# ---------------------------------------------------------------------------

class TestReviewHeaderOrTrash:
    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_the_variant_hint_and_values_of_the_row_contexts_are_taken_off(
        self, db_session, factories, kind
    ):
        scene = _review_scene(db_session, factories)
        _set_hint(db_session, scene.source_context_id)

        set_kind(db_session, to_review_id=scene.source_cp_id, kind=kind)

        assert _context(db_session, scene.source_context_id).semantic_state == "NOT_APPLICABLE"
        _assert_variant_taken_off(db_session, scene.source_context_id)
        assert _fresh(db_session, CatalogPosition, scene.source_cp_id).kind == kind

    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_the_emptied_variant_is_archived(self, db_session, factories, kind):
        scene = _review_scene(db_session, factories)

        set_kind(db_session, to_review_id=scene.source_cp_id, kind=kind)

        assert _variant_status(db_session, scene.source_variant_id) == "archived"

    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_a_variant_with_a_context_of_another_row_stays_active(
        self, db_session, factories, kind
    ):
        scene = _review_scene(db_session, factories, target_variant="same")

        set_kind(db_session, to_review_id=scene.source_cp_id, kind=kind)

        assert _variant_status(db_session, scene.source_variant_id) == "active"
        assert _context(db_session, scene.target_context_id).work_variant_id == scene.source_variant_id

    def test_position_leaves_the_variant_alone(self, db_session, factories):
        scene = _review_scene(db_session, factories)

        set_kind(db_session, to_review_id=scene.source_cp_id, kind="POSITION")

        assert _context(db_session, scene.source_context_id).work_variant_id == scene.source_variant_id
        assert _variant_status(db_session, scene.source_variant_id) == "active"
        assert _value_rows(db_session, scene.source_context_id) == 1

    def test_locks_follow_the_feature_order(self, db_session, factories):
        scene = _review_scene(db_session, factories)

        with _capturing_sql(db_session) as statements:
            set_kind(db_session, to_review_id=scene.source_cp_id, kind="HEADER")

        tables = [table for table, _mode in _lock_sequence(statements)]
        first = {name: tables.index(name) for name in set(tables)}
        assert (
            first["catalog_positions"] < first["work_families"]
            < first["work_variants"] < first["catalog_contexts"]
        ), tables


# ---------------------------------------------------------------------------
#  Операторы блокировки
# ---------------------------------------------------------------------------

class TestVariantLockStatement:
    def test_variants_are_locked_for_update_in_ascending_id_order(self):
        from services.work_families import _lock_variants_statement

        text = _sql(_lock_variants_statement([7, 3]))

        assert "ORDER BY work_variants.id" in text
        assert re.search(r"FOR UPDATE\s*$", text), text


# ---------------------------------------------------------------------------
#  Гонки
# ---------------------------------------------------------------------------

class TestLastTwoContextsLeaveInParallel:
    def test_the_variant_is_archived_once_when_the_last_two_contexts_are_marked_at_once(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, variant, second = _two_contexts_on_one_variant(committing_db, committing_factories)
        user_id = committing_factories.UserFactory.create().id
        committing_db.commit()
        scene = _Scene(committing_session_factory)

        def mark(context_id):
            def work(db):
                wv.mark_context_not_work(db, context_id=context_id, actor_id=user_id)
                return True

            return work

        try:
            scene.spawn("a", mark(world.context_id), pause_on=_is_variant_count)
            assert scene.wait_paused("a"), "A не дошла до чтения подсчёта"
            scene.spawn("b", mark(second))
            settled = scene.wait_settled("b", contains="work_variants")
        finally:
            scene.finish()

        scene.assert_clean()
        # Без блокировки варианта B не ждёт A, видит её незафиксированную ссылку
        # и не архивирует; A тоже видит ссылку B, и вариант остаётся активным
        # без контекстов.
        assert settled == "blocked", f"B не встала на замок варианта: {settled}"
        assert _final_variant(committing_session_factory, variant.id) == ("archived", 0)
        with committing_session_factory() as check:
            archived = check.execute(
                sa.select(sa.func.count()).select_from(SemanticEvent).where(
                    SemanticEvent.event_type == "context_not_work",
                    SemanticEvent.context_id.in_([world.context_id, second]),
                )
            ).scalar_one()
        assert archived == 2

    def test_the_variant_is_archived_once_when_the_last_two_families_are_removed_at_once(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, variant, second = _two_contexts_on_one_variant(committing_db, committing_factories)
        user_id = committing_factories.UserFactory.create().id
        committing_db.commit()
        scene = _Scene(committing_session_factory)

        def remove(context_id):
            def work(db):
                assign_family(db, context_id=context_id, family_id=None, actor_id=user_id)
                return True

            return work

        try:
            scene.spawn("a", remove(world.context_id), pause_on=_is_variant_count)
            assert scene.wait_paused("a"), "A не дошла до чтения подсчёта"
            scene.spawn("b", remove(second))
            settled = scene.wait_settled("b", contains="work_variants")
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"B не встала на замок варианта: {settled}"
        assert _final_variant(committing_session_factory, variant.id) == ("archived", 0)


class TestVariantLockPrecedesTheContextLock:
    def test_a_writer_that_takes_the_variant_first_is_not_deadlocked_with_the_removal(
        self, committing_db, committing_factories, committing_session_factory
    ):
        # Порядок «вариант -> контекст» — у обработчика значений и у слияния
        # вариантов; снятие обязано идти тем же порядком. Писатель B держит
        # вариант и затем просит контекст; снятие A, взяв сначала контекст,
        # встало бы на варианте B — цикл.
        db, factories = committing_db, committing_factories
        world, variant, _second = _two_contexts_on_one_variant(db, factories)
        user_id = factories.UserFactory.create().id
        db.commit()
        scene = _Scene(committing_session_factory)

        def variant_first(session):
            session.execute(
                sa.select(WorkVariant.id).where(WorkVariant.id == variant.id).with_for_update()
            ).all()
            return session.execute(
                sa.select(CatalogContext.id)
                .where(CatalogContext.id == world.context_id)
                .with_for_update()
            ).scalar_one()

        def is_variant_lock(statement: str) -> bool:
            return (
                statement.lstrip().startswith("SELECT")
                and "FROM work_variants" in statement
                and "FOR UPDATE" in statement
            )

        def mark(session):
            wv.mark_context_not_work(session, context_id=world.context_id, actor_id=user_id)
            return True

        try:
            scene.spawn("b", variant_first, pause_on=is_variant_lock)
            assert scene.wait_paused("b"), "B не взяла вариант"
            scene.spawn("a", mark)
            settled = scene.wait_settled("a", contains="work_variants")
            scene.release["b"].set()
        finally:
            scene.finish()

        assert settled == "blocked", f"A не встала на замок варианта первой: {settled}"
        scene.assert_clean()
        assert not any("deadlock" in error.lower() for error in scene.errors), scene.errors
        assert isinstance(scene.results["a"], bool) and scene.results["a"] is True
        assert scene.results["b"] == world.context_id


class TestVariantIsReadUnderTheFamilyLock:
    def test_a_family_writer_waits_while_the_removal_reads_the_variant(
        self, committing_db, committing_factories, committing_session_factory
    ):
        # Вариант контекста читается ПОСЛЕ замка семьи: под `FOR SHARE` семьи
        # его не сменит ни один писатель (обработчик значений берёт семью
        # `FOR UPDATE`). Прочитай снятие вариант до замка — писатель семьи
        # прошёл бы, пока снятие стоит на прочитанном.
        db, factories = committing_db, committing_factories
        world, _variant_row, _second = _two_contexts_on_one_variant(db, factories)
        user_id = factories.UserFactory.create().id
        db.commit()
        scene = _Scene(committing_session_factory)

        def is_variant_read(statement: str) -> bool:
            return (
                statement.lstrip().startswith("SELECT")
                and "catalog_contexts.work_variant_id" in statement
                and "catalog_contexts.work_family_id" not in statement
                and "count(" not in statement
                and "FOR UPDATE" not in statement
            )

        def mark(session):
            wv.mark_context_not_work(session, context_id=world.context_id, actor_id=user_id)
            return True

        def family_writer(session):
            return session.execute(
                sa.select(WorkFamily.id).where(WorkFamily.id == world.family.id).with_for_update()
            ).scalar_one()

        try:
            scene.spawn("a", mark, pause_on=is_variant_read)
            assert scene.wait_paused("a"), "A не дошла до чтения варианта"
            scene.spawn("b", family_writer)
            settled = scene.wait_settled("b", contains="work_families")
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"писатель семьи не встал на замок снятия: {settled}"
        assert scene.results["a"] is True
        assert scene.results["b"] == world.family.id


def test_member_rows_of_a_marked_context_are_untouched(db_session, factories):
    world = _applied(db_session, factories)
    members = db_session.execute(
        sa.select(sa.func.count()).select_from(ContextMember).where(
            ContextMember.context_id == world.context_id
        )
    ).scalar_one()
    user = factories.UserFactory.create()

    wv.mark_context_not_work(db_session, context_id=world.context_id, actor_id=user.id)

    after = db_session.execute(
        sa.select(sa.func.count()).select_from(ContextMember).where(
            ContextMember.context_id == world.context_id
        )
    ).scalar_one()
    assert after == members and members > 0

"""Глобальная пометка строки каталога `HEADER`/`TRASH` и нормативы (спека
`2026-10-02-catalog-variants-design.md` §2.11).

Правило: любая строка `POSITION` — продвинутая правилом (с вариантом) и
утверждённая человеком — помечается целиком; у строки с нормативом пометка
отказывает с перечнем нормативов и ничего не записывает. Гонки двух сессий — в
конце файла: настоящие commit-ы, пауза после чтения (`after_cursor_execute`),
жёсткие таймауты; без защиты каждый такой тест краснеет, а не виснет.
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from crud.common import DomainError
from crud.rate_standards import (
    create_rate_standard,
    reapprove_rate_standard,
    update_rate_standard,
)
from models import (
    CatalogContext,
    CatalogPosition,
    ContextMember,
    MatchingCache,
    RateStandard,
    SemanticEvent,
    WorkVariant,
)
from services import work_variants as wv
from services.family_change import FamilyLockMismatch
from services.review import set_kind, set_position_kind_global
from services.work_families import REFUSE_INVALID_KIND, WorkFamilyError
from services.work_variants import apply_values
from tests.integration.test_semantic_queue_hooks_work_variants import _values_jobs
from tests.integration.test_work_variants_concurrency import (
    _final_variant,
    _Scene,
    _two_contexts_on_one_variant,
    _values_race_world,
)
from tests.integration.test_work_variants_core import (
    _answer,
    _capturing_sql,
    _events,
    _lock_sequence,
    _named,
    _set_pending,
    _settings,
    _world,
)
from tests.integration.test_work_variants_material import _bind, _chain_context, _uid
from tests.integration.test_work_variants_removal import (
    _applied,
    _assert_pending_cleared,
    _assert_variant_taken_off,
    _context,
    _fresh,
    _neighbour,
    _other_family,
    _set_hint,
    _value_rows,
    _variant_status,
)

pytestmark = pytest.mark.integration

_KINDS = ("HEADER", "TRASH")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _human_position(db, factories, **kwargs):
    """Строка `POSITION`, утверждённая человеком тем же путём, что в Review:
    строка `TO_REVIEW` проходит `set_kind(POSITION)` (ручная запись кэша на
    себя уже есть); семья у контекста есть, варианта нет."""
    world = _world(db, factories, catalog_kind="TO_REVIEW", **kwargs)
    set_kind(db, to_review_id=world.catalog_id, kind="POSITION")
    db.flush()
    return world


def _row_kind(db, catalog_id) -> str:
    return _fresh(db, CatalogPosition, catalog_id).kind


def _second_context_of_the_row(db, factories, world, *, family=None):
    """Второй контекст той же строки каталога (другая корзина)."""
    other, _ = _chain_context(
        db, factories, title=f"Вторая корзина {_uid()}", path_specs=[((), 1)]
    )
    bucket = db.get(CatalogContext, other).bucket
    bucket.catalog_position_id = world.catalog_id
    bucket.work_category_id = db.execute(
        sa.text("SELECT id FROM work_categories ORDER BY id LIMIT 1")
    ).scalar_one()
    db.flush()
    _bind(db, factories, other, family=family)
    return other


def _manual_cache_rows(db, catalog_id):
    db.flush()
    db.expire_all()
    return db.execute(
        sa.select(MatchingCache).where(MatchingCache.catalog_position_id == catalog_id)
    ).scalars().all()


def _standard(db, factories, catalog_id, *, valid_from, valid_to=None, rate_class=None, **extra):
    return factories.RateStandardFactory.create(
        catalog_position=db.get(CatalogPosition, catalog_id),
        **({"rate_class": rate_class} if rate_class is not None else {}),
        valid_from=valid_from,
        valid_to=valid_to,
        **extra,
    )


def _event_count(db, event_type, context_ids) -> int:
    db.flush()
    return db.execute(
        sa.select(sa.func.count()).select_from(SemanticEvent).where(
            SemanticEvent.event_type == event_type, SemanticEvent.context_id.in_(context_ids)
        )
    ).scalar_one()


def _is_standards_read(statement: str) -> bool:
    return statement.lstrip().startswith("SELECT") and "FROM rate_standards" in statement


def _is_position_read(statement: str) -> bool:
    return (
        statement.lstrip().startswith("SELECT")
        and "FROM catalog_positions" in statement
        and "catalog_positions.id" in statement
    )


def _is_variant_lock(statement: str) -> bool:
    return (
        statement.lstrip().startswith("SELECT")
        and "FROM work_variants" in statement
        and "FOR UPDATE" in statement
    )


# ---------------------------------------------------------------------------
#  Что делает пометка
# ---------------------------------------------------------------------------

class TestMarksEveryPositionRow:
    @pytest.mark.parametrize("kind", _KINDS)
    def test_a_row_promoted_by_a_variant_is_marked(self, db_session, factories, kind):
        world = _applied(db_session, factories)
        assert _row_kind(db_session, world.catalog_id) == "POSITION"
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind=kind, actor_id=user.id
        )

        assert _row_kind(db_session, world.catalog_id) == kind
        assert _context(db_session, world.context_id).semantic_state == "NOT_APPLICABLE"

    @pytest.mark.parametrize("kind", _KINDS)
    def test_a_row_approved_by_a_human_is_marked(self, db_session, factories, kind):
        world = _human_position(db_session, factories)
        assert _row_kind(db_session, world.catalog_id) == "POSITION"
        assert _context(db_session, world.context_id).work_variant_id is None
        [before] = _manual_cache_rows(db_session, world.catalog_id)
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind=kind, actor_id=user.id
        )

        assert _row_kind(db_session, world.catalog_id) == kind
        context = _context(db_session, world.context_id)
        assert context.semantic_state == "NOT_APPLICABLE"
        assert context.work_family_id is None
        [after] = _manual_cache_rows(db_session, world.catalog_id)
        assert (after.cache_key, after.source) == (before.cache_key, "manual")

    def test_a_row_without_contexts_is_marked_with_its_cache_entry(
        self, db_session, factories
    ):
        row = factories.CatalogPositionFactory.create(kind="POSITION")
        db_session.flush()
        user = factories.UserFactory.create()

        set_position_kind_global(db_session, position_id=row.id, kind="TRASH", actor_id=user.id)

        assert _row_kind(db_session, row.id) == "TRASH"
        [entry] = _manual_cache_rows(db_session, row.id)
        assert entry.source == "manual"

    def test_every_context_of_the_row_is_marked_and_the_contexts_of_other_rows_are_not(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world, family=world.family)
        neighbour = _neighbour(db_session, factories, world)
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert _context(db_session, world.context_id).semantic_state == "NOT_APPLICABLE"
        assert _context(db_session, second).semantic_state == "NOT_APPLICABLE"
        other = _context(db_session, neighbour)
        assert other.semantic_state != "NOT_APPLICABLE"
        assert other.work_variant_id == world.variant_id
        assert other.work_family_id == world.family.id

    def test_family_variant_values_hint_and_pending_are_taken_off_with_the_event(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="TRASH", actor_id=user.id
        )

        context = _context(db_session, world.context_id)
        for column in ("work_family_id", "family_source", "family_by", "family_at"):
            assert getattr(context, column) is None, column
        _assert_variant_taken_off(db_session, world.context_id)
        _assert_pending_cleared(db_session, world.context_id)
        [event] = _events(db_session, "context_not_work", context_id=world.context_id)
        assert event.actor_id == user.id
        assert event.payload == {
            "reason": "position_kind", "cleared_family_id": world.family.id,
            "cleared_variant_id": world.variant_id,
        }
        [pending_event] = _events(db_session, "context_family_pending", context_id=world.context_id)
        assert pending_event.payload["outcome"] == "cancelled"

    def test_a_context_without_family_and_variant_is_marked_with_empty_clearings(
        self, db_session, factories
    ):
        world = _human_position(db_session, factories)
        user = factories.UserFactory.create()
        wv_context = _context(db_session, world.context_id)
        wv_context.work_family_id = None
        wv_context.family_source = None
        wv_context.family_by = None
        wv_context.family_at = None
        db_session.flush()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        [event] = _events(db_session, "context_not_work", context_id=world.context_id)
        assert event.payload["cleared_family_id"] is None
        assert event.payload["cleared_variant_id"] is None

    def test_a_context_that_was_already_marked_gets_no_second_event(self, db_session, factories):
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world, family=world.family)
        user = factories.UserFactory.create()
        wv.mark_context_not_work(db_session, context_id=second, actor_id=user.id)
        assert len(_events(db_session, "context_not_work", context_id=second)) == 1

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert len(_events(db_session, "context_not_work", context_id=second)) == 1
        assert len(_events(db_session, "context_not_work", context_id=world.context_id)) == 1
        assert _context(db_session, second).semantic_state == "NOT_APPLICABLE"

    def test_a_not_applicable_context_that_holds_a_family_loses_it_with_the_event(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world, family=world.family)
        db_session.get(CatalogContext, second).semantic_state = "NOT_APPLICABLE"
        db_session.flush()
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        context = _context(db_session, second)
        assert context.semantic_state == "NOT_APPLICABLE"
        assert context.work_family_id is None
        [event] = _events(db_session, "context_not_work", context_id=second)
        assert event.payload == {
            "reason": "position_kind", "cleared_family_id": world.family.id,
            "cleared_variant_id": None,
        }

    def test_a_not_applicable_context_that_holds_a_variant_loses_it_and_the_variant_is_archived(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        db_session.get(CatalogContext, world.context_id).semantic_state = "NOT_APPLICABLE"
        db_session.flush()
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="TRASH", actor_id=user.id
        )

        _assert_variant_taken_off(db_session, world.context_id)
        assert _context(db_session, world.context_id).work_family_id is None
        assert _variant_status(db_session, world.variant_id) == "archived"
        [event] = _events(db_session, "context_not_work", context_id=world.context_id)
        assert event.payload["cleared_variant_id"] == world.variant_id

    def test_a_not_applicable_context_that_holds_only_a_pending_family_loses_it_with_the_event(
        self, db_session, factories
    ):
        # Ожидание без текущей семьи — законное состояние (`CK_CONTEXT_PENDING` не
        # требует семьи); «чистым» такой контекст не считается.
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, second, target)
        db_session.get(CatalogContext, second).semantic_state = "NOT_APPLICABLE"
        db_session.flush()
        assert _context(db_session, second).work_family_id is None
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        _assert_pending_cleared(db_session, second)
        [event] = _events(db_session, "context_not_work", context_id=second)
        assert event.payload == {
            "reason": "position_kind", "cleared_family_id": None, "cleared_variant_id": None,
        }

    def test_an_archived_context_of_the_row_is_taken_off_with_the_event(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world, family=world.family)
        db_session.get(CatalogContext, second).archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        context = _context(db_session, second)
        assert context.archived_at is not None
        assert context.semantic_state == "NOT_APPLICABLE"
        assert context.work_family_id is None
        [event] = _events(db_session, "context_not_work", context_id=second)
        assert event.payload == {
            "reason": "position_kind", "cleared_family_id": world.family.id,
            "cleared_variant_id": None,
        }

    def test_member_rows_of_the_marked_contexts_are_untouched(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()
        before = db_session.execute(
            sa.select(sa.func.count()).select_from(ContextMember).where(
                ContextMember.context_id == world.context_id
            )
        ).scalar_one()
        assert before > 0

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        after = db_session.execute(
            sa.select(sa.func.count()).select_from(ContextMember).where(
                ContextMember.context_id == world.context_id
            )
        ).scalar_one()
        assert after == before


class TestEmptiedVariants:
    def test_the_emptied_variant_is_archived(self, db_session, factories):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert _variant_status(db_session, world.variant_id) == "archived"

    def test_a_variant_with_a_context_of_another_row_stays_active_with_its_values(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        other = _neighbour(db_session, factories, world)
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert _variant_status(db_session, world.variant_id) == "active"
        assert _context(db_session, other).work_variant_id == world.variant_id
        assert _value_rows(db_session, other) == 1

    def test_a_variant_shared_by_two_contexts_of_the_row_is_archived_once(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        second = _second_context_of_the_row(db_session, factories, world, family=world.family)
        from tests.integration.test_work_variants_core import _attach_variant

        _attach_variant(db_session, second, db_session.get(WorkVariant, world.variant_id))
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="TRASH", actor_id=user.id
        )

        assert _variant_status(db_session, world.variant_id) == "archived"
        assert _context(db_session, second).work_variant_id is None


class TestManualCache:
    @pytest.mark.parametrize("kind", _KINDS)
    def test_the_row_gets_one_manual_cache_entry_pointing_at_itself(
        self, db_session, factories, kind
    ):
        world = _applied(db_session, factories)
        assert _manual_cache_rows(db_session, world.catalog_id) == []
        title = _fresh(db_session, CatalogPosition, world.catalog_id).standard_job_title
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind=kind, actor_id=user.id
        )

        [entry] = _manual_cache_rows(db_session, world.catalog_id)
        assert entry.source == "manual"
        assert entry.expires_at is None
        assert entry.job_title_text == title

    def test_a_refusal_writes_no_cache_entry(self, db_session, factories):
        world = _applied(db_session, factories)
        _standard(db_session, factories, world.catalog_id, valid_from=dt.date(2025, 1, 1))
        user = factories.UserFactory.create()

        with pytest.raises(WorkFamilyError):
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )

        assert _manual_cache_rows(db_session, world.catalog_id) == []


# ---------------------------------------------------------------------------
#  Отказы
# ---------------------------------------------------------------------------

def _world_snapshot(db, world):
    """Всё, что пометка меняет, одним сравнимым значением."""
    db.flush()
    db.expire_all()
    context = db.get(CatalogContext, world.context_id)
    return {
        "kind": db.get(CatalogPosition, world.catalog_id).kind,
        "state": context.semantic_state,
        "family": context.work_family_id,
        "variant": context.work_variant_id,
        "values": _value_rows(db, world.context_id),
        "events": _event_count(db, "context_not_work", [world.context_id]),
        "cache": len(_manual_cache_rows(db, world.catalog_id)),
    }


class TestRefusals:
    @pytest.mark.parametrize("current", ["TO_REVIEW", "HEADER", "TRASH"])
    def test_a_row_that_is_not_a_position_is_refused_and_left_as_it_was(
        self, db_session, factories, current
    ):
        world = _world(db_session, factories, catalog_kind=current)
        user = factories.UserFactory.create()
        before = _world_snapshot(db_session, world)

        with pytest.raises(WorkFamilyError) as caught:
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )

        assert caught.value.code == "position_not_position"
        assert _world_snapshot(db_session, world) == before

    @pytest.mark.parametrize("kind", ["POSITION", "TO_REVIEW", "WORK", ""])
    def test_a_kind_other_than_header_or_trash_is_refused(self, db_session, factories, kind):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()
        before = _world_snapshot(db_session, world)

        with pytest.raises(WorkFamilyError) as caught:
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind=kind, actor_id=user.id
            )

        assert caught.value.code == REFUSE_INVALID_KIND
        assert _world_snapshot(db_session, world) == before

    def test_an_unknown_row_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()

        with pytest.raises(WorkFamilyError) as caught:
            set_position_kind_global(
                db_session, position_id=999_999_999, kind="HEADER", actor_id=user.id
            )

        assert caught.value.code == "position_not_found"

    def test_rate_standards_refuse_with_the_full_list_and_nothing_is_written(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        _set_hint(db_session, world.context_id)
        # Порядок вставки (куча, классы) обратен порядку id: перечень обязан идти
        # по id, а не по тому, как строки легли в таблицу.
        base = db_session.execute(
            sa.select(sa.func.coalesce(sa.func.max(RateStandard.id), 0))
        ).scalar_one() + 1000
        first = _standard(
            db_session, factories, world.catalog_id,
            valid_from=dt.date(2025, 1, 1), valid_to=dt.date(2025, 7, 1), id=base + 2,
        )
        second = _standard(
            db_session, factories, world.catalog_id, valid_from=dt.date(2026, 3, 1), id=base + 1,
        )
        first_class, second_class = first.rate_class, second.rate_class
        expected = [
            {
                "id": second.id, "rate_class_id": second_class.id,
                "rate_class_title": second_class.title,
                "valid_from": dt.date(2026, 3, 1), "valid_to": None,
            },
            {
                "id": first.id, "rate_class_id": first_class.id,
                "rate_class_title": first_class.title,
                "valid_from": dt.date(2025, 1, 1), "valid_to": dt.date(2025, 7, 1),
            },
        ]
        user = factories.UserFactory.create()
        before = _world_snapshot(db_session, world)
        assert before["variant"] == world.variant_id

        with pytest.raises(WorkFamilyError) as caught:
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="TRASH", actor_id=user.id
            )

        assert caught.value.code == "position_has_standards"
        assert caught.value.standards == expected  # перечень по возрастанию id
        assert _world_snapshot(db_session, world) == before
        context = _context(db_session, world.context_id)
        assert context.variant_split_hint == "path_conflict"
        assert context.work_family_id == world.family.id
        assert _variant_status(db_session, world.variant_id) == "active"

    def test_a_standard_of_another_row_does_not_stop_the_mark(self, db_session, factories):
        world = _applied(db_session, factories)
        other = _human_position(db_session, factories)
        _standard(db_session, factories, other.catalog_id, valid_from=dt.date(2025, 1, 1))
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert _row_kind(db_session, world.catalog_id) == "HEADER"
        assert _row_kind(db_session, other.catalog_id) == "POSITION"

    def test_the_standards_stay_untouched_by_a_successful_mark_of_another_row(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        other = _human_position(db_session, factories)
        standard = _standard(db_session, factories, other.catalog_id, valid_from=dt.date(2025, 1, 1))
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert db_session.get(RateStandard, standard.id).catalog_position_id == other.catalog_id


# ---------------------------------------------------------------------------
#  Блокировки и сверка
# ---------------------------------------------------------------------------

class TestLocks:
    def test_locks_follow_the_feature_order_and_the_families_are_taken_for_update(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()

        with _capturing_sql(db_session) as statements:
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )

        sequence = _lock_sequence(statements)
        assert sequence[:4] == [
            ("catalog_positions", "UPDATE"),
            ("work_families", "UPDATE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
        ]
        assert ("work_families", "SHARE") not in sequence
        tables = [table for table, _mode in sequence]
        if "semantic_jobs" in tables:
            assert tables.index("semantic_jobs") > tables.index("catalog_contexts"), tables

    def test_the_current_and_the_pending_family_are_locked_by_one_statement_in_id_order(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        target = _other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target)
        user = factories.UserFactory.create()
        captured: list[tuple[str, dict]] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            if "FROM work_families" in statement and "FOR UPDATE" in statement:
                captured.append((statement, parameters))

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert len(captured) == 1
        statement, parameters = captured[0]
        assert "ORDER BY work_families.id" in statement
        assert sorted(v for v in parameters.values() if isinstance(v, int)) == sorted(
            [world.family.id, target.id]
        )

    def test_a_refusal_for_standards_takes_no_lock_below_the_row(self, db_session, factories):
        world = _applied(db_session, factories)
        _standard(db_session, factories, world.catalog_id, valid_from=dt.date(2025, 1, 1))
        user = factories.UserFactory.create()

        with _capturing_sql(db_session) as statements, pytest.raises(WorkFamilyError):
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )

        assert _lock_sequence(statements) == [("catalog_positions", "UPDATE")]

    def test_the_family_of_the_context_changing_under_the_lock_is_a_lock_mismatch(
        self, db_session, factories, monkeypatch
    ):
        world = _applied(db_session, factories)
        user = factories.UserFactory.create()
        monkeypatch.setattr(
            "services.review.acquire_family_locks", lambda *args, **kwargs: {world.context_id}
        )

        with pytest.raises(FamilyLockMismatch):
            set_position_kind_global(
                db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
            )

        assert _row_kind(db_session, world.catalog_id) == "POSITION"


class TestReconciles:
    def test_the_values_job_of_the_marked_context_is_cancelled_in_the_same_transaction(
        self, db_session, factories
    ):
        from tests.integration.test_work_variants_core import _apply

        world = _world(db_session, factories)
        assert _apply(db_session, world, paths_hash="paths-of-the-request").applied
        [job] = _values_jobs(db_session, context_id=world.context_id)
        assert job.status == "pending"
        user = factories.UserFactory.create()

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        assert db_session.in_transaction()
        [job] = _values_jobs(db_session, context_id=world.context_id)
        assert job.status == "cancelled"

    def test_the_job_of_a_context_of_another_row_stays_in_the_queue(self, db_session, factories):
        from tests.integration.test_work_variants_core import _apply

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

        set_position_kind_global(
            db_session, position_id=world.catalog_id, kind="HEADER", actor_id=user.id
        )

        [other_job] = _values_jobs(db_session, context_id=other)
        assert other_job.status == "pending"


class TestOnlyCreationLocksTheRow:
    def test_update_and_reapprove_of_a_standard_take_no_lock_on_the_row(
        self, db_session, factories
    ):
        world = _applied(db_session, factories)
        standard = _standard(db_session, factories, world.catalog_id, valid_from=dt.date(2025, 1, 1))

        with _capturing_sql(db_session) as statements:
            update_rate_standard(db_session, standard.id, note="правка ввода")
            reapprove_rate_standard(
                db_session, standard.id, valid_from=dt.date(2026, 1, 1),
                standard_unit_rate=120,
            )

        assert [
            pair for pair in _lock_sequence(statements) if pair[0] == "catalog_positions"
        ] == []

    def test_creation_takes_the_row_for_share(self, db_session, factories):
        world = _applied(db_session, factories)
        rate_class = factories.RateClassFactory.create()

        with _capturing_sql(db_session) as statements:
            create_rate_standard(
                db_session, catalog_position_id=world.catalog_id, rate_class_id=rate_class.id,
                standard_unit_rate=100, valid_from=dt.date(2025, 1, 1),
            )

        assert ("catalog_positions", "SHARE") in _lock_sequence(statements)


# ---------------------------------------------------------------------------
#  Устаревшая копия в сессии вызывающего (populate_existing)
# ---------------------------------------------------------------------------

class TestStaleSessionCopies:
    def test_creation_with_a_stale_position_in_the_session_sees_the_committed_mark(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, _variant, _second, user_id = _committed_position_world(db, factories)
        rate_class_id = factories.RateClassFactory.create().id
        db.commit()
        stale = db.get(CatalogPosition, world.catalog_id)  # ссылка держит identity map
        assert stale.kind == "POSITION"
        with committing_session_factory() as other:
            set_position_kind_global(
                other, position_id=world.catalog_id, kind="HEADER", actor_id=user_id
            )
            other.commit()

        with pytest.raises(DomainError) as caught:
            create_rate_standard(
                db, catalog_position_id=world.catalog_id, rate_class_id=rate_class_id,
                standard_unit_rate=100, valid_from=dt.date(2025, 1, 1),
            )

        assert caught.value.status_code == 422
        db.rollback()
        assert _standards_count(committing_session_factory, world.catalog_id) == 0

    def test_the_mark_with_a_stale_context_in_the_session_takes_off_the_committed_family(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, _variant, _second, user_id = _committed_position_world(db, factories)
        context = db.get(CatalogContext, world.context_id)
        context.work_variant_id = None
        context.variant_at = None
        context.variant_paths_hash = None
        context.work_family_id = None
        context.family_source = None
        context.family_by = None
        context.family_at = None
        db.commit()
        stale = db.get(CatalogContext, world.context_id)  # ссылка держит identity map
        assert stale.work_family_id is None
        with committing_session_factory() as other:
            fresh = other.get(CatalogContext, world.context_id)
            fresh.work_family_id = world.family.id
            fresh.family_source = "manual"
            fresh.family_by = user_id
            fresh.family_at = dt.datetime.now(dt.UTC)
            other.commit()

        set_position_kind_global(db, position_id=world.catalog_id, kind="HEADER", actor_id=user_id)
        db.commit()

        with committing_session_factory() as probe:
            marked = probe.get(CatalogContext, world.context_id)
            assert marked.semantic_state == "NOT_APPLICABLE"
            assert marked.work_family_id is None
            [event] = probe.execute(
                sa.select(SemanticEvent).where(
                    SemanticEvent.event_type == "context_not_work",
                    SemanticEvent.context_id == world.context_id,
                )
            ).scalars().all()
            assert event.payload["cleared_family_id"] == world.family.id


# ---------------------------------------------------------------------------
#  Гонки
# ---------------------------------------------------------------------------

def _committed_position_world(db, factories):
    """Два контекста на одном варианте; строка первого — `POSITION`."""
    world, variant, second = _two_contexts_on_one_variant(db, factories)
    db.get(CatalogPosition, world.catalog_id).kind = "POSITION"
    user_id = factories.UserFactory.create().id
    db.commit()
    return world, variant, second, user_id


def _create_standard_work(catalog_id, rate_class_id):
    """Работа потока: создание норматива; отказ доменной ошибкой возвращается
    значением (поток не падает)."""

    def work(db):
        try:
            return create_rate_standard(
                db, catalog_position_id=catalog_id, rate_class_id=rate_class_id,
                standard_unit_rate=100, valid_from=dt.date(2025, 1, 1),
            )
        except DomainError as exc:
            db.rollback()
            return ("refused", exc.status_code)

    return work


def _mark_work(catalog_id, user_id, kind="HEADER"):
    def work(db):
        try:
            set_position_kind_global(db, position_id=catalog_id, kind=kind, actor_id=user_id)
        except WorkFamilyError as exc:
            db.rollback()
            return ("refused", exc.code)
        return "marked"

    return work


def _standards_count(session_factory, catalog_id) -> int:
    with session_factory() as db:
        return db.execute(
            sa.select(sa.func.count()).select_from(RateStandard).where(
                RateStandard.catalog_position_id == catalog_id
            )
        ).scalar_one()


def _kind_of(session_factory, catalog_id) -> str:
    with session_factory() as db:
        return db.get(CatalogPosition, catalog_id).kind


class TestMarkVersusCreateRateStandard:
    def test_a_standard_created_first_makes_the_mark_refuse(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, _variant, _second, user_id = _committed_position_world(db, factories)
        rate_class_id = factories.RateClassFactory.create().id
        db.commit()
        scene = _Scene(committing_session_factory)

        try:
            scene.spawn(
                "create", _create_standard_work(world.catalog_id, rate_class_id),
                pause_on=_is_position_read,
            )
            assert scene.wait_paused("create"), "создание не дошло до чтения строки"
            scene.spawn("mark", _mark_work(world.catalog_id, user_id))
            settled = scene.wait_settled("mark", contains="catalog_positions")
        finally:
            scene.finish()

        scene.assert_clean()
        # Без общего замка строки пометка проходит, пока создание стоит после
        # чтения: оба успешны, норматив остаётся на строке `HEADER`.
        assert settled == "blocked", f"пометка не встала на замок строки: {settled}"
        assert isinstance(scene.results["create"], dict), scene.results
        assert scene.results["mark"] == ("refused", "position_has_standards")
        assert _kind_of(committing_session_factory, world.catalog_id) == "POSITION"
        assert _standards_count(committing_session_factory, world.catalog_id) == 1

    def test_a_mark_made_first_makes_the_creation_refuse(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, _variant, _second, user_id = _committed_position_world(db, factories)
        rate_class_id = factories.RateClassFactory.create().id
        db.commit()
        scene = _Scene(committing_session_factory)

        try:
            scene.spawn(
                "mark", _mark_work(world.catalog_id, user_id), pause_on=_is_standards_read
            )
            assert scene.wait_paused("mark"), "пометка не дошла до чтения нормативов"
            scene.spawn("create", _create_standard_work(world.catalog_id, rate_class_id))
            settled = scene.wait_settled("create", contains="catalog_positions")
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"создание не встало на замок строки: {settled}"
        assert scene.results["mark"] == "marked"
        assert scene.results["create"] == ("refused", 422)
        assert _kind_of(committing_session_factory, world.catalog_id) == "HEADER"
        assert _standards_count(committing_session_factory, world.catalog_id) == 0


class TestMarkVersusValueWriters:
    def test_a_writer_that_takes_the_variant_first_is_not_deadlocked_with_the_mark(
        self, committing_db, committing_factories, committing_session_factory
    ):
        # Порядок «вариант -> контекст» — у обработчика значений; пометка
        # обязана идти тем же порядком. Писатель B держит вариант и затем
        # просит контекст; пометка, взяв сначала контекст, встала бы на
        # варианте B — цикл.
        db, factories = committing_db, committing_factories
        world, variant, _second, user_id = _committed_position_world(db, factories)
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

        try:
            scene.spawn("b", variant_first, pause_on=_is_variant_lock)
            assert scene.wait_paused("b"), "B не взяла вариант"
            scene.spawn("a", _mark_work(world.catalog_id, user_id))
            settled = scene.wait_settled("a", contains="work_variants")
            scene.release["b"].set()
        finally:
            scene.finish()

        assert settled == "blocked", f"пометка не встала на замок варианта: {settled}"
        scene.assert_clean()
        assert not any("deadlock" in error.lower() for error in scene.errors), scene.errors
        assert scene.results["a"] == "marked"
        assert scene.results["b"] == world.context_id
        assert _kind_of(committing_session_factory, world.catalog_id) == "HEADER"

    def test_the_values_handler_in_progress_is_waited_for_and_its_variant_is_taken_off(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, _job, guard = _values_race_world(db, factories)
        user_id = factories.UserFactory.create().id
        db.commit()
        scene = _Scene(committing_session_factory)

        def apply(session):
            return apply_values(
                session, context_id=world.context_id, schema_id=world.schema.id,
                answer=_answer(_named(1, "50 мм"), _named(2, "бетон")), paths_hash="paths-a",
                guard=guard, settings=_settings(),
            )

        try:
            scene.spawn("apply", apply, pause_on=_is_variant_lock)
            assert scene.wait_paused("apply"), "обработчик значений не взял вариант"
            scene.spawn("mark", _mark_work(world.catalog_id, user_id))
            settled = scene.wait_settled("mark", contains="catalog_positions")
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"пометка не встала на замок строки: {settled}"
        assert scene.results["apply"].applied is True
        assert scene.results["mark"] == "marked"
        with committing_session_factory() as probe:
            context = probe.get(CatalogContext, world.context_id)
            assert context.semantic_state == "NOT_APPLICABLE"
            assert context.work_variant_id is None
            variant_id = scene.results["apply"].variant_id
        assert _final_variant(committing_session_factory, variant_id) == ("archived", 0)
        assert _kind_of(committing_session_factory, world.catalog_id) == "HEADER"

    def test_the_mark_in_progress_is_waited_for_by_the_values_handler(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world, variant, _second, user_id = _committed_position_world(db, factories)
        scene = _Scene(committing_session_factory)

        def apply(session):
            return apply_values(
                session, context_id=world.context_id, schema_id=world.schema.id,
                answer=_answer(_named(1, "50 мм"), _named(2, "бетон")), paths_hash="paths-a",
                guard=None, settings=_settings(),
            )

        try:
            scene.spawn("mark", _mark_work(world.catalog_id, user_id), pause_on=_is_variant_lock)
            assert scene.wait_paused("mark"), "пометка не взяла вариант"
            scene.spawn("apply", apply)
            settled = scene.wait_settled("apply", contains="catalog_positions")
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", f"обработчик значений не встал на замок строки: {settled}"
        assert scene.results["mark"] == "marked"
        outcome = scene.results["apply"]
        # Семья контекста снята пометкой, схема ответа ей уже не принадлежит.
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        with committing_session_factory() as probe:
            context = probe.get(CatalogContext, world.context_id)
            assert context.semantic_state == "NOT_APPLICABLE"
            assert context.work_variant_id is None
            assert probe.get(WorkVariant, variant.id).status == "active"  # второй контекст остался

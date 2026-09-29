"""`assign_family(source, suggestion_id)`, `payload` события и сверка очереди
при назначении и снятии семьи (задача 7 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 7.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§1.10, §2.7 (инвариант), §2.9, §2.14.

Один тест — одно строго отличающееся свойство. Помощники — ЛОКАЛЬНАЯ копия
помощников `test_semantic_queue_reconcile.py`, не импорт."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.work_families as work_families_module
from config import settings
from models import (
    CatalogContext,
    FamilySource,
    SemanticCancelReason,
    SemanticEvent,
    SemanticJob,
    SemanticJobStatus,
    SemanticReconcileBatch,
)
from services.context_routing import route_position
from services.semantic_events import SemanticEventError, record_event
from services.semantic_reconcile import NO_CAP, reconcile_semantic_jobs
from services.work_families import activate_family, assign_family, create_family

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _unit_id(db, code):
    from services.unit_resolution import UnitResolver

    return UnitResolver(db).resolve(code).unit_id


def _active_family(db, *, title, unit_name, actor_id, definition="Определение семьи"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id)
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


def _context(db, factories, *, title) -> int:
    """Одна каталожная строка без раздела — своя корзина и контекст."""
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot)
    cp = factories.CatalogPositionFactory.create(unit_id=_unit_id(db, "M2"), standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    return route_position(db, position_item_id=position.id).context_id


def _setup(db, factories, *, title):
    """Пользователь, активная семья с определением и контекст той же единицы."""
    user = factories.UserFactory.create()
    family = _active_family(db, title=f"Семья {title}", unit_name="M2", actor_id=user.id)
    return user, family, _context(db, factories, title=title)


def _events(db, context_id):
    return (
        db.execute(
            sa.select(SemanticEvent)
            .where(
                SemanticEvent.context_id == context_id,
                SemanticEvent.event_type == "context_family_assigned",
            )
            .order_by(SemanticEvent.id)
        )
        .scalars()
        .all()
    )


def _jobs(db, context_id):
    return db.execute(sa.select(SemanticJob).where(SemanticJob.context_id == context_id)).scalars().all()


# ---------------------------------------------------------------------------
#  Назначение предложением
# ---------------------------------------------------------------------------

class TestAssignBySuggestion:
    def test_provenance_fields(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Назначение предложением")

        context = assign_family(
            db_session,
            context_id=context_id,
            family_id=family.id,
            actor_id=user.id,
            source=FamilySource.suggestion,
            suggestion_id=4242,
        )
        db_session.flush()  # CHECK проверяется на flush

        assert context.work_family_id == family.id
        assert context.family_source == "suggestion"
        assert context.family_by is None
        assert context.family_at is not None
        # Строка в базе, а не состояние объекта в сессии.
        row = db_session.execute(
            sa.text(
                "SELECT work_family_id, family_source, family_by, family_at "
                "FROM catalog_contexts WHERE id = :id"
            ),
            {"id": context_id},
        ).one()
        assert row.work_family_id == family.id
        assert row.family_source == "suggestion"
        assert row.family_by is None
        assert row.family_at is not None

    def test_event_carries_source_suggestion_id_and_actor(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Событие предложения")

        assign_family(
            db_session,
            context_id=context_id,
            family_id=family.id,
            actor_id=user.id,
            source=FamilySource.suggestion,
            suggestion_id=4242,
        )

        (event,) = _events(db_session, context_id)
        assert event.payload == {
            "from_family_id": None,
            "to_family_id": family.id,
            "source": "suggestion",
            "suggestion_id": 4242,
        }
        assert event.actor_id == user.id

    def test_manual_event_payload_has_no_suggestion_id(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Ручное назначение")

        context = assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)

        assert context.family_source == "manual"
        assert context.family_by == user.id
        (event,) = _events(db_session, context_id)
        assert event.payload == {
            "from_family_id": None,
            "to_family_id": family.id,
            "source": "manual",
        }

    def test_suggestion_replaces_manual_assignment_clearing_family_by(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Замена ручного")
        other = _active_family(db_session, title="Другая семья замены", unit_name="M2", actor_id=user.id)
        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)

        context = assign_family(
            db_session,
            context_id=context_id,
            family_id=other.id,
            actor_id=user.id,
            source=FamilySource.suggestion,
            suggestion_id=7,
        )
        db_session.flush()

        assert context.family_source == "suggestion"
        assert context.family_by is None
        assert _events(db_session, context_id)[-1].payload["from_family_id"] == family.id


# ---------------------------------------------------------------------------
#  Контракт вызова: ошибка программиста до любой записи
# ---------------------------------------------------------------------------

class TestCallContract:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"source": FamilySource.suggestion},
            {"source": FamilySource.suggestion, "suggestion_id": None},
            {"source": FamilySource.manual, "suggestion_id": 5},
        ],
        ids=["suggestion-without-id", "suggestion-with-explicit-none", "manual-with-id"],
    )
    def test_inconsistent_source_and_suggestion_id_raise_before_write(self, db_session, factories, kwargs):
        user, family, context_id = _setup(db_session, factories, title="Контракт вызова")

        with pytest.raises(ValueError):
            assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id, **kwargs)

        assert _events(db_session, context_id) == []
        context = db_session.get(CatalogContext, context_id)
        assert context.work_family_id is None
        assert context.family_source is None

    def test_suggestion_cannot_clear_family(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Снятие предложением")
        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        events_before = len(_events(db_session, context_id))

        with pytest.raises(ValueError):
            assign_family(
                db_session,
                context_id=context_id,
                family_id=None,
                actor_id=user.id,
                source=FamilySource.suggestion,
                suggestion_id=9,
            )

        db_session.expire_all()
        assert db_session.get(CatalogContext, context_id).work_family_id == family.id
        assert len(_events(db_session, context_id)) == events_before

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"family_id": 1, "source": FamilySource.suggestion},
            {"family_id": 1, "source": FamilySource.suggestion, "suggestion_id": None},
            {"family_id": 1, "source": FamilySource.manual, "suggestion_id": 5},
            {"family_id": None, "source": FamilySource.suggestion, "suggestion_id": 1},
        ],
        ids=["suggestion-without-id", "suggestion-with-explicit-none", "manual-with-id", "suggestion-clear"],
    )
    def test_value_error_precedes_context_lookup(self, db_session, factories, kwargs):
        user = factories.UserFactory.create()

        # Несуществующий контекст дал бы WorkFamilyError уже на чтении — до любой
        # блокировки; ошибка контракта обязана прозвучать раньше него.
        with pytest.raises(ValueError):
            assign_family(db_session, context_id=987654321, actor_id=user.id, **kwargs)


# ---------------------------------------------------------------------------
#  record_event: условный ключ suggestion_id
# ---------------------------------------------------------------------------

class TestRecordEventSuggestionId:
    def _payload(self, source, **extra):
        return {"from_family_id": None, "to_family_id": None, "source": source, **extra}

    def _record(self, db, context_id, user_id, payload):
        return record_event(
            db,
            event_type="context_family_assigned",
            context_id=context_id,
            actor_id=user_id,
            payload=payload,
        )

    def test_suggestion_without_key_is_rejected(self, db_session, factories):
        user, _, context_id = _setup(db_session, factories, title="Событие без ключа")

        with pytest.raises(SemanticEventError, match="suggestion_id"):
            self._record(db_session, context_id, user.id, self._payload("suggestion"))

    @pytest.mark.parametrize("bad", [None, True, "12", 1.5], ids=["none", "bool", "str", "float"])
    def test_suggestion_with_non_int_key_is_rejected(self, db_session, factories, bad):
        user, _, context_id = _setup(db_session, factories, title="Событие с плохим ключом")

        with pytest.raises(SemanticEventError, match="suggestion_id"):
            self._record(db_session, context_id, user.id, self._payload("suggestion", suggestion_id=bad))

    def test_suggestion_with_int_key_is_accepted(self, db_session, factories):
        user, _, context_id = _setup(db_session, factories, title="Событие с ключом")

        event = self._record(db_session, context_id, user.id, self._payload("suggestion", suggestion_id=3))

        assert event.payload["suggestion_id"] == 3

    def test_manual_with_key_is_rejected(self, db_session, factories):
        user, _, context_id = _setup(db_session, factories, title="Ручное с ключом")

        with pytest.raises(SemanticEventError, match="suggestion_id"):
            self._record(db_session, context_id, user.id, self._payload("manual", suggestion_id=3))

    def test_manual_without_key_is_accepted(self, db_session, factories):
        user, _, context_id = _setup(db_session, factories, title="Ручное без ключа")

        event = self._record(db_session, context_id, user.id, self._payload("manual"))

        assert "suggestion_id" not in event.payload


# ---------------------------------------------------------------------------
#  Сверка очереди при назначении и снятии
# ---------------------------------------------------------------------------

class TestReconcileOnAssignAndUnassign:
    def test_unassign_creates_pending_for_applicable_context(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Снятие создаёт задание")
        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        assert _jobs(db_session, context_id) == []

        assign_family(db_session, context_id=context_id, family_id=None, actor_id=user.id)

        (job,) = _jobs(db_session, context_id)
        assert job.status == SemanticJobStatus.pending.value

    def test_assign_cancels_open_pending_as_not_applicable(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Назначение отменяет задание")
        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")
        (job,) = _jobs(db_session, context_id)
        assert job.status == SemanticJobStatus.pending.value

        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)

        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert job.status == SemanticJobStatus.cancelled.value
        assert job.cancel_reason == SemanticCancelReason.not_applicable.value

    def test_assign_by_suggestion_also_reconciles(self, db_session, factories):
        user, family, context_id = _setup(db_session, factories, title="Предложение отменяет задание")
        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")
        (job,) = _jobs(db_session, context_id)

        assign_family(
            db_session,
            context_id=context_id,
            family_id=family.id,
            actor_id=user.id,
            source=FamilySource.suggestion,
            suggestion_id=11,
        )

        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == SemanticJobStatus.cancelled.value

    def test_event_cap_is_read_from_settings_at_call_time(self, db_session, factories, monkeypatch):
        user, family, context_id = _setup(db_session, factories, title="Потолок из настроек")
        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_RESERVE_USD", Decimal("1000000"))

        assign_family(db_session, context_id=context_id, family_id=None, actor_id=user.id)

        assert _jobs(db_session, context_id) == []
        held = db_session.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert [batch.status for batch in held] == ["held"]

    def test_reconcile_failure_propagates_and_rolls_back_assignment(
        self, db_session, factories, monkeypatch
    ):
        user, family, context_id = _setup(db_session, factories, title="Сверка упала")

        def _failing_reconcile(*args, **kwargs):
            raise RuntimeError("сверка упала")

        monkeypatch.setattr(work_families_module, "reconcile_semantic_jobs", _failing_reconcile)
        savepoint = db_session.begin_nested()

        with pytest.raises(RuntimeError, match="сверка упала"):
            assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        savepoint.rollback()

        db_session.expire_all()
        assert db_session.get(CatalogContext, context_id).work_family_id is None
        assert _events(db_session, context_id) == []

"""Сверка `reconcile_semantic_jobs` и удержанные пачки события (задача 6
фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 6.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.7 (предикат, таблица исходов, инвариант), §2.8 (публикация), §2.11
(потолок события).

Один тест — одно строго отличающееся свойство: каждая строка таблицы
исходов §2.7 — отдельный вход (`docs/insights/claimed-property-needs-its-
own-input.md`). Помощники (`_proposal`, `_unit_id`, `_active_family`,
`_simple_context`) — ЛОКАЛЬНАЯ копия помощников `test_semantic_queue_
material.py`, не импорт (докстрока `test_context_routing.py`: наборы
помощников тестов проекта друг у друга не импортируют).

Гонка дедупликации (`TestConcurrentDedup`) — две настоящие сессии, и
чередование в них ПРИНУДИТЕЛЬНОЕ: обе сессии останавливаются барьером перед
`INSERT` пачки, то есть обе уже прочитали всё, что читают до вставки. Без
этого вариант «прочитать, затем вставить» почти всегда проходит зелёным —
одна сессия успевает закоммитить раньше, чем вторая читает, — и тест не
отличает атомарную вставку от гонки.

«Условность перехода по статусу» (`TestConditionalTransitions`) проверяется
в одной транзакции: обёртка чтения заданий сверки сразу после чтения меняет
строку сырым SQL в обход карты идентичности — так выглядит для сверки захват
или ручное действие, закоммиченное другой транзакцией между её чтением и её
`UPDATE` (спека §2.5, «Гонка захвата и отмены»).

Неизменность строк проверяется по `ctid`, а не по `updated_at`: `now()` в
одной транзакции постоянен, и `updated_at` после `UPDATE` в той же
транзакции равен прежнему."""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from config import settings
from models import (
    CatalogContext,
    FamilyParameterSchema,
    FamilySuggestion,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SuggestionUnpublishedReason,
)
from services.context_routing import route_position
from services.semantic_cost import EventCap
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    ReconcileReport,
    fingerprints_hash,
    get_or_create_held_batch,
    held_fingerprints,
    reconcile_semantic_jobs,
)
from services.semantic_request import load_request_material, render_context_request
from services.work_families import activate_family, assign_family, create_family

pytestmark = pytest.mark.integration

_JOIN_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
#  Помощники (локальная копия test_semantic_queue_material.py)
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _unit_id(db, code):
    from services.unit_resolution import UnitResolver

    return UnitResolver(db).resolve(code).unit_id


def _active_family(db, *, title, unit_name, actor_id, definition="Определение семьи"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id)
    family = activate_family(db, family_id=fam.id, actor_id=actor_id)
    # Семья уже со схемой: сцены этого файла проверяют задания предложений, а
    # активная семья без схемы получала бы ещё и задание схемы.
    db.add(
        FamilyParameterSchema(
            family_id=family.id, version=1, status="frozen", origin="model",
            frozen_at=dt.datetime.now(dt.UTC),
        )
    )
    db.flush()
    return family


def _simple_context(db, factories, proposal, *, unit_id, title) -> int:
    """Одна каталожная строка без раздела и без статьи — своя корзина, свой
    контекст по умолчанию, ровно одно членство."""
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    member = route_position(db, position_item_id=position.id)
    return member.context_id


def _rendered_for(db, context_id):
    """Текущий рендер контекста — той же парой функций, что зовёт сама
    сверка, чтобы тестовый job ровно совпал с «текущим отпечатком»."""
    material = load_request_material(db, [context_id])[context_id]
    return material, render_context_request(material, settings=settings)


def _make_job(
    db,
    *,
    context_id,
    request_hash,
    status,
    cancel_reason=None,
    claim_token=None,
    result_suggestion_id=None,
    unit_id=None,
    privacy_matches=None,
    retry_generation=0,
    attempts_in_generation=0,
    next_attempt_at=None,
    privacy_released_matches=None,
    last_error_class=None,
) -> SemanticJob:
    job = SemanticJob(
        context_id=context_id,
        request_hash=request_hash,
        status=status,
        cancel_reason=cancel_reason,
        claim_token=claim_token,
        result_suggestion_id=result_suggestion_id,
        unit_id=unit_id,
        privacy_matches=privacy_matches,
        privacy_released_matches=privacy_released_matches,
        last_error_class=last_error_class,
        retry_generation=retry_generation,
        attempts_in_generation=attempts_in_generation,
        next_attempt_at=next_attempt_at or dt.datetime.now(dt.UTC),
        prompt_version="1",
        model_requested="test-model",
        place_dictionary_version=1,
        candidates_hash="candidates-hash",
        prefix_hash="prefix-hash",
        input_hash="input-hash",
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _make_attempt(db, *, job_id) -> SemanticJobAttempt:
    attempt = SemanticJobAttempt(
        job_id=job_id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=dt.datetime.now(dt.UTC),
        reserve_usd=Decimal("0.01"),
        prefix_hash="prefix-hash",
        privacy_dictionary_hash="dict-hash",
    )
    db.add(attempt)
    db.flush()
    return attempt


def _make_suggestion(
    db,
    *,
    context_id,
    job_id,
    attempt_id,
    request_hash,
    is_published=False,
    unpublished_reason=None,
    decision=None,
    decided_by=None,
) -> FamilySuggestion:
    decided_at = dt.datetime.now(dt.UTC) if decision is not None else None
    suggestion = FamilySuggestion(
        context_id=context_id,
        job_id=job_id,
        attempt_id=attempt_id,
        request_hash=request_hash,
        candidates_hash="candidates-hash",
        candidates_snapshot=[],
        family_id=None,
        new_family_name="Новая семья",
        confidence=Decimal("0.9"),
        reason="проверочная причина",
        is_published=is_published,
        unpublished_reason=unpublished_reason,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at,
    )
    db.add(suggestion)
    db.flush()
    return suggestion


def _done_job_with_suggestion(db, *, context_id, request_hash, decision=None, is_published=False, decided_by=None):
    job = _make_job(db, context_id=context_id, request_hash=request_hash, status=SemanticJobStatus.done.value)
    attempt = _make_attempt(db, job_id=job.id)
    suggestion = _make_suggestion(
        db,
        context_id=context_id,
        job_id=job.id,
        attempt_id=attempt.id,
        request_hash=request_hash,
        is_published=is_published,
        unpublished_reason=None if is_published or decision is None else SuggestionUnpublishedReason.rejected.value,
        decision=decision,
        decided_by=decided_by,
    )
    job.result_suggestion_id = suggestion.id
    db.flush()
    return job, suggestion


_ZERO_REPORT = ReconcileReport(
    created=0, revived=0, cancelled=0, republished=0, unpublished=0, held_batch_id=None
)



def _fp(context_id, request_hash):
    """Отпечаток предложения — единственный вид в сценах этого файла."""
    return Fingerprint("family_suggestion", context_id, None, None, request_hash)


def _held_pairs(batch):
    # Пары берутся только у отпечатков предложений: элемент другого вида в
    # пачке сцены этого файла — уже ошибка, а не пара с пустыми полями.
    assert all(
        (e["kind"], e["family_id"], e["schema_id"]) == ("family_suggestion", None, None)
        for e in batch.held_fingerprints
    ), batch.held_fingerprints
    return [(e["context_id"], e["request_hash"]) for e in batch.held_fingerprints]


_SNAPSHOT_TABLES = ("semantic_jobs", "family_suggestions", "semantic_reconcile_batches")


def _ctid(db, table, row_id) -> str:
    """Физический адрес версии строки: любой `UPDATE` (даже тех же значений)
    даёт новую версию и новый `ctid`."""
    return db.execute(
        sa.text(f"SELECT ctid::text FROM {table} WHERE id = :id"), {"id": row_id}
    ).scalar_one()


def _table_snapshot(db) -> dict[str, list[dict]]:
    """Все строки трёх таблиц очереди вместе с `ctid` — снимок для сравнения
    «второй вызов не изменил ни одной строки»."""
    return {
        table: [
            dict(row)
            for row in db.execute(
                sa.text(f"SELECT ctid::text AS ctid, t.* FROM {table} t ORDER BY id")
            ).mappings().all()
        ]
        for table in _SNAPSHOT_TABLES
    }


def _after_jobs_read(monkeypatch, db, sql, params):
    """После чтения заданий сверкой выполняет `sql` сырым запросом — строка
    меняется в базе, а прочитанные сверкой объекты остаются прежними.
    Возвращает список вызовов: тест обязан убедиться, что сдвиг состоялся, —
    иначе сверка, переставшая звать эту функцию, прошла бы тест без гонки."""
    import services.semantic_reconcile as reconcile_module

    original = reconcile_module._load_jobs_for_contexts
    calls: list[int] = []

    def wrapper(session, context_ids):
        jobs = original(session, context_ids)
        session.connection().execute(sa.text(sql), params)
        calls.append(1)
        return jobs

    monkeypatch.setattr(reconcile_module, "_load_jobs_for_contexts", wrapper)
    return calls


def _make_observed_attempt(db, *, job_id, prefix_hash, cache_write_tokens) -> SemanticJobAttempt:
    """Попытка с наблюдением токенов префикса — `known_prefix_tokens` вернёт
    `cache_write_tokens` для этого `prefix_hash`."""
    attempt = SemanticJobAttempt(
        job_id=job_id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=dt.datetime.now(dt.UTC),
        reserve_usd=Decimal("0.01"),
        prefix_hash=prefix_hash,
        privacy_dictionary_hash="dict-hash",
        cache_write_tokens=cache_write_tokens,
    )
    db.add(attempt)
    db.flush()
    return attempt


# ---------------------------------------------------------------------------
#  Таблица исходов §2.7 — задание ТЕКУЩЕГО отпечатка
# ---------------------------------------------------------------------------

class TestCurrentFingerprintOutcomes:
    def _context(self, db, factories, user, *, title):
        proposal = _proposal(factories)
        unit_id = _unit_id(db, "M2")
        _active_family(db, title=f"Семья {title}", unit_name="M2", actor_id=user.id)
        return _simple_context(db, factories, proposal, unit_id=unit_id, title=title)

    def test_no_job_creates_pending(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Нет задания")
        material, rendered = _rendered_for(db_session, context_id)

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=1, revived=0, cancelled=0, republished=0, unpublished=0, held_batch_id=None
        )
        job = db_session.execute(
            sa.select(SemanticJob).where(SemanticJob.context_id == context_id)
        ).scalar_one()
        assert job.status == SemanticJobStatus.pending.value
        assert job.request_hash == rendered.request_hash
        assert job.unit_id == material.unit_id
        assert job.model_requested == settings.SEMANTIC_MODEL
        assert job.prompt_version == "1"
        assert job.candidates_hash == rendered.candidates_hash
        assert job.prefix_hash == rendered.prefix_hash
        assert job.input_hash == rendered.input_hash
        assert job.response_schema_version == "1"
        assert job.serialization_version == "1"
        assert job.place_dictionary_version == rendered.place_dictionary_version

    @pytest.mark.parametrize(
        "status,extra",
        [
            (SemanticJobStatus.pending.value, {}),
            (SemanticJobStatus.running.value, {"claim_token": uuid.uuid4()}),
            (SemanticJobStatus.privacy_hold.value, {"privacy_matches": []}),
        ],
    )
    def test_leave_unchanged_statuses(self, db_session, factories, status, extra):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title=f"Не трогать {status}")
        _material, rendered = _rendered_for(db_session, context_id)
        job = _make_job(
            db_session, context_id=context_id, request_hash=rendered.request_hash, status=status, **extra
        )
        before = (job.status, job.cancel_reason, job.claim_token, _ctid(db_session, "semantic_jobs", job.id))

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        db_session.refresh(job)
        after = (job.status, job.cancel_reason, job.claim_token, _ctid(db_session, "semantic_jobs", job.id))
        assert after == before

    def test_done_unresolved_unpublished_is_republished_without_model_call(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Republish")
        _material, rendered = _rendered_for(db_session, context_id)
        _job, suggestion = _done_job_with_suggestion(
            db_session, context_id=context_id, request_hash=rendered.request_hash, decision=None, is_published=False
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=0, revived=0, cancelled=0, republished=1, unpublished=0, held_batch_id=None
        )
        db_session.refresh(suggestion)
        assert suggestion.is_published is True
        assert suggestion.unpublished_reason is None

    def test_done_rejected_is_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Rejected")
        _material, rendered = _rendered_for(db_session, context_id)
        _job, suggestion = _done_job_with_suggestion(
            db_session,
            context_id=context_id,
            request_hash=rendered.request_hash,
            decision="rejected",
            is_published=False,
            decided_by=user.id,
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        db_session.refresh(suggestion)
        assert suggestion.is_published is False
        assert suggestion.decision == "rejected"

    def test_done_without_suggestion_is_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Done no suggestion")
        _material, rendered = _rendered_for(db_session, context_id)
        job = _make_job(
            db_session, context_id=context_id, request_hash=rendered.request_hash,
            status=SemanticJobStatus.done.value,
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        db_session.refresh(job)
        assert job.result_suggestion_id is None

    @pytest.mark.parametrize(
        "cancel_reason",
        [SemanticCancelReason.input_changed.value, SemanticCancelReason.not_applicable.value,
         SemanticCancelReason.stale_hold.value],
    )
    def test_revivable_cancelled_returns_to_pending(self, db_session, factories, cancel_reason):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title=f"Revive {cancel_reason}")
        _material, rendered = _rendered_for(db_session, context_id)
        past = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        job = _make_job(
            db_session, context_id=context_id, request_hash=rendered.request_hash,
            status=SemanticJobStatus.cancelled.value, cancel_reason=cancel_reason,
            retry_generation=2, attempts_in_generation=3, next_attempt_at=past,
            privacy_released_matches=["some", "match"], last_error_class="SomeError",
        )
        job_id = job.id

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=0, revived=1, cancelled=0, republished=0, unpublished=0, held_batch_id=None
        )
        db_session.expire_all()
        revived = db_session.get(SemanticJob, job_id)
        assert revived.status == SemanticJobStatus.pending.value
        assert revived.cancel_reason is None
        assert revived.retry_generation == 3
        assert revived.attempts_in_generation == 0
        assert revived.next_attempt_at > past
        assert revived.privacy_released_matches is None
        assert revived.last_error_class is None

    def test_privacy_declined_is_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Privacy declined")
        _material, rendered = _rendered_for(db_session, context_id)
        job = _make_job(
            db_session, context_id=context_id, request_hash=rendered.request_hash,
            status=SemanticJobStatus.cancelled.value,
            cancel_reason=SemanticCancelReason.privacy_declined.value,
        )
        job_id = job.id

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        db_session.expire_all()
        unchanged = db_session.get(SemanticJob, job_id)
        assert unchanged.status == SemanticJobStatus.cancelled.value
        assert unchanged.cancel_reason == SemanticCancelReason.privacy_declined.value

    def test_error_is_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Error")
        _material, rendered = _rendered_for(db_session, context_id)
        job = _make_job(
            db_session, context_id=context_id, request_hash=rendered.request_hash,
            status=SemanticJobStatus.error.value,
        )
        job_id = job.id

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        db_session.expire_all()
        unchanged = db_session.get(SemanticJob, job_id)
        assert unchanged.status == SemanticJobStatus.error.value


# ---------------------------------------------------------------------------
#  Применимый контекст, задание СТАРОГО отпечатка (спека §2.7)
# ---------------------------------------------------------------------------

class TestStaleFingerprintTransitions:
    def _context(self, db, factories, user, *, title):
        proposal = _proposal(factories)
        unit_id = _unit_id(db, "M2")
        _active_family(db, title=f"Семья {title}", unit_name="M2", actor_id=user.id)
        return _simple_context(db, factories, proposal, unit_id=unit_id, title=title)

    def test_old_pending_becomes_input_changed_and_new_job_is_created(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Old pending")
        old_job = _make_job(
            db_session, context_id=context_id, request_hash="old-hash-pending",
            status=SemanticJobStatus.pending.value,
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=1, revived=0, cancelled=1, republished=0, unpublished=0, held_batch_id=None
        )
        db_session.expire_all()
        old = db_session.get(SemanticJob, old_job.id)
        assert old.status == SemanticJobStatus.cancelled.value
        assert old.cancel_reason == SemanticCancelReason.input_changed.value
        current = db_session.execute(
            sa.select(SemanticJob).where(
                SemanticJob.context_id == context_id, SemanticJob.id != old_job.id
            )
        ).scalar_one()
        assert current.status == SemanticJobStatus.pending.value

    def test_old_privacy_hold_becomes_stale_hold_and_new_job_is_created(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._context(db_session, factories, user, title="Old privacy hold")
        old_job = _make_job(
            db_session, context_id=context_id, request_hash="old-hash-privacy",
            status=SemanticJobStatus.privacy_hold.value, privacy_matches=[],
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=1, revived=0, cancelled=1, republished=0, unpublished=0, held_batch_id=None
        )
        db_session.expire_all()
        old = db_session.get(SemanticJob, old_job.id)
        assert old.status == SemanticJobStatus.cancelled.value
        assert old.cancel_reason == SemanticCancelReason.stale_hold.value


# ---------------------------------------------------------------------------
#  Неприменимый контекст (спека §2.7)
# ---------------------------------------------------------------------------

def _mark_not_applicable(db, context_id):
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(semantic_state="NOT_APPLICABLE")
    )
    db.expire_all()


class TestInapplicableContext:
    def _inapplicable_context(self, db, factories, user, *, title):
        proposal = _proposal(factories)
        unit_id = _unit_id(db, "M2")
        family = _active_family(db, title=f"Семья {title}", unit_name="M2", actor_id=user.id)
        context_id = _simple_context(db, factories, proposal, unit_id=unit_id, title=title)
        # Контекст неприменим: `NOT_APPLICABLE` (привязка семьи применимость не
        # отменяет, спека вариантов §2.5).
        _mark_not_applicable(db, context_id)
        assert family.id is not None
        return context_id

    @pytest.mark.parametrize(
        "status,extra",
        [
            (SemanticJobStatus.pending.value, {}),
            (SemanticJobStatus.privacy_hold.value, {"privacy_matches": []}),
        ],
    )
    def test_open_job_is_cancelled_not_applicable(self, db_session, factories, status, extra):
        user = factories.UserFactory.create()
        context_id = self._inapplicable_context(db_session, factories, user, title=f"Inapplicable {status}")
        job = _make_job(
            db_session, context_id=context_id, request_hash="any-hash", status=status, **extra
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=0, revived=0, cancelled=1, republished=0, unpublished=0, held_batch_id=None
        )
        db_session.expire_all()
        cancelled = db_session.get(SemanticJob, job.id)
        assert cancelled.status == SemanticJobStatus.cancelled.value
        assert cancelled.cancel_reason == SemanticCancelReason.not_applicable.value

    def test_published_suggestion_is_unpublished(self, db_session, factories):
        user = factories.UserFactory.create()
        context_id = self._inapplicable_context(db_session, factories, user, title="Inapplicable published")
        _job, suggestion = _done_job_with_suggestion(
            db_session, context_id=context_id, request_hash="any-hash", decision=None, is_published=True
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=0, revived=0, cancelled=0, republished=0, unpublished=1, held_batch_id=None
        )
        db_session.refresh(suggestion)
        assert suggestion.is_published is False
        assert suggestion.unpublished_reason == SuggestionUnpublishedReason.context_not_applicable.value


# ---------------------------------------------------------------------------
#  Потолок события (спека §2.11)
# ---------------------------------------------------------------------------

class TestEventCap:
    def _two_contexts(self, db, factories, user):
        proposal = _proposal(factories)
        unit_id = _unit_id(db, "M2")
        _active_family(db, title="Семья потолка", unit_name="M2", actor_id=user.id)
        a = _simple_context(db, factories, proposal, unit_id=unit_id, title="Потолок A")
        b = _simple_context(db, factories, proposal, unit_id=unit_id, title="Потолок B")
        return a, b

    def test_over_contexts_cap_holds_batch_instead_of_creating(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=1, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")

        assert report.created == 0
        assert report.revived == 0
        assert report.held_batch_id is not None
        jobs = db_session.execute(sa.select(SemanticJob)).all()
        assert jobs == []
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert batch.status == "held"
        assert batch.contexts_count == 2
        assert batch.unit_id is None
        assert batch.source == "mass"
        assert [Fingerprint.from_dict(e) for e in batch.held_fingerprints] == held_fingerprints(
            db_session, [a, b]
        )

    def test_exactly_at_contexts_cap_creates_jobs(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")

        assert report.created == 2
        assert report.held_batch_id is None

    def test_over_reserve_cap_holds_batch(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=1000, max_reserve_usd=Decimal("0"))

        report = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")

        assert report.created == 0
        assert report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert batch.reserve_estimate_usd > 0

    def test_no_cap_creates_jobs_regardless_of_size(self, db_session, factories, monkeypatch):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        # Действующий потолок настроек ниже набора по обоим условиям: `NO_CAP`
        # обязан не подменяться им.
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_RESERVE_USD", Decimal("0"))

        report = reconcile_semantic_jobs(db_session, [a, b], cap=NO_CAP, source="mass")

        assert report.created == 2
        assert report.held_batch_id is None


class TestHeldBatchDeduplication:
    def _two_contexts(self, db, factories, user):
        proposal = _proposal(factories)
        unit_id = _unit_id(db, "M2")
        _active_family(db, title="Семья дедупа", unit_name="M2", actor_id=user.id)
        a = _simple_context(db, factories, proposal, unit_id=unit_id, title="Дедуп A")
        b = _simple_context(db, factories, proposal, unit_id=unit_id, title="Дедуп B")
        return a, b

    def test_repeat_call_over_cap_reuses_same_batch(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        first = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")
        second = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")

        assert first.held_batch_id is not None
        assert second.held_batch_id == first.held_batch_id
        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch).where(
                SemanticReconcileBatch.status == "held"
            )
        ).scalar_one()
        assert count == 1
        assert second.created == 0 and second.revived == 0

    def test_different_set_creates_a_different_batch(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        first = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")
        second = reconcile_semantic_jobs(db_session, [a], cap=cap, source="mass")

        assert first.held_batch_id != second.held_batch_id
        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch).where(
                SemanticReconcileBatch.status == "held"
            )
        ).scalar_one()
        assert count == 2

    def test_approved_batch_with_same_hash_does_not_block_a_new_held_batch(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        fingerprints = held_fingerprints(db_session, [a, b])
        first_id = get_or_create_held_batch(
            db_session, fingerprints=fingerprints, source="mass", import_job_id=None, unit_id=None,
            reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
        )
        db_session.execute(
            sa.update(SemanticReconcileBatch)
            .where(SemanticReconcileBatch.id == first_id)
            .values(status="approved", decided_by=user.id, decided_at=dt.datetime.now(dt.UTC))
        )

        second_id = get_or_create_held_batch(
            db_session, fingerprints=fingerprints, source="mass", import_job_id=None, unit_id=None,
            reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
        )

        assert second_id != first_id
        second = db_session.get(SemanticReconcileBatch, second_id)
        assert second.status == "held"

    def test_fingerprints_hash_is_independent_of_input_order(self):
        pairs = [_fp(3, "c"), _fp(1, "a"), _fp(2, "b")]
        reversed_pairs = list(reversed(pairs))

        assert fingerprints_hash(pairs) == fingerprints_hash(reversed_pairs)

    def test_held_fingerprints_matches_what_reconcile_would_hold(self, db_session, factories):
        user = factories.UserFactory.create()
        a, b = self._two_contexts(db_session, factories, user)
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        preview = held_fingerprints(db_session, [a, b])
        report = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="mass")

        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert [Fingerprint.from_dict(e) for e in batch.held_fingerprints] == preview


# ---------------------------------------------------------------------------
#  Пакетность и идемпотентность (план, «Утверждения»)
# ---------------------------------------------------------------------------

class TestBatchingAndIdempotence:
    @pytest.mark.parametrize("over_cap", [False, True])
    def test_query_count_does_not_grow_with_context_count(self, db_session, factories, over_cap):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        _active_family(db_session, title="Семья пакетности", unit_name="M2", actor_id=user.id)
        small_ids = [
            _simple_context(db_session, factories, proposal, unit_id=unit_id, title=f"Пакет {i}")
            for i in range(10)
        ]
        large_ids = [
            _simple_context(db_session, factories, proposal, unit_id=unit_id, title=f"Пакет Б{i}")
            for i in range(60)
        ]
        # Сверх потолка — путь удержанной пачки: резерв и цена с кэшем по E.
        cap = EventCap(max_contexts=0 if over_cap else 1000, max_reserve_usd=Decimal("1000000"))

        statements: list[list[str]] = []
        for ids in (small_ids, large_ids):
            captured: list[str] = []

            def _listener(conn, cursor, statement, parameters, context, executemany, _bucket=captured):
                _bucket.append(statement)

            connection = db_session.connection()
            sa.event.listen(connection, "before_cursor_execute", _listener)
            try:
                report = reconcile_semantic_jobs(db_session, ids, cap=cap, source="mass")
            finally:
                sa.event.remove(connection, "before_cursor_execute", _listener)
            if over_cap:
                assert report.created == 0 and report.held_batch_id is not None
            else:
                assert report.created == len(ids)
            statements.append(captured)

        assert len(statements[0]) == len(statements[1])
        insert_statements = [s for s in statements[1] if s.strip().upper().startswith("INSERT INTO SEMANTIC_JOBS")]
        assert len(insert_statements) == (0 if over_cap else 1)

    @pytest.mark.parametrize("over_cap", [False, True])
    def test_prefix_tokens_queried_once_per_distinct_prefix_hash(self, db_session, factories, over_cap):
        """Токены префикса — один запрос на РАЗЛИЧНЫЙ `prefix_hash` набора: и
        для резерва (потолок), и для цены с кэшем (пачка) — общий, а не по
        запросу на каждую сумму и не на контекст."""
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        m2 = _unit_id(db_session, "M2")
        pcs = _unit_id(db_session, "PCS")
        _active_family(db_session, title="Семья префикса M2", unit_name="M2", actor_id=user.id)
        _active_family(db_session, title="Семья префикса PCS", unit_name="PCS", actor_id=user.id)
        ids = [_simple_context(db_session, factories, proposal, unit_id=m2, title=f"Префикс M2 {i}") for i in range(3)]
        ids.append(_simple_context(db_session, factories, proposal, unit_id=pcs, title="Префикс PCS"))
        prefixes = {_rendered_for(db_session, cid)[1].prefix_hash for cid in ids}
        assert len(prefixes) == 2, "вход обязан нести два различных префикса"
        cap = EventCap(max_contexts=0 if over_cap else 1000, max_reserve_usd=Decimal("1000000"))
        captured: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            report = reconcile_semantic_jobs(db_session, ids, cap=cap, source="operation")
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert (report.held_batch_id is not None) is over_cap
        prefix_queries = [s for s in captured if "semantic_job_attempts" in s]
        assert len(prefix_queries) == len(prefixes)

    def test_second_call_on_unchanged_data_under_cap_changes_nothing(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        _active_family(db_session, title="Семья идемпотентности", unit_name="M2", actor_id=user.id)
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Идемпотентность")

        first = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")
        second = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert first.created == 1
        assert second == _ZERO_REPORT
        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticJob).where(SemanticJob.context_id == context_id)
        ).scalar_one()
        assert count == 1


# ---------------------------------------------------------------------------
#  Валидация входа (до записи)
# ---------------------------------------------------------------------------

class TestValidation:
    def test_unknown_source_raises_value_error(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        _active_family(db_session, title="Семья валидации", unit_name="M2", actor_id=user.id)
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Валидация источника")

        with pytest.raises(ValueError):
            reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="not-a-real-source")

        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticJob).where(SemanticJob.context_id == context_id)
        ).scalar_one()
        assert count == 0

    def test_unknown_cap_type_raises_type_error(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        _active_family(db_session, title="Семья типа потолка", unit_name="M2", actor_id=user.id)
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Валидация потолка")

        with pytest.raises(TypeError):
            reconcile_semantic_jobs(db_session, [context_id], cap="not-a-cap", source="operation")

    def test_empty_context_ids_returns_zero_report_without_queries(self, db_session):
        captured: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            report = reconcile_semantic_jobs(db_session, [], cap=NO_CAP, source="operation")
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert report == _ZERO_REPORT
        assert captured == []


# ---------------------------------------------------------------------------
#  Гонка дедупликации (две сессии, жёсткий таймаут)
# ---------------------------------------------------------------------------

class TestConcurrentDedup:
    def test_two_concurrent_reconciles_over_cap_get_one_batch(self, committing_session_factory, committing_factories, committing_db):
        user = committing_factories.UserFactory.create()
        proposal = _proposal(committing_factories)
        unit_id = _unit_id(committing_db, "M2")
        _active_family(committing_db, title="Семья гонки", unit_name="M2", actor_id=user.id)
        context_id = _simple_context(committing_db, committing_factories, proposal, unit_id=unit_id, title="Гонка")
        committing_db.commit()

        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))
        start_barrier = threading.Barrier(2)
        # Обе сессии доходят до вставки пачки прежде, чем любая её выполнит:
        # всё, что сверка читает до вставки, прочитано обеими.
        insert_barrier = threading.Barrier(2)
        results: dict[str, int | None] = {}
        errors: dict[str, BaseException] = {}
        held_at_insert: list[int] = []

        def _hold_before_batch_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT INTO SEMANTIC_RECONCILE_BATCHES"):
                held_at_insert.append(1)
                insert_barrier.wait(timeout=10)

        def worker(key: str) -> None:
            db = committing_session_factory()
            try:
                sa.event.listen(db.connection(), "before_cursor_execute", _hold_before_batch_insert)
                start_barrier.wait(timeout=10)
                report = reconcile_semantic_jobs(db, [context_id], cap=cap, source="operation")
                db.commit()
                results[key] = report.held_batch_id
            except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
                db.rollback()
                errors[key] = exc
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(key,)) for key in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_JOIN_TIMEOUT)

        assert not any(t.is_alive() for t in threads), "поток гонки завис за отведённый таймаут"
        assert not errors, errors
        # Барьер перед вставкой прошли обе сессии — чередование состоялось.
        assert len(held_at_insert) == 2
        assert results["a"] is not None
        assert results["a"] == results["b"]

        count = committing_db.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch).where(
                SemanticReconcileBatch.status == "held"
            )
        ).scalar_one()
        assert count == 1


# ---------------------------------------------------------------------------
#  Помощник сценариев с несколькими контекстами одной единицы
# ---------------------------------------------------------------------------

def _unit_contexts(db, factories, user, *, family_title, titles):
    proposal = _proposal(factories)
    unit_id = _unit_id(db, "M2")
    family = _active_family(db, title=family_title, unit_name="M2", actor_id=user.id)
    ids = [_simple_context(db, factories, proposal, unit_id=unit_id, title=t) for t in titles]
    return family, ids


def _jobs_of(db, context_id):
    db.expire_all()
    return db.execute(
        sa.select(SemanticJob).where(SemanticJob.context_id == context_id).order_by(SemanticJob.id)
    ).scalars().all()


# ---------------------------------------------------------------------------
#  Ветви, которые таблица исходов оставляет нетронутыми
# ---------------------------------------------------------------------------

class TestUntouchedBranches:
    def test_old_fingerprint_jobs_in_other_statuses_are_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья старых", titles=["Старые прочие"]
        )
        running = _make_job(db_session, context_id=context_id, request_hash="old-running",
                            status=SemanticJobStatus.running.value, claim_token=uuid.uuid4())
        done = _make_job(db_session, context_id=context_id, request_hash="old-done",
                         status=SemanticJobStatus.done.value)
        error = _make_job(db_session, context_id=context_id, request_hash="old-error",
                          status=SemanticJobStatus.error.value)
        declined = _make_job(db_session, context_id=context_id, request_hash="old-declined",
                             status=SemanticJobStatus.cancelled.value,
                             cancel_reason=SemanticCancelReason.privacy_declined.value)
        watched = [running, done, error, declined]
        before = {job.id: _ctid(db_session, "semantic_jobs", job.id) for job in watched}

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=1, revived=0, cancelled=0, republished=0, unpublished=0, held_batch_id=None
        )
        assert {job.id: _ctid(db_session, "semantic_jobs", job.id) for job in watched} == before

    def test_inapplicable_context_leaves_running_done_error_and_unpublished_reason(self, db_session, factories):
        user = factories.UserFactory.create()
        family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья неприменимых прочих", titles=["Неприменимый прочий"]
        )
        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        running = _make_job(db_session, context_id=context_id, request_hash="h-running",
                            status=SemanticJobStatus.running.value, claim_token=uuid.uuid4())
        error = _make_job(db_session, context_id=context_id, request_hash="h-error",
                          status=SemanticJobStatus.error.value)
        done = _make_job(db_session, context_id=context_id, request_hash="h-done",
                         status=SemanticJobStatus.done.value)
        attempt = _make_attempt(db_session, job_id=done.id)
        lost = _make_suggestion(
            db_session, context_id=context_id, job_id=done.id, attempt_id=attempt.id, request_hash="h-done",
            is_published=False, unpublished_reason=SuggestionUnpublishedReason.lost_claim.value,
        )
        before_jobs = {job.id: _ctid(db_session, "semantic_jobs", job.id) for job in (running, error, done)}
        before_suggestion = _ctid(db_session, "family_suggestions", lost.id)

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        assert {j: _ctid(db_session, "semantic_jobs", j) for j in before_jobs} == before_jobs
        assert _ctid(db_session, "family_suggestions", lost.id) == before_suggestion
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, lost.id).unpublished_reason == SuggestionUnpublishedReason.lost_claim.value

    def test_done_current_already_published_is_left_unchanged(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья опубликованного", titles=["Уже опубликовано"]
        )
        _material, rendered = _rendered_for(db_session, context_id)
        _job, suggestion = _done_job_with_suggestion(
            db_session, context_id=context_id, request_hash=rendered.request_hash, is_published=True
        )
        before = _ctid(db_session, "family_suggestions", suggestion.id)

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == _ZERO_REPORT
        assert _ctid(db_session, "family_suggestions", suggestion.id) == before


# ---------------------------------------------------------------------------
#  Повторная публикация (спека §2.8: одно опубликованное на контекст)
# ---------------------------------------------------------------------------

class TestRepublish:
    def test_other_published_suggestion_loses_publication_first(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья вытеснения", titles=["Вытеснение"]
        )
        _material, rendered = _rendered_for(db_session, context_id)
        _old_job, old_suggestion = _done_job_with_suggestion(
            db_session, context_id=context_id, request_hash="old-hash", is_published=True
        )
        current_job = _make_job(db_session, context_id=context_id, request_hash=rendered.request_hash,
                                status=SemanticJobStatus.done.value)
        attempt = _make_attempt(db_session, job_id=current_job.id)
        current_suggestion = _make_suggestion(
            db_session, context_id=context_id, job_id=current_job.id, attempt_id=attempt.id,
            request_hash=rendered.request_hash, is_published=False,
            unpublished_reason=SuggestionUnpublishedReason.context_not_applicable.value,
        )
        current_job.result_suggestion_id = current_suggestion.id
        db_session.flush()

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report == ReconcileReport(
            created=0, revived=0, cancelled=0, republished=1, unpublished=1, held_batch_id=None
        )
        db_session.expire_all()
        old = db_session.get(FamilySuggestion, old_suggestion.id)
        current = db_session.get(FamilySuggestion, current_suggestion.id)
        assert (old.is_published, old.unpublished_reason) == (
            False, SuggestionUnpublishedReason.stale_fingerprint.value
        )
        assert (current.is_published, current.unpublished_reason) == (True, None)

    def test_old_fingerprint_done_suggestion_is_not_republished(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья старого ответа", titles=["Старый ответ"]
        )
        _job, suggestion = _done_job_with_suggestion(
            db_session, context_id=context_id, request_hash="old-hash", is_published=False
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert report.republished == 0
        assert report.created == 1
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, suggestion.id).is_published is False


# ---------------------------------------------------------------------------
#  Условность переходов по статусу (спека §2.5, «Гонка захвата и отмены»)
# ---------------------------------------------------------------------------

class TestConditionalTransitions:
    @pytest.mark.parametrize("scenario", ["inapplicable_pending", "old_pending", "old_privacy_hold"])
    def test_cancel_skips_row_changed_after_read(self, db_session, factories, monkeypatch, scenario):
        user = factories.UserFactory.create()
        family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title=f"Семья гонки {scenario}", titles=[f"Гонка {scenario}"]
        )
        if scenario == "inapplicable_pending":
            assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)
        if scenario == "old_privacy_hold":
            job = _make_job(db_session, context_id=context_id, request_hash="old-hash",
                            status=SemanticJobStatus.privacy_hold.value, privacy_matches=[])
            # «Отправить» (спека §2.10) закоммичено другой транзакцией.
            sql = ("UPDATE semantic_jobs SET status = 'pending', privacy_released_matches = '[]'::jsonb "
                   "WHERE id = :id")
            expected_status = SemanticJobStatus.pending.value
        else:
            job = _make_job(db_session, context_id=context_id, request_hash="old-hash",
                            status=SemanticJobStatus.pending.value)
            # Захват закоммичен другой транзакцией.
            sql = "UPDATE semantic_jobs SET status = 'running', claim_token = gen_random_uuid() WHERE id = :id"
            expected_status = SemanticJobStatus.running.value
        shifted = _after_jobs_read(monkeypatch, db_session, sql, {"id": job.id})

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert shifted == [1]
        assert report.cancelled == 0
        row = db_session.execute(
            sa.text("SELECT status, cancel_reason FROM semantic_jobs WHERE id = :id"), {"id": job.id}
        ).one()
        assert (row.status, row.cancel_reason) == (expected_status, None)

    def test_revive_skips_row_revived_after_read(self, db_session, factories, monkeypatch):
        user = factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            db_session, factories, user, family_title="Семья двойного возврата", titles=["Двойной возврат"]
        )
        _material, rendered = _rendered_for(db_session, context_id)
        job = _make_job(db_session, context_id=context_id, request_hash=rendered.request_hash,
                        status=SemanticJobStatus.cancelled.value,
                        cancel_reason=SemanticCancelReason.input_changed.value, retry_generation=2)
        # Другая сверка того же контекста уже вернула задание и закоммитила.
        shifted = _after_jobs_read(
            monkeypatch, db_session,
            "UPDATE semantic_jobs SET status = 'pending', cancel_reason = NULL, "
            "retry_generation = retry_generation + 1 WHERE id = :id",
            {"id": job.id},
        )

        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert shifted == [1]
        assert report.revived == 0
        row = db_session.execute(
            sa.text("SELECT status, retry_generation FROM semantic_jobs WHERE id = :id"), {"id": job.id}
        ).one()
        assert (row.status, row.retry_generation) == (SemanticJobStatus.pending.value, 3)

    def test_insert_skips_job_inserted_after_read(self, db_session, factories, monkeypatch):
        user = factories.UserFactory.create()
        _family, (a, b) = _unit_contexts(
            db_session, factories, user, family_title="Семья двойной вставки", titles=["Вставка A", "Вставка B"]
        )
        _material, rendered_a = _rendered_for(db_session, a)
        # Другая сверка уже вставила задание контекста `a` и закоммитила.
        shifted = _after_jobs_read(
            monkeypatch, db_session,
            "INSERT INTO semantic_jobs (kind, context_id, request_hash, status, prompt_version, "
            "model_requested, place_dictionary_version, candidates_hash, prefix_hash, input_hash, "
            "response_schema_version, serialization_version) "
            "VALUES ('family_suggestion', :cid, :h, 'pending', '1', 'm', 1, 'c', 'p', 'i', '1', '1')",
            {"cid": a, "h": rendered_a.request_hash},
        )

        report = reconcile_semantic_jobs(db_session, [a, b], cap=NO_CAP, source="operation")

        assert shifted == [1]
        assert report.created == 1
        assert len(_jobs_of(db_session, a)) == 1
        assert len(_jobs_of(db_session, b)) == 1


# ---------------------------------------------------------------------------
#  Потолок считает только набор постановки E (контексты и резерв)
# ---------------------------------------------------------------------------

class TestCapCountsOnlyPostanovka:
    _OBSERVED_PREFIX_TOKENS = 100_000

    def _scenario(self, db, factories, user):
        """`a`, `b` — без заданий (набор E); `c` — задание текущего отпечатка
        `pending` (вне E). У префикса единицы есть наблюдение токенов — резерв
        считается по нему, а не по байтам."""
        _family, (a, b, c) = _unit_contexts(
            db, factories, user, family_title="Семья набора E", titles=["E-A", "E-B", "E-C"]
        )
        renders = {cid: _rendered_for(db, cid)[1] for cid in (a, b, c)}
        c_job = _make_job(db, context_id=c, request_hash=renders[c].request_hash,
                          status=SemanticJobStatus.pending.value)
        _make_observed_attempt(db, job_id=c_job.id, prefix_hash=renders[c].prefix_hash,
                               cache_write_tokens=self._OBSERVED_PREFIX_TOKENS)
        return (a, b, c), renders

    @staticmethod
    def _expected(db, renders, ids):
        """Эталон — функции задачи 5 по одному контексту (каждая сама читает
        наблюдение префикса), а не агрегат сверки."""
        from services.semantic_cost import expected_cached_cost, reserve_for, tariffs_from

        tariffs = tariffs_from(settings)
        reserve = sum(
            (reserve_for(db, renders[cid], tariffs, settings.SEMANTIC_MAX_TOKENS) for cid in ids), Decimal("0")
        )
        cached = sum((expected_cached_cost(db, renders[cid], tariffs) for cid in ids), Decimal("0"))
        return reserve, cached

    def test_contexts_cap_equal_to_postanovka_size_creates_jobs(self, db_session, factories):
        user = factories.UserFactory.create()
        (a, b, c), _renders = self._scenario(db_session, factories, user)
        cap = EventCap(max_contexts=2, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(db_session, [a, b, c], cap=cap, source="operation")

        assert (report.created, report.held_batch_id) == (2, None)

    def test_reserve_cap_equal_to_postanovka_reserve_creates_jobs(self, db_session, factories):
        user = factories.UserFactory.create()
        (a, b, c), renders = self._scenario(db_session, factories, user)
        reserve, _cached = self._expected(db_session, renders, (a, b))
        cap = EventCap(max_contexts=1000, max_reserve_usd=reserve)

        report = reconcile_semantic_jobs(db_session, [a, b, c], cap=cap, source="operation")

        assert (report.created, report.held_batch_id) == (2, None)

    def test_reserve_one_cent_over_cap_holds_batch_with_estimates(self, db_session, factories):
        user = factories.UserFactory.create()
        (a, b, c), renders = self._scenario(db_session, factories, user)
        reserve, cached = self._expected(db_session, renders, (a, b))
        assert cached < reserve - Decimal("0.01"), "вход обязан различать резерв и цену с кэшем"
        cap = EventCap(max_contexts=1000, max_reserve_usd=reserve - Decimal("0.01"))

        report = reconcile_semantic_jobs(db_session, [a, b, c], cap=cap, source="operation")

        assert report.created == 0 and report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert batch.contexts_count == 2
        assert _held_pairs(batch) == [
            (a, renders[a].request_hash), (b, renders[b].request_hash)
        ]
        assert batch.reserve_estimate_usd == reserve
        assert batch.cached_estimate_usd == cached

    def test_unknown_prefix_estimates_use_prefix_bytes(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (a, b) = _unit_contexts(
            db_session, factories, user, family_title="Семья без наблюдений", titles=["Без A", "Без B"]
        )
        renders = {cid: _rendered_for(db_session, cid)[1] for cid in (a, b)}
        reserve, cached = self._expected(db_session, renders, (a, b))
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(db_session, [a, b], cap=cap, source="operation")

        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert (batch.reserve_estimate_usd, batch.cached_estimate_usd) == (reserve, cached)


# ---------------------------------------------------------------------------
#  Сверх потолка: удерживается только постановка, остальное выполняется
# ---------------------------------------------------------------------------

class TestOverCapSideEffects:
    def test_cancellations_and_republish_happen_over_cap(self, db_session, factories):
        user = factories.UserFactory.create()
        family, (a, b, c, d, e) = _unit_contexts(
            db_session, factories, user, family_title="Семья сверх потолка",
            titles=["Сверх A", "Сверх B", "Сверх C", "Сверх D", "Сверх E"],
        )
        _mark_not_applicable(db_session, e)
        renders = {cid: _rendered_for(db_session, cid)[1] for cid in (a, b, c, d)}
        revivable = _make_job(db_session, context_id=b, request_hash=renders[b].request_hash,
                              status=SemanticJobStatus.cancelled.value,
                              cancel_reason=SemanticCancelReason.input_changed.value)
        old_pending = _make_job(db_session, context_id=c, request_hash="old-hash",
                                status=SemanticJobStatus.pending.value)
        _done, suggestion = _done_job_with_suggestion(
            db_session, context_id=d, request_hash=renders[d].request_hash, is_published=False
        )
        inapplicable_pending = _make_job(db_session, context_id=e, request_hash="any-hash",
                                         status=SemanticJobStatus.pending.value)
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(db_session, [a, b, c, d, e], cap=cap, source="operation")

        assert (report.created, report.revived, report.cancelled, report.republished, report.unpublished) == (
            0, 0, 2, 1, 0
        )
        assert report.held_batch_id is not None
        db_session.expire_all()
        still_cancelled = db_session.get(SemanticJob, revivable.id)
        assert (still_cancelled.status, still_cancelled.cancel_reason, still_cancelled.retry_generation) == (
            SemanticJobStatus.cancelled.value, SemanticCancelReason.input_changed.value, 0
        )
        assert db_session.get(SemanticJob, old_pending.id).cancel_reason == SemanticCancelReason.input_changed.value
        assert db_session.get(SemanticJob, inapplicable_pending.id).cancel_reason == (
            SemanticCancelReason.not_applicable.value
        )
        assert db_session.get(FamilySuggestion, suggestion.id).is_published is True
        assert _jobs_of(db_session, a) == []
        assert [j.id for j in _jobs_of(db_session, c)] == [old_pending.id]
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert _held_pairs(batch) == sorted(
            [(a, renders[a].request_hash), (b, renders[b].request_hash), (c, renders[c].request_hash)]
        )
        assert batch.contexts_count == 3

    def test_import_job_id_is_recorded_on_held_batch(self, db_session, factories):
        user = factories.UserFactory.create()
        _family, (a,) = _unit_contexts(
            db_session, factories, user, family_title="Семья импорта", titles=["Импорт A"]
        )
        import_job = factories.ImportJobFactory.create()
        cap = EventCap(max_contexts=0, max_reserve_usd=Decimal("1000000"))

        report = reconcile_semantic_jobs(
            db_session, [a], cap=cap, source="import", import_job_id=import_job.id
        )

        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert (batch.source, batch.import_job_id) == ("import", import_job.id)


# ---------------------------------------------------------------------------
#  Пачка: канонический вид, хэш, соседство решённых пачек
# ---------------------------------------------------------------------------

class TestHeldBatchShape:
    def test_get_or_create_rejects_unknown_source_before_write(self, db_session):
        with pytest.raises(ValueError):
            get_or_create_held_batch(
                db_session, fingerprints=[_fp(1, "a")], source="not-a-real-source", import_job_id=None,
                unit_id=None, reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
            )
        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticReconcileBatch)
        ).scalar_one()
        assert count == 0

    def test_stored_fingerprints_are_canonically_sorted(self, db_session):
        batch_id = get_or_create_held_batch(
            db_session, fingerprints=[_fp(2, "b"), _fp(1, "z"), _fp(1, "a")], source="mass", import_job_id=None,
            unit_id=None, reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
        )

        batch = db_session.get(SemanticReconcileBatch, batch_id)
        assert _held_pairs(batch) == [(1, "a"), (1, "z"), (2, "b")]
        assert batch.fingerprints_hash == fingerprints_hash([_fp(1, "a"), _fp(1, "z"), _fp(2, "b")])

    def test_fingerprints_hash_depends_on_request_hash_and_context(self):
        base = fingerprints_hash([_fp(1, "a"), _fp(2, "b")])

        assert fingerprints_hash([_fp(1, "a"), _fp(2, "c")]) != base
        assert fingerprints_hash([_fp(1, "a"), _fp(3, "b")]) != base

    def test_held_fingerprints_sorted_regardless_of_load_order(self, db_session, factories, monkeypatch):
        import services.semantic_reconcile as reconcile_module

        user = factories.UserFactory.create()
        _family, (a, b, c) = _unit_contexts(
            db_session, factories, user, family_title="Семья порядка", titles=["Порядок A", "Порядок B", "Порядок C"]
        )
        original = reconcile_module.load_request_material
        monkeypatch.setattr(
            reconcile_module, "load_request_material",
            lambda session, ids: dict(reversed(list(original(session, ids).items()))),
        )

        pairs = held_fingerprints(db_session, [c, b, a])

        assert [fp.context_id for fp in pairs] == sorted([a, b, c])

    def test_held_batch_is_found_among_approved_and_discarded_with_same_hash(self, db_session, factories):
        user = factories.UserFactory.create()
        fingerprints = [_fp(1, "a"), _fp(2, "b")]

        def call():
            return get_or_create_held_batch(
                db_session, fingerprints=fingerprints, source="mass", import_job_id=None, unit_id=None,
                reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
            )

        def decide(batch_id, status):
            db_session.execute(
                sa.update(SemanticReconcileBatch)
                .where(SemanticReconcileBatch.id == batch_id)
                .values(status=status, decided_by=user.id, decided_at=dt.datetime.now(dt.UTC))
            )

        approved = call()
        decide(approved, "approved")
        discarded = call()
        decide(discarded, "discarded")
        held = call()

        assert len({approved, discarded, held}) == 3
        assert call() == held

    def test_get_or_create_past_prepare_threshold(self, db_session):
        """Арбитр `ON CONFLICT` — частичный индекс: партией больше порога
        подготовки запросов psycopg3 (`docs/insights/batch-larger-than-five.md`),
        и по ветке вставки, и по ветке конфликта."""
        def call(n):
            return get_or_create_held_batch(
                db_session, fingerprints=[_fp(n, "h")], source="mass", import_job_id=None, unit_id=None,
                reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"),
            )

        created = [call(n) for n in range(12)]
        repeated = [call(n) for n in range(12)]

        assert len(set(created)) == 12
        assert repeated == created

    def test_held_fingerprints_is_exactly_postanovka(self, db_session, factories):
        """Набор постановки E — ровно контексты без задания текущего
        отпечатка и с возвращаемой отменой; все прочие статусы текущего
        задания в E не входят."""
        user = factories.UserFactory.create()
        in_e = {
            "none": None,
            "input_changed": (SemanticJobStatus.cancelled.value, SemanticCancelReason.input_changed.value),
            "not_applicable": (SemanticJobStatus.cancelled.value, SemanticCancelReason.not_applicable.value),
            "stale_hold": (SemanticJobStatus.cancelled.value, SemanticCancelReason.stale_hold.value),
        }
        out_of_e = {
            "pending": (SemanticJobStatus.pending.value, None),
            "running": (SemanticJobStatus.running.value, None),
            "privacy_hold": (SemanticJobStatus.privacy_hold.value, None),
            "done": (SemanticJobStatus.done.value, None),
            "error": (SemanticJobStatus.error.value, None),
            "privacy_declined": (SemanticJobStatus.cancelled.value, SemanticCancelReason.privacy_declined.value),
        }
        names = list(in_e) + list(out_of_e)
        _family, ids = _unit_contexts(
            db_session, factories, user, family_title="Семья состава E", titles=[f"Состав {n}" for n in names]
        )
        by_name = dict(zip(names, ids, strict=True))
        renders = {cid: _rendered_for(db_session, cid)[1] for cid in ids}
        for name, spec in {**in_e, **out_of_e}.items():
            if spec is None:
                continue
            status, reason = spec
            extra = {}
            if status == SemanticJobStatus.running.value:
                extra["claim_token"] = uuid.uuid4()
            if status == SemanticJobStatus.privacy_hold.value:
                extra["privacy_matches"] = []
            _make_job(db_session, context_id=by_name[name], request_hash=renders[by_name[name]].request_hash,
                      status=status, cancel_reason=reason, **extra)

        pairs = held_fingerprints(db_session, ids)

        assert [(fp.context_id, fp.request_hash) for fp in pairs] == sorted(
            (by_name[n], renders[by_name[n]].request_hash) for n in in_e
        )


# ---------------------------------------------------------------------------
#  Идемпотентность по снимку таблиц (а не только по отчёту)
# ---------------------------------------------------------------------------

class TestIdempotenceSnapshot:
    def _scenario(self, db, factories, user):
        """Все виды переходов разом: новый контекст, возврат, старый
        отпечаток, повторная публикация, неприменимый с открытым заданием и
        опубликованным предложением."""
        family, (a, b, c, d, e) = _unit_contexts(
            db, factories, user, family_title="Семья снимка",
            titles=["Снимок A", "Снимок B", "Снимок C", "Снимок D", "Снимок E"],
        )
        assign_family(db, context_id=e, family_id=family.id, actor_id=user.id)
        renders = {cid: _rendered_for(db, cid)[1] for cid in (a, b, c, d)}
        _make_job(db, context_id=b, request_hash=renders[b].request_hash,
                  status=SemanticJobStatus.cancelled.value, cancel_reason=SemanticCancelReason.not_applicable.value)
        _make_job(db, context_id=c, request_hash="old-hash", status=SemanticJobStatus.pending.value)
        _done_job_with_suggestion(db, context_id=d, request_hash=renders[d].request_hash, is_published=False)
        _make_job(db, context_id=e, request_hash="any-hash", status=SemanticJobStatus.pending.value)
        _done_job_with_suggestion(db, context_id=e, request_hash="e-done", is_published=True)
        return [a, b, c, d, e]

    @pytest.mark.parametrize("over_cap", [False, True])
    def test_second_call_changes_no_row(self, db_session, factories, over_cap):
        user = factories.UserFactory.create()
        ids = self._scenario(db_session, factories, user)
        cap = EventCap(max_contexts=0 if over_cap else 1000, max_reserve_usd=Decimal("1000000"))

        first = reconcile_semantic_jobs(db_session, ids, cap=cap, source="operation")
        snapshot = _table_snapshot(db_session)
        second = reconcile_semantic_jobs(db_session, ids, cap=cap, source="operation")

        assert (first.held_batch_id is not None) is over_cap
        assert second == ReconcileReport(
            created=0, revived=0, cancelled=0, republished=0, unpublished=0,
            held_batch_id=first.held_batch_id,
        )
        assert _table_snapshot(db_session) == snapshot


# ---------------------------------------------------------------------------
#  Сверка не коммитит: откат вызывающего снимает всё
# ---------------------------------------------------------------------------

class TestNoCommit:
    def test_caller_rollback_undoes_reconcile(self, committing_session_factory, committing_factories, committing_db):
        user = committing_factories.UserFactory.create()
        _family, (context_id,) = _unit_contexts(
            committing_db, committing_factories, user, family_title="Семья отката", titles=["Откат"]
        )
        committing_db.commit()

        session = committing_session_factory()
        try:
            report = reconcile_semantic_jobs(session, [context_id], cap=NO_CAP, source="operation")
            assert report.created == 1
            session.rollback()
        finally:
            session.close()

        count = committing_db.execute(
            sa.select(sa.func.count()).select_from(SemanticJob).where(SemanticJob.context_id == context_id)
        ).scalar_one()
        assert count == 0


# ---------------------------------------------------------------------------
#  Замки на задания: все сразу и в порядке id
# ---------------------------------------------------------------------------

def _mixed_jobs_scene(db, factories):
    """Три контекста одной единицы и по заданию, которое сверка изменит тремя
    разными видами перехода: возврат в очередь (текущий отпечаток, отменено),
    отмена прежнего отпечатка, отмена у контекста, ставшего неприменимым.
    Возврат — у задания с наименьшим id, а среди видов перехода выполняется последним."""
    user = factories.UserFactory.create()
    family, (revive_ctx, old_ctx, inapplicable_ctx) = _unit_contexts(
        db, factories, user, family_title="Семья замков", titles=["Возврат", "Прежний", "Неприменимый"]
    )
    _material, rendered = _rendered_for(db, revive_ctx)
    revive = _make_job(
        db, context_id=revive_ctx, request_hash=rendered.request_hash,
        status=SemanticJobStatus.cancelled.value, cancel_reason="input_changed",
    )
    old = _make_job(
        db, context_id=old_ctx, request_hash="old-hash", status=SemanticJobStatus.pending.value
    )
    inapplicable = _make_job(
        db, context_id=inapplicable_ctx, request_hash="other-old-hash",
        status=SemanticJobStatus.pending.value,
    )
    # Контекст стал неприменимым, а его открытое задание ещё не отменено:
    # отмену сделает сама сверка.
    db.execute(
        sa.text("UPDATE catalog_contexts SET semantic_state = 'NOT_APPLICABLE' WHERE id = :id"),
        {"id": inapplicable_ctx},
    )
    db.expire_all()
    return (revive_ctx, old_ctx, inapplicable_ctx), (revive, old, inapplicable)


class TestJobLocks:
    def test_all_jobs_it_will_change_are_locked_in_id_order_before_the_first_write(
        self, db_session, factories
    ):
        contexts, jobs = _mixed_jobs_scene(db_session, factories)
        statements: list[tuple[str, object]] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            reconcile_semantic_jobs(db_session, contexts, cap=NO_CAP, source="operation")
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        normalized = [(" ".join(sql.split()).upper(), params) for sql, params in statements]
        locks = [
            (i, params) for i, (sql, params) in enumerate(normalized)
            if sql.startswith("SELECT SEMANTIC_JOBS.ID") and "FOR UPDATE" in sql
        ]
        assert len(locks) == 1
        index, params = locks[0]
        assert "ORDER BY SEMANTIC_JOBS.ID" in normalized[index][0]
        assert sorted(params.values() if isinstance(params, dict) else params) == sorted(
            j.id for j in jobs
        )
        first_write = next(
            i for i, (sql, _p) in enumerate(normalized) if sql.startswith("UPDATE SEMANTIC_JOBS")
        )
        assert index < first_write

    def test_a_blocked_reconcile_holds_no_lock_on_the_jobs_it_has_not_reached(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Параллельная транзакция держит задание с меньшим id (то, что сверка
        меняет последним видом перехода). Сверка ждёт его, ничего другого не
        удерживая: задание с большим id свободно. Без общего порядка она успела бы
        отменить «прежнее» и ждала бы, держа его, — из пар таких сверок и
        получается цикл ожидания."""
        contexts, (first, second, third) = _mixed_jobs_scene(committing_db, committing_factories)
        committing_db.commit()
        lowest_id, other_ids = first.id, [second.id, third.id]
        assert lowest_id < min(other_ids)

        holder = committing_session_factory()
        outcome: dict[str, object] = {}

        def _reconcile() -> None:
            db = committing_session_factory()
            try:
                db.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
                outcome["report"] = reconcile_semantic_jobs(
                    db, contexts, cap=NO_CAP, source="operation"
                )
                db.commit()
            except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
                db.rollback()
                outcome["error"] = exc
            finally:
                db.close()

        def _waiting() -> int:
            with committing_session_factory() as probe:
                return probe.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                    )
                ).scalar_one()

        thread = threading.Thread(target=_reconcile, name="blocked-reconcile")
        try:
            holder.execute(
                sa.text("SELECT id FROM semantic_jobs WHERE id = :id FOR UPDATE"), {"id": lowest_id}
            )
            thread.start()
            deadline = time.monotonic() + 15
            while thread.is_alive() and _waiting() == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert thread.is_alive() and _waiting() > 0, "сверка не встала на замок"
            with committing_session_factory() as probe:
                free = probe.execute(
                    sa.text("SELECT id FROM semantic_jobs WHERE id = ANY(:ids) FOR UPDATE NOWAIT"),
                    {"ids": other_ids},
                ).scalars().all()
            holder.commit()
        finally:
            holder.rollback()
            holder.close()
            thread.join(timeout=_JOIN_TIMEOUT)

        assert not thread.is_alive(), "сверка зависла за отведённый таймаут"
        assert sorted(free) == sorted(other_ids)
        assert "error" not in outcome, outcome.get("error")


class TestInsertOrder:
    def test_new_jobs_are_inserted_in_context_id_order_whatever_the_input_order(
        self, db_session, factories
    ):
        """Новые задания вставляются по возрастанию `context_id` при любом порядке
        входа: две сверки общих контекстов ждут друг друга на уникальном индексе
        в одном и том же порядке."""
        from services.semantic_reconcile import _insert_new_jobs

        user = factories.UserFactory.create()
        _family, (first, second, third) = _unit_contexts(
            db_session, factories, user, family_title="Семья порядка", titles=["А", "Б", "В"]
        )
        materials = load_request_material(db_session, [first, second, third])
        rendered = {cid: render_context_request(m, settings=settings) for cid, m in materials.items()}

        _insert_new_jobs(db_session, [third, first, second], rendered, materials)

        inserted = db_session.execute(
            sa.select(SemanticJob.context_id)
            .where(SemanticJob.context_id.in_([first, second, third]))
            .order_by(SemanticJob.id)
        ).scalars().all()
        assert inserted == sorted([first, second, third])


class TestJobLocksSkipUnchangedJobs:
    def test_running_job_of_an_inapplicable_context_is_not_locked(self, db_session, factories):
        """Выполняющееся задание неприменимого контекста сверка не меняет, а замок на
        него держал бы запись результата исполнителя."""
        contexts, (revive, old, inapplicable) = _mixed_jobs_scene(db_session, factories)
        running = _make_job(
            db_session, context_id=contexts[2], request_hash="running-hash",
            status=SemanticJobStatus.running.value, claim_token=uuid.uuid4(),
        )
        statements: list[object] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement.upper() and "SEMANTIC_JOBS" in statement.upper():
                statements.append(parameters)

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            reconcile_semantic_jobs(db_session, contexts, cap=NO_CAP, source="operation")
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        [params] = statements
        locked = sorted(params.values() if isinstance(params, dict) else params)
        assert locked == sorted([revive.id, old.id, inapplicable.id])
        assert running.id not in locked

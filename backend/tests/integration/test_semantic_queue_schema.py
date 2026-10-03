"""Схема очереди семантических предложений (план фичи «Семантические
предложения», задача 1; спека `2026-09-28-semantic-suggestions-design.md`
§2.4): каждый CHECK, партиционный уникальный индекс, FK-цикл и downgrade —
доказаны пробоем, `INSERT` с ожиданием `IntegrityError`, по одному нарушенному
ограничению на вход.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR,
    CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON,
    CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT,
    CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY,
    CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES,
    CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR,
    CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR,
    CK_WORKER_STATE_PAUSED_AT_PAIR,
    CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR,
    CK_WORKER_STATE_PAUSED_REASON_PAIR,
    CK_WORKER_STATE_RESUMED_PAIR,
    CK_WORKER_STATE_SINGLETON_ID,
    RECONCILE_BATCH_SOURCES,
    RECONCILE_BATCH_STATUSES,
    SEMANTIC_ATTEMPT_OUTCOMES,
    SEMANTIC_CANCEL_REASONS,
    SEMANTIC_JOB_STATUSES,
    SUGGESTION_UNPUBLISHED_REASONS,
    CatalogContext,
    ContextBucket,
    DecisionSource,
    FamilySuggestion,
    NameRole,
    ReconcileBatchSource,
    ReconcileBatchStatus,
    SemanticAttemptOutcome,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobStatus,
    SemanticKind,
    SemanticReconcileBatch,
    SemanticState,
    SemanticWorkerState,
    SuggestionDecision,
    SuggestionUnpublishedReason,
)
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _bucket(session, factories) -> ContextBucket:
    cp = factories.CatalogPositionFactory.create()
    b = ContextBucket(catalog_position_id=cp.id, work_category_id=None)
    session.add(b)
    session.flush()
    return b


def _context(session, factories, **overrides) -> CatalogContext:
    bucket = overrides.pop("bucket", None) or _bucket(session, factories)
    defaults = dict(
        bucket_id=bucket.id,
        is_default=False,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_by=None,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_by=None,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


def _job(session, factories, context=None, **overrides) -> SemanticJob:
    context = context or _context(session, factories)
    defaults = dict(
        context_id=context.id,
        request_hash=f"req-{_uid()}",
        status=SemanticJobStatus.pending.value,
        cancel_reason=None,
        claim_token=None,
        prompt_version="v1",
        model_requested="gpt-test",
        place_dictionary_version=1,
        candidates_hash=f"cand-{_uid()}",
        prefix_hash=f"prefix-{_uid()}",
        input_hash=f"input-{_uid()}",
        response_schema_version="v1",
        serialization_version="v1",
    )
    defaults.update(overrides)
    job = SemanticJob(**defaults)
    session.add(job)
    session.flush()
    return job


def _attempt(session, factories, job=None, **overrides) -> SemanticJobAttempt:
    job = job or _job(session, factories)
    defaults = dict(
        job_id=job.id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=_now(),
        reserve_usd=Decimal("0.10"),
        prefix_hash=f"prefix-{_uid()}",
        privacy_dictionary_hash=f"pdict-{_uid()}",
    )
    defaults.update(overrides)
    attempt = SemanticJobAttempt(**defaults)
    session.add(attempt)
    session.flush()
    return attempt


def _suggestion(session, factories, context=None, job=None, attempt=None, **overrides) -> FamilySuggestion:
    context = context or _context(session, factories)
    job = job or _job(session, factories, context=context)
    attempt = attempt or _attempt(session, factories, job=job)
    defaults = dict(
        context_id=context.id,
        job_id=job.id,
        attempt_id=attempt.id,
        request_hash=job.request_hash,
        candidates_hash=f"cand-{_uid()}",
        candidates_snapshot={"candidates": []},
        family_id=None,
        new_family_name=f"Новая семья {_uid()}",
        confidence=Decimal("0.9"),
        reason="похожие формулировки",
        is_published=False,
    )
    defaults.update(overrides)
    suggestion = FamilySuggestion(**defaults)
    session.add(suggestion)
    session.flush()
    return suggestion


def _batch(session, factories, **overrides) -> SemanticReconcileBatch:
    defaults = dict(
        source=ReconcileBatchSource.import_.value,
        import_job_id=None,
        unit_id=None,
        held_fingerprints=[[1, "hash-a"]],
        fingerprints_hash=f"fp-{_uid()}",
        contexts_count=1,
        reserve_estimate_usd=Decimal("1.00"),
        cached_estimate_usd=Decimal("0.00"),
        status=ReconcileBatchStatus.held.value,
        decided_by=None,
        decided_at=None,
    )
    defaults.update(overrides)
    batch = SemanticReconcileBatch(**defaults)
    session.add(batch)
    session.flush()
    return batch


def _migration_0018():
    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0018-semantic_queue.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0018", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
#  1. Пять таблиц существуют — позитивный вход на каждую
# ---------------------------------------------------------------------------

class TestTablesExist:
    def test_semantic_reconcile_batch_row(self, db_session, factories):
        batch = _batch(db_session, factories)
        assert batch.id is not None

    def test_semantic_job_row(self, db_session, factories):
        job = _job(db_session, factories)
        assert job.id is not None

    def test_semantic_job_attempt_row(self, db_session, factories):
        attempt = _attempt(db_session, factories)
        assert attempt.id is not None

    def test_family_suggestion_row(self, db_session, factories):
        suggestion = _suggestion(db_session, factories)
        assert suggestion.id is not None

    def test_worker_state_row_seeded_by_migration(self, db_session):
        """Строку вставляет сама миграция 0018 — здесь она уже должна быть."""
        row = db_session.execute(
            sa.text("SELECT id, claim_paused FROM semantic_worker_state")
        ).one()
        assert row == (1, False)


# ---------------------------------------------------------------------------
#  2. semantic_jobs: status/cancel_reason, status/claim_token, privacy_hold
# ---------------------------------------------------------------------------

class TestSemanticJobsEquivalences:
    def test_cancelled_without_cancel_reason_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_status_cancel_reason_pair"'):
            _job(db_session, factories, status=SemanticJobStatus.cancelled.value, cancel_reason=None)

    def test_pending_with_cancel_reason_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_status_cancel_reason_pair"'):
            _job(
                db_session, factories, status=SemanticJobStatus.pending.value,
                cancel_reason=SemanticCancelReason.input_changed.value,
            )

    def test_cancelled_with_cancel_reason_passes(self, db_session, factories):
        job = _job(
            db_session, factories, status=SemanticJobStatus.cancelled.value,
            cancel_reason=SemanticCancelReason.input_changed.value,
        )
        assert job.id is not None

    def test_running_without_claim_token_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_status_claim_token_pair"'):
            _job(db_session, factories, status=SemanticJobStatus.running.value, claim_token=None)

    def test_pending_with_claim_token_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_status_claim_token_pair"'):
            _job(
                db_session, factories, status=SemanticJobStatus.pending.value,
                claim_token=uuid.uuid4(),
            )

    def test_running_with_claim_token_passes(self, db_session, factories):
        job = _job(
            db_session, factories, status=SemanticJobStatus.running.value, claim_token=uuid.uuid4()
        )
        assert job.id is not None

    def test_privacy_hold_without_privacy_matches_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_privacy_hold_requires_matches"'):
            _job(
                db_session, factories, status=SemanticJobStatus.privacy_hold.value,
                privacy_matches=None,
            )

    def test_privacy_hold_with_privacy_matches_passes(self, db_session, factories):
        job = _job(
            db_session, factories, status=SemanticJobStatus.privacy_hold.value,
            privacy_matches={"matches": ["Алматы"]},
        )
        assert job.id is not None

    def test_privacy_released_matches_none_is_sql_null(self, db_session, factories):
        """`none_as_null=True` у второй JSONB-колонки privacy: явный Python `None`
        ложится SQL `NULL`, а не JSON-литералом `null` (у `privacy_matches` то же
        свойство стережёт CHECK privacy_hold; у этой колонки CHECK нет — свойство
        проверяется прямо)."""
        job = _job(db_session, factories, privacy_released_matches=None)
        is_sql_null = db_session.execute(
            sa.text("SELECT privacy_released_matches IS NULL FROM semantic_jobs WHERE id = :id"),
            {"id": job.id},
        ).scalar_one()
        assert is_sql_null is True

    def test_unknown_status_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_status"'):
            _job(db_session, factories, status="bogus")

    def test_unknown_cancel_reason_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_cancel_reason"'):
            _job(db_session, factories, status=SemanticJobStatus.cancelled.value, cancel_reason="bogus")

    def test_duplicate_context_and_request_hash_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        _job(db_session, factories, context=ctx, request_hash="dup-hash")
        with rejected(db_session, contains='"uq_semantic_jobs_subject_request_hash"'):
            _job(db_session, factories, context=ctx, request_hash="dup-hash")

    def test_same_request_hash_different_context_passes(self, db_session, factories):
        j1 = _job(db_session, factories, request_hash="shared-hash")
        j2 = _job(db_session, factories, request_hash="shared-hash")
        assert j1.id != j2.id

    def test_delete_context_referenced_by_job_rejected(self, db_session, factories):
        job = _job(db_session, factories)
        with rejected(db_session, contains='"fk_semantic_jobs_context_id"'):
            db_session.execute(
                sa.text("DELETE FROM catalog_contexts WHERE id = :id"), {"id": job.context_id}
            )


# ---------------------------------------------------------------------------
#  3. semantic_job_attempts: outcome — закрытый список
# ---------------------------------------------------------------------------

class TestSemanticJobAttempts:
    def test_unknown_outcome_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_job_attempts_outcome"'):
            _attempt(db_session, factories, outcome="bogus")

    def test_null_outcome_passes(self, db_session, factories):
        attempt = _attempt(db_session, factories, outcome=None)
        assert attempt.id is not None

    def test_known_outcome_passes(self, db_session, factories):
        attempt = _attempt(db_session, factories, outcome=SemanticAttemptOutcome.ok.value)
        assert attempt.id is not None


# ---------------------------------------------------------------------------
#  4. family_suggestions: decision/decided_by/decided_at, published/unpublished
# ---------------------------------------------------------------------------

class TestFamilySuggestionsEquivalences:
    def test_decision_without_decided_by_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_suggestions_decision_author_pair"'):
            _suggestion(
                db_session, factories, decision=SuggestionDecision.accepted.value,
                decided_by=None, decided_at=_now(),
            )

    def test_decided_by_without_decision_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision_author_pair"'):
            _suggestion(db_session, factories, decision=None, decided_by=user.id, decided_at=None)

    def test_decision_without_decided_at_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision_at_pair"'):
            _suggestion(
                db_session, factories, decision=SuggestionDecision.accepted.value,
                decided_by=user.id, decided_at=None,
            )

    def test_decided_at_without_decision_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_suggestions_decision_at_pair"'):
            _suggestion(db_session, factories, decision=None, decided_by=None, decided_at=_now())

    def test_decision_with_author_and_time_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        suggestion = _suggestion(
            db_session, factories, decision=SuggestionDecision.accepted.value,
            decided_by=user.id, decided_at=_now(),
        )
        assert suggestion.id is not None

    def test_published_with_unpublished_reason_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_suggestions_published_no_unpublished_reason"'):
            _suggestion(
                db_session, factories, is_published=True,
                unpublished_reason=SuggestionUnpublishedReason.rejected.value,
            )

    def test_published_without_unpublished_reason_passes(self, db_session, factories):
        suggestion = _suggestion(db_session, factories, is_published=True, unpublished_reason=None)
        assert suggestion.id is not None

    def test_unpublished_with_reason_passes(self, db_session, factories):
        suggestion = _suggestion(
            db_session, factories, is_published=False,
            unpublished_reason=SuggestionUnpublishedReason.rejected.value,
        )
        assert suggestion.id is not None

    def test_unknown_unpublished_reason_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_suggestions_unpublished_reason"'):
            _suggestion(db_session, factories, is_published=False, unpublished_reason="bogus")

    def test_unknown_decision_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision"'):
            _suggestion(db_session, factories, decision="bogus", decided_by=user.id, decided_at=_now())

    def test_delete_context_referenced_only_by_suggestion_rejected(self, db_session, factories):
        """Спека §2.4: `family_suggestions.context_id` — `ON DELETE RESTRICT`.
        Задание предложения стоит на ДРУГОМ контексте, иначе отказ дал бы
        `fk_semantic_jobs_context_id` и маскировал бы эту ссылку."""
        job_ctx = _context(db_session, factories)
        suggestion_ctx = _context(db_session, factories)
        job = _job(db_session, factories, context=job_ctx)
        _suggestion(db_session, factories, context=suggestion_ctx, job=job)
        with rejected(db_session, contains='"fk_family_suggestions_context_id"'):
            db_session.execute(
                sa.text("DELETE FROM catalog_contexts WHERE id = :id"), {"id": suggestion_ctx.id}
            )

    def test_candidates_snapshot_orm_none_rejected(self, db_session, factories):
        """Без `JSONB(none_as_null=True)` явный ORM `None` ложится
        JSON-литералом `null`, и NOT NULL спеки §2.4 не срабатывает вовсе
        (вставка проходит, `jsonb_typeof = 'null'`) — тот же класс дефекта, что
        у `SemanticJob.privacy_matches`. Снятие `none_as_null=True` красит этот
        тест в DID NOT RAISE."""
        with rejected(db_session, contains='"candidates_snapshot"'):
            _suggestion(db_session, factories, candidates_snapshot=None)

    def test_second_published_suggestion_same_context_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        _suggestion(db_session, factories, context=ctx, is_published=True)
        with rejected(db_session, contains='"uq_family_suggestions_context_id_published"'):
            _suggestion(db_session, factories, context=ctx, is_published=True)

    def test_second_unpublished_suggestion_same_context_passes(self, db_session, factories):
        ctx = _context(db_session, factories)
        s1 = _suggestion(db_session, factories, context=ctx, is_published=False)
        s2 = _suggestion(db_session, factories, context=ctx, is_published=False)
        assert s1.id != s2.id


# ---------------------------------------------------------------------------
#  5. semantic_reconcile_batches: held/decided_by/decided_at, partial unique
# ---------------------------------------------------------------------------

class TestReconcileBatchesEquivalences:
    def test_held_fingerprints_orm_none_rejected(self, db_session, factories):
        """Тот же дефект, что у
        `FamilySuggestion.candidates_snapshot`: без `JSONB(none_as_null=True)`
        ORM `None` ложится JSON `null`, и NOT NULL не срабатывает."""
        with rejected(db_session, contains='"held_fingerprints"'):
            _batch(db_session, factories, held_fingerprints=None)

    def test_held_batch_with_decided_by_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_held_no_decided_by"'):
            _batch(db_session, factories, status=ReconcileBatchStatus.held.value, decided_by=user.id)

    def test_approved_batch_without_decided_by_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_held_no_decided_by"'):
            _batch(
                db_session, factories, status=ReconcileBatchStatus.approved.value,
                decided_by=None, decided_at=_now(),
            )

    def test_held_batch_with_decided_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_held_no_decided_at"'):
            _batch(db_session, factories, status=ReconcileBatchStatus.held.value, decided_at=_now())

    def test_approved_batch_without_decided_at_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_held_no_decided_at"'):
            _batch(
                db_session, factories, status=ReconcileBatchStatus.approved.value,
                decided_by=user.id, decided_at=None,
            )

    def test_held_batch_without_decision_passes(self, db_session, factories):
        batch = _batch(db_session, factories, status=ReconcileBatchStatus.held.value)
        assert batch.id is not None

    def test_approved_batch_with_decision_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        batch = _batch(
            db_session, factories, status=ReconcileBatchStatus.approved.value,
            decided_by=user.id, decided_at=_now(),
        )
        assert batch.id is not None

    def test_discarded_batch_with_decision_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        batch = _batch(
            db_session, factories, status=ReconcileBatchStatus.discarded.value,
            decided_by=user.id, decided_at=_now(),
        )
        assert batch.id is not None

    def test_unknown_source_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_source"'):
            _batch(db_session, factories, source="bogus")

    def test_unknown_status_rejected(self, db_session, factories):
        """decided_by/decided_at заполнены ОБА, чтобы вход пробивал только
        членство статуса, не равносильности held/decided_* (status='bogus' —
        не 'held', и голые equivalence-CHECK'и иначе сработали бы первыми)."""
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_semantic_reconcile_batches_status"'):
            _batch(db_session, factories, status="bogus", decided_by=user.id, decided_at=_now())

    def test_second_held_batch_same_fingerprints_hash_rejected(self, db_session, factories):
        h = f"dup-fp-{_uid()}"
        _batch(db_session, factories, status=ReconcileBatchStatus.held.value, fingerprints_hash=h)
        with rejected(db_session, contains='"uq_semantic_reconcile_batches_fingerprints_held"'):
            _batch(db_session, factories, status=ReconcileBatchStatus.held.value, fingerprints_hash=h)

    def test_partial_unique_index_is_a_valid_on_conflict_arbiter(self, db_session, factories):
        """Решение оркестратора задачи 1: индекс обязан годиться как арбитр
        `INSERT ... ON CONFLICT (fingerprints_hash) WHERE status='held' DO
        NOTHING` (задача 6) — предикат буквально `status = 'held'`. Проверено
        РЕАЛЬНЫМ `ON CONFLICT`, а не только тем, что голый дубль отвергается."""
        h = f"dup-fp-{_uid()}"
        first = _batch(db_session, factories, status=ReconcileBatchStatus.held.value, fingerprints_hash=h)
        result = db_session.execute(
            sa.text(
                "INSERT INTO semantic_reconcile_batches "
                "(source, held_fingerprints, fingerprints_hash, contexts_count, "
                " reserve_estimate_usd, cached_estimate_usd, status) "
                "VALUES ('import', :fp, :hash, 1, 1.00, 0.00, 'held') "
                "ON CONFLICT (fingerprints_hash) WHERE status = 'held' DO NOTHING "
                "RETURNING id"
            ),
            {"fp": '[[1, "hash-a"]]', "hash": h},
        )
        db_session.flush()
        assert result.fetchall() == []
        count = db_session.execute(
            sa.text("SELECT count(*) FROM semantic_reconcile_batches WHERE fingerprints_hash = :hash"),
            {"hash": h},
        ).scalar_one()
        assert count == 1
        assert first.id is not None

    def test_approved_batch_same_fingerprints_hash_as_held_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        h = f"dup-fp-{_uid()}"
        held = _batch(db_session, factories, status=ReconcileBatchStatus.held.value, fingerprints_hash=h)
        approved = _batch(
            db_session, factories, status=ReconcileBatchStatus.approved.value, fingerprints_hash=h,
            decided_by=user.id, decided_at=_now(),
        )
        assert held.id != approved.id

    def test_discarded_batch_same_fingerprints_hash_as_held_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        h = f"dup-fp-{_uid()}"
        held = _batch(db_session, factories, status=ReconcileBatchStatus.held.value, fingerprints_hash=h)
        discarded = _batch(
            db_session, factories, status=ReconcileBatchStatus.discarded.value, fingerprints_hash=h,
            decided_by=user.id, decided_at=_now(),
        )
        assert held.id != discarded.id


# ---------------------------------------------------------------------------
#  6. semantic_worker_state: паузa-триплет, last_resumed-пара, singleton
# ---------------------------------------------------------------------------

class TestWorkerStateEquivalences:
    """Живая строка id=1 (вставлена миграцией) обновляется UPDATE-ом —
    отдельная строка id=2 заводится только для пробоя singleton-CHECK."""

    def _update(self, db_session, **values) -> None:
        cols = ", ".join(f"{k} = :{k}" for k in values)
        db_session.execute(sa.text(f"UPDATE semantic_worker_state SET {cols} WHERE id = 1"), values)
        db_session.flush()

    def test_paused_without_reason_rejected(self, db_session, factories):
        attempt = _attempt(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_reason_pair"'):
            self._update(
                db_session, claim_paused=True, paused_reason=None,
                paused_attempt_id=attempt.id, paused_at=_now(),
            )

    def test_paused_without_attempt_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_attempt_pair"'):
            self._update(
                db_session, claim_paused=True, paused_reason="лимит бюджета",
                paused_attempt_id=None, paused_at=_now(),
            )

    def test_paused_without_paused_at_rejected(self, db_session, factories):
        attempt = _attempt(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_at_pair"'):
            self._update(
                db_session, claim_paused=True, paused_reason="лимит бюджета",
                paused_attempt_id=attempt.id, paused_at=None,
            )

    def test_not_paused_with_reason_rejected(self, db_session):
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_reason_pair"'):
            self._update(db_session, claim_paused=False, paused_reason="лимит бюджета")

    def test_not_paused_with_attempt_rejected(self, db_session, factories):
        attempt = _attempt(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_attempt_pair"'):
            self._update(db_session, claim_paused=False, paused_attempt_id=attempt.id)

    def test_not_paused_with_paused_at_rejected(self, db_session):
        with rejected(db_session, contains='"ck_semantic_worker_state_paused_at_pair"'):
            self._update(db_session, claim_paused=False, paused_at=_now())

    def test_paused_with_all_three_fields_passes(self, db_session, factories):
        attempt = _attempt(db_session, factories)
        self._update(
            db_session, claim_paused=True, paused_reason="лимит бюджета",
            paused_attempt_id=attempt.id, paused_at=_now(),
        )
        row = db_session.execute(
            sa.text("SELECT claim_paused FROM semantic_worker_state WHERE id = 1")
        ).scalar_one()
        assert row is True

    def test_last_resumed_by_without_at_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_semantic_worker_state_resumed_pair"'):
            self._update(db_session, last_resumed_by=user.id, last_resumed_at=None)

    def test_last_resumed_at_without_by_rejected(self, db_session):
        with rejected(db_session, contains='"ck_semantic_worker_state_resumed_pair"'):
            self._update(db_session, last_resumed_by=None, last_resumed_at=_now())

    def test_last_resumed_pair_together_passes(self, db_session, factories):
        user = factories.UserFactory.create()
        self._update(db_session, last_resumed_by=user.id, last_resumed_at=_now())
        row = db_session.execute(
            sa.text("SELECT last_resumed_by FROM semantic_worker_state WHERE id = 1")
        ).scalar_one()
        assert row == user.id

    def test_delete_attempt_referenced_by_pause_rejected_by_fk(self, db_session, factories):
        """`paused_attempt_id` — `ON DELETE RESTRICT`: удаление попытки, на
        которой остановлен захват, отвергает сам FK. При `SET NULL` отказ дал бы
        CHECK `ck_semantic_worker_state_paused_attempt_pair` (NULL при
        `claim_paused=true`), при `CASCADE` удаление прошло бы вместе со строкой
        состояния."""
        attempt = _attempt(db_session, factories)
        self._update(
            db_session, claim_paused=True, paused_reason="лимит бюджета",
            paused_attempt_id=attempt.id, paused_at=_now(),
        )
        with rejected(db_session, contains='"fk_semantic_worker_state_paused_attempt_id"'):
            db_session.execute(
                sa.text("DELETE FROM semantic_job_attempts WHERE id = :id"), {"id": attempt.id}
            )

    def test_worker_state_id_is_not_autonumbered(self, db_session):
        """Спека §2.4: `id smallint PK CHECK (id = 1)` — фиксированный singleton,
        без последовательности и без `DEFAULT nextval(...)`."""
        row = db_session.execute(
            sa.text(
                "SELECT pg_get_serial_sequence('semantic_worker_state', 'id'), column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'semantic_worker_state' AND column_name = 'id'"
            )
        ).one()
        assert row == (None, None)

    def test_second_worker_state_row_rejected(self, db_session):
        with rejected(db_session, contains='"ck_semantic_worker_state_singleton_id"'):
            db_session.add(SemanticWorkerState(id=2, claim_paused=False))
            db_session.flush()


# ---------------------------------------------------------------------------
#  7. Parity: литералы IN(...) миграции 0018 = models.py = независимый литерал
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    """Каждый из семи перечислений сверен ТРОЙКОЙ независимых источников:
    константа `models.py`, константа миграции 0018 и литерал, написанный
    здесь заново (не переиспользующий ни генератор `_sql_str_list`, ни код
    перечисления) — сверщик не должен делить предикат с генератором,
    который проверяет (`docs/insights/verifier-sharing-predicate-with-generator.md`).
    """

    _CASES = [
        (SEMANTIC_JOB_STATUSES, "SEMANTIC_JOB_STATUSES",
         {"pending", "running", "done", "error", "cancelled", "privacy_hold"}),
        (SEMANTIC_CANCEL_REASONS, "SEMANTIC_CANCEL_REASONS",
         {"input_changed", "not_applicable", "privacy_declined", "stale_hold"}),
        (SEMANTIC_ATTEMPT_OUTCOMES, "SEMANTIC_ATTEMPT_OUTCOMES",
         {"ok", "transient_error", "permanent_error", "schema_error", "lost_claim"}),
        (SUGGESTION_UNPUBLISHED_REASONS, "SUGGESTION_UNPUBLISHED_REASONS",
         {"stale_fingerprint", "lost_claim", "context_not_applicable", "rejected"}),
        # Расширено миграцией 0019: здесь замороженная редакция 0018 против её
        # прежнего литерала; `models.py` против 0019 — test_work_variants_schema.py.
        ("'accepted', 'rejected', 'other_family', 'family_created'", "SUGGESTION_DECISIONS",
         {"accepted", "rejected", "other_family", "family_created"}),
        (RECONCILE_BATCH_SOURCES, "RECONCILE_BATCH_SOURCES",
         {"import", "operation", "mass", "unit_reask", "config_reask"}),
        (RECONCILE_BATCH_STATUSES, "RECONCILE_BATCH_STATUSES", {"held", "approved", "discarded"}),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "migration_name", "independent_literal"), _CASES,
        ids=[name for _, name, _ in _CASES],
    )
    def test_model_matches_migration_and_independent_literal(
        self, model_expr, migration_name, independent_literal
    ):
        migration_value = getattr(_migration_0018(), migration_name)
        assert model_expr == migration_value
        parsed_from_model = {piece.strip().strip("'") for piece in model_expr.split(",")}
        assert parsed_from_model == independent_literal

    _CK_CASES = [
        (CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR, "CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR"),
        (CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR, "CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR"),
        (
            CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES,
            "CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES",
        ),
        # Расширено миграцией 0019: замороженная редакция 0018 против прежнего литерала.
        (
            "(decision IS NULL) = (decided_by IS NULL)",
            "CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR",
        ),
        (CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR, "CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR"),
        (
            CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON,
            "CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON",
        ),
        (CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY, "CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY"),
        (CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT, "CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT"),
        (CK_WORKER_STATE_PAUSED_REASON_PAIR, "CK_WORKER_STATE_PAUSED_REASON_PAIR"),
        (CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR, "CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR"),
        (CK_WORKER_STATE_PAUSED_AT_PAIR, "CK_WORKER_STATE_PAUSED_AT_PAIR"),
        (CK_WORKER_STATE_RESUMED_PAIR, "CK_WORKER_STATE_RESUMED_PAIR"),
        (CK_WORKER_STATE_SINGLETON_ID, "CK_WORKER_STATE_SINGLETON_ID"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "migration_name"), _CK_CASES, ids=[name for _, name in _CK_CASES],
    )
    def test_check_expressions_match_migration(self, model_expr, migration_name):
        assert model_expr == getattr(_migration_0018(), migration_name)


# ---------------------------------------------------------------------------
#  8. Все пять таблиц — явно в _DOMAIN_TABLES; singleton переживает очистку
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_all_five_tables_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in (
            "semantic_jobs", "semantic_job_attempts", "family_suggestions",
            "semantic_reconcile_batches", "semantic_worker_state",
        ):
            assert table in _DOMAIN_TABLES

    def test_worker_state_row_survives_domain_tables_truncate(self, db_engine):
        """`TRUNCATE` доменных таблиц сносит singleton-строку — фикстура
        обязана вставить её заново, иначе захват (задача 10) не работает."""
        from tests.conftest import _truncate_domain_tables

        _truncate_domain_tables(db_engine)

        with db_engine.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT id, claim_paused FROM semantic_worker_state")
            ).all()
        assert rows == [(1, False)]


# ---------------------------------------------------------------------------
#  9. downgrade — три счётчика по отдельности плюс живой прогон на scratch-базе
# ---------------------------------------------------------------------------

class TestDowngradeRefusalLogic:
    """Чистая логика `_downgrade_refusal`, без обращения к БД: каждый из трёх
    счётчиков — единственный положительный, значение (5) отличительное."""

    _ZERO = {"semantic_jobs": 0, "family_suggestions": 0, "semantic_reconcile_batches": 0}
    _PHRASE_BY_KEY = {
        "semantic_jobs": "заданий — 5",
        "family_suggestions": "предложений — 5",
        "semantic_reconcile_batches": "удержанных пачек — 5",
    }

    @pytest.mark.parametrize(
        "key", ["semantic_jobs", "family_suggestions", "semantic_reconcile_batches"]
    )
    def test_each_counter_alone_blocks_and_is_named(self, key):
        m = _migration_0018()
        blockers = dict(self._ZERO)
        blockers[key] = 5
        refusal = m._downgrade_refusal(blockers)
        assert refusal is not None
        assert self._PHRASE_BY_KEY[key] in refusal

    def test_all_zero_blockers_pass(self):
        m = _migration_0018()
        assert m._downgrade_refusal(dict(self._ZERO)) is None


class TestDowngradeBlockersLive:
    def test_clean_schema_does_not_block(self, db_session):
        m = _migration_0018()
        assert m._downgrade_refusal(m._downgrade_blockers(db_session.connection())) is None

    def test_semantic_jobs_counted_by_their_own_table(self, db_session, factories):
        for _ in range(3):
            _job(db_session, factories)
        db_session.flush()
        m = _migration_0018()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "semantic_jobs": 3, "family_suggestions": 0, "semantic_reconcile_batches": 0,
        }
        assert "заданий — 3" in m._downgrade_refusal(blockers)

    def test_family_suggestions_counted_by_their_own_table_not_jobs(self, db_session, factories):
        """Одно задание, ДВЕ строки предложений на нём (два ответа модели):
        `semantic_jobs`=1, `family_suggestions`=2 — запрос, случайно считающий
        предложения по заданиям (или наоборот), здесь дал бы 1=1."""
        job = _job(db_session, factories)
        attempt1 = _attempt(db_session, factories, job=job)
        attempt2 = _attempt(db_session, factories, job=job)
        _suggestion(db_session, factories, context_id=job.context_id, job=job, attempt=attempt1)
        _suggestion(db_session, factories, context_id=job.context_id, job=job, attempt=attempt2)
        db_session.flush()
        m = _migration_0018()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "semantic_jobs": 1, "family_suggestions": 2, "semantic_reconcile_batches": 0,
        }
        assert "предложений — 2" in m._downgrade_refusal(blockers)

    def test_reconcile_batches_counted_by_their_own_table_not_jobs(self, db_session, factories):
        _job(db_session, factories)
        _batch(db_session, factories)
        _batch(db_session, factories)
        db_session.flush()
        m = _migration_0018()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "semantic_jobs": 1, "family_suggestions": 0, "semantic_reconcile_batches": 2,
        }
        assert "удержанных пачек — 2" in m._downgrade_refusal(blockers)


class TestDowngradeFifthInputReal:
    """Пятый вход `downgrade` — РЕАЛЬНЫЙ прогон `alembic upgrade head` →
    `downgrade 0017` → `upgrade head` на ОТДЕЛЬНОЙ scratch-базе. Сессионная
    тестовая БД (`db_engine`) не трогается — `downgrade` на ней сломал бы все
    остальные тесты файла.

    Второй прогон на ТОЙ ЖЕ базе, уже с данными фичи 1 (строка `work_families`)
    и пустой очередью, доказывает утверждение плана «на пустой очереди при
    данных фичи 1 откат проходит»: отказ смотрит только на свои три таблицы.
    """

    def test_downgrade_then_upgrade_round_trip_with_feature1_data(self):
        test_url = os.environ.get("TEST_DATABASE_URL")
        if not test_url:
            pytest.skip("TEST_DATABASE_URL не задан")

        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        from db_guard import ensure_mutation_allowed
        from tests.conftest import _create_worker_database

        parsed = make_url(test_url)
        base = (parsed.database or "gca").removesuffix("_test") or "gca"
        scratch_name = f"{base}_scratch_{_uid()}_test"
        scratch_url = parsed.set(database=scratch_name).render_as_string(hide_password=False)

        ensure_mutation_allowed(scratch_url, "test_semantic_queue_schema scratch db (task 1)")
        _create_worker_database(scratch_url)

        try:
            from alembic import command
            from alembic.config import Config

            backend_root = Path(__file__).resolve().parents[2]
            cfg = Config(str(backend_root / "alembic.ini"))
            cfg.set_main_option("script_location", str(backend_root / "alembic"))
            cfg.set_main_option("sqlalchemy.url", scratch_url)

            environ_snapshot = dict(os.environ)
            try:
                command.upgrade(cfg, "head")

                engine = sa.create_engine(scratch_url)
                try:
                    with engine.begin() as conn:
                        five_tables_present = conn.execute(
                            sa.text(
                                "SELECT to_regclass('public.semantic_jobs') IS NOT NULL "
                                "AND to_regclass('public.semantic_job_attempts') IS NOT NULL "
                                "AND to_regclass('public.family_suggestions') IS NOT NULL "
                                "AND to_regclass('public.semantic_reconcile_batches') IS NOT NULL "
                                "AND to_regclass('public.semantic_worker_state') IS NOT NULL"
                            )
                        ).scalar_one()
                        assert five_tables_present is True
                finally:
                    engine.dispose()

                # Пустая база (ни данных фичи 1, ни очереди) — первая половина
                # утверждения плана «downgrade -1 проходит на пустой базе и на
                # базе с данными фичи 1»: откат и повторный накат.
                command.downgrade(cfg, "0017")
                command.upgrade(cfg, "head")

                engine = sa.create_engine(scratch_url)
                try:
                    with engine.begin() as conn:
                        # Данные фичи 1, очередь пустая — откат обязан пройти
                        # (утверждение плана задачи 1).
                        conn.execute(
                            sa.text(
                                "INSERT INTO work_families (title, status, seed_key) "
                                "VALUES ('Штукатурка (scratch)', 'draft', :seed_key)"
                            ),
                            {"seed_key": f"scratch-{_uid()}"},
                        )
                finally:
                    engine.dispose()

                command.downgrade(cfg, "0017")

                gone_engine = sa.create_engine(scratch_url)
                try:
                    with gone_engine.connect() as conn:
                        five_tables_gone = conn.execute(
                            sa.text(
                                "SELECT to_regclass('public.semantic_jobs') IS NULL "
                                "AND to_regclass('public.semantic_job_attempts') IS NULL "
                                "AND to_regclass('public.family_suggestions') IS NULL "
                                "AND to_regclass('public.semantic_reconcile_batches') IS NULL "
                                "AND to_regclass('public.semantic_worker_state') IS NULL"
                            )
                        ).scalar_one()
                        work_family_survived = conn.execute(
                            sa.text("SELECT count(*) FROM work_families")
                        ).scalar_one()
                finally:
                    gone_engine.dispose()
                assert five_tables_gone is True
                assert work_family_survived == 1

                command.upgrade(cfg, "head")
            finally:
                os.environ.clear()
                os.environ.update(environ_snapshot)

            check_engine = sa.create_engine(scratch_url)
            try:
                with check_engine.connect() as conn:
                    five_tables_exist = conn.execute(
                        sa.text(
                            "SELECT to_regclass('public.semantic_jobs') IS NOT NULL "
                            "AND to_regclass('public.semantic_job_attempts') IS NOT NULL "
                            "AND to_regclass('public.family_suggestions') IS NOT NULL "
                            "AND to_regclass('public.semantic_reconcile_batches') IS NOT NULL "
                            "AND to_regclass('public.semantic_worker_state') IS NOT NULL"
                        )
                    ).scalar_one()
                    worker_state_row = conn.execute(
                        sa.text("SELECT id, claim_paused FROM semantic_worker_state")
                    ).one()
            finally:
                check_engine.dispose()
            assert five_tables_exist is True
            assert worker_state_row == (1, False)
        finally:
            with psycopg.connect(
                host=parsed.host, port=parsed.port, user=parsed.username,
                password=parsed.password, dbname="postgres", autocommit=True,
            ) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(scratch_name)
                    )
                )

    def test_downgrade_blocked_by_pending_job_on_scratch_db(self):
        """Отказ downgrade — тоже живой: задание в очереди блокирует откат
        по-настоящему, не только через чистую логику `_downgrade_refusal`."""
        test_url = os.environ.get("TEST_DATABASE_URL")
        if not test_url:
            pytest.skip("TEST_DATABASE_URL не задан")

        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        from db_guard import ensure_mutation_allowed
        from tests.conftest import _create_worker_database

        parsed = make_url(test_url)
        base = (parsed.database or "gca").removesuffix("_test") or "gca"
        scratch_name = f"{base}_scratch_{_uid()}_test"
        scratch_url = parsed.set(database=scratch_name).render_as_string(hide_password=False)

        ensure_mutation_allowed(scratch_url, "test_semantic_queue_schema scratch db (task 1, blocked)")
        _create_worker_database(scratch_url)

        try:
            from alembic import command
            from alembic.config import Config

            backend_root = Path(__file__).resolve().parents[2]
            cfg = Config(str(backend_root / "alembic.ini"))
            cfg.set_main_option("script_location", str(backend_root / "alembic"))
            cfg.set_main_option("sqlalchemy.url", scratch_url)

            environ_snapshot = dict(os.environ)
            try:
                command.upgrade(cfg, "head")

                engine = sa.create_engine(scratch_url)
                try:
                    with engine.begin() as conn:
                        bucket_id = conn.execute(
                            sa.text(
                                "INSERT INTO catalog_positions "
                                "(standard_job_title, normalized_job_title, kind, status) "
                                "VALUES ('Работа (scratch)', 'работа (scratch)', 'POSITION', 'na') "
                                "RETURNING id"
                            )
                        ).scalar_one()
                        bucket_id = conn.execute(
                            sa.text(
                                "INSERT INTO context_buckets (catalog_position_id) "
                                "VALUES (:cp) RETURNING id"
                            ),
                            {"cp": bucket_id},
                        ).scalar_one()
                        context_id = conn.execute(
                            sa.text(
                                "INSERT INTO catalog_contexts "
                                "(bucket_id, semantic_kind, semantic_kind_source, semantic_kind_at, "
                                " name_role, name_role_source, name_role_at, place_dictionary_version, "
                                " semantic_state) "
                                "VALUES (:bucket_id, 'WORK', 'rule', now(), 'WORK', 'rule', now(), 1, "
                                " 'SUGGESTED') RETURNING id"
                            ),
                            {"bucket_id": bucket_id},
                        ).scalar_one()
                        conn.execute(
                            sa.text(
                                "INSERT INTO semantic_jobs "
                                "(kind, context_id, request_hash, status, prompt_version, "
                                " model_requested, place_dictionary_version, candidates_hash, "
                                " prefix_hash, input_hash, response_schema_version, "
                                " serialization_version) "
                                "VALUES ('family_suggestion', :context_id, 'scratch-hash', "
                                " 'pending', 'v1', 'gpt-test', 1, 'c', 'p', 'i', 'v1', 'v1')"
                            ),
                            {"context_id": context_id},
                        )
                finally:
                    engine.dispose()

                with pytest.raises(Exception, match="Откат 0018 невозможен"):
                    command.downgrade(cfg, "0017")
            finally:
                os.environ.clear()
                os.environ.update(environ_snapshot)
        finally:
            with psycopg.connect(
                host=parsed.host, port=parsed.port, user=parsed.username,
                password=parsed.password, dbname="postgres", autocommit=True,
            ) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(scratch_name)
                    )
                )

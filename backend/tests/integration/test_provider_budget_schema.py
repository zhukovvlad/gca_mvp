"""Схема паузы до сброса лимита провайдера (миграция 0026; спека
`2026-11-15-provider-budget-pause-design.md` §2.6).

Новое: исход попытки `budget_refused`, пять полей паузы у `semantic_worker_state` и два
CHECK к ним. Каждый отказ сверяется с именем ограничения (`diag.constraint_name`), а
вход строится по одному на каждое заявление проверки (`docs/pitfalls/db.md`).
`upgrade` и `downgrade` — на отдельной scratch-базе (`migration_roundtrip`).
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_WORKER_STATE_BUDGET_PAUSE_TRIPLE,
    CK_WORKER_STATE_BUDGET_SUMS,
    SEMANTIC_ATTEMPT_OUTCOMES,
    SemanticAttemptOutcome,
    SemanticWorkerState,
)
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_schema import _attempt
from tests.integration.test_work_variants_schema import _scratch_alembic

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_STATE_COLUMNS = (
    "budget_paused_until", "budget_pause_attempt_id", "budget_pause_source",
    "budget_limit_usd", "budget_spend_usd",
)
_OUTCOME_CHECK = "ck_semantic_job_attempts_outcome"
_TRIPLE = "ck_semantic_worker_state_budget_pause_triple"
_SUMS = "ck_semantic_worker_state_budget_sums"


def _migration_0026():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0026-provider_budget_pause.py"))
    spec = importlib.util.spec_from_file_location("_migration_0026_budget_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now():
    return datetime.now(UTC)


def _set_state(session, **values):
    session.execute(
        sa.update(SemanticWorkerState).where(SemanticWorkerState.id == 1).values(**values)
    )


def _paused(session, factories, **extra):
    attempt = _attempt(session, factories, outcome="budget_refused")
    _set_state(
        session, budget_paused_until=_now(), budget_pause_attempt_id=attempt.id,
        budget_pause_source="provider_budget", **extra,
    )
    session.flush()
    return attempt


# ---------------------------------------------------------------------------
#  Схема на базе, мигрированной до head
# ---------------------------------------------------------------------------

class TestSchemaAtHead:
    def test_the_revision_chain(self):
        module = _migration_0026()
        assert (module.revision, module.down_revision) == ("0026", "0025")

    def test_the_five_columns_exist_and_are_nullable(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name = 'semantic_worker_state' AND column_name LIKE 'budget\\_%'"
            )
        ).all()
        assert {r[0]: (r[1], r[2]) for r in rows} == {
            "budget_paused_until": ("YES", "timestamp with time zone"),
            "budget_pause_attempt_id": ("YES", "bigint"),
            "budget_pause_source": ("YES", "text"),
            "budget_limit_usd": ("YES", "numeric"),
            "budget_spend_usd": ("YES", "numeric"),
        }

    def test_the_attempt_foreign_key_restricts_deletion(self, db_session, factories):
        attempt = _paused(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            db_session.execute(
                sa.text("DELETE FROM semantic_job_attempts WHERE id = :i"), {"i": attempt.id}
            )
        assert exc.value.orig.diag.constraint_name.endswith("_fkey")

    def test_the_closed_set_and_the_constraint_texts_match_the_migration(self):
        module = _migration_0026()
        assert SemanticAttemptOutcome.budget_refused.value == "budget_refused"
        assert f"outcome IS NULL OR outcome IN ({SEMANTIC_ATTEMPT_OUTCOMES})" == (
            module._outcome_check(module.OUTCOMES)
        )
        # Независимый литерал: сверщик не делит предикат с генератором `_sql_str_list`.
        assert {p.strip().strip("'") for p in SEMANTIC_ATTEMPT_OUTCOMES.split(",")} == {
            "ok", "transient_error", "permanent_error", "schema_error", "lost_claim",
            "budget_refused",
        }
        assert CK_WORKER_STATE_BUDGET_PAUSE_TRIPLE == module.CK_BUDGET_PAUSE_TRIPLE
        assert CK_WORKER_STATE_BUDGET_SUMS == module.CK_BUDGET_SUMS

    def test_the_constraints_are_named_in_the_database(self, db_session):
        names = {
            r[0]
            for r in db_session.execute(
                sa.text("SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_semantic_%'")
            )
        }
        assert {_OUTCOME_CHECK, _TRIPLE, _SUMS} <= names


class TestOutcomeCheck:
    def test_a_budget_refused_attempt_is_accepted(self, db_session, factories):
        attempt = _attempt(db_session, factories, outcome="budget_refused")
        assert attempt.outcome == "budget_refused"

    def test_an_unlisted_outcome_is_rejected(self, db_session, factories):
        with rejected_by(db_session, _OUTCOME_CHECK):
            _attempt(db_session, factories, outcome="budget_unknown")


class TestPauseTripleCheck:
    def test_all_three_set_is_accepted(self, db_session, factories):
        _paused(db_session, factories)

    def test_all_three_empty_is_accepted(self, db_session):
        _set_state(db_session, budget_paused_until=None)
        db_session.flush()

    def test_a_deadline_without_the_attempt_is_rejected(self, db_session):
        with rejected_by(db_session, _TRIPLE):
            _set_state(db_session, budget_paused_until=_now(), budget_pause_source="x")

    def test_a_deadline_without_the_source_is_rejected(self, db_session, factories):
        attempt = _attempt(db_session, factories, outcome="budget_refused")
        with rejected_by(db_session, _TRIPLE):
            _set_state(
                db_session, budget_paused_until=_now(), budget_pause_attempt_id=attempt.id
            )

    def test_an_attempt_without_the_deadline_is_rejected(self, db_session, factories):
        attempt = _attempt(db_session, factories, outcome="budget_refused")
        with rejected_by(db_session, _TRIPLE):
            _set_state(
                db_session, budget_pause_attempt_id=attempt.id, budget_pause_source="x"
            )

    def test_a_source_alone_is_rejected(self, db_session):
        with rejected_by(db_session, _TRIPLE):
            _set_state(db_session, budget_pause_source="x")


class TestSumsCheck:
    def test_sums_with_a_deadline_are_accepted(self, db_session, factories):
        _paused(
            db_session, factories,
            budget_limit_usd=Decimal("5"), budget_spend_usd=Decimal("4.99"),
        )

    def test_a_deadline_without_sums_is_accepted(self, db_session, factories):
        _paused(db_session, factories)

    def test_a_limit_without_a_deadline_is_rejected(self, db_session):
        with rejected_by(db_session, _SUMS):
            _set_state(db_session, budget_limit_usd=Decimal("5"))

    def test_a_spend_without_a_deadline_is_rejected(self, db_session):
        with rejected_by(db_session, _SUMS):
            _set_state(db_session, budget_spend_usd=Decimal("1"))


# ---------------------------------------------------------------------------
#  upgrade / downgrade на scratch-базе
# ---------------------------------------------------------------------------

def _state_columns(conn) -> set:
    return {
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'semantic_worker_state' AND column_name LIKE 'budget\\_%'"
            )
        )
    }


def _constraint_defs(conn) -> dict:
    return dict(
        conn.execute(
            sa.text(
                "SELECT c.conrelid::regclass::text || '.' || c.conname, "
                "pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = 'public'"
            )
        ).all()
    )


def _columns(conn) -> set:
    return {
        tuple(r)
        for r in conn.execute(
            sa.text(
                "SELECT table_name, column_name, is_nullable, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
    }


def _revision(conn) -> str:
    return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def _snapshot(conn):
    return _constraint_defs(conn), _columns(conn)


def _seed_attempt(conn, outcome: str | None) -> int:
    """Попытка с настоящей цепочкой родителей; исход идёт прямо в INSERT, чтобы CHECK
    исхода проверялся самой вставкой."""
    position_id = conn.execute(
        sa.text(
            "INSERT INTO catalog_positions "
            "(standard_job_title, normalized_job_title, kind, status) "
            "VALUES ('Работа (scratch)', 'работа (scratch)', 'POSITION', 'na') RETURNING id"
        )
    ).scalar_one()
    bucket_id = conn.execute(
        sa.text("INSERT INTO context_buckets (catalog_position_id) VALUES (:cp) RETURNING id"),
        {"cp": position_id},
    ).scalar_one()
    context_id = conn.execute(
        sa.text(
            "INSERT INTO catalog_contexts "
            "(bucket_id, semantic_kind, semantic_kind_source, semantic_kind_at, "
            " name_role, name_role_source, name_role_at, place_dictionary_version, "
            " semantic_state) "
            "VALUES (:b, 'WORK', 'rule', now(), 'WORK', 'rule', now(), 1, 'SUGGESTED') "
            "RETURNING id"
        ),
        {"b": bucket_id},
    ).scalar_one()
    job_id = conn.execute(
        sa.text(
            "INSERT INTO semantic_jobs (kind, context_id, request_hash, status, prompt_version, "
            "model_requested, place_dictionary_version, candidates_hash, prefix_hash, "
            "input_hash, response_schema_version, serialization_version) "
            "VALUES ('family_suggestion', :c, 'h', 'pending', '1', 'm', 1, 'c', 'p', 'i', '1', '1') RETURNING id"
        ),
        {"c": context_id},
    ).scalar_one()
    return conn.execute(
        sa.text(
            "INSERT INTO semantic_job_attempts "
            "(job_id, claim_token, retry_generation, started_at, reserve_usd, prefix_hash, "
            " privacy_dictionary_hash, outcome) "
            "VALUES (:j, :t, 0, now(), 0.1, 'p', 'd', :o) RETURNING id"
        ),
        {"j": job_id, "t": str(uuid.uuid4()), "o": outcome},
    ).scalar_one()


def _outcome_gate_rejects(engine) -> str:
    """Имя нарушенного ограничения при вставке попытки `budget_refused`."""
    with pytest.raises(sa.exc.IntegrityError) as exc, engine.begin() as conn:
        _seed_attempt(conn, "budget_refused")
    return exc.value.orig.diag.constraint_name


@pytest.mark.migration_roundtrip
class TestUpgrade:
    def test_upgrade_adds_the_fields_and_both_checks(self):
        with _scratch_alembic("0026 upgrade") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0025")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    assert _state_columns(conn) == set()
                    before = _constraint_defs(conn)
                command.upgrade(cfg, "0026")
                with engine.connect() as conn:
                    assert _revision(conn) == "0026"
                    assert _state_columns(conn) == set(_STATE_COLUMNS)
                    after = _constraint_defs(conn)
                added = set(after) - set(before)
                assert f"semantic_worker_state.{_TRIPLE}" in added
                assert f"semantic_worker_state.{_SUMS}" in added
                key = f"semantic_job_attempts.{_OUTCOME_CHECK}"
                assert "budget_refused" in after[key]
                assert "budget_refused" not in before[key]
            finally:
                engine.dispose()

    def test_budget_refused_is_rejected_before_the_upgrade_and_accepted_after(self):
        with _scratch_alembic("0026 outcome gate") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0025")
            engine = sa.create_engine(scratch_url)
            try:
                assert _outcome_gate_rejects(engine) == _OUTCOME_CHECK
                command.upgrade(cfg, "0026")
                with engine.begin() as conn:
                    assert _seed_attempt(conn, "budget_refused")
            finally:
                engine.dispose()


@pytest.mark.migration_roundtrip
class TestDowngrade:
    def test_downgrade_on_an_empty_database_restores_the_old_schema_exactly(self):
        with _scratch_alembic("0026 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0025")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before = _snapshot(conn)
                command.upgrade(cfg, "0026")
                command.downgrade(cfg, "0025")
                with engine.connect() as conn:
                    assert _revision(conn) == "0025"
                    assert _state_columns(conn) == set()
                    assert _snapshot(conn) == before
                # прежний CHECK исхода вернулся: budget_refused снова не проходит
                assert _outcome_gate_rejects(engine) == _OUTCOME_CHECK
            finally:
                engine.dispose()

    def test_downgrade_is_refused_by_a_budget_refused_attempt(self):
        with _scratch_alembic("0026 refusal attempt") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0026")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    _seed_attempt(conn, "budget_refused")
                    before = _snapshot(conn)
                with pytest.raises(RuntimeError, match="0026") as exc:
                    command.downgrade(cfg, "0025")
                assert "budget_refused_attempts — 1" in str(exc.value)
                with engine.connect() as conn:
                    assert _revision(conn) == "0026"
                    assert _state_columns(conn) == set(_STATE_COLUMNS)
                    assert _snapshot(conn) == before
            finally:
                engine.dispose()

    def test_downgrade_is_refused_by_a_standing_pause(self):
        with _scratch_alembic("0026 refusal pause") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0026")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    attempt_id = _seed_attempt(conn, "transient_error")
                    conn.execute(
                        sa.text(
                            "UPDATE semantic_worker_state SET budget_paused_until = now(), "
                            "budget_pause_attempt_id = :a, budget_pause_source = 'x' "
                            "WHERE id = 1"
                        ),
                        {"a": attempt_id},
                    )
                    before = _snapshot(conn)
                with pytest.raises(RuntimeError, match="0026") as exc:
                    command.downgrade(cfg, "0025")
                assert "budget_pause_standing — 1" in str(exc.value)
                with engine.connect() as conn:
                    assert _revision(conn) == "0026"
                    assert _state_columns(conn) == set(_STATE_COLUMNS)
                    assert _snapshot(conn) == before
            finally:
                engine.dispose()

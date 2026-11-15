"""Отказ провайдера по деньгам: запись, пауза, остановка, статус (задача 3 фичи
«пауза до сброса лимита провайдера»).

Спека: `docs/superpowers/specs/2026-11-15-provider-budget-pause-design.md`
§2.2 (запись отказа), §2.3 (пауза и остановка), §2.4 (статус), проверки §3 п. 4–15.
Время задаётся явно; от реальных часов зависит только `queue_status`, поэтому у
него срок паузы либо далеко в будущем, либо давно в прошлом.
"""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import crud.semantic_queue as crud_semantic_queue
import services.semantic_worker as worker
from config import settings as app_settings
from crud.semantic_queue import queue_status
from models import SemanticJob, SemanticJobAttempt
from services.semantic_client import BudgetAction, ProviderBudgetError
from services.semantic_cost import spent_last_24h
from services.semantic_decisions import resume_worker
from services.semantic_reconcile import (
    open_suggestion_holds,
    schema_build_wait,
    schema_ready_to_build,
)
from services.semantic_worker import claim_next, process_one, record_failure
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_scope import world  # noqa: F401 — фикстура
from tests.integration.test_catalog_discovery_worker import (
    NOW as DISCOVERY_NOW,
)
from tests.integration.test_catalog_discovery_worker import (
    _attempt,
    _chunk_answers,
    _chunked,
)
from tests.integration.test_catalog_discovery_worker import _job as _discovery_job
from tests.integration.test_semantic_queue_worker import (
    NOW,
    S,
    _answer,
    _attempts,
    _claim_one,
    _committed_scene,
    _FakeClient,
    _pause,
    _reload,
    _response,
    _scene,
    _state,
)
from tests.integration.test_simplify_auto_split import _all_contexts
from tests.integration.test_simplify_auto_split import _job as _split_job
from tests.integration.test_simplify_auto_split import _scene as _auto_split_scene
from tests.integration.test_work_variants_material import _unit_id
from tests.integration.test_work_variants_reconcile import (
    _SUGGESTION,
    _VALUES,
    _active_family,
    _context,
    _fp,
    _held,
)
from tests.integration.test_work_variants_reconcile import _job as _reconcile_job
from tests.integration.test_work_variants_worker import NOW as SPLIT_NOW
from tests.integration.test_work_variants_worker import S as SPLIT_S

pytestmark = pytest.mark.integration

RESETS = NOW + dt.timedelta(hours=1)
FAR_FUTURE = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
_PAUSE_FIELDS = (
    "budget_paused_until",
    "budget_pause_attempt_id",
    "budget_pause_source",
    "budget_limit_usd",
    "budget_spend_usd",
)
STOP_REASONS = ("provider_credits", "provider_budget_no_reset", "provider_refusal_unknown")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _pause_error(
    resets_at=RESETS, *, retry_after_s=None, source="key_limit",
    limit=Decimal("5"), spend=Decimal("5"),
) -> ProviderBudgetError:
    return ProviderBudgetError(
        "лимит провайдера", action=BudgetAction.pause, error_class="budget_key_limit",
        resets_at=resets_at, retry_after_s=retry_after_s, limit_source=source,
        limit_usd=limit, spend_usd=spend,
    )


def _stop_error(reason="provider_credits") -> ProviderBudgetError:
    return ProviderBudgetError(
        "нет денег", action=BudgetAction.stop, error_class="budget_credits", stop_reason=reason,
    )


def _pause_fields(db) -> tuple:
    state = _state(db)
    return tuple(getattr(state, name) for name in _PAUSE_FIELDS)


def _fail(db, claim, error, *, now=NOW, completed=()):
    record_failure(db, claim, error, now=now, settings=S, completed=completed)


def _two_claims(db, factories):
    scene = _scene(db, factories, titles=("Устройство пола", "Устройство потолка"))
    first = _claim_one(db)
    second = _claim_one(db)
    assert first.job_id != second.job_id
    return scene, first, second


def _take_over(db, job_id):
    other = uuid.uuid4()
    db.execute(
        sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
        {"t": other, "id": job_id},
    )
    return other


# ---------------------------------------------------------------------------
#  Запись отказа (§2.2)
# ---------------------------------------------------------------------------

class TestProviderBudgetRecord:
    def test_the_job_returns_to_pending_untouched_and_the_attempt_is_budget_refused(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        assert _reload(db_session, claim.job_id).attempts_in_generation == 1

        _fail(db_session, claim, _pause_error())

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.attempts_in_generation) == ("pending", None, 0)
        assert job.next_attempt_at == NOW
        assert job.last_error_class == "budget_key_limit"
        assert job.retry_generation == 0
        (attempt,) = _attempts(db_session, scene.jobs[0].id)
        assert attempt.outcome == "budget_refused"
        assert attempt.finished_at == NOW
        assert attempt.cost_usd == Decimal(0)
        assert (attempt.error_class, attempt.error_text) == ("budget_key_limit", "лимит провайдера")

    def test_only_the_claim_increment_is_taken_back_and_the_generation_is_kept(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text(
                "UPDATE semantic_jobs SET attempts_in_generation = 2, retry_generation = 3 "
                "WHERE id = :id"
            ),
            {"id": claim.job_id},
        )

        _fail(db_session, claim, _pause_error())

        job = _reload(db_session, claim.job_id)
        assert (job.attempts_in_generation, job.retry_generation) == (1, 3)

    def test_the_attempt_counter_never_goes_below_zero(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET attempts_in_generation = 0 WHERE id = :id"),
            {"id": claim.job_id},
        )

        _fail(db_session, claim, _pause_error())

        assert _reload(db_session, claim.job_id).attempts_in_generation == 0

    def test_a_job_on_its_last_attempt_is_not_parked_in_error(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET attempts_in_generation = :n WHERE id = :id"),
            {"n": S.SEMANTIC_MAX_ATTEMPTS, "id": claim.job_id},
        )

        _fail(db_session, claim, _pause_error())

        job = _reload(db_session, claim.job_id)
        assert job.status == "pending"
        assert job.attempts_in_generation == S.SEMANTIC_MAX_ATTEMPTS - 1

    def test_a_stop_refusal_also_returns_the_job_to_pending(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _stop_error("provider_credits"))

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.attempts_in_generation) == ("pending", None, 0)
        assert _attempts(db_session, claim.job_id)[0].outcome == "budget_refused"

    def test_a_lost_claim_is_recorded_as_lost_claim_and_the_pause_still_stands(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        other = _take_over(db_session, claim.job_id)

        _fail(db_session, claim, _pause_error())

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.attempts_in_generation) == ("running", other, 1)
        (attempt,) = _attempts(db_session, claim.job_id)
        assert (attempt.outcome, attempt.cost_usd) == ("lost_claim", Decimal(0))
        assert attempt.error_class == "budget_key_limit"
        assert _pause_fields(db_session)[0] == RESETS

    def test_a_closed_attempt_is_not_rewritten(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error(), now=NOW)
        later = NOW + dt.timedelta(minutes=5)

        _fail(db_session, claim, _pause_error(later + dt.timedelta(hours=5)), now=later)

        assert _pause_fields(db_session)[0] == RESETS
        assert _attempts(db_session, claim.job_id)[0].finished_at == NOW

    def test_completed_portions_of_an_ordinary_job_sum_their_costs(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd
        done = [_response("a", cost=reserve / 4), _response("b", cost=reserve / 8)]

        _fail(db_session, claim, _pause_error(), completed=done)

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.cost_usd == reserve / 4 + reserve / 8
        assert (attempt.prompt_tokens, attempt.completion_tokens) == (2 * 8274, 2 * 156)
        assert attempt.reserve_exceeded is False

    def test_an_unknown_cost_of_a_portion_makes_the_attempt_cost_null(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        done = [_response("a", cost=Decimal("0.001")), _response("b", cost=None)]

        _fail(db_session, claim, _pause_error(), completed=done)

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.cost_usd is None
        assert attempt.reserve_exceeded is False
        assert spent_last_24h(db_session, now=NOW) == attempt.reserve_usd


# ---------------------------------------------------------------------------
#  Пауза по времени (§2.3)
# ---------------------------------------------------------------------------

class TestProviderBudgetPauseTerm:
    def test_the_pause_records_the_term_attempt_source_and_sums(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(source="key_limit", limit=Decimal("5"),
                                              spend=Decimal("4.9999")))

        assert _pause_fields(db_session) == (
            RESETS, claim.attempt_id, "key_limit", Decimal("5"), Decimal("4.9999"),
        )
        assert _state(db_session).claim_paused is False

    def test_a_pause_without_sums_keeps_them_null(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(limit=None, spend=None))

        until, attempt_id, source, limit, spend = _pause_fields(db_session)
        assert (until, attempt_id, source) == (RESETS, claim.attempt_id, "key_limit")
        assert (limit, spend) == (None, None)

    def test_a_reset_time_in_the_past_gives_the_floor_of_sixty_seconds(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(NOW - dt.timedelta(minutes=10)))

        assert dt.timedelta(seconds=60) == worker.BUDGET_PAUSE_FLOOR
        assert _pause_fields(db_session)[0] == NOW + dt.timedelta(seconds=60)

    def test_a_short_retry_after_gives_the_floor_too(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(None, retry_after_s=30))

        assert _pause_fields(db_session)[0] == NOW + dt.timedelta(seconds=60)

    def test_a_long_retry_after_is_kept_as_is(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(None, retry_after_s=300))

        assert _pause_fields(db_session)[0] == NOW + dt.timedelta(seconds=300)

    def test_an_earlier_second_term_does_not_shorten_the_first(self, db_session, factories):
        _scene_, first, second = _two_claims(db_session, factories)
        _fail(db_session, first, _pause_error(RESETS, source="key_limit",
                                              limit=Decimal("5"), spend=Decimal("5")))

        _fail(db_session, second, _pause_error(RESETS - dt.timedelta(minutes=30), source="other",
                                               limit=Decimal("9"), spend=Decimal("8")))

        assert _pause_fields(db_session) == (
            RESETS, first.attempt_id, "key_limit", Decimal("5"), Decimal("5"),
        )

    def test_an_equal_second_term_keeps_the_first_pause_whole(self, db_session, factories):
        _scene_, first, second = _two_claims(db_session, factories)
        _fail(db_session, first, _pause_error(RESETS, source="key_limit",
                                              limit=Decimal("5"), spend=Decimal("5")))

        _fail(db_session, second, _pause_error(RESETS, source="other",
                                               limit=Decimal("9"), spend=Decimal("8")))

        assert _pause_fields(db_session) == (
            RESETS, first.attempt_id, "key_limit", Decimal("5"), Decimal("5"),
        )

    def test_a_later_second_term_extends_the_pause_with_its_attempt_and_sums(
        self, db_session, factories
    ):
        _scene_, first, second = _two_claims(db_session, factories)
        _fail(db_session, first, _pause_error(RESETS, source="key_limit",
                                              limit=Decimal("5"), spend=Decimal("5")))
        later = RESETS + dt.timedelta(hours=2)

        _fail(db_session, second, _pause_error(later, source="other",
                                               limit=Decimal("9"), spend=None))

        assert _pause_fields(db_session) == (later, second.attempt_id, "other", Decimal("9"), None)


# ---------------------------------------------------------------------------
#  Остановка до человека (§2.3)
# ---------------------------------------------------------------------------

class TestProviderBudgetStop:
    @pytest.mark.parametrize("reason", STOP_REASONS)
    def test_each_stop_reason_reaches_the_worker_state(self, db_session, factories, reason):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _stop_error(reason))

        state = _state(db_session)
        assert (state.claim_paused, state.paused_reason) == (True, reason)
        assert (state.paused_attempt_id, state.paused_at) == (claim.attempt_id, NOW)
        assert _pause_fields(db_session) == (None,) * 5

    def test_the_constants_equal_the_client_stop_reasons(self):
        assert (
            worker.PAUSE_REASON_PROVIDER_CREDITS,
            worker.PAUSE_REASON_PROVIDER_BUDGET_NO_RESET,
            worker.PAUSE_REASON_PROVIDER_REFUSAL_UNKNOWN,
        ) == STOP_REASONS

    def test_a_stop_clears_a_standing_time_pause(self, db_session, factories):
        _scene_, first, second = _two_claims(db_session, factories)
        _fail(db_session, first, _pause_error())
        assert _pause_fields(db_session)[0] == RESETS

        _fail(db_session, second, _stop_error("provider_credits"))

        state = _state(db_session)
        assert (state.claim_paused, state.paused_reason) == (True, "provider_credits")
        assert state.paused_attempt_id == second.attempt_id
        assert _pause_fields(db_session) == (None,) * 5

    def test_a_pause_is_not_written_while_a_stop_stands(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        _pause(db_session, scene.context_ids[0])

        _fail(db_session, claim, _pause_error())

        state = _state(db_session)
        assert (state.claim_paused, state.paused_reason) == (True, "manual")
        assert _pause_fields(db_session) == (None,) * 5

    def test_the_first_stop_stays_when_a_second_one_arrives(self, db_session, factories):
        _scene_, first, second = _two_claims(db_session, factories)
        _fail(db_session, first, _stop_error("provider_credits"))

        _fail(db_session, second, _stop_error("provider_refusal_unknown"),
              now=NOW + dt.timedelta(days=1))

        state = _state(db_session)
        assert (state.paused_reason, state.paused_attempt_id, state.paused_at) == (
            "provider_credits", first.attempt_id, NOW,
        )

    def test_resume_after_provider_credits_lets_the_claim_through(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _stop_error("provider_credits"))
        assert claim_next(db_session, settings=S, now=NOW) is None

        resume_worker(db_session, actor_id=scene.user.id)

        again = claim_next(db_session, settings=S, now=NOW)
        assert again is not None and again.job_id == claim.job_id


# ---------------------------------------------------------------------------
#  Захват при паузе (§2.3)
# ---------------------------------------------------------------------------

class TestProviderBudgetClaim:
    def test_the_claim_before_the_term_is_none_and_the_job_is_untouched(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error())

        assert claim_next(db_session, settings=S, now=RESETS - dt.timedelta(seconds=1)) is None

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.attempts_in_generation) == ("pending", None, 0)
        assert len(_attempts(db_session, claim.job_id)) == 1
        assert _pause_fields(db_session)[0] == RESETS

    def test_the_claim_at_the_term_takes_the_job_and_clears_the_five_fields(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error())

        again = claim_next(db_session, settings=S, now=RESETS)

        assert again is not None and again.job_id == claim.job_id
        assert _pause_fields(db_session) == (None,) * 5
        assert _reload(db_session, claim.job_id).status == "running"

    def test_an_expired_pause_is_cleared_even_when_there_is_nothing_to_claim(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error())
        db_session.execute(
            sa.update(SemanticJob).values(status="cancelled", cancel_reason="not_applicable")
        )

        assert claim_next(db_session, settings=S, now=RESETS) is None

        assert _pause_fields(db_session) == (None,) * 5


# ---------------------------------------------------------------------------
#  Предохранитель на пути отказа (§2.2)
# ---------------------------------------------------------------------------

class TestProviderBudgetFuse:
    def _over_reserve(self, db, claim):
        reserve = _attempts(db, claim.job_id)[0].reserve_usd
        return [_response("a", cost=reserve * 2)]

    def test_cost_above_the_reserve_stops_the_claim_instead_of_pausing(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error(), completed=self._over_reserve(db_session, claim))

        (attempt,) = _attempts(db_session, claim.job_id)
        assert (attempt.outcome, attempt.reserve_exceeded) == ("budget_refused", True)
        state = _state(db_session)
        assert (state.claim_paused, state.paused_reason, state.paused_attempt_id) == (
            True, "reserve_exceeded", claim.attempt_id,
        )
        assert _pause_fields(db_session) == (None,) * 5
        assert claim_next(db_session, settings=S, now=RESETS + dt.timedelta(hours=1)) is None
        assert _reload(db_session, claim.job_id).status == "pending"

    def test_the_same_for_a_lost_claim(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _take_over(db_session, claim.job_id)

        _fail(db_session, claim, _pause_error(), completed=self._over_reserve(db_session, claim))

        (attempt,) = _attempts(db_session, claim.job_id)
        assert (attempt.outcome, attempt.reserve_exceeded) == ("lost_claim", True)
        state = _state(db_session)
        assert (state.claim_paused, state.paused_reason) == (True, "reserve_exceeded")
        assert _pause_fields(db_session) == (None,) * 5
        assert claim_next(db_session, settings=S, now=RESETS + dt.timedelta(hours=1)) is None

    def test_a_stop_refusal_does_not_replace_the_fuse_stop(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _stop_error("provider_credits"),
              completed=self._over_reserve(db_session, claim))

        state = _state(db_session)
        assert (state.paused_reason, state.paused_attempt_id) == (
            "reserve_exceeded", claim.attempt_id,
        )

    def test_cost_equal_to_the_reserve_leaves_the_pause_in_place(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd

        _fail(db_session, claim, _pause_error(), completed=[_response("a", cost=reserve)])

        assert _attempts(db_session, claim.job_id)[0].reserve_exceeded is False
        assert _state(db_session).claim_paused is False
        assert _pause_fields(db_session)[0] == RESETS

    def test_zero_cost_does_not_trip_the_fuse(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, _pause_error())

        assert _attempts(db_session, claim.job_id)[0].reserve_exceeded is False
        assert _state(db_session).claim_paused is False


# ---------------------------------------------------------------------------
#  Открытие семей: порции (проверки 5 и 6)
# ---------------------------------------------------------------------------

class TestProviderBudgetDiscovery:
    def _script(self, world, costs):
        first, second, _third = _chunk_answers(seed_category_id(world.db))
        done = [
            dataclasses.replace(first, cost_usd=costs[0]),
            dataclasses.replace(second, cost_usd=costs[1]),
        ]
        return [*done, _pause_error()]

    def test_two_done_portions_and_a_refused_third_cost_their_sum(self, world):
        script = self._script(world, (Decimal("0.0010"), Decimal("0.0020")))

        job, _claim_, client, _ = _chunked(world, script)

        attempt = _attempt(world.db, job.id)
        assert attempt.outcome == "budget_refused"
        assert attempt.cost_usd == Decimal("0.0030")
        assert attempt.reserve_exceeded is False
        # Резерв строго больше суммы: иначе «стоимость = резерв» совпала бы с ожидаемой.
        assert attempt.reserve_usd > Decimal("0.0030")
        assert len(client.bodies) == 3
        assert spent_last_24h(world.db, now=DISCOVERY_NOW) == Decimal("0.0030")
        row = _discovery_job(world.db, job.id)
        assert (row.status, row.claim_token, row.attempts_in_generation) == ("pending", None, 0)

    def test_an_unknown_cost_of_one_portion_makes_the_cost_null_and_the_spend_the_reserve(
        self, world
    ):
        script = self._script(world, (Decimal("0.0010"), None))

        job, _claim_, _client, _ = _chunked(world, script)

        attempt = _attempt(world.db, job.id)
        assert (attempt.outcome, attempt.cost_usd) == ("budget_refused", None)
        assert spent_last_24h(world.db, now=DISCOVERY_NOW) == attempt.reserve_usd

    def test_a_refusal_of_the_first_portion_costs_zero(self, world):
        job, _claim_, client, _ = _chunked(world, [_pause_error()])

        attempt = _attempt(world.db, job.id)
        assert (attempt.outcome, attempt.cost_usd) == ("budget_refused", Decimal(0))
        assert len(client.bodies) == 1
        assert _discovery_job(world.db, job.id).status == "pending"


# ---------------------------------------------------------------------------
#  Автоделение (проверки 7 и 8а)
# ---------------------------------------------------------------------------

class _SplitClient:
    """Первая группа отвечает значением со стоимостью, вторая — отказ по деньгам."""

    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    def complete(self, body, *, timeout_s):
        self.calls += 1
        if self.calls == 2:
            raise _pause_error(SPLIT_NOW + dt.timedelta(hours=1))
        content = (
            '{"values": [{"ordinal": 1, "kind": "value", "value": "стены", "source": "path"}]}'
        )
        return _response(content, cost=self.cost)


def _split_attempt(db, job_id) -> SemanticJobAttempt:
    db.expire_all()
    return db.execute(
        sa.select(SemanticJobAttempt).where(SemanticJobAttempt.job_id == job_id)
    ).scalar_one()


class TestProviderBudgetAutoSplit:
    def test_a_refusal_on_the_second_group_keeps_the_job_and_the_cost_of_the_first(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _auto_split_scene(committing_db, committing_factories)
        committing_db.commit()
        cost = Decimal("0.0001")
        client = _SplitClient(cost)

        assert process_one(
            committing_session_factory, client, settings=SPLIT_S, clock=lambda: SPLIT_NOW
        ) is True

        assert client.calls == 2
        job = _split_job(committing_db, scene)
        assert (job.status, job.claim_token, job.attempts_in_generation) == ("pending", None, 0)
        attempt = _split_attempt(committing_db, job.id)
        assert attempt.outcome == "budget_refused"
        assert attempt.cost_usd == cost
        assert attempt.reserve_exceeded is False
        # Резерв строго больше: иначе «стоимость = резерв» совпала бы с ожидаемой.
        assert attempt.reserve_usd > cost
        assert len(_all_contexts(committing_db, scene)) == 1
        assert _state(committing_db).claim_paused is False
        assert _pause_fields(committing_db)[0] == SPLIT_NOW + dt.timedelta(hours=1)

    def test_a_cost_above_the_reserve_before_the_refusal_stops_the_claim(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _auto_split_scene(committing_db, committing_factories)
        committing_db.commit()

        process_one(
            committing_session_factory, _SplitClient(Decimal("5")), settings=SPLIT_S,
            clock=lambda: SPLIT_NOW,
        )

        job = _split_job(committing_db, scene)
        assert job.status == "pending"
        attempt = _split_attempt(committing_db, job.id)
        assert (attempt.outcome, attempt.reserve_exceeded) == ("budget_refused", True)
        state = _state(committing_db)
        assert (state.claim_paused, state.paused_reason) == (True, "reserve_exceeded")
        assert _pause_fields(committing_db) == (None,) * 5
        later = SPLIT_NOW + dt.timedelta(hours=2)
        assert process_one(
            committing_session_factory, _SplitClient(Decimal("0")), settings=SPLIT_S,
            clock=lambda: later,
        ) is False


# ---------------------------------------------------------------------------
#  Сквозной проход (проверка 14)
# ---------------------------------------------------------------------------

class TestProviderBudgetEndToEnd:
    def test_one_refusal_pauses_the_queue_and_after_the_term_all_three_jobs_run(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(
            committing_db, committing_factories,
            titles=("Устройство пола", "Устройство потолка", "Устройство стен"),
        )
        ids = [job.id for job in scene.jobs]
        client = _FakeClient(_pause_error(RESETS))

        assert process_one(committing_session_factory, client, settings=S, clock=lambda: NOW) is True

        assert len(client.calls) == 1
        for job_id in ids:
            job = _reload(committing_db, job_id)
            assert (job.status, job.claim_token, job.attempts_in_generation) == ("pending", None, 0)
        refused = [job_id for job_id in ids if _attempts(committing_db, job_id)]
        assert len(refused) == 1
        assert _attempts(committing_db, refused[0])[0].outcome == "budget_refused"

        before_term = RESETS - dt.timedelta(seconds=1)
        for _ in range(2):
            assert process_one(
                committing_session_factory, client, settings=S, clock=lambda: before_term
            ) is False
        assert len(client.calls) == 1
        assert sum(bool(_attempts(committing_db, job_id)) for job_id in ids) == 1
        assert all(_reload(committing_db, j).attempts_in_generation == 0 for j in ids)

        client.script.extend(_response(_answer(scene.family.id)) for _ in range(3))
        for _ in range(3):
            assert process_one(
                committing_session_factory, client, settings=S, clock=lambda: RESETS
            ) is True
        assert process_one(
            committing_session_factory, client, settings=S, clock=lambda: RESETS
        ) is False

        assert [_reload(committing_db, job_id).status for job_id in ids] == ["done"] * 3
        assert _pause_fields(committing_db) == (None,) * 5


# ---------------------------------------------------------------------------
#  Статус (проверка 15)
# ---------------------------------------------------------------------------

class TestProviderBudgetStatus:
    def test_the_status_carries_the_pause_with_the_term_and_sums_as_strings(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error(FAR_FUTURE, source="key_limit",
                                              limit=Decimal("5.0001"), spend=Decimal("4.5")))

        status = queue_status(db_session)

        pause = status["budget_pause"]
        assert pause is not None
        # Пояс строки зависит от сессии базы: сравнивается момент, не запись.
        assert dt.datetime.fromisoformat(pause["until"]) == FAR_FUTURE
        assert {k: v for k, v in pause.items() if k != "until"} == {
            "source": "key_limit",
            "limit_usd": "5.0001",
            "spend_usd": "4.5",
            "attempt_id": claim.attempt_id,
        }
        assert status["claim_paused"] is None

    def test_missing_sums_are_none(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error(FAR_FUTURE, limit=None, spend=None))

        pause = queue_status(db_session)["budget_pause"]

        assert pause is not None
        assert (pause["limit_usd"], pause["spend_usd"]) == (None, None)

    def test_the_status_is_null_after_the_term(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _pause_error(NOW + dt.timedelta(hours=1)))
        assert _pause_fields(db_session)[0] is not None

        assert queue_status(db_session)["budget_pause"] is None

    def test_a_pause_reaches_the_route_while_the_heavy_parts_are_cached(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """§2.4: поле читается вживую, мимо кэша тяжёлых частей. Кэш включён, второй
        запрос в пределах срока его не пересчитывает, а пауза, записанная опросчиком в
        обход HTTP (сброса кэша нет), в ответе уже есть."""
        monkeypatch.setattr(app_settings, "SEMANTIC_STATUS_CACHE_TTL_S", 300)
        real = crud_semantic_queue.heavy_status_parts
        heavy_calls: list[int] = []

        def counted(db):
            heavy_calls.append(1)
            return real(db)

        monkeypatch.setattr(crud_semantic_queue, "heavy_status_parts", counted)
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        first = admin_client.get("/api/v1/semantic/status")
        _fail(db_session, claim, _pause_error(FAR_FUTURE))
        db_session.expire_all()
        second = admin_client.get("/api/v1/semantic/status")

        assert first.status_code == second.status_code == 200
        assert len(heavy_calls) == 1
        assert first.json()["budget_pause"] is None
        pause = second.json()["budget_pause"]
        assert pause is not None and pause["attempt_id"] == claim.attempt_id
        assert dt.datetime.fromisoformat(pause["until"]) == FAR_FUTURE

    def test_the_status_is_null_without_a_pause(self, db_session, factories):
        _scene(db_session, factories)

        assert queue_status(db_session)["budget_pause"] is None

    @pytest.mark.parametrize("reason", STOP_REASONS)
    def test_a_stop_reason_arrives_in_claim_paused(self, db_session, factories, reason):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        _fail(db_session, claim, _stop_error(reason))

        status = queue_status(db_session)

        assert status["claim_paused"] is not None
        assert status["claim_paused"]["reason"] == reason
        assert status["claim_paused"]["attempt_id"] == claim.attempt_id
        assert status["budget_pause"] is None


# ---------------------------------------------------------------------------
#  Причина ожидания схемы (спека §2.7 п. 3, проверки 19, 19а)
# ---------------------------------------------------------------------------

class TestBuildWait:
    def _scene(self, db, factories):
        unit = _unit_id(db, "M2")
        other = _unit_id(db, "PCS")
        return SimpleNamespace(
            unit=unit, other=other, family=_active_family(db, unit_id=unit),
            ctx=_context(db, factories, unit_id=unit),
            other_ctx=_context(db, factories, unit_id=other),
        )

    def _agree(self, db, family_id):
        wait = schema_build_wait(db, family_id)
        assert schema_ready_to_build(db, family_id) is (wait is None)
        return wait

    def test_a_held_batch_of_the_unit_gives_its_number_source_and_job(self, db_session, factories):
        scene = self._scene(db_session, factories)
        job = factories.ImportJobFactory.create()
        batch = _held(db_session, factories, [_fp(_SUGGESTION, context_id=scene.ctx)])
        batch.import_job_id = job.id
        db_session.flush()

        wait = self._agree(db_session, scene.family.id)

        assert wait == {
            "held_batches": [
                {"batch_id": batch.id, "source": "import", "import_job_id": job.id},
            ],
            "live_jobs": 0,
        }

    def test_a_mass_batch_with_suggestions_is_in_every_unit(self, db_session, factories):
        scene = self._scene(db_session, factories)
        batch = _held(
            db_session, factories, [_fp(_SUGGESTION, context_id=scene.other_ctx)], source="mass",
        )

        wait = self._agree(db_session, scene.family.id)

        assert wait is not None
        assert [(b["batch_id"], b["source"], b["import_job_id"]) for b in wait["held_batches"]] == [
            (batch.id, "mass", None),
        ]

    def test_only_live_jobs_give_their_exact_count(self, db_session, factories):
        scene = self._scene(db_session, factories)
        for index, status in enumerate(("pending", "running", "pending")):
            _reconcile_job(
                db_session, kind=_SUGGESTION, request_hash=f"s{index}", status=status,
                context_id=scene.ctx, unit_id=scene.unit,
            )
        _reconcile_job(
            db_session, kind=_SUGGESTION, request_hash="e", status="error",
            context_id=scene.ctx, unit_id=scene.unit,
        )
        _reconcile_job(
            db_session, kind=_SUGGESTION, request_hash="o", status="pending",
            context_id=scene.other_ctx, unit_id=scene.other,
        )

        wait = self._agree(db_session, scene.family.id)

        assert wait == {"held_batches": [], "live_jobs": 3}

    def test_two_batches_are_listed_by_number_without_duplicates(self, db_session, factories):
        scene = self._scene(db_session, factories)
        mass = _held(
            db_session, factories, [_fp(_SUGGESTION, context_id=scene.other_ctx)], source="mass",
        )
        own = _held(db_session, factories, [_fp(_SUGGESTION, context_id=scene.ctx)])
        both = _held(
            db_session, factories,
            [_fp(_SUGGESTION, context_id=scene.ctx), _fp(_SUGGESTION, context_id=scene.other_ctx)],
            source="mass",
        )

        wait = self._agree(db_session, scene.family.id)

        assert wait is not None
        assert [b["batch_id"] for b in wait["held_batches"]] == sorted(
            [mass.id, own.id, both.id]
        )

    @pytest.mark.parametrize("source", ["import", "mass"])
    def test_a_values_only_batch_is_not_a_wait(self, db_session, factories, source):
        scene = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_VALUES, context_id=scene.ctx, schema_id=5)],
              source=source)
        assert self._agree(db_session, scene.family.id) is None

    @pytest.mark.parametrize("source", ["import", "mass"])
    def test_a_schema_only_batch_is_not_a_wait(self, db_session, factories, source):
        scene = self._scene(db_session, factories)
        _held(db_session, factories, [_fp("family_schema", family_id=scene.family.id)],
              source=source)
        assert self._agree(db_session, scene.family.id) is None

    def test_suggestions_of_another_unit_are_not_a_wait(self, db_session, factories):
        scene = self._scene(db_session, factories)
        _held(db_session, factories, [_fp(_SUGGESTION, context_id=scene.other_ctx)])
        assert self._agree(db_session, scene.family.id) is None

    def test_a_mixed_batch_of_other_unit_suggestions_and_own_values_is_not_a_wait(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)
        _held(
            db_session, factories,
            [_fp(_SUGGESTION, context_id=scene.other_ctx),
             _fp(_VALUES, context_id=scene.ctx, schema_id=5)],
        )
        assert self._agree(db_session, scene.family.id) is None

    def test_an_unknown_family_has_no_wait_and_is_not_ready(self, db_session):
        assert schema_build_wait(db_session, 987654321) is None
        assert schema_ready_to_build(db_session, 987654321) is False

    def test_the_holds_keep_the_old_selection_rule(self, db_session, factories):
        scene = self._scene(db_session, factories)
        own = _held(db_session, factories, [_fp(_SUGGESTION, context_id=scene.ctx)])
        _reconcile_job(
            db_session, kind=_SUGGESTION, request_hash="s", status="pending",
            context_id=scene.other_ctx, unit_id=scene.other,
        )

        holds = open_suggestion_holds(db_session)

        assert holds.mass == ()
        assert holds.units == frozenset({scene.unit, scene.other})
        assert [h.batch_id for h in holds.by_unit[scene.unit]] == [own.id]
        assert dict(holds.live_jobs_by_unit) == {scene.other: 1}

    def test_the_route_carries_the_wait(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)
        batch = _held(db_session, factories, [_fp(_SUGGESTION, context_id=scene.ctx)])

        body = admin_client.get(f"/api/v1/semantic/families/{scene.family.id}/schema").json()

        assert body["ready_to_build"] is False
        assert body["build_wait"] == {
            "held_batches": [{"batch_id": batch.id, "source": "import", "import_job_id": None}],
            "live_jobs": 0,
        }

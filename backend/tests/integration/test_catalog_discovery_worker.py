"""Исполнение открытия семей: захват, приватность, повторы, обработка ответа
(спека 3б §2.3, «Исполнение»; DoD 6, 7).

Задание открытия идёт тем же захватом, повторами и восстановлением, что прочие
виды; отличается предмет (единица целиком) и запись результата: ответ
превращается в черновики одной транзакцией (`apply_discovery`)."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from crud.semantic_queue import list_jobs
from models import (
    CatalogContext,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    SemanticWorkerState,
)
from services.discovery_result import DiscoveryOutcome, apply_discovery
from services.family_discovery import (
    discovery_scope,
    launch_discovery,
    preview_discovery,
    sent_of,
)
from services.semantic_client import ModelResponse, PermanentModelError, TransientModelError
from services.semantic_decisions import (
    DecisionConflict,
    decline_privacy_hold,
    release_privacy_hold,
    retry_job,
)
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    _open_suggestion_state,
    _split_jobs,
    reconcile_semantic_jobs,
)
from services.semantic_runner import recover_semantic_jobs
from services.variant_answer import parse_discovery_answer
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _leaf_categories,
    _make,
    _make_system,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_semantic_queue_api import _active_family

pytestmark = pytest.mark.integration

#: Позже любого `now()` базы: задание, созданное в тесте, уже «созрело».
NOW = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.UTC)

S = app_settings.model_copy(
    update={
        "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
    }
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _response(content: str, *, cost: str | None = "0.01") -> ModelResponse:
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=8274, completion_tokens=156, cache_write_tokens=8058, cached_tokens=0,
        cost_usd=None if cost is None else Decimal(cost),
    )


def _group(names, *, family_id=None, title="Новая семья", definition="Определение",
           category_id=None, similar=None):
    if family_id is not None:
        title = definition = None
    return {
        "family_id": family_id, "title": title, "definition": definition,
        "category_id": category_id, "similar_family_id": similar, "names": names,
    }


def _answer(groups, not_work=(), family_categories=()):
    return json.dumps(
        {
            "groups": groups,
            "not_work": list(not_work),
            "family_categories": [
                {"family_id": f, "category_id": c} for f, c in family_categories
            ],
        },
        ensure_ascii=False,
    )


def _launch(db, unit_id, *, admin_id, settings=S) -> SemanticJob:
    preview = preview_discovery(db, unit_id=unit_id, settings=settings)
    return launch_discovery(
        db, unit_id=unit_id, preview_hash=preview.preview_hash, actor_id=admin_id,
        settings=settings,
    )


def _claim(db, *, settings=S):
    claim = worker.claim_next(db, settings=settings, now=NOW)
    assert claim is not None, "вход теста: задание должно быть захвачено"
    return claim


def _bare_world(w, titles=("Имя А", "Имя Б", "Имя В")):
    """Единица без активных семей и по контексту на каждое имя."""
    return [_ctx_bare_unit(w, title) for title in titles]


def _job(db, job_id) -> SemanticJob:
    db.expire_all()
    return db.get(SemanticJob, job_id)


def _drafts(db, job_id):
    return (
        db.execute(
            sa.select(FamilyDraft).where(FamilyDraft.job_id == job_id).order_by(FamilyDraft.ordinal)
        )
        .scalars()
        .all()
    )


def _members(db, draft_id) -> list[int]:
    return sorted(
        db.execute(
            sa.select(FamilyDraftMember.context_id).where(FamilyDraftMember.draft_id == draft_id)
        ).scalars()
    )


def _attempt(db, job_id) -> SemanticJobAttempt:
    return db.execute(
        sa.select(SemanticJobAttempt)
        .where(SemanticJobAttempt.job_id == job_id)
        .order_by(SemanticJobAttempt.id.desc())
    ).scalars().first()


# ---------------------------------------------------------------------------
#  Захват
# ---------------------------------------------------------------------------

class TestClaim:
    def test_claim_renders_the_unit_and_opens_an_attempt_with_a_reserve(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)

        claim = _claim(world.db)

        assert claim.kind.value == "family_discovery"
        assert claim.job_id == job.id and not claim.synthesized
        assert claim.rendered.request_hash == job.request_hash
        assert claim.sent == sent_of(discovery_scope(world.db, world.bare_unit))
        assert claim.candidates == ()
        job = _job(world.db, job.id)
        assert job.status == "running" and job.claim_token == claim.claim_token
        attempt = _attempt(world.db, job.id)
        assert attempt.reserve_usd > 0
        assert attempt.prefix_hash == job.prefix_hash

    def test_scope_changed_before_the_claim_cancels_the_job_as_input_changed(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        _ctx_bare_unit(world, "Имя Г — пришло после запуска")

        assert worker.claim_next(world.db, settings=S, now=NOW) is None

        job = _job(world.db, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert world.db.execute(sa.select(SemanticJobAttempt)).first() is None

    def test_emptied_scope_cancels_the_job_as_not_applicable(self, world):
        contexts = _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        for context_id in contexts:
            world.db.execute(
                sa.update(CatalogContext).where(CatalogContext.id == context_id).values(
                    archived_at=dt.datetime.now(dt.UTC)
                )
            )
        world.db.expire_all()

        assert worker.claim_next(world.db, settings=S, now=NOW) is None

        job = _job(world.db, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_categories_only_launch_is_renderable_and_claimed(self, world):
        _uncategorize(world.db, world.family.id)
        job = _launch(world.db, world.family_unit, admin_id=world.admin.id)

        claim = _claim(world.db)

        assert claim.job_id == job.id
        assert claim.sent.names_count == 0
        assert claim.sent.uncategorized_family_ids == frozenset({world.family.id})

    def test_budget_below_the_reserve_leaves_the_job_pending(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        tiny = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": Decimal("0.0001")})

        assert worker.claim_next(world.db, settings=tiny, now=NOW) is None

        assert _job(world.db, job.id).status == "pending"
        assert world.db.execute(sa.select(SemanticJobAttempt)).first() is None

    def test_reserve_uses_the_discovery_tariffs(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        pricey = S.model_copy(
            update={
                "SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M": Decimal("1000"),
                "SEMANTIC_DAILY_BUDGET_USD": Decimal("1000"),
            }
        )

        claim = worker.claim_next(world.db, settings=pricey, now=NOW)

        assert claim is not None
        reserve = _attempt(world.db, claim.job_id).reserve_usd
        # 32000 токенов ответа по 1000 $/млн — 32 $ одним ответом.
        assert reserve > Decimal("32")


# ---------------------------------------------------------------------------
#  Приватность
# ---------------------------------------------------------------------------

class TestPrivacy:
    def _held(self, w):
        w.factories.ContractorFactory.create(title="Ромашка")
        _bare_world(w, titles=("Кладка у Ромашка", "Имя Б"))
        job = _launch(w.db, w.bare_unit, admin_id=w.admin.id)
        assert worker.claim_next(w.db, settings=S, now=NOW) is None
        job = _job(w.db, job.id)
        assert job.status == "privacy_hold"
        return job

    def test_match_in_a_name_holds_the_job_with_the_context_place(self, world):
        job = self._held(world)

        assert [(m["text"], m["where"]) for m in job.privacy_matches] == [("ромашка", "context")]
        assert world.db.execute(sa.select(SemanticJobAttempt)).first() is None

    def test_release_sends_the_job_and_the_claim_follows(self, world):
        job = self._held(world)

        release_privacy_hold(
            world.db, job_id=job.id, shown_matches=job.privacy_matches, actor_id=world.admin.id
        )
        claim = _claim(world.db)

        assert claim.job_id == job.id

    def test_decline_cancels_the_job_as_privacy_declined(self, world):
        job = self._held(world)

        decline_privacy_hold(
            world.db, job_id=job.id, shown_matches=job.privacy_matches, actor_id=world.admin.id
        )

        job = _job(world.db, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "privacy_declined")

    def test_release_with_changed_input_is_a_job_changed_conflict(self, world):
        job = self._held(world)
        _ctx_bare_unit(world, "Имя Г — пришло после задержки")

        with pytest.raises(DecisionConflict) as raised:
            release_privacy_hold(
                world.db, job_id=job.id, shown_matches=job.privacy_matches,
                actor_id=world.admin.id,
            )

        assert raised.value.code == "job_changed"
        assert _job(world.db, job.id).status == "privacy_hold"


# ---------------------------------------------------------------------------
#  Ошибки, повторы, восстановление
# ---------------------------------------------------------------------------

class TestFailuresAndRecovery:
    def test_transient_failure_returns_the_job_to_pending_with_a_delay(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)

        worker.record_failure(
            world.db, claim, TransientModelError("сеть", error_class="timeout"), now=NOW,
            settings=S, rng=lambda: 0.0,
        )

        job = _job(world.db, claim.job_id)
        assert job.status == "pending" and job.next_attempt_at > NOW
        assert job.last_error_class == "timeout"

    def test_permanent_failure_is_an_error_and_retry_makes_it_pending_again(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        worker.record_failure(
            world.db, claim, PermanentModelError("отказ", error_class="refused"), now=NOW,
            settings=S,
        )
        assert _job(world.db, claim.job_id).status == "error"

        retry_job(world.db, job_id=claim.job_id, actor_id=world.admin.id)

        job = _job(world.db, claim.job_id)
        assert (job.status, job.retry_generation, job.attempts_in_generation) == ("pending", 1, 0)
        assert _claim(world.db).job_id == job.id

    def test_retry_of_a_pending_job_is_refused(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)

        with pytest.raises(DecisionConflict) as raised:
            retry_job(world.db, job_id=job.id, actor_id=world.admin.id)

        assert raised.value.code == "job_changed"

    def test_running_job_returns_to_pending_on_start(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)

        assert recover_semantic_jobs(world.db, now=NOW) == 1

        job = _job(world.db, claim.job_id)
        assert job.status == "pending" and job.claim_token is None
        assert _attempt(world.db, job.id).error_class == "interrupted"

    def test_schema_error_is_an_error_without_automatic_retry(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)

        worker.record_result(
            world.db, claim, _response("это не json"), now=NOW, settings=S
        )

        job = _job(world.db, claim.job_id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        attempt = _attempt(world.db, job.id)
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert worker.claim_next(world.db, settings=S, now=NOW) is None
        assert _drafts(world.db, job.id) == []

    def test_reference_violation_in_the_answer_is_a_schema_error(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([99], category_id=category)])), now=NOW, settings=S,
        )

        job = _job(world.db, claim.job_id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        assert _attempt(world.db, job.id).outcome == "schema_error"

    def test_fuse_pauses_the_claim_when_the_cost_exceeds_the_reserve(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1, 2, 3], category_id=category)]), cost="999"),
            now=NOW, settings=S,
        )

        world.db.expire_all()
        state = world.db.get(SemanticWorkerState, 1)
        assert state.claim_paused is True
        assert _attempt(world.db, claim.job_id).reserve_exceeded is True
        assert _job(world.db, claim.job_id).status == "done"


# ---------------------------------------------------------------------------
#  Обработка ответа
# ---------------------------------------------------------------------------

class TestApply:
    def test_answer_becomes_drafts_members_and_proposals(self, world):
        a, b, c = _bare_world(world, titles=("Имя А", "Имя Б", "Имя В"))
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1], category_id=category), _group([2], category_id=category)],
                              not_work=[3])),
            now=NOW, settings=S,
        )

        job = _job(world.db, claim.job_id)
        assert job.status == "done" and job.claim_token is None
        assert job.result_suggestion_id is None
        first, second, trash = _drafts(world.db, job.id)
        assert [(d.grp, d.status, d.ordinal) for d in (first, second, trash)] == [
            ("new", "open", 1), ("new", "open", 2), ("not_work", "open", 3),
        ]
        assert (first.title, first.definition, first.family_category_id) == (
            "Новая семья", "Определение", category,
        )
        assert first.unit_id == world.bare_unit
        assert _members(world.db, first.id) == [a]
        assert _members(world.db, second.id) == [b]
        assert _members(world.db, trash.id) == [c]
        assert _attempt(world.db, job.id).outcome == "ok"

    def test_group_of_an_active_family_and_similar_family(self, world):
        contexts = _bare_world(world, titles=("Имя А", "Имя Б"))
        family = _active_family(
            world.db, title="Семья ванной", unit_name="M3", actor_id=world.admin.id
        )
        # Единица с активной семьёй: контексты остаются в охвате как системы.
        for context_id in contexts:
            _make_system(world.db, context_id, world.admin.id)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([
                _group([1], family_id=family.id),
                _group([2], category_id=category, similar=family.id),
            ])),
            now=NOW, settings=S,
        )

        existing, new = _drafts(world.db, _job(world.db, job.id).id)
        assert (existing.grp, existing.existing_family_id, existing.title) == (
            "existing", family.id, None,
        )
        assert (new.grp, new.similar_family_id) == ("new", family.id)

    def test_category_proposals_are_written_for_uncategorized_families(self, world):
        _uncategorize(world.db, world.family.id)
        _ctx = world.family_unit
        context_id, _ = _make(world.db, world.factories, world.proposal, _ctx, "Система")
        _make_system(world.db, context_id, world.admin.id)
        job = _launch(world.db, world.family_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1], category_id=category)],
                              family_categories=[(world.family.id, category)])),
            now=NOW, settings=S,
        )

        (proposal,) = world.db.execute(sa.select(FamilyCategoryProposal)).scalars().all()
        assert (proposal.job_id, proposal.family_id, proposal.family_category_id, proposal.status) == (
            job.id, world.family.id, category, "open",
        )

    def test_categories_only_answer_writes_proposals_without_drafts(self, world):
        _uncategorize(world.db, world.family.id)
        job = _launch(world.db, world.family_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        category = seed_category_id(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([], family_categories=[(world.family.id, category)])),
            now=NOW, settings=S,
        )

        assert _drafts(world.db, job.id) == []
        assert world.db.execute(sa.select(FamilyCategoryProposal)).scalars().one().status == "open"
        assert _job(world.db, job.id).status == "done"

    def test_one_title_in_two_articles_puts_both_contexts_into_the_group(self, world):
        cats = _leaf_categories(world.db, 2)
        first, cp = _make(
            world.db, world.factories, world.proposal, world.bare_unit, "Общее имя",
            article_id=cats[0],
        )
        second, _ = _make(
            world.db, world.factories, world.proposal, world.bare_unit, "Общее имя", cp=cp,
            article_id=cats[1],
        )
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1], category_id=seed_category_id(world.db))])),
            now=NOW, settings=S,
        )

        (draft,) = _drafts(world.db, job.id)
        assert _members(world.db, draft.id) == sorted([first, second])
        names = world.db.execute(
            sa.select(FamilyDraftMember.name_index).where(FamilyDraftMember.draft_id == draft.id)
        ).scalars().all()
        assert set(names) == {1}

    def test_outcome_counts_created_superseded_and_unassigned(self, world):
        _bare_world(world, titles=("Имя А", "Имя Б", "Имя В"))
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        answer = parse_discovery_answer(
            _answer([_group([1], category_id=seed_category_id(world.db))]), claim.sent
        )

        outcome = apply_discovery(
            world.db, job_id=job.id, claim_token=claim.claim_token, answer=answer, settings=S
        )

        assert outcome == DiscoveryOutcome(
            applied=True, unapplied_reason=None, drafts_created=1, superseded_drafts=0,
            unassigned=2,
        )

    def test_scope_changed_between_the_claim_and_the_answer_writes_nothing(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        _ctx_bare_unit(world, "Имя Г — пришло после захвата")
        answer = parse_discovery_answer(
            _answer([_group([1], category_id=seed_category_id(world.db))]), claim.sent
        )

        outcome = apply_discovery(
            world.db, job_id=job.id, claim_token=claim.claim_token, answer=answer, settings=S
        )

        assert outcome == DiscoveryOutcome(False, "stale_fingerprint", 0, 0, 0)
        job = _job(world.db, job.id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )
        assert _drafts(world.db, job.id) == []

    def test_record_result_on_a_stale_scope_closes_the_attempt_ok_and_cancels(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        _ctx_bare_unit(world, "Имя Г — пришло после захвата")

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1], category_id=seed_category_id(world.db))])),
            now=NOW, settings=S,
        )

        job = _job(world.db, claim.job_id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert _attempt(world.db, job.id).outcome == "ok"
        assert world.db.execute(sa.select(FamilyDraft)).first() is None

    def test_answer_of_a_lost_claim_writes_nothing(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        recover_semantic_jobs(world.db, now=NOW)
        answer = parse_discovery_answer(
            _answer([_group([1], category_id=seed_category_id(world.db))]), claim.sent
        )

        outcome = apply_discovery(
            world.db, job_id=job.id, claim_token=claim.claim_token, answer=answer, settings=S
        )

        assert outcome == DiscoveryOutcome(False, "lost_claim", 0, 0, 0)
        assert _job(world.db, job.id).status == "pending"
        assert _drafts(world.db, job.id) == []

    def test_record_result_of_a_lost_claim_closes_the_attempt_as_lost_claim(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        recover_semantic_jobs(world.db, now=NOW)

        worker.record_result(
            world.db, claim,
            _response(_answer([_group([1], category_id=seed_category_id(world.db))])),
            now=NOW, settings=S,
        )

        assert _job(world.db, job.id).status == "pending"
        assert _attempt(world.db, job.id).outcome == "lost_claim"
        assert world.db.execute(sa.select(FamilyDraft)).first() is None


class TestSupersede:
    def _run(self, w, answer_groups, *, unit):
        job = _launch(w.db, unit, admin_id=w.admin.id)
        claim = _claim(w.db)
        worker.record_result(
            w.db, claim, _response(_answer(answer_groups)), now=NOW, settings=S
        )
        return job.id

    def test_next_result_supersedes_open_drafts_and_proposals_of_the_unit(self, world):
        category = seed_category_id(world.db)
        _bare_world(world, titles=("Имя А", "Имя Б"))
        first = self._run(world, [_group([1], category_id=category)], unit=world.bare_unit)
        (old,) = _drafts(world.db, first)
        world.db.add(
            FamilyCategoryProposal(
                job_id=first, family_id=world.family.id, family_category_id=category,
                status="open",
            )
        )
        world.db.flush()
        _ctx_bare_unit(world, "Имя В — новая строка")
        second = self._run(world, [_group([1, 2, 3], category_id=category)], unit=world.bare_unit)

        world.db.expire_all()
        assert world.db.get(FamilyDraft, old.id).status == "superseded"
        (new,) = _drafts(world.db, second)
        assert new.status == "open"
        proposal = world.db.execute(sa.select(FamilyCategoryProposal)).scalars().one()
        assert proposal.status == "superseded"

    def test_other_units_open_drafts_stay_open(self, world):
        category = seed_category_id(world.db)
        _bare_world(world, titles=("Имя А",))
        other_context, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        other = self._run(world, [_group([1], category_id=category)], unit=None)
        mine = self._run(world, [_group([1], category_id=category)], unit=world.bare_unit)

        world.db.expire_all()
        (theirs,) = _drafts(world.db, other)
        assert theirs.status == "open" and theirs.unit_id is None
        assert other_context in discovery_scope(world.db, None).context_ids
        assert _drafts(world.db, mine)[0].status == "open"

    def test_next_result_of_the_null_unit_supersedes_its_open_drafts(self, world):
        """Ревью задачи 3: вытеснение по единице «без единицы» (NULL-безопасное)."""
        category = seed_category_id(world.db)
        _make(world.db, world.factories, world.proposal, None, "Без единицы А")
        first = self._run(world, [_group([1], category_id=category)], unit=None)
        (old,) = _drafts(world.db, first)
        world.db.add(
            FamilyCategoryProposal(
                job_id=first, family_id=world.family.id, family_category_id=category,
                status="open",
            )
        )
        world.db.flush()
        _make(world.db, world.factories, world.proposal, None, "Без единицы Б")
        second = self._run(world, [_group([1, 2], category_id=category)], unit=None)

        world.db.expire_all()
        assert world.db.get(FamilyDraft, old.id).status == "superseded"
        assert _drafts(world.db, second)[0].status == "open"
        assert world.db.execute(sa.select(FamilyCategoryProposal.status)).scalars().all() == [
            "superseded"
        ]

    def test_other_units_open_category_proposals_stay_open(self, world):
        """Ревью задачи 3: предложение категории чужой единицы не вытесняется."""
        category = seed_category_id(world.db)
        _make(world.db, world.factories, world.proposal, None, "Без единицы")
        other = self._run(world, [_group([1], category_id=category)], unit=None)
        world.db.add(
            FamilyCategoryProposal(
                job_id=other, family_id=world.family.id, family_category_id=category,
                status="open",
            )
        )
        world.db.flush()
        _bare_world(world, titles=("Имя А",))
        self._run(world, [_group([1], category_id=category)], unit=world.bare_unit)

        world.db.expire_all()
        assert world.db.execute(sa.select(FamilyCategoryProposal.status)).scalars().all() == [
            "open"
        ]

    def test_discarded_and_activated_drafts_are_not_touched(self, world):
        category = seed_category_id(world.db)
        _bare_world(world, titles=("Имя А", "Имя Б"))
        first = self._run(
            world, [_group([1], category_id=category), _group([2], category_id=category)],
            unit=world.bare_unit,
        )
        kept, gone = _drafts(world.db, first)
        world.db.execute(
            sa.update(FamilyDraft).where(FamilyDraft.id == gone.id).values(
                status="discarded", decided_by=world.admin.id,
                decided_at=dt.datetime.now(dt.UTC),
            )
        )
        _ctx_bare_unit(world, "Имя В — новая строка")
        self._run(world, [_group([1, 2, 3], category_id=category)], unit=world.bare_unit)

        world.db.expire_all()
        assert world.db.get(FamilyDraft, kept.id).status == "superseded"
        assert world.db.get(FamilyDraft, gone.id).status == "discarded"


# ---------------------------------------------------------------------------
#  Сверка и пачки открытие не трогают
# ---------------------------------------------------------------------------

class TestReconcileLeavesDiscoveryAlone:
    def test_reconcile_of_the_unit_does_not_cancel_a_live_discovery_job(self, world):
        contexts = _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)

        reconcile_semantic_jobs(world.db, contexts, cap=NO_CAP, source="operation")

        assert _job(world.db, job.id).status == "pending"

    def test_split_jobs_drops_discovery(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)

        assert _split_jobs([job]) == ([], [])

    def test_open_suggestion_state_does_not_count_a_live_discovery_job(self, world):
        _bare_world(world)
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)

        assert _open_suggestion_state(world.db) == (False, set())

    def test_note_closed_ignores_discovery_jobs(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        closed: list = []

        worker._note_closed(closed, job)

        assert closed == []

    def test_mark_batch_jobs_does_not_reach_a_discovery_job(self, world):
        from services.semantic_decisions import _mark_batch_jobs

        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        fingerprint = Fingerprint(
            kind=job.kind, context_id=None, family_id=None, schema_id=None,
            request_hash=job.request_hash,
        )

        _mark_batch_jobs(world.db, 999_999, [fingerprint])

        assert _job(world.db, job.id).batch_id is None
        assert world.db.execute(sa.select(SemanticReconcileBatch)).first() is None


# ---------------------------------------------------------------------------
#  Очереди «Ошибки» и «Задержанные»
# ---------------------------------------------------------------------------

class TestQueueRows:
    def test_error_row_of_a_discovery_job(self, world):
        _bare_world(world)
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        worker.record_result(world.db, claim, _response("не json"), now=NOW, settings=S)

        response = list_jobs(world.db, status="error")

        (row,) = response["items"]
        assert row["job_id"] == job.id and row["kind"] == "family_discovery"
        assert row["context_id"] is None and row["family_id"] is None
        assert row["unit_id"] == world.bare_unit
        assert row["unit_code"] == "M3"
        assert row["names_count"] == 3
        assert row["error_text"]
        assert row["last_error_class"] == "schema_error"
        assert row["matches"] is None and row["path"] == []

    def test_hold_row_of_a_discovery_job_carries_the_matches(self, world):
        world.factories.ContractorFactory.create(title="Ромашка")
        _bare_world(world, titles=("Кладка у Ромашка", "Имя Б"))
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        assert worker.claim_next(world.db, settings=S, now=NOW) is None

        response = list_jobs(world.db, status="privacy_hold")

        (row,) = response["items"]
        assert row["job_id"] == job.id and row["kind"] == "family_discovery"
        assert row["names_count"] == 2 and row["unit_code"] == "M3"
        assert [(m["text"], m["where"]) for m in row["matches"]] == [("ромашка", "context")]

    def test_listing_survives_a_mix_of_discovery_and_suggestion_jobs(self, world):
        from tests.integration.test_semantic_queue_api import _hold_job

        world.factories.ContractorFactory.create(title="Ромашка")
        contexts = _bare_world(world, titles=("Кладка у Ромашка", "Имя Б"))
        discovery = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        assert worker.claim_next(world.db, settings=S, now=NOW) is None
        suggestion = _hold_job(world.db, contexts[0], unit_id=world.bare_unit)

        items = list_jobs(world.db, status="privacy_hold")["items"]

        assert sorted(item["job_id"] for item in items) == sorted([discovery.id, suggestion.id])
        by_id = {item["job_id"]: item for item in items}
        assert by_id[suggestion.id]["context_id"] == contexts[0]
        assert by_id[discovery.id]["context_id"] is None

    def test_hold_row_has_no_names_count_when_the_scope_changed_after_launch(self, world):
        world.factories.ContractorFactory.create(title="Ромашка")
        _bare_world(world, titles=("Кладка у Ромашка", "Имя Б"))
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        assert worker.claim_next(world.db, settings=S, now=NOW) is None
        assert list_jobs(world.db, status="privacy_hold")["items"][0]["names_count"] == 2
        _ctx_bare_unit(world, "Имя В — пришло после запуска")

        (row,) = list_jobs(world.db, status="privacy_hold")["items"]

        assert row["job_id"] == job.id and row["names_count"] is None

    def test_error_row_has_no_names_count_when_the_scope_changed_after_launch(self, world):
        _bare_world(world, titles=("Имя А", "Имя Б"))
        _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        worker.record_result(world.db, claim, _response("не json"), now=NOW, settings=S)
        assert list_jobs(world.db, status="error")["items"][0]["names_count"] == 2
        _ctx_bare_unit(world, "Имя В — пришло после ошибки")

        (row,) = list_jobs(world.db, status="error")["items"]

        assert row["names_count"] is None

    def test_jobs_of_other_units_do_not_leak_into_names_count(self, world):
        _bare_world(world, titles=("Имя А", "Имя Б"))
        _make(world.db, world.factories, world.proposal, None, "Без единицы")
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id)
        claim = _claim(world.db)
        worker.record_result(world.db, claim, _response("не json"), now=NOW, settings=S)

        (row,) = list_jobs(world.db, status="error")["items"]

        assert row["job_id"] == job.id and row["names_count"] == 2

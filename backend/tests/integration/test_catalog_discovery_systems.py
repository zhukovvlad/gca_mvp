"""Системы на всём пути контура предложений (спека 3б §1.3, §2.6, DoD 4, 5).

Система (`semantic_kind = 'SYSTEM'`) применима там же, где работа: получает
задание предложения с собственным промптом, её ответ публикуется и
подтверждается, значения дают вариант и промоушен строки каталога. Метки
очереди: `semantic_kind` у строки и `system_count` у группы.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from crud.semantic_queue import list_suggestions, queue_status
from models import CatalogContext, CatalogPosition, ContextBucket, FamilySuggestion, SemanticJob
from services.semantic_client import ModelResponse
from services.semantic_decisions import confirm_suggestions
from services.semantic_reconcile import NO_CAP, reconcile_semantic_jobs
from services.semantic_request import SEMANTIC_PROMPT, SYSTEM_SEMANTIC_PROMPT
from tests.integration.test_semantic_queue_api import (
    _make_job,
    _published,
    _rendered,
    _scene,
)
from tests.integration.test_work_variants_core import _drop_family, _world

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

_VALUES_ANSWER = (
    '{"values": [{"ordinal": 1, "kind": "value", "value": "50 мм", "source": "name"},'
    ' {"ordinal": 2, "kind": "value", "value": "бетон", "source": "path"}]}'
)


def _make_system(db, context_id: int, user_id: int) -> None:
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(
            semantic_kind="SYSTEM", semantic_kind_source="manual",
            semantic_kind_by=user_id, semantic_kind_at=dt.datetime.now(dt.UTC),
        )
    )
    db.expire_all()


def _response(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=8274, completion_tokens=156, cache_write_tokens=8058, cached_tokens=0,
        cost_usd=Decimal("0.01"),
    )


def _answer(family_id: int, confidence="0.95") -> str:
    return (
        f'{{"family_id": {family_id}, "new_family_name": null, '
        f'"confidence": {confidence}, "reason": "проверочная причина"}}'
    )


class TestSuggestionJobsByKind:
    def test_each_kind_gets_its_own_prompt_version_and_hash(
        self, db_session, factories, admin_user
    ):
        scene = _scene(db_session, factories, admin_user, titles=("Работа", "Система"))
        work, system = scene.context_ids
        _make_system(db_session, system, admin_user.id)

        report = reconcile_semantic_jobs(
            db_session, [work, system], cap=NO_CAP, source="operation"
        )

        assert report.created == 2
        jobs = {
            j.context_id: j
            for j in db_session.execute(
                sa.select(SemanticJob).where(SemanticJob.kind == "family_suggestion")
            ).scalars()
        }
        assert (jobs[work].prompt_version, jobs[system].prompt_version) == ("1", "system:1")
        assert jobs[work].request_hash == _rendered(db_session, work).request_hash
        assert jobs[system].request_hash == _rendered(db_session, system).request_hash
        assert jobs[work].request_hash != jobs[system].request_hash
        assert jobs[work].prefix_hash != jobs[system].prefix_hash


class TestQueueMarks:
    def test_rows_carry_kind_and_group_counts_systems(self, db_session, factories, admin_user):
        scene = _scene(
            db_session, factories, admin_user, titles=("Пол А", "Пол Б", "Пол В")
        )
        a, b, c = scene.context_ids
        _make_system(db_session, a, admin_user.id)
        _make_system(db_session, b, admin_user.id)
        for context_id in (a, b, c):
            _published(db_session, context_id, family_id=scene.family.id, confidence="0.95")

        queue = list_suggestions(db_session, queue="list")

        (group,) = queue["groups"]
        assert (group["total"], group["system_count"]) == (3, 2)
        kinds = {row["context_id"]: row["semantic_kind"] for row in group["rows"]}
        assert kinds == {a: "SYSTEM", b: "SYSTEM", c: "WORK"}

    def test_group_without_systems_counts_zero(self, db_session, factories, admin_user):
        scene = _scene(db_session, factories, admin_user, titles=("Пол А", "Пол Б"))
        for context_id in scene.context_ids:
            _published(db_session, context_id, family_id=scene.family.id)

        (group,) = list_suggestions(db_session, queue="list")["groups"]

        assert (group["total"], group["system_count"]) == (2, 0)

    def test_work_and_system_of_one_unit_are_both_current(
        self, db_session, factories, admin_user
    ):
        """Единица со смешанными видами: у каждого вида свой префикс запроса, и
        предложение каждого опубликовано на его собственный отпечаток."""
        scene = _scene(db_session, factories, admin_user, titles=("Работа", "Система"))
        work, system = scene.context_ids
        _make_system(db_session, system, admin_user.id)
        for context_id in (work, system):
            _published(db_session, context_id, family_id=scene.family.id)

        (group,) = list_suggestions(db_session, queue="list")["groups"]

        assert sorted(row["context_id"] for row in group["rows"]) == sorted([work, system])

    def test_suggestion_made_for_work_is_not_current_after_the_kind_changed(
        self, db_session, factories, admin_user
    ):
        scene = _scene(db_session, factories, admin_user, titles=("Работа", "Станет системой"))
        work, becomes_system = scene.context_ids
        for context_id in (work, becomes_system):
            _published(db_session, context_id, family_id=scene.family.id)
        _make_system(db_session, becomes_system, admin_user.id)

        (group,) = list_suggestions(db_session, queue="list")["groups"]

        assert [row["context_id"] for row in group["rows"]] == [work]

    def test_system_answer_of_the_model_goes_to_the_new_queue_flagged(
        self, db_session, factories, admin_user
    ):
        scene = _scene(db_session, factories, admin_user, titles=("Тепловой пункт",))
        (system,) = scene.context_ids
        _make_system(db_session, system, admin_user.id)
        reconcile_semantic_jobs(db_session, [system], cap=NO_CAP, source="operation")
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None and claim.job_id is not None

        worker.record_result(
            db_session, claim,
            _response(
                '{"family_id": 0, "new_family_name": "СИСТЕМА", "confidence": 0.9,'
                ' "reason": "комплект"}'
            ),
            now=NOW, settings=S,
        )

        items = list_suggestions(db_session, queue="new")["items"]
        assert [(i["context_id"], i["new_family_name"], i["is_system"]) for i in items] == [
            (system, "СИСТЕМА", True)
        ]
        assert list_suggestions(db_session, queue="list")["groups"] == []


class TestStaleScanOfSystems:
    def test_system_without_a_covering_job_makes_its_unit_stale(
        self, db_session, factories, admin_user
    ):
        scene = _scene(db_session, factories, admin_user, titles=("Работа", "Система"))
        work, system = scene.context_ids
        _make_system(db_session, system, admin_user.id)
        _make_job(db_session, work, status="done")

        status = queue_status(db_session)

        assert status["stale_units"] == [
            {"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 1}
        ]

    def test_system_with_a_covering_job_keeps_the_unit_current(
        self, db_session, factories, admin_user
    ):
        scene = _scene(db_session, factories, admin_user, titles=("Работа", "Система"))
        work, system = scene.context_ids
        _make_system(db_session, system, admin_user.id)
        _make_job(db_session, work, status="done")
        _make_job(db_session, system, status="done")

        status = queue_status(db_session)

        assert (status["stale_units"], status["config_stale"]) == ([], None)


class TestSystemFromSuggestionToPromotion:
    """DoD 4: система в единице с активной семьёй проходит весь путь."""

    def test_system_gets_a_family_values_a_variant_and_a_position_row(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories)
        context_id = world.context_id
        world.family.status = "active"
        _drop_family(db_session, world)
        db_session.flush()
        _make_system(db_session, context_id, admin_user.id)
        catalog = db_session.get(CatalogPosition, world.catalog_id)
        assert catalog.kind == "TO_REVIEW"

        # 1. задание предложения со своим промптом
        report = reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")
        assert report.created == 1
        job = db_session.execute(
            sa.select(SemanticJob).where(SemanticJob.kind == "family_suggestion")
        ).scalar_one()
        assert job.prompt_version == "system:1"
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None and claim.job_id == job.id
        first_block = claim.rendered.body["messages"][0]["content"][0]["text"]
        assert first_block == SYSTEM_SEMANTIC_PROMPT
        assert first_block != SEMANTIC_PROMPT

        # 2. ответ публикуется и подтверждается
        worker.record_result(
            db_session, claim, _response(_answer(world.family.id)), now=NOW, settings=S
        )
        suggestion = db_session.execute(
            sa.select(FamilySuggestion).where(FamilySuggestion.context_id == context_id)
        ).scalar_one()
        assert (suggestion.is_published, suggestion.family_id) == (True, world.family.id)
        confirm_suggestions(db_session, suggestion_ids=[suggestion.id], actor_id=admin_user.id)
        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert (context.work_family_id, context.family_source) == (world.family.id, "suggestion")

        # 3. сверка ставит задание значений, его результат даёт вариант и промоушен
        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")
        values_job = db_session.execute(
            sa.select(SemanticJob).where(
                SemanticJob.kind == "context_values", SemanticJob.status == "pending"
            )
        ).scalar_one()
        values_claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert values_claim is not None and values_claim.job_id == values_job.id
        worker.record_result(
            db_session, values_claim, _response(_VALUES_ANSWER), now=NOW, settings=S
        )

        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert context.work_variant_id is not None
        bucket = db_session.get(ContextBucket, context.bucket_id)
        assert db_session.get(CatalogPosition, bucket.catalog_position_id).kind == "POSITION"


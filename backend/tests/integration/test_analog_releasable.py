"""Признак «можно отпустить»: хранение, правка схемы, задание `releasable_params`,
предложения, решение человека, очередь и команда (лестница аналогов, спека §2.6,
§2.7, §2.17).

Модель подменена клиентом: настоящих вызовов нет. Свойство К3 (ответ и принятие
предложения относятся к своему запросу) проверяется ВХОДОМ В ОБХОД ГИГИЕНЫ: тест
меняет входы запроса прямым `UPDATE`, как сделал бы путь правки, забывший уборку, и
ПЕРЕД действием проверяет предусловие (задание в `running` с прежним отпечатком;
строка предложения со старым отпечатком существует). Уборка и отмена обычными
путями правки — отдельные тесты.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
import crud.semantic_queue as queue
import crud.work_variants as crud_work_variants
from config import settings as app_settings
from crud.common import DomainError
from models import (
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    ParameterRole,
    ParameterValueKind,
    ReleasableProposal,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    UserRole,
    WorkFamily,
)
from services import releasable
from services import schema_edit as se
from services.releasable import (
    CODE_PROPOSAL_STALE,
    current_proposals,
    decide_releasable,
    enqueue_releasable,
    lock_inputs,
    render_releasable_request,
)
from services.semantic_client import ModelResponse
from services.semantic_worker import claim_next, record_result, render_job_request
from services.unit_resolution import UnitResolver
from services.work_families import UNSET, merge_families, set_unit, update_family
from services.work_variants import merge_parameter_values
from tests.integration.test_work_variants_core import (
    _answer as _values_answer,
)
from tests.integration.test_work_variants_core import (
    _apply as _apply_values,
)
from tests.integration.test_work_variants_core import (
    _freeze,
    _freeze_world,
    _lock_sequence,
    _named,
)
from tests.integration.test_work_variants_core import (
    _world as _values_world,
)
from tests.integration.test_work_variants_material import _capturing_sql, _frozen_schema, _uid
from tests.integration.test_work_variants_schema import _family

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"

S = app_settings.model_copy(
    update={
        "SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD": Decimal("0.95"),
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
        "SEMANTIC_MAX_ATTEMPTS": 3,
    }
)

_PARAMS = (("Толщина", ["50 мм", "80 мм"]), ("Армирование", ["сетка", "фибра"]))


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)


def _response(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=900, completion_tokens=120, cache_write_tokens=0, cached_tokens=0,
        cost_usd=Decimal("0.01"),
    )


def _unit(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _world(db, factories, params=_PARAMS, *, unit="м2"):
    """Активная семья с действующей версией и главными параметрами; задания нет."""
    family = _family(
        db, status="active", definition="Стяжка пола", title=f"Стяжка {_uid()}",
        unit_id=_unit(db, unit) if unit else None,
    )
    schema = _frozen_schema(
        db, factories, family, [(i, name, values) for i, (name, values) in enumerate(params, 1)]
    )
    ids = list(
        db.execute(
            sa.select(FamilyParameter.id)
            .where(FamilyParameter.schema_id == schema.id)
            .order_by(FamilyParameter.ordinal)
        ).scalars()
    )
    return SimpleNamespace(family=family, schema=schema, ids=ids)


def _queued(db, factories, **kwargs):
    world = _world(db, factories, **kwargs)
    assert enqueue_releasable(db, world.schema.id, settings=S) is True
    world.job_id = _jobs(db)[-1].id
    return world


def _jobs(db, schema_id=None):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == SemanticJobKind.releasable_params.value)
    if schema_id is not None:
        stmt = stmt.where(SemanticJob.schema_id == schema_id)
    return list(db.execute(stmt.order_by(SemanticJob.id)).scalars())


def _claim(db):
    claim = claim_next(db, settings=S, now=_now())
    assert claim is not None, "захвата нет"
    return claim


def _running(db, factories, **kwargs):
    """Мир с заданием в `running`; возвращает (мир, захват)."""
    world = _queued(db, factories, **kwargs)
    return world, _claim(db)


def _hash_now(db, world) -> str:
    return render_releasable_request(db, world.family.id, world.schema.id, settings=S).request_hash


def _reload(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _flags(db, world) -> list[bool | None]:
    db.expire_all()
    return [db.get(FamilyParameter, pid).releasable for pid in world.ids]


def _proposals(db, world=None) -> list[ReleasableProposal]:
    db.expire_all()
    stmt = sa.select(ReleasableProposal).order_by(ReleasableProposal.parameter_id)
    if world is not None:
        stmt = stmt.where(ReleasableProposal.parameter_id.in_(world.ids))
    return list(db.execute(stmt).scalars())


def _answer(world, *, releasable=True, confidence=0.99, only=None) -> str:
    ids = world.ids if only is None else only
    return json.dumps(
        {
            "parameters": [
                {
                    "parameter_id": pid,
                    "releasable": releasable,
                    "confidence": confidence,
                    "reason": "Разница по параметру мало меняет цену",
                }
                for pid in ids
            ]
        },
        ensure_ascii=False,
    )


def _record(db, claim, content):
    return record_result(db, claim, _response(content), now=_now(), settings=S)


def _deferred(db, factories, **kwargs):
    """Мир, где модель ответила неуверенно: у каждого параметра есть предложение
    со старым отпечатком; задание `done`."""
    world, claim = _running(db, factories, **kwargs)
    world.old_hash = claim.rendered.request_hash
    _record(db, claim, _answer(world, confidence=0.6))
    assert [p.request_hash for p in _proposals(db, world)] == [world.old_hash] * len(world.ids)
    return world


def _admin(factories):
    return factories.UserFactory.create(role=UserRole.admin)


# Входы запроса, меняемые прямым UPDATE (в обход гигиены правок).

def _m_rename_parameter(db, world):
    db.execute(
        sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
        .values(name="Толщина слоя")
    )


def _m_change_values(db, world):
    db.execute(
        sa.update(FamilyParameterValue)
        .where(FamilyParameterValue.parameter_id == world.ids[0])
        .values(value=sa.func.concat(FamilyParameterValue.value, " (изм.)"))
    )


def _m_family_definition(db, world):
    db.execute(
        sa.update(WorkFamily).where(WorkFamily.id == world.family.id)
        .values(definition="Совсем другое определение")
    )


def _m_family_title(db, world):
    db.execute(
        sa.update(WorkFamily).where(WorkFamily.id == world.family.id)
        .values(title=sa.func.concat(WorkFamily.title, " (новая)"))
    )


def _m_family_unit(db, world):
    db.execute(
        sa.update(WorkFamily).where(WorkFamily.id == world.family.id)
        .values(unit_id=_unit(db, "м3"))
    )


MUTATIONS = [
    pytest.param(_m_rename_parameter, id="parameter_renamed"),
    pytest.param(_m_change_values, id="values_changed"),
    pytest.param(_m_family_definition, id="family_definition"),
    pytest.param(_m_family_title, id="family_title"),
    pytest.param(_m_family_unit, id="family_unit"),
]


# ---------------------------------------------------------------------------
#  К3, ответ модели: повторный рендер под замками, а не статус задания
# ---------------------------------------------------------------------------

class TestAnswerBelongsToItsRequest:
    @pytest.mark.parametrize("mutate", MUTATIONS)
    def test_a_confident_answer_after_a_changed_input_is_cancelled(
        self, db_session, factories, mutate
    ):
        world, claim = _running(db_session, factories)
        job = _reload(db_session, SemanticJob, claim.job_id)
        # Предусловие: задание в running с прежним отпечатком, чистой гигиены нет.
        assert job.status == "running" and job.claim_token == claim.claim_token
        assert job.request_hash == claim.rendered.request_hash == _hash_now(db_session, world)

        mutate(db_session, world)

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert job.status == "running", "гигиена не должна была сработать"
        assert _hash_now(db_session, world) != job.request_hash, "вход не изменился"

        _record(db_session, claim, _answer(world))

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )
        assert _flags(db_session, world) == [None, None]
        assert _proposals(db_session, world) == []

    @pytest.mark.parametrize("mutate", MUTATIONS)
    def test_a_low_confidence_answer_after_a_changed_input_writes_no_proposal(
        self, db_session, factories, mutate
    ):
        world, claim = _running(db_session, factories)
        assert _reload(db_session, SemanticJob, claim.job_id).status == "running"
        mutate(db_session, world)
        assert _hash_now(db_session, world) != claim.rendered.request_hash

        _record(db_session, claim, _answer(world, confidence=0.3))

        assert _reload(db_session, SemanticJob, claim.job_id).cancel_reason == "input_changed"
        assert _proposals(db_session, world) == []

    def test_a_superseded_version_makes_the_answer_not_applicable(self, db_session, factories):
        world, claim = _running(db_session, factories)
        job = _reload(db_session, SemanticJob, claim.job_id)
        assert job.status == "running" and job.request_hash == _hash_now(db_session, world)

        db_session.execute(
            sa.update(FamilyParameterSchema).where(FamilyParameterSchema.id == world.schema.id)
            .values(status="superseded", superseded_at=sa.func.now())
        )

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert job.status == "running"
        _record(db_session, claim, _answer(world))

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _flags(db_session, world) == [None, None]
        assert _proposals(db_session, world) == []

    def test_an_archived_family_makes_the_answer_not_applicable(self, db_session, factories):
        world, claim = _running(db_session, factories)
        assert _reload(db_session, SemanticJob, claim.job_id).status == "running"

        db_session.execute(
            sa.update(WorkFamily).where(WorkFamily.id == world.family.id)
            .values(status="archived", archived_at=sa.func.now())
        )

        assert _reload(db_session, SemanticJob, claim.job_id).status == "running"
        _record(db_session, claim, _answer(world))

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _flags(db_session, world) == [None, None]

    def test_a_lost_claim_writes_nothing(self, db_session, factories):
        world, claim = _running(db_session, factories)
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == claim.job_id)
            .values(status="pending", claim_token=None)
        )

        _record(db_session, claim, _answer(world))

        assert _reload(db_session, SemanticJob, claim.job_id).status == "pending"
        assert _flags(db_session, world) == [None, None]
        assert _proposals(db_session, world) == []
        attempt = db_session.execute(
            sa.select(SemanticJobAttempt).where(SemanticJobAttempt.job_id == claim.job_id)
        ).scalar_one()
        assert attempt.outcome == "lost_claim"


# ---------------------------------------------------------------------------
#  К3, предложение: действующее предложение определяется рендером, а не строкой
# ---------------------------------------------------------------------------

class TestProposalBelongsToItsRequest:
    @pytest.mark.parametrize("mutate", MUTATIONS)
    def test_a_decision_with_the_old_hash_after_a_changed_input_is_409(
        self, db_session, factories, mutate
    ):
        world = _deferred(db_session, factories)
        admin = _admin(factories)
        # Предусловие: предложение видно и сверку проходит (контроль) ...
        assert len(current_proposals(db_session, family_ids=None, page=None)) == 2
        assert queue.queue_status(db_session)["releasable_open"] == 2

        mutate(db_session, world)

        # ... строка предложения со старым отпечатком жива, гигиены не было ...
        stale_rows = _proposals(db_session, world)
        assert [p.request_hash for p in stale_rows] == [world.old_hash] * 2
        assert _hash_now(db_session, world) != world.old_hash

        with pytest.raises(DomainError) as caught:
            decide_releasable(
                db_session, world.family.id, world.ids[0], True,
                request_hash=world.old_hash, actor_id=admin.id,
            )

        assert (caught.value.status_code, caught.value.code) == (409, CODE_PROPOSAL_STALE)
        assert _flags(db_session, world) == [None, None]
        # недействующее предложение при этом удаляется
        assert [p.parameter_id for p in _proposals(db_session, world)] == [world.ids[1]]

    @pytest.mark.parametrize("mutate", MUTATIONS)
    def test_the_stale_proposal_is_invisible_everywhere(self, db_session, factories, mutate):
        world = _deferred(db_session, factories)
        before = crud_work_variants.schema_out(db_session, world.family.id)
        assert [p["releasable_proposal"] is not None for p in before["parameters"]] == [True, True]

        mutate(db_session, world)

        assert len(_proposals(db_session, world)) == 2  # строки на месте
        assert current_proposals(db_session, family_ids=None, page=None) == []
        assert current_proposals(db_session, family_ids=[world.family.id], page=1) == []
        after = crud_work_variants.schema_out(db_session, world.family.id)
        assert [p["releasable_proposal"] for p in after["parameters"]] == [None, None]
        assert queue.queue_status(db_session)["releasable_open"] == 0

    def test_a_decision_without_the_hash_is_not_checked_against_the_render(
        self, db_session, factories
    ):
        world = _deferred(db_session, factories)
        _m_family_definition(db_session, world)

        decide_releasable(
            db_session, world.family.id, world.ids[0], True, request_hash=None,
            actor_id=_admin(factories).id,
        )

        assert _flags(db_session, world) == [True, None]


# ---------------------------------------------------------------------------
#  Принятие ответа
# ---------------------------------------------------------------------------

class TestAcceptance:
    def test_confident_answers_write_true_and_false_without_touching_origin_and_revision(
        self, db_session, factories
    ):
        world, claim = _running(db_session, factories)
        revision = _reload(db_session, FamilyParameterSchema, world.schema.id).revision
        answer = json.dumps(
            {"parameters": [
                {"parameter_id": world.ids[0], "releasable": True, "confidence": 0.95,
                 "reason": "а"},
                {"parameter_id": world.ids[1], "releasable": False, "confidence": 1,
                 "reason": "б"},
            ]}
        )

        _record(db_session, claim, answer)

        assert _flags(db_session, world) == [True, False]
        db_session.expire_all()
        assert [db_session.get(FamilyParameter, pid).origin for pid in world.ids] == ["model"] * 2
        assert db_session.get(FamilyParameterSchema, world.schema.id).revision == revision
        assert _proposals(db_session, world) == []
        job = db_session.get(SemanticJob, claim.job_id)
        assert (job.status, job.claim_token) == ("done", None)

    def test_below_the_threshold_the_answer_becomes_a_proposal_with_the_job_hash(
        self, db_session, factories
    ):
        world, claim = _running(db_session, factories)
        answer = json.dumps(
            {"parameters": [
                {"parameter_id": world.ids[0], "releasable": True, "confidence": 0.949,
                 "reason": "возможно"},
                {"parameter_id": world.ids[1], "releasable": False, "confidence": 0.5,
                 "reason": "неясно"},
            ]}
        )

        _record(db_session, claim, answer)

        assert _flags(db_session, world) == [None, None]
        first, second = _proposals(db_session, world)
        assert (first.parameter_id, first.releasable, first.confidence, first.reason) == (
            world.ids[0], True, Decimal("0.949"), "возможно",
        )
        assert (second.releasable, second.request_hash) == (False, claim.rendered.request_hash)
        assert _reload(db_session, SemanticJob, claim.job_id).status == "done"

    def test_the_boundary_value_is_accepted(self, db_session, factories):
        world, claim = _running(db_session, factories)

        _record(db_session, claim, _answer(world, confidence=0.95))

        assert _flags(db_session, world) == [True, True]

    def test_the_threshold_comes_from_the_settings(self, db_session, factories):
        world = _queued(db_session, factories)
        strict = S.model_copy(update={"SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD": Decimal("1")})
        claim = claim_next(db_session, settings=strict, now=_now())

        record_result(
            db_session, claim, _response(_answer(world, confidence=0.99)), now=_now(),
            settings=strict,
        )

        assert _flags(db_session, world) == [None, None]
        assert len(_proposals(db_session, world)) == 2

    def test_a_marked_parameter_is_not_touched_by_the_answer(self, db_session, factories):
        world, claim = _running(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(releasable=False, origin="human")
        )

        _record(db_session, claim, _answer(world, releasable=True))

        assert _flags(db_session, world) == [False, True]
        assert _proposals(db_session, world) == []

    def test_a_confident_answer_removes_an_older_proposal_of_the_parameter(
        self, db_session, factories
    ):
        world, claim = _running(db_session, factories)
        db_session.add(
            ReleasableProposal(
                parameter_id=world.ids[0], releasable=False, confidence=Decimal("0.4"),
                reason="старое", request_hash="old",
            )
        )
        db_session.flush()

        _record(db_session, claim, _answer(world))

        assert _proposals(db_session, world) == []
        assert _flags(db_session, world) == [True, True]

    def test_an_answer_over_a_repeated_pending_proposal_replaces_it(self, db_session, factories):
        world, claim = _running(db_session, factories)
        db_session.add(
            ReleasableProposal(
                parameter_id=world.ids[0], releasable=False, confidence=Decimal("0.4"),
                reason="старое", request_hash="old",
            )
        )
        db_session.flush()

        _record(db_session, claim, _answer(world, confidence=0.7))

        first, _second = _proposals(db_session, world)
        assert (first.request_hash, first.reason) == (
            claim.rendered.request_hash, "Разница по параметру мало меняет цену",
        )

    @pytest.mark.parametrize(
        "shape",
        ["skipped", "repeated", "foreign", "malformed"],
    )
    def test_a_wrong_answer_set_is_an_attempt_error(self, db_session, factories, shape):
        world, claim = _running(db_session, factories)
        content = {
            "skipped": _answer(world, only=world.ids[:1]),
            "repeated": _answer(world, only=[world.ids[0], world.ids[0], world.ids[1]]),
            "foreign": _answer(world, only=[*world.ids, world.ids[-1] + 1000]),
            "malformed": "не JSON",
        }[shape]

        _record(db_session, claim, content)

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        attempt = db_session.execute(
            sa.select(SemanticJobAttempt).where(SemanticJobAttempt.job_id == claim.job_id)
        ).scalar_one()
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert _flags(db_session, world) == [None, None]
        assert _proposals(db_session, world) == []

    def test_the_stale_check_comes_before_the_coverage_check(self, db_session, factories):
        world, claim = _running(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[1])
            .values(archived_at=sa.func.now())
        )

        _record(db_session, claim, _answer(world))  # ответ о двух, активен один

        job = _reload(db_session, SemanticJob, claim.job_id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")


# ---------------------------------------------------------------------------
#  Решение человека
# ---------------------------------------------------------------------------

class TestDecision:
    def test_a_decision_marks_the_parameter_as_human_bumps_the_revision_and_drops_the_proposal(
        self, db_session, factories
    ):
        world = _deferred(db_session, factories)
        revision = _reload(db_session, FamilyParameterSchema, world.schema.id).revision

        decide_releasable(
            db_session, world.family.id, world.ids[0], True, request_hash=world.old_hash,
            actor_id=_admin(factories).id,
        )

        db_session.expire_all()
        parameter = db_session.get(FamilyParameter, world.ids[0])
        assert (parameter.releasable, parameter.origin) == (True, "human")
        assert db_session.get(FamilyParameterSchema, world.schema.id).revision == revision + 1
        assert [p.parameter_id for p in _proposals(db_session, world)] == [world.ids[1]]

    def test_an_answer_of_no_is_stored_as_false(self, db_session, factories):
        world = _deferred(db_session, factories)

        decide_releasable(
            db_session, world.family.id, world.ids[1], False, request_hash=world.old_hash,
            actor_id=_admin(factories).id,
        )

        assert _flags(db_session, world) == [None, False]

    def test_a_decision_without_a_hash_and_without_a_proposal_is_taken_as_is(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        revision = world.schema.revision

        decide_releasable(
            db_session, world.family.id, world.ids[0], True, request_hash=None,
            actor_id=_admin(factories).id,
        )

        assert _flags(db_session, world) == [True, None]
        assert _reload(db_session, FamilyParameterSchema, world.schema.id).revision == revision + 1

    def test_a_decision_over_an_existing_flag_replaces_it(self, db_session, factories):
        world = _world(db_session, factories)
        admin = _admin(factories)

        for answer in (True, False):
            decide_releasable(
                db_session, world.family.id, world.ids[0], answer, request_hash=None,
                actor_id=admin.id,
            )

        assert _flags(db_session, world) == [False, None]

    def test_a_decision_changes_no_job_and_enqueues_nothing(self, db_session, factories):
        world = _deferred(db_session, factories)
        jobs = [(j.id, j.status) for j in _jobs(db_session)]

        decide_releasable(
            db_session, world.family.id, world.ids[0], True, request_hash=world.old_hash,
            actor_id=_admin(factories).id,
        )

        assert [(j.id, j.status) for j in _jobs(db_session)] == jobs

    def test_a_secondary_parameter_is_422(self, db_session, factories):
        world = _world(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(role="secondary")
        )

        with pytest.raises(DomainError) as caught:
            decide_releasable(
                db_session, world.family.id, world.ids[0], True, request_hash=None,
                actor_id=_admin(factories).id,
            )

        assert (caught.value.status_code, caught.value.code) == (422, "parameter_not_main")

    @pytest.mark.parametrize("case", ["unknown_parameter", "other_family", "superseded", "no_family"])
    def test_a_parameter_outside_the_current_schema_of_the_family_is_404(
        self, db_session, factories, case
    ):
        world = _world(db_session, factories)
        other = _world(db_session, factories)
        family_id, parameter_id = world.family.id, world.ids[0]
        if case == "unknown_parameter":
            parameter_id = 987654321
        elif case == "other_family":
            parameter_id = other.ids[0]
        elif case == "superseded":
            db_session.execute(
                sa.update(FamilyParameterSchema).where(FamilyParameterSchema.id == world.schema.id)
                .values(status="superseded", superseded_at=sa.func.now())
            )
        else:
            family_id = 987654321

        with pytest.raises(DomainError) as caught:
            decide_releasable(
                db_session, family_id, parameter_id, True, request_hash=None,
                actor_id=_admin(factories).id,
            )

        assert caught.value.status_code == 404

    @pytest.mark.parametrize("case", ["no_row", "other_row_hash", "wrong_given_hash"])
    def test_without_a_live_proposal_with_that_hash_it_is_409(self, db_session, factories, case):
        world = _deferred(db_session, factories)
        given = world.old_hash
        if case == "no_row":
            db_session.execute(
                sa.delete(ReleasableProposal).where(ReleasableProposal.parameter_id == world.ids[0])
            )
        elif case == "other_row_hash":
            db_session.execute(
                sa.update(ReleasableProposal)
                .where(ReleasableProposal.parameter_id == world.ids[0])
                .values(request_hash="другой")
            )
        else:
            given = "чужой"

        with pytest.raises(DomainError) as caught:
            decide_releasable(
                db_session, world.family.id, world.ids[0], True, request_hash=given,
                actor_id=_admin(factories).id,
            )

        assert (caught.value.status_code, caught.value.code) == (409, CODE_PROPOSAL_STALE)
        assert _flags(db_session, world)[0] is None

    def test_a_proposal_of_an_already_marked_parameter_is_not_live(self, db_session, factories):
        world = _deferred(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(releasable=False, origin="human")
        )

        assert [v.parameter_id for v in current_proposals(db_session, family_ids=None, page=None)] == [
            world.ids[1]
        ]
        with pytest.raises(DomainError) as caught:
            decide_releasable(
                db_session, world.family.id, world.ids[0], True, request_hash=world.old_hash,
                actor_id=_admin(factories).id,
            )
        assert caught.value.code == CODE_PROPOSAL_STALE
        assert _flags(db_session, world) == [False, None]


class TestCurrentProposals:
    def test_views_carry_family_parameter_values_answer_and_hash(self, db_session, factories):
        world = _deferred(db_session, factories)

        views = current_proposals(db_session, family_ids=None, page=None)

        assert [v.parameter_id for v in views] == world.ids
        first = views[0]
        assert (first.family_id, first.family_title, first.parameter_name) == (
            world.family.id, world.family.title, "Толщина",
        )
        assert (first.values, first.releasable, first.confidence, first.request_hash) == (
            ("50 мм", "80 мм"), True, Decimal("0.6"), world.old_hash,
        )

    def test_family_filter_and_pages(self, db_session, factories):
        a = _deferred(db_session, factories)
        _deferred(db_session, factories)

        assert len(current_proposals(db_session, family_ids=None, page=None)) == 4
        only = current_proposals(db_session, family_ids=[a.family.id], page=None)
        assert [v.parameter_id for v in only] == a.ids
        assert len(current_proposals(db_session, family_ids=None, page=1)) == 4
        assert current_proposals(db_session, family_ids=None, page=2) == []

    def test_archiving_a_parameter_changes_the_request_and_hides_the_proposals(
        self, db_session, factories
    ):
        world = _deferred(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(archived_at=sa.func.now())
        )

        assert current_proposals(db_session, family_ids=None, page=None) == []


# ---------------------------------------------------------------------------
#  Уборка и отмена обычными путями правки
# ---------------------------------------------------------------------------

def _edit_schema(db, factories, world):
    """Правка схемы на месте: параметру добавлено значение; признаки сохранены."""
    flags = _flags(db, world)
    drafts = [
        se.ParameterDraft(
            ordinal=i, name=name, role=ParameterRole.main, value_kind=ParameterValueKind.exact,
            closed=False, values=tuple([*values, "добавлено"] if i == 1 else values), range=None,
            releasable=flags[i - 1],
        )
        for i, (name, values) in enumerate(_PARAMS, 1)
    ]
    summary = se.preview_schema_edit(db, family_id=world.family.id, drafts=drafts)
    se.apply_schema_edit(
        db, family_id=world.family.id, drafts=drafts, preview_hash=summary.preview_hash,
        actor_id=_admin(factories).id,
    )


def _edit_definition(db, factories, world):
    update_family(
        db, family_id=world.family.id, definition="Новое определение семьи",
        actor_id=_admin(factories).id,
    )


def _edit_unit(db, factories, world):
    set_unit(db, family_id=world.family.id, unit_name="м3", actor_id=_admin(factories).id)


def _edit_title(db, factories, world):
    update_family(
        db, family_id=world.family.id, title=f"Другое имя {_uid()}", actor_id=_admin(factories).id,
    )


EDITS = [
    pytest.param(_edit_schema, id="schema_edit"),
    pytest.param(_edit_definition, id="update_family_definition"),
    pytest.param(_edit_title, id="update_family_title"),
    pytest.param(_edit_unit, id="set_unit"),
]


class TestHygieneOfOrdinaryEdits:
    @pytest.mark.parametrize("edit", EDITS)
    def test_stale_proposals_are_dropped_and_a_new_job_is_queued(self, db_session, factories, edit):
        world = _deferred(db_session, factories)
        old_job = _jobs(db_session)[-1]

        edit(db_session, factories, world)

        assert _proposals(db_session, world) == []
        jobs = _jobs(db_session, world.schema.id)
        assert [j.status for j in jobs] == ["done", "pending"]
        assert jobs[-1].request_hash == _hash_now(db_session, world) != old_job.request_hash

    @pytest.mark.parametrize("edit", EDITS)
    def test_an_unfinished_job_is_cancelled_as_input_changed_and_a_new_one_queued(
        self, db_session, factories, edit
    ):
        world, claim = _running(db_session, factories)

        edit(db_session, factories, world)

        jobs = _jobs(db_session, world.schema.id)
        assert [(j.status, j.cancel_reason, j.claim_token) for j in jobs] == [
            ("cancelled", "input_changed", None), ("pending", None, None),
        ]
        assert jobs[0].id == claim.job_id
        assert jobs[1].request_hash == _hash_now(db_session, world)

    @pytest.mark.parametrize("edit", EDITS)
    def test_a_pending_job_with_the_old_hash_is_cancelled_too(self, db_session, factories, edit):
        world = _queued(db_session, factories)

        edit(db_session, factories, world)

        statuses = [(j.status, j.cancel_reason) for j in _jobs(db_session, world.schema.id)]
        assert statuses == [("cancelled", "input_changed"), ("pending", None)]

    @pytest.mark.parametrize("edit", EDITS)
    def test_no_new_job_when_every_main_parameter_is_marked(self, db_session, factories, edit):
        world = _queued(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id.in_(world.ids))
            .values(releasable=True, origin="human")
        )

        edit(db_session, factories, world)

        statuses = [(j.status, j.cancel_reason) for j in _jobs(db_session, world.schema.id)]
        assert statuses == [("cancelled", "input_changed")]

    def test_an_edit_that_changes_nothing_keeps_the_job_and_the_proposals(
        self, db_session, factories
    ):
        world = _deferred(db_session, factories)

        update_family(
            db_session, family_id=world.family.id, title=world.family.title,
            actor_id=_admin(factories).id,
        )

        assert len(_proposals(db_session, world)) == 2
        assert [j.status for j in _jobs(db_session, world.schema.id)] == ["done"]

    def test_reverting_the_input_revives_the_cancelled_job_instead_of_leaving_nothing(
        self, db_session, factories
    ):
        world = _queued(db_session, factories)
        first = _jobs(db_session)[-1]
        first_id, first_generation = first.id, first.retry_generation
        admin = _admin(factories)

        update_family(db_session, family_id=world.family.id, definition="Иное", actor_id=admin.id)
        update_family(db_session, family_id=world.family.id, definition="Стяжка пола",
                      actor_id=admin.id)

        jobs = _jobs(db_session, world.schema.id)
        live = [j for j in jobs if j.status == "pending"]
        assert [j.id for j in live] == [first_id]
        assert live[0].request_hash == _hash_now(db_session, world)
        assert live[0].retry_generation == first_generation + 1

    def test_after_a_decision_an_edit_still_asks_about_the_other_parameter(
        self, db_session, factories
    ):
        world = _deferred(db_session, factories)
        decide_releasable(
            db_session, world.family.id, world.ids[0], True, request_hash=world.old_hash,
            actor_id=_admin(factories).id,
        )

        _edit_definition(db_session, factories, world)

        assert _flags(db_session, world) == [True, None]
        assert _jobs(db_session, world.schema.id)[-1].status == "pending"  # второй параметр


def _merge_values(db, factories, world):
    values = list(
        db.execute(
            sa.select(FamilyParameterValue.id)
            .where(FamilyParameterValue.parameter_id == world.ids[0])
            .order_by(FamilyParameterValue.id)
        ).scalars()
    )
    merge_parameter_values(
        db, parameter_id=world.ids[0], source_value_id=values[0], target_value_id=values[1],
        actor_id=_admin(factories).id,
    )


def _extend_values(db, factories, world):
    """Ответ `context_values` дописывает новое значение первому параметру."""
    _apply_values(
        db, world.values_world,
        _values_answer((1, "new", "70 мм", "name"), _named(2, "бетон", "path")),
    )


def _merge_scene(db, factories):
    return _queued(db, factories)


def _extend_scene(db, factories):
    """Мир `apply_values`: активная семья, контекст и параметры по умолчанию."""
    values_world = _values_world(db, factories)
    db.execute(
        sa.update(WorkFamily).where(WorkFamily.id == values_world.family.id)
        .values(status="active")
    )
    ids = list(
        db.execute(
            sa.select(FamilyParameter.id)
            .where(FamilyParameter.schema_id == values_world.schema.id)
            .order_by(FamilyParameter.ordinal)
        ).scalars()
    )
    world = SimpleNamespace(
        family=values_world.family, schema=values_world.schema, ids=ids,
        values_world=values_world,
    )
    assert enqueue_releasable(db, world.schema.id, settings=S) is True
    world.job_id = _jobs(db)[-1].id
    return world


VALUE_PATHS = [
    pytest.param(_merge_values, _merge_scene, id="merge_parameter_values"),
    pytest.param(_extend_values, _extend_scene, id="apply_values_new_value"),
]


class TestHygieneOfValuePaths:
    @pytest.mark.parametrize(("edit", "build"), VALUE_PATHS)
    def test_stale_proposals_are_dropped_and_a_new_job_is_queued(
        self, db_session, factories, edit, build
    ):
        world = build(db_session, factories)
        old_hash = _hash_now(db_session, world)
        for parameter_id in world.ids:
            db_session.add(
                ReleasableProposal(
                    parameter_id=parameter_id, releasable=True, confidence=Decimal("0.6"),
                    reason="р", request_hash=old_hash,
                )
            )
        db_session.flush()
        assert len(_proposals(db_session, world)) == 2

        edit(db_session, factories, world)

        assert _proposals(db_session, world) == []
        jobs = _jobs(db_session, world.schema.id)
        assert jobs[-1].status == "pending"
        assert jobs[-1].request_hash == _hash_now(db_session, world) != old_hash

    @pytest.mark.parametrize(("edit", "build"), VALUE_PATHS)
    def test_an_unfinished_job_is_cancelled_as_input_changed_and_a_new_one_queued(
        self, db_session, factories, edit, build
    ):
        world = build(db_session, factories)
        claim = _claim(db_session)
        old_hash = claim.rendered.request_hash
        assert _reload(db_session, SemanticJob, claim.job_id).status == "running"
        assert old_hash == _hash_now(db_session, world)

        edit(db_session, factories, world)

        jobs = _jobs(db_session, world.schema.id)
        assert [(j.id, j.status, j.cancel_reason, j.claim_token) for j in jobs[:1]] == [
            (claim.job_id, "cancelled", "input_changed", None)
        ]
        assert jobs[-1].status == "pending"
        assert jobs[-1].request_hash == _hash_now(db_session, world) != old_hash


class TestVersionLeavesFrozen:
    def test_freezing_a_new_version_retires_the_old_proposals_and_jobs(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        old = world.current
        old_parameter = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == old.id)
        ).scalar_one()
        assert enqueue_releasable(db_session, old.id, settings=S) is True
        db_session.add(
            ReleasableProposal(
                parameter_id=old_parameter.id, releasable=True, confidence=Decimal("0.5"),
                reason="р", request_hash="h",
            )
        )
        db_session.flush()

        assert _freeze(db_session, world).applied is True

        db_session.expire_all()
        assert _proposals(db_session) == []
        old_jobs = _jobs(db_session, old.id)
        assert [(j.status, j.cancel_reason) for j in old_jobs] == [("cancelled", "not_applicable")]

    def test_freezing_queues_a_job_for_the_new_version(self, db_session, factories):
        world = _freeze_world(db_session, factories)

        assert _freeze(db_session, world).applied is True

        jobs = _jobs(db_session, world.building.id)
        assert [j.status for j in jobs] == ["pending"]
        assert jobs[0].family_id == world.family.id
        assert jobs[0].request_hash == _hash_now(
            db_session, SimpleNamespace(family=world.family, schema=world.building)
        )

    def test_a_version_without_parameters_gets_no_job(self, db_session, factories):
        world = _freeze_world(db_session, factories)

        assert _freeze(db_session, world, parameters=()).applied is True

        assert _jobs(db_session, world.building.id) == []

    def test_a_failed_freeze_queues_nothing(self, db_session, factories):
        world = _freeze_world(db_session, factories, family_status="draft")

        outcome = _freeze(db_session, world)

        assert outcome.applied is False
        assert _jobs(db_session) == []

    def test_a_human_parameter_keeps_its_flag_a_model_parameter_does_not_and_a_job_is_queued(
        self, db_session, factories
    ):
        world = _freeze_world(db_session, factories)
        human = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == world.current.id)
        ).scalar_one()
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == human.id)
            .values(origin="human", releasable=True)
        )
        model_parameter = FamilyParameter(
            schema_id=world.current.id, ordinal=2, name="Модельный", name_norm="модельный",
            origin="model", releasable=False,
        )
        db_session.add(model_parameter)
        db_session.flush()

        assert _freeze(db_session, world).applied is True

        db_session.expire_all()
        carried = db_session.execute(
            sa.select(FamilyParameter.name, FamilyParameter.releasable, FamilyParameter.origin)
            .where(FamilyParameter.schema_id == world.building.id)
            .order_by(FamilyParameter.ordinal)
        ).all()
        assert [tuple(row) for row in carried] == [
            ("Старый параметр", True, "human"),
            ("Толщина", None, "model"),
            ("Материал", None, "model"),
        ]
        assert [j.status for j in _jobs(db_session, world.building.id)] == ["pending"]

    def test_merging_a_family_retires_the_proposals_of_its_versions(self, db_session, factories):
        source = _deferred(db_session, factories, unit=None)
        target = _world(db_session, factories, unit=None)
        assert len(_proposals(db_session, source)) == 2

        merge_families(
            db_session, source_family_id=source.family.id, target_family_id=target.family.id,
            actor_id=_admin(factories).id,
        )

        assert _proposals(db_session, source) == []
        assert _reload(db_session, FamilyParameterSchema, source.schema.id).status == "superseded"

    def test_merging_a_family_cancels_the_unfinished_job_of_its_version(
        self, db_session, factories
    ):
        source = _queued(db_session, factories, unit=None)
        target = _world(db_session, factories, unit=None)

        merge_families(
            db_session, source_family_id=source.family.id, target_family_id=target.family.id,
            actor_id=_admin(factories).id,
        )

        (job,) = _jobs(db_session, source.schema.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")


# ---------------------------------------------------------------------------
#  Постановка
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_it_is_idempotent(self, db_session, factories):
        world = _world(db_session, factories)

        assert enqueue_releasable(db_session, world.schema.id, settings=S) is True
        assert enqueue_releasable(db_session, world.schema.id, settings=S) is False

        (job,) = _jobs(db_session, world.schema.id)
        assert (job.family_id, job.schema_id, job.status) == (
            world.family.id, world.schema.id, "pending",
        )
        assert job.request_hash == _hash_now(db_session, world)
        assert job.model_requested == S.SEMANTIC_SCHEMA_MODEL

    def test_a_done_job_with_the_current_hash_is_not_asked_again(self, db_session, factories):
        world = _deferred(db_session, factories)

        assert enqueue_releasable(db_session, world.schema.id, settings=S) is False
        assert [j.status for j in _jobs(db_session, world.schema.id)] == ["done"]

    def test_nothing_is_queued_when_all_main_parameters_are_marked(self, db_session, factories):
        world = _world(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id.in_(world.ids))
            .values(releasable=False, origin="human")
        )

        assert enqueue_releasable(db_session, world.schema.id, settings=S) is False
        assert _jobs(db_session) == []

    def test_a_version_that_is_not_current_is_not_queued(self, db_session, factories):
        world = _world(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameterSchema).where(FamilyParameterSchema.id == world.schema.id)
            .values(status="superseded", superseded_at=sa.func.now())
        )

        assert enqueue_releasable(db_session, world.schema.id, settings=S) is False

    def test_the_claim_of_a_job_with_nothing_left_to_ask_is_not_applicable(
        self, db_session, factories
    ):
        world = _queued(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id.in_(world.ids))
            .values(releasable=True, origin="human")
        )
        job = _reload(db_session, SemanticJob, world.job_id)

        render = render_job_request(db_session, job, settings=S)

        assert render.rendered is None and render.cancel_reason.value == "not_applicable"

    def test_the_error_queue_lists_the_job_with_the_family_title(self, db_session, factories):
        world, claim = _running(db_session, factories)
        _record(db_session, claim, "не JSON")

        items = queue.list_jobs(db_session, status="error")["items"]

        (item,) = items
        assert (item["kind"], item["family_id"], item["schema_id"], item["title"]) == (
            "releasable_params", world.family.id, world.schema.id, world.family.title,
        )


# ---------------------------------------------------------------------------
#  Правка схемы: поле параметра
# ---------------------------------------------------------------------------

def _drafts(world, *, releasable=UNSET, role=ParameterRole.main):
    out = []
    for i, (name, values) in enumerate(_PARAMS, 1):
        out.append(
            se.ParameterDraft(
                ordinal=i, name=name, role=role if i == 1 else ParameterRole.main,
                value_kind=ParameterValueKind.exact, closed=False, values=tuple(values),
                range=None, releasable=releasable if i == 1 else UNSET,
            )
        )
    return out


def _apply(db, factories, world, drafts):
    summary = se.preview_schema_edit(db, family_id=world.family.id, drafts=drafts)
    se.apply_schema_edit(
        db, family_id=world.family.id, drafts=drafts, preview_hash=summary.preview_hash,
        actor_id=_admin(factories).id,
    )
    return summary


class TestSchemaEditField:
    def test_the_flag_in_the_body_is_a_human_decision_and_bumps_the_revision(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        revision = world.schema.revision

        _apply(db_session, factories, world, _drafts(world, releasable=True))

        db_session.expire_all()
        parameter = db_session.get(FamilyParameter, world.ids[0])
        assert (parameter.releasable, parameter.origin) == (True, "human")
        assert db_session.get(FamilyParameterSchema, world.schema.id).revision == revision + 1
        assert _flags(db_session, world) == [True, None]

    def test_the_flag_changes_the_preview_hash(self, db_session, factories):
        world = _world(db_session, factories)

        hashes = {
            se.preview_schema_edit(
                db_session, family_id=world.family.id, drafts=_drafts(world, releasable=value)
            ).preview_hash
            for value in (None, True, False)
        }

        assert len(hashes) == 3

    def test_clearing_the_flag_is_an_edit(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))

        _apply(db_session, factories, world, _drafts(world, releasable=None))

        assert _flags(db_session, world) == [None, None]
        assert _reload(db_session, FamilyParameter, world.ids[0]).origin == "human"

    def test_a_body_without_the_field_keeps_the_flag_and_the_origin(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=False))
        revision = _reload(db_session, FamilyParameterSchema, world.schema.id).revision

        _apply(db_session, factories, world, _drafts(world))  # поле не прислано

        parameter = _reload(db_session, FamilyParameter, world.ids[0])
        assert (parameter.releasable, parameter.origin) == (False, "human")
        assert _reload(db_session, FamilyParameterSchema, world.schema.id).revision == revision

    def test_a_body_without_the_field_does_not_touch_a_model_parameters_origin(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[1])
            .values(releasable=True)
        )
        revision = _reload(db_session, FamilyParameterSchema, world.schema.id).revision

        _apply(db_session, factories, world, _drafts(world))

        parameter = _reload(db_session, FamilyParameter, world.ids[1])
        assert (parameter.releasable, parameter.origin) == (True, "model")
        assert _reload(db_session, FamilyParameterSchema, world.schema.id).revision == revision

    def test_the_preview_hash_of_a_body_without_the_field_equals_the_hash_with_the_current_value(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))

        def preview(drafts):
            return se.preview_schema_edit(
                db_session, family_id=world.family.id, drafts=drafts
            ).preview_hash

        current = [
            replace(d, releasable=flag)
            for d, flag in zip(_drafts(world), _flags(db_session, world), strict=True)
        ]
        assert preview(_drafts(world)) == preview(current)
        assert preview(_drafts(world)) != preview(_drafts(world, releasable=False))

    def test_an_explicit_null_is_a_human_decision(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(origin="model")
        )

        _apply(db_session, factories, world, _drafts(world, releasable=None))

        parameter = _reload(db_session, FamilyParameter, world.ids[0])
        assert (parameter.releasable, parameter.origin) == (None, "human")

    def test_a_new_parameter_without_the_field_is_not_marked(self, db_session, factories):
        world = _world(db_session, factories)
        drafts = [
            *_drafts(world),
            se.ParameterDraft(
                ordinal=None, name="Новый", role=ParameterRole.main,
                value_kind=ParameterValueKind.exact, closed=False, values=("а",), range=None,
            ),
        ]

        _apply(db_session, factories, world, drafts)

        created = db_session.execute(
            sa.select(FamilyParameter.releasable).where(
                FamilyParameter.schema_id == world.schema.id, FamilyParameter.name == "Новый"
            )
        ).scalar_one()
        assert created is None

    def test_the_route_reads_a_missing_field_as_keep_and_null_as_clear(
        self, admin_client, db_session, factories
    ):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))
        db_session.commit()
        url = f"{BASE}/families/{world.family.id}/schema"

        def edit(first):
            body = {"parameters": [
                {"ordinal": 1, "name": "Толщина", "values": ["50 мм", "80 мм"], **first},
                {"ordinal": 2, "name": "Армирование", "values": ["сетка", "фибра"]},
            ]}
            preview = admin_client.post(f"{url}/edit-preview", json=body).json()["preview_hash"]
            return admin_client.patch(url, json={**body, "preview_hash": preview})

        assert edit({}).json()["parameters"][0]["releasable"] is True
        assert edit({"releasable": None}).json()["parameters"][0]["releasable"] is None

    def test_a_flag_only_edit_recomputes_no_variants_and_asks_no_values(
        self, db_session, factories, monkeypatch
    ):
        world = _world(db_session, factories)
        calls = []
        monkeypatch.setattr(se, "recompute_variants", lambda *a, **k: calls.append(1))

        summary = _apply(db_session, factories, world, _drafts(world, releasable=True))

        assert calls == []
        assert summary.variants_before == summary.variants_after_now
        assert summary.asked_by_parameter == ()

    def test_an_unchanged_flag_is_not_an_edit(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))
        revision = _reload(db_session, FamilyParameterSchema, world.schema.id).revision

        _apply(db_session, factories, world, _drafts(world, releasable=True))

        assert _reload(db_session, FamilyParameterSchema, world.schema.id).revision == revision

    def test_demoting_a_main_parameter_clears_the_flag(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))

        _apply(
            db_session, factories, world,
            _drafts(world, releasable=True, role=ParameterRole.secondary),
        )

        db_session.expire_all()
        parameter = db_session.get(FamilyParameter, world.ids[0])
        assert (parameter.role, parameter.releasable) == ("secondary", None)

    def test_a_new_parameter_is_created_with_its_flag(self, db_session, factories):
        world = _world(db_session, factories)
        drafts = [
            *_drafts(world),
            se.ParameterDraft(
                ordinal=None, name="Новый", role=ParameterRole.main,
                value_kind=ParameterValueKind.exact, closed=False, values=("а",), range=None,
                releasable=False,
            ),
        ]

        _apply(db_session, factories, world, drafts)

        created = db_session.execute(
            sa.select(FamilyParameter.releasable).where(
                FamilyParameter.schema_id == world.schema.id, FamilyParameter.name == "Новый"
            )
        ).scalar_one()
        assert created is False

    def test_a_value_only_edit_of_a_human_parameter_writes_no_empty_update(
        self, db_session, factories
    ):
        # Параметр человека с одной правкой значений: полей параметра менять нечего,
        # `UPDATE family_parameters SET` без столбцов — синтаксическая ошибка
        # (`schema_edit._write_plan`, `if values:`).
        world = _world(db_session, factories)
        _apply(db_session, factories, world, _drafts(world, releasable=True))
        db_session.expire_all()
        assert db_session.get(FamilyParameter, world.ids[0]).origin == "human"
        drafts = _drafts(world, releasable=True)
        drafts[0] = se.ParameterDraft(
            ordinal=1, name=_PARAMS[0][0], role=ParameterRole.main,
            value_kind=ParameterValueKind.exact, closed=False,
            values=(*_PARAMS[0][1], "120 мм"), range=None, releasable=True,
        )

        _apply(db_session, factories, world, drafts)

        db_session.expire_all()
        values = db_session.execute(
            sa.select(FamilyParameterValue.value)
            .where(FamilyParameterValue.parameter_id == world.ids[0])
            .order_by(FamilyParameterValue.id)
        ).scalars().all()
        assert values == [*_PARAMS[0][1], "120 мм"]
        assert _flags(db_session, world) == [True, None]

    def test_the_change_is_named_in_the_event(self, db_session, factories):
        world = _world(db_session, factories)

        _apply(db_session, factories, world, _drafts(world, releasable=True))

        payload = db_session.execute(
            sa.text(
                "select payload from semantic_events where event_type = 'family_schema_edited'"
                " order by id desc limit 1"
            )
        ).scalar_one()
        (change,) = payload["changes"]
        assert change["releasable"] == {"from": None, "to": True}


# ---------------------------------------------------------------------------
#  Замки
# ---------------------------------------------------------------------------

class TestLockOrder:
    def test_lock_inputs_takes_family_then_version_then_parameters_all_for_share(
        self, db_session, factories
    ):
        world = _world(db_session, factories)

        with _capturing_sql(db_session) as statements:
            lock_inputs(db_session, world.family.id, world.schema.id)

        assert _lock_sequence(statements) == [
            ("work_families", "SHARE"),
            ("family_parameter_schemas", "SHARE"),
            ("family_parameters", "SHARE"),
        ]

    def test_record_result_takes_the_inputs_before_the_job(self, db_session, factories):
        world, claim = _running(db_session, factories)

        with _capturing_sql(db_session) as statements:
            _record(db_session, claim, _answer(world))

        sequence = _lock_sequence(statements)
        assert sequence[:4] == [
            ("work_families", "SHARE"),
            ("family_parameter_schemas", "SHARE"),
            ("family_parameters", "SHARE"),
            ("semantic_jobs", "UPDATE"),
        ]

    def test_a_decision_takes_the_inputs_before_the_proposal(self, db_session, factories):
        world = _deferred(db_session, factories)

        with _capturing_sql(db_session) as statements:
            decide_releasable(
                db_session, world.family.id, world.ids[0], True, request_hash=world.old_hash,
                actor_id=_admin(factories).id,
            )

        # Версия берётся `FOR NO KEY UPDATE` (решение повышает `revision`), поэтому в
        # последовательность `FOR UPDATE`/`FOR SHARE` она не попадает: проверяется отдельно.
        assert _lock_sequence(statements)[:3] == [
            ("work_families", "SHARE"),
            ("family_parameters", "SHARE"),
            ("releasable_proposals", "UPDATE"),
        ]
        locks = []
        for text in statements:
            mode = re.search(r"FOR (NO KEY UPDATE|UPDATE|SHARE)\s*$", text.strip())
            table = re.search(r"\bFROM (\w+)", text)
            if mode and table:
                locks.append((table.group(1), f"FOR {mode.group(1)}"))
        assert locks[:4] == [
            ("work_families", "FOR SHARE"),
            ("family_parameter_schemas", "FOR NO KEY UPDATE"),
            ("family_parameters", "FOR SHARE"),
            ("releasable_proposals", "FOR UPDATE"),
        ]


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_list_returns_live_proposals_with_family_parameter_and_hash(
        self, admin_client, db_session, factories
    ):
        world = _deferred(db_session, factories)
        db_session.commit()

        response = admin_client.get(f"{BASE}/releasable-proposals")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        first = body["items"][0]
        assert first["family"] == {"id": world.family.id, "title": world.family.title}
        assert first["parameter"] == {"id": world.ids[0], "name": "Толщина"}
        assert first["values"] == ["50 мм", "80 мм"]
        assert (first["releasable"], first["confidence"], first["request_hash"]) == (
            True, "0.6", world.old_hash,
        )

    def test_list_hides_a_stale_proposal(self, admin_client, db_session, factories):
        world = _deferred(db_session, factories)
        _m_family_definition(db_session, world)
        db_session.commit()

        body = admin_client.get(f"{BASE}/releasable-proposals").json()

        assert body == {"items": [], "total": 0}

    def test_list_pages(self, admin_client, db_session, factories):
        _deferred(db_session, factories)
        db_session.commit()

        assert admin_client.get(f"{BASE}/releasable-proposals", params={"page": 2}).json()[
            "items"
        ] == []
        assert admin_client.get(f"{BASE}/releasable-proposals", params={"page": 0}).status_code == 422

    def test_put_decides_and_returns_204(self, admin_client, db_session, factories):
        world = _deferred(db_session, factories)
        db_session.commit()

        response = admin_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable",
            json={"releasable": True, "request_hash": world.old_hash},
        )

        assert response.status_code == 204
        assert _flags(db_session, world) == [True, None]
        parameter = _reload(db_session, FamilyParameter, world.ids[0])
        assert parameter.origin == "human"

    def test_put_without_the_hash_is_a_plain_decision(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        db_session.commit()

        response = admin_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[1]}/releasable",
            json={"releasable": False},
        )

        assert response.status_code == 204
        assert _flags(db_session, world) == [None, False]

    def test_put_stale_is_409_with_the_code(self, admin_client, db_session, factories):
        world = _deferred(db_session, factories)
        _m_family_title(db_session, world)
        db_session.commit()

        response = admin_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable",
            json={"releasable": True, "request_hash": world.old_hash},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "proposal_stale"
        assert _flags(db_session, world) == [None, None]

    def test_put_secondary_is_422_and_unknown_is_404(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.ids[0])
            .values(role="secondary")
        )
        db_session.commit()
        url = f"{BASE}/families/{world.family.id}/parameters/%s/releasable"

        not_main = admin_client.put(url % world.ids[0], json={"releasable": True})
        unknown = admin_client.put(url % 987654321, json={"releasable": True})

        assert not_main.status_code == 422
        assert not_main.json()["detail"]["code"] == "parameter_not_main"
        assert unknown.status_code == 404

    def test_put_requires_the_flag(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        db_session.commit()

        response = admin_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable", json={}
        )

        assert response.status_code == 422

    def test_member_is_refused_on_both_routes(self, member_client, db_session, factories):
        world = _world(db_session, factories)
        db_session.commit()

        assert member_client.get(f"{BASE}/releasable-proposals").status_code == 403
        assert member_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable",
            json={"releasable": True},
        ).status_code == 403

    def test_schema_carries_the_flag_and_the_live_proposal(
        self, admin_client, db_session, factories
    ):
        world = _deferred(db_session, factories)
        decide_releasable(
            db_session, world.family.id, world.ids[0], False, request_hash=world.old_hash,
            actor_id=_admin(factories).id,
        )
        db_session.commit()

        body = admin_client.get(f"{BASE}/families/{world.family.id}/schema").json()

        first, second = body["parameters"]
        assert (first["releasable"], first["releasable_proposal"]) == (False, None)
        assert second["releasable"] is None
        assert second["releasable_proposal"] == {
            "releasable": True, "confidence": "0.6",
            "reason": "Разница по параметру мало меняет цену",
        }

    def test_schema_edit_route_accepts_the_flag(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        db_session.commit()
        body = {
            "parameters": [
                {"ordinal": 1, "name": "Толщина", "values": ["50 мм", "80 мм"], "releasable": True},
                {"ordinal": 2, "name": "Армирование", "values": ["сетка", "фибра"]},
            ]
        }
        preview = admin_client.post(f"{BASE}/families/{world.family.id}/schema/edit-preview", json=body)
        assert preview.status_code == 200

        response = admin_client.patch(
            f"{BASE}/families/{world.family.id}/schema",
            json={**body, "preview_hash": preview.json()["preview_hash"]},
        )

        assert response.status_code == 200
        assert [p["releasable"] for p in response.json()["parameters"]] == [True, None]

    def test_status_counts_only_live_proposals(self, admin_client, db_session, factories):
        live = _deferred(db_session, factories)
        stale = _deferred(db_session, factories)
        _m_family_definition(db_session, stale)
        db_session.commit()

        body = admin_client.get(f"{BASE}/status").json()

        assert body["releasable_open"] == 2
        assert live.family.id != stale.family.id

    def test_put_commits_the_deletion_of_a_stale_proposal_with_the_409(
        self, committing_client, committing_db, committing_factories
    ):
        from auth import get_current_user
        from main import app

        world = _deferred(committing_db, committing_factories)
        _m_family_definition(committing_db, world)
        admin = _admin(committing_factories)
        committing_db.commit()
        app.dependency_overrides[get_current_user] = lambda: admin

        response = committing_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable",
            json={"releasable": True, "request_hash": world.old_hash},
        )

        assert response.status_code == 409
        assert [p.parameter_id for p in _proposals(committing_db, world)] == [world.ids[1]]

    def test_put_commits_a_decision(self, committing_client, committing_db, committing_factories):
        from auth import get_current_user
        from main import app

        world = _deferred(committing_db, committing_factories)
        admin = _admin(committing_factories)
        committing_db.commit()
        app.dependency_overrides[get_current_user] = lambda: admin

        response = committing_client.put(
            f"{BASE}/families/{world.family.id}/parameters/{world.ids[0]}/releasable",
            json={"releasable": True, "request_hash": world.old_hash},
        )

        assert response.status_code == 204
        assert _flags(committing_db, world) == [True, None]


# ---------------------------------------------------------------------------
#  Команда releasable-enqueue-all
# ---------------------------------------------------------------------------

class TestEnqueueAllCommand:
    def _run(self, committing_session_factory, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)
        return CliRunner().invoke(cli.cli, ["releasable-enqueue-all"])

    def _seed(self, committing_db, committing_factories):
        queued = _world(committing_db, committing_factories)
        marked = _world(committing_db, committing_factories)
        committing_db.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id.in_(marked.ids))
            .values(releasable=True, origin="human")
        )
        committing_db.commit()
        return queued, marked

    def test_it_prints_the_count_and_the_estimate_before_queueing(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        queued, marked = self._seed(committing_db, committing_factories)

        result = self._run(committing_session_factory, monkeypatch)

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "К постановке заданий: 1" in result.output
        assert "оценка резерва" in result.output
        assert result.output.index("К постановке") < result.output.index("Заданий поставлено=1")
        assert [j.schema_id for j in _jobs(committing_db)] == [queued.schema.id]

    def test_the_second_run_queues_nothing(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        self._seed(committing_db, committing_factories)
        self._run(committing_session_factory, monkeypatch)

        result = self._run(committing_session_factory, monkeypatch)

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "К постановке заданий: 0" in result.output
        assert "Заданий поставлено=0" in result.output
        assert len(_jobs(committing_db)) == 1

    def test_it_checks_the_mutation_guard_first(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        reached = []

        def _record_call():
            reached.append(True)
            raise RuntimeError("stop-here")

        monkeypatch.setattr(cli, "SessionLocal", _record_call)
        CliRunner().invoke(cli.cli, ["releasable-enqueue-all"])

        assert reached


class TestEnqueueAllHoldsTheFamily:
    """`releasable-enqueue-all` идёт параллельно с приложением: постановка версии обязана держать
    семью `FOR UPDATE` от чтения материала до коммита (ревью Codex, P2). Барьер стоит ПОСЛЕ
    чтения материала: писатель входов другим соединением пробует взять семью, когда постановка
    уже прочла вход."""

    def test_the_family_lock_is_for_update(self):
        sql = str(releasable._lock_family_exclusive_statement(1).compile(dialect=sa.dialects.postgresql.dialect()))

        assert sql.rstrip().endswith("FOR UPDATE")

    def test_an_input_writer_cannot_take_the_family_between_the_read_and_the_enqueue(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        world = _world(committing_db, committing_factories)
        committing_db.commit()
        real = releasable.load_releasable_material
        outcomes: list[str] = []

        def read_then_try_to_write(db, family_id, schema_id):
            material = real(db, family_id, schema_id)
            with committing_session_factory() as writer:
                try:
                    writer.execute(sa.text("SET LOCAL lock_timeout = '300ms'"))
                    writer.execute(
                        sa.select(WorkFamily.id).where(WorkFamily.id == family_id).with_for_update()
                    ).all()
                    outcomes.append("took")
                except sa.exc.OperationalError:
                    outcomes.append("blocked")
                finally:
                    writer.rollback()
            return material

        monkeypatch.setattr(releasable, "load_releasable_material", read_then_try_to_write)

        report = releasable.enqueue_all_releasable(committing_db, settings=S)

        # первое чтение — подсчёт без замков, второе — постановка под замком семьи
        assert outcomes == ["took", "blocked"]
        assert report.enqueued == 1
        assert [j.schema_id for j in _jobs(committing_db)] == [world.schema.id]


def test_the_module_exposes_the_agreed_names():
    for name in (
        "render_releasable_request", "lock_inputs", "record_releasable_result",
        "current_proposals", "decide_releasable", "drop_stale_proposals", "enqueue_releasable",
        "ReleasableAnswer", "ProposalView", "CODE_PROPOSAL_STALE",
    ):
        assert hasattr(releasable, name), name
    assert SemanticJobKind.releasable_params.value == "releasable_params"


# ---------------------------------------------------------------------------
#  Массовое принятие предложений модели
# ---------------------------------------------------------------------------

def _deferred_false(db, factories):
    """Как `_deferred`, но модель ответила «обязателен» (false) без уверенности."""
    world, claim = _running(db, factories)
    world.old_hash = claim.rendered.request_hash
    _record(db, claim, _answer(world, releasable=False, confidence=0.6))
    return world


def _revision(db, world) -> int:
    db.expire_all()
    return db.get(FamilyParameterSchema, world.schema.id).revision


class TestAcceptAll:
    URL = f"{BASE}/releasable-proposals/accept-all"

    def test_accepts_every_live_proposal_as_a_human_decision(
        self, admin_client, db_session, factories
    ):
        yes = _deferred(db_session, factories)
        no = _deferred_false(db_session, factories)
        db_session.commit()
        before = (_revision(db_session, yes), _revision(db_session, no))

        response = admin_client.post(self.URL)

        assert response.status_code == 200
        assert response.json() == {"accepted": 4, "stale": 0, "failed": 0}
        assert _flags(db_session, yes) == [True, True]
        assert _flags(db_session, no) == [False, False]
        for world in (yes, no):
            for pid in world.ids:
                assert _reload(db_session, FamilyParameter, pid).origin == "human"
        assert (_revision(db_session, yes), _revision(db_session, no)) == (
            before[0] + 2, before[1] + 2,
        )
        assert _proposals(db_session) == []

    def test_a_proposal_stale_before_the_read_is_not_in_the_queue_and_the_rest_are_accepted(
        self, admin_client, db_session, factories
    ):
        fine = _deferred(db_session, factories)
        stale = _deferred(db_session, factories)
        _m_family_title(db_session, stale)
        db_session.commit()
        # Предусловие: у устаревшей семьи действующих предложений нет, поэтому в выборку
        # сводки она не попадает; устареть предложение должно МЕЖДУ выборкой и решением.
        assert {v.family_id for v in current_proposals(db_session, family_ids=None, page=None)} == {
            fine.family.id
        }

        response = admin_client.post(self.URL)

        assert response.json() == {"accepted": 2, "stale": 0, "failed": 0}
        assert _flags(db_session, stale) == [None, None]

    def test_a_proposal_that_went_stale_after_the_read_is_counted_and_removed(
        self, db_session, factories, monkeypatch
    ):
        fine = _deferred(db_session, factories)
        stale = _deferred(db_session, factories)
        db_session.commit()
        real = releasable.current_proposals

        def read_then_change(db, **kwargs):
            views = real(db, **kwargs)
            _m_family_title(db, stale)  # вход семьи изменён после чтения очереди, в обход уборки
            return views

        monkeypatch.setattr(releasable, "current_proposals", read_then_change)

        report = releasable.accept_all_proposals(db_session, actor_id=_admin(factories).id)

        assert report == {"accepted": 2, "stale": 2, "failed": 0}
        assert _flags(db_session, fine) == [True, True]
        assert _flags(db_session, stale) == [None, None]
        # недействующие строки удалены и удаление закоммичено
        db_session.rollback()
        assert _proposals(db_session, stale) == []

    def test_another_domain_error_fails_only_that_proposal(
        self, db_session, factories, monkeypatch
    ):
        world = _deferred(db_session, factories)
        db_session.commit()
        real = releasable.decide_releasable

        def refuse_first(db, family_id, parameter_id, *args, **kwargs):
            if parameter_id == world.ids[0]:
                raise DomainError(422, "отказ", code="parameter_not_main")
            return real(db, family_id, parameter_id, *args, **kwargs)

        monkeypatch.setattr(releasable, "decide_releasable", refuse_first)

        report = releasable.accept_all_proposals(db_session, actor_id=_admin(factories).id)

        assert report == {"accepted": 1, "stale": 0, "failed": 1}
        assert _flags(db_session, world) == [None, True]

    def test_empty_queue(self, admin_client, db_session):
        response = admin_client.post(self.URL)

        assert response.status_code == 200
        assert response.json() == {"accepted": 0, "stale": 0, "failed": 0}

    def test_member_is_refused(self, member_client, db_session, factories):
        world = _deferred(db_session, factories)
        db_session.commit()

        assert member_client.post(self.URL).status_code == 403
        assert _flags(db_session, world) == [None, None]

    def test_without_the_request_hash_the_stale_proposal_would_be_accepted(
        self, db_session, factories, monkeypatch
    ):
        """Контроль теста устаревшего: без `request_hash` устаревшее принимается."""
        stale = _deferred(db_session, factories)
        db_session.commit()
        real = releasable.current_proposals

        def read_then_change(db, **kwargs):
            views = real(db, **kwargs)
            _m_family_title(db, stale)
            return [replace(v, request_hash=None) for v in views]

        monkeypatch.setattr(releasable, "current_proposals", read_then_change)

        report = releasable.accept_all_proposals(db_session, actor_id=_admin(factories).id)

        assert report["accepted"] == 2 and report["stale"] == 0

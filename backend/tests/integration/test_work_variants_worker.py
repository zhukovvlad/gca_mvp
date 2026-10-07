"""Исполнитель очереди для заданий `family_schema` и `context_values`: захват по
виду, нулевая схема без вызова модели, запись результата через обработчики
ядра, задержанные и ошибки по предмету, пачки трёх видов (спека
`2026-10-02-catalog-variants-design.md` §2.3, §2.6, §2.7, §2.11).

Путь `family_suggestion` здесь не проверяется — его держат наборы фичи 2 без
правок. Каждая ветвь захвата и каждый исход записи — свой вход. Порядок
блокировок записи проверяется потоком SQL-операторов: задание берётся не
раньше доменных строк.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from crud.semantic_queue import list_jobs
from models import (
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticEvent,
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    SemanticWorkerState,
    WorkVariant,
)
from services.semantic_client import ModelResponse, PermanentModelError, TransientModelError
from services.semantic_decisions import (
    DecisionConflict,
    approve_batch,
    decline_privacy_hold,
    discard_batch,
    preview_batch,
    release_privacy_hold,
    release_unit_privacy_holds,
    retry_job,
)
from services.semantic_privacy import build_privacy_dictionary, find_privacy_matches
from services.semantic_reconcile import (
    Fingerprint,
    estimate_enqueue,
    fingerprints_hash,
    get_or_create_held_batch,
)
from services.semantic_request import load_request_material, render_context_request
from services.semantic_runner import recover_semantic_jobs
from services.variant_request import (
    load_schema_material,
    load_values_material,
    paths_hash_of,
    render_request_for,
    render_schema_request,
    render_values_request,
)
from tests.integration.test_work_variants_core import _DEFAULT_PARAMS, _world
from tests.integration.test_work_variants_material import (
    _bind,
    _capturing_sql,
    _chain_context,
    _frozen_schema,
    _uid,
)
from tests.integration.test_work_variants_reconcile import _job, _schema_hash
from tests.integration.test_work_variants_schema import (
    _batch,
    _family,
    _migration_0019,
    _schema,
)

pytestmark = pytest.mark.integration

#: Позже любого `now()` базы: задание, созданное в тесте, уже «созрело».
NOW = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.UTC)

#: Свои тарифы у каждого вида: резерв и preview обязаны считаться тарифами вида.
SUGGESTION_TARIFFS = (Decimal("2"), Decimal("2.5"), Decimal("0.2"), Decimal("10"))
SCHEMA_TARIFFS = (Decimal("3"), Decimal("4"), Decimal("0.3"), Decimal("20"))
VALUES_TARIFFS = (Decimal("5"), Decimal("6"), Decimal("0.5"), Decimal("30"))

S = app_settings.model_copy(
    update={
        "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
        "SEMANTIC_PRICE_INPUT_PER_M": SUGGESTION_TARIFFS[0],
        "SEMANTIC_PRICE_CACHE_WRITE_PER_M": SUGGESTION_TARIFFS[1],
        "SEMANTIC_PRICE_CACHE_READ_PER_M": SUGGESTION_TARIFFS[2],
        "SEMANTIC_PRICE_OUTPUT_PER_M": SUGGESTION_TARIFFS[3],
        "SEMANTIC_SCHEMA_PRICE_INPUT_PER_M": SCHEMA_TARIFFS[0],
        "SEMANTIC_SCHEMA_PRICE_CACHE_WRITE_PER_M": SCHEMA_TARIFFS[1],
        "SEMANTIC_SCHEMA_PRICE_CACHE_READ_PER_M": SCHEMA_TARIFFS[2],
        "SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M": SCHEMA_TARIFFS[3],
        "SEMANTIC_VALUES_PRICE_INPUT_PER_M": VALUES_TARIFFS[0],
        "SEMANTIC_VALUES_PRICE_CACHE_WRITE_PER_M": VALUES_TARIFFS[1],
        "SEMANTIC_VALUES_PRICE_CACHE_READ_PER_M": VALUES_TARIFFS[2],
        "SEMANTIC_VALUES_PRICE_OUTPUT_PER_M": VALUES_TARIFFS[3],
    }
)

KINDS = ["family_schema", "context_values"]

_SCHEMA_ANSWER = (
    '{"parameters": [{"ordinal": 1, "name": "Материал", "values": ["бетон", "кирпич"]}]}'
)
_VALUES_ANSWER = (
    '{"values": [{"ordinal": 1, "kind": "value", "value": "50 мм", "source": "name"},'
    ' {"ordinal": 2, "kind": "value", "value": "бетон", "source": "path"}]}'
)
#: Разбор принимает («», кавычки — непустая строка), запись отвергает: после
#: нормализации значение пусто.
_SCHEMA_ANSWER_EMPTY_AFTER_NORMALIZATION = (
    '{"parameters": [{"ordinal": 1, "name": "«»", "values": ["бетон"]}]}'
)
_VALUES_ANSWER_EMPTY_AFTER_NORMALIZATION = (
    '{"values": [{"ordinal": 1, "kind": "new", "value": "«»", "source": "name"},'
    ' {"ordinal": 2, "kind": "none", "value": null, "source": null}]}'
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _response(content, *, cost=Decimal("0.01")):
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=8274, completion_tokens=156, cache_write_tokens=8058, cached_tokens=0,
        cost_usd=cost,
    )


def _reserve(rendered, tariffs) -> Decimal:
    """Резерв по формуле спеки §2.11 при тарифах `(input, cache_write, cache_read,
    output)`; токены префикса — его байты (наблюдений нет)."""
    input_, cache_write, _read, output = tariffs
    return (
        Decimal(rendered.prefix_bytes) * cache_write
        + Decimal(rendered.user_bytes) * input_
        + Decimal(rendered.body["max_tokens"]) * output
    ) / Decimal(1_000_000)


def _schema_world(db, factories, *, family_status="active", schema_status="building", title=None):
    """Активная семья с версией `building`, контекстом (наименование уходит в
    запрос) и заданием `pending` на текущий отпечаток."""
    family = _family(db, status=family_status, definition="Стяжка пола")
    schema = _schema(db, factories, family, version=1, status=schema_status)
    context_id, _ = _chain_context(
        db, factories, title=title or f"Стяжка {_uid()}", path_specs=[((), 1)]
    )
    _bind(db, factories, context_id, family=family)
    job = _job(
        db, kind="family_schema", family_id=family.id, schema_id=schema.id,
        request_hash=_schema_hash(db, family.id, schema.id),
    )
    return SimpleNamespace(family=family, schema=schema, context_id=context_id, job=job)


def _values_world(db, factories, *, params=_DEFAULT_PARAMS, title=None, catalog_kind="TO_REVIEW"):
    """Контекст с текущей схемой семьи и заданием `pending` на текущий
    отпечаток."""
    world = _world(
        db, factories, params=params, catalog_kind=catalog_kind,
        title=title or f"Стяжка {_uid()}",
    )
    material = load_values_material(db, [world.context_id])[world.context_id]
    world.job = _job(
        db, kind="context_values", context_id=world.context_id, schema_id=world.schema.id,
        request_hash=render_values_request(material, settings=S).request_hash,
        paths_hash=paths_hash_of(material.paths),
    )
    return world


def _scene(kind, db, factories, **kwargs):
    if kind == "family_schema":
        return _schema_world(db, factories, **kwargs)
    return _values_world(db, factories, **kwargs)


def _answer_of(kind) -> str:
    return _SCHEMA_ANSWER if kind == "family_schema" else _VALUES_ANSWER


def _claim_one(db):
    claim = worker.claim_next(db, settings=S, now=NOW)
    assert claim is not None, "захват не состоялся"
    return claim


def _reload(db, job_id) -> SemanticJob:
    db.expire_all()
    return db.get(SemanticJob, job_id)


def _attempts(db, job_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJobAttempt)
            .where(SemanticJobAttempt.job_id == job_id)
            .order_by(SemanticJobAttempt.id)
        ).scalars()
    )


def _count(db, model, *where) -> int:
    db.expire_all()
    return db.execute(sa.select(sa.func.count()).select_from(model).where(*where)).scalar_one()


def _events(db, event_type) -> int:
    return _count(db, SemanticEvent, SemanticEvent.event_type == event_type)


def _first(statements, *needles):
    for index, statement in enumerate(statements):
        if all(needle in statement for needle in needles):
            return index
    return None


# ---------------------------------------------------------------------------
#  Захват нового вида: применимость, отпечаток, приватность, бюджет, резерв
# ---------------------------------------------------------------------------

def _make_inapplicable(kind, db, scene):
    if kind == "family_schema":
        scene.family.status = "archived"
    else:
        from models import CatalogContext

        db.get(CatalogContext, scene.context_id).archived_at = NOW
    db.flush()


class TestClaimNewKinds:
    @pytest.mark.parametrize("kind", KINDS)
    def test_claim_makes_the_job_running_and_writes_the_attempt(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        scene.job.retry_generation = 2
        db_session.flush()
        rendered = render_request_for(db_session, scene.job, settings=S)

        claim = _claim_one(db_session)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.attempts_in_generation) == ("running", 1)
        assert job.claim_token == claim.claim_token
        assert (claim.job_id, claim.kind.value, claim.synthesized) == (job.id, kind, False)
        assert claim.rendered == rendered
        assert claim.candidates == ()
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.id == claim.attempt_id
        assert (attempt.claim_token, attempt.retry_generation) == (claim.claim_token, 2)
        assert attempt.prefix_hash == rendered.prefix_hash
        assert attempt.privacy_dictionary_hash == build_privacy_dictionary(db_session).digest
        assert attempt.finished_at is None and attempt.reserve_exceeded is False

    @pytest.mark.parametrize(("kind", "tariffs"), [
        ("family_schema", SCHEMA_TARIFFS), ("context_values", VALUES_TARIFFS),
    ])
    def test_reserve_is_counted_with_the_tariffs_of_the_kind(
        self, db_session, factories, kind, tariffs
    ):
        scene = _scene(kind, db_session, factories)
        rendered = render_request_for(db_session, scene.job, settings=S)

        claim = _claim_one(db_session)

        (attempt,) = _attempts(db_session, scene.job.id)
        assert attempt.id == claim.attempt_id
        assert attempt.reserve_usd == _reserve(rendered, tariffs)
        # Тарифы предложений дали бы другое число: различие видимо в данных теста.
        assert _reserve(rendered, tariffs) != _reserve(rendered, SUGGESTION_TARIFFS)

    @pytest.mark.parametrize("kind", KINDS)
    def test_inapplicable_subject_cancels_the_job(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        _make_inapplicable(kind, db_session, scene)

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _attempts(db_session, job.id) == []

    @pytest.mark.parametrize("kind", KINDS)
    def test_changed_request_cancels_the_job_as_input_changed(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        scene.job.request_hash = "hash-of-another-request"
        db_session.flush()

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert _attempts(db_session, job.id) == []

    def test_schema_version_that_is_not_building_cancels_the_schema_job(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        scene.schema.status = "cancelled"
        scene.schema.cancelled_at = NOW
        db_session.flush()

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_values_job_of_a_context_on_a_header_row_is_not_applicable(self, db_session, factories):
        scene = _values_world(db_session, factories, catalog_kind="HEADER")

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_values_job_of_a_context_without_a_family_has_no_material(self, db_session, factories):
        scene = _values_world(db_session, factories)
        db_session.execute(
            sa.text(
                "UPDATE catalog_contexts SET work_family_id = NULL, family_source = NULL, "
                "family_by = NULL, family_at = NULL WHERE id = :i"
            ),
            {"i": scene.context_id},
        )

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_values_job_of_a_version_that_is_no_longer_current_is_input_changed(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        old = _schema(db_session, factories, scene.family, version=0, status="superseded")
        scene.job.schema_id = old.id
        db_session.flush()

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    @pytest.mark.parametrize("kind", KINDS)
    def test_privacy_match_puts_the_job_on_hold(self, db_session, factories, kind):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(kind, db_session, factories, title="Стяжка Ромашка Строй")

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert job.status == "privacy_hold"
        assert [(m["text"], m["kind"]) for m in job.privacy_matches] == [
            ("ромашка строй", "contractor")
        ]
        assert job.claim_token is None
        assert _attempts(db_session, job.id) == []

    @pytest.mark.parametrize("kind", KINDS)
    def test_released_privacy_set_lets_the_job_through(self, db_session, factories, kind):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(kind, db_session, factories, title="Стяжка Ромашка Строй")
        rendered = render_request_for(db_session, scene.job, settings=S)
        scene.job.privacy_released_matches = worker.serialize_privacy_matches(
            find_privacy_matches(build_privacy_dictionary(db_session), rendered)
        )
        db_session.flush()

        claim = _claim_one(db_session)

        assert claim.job_id == scene.job.id

    @pytest.mark.parametrize("kind", KINDS)
    def test_budget_that_does_not_cover_the_reserve_leaves_the_job_pending(
        self, db_session, factories, kind
    ):
        scene = _scene(kind, db_session, factories)
        poor = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": Decimal("0")})

        assert worker.claim_next(db_session, settings=poor, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.attempts_in_generation) == ("pending", 0)
        assert _attempts(db_session, job.id) == []


# ---------------------------------------------------------------------------
#  Синтетический захват нулевой схемы
# ---------------------------------------------------------------------------

class _CountingClient:
    def __init__(self):
        self.calls = 0

    def complete(self, body, *, timeout_s):
        self.calls += 1
        raise AssertionError("модель не должна вызываться")


class TestSyntheticClaim:
    def test_zero_parameter_schema_gives_a_synthetic_claim_without_attempt_or_reserve(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories, params=())

        claim = _claim_one(db_session)

        assert (claim.synthesized, claim.attempt_id, claim.rendered, claim.candidates) == (
            True, None, None, (),
        )
        assert (claim.job_id, claim.kind.value) == (scene.job.id, "context_values")
        job = _reload(db_session, scene.job.id)
        assert job.status == "running" and job.claim_token == claim.claim_token
        assert job.attempts_in_generation == 0
        assert _attempts(db_session, job.id) == []
        assert _count(db_session, SemanticJobAttempt) == 0

    def test_parameters_in_the_schema_give_an_ordinary_claim(self, db_session, factories):
        _values_world(db_session, factories)

        claim = _claim_one(db_session)

        assert (claim.synthesized, claim.attempt_id is not None, claim.rendered is not None) == (
            False, True, True,
        )

    def test_synthetic_claim_precedes_the_privacy_check(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _values_world(db_session, factories, params=(), title="Стяжка Ромашка Строй")

        claim = _claim_one(db_session)

        assert claim.synthesized is True
        assert _reload(db_session, scene.job.id).status == "running"

    def test_synthetic_claim_precedes_the_budget_check(self, db_session, factories):
        scene = _values_world(db_session, factories, params=())
        poor = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": Decimal("0")})

        claim = worker.claim_next(db_session, settings=poor, now=NOW)

        assert claim is not None and claim.synthesized is True
        assert _reload(db_session, scene.job.id).status == "running"

    def test_synthetic_claim_still_checks_the_fingerprint(self, db_session, factories):
        scene = _values_world(db_session, factories, params=())
        scene.job.request_hash = "hash-of-another-request"
        db_session.flush()

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    def test_paused_worker_does_not_claim_even_a_synthetic_job(self, db_session, factories):
        scene = _values_world(db_session, factories, params=())
        other = _values_world(db_session, factories)
        attempt = SemanticJobAttempt(
            job_id=other.job.id, claim_token=uuid.uuid4(), retry_generation=0, started_at=NOW,
            reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
        )
        db_session.add(attempt)
        db_session.flush()
        db_session.execute(
            sa.text(
                "UPDATE semantic_worker_state SET claim_paused = true, paused_reason = 'manual', "
                "paused_at = :at, paused_attempt_id = :aid WHERE id = 1"
            ),
            {"at": NOW, "aid": attempt.id},
        )

        assert worker.claim_next(db_session, settings=S, now=NOW) is None
        assert _reload(db_session, scene.job.id).status == "pending"


class TestCompleteWithoutModel:
    def test_runs_the_whole_values_protocol_without_attempt_or_model_call(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories, params=())
        claim = _claim_one(db_session)

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("done", None)
        assert _attempts(db_session, job.id) == []
        context = db_session.execute(
            sa.text("SELECT work_variant_id, variant_paths_hash FROM catalog_contexts WHERE id = :i"),
            {"i": scene.context_id},
        ).one()
        variant = db_session.get(WorkVariant, context.work_variant_id)
        assert (variant.values_key, variant.schema_id) == ("", scene.schema.id)
        assert context.variant_paths_hash == paths_hash_of(())
        assert _count(db_session, ContextParameterValue) == 0
        assert _events(db_session, "context_variant_assigned") == 1

    def test_precondition_is_a_synthetic_claim(self, db_session, factories):
        _values_world(db_session, factories)
        claim = _claim_one(db_session)

        with pytest.raises(ValueError):
            worker.complete_without_model(db_session, claim, now=NOW, settings=S)

    def test_a_claim_lost_meanwhile_leaves_the_job_of_the_new_owner_alone(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories, params=())
        claim = _claim_one(db_session)
        new_token = uuid.uuid4()
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == scene.job.id).values(claim_token=new_token)
        )

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("running", new_token)
        assert _count(db_session, WorkVariant) == 0
        assert _events(db_session, "context_variant_assigned") == 0

    def test_a_request_that_changed_meanwhile_cancels_the_job_as_input_changed(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories, params=())
        claim = _claim_one(db_session)
        from models import CatalogPosition

        db_session.get(CatalogPosition, scene.catalog_id).standard_job_title = f"Другое {_uid()}"
        db_session.flush()

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert _count(db_session, WorkVariant) == 0

    def test_completion_renders_with_the_settings_it_is_given(self, db_session, factories):
        """Отпечаток перепроверяется теми же настройками, что и у захвата: другая
        модель значений даёт другое тело, и настройки приложения вместо
        переданных отменили бы задание как устаревшее."""
        other = S.model_copy(update={"SEMANTIC_VALUES_MODEL": "anthropic/claude-other-model"})
        scene = _values_world(db_session, factories, params=())
        material = load_values_material(db_session, [scene.context_id])[scene.context_id]
        scene.job.request_hash = render_values_request(material, settings=other).request_hash
        db_session.flush()
        assert scene.job.request_hash != render_values_request(material, settings=S).request_hash
        claim = worker.claim_next(db_session, settings=other, now=NOW)
        assert claim is not None and claim.synthesized is True

        worker.complete_without_model(db_session, claim, now=NOW, settings=other)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("done", None)

    def test_process_one_does_not_park_a_job_claimed_by_someone_else_meanwhile(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """Завершение упало, а задание тем временем захватил другой исполнитель:
        перевод в `error` чужого захвата не трогает."""
        scene = _values_world(committing_db, committing_factories, params=())
        committing_db.commit()
        new_token = uuid.uuid4()

        def _reclaimed_then_boom(*args, **kwargs):
            with committing_session_factory() as other:
                other.execute(
                    sa.update(SemanticJob)
                    .where(SemanticJob.id == scene.job.id)
                    .values(claim_token=new_token)
                )
                other.commit()
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "apply_values", _reclaimed_then_boom)

        assert worker.process_one(
            committing_session_factory, _CountingClient(), settings=S, clock=lambda: NOW
        ) is True

        job = _reload(committing_db, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == ("running", new_token, None)

    def test_process_one_completes_a_zero_parameter_job_without_calling_the_model(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _values_world(committing_db, committing_factories, params=())
        committing_db.commit()
        client = _CountingClient()

        assert worker.process_one(
            committing_session_factory, client, settings=S, clock=lambda: NOW
        ) is True

        assert client.calls == 0
        job = _reload(committing_db, scene.job.id)
        assert job.status == "done"
        assert _attempts(committing_db, job.id) == []
        assert _events(committing_db, "context_variant_assigned") == 1

    def test_process_one_parks_a_job_whose_completion_failed_in_error(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        scene = _values_world(committing_db, committing_factories, params=())
        committing_db.commit()

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "apply_values", _boom)

        assert worker.process_one(
            committing_session_factory, _CountingClient(), settings=S, clock=lambda: NOW
        ) is True

        job = _reload(committing_db, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == ("error", None, "RuntimeError")


# ---------------------------------------------------------------------------
#  Запись результата family_schema
# ---------------------------------------------------------------------------

class TestRecordResultSchema:
    def test_applied_answer_freezes_the_version_and_closes_the_attempt_ok(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(
            db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("done", None)
        schema = db_session.get(FamilyParameterSchema, scene.schema.id)
        assert schema.status == "frozen"
        parameters = db_session.execute(
            sa.select(FamilyParameter.name).where(FamilyParameter.schema_id == scene.schema.id)
        ).scalars().all()
        assert parameters == ["Материал"]
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "ok" and attempt.finished_at == NOW
        assert attempt.raw_response == _SCHEMA_ANSWER and attempt.cost_usd == Decimal("0.01")
        assert _events(db_session, "family_schema_frozen") == 1

    def test_unparsable_answer_is_a_schema_error_of_the_owned_job(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(db_session, claim, _response("не json"), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == (
            "error", None, "schema_error",
        )
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_answer_empty_after_normalization_is_a_schema_error_and_writes_nothing(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(
            db_session, claim, _response(_SCHEMA_ANSWER_EMPTY_AFTER_NORMALIZATION),
            now=NOW, settings=S,
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"
        assert _count(db_session, FamilyParameter) == 0

    def test_changed_request_closes_the_job_as_input_changed_and_the_attempt_ok(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        scene.family.definition = "Другое определение"
        db_session.flush()

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )
        assert _attempts(db_session, job.id)[0].outcome == "ok"
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_version_that_left_building_closes_the_job_as_not_applicable(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        scene.schema.status = "cancelled"
        scene.schema.cancelled_at = NOW
        db_session.flush()

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _attempts(db_session, job.id)[0].outcome == "ok"
        assert _count(db_session, FamilyParameter) == 0

    def test_lost_claim_closes_only_the_own_attempt(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        new_token = uuid.uuid4()
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == scene.job.id).values(claim_token=new_token)
        )

        worker.record_result(db_session, claim, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("running", new_token)
        assert _attempts(db_session, job.id)[0].outcome == "lost_claim"
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_unparsable_answer_of_a_lost_claim_leaves_the_job_alone(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)
        new_token = uuid.uuid4()
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == scene.job.id).values(claim_token=new_token)
        )

        worker.record_result(db_session, claim, _response("не json"), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == ("running", new_token, None)
        assert _attempts(db_session, job.id)[0].outcome == "lost_claim"

    def test_duplicate_result_after_a_lost_claim_does_not_make_a_second_version(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        first = _claim_one(db_session)
        # Первый исполнитель «умер»: восстановление вернуло задание в очередь, его
        # подхватил второй и успел записать результат.
        recover_semantic_jobs(db_session, now=NOW)
        db_session.flush()
        second = _claim_one(db_session)
        assert second.claim_token != first.claim_token
        worker.record_result(db_session, second, _response(_SCHEMA_ANSWER), now=NOW, settings=S)
        frozen_state = (
            _count(db_session, FamilyParameterSchema, FamilyParameterSchema.status == "frozen"),
            _count(db_session, FamilyParameter),
            _count(db_session, FamilyParameterValue),
            _events(db_session, "family_schema_frozen"),
        )
        assert frozen_state == (1, 1, 2, 1)

        # Первый «оживает» и приносит тот же результат.
        worker.record_result(db_session, first, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        assert (
            _count(db_session, FamilyParameterSchema, FamilyParameterSchema.status == "frozen"),
            _count(db_session, FamilyParameter),
            _count(db_session, FamilyParameterValue),
            _events(db_session, "family_schema_frozen"),
        ) == frozen_state
        assert _reload(db_session, scene.job.id).status == "done"
        assert [a.outcome for a in _attempts(db_session, scene.job.id)] == ["lost_claim", "ok"]

    def test_result_of_the_old_owner_while_the_new_one_runs_is_a_lost_claim(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        first = _claim_one(db_session)
        recover_semantic_jobs(db_session, now=NOW)
        db_session.flush()
        second = _claim_one(db_session)

        worker.record_result(db_session, first, _response(_SCHEMA_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("running", second.claim_token)
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_cost_above_the_reserve_stops_the_claims(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(
            db_session, claim, _response(_SCHEMA_ANSWER, cost=Decimal("100")), now=NOW, settings=S
        )

        db_session.expire_all()
        state = db_session.get(SemanticWorkerState, 1)
        assert (state.claim_paused, state.paused_reason, state.paused_attempt_id) == (
            True, "reserve_exceeded", claim.attempt_id,
        )
        assert _attempts(db_session, scene.job.id)[0].reserve_exceeded is True


# ---------------------------------------------------------------------------
#  Запись результата context_values
# ---------------------------------------------------------------------------

class TestRecordResultValues:
    def test_applied_answer_assigns_the_variant_and_closes_the_attempt_ok(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(db_session, claim, _response(_VALUES_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("done", None)
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "ok" and attempt.raw_response == _VALUES_ANSWER
        assert _count(db_session, ContextParameterValue) == 2
        assert _count(db_session, WorkVariant) == 1
        assert _events(db_session, "context_variant_assigned") == 1

    def test_unparsable_answer_is_a_schema_error_of_the_owned_job(self, db_session, factories):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(db_session, claim, _response('{"values": []}'), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == (
            "error", None, "schema_error",
        )
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert _count(db_session, WorkVariant) == 0

    def test_value_that_is_not_in_the_list_of_the_job_version_is_a_schema_error(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)
        wrong = _VALUES_ANSWER.replace("50 мм", "70 мм")

        worker.record_result(db_session, claim, _response(wrong), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        assert _count(db_session, WorkVariant) == 0

    def test_own_job_is_done_before_the_extension_wave_so_the_wave_goes(
        self, db_session, factories
    ):
        """Задание переходит в `done` до шага (8) и волны: иначе собственное
        задание `running` выглядело бы открытым заданием семьи, и волна после
        расширения списка не пошла бы к контексту с пустым значением."""
        from tests.integration.test_work_variants_reconcile import _set_cpv, _wave_world

        world = _wave_world(db_session, factories, contexts=2)
        answering, waiting = world.contexts
        _set_cpv(db_session, world, waiting, None)
        material = load_values_material(db_session, [answering])[answering]
        own = _job(
            db_session, kind="context_values", context_id=answering, schema_id=world.schema.id,
            request_hash=render_values_request(material, settings=S).request_hash,
            paths_hash=paths_hash_of(material.paths),
        )
        claim = _claim_one(db_session)
        assert claim.job_id == own.id
        extension = (
            '{"values": [{"ordinal": 1, "kind": "new", "value": "70 мм", "source": "name"}]}'
        )

        worker.record_result(db_session, claim, _response(extension), now=NOW, settings=S)

        assert _reload(db_session, own.id).status == "done"
        db_session.expire_all()
        waves = db_session.execute(
            sa.select(SemanticJob.status).where(
                SemanticJob.kind == "context_values", SemanticJob.context_id == waiting
            )
        ).scalars().all()
        assert waves == ["pending"]

    def test_a_merged_value_is_not_in_the_list_the_answer_is_parsed_against(
        self, db_session, factories
    ):
        """Слитое значение в список версии для разбора не входит: ответ с ним
        отвергает сам разбор — до доменных блокировок дело не доходит."""
        scene = _values_world(db_session, factories)
        canonical = db_session.execute(
            sa.select(FamilyParameterValue)
            .join(FamilyParameter, FamilyParameter.id == FamilyParameterValue.parameter_id)
            .where(
                FamilyParameter.schema_id == scene.schema.id,
                FamilyParameterValue.value == "50 мм",
            )
        ).scalar_one()
        db_session.add(
            FamilyParameterValue(
                parameter_id=canonical.parameter_id, value="50мм", value_norm="50мм синоним",
                origin="schema", merged_into_id=canonical.id,
            )
        )
        db_session.flush()
        material = load_values_material(db_session, [scene.context_id])[scene.context_id]
        scene.job.request_hash = render_values_request(material, settings=S).request_hash
        db_session.flush()
        claim = _claim_one(db_session)

        with _capturing_sql(db_session) as statements:
            worker.record_result(
                db_session, claim, _response(_VALUES_ANSWER.replace("50 мм", "50мм")),
                now=NOW, settings=S,
            )

        assert _first(statements, "FROM catalog_positions", "FOR UPDATE") is None
        job = _reload(db_session, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        assert _count(db_session, WorkVariant) == 0

    def test_answer_empty_after_normalization_is_a_schema_error_and_writes_nothing(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(
            db_session, claim, _response(_VALUES_ANSWER_EMPTY_AFTER_NORMALIZATION),
            now=NOW, settings=S,
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.outcome == "schema_error" and attempt.validation_error
        assert _count(db_session, WorkVariant) == 0
        assert _count(db_session, ContextParameterValue) == 0
        assert _count(db_session, FamilyParameterValue) == 4  # список схемы не расширен

    def test_changed_request_closes_the_job_as_input_changed_and_the_attempt_ok(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)
        from models import CatalogPosition

        db_session.get(CatalogPosition, scene.catalog_id).standard_job_title = f"Другое {_uid()}"
        db_session.flush()

        worker.record_result(db_session, claim, _response(_VALUES_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )
        assert _attempts(db_session, job.id)[0].outcome == "ok"
        assert _count(db_session, WorkVariant) == 0

    def test_archived_context_closes_the_job_as_not_applicable(self, db_session, factories):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)
        from models import CatalogContext

        db_session.get(CatalogContext, scene.context_id).archived_at = NOW
        db_session.flush()

        worker.record_result(db_session, claim, _response(_VALUES_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _attempts(db_session, job.id)[0].outcome == "ok"
        assert _count(db_session, WorkVariant) == 0

    def test_lost_claim_closes_only_the_own_attempt(self, db_session, factories):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)
        new_token = uuid.uuid4()
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == scene.job.id).values(claim_token=new_token)
        )

        worker.record_result(db_session, claim, _response(_VALUES_ANSWER), now=NOW, settings=S)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token) == ("running", new_token)
        assert _attempts(db_session, job.id)[0].outcome == "lost_claim"
        assert _count(db_session, WorkVariant) == 0

    def test_cost_above_the_reserve_stops_the_claims(self, db_session, factories):
        scene = _values_world(db_session, factories)
        claim = _claim_one(db_session)

        worker.record_result(
            db_session, claim, _response(_VALUES_ANSWER, cost=Decimal("100")), now=NOW, settings=S
        )

        db_session.expire_all()
        state = db_session.get(SemanticWorkerState, 1)
        assert (state.claim_paused, state.paused_attempt_id) == (True, claim.attempt_id)
        assert _attempts(db_session, scene.job.id)[0].reserve_exceeded is True


class TestRecordResultTakesTheJobAfterTheDomain:
    @pytest.mark.parametrize(("kind", "domain_table"), [
        ("family_schema", "work_families"), ("context_values", "catalog_positions"),
    ])
    def test_the_job_is_locked_after_the_domain_rows(
        self, db_session, factories, kind, domain_table
    ):
        _scene(kind, db_session, factories)
        claim = _claim_one(db_session)

        with _capturing_sql(db_session) as statements:
            worker.record_result(
                db_session, claim, _response(_answer_of(kind)), now=NOW, settings=S
            )

        domain = _first(statements, f"FROM {domain_table}", "FOR UPDATE")
        job = _first(statements, "FROM semantic_jobs", "FOR UPDATE")
        assert domain is not None, "доменная строка не заблокирована"
        assert job is not None, "задание не заблокировано"
        assert domain < job, "задание заблокировано раньше доменной строки"

    @pytest.mark.parametrize(("kind", "bad_answer"), [
        ("family_schema", "не json"), ("context_values", '{"values": []}'),
    ])
    def test_a_parse_error_locks_the_job_and_nothing_of_the_domain(
        self, db_session, factories, kind, bad_answer
    ):
        _scene(kind, db_session, factories)
        claim = _claim_one(db_session)

        with _capturing_sql(db_session) as statements:
            worker.record_result(db_session, claim, _response(bad_answer), now=NOW, settings=S)

        assert _first(statements, "FROM semantic_jobs", "FOR UPDATE") is not None
        assert _first(statements, "FROM work_families", "FOR UPDATE") is None
        assert _first(statements, "FROM catalog_positions", "FOR UPDATE") is None

    def test_inner_schema_error_releases_the_domain_locks_before_the_job_is_taken(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """Ошибка схемы изнутри обработчика откатывает его точку сохранения: к
        моменту, когда путь схемной ошибки держит задание, семья уже свободна —
        запись не держит доменную строку дольше, чем нужно её решению."""
        scene = _schema_world(committing_db, committing_factories)
        committing_db.commit()
        with committing_session_factory() as db:
            claim = worker.claim_next(db, settings=S, now=NOW)
        assert claim is not None and claim.job_id == scene.job.id
        observed: list[str] = []
        original_fuse = worker._apply_fuse

        def _probe_then_fuse(db, attempt, *, now):
            with committing_session_factory() as other:
                try:
                    other.execute(
                        sa.text("SELECT id FROM work_families WHERE id = :f FOR UPDATE NOWAIT"),
                        {"f": scene.family.id},
                    )
                    observed.append("free")
                except sa.exc.OperationalError:
                    observed.append("locked")
                other.rollback()
            original_fuse(db, attempt, now=now)

        monkeypatch.setattr(worker, "_apply_fuse", _probe_then_fuse)

        with committing_session_factory() as db:
            worker.record_result(
                db, claim, _response(_SCHEMA_ANSWER_EMPTY_AFTER_NORMALIZATION), now=NOW, settings=S
            )

        assert observed == ["free"]
        job = _reload(committing_db, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")


# ---------------------------------------------------------------------------
#  record_failure для новых видов
# ---------------------------------------------------------------------------

class TestRecordFailureNewKinds:
    @pytest.mark.parametrize("kind", KINDS)
    def test_transient_error_returns_the_job_for_a_retry(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        claim = _claim_one(db_session)

        worker.record_failure(
            db_session, claim, TransientModelError("сеть", error_class="Timeout"),
            now=NOW, settings=S, rng=lambda: 0.0,
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.claim_token, job.last_error_class) == ("pending", None, "Timeout")
        assert job.next_attempt_at == NOW + dt.timedelta(seconds=30)
        assert _attempts(db_session, job.id)[0].outcome == "transient_error"

    @pytest.mark.parametrize("kind", KINDS)
    def test_permanent_error_parks_the_job_in_error(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        claim = _claim_one(db_session)

        worker.record_failure(
            db_session, claim, PermanentModelError("отказ", error_class="Refused"),
            now=NOW, settings=S,
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.last_error_class) == ("error", "Refused")

    def test_a_synthetic_claim_has_no_attempt_to_record_a_failure_on(self, db_session, factories):
        _values_world(db_session, factories, params=())
        claim = _claim_one(db_session)

        with pytest.raises(ValueError):
            worker.record_failure(
                db_session, claim, TransientModelError("x", error_class="X"), now=NOW, settings=S
            )

    def test_record_result_refuses_a_synthetic_claim(self, db_session, factories):
        _values_world(db_session, factories, params=())
        claim = _claim_one(db_session)

        with pytest.raises(ValueError):
            worker.record_result(db_session, claim, _response(_VALUES_ANSWER), now=NOW, settings=S)


# ---------------------------------------------------------------------------
#  Задержанные и ошибки по предмету
# ---------------------------------------------------------------------------

def _hold(kind, db, factories, *, title="Стяжка Ромашка Строй"):
    """Задание нового вида в `privacy_hold` с ТЕКУЩИМ набором совпадений."""
    factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
    scene = _scene(kind, db, factories, title=title)
    rendered = render_request_for(db, scene.job, settings=app_settings)
    shown = worker.serialize_privacy_matches(
        find_privacy_matches(build_privacy_dictionary(db), rendered)
    )
    assert shown, "набор совпадений пуст — проверка приватности не сработала"
    scene.job.status = "privacy_hold"
    scene.job.privacy_matches = shown
    db.flush()
    scene.shown = shown
    return scene


def _to_error(db, job):
    job.status = "error"
    job.last_error_class = "TimeoutError"
    db.flush()


class TestJobsListRows:
    def test_schema_job_row_is_by_the_family(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        # Номер версии, число наименований и число заданий различны: поле не
        # выдать одно за другое.
        second_id, _ = _chain_context(
            db_session, factories, title=f"Стяжка вторая {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, second_id, family=scene.family)
        scene.schema.version = 3
        _to_error(db_session, scene.job)
        db_session.add(
            SemanticJobAttempt(
                job_id=scene.job.id, claim_token=uuid.uuid4(), retry_generation=0, started_at=NOW,
                reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
                error_text="таймаут", finished_at=NOW, outcome="transient_error",
            )
        )
        db_session.flush()

        response = list_jobs(db_session, status="error")

        (row,) = response["items"]
        assert (row["job_id"], row["kind"], row["context_id"]) == (scene.job.id, "family_schema", None)
        assert (row["family_id"], row["schema_id"]) == (scene.family.id, scene.schema.id)
        assert (row["title"], row["schema_version"], row["names_count"]) == (
            scene.family.title, 3, 2,
        )
        assert (row["article"], row["path"], row["error_text"]) == (None, [], "таймаут")
        assert (row["status"], row["last_error_class"]) == ("error", "TimeoutError")

    def test_schema_job_row_in_privacy_hold_carries_the_matches(self, db_session, factories):
        scene = _hold("family_schema", db_session, factories)

        (row,) = list_jobs(db_session, status="privacy_hold")["items"]

        assert (row["kind"], row["context_id"], row["matches"]) == (
            "family_schema", None, scene.shown,
        )

    def test_values_job_row_is_by_the_context_with_the_kind(self, db_session, factories):
        scene = _values_world(db_session, factories, title="Стяжка уникальная")
        _to_error(db_session, scene.job)

        (row,) = list_jobs(db_session, status="error")["items"]

        assert (row["kind"], row["context_id"], row["title"]) == (
            "context_values", scene.context_id, "Стяжка уникальная",
        )
        assert (row["family_id"], row["schema_id"]) == (None, scene.schema.id)
        assert (row["schema_version"], row["names_count"]) == (None, None)
        assert row["path"] == ["Секция", "Полы"]

    def test_suggestion_job_row_keeps_its_shape_with_the_new_fields_empty(
        self, db_session, factories
    ):
        scene = _values_world(db_session, factories)
        scene.job.kind = "family_suggestion"
        scene.job.schema_id = None
        scene.job.paths_hash = None
        _to_error(db_session, scene.job)

        (row,) = list_jobs(db_session, status="error")["items"]

        assert (row["kind"], row["family_id"], row["schema_id"]) == ("family_suggestion", None, None)
        assert (row["schema_version"], row["names_count"]) == (None, None)


_VIOLATIONS = ["not_privacy_hold", "inapplicable", "stale_fingerprint", "matches_differ"]


def _violate(violation, kind, db, scene):
    if violation == "not_privacy_hold":
        scene.job.status = "pending"
        scene.job.privacy_matches = None
        db.flush()
    elif violation == "inapplicable":
        _make_inapplicable(kind, db, scene)
    elif violation == "stale_fingerprint":
        scene.job.request_hash = "hash-of-another-request"
        db.flush()
    else:
        scene.shown = [{"text": "другое", "kind": "contractor", "where": "context"}]


class TestHeldJobsBySubject:
    @pytest.mark.parametrize("kind", KINDS)
    def test_release_sends_the_job_back_with_the_shown_set(self, db_session, factories, kind):
        scene = _hold(kind, db_session, factories)
        user = factories.UserFactory.create()

        release_privacy_hold(
            db_session, job_id=scene.job.id, shown_matches=scene.shown, actor_id=user.id
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.privacy_released_matches, job.privacy_decided_by) == (
            "pending", scene.shown, user.id,
        )

    @pytest.mark.parametrize("violation", _VIOLATIONS)
    @pytest.mark.parametrize("kind", KINDS)
    def test_release_refuses_a_job_that_no_longer_matches_the_screen(
        self, db_session, factories, kind, violation
    ):
        scene = _hold(kind, db_session, factories)
        user = factories.UserFactory.create()
        _violate(violation, kind, db_session, scene)

        with pytest.raises(DecisionConflict) as raised:
            release_privacy_hold(
                db_session, job_id=scene.job.id, shown_matches=scene.shown, actor_id=user.id
            )

        assert raised.value.code == "job_changed"

    @pytest.mark.parametrize("violation", _VIOLATIONS)
    @pytest.mark.parametrize("kind", KINDS)
    def test_decline_refuses_a_job_that_no_longer_matches_the_screen(
        self, db_session, factories, kind, violation
    ):
        scene = _hold(kind, db_session, factories)
        user = factories.UserFactory.create()
        _violate(violation, kind, db_session, scene)

        with pytest.raises(DecisionConflict) as raised:
            decline_privacy_hold(
                db_session, job_id=scene.job.id, shown_matches=scene.shown, actor_id=user.id
            )

        assert raised.value.code == "job_changed"

    def test_decline_of_a_values_job_cancels_it_and_leaves_the_schema_alone(
        self, db_session, factories
    ):
        scene = _hold("context_values", db_session, factories)
        user = factories.UserFactory.create()

        decline_privacy_hold(
            db_session, job_id=scene.job.id, shown_matches=scene.shown, actor_id=user.id
        )

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "privacy_declined")
        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "frozen"

    def test_unit_release_sends_the_jobs_of_both_new_kinds(self, db_session, factories):
        schema_scene = _hold("family_schema", db_session, factories)
        values_scene = _hold(
            "context_values", db_session, factories, title="Кладка Ромашка Строй"
        )
        user = factories.UserFactory.create()

        report = release_unit_privacy_holds(
            db_session, unit_id=None, shown_matches=schema_scene.shown, actor_id=user.id
        )

        assert sorted(report.confirmed) == sorted([schema_scene.job.id, values_scene.job.id])
        assert report.skipped == []
        assert _reload(db_session, schema_scene.job.id).status == "pending"
        assert _reload(db_session, values_scene.job.id).status == "pending"

    def test_unit_release_skips_the_job_that_no_longer_matches(self, db_session, factories):
        schema_scene = _hold("family_schema", db_session, factories)
        values_scene = _hold(
            "context_values", db_session, factories, title="Кладка Ромашка Строй"
        )
        values_scene.job.request_hash = "hash-of-another-request"
        db_session.flush()
        user = factories.UserFactory.create()

        report = release_unit_privacy_holds(
            db_session, unit_id=None, shown_matches=schema_scene.shown, actor_id=user.id
        )

        assert (report.confirmed, report.skipped) == ([schema_scene.job.id], [values_scene.job.id])


class TestRetryByStatus:
    @pytest.mark.parametrize("kind", KINDS)
    def test_retry_returns_the_error_job_to_the_queue_with_a_new_generation(
        self, db_session, factories, kind
    ):
        scene = _scene(kind, db_session, factories)
        _to_error(db_session, scene.job)
        user = factories.UserFactory.create()

        retry_job(db_session, job_id=scene.job.id, actor_id=user.id)

        job = _reload(db_session, scene.job.id)
        assert (job.status, job.retry_generation, job.attempts_in_generation) == ("pending", 1, 0)
        assert job.last_error_class is None

    @pytest.mark.parametrize("kind", KINDS)
    def test_retry_refuses_a_job_that_is_not_in_error(self, db_session, factories, kind):
        scene = _scene(kind, db_session, factories)
        user = factories.UserFactory.create()

        with pytest.raises(DecisionConflict) as raised:
            retry_job(db_session, job_id=scene.job.id, actor_id=user.id)

        assert raised.value.code == "job_changed"

    def test_retry_of_a_schema_job_goes_through_the_same_building_version(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        _to_error(db_session, scene.job)
        user = factories.UserFactory.create()

        retry_job(db_session, job_id=scene.job.id, actor_id=user.id)

        schema = db_session.get(FamilyParameterSchema, scene.schema.id)
        assert (schema.status, schema.cancelled_at) == ("building", None)


# ---------------------------------------------------------------------------
#  Отмена версии building
# ---------------------------------------------------------------------------

def _schema_fingerprint(scene, *, schema_id=-1) -> Fingerprint:
    return Fingerprint(
        "family_schema", None, scene.family.id,
        scene.schema.id if schema_id == -1 else schema_id, scene.job.request_hash,
    )


def _held_batch(db, factories, fingerprints, *, status="held"):
    return _batch(
        db, factories, status=status, held_fingerprints=[f.as_dict() for f in fingerprints],
        fingerprints_hash=fingerprints_hash(fingerprints), contexts_count=len(fingerprints),
        decided_by=factories.UserFactory.create().id if status != "held" else None,
        decided_at=NOW if status != "held" else None,
    )


def _decline(db, factories, scene):
    release_user = factories.UserFactory.create()
    decline_privacy_hold(
        db, job_id=scene.job.id, shown_matches=scene.shown, actor_id=release_user.id
    )


class TestDeclineCancelsTheBuildingVersion:
    def test_decline_of_a_schema_job_cancels_the_version_nobody_else_represents(
        self, db_session, factories
    ):
        scene = _hold("family_schema", db_session, factories)

        _decline(db_session, factories, scene)

        db_session.expire_all()
        schema = db_session.get(FamilyParameterSchema, scene.schema.id)
        assert (schema.status, schema.cancelled_at is not None) == ("cancelled", True)
        job = db_session.get(SemanticJob, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "privacy_declined")

    def test_another_held_batch_with_the_fingerprint_keeps_the_version(
        self, db_session, factories
    ):
        scene = _hold("family_schema", db_session, factories)
        _held_batch(db_session, factories, [_schema_fingerprint(scene)])

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"
        assert _reload(db_session, scene.job.id).status == "cancelled"

    def test_another_approved_batch_with_the_fingerprint_keeps_the_version(
        self, db_session, factories
    ):
        scene = _hold("family_schema", db_session, factories)
        _held_batch(db_session, factories, [_schema_fingerprint(scene)], status="approved")

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_the_approved_batch_that_produced_the_job_does_not_keep_the_version(
        self, db_session, factories
    ):
        scene = _hold("family_schema", db_session, factories)
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)], status="approved")
        scene.job.batch_id = batch.id
        db_session.flush()

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"

    def test_a_batch_of_another_version_does_not_keep_the_version(self, db_session, factories):
        scene = _hold("family_schema", db_session, factories)
        other = _schema(db_session, factories, scene.family, version=0, status="superseded")
        _held_batch(db_session, factories, [_schema_fingerprint(scene, schema_id=other.id)])

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"

    @pytest.mark.parametrize("status", ["pending", "running", "privacy_hold"])
    def test_a_job_of_an_old_request_does_not_keep_the_version(
        self, db_session, factories, status
    ):
        scene = _hold("family_schema", db_session, factories)
        other = _job(
            db_session, kind="family_schema", family_id=scene.family.id,
            schema_id=scene.schema.id, request_hash="hash-of-an-old-request", status=status,
        )

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"
        job = _reload(db_session, other.id)
        if status == "running":
            assert (job.status, job.cancel_reason) == ("running", None)
        else:
            assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    @pytest.mark.parametrize("status", ["held", "approved"])
    def test_a_batch_of_an_old_request_does_not_keep_the_version(
        self, db_session, factories, status
    ):
        scene = _hold("family_schema", db_session, factories)
        old = Fingerprint(
            "family_schema", None, scene.family.id, scene.schema.id, "hash-of-an-old-request"
        )
        _held_batch(db_session, factories, [old], status=status)

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"

    @pytest.mark.parametrize("status", ["held", "approved"])
    def test_a_batch_of_the_current_request_keeps_the_version(self, db_session, factories, status):
        scene = _hold("family_schema", db_session, factories)
        _held_batch(db_session, factories, [_schema_fingerprint(scene)], status=status)

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    @pytest.mark.parametrize("status", ["done", "error", "cancelled"])
    def test_another_job_that_is_not_live_does_not_keep_the_version(
        self, db_session, factories, status
    ):
        scene = _hold("family_schema", db_session, factories)
        _job(
            db_session, kind="family_schema", family_id=scene.family.id,
            schema_id=scene.schema.id, request_hash="hash-of-the-other-job", status=status,
        )

        _decline(db_session, factories, scene)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"

    def test_error_does_not_touch_the_version(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        _to_error(db_session, scene.job)
        user = factories.UserFactory.create()
        retry_job(db_session, job_id=scene.job.id, actor_id=user.id)

        schema = db_session.get(FamilyParameterSchema, scene.schema.id)
        assert (schema.status, schema.cancelled_at) == ("building", None)

    def test_decline_takes_the_family_and_the_version_before_the_job(self, db_session, factories):
        scene = _hold("family_schema", db_session, factories)

        with _capturing_sql(db_session) as statements:
            _decline(db_session, factories, scene)

        job = _first(statements, "FROM semantic_jobs", "FOR UPDATE")
        family = _first(statements, "FROM work_families", "FOR UPDATE")
        version = _first(statements, "FROM family_parameter_schemas", "FOR UPDATE")
        assert None not in (job, family, version)
        assert family < version < job


class TestDiscardCancelsTheBuildingVersion:
    def test_discard_cancels_the_version_of_a_schema_fingerprint_nobody_else_represents(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        scene.job.status = "cancelled"
        scene.job.cancel_reason = "privacy_declined"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        db_session.expire_all()
        schema = db_session.get(FamilyParameterSchema, scene.schema.id)
        assert (schema.status, schema.cancelled_at is not None) == ("cancelled", True)
        assert db_session.get(SemanticReconcileBatch, batch.id).status == "discarded"

    def test_another_held_batch_keeps_the_version(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        # Живого задания нет: версию держит только другая пачка.
        scene.job.status = "cancelled"
        scene.job.cancel_reason = "privacy_declined"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        _held_batch(
            db_session, factories,
            [_schema_fingerprint(scene), Fingerprint("family_suggestion", 1, None, None, "x")],
        )
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_another_approved_batch_keeps_the_version(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        # Живого задания нет: версию держит только другая пачка.
        scene.job.status = "cancelled"
        scene.job.cancel_reason = "privacy_declined"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        _held_batch(
            db_session, factories,
            [_schema_fingerprint(scene), Fingerprint("family_suggestion", 1, None, None, "x")],
            status="approved",
        )
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_an_error_job_is_cancelled_with_the_version(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        _to_error(db_session, scene.job)
        running = _job(
            db_session, kind="family_schema", family_id=scene.family.id,
            schema_id=scene.schema.id, request_hash="hash-of-an-old-request", status="running",
        )
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"
        job = _reload(db_session, scene.job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
        assert _reload(db_session, running.id).status == "running"

    def test_an_approved_batch_of_an_old_request_does_not_keep_the_version(
        self, db_session, factories
    ):
        scene = _schema_world(db_session, factories)
        scene.job.status = "cancelled"
        scene.job.cancel_reason = "privacy_declined"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        old = Fingerprint(
            "family_schema", None, scene.family.id, scene.schema.id, "hash-of-an-old-request"
        )
        _held_batch(db_session, factories, [old], status="approved")
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "cancelled"

    @pytest.mark.parametrize("status", ["pending", "running", "privacy_hold"])
    def test_a_live_job_of_the_current_request_keeps_the_version(
        self, db_session, factories, status
    ):
        """Живое задание текущего отпечатка (каждый живой статус) держит версию,
        и отмена версии его не трогает."""
        scene = _schema_world(db_session, factories)
        scene.job.status = status
        if status == "running":
            scene.job.claim_token = uuid.uuid4()
        if status == "privacy_hold":
            scene.job.privacy_matches = [
                {"text": "ромашка", "kind": "contractor", "where": "context"}
            ]
        db_session.flush()
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"
        assert _reload(db_session, scene.job.id).status == status

    def test_a_fingerprint_without_a_version_cancels_nothing(self, db_session, factories):
        scene = _schema_world(db_session, factories)
        scene.job.status = "cancelled"
        scene.job.cancel_reason = "privacy_declined"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene, schema_id=None)])
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "building"

    def test_a_version_that_is_not_building_is_not_touched(self, db_session, factories):
        scene = _schema_world(db_session, factories, schema_status="frozen")
        scene.job.status = "done"
        batch = _held_batch(db_session, factories, [_schema_fingerprint(scene)])
        user = factories.UserFactory.create()

        discard_batch(db_session, batch_id=batch.id, actor_id=user.id)

        assert db_session.get(FamilyParameterSchema, scene.schema.id).status == "frozen"


# ---------------------------------------------------------------------------
#  Пачки трёх видов
# ---------------------------------------------------------------------------

def _three_kinds_world(db, factories):
    """Предложение контекста C0 (единица M2, семьи нет), значения контекста C1
    (та же единица, семья F1 с текущей схемой) и схема семьи F2 (другая единица,
    схемы нет) — три вида в одном наборе."""
    from services.unit_resolution import UnitResolver

    unit_m2 = UnitResolver(db).resolve("M2").unit_id
    unit_m3 = UnitResolver(db).resolve("M3").unit_id
    family_1 = _family(db, status="active", definition="Определение 1", unit_id=unit_m2)
    _frozen_schema(db, factories, family_1, [[1, "Толщина", ["50 мм", "100 мм"]]])
    context_id, _ = _chain_context(
        db, factories, title=f"Стяжка {_uid()}", path_specs=[(("Секция", "Полы"), 1)],
        unit_id=unit_m2,
    )
    _bind(db, factories, context_id, family=family_1)
    suggest_context_id, _ = _chain_context(
        db, factories, title=f"Кладка {_uid()}", path_specs=[((), 1)], unit_id=unit_m2
    )
    family_2 = _family(db, status="active", definition="Определение 2", unit_id=unit_m3)
    return SimpleNamespace(
        context_id=context_id, suggest_context_id=suggest_context_id, family_1=family_1,
        family_2=family_2,
    )


def _batch_of(db, factories, world):
    pairs, reserve, cached = estimate_enqueue(
        db, [world.suggest_context_id, world.context_id], [world.family_2.id]
    )
    # Привязанный контекст C1 тоже получает задание предложения (спека вариантов
    # §2.5): у набора четыре отпечатка, два из них — предложения.
    assert sorted(f.kind.value for f in pairs) == [
        "context_values", "family_schema", "family_suggestion", "family_suggestion",
    ]
    batch_id = get_or_create_held_batch(
        db, fingerprints=pairs, source="import", import_job_id=None, unit_id=None,
        reserve_estimate_usd=reserve, cached_estimate_usd=cached,
    )
    return db.get(SemanticReconcileBatch, batch_id), pairs


class TestBatchesOfThreeKinds:
    def test_approval_queues_the_jobs_of_all_three_kinds(self, db_session, factories):
        world = _three_kinds_world(db_session, factories)
        batch, _pairs = _batch_of(db_session, factories, world)
        user = factories.UserFactory.create()
        preview = preview_batch(db_session, batch_id=batch.id)
        # Задания ставит подтверждение, а не подготовка сцены.
        assert _count(db_session, SemanticJob) == 0

        report = approve_batch(
            db_session, batch_id=batch.id, preview_hash=preview.preview_hash, actor_id=user.id
        )

        # Отчёт складывает обе сверки: контексты (два предложения и значения) и семьи.
        assert report.created == 4
        db_session.expire_all()
        jobs = db_session.execute(sa.select(SemanticJob).order_by(SemanticJob.id)).scalars().all()
        assert sorted((j.kind, j.status) for j in jobs) == [
            ("context_values", "pending"), ("family_schema", "pending"),
            ("family_suggestion", "pending"), ("family_suggestion", "pending"),
        ]
        assert {j.batch_id for j in jobs} == {batch.id}
        schema_job = next(j for j in jobs if j.kind == "family_schema")
        assert schema_job.family_id == world.family_2.id and schema_job.schema_id is not None
        version = db_session.get(FamilyParameterSchema, schema_job.schema_id)
        assert (version.family_id, version.status) == (world.family_2.id, "building")
        assert db_session.get(SemanticReconcileBatch, batch.id).status == "approved"

    def test_a_schema_only_batch_is_approved_by_its_family(self, db_session, factories):
        world = _three_kinds_world(db_session, factories)
        pairs, reserve, cached = estimate_enqueue(db_session, [], [world.family_2.id])
        assert [f.kind.value for f in pairs] == ["family_schema"]
        batch_id = get_or_create_held_batch(
            db_session, fingerprints=pairs, source="import", import_job_id=None, unit_id=None,
            reserve_estimate_usd=reserve, cached_estimate_usd=cached,
        )
        user = factories.UserFactory.create()
        preview = preview_batch(db_session, batch_id=batch_id)
        assert preview.context_count == 1

        report = approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=user.id
        )

        assert report.created == 1
        (job,) = db_session.execute(sa.select(SemanticJob)).scalars().all()
        assert (job.kind, job.family_id, job.status, job.batch_id) == (
            "family_schema", world.family_2.id, "pending", batch_id,
        )

    def test_a_schema_fingerprint_with_a_version_marks_the_job_of_that_version(
        self, db_session, factories
    ):
        """Отпечаток схемы, уже несущий версию `building`: задание этой версии
        ставит подтверждение, и `batch_id` оно получает по (семья, версия, хэш)."""
        world = _three_kinds_world(db_session, factories)
        version = _schema(db_session, factories, world.family_2, version=1, status="building")
        pairs, reserve, cached = estimate_enqueue(db_session, [], [world.family_2.id])
        assert [(f.kind.value, f.schema_id) for f in pairs] == [("family_schema", version.id)]
        batch_id = get_or_create_held_batch(
            db_session, fingerprints=pairs, source="import", import_job_id=None, unit_id=None,
            reserve_estimate_usd=reserve, cached_estimate_usd=cached,
        )
        user = factories.UserFactory.create()
        preview = preview_batch(db_session, batch_id=batch_id)
        assert _count(db_session, SemanticJob) == 0

        approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=user.id
        )

        db_session.expire_all()
        (job,) = db_session.execute(sa.select(SemanticJob)).scalars().all()
        assert (job.kind, job.family_id, job.schema_id, job.status, job.batch_id) == (
            "family_schema", world.family_2.id, version.id, "pending", batch_id,
        )

    def test_preview_counts_the_subjects_of_every_kind(self, db_session, factories):
        world = _three_kinds_world(db_session, factories)
        batch, pairs = _batch_of(db_session, factories, world)

        preview = preview_batch(db_session, batch_id=batch.id)

        assert preview.context_count == 4

    def test_preview_sums_use_the_tariffs_of_each_kind(
        self, db_session, factories, monkeypatch
    ):
        for prefix, tariffs in (
            ("SEMANTIC_PRICE", SUGGESTION_TARIFFS),
            ("SEMANTIC_SCHEMA_PRICE", SCHEMA_TARIFFS),
            ("SEMANTIC_VALUES_PRICE", VALUES_TARIFFS),
        ):
            for suffix, price in zip(
                ("INPUT_PER_M", "CACHE_WRITE_PER_M", "CACHE_READ_PER_M", "OUTPUT_PER_M"),
                tariffs, strict=True,
            ):
                monkeypatch.setattr(app_settings, f"{prefix}_{suffix}", price)
        world = _three_kinds_world(db_session, factories)
        batch, pairs = _batch_of(db_session, factories, world)

        preview = preview_batch(db_session, batch_id=batch.id)

        suggestion = render_context_request(
            load_request_material(db_session, [world.suggest_context_id])[world.suggest_context_id],
            settings=app_settings,
        )
        bound_suggestion = render_context_request(
            load_request_material(db_session, [world.context_id])[world.context_id],
            settings=app_settings,
        )
        values = render_values_request(
            load_values_material(db_session, [world.context_id])[world.context_id],
            settings=app_settings,
        )
        schema = render_schema_request(
            load_schema_material(db_session, world.family_2.id, 0), settings=app_settings
        )
        rendered = [suggestion, bound_suggestion, values, schema]
        expected_reserve = (
            _reserve(suggestion, SUGGESTION_TARIFFS)
            + _reserve(bound_suggestion, SUGGESTION_TARIFFS)
            + _reserve(values, VALUES_TARIFFS)
            + _reserve(schema, SCHEMA_TARIFFS)
        )
        assert preview.reserve_usd == expected_reserve
        # Тариф любого одного вида на все три задания дал бы другую сумму.
        for flat in (SUGGESTION_TARIFFS, SCHEMA_TARIFFS, VALUES_TARIFFS):
            assert preview.reserve_usd != sum(_reserve(r, flat) for r in rendered)

    def test_preview_hash_is_the_hash_of_the_canonical_text_with_the_tariffs_by_kind(
        self, db_session, factories, monkeypatch
    ):
        for prefix, tariffs in (
            ("SEMANTIC_PRICE", SUGGESTION_TARIFFS),
            ("SEMANTIC_SCHEMA_PRICE", SCHEMA_TARIFFS),
            ("SEMANTIC_VALUES_PRICE", VALUES_TARIFFS),
        ):
            for suffix, price in zip(
                ("INPUT_PER_M", "CACHE_WRITE_PER_M", "CACHE_READ_PER_M", "OUTPUT_PER_M"),
                tariffs, strict=True,
            ):
                monkeypatch.setattr(app_settings, f"{prefix}_{suffix}", price)
        world = _three_kinds_world(db_session, factories)
        batch, pairs = _batch_of(db_session, factories, world)

        preview = preview_batch(db_session, batch_id=batch.id)

        def _tariffs(values):
            return dict(zip(
                ("input_per_m", "cache_write_per_m", "cache_read_per_m", "output_per_m"),
                values, strict=True,
            ))

        canonical = json.dumps(
            {
                "fingerprints": [f.as_dict() for f in pairs],
                "reserve_usd": str(preview.reserve_usd),
                "cached_usd": str(preview.expected_cached_usd),
                "tariffs": {
                    "family_suggestion": _tariffs(("2", "2.5", "0.2", "10")),
                    "family_schema": _tariffs(("3", "4", "0.3", "20")),
                    "context_values": _tariffs(("5", "6", "0.5", "30")),
                },
                "reserve_formula_version": 1,
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        assert preview.preview_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def test_preview_hash_changes_with_the_tariffs_of_a_kind_that_is_in_the_batch(
        self, db_session, factories, monkeypatch
    ):
        world = _three_kinds_world(db_session, factories)
        batch, _pairs = _batch_of(db_session, factories, world)
        before = preview_batch(db_session, batch_id=batch.id).preview_hash

        monkeypatch.setattr(app_settings, "SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M", Decimal("21"))

        assert preview_batch(db_session, batch_id=batch.id).preview_hash != before

    def test_a_batch_moved_by_the_migration_is_approved(self, db_session, factories):
        world = _three_kinds_world(db_session, factories)
        migration = _migration_0019()
        request_hash = render_context_request(
            load_request_material(db_session, [world.suggest_context_id])[world.suggest_context_id],
            settings=app_settings,
        ).request_hash
        moved = migration._fingerprints_to_objects([[world.suggest_context_id, request_hash]])
        batch = _batch(
            db_session, factories, held_fingerprints=moved,
            fingerprints_hash=migration._canonical_hash(moved), contexts_count=1,
        )
        user = factories.UserFactory.create()
        preview = preview_batch(db_session, batch_id=batch.id)
        # Задание ставит подтверждение, а не подготовка сцены.
        assert _count(db_session, SemanticJob) == 0

        approve_batch(
            db_session, batch_id=batch.id, preview_hash=preview.preview_hash, actor_id=user.id
        )

        db_session.expire_all()
        jobs = db_session.execute(sa.select(SemanticJob)).scalars().all()
        assert [(j.kind, j.context_id, j.request_hash, j.status, j.batch_id) for j in jobs] == [
            ("family_suggestion", world.suggest_context_id, request_hash, "pending", batch.id)
        ]
        assert db_session.get(SemanticReconcileBatch, batch.id).status == "approved"

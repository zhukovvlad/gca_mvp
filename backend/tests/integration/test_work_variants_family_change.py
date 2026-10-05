"""Смена семьи контекста: единая точка входа, ожидающее назначение,
правила публикации и контракт `assign_family` (спека
`2026-10-02-catalog-variants-design.md` §2.5, §2.13).

Однопоточные входы на транзакционной сессии; гонки двух сессий с настоящими
commit-ами — в конце файла. Каждое заявление проверки — свой вход: шесть
пунктов правил ожидания, семь строк таблицы публикации, граница порога.
Решения человека по предложениям — в `test_work_variants_decisions.py`.

Помощники сцены (семья, схема, предложение на текущий отпечаток) приходят из
набора решений очереди и набора ядра варианта.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker_module
import services.work_variants as work_variants_module
from config import settings as app_settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    FamilyParameterSchema,
    FamilySource,
    FamilySuggestion,
    SemanticEvent,
    SemanticJob,
    WorkFamily,
    WorkVariant,
)
from services.family_change import (
    FamilyChangeOutcome,
    apply_publication_rules,
    cancel_pending_family,
    request_family_change,
)
from services.semantic_decisions import (
    confirm_suggestions,
    confirm_unit_reask,
    preview_unit_reask,
    reject_suggestion,
)
from services.semantic_reconcile import NO_CAP, reconcile_semantic_jobs
from services.semantic_request import is_applicable, load_request_material
from services.semantic_worker import claim_next, process_one, record_result
from services.work_families import (
    REFUSE_ARCHIVE_WITH_LINKS,
    REFUSE_FAMILY_NOT_ACTIVE,
    REFUSE_UNIT_CHANGE_WITH_LINKS,
    REFUSE_UNIT_MISMATCH,
    WorkFamilyError,
    archive_family,
    assign_family,
    set_unit,
)
from services.work_variants import apply_values
from tests.integration.test_semantic_queue_decisions import (
    _active_family,
    _fresh,
    _published,
    _rendered,
    _scene,
    _unit_id,
)
from tests.integration.test_semantic_queue_worker import (
    NOW,
    S,
    _clock,
    _committed_scene,
    _FakeClient,
    _response,
    _suggestions,
)
from tests.integration.test_semantic_queue_worker import (
    _answer as _model_answer,
)
from tests.integration.test_work_variants_core import _answer, _attach_variant, _events
from tests.integration.test_work_variants_material import _settings, _uid
from tests.integration.test_work_variants_schema import _param, _value, _variant

pytestmark = pytest.mark.integration

THRESHOLD = Decimal("0.80")


# ---------------------------------------------------------------------------
#  Сцена
# ---------------------------------------------------------------------------

def _values_jobs(db, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == "context_values")
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt.order_by(SemanticJob.id)).scalars().all())


def _two_families(db, factories, *, titles=("Устройство пола",)):
    """Сцена очереди: семья A (`scene.family`), ещё две активные семьи B и C
    той же единицы (у каждой своя текущая схема без параметров) и контексты."""
    scene = _scene(db, factories, titles=titles)
    scene.family_b = _active_family(db, title="Семья B", unit_name="M2", actor_id=scene.user.id)
    scene.family_c = _active_family(db, title="Семья C", unit_name="M2", actor_id=scene.user.id)
    return scene


def _current_schema(db, family):
    return db.execute(
        sa.select(FamilyParameterSchema).where(
            FamilyParameterSchema.family_id == family.id,
            FamilyParameterSchema.status == "frozen",
        )
    ).scalar_one()


def _bind_source(db, scene, context_id, family, source="manual"):
    """Привязка контекста к семье с нужным происхождением, в обход операций."""
    context = db.get(CatalogContext, context_id)
    context.work_family_id = family.id
    context.family_source = source
    context.family_by = scene.user.id if source == "manual" else None
    context.family_at = dt.datetime.now(dt.UTC)
    db.flush()
    return context


def _with_variant(db, scene, context_id, family, source="manual"):
    """Контекст на семье с активным вариантом по её текущей схеме."""
    _bind_source(db, scene, context_id, family, source)
    variant = _variant(db, family, _current_schema(db, family))
    _attach_variant(db, context_id, variant)
    return variant


def _ctx(db, context_id) -> CatalogContext:
    db.flush()  # `_fresh` сбрасывает сессию: несброшенные правки пропали бы
    return _fresh(db, CatalogContext, context_id)


def _sug(db, suggestion_id) -> FamilySuggestion:
    db.flush()
    return _fresh(db, FamilySuggestion, suggestion_id)


def _publish(db, context_id, *, family_id, **kwargs) -> FamilySuggestion:
    """Предложение на текущий отпечаток контекста. Место ему освобождают живые
    задания предложения контекста (привязанный контекст применим, и сверка уже
    поставила своё) и прежние предложения с тем же отпечатком: вид, предмет и
    отпечаток задания уникальны."""
    db.flush()
    db.execute(
        sa.delete(SemanticJob).where(
            SemanticJob.context_id == context_id,
            SemanticJob.kind == "family_suggestion",
            ~sa.exists().where(FamilySuggestion.job_id == SemanticJob.id),
        )
    )
    current = _rendered(db, context_id).request_hash
    for model in (SemanticJob, FamilySuggestion):
        db.execute(
            sa.update(model)
            .where(model.context_id == context_id, model.request_hash == current)
            .values(request_hash=f"older-{_uid()}")
        )
    return _published(db, context_id, family_id=family_id, **kwargs)


def _pending_columns(context):
    return (
        context.pending_family_id, context.pending_family_source,
        context.pending_suggestion_id, context.pending_by, context.pending_threshold,
    )


def _pending_events(db, context_id):
    return [(e.payload["outcome"], e.payload) for e in _events(db, "context_family_pending", context_id=context_id)]


def _unpublish(db, suggestion):
    """Предложение остаётся с решением, но публикация снята: следующее
    предложение контекста публикуется как после записи ответа (одно
    опубликованное на контекст)."""
    suggestion.is_published = False
    suggestion.unpublished_reason = "stale_fingerprint"
    db.flush()


def _decision(db, suggestion_id):
    s = _sug(db, suggestion_id)
    return (s.decision, s.decided_by)


# ---------------------------------------------------------------------------
#  Контракт assign_family
# ---------------------------------------------------------------------------

class TestAssignFamilyContract:
    def test_auto_suggestion_leaves_no_author_and_logs_threshold_and_confidence(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family.id)

        assign_family(
            db_session, context_id=context_id, family_id=scene.family.id, actor_id=None,
            source=FamilySource.auto_suggestion, suggestion_id=suggestion.id,
            threshold=Decimal("0.80"), confidence=Decimal("0.9"),
        )

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            scene.family.id, "auto_suggestion", None,
        )
        [event] = _events(db_session, "context_family_assigned", context_id=context_id)
        assert event.actor_id is None
        assert event.payload == {
            "from_family_id": None, "to_family_id": scene.family.id,
            "source": "auto_suggestion", "suggestion_id": suggestion.id,
            "threshold": "0.80", "confidence": "0.9",
        }

    @pytest.mark.parametrize(
        "case",
        [
            "auto_with_actor", "auto_without_threshold", "auto_without_confidence",
            "auto_without_suggestion", "auto_without_family", "manual_without_actor",
            "suggestion_without_actor", "manual_with_threshold", "manual_with_confidence",
            "suggestion_with_threshold", "suggestion_with_confidence",
        ],
    )
    def test_incoherent_arguments_are_refused_before_any_write(self, db_session, factories, case):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        user = scene.user.id
        fam = scene.family.id
        auto, sug, man = (
            FamilySource.auto_suggestion, FamilySource.suggestion, FamilySource.manual,
        )
        calls = {
            "auto_with_actor": dict(family_id=fam, actor_id=user, source=auto, suggestion_id=1,
                                    threshold=THRESHOLD, confidence=THRESHOLD),
            "auto_without_threshold": dict(family_id=fam, actor_id=None, source=auto,
                                           suggestion_id=1, confidence=THRESHOLD),
            "auto_without_confidence": dict(family_id=fam, actor_id=None, source=auto,
                                            suggestion_id=1, threshold=THRESHOLD),
            "auto_without_suggestion": dict(family_id=fam, actor_id=None, source=auto,
                                            threshold=THRESHOLD, confidence=THRESHOLD),
            "auto_without_family": dict(family_id=None, actor_id=None, source=auto,
                                        suggestion_id=1, threshold=THRESHOLD,
                                        confidence=THRESHOLD),
            "manual_without_actor": dict(family_id=fam, actor_id=None, source=man),
            "suggestion_without_actor": dict(family_id=fam, actor_id=None, source=sug,
                                             suggestion_id=1),
            "manual_with_threshold": dict(family_id=fam, actor_id=user, source=man,
                                          threshold=THRESHOLD),
            "manual_with_confidence": dict(family_id=fam, actor_id=user, source=man,
                                           confidence=THRESHOLD),
            "suggestion_with_threshold": dict(family_id=fam, actor_id=user, source=sug,
                                              suggestion_id=1, threshold=THRESHOLD),
            "suggestion_with_confidence": dict(family_id=fam, actor_id=user, source=sug,
                                               suggestion_id=1, confidence=THRESHOLD),
        }

        with pytest.raises(ValueError):
            assign_family(db_session, context_id=context_id, **calls[case])

        assert _ctx(db_session, context_id).work_family_id is None
        assert _events(db_session, "context_family_assigned") == []


# ---------------------------------------------------------------------------
#  Предикат применимости
# ---------------------------------------------------------------------------

class TestApplicabilityKeepsBoundContexts:
    def test_a_bound_context_is_applicable(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)

        material = load_request_material(db_session, [context_id])[context_id]

        assert material.work_family_id == scene.family.id
        assert is_applicable(material) is True

    def test_an_archived_bound_context_stays_inapplicable(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        context = _bind_source(db_session, scene, context_id, scene.family)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        material = load_request_material(db_session, [context_id])[context_id]

        assert is_applicable(material) is False

    def test_a_bound_context_gets_a_family_job_when_the_unit_is_reasked(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)
        db_session.execute(sa.delete(SemanticJob))
        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)

        report = confirm_unit_reask(
            db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
            actor_id=scene.user.id,
        )

        assert preview.context_count >= 1
        assert report.created >= 1
        [job] = db_session.execute(
            sa.select(SemanticJob).where(
                SemanticJob.context_id == context_id, SemanticJob.kind == "family_suggestion"
            )
        ).scalars().all()
        assert job.status == "pending"


# ---------------------------------------------------------------------------
#  request_family_change
# ---------------------------------------------------------------------------

class TestRequestFamilyChangePathOne:
    """Контекст без варианта: смена сразу, есть у него семья или нет."""

    def test_a_context_without_a_family_is_assigned_at_once(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome == FamilyChangeOutcome("assigned", context_id, scene.family.id, None)
        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            scene.family.id, "manual", scene.user.id,
        )
        assert _pending_columns(context) == (None, None, None, None, None)

    def test_a_context_with_a_family_but_no_variant_changes_family_at_once(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome.kind == "assigned"
        assert _ctx(db_session, context_id).work_family_id == scene.family_b.id
        assert _pending_events(db_session, context_id) == []

    def test_the_auto_source_assigns_with_threshold_and_the_suggestion_confidence(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family.id)
        suggestion.confidence = Decimal("0.93")
        db_session.flush()

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id, actor_id=None,
            source="auto_suggestion", suggestion_id=suggestion.id, threshold=THRESHOLD,
        )

        assert outcome.kind == "assigned"
        [event] = _events(db_session, "context_family_assigned", context_id=context_id)
        assert (event.payload["threshold"], event.payload["confidence"]) == ("0.80", "0.93")
        assert _ctx(db_session, context_id).family_by is None

    def test_a_pending_family_of_a_variantless_context_is_replaced_by_the_immediate_change(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)
        context = _ctx(db_session, context_id)
        context.pending_family_id = scene.family_c.id
        context.pending_family_source = "manual"
        context.pending_by = scene.user.id
        context.pending_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome.kind == "assigned"
        context = _ctx(db_session, context_id)
        assert context.work_family_id == scene.family_b.id
        assert _pending_columns(context) == (None, None, None, None, None)
        assert [outcome_name for outcome_name, _ in _pending_events(db_session, context_id)] == [
            "superseded"
        ]


class TestRequestFamilyChangePathTwo:
    """Контекст с вариантом: смена семьи — ожидание."""

    @pytest.mark.parametrize("source", ["manual", "suggestion", "auto_suggestion"])
    def test_the_change_becomes_a_pending_family_with_the_columns_of_its_source(
        self, db_session, factories, source
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        suggestion = None
        if source != "manual":
            suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        actor = None if source == "auto_suggestion" else scene.user.id
        threshold = THRESHOLD if source == "auto_suggestion" else None

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id, actor_id=actor,
            source=source, suggestion_id=suggestion.id if suggestion else None,
            threshold=threshold,
        )

        assert outcome == FamilyChangeOutcome("pending", context_id, scene.family_b.id, None)
        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (
            scene.family_b.id, source, suggestion.id if suggestion else None,
            scene.user.id if source == "manual" else None, threshold,
        )
        assert context.pending_at is not None
        [(name, payload)] = _pending_events(db_session, context_id)
        expected = {
            "pending_family_id": scene.family_b.id, "source": source,
            "suggestion_id": suggestion.id if suggestion else None, "outcome": "set",
        }
        if source == "auto_suggestion":
            expected["threshold"] = "0.80"
        assert (name, payload) == ("set", expected)
        assert _events(db_session, "context_family_assigned", context_id=context_id) == []

    def test_the_values_job_is_set_by_the_schema_of_the_new_family(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        db_session.execute(sa.delete(SemanticJob))

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        [job] = _values_jobs(db_session, context_id=context_id)
        assert job.schema_id == _current_schema(db_session, scene.family_b).id

    def test_a_family_without_a_current_schema_leaves_the_job_for_the_freeze(
        self, db_session, factories
    ):
        from tests.integration.test_work_variants_schema import _family

        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        bare = _family(
            db_session, status="active", definition="Семья без схемы", unit_id=scene.unit_id,
            title="Семья без схемы",
        )
        db_session.execute(sa.delete(SemanticJob))

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=bare.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome.kind == "pending"
        assert _ctx(db_session, context_id).pending_family_id == bare.id
        assert _values_jobs(db_session, context_id=context_id) == []

    def test_the_same_family_changes_nothing(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome == FamilyChangeOutcome("unchanged", context_id, scene.family.id, None)
        assert _pending_events(db_session, context_id) == []
        assert _events(db_session, "context_family_assigned", context_id=context_id) == []

    def test_the_same_family_withdraws_a_pending_one_of_another_family(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome.kind == "unchanged"
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)
        assert [name for name, _ in _pending_events(db_session, context_id)] == [
            "set", "superseded",
        ]

    def test_a_new_pending_family_supersedes_the_old_one_and_logs_both(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id,
            actor_id=scene.user.id, source="manual",
        )

        assert outcome.kind == "pending"
        context = _ctx(db_session, context_id)
        assert context.pending_family_id == scene.family_c.id
        events = _pending_events(db_session, context_id)
        assert [name for name, _ in events] == ["set", "superseded", "set"]
        assert events[1][1]["pending_family_id"] == scene.family_b.id
        assert events[2][1]["pending_family_id"] == scene.family_c.id


class TestSupersededSuggestions:
    """Исход предложения, чьё ожидание вытеснено (спека §2.5 п. 4)."""

    def _pending_by(self, db, factories, source):
        scene = _two_families(db, factories)
        context_id = scene.context_ids[0]
        _with_variant(db, scene, context_id, scene.family)
        suggestion = _publish(db, context_id, family_id=scene.family_b.id)
        decision = "auto_pending" if source == "auto_suggestion" else "accepted_pending"
        suggestion.decision = decision
        suggestion.decided_by = None if source == "auto_suggestion" else scene.user.id
        suggestion.decided_at = dt.datetime.now(dt.UTC)
        context = _ctx(db, context_id)
        context.pending_family_id = scene.family_b.id
        context.pending_family_source = source
        context.pending_suggestion_id = suggestion.id
        context.pending_threshold = THRESHOLD if source == "auto_suggestion" else None
        context.pending_at = dt.datetime.now(dt.UTC)
        db.flush()
        scene.suggestion = suggestion
        return scene, context_id

    def test_a_human_replacing_an_auto_pending_rejects_its_suggestion_with_the_author(
        self, db_session, factories
    ):
        scene, context_id = self._pending_by(db_session, factories, "auto_suggestion")
        other = factories.UserFactory.create()

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id,
            actor_id=other.id, source="manual",
        )

        assert outcome.superseded_suggestion_id == scene.suggestion.id
        assert _decision(db_session, scene.suggestion.id) == ("rejected", other.id)

    def test_a_human_replacing_a_human_pending_rejects_its_suggestion_with_the_author(
        self, db_session, factories
    ):
        scene, context_id = self._pending_by(db_session, factories, "suggestion")
        other = factories.UserFactory.create()

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id,
            actor_id=other.id, source="manual",
        )

        assert _decision(db_session, scene.suggestion.id) == ("rejected", other.id)

    def test_the_rule_replacing_an_auto_pending_marks_its_suggestion_auto_superseded(
        self, db_session, factories
    ):
        scene, context_id = self._pending_by(db_session, factories, "auto_suggestion")
        _unpublish(db_session, scene.suggestion)
        newer = _publish(db_session, context_id, family_id=scene.family_c.id)

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id, actor_id=None,
            source="auto_suggestion", suggestion_id=newer.id, threshold=THRESHOLD,
        )

        assert outcome.kind == "pending"
        assert _decision(db_session, scene.suggestion.id) == ("auto_superseded", None)
        assert _ctx(db_session, context_id).pending_family_id == scene.family_c.id

    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    def test_the_rule_does_not_displace_a_human_pending_family(
        self, db_session, factories, source
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        human = None
        if source == "suggestion":
            human = _publish(db_session, context_id, family_id=scene.family_b.id)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source=source,
            suggestion_id=human.id if human else None,
        )
        before = _pending_columns(_ctx(db_session, context_id))
        if human:
            _unpublish(db_session, human)
        newer = _publish(db_session, context_id, family_id=scene.family_c.id)

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id, actor_id=None,
            source="auto_suggestion", suggestion_id=newer.id, threshold=THRESHOLD,
        )

        assert outcome == FamilyChangeOutcome("unchanged", context_id, scene.family_c.id, None)
        assert _pending_columns(_ctx(db_session, context_id)) == before
        assert [name for name, _ in _pending_events(db_session, context_id)] == ["set"]

    def test_a_decision_other_than_pending_survives_the_supersede(self, db_session, factories):
        """`family_created` и `other_family` ожидание порождают, но их решение
        от вытеснения не меняется."""
        scene, context_id = self._pending_by(db_session, factories, "suggestion")
        scene.suggestion.decision = "family_created"
        db_session.flush()

        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_c.id,
            actor_id=scene.user.id, source="manual",
        )

        assert _decision(db_session, scene.suggestion.id)[0] == "family_created"


class TestRequestFamilyChangeRefusals:
    def test_an_archived_target_family_is_refused_and_nothing_is_written(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        archive_family(db_session, family_id=scene.family_b.id, actor_id=scene.user.id)

        with pytest.raises(WorkFamilyError) as caught:
            request_family_change(
                db_session, context_id=context_id, family_id=scene.family_b.id,
                actor_id=scene.user.id, source="manual",
            )

        assert caught.value.code == REFUSE_FAMILY_NOT_ACTIVE
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)

    def test_a_family_of_another_unit_is_refused(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        foreign = _active_family(
            db_session, title="Семья в штуках", unit_name="PCS", actor_id=scene.user.id
        )

        with pytest.raises(WorkFamilyError) as caught:
            request_family_change(
                db_session, context_id=context_id, family_id=foreign.id,
                actor_id=scene.user.id, source="manual",
            )

        assert caught.value.code == REFUSE_UNIT_MISMATCH

    def test_removing_the_family_of_a_context_with_a_variant_is_a_separate_operation(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        with pytest.raises(ValueError):
            request_family_change(
                db_session, context_id=context_id, family_id=None,
                actor_id=scene.user.id, source="manual",
            )

    @pytest.mark.parametrize(
        "case",
        ["manual_without_actor", "auto_with_actor", "auto_without_threshold",
         "suggestion_without_id", "manual_with_threshold", "auto_without_suggestion"],
    )
    def test_incoherent_arguments_are_refused_before_any_lock(self, db_session, factories, case):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        user, fam = scene.user.id, scene.family.id
        calls = {
            "manual_without_actor": dict(actor_id=None, source="manual"),
            "auto_with_actor": dict(actor_id=user, source="auto_suggestion", suggestion_id=1,
                                    threshold=THRESHOLD),
            "auto_without_threshold": dict(actor_id=None, source="auto_suggestion",
                                           suggestion_id=1),
            "suggestion_without_id": dict(actor_id=user, source="suggestion"),
            "manual_with_threshold": dict(actor_id=user, source="manual", threshold=THRESHOLD),
            "auto_without_suggestion": dict(actor_id=None, source="auto_suggestion",
                                            threshold=THRESHOLD),
        }
        statements: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            with pytest.raises(ValueError):
                request_family_change(
                    db_session, context_id=context_id, family_id=fam, **calls[case]
                )
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert not any("FOR " in s for s in statements)


class TestCancelPendingFamily:
    def test_cancel_clears_the_columns_and_logs_the_outcome(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )

        cancel_pending_family(db_session, context_id=context_id, actor_id=scene.user.id)

        context = _ctx(db_session, context_id)
        assert _pending_columns(context) == (None, None, None, None, None)
        assert context.pending_at is None
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        events = _pending_events(db_session, context_id)
        assert [name for name, _ in events] == ["set", "cancelled"]
        assert events[1][1]["pending_family_id"] == scene.family_b.id

    def test_cancel_cancels_the_values_job_of_the_pending_family(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [job] = _values_jobs(db_session, context_id=context_id)

        cancel_pending_family(db_session, context_id=context_id, actor_id=scene.user.id)

        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    @pytest.mark.parametrize(
        ("source", "decision"), [("auto_suggestion", "auto_pending"), ("suggestion", "accepted_pending")]
    )
    def test_cancel_rejects_the_suggestion_of_the_pending_family_with_the_author(
        self, db_session, factories, source, decision
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        suggestion.decision = decision
        suggestion.decided_by = None if source == "auto_suggestion" else scene.user.id
        suggestion.decided_at = dt.datetime.now(dt.UTC)
        context = _ctx(db_session, context_id)
        context.pending_family_id = scene.family_b.id
        context.pending_family_source = source
        context.pending_suggestion_id = suggestion.id
        context.pending_threshold = THRESHOLD if source == "auto_suggestion" else None
        context.pending_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        canceller = factories.UserFactory.create()

        cancel_pending_family(db_session, context_id=context_id, actor_id=canceller.id)

        assert _decision(db_session, suggestion.id) == ("rejected", canceller.id)

    def test_cancel_without_a_pending_family_does_nothing(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        cancel_pending_family(db_session, context_id=context_id, actor_id=scene.user.id)

        assert _pending_events(db_session, context_id) == []


# ---------------------------------------------------------------------------
#  Ожидание: применение, ошибки, живая привязка (спека §2.5 п. 3, 5, 6)
# ---------------------------------------------------------------------------

def _pending_scene(db, factories, *, source="manual", with_job=False):
    """Контекст семьи A с вариантом и ожиданием семьи B, поставленным настоящей
    операцией; предложение (для `suggestion` и `auto_suggestion`) решено по
    своему маршруту."""
    scene = _two_families(db, factories)
    context_id = scene.context_ids[0]
    scene.old_variant = _with_variant(db, scene, context_id, scene.family)
    scene.suggestion = None
    if source != "manual":
        scene.suggestion = _publish(db, context_id, family_id=scene.family_b.id)
        scene.suggestion.decision = (
            "auto_pending" if source == "auto_suggestion" else "accepted_pending"
        )
        scene.suggestion.decided_by = None if source == "auto_suggestion" else scene.user.id
        scene.suggestion.decided_at = dt.datetime.now(dt.UTC)
        db.flush()
    request_family_change(
        db, context_id=context_id, family_id=scene.family_b.id,
        actor_id=None if source == "auto_suggestion" else scene.user.id, source=source,
        suggestion_id=scene.suggestion.id if scene.suggestion else None,
        threshold=THRESHOLD if source == "auto_suggestion" else None,
    )
    scene.context_id = context_id
    scene.schema_b = _current_schema(db, scene.family_b)
    return scene


def _apply_pending(db, scene, **kwargs):
    return apply_values(
        db, context_id=scene.context_id, schema_id=scene.schema_b.id, answer=_answer(),
        paths_hash="paths-1", guard=None, settings=_settings(), **kwargs,
    )


class TestPendingIsApplied:
    @pytest.mark.parametrize(
        ("source", "before", "after"),
        [("auto_suggestion", "auto_pending", "auto_accepted"),
         ("suggestion", "accepted_pending", "accepted")],
    )
    def test_applying_the_values_switches_family_and_variant_and_settles_the_suggestion(
        self, db_session, factories, source, before, after
    ):
        scene = _pending_scene(db_session, factories, source=source)
        assert _decision(db_session, scene.suggestion.id)[0] == before

        outcome = _apply_pending(db_session, scene)

        assert outcome.applied and outcome.family_switched
        context = _ctx(db_session, scene.context_id)
        assert context.work_family_id == scene.family_b.id
        assert context.work_variant_id not in (None, scene.old_variant.id)
        assert context.family_source == source
        assert _pending_columns(context) == (None, None, None, None, None)
        assert _decision(db_session, scene.suggestion.id)[0] == after
        events = _pending_events(db_session, scene.context_id)
        assert [name for name, _ in events] == ["set", "applied"]
        assert events[1][1]["pending_family_id"] == scene.family_b.id

    def test_a_manual_pending_is_applied_without_a_suggestion_to_settle(
        self, db_session, factories
    ):
        scene = _pending_scene(db_session, factories, source="manual")

        outcome = _apply_pending(db_session, scene)

        assert outcome.applied
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            scene.family_b.id, "manual", scene.user.id,
        )

    def test_the_threshold_of_the_applied_event_is_the_one_stored_with_the_pending(
        self, db_session, factories, monkeypatch
    ):
        scene = _pending_scene(db_session, factories, source="auto_suggestion")
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.99"))

        _apply_pending(db_session, scene)

        events = _pending_events(db_session, scene.context_id)
        assert events[1][1]["threshold"] == "0.80"
        [assigned] = _events(db_session, "context_family_assigned", context_id=scene.context_id)
        assert assigned.payload["threshold"] == "0.80"

    def test_the_old_variant_is_archived_when_the_switch_leaves_it_empty(
        self, db_session, factories
    ):
        scene = _pending_scene(db_session, factories, source="manual")

        outcome = _apply_pending(db_session, scene)

        assert outcome.previous_archived is True
        db_session.expire_all()
        assert db_session.get(WorkVariant, scene.old_variant.id).status == "archived"


class TestSwitchIsOneTransaction:
    def test_a_failure_after_the_switch_leaves_family_variant_and_pending_untouched(
        self, committing_db, committing_factories, committing_session_factory
    ):
        scene = _pending_scene(committing_db, committing_factories, source="auto_suggestion")
        committing_db.commit()
        context_id = scene.context_id

        with (
            mock.patch.object(
                work_variants_module, "archive_variant_if_empty", side_effect=RuntimeError("boom")
            ),
            committing_session_factory() as db,
        ):
            with pytest.raises(RuntimeError):
                apply_values(
                    db, context_id=context_id, schema_id=scene.schema_b.id, answer=_answer(),
                    paths_hash="paths-1", guard=None, settings=_settings(),
                )
            db.rollback()

        with committing_session_factory() as probe:
            context = probe.get(CatalogContext, context_id)
            assert (context.work_family_id, context.work_variant_id) == (
                scene.family.id, scene.old_variant.id,
            )
            assert context.pending_family_id == scene.family_b.id
            assert probe.get(FamilySuggestion, scene.suggestion.id).decision == "auto_pending"
            assert [
                e.payload["outcome"]
                for e in probe.execute(
                    sa.select(SemanticEvent).where(
                        SemanticEvent.context_id == context_id,
                        SemanticEvent.event_type == "context_family_pending",
                    )
                ).scalars()
            ] == ["set"]


class TestPendingSurvivesFailures:
    def test_a_schema_error_leaves_the_pending_family_and_the_old_variant(
        self, db_session, factories
    ):
        from services.semantic_answer import AnswerSchemaError

        scene = _pending_scene(db_session, factories, source="manual")
        wrong = _answer((9, "value", "нет такого", "name"))

        with pytest.raises(AnswerSchemaError):
            apply_values(
                db_session, context_id=scene.context_id, schema_id=scene.schema_b.id,
                answer=wrong, paths_hash="paths-1", guard=None, settings=_settings(),
            )

        context = _ctx(db_session, scene.context_id)
        assert context.pending_family_id == scene.family_b.id
        assert (context.work_family_id, context.work_variant_id) == (
            scene.family.id, scene.old_variant.id,
        )


    def test_a_privacy_hold_of_the_values_job_leaves_the_pending_family_and_the_old_variant(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _two_families(db_session, factories, titles=("Стяжка Ромашка Строй",))
        context_id = scene.context_ids[0]
        old_variant = _with_variant(db_session, scene, context_id, scene.family)
        # У схемы ожидаемой семьи есть параметр: без параметров модели нечего
        # спрашивать, и приватность не проверяется.
        _value(db_session, _param(db_session, _current_schema(db_session, scene.family_b)), "50 mm")
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        [values_job] = _values_jobs(db_session, context_id=context_id)

        # У привязанного контекста есть и задание предложения: каждое
        # удерживается своим вызовом.
        later = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        for _ in range(3):
            assert claim_next(db_session, settings=S, now=later) is None

        db_session.expire_all()
        assert db_session.get(SemanticJob, values_job.id).status == "privacy_hold"
        context = _ctx(db_session, context_id)
        assert context.pending_family_id == scene.family_b.id
        assert (context.work_family_id, context.work_variant_id) == (
            scene.family.id, old_variant.id,
        )


class TestPendingFamilyIsALiveLink:
    def test_archive_refuses_a_family_that_only_waits_for_a_context(self, db_session, factories):
        scene = _pending_scene(db_session, factories, source="manual")

        with pytest.raises(WorkFamilyError) as caught:
            archive_family(db_session, family_id=scene.family_b.id, actor_id=scene.user.id)

        assert caught.value.code == REFUSE_ARCHIVE_WITH_LINKS
        assert caught.value.count == 1

    def test_set_unit_refuses_a_family_that_only_waits_for_a_context(self, db_session, factories):
        scene = _pending_scene(db_session, factories, source="manual")

        with pytest.raises(WorkFamilyError) as caught:
            set_unit(
                db_session, family_id=scene.family_b.id, unit_name="PCS", actor_id=scene.user.id
            )

        assert caught.value.code == REFUSE_UNIT_CHANGE_WITH_LINKS

    def test_a_family_without_links_is_still_archived(self, db_session, factories):
        scene = _pending_scene(db_session, factories, source="manual")

        archived = archive_family(db_session, family_id=scene.family_c.id, actor_id=scene.user.id)

        assert archived.status == "archived"

    def test_the_pending_link_is_released_by_cancelling_it(self, db_session, factories):
        scene = _pending_scene(db_session, factories, source="manual")
        cancel_pending_family(db_session, context_id=scene.context_id, actor_id=scene.user.id)

        archived = archive_family(db_session, family_id=scene.family_b.id, actor_id=scene.user.id)

        assert archived.status == "archived"

    @pytest.mark.parametrize("breakage", ["archived", "other_unit"])
    @pytest.mark.parametrize(
        ("source", "before", "after", "author"),
        [("auto_suggestion", "auto_pending", "auto_superseded", False),
         ("suggestion", "accepted_pending", "rejected", True)],
    )
    def test_a_family_that_stopped_fitting_cancels_the_pending_and_the_result_is_not_applied(
        self, db_session, factories, breakage, source, before, after, author
    ):
        scene = _pending_scene(db_session, factories, source=source)
        pcs = _unit_id(db_session, "PCS")
        family = db_session.get(WorkFamily, scene.family_b.id)
        if breakage == "archived":
            family.status = "archived"
            family.archived_at = dt.datetime.now(dt.UTC)
        else:
            family.unit_id = pcs
        db_session.flush()
        before_state = _decision(db_session, scene.suggestion.id)
        assert before_state[0] == before

        outcome = _apply_pending(db_session, scene)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        context = _ctx(db_session, scene.context_id)
        assert _pending_columns(context) == (None, None, None, None, None)
        assert (context.work_family_id, context.work_variant_id) == (
            scene.family.id, scene.old_variant.id,
        )
        decision, decided_by = _decision(db_session, scene.suggestion.id)
        assert decision == after
        assert (decided_by == scene.user.id) is author
        events = _pending_events(db_session, scene.context_id)
        assert [name for name, _ in events] == ["set", "cancelled"]

    def test_the_cancelled_pending_closes_the_job_as_not_applicable(self, db_session, factories):
        from tests.integration.test_work_variants_core import _guarded

        scene = _pending_scene(db_session, factories, source="manual")
        family = db_session.get(WorkFamily, scene.family_b.id)
        family.status = "archived"
        family.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.execute(sa.delete(SemanticJob))
        job, guard = _guarded(
            db_session, context_id=scene.context_id, schema_id=scene.schema_b.id
        )

        outcome = apply_values(
            db_session, context_id=scene.context_id, schema_id=scene.schema_b.id,
            answer=_answer(), paths_hash="paths-1", guard=guard, settings=_settings(),
        )

        assert outcome.unapplied_reason == "not_applicable"
        db_session.expire_all()
        job = db_session.get(SemanticJob, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")


class TestTerminalOutcomesStayTerminal:
    """`auto_accepted`, `accepted`, `rejected`, `auto_superseded` не меняются ни
    правилами публикации, ни подтверждением: тот же отпечаток не воскресает."""

    @pytest.mark.parametrize(
        "terminal", ["auto_accepted", "accepted", "rejected", "auto_superseded"]
    )
    def test_a_decided_suggestion_is_untouched_by_the_rules_and_the_confirmation(
        self, db_session, factories, terminal
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        suggestion.confidence = Decimal("0.99")
        suggestion.decision = terminal
        suggestion.decided_by = None if terminal.startswith("auto") else scene.user.id
        suggestion.decided_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        rules = apply_publication_rules(
            db_session, suggestion_id=suggestion.id, threshold=THRESHOLD
        )
        report = confirm_suggestions(
            db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id
        )
        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        assert rules is None
        assert (report.confirmed, report.skipped) == ([], [suggestion.id])
        assert _decision(db_session, suggestion.id)[0] == terminal
        assert _ctx(db_session, context_id).work_family_id is None


# ---------------------------------------------------------------------------
#  Таблица публикации (спека §2.5, семь строк)
# ---------------------------------------------------------------------------

def _rules_scene(db, factories, *, confidence="0.9", suggested="b"):
    """Две семьи единицы, контекст и опубликованное предложение на текущий
    отпечаток; семья предложения — B или A (`suggested`)."""
    scene = _two_families(db, factories)
    scene.context_id = scene.context_ids[0]
    target = scene.family_b if suggested == "b" else scene.family
    scene.suggestion = _publish(db, scene.context_id, family_id=target.id)
    scene.suggestion.confidence = Decimal(confidence)
    db.flush()
    return scene


def _rules(db, scene, threshold=THRESHOLD):
    return apply_publication_rules(db, suggestion_id=scene.suggestion.id, threshold=threshold)


class TestRowOneAndTwoNoFamilyNoVariant:
    def test_row_1_at_or_above_the_threshold_assigns_with_the_auto_source(
        self, db_session, factories
    ):
        scene = _rules_scene(db_session, factories, confidence="0.9")

        outcome = _rules(db_session, scene)

        assert outcome == FamilyChangeOutcome("assigned", scene.context_id, scene.family_b.id, None)
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            scene.family_b.id, "auto_suggestion", None,
        )
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)
        assert _sug(db_session, scene.suggestion.id).decided_at is not None
        [event] = _events(db_session, "context_family_assigned", context_id=scene.context_id)
        assert (event.actor_id, event.payload["threshold"], event.payload["confidence"]) == (
            None, "0.80", "0.9",
        )

    def test_row_1_sets_the_values_job_of_the_new_family(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        db_session.execute(sa.delete(SemanticJob).where(SemanticJob.kind == "context_values"))

        _rules(db_session, scene)

        [job] = _values_jobs(db_session, context_id=scene.context_id)
        assert job.schema_id == _current_schema(db_session, scene.family_b).id

    def test_row_2_below_the_threshold_leaves_the_suggestion_to_the_human(
        self, db_session, factories
    ):
        scene = _rules_scene(db_session, factories, confidence="0.7")

        outcome = _rules(db_session, scene)

        assert outcome is None
        assert _ctx(db_session, scene.context_id).work_family_id is None
        suggestion = _sug(db_session, scene.suggestion.id)
        assert (suggestion.decision, suggestion.is_published) == (None, True)

    def test_exactly_on_the_threshold_is_accepted(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.80")

        assert _rules(db_session, scene).kind == "assigned"

    def test_one_least_significant_digit_below_the_threshold_is_not_accepted(
        self, db_session, factories
    ):
        scene = _rules_scene(db_session, factories, confidence="0.79")

        assert _rules(db_session, scene) is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    def test_an_empty_threshold_makes_no_auto_binding(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="1")

        assert _rules(db_session, scene, threshold=None) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    def test_an_answer_without_a_family_is_left_to_the_human(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=None)
        suggestion.confidence = Decimal("0.99")
        db_session.flush()

        assert apply_publication_rules(
            db_session, suggestion_id=suggestion.id, threshold=THRESHOLD
        ) is None
        assert _decision(db_session, suggestion.id) == (None, None)


class TestRowThreeSameFamily:
    @pytest.mark.parametrize("confidence", ["0.9", "0.2"])
    def test_the_same_family_is_accepted_at_any_confidence_and_nothing_else_changes(
        self, db_session, factories, confidence
    ):
        scene = _rules_scene(db_session, factories, confidence=confidence, suggested="a")
        variant = _with_variant(db_session, scene, scene.context_id, scene.family, "manual")

        outcome = _rules(db_session, scene)

        assert outcome.kind == "unchanged"
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source, context.work_variant_id) == (
            scene.family.id, "manual", variant.id,
        )
        assert _events(db_session, "context_family_assigned", context_id=scene.context_id) == []

    def test_an_auto_pending_of_another_family_is_withdrawn_with_its_suggestion(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family, "auto_suggestion")
        old = _publish(
            db_session, context_id, family_id=scene.family_b.id, is_published=False
        )
        old.decision, old.decided_at = "auto_pending", dt.datetime.now(dt.UTC)
        context = _ctx(db_session, context_id)
        context.pending_family_id = scene.family_b.id
        context.pending_family_source = "auto_suggestion"
        context.pending_suggestion_id = old.id
        context.pending_threshold = THRESHOLD
        context.pending_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.execute(sa.delete(SemanticJob).where(SemanticJob.kind == "context_values"))
        values_job = SemanticJob(
            kind="context_values", context_id=context_id,
            schema_id=_current_schema(db_session, scene.family_b).id, paths_hash="x",
            request_hash="h", status="pending", prompt_version="v1", model_requested="m",
            place_dictionary_version=1, candidates_hash="c", prefix_hash="p", input_hash="i",
            response_schema_version="v1", serialization_version="v1",
        )
        db_session.add(values_job)
        db_session.flush()
        scene.context_id = context_id
        newer = _publish(db_session, context_id, family_id=scene.family.id)
        newer.confidence = Decimal("0.9")
        db_session.flush()
        scene.suggestion = newer

        outcome = _rules(db_session, scene)

        assert outcome.kind == "unchanged"
        assert outcome.superseded_suggestion_id == old.id
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)
        assert _decision(db_session, old.id) == ("auto_superseded", None)
        assert _decision(db_session, newer.id) == ("auto_accepted", None)
        db_session.expire_all()
        assert db_session.get(SemanticJob, values_job.id).status == "cancelled"
        assert [name for name, _ in _pending_events(db_session, context_id)] == ["superseded"]

    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    def test_a_human_pending_of_another_family_is_not_touched(
        self, db_session, factories, source
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family, "auto_suggestion")
        human = None
        if source == "suggestion":
            human = _publish(
                db_session, context_id, family_id=scene.family_b.id, is_published=False
            )
            human.decision, human.decided_by = "accepted_pending", scene.user.id
            human.decided_at = dt.datetime.now(dt.UTC)
        context = _ctx(db_session, context_id)
        context.pending_family_id = scene.family_b.id
        context.pending_family_source = source
        context.pending_suggestion_id = human.id if human else None
        context.pending_by = scene.user.id if source == "manual" else None
        context.pending_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        newer = _publish(db_session, context_id, family_id=scene.family.id)
        scene.context_id, scene.suggestion = context_id, newer
        before = _pending_columns(_ctx(db_session, context_id))

        outcome = _rules(db_session, scene)

        assert outcome.kind == "unchanged"
        assert outcome.superseded_suggestion_id is None
        assert _pending_columns(_ctx(db_session, context_id)) == before
        assert _decision(db_session, newer.id) == ("auto_accepted", None)
        if human:
            assert _decision(db_session, human.id)[0] == "accepted_pending"


class TestRowsFourToSevenAnotherFamily:
    def test_row_4_auto_family_without_a_variant_changes_at_once(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        _bind_source(db_session, scene, scene.context_id, scene.family, "auto_suggestion")

        outcome = _rules(db_session, scene)

        assert outcome.kind == "assigned"
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source) == (
            scene.family_b.id, "auto_suggestion",
        )
        assert _pending_columns(context) == (None, None, None, None, None)
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)

    def test_row_4_cancels_the_old_values_job_and_sets_a_new_one(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        _bind_source(db_session, scene, scene.context_id, scene.family, "auto_suggestion")
        reconcile_semantic_jobs(
            db_session, [scene.context_id], cap=NO_CAP, source="operation"
        )
        [old_job] = _values_jobs(db_session, context_id=scene.context_id)
        assert old_job.schema_id == _current_schema(db_session, scene.family).id

        _rules(db_session, scene)

        db_session.expire_all()
        assert db_session.get(SemanticJob, old_job.id).status == "cancelled"
        live = [
            j for j in _values_jobs(db_session, context_id=scene.context_id)
            if j.status == "pending"
        ]
        assert [j.schema_id for j in live] == [_current_schema(db_session, scene.family_b).id]

    def test_row_5_auto_family_with_a_variant_becomes_a_pending_family(
        self, db_session, factories
    ):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        variant = _with_variant(
            db_session, scene, scene.context_id, scene.family, "auto_suggestion"
        )

        outcome = _rules(db_session, scene, threshold=Decimal("0.85"))

        assert outcome.kind == "pending"
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (
            scene.family_b.id, "auto_suggestion", scene.suggestion.id, None, Decimal("0.85"),
        )
        assert _decision(db_session, scene.suggestion.id) == ("auto_pending", None)

    @pytest.mark.parametrize("variant", [False, True])
    def test_row_6_auto_family_below_the_threshold_is_published_for_the_human(
        self, db_session, factories, variant
    ):
        scene = _rules_scene(db_session, factories, confidence="0.5")
        if variant:
            _with_variant(db_session, scene, scene.context_id, scene.family, "auto_suggestion")
        else:
            _bind_source(db_session, scene, scene.context_id, scene.family, "auto_suggestion")

        outcome = _rules(db_session, scene)

        assert outcome is None
        assert _ctx(db_session, scene.context_id).work_family_id == scene.family.id
        suggestion = _sug(db_session, scene.suggestion.id)
        assert (suggestion.decision, suggestion.is_published) == (None, True)

    @pytest.mark.parametrize("variant", [False, True])
    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    def test_row_7_a_human_family_is_not_touched_at_any_confidence(
        self, db_session, factories, source, variant
    ):
        scene = _rules_scene(db_session, factories, confidence="0.99")
        if variant:
            _with_variant(db_session, scene, scene.context_id, scene.family, source)
        else:
            _bind_source(db_session, scene, scene.context_id, scene.family, source)
        before = _pending_columns(_ctx(db_session, scene.context_id))

        outcome = _rules(db_session, scene)

        assert outcome is None
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source) == (scene.family.id, source)
        assert _pending_columns(context) == before
        suggestion = _sug(db_session, scene.suggestion.id)
        assert (suggestion.decision, suggestion.is_published) == (None, True)

    def test_a_human_pending_family_blocks_the_rule_from_a_new_pending(
        self, db_session, factories
    ):
        scene = _rules_scene(db_session, factories, confidence="0.99")
        _with_variant(db_session, scene, scene.context_id, scene.family, "auto_suggestion")
        context = _ctx(db_session, scene.context_id)
        context.pending_family_id = scene.family_c.id
        context.pending_family_source = "manual"
        context.pending_by = scene.user.id
        context.pending_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        outcome = _rules(db_session, scene)

        assert outcome is None
        assert _ctx(db_session, scene.context_id).pending_family_id == scene.family_c.id
        assert _decision(db_session, scene.suggestion.id) == (None, None)


class TestRulesRecheck:
    def test_a_suggestion_decided_by_a_human_meanwhile_is_not_touched(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        scene.suggestion.decision = "rejected"
        scene.suggestion.decided_by = scene.user.id
        scene.suggestion.decided_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        assert _rules(db_session, scene) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None

    def test_an_unpublished_suggestion_is_not_touched(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        scene.suggestion.is_published = False
        scene.suggestion.unpublished_reason = "stale_fingerprint"
        db_session.flush()

        assert _rules(db_session, scene) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None

    def test_a_suggestion_with_a_stale_fingerprint_is_not_touched(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        _active_family(db_session, title="Семья D", unit_name="M2", actor_id=scene.user.id)

        assert _rules(db_session, scene) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    def test_a_context_that_stopped_being_applicable_is_not_touched(self, db_session, factories):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == scene.context_id)
            .values(semantic_state="NOT_APPLICABLE")
        )
        db_session.expire_all()

        assert _rules(db_session, scene) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None

    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_a_context_of_a_header_or_trash_row_is_not_touched(self, db_session, factories, kind):
        scene = _rules_scene(db_session, factories, confidence="0.9")
        catalog_id = db_session.execute(
            sa.select(ContextBucket.catalog_position_id)
            .join(CatalogContext, CatalogContext.bucket_id == ContextBucket.id)
            .where(CatalogContext.id == scene.context_id)
        ).scalar_one()
        db_session.get(CatalogPosition, catalog_id).kind = kind
        db_session.flush()

        assert _rules(db_session, scene) is None
        assert _ctx(db_session, scene.context_id).work_family_id is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    def test_a_missing_suggestion_is_ignored(self, db_session, factories):
        assert apply_publication_rules(db_session, suggestion_id=987654, threshold=THRESHOLD) is None


# ---------------------------------------------------------------------------
#  Правила публикации в потоке исполнителя
# ---------------------------------------------------------------------------

_SETTINGS_ON = S.model_copy(update={"SEMANTIC_AUTO_ACCEPT_THRESHOLD": THRESHOLD})


class TestRulesRunAfterTheResultIsRecorded:
    def test_process_one_applies_the_rules_after_recording_the_answer(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response(_model_answer(scene.family.id, "0.9")))

        assert process_one(
            committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock
        ) is True

        committing_db.expire_all()
        context = committing_db.get(CatalogContext, scene.context_ids[0])
        assert (context.work_family_id, context.family_source) == (
            scene.family.id, "auto_suggestion",
        )
        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert suggestion.decision == "auto_accepted"
        assert committing_db.get(SemanticJob, scene.jobs[0].id).status == "done"

    def test_without_a_threshold_the_suggestion_stays_published_for_the_human(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response(_model_answer(scene.family.id, "0.99")))

        process_one(committing_session_factory, client, settings=S, clock=_clock)

        committing_db.expire_all()
        assert committing_db.get(CatalogContext, scene.context_ids[0]).work_family_id is None
        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert (suggestion.decision, suggestion.is_published) == (None, True)

    def test_a_failure_of_the_rules_does_not_touch_the_recorded_answer(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response(_model_answer(scene.family.id, "0.9")))

        def _boom(*args, **kwargs):
            raise RuntimeError("rules failed")

        monkeypatch.setattr(worker_module, "apply_publication_rules", _boom)

        assert process_one(
            committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock
        ) is True

        committing_db.expire_all()
        assert committing_db.get(SemanticJob, scene.jobs[0].id).status == "done"
        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert (suggestion.decision, suggestion.is_published) == (None, True)
        assert committing_db.get(CatalogContext, scene.context_ids[0]).work_family_id is None

    def test_a_failure_of_the_rules_is_logged_as_such_and_not_as_an_open_attempt(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch,
        caplog,
    ):
        """Ошибка правил ловится их собственной обёрткой: в журнале — «правила
        не применены», а не «попытка осталась открытой» (последнее значило бы,
        что задание вернёт восстановление, а ответ уже записан)."""
        import logging

        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response(_model_answer(scene.family.id, "0.9")))

        def _boom(*args, **kwargs):
            raise RuntimeError("rules failed")

        monkeypatch.setattr(worker_module, "apply_publication_rules", _boom)

        with caplog.at_level(logging.ERROR, logger="services.semantic_worker"):
            process_one(committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock)

        messages = [r.getMessage() for r in caplog.records if r.name == "services.semantic_worker"]
        assert any("Правила публикации предложения" in m for m in messages), messages
        assert not any("осталась открытой" in m for m in messages), messages

    def test_an_unpublished_result_is_not_offered_to_the_rules(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response("not json at all"))
        calls: list[int] = []
        monkeypatch.setattr(
            worker_module, "apply_publication_rules",
            lambda db, **kwargs: calls.append(kwargs["suggestion_id"]),
        )

        process_one(committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock)

        assert calls == []


# ---------------------------------------------------------------------------
#  Гонки двух сессий
# ---------------------------------------------------------------------------

_JOIN = 30.0


class TestRulesAgainstAHumanDecisionMeanwhile:
    @pytest.mark.parametrize("case", ["rejected_and_unpublished", "decided_and_still_published"])
    def test_a_decision_committed_between_the_read_and_the_locks_is_not_overwritten(
        self, committing_session_factory, committing_db, committing_factories, case
    ):
        """Правила читают предложение без блокировок, затем берут семьи и
        контекст и перепроверяют его под блокировкой. Человек решил
        предложение между чтением и блокировками: правило обязано увидеть
        решение и не привязывать семью. Во втором входе публикация не снята —
        нарушено ровно условие «решения нет»."""
        from tests.integration.test_work_variants_concurrency import _Scene

        scene = _rules_scene(committing_db, committing_factories, confidence="0.95")
        committing_db.commit()
        runner = _Scene(committing_session_factory)

        def _is_first_read(statement: str) -> bool:
            return (
                statement.lstrip().startswith("SELECT family_suggestions.context_id")
                and "FOR " not in statement
            )

        suggestion_id, user_id, context_id = (
            scene.suggestion.id, scene.user.id, scene.context_id,
        )

        def work(db):
            return apply_publication_rules(db, suggestion_id=suggestion_id, threshold=THRESHOLD)

        try:
            runner.spawn("rules", work, pause_on=_is_first_read)
            assert runner.wait_paused("rules"), "правила не дошли до первого чтения"
            with committing_session_factory() as other:
                if case == "rejected_and_unpublished":
                    reject_suggestion(other, suggestion_id=suggestion_id, actor_id=user_id)
                else:
                    other.execute(
                        sa.text(
                            "UPDATE family_suggestions SET decision = 'rejected', "
                            "decided_by = :u, decided_at = now() WHERE id = :id"
                        ),
                        {"u": user_id, "id": suggestion_id},
                    )
                other.commit()
            runner.release["rules"].set()
            runner.done["rules"].wait(timeout=_JOIN)
        finally:
            runner.finish()

        runner.assert_clean()
        assert runner.results["rules"] is None
        with committing_session_factory() as probe:
            suggestion = probe.get(FamilySuggestion, suggestion_id)
            assert (suggestion.decision, suggestion.decided_by) == ("rejected", user_id)
            assert probe.get(CatalogContext, context_id).work_family_id is None


class TestRecordResultAndTheRules:
    def test_the_rules_start_after_the_recording_transaction_has_ended(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """Правила публикации идут новой транзакцией ПОСЛЕ коммита записи
        ответа: к моменту их вызова задание не заблокировано, а предложение
        уже видно другой сессии. Вызов внутри `record_result` держал бы задание
        и брал бы семьи и контекст после него: порядок «задание -> домен»."""
        scene = _committed_scene(committing_db, committing_factories)
        job_id = scene.jobs[0].id
        seen: dict[str, object] = {}

        def _probe(db, *, suggestion_id, threshold):
            with committing_session_factory() as other:
                try:
                    other.execute(
                        sa.text("SELECT id FROM semantic_jobs WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": job_id},
                    )
                    seen["job_free"] = True
                except sa.exc.OperationalError:
                    seen["job_free"] = False
                seen["visible"] = other.execute(
                    sa.text("SELECT is_published FROM family_suggestions WHERE id = :id"),
                    {"id": suggestion_id},
                ).scalar_one_or_none()
                other.rollback()

        monkeypatch.setattr(worker_module, "apply_publication_rules", _probe)
        client = _FakeClient(_response(_model_answer(scene.family.id, "0.9")))

        process_one(committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock)

        assert seen["job_free"] is True
        assert seen["visible"] is True

    @staticmethod
    def _blocked_query(factory, pid: int, *, timeout: float = 10.0) -> str | None:
        """Текст запроса, на котором backend `pid` стоит в ожидании замка (по
        `pg_blocking_pids`); `None` — не встал за `timeout`."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with factory() as probe:
                row = probe.execute(
                    sa.text(
                        "SELECT cardinality(pg_blocking_pids(:pid)) > 0 AS blocked, "
                        "(SELECT query FROM pg_stat_activity WHERE pid = :pid) AS query"
                    ),
                    {"pid": pid},
                ).one()
                probe.rollback()
            if row.blocked:
                return row.query or ""
            time.sleep(0.05)
        return None

    @staticmethod
    def _job_is_free(factory, job_id: int) -> bool:
        with factory() as probe:
            try:
                probe.execute(
                    sa.text("SELECT id FROM semantic_jobs WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": job_id},
                )
                return True
            except sa.exc.OperationalError:
                return False
            finally:
                probe.rollback()

    def _run(self, factory, scene_parts, *, lost_claim: bool, hold_kind: str):
        """Операция держит контекст (`hold_kind="context"`) либо семью ответа
        (`"family"`) и затем сверяет задание, пока запись ответа ждёт. Возвращает
        запрос, на котором встала запись, и свободно ли было задание."""
        from tests.integration.test_work_variants_concurrency import _Scene

        context_id, family_id, user_id, job_id, claim = scene_parts
        if lost_claim:
            with factory() as db:
                db.execute(
                    sa.text(
                        "UPDATE semantic_jobs SET status = 'pending', claim_token = NULL "
                        "WHERE id = :id"
                    ),
                    {"id": job_id},
                )
                db.commit()
        holder = _Scene(factory)
        recorder = _Scene(factory)

        def _paused_on_context(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM catalog_contexts" in statement

        def _paused_on_family(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM work_families" in statement

        def hold(db):
            if hold_kind == "context":
                request_family_change(
                    db, context_id=context_id, family_id=family_id, actor_id=user_id,
                    source="manual",
                )
            else:
                db.execute(
                    sa.text("SELECT id FROM work_families WHERE id = :id FOR UPDATE"),
                    {"id": family_id},
                )
            # Контекст перестаёт быть применимым, и сверка под удерживаемой
            # строкой берёт задание, чтобы отменить его.
            db.get(CatalogContext, context_id).semantic_state = "NOT_APPLICABLE"
            db.flush()
            reconcile_semantic_jobs(db, [context_id], cap=NO_CAP, source="operation")
            return "held"

        def record(db):
            record_result(db, claim, _response(_model_answer(family_id, "0.9")), now=NOW, settings=S)
            return "recorded"

        seen: dict[str, object] = {}
        try:
            holder.spawn(
                "holder", hold,
                pause_on=_paused_on_context if hold_kind == "context" else _paused_on_family,
            )
            assert holder.wait_paused("holder"), "операция не дошла до блокировки"
            recorder.spawn("recorder", record)
            assert recorder.wait_pid("recorder")
            seen["query"] = self._blocked_query(factory, recorder.pids["recorder"])
            seen["job_free"] = self._job_is_free(factory, job_id)
            holder.release["holder"].set()
            holder.done["holder"].wait(timeout=_JOIN)
            recorder.done["recorder"].wait(timeout=_JOIN)
        finally:
            holder.finish()
            recorder.finish()

        holder.assert_clean()
        recorder.assert_clean()
        assert (holder.results["holder"], recorder.results["recorder"]) == ("held", "recorded")
        return seen

    def _scene_parts(self, db, factories, factory):
        scene = _committed_scene(db, factories)
        with factory() as session:
            claim = claim_next(session, settings=S, now=NOW)
            assert claim is not None
        return (
            scene.context_ids[0], scene.family.id, scene.user.id, scene.jobs[0].id, claim
        )

    @staticmethod
    def _assert_domain_before_job(seen, table: str) -> None:
        assert seen["query"] is not None, "запись ответа не встала на замке"
        assert "FOR KEY SHARE" in seen["query"] and f"FROM {table}" in seen["query"], seen["query"]
        assert seen["job_free"] is True, "запись ответа ждёт домен, уже держа задание"

    def test_the_recording_waits_on_the_context_before_it_takes_the_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Запись ответа берёт контекст `FOR KEY SHARE` ДО замка задания: пока
        контекст у операции, она стоит на нём, не держа задания (порядок
        «домен -> задание»), и обе стороны заканчивают."""
        parts = self._scene_parts(
            committing_db, committing_factories, committing_session_factory
        )

        seen = self._run(
            committing_session_factory, parts, lost_claim=False, hold_kind="context"
        )

        self._assert_domain_before_job(seen, "catalog_contexts")
        with committing_session_factory() as probe:
            assert probe.get(CatalogContext, parts[0]).work_family_id == parts[1]

    def test_a_lost_claim_and_an_operation_holding_the_context_finish_without_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Потерянный захват возвращает задание в `pending`: операция, держащая
        контекст и сверяющая это задание, не замыкает цикл с записью ответа."""
        parts = self._scene_parts(
            committing_db, committing_factories, committing_session_factory
        )

        seen = self._run(
            committing_session_factory, parts, lost_claim=True, hold_kind="context"
        )

        self._assert_domain_before_job(seen, "catalog_contexts")
        with committing_session_factory() as probe:
            job = probe.get(SemanticJob, parts[3])
            assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")
            [suggestion] = probe.execute(sa.select(FamilySuggestion)).scalars().all()
            assert (suggestion.is_published, suggestion.unpublished_reason) == (
                False, "lost_claim",
            )

    def test_a_lost_claim_and_an_operation_holding_the_answer_family_finish_without_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """То же для семьи ответа, взятой `FOR UPDATE` (слияние, архивирование):
        запись ждёт её `FOR KEY SHARE` до замка задания."""
        parts = self._scene_parts(
            committing_db, committing_factories, committing_session_factory
        )

        seen = self._run(
            committing_session_factory, parts, lost_claim=True, hold_kind="family"
        )

        self._assert_domain_before_job(seen, "work_families")
        with committing_session_factory() as probe:
            job = probe.get(SemanticJob, parts[3])
            assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_the_recording_takes_the_family_before_the_context_like_the_values_handler(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Ключевые замки записи идут в порядке домена «семья -> контекст» (ревью
        задачи 8, круг 1): обработчик значений держит семью `FOR UPDATE` и затем
        берёт контекст `FOR UPDATE`. Запись, взявшая контекст раньше семьи,
        держала бы ключевой замок контекста, ждала семью — и обработчик встал
        бы на контексте встречно."""
        from tests.integration.test_work_variants_concurrency import _Scene

        context_id, family_id, _user_id, _job_id, claim = self._scene_parts(
            committing_db, committing_factories, committing_session_factory
        )
        holder = _Scene(committing_session_factory)
        recorder = _Scene(committing_session_factory)

        def _paused_on_family(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM work_families" in statement

        def handler(db):
            db.execute(
                sa.text("SELECT id FROM work_families WHERE id = :id FOR UPDATE"), {"id": family_id}
            )
            db.execute(
                sa.text("SELECT id FROM catalog_contexts WHERE id = :id FOR UPDATE"),
                {"id": context_id},
            )
            return "handled"

        def record(db):
            record_result(db, claim, _response(_model_answer(family_id, "0.9")), now=NOW, settings=S)
            return "recorded"

        try:
            holder.spawn("handler", handler, pause_on=_paused_on_family)
            assert holder.wait_paused("handler"), "обработчик не взял семью"
            recorder.spawn("recorder", record)
            assert recorder.wait_pid("recorder")
            query = self._blocked_query(committing_session_factory, recorder.pids["recorder"])
            holder.release["handler"].set()
            holder.done["handler"].wait(timeout=_JOIN)
            recorder.done["recorder"].wait(timeout=_JOIN)
        finally:
            holder.finish()
            recorder.finish()

        holder.assert_clean()
        recorder.assert_clean()
        assert query is not None and "FROM work_families" in query, query
        assert (holder.results["handler"], recorder.results["recorder"]) == ("handled", "recorded")


class TestLockSetOfARequest:
    """Захват семей решения: все семьи — запрашиваемая, текущая и ожидаемая —
    держатся `FOR SHARE` до фиксации (иначе обработчик значений, берущий их
    `FOR UPDATE`, замыкает цикл с операцией, добирающей семью при удержании
    контекста)."""

    def _paused_request(self, factories, db, factory, scene, *, target, name):
        from tests.integration.test_work_variants_concurrency import _Scene

        runner = _Scene(factory)

        def _is_context_lock(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM catalog_contexts" in statement

        context_id, target_id, user_id = scene.context_ids[0], target.id, scene.user.id

        def work(session):
            return request_family_change(
                session, context_id=context_id, family_id=target_id, actor_id=user_id,
                source="manual",
            )

        runner.spawn(name, work, pause_on=_is_context_lock)
        assert runner.wait_paused(name), "запрос не дошёл до блокировки контекста"
        return runner

    @staticmethod
    def _locked(factory, family_id) -> bool:
        with factory() as probe:
            probe.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
            try:
                probe.execute(
                    sa.text("SELECT id FROM work_families WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": family_id},
                )
                return False
            except sa.exc.OperationalError:
                return True
            finally:
                probe.rollback()

    def test_the_current_the_pending_and_the_requested_families_are_all_held(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _two_families(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        _with_variant(committing_db, scene, context_id, scene.family)
        request_family_change(
            committing_db, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        family_ids = (scene.family.id, scene.family_b.id, scene.family_c.id)
        committing_db.commit()
        runner = self._paused_request(
            committing_factories, committing_db, committing_session_factory, scene,
            target=scene.family_c, name="request",
        )
        try:
            held = {
                "current": self._locked(committing_session_factory, family_ids[0]),
                "pending": self._locked(committing_session_factory, family_ids[1]),
                "requested": self._locked(committing_session_factory, family_ids[2]),
            }
        finally:
            runner.finish()

        runner.assert_clean()
        assert held == {"current": True, "pending": True, "requested": True}

    def _paused(self, factory, work, name):
        from tests.integration.test_work_variants_concurrency import _Scene

        runner = _Scene(factory)

        def _is_context_lock(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM catalog_contexts" in statement

        runner.spawn(name, work, pause_on=_is_context_lock)
        assert runner.wait_paused(name), "операция не дошла до блокировки контекста"
        return runner

    def test_cancelling_holds_the_current_and_the_pending_family_and_no_other(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _two_families(committing_db, committing_factories)
        context_id, user_id = scene.context_ids[0], scene.user.id
        _with_variant(committing_db, scene, context_id, scene.family)
        request_family_change(
            committing_db, context_id=context_id, family_id=scene.family_b.id,
            actor_id=user_id, source="manual",
        )
        a_id, b_id, c_id = scene.family.id, scene.family_b.id, scene.family_c.id
        committing_db.commit()
        runner = self._paused(
            committing_session_factory,
            lambda session: cancel_pending_family(
                session, context_id=context_id, actor_id=user_id
            ),
            "cancel",
        )
        try:
            held = {
                "current": self._locked(committing_session_factory, a_id),
                "pending": self._locked(committing_session_factory, b_id),
                "unrelated": self._locked(committing_session_factory, c_id),
            }
        finally:
            runner.finish()

        runner.assert_clean()
        assert held == {"current": True, "pending": True, "unrelated": False}

    def test_the_rules_hold_the_current_the_pending_and_the_suggested_family_and_no_other(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _two_families(committing_db, committing_factories)
        unrelated = _active_family(
            committing_db, title="Семья D", unit_name="M2", actor_id=scene.user.id
        )
        context_id, user_id = scene.context_ids[0], scene.user.id
        _with_variant(committing_db, scene, context_id, scene.family, "auto_suggestion")
        request_family_change(
            committing_db, context_id=context_id, family_id=scene.family_b.id,
            actor_id=user_id, source="manual",
        )
        suggestion = _publish(committing_db, context_id, family_id=scene.family_c.id)
        suggestion.confidence = Decimal("0.95")
        a_id, b_id, c_id, d_id = (
            scene.family.id, scene.family_b.id, scene.family_c.id, unrelated.id,
        )
        suggestion_id = suggestion.id
        committing_db.commit()
        runner = self._paused(
            committing_session_factory,
            lambda session: apply_publication_rules(
                session, suggestion_id=suggestion_id, threshold=THRESHOLD
            ),
            "rules",
        )
        try:
            held = {
                "current": self._locked(committing_session_factory, a_id),
                "pending": self._locked(committing_session_factory, b_id),
                "suggested": self._locked(committing_session_factory, c_id),
                "unrelated": self._locked(committing_session_factory, d_id),
            }
        finally:
            runner.finish()

        runner.assert_clean()
        assert held == {"current": True, "pending": True, "suggested": True, "unrelated": False}


class TestOpposingPendingFamilies:
    def test_a_to_b_and_b_to_a_requests_in_parallel_finish_without_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Встречные запросы берут семьи `FOR SHARE` (совместимо) и не ждут друг
        друга; защиту порядка замков у обработчиков значений проверяет тест ниже."""
        from tests.integration.test_work_variants_concurrency import _Scene

        scene = _two_families(committing_db, committing_factories, titles=("Пол А", "Пол Б"))
        first, second = scene.context_ids
        _with_variant(committing_db, scene, first, scene.family)
        _with_variant(committing_db, scene, second, scene.family_b)
        a_id, b_id, user_id = scene.family.id, scene.family_b.id, scene.user.id
        committing_db.commit()
        runners = _Scene(committing_session_factory)

        def _is_context_lock(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM catalog_contexts" in statement

        def request(context_id, target_id):
            def work(db):
                return request_family_change(
                    db, context_id=context_id, family_id=target_id, actor_id=user_id,
                    source="manual",
                )

            return work

        try:
            runners.spawn("a_to_b", request(first, b_id), pause_on=_is_context_lock)
            runners.spawn("b_to_a", request(second, a_id), pause_on=_is_context_lock)
            reached = runners.wait_paused("a_to_b") and runners.wait_paused("b_to_a")
        finally:
            runners.finish()

        runners.assert_clean()
        assert reached, "запросы не дошли до блокировки контекстов"
        assert runners.results["a_to_b"].kind == runners.results["b_to_a"].kind == "pending"
        with committing_session_factory() as probe:
            assert probe.get(CatalogContext, first).pending_family_id == b_id
            assert probe.get(CatalogContext, second).pending_family_id == a_id

    def test_the_value_handlers_of_a_to_b_and_b_to_a_finish_without_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Каждый обработчик держит прежний вариант своего контекста, а целевым
        вариантом берёт прежний вариант чужого: без замка обеих семей сразу
        они ждали бы друг друга. Пауза — после замка прежнего варианта: без
        защиты оба доходят до неё, потом встречно ждут целевой вариант."""
        from tests.integration.test_work_variants_concurrency import _Scene

        scene = _two_families(committing_db, committing_factories, titles=("Пол А", "Пол Б"))
        first, second = scene.context_ids
        schema_a = _current_schema(committing_db, scene.family)
        schema_b = _current_schema(committing_db, scene.family_b)
        value_a = _value(committing_db, _param(committing_db, schema_a), "alpha")
        value_b = _value(committing_db, _param(committing_db, schema_b), "beta")
        variant_a = _variant(
            committing_db, scene.family, schema_a, values_key=f"1={value_a.id}"
        )
        variant_b = _variant(
            committing_db, scene.family_b, schema_b, values_key=f"1={value_b.id}"
        )
        _bind_source(committing_db, scene, first, scene.family)
        _attach_variant(committing_db, first, variant_a)
        _bind_source(committing_db, scene, second, scene.family_b)
        _attach_variant(committing_db, second, variant_b)
        for context_id, target in ((first, scene.family_b), (second, scene.family)):
            request_family_change(
                committing_db, context_id=context_id, family_id=target.id,
                actor_id=scene.user.id, source="manual",
            )
        committing_db.commit()
        runners = _Scene(committing_session_factory)

        def _is_variant_lock(statement: str) -> bool:
            return "FOR UPDATE" in statement and "FROM work_variants" in statement

        def handle(context_id, schema_id, text):
            def work(db):
                return apply_values(
                    db, context_id=context_id, schema_id=schema_id,
                    answer=_answer((1, "value", text, "name")), paths_hash="paths-1",
                    guard=None, settings=_settings(),
                )

            return work

        try:
            runners.spawn("a_to_b", handle(first, schema_b.id, "beta"), pause_on=_is_variant_lock)
            runners.spawn("b_to_a", handle(second, schema_a.id, "alpha"), pause_on=_is_variant_lock)
            assert runners.pending["a_to_b"].wait(timeout=5) or runners.pending[
                "b_to_a"
            ].wait(timeout=5), "ни один обработчик не дошёл до замка варианта"
            # Второму даётся время дойти до того же замка: с защитой он стоит на
            # замке семьи и не дойдёт, без неё дойдёт, и оба встанут встречно.
            runners.pending["a_to_b"].wait(timeout=2)
            runners.pending["b_to_a"].wait(timeout=2)
        finally:
            runners.finish()

        runners.assert_clean()
        assert runners.results["a_to_b"].applied and runners.results["b_to_a"].applied
        with committing_session_factory() as probe:
            moved = probe.get(CatalogContext, first)
            assert (moved.work_family_id, moved.work_variant_id) == (
                scene.family_b.id, variant_b.id,
            )
            moved = probe.get(CatalogContext, second)
            assert (moved.work_family_id, moved.work_variant_id) == (
                scene.family.id, variant_a.id,
            )


# ---------------------------------------------------------------------------
#  Ревью задачи 8: входы заявлений, которые не были предъявлены
# ---------------------------------------------------------------------------

class TestRequestFamilyChangeReviewInputs:
    def test_an_archived_context_with_a_variant_gets_no_pending_family(
        self, db_session, factories
    ):
        """Путь 2 (ожидание) не проходит через `assign_family`: отказ архивному
        контексту — собственная проверка `request_family_change`."""
        from services.work_families import REFUSE_CONTEXT_ARCHIVED

        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        context = _ctx(db_session, context_id)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        with pytest.raises(WorkFamilyError) as caught:
            request_family_change(
                db_session, context_id=context_id, family_id=scene.family_b.id,
                actor_id=scene.user.id, source="manual",
            )

        assert caught.value.code == REFUSE_CONTEXT_ARCHIVED
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)
        assert _pending_events(db_session, context_id) == []

    def test_a_missing_target_family_is_refused_by_its_own_code(self, db_session, factories):
        from services.work_families import REFUSE_FAMILY_NOT_FOUND

        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        with pytest.raises(WorkFamilyError) as caught:
            request_family_change(
                db_session, context_id=context_id, family_id=987654,
                actor_id=scene.user.id, source="manual",
            )

        assert caught.value.code == REFUSE_FAMILY_NOT_FOUND
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)

    def test_the_rule_confirming_the_current_family_keeps_a_human_pending(
        self, db_session, factories
    ):
        """Автоисточник с ТЕКУЩЕЙ семьёй («та же семья») не вытесняет ожидание
        человека — правило его не снимает ни по какой ветви."""
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family, "auto_suggestion")
        request_family_change(
            db_session, context_id=context_id, family_id=scene.family_b.id,
            actor_id=scene.user.id, source="manual",
        )
        before = _pending_columns(_ctx(db_session, context_id))
        newer = _publish(db_session, context_id, family_id=scene.family.id)

        outcome = request_family_change(
            db_session, context_id=context_id, family_id=scene.family.id, actor_id=None,
            source="auto_suggestion", suggestion_id=newer.id, threshold=THRESHOLD,
        )

        assert outcome == FamilyChangeOutcome("unchanged", context_id, scene.family.id, None)
        assert _pending_columns(_ctx(db_session, context_id)) == before
        assert [name for name, _ in _pending_events(db_session, context_id)] == ["set"]

    @pytest.mark.parametrize(
        "case", ["manual_with_suggestion", "suggestion_without_family", "unknown_source"]
    )
    def test_more_incoherent_arguments_are_refused_before_any_lock(
        self, db_session, factories, case
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        user = scene.user.id
        calls = {
            "manual_with_suggestion": dict(family_id=scene.family_b.id, actor_id=user,
                                           source="manual", suggestion_id=1),
            "suggestion_without_family": dict(family_id=None, actor_id=user, source="suggestion",
                                              suggestion_id=1),
            # С `suggestion_id`: иначе отказ дала бы проверка «источник
            # предложения требует suggestion_id», а не проверка источника.
            "unknown_source": dict(family_id=scene.family_b.id, actor_id=user, source="robot",
                                   suggestion_id=1),
        }
        statements: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        connection = db_session.connection()
        sa.event.listen(connection, "before_cursor_execute", _listener)
        try:
            with pytest.raises(ValueError):
                request_family_change(db_session, context_id=context_id, **calls[case])
        finally:
            sa.event.remove(connection, "before_cursor_execute", _listener)

        assert not any("FOR " in s for s in statements)


class TestTerminalOutcomesDoNotResurrect:
    @pytest.mark.parametrize(
        "terminal", ["auto_accepted", "accepted", "rejected", "auto_superseded"]
    )
    def test_reconciliation_neither_republishes_nor_requeues_a_decided_suggestion(
        self, db_session, factories, terminal
    ):
        """Тот же отпечаток не воскресает: сверка не возвращает решённому
        предложению публикацию и не ставит по нему нового задания."""
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        suggestion.decision = terminal
        suggestion.decided_by = None if terminal.startswith("auto") else scene.user.id
        suggestion.decided_at = dt.datetime.now(dt.UTC)
        suggestion.is_published = False
        suggestion.unpublished_reason = "stale_fingerprint"
        db_session.flush()

        reconcile_semantic_jobs(db_session, [context_id], cap=NO_CAP, source="operation")

        row = _sug(db_session, suggestion.id)
        assert (row.decision, row.is_published) == (terminal, False)
        jobs = db_session.execute(
            sa.select(SemanticJob).where(
                SemanticJob.context_id == context_id, SemanticJob.kind == "family_suggestion"
            )
        ).scalars().all()
        assert [(j.id, j.status) for j in jobs] == [(suggestion.job_id, "done")]


class TestAnUnpublishedAnswerIsNotOfferedToTheRules:
    def test_a_parsed_answer_left_unpublished_by_the_verdict_is_not_offered(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """Ответ разобран, предложение записано, но вердикт его не публикует
        (отпечаток устарел за время вызова модели) — правилам оно не
        предлагается."""
        scene = _committed_scene(committing_db, committing_factories)
        calls: list[int] = []
        monkeypatch.setattr(
            worker_module, "apply_publication_rules",
            lambda db, **kwargs: calls.append(kwargs["suggestion_id"]),
        )

        def _stale_meanwhile():
            with committing_session_factory() as other:
                _active_family(other, title="Семья во время вызова", unit_name="M2",
                               actor_id=scene.user.id)
                other.commit()

        client = _FakeClient(
            _response(_model_answer(scene.family.id, "0.9")), probe=_stale_meanwhile
        )

        process_one(committing_session_factory, client, settings=_SETTINGS_ON, clock=_clock)

        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert (suggestion.is_published, suggestion.unpublished_reason) == (
            False, "stale_fingerprint",
        )
        assert calls == []

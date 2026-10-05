"""Массовое автопринятие: показ, применение, CLI и постановка схем (спека
`2026-10-02-catalog-variants-design.md` §2.5, §2.12).

Однопоточные входы идут на транзакционной сессии; гонки с настоящими commit-ами
и команды CLI - на сессиях, которые коммитят. Сцена развёртывания держит пять
контекстов по одному на строку таблицы публикации: каждая ось хэша показа и каждое
из четырёх действий между показом и применением - свой вход на свой контекст.

Помощники сцены (две-три семьи, предложение на текущий отпечаток, привязка с
происхождением) приходят из набора смены семьи.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
import services.family_change as family_change_module
import services.semantic_worker as worker_module
from config import settings as app_settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    FamilyParameterSchema,
    FamilySource,
    FamilySuggestion,
    SemanticJob,
    SemanticReconcileBatch,
)
from services.family_change import (
    AutoAcceptError,
    AutoAcceptPreview,
    apply_auto_accept,
    apply_publication_rules,
    preview_auto_accept,
    request_family_change,
    rule_outcome,
)
from services.semantic_decisions import backfill_family_schemas
from services.semantic_worker import process_one
from services.work_families import assign_family, merge_families
from tests.integration.test_semantic_queue_decisions import (
    _active_family,
    _lock_statements,
    _rendered,
    _simple_context,
)
from tests.integration.test_semantic_queue_worker import (
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
from tests.integration.test_work_variants_concurrency import _Scene as _Runner
from tests.integration.test_work_variants_core import _answer, _attach_variant, _events
from tests.integration.test_work_variants_family_change import (
    THRESHOLD,
    _bind_source,
    _ctx,
    _current_schema,
    _decision,
    _publish,
    _rules_scene,
    _two_families,
    _unpublish,
    _values_jobs,
    _with_variant,
)
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_reconcile import _active_family as _schemaless_family
from tests.integration.test_work_variants_schema import _param, _schema, _value, _variant

pytestmark = pytest.mark.integration

TITLES = ("Пол 1", "Пол 2", "Пол 3", "Пол 4", "Пол 5")


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)


# ---------------------------------------------------------------------------
#  Сцена
# ---------------------------------------------------------------------------

def _deploy_scene(db, factories):
    """Пять контекстов, у каждого опубликованное предложение семьи B с уверенностью
    0.9 при пороге 0.80; исходы: назначить (нет семьи), назначить (автосемья без
    варианта), ожидание (автосемья с вариантом), подтвердить (уже B), ничего
    (семья человека)."""
    scene = _two_families(db, factories, titles=TITLES)
    scene.fx = factories
    (scene.c_assign, scene.c_pending, scene.c_confirm, scene.c_none, scene.c_auto) = (
        scene.context_ids
    )
    scene.suggestions = {
        cid: _publish(db, cid, family_id=scene.family_b.id) for cid in scene.context_ids
    }
    scene.variant = _with_variant(db, scene, scene.c_pending, scene.family, "auto_suggestion")
    _bind_source(db, scene, scene.c_confirm, scene.family_b, "manual")
    _bind_source(db, scene, scene.c_none, scene.family, "manual")
    _bind_source(db, scene, scene.c_auto, scene.family, "auto_suggestion")
    return scene


def _hash(db) -> str:
    return preview_auto_accept(db).preview_hash


def _state(db, scene):
    """Что обязано остаться нетронутым при отказе: колонки контекстов и решения
    предложений."""
    db.flush()
    db.expire_all()
    contexts = [
        (
            c.id, c.work_family_id, c.family_source, c.work_variant_id, c.pending_family_id,
            c.pending_family_source,
        )
        for c in db.execute(sa.select(CatalogContext).order_by(CatalogContext.id)).scalars()
    ]
    decisions = [
        (s.id, s.decision)
        for s in db.execute(sa.select(FamilySuggestion).order_by(FamilySuggestion.id)).scalars()
    ]
    return contexts, decisions


def _spare_suggestion(db, scene):
    """Решённое предложение для колонки `pending_suggestion_id` ожидания с
    источником `suggestion` (опубликованное тут не годится: опубликованное
    предложение контекста одно)."""
    spare = _two_family_context(db, scene)
    return _publish(
        db, spare, family_id=scene.family_c.id, decision="accepted_pending",
        decided_by=scene.user.id,
    )


def _two_family_context(db, scene):
    return _simple_context(
        db, scene.fx, scene.proposal, unit_id=scene.unit_id, title="Запасной контекст"
    )


# ---------------------------------------------------------------------------
#  rule_outcome: единственное место, где считается исход
# ---------------------------------------------------------------------------

def _row(**kw):
    base = dict(
        work_family_id=None, family_source=None, work_variant_id=None,
        pending_family_id=None, pending_family_source=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestRuleOutcome:
    @pytest.mark.parametrize(
        ("context", "confidence", "expected"),
        [
            (_row(), "0.80", "assign"),
            (_row(), "0.79", "none"),
            (_row(work_family_id=7), "0.10", "confirm"),
            (_row(work_family_id=3, family_source="auto_suggestion"), "0.80", "assign"),
            (
                _row(work_family_id=3, family_source="auto_suggestion", work_variant_id=1),
                "0.80", "pending",
            ),
            (_row(work_family_id=3, family_source="auto_suggestion"), "0.79", "none"),
            (_row(work_family_id=3, family_source="manual"), "0.99", "none"),
            (_row(work_family_id=3, family_source="suggestion"), "0.99", "none"),
            (
                _row(
                    work_family_id=3, family_source="auto_suggestion", work_variant_id=1,
                    pending_family_id=9, pending_family_source="manual",
                ),
                "0.99", "none",
            ),
            (
                _row(
                    work_family_id=3, family_source="auto_suggestion", work_variant_id=1,
                    pending_family_id=9, pending_family_source="auto_suggestion",
                ),
                "0.99", "pending",
            ),
        ],
    )
    def test_each_row_of_the_publication_table(self, context, confidence, expected):
        assert rule_outcome(
            family_id=7, confidence=Decimal(confidence), context=context,
            threshold=Decimal("0.80"), family_fits=True,
        ) == expected

    def test_a_family_that_does_not_fit_the_context_is_not_applied(self):
        kwargs = dict(
            family_id=7, confidence=Decimal("0.99"), threshold=Decimal("0.80"),
        )
        assert rule_outcome(context=_row(), family_fits=False, **kwargs) == "none"
        assert rule_outcome(context=_row(), family_fits=True, **kwargs) == "assign"


# ---------------------------------------------------------------------------
#  Показ
# ---------------------------------------------------------------------------

class TestPreview:
    def test_counts_candidates_by_outcome_of_the_publication_table(self, db_session, factories):
        _deploy_scene(db_session, factories)

        preview = preview_auto_accept(db_session)

        assert isinstance(preview, AutoAcceptPreview)
        assert dict(preview.by_outcome) == {"confirm": 1, "assign": 2, "pending": 1, "none": 1}
        assert (preview.total, preview.threshold) == (5, THRESHOLD)
        assert len(preview.preview_hash) == 64

    def test_preview_writes_and_decides_nothing(self, db_session, factories):
        scene = _deploy_scene(db_session, factories)
        before = _state(db_session, scene)

        preview_auto_accept(db_session)

        assert _state(db_session, scene) == before

    def test_the_hash_is_stable_for_an_unchanged_state(self, db_session, factories):
        _deploy_scene(db_session, factories)

        assert _hash(db_session) == _hash(db_session)

    def test_no_candidates_gives_an_empty_preview(self, db_session, factories):
        preview = preview_auto_accept(db_session)

        assert dict(preview.by_outcome) == {"confirm": 0, "assign": 0, "pending": 0, "none": 0}
        assert preview.total == 0

    def test_the_preview_refuses_without_a_threshold(self, db_session, factories, monkeypatch):
        _deploy_scene(db_session, factories)
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)

        with pytest.raises(AutoAcceptError) as excinfo:
            preview_auto_accept(db_session)

        assert excinfo.value.code == "threshold_missing"


# ---------------------------------------------------------------------------
#  Кто попадает в кандидаты
# ---------------------------------------------------------------------------

def _candidate_scene(db, factories):
    scene = _two_families(db, factories, titles=("Пол 1", "Пол 2"))
    scene.control, scene.tested = scene.context_ids
    scene.other_unit = _active_family(
        db, title="Семья штук", unit_name="PCS", actor_id=scene.user.id
    )
    for cid in scene.context_ids:
        _publish(db, cid, family_id=scene.family_b.id)
    return scene


def _tested_suggestion(db, scene):
    return db.execute(
        sa.select(FamilySuggestion).where(FamilySuggestion.context_id == scene.tested)
    ).scalar_one()


def _break_decided(db, scene):
    suggestion = _tested_suggestion(db, scene)
    suggestion.decision = "rejected"
    suggestion.decided_by = scene.user.id
    suggestion.decided_at = dt.datetime.now(dt.UTC)


def _break_unpublished(db, scene):
    _unpublish(db, _tested_suggestion(db, scene))


def _break_stale(db, scene):
    _tested_suggestion(db, scene).request_hash = "older-fingerprint"


def _break_archived(db, scene):
    db.get(CatalogContext, scene.tested).archived_at = dt.datetime.now(dt.UTC)


def _break_not_applicable(db, scene):
    db.execute(
        sa.update(CatalogContext).where(CatalogContext.id == scene.tested)
        .values(semantic_state="NOT_APPLICABLE")
    )
    db.expire_all()


def _break_kind(kind):
    def _do(db, scene):
        catalog_id = db.execute(
            sa.select(ContextBucket.catalog_position_id)
            .join(CatalogContext, CatalogContext.bucket_id == ContextBucket.id)
            .where(CatalogContext.id == scene.tested)
        ).scalar_one()
        db.get(CatalogPosition, catalog_id).kind = kind

    return _do


def _break_no_family(db, scene):
    _unpublish(db, _tested_suggestion(db, scene))
    _publish(db, scene.tested, family_id=None)


class TestCandidates:
    @pytest.mark.parametrize(
        ("name", "breaker", "total"),
        [
            ("untouched", lambda db, scene: None, 2),
            ("decided", _break_decided, 1),
            ("unpublished", _break_unpublished, 1),
            ("stale_fingerprint", _break_stale, 1),
            ("archived_context", _break_archived, 1),
            ("not_applicable", _break_not_applicable, 1),
            ("header_row", _break_kind("HEADER"), 1),
            ("trash_row", _break_kind("TRASH"), 1),
            ("answer_without_own_family", _break_no_family, 1),
        ],
    )
    def test_only_a_current_published_undecided_suggestion_of_an_applicable_context_counts(
        self, db_session, factories, name, breaker, total
    ):
        scene = _candidate_scene(db_session, factories)
        breaker(db_session, scene)
        db_session.flush()

        assert preview_auto_accept(db_session).total == total, name

    def test_a_suggestion_missed_between_the_recording_and_the_rules_is_picked_up(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        scene = _committed_scene(committing_db, committing_factories)
        monkeypatch.setattr(worker_module, "apply_publication_rules", lambda *a, **k: None)
        process_one(
            committing_session_factory,
            _FakeClient(_response(_model_answer(scene.family.id, "0.9"))),
            settings=S, clock=_clock,
        )
        committing_db.expire_all()
        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert (suggestion.is_published, suggestion.decision) == (True, None)
        assert committing_db.get(CatalogContext, scene.context_ids[0]).work_family_id is None

        preview = preview_auto_accept(committing_db)
        applied = apply_auto_accept(committing_db, preview_hash=preview.preview_hash)
        committing_db.commit()

        assert dict(applied) == {"confirm": 0, "assign": 1, "pending": 0}
        committing_db.expire_all()
        context = committing_db.get(CatalogContext, scene.context_ids[0])
        assert (context.work_family_id, context.family_source) == (
            scene.family.id, "auto_suggestion",
        )
        assert committing_db.get(FamilySuggestion, suggestion.id).decision == "auto_accepted"

    def test_a_family_of_another_unit_is_counted_but_never_applied(self, db_session, factories):
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2"))
        first, second = scene.context_ids
        other = _active_family(db_session, title="Штучная", unit_name="PCS", actor_id=scene.user.id)
        _publish(db_session, first, family_id=scene.family_b.id)
        _publish(db_session, second, family_id=other.id)

        preview = preview_auto_accept(db_session)
        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert dict(preview.by_outcome) == {"confirm": 0, "assign": 1, "pending": 0, "none": 1}
        assert dict(applied) == {"confirm": 0, "assign": 1, "pending": 0}
        assert _ctx(db_session, second).work_family_id is None
        assert _ctx(db_session, first).work_family_id == scene.family_b.id

    def test_the_single_rule_leaves_a_family_of_another_unit_to_the_human(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        other = _active_family(db_session, title="Штучная", unit_name="PCS", actor_id=scene.user.id)
        suggestion = _publish(db_session, scene.context_ids[0], family_id=other.id)

        outcome = apply_publication_rules(
            db_session, suggestion_id=suggestion.id, threshold=THRESHOLD
        )

        assert outcome is None
        assert _ctx(db_session, scene.context_ids[0]).work_family_id is None
        assert _decision(db_session, suggestion.id) == (None, None)


# ---------------------------------------------------------------------------
#  Показ и правило публикации считают один исход
# ---------------------------------------------------------------------------

def _bound(source, *, variant=False, pending=False):
    def _do(db, scene):
        if variant:
            _with_variant(db, scene, scene.context_id, scene.family, source)
        else:
            _bind_source(db, scene, scene.context_id, scene.family, source)
        if pending:
            context = db.get(CatalogContext, scene.context_id)
            context.pending_family_id = scene.family_c.id
            context.pending_family_source = "manual"
            context.pending_by = scene.user.id
            context.pending_at = dt.datetime.now(dt.UTC)
            db.flush()

    return _do


_PARITY = [
    ("row1_no_family", "0.9", lambda db, scene: None, "assign", "assigned"),
    ("row2_below", "0.5", lambda db, scene: None, "none", None),
    ("row3_same_family", "0.1", "same", "confirm", "unchanged"),
    ("row4_auto_plain", "0.9", _bound("auto_suggestion"), "assign", "assigned"),
    ("row5_auto_variant", "0.9", _bound("auto_suggestion", variant=True), "pending", "pending"),
    ("row6_auto_below", "0.5", _bound("auto_suggestion"), "none", None),
    ("row7_manual", "0.99", _bound("manual"), "none", None),
    ("row7_suggestion", "0.99", _bound("suggestion"), "none", None),
    (
        "human_pending",
        "0.99",
        _bound("auto_suggestion", variant=True, pending=True),
        "none", None,
    ),
]


class TestPreviewAgreesWithThePublicationRule:
    @pytest.mark.parametrize(
        ("name", "confidence", "bind", "outcome", "route"),
        _PARITY,
        ids=[case[0] for case in _PARITY],
    )
    def test_the_previewed_outcome_is_what_the_rule_does(
        self, db_session, factories, name, confidence, bind, outcome, route
    ):
        scene = _rules_scene(
            db_session, factories, confidence=confidence,
            suggested="a" if bind == "same" else "b",
        )
        if bind == "same":
            _bind_source(db_session, scene, scene.context_id, scene.family, "manual")
        else:
            bind(db_session, scene)
        db_session.flush()

        previewed = {k: v for k, v in preview_auto_accept(db_session).by_outcome.items() if v}
        result = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, threshold=THRESHOLD
        )

        assert previewed == {outcome: 1}
        assert (None if result is None else result.kind) == route


# ---------------------------------------------------------------------------
#  Каждая ось кортежа хэша и порог
# ---------------------------------------------------------------------------

def _set_pending(db, scene, context_id, family, *, source="manual"):
    context = db.get(CatalogContext, context_id)
    context.pending_family_id = family.id
    context.pending_family_source = source
    context.pending_at = dt.datetime.now(dt.UTC)
    if source == "manual":
        context.pending_by = scene.user.id
        context.pending_suggestion_id = None
    else:
        context.pending_by = None
        context.pending_suggestion_id = scene.spare.id
    db.flush()


def _new_family(db, scene, title):
    return _active_family(db, title=title, unit_name="M2", actor_id=scene.user.id)


def _prep_pending(db, scene):
    _set_pending(db, scene, scene.c_none, scene.family_c)


def _prep_pending_source(db, scene):
    scene.spare = _spare_suggestion(db, scene)
    _set_pending(db, scene, scene.c_none, scene.family_c)


def _mut_suggestion_id(db, scene):
    """Новое предложение той же семьи на тот же отпечаток - у контекста с
    наибольшим `id` предложения, чтобы порядок строк в хэше не менялся."""
    _unpublish(db, scene.suggestions[scene.c_auto])
    _publish(db, scene.c_auto, family_id=scene.family_b.id)


def _mut_request_hash(db, scene):
    _new_family(db, scene, "Семья D")
    db.flush()
    for cid, suggestion in scene.suggestions.items():
        db.expire_all()
        db.get(FamilySuggestion, suggestion.id).request_hash = _rendered(db, cid).request_hash
        db.flush()


def _mut_suggested_family(db, scene):
    db.get(FamilySuggestion, scene.suggestions[scene.c_assign].id).family_id = scene.family_c.id


def _mut_outcome(db, scene):
    db.get(FamilySuggestion, scene.suggestions[scene.c_assign].id).confidence = Decimal("0.5")


def _mut_work_family(db, scene):
    _bind_source(db, scene, scene.c_none, scene.family_c, "manual")


def _mut_family_source(db, scene):
    _bind_source(db, scene, scene.c_none, scene.family, "suggestion")


def _mut_variant(db, scene):
    other = _variant(db, scene.family, _current_schema(db, scene.family))
    _attach_variant(db, scene.c_pending, other)


def _mut_pending_family(db, scene):
    _set_pending(db, scene, scene.c_none, scene.family_b)


def _mut_pending_source(db, scene):
    _set_pending(db, scene, scene.c_none, scene.family_c, source="suggestion")


def _mut_threshold(db, scene):
    app_settings.SEMANTIC_AUTO_ACCEPT_THRESHOLD = Decimal("0.85")


def _no_prep(db, scene):
    return None


#: (ось, подготовка, правка, меняется ли исход). Подготовка выполняется до
#: первого хэша: оси ожидания нужно ожидание, которое правка потом меняет.
AXES = [
    ("suggestion_id", _no_prep, _mut_suggestion_id, False),
    ("request_hash", _no_prep, _mut_request_hash, False),
    ("suggested_family", _no_prep, _mut_suggested_family, False),
    ("outcome", _no_prep, _mut_outcome, True),
    ("work_family_id", _no_prep, _mut_work_family, False),
    ("family_source", _no_prep, _mut_family_source, False),
    ("work_variant_id", _no_prep, _mut_variant, False),
    ("pending_family_id", _prep_pending, _mut_pending_family, False),
    ("pending_family_source", _prep_pending_source, _mut_pending_source, False),
    ("threshold", _no_prep, _mut_threshold, False),
]


class TestPreviewHashAxes:
    @pytest.mark.parametrize(
        ("axis", "prepare", "mutate", "outcome_changes"),
        AXES,
        ids=[axis[0] for axis in AXES],
    )
    def test_each_axis_of_the_tuple_and_the_threshold_changes_the_hash(
        self, db_session, factories, monkeypatch, axis, prepare, mutate, outcome_changes
    ):
        scene = _deploy_scene(db_session, factories)
        prepare(db_session, scene)
        before = preview_auto_accept(db_session)
        monkeypatch.setattr(
            app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD
        )  # возврат порога после правки порога

        mutate(db_session, scene)
        db_session.flush()
        after = preview_auto_accept(db_session)

        assert after.preview_hash != before.preview_hash, axis
        assert (dict(after.by_outcome) != dict(before.by_outcome)) is outcome_changes, axis
        assert after.total == before.total, axis

    @pytest.mark.parametrize(
        "neutral",
        ["confidence_same_outcome", "reason_text", "unrelated_context", "decided_suggestion"],
    )
    def test_an_input_that_touches_no_axis_leaves_the_hash(
        self, db_session, factories, neutral
    ):
        scene = _deploy_scene(db_session, factories)
        before = _hash(db_session)

        if neutral == "confidence_same_outcome":
            db_session.get(FamilySuggestion, scene.suggestions[scene.c_assign].id).confidence = (
                Decimal("0.95")
            )
        elif neutral == "reason_text":
            db_session.get(FamilySuggestion, scene.suggestions[scene.c_assign].id).reason = "иное"
        elif neutral == "unrelated_context":
            _simple_context(
                db_session, factories, scene.proposal, unit_id=scene.unit_id, title="Без предложения"
            )
        else:
            spare = _two_family_context(db_session, scene)
            _publish(
                db_session, spare, family_id=scene.family_b.id, decision="rejected",
                decided_by=scene.user.id,
            )
        db_session.flush()

        assert _hash(db_session) == before


# ---------------------------------------------------------------------------
#  Применение
# ---------------------------------------------------------------------------

class TestApply:
    def test_applies_every_row_of_the_table_and_reports_counts_by_outcome(
        self, db_session, factories
    ):
        scene = _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)

        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert dict(applied) == {"confirm": 1, "assign": 2, "pending": 1}
        assigned = _ctx(db_session, scene.c_assign)
        assert (assigned.work_family_id, assigned.family_source, assigned.family_by) == (
            scene.family_b.id, "auto_suggestion", None,
        )
        replaced = _ctx(db_session, scene.c_auto)
        assert (replaced.work_family_id, replaced.family_source) == (
            scene.family_b.id, "auto_suggestion",
        )
        waiting = _ctx(db_session, scene.c_pending)
        assert (waiting.work_family_id, waiting.work_variant_id) == (
            scene.family.id, scene.variant.id,
        )
        assert (
            waiting.pending_family_id, waiting.pending_family_source,
            waiting.pending_suggestion_id, waiting.pending_threshold,
        ) == (
            scene.family_b.id, "auto_suggestion", scene.suggestions[scene.c_pending].id,
            Decimal("0.80"),
        )
        confirmed = _ctx(db_session, scene.c_confirm)
        assert (confirmed.work_family_id, confirmed.family_source) == (scene.family_b.id, "manual")
        untouched = _ctx(db_session, scene.c_none)
        assert (untouched.work_family_id, untouched.family_source) == (scene.family.id, "manual")
        decisions = {
            name: _decision(db_session, scene.suggestions[cid].id)
            for name, cid in (
                ("assign", scene.c_assign), ("auto", scene.c_auto), ("pending", scene.c_pending),
                ("confirm", scene.c_confirm), ("none", scene.c_none),
            )
        }
        assert decisions == {
            "assign": ("auto_accepted", None), "auto": ("auto_accepted", None),
            "pending": ("auto_pending", None), "confirm": ("auto_accepted", None),
            "none": (None, None),
        }

    def test_the_applied_rows_carry_threshold_and_confidence_in_their_events(
        self, db_session, factories
    ):
        scene = _deploy_scene(db_session, factories)

        apply_auto_accept(db_session, preview_hash=_hash(db_session))

        [event] = _events(db_session, "context_family_assigned", context_id=scene.c_assign)
        assert event.actor_id is None
        assert event.payload["threshold"] == "0.80"
        assert event.payload["confidence"] == "0.9"
        assert event.payload["source"] == "auto_suggestion"

    def test_the_values_jobs_are_set_by_the_end_of_the_transaction(self, db_session, factories):
        scene = _deploy_scene(db_session, factories)

        apply_auto_accept(db_session, preview_hash=_hash(db_session))

        live = [
            job for job in _values_jobs(db_session, context_id=scene.c_assign)
            if job.status == "pending"
        ]
        assert [job.schema_id for job in live] == [_current_schema(db_session, scene.family_b).id]
        waiting = [
            job for job in _values_jobs(db_session, context_id=scene.c_pending)
            if job.status == "pending"
        ]
        assert [job.schema_id for job in waiting] == [_current_schema(db_session, scene.family_b).id]

    def test_a_repeat_with_the_old_hash_is_refused_because_the_state_changed(
        self, db_session, factories
    ):
        _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)
        apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert excinfo.value.code == "preview_changed"

    def test_nothing_to_apply_applies_nothing_with_the_hash_of_an_empty_preview(
        self, db_session, factories
    ):
        preview = preview_auto_accept(db_session)

        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert dict(applied) == {"confirm": 0, "assign": 0, "pending": 0}

    def test_the_apply_refuses_without_a_threshold_and_changes_nothing(
        self, db_session, factories, monkeypatch
    ):
        scene = _deploy_scene(db_session, factories)
        hash_ = _hash(db_session)
        before = _state(db_session, scene)
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=hash_)

        assert excinfo.value.code == "threshold_missing"
        assert _state(db_session, scene) == before

    def test_a_wrong_hash_is_refused_and_changes_nothing(self, db_session, factories):
        scene = _deploy_scene(db_session, factories)
        before = _state(db_session, scene)

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash="0" * 64)

        assert excinfo.value.code == "preview_changed"
        assert _state(db_session, scene) == before


def _act_manual_binding(db, scene):
    assign_family(
        db, context_id=scene.c_assign, family_id=scene.family_c.id, actor_id=scene.user.id,
        source=FamilySource.manual,
    )


def _act_variant(db, scene):
    _attach_variant(
        db, scene.c_auto, _variant(db, scene.family, _current_schema(db, scene.family))
    )


def _act_pending(db, scene):
    request_family_change(
        db, context_id=scene.c_pending, family_id=scene.family_c.id, actor_id=scene.user.id,
        source="manual",
    )


def _act_merge(db, scene):
    merge_families(
        db, source_family_id=scene.family_b.id, target_family_id=scene.family_c.id,
        actor_id=scene.user.id,
    )


class TestActionsBetweenPreviewAndApply:
    @pytest.mark.parametrize(
        "action",
        [_act_manual_binding, _act_variant, _act_pending, _act_merge],
        ids=["manual_binding", "variant_issued", "pending_set", "suggested_family_merged"],
    )
    def test_the_hash_of_a_preview_does_not_survive_the_action_and_nothing_is_applied(
        self, db_session, factories, action
    ):
        scene = _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)
        action(db_session, scene)
        db_session.flush()
        before = _state(db_session, scene)
        assert preview_auto_accept(db_session).preview_hash != preview.preview_hash

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert excinfo.value.code == "preview_changed"
        assert _state(db_session, scene) == before



# ---------------------------------------------------------------------------
#  Применение: перепроверка под замками (правки ревью задачи 9)
# ---------------------------------------------------------------------------

def _before_the_locks(monkeypatch, step):
    """`step(db)` выполняется внутри `apply_auto_accept` между чтением
    кандидатов без блокировок и захватом семей и контекстов - окно, в которое
    коммитит параллельная транзакция."""
    real = family_change_module.acquire_family_locks
    done = []

    def _wrapped(db, items, **kwargs):
        if not done:  # только первый захват - групповой
            done.append(True)
            step(db)
            db.flush()
        return real(db, items, **kwargs)

    monkeypatch.setattr(family_change_module, "acquire_family_locks", _wrapped)


class TestApplyRechecksUnderTheLocks:
    def test_a_change_after_the_unlocked_read_is_caught_by_the_locked_reread(
        self, db_session, factories, monkeypatch
    ):
        """Хэш сверяется с кандидатами, перечитанными ПОД блокировками, а не с
        первым чтением: ручная привязка, легшая между ними, не перезаписывается
        автопринятием."""
        scene = _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)
        _before_the_locks(monkeypatch, lambda db: _act_manual_binding(db, scene))

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert excinfo.value.code == "preview_changed"
        bound = _ctx(db_session, scene.c_assign)
        assert (bound.work_family_id, bound.family_source) == (scene.family_c.id, "manual")
        assert _decision(db_session, scene.suggestions[scene.c_assign].id) == (None, None)

    def test_a_candidate_back_after_the_unlocked_read_is_refused_although_the_hash_matches(
        self, db_session, factories, monkeypatch
    ):
        """Кандидат, которого не было при первом чтении (и чей контекст поэтому
        не попал под замок), вернулся к перечитыванию: хэш снова равен показу,
        но набор кандидатов под замками не тот, что захватывался, - отказ."""
        scene = _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)
        db_session.get(CatalogContext, scene.c_assign).archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        before = _state(db_session, scene)

        def _unarchive(db):
            db.get(CatalogContext, scene.c_assign).archived_at = None

        _before_the_locks(monkeypatch, _unarchive)

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert excinfo.value.code == "preview_changed"
        assert preview_auto_accept(db_session).preview_hash == preview.preview_hash
        assert _state(db_session, scene) == before

    def test_family_pairs_unstable_after_the_retry_refuse_and_apply_nothing(
        self, db_session, factories, monkeypatch
    ):
        """Пара семей контекста не устоялась и на повторе захвата: отказ, хотя
        хэш под замками совпал бы (подмена видна только прочитанному до
        замков)."""
        scene = _deploy_scene(db_session, factories)
        preview = preview_auto_accept(db_session)
        before = _state(db_session, scene)
        real_read = family_change_module._read_family_pairs
        calls = []

        def _flapping(db, context_ids):
            pairs = real_read(db, context_ids)
            calls.append(1)
            if len(calls) in (1, 3):  # чтение ДО замков обеих попыток группового захвата
                pairs = {**pairs, scene.c_assign: (scene.family_c.id, None)}
            return pairs

        monkeypatch.setattr(family_change_module, "_read_family_pairs", _flapping)

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert excinfo.value.code == "preview_changed"
        assert len(calls) == 4  # две попытки: прочитанное и перечитанное каждой
        assert _state(db_session, scene) == before

    def test_an_outcome_that_disagrees_with_the_change_path_is_refused(
        self, db_session, factories
    ):
        """Исход правила и путь `request_family_change` обязаны совпасть: исход
        «ожидание» у контекста без варианта (путь - назначение сразу) - отказ,
        а не назначение под решением `auto_pending`."""
        scene = _deploy_scene(db_session, factories)
        suggestion = db_session.get(FamilySuggestion, scene.suggestions[scene.c_assign].id)

        with pytest.raises(RuntimeError, match="pending"):
            family_change_module._apply_outcome(
                db_session, suggestion, _ctx(db_session, scene.c_assign), "pending", THRESHOLD
            )


class TestAFamilyNoLongerActive:
    def _scene(self, db, factories):
        """Предложение семьи C на текущем отпечатке, семья C заархивирована
        (отпечаток пересчитан после архивации - вход «текущий отпечаток +
        неактивная семья»), рядом контроль с семьёй B."""
        scene = _two_families(db, factories, titles=("Пол 1", "Пол 2"))
        scene.control, scene.tested = scene.context_ids
        scene.control_suggestion = _publish(db, scene.control, family_id=scene.family_b.id)
        scene.tested_suggestion = _publish(db, scene.tested, family_id=scene.family_c.id)
        scene.family_c.status = "archived"
        scene.family_c.archived_at = dt.datetime.now(dt.UTC)
        db.flush()
        for cid, suggestion in (
            (scene.control, scene.control_suggestion), (scene.tested, scene.tested_suggestion),
        ):
            db.expire_all()
            db.get(FamilySuggestion, suggestion.id).request_hash = _rendered(db, cid).request_hash
            db.flush()
        return scene

    def test_is_counted_but_never_applied(self, db_session, factories):
        scene = self._scene(db_session, factories)

        preview = preview_auto_accept(db_session)
        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert dict(preview.by_outcome) == {"confirm": 0, "assign": 1, "pending": 0, "none": 1}
        assert dict(applied) == {"confirm": 0, "assign": 1, "pending": 0}
        assert _ctx(db_session, scene.tested).work_family_id is None
        assert _ctx(db_session, scene.control).work_family_id == scene.family_b.id

    def test_the_single_rule_leaves_it_to_the_human(self, db_session, factories):
        scene = self._scene(db_session, factories)

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.tested_suggestion.id, threshold=THRESHOLD
        )

        assert outcome is None
        assert _ctx(db_session, scene.tested).work_family_id is None
        assert _decision(db_session, scene.tested_suggestion.id) == (None, None)


# ---------------------------------------------------------------------------
#  Порядок блокировок и гонка с обработчиком значений
# ---------------------------------------------------------------------------

def _is_context_lock(statement: str) -> bool:
    return "FOR UPDATE" in statement and "FROM catalog_contexts" in statement


def _is_family_lock(statement: str) -> bool:
    return "FOR UPDATE" in statement and "FROM work_families" in statement


class TestApplyLocking:
    def test_families_share_then_contexts_update_then_suggestions_update(
        self, db_session, factories
    ):
        _deploy_scene(db_session, factories)
        hash_ = _hash(db_session)

        seen = _lock_statements(
            db_session,
            lambda: apply_auto_accept(db_session, preview_hash=hash_),
        )

        assert seen[:3] == [
            ("work_families", "SHARE"), ("catalog_contexts", "UPDATE"),
            ("family_suggestions", "UPDATE"),
        ]
        assert [mode for table, mode in seen if table == "work_families"] == ["SHARE"] * len(
            [1 for table, _ in seen if table == "work_families"]
        )

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

    def test_the_current_the_pending_and_the_suggested_family_are_held_and_no_other(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _deploy_scene(committing_db, committing_factories)
        _set_pending(committing_db, scene, scene.c_none, scene.family_c)
        unrelated = _new_family(committing_db, scene, "Семья D")
        a_id, b_id, c_id, d_id = (
            scene.family.id, scene.family_b.id, scene.family_c.id, unrelated.id,
        )
        # Предложения остаются на текущем отпечатке: новая семья D появилась
        # до публикации в смысле хэша - пересчитываем показ после неё.
        for cid, suggestion in scene.suggestions.items():
            committing_db.get(FamilySuggestion, suggestion.id).request_hash = (
                _rendered(committing_db, cid).request_hash
            )
        committing_db.commit()
        hash_ = _hash(committing_db)
        committing_db.rollback()
        runner = _Runner(committing_session_factory)
        runner.spawn(
            "apply",
            lambda session: apply_auto_accept(session, preview_hash=hash_),
            pause_on=_is_context_lock,
        )
        try:
            assert runner.wait_paused("apply"), "применение не дошло до блокировки контекстов"
            held = {
                "current": self._locked(committing_session_factory, a_id),
                "suggested": self._locked(committing_session_factory, b_id),
                "pending": self._locked(committing_session_factory, c_id),
                "unrelated": self._locked(committing_session_factory, d_id),
            }
        finally:
            runner.finish()

        runner.assert_clean()
        assert held == {"current": True, "suggested": True, "pending": True, "unrelated": False}


class TestApplyAgainstTheValuesHandler:
    def test_apply_and_a_handler_holding_the_family_finish_without_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Обработчик значений держит семью `FOR UPDATE` и просит контекст;
        применение просит семью и не должно держать контекст, пока не получило
        семью: иначе встречные ожидания замыкаются в цикл."""
        scene = _two_families(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        schema = _current_schema(committing_db, scene.family)
        value = _value(committing_db, _param(committing_db, schema), "alpha")
        variant = _variant(committing_db, scene.family, schema, values_key=f"1={value.id}")
        _publish(committing_db, context_id, family_id=scene.family_b.id)
        _bind_source(committing_db, scene, context_id, scene.family, "auto_suggestion")
        _attach_variant(committing_db, context_id, variant)
        schema_id = schema.id
        committing_db.commit()
        hash_ = _hash(committing_db)
        committing_db.rollback()
        runner = _Runner(committing_session_factory)

        def handle(session):
            from services.work_variants import apply_values

            return apply_values(
                session, context_id=context_id, schema_id=schema_id,
                answer=_answer((1, "value", "alpha", "name")), paths_hash="paths-1",
                guard=None, settings=_settings(),
            )

        try:
            runner.spawn("handler", handle, pause_on=_is_family_lock)
            assert runner.wait_paused("handler"), "обработчик не взял семью"
            runner.spawn(
                "apply",
                lambda session: apply_auto_accept(session, preview_hash=hash_),
            )
            state = runner.wait_settled("apply", contains="work_families")
            assert state == "blocked", f"применение не встало на семье: {state}"
        finally:
            runner.finish()

        runner.assert_clean()
        assert runner.results["handler"].applied
        assert dict(runner.results["apply"]) == {"confirm": 0, "assign": 0, "pending": 1}
        with committing_session_factory() as probe:
            context = probe.get(CatalogContext, context_id)
            assert context.pending_family_id == scene.family_b.id


# ---------------------------------------------------------------------------
#  CLI: semantic-auto-accept
# ---------------------------------------------------------------------------

class TestApplyReconcilesOnceUnderTheEventCap:
    def test_values_jobs_over_the_cap_become_one_held_batch(
        self, db_session, factories, monkeypatch
    ):
        """Три контекста получают задание значений (два назначения, одно
        ожидание) при потолке 1: одна сверка применения - одна удержанная пачка,
        заданий нет."""
        _deploy_scene(db_session, factories)
        hash_ = _hash(db_session)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        apply_auto_accept(db_session, preview_hash=hash_)

        live = [job for job in _values_jobs(db_session) if job.status == "pending"]
        batches = db_session.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert live == []
        assert [(batch.status, batch.contexts_count) for batch in batches] == [("held", 3)]


@pytest.fixture
def cli_db(committing_session_factory, monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)
    return committing_session_factory


def _committed_deploy_scene(db, factories):
    scene = _deploy_scene(db, factories)
    db.commit()
    return scene


def _read(factory, model, row_id):
    with factory() as probe:
        row = probe.get(model, row_id)
        probe.expunge(row)
        return row


class TestAutoAcceptCommand:
    def test_command_refuses_unlisted_target_before_opening_a_session(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.setenv("DB_EXTRA_TARGETS", "")
        for name in ("PGHOSTADDR", "PGSERVICE", "PGPORT", "PGHOST", "PGDATABASE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            cli.settings, "DATABASE_URL",
            "postgresql+psycopg://test_owner:secret-pw@ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb",
            raising=False,
        )

        def _explode():
            raise AssertionError("SessionLocal() вызван - guard сработал слишком поздно")

        monkeypatch.setattr(cli, "SessionLocal", _explode)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)

    def test_yes_prints_the_preview_and_applies(
        self, cli_db, committing_db, committing_factories
    ):
        scene = _committed_deploy_scene(committing_db, committing_factories)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 0, result.output
        assert "assign: 2" in result.output
        assert "pending: 1" in result.output
        assert "confirm: 1" in result.output
        assert "none: 1" in result.output
        assert "Кандидатов всего: 5" in result.output
        context = _read(cli_db, CatalogContext, scene.c_assign)
        assert (context.work_family_id, context.family_source) == (
            scene.family_b.id, "auto_suggestion",
        )

    def test_a_declined_confirmation_applies_nothing(
        self, cli_db, committing_db, committing_factories
    ):
        scene = _committed_deploy_scene(committing_db, committing_factories)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept"], input="n\n")

        assert result.exit_code != 0
        assert _read(cli_db, CatalogContext, scene.c_assign).work_family_id is None
        assert _read(cli_db, FamilySuggestion, scene.suggestions[scene.c_assign].id).decision is None

    def test_a_confirmed_prompt_applies(self, cli_db, committing_db, committing_factories):
        scene = _committed_deploy_scene(committing_db, committing_factories)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept"], input="y\n")

        assert result.exit_code == 0, result.output
        assert _read(cli_db, CatalogContext, scene.c_assign).work_family_id == scene.family_b.id

    def test_without_a_threshold_the_command_fails_and_changes_nothing(
        self, cli_db, committing_db, committing_factories, monkeypatch
    ):
        scene = _committed_deploy_scene(committing_db, committing_factories)
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 1
        assert "SEMANTIC_AUTO_ACCEPT_THRESHOLD" in result.output
        assert _read(cli_db, CatalogContext, scene.c_assign).work_family_id is None

    def test_nothing_to_apply_is_reported_and_asks_nothing(self, cli_db, committing_db):
        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept"])

        assert result.exit_code == 0, result.output
        assert "Применять нечего" in result.output

    def test_a_state_change_between_preview_and_apply_refuses_and_applies_nothing(
        self, cli_db, committing_db, committing_factories, monkeypatch
    ):
        scene = _committed_deploy_scene(committing_db, committing_factories)
        real = cli.preview_auto_accept

        def _preview_then_change(db):
            preview = real(db)
            with cli_db() as other:
                assign_family(
                    other, context_id=scene.c_assign, family_id=scene.family_c.id,
                    actor_id=scene.user.id, source=FamilySource.manual,
                )
                other.commit()
            return preview

        monkeypatch.setattr(cli, "preview_auto_accept", _preview_then_change)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 1
        assert _read(cli_db, CatalogContext, scene.c_assign).family_source == "manual"
        assert _read(cli_db, CatalogContext, scene.c_auto).work_family_id == scene.family.id
        for cid in (scene.c_auto, scene.c_pending, scene.c_confirm):
            assert _read(cli_db, FamilySuggestion, scene.suggestions[cid].id).decision is None

    def test_only_untouched_candidates_report_nothing_to_apply_and_ask_nothing(
        self, cli_db, committing_db, committing_factories
    ):
        """Кандидаты есть, но правило ни одного не трогает (привязка человека):
        «применять нечего», без вопроса и без применения."""
        scene = _two_families(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        _bind_source(committing_db, scene, context_id, scene.family, "manual")
        suggestion = _publish(committing_db, context_id, family_id=scene.family_b.id)
        suggestion_id = suggestion.id
        committing_db.commit()

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept"])

        assert result.exit_code == 0, result.output
        assert "none: 1" in result.output
        assert "Применять нечего" in result.output
        assert "Применить?" not in result.output
        assert _read(cli_db, FamilySuggestion, suggestion_id).decision is None

    def test_no_transaction_stays_open_while_the_prompt_waits(
        self, cli_db, committing_db, committing_factories, monkeypatch
    ):
        """Показ не держит транзакцию открытой, пока человек думает над
        подтверждением: применение идёт новой транзакцией."""
        _committed_deploy_scene(committing_db, committing_factories)
        sessions = []

        def _factory():
            session = cli_db()
            sessions.append(session)
            return session

        monkeypatch.setattr(cli, "SessionLocal", _factory)
        open_at_prompt = []

        def _confirm(*args, **kwargs):
            open_at_prompt.append(sessions[0].in_transaction())
            return True

        monkeypatch.setattr(cli.click, "confirm", _confirm)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept"])

        assert result.exit_code == 0, result.output
        assert open_at_prompt == [False]


# ---------------------------------------------------------------------------
#  Постановка схем: backfill_family_schemas и semantic-schemas-backfill
# ---------------------------------------------------------------------------

def _schema_jobs(db):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob).where(SemanticJob.kind == "family_schema")
            .order_by(SemanticJob.id)
        ).scalars()
    )


class TestSchemasBackfill:
    def test_only_active_families_without_a_current_version_get_a_schema_job(
        self, db_session, factories
    ):
        needy = _schemaless_family(db_session)
        with_current = _schemaless_family(db_session)
        _schema(db_session, factories, with_current, version=1, status="frozen")
        archived = _schemaless_family(db_session)
        archived.status = "archived"
        archived.archived_at = dt.datetime.now(dt.UTC)
        draft = _schemaless_family(db_session)
        draft.status = "draft"
        db_session.flush()

        report = backfill_family_schemas(db_session)

        assert (report.created, report.held_batch_id) == (1, None)
        assert [job.family_id for job in _schema_jobs(db_session)] == [needy.id]
        assert db_session.execute(
            sa.select(FamilyParameterSchema.family_id).where(
                FamilyParameterSchema.status == "building"
            )
        ).scalars().all() == [needy.id]
        assert needy.id not in (with_current.id, archived.id, draft.id)

    def test_a_repeat_creates_nothing_more(self, db_session, factories):
        _schemaless_family(db_session)
        backfill_family_schemas(db_session)

        report = backfill_family_schemas(db_session)

        assert (report.created, report.revived, report.held_batch_id) == (0, 0, None)
        assert len(_schema_jobs(db_session)) == 1

    def test_over_the_event_cap_the_set_is_one_held_mass_batch(
        self, db_session, factories, monkeypatch
    ):
        first = _schemaless_family(db_session)
        second = _schemaless_family(db_session)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        report = backfill_family_schemas(db_session)

        assert report.created == 0 and report.held_batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, report.held_batch_id)
        assert (batch.source, batch.status, batch.contexts_count) == ("mass", "held", 2)
        assert sorted(element["family_id"] for element in batch.held_fingerprints) == sorted(
            [first.id, second.id]
        )
        assert {element["kind"] for element in batch.held_fingerprints} == {"family_schema"}
        assert _schema_jobs(db_session) == []

    def test_exactly_on_the_cap_the_jobs_are_created(self, db_session, factories, monkeypatch):
        _schemaless_family(db_session)
        _schemaless_family(db_session)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 2)

        report = backfill_family_schemas(db_session)

        assert (report.created, report.held_batch_id) == (2, None)

    def test_command_commits_the_jobs_and_prints_the_count(
        self, cli_db, committing_db, committing_factories
    ):
        family = _schemaless_family(committing_db)
        family_id = family.id
        committing_db.commit()

        result = CliRunner().invoke(cli.cli, ["semantic-schemas-backfill"])

        assert result.exit_code == 0, result.output
        assert "поставлено=1" in result.output
        with cli_db() as check:
            jobs = check.execute(
                sa.select(SemanticJob).where(SemanticJob.kind == "family_schema")
            ).scalars().all()
            assert [job.family_id for job in jobs] == [family_id]

    def test_command_holds_a_batch_over_the_cap_and_prints_its_number(
        self, cli_db, committing_db, committing_factories, monkeypatch
    ):
        _schemaless_family(committing_db)
        _schemaless_family(committing_db)
        committing_db.commit()
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        result = CliRunner().invoke(cli.cli, ["semantic-schemas-backfill"])

        assert result.exit_code == 0, result.output
        with cli_db() as check:
            batch = check.execute(select_batch()).scalar_one()
            assert (batch.source, batch.status) == ("mass", "held")
            assert f"№{batch.id}" in result.output
            assert check.execute(
                sa.select(sa.func.count()).select_from(SemanticJob)
            ).scalar_one() == 0

    def test_command_refuses_unlisted_target_before_opening_a_session(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.setenv("DB_EXTRA_TARGETS", "")
        for name in ("PGHOSTADDR", "PGSERVICE", "PGPORT", "PGHOST", "PGDATABASE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            cli.settings, "DATABASE_URL",
            "postgresql+psycopg://test_owner:secret-pw@ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb",
            raising=False,
        )

        def _explode():
            raise AssertionError("SessionLocal() вызван - guard сработал слишком поздно")

        monkeypatch.setattr(cli, "SessionLocal", _explode)

        result = CliRunner().invoke(cli.cli, ["semantic-schemas-backfill"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)


def select_batch():
    return sa.select(SemanticReconcileBatch)


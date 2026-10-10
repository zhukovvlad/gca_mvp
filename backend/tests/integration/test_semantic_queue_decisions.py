"""Решения `admin` над очередью: предложения, задержанные, preview, пачки,
ручной повтор, остановка захвата, массовая постановка (задача 12 фичи
«Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 12.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.6 (ручной повтор), §2.9 (решения по предложению), §2.10 (preview, пачки).

Один тест — одно строго отличающееся свойство: каждое из нарушений
перепроверки — отдельный вход (`docs/insights/claimed-property-needs-its-own-
input.md`). Помощники — ЛОКАЛЬНАЯ копия помощников соседних наборов.

Отпечаток контекста для «устаревшего» входа меняется добавлением ещё одной
активной семьи его единицы: список кандидатов входит в тело запроса.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
import services.family_change as family_change_module
import services.semantic_decisions as decisions
from config import settings as app_settings
from models import (
    CatalogContext,
    FamilyParameterSchema,
    FamilySuggestion,
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    SemanticWorkerState,
    WorkFamily,
)
from services.context_routing import route_position
from services.semantic_cost import EventCap
from services.semantic_decisions import (
    ConfirmReport,
    DecisionConflict,
    approve_batch,
    assign_other_family,
    confirm_config_reask,
    confirm_suggestions,
    confirm_unit_reask,
    create_family_from_suggestion,
    decline_privacy_hold,
    discard_batch,
    enqueue_all,
    preview_batch,
    preview_config_reask,
    preview_unit_reask,
    reject_suggestion,
    release_privacy_hold,
    release_unit_privacy_holds,
    resume_worker,
    retry_job,
)
from services.semantic_privacy import build_privacy_dictionary, find_privacy_matches
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    estimate_enqueue,
    held_fingerprints,
    reconcile_semantic_jobs,
)
from services.semantic_request import load_request_material, render_context_request
from services.semantic_worker import claim_next
from services.work_families import WorkFamilyError, activate_family, create_family
from tests.factories import seed_category_id

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)

_SHOWN = [{"text": "ромашка строй", "kind": "contractor", "where": "context"}]


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _unit_id(db, code):
    from services.unit_resolution import UnitResolver

    return UnitResolver(db).resolve(code).unit_id


def _active_family(db, *, title, unit_name, actor_id, definition="Определение семьи"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id, family_category_id=seed_category_id(db))
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
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    return route_position(db, position_item_id=position.id).context_id


def _rendered(db, context_id):
    material = load_request_material(db, [context_id])[context_id]
    return render_context_request(material, settings=app_settings)


class _Scene:
    pass


def _scene(db, factories, *, titles=("Устройство пола",), unit="M2") -> _Scene:
    """Пользователь, активная семья единицы и по контексту на каждое название."""
    scene = _Scene()
    scene.user = factories.UserFactory.create()
    scene.unit_id = _unit_id(db, unit) if unit else None
    scene.unit = unit
    scene.family = _active_family(db, title="Семья пола", unit_name=unit, actor_id=scene.user.id)
    scene.proposal = _proposal(factories)
    scene.context_ids = [
        _simple_context(db, factories, scene.proposal, unit_id=scene.unit_id, title=t)
        for t in titles
    ]
    return scene


def _make_job(
    db, context_id, *, status="pending", request_hash=None, unit_id=None, privacy_matches=None,
    last_error_class=None, retry_generation=0, attempts_in_generation=0,
) -> SemanticJob:
    rendered = _rendered(db, context_id)
    job = SemanticJob(
        context_id=context_id,
        request_hash=request_hash or rendered.request_hash,
        status=status,
        unit_id=unit_id,
        privacy_matches=privacy_matches,
        last_error_class=last_error_class,
        retry_generation=retry_generation,
        attempts_in_generation=attempts_in_generation,
        next_attempt_at=NOW - dt.timedelta(minutes=1),
        prompt_version="1",
        model_requested=app_settings.SEMANTIC_MODEL,
        place_dictionary_version=rendered.place_dictionary_version,
        candidates_hash=rendered.candidates_hash,
        prefix_hash=rendered.prefix_hash,
        input_hash=rendered.input_hash,
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _make_attempt(db, job_id, *, prefix_hash="prefix-hash", cache_write_tokens=None):
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


def _published(
    db, context_id, *, family_id, is_published=True, decision=None, decided_by=None
) -> FamilySuggestion:
    """Предложение на ТЕКУЩИЙ отпечаток контекста (по умолчанию опубликованное)."""
    job = _make_job(db, context_id, status="done")
    attempt = _make_attempt(db, job.id)
    suggestion = FamilySuggestion(
        context_id=context_id,
        job_id=job.id,
        attempt_id=attempt.id,
        request_hash=job.request_hash,
        candidates_hash=job.candidates_hash,
        candidates_snapshot=[],
        family_id=family_id,
        new_family_name=None if family_id else "Новая семья",
        confidence=Decimal("0.9"),
        reason="проверочная причина",
        is_published=is_published,
        unpublished_reason=None if is_published else "stale_fingerprint",
        decision=decision,
        decided_by=decided_by,
        decided_at=dt.datetime.now(dt.UTC) if decision else None,
    )
    db.add(suggestion)
    db.flush()
    job.result_suggestion_id = suggestion.id
    db.flush()
    return suggestion


def _fresh(db, model, row_id):
    db.expire_all()
    return db.get(model, row_id)


def _make_stale(db, scene, title="Семья, меняющая отпечаток"):
    """Ещё одна активная семья той же единицы: кандидаты, а с ними и отпечаток
    каждого контекста единицы, изменились."""
    return _active_family(db, title=title, unit_name=scene.unit, actor_id=scene.user.id)


def _make_inapplicable(db, context_id):
    """Контекст неприменим, отпечаток прежний: `semantic_state` в тело запроса
    не входит, а назначение семьи его не проверяет — нарушено ровно условие
    «контекст применим»."""
    before = _rendered(db, context_id).request_hash
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(semantic_state="NOT_APPLICABLE")
    )
    db.expire_all()
    assert _rendered(db, context_id).request_hash == before, "вход теста: отпечаток не меняется"


def _events(db, context_id):
    return db.execute(
        sa.text(
            "SELECT payload FROM semantic_events WHERE context_id = :c "
            "AND event_type = 'context_family_assigned'"
        ),
        {"c": context_id},
    ).scalars().all()


def _hold_job(db, context_id, *, unit_id=None) -> SemanticJob:
    """Задание в `privacy_hold` с ТЕКУЩИМ набором совпадений (его считает
    поиск по словарю, как захват)."""
    rendered = _rendered(db, context_id)
    found = find_privacy_matches(build_privacy_dictionary(db), rendered)
    matches = [{"text": m.text, "kind": m.kind, "where": m.where} for m in found]
    assert matches, "вход теста: у контекста обязано быть совпадение"
    return _make_job(db, context_id, status="privacy_hold", unit_id=unit_id, privacy_matches=matches)


def _hold_scene(db, factories, *, titles=("Кладка Ромашка Строй стен",), unit="M2"):
    factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
    scene = _scene(db, factories, titles=titles, unit=unit)
    scene.jobs = [_hold_job(db, cid, unit_id=scene.unit_id) for cid in scene.context_ids]
    return scene


# ---------------------------------------------------------------------------
#  Подтверждение предложений
# ---------------------------------------------------------------------------

class TestConfirmSuggestions:
    def test_confirmed_suggestion_assigns_family_with_suggestion_source(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        report = confirm_suggestions(db_session, suggestion_ids=[sug.id], actor_id=scene.user.id)

        assert report == ConfirmReport(confirmed=[sug.id], skipped=[])
        ctx = _fresh(db_session, CatalogContext, scene.context_ids[0])
        assert (ctx.work_family_id, ctx.family_source, ctx.family_by) == (
            scene.family.id, "suggestion", None,
        )
        sug = _fresh(db_session, FamilySuggestion, sug.id)
        assert (sug.decision, sug.decided_by) == ("accepted", scene.user.id)
        assert sug.decided_at is not None
        assert _events(db_session, ctx.id) == [
            {"from_family_id": None, "to_family_id": scene.family.id,
             "source": "suggestion", "suggestion_id": sug.id}
        ]

    @pytest.mark.parametrize(
        "case", ["unpublished", "decided", "decided_published", "inapplicable", "stale"]
    )
    def test_suggestion_failing_recheck_is_skipped_and_others_are_assigned(
        self, db_session, factories, case
    ):
        scene = _scene(db_session, factories, titles=("Плохое", "Хорошее"))
        bad_ctx, good_ctx = scene.context_ids
        if case == "unpublished":
            bad = _published(db_session, bad_ctx, family_id=scene.family.id, is_published=False)
        elif case == "decided":
            bad = _published(
                db_session, bad_ctx, family_id=scene.family.id, is_published=False,
                decision="rejected", decided_by=scene.user.id,
            )
        elif case == "decided_published":
            # Решение есть, публикация не снята: условие «решения нет» —
            # единственное нарушенное.
            bad = _published(
                db_session, bad_ctx, family_id=scene.family.id,
                decision="accepted", decided_by=scene.user.id,
            )
        elif case == "inapplicable":
            bad = _published(db_session, bad_ctx, family_id=scene.family.id)
            _make_inapplicable(db_session, bad_ctx)
        else:
            bad = _published(db_session, bad_ctx, family_id=scene.family.id)
        good = _published(db_session, good_ctx, family_id=scene.family.id)
        if case == "stale":
            # Устаревает только первое: у второго отпечаток берётся после смены.
            _make_stale(db_session, scene)
            good_job = db_session.get(SemanticJob, good.job_id)
            new_hash = _rendered(db_session, good_ctx).request_hash
            good.request_hash = new_hash
            good_job.request_hash = new_hash
            db_session.flush()

        report = confirm_suggestions(
            db_session, suggestion_ids=[bad.id, good.id], actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[good.id], skipped=[bad.id])
        assert _fresh(db_session, CatalogContext, bad_ctx).work_family_id is None
        assert _fresh(db_session, CatalogContext, good_ctx).work_family_id == scene.family.id

    def test_suggestion_without_family_cannot_be_confirmed(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)

        report = confirm_suggestions(db_session, suggestion_ids=[sug.id], actor_id=scene.user.id)

        assert report == ConfirmReport(confirmed=[], skipped=[sug.id])
        assert _fresh(db_session, FamilySuggestion, sug.id).decision is None

    def test_unknown_id_is_skipped(self, db_session, factories):
        scene = _scene(db_session, factories)

        report = confirm_suggestions(db_session, suggestion_ids=[999_999], actor_id=scene.user.id)

        assert report == ConfirmReport(confirmed=[], skipped=[999_999])

    def test_repeated_ids_are_reported_once(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        report = confirm_suggestions(
            db_session, suggestion_ids=[sug.id, 999_999, sug.id, 999_999], actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[sug.id], skipped=[999_999])

    def test_group_is_processed_in_family_then_context_order(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        second_family = _active_family(
            db_session, title="Вторая семья", unit_name="M2", actor_id=scene.user.id
        )
        a, b, c = scene.context_ids
        # Порядок входа нарочно обратный порядку блокировок.
        s_c = _published(db_session, c, family_id=scene.family.id)
        s_b = _published(db_session, b, family_id=second_family.id)
        s_a = _published(db_session, a, family_id=second_family.id)
        # `_make_stale`-подобных правок нет: отпечатки уже учитывают обе семьи.
        order: list[tuple[int, int]] = []
        original = family_change_module.assign_family

        def _spy(db, *, context_id, family_id, **kwargs):
            order.append((family_id, context_id))
            return original(db, context_id=context_id, family_id=family_id, **kwargs)

        monkeypatch.setattr(family_change_module, "assign_family", _spy)

        report = confirm_suggestions(
            db_session, suggestion_ids=[s_c.id, s_b.id, s_a.id], actor_id=scene.user.id
        )

        assert sorted(report.confirmed) == sorted([s_a.id, s_b.id, s_c.id])
        assert order == sorted(order)
        assert len(order) == 3

    def test_family_that_no_longer_fits_the_context_skips_only_that_suggestion(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Плохое", "Хорошее"))
        bad_ctx, good_ctx = scene.context_ids
        other_unit_family = _active_family(
            db_session, title="Семья штук", unit_name="PCS", actor_id=scene.user.id
        )
        # Предложение со «своей» семьёй другой единицы: назначение отвергнет её
        # (`unit_mismatch`), остальные группы не должны пострадать.
        bad = _published(db_session, bad_ctx, family_id=other_unit_family.id)
        good = _published(db_session, good_ctx, family_id=scene.family.id)

        report = confirm_suggestions(
            db_session, suggestion_ids=[bad.id, good.id], actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[good.id], skipped=[bad.id])
        assert _fresh(db_session, FamilySuggestion, bad.id).decision is None
        assert _fresh(db_session, CatalogContext, bad_ctx).work_family_id is None


# ---------------------------------------------------------------------------
#  Отклонить, другая семья, завести семью
# ---------------------------------------------------------------------------

class TestRejectSuggestion:
    def test_reject_records_decision_and_unpublishes(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        reject_suggestion(db_session, suggestion_id=sug.id, actor_id=scene.user.id)

        sug = _fresh(db_session, FamilySuggestion, sug.id)
        assert (sug.decision, sug.is_published, sug.unpublished_reason, sug.decided_by) == (
            "rejected", False, "rejected", scene.user.id,
        )
        assert _fresh(db_session, CatalogContext, scene.context_ids[0]).work_family_id is None

    def test_rejected_suggestion_does_not_come_back_after_reconcile(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        reject_suggestion(db_session, suggestion_id=sug.id, actor_id=scene.user.id)

        report = reconcile_semantic_jobs(
            db_session, scene.context_ids, cap=NO_CAP, source="operation"
        )

        assert report.republished == 0
        sug = _fresh(db_session, FamilySuggestion, sug.id)
        assert (sug.is_published, sug.unpublished_reason) == (False, "rejected")

    @pytest.mark.parametrize(
        "case", ["unpublished", "decided", "decided_published", "inapplicable", "stale"]
    )
    def test_reject_of_a_changed_suggestion_conflicts(self, db_session, factories, case):
        scene = _scene(db_session, factories)
        if case == "unpublished":
            sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id, is_published=False)
        elif case == "decided":
            sug = _published(
                db_session, scene.context_ids[0], family_id=scene.family.id,
                is_published=False, decision="rejected", decided_by=scene.user.id,
            )
        elif case == "decided_published":
            sug = _published(
                db_session, scene.context_ids[0], family_id=scene.family.id,
                decision="accepted", decided_by=scene.user.id,
            )
        elif case == "inapplicable":
            sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
            _make_inapplicable(db_session, scene.context_ids[0])
        else:
            sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
            _make_stale(db_session, scene)

        with pytest.raises(DecisionConflict) as caught:
            reject_suggestion(db_session, suggestion_id=sug.id, actor_id=scene.user.id)

        assert caught.value.code == "suggestion_changed"


class TestAssignOtherFamily:
    def test_other_family_is_assigned_manually(self, db_session, factories):
        scene = _scene(db_session, factories)
        other = _active_family(db_session, title="Другая семья", unit_name="M2", actor_id=scene.user.id)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        assign_other_family(
            db_session, suggestion_id=sug.id, family_id=other.id, actor_id=scene.user.id
        )

        ctx = _fresh(db_session, CatalogContext, scene.context_ids[0])
        assert (ctx.work_family_id, ctx.family_source, ctx.family_by) == (
            other.id, "manual", scene.user.id,
        )
        sug = _fresh(db_session, FamilySuggestion, sug.id)
        assert (sug.decision, sug.decided_by) == ("other_family", scene.user.id)

    def test_stale_suggestion_conflicts_and_assigns_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        other = _make_stale(db_session, scene)

        with pytest.raises(DecisionConflict) as caught:
            assign_other_family(
                db_session, suggestion_id=sug.id, family_id=other.id, actor_id=scene.user.id
            )

        assert caught.value.code == "suggestion_changed"
        assert _fresh(db_session, CatalogContext, scene.context_ids[0]).work_family_id is None


class TestCreateFamilyFromSuggestion:
    def test_family_is_created_active_and_assigned_with_suggestion_source(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)

        family_id = create_family_from_suggestion(
            db_session, suggestion_id=sug.id, title="Новая семья пола",
            definition="Что входит и что не входит", actor_id=scene.user.id,
            family_category_id=seed_category_id(db_session),
        )

        family = _fresh(db_session, WorkFamily, family_id)
        assert (family.title, family.status, family.definition, family.unit_id) == (
            "Новая семья пола", "active", "Что входит и что не входит", scene.unit_id,
        )
        ctx = _fresh(db_session, CatalogContext, scene.context_ids[0])
        assert (ctx.work_family_id, ctx.family_source, ctx.family_by) == (family_id, "suggestion", None)
        sug = _fresh(db_session, FamilySuggestion, sug.id)
        assert (sug.decision, sug.decided_by) == ("family_created", scene.user.id)

    @pytest.mark.parametrize("definition", ["", "   \t"])
    def test_blank_definition_is_refused_before_any_write(self, db_session, factories, definition):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)
        families_before = db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one()

        with pytest.raises(ValueError):
            create_family_from_suggestion(
                db_session, suggestion_id=sug.id, title="Имя", definition=definition,
                actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one() == families_before
        assert _fresh(db_session, FamilySuggestion, sug.id).decision is None

    def test_blank_title_is_refused_before_any_write(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)
        families_before = db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one()

        with pytest.raises(ValueError):
            create_family_from_suggestion(
                db_session, suggestion_id=sug.id, title="  ", definition="Определение",
                actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one() == families_before

    def test_duplicate_active_name_and_unit_conflicts_and_writes_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)
        families_before = db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one()

        with pytest.raises(DecisionConflict) as caught:
            create_family_from_suggestion(
                db_session, suggestion_id=sug.id, title=" семья ПОЛА ", definition="Определение",
                actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert caught.value.code == "family_exists"
        assert caught.value.family_id == scene.family.id
        assert db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one() == families_before
        assert _fresh(db_session, CatalogContext, scene.context_ids[0]).work_family_id is None
        assert _fresh(db_session, FamilySuggestion, sug.id).decision is None

    def test_title_and_definition_are_stored_without_surrounding_spaces(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)

        family_id = create_family_from_suggestion(
            db_session, suggestion_id=sug.id, title="  Новая семья пола \t",
            definition="\n Что входит и что не входит  ", actor_id=scene.user.id,
            family_category_id=seed_category_id(db_session),
        )

        family = _fresh(db_session, WorkFamily, family_id)
        assert (family.title, family.definition) == ("Новая семья пола", "Что входит и что не входит")

    def test_other_refusal_of_activation_is_not_family_exists(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)
        families_before = db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one()

        def _refuse(db, *, family_id, actor_id):
            raise WorkFamilyError("activate_not_draft", "семья не в статусе draft")

        monkeypatch.setattr(decisions, "activate_family", _refuse)

        with pytest.raises(WorkFamilyError) as caught:
            create_family_from_suggestion(
                db_session, suggestion_id=sug.id, title="Новая", definition="Определение",
                actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert caught.value.code == "activate_not_draft"
        assert db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one() == families_before
        assert _fresh(db_session, FamilySuggestion, sug.id).decision is None

    def test_stale_suggestion_conflicts_before_creating_the_family(self, db_session, factories):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=None)
        _make_stale(db_session, scene)
        families_before = db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one()

        with pytest.raises(DecisionConflict) as caught:
            create_family_from_suggestion(
                db_session, suggestion_id=sug.id, title="Новая", definition="Определение",
                actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert caught.value.code == "suggestion_changed"
        assert db_session.execute(sa.select(sa.func.count()).select_from(WorkFamily)).scalar_one() == families_before


# ---------------------------------------------------------------------------
#  Задержанные проверкой
# ---------------------------------------------------------------------------

def _break_hold(db, scene, case):
    """Каждое из четырёх нарушений перепроверки — отдельный вход; возвращает
    показанный набор, который экран передал бы решению."""
    job = scene.jobs[0]
    if case == "not_privacy_hold":
        job.status = "pending"
        db.flush()
        return _SHOWN
    if case == "inapplicable":
        db.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == job.context_id)
            .values(archived_at=dt.datetime.now(dt.UTC))
        )
        return _SHOWN
    if case == "stale_fingerprint":
        _make_stale(db, scene)
        return _SHOWN
    if case == "matches_differ":
        return [{"text": "ромашка строй", "kind": "contractor", "where": "prompt"}]
    raise AssertionError(case)


_HOLD_VIOLATIONS = ["not_privacy_hold", "inapplicable", "stale_fingerprint", "matches_differ"]


class TestPrivacyHoldDecisions:
    def test_release_sends_the_job_with_the_shown_set(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        # Время следующей попытки в будущем: иначе «сброшено на сейчас» не
        # отличить от «не тронуто».
        scene.jobs[0].next_attempt_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        db_session.flush()

        release_privacy_hold(
            db_session, job_id=scene.jobs[0].id, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        job = _fresh(db_session, SemanticJob, scene.jobs[0].id)
        assert job.status == "pending"
        assert job.privacy_released_matches == _SHOWN
        assert (job.privacy_decided_by, job.privacy_decided_at is not None) == (scene.user.id, True)
        assert job.next_attempt_at <= dt.datetime.now(dt.UTC)

    def test_release_accepts_several_matches_in_the_order_the_capture_stored_them(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        factories.ObjectFactory.create(title="Альфа Парк")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))
        # Совпадение в списке семей идёт ПОСЛЕ совпадения в строке контекста —
        # порядок захвата не совпадает с сортировкой по тексту.
        _active_family(db_session, title="Семья Альфа Парк", unit_name="M2", actor_id=scene.user.id)
        job = _hold_job(db_session, scene.context_ids[0], unit_id=scene.unit_id)
        shown = list(job.privacy_matches)
        assert [m["where"] for m in shown][0] == "context" and len(shown) == 2
        assert shown != sorted(shown, key=lambda m: m["text"])

        release_privacy_hold(db_session, job_id=job.id, shown_matches=shown, actor_id=scene.user.id)

        assert _fresh(db_session, SemanticJob, job.id).status == "pending"

    def test_released_job_is_claimed_by_the_worker(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        release_privacy_hold(
            db_session, job_id=scene.jobs[0].id, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        claim = claim_next(db_session, settings=app_settings, now=dt.datetime.now(dt.UTC))

        assert claim is not None and claim.job_id == scene.jobs[0].id

    def test_decline_cancels_the_job(self, db_session, factories):
        scene = _hold_scene(db_session, factories)

        decline_privacy_hold(
            db_session, job_id=scene.jobs[0].id, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        job = _fresh(db_session, SemanticJob, scene.jobs[0].id)
        assert (job.status, job.cancel_reason) == ("cancelled", "privacy_declined")
        assert (job.privacy_decided_by, job.privacy_decided_at is not None) == (scene.user.id, True)

    @pytest.mark.parametrize("case", _HOLD_VIOLATIONS)
    def test_release_of_a_changed_job_conflicts(self, db_session, factories, case):
        scene = _hold_scene(db_session, factories)
        shown = _break_hold(db_session, scene, case)

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                db_session, job_id=scene.jobs[0].id, shown_matches=shown, actor_id=scene.user.id
            )

        assert caught.value.code == "job_changed"
        job = _fresh(db_session, SemanticJob, scene.jobs[0].id)
        assert job.privacy_released_matches is None

    @pytest.mark.parametrize("case", _HOLD_VIOLATIONS)
    def test_decline_of_a_changed_job_conflicts(self, db_session, factories, case):
        scene = _hold_scene(db_session, factories)
        shown = _break_hold(db_session, scene, case)

        with pytest.raises(DecisionConflict) as caught:
            decline_privacy_hold(
                db_session, job_id=scene.jobs[0].id, shown_matches=shown, actor_id=scene.user.id
            )

        assert caught.value.code == "job_changed"
        assert _fresh(db_session, SemanticJob, scene.jobs[0].id).cancel_reason is None


def _current_matches(db, context_id):
    found = find_privacy_matches(build_privacy_dictionary(db), _rendered(db, context_id))
    return [{"text": m.text, "kind": m.kind, "where": m.where} for m in found]


def _rename_contractor_to_nothing(db, title="ООО «Одуванчик»"):
    db.execute(sa.text("UPDATE contractors SET title = :t"), {"t": title})
    db.expire_all()


class TestPrivacyHoldDictionaryChange:
    """Словарь изменился после задержания: задание не должно застревать на
    сохранённом наборе, который уже не воспроизвести."""

    def test_release_refreshes_the_stored_set_to_the_current_one_and_conflicts(
        self, db_session, factories
    ):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        db_session.expire_all()
        current = _current_matches(db_session, job.context_id)
        assert current and current != job.privacy_matches, "вход теста: набор изменился, но не пуст"

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                db_session, job_id=job.id, shown_matches=job.privacy_matches, actor_id=scene.user.id
            )

        assert caught.value.code == "job_changed"
        fresh = _fresh(db_session, SemanticJob, job.id)
        assert (fresh.status, fresh.privacy_matches) == ("privacy_hold", current)
        assert fresh.privacy_released_matches is None

    def test_decision_over_the_refreshed_set_passes(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        old = list(job.privacy_matches)
        factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        db_session.expire_all()
        with pytest.raises(DecisionConflict):
            release_privacy_hold(db_session, job_id=job.id, shown_matches=old, actor_id=scene.user.id)
        refreshed = list(_fresh(db_session, SemanticJob, job.id).privacy_matches)

        release_privacy_hold(db_session, job_id=job.id, shown_matches=refreshed, actor_id=scene.user.id)

        fresh = _fresh(db_session, SemanticJob, job.id)
        assert (fresh.status, fresh.privacy_released_matches) == ("pending", refreshed)

    def test_decline_refreshes_the_stored_set_too(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        db_session.expire_all()
        current = _current_matches(db_session, job.context_id)
        assert current != job.privacy_matches

        with pytest.raises(DecisionConflict):
            decline_privacy_hold(
                db_session, job_id=job.id, shown_matches=job.privacy_matches, actor_id=scene.user.id
            )

        fresh = _fresh(db_session, SemanticJob, job.id)
        assert (fresh.status, fresh.privacy_matches, fresh.cancel_reason) == (
            "privacy_hold", current, None,
        )

    def test_clean_dictionary_sends_the_job_back_to_pending(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        job.next_attempt_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        db_session.flush()
        _rename_contractor_to_nothing(db_session)
        assert _current_matches(db_session, job.context_id) == [], "вход теста: словарь чист"

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                db_session, job_id=job.id, shown_matches=job.privacy_matches, actor_id=scene.user.id
            )

        assert caught.value.code == "job_changed"
        fresh = _fresh(db_session, SemanticJob, job.id)
        assert fresh.status == "pending"
        assert fresh.privacy_matches == []
        assert fresh.next_attempt_at <= dt.datetime.now(dt.UTC)
        assert fresh.privacy_released_matches is None and fresh.privacy_decided_by is None

    @pytest.mark.parametrize("violation", ["stale_fingerprint", "inapplicable", "declined_meanwhile"])
    def test_other_violations_do_not_rewrite_the_job(self, db_session, factories, violation):
        """Словарь тоже изменился (стал чистым), но нарушено и другое условие
        перепроверки: набор не переписывается, и задание не уходит в очередь —
        в том числе уже отклонённое другим `admin`."""
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        before = list(job.privacy_matches)
        if violation == "stale_fingerprint":
            _make_stale(db_session, scene)
        elif violation == "inapplicable":
            _make_inapplicable(db_session, job.context_id)
        else:
            decline_privacy_hold(
                db_session, job_id=job.id, shown_matches=before, actor_id=scene.user.id
            )
        _rename_contractor_to_nothing(db_session)
        assert _current_matches(db_session, job.context_id) == [], "вход теста: словарь чист"

        with pytest.raises(DecisionConflict):
            release_privacy_hold(
                db_session, job_id=job.id, shown_matches=before, actor_id=scene.user.id
            )

        fresh = _fresh(db_session, SemanticJob, job.id)
        status = "cancelled" if violation == "declined_meanwhile" else "privacy_hold"
        assert (fresh.status, fresh.privacy_matches) == (status, before)

    def test_unit_release_skips_and_refreshes_the_job_with_a_changed_set(
        self, db_session, factories
    ):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        old = list(job.privacy_matches)
        factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        db_session.expire_all()
        current = _current_matches(db_session, job.context_id)
        assert current != old

        report = release_unit_privacy_holds(
            db_session, unit_id=scene.unit_id, shown_matches=old, actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[], skipped=[job.id])
        fresh = _fresh(db_session, SemanticJob, job.id)
        assert (fresh.status, fresh.privacy_matches) == ("privacy_hold", current)


class TestPrivacyHoldRefreshIsLeftToTheCaller:
    def test_service_marks_the_refusal_and_does_not_commit_the_refresh(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _hold_scene(committing_db, committing_factories)
        job_id, old = scene.jobs[0].id, list(scene.jobs[0].privacy_matches)
        committing_factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        committing_db.commit()

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                committing_db, job_id=job_id, shown_matches=old, actor_id=scene.user.id
            )

        assert caught.value.keep is True
        with committing_session_factory() as other:
            assert other.get(SemanticJob, job_id).privacy_matches == old, "до коммита вызывающего"
        committing_db.rollback()
        with committing_session_factory() as other:
            assert other.get(SemanticJob, job_id).privacy_matches == old, "откат стирает запись"

    def test_other_refusals_do_not_ask_to_keep(self, db_session, factories):
        scene = _hold_scene(db_session, factories)
        _make_stale(db_session, scene)

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                db_session, job_id=scene.jobs[0].id, shown_matches=_SHOWN, actor_id=scene.user.id
            )

        assert caught.value.keep is False


class TestReleaseUnitPrivacyHolds:
    def test_only_holds_of_the_unit_are_sent(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        m2 = _scene(db_session, factories, titles=("Кладка Ромашка Строй А", "Кладка Ромашка Строй Б"))
        pieces = _scene(db_session, factories, titles=("Штука Ромашка Строй",), unit="PCS")
        m2_jobs = [_hold_job(db_session, cid, unit_id=m2.unit_id) for cid in m2.context_ids]
        other_job = _hold_job(db_session, pieces.context_ids[0], unit_id=pieces.unit_id)

        report = release_unit_privacy_holds(
            db_session, unit_id=m2.unit_id, shown_matches=_SHOWN, actor_id=m2.user.id
        )

        assert report == ConfirmReport(confirmed=sorted(j.id for j in m2_jobs), skipped=[])
        assert _fresh(db_session, SemanticJob, other_job.id).status == "privacy_hold"
        assert all(_fresh(db_session, SemanticJob, j.id).status == "pending" for j in m2_jobs)

    def test_none_addresses_only_jobs_without_a_unit(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй А",))
        with_unit = _hold_job(db_session, scene.context_ids[0], unit_id=scene.unit_id)
        no_unit_scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй Б",), unit=None)
        no_unit = _hold_job(db_session, no_unit_scene.context_ids[0], unit_id=None)

        report = release_unit_privacy_holds(
            db_session, unit_id=None, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[no_unit.id], skipped=[])
        assert _fresh(db_session, SemanticJob, with_unit.id).status == "privacy_hold"

    def test_jobs_of_the_unit_in_other_statuses_are_neither_sent_nor_skipped(
        self, db_session, factories
    ):
        scene = _hold_scene(
            db_session, factories, titles=("Кладка Ромашка Строй А", "Кладка Ромашка Строй Б")
        )
        held, pending = scene.jobs
        pending.status = "pending"
        db_session.flush()

        report = release_unit_privacy_holds(
            db_session, unit_id=scene.unit_id, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[held.id], skipped=[])
        assert _fresh(db_session, SemanticJob, pending.id).privacy_released_matches is None

    def test_job_failing_recheck_is_skipped_and_others_are_sent(self, db_session, factories):
        scene = _hold_scene(
            db_session, factories, titles=("Кладка Ромашка Строй А", "Кладка Ромашка Строй Б")
        )
        bad, good = scene.jobs
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == bad.context_id)
            .values(archived_at=dt.datetime.now(dt.UTC))
        )

        report = release_unit_privacy_holds(
            db_session, unit_id=scene.unit_id, shown_matches=_SHOWN, actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[good.id], skipped=[bad.id])
        assert _fresh(db_session, SemanticJob, bad.id).status == "privacy_hold"
        assert _fresh(db_session, SemanticJob, good.id).status == "pending"


# ---------------------------------------------------------------------------
#  Ручной повтор
# ---------------------------------------------------------------------------

class TestRetryJob:
    def test_error_job_returns_to_pending_as_the_same_job(self, db_session, factories):
        scene = _scene(db_session, factories)
        job = _make_job(
            db_session, scene.context_ids[0], status="error", last_error_class="schema_error",
            retry_generation=2, attempts_in_generation=3,
        )
        # Отсрочка в будущем: «обнулена» отличима от «не тронута».
        job.next_attempt_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        db_session.flush()
        attempt = _make_attempt(db_session, job.id)

        retry_job(db_session, job_id=job.id, actor_id=scene.user.id)

        fresh = _fresh(db_session, SemanticJob, job.id)
        assert (fresh.id, fresh.status, fresh.retry_generation, fresh.attempts_in_generation) == (
            job.id, "pending", 3, 0,
        )
        assert fresh.last_error_class is None
        assert fresh.next_attempt_at <= dt.datetime.now(dt.UTC)
        attempts = db_session.execute(
            sa.select(SemanticJobAttempt.id).where(SemanticJobAttempt.job_id == job.id)
        ).scalars().all()
        assert attempts == [attempt.id]
        jobs = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticJob)
            .where(SemanticJob.context_id == scene.context_ids[0])
        ).scalar_one()
        assert jobs == 1

    @pytest.mark.parametrize("status", ["pending", "done"])
    def test_job_not_in_error_conflicts(self, db_session, factories, status):
        scene = _scene(db_session, factories)
        job = _make_job(db_session, scene.context_ids[0], status=status)

        with pytest.raises(DecisionConflict) as caught:
            retry_job(db_session, job_id=job.id, actor_id=scene.user.id)

        assert caught.value.code == "job_changed"
        assert _fresh(db_session, SemanticJob, job.id).retry_generation == 0


# ---------------------------------------------------------------------------
#  Preview и подтверждение перезапросов
# ---------------------------------------------------------------------------

class TestPreview:
    def test_counts_and_sums_come_from_the_enqueue_estimate(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))

        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)

        pairs, reserve, cached = estimate_enqueue(db_session, scene.context_ids)
        assert preview.context_count == len(pairs) == 2
        assert preview.reserve_usd == reserve > 0
        assert preview.expected_cached_usd == cached > 0

    def test_hash_is_stable_for_unchanged_state(self, db_session, factories):
        scene = _scene(db_session, factories)

        first = preview_unit_reask(db_session, unit_id=scene.unit_id)
        second = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert first.preview_hash == second.preview_hash

    def test_hash_changes_when_the_set_changes(self, db_session, factories):
        scene = _scene(db_session, factories)
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        _simple_context(
            db_session, factories, scene.proposal, unit_id=scene.unit_id, title="Ещё один контекст"
        )

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert after.context_count == before.context_count + 1
        assert after.preview_hash != before.preview_hash

    @pytest.mark.parametrize(
        ("zeroed_tariff", "moved", "still"),
        [
            # Тариф чтения кэша нулевой: ожидаемая цена от токенов префикса не
            # зависит, меняется один резерв.
            ("SEMANTIC_PRICE_CACHE_READ_PER_M", "reserve_usd", "expected_cached_usd"),
            # Тариф записи кэша нулевой: резерв не зависит, меняется одна цена.
            ("SEMANTIC_PRICE_CACHE_WRITE_PER_M", "expected_cached_usd", "reserve_usd"),
        ],
    )
    def test_hash_changes_when_only_one_sum_changes_over_the_same_set(
        self, db_session, factories, monkeypatch, zeroed_tariff, moved, still
    ):
        scene = _scene(db_session, factories)
        monkeypatch.setattr(app_settings, zeroed_tariff, Decimal("0"))
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        rendered = _rendered(db_session, scene.context_ids[0])
        # Новое наблюдение токенов того же префикса: набор пар прежний.
        observer = _make_job(db_session, scene.context_ids[0], status="done", request_hash="другой")
        _make_attempt(
            db_session, observer.id, prefix_hash=rendered.prefix_hash,
            cache_write_tokens=rendered.prefix_bytes * 3,
        )

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert [
            (fp.context_id, fp.request_hash) for fp in estimate_enqueue(db_session, scene.context_ids)[0]
        ] == [(scene.context_ids[0], rendered.request_hash)]
        assert after.context_count == before.context_count
        assert getattr(after, moved) != getattr(before, moved)
        assert getattr(after, still) == getattr(before, still)
        assert after.preview_hash != before.preview_hash

    def test_hash_changes_when_only_the_pairs_change(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories)
        # Все тарифы нулевые: обе суммы — ноль при любом наборе, число
        # контекстов прежнее — меняется только отпечаток пары.
        for name in (
            "SEMANTIC_PRICE_INPUT_PER_M", "SEMANTIC_PRICE_CACHE_WRITE_PER_M",
            "SEMANTIC_PRICE_CACHE_READ_PER_M", "SEMANTIC_PRICE_OUTPUT_PER_M",
        ):
            monkeypatch.setattr(app_settings, name, Decimal("0"))
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        _make_stale(db_session, scene)

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert (after.context_count, after.reserve_usd, after.expected_cached_usd) == (
            before.context_count, before.reserve_usd, before.expected_cached_usd,
        )
        assert after.preview_hash != before.preview_hash

    def test_hash_changes_with_a_tariff(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories)
        # Набор пуст (у контекста уже есть задание текущего отпечатка): тариф
        # не двигает ни одной суммы, в хэш он входит сам.
        _make_job(db_session, scene.context_ids[0], status="pending")
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        monkeypatch.setattr(app_settings, "SEMANTIC_PRICE_OUTPUT_PER_M", Decimal("11"))

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert (after.context_count, after.reserve_usd, after.expected_cached_usd) == (
            0, before.reserve_usd, before.expected_cached_usd,
        )
        assert after.preview_hash != before.preview_hash

    def test_hash_sees_a_one_token_change_of_the_prefix(self, db_session, factories):
        scene = _scene(db_session, factories)
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        rendered = _rendered(db_session, scene.context_ids[0])
        observer = _make_job(db_session, scene.context_ids[0], status="done", request_hash="другой")
        _make_attempt(
            db_session, observer.id, prefix_hash=rendered.prefix_hash,
            cache_write_tokens=rendered.prefix_bytes + 1,
        )

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        # Суммы — точно, без округления (спека §2.10): разница меньше сотой цента.
        assert Decimal("0") < after.reserve_usd - before.reserve_usd < Decimal("0.0001")
        assert after.preview_hash != before.preview_hash

    def test_hash_changes_with_the_reserve_formula_version(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories)
        before = preview_unit_reask(db_session, unit_id=scene.unit_id)
        monkeypatch.setattr(decisions, "RESERVE_FORMULA_VERSION", 99)

        after = preview_unit_reask(db_session, unit_id=scene.unit_id)

        assert (after.reserve_usd, after.expected_cached_usd) == (
            before.reserve_usd, before.expected_cached_usd,
        )
        assert after.preview_hash != before.preview_hash

    def test_unit_none_addresses_only_contexts_without_a_unit(self, db_session, factories):
        with_unit = _scene(db_session, factories, titles=("С единицей",))
        no_unit = _scene(db_session, factories, titles=("Без единицы А", "Без единицы Б"), unit=None)

        assert preview_unit_reask(db_session, unit_id=None).context_count == 2
        assert preview_unit_reask(db_session, unit_id=with_unit.unit_id).context_count == 1
        assert no_unit.unit_id is None

    def test_config_preview_covers_every_live_context_and_skips_archived(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[2])
            .values(archived_at=dt.datetime.now(dt.UTC))
        )

        assert preview_config_reask(db_session).context_count == 2


class TestConfirmReask:
    def _over_cap(self, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_RESERVE_USD", Decimal("0"))

    def test_unit_reask_creates_jobs_above_the_event_cap_without_a_batch(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        self._over_cap(monkeypatch)
        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)

        report = confirm_unit_reask(
            db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
            actor_id=scene.user.id,
        )

        assert (report.created, report.held_batch_id) == (3, None)
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticReconcileBatch)).scalar_one() == 0

    def test_config_reask_creates_jobs_above_the_event_cap_without_a_batch(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        self._over_cap(monkeypatch)
        preview = preview_config_reask(db_session)

        report = confirm_config_reask(
            db_session, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )

        assert (report.created, report.held_batch_id) == (2, None)

    def test_unit_reask_with_a_changed_set_conflicts_and_creates_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)
        _simple_context(
            db_session, factories, scene.proposal, unit_id=scene.unit_id, title="Появился после preview"
        )

        with pytest.raises(DecisionConflict) as caught:
            confirm_unit_reask(
                db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "preview_changed"
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0

    @pytest.mark.parametrize(
        "zeroed_tariff", ["SEMANTIC_PRICE_CACHE_READ_PER_M", "SEMANTIC_PRICE_CACHE_WRITE_PER_M"]
    )
    def test_unit_reask_with_one_changed_sum_and_the_same_set_conflicts(
        self, db_session, factories, monkeypatch, zeroed_tariff
    ):
        scene = _scene(db_session, factories)
        monkeypatch.setattr(app_settings, zeroed_tariff, Decimal("0"))
        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)
        rendered = _rendered(db_session, scene.context_ids[0])
        observer = _make_job(db_session, scene.context_ids[0], status="done", request_hash="другой")
        _make_attempt(
            db_session, observer.id, prefix_hash=rendered.prefix_hash,
            cache_write_tokens=rendered.prefix_bytes * 3,
        )

        with pytest.raises(DecisionConflict) as caught:
            confirm_unit_reask(
                db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "preview_changed"

    def test_config_reask_with_a_changed_set_conflicts(self, db_session, factories):
        scene = _scene(db_session, factories)
        preview = preview_config_reask(db_session)
        _make_stale(db_session, scene)
        _simple_context(
            db_session, factories, scene.proposal, unit_id=scene.unit_id, title="Ещё один контекст"
        )

        with pytest.raises(DecisionConflict) as caught:
            confirm_config_reask(
                db_session, preview_hash=preview.preview_hash, actor_id=scene.user.id
            )

        assert caught.value.code == "preview_changed"

    def test_batch_source_of_reask_jobs_does_not_bypass_the_daily_budget(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories)
        preview = preview_unit_reask(db_session, unit_id=scene.unit_id)
        confirm_unit_reask(
            db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
            actor_id=scene.user.id,
        )
        monkeypatch.setattr(app_settings, "SEMANTIC_DAILY_BUDGET_USD", Decimal("0"))

        claim = claim_next(db_session, settings=app_settings, now=dt.datetime.now(dt.UTC))

        assert claim is None
        job = db_session.execute(sa.select(SemanticJob)).scalar_one()
        assert (job.status, job.attempts_in_generation) == ("pending", 0)


# ---------------------------------------------------------------------------
#  Удержанные пачки
# ---------------------------------------------------------------------------

def _held_batch(db, scene, *, source="mass") -> int:
    cap = EventCap(max_contexts=1, max_reserve_usd=Decimal("1000000"))
    report = reconcile_semantic_jobs(db, scene.context_ids, cap=cap, source=source)
    assert report.held_batch_id is not None
    return report.held_batch_id


class TestBatches:
    def test_estimate_equals_what_reconcile_puts_into_the_batch(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)

        pairs, reserve, cached = estimate_enqueue(db_session, scene.context_ids)

        batch = db_session.get(SemanticReconcileBatch, batch_id)
        assert pairs == held_fingerprints(db_session, scene.context_ids)
        assert (batch.reserve_estimate_usd, batch.cached_estimate_usd) == (reserve, cached)

    def test_approve_sets_jobs_without_the_cap_and_marks_the_batch(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        batch_id = _held_batch(db_session, scene, source="operation")
        audit_before = [list(p) for p in db_session.get(SemanticReconcileBatch, batch_id).held_fingerprints]
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)
        preview = preview_batch(db_session, batch_id=batch_id)

        report = approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )

        assert (report.created, report.held_batch_id) == (3, None)
        batch = _fresh(db_session, SemanticReconcileBatch, batch_id)
        assert (batch.status, batch.decided_by, batch.source) == ("approved", scene.user.id, "operation")
        assert batch.decided_at is not None
        assert [list(p) for p in batch.held_fingerprints] == audit_before
        jobs = db_session.execute(sa.select(SemanticJob)).scalars().all()
        assert len(jobs) == 3 and {j.batch_id for j in jobs} == {batch_id}

    def test_batch_id_marks_only_jobs_of_the_current_pairs(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        # Задание прежнего отпечатка того же контекста пачки: не из неё.
        old = _make_job(db_session, scene.context_ids[0], status="error", request_hash="прежний")
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)

        approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )

        assert _fresh(db_session, SemanticJob, old.id).batch_id is None
        new_jobs = db_session.execute(
            sa.select(SemanticJob).where(SemanticJob.id != old.id)
        ).scalars().all()
        assert len(new_jobs) == 2 and {j.batch_id for j in new_jobs} == {batch_id}

    def test_second_approval_conflicts_and_does_not_enqueue_twice(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )

        with pytest.raises(DecisionConflict) as caught:
            approve_batch(
                db_session, batch_id=batch_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "batch_decided"
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 2

    def test_discard_marks_the_batch_and_keeps_the_audit(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        audit_before = [list(p) for p in db_session.get(SemanticReconcileBatch, batch_id).held_fingerprints]

        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        batch = _fresh(db_session, SemanticReconcileBatch, batch_id)
        assert (batch.status, batch.decided_by) == ("discarded", scene.user.id)
        assert [list(p) for p in batch.held_fingerprints] == audit_before
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0

    def test_discard_after_discard_conflicts(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        with pytest.raises(DecisionConflict) as caught:
            discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        assert caught.value.code == "batch_decided"

    def test_approve_after_discard_conflicts(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        with pytest.raises(DecisionConflict) as caught:
            approve_batch(
                db_session, batch_id=batch_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "batch_decided"
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0

    def test_discard_after_approve_conflicts(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )

        with pytest.raises(DecisionConflict) as caught:
            discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        assert caught.value.code == "batch_decided"

    def test_approve_with_a_stale_preview_conflicts_and_leaves_the_batch_held(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        _make_stale(db_session, scene)

        with pytest.raises(DecisionConflict) as caught:
            approve_batch(
                db_session, batch_id=batch_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "preview_changed"
        assert _fresh(db_session, SemanticReconcileBatch, batch_id).status == "held"
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0

    def test_preview_of_a_batch_uses_current_fingerprints(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        stored = [tuple(p) for p in db_session.get(SemanticReconcileBatch, batch_id).held_fingerprints]
        before = preview_batch(db_session, batch_id=batch_id)
        _make_stale(db_session, scene)

        after = preview_batch(db_session, batch_id=batch_id)

        current = held_fingerprints(db_session, scene.context_ids)
        assert current != stored
        assert after.context_count == len(current)
        assert after.preview_hash != before.preview_hash

    def test_approved_jobs_still_respect_the_daily_budget(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        approve_batch(
            db_session, batch_id=batch_id, preview_hash=preview.preview_hash, actor_id=scene.user.id
        )
        monkeypatch.setattr(app_settings, "SEMANTIC_DAILY_BUDGET_USD", Decimal("0"))

        assert claim_next(db_session, settings=app_settings, now=dt.datetime.now(dt.UTC)) is None


def _without_schema(db, family_id):
    """Семья сцены заведена уже со схемой; для проверки сверки схем её убирают:
    активная семья без текущей версии ждёт конца перезапроса единицы."""
    db.execute(sa.delete(FamilyParameterSchema).where(FamilyParameterSchema.family_id == family_id))
    db.flush()


def _schema_jobs_of(db, family_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob).where(
                SemanticJob.kind == "family_schema", SemanticJob.family_id == family_id
            )
        ).scalars()
    )


class TestDiscardReconcilesSchemas:
    """Удержанная пачка предложений не даёт строить схемы единицы (спека §2.6);
    после отбрасывания блокировка исчезает, и семьи без схемы получают задание
    схемы, а не ждут чужого события."""

    def test_discard_of_the_blocking_batch_sets_the_schema_job(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        _without_schema(db_session, scene.family.id)
        batch_id = _held_batch(db_session, scene, source="operation")
        assert _schema_jobs_of(db_session, scene.family.id) == [], "вход: пачка блокирует схему"

        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        (job,) = _schema_jobs_of(db_session, scene.family.id)
        assert job.status == "pending"

    def test_another_live_suggestion_job_in_the_unit_keeps_the_schema_waiting(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        _without_schema(db_session, scene.family.id)
        _make_job(db_session, scene.context_ids[2], unit_id=scene.unit_id)
        scene.context_ids = scene.context_ids[:2]
        batch_id = _held_batch(db_session, scene, source="operation")

        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        assert _schema_jobs_of(db_session, scene.family.id) == []

    def test_a_family_of_another_unit_is_left_alone_by_a_non_mass_batch(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        other = _scene(db_session, factories, titles=("Труба А",), unit="M3")
        _without_schema(db_session, scene.family.id)
        _without_schema(db_session, other.family.id)
        batch_id = _held_batch(db_session, scene, source="operation")

        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        assert len(_schema_jobs_of(db_session, scene.family.id)) == 1
        assert _schema_jobs_of(db_session, other.family.id) == []

    def test_discard_of_a_mass_batch_reconciles_the_families_of_all_units(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        other = _scene(db_session, factories, titles=("Труба А",), unit="M3")
        _without_schema(db_session, scene.family.id)
        _without_schema(db_session, other.family.id)
        batch_id = _held_batch(db_session, scene, source="mass")
        assert _schema_jobs_of(db_session, other.family.id) == [], "вход: mass держит все единицы"

        discard_batch(db_session, batch_id=batch_id, actor_id=scene.user.id)

        assert len(_schema_jobs_of(db_session, scene.family.id)) == 1
        assert len(_schema_jobs_of(db_session, other.family.id)) == 1


# ---------------------------------------------------------------------------
#  Остановка захвата
# ---------------------------------------------------------------------------

def _pause(db, context_id):
    old = _make_job(db, context_id, status="done", request_hash="pause-hash")
    attempt = _make_attempt(db, old.id)
    db.execute(
        sa.text(
            "UPDATE semantic_worker_state SET claim_paused = true, paused_reason = 'manual', "
            "paused_at = :at, paused_attempt_id = :aid WHERE id = 1"
        ),
        {"at": NOW, "aid": attempt.id},
    )
    db.expire_all()


class TestResumeWorker:
    def test_resume_clears_the_stop_and_records_the_author(self, db_session, factories):
        scene = _scene(db_session, factories)
        _pause(db_session, scene.context_ids[0])

        resume_worker(db_session, actor_id=scene.user.id)

        state = _fresh(db_session, SemanticWorkerState, 1)
        assert (
            state.claim_paused, state.paused_reason, state.paused_attempt_id, state.paused_at,
            state.last_resumed_by,
        ) == (False, None, None, None, scene.user.id)
        assert state.last_resumed_at is not None

    def test_resume_of_a_running_worker_writes_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)

        resume_worker(db_session, actor_id=scene.user.id)

        state = _fresh(db_session, SemanticWorkerState, 1)
        assert (state.claim_paused, state.last_resumed_by, state.last_resumed_at) == (False, None, None)


# ---------------------------------------------------------------------------
#  Массовая постановка
# ---------------------------------------------------------------------------

class TestEnqueueAll:
    def test_batch_is_always_created_even_under_the_cap(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))

        batch_id = enqueue_all(db_session)

        batch = _fresh(db_session, SemanticReconcileBatch, batch_id)
        assert (batch.source, batch.status, batch.contexts_count) == ("mass", "held", 2)
        assert [Fingerprint.from_dict(e) for e in batch.held_fingerprints] == held_fingerprints(
            db_session, scene.context_ids
        )
        _pairs, reserve, cached = estimate_enqueue(db_session, scene.context_ids)
        assert (batch.reserve_estimate_usd, batch.cached_estimate_usd) == (reserve, cached)
        assert reserve != cached
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0

    def test_nothing_to_enqueue_returns_none_without_a_batch(self, db_session, factories):
        scene = _scene(db_session, factories)
        _make_job(db_session, scene.context_ids[0], status="pending")

        assert enqueue_all(db_session) is None
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticReconcileBatch)).scalar_one() == 0

    def test_archived_contexts_are_not_enqueued(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[1])
            .values(archived_at=dt.datetime.now(dt.UTC))
        )

        batch_id = enqueue_all(db_session)

        assert _fresh(db_session, SemanticReconcileBatch, batch_id).contexts_count == 1

    def test_existing_jobs_are_not_cancelled(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        stale = _make_job(db_session, scene.context_ids[0], request_hash="устарело")

        enqueue_all(db_session)

        assert _fresh(db_session, SemanticJob, stale.id).status == "pending"


class TestEnqueueAllCommand:
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
            raise AssertionError("SessionLocal() вызван — guard сработал слишком поздно")

        monkeypatch.setattr(cli, "SessionLocal", _explode)

        result = CliRunner().invoke(cli.cli, ["semantic-enqueue-all"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)

    def test_command_commits_the_batch_and_prints_its_number(
        self, committing_session_factory, committing_factories, committing_db, monkeypatch
    ):
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)
        scene = _scene(committing_db, committing_factories, titles=("Пол А", "Пол Б"))
        committing_db.commit()

        result = CliRunner().invoke(cli.cli, ["semantic-enqueue-all"])

        assert result.exit_code == 0, result.output
        with committing_session_factory() as check:
            batch = check.execute(sa.select(SemanticReconcileBatch)).scalar_one()
            assert (batch.source, batch.status, batch.contexts_count) == ("mass", "held", 2)
            assert f"№{batch.id}" in result.output
            assert "контекстов=2" in result.output
            assert f"резерв=${batch.reserve_estimate_usd:.4f}" in result.output
        assert scene.context_ids

    def test_command_says_there_is_nothing_to_enqueue(
        self, committing_session_factory, monkeypatch
    ):
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)

        result = CliRunner().invoke(cli.cli, ["semantic-enqueue-all"])

        assert result.exit_code == 0, result.output
        assert "Ставить нечего" in result.output
        with committing_session_factory() as check:
            assert check.execute(sa.select(sa.func.count()).select_from(SemanticReconcileBatch)).scalar_one() == 0


# ---------------------------------------------------------------------------
#  Порядок блокировок и перечитывание под блокировкой
# ---------------------------------------------------------------------------

def _lock_statements(db, action):
    """Выполняет `action` и возвращает `(таблица, режим)` каждого `SELECT ...
    FOR UPDATE | FOR SHARE` в порядке исполнения — проверяется SQL, ушедший в
    базу, а не намерение вызова (`docs/pitfalls/db.md`)."""
    seen: list[tuple[str, str]] = []
    engine = db.get_bind().engine

    def _listener(conn, cursor, statement, parameters, context, executemany):
        text = " ".join(statement.split())
        if " FOR UPDATE" in text or " FOR SHARE" in text:
            table = re.search(r" FROM (\w+)", text).group(1)
            seen.append((table, "SHARE" if " FOR SHARE" in text else "UPDATE"))

    sa.event.listen(engine, "before_cursor_execute", _listener)
    try:
        action()
    finally:
        sa.event.remove(engine, "before_cursor_execute", _listener)
    return seen


def _change_before_context_lock(monkeypatch, db, sql, params):
    """Правка строки между чтением без блокировки и блокировкой контекста —
    то, что сделала бы транзакция, закоммитившаяся в этот промежуток."""
    original = family_change_module._lock_contexts
    done: list[bool] = []

    def _lock(session, context_ids):
        if not done:
            done.append(True)
            session.execute(sa.text(sql), params)
        return original(session, context_ids)

    monkeypatch.setattr(family_change_module, "_lock_contexts", _lock)
    return done


class TestLockOrderAndReread:
    def test_decision_locks_family_then_context_then_suggestion(self, db_session, factories):
        scene = _scene(db_session, factories)
        other = _active_family(db_session, title="Другая семья", unit_name="M2", actor_id=scene.user.id)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        seen = _lock_statements(
            db_session,
            lambda: assign_other_family(
                db_session, suggestion_id=sug.id, family_id=other.id, actor_id=scene.user.id
            ),
        )

        assert seen[:3] == [
            ("work_families", "SHARE"),
            ("catalog_contexts", "UPDATE"),
            ("family_suggestions", "UPDATE"),
        ]

    def test_suggestion_unpublished_between_read_and_lock_conflicts(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        done = _change_before_context_lock(
            monkeypatch, db_session,
            "UPDATE family_suggestions SET is_published = false, "
            "unpublished_reason = 'stale_fingerprint' WHERE id = :id",
            {"id": sug.id},
        )

        with pytest.raises(DecisionConflict) as caught:
            reject_suggestion(db_session, suggestion_id=sug.id, actor_id=scene.user.id)

        assert done == [True]
        assert caught.value.code == "suggestion_changed"
        assert _fresh(db_session, FamilySuggestion, sug.id).decision is None

    def test_group_skips_a_suggestion_whose_family_changed_under_the_lock(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories)
        other = _active_family(db_session, title="Другая семья", unit_name="M2", actor_id=scene.user.id)
        sug = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        # Отпечаток прежний: обе семьи уже были кандидатами до публикации.
        done = _change_before_context_lock(
            monkeypatch, db_session,
            "UPDATE family_suggestions SET family_id = :f WHERE id = :id",
            {"f": other.id, "id": sug.id},
        )

        report = confirm_suggestions(db_session, suggestion_ids=[sug.id], actor_id=scene.user.id)

        assert done == [True]
        assert report == ConfirmReport(confirmed=[], skipped=[sug.id])
        assert _fresh(db_session, CatalogContext, scene.context_ids[0]).work_family_id is None

    def test_batch_decided_after_its_preview_is_reread_under_the_lock(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(db_session, scene)
        preview = preview_batch(db_session, batch_id=batch_id)
        # Вызывающий держит загруженную пачку (без ссылки объект ушёл бы из
        # карты идентичности, и перечитывание было бы неотличимо от первого чтения).
        held = db_session.get(SemanticReconcileBatch, batch_id)
        assert held.status == "held"
        # Второе решение закоммичено между preview и подтверждением этой сессии.
        db_session.execute(
            sa.text(
                "UPDATE semantic_reconcile_batches SET status = 'discarded', "
                "decided_by = :u, decided_at = now() WHERE id = :id"
            ),
            {"u": scene.user.id, "id": batch_id},
        )

        with pytest.raises(DecisionConflict) as caught:
            approve_batch(
                db_session, batch_id=batch_id, preview_hash=preview.preview_hash,
                actor_id=scene.user.id,
            )

        assert caught.value.code == "batch_decided"
        assert db_session.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 0


    def test_held_job_changed_after_it_was_loaded_is_reread_under_the_lock(
        self, db_session, factories
    ):
        scene = _hold_scene(db_session, factories)
        job = scene.jobs[0]
        assert job.status == "privacy_hold"
        db_session.execute(
            sa.text(
                "UPDATE semantic_jobs SET status = 'cancelled', cancel_reason = 'stale_hold' "
                "WHERE id = :id"
            ),
            {"id": job.id},
        )

        with pytest.raises(DecisionConflict) as caught:
            release_privacy_hold(
                db_session, job_id=job.id, shown_matches=_SHOWN, actor_id=scene.user.id
            )

        assert caught.value.code == "job_changed"

    def test_stop_lifted_after_the_state_was_loaded_is_reread_under_the_lock(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        other = factories.UserFactory.create()
        _pause(db_session, scene.context_ids[0])
        state = db_session.get(SemanticWorkerState, 1)
        assert state.claim_paused is True
        db_session.execute(
            sa.text(
                "UPDATE semantic_worker_state SET claim_paused = false, paused_reason = NULL, "
                "paused_attempt_id = NULL, paused_at = NULL, last_resumed_by = :u, "
                "last_resumed_at = now() WHERE id = 1"
            ),
            {"u": other.id},
        )

        resume_worker(db_session, actor_id=scene.user.id)

        assert _fresh(db_session, SemanticWorkerState, 1).last_resumed_by == other.id


# ---------------------------------------------------------------------------
#  Гонка двух решений — настоящие сессии
# ---------------------------------------------------------------------------

_RACE_JOIN_TIMEOUT = 30.0


def _race_two_decisions(committing_session_factory, decide):
    """Первое решение держит блокировку незакоммиченным, второе стартует в
    другой сессии и обязано дождаться его commit. Возвращает исход второго:
    `"ok"`, код `DecisionConflict` или `repr` иного исключения."""
    outcome: dict[str, str] = {}

    def _second() -> None:
        db = committing_session_factory()
        try:
            # Ожидание замка сверх срока — ошибка, а не зависший прогон.
            db.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
            decide(db, "second")
            db.commit()
            outcome["second"] = "ok"
        except DecisionConflict as exc:
            db.rollback()
            outcome["second"] = exc.code
        except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
            db.rollback()
            outcome["second"] = repr(exc)
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

    first = committing_session_factory()
    second = threading.Thread(target=_second, name="second-decision")
    try:
        decide(first, "first")
        second.start()
        deadline = time.monotonic() + 15
        while second.is_alive() and _waiting() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        first.commit()
    finally:
        first.rollback()
        first.close()
        if second.is_alive():
            second.join(timeout=_RACE_JOIN_TIMEOUT)

    assert not second.is_alive(), "второе решение зависло за отведённый таймаут"
    return outcome.get("second")


class TestDecisionRace:
    def test_second_approval_of_a_batch_waits_and_gets_batch_decided(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _scene(committing_db, committing_factories, titles=("Пол А", "Пол Б"))
        batch_id = _held_batch(committing_db, scene)
        committing_db.commit()
        preview_hash = preview_batch(committing_db, batch_id=batch_id).preview_hash
        committing_db.rollback()
        user_id = scene.user.id

        second = _race_two_decisions(
            committing_session_factory,
            lambda db, _who: approve_batch(
                db, batch_id=batch_id, preview_hash=preview_hash, actor_id=user_id
            ),
        )

        assert second == "batch_decided"
        with committing_session_factory() as check:
            assert check.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one() == 2
            batch = check.get(SemanticReconcileBatch, batch_id)
            assert (batch.status, batch.decided_by) == ("approved", user_id)

    def test_second_release_of_a_held_job_waits_and_gets_job_changed(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _hold_scene(committing_db, committing_factories)
        other = committing_factories.UserFactory.create()
        committing_db.commit()
        job_id, actors = scene.jobs[0].id, {"first": scene.user.id, "second": other.id}

        second = _race_two_decisions(
            committing_session_factory,
            lambda db, who: release_privacy_hold(
                db, job_id=job_id, shown_matches=_SHOWN, actor_id=actors[who]
            ),
        )

        assert second == "job_changed"
        with committing_session_factory() as check:
            job = check.get(SemanticJob, job_id)
            assert (job.status, job.privacy_decided_by) == ("pending", scene.user.id)

    def test_second_retry_of_an_error_job_waits_and_gets_job_changed(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _scene(committing_db, committing_factories)
        job = _make_job(committing_db, scene.context_ids[0], status="error", last_error_class="schema_error")
        committing_db.commit()
        job_id, user_id = job.id, scene.user.id

        second = _race_two_decisions(
            committing_session_factory,
            lambda db, _who: retry_job(db, job_id=job_id, actor_id=user_id),
        )

        assert second == "job_changed"
        with committing_session_factory() as check:
            assert check.get(SemanticJob, job_id).retry_generation == 1

    def test_second_resume_waits_and_writes_nothing(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _scene(committing_db, committing_factories)
        other = committing_factories.UserFactory.create()
        _pause(committing_db, scene.context_ids[0])
        committing_db.commit()
        actors = {"first": scene.user.id, "second": other.id}

        second = _race_two_decisions(
            committing_session_factory,
            lambda db, who: resume_worker(db, actor_id=actors[who]),
        )

        assert second == "ok"
        with committing_session_factory() as check:
            state = check.get(SemanticWorkerState, 1)
            assert (state.claim_paused, state.last_resumed_by) == (False, scene.user.id)


# ---------------------------------------------------------------------------
#  Гонка снимка preview с параллельным импортом
# ---------------------------------------------------------------------------

def _concurrent_input_change(session_factory, scene, context_ids):
    """Другой сеанс между проверкой `preview_hash` и сверкой: новая активная
    семья единицы (отпечатки контекстов меняются) и сверка, ставящая задания на
    НОВЫЕ отпечатки, — как параллельный импорт; всё закоммичено."""
    other = session_factory()
    try:
        other.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
        _active_family(other, title="Семья из параллельного импорта", unit_name=scene.unit, actor_id=scene.user_id)
        reconcile_semantic_jobs(other, context_ids, cap=NO_CAP, source="import")
        other.commit()
    finally:
        other.close()


def _after_hash_check(monkeypatch, action):
    """Вклинивание ровно после расчёта оценки, которой проверяется хэш."""
    real = decisions._preview_and_pairs
    fired: list[bool] = []

    def _wrapped(db, context_ids, **kwargs):
        result = real(db, context_ids, **kwargs)
        if not fired:
            fired.append(True)
            action()
        return result

    monkeypatch.setattr(decisions, "_preview_and_pairs", _wrapped)
    return fired


def _live_jobs(session_factory, context_id):
    with session_factory() as check:
        rows = check.execute(
            sa.select(SemanticJob.request_hash, SemanticJob.status, SemanticJob.cancel_reason)
            .where(SemanticJob.context_id == context_id)
            .order_by(SemanticJob.id)
        ).all()
    return rows


class TestPreviewSnapshotRace:
    def _scene(self, committing_db, committing_factories):
        scene = _scene(committing_db, committing_factories, titles=("Пол А", "Пол Б"))
        scene.user_id = scene.user.id
        committing_db.commit()
        return scene

    @pytest.mark.parametrize("kind", ["unit", "config", "batch"])
    def test_job_created_after_the_hash_check_survives_the_reconcile(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch, kind
    ):
        scene = self._scene(committing_db, committing_factories)
        if kind == "batch":
            batch_id = _held_batch(committing_db, scene)
            committing_db.commit()
            preview = preview_batch(committing_db, batch_id=batch_id)
        elif kind == "unit":
            preview = preview_unit_reask(committing_db, unit_id=scene.unit_id)
        else:
            preview = preview_config_reask(committing_db)
        committing_db.rollback()
        fired = _after_hash_check(
            monkeypatch,
            lambda: _concurrent_input_change(committing_session_factory, scene, scene.context_ids),
        )

        if kind == "batch":
            approve_batch(
                committing_db, batch_id=batch_id, preview_hash=preview.preview_hash,
                actor_id=scene.user_id,
            )
        elif kind == "unit":
            confirm_unit_reask(
                committing_db, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
                actor_id=scene.user_id,
            )
        else:
            confirm_config_reask(
                committing_db, preview_hash=preview.preview_hash, actor_id=scene.user_id
            )
        committing_db.commit()

        assert fired, "вход теста: вклинивание сработало"
        for context_id in scene.context_ids:
            with committing_session_factory() as check:
                current = _rendered(check, context_id).request_hash
            jobs = _live_jobs(committing_session_factory, context_id)
            assert [(h, st) for h, st, _ in jobs if st == "pending"] == [(current, "pending")]
            assert all(r[0] == current or r[1] == "cancelled" for r in jobs)

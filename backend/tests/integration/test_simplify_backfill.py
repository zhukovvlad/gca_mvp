"""Первый прогон упрощения каталога на накопленных данных: команда
`catalog-simplify-backfill` и сервис `services/simplify_backfill.py` (спека упрощения
каталога §2.12).

Шаг 1 — склейки в списках становятся составными на месте (без модели); шаг 2 —
контексты «к делению» получают задание `context_values` заново. Эталоны — независимые
литералы: тексты, ключи, нормы и числа записаны руками. Значения условные.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
import services.semantic_reconcile as reconcile_module
from config import settings as app_settings
from models import (
    CatalogAutoAction,
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterValue,
    FamilyValuePart,
    SemanticJob,
    SemanticJobKind,
    WorkVariantValue,
)
from services.human_decisions import add_veto
from services.releasable import load_releasable_material, render_releasable_material
from services.semantic_cost import reserve_for, tariffs_from
from services.semantic_worker import render_job_request
from services.simplify_backfill import (
    backfill_family_values,
    backfill_path_conflicts,
    families_for_backfill,
)
from services.work_variants import merge_parameter_values
from tests.integration import test_simplify_auto_split as split
from tests.integration.test_simplify_composite import (
    _context_value,
    _journal,
    _make,
    _parts_of,
    _put_row,
    _revision,
    _row,
    _scene,
    _variant_for,
)
from tests.integration.test_work_variants_worker import S

pytestmark = pytest.mark.integration


@contextmanager
def _statements(db):
    seen: list[str] = []

    def _tick(_conn, _cursor, statement, *_args, **_kwargs):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _tick)
    try:
        yield seen
    finally:
        sa.event.remove(engine, "before_cursor_execute", _tick)


def _writes(seen):
    return [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def _run1(db, scene, *, apply=True):
    return backfill_family_values(db, scene.family.id, apply=apply)


def _state(db):
    """Всё, что шаг 1 может записать, одним сравнимым значением."""
    db.expire_all()
    values = db.execute(
        sa.select(
            FamilyParameterValue.id, FamilyParameterValue.value, FamilyParameterValue.value_norm,
            FamilyParameterValue.composite_kind, FamilyParameterValue.composite_key,
            FamilyParameterValue.merged_into_id,
        ).order_by(FamilyParameterValue.id)
    ).all()
    parts = db.execute(
        sa.select(FamilyValuePart.value_id, FamilyValuePart.part_value_id).order_by(
            FamilyValuePart.value_id, FamilyValuePart.part_value_id
        )
    ).all()
    rows = db.execute(
        sa.select(ContextParameterValue.context_id, ContextParameterValue.value_id).order_by(
            ContextParameterValue.context_id
        )
    ).all()
    actions = db.execute(sa.select(sa.func.count()).select_from(CatalogAutoAction)).scalar_one()
    return [tuple(v) for v in values], [tuple(p) for p in parts], [tuple(r) for r in rows], actions


# ---------------------------------------------------------------------------
#  Шаг 1: склейки в списках
# ---------------------------------------------------------------------------

class TestGlueBecomesComposite:
    def test_the_glue_is_converted_in_place_all(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        glue = scene.ids["плиты, балки"]
        a, b = scene.ids["плиты"], scene.ids["балки"]

        report = _run1(db_session, scene)

        row = _row(db_session, glue)
        assert report.converted == [glue]
        assert (row.id, row.composite_kind) == (glue, "all")
        assert row.composite_key == f"all:{a},{b}"
        assert row.value == "плиты + балки"
        assert row.value_norm == "плиты + балки"
        assert row.merged_into_id is None
        assert _parts_of(db_session, glue) == sorted([a, b])

    def test_the_glue_with_or_becomes_any(self, db_session, factories):
        scene = _scene(db_session, factories, ("Knauf П113", "Gyproc П1", "Knauf П113 или Gyproc П1"))
        glue = scene.ids["Knauf П113 или Gyproc П1"]

        _run1(db_session, scene)

        row = _row(db_session, glue)
        assert row.composite_kind == "any"
        assert row.composite_key == f"any:{scene.ids['Knauf П113']},{scene.ids['Gyproc П1']}"
        assert row.value == "Knauf П113 или Gyproc П1"

    def test_the_text_follows_the_parts_in_id_order_not_the_glue_order(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "балки, плиты"))

        _run1(db_session, scene)

        assert _row(db_session, scene.ids["балки, плиты"]).value == "плиты + балки"

    def test_the_parts_are_found_by_lemma(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плита, балка"))
        glue = scene.ids["плита, балка"]

        report = _run1(db_session, scene)

        assert report.converted == [glue]
        assert _parts_of(db_session, glue) == sorted([scene.ids["плиты"], scene.ids["балки"]])

    def test_contexts_and_variants_are_not_touched(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        glue = scene.ids["плиты, балки"]
        context_id = scene.world.context_id
        _put_row(db_session, scene, context_id, glue)
        variant_id = _variant_for(db_session, scene, context_id, glue)
        before = db_session.execute(
            sa.select(WorkVariantValue.variant_id, WorkVariantValue.value_id)
        ).all()

        _run1(db_session, scene)

        assert _context_value(db_session, scene, context_id) == glue
        db_session.expire_all()
        assert db_session.get(CatalogContext, context_id).work_variant_id == variant_id
        after = db_session.execute(
            sa.select(WorkVariantValue.variant_id, WorkVariantValue.value_id)
        ).all()
        assert sorted(map(tuple, after)) == sorted(map(tuple, before))

    def test_every_conversion_writes_one_composite_journal_record(self, db_session, factories):
        scene = _scene(
            db_session, factories, ("плиты", "балки", "перекрытия", "плиты, балки", "балки, перекрытия")
        )
        first, second = scene.ids["плиты, балки"], scene.ids["балки, перекрытия"]

        _run1(db_session, scene)

        records = [r for r in _journal(db_session, scene.family.id) if r.kind == "composite"]
        assert sorted(r.value_id for r in records) == sorted([first, second])
        by_value = {r.value_id: r for r in records}
        assert by_value[first].payload["text"] == "плиты, балки"
        assert by_value[first].payload["kind"] == "all"
        assert by_value[first].payload["part_ids"] == sorted(
            [scene.ids["плиты"], scene.ids["балки"]]
        )
        assert by_value[first].parameter_id == scene.parameter_id

    def test_the_schema_revision_grows(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        before = _revision(db_session, scene.schema.id)

        _run1(db_session, scene)

        assert _revision(db_session, scene.schema.id) > before

    def test_nothing_to_convert_leaves_the_revision_alone(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        before = _revision(db_session, scene.schema.id)

        report = _run1(db_session, scene)

        assert report.converted == []
        assert _revision(db_session, scene.schema.id) == before


class TestGlueMerges:
    def test_two_glues_of_one_composition_are_merged(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки", "балки и плиты"))
        first, second = scene.ids["плиты, балки"], scene.ids["балки и плиты"]
        context_id = scene.world.context_id
        _put_row(db_session, scene, context_id, second)

        report = _run1(db_session, scene)

        assert report.converted == [first]
        assert report.merged_into_composite == [second]
        assert _row(db_session, second).merged_into_id == first
        assert _row(db_session, first).composite_kind == "all"
        assert _context_value(db_session, scene, context_id) == first

    def test_a_glue_of_an_existing_composite_is_merged_into_it(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "балки, плиты"))
        glue = scene.ids["балки, плиты"]
        existing = _make(db_session, scene, "плиты", "балки")

        report = _run1(db_session, scene)

        assert report.converted == []
        assert report.merged_into_composite == [glue]
        row = _row(db_session, glue)
        assert row.merged_into_id == existing and row.composite_kind is None

    def test_a_glue_that_collapses_to_one_part_is_merged_into_that_part(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, плиты"))
        glue = scene.ids["плиты, плиты"]

        report = _run1(db_session, scene)

        assert report.collapsed == [glue]
        assert report.converted == []
        row = _row(db_session, glue)
        assert row.merged_into_id == scene.ids["плиты"] and row.composite_kind is None


class TestGlueStaysWhole:
    def test_parts_not_in_the_list_leave_the_value_whole_and_reported(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "ГО и ЧС"))
        glue = scene.ids["ГО и ЧС"]
        before = _state(db_session)

        report = _run1(db_session, scene)

        assert report.kept_whole == [glue]
        assert report.converted == []
        assert _state(db_session) == before

    def test_one_part_missing_from_the_list_is_enough(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, фундаменты"))
        glue = scene.ids["плиты, фундаменты"]

        report = _run1(db_session, scene)

        assert report.kept_whole == [glue]
        assert _row(db_session, glue).composite_kind is None

    def test_a_veto_on_the_composition_keeps_the_value_and_reports_it(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        glue = scene.ids["плиты, балки"]
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], value_id=glue, user_id=scene.actor,
        )
        before = _state(db_session)

        report = _run1(db_session, scene)

        assert report.under_veto == [glue]
        assert report.converted == []
        assert _state(db_session) == before

    def test_a_veto_of_the_other_kind_does_not_block(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        glue = scene.ids["плиты, балки"]
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="any",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], value_id=glue, user_id=scene.actor,
        )

        report = _run1(db_session, scene)

        assert report.converted == [glue] and report.under_veto == []

    def test_a_taken_text_keeps_the_original_text_and_is_reported(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки", "плиты + балки"))
        glue = scene.ids["плиты, балки"]

        report = _run1(db_session, scene)

        assert report.converted == [glue] and report.text_kept == [glue]
        row = _row(db_session, glue)
        assert row.composite_kind == "all"
        assert (row.value, row.value_norm) == ("плиты, балки", "плиты, балки")
        assert _row(db_session, scene.ids["плиты + балки"]).composite_kind is None

    def test_a_closed_parameter_is_converted_too(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == scene.parameter_id)
            .values(closed=True)
        )
        db_session.flush()
        glue = scene.ids["плиты, балки"]

        report = _run1(db_session, scene)

        assert report.converted == [glue]
        assert _row(db_session, glue).composite_kind == "all"

    def test_a_range_parameter_is_not_touched(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == scene.parameter_id)
            .values(
                value_kind="range", closed=True, range_from=Decimal("1"), range_to=Decimal("9"),
                range_step=Decimal("1"),
            )
        )
        db_session.flush()

        report = _run1(db_session, scene)

        assert report.converted == [] and report.kept_whole == []
        assert _row(db_session, scene.ids["плиты, балки"]).composite_kind is None

    def test_a_family_that_is_not_active_is_skipped(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        scene.family.status = "draft"
        db_session.flush()

        report = _run1(db_session, scene)

        assert report.skipped_families == [scene.family.id]
        assert _row(db_session, scene.ids["плиты, балки"]).composite_kind is None


class TestSystemMergeKeepsOrigin:
    def _origin(self, db, scene):
        db.expire_all()
        return db.get(FamilyParameter, scene.parameter_id).origin

    def test_a_backfill_merge_leaves_the_parameter_origin_alone(self, db_session, factories):
        scene = _scene(
            db_session, factories,
            ("плиты", "балки", "плиты, плиты", "плиты, балки", "балки и плиты"),
        )
        assert self._origin(db_session, scene) == "model"

        report = _run1(db_session, scene)

        assert report.collapsed and report.merged_into_composite
        assert self._origin(db_session, scene) == "model"

    def test_a_human_merge_still_makes_the_parameter_human(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "перекрытия"))
        assert self._origin(db_session, scene) == "model"

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id,
            source_value_id=scene.ids["перекрытия"], target_value_id=scene.ids["плиты"],
            actor_id=scene.actor,
        )

        assert self._origin(db_session, scene) == "human"


def _releasable_reserve(db, scene) -> Decimal:
    """Резерв задания «можно отпустить» по рендеру семьи сейчас, посчитанный напрямую."""
    material = load_releasable_material(db, scene.family.id, scene.schema.id)
    rendered = render_releasable_material(material, settings=app_settings)
    return reserve_for(
        db, rendered, tariffs_from(app_settings, SemanticJobKind.releasable_params),
        rendered.body["max_tokens"],
    )


def _releasable_jobs(db, schema_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob).where(
                SemanticJob.kind == SemanticJobKind.releasable_params.value,
                SemanticJob.schema_id == schema_id,
                SemanticJob.status == "pending",
            )
        ).scalars()
    )


class TestStepOnePaidJobs:
    def test_the_real_run_counts_the_releasable_job_it_queued_and_its_reserve(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        expected = _releasable_reserve(db_session, scene)
        before = {job.id for job in _releasable_jobs(db_session, scene.schema.id)}

        report = _run1(db_session, scene)

        queued = [j for j in _releasable_jobs(db_session, scene.schema.id) if j.id not in before]
        assert len(queued) == 1
        assert report.jobs_queued == 1
        assert report.reserve_usd == expected > Decimal("0")

    def test_dry_run_counts_the_same_without_writing(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))

        dry = _run1(db_session, scene, apply=False)
        real = _run1(db_session, scene)

        assert dry.jobs_queued == real.jobs_queued == 1
        assert dry.reserve_usd == real.reserve_usd > Decimal("0")

    def test_nothing_to_change_costs_nothing(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        report = _run1(db_session, scene)

        assert report.jobs_queued == 0 and report.reserve_usd == Decimal("0")

    def test_every_parameter_marked_means_no_releasable_job(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == scene.parameter_id)
            .values(releasable=True)
        )
        db_session.flush()

        report = _run1(db_session, scene)

        assert report.converted and report.jobs_queued == 0
        assert report.reserve_usd == Decimal("0")


class TestStepOneRerunAndDryRun:
    def test_the_second_run_changes_nothing(self, db_session, factories):
        scene = _scene(
            db_session, factories,
            ("плиты", "балки", "плиты, балки", "балки и плиты", "плиты, плиты", "ГО и ЧС"),
        )
        _run1(db_session, scene)
        after_first = _state(db_session)

        report = _run1(db_session, scene)

        assert _state(db_session) == after_first
        assert report.converted == [] and report.merged_into_composite == []
        assert report.collapsed == []
        assert report.kept_whole == [scene.ids["ГО и ЧС"]]

    def test_dry_run_writes_nothing_and_reports_what_the_real_run_does(
        self, db_session, factories
    ):
        scene = _scene(
            db_session, factories,
            ("плиты", "балки", "плиты, балки", "балки и плиты", "плиты, плиты", "ГО и ЧС"),
        )
        before = _state(db_session)

        with _statements(db_session) as seen:
            dry = _run1(db_session, scene, apply=False)

        assert seen and _writes(seen) == []
        assert _state(db_session) == before
        real = _run1(db_session, scene)
        assert dry == real
        assert real.converted and real.merged_into_composite and real.collapsed and real.kept_whole

    def test_families_are_scoped_by_the_option(self, db_session, factories):
        one = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))
        two = _scene(db_session, factories, ("плиты", "балки", "плиты, балки"))

        assert families_for_backfill(db_session, one.family.id) == [one.family.id]
        everyone = families_for_backfill(db_session, None)
        assert {one.family.id, two.family.id} <= set(everyone)
        assert families_for_backfill(db_session, 987654321) == []


# ---------------------------------------------------------------------------
#  Шаг 2: контексты «к делению»
# ---------------------------------------------------------------------------

def _values_jobs(db, context_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob)
            .where(
                SemanticJob.kind == SemanticJobKind.context_values.value,
                SemanticJob.context_id == context_id,
            )
            .order_by(SemanticJob.id)
        ).scalars()
    )


def _pending(db, context_id):
    return [job for job in _values_jobs(db, context_id) if job.status == "pending"]


def _auto_split_jobs(db):
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count()).select_from(SemanticJob).where(
            SemanticJob.kind == SemanticJobKind.auto_split.value
        )
    ).scalar_one()


def _marked(db, context_id):
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue.refresh_requested_at).where(
            ContextParameterValue.context_id == context_id
        )
    ).scalar_one()


def _run2(db, **kwargs):
    kwargs.setdefault("family_id", None)
    kwargs.setdefault("limit", None)
    kwargs.setdefault("apply", True)
    return backfill_path_conflicts(db, **kwargs)


class TestPathConflictContexts:
    def test_a_values_job_is_queued_and_the_row_is_marked(self, db_session, factories):
        scene = split._scene(db_session, factories)
        assert _pending(db_session, scene.ctx) == []

        report = _run2(db_session)

        assert report.queued == [scene.ctx]
        (job,) = _pending(db_session, scene.ctx)
        assert job.prompt_version == "2"
        assert _marked(db_session, scene.ctx) is not None
        assert report.jobs_created + report.jobs_revived == 1

    def test_the_command_does_not_queue_auto_split_itself(self, db_session, factories):
        scene = split._scene(db_session, factories)
        before = _auto_split_jobs(db_session)

        _run2(db_session)

        assert scene.ctx and _auto_split_jobs(db_session) == before

    def test_the_reserve_is_priced_by_the_values_tariffs(self, db_session, factories, monkeypatch):
        scene = split._scene(db_session, factories)
        monkeypatch.setattr(reconcile_module, "settings", S)

        report = _run2(db_session)

        (job,) = _pending(db_session, scene.ctx)
        rendered = render_job_request(db_session, job, settings=S).rendered
        expected = reserve_for(
            db_session, rendered, tariffs_from(S, SemanticJobKind.context_values),
            rendered.body["max_tokens"],
        )
        as_suggestion = reserve_for(
            db_session, rendered, tariffs_from(S, SemanticJobKind.family_suggestion),
            rendered.body["max_tokens"],
        )
        assert report.reserve_usd == expected
        assert expected != as_suggestion and expected > Decimal("0")

    def test_the_second_run_queues_no_duplicates(self, db_session, factories):
        scene = split._scene(db_session, factories)
        _run2(db_session)
        jobs_before = [job.id for job in _values_jobs(db_session, scene.ctx)]

        report = _run2(db_session)

        assert report.queued == [] and report.reserve_usd == Decimal("0")
        assert [job.id for job in _values_jobs(db_session, scene.ctx)] == jobs_before
        assert len(_pending(db_session, scene.ctx)) == 1

    @pytest.mark.parametrize("skip", ["archived", "blocked", "no_hint"])
    def test_a_context_that_is_not_to_be_split_is_left_alone(self, db_session, factories, skip):
        scene = split._scene(db_session, factories)
        context = db_session.get(CatalogContext, scene.ctx)
        now = dt.datetime.now(dt.UTC)
        if skip == "archived":
            context.archived_at = now
        elif skip == "blocked":
            context.auto_split_blocked_at = now
            context.auto_split_blocked_by = scene.actor
        else:
            context.variant_split_hint = None
        db_session.flush()

        report = _run2(db_session)

        assert report.queued == [] and _pending(db_session, scene.ctx) == []

    def test_the_limit_bounds_the_number_of_contexts(self, db_session, factories):
        one = split._scene(db_session, factories)
        two = split._scene(db_session, factories)

        report = _run2(db_session, limit=1)

        assert report.queued == [min(one.ctx, two.ctx)]
        assert _pending(db_session, max(one.ctx, two.ctx)) == []
        assert len(_pending(db_session, min(one.ctx, two.ctx))) == 1

    def test_the_family_option_scopes_the_contexts(self, db_session, factories):
        one = split._scene(db_session, factories)
        two = split._scene(db_session, factories)

        report = _run2(db_session, family_id=two.family.id)

        assert report.queued == [two.ctx]
        assert _pending(db_session, one.ctx) == []

    def test_dry_run_writes_nothing_and_prices_what_the_real_run_queues(
        self, db_session, factories
    ):
        scene = split._scene(db_session, factories)
        before = [(job.id, job.status) for job in _values_jobs(db_session, scene.ctx)]

        with _statements(db_session) as seen:
            dry = _run2(db_session, apply=False)

        assert seen and _writes(seen) == []
        assert [(job.id, job.status) for job in _values_jobs(db_session, scene.ctx)] == before
        assert _marked(db_session, scene.ctx) is None
        real = _run2(db_session)
        assert dry.queued == real.queued == [scene.ctx]
        assert dry.reserve_usd == real.reserve_usd > Decimal("0")


def _finish(db, job_id, *, prompt_version):
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.id == job_id)
        .values(status="done", prompt_version=prompt_version)
    )
    db.flush()
    db.expire_all()


class TestStepTwoAskedOnce:
    def test_a_context_answered_with_the_current_prompt_is_not_asked_again(
        self, db_session, factories
    ):
        scene = split._scene(db_session, factories)
        _run2(db_session)
        (job,) = _pending(db_session, scene.ctx)
        _finish(db_session, job.id, prompt_version="2")

        report = _run2(db_session)

        assert report.queued == [] and report.reserve_usd == Decimal("0")
        assert _pending(db_session, scene.ctx) == []

    def test_a_context_last_asked_with_the_old_prompt_is_queued(self, db_session, factories):
        scene = split._scene(db_session, factories)
        _run2(db_session)
        (job,) = _pending(db_session, scene.ctx)
        _finish(db_session, job.id, prompt_version="1")

        report = _run2(db_session)

        assert report.queued == [scene.ctx] and report.reserve_usd > Decimal("0")

    def test_a_revived_job_records_the_prompt_it_is_asked_with(self, db_session, factories):
        scene = split._scene(db_session, factories)
        _run2(db_session)
        (job,) = _pending(db_session, scene.ctx)
        _finish(db_session, job.id, prompt_version="1")

        _run2(db_session)

        (revived,) = _pending(db_session, scene.ctx)
        assert revived.id == job.id and revived.prompt_version == "2"
        # ответ получен, конфликт остался: второй запрос уже не нужен
        _finish(db_session, revived.id, prompt_version=revived.prompt_version)
        assert _run2(db_session).queued == []

    def test_dry_run_skips_the_answered_context_too(self, db_session, factories):
        scene = split._scene(db_session, factories)
        _run2(db_session)
        (job,) = _pending(db_session, scene.ctx)
        _finish(db_session, job.id, prompt_version="2")

        dry = _run2(db_session, apply=False)

        assert dry.queued == [] and dry.reserve_usd == Decimal("0")


# ---------------------------------------------------------------------------
#  Команда
# ---------------------------------------------------------------------------

class TestBackfillCommand:
    def _run(self, committing_session_factory, monkeypatch, *args):
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)
        return CliRunner().invoke(cli.cli, ["catalog-simplify-backfill", *args])

    def _seed(self, committing_db, committing_factories):
        scene = _scene(committing_db, committing_factories, ("плиты", "балки", "плиты, балки"))
        committing_db.commit()
        return scene

    def test_the_report_is_ascii_and_the_value_is_converted(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        scene = self._seed(committing_db, committing_factories)
        glue = scene.ids["плиты, балки"]

        result = self._run(committing_session_factory, monkeypatch)

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert result.output.isascii()
        assert "dry_run=0" in result.output
        assert "converted=1" in result.output
        assert f"converted_ids={glue}" in result.output
        assert "reserve_usd=" in result.output
        assert "step1 jobs_queued=1" in result.output
        assert _row(committing_db, glue).composite_kind == "all"

    def test_dry_run_prints_the_same_numbers_and_writes_nothing(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        scene = self._seed(committing_db, committing_factories)
        glue = scene.ids["плиты, балки"]
        before = _state(committing_db)

        with _statements(committing_db) as seen:
            result = self._run(committing_session_factory, monkeypatch, "--dry-run")

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert result.output.isascii() and "dry_run=1" in result.output
        assert "converted=1" in result.output and f"converted_ids={glue}" in result.output
        assert seen and _writes(seen) == []
        assert _state(committing_db) == before

    def test_the_second_run_converts_nothing(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        self._seed(committing_db, committing_factories)
        self._run(committing_session_factory, monkeypatch)

        result = self._run(committing_session_factory, monkeypatch)

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "converted=0" in result.output

    def test_the_family_option_limits_both_steps(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        scene = self._seed(committing_db, committing_factories)
        other = _scene(committing_db, committing_factories, ("плиты", "балки", "плиты, балки"))
        committing_db.commit()

        result = self._run(
            committing_session_factory, monkeypatch, "--family", str(scene.family.id)
        )

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert _row(committing_db, scene.ids["плиты, балки"]).composite_kind == "all"
        assert _row(committing_db, other.ids["плиты, балки"]).composite_kind is None

    def test_command_checks_the_mutation_guard_first(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        reached = []

        def _record():
            reached.append(True)
            raise RuntimeError("stop-here")

        monkeypatch.setattr(cli, "SessionLocal", _record)
        CliRunner().invoke(cli.cli, ["catalog-simplify-backfill"])

        assert reached

    def test_the_command_refuses_an_unlisted_target_before_the_session(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.setenv("DB_EXTRA_TARGETS", "")
        monkeypatch.setattr(
            cli.settings, "DATABASE_URL",
            "postgresql+psycopg://test_owner:secret-pw@ep-example-0000.c-3.eu-central-1."
            "aws.neon.tech/neondb",
            raising=False,
        )

        def _explode():
            raise AssertionError("SessionLocal() called after the guard")

        monkeypatch.setattr(cli, "SessionLocal", _explode)
        result = CliRunner().invoke(cli.cli, ["catalog-simplify-backfill", "--dry-run"])

        assert isinstance(result.exception, RuntimeError)
        assert "APP_ENV=dev" in str(result.exception)


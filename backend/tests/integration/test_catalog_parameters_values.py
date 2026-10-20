"""Частичный запрос значений: контексту спрашивают только нужные параметры, известные
значения остаются (спека 3в §2.4, §2.6, §2.7).

Что нужно контексту (`values_need`), план по паре «что спросить / нужен ли вариант»,
завершение без модели, воспроизведение рендера по заданию, запись частичного
ответа, волна расширения и оценки стоимости. Строки значений, `closed`, вид
параметра и `refresh_requested_at` выставляются прямо в базе: сервис правки схемы
появляется позже, а частичный запрос от него не зависит. Путь опросчика —
настоящий: `claim_next` и `record_result` с ответом модели, как в наборе
исполнителя.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from crud.work_variants import _estimate, rebuild_preview
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    WorkVariant,
)
from services.semantic_answer import AnswerSchemaError
from services.semantic_client import ModelResponse
from services.semantic_decisions import _mark_batch_jobs
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    ValuesNeed,
    _estimate_totals,
    _plan_all,
    estimate_enqueue,
    held_fingerprints,
    reconcile_context_values,
    schedule_extension_wave,
    values_need,
    without_discarded_values,
)
from services.variant_request import (
    load_schema_material,
    load_values_material,
    paths_hash_of,
    render_request_for,
    render_schema_request,
    render_values_request,
)
from services.work_variants import JobGuard, apply_values
from tests.integration.test_semantic_queue_schema import _batch
from tests.integration.test_work_variants_core import (
    _answer,
    _events,
    _named,
    _set_pending,
    _table_rows,
)
from tests.integration.test_work_variants_material import _settings, _uid
from tests.integration.test_work_variants_schema import _param, _schema, _value, _variant
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _paths_hash,
    _second_context,
    _settle_context,
)

pytestmark = pytest.mark.integration

#: Позже любого `now()` базы: задание, созданное в тесте, уже «созрело».
NOW = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.UTC)

S = app_settings.model_copy(
    update={
        "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
    }
)

_DASH = "–"

_PARAMS = (
    (1, "Толщина", ["50 мм", "100 мм"]),
    (2, "Материал", ["бетон", "кирпич"]),
)
_THREE = (
    (1, "Толщина", ["50 мм", "100 мм"]),
    (2, "Материал", ["бетон", "кирпич"]),
    (3, "Класс", ["В15", "В25"]),
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _settle(db, world, context_id=None, thickness="50 мм", material="бетон"):
    """Контекст получает вариант и строки обоих параметров настоящим обработчиком."""
    return _settle_context(
        db, world, context_id or world.context_id,
        _answer(_named(1, thickness), _named(2, material, "path")),
    )


def _rows(db, context_id) -> dict:
    """`{ordinal: строка}` значений контекста."""
    db.expire_all()
    return {
        row.ordinal: row
        for row in db.execute(
            sa.select(
                FamilyParameter.ordinal, ContextParameterValue.schema_id,
                ContextParameterValue.value_id, ContextParameterValue.source,
                ContextParameterValue.off_list_text, ContextParameterValue.refresh_requested_at,
                ContextParameterValue.job_id, ContextParameterValue.created_at,
            )
            .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
            .where(ContextParameterValue.context_id == context_id)
        ).all()
    }


def _refresh_mark(db, context_id, parameter_id):
    """`refresh_requested_at` строки параметра у контекста (по id параметра:
    номера разных версий совпадают)."""
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue.refresh_requested_at).where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
    ).scalar_one()


def _put_row(db, context_id, schema_id, parameter_id, value_id, *, source="name", refresh=False,
             off_list_text=None):
    db.add(
        ContextParameterValue(
            context_id=context_id, schema_id=schema_id, parameter_id=parameter_id,
            value_id=value_id, source=source, off_list_text=off_list_text,
            refresh_requested_at=_now() if refresh else None,
        )
    )
    db.flush()


def _drop_row(db, context_id, parameter_id):
    db.execute(
        sa.delete(ContextParameterValue).where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
    )
    db.flush()


def _mark(db, context_id, parameter_id):
    db.execute(
        sa.update(ContextParameterValue)
        .where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
        .values(refresh_requested_at=sa.func.now())
    )
    db.flush()


def _update_parameter(db, parameter_id, **values):
    db.execute(
        sa.update(FamilyParameter).where(FamilyParameter.id == parameter_id).values(**values)
    )
    db.flush()
    db.expire_all()


def _context(db, context_id) -> CatalogContext:
    db.expire_all()
    return db.get(CatalogContext, context_id)


def _need(db, world, context_id=None, schema_id=None) -> tuple[tuple[int, ...], bool]:
    need = values_need(db, context_id or world.context_id, schema_id or world.schema.id)
    return need.ordinals, need.variant_due


def _reconcile(db, context_ids):
    return reconcile_context_values(db, context_ids, cap=NO_CAP, source="operation")


def _values_jobs(db, context_id) -> list[SemanticJob]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob)
            .where(SemanticJob.kind == "context_values", SemanticJob.context_id == context_id)
            .order_by(SemanticJob.id)
        )
        .scalars()
        .all()
    )


def _hash(db, context_id, ordinals=None, *, settings=app_settings) -> str:
    material = load_values_material(db, [context_id], requested_ordinals=ordinals)[context_id]
    return render_values_request(material, settings=settings).request_hash


def _make_range(db, world, ordinal, values):
    """Параметр `ordinal` — диапазон 120..200 с шагом 40: значения записываются
    литералами `(подпись, нижняя, верхняя)`, существующие значения параметра
    удаляются (строк контекстов на них нет)."""
    parameter_id = world.parameters[ordinal]
    db.execute(
        sa.delete(FamilyParameterValue).where(FamilyParameterValue.parameter_id == parameter_id)
    )
    _update_parameter(
        db, parameter_id, value_kind="range", closed=True, range_from=Decimal(120),
        range_to=Decimal(200), range_step=Decimal(40),
    )
    ids = {}
    for label, lo, hi in values:
        row = FamilyParameterValue(
            parameter_id=parameter_id, value=label, value_norm=label.lower(), origin="manual",
            range_lo=lo, range_hi=hi,
        )
        db.add(row)
        db.flush()
        ids[label] = row.id
    return ids


_RANGE_VALUES = (
    ("до 120", None, Decimal(120)),
    (f"120{_DASH}160", Decimal(120), Decimal(160)),
    (f"160{_DASH}200", Decimal(160), Decimal(200)),
    ("200 и более", Decimal(200), None),
)


def _job_for(db, world, context_id, ordinals, *, status="pending", settings=app_settings):
    """Задание значений контекста с отпечатком РЕНДЕРА по `ordinals`; номера в
    задании — `NULL`, когда они равны всем активным параметрам."""
    material = load_values_material(db, [context_id], requested_ordinals=ordinals)[context_id]
    rendered = render_values_request(material, settings=settings)
    active = sorted(p.ordinal for p in material.parameters)
    stored = None if sorted(ordinals) == active else sorted(ordinals)
    token = uuid.uuid4() if status == "running" else None
    job = SemanticJob(
        kind="context_values", context_id=context_id, schema_id=world.schema.id,
        paths_hash=paths_hash_of(material.paths), requested_ordinals=stored,
        request_hash=rendered.request_hash, status=status, claim_token=token,
        prompt_version="1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job, JobGuard(
        job_id=job.id, claim_token=token or uuid.uuid4(), expected_request_hash=rendered.request_hash
    )


def _apply(db, world, context_id, answer, *, ordinals=None, guard=None, schema_id=None):
    return apply_values(
        db, context_id=context_id, schema_id=schema_id or world.schema.id, answer=answer,
        paths_hash=_paths_hash(db, context_id), guard=guard, settings=_settings(),
        requested_ordinals=ordinals,
    )


def _foreign_variant(db, factories, world):
    """Вариант прежней версии с хэшем путей, равным нынешним (контекст после
    пересборки, чьи строки перенесены)."""
    old = _schema(db, factories, world.family, version=7, status="superseded")
    context = _context(db, world.context_id)
    context.work_variant_id = _variant(db, world.family, old).id
    context.variant_at = _now()
    context.variant_paths_hash = _paths_hash(db, world.context_id)
    db.flush()


def _other_family_world(db, factories, params=_PARAMS):
    """Вторая активная семья со своей текущей схемой и контекстом."""
    return _active_world(db, factories, params)


def _response(content):
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=8274, completion_tokens=156, cache_write_tokens=8058, cached_tokens=0,
        cost_usd=Decimal("0.01"),
    )


# ---------------------------------------------------------------------------
#  values_need: что нужно контексту
# ---------------------------------------------------------------------------

class TestValuesNeed:
    def test_changed_paths_ask_every_active_parameter(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _context(db_session, world.context_id).variant_paths_hash = "other-paths"
        db_session.flush()

        assert _need(db_session, world) == ((1, 2), False)

    def test_matching_paths_and_a_full_set_of_rows_need_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)

        assert _need(db_session, world) == ((), False)

    def test_a_missing_row_asks_only_its_parameter(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _drop_row(db_session, world.context_id, world.parameters[2])

        assert _need(db_session, world) == ((2,), False)

    def test_a_marked_row_asks_only_its_parameter(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[1])

        assert _need(db_session, world) == ((1,), False)

    def test_an_unmarked_row_with_no_value_is_known_and_not_asked_again(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), (2, "none", None, None)),
        )

        assert _need(db_session, world) == ((), False)

    def test_an_archived_parameter_is_never_asked_even_without_a_row(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _drop_row(db_session, world.context_id, world.parameters[2])
        _update_parameter(db_session, world.parameters[2], archived_at=sa.func.now())

        assert _need(db_session, world) == ((), False)

    def test_an_archived_parameter_is_not_asked_when_paths_changed_either(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _update_parameter(db_session, world.parameters[2], archived_at=sa.func.now())
        _context(db_session, world.context_id).variant_paths_hash = "other-paths"
        db_session.flush()

        assert _need(db_session, world) == ((1,), False)

    def test_the_role_of_a_parameter_does_not_change_what_is_asked(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _update_parameter(db_session, world.parameters[2], role="secondary")
        _drop_row(db_session, world.context_id, world.parameters[2])

        assert _need(db_session, world) == ((2,), False)

    def test_rows_of_another_version_do_not_count_as_known(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _drop_row(db_session, world.context_id, world.parameters[2])
        old = _schema(db_session, factories, world.family, version=7, status="superseded")
        old_parameter = _param(db_session, old, 2, "Материал")
        old_value = _value(db_session, old_parameter, "бетон")
        _put_row(db_session, world.context_id, old.id, old_parameter.id, old_value.id)

        assert _need(db_session, world) == ((2,), False)

    def test_pending_family_with_every_row_of_its_version_needs_only_a_variant(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, target.schema.id, target.parameters[ordinal],
                target.values[(ordinal, _PARAMS[ordinal - 1][2][0])],
            )

        # Пути варианта прежней семьи равны нынешним, строки новой версии есть.
        assert _need(db_session, world, schema_id=target.schema.id) == ((), True)

    def test_pending_family_without_rows_of_its_version_asks_every_parameter(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)

        assert _need(db_session, world, schema_id=target.schema.id) == ((1, 2), True)

    def test_a_context_without_a_variant_has_an_empty_paths_hash_and_asks_everything(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, world.schema.id, world.parameters[ordinal],
                world.values[(ordinal, _PARAMS[ordinal - 1][2][0])],
            )

        assert _need(db_session, world) == ((1, 2), True)

    def test_a_context_without_a_variant_and_without_rows_asks_everything(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)

        assert _need(db_session, world) == ((1, 2), True)

    def test_a_variant_of_another_version_makes_a_variant_due(self, db_session, factories):
        world = _active_world(db_session, factories)
        old = _schema(db_session, factories, world.family, version=7, status="superseded")
        old_variant = _variant(db_session, world.family, old)
        context = _context(db_session, world.context_id)
        context.work_variant_id = old_variant.id
        context.variant_at = _now()
        context.variant_paths_hash = _paths_hash(db_session, world.context_id)
        db_session.flush()
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, world.schema.id, world.parameters[ordinal],
                world.values[(ordinal, _PARAMS[ordinal - 1][2][0])],
            )

        assert _need(db_session, world) == ((), True)

    def test_a_schema_without_parameters_needs_a_variant_until_it_is_assigned(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, params=())
        assert _need(db_session, world) == ((), True)

        _settle_context(db_session, world, world.context_id, _answer())

        assert _need(db_session, world) == ((), False)

    def test_a_version_that_is_not_current_for_the_context_asks_nothing(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        assert _need(db_session, world) == ((1, 2), True)

        old = _schema(db_session, factories, world.family, version=7, status="superseded")

        assert _need(db_session, world, schema_id=old.id) == ((), False)

    def test_need_is_a_pair_of_the_documented_fields(self, db_session, factories):
        world = _active_world(db_session, factories)
        need = values_need(db_session, world.context_id, world.schema.id)
        assert need == ValuesNeed(ordinals=(1, 2), variant_due=True)

    def test_a_context_that_does_not_exist_asks_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)
        assert values_need(db_session, 987654321, world.schema.id) == ValuesNeed((), False)


# ---------------------------------------------------------------------------
#  План: пара «что спросить / нужен ли вариант»
# ---------------------------------------------------------------------------

class TestPlanTable:
    def test_a_missing_parameter_is_posted_as_a_partial_job_with_its_ordinals(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[1])
        _drop_row(db_session, world.context_id, world.parameters[3])

        report = _reconcile(db_session, [world.context_id])

        [job] = _values_jobs(db_session, world.context_id)
        assert report.created == 1 and job.status == "pending"
        assert job.requested_ordinals == [1, 3]
        assert job.request_hash == _hash(db_session, world.context_id, [1, 3])
        assert job.request_hash != _hash(db_session, world.context_id)

    def test_every_active_parameter_needed_is_stored_as_null(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _context(db_session, world.context_id).variant_paths_hash = "other-paths"
        db_session.flush()

        _reconcile(db_session, [world.context_id])

        [job] = _values_jobs(db_session, world.context_id)
        assert job.requested_ordinals is None
        assert job.request_hash == _hash(db_session, world.context_id)

    def test_a_single_missing_parameter_of_a_one_parameter_schema_is_null_too(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, ((1, "Толщина", ["50 мм", "100 мм"]),))
        _settle_context(db_session, world, world.context_id, _answer(_named(1, "50 мм")))
        _drop_row(db_session, world.context_id, world.parameters[1])

        _reconcile(db_session, [world.context_id])

        [job] = _values_jobs(db_session, world.context_id)
        assert job.requested_ordinals is None

    def test_nothing_to_ask_and_a_variant_due_is_posted_as_a_free_job(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _foreign_variant(db_session, factories, world)
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, world.schema.id, world.parameters[ordinal],
                world.values[(ordinal, _PARAMS[ordinal - 1][2][0])],
            )

        # Отпечаток задания без модели помечен «бесплатным» в плане (стоит 0).
        plan = _plan_all(db_session, [world.context_id])
        [free] = plan.free_fingerprints()
        report = _reconcile(db_session, [world.context_id])

        [job] = _values_jobs(db_session, world.context_id)
        assert report.created == 1 and job.requested_ordinals is None
        assert job.request_hash == _hash(db_session, world.context_id)
        assert (free.kind, free.context_id, free.request_hash) == (
            SemanticJobKind.context_values, world.context_id, job.request_hash,
        )

    def test_nothing_to_ask_and_no_variant_due_posts_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)

        report = _reconcile(db_session, [world.context_id])

        assert report.created == 0
        assert _values_jobs(db_session, world.context_id) == []

    def test_a_partial_job_costs_less_than_the_full_one(self, db_session, factories):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[2])
        _, partial_reserve, _ = estimate_enqueue(db_session, [world.context_id])
        _context(db_session, world.context_id).variant_paths_hash = "other-paths"
        db_session.flush()

        _, full_reserve, _ = estimate_enqueue(db_session, [world.context_id])

        assert 0 < partial_reserve < full_reserve

    def test_a_second_reconcile_neither_cancels_nor_duplicates_a_partial_job(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[2])
        _reconcile(db_session, [world.context_id])
        [first] = _values_jobs(db_session, world.context_id)

        report = _reconcile(db_session, [world.context_id])

        [again] = _values_jobs(db_session, world.context_id)
        assert again.id == first.id and (again.status, again.cancel_reason) == ("pending", None)
        assert (report.created, report.cancelled) == (0, 0)

    def test_a_partial_job_that_stopped_being_current_is_cancelled_as_input_changed(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[2])
        _reconcile(db_session, [world.context_id])
        [first] = _values_jobs(db_session, world.context_id)
        _drop_row(db_session, world.context_id, world.parameters[3])

        _reconcile(db_session, [world.context_id])

        jobs = _values_jobs(db_session, world.context_id)
        assert [(j.status, j.requested_ordinals) for j in jobs] == [
            ("cancelled", [2]), ("pending", [2, 3]),
        ]
        assert jobs[0].id == first.id and jobs[0].cancel_reason == "input_changed"

    def test_the_posted_set_of_a_batch_marks_the_partial_job(self, db_session, factories):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[2])
        fingerprints = held_fingerprints(db_session, [world.context_id])
        assert [f.request_hash for f in fingerprints] == [_hash(db_session, world.context_id, [2])]
        _reconcile(db_session, [world.context_id])
        [job] = _values_jobs(db_session, world.context_id)
        batch = _batch(
            db_session, factories, held_fingerprints=[f.as_dict() for f in fingerprints]
        )

        _mark_batch_jobs(db_session, batch.id, fingerprints)

        [marked] = _values_jobs(db_session, world.context_id)
        assert (marked.id, marked.batch_id) == (job.id, batch.id)

    def test_a_discarded_partial_fingerprint_is_not_returned_but_a_full_one_is(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        _drop_row(db_session, world.context_id, world.parameters[2])
        discarded = Fingerprint(
            "context_values", world.context_id, None, world.schema.id,
            _hash(db_session, world.context_id, [2]),
        )
        _batch(
            db_session, factories, status="discarded", decided_by=factories.UserFactory.create().id,
            decided_at=_now(), held_fingerprints=[discarded.as_dict()],
            fingerprints_hash=f"fp-{_uid()}",
        )

        assert without_discarded_values(db_session, [world.context_id]) == []

        # Нужда изменилась (спрашивается другое подмножество): тот же контекст возвращается.
        _drop_row(db_session, world.context_id, world.parameters[3])
        assert without_discarded_values(db_session, [world.context_id]) == [world.context_id]


# ---------------------------------------------------------------------------
#  Завершение без модели
# ---------------------------------------------------------------------------

def _only_values_jobs(db):
    """Прежние шаги теста ставят и задания предложений: захват берёт первое
    готовое задание, поэтому остальных видов не остаётся."""
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.kind != "context_values", SemanticJob.status == "pending")
        .values(status="cancelled", cancel_reason="not_applicable")
    )
    db.flush()


def _claim_synthetic(db):
    _only_values_jobs(db)
    claim = worker.claim_next(db, settings=S, now=NOW)
    assert claim is not None, "захват не состоялся"
    assert claim.synthesized is True, "задание ушло бы модели"
    return claim


class TestCompletionWithoutModel:
    def test_a_new_row_of_a_family_without_parameters_gets_its_variant(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, params=())
        _reconcile(db_session, [world.context_id])
        claim = _claim_synthetic(db_session)

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        context = _context(db_session, world.context_id)
        variant = db_session.get(WorkVariant, context.work_variant_id)
        assert (variant.values_key, variant.schema_id) == ("", world.schema.id)
        assert context.variant_paths_hash == paths_hash_of(())
        [job] = _values_jobs(db_session, world.context_id)
        assert (job.status, job.claim_token) == ("done", None)
        assert db_session.execute(sa.select(sa.func.count(SemanticJobAttempt.id))).scalar_one() == 0
        [event] = _events(db_session, "context_variant_assigned", context_id=world.context_id)
        assert event.payload["answered_ordinals"] == [] and event.payload["kept_ordinals"] == []

    def test_known_rows_of_every_parameter_give_a_variant_without_the_model(
        self, db_session, factories
    ):
        """Контекст после пересборки без параметров модели: строки человека
        перенесены, варианта нет."""
        world = _active_world(db_session, factories)
        _foreign_variant(db_session, factories, world)
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, world.schema.id, world.parameters[ordinal],
                world.values[(ordinal, _PARAMS[ordinal - 1][2][0])], source="manual",
            )
        before = _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.context_id == world.context_id,
        )
        _reconcile(db_session, [world.context_id])
        claim = _claim_synthetic(db_session)

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        context = _context(db_session, world.context_id)
        variant = db_session.get(WorkVariant, context.work_variant_id)
        assert variant.values_key == (
            f"1={world.values[(1, '50 мм')]}|2={world.values[(2, 'бетон')]}"
        )
        assert context.variant_paths_hash == _paths_hash(db_session, world.context_id)
        assert _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.context_id == world.context_id,
        ) == before
        [event] = _events(db_session, "context_variant_assigned", context_id=world.context_id)
        assert event.payload["answered_ordinals"] == [] and event.payload["kept_ordinals"] == [1, 2]
        [job] = _values_jobs(db_session, world.context_id)
        assert job.status == "done"

    def test_the_pending_family_is_applied_and_the_row_is_promoted(self, db_session, factories):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)
        for ordinal in (1, 2):
            _put_row(
                db_session, world.context_id, target.schema.id, target.parameters[ordinal],
                target.values[(ordinal, _PARAMS[ordinal - 1][2][0])],
            )
        _reconcile(db_session, [world.context_id])
        claim = _claim_synthetic(db_session)

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        context = _context(db_session, world.context_id)
        assert (context.work_family_id, context.pending_family_id) == (target.family.id, None)
        assert db_session.get(WorkVariant, context.work_variant_id).schema_id == target.schema.id
        event = _events(db_session, "context_variant_assigned", context_id=world.context_id)[-1]
        assert event.payload["kept_ordinals"] == [1, 2]
        assert event.payload["to_variant_id"] == context.work_variant_id
        assert len(_events(db_session, "context_family_assigned", context_id=world.context_id)) == 1

    def test_a_job_with_something_to_ask_still_goes_to_the_model(self, db_session, factories):
        world = _active_world(db_session, factories)
        _reconcile(db_session, [world.context_id])

        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.synthesized is False and claim.rendered is not None

    def test_a_wave_job_for_an_explicit_subset_goes_to_the_model_even_with_rows_known(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        job, _guard = _job_for(db_session, world, world.context_id, [2])

        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.job_id == job.id and claim.synthesized is False

    def test_an_explicit_subset_goes_to_the_model_even_when_only_a_variant_is_due(
        self, db_session, factories
    ):
        """Строки всех параметров есть, вариант чужой версии (нужен вариант), а у
        задания явное подмножество: модель спрашивается — признак «без модели»
        требует `NULL` у задания, а не только пустой нужды."""
        world = _active_world(db_session, factories, _THREE)
        _foreign_variant(db_session, factories, world)
        for ordinal in (1, 2, 3):
            _put_row(
                db_session, world.context_id, world.schema.id, world.parameters[ordinal],
                world.values[(ordinal, _THREE[ordinal - 1][2][0])],
            )
        assert _need(db_session, world) == ((), True)
        job, _guard = _job_for(db_session, world, world.context_id, [2])
        assert job.requested_ordinals == [2]

        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.job_id == job.id and claim.synthesized is False

    def test_a_job_of_a_schema_without_parameters_never_goes_to_the_model(
        self, db_session, factories
    ):
        """Схема без активных параметров: даже когда вариант уже назначен (нужды и
        назначения нет), задание завершается без модели — спрашивать нечего."""
        world = _active_world(db_session, factories, params=())
        _settle_context(db_session, world, world.context_id, _answer())
        assert _need(db_session, world) == ((), False)
        job, _guard = _job_for(db_session, world, world.context_id, [])

        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.job_id == job.id and claim.synthesized is True


# ---------------------------------------------------------------------------
#  Рендер по заданию
# ---------------------------------------------------------------------------

class TestJobRender:
    def test_render_request_for_reproduces_the_hash_of_a_partial_job(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        job, _guard = _job_for(db_session, world, world.context_id, [2])
        assert job.requested_ordinals == [2]

        rendered = render_request_for(db_session, job, settings=app_settings)

        assert rendered.request_hash == job.request_hash
        assert rendered.request_hash != _hash(db_session, world.context_id)

    def test_render_job_request_reproduces_it_without_a_cancel_reason(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        job, _guard = _job_for(db_session, world, world.context_id, [2])

        current = worker.render_job_request(db_session, job, settings=app_settings)

        assert current.cancel_reason is None
        assert current.rendered.request_hash == job.request_hash
        assert current.without_model is False

    def test_a_null_subset_renders_every_active_parameter(self, db_session, factories):
        world = _active_world(db_session, factories)
        job, _guard = _job_for(db_session, world, world.context_id, [1, 2])
        assert job.requested_ordinals is None

        rendered = render_request_for(db_session, job, settings=app_settings)

        assert rendered.request_hash == job.request_hash == _hash(db_session, world.context_id)

    def test_the_block_of_a_partial_request_names_only_the_requested_parameter(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        _reconcile(db_session, [world.context_id])

        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.rendered is not None
        block = claim.rendered.body["messages"][0]["content"][1]["text"]
        assert "Параметр 2: Материал" in block and "Параметр 1" not in block
        schema = claim.rendered.body["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["values"]["items"]["properties"]["ordinal"]["enum"] == [2]

    def test_the_role_of_a_parameter_does_not_change_the_request(self, db_session, factories):
        world = _active_world(db_session, factories)
        before = _hash(db_session, world.context_id)

        _update_parameter(db_session, world.parameters[2], role="secondary")

        assert _hash(db_session, world.context_id) == before

    def test_an_archived_parameter_and_a_retired_value_are_not_rendered(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories, _THREE)
        _update_parameter(db_session, world.parameters[3], archived_at=sa.func.now())
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == world.values[(2, "кирпич")])
            .values(retired_at=sa.func.now())
        )
        db_session.flush()

        material = load_values_material(db_session, [world.context_id])[world.context_id]

        assert [p.ordinal for p in material.parameters] == [1, 2]
        assert [p.values for p in material.parameters] == [("50 мм", "100 мм"), ("бетон",)]
        block = render_values_request(material, settings=app_settings).body["messages"][0][
            "content"
        ][1]["text"]
        assert "Класс" not in block and "кирпич" not in block

    def test_a_range_parameter_is_marked_in_the_material_and_carries_the_rule(
        self, db_session, factories
    ):
        from services.parameter_ranges import RANGE_RULE_LINE

        world = _active_world(db_session, factories)
        _make_range(db_session, world, 1, _RANGE_VALUES)

        material = load_values_material(db_session, [world.context_id])[world.context_id]
        block = render_values_request(material, settings=app_settings).body["messages"][0][
            "content"
        ][1]["text"]

        assert [p.value_kind for p in material.parameters] == ["range", "exact"]
        assert block.count(RANGE_RULE_LINE) == 1
        assert block.index("Параметр 1") < block.index(RANGE_RULE_LINE) < block.index("Параметр 2")

    def test_editing_a_requested_parameter_changes_the_hash_but_another_one_does_not(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        before = _hash(db_session, world.context_id, [2])

        _value(db_session, db_session.get(FamilyParameter, world.parameters[1]), "150 мм")
        assert _hash(db_session, world.context_id, [2]) == before

        _value(db_session, db_session.get(FamilyParameter, world.parameters[2]), "дерево")
        assert _hash(db_session, world.context_id, [2]) != before


# ---------------------------------------------------------------------------
#  Запись частичного ответа
# ---------------------------------------------------------------------------

class TestApplyPartial:
    def test_rows_of_unrequested_parameters_keep_every_field(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        kept_before = _rows(db_session, world.context_id)[1]

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.applied is True
        assert rows[1] == kept_before
        assert rows[2].value_id == world.values[(2, "кирпич")]
        assert rows[2].refresh_requested_at is None

    def test_the_refresh_mark_is_cleared_only_for_the_requested_parameter(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[1])
        _mark(db_session, world.context_id, world.parameters[2])

        _apply(db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,))

        rows = _rows(db_session, world.context_id)
        assert rows[1].refresh_requested_at is not None
        assert rows[2].refresh_requested_at is None

    def test_the_variant_is_resolved_from_kept_and_new_rows_together(self, db_session, factories):
        world = _active_world(db_session, factories)
        first = _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        assert outcome.variant_id != first
        variant = db_session.get(WorkVariant, outcome.variant_id)
        assert variant.values_key == (
            f"1={world.values[(1, '50 мм')]}|2={world.values[(2, 'кирпич')]}"
        )
        [event] = _events(
            db_session, "context_variant_assigned", context_id=world.context_id
        )[-1:]
        assert event.payload["answered_ordinals"] == [2]
        assert event.payload["kept_ordinals"] == [1]
        assert set(event.payload["values"]) == {"2"}

    def test_rows_of_another_version_are_deleted_and_not_kept_as_known(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)
        old_schema_id = world.schema.id
        assert {row.schema_id for row in _rows(db_session, world.context_id).values()} == {
            old_schema_id
        }

        outcome = _apply(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон", "path")),
            schema_id=target.schema.id,
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.applied and outcome.family_switched
        assert {row.schema_id for row in rows.values()} == {target.schema.id}
        assert sorted(rows) == [1, 2]

    def test_rows_of_a_previous_version_of_the_same_family_are_deleted_by_the_write(
        self, db_session, factories
    ):
        """Прежняя версия той же семьи (после пересборки): семья не меняется, и
        строки прежней версии удаляет сама запись ответа, а не переключение
        ожидающей семьи."""
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        old = _schema(db_session, factories, world.family, version=7, status="superseded")
        old_parameter = _param(db_session, old, 3, "Класс")
        old_value = _value(db_session, old_parameter, "В15")
        _put_row(db_session, world.context_id, old.id, old_parameter.id, old_value.id)

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.applied is True and outcome.family_switched is False
        assert {row.schema_id for row in rows.values()} == {world.schema.id}
        assert sorted(rows) == [1, 2]

    def test_a_partial_answer_after_a_family_switch_keeps_only_rows_of_the_new_version(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)
        _put_row(
            db_session, world.context_id, target.schema.id, target.parameters[1],
            target.values[(1, "100 мм")],
        )

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")),
            ordinals=(2,), schema_id=target.schema.id,
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.family_switched is True
        assert {row.schema_id for row in rows.values()} == {target.schema.id}
        assert rows[1].value_id == target.values[(1, "100 мм")]
        assert rows[2].value_id == target.values[(2, "кирпич")]

    def test_a_new_value_of_an_open_parameter_extends_the_list(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[1])

        outcome = _apply(
            db_session, world, world.context_id,
            _answer((1, "new", "  75 мм ", "name")), ordinals=(1,),
        )

        origin = db_session.execute(
            sa.select(FamilyParameterValue.origin, FamilyParameterValue.value).where(
                FamilyParameterValue.id == _rows(db_session, world.context_id)[1].value_id
            )
        ).one()
        assert origin == ("extension", "75 мм")
        assert len(outcome.values_added) == 1

    def test_a_retired_value_named_as_new_returns_from_history(self, db_session, factories):
        """Открытый параметр: модель называет `new` текстом выведенного значения —
        значение возвращается из истории (тот же `id`, `retired_at` снят,
        `revision` +1, считается добавленным), строка ссылается на него."""
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        retired_id = world.values[(1, "100 мм")]
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == retired_id)
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        _mark(db_session, world.context_id, world.parameters[1])
        db_session.expire_all()
        revision_before = db_session.get(FamilyParameterSchema, world.schema.id).revision

        outcome = _apply(
            db_session, world, world.context_id,
            _answer((1, "new", "100 мм", "name")), ordinals=(1,),
        )

        row = _rows(db_session, world.context_id)[1]
        assert (row.value_id, row.source) == (retired_id, "name")
        assert db_session.get(FamilyParameterValue, retired_id).retired_at is None
        assert outcome.values_added == (retired_id,)
        assert db_session.get(FamilyParameterSchema, world.schema.id).revision == (
            revision_before + 1
        )

    def test_a_new_value_of_a_closed_parameter_is_an_off_list_row_and_the_list_stays(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _update_parameter(db_session, world.parameters[1], closed=True)
        _mark(db_session, world.context_id, world.parameters[1])
        values_before = _table_rows(
            db_session, FamilyParameterValue,
            FamilyParameterValue.parameter_id == world.parameters[1],
        )
        revision_before = db_session.get(FamilyParameterSchema, world.schema.id).revision

        outcome = _apply(
            db_session, world, world.context_id,
            _answer((1, "new", "  75 мм ", "path")), ordinals=(1,),
        )

        row = _rows(db_session, world.context_id)[1]
        assert (row.value_id, row.source, row.off_list_text) == (None, "off_list", "75 мм")
        assert outcome.values_added == ()
        assert _table_rows(
            db_session, FamilyParameterValue,
            FamilyParameterValue.parameter_id == world.parameters[1],
        ) == values_before
        db_session.expire_all()
        assert db_session.get(FamilyParameterSchema, world.schema.id).revision == revision_before

    @pytest.mark.parametrize(
        ("text", "label"),
        [
            ("130,5", f"120{_DASH}160"),
            ("131.73 кг/м3", f"120{_DASH}160"),
            ("160", f"160{_DASH}200"),
            ("119,99", "до 120"),
            ("200", "200 и более"),
            ("5 мм", "до 120"),
        ],
    )
    def test_a_number_for_a_range_parameter_is_assigned_to_its_range(
        self, db_session, factories, text, label
    ):
        world = _active_world(db_session, factories)
        ids = _make_range(db_session, world, 1, _RANGE_VALUES)
        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "none", None, None), _named(2, "бетон")),
        )
        _mark(db_session, world.context_id, world.parameters[1])

        _apply(db_session, world, world.context_id, _answer((1, "new", text, "path")), ordinals=(1,))

        row = _rows(db_session, world.context_id)[1]
        assert (row.value_id, row.source, row.off_list_text) == (ids[label], "path", None)

    @pytest.mark.parametrize("text", ["до 100 мм", "от 40 мм", "40-50", "1/2", "В25"])
    def test_a_text_that_is_not_one_number_for_a_range_parameter_is_off_list(
        self, db_session, factories, text
    ):
        world = _active_world(db_session, factories)
        _make_range(db_session, world, 1, _RANGE_VALUES)
        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "none", None, None), _named(2, "бетон")),
        )
        _mark(db_session, world.context_id, world.parameters[1])

        _apply(db_session, world, world.context_id, _answer((1, "new", text, "name")), ordinals=(1,))

        row = _rows(db_session, world.context_id)[1]
        assert (row.value_id, row.source, row.off_list_text) == (None, "off_list", text)

    def test_a_retired_range_value_is_not_a_target_of_a_number(self, db_session, factories):
        world = _active_world(db_session, factories)
        ids = _make_range(db_session, world, 1, _RANGE_VALUES)
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == ids[f"120{_DASH}160"])
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "none", None, None), _named(2, "бетон")),
        )
        _mark(db_session, world.context_id, world.parameters[1])

        _apply(
            db_session, world, world.context_id, _answer((1, "new", "130", "name")), ordinals=(1,)
        )

        row = _rows(db_session, world.context_id)[1]
        assert (row.value_id, row.source) == (None, "off_list")

    def test_a_retired_value_cannot_be_named_from_the_list(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == world.values[(1, "100 мм")])
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        _mark(db_session, world.context_id, world.parameters[1])
        before = _rows(db_session, world.context_id)

        with pytest.raises(AnswerSchemaError) as raised:
            _apply(
                db_session, world, world.context_id, _answer(_named(1, "100 мм")), ordinals=(1,)
            )

        assert raised.value.code == "value_not_in_list"
        assert _rows(db_session, world.context_id) == before

    def test_the_answer_must_cover_exactly_the_requested_parameters(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        before = _rows(db_session, world.context_id)

        with pytest.raises(AnswerSchemaError) as raised:
            _apply(
                db_session, world, world.context_id,
                _answer(_named(1, "100 мм"), _named(2, "кирпич")), ordinals=(2,),
            )

        assert raised.value.code == "bad_ordinal"
        assert _rows(db_session, world.context_id) == before

    def test_without_a_main_row_the_variant_and_the_pending_family_stay(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        first = _settle(db_session, world)
        _set_pending(db_session, factories, world.context_id, target.family)

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(1, "50 мм")),
            ordinals=(1,), schema_id=target.schema.id,
        )

        context = _context(db_session, world.context_id)
        assert outcome.applied is True and outcome.family_switched is False
        assert (context.work_variant_id, context.pending_family_id) == (first, target.family.id)
        assert context.work_family_id == world.family.id
        assert sorted(_rows(db_session, world.context_id)) == [1]

    def test_without_a_main_row_no_assignment_event_is_written(self, db_session, factories):
        world = _active_world(db_session, factories)
        target = _other_family_world(db_session, factories)
        _set_pending(db_session, factories, world.context_id, target.family)

        _apply(
            db_session, world, world.context_id, _answer(_named(1, "50 мм")),
            ordinals=(1,), schema_id=target.schema.id,
        )

        assert _events(db_session, "context_variant_assigned", context_id=world.context_id) == []
        assert _context(db_session, world.context_id).work_variant_id is None

    def test_a_main_row_still_marked_keeps_the_previous_variant(self, db_session, factories):
        world = _active_world(db_session, factories)
        first = _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[1])
        _mark(db_session, world.context_id, world.parameters[2])

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        assert outcome.applied is True and outcome.variant_id == first
        assert _context(db_session, world.context_id).work_variant_id == first

    def test_a_secondary_parameter_row_does_not_hold_the_variant_back(self, db_session, factories):
        world = _active_world(db_session, factories)
        _update_parameter(db_session, world.parameters[2], role="secondary")
        first = _settle(db_session, world)
        _drop_row(db_session, world.context_id, world.parameters[2])

        outcome = _apply(db_session, world, world.context_id, _answer(), ordinals=())

        assert outcome.applied is True and outcome.variant_id == first
        assert sorted(_rows(db_session, world.context_id)) == [1]

    def test_the_split_hint_is_computed_over_the_rows_of_all_active_parameters(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "conflict", None, None), _named(2, "бетон")),
        )
        assert _context(db_session, world.context_id).variant_split_hint == "path_conflict"
        _mark(db_session, world.context_id, world.parameters[2])

        _apply(db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,))

        assert _context(db_session, world.context_id).variant_split_hint == "path_conflict"

    def test_the_split_hint_goes_when_no_active_row_has_a_conflict(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "conflict", None, None), _named(2, "бетон")),
        )
        _mark(db_session, world.context_id, world.parameters[1])

        _apply(db_session, world, world.context_id, _answer(_named(1, "50 мм")), ordinals=(1,))

        assert _context(db_session, world.context_id).variant_split_hint is None

    def test_an_archived_parameter_row_is_not_touched_and_not_asked(self, db_session, factories):
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        archived_before = _rows(db_session, world.context_id)[3]
        _update_parameter(db_session, world.parameters[3], archived_at=sa.func.now())
        _mark(db_session, world.context_id, world.parameters[2])

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.applied is True and rows[3] == archived_before
        [event] = _events(db_session, "context_variant_assigned", context_id=world.context_id)[-1:]
        assert event.payload["kept_ordinals"] == [1]

    def test_an_archived_ordinal_among_the_requested_is_not_expected_in_the_answer(
        self, db_session, factories
    ):
        """Запрошенные номера пересекаются с активными: архивный номер в запросе
        ответа не требует и строки не получает."""
        world = _active_world(db_session, factories, _THREE)
        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15")),
        )
        archived_before = _rows(db_session, world.context_id)[3]
        _update_parameter(db_session, world.parameters[3], archived_at=sa.func.now())
        _mark(db_session, world.context_id, world.parameters[2])

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2, 3)
        )

        rows = _rows(db_session, world.context_id)
        assert outcome.applied is True
        assert rows[2].value_id == world.values[(2, "кирпич")]
        assert rows[3] == archived_before

    def test_a_schema_whose_parameters_are_all_archived_stores_the_empty_paths_hash(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        for ordinal in (1, 2):
            _update_parameter(db_session, world.parameters[ordinal], archived_at=sa.func.now())

        outcome = _apply(db_session, world, world.context_id, _answer(), ordinals=())

        assert outcome.applied is True
        assert _context(db_session, world.context_id).variant_paths_hash == paths_hash_of(())
        assert _paths_hash(db_session, world.context_id) != paths_hash_of(())

    def test_without_explicit_ordinals_the_job_ordinals_decide(self, db_session, factories):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        job, guard = _job_for(
            db_session, world, world.context_id, [2], status="running", settings=_settings()
        )

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), guard=guard
        )

        assert outcome.applied is True
        assert _rows(db_session, world.context_id)[2].value_id == world.values[(2, "кирпич")]
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "done"

    def test_edit_of_a_requested_parameter_between_claim_and_write_is_stale(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        job, guard = _job_for(
            db_session, world, world.context_id, [2], status="running", settings=_settings()
        )
        _value(db_session, db_session.get(FamilyParameter, world.parameters[2]), "дерево")
        before = _rows(db_session, world.context_id)

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), guard=guard
        )

        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _rows(db_session, world.context_id) == before
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).cancel_reason == "input_changed"

    def test_edit_of_a_parameter_outside_the_subset_does_not_make_it_stale(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])
        job, guard = _job_for(
            db_session, world, world.context_id, [2], status="running", settings=_settings()
        )
        _value(db_session, db_session.get(FamilyParameter, world.parameters[1]), "150 мм")

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), guard=guard
        )

        assert outcome.applied is True and outcome.unapplied_reason is None

    def test_a_secondary_parameter_answer_is_written_without_changing_the_variant(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _update_parameter(db_session, world.parameters[2], role="secondary")
        first = _settle(db_session, world)
        _mark(db_session, world.context_id, world.parameters[2])

        outcome = _apply(
            db_session, world, world.context_id, _answer(_named(2, "кирпич")), ordinals=(2,)
        )

        assert outcome.variant_id == first
        assert _rows(db_session, world.context_id)[2].value_id == world.values[(2, "кирпич")]


# ---------------------------------------------------------------------------
#  Опросчик: путь `claim_next` -> `record_result`
# ---------------------------------------------------------------------------

class TestWorkerPartialPath:
    def _partial_scene(self, db, factories):
        world = _active_world(db, factories)
        _settle(db, world)
        _mark(db, world.context_id, world.parameters[2])
        _reconcile(db, [world.context_id])
        return world

    def test_a_partial_answer_replaces_only_the_requested_row(self, db_session, factories):
        world = self._partial_scene(db_session, factories)
        kept = _rows(db_session, world.context_id)[1]
        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None and claim.synthesized is False

        worker.record_result(
            db_session, claim,
            _response(
                '{"values": [{"ordinal": 2, "kind": "value", "value": "кирпич", "source": "name"}]}'
            ),
            now=NOW, settings=S,
        )

        rows = _rows(db_session, world.context_id)
        assert rows[1] == kept
        assert rows[2].value_id == world.values[(2, "кирпич")]
        [job] = _values_jobs(db_session, world.context_id)
        assert (job.status, job.claim_token) == ("done", None)
        [attempt] = db_session.execute(sa.select(SemanticJobAttempt)).scalars().all()
        assert attempt.outcome == "ok"

    def test_an_answer_for_the_whole_schema_is_a_schema_error_for_a_partial_job(
        self, db_session, factories
    ):
        world = self._partial_scene(db_session, factories)
        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None
        before = _rows(db_session, world.context_id)

        worker.record_result(
            db_session, claim,
            _response(
                '{"values": [{"ordinal": 1, "kind": "value", "value": "100 мм", "source": "name"},'
                ' {"ordinal": 2, "kind": "value", "value": "кирпич", "source": "name"}]}'
            ),
            now=NOW, settings=S,
        )

        [job] = _values_jobs(db_session, world.context_id)
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        assert _rows(db_session, world.context_id) == before

    def test_a_value_retired_outside_the_subset_does_not_touch_the_answer(
        self, db_session, factories
    ):
        world = self._partial_scene(db_session, factories)
        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == world.values[(1, "100 мм")])
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        # Параметр 1 в запросе не участвовал: выведенное у него значение отпечаток
        # частичного запроса не меняет.
        worker.record_result(
            db_session, claim,
            _response(
                '{"values": [{"ordinal": 2, "kind": "value", "value": "кирпич", "source": "name"}]}'
            ),
            now=NOW, settings=S,
        )

        [job] = _values_jobs(db_session, world.context_id)
        assert job.status == "done"


# ---------------------------------------------------------------------------
#  Волна расширения
# ---------------------------------------------------------------------------

class TestExtensionWaveSubset:
    def _wave_scene(self, db, factories, params=_PARAMS):
        world = _active_world(db, factories, params)
        other = _second_context(db, factories, world)
        answer = _answer(*[_named(ordinal, values[0]) for ordinal, _name, values in params])
        for context_id in (world.context_id, other):
            _settle_context(db, world, context_id, answer)
        return world, other

    def test_the_wave_asks_only_the_extended_parameter(self, db_session, factories):
        world, other = self._wave_scene(db_session, factories)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(value_id=None, source="none")
        )
        db_session.flush()

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[1]
        )

        assert posted == 1
        [job] = _values_jobs(db_session, other)
        assert job.requested_ordinals == [1]
        assert job.request_hash == _hash(db_session, other, [1])
        assert job.request_hash != _hash(db_session, other)
        assert _values_jobs(db_session, world.context_id) == []

    def test_a_context_with_an_off_list_value_is_asked_again_too(self, db_session, factories):
        world, other = self._wave_scene(db_session, factories)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[2],
            )
            .values(value_id=None, source="off_list", off_list_text="дерево")
        )
        db_session.flush()

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[2]
        )

        assert posted == 1
        [job] = _values_jobs(db_session, other)
        assert job.requested_ordinals == [2]

    def test_a_wave_job_survives_the_next_reconcile_and_the_row_stays_marked(
        self, db_session, factories
    ):
        world, other = self._wave_scene(db_session, factories)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(value_id=None, source="none")
        )
        db_session.flush()
        schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[1]
        )
        [job] = _values_jobs(db_session, other)
        assert job.requested_ordinals == [1]
        assert _rows(db_session, other)[1].refresh_requested_at is not None

        report = _reconcile(db_session, [other])

        [again] = _values_jobs(db_session, other)
        assert (again.id, again.status, again.cancel_reason) == (job.id, "pending", None)
        assert (report.created, report.cancelled) == (0, 0)
        assert _rows(db_session, other)[1].refresh_requested_at is not None
        assert _rows(db_session, other)[2].refresh_requested_at is None
        assert _rows(db_session, world.context_id)[1].refresh_requested_at is None

    def test_the_answer_of_a_wave_job_clears_the_mark(self, db_session, factories):
        world, other = self._wave_scene(db_session, factories)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(value_id=None, source="none")
        )
        db_session.flush()
        schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[1]
        )
        _only_values_jobs(db_session)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        assert claim is not None and claim.synthesized is False

        worker.record_result(
            db_session, claim,
            _response(
                '{"values": [{"ordinal": 1, "kind": "value", "value": "100 мм", "source": "name"}]}'
            ),
            now=NOW, settings=S,
        )

        row = _rows(db_session, other)[1]
        assert (row.value_id, row.refresh_requested_at) == (world.values[(1, "100 мм")], None)

    def test_a_wave_for_a_parameter_of_a_superseded_version_asks_nothing(
        self, db_session, factories
    ):
        """Параметр прежней версии: у текущей версии под тем же номером может быть
        другой параметр — волна не спрашивает его чужим номером."""
        world, other = self._wave_scene(db_session, factories)
        old = _schema(db_session, factories, world.family, version=7, status="superseded")
        old_parameter = _param(db_session, old, 1, "Толщина")
        _put_row(db_session, other, old.id, old_parameter.id, None, source="none")

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=old_parameter.id
        )

        assert posted == 0
        assert _values_jobs(db_session, other) == []
        # Волна такого параметра ничего не пишет: строка прежней версии не помечается.
        assert _refresh_mark(db_session, other, old_parameter.id) is None

    def test_a_wave_for_an_archived_parameter_asks_nothing(self, db_session, factories):
        world, other = self._wave_scene(db_session, factories)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[2],
            )
            .values(value_id=None, source="none")
        )
        _update_parameter(db_session, world.parameters[2], archived_at=sa.func.now())

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[2]
        )

        assert posted == 0
        assert _values_jobs(db_session, other) == []
        assert _refresh_mark(db_session, other, world.parameters[2]) is None

    def test_a_row_already_marked_keeps_the_time_of_its_mark(self, db_session, factories):
        """Отметку «уточняется», поставленную раньше, волна не переписывает: строка
        уже ждёт перезапроса, задание всё равно ставится."""
        world, other = self._wave_scene(db_session, factories)
        marked_at = dt.datetime(2001, 2, 3, 4, 5, 6, tzinfo=dt.UTC)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == other,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(value_id=None, source="none", refresh_requested_at=marked_at)
        )
        db_session.flush()

        posted = schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[1]
        )

        assert posted == 1
        [job] = _values_jobs(db_session, other)
        assert job.requested_ordinals == [1]
        assert _refresh_mark(db_session, other, world.parameters[1]) == marked_at

    def test_a_one_parameter_schema_stores_the_full_set_as_null(self, db_session, factories):
        world, other = self._wave_scene(db_session, factories, ((1, "Толщина", ["50 мм"]),))
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(ContextParameterValue.context_id == other)
            .values(value_id=None, source="none")
        )
        db_session.flush()

        schedule_extension_wave(
            db_session, family_id=world.family.id, parameter_id=world.parameters[1]
        )

        [job] = _values_jobs(db_session, other)
        assert job.requested_ordinals is None
        assert job.request_hash == _hash(db_session, other)


# ---------------------------------------------------------------------------
#  Оценки стоимости
# ---------------------------------------------------------------------------

class TestEstimatesAgreeWithThePlan:
    def test_the_preview_prices_the_same_subsets_as_the_planner(self, db_session, factories):
        world = _active_world(db_session, factories, _THREE)
        partial = world.context_id
        full = _second_context(db_session, factories, world)
        settled = _second_context(db_session, factories, world)
        answer = _answer(_named(1, "50 мм"), _named(2, "бетон"), _named(3, "В15"))
        for context_id in (partial, full, settled):
            _settle_context(db_session, world, context_id, answer)
        _drop_row(db_session, partial, world.parameters[2])
        _context(db_session, full).variant_paths_hash = "other-paths"
        db_session.flush()
        contexts = [partial, full, settled]

        fingerprints, planned_reserve, planned_cached = estimate_enqueue(db_session, contexts)
        assert {f.kind for f in fingerprints} == {SemanticJobKind.context_values}
        assert len(fingerprints) == 2
        preview = rebuild_preview(db_session, world.family.id)

        schema_reserve, schema_cached = _estimate(
            db_session,
            [(
                SemanticJobKind.family_schema,
                render_schema_request(
                    load_schema_material(db_session, world.family.id, 0), settings=app_settings
                ),
            )],
        )
        expected_reserve, expected_cached = _estimate_totals(
            db_session,
            [
                ("context_values", _render(db_session, partial, [2])),
                ("context_values", _render(db_session, full, [1, 2, 3])),
            ],
        )
        assert planned_reserve == expected_reserve > 0
        assert planned_cached == expected_cached
        # Предпросмотр пересборки: после неё спрашивают каждый контекст по всем
        # параметрам версии, поэтому часть значений — полные рендеры всех контекстов,
        # в том числе уже заполненного.
        full_reserve, full_cached = _estimate_totals(
            db_session,
            [("context_values", _render(db_session, c, [1, 2, 3])) for c in contexts],
        )
        assert preview.reserve_usd == schema_reserve + full_reserve
        assert preview.expected_cached_usd == schema_cached + full_cached
        assert full_reserve > planned_reserve

    def test_a_settled_family_still_has_a_values_part_in_the_preview(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        _settle(db_session, world)
        assert _need(db_session, world) == ((), False)

        preview = rebuild_preview(db_session, world.family.id)

        schema_reserve, _ = _estimate(
            db_session,
            [(
                SemanticJobKind.family_schema,
                render_schema_request(
                    load_schema_material(db_session, world.family.id, 0), settings=app_settings
                ),
            )],
        )
        values_reserve, _ = _estimate_totals(
            db_session, [("context_values", _render(db_session, world.context_id, [1, 2]))]
        )
        assert values_reserve > 0
        assert preview.reserve_usd == schema_reserve + values_reserve


def _render(db, context_id, ordinals):
    material = load_values_material(db, [context_id], requested_ordinals=ordinals)[context_id]
    return render_values_request(material, settings=app_settings)

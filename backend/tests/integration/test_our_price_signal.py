"""Сигнал «каталог ещё разбирает» — `crud/our_price_load.py::catalog_signal`
(спека 4а §2.11, §2.17, §2.18).

Каждое правило имеет обе стороны: вход, где флаг поднимается, и соседний вход,
нарушающий ровно одно условие. Сцены строятся через фабрики и сервисы каталога;
задания, пачки и состояние захвата записываются прямо, литералами.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from crud.our_price_load import CatalogSignal, catalog_signal
from models import (
    FamilyParameterSchema,
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    SemanticWorkerState,
)
from services.context_routing import route_position
from services.work_families import assign_family
from tests.integration.test_semantic_queue_api import _active_family, _unit_id

pytestmark = pytest.mark.integration

NONE = CatalogSignal(False, False)
IN_PROGRESS = CatalogSignal(True, False)
WAITING = CatalogSignal(False, True)

CONTEXT_KINDS = ("family_suggestion", "context_values")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _estimate_proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return estimate.id, factories.ProposalFactory.create(lot=lot)


def _row(db, factories, proposal, *, unit_id, title, family_id=None, admin_id=None):
    """Строка сметы с каталожной строкой, членством и контекстом; семья —
    по желанию. Возвращает `(position_item_id, context_id)`."""
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    item = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    context_id = route_position(db, position_item_id=item.id).context_id
    if family_id is not None:
        assign_family(db, context_id=context_id, family_id=family_id, actor_id=admin_id)
    return item.id, context_id


def _schema_id(db, family_id) -> int:
    return db.execute(
        sa.select(FamilyParameterSchema.id).where(FamilyParameterSchema.family_id == family_id)
    ).scalar_one()


def _job(db, kind, status, *, context_id=None, family_id=None, unit_id=None, schema_of=None):
    """Задание очереди с литеральными осями запроса; обязательные парные поля
    статуса заполнены так, чтобы проходили CHECK-и схемы."""
    schema_id = None
    if kind in ("family_schema", "context_values"):
        schema_id = _schema_id(db, schema_of if schema_of is not None else family_id)
    job = SemanticJob(
        kind=kind,
        context_id=context_id,
        family_id=family_id,
        schema_id=schema_id,
        request_hash=uuid.uuid4().hex,
        status=status,
        unit_id=unit_id,
        claim_token=uuid.uuid4() if status == "running" else None,
        cancel_reason="input_changed" if status == "cancelled" else None,
        privacy_matches=(
            [{"text": "иное имя", "kind": "contractor", "where": "context"}] if status == "privacy_hold" else None
        ),
        prompt_version="1",
        model_requested="m",
        place_dictionary_version=1,
        candidates_hash="c",
        prefix_hash="p",
        input_hash="i",
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _batch(db, elements, *, status="held", admin_id=None):
    decided = status != "held"
    batch = SemanticReconcileBatch(
        source="mass",
        held_fingerprints=elements,
        fingerprints_hash=uuid.uuid4().hex,
        contexts_count=len(elements),
        reserve_estimate_usd=0,
        cached_estimate_usd=0,
        status=status,
        decided_by=admin_id if decided else None,
        decided_at=dt.datetime.now(dt.UTC) if decided else None,
    )
    db.add(batch)
    db.flush()
    return batch


def _element(kind, *, context_id=None, family_id=None, schema_id=None):
    return {
        "kind": kind,
        "context_id": context_id,
        "family_id": family_id,
        "schema_id": schema_id,
        "request_hash": uuid.uuid4().hex,
    }


def _pause(db):
    """Захват остановлен: парные поля состояния требуют попытку-причину."""
    anchor = _job_anchor(db)
    attempt = SemanticJobAttempt(
        job_id=anchor.id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=dt.datetime.now(dt.UTC),
        reserve_usd=0,
        prefix_hash="p",
        privacy_dictionary_hash="d",
    )
    db.add(attempt)
    db.flush()
    db.execute(
        sa.update(SemanticWorkerState)
        .where(SemanticWorkerState.id == 1)
        .values(
            claim_paused=True,
            paused_reason="reserve_exceeded",
            paused_attempt_id=attempt.id,
            paused_at=dt.datetime.now(dt.UTC),
        )
    )


def _job_anchor(db):
    """Завершённое задание-якорь: на сигнал строк не влияет."""
    return _job(db, "family_discovery", "done", unit_id=-1)


@contextmanager
def count_statements(db):
    seen: list[str] = []
    bind = db.get_bind()

    def _tick(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    sa.event.listen(bind, "before_cursor_execute", _tick)
    try:
        yield seen
    finally:
        sa.event.remove(bind, "before_cursor_execute", _tick)


class Scene:
    """Две единицы и по строке на каждую комбинацию «с семьёй / без семьи»."""


@pytest.fixture
def scene(db_session, factories, admin_user):
    s = Scene()
    s.db = db_session
    s.admin = admin_user
    s.unit = _unit_id(db_session, "M2")
    s.other_unit = _unit_id(db_session, "M3")
    s.family = _active_family(db_session, title="Семья пола", unit_name="M2", actor_id=admin_user.id)
    s.other_family = _active_family(db_session, title="Семья стен", unit_name="M3", actor_id=admin_user.id)
    s.estimate_id, proposal = _estimate_proposal(factories)
    s.proposal = proposal
    s.factories = factories
    s.famed, s.famed_ctx = _row(
        db_session,
        factories,
        proposal,
        unit_id=s.unit,
        title="Пол",
        family_id=s.family.id,
        admin_id=admin_user.id,
    )
    s.free, s.free_ctx = _row(db_session, factories, proposal, unit_id=s.unit, title="Стена")
    s.neighbor, s.neighbor_ctx = _row(
        db_session,
        factories,
        proposal,
        unit_id=s.other_unit,
        title="Окно",
        family_id=s.other_family.id,
        admin_id=admin_user.id,
    )
    # Назначение семьи само ставит задания очереди; сцена стартует без заданий,
    # каждый тест кладёт ровно те, что проверяет.
    db_session.execute(sa.delete(SemanticJob))
    db_session.expire_all()
    return s


def _signal(s):
    return catalog_signal(s.db, [s.estimate_id])


# ---------------------------------------------------------------------------
#  Живые задания: по виду и статусу
# ---------------------------------------------------------------------------


class TestLiveJobs:
    @pytest.mark.parametrize("kind", CONTEXT_KINDS)
    @pytest.mark.parametrize("status", ("pending", "running"))
    def test_context_job_marks_its_row_only(self, scene, kind, status):
        _job(scene.db, kind, status, context_id=scene.free_ctx, schema_of=scene.family.id)

        signals = _signal(scene)

        assert signals[scene.free] == IN_PROGRESS
        assert signals[scene.famed] == NONE
        assert signals[scene.neighbor] == NONE

    @pytest.mark.parametrize("status", ("pending", "running"))
    def test_family_schema_job_marks_rows_of_that_family(self, scene, status):
        _job(scene.db, "family_schema", status, family_id=scene.family.id)

        signals = _signal(scene)

        assert signals[scene.famed] == IN_PROGRESS
        assert signals[scene.free] == NONE
        assert signals[scene.neighbor] == NONE

    @pytest.mark.parametrize("status", ("pending", "running"))
    def test_discovery_marks_a_row_without_family_of_that_unit(self, scene, status):
        _job(scene.db, "family_discovery", status, unit_id=scene.unit)

        signals = _signal(scene)

        assert signals[scene.free] == IN_PROGRESS

    def test_discovery_does_not_mark_a_row_with_a_family(self, scene):
        _job(scene.db, "family_discovery", "pending", unit_id=scene.unit)

        signals = _signal(scene)

        assert signals[scene.famed] == NONE
        assert signals[scene.neighbor] == NONE

    def test_discovery_of_another_unit_does_not_mark_the_row(self, scene):
        _job(scene.db, "family_discovery", "pending", unit_id=scene.other_unit)

        assert _signal(scene)[scene.free] == NONE

    def test_discovery_of_a_unit_without_value_marks_rows_without_unit(self, scene):
        item, ctx = _row(scene.db, scene.factories, scene.proposal, unit_id=None, title="Без единицы")
        _job(scene.db, "family_discovery", "pending", unit_id=None)

        signals = _signal(scene)

        assert signals[item] == IN_PROGRESS
        assert signals[scene.free] == NONE

    def test_job_of_a_context_kind_does_not_mark_by_unit(self, scene):
        """Единица у задания контекста — ось приоритета, а не предмет."""
        _job(scene.db, "family_suggestion", "pending", context_id=scene.neighbor_ctx, unit_id=scene.unit)

        signals = _signal(scene)

        assert signals[scene.free] == NONE
        assert signals[scene.famed] == NONE
        assert signals[scene.neighbor] == IN_PROGRESS

    def test_schema_job_does_not_mark_by_context(self, scene):
        _job(scene.db, "family_schema", "pending", family_id=scene.other_family.id)

        signals = _signal(scene)

        assert signals[scene.neighbor] == IN_PROGRESS
        assert signals[scene.famed] == NONE


# ---------------------------------------------------------------------------
#  Остановившиеся и завершённые задания
# ---------------------------------------------------------------------------


def _seed_kind(s, kind, status):
    if kind in CONTEXT_KINDS:
        _job(s.db, kind, status, context_id=s.famed_ctx, schema_of=s.family.id)
    elif kind == "family_schema":
        _job(s.db, kind, status, family_id=s.family.id)
    else:
        _job(s.db, kind, status, unit_id=s.unit)


ALL_KINDS = (*CONTEXT_KINDS, "family_schema", "family_discovery")


class TestStuckAndFinishedJobs:
    @pytest.mark.parametrize("kind", ALL_KINDS)
    @pytest.mark.parametrize("status", ("error", "privacy_hold"))
    def test_stuck_job_waits_for_admin(self, scene, kind, status):
        target = scene.free if kind == "family_discovery" else scene.famed
        if kind == "family_discovery":
            _job(scene.db, kind, status, unit_id=scene.unit)
        else:
            _seed_kind(scene, kind, status)

        signals = _signal(scene)

        assert signals[target] == WAITING
        # Задание принадлежит только своей строке: соседние по контексту, семье
        # и единице не трогаются.
        for other in {scene.famed, scene.free, scene.neighbor} - {target}:
            assert signals[other] == NONE

    @pytest.mark.parametrize("kind", ALL_KINDS)
    @pytest.mark.parametrize("status", ("done", "cancelled"))
    def test_finished_job_changes_nothing(self, scene, kind, status):
        _seed_kind(scene, kind, status)

        signals = _signal(scene)

        assert signals[scene.famed] == NONE
        assert signals[scene.free] == NONE


# ---------------------------------------------------------------------------
#  Удержанные пачки
# ---------------------------------------------------------------------------


class TestHeldBatches:
    def test_held_batch_naming_the_context_waits_for_admin(self, scene):
        _batch(scene.db, [_element("family_suggestion", context_id=scene.free_ctx)])

        signals = _signal(scene)

        assert signals[scene.free] == WAITING
        assert signals[scene.famed] == NONE

    def test_held_batch_naming_the_family_waits_for_admin(self, scene):
        _batch(scene.db, [_element("family_schema", family_id=scene.family.id)])

        signals = _signal(scene)

        assert signals[scene.famed] == WAITING
        assert signals[scene.free] == NONE
        assert signals[scene.neighbor] == NONE

    def test_held_batch_of_another_context_changes_nothing(self, scene):
        _batch(scene.db, [_element("context_values", context_id=scene.neighbor_ctx)])

        signals = _signal(scene)

        assert signals[scene.famed] == NONE
        assert signals[scene.free] == NONE
        assert signals[scene.neighbor] == WAITING

    def test_element_of_the_old_pair_form_is_skipped(self, scene):
        """Пара `[context_id, hash]` прежнего формата схемой не запрещена;
        сигнал её пропускает и читает остальные элементы пачки."""
        _batch(
            scene.db,
            [[scene.free_ctx, uuid.uuid4().hex], _element("family_suggestion", context_id=scene.famed_ctx)],
        )

        signals = _signal(scene)

        assert signals[scene.famed] == WAITING
        assert signals[scene.neighbor] == NONE

    @pytest.mark.parametrize("status", ("approved", "discarded"))
    def test_decided_batch_changes_nothing(self, scene, status):
        _batch(
            scene.db,
            [
                _element("family_suggestion", context_id=scene.free_ctx),
                _element("family_schema", family_id=scene.family.id),
            ],
            status=status,
            admin_id=scene.admin.id,
        )

        signals = _signal(scene)

        assert signals[scene.free] == NONE
        assert signals[scene.famed] == NONE


# ---------------------------------------------------------------------------
#  Захват на паузе
# ---------------------------------------------------------------------------


class TestClaimPaused:
    def test_pause_with_live_job_turns_progress_into_waiting(self, scene):
        _job(scene.db, "family_suggestion", "pending", context_id=scene.free_ctx)
        _pause(scene.db)

        signals = _signal(scene)

        assert signals[scene.free] == WAITING

    def test_pause_without_live_job_leaves_the_row_alone(self, scene):
        _job(scene.db, "family_suggestion", "pending", context_id=scene.free_ctx)
        _pause(scene.db)

        signals = _signal(scene)

        assert signals[scene.famed] == NONE
        assert signals[scene.neighbor] == NONE

    def test_without_pause_the_same_job_is_progress(self, scene):
        _job(scene.db, "family_suggestion", "pending", context_id=scene.free_ctx)

        assert _signal(scene)[scene.free] == IN_PROGRESS

    def test_missing_worker_state_row_means_not_paused(self, scene):
        scene.db.execute(sa.delete(SemanticWorkerState))
        _job(scene.db, "family_suggestion", "pending", context_id=scene.free_ctx)

        assert _signal(scene)[scene.free] == IN_PROGRESS


# ---------------------------------------------------------------------------
#  Состав ответа
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_row_without_membership_has_both_flags_false(self, scene):
        item = scene.factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=False, job_title_in_proposal="Без каталога"
        )
        # Строка с контекстом без единицы и без семьи — чтобы открытие единицы без
        # значения реально дошло до сборки сигнала; строка без членства его
        # не получает.
        unitless, _ = _row(scene.db, scene.factories, scene.proposal, unit_id=None, title="Без единицы")
        _job(scene.db, "family_discovery", "pending", unit_id=None)

        signals = _signal(scene)

        assert signals[unitless] == IN_PROGRESS
        assert signals[item.id] == NONE

    def test_chapters_are_absent_and_all_other_rows_present(self, scene):
        chapter = scene.factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=True, job_title_in_proposal="Раздел"
        )

        signals = _signal(scene)

        assert chapter.id not in signals
        assert set(signals) == {scene.famed, scene.free, scene.neighbor}

    def test_rows_of_other_estimates_are_absent_and_listed_ones_are_present(self, scene, factories):
        other_estimate_id, other_proposal = _estimate_proposal(factories)
        other, _ = _row(scene.db, factories, other_proposal, unit_id=scene.unit, title="Чужая")

        only_scene = catalog_signal(scene.db, [scene.estimate_id])
        both = catalog_signal(scene.db, [scene.estimate_id, other_estimate_id])

        assert other not in only_scene
        assert other in both
        assert scene.famed in both

    def test_empty_list_of_estimates_gives_empty_answer(self, scene):
        assert catalog_signal(scene.db, []) == {}


# ---------------------------------------------------------------------------
#  Число запросов
# ---------------------------------------------------------------------------


def _stage(db, factories, admin, rows: int):
    """Этап из `rows` строк на отдельных контекстах, у части — живые и
    остановленные задания, у части — семья."""
    unit = _unit_id(db, "M2")
    family = _active_family(db, title=f"Семья {uuid.uuid4().hex[:6]}", unit_name="M2", actor_id=admin.id)
    estimate_id, proposal = _estimate_proposal(factories)
    tag = uuid.uuid4().hex[:6]
    for i in range(rows):
        _, ctx = _row(
            db,
            factories,
            proposal,
            unit_id=unit,
            title=f"Работа {tag} {i}",
            family_id=family.id if i % 2 else None,
            admin_id=admin.id,
        )
        _job(db, "family_suggestion", "pending" if i % 3 else "error", context_id=ctx)
    db.flush()
    return estimate_id


class TestStatementCount:
    def test_count_does_not_depend_on_the_number_of_rows(self, db_session, factories, admin_user):
        small = _stage(db_session, factories, admin_user, 2)
        large = _stage(db_session, factories, admin_user, 20)

        with count_statements(db_session) as small_seen:
            small_signals = catalog_signal(db_session, [small])
        with count_statements(db_session) as large_seen:
            large_signals = catalog_signal(db_session, [large])

        assert len(small_signals) == 2
        assert len(large_signals) == 20
        assert len(small_seen) == len(large_seen)

    def test_every_statement_is_a_read(self, db_session, factories, admin_user):
        estimate_id = _stage(db_session, factories, admin_user, 6)

        with count_statements(db_session) as seen:
            catalog_signal(db_session, [estimate_id])

        assert seen
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in seen)

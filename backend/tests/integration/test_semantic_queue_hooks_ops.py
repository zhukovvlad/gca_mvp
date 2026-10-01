"""Точки инварианта очереди семантических предложений: операции над
контекстами, Review, разовый проход (задача 9 фичи «Семантические
предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 9.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.7 (перечень операций, решения 2 и 5, разовый проход), §2.11 (потолок).

Тест точки строится входом, который краснеет, если вызов сверки в ЭТОЙ точке
снят: перед операцией очередь приведена в соответствие сверкой самого теста,
поэтому без вызова в операции задания остаются такими, какими были до неё.
«Отпечаток изменился» обеспечивает самый частый путь разделов членов — он
входит в тело запроса (спека §2.2); ничья путей решается по возрастанию
текста, поэтому в сценах путь «Стены» побеждает «Пол» только по числу
членов. Отрицательные утверждения (перевод в `STALE`, роль имени, правки
семей) проверяются на очереди, УЖЕ устаревшей второй активной семьёй: любая
лишняя сверка в такой точке оставила бы после себя новое задание.

Помощники — локальные: наборы помощников тестов проекта друг у друга не
импортируют. Модули, чьи записи покрыты точками других файлов —
`context_routing` (создаёт членства на импорте, разовом проходе и переносе),
`estimate_import` и `round_import` (собирают контексты вытесненных смет до
удаления), `crud.contracts`/`crud.tenders` (удаления), — названы в
`test_semantic_queue_hooks_import.py`.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.catalog_backfill as catalog_backfill_module
import services.context_operations as context_operations_module
import services.semantic_reconcile as semantic_reconcile_module
import services.semantic_request as semantic_request_module
from config import settings
from models import (
    CatalogContext,
    CatalogKind,
    ContextMember,
    Lot,
    MembershipState,
    NameRole,
    PositionItem,
    Proposal,
    ReconcileBatchStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobStatus,
    SemanticReconcileBatch,
    WorkCategory,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.catalog_backfill import run_backfill
from services.category_override import set_override
from services.category_resolution import CategoryResolver
from services.context_operations import (
    accept_target_decision,
    accept_transfer,
    archive_context,
    merge_contexts,
    move_members,
    refresh_membership_states,
    split_context,
    transfer_stale_group,
)
from services.context_routing import route_position, route_positions
from services.estimate_import import import_estimate
from services.import_owners import contract_estimate_owner
from services.matching import match_positions
from services.review import merge_into_position, set_kind
from services.semantic_cost import EventCap
from services.semantic_reconcile import NO_CAP, reconcile_semantic_jobs
from services.semantic_request import load_request_material, render_context_request
from services.unit_resolution import UnitResolver
from services.work_families import (
    activate_family,
    archive_family,
    assign_family,
    confirm_kind,
    create_family,
    merge_families,
    set_name_role,
    unconfirm_kind,
    update_family,
)
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration

_PENDING = SemanticJobStatus.pending.value
_CANCELLED = SemanticJobStatus.cancelled.value
_INPUT_CHANGED = SemanticCancelReason.input_changed.value
_NOT_APPLICABLE = SemanticCancelReason.not_applicable.value


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _active_family(db, *, title, unit_name, actor_id):
    fam = create_family(
        db, title=title, unit_name=unit_name, definition="Определение семьи", actor_id=actor_id
    )
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _leaf_category_id(db) -> int:
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return db.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent))
        .order_by(WorkCategory.sort_order)
        .limit(1)
    ).scalar_one()


def _settle(db, *context_ids) -> None:
    """Приводит очередь контекстов в соответствие с их состоянием — исходная
    точка каждого теста, независимая от вызовов сверки в проверяемых операциях."""
    reconcile_semantic_jobs(db, context_ids, cap=NO_CAP, source="operation")
    db.expire_all()


def _jobs(db, context_id) -> list[SemanticJob]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob).where(SemanticJob.context_id == context_id).order_by(SemanticJob.id)
        ).scalars()
    )


def _snapshot(db, context_id):
    return [
        (j.id, j.status, j.cancel_reason, j.request_hash, j.retry_generation)
        for j in _jobs(db, context_id)
    ]


def _current_hash(db, context_id) -> str:
    material = load_request_material(db, [context_id])[context_id]
    return render_context_request(material, settings=settings).request_hash


def _job_count(db) -> int:
    db.expire_all()
    return db.execute(sa.select(sa.func.count()).select_from(SemanticJob)).scalar_one()


def _assert_pending_with_current_hash(db, context_id) -> SemanticJob:
    pending = [j for j in _jobs(db, context_id) if j.status == _PENDING]
    assert len(pending) == 1, [(j.status, j.cancel_reason) for j in _jobs(db, context_id)]
    assert pending[0].request_hash == _current_hash(db, context_id)
    return pending[0]


def _assert_old_pending_cancelled_new_pending(db, context_id, old_hash) -> None:
    rows = _jobs(db, context_id)
    old = [j for j in rows if j.request_hash == old_hash]
    assert [(j.status, j.cancel_reason) for j in old] == [(_CANCELLED, _INPUT_CHANGED)]
    assert _current_hash(db, context_id) != old_hash
    _assert_pending_with_current_hash(db, context_id)


def _assert_cancelled_not_applicable(db, context_id) -> None:
    rows = _jobs(db, context_id)
    assert rows, "у контекста были задания"
    assert [(j.status, j.cancel_reason) for j in rows] == [(_CANCELLED, _NOT_APPLICABLE)] * len(rows)


# ---------------------------------------------------------------------------
#  Сцена: смета через настоящий импорт, матчинг и маршрутизацию; одна и та же
#  работа в нескольких разделах — одна корзина, один контекст по умолчанию
# ---------------------------------------------------------------------------

class _Scene:
    pass


def _scene(db, factories, layout) -> _Scene:
    """`layout` — список `(название раздела, число работ в нём)`; все работы
    называются одинаково и попадают в один контекст."""
    title = f"Работа {_uid()}"
    rows = []
    number = 0
    for index, (chapter, count) in enumerate(layout, start=1):
        number += 1
        rows.append(
            position(job_title=chapter, is_chapter=True, chapter_number=str(index), number=str(number))
        )
        for _ in range(count):
            number += 1
            rows.append(
                position(
                    job_title=title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00",
                    chapter_ref=str(index), number=str(number),
                )
            )
    contract = factories.ContractFactory.create()
    db.flush()
    outcome = import_estimate(
        db,
        owner=contract_estimate_owner(contract, None),
        data=payload_for(contract, rows),
        parser_version="1.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=UnitResolver(db),
        category_resolver=CategoryResolver.from_db(db),
    )
    match_positions(db, outcome.positions_to_match)
    db.flush()
    route_positions(db, estimate_ids=[outcome.estimate_id])

    scene = _Scene()
    scene.estimate_id = outcome.estimate_id
    scene.user = factories.UserFactory.create()
    scene.family = _active_family(db, title=f"Семья {title}", unit_name="M2", actor_id=scene.user.id)
    scene.chapter_ids = {}
    scene.works = {}
    for chapter, _count in layout:
        chapter_row = db.execute(
            sa.select(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(
                Lot.estimate_id == outcome.estimate_id,
                PositionItem.is_chapter.is_(True),
                PositionItem.job_title_in_proposal == chapter,
            )
        ).scalar_one()
        scene.chapter_ids[chapter] = chapter_row.id
        scene.works[chapter] = list(
            db.execute(
                sa.select(PositionItem.id)
                .where(PositionItem.chapter_item_id == chapter_row.id)
                .order_by(PositionItem.id)
            ).scalars()
        )
    all_work_ids = [pid for ids in scene.works.values() for pid in ids]
    contexts = set(
        db.execute(
            sa.select(ContextMember.context_id).where(ContextMember.position_item_id.in_(all_work_ids))
        ).scalars()
    )
    assert len(contexts) == 1, "все работы сцены — в одном контексте"
    scene.context_id = contexts.pop()
    return scene


def _context_of(db, position_item_id) -> int:
    db.expire_all()
    return db.get(ContextMember, position_item_id).context_id


def _split_out(db, scene, chapter) -> int:
    """Выносит работы раздела в новый контекст той же корзины и возвращает его id."""
    result = split_context(
        db,
        context_id=scene.context_id,
        position_item_ids=scene.works[chapter],
        rule=None,
        actor_id=scene.user.id,
    )
    return result.new_context_id


def _stale_the_queue(db, scene) -> None:
    """Вторая активная семья единицы меняет отпечаток всех контекстов этой
    единицы, не ставя заданий (решение спеки 2): очередь становится устаревшей."""
    _active_family(db, title=f"Вторая семья {_uid()}", unit_name="M2", actor_id=scene.user.id)
    db.expire_all()


# ---------------------------------------------------------------------------
#  Разделение
# ---------------------------------------------------------------------------

class TestSplitPoint:
    def test_source_context_gets_a_new_fingerprint_after_its_members_left(self, db_session, factories):
        scene = _scene(db_session, factories, [("Стены", 2), ("Пол", 1)])
        _settle(db_session, scene.context_id)
        (old,) = _jobs(db_session, scene.context_id)

        _split_out(db_session, scene, "Стены")

        _assert_old_pending_cancelled_new_pending(db_session, scene.context_id, old.request_hash)

    def test_the_new_context_is_queued(self, db_session, factories):
        scene = _scene(db_session, factories, [("Стены", 2), ("Пол", 1)])
        _settle(db_session, scene.context_id)

        new_context_id = _split_out(db_session, scene, "Стены")

        assert [j.status for j in _jobs(db_session, new_context_id)] == [_PENDING]
        _assert_pending_with_current_hash(db_session, new_context_id)


# ---------------------------------------------------------------------------
#  Слияние, перенос членств, архивирование
# ---------------------------------------------------------------------------

def _two_contexts(db, factories):
    """`A` — «Пол» ×1, `B` — «Стены» ×2, обе в одной корзине; очередь приведена."""
    scene = _scene(db, factories, [("Стены", 2), ("Пол", 1)])
    new_context_id = _split_out(db, scene, "Стены")
    a_context_id = _context_of(db, scene.works["Пол"][0])
    _settle(db, a_context_id, new_context_id)
    return scene, a_context_id, new_context_id


class TestMergePoint:
    def test_archived_source_loses_its_pending(self, db_session, factories):
        scene, a_id, b_id = _two_contexts(db_session, factories)
        assert [j.status for j in _jobs(db_session, b_id)] == [_PENDING]

        merge_contexts(db_session, source_context_id=b_id, target_context_id=a_id, actor_id=scene.user.id)

        _assert_cancelled_not_applicable(db_session, b_id)

    def test_target_gets_a_new_fingerprint_from_the_arrived_members(self, db_session, factories):
        scene, a_id, b_id = _two_contexts(db_session, factories)
        (old,) = _jobs(db_session, a_id)

        merge_contexts(db_session, source_context_id=b_id, target_context_id=a_id, actor_id=scene.user.id)

        _assert_old_pending_cancelled_new_pending(db_session, a_id, old.request_hash)


class TestMoveMembersPoint:
    def test_emptied_source_loses_its_pending(self, db_session, factories):
        scene, a_id, b_id = _two_contexts(db_session, factories)

        move_members(
            db_session,
            position_item_ids=scene.works["Стены"],
            target_context_id=a_id,
            actor_id=scene.user.id,
            reason="manual",
        )

        _assert_cancelled_not_applicable(db_session, b_id)

    def test_target_gets_a_new_fingerprint_from_the_arrived_members(self, db_session, factories):
        scene, a_id, b_id = _two_contexts(db_session, factories)
        (old,) = _jobs(db_session, a_id)

        move_members(
            db_session,
            position_item_ids=scene.works["Стены"],
            target_context_id=a_id,
            actor_id=scene.user.id,
            reason="manual",
        )

        _assert_old_pending_cancelled_new_pending(db_session, a_id, old.request_hash)


class TestArchivePoint:
    def test_pending_left_over_an_emptied_context_is_cancelled(self, db_session, factories):
        """Опустевший контекст со «своим» заданием — след прежней операции без
        сверки; архивирование обязано его снять."""
        scene, a_id, b_id = _two_contexts(db_session, factories)
        assert [j.status for j in _jobs(db_session, b_id)] == [_PENDING]
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.context_id == b_id)
            .values(context_id=a_id)
        )
        db_session.expire_all()

        archive_context(db_session, context_id=b_id, new_default_context_id=None, actor_id=scene.user.id)

        _assert_cancelled_not_applicable(db_session, b_id)


# ---------------------------------------------------------------------------
#  Решение цели, перенос устаревшего
# ---------------------------------------------------------------------------

class TestAcceptTargetDecisionPoint:
    def test_context_of_the_decided_members_is_reconciled(self, db_session, factories):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        (old,) = _jobs(db_session, scene.context_id)
        (position_id,) = scene.works["Пол"]
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id == position_id)
            .values(conflict_at=dt.datetime.now(dt.UTC), conflict_from_context_id=scene.context_id)
        )
        _stale_the_queue(db_session, scene)

        accept_target_decision(db_session, position_item_ids=[position_id], actor_id=scene.user.id)

        _assert_old_pending_cancelled_new_pending(db_session, scene.context_id, old.request_hash)


def _stale_scene(db, factories):
    """Работы раздела «Стены» получили статью, их членства — `STALE`; очередь приведена."""
    scene = _scene(db, factories, [("Стены", 2), ("Пол", 1)])
    _settle(db, scene.context_id)
    scene.category_id = _leaf_category_id(db)
    set_override(
        db,
        estimate_id=scene.estimate_id,
        position_item_id=scene.chapter_ids["Стены"],
        work_category_id=scene.category_id,
        note=None,
        user_id=scene.user.id,
    )
    db.flush()
    db.expire_all()
    for position_id in scene.works["Стены"]:
        assert db.get(ContextMember, position_id).membership_state == MembershipState.STALE.value
    return scene


class TestAcceptTransferPoint:
    def test_source_context_gets_a_new_fingerprint_after_the_member_left(self, db_session, factories):
        scene = _stale_scene(db_session, factories)
        (old,) = _jobs(db_session, scene.context_id)

        accept_transfer(
            db_session,
            position_item_id=scene.works["Стены"][0],
            expected_category_id=scene.category_id,
            actor_id=scene.user.id,
        )

        _assert_old_pending_cancelled_new_pending(db_session, scene.context_id, old.request_hash)

    def test_target_context_of_the_new_bucket_is_queued(self, db_session, factories):
        scene = _stale_scene(db_session, factories)
        moved_id = scene.works["Стены"][0]

        accept_transfer(
            db_session,
            position_item_id=moved_id,
            expected_category_id=scene.category_id,
            actor_id=scene.user.id,
        )

        target_id = _context_of(db_session, moved_id)
        assert target_id != scene.context_id
        _assert_pending_with_current_hash(db_session, target_id)


class TestTransferStaleGroupPoint:
    def _run(self, db, scene):
        return transfer_stale_group(
            db,
            context_id=scene.context_id,
            chapter_item_ids=(scene.chapter_ids["Стены"],),
            expected_category_id=scene.category_id,
            actor_id=scene.user.id,
        )

    def test_source_and_target_contexts_are_reconciled(self, db_session, factories):
        scene = _stale_scene(db_session, factories)
        (old,) = _jobs(db_session, scene.context_id)

        result = self._run(db_session, scene)

        assert result.moved == 2
        _assert_old_pending_cancelled_new_pending(db_session, scene.context_id, old.request_hash)
        target_id = _context_of(db_session, scene.works["Стены"][0])
        assert target_id != scene.context_id
        _assert_pending_with_current_hash(db_session, target_id)

    def test_the_package_is_reconciled_once_not_per_position(self, db_session, factories, monkeypatch):
        scene = _stale_scene(db_session, factories)
        calls = []
        real = semantic_reconcile_module.reconcile_semantic_jobs

        def _spy(db, context_ids, **kwargs):
            calls.append(set(context_ids))
            return real(db, context_ids, **kwargs)

        monkeypatch.setattr(semantic_reconcile_module, "reconcile_semantic_jobs", _spy)

        result = self._run(db_session, scene)

        assert result.moved == 2
        assert len(calls) == 1
        target_id = _context_of(db_session, scene.works["Стены"][0])
        assert calls[0] == {scene.context_id, target_id}

    def test_a_package_that_moved_nothing_reconciles_nothing(self, db_session, factories, monkeypatch):
        """Все позиции пакета отказаны (ожидалась другая статья) — точки
        сохранения откатили каждую, состояние прежнее, сверки нет."""
        scene = _stale_scene(db_session, factories)
        _stale_the_queue(db_session, scene)
        before = _snapshot(db_session, scene.context_id)
        calls = []
        real = semantic_reconcile_module.reconcile_semantic_jobs

        def _spy(db, context_ids, **kwargs):
            calls.append(set(context_ids))
            return real(db, context_ids, **kwargs)

        monkeypatch.setattr(semantic_reconcile_module, "reconcile_semantic_jobs", _spy)

        result = transfer_stale_group(
            db_session,
            context_id=scene.context_id,
            chapter_item_ids=(scene.chapter_ids["Стены"],),
            expected_category_id=None,
            actor_id=scene.user.id,
        )

        assert (result.moved, result.refused) == (0, 2)
        assert calls == []
        assert _snapshot(db_session, scene.context_id) == before

    def test_a_package_broken_mid_way_leaves_single_transfers_reconciling(
        self, db_session, factories, monkeypatch
    ):
        """Исключение посреди пакета не оставляет отложенную сверку включённой:
        следующий одиночный перенос в том же потоке сверяет сам."""
        scene = _stale_scene(db_session, factories)
        (old,) = _jobs(db_session, scene.context_id)
        real_accept = context_operations_module.accept_transfer

        def _broken(db, **kwargs):
            real_accept(db, **kwargs)
            raise RuntimeError("сбой посреди пакета")

        monkeypatch.setattr(context_operations_module, "accept_transfer", _broken)
        with pytest.raises(RuntimeError):
            self._run(db_session, scene)
        monkeypatch.setattr(context_operations_module, "accept_transfer", real_accept)
        db_session.expire_all()

        accept_transfer(
            db_session,
            position_item_id=scene.works["Стены"][0],
            expected_category_id=scene.category_id,
            actor_id=scene.user.id,
        )

        _assert_old_pending_cancelled_new_pending(db_session, scene.context_id, old.request_hash)


class TestRefreshMembershipStatesDoesNotChangeTheInput:
    def test_marking_stale_leaves_the_fingerprint_and_the_queue_alone(self, db_session, factories):
        scene = _scene(db_session, factories, [("Стены", 2), ("Пол", 1)])
        _settle(db_session, scene.context_id)
        _stale_the_queue(db_session, scene)
        before = _snapshot(db_session, scene.context_id)
        hash_before = _current_hash(db_session, scene.context_id)
        jobs_before = _job_count(db_session)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == scene.chapter_ids["Стены"])
            .values(work_category_id=_leaf_category_id(db_session), category_source="file")
        )
        db_session.expire_all()

        report = refresh_membership_states(
            db_session, position_item_ids=[pid for ids in scene.works.values() for pid in ids]
        )

        assert report.marked_stale == 2
        assert _current_hash(db_session, scene.context_id) == hash_before
        assert _snapshot(db_session, scene.context_id) == before
        assert _job_count(db_session) == jobs_before


# ---------------------------------------------------------------------------
#  Вид, роль имени, правки семей
# ---------------------------------------------------------------------------

class TestKindPoints:
    def test_confirming_system_makes_the_context_inapplicable(self, db_session, factories):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        assert [j.status for j in _jobs(db_session, scene.context_id)] == [_PENDING]

        confirm_kind(db_session, context_id=scene.context_id, kind="SYSTEM", actor_id=scene.user.id)

        _assert_cancelled_not_applicable(db_session, scene.context_id)

    def test_removing_the_confirmation_revives_the_same_job(self, db_session, factories):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        confirm_kind(db_session, context_id=scene.context_id, kind="SYSTEM", actor_id=scene.user.id)
        _settle(db_session, scene.context_id)
        (cancelled,) = _jobs(db_session, scene.context_id)
        assert (cancelled.status, cancelled.cancel_reason) == (_CANCELLED, _NOT_APPLICABLE)
        job_id, generation = cancelled.id, cancelled.retry_generation

        unconfirm_kind(db_session, context_id=scene.context_id, actor_id=scene.user.id)

        (revived,) = _jobs(db_session, scene.context_id)
        assert revived.id == job_id
        assert revived.status == _PENDING
        assert revived.retry_generation == generation + 1


class TestNameRoleDoesNotChangeTheInput:
    def test_manual_role_leaves_the_fingerprint_and_the_queue_alone(self, db_session, factories):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        _stale_the_queue(db_session, scene)
        before = _snapshot(db_session, scene.context_id)
        hash_before = _current_hash(db_session, scene.context_id)
        jobs_before = _job_count(db_session)
        current_role = db_session.get(CatalogContext, scene.context_id).name_role
        other_role = next(r.value for r in NameRole if r.value != current_role)

        set_name_role(db_session, context_id=scene.context_id, role=other_role, actor_id=scene.user.id)

        assert db_session.get(CatalogContext, scene.context_id).name_role == other_role
        assert _current_hash(db_session, scene.context_id) == hash_before
        assert _snapshot(db_session, scene.context_id) == before
        assert _job_count(db_session) == jobs_before


class TestFamilyEditsQueueNothing:
    """Правки списка семей под инвариант не попадают (спека §2.7, решение 2):
    отпечаток меняется, задания нет — ручной перезапрос единицы."""

    def _edit_and_check(self, db, scene, edit, *, changes_the_input):
        before = _snapshot(db, scene.context_id)
        hash_before = _current_hash(db, scene.context_id)
        jobs_before = _job_count(db)

        edit()
        db.expire_all()

        if changes_the_input:
            assert _current_hash(db, scene.context_id) != hash_before
        assert _snapshot(db, scene.context_id) == before
        assert _job_count(db) == jobs_before

    def _settled(self, db, factories):
        scene = _scene(db, factories, [("Пол", 1)])
        _settle(db, scene.context_id)
        assert [j.status for j in _jobs(db, scene.context_id)] == [_PENDING]
        return scene

    def test_create_family(self, db_session, factories):
        """Черновик вход не меняет — очередь заранее устарела, иначе лишняя
        сверка здесь не оставила бы следа."""
        scene = self._settled(db_session, factories)
        _stale_the_queue(db_session, scene)
        self._edit_and_check(
            db_session,
            scene,
            lambda: create_family(
                db_session, title=f"Черновик {_uid()}", unit_name="M2", definition="Черновик",
                actor_id=scene.user.id,
            ),
            changes_the_input=False,
        )

    def test_activate_family(self, db_session, factories):
        scene = self._settled(db_session, factories)
        draft = create_family(
            db_session, title=f"Черновик {_uid()}", unit_name="M2", definition="Определение",
            actor_id=scene.user.id,
        )
        self._edit_and_check(
            db_session,
            scene,
            lambda: activate_family(db_session, family_id=draft.id, actor_id=scene.user.id),
            changes_the_input=True,
        )

    def test_update_family(self, db_session, factories):
        scene = self._settled(db_session, factories)
        self._edit_and_check(
            db_session,
            scene,
            lambda: update_family(
                db_session, family_id=scene.family.id, title=f"Новое имя {_uid()}",
                actor_id=scene.user.id,
            ),
            changes_the_input=True,
        )

    def test_archive_family(self, db_session, factories):
        scene = self._settled(db_session, factories)
        spare = _active_family(db_session, title=f"Запасная {_uid()}", unit_name="M2", actor_id=scene.user.id)
        _settle(db_session, scene.context_id)
        self._edit_and_check(
            db_session,
            scene,
            lambda: archive_family(db_session, family_id=spare.id, actor_id=scene.user.id),
            changes_the_input=True,
        )

    def test_merge_families(self, db_session, factories):
        scene = self._settled(db_session, factories)
        spare = _active_family(db_session, title=f"Запасная {_uid()}", unit_name="M2", actor_id=scene.user.id)
        _settle(db_session, scene.context_id)
        self._edit_and_check(
            db_session,
            scene,
            lambda: merge_families(
                db_session, source_family_id=spare.id, target_family_id=scene.family.id,
                actor_id=scene.user.id,
            ),
            changes_the_input=True,
        )


# ---------------------------------------------------------------------------
#  Review: слияние в позицию и смена `kind` строки
# ---------------------------------------------------------------------------

def _catalog_row(factories, *, kind, unit_id, title):
    return factories.CatalogPositionFactory.create(
        standard_job_title=title,
        normalized_job_title=normalize_job_title_with_lemmatization(title),
        kind=kind,
        unit_id=unit_id,
    )


def _fresh_proposal(factories):
    return factories.ProposalFactory.create(
        lot=factories.LotFactory.create(estimate=factories.EstimateFactory.create())
    )


def _unrouted_position(factories, *, catalog_position, chapter=None):
    kwargs = dict(
        proposal=chapter.proposal if chapter is not None else _fresh_proposal(factories),
        is_chapter=False,
        job_title_in_proposal=catalog_position.standard_job_title,
        catalog_position_id=catalog_position.id,
    )
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    return factories.PositionItemFactory.create(**kwargs)


def _chapter_row(factories, title):
    return factories.PositionItemFactory.create(
        proposal=_fresh_proposal(factories),
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal="1",
    )


class _ReviewScene:
    pass


def _review_scene(db, factories, *, source_members=1) -> _ReviewScene:
    """Цель — POSITION с одной работой в разделе «Пол»; источник — TO_REVIEW-строка
    с `source_members` работами в разделе «Стены»; единица `M2`, активная семья
    есть, очередь обоих контекстов приведена."""
    scene = _ReviewScene()
    scene.user = factories.UserFactory.create()
    _active_family(db, title=f"Семья {_uid()}", unit_name="M2", actor_id=scene.user.id)
    unit_id = _unit_id(db, "M2")
    scene.target = _catalog_row(
        factories, kind=CatalogKind.POSITION.value, unit_id=unit_id, title=f"Цель {_uid()}"
    )
    scene.source = _catalog_row(
        factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title=f"Источник {_uid()}"
    )
    floor = _chapter_row(factories, "Пол")
    walls = _chapter_row(factories, "Стены")
    target_item = _unrouted_position(factories, catalog_position=scene.target, chapter=floor)
    scene.target_context_id = route_position(db, position_item_id=target_item.id).context_id
    source_contexts = set()
    for _ in range(source_members):
        item = _unrouted_position(factories, catalog_position=scene.source, chapter=walls)
        source_contexts.add(route_position(db, position_item_id=item.id).context_id)
    assert len(source_contexts) == 1
    scene.source_context_id = source_contexts.pop()
    _settle(db, scene.target_context_id, scene.source_context_id)
    return scene


class TestReviewMergePoint:
    def test_source_context_is_archived_and_loses_its_pending(self, db_session, factories):
        scene = _review_scene(db_session, factories)
        assert [j.status for j in _jobs(db_session, scene.source_context_id)] == [_PENDING]

        merge_into_position(db_session, to_review_id=scene.source.id, target_id=scene.target.id)

        _assert_cancelled_not_applicable(db_session, scene.source_context_id)

    def test_target_context_gets_a_new_fingerprint_from_the_arrived_members(
        self, db_session, factories
    ):
        scene = _review_scene(db_session, factories, source_members=2)
        (old,) = _jobs(db_session, scene.target_context_id)

        merge_into_position(db_session, to_review_id=scene.source.id, target_id=scene.target.id)

        _assert_old_pending_cancelled_new_pending(
            db_session, scene.target_context_id, old.request_hash
        )


class TestReviewSetKindPoint:
    def test_header_makes_the_rows_context_inapplicable(self, db_session, factories):
        scene = _review_scene(db_session, factories)
        assert [j.status for j in _jobs(db_session, scene.source_context_id)] == [_PENDING]

        set_kind(db_session, to_review_id=scene.source.id, kind=CatalogKind.HEADER.value)

        _assert_cancelled_not_applicable(db_session, scene.source_context_id)

    def test_position_reconciles_the_rows_context(self, db_session, factories):
        """`POSITION` не меняет ни поля контекста, ни его применимость — сверка
        всё равно идёт (спека §2.7: смена `kind` строки), и устаревшее задание
        заменяется."""
        scene = _review_scene(db_session, factories)
        (old,) = _jobs(db_session, scene.source_context_id)
        _active_family(db_session, title=f"Вторая {_uid()}", unit_name="M2", actor_id=scene.user.id)
        db_session.expire_all()

        set_kind(db_session, to_review_id=scene.source.id, kind=CatalogKind.POSITION.value)

        _assert_old_pending_cancelled_new_pending(
            db_session, scene.source_context_id, old.request_hash
        )


# ---------------------------------------------------------------------------
#  Разовый проход: сверка каждой партии до её commit
# ---------------------------------------------------------------------------

def _committed_family(db, factories):
    user = factories.UserFactory.create()
    _active_family(db, title=f"Семья {_uid()}", unit_name="M2", actor_id=user.id)
    db.commit()
    return user


def _backfill_catalog_row(db, factories, title):
    return factories.CatalogPositionFactory.create(
        standard_job_title=title,
        normalized_job_title=normalize_job_title_with_lemmatization(title),
        unit_id=_unit_id(db, "M2"),
    )


def _contexts_of_positions(db, position_ids) -> list[int]:
    db.expire_all()
    return sorted(
        set(
            db.execute(
                sa.select(ContextMember.context_id).where(
                    ContextMember.position_item_id.in_(position_ids)
                )
            ).scalars()
        )
    )


class TestBackfillPoint:
    def _three_positions_two_contexts(self, db, factories):
        first = _backfill_catalog_row(db, factories, f"Кладка {_uid()}")
        second = _backfill_catalog_row(db, factories, f"Заливка {_uid()}")
        positions = [
            _unrouted_position(factories, catalog_position=first),
            _unrouted_position(factories, catalog_position=first),
            _unrouted_position(factories, catalog_position=second),
        ]
        db.commit()
        return [p.id for p in positions]

    def test_context_created_by_a_batch_has_pending_with_the_current_fingerprint(
        self, committing_db, committing_factories
    ):
        _committed_family(committing_db, committing_factories)
        position_ids = self._three_positions_two_contexts(committing_db, committing_factories)

        run_backfill(committing_db, batch_size=500)

        contexts = _contexts_of_positions(committing_db, position_ids)
        assert len(contexts) == 2
        for context_id in contexts:
            _assert_pending_with_current_hash(committing_db, context_id)

    def test_context_topped_up_by_a_later_batch_gets_a_new_fingerprint(
        self, committing_db, committing_factories
    ):
        """Контекст существовал до прохода, а партия сменила самый частый путь
        его членов: сверяются и пополненные контексты, не только созданные."""
        _committed_family(committing_db, committing_factories)
        row = _backfill_catalog_row(committing_db, committing_factories, f"Кладка {_uid()}")
        walls = _chapter_row(committing_factories, "Стены")
        floor = _chapter_row(committing_factories, "Пол")
        first = _unrouted_position(committing_factories, catalog_position=row, chapter=walls)
        committing_db.commit()
        run_backfill(committing_db, batch_size=500)
        (context_id,) = _contexts_of_positions(committing_db, [first.id])
        (old,) = _jobs(committing_db, context_id)

        for _ in range(3):
            _unrouted_position(committing_factories, catalog_position=row, chapter=floor)
        committing_db.commit()
        run_backfill(committing_db, batch_size=500)

        _assert_old_pending_cancelled_new_pending(committing_db, context_id, old.request_hash)

    def test_small_passed_cap_holds_the_batch(self, committing_db, committing_factories):
        _committed_family(committing_db, committing_factories)
        position_ids = self._three_positions_two_contexts(committing_db, committing_factories)

        run_backfill(
            committing_db,
            batch_size=500,
            event_cap=EventCap(max_contexts=1, max_reserve_usd=Decimal("1000")),
        )

        contexts = _contexts_of_positions(committing_db, position_ids)
        assert len(contexts) == 2
        assert all(_jobs(committing_db, context_id) == [] for context_id in contexts)
        (batch,) = committing_db.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert batch.status == ReconcileBatchStatus.held.value
        assert batch.source == "operation"
        assert batch.contexts_count == 2

    def test_call_without_the_parameter_reads_the_settings_cap_at_call_time(
        self, committing_db, committing_factories, monkeypatch
    ):
        _committed_family(committing_db, committing_factories)
        position_ids = self._three_positions_two_contexts(committing_db, committing_factories)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        run_backfill(committing_db, batch_size=500)

        contexts = _contexts_of_positions(committing_db, position_ids)
        assert all(_jobs(committing_db, context_id) == [] for context_id in contexts)
        (batch,) = committing_db.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert batch.status == ReconcileBatchStatus.held.value

    def test_passed_cap_wins_over_the_settings_cap(
        self, committing_db, committing_factories, monkeypatch
    ):
        _committed_family(committing_db, committing_factories)
        position_ids = self._three_positions_two_contexts(committing_db, committing_factories)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        run_backfill(
            committing_db,
            batch_size=500,
            event_cap=EventCap(max_contexts=100, max_reserve_usd=Decimal("1000")),
        )

        for context_id in _contexts_of_positions(committing_db, position_ids):
            _assert_pending_with_current_hash(committing_db, context_id)
        assert committing_db.execute(sa.select(SemanticReconcileBatch)).scalars().all() == []

    def test_each_batch_is_reconciled_before_its_own_commit(
        self, committing_db, committing_factories, monkeypatch
    ):
        _committed_family(committing_db, committing_factories)
        self._three_positions_two_contexts(committing_db, committing_factories)
        order = []
        real_reconcile = catalog_backfill_module.reconcile_semantic_jobs
        real_commit = committing_db.commit

        def _reconcile(*args, **kwargs):
            order.append("reconcile")
            return real_reconcile(*args, **kwargs)

        def _commit():
            order.append("commit")
            return real_commit()

        monkeypatch.setattr(catalog_backfill_module, "reconcile_semantic_jobs", _reconcile)
        monkeypatch.setattr(committing_db, "commit", _commit)

        run_backfill(committing_db, batch_size=1)

        # три партии по одной позиции; последний commit — после пересчёта ролей имени
        assert order == ["reconcile", "commit"] * 3 + ["commit"]


class TestRecomputeNameRolesDoesNotChangeTheInput:
    def test_role_recompute_leaves_the_fingerprint_and_the_queue_alone(
        self, committing_db, committing_factories
    ):
        user = _committed_family(committing_db, committing_factories)
        row = _backfill_catalog_row(committing_db, committing_factories, f"Кладка {_uid()}")
        position = _unrouted_position(committing_factories, catalog_position=row)
        committing_db.commit()
        run_backfill(committing_db, batch_size=500)
        (context_id,) = _contexts_of_positions(committing_db, [position.id])
        _active_family(committing_db, title=f"Вторая {_uid()}", unit_name="M2", actor_id=user.id)
        context = committing_db.get(CatalogContext, context_id)
        computed_role = context.name_role
        wrong_role = next(r.value for r in NameRole if r.value != computed_role)
        context.name_role = wrong_role
        context.place_dictionary_version = context.place_dictionary_version - 1
        committing_db.commit()
        before = _snapshot(committing_db, context_id)
        hash_before = _current_hash(committing_db, context_id)
        jobs_before = _job_count(committing_db)

        run_backfill(committing_db, batch_size=500)

        assert committing_db.get(CatalogContext, context_id).name_role == computed_role
        assert _current_hash(committing_db, context_id) == hash_before
        assert _snapshot(committing_db, context_id) == before
        assert _job_count(committing_db) == jobs_before


# ---------------------------------------------------------------------------
#  Цикл в цепочке разделов: контекст неприменим, сверка не падает
# ---------------------------------------------------------------------------

def _cyclic_context(db, factories, user) -> int:
    """Контекст с заданием `pending`, у которого потом появляется цикл разделов
    (фича 1 такие данные допускает)."""
    cp = _catalog_row(
        factories, kind=CatalogKind.POSITION.value, unit_id=_unit_id(db, "M2"), title=f"Цикл {_uid()}"
    )
    outer = _chapter_row(factories, "Внешний")
    inner = factories.PositionItemFactory.create(
        proposal=outer.proposal, is_chapter=True, job_title_in_proposal="Внутренний",
        chapter_item_id=outer.id,
    )
    item = _unrouted_position(factories, catalog_position=cp, chapter=inner)
    context_id = route_position(db, position_item_id=item.id).context_id
    _settle(db, context_id)
    assert [j.status for j in _jobs(db, context_id)] == [_PENDING]
    outer.chapter_item_id = inner.id
    db.flush()
    db.expire_all()
    return context_id


class TestChapterCycleIsInapplicable:
    def test_confirm_kind_commits_and_cancels_the_pending(self, db_session, factories):
        user = factories.UserFactory.create()
        _active_family(db_session, title=f"Семья {_uid()}", unit_name="M2", actor_id=user.id)
        context_id = _cyclic_context(db_session, factories, user)

        confirm_kind(db_session, context_id=context_id, kind="WORK", actor_id=user.id)

        assert db_session.get(CatalogContext, context_id).semantic_state == "CONFIRMED"
        _assert_cancelled_not_applicable(db_session, context_id)

    def test_assign_family_commits_and_cancels_the_pending(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(db_session, title=f"Семья {_uid()}", unit_name="M2", actor_id=user.id)
        context_id = _cyclic_context(db_session, factories, user)

        assign_family(db_session, context_id=context_id, family_id=family.id, actor_id=user.id)

        assert db_session.get(CatalogContext, context_id).work_family_id == family.id
        _assert_cancelled_not_applicable(db_session, context_id)

    def test_batch_with_a_good_and_a_cyclic_context_handles_both(self, db_session, factories):
        scene = _scene(db_session, factories, [("Пол", 1)])
        cyclic_id = _cyclic_context(db_session, factories, scene.user)

        report = reconcile_semantic_jobs(
            db_session, [scene.context_id, cyclic_id], cap=NO_CAP, source="operation"
        )
        db_session.expire_all()

        assert report.created == 1
        _assert_pending_with_current_hash(db_session, scene.context_id)
        _assert_cancelled_not_applicable(db_session, cyclic_id)

    def test_material_of_the_cyclic_context_has_no_path_the_good_one_keeps_its_own(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, [("Пол", 1)])
        cyclic_id = _cyclic_context(db_session, factories, scene.user)

        material = load_request_material(db_session, [scene.context_id, cyclic_id])

        assert (material[cyclic_id].path_broken, material[cyclic_id].path_counts) == (True, ())
        assert material[scene.context_id].path_broken is False
        assert material[scene.context_id].path_counts == (("Пол", 1),)

    def test_paths_without_a_cycle_are_read_in_one_call(self, db_session, factories, monkeypatch):
        """Поразделовый разбор — только после отказа общего вызова."""
        scene, a_id, b_id = _two_contexts(db_session, factories)
        calls = []
        real = semantic_request_module.chapter_paths

        def _spy(db, chapter_item_ids):
            calls.append(set(chapter_item_ids))
            return real(db, chapter_item_ids)

        monkeypatch.setattr(semantic_request_module, "chapter_paths", _spy)

        material = load_request_material(db_session, [a_id, b_id])

        assert calls == [set(scene.chapter_ids.values())]
        assert material[a_id].path_counts == (("Пол", 1),)
        assert material[b_id].path_counts == (("Стены", 2),)


# ---------------------------------------------------------------------------
#  Потолок события и источник пачки в точках операций
# ---------------------------------------------------------------------------

def _held_batches(db) -> list[SemanticReconcileBatch]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticReconcileBatch).where(
                SemanticReconcileBatch.status == ReconcileBatchStatus.held.value
            )
        ).scalars()
    )


def _pending(db, context_id) -> list[SemanticJob]:
    return [j for j in _jobs(db, context_id) if j.status == _PENDING]


class TestOperationPointsUseTheSettingsCap:
    """Каждая точка ставит задания с потолком события из настроек и источником
    `operation` (спека §2.11): при нулевом потолке постановка — удержанная пачка,
    отмены — сразу."""

    @staticmethod
    def _zero_cap(monkeypatch):
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

    @staticmethod
    def _assert_one_held_operation_batch(db):
        batches = _held_batches(db)
        assert [b.source for b in batches] == ["operation"]

    def test_context_operations(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, [("Стены", 2), ("Пол", 1)])
        _settle(db_session, scene.context_id)
        self._zero_cap(monkeypatch)

        new_context_id = _split_out(db_session, scene, "Стены")

        assert _pending(db_session, scene.context_id) == []
        assert _jobs(db_session, new_context_id) == []
        self._assert_one_held_operation_batch(db_session)

    def test_confirm_kind(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        _stale_the_queue(db_session, scene)
        self._zero_cap(monkeypatch)

        confirm_kind(db_session, context_id=scene.context_id, kind="WORK", actor_id=scene.user.id)

        assert _pending(db_session, scene.context_id) == []
        self._assert_one_held_operation_batch(db_session)

    def test_unconfirm_kind(self, db_session, factories, monkeypatch):
        scene = _scene(db_session, factories, [("Пол", 1)])
        _settle(db_session, scene.context_id)
        confirm_kind(db_session, context_id=scene.context_id, kind="SYSTEM", actor_id=scene.user.id)
        _settle(db_session, scene.context_id)
        self._zero_cap(monkeypatch)

        unconfirm_kind(db_session, context_id=scene.context_id, actor_id=scene.user.id)

        assert _pending(db_session, scene.context_id) == []
        self._assert_one_held_operation_batch(db_session)

    def test_review_merge(self, db_session, factories, monkeypatch):
        scene = _review_scene(db_session, factories, source_members=2)
        self._zero_cap(monkeypatch)

        merge_into_position(db_session, to_review_id=scene.source.id, target_id=scene.target.id)

        assert _pending(db_session, scene.target_context_id) == []
        self._assert_one_held_operation_batch(db_session)

    def test_review_set_kind(self, db_session, factories, monkeypatch):
        scene = _review_scene(db_session, factories)
        _active_family(db_session, title=f"Вторая {_uid()}", unit_name="M2", actor_id=scene.user.id)
        self._zero_cap(monkeypatch)

        set_kind(db_session, to_review_id=scene.source.id, kind=CatalogKind.POSITION.value)

        assert _pending(db_session, scene.source_context_id) == []
        self._assert_one_held_operation_batch(db_session)


# ---------------------------------------------------------------------------
#  Одна сверка на операцию над несколькими элементами
# ---------------------------------------------------------------------------

class TestDeferredReconcile:
    def _two_review_rows(self, db, factories):
        user = factories.UserFactory.create()
        _active_family(db, title=f"Семья {_uid()}", unit_name="M2", actor_id=user.id)
        rows = []
        contexts = []
        for _ in range(2):
            row = _catalog_row(
                factories, kind=CatalogKind.TO_REVIEW.value, unit_id=_unit_id(db, "M2"),
                title=f"Строка {_uid()}",
            )
            item = _unrouted_position(factories, catalog_position=row)
            contexts.append(route_position(db, position_item_id=item.id).context_id)
            rows.append(row)
        db.commit()
        return rows, contexts

    def test_batch_set_kind_over_the_event_cap_holds_the_batch(
        self, client, db_session, factories, monkeypatch
    ):
        rows, contexts = self._two_review_rows(db_session, factories)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        response = client.post(
            "/api/v1/review/batch-kind",
            json={"ids": [r.id for r in rows], "kind": CatalogKind.POSITION.value},
        )

        assert response.status_code == 200, response.text
        assert all(_jobs(db_session, context_id) == [] for context_id in contexts)
        (batch,) = db_session.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert batch.status == ReconcileBatchStatus.held.value
        assert batch.source == "operation"
        assert batch.contexts_count == 2

    def test_batch_set_kind_under_the_cap_queues_every_context(
        self, client, db_session, factories
    ):
        rows, contexts = self._two_review_rows(db_session, factories)

        response = client.post(
            "/api/v1/review/batch-kind",
            json={"ids": [r.id for r in rows], "kind": CatalogKind.POSITION.value},
        )

        assert response.status_code == 200, response.text
        for context_id in contexts:
            _assert_pending_with_current_hash(db_session, context_id)

    def test_single_set_kind_is_reconciled_immediately_under_the_cap(
        self, db_session, factories, monkeypatch
    ):
        rows, contexts = self._two_review_rows(db_session, factories)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)

        set_kind(db_session, to_review_id=rows[0].id, kind=CatalogKind.POSITION.value)

        _assert_pending_with_current_hash(db_session, contexts[0])
        assert db_session.execute(sa.select(SemanticReconcileBatch)).scalars().all() == []

    def test_exception_inside_the_block_leaves_no_state_for_the_next_call(
        self, db_session, factories
    ):
        rows, contexts = self._two_review_rows(db_session, factories)

        with pytest.raises(RuntimeError), semantic_reconcile_module.deferred_reconcile(db_session):
            set_kind(db_session, to_review_id=rows[0].id, kind=CatalogKind.POSITION.value)
            raise RuntimeError("сбой внутри блока")
        assert _jobs(db_session, contexts[0]) == []

        set_kind(db_session, to_review_id=rows[1].id, kind=CatalogKind.POSITION.value)

        _assert_pending_with_current_hash(db_session, contexts[1])
        assert _jobs(db_session, contexts[0]) == [], "накопленное отброшено, не подмешано"

    def test_nested_block_joins_the_outer_one(self, db_session, factories, monkeypatch):
        rows, contexts = self._two_review_rows(db_session, factories)
        calls = []
        real = semantic_reconcile_module.reconcile_semantic_jobs

        def _spy(db, context_ids, **kwargs):
            calls.append(set(context_ids))
            return real(db, context_ids, **kwargs)

        monkeypatch.setattr(semantic_reconcile_module, "reconcile_semantic_jobs", _spy)

        with semantic_reconcile_module.deferred_reconcile(db_session):
            with semantic_reconcile_module.deferred_reconcile(db_session):
                set_kind(db_session, to_review_id=rows[0].id, kind=CatalogKind.POSITION.value)
            assert calls == []
            set_kind(db_session, to_review_id=rows[1].id, kind=CatalogKind.POSITION.value)

        assert calls == [set(contexts)]

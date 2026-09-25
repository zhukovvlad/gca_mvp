"""Слияние в Review сводит корзины/контексты, конфликт решений и `warnings`;
переходы `set_kind` (спека `2026-09-22-catalog-families-design.md` §2.5,
§2.8, §2.14; план, задача 9).

Помощники — локальная копия набора `test_context_operations.py` (докстрока
того файла: наборы помощников тестов друг у друга не импортируют). Имя файла
несёт `review`, поэтому широкая команда `-k review` по всему
`tests/integration` растёт этим файлом — ожидаемо.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from unittest import mock

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.review as review_module
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    MembershipState,
    NameRole,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkCategory,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.context_operations import (
    REFUSE_INVALID_MEMBERSHIP,
    REFUSE_NOT_CONFLICTED,
    ContextOperationError,
    accept_target_decision,
    move_members,
)
from services.context_routing import PREDICATE_NEAREST_CHAPTER_EQUALS, route_position
from services.review import (
    MANUAL_KINDS,
    MergeOutcome,
    ReviewError,
    merge_into_position,
    merge_into_position_outcome,
    reconcile_contexts,
    set_kind,
)
from services.unit_resolution import UnitResolver
from services.work_families import activate_family, assign_family, confirm_kind, create_family

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники (локальная копия — см. докстроку модуля)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chapter(factories, proposal, *, title="Раздел", chapter_number="1", category_id=None):
    kwargs = dict(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal=chapter_number,
    )
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = "file"
    return factories.PositionItemFactory.create(**kwargs)


def _position(factories, proposal, *, chapter=None, title="Работа", catalog_position=None):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _leaf_category_ids(session, n=1) -> list[int]:
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return (
        session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(n)
        )
        .scalars()
        .all()
    )


def _catalog_row(factories, *, kind, unit_id=None, title):
    return factories.CatalogPositionFactory.create(
        standard_job_title=title,
        normalized_job_title=normalize_job_title_with_lemmatization(title),
        kind=kind,
        unit_id=unit_id,
    )


def _routed_position(db, factories, *, catalog_position, chapter=None, title="Позиция"):
    proposal = _proposal(factories)
    item = _position(factories, proposal, chapter=chapter, catalog_position=catalog_position, title=title)
    member = route_position(db, position_item_id=item.id)
    return item, member


def _active_family(db, *, title, unit_name, actor_id, definition="Определение"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id)
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


def _member(db, position_item_id) -> ContextMember:
    return db.get(ContextMember, position_item_id)


def _context(db, context_id) -> CatalogContext:
    return db.get(CatalogContext, context_id)


def _bucket_exists(db, bucket_id) -> bool:
    """Существование корзины — ПРЯМЫМ запросом, а не `db.get()`: Core-style
    `DELETE` (как в `_delete_emptied_bucket`) не синхронизирует identity map
    сессии, и `db.get()` на уже загруженный объект вернул бы устаревший
    закэшированный экземпляр вместо `None`."""
    return (
        db.execute(sa.select(sa.func.count()).select_from(ContextBucket).where(ContextBucket.id == bucket_id))
        .scalar_one()
        > 0
    )


def _context_events(db, context_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.context_id == context_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return db.execute(stmt).scalars().all()


class Scene:
    """Контейнер сцены: одна корзина у источника, одна у цели, обе — с
    одной позицией, одинаковая единица (нужна для `assign_family`)."""


def _simple_scene(db, factories, *, unit_code="M2") -> Scene:
    unit_id = _unit_id(db, unit_code)
    target_cp = _catalog_row(
        factories, kind=CatalogKind.POSITION.value, unit_id=unit_id, title=f"Цель {_uid()}"
    )
    source_cp = _catalog_row(
        factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title=f"Источник {_uid()}"
    )
    target_item, target_member = _routed_position(
        db, factories, catalog_position=target_cp, title="Целевая позиция"
    )
    source_item, source_member = _routed_position(
        db, factories, catalog_position=source_cp, title="Исходная позиция"
    )

    scene = Scene()
    scene.unit_id = unit_id
    scene.target_cp_id = target_cp.id
    scene.source_cp_id = source_cp.id
    scene.target_item_id = target_item.id
    scene.source_item_id = source_item.id
    scene.target_context_id = target_member.context_id
    scene.source_context_id = source_member.context_id
    scene.target_bucket_id = target_member.bucket_id
    scene.source_bucket_id = source_member.bucket_id
    return scene


# ---------------------------------------------------------------------------
#  Слияние: простой путь — одна корзина с каждой стороны, конфликтов нет
# ---------------------------------------------------------------------------

class TestSimpleMerge:
    def test_merge_reconciles_buckets_contexts_and_members(self, db_session, factories):
        scene = _simple_scene(db_session, factories)

        outcome = merge_into_position(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome == 1

        # Позиция источника теперь в контексте цели, в её корзине.
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.context_id == scene.target_context_id
        assert moved_member.bucket_id == scene.target_bucket_id
        assert moved_member.membership_state == MembershipState.CURRENT.value
        assert moved_member.conflict_at is None
        assert moved_member.conflict_from_context_id is None

        # Контекст источника пережил слияние — архивирован и перевешен, не удалён.
        source_ctx = _context(db_session, scene.source_context_id)
        assert source_ctx is not None
        assert source_ctx.archived_at is not None
        assert source_ctx.bucket_id == scene.target_bucket_id

        # Корзина источника удалена.
        assert not _bucket_exists(db_session, scene.source_bucket_id)

        # Каталожная строка источника удалена (шаг 4 прошёл).
        assert db_session.get(CatalogPosition, scene.source_cp_id) is None

        # Журнал: ровно один context_archived(reason=review_merge) на источник,
        # ровно один members_moved(reason=review_merge) на цель, ноль
        # routing_rules_dropped (правил не было).
        archived_events = _context_events(db_session, scene.source_context_id, "context_archived")
        assert len(archived_events) == 1
        assert archived_events[0].payload == {"reason": "review_merge"}

        moved_events = _context_events(db_session, scene.target_context_id, "members_moved")
        assert len(moved_events) == 1
        assert moved_events[0].payload == {
            "from_context_id": scene.source_context_id,
            "moved_members": 1,
            "reason": "review_merge",
        }

        dropped = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(SemanticEvent.event_type == "routing_rules_dropped")
        ).scalar_one()
        assert dropped == 0

    def test_merge_result_has_empty_warnings_when_decisions_do_not_diverge(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories)
        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert isinstance(outcome, MergeOutcome)
        assert outcome.moved_positions == 1
        assert outcome.warnings == []

    def test_merge_without_any_bucket_is_a_no_op_for_reconcile(self, db_session, factories):
        """Существующие сценарии Review (задачи 1–8) не маршрутизируют
        позиции — источник и цель без единой корзины. `reconcile_contexts`
        обязана быть no-op, иначе регресс `test_review*.py`/`test_matching.py`."""
        unit_id = _unit_id(db_session, "M2")
        target_cp = _catalog_row(
            factories, kind=CatalogKind.POSITION.value, unit_id=unit_id, title=f"Цель {_uid()}"
        )
        source_cp = _catalog_row(
            factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title=f"Источник {_uid()}"
        )
        warnings = reconcile_contexts(
            db_session, source_position_id=source_cp.id, target_position_id=target_cp.id
        )
        assert warnings == []


class TestRoutingRulesDropped:
    def test_two_rules_give_one_event_with_count_bucket_and_the_live_default_as_subject(
        self, db_session, factories
    ):
        """Спека §2.14: `routing_rules_dropped` — предмет контекст, ключи
        `bucket_id`, `count`, `reason`. Предмет — действующий контекст по
        умолчанию исходной корзины ДО слияния (§2.14).
        Вход — ДВА правила (не одно и не ноль): число обязано быть счётом, а
        не константой."""
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        # Правила синтаксически валидны (предикат проверяется формой при
        # создании через split_context, здесь заводятся напрямую — как и
        # предикат `evaluate_predicate` его бы разобрал), но заведомо не
        # совпадают ни с чьей цепочкой разделов — сработать они не должны:
        # тест проверяет, что их ОТБРОСИЛИ при слиянии, не то, что они
        # маршрутизируют.
        rule_1 = ContextRoutingRule(
            bucket_id=scene.source_bucket_id,
            ordinal=1,
            predicate={"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Не тот раздел 1"},
            context_id=scene.source_context_id,
            created_by=user.id,
        )
        rule_2 = ContextRoutingRule(
            bucket_id=scene.source_bucket_id,
            ordinal=2,
            predicate={"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Не тот раздел 2"},
            context_id=scene.source_context_id,
            created_by=user.id,
        )
        db_session.add_all([rule_1, rule_2])
        db_session.flush()

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome.moved_positions == 1

        events = db_session.execute(
            sa.select(SemanticEvent).where(SemanticEvent.event_type == "routing_rules_dropped")
        ).scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.context_id == scene.source_context_id  # действующий default ДО слияния
        assert event.payload == {
            "bucket_id": scene.source_bucket_id,
            "count": 2,
            "reason": "review_merge",
        }

        # Правила исходной корзины ушли каскадом вместе с корзиной.
        remaining = db_session.execute(
            sa.select(sa.func.count())
            .select_from(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == scene.source_bucket_id)
        ).scalar_one()
        assert remaining == 0


class TestManualMembershipDuringMerge:
    """Записано в докстроке модуля `services/review.py`
    и в докстроке `_route_member_into_bucket`: членство `routed_by='manual'`
    исходной корзины перемаршрутизируется в цель ОБЫЧНОЙ маршрутизацией, а не
    остаётся в своём (архивируемом) контексте."""

    def test_manual_member_is_rerouted_into_the_target_and_counted(self, db_session, factories):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        move_members(
            db_session,
            position_item_ids=[scene.source_item_id],
            target_context_id=scene.source_context_id,
            actor_id=user.id,
            reason="manual",
        )
        primed = _member(db_session, scene.source_item_id)
        assert primed.routed_by == RoutedBy.manual.value

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome.moved_positions == 1

        moved_member = _member(db_session, scene.source_item_id)
        # Цель без правил — маршрутизация обычная даёт действующее умолчание,
        # `routed_by='manual'` источника не сохраняется (его адресат
        # архивируется слиянием).
        assert moved_member.context_id == scene.target_context_id
        assert moved_member.bucket_id == scene.target_bucket_id
        assert moved_member.routed_by == RoutedBy.default.value
        assert moved_member.routing_rule_id is None

        events = _context_events(db_session, scene.target_context_id, "members_moved")
        assert len(events) == 1
        assert events[0].payload == {
            "from_context_id": scene.source_context_id,
            "moved_members": 1,
            "reason": "review_merge",
        }


class TestStaleMembershipDuringMerge:
    """Устаревшее (`STALE`) членство исходной корзины — эффективная статья
    позиции разошлась с `work_category_id` корзины после ручного разноса —
    переносится в корзину цели той же ОБЫЧНОЙ маршрутизацией, что и
    `CURRENT`, и остаётся `STALE`: перенос по НОВОЙ статье слиянием не
    делается — это отдельное решение оператора (`accept_transfer`).
    Параметризовано по `routed_by` источника: до слияния оба варианта
    (правило/умолчание и ручной выбор) обязаны вести себя одинаково."""

    @pytest.mark.parametrize("mark_manual", [False, True])
    def test_stale_member_stays_stale_in_the_target_bucket(self, db_session, factories, mark_manual):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        if mark_manual:
            # Тот же приём, что `TestManualMembershipDuringMerge`: перенос в
            # СВОЙ ЖЕ контекст меняет только `routed_by` на `manual`.
            move_members(
                db_session,
                position_item_ids=[scene.source_item_id],
                target_context_id=scene.source_context_id,
                actor_id=user.id,
                reason="manual",
            )
        member = _member(db_session, scene.source_item_id)
        expected_routed_by = RoutedBy.manual.value if mark_manual else member.routed_by
        assert member.routed_by == expected_routed_by
        member.membership_state = MembershipState.STALE.value
        db_session.flush()

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome.moved_positions == 1

        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.context_id == scene.target_context_id
        assert moved_member.bucket_id == scene.target_bucket_id
        assert moved_member.membership_state == MembershipState.STALE.value

        # Ни одной лишней корзины: членство осталось ВНУТРИ уже определённой
        # и заблокированной корзины цели, а не уехало по текущей эффективной
        # статье позиции в другую (не взятую под замок слияния) корзину.
        bucket_count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(ContextBucket)
            .where(ContextBucket.catalog_position_id == scene.target_cp_id)
        ).scalar_one()
        assert bucket_count == 1


class TestMergeCreatesTargetBucketWithRepresentativeChain:
    def test_fresh_target_default_context_keeps_work_title_under_a_chapter(
        self, db_session, factories
    ):
        """Слияние, заводящее НОВУЮ корзину цели (статья источника ещё не
        встречалась у цели), читает роль имени её свежего контекста по
        умолчанию по цепочке ПРЕДСТАВИТЕЛЬНОГО членства корзины источника, не
        по пустой — иначе `LOCATION_ONLY` под рабочим разделом теряет
        рабочее имя."""
        unit_id = _unit_id(db_session, "M2")
        # Роль имени НОВОГО контекста цели классифицируется по СОБСТВЕННОМУ
        # `standard_job_title` ЦЕЛЕВОЙ каталожной строки (`target_cp`) —
        # `chain` лишь подсказывает рабочий раздел рядом; для проверки
        # `LOCATION_ONLY` имя цели ТОЖЕ обязано быть местом, тем же текстом,
        # что и переносимая позиция источника.
        target_cp = _catalog_row(
            factories, kind=CatalogKind.POSITION.value, unit_id=unit_id, title="Корпус 1"
        )
        # Источник — та же роль-достойная форма имени (классифицируется
        # независимо от цели, СОБСТВЕННЫМ `standard_job_title` + цепочкой
        # своего раздела) — предпосылка теста нужна на ОБОИХ; название другое
        # ("Корпус 2"), чтобы не столкнуться с target_cp по уникальному
        # индексу (написание × единица).
        source_cp = _catalog_row(
            factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title="Корпус 2"
        )
        # Целевая позиция роутится обычным путём — её корзина у target_cp
        # несёт `work_category_id=None`.
        target_item, _target_member = _routed_position(
            db_session, factories, catalog_position=target_cp, title="Целевая позиция"
        )

        cat_a = _leaf_category_ids(db_session, 1)[0]
        source_proposal = _proposal(factories)
        source_chapter = _chapter(
            factories, source_proposal, title="Монтаж витражей", category_id=cat_a
        )
        source_item = _position(
            factories, source_proposal, chapter=source_chapter, catalog_position=source_cp,
            title="Корпус 1",
        )
        source_member = route_position(db_session, position_item_id=source_item.id)
        source_bucket = db_session.get(ContextBucket, source_member.bucket_id)
        assert source_bucket.work_category_id == cat_a  # предпосылка — своя статья, не None
        source_ctx = db_session.get(CatalogContext, source_member.context_id)
        assert source_ctx.name_role == NameRole.LOCATION_ONLY.value  # предпосылка
        assert source_ctx.comparability_reason is None  # рабочий раздел найден при создании

        outcome = merge_into_position_outcome(
            db_session, to_review_id=source_cp.id, target_id=target_cp.id
        )
        assert outcome.moved_positions == 1

        moved_member = _member(db_session, source_item.id)
        # Новая корзина цели — статья cat_a у target_cp встречается впервые.
        assert moved_member.bucket_id != _member(db_session, target_item.id).bucket_id
        new_bucket = db_session.get(ContextBucket, moved_member.bucket_id)
        assert new_bucket.catalog_position_id == target_cp.id
        assert new_bucket.work_category_id == cat_a

        new_context = _context(db_session, moved_member.context_id)
        assert new_context.name_role == NameRole.LOCATION_ONLY.value
        assert new_context.comparability_reason is None


# ---------------------------------------------------------------------------
#  Конфликт: разные назначенные семьи
# ---------------------------------------------------------------------------

class TestConflictFamilies:
    def test_different_assigned_families_flag_the_transferred_membership(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)

        source_family = _active_family(
            db_session, title=f"Источник-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        target_family = _active_family(
            db_session, title=f"Цель-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.target_context_id, family_id=target_family.id, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )

        assert len(outcome.warnings) == 1
        assert source_family.title in outcome.warnings[0]
        assert target_family.title in outcome.warnings[0]

        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is not None
        assert moved_member.conflict_from_context_id == scene.source_context_id
        # membership_state НЕ трогается конфликтом — отдельное утверждение.
        assert moved_member.membership_state == MembershipState.CURRENT.value
        assert moved_member.context_id == scene.target_context_id

    def test_same_assigned_family_gives_empty_warnings_and_no_conflict(self, db_session, factories):
        """Совпадающие решения — пустой `warnings` И пустой `conflict_at`
        (вторая сторона утверждения о конфликте)."""
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)

        shared_family = _active_family(
            db_session, title=f"Общая семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=shared_family.id, actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.target_context_id, family_id=shared_family.id, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )

        assert outcome.warnings == []
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is None
        assert moved_member.conflict_from_context_id is None

    def test_family_on_only_one_side_is_not_a_conflict(self, db_session, factories):
        """Отрицательная сторона правила: конфликт требует семьи у ОБЕИХ
        сторон разом (спека §2.8 — «разные назначенные семьи», а не «семья
        назначена хотя бы у одной»). Семья только у источника, цель без
        семьи — не конфликт."""
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        source_family = _active_family(
            db_session, title=f"Только источник {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )

        assert outcome.warnings == []
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is None
        assert moved_member.conflict_from_context_id is None


# ---------------------------------------------------------------------------
#  Конфликт: разные подтверждённые виды — ОТДЕЛЬНЫЙ вход (правило через ИЛИ)
# ---------------------------------------------------------------------------

class TestConflictConfirmedKinds:
    def test_different_confirmed_kinds_without_families_flag_the_membership(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)

        confirm_kind(
            db_session, context_id=scene.source_context_id, kind=SemanticKind.SYSTEM.value, actor_id=user.id
        )
        confirm_kind(
            db_session, context_id=scene.target_context_id, kind=SemanticKind.WORK.value, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )

        assert len(outcome.warnings) == 1
        assert SemanticKind.SYSTEM.value in outcome.warnings[0]
        assert SemanticKind.WORK.value in outcome.warnings[0]

        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is not None
        assert moved_member.conflict_from_context_id == scene.source_context_id

        # Ни у одного контекста семьи не было — конфликт строго по видовой ветке.
        assert _context(db_session, scene.source_context_id).work_family_id is None

    def test_unconfirmed_kind_mismatch_is_not_a_conflict(self, db_session, factories):
        """Правило — через ИЛИ, и его видовая половина требует `manual`:
        неподтверждённое (`source='rule'`) расхождение вида НЕ конфликт —
        предъявлено на ВХОДЕ, где вид РЕАЛЬНО разный (компл → SYSTEM, M2 →
        WORK), а не на тождественном входе, которому расходиться нечем
        (прежняя редакция этого теста была ложно-зелёной: у обеих сторон
        был WORK, и тест доказывал «равные виды не конфликт», а не
        заявленное)."""
        target_unit_id = _unit_id(db_session, "M2")
        source_unit_id = _unit_id(db_session, "компл")
        target_cp = _catalog_row(
            factories, kind=CatalogKind.POSITION.value, unit_id=target_unit_id, title=f"Цель {_uid()}"
        )
        source_cp = _catalog_row(
            factories, kind=CatalogKind.TO_REVIEW.value, unit_id=source_unit_id, title=f"Источник {_uid()}"
        )
        target_item, target_member = _routed_position(db_session, factories, catalog_position=target_cp)
        source_item, source_member = _routed_position(db_session, factories, catalog_position=source_cp)

        source_ctx = _context(db_session, source_member.context_id)
        target_ctx = _context(db_session, target_member.context_id)
        assert source_ctx.semantic_kind_source == DecisionSource.rule.value
        assert target_ctx.semantic_kind_source == DecisionSource.rule.value
        # Предпосылка: вид РЕАЛЬНО разный, а не подделан руками.
        assert source_ctx.semantic_kind == SemanticKind.SYSTEM.value
        assert target_ctx.semantic_kind == SemanticKind.WORK.value

        outcome = merge_into_position_outcome(
            db_session, to_review_id=source_cp.id, target_id=target_cp.id
        )
        assert outcome.warnings == []
        moved_member = _member(db_session, source_item.id)
        assert moved_member.conflict_at is None

    def test_manual_source_against_non_manual_target_is_not_a_conflict(self, db_session, factories):
        """Отрицательная сторона: правило требует `manual` у ОБЕИХ сторон.
        Подтверждённый вид источника против НЕподтверждённого (`source='rule'`,
        значение по умолчанию) вида цели — не конфликт, даже если сами виды
        разошлись (иначе требование `manual` держалось бы только одной
        стороной незамеченно)."""
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        confirm_kind(
            db_session, context_id=scene.source_context_id, kind=SemanticKind.SYSTEM.value, actor_id=user.id
        )
        target_ctx = _context(db_session, scene.target_context_id)
        assert target_ctx.semantic_kind_source == DecisionSource.rule.value
        assert target_ctx.semantic_kind == SemanticKind.WORK.value

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome.warnings == []
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is None

    def test_same_confirmed_kind_gives_empty_warnings_and_no_conflict(self, db_session, factories):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        confirm_kind(
            db_session, context_id=scene.source_context_id, kind=SemanticKind.SYSTEM.value, actor_id=user.id
        )
        confirm_kind(
            db_session, context_id=scene.target_context_id, kind=SemanticKind.SYSTEM.value, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        assert outcome.warnings == []
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is None
        assert moved_member.conflict_from_context_id is None


# ---------------------------------------------------------------------------
#  Строка с двумя корзинами в цель с одной
# ---------------------------------------------------------------------------

class TestTwoSourceBucketsIntoOneTargetBucket:
    def test_second_target_bucket_is_created_and_both_sources_are_cleaned_up(
        self, db_session, factories
    ):
        (category_id,) = _leaf_category_ids(db_session, 1)
        unit_id = _unit_id(db_session, "M2")

        target_cp = _catalog_row(
            factories, kind=CatalogKind.POSITION.value, unit_id=unit_id, title=f"Цель {_uid()}"
        )
        source_cp = _catalog_row(
            factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title=f"Источник {_uid()}"
        )
        target_cp_id = target_cp.id
        source_cp_id = source_cp.id

        # Цель: одна существующая корзина (без статьи).
        target_item, target_member = _routed_position(
            db_session, factories, catalog_position=target_cp, title="Целевая позиция"
        )
        # Источник: корзина БЕЗ статьи (найдёт существующую корзину цели) и
        # корзина СО статьёй (заведёт цели новую).
        source_proposal = _proposal(factories)
        source_item_plain = _position(
            factories, source_proposal, catalog_position=source_cp, title="Исходная без статьи"
        )
        source_member_plain = route_position(db_session, position_item_id=source_item_plain.id)

        chapter = _chapter(factories, source_proposal, category_id=category_id)
        source_item_categorized = _position(
            factories, source_proposal, chapter=chapter, catalog_position=source_cp, title="Исходная со статьёй"
        )
        source_member_categorized = route_position(
            db_session, position_item_id=source_item_categorized.id
        )

        # Захватываем ОБЫЧНЫМИ int'ами ДО слияния: `route_position`/
        # `_apply_routing` мутируют ЭТИ ЖЕ ORM-объекты (identity map отдаёт
        # ту же ссылку `db.get(ContextMember, ...)`), поэтому после
        # `merge_into_position` их атрибуты уже показывают НОВОЕ (целевое)
        # состояние — сравнивать «до» пришлось бы с ними же.
        source_bucket_plain_id = source_member_plain.bucket_id
        source_bucket_categorized_id = source_member_categorized.bucket_id
        source_context_plain_id = source_member_plain.context_id
        source_context_categorized_id = source_member_categorized.context_id
        assert source_bucket_plain_id != source_bucket_categorized_id

        target_buckets_before = db_session.execute(
            sa.select(sa.func.count())
            .select_from(ContextBucket)
            .where(ContextBucket.catalog_position_id == target_cp_id)
        ).scalar_one()
        assert target_buckets_before == 1

        outcome = merge_into_position(
            db_session, to_review_id=source_cp_id, target_id=target_cp_id
        )
        assert outcome == 2

        target_buckets_after = db_session.execute(
            sa.select(ContextBucket).where(ContextBucket.catalog_position_id == target_cp_id)
        ).scalars().all()
        assert len(target_buckets_after) == 2  # вторая корзина цели создана

        # Обе исходные корзины удалены.
        assert not _bucket_exists(db_session, source_bucket_plain_id)
        assert not _bucket_exists(db_session, source_bucket_categorized_id)

        # Соответствие корзин — ПО СТАТЬЕ, а не «любая из двух»:
        # без-статейный источник обязан лечь ИМЕННО на
        # без-статейную корзину цели, категоризованный — ИМЕННО на корзину
        # цели ЭТОЙ СТАТЬИ, а не наоборот (предикат `in target_bucket_ids`
        # пропустил бы перевес на чужую корзину цели).
        target_bucket_by_category = {
            bucket.work_category_id: bucket.id for bucket in target_buckets_after
        }
        assert target_bucket_by_category.keys() == {None, category_id}
        target_bucket_plain_id = target_bucket_by_category[None]
        target_bucket_categorized_id = target_bucket_by_category[category_id]

        # Оба исходных контекста архивны и висят РОВНО на СВОЕЙ корзине цели.
        plain_ctx = _context(db_session, source_context_plain_id)
        categorized_ctx = _context(db_session, source_context_categorized_id)
        assert plain_ctx.archived_at is not None
        assert plain_ctx.bucket_id == target_bucket_plain_id
        assert categorized_ctx.archived_at is not None
        assert categorized_ctx.bucket_id == target_bucket_categorized_id

        # Их журнал (context_archived) читается ПОСЛЕ слияния.
        for original_context_id in (source_context_plain_id, source_context_categorized_id):
            events = _context_events(db_session, original_context_id, "context_archived")
            assert len(events) == 1

        # Обе позиции теперь членства РОВНО на своей корзине цели.
        moved_plain = _member(db_session, source_item_plain.id)
        moved_categorized = _member(db_session, source_item_categorized.id)
        assert moved_plain.bucket_id == target_bucket_plain_id
        assert moved_categorized.bucket_id == target_bucket_categorized_id

        # DELETE каталожной строки прошёл.
        assert not db_session.execute(
            sa.select(sa.func.count()).select_from(CatalogPosition).where(CatalogPosition.id == source_cp_id)
        ).scalar_one()


# ---------------------------------------------------------------------------
#  Три звена RESTRICT — снимаются ПО ОТДЕЛЬНОСТИ
# ---------------------------------------------------------------------------

def _fk_violation(db_session, exc_info) -> psycopg.errors.ForeignKeyViolation | psycopg.errors.UniqueViolation:
    orig = getattr(exc_info.value, "orig", exc_info.value)
    db_session.rollback()
    return orig


class TestRestrictChainRemovedLinkByLink:
    """Каждый тест снимает РОВНО ОДНО звено (мокая один из внутренних шагов
    `reconcile_contexts` no-op'ом), оставляя два других действовать реально —
    три РАЗНЫХ падения на трёх РАЗНЫХ ограничениях, проверяемых по
    `e.orig.diag.constraint_name`, а не просто по типу `IntegrityError`."""

    def test_without_member_transfer_delete_fails_on_membership_restrict(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories)
        with mock.patch.object(
            review_module, "_transfer_members", return_value=({}, [])
        ), pytest.raises(Exception) as exc_info:
            merge_into_position(
                db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
            )
        orig = _fk_violation(db_session, exc_info)
        assert isinstance(orig, psycopg.errors.ForeignKeyViolation)
        assert orig.diag.constraint_name == "fk_context_members_bucket_context"

    def test_without_context_reweigh_delete_fails_on_context_restrict(self, db_session, factories):
        scene = _simple_scene(db_session, factories)
        with mock.patch.object(
            review_module, "_reweigh_contexts", return_value=None
        ), pytest.raises(Exception) as exc_info:
            merge_into_position(
                db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
            )
        orig = _fk_violation(db_session, exc_info)
        assert isinstance(orig, psycopg.errors.ForeignKeyViolation)
        assert orig.diag.constraint_name == "fk_catalog_contexts_bucket_id"

    def test_without_bucket_deletion_delete_fails_on_bucket_restrict(self, db_session, factories):
        scene = _simple_scene(db_session, factories)
        with mock.patch.object(
            review_module, "_delete_emptied_bucket", return_value=None
        ), pytest.raises(Exception) as exc_info:
            merge_into_position(
                db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
            )
        orig = _fk_violation(db_session, exc_info)
        assert isinstance(orig, psycopg.errors.ForeignKeyViolation)
        assert orig.diag.constraint_name == "fk_context_buckets_catalog_position_id"


class TestArchiveBeforeReweighOrder:
    """Снятие порядка «сперва архивировать, потом перевешивать» обязано
    упасть на частичном уникальном индексе умолчания корзины, а не на
    membership-цепочке."""

    def test_reweigh_without_archive_hits_the_default_partial_unique_index(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories)
        with mock.patch.object(
            review_module, "_archive_contexts", return_value=None
        ), pytest.raises(Exception) as exc_info:
            merge_into_position(
                db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
            )
        orig = _fk_violation(db_session, exc_info)
        assert isinstance(orig, psycopg.errors.UniqueViolation)
        assert orig.diag.constraint_name == "uq_catalog_contexts_default_per_bucket"


# ---------------------------------------------------------------------------
#  Локи корзин — компиляцией РЕАЛЬНОГО SQL (уроки задач 4–8)
# ---------------------------------------------------------------------------

class _CapturingSQL:
    """Перехватывает и текст, и связанные параметры каждого запроса —
    режим/порядок локов проверяется текстом, а СОСТАВ заблокированных id
    («набор замков без корзины цели» проходил бы
    незамеченным, если смотреть только текст) — параметрами."""

    def __init__(self, session):
        self.session = session
        self.statements: list[str] = []
        self.parameters: list[object] = []

    def __enter__(self):
        def _listener(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(statement)
            self.parameters.append(parameters)

        self._listener = _listener
        self._connection = self.session.connection()
        event.listen(self._connection, "before_cursor_execute", _listener)
        return self

    def __exit__(self, *exc):
        event.remove(self._connection, "before_cursor_execute", self._listener)


def _bound_values(parameters: object) -> set[object]:
    """Плоское множество значений связанных параметров — не важно, психопг
    прислал их именованным словарём или позиционным кортежем/списком."""
    if isinstance(parameters, dict):
        return set(parameters.values())
    if isinstance(parameters, list | tuple):
        return set(parameters)
    return {parameters}


class TestBucketLockCompiledSQL:
    def test_reconcile_takes_exclusive_lock_ascending_by_id_after_the_catalog_row_lock(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories)
        # Целевая корзина уже СУЩЕСТВУЕТ до слияния (создана `_simple_scene`
        # маршрутизацией целевой позиции) — это ЕЁ id обязан войти в набор
        # локов, а не только id источника: набор без
        # цели проходил бы этот тест незамеченным, если бы он смотрел только
        # текст SQL, а не связанные параметры.
        target_bucket_id_before = scene.target_bucket_id

        with _CapturingSQL(db_session) as capture:
            merge_into_position(
                db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
            )
        statements = capture.statements

        catalog_lock_index = next(
            index
            for index, statement in enumerate(statements)
            if "catalog_positions" in statement and " FOR UPDATE" in statement
        )
        bucket_lock_index, bucket_lock_statement = next(
            (index, statement)
            for index, statement in enumerate(statements)
            if "context_buckets" in statement and (" FOR UPDATE" in statement or " FOR SHARE" in statement)
        )
        assert " FOR UPDATE" in bucket_lock_statement
        assert " FOR SHARE" not in bucket_lock_statement
        assert "NO KEY" not in bucket_lock_statement
        assert "ORDER BY context_buckets.id" in bucket_lock_statement
        # Порядок: каталожные строки → корзины — сравнение по РЕАЛЬНО
        # испущенным запросам, а не по намерению кода.
        assert catalog_lock_index < bucket_lock_index

        bound = _bound_values(capture.parameters[bucket_lock_index])
        assert scene.source_bucket_id in bound
        assert target_bucket_id_before in bound


# ---------------------------------------------------------------------------
#  `set_kind`: переходы контекстов (спека §2.5, таблица)
# ---------------------------------------------------------------------------

class TestSetKindContextTransitions:
    def test_header_marks_all_contexts_of_the_row_not_applicable_and_keeps_memberships(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories, unit_code="M2")
        # Источник берём как TO_REVIEW-строку без слияния — просто ставим kind.
        set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.HEADER.value)

        ctx = _context(db_session, scene.source_context_id)
        assert ctx.semantic_state == SemanticState.NOT_APPLICABLE.value

        # Членство цело.
        assert db_session.get(ContextMember, scene.source_item_id) is not None
        assert _member(db_session, scene.source_item_id).context_id == scene.source_context_id

    def test_trash_marks_all_contexts_of_the_row_not_applicable_separately_from_header(
        self, db_session, factories
    ):
        """Отдельный вход, а не «HEADER или TRASH одним»."""
        scene = _simple_scene(db_session, factories, unit_code="M2")
        set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.TRASH.value)

        ctx = _context(db_session, scene.source_context_id)
        assert ctx.semantic_state == SemanticState.NOT_APPLICABLE.value
        assert db_session.get(ContextMember, scene.source_item_id) is not None

    def test_header_marks_every_bucket_of_a_row_with_two_buckets(self, db_session, factories):
        (category_id,) = _leaf_category_ids(db_session, 1)
        unit_id = _unit_id(db_session, "M2")
        source_cp = _catalog_row(
            factories, kind=CatalogKind.TO_REVIEW.value, unit_id=unit_id, title=f"Источник {_uid()}"
        )
        proposal = _proposal(factories)
        item_plain = _position(factories, proposal, catalog_position=source_cp, title="Без статьи")
        member_plain = route_position(db_session, position_item_id=item_plain.id)
        chapter = _chapter(factories, proposal, category_id=category_id)
        item_categorized = _position(
            factories, proposal, chapter=chapter, catalog_position=source_cp, title="Со статьёй"
        )
        member_categorized = route_position(db_session, position_item_id=item_categorized.id)
        assert member_plain.bucket_id != member_categorized.bucket_id

        set_kind(db_session, to_review_id=source_cp.id, kind=CatalogKind.HEADER.value)

        assert _context(db_session, member_plain.context_id).semantic_state == (
            SemanticState.NOT_APPLICABLE.value
        )
        assert _context(db_session, member_categorized.context_id).semantic_state == (
            SemanticState.NOT_APPLICABLE.value
        )

    def test_position_does_not_touch_a_single_context_field(self, db_session, factories):
        """`set_kind(POSITION)` — снимок ВСЕХ полей контекста до/после, а не
        отсутствие ошибки."""
        scene = _simple_scene(db_session, factories, unit_code="M2")
        before = _context(db_session, scene.source_context_id)
        snapshot_before = {
            column.name: getattr(before, column.name)
            for column in CatalogContext.__table__.columns
        }
        db_session.expire_all()

        set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.POSITION.value)

        after = _context(db_session, scene.source_context_id)
        snapshot_after = {
            column.name: getattr(after, column.name)
            for column in CatalogContext.__table__.columns
        }
        assert snapshot_after == snapshot_before

    def test_lot_header_is_rejected_before_touching_context(self, db_session, factories):
        scene = _simple_scene(db_session, factories, unit_code="M2")
        assert "LOT_HEADER" not in MANUAL_KINDS
        with pytest.raises(ReviewError):
            set_kind(db_session, to_review_id=scene.source_cp_id, kind="LOT_HEADER")
        # Контекст не тронут — отказ произошёл до любой мутации.
        ctx = _context(db_session, scene.source_context_id)
        assert ctx.semantic_state == SemanticState.SUGGESTED.value

    def test_not_applicable_is_terminal_second_set_kind_call_is_refused(self, db_session, factories):
        scene = _simple_scene(db_session, factories, unit_code="M2")
        set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.HEADER.value)
        with pytest.raises(ReviewError):
            set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.POSITION.value)


# ---------------------------------------------------------------------------
#  `accept_target_decision` (задача 9, спека §2.8)
# ---------------------------------------------------------------------------

class TestAcceptTargetDecision:
    def _conflicted_scene(self, db_session, factories):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        source_family = _active_family(
            db_session, title=f"Источник-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        target_family = _active_family(
            db_session, title=f"Цель-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.target_context_id, family_id=target_family.id, actor_id=user.id
        )
        merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        return scene, user

    def test_accepting_clears_both_conflict_columns_together(self, db_session, factories):
        scene, user = self._conflicted_scene(db_session, factories)
        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is not None

        count = accept_target_decision(
            db_session, position_item_ids=[scene.source_item_id], actor_id=user.id
        )
        assert count == 1

        moved_member = _member(db_session, scene.source_item_id)
        assert moved_member.conflict_at is None
        assert moved_member.conflict_from_context_id is None
        # context_id и membership_state НЕ тронуты.
        assert moved_member.context_id == scene.target_context_id
        assert moved_member.membership_state == MembershipState.CURRENT.value

    def test_membership_without_conflict_is_refused(self, db_session, factories):
        scene = _simple_scene(db_session, factories)
        user = factories.UserFactory.create()
        with pytest.raises(ContextOperationError) as exc_info:
            accept_target_decision(
                db_session, position_item_ids=[scene.source_item_id], actor_id=user.id
            )
        assert exc_info.value.code == REFUSE_NOT_CONFLICTED

    def test_missing_membership_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(ContextOperationError) as exc_info:
            accept_target_decision(db_session, position_item_ids=[999999], actor_id=user.id)
        assert exc_info.value.code == REFUSE_INVALID_MEMBERSHIP


class TestAcceptTargetDecisionRereadsAfterLock:
    """Тот же приём, что `TestArchiveRereadsAfterLock`
    (`test_context_concurrency.py`): без потоков, ДВЕ настоящие (коммитящие)
    сессии по очереди — стереги не гонку, а саму СТАЛОСТЬ identity map."""

    def test_conflict_cleared_between_first_read_and_lock_is_caught(
        self, committing_db, committing_factories, committing_session_factory
    ):
        user = committing_factories.UserFactory.create()
        scene = _simple_scene(committing_db, committing_factories)
        source_family = _active_family(
            committing_db, title=f"Источник-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        target_family = _active_family(
            committing_db, title=f"Цель-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            committing_db, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )
        assign_family(
            committing_db, context_id=scene.target_context_id, family_id=target_family.id, actor_id=user.id
        )
        committing_db.commit()

        merge_into_position_outcome(
            committing_db, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )
        committing_db.commit()

        # Прайминг: `committing_db` читает членство, кэширует conflict_at
        # != None — ТА ЖЕ сессия ниже вызовет `accept_target_decision`.
        primed = committing_db.get(ContextMember, scene.source_item_id)
        assert primed.conflict_at is not None

        # ОТДЕЛЬНАЯ, независимая сессия снимает конфликт (второй оператор уже
        # принял решение цели) и коммитит.
        with committing_session_factory() as other:
            other_member = other.get(ContextMember, scene.source_item_id)
            other_member.conflict_at = None
            other_member.conflict_from_context_id = None
            other.commit()

        # Без `db.expire_all()` после лока `accept_target_decision` увидела бы
        # закэшированное `conflict_at != None` и молча «приняла» бы уже
        # разрешённый конфликт вместо отказа по СВЕЖЕМУ состоянию.
        with pytest.raises(ContextOperationError) as exc_info:
            accept_target_decision(
                committing_db, position_item_ids=[scene.source_item_id], actor_id=user.id
            )
        assert exc_info.value.code == REFUSE_NOT_CONFLICTED


def _blocked_on(session_factory, *, pid: int, contains: str) -> bool:
    """Один снимок: КОНКРЕТНЫЙ backend `pid` ждёт лока, и текст его запроса
    содержит `contains` — не общий счётчик по базе (иначе зелено и когда
    ждёт посторонний backend)."""
    with session_factory() as probe:
        row = probe.execute(
            sa.text(
                "SELECT cardinality(pg_blocking_pids(:pid)) > 0 AS blocked, "
                "(SELECT query FROM pg_stat_activity WHERE pid = :pid) AS query"
            ),
            {"pid": pid},
        ).one()
        probe.rollback()
        return bool(row.blocked and contains in (row.query or ""))


def _wait_until_blocked(session_factory, *, pid: int, contains: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _blocked_on(session_factory, pid=pid, contains=contains):
            return True
        time.sleep(0.05)
    return False


def _terminate_merge_race_backend(session_factory, pid: int | None) -> None:
    if pid is None:
        return
    try:
        with session_factory() as probe:
            probe.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            probe.commit()
    except Exception:  # pragma: no cover — best-effort уборка
        pass


class TestMergeRereadsSourceContextAfterLock:
    def test_family_assigned_while_merge_waits_on_archive_produces_conflict(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Пока `assign_family` держит `FOR UPDATE` на исходный контекст (лок
        взят ДО её собственного `flush`), слияние читает тот же контекст
        ОБЫЧНЫМ `SELECT` (семьи на нём ещё правда нет) и застревает на
        `UPDATE catalog_contexts SET archived_at=...` того же контекста
        (`_archive_contexts`), ожидая коммита `assign_family`. Освобождённый
        `assign_family` коммитит семью; блокировка слияния снимается — но без
        перечитывания ПОСЛЕ неё Python-объект остаётся с устаревшим
        `work_family_id=None`, и сравнение решений (`_conflict_warning`)
        молча пропускает реально разошедшиеся семьи."""
        user = committing_factories.UserFactory.create()
        scene = _simple_scene(committing_db, committing_factories)
        target_family = _active_family(
            committing_db, title=f"Цель-семья гонки {_uid()}", unit_name="M2", actor_id=user.id,
        )
        assign_family(
            committing_db, context_id=scene.target_context_id, family_id=target_family.id,
            actor_id=user.id,
        )
        committing_db.commit()

        b_paused = threading.Event()
        b_release = threading.Event()
        errors: list[str] = []
        pid_holder: dict[str, int] = {}
        a_result: dict[str, object] = {}
        b_result: dict[str, object] = {}

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b"] = dbB.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    source_family = _active_family(
                        dbB, title=f"Источник-семья гонки {_uid()}", unit_name="M2",
                        actor_id=user.id,
                    )
                    b_result["source_family_id"] = source_family.id
                    b_result["source_family_title"] = source_family.title

                    def _pause_before_flush(session, flush_context, instances):
                        b_paused.set()
                        assert b_release.wait(timeout=30.0), "release B не пришёл вовремя"

                    event.listen(dbB, "before_flush", _pause_before_flush)
                    try:
                        assign_family(
                            dbB, context_id=scene.source_context_id, family_id=source_family.id,
                            actor_id=user.id,
                        )
                    finally:
                        event.remove(dbB, "before_flush", _pause_before_flush)
                    dbB.commit()
                    b_result["assigned"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"B: {type(exc).__name__}: {exc}")

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a"] = dbA.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    outcome = merge_into_position_outcome(
                        dbA, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id,
                    )
                    dbA.commit()
                    a_result["warnings"] = outcome.warnings
            except Exception as exc:  # noqa: BLE001
                errors.append(f"A: {type(exc).__name__}: {exc}")

        tb = threading.Thread(target=thread_b, name="B", daemon=True)
        ta = threading.Thread(target=thread_a, name="A", daemon=True)
        a_blocked_on_b = False
        try:
            tb.start()
            assert b_paused.wait(timeout=15.0), "B не встала на паузу"

            ta.start()
            deadline = time.monotonic() + 15.0
            while "a" not in pid_holder and time.monotonic() < deadline:
                time.sleep(0.02)
            assert "a" in pid_holder, "A не сообщила свой backend pid"

            a_blocked_on_b = _wait_until_blocked(
                committing_session_factory, pid=pid_holder["a"], contains="catalog_contexts",
                timeout=15.0,
            )

            b_release.set()
            tb.join(timeout=30.0)
            ta.join(timeout=30.0)
        finally:
            b_release.set()
            tb.join(timeout=30.0)
            ta.join(timeout=30.0)
            if tb.is_alive():
                _terminate_merge_race_backend(committing_session_factory, pid_holder.get("b"))
            if ta.is_alive():
                _terminate_merge_race_backend(committing_session_factory, pid_holder.get("a"))

        assert not errors, errors
        assert not tb.is_alive()
        assert not ta.is_alive()

        assert b_result.get("assigned") is True
        warnings = a_result.get("warnings")
        assert warnings is not None and len(warnings) == 1, warnings
        assert b_result["source_family_title"] in warnings[0]
        assert target_family.title in warnings[0]

        moved_member = committing_db.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == scene.source_item_id)
        ).scalar_one()
        assert moved_member.conflict_at is not None

        assert a_blocked_on_b, "A (слияние) не встала в очередь на лок, который держит B"


# ---------------------------------------------------------------------------
#  API: `warnings` в ответе `POST /{to_review_id}/merge`
# ---------------------------------------------------------------------------

class TestMergeAPIReturnsWarnings:
    def test_empty_warnings_when_decisions_do_not_diverge(self, client, db_session, factories):
        scene = _simple_scene(db_session, factories)
        response = client.post(
            f"/api/v1/review/{scene.source_cp_id}/merge", json={"target_id": scene.target_cp_id}
        )
        assert response.status_code == 200
        assert response.json()["warnings"] == []

    def test_non_empty_warnings_on_conflict(self, client, db_session, factories):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        source_family = _active_family(
            db_session, title=f"Источник-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        target_family = _active_family(
            db_session, title=f"Цель-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.target_context_id, family_id=target_family.id, actor_id=user.id
        )

        response = client.post(
            f"/api/v1/review/{scene.source_cp_id}/merge", json={"target_id": scene.target_cp_id}
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["warnings"]) == 1
        assert source_family.title in body["warnings"][0]
        assert target_family.title in body["warnings"][0]


# ---------------------------------------------------------------------------
#  Дедупликация `warnings`: одно предупреждение на пару контекстов, а не на
#  членство (спека §2.8 говорит о конфликте КОНТЕКСТОВ, а не отдельных
#  позиций)
# ---------------------------------------------------------------------------

class TestWarningsDeduplication:
    def test_multiple_conflicting_members_of_the_same_pair_give_one_warning(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        scene = _simple_scene(db_session, factories)
        source_cp = db_session.get(CatalogPosition, scene.source_cp_id)
        # Вторая позиция в ТОЙ ЖЕ исходной корзине/контексте (тот же
        # catalog_position, та же — пустая — статья): маршрутизация даёт тот
        # же default-контекст, что и первая.
        second_item, second_member = _routed_position(
            db_session, factories, catalog_position=source_cp, title="Вторая исходная"
        )
        assert second_member.context_id == scene.source_context_id

        source_family = _active_family(
            db_session, title=f"Источник-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        target_family = _active_family(
            db_session, title=f"Цель-семья {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.source_context_id, family_id=source_family.id, actor_id=user.id
        )
        assign_family(
            db_session, context_id=scene.target_context_id, family_id=target_family.id, actor_id=user.id
        )

        outcome = merge_into_position_outcome(
            db_session, to_review_id=scene.source_cp_id, target_id=scene.target_cp_id
        )

        # Дедупликация: ДВА конфликтных членства одной пары (источник, цель)
        # — ОДНО предупреждение, не два.
        assert outcome.moved_positions == 2
        assert len(outcome.warnings) == 1

        # Оба членства при этом ПОМЕЧЕНЫ конфликтом — дедупликация только
        # текста предупреждения, не самого флага на каждой строке.
        for pid in (scene.source_item_id, second_item.id):
            member = _member(db_session, pid)
            assert member.conflict_at is not None
            assert member.conflict_from_context_id == scene.source_context_id

        events = _context_events(db_session, scene.target_context_id, "members_moved")
        assert len(events) == 1
        assert events[0].payload["moved_members"] == 2


# ---------------------------------------------------------------------------
#  `set_kind(HEADER|TRASH)`: контексты берутся FOR UPDATE до NOT_APPLICABLE
# ---------------------------------------------------------------------------

class TestSetKindLocksContextsBeforeMarkingNotApplicable:
    def test_compiled_sql_locks_catalog_contexts_for_update_before_the_update(
        self, db_session, factories
    ):
        scene = _simple_scene(db_session, factories, unit_code="M2")
        with _CapturingSQL(db_session) as capture:
            set_kind(db_session, to_review_id=scene.source_cp_id, kind=CatalogKind.HEADER.value)
        statements = capture.statements

        lock_index, lock_statement = next(
            (index, statement)
            for index, statement in enumerate(statements)
            if "catalog_contexts" in statement and (" FOR UPDATE" in statement or " FOR SHARE" in statement)
        )
        assert " FOR UPDATE" in lock_statement
        assert " FOR SHARE" not in lock_statement
        assert "ORDER BY catalog_contexts.id" in lock_statement

        update_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.strip().upper().startswith("UPDATE CATALOG_CONTEXTS")
        )
        assert lock_index < update_index

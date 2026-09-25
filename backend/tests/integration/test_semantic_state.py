"""Ось вида работы: подтверждение/снятие подтверждения, ручная роль имени,
таблица переходов `semantic_state` (план
`docs/superpowers/plans/2026-09-22-catalog-families.md`, задача 8; спека
`docs/superpowers/specs/2026-09-22-catalog-families-design.md` §2.5, §2.6,
§2.7 «Назначение семьи контексту», §2.14).

`semantic_state` несёт РОВНО ТРИ значения (`CHECK`
`ck_catalog_contexts_semantic_state`) — тест на это уже существует
(`test_semantic_schema.py::test_semantic_state_pending_rejected`, задача 1) и
здесь не повторяется. Этот файл — про КОД задачи 8: переходы таблицы §2.5,
достижимые прямой операцией (`confirm_kind`/`unconfirm_kind`), независимость
оси вида от оси семьи, и `set_name_role`.

Помощники (`_proposal`, `_position`, `_routed_context`) — ЛОКАЛЬНАЯ копия
набора `test_context_operations.py`/`test_work_families.py` (докстрока тех
файлов: наборы помощников тестов друг у друга не импортируют).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from models import (
    CatalogContext,
    CatalogKind,
    DecisionSource,
    NameRole,
    SemanticEvent,
    SemanticKind,
    SemanticState,
)
from services.context_routing import route_position
from services.semantic_rules import PLACE_DICTIONARY_VERSION, classify_kind
from services.unit_resolution import UnitResolver
from services.work_families import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_APPLICABLE,
    REFUSE_CONTEXT_NOT_FOUND,
    REFUSE_INVALID_KIND,
    REFUSE_INVALID_NAME_ROLE,
    WorkFamilyError,
    activate_family,
    assign_family,
    confirm_kind,
    create_family,
    set_name_role,
    unconfirm_kind,
)

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _position(factories, proposal, *, catalog_position=None, title="Работа"):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _routed_context(db, factories, *, unit_id=None, catalog_kind=CatalogKind.POSITION.value):
    """Реальная маршрутизация (задача 4): корзина + контекст по умолчанию
    строятся `route_position`, вид/роль — правилом (задача 3). Возвращает
    `(context, catalog_position)`."""
    proposal = _proposal(factories)
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, kind=catalog_kind)
    position = _position(factories, proposal, catalog_position=cp)
    member = route_position(db, position_item_id=position.id)
    context = db.get(CatalogContext, member.context_id)
    return context, cp


def _context_events(db, context_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.context_id == context_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return db.execute(stmt).scalars().all()


# ---------------------------------------------------------------------------
#  Таблица переходов §2.5, достижимая ПРЯМОЙ операцией: рождение SUGGESTED,
#  SUGGESTED -> CONFIRMED (подтверждение и переопределение), CONFIRMED ->
#  SUGGESTED (пересчёт правилом). `set_kind`/`NOT_APPLICABLE` через Review —
#  задача 9, здесь не утверждаются (план, Task 8, «Утверждения»).
# ---------------------------------------------------------------------------


class TestKindTransitionsViaDirectRun:
    def test_context_is_born_suggested_with_rule_kind(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        assert context.semantic_state == SemanticState.SUGGESTED.value
        assert context.semantic_kind == SemanticKind.WORK.value
        assert context.semantic_kind_source == DecisionSource.rule.value
        assert context.semantic_kind_by is None

    def test_confirm_without_override_keeps_kind_sets_manual_confirmed(
        self, db_session, factories
    ):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        kind_before = context.semantic_kind

        result = confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        assert result.semantic_kind == kind_before
        assert result.semantic_kind_source == DecisionSource.manual.value
        assert result.semantic_kind_by == user.id
        assert result.semantic_kind_at is not None
        assert result.semantic_state == SemanticState.CONFIRMED.value

        events = _context_events(db_session, context.id, "kind_set")
        assert len(events) == 1
        assert events[0].payload == {
            "from": kind_before, "to": kind_before, "source": "manual",
        }

    def test_confirm_with_override_changes_kind(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        assert context.semantic_kind == SemanticKind.WORK.value  # предпосылка

        result = confirm_kind(
            db_session, context_id=context.id, kind=SemanticKind.SYSTEM.value,
            actor_id=user.id,
        )
        assert result.semantic_kind == SemanticKind.SYSTEM.value
        assert result.semantic_kind_source == DecisionSource.manual.value
        assert result.semantic_state == SemanticState.CONFIRMED.value

        events = _context_events(db_session, context.id, "kind_set")
        assert events[-1].payload == {
            "from": SemanticKind.WORK.value, "to": SemanticKind.SYSTEM.value, "source": "manual",
        }

    def test_unconfirm_recomputes_kind_by_rule_not_keeps_prior_value(
        self, db_session, factories
    ):
        """Утверждение плана: обратный переход пересчитывает вид ПРАВИЛОМ, а
        не сохраняет прежнее значение — проверяется сравнением ПОЛЯ, а не
        отсутствием ошибки. Переопределяем на `SYSTEM` (единица — `M2`, по
        правилу это `WORK`), затем снимаем подтверждение: результат обязан
        вернуться к `WORK`, а не остаться `SYSTEM`."""
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        confirm_kind(
            db_session, context_id=context.id, kind=SemanticKind.SYSTEM.value,
            actor_id=user.id,
        )
        db_session.refresh(context)
        assert context.semantic_kind == SemanticKind.SYSTEM.value  # предпосылка

        result = unconfirm_kind(db_session, context_id=context.id, actor_id=user.id)
        assert result.semantic_kind == SemanticKind.WORK.value  # пересчитано, не SYSTEM
        assert result.semantic_kind == classify_kind("M2")
        assert result.semantic_kind_source == DecisionSource.rule.value
        assert result.semantic_kind_by is None
        assert result.semantic_kind_at is not None
        assert result.semantic_state == SemanticState.SUGGESTED.value

        events = _context_events(db_session, context.id, "kind_set")
        assert events[-1].payload == {
            "from": SemanticKind.SYSTEM.value, "to": SemanticKind.WORK.value, "source": "rule",
        }

    def test_confirm_invalid_kind_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            confirm_kind(
                db_session, context_id=context.id, kind="NOT_A_KIND", actor_id=user.id
            )
        assert exc.value.code == REFUSE_INVALID_KIND

    def test_context_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            confirm_kind(db_session, context_id=999_999_999, kind=None, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_NOT_FOUND
        with pytest.raises(WorkFamilyError) as exc2:
            unconfirm_kind(db_session, context_id=999_999_999, actor_id=user.id)
        assert exc2.value.code == REFUSE_CONTEXT_NOT_FOUND


# ---------------------------------------------------------------------------
#  Не-оп с обеих сторон: повторный
#  `confirm_kind` на уже CONFIRMED БЕЗ реального переопределения и
#  `unconfirm_kind` на уже SUGGESTED не трогают поля и не пишут событие.
# ---------------------------------------------------------------------------


class TestConfirmUnconfirmNoOpSides:
    def test_reconfirm_same_kind_on_confirmed_is_noop(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        db_session.refresh(context)
        kind_before = context.semantic_kind
        by_before = context.semantic_kind_by
        at_before = context.semantic_kind_at
        events_before = len(_context_events(db_session, context.id, "kind_set"))
        assert context.semantic_state == SemanticState.CONFIRMED.value  # предпосылка

        result = confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        assert result.semantic_kind == kind_before
        assert result.semantic_kind_by == by_before
        assert result.semantic_kind_at == at_before
        assert result.semantic_state == SemanticState.CONFIRMED.value
        assert len(_context_events(db_session, context.id, "kind_set")) == events_before

    def test_reconfirm_same_explicit_kind_on_confirmed_is_noop(self, db_session, factories):
        """То же самое, но `kind` передан ЯВНО (не `None`) и совпадает с
        текущим значением — «то же самое значение» решает исход, не сам факт
        передачи параметра."""
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        db_session.refresh(context)
        current_kind = context.semantic_kind
        events_before = len(_context_events(db_session, context.id, "kind_set"))

        confirm_kind(db_session, context_id=context.id, kind=current_kind, actor_id=user.id)
        assert len(_context_events(db_session, context.id, "kind_set")) == events_before

    def test_reconfirm_different_kind_on_confirmed_updates_and_writes_event(
        self, db_session, factories
    ):
        """Обратная сторона не-опа: реальное переопределение на CONFIRMED
        обязано пройти и записать НОВОЕ событие — не-оп только когда значение
        не меняется."""
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        db_session.refresh(context)
        events_before = len(_context_events(db_session, context.id, "kind_set"))
        assert context.semantic_kind == SemanticKind.WORK.value  # предпосылка

        result = confirm_kind(
            db_session, context_id=context.id, kind=SemanticKind.SYSTEM.value,
            actor_id=user.id,
        )
        assert result.semantic_kind == SemanticKind.SYSTEM.value
        assert len(_context_events(db_session, context.id, "kind_set")) == events_before + 1

    def test_unconfirm_on_suggested_is_noop(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        assert context.semantic_state == SemanticState.SUGGESTED.value  # предпосылка
        kind_before = context.semantic_kind
        source_before = context.semantic_kind_source
        at_before = context.semantic_kind_at
        events_before = len(_context_events(db_session, context.id, "kind_set"))

        result = unconfirm_kind(db_session, context_id=context.id, actor_id=user.id)
        assert result.semantic_kind == kind_before
        assert result.semantic_kind_source == source_before
        assert result.semantic_kind_at == at_before
        assert result.semantic_state == SemanticState.SUGGESTED.value
        assert len(_context_events(db_session, context.id, "kind_set")) == events_before


# ---------------------------------------------------------------------------
#  NOT_APPLICABLE — оба отказывают (свой код сверх плана).
# ---------------------------------------------------------------------------


class TestNotApplicableRefusesBoth:
    def test_confirm_kind_on_not_applicable_refuses(self, db_session, factories):
        context, _cp = _routed_context(
            db_session, factories, unit_id=None, catalog_kind=CatalogKind.TRASH.value
        )
        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value  # предпосылка
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_NOT_APPLICABLE

    def test_unconfirm_kind_on_not_applicable_refuses(self, db_session, factories):
        context, _cp = _routed_context(
            db_session, factories, unit_id=None, catalog_kind=CatalogKind.HEADER.value
        )
        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            unconfirm_kind(db_session, context_id=context.id, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_NOT_APPLICABLE


# ---------------------------------------------------------------------------
#  Архивный контекст «выведен из обращения» (спека §2.8) — тем же кодом, что
#  `context_operations`, а не молча принимает правку.
# ---------------------------------------------------------------------------


class TestArchivedContextRefusesFamilyOperations:
    def test_confirm_kind_on_archived_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.expire(context)
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_unconfirm_kind_on_archived_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.expire(context)
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            unconfirm_kind(db_session, context_id=context.id, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_set_name_role_on_archived_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.expire(context)
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            set_name_role(
                db_session, context_id=context.id, role=NameRole.GENERIC_WORK.value,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_CONTEXT_ARCHIVED


# ---------------------------------------------------------------------------
#  Независимость осей (обратная сторона — прямая доказана в
#  test_work_families.py::TestAssignFamily::test_assignment_does_not_change_semantic_state).
# ---------------------------------------------------------------------------


class TestAxisIndependence:
    def test_confirm_kind_does_not_change_work_family_id(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Не трогает семью {_uid()}", unit_name="M2",
            definition="Определение", actor_id=user.id,
        )
        activate_family(db_session, family_id=family.id, actor_id=user.id)
        assign_family(db_session, context_id=context.id, family_id=family.id, actor_id=user.id)
        db_session.refresh(context)
        family_id_before = context.work_family_id
        assert family_id_before == family.id  # предпосылка

        confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        db_session.refresh(context)
        assert context.work_family_id == family_id_before


# ---------------------------------------------------------------------------
#  set_name_role
# ---------------------------------------------------------------------------


class TestSetNameRole:
    def test_success_sets_manual_triple_and_event(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        role_before = context.name_role

        result = set_name_role(
            db_session, context_id=context.id, role=NameRole.GENERIC_WORK.value,
            actor_id=user.id,
        )
        assert result.name_role == NameRole.GENERIC_WORK.value
        assert result.name_role_source == DecisionSource.manual.value
        assert result.name_role_by == user.id
        assert result.name_role_at is not None
        assert result.place_dictionary_version == PLACE_DICTIONARY_VERSION

        events = _context_events(db_session, context.id, "name_role_set")
        assert len(events) == 1
        assert events[0].payload == {
            "from": role_before, "to": NameRole.GENERIC_WORK.value, "source": "manual",
            "place_dictionary_version": PLACE_DICTIONARY_VERSION,
        }

    def test_invalid_role_refuses_before_any_read(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            set_name_role(
                db_session, context_id=999_999_999, role="NOT_A_ROLE", actor_id=user.id
            )
        assert exc.value.code == REFUSE_INVALID_NAME_ROLE

    def test_context_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            set_name_role(
                db_session, context_id=999_999_999, role=NameRole.WORK.value,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_CONTEXT_NOT_FOUND


# ---------------------------------------------------------------------------
#  Режим и позиция локов — компиляцией РЕАЛЬНОГО запроса (тот же приём, что
#  `TestFamilyLockCompilation`, `test_work_families.py`). Помощники —
#  ЛОКАЛЬНАЯ копия.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _capturing_sql(session):
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _find_context_lock(statements):
    for index, statement in enumerate(statements):
        if "catalog_contexts" in statement and " FOR UPDATE" in statement:
            return index, statement
    raise AssertionError(
        f"нет запроса к catalog_contexts с FOR UPDATE среди {len(statements)}:\n"
        + "\n---\n".join(statements)
    )


def _plain_context_read_indices(statements):
    indices = []
    for index, statement in enumerate(statements):
        stripped = statement.lstrip().upper()
        if (
            stripped.startswith("SELECT")
            and "catalog_contexts" in statement
            and "FOR UPDATE" not in statement
            and "FOR SHARE" not in statement
        ):
            indices.append(index)
    return indices


class TestSemanticStateLockCompilation:
    def test_confirm_kind_for_update_with_reread(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        db_session.expire(context)  # см. TestFamilyLockCompilation — иначе кэш

        with _capturing_sql(db_session) as statements:
            confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)

        lock_i, lock_statement = _find_context_lock(statements)
        assert "FOR SHARE" not in lock_statement
        plain_reads = _plain_context_read_indices(statements)
        assert [i for i in plain_reads if i > lock_i], "нет перечитывания semantic_state после лока"

    def test_unconfirm_kind_for_update_with_reread(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        confirm_kind(db_session, context_id=context.id, kind=None, actor_id=user.id)
        db_session.expire(context)

        with _capturing_sql(db_session) as statements:
            unconfirm_kind(db_session, context_id=context.id, actor_id=user.id)

        lock_i, lock_statement = _find_context_lock(statements)
        assert "FOR SHARE" not in lock_statement
        plain_reads = _plain_context_read_indices(statements)
        assert [i for i in plain_reads if i > lock_i], "нет перечитывания semantic_state после лока"

    def test_set_name_role_for_update(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        db_session.expire(context)

        with _capturing_sql(db_session) as statements:
            set_name_role(
                db_session, context_id=context.id, role=NameRole.WORK.value,
                actor_id=user.id,
            )

        lock_i, lock_statement = _find_context_lock(statements)
        assert "FOR SHARE" not in lock_statement


# ---------------------------------------------------------------------------
#  Перечитывание после лока — БЕЗ потоков (тот же приём, что
#  `TestArchiveRereadsAfterLock`, `test_context_concurrency.py`, задача 6):
#  другая закоммиченная сессия меняет `semantic_state` МЕЖДУ первым чтением
#  этой сессии и локом.
# ---------------------------------------------------------------------------


class TestSemanticStateRereadAfterLock:
    def test_confirm_kind_rereads_not_applicable_set_after_first_read(
        self, committing_db, committing_factories, committing_session_factory
    ):
        context, _cp = _routed_context(committing_db, committing_factories, unit_id=None)
        committing_db.commit()
        context_id = context.id
        user = committing_factories.UserFactory.create()
        committing_db.commit()

        # ПЕРВОЕ чтение ЭТОЙ сессией — до внешнего изменения.
        primed = committing_db.get(CatalogContext, context_id)
        assert primed.semantic_state == SemanticState.SUGGESTED.value

        with committing_session_factory() as other:
            other_ctx = other.get(CatalogContext, context_id)
            other_ctx.semantic_state = SemanticState.NOT_APPLICABLE.value
            other.commit()

        with pytest.raises(WorkFamilyError) as exc:
            confirm_kind(committing_db, context_id=context_id, kind=None, actor_id=user.id)
        assert exc.value.code == REFUSE_CONTEXT_NOT_APPLICABLE

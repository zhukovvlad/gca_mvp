"""Операции оператора над контекстами: разделить, слить, перенести,
архивировать (спека `2026-09-22-catalog-families-design.md` §2.4, §2.8,
§2.14; план, задача 6).

Помощники — локальная копия набора `test_context_routing.py` (докстрока
того файла: наборы помощников тестов друг у друга не импортируют).
"""
from __future__ import annotations

import contextlib
import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from models import (
    CatalogContext,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    NameRole,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
)
from services.context_operations import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_EMPTY,
    REFUSE_CONTEXT_NOT_FOUND,
    REFUSE_DEFAULT_WITHOUT_SUCCESSOR,
    REFUSE_DIFFERENT_BUCKET,
    REFUSE_INCOMING_RULES,
    REFUSE_INVALID_MEMBERSHIP,
    REFUSE_INVALID_NEW_DEFAULT,
    REFUSE_INVALID_REASON,
    REFUSE_RULE_DOES_NOT_COVER,
    REFUSE_SAME_CONTEXT,
    ContextOperationError,
    SplitResult,
    archive_context,
    merge_contexts,
    move_members,
    split_context,
)
from services.context_routing import (
    PREDICATE_CHAPTER_CHAIN_CONTAINS,
    RoutingError,
    route_position,
)

pytestmark = pytest.mark.integration


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot), estimate


def _chapter(factories, proposal, *, title="Раздел", chapter_number="1", parent=None):
    kwargs = dict(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal=chapter_number,
    )
    if parent is not None:
        kwargs["chapter_item_id"] = parent.id
    return factories.PositionItemFactory.create(**kwargs)


def _position(factories, proposal, *, chapter=None, title="Работа", catalog_position=None):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _context(session, bucket, *, is_default=False, **overrides) -> CatalogContext:
    defaults = dict(
        bucket_id=bucket.id,
        is_default=is_default,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


def _rule(session, bucket, *, ordinal, predicate, context, user) -> ContextRoutingRule:
    rule = ContextRoutingRule(
        bucket_id=bucket.id,
        ordinal=ordinal,
        predicate=predicate,
        context_id=context.id,
        created_by=user.id,
    )
    session.add(rule)
    session.flush()
    return rule


def _events(session, context_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.context_id == context_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return session.execute(stmt).scalars().all()


def _seed_default_member(db_session, factories):
    """Обычная маршрутизация: корзина + контекст по умолчанию + одно
    членство — стартовая точка большинства сценариев этого файла."""
    proposal, _ = _proposal(factories)
    cp = factories.CatalogPositionFactory.create()
    position = _position(factories, proposal, catalog_position=cp)
    member = route_position(db_session, position_item_id=position.id)
    bucket = db_session.get(ContextBucket, member.bucket_id)
    default_ctx = db_session.get(CatalogContext, member.context_id)
    return bucket, default_ctx, position, cp


@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает КАЖДЫЙ SQL-текст, реально отправленный на этом
    соединении, через `before_cursor_execute` (MAJOR-1, ревью задачи 6
    раунд 3) — то, что предлагает сам отчёт ревью: режим лока проверяется
    компиляцией РЕАЛЬНОГО запроса операции, а не намерением вызова и не
    отдельным тестом `lock_buckets` из задачи 4, который не видит, какой
    `exclusive` передаёт вызывающий."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _find_bucket_lock_statement(statements: list[str]) -> tuple[int, str]:
    """Находит запрос `lock_buckets` по СОЧЕТАНИЮ признаков: `FROM
    context_buckets` — простой `db.get(ContextBucket, ...)` в коде задачи
    ТОЖЕ даёт `FROM context_buckets`, но БЕЗ модификатора `FOR …` — только
    строка с блокировкой несёт оба признака разом."""
    for index, statement in enumerate(statements):
        if "context_buckets" in statement and (
            " FOR UPDATE" in statement or " FOR SHARE" in statement
        ):
            return index, statement
    raise AssertionError(
        f"ни один из {len(statements)} перехваченных запросов не содержит "
        "лок context_buckets:\n" + "\n---\n".join(statements)
    )


def _precondition_read_indices(statements: list[str]) -> list[int]:
    """Индексы ВСЕХ `SELECT`, читающих `context_members`/
    `context_routing_rules`, по ВСЕМУ списку (не только после лока) —
    NIT-R2-1 (ревью задачи 6, раунд 2): прежняя версия искала только ПОСЛЕ
    индекса лока, поэтому сравнение позиций было тавтологией (истинно по
    построению, а не по факту, что запросы шли раньше)."""
    indices = []
    for index, statement in enumerate(statements):
        stripped = statement.lstrip().upper()
        if stripped.startswith("SELECT") and (
            "context_members" in statement or "context_routing_rules" in statement
        ):
            indices.append(index)
    return indices


# ---------------------------------------------------------------------------
#  Режим блокировки — компиляцией РЕАЛЬНОГО запроса каждой из четырёх
#  операций, а не намерением вызова (A6.9; MAJOR-1, ревью задачи 6 раунд 3:
#  `split_context`/`move_members` с `exclusive=False` проходили весь набор
#  незамеченными — задача 4 компилирует только сам `lock_buckets`, не видя,
#  какой `exclusive` передаёт вызывающая операция).
# ---------------------------------------------------------------------------

class TestAllFourOperationsCompileToForUpdateBeforePreconditionReads:
    def _assert_for_update_before_preconditions(
        self, statements: list[str], *, pre_lock_reads_allowed: int = 0
    ) -> None:
        """`pre_lock_reads_allowed` — число чтений `context_members`/
        `context_routing_rules`, ЗАКОННО происходящих ДО лока у этой
        КОНКРЕТНОЙ операции (0 для трёх из четырёх; у `move_members` — 1:
        предварительная проверка существования членств нужна ДО того, как
        известно, какую корзину блокировать — сама проверка не несёт
        решения, оно принимается ПОСЛЕ перечитывания, ревью задачи 6 раунд
        2, NIT-R2-1). Число проверяется ТОЧНО — лишнее чтение до лока для
        операции, где `pre_lock_reads_allowed=0`, тоже обязано покраснеть.
        Хотя бы одно чтение ПОСЛЕ лока обязательно всегда — иначе
        перечитывание ничем не подтверждено."""
        lock_index, lock_statement = _find_bucket_lock_statement(statements)
        assert "FOR UPDATE" in lock_statement
        assert "FOR SHARE" not in lock_statement
        assert "KEY SHARE" not in lock_statement
        assert "NO KEY" not in lock_statement

        precondition_indices = _precondition_read_indices(statements)
        before_lock = [i for i in precondition_indices if i < lock_index]
        after_lock = [i for i in precondition_indices if i > lock_index]

        assert len(before_lock) == pre_lock_reads_allowed, (
            f"чтений context_members/context_routing_rules ДО лока (индекс "
            f"{lock_index}): {len(before_lock)} (индексы {before_lock}), "
            f"ожидалось ровно {pre_lock_reads_allowed}"
        )
        assert after_lock, (
            "ни одного чтения context_members/context_routing_rules ПОСЛЕ "
            f"лока (индекс {lock_index}) — перечитывание предусловий ничем "
            "не подтверждено"
        )

    def test_split_context(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with _capturing_sql(db_session) as statements:
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[position.id],
                rule=None, actor_id=user.id,
            )
        self._assert_for_update_before_preconditions(statements, pre_lock_reads_allowed=0)

    def test_merge_contexts(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        with _capturing_sql(db_session) as statements:
            merge_contexts(
                db_session, source_context_id=default_ctx.id, target_context_id=target.id,
                actor_id=user.id,
            )
        self._assert_for_update_before_preconditions(statements, pre_lock_reads_allowed=0)

    def test_move_members(self, db_session, factories):
        """`pre_lock_reads_allowed=1`: ДО лока `move_members` ОБЯЗАНА
        прочитать членства хотя бы раз, чтобы определить, какую корзину
        блокировать (сама корзина не названа аргументом — она выводится из
        `context_members.bucket_id`), — то же законное предварительное
        чтение, что у `split_context`/`merge_contexts` через `CatalogContext`
        (не через эти две таблицы). Решение по предусловию при этом всё
        равно принимается ПОСЛЕ лока — вторым чтением, тем же
        `_assert_for_update_before_preconditions` подтверждённым."""
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        with _capturing_sql(db_session) as statements:
            move_members(
                db_session, position_item_ids=[position.id], target_context_id=target.id,
                actor_id=user.id, reason="manual",
            )
        self._assert_for_update_before_preconditions(statements, pre_lock_reads_allowed=1)

    def test_archive_context(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket, is_default=False)  # архивируем ЕГО, не default

        with _capturing_sql(db_session) as statements:
            archive_context(
                db_session, context_id=target.id, new_default_context_id=None, actor_id=user.id
            )
        self._assert_for_update_before_preconditions(statements)


# ---------------------------------------------------------------------------
#  Разделить — с исполнимым правилом (спека §2.4)
# ---------------------------------------------------------------------------

class TestSplitWithExecutableRule:
    def test_next_import_matching_the_rule_routes_by_it_not_by_the_row_existing(
        self, db_session, factories
    ):
        """Утверждение плана: проверяется МАРШРУТОМ новой позиции, а не
        наличием строки правила."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Альфа")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        default_ctx = db_session.get(CatalogContext, member.context_id)

        result = split_context(
            db_session,
            context_id=default_ctx.id,
            position_item_ids=[position.id],
            rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Альфа"},
            actor_id=user.id,
        )
        assert isinstance(result, SplitResult)
        assert result.moved_members == 1
        assert result.rule_id is not None
        assert result.default_replaced is False

        moved = db_session.get(ContextMember, position.id)
        assert moved.context_id == result.new_context_id
        assert moved.routed_by == RoutedBy.rule.value
        assert moved.routing_rule_id == result.rule_id

        # Новая позиция той же корзины, matching правилу — уходит по правилу.
        new_position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Ещё работа"
        )
        new_member = route_position(db_session, position_item_id=new_position.id)
        assert new_member.context_id == result.new_context_id
        assert new_member.routed_by == RoutedBy.rule.value
        assert new_member.routing_rule_id == result.rule_id

        # Позиция, НЕ подпадающая под правило — по-прежнему в старом умолчании.
        other_chapter = _chapter(factories, proposal, title="Секция Бета", chapter_number="2")
        unrelated_position = _position(
            factories, proposal, chapter=other_chapter, catalog_position=cp, title="Третья работа"
        )
        unrelated_member = route_position(db_session, position_item_id=unrelated_position.id)
        assert unrelated_member.context_id == default_ctx.id
        assert unrelated_member.routed_by == RoutedBy.default.value

    def test_rule_ordinal_is_max_plus_one_in_the_bucket(self, db_session, factories):
        # Правило обязано ПОКРЫВАТЬ выбранное членство (MINOR-3, ревью
        # задачи 6 раунд 3) — позиция строится с реальным разделом,
        # совпадающим с предикатом нового правила, а правило-сосед метит в
        # ДРУГОЙ раздел, чтобы не затенять его.
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Y")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        default_ctx = db_session.get(CatalogContext, member.context_id)

        other_ctx = _context(db_session, bucket)
        # ДВА существующих правила с РАЗНЫМИ ordinal (NIT-R2-3, ревью задачи
        # 6 раунд 2) — одного было недостаточно, чтобы отличить «истинный
        # максимум» от «ordinal первого правила в списке» (запрос сортирует
        # по возрастанию, первый элемент — МЕНЬШИЙ, не максимум): второе
        # правило стоит РАНЬШЕ (`ordinal=2`), первым в списке, а истинный
        # максимум (`5`) — у последнего.
        _rule(
            db_session, bucket, ordinal=2,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Z"},
            context=other_ctx, user=user,
        )
        _rule(
            db_session, bucket, ordinal=5,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция X"},
            context=other_ctx, user=user,
        )

        result = split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Y"},
            actor_id=user.id,
        )
        new_rule = db_session.get(ContextRoutingRule, result.rule_id)
        assert new_rule.ordinal == 6

    def test_writes_context_split_and_context_created_events_only(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Z")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        default_ctx = db_session.get(CatalogContext, member.context_id)

        result = split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Z"},
            actor_id=user.id,
        )

        split_events = _events(db_session, result.new_context_id, "context_split")
        assert len(split_events) == 1
        assert split_events[0].payload == {
            "from_context_id": default_ctx.id,
            "moved_members": 1,
            "rule_id": result.rule_id,
        }
        created_events = _events(db_session, result.new_context_id, "context_created")
        assert len(created_events) == 1
        assert created_events[0].payload == {"bucket_id": bucket.id, "origin": "split"}

    def test_malformed_rule_is_refused_by_routing_error_before_any_write(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with pytest.raises(RoutingError, match="неизвестный вид"):
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[position.id],
                rule={"kind": "мусор", "value": "x"}, actor_id=user.id,
            )
        # Ничего не создалось — отказ сработал ДО чтения/блокировки контекста.
        assert db_session.get(ContextMember, position.id).context_id == default_ctx.id


# ---------------------------------------------------------------------------
#  Разделить с правилом — правило обязано ПОКРЫВАТЬ выбранные членства
#  (MINOR-3, ревью задачи 6 раунд 3, решение оркестратора)
# ---------------------------------------------------------------------------

class TestSplitRuleMustCoverSelectedMembers:
    def _bucket_state(self, session, bucket_id):
        """(число контекстов, число правил) корзины — снимок «ничего не
        записалось» при отказе."""
        contexts = session.execute(
            sa.select(sa.func.count())
            .select_from(CatalogContext)
            .where(CatalogContext.bucket_id == bucket_id)
        ).scalar_one()
        rules = session.execute(
            sa.select(sa.func.count())
            .select_from(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == bucket_id)
        ).scalar_one()
        return contexts, rules

    def test_predicate_not_matching_the_member_is_refused_and_writes_nothing(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Q")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        default_ctx = db_session.get(CatalogContext, member.context_id)
        before = self._bucket_state(db_session, bucket.id)

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[position.id],
                rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Совсем другое"},
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_RULE_DOES_NOT_COVER
        assert excinfo.value.count == 1
        assert excinfo.value.position_item_ids == [position.id]

        db_session.refresh(member)
        assert member.context_id == default_ctx.id
        assert member.routed_by == RoutedBy.default.value
        assert self._bucket_state(db_session, bucket.id) == before

    def test_member_shadowed_by_an_earlier_rule_is_refused_and_writes_nothing(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Shadow")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        default_ctx = db_session.get(CatalogContext, member.context_id)

        elsewhere = _context(db_session, bucket)
        _rule(
            db_session, bucket, ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Shadow"},
            context=elsewhere, user=user,
        )
        before = self._bucket_state(db_session, bucket.id)

        # Новое правило метит В ТУ ЖЕ цепочку — без проверки затенения оно
        # выглядело бы «покрывающим», но существующее правило (ordinal=1,
        # раньше будущего max+1) перехватило бы позицию первым в реальной
        # маршрутизации.
        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[position.id],
                rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Shadow"},
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_RULE_DOES_NOT_COVER
        assert excinfo.value.count == 1

        db_session.refresh(member)
        assert member.context_id == default_ctx.id
        assert self._bucket_state(db_session, bucket.id) == before

    def test_two_uncovered_members_give_a_count_of_two_not_a_constant(
        self, db_session, factories
    ):
        """Число в отказе — СЧЁТ, а не константа `1`: вход из ДВУХ
        непокрытых членств отличил бы разницу (тот же приём, каким ревью
        задачи 6 предлагало закрыть NIT-1)."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter_a = _chapter(factories, proposal, title="Секция Раз", chapter_number="1")
        chapter_b = _chapter(factories, proposal, title="Секция Два", chapter_number="2")
        cp = factories.CatalogPositionFactory.create()
        position_a = _position(factories, proposal, chapter=chapter_a, catalog_position=cp)
        position_b = _position(
            factories, proposal, chapter=chapter_b, catalog_position=cp, title="Вторая"
        )
        member_a = route_position(db_session, position_item_id=position_a.id)
        route_position(db_session, position_item_id=position_b.id)
        default_ctx = db_session.get(CatalogContext, member_a.context_id)

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id,
                position_item_ids=[position_a.id, position_b.id],
                rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Ни с чем не совпадёт"},
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_RULE_DOES_NOT_COVER
        assert excinfo.value.count == 2
        assert sorted(excinfo.value.position_item_ids) == sorted([position_a.id, position_b.id])

    def test_refusal_names_exactly_the_uncovered_member_not_the_covered_one(
        self, db_session, factories
    ):
        """NIT-R2-2 (ревью задачи 6, раунд 2): вход СМЕШАННЫЙ — одно
        членство правило РЕАЛЬНО покрывает, другое нет. `position_item_ids`
        отказа обязан назвать ТОЛЬКО непокрытое (и его count — 1, а не 2):
        на входах, где выбранные == непокрытым целиком, список отказа от
        «всех выбранных» не отличить (R45 отчёта ревью, был зелёным)."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        covered_chapter = _chapter(
            factories, proposal, title="Секция Покрыта", chapter_number="1"
        )
        uncovered_chapter = _chapter(
            factories, proposal, title="Секция Не покрыта", chapter_number="2"
        )
        cp = factories.CatalogPositionFactory.create()
        covered_position = _position(
            factories, proposal, chapter=covered_chapter, catalog_position=cp, title="Покрыта"
        )
        uncovered_position = _position(
            factories, proposal, chapter=uncovered_chapter, catalog_position=cp,
            title="Не покрыта",
        )
        covered_member = route_position(db_session, position_item_id=covered_position.id)
        route_position(db_session, position_item_id=uncovered_position.id)
        default_ctx = db_session.get(CatalogContext, covered_member.context_id)

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id,
                position_item_ids=[covered_position.id, uncovered_position.id],
                rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Покрыта"},
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_RULE_DOES_NOT_COVER
        assert excinfo.value.count == 1
        assert excinfo.value.position_item_ids == [uncovered_position.id]

        db_session.refresh(covered_member)
        assert covered_member.context_id == default_ctx.id  # ничего не записалось

    def test_covered_member_stays_in_the_new_context_after_reroute(self, db_session, factories):
        """Утверждение MINOR-3: покрытое правилом членство НЕ откатывается
        молча при повторной переоценке маршрутизации (следующий импорт той
        же позиции, `_apply_routing` переоценивает не-`manual` членства)."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Stable")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        default_ctx = db_session.get(CatalogContext, member.context_id)

        result = split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Stable"},
            actor_id=user.id,
        )
        assert result.rule_id is not None

        rerouted = route_position(db_session, position_item_id=position.id)
        assert rerouted.context_id == result.new_context_id
        assert rerouted.routed_by == RoutedBy.rule.value
        assert rerouted.routing_rule_id == result.rule_id


# ---------------------------------------------------------------------------
#  Разделить — без правила (спека §2.4, решение оркестратора брифа)
# ---------------------------------------------------------------------------

class TestSplitWithoutRuleOnTheDefaultContext:
    def test_fresh_context_replaces_the_old_default_and_gets_next_imports(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        result = split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule=None, actor_id=user.id,
        )
        assert result.rule_id is None
        assert result.default_replaced is True
        assert result.moved_members == 1

        db_session.refresh(default_ctx)
        assert default_ctx.is_default is False
        assert default_ctx.archived_at is None  # НЕ архивируется — только теряет флаг

        fresh = db_session.execute(
            sa.select(CatalogContext).where(
                CatalogContext.bucket_id == bucket.id,
                CatalogContext.is_default.is_(True),
                CatalogContext.archived_at.is_(None),
            )
        ).scalar_one()
        assert fresh.id not in (default_ctx.id, result.new_context_id)

        moved = db_session.get(ContextMember, position.id)
        assert moved.context_id == result.new_context_id
        assert moved.routed_by == RoutedBy.manual.value
        assert moved.routing_rule_id is None

    def test_next_import_of_the_same_bucket_goes_to_the_fresh_context(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        proposal2, _ = _proposal(factories)

        split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule=None, actor_id=user.id,
        )

        # NIT-3 (ревью задачи 6 раунд 3): сравнение через `==` с id СВЕЖЕГО
        # умолчания (выборка по `is_default AND archived_at IS NULL`), а не
        # `!=` с двумя другими контекстами — иначе импорт в КАКОЙ-ТО третий
        # (несуществующий) контекст остался бы незамеченным.
        fresh_id = db_session.execute(
            sa.select(CatalogContext.id).where(
                CatalogContext.bucket_id == bucket.id,
                CatalogContext.is_default.is_(True),
                CatalogContext.archived_at.is_(None),
            )
        ).scalar_one()

        new_position = _position(factories, proposal2, catalog_position=cp, title="Новая работа")
        new_member = route_position(db_session, position_item_id=new_position.id)

        assert new_member.context_id == fresh_id
        assert new_member.routed_by == RoutedBy.default.value

    def test_writes_two_context_created_events_and_one_context_split(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        result = split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule=None, actor_id=user.id,
        )

        all_created = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.event_type == "context_created",
                SemanticEvent.payload["bucket_id"].as_integer() == bucket.id,
            )
        ).scalars().all()
        # Один context_created — на импорте (стартовый контекст по
        # умолчанию), два — на этом разделении (target + fresh).
        split_origin_events = [e for e in all_created if e.payload.get("origin") == "split"]
        assert len(split_origin_events) == 2
        contexts_with_created = {e.context_id for e in split_origin_events}
        assert contexts_with_created == {
            result.new_context_id,
            db_session.execute(
                sa.select(CatalogContext.id).where(
                    CatalogContext.bucket_id == bucket.id,
                    CatalogContext.is_default.is_(True),
                )
            ).scalar_one(),
        }

        split_events = _events(db_session, result.new_context_id, "context_split")
        assert len(split_events) == 1

    def test_routing_rules_dropped_event_is_never_written_by_split(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        split_context(
            db_session, context_id=default_ctx.id, position_item_ids=[position.id],
            rule=None, actor_id=user.id,
        )

        count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(SemanticEvent.event_type == "routing_rules_dropped")
        ).scalar_one()
        assert count == 0


class TestSplitWithoutRuleOnANonDefaultContext:
    def test_default_is_not_replaced_when_the_split_context_is_not_the_default(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        # Второй, НЕ-умолчательный контекст той же корзины с собственным членством
        # (перенос вручную — операция задачи 6, здесь строим прямой правкой,
        # как это делает test_context_membership.py).
        extra_ctx = _context(db_session, bucket, is_default=False)
        proposal2, _ = _proposal(factories)
        extra_position = _position(factories, proposal2, catalog_position=cp, title="В доп. контексте")
        member = route_position(db_session, position_item_id=extra_position.id)
        member.context_id = extra_ctx.id
        member.routed_by = RoutedBy.manual.value
        member.routing_rule_id = None
        db_session.flush()

        before_contexts = db_session.execute(
            sa.select(sa.func.count()).select_from(CatalogContext).where(
                CatalogContext.bucket_id == bucket.id
            )
        ).scalar_one()

        result = split_context(
            db_session, context_id=extra_ctx.id, position_item_ids=[extra_position.id],
            rule=None, actor_id=user.id,
        )
        assert result.default_replaced is False

        after_contexts = db_session.execute(
            sa.select(sa.func.count()).select_from(CatalogContext).where(
                CatalogContext.bucket_id == bucket.id
            )
        ).scalar_one()
        # Ровно ОДИН новый контекст (target), не два — свежий не заводится.
        assert after_contexts == before_contexts + 1

        db_session.refresh(default_ctx)
        assert default_ctx.is_default is True  # прежний default не тронут

        new_position = _position(factories, proposal2, catalog_position=cp, title="Импорт снова")
        new_member = route_position(db_session, position_item_id=new_position.id)
        assert new_member.context_id == default_ctx.id


class TestSplitRefusals:
    def test_empty_position_item_ids_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[],
                rule=None, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_INVALID_MEMBERSHIP

    def test_context_not_found_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=999_999_999, position_item_ids=[1],
                rule=None, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_archived_context_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        # Контекст архивируется мимо операции — прямой правкой строки (та же
        # техника, что test_context_routing.py::TestBucketWithoutLiveDefaultContextRaises).
        default_ctx.archived_at = _now()
        default_ctx.is_default = False
        db_session.flush()

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=default_ctx.id, position_item_ids=[position.id],
                rule=None, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_position_not_a_member_of_this_context_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        other_ctx = _context(db_session, bucket)

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                db_session, context_id=other_ctx.id, position_item_ids=[position.id],
                rule=None, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_INVALID_MEMBERSHIP
        assert position.id in excinfo.value.position_item_ids


# ---------------------------------------------------------------------------
#  Слить (спека §2.4)
# ---------------------------------------------------------------------------

class TestMergeContexts:
    def test_manual_membership_stays_manual_and_moves_to_target(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        member = db_session.get(ContextMember, position.id)
        member.context_id = default_ctx.id  # уже там
        member.routed_by = RoutedBy.manual.value
        member.routing_rule_id = None
        db_session.flush()

        moved = merge_contexts(
            db_session, source_context_id=default_ctx.id, target_context_id=target.id,
            actor_id=user.id,
        )
        assert moved == 1

        db_session.refresh(member)
        assert member.context_id == target.id
        assert member.routed_by == RoutedBy.manual.value

    def test_rule_pointing_at_source_is_translated_to_target_and_routing_proves_it(
        self, db_session, factories
    ):
        """Утверждение плана: проверяется МАРШРУТОМ, а не значением колонки."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Гамма")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)

        source = _context(db_session, bucket)
        target = _context(db_session, bucket)
        rule = _rule(
            db_session, bucket, ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Гамма"},
            context=source, user=user,
        )
        rerouted = route_position(db_session, position_item_id=position.id)
        assert rerouted.context_id == source.id
        assert rerouted.routing_rule_id == rule.id

        merge_contexts(
            db_session, source_context_id=source.id, target_context_id=target.id, actor_id=user.id
        )

        db_session.refresh(rule)
        assert rule.context_id == target.id

        new_position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Ещё в Гамме"
        )
        new_member = route_position(db_session, position_item_id=new_position.id)
        assert new_member.context_id == target.id
        assert new_member.routed_by == RoutedBy.rule.value
        assert new_member.routing_rule_id == rule.id

        # MINOR-2 (ревью задачи 6 раунд 3): сверка по всей базе В МЕСТЕ, где
        # правило РЕАЛЬНО было под угрозой — `source` только что архивирован
        # слиянием, и БЕЗ перевода правила (M16 отчёта ревью) оно осталось бы
        # висеть именно на нём.
        dangling = db_session.execute(
            sa.text(
                "SELECT count(*) FROM context_routing_rules r "
                "JOIN catalog_contexts c ON c.id = r.context_id "
                "WHERE c.archived_at IS NOT NULL"
            )
        ).scalar_one()
        assert dangling == 0

    def test_default_flag_transfers_from_source_to_target(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket, is_default=False)

        merge_contexts(
            db_session, source_context_id=default_ctx.id, target_context_id=target.id,
            actor_id=user.id,
        )

        db_session.refresh(default_ctx)
        db_session.refresh(target)
        assert default_ctx.is_default is False
        assert target.is_default is True
        assert default_ctx.archived_at is not None

    def test_default_flag_untouched_when_source_is_not_the_default(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        source = _context(db_session, bucket, is_default=False)
        target = _context(db_session, bucket, is_default=False)

        merge_contexts(
            db_session, source_context_id=source.id, target_context_id=target.id, actor_id=user.id
        )

        db_session.refresh(default_ctx)
        db_session.refresh(target)
        assert default_ctx.is_default is True
        assert target.is_default is False

    def test_writes_context_merged_and_context_archived_with_merge_reason(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        merge_contexts(
            db_session, source_context_id=default_ctx.id, target_context_id=target.id,
            actor_id=user.id,
        )

        merged = _events(db_session, default_ctx.id, "context_merged")
        assert len(merged) == 1
        assert merged[0].payload == {"into_context_id": target.id, "moved_members": 1}

        archived = _events(db_session, default_ctx.id, "context_archived")
        assert len(archived) == 1
        assert archived[0].payload == {"reason": "context_merge"}


class TestMergeRefusals:
    def test_same_context_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            merge_contexts(
                db_session, source_context_id=default_ctx.id, target_context_id=default_ctx.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_SAME_CONTEXT

    def test_source_not_found(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            merge_contexts(
                db_session, source_context_id=999_999_999, target_context_id=default_ctx.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_different_buckets_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket_a, default_a, position_a, cp_a = _seed_default_member(db_session, factories)
        bucket_b, default_b, position_b, cp_b = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            merge_contexts(
                db_session, source_context_id=default_a.id, target_context_id=default_b.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_DIFFERENT_BUCKET

    def test_source_already_archived_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)
        archived_source = _context(db_session, bucket, archived_at=_now())

        with pytest.raises(ContextOperationError) as excinfo:
            merge_contexts(
                db_session, source_context_id=archived_source.id, target_context_id=target.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_target_already_archived_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        archived_target = _context(db_session, bucket, archived_at=_now())

        with pytest.raises(ContextOperationError) as excinfo:
            merge_contexts(
                db_session, source_context_id=default_ctx.id, target_context_id=archived_target.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED


# ---------------------------------------------------------------------------
#  Перенести (спека §2.4)
# ---------------------------------------------------------------------------

class TestMoveMembers:
    def test_moved_membership_becomes_manual_with_no_rule(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        moved = move_members(
            db_session, position_item_ids=[position.id], target_context_id=target.id,
            actor_id=user.id, reason="manual",
        )
        assert moved == 1

        member = db_session.get(ContextMember, position.id)
        assert member.context_id == target.id
        assert member.routed_by == RoutedBy.manual.value
        assert member.routing_rule_id is None

    def test_one_event_per_distinct_source_context(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position_a, cp = _seed_default_member(db_session, factories)
        proposal_b, _ = _proposal(factories)
        position_b = _position(factories, proposal_b, catalog_position=cp, title="Б")
        member_b = route_position(db_session, position_item_id=position_b.id)
        assert member_b.context_id == default_ctx.id  # тот же default той же корзины

        other_source = _context(db_session, bucket)
        member_a = db_session.get(ContextMember, position_a.id)
        member_a.context_id = other_source.id
        member_a.routed_by = RoutedBy.manual.value
        db_session.flush()

        target = _context(db_session, bucket)

        moved = move_members(
            db_session, position_item_ids=[position_a.id, position_b.id],
            target_context_id=target.id, actor_id=user.id, reason="manual",
        )
        assert moved == 2

        events = _events(db_session, target.id, "members_moved")
        assert len(events) == 2
        by_source = {e.payload["from_context_id"]: e.payload["moved_members"] for e in events}
        assert by_source == {other_source.id: 1, default_ctx.id: 1}
        assert all(e.payload["reason"] == "manual" for e in events)


class TestMoveMembersRefusals:
    def test_invalid_reason_is_refused_before_any_read(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[position.id], target_context_id=target.id,
                actor_id=user.id, reason="совсем-не-то",
            )
        assert excinfo.value.code == REFUSE_INVALID_REASON

    def test_empty_position_item_ids_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[], target_context_id=target.id,
                actor_id=user.id, reason="manual",
            )
        assert excinfo.value.code == REFUSE_INVALID_MEMBERSHIP

    def test_position_without_membership_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket)

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[999_999_999], target_context_id=target.id,
                actor_id=user.id, reason="manual",
            )
        assert excinfo.value.code == REFUSE_INVALID_MEMBERSHIP

    def test_target_not_found_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[position.id], target_context_id=999_999_999,
                actor_id=user.id, reason="manual",
            )
        assert excinfo.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_different_bucket_is_refused(self, db_session, factories):
        bucket_a, default_a, position_a, cp_a = _seed_default_member(db_session, factories)
        bucket_b, default_b, position_b, cp_b = _seed_default_member(db_session, factories)

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[position_a.id], target_context_id=default_b.id,
                actor_id=1, reason="manual",
            )
        assert excinfo.value.code == REFUSE_DIFFERENT_BUCKET

    def test_archived_target_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        archived_target = _context(db_session, bucket, archived_at=_now())

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                db_session, position_item_ids=[position.id], target_context_id=archived_target.id,
                actor_id=user.id, reason="manual",
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED


# ---------------------------------------------------------------------------
#  Архивировать — три предусловия, каждое со своим кодом и числом (спека §2.8)
# ---------------------------------------------------------------------------

class TestArchiveContextRefusals:
    def test_not_empty_names_the_member_count(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        # default_ctx несёт одно членство — но он ещё и default; проверим
        # предусловие НЕ-пустоты на НЕ-умолчательном контексте, чтобы не
        # спутать с предусловием 3.
        non_default_with_member = _context(db_session, bucket, is_default=False)
        member = db_session.get(ContextMember, position.id)
        member.context_id = non_default_with_member.id
        member.routed_by = RoutedBy.manual.value
        db_session.flush()

        # Второе членство — число в отказе обязано быть СЧЁТОМ, а не
        # константой `1` (NIT-1, ревью задачи 6 раунд 3): вход с одним
        # членством не отличает подсчёт от заглушки.
        proposal2, _ = _proposal(factories)
        position2 = _position(factories, proposal2, catalog_position=cp)
        member2 = route_position(db_session, position_item_id=position2.id)
        member2.context_id = non_default_with_member.id
        member2.routed_by = RoutedBy.manual.value
        db_session.flush()

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=non_default_with_member.id,
                new_default_context_id=None, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_NOT_EMPTY
        assert excinfo.value.count == 2

    def test_incoming_rules_names_the_rule_count(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        empty_ctx = _context(db_session, bucket, is_default=False)
        _rule(
            db_session, bucket, ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "x"},
            context=empty_ctx, user=user,
        )
        # Второе правило — та же причина, что у числа членств: счёт, а не
        # константа `1` (NIT-1).
        _rule(
            db_session, bucket, ordinal=2,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "y"},
            context=empty_ctx, user=user,
        )

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=empty_ctx.id, new_default_context_id=None,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_INCOMING_RULES
        assert excinfo.value.count == 2

        # MINOR-2 (ревью задачи 6 раунд 3): сверка по всей базе В МЕСТЕ, где
        # правило РЕАЛЬНО под угрозой — оба правила ведут именно в
        # `empty_ctx`, и отказ обязан оставить его НЕ архивным.
        dangling = db_session.execute(
            sa.text(
                "SELECT count(*) FROM context_routing_rules r "
                "JOIN catalog_contexts c ON c.id = r.context_id "
                "WHERE c.archived_at IS NOT NULL"
            )
        ).scalar_one()
        assert dangling == 0
        db_session.refresh(empty_ctx)
        assert empty_ctx.archived_at is None

    def test_default_without_successor_names_the_bucket(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        # default_ctx несёт членство — снимем его перед проверкой третьего
        # предусловия, иначе сработает первое.
        member = db_session.get(ContextMember, position.id)
        db_session.delete(member)
        db_session.flush()

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=default_ctx.id, new_default_context_id=None,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_DEFAULT_WITHOUT_SUCCESSOR
        assert excinfo.value.bucket_id == bucket.id

    def test_context_not_found(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=999_999_999, new_default_context_id=None,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_already_archived_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        already = _context(db_session, bucket, is_default=False, archived_at=_now())

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=already.id, new_default_context_id=None,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_new_default_from_another_bucket_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket_a, default_a, position_a, cp_a = _seed_default_member(db_session, factories)
        bucket_b, default_b, position_b, cp_b = _seed_default_member(db_session, factories)
        db_session.delete(db_session.get(ContextMember, position_a.id))
        db_session.flush()

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=default_a.id, new_default_context_id=default_b.id,
                actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_INVALID_NEW_DEFAULT

    def test_new_default_already_archived_is_refused(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        db_session.delete(db_session.get(ContextMember, position.id))
        db_session.flush()
        archived_candidate = _context(db_session, bucket, is_default=False, archived_at=_now())

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                db_session, context_id=default_ctx.id,
                new_default_context_id=archived_candidate.id, actor_id=user.id,
            )
        assert excinfo.value.code == REFUSE_INVALID_NEW_DEFAULT


class TestArchiveContextPositiveInput:
    def test_empty_non_default_context_without_rules_archives(self, db_session, factories):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket, is_default=False)

        archive_context(
            db_session, context_id=target.id, new_default_context_id=None, actor_id=user.id
        )

        db_session.refresh(target)
        assert target.archived_at is not None

        events = _events(db_session, target.id, "context_archived")
        assert len(events) == 1
        assert events[0].payload == {"reason": "operator"}

    def test_no_rule_routes_into_an_archived_context_anywhere_in_the_database(
        self, db_session, factories
    ):
        """Сверка по ВСЕЙ базе — отдельный SQL, не через код модуля (план,
        Task 6, положительный вход)."""
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        target = _context(db_session, bucket, is_default=False)
        _rule(
            db_session, bucket, ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "куда-то ещё"},
            context=default_ctx, user=user,
        )

        archive_context(
            db_session, context_id=target.id, new_default_context_id=None, actor_id=user.id
        )

        dangling = db_session.execute(
            sa.text(
                "SELECT count(*) FROM context_routing_rules r "
                "JOIN catalog_contexts c ON c.id = r.context_id "
                "WHERE c.archived_at IS NOT NULL"
            )
        ).scalar_one()
        assert dangling == 0

    def test_default_context_archives_with_successor_and_flag_transfers(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        successor = _context(db_session, bucket, is_default=False)
        db_session.delete(db_session.get(ContextMember, position.id))
        db_session.flush()

        archive_context(
            db_session, context_id=default_ctx.id, new_default_context_id=successor.id,
            actor_id=user.id,
        )

        db_session.refresh(default_ctx)
        db_session.refresh(successor)
        assert default_ctx.archived_at is not None
        assert default_ctx.is_default is False
        assert successor.is_default is True
        assert successor.archived_at is None


class TestNoRoutingRulesDroppedEventAnywhere:
    def test_structural_absence_across_all_operations(self, db_session, factories):
        """NIT-4 (ревью задачи 6 раунд 3): `archive_context` теперь тоже
        участвует в сцене (раньше проверялись только слияние и перенос), и
        сливаемый источник несёт РЕАЛЬНОЕ правило — запись
        `routing_rules_dropped`, УСЛОВНАЯ на «есть ли у источника правила»,
        раньше проходила бы этот тест незамеченной: пустой список правил не
        отличается от условия «правил нет, поэтому не пишем»."""
        user = factories.UserFactory.create()
        bucket, default_ctx, position, cp = _seed_default_member(db_session, factories)
        move_target = _context(db_session, bucket, is_default=False)
        move_members(
            db_session, position_item_ids=[position.id], target_context_id=move_target.id,
            actor_id=user.id, reason="manual",
        )
        # Сливаемый источник несёт РЕАЛЬНОЕ входящее правило (переводится на
        # цель, не отбрасывается).
        _rule(
            db_session, bucket, ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "неважно"},
            context=move_target, user=user,
        )
        merge_target = _context(db_session, bucket, is_default=False)
        merge_contexts(
            db_session, source_context_id=move_target.id, target_context_id=merge_target.id,
            actor_id=user.id,
        )

        empty_ctx = _context(db_session, bucket, is_default=False)
        archive_context(
            db_session, context_id=empty_ctx.id, new_default_context_id=None, actor_id=user.id
        )

        count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(SemanticEvent.event_type == "routing_rules_dropped")
        ).scalar_one()
        assert count == 0

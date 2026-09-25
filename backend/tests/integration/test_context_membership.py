"""Явное членство: `routed_by='manual'` переживает повторную маршрутизацию, два
разных факта «нет членства» — два счётчика, контекст по умолчанию рождается с
видом/ролью по правилам и пишет `context_created` (спека
`2026-09-22-catalog-families-design.md` §2.1–§2.5, §2.14; план, задача 4).

Помощники — локальная копия набора `test_context_routing.py`, не импорт
(докстрока того файла: наборы помощников тестов друг у друга не импортируют).
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    CatalogKind,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    UnitOfMeasure,
)
from services.context_routing import RoutingError, route_position, route_positions
from services.semantic_rules import PLACE_DICTIONARY_VERSION

pytestmark = pytest.mark.integration


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot), estimate


def _chapter(factories, proposal, *, title="Раздел", chapter_number="1"):
    return factories.PositionItemFactory.create(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal=chapter_number,
    )


def _position(
    factories, proposal, *, chapter=None, title="Работа", catalog_position=None, unit_id=None
):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    if unit_id is not None:
        kwargs["unit_id"] = unit_id
    return factories.PositionItemFactory.create(**kwargs)


def _extra_context(session, bucket, **overrides) -> CatalogContext:
    """Второй (не-умолчательный) контекст той же корзины — заводится напрямую,
    как результат операции «разделить» (задача 6, вне границ этой задачи)."""
    now = _now()
    defaults = dict(
        bucket_id=bucket.id,
        is_default=False,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source="rule",
        semantic_kind_at=now,
        name_role="WORK",
        name_role_source="rule",
        name_role_at=now,
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


# ---------------------------------------------------------------------------
#  `routed_by='manual'` переживает повторную маршрутизацию (спека §2.4)
# ---------------------------------------------------------------------------

class TestManualRoutingSurvivesReroute:
    def test_manual_membership_is_not_rewritten_even_when_a_rule_points_elsewhere(
        self, db_session, factories
    ):
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Особая")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)

        elsewhere = _extra_context(db_session, bucket)
        manual_target = _extra_context(db_session, bucket)

        # Оператор перенёс членство вручную (операция «перенести» — задача 6;
        # здесь — прямая правка строки, что производство и делает).
        member.context_id = manual_target.id
        member.routed_by = RoutedBy.manual.value
        member.routing_rule_id = None
        db_session.flush()

        # Правило корзины ДЕЙСТВИТЕЛЬНО срабатывает на этой позиции и указывает
        # на `elsewhere` — если бы проверка на `manual` не читалась, членство
        # переписалось бы именно на него.
        user = factories.UserFactory.create()
        rule = ContextRoutingRule(
            bucket_id=bucket.id,
            ordinal=1,
            predicate={"kind": "chapter_chain_contains", "value": "Секция Особая"},
            context_id=elsewhere.id,
            created_by=user.id,
        )
        db_session.add(rule)
        db_session.flush()

        reroute = route_position(db_session, position_item_id=position.id)

        assert reroute.context_id == manual_target.id
        assert reroute.routed_by == RoutedBy.manual.value
        assert reroute.routing_rule_id is None
        assert reroute.context_id != elsewhere.id


# ---------------------------------------------------------------------------
#  Два разных факта «нет членства» — два счётчика (спека §2.2, §2.4)
# ---------------------------------------------------------------------------

class TestSkipCountersAreTwoDifferentFacts:
    def test_chapters_and_unmatched_positions_count_separately(self, db_session, factories):
        """Вход асимметричен (2 раздела, 1 без каталога) — при симметричном
        входе (1/1) перестановка двух счётчиков местами осталась бы зелёной;
        оба значения проверены порознь, а не только суммой."""
        proposal, estimate = _proposal(factories)

        # Два раздела — is_chapter=True: не работа, членства не имеют ВООБЩЕ.
        _chapter(factories, proposal, title="Раздел А, не работа", chapter_number="1")
        _chapter(factories, proposal, title="Раздел Б, не работа", chapter_number="2")

        # Одна позиция без catalog_position_id — не сопоставлена с каталогом.
        unmatched = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Без каталога"
        )

        # Обычная маршрутизируемая позиция.
        cp = factories.CatalogPositionFactory.create()
        routable = _position(factories, proposal, catalog_position=cp, title="Маршрутизируемая")

        outcome = route_positions(db_session, estimate_ids=[estimate.id])

        assert outcome.members_skipped_chapters == 2
        assert outcome.members_skipped_unmatched == 1
        assert outcome.members_created == 1
        assert outcome.buckets_created == 1
        assert outcome.contexts_created == 1

        assert db_session.get(ContextMember, unmatched.id) is None
        assert db_session.get(ContextMember, routable.id) is not None


# ---------------------------------------------------------------------------
#  Контекст создаётся по правилам вида/роли, пишет context_created (спека §2.5, §2.14)
# ---------------------------------------------------------------------------

class TestNewContextGetsKindAndRoleByRules:
    def test_position_kind_creates_suggested_context_with_rule_source(
        self, db_session, factories
    ):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
        position = _position(factories, proposal, catalog_position=cp, title="Устройство стяжки")

        member = route_position(db_session, position_item_id=position.id, origin="import")
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_state == SemanticState.SUGGESTED.value
        assert context.semantic_kind_source == "rule"
        assert context.name_role_source == "rule"
        assert context.semantic_kind_by is None
        assert context.name_role_by is None
        assert context.place_dictionary_version == PLACE_DICTIONARY_VERSION
        assert context.is_default is True

        event = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.context_id == context.id,
                SemanticEvent.event_type == "context_created",
            )
        ).scalar_one()
        assert event.payload["bucket_id"] == member.bucket_id
        assert event.payload["origin"] == "import"

    def test_header_kind_creates_not_applicable_context(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(kind=CatalogKind.HEADER.value)
        position = _position(factories, proposal, catalog_position=cp, title="Заголовок раздела")

        member = route_position(db_session, position_item_id=position.id, origin="backfill")
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value
        # Вид и роль всё равно поставлены правилом — semantic_state описывает
        # РОВНО ОДНО решение (вид работы), а не «есть ли решение вообще»
        # (спека §2.5).
        assert context.semantic_kind_source == "rule"
        assert context.name_role_source == "rule"
        assert context.place_dictionary_version == PLACE_DICTIONARY_VERSION

        event = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.context_id == context.id,
                SemanticEvent.event_type == "context_created",
            )
        ).scalar_one()
        assert event.payload["bucket_id"] == member.bucket_id
        assert event.payload["origin"] == "backfill"

    def test_trash_kind_also_creates_not_applicable_context(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(kind=CatalogKind.TRASH.value)
        position = _position(factories, proposal, catalog_position=cp, title="Мусорная строка")

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value

    def test_lot_header_kind_also_creates_not_applicable_context(self, db_session, factories):
        """Пятая (и последняя) точка множества `{HEADER, LOT_HEADER, TRASH}`
        — `test_header_kind_...`/`test_trash_kind_...` выше покрывают только
        две из трёх."""
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(kind=CatalogKind.LOT_HEADER.value)
        position = _position(factories, proposal, catalog_position=cp, title="Заголовок лота")

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value

    def test_to_review_kind_creates_suggested_context(self, db_session, factories):
        """Вторая точка множества `{TO_REVIEW, POSITION}` —
        `test_position_kind_...` выше покрывает только одну из двух."""
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        position = _position(factories, proposal, catalog_position=cp, title="На разбор")

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_state == SemanticState.SUGGESTED.value

    def test_name_role_uses_the_routed_positions_own_chain_not_a_constant(
        self, db_session, factories
    ):
        """Написание каталожной строки само по себе — чистое имя
        места («Секция 1»), а в цепочке РОУТИМОЙ позиции есть «рабочий»
        раздел («Устройство стяжки») — роль обязана стать `LOCATION_ONLY`
        (не константный `WORK`), а `comparability_reason` — `None` (работа
        уже названа рабочим разделом цепочки). Если бы код передавал в
        `classify_name_role` ПОСТОЯННУЮ пустую цепочку вместо реальной
        `chapters.chain` позиции, рабочий раздел не нашёлся бы, и
        `comparability_reason` стал бы `insufficient_description` — эта
        проверка красится и на «константный WORK», и на «нулёванную
        причину», и на «пустую цепочку»."""
        proposal, _ = _proposal(factories)
        working_chapter = _chapter(factories, proposal, title="Устройство стяжки")
        cp = factories.CatalogPositionFactory.create(standard_job_title="Секция 1")
        position = _position(
            factories, proposal, chapter=working_chapter, catalog_position=cp,
            title="Секция 1 деталь",
        )

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.name_role == "LOCATION_ONLY"
        assert context.comparability_reason is None

    def test_name_role_insufficient_description_without_a_working_chapter(
        self, db_session, factories
    ):
        """Вторая половина предыдущего теста: то же чистое место, но БЕЗ
        рабочего раздела в цепочке (сам раздел — тоже место) —
        `comparability_reason` обязан стать `insufficient_description`, а не
        остаться пустым."""
        proposal, _ = _proposal(factories)
        place_chapter = _chapter(factories, proposal, title="Секция 2")
        cp = factories.CatalogPositionFactory.create(standard_job_title="Корпус 3")
        position = _position(factories, proposal, chapter=place_chapter, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.name_role == "LOCATION_ONLY"
        assert context.comparability_reason == "insufficient_description"

    def test_semantic_kind_by_unit_wiring_system_unit_gives_system_kind(
        self, db_session, factories
    ):
        unit = db_session.execute(
            sa.select(UnitOfMeasure).where(UnitOfMeasure.code == "SET")
        ).scalar_one()

        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(unit_id=unit.id)
        position = _position(factories, proposal, catalog_position=cp, title="Комплект дверной")

        member = route_position(db_session, position_item_id=position.id)
        context = db_session.get(CatalogContext, member.context_id)

        assert context.semantic_kind == SemanticKind.SYSTEM.value


# ---------------------------------------------------------------------------
#  origin: валидируется на входе ОБЕИХ функций, всегда, ДО любой записи
# ---------------------------------------------------------------------------

def _counts(session) -> tuple[int, int, int]:
    """(корзины, контексты, членства) — снимок для проверки «ничего не
    создалось»."""
    return (
        session.execute(sa.select(sa.func.count()).select_from(ContextBucket)).scalar_one(),
        session.execute(sa.select(sa.func.count()).select_from(CatalogContext)).scalar_one(),
        session.execute(sa.select(sa.func.count()).select_from(ContextMember)).scalar_one(),
    )


class TestOriginValidation:
    def test_invalid_origin_is_refused_by_route_position_even_on_an_existing_bucket(
        self, db_session, factories
    ):
        """Невалидный `origin` на УЖЕ существующей корзине обязан отказывать
        не хуже, чем на новой: проверка стоит на входе ОБЕИХ функций, а не
        только в пути создания контекста. Прогрев маршрутизирует позицию
        валидным `origin`, ВТОРОЙ вызов — заведомо невалидным, на ту же (уже
        существующую) корзину."""
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        route_position(db_session, position_item_id=position.id)

        with pytest.raises(RoutingError, match="origin"):
            route_position(db_session, position_item_id=position.id, origin="совсем-не-то")

    def test_invalid_origin_is_refused_by_route_positions_even_on_an_existing_bucket(
        self, db_session, factories
    ):
        proposal, estimate = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        _position(factories, proposal, catalog_position=cp)

        route_positions(db_session, estimate_ids=[estimate.id])

        with pytest.raises(RoutingError, match="origin"):
            route_positions(db_session, estimate_ids=[estimate.id], origin="совсем-не-то")

    def test_invalid_origin_on_a_new_bucket_creates_nothing_via_route_position(
        self, db_session, factories
    ):
        """Проверка на СУЩЕСТВУЮЩЕЙ корзине не различает «origin проверяется
        первым» от «origin проверяется последним, но корзина уже была»: в
        обоих случаях ничего нового не создаётся, и тест выше остался бы
        зелёным при переносе проверки в конец функции. Здесь корзина — ВПЕРВЫЕ
        встреченная пара (написание × статья); если бы проверка origin стояла
        не первой строкой, к моменту отказа уже успели бы вставиться корзина,
        контекст и членство — и счётчики после отличались бы от снятых до."""
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        before = _counts(db_session)
        with pytest.raises(RoutingError, match="origin"):
            route_position(db_session, position_item_id=position.id, origin="совсем-не-то")
        db_session.flush()
        after = _counts(db_session)

        assert after == before

    def test_invalid_origin_on_a_new_bucket_creates_nothing_via_route_positions(
        self, db_session, factories
    ):
        proposal, estimate = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        _position(factories, proposal, catalog_position=cp)

        before = _counts(db_session)
        with pytest.raises(RoutingError, match="origin"):
            route_positions(db_session, estimate_ids=[estimate.id], origin="совсем-не-то")
        db_session.flush()
        after = _counts(db_session)

        assert after == before

    def test_default_origin_is_import_on_both_functions(self, db_session, factories):
        proposal_a, _ = _proposal(factories)
        cp_a = factories.CatalogPositionFactory.create()
        position_a = _position(factories, proposal_a, catalog_position=cp_a)
        member_a = route_position(db_session, position_item_id=position_a.id)
        event_a = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.context_id == member_a.context_id,
                SemanticEvent.event_type == "context_created",
            )
        ).scalar_one()
        assert event_a.payload["origin"] == "import"

        proposal_b, estimate_b = _proposal(factories)
        cp_b = factories.CatalogPositionFactory.create()
        position_b = _position(factories, proposal_b, catalog_position=cp_b)
        route_positions(db_session, estimate_ids=[estimate_b.id])
        member_b = db_session.get(ContextMember, position_b.id)
        event_b = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.context_id == member_b.context_id,
                SemanticEvent.event_type == "context_created",
            )
        ).scalar_one()
        assert event_b.payload["origin"] == "import"

    def test_backfill_origin_passes_through_route_positions_too(self, db_session, factories):
        proposal, estimate = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        route_positions(db_session, estimate_ids=[estimate.id], origin="backfill")
        member = db_session.get(ContextMember, position.id)
        event = db_session.execute(
            sa.select(SemanticEvent).where(
                SemanticEvent.context_id == member.context_id,
                SemanticEvent.event_type == "context_created",
            )
        ).scalar_one()
        assert event.payload["origin"] == "backfill"

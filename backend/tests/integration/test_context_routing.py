"""Маршрутизация позиции: корзины, контексты, правила, блокировка (спека
`2026-09-22-catalog-families-design.md` §2.1–§2.5, §2.8; план, задача 4).

Помощники (`_proposal`, `_chapter`, `_position`, `_leaf_category_ids`,
`_context`, `_rule`) — локальные: наборы помощников тестов этого проекта
друг у друга не импортируют (докстрока `test_passport_article_rates.py`,
`test_project_passport_api.py`), тот же приём и у `test_context_membership.py`
рядом — с намеренно НЕ разделяемыми копиями.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import threading
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

import services.context_routing as context_routing_module
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
    SemanticKind,
    SemanticState,
    WorkCategory,
)
from services.context_routing import (
    PREDICATE_CHAPTER_CHAIN_CONTAINS,
    PREDICATE_CHAPTER_LEVEL_EQUALS,
    PREDICATE_NEAREST_CHAPTER_EQUALS,
    ChapterContext,
    RoutingError,
    chapter_context,
    effective_category_id,
    evaluate_predicate,
    lock_buckets,
    route_position,
    route_positions,
)

pytestmark = pytest.mark.integration


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot), estimate


def _chapter(
    factories,
    proposal,
    *,
    title="Раздел",
    chapter_number="1",
    parent=None,
    category_id=None,
    category_source="file",
):
    kwargs = dict(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal=chapter_number,
    )
    if parent is not None:
        kwargs["chapter_item_id"] = parent.id
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = category_source
    return factories.PositionItemFactory.create(**kwargs)


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


def _leaf_category_ids(session, n=1) -> list[int]:
    """N различных ЛИСТЬЕВ классификатора (не использованных как parent_id) —
    тот же приём, что `test_category_override_concurrency.py::scene`."""
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return list(
        session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(n)
        )
        .scalars()
        .all()
    )


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


def _category_id_by_code(session, code: str) -> int:
    """Статья классификатора по КОДУ, не по id: id справочника может
    сдвинуться при пересеве, код — устойчивый идентификатор строки
    seed-миграции 0005 (`2026_08_05_0005-work_categories.py`)."""
    return session.execute(
        sa.select(WorkCategory.id).where(WorkCategory.code == code)
    ).scalar_one()


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


def _wait_until_backend_blocks(
    probe_factory, *, pid: int, contains: str, timeout: float = 15.0
) -> bool:
    """Ждёт, пока УКАЗАННЫЙ backend (`pid`) не встанет в очередь ИМЕННО этого
    запроса.

    НЕ копия generic-хелпера `test_category_override_concurrency.py`: тот
    считает ЛЮБОЕ ожидание любого backend'а в базе — `docs/pitfalls/db.md`
    называет это отдельной ловушкой того же теста («ассерт зелен и когда
    ждёт посторонний backend»). Здесь блокировка проверяется через
    `pg_blocking_pids(pid)` конкретного backend'а И текст его запроса.
    """
    deadline = time.monotonic() + timeout
    with probe_factory() as probe:
        while time.monotonic() < deadline:
            row = probe.execute(
                sa.text(
                    "SELECT cardinality(pg_blocking_pids(:pid)) > 0 AS blocked, "
                    "(SELECT query FROM pg_stat_activity WHERE pid = :pid) AS query"
                ),
                {"pid": pid},
            ).one()
            probe.rollback()  # не держим свой снимок
            if row.blocked and contains in (row.query or ""):
                return True
            time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
#  Эффективная статья — у ближайшего раздела, не у позиции (спека §1.7, §2.2)
# ---------------------------------------------------------------------------

class TestEffectiveCategoryFromChapterNotPosition:
    def test_position_without_a_chapter_has_no_effective_category(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        chapters = chapter_context(db_session, position.id)
        assert chapters == ChapterContext(
            chain=(), nearest=None, category_id=None, category_source=None
        )
        assert effective_category_id(db_session, position.id) is None

    def test_chapter_without_a_category_has_no_effective_category(self, db_session, factories):
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Раздел без статьи")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        chapters = chapter_context(db_session, position.id)
        assert chapters.nearest == "Раздел без статьи"
        assert chapters.category_id is None
        assert chapters.category_source is None
        assert effective_category_id(db_session, position.id) is None

    def test_chapter_with_a_category_gives_that_category(self, db_session, factories):
        (category_id,) = _leaf_category_ids(db_session, 1)
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Раздел А", category_id=category_id)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        chapters = chapter_context(db_session, position.id)
        assert chapters.category_id == category_id
        assert chapters.category_source == "file"
        assert effective_category_id(db_session, position.id) == category_id

    def test_manual_category_override_on_chapter_moves_position_to_target_bucket(
        self, db_session, factories
    ):
        category_a, category_b = _leaf_category_ids(db_session, 2)
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Раздел А", category_id=category_a)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member_before = route_position(db_session, position_item_id=position.id)
        bucket_before = db_session.get(ContextBucket, member_before.bucket_id)
        assert bucket_before.work_category_id == category_a

        # Ручной разнос статьи раздела — прямой правкой строки (то, во что
        # материализуется `CategoryResolver` после `set_override`; сама
        # услуга вне границ задачи 4, спека §1.7).
        chapter.work_category_id = category_b
        chapter.category_source = "manual"
        db_session.flush()

        member_after = route_position(db_session, position_item_id=position.id)
        bucket_after = db_session.get(ContextBucket, member_after.bucket_id)
        assert bucket_after.work_category_id == category_b
        assert bucket_after.id != bucket_before.id
        assert member_after.bucket_id == bucket_after.id
        assert member_after.routed_by == RoutedBy.default.value

    def test_two_level_chain_uses_the_nearest_chapters_category_not_the_root(
        self, db_session, factories
    ):
        """Корень и ближайший раздел несут РАЗНЫЕ статьи — взятие статьи у
        корня обязано покраснеть этот тест. Заодно проверяет порядок и
        состав `chain` (ближайший раздел первым)."""
        category_root, category_nearest = _leaf_category_ids(db_session, 2)
        proposal, _ = _proposal(factories)
        root = _chapter(
            factories, proposal, title="Раздел корень", chapter_number="1",
            category_id=category_root,
        )
        nearest = _chapter(
            factories,
            proposal,
            title="Подраздел ближний",
            chapter_number="1.1",
            parent=root,
            category_id=category_nearest,
        )
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=nearest, catalog_position=cp)

        chapters = chapter_context(db_session, position.id)
        assert chapters.chain == ("Подраздел ближний", "Раздел корень")
        assert chapters.nearest == "Подраздел ближний"
        assert chapters.category_id == category_nearest
        assert chapters.category_id != category_root
        assert effective_category_id(db_session, position.id) == category_nearest

        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        assert bucket.work_category_id == category_nearest


# ---------------------------------------------------------------------------
#  Ни одно правило не сработало → действующий контекст по умолчанию (спека §2.4)
# ---------------------------------------------------------------------------

class TestNoRuleFallsBackToLiveDefaultContext:
    def test_no_rule_routes_to_live_default_context(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)

        assert member.routed_by == RoutedBy.default.value
        assert member.routing_rule_id is None

        default_ctx = db_session.execute(
            sa.select(CatalogContext).where(
                CatalogContext.bucket_id == member.bucket_id,
                CatalogContext.is_default.is_(True),
            )
        ).scalar_one()
        assert member.context_id == default_ctx.id


class TestBucketWithoutLiveDefaultContextRaises:
    def test_raises_naming_the_bucket(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        bucket_id = member.bucket_id

        default_ctx = db_session.execute(
            sa.select(CatalogContext).where(
                CatalogContext.bucket_id == bucket_id, CatalogContext.is_default.is_(True)
            )
        ).scalar_one()
        # Прямая правка строки в обход операции: сама операция архивирования
        # (задача 6) не даёт архивировать единственный действующий default —
        # этот вход строится МИМО неё, а не через неё (спека §2.4).
        default_ctx.archived_at = _now()
        db_session.flush()

        # `match=str(bucket_id)` была бы подстрокой — совпала бы и с чужим
        # числом, где-то содержащим те же цифры; якорим ТОЧНОЙ фразой,
        # которой сообщение называет корзину.
        with pytest.raises(RoutingError, match=re.escape(f"корзины {bucket_id} нет")):
            route_position(db_session, position_item_id=position.id)


# ---------------------------------------------------------------------------
#  Отказы route_position на самой позиции
# ---------------------------------------------------------------------------

class TestRoutePositionRefusals:
    def test_position_not_found_is_refused(self, db_session):
        with pytest.raises(RoutingError, match="не найдена"):
            route_position(db_session, position_item_id=999_999_999)

    def test_chapter_row_has_no_membership(self, db_session, factories):
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Раздел")

        with pytest.raises(RoutingError, match="раздел"):
            route_position(db_session, position_item_id=chapter.id)

    def test_position_without_catalog_position_id_is_refused(self, db_session, factories):
        proposal, _ = _proposal(factories)
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Без каталога"
        )

        with pytest.raises(RoutingError, match="не сопоставлена"):
            route_position(db_session, position_item_id=position.id)


# ---------------------------------------------------------------------------
#  Цикл в chapter_item_id — громкий отказ, не бесконечный цикл
# ---------------------------------------------------------------------------

#: Окно ожидания `chapter_context` в фоновом потоке: здоровый путь (страж на
#: месте) укладывается в доли секунды даже под нагрузкой общего кластера — 30 с
#: оставляют большой запас, а снятый страж всё равно уйдёт в красный ВНУТРИ
#: окна, а не зависнет навсегда (замерено: под конкурентной нагрузкой узкого
#: набора тест однажды упал по истечении 5-секундного окна на здоровом коде —
#: не хватало запаса, а не механизма).
_CYCLE_GUARD_JOIN_TIMEOUT = 30.0


class TestChapterCycleGuard:
    def test_cycle_in_chapter_item_id_raises_loudly(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Без стража `chapter_context` уходит в БЕСКОНЕЧНЫЙ Python-цикл —
        `pytest-timeout` в проекте не установлен, и снятие стража способно
        повесить не один тест, а весь прогон: демон-поток с общей
        транзакционной сессией держал бы её открытой вечно, и следующий
        `TRUNCATE` (teardown любого следующего теста) стал бы его жертвой —
        именно так теряются целые прогоны. Поэтому вызов идёт в
        ОТДЕЛЬНОЙ, ВЫДЕЛЕННОЙ сессии (`committing_session_factory`, не
        `db_session`, которым пользуются остальные фикстуры), в фоновом
        демоне-потоке, с `join(timeout=...)`. Если поток не успел — backend
        этой выделенной сессии убивается НА УРОВНЕ POSTGRES
        (`pg_terminate_backend`) через отдельную пробную сессию ДО того, как
        тест вернёт управление; тест не полагается на то, что Python-поток
        когда-нибудь сам заметит обрыв и остановится.
        """
        proposal, _ = _proposal(committing_factories)
        chapter_a = _chapter(committing_factories, proposal, title="A", chapter_number="1")
        chapter_b = _chapter(
            committing_factories, proposal, title="B", chapter_number="2", parent=chapter_a
        )
        # Цикл строится ПРЯМОЙ правкой строки, в обход операции: составной FK
        # `fk_position_items_chapter` этого не запрещает — он проверяет, что
        # цель существует и принадлежит той же смете, но не то, что граф
        # ссылок ацикличен.
        chapter_a.chapter_item_id = chapter_b.id
        committing_db.flush()

        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(
            committing_factories, proposal, chapter=chapter_b, catalog_position=cp
        )
        committing_db.commit()

        worker_db = committing_session_factory()
        worker_pid = worker_db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()

        outcome: dict[str, object] = {}

        def worker():
            try:
                chapter_context(worker_db, position.id)
            except Exception as exc:  # noqa: BLE001 — тест сам решает дальше
                outcome["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=_CYCLE_GUARD_JOIN_TIMEOUT)

        if thread.is_alive():
            # Страж снят (регрессия) — поток завис внутри бесконечного цикла.
            # Убиваем его backend НАСИЛЬНО, а не ждём, пока Python заметит.
            with committing_session_factory() as probe:
                probe.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": worker_pid})
                probe.commit()
            thread.join(timeout=10)

        with contextlib.suppress(Exception):  # соединение уже могло умереть
            worker_db.close()

        assert not thread.is_alive(), (
            "chapter_context завис — страж цикла (visited-set) не сработал, "
            "и backend не удалось остановить даже через pg_terminate_backend"
        )
        assert isinstance(outcome.get("error"), RoutingError)
        assert "цикл" in str(outcome["error"])


# ---------------------------------------------------------------------------
#  Повторная маршрутизация НЕ-ручного членства
# ---------------------------------------------------------------------------

class TestRerouteOfNonManualMembership:
    def test_routing_rule_id_changes_when_a_different_rule_now_fires(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Альфа")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member0 = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member0.bucket_id)

        context_x = _context(db_session, bucket)
        rule_x = _rule(
            db_session,
            bucket,
            ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Альфа"},
            context=context_x,
            user=user,
        )
        member1 = route_position(db_session, position_item_id=position.id)
        assert member1.routing_rule_id == rule_x.id
        assert member1.context_id == context_x.id
        # `member1` и `member2` ниже — ОДИН И ТОТ ЖЕ объект ORM (identity map
        # по PK): значения обязаны сняться СЕЙЧАС, в простые переменные, иначе
        # сравнение после второй маршрутизации сравнило бы объект сам с собой.
        member1_routing_rule_id = member1.routing_rule_id

        context_y = _context(db_session, bucket)
        rule_y = _rule(
            db_session,
            bucket,
            ordinal=0,  # раньше rule_x — выигрывает первым
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Альфа"},
            context=context_y,
            user=user,
        )
        member2 = route_position(db_session, position_item_id=position.id)
        assert member2.routing_rule_id == rule_y.id
        assert member2.context_id == context_y.id
        assert member2.routing_rule_id != member1_routing_rule_id

    def test_routing_rule_id_changes_while_the_context_stays_the_same(
        self, db_session, factories
    ):
        """Второе правило указывает на ТОТ ЖЕ контекст, что и первое, но
        стоит раньше по `ordinal` — меняется только `routing_rule_id`,
        `context_id` остаётся прежним (в отличие от предыдущего теста, где
        менялись оба)."""
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция Бета")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member0 = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member0.bucket_id)

        shared_context = _context(db_session, bucket)
        rule_1 = _rule(
            db_session,
            bucket,
            ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Бета"},
            context=shared_context,
            user=user,
        )
        member1 = route_position(db_session, position_item_id=position.id)
        assert member1.routing_rule_id == rule_1.id
        assert member1.context_id == shared_context.id
        member1_routing_rule_id = member1.routing_rule_id
        member1_context_id = member1.context_id

        rule_0 = _rule(
            db_session,
            bucket,
            ordinal=0,  # раньше rule_1 — выигрывает первым
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция Бета"},
            context=shared_context,  # ТОТ ЖЕ контекст
            user=user,
        )
        member2 = route_position(db_session, position_item_id=position.id)
        assert member2.routing_rule_id == rule_0.id
        assert member2.routing_rule_id != member1_routing_rule_id
        assert member2.context_id == member1_context_id
        assert member2.context_id == shared_context.id

    def test_membership_state_becomes_current_after_reroute_matching_its_bucket(
        self, db_session, factories
    ):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        # Симулируем «устаревшее» состояние (операция, которая его ставит —
        # задача 6, вне границ этой задачи): прямая правка строки.
        member.membership_state = MembershipState.STALE.value
        db_session.flush()

        rerouted = route_position(db_session, position_item_id=position.id)
        assert rerouted.membership_state == MembershipState.CURRENT.value


# ---------------------------------------------------------------------------
#  Порядок правил — по ordinal, перестановка меняет результат (спека §2.4)
# ---------------------------------------------------------------------------

class TestRuleOrderIsByOrdinal:
    def test_first_true_rule_wins_and_swapping_ordinals_changes_the_result(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        proposal, _ = _proposal(factories)
        outer = _chapter(factories, proposal, title="Секция 1", chapter_number="1")
        inner = _chapter(
            factories, proposal, title="Стены", chapter_number="1.1", parent=outer
        )
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=inner, catalog_position=cp)

        # Сначала — БЕЗ правил, чтобы получить корзину и контекст по умолчанию.
        member0 = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member0.bucket_id)

        context_a = _context(db_session, bucket)
        context_b = _context(db_session, bucket)

        # Обе истинны на цепочке ["Стены", "Секция 1"]: правило A ищет
        # "Секция 1" где-то в цепочке, правило B — "Стены".
        rule_a = _rule(
            db_session,
            bucket,
            ordinal=1,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Секция 1"},
            context=context_a,
            user=user,
        )
        rule_b = _rule(
            db_session,
            bucket,
            ordinal=2,
            predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "Стены"},
            context=context_b,
            user=user,
        )

        member1 = route_position(db_session, position_item_id=position.id)
        assert member1.context_id == context_a.id
        assert member1.routing_rule_id == rule_a.id
        assert member1.routed_by == RoutedBy.rule.value

        # Перестановка ordinal — через временное значение, иначе
        # `uq_context_routing_rules_bucket_ordinal` отвергнет промежуточный
        # дубль (1,1) внутри одного flush.
        rule_a.ordinal = 999
        db_session.flush()
        rule_b.ordinal = 1
        db_session.flush()
        rule_a.ordinal = 2
        db_session.flush()

        member2 = route_position(db_session, position_item_id=position.id)
        assert member2.context_id == context_b.id
        assert member2.routing_rule_id == rule_b.id
        assert member2.routed_by == RoutedBy.rule.value


# ---------------------------------------------------------------------------
#  «Прочее» — полноценная статья, отдельной ветки нет (спека §1.10, §2.2)
# ---------------------------------------------------------------------------

class TestNoSpecialBranchForProchee:
    def test_module_source_never_mentions_prochee_or_its_code(self):
        """Структурная проверка: ни основы «проч» (в любом регистре —
        «прочее», «прочих», «Прочее»…), ни буквальных кодов статьи «Прочее»
        (`99`, `.99`) в исходнике модуля нет.

        Что это ДОКАЗЫВАЕТ: код маршрутизации не содержит текстового или
        числового литерала, завязанного на конкретную статью «Прочее» — ни
        явной ветки по имени, ни магического числа-кода.

        Чего это НЕ доказывает: тест читает ИСХОДНЫЙ ТЕКСТ, а не поведение —
        он не ловит косвенное кодирование того же условия (например,
        сравнение с `WorkCategory.code`, полученным из внешней конфигурации,
        переменной окружения или конкатенации строк `"9" + "9"`, которая не
        содержит подстроки «99» целиком). Уверенность в отсутствии ветки
        даёт СОЧЕТАНИЕ этой проверки с позитивным входом ниже (тот же путь,
        что у любой другой статьи), а не она одна.
        """
        source = Path(context_routing_module.__file__).read_text(encoding="utf-8")
        lowered = source.lower()
        assert "прочее" not in lowered
        assert "проч" not in lowered
        assert "99" not in source
        assert ".99" not in source

    def test_position_under_the_real_prochee_article_gets_a_bucket_the_same_way(
        self, db_session, factories
    ):
        """Позитивный вход — РЕАЛЬНАЯ статья классификатора «Прочее» (код
        `99`, найдена по коду, а не по id), а не произвольный лист с
        совпадающим текстом заголовка."""
        category_id = _category_id_by_code(db_session, "99")
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Прочее", category_id=category_id)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        assert bucket.work_category_id == category_id
        assert member.routed_by == RoutedBy.default.value

    def test_position_under_a_leaf_prochee_article_gets_a_bucket_the_same_way(
        self, db_session, factories
    ):
        """Тот же позитивный вход, но под ВЛОЖЕННЫМ («дочерним») листом
        `*.99` (`1.99`, «Прочее (подготовительные работы, содержание
        площадки)») — корневая статья `99` не единственная форма «Прочее» в
        классификаторе (`IS_BUCKET_EXPRESSION = "code = '99' OR code LIKE
        '%.99'"`, миграция 0005)."""
        category_id = _category_id_by_code(db_session, "1.99")
        proposal, _ = _proposal(factories)
        chapter = _chapter(
            factories, proposal, title="Прочее по разделу 1", category_id=category_id
        )
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)

        member = route_position(db_session, position_item_id=position.id)
        bucket = db_session.get(ContextBucket, member.bucket_id)
        assert bucket.work_category_id == category_id
        assert member.routed_by == RoutedBy.default.value


# ---------------------------------------------------------------------------
#  Предикаты правил: все три вида, отказы, сравнение через нормализацию
# ---------------------------------------------------------------------------

class TestEvaluatePredicateAllKinds:
    def test_nearest_chapter_equals_true_and_false(self, db_session, factories):
        proposal, _ = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Секция 1")
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        chapters = chapter_context(db_session, position.id)

        assert (
            evaluate_predicate(
                {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Секция 1"}, chapters
            )
            is True
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Секция 2"}, chapters
            )
            is False
        )

    def test_nearest_chapter_equals_compares_against_the_nearest_not_the_root(self):
        """Двухуровневая цепочка, где совпадает ТОЛЬКО ближайший раздел:
        сравнение с корнем обязано покраснеть эту проверку."""
        chapters = ChapterContext(
            chain=("Стены", "Секция 1"), nearest="Стены", category_id=None, category_source=None
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Стены"}, chapters
            )
            is True
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Секция 1"}, chapters
            )
            is False
        )

    def test_nearest_chapter_equals_is_false_without_a_chapter(self):
        empty = ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Секция 1"}, empty
            )
            is False
        )

    def test_chapter_level_equals_true_false_and_out_of_range(self):
        chapters = ChapterContext(
            chain=("Стены", "Секция 1"), nearest="Стены", category_id=None, category_source=None
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_CHAPTER_LEVEL_EQUALS, "level": 0, "value": "Стены"}, chapters
            )
            is True
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_CHAPTER_LEVEL_EQUALS, "level": 1, "value": "Секция 1"},
                chapters,
            )
            is True
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_CHAPTER_LEVEL_EQUALS, "level": 1, "value": "Стены"}, chapters
            )
            is False
        )
        assert (
            evaluate_predicate(
                {"kind": PREDICATE_CHAPTER_LEVEL_EQUALS, "level": 5, "value": "Стены"}, chapters
            )
            is False
        )

    def test_matches_only_through_normalization(self):
        """Пара из `test_sanitize_text.py`: разные словоформы, равные после
        лемматизации. Отключение нормализации (сравнение сырого текста)
        обязано покрасить этот тест."""
        chapters = ChapterContext(
            chain=("Устройство монолитных стен",),
            nearest="Устройство монолитных стен",
            category_id=None,
            category_source=None,
        )
        assert (
            evaluate_predicate(
                {
                    "kind": PREDICATE_NEAREST_CHAPTER_EQUALS,
                    "value": "устройства монолитных стен",
                },
                chapters,
            )
            is True
        )

    def test_unknown_predicate_kind_is_refused(self):
        empty = ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)
        with pytest.raises(RoutingError, match="неизвестный вид"):
            evaluate_predicate({"kind": "какой-то_другой", "value": "x"}, empty)

    def test_predicate_without_value_is_refused(self):
        empty = ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)
        with pytest.raises(RoutingError, match="value"):
            evaluate_predicate({"kind": PREDICATE_NEAREST_CHAPTER_EQUALS}, empty)

    def test_chapter_level_equals_without_level_is_refused(self):
        empty = ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)
        with pytest.raises(RoutingError, match="level"):
            evaluate_predicate(
                {"kind": PREDICATE_CHAPTER_LEVEL_EQUALS, "value": "x"}, empty
            )


# ---------------------------------------------------------------------------
#  Партия больше пяти — psycopg3 prepare_threshold (docs/insights/…)
# ---------------------------------------------------------------------------

class TestBatchLargerThanThePrepareThreshold:
    def test_route_positions_handles_twelve_new_buckets_in_one_call(
        self, db_session, factories
    ):
        proposal, estimate = _proposal(factories)
        positions = []
        for i in range(12):
            cp = factories.CatalogPositionFactory.create()
            positions.append(
                _position(factories, proposal, catalog_position=cp, title=f"Работа {i}")
            )

        outcome = route_positions(db_session, estimate_ids=[estimate.id])

        assert outcome.buckets_created == 12
        assert outcome.contexts_created == 12
        assert outcome.members_created == 12
        assert outcome.members_skipped_chapters == 0
        assert outcome.members_skipped_unmatched == 0

        member_count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.position_item_id.in_([p.id for p in positions]))
        ).scalar_one()
        assert member_count == 12


# ---------------------------------------------------------------------------
#  `lock_buckets`: режим компилируется в правильный SQL (спека §2.8, db.md)
# ---------------------------------------------------------------------------

class _CapturingSession:
    """Подставной db, который ЗАПОМИНАЕТ переданный `Select`, не исполняя
    его — statement можно скомпилировать и проверить РЕЖИМ лока (`FOR SHARE`
    против `FOR UPDATE`), а не намерение вызова.

    `existing_ids` — id, которые «нашлись бы» реальным запросом: `lock_buckets`
    сверяет число заблокированных строк с запрошенным списком и отказывает
    при недостаче, поэтому подставная сессия обязана возвращать строки,
    иначе тесты этого класса упали бы на самой проверке количества, а не на
    компиляции SQL, которую они предъявляют.
    """

    def __init__(self, existing_ids: frozenset[int] = frozenset()):
        self.statements: list = []
        self._existing_ids = existing_ids

    def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        existing_ids = self._existing_ids

        class _Row:
            def __init__(self, row_id: int):
                self.id = row_id

        class _Result:
            def all(self_inner):
                return [_Row(i) for i in existing_ids]

        return _Result()


class TestLockBucketsCompilesToTheRightMode:
    def test_non_exclusive_compiles_to_for_share_not_for_update(self):
        fake = _CapturingSession(existing_ids=frozenset({3, 9, 5}))
        lock_buckets(fake, [3, 9, 5], exclusive=False)

        sql = str(fake.statements[0].compile(dialect=postgresql.dialect()))
        assert "FOR SHARE" in sql
        assert "FOR UPDATE" not in sql
        assert "KEY SHARE" not in sql
        # Подстрока "ORDER BY context_buckets.id" совпала бы и с
        # "ORDER BY context_buckets.id DESC" — якорим тем, что СРАЗУ после
        # `id` (не считая пробелов) идёт `FOR`, то есть модификатора
        # направления там нет вовсе (ASC у SQLAlchemy — умолчание, не
        # рендерится отдельным словом).
        assert re.search(r"ORDER BY context_buckets\.id\s+FOR", sql)
        assert "DESC" not in sql

    def test_exclusive_compiles_to_for_update(self):
        fake = _CapturingSession(existing_ids=frozenset({3, 9, 5}))
        lock_buckets(fake, [3, 9, 5], exclusive=True)

        sql = str(fake.statements[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in sql
        assert "NO KEY UPDATE" not in sql
        assert "FOR SHARE" not in sql
        assert re.search(r"ORDER BY context_buckets\.id\s+FOR", sql)
        assert "DESC" not in sql

    def test_empty_bucket_ids_is_a_no_op(self):
        fake = _CapturingSession()
        lock_buckets(fake, [], exclusive=False)
        assert fake.statements == []


class TestLockBucketsMissingBucketRaises:
    def test_lock_buckets_raises_naming_only_the_missing_bucket_ids(self, db_session, factories):
        """`lock_buckets` не притворяется, что заблокировала корзину,
        которой нет — отказывает и называет ИМЕННО отсутствующие id.
        Проверено с обеих сторон: сообщение обязано назвать пропавший id и
        НЕ обязано (не должно) упоминать реально заблокированный — иначе
        отказ выглядел бы так же, даже если бы функция перепутала местами
        «нашла» и «не нашла»."""
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)
        member = route_position(db_session, position_item_id=position.id)
        real_bucket_id = member.bucket_id
        missing_id = real_bucket_id + 999_999_999

        with pytest.raises(RoutingError) as excinfo:
            lock_buckets(db_session, [real_bucket_id, missing_id], exclusive=False)

        # Id сравниваются множеством из списка в сообщении, а не подстрокой:
        # при real_bucket_id = 1 подстрока «1» есть и внутри missing_id.
        message = str(excinfo.value)
        listed = re.search(r"\[([\d,\s]+)\]", message)
        assert listed is not None, message
        named_ids = {int(part) for part in listed.group(1).split(",")}
        assert named_ids == {missing_id}


# ---------------------------------------------------------------------------
#  Контракт `FOR SHARE`: взят, режим, совместим сам с собой (спека §2.8)
# ---------------------------------------------------------------------------

class TestForShareLockContract:
    def test_existing_for_update_delays_routing_until_released(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        # Прогрев: корзина+контекст по умолчанию уже существуют — иначе она
        # новая, и лока ей не нужно (спека §2.8: «вновь созданную корзину
        # блокировать не нужно»).
        route_position(committing_db, position_item_id=position.id)
        committing_db.commit()

        member_before = committing_db.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == position.id)
        ).scalar_one()
        bucket_id = member_before.bucket_id

        result: dict[str, object] = {}
        pid_holder: dict[str, int] = {}
        second_started = threading.Event()

        def routing_session():
            with committing_session_factory() as db2:
                pid_holder["pid"] = db2.execute(
                    sa.text("SELECT pg_backend_pid()")
                ).scalar_one()
                second_started.set()
                member = route_position(db2, position_item_id=position.id)
                db2.commit()
                result["routed_by"] = member.routed_by

        db1 = committing_session_factory()
        thread = threading.Thread(target=routing_session, daemon=True)
        try:
            db1.execute(
                sa.select(ContextBucket.id)
                .where(ContextBucket.id == bucket_id)
                .with_for_update()
            ).all()

            thread.start()
            assert second_started.wait(timeout=10)

            # Витнесс через ТЕКСТ ожидающего запроса: обязан быть `SELECT
            # ... FOR SHARE` (запрос `lock_buckets`), а НЕ `INSERT` (запрос
            # `get_or_create_bucket` для новой корзины тоже содержит
            # "context_buckets" — подстрока этого не различала).
            assert _wait_until_backend_blocks(
                committing_session_factory,
                pid=pid_holder["pid"],
                contains="FOR SHARE",
                timeout=10,
            ), "маршрутизация не встала на ожидание лока корзины"
        finally:
            # Снимаем FOR UPDATE и дожидаемся потока ВСЕГДА, даже если ассерт
            # выше упал: иначе поток остаётся держателем лока, teardown
            # (TRUNCATE следующего теста) становится его жертвой, и падение
            # одного ассерта превращается в ложный ВТОРОЙ отказ на
            # совершенно другом тесте.
            try:
                db1.commit()  # снимает FOR UPDATE
            except Exception:
                db1.rollback()
            db1.close()
            thread.join(timeout=20)

        assert not thread.is_alive()
        assert result.get("routed_by") == RoutedBy.default.value

    def test_for_share_is_compatible_with_itself(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()
        route_position(committing_db, position_item_id=position.id)
        committing_db.commit()

        member = committing_db.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == position.id)
        ).scalar_one()
        bucket_id = member.bucket_id

        acquired_a = threading.Event()
        acquired_b = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []

        def worker(acquired_event: threading.Event):
            try:
                with committing_session_factory() as db:
                    lock_buckets(db, [bucket_id], exclusive=False)
                    acquired_event.set()
                    assert release.wait(timeout=10), "release не пришёл вовремя"
                    db.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        ta = threading.Thread(target=worker, args=(acquired_a,), daemon=True)
        tb = threading.Thread(target=worker, args=(acquired_b,), daemon=True)
        ta.start()
        tb.start()

        try:
            # ОБА обязаны взять FOR SHARE, не дожидаясь друг друга: если бы
            # один ждал другого, его сигнал не пришёл бы за таймаут, пока
            # держит release.
            assert acquired_a.wait(timeout=5), "первый FOR SHARE не взят вовремя"
            assert acquired_b.wait(timeout=5), "второй FOR SHARE ждёт первый — не должен"
        finally:
            # Отпускаем ОБА потока и дожидаемся их ВСЕГДА, даже если ассерт
            # выше упал — иначе держатели FOR SHARE переживают тест и
            # сталкиваются с TRUNCATE в teardown следующего теста.
            release.set()
            ta.join(timeout=10)
            tb.join(timeout=10)
        assert not errors, errors

    def test_route_position_itself_is_compatible_with_itself(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        """Предыдущий тест самосовместимости брал `lock_buckets` НАПРЯМУЮ,
        поэтому `exclusive=True` внутри самого `route_position` оставался бы
        незамеченным. Здесь тот же факт доказывается через сам
        `route_position` — двумя РАЗНЫМИ позициями одной и той же корзины,
        с искусственной паузой ПОСЛЕ захвата настоящего лока (внутри
        `_apply_routing`, монки-патчено только для превращения окна в
        детерминированное — сам вызов `lock_buckets` не подменяется)."""
        proposal_a, _ = _proposal(committing_factories)
        proposal_b, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position_a = _position(committing_factories, proposal_a, catalog_position=cp)
        position_b = _position(committing_factories, proposal_b, catalog_position=cp)
        committing_db.commit()

        route_position(committing_db, position_item_id=position_a.id)
        committing_db.commit()

        original_apply_routing = context_routing_module._apply_routing
        a_locked = threading.Event()
        release_a = threading.Event()

        def patched_apply_routing(db, *, position, bucket, chapters):
            if position.id == position_a.id:
                a_locked.set()
                assert release_a.wait(timeout=15), "release не пришёл вовремя"
            return original_apply_routing(db, position=position, bucket=bucket, chapters=chapters)

        monkeypatch.setattr(context_routing_module, "_apply_routing", patched_apply_routing)

        errors: list[Exception] = []
        b_done = threading.Event()

        def thread_a():
            try:
                with committing_session_factory() as db:
                    route_position(db, position_item_id=position_a.id)
                    db.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        def thread_b():
            try:
                assert a_locked.wait(timeout=10), "A не встал в критическую секцию"
                with committing_session_factory() as db:
                    route_position(db, position_item_id=position_b.id)
                    db.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)
            finally:
                b_done.set()

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        ta.start()
        tb_started = False
        try:
            assert a_locked.wait(timeout=10), "A не встал на удержание лока"
            tb.start()
            tb_started = True

            # МЕХАНИЗМ: B обязан завершиться, пока A ВСЁ ЕЩЁ держит FOR SHARE
            # незакоммиченной транзакцией. Будь внутри route_position
            # `exclusive=True`, B ждал бы A, и это ожидание не завершилось бы
            # за отведённый таймаут.
            assert b_done.wait(timeout=10), (
                "B не завершился, пока A держит лок — route_position взял FOR UPDATE?"
            )
        finally:
            # A стоит внутри монки-патча и ждёт release_a бесконечно (точнее,
            # до собственного внутреннего таймаута в 15 с) — отпускаем его и
            # дожидаемся ОБОИХ потоков ВСЕГДА, иначе упавший ассерт оставляет
            # держателя лока жить дальше теста, и следующий TRUNCATE в
            # teardown становится его жертвой.
            release_a.set()
            ta.join(timeout=20)
            if tb_started:
                tb.join(timeout=20)
        assert not errors, errors


# ---------------------------------------------------------------------------
#  Новый контекст читает `kind` каталожной строки под замком — гонка
#  «создание контекста импортом против `set_kind(HEADER|TRASH)`». Приём
#  БЕЗ потоков — тот же, что `test_work_families.py::
#  TestFamilyRereadAfterLock` (задача 8): другая, ПОЛНОСТЬЮ закоммиченная
#  сессия меняет `kind` (атрибут ORM-объекта, кэшируемый identity map)
#  МЕЖДУ первым чтением ЭТОЙ сессии и локом создания контекста.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает каждый SQL-текст, реально отправленный на этом
    соединении — тот же приём, что `test_work_families.py::_capturing_sql`
    (локальная копия, докстрока модуля: наборы помощников друг у друга не
    импортируют)."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


class TestNewContextRereadsCatalogKindAfterLock:
    def test_new_default_context_locks_catalog_position_for_share(self, db_session, factories):
        proposal, _ = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        position = _position(factories, proposal, catalog_position=cp)

        with _capturing_sql(db_session) as statements:
            route_position(db_session, position_item_id=position.id)

        lock_statement = next(
            (
                s for s in statements
                if "catalog_positions" in s and " FOR SHARE" in s and "FOR UPDATE" not in s
            ),
            None,
        )
        assert lock_statement is not None, (
            "нет FOR SHARE на catalog_positions среди:\n" + "\n---\n".join(statements)
        )

    def test_new_context_is_not_applicable_when_kind_becomes_header_before_lock(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        primed = committing_db.get(CatalogPosition, cp.id)
        assert primed.kind == CatalogKind.POSITION.value

        with committing_session_factory() as other:
            other_cp = other.get(CatalogPosition, cp.id)
            other_cp.kind = CatalogKind.HEADER.value
            other.commit()

        # Бакет для этой каталожной строки ещё не существует — маршрутизация
        # заводит НОВЫЙ контекст по умолчанию (путь, который и обязан
        # перечитать `kind`).
        member = route_position(committing_db, position_item_id=position.id)
        context = committing_db.get(CatalogContext, member.context_id)
        assert context.semantic_state == SemanticState.NOT_APPLICABLE.value


class TestRoutePositionsForShareLockContract:
    def test_existing_for_update_delays_route_positions_until_released(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, estimate = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        # Прогрев: корзина+контекст по умолчанию уже существуют.
        route_positions(committing_db, estimate_ids=[estimate.id])
        committing_db.commit()

        member_before = committing_db.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == position.id)
        ).scalar_one()
        bucket_id = member_before.bucket_id

        result: dict[str, object] = {}
        pid_holder: dict[str, int] = {}
        second_started = threading.Event()

        def routing_session():
            with committing_session_factory() as db2:
                pid_holder["pid"] = db2.execute(
                    sa.text("SELECT pg_backend_pid()")
                ).scalar_one()
                second_started.set()
                outcome = route_positions(db2, estimate_ids=[estimate.id])
                db2.commit()
                result["outcome"] = outcome

        db1 = committing_session_factory()
        thread = threading.Thread(target=routing_session, daemon=True)
        try:
            db1.execute(
                sa.select(ContextBucket.id)
                .where(ContextBucket.id == bucket_id)
                .with_for_update()
            ).all()

            thread.start()
            assert second_started.wait(timeout=10)

            # Витнесс через ТЕКСТ ожидающего запроса: он обязан быть
            # `SELECT ... FOR SHARE` (запрос `lock_buckets`), а НЕ `INSERT`
            # (запрос `get_or_create_bucket` для новой корзины) — корзина
            # здесь уже существует, второй импорт блокируется именно на
            # локе, а не на вставке.
            assert _wait_until_backend_blocks(
                committing_session_factory,
                pid=pid_holder["pid"],
                contains="FOR SHARE",
                timeout=10,
            ), "route_positions не встал на ожидание лока корзины"
        finally:
            # Снимаем FOR UPDATE и дожидаемся потока ВСЕГДА — та же причина,
            # что у аналогичного теста `route_position` выше: незакрытый
            # держатель лока становится жертвой TRUNCATE в teardown.
            try:
                db1.commit()  # снимает FOR UPDATE
            except Exception:
                db1.rollback()
            db1.close()
            thread.join(timeout=20)

        assert not thread.is_alive()
        assert result["outcome"].buckets_created == 0
        assert result["outcome"].members_created == 0  # членство уже существовало

    def test_route_positions_itself_is_compatible_with_itself(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        """Та же техника, что у аналогичного теста `route_position`: пауза
        внутри `_apply_routing` ПОСЛЕ настоящего `lock_buckets`, чтобы окно
        совместимости было детерминированным, а не гонкой на удачу."""
        proposal_a, estimate_a = _proposal(committing_factories)
        proposal_b, estimate_b = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position_a = _position(committing_factories, proposal_a, catalog_position=cp)
        _position_b = _position(committing_factories, proposal_b, catalog_position=cp)
        committing_db.commit()

        route_positions(committing_db, estimate_ids=[estimate_a.id])
        committing_db.commit()

        original_apply_routing = context_routing_module._apply_routing
        a_locked = threading.Event()
        release_a = threading.Event()

        def patched_apply_routing(db, *, position, bucket, chapters):
            if position.id == position_a.id:
                a_locked.set()
                assert release_a.wait(timeout=15), "release не пришёл вовремя"
            return original_apply_routing(db, position=position, bucket=bucket, chapters=chapters)

        monkeypatch.setattr(context_routing_module, "_apply_routing", patched_apply_routing)

        errors: list[Exception] = []
        b_done = threading.Event()

        def thread_a():
            try:
                with committing_session_factory() as db:
                    route_positions(db, estimate_ids=[estimate_a.id])
                    db.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        def thread_b():
            try:
                assert a_locked.wait(timeout=10), "A не встал в критическую секцию"
                with committing_session_factory() as db:
                    route_positions(db, estimate_ids=[estimate_b.id])
                    db.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)
            finally:
                b_done.set()

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        ta.start()
        tb_started = False
        try:
            assert a_locked.wait(timeout=10), "A не встал на удержание лока"
            tb.start()
            tb_started = True

            assert b_done.wait(timeout=10), (
                "B не завершился, пока A держит лок — route_positions взял FOR UPDATE?"
            )
        finally:
            release_a.set()
            ta.join(timeout=20)
            if tb_started:
                tb.join(timeout=20)
        assert not errors, errors


# ---------------------------------------------------------------------------
#  Корзина идемпотентна под конкуренцией (спека §2.1, §2.4)
# ---------------------------------------------------------------------------

class TestBucketIdempotentUnderConcurrency:
    def test_two_concurrent_imports_of_the_same_pair_yield_one_bucket_and_one_context(
        self, committing_db, committing_factories, committing_session_factory
    ):
        cp = committing_factories.CatalogPositionFactory.create()
        proposal_a, _ = _proposal(committing_factories)
        proposal_b, _ = _proposal(committing_factories)
        position_a = _position(committing_factories, proposal_a, catalog_position=cp)
        position_b = _position(committing_factories, proposal_b, catalog_position=cp)
        committing_db.commit()

        a_ready = threading.Event()
        a_release = threading.Event()
        b_pid_ready = threading.Event()
        a_result: dict[str, int] = {}
        b_result: dict[str, int] = {}
        b_pid: dict[str, int] = {}

        def thread_a():
            with committing_session_factory() as db:
                member = route_position(db, position_item_id=position_a.id)
                a_result["bucket_id"] = member.bucket_id
                a_ready.set()
                assert a_release.wait(timeout=15), "release не пришёл вовремя"
                db.commit()

        def thread_b():
            assert a_ready.wait(timeout=10)
            with committing_session_factory() as db:
                b_pid["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                b_pid_ready.set()
                member = route_position(db, position_item_id=position_b.id)
                b_result["bucket_id"] = member.bucket_id
                db.commit()

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        ta.start()
        tb_started = False
        try:
            assert a_ready.wait(timeout=10)
            tb.start()
            tb_started = True
            assert b_pid_ready.wait(timeout=10)

            # МЕХАНИЗМ: вторая вставка натыкается на НЕЗАКОММИЧЕННУЮ строку
            # той же пары и обязана встать в очередь на разрешение
            # конфликта, а не создать вторую корзину и не отказать.
            assert _wait_until_backend_blocks(
                committing_session_factory,
                pid=b_pid["pid"],
                contains="context_buckets",
                timeout=10,
            ), "второй импорт не встал на ожидание конфликтующей вставки корзины"
        finally:
            # A держит незакоммиченную строку конфликта — отпускаем и
            # дожидаемся ОБОИХ потоков ВСЕГДА: незакрытый держатель конфликта
            # переживает тест и сталкивается с TRUNCATE в teardown
            # следующего теста.
            a_release.set()
            ta.join(timeout=20)
            if tb_started:
                tb.join(timeout=20)
        assert not ta.is_alive() and not tb.is_alive()

        assert a_result["bucket_id"] == b_result["bucket_id"]

        contexts_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(CatalogContext)
            .where(CatalogContext.bucket_id == a_result["bucket_id"])
        ).scalar_one()
        assert contexts_count == 1

        buckets_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(ContextBucket)
            .where(ContextBucket.catalog_position_id == cp.id)
        ).scalar_one()
        assert buckets_count == 1

"""Каскадные события: ручной разнос, `STALE`, предложение переноса (спека
`2026-09-22-catalog-families-design.md` §1.7, §1.15, §2.5, §2.8, §2.14; план,
задача 10).

Помощники — локальная копия наборов `test_review_contexts.py` /
`test_context_membership.py` / `test_import_pipeline.py` (докстрока тех
файлов: наборы помощников тестов друг у друга не импортируют).
"""
from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import aliased

import crud.contracts as crud_contracts
import crud.tenders as crud_tenders
from models import (
    CatalogContext,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    Estimate,
    ImportJobStatus,
    Lot,
    MembershipState,
    PositionItem,
    Proposal,
    SemanticEvent,
    UserRole,
    WorkCategory,
)
from parser import ParseResult
from services import import_pipeline
from services.category_override import apply_overrides, clear_override, set_override
from services.category_resolution import CategoryResolver
from services.context_operations import (
    REFUSE_CATEGORY_CHANGED,
    ContextOperationError,
    RefreshReport,
    accept_transfer,
    refresh_membership_states,
    split_context,
    stale_mismatch_report,
    transfer_proposal,
)
from services.context_routing import (
    PREDICATE_NEAREST_CHAPTER_EQUALS,
    effective_category_id,
    route_positions,
)
from services.estimate_import import import_estimate
from services.import_owners import contract_estimate_owner
from services.matching import match_positions
from services.round_category_override import set_round_override
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MANUAL_OVERRIDES_18_PATH = DATA_DIR / "manual_overrides_18.json"


def _load_manual_overrides_18() -> list[dict]:
    with open(MANUAL_OVERRIDES_18_PATH, encoding="utf-8") as f:
        return json.load(f)


MANUAL_OVERRIDES_18 = _load_manual_overrides_18()


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_routed_estimate(db, factories, positions, **kwargs):
    """Смета через НАСТОЯЩИЙ импорт + матчинг + маршрутизацию — три шага
    `run_import_job` (§5, §2.9) без обвязки job/статусов: каскадные функции
    задачи 10 (`apply_overrides`, `refresh_membership_states`) читают
    `estimate_raw_data`, а `route_positions` требует `catalog_position_id`,
    так что ни `make_imported_estimate` (без матчинга и маршрутизации), ни
    сырые фабрики (без `raw_data`) в одиночку не годятся."""
    contract = factories.ContractFactory.create()
    db.flush()
    data = payload_for(contract, positions, **kwargs)
    outcome = import_estimate(
        db,
        owner=contract_estimate_owner(contract, None),
        data=data,
        parser_version="1.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=UnitResolver(db),
        category_resolver=CategoryResolver.from_db(db),
    )
    match_positions(db, outcome.positions_to_match)
    db.flush()
    route_positions(db, estimate_ids=[outcome.estimate_id])
    return db.get(Estimate, outcome.estimate_id)


def _member(db, position_item_id) -> ContextMember:
    return db.get(ContextMember, position_item_id)


def _bucket_of(db, member) -> ContextBucket:
    return db.get(ContextBucket, member.bucket_id)


def _chapter_row(db, estimate, chapter_number):
    return db.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate.id,
            PositionItem.is_chapter.is_(True),
            PositionItem.chapter_number_in_proposal == chapter_number,
        )
    ).scalar_one()


def _work_row(db, estimate, title):
    return db.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate.id,
            PositionItem.is_chapter.is_(False),
            PositionItem.job_title_in_proposal == title,
        )
    ).scalars().first()


def _work_rows(db, estimate, title):
    return db.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate.id,
            PositionItem.is_chapter.is_(False),
            PositionItem.job_title_in_proposal == title,
        )
        .order_by(PositionItem.id)
    ).scalars().all()


def _leaf_category_ids(db, n=2) -> list[int]:
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return (
        db.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(n)
        )
        .scalars()
        .all()
    )


def _category_by_code(db, code: str) -> int:
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def _events(db, context_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.context_id == context_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return db.execute(stmt).scalars().all()


@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает КАЖДЫЙ SQL-текст, реально отправленный на этом
    соединении (локальная копия помощника `test_context_operations.py`,
    задача 6, раунд 3 — режим лока проверяется компиляцией РЕАЛЬНОГО
    запроса, а не намерением вызова)."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _find_lock_statement(statements: list[str], table_name: str) -> tuple[int, str]:
    """Находит запрос, блокирующий `table_name`, по СОЧЕТАНИЮ признаков —
    таблица в тексте И модификатор `FOR …`, иначе обычный `SELECT`/`db.get()`
    той же таблицы дал бы ложное совпадение (локальная копия помощника
    `test_context_operations.py`)."""
    for index, statement in enumerate(statements):
        if table_name in statement and (
            " FOR UPDATE" in statement or " FOR SHARE" in statement
        ):
            return index, statement
    raise AssertionError(
        f"ни один из {len(statements)} перехваченных запросов не содержит "
        f"лок {table_name}:\n" + "\n---\n".join(statements)
    )


def _two_chapter_scene(db, factories):
    """Одна смета, два раздела верхнего уровня БЕЗ статьи ("1" и "2"), у
    каждого — своя работа ОДНОГО И ТОГО ЖЕ написания (общий `job_title`+
    `unit`) — matching сводит их к ОДНОЙ каталожной строке, и обе, ещё без
    статьи, маршрутизируются в ОДНУ корзину и ОДИН (умолчательный) контекст.
    Общий контекст необходим сцене: «незатронутое членство ТОГО ЖЕ
    контекста» с разными написаниями не проверяется — member1/member2
    лежали бы в разных корзинах, и ось-на-контексте (STALE всему контексту
    вместо только затронутого членства) осталась бы незамеченной.
    Возвращает объект с атрибутами `estimate`, `chapter1`, `chapter2`,
    `work1`, `work2`, `member1`, `member2`."""
    shared_title = f"Общая работа {_uid()}"
    estimate = _make_routed_estimate(
        db,
        factories,
        [
            position(job_title="Раздел А", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
            position(job_title="Раздел Б", is_chapter=True, chapter_number="2", number="3"),
            position(
                job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
            ),
        ],
    )

    class Scene:
        pass

    scene = Scene()
    scene.estimate = estimate
    scene.chapter1 = _chapter_row(db, estimate, "1")
    scene.chapter2 = _chapter_row(db, estimate, "2")
    scene.work1 = db.execute(
        sa.select(PositionItem).where(PositionItem.chapter_item_id == scene.chapter1.id)
    ).scalar_one()
    scene.work2 = db.execute(
        sa.select(PositionItem).where(PositionItem.chapter_item_id == scene.chapter2.id)
    ).scalar_one()
    scene.member1 = _member(db, scene.work1.id)
    scene.member2 = _member(db, scene.work2.id)
    return scene


def _four_chapter_scene(db, factories):
    """Одна смета, четыре раздела верхнего уровня ("1".."4") с ОБЩИМ
    написанием работы — одна корзина, один умолчательный контекст, четыре
    членства, все `CURRENT` (ни одна статья не задана). Возвращает
    `(estimate, [chapter1..4], [work1..4])`."""
    shared_title = f"Общая работа {_uid()}"
    positions = []
    for i in range(1, 5):
        positions.append(position(job_title=f"Раздел {i}", is_chapter=True, chapter_number=str(i), number=str(2 * i - 1)))
        positions.append(
            position(
                job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref=str(i), number=str(2 * i),
            )
        )
    estimate = _make_routed_estimate(db, factories, positions)
    chapters = [_chapter_row(db, estimate, str(i)) for i in range(1, 5)]
    works = [
        db.execute(sa.select(PositionItem).where(PositionItem.chapter_item_id == c.id)).scalar_one()
        for c in chapters
    ]
    return estimate, chapters, works


def _raw_set_chapter_category(db, chapter: PositionItem, category_id: int | None) -> None:
    """Пишет статью раздела НАПРЯМУЮ, В ОБХОД `apply_overrides`/`CategoryResolver`
    (только для тестов задачи 10, проверяющих `refresh_membership_states`
    САМ ПО СЕБЕ, а не встроенный в него вызов): продакшен-путь всегда
    материализует статью ЧЕРЕЗ резолвер, эта функция симулирует ТОЛЬКО
    результат — расхождение хранимого membership_state с только что
    изменившейся статьёй раздела, БЕЗ немедленного автоматического
    приведения, чтобы `refresh_membership_states` было что приводить по
    команде теста, а не пайплайна."""
    chapter.work_category_id = category_id
    chapter.category_source = "manual" if category_id is not None else None
    db.flush()
    db.expire_all()


# ---------------------------------------------------------------------------
#  `refresh_membership_states`: разнос помечает STALE только затронутые
# ---------------------------------------------------------------------------

class TestCategoryOverrideMarksOnlyAffectedMemberships:
    def test_category_override_marks_only_the_overridden_chapters_membership_stale(
        self, db_session, factories, admin_user
    ):
        scene = _two_chapter_scene(db_session, factories)
        assert scene.member1.membership_state == MembershipState.CURRENT.value
        assert scene.member2.membership_state == MembershipState.CURRENT.value
        # Оба члена делят ОДИН контекст (общее написание, обе без статьи) —
        # без этого равенства ниже утверждение об оси членства (не контекста)
        # не проверено.
        assert scene.member1.context_id == scene.member2.context_id

        category_id = _leaf_category_ids(db_session, 1)[0]
        set_override(
            db_session,
            estimate_id=scene.estimate.id,
            position_item_id=scene.chapter1.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        member1 = _member(db_session, scene.work1.id)
        member2 = _member(db_session, scene.work2.id)
        assert member1.context_id == member2.context_id, "они по-прежнему в одном контексте"
        assert member1.membership_state == MembershipState.STALE.value, (
            "затронутое членство обязано стать STALE"
        )
        assert member2.membership_state == MembershipState.CURRENT.value, (
            "незатронутое членство того же контекста обязано остаться CURRENT"
        )


# ---------------------------------------------------------------------------
#  Цикл CURRENT -> STALE -> CURRENT, три шага (спека §2.5)
# ---------------------------------------------------------------------------

class TestCategoryOverrideCurrentStaleCurrentCycle:
    def test_category_override_then_clear_override_returns_the_membership_to_current(
        self, db_session, factories, admin_user
    ):
        scene = _two_chapter_scene(db_session, factories)
        member = _member(db_session, scene.work1.id)
        assert member.membership_state == MembershipState.CURRENT.value  # шаг 1

        category_id = _leaf_category_ids(db_session, 1)[0]
        set_override(
            db_session, estimate_id=scene.estimate.id, position_item_id=scene.chapter1.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()
        member = _member(db_session, scene.work1.id)
        assert member.membership_state == MembershipState.STALE.value  # шаг 2
        events_after_stale = _events(db_session, member.context_id, "members_marked_stale")

        clear_override(db_session, estimate_id=scene.estimate.id, position_item_id=scene.chapter1.id)
        db_session.flush()
        db_session.expire_all()
        member = _member(db_session, scene.work1.id)
        assert member.membership_state == MembershipState.CURRENT.value  # шаг 3

        # Переход STALE -> CURRENT не пишет `members_marked_stale` —
        # событие называет ИМЕННО помеченных устаревшими, а не любое
        # приведение состояния.
        events_after_current = _events(db_session, member.context_id, "members_marked_stale")
        assert len(events_after_current) == len(events_after_stale), (
            "возврат в CURRENT не должен писать новых members_marked_stale"
        )


# ---------------------------------------------------------------------------
#  RefreshReport: три счётчика порознь, идемпотентность
# ---------------------------------------------------------------------------

class TestRefreshReportCounters:
    def test_marked_stale_and_marked_current_are_counted_independently(
        self, db_session, factories
    ):
        """Три счётчика ПОРОЗНЬ, а не только суммарно: сцена — четыре членства
        одного контекста; статья раздела меняется В ОБХОД `apply_overrides`
        (`_raw_set_chapter_category`), поэтому `refresh_membership_states`
        зовётся ЯВНО и есть что приводить.

        Прямой ход и обратный вызываются с РАЗНЫМ набором id (4 и 3) — а не
        зеркально одним и тем же, — так что `unchanged` тоже РАЗНЫЙ (2 и 1):
        перестановка `marked_stale`/`marked_current` не может остаться
        зелёной за счёт совпавшего `unchanged`."""
        _estimate, chapters, works = _four_chapter_scene(db_session, factories)
        category_id = _leaf_category_ids(db_session, 1)[0]
        all_ids = [w.id for w in works]

        # Прямой ход: разделы 1 и 2 получают статью МИМО apply_overrides —
        # их членства ещё CURRENT, хотя корзина (без статьи) уже разошлась
        # с их новой эффективной статьёй.
        _raw_set_chapter_category(db_session, chapters[0], category_id)
        _raw_set_chapter_category(db_session, chapters[1], category_id)

        forward = refresh_membership_states(db_session, position_item_ids=all_ids)
        assert forward == RefreshReport(marked_stale=2, marked_current=0, unchanged=2)
        assert _member(db_session, works[0].id).membership_state == MembershipState.STALE.value
        assert _member(db_session, works[1].id).membership_state == MembershipState.STALE.value
        assert _member(db_session, works[2].id).membership_state == MembershipState.CURRENT.value
        assert _member(db_session, works[3].id).membership_state == MembershipState.CURRENT.value

        # Обратный ход: статья снята — членства 1 и 2 обязаны вернуться в
        # CURRENT. Набор id НА ЭТОТ РАЗ короче (без works[3]) — unchanged
        # обязан отличаться от прямого хода (1, не 2), а не просто повторить
        # прошлое число.
        _raw_set_chapter_category(db_session, chapters[0], None)
        _raw_set_chapter_category(db_session, chapters[1], None)

        reverse = refresh_membership_states(
            db_session, position_item_ids=[works[0].id, works[1].id, works[2].id]
        )
        assert reverse == RefreshReport(marked_stale=0, marked_current=2, unchanged=1)
        assert forward.unchanged != reverse.unchanged, (
            "обе стороны цикла обязаны предъявить РАЗНЫЙ unchanged, иначе "
            "перепутанные marked_stale/marked_current не гарантированно ловятся"
        )

    def test_idempotent_rerun_gives_zero_zero(self, db_session, factories, admin_user):
        scene = _two_chapter_scene(db_session, factories)
        ids = [scene.work1.id, scene.work2.id]

        first = refresh_membership_states(db_session, position_item_ids=ids)
        assert (first.marked_stale, first.marked_current) == (0, 0)

        second = refresh_membership_states(db_session, position_item_ids=ids)
        assert second == RefreshReport(0, 0, 2)

    def test_empty_input_gives_zero_report_without_touching_the_database(
        self, db_session, factories
    ):
        report = refresh_membership_states(db_session, position_item_ids=[])
        assert report == RefreshReport(0, 0, 0)


# ---------------------------------------------------------------------------
#  Событие `members_marked_stale` — только при marked_stale > 0
# ---------------------------------------------------------------------------

class TestMembersMarkedStaleEvent:
    def test_category_override_event_is_written_with_count_and_trigger(self, db_session, factories, admin_user):
        scene = _two_chapter_scene(db_session, factories)
        category_id = _leaf_category_ids(db_session, 1)[0]

        set_override(
            db_session, estimate_id=scene.estimate.id, position_item_id=scene.chapter1.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        member1 = _member(db_session, scene.work1.id)
        events = _events(db_session, member1.context_id, "members_marked_stale")
        assert len(events) == 1
        assert events[0].payload == {"count": 1, "trigger": "category_override"}

    def test_no_event_when_nothing_becomes_stale(self, db_session, factories):
        scene = _two_chapter_scene(db_session, factories)
        # apply_overrides никогда не вызывался с реальными решениями — только
        # штатно, внутри импорта, где решений ещё нет: ни одно членство не
        # затронуто, событий нет вовсе.
        before = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(SemanticEvent.event_type == "members_marked_stale")
        ).scalar_one()

        apply_overrides(db_session, scene.estimate.id)
        db_session.flush()

        after = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(SemanticEvent.event_type == "members_marked_stale")
        ).scalar_one()
        assert after == before


# ---------------------------------------------------------------------------
#  Каскад из раунда: разнос раунда обязан привести членства ВСЕХ его смет.
#  Отдельного вызова `refresh_membership_states` в `_recompute` нет —
#  решение оркестратора: он эквивалентен вызову, встроенному в
#  `apply_overrides`, который `_recompute` зовёт по каждой смете раунда в
#  цикле; поведенческий тест ниже держится именно этим вызовом.
# ---------------------------------------------------------------------------

class TestRoundRecomputeRefreshesAllRoundEstimates:
    def test_round_override_marks_stale_in_every_offer_estimate_sharing_the_context(
        self, db_session, factories, admin_user
    ):
        """Тот же логический раздел присутствует в ДВУХ offer-сметах раунда
        (одинаковое написание + номер раздела); разнос применяется РАЗОМ.
        Оба членства обязаны стать STALE — `_recompute` зовёт
        `apply_overrides` по каждой смете, и приведение обязано дойти до
        каждой из них."""
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender)
        db_session.flush()

        rows = [
            position(job_title="Раздел раунда", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=f"Работа раунда {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]

        estimate_ids = []
        for _ in range(2):
            contract = factories.ContractFactory.create()
            db_session.flush()
            offer_contract = contract  # титул/адрес роли не играют для раундового резолва
            data = payload_for(offer_contract, rows)
            package = factories.OfferPackageFactory.create(tender=tender)
            offer = factories.OfferFactory.create(round=rnd, package=package, tender_id=tender.id)
            db_session.flush()
            outcome = import_estimate(
                db_session,
                owner=contract_estimate_owner(offer_contract, None),
                data=data,
                parser_version="1.0.0",
                import_job_id=None,
                replace=False,
                unit_resolver=UnitResolver(db_session),
                category_resolver=CategoryResolver.from_db(db_session),
            )
            # Смета создана как договорная (owner=contract_estimate_owner) —
            # переставляем её на предложение раунда: тесту нужны ДВЕ сметы,
            # разделяющие один логический раздел, а не реальный импорт раунда.
            est = db_session.get(Estimate, outcome.estimate_id)
            est.contract_id = None
            est.offer_id = offer.id
            db_session.flush()
            match_positions(db_session, outcome.positions_to_match)
            db_session.flush()
            route_positions(db_session, estimate_ids=[est.id])
            estimate_ids.append(est.id)

        # Оба членства CURRENT до разноса.
        members_before = [
            _member(db_session, w.id)
            for eid in estimate_ids
            for w in db_session.execute(
                sa.select(PositionItem)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == eid, PositionItem.is_chapter.is_(False))
            ).scalars().all()
        ]
        assert all(m.membership_state == MembershipState.CURRENT.value for m in members_before)

        category_id = _leaf_category_ids(db_session, 1)[0]
        set_round_override(
            db_session, tender_id=tender.id, round_id=rnd.id, lot_key="lot_1",
            position_key_in_proposal="1", work_category_id=category_id, note=None,
            user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        members_after = [
            _member(db_session, w.id)
            for eid in estimate_ids
            for w in db_session.execute(
                sa.select(PositionItem)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == eid, PositionItem.is_chapter.is_(False))
            ).scalars().all()
        ]
        assert all(m.membership_state == MembershipState.STALE.value for m in members_after), (
            "разнос раунда обязан пометить STALE членства ВСЕХ offer-смет раунда"
        )


# ---------------------------------------------------------------------------
#  `stale_mismatch_report` — независимая сверка, совпадает с
#  `effective_category_id` на разнообразных входах
# ---------------------------------------------------------------------------

class TestStaleMismatchReportAgreesWithEffectiveCategoryId:
    def test_report_is_empty_on_a_consistent_catalog_with_varied_inputs(
        self, db_session, factories, admin_user
    ):
        """Три разных входа сразу: позиция БЕЗ раздела, раздел БЕЗ статьи,
        раздел С ручным разносом — предикат обязан согласиться со всеми."""
        estimate = _make_routed_estimate(
            db_session,
            factories,
            [
                position(job_title="Раздел без статьи", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=f"Работа под разделом {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                # Позиция БЕЗ раздела вовсе — верхнего уровня, chapter_ref не задан.
                position(
                    job_title=f"Работа без раздела {_uid()}", unit="шт", quantity=1, suggested_quantity=1,
                    unit_cost_total="50.00", total_cost_total="50.00", number="3",
                ),
            ],
        )
        chapter = _chapter_row(db_session, estimate, "1")

        report_before = stale_mismatch_report(db_session)
        member_ids = set(
            db_session.execute(
                sa.select(ContextMember.position_item_id)
                .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate.id)
            ).scalars().all()
        )
        assert not (set(report_before) & member_ids)

        category_id = _leaf_category_ids(db_session, 1)[0]
        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        # Ручной разнос — членство теперь STALE, но хранимое ПРАВИЛЬНО
        # приведено (apply_overrides уже вызвал refresh): сверка по-прежнему
        # пуста для позиций этой сметы.
        report_after = stale_mismatch_report(db_session)
        assert not (set(report_after) & member_ids)


def _independent_sql_category(db, position_item_id: int) -> int | None:
    """Та же ОДНОУРОВНЕВАЯ SQL-форма, что несёт `stale_mismatch_report`
    (`position_items → chapter_item_id → work_category_id`), но возвращает
    ЗНАЧЕНИЕ статьи, а не факт расхождения — для прямого сравнения с
    `effective_category_id` по каждой позиции."""
    chapter = aliased(PositionItem)
    return db.execute(
        sa.select(chapter.work_category_id)
        .select_from(PositionItem)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .where(PositionItem.id == position_item_id)
    ).scalar_one()


class TestBothCategoryComputationsAgreePerPosition:
    """План требует отдельный вход «оба написания дают один ответ»: прямое
    сравнение `_independent_sql_category` (SQL-предикат
    `stale_mismatch_report`) с `effective_category_id` (Python-предикат
    `refresh_membership_states`) НА КАЖДОЙ позиции разнообразного каталога —
    не через хранимое `membership_state` одной сметы (косвенно), а напрямую.
    Вложенные разделы — специально: `chapter_context` ходит по цепочке
    `chapter_item_id` до КОРНЯ, а обе сверяемые формы читают только БЛИЖАЙШИЙ
    уровень (спека §1.7 — резолвер материализует унаследованное значение НА
    КАЖДОМ разделе поддерева); без вложенности расхождение на глубине 2 не
    могло бы проявиться ни в одной сцене этого файла."""

    def test_agreement_on_no_chapter_empty_chapter_nested_chapter_and_manual_override(
        self, db_session, factories, admin_user
    ):
        cat_inherited, cat_direct = _leaf_category_ids(db_session, 2)
        estimate = _make_routed_estimate(
            db_session,
            factories,
            [
                # (a) позиция БЕЗ раздела вовсе.
                position(
                    job_title=f"Без раздела {_uid()}", unit="шт", quantity=1, suggested_quantity=1,
                    unit_cost_total="10.00", total_cost_total="10.00", number="1",
                ),
                # (b) раздел БЕЗ статьи (не трогается разносом).
                position(job_title="Раздел пустой", is_chapter=True, chapter_number="10", number="2"),
                position(
                    job_title=f"Под пустым {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="20.00", total_cost_total="20.00", chapter_ref="10", number="3",
                ),
                # (c) вложенные разделы: "20" — предок, "20.1" — потомок по
                # номеру (дот-нотация, тот же приём, что у `imported_estimate`
                # в conftest.py). Override — на ПРЕДКЕ; позиция — под потомком.
                position(job_title="Раздел верхний", is_chapter=True, chapter_number="20", number="4"),
                position(job_title="Подраздел", is_chapter=True, chapter_number="20.1", number="5"),
                position(
                    job_title=f"Под подразделом {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="30.00", total_cost_total="30.00", chapter_ref="20.1", number="6",
                ),
                # (d) прямой ручной разнос — override на СОБСТВЕННОМ разделе
                # позиции, не унаследованный.
                position(job_title="Раздел прямой", is_chapter=True, chapter_number="30", number="7"),
                position(
                    job_title=f"Прямой разнос {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="40.00", total_cost_total="40.00", chapter_ref="30", number="8",
                ),
            ],
        )
        no_chapter_position = db_session.execute(
            sa.select(PositionItem.id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(
                Lot.estimate_id == estimate.id,
                PositionItem.chapter_item_id.is_(None),
                PositionItem.is_chapter.is_(False),
            )
        ).scalar_one()
        chapter_empty = _chapter_row(db_session, estimate, "10")
        chapter_top = _chapter_row(db_session, estimate, "20")
        chapter_nested = _chapter_row(db_session, estimate, "20.1")
        chapter_direct = _chapter_row(db_session, estimate, "30")

        # Наследование действительно случилось ДО override — иначе сцена не
        # проверяет вложенность, а `chapter_nested.chapter_item_id` не ведёт
        # на `chapter_top`.
        assert chapter_nested.chapter_item_id == chapter_top.id, (
            "«20.1» обязан числиться потомком «20» — иначе сцена не про вложенность"
        )

        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_top.id,
            work_category_id=cat_inherited, note=None, user_id=admin_user.id,
        )
        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_direct.id,
            work_category_id=cat_direct, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        # Наследование реально материализовалось на ПОТОМКЕ (спека §1.7) —
        # без этого сравнение ниже проверило бы предка, а не то расхождение
        # глубины 2, ради которого сцена заведена.
        db_session.refresh(chapter_nested)
        assert chapter_nested.work_category_id == cat_inherited

        positions_under_test = {
            "нет раздела": no_chapter_position,
            "раздел без статьи": db_session.execute(
                sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_empty.id)
            ).scalar_one(),
            "вложенный раздел, унаследованная статья": db_session.execute(
                sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_nested.id)
            ).scalar_one(),
            "раздел с прямым разносом": db_session.execute(
                sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_direct.id)
            ).scalar_one(),
        }

        for label, position_item_id in positions_under_test.items():
            sql_value = _independent_sql_category(db_session, position_item_id)
            python_value = effective_category_id(db_session, position_item_id)
            assert sql_value == python_value, (
                f"«{label}»: независимый SQL ({sql_value!r}) разошёлся с "
                f"effective_category_id ({python_value!r})"
            )


class TestStaleMismatchReportIsIndependentOfEffectiveCategoryId:
    """Снятие «общий предикат сверки»: PERMANENT-тест, доказывающий, что
    `stale_mismatch_report` — СВОЙ SQL-запрос. Инъекция ошибки в
    `effective_category_id` (используется `refresh_membership_states`, но
    НЕ используется сверкой) обязана дать РАСХОЖДЕНИЕ — сверка ловит
    неправильно приведённое хранимое состояние, потому что не разделяет
    предикат с генератором."""

    def test_a_bug_in_effective_category_id_is_caught_by_the_independent_report(
        self, db_session, factories, admin_user, monkeypatch
    ):
        estimate = _make_routed_estimate(
            db_session,
            factories,
            [
                position(job_title="Раздел под подмену", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=f"Работа под подмену {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
            ],
        )
        chapter = _chapter_row(db_session, estimate, "1")
        work = db_session.execute(
            sa.select(PositionItem).where(PositionItem.chapter_item_id == chapter.id)
        ).scalar_one()

        category_id = _leaf_category_ids(db_session, 1)[0]

        # Внедрённая ошибка: effective_category_id ВСЕГДА отвечает "нет статьи".
        # `refresh_membership_states` (через `context_operations`) использует
        # ИМЕННО эту функцию — приведёт членство неверно (оставит CURRENT).
        import services.context_operations as context_operations_module

        def broken_effective_category_id(db, position_item_id):
            return None

        monkeypatch.setattr(
            context_operations_module.context_routing_module,
            "effective_category_id",
            broken_effective_category_id,
        )

        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        member = _member(db_session, work.id)
        assert member.membership_state == MembershipState.CURRENT.value, (
            "с внедрённой ошибкой приведение (через испорченный effective_category_id) "
            "неверно оставляет CURRENT"
        )

        report = stale_mismatch_report(db_session)
        assert work.id in report, (
            "независимая сверка обязана РАЗОЙТИСЬ с неверно приведённым хранимым "
            "состоянием — иначе она делит предикат с генератором"
        )


# ---------------------------------------------------------------------------
#  `transfer_proposal` — вычисляется, ничего не пишет
# ---------------------------------------------------------------------------

class TestTransferProposal:
    def test_none_for_a_current_membership(self, db_session, factories):
        scene = _two_chapter_scene(db_session, factories)
        assert transfer_proposal(db_session, position_item_id=scene.work1.id) is None

    def _counts(self, db):
        return (
            db.execute(sa.select(sa.func.count()).select_from(ContextBucket)).scalar_one(),
            db.execute(sa.select(sa.func.count()).select_from(CatalogContext)).scalar_one(),
            db.execute(sa.select(sa.func.count()).select_from(ContextMember)).scalar_one(),
            db.execute(sa.select(sa.func.count()).select_from(SemanticEvent)).scalar_one(),
        )

    def test_proposal_for_a_not_yet_existing_target_bucket_has_none_ids(
        self, db_session, factories, admin_user
    ):
        scene = _two_chapter_scene(db_session, factories)
        category_id = _leaf_category_ids(db_session, 1)[0]

        set_override(
            db_session, estimate_id=scene.estimate.id, position_item_id=scene.chapter1.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        before = self._counts(db_session)
        proposal = transfer_proposal(db_session, position_item_id=scene.work1.id)
        after = self._counts(db_session)

        assert proposal is not None
        assert proposal.effective_category_id == category_id
        assert proposal.proposed_bucket_id is None
        assert proposal.proposed_context_id is None
        assert before == after, "предложение обязано вычисляться БЕЗ единой записи"

    def test_proposal_for_an_existing_target_bucket_names_it(
        self, db_session, factories, admin_user
    ):
        """Две одинаковые работы в разных разделах, обе без статьи изначально
        (одна корзина на двоих). Первую переносят в статью A принятием
        предложения (корзина A×написание рождается ИМЕННО так — разнос сам
        по себе корзину не создаёт, §2.8); предложение для ВТОРОЙ, разнесённой
        в ту же статью A, обязано указать на УЖЕ существующую корзину/контекст
        первой."""
        shared_title = f"Общая работа {_uid()}"
        category_id, _other = _leaf_category_ids(db_session, 2)

        estimate = _make_routed_estimate(
            db_session,
            factories,
            [
                position(job_title="Раздел один", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                position(job_title="Раздел два", is_chapter=True, chapter_number="2", number="3"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
                ),
            ],
        )
        chapter_one = _chapter_row(db_session, estimate, "1")
        chapter_two = _chapter_row(db_session, estimate, "2")
        work_one = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_one.id)
        ).scalar_one()
        work_two = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_two.id)
        ).scalar_one()

        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_one.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        # Первую позицию действительно ПЕРЕНОСЯТ — только это рождает
        # корзину «написание × статья A» (§2.8: разнос сам по себе корзину
        # не создаёт, только помечает STALE).
        first_proposal = transfer_proposal(db_session, position_item_id=work_one)
        accept_transfer(
            db_session, position_item_id=work_one,
            expected_category_id=first_proposal.effective_category_id, actor_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()
        existing_bucket_member = _member(db_session, work_one)

        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_two.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        proposal = transfer_proposal(db_session, position_item_id=work_two)
        assert proposal is not None
        assert proposal.proposed_bucket_id == existing_bucket_member.bucket_id
        assert proposal.proposed_context_id == existing_bucket_member.context_id

    def test_proposal_and_accept_for_a_none_category_target_name_the_existing_no_article_bucket(
        self, db_session, factories, admin_user
    ):
        """Целевая статья предложения может быть `None` («нет статьи») так
        же законно, как любая другая — `_lookup_bucket` ищет корзину по
        `ContextBucket.work_category_id == work_category_id`, и `Column ==
        None` в SQLAlchemy компилируется в `IS NULL` (тот же приём, что
        `get_or_create_bucket`), а не в `= NULL`, которое не нашло бы строку
        никогда. Сцена: два раздела с общим написанием, оба СТАРТУЮТ без
        статьи (корзина «написание × без статьи» уже существует — в ней всё
        время лежит вторая позиция); первую переносят в статью A и обратно —
        второй разнос возвращает её эффективную статью к `None`, а хранимая
        корзина остаётся «× A» (расхождение, снова `STALE`). Предложение
        обязано назвать УЖЕ существующую корзину «без статьи» и контекст
        второй позиции, а не «корзина будет создана» — а принятие обязано
        лечь в НЕЁ, не заводя вторую корзину «× None»."""
        shared_title = f"Работа без статьи {_uid()}"
        category_id = _leaf_category_ids(db_session, 1)[0]

        estimate = _make_routed_estimate(
            db_session,
            factories,
            [
                position(job_title="Раздел туда-обратно", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                position(job_title="Раздел якорь", is_chapter=True, chapter_number="2", number="3"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
                ),
            ],
        )
        chapter_round_trip = _chapter_row(db_session, estimate, "1")
        chapter_anchor = _chapter_row(db_session, estimate, "2")
        work_round_trip = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_round_trip.id)
        ).scalar_one()
        work_anchor = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_anchor.id)
        ).scalar_one()

        # Якорь никогда не покидает корзину «без статьи» — она существует и
        # используется на всём протяжении теста.
        no_article_member_before = _member(db_session, work_anchor)
        assert no_article_member_before.membership_state == MembershipState.CURRENT.value

        # Разнос → статья A → принятие переноса.
        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_round_trip.id,
            work_category_id=category_id, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()
        to_a = transfer_proposal(db_session, position_item_id=work_round_trip)
        accept_transfer(
            db_session, position_item_id=work_round_trip,
            expected_category_id=to_a.effective_category_id, actor_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        bucket_count_before_round_trip = db_session.execute(
            sa.select(sa.func.count()).select_from(ContextBucket)
        ).scalar_one()

        # Снятие разноса → статья снова None → членство снова STALE (хранимая
        # корзина «× A», эффективная статья — «нет статьи»).
        clear_override(db_session, estimate_id=estimate.id, position_item_id=chapter_round_trip.id)
        db_session.flush()
        db_session.expire_all()
        member = _member(db_session, work_round_trip)
        assert member.membership_state == MembershipState.STALE.value

        proposal = transfer_proposal(db_session, position_item_id=work_round_trip)
        assert proposal is not None
        assert proposal.effective_category_id is None
        assert proposal.proposed_bucket_id == no_article_member_before.bucket_id, (
            "предложение обязано назвать УЖЕ существующую корзину «без статьи», "
            "а не сообщать, что она будет создана"
        )
        assert proposal.proposed_context_id == no_article_member_before.context_id

        moved = accept_transfer(
            db_session, position_item_id=work_round_trip,
            expected_category_id=proposal.effective_category_id, actor_id=admin_user.id,
        )
        assert moved == 1
        db_session.flush()
        db_session.expire_all()

        final_member = _member(db_session, work_round_trip)
        assert final_member.membership_state == MembershipState.CURRENT.value
        assert final_member.bucket_id == no_article_member_before.bucket_id
        assert final_member.context_id == no_article_member_before.context_id

        bucket_count_after = db_session.execute(
            sa.select(sa.func.count()).select_from(ContextBucket)
        ).scalar_one()
        assert bucket_count_after == bucket_count_before_round_trip, (
            "принятие обязано лечь в СУЩЕСТВУЮЩУЮ корзину «без статьи», а не завести вторую"
        )


# ---------------------------------------------------------------------------
#  `accept_transfer` — принятие под блокировкой, перечитывание
# ---------------------------------------------------------------------------

class TestAcceptTransfer:
    def _stale_scene(self, db, factories, admin_user):
        shared_title = f"Принимаемая работа {_uid()}"
        cat_a, cat_b = _leaf_category_ids(db, 2)
        estimate = _make_routed_estimate(
            db, factories,
            [
                position(job_title="Раздел A", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                position(job_title="Раздел B", is_chapter=True, chapter_number="2", number="3"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
                ),
            ],
        )
        chapter_a = _chapter_row(db, estimate, "1")
        chapter_b = _chapter_row(db, estimate, "2")
        work_a_id = db.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_a.id)
        ).scalar_one()
        target_work_id = db.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_b.id)
        ).scalar_one()

        set_override(
            db, estimate_id=estimate.id, position_item_id=chapter_a.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db.flush()
        db.expire_all()

        # Корзину «написание × cat_a» рождает ТОЛЬКО перенос, не сам разнос
        # (§2.8) — принимаем предложение по первому разделу, чтобы у второго
        # была УЖЕ существующая цель.
        first_proposal = transfer_proposal(db, position_item_id=work_a_id)
        accept_transfer(
            db, position_item_id=work_a_id,
            expected_category_id=first_proposal.effective_category_id, actor_id=admin_user.id,
        )
        db.flush()
        db.expire_all()

        set_override(
            db, estimate_id=estimate.id, position_item_id=chapter_b.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db.flush()
        db.expire_all()

        return estimate, chapter_b, target_work_id, cat_a, cat_b

    def test_accepting_a_stale_proposal_moves_the_membership(
        self, db_session, factories, admin_user
    ):
        estimate, chapter_b, target_work_id, cat_a, _cat_b = self._stale_scene(
            db_session, factories, admin_user
        )
        before = _member(db_session, target_work_id)
        assert before.membership_state == MembershipState.STALE.value
        from_context_id = before.context_id

        proposal = transfer_proposal(db_session, position_item_id=target_work_id)
        assert proposal is not None
        assert proposal.proposed_bucket_id is not None

        events_before = _events(db_session, proposal.proposed_context_id, "members_moved")

        moved = accept_transfer(
            db_session, position_item_id=target_work_id,
            expected_category_id=proposal.effective_category_id, actor_id=admin_user.id,
        )
        assert moved == 1

        after = _member(db_session, target_work_id)
        assert after.membership_state == MembershipState.CURRENT.value
        assert after.bucket_id == proposal.proposed_bucket_id
        assert after.context_id == proposal.proposed_context_id

        events_after = _events(db_session, after.context_id, "members_moved")
        new_events = [e for e in events_after if e.id not in {e0.id for e0 in events_before}]
        assert len(new_events) == 1
        assert new_events[0].payload == {
            "from_context_id": from_context_id, "moved_members": 1, "reason": "stale_accepted",
        }

    def test_refuses_and_returns_a_fresh_proposal_when_category_changed_again(
        self, db_session, factories, admin_user
    ):
        estimate, chapter_b, target_work_id, cat_a, cat_b = self._stale_scene(
            db_session, factories, admin_user
        )
        stale_proposal = transfer_proposal(db_session, position_item_id=target_work_id)
        assert stale_proposal.effective_category_id == cat_a

        before = _member(db_session, target_work_id)
        before_context_id, before_bucket_id = before.context_id, before.bucket_id

        # Статья успела поменяться СНОВА, пока предложение показывалось.
        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_b.id,
            work_category_id=cat_b, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        with pytest.raises(ContextOperationError) as exc:
            accept_transfer(
                db_session, position_item_id=target_work_id,
                expected_category_id=stale_proposal.effective_category_id,  # устаревшее значение (cat_a)
                actor_id=admin_user.id,
            )
        assert exc.value.code == REFUSE_CATEGORY_CHANGED
        new_proposal = exc.value.new_proposal
        assert new_proposal is not None
        assert new_proposal.effective_category_id == cat_b

        after = _member(db_session, target_work_id)
        assert after.context_id == before_context_id
        assert after.bucket_id == before_bucket_id
        assert after.membership_state == MembershipState.STALE.value, "ничего не перенесено"


class TestAcceptTransferRefusesOnCurrentMembership:
    """Сверх плана: принятие на `CURRENT`-членстве — переносить нечего,
    `transfer_proposal` на нём уже вернул бы `None`."""

    def test_accept_transfer_refuses_with_its_own_code_and_changes_nothing(
        self, db_session, factories, admin_user
    ):
        scene = _two_chapter_scene(db_session, factories)
        assert scene.member1.membership_state == MembershipState.CURRENT.value
        before_context_id, before_bucket_id = scene.member1.context_id, scene.member1.bucket_id

        with pytest.raises(ContextOperationError) as exc:
            accept_transfer(
                db_session, position_item_id=scene.work1.id, expected_category_id=None,
                actor_id=admin_user.id,
            )
        assert exc.value.code == "not_stale"

        after = _member(db_session, scene.work1.id)
        assert after.context_id == before_context_id
        assert after.bucket_id == before_bucket_id
        assert after.membership_state == MembershipState.CURRENT.value


class TestAcceptTransferCompilesToForUpdateBeforeReread:
    """Компиляция РЕАЛЬНОГО SQL — членство и корзина цели блокируются
    `FOR UPDATE` (не `FOR SHARE`/`NO KEY`), и перечитывание статьи
    (`position_items`) идёт ПОСЛЕ блокировки корзин, а не до неё."""

    def _stale_scene_with_existing_target(self, db, factories, admin_user):
        shared_title = f"Работа лока {_uid()}"
        cat_a, _cat_b = _leaf_category_ids(db, 2)
        estimate = _make_routed_estimate(
            db, factories,
            [
                position(job_title="Раздел лока A", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                position(job_title="Раздел лока B", is_chapter=True, chapter_number="2", number="3"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
                ),
            ],
        )
        chapter_a = _chapter_row(db, estimate, "1")
        chapter_b = _chapter_row(db, estimate, "2")
        work_a_id = db.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_a.id)
        ).scalar_one()
        target_work_id = db.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_b.id)
        ).scalar_one()

        set_override(
            db, estimate_id=estimate.id, position_item_id=chapter_a.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db.flush()
        db.expire_all()
        first_proposal = transfer_proposal(db, position_item_id=work_a_id)
        accept_transfer(
            db, position_item_id=work_a_id,
            expected_category_id=first_proposal.effective_category_id, actor_id=admin_user.id,
        )
        db.flush()
        db.expire_all()

        set_override(
            db, estimate_id=estimate.id, position_item_id=chapter_b.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db.flush()
        db.expire_all()
        return target_work_id

    def test_locks_membership_and_target_bucket_for_update_before_rereading_the_category(
        self, db_session, factories, admin_user
    ):
        target_work_id = self._stale_scene_with_existing_target(db_session, factories, admin_user)
        proposal = transfer_proposal(db_session, position_item_id=target_work_id)
        assert proposal.proposed_bucket_id is not None, "сцена обязана дать УЖЕ существующую цель"

        with _capturing_sql(db_session) as statements:
            accept_transfer(
                db_session, position_item_id=target_work_id,
                expected_category_id=proposal.effective_category_id, actor_id=admin_user.id,
            )

        member_lock_idx, member_lock_stmt = _find_lock_statement(statements, "context_members")
        assert "FOR UPDATE" in member_lock_stmt
        assert "FOR SHARE" not in member_lock_stmt
        assert "KEY SHARE" not in member_lock_stmt
        assert "NO KEY" not in member_lock_stmt
        # Единственный законный запрос ДО лока членства — ленивая подгрузка
        # ОБЪЕКТА теста (`admin_user`, вне тела accept_transfer, таблица
        # `users`); ни один запрос к `context_buckets`/`context_routing_rules`/
        # `position_items` (маршрутизационные предусловия) до лока идти не
        # должен.
        routing_reads_before_lock = [
            s for s in statements[:member_lock_idx]
            if any(t in s for t in ("context_buckets", "context_routing_rules", "position_items"))
        ]
        assert not routing_reads_before_lock, (
            f"чтение маршрутизационного предусловия ДО лока членства: {routing_reads_before_lock!r}"
        )

        bucket_lock_idx, bucket_lock_stmt = _find_lock_statement(statements, "context_buckets")
        assert "FOR UPDATE" in bucket_lock_stmt
        assert "FOR SHARE" not in bucket_lock_stmt
        assert "KEY SHARE" not in bucket_lock_stmt
        assert "NO KEY" not in bucket_lock_stmt
        assert bucket_lock_idx > member_lock_idx

        reread_indices = [
            i for i, s in enumerate(statements)
            if i > bucket_lock_idx and "position_items" in s and s.lstrip().upper().startswith("SELECT")
        ]
        assert reread_indices, (
            "перечитывание статьи (position_items) обязано идти ПОСЛЕ блокировки "
            "корзин, а не до неё"
        )


class TestAcceptTransferRelocksWhenTargetBucketAppearsBetweenReadAndLock:
    """Гонка: корзина цели выбирается для блокировки по статье, прочитанной
    ДО лока (`candidate_effective`). Если корзина цели
    рождается МЕЖДУ этим чтением и локом (конкурентная сессия создала её
    первой), перечитанная статья может СОВПАСТЬ с ожидаемой, а корзина —
    остаться незаблокированной. Нетредовая (не потоковая) проверка: гонка
    симулируется монкипатчем `lock_buckets`, который перед РЕАЛЬНОЙ
    блокировкой создаёт целевую корзину ЧЕРЕЗ ДРУГУЮ, отдельно закоммиченную
    сессию — тот же приём, что `TestFamilyRereadAfterLock`
    (`test_work_families.py`)."""

    def test_refuses_when_the_target_bucket_is_created_by_another_session_after_the_candidate_read(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        import services.context_operations as context_operations_module
        from services.context_routing import route_position

        admin = committing_factories.UserFactory.create(role=UserRole.admin)
        committing_db.commit()

        shared_title = f"Гонка на корзине {_uid()}"
        cat_a = _leaf_category_ids(committing_db, 1)[0]
        estimate = _make_routed_estimate(
            committing_db, committing_factories,
            [
                position(job_title="Раздел гонки", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
            ],
        )
        chapter = _chapter_row(committing_db, estimate, "1")
        work_id = committing_db.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter.id)
        ).scalar_one()
        catalog_position_id = committing_db.get(PositionItem, work_id).catalog_position_id

        set_override(
            committing_db, estimate_id=estimate.id, position_item_id=chapter.id,
            work_category_id=cat_a, note=None, user_id=admin.id,
        )
        committing_db.commit()

        proposal = transfer_proposal(committing_db, position_item_id=work_id)
        assert proposal is not None
        assert proposal.proposed_bucket_id is None, "сцена обязана начать БЕЗ существующей цели"

        # Донор, которого «другая сессия» ПОЗЖЕ (внутри lock_buckets) реально
        # промаршрутизирует в статью cat_a — это и родит корзину цели.
        donor_proposal = committing_factories.ProposalFactory.create()
        donor_chapter = committing_factories.PositionItemFactory.create(
            proposal=donor_proposal, is_chapter=True, job_title_in_proposal="Раздел-донор",
            work_category_id=cat_a, category_source="file",
        )
        donor_position = committing_factories.PositionItemFactory.create(
            proposal=donor_proposal, is_chapter=False, chapter_item_id=donor_chapter.id,
            catalog_position_id=catalog_position_id,
        )
        committing_db.commit()

        real_lock_buckets = context_operations_module.lock_buckets

        def lock_buckets_after_a_concurrent_bucket_is_born(db, bucket_ids, *, exclusive):
            with committing_session_factory() as other:
                route_position(other, position_item_id=donor_position.id)
                other.commit()
            return real_lock_buckets(db, bucket_ids, exclusive=exclusive)

        monkeypatch.setattr(
            context_operations_module, "lock_buckets", lock_buckets_after_a_concurrent_bucket_is_born
        )
        with pytest.raises(ContextOperationError) as exc:
            accept_transfer(
                committing_db, position_item_id=work_id,
                expected_category_id=proposal.effective_category_id, actor_id=admin.id,
            )

        assert exc.value.code == REFUSE_CATEGORY_CHANGED
        assert exc.value.new_proposal is not None
        # Ничего не перенесено — членство осталось на прежней (исходной) корзине.
        committing_db.expire_all()
        after = _member(committing_db, work_id)
        assert after.membership_state == MembershipState.STALE.value


class TestAcceptTransferRoutesIntoARuleSplitTargetBucket:
    """Корзина цели уже РАЗДЕЛЕНА правилом — и предложение, и принятие
    обязаны назвать/использовать контекст ПРАВИЛА, а не умолчательный
    «всегда default» (мутация, отключающая учёт правил, обязана
    покраснеть здесь)."""

    def test_proposal_names_the_rule_context_and_accept_lands_there(
        self, db_session, factories, admin_user
    ):
        shared_title = f"Работа с правилом {_uid()}"
        cat_a, _cat_b = _leaf_category_ids(db_session, 2)
        estimate = _make_routed_estimate(
            db_session, factories,
            [
                # Раздел-донор: сразу со статьёй cat_a — его перенос рождает
                # корзину cat_a и её умолчательный контекст.
                position(job_title="Раздел донор", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
                # Раздел «Особый» — цепочка его позиции обязана совпасть с
                # предикатом правила ниже; изначально БЕЗ статьи.
                position(job_title="Раздел Особый", is_chapter=True, chapter_number="2", number="3"),
                position(
                    job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
                ),
            ],
        )
        chapter_donor = _chapter_row(db_session, estimate, "1")
        chapter_special = _chapter_row(db_session, estimate, "2")
        donor_work_id = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_donor.id)
        ).scalar_one()
        special_work_id = db_session.execute(
            sa.select(PositionItem.id).where(PositionItem.chapter_item_id == chapter_special.id)
        ).scalar_one()

        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_donor.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()
        donor_proposal_obj = transfer_proposal(db_session, position_item_id=donor_work_id)
        accept_transfer(
            db_session, position_item_id=donor_work_id,
            expected_category_id=donor_proposal_obj.effective_category_id, actor_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        bucket_a_id = _member(db_session, donor_work_id).bucket_id
        default_context_id = _member(db_session, donor_work_id).context_id

        # Разделение корзины cat_a правилом — прямой INSERT (та же операция,
        # что «разделить», задача 6, вне границ этой задачи; тот же приём,
        # что `_extra_context`/ручной `ContextRoutingRule` в
        # `test_context_membership.py`).
        rule_context = CatalogContext(
            bucket_id=bucket_a_id, is_default=False,
            semantic_kind="WORK", semantic_kind_source="rule", semantic_kind_at=sa.func.now(),
            name_role="WORK", name_role_source="rule", name_role_at=sa.func.now(),
            place_dictionary_version=1, semantic_state="SUGGESTED",
        )
        db_session.add(rule_context)
        db_session.flush()
        rule = ContextRoutingRule(
            bucket_id=bucket_a_id, ordinal=1,
            predicate={"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Раздел Особый"},
            context_id=rule_context.id, created_by=admin_user.id,
        )
        db_session.add(rule)
        db_session.flush()

        # Теперь разносим «Особый» раздел в ту же статью cat_a — его позиция
        # станет STALE (бакет не менялся), а её ЦЕПОЧКА совпадает с правилом.
        set_override(
            db_session, estimate_id=estimate.id, position_item_id=chapter_special.id,
            work_category_id=cat_a, note=None, user_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        proposal = transfer_proposal(db_session, position_item_id=special_work_id)
        assert proposal is not None
        assert proposal.proposed_bucket_id == bucket_a_id
        assert proposal.proposed_context_id == rule_context.id, (
            "предложение обязано назвать контекст ПРАВИЛА, а не умолчательный default"
        )
        assert proposal.proposed_context_id != default_context_id

        accept_transfer(
            db_session, position_item_id=special_work_id,
            expected_category_id=proposal.effective_category_id, actor_id=admin_user.id,
        )
        db_session.flush()
        db_session.expire_all()

        final_member = _member(db_session, special_work_id)
        assert final_member.context_id == rule_context.id
        assert final_member.bucket_id == bucket_a_id
        assert final_member.routed_by == "rule"
        assert final_member.routing_rule_id == rule.id


# ---------------------------------------------------------------------------
#  Равенство хранимого/вычисляемого переживает слияние в Review с конфликтом
#  (задача 9)
# ---------------------------------------------------------------------------

class TestConsistencySurvivesReviewMergeWithConflict:
    def test_stale_mismatch_report_stays_empty_after_a_conflicting_merge(
        self, db_session, factories, admin_user
    ):
        from services.context_routing import route_position
        from services.review import merge_into_position
        from services.work_families import activate_family, assign_family, create_family

        unit_id = UnitResolver(db_session).resolve("M2").unit_id
        target_cp = factories.CatalogPositionFactory.create(
            standard_job_title=f"Цель {_uid()}", kind="POSITION", unit_id=unit_id,
        )
        source_cp = factories.CatalogPositionFactory.create(
            standard_job_title=f"Источник {_uid()}", kind="TO_REVIEW", unit_id=unit_id,
        )
        target_proposal = factories.ProposalFactory.create()
        target_item = factories.PositionItemFactory.create(
            proposal=target_proposal, is_chapter=False, catalog_position_id=target_cp.id,
        )
        source_proposal = factories.ProposalFactory.create()
        source_item = factories.PositionItemFactory.create(
            proposal=source_proposal, is_chapter=False, catalog_position_id=source_cp.id,
        )
        target_member = route_position(db_session, position_item_id=target_item.id)
        route_position(db_session, position_item_id=source_item.id)

        source_family = create_family(
            db_session, title=f"Семья-источник {_uid()}", unit_name="M2",
            definition="Определение", actor_id=admin_user.id,
        )
        source_family = activate_family(db_session, family_id=source_family.id, actor_id=admin_user.id)
        target_family = create_family(
            db_session, title=f"Семья-цель {_uid()}", unit_name="M2",
            definition="Определение", actor_id=admin_user.id,
        )
        target_family = activate_family(db_session, family_id=target_family.id, actor_id=admin_user.id)
        source_member_before = _member(db_session, source_item.id)
        assign_family(
            db_session, context_id=source_member_before.context_id, family_id=source_family.id,
            actor_id=admin_user.id,
        )
        assign_family(
            db_session, context_id=target_member.context_id, family_id=target_family.id,
            actor_id=admin_user.id,
        )

        merge_into_position(db_session, to_review_id=source_cp.id, target_id=target_cp.id)
        db_session.flush()

        moved = _member(db_session, source_item.id)
        assert moved.conflict_at is not None, "сцена обязана дать конфликт — иначе утверждение не проверено"
        assert moved.membership_state == MembershipState.CURRENT.value

        report = stale_mismatch_report(db_session)
        assert source_item.id not in report
        assert target_item.id not in report


# ---------------------------------------------------------------------------
#  `replace`: контексты/правила/архивные переживают замену без единого
#  архивирования
# ---------------------------------------------------------------------------

def _fake_parse(payload):
    def _parse(handle):
        handle.read()
        return ParseResult(data=payload, parser_version="1.0.0", warnings=[])
    return _parse


@pytest.fixture
def job_env(committing_db, committing_factories, tmp_storage, committing_session_factory):
    """Локальная копия `job_env` из `test_import_pipeline.py` (см. докстроку
    модуля — фикстуры пайплайна не переиспользуются между файлами тестов)."""

    class Env:
        db = committing_db
        factories = committing_factories
        storage = tmp_storage
        session_factory = committing_session_factory

        def __init__(self):
            self.contract = committing_factories.ContractFactory.create()
            committing_db.flush()
            self.job = self.new_job()
            committing_db.commit()

        def new_job(self, *, amendment_no=None, contract=None):
            key = tmp_storage.save(b"PK\x03\x04not-a-real-xlsx")
            job = committing_factories.ImportJobFactory.create(
                contract=contract or self.contract, amendment_no=amendment_no,
                file_key=key, status=ImportJobStatus.pending.value,
            )
            committing_db.flush()
            return job

        def reload(self, job):
            committing_db.expire_all()
            from models import ImportJob
            return committing_db.get(ImportJob, job.id)

        def run(self, payload, *, job=None, **kwargs):
            kwargs.setdefault("parse", _fake_parse(payload))
            import_pipeline.run_import_job(
                (job or self.job).id, session_factory=committing_session_factory,
                storage=tmp_storage, **kwargs,
            )
            return self.reload(job or self.job)

    return Env()


class TestReplacePreservesContextsAndRules:
    def test_ids_defaults_rules_and_archived_at_are_unchanged_and_memberships_land_per_position(
        self, job_env
    ):
        rows = [
            position(job_title="Раздел замены", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title="Работа замены", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        first = job_env.run(payload_for(job_env.contract, rows))
        assert first.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        context_snapshot_before = {
            c.id: (c.is_default, c.archived_at)
            for c in job_env.db.execute(sa.select(CatalogContext)).scalars().all()
        }
        old_work_id = job_env.db.execute(
            sa.select(PositionItem.id).where(PositionItem.is_chapter.is_(False))
        ).scalar_one()
        old_member = _member(job_env.db, old_work_id)
        old_context_id = old_member.context_id

        # Без ХОТЯ БЫ ОДНОГО правила сравнение
        # `rule_ids_after == rule_ids_before` было бы тривиальным (оба —
        # пустое множество). Правило указывает в тот же (умолчательный)
        # контекст — этого достаточно, чтобы факт «правило пережило замену»
        # проверялся не пустотой с обеих сторон.
        admin = job_env.factories.UserFactory.create(role=UserRole.admin)
        job_env.db.add(
            ContextRoutingRule(
                bucket_id=old_member.bucket_id, ordinal=1,
                predicate={"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Раздел замены"},
                context_id=old_context_id, created_by=admin.id,
            )
        )
        job_env.db.flush()
        rule_ids_before = set(
            job_env.db.execute(sa.select(ContextRoutingRule.id)).scalars().all()
        )
        assert rule_ids_before, "сцена обязана дать НЕПУСТОЙ набор правил ДО замены"

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job, replace=True)
        assert job.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        context_snapshot_after = {
            c.id: (c.is_default, c.archived_at)
            for c in job_env.db.execute(sa.select(CatalogContext)).scalars().all()
        }
        assert context_snapshot_after == context_snapshot_before

        rule_ids_after = set(job_env.db.execute(sa.select(ContextRoutingRule.id)).scalars().all())
        assert rule_ids_after == rule_ids_before

        archived_count = job_env.db.execute(
            sa.select(sa.func.count()).select_from(CatalogContext).where(CatalogContext.archived_at.is_not(None))
        ).scalar_one()
        assert archived_count == 0

        new_work_id = job_env.db.execute(
            sa.select(PositionItem.id).where(PositionItem.is_chapter.is_(False))
        ).scalar_one()
        assert new_work_id != old_work_id
        new_member = _member(job_env.db, new_work_id)
        assert new_member.context_id == old_context_id

    def test_a_split_bucket_survives_replace_and_repopulates_both_contexts(self, job_env):
        shared_title = "Работа расщеплённой корзины"
        rows = [
            position(job_title="Раздел X", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
            position(job_title="Раздел Y", is_chapter=True, chapter_number="2", number="3"),
            position(
                job_title=shared_title, unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="2", number="4",
            ),
        ]
        first = job_env.run(payload_for(job_env.contract, rows))
        assert first.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        chapter_alias = aliased(PositionItem)
        work_y_before = job_env.db.execute(
            sa.select(PositionItem.id)
            .join(chapter_alias, chapter_alias.id == PositionItem.chapter_item_id)
            .where(
                PositionItem.job_title_in_proposal == shared_title,
                chapter_alias.job_title_in_proposal == "Раздел Y",
            )
        ).scalars().all()
        # Оба «Y»-варианта делят корзину с «X» (одинаковое написание, обе
        # статьи пустые) — разделяем правилом по цепочке раздела «Раздел Y».
        member_y = _member(job_env.db, work_y_before[0])
        default_context_id_before = member_y.context_id

        split = split_context(
            job_env.db, context_id=default_context_id_before,
            position_item_ids=list(work_y_before),
            rule={"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Раздел Y"},
            actor_id=job_env.factories.UserFactory.create(role=UserRole.admin).id,
        )
        job_env.db.commit()
        split_context_id = split.new_context_id

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job, replace=True)
        assert job.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        contexts_after = job_env.db.execute(
            sa.select(CatalogContext.id, CatalogContext.archived_at)
        ).all()
        by_id = {c.id: c.archived_at for c in contexts_after}
        assert default_context_id_before in by_id and by_id[default_context_id_before] is None
        assert split_context_id in by_id and by_id[split_context_id] is None

        chapter_alias2 = aliased(PositionItem)
        new_positions = job_env.db.execute(
            sa.select(PositionItem.id, chapter_alias2.job_title_in_proposal)
            .join(chapter_alias2, chapter_alias2.id == PositionItem.chapter_item_id)
            .where(PositionItem.job_title_in_proposal == shared_title)
        ).all()
        contexts_seen = set()
        for work_id, chapter_title in new_positions:
            member = _member(job_env.db, work_id)
            contexts_seen.add(member.context_id)
            if chapter_title == "Раздел Y":
                assert member.context_id == split_context_id
            else:
                assert member.context_id == default_context_id_before
        assert contexts_seen == {default_context_id_before, split_context_id}


class TestReplaceWithAutoArchiveFailsClosed:
    """Негативная сторона плана (§2.8): если бы опустевший контекст по
    умолчанию архивировался автоматически, `replace` упал бы доменной
    ошибкой маршрутизации на следующем же шаге ТОЙ ЖЕ транзакции — вместо
    того, чтобы молча заменить смету. Этот тест — ПОСТОЯННЫЙ регресс на
    отменённый вариант дизайна r6 §3.1: он симулирует запрещённое поведение
    монкипатчем и проверяет, что код по-прежнему падает fail-closed."""

    def test_replace_fails_closed_when_the_emptied_default_context_gets_archived(
        self, job_env, monkeypatch
    ):
        import services.estimate_import as estimate_import_module

        rows = [
            position(job_title="Раздел авто-архива", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title="Работа авто-архива", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        first = job_env.run(payload_for(job_env.contract, rows))
        assert first.status == ImportJobStatus.done.value
        job_env.db.expire_all()
        old_estimate_id = job_env.db.execute(sa.select(Estimate.id)).scalar_one()

        real_replace_existing = estimate_import_module._replace_existing

        def replace_and_archive_emptied_defaults(db, contract_id, amendment_no, replace, warnings):
            old_id = real_replace_existing(db, contract_id, amendment_no, replace, warnings)
            if old_id is not None:
                # Симуляция отменённого варианта дизайна: контекст по
                # умолчанию, оставшийся без членов ПОСЛЕ удаления старой
                # сметы (в этой же транзакции), архивируется. Прямой UPDATE,
                # в обход `archive_context` — её предусловие 3 именно ЭТО и
                # запрещает.
                db.execute(
                    sa.update(CatalogContext)
                    .where(
                        CatalogContext.is_default.is_(True),
                        CatalogContext.archived_at.is_(None),
                        ~sa.exists(
                            sa.select(ContextMember.position_item_id).where(
                                ContextMember.context_id == CatalogContext.id
                            )
                        ),
                    )
                    .values(archived_at=sa.func.now())
                )
            return old_id

        monkeypatch.setattr(
            estimate_import_module, "_replace_existing", replace_and_archive_emptied_defaults
        )

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job, replace=True)

        assert job.status == ImportJobStatus.error.value
        # Текст ошибки — доменная RoutingError о корзине без действующего
        # контекста по умолчанию (спека §2.4, fail-closed), а не безликий 500.
        assert "действующего контекста по умолчанию" in (job.error_text or ""), job.error_text

        job_env.db.expire_all()
        remaining_ids = set(job_env.db.execute(sa.select(Estimate.id)).scalars().all())
        # Смета НЕ заменена: удаление старой и вставка новой шли в ОДНОЙ
        # транзакции (§5 правило 3), и отказ маршрутизации откатил обе
        # половины разом — старая смета осталась НА МЕСТЕ, новой не появилось.
        assert remaining_ids == {old_estimate_id}


# ---------------------------------------------------------------------------
#  Удаление договора/раунда/тендера/участника: членства уходят каскадом,
#  контексты остаются действующими и пустыми (четыре входа)
# ---------------------------------------------------------------------------

class TestDeletionCascadeKeepsContextsLiveAndEmpty:
    def _scene(self, db, factories):
        return _make_routed_estimate(
            db, factories,
            [
                position(job_title="Раздел удаления", is_chapter=True, chapter_number="1", number="1"),
                position(
                    job_title=f"Работа удаления {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
                ),
            ],
        )

    def _assert_context_survives_empty(self, db, context_id):
        db.expire_all()
        ctx = db.get(CatalogContext, context_id)
        assert ctx is not None
        assert ctx.archived_at is None
        member_count = db.execute(
            sa.select(sa.func.count()).select_from(ContextMember).where(ContextMember.context_id == context_id)
        ).scalar_one()
        assert member_count == 0

    def test_deleting_the_contract_keeps_the_context_live_and_empty(self, db_session, factories):
        estimate = self._scene(db_session, factories)
        work = _work_row(db_session, estimate, None) or db_session.execute(
            sa.select(PositionItem).where(PositionItem.is_chapter.is_(False))
        ).scalar_one()
        member = _member(db_session, work.id)
        context_id = member.context_id
        contract_id = estimate.contract_id

        crud_contracts.delete_contract(db_session, contract_id)
        self._assert_context_survives_empty(db_session, context_id)

    def test_deleting_the_round_keeps_the_context_live_and_empty(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender)
        db_session.flush()
        contract = factories.ContractFactory.create()
        db_session.flush()
        rows = [
            position(job_title="Раздел раунда-удал", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=f"Работа раунда-удал {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        outcome = import_estimate(
            db_session, owner=contract_estimate_owner(contract, None), data=payload_for(contract, rows),
            parser_version="1.0.0", import_job_id=None, replace=False,
            unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
        )
        est = db_session.get(Estimate, outcome.estimate_id)
        est.contract_id = None
        est.round_id = rnd.id
        db_session.flush()
        match_positions(db_session, outcome.positions_to_match)
        db_session.flush()
        route_positions(db_session, estimate_ids=[est.id])

        work = db_session.execute(
            sa.select(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == est.id, PositionItem.is_chapter.is_(False))
        ).scalar_one()
        member = _member(db_session, work.id)
        context_id = member.context_id

        crud_tenders.delete_round(db_session, tender.id, rnd.id)
        self._assert_context_survives_empty(db_session, context_id)

    def test_deleting_the_tender_keeps_the_context_live_and_empty(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender)
        db_session.flush()
        contract = factories.ContractFactory.create()
        db_session.flush()
        rows = [
            position(job_title="Раздел тендер-удал", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=f"Работа тендер-удал {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        outcome = import_estimate(
            db_session, owner=contract_estimate_owner(contract, None), data=payload_for(contract, rows),
            parser_version="1.0.0", import_job_id=None, replace=False,
            unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
        )
        est = db_session.get(Estimate, outcome.estimate_id)
        est.contract_id = None
        est.round_id = rnd.id
        db_session.flush()
        match_positions(db_session, outcome.positions_to_match)
        db_session.flush()
        route_positions(db_session, estimate_ids=[est.id])

        work = db_session.execute(
            sa.select(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == est.id, PositionItem.is_chapter.is_(False))
        ).scalar_one()
        member = _member(db_session, work.id)
        context_id = member.context_id

        crud_tenders.delete_tender(db_session, tender.id)
        self._assert_context_survives_empty(db_session, context_id)

    def test_deleting_the_participant_keeps_the_context_live_and_empty(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender)
        package = factories.OfferPackageFactory.create(tender=tender)
        offer = factories.OfferFactory.create(round=rnd, package=package, tender_id=tender.id)
        db_session.flush()
        contract = factories.ContractFactory.create()
        db_session.flush()
        rows = [
            position(job_title="Раздел участник-удал", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title=f"Работа участник-удал {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        outcome = import_estimate(
            db_session, owner=contract_estimate_owner(contract, None), data=payload_for(contract, rows),
            parser_version="1.0.0", import_job_id=None, replace=False,
            unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
        )
        est = db_session.get(Estimate, outcome.estimate_id)
        est.contract_id = None
        est.offer_id = offer.id
        db_session.flush()
        match_positions(db_session, outcome.positions_to_match)
        db_session.flush()
        route_positions(db_session, estimate_ids=[est.id])

        work = db_session.execute(
            sa.select(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == est.id, PositionItem.is_chapter.is_(False))
        ).scalar_one()
        member = _member(db_session, work.id)
        context_id = member.context_id

        token = crud_tenders.participant_deletion_preview(db_session, tender.id, package.id)["confirmation_token"]
        crud_tenders.delete_participant(db_session, tender.id, package.id, confirmation_token=token)
        self._assert_context_survives_empty(db_session, context_id)


# ---------------------------------------------------------------------------
#  Повторная загрузка того же файла — тот же контекст с тем же id
# ---------------------------------------------------------------------------

class TestReuploadSameFileReusesTheSameContext:
    def test_reimporting_the_same_position_keeps_the_context_id(self, db_session, factories):
        rows = [
            position(job_title="Раздел повтора", is_chapter=True, chapter_number="1", number="1"),
            position(
                job_title="Работа повтора", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="100.00", total_cost_total="100.00", chapter_ref="1", number="2",
            ),
        ]
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, rows)

        outcome1 = import_estimate(
            db_session, owner=contract_estimate_owner(contract, None), data=data,
            parser_version="1.0.0", import_job_id=None, replace=False,
            unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
        )
        match_positions(db_session, outcome1.positions_to_match)
        db_session.flush()
        route_positions(db_session, estimate_ids=[outcome1.estimate_id])

        estimate1 = db_session.get(Estimate, outcome1.estimate_id)
        work1 = _work_row(db_session, estimate1, "Работа повтора")
        context_id_1 = _member(db_session, work1.id).context_id

        # Вторая независимая смета (другой договор) с ТЕМ ЖЕ написанием и
        # разделом — «повторная загрузка того же файла» на уровне корзины:
        # то же написание × та же (отсутствующая) статья, тот же путь по
        # умолчанию.
        contract2 = factories.ContractFactory.create()
        db_session.flush()
        data2 = payload_for(contract2, rows)
        outcome2 = import_estimate(
            db_session, owner=contract_estimate_owner(contract2, None), data=data2,
            parser_version="1.0.0", import_job_id=None, replace=False,
            unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
        )
        match_positions(db_session, outcome2.positions_to_match)
        db_session.flush()
        route_positions(db_session, estimate_ids=[outcome2.estimate_id])

        estimate2 = db_session.get(Estimate, outcome2.estimate_id)
        work2 = _work_row(db_session, estimate2, "Работа повтора")
        context_id_2 = _member(db_session, work2.id).context_id

        assert context_id_2 == context_id_1


# ---------------------------------------------------------------------------
#  Фикстура `manual_overrides_18.json` — контракт белого списка
# ---------------------------------------------------------------------------

REQUIRED_KEYS_18 = frozenset({"chapter_title", "category_code"})
ALLOWED_KEYS_18 = REQUIRED_KEYS_18  # опциональных ключей нет


def _fixture_row_18_is_valid(row: dict) -> bool:
    keys = set(row.keys())
    return REQUIRED_KEYS_18.issubset(keys) and keys.issubset(ALLOWED_KEYS_18)


class TestManualOverrides18FixtureContract:
    def test_fixture_has_eighteen_records(self):
        assert len(MANUAL_OVERRIDES_18) == 18

    @pytest.mark.parametrize(
        "record", MANUAL_OVERRIDES_18, ids=[f"{r['chapter_title']}-{i}" for i, r in enumerate(MANUAL_OVERRIDES_18)]
    )
    def test_record_matches_whitelist_both_directions(self, record):
        assert _fixture_row_18_is_valid(record), (
            f"запись {record!r} не проходит контракт белого списка "
            f"(обязательные {sorted(REQUIRED_KEYS_18)}, допустимые {sorted(ALLOWED_KEYS_18)})"
        )

    def test_whitelist_rejects_an_unknown_key(self):
        mutated = {**MANUAL_OVERRIDES_18[0], "work_category_id": "343"}
        assert not _fixture_row_18_is_valid(mutated)

    def test_whitelist_rejects_a_missing_required_key(self):
        mutated = dict(MANUAL_OVERRIDES_18[0])
        del mutated["category_code"]
        assert not _fixture_row_18_is_valid(mutated)

    def test_every_category_code_resolves_in_the_test_catalog(self, db_session):
        for row in MANUAL_OVERRIDES_18:
            code = row["category_code"]
            found = db_session.execute(
                sa.select(WorkCategory.id).where(WorkCategory.code == code)
            ).scalar_one_or_none()
            assert found is not None, f"код статьи {code!r} не найден в тестовом классификаторе"


class TestManualOverrides18Scenario:
    def test_eighteen_manual_category_overrides_land_positions_in_target_category_buckets(
        self, db_session, factories, admin_user
    ):
        rows = []
        for i, record in enumerate(MANUAL_OVERRIDES_18, start=1):
            rows.append(
                position(
                    job_title=record["chapter_title"], is_chapter=True, chapter_number=str(i),
                    number=str(2 * i - 1),
                )
            )
            rows.append(
                position(
                    job_title=f"Работа {i} {_uid()}", unit="м2", quantity=1, suggested_quantity=1,
                    unit_cost_total="100.00", total_cost_total="100.00", chapter_ref=str(i),
                    number=str(2 * i),
                )
            )
        estimate = _make_routed_estimate(db_session, factories, rows)

        for i, record in enumerate(MANUAL_OVERRIDES_18, start=1):
            chapter = _chapter_row(db_session, estimate, str(i))
            work = db_session.execute(
                sa.select(PositionItem).where(PositionItem.chapter_item_id == chapter.id)
            ).scalar_one()
            category_id = _category_by_code(db_session, record["category_code"])

            set_override(
                db_session, estimate_id=estimate.id, position_item_id=chapter.id,
                work_category_id=category_id, note=None, user_id=admin_user.id,
            )
            db_session.flush()
            db_session.expire_all()

            db_session.refresh(chapter)
            assert chapter.work_category_id == category_id
            assert chapter.category_source == "manual"

            proposal = transfer_proposal(db_session, position_item_id=work.id)
            assert proposal is not None
            assert proposal.effective_category_id == category_id

            accept_transfer(
                db_session, position_item_id=work.id, expected_category_id=category_id,
                actor_id=admin_user.id,
            )
            db_session.flush()
            db_session.expire_all()

            final_member = _member(db_session, work.id)
            assert final_member.membership_state == MembershipState.CURRENT.value
            final_bucket = _bucket_of(db_session, final_member)
            assert final_bucket.work_category_id == category_id, (
                f"позиция {i} обязана лежать в корзине ЦЕЛЕВОЙ статьи {category_id}"
            )

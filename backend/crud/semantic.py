"""Чтение для экрана «Семьи и контексты» (спека
`2026-09-22-catalog-families-design.md` §2.10; план, задача 12).

HTTP-слой поверх сервисов задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) — этот модуль
сам ничего не мутирует, только строит выдачу для экрана.

`list_contexts` обязан читать ограниченным числом запросов, НЕЗАВИСИМЫМ от
числа строк выдачи (план, задача 12, «Утверждения»): выбираются только
СКАЛЯРНЫЕ колонки через явные `join`/`outerjoin`, ни одна лениво подгружаемая
ORM-связь не читается в цикле по строкам — иначе число запросов росло бы
вместе с числом строк (N+1). Ровно два запроса — счётчик и страница.

Три фильтра-признака (`has_stale_members`, `has_conflicting_members`,
`has_no_members`) — ТРИ РАЗНЫХ `EXISTS`-подзапроса, а не один общий
(план, задача 12, «Утверждения»): оси независимы, и вход с одновременно
`STALE`-членством и `conflict_at` обязан пройти оба первых фильтра сразу.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    Lot,
    MembershipState,
    PositionItem,
    Proposal,
    SemanticEvent,
    UnitOfMeasure,
    WorkCategory,
    WorkFamily,
)

#: Потолок числа членств, отдаваемых карточкой контекста поштучно — экран не
#: может проверять/предлагать действия по членству, не видя его id, а
#: `member_count` был только агрегатом. Отдельная константа, а не литерал в
#: запросе: тест переопределяет её монки-патчем, чтобы проверить
#: `members_truncated` не заводя 500+ строк в БД.
CONTEXT_MEMBERS_PAGE_CAP = 500


@dataclass(frozen=True)
class ContextFilters:
    """Фильтры очереди контекстов (план, задача 12, `crud/semantic.py`).

    Все поля — `None` значит «фильтр не применён». Три булевых
    фильтра-признака — независимые оси (см. докстринг модуля).
    """

    catalog_query: str | None = None
    work_category_id: int | None = None
    semantic_kind: str | None = None
    name_role: str | None = None
    semantic_state: str | None = None
    has_stale_members: bool | None = None
    has_conflicting_members: bool | None = None
    has_no_members: bool | None = None


# ---------------------------------------------------------------------------
#  Семьи
# ---------------------------------------------------------------------------

def list_families(db: Session, *, status: str | None, unit_id: int | None) -> list[dict]:
    """Список семей с фильтром по статусу/единице и числом привязанных
    контекстов у КАЖДОЙ (спека §2.10; план, задача 12, «Утверждения») — то
    самое число, на которое ссылаются отказы правки единицы и
    архивирования (`REFUSE_UNIT_CHANGE_WITH_LINKS`/`REFUSE_ARCHIVE_WITH_LINKS`,
    `services/work_families.py`)."""
    stmt = (
        sa.select(
            WorkFamily.id,
            WorkFamily.title,
            WorkFamily.unit_id,
            UnitOfMeasure.code.label("unit_code"),
            WorkFamily.definition,
            WorkFamily.status,
            WorkFamily.seed_key,
            WorkFamily.created_by,
            WorkFamily.created_at,
            WorkFamily.updated_at,
            WorkFamily.activated_by,
            WorkFamily.activated_at,
            WorkFamily.archived_at,
            sa.func.count(CatalogContext.id).label("context_count"),
        )
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == WorkFamily.unit_id)
        .outerjoin(CatalogContext, CatalogContext.work_family_id == WorkFamily.id)
        .group_by(WorkFamily.id, UnitOfMeasure.code)
        .order_by(WorkFamily.id)
    )
    if status is not None:
        stmt = stmt.where(WorkFamily.status == status)
    if unit_id is not None:
        stmt = stmt.where(WorkFamily.unit_id == unit_id)

    rows = db.execute(stmt).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "unit_id": row.unit_id,
            "unit_code": row.unit_code,
            "definition": row.definition,
            "status": row.status,
            "seed_key": row.seed_key,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "activated_by": row.activated_by,
            "activated_at": row.activated_at,
            "archived_at": row.archived_at,
            "context_count": row.context_count,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
#  Контексты — очередь
# ---------------------------------------------------------------------------

def _stale_exists():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(
            ContextMember.context_id == CatalogContext.id,
            ContextMember.membership_state == MembershipState.STALE.value,
        )
        .exists()
    )


def _conflict_exists():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(
            ContextMember.context_id == CatalogContext.id,
            ContextMember.conflict_at.isnot(None),
        )
        .exists()
    )


def _members_exist():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(ContextMember.context_id == CatalogContext.id)
        .exists()
    )


def _escape_ilike(text: str) -> str:
    """Экранирует `%`, `_` и сам экранирующий символ `\\` перед подстановкой
    в `ILIKE`: без этого `catalog_query`, содержащий
    буквальный `%` или `_` (в названии работы такое бывает — «40% раствор»,
    «шпонка_A»), трактуется как метасимвол шаблона, а не как искомый текст.
    Порядок замен важен: сам `\\` экранируется ПЕРВЫМ, иначе экранирующие
    слэши, вставленные для `%`/`_`, экранировались бы повторно."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _context_query(filters: ContextFilters):
    """Один запрос-строитель: только скаляры через `join`/`outerjoin`, ни
    одной ленивой ORM-связи — см. докстринг модуля про N+1."""
    stmt = (
        sa.select(
            CatalogContext.id,
            CatalogContext.bucket_id,
            CatalogContext.is_default,
            CatalogContext.semantic_kind,
            CatalogContext.semantic_kind_source,
            CatalogContext.name_role,
            CatalogContext.name_role_source,
            CatalogContext.semantic_state,
            CatalogContext.comparability_reason,
            CatalogContext.work_family_id,
            WorkFamily.title.label("family_title"),
            ContextBucket.work_category_id,
            WorkCategory.code.label("work_category_code"),
            WorkCategory.title.label("work_category_title"),
            CatalogPosition.id.label("catalog_position_id"),
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code.label("unit_code"),
            CatalogContext.archived_at,
        )
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .outerjoin(WorkCategory, WorkCategory.id == ContextBucket.work_category_id)
        .outerjoin(WorkFamily, WorkFamily.id == CatalogContext.work_family_id)
    )
    if filters.catalog_query:
        stmt = stmt.where(
            CatalogPosition.standard_job_title.ilike(
                f"%{_escape_ilike(filters.catalog_query)}%", escape="\\"
            )
        )
    if filters.work_category_id is not None:
        stmt = stmt.where(ContextBucket.work_category_id == filters.work_category_id)
    if filters.semantic_kind is not None:
        stmt = stmt.where(CatalogContext.semantic_kind == filters.semantic_kind)
    if filters.name_role is not None:
        stmt = stmt.where(CatalogContext.name_role == filters.name_role)
    if filters.semantic_state is not None:
        stmt = stmt.where(CatalogContext.semantic_state == filters.semantic_state)

    # Три независимые оси (см. докстринг модуля) — `True`/`False` фильтруют
    # обе стороны, `None` фильтр не применяет вовсе.
    if filters.has_stale_members is True:
        stmt = stmt.where(_stale_exists())
    elif filters.has_stale_members is False:
        stmt = stmt.where(~_stale_exists())
    if filters.has_conflicting_members is True:
        stmt = stmt.where(_conflict_exists())
    elif filters.has_conflicting_members is False:
        stmt = stmt.where(~_conflict_exists())
    if filters.has_no_members is True:
        stmt = stmt.where(~_members_exist())
    elif filters.has_no_members is False:
        stmt = stmt.where(_members_exist())

    return stmt


def list_contexts(db: Session, *, filters: ContextFilters, limit: int, offset: int) -> dict:
    """Очередь контекстов — РОВНО ДВА запроса, независимо от `limit`/числа
    найденных строк (план, задача 12, «Утверждения»): счётчик и страница."""
    stmt = _context_query(filters)
    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(stmt.order_by(CatalogContext.id).offset(offset).limit(limit)).all()
    items = [
        {
            "id": row.id,
            "bucket_id": row.bucket_id,
            "is_default": row.is_default,
            "semantic_kind": row.semantic_kind,
            "semantic_kind_source": row.semantic_kind_source,
            "name_role": row.name_role,
            "name_role_source": row.name_role_source,
            "semantic_state": row.semantic_state,
            "comparability_reason": row.comparability_reason,
            "work_family_id": row.work_family_id,
            "family_title": row.family_title,
            "work_category_id": row.work_category_id,
            "work_category_code": row.work_category_code,
            "work_category_title": row.work_category_title,
            "catalog_position_id": row.catalog_position_id,
            "standard_job_title": row.standard_job_title,
            "unit_code": row.unit_code,
            "archived_at": row.archived_at,
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
#  Контексты — карточка
# ---------------------------------------------------------------------------

def context_card(db: Session, *, context_id: int) -> dict | None:
    """Карточка контекста (спека §2.10): написание (нормализованное имя +
    единица), статья и её источник (`file`/`manual` — источник разноса
    строки-раздела, читается у ЛЮБОГО одного членства контекста: внутри
    одной корзины написание и эффективная статья одинаковы по построению,
    `services/context_routing.py`), вид с источником, роль имени с
    источником и версией словаря, `comparability_reason`, семья с
    источником назначения, число членств (`member_count`, всегда полное),
    соседей по корзине (`bucket_contexts` — id, `is_default`, `archived_at`,
    `member_count` КАЖДОГО контекста той же `bucket_id`, включая архивные;
    цель слияния/переноса выбирается из живых соседей), членства поштучно
    (`members` — id позиции, название работы по смете, id сметы, состояние
    членства, конфликт и его источник, `routed_by`; ограничено
    `CONTEXT_MEMBERS_PAGE_CAP`, обрезка отмечена `members_truncated`) и
    журнал событий (по времени).

    `None`, если контекст не найден — роутер переводит это в 404.
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        return None

    bucket = db.get(ContextBucket, context.bucket_id)
    catalog_position = db.get(CatalogPosition, bucket.catalog_position_id)
    unit_code = catalog_position.unit.code if catalog_position.unit_id is not None else None

    work_category = (
        db.get(WorkCategory, bucket.work_category_id)
        if bucket.work_category_id is not None
        else None
    )

    family = db.get(WorkFamily, context.work_family_id) if context.work_family_id is not None else None

    # `ORDER BY position_item_id` — детерминированный выбор членства:
    # без порядка `LIMIT 1` возвращает произвольную
    # строку, и при разных источниках статьи (file/manual) у членств одной
    # корзины ответ был бы недетерминирован. Младший `position_item_id` —
    # самый простой воспроизводимый порядок; спека не
    # называет иного критерия «какое членство представляет корзину».
    chapter = aliased(PositionItem)
    work_category_source = db.execute(
        sa.select(chapter.category_source)
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .where(ContextMember.context_id == context_id)
        .order_by(ContextMember.position_item_id)
        .limit(1)
    ).scalar_one_or_none()

    member_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextMember)
        .where(ContextMember.context_id == context_id)
    ).scalar_one()

    # Соседи по корзине (та же `bucket_id`) — цель для слияния/переноса
    # (спека §2.4: разделение оставляет несколько контекстов на одной
    # корзине, слияние допустимо только внутри неё). Живые И архивные — оба
    # нужны экрану: архивные не предлагаются целью, но названы должны быть
    # видимо ПОЧЕМУ их нет в выборе, а не молчаливым отсутствием. ОДИН
    # агрегирующий запрос (`GROUP BY`, `outerjoin` на членства) — не растёт
    # с числом соседей, тот же приём, что у списка членств выше.
    bucket_context_rows = db.execute(
        sa.select(
            CatalogContext.id,
            CatalogContext.is_default,
            CatalogContext.archived_at,
            sa.func.count(ContextMember.position_item_id).label("member_count"),
        )
        .select_from(CatalogContext)
        .outerjoin(ContextMember, ContextMember.context_id == CatalogContext.id)
        .where(CatalogContext.bucket_id == context.bucket_id)
        .group_by(CatalogContext.id)
        .order_by(CatalogContext.id)
    ).all()

    # Членства поштучно: экран решает, какое действие предложить
    # КОНКРЕТНОМУ членству (перенос устаревшего,
    # решение цели конфликтного), и без id и состояния по каждому это
    # неисполнимо — `member_count` выше это только агрегат. ОДИН ограниченный
    # запрос (`LIMIT CAP + 1`, не завязанный на число строк выдачи) —
    # не N+1: число запросов не растёт вместе с числом членств, а `+1`
    # к лимиту отличает «ровно потолок» от «больше потолка» без отдельного
    # count-запроса. `member_count` остаётся ПОЛНЫМ (запрос выше), а не
    # длиной этого списка — список может быть обрезан, счётчик всегда точен.
    member_rows = db.execute(
        sa.select(
            ContextMember.position_item_id,
            PositionItem.job_title_in_proposal.label("job_title"),
            Lot.estimate_id,
            ContextMember.membership_state,
            ContextMember.conflict_at,
            ContextMember.conflict_from_context_id,
            ContextMember.routed_by,
        )
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(ContextMember.context_id == context_id)
        .order_by(ContextMember.position_item_id)
        .limit(CONTEXT_MEMBERS_PAGE_CAP + 1)
    ).all()
    members_truncated = len(member_rows) > CONTEXT_MEMBERS_PAGE_CAP
    member_rows = member_rows[:CONTEXT_MEMBERS_PAGE_CAP]

    events = (
        db.execute(
            sa.select(SemanticEvent)
            .where(SemanticEvent.context_id == context_id)
            .order_by(SemanticEvent.created_at, SemanticEvent.id)
        )
        .scalars()
        .all()
    )

    return {
        "id": context.id,
        "bucket_id": context.bucket_id,
        "is_default": context.is_default,
        "archived_at": context.archived_at,
        "catalog_position_id": catalog_position.id,
        "standard_job_title": catalog_position.standard_job_title,
        "unit_id": catalog_position.unit_id,
        "unit_code": unit_code,
        "work_category_id": bucket.work_category_id,
        "work_category_code": work_category.code if work_category is not None else None,
        "work_category_title": work_category.title if work_category is not None else None,
        "work_category_source": work_category_source,
        "semantic_kind": context.semantic_kind,
        "semantic_kind_source": context.semantic_kind_source,
        "semantic_kind_by": context.semantic_kind_by,
        "semantic_kind_at": context.semantic_kind_at,
        "name_role": context.name_role,
        "name_role_source": context.name_role_source,
        "name_role_by": context.name_role_by,
        "name_role_at": context.name_role_at,
        "place_dictionary_version": context.place_dictionary_version,
        "comparability_reason": context.comparability_reason,
        "semantic_state": context.semantic_state,
        "work_family_id": context.work_family_id,
        "family_title": family.title if family is not None else None,
        "family_source": context.family_source,
        "family_by": context.family_by,
        "family_at": context.family_at,
        "member_count": member_count,
        "bucket_contexts": [
            {
                "id": row.id,
                "is_default": row.is_default,
                "archived_at": row.archived_at,
                "member_count": row.member_count,
            }
            for row in bucket_context_rows
        ],
        "members": [
            {
                "position_item_id": row.position_item_id,
                "job_title": row.job_title,
                "estimate_id": row.estimate_id,
                "membership_state": row.membership_state,
                "conflict_at": row.conflict_at,
                "conflict_from_context_id": row.conflict_from_context_id,
                "routed_by": row.routed_by,
            }
            for row in member_rows
        ],
        "members_truncated": members_truncated,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload,
                "actor_id": event.actor_id,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }

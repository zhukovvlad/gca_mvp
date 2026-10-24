"""Загрузчики экрана «КП против нашей цены» (спека 4а §2.11, §2.17).

Модуль только читает: ни одного `INSERT`/`UPDATE`/`DELETE`. Расчёт — в чистых
слоях `services/our_price.py` и `services/our_price_money.py`; здесь только SQL
и свёртка прочитанного в плоские значения.

**Сигнал «каталог ещё разбирает»** (`catalog_signal`) считает два флага для
каждой строки сметы одним фиксированным числом запросов, не зависящим от числа
строк: строки со связями, задания очереди, удержанные пачки, состояние захвата.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    Lot,
    PositionItem,
    Proposal,
    ReconcileBatchStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SemanticWorkerState,
)

#: Задание разбирается прямо сейчас (спека 4а §2.11).
_LIVE_STATUSES = (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
#: Задание остановилось и ждёт человека: не «разбирает», а «ждёт администратора».
_STUCK_STATUSES = (SemanticJobStatus.error.value, SemanticJobStatus.privacy_hold.value)

_CONTEXT_KINDS = (
    SemanticJobKind.family_suggestion.value,
    SemanticJobKind.context_values.value,
)


@dataclass(frozen=True)
class CatalogSignal:
    """Два независимых флага строки КП: каталог ещё разбирает её / строка ждёт
    решения администратора. Подпись по сочетанию флагов и состояния работы
    наносит чистое ядро."""

    in_progress: bool
    waiting_admin: bool


@dataclass(frozen=True)
class _Row:
    position_item_id: int
    context_id: int | None
    family_id: int | None
    unit_id: int | None


def _rows_of(db: Session, estimate_ids: Sequence[int]) -> list[_Row]:
    """Все не-главы смет с контекстом, семьёй контекста и единицей каталожной
    строки его корзины (`NULL` у строки без членства)."""
    stmt = (
        sa.select(
            PositionItem.id,
            ContextMember.context_id,
            CatalogContext.work_family_id,
            CatalogPosition.unit_id,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .outerjoin(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)), PositionItem.is_chapter.is_(False))
    )
    return [_Row(*r) for r in db.execute(stmt).all()]


def _jobs_by_subject(
    db: Session, rows: Sequence[_Row]
) -> tuple[dict[int, set[str]], dict[int, set[str]], dict[int | None, set[str]]]:
    """Статусы заданий по предмету: контекст, семья (`family_schema`), единица
    (`family_discovery`). Единица без значения (`None`) — свой предмет."""
    context_ids = {r.context_id for r in rows if r.context_id is not None}
    family_ids = {r.family_id for r in rows if r.family_id is not None}
    unit_ids = {r.unit_id for r in rows if r.context_id is not None and r.family_id is None}
    unit_clause = SemanticJob.unit_id.in_([u for u in unit_ids if u is not None])
    if None in unit_ids:
        unit_clause = sa.or_(unit_clause, SemanticJob.unit_id.is_(None))
    stmt = sa.select(
        SemanticJob.kind,
        SemanticJob.status,
        SemanticJob.context_id,
        SemanticJob.family_id,
        SemanticJob.unit_id,
    ).where(
        SemanticJob.status.in_(_LIVE_STATUSES + _STUCK_STATUSES),
        sa.or_(
            sa.and_(
                SemanticJob.kind.in_(_CONTEXT_KINDS),
                SemanticJob.context_id.in_(list(context_ids)),
            ),
            sa.and_(
                SemanticJob.kind == SemanticJobKind.family_schema.value,
                SemanticJob.family_id.in_(list(family_ids)),
            ),
            sa.and_(SemanticJob.kind == SemanticJobKind.family_discovery.value, unit_clause),
        ),
    )
    by_context: dict[int, set[str]] = defaultdict(set)
    by_family: dict[int, set[str]] = defaultdict(set)
    by_unit: dict[int | None, set[str]] = defaultdict(set)
    for kind, status, context_id, family_id, unit_id in db.execute(stmt).all():
        if kind in _CONTEXT_KINDS:
            by_context[context_id].add(status)
        elif kind == SemanticJobKind.family_schema.value:
            by_family[family_id].add(status)
        else:
            by_unit[unit_id].add(status)
    return by_context, by_family, by_unit


def _held_subjects(db: Session) -> tuple[set[int], set[int]]:
    """Контексты и семьи, названные в отпечатках удержанных пачек. Элемент —
    объект с ключами `kind`, `context_id`, `family_id`, `schema_id`,
    `request_hash` (отсутствующий предмет — `null`)."""
    held_contexts: set[int] = set()
    held_families: set[int] = set()
    batches = db.execute(
        sa.select(SemanticReconcileBatch.held_fingerprints).where(
            SemanticReconcileBatch.status == ReconcileBatchStatus.held.value
        )
    ).scalars()
    for held in batches:
        for element in held:
            if not isinstance(element, dict):
                continue
            if element.get("context_id") is not None:
                held_contexts.add(int(element["context_id"]))
            if element.get("family_id") is not None:
                held_families.add(int(element["family_id"]))
    return held_contexts, held_families


def _claim_paused(db: Session) -> bool:
    """Захват остановлен; строки состояния может не быть — тогда нет."""
    paused = db.execute(sa.select(SemanticWorkerState.claim_paused)).scalar_one_or_none()
    return bool(paused)


def catalog_signal(db: Session, estimate_ids: Sequence[int]) -> dict[int, CatalogSignal]:
    """`position_item_id` → сигнал для каждой строки (не главы) заданных смет.

    `in_progress` — у контекста строки `family_suggestion`/`context_values`, у
    семьи контекста `family_schema`, а при контексте без семьи — `family_discovery`
    по единице каталожной строки в `pending`/`running`. `waiting_admin` — те же
    три правила для заданий в `error`/`privacy_hold`, удержанная пачка с
    контекстом или семьёй строки в отпечатках, либо остановленный захват при
    живом задании строки (тогда `in_progress` гаснет: плашка не должна висеть
    без конца). Строка без членства в контексте — оба флага ложны. Флаги не
    смотрят на вариант контекста.
    """
    if not estimate_ids:
        return {}
    rows = _rows_of(db, estimate_ids)
    if not rows:
        return {}
    by_context, by_family, by_unit = _jobs_by_subject(db, rows)
    held_contexts, held_families = _held_subjects(db)
    paused = _claim_paused(db)

    signals: dict[int, CatalogSignal] = {}
    for row in rows:
        if row.context_id is None:
            signals[row.position_item_id] = CatalogSignal(False, False)
            continue
        statuses = set(by_context.get(row.context_id, ()))
        if row.family_id is not None:
            statuses |= by_family.get(row.family_id, set())
        else:
            statuses |= by_unit.get(row.unit_id, set())
        live = bool(statuses & set(_LIVE_STATUSES))
        stuck = bool(statuses & set(_STUCK_STATUSES))
        held = row.context_id in held_contexts or row.family_id in held_families
        signals[row.position_item_id] = CatalogSignal(
            in_progress=live and not paused,
            waiting_admin=stuck or held or (live and paused),
        )
    return signals

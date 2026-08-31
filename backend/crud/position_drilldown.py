"""Разложение статьи: чтение входов постоянным числом запросов (спека
2026-08-30-position-drilldown-design.md §2.1, §2.2, §2.7, §2.11).

Арифметика — services/position_drilldown.py; здесь запросы и сборка GroupInput.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, aliased

from models import (
    CatalogPosition,
    EstimateAdditionalWork,
    Lot,
    PositionItem,
    Proposal,
    UnitOfMeasure,
    WorkCategory,
)
from services import position_drilldown as pd

UNMATCHED_TITLE = "Строки без каталожной привязки"
_NOT_FINITE = (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"))


def _gross(amount: Decimal | None) -> Decimal:
    """`SUM` без единой конечной строки отдаёт `NULL` — ноль, а не отсутствие
    (группа с нулевой суммой ≠ отсутствующая группа). `amount or 0` здесь не
    подошёл бы: `Decimal("0")` тоже ложно, и такая проверка не отличима от
    настоящего NULL по написанию — только по значению самого `amount`."""
    return amount if amount is not None else Decimal(0)


def subtree_ids(db: Session, work_category_id: int) -> list[int]:
    """Статья и все потомки. Один запрос всего справочника: категорий сотни,
    и обход в Python дешевле рекурсивного SQL (тот же приём — subtree_ids
    генератора макета)."""
    children: dict[int | None, list[int]] = defaultdict(list)
    for cid, parent in db.execute(sa.select(WorkCategory.id, WorkCategory.parent_id)).all():
        children[parent].append(cid)
    out, stack = [], [work_category_id]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children[current])
    return out


def load_groups(db: Session, estimate_ids: Sequence[int], subtree: Sequence[int]) -> list[pd.GroupInput]:
    column_of = {e: i for i, e in enumerate(estimate_ids)}
    estimate_ids = list(estimate_ids)
    subtree = list(subtree)
    chapter = aliased(PositionItem)
    finite = sa.case((PositionItem.total_cost_total.in_(_NOT_FINITE), None),
                     else_=PositionItem.total_cost_total)
    position_rows = db.execute(
        sa.select(Lot.estimate_id, PositionItem.catalog_position_id,
                  sa.func.min(CatalogPosition.standard_job_title),
                  sa.func.sum(finite), sa.func.count(),
                  sa.func.array_agg(sa.distinct(PositionItem.suggested_quantity)),
                  sa.func.min(UnitOfMeasure.symbol))
        .select_from(PositionItem)
        .join(chapter, sa.and_(chapter.id == PositionItem.chapter_item_id,
                               chapter.proposal_id == PositionItem.proposal_id))
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .where(Lot.estimate_id.in_(estimate_ids), PositionItem.is_chapter.is_(False),
               chapter.work_category_id.in_(subtree))
        .group_by(Lot.estimate_id, PositionItem.catalog_position_id)
    ).all()

    groups: dict[object, dict] = {}
    for estimate_id, cat_id, title, amount, rows, quantities, unit in position_rows:
        # Ключ — ТОЛЬКО каталожная позиция, статья сюда сознательно не входит:
        # работа, переехавшая из родительской статьи в дочернюю, обязана
        # остаться ОДНОЙ группой (§2.2). Гранулярность SQL GROUP BY выше
        # (по estimate_id, catalog_position_id) этого решения не принимает —
        # идентичность группы решается здесь, в Python-ключе.
        key = pd.KIND_UNMATCHED if cat_id is None else cat_id
        group = groups.setdefault(key, {
            "kind": pd.KIND_UNMATCHED if cat_id is None else pd.KIND_POSITION,
            "catalog_position_id": cat_id, "chapter_ref_raw": None,
            "title": UNMATCHED_TITLE if cat_id is None else title,
            "key": (key,), "stages": {}})
        group["stages"][column_of[estimate_id]] = pd.GroupStage(
            gross=_gross(amount), rows=rows,
            quantities=tuple(sorted(q for q in quantities if q is not None)), unit=unit)

    extra_rows = db.execute(
        # Ключ группы — ЛОТ плюс ссылка: «3.2.2» в разных лотах законно означает
        # разные работы (§2.7). Подпись внутри этапа — первая строка по паре
        # (proposal_id, ordinal): ordinal уникален лишь внутри предложения.
        sa.select(Lot.estimate_id, Lot.lot_key, EstimateAdditionalWork.chapter_ref_raw,
                  sa.func.array_agg(aggregate_order_by(
                      EstimateAdditionalWork.title,
                      EstimateAdditionalWork.proposal_id,
                      EstimateAdditionalWork.ordinal))[1],
                  sa.func.sum(EstimateAdditionalWork.total_amount), sa.func.count())
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids),
               EstimateAdditionalWork.work_category_id.in_(subtree))
        .group_by(Lot.estimate_id, Lot.lot_key, EstimateAdditionalWork.chapter_ref_raw)
        .order_by(Lot.estimate_id, Lot.lot_key, EstimateAdditionalWork.chapter_ref_raw)
    ).all()
    ordered = sorted(extra_rows, key=lambda r: column_of[r[0]])
    for estimate_id, lot_key, ref, title, amount, rows in ordered:
        key = ("extra", lot_key, ref)   # лот в ключе; в ответ он идёт полями
                                        # row_key и lot_key (§2.11)
        group = groups.setdefault(key, {"kind": pd.KIND_ADDITIONAL_WORKS,
                                        "catalog_position_id": None, "chapter_ref_raw": ref,
                                        "title": title, "key": (lot_key, ref), "stages": {}})
        group["title"] = title    # подпись с ПОСЛЕДНЕГО этапа присутствия (§2.7)
        group["stages"][column_of[estimate_id]] = pd.GroupStage(gross=_gross(amount), rows=rows)

    return [pd.GroupInput(kind=g["kind"], catalog_position_id=g["catalog_position_id"],
                          chapter_ref_raw=g["chapter_ref_raw"], title=g["title"],
                          stages=g["stages"], key=g["key"])
            for g in groups.values()]

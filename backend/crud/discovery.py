"""Чтение блока «Открыть семьи» (спека 3б §2.12): по единице — состав охвата,
оценка и последнее открытие. Пишущая часть — `services/family_discovery.py`;
числа строки считает та же `discovery_scope`, что тело запроса и запуск (один
предикат на блок, preview, тело и результат).
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from crud.semantic_queue import money_str
from models import (
    CatalogPosition,
    DraftGroup,
    DraftStatus,
    FamilyDraft,
    FamilyStatus,
    SemanticJob,
    SemanticJobKind,
    UnitOfMeasure,
    WorkFamily,
)
from services.family_discovery import _preview_of, _scope_query, discovery_scope


def _candidate_units(db: Session) -> set[int | None]:
    """Единицы, у которых есть что открывать: в охвате есть контекст либо есть
    активная семья без категории."""
    in_scope = set(db.execute(_scope_query(CatalogPosition.unit_id).distinct()).scalars())
    uncategorized = set(
        db.execute(
            sa.select(WorkFamily.unit_id)
            .where(
                WorkFamily.status == FamilyStatus.active.value,
                WorkFamily.family_category_id.is_(None),
            )
            .distinct()
        ).scalars()
    )
    return in_scope | uncategorized


def _last_discovery(db: Session, unit_id: int | None) -> dict | None:
    job = db.execute(
        sa.select(SemanticJob)
        .where(
            SemanticJob.kind == SemanticJobKind.family_discovery.value,
            SemanticJob.unit_id.is_not_distinct_from(unit_id),
        )
        .order_by(SemanticJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return None
    open_drafts = db.execute(
        sa.select(sa.func.count(FamilyDraft.id)).where(
            FamilyDraft.job_id == job.id,
            FamilyDraft.grp == DraftGroup.new.value,
            FamilyDraft.status == DraftStatus.open.value,
        )
    ).scalar_one()
    return {
        "job_id": job.id,
        "status": job.status,
        "at": job.updated_at.isoformat(),
        "open_drafts": open_drafts,
    }


def discovery_units(db: Session, *, settings: Settings) -> list[dict]:
    """Строка на каждую единицу с непустым охватом или с семьями без категории
    (`None` — единица «без единицы», своя строка). Числа — из `discovery_scope`
    той же единицы; оценка — резерв и ожидаемая цена при попадании в кэш;
    последнее открытие — статус, время и число открытых черновиков новых семей.
    Порядок: по коду единицы, «без единицы» последней."""
    units = _candidate_units(db)
    codes: dict[int | None, str | None] = {None: None}
    real = {unit for unit in units if unit is not None}
    if real:
        codes.update(
            db.execute(
                sa.select(UnitOfMeasure.id, UnitOfMeasure.code).where(UnitOfMeasure.id.in_(real))
            ).all()
        )
    rows: list[dict] = []
    for unit_id in units:
        scope = discovery_scope(db, unit_id)
        preview, _rendered = _preview_of(db, scope, settings)
        counts = scope.counts
        rows.append(
            {
                "unit_id": unit_id,
                "unit_code": codes.get(unit_id),
                "systems": counts.systems,
                "new_family": counts.new_family,
                "bare": counts.bare,
                "names": counts.names,
                "uncategorized_families": counts.uncategorized_families,
                "active_families": preview.active_families,
                "reserve_usd": money_str(preview.reserve_usd),
                "expected_cached_usd": money_str(preview.expected_cached_usd),
                "last_discovery": _last_discovery(db, unit_id),
            }
        )
    rows.sort(key=lambda r: (r["unit_code"] is None, r["unit_code"] or "", r["unit_id"] or 0))
    return rows

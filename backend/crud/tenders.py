"""Тендерный контур: тендеры, раунды, участники, решётка, удаления (спека
2026-08-26-tenders-contour-design.md §2.11–§2.13).

Иерархия локов — ОДНА для импорта и всех удалений: tender → rounds по id →
package (§2.11). Удаление раунда и upload берут тендер FOR KEY SHARE; удаление
тендера и участника — FOR UPDATE. `RESTRICT` от пакета требует явного
`DELETE offers` до пакета и до тендера.
"""
from __future__ import annotations

import hashlib
import json
import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session, joinedload

from crud.common import DomainError, clamp_page, iso, paginated, require_text, translating_integrity
from crud.estimate_totals import estimate_total_including_vat
from models import (
    TERMINAL_IMPORT_JOB_STATUSES,
    Contractor,
    Estimate,
    EstimateCategoryOverride,
    ImportJob,
    ImportJobStatus,
    Lot,
    ObjectModel,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    RateClass,
    Tender,
    TenderRound,
)

log = logging.getLogger(__name__)

#: Sentinel «поле не передано» — тот же приём, что в остальных crud-модулях
#: (`crud/references.py`, `crud/contracts.py`): отличает PATCH без поля от PATCH с null.
UNSET = object()

_TERMINAL = [s.value for s in TERMINAL_IMPORT_JOB_STATUSES]


def _dec(value) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

def get_tender(db: Session, tender_id: int) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")
    return tender


def get_round(db: Session, tender_id: int, round_id: int) -> TenderRound:
    """Раунд ищется ПАРОЙ (§2.13): чужой тендер в URL — 404, не чужие данные."""
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id)
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.")
    return rnd


def list_tenders(db: Session, *, q: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    page, page_size = clamp_page(page, page_size)
    rounds_count = sa.select(sa.func.count()).where(TenderRound.tender_id == Tender.id).scalar_subquery()
    participants_count = sa.select(sa.func.count()).where(OfferPackage.tender_id == Tender.id).scalar_subquery()
    stmt = (
        sa.select(Tender, ObjectModel.title, RateClass.title, rounds_count, participants_count)
        .join(ObjectModel, ObjectModel.id == Tender.object_id)
        .join(RateClass, RateClass.id == Tender.rate_class_id)
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(sa.or_(Tender.tender_number.ilike(pattern), Tender.title.ilike(pattern),
                                 ObjectModel.title.ilike(pattern)))
    rows, total = paginated(db, stmt, order_by=(Tender.created_at.desc(), Tender.id.desc()),
                            page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": t.id, "tender_number": t.tender_number, "title": t.title,
                "object_id": t.object_id, "object_title": object_title,
                "rate_class_id": t.rate_class_id, "rate_class_title": rate_class_title,
                "rounds_count": rounds, "participants_count": participants,
                "created_at": iso(t.created_at),
            }
            for t, object_title, rate_class_title, rounds, participants in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


def round_estimate_ids(db: Session, round_id: int) -> list[int]:
    """Все сметы раунда — offer-owned и baseline. Публичная: роутер загрузки
    решает по ней «есть ли что заменять» (задача 8)."""
    offer_ids = sa.select(Offer.id).where(Offer.round_id == round_id)
    return list(db.execute(
        sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offer_ids)))
    ).scalars())


def current_round_job(db: Session, round_id: int) -> ImportJob | None:
    """Текущий job раунда (§2.12): done, число смет раунда с этим job равно
    estimates_created, других смет у раунда нет. После удаления участника
    набор неполон — job перестаёт быть текущим, и тот же файл требует replace."""
    estimate_ids = round_estimate_ids(db, round_id)
    if not estimate_ids:
        return None
    rows = db.execute(
        sa.select(Estimate.import_job_id, sa.func.count()).where(Estimate.id.in_(estimate_ids))
        .group_by(Estimate.import_job_id)
    ).all()
    if len(rows) != 1:
        return None
    job_id, count = rows[0]
    if job_id is None:
        return None
    job = db.get(ImportJob, job_id)
    if job is None or job.status != ImportJobStatus.done.value or job.estimates_created != count:
        return None
    return job


def active_round_job(db: Session, round_id: int) -> ImportJob | None:
    return db.execute(
        sa.select(ImportJob).where(ImportJob.round_id == round_id, ImportJob.status.notin_(_TERMINAL))
        .order_by(ImportJob.id).limit(1)
    ).scalar_one_or_none()


def get_tender_card(db: Session, tender_id: int) -> dict:
    tender = db.execute(
        sa.select(Tender).options(joinedload(Tender.object), joinedload(Tender.rate_class))
        .where(Tender.id == tender_id)
    ).scalar_one_or_none()
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")

    rounds = db.execute(sa.select(TenderRound).where(TenderRound.tender_id == tender_id)
                        .order_by(TenderRound.stage_no)).scalars().all()
    packages = db.execute(
        sa.select(OfferPackage, Contractor).join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(OfferPackage.tender_id == tender_id).order_by(Contractor.title)
    ).all()
    offers = db.execute(
        sa.select(Offer, Estimate.id).outerjoin(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.tender_id == tender_id)
    ).all()
    offer_by_cell = {(o.round_id, o.package_id): (o.id, estimate_id) for o, estimate_id in offers}

    rounds_out = []
    for rnd in rounds:
        latest = db.execute(sa.select(ImportJob).where(ImportJob.round_id == rnd.id)
                            .order_by(ImportJob.id.desc()).limit(1)).scalar_one_or_none()
        current = current_round_job(db, rnd.id)
        baseline_id = db.execute(sa.select(Estimate.id).where(Estimate.round_id == rnd.id)).scalar_one_or_none()
        rounds_out.append({
            "id": rnd.id, "stage_no": rnd.stage_no, "label": rnd.label, "held_on": iso(rnd.held_on),
            "latest_job": _job_brief(latest) if latest else None,
            "current_job_id": current.id if current else None,
            "baseline_estimate_id": baseline_id,
            "baseline_total_including_vat": _dec(estimate_total_including_vat(db, baseline_id)) if baseline_id else None,
        })

    cells = []
    for rnd in rounds:
        for package, _ in packages:
            offer_id, estimate_id = offer_by_cell.get((rnd.id, package.id), (None, None))
            cells.append({
                "round_id": rnd.id, "package_id": package.id,
                "offer_id": offer_id, "estimate_id": estimate_id,
                "total_including_vat": _dec(estimate_total_including_vat(db, estimate_id)) if estimate_id else None,
            })

    return {
        "id": tender.id, "tender_number": tender.tender_number, "title": tender.title, "notes": tender.notes,
        "object_id": tender.object_id, "object_title": tender.object.title, "object_address": tender.object.address,
        "rate_class_id": tender.rate_class_id, "rate_class_title": tender.rate_class.title,
        "created_at": iso(tender.created_at),
        "rounds": rounds_out,
        "participants": [
            {"package_id": p.id, "contractor_id": c.id, "title": c.title, "inn": c.inn} for p, c in packages
        ],
        "cells": cells,
    }


def _job_brief(job: ImportJob) -> dict:
    return {"id": job.id, "status": job.status, "filename": job.filename,
            "finished_at": iso(job.finished_at), "created_at": iso(job.created_at)}


def list_round_import_jobs(db: Session, tender_id: int, round_id: int) -> list[dict]:
    get_round(db, tender_id, round_id)
    current = current_round_job(db, round_id)
    jobs = db.execute(sa.select(ImportJob).where(ImportJob.round_id == round_id)
                      .order_by(ImportJob.id.desc())).scalars().all()
    out = []
    for job in jobs:
        estimate_ids = list(db.execute(sa.select(Estimate.id).where(Estimate.import_job_id == job.id)
                                       .order_by(Estimate.id)).scalars())
        out.append({
            "id": job.id, "owner_type": "round", "tender_id": tender_id, "round_id": round_id,
            "filename": job.filename, "file_sha256": job.file_sha256, "status": job.status,
            "error_text": job.error_text, "warnings": job.warnings,
            "counters": {
                "positions_total": job.positions_total, "matched_cache": job.matched_cache,
                "matched_exact": job.matched_exact, "matched_nonposition": job.matched_nonposition,
                "to_review": job.to_review,
            },
            "estimate_ids": estimate_ids, "estimates_created": job.estimates_created,
            "is_current": current is not None and current.id == job.id,
            "created_at": iso(job.created_at), "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
        })
    return out


# ---------------------------------------------------------------------------
#  Создание и правка
# ---------------------------------------------------------------------------

def create_tender(db: Session, *, object_id: int, title: str, tender_number: str,
                  rate_class_id: int | None, notes: str | None) -> dict:
    obj = db.get(ObjectModel, object_id)
    if obj is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    snapshot_class = rate_class_id if rate_class_id is not None else obj.rate_class_id
    if snapshot_class is None:
        raise DomainError(422, "Класс объектов не задан ни у тендера, ни у объекта.")
    if db.get(RateClass, snapshot_class) is None:
        raise DomainError(404, f"Класс объектов {snapshot_class} не найден.")
    tender = Tender(
        object_id=object_id, title=require_text(title, "Предмет торга"),
        tender_number=require_text(tender_number, "Номер тендера"),
        rate_class_id=snapshot_class, notes=(notes or None),
    )
    db.add(tender)
    with translating_integrity(db, {"uq_tenders_tender_number": "Тендер с таким номером уже есть."}):
        db.commit()
    log.info("tender_created id=%s", tender.id)
    return get_tender_card(db, tender.id)


def update_tender(db: Session, tender_id: int, *, title=UNSET, notes=UNSET) -> dict:
    tender = get_tender(db, tender_id)
    if title is not UNSET:
        tender.title = require_text(title, "Предмет торга")
    if notes is not UNSET:
        tender.notes = notes or None
    db.commit()
    return get_tender_card(db, tender_id)


def create_round(db: Session, tender_id: int, *, stage_no: int, label: str | None, held_on) -> dict:
    get_tender(db, tender_id)
    if stage_no <= 0:
        raise DomainError(422, "Номер этапа нумеруется с 1.")
    rnd = TenderRound(tender_id=tender_id, stage_no=stage_no, label=label or None, held_on=held_on)
    db.add(rnd)
    with translating_integrity(db, {"uq_tender_rounds_tender_stage": "Этап с таким номером в этом тендере уже есть."}):
        db.commit()
    return get_tender_card(db, tender_id)


def update_round(db: Session, tender_id: int, round_id: int, *, label=UNSET, held_on=UNSET) -> dict:
    rnd = get_round(db, tender_id, round_id)
    if label is not UNSET:
        rnd.label = label or None
    if held_on is not UNSET:
        rnd.held_on = held_on
    db.commit()
    return get_tender_card(db, tender_id)


# ---------------------------------------------------------------------------
#  Удаления — команды с локами по иерархии tender → rounds → package
# ---------------------------------------------------------------------------

def _lock_tender(db: Session, tender_id: int, *, exclusive: bool) -> Tender:
    stmt = sa.select(Tender).where(Tender.id == tender_id).execution_options(populate_existing=True)
    stmt = stmt.with_for_update() if exclusive else stmt.with_for_update(key_share=True)
    tender = db.execute(stmt).scalar_one_or_none()
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")
    return tender


def _lock_rounds(db: Session, tender_id: int) -> list[int]:
    return list(db.execute(
        sa.select(TenderRound.id).where(TenderRound.tender_id == tender_id).order_by(TenderRound.id).with_for_update()
    ).scalars())


def _refuse_if_active(db: Session, round_ids: list[int]) -> None:
    if not round_ids:
        return
    active = db.execute(
        sa.select(ImportJob.id, ImportJob.status).where(ImportJob.round_id.in_(round_ids), ImportJob.status.notin_(_TERMINAL))
        .order_by(ImportJob.id).limit(1)
    ).first()
    if active is not None:
        raise DomainError(
            409, f"Импорт раунда выполняется (задание {active.id}, статус «{active.status}»). "
            "Дождитесь завершения и повторите.",
            code="active_import", context={"job_id": active.id},
        )


def delete_round(db: Session, tender_id: int, round_id: int) -> list[str]:
    """tender FOR KEY SHARE → round FOR UPDATE; каскад уносит сметы и jobs раунда."""
    _lock_tender(db, tender_id, exclusive=False)
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id).with_for_update()
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.")
    _refuse_if_active(db, [round_id])
    file_keys = list(db.execute(sa.select(ImportJob.file_key).where(ImportJob.round_id == round_id).order_by(ImportJob.id)).scalars())
    db.execute(sa.delete(TenderRound).where(TenderRound.id == round_id))
    db.commit()
    log.info("tender_round_deleted tender=%s round=%s jobs=%d", tender_id, round_id, len(file_keys))
    return file_keys


def delete_tender(db: Session, tender_id: int) -> list[str]:
    """tender FOR UPDATE → все раунды по id → явный DELETE offers → тендер (каскад)."""
    _lock_tender(db, tender_id, exclusive=True)
    round_ids = _lock_rounds(db, tender_id)
    _refuse_if_active(db, round_ids)
    file_keys = list(db.execute(sa.select(ImportJob.file_key).where(ImportJob.round_id.in_(round_ids)).order_by(ImportJob.id)).scalars()) if round_ids else []
    # НЕ load-bearing для fk_offers_package: ветка tender_rounds уносит все
    # offers каскадом (fk_offers_round ON DELETE CASCADE) раньше, чем ветка
    # offer_packages вообще может дойти до RESTRICT — без этой строки ни один
    # тест не краснеет. Она здесь, чтобы удаление тендера не зависело от того,
    # в каком порядке Postgres пойдёт по двум веткам каскада одного DELETE.
    db.execute(sa.delete(Offer).where(Offer.tender_id == tender_id))
    db.execute(sa.delete(Tender).where(Tender.id == tender_id))
    db.commit()
    log.info("tender_deleted id=%s jobs=%d", tender_id, len(file_keys))
    return file_keys


def _participant_composition(db: Session, package_id: int) -> dict:
    offer_ids = list(db.execute(sa.select(Offer.id).where(Offer.package_id == package_id).order_by(Offer.id)).scalars())
    estimate_ids = list(db.execute(sa.select(Estimate.id).where(Estimate.offer_id.in_(offer_ids)).order_by(Estimate.id)).scalars()) if offer_ids else []
    positions = db.execute(
        sa.select(sa.func.count()).select_from(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id.in_(estimate_ids))
    ).scalar_one() if estimate_ids else 0
    overrides = db.execute(
        sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).scalar_one() if estimate_ids else 0
    token = hashlib.sha256(json.dumps(
        {"offers": offer_ids, "estimates": estimate_ids, "positions": positions, "overrides": overrides},
        sort_keys=True,
    ).encode()).hexdigest()
    return {
        "rounds_count": len(offer_ids), "estimates_count": len(estimate_ids),
        "positions_count": positions, "overrides_count": overrides, "confirmation_token": token,
    }


def participant_deletion_preview(db: Session, tender_id: int, package_id: int) -> dict:
    get_tender(db, tender_id)
    package = db.execute(sa.select(OfferPackage).where(OfferPackage.id == package_id, OfferPackage.tender_id == tender_id)).scalar_one_or_none()
    if package is None:
        raise DomainError(404, f"Участник {package_id} тендера {tender_id} не найден.")
    return _participant_composition(db, package_id)


def delete_participant(db: Session, tender_id: int, package_id: int, *, confirmation_token: str | None) -> None:
    """«Удалить участника и его материализованные предложения; исходные файлы
    и результаты разбора сохраняются» (§2.11).

    tender FOR UPDATE → раунды по id → package FOR UPDATE → пересчёт состава →
    token совпал → явный DELETE offers → DELETE package. Несовпадение — 409 с
    ОБНОВЛЁННЫМ preview, ничего не удалено.
    """
    _lock_tender(db, tender_id, exclusive=True)
    round_ids = _lock_rounds(db, tender_id)
    package = db.execute(
        sa.select(OfferPackage).where(OfferPackage.id == package_id, OfferPackage.tender_id == tender_id).with_for_update()
    ).scalar_one_or_none()
    if package is None:
        raise DomainError(404, f"Участник {package_id} тендера {tender_id} не найден.")
    _refuse_if_active(db, round_ids)
    composition = _participant_composition(db, package_id)
    if confirmation_token != composition["confirmation_token"]:
        # Ничего не менялось выше этой строки — только SELECT/FOR UPDATE, писать
        # нечего, откатывать нечего. `db.rollback()` здесь был бы ЛИШНИМ: он снёс
        # бы не только эти локи, но и любую несвязанную работу, уже накопленную в
        # той же транзакции вызывающего (в проде — редко, в тесте на одной
        # долгоживущей сессии — весь корпус решётки, заведённый до вызова).
        raise DomainError(
            409, "Удаление участника требует подтверждения состава: будут удалены предложения "
            f"в раундах — {composition['rounds_count']}, смет — {composition['estimates_count']}, "
            f"позиций — {composition['positions_count']}, ручных решений — {composition['overrides_count']}. "
            "Исходные файлы и результаты разбора сохраняются.",
            code="confirmation_required", context=composition,
        )
    db.execute(sa.delete(Offer).where(Offer.package_id == package_id))
    db.execute(sa.delete(OfferPackage).where(OfferPackage.id == package_id))
    db.commit()
    log.info("tender_participant_deleted tender=%s package=%s estimates=%d",
             tender_id, package_id, composition["estimates_count"])

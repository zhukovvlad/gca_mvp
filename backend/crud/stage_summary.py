"""Свод по этапам одного участника — проверка выбора, чтение входов, JSON
(спека 2026-08-27-stage-summary-design.md §2.3, §2.12, §2.16).

Арифметика — в `services/stage_summary.py`; здесь только запросы (фиксированное
число, не зависящее от числа колонок) и квантование на выходе.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.estimate_totals import estimate_totals_including_vat
from crud.project_passport import CATEGORY_TOTALS
from models import (
    Contractor,
    Estimate,
    EstimateCategoryOverride,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    Tender,
    TenderRound,
    WorkCategory,
)
from money.vat import quantize_money
from services import stage_summary as ss
from services.category_rollup import CategoryRef, DirectTotals

CODE_OFFER_NOT_FOUND = "offer_not_found"
CODE_TOO_FEW = "too_few_offers"
CODE_ONE_PER_ROUND = "one_offer_per_round"
CODE_SINGLE_PARTICIPANT = "single_participant"
CODE_NO_ESTIMATE = "offer_has_no_estimate"
CODE_TENDER_NOT_FOUND = "tender_not_found"

_PCT = Decimal("0.1")


def _refuse(status: int, code: str, message: str, offers: Sequence[int]) -> DomainError:
    return DomainError(status, message, code=code, context={"offers": sorted(offers)})


def validate_selection(db: Session, tender_id: int, offer_ids: Sequence[int]):
    """Пять проверок §2.3 в порядке таблицы спеки; результат — строки
    (Offer, Estimate.id | None, TenderRound) по возрастанию stage_no."""
    wanted = list(dict.fromkeys(offer_ids))
    rows = db.execute(
        sa.select(Offer, Estimate.id, TenderRound)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .outerjoin(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.id.in_(wanted or [-1]), Offer.tender_id == tender_id)
        .order_by(TenderRound.stage_no)
    ).all()
    found = {r[0].id for r in rows}
    missing = [o for o in wanted if o not in found]
    if missing:
        raise _refuse(404, CODE_OFFER_NOT_FOUND, "Предложение не найдено в этом тендере.", missing)
    if len(rows) < 2:
        raise _refuse(422, CODE_TOO_FEW, "Для свода нужны хотя бы два предложения.", wanted)
    by_round: dict[int, list[int]] = {}
    for offer, _, rnd in rows:
        by_round.setdefault(rnd.id, []).append(offer.id)
    dup = [o for offers in by_round.values() if len(offers) > 1 for o in offers]
    if dup:
        raise _refuse(422, CODE_ONE_PER_ROUND, "В одном раунде можно выбрать только одно предложение.", dup)
    packages = {offer.package_id for offer, _, _ in rows}
    if len(packages) > 1:
        raise _refuse(422, CODE_SINGLE_PARTICIPANT, "Свод строится по одному участнику.", [r[0].id for r in rows])
    no_estimate = [offer.id for offer, estimate_id, _ in rows if estimate_id is None]
    if no_estimate:
        raise _refuse(422, CODE_NO_ESTIMATE, "У предложения нет сметы — раунд был заменён другим файлом.", no_estimate)
    return rows


def _sum_known(*values: Decimal | None) -> Decimal | None:
    """Сумма известных слагаемых; `None`, только если известных нет вовсе
    (тот же приём, что `project_passport._sum_known`).

    НЕ `(a or 0) + (b or 0)`: `Decimal('0')` — валидная сумма ветки, а не
    признак отсутствующей строки, и `or` не отличает настоящий ноль от
    `None` (документированная в этом проекте ловушка truthiness на `Decimal`).
    Неизвестное слагаемое остаётся ОТСУТСТВУЮЩИМ, а не нулём: у сметы с двумя
    лотами вторая строка VIEW может законно не нести суммы вовсе (амount is
    None), и это не должно погасить уже накопленную сумму первой строки.
    """
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _direct_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, dict]:
    """`{estimate_id -> {work_category_id | None -> {source -> DirectTotals}}}` — ВАЛОВЫЕ,
    накоплением по строкам VIEW (как `_direct_totals` паспорта, но без пересчёта ставки).

    Накопление, а не присваивание, нужно СМЕТЕ С НЕСКОЛЬКИМИ ЛОТАМИ одного
    участника: `v_category_totals` группируется ещё и по `proposal_id`
    (миграция 0012), и на статью со сметой из нескольких предложений придёт
    НЕСКОЛЬКО строк VIEW — присваивание молча оставило бы только последнюю."""
    out: dict[int, dict] = {e: {} for e in estimate_ids}
    for row in db.execute(sa.select(CATEGORY_TOTALS).where(CATEGORY_TOTALS.c.estimate_id.in_(estimate_ids))).all():
        bucket = out[row.estimate_id].setdefault(row.work_category_id, {})
        prev = bucket.get(row.source)
        amount = row.amount if prev is None else _sum_known(prev.amount, row.amount)
        bucket[row.source] = DirectTotals(
            amount=amount,
            row_count=row.row_count + (prev.row_count if prev else 0),
            rows_with_amount=row.rows_with_amount + (prev.rows_with_amount if prev else 0),
            rows_not_finite=row.rows_not_finite + (prev.rows_not_finite if prev else 0),
        )
    return out


def _rates_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, Decimal | None]:
    """Эффективная база НДС сметы: `COALESCE(estimates.vat_rate_base_override, единогласие
    Proposal.vat_rate)` (спека §1.3, §2.8) — то же правило, что у `v_category_totals` и
    `_vat_rate` паспорта, двумя запросами на список."""
    overrides = dict(db.execute(
        sa.select(Estimate.id, Estimate.vat_rate_base_override).where(Estimate.id.in_(estimate_ids))
    ).all())
    declared: dict[int, set] = {e: set() for e in estimate_ids}
    for estimate_id, rate in db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).all():
        declared[estimate_id].add(rate)
    out: dict[int, Decimal | None] = {}
    for e in estimate_ids:
        if overrides.get(e) is not None:
            out[e] = overrides[e]
        else:
            s = declared[e]
            out[e] = next(iter(s)) if len(s) == 1 and None not in s else None
    return out


def _overrides_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, tuple[int, object]]:
    rows = db.execute(
        sa.select(Lot.estimate_id, sa.func.count(), sa.func.max(EstimateCategoryOverride.assigned_at))
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids)).group_by(Lot.estimate_id)
    ).all()
    found = {e: (n, last) for e, n, last in rows}
    return {e: found.get(e, (0, None)) for e in estimate_ids}


def load_inputs(db: Session, selection) -> list[ss.ColumnInput]:
    estimate_ids = [estimate_id for _, estimate_id, _ in selection]
    direct = _direct_by_estimate(db, estimate_ids)
    rates = _rates_by_estimate(db, estimate_ids)
    totals = estimate_totals_including_vat(db, estimate_ids)
    overrides = _overrides_by_estimate(db, estimate_ids)
    return [
        ss.ColumnInput(offer_id=offer.id, estimate_id=estimate_id, round_id=rnd.id, stage_no=rnd.stage_no,
                       label=rnd.label, held_on=rnd.held_on, vat_rate_base=rates[estimate_id],
                       direct=direct[estimate_id], file_total_gross=totals[estimate_id],
                       overrides_count=overrides[estimate_id][0], overrides_last_at=overrides[estimate_id][1])
        for offer, estimate_id, rnd in selection
    ]


def money_str(v: Decimal | None) -> str | None:
    """Деньги в строку через `quantize_money` — ПУБЛИЧНОЕ правило сериализации,
    одно на свод и на разложение статьи (`crud/position_drilldown.py`, спека
    2026-08-30-position-drilldown-design.md §2.11): второй копии этого правила
    быть не должно, иначе они разойдутся незаметно."""
    q = quantize_money(v)
    return None if q is None else str(q)


def pct_str(v: Decimal | None) -> str | None:
    """Процент в строку с одним знаком после запятой — то же правило одно на
    свод и на разложение статьи, см. докстроку `money_str`."""
    return None if v is None else str(v.quantize(_PCT, rounding=ROUND_HALF_UP))


def change_json(c: ss.Change) -> dict:
    """Сериализация `ss.Change` — то же правило одно на свод и на разложение
    статьи, см. докстроку `money_str`."""
    value = pct_str(c.value) if c.kind == ss.KIND_PERCENT else money_str(c.value)
    return {"kind": c.kind, "value": value, "direction": c.direction, "reason": c.reason}


def _cell(c: ss.Cell) -> dict:
    return {"state": c.state, "amount": money_str(c.shown), "amount_unavailable_reason": c.unavailable_reason,
            "additional_works_amount": money_str(c.additional_works_shown),
            "rows": {"row_count": c.rows.row_count, "rows_with_amount": c.rows.rows_with_amount,
                     "rows_not_finite": c.rows.rows_not_finite},
            "change": change_json(c.change)}


def _total_cell(c: ss.TotalCell) -> dict:
    """§2.16 (ревизия 28.08.2026): `TotalCell` — АГРЕГАТ, без `state` и без
    `additional_works_amount` (спека перечисляет их отсутствие явно). Отдельный
    сериализатор, а не `_cell`, потому что у типов теперь разные поля, а не
    только разные значения одних и тех же."""
    return {"amount": money_str(c.shown), "amount_unavailable_reason": c.unavailable_reason,
            "rows": {"row_count": c.rows.row_count, "rows_with_amount": c.rows.rows_with_amount,
                     "rows_not_finite": c.rows.rows_not_finite},
            "change": change_json(c.change)}


def _row(r: ss.SummaryRow) -> dict:
    return {"work_category_id": None if r.ref is None else r.ref.id,
            "code": None if r.ref is None else r.ref.code,
            "title": "Нераспределённое" if r.ref is None else r.ref.title,
            "is_unallocated": r.is_unallocated,
            # §2.12 спеки фичи 4: есть ли в ПОДДЕРЕВЕ строки разложения хотя бы
            # в одной выбранной колонке. row_count узла уже включает всё
            # поддерево по ОБЕИМ ветвям (позиции + допработы) — см. комментарий
            # у total_row_counts в services/stage_summary.compute_summary.
            # У «Нераспределённого» — всегда false: разложение адресуется
            # work_category_id, которого у этой строки нет, и обещать раскрытие,
            # которого нельзя запросить, контракт не должен.
            "has_drilldown_rows": (not r.is_unallocated
                                   and any(c.rows.row_count > 0 for c in r.cells)),
            "cells": [_cell(c) for c in r.cells],
            "bargain": change_json(r.bargain),
            "contribution": {"value": money_str(r.contribution.value), "direction": r.contribution.direction,
                             "reason": r.contribution.reason},
            "children": [_row(ch) for ch in r.children]}


def build_stage_summary(db: Session, tender_id: int, offer_ids: Sequence[int]) -> dict:
    tender = db.get(Tender, tender_id)
    if tender is None:
        # Свой код, а не `get_tender`: у свода detail — ОДИН объект у 404 и 422 (§2.16),
        # строковый detail карточки тендера сюда не годится.
        raise _refuse(404, CODE_TENDER_NOT_FOUND, f"Тендер {tender_id} не найден.", list(offer_ids))
    selection = validate_selection(db, tender_id, offer_ids)
    columns = load_inputs(db, selection)
    package_id = selection[0][0].package_id
    package, contractor = db.execute(
        sa.select(OfferPackage, Contractor).join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(OfferPackage.id == package_id)
    ).one()
    selected_offer_ids = {offer.id for offer, _, _ in selection}
    stage_rows = db.execute(
        sa.select(TenderRound.stage_no, TenderRound.label, Offer.id)
        .select_from(Offer).join(Estimate, Estimate.offer_id == Offer.id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.package_id == package_id).order_by(TenderRound.stage_no)
    ).all()
    stages = [{"stage_no": s, "label": lbl, "offer_id": oid, "selected": oid in selected_offer_ids}
              for s, lbl, oid in stage_rows]
    rounds_with_estimate = len(stages)
    last_estimate_id = columns[-1].estimate_id
    last_positions = db.execute(
        sa.select(sa.func.count()).select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == last_estimate_id, PositionItem.is_chapter.is_(False))
    ).scalar_one()
    refs = [CategoryRef(id=c.id, code=c.code, title=c.title, parent_id=c.parent_id, is_bucket=c.is_bucket,
                        sort_order=c.sort_order) for c in db.execute(sa.select(WorkCategory)).scalars().all()]

    result = ss.compute_summary(columns, refs)

    return {
        "tender": {"id": tender.id, "tender_number": tender.tender_number, "title": tender.title,
                   "object_title": tender.object.title},
        "participant": {"package_id": package.id, "contractor_id": contractor.id, "title": contractor.title,
                        "inn": contractor.inn, "rounds_with_estimate": rounds_with_estimate, "stages": stages},
        "columns": [{
            "kind": "round", "offer_id": c.input.offer_id, "estimate_id": c.input.estimate_id,
            "round_id": c.input.round_id, "stage_no": c.input.stage_no, "label": c.input.label,
            "held_on": iso(c.input.held_on), "vat_rate_base": None if c.input.vat_rate_base is None else str(c.input.vat_rate_base),
            "vat_state": c.vat_state, "total": money_str(c.total_shown), "total_change": change_json(c.total_change),
            "bar_height_pct": pct_str(c.bar_height_pct),
            "manual_overrides": {"count": c.input.overrides_count, "last_at": iso(c.input.overrides_last_at)},
            "convergence": {"categories_sum": money_str(c.convergence.categories_sum_gross),
                            "file_total": money_str(c.convergence.file_total_gross),
                            "converged": c.convergence.converged, "delta": money_str(c.convergence.delta),
                            "reason": c.convergence.reason},
        } for c in result.columns],
        "rows": [_row(r) for r in result.rows],
        "unallocated": _row(result.unallocated),
        "total": {"cells": [_total_cell(c) for c in result.total_cells]},
        "display": {"tax_basis": result.display.basis, "reason": result.display.reason,
                    "rates_by_column": None if result.display.rates_by_column is None
                    else [None if r is None else str(r) for r in result.display.rates_by_column],
                    "price_level": "nominal"},
        "kpi": {"stages_selected": result.kpi.stages_selected, "stages_loaded": rounds_with_estimate,
                "last_stage_positions": last_positions, "categories_with_amount": result.kpi.categories_with_amount,
                "categories_total": result.kpi.categories_total, "first_to_last": change_json(result.kpi.first_to_last)},
        "track": {"available": result.track.available, "reason": result.track.reason},
    }

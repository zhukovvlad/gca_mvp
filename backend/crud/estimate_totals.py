"""«Итого с НДС» сметы — правило единогласия (спека контура §2.13).

Блок итогов общий для листа и повторяется у предложения каждого лота, поэтому
сумма по предложениям дала бы N-кратный итог на многолотовом файле. Значение
есть ТОЛЬКО если у каждого предложения ровно одно конечное значение
`total_cost_including_vat.total_cost_total` и все они равны; пропуск или
`NaN`/`Infinity` у одного — `None`, «единогласия остальных» не бывает.

Паспорт считает то же число СУММОЙ (`project_passport._file_total_including_vat`)
по записанному правилу его спеки §2.5 — здесь оно не переписывается
(`TECH_DEBT.md` запись 19).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import Lot, Proposal, ProposalSummaryLine
from parser.constants import JSON_KEY_TOTAL_COST_INCLUDING_VAT


def estimate_total_including_vat(db: Session, estimate_id: int) -> Decimal | None:
    proposal_ids = list(db.execute(
        sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == estimate_id)
    ).scalars())
    if not proposal_ids:
        return None
    totals = list(db.execute(
        sa.select(ProposalSummaryLine.total_cost).where(
            ProposalSummaryLine.proposal_id.in_(proposal_ids),
            ProposalSummaryLine.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
        )
    ).scalars())
    if len(totals) != len(proposal_ids):
        return None
    if any(value is None or not value.is_finite() for value in totals):
        return None
    if len(set(totals)) != 1:
        return None
    return totals[0]


def estimate_totals_including_vat(db: Session, estimate_ids: Sequence[int]) -> dict[int, Decimal | None]:
    """То же правило единогласия, что выше, для списка смет за ДВА запроса
    (спека свода §2.12: число запросов не растёт с числом колонок).
    Ключ есть у каждого переданного id; `None` — итог недоступен."""
    ids = list(dict.fromkeys(estimate_ids))
    result: dict[int, Decimal | None] = {i: None for i in ids}
    if not ids:
        return result
    proposals = db.execute(
        sa.select(Lot.estimate_id, Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id.in_(ids))
    ).all()
    by_estimate: dict[int, list[int]] = {}
    for estimate_id, proposal_id in proposals:
        by_estimate.setdefault(estimate_id, []).append(proposal_id)
    all_proposals = [p for ps in by_estimate.values() for p in ps]
    # СПИСКИ строк на предложение, не dict: dict молча схлопнул бы дубль строки итога,
    # а одиночное правило на дубле отдаёт None (len(totals) != len(proposal_ids)).
    lines: dict[int, list] = {p: [] for p in all_proposals}
    for proposal_id, total in db.execute(
        sa.select(ProposalSummaryLine.proposal_id, ProposalSummaryLine.total_cost).where(
            ProposalSummaryLine.proposal_id.in_(all_proposals or [-1]),
            ProposalSummaryLine.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
        )
    ).all():
        lines[proposal_id].append(total)
    for estimate_id, proposal_ids in by_estimate.items():
        per_proposal = [lines[p] for p in proposal_ids]
        if any(len(ls) != 1 for ls in per_proposal):
            continue                       # пропуск или дубль у одного предложения
        values = [ls[0] for ls in per_proposal]
        if any(v is None or not v.is_finite() for v in values) or len(set(values)) != 1:
            continue
        result[estimate_id] = values[0]
    return result

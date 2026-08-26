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

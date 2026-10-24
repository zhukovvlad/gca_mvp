"""Сборка ответов экрана «КП против нашей цены» (спека 4а §2.12).

Модуль только читает: ни одного `INSERT`/`UPDATE`/`DELETE`. Расчёт — в чистых
слоях `services/our_price.py` и `services/our_price_money.py`, SQL — в
`crud/our_price_load.py` и `crud/our_price_inflation.py`; здесь они собираются в
форму ответа. Каждый загрузчик вызывается один раз на этап со списком смет,
поэтому число запросов не зависит ни от числа строк, ни от числа участников
(спека §2.17).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.comparison import InflationPlan, _inflation_sentence
from crud.our_price_inflation import plan_history_inflation, round_target_month
from crud.our_price_load import (
    KpLineRow,
    category_roots,
    category_rows,
    kp_vat_bases,
    load_history,
    load_kp_lines,
)
from crud.stage_summary import pct_str
from models import (
    Contractor,
    Estimate,
    ObjectModel,
    Offer,
    OfferPackage,
    RateClass,
    Tender,
    TenderRound,
)
from money.inflation import adjust_amount
from money.price import is_price
from services.our_price import (
    _CONTEXT,
    HistoryContract,
    HistoryIndex,
    LineState,
    OfferEvaluation,
    OfferTotals,
    evaluate_offer,
    offer_totals,
)
from services.our_price_money import (
    UNALLOCATED,
    estimate_net_total,
    history_shown_rate,
    money_pair,
    net_by_root,
    pct_to_median,
    per_sqm,
)

#: Подпись строки «Итого на м² объекта» в форме статьи.
TOTAL_ROW_TITLE = "Итого на м² объекта"
#: Подпись «Нераспределённого» — та же, что у свода по этапам.
UNALLOCATED_TITLE = "Нераспределённое"


@dataclass(frozen=True)
class _Head:
    tender_id: int
    tender_title: str
    object_id: int
    object_title: str
    area: Decimal | None
    rate_class_id: int
    rate_class_title: str
    round_id: int
    stage_no: int
    label: str | None
    held_on: object


@dataclass(frozen=True)
class _Participant:
    offer_id: int
    contractor_id: int
    contractor_title: str
    estimate_id: int | None
    base: Decimal | None

    @property
    def state(self) -> str:
        if self.estimate_id is None:
            return "no_estimate"
        if self.base is None:
            return "unknown_vat_base"
        return "ok"


@dataclass(frozen=True)
class _HistorySums:
    """₽/м² договоров истории (после приведения, если оно есть): итог сметы и
    каждый корень статьи. Договор попадает, только если его площадь проходит
    `is_price` и у сметы есть суммы; корень с неизвестной базой не входит."""

    total: dict[int, Decimal]
    by_root: dict[int | None, dict[int, Decimal]]


def _load_head(db: Session, tender_id: int, round_id: int) -> _Head:
    tender = db.execute(
        sa.select(
            Tender.id,
            Tender.title,
            Tender.object_id,
            ObjectModel.title,
            ObjectModel.area_total_sp,
            Tender.rate_class_id,
            RateClass.title,
        )
        .select_from(Tender)
        .join(ObjectModel, ObjectModel.id == Tender.object_id)
        .join(RateClass, RateClass.id == Tender.rate_class_id)
        .where(Tender.id == tender_id)
    ).one_or_none()
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")
    rnd = db.execute(
        sa.select(TenderRound.id, TenderRound.stage_no, TenderRound.label, TenderRound.held_on).where(
            TenderRound.id == round_id, TenderRound.tender_id == tender_id
        )
    ).one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.")
    return _Head(
        tender_id=tender[0],
        tender_title=tender[1],
        object_id=tender[2],
        object_title=tender[3],
        area=tender[4],
        rate_class_id=tender[5],
        rate_class_title=tender[6],
        round_id=rnd[0],
        stage_no=rnd[1],
        label=rnd[2],
        held_on=rnd[3],
    )


def _load_participants(db: Session, round_id: int) -> list[tuple[int, int, str, int | None]]:
    """Все офферы этапа: `(offer_id, contractor_id, название, estimate_id | None)`
    в порядке названия подрядчика."""
    rows = db.execute(
        sa.select(Offer.id, Contractor.id, Contractor.title, Estimate.id)
        .select_from(Offer)
        .join(OfferPackage, OfferPackage.id == Offer.package_id)
        .join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .outerjoin(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.round_id == round_id)
        .order_by(Contractor.title, Offer.id)
    ).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _history_sums(
    contracts: Sequence[HistoryContract],
    by_root: Mapping[int, Mapping[int | None, Decimal | None]],
    factors: Mapping[int, Decimal] | None,
) -> _HistorySums:
    total: dict[int, Decimal] = {}
    roots: dict[int | None, dict[int, Decimal]] = {}

    def adjusted(net: Decimal, estimate_id: int) -> Decimal:
        return net if factors is None else adjust_amount(net, factors[estimate_id])

    for contract in contracts:
        sums = by_root.get(contract.estimate_id)
        if not sums or not is_price(contract.area):
            continue
        net_total = estimate_net_total(sums)
        if net_total is not None:
            total[contract.contract_id] = per_sqm(adjusted(net_total, contract.estimate_id), contract.area)
        for root, value in sums.items():
            if value is not None:
                roots.setdefault(root, {})[contract.contract_id] = per_sqm(
                    adjusted(value, contract.estimate_id), contract.area
                )
    return _HistorySums(total=total, by_root=roots)


def _fork(values: Mapping[int, Decimal], class_contract_ids: set[int]) -> tuple[Decimal, Decimal] | None:
    """Минимум и максимум ₽/м² договоров класса; ни одного — `None`. Нулевые и
    отрицательные значения законны (правило сумм, спека §2.8)."""
    found = [value for contract_id, value in values.items() if contract_id in class_contract_ids]
    return (min(found), max(found)) if found else None


def _fork_view(fork: tuple[Decimal, Decimal] | None, rate: Decimal | None) -> dict | None:
    if fork is None:
        return None
    return {
        "min": money_pair(fork[0], rate, unit=True),
        "max": money_pair(fork[1], rate, unit=True),
    }


def _totals_view(totals: OfferTotals, rate: Decimal | None) -> dict:
    def pair(value: Decimal | None) -> dict | None:
        return money_pair(value, rate, unit=False)

    return {
        "bargain": pair(totals.bargain),
        "below": pair(totals.below),
        "below_lines": totals.below_lines,
        "unit_mismatch_lines": totals.unit_mismatch_lines,
        "unit_mismatch_amount": pair(totals.unit_mismatch_amount),
        "not_comparable": {
            "total": pair(totals.not_comparable_total),
            "by_state": {
                state.value: {
                    "lines": totals.not_comparable[state].lines,
                    "amount": pair(totals.not_comparable[state].amount),
                }
                for state in LineState
                if state in totals.not_comparable
            },
        },
    }


def _progress(kp_lines: Mapping[int, Sequence[KpLineRow]]) -> dict:
    """Тройка плашки по всем строкам (без глав) всех КП этапа. «Ждёт решения
    администратора» — строка без варианта, не `in_progress`, с флагом сигнала."""
    lines_total = in_progress = waiting_admin = 0
    for rows in kp_lines.values():
        for row in rows:
            lines_total += 1
            if row.line.in_progress:
                in_progress += 1
            elif row.line.key is None and row.line.waiting_admin:
                waiting_admin += 1
    return {"lines_total": lines_total, "in_progress": in_progress, "waiting_admin": waiting_admin}


def _inflation_view(plan: InflationPlan) -> dict:
    return {
        "series_id": plan.series_id,
        "series_name": plan.series_name,
        "series_note": plan.series_note,
        "series_updated_at": plan.series_updated_at,
        "target_month": plan.target_month,
        "has_forecast": plan.has_forecast,
        "used_years": plan.used_years,
        "sentence": _inflation_sentence(plan),
    }


def _whole(per_sqm_value: Decimal | None, fork: tuple[Decimal, Decimal] | None, area: Decimal | None):
    """Статья целиком: (₽/м² КП − минимум ₽/м² договоров класса) × площадь."""
    if per_sqm_value is None or fork is None:
        return None
    with localcontext(_CONTEXT):
        return (per_sqm_value - fork[0]) * area


def build_round_view(db: Session, tender_id: int, round_id: int, *, inflation_series_id: int | None) -> dict:
    """Ответ экрана этапа (спека 4а §2.12).

    Raises:
        DomainError 404: тендера нет, этап не принадлежит тендеру или нет ряда.
        DomainError 422: отказы приведения по ИПЦ — `inflation_target_missing`,
            `amendment_date_missing`, `missing_inflation_years`, в этом порядке.
    """
    head = _load_head(db, tender_id, round_id)
    participants_raw = _load_participants(db, head.round_id)
    kp_ids = [estimate_id for *_rest, estimate_id in participants_raw if estimate_id is not None]
    bases = kp_vat_bases(db, kp_ids)
    participants = [_Participant(*raw, base=bases.get(raw[3])) for raw in participants_raw]

    contracts, history_lines = load_history(db, exclude_object_id=head.object_id)
    plan: InflationPlan | None = None
    if inflation_series_id is not None:
        plan = plan_history_inflation(
            db,
            [contract.estimate_id for contract in contracts],
            series_id=inflation_series_id,
            target_month=round_target_month(db, head.round_id),
        )
    factors = plan.factor_by_estimate if plan is not None else None
    index = HistoryIndex(contracts, history_lines, factor_by_estimate=factors)

    kp_lines = load_kp_lines(db, kp_ids)
    root_of, root_meta = category_roots(db)
    roots_by_estimate = net_by_root(
        category_rows(db, kp_ids + [contract.estimate_id for contract in contracts]), root_of
    )

    area = head.area
    class_contracts = {c.contract_id for c in contracts if c.rate_class_id == head.rate_class_id}
    history = _history_sums(contracts, roots_by_estimate, factors)
    history_rate = history_shown_rate(p.base for p in participants)

    evaluations: dict[int, OfferEvaluation] = {}
    for participant in participants:
        if participant.state == "ok":
            evaluations[participant.offer_id] = evaluate_offer(
                [row.line for row in kp_lines[participant.estimate_id]],
                index,
                kp_vat_base=participant.base,
                tender_area=area,
                tender_class_id=head.rate_class_id,
            )

    total_fork = _fork(history.total, class_contracts)
    class_pool = [value for contract_id, value in history.total.items() if contract_id in class_contracts]

    participant_views = []
    kp_per_sqm: dict[int, Decimal | None] = {}
    for participant in participants:
        view: dict = {
            "offer_id": participant.offer_id,
            "estimate_id": participant.estimate_id,
            "contractor": {"id": participant.contractor_id, "title": participant.contractor_title},
            "vat_rate": None,
            "state": participant.state,
            "per_sqm": None,
            "per_sqm_reason": None,
            "class_median_pct": None,
            "ours_with_contractor": sum(1 for c in contracts if c.contractor_id == participant.contractor_id),
            "totals": None,
        }
        if participant.state == "ok":
            sums = roots_by_estimate.get(participant.estimate_id, {})
            kp_net = estimate_net_total(sums)
            kp_value = per_sqm(kp_net, area)
            kp_per_sqm[participant.offer_id] = kp_value
            view["vat_rate"] = participant.base
            view["per_sqm"] = money_pair(kp_value, participant.base, unit=True)
            if not is_price(area):
                view["per_sqm_reason"] = "no_area"
            pct = pct_to_median(kp_value, class_pool)
            view["class_median_pct"] = pct_str(pct)
            view["totals"] = _totals_view(offer_totals(evaluations[participant.offer_id]), participant.base)
        elif participant.state == "unknown_vat_base":
            view["per_sqm_reason"] = "unknown_vat_base"
        participant_views.append(view)

    def cells_for(root: int | None, *, is_total: bool) -> list[dict]:
        fork = total_fork if is_total else _fork(history.by_root.get(root, {}), class_contracts)
        cells = []
        for participant in participants:
            cell = {
                "offer_id": participant.offer_id,
                "per_sqm": None,
                "whole": None,
                "unit_mismatch_lines": 0,
                "ours_class_per_sqm": None,
            }
            if participant.state == "ok":
                if is_total:
                    value = kp_per_sqm[participant.offer_id]
                else:
                    net = roots_by_estimate.get(participant.estimate_id, {}).get(root)
                    value = per_sqm(net, area)
                evaluation = evaluations[participant.offer_id]
                if is_total:
                    cell["unit_mismatch_lines"] = offer_totals(evaluation).unit_mismatch_lines
                else:
                    cell["unit_mismatch_lines"] = sum(
                        1
                        for row in kp_lines[participant.estimate_id]
                        if row.article_root_id == root
                        and evaluation.lines[row.line.position_item_id].state is LineState.unit_mismatch
                    )
                cell["per_sqm"] = money_pair(value, participant.base, unit=True)
                cell["whole"] = money_pair(_whole(value, fork, area), participant.base, unit=False)
                cell["ours_class_per_sqm"] = _fork_view(fork, participant.base)
            cells.append(cell)
        return cells

    present_roots = {root for eid in kp_ids for root in roots_by_estimate.get(eid, {})}
    ordered_roots = sorted(
        (r for r in present_roots if r is not UNALLOCATED), key=lambda r: (root_meta[r].sort_order, r)
    )
    if UNALLOCATED in present_roots:
        ordered_roots.append(UNALLOCATED)

    articles = []
    for root in ordered_roots:
        meta = root_meta.get(root)
        fork = _fork(history.by_root.get(root, {}), class_contracts)
        articles.append(
            {
                "work_category_id": root,
                "code": None if meta is None else meta.code,
                "title": UNALLOCATED_TITLE if meta is None else meta.title,
                "sort_order": None if meta is None else meta.sort_order,
                "ours_class_per_sqm": _fork_view(fork, history_rate),
                "cells": cells_for(root, is_total=False),
            }
        )
    total_row = {
        "work_category_id": None,
        "code": None,
        "title": TOTAL_ROW_TITLE,
        "sort_order": None,
        "ours_class_per_sqm": _fork_view(total_fork, history_rate),
        "cells": cells_for(None, is_total=True),
    }

    result = {
        "tender": {
            "id": head.tender_id,
            "title": head.tender_title,
            "object": {"id": head.object_id, "title": head.object_title, "area_total_sp": area},
            "rate_class": {"id": head.rate_class_id, "title": head.rate_class_title},
        },
        "round": {
            "id": head.round_id,
            "stage_no": head.stage_no,
            "label": head.label,
            "held_on": iso(head.held_on),
        },
        "progress": _progress(kp_lines),
        "history": {
            "contracts": [
                {
                    "id": c.contract_id,
                    "number": c.number,
                    "object_title": c.object_title,
                    "year": c.year,
                    "rate_class": {"id": c.rate_class_id, "title": c.rate_class_title},
                    "contractor_id": c.contractor_id,
                    "area": c.area,
                }
                for c in contracts
            ],
            "shown_rate": history_rate,
            "per_sqm": [
                {"contract_id": contract_id, **money_pair(value, history_rate, unit=True)}
                for contract_id, value in history.total.items()
            ],
        },
        "participants": participant_views,
        "articles": articles,
        "total": total_row,
    }
    if plan is not None:
        result["inflation"] = _inflation_view(plan)
    return result

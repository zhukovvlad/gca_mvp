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
from crud.kp_decisions import EstimateDecisions, kp_source_line, load_decisions, load_unit_texts
from crud.our_price_inflation import plan_history_inflation, round_target_month
from crud.our_price_load import (
    ArticleMeta,
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
from services.kp_comment import InflationLabel, template_text
from services.kp_file import line_comment_inputs
from services.our_price import (
    _CONTEXT,
    RUNG_ORDER,
    HistoryContract,
    HistoryIndex,
    KpLine,
    LineResult,
    LineState,
    OfferEvaluation,
    OfferTotals,
    RateRange,
    Rung,
    RungChoice,
    available_rungs,
    evaluate_offer,
    lines_totals,
    offer_totals,
)
from services.our_price_money import (
    UNALLOCATED,
    article_net,
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
#: Код отказа панели строки: ступень не названа целиком, неизвестна или недоступна строке.
CODE_UNKNOWN_RUNG = "unknown_rung"


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


@dataclass(frozen=True)
class _Scene:
    """Выборка, общая у ответа этапа и разбора участника: участники, история с
    приведением (если задан ряд), строки КП и суммы статей."""

    head: _Head
    participants: list[_Participant]
    kp_ids: list[int]
    contracts: list[HistoryContract]
    plan: InflationPlan | None
    index: HistoryIndex
    kp_lines: dict[int, list[KpLineRow]]
    root_meta: dict[int, ArticleMeta]
    roots_by_estimate: dict[int, dict[int | None, Decimal | None]]
    class_contracts: set[int]
    history: _HistorySums
    #: Вилка ₽/м² договоров класса по итогу сметы.
    total_fork: tuple[Decimal, Decimal] | None
    #: Решения по строкам каждой сметы КП этапа (спека 4б §2.13).
    decisions: dict[int, EstimateDecisions]


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
        for root, net in sums.items():
            value = article_net(net)
            if value is not None:
                roots.setdefault(root, {})[contract.contract_id] = per_sqm(
                    adjusted(value, contract.estimate_id), contract.area
                )
    return _HistorySums(total=total, by_root=roots)


def _fork(values: Mapping[int, Decimal], class_contract_ids: set[int]) -> tuple[Decimal, Decimal] | None:
    """Минимум и максимум ₽/м² договоров класса; ни одного — `None`. Нулевые и
    отрицательные значения законны (правило сумм, спека §2.8); нулевые суммы
    статей в `values` по статье не попадают (`article_net`)."""
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
            elif _is_waiting_admin(row.line):
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


def _build_scene(
    db: Session,
    head: _Head,
    participants_raw: Sequence[tuple[int, int, str, int | None]],
    *,
    inflation_series_id: int | None,
    require_line: int | None = None,
) -> _Scene:
    """Общая выборка трёх ответов: база НДС КП, история (с приведением, если задан
    ряд), строки КП и суммы статей. Ответ этапа и разбор участника строятся из
    неё одним путём, поэтому цифры участника в них совпадают. `require_line` —
    строка, которая обязана быть среди строк КП: её отсутствие (404) проверяется
    раньше любой работы по ИПЦ."""
    kp_ids = [estimate_id for *_rest, estimate_id in participants_raw if estimate_id is not None]
    bases = kp_vat_bases(db, kp_ids)
    participants = [_Participant(*raw, base=bases.get(raw[3])) for raw in participants_raw]

    kp_lines = load_kp_lines(db, kp_ids)
    if require_line is not None and not any(
        row.line.position_item_id == require_line for rows in kp_lines.values() for row in rows
    ):
        raise DomainError(404, f"Строка {require_line} не найдена в КП оффера {participants_raw[0][0]}.")

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

    root_of, root_meta = category_roots(db)
    roots_by_estimate = net_by_root(
        category_rows(db, kp_ids + [contract.estimate_id for contract in contracts]), root_of
    )
    class_contracts = {c.contract_id for c in contracts if c.rate_class_id == head.rate_class_id}
    history = _history_sums(contracts, roots_by_estimate, factors)
    return _Scene(
        head=head,
        participants=participants,
        kp_ids=kp_ids,
        contracts=contracts,
        plan=plan,
        index=index,
        kp_lines=kp_lines,
        root_meta=root_meta,
        roots_by_estimate=roots_by_estimate,
        class_contracts=class_contracts,
        history=history,
        total_fork=_fork(history.total, class_contracts),
        decisions=load_decisions(db, kp_ids),
    )


def _evaluate(scene: _Scene, participant: _Participant, choice: RungChoice | None = None) -> OfferEvaluation:
    return evaluate_offer(
        [row.line for row in scene.kp_lines[participant.estimate_id]],
        scene.index,
        kp_vat_base=participant.base,
        tender_area=scene.head.area,
        tender_class_id=scene.head.rate_class_id,
        choice=choice,
        saved=scene.decisions[participant.estimate_id].saved,
    )


def _participant_view(
    scene: _Scene, participant: _Participant, evaluation: OfferEvaluation | None
) -> tuple[dict, Decimal | None]:
    """Карточка участника и его ₽/м² (без округления); `evaluation` нужен только
    участнику с состоянием `ok`."""
    area = scene.head.area
    view: dict = {
        "offer_id": participant.offer_id,
        "estimate_id": participant.estimate_id,
        "contractor": {"id": participant.contractor_id, "title": participant.contractor_title},
        "vat_rate": None,
        "state": participant.state,
        "per_sqm": None,
        "per_sqm_reason": None,
        "class_median_pct": None,
        "ours_with_contractor": sum(1 for c in scene.contracts if c.contractor_id == participant.contractor_id),
        "totals": None,
    }
    kp_value = None
    if participant.state == "ok":
        sums = scene.roots_by_estimate.get(participant.estimate_id, {})
        kp_value = per_sqm(estimate_net_total(sums), area)
        view["vat_rate"] = participant.base
        view["per_sqm"] = money_pair(kp_value, participant.base, unit=True)
        if not is_price(area):
            view["per_sqm_reason"] = "no_area"
        class_pool = [v for contract_id, v in scene.history.total.items() if contract_id in scene.class_contracts]
        view["class_median_pct"] = pct_str(pct_to_median(kp_value, class_pool))
        view["totals"] = _totals_view(offer_totals(evaluation), participant.base)
    elif participant.state == "unknown_vat_base":
        view["per_sqm_reason"] = "unknown_vat_base"
    return view, kp_value


def _cell_of(
    scene: _Scene,
    participant: _Participant,
    evaluation: OfferEvaluation | None,
    kp_value: Decimal | None,
    root: int | None,
    *,
    is_total: bool,
    fork: tuple[Decimal, Decimal] | None,
) -> dict:
    """Ячейка «статья × участник» в ставке участника; пустая, если смета не
    посчитана (нет сметы или база НДС неизвестна)."""
    cell: dict = {"per_sqm": None, "whole": None, "unit_mismatch_lines": 0, "ours_class_per_sqm": None}
    if participant.state != "ok":
        return cell
    area = scene.head.area
    if is_total:
        value = kp_value
        mismatch_lines = offer_totals(evaluation).unit_mismatch_lines
    else:
        value = per_sqm(article_net(scene.roots_by_estimate.get(participant.estimate_id, {}).get(root)), area)
        mismatch_lines = sum(
            1
            for row in scene.kp_lines[participant.estimate_id]
            if row.article_root_id == root
            and evaluation.lines[row.line.position_item_id].state is LineState.unit_mismatch
        )
    cell["unit_mismatch_lines"] = mismatch_lines
    cell["per_sqm"] = money_pair(value, participant.base, unit=True)
    cell["whole"] = money_pair(_whole(value, fork, area), participant.base, unit=False)
    cell["ours_class_per_sqm"] = _fork_view(fork, participant.base)
    return cell


def _article_roots(scene: _Scene, estimate_ids: Sequence[int]) -> set[int | None]:
    """Статьи смет КП: корни с суммами и корни строк КП. Статья, у которой все суммы
    пусты, в суммах отсутствует, но её строки есть, и открываются они только через
    статью (спека 4а §2.12, пункт `article_id`)."""
    roots: set[int | None] = set()
    for estimate_id in estimate_ids:
        roots.update(scene.roots_by_estimate.get(estimate_id, {}))
        roots.update(row.article_root_id for row in scene.kp_lines.get(estimate_id, []))
    return roots


def _ordered_roots(present_roots: set[int | None], root_meta: Mapping[int, ArticleMeta]) -> list[int | None]:
    """Корни статей по номеру классификатора, «Нераспределённое» — последним."""
    ordered: list[int | None] = sorted(
        (r for r in present_roots if r is not UNALLOCATED), key=lambda r: (root_meta[r].sort_order, r)
    )
    if UNALLOCATED in present_roots:
        ordered.append(UNALLOCATED)
    return ordered


def _article_head(root: int | None, root_meta: Mapping[int, ArticleMeta]) -> dict:
    meta = root_meta.get(root)
    return {
        "work_category_id": root,
        "code": None if meta is None else meta.code,
        "title": UNALLOCATED_TITLE if meta is None else meta.title,
        "sort_order": None if meta is None else meta.sort_order,
    }


_TOTAL_HEAD = {
    "work_category_id": None,
    "code": None,
    "title": TOTAL_ROW_TITLE,
    "sort_order": None,
}


def _tender_view(head: _Head) -> dict:
    return {
        "id": head.tender_id,
        "title": head.tender_title,
        "object": {"id": head.object_id, "title": head.object_title, "area_total_sp": head.area},
        "rate_class": {"id": head.rate_class_id, "title": head.rate_class_title},
    }


def _round_view(head: _Head) -> dict:
    return {"id": head.round_id, "stage_no": head.stage_no, "label": head.label, "held_on": iso(head.held_on)}


def _history_view(scene: _Scene, rate: Decimal | None) -> dict:
    return {
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
            for c in scene.contracts
        ],
        "shown_rate": rate,
        "per_sqm": [
            {"contract_id": contract_id, **money_pair(value, rate, unit=True)}
            for contract_id, value in scene.history.total.items()
        ],
    }


def build_round_view(db: Session, tender_id: int, round_id: int, *, inflation_series_id: int | None) -> dict:
    """Ответ экрана этапа (спека 4а §2.12).

    Raises:
        DomainError 404: тендера нет, этап не принадлежит тендеру или нет ряда.
        DomainError 422: отказы приведения по ИПЦ — `inflation_target_missing`,
            `amendment_date_missing`, `missing_inflation_years`, в этом порядке.
    """
    head = _load_head(db, tender_id, round_id)
    scene = _build_scene(db, head, _load_participants(db, head.round_id), inflation_series_id=inflation_series_id)
    participants = scene.participants
    history_rate = history_shown_rate(p.base for p in participants)

    evaluations: dict[int, OfferEvaluation] = {p.offer_id: _evaluate(scene, p) for p in participants if p.state == "ok"}
    participant_views = []
    kp_per_sqm: dict[int, Decimal | None] = {}
    for participant in participants:
        view, kp_value = _participant_view(scene, participant, evaluations.get(participant.offer_id))
        kp_per_sqm[participant.offer_id] = kp_value
        participant_views.append(view)

    def cells_for(root: int | None, *, is_total: bool) -> list[dict]:
        fork = scene.total_fork if is_total else _fork(scene.history.by_root.get(root, {}), scene.class_contracts)
        return [
            {
                "offer_id": participant.offer_id,
                **_cell_of(
                    scene,
                    participant,
                    evaluations.get(participant.offer_id),
                    kp_per_sqm[participant.offer_id],
                    root,
                    is_total=is_total,
                    fork=fork,
                ),
            }
            for participant in participants
        ]

    articles = []
    for root in _ordered_roots(_article_roots(scene, scene.kp_ids), scene.root_meta):
        fork = _fork(scene.history.by_root.get(root, {}), scene.class_contracts)
        articles.append(
            {
                **_article_head(root, scene.root_meta),
                "ours_class_per_sqm": _fork_view(fork, history_rate),
                "cells": cells_for(root, is_total=False),
            }
        )
    total_row = {
        **_TOTAL_HEAD,
        "ours_class_per_sqm": _fork_view(scene.total_fork, history_rate),
        "cells": cells_for(None, is_total=True),
    }

    result = {
        "tender": _tender_view(head),
        "round": _round_view(head),
        "progress": _progress(scene.kp_lines),
        "history": _history_view(scene, history_rate),
        "participants": participant_views,
        "articles": articles,
        "total": total_row,
    }
    if scene.plan is not None:
        result["inflation"] = _inflation_view(scene.plan)
    return result


# --- разбор участника и панель строки ----------------------------------------------


def _offer_context(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    *,
    inflation_series_id: int | None,
    require_line: int | None = None,
) -> tuple[_Scene, _Participant, OfferEvaluation]:
    """Выборка одного участника этапа и расчёт его строк на автоматических ступенях.

    Raises:
        DomainError 404: тендера, этапа или оффера этого этапа нет, у оффера нет
            сметы КП (разбирать нечего) или нет ряда ИПЦ.
        DomainError 422: отказы приведения по ИПЦ, как у экрана этапа.
    """
    head = _load_head(db, tender_id, round_id)
    raw = next((r for r in _load_participants(db, head.round_id) if r[0] == offer_id), None)
    if raw is None:
        raise DomainError(404, f"Оффер {offer_id} не найден в этапе {round_id} тендера {tender_id}.")
    if raw[3] is None:
        raise DomainError(404, f"У оффера {offer_id} нет сметы КП — разбирать нечего.")
    scene = _build_scene(db, head, [raw], inflation_series_id=inflation_series_id, require_line=require_line)
    participant = scene.participants[0]
    return scene, participant, _evaluate(scene, participant)


def _is_waiting_admin(line: KpLine) -> bool:
    """«Ждёт решения администратора»: нет варианта, не `in_progress`, сигнал поднят."""
    return line.key is None and not line.in_progress and line.waiting_admin


def _range_view(rng: RateRange | None, rate: Decimal | None) -> dict | None:
    if rng is None:
        return None
    return {
        "min": money_pair(rng.min, rate, unit=True),
        "max": money_pair(rng.max, rate, unit=True),
        "median": money_pair(rng.median, rate, unit=True),
    }


def _line_view(row: KpLineRow, result: LineResult, rate: Decimal | None) -> dict:
    """Строка КП на той ступени, на которой её посчитало ядро (спека 4а §2.12)."""
    line = row.line
    group = None
    if result.group_index is not None:
        group = {
            "lines": result.group_lines,
            "share": result.share,
            "kp_per_sqm": money_pair(result.group_per_sqm, rate, unit=True),
        }
    mismatch = None
    if result.mismatch is not None:
        mismatch = {
            "kp_ratio": result.mismatch.kp_ratio,
            "contracts": [
                {"contract_id": contract_id, "ratio": ratio} for contract_id, ratio in result.mismatch.contracts
            ],
        }
    return {
        "position_item_id": line.position_item_id,
        "context_id": row.context_id,
        "unit_id": row.unit_id,
        "article_id": row.article_root_id,
        "title": row.title,
        "unit": row.unit,
        "quantity": row.quantity,
        "section_ref": row.section_ref,
        "family": row.family_title,
        "variant_label": row.variant_label,
        "secondary": [{"name": name, "value": value} for name, value in row.secondary],
        "state": result.state.value,
        "waiting_admin": _is_waiting_admin(line),
        "suggestion_queue": row.suggestion_queue if _is_waiting_admin(line) else None,
        "kind": "system" if line.is_system else "work",
        "kp": {
            "unit_price": money_pair(result.kp_unit_net, rate, unit=True),
            "total": money_pair(result.kp_total_net, rate, unit=False),
        },
        "rung": (
            None
            if result.rung is None
            else {
                "level": result.rung.level,
                "scope": result.rung.scope,
                "auto": result.auto,
                "threshold_met": result.threshold_met,
            }
        ),
        "rungs": [
            {
                "level": count.rung.level,
                "scope": count.rung.scope,
                "contracts": count.contracts,
                "selectable": count.rung in result.selectable,
            }
            for count in result.rungs
        ],
        "range": _range_view(result.range, rate),
        "ours": (
            None
            if result.ours is None
            else {
                "value": money_pair(result.ours, rate, unit=True),
                "from_all_classes": result.ours_from_all_classes,
            }
        ),
        "position": result.position,
        "to_ours": money_pair(result.to_ours, rate, unit=False),
        "bargain": money_pair(result.bargain, rate, unit=False),
        "below": money_pair(result.below, rate, unit=False),
        "group": group,
        "unit_mismatch": mismatch,
    }


def _author(user_id: int, email: str) -> dict:
    return {"id": user_id, "email": email}


def _inflation_label(plan: InflationPlan | None) -> InflationLabel | None:
    """Ряд и целевой месяц `ММ.ГГГГ` для комментария; без ряда — `None`."""
    if plan is None:
        return None
    year, month = plan.target_month.split("-")
    return InflationLabel(plan.series_name, f"{month}.{year}")


def _template_text(
    scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult, unit_text: str
) -> str | None:
    """Шаблонный комментарий строки; вход собирает та же функция, что у файла. У КП с
    неизвестной базой НДС шаблона нет: нетто цены КП посчитать не из чего."""
    if participant.base is None:
        return None
    pid = row.line.position_item_id
    agreed = scene.decisions[participant.estimate_id].agreements.get(pid)
    return template_text(
        line_comment_inputs(
            kp_source_line(row, unit_text),
            result,
            history=scene.index,
            tender_class_id=scene.head.rate_class_id,
            tender_contractor_id=participant.contractor_id,
            tender_area=scene.head.area,
            kp_vat_base=participant.base,
            inflation=_inflation_label(scene.plan),
            agreed_net=agreed[0] if agreed is not None else None,
        )
    )


def _decision_fields(
    scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult, template: str | None
) -> dict:
    """Поля решений строки: `agreed`, `comment_kind`, `manual_rung` (спека 4б §2.11).
    Текст комментария сюда не входит — его отдаёт только панель."""
    decisions = scene.decisions[participant.estimate_id]
    pid = row.line.position_item_id

    agreed = None
    record = decisions.agreements.get(pid)
    if record is not None:
        net, at, user_id, email = record
        agreed = {**money_pair(net, participant.base, unit=True), "set_by": _author(user_id, email), "set_at": iso(at)}

    comment_kind = None
    if pid in decisions.comments:
        comment_kind = "custom"
    elif template is not None:
        comment_kind = "template"

    manual_rung = None
    manual = result.manual
    if manual is not None:
        owner = pid if manual.target == "line" else row.line.key.family_id
        at, user_id, email = decisions.rung_meta[(manual.target, owner)]
        manual_rung = {
            "level": manual.rung.level,
            "scope": manual.rung.scope,
            "active": manual.active,
            "reset_reason": manual.reset_reason,
            "target": manual.target,
            "set_by": _author(user_id, email),
            "set_at": iso(at),
        }
    return {"agreed": agreed, "comment_kind": comment_kind, "manual_rung": manual_rung}


def _line_with_decisions(scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult) -> dict:
    # Строке разбора нужен только вид комментария, а не его текст, поэтому
    # единицу КП не читаем: вид от неё не зависит.
    template = _template_text(scene, participant, row, result, "")
    return {
        **_line_view(row, result, participant.base),
        **_decision_fields(scene, participant, row, result, template),
    }


def build_offer_view(
    db: Session, tender_id: int, round_id: int, offer_id: int, *, inflation_series_id: int | None
) -> dict:
    """Разбор участника: шапка этапа для одного участника, статьи его КП с суммами и
    все строки КП (спека 4а §2.12, §2.14). История показана в ставке участника.

    Raises:
        DomainError 404: тендера, этапа или оффера этого этапа нет, у оффера нет
            сметы КП или нет ряда ИПЦ.
        DomainError 422: отказы приведения по ИПЦ, как у экрана этапа.
    """
    scene, participant, evaluation = _offer_context(
        db, tender_id, round_id, offer_id, inflation_series_id=inflation_series_id
    )
    head = scene.head
    rate = participant.base
    rows = scene.kp_lines[participant.estimate_id]
    view, kp_value = _participant_view(scene, participant, evaluation)

    def flat_cell(root: int | None, *, is_total: bool) -> dict:
        fork = scene.total_fork if is_total else _fork(scene.history.by_root.get(root, {}), scene.class_contracts)
        return _cell_of(scene, participant, evaluation, kp_value, root, is_total=is_total, fork=fork)

    articles = []
    for root in _ordered_roots(_article_roots(scene, [participant.estimate_id]), scene.root_meta):
        article_lines = None
        if participant.state == "ok":
            article_lines = _totals_view(
                lines_totals(evaluation, [row.line.position_item_id for row in rows if row.article_root_id == root]),
                rate,
            )
        articles.append(
            {**_article_head(root, scene.root_meta), **flat_cell(root, is_total=False), "lines_totals": article_lines}
        )

    result = {
        "tender": _tender_view(head),
        "round": _round_view(head),
        "progress": _progress({participant.estimate_id: rows}),
        "history": _history_view(scene, rate),
        "participants": [view],
        "articles": articles,
        "total": {**_TOTAL_HEAD, **flat_cell(None, is_total=True)},
        "lines": [
            _line_with_decisions(scene, participant, row, evaluation.lines[row.line.position_item_id]) for row in rows
        ],
    }
    if scene.plan is not None:
        result["inflation"] = _inflation_view(scene.plan)
    return result


def _rung_choice(level: str | None, scope: str | None, position_item_id: int, auto: LineResult) -> RungChoice | None:
    """Выбор ступени из запроса; без `level` и `scope` — `None` (авто). Ядро молча
    игнорирует выбор у строк, не дошедших до ступеней, поэтому запрос проверяется
    здесь: неполная пара, неизвестное значение и ступень вне доступных строке (её
    `rungs` в авто-расчёте) — один отказ `unknown_rung`."""
    if level is None and scope is None:
        return None
    rung = next((r for r in RUNG_ORDER if r.level == level and r.scope == scope), None)
    if rung not in {count.rung for count in auto.rungs}:
        raise DomainError(
            422,
            "Такой ступени у строки нет: нужны обе величины — уровень (v, p, f) и охват (cls, all) — "
            "и ступень из доступных этой строке.",
            code=CODE_UNKNOWN_RUNG,
        )
    return RungChoice(position_item_id, rung)


def _vat_dropped_view(scene: _Scene, row: KpLineRow, result: LineResult) -> list[dict]:
    """Договоры, выпавшие из-за неизвестной базы НДС, на ступени строки. Без выбранной
    ступени (`no_history`) — на первой ступени лестницы строки, где такие договоры
    есть: охват «класс» — только класс тендера."""
    line = row.line
    if result.rung is not None:
        rungs: Sequence[Rung] = [result.rung]
    elif result.state is LineState.no_history:
        rungs = available_rungs(line.key)
    else:
        return []
    for rung in rungs:
        dropped = [
            scene.index.contract(contract_id)
            for contract_id in scene.index.vat_dropped(rung.level, line.key.at(rung.level), is_system=line.is_system)
        ]
        dropped = [c for c in dropped if rung.scope == "all" or c.rate_class_id == scene.head.rate_class_id]
        if dropped:
            return [
                {"contract_id": c.contract_id, "object_title": c.object_title, "number": c.number, "year": c.year}
                for c in dropped
            ]
    return []


def build_line_view(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    position_item_id: int,
    *,
    level: str | None,
    scope: str | None,
    inflation_series_id: int | None,
) -> dict:
    """Панель строки: строка на выбранной ступени (без `level`/`scope` — авто, и она
    равна строке разбора), строки её группы системы, договоры ступени и договоры
    ключа, выпавшие из-за базы НДС (спека 4а §2.5, §2.12).

    Raises:
        DomainError 404: тендера, этапа, оффера или строки этого КП нет (глава — не
            строка), у оффера нет сметы КП или нет ряда ИПЦ.
        DomainError 422: `unknown_rung` либо отказы приведения по ИПЦ, как у экрана этапа.
    """
    scene, participant, evaluation = _offer_context(
        db,
        tender_id,
        round_id,
        offer_id,
        inflation_series_id=inflation_series_id,
        require_line=position_item_id,
    )
    rows = scene.kp_lines[participant.estimate_id]
    (row,) = (r for r in rows if r.line.position_item_id == position_item_id)
    choice = _rung_choice(level, scope, position_item_id, evaluation.lines[position_item_id])
    if choice is not None:
        evaluation = _evaluate(scene, participant, choice)
    result = evaluation.lines[position_item_id]
    rate = participant.base
    unit_texts = load_unit_texts(db, participant.estimate_id)

    group_lines = []
    if result.group_index is not None:
        family_id = evaluation.groups[result.group_index].family_id
        members = {pid for g in evaluation.groups if g.family_id == family_id for pid in g.lines}
        group_lines = [
            _line_with_decisions(scene, participant, r, evaluation.lines[r.line.position_item_id])
            for r in rows
            if r.line.position_item_id in members
        ]

    ratios = dict(result.mismatch.contracts) if result.mismatch is not None else {}
    contracts = []
    for rated in result.contracts:
        contract = scene.index.contract(rated.contract_id)
        contracts.append(
            {
                "contract_id": contract.contract_id,
                "object_title": contract.object_title,
                "number": contract.number,
                "year": contract.year,
                "rate_class": {"id": contract.rate_class_id, "title": contract.rate_class_title},
                "contractor_id": contract.contractor_id,
                "contractor_title": contract.contractor_title,
                "rate": money_pair(rated.net, rate, unit=True),
                "is_lower_bound": rated.net == result.range.min,
                "with_this_contractor": contract.contractor_id == participant.contractor_id,
                "ratio": ratios.get(rated.contract_id),
            }
        )

    vat_dropped = _vat_dropped_view(scene, row, result)

    template = _template_text(scene, participant, row, result, unit_texts[position_item_id])
    own_comment = scene.decisions[participant.estimate_id].comments.get(position_item_id)
    line = {**_line_view(row, result, rate), **_decision_fields(scene, participant, row, result, template)}
    return {
        "line": line,
        "group_lines": group_lines,
        "contracts": contracts,
        "vat_dropped": vat_dropped,
        "comment": {
            "kind": line["comment_kind"],
            "text": own_comment[0] if own_comment is not None else template,
            "template_text": template,
        },
    }

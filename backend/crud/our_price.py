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
from datetime import date
from decimal import Decimal, localcontext

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.comparison import InflationPlan, _inflation_sentence
from crud.kp_decisions import (
    CODE_UNKNOWN_RUNG,
    CODE_UNKNOWN_VAT_BASE,
    EstimateDecisions,
    kp_source_line,
    load_decisions,
    load_file_skeleton,
    load_unit_texts,
)
from crud.our_price_inflation import plan_history_inflation, round_target_month
from crud.our_price_load import (
    ArticleMeta,
    CategoryMeta,
    KpLineRow,
    category_roots,
    category_rows,
    category_tree,
    kp_vat_bases,
    load_analog_details,
    load_composites,
    load_history,
    load_kp_lines,
    load_work_history,
)
from crud.stage_summary import pct_str
from models import (
    Contractor,
    Estimate,
    KpSentFile,
    ObjectModel,
    Offer,
    OfferPackage,
    RateClass,
    Tender,
    TenderRound,
)
from money.inflation import YearMonth, adjust_amount
from money.price import is_price
from services.analog_ladder import FamilyLadderInfo, Figure, WorkHistory, parts_reference
from services.context_routing import ChapterStep, chapter_steps
from services.excel import iso_date_to_ru
from services.kp_comment import CommentInputs, InflationLabel, target_net, template_text
from services.kp_file import FileContext, KpSourceLine, line_comment_inputs
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
    SavedTarget,
    available_rungs,
    evaluate_offer,
    kp_work_of,
    lines_totals,
    offer_totals,
    resolve_target,
)
from services.our_price_money import (
    UNALLOCATED,
    NodeLine,
    article_net,
    estimate_net_total,
    history_shown_rate,
    money_pair,
    net_by_root,
    node_totals,
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
    #: Строки истории по работам, разложенные по семьям, и сведения семей — вход лестницы аналогов.
    #: Кандидаты семьи считаются один раз на сцену, а не на каждую строку КП.
    work_history: WorkHistory
    families: dict[int, FamilyLadderInfo]
    #: Дата КП: месяц этапа. Нужна и без ряда ИПЦ — самая свежая считается всегда.
    target_month: YearMonth | None
    kp_lines: dict[int, list[KpLineRow]]
    root_meta: dict[int, ArticleMeta]
    roots_by_estimate: dict[int, dict[int | None, Decimal | None]]
    class_contracts: set[int]
    history: _HistorySums
    #: Вилка ₽/м² договоров класса по итогу сметы.
    total_fork: tuple[Decimal, Decimal] | None
    #: Решения по строкам каждой сметы КП этапа (спека 4б §2.13).
    decisions: dict[int, EstimateDecisions]
    #: Число отправленных файлов по офферам этапа: у оффера без сметы они остаются.
    sent_files: dict[int, int]


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
        "over_lowest": pair(totals.bargain),
        "over_freshest": pair(totals.over_freshest),
        "comparable_lines": totals.comparable_lines,
        "freshest_lines": totals.freshest_lines,
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


def _progress(kp_lines: Mapping[int, Sequence[KpLineRow]], results: Mapping[int, LineResult]) -> dict:
    """Тройка плашки по всем строкам (без глав) всех КП этапа. «Ждёт решения
    администратора» — то же правило, что у флага строки (`_waiting_admin`); `results` — расчёт
    строк (у участника без расчёта строк в нём нет: считается только правило без варианта)."""
    lines_total = in_progress = waiting_admin = 0
    for rows in kp_lines.values():
        for row in rows:
            lines_total += 1
            if row.line.in_progress:
                in_progress += 1
            elif _waiting_admin(row.line, results.get(row.line.position_item_id)):
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


def _sent_files_counts(db: Session, offer_ids: Sequence[int]) -> dict[int, int]:
    """Число отправленных файлов по офферам одним сгруппированным запросом."""
    if not offer_ids:
        return {}
    rows = db.execute(
        sa.select(KpSentFile.offer_id, sa.func.count())
        .where(KpSentFile.offer_id.in_(offer_ids))
        .group_by(KpSentFile.offer_id)
    ).all()
    return {offer_id: count for offer_id, count in rows}


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
    work_history, families = load_work_history(
        db, estimate_of={contract.contract_id: contract.estimate_id for contract in contracts}
    )
    target_month = round_target_month(db, head.round_id)
    plan: InflationPlan | None = None
    if inflation_series_id is not None:
        plan = plan_history_inflation(
            db,
            [contract.estimate_id for contract in contracts],
            series_id=inflation_series_id,
            target_month=target_month,
        )
    factors = plan.factor_by_estimate if plan is not None else None
    index = HistoryIndex(contracts, history_lines, factor_by_estimate=factors)
    ladder_history = WorkHistory(work_history, families, index.contracts, factors=factors)

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
        work_history=ladder_history,
        families=families,
        target_month=target_month,
        kp_lines=kp_lines,
        root_meta=root_meta,
        roots_by_estimate=roots_by_estimate,
        class_contracts=class_contracts,
        history=history,
        total_fork=_fork(history.total, class_contracts),
        decisions=load_decisions(db, kp_ids),
        sent_files=_sent_files_counts(db, [raw[0] for raw in participants_raw]),
    )


def _evaluate(scene: _Scene, participant: _Participant, choice: RungChoice | None = None) -> OfferEvaluation:
    return evaluate_offer(
        [row.line for row in scene.kp_lines[participant.estimate_id]],
        scene.index,
        work_history=scene.work_history,
        families=scene.families,
        kp_vat_base=participant.base,
        tender_area=scene.head.area,
        tender_class_id=scene.head.rate_class_id,
        tender_contractor_id=participant.contractor_id,
        target_month=scene.target_month,
        factors=scene.plan.factor_by_estimate if scene.plan is not None else None,
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
        "sent_files_count": scene.sent_files.get(participant.offer_id, 0),
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
        "progress": _progress(
            scene.kp_lines, {pid: res for ev in evaluations.values() for pid, res in ev.lines.items()}
        ),
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


def _period_text(period: YearMonth | None) -> str | None:
    return None if period is None else f"{period.year:04d}-{period.month:02d}"


def _lowest_view(result: LineResult, rate: Decimal | None) -> dict | None:
    """Самая низкая расценка строки: у работы — аналог, у системы — ставка класса (без договора в ядре)."""
    if result.ours is None:
        return None
    figure = result.ladder.lowest if result.ladder is not None else None
    return {
        "value": money_pair(result.ours, rate, unit=True),
        "analog_id": None if figure is None else figure.analog_id,
        "contract_id": None if figure is None else figure.contract_id,
        "from_all_classes": result.ours_from_all_classes,
    }


def _freshest_view(figure: Figure | None, history: HistoryIndex, rate: Decimal | None) -> dict | None:
    if figure is None:
        return None
    return {
        "value": money_pair(figure.value, rate, unit=True),
        "analog_id": figure.analog_id,
        "contract_id": figure.contract_id,
        "period": _period_text(history.contract(figure.contract_id).period),
    }


def _mismatch_view(result: LineResult) -> dict | None:
    if result.mismatch is None:
        return None
    ladder = result.ladder
    return {
        "kp_ratio": result.mismatch.kp_ratio,
        "all_outliers": ladder.all_outliers if ladder is not None else False,
        "analogs": (
            []
            if ladder is None
            else [
                {"analog_id": a.position_item_id, "ratio": a.outlier_ratio}
                for a in ladder.analogs
                if a.outlier_ratio is not None
            ]
        ),
        "contracts": [{"contract_id": contract_id, "ratio": ratio} for contract_id, ratio in result.mismatch.contracts],
    }


def _waiting_admin(line: KpLine, result: LineResult | None) -> bool:
    """«Ждёт решения администратора»: нет варианта (спека 4а §2.11) либо нет зоны у цепочки (§2.10).
    Единственное правило и для флага строки, и для счётчика плашки."""
    return _is_waiting_admin(line) or (
        result is not None and result.state is LineState.no_zone and line.zone_waiting
    )


def _composite_parameter(line: KpLine, families: Mapping[int, FamilyLadderInfo]) -> dict | None:
    """Первый по порядку главный параметр строки с составным значением: `{id, name}`; иначе `None`.
    Берётся из форм, загруженных вместе с вариантом, запросов не делает."""
    info = families.get(line.key.family_id) if line.key is not None else None
    if info is None:
        return None
    for parameter_id in info.main_parameter_ids:
        if line.forms.get(parameter_id, "simple") != "simple" and parameter_id not in line.conflicted:
            return {"id": parameter_id, "name": info.names[parameter_id]}
    return None


def _path_view(steps: Sequence[ChapterStep]) -> list[dict]:
    """Путь по разделам сметы от её корня: ключ, название, номер раздела в файле."""
    return [{"chapter_item_id": s.chapter_item_id, "title": s.title, "number": s.number} for s in steps]


def _category_chain(tree: Mapping[int, CategoryMeta], category_id: int) -> list[CategoryMeta]:
    """Статьи от корня до `category_id` включительно."""
    chain = [tree[category_id]]
    while chain[-1].parent_id is not None:
        chain.append(tree[chain[-1].parent_id])
    chain.reverse()
    return chain


def _article_ref(tree: Mapping[int, CategoryMeta], category_id: int | None) -> dict | None:
    """Статья со своей цепочкой от корня (`chain` кончается самой статьёй); без статьи — `None`."""
    if category_id is None:
        return None
    chain = _category_chain(tree, category_id)
    node = chain[-1]
    return {
        "id": node.id,
        "code": node.code,
        "title": node.title,
        "chain": [{"id": step.id, "code": step.code, "title": step.title} for step in chain],
    }


def _line_view(
    row: KpLineRow,
    result: LineResult,
    rate: Decimal | None,
    history: HistoryIndex,
    families: Mapping[int, FamilyLadderInfo],
    steps: Mapping[int, tuple[ChapterStep, ...]],
) -> dict:
    """Строка КП: у работы — на лестнице аналогов, у системы — на ступени, которую выбрало ядро.

    Имена ядра 4а (`ours`, `to_ours`, `bargain`) на границе ответа названы по смыслу:
    `lowest`, `to_lowest`, `over_lowest`.
    """
    line = row.line
    ladder = result.ladder
    group = None
    if result.group_index is not None:
        group = {
            "lines": result.group_lines,
            "share": result.share,
            "kp_per_sqm": money_pair(result.group_per_sqm, rate, unit=True),
        }
    over_freshest = (
        max(Decimal(0), result.to_freshest)
        if result.state is LineState.comparable and result.to_freshest is not None
        else None
    )
    waiting_admin = _waiting_admin(line, result)
    return {
        "position_item_id": line.position_item_id,
        "context_id": row.context_id,
        "unit_id": row.unit_id,
        "article_id": row.article_root_id,
        "category_id": row.category_id,
        "chapter_path": _path_view(steps.get(row.chapter_item_id, ())),
        "title": row.title,
        "unit": row.unit,
        "quantity": row.quantity,
        "section_ref": row.section_ref,
        "family": row.family_title,
        "variant_label": row.variant_label,
        "secondary": [{"name": name, "value": value} for name, value in row.secondary],
        "state": result.state.value,
        "waiting_admin": waiting_admin,
        "suggestion_queue": row.suggestion_queue if _is_waiting_admin(line) else None,
        "kind": "system" if line.is_system else "work",
        "kp": {
            "unit_price": money_pair(result.kp_unit_net, rate, unit=True),
            "total": money_pair(result.kp_total_net, rate, unit=False),
        },
        "work_rung": (
            None
            if ladder is None or ladder.rung_no is None
            else {"no": ladder.rung_no, "released": list(ladder.released), "other_class": ladder.other_class}
        ),
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
        "zone": line.zone if not line.is_system else None,
        "section_chain_id": None if line.is_system else line.section_chain_id,
        "lowest": _lowest_view(result, rate),
        "freshest": _freshest_view(result.freshest, history, rate),
        "contractor": _freshest_view(None if ladder is None else ladder.contractor, history, rate),
        "range": _range_view(result.range, rate),
        "position": result.position,
        "to_lowest": money_pair(result.to_ours, rate, unit=False),
        "to_freshest": money_pair(result.to_freshest, rate, unit=False),
        "to_lowest_unit": money_pair(result.to_ours_unit, rate, unit=True),
        "to_freshest_unit": money_pair(result.to_freshest_unit, rate, unit=True),
        "over_lowest": money_pair(result.bargain, rate, unit=False),
        "over_freshest": money_pair(over_freshest, rate, unit=False),
        "below": money_pair(result.below, rate, unit=False),
        "analogs_count": len(ladder.analogs) if ladder is not None else 0,
        "no_history_reason": ladder.no_history_reason if ladder is not None else None,
        "composite_parameter": _composite_parameter(line, families),
        "group": group,
        "unit_mismatch": _mismatch_view(result),
    }


def _author(user_id: int, email: str) -> dict:
    return {"id": user_id, "email": email}


def _inflation_label(plan: InflationPlan | None) -> InflationLabel | None:
    """Ряд и целевой месяц `ММ.ГГГГ` для комментария; без ряда — `None`."""
    if plan is None:
        return None
    year, month = plan.target_month.split("-")
    return InflationLabel(plan.series_name, f"{month}.{year}")


def _comment_inputs(
    scene: _Scene,
    participant: _Participant,
    row: KpLineRow,
    result: LineResult,
    unit_text: str,
    *,
    agreed_net: Decimal | None,
) -> CommentInputs:
    """Вход комментария строки; собирает та же функция, что у файла."""
    return line_comment_inputs(
        kp_source_line(row, unit_text),
        result,
        history=scene.index,
        tender_class_id=scene.head.rate_class_id,
        tender_contractor_id=participant.contractor_id,
        tender_area=scene.head.area,
        kp_vat_base=participant.base,
        inflation=_inflation_label(scene.plan),
        agreed_net=agreed_net,
        saved_target=_saved_target(scene.decisions[participant.estimate_id], row.line.position_item_id),
    )


def _saved_target(decisions: EstimateDecisions, pid: int) -> SavedTarget | None:
    record = decisions.targets.get(pid)
    return None if record is None else (record[0], record[1])


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
        _comment_inputs(
            scene, participant, row, result, unit_text, agreed_net=agreed[0] if agreed is not None else None
        )
    )


def _file_ours(
    scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult, unit_text: str
) -> dict | None:
    """Автоматическая цель строки за единицу строки КП: то, что файл поставил бы в «Нашу расценку
    за ед.» без договорённости. У системы `line.ours` — ставка за м² объекта, а не цена за единицу
    подрядчика, поэтому панель получает это поле отдельно (спека 4б §2.2). У КП с неизвестной базой
    НДС вход комментария не собрать, как и шаблон: целей нет."""
    if participant.base is None:
        return None
    inputs = _comment_inputs(scene, participant, row, result, unit_text, agreed_net=None)
    return money_pair(target_net(inputs), participant.base, unit=True)


def _decision_fields(
    scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult, template: str | None
) -> dict:
    """Поля решений строки: `agreed`, `comment_kind`, `manual_rung`, `target_kind`, `choice`
    (спека 4б §2.11, спека лестницы аналогов §2.14, §2.17). Текст комментария сюда не входит — его
    отдаёт только панель."""
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
        at, user_id, email = decisions.rung_meta[(manual.target, row.line.key.family_id)]
        manual_rung = {
            "level": manual.rung.level,
            "scope": manual.rung.scope,
            "active": manual.active,
            "reset_reason": manual.reset_reason,
            "target": manual.target,
            "set_by": _author(user_id, email),
            "set_at": iso(at),
        }
    choice = resolve_target(_saved_target(decisions, pid), result, is_system=row.line.is_system)
    choice_view = None
    if choice is not None:
        _, _, at, user_id, email = decisions.targets[pid]
        choice_view = {
            "kind": choice.kind,
            "analog_id": choice.analog_id,
            "active": choice.active,
            "reset_reason": choice.reset_reason,
            "set_by": _author(user_id, email),
            "set_at": iso(at),
        }
    if agreed is not None:
        target_kind = "agreed"
    elif result.state is not LineState.comparable:
        target_kind = None
    else:
        target_kind = choice.kind if choice is not None and choice.active else "lowest"
    return {
        "agreed": agreed,
        "comment_kind": comment_kind,
        "manual_rung": manual_rung,
        "target_kind": target_kind,
        "choice": choice_view,
    }


def _line_with_decisions(
    scene: _Scene,
    participant: _Participant,
    row: KpLineRow,
    result: LineResult,
    steps: Mapping[int, tuple[ChapterStep, ...]],
) -> dict:
    # Строке разбора нужен только вид комментария, а не его текст, поэтому
    # единицу КП не читаем: вид от неё не зависит.
    template = _template_text(scene, participant, row, result, "")
    return {
        **_line_view(row, result, participant.base, scene.index, scene.families, steps),
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
    steps = chapter_steps(db, {row.chapter_item_id for row in rows if row.chapter_item_id is not None})
    _, tree = category_tree(db)

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
        "progress": _progress({participant.estimate_id: rows}, evaluation.lines),
        "history": _history_view(scene, rate),
        "participants": [view],
        "articles": articles,
        "total": {**_TOTAL_HEAD, **flat_cell(None, is_total=True)},
        "categories": _categories_view(tree, rows),
        "node_totals": _node_totals_view(tree, rows, evaluation, steps, rate),
        "lines": [
            _line_with_decisions(scene, participant, row, evaluation.lines[row.line.position_item_id], steps)
            for row in rows
        ],
    }
    if scene.plan is not None:
        result["inflation"] = _inflation_view(scene.plan)
    return result


def _categories_view(tree: Mapping[int, CategoryMeta], rows: Sequence[KpLineRow]) -> list[dict]:
    """Статьи на путях строк, от корня до статьи раздела строки, без лишних; по номеру статьи."""
    on_paths = {
        node.id for row in rows if row.category_id is not None for node in _category_chain(tree, row.category_id)
    }
    return [
        {
            "id": node.id,
            "parent_id": node.parent_id,
            "code": node.code,
            "title": node.title,
            "sort_order": node.sort_order,
        }
        for node in sorted((tree[category_id] for category_id in on_paths), key=lambda n: n.id)
    ]


def _node_totals_view(
    tree: Mapping[int, CategoryMeta],
    rows: Sequence[KpLineRow],
    evaluation: OfferEvaluation,
    steps: Mapping[int, tuple[ChapterStep, ...]],
    base: Decimal | None,
) -> dict:
    """Итоги узлов дерева по всем строкам КП: статьи (ключ `None` — «Нераспределённое», как у
    корневых `articles`) и разделы сметы."""
    lines = [
        NodeLine(
            total=row.line.total,
            no_price=evaluation.lines[row.line.position_item_id].state is LineState.no_price,
            categories=(
                (None,)
                if row.category_id is None
                else tuple(node.id for node in _category_chain(tree, row.category_id))
            ),
            chapters=tuple(step.chapter_item_id for step in steps.get(row.chapter_item_id, ())),
        )
        for row in rows
    ]
    return node_totals(lines, base=base)


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


def line_for_rung_check(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    position_item_id: int,
    *,
    inflation_series_id: int | None = None,
) -> tuple[int, KpLineRow, LineResult]:
    """Смета, строка и её расчёт для проверки решения: та же сцена, что у разбора. Ручная ступень
    системы ряд не передаёт — число договоров ступени от приведения не зависит; выбор цели работы
    передаёт ряд чтения: остаток аналогов от приведения зависит (спека лестницы аналогов §2.17).

    Raises:
        DomainError 404: тендера, этапа, оффера или строки этого КП нет, у оффера нет сметы.
    """
    scene, participant, evaluation = _offer_context(
        db, tender_id, round_id, offer_id, inflation_series_id=inflation_series_id, require_line=position_item_id
    )
    (row,) = (r for r in scene.kp_lines[participant.estimate_id] if r.line.position_item_id == position_item_id)
    return participant.estimate_id, row, evaluation.lines[position_item_id]


def _vat_dropped_view(scene: _Scene, row: KpLineRow, result: LineResult) -> list[dict]:
    """Договоры, выпавшие из-за неизвестной базы НДС, на ступени строки. У работы их называет
    лестница аналогов. У системы без выбранной ступени (`no_history`) — первая ступень строки,
    где такие договоры есть: охват «класс» — только класс тендера."""
    line = row.line
    if result.ladder is not None:
        return [
            {"contract_id": c.contract_id, "object_title": c.object_title, "number": c.number, "year": c.year}
            for c in (scene.index.contract(contract_id) for contract_id in result.ladder.vat_dropped)
        ]
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


def _analog_ids(result: LineResult) -> list[int]:
    """Строки договоров, которые панель покажет аналогами: у системы и у работы без ступени их нет."""
    ladder = result.ladder
    if ladder is None or ladder.rung_no is None:
        return []
    return [analog.position_item_id for analog in ladder.analogs]


def _analogs_view(
    scene: _Scene,
    participant: _Participant,
    result: LineResult,
    details: Mapping[int, tuple[str, str | None, list[str], int | None, int | None]],
    tree: Mapping[int, CategoryMeta],
    steps: Mapping[int, tuple[ChapterStep, ...]],
) -> list[dict]:
    """Справка работы: все аналоги выбранной ступени (включая выбросы) в порядке спеки §2.11.
    У системы и у работы без ступени — пусто. «Где аналог» — статья раздела строки договора и
    полный путь по смете договора, не статья строки КП."""
    ladder = result.ladder
    if ladder is None or ladder.rung_no is None:
        return []
    rate = participant.base
    lowest = ladder.lowest.analog_id if ladder.lowest is not None else None
    freshest = ladder.freshest.analog_id if ladder.freshest is not None else None
    view = []
    for analog in ladder.analogs:
        contract = scene.index.contract(analog.contract_id)
        title, unit, section_titles, category_id, chapter_item_id = details[analog.position_item_id]
        view.append(
            {
                "analog_id": analog.position_item_id,
                "contract_id": contract.contract_id,
                "object_title": contract.object_title,
                "number": contract.number,
                "year": contract.year,
                "period": _period_text(analog.period),
                "rate_class": {"id": contract.rate_class_id, "title": contract.rate_class_title},
                "contractor_id": contract.contractor_id,
                "contractor_title": contract.contractor_title,
                "section_titles": section_titles,
                "article": _article_ref(tree, category_id),
                "chapter_path": _path_view(steps.get(chapter_item_id, ())),
                "title": title,
                "unit": unit,
                "price": money_pair(analog.net, rate, unit=True),
                "nominal": money_pair(analog.nominal, rate, unit=True),
                "is_lowest": analog.position_item_id == lowest,
                "is_freshest": analog.position_item_id == freshest,
                "outlier_ratio": analog.outlier_ratio,
                "with_this_contractor": contract.contractor_id == participant.contractor_id,
                "place_match": analog.place_match,
                "selectable": analog.outlier_ratio is None,
            }
        )
    return view


def _parts_reference_view(
    db: Session, scene: _Scene, participant: _Participant, row: KpLineRow, result: LineResult
) -> list[dict]:
    """Справка «по частям» сборной строки (спека упрощения каталога §2.8): только панель. У системы
    и у работы, не дошедшей до лестницы, справки нет; на суммы и цель она не влияет."""
    if result.ladder is None:
        return []
    groups = parts_reference(
        kp_work_of(row.line, result.kp_unit_net),
        scene.work_history,
        tender_class_id=scene.head.rate_class_id,
        composites=load_composites(db, row.line),
        tender_contractor_id=participant.contractor_id,
        target_month=scene.target_month,
    )
    rate = participant.base
    view = []
    for group in groups:
        parts = []
        for part in group["parts"]:
            lowest = None
            if part.lowest_analog_id is not None:
                contract = scene.index.contract(part.lowest_contract_id)
                lowest = {
                    "value": money_pair(part.min, rate, unit=True),
                    "analog_id": part.lowest_analog_id,
                    "object_title": contract.object_title,
                    "contract_number": contract.number,
                    "period": _period_text(contract.period),
                }
            parts.append(
                {
                    "value_id": part.value_id,
                    "value": part.value,
                    "analogs": part.analogs,
                    "contracts": part.contracts,
                    "min": money_pair(part.min, rate, unit=True),
                    "max": money_pair(part.max, rate, unit=True),
                    "lowest": lowest,
                    "other_class": part.other_class,
                }
            )
        view.append({**group, "parts": parts})
    return view


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

    group_rows: list[KpLineRow] = []
    if result.group_index is not None:
        family_id = evaluation.groups[result.group_index].family_id
        members = {pid for g in evaluation.groups if g.family_id == family_id for pid in g.lines}
        group_rows = [r for r in rows if r.line.position_item_id in members]
    # пути разделов строки КП, её группы и аналогов — одним запросом на ответ
    analog_details = load_analog_details(db, _analog_ids(result))
    chapter_ids = {r.chapter_item_id for r in [row, *group_rows]} | {d[4] for d in analog_details.values()}
    steps = chapter_steps(db, chapter_ids - {None})
    _, tree = category_tree(db)
    group_lines = [
        _line_with_decisions(scene, participant, r, evaluation.lines[r.line.position_item_id], steps)
        for r in group_rows
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
    analogs = _analogs_view(scene, participant, result, analog_details, tree, steps)

    template = _template_text(scene, participant, row, result, unit_texts[position_item_id])
    own_comment = scene.decisions[participant.estimate_id].comments.get(position_item_id)
    line = {
        **_line_view(row, result, rate, scene.index, scene.families, steps),
        **_decision_fields(scene, participant, row, result, template),
        "file_ours": _file_ours(scene, participant, row, result, unit_texts[position_item_id]),
        "article": _article_ref(tree, row.category_id),
    }
    return {
        "line": line,
        "group_lines": group_lines,
        "contracts": contracts,
        "analogs": analogs,
        "parts_reference": _parts_reference_view(db, scene, participant, row, result),
        "vat_dropped": vat_dropped,
        "comment": {
            "kind": line["comment_kind"],
            "text": own_comment[0] if own_comment is not None else template,
            "template_text": template,
        },
    }


@dataclass(frozen=True)
class FileInputs:
    """Всё, из чего собираются строки и шапка файла подрядчику."""

    context: FileContext
    #: Главы и позиции сметы КП в порядке файла `(лот, id)`.
    source_lines: list[KpSourceLine]
    evaluation: OfferEvaluation
    #: position_item_id -> свой комментарий (пустой текст — тоже решение).
    comments: dict[int, str]
    #: position_item_id -> (нетто за единицу, дата `ДД.ММ.ГГГГ`).
    agreements: dict[int, tuple[Decimal, str]]
    #: position_item_id -> выбор цели работы `(вид, аналог | None)`.
    targets: dict[int, SavedTarget]


def build_file_inputs(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    *,
    inflation_series_id: int | None,
    exported_on: date,
) -> FileInputs:
    """Сцена участника, превращённая во вход строк файла: та же сцена с сохранёнными решениями,
    что у разбора и панели (спека 4б §2.8). Файл всегда в базе НДС КП, переключателя НДС нет.

    Raises:
        DomainError 404: тендера, этапа или оффера этого этапа нет, у оффера нет сметы КП.
        DomainError 422: отказы приведения по ИПЦ, как у разбора; `unknown_vat_base` — у КП нет базы.
    """
    scene, participant, evaluation = _offer_context(
        db, tender_id, round_id, offer_id, inflation_series_id=inflation_series_id
    )
    if participant.base is None:
        raise DomainError(
            422,
            "У КП неизвестна база НДС: наши цены не в чем перевести в его базу, файла подрядчику нет.",
            code=CODE_UNKNOWN_VAT_BASE,
        )
    estimate_id = participant.estimate_id
    head = scene.head
    decisions = scene.decisions[estimate_id]
    unit_texts = load_unit_texts(db, estimate_id)
    lot_of, chapters = load_file_skeleton(db, estimate_id)
    lines = [kp_source_line(row, unit_texts[row.line.position_item_id]) for row in scene.kp_lines[estimate_id]]
    ordered = sorted(chapters + lines, key=lambda source: (lot_of[source.position_item_id], source.position_item_id))

    plan = scene.plan
    periods = (
        {}
        if plan is None
        else {contract.contract_id: plan.period_by_estimate[contract.estimate_id] for contract in scene.contracts}
    )
    context = FileContext(
        kp_vat_base=participant.base,
        history=scene.index,
        tender_class_id=head.rate_class_id,
        tender_area=head.area,
        tender_contractor_id=participant.contractor_id,
        inflation=_inflation_label(plan),
        inflation_plan=plan,
        contract_periods=periods,
        tender_title=head.tender_title,
        object_title=head.object_title,
        stage_number=head.stage_no,
        stage_date=head.held_on,
        contractor_title=participant.contractor_title,
        exported_on=exported_on,
    )
    return FileInputs(
        context=context,
        source_lines=ordered,
        evaluation=evaluation,
        comments={pid: record[0] for pid, record in decisions.comments.items()},
        agreements={pid: (record[0], iso_date_to_ru(iso(record[1]))) for pid, record in decisions.agreements.items()},
        targets={pid: (record[0], record[1]) for pid, record in decisions.targets.items()},
    )

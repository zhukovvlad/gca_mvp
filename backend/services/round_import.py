"""Импорт сводной таблицы раунда: N offer-смет и опциональная baseline из одного
файла, атомарно (спека контура §2.5).

Оркестратор над `import_estimate`, а не второй конвейер (рамка §3.3, план Р4):
файл разбирается один раз, JSON режется на проекции — по одному предложению на
лот у каждого участника и baseline из валидных лотов, — и каждая проекция идёт
тем же путём, что смета договора. Ниже сметы ничего не меняется.

Идентичность участника — КАНОНИЧЕСКИЙ ИНН через все лоты (§2.7): `contractor_N`
в JSON — локальный индекс постобработки, не участник. Любое нарушение состава
— отказ, не предупреждение: частичное участие как правило никто не принимал.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

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
)
from parser.constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from services.category_resolution import CategoryResolver
from services.estimate_import import (
    EstimateImportError,
    PositionToMatch,
    _text,
    import_estimate,
)
from services.import_owners import baseline_estimate_owner, offer_estimate_owner
from services.unit_resolution import UnitResolver
from utils import canonicalize_inn


@dataclass(frozen=True)
class RoundProjection:
    """JSON раунда, сведённый к ОДНОМУ участнику: в каждом лоте ровно его блок."""

    inn: str
    title: str | None
    address: str | None
    accreditation: str | None
    data: dict[str, Any]


@dataclass(frozen=True)
class BaselineProjection:
    """JSON раунда, сведённый к «Расчетной стоимости»: только лоты с валидной базой."""

    data: dict[str, Any]
    lots_with_baseline: tuple[str, ...]
    lots_without: tuple[str, ...]


@dataclass
class RoundImportOutcome:
    estimate_ids: list[int] = field(default_factory=list)
    positions_to_match: list[PositionToMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimates_created: int = 0


def _baseline_is_valid(lot: dict[str, Any]) -> bool:
    """Валидность решил postprocess, по лоту (спека §1.3; план Р5): заглушка —
    невалидна, полный блок — валиден. Второй раз `_is_baseline_valid` не зовём."""
    block = lot.get(JSON_KEY_BASELINE_PROPOSAL) or {}
    return _text(block.get(JSON_KEY_CONTRACTOR_TITLE)) != BASELINE_MISSING_TITLE


def _lot_shell(lot: dict[str, Any], proposal_block: dict[str, Any]) -> dict[str, Any]:
    shell = {k: v for k, v in lot.items() if k not in (JSON_KEY_PROPOSALS, JSON_KEY_BASELINE_PROPOSAL)}
    shell[JSON_KEY_PROPOSALS] = {"contractor_1": copy.deepcopy(proposal_block)}
    shell[JSON_KEY_BASELINE_PROPOSAL] = {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}
    return shell


def split_round_payload(data: dict[str, Any]) -> tuple[list[RoundProjection], BaselineProjection | None]:
    """Режет JSON раунда на проекции участников и baseline (спека §2.7, §2.9).

    Raises:
        EstimateImportError: пустой ИНН у блока; один ИНН дважды в лоте;
            наборы ИНН в лотах не совпадают.
    """
    lots: dict[str, dict[str, Any]] = data.get(JSON_KEY_LOTS) or {}
    if not lots:
        raise EstimateImportError("В разобранном файле нет ни одного лота — импортировать нечего.")

    per_lot: dict[str, dict[str, dict[str, Any]]] = {}  # lot_key -> inn -> block
    for lot_key, lot in lots.items():
        seen: dict[str, dict[str, Any]] = {}
        for block in (lot.get(JSON_KEY_PROPOSALS) or {}).values():
            inn = canonicalize_inn(block.get(JSON_KEY_CONTRACTOR_INN))
            title = _text(block.get(JSON_KEY_CONTRACTOR_TITLE))
            if not inn:
                raise EstimateImportError(
                    f"В лоте «{lot_key}» у блока подрядчика «{title}» нет ИНН — участника "
                    "раунда не по чему опознать. Файл раунда обязан нести ИНН в каждом блоке."
                )
            if inn in seen:
                raise EstimateImportError(
                    f"В лоте «{lot_key}» ИНН {inn} встречается дважды («{_text(seen[inn].get(JSON_KEY_CONTRACTOR_TITLE))}» "
                    f"и «{title}»). Один участник — один блок."
                )
            seen[inn] = block
        per_lot[lot_key] = seen

    lot_keys = list(per_lot)
    reference = set(per_lot[lot_keys[0]])
    for lot_key in lot_keys[1:]:
        if set(per_lot[lot_key]) != reference:
            raise EstimateImportError(
                f"В раунде наборы участников в лотах различаются: «{lot_keys[0]}» — {sorted(reference)}, "
                f"«{lot_key}» — {sorted(per_lot[lot_key])}. Частичное участие в раунде не "
                "поддерживается — файл раунда должен нести одних и тех же участников во всех лотах."
            )

    projections: list[RoundProjection] = []
    for inn in sorted(reference, key=lambda i: list(per_lot[lot_keys[0]]).index(i)):
        first_block = per_lot[lot_keys[0]][inn]
        projected = copy.deepcopy(data)
        projected[JSON_KEY_LOTS] = {
            lot_key: _lot_shell(lots[lot_key], per_lot[lot_key][inn]) for lot_key in lot_keys
        }
        projections.append(RoundProjection(
            inn=inn,
            title=_text(first_block.get(JSON_KEY_CONTRACTOR_TITLE)),
            address=_text(first_block.get(JSON_KEY_CONTRACTOR_ADDRESS)),
            accreditation=_text(first_block.get(JSON_KEY_CONTRACTOR_ACCREDITATION)),
            data=projected,
        ))

    with_baseline = tuple(k for k in lot_keys if _baseline_is_valid(lots[k]))
    without = tuple(k for k in lot_keys if k not in with_baseline)
    baseline: BaselineProjection | None = None
    if with_baseline:
        projected = copy.deepcopy(data)
        projected[JSON_KEY_LOTS] = {
            k: _lot_shell(lots[k], lots[k][JSON_KEY_BASELINE_PROPOSAL]) for k in with_baseline
        }
        baseline = BaselineProjection(data=projected, lots_with_baseline=with_baseline, lots_without=without)
    return projections, baseline


def get_or_create_contractor(
    db: Session, *, inn: str, title: str | None, address: str | None,
    accreditation: str | None, warnings: list[str],
) -> Contractor:
    """Участник из файла: новый ИНН заводит карточку, известный — берётся как
    есть (спека §2.8). Карточка из XLSX НЕ обновляется (§3): расхождение имени —
    предупреждение. INSERT … ON CONFLICT + SELECT — без IntegrityError в
    транзакции: два раунда могут заводить одного подрядчика одновременно."""
    inserted = db.execute(
        pg_insert(Contractor)
        .values(inn=inn, title=title or f"Подрядчик ИНН {inn}", address=address or "", accreditation=accreditation or "")
        .on_conflict_do_nothing(index_elements=["inn"])
        .returning(Contractor.id)
    ).scalar_one_or_none()
    contractor = db.execute(sa.select(Contractor).where(Contractor.inn == inn)).scalar_one()
    if inserted is not None:
        warnings.append(
            f"Участник «{contractor.title}» (ИНН {inn}) заведён в справочник подрядчиков из файла раунда."
        )
    # Расхождения СУЩЕСТВУЮЩЕЙ карточки с файлом (имя, адрес, аккредитация)
    # здесь не проверяются: это делает `compare_header` по истине владельца —
    # один раз и в одном месте, иначе одно расхождение давало бы два предупреждения.
    return contractor


def get_or_create_package(db: Session, *, tender_id: int, contractor_id: int) -> OfferPackage:
    db.execute(
        pg_insert(OfferPackage)
        .values(tender_id=tender_id, contractor_id=contractor_id)
        .on_conflict_do_nothing(index_elements=["tender_id", "contractor_id"])
    )
    return db.execute(
        sa.select(OfferPackage).where(OfferPackage.tender_id == tender_id, OfferPackage.contractor_id == contractor_id)
    ).scalar_one()


def get_or_create_offer(db: Session, *, tender_id: int, round_id: int, package_id: int) -> Offer:
    db.execute(
        pg_insert(Offer)
        .values(tender_id=tender_id, round_id=round_id, package_id=package_id)
        .on_conflict_do_nothing(index_elements=["round_id", "package_id"])
    )
    return db.execute(
        sa.select(Offer).where(Offer.round_id == round_id, Offer.package_id == package_id)
    ).scalar_one()


def _round_estimate_ids(db: Session, round_id: int) -> list[int]:
    offer_ids = sa.select(Offer.id).where(Offer.round_id == round_id)
    return list(db.execute(
        sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offer_ids)))
        .order_by(Estimate.id)
    ).scalars())


def replace_round_estimates(db: Session, round_id: int, replace: bool, warnings: list[str]) -> list[int]:
    """Замена уровнем раунда, ДО цикла по участникам (спека §2.6).

    Вызывается под локами `tender FOR KEY SHARE`, `round FOR UPDATE`, взятыми
    вызывающим. Удаляются СМЕТЫ — offer- и round-owned; `Offer` и пакеты остаются:
    участник без сметы текущей загрузки — законное состояние решётки.
    Предупреждение о снесённых ручных решениях — одно на весь набор.
    """
    ids = _round_estimate_ids(db, round_id)
    if not ids:
        return []
    if not replace:
        raise EstimateImportError(
            f"Раунд уже загружен (смет: {len(ids)}); для замены повторите запрос с replace=true. "
            "Замена раунда — целиком, всех участников и расчётной стоимости разом."
        )
    lost = db.execute(
        sa.select(sa.func.count())
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(ids))
    ).scalar_one()
    if lost:
        warnings.append(f"Заменой раунда снесён ручной разнос статей; решений потеряно: {lost}.")
    db.execute(sa.delete(Estimate).where(Estimate.id.in_(ids)))
    warnings.append(f"Заменён раунд: удалено смет предыдущей загрузки — {len(ids)}.")
    return ids


def import_round(
    db: Session,
    *,
    tender_round: TenderRound,
    data: dict[str, Any],
    parser_version: str,
    import_job_id: int | None,
    replace: bool,
    unit_resolver: UnitResolver,
    category_resolver: CategoryResolver,
) -> RoundImportOutcome:
    """Один файл раунда → N offer-смет + опциональная baseline, в транзакции вызывающего.

    Порядок (спека §2.5): локи по иерархии tender → round (§2.11) → замена
    уровнем раунда ДО цикла (§2.6) → разбиение (§2.7) → на каждую проекцию
    get-or-create contractor → package → offer → import_estimate → baseline
    (§2.9). Матчинг здесь НЕ вызывается: позиции всех смет возвращаются
    объединённым списком, и пайплайн матчит их один раз (§2.5 п.5).
    """
    outcome = RoundImportOutcome()

    tender = db.execute(
        sa.select(Tender).where(Tender.id == tender_round.tender_id).with_for_update(key_share=True)
    ).scalar_one()
    db.execute(
        sa.select(TenderRound.id).where(TenderRound.id == tender_round.id).with_for_update()
    ).scalar_one()

    replace_round_estimates(db, tender_round.id, replace, outcome.warnings)

    projections, baseline = split_round_payload(data)

    for projection in projections:
        contractor = get_or_create_contractor(
            db, inn=projection.inn, title=projection.title, address=projection.address,
            accreditation=projection.accreditation, warnings=outcome.warnings,
        )
        package = get_or_create_package(db, tender_id=tender.id, contractor_id=contractor.id)
        offer = get_or_create_offer(db, tender_id=tender.id, round_id=tender_round.id, package_id=package.id)
        part = import_estimate(
            db, owner=offer_estimate_owner(offer, tender, contractor), data=projection.data,
            parser_version=parser_version, import_job_id=import_job_id, replace=False,
            unit_resolver=unit_resolver, category_resolver=category_resolver,
        )
        outcome.estimate_ids.append(part.estimate_id)
        outcome.positions_to_match.extend(part.positions_to_match)
        outcome.warnings.extend(part.warnings)

    if baseline is None:
        outcome.warnings.append(
            "Расчётная стоимость в файле раунда не заполнена ни в одном лоте — baseline-смета не создана."
        )
    else:
        if baseline.lots_without:
            outcome.warnings.append(
                "Расчётная стоимость заполнена не во всех лотах; без базы: "
                f"{', '.join(baseline.lots_without)}. Baseline-смета создана только по лотам с базой."
            )
        part = import_estimate(
            db, owner=baseline_estimate_owner(tender_round, tender), data=baseline.data,
            parser_version=parser_version, import_job_id=import_job_id, replace=False,
            unit_resolver=unit_resolver, category_resolver=category_resolver,
        )
        outcome.estimate_ids.append(part.estimate_id)
        outcome.positions_to_match.extend(part.positions_to_match)
        outcome.warnings.extend(part.warnings)

    outcome.estimates_created = len(outcome.estimate_ids)
    return outcome

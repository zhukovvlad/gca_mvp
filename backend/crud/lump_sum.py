"""Загрузка для правила «строка похожа на комплект» (спека строк-сумм §2): обычные ставки
ключей `(семья, единица)` и поправки администратора. Правило — `services/lump_sum.py`."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    CatalogContext,
    CatalogPosition,
    ContextMember,
    Estimate,
    Lot,
    LumpSumOverride,
    PositionItem,
    Proposal,
    UnitOfMeasure,
    User,
)
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.analog_ladder import MIN_NOMINAL
from services.lump_sum import TYPICAL_MIN_LINES, LumpOverride, TypicalRate, TypicalRates
from services.price_basis import median
from services.status_cache import typical_rates_cache

_ONE = Decimal(1)


def load_typical_rates(db: Session) -> TypicalRates:
    """Пул обычной ставки одним чтением по всем сметам договоров и КП (спека §1 п. 2).

    Строка входит, если: смета договора или КП (не раунд); не глава; контекст не архивный, не
    `SYSTEM`, с семьёй (членство — без фильтра `membership_state`, как у загрузчиков сравнения);
    у каталожной позиции есть единица; цена `is_price`, вес (`coalesce(suggested_quantity,
    quantity)`) `is_weight` и не равен 1; база НДС известна, нетто не меньше `MIN_NOMINAL`;
    поправки «это комплект» нет. Обычная ставка — медиана нетто (`price_basis.median`, точная
    десятичная); пул меньше `TYPICAL_MIN_LINES` ставки не даёт."""
    weight_column = sa.func.coalesce(PositionItem.suggested_quantity, PositionItem.quantity)
    rows = db.execute(
        sa.select(
            CatalogContext.work_family_id,
            CatalogPosition.unit_id,
            PositionItem.unit_cost_total,
            weight_column,
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .join(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .join(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(
            sa.or_(Estimate.contract_id.is_not(None), Estimate.offer_id.is_not(None)),
            PositionItem.is_chapter.is_(False),
            PositionItem.unit_cost_total.is_not(None),
            weight_column.is_not(None),
            CatalogPosition.unit_id.is_not(None),
            CatalogContext.work_family_id.is_not(None),
            CatalogContext.archived_at.is_(None),
            CatalogContext.semantic_kind != "SYSTEM",
            ~sa.exists().where(
                LumpSumOverride.position_item_id == PositionItem.id,
                LumpSumOverride.verdict == "lump",
            ),
        )
    ).all()
    pools: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
    for family_id, unit_id, unit_price, weight, vat_base in rows:
        if vat_base is None or not is_price(unit_price) or not is_weight(weight) or weight == _ONE:
            continue
        net = gross_to_net(unit_price, vat_base)
        if net >= MIN_NOMINAL:
            pools[(family_id, unit_id)].append(net)
    rates = {
        key: TypicalRate(net=median(nets), lines=len(nets))
        for key, nets in pools.items()
        if len(nets) >= TYPICAL_MIN_LINES
    }
    units = db.execute(
        sa.select(UnitOfMeasure.id, UnitOfMeasure.code, UnitOfMeasure.symbol)
    ).all()
    return TypicalRates(
        rates=rates,
        unit_codes={unit_id: code for unit_id, code, _ in units},
        unit_labels={unit_id: symbol for unit_id, _, symbol in units},
    )


def typical_rates(db: Session) -> TypicalRates:
    """Обычные ставки через кэш (`typical_rates_cache`, срок `TYPICAL_RATES_CACHE_TTL_S`)."""
    return typical_rates_cache.get(lambda: load_typical_rates(db))


def load_lump_overrides(
    db: Session, position_item_ids: Collection[int]
) -> dict[int, LumpOverride]:
    """Поправки по строкам сметы, с автором; строки без поправки в ответе нет. Пустой набор —
    без запроса."""
    ids = sorted(set(position_item_ids))
    if not ids:
        return {}
    return {
        item_id: LumpOverride(verdict=verdict, set_by_id=set_by, set_by_email=email, set_at=set_at)
        for item_id, verdict, set_by, email, set_at in db.execute(
            sa.select(
                LumpSumOverride.position_item_id,
                LumpSumOverride.verdict,
                LumpSumOverride.set_by,
                User.email,
                LumpSumOverride.set_at,
            )
            .join(User, User.id == LumpSumOverride.set_by)
            .where(LumpSumOverride.position_item_id.in_(ids))
        ).all()
    }

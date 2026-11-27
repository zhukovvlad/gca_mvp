"""Загрузка для правила «строка похожа на комплект» (спека строк-сумм §2): обычные ставки
ключей `(семья, единица)` и поправки администратора. Правило — `services/lump_sum.py`."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Collection
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError
from models import (
    CatalogContext,
    CatalogPosition,
    ContextMember,
    Estimate,
    Lot,
    LumpSumOverride,
    PositionItem,
    PriceErrorMark,
    Proposal,
    UnitOfMeasure,
    User,
)
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.analog_ladder import MIN_NOMINAL
from services.lump_sum import (
    TYPICAL_MIN_LINES,
    LumpOverride,
    LumpVerdict,
    TypicalRate,
    TypicalRates,
)
from services.price_basis import median
from services.stage_cache import is_dirty
from services.status_cache import typical_rates_cache

CODE_POSITION_ITEM_NOT_FOUND = "position_item_not_found"
CODE_LUMP_SUM_NOT_APPLICABLE = "lump_sum_not_applicable"

_ONE = Decimal(1)


def load_typical_rates(db: Session) -> TypicalRates:
    """Пул обычной ставки одним чтением по всем сметам договоров и КП (спека §1 п. 2).

    Строка входит, если: смета договора или КП (не раунд); не глава; контекст не архивный, не
    `SYSTEM`, с семьёй (членство — без фильтра `membership_state`, как у загрузчиков сравнения);
    у каталожной позиции есть единица; цена `is_price`, вес (`coalesce(suggested_quantity,
    quantity)`) `is_weight` и не равен 1; база НДС известна, нетто не меньше `MIN_NOMINAL`;
    поправки «это комплект» нет; отметки «ошибка в цене» нет (спека «Не аналог» §5 п. 4; тендерные
    отметки ставку не трогают). Обычная ставка — медиана нетто (`price_basis.median`, точная
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
            ~sa.exists().where(PriceErrorMark.position_item_id == PositionItem.id),
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
    """Обычные ставки через кэш (`typical_rates_cache`, срок `TYPICAL_RATES_CACHE_TTL_S`).

    В «грязной» транзакции (записано вне закрытого списка кэша сцен, `is_dirty`) ставки считаются
    напрямую, а общий кэш не читается и не заполняется (спека стадийного кэша §5): тёплый кэш не
    видел бы собственной правки цены или состава транзакции, а пустой запомнил бы ставки по данным,
    которые откатятся, — сброса на откате нет."""
    if is_dirty(db):
        return load_typical_rates(db)
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


def _lock_applicable_item(db: Session, position_item_id: int) -> None:
    """Строка сметы под `FOR UPDATE` (по образцу `decide_zone`) и проверка состава (спека §3 п. 2):
    правило применимо только к строке-работе (не глава) сметы договора или КП, чей контекст не в
    архиве, не `SYSTEM`, с семьёй и вариантом. Цена, вес и база НДС поправку не запрещают."""
    locked = db.execute(
        sa.select(PositionItem.id, PositionItem.is_chapter)
        .where(PositionItem.id == position_item_id)
        .with_for_update()
    ).one_or_none()
    if locked is None:
        raise DomainError(
            404, f"Строка сметы {position_item_id} не найдена.", code=CODE_POSITION_ITEM_NOT_FOUND
        )
    is_chapter = locked[1]
    row = db.execute(
        sa.select(
            Estimate.contract_id,
            Estimate.offer_id,
            CatalogContext.work_family_id,
            CatalogContext.work_variant_id,
            CatalogContext.archived_at,
            CatalogContext.semantic_kind,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(PositionItem.id == position_item_id)
    ).first()
    applicable = (
        not is_chapter
        and row is not None
        and (row.contract_id is not None or row.offer_id is not None)
        and row.work_family_id is not None
        and row.work_variant_id is not None
        and row.archived_at is None
        and row.semantic_kind != "SYSTEM"
    )
    if not applicable:
        raise DomainError(
            422,
            "Правило «комплект или ставка» к этой строке неприменимо: нужна строка-работа "
            "сметы договора или КП в активном контексте с семьёй и вариантом.",
            code=CODE_LUMP_SUM_NOT_APPLICABLE,
        )


def set_lump_override(
    db: Session, *, position_item_id: int, verdict: LumpVerdict, actor_id: int
) -> LumpSumOverride:
    """Поправка администратора к строке (спека §3 п. 2). То же значение — запись не трогается
    (автор и время прежние); иное — значение, автор и `now()`. Не коммитит."""
    _lock_applicable_item(db, position_item_id)
    row = db.get(LumpSumOverride, position_item_id, populate_existing=True)
    if row is None:
        row = LumpSumOverride(
            position_item_id=position_item_id,
            verdict=verdict,
            set_by=actor_id,
            set_at=dt.datetime.now(dt.UTC),
        )
        db.add(row)
    elif row.verdict != verdict:
        row.verdict = verdict
        row.set_by = actor_id
        row.set_at = dt.datetime.now(dt.UTC)
    db.flush()
    db.refresh(row)  # время — как из базы (пояс сессии), одинаково для первой и повторной записи
    return row


def clear_lump_override(db: Session, *, position_item_id: int) -> None:
    """«Как решило правило»: поправка снимается; без записи — ничего. Строки нет — 404. Не коммитит."""
    locked = db.execute(
        sa.select(PositionItem.id).where(PositionItem.id == position_item_id).with_for_update()
    ).one_or_none()
    if locked is None:
        raise DomainError(
            404, f"Строка сметы {position_item_id} не найдена.", code=CODE_POSITION_ITEM_NOT_FOUND
        )
    db.execute(sa.delete(LumpSumOverride).where(LumpSumOverride.position_item_id == position_item_id))
    db.flush()

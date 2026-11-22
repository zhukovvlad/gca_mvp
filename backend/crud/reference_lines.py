"""Справочные строки договоров и значения параметров связей (спека справочных
аналогов §2.3, §2.7 п. 1).

Модуль только читает. Справочная строка — строка последней сметы договора истории,
годная для блоков справочных аналогов, поиска и справочной цели. Условия §2.3
делятся между запросом и загрузчиком так: `reference_lines_query` отбирает
условия 1–5 (смета из заданных договоров, не глава, контекст не `SYSTEM`, цена и
вес по предикатам лестницы, база НДС известна); условие 6 (номинал нетто не ниже
`MIN_NOMINAL`) считает `load_reference_lines` в Python после `gross_to_net`, как
лестница, — так граница «1,00 проходит, 0,99 нет» не расходится с ней в округлении.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.lump_sum import load_lump_overrides, typical_rates
from crud.our_price_load import _WEIGHT, _price_ok, _weight_ok
from models import (
    CatalogContext,
    CatalogPosition,
    ContextMember,
    ContextParameterValue,
    Estimate,
    FamilyParameter,
    FamilyParameterValue,
    Lot,
    PositionItem,
    Proposal,
    SectionChain,
    UnitOfMeasure,
    ValueSource,
    WorkFamily,
)
from money.inflation import adjust_amount
from money.vat import gross_to_net
from services.analog_ladder import MIN_NOMINAL
from services.lump_sum import LumpCheck, lump_check
from services.price_basis import HistoryContract
from services.zone_rules import Zone, normalize_title


@dataclass(frozen=True)
class ReferenceLine:
    """Справочная строка договора (спека §2.3)."""

    position_item_id: int
    contract_id: int
    estimate_id: int
    title: str
    #: Единица строки — `catalog_positions.unit_id` её каталожной позиции.
    unit_id: int | None
    unit_symbol: str | None
    #: Семья и зона — `None` у строки без варианта и без определённой зоны.
    family_id: int | None
    context_id: int | None
    zone: Zone | None
    chain_titles: frozenset[str]
    chapter_item_id: int | None
    #: Цена за единицу без НДС, без ИПЦ.
    nominal: Decimal
    #: Номинал с ИПЦ сметы, если ряд выбран; иначе равен номиналу.
    net: Decimal
    #: Вес строки (`coalesce(suggested_quantity, quantity)`) — проходит `is_weight`.
    weight: Decimal
    #: Решение «комплект или ставка» (спека строк-сумм §6 п. 1); `None` — правило неприменимо (нет семьи или
    #: варианта, контекст в архиве, нет единицы каталожной позиции).
    lump: LumpCheck | None = None
    #: Название семьи (подпись признака «комплект»); `None` — у строки нет семьи.
    family_title: str | None = None


@dataclass(frozen=True)
class ParamText:
    """Значение параметра контекста строки как оно записано, с пометками, по которым
    число из него не берётся (спека §2.7 п. 1)."""

    text: str | None
    parameter_name: str
    #: `ValueSource`: `name`, `path`, `manual`, `path_conflict`, `none`, `off_list`.
    source: str | None
    #: Значение не из списка параметра; в `text` лежит `off_list_text`.
    off_list: bool
    #: Составное значение («плиты + балки», «Knauf или Gyproc»).
    composite: bool


def reference_lines_query(contracts: Sequence[HistoryContract]) -> sa.Select:
    """Строки, прошедшие условия 1–5 §2.3, по сметам заданных договоров.

    Контекст у строки один (`context_members.position_item_id` — ключ), поэтому
    «ни один контекст не `SYSTEM`» — это один контекст не `SYSTEM`; строка без
    контекста проходит. Колонки: `position_item_id`, `contract_id`, `estimate_id`,
    `title`, `unit_id`, `unit_symbol`, `family_id`, `context_id`, `zone`,
    `chain_titles`, `chapter_item_id`, `unit_cost_total`, `vat_base`, `weight`, `variant_id`,
    `archived_at`, `family_title` (последние три и вес — для решения «комплект или ставка»). Условие 6
    здесь не применяется (см. докстроку модуля).
    """
    estimate_ids = sorted({contract.estimate_id for contract in contracts})
    return (
        sa.select(
            PositionItem.id.label("position_item_id"),
            Estimate.contract_id.label("contract_id"),
            Estimate.id.label("estimate_id"),
            PositionItem.job_title_in_proposal.label("title"),
            CatalogPosition.unit_id.label("unit_id"),
            UnitOfMeasure.symbol.label("unit_symbol"),
            CatalogContext.work_family_id.label("family_id"),
            CatalogContext.id.label("context_id"),
            SectionChain.zone.label("zone"),
            SectionChain.titles.label("chain_titles"),
            PositionItem.chapter_item_id.label("chapter_item_id"),
            PositionItem.unit_cost_total.label("unit_cost_total"),
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate).label("vat_base"),
            _WEIGHT.label("weight"),
            CatalogContext.work_variant_id.label("variant_id"),
            CatalogContext.archived_at.label("archived_at"),
            WorkFamily.title.label("family_title"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .outerjoin(WorkFamily, WorkFamily.id == CatalogContext.work_family_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .outerjoin(SectionChain, SectionChain.id == PositionItem.section_chain_id)
        .where(
            Estimate.id.in_(estimate_ids),
            PositionItem.is_chapter.is_(False),
            sa.or_(CatalogContext.id.is_(None), CatalogContext.semantic_kind != "SYSTEM"),
            _price_ok(PositionItem.unit_cost_total),
            _weight_ok(_WEIGHT),
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate).is_not(None),
        )
    )


def load_reference_lines(
    db: Session,
    contracts: Sequence[HistoryContract],
    ids: Collection[int],
    *,
    factors: Mapping[int, Decimal] | None,
) -> dict[int, ReferenceLine]:
    """Справочные строки среди `ids`: `position_item_id` -> строка. Один запрос.

    Условия 1–5 отбирает `reference_lines_query`, условие 6 — здесь. `factors` — множитель
    ИПЦ по `estimate_id` сметы договора; `None` — ряд не выбран, `net = nominal`. Множитель,
    которого нет в отображении, — `KeyError`, как в лестнице аналогов.
    """
    if not contracts or not ids:
        return {}
    rows = db.execute(reference_lines_query(contracts).where(PositionItem.id.in_(sorted(set(ids))))).all()
    return reference_lines_from_rows(db, rows, factors)


def reference_lines_from_rows(
    db: Session, rows: Iterable, factors: Mapping[int, Decimal] | None
) -> dict[int, ReferenceLine]:
    """Строки результата `reference_lines_query` (с любыми дополнительными условиями) -> справочные
    строки: условие 6 §2.3, приведение по ИПЦ и решение «комплект или ставка». Порядок словаря — порядок
    строк. Ставки (кэш) и поправки читаются одним запросом на набор (спека строк-сумм §2)."""
    rows = list(rows)
    # правило применимо к строке с семьёй и вариантом в неархивном контексте (контекст не `SYSTEM` — условие запроса)
    applicable = {
        row.position_item_id
        for row in rows
        if row.family_id is not None and row.variant_id is not None and row.archived_at is None
    }
    rates = typical_rates(db)
    overrides = load_lump_overrides(db, applicable)
    lines: dict[int, ReferenceLine] = {}
    for row in rows:
        nominal = gross_to_net(row.unit_cost_total, row.vat_base)
        if nominal < MIN_NOMINAL:
            continue
        net = nominal if factors is None else adjust_amount(nominal, factors[row.estimate_id])
        lines[row.position_item_id] = ReferenceLine(
            position_item_id=row.position_item_id,
            contract_id=row.contract_id,
            estimate_id=row.estimate_id,
            title=row.title,
            unit_id=row.unit_id,
            unit_symbol=row.unit_symbol,
            family_id=row.family_id,
            context_id=row.context_id,
            zone=row.zone,
            chain_titles=frozenset(normalize_title(title) for title in row.chain_titles or ()),
            chapter_item_id=row.chapter_item_id,
            nominal=nominal,
            net=net,
            weight=row.weight,
            lump=(
                lump_check(
                    family_id=row.family_id,
                    unit_id=row.unit_id,
                    unit_price=row.unit_cost_total,
                    weight=row.weight,
                    vat_base=row.vat_base,
                    override=overrides.get(row.position_item_id),
                    rates=rates,
                )
                if row.position_item_id in applicable
                else None
            ),
            family_title=row.family_title,
        )
    return lines


def load_link_values(db: Session, pairs: Collection[tuple[int, int]]) -> dict[tuple[int, int], ParamText]:
    """Значения параметров контекстов строк: `(position_item_id, parameter_id)` -> `ParamText`.

    Один запрос. Пары без значения в ответе нет: ни записи контекста, ни записи с источником
    `none` (не уточнено). Запись `path_conflict` отдаётся с `text = None` и своим источником:
    число из неё не берётся (спека §2.7 п. 1). Значение не из списка — `text = off_list_text`
    и `off_list = True`.
    """
    wanted = sorted(set(pairs))
    if not wanted:
        return {}
    rows = db.execute(
        sa.select(
            ContextMember.position_item_id,
            ContextParameterValue.parameter_id,
            FamilyParameterValue.value,
            ContextParameterValue.off_list_text,
            FamilyParameter.name,
            ContextParameterValue.source,
            FamilyParameterValue.composite_kind,
        )
        .select_from(ContextMember)
        .join(ContextParameterValue, ContextParameterValue.context_id == ContextMember.context_id)
        .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id)
        .where(
            sa.tuple_(ContextMember.position_item_id, ContextParameterValue.parameter_id).in_(wanted),
            ContextParameterValue.source != ValueSource.none.value,
        )
    ).all()
    result: dict[tuple[int, int], ParamText] = {}
    for item_id, parameter_id, value, off_list_text, name, source, composite_kind in rows:
        off_list = source == ValueSource.off_list.value
        result[(item_id, parameter_id)] = ParamText(
            text=off_list_text if off_list else value,
            parameter_name=name,
            source=source,
            off_list=off_list,
            composite=composite_kind is not None,
        )
    return result

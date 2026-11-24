"""Отметки «не аналог» и «ошибка в цене»: загрузчики (спека «Не аналог» §1 п. 3).

Модуль только читает. Отметки тендера — одним запросом на сцену, «ошибка в цене» — одним запросом
по идентификаторам строк договоров (пустой набор — без запроса). Кэша нет.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from models import (
    CatalogContext,
    ContextMember,
    Estimate,
    Lot,
    NotAnalogMark,
    Offer,
    PositionItem,
    PriceErrorMark,
    Proposal,
    TenderRound,
    User,
)
from services.zone_rules import normalize_title

CODE_POSITION_ITEM_NOT_FOUND = "position_item_not_found"
CODE_NOT_ANALOG_NOT_APPLICABLE = "not_analog_not_applicable"

#: Ключ строки формы: нормализованное наименование строки КП и единица (`None` — без единицы).
FormKey = tuple[str, int | None]


@dataclass(frozen=True)
class Mark:
    """Отметка с подписью: вид, причина, автор (идентификатор и почта) и время."""

    kind: Literal["price_error", "tender"]
    reason: str
    set_by_id: int
    set_by_email: str
    set_at: datetime


def load_not_analog_marks(db: Session, tender_id: int) -> Mapping[FormKey, Mapping[int, Mark]]:
    """Отметки тендера: ключ формы -> строка договора -> `Mark` (`kind="tender"`). Один запрос."""
    rows = db.execute(
        sa.select(
            NotAnalogMark.form_title,
            NotAnalogMark.form_unit_id,
            NotAnalogMark.position_item_id,
            NotAnalogMark.reason,
            NotAnalogMark.set_by,
            User.email,
            NotAnalogMark.set_at,
        )
        .join(User, User.id == NotAnalogMark.set_by)
        .where(NotAnalogMark.tender_id == tender_id)
    ).all()
    result: dict[FormKey, dict[int, Mark]] = defaultdict(dict)
    for title, unit_id, item_id, reason, set_by, email, set_at in rows:
        result[(title, unit_id)][item_id] = Mark("tender", reason, set_by, email, set_at)
    return result


def load_price_error_marks(db: Session, position_item_ids: Collection[int]) -> dict[int, Mark]:
    """«Ошибка в цене» по строкам договоров: `position_item_id` -> `Mark` (`kind="price_error"`);
    строки без отметки в ответе нет. Пустой набор — без запроса."""
    ids = sorted(set(position_item_ids))
    if not ids:
        return {}
    rows = db.execute(
        sa.select(
            PriceErrorMark.position_item_id,
            PriceErrorMark.reason,
            PriceErrorMark.set_by,
            User.email,
            PriceErrorMark.set_at,
        )
        .join(User, User.id == PriceErrorMark.set_by)
        .where(PriceErrorMark.position_item_id.in_(ids))
    ).all()
    return {
        item_id: Mark("price_error", reason, set_by, email, set_at)
        for item_id, reason, set_by, email, set_at in rows
    }


def _lock_contract_item(db: Session, position_item_id: int) -> None:
    """Строка под `FOR UPDATE` и проверка состава (спека «Не аналог» §2 п. 3): нужна строка-работа
    сметы договора вне контекста `SYSTEM`. Вариант, цена, база НДС и «комплект» отметку не запрещают."""
    locked = db.execute(
        sa.select(PositionItem.id, PositionItem.is_chapter)
        .where(PositionItem.id == position_item_id)
        .with_for_update()
    ).one_or_none()
    if locked is None:
        raise DomainError(
            404, f"Строка сметы {position_item_id} не найдена.", code=CODE_POSITION_ITEM_NOT_FOUND
        )
    row = db.execute(
        sa.select(Estimate.contract_id, CatalogContext.semantic_kind)
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(PositionItem.id == position_item_id)
    ).first()
    if locked[1] or row is None or row.contract_id is None or row.semantic_kind == "SYSTEM":
        raise DomainError(
            422,
            "Отметка к этой строке неприменима: нужна строка-работа сметы договора, не системная.",
            code=CODE_NOT_ANALOG_NOT_APPLICABLE,
        )


def _kp_form_key(
    db: Session, tender_id: int, round_id: int, offer_id: int, kp_item_id: int
) -> FormKey:
    """Ключ формы строки КП (не глава) оффера этапа этого тендера: нормализованное наименование и
    `position_items.unit_id` — то же, что кладёт в `KpLine.form_key` загрузчик.

    Raises:
        DomainError 404: цепочка тендер, этап, оффер, смета, строка не сходится.
    """
    row = db.execute(
        sa.select(PositionItem.job_title_in_proposal, PositionItem.unit_id)
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(Offer, Offer.id == Estimate.offer_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(
            PositionItem.id == kp_item_id,
            PositionItem.is_chapter.is_(False),
            Offer.id == offer_id,
            TenderRound.id == round_id,
            TenderRound.tender_id == tender_id,
        )
    ).one_or_none()
    if row is None:
        raise DomainError(404, f"Строка {kp_item_id} не найдена в КП оффера {offer_id}.")
    return normalize_title(row[0]), row[1]


def _mark(kind: Literal["price_error", "tender"], row, db: Session) -> Mark:
    email = db.execute(sa.select(User.email).where(User.id == row.set_by)).scalar_one()
    return Mark(kind, row.reason, row.set_by, email, row.set_at)


def mark_view(mark: Mark) -> dict:
    """`MarkView` спеки «Не аналог» §7 п. 1."""
    return {
        "kind": mark.kind,
        "reason": mark.reason,
        "set_by": {"id": mark.set_by_id, "email": mark.set_by_email},
        "set_at": iso(mark.set_at),
    }


def set_price_error(db: Session, *, position_item_id: int, reason: str, actor_id: int) -> Mark:
    """«Ошибка в цене» строки договора (спека §2 п. 1, 3, 4). Та же причина — запись не трогается
    (автор и время прежние); иная — причина, автор и `now()`. Не коммитит."""
    _lock_contract_item(db, position_item_id)
    row = db.get(PriceErrorMark, position_item_id, populate_existing=True)
    if row is None:
        row = PriceErrorMark(
            position_item_id=position_item_id,
            reason=reason,
            set_by=actor_id,
            set_at=dt.datetime.now(dt.UTC),
        )
        db.add(row)
    elif row.reason != reason:
        row.reason = reason
        row.set_by = actor_id
        row.set_at = dt.datetime.now(dt.UTC)
    db.flush()
    db.refresh(row)  # время — как из базы, одинаково для первой и повторной записи
    return _mark("price_error", row, db)


def clear_price_error(db: Session, *, position_item_id: int) -> None:
    """Снять «ошибку в цене»; без записи — ничего. Строки нет — 404. Не коммитит."""
    locked = db.execute(
        sa.select(PositionItem.id).where(PositionItem.id == position_item_id).with_for_update()
    ).one_or_none()
    if locked is None:
        raise DomainError(
            404, f"Строка сметы {position_item_id} не найдена.", code=CODE_POSITION_ITEM_NOT_FOUND
        )
    db.execute(sa.delete(PriceErrorMark).where(PriceErrorMark.position_item_id == position_item_id))
    db.flush()


def _tender_mark_row(
    db: Session, tender_id: int, form_key: FormKey, position_item_id: int
) -> NotAnalogMark | None:
    title, unit_id = form_key
    return db.execute(
        sa.select(NotAnalogMark)
        .where(
            NotAnalogMark.tender_id == tender_id,
            NotAnalogMark.form_title == title,
            NotAnalogMark.form_unit_id.is_(None)
            if unit_id is None
            else NotAnalogMark.form_unit_id == unit_id,
            NotAnalogMark.position_item_id == position_item_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def set_not_analog(
    db: Session,
    *,
    tender_id: int,
    round_id: int,
    offer_id: int,
    kp_item_id: int,
    position_item_id: int,
    reason: str,
    actor_id: int,
) -> Mark:
    """«Не аналог в этом тендере»: отметка на ключ формы строки КП (спека §2 п. 2–4). Та же
    причина — запись не трогается; иная — причина, автор и `now()`. Не коммитит."""
    form_key = _kp_form_key(db, tender_id, round_id, offer_id, kp_item_id)
    _lock_contract_item(db, position_item_id)
    row = _tender_mark_row(db, tender_id, form_key, position_item_id)
    if row is None:
        row = NotAnalogMark(
            tender_id=tender_id,
            form_title=form_key[0],
            form_unit_id=form_key[1],
            position_item_id=position_item_id,
            reason=reason,
            set_by=actor_id,
            set_at=dt.datetime.now(dt.UTC),
        )
        db.add(row)
    elif row.reason != reason:
        row.reason = reason
        row.set_by = actor_id
        row.set_at = dt.datetime.now(dt.UTC)
    db.flush()
    db.refresh(row)
    return _mark("tender", row, db)


def clear_not_analog(
    db: Session,
    *,
    tender_id: int,
    round_id: int,
    offer_id: int,
    kp_item_id: int,
    position_item_id: int,
) -> None:
    """Снять тендерную отметку; без записи — ничего. Строка КП не в оффере или строки договора
    нет — 404. Не коммитит."""
    form_key = _kp_form_key(db, tender_id, round_id, offer_id, kp_item_id)
    locked = db.execute(
        sa.select(PositionItem.id).where(PositionItem.id == position_item_id).with_for_update()
    ).one_or_none()
    if locked is None:
        raise DomainError(
            404, f"Строка сметы {position_item_id} не найдена.", code=CODE_POSITION_ITEM_NOT_FOUND
        )
    row = _tender_mark_row(db, tender_id, form_key, position_item_id)
    if row is not None:
        db.delete(row)
    db.flush()

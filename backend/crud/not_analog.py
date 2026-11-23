"""Отметки «не аналог» и «ошибка в цене»: загрузчики (спека «Не аналог» §1 п. 3).

Модуль только читает. Отметки тендера — одним запросом на сцену, «ошибка в цене» — одним запросом
по идентификаторам строк договоров (пустой набор — без запроса). Кэша нет.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import NotAnalogMark, PriceErrorMark, User

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

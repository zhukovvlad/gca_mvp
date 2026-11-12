"""Запись «Журнала ИИ»: что система сделала сама (спека упрощения каталога §2.7).

Строка — `catalog_auto_actions`; состав `payload` зависит от вида (`split`, `value_added`,
`composite`, `equated`). Функция ничего не коммитит: транзакцией владеет вызывающий.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import CatalogAutoAction


def record_action(
    db: Session, *, kind: str, family_id: int, payload: dict,
    context_id: int | None = None, parameter_id: int | None = None,
    value_id: int | None = None,
) -> int:
    """Пишет одну запись журнала и возвращает её `id`. Вид и пустой `payload`
    проверяет база (`ck_catalog_auto_actions_kind`, `NOT NULL`)."""
    row = CatalogAutoAction(
        kind=kind, family_id=family_id, payload=payload, context_id=context_id,
        parameter_id=parameter_id, value_id=value_id,
    )
    db.add(row)
    db.flush()
    return row.id

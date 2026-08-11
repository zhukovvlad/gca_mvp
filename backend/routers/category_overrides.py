"""Роутер ручного разноса разделов сметы по статьям (спека разноса §2.7).

Права: `member` тоже вправе — решение ограничено одной сметой и обратимо, тогда
как ручной матчинг правит ОБЩИЙ каталог и тоже открыт `member`. Разрушительное
действие (`replace`) закрыто на `admin` в своём роутере. Поэтому `require_admin`
здесь нет, только аутентификация (навешена в main.py).

Транзакцию ведёт роутер: сервис только пишет и пересчитывает.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import User
from services.category_override import CategoryOverrideError, clear_override, set_override

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/estimates", tags=["category-overrides"])

#: Код ошибки сервиса → HTTP. `not_found` покрывает и гонку с заменой сметы:
#: после ожидания блокировки строки просто нет, и отличить это от изначально
#: отсутствовавшей сметы нельзя (спека §2.5). `409` принадлежит ТОЛЬКО
#: `structure_disabled` — так сказано в спеке §2.7.
#:
#: `mapping_broken` в карте ОТСУТСТВУЕТ намеренно: расхождение разобранной копии
#: файла со строками сметы — нарушение целостности НАШИХ данных, а не конфликт
#: пользовательского действия. Пользователь ничего не может с ним сделать, и
#: `409` предложил бы ему повторить попытку, которая обречена. Такая ошибка
#: должна дойти до `500` и до логов — этим и занимается `_apply`.
_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_a_chapter": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "structure_disabled": status.HTTP_409_CONFLICT,
}


class OverrideRequest(BaseModel):
    work_category_id: int
    note: str | None = Field(default=None, max_length=2000)


def _apply(db: Session, action, **kwargs):
    try:
        result = action(db, **kwargs)
        db.commit()
    except CategoryOverrideError as exc:
        db.rollback()
        http_status = _STATUS.get(exc.code)
        if http_status is None:
            # `mapping_broken` — не пользовательский конфликт, а нарушение
            # целостности наших данных: логируем и отдаём 500, потому что повторять
            # такой запрос бессмысленно, а тишина скрыла бы поломку.
            log.error("Разнос статей: %s", exc, exc_info=True)
            raise
        raise HTTPException(http_status, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return {
        "chapters_updated": result.chapters_updated,
        "additional_works_updated": result.additional_works_updated,
        "chapters_manual": result.chapters_manual,
    }


@router.put("/{estimate_id}/category-overrides/{position_item_id}")
def put_override(
    estimate_id: int,
    position_item_id: int,
    body: OverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Назначить статью строке-разделу и пересчитать смету целиком.

    Повторный запрос с теми же статьёй и примечанием — no-op: аудит не двигается
    (спека §2.2).
    """
    return _apply(
        db,
        set_override,
        estimate_id=estimate_id,
        position_item_id=position_item_id,
        work_category_id=body.work_category_id,
        note=body.note,
        user_id=current_user.id,
    )


@router.delete("/{estimate_id}/category-overrides/{position_item_id}")
def delete_override(
    estimate_id: int,
    position_item_id: int,
    db: Session = Depends(get_db),
):
    """Снять решение и пересчитать смету целиком: разделы, ставшие
    нераспределёнными, получают `NULL` (спека §2.3)."""
    return _apply(
        db, clear_override, estimate_id=estimate_id, position_item_id=position_item_id
    )

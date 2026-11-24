"""Маршруты отметок «ошибка в цене» и «не аналог в этом тендере» (спека «Не аналог» §2).

Только `admin` (`require_admin`). Транзакцию ведёт роутер: коммит на успехе, откат на отказе.
Ответ `PUT` — `MarkView` (спека §7 п. 1).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

import crud.not_analog as crud_not_analog
from auth import require_admin
from crud.common import DomainError
from database import get_db
from models import User
from routers.domain_errors import raise_domain_error

router = APIRouter(prefix="/api/v1", tags=["not-analog"])

_NOT_ANALOG = (
    "/tenders/{tender_id}/rounds/{round_id}/our-price/offers/{offer_id}"
    "/lines/{kp_item_id}/not-analog/{position_item_id}"
)


class MarkRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 300:
            raise ValueError("Причина — от 1 до 300 символов.")
        return value


@router.put("/position-items/{position_item_id}/price-error")
def set_price_error_route(
    position_item_id: int,
    body: MarkRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Ошибка в цене» строки договора; та же причина — без смены автора и времени."""
    try:
        mark = crud_not_analog.set_price_error(
            db, position_item_id=position_item_id, reason=body.reason, actor_id=admin.id
        )
        response = crud_not_analog.mark_view(mark)
        db.commit()
        return response
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise


@router.delete("/position-items/{position_item_id}/price-error", status_code=status.HTTP_204_NO_CONTENT)
def clear_price_error_route(
    position_item_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Снять отметку; без записи — тоже 204."""
    try:
        crud_not_analog.clear_price_error(db, position_item_id=position_item_id)
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(_NOT_ANALOG)
def set_not_analog_route(
    tender_id: int,
    round_id: int,
    offer_id: int,
    kp_item_id: int,
    position_item_id: int,
    body: MarkRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Не аналог в этом тендере»: ключ формы берётся со строки КП."""
    try:
        mark = crud_not_analog.set_not_analog(
            db,
            tender_id=tender_id,
            round_id=round_id,
            offer_id=offer_id,
            kp_item_id=kp_item_id,
            position_item_id=position_item_id,
            reason=body.reason,
            actor_id=admin.id,
        )
        response = crud_not_analog.mark_view(mark)
        db.commit()
        return response
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise


@router.delete(_NOT_ANALOG, status_code=status.HTTP_204_NO_CONTENT)
def clear_not_analog_route(
    tender_id: int,
    round_id: int,
    offer_id: int,
    kp_item_id: int,
    position_item_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Снять тендерную отметку; без записи — тоже 204."""
    try:
        crud_not_analog.clear_not_analog(
            db,
            tender_id=tender_id,
            round_id=round_id,
            offer_id=offer_id,
            kp_item_id=kp_item_id,
            position_item_id=position_item_id,
        )
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)

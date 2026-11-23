"""Маршруты поправки администратора «комплект или ставка» (спека строк-сумм §3).

Отдельный роутер `/api/v1/position-items`: строка договора и строка КП — одна сущность
(`position_items`), а панель знает только `position_item_id`. Только `admin`. Транзакцию ведёт
роутер: коммит на успехе, откат на отказе.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

import crud.lump_sum as crud_lump_sum
from auth import require_admin
from crud.common import DomainError, iso
from database import get_db
from models import User
from routers.domain_errors import raise_domain_error
from services.lump_sum import LumpVerdict

router = APIRouter(prefix="/api/v1/position-items", tags=["lump-sum"])


class LumpSumRequest(BaseModel):
    verdict: LumpVerdict


@router.put("/{position_item_id}/lump-sum")
def set_lump_sum_route(
    position_item_id: int,
    body: LumpSumRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Поправка «это комплект» / «это ставка»; то же значение — без смены автора и времени."""
    try:
        row = crud_lump_sum.set_lump_override(
            db, position_item_id=position_item_id, verdict=body.verdict, actor_id=admin.id
        )
        author = db.get(User, row.set_by)
        response = {
            "position_item_id": row.position_item_id,
            "verdict": row.verdict,
            "set_by": {"id": author.id, "email": author.email},
            "set_at": iso(row.set_at),
        }
        db.commit()
        return response
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise


@router.delete("/{position_item_id}/lump-sum", status_code=status.HTTP_204_NO_CONTENT)
def clear_lump_sum_route(
    position_item_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Как решило правило»: поправка снимается; без записи — тоже 204."""
    try:
        crud_lump_sum.clear_lump_override(db, position_item_id=position_item_id)
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)

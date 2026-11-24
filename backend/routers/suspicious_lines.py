"""Маршруты «подозрительных строк договоров» (спека `2026-11-24-suspicious-lines-design.md` §3).

Только `admin` (`require_admin`; у `member` — 403). Список и счётчики читают индекс из кэша;
решения «не ошибка» (`PUT`/`DELETE …/not-error`) читают свежий индекс под замком строки.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

import crud.suspicious_lines as crud_suspicious
from auth import require_admin
from crud.common import DomainError
from database import get_db
from models import User
from routers.domain_errors import raise_domain_error
from services.status_cache import suspicion_cache
from services.suspicious_lines import Basis, OutlierBasis, TwinBasis

router = APIRouter(prefix="/api/v1/suspicious-lines", tags=["suspicious-lines"])
decisions_router = APIRouter(prefix="/api/v1/position-items", tags=["suspicious-lines"])


class OutlierKey(BaseModel):
    kind: Literal["outlier"]
    family_id: int
    unit_id: int
    direction: Literal["up", "down"]


class TwinKey(BaseModel):
    kind: Literal["twin"]
    neighbor_item_id: int


BasisKey = Annotated[OutlierKey | TwinKey, Field(discriminator="kind")]


class NotErrorRequest(BaseModel):
    reason: str
    bases: list[BasisKey] = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 300:
            raise ValueError("Причина — от 1 до 300 символов.")
        return value

    def basis_set(self) -> list[Basis]:
        return [
            OutlierBasis(family_id=b.family_id, unit_id=b.unit_id, direction=b.direction)
            if isinstance(b, OutlierKey)
            else TwinBasis(neighbor_item_id=b.neighbor_item_id)
            for b in self.bases
        ]


@router.get("")
def list_suspicious_lines_route(
    contract_id: int | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список подозрительных строк: не разобранные и разобранные; `contract_id` — только договор (404, если его нет)."""
    try:
        return crud_suspicious.suspicious_list(db, contract_id=contract_id)
    except DomainError as exc:
        raise_domain_error(exc)


@router.get("/counts")
def suspicious_counts_route(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Счётчики не разобранных: всего и по договорам (договоры с нулём не перечисляются)."""
    return crud_suspicious.suspicious_counts(db)


@decisions_router.put("/{position_item_id}/not-error")
def set_not_error_route(
    position_item_id: int,
    body: NotErrorRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Не ошибка»: решения по основаниям, которые администратор видел; ответ — все решения строки."""
    try:
        records = crud_suspicious.set_not_error(
            db,
            position_item_id=position_item_id,
            reason=body.reason,
            bases=body.basis_set(),
            actor_id=admin.id,
        )
        response = crud_suspicious.decisions_view(db, records)
        db.commit()
        suspicion_cache.invalidate()
        return response
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise


@decisions_router.delete("/{position_item_id}/not-error", status_code=status.HTTP_204_NO_CONTENT)
def clear_not_error_route(
    position_item_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Вернуть»: удалить все решения «не ошибка» строки; без решений — тоже 204."""
    try:
        crud_suspicious.clear_not_error(db, position_item_id=position_item_id)
        db.commit()
        suspicion_cache.invalidate()
    except DomainError as exc:
        db.rollback()
        raise_domain_error(exc)
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)

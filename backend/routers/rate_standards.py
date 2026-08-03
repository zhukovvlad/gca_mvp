"""Роутер нормативов расценок (AGENTS.md §4, §7.3).

Права: чтение — любому аутентифицированному, изменение — `admin`. Здесь это не
решение фазы 5, а буква §3: нормативы перечислены за `admin` прямо.

Деньги (`standard_unit_rate`, `inflation_index`) уезжают строками — все ответы
идут через `decimal_json` (см. `responses.py`), а `float` на входе отклоняется.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from crud import rate_standards as crud_standards
from crud.common import DomainError
from database import get_db
from models import User
from responses import decimal_json

router = APIRouter(prefix="/api/v1/rate-standards", tags=["rate-standards"])

#: Денежные и коэффициентные поля: float для них запрещён (§3).
_EXACT_FIELDS = ("standard_unit_rate", "inflation_index")


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


class _ExactNumbersMixin(BaseModel):
    """Отклоняет `float` в точных числах — ставка и коэффициент (§3).

    Коэффициент инфляции тоже точный: переутверждение считает `ставка × индекс`,
    и float в множителе испортил бы результат ровно так же, как в самой ставке.
    """

    @field_validator(*_EXACT_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _reject_float(cls, value):
        if isinstance(value, float):
            raise ValueError(
                "Передавайте значение строкой (например \"1234.56\"), а не числом с "
                "плавающей точкой: float теряет точность."
            )
        return value


class RateStandardCreate(_ExactNumbersMixin):
    catalog_position_id: int
    rate_class_id: int
    standard_unit_rate: Decimal
    valid_from: dt.date
    valid_to: dt.date | None = None
    inflation_index: Decimal | None = None
    approved_by: str | None = None
    approved_at: dt.datetime | None = None
    note: str | None = None


class RateStandardUpdate(_ExactNumbersMixin):
    standard_unit_rate: Decimal | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    inflation_index: Decimal | None = None
    approved_by: str | None = None
    approved_at: dt.datetime | None = None
    note: str | None = None


class ReapproveRequest(_ExactNumbersMixin):
    """Переутверждение: новый период плюс ставка либо коэффициент (§7.3)."""
    valid_from: dt.date
    standard_unit_rate: Decimal | None = None
    inflation_index: Decimal | None = None
    approved_by: str | None = None
    approved_at: dt.datetime | None = None
    note: str | None = None


#: Поля правки, для которых `null` бессмысленен: колонки NOT NULL.
_NON_NULLABLE = ("standard_unit_rate", "valid_from")


@router.get("")
def list_rate_standards(
    rate_class_id: int | None = Query(default=None),
    catalog_position_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    on_date: dt.date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Нормативы с фильтрами; `on_date` — действующие на дату (§7.3)."""
    return decimal_json(
        crud_standards.list_rate_standards(
            db,
            rate_class_id=rate_class_id,
            catalog_position_id=catalog_position_id,
            q=q,
            on_date=on_date,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{standard_id}")
def get_rate_standard(standard_id: int, db: Session = Depends(get_db)):
    try:
        return decimal_json(crud_standards.get_rate_standard_dict(db, standard_id))
    except DomainError as e:
        _raise(e)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_rate_standard(
    body: RateStandardCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Создать норматив. 400 — пересечение периодов (EXCLUDE), 422 — ставка/период."""
    try:
        created = crud_standards.create_rate_standard(
            db,
            catalog_position_id=body.catalog_position_id,
            rate_class_id=body.rate_class_id,
            standard_unit_rate=body.standard_unit_rate,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            inflation_index=body.inflation_index,
            approved_by=body.approved_by,
            approved_at=body.approved_at,
            note=body.note,
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(created, status.HTTP_201_CREATED)


@router.patch("/{standard_id}")
def update_rate_standard(
    standard_id: int,
    body: RateStandardUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Исправить норматив. Это НЕ переутверждение — для него `/reapprove`."""
    fields = body.model_dump(exclude_unset=True)
    for name in _NON_NULLABLE:
        if name in fields and fields[name] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Поле {name} не может быть null."
            )
    try:
        updated = crud_standards.update_rate_standard(db, standard_id, **fields)
    except DomainError as e:
        _raise(e)
    return decimal_json(updated)


@router.post("/{standard_id}/reapprove")
def reapprove_rate_standard(
    standard_id: int,
    body: ReapproveRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Переутвердить: закрыть прежний период и открыть новый в одной транзакции (§4).

    Возвращает обе строки — `previous` и `current`, чтобы экран показал, что
    история не переписана, а продолжена.
    """
    try:
        result = crud_standards.reapprove_rate_standard(
            db,
            standard_id,
            valid_from=body.valid_from,
            standard_unit_rate=body.standard_unit_rate,
            inflation_index=body.inflation_index,
            approved_by=body.approved_by,
            approved_at=body.approved_at,
            note=body.note,
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(result)


@router.delete("/{standard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_standard(
    standard_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        crud_standards.delete_rate_standard(db, standard_id)
    except DomainError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

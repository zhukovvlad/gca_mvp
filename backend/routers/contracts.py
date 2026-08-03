"""Роутер договоров генподряда (AGENTS.md §7.1).

Тонкий слой над `crud/contracts.py`. Права: чтение — любому аутентифицированному,
изменение — `require_admin` (решение фазы 5 §6.2).

**Деньги приходят строками, не числами с плавающей точкой** (§3). Валидатор
`_reject_float` отклоняет `float` на входе явно: Pydantic молча превратил бы
`1234.56` в `Decimal`, и на глаз всё выглядело бы правильно — а потерянные на
округлении копейки нашлись бы уже в сумме по договору.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from crud import contracts as crud_contracts
from crud.common import DomainError
from database import get_db
from models import User
from responses import decimal_json

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


class _MoneyMixin(BaseModel):
    """Общие правила для денежного поля. `check_fields=False` — поле объявлено
    в наследниках, а не здесь."""

    @field_validator("total_amount", mode="before", check_fields=False)
    @classmethod
    def _reject_float(cls, value):
        """Деньги — строка или целое, но не float (§3, «никаких float»)."""
        if isinstance(value, float):
            raise ValueError(
                "Сумму передавайте строкой (например \"1234.56\"), а не числом с "
                "плавающей точкой: float теряет копейки."
            )
        return value

    @field_validator("total_amount", check_fields=False)
    @classmethod
    def _non_negative(cls, value: Decimal | None):
        """CHECK ck_contracts_total_amount_non_negative — понятным 422, не 500."""
        if value is not None and value < 0:
            raise ValueError("Сумма договора не может быть отрицательной.")
        return value


class ContractCreate(_MoneyMixin):
    object_id: int
    contractor_id: int
    contract_number: str
    signed_date: dt.date
    #: Не передан — берётся класс объекта (снимок на момент создания, §4).
    rate_class_id: int | None = None
    title: str | None = None
    signer: str | None = None
    total_amount: Decimal | None = None
    notes: str | None = None


class ContractUpdate(_MoneyMixin):
    object_id: int | None = None
    contractor_id: int | None = None
    rate_class_id: int | None = None
    contract_number: str | None = None
    signed_date: dt.date | None = None
    title: str | None = None
    signer: str | None = None
    total_amount: Decimal | None = None
    notes: str | None = None


#: Поля карточки, для которых `null` в PATCH бессмысленен: колонки NOT NULL.
_NON_NULLABLE = (
    "object_id",
    "contractor_id",
    "rate_class_id",
    "contract_number",
    "signed_date",
)


@router.get("")
def list_contracts(
    q: str | None = Query(default=None),
    object_id: int | None = Query(default=None),
    contractor_id: int | None = Query(default=None),
    rate_class_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Список договоров с фильтрами (§7.1).

    `decimal_json` обязателен: в ответе есть `total_amount` (§3, деньги строками).
    """
    return decimal_json(
        crud_contracts.list_contracts(
            db,
            q=q,
            object_id=object_id,
            contractor_id=contractor_id,
            rate_class_id=rate_class_id,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{contract_id}")
def get_contract(contract_id: int, db: Session = Depends(get_db)):
    """Карточка договора: реквизиты, класс, текущие сметы."""
    try:
        return decimal_json(crud_contracts.get_contract_dict(db, contract_id))
    except DomainError as e:
        _raise(e)


@router.get("/{contract_id}/import-jobs")
def list_contract_import_jobs(contract_id: int, db: Session = Depends(get_db)):
    """История загрузок и замен договора; `is_current` отмечает актуальную смету."""
    try:
        return crud_contracts.list_contract_import_jobs(db, contract_id)
    except DomainError as e:
        _raise(e)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_contract(
    body: ContractCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        body_out = crud_contracts.create_contract(
            db,
            object_id=body.object_id,
            contractor_id=body.contractor_id,
            contract_number=body.contract_number,
            signed_date=body.signed_date,
            rate_class_id=body.rate_class_id,
            title=body.title,
            signer=body.signer,
            total_amount=body.total_amount,
            notes=body.notes,
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(body_out, status.HTTP_201_CREATED)


@router.patch("/{contract_id}")
def update_contract(
    contract_id: int,
    body: ContractUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    fields = body.model_dump(exclude_unset=True)
    for name in _NON_NULLABLE:
        if name in fields and fields[name] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Поле {name} не может быть null."
            )
    try:
        updated = crud_contracts.update_contract(
            db,
            contract_id,
            **{
                name: fields[name]
                for name in (
                    "object_id",
                    "contractor_id",
                    "rate_class_id",
                    "contract_number",
                    "title",
                    "signer",
                    "signed_date",
                    "total_amount",
                    "notes",
                )
                if name in fields
            },
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(updated)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Удалить договор. 409, если есть сметы или задания импорта (аудит, §5)."""
    try:
        crud_contracts.delete_contract(db, contract_id)
    except DomainError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Роутер правки ставок НДС сметы (спека пересчёта §2.7).

Право — `admin`: правка меняет все деньги договора сразу, включая выгрузку для
банка. Это тот же вес, что у замены смет и нормативов (`AGENTS.md` §3), и именно
поэтому здесь `require_admin`, в отличие от разноса статей.

Транзакцию ведёт роутер, сервис только пишет — та же раскладка, что у разноса.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from database import get_db
from models import User
from services.estimate_vat import UNSET, EstimateVatError, set_vat_rates

router = APIRouter(prefix="/api/v1/estimates", tags=["estimate-vat"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "unknown_base": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

_EXACT_VAT_FIELDS = ("base_override", "target")


class VatRatesRequest(BaseModel):
    """Отсутствующее поле — «не менять», `null` — «снять».

    Различить их можно только через `model_fields_set`: Pydantic кладёт `None` и
    в то, и в другое.
    """

    base_override: Decimal | None = Field(default=None, ge=0, le=100)
    target: Decimal | None = Field(default=None, ge=0, le=100)

    # Имя метода УНИКАЛЬНО по всему дереву наследования (`AGENTS.md` §11):
    # одноимённые валидаторы схлопываются в один слот, и второй исчезает молча.
    # `Decimal | None` без этой проверки принял бы JSON-число с плавающей точкой,
    # то есть ровно тот `float`, который §3 запрещает.
    @field_validator(*_EXACT_VAT_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _reject_float_in_vat_rates(cls, value):
        if isinstance(value, float):
            raise ValueError(
                'Передавайте ставку строкой (например "16.5"), а не числом с '
                "плавающей точкой."
            )
        return value


@router.patch("/{estimate_id}/vat")
def patch_vat_rates(
    estimate_id: int,
    body: VatRatesRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    fields = body.model_fields_set
    try:
        estimate = set_vat_rates(
            db,
            estimate_id=estimate_id,
            base_override=body.base_override if "base_override" in fields else UNSET,
            target=body.target if "target" in fields else UNSET,
            user_id=current_user.id,
        )
        db.commit()
    except EstimateVatError as exc:
        db.rollback()
        raise HTTPException(_STATUS[exc.code], str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "estimate_id": estimate.id,
        "vat_rate_base_override": estimate.vat_rate_base_override,
        "vat_rate_target": estimate.vat_rate_target,
        "vat_rate_updated_at": estimate.vat_rate_updated_at,
    }

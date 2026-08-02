from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from crud.projects import create_reference_price, delete_reference_price, get_reference_prices, update_reference_price
from database import get_db
from models import MaterialClass, ReferencePrice, UnitOfMeasure
from routers.common import resolve_direction_type

router = APIRouter()

class ReferencePriceCreate(BaseModel):
    project_id: int
    material_class_id: int
    unit_id: int
    price: Decimal
    period_start: date
    period_end: date
    source: str | None = None


def _validate_ref_unit(db: Session, material_class_id: int, unit_id: int) -> None:
    unit = db.query(UnitOfMeasure).filter(UnitOfMeasure.id == unit_id).first()
    if unit is None:
        raise HTTPException(status_code=422, detail="Единица измерения не найдена")
    # Validation 1: must be a base unit
    if unit.base_unit_id is not None:
        raise HTTPException(status_code=422, detail="Базовая цена задаётся только в базовой единице (т, м³, м, шт)")
    # Validation 2: dimension must match the class's material_type default unit (when defined)
    mc = db.query(MaterialClass).filter(MaterialClass.id == material_class_id).first()
    if mc is None:
        raise HTTPException(status_code=422, detail="Класс материала не найден")
    if mc.material_type.code == "other":
        raise HTTPException(
            status_code=422,
            detail="Классам типа «Прочее» базовая цена не назначается (направления не образует)",
        )
    default_unit = mc.material_type.default_unit
    if default_unit is not None and default_unit.dimension != unit.dimension:
        raise HTTPException(
            status_code=422,
            detail=f"Размерность единицы ({unit.dimension}) не совпадает с типом материала ({default_unit.dimension})",
        )


@router.get("")
def list_reference_prices(project_id: int | None = None, material_class_id: int | None = None, direction: str | None = None, db: Session = Depends(get_db)):
    resolve_direction_type(db, direction)  # 422 on unknown code
    prices = get_reference_prices(db, project_id, material_class_id, material_type_code=direction)
    return [
        {
            "id": rp.id,
            "project_id": rp.project_id,
            "project_name": rp.project.name,
            "material_class_id": rp.material_class_id,
            "material_class_name": rp.material_class.name,
            "material_type": rp.material_class.material_type.code,
            "unit_id": rp.unit_id,
            "unit_symbol": rp.unit.symbol if rp.unit else None,
            "price": rp.price,
            "period_start": rp.period_start.isoformat(),
            "period_end": rp.period_end.isoformat(),
            "source": rp.source,
        }
        for rp in prices
    ]

@router.post("")
def create_reference_price_route(data: ReferencePriceCreate, db: Session = Depends(get_db)):
    _validate_ref_unit(db, data.material_class_id, data.unit_id)
    rp = create_reference_price(
        db, data.project_id, data.material_class_id, data.price,
        data.period_start, data.period_end, data.source, unit_id=data.unit_id,
    )
    return {"id": rp.id}

class ReferencePriceUpdate(BaseModel):
    price: Decimal | None = None
    period_start: date | None = None
    period_end: date | None = None
    source: str | None = None

    @field_validator("price", "period_start", "period_end", mode="before")
    @classmethod
    def not_null(cls, v, info):
        if v is None:
            raise ValueError(f"{info.field_name} не может быть null")
        return v

@router.patch("/{rp_id}")
def update_reference_price_route(rp_id: int, data: ReferencePriceUpdate, db: Session = Depends(get_db)):
    existing = db.query(ReferencePrice).filter(ReferencePrice.id == rp_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Эталон не найден")
    # §5.3 defense-in-depth: legacy-строка типа «Прочее» (POST уже заблокирован)
    # не должна оставаться редактируемой.
    if existing.material_class.material_type.code == "other":
        raise HTTPException(
            status_code=422,
            detail="Классам типа «Прочее» базовая цена не назначается (направления не образует)",
        )
    fields = data.model_fields_set
    kwargs = {k: getattr(data, k) for k in ("price", "period_start", "period_end", "source") if k in fields}
    rp = update_reference_price(db, rp_id, **kwargs)
    if not rp:
        raise HTTPException(status_code=404, detail="Эталон не найден")
    return {
        "id": rp.id,
        "project_id": rp.project_id,
        "project_name": rp.project.name,
        "material_class_id": rp.material_class_id,
        "material_class_name": rp.material_class.name,
        "material_type": rp.material_class.material_type.code,
        "unit_id": rp.unit_id,
        "unit_symbol": rp.unit.symbol if rp.unit else None,
        "price": rp.price,
        "period_start": rp.period_start.isoformat(),
        "period_end": rp.period_end.isoformat(),
        "source": rp.source,
    }

@router.delete("/{rp_id}")
def delete_reference_price_route(rp_id: int, db: Session = Depends(get_db)):
    rp = delete_reference_price(db, rp_id)
    if not rp:
        raise HTTPException(status_code=404, detail="Эталон не найден")
    return {"message": "Удалено"}

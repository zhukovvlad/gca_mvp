from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import MaterialType, UnitAlias, UnitOfMeasure

router = APIRouter()


@router.get("")
def list_units(db: Session = Depends(get_db)):
    units = db.query(UnitOfMeasure).order_by(UnitOfMeasure.dimension, UnitOfMeasure.code).all()
    return [
        {
            "id": u.id,
            "code": u.code,
            "name": u.name,
            "symbol": u.symbol,
            "dimension": u.dimension.value if hasattr(u.dimension, "value") else u.dimension,
            "base_unit_id": u.base_unit_id,
        }
        for u in units
    ]


@router.get("/{unit_id}/aliases")
def list_unit_aliases(unit_id: int, db: Session = Depends(get_db)):
    if not db.query(UnitOfMeasure).filter(UnitOfMeasure.id == unit_id).first():
        raise HTTPException(status_code=404, detail="Единица не найдена")
    aliases = (
        db.query(UnitAlias)
        .filter(UnitAlias.unit_id == unit_id)
        .order_by(UnitAlias.raw_text)
        .all()
    )
    return [{"id": a.id, "raw_text": a.raw_text, "unit_id": a.unit_id} for a in aliases]


material_types_router = APIRouter()


@material_types_router.get("")
def list_material_types(db: Session = Depends(get_db)):
    types = db.query(MaterialType).order_by(MaterialType.code).all()
    return [
        {
            "id": mt.id,
            "code": mt.code,
            "name": mt.name,
            "default_unit": (
                {"id": mt.default_unit.id, "code": mt.default_unit.code, "symbol": mt.default_unit.symbol}
                if mt.default_unit else None
            ),
        }
        for mt in types
    ]

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from crud.materials import (
    UnknownMaterialType,
    delete_material_class,
    get_material_classes,
    get_or_create_material_class,
)
from database import get_db

router = APIRouter()


class MaterialClassCreate(BaseModel):
    name: str
    material_type: str  # concrete / rebar / other


@router.get("")
def list_material_classes(material_type: str | None = None, db: Session = Depends(get_db)):
    classes = get_material_classes(db, material_type)
    return [
        {"id": mc.id, "name": mc.name, "material_type": mc.material_type.code}
        for mc in classes
    ]


@router.post("")
def create_material_class(data: MaterialClassCreate, db: Session = Depends(get_db)):
    try:
        mc = get_or_create_material_class(db, data.name, data.material_type)
    except UnknownMaterialType:
        raise HTTPException(status_code=422, detail=f"Неизвестный тип материала: {data.material_type}") from None
    return {"id": mc.id, "name": mc.name, "material_type": mc.material_type.code}


@router.delete("/{class_id}")
def delete_material_class_route(class_id: int, db: Session = Depends(get_db)):
    mc = delete_material_class(db, class_id)
    if not mc:
        raise HTTPException(status_code=404, detail="Класс не найден")
    return {"message": "Удалено"}

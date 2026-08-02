from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from crud.compensation_corridors import (
    build_resolved_matrix,
    delete_class_corridor,
    delete_type_corridor,
    set_class_corridor,
    set_type_corridor,
)
from crud.projects import create_project, delete_project, get_projects, update_project
from crud.supplier_exclusions import get_excluded_supplier_ids, set_supplier_excluded
from database import get_db
from models import Document, Invoice, InvoiceItem, MaterialClass, MaterialType, Project, Supplier
from routers.common import resolve_direction_type

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    contract_number: str | None = None


class ExclusionCreate(BaseModel):
    reason: str | None = None


class CorridorUpsert(BaseModel):
    is_compensable: bool
    corridor_pct: Decimal | None = None

    @model_validator(mode="after")
    def validate_pct_logic(self) -> "CorridorUpsert":
        if self.is_compensable and self.corridor_pct is None:
            raise ValueError("corridor_pct обязателен, если is_compensable=True")
        if not self.is_compensable:
            self.corridor_pct = None
        if self.corridor_pct is not None and not (0 <= self.corridor_pct <= 100):
            raise ValueError("corridor_pct должен быть от 0 до 100")
        return self


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = get_projects(db)
    return [{"id": p.id, "name": p.name, "contract_number": p.contract_number, "doc_count": len(p.documents)} for p in projects]

@router.post("")
def create_project_route(data: ProjectCreate, db: Session = Depends(get_db)):
    project = create_project(db, data.name, data.contract_number)
    return {"id": project.id, "name": project.name, "contract_number": project.contract_number}

@router.put("/{project_id}")
def update_project_route(project_id: int, data: ProjectCreate, db: Session = Depends(get_db)):
    project = update_project(db, project_id, data.name, data.contract_number)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"id": project.id, "name": project.name, "contract_number": project.contract_number}

@router.delete("/{project_id}")
def delete_project_route(project_id: int, db: Session = Depends(get_db)):
    project = delete_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"message": "Удалено"}


@router.get("/{project_id}/suppliers")
def list_project_suppliers(project_id: int, direction: str | None = None, db: Session = Depends(get_db)):
    """Список поставщиков проекта с кол-вом счетов. Инвойсы без supplier_id не включаются."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    mt = resolve_direction_type(db, direction)
    q = (
        db.query(
            Supplier.id,
            Supplier.name,
            Supplier.inn,
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.supplier_id == Supplier.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id)
    )
    if mt is not None:
        direction_exists = (
            db.query(InvoiceItem.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id == Invoice.id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
            )
            .exists()
        )
        q = q.filter(direction_exists)
    rows = (
        q.group_by(Supplier.id, Supplier.name, Supplier.inn)
        .order_by(Supplier.name)
        .all()
    )
    return [
        {"id": r.id, "name": r.name, "inn": r.inn, "invoice_count": r.invoice_count}
        for r in rows
    ]


@router.get("/{project_id}/supplier-exclusions")
def list_supplier_exclusions(project_id: int, db: Session = Depends(get_db)):
    """Список supplier_id, исключённых из расчётов для данного проекта."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    return sorted(get_excluded_supplier_ids(db, project_id))


@router.post("/{project_id}/supplier-exclusions/{supplier_id}", status_code=204)
def add_supplier_exclusion(
    project_id: int,
    supplier_id: int,
    data: ExclusionCreate | None = None,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    if not db.query(Supplier).filter(Supplier.id == supplier_id).first():
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    set_supplier_excluded(db, project_id, supplier_id, excluded=True, reason=data.reason if data else None)
    return Response(status_code=204)


@router.delete("/{project_id}/supplier-exclusions/{supplier_id}", status_code=204)
def remove_supplier_exclusion(
    project_id: int,
    supplier_id: int,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    set_supplier_excluded(db, project_id, supplier_id, excluded=False)
    return Response(status_code=204)


# --- Corridors (fallback hierarchy) ---


def _resolve_material_type_id(db: Session, code: str) -> int:
    mt = db.query(MaterialType).filter(MaterialType.code == code).first()
    if mt is None:
        raise HTTPException(status_code=404, detail=f"Тип материала не найден: {code}")
    return mt.id


@router.get("/{project_id}/corridors")
def list_corridors(project_id: int, db: Session = Depends(get_db)):
    """Resolved corridor matrix: all material types + classes with resolved status."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    return build_resolved_matrix(db, project_id)


@router.put("/{project_id}/corridors/type/{material_type}")
def upsert_type_corridor(
    project_id: int,
    material_type: str,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    mt_id = _resolve_material_type_id(db, material_type)
    set_type_corridor(db, project_id, mt_id, data.is_compensable, data.corridor_pct)
    return {"material_type": material_type, "is_compensable": data.is_compensable, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/corridors/type/{material_type}", status_code=204)
def remove_type_corridor(
    project_id: int,
    material_type: str,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    mt_id = _resolve_material_type_id(db, material_type)
    delete_type_corridor(db, project_id, mt_id)
    return Response(status_code=204)


@router.put("/{project_id}/corridors/class/{material_class_id}")
def upsert_class_corridor(
    project_id: int,
    material_class_id: int,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    if not db.query(MaterialClass).filter(MaterialClass.id == material_class_id).first():
        raise HTTPException(status_code=404, detail="Класс материала не найден")
    set_class_corridor(db, project_id, material_class_id, data.is_compensable, data.corridor_pct)
    return {"material_class_id": material_class_id, "is_compensable": data.is_compensable, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/corridors/class/{material_class_id}", status_code=204)
def remove_class_corridor(
    project_id: int,
    material_class_id: int,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    delete_class_corridor(db, project_id, material_class_id)
    return Response(status_code=204)


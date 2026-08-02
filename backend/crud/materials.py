import logging

from sqlalchemy.orm import Session, joinedload

from models import InvoiceItem, MaterialClass, MaterialType, ReferencePrice

logger = logging.getLogger(__name__)

VALID_CALC_ROLES = {"base", "additive", "exclude"}


class UnknownMaterialType(ValueError):
    """Raised when a material_type code is not in the material_types table."""


def _material_type_id_by_code(db: Session, code: str) -> int:
    mt = db.query(MaterialType).filter(MaterialType.code == code).first()
    if mt is None:
        raise UnknownMaterialType(code)
    return mt.id


# --- Material Classes ---

def get_material_classes(db: Session, material_type: str | None = None) -> list[MaterialClass]:
    q = (
        db.query(MaterialClass)
        .options(joinedload(MaterialClass.material_type))
        .join(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .order_by(MaterialType.code, MaterialClass.name)
    )
    if material_type:
        q = q.filter(MaterialType.code == material_type)
    return q.all()


def get_material_class(db: Session, class_id: int):
    return db.query(MaterialClass).filter(MaterialClass.id == class_id).first()


def get_or_create_material_class(
    db: Session, name: str, material_type: str, calc_role: str = "base", *, commit: bool = True
) -> MaterialClass:
    """Найти или создать класс материала.

    commit=True (по умолчанию) — самостоятельная транзакция (ручные CRUD-пути).
    commit=False — только flush, чтобы остаться в транзакции вызывающего (фаза B).
    """
    if calc_role not in VALID_CALC_ROLES:
        raise ValueError(f"Unknown calc_role {calc_role!r}; allowed: {sorted(VALID_CALC_ROLES)}")
    material_type_id = _material_type_id_by_code(db, material_type)
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type_id == material_type_id
    ).first()
    if not mc:
        mc = MaterialClass(name=name, material_type_id=material_type_id, calc_role=calc_role)
        db.add(mc)
        if commit:
            db.commit()
            db.refresh(mc)
        else:
            db.flush()
    elif mc.calc_role != calc_role:
        # Preserved intentionally: the DB record represents a human-reviewed classification;
        # auto-update would allow LLM hallucinations to corrupt it.
        # To reclassify: delete the MaterialClass record via DELETE /api/material-classes/{id}
        # so the next parse recreates it with the correct calc_role, or update directly in the DB.
        logger.warning(
            "get_or_create_material_class: class %r/%r found with calc_role=%r, "
            "but caller expects %r — stored value preserved; "
            "to reclassify, delete the record via DELETE /api/material-classes/{id} "
            "and re-parse, or update directly in the DB",
            name, material_type, mc.calc_role, calc_role,
        )
    return mc


def delete_material_class(db: Session, class_id: int):
    mc = get_material_class(db, class_id)
    if mc:
        db.query(InvoiceItem).filter(InvoiceItem.material_class_id == class_id).update(
            {InvoiceItem.material_class_id: None}, synchronize_session=False
        )
        db.query(ReferencePrice).filter(ReferencePrice.material_class_id == class_id).delete()
        db.delete(mc)
        db.commit()
    return mc

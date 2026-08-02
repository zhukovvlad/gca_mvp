from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CompensationCorridor, MaterialClass, MaterialType


def get_corridor_rows(db: Session, project_id: int) -> list[CompensationCorridor]:
    """All corridor rows for project (type-level + class-level)."""
    return (
        db.query(CompensationCorridor)
        .filter(CompensationCorridor.project_id == project_id)
        .all()
    )


def get_corridor_map(
    db: Session, project_id: int,
) -> tuple[dict[int, CompensationCorridor], dict[int, CompensationCorridor]]:
    """Single query → (by_class, by_type) dicts. by_type keyed by material_type_id."""
    rows = get_corridor_rows(db, project_id)
    by_class = {r.material_class_id: r for r in rows if r.material_class_id is not None}
    by_type = {r.material_type_id: r for r in rows if r.material_type_id is not None}
    return by_class, by_type


def resolve_corridor(
    by_class: dict[int, CompensationCorridor],
    by_type: dict[int, CompensationCorridor],
    class_id: int,
    material_type_id: int,
) -> tuple[bool | None, Decimal | None]:
    """Resolve corridor for a material class: class → type → None (not compensable).

    Returns (compensable, corridor_pct):
      (None, None)  — no row, default = not compensable
      (False, None) — explicitly disabled
      (True, pct)   — enabled, pct guaranteed by DB constraint
    """
    row = by_class.get(class_id) or by_type.get(material_type_id)
    if row is None:
        return None, None
    if not row.is_compensable:
        return False, None
    return True, row.corridor_pct


def set_type_corridor(
    db: Session, project_id: int, material_type_id: int,
    is_compensable: bool, corridor_pct: Decimal | None,
) -> None:
    """Upsert type-level corridor rule. Race-safe via partial unique index."""
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type_id=material_type_id,
        material_class_id=None,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_type_id"],
        index_where=text("material_class_id IS NULL"),
        set_={
            "is_compensable": stmt.excluded.is_compensable,
            "corridor_pct": stmt.excluded.corridor_pct,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def set_class_corridor(
    db: Session, project_id: int, material_class_id: int,
    is_compensable: bool, corridor_pct: Decimal | None,
) -> None:
    """Upsert class-level corridor override. Race-safe via partial unique index."""
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type_id=None,
        material_class_id=material_class_id,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_class_id"],
        index_where=text("material_type_id IS NULL"),
        set_={
            "is_compensable": stmt.excluded.is_compensable,
            "corridor_pct": stmt.excluded.corridor_pct,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def delete_type_corridor(db: Session, project_id: int, material_type_id: int) -> bool:
    """Remove type-level rule. Returns True if row existed."""
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_type_id == material_type_id,
            CompensationCorridor.material_class_id.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def delete_class_corridor(db: Session, project_id: int, material_class_id: int) -> bool:
    """Remove class-level override. Returns True if row existed."""
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_class_id == material_class_id,
            CompensationCorridor.material_type_id.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def build_resolved_matrix(db: Session, project_id: int) -> dict:
    """Build the full resolved matrix for the corridors GET endpoint.

    Returns {types: [...], classes: [...]} where each class has its resolved
    is_compensable, corridor_pct, level ("type"/"class"/"default"), has_override.
    Emits material_type CODE strings for the frontend (unchanged contract),
    resolving internally by material_type_id.
    """
    by_class, by_type = get_corridor_map(db, project_id)
    all_classes = (
        db.query(MaterialClass)
        .join(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .order_by(MaterialType.code, MaterialClass.name)
        .all()
    )

    # id → code for all material types present on classes
    type_code: dict[int, str] = {
        mt.id: mt.code for mt in db.query(MaterialType).all()
    }

    # Distinct type IDs from material classes, in code order
    all_type_ids = sorted({mc.material_type_id for mc in all_classes}, key=lambda tid: type_code[tid])

    types_out = []
    for mt_id in all_type_ids:
        rule = by_type.get(mt_id)
        types_out.append({
            "material_type": type_code[mt_id],
            "is_compensable": rule.is_compensable if rule else None,
            "corridor_pct": rule.corridor_pct if rule else None,
            "has_rule": rule is not None,
        })

    classes_out = []
    for mc in all_classes:
        has_override = mc.id in by_class
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        if has_override:
            level = "class"
        elif mc.material_type_id in by_type:
            level = "type"
        else:
            level = "default"
        classes_out.append({
            "material_class_id": mc.id,
            "material_class_name": mc.name,
            "material_type": type_code[mc.material_type_id],
            "is_compensable": compensable if compensable is not None else False,
            "corridor_pct": pct,
            "level": level,
            "has_override": has_override,
        })

    return {"types": types_out, "classes": classes_out}

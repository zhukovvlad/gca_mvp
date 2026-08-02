from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import ProjectSupplierExclusion


def get_excluded_supplier_ids(db: Session, project_id: int) -> set[int]:
    """Возвращает множество supplier_id, исключённых из расчётов для данного проекта."""
    rows = (
        db.query(ProjectSupplierExclusion.supplier_id)
        .filter(ProjectSupplierExclusion.project_id == project_id)
        .all()
    )
    return {row.supplier_id for row in rows}


def set_supplier_excluded(
    db: Session,
    project_id: int,
    supplier_id: int,
    excluded: bool,
    reason: str | None = None,
) -> None:
    """Добавить или убрать исключение поставщика для проекта. Идемпотентно и race-safe."""
    if excluded:
        stmt = (
            pg_insert(ProjectSupplierExclusion)
            .values(project_id=project_id, supplier_id=supplier_id, reason=reason)
            .on_conflict_do_nothing(index_elements=["project_id", "supplier_id"])
        )
        db.execute(stmt)
        db.commit()
    else:
        db.query(ProjectSupplierExclusion).filter(
            ProjectSupplierExclusion.project_id == project_id,
            ProjectSupplierExclusion.supplier_id == supplier_id,
        ).delete()
        db.commit()

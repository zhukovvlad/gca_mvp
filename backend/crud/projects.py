from datetime import date

from sqlalchemy.orm import Session, joinedload

from models import MaterialClass, MaterialType, Project, ReferencePrice

# Sentinel: field was not provided in the update payload (differs from explicit None)
_UNSET = object()


# --- Projects ---

def get_projects(db: Session):
    return db.query(Project).order_by(Project.name).all()


def get_project(db: Session, project_id: int):
    return db.query(Project).filter(Project.id == project_id).first()


def create_project(db: Session, name: str, contract_number: str = None) -> Project:
    project = Project(name=name, contract_number=contract_number)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project_id: int, name: str, contract_number: str = None):
    project = get_project(db, project_id)
    if project:
        project.name = name
        project.contract_number = contract_number
        db.commit()
        db.refresh(project)
    return project


def delete_project(db: Session, project_id: int):
    project = get_project(db, project_id)
    if project:
        db.delete(project)
        db.commit()
    return project


# --- Reference Prices ---

def get_reference_prices(db: Session, project_id: int = None, material_class_id: int = None, material_type_code: str = None):
    q = db.query(ReferencePrice).options(
        joinedload(ReferencePrice.project),
        joinedload(ReferencePrice.material_class).joinedload(MaterialClass.material_type),
        joinedload(ReferencePrice.unit),
    )
    if project_id is not None:
        q = q.filter(ReferencePrice.project_id == project_id)
    if material_class_id is not None:
        q = q.filter(ReferencePrice.material_class_id == material_class_id)
    if material_type_code is not None:
        q = (
            q.join(ReferencePrice.material_class)
            .join(MaterialClass.material_type)
            .filter(MaterialType.code == material_type_code)
        )
    return q.order_by(ReferencePrice.period_start.desc()).all()


def create_reference_price(db: Session, project_id: int, material_class_id: int,
                           price: float, period_start: date, period_end: date,
                           source: str | None = None, *, unit_id: int) -> ReferencePrice:
    # unit_id is NOT NULL in the DB; require it explicitly (keyword-only) so a
    # missing unit surfaces as a clear TypeError at the call site, not an
    # IntegrityError at commit. The sole caller (reference_prices router) validates it first.
    rp = ReferencePrice(
        project_id=project_id, material_class_id=material_class_id,
        unit_id=unit_id,
        price=price, period_start=period_start, period_end=period_end,
        source=source if (isinstance(source, str) and source.strip()) else None,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return rp


def update_reference_price(db: Session, rp_id: int, price=_UNSET,
                           period_start=_UNSET, period_end=_UNSET,
                           source=_UNSET) -> ReferencePrice | None:
    rp = db.query(ReferencePrice).filter(ReferencePrice.id == rp_id).first()
    if not rp:
        return None
    if price is not _UNSET:
        rp.price = price
    if period_start is not _UNSET:
        rp.period_start = period_start
    if period_end is not _UNSET:
        rp.period_end = period_end
    if source is not _UNSET:
        rp.source = source if (isinstance(source, str) and source.strip()) else None
    db.commit()
    db.refresh(rp)
    return rp


def delete_reference_price(db: Session, rp_id: int):
    rp = db.query(ReferencePrice).filter(ReferencePrice.id == rp_id).first()
    if rp:
        db.delete(rp)
        db.commit()
    return rp

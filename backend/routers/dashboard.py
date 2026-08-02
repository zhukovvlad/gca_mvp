from calendar import monthrange
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import distinct, extract, func, literal, or_
from sqlalchemy.orm import Session

from crud.calculations import compute_calculations, full_compensation_from_rows
from crud.supplier_exclusions import get_excluded_supplier_ids
from crud.units import item_has_issues
from database import get_db
from models import (
    Document,
    Invoice,
    InvoiceItem,
    MaterialClass,
    MaterialType,
    Project,
    ProjectSupplierExclusion,
    UnitOfMeasure,
)
from routers.common import resolve_direction_type

router = APIRouter()


def _directions_by_invoice(db: Session, project_id: int, excl_filter=lambda q: q) -> dict[int, set[str]]:
    """Коды направлений (material_types, кроме other — ADR #9), которых касается каждый счёт.

    Единый резолвер: match по material_class.material_type_id, item_type='material', distinct.
    Используется и _direction_summaries (§5.5), и /dashboard/invoices — ОДНА реализация
    классификации «какие направления у счёта», без второй ветки.
    excl_filter — опционально (summary передаёт фильтр исключённых поставщиков; список счетов — нет).
    """
    direction_types = [t for t in db.query(MaterialType).all() if t.code != "other"]
    id_to_code = {t.id: t.code for t in direction_types}
    if not id_to_code:
        return {}
    rows = excl_filter(
        db.query(
            Invoice.id.label("inv_id"),
            MaterialClass.material_type_id.label("type_id"),
        )
        .join(Document, Invoice.document_id == Document.id)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            Document.project_id == project_id,
            InvoiceItem.item_type == "material",
            MaterialClass.material_type_id.in_(id_to_code.keys()),
        )
        .distinct()
    ).all()
    result: dict[int, set[str]] = {}
    for r in rows:
        result.setdefault(r.inv_id, set()).add(id_to_code[r.type_id])
    return result


def _direction_summaries(db: Session, project_id: int, excl_filter, calc_rows: list[dict]) -> dict:
    """Разбивка summary по направлениям (спека §5.1–§5.5, §6.1).

    excl_filter — функция, добавляющая фильтр исключённых поставщиков (как в summary).
    calc_rows — строки compute_calculations за полный период (для overpayment).
    Возвращает {directions, mixed_invoice_count, directed_invoice_ids,
    other_material_total}: directed_invoice_ids нужен вызывающему для
    other_invoice_count (= invoice_count - len(...)), other_material_total
    доливается в other_total."""
    types = db.query(MaterialType).order_by(MaterialType.id).all()
    direction_types = [t for t in types if t.code != "other"]   # ADR #9
    vat_expr = func.coalesce(
        InvoiceItem.vat_amount,
        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
    )

    # 1) Оборот по типам (позиционно, §5.1). outerjoin: NULL-класс → type_id IS NULL.
    turnover_rows = excl_filter(
        db.query(
            MaterialClass.material_type_id.label("type_id"),
            func.sum(InvoiceItem.amount + vat_expr).label("turnover"),
        )
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .outerjoin(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(Document.project_id == project_id, InvoiceItem.item_type == "material")
    ).group_by(MaterialClass.material_type_id).all()
    turnover_by_type = {r.type_id: r.turnover or Decimal("0") for r in turnover_rows}

    # 2) Объём по типам: только base, размерность = размерности default_unit (§5.2).
    #    outerjoin к units: ненормализованные строки → dimension IS NULL → в excluded.
    vol_rows = excl_filter(
        db.query(
            MaterialClass.material_type_id.label("type_id"),
            UnitOfMeasure.dimension.label("dimension"),
            func.sum(InvoiceItem.normalized_quantity).label("qty"),
            func.count(InvoiceItem.id).label("position_count"),
        )
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .outerjoin(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
        .filter(
            Document.project_id == project_id,
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
    ).group_by(MaterialClass.material_type_id, UnitOfMeasure.dimension).all()

    # 3) Счета по направлениям + смешанность (§5.5) — общий резолвер (коды направлений на счёт).
    types_by_invoice = _directions_by_invoice(db, project_id, excl_filter)
    mixed_invoice_ids = {inv for inv, s in types_by_invoice.items() if len(s) >= 2}

    # 4) Переплата по направлениям = Σ компенсаций (за пределами коридора) из УЖЕ
    #    посчитанных calc_rows (ноль лишних прогонов). None у направления = нет ни
    #    одного компенсируемого класса (нет коридора / вне whitelist).
    overpayment_by_code: dict[str, Decimal] = {}
    has_comp_by_code: set[str] = set()
    for r in calc_rows:
        if r["compensation_amount"] is not None:
            has_comp_by_code.add(r["direction"])
            overpayment_by_code[r["direction"]] = (
                overpayment_by_code.get(r["direction"], Decimal("0")) + r["compensation_amount"]
            )

    directions = []
    for t in direction_types:
        invoice_ids = {inv for inv, s in types_by_invoice.items() if t.code in s}
        if not invoice_ids and not turnover_by_type.get(t.id):
            continue  # направление без данных не показывается (§3.1)
        # default_unit IS NULL → объёма нет (volume=None), все base-позиции уходят в excluded_count — деградация осознанная, у direction-типов default_unit задан сидом
        default_dim = t.default_unit.dimension if t.default_unit else None
        volume = Decimal("0")
        excluded_positions = 0
        for vr in vol_rows:
            if vr.type_id != t.id:
                continue
            if default_dim is not None and vr.dimension == default_dim:
                volume += vr.qty or Decimal("0")
            else:
                excluded_positions += vr.position_count
        directions.append({
            "code": t.code,
            "name": t.name,
            "turnover": round(float(turnover_by_type.get(t.id, 0) or 0), 2),
            "overpayment": (
                round(float(overpayment_by_code[t.code]), 2) if t.code in has_comp_by_code else None
            ),
            "volume": round(float(volume), 2) if t.default_unit else None,
            "volume_unit": t.default_unit.symbol if t.default_unit else None,
            "volume_excluded_count": excluded_positions,
            "invoice_count": len(invoice_ids),
            "mixed_invoice_count": len(invoice_ids & mixed_invoice_ids),
        })

    # other_total долив (§5.1): material-позиции типа other + позиции без класса
    other_type_ids = [t.id for t in types if t.code == "other"]
    other_material_total = sum(
        (v for k, v in turnover_by_type.items() if k is None or k in other_type_ids),
        Decimal("0"),
    )
    return {
        "directions": directions,
        "mixed_invoice_count": len(mixed_invoice_ids),
        "directed_invoice_ids": set(types_by_invoice.keys()),
        "other_material_total": other_material_total,
    }


@router.get("/summary")
def get_project_summary(project_id: int, db: Session = Depends(get_db)):
    """Сводка по проекту: кол-во документов, СФ, позиций, общие суммы + компенсация за весь период."""
    excluded = get_excluded_supplier_ids(db, project_id)

    def _excl_filter(q):
        """Добавить фильтр исключённых поставщиков к запросу по Invoice."""
        if not excluded:
            return q
        return q.filter(
            or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded))
        )

    doc_count = db.query(func.count(Document.id)).filter(Document.project_id == project_id).scalar()
    invoice_count = _excl_filter(
        db.query(func.count(Invoice.id))
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id)
    ).scalar()
    totals_by_type = _excl_filter(
        db.query(
            InvoiceItem.item_type,
            func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100)).label("amount"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id)
    ).group_by(InvoiceItem.item_type).all()
    by_type = {row.item_type: float(row.amount or 0) for row in totals_by_type}
    total_all = sum(by_type.values())

    total_qty = _excl_filter(
        db.query(func.sum(InvoiceItem.quantity))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id, InvoiceItem.item_type == "material")
    ).scalar() or 0.0

    # Full invoice date range for this project (по всем инвойсам, без фильтра — для отображения периода)
    date_bounds = (
        db.query(func.min(Invoice.date), func.max(Invoice.date))
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id)
        .first()
    )
    first_invoice_date, last_invoice_date = date_bounds if date_bounds else (None, None)

    calc_rows: list[dict] = []
    full_compensation = None
    if first_invoice_date and last_invoice_date:
        period_start = first_invoice_date.replace(day=1)
        last_day = monthrange(last_invoice_date.year, last_invoice_date.month)[1]
        period_end = last_invoice_date.replace(day=last_day)
        calc_rows = compute_calculations(
            db, project_id, period_start, period_end,
            excluded_supplier_ids=excluded or None,
        )
        full_compensation = full_compensation_from_rows(calc_rows)

    dir_data = _direction_summaries(db, project_id, _excl_filter, calc_rows)

    return {
        "doc_count": doc_count or 0,
        "invoice_count": invoice_count or 0,
        "total_amount": round(total_all, 2),
        "material_amount": round(by_type.get("material", 0), 2),
        "delivery_amount": round(by_type.get("delivery", 0), 2),
        "other_amount": round(by_type.get("other", 0), 2),
        "total_qty": round(float(total_qty), 2),
        "first_invoice_date": first_invoice_date.isoformat() if first_invoice_date else None,
        "last_invoice_date": last_invoice_date.isoformat() if last_invoice_date else None,
        "full_compensation_amount": full_compensation,
        "calculations": [_serialize_calc_row(r) for r in calc_rows],
        "directions": dir_data["directions"],
        "mixed_invoice_count": dir_data["mixed_invoice_count"],
        "other_invoice_count": (invoice_count or 0) - len(dir_data["directed_invoice_ids"]),
        "delivery_total": round(by_type.get("delivery", 0), 2),
        "other_total": round(by_type.get("other", 0) + float(dir_data["other_material_total"]), 2),
    }


@router.get("/invoices")
def list_project_invoices(
    project_id: int,
    direction: str | None = None,
    db: Session = Depends(get_db),
):
    """Все СФ по проекту со всеми позициями — для отображения на дашборде."""
    mt = resolve_direction_type(db, direction)

    q = (
        db.query(Invoice)
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
    invoices = q.order_by(Invoice.date.desc()).all()
    dir_map = _directions_by_invoice(db, project_id)   # без excl-фильтра: сырой список всех счетов
    def _has_issues(inv):
        """Счёт «к разбору»: нет позиций или хотя бы одна позиция с проблемой (units)."""
        if not inv.items:
            return True
        return any(item_has_issues(it) for it in inv.items)

    return [
        {
            "id": inv.id,
            "document_id": inv.document_id,
            "number": inv.number,
            "date": inv.date.isoformat(),
            "supplier_name": inv.supplier_name,
            "supplier_inn": inv.supplier_inn,
            "vat_rate": inv.vat_rate,
            "ai_confidence": inv.ai_confidence,
            "has_issues": _has_issues(inv),
            "directions": sorted(dir_map.get(inv.id, set())),   # [] = прочий
            "verified": inv.verified,
            "verified_at": inv.verified_at.isoformat() if inv.verified_at else None,
            "items": [
                {
                    "raw_name": item.raw_name,
                    "item_type": item.item_type,
                    "material_class": item.material_class.name if item.material_class else None,
                    "quantity": item.quantity,
                    "raw_unit": item.raw_unit,
                    "unit": item.raw_unit,  # legacy alias — drop after frontend plan ships
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                    "vat_amount": item.vat_amount,
                }
                for item in inv.items
            ],
        }
        for inv in invoices
    ]


def _serialize_calc_row(r: dict) -> dict:
    """JSON-форма строки compute_calculations — единый контракт /summary и /calculations."""
    return {
        "project_id": r["project_id"],
        "material_class_id": r["material_class_id"],
        "material_class_name": r["material_class_name"],
        "direction": r["direction"],
        "period_start": r["period_start"].isoformat(),
        "period_end": r["period_end"].isoformat(),
        "material_total": r["material_total"],
        "delivery_total": r["delivery_total"],
        "total_qty": r["total_qty"],
        "avg_price": r["avg_price"],
        "unit_symbol": r["unit_symbol"],
        "dimension_mismatch": r["dimension_mismatch"],
        "invoice_count": r["invoice_count"],
        "reference_price": r["reference_price"],
        "deviation_pct": r["deviation_pct"],
        "deviation_amount": r["deviation_amount"],
        "corridor_pct": r["corridor_pct"],
        "compensation_per_unit": r["compensation_per_unit"],
        "compensation_amount": r["compensation_amount"],
    }


@router.get("/calculations")
def list_calculations(
    project_id: int | None = None,
    material_class_id: int | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    direction: str | None = None,
    db: Session = Depends(get_db),
):
    """Live-вычисление расчётов помесячно. Если project_id не задан — по всем проектам."""
    mt = resolve_direction_type(db, direction)
    direction_type_id = mt.id if mt else None

    if project_id is None:
        projects = db.query(Project).all()
        # Bulk-load all exclusions in a single query to avoid N+1; select only needed columns
        all_exclusions = db.query(
            ProjectSupplierExclusion.project_id,
            ProjectSupplierExclusion.supplier_id,
        ).all()
        exclusions_by_project: dict[int, set[int]] = {}
        for exc_project_id, exc_supplier_id in all_exclusions:
            exclusions_by_project.setdefault(exc_project_id, set()).add(exc_supplier_id)
        rows: list[dict] = []
        for p in projects:
            excl = exclusions_by_project.get(p.id) or None
            rows.extend(
                compute_calculations(
                    db, p.id, period_start, period_end, material_class_id,
                    excluded_supplier_ids=excl,
                    direction_type_id=direction_type_id,
                )
            )
    else:
        excluded = get_excluded_supplier_ids(db, project_id)
        rows = compute_calculations(
            db, project_id, period_start, period_end, material_class_id,
            excluded_supplier_ids=excluded or None,
            direction_type_id=direction_type_id,
        )

    return [_serialize_calc_row(r) for r in rows]


@router.get("/monthly-summary")
def get_monthly_summary(
    project_id: int,
    direction: str | None = None,
    db: Session = Depends(get_db),
):
    """Помесячная агрегация по проекту: оборот (материалы), объём, количество СФ."""
    excluded = get_excluded_supplier_ids(db, project_id)
    mt = resolve_direction_type(db, direction)

    year_expr = extract("year", Invoice.date)
    month_expr = extract("month", Invoice.date)

    if mt is not None:
        default_dim = mt.default_unit.dimension if mt.default_unit else None
        vat_expr = func.coalesce(
            InvoiceItem.vat_amount,
            InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
        )
        amount_q = (
            db.query(
                year_expr.label("year"), month_expr.label("month"),
                func.sum(InvoiceItem.amount + vat_expr).label("total_amount"),
                func.count(distinct(Invoice.id)).label("invoice_count"),
            )
            .select_from(Invoice)
            .join(Document, Invoice.document_id == Document.id)
            .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                Document.project_id == project_id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
            )
        )
        qty_q = (
            db.query(
                year_expr.label("year"), month_expr.label("month"),
                func.sum(InvoiceItem.normalized_quantity).label("total_qty"),
            )
            .select_from(Invoice)
            .join(Document, Invoice.document_id == Document.id)
            .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .join(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
            .filter(
                Document.project_id == project_id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
                MaterialClass.calc_role == "base",
                UnitOfMeasure.dimension == default_dim,
            )
        )
        if excluded:
            excl = or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded))
            amount_q = amount_q.filter(excl)
            qty_q = qty_q.filter(excl)
        amount_rows = amount_q.group_by(year_expr, month_expr).order_by(year_expr, month_expr).all()
        qty_by_month = {
            (int(r.year), int(r.month)): r.total_qty
            for r in qty_q.group_by(year_expr, month_expr).all()
        }
        unit_symbol = mt.default_unit.symbol if mt.default_unit else None
        return [
            {
                "year": int(r.year),
                "month": int(r.month),
                "total_amount": round(float(r.total_amount or 0), 2),
                "total_qty": round(float(qty_by_month.get((int(r.year), int(r.month)), 0) or 0), 2),
                "invoice_count": int(r.invoice_count),
                "volume_unit": unit_symbol,
            }
            for r in amount_rows
        ]

    q = (
        db.query(
            year_expr.label("year"),
            month_expr.label("month"),
            func.sum(
                InvoiceItem.amount
                + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100)
            ).label("total_amount"),
            func.sum(InvoiceItem.quantity).label("total_qty"),
            func.count(distinct(Invoice.id)).label("invoice_count"),
        )
        .join(Document, Invoice.document_id == Document.id)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(Document.project_id == project_id)
    )
    if excluded:
        q = q.filter(
            or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded))
        )
    rows = q.group_by(year_expr, month_expr).order_by(year_expr, month_expr).all()

    return [
        {
            "year": int(r.year),
            "month": int(r.month),
            "total_amount": round(float(r.total_amount or 0), 2),
            "total_qty": round(float(r.total_qty or 0), 2),
            "invoice_count": int(r.invoice_count),
            "volume_unit": None,
        }
        for r in rows
    ]

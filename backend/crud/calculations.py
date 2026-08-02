from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import func, literal, or_
from sqlalchemy.orm import Session

from models import Document, Invoice, InvoiceItem, MaterialClass, MaterialType, ReferencePrice, UnitOfMeasure


def compute_compensation_per_unit(
    avg_price,
    ref_price,
    corridor_pct,
):
    """Компенсация на единицу объёма (нелинейная). Все величины — Decimal.

    None если класс некомпенсируемый (corridor_pct is None) или нет базовой цены.
    Decimal("0") если внутри коридора. Знак: + удорожание, − экономия.
    """
    from finance import money_round  # noqa: PLC0415
    if corridor_pct is None or not ref_price or ref_price <= 0:
        return None
    k = corridor_pct / Decimal("100")
    upper = ref_price * (Decimal("1") + k)
    lower = ref_price * (Decimal("1") - k)
    if avg_price > upper:
        return money_round(avg_price - upper, 2)
    if avg_price < lower:
        return money_round(avg_price - lower, 2)
    return Decimal("0")


def dimension_matches(class_dimension: str | None, ref_dimension: str | None) -> bool:
    """True only if both dimensions are present and equal (spec §4.2 guard)."""
    return class_dimension is not None and ref_dimension is not None and class_dimension == ref_dimension


def compute_shared_shares(base_rows) -> dict[int, Decimal]:
    """Per-class allocation share of shared cost within ONE invoice (spec §4.3).

    base_rows: objects with .material_class_id, .dimension, .qty (normalized), .mat_total (amount excl VAT).
    Mono-dimension → split by normalized quantity. Mixed dimensions → split by amount.
    Zero denominator → all shares 0 (no DivisionByZero).
    """
    from collections import defaultdict

    dims = {r.dimension for r in base_rows if r.dimension is not None}
    use_qty = len(dims) <= 1
    # Accumulate per class_id (a class may appear in >1 row if it spans dimensions);
    # a dict-comprehension would silently drop all but the last (last-wins) row.
    basis: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for r in base_rows:
        basis[r.material_class_id] += r.qty if use_qty else r.mat_total
    denom = sum(basis.values(), Decimal("0"))
    if denom <= 0:
        return {cid: Decimal("0") for cid in basis}
    return {cid: val / denom for cid, val in basis.items()}


def _months_in_range(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar month intervals clamped to the requested bounds."""
    months = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last_day = monthrange(cur.year, cur.month)[1]
        month_end = date(cur.year, cur.month, last_day)
        months.append((max(cur, start), min(month_end, end)))
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


def _aggregate_by_class(
    base_rows,
    delivery_per_invoice: dict[int, Decimal],
    additive_per_invoice_type: dict[tuple[int, int], Decimal],
) -> dict[int, dict]:
    """Distribute shared costs across base classes per invoice (spec §5.4).

    Delivery is invoice-wide (no direction on a delivery line). Additive-class
    costs are scoped to base classes of the SAME material_type within the
    invoice; an additive whose type has no base rows in the invoice is not
    allocated to anyone (honest refusal, spec §5.4 edge case).

    base_rows: rows with (invoice_id, material_class_id, mat_total, mat_vat,
      qty, dimension, symbol, type_id). qty is SUM(normalized_quantity);
      mat_total is SUM(amount) excl VAT.
    Returns dict[class_id -> {mat_with_vat, shared_with_vat, qty, dimensions,
      symbol, invoice_ids}].
    """
    from collections import defaultdict

    rows_by_invoice: dict[int, list] = defaultdict(list)
    for row in base_rows:
        rows_by_invoice[row.invoice_id].append(row)

    class_contrib: dict[int, dict] = {}
    for inv_id, rows in rows_by_invoice.items():
        # Per-ROW accumulation: material, qty, dimensions (a class may have >1
        # row when it spans dimensions — these MUST sum across rows).
        for row in rows:
            cid = row.material_class_id
            if cid not in class_contrib:
                class_contrib[cid] = {
                    "mat_with_vat": Decimal("0"),
                    "shared_with_vat": Decimal("0"),
                    "qty": Decimal("0"),
                    "dimensions": set(),   # >1 ⇒ intra-class dimension mix (guarded downstream)
                    "symbol": row.symbol,
                    "invoice_ids": set(),
                }
            class_contrib[cid]["mat_with_vat"] += row.mat_total + row.mat_vat
            class_contrib[cid]["qty"] += row.qty
            class_contrib[cid]["dimensions"].add(row.dimension)
            class_contrib[cid]["invoice_ids"].add(inv_id)

        # Delivery: invoice-wide shares (exactly ONCE per (invoice, class)).
        delivery_total = delivery_per_invoice.get(inv_id, Decimal("0"))
        if delivery_total:
            for cid, share in compute_shared_shares(rows).items():
                class_contrib[cid]["shared_with_vat"] += delivery_total * share

        # Additive: shares within base rows of the SAME material_type.
        rows_by_type: dict[int, list] = defaultdict(list)
        for row in rows:
            rows_by_type[row.type_id].append(row)
        for type_id, type_rows in rows_by_type.items():
            additive_total = additive_per_invoice_type.get((inv_id, type_id), Decimal("0"))
            if additive_total:
                for cid, share in compute_shared_shares(type_rows).items():
                    class_contrib[cid]["shared_with_vat"] += additive_total * share
    return class_contrib


def compute_calculations(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
    direction_type_id: int | None = None,
) -> list[dict]:
    """Live monthly calculations per material class (normalized units). See spec §4."""
    if period_start is None or period_end is None:
        bounds_q = (
            db.query(func.min(Invoice.date), func.max(Invoice.date))
            .join(Document, Invoice.document_id == Document.id)
            .filter(Document.project_id == project_id)
        )
        if excluded_supplier_ids:
            bounds_q = bounds_q.filter(
                or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded_supplier_ids))
            )
        bounds = bounds_q.first()
        if not bounds or not bounds[0]:
            return []
        min_date, max_date = bounds
        if period_start is None:
            period_start = min_date.replace(day=1)
        if period_end is None:
            period_end = max_date.replace(day=monthrange(max_date.year, max_date.month)[1])

    months = _months_in_range(period_start, period_end)
    if not months:
        return []

    class_name_map: dict[int, str] = {}
    class_type_id_map: dict[int, int] = {}

    from crud.compensation_corridors import get_corridor_map, resolve_corridor  # noqa: PLC0415
    corridor_by_class, corridor_by_type = get_corridor_map(db, project_id)

    type_code_map: dict[int, str] = {t.id: t.code for t in db.query(MaterialType).all()}

    from finance import money_round  # noqa: PLC0415
    results: list[dict] = []

    for month_start, month_end in months:
        invoice_ids_month_q = (
            db.query(Invoice.id)
            .join(Document, Invoice.document_id == Document.id)
            .filter(
                Document.project_id == project_id,
                Invoice.date >= month_start,
                Invoice.date <= month_end,
            )
        )
        if excluded_supplier_ids:
            invoice_ids_month_q = invoice_ids_month_q.filter(
                or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded_supplier_ids))
            )
        invoice_ids_month = [row[0] for row in invoice_ids_month_q.all()]
        if not invoice_ids_month:
            continue

        # Base material rows per (invoice, class) — ALL base classes (no class filter here),
        # only normalized rows. Joined to units for dimension/symbol.
        base_rows = (
            db.query(
                InvoiceItem.invoice_id,
                InvoiceItem.material_class_id,
                func.sum(InvoiceItem.amount).label("mat_total"),
                func.sum(func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                )).label("mat_vat"),
                func.sum(InvoiceItem.normalized_quantity).label("qty"),
                UnitOfMeasure.dimension.label("dimension"),
                UnitOfMeasure.symbol.label("symbol"),
                MaterialClass.material_type_id.label("type_id"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .join(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                InvoiceItem.normalized_unit_id.isnot(None),
                MaterialClass.calc_role == "base",
            )
            .group_by(
                InvoiceItem.invoice_id, InvoiceItem.material_class_id,
                UnitOfMeasure.dimension, UnitOfMeasure.symbol,
                MaterialClass.material_type_id,
            )
            .all()
        )
        if not base_rows:
            continue

        # Delivery per invoice (amount + VAT), item_type=delivery
        delivery_per_invoice: dict[int, Decimal] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                func.sum(
                    InvoiceItem.amount + func.coalesce(
                        InvoiceItem.vat_amount,
                        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                    )
                ).label("total_with_vat"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .filter(InvoiceItem.invoice_id.in_(invoice_ids_month), InvoiceItem.item_type == "delivery")
            .group_by(InvoiceItem.invoice_id)
            .all()
        ):
            delivery_per_invoice[row.invoice_id] = row.total_with_vat

        # Additives (material + calc_role=additive) — grouped by (invoice, material_type)
        # so each type's pool is allocated only to base rows of the same type (spec §5.4).
        additive_per_invoice_type: dict[tuple[int, int], Decimal] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                MaterialClass.material_type_id.label("type_id"),
                func.sum(
                    InvoiceItem.amount + func.coalesce(
                        InvoiceItem.vat_amount,
                        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                    )
                ).label("total_with_vat"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                MaterialClass.calc_role == "additive",
            )
            .group_by(InvoiceItem.invoice_id, MaterialClass.material_type_id)
            .all()
        ):
            additive_per_invoice_type[(row.invoice_id, row.type_id)] = row.total_with_vat

        class_contrib = _aggregate_by_class(base_rows, delivery_per_invoice, additive_per_invoice_type)
        class_ids = list(class_contrib.keys())

        missing_ids = [cid for cid in class_ids if cid not in class_name_map]
        if missing_ids:
            for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(missing_ids)).all():
                class_name_map[mc.id] = mc.name
                class_type_id_map[mc.id] = mc.material_type_id

        # Reference prices overlapping the month, latest per class, joined to unit dimension
        ref_rows = (
            db.query(ReferencePrice, UnitOfMeasure.dimension.label("ref_dim"))
            .join(UnitOfMeasure, ReferencePrice.unit_id == UnitOfMeasure.id)
            .filter(
                ReferencePrice.project_id == project_id,
                ReferencePrice.material_class_id.in_(class_ids),
                ReferencePrice.period_start <= month_end,
                ReferencePrice.period_end >= month_start,
            )
            .order_by(
                ReferencePrice.material_class_id,
                ReferencePrice.period_start.desc(),
                ReferencePrice.period_end.desc(),
                ReferencePrice.id.desc(),
            )
            .all()
        )
        ref_by_class: dict[int, tuple] = {}
        for ref, ref_dim in ref_rows:
            if ref.material_class_id not in ref_by_class:
                ref_by_class[ref.material_class_id] = (ref, ref_dim)

        for cid, contrib in class_contrib.items():
            if material_class_id is not None and cid != material_class_id:
                continue
            if direction_type_id is not None and class_type_id_map.get(cid) != direction_type_id:
                continue
            qty = contrib["qty"]
            if qty <= 0:
                continue
            avg_price = (contrib["mat_with_vat"] + contrib["shared_with_vat"]) / qty

            ref_tuple = ref_by_class.get(cid)
            ref = ref_tuple[0] if ref_tuple else None
            ref_dim = ref_tuple[1] if ref_tuple else None
            ref_price = ref.price if ref else None
            # class_dim is None when the class spans >1 dimension → guard blocks (intra mix).
            class_dim = next(iter(contrib["dimensions"])) if len(contrib["dimensions"]) == 1 else None
            intra_mismatch = len(contrib["dimensions"]) > 1
            mismatch = intra_mismatch or (ref is not None and not dimension_matches(class_dim, ref_dim))

            deviation_pct = None
            deviation_amount = None
            if ref_price and ref_price > 0 and not mismatch:
                deviation_pct = money_round((avg_price - ref_price) / ref_price * 100, 2)
                deviation_amount = money_round((avg_price - ref_price) * qty, 2)

            compensable, corridor_pct = resolve_corridor(
                corridor_by_class, corridor_by_type, cid, class_type_id_map.get(cid),
            )
            if not compensable or mismatch:
                compensation_per_unit = None
                compensation_amount = None
            else:
                compensation_per_unit = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
                compensation_amount = (
                    money_round(compensation_per_unit * qty, 2)
                    if compensation_per_unit is not None else None
                )

            results.append({
                "project_id": project_id,
                "material_class_id": cid,
                "material_class_name": class_name_map.get(cid, "?"),
                "direction": type_code_map[class_type_id_map[cid]],
                "period_start": month_start,
                "period_end": month_end,
                "material_total": money_round(contrib["mat_with_vat"], 2),
                "delivery_total": money_round(contrib["shared_with_vat"], 2),
                "total_qty": money_round(qty, 3),
                "avg_price": money_round(avg_price, 2),
                "unit_symbol": contrib["symbol"],
                "dimension_mismatch": mismatch,
                "invoice_count": len(contrib["invoice_ids"]),
                "reference_price": ref_price,
                "deviation_pct": deviation_pct,
                "deviation_amount": deviation_amount,
                "corridor_pct": corridor_pct,
                "compensation_per_unit": compensation_per_unit,
                "compensation_amount": compensation_amount,
            })

    return results


def full_deviation_from_rows(rows: list[dict]) -> Decimal | None:
    """Total deviation over precomputed compute_calculations rows.
    Returns None if no reference prices produced a deviation (not 0.0).
    Слагаемые уже money_round-нуты до копеек, поэтому итог точен и
    money_round здесь — соблюдение конвенции, не смена значения."""
    from finance import money_round  # noqa: PLC0415
    amounts = [r["deviation_amount"] for r in rows if r["deviation_amount"] is not None]
    return money_round(sum(amounts, Decimal("0")), 2) if amounts else None


def full_compensation_from_rows(rows: list[dict]) -> Decimal | None:
    """Total compensation over precomputed compute_calculations rows.
    Returns None if ни один класс не компенсируется (нет коридора или вне
    whitelist), а не 0.0. Слагаемые уже money_round-нуты до копеек, поэтому
    итог точен; money_round здесь — соблюдение конвенции."""
    from finance import money_round  # noqa: PLC0415
    amounts = [r["compensation_amount"] for r in rows if r["compensation_amount"] is not None]
    return money_round(sum(amounts, Decimal("0")), 2) if amounts else None


def compute_full_deviation(
    db: Session,
    project_id: int,
    period_start: date,
    period_end: date,
    excluded_supplier_ids: set[int] | None = None,
) -> Decimal | None:
    """Compute total deviation_amount for a project over [period_start, period_end].
    Delegates to compute_calculations() — единый источник истины.
    Returns None if no reference prices are available for any class (not 0.0)."""
    rows = compute_calculations(db, project_id, period_start, period_end, excluded_supplier_ids=excluded_supplier_ids)
    return full_deviation_from_rows(rows)


def compute_export_rows(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
    direction_type_id: int | None = None,
) -> list[dict]:
    """Per-(invoice, material_class) rows for the detailed Excel report (normalized units)."""
    from collections import defaultdict

    if period_start is None or period_end is None:
        bounds_q = (
            db.query(func.min(Invoice.date), func.max(Invoice.date))
            .join(Document, Invoice.document_id == Document.id)
            .filter(Document.project_id == project_id)
        )
        if excluded_supplier_ids:
            bounds_q = bounds_q.filter(
                or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded_supplier_ids))
            )
        bounds = bounds_q.first()
        if not bounds or not bounds[0]:
            return []
        if period_start is None:
            period_start = bounds[0].replace(day=1)
        if period_end is None:
            max_d = bounds[1]
            period_end = max_d.replace(day=monthrange(max_d.year, max_d.month)[1])

    invoices_raw_q = (
        db.query(Invoice.id, Invoice.date, Invoice.number, Invoice.supplier_name, Invoice.vat_rate)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id, Invoice.date >= period_start, Invoice.date <= period_end)
        .order_by(Invoice.date, Invoice.number)
    )
    if excluded_supplier_ids:
        invoices_raw_q = invoices_raw_q.filter(
            or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded_supplier_ids))
        )
    invoices_raw = invoices_raw_q.all()
    if not invoices_raw:
        return []

    invoice_ids = [r.id for r in invoices_raw]
    invoice_map = {r.id: r for r in invoices_raw}

    # Base material rows per (invoice, class) — normalized only, NO class filter (denominator needs full invoice)
    base_rows = (
        db.query(
            InvoiceItem.invoice_id,
            InvoiceItem.material_class_id,
            func.sum(InvoiceItem.amount).label("mat_total"),
            func.sum(func.coalesce(
                InvoiceItem.vat_amount,
                InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
            )).label("mat_vat"),
            func.sum(InvoiceItem.normalized_quantity).label("qty"),
            func.sum(InvoiceItem.quantity).label("raw_qty"),
            func.max(InvoiceItem.raw_unit).label("raw_unit"),
            UnitOfMeasure.symbol.label("symbol"),
            UnitOfMeasure.dimension.label("dimension"),
            MaterialClass.material_type_id.label("type_id"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .join(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            InvoiceItem.normalized_unit_id.isnot(None),
            MaterialClass.calc_role == "base",
        )
        .group_by(
            InvoiceItem.invoice_id, InvoiceItem.material_class_id,
            UnitOfMeasure.symbol, UnitOfMeasure.dimension,
            MaterialClass.material_type_id,
        )
        .all()
    )
    if not base_rows:
        return []

    invoice_ids = list({r.invoice_id for r in base_rows})

    # Dimension-aware share per (invoice, class) — delivery uses all rows (invoice-wide),
    # additive uses only rows of the same material_type (spec §5.4).
    rows_by_invoice = defaultdict(list)
    for r in base_rows:
        rows_by_invoice[r.invoice_id].append(r)
    delivery_share_by_inv_class: dict[tuple[int, int], Decimal] = {}
    additive_share_by_inv_class: dict[tuple[int, int], Decimal] = {}
    for inv_id, rows in rows_by_invoice.items():
        for cid, share in compute_shared_shares(rows).items():
            delivery_share_by_inv_class[(inv_id, cid)] = share
        rows_by_type = defaultdict(list)
        for r in rows:
            rows_by_type[r.type_id].append(r)
        for type_rows in rows_by_type.values():
            for cid, share in compute_shared_shares(type_rows).items():
                additive_share_by_inv_class[(inv_id, cid)] = share  # класс в одном типе — ключ не конфликтует

    # Delivery per invoice (excl/with VAT)
    delivery_per_inv: dict[int, Decimal] = {}
    delivery_excl_per_inv: dict[int, Decimal] = {}
    for r in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(InvoiceItem.amount).label("excl_vat"),
            func.sum(InvoiceItem.amount + func.coalesce(
                InvoiceItem.vat_amount,
                InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
            )).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(InvoiceItem.invoice_id.in_(invoice_ids), InvoiceItem.item_type == "delivery")
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        delivery_per_inv[r.invoice_id] = r.total_with_vat
        delivery_excl_per_inv[r.invoice_id] = r.excl_vat

    # Additives grouped by (invoice, material_type) — per-type pool for allocation (spec §5.4).
    additive_per_inv_type: dict[tuple[int, int], Decimal] = {}
    additive_excl_per_inv_type: dict[tuple[int, int], Decimal] = {}
    for r in (
        db.query(
            InvoiceItem.invoice_id,
            MaterialClass.material_type_id.label("type_id"),
            func.sum(InvoiceItem.amount).label("excl_vat"),
            func.sum(InvoiceItem.amount + func.coalesce(
                InvoiceItem.vat_amount,
                InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
            )).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "additive",
        )
        .group_by(InvoiceItem.invoice_id, MaterialClass.material_type_id)
        .all()
    ):
        additive_per_inv_type[(r.invoice_id, r.type_id)] = r.total_with_vat
        additive_excl_per_inv_type[(r.invoice_id, r.type_id)] = r.excl_vat

    class_ids = list({r.material_class_id for r in base_rows})
    class_name_map = {
        mc.id: mc.name for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(class_ids)).all()
    }

    all_ref: list = (
        db.query(ReferencePrice)
        .filter(
            ReferencePrice.project_id == project_id,
            ReferencePrice.material_class_id.in_(class_ids),
            ReferencePrice.period_end >= period_start,
            ReferencePrice.period_start <= period_end,
        )
        .order_by(
            ReferencePrice.material_class_id,
            ReferencePrice.period_start.desc(),
            ReferencePrice.period_end.desc(),
            ReferencePrice.id.desc(),
        )
        .all()
    )
    ref_by_class: dict[int, list] = {}
    for rp in all_ref:
        ref_by_class.setdefault(rp.material_class_id, []).append(rp)

    def _ref_price(class_id: int, inv_date: date):
        for rp in ref_by_class.get(class_id, []):
            if rp.period_start <= inv_date <= rp.period_end:
                return rp.price
        return None

    from finance import money_round  # noqa: PLC0415
    rows: list[dict] = []
    for br in base_rows:
        if material_class_id is not None and br.material_class_id != material_class_id:
            continue
        if direction_type_id is not None and br.type_id != direction_type_id:
            continue
        inv_id = br.invoice_id
        cid = br.material_class_id
        qty = br.qty  # normalized
        if qty is None or qty <= 0:
            continue

        delivery_share = delivery_share_by_inv_class.get((inv_id, cid), Decimal("0"))
        additive_share = additive_share_by_inv_class.get((inv_id, cid), Decimal("0"))
        mat_with_vat = br.mat_total + br.mat_vat
        delivery_alloc = delivery_per_inv.get(inv_id, Decimal("0")) * delivery_share
        additive_alloc = additive_per_inv_type.get((inv_id, br.type_id), Decimal("0")) * additive_share
        delivery_excl_alloc = delivery_excl_per_inv.get(inv_id, Decimal("0")) * delivery_share
        additive_excl_alloc = additive_excl_per_inv_type.get((inv_id, br.type_id), Decimal("0")) * additive_share

        mat_per_unit_excl_vat = br.mat_total / qty
        mat_per_unit = mat_with_vat / qty
        delivery_per_unit_excl_vat = delivery_excl_alloc / qty
        delivery_per_unit = delivery_alloc / qty
        other_per_unit_excl_vat = additive_excl_alloc / qty
        other_per_unit = additive_alloc / qty
        total_per_unit = mat_per_unit + delivery_per_unit + other_per_unit

        inv = invoice_map[inv_id]
        vat_rate_decimal = (inv.vat_rate if inv.vat_rate is not None else Decimal("20")) / Decimal("100")
        ref_price = _ref_price(cid, inv.date)
        deviation_pct = (
            money_round((total_per_unit - ref_price) / ref_price * 100, 2)
            if ref_price and ref_price > 0 else None
        )
        deviation_amount = (
            money_round((total_per_unit - ref_price) * qty, 2)
            if ref_price and ref_price > 0 else None
        )

        rows.append({
            "material_class_id": cid,
            "material_class_name": class_name_map.get(cid, "?"),
            "invoice_id": inv_id,
            "invoice_date": inv.date,
            "invoice_number": inv.number,
            "supplier_name": inv.supplier_name or "—",
            "raw_qty": money_round(br.raw_qty, 6),
            "raw_unit": br.raw_unit or "—",
            "qty": money_round(qty, 6),
            "unit_symbol": br.symbol,
            "ref_price": ref_price,
            "mat_per_m3_excl_vat": money_round(mat_per_unit_excl_vat, 6),
            "vat_rate": vat_rate_decimal,
            "mat_per_m3": money_round(mat_per_unit, 6),
            "delivery_per_m3_excl_vat": money_round(delivery_per_unit_excl_vat, 6),
            "delivery_per_m3": money_round(delivery_per_unit, 6),
            "other_per_m3_excl_vat": money_round(other_per_unit_excl_vat, 6),
            "other_per_m3": money_round(other_per_unit, 6),
            "total_per_m3": money_round(total_per_unit, 6),
            "deviation_pct": deviation_pct,
            "deviation_amount": deviation_amount,
        })

    rows.sort(key=lambda r: (r["material_class_name"], r["invoice_date"], r["invoice_number"]))
    return rows

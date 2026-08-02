import logging
from decimal import Decimal

from sqlalchemy import case, func, literal, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from models import Document, Invoice, InvoiceItem, MaterialClass, Project, ReferencePrice, Supplier, UnitOfMeasure
from utils import utcnow

logger = logging.getLogger(__name__)


def get_suppliers(db: Session) -> list[tuple]:
    """Возвращает список (Supplier, invoice_count)."""
    results = (
        db.query(Supplier, func.count(Invoice.id).label("invoice_count"))
        .outerjoin(Invoice, Invoice.supplier_id == Supplier.id)
        .group_by(Supplier.id)
        .order_by(Supplier.name)
        .all()
    )
    return results


def get_supplier(db: Session, supplier_id: int) -> Supplier | None:
    return db.query(Supplier).filter(Supplier.id == supplier_id).first()


def create_supplier(db: Session, name: str, inn: str | None) -> Supplier:
    """Создать поставщика напрямую. Не дедуплицирует — если ИНН уже занят, бросает IntegrityError."""
    supplier = Supplier(name=name, inn=inn or None)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def update_supplier(db: Session, supplier_id: int, name: str, inn: str | None) -> Supplier | None:
    """Обновить каноническое имя/ИНН поставщика и синхронизировать все связанные инвойсы."""
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return None
    supplier.name = name
    supplier.inn = inn or None
    # Синхронизируем отображаемые поля в инвойсах
    db.query(Invoice).filter(Invoice.supplier_id == supplier_id).update(
        {Invoice.supplier_name: name, Invoice.supplier_inn: supplier.inn},
        synchronize_session=False,
    )
    db.commit()
    db.refresh(supplier)
    return supplier


def delete_supplier(db: Session, supplier_id: int) -> Supplier | None:
    """Удалить поставщика. Возвращает None если не найден. Вызывать нельзя если
    есть связанные инвойсы — роутер должен проверить это ДО вызова."""
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return None
    db.delete(supplier)
    db.commit()
    return supplier


def get_supplier_invoices(db: Session, supplier_id: int) -> list[Invoice]:
    return (
        db.query(Invoice)
        .filter(Invoice.supplier_id == supplier_id)
        .order_by(Invoice.date.desc())
        .all()
    )


def get_or_create_supplier(db: Session, name: str, inn: str | None) -> Supplier:
    """Найти или создать поставщика. По ИНН если задан, иначе по имени (без ИНН).

    Не делает commit — использует flush чтобы оставаться в транзакции вызывающего.
    Защищён от race condition на уникальный ИНН через INSERT ... ON CONFLICT DO NOTHING.
    """
    if inn:
        supplier = db.query(Supplier).filter(Supplier.inn == inn).first()
        if not supplier:
            stmt = (
                pg_insert(Supplier)
                .values(name=name, inn=inn, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["inn"])
                .returning(Supplier.id)
            )
            result = db.execute(stmt)
            row = result.fetchone()
            if row:
                db.flush()
            # Повторный SELECT — либо только что вставленная, либо вставленная конкурентом
            supplier = db.query(Supplier).filter(Supplier.inn == inn).first()
            if supplier is None:
                raise RuntimeError(f"get_or_create_supplier: не удалось получить поставщика по ИНН={inn!r}")
    else:
        supplier = db.query(Supplier).filter(Supplier.inn.is_(None), Supplier.name == name).first()
        if not supplier:
            stmt = (
                pg_insert(Supplier)
                .values(name=name, inn=None, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["name"], index_where=text("inn IS NULL"))
                .returning(Supplier.id)
            )
            result = db.execute(stmt)
            row = result.fetchone()
            if row:
                db.flush()
            # Повторный SELECT — либо только что вставленная, либо вставленная конкурентом
            supplier = db.query(Supplier).filter(Supplier.inn.is_(None), Supplier.name == name).first()
            if supplier is None:
                raise RuntimeError(f"get_or_create_supplier: не удалось получить поставщика по имени={name!r}")
    return supplier


def merge_suppliers(db: Session, source_id: int, target_id: int) -> Supplier | None:
    """Перенести все инвойсы от source к target и удалить source."""
    if source_id == target_id:
        return db.query(Supplier).filter(Supplier.id == target_id).first()
    source = db.query(Supplier).filter(Supplier.id == source_id).first()
    target = db.query(Supplier).filter(Supplier.id == target_id).first()
    if not source or not target:
        return None
    db.query(Invoice).filter(Invoice.supplier_id == source_id).update(
        {
            Invoice.supplier_id: target_id,
            Invoice.supplier_name: target.name,
            Invoice.supplier_inn: target.inn,
        },
        synchronize_session=False,
    )
    db.delete(source)
    db.commit()
    db.refresh(target)
    return target


def get_supplier_duplicates(db: Session, threshold: float = 85.0) -> list[tuple]:
    """Вернуть пары поставщиков без ИНН с похожими названиями.

    Использует pg_trgm similarity() на стороне БД. Для отбора кандидатов
    применяется индексируемый оператор `%` (использует GIN-индекс), а
    similarity() остаётся для точного score и сортировки.
    threshold задаётся в диапазоне 0–100 как pg_trgm similarity * 100.
    Внутри SQL similarity() работает в диапазоне 0.0–1.0.
    Возвращаемый score также равен pg_trgm similarity * 100.
    """
    threshold = float(threshold)
    if not (0 < threshold <= 100):
        raise ValueError(f"threshold must be in (0, 100], got {threshold}")
    similarity_threshold = threshold / 100.0
    # SET LOCAL влияет только на оператор % внутри текущей транзакции.
    # Параметры не поддерживаются в SET — значение вставляется напрямую.
    # repr(float) всегда даёт числовой литерал, SQL-инъекция невозможна.
    db.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {similarity_threshold!r}"))
    S1 = aliased(Supplier)
    S2 = aliased(Supplier)
    score = func.similarity(S1.name, S2.name).label("score")
    rows = (
        db.query(S1, S2, score)
        .select_from(S1)
        .join(
            S2,
            (S1.id < S2.id)
            & S1.inn.is_(None)
            & S2.inn.is_(None)
            & S1.name.op("%")(S2.name),
        )
        .filter(score >= similarity_threshold)
        .order_by(score.desc())
        .limit(500)
        .all()
    )
    return [(s1, s2, round(float(score) * 100, 1)) for s1, s2, score in rows]


def get_suppliers_with_stats(db: Session) -> list[dict]:
    """Список поставщиков с агрегатами: оборот, число объектов, счетов, дата первого счёта.

    Оборот считается как сумма всех позиций счетов поставщика (материалы + доставка).
    Категории выводятся из классов материалов, которые поставлял данный поставщик.
    """
    turnover_label = func.coalesce(
        func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100)),
        0,
    ).label("turnover")
    rows = (
        db.query(
            Supplier,
            func.count(Invoice.id.distinct()).label("invoice_count"),
            turnover_label,
            func.count(Document.project_id.distinct()).label("project_count"),
            func.min(Invoice.date).label("first_invoice_date"),
        )
        .outerjoin(Invoice, Invoice.supplier_id == Supplier.id)
        .outerjoin(Document, Invoice.document_id == Document.id)
        .outerjoin(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .group_by(Supplier.id)
        .order_by(turnover_label.desc())
        .all()
    )

    # Категории (классы материалов) по каждому поставщику — одним запросом
    cat_rows = (
        db.query(Invoice.supplier_id, MaterialClass.name)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(Invoice.supplier_id.isnot(None))
        .distinct()
        .all()
    )
    cats_by_supplier: dict[int, list[str]] = {}
    for sid, class_name in cat_rows:
        lst = cats_by_supplier.setdefault(sid, [])
        if class_name not in lst:
            lst.append(class_name)

    return [
        {
            "id": s.id,
            "name": s.name,
            "inn": s.inn,
            "created_at": s.created_at,
            "invoice_count": invoice_count,
            "turnover": float(turnover),
            "project_count": project_count,
            "first_invoice_date": first_invoice_date,
            "categories": cats_by_supplier.get(s.id, []),
        }
        for s, invoice_count, turnover, project_count, first_invoice_date in rows
    ]


def get_supplier_detail(db: Session, supplier_id: int) -> dict | None:
    """Детальная шапка поставщика: агрегаты по всем объектам."""
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return None

    agg = (
        db.query(
            func.count(Invoice.id.distinct()).label("invoice_count"),
            func.coalesce(func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100)), 0).label("turnover"),
            func.count(Document.project_id.distinct()).label("project_count"),
            func.min(Invoice.date).label("first_invoice_date"),
        )
        .select_from(Invoice)
        .outerjoin(Document, Invoice.document_id == Document.id)
        .outerjoin(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.supplier_id == supplier_id)
        .first()
    )

    cat_rows = (
        db.query(MaterialClass.name)
        .join(InvoiceItem, InvoiceItem.material_class_id == MaterialClass.id)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.supplier_id == supplier_id)
        .distinct()
        .all()
    )
    categories = [r.name for r in cat_rows]

    return {
        "id": supplier.id,
        "name": supplier.name,
        "inn": supplier.inn,
        "created_at": supplier.created_at,
        "invoice_count": agg.invoice_count if agg else 0,
        "turnover": float(agg.turnover) if agg else 0.0,
        "project_count": agg.project_count if agg else 0,
        "first_invoice_date": agg.first_invoice_date if agg else None,
        "categories": categories,
    }


def _compute_supplier_project_deviation(
    db: Session, supplier_id: int, project_id: int
) -> tuple[float | None, float | None]:
    """Вычислить отклонение от плана для пары поставщик×объект.

    Методология идентична compute_full_deviation, но привязана только к счетам
    данного поставщика. Общие затраты (доставка + позиции с calc_role='additive')
    распределяются пропорционально объёму базовых материалов (calc_role='base')
    внутри каждого счёта.

    Отличие от compute_full_deviation: базовая цена выбирается без учёта периода —
    берётся самая свежая актуальная запись по каждому классу (order by period_start desc).
    Это намеренное решение: карточка поставщика показывает обобщённую аналитику
    за весь срок работы, а не за конкретный период. Если требуется сравнение
    с проектной страницей за тот же период — использовать compute_full_deviation напрямую.
    """
    # Подзапрос: ID счетов данного поставщика для данного объекта
    invoice_ids_q = (
        db.query(Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Invoice.supplier_id == supplier_id, Document.project_id == project_id)
    )

    # Base-материалы по счёту×класс — нормализованные единицы + dimension/symbol для
    # dimension-aware распределения общих затрат (аналогично compute_calculations).
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
            InvoiceItem.invoice_id.in_(invoice_ids_q),
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
        return None, None

    # Delivery per invoice (with VAT) — invoice-wide, no direction on delivery lines.
    delivery_per_invoice: dict[int, Decimal] = {}
    for row in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(
                InvoiceItem.amount +
                func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                )
            ).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids_q),
            InvoiceItem.item_type == "delivery",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        delivery_per_invoice[row.invoice_id] = row.total_with_vat

    # Additives grouped by (invoice, material_type) — each pool allocated only to
    # base rows of the same type (spec §5.4).
    additive_per_invoice_type: dict[tuple[int, int], Decimal] = {}
    for row in (
        db.query(
            InvoiceItem.invoice_id,
            MaterialClass.material_type_id.label("type_id"),
            func.sum(
                InvoiceItem.amount +
                func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                )
            ).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids_q),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "additive",
        )
        .group_by(InvoiceItem.invoice_id, MaterialClass.material_type_id)
        .all()
    ):
        additive_per_invoice_type[(row.invoice_id, row.type_id)] = row.total_with_vat

    # Агрегируем contribution по классу — локальный импорт для избежания кругового импорта
    from crud.calculations import _aggregate_by_class  # noqa: PLC0415
    class_contrib = _aggregate_by_class(base_rows, delivery_per_invoice, additive_per_invoice_type)

    if not class_contrib:
        return None, None

    class_ids = list(class_contrib.keys())

    # Последние базовые цены по объекту×класс (без ограничения периода)
    ref_rows = (
        db.query(ReferencePrice)
        .filter(
            ReferencePrice.project_id == project_id,
            ReferencePrice.material_class_id.in_(class_ids),
        )
        .order_by(
            ReferencePrice.material_class_id,
            ReferencePrice.period_start.desc(),
            ReferencePrice.period_end.desc(),
            ReferencePrice.id.desc(),
        )
        .all()
    )
    ref_by_class: dict[int, ReferencePrice] = {}
    for ref in ref_rows:
        if ref.material_class_id not in ref_by_class:
            ref_by_class[ref.material_class_id] = ref

    from finance import money_round  # noqa: PLC0415
    total_deviation: Decimal = Decimal("0")
    reference_total: Decimal = Decimal("0")
    any_ref = False

    for cid, contrib in class_contrib.items():
        qty = contrib["qty"]
        if qty is None or qty <= 0:
            continue
        avg_price = (contrib["mat_with_vat"] + contrib["shared_with_vat"]) / qty

        ref = ref_by_class.get(cid)
        if ref and ref.price and ref.price > 0:
            any_ref = True
            total_deviation += (avg_price - ref.price) * qty
            reference_total += ref.price * qty

    if not any_ref or reference_total == 0:
        return None, None

    deviation_amount = money_round(total_deviation, 2)
    deviation_pct = money_round(total_deviation / reference_total * 100, 2)
    return deviation_pct, deviation_amount


def get_supplier_project_stats(db: Session, supplier_id: int) -> list[dict]:
    """Статистика по каждому объекту для поставщика: оборот, объём м³, число счетов, наценка."""
    # Базовый агрегат: оборот + объём м³ + число счетов, группировка по объекту
    volume_expr = func.coalesce(
        func.sum(case((InvoiceItem.item_type == "material", InvoiceItem.quantity))),
        0,
    ).label("volume_m3")
    turnover_expr = func.coalesce(
        func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100)),
        0,
    ).label("turnover")

    rows = (
        db.query(
            Document.project_id,
            Project.name.label("project_name"),
            Project.contract_number,
            func.count(Invoice.id.distinct()).label("invoice_count"),
            turnover_expr,
            volume_expr,
        )
        .select_from(Invoice)
        .join(Document, Invoice.document_id == Document.id)
        .join(Project, Project.id == Document.project_id)
        .outerjoin(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.supplier_id == supplier_id)
        .group_by(Document.project_id, Project.name, Project.contract_number)
        .order_by(turnover_expr.desc())
        .all()
    )

    result = []
    for project_id, project_name, contract_number, invoice_count, turnover, volume_m3 in rows:
        deviation_pct, deviation_amount = _compute_supplier_project_deviation(
            db, supplier_id, project_id
        )
        result.append({
            "project_id": project_id,
            "project_name": project_name,
            "contract_number": contract_number,
            "invoice_count": invoice_count,
            "turnover": float(turnover),
            "volume_m3": float(volume_m3),
            "deviation_pct": deviation_pct,
            "deviation_amount": deviation_amount,
        })
    return result


def get_supplier_invoices_list(db: Session, supplier_id: int,
                               project_id: int | None = None) -> list[dict]:
    """Список счетов поставщика по всем объектам (для таба «Счета»)."""
    q = (
        db.query(
            Invoice.id,
            Invoice.document_id,
            Invoice.number,
            Invoice.date,
            Invoice.verified,
            Invoice.verified_at,
            Invoice.ai_confidence,
            Document.project_id,
            Project.name.label("project_name"),
            func.coalesce(func.sum(InvoiceItem.amount), 0).label("amount"),
        )
        .join(Document, Invoice.document_id == Document.id)
        .join(Project, Project.id == Document.project_id)
        .outerjoin(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.supplier_id == supplier_id)
    )
    if project_id is not None:
        q = q.filter(Document.project_id == project_id)
    q = q.group_by(
        Invoice.id, Invoice.document_id, Invoice.number, Invoice.date, Invoice.verified,
        Invoice.verified_at, Invoice.ai_confidence, Document.project_id, Project.name,
    ).order_by(Invoice.date.desc())

    return [
        {
            "id": row.id,
            "document_id": row.document_id,
            "number": row.number,
            "date": str(row.date),
            "verified": row.verified,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
            "ai_confidence": row.ai_confidence,
            "project_id": row.project_id,
            "project_name": row.project_name,
            "amount": float(row.amount),
        }
        for row in q.all()
    ]

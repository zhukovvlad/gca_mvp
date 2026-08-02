from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from crud.suppliers import get_or_create_supplier
from crud.units import load_alias_map, normalize_item
from models import Document, Invoice, InvoiceItem


def _dec(value) -> Decimal | None:
    """LLM/JSON float → Decimal через str() (отсекает бинарную погрешность float). None-safe."""
    return None if value is None else Decimal(str(value))


# --- Documents ---

def get_documents(db: Session, project_id: int = None, status: str = None):
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    if project_id is not None:
        q = q.filter(Document.project_id == project_id)
    if status is not None:
        q = q.filter(Document.status == status)
    return q.all()


def get_document(db: Session, doc_id: int):
    return db.query(Document).filter(Document.id == doc_id).first()


def create_document(db: Session, project_id: int, filename: str, s3_key: str,
                    file_hash: str | None = None) -> Document:
    """Создать документ (pending). file_hash — sha256 исходных байтов (Q6, дедуп)."""
    doc = Document(project_id=project_id, filename=filename, s3_key=s3_key, file_hash=file_hash)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def delete_document(db: Session, doc_id: int):
    doc = get_document(db, doc_id)
    if doc:
        db.delete(doc)
        db.commit()
    return doc


def try_acquire_processing(db: Session, doc_id: int, run_id: str | None = None) -> bool:
    """Атомарно перевести документ в processing, если он ещё не там (guard S0-5).

    Коммитит немедленно — иначе переход не виден другим сессиям (409 не сработает,
    а фоновая таска на S1 не увидит processing). Возвращает True, если захватили.
    """
    result = db.execute(
        text("UPDATE documents SET status='processing', processing_started_at=now(), "
             "processing_run_id=:rid, last_error=NULL "
             "WHERE id=:id AND status != 'processing'"),
        {"id": doc_id, "rid": run_id},
    )
    db.commit()
    return result.rowcount == 1


# --- Invoices ---

def create_invoice(db: Session, document_id: int, number: str, invoice_date: date,
                   supplier_name: str | None, supplier_inn: str | None, vat_rate: float | None,
                   confidence: float, items: list[dict]) -> Invoice:
    # Нормализуем: пустые строки и whitespace → None
    _inn = (supplier_inn.strip() or None) if supplier_inn else None
    _name = (supplier_name.strip() or None) if supplier_name else None

    # ИНН без имени — сбрасываем: нет смысла хранить ИНН без привязанного Supplier.
    if not _name:
        _inn = None

    # Если поставщик уже есть в БД (напр., по тому же ИНН) — берём каноническое имя из БД,
    # а не сырой текст из документа.
    supplier_id = None
    if _name:
        supplier = get_or_create_supplier(db, name=_name, inn=_inn)
        supplier_id = supplier.id
        _name = supplier.name
        _inn = supplier.inn

    invoice = Invoice(
        document_id=document_id,
        supplier_id=supplier_id,
        number=number,
        date=invoice_date,
        supplier_name=_name,
        supplier_inn=_inn,
        vat_rate=_dec(vat_rate),
        ai_confidence=confidence,
    )
    db.add(invoice)
    db.flush()

    aliases = load_alias_map(db)
    for item in items:
        quantity = _dec(item["quantity"])
        unit_price = _dec(item["unit_price"])
        raw_unit = item.get("unit")
        norm = normalize_item(raw_unit, quantity, unit_price, aliases)
        db_item = InvoiceItem(
            invoice_id=invoice.id,
            raw_name=item["raw_name"],
            item_type=item["item_type"],
            material_class_id=item.get("material_class_id"),
            quantity=quantity,
            raw_unit=raw_unit,
            normalized_unit_id=norm.normalized_unit_id if norm else None,
            normalized_quantity=norm.normalized_quantity if norm else None,
            normalized_unit_price=norm.normalized_unit_price if norm else None,
            unit_price=unit_price,
            amount=_dec(item["amount"]),
            vat_amount=_dec(item.get("vat_amount")),
        )
        db.add(db_item)

    db.commit()
    db.refresh(invoice)
    return invoice

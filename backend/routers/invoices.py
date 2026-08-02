import hashlib
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crud.documents import (
    create_document,
    delete_document,
    get_document,
    get_documents,
    try_acquire_processing,
)
from crud.suppliers import get_or_create_supplier
from crud.units import item_has_issues, load_alias_map, normalize_item
from database import get_db
from models import Document, Invoice, InvoiceItem, MaterialClass
from processing import get_processing_session_factory
from s3 import delete_file, delete_file_async, download_file, upload_file_async

logger = logging.getLogger(__name__)
router = APIRouter()


def _load_document_locked(db: Session, doc_id: int) -> Document | None:
    """SELECT ... FOR UPDATE строки документа — сериализует мутации СФ с фазой B (S0-8).

    Держит блокировку строки documents до конца транзакции запроса: пока она
    жива, фаза B (persist_parse_result) не сможет пройти свой собственный
    FOR UPDATE и будет ждать (или наоборот) — мутации СФ и переразбор одного
    документа никогда не пересекаются гонкой.
    """
    return db.query(Document).filter(Document.id == doc_id).with_for_update().first()


def _reject_if_busy(doc: Document | None) -> None:
    """409, если документ в нетерминальном статусе (pending|processing) —
    мутации СФ/документа запрещены до терминального.

    pending включён (S1): в окне между commit create_document (pending) и
    guard-commit (processing) документ иначе можно было бы удалить,
    оставив S3-сироту. Легитимных мутаций pending-документа не существует.
    """
    if doc is not None and doc.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="Документ обрабатывается — дождитесь завершения")


class InvoiceItemEdit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None  # None для новой позиции
    raw_name: str
    item_type: str  # material / delivery / other
    material_class_id: int | None = None
    quantity: float
    # Accept both "raw_unit" (new) and legacy "unit" during the FE/BE transition.
    raw_unit: str | None = Field(default=None, validation_alias=AliasChoices("raw_unit", "unit"))
    unit_price: float
    amount: float
    vat_amount: float | None = None


class InvoiceUpdate(BaseModel):
    number: str
    date: date
    supplier_name: str | None = None
    supplier_inn: str | None = None
    vat_rate: float = 20.0
    items: list[InvoiceItemEdit]


def _doc_has_issues(doc) -> bool:
    """Документ требует проверки, если есть позиции, по которым нельзя считать
    аналитику: нет количества, нет описания, единица измерения материала не
    нормализована, или нарушен инвариант количество*цена≈сумма."""
    for inv in doc.invoices:
        if not inv.items:
            return True
        for item in inv.items:
            if item_has_issues(item):
                return True
    return False


def _avg_confidence(doc) -> float | None:
    confs = [inv.ai_confidence for inv in doc.invoices if inv.ai_confidence is not None]
    if not confs:
        return None
    return round(sum(confs) / len(confs), 2)


def _serialize_document(doc) -> dict:
    """Полная сериализация документа со счетами-фактурами и позициями.
    Используется в GET /documents/{id}, POST /upload, POST /reparse —
    клиент кеширует один и тот же shape независимо от точки входа."""
    return {
        "id": doc.id,
        "project_id": doc.project_id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "last_error": doc.last_error,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        "invoice_count": len(doc.invoices),
        "has_issues": _doc_has_issues(doc) if doc.status == "parsed" else False,
        "ai_confidence": _avg_confidence(doc),
        "parse_cost_usd": float(doc.parse_cost_usd),
        "parse_count": doc.parse_count,
        "invoices": [
            {
                "id": inv.id,
                "document_id": doc.id,
                "number": inv.number,
                "date": inv.date.isoformat(),
                "supplier_name": inv.supplier_name,
                "supplier_inn": inv.supplier_inn,
                "vat_rate": inv.vat_rate,
                "ai_confidence": inv.ai_confidence,
                "verified": inv.verified,
                "verified_at": inv.verified_at.isoformat() if inv.verified_at else None,
                "has_issues": False,  # пер-СФ флаг можно вычислить позже, пока на уровне документа
                "items": [
                    {
                        "id": item.id,
                        "raw_name": item.raw_name,
                        "item_type": item.item_type,
                        "material_class": (
                            {"id": item.material_class.id, "name": item.material_class.name}
                            if item.material_class
                            else None
                        ),
                        "material_class_id": item.material_class_id,
                        "quantity": item.quantity,
                        "raw_unit": item.raw_unit,
                        "unit": item.raw_unit,  # legacy alias — drop after frontend plan ships
                        "normalized_unit_id": item.normalized_unit_id,
                        "unit_price": item.unit_price,
                        "amount": item.amount,
                        "vat_amount": item.vat_amount,
                    }
                    for item in inv.items
                ],
            }
            for inv in doc.invoices
        ],
    }


@router.get("/documents")
def list_documents(project_id: int | None = None, db: Session = Depends(get_db)):
    """Список документов (опц. фильтр по проекту) со статусами и метриками для UI."""
    docs = get_documents(db, project_id)
    return [
        {
            "id": doc.id,
            "project_id": doc.project_id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "status": doc.status,
            "last_error": doc.last_error,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            "invoice_count": len(doc.invoices),
            "has_issues": _doc_has_issues(doc) if doc.status == "parsed" else False,
            "ai_confidence": _avg_confidence(doc),
            "parse_cost_usd": float(doc.parse_cost_usd),
            "parse_count": doc.parse_count,
        }
        for doc in docs
    ]


@router.get("/documents/{doc_id}")
def get_document_detail(doc_id: int, db: Session = Depends(get_db)):
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return _serialize_document(doc)


@router.get("/documents/{doc_id}/pdf")
def get_document_pdf(doc_id: int, db: Session = Depends(get_db)):
    doc = get_document(db, doc_id)
    if not doc or not doc.s3_key:
        raise HTTPException(status_code=404, detail="PDF не найден")
    try:
        file_bytes = download_file(doc.s3_key)
    except Exception:
        raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
    return Response(content=file_bytes, media_type="application/pdf")


@router.post("/documents/{doc_id}/reparse", status_code=202)
async def reparse_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Повторный парсинг в фоне: синхронные проверки + guard → 202 (S1-1)."""
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not doc.s3_key:
        raise HTTPException(status_code=400, detail="PDF недоступен в хранилище")
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед повторным разбором")
    if not try_acquire_processing(db, doc_id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    background_tasks.add_task(process_document, doc_id, mode="parse", session_factory=session_factory)

    db.expire_all()
    return _serialize_document(get_document(db, doc_id))


@router.post("/documents/{doc_id}/deskew-reparse", status_code=202)
async def deskew_reparse_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Коррекция ориентации + переразбор в фоне; ошибки ориентации доезжают статусом
    error + last_error через polling (S1)."""
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not doc.s3_key:
        raise HTTPException(status_code=400, detail="PDF недоступен в хранилище")
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед коррекцией")
    if not try_acquire_processing(db, doc_id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    background_tasks.add_task(process_document, doc_id, mode="deskew", session_factory=session_factory)

    db.expire_all()
    return _serialize_document(get_document(db, doc_id))


@router.post("/upload", status_code=202)
async def upload_pdf(
    response: Response,
    background_tasks: BackgroundTasks,
    project_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Загрузить PDF: S3 + документ + guard, обработка в фоне; 202 немедленно (S1-1).

    Дубликат по file_hash → 200 duplicate:true с существующим документом (Q6).
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Только PDF-файлы")

    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()  # хеш ОРИГИНАЛА; после deskew не пересчитывается (Q6)

    # Fast-path дедупа: файл уже загружали в этот проект → отдаём существующий документ.
    existing = (db.query(Document)
                .filter(Document.project_id == project_id, Document.file_hash == file_hash)
                .first())
    if existing:
        response.status_code = 200
        return {**_serialize_document(existing), "duplicate": True}

    now = datetime.now(UTC)
    object_name = f"{now.year}/{now.month:02d}/{uuid.uuid4().hex}_{file.filename}"
    try:
        # ensure_bucket() не в запросе — bucket создаётся в lifespan (S0-6, не блокируем loop).
        await upload_file_async(file_bytes, object_name)
    except Exception:
        logger.exception("Upload: ошибка загрузки в S3")
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл в хранилище")

    try:
        doc = create_document(db, project_id, file.filename, object_name, file_hash=file_hash)
    except IntegrityError:
        # Гонка двух параллельных загрузок одного файла ИЛИ иная integrity-ошибка (FK).
        db.rollback()  # обязателен: сессия в failed state (PendingRollbackError без него)
        winner = (db.query(Document)
                  .filter(Document.project_id == project_id, Document.file_hash == file_hash)
                  .first())
        try:
            await delete_file_async(object_name)  # наш S3-объект осиротел — best-effort очистка
        except Exception:
            logger.warning(f"Дедуп-гонка: не удалось удалить S3-сироту {object_name}")
        if winner is None:
            raise  # IntegrityError был НЕ про uq_documents_project_file_hash — не маскируем под дубликат
        response.status_code = 200
        return {**_serialize_document(winner), "duplicate": True}

    if not try_acquire_processing(db, doc.id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    background_tasks.add_task(process_document, doc.id, mode="parse",
                              pdf_bytes=file_bytes, session_factory=session_factory)

    db.expire_all()
    return {**_serialize_document(get_document(db, doc.id)), "duplicate": False}


@router.put("/{invoice_id}")
def update_invoice(invoice_id: int, data: InvoiceUpdate, db: Session = Depends(get_db)):
    """Обновить СФ и её позиции. Удаляет позиции, которых нет в новом списке.

    Запрещено во время обработки документа (S0-8): лочим Document, отклоняем
    processing 409, затем перезапрашиваем СФ под блокировкой — фаза B могла
    успеть её удалить (parse-then-swap) до того, как мы получили блокировку.
    """
    # 1) первичный lookup — только чтобы узнать document_id, без мутации устаревшего ORM-объекта.
    row = db.query(Invoice.document_id).filter(Invoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 2) блокируем документ; 3) отклоняем, если обрабатывается.
    doc = _load_document_locked(db, row.document_id)
    _reject_if_busy(doc)
    # 4) перезапрашиваем СФ ПОД блокировкой — фаза B могла её удалить.
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    if invoice.verified:
        raise HTTPException(status_code=409, detail="СФ подтверждена — снимите подтверждение перед редактированием")

    invoice.number = data.number
    invoice.date = data.date
    _name = (data.supplier_name.strip() or None) if data.supplier_name else None
    _inn = (data.supplier_inn.strip() or None) if data.supplier_inn else None
    if _inn and not _name:
        raise HTTPException(status_code=422, detail="supplier_name обязателен при указании supplier_inn")
    invoice.vat_rate = data.vat_rate

    warnings: list[dict] = []
    if _name:
        supplier = get_or_create_supplier(db, name=_name, inn=_inn)
        if _name != supplier.name:
            # ИНН совпал, имя изменилось → каноническое переименование.
            # Каскадим в денормализованную витрину всех счетов поставщика
            # (та же семантика, что crud.suppliers.update_supplier, но без отдельного commit).
            supplier.name = _name
            affected = db.query(Invoice).filter(Invoice.supplier_id == supplier.id).update(
                {Invoice.supplier_name: _name}, synchronize_session=False
            )
            warnings.append({
                "field": "supplier_name",
                "code": "supplier_renamed",
                "message": f"Имя поставщика обновлено во всех счетах ({affected})",
            })
        invoice.supplier_id = supplier.id
        invoice.supplier_name = supplier.name
        invoice.supplier_inn = supplier.inn
    else:
        invoice.supplier_id = None
        invoice.supplier_name = None
        invoice.supplier_inn = None

    incoming_ids = {item.id for item in data.items if item.id is not None}
    existing = {item.id: item for item in invoice.items}

    # Удаляем позиции, которых нет в новом списке
    for existing_id, existing_item in existing.items():
        if existing_id not in incoming_ids:
            db.delete(existing_item)

    aliases = load_alias_map(db)

    def _normalize(item_data):
        quantity = Decimal(str(item_data.quantity))
        unit_price = Decimal(str(item_data.unit_price))
        norm = normalize_item(item_data.raw_unit, quantity, unit_price, aliases)
        if norm is None and item_data.item_type == "material" and item_data.raw_unit:
            warnings.append({
                "field": "raw_unit",
                "code": "unknown_unit",
                "message": f"Единица измерения «{item_data.raw_unit}» не найдена в справочнике",
            })
        return norm

    # Обновляем существующие, создаём новые
    for item_data in data.items:
        norm = _normalize(item_data)
        if item_data.id and item_data.id in existing:
            item = existing[item_data.id]
            item.raw_name = item_data.raw_name
            item.item_type = item_data.item_type
            item.material_class_id = item_data.material_class_id
            item.quantity = item_data.quantity
            item.raw_unit = item_data.raw_unit
            item.unit_price = item_data.unit_price
            item.amount = item_data.amount
            item.vat_amount = item_data.vat_amount
            item.normalized_unit_id = norm.normalized_unit_id if norm else None
            item.normalized_quantity = norm.normalized_quantity if norm else None
            item.normalized_unit_price = norm.normalized_unit_price if norm else None
        else:
            new_item = InvoiceItem(
                invoice_id=invoice.id,
                raw_name=item_data.raw_name,
                item_type=item_data.item_type,
                material_class_id=item_data.material_class_id,
                quantity=item_data.quantity,
                raw_unit=item_data.raw_unit,
                unit_price=item_data.unit_price,
                amount=item_data.amount,
                vat_amount=item_data.vat_amount,
                normalized_unit_id=norm.normalized_unit_id if norm else None,
                normalized_quantity=norm.normalized_quantity if norm else None,
                normalized_unit_price=norm.normalized_unit_price if norm else None,
            )
            db.add(new_item)

    db.commit()
    db.refresh(invoice)
    return {"message": "Сохранено", "invoice_id": invoice.id, "warnings": warnings}


@router.post("/{invoice_id}/verify")
def verify_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Отметить СФ как проверенную человеком (запрещено во время обработки документа, S0-8)."""
    # 1) первичный lookup — только чтобы узнать document_id.
    row = db.query(Invoice.document_id).filter(Invoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 2) блокируем документ; 3) отклоняем, если обрабатывается.
    doc = _load_document_locked(db, row.document_id)
    _reject_if_busy(doc)
    # 4) перезапрашиваем СФ ПОД блокировкой — фаза B могла её удалить (parse-then-swap).
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 5) мутируем.
    invoice.verified = True
    invoice.verified_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    logger.info(f"Invoice id={invoice_id} помечена как проверенная")
    return {"message": "Проверено", "invoice_id": invoice.id, "verified_at": invoice.verified_at.isoformat()}


@router.post("/{invoice_id}/unverify")
def unverify_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Снять отметку о проверке с СФ (запрещено во время обработки документа, S0-8)."""
    # 1) первичный lookup — только чтобы узнать document_id.
    row = db.query(Invoice.document_id).filter(Invoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 2) блокируем документ; 3) отклоняем, если обрабатывается.
    doc = _load_document_locked(db, row.document_id)
    _reject_if_busy(doc)
    # 4) перезапрашиваем СФ ПОД блокировкой — фаза B могла её удалить (parse-then-swap).
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 5) мутируем.
    invoice.verified = False
    invoice.verified_at = None
    db.commit()
    logger.info(f"Invoice id={invoice_id}: отметка о проверке снята")
    return {"message": "Отметка снята", "invoice_id": invoice.id}


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(..., max_length=1000)


@router.delete("/bulk", status_code=200)
def bulk_delete_invoices(body: BulkDeleteRequest, db: Session = Depends(get_db)):
    """Удалить несколько СФ за раз. Документы в processing → 409 (атомарно — НИЧЕГО не
    удаляется, если хоть один документ набора обрабатывается, S0-8). Подтверждённые
    пропускаются (не удаляются)."""
    if not body.ids:
        return {"deleted": 0, "skipped": []}

    # 1) узнаём document_id по входным СФ (без блокировки), 2) лочим документы по
    # возрастанию id — общий порядок для всех транзакций исключает дедлок.
    doc_ids = sorted({
        r.document_id for r in
        db.query(Invoice.document_id).filter(Invoice.id.in_(body.ids)).all()
    })
    for did in doc_ids:
        _reject_if_busy(_load_document_locked(db, did))

    # 3) перезапрашиваем СФ ПОД блокировкой документов — фаза B могла часть удалить.
    invoices = db.query(Invoice).filter(Invoice.id.in_(body.ids)).all()
    deleted = 0
    skipped: list[int] = []
    for inv in invoices:
        if inv.verified:
            skipped.append(inv.id)
        else:
            db.delete(inv)
            deleted += 1
    db.commit()
    return {"deleted": deleted, "skipped": skipped}


@router.delete("/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Удалить одну СФ из документа (PDF документ остаётся). Запрещено во время
    обработки документа (S0-8)."""
    # 1) первичный lookup — только чтобы узнать document_id.
    row = db.query(Invoice.document_id).filter(Invoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 2) блокируем документ; 3) отклоняем, если обрабатывается.
    doc = _load_document_locked(db, row.document_id)
    _reject_if_busy(doc)
    # 4) перезапрашиваем СФ ПОД блокировкой — фаза B могла её удалить (parse-then-swap).
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    if invoice.verified:
        raise HTTPException(status_code=409, detail="СФ подтверждена — снимите подтверждение перед удалением")
    db.delete(invoice)
    db.commit()
    return {"message": "СФ удалена"}


@router.delete("/documents/{doc_id}")
def delete_document_route(doc_id: int, db: Session = Depends(get_db)):
    """Удалить документ вместе с СФ (запрещено во время обработки, S0-8)."""
    doc = _load_document_locked(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _reject_if_busy(doc)
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед удалением")
    if doc.s3_key:
        try:
            delete_file(doc.s3_key)
        except Exception:
            pass
    delete_document(db, doc_id)
    return {"message": "Удалено"}

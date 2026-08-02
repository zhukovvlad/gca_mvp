# DB Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old 5-table schema (suppliers, materials, invoices, invoice_items, price_stats) with a new 7-table schema (projects, material_classes, reference_prices, documents, invoices, invoice_items, price_calculations) per the spec at `docs/superpowers/specs/2026-05-08-udp-db-redesign.md`.

**Architecture:** SQLAlchemy ORM models define the schema. SQLite auto-creates tables on startup. Old DB file is deleted and recreated. Backend routers are restructured: `suppliers.py` and `materials.py` are replaced by `projects.py` and `material_classes.py`. PDF parser prompt is rewritten to extract data matching new schema. Frontend pages are rebuilt to match new entities.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, SQLite, httpx, React 18, TypeScript, shadcn/ui, Tailwind CSS, Recharts

---

### Task 1: New ORM Models

**Files:**
- Rewrite: `backend/models.py`

- [ ] **Step 1: Delete old database**

```bash
cd backend
del database.db
```

(Removes old schema — will be recreated on next startup)

- [ ] **Step 2: Rewrite models.py**

Replace entire `backend/models.py` with:

```python
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contract_number = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="project")
    reference_prices = relationship("ReferencePrice", back_populates="project")
    price_calculations = relationship("PriceCalculation", back_populates="project")


class MaterialClass(Base):
    __tablename__ = "material_classes"

    id = Column(Integer, primary_key=True, index=True)
    material_type = Column(String, nullable=False)  # concrete / rebar / other
    name = Column(String, nullable=False)  # В15, В40
    created_at = Column(DateTime, default=datetime.utcnow)

    reference_prices = relationship("ReferencePrice", back_populates="material_class")
    invoice_items = relationship("InvoiceItem", back_populates="material_class")
    price_calculations = relationship("PriceCalculation", back_populates="material_class")


class ReferencePrice(Base):
    __tablename__ = "reference_prices"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=False)
    price = Column(Float, nullable=False)  # эталонная цена за ед. с НДС
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    source = Column(String)  # "договор" / "допсоглашение №2"

    project = relationship("Project", back_populates="reference_prices")
    material_class = relationship("MaterialClass", back_populates="reference_prices")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    filename = Column(String, nullable=False)
    s3_key = Column(String)
    doc_type = Column(String, default="unknown")  # invoice / unknown
    status = Column(String, default="parsed")  # parsed / review / error / rejected
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="documents")
    invoices = relationship("Invoice", back_populates="document", cascade="all, delete-orphan")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    number = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    supplier_name = Column(String)
    supplier_inn = Column(String)
    vat_rate = Column(Float, default=20.0)
    ai_confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    raw_name = Column(String)  # сырое наименование из документа
    item_type = Column(String, nullable=False)  # material / delivery / other
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=True)
    quantity = Column(Float, nullable=False)
    unit = Column(String)  # м3, рейс, час
    unit_price = Column(Float, nullable=False)  # цена за ед. с НДС
    amount = Column(Float, nullable=False)  # сумма с НДС
    vat_amount = Column(Float)  # НДС из суммы (справка)

    invoice = relationship("Invoice", back_populates="items")
    material_class = relationship("MaterialClass", back_populates="invoice_items")


class PriceCalculation(Base):
    __tablename__ = "price_calculations"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    material_total = Column(Float, default=0)
    material_vat = Column(Float, default=0)
    delivery_total = Column(Float, default=0)
    delivery_vat = Column(Float, default=0)
    total_qty = Column(Float, default=0)
    avg_price = Column(Float, default=0)
    invoice_count = Column(Integer, default=0)
    reference_price = Column(Float)
    deviation_pct = Column(Float)
    deviation_amount = Column(Float)
    calculated_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="price_calculations")
    material_class = relationship("MaterialClass", back_populates="price_calculations")
```

- [ ] **Step 3: Verify app starts**

```bash
python -m uvicorn main:app --port 8000
```

Expected: starts without errors, creates new `database.db` with 7 tables.

- [ ] **Step 4: Commit**

```bash
git add backend/models.py
git commit -m "feat: new DB schema with 7 tables (projects, material_classes, reference_prices, documents, invoices, invoice_items, price_calculations)"
```

---

### Task 2: CRUD Operations

**Files:**
- Rewrite: `backend/crud.py`

- [ ] **Step 1: Rewrite crud.py**

Replace entire `backend/crud.py` with:

```python
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, datetime
from models import (
    Project, MaterialClass, ReferencePrice,
    Document, Invoice, InvoiceItem, PriceCalculation,
)


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


# --- Material Classes ---

def get_material_classes(db: Session, material_type: str = None):
    q = db.query(MaterialClass).order_by(MaterialClass.material_type, MaterialClass.name)
    if material_type:
        q = q.filter(MaterialClass.material_type == material_type)
    return q.all()


def get_material_class(db: Session, class_id: int):
    return db.query(MaterialClass).filter(MaterialClass.id == class_id).first()


def get_or_create_material_class(db: Session, name: str, material_type: str) -> MaterialClass:
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type == material_type
    ).first()
    if not mc:
        mc = MaterialClass(name=name, material_type=material_type)
        db.add(mc)
        db.commit()
        db.refresh(mc)
    return mc


def delete_material_class(db: Session, class_id: int):
    mc = get_material_class(db, class_id)
    if mc:
        db.query(InvoiceItem).filter(InvoiceItem.material_class_id == class_id).update(
            {InvoiceItem.material_class_id: None}, synchronize_session=False
        )
        db.query(PriceCalculation).filter(PriceCalculation.material_class_id == class_id).delete()
        db.query(ReferencePrice).filter(ReferencePrice.material_class_id == class_id).delete()
        db.delete(mc)
        db.commit()
    return mc


# --- Reference Prices ---

def get_reference_prices(db: Session, project_id: int = None):
    q = db.query(ReferencePrice)
    if project_id:
        q = q.filter(ReferencePrice.project_id == project_id)
    return q.order_by(ReferencePrice.period_start.desc()).all()


def create_reference_price(db: Session, project_id: int, material_class_id: int,
                           price: float, period_start: date, period_end: date,
                           source: str = None) -> ReferencePrice:
    rp = ReferencePrice(
        project_id=project_id, material_class_id=material_class_id,
        price=price, period_start=period_start, period_end=period_end, source=source,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return rp


def delete_reference_price(db: Session, rp_id: int):
    rp = db.query(ReferencePrice).filter(ReferencePrice.id == rp_id).first()
    if rp:
        db.delete(rp)
        db.commit()
    return rp


# --- Documents ---

def get_documents(db: Session, project_id: int = None, status: str = None):
    q = db.query(Document).order_by(Document.uploaded_at.desc())
    if project_id:
        q = q.filter(Document.project_id == project_id)
    if status:
        q = q.filter(Document.status == status)
    return q.all()


def get_document(db: Session, doc_id: int):
    return db.query(Document).filter(Document.id == doc_id).first()


def create_document(db: Session, project_id: int, filename: str, s3_key: str) -> Document:
    doc = Document(project_id=project_id, filename=filename, s3_key=s3_key)
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


# --- Invoices ---

def create_invoice(db: Session, document_id: int, number: str, invoice_date: date,
                   supplier_name: str, supplier_inn: str, vat_rate: float,
                   confidence: float, items: list[dict]) -> Invoice:
    invoice = Invoice(
        document_id=document_id,
        number=number,
        date=invoice_date,
        supplier_name=supplier_name,
        supplier_inn=supplier_inn,
        vat_rate=vat_rate,
        ai_confidence=confidence,
    )
    db.add(invoice)
    db.flush()

    for item in items:
        db_item = InvoiceItem(
            invoice_id=invoice.id,
            raw_name=item["raw_name"],
            item_type=item["item_type"],
            material_class_id=item.get("material_class_id"),
            quantity=item["quantity"],
            unit=item.get("unit"),
            unit_price=item["unit_price"],
            amount=item["amount"],
            vat_amount=item.get("vat_amount"),
        )
        db.add(db_item)

    db.commit()
    db.refresh(invoice)
    return invoice


# --- Price Calculations ---

def recalculate_prices(db: Session, project_id: int, material_class_id: int,
                       period_start: date, period_end: date):
    """Пересчитать среднюю цену за период для проекта и класса материала."""
    # Удаляем старый расчёт
    db.query(PriceCalculation).filter(
        PriceCalculation.project_id == project_id,
        PriceCalculation.material_class_id == material_class_id,
        PriceCalculation.period_start == period_start,
        PriceCalculation.period_end == period_end,
    ).delete()

    # Собираем данные из позиций
    items_query = (
        db.query(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(
            Document.project_id == project_id,
            Invoice.date >= period_start,
            Invoice.date <= period_end,
        )
    )

    material_items = items_query.filter(
        InvoiceItem.item_type == "material",
        InvoiceItem.material_class_id == material_class_id,
    ).all()

    delivery_items = items_query.filter(
        InvoiceItem.item_type == "delivery",
    ).all()

    material_total = sum(i.amount for i in material_items)
    material_vat = sum(i.vat_amount or 0 for i in material_items)
    delivery_total = sum(i.amount for i in delivery_items)
    delivery_vat = sum(i.vat_amount or 0 for i in delivery_items)
    total_qty = sum(i.quantity for i in material_items)

    if total_qty == 0:
        return None

    avg_price = (material_total + delivery_total) / total_qty

    # Эталонная цена
    ref = db.query(ReferencePrice).filter(
        ReferencePrice.project_id == project_id,
        ReferencePrice.material_class_id == material_class_id,
        ReferencePrice.period_start <= period_end,
        ReferencePrice.period_end >= period_start,
    ).first()

    reference_price = ref.price if ref else None
    deviation_pct = None
    deviation_amount = None
    if reference_price and reference_price > 0:
        deviation_pct = round((avg_price - reference_price) / reference_price * 100, 2)
        deviation_amount = round((avg_price - reference_price) * total_qty, 2)

    invoice_count = (
        db.query(func.count(Invoice.id.distinct()))
        .join(Document, Invoice.document_id == Document.id)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(
            Document.project_id == project_id,
            Invoice.date >= period_start,
            Invoice.date <= period_end,
            InvoiceItem.material_class_id == material_class_id,
        ).scalar()
    )

    calc = PriceCalculation(
        project_id=project_id,
        material_class_id=material_class_id,
        period_start=period_start,
        period_end=period_end,
        material_total=round(material_total, 2),
        material_vat=round(material_vat, 2),
        delivery_total=round(delivery_total, 2),
        delivery_vat=round(delivery_vat, 2),
        total_qty=round(total_qty, 3),
        avg_price=round(avg_price, 2),
        invoice_count=invoice_count,
        reference_price=reference_price,
        deviation_pct=deviation_pct,
        deviation_amount=deviation_amount,
        calculated_at=datetime.utcnow(),
    )
    db.add(calc)
    db.commit()
    db.refresh(calc)
    return calc
```

- [ ] **Step 2: Commit**

```bash
git add backend/crud.py
git commit -m "feat: rewrite CRUD for new 7-table schema"
```

---

### Task 3: Backend Routers

**Files:**
- Delete: `backend/routers/suppliers.py`, `backend/routers/materials.py`
- Create: `backend/routers/projects.py`, `backend/routers/material_classes.py`, `backend/routers/reference_prices.py`
- Rewrite: `backend/routers/invoices.py`, `backend/routers/dashboard.py`, `backend/routers/export.py`
- Modify: `backend/main.py`

This task is large — see the full implementation in the spec. Key changes:

- [ ] **Step 1: Create `backend/routers/projects.py`** — CRUD for projects (list, create, update, delete)
- [ ] **Step 2: Create `backend/routers/material_classes.py`** — CRUD for material classes (list, create, delete)
- [ ] **Step 3: Create `backend/routers/reference_prices.py`** — CRUD for reference prices (list by project, create, delete)
- [ ] **Step 4: Rewrite `backend/routers/invoices.py`** — Upload PDF → create document → parse → create invoices/items. Delete document (cascade). Get document PDF from S3.
- [ ] **Step 5: Rewrite `backend/routers/dashboard.py`** — Summary by project + material class + period. Price history.
- [ ] **Step 6: Rewrite `backend/routers/export.py`** — Excel report: project, period, material class, avg price, reference, deviation, list of invoices.
- [ ] **Step 7: Update `backend/main.py`** — Replace old router imports with new ones (projects, material_classes, reference_prices instead of suppliers, materials).
- [ ] **Step 8: Delete old routers** — Remove `suppliers.py`, `materials.py` and their `__pycache__`.
- [ ] **Step 9: Verify startup**

```bash
python -m uvicorn main:app --port 8000
```

- [ ] **Step 10: Commit**

```bash
git add backend/
git commit -m "feat: new routers for projects, material_classes, reference_prices; rewrite invoices/dashboard/export"
```

---

### Task 4: PDF Parser Rewrite

**Files:**
- Rewrite: `backend/pdf_parser.py`

- [ ] **Step 1: Rewrite prompt and parser**

New prompt must:
- First classify: is this an invoice (СФ/УПД) or unknown document?
- Extract multiple invoices from one PDF
- For each invoice: number, date, supplier (name, inn), VAT rate
- For each item: raw_name, classify as material/delivery/other, extract class (В15, В40...), quantity, unit, unit_price with VAT, amount with VAT, VAT amount
- Return structured JSON matching new schema

New response format:
```json
{
  "doc_type": "invoice",
  "invoices": [
    {
      "number": "...",
      "date": "YYYY-MM-DD",
      "supplier_name": "...",
      "supplier_inn": "...",
      "vat_rate": 20,
      "items": [
        {
          "raw_name": "Бетон В40 П4 F200 W12 ПМД -5 гравий",
          "item_type": "material",
          "material_class": "В40",
          "material_type": "concrete",
          "quantity": 7.0,
          "unit": "м3",
          "unit_price": 8500.0,
          "amount": 59500.0,
          "vat_amount": 9916.67
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Update parser to create entities via crud**

After parsing, create `MaterialClass` via `get_or_create_material_class`, then build items list for `crud.create_invoice`.

- [ ] **Step 3: Commit**

```bash
git add backend/pdf_parser.py
git commit -m "feat: rewrite PDF parser for new schema — classify docs, extract classes, prices with VAT"
```

---

### Task 5: Frontend — New Navigation and Pages

**Files:**
- Rewrite: `frontend/src/App.tsx`
- Delete: `frontend/src/pages/Suppliers.tsx`, `frontend/src/pages/Materials.tsx`
- Create: `frontend/src/pages/Projects.tsx`, `frontend/src/pages/MaterialClasses.tsx`, `frontend/src/pages/ReferencePrices.tsx`
- Rewrite: `frontend/src/pages/Upload.tsx`, `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/Reports.tsx`, `frontend/src/pages/Review.tsx`

- [ ] **Step 1: Update App.tsx navigation**

New nav items: Дашборд, Загрузка, Объекты, Классы материалов, Эталоны, Отчёты, Настройки

- [ ] **Step 2: Create Projects.tsx** — List projects, add/edit/delete. Fields: name, contract_number.
- [ ] **Step 3: Create MaterialClasses.tsx** — List classes, add/delete. Fields: material_type (select: concrete/rebar/other), name.
- [ ] **Step 4: Create ReferencePrices.tsx** — List by project, add/delete. Fields: project, material_class, price, period_start, period_end, source.
- [ ] **Step 5: Rewrite Upload.tsx** — User selects project before uploading. Shows documents list with delete.
- [ ] **Step 6: Rewrite Dashboard.tsx** — Filter by project + material class + period. Show avg_price vs reference, deviation. Chart.
- [ ] **Step 7: Rewrite Reports.tsx** — Select project + period → download Excel with all data.
- [ ] **Step 8: Update Review.tsx** — Show parsed invoices from document, allow manual correction.
- [ ] **Step 9: Build and verify**

```bash
cd frontend
npx vite build
```

- [ ] **Step 10: Commit**

```bash
git add frontend/src/
git commit -m "feat: frontend pages for new schema — projects, material classes, reference prices, updated upload/dashboard"
```

---

### Task 6: Integration Testing

- [ ] **Step 1: Start MinIO, backend, frontend**
- [ ] **Step 2: Create a project via UI**
- [ ] **Step 3: Add material class "В40" (concrete)**
- [ ] **Step 4: Add reference price for project + В40**
- [ ] **Step 5: Upload a real PDF invoice**
- [ ] **Step 6: Verify parsed data — items with correct classes and prices with VAT**
- [ ] **Step 7: Run price calculation — verify avg_price and deviation**
- [ ] **Step 8: Download Excel report — verify contents**
- [ ] **Step 9: Delete document — verify cascade deletion**
- [ ] **Step 10: Commit any fixes**

---

## Execution Order

Tasks 1 → 2 → 3 → 4 → 5 → 6 (strictly sequential — each depends on the previous).

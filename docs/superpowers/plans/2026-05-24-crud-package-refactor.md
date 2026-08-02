# crud.py → crud/ Package Refactor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split монолитный `backend/crud.py` (1322 строки) на пакет `backend/crud/` с прямыми импортами в роутерах.

**Architecture:** Создаём пакет `crud/` с пятью модулями по смысловым доменам. Роутеры переходят с `import crud` + `crud.fn()` на `from crud.<module> import fn`. `crud/__init__.py` остаётся пустым (или с минимальным `__all__` — только для документации, не для re-export). Тесты тоже обновляются на прямые импорты.

**Tech Stack:** Python 3.12, SQLAlchemy, pytest. Никаких новых зависимостей.

---

## Структура файлов

| Файл | Что переезжает | Строк (approx) |
|------|---------------|----------------|
| `crud/__init__.py` | пусто / `__all__` | ~10 |
| `crud/projects.py` | Projects + ReferencePrices | ~115 |
| `crud/materials.py` | MaterialClass + `VALID_CALC_ROLES` | ~55 |
| `crud/documents.py` | Documents + Invoices (create_invoice) | ~105 |
| `crud/calculations.py` | `_months_in_range`, `_aggregate_by_class`, `compute_*` | ~285 |
| `crud/suppliers.py` | всё по Supplier (12 функций) | ~530 |

**Удаляется:** `backend/crud.py`

**Обновляются импорты (только `import crud` → прямые):**
- `routers/projects.py`
- `routers/invoices.py`
- `routers/material_classes.py`
- `routers/reference_prices.py`
- `routers/dashboard.py`
- `routers/export.py`
- `routers/suppliers.py`
- `pdf_parser.py`
- `tests/unit/test_crud_recalculate.py`
- `tests/integration/test_export.py`
- `tests/integration/test_suppliers.py`

---

## Task 1: Создать crud/projects.py

**Files:**
- Create: `backend/crud/projects.py`

- [ ] **Step 1: Создать пакетную директорию и `__init__.py`**

```bash
mkdir -p backend/crud
touch backend/crud/__init__.py
```

`backend/crud/__init__.py` оставить пустым:
```python
# crud package — import directly from submodules:
#   from crud.projects import get_project, create_project
#   from crud.materials import get_material_class
#   from crud.documents import create_invoice
#   from crud.calculations import compute_calculations
#   from crud.suppliers import get_suppliers_with_stats
```

- [ ] **Step 2: Создать `crud/projects.py`**

Перенести из `crud.py` строки, относящиеся к Projects и ReferencePrices:

```python
from calendar import monthrange
from datetime import date

from sqlalchemy.orm import Session, joinedload

from models import Project, ReferencePrice

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

def get_reference_prices(db: Session, project_id: int = None, material_class_id: int = None):
    q = db.query(ReferencePrice).options(
        joinedload(ReferencePrice.project),
        joinedload(ReferencePrice.material_class),
    )
    if project_id:
        q = q.filter(ReferencePrice.project_id == project_id)
    if material_class_id:
        q = q.filter(ReferencePrice.material_class_id == material_class_id)
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
```

- [ ] **Step 3: Убедиться что файл синтаксически корректен**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'from crud.projects import get_project, create_project, update_project, delete_project, get_projects, get_reference_prices, create_reference_price, update_reference_price, delete_reference_price; print(\"OK\")'"
```

Ожидаем: `OK`

---

## Task 2: Создать crud/materials.py

**Files:**
- Create: `backend/crud/materials.py`

- [ ] **Step 1: Создать `crud/materials.py`**

```python
import logging

from sqlalchemy.orm import Session

from models import InvoiceItem, MaterialClass, ReferencePrice

logger = logging.getLogger(__name__)

VALID_CALC_ROLES = {"base", "additive", "exclude"}


# --- Material Classes ---

def get_material_classes(db: Session, material_type: str = None):
    q = db.query(MaterialClass).order_by(MaterialClass.material_type, MaterialClass.name)
    if material_type:
        q = q.filter(MaterialClass.material_type == material_type)
    return q.all()


def get_material_class(db: Session, class_id: int):
    return db.query(MaterialClass).filter(MaterialClass.id == class_id).first()


def get_or_create_material_class(
    db: Session, name: str, material_type: str, calc_role: str = "base"
) -> MaterialClass:
    if calc_role not in VALID_CALC_ROLES:
        raise ValueError(f"Unknown calc_role {calc_role!r}; allowed: {sorted(VALID_CALC_ROLES)}")
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type == material_type
    ).first()
    if not mc:
        mc = MaterialClass(name=name, material_type=material_type, calc_role=calc_role)
        db.add(mc)
        db.commit()
        db.refresh(mc)
    elif mc.calc_role != calc_role:
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
```

- [ ] **Step 2: Проверить синтаксис**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'from crud.materials import get_material_classes, get_material_class, get_or_create_material_class, delete_material_class, VALID_CALC_ROLES; print(\"OK\")'"
```

Ожидаем: `OK`

---

## Task 3: Создать crud/documents.py

**Files:**
- Create: `backend/crud/documents.py`

- [ ] **Step 1: Создать `crud/documents.py`**

```python
from datetime import date

from sqlalchemy.orm import Session

from models import Document, Invoice, InvoiceItem
from crud.suppliers import get_or_create_supplier


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
                   supplier_name: str | None, supplier_inn: str | None, vat_rate: float,
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
```

- [ ] **Step 2: Проверить синтаксис**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'from crud.documents import get_documents, get_document, create_document, delete_document, create_invoice; print(\"OK\")'"
```

Ожидаем: `OK`

---

## Task 4: Создать crud/suppliers.py

**Files:**
- Create: `backend/crud/suppliers.py`

- [ ] **Step 1: Создать `crud/suppliers.py`**

Перенести из `crud.py` все функции раздела `# --- Suppliers ---` начиная со строки ~797 до конца файла (строки 797–1322):

```python
import logging

from sqlalchemy import case, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from models import Document, Invoice, InvoiceItem, MaterialClass, Project, ReferencePrice, Supplier
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
    similarity_threshold = threshold / 100.0
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
    """Список поставщиков с агрегатами: оборот, число объектов, счетов, дата первого счёта."""
    turnover_label = func.coalesce(
        func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100)),
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
            func.coalesce(func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100)), 0).label("turnover"),
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
    за весь срок работы, а не за конкретный период.
    """
    invoice_ids_q = (
        db.query(Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Invoice.supplier_id == supplier_id, Document.project_id == project_id)
    )

    base_rows = (
        db.query(
            InvoiceItem.invoice_id,
            InvoiceItem.material_class_id,
            func.sum(InvoiceItem.amount).label("mat_total"),
            func.sum(func.coalesce(
                InvoiceItem.vat_amount,
                InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
            )).label("mat_vat"),
            func.sum(InvoiceItem.quantity).label("qty"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids_q),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
        .group_by(InvoiceItem.invoice_id, InvoiceItem.material_class_id)
        .all()
    )
    if not base_rows:
        return None, None

    base_qty_per_invoice: dict[int, float] = {}
    for row in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(InvoiceItem.quantity).label("total_qty"),
        )
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids_q),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        base_qty_per_invoice[row.invoice_id] = float(row.total_qty)

    shared_per_invoice: dict[int, float] = {}

    for row in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(
                InvoiceItem.amount +
                func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
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
        shared_per_invoice[row.invoice_id] = (
            shared_per_invoice.get(row.invoice_id, 0.0) + float(row.total_with_vat)
        )

    for row in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(
                InvoiceItem.amount +
                func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
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
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        shared_per_invoice[row.invoice_id] = (
            shared_per_invoice.get(row.invoice_id, 0.0) + float(row.total_with_vat)
        )

    from crud.calculations import _aggregate_by_class
    class_contrib = _aggregate_by_class(base_rows, base_qty_per_invoice, shared_per_invoice)

    if not class_contrib:
        return None, None

    class_ids = list(class_contrib.keys())

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

    total_deviation: float = 0.0
    reference_total: float = 0.0
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

    if not any_ref or reference_total == 0.0:
        return None, None

    deviation_amount = round(total_deviation, 2)
    deviation_pct = round(total_deviation / reference_total * 100, 2)
    return deviation_pct, deviation_amount


def get_supplier_project_stats(db: Session, supplier_id: int) -> list[dict]:
    """Статистика по каждому объекту для поставщика: оборот, объём м³, число счетов, наценка."""
    volume_expr = func.coalesce(
        func.sum(case((InvoiceItem.item_type == "material", InvoiceItem.quantity))),
        0,
    ).label("volume_m3")
    turnover_expr = func.coalesce(
        func.sum(InvoiceItem.amount + func.coalesce(InvoiceItem.vat_amount, InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100)),
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
```

- [ ] **Step 2: Проверить синтаксис**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'from crud.suppliers import get_suppliers, get_supplier, create_supplier, update_supplier, delete_supplier, get_or_create_supplier, merge_suppliers, get_supplier_duplicates, get_suppliers_with_stats, get_supplier_detail, get_supplier_project_stats, get_supplier_invoices_list; print(\"OK\")'"
```

Ожидаем: `OK`

---

## Task 5: Создать crud/calculations.py

**Files:**
- Create: `backend/crud/calculations.py`

- [ ] **Step 1: Создать `crud/calculations.py`**

```python
from calendar import monthrange
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Document, Invoice, InvoiceItem, MaterialClass, ReferencePrice


def _months_in_range(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar month intervals clamped to the requested bounds."""
    months = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last_day = monthrange(cur.year, cur.month)[1]
        month_end = date(cur.year, cur.month, last_day)
        months.append((max(cur, start), min(month_end, end)))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


def _aggregate_by_class(
    base_rows,
    base_qty_per_invoice: dict[int, float],
    shared_per_invoice: dict[int, float],
) -> dict[int, dict]:
    """Distribute shared costs proportionally across base material classes per invoice."""
    class_contrib: dict[int, dict] = {}
    for row in base_rows:
        cid = row.material_class_id
        inv_id = row.invoice_id
        qty_base_in_inv = base_qty_per_invoice.get(inv_id, 0.0)
        if qty_base_in_inv <= 0:
            continue
        share = float(row.qty) / qty_base_in_inv
        shared = shared_per_invoice.get(inv_id, 0.0) * share
        if cid not in class_contrib:
            class_contrib[cid] = {
                "mat_with_vat": 0.0,
                "shared_with_vat": 0.0,
                "qty": 0.0,
                "invoice_ids": set(),
            }
        class_contrib[cid]["mat_with_vat"] += float(row.mat_total) + float(row.mat_vat)
        class_contrib[cid]["shared_with_vat"] += shared
        class_contrib[cid]["qty"] += float(row.qty)
        class_contrib[cid]["invoice_ids"].add(inv_id)
    return class_contrib


def compute_calculations(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
) -> list[dict]:
    """Live-вычисление расчётов по проекту помесячно без записи в БД."""
    if period_start is None or period_end is None:
        bounds = (
            db.query(func.min(Invoice.date), func.max(Invoice.date))
            .join(Document, Invoice.document_id == Document.id)
            .filter(Document.project_id == project_id)
            .first()
        )
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
    results: list[dict] = []

    for month_start, month_end in months:
        invoice_ids_month = [
            row[0] for row in (
                db.query(Invoice.id)
                .join(Document, Invoice.document_id == Document.id)
                .filter(
                    Document.project_id == project_id,
                    Invoice.date >= month_start,
                    Invoice.date <= month_end,
                )
                .all()
            )
        ]
        if not invoice_ids_month:
            continue

        base_per_invoice_q = (
            db.query(
                InvoiceItem.invoice_id,
                InvoiceItem.material_class_id,
                func.sum(InvoiceItem.amount).label("mat_total"),
                func.sum(func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
                )).label("mat_vat"),
                func.sum(InvoiceItem.quantity).label("qty"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                MaterialClass.calc_role == "base",
            )
        )
        if material_class_id is not None:
            base_per_invoice_q = base_per_invoice_q.filter(
                InvoiceItem.material_class_id == material_class_id
            )
        base_rows = base_per_invoice_q.group_by(
            InvoiceItem.invoice_id, InvoiceItem.material_class_id
        ).all()

        if not base_rows:
            continue

        base_qty_per_invoice: dict[int, float] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                func.sum(InvoiceItem.quantity).label("total_qty"),
            )
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                MaterialClass.calc_role == "base",
            )
            .group_by(InvoiceItem.invoice_id)
            .all()
        ):
            base_qty_per_invoice[row.invoice_id] = float(row.total_qty)

        delivery_per_invoice: dict[int, float] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                func.sum(
                    InvoiceItem.amount +
                    func.coalesce(
                        InvoiceItem.vat_amount,
                        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
                    )
                ).label("total_with_vat"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "delivery",
            )
            .group_by(InvoiceItem.invoice_id)
            .all()
        ):
            delivery_per_invoice[row.invoice_id] = float(row.total_with_vat)

        additive_per_invoice: dict[int, float] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                func.sum(
                    InvoiceItem.amount +
                    func.coalesce(
                        InvoiceItem.vat_amount,
                        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
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
            .group_by(InvoiceItem.invoice_id)
            .all()
        ):
            additive_per_invoice[row.invoice_id] = float(row.total_with_vat)

        shared_per_invoice = {
            inv_id: delivery_per_invoice.get(inv_id, 0.0) + additive_per_invoice.get(inv_id, 0.0)
            for inv_id in set(delivery_per_invoice) | set(additive_per_invoice)
        }

        class_contrib = _aggregate_by_class(base_rows, base_qty_per_invoice, shared_per_invoice)
        class_ids = list(class_contrib.keys())

        missing_ids = [cid for cid in class_ids if cid not in class_name_map]
        if missing_ids:
            for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(missing_ids)).all():
                class_name_map[mc.id] = mc.name

        ref_rows = (
            db.query(ReferencePrice)
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
        ref_by_class: dict[int, ReferencePrice] = {}
        for ref in ref_rows:
            if ref.material_class_id not in ref_by_class:
                ref_by_class[ref.material_class_id] = ref

        for cid, contrib in class_contrib.items():
            qty = contrib["qty"]
            if qty <= 0:
                continue
            avg_price = (contrib["mat_with_vat"] + contrib["shared_with_vat"]) / qty

            ref = ref_by_class.get(cid)
            ref_price = ref.price if ref else None
            deviation_pct = None
            deviation_amount = None
            if ref_price and ref_price > 0:
                deviation_pct = round((avg_price - ref_price) / ref_price * 100, 2)
                deviation_amount = round((avg_price - ref_price) * qty, 2)

            results.append({
                "project_id": project_id,
                "material_class_id": cid,
                "material_class_name": class_name_map.get(cid, "?"),
                "period_start": month_start,
                "period_end": month_end,
                "material_total": round(contrib["mat_with_vat"], 2),
                "delivery_total": round(contrib["shared_with_vat"], 2),
                "total_qty": round(qty, 3),
                "avg_price": round(avg_price, 2),
                "invoice_count": len(contrib["invoice_ids"]),
                "reference_price": ref_price,
                "deviation_pct": deviation_pct,
                "deviation_amount": deviation_amount,
            })

    return results


def compute_full_deviation(
    db: Session, project_id: int, period_start: date, period_end: date
) -> float | None:
    """Compute total deviation_amount for a project over [period_start, period_end].
    Delegates to compute_calculations() — единый источник истины.
    Returns None if no reference prices are available for any class (not 0.0)."""
    rows = compute_calculations(db, project_id, period_start, period_end)
    amounts = [r["deviation_amount"] for r in rows if r["deviation_amount"] is not None]
    return round(sum(amounts), 2) if amounts else None


def compute_export_rows(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
) -> list[dict]:
    """Per-(invoice, material_class) rows for the detailed Excel report."""
    if period_start is None or period_end is None:
        bounds = (
            db.query(func.min(Invoice.date), func.max(Invoice.date))
            .join(Document, Invoice.document_id == Document.id)
            .filter(Document.project_id == project_id)
            .first()
        )
        if not bounds or not bounds[0]:
            return []
        if period_start is None:
            period_start = bounds[0].replace(day=1)
        if period_end is None:
            max_d = bounds[1]
            period_end = max_d.replace(day=monthrange(max_d.year, max_d.month)[1])

    invoices_raw = (
        db.query(Invoice.id, Invoice.date, Invoice.number, Invoice.supplier_name, Invoice.vat_rate)
        .join(Document, Invoice.document_id == Document.id)
        .filter(
            Document.project_id == project_id,
            Invoice.date >= period_start,
            Invoice.date <= period_end,
        )
        .order_by(Invoice.date, Invoice.number)
        .all()
    )
    if not invoices_raw:
        return []

    invoice_ids = [r.id for r in invoices_raw]
    invoice_map = {r.id: r for r in invoices_raw}

    base_q = (
        db.query(
            InvoiceItem.invoice_id,
            InvoiceItem.material_class_id,
            func.sum(InvoiceItem.amount).label("mat_total"),
            func.sum(func.coalesce(
                InvoiceItem.vat_amount,
                InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100,
            )).label("mat_vat"),
            func.sum(InvoiceItem.quantity).label("qty"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
    )
    if material_class_id is not None:
        base_q = base_q.filter(InvoiceItem.material_class_id == material_class_id)
    base_rows = base_q.group_by(InvoiceItem.invoice_id, InvoiceItem.material_class_id).all()

    if not base_rows:
        return []

    invoice_ids = list({r.invoice_id for r in base_rows})

    total_base_qty_per_inv: dict[int, float] = {}
    for r in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(InvoiceItem.quantity).label("total_qty"),
        )
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        total_base_qty_per_inv[r.invoice_id] = float(r.total_qty)

    delivery_per_inv: dict[int, float] = {}
    delivery_excl_per_inv: dict[int, float] = {}
    for r in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(InvoiceItem.amount).label("excl_vat"),
            func.sum(
                InvoiceItem.amount + func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100,
                )
            ).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "delivery",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        delivery_per_inv[r.invoice_id] = float(r.total_with_vat)
        delivery_excl_per_inv[r.invoice_id] = float(r.excl_vat)

    additive_per_inv: dict[int, float] = {}
    additive_excl_per_inv: dict[int, float] = {}
    for r in (
        db.query(
            InvoiceItem.invoice_id,
            func.sum(InvoiceItem.amount).label("excl_vat"),
            func.sum(
                InvoiceItem.amount + func.coalesce(
                    InvoiceItem.vat_amount,
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100,
                )
            ).label("total_with_vat"),
        )
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "additive",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        additive_per_inv[r.invoice_id] = float(r.total_with_vat)
        additive_excl_per_inv[r.invoice_id] = float(r.excl_vat)

    class_ids = list({r.material_class_id for r in base_rows})
    class_name_map = {
        mc.id: mc.name
        for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(class_ids)).all()
    }

    all_ref: list[ReferencePrice] = (
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

    ref_by_class: dict[int, list[ReferencePrice]] = {}
    for rp in all_ref:
        ref_by_class.setdefault(rp.material_class_id, []).append(rp)

    def _ref_price(class_id: int, inv_date: date) -> float | None:
        for rp in ref_by_class.get(class_id, []):
            if rp.period_start <= inv_date <= rp.period_end:
                return rp.price
        return None

    rows: list[dict] = []
    for br in base_rows:
        inv_id = br.invoice_id
        cid = br.material_class_id
        qty = float(br.qty)
        if qty <= 0:
            continue

        total_base_qty = total_base_qty_per_inv.get(inv_id, 0.0)
        share = qty / total_base_qty if total_base_qty > 0 else 0.0

        mat_with_vat = float(br.mat_total) + float(br.mat_vat)
        delivery_alloc = delivery_per_inv.get(inv_id, 0.0) * share
        additive_alloc = additive_per_inv.get(inv_id, 0.0) * share
        delivery_excl_alloc = delivery_excl_per_inv.get(inv_id, 0.0) * share
        additive_excl_alloc = additive_excl_per_inv.get(inv_id, 0.0) * share

        mat_per_m3_excl_vat = float(br.mat_total) / qty
        mat_per_m3 = mat_with_vat / qty
        delivery_per_m3_excl_vat = delivery_excl_alloc / qty
        delivery_per_m3 = delivery_alloc / qty
        other_per_m3_excl_vat = additive_excl_alloc / qty
        other_per_m3 = additive_alloc / qty
        total_per_m3 = mat_per_m3 + delivery_per_m3 + other_per_m3

        inv = invoice_map[inv_id]
        vat_rate_decimal = (inv.vat_rate if inv.vat_rate is not None else 20.0) / 100.0
        ref_price = _ref_price(cid, inv.date)
        deviation_pct = (
            round((total_per_m3 - ref_price) / ref_price * 100, 2)
            if ref_price and ref_price > 0
            else None
        )
        deviation_amount = (
            round((total_per_m3 - ref_price) * qty, 2)
            if ref_price and ref_price > 0
            else None
        )

        rows.append({
            "material_class_id": cid,
            "material_class_name": class_name_map.get(cid, "?"),
            "invoice_id": inv_id,
            "invoice_date": inv.date,
            "invoice_number": inv.number,
            "supplier_name": inv.supplier_name or "—",
            "qty": round(qty, 6),
            "ref_price": ref_price,
            "mat_per_m3_excl_vat": round(mat_per_m3_excl_vat, 6),
            "vat_rate": vat_rate_decimal,
            "mat_per_m3": round(mat_per_m3, 6),
            "delivery_per_m3_excl_vat": round(delivery_per_m3_excl_vat, 6),
            "delivery_per_m3": round(delivery_per_m3, 6),
            "other_per_m3_excl_vat": round(other_per_m3_excl_vat, 6),
            "other_per_m3": round(other_per_m3, 6),
            "total_per_m3": round(total_per_m3, 6),
            "deviation_pct": deviation_pct,
            "deviation_amount": deviation_amount,
        })

    rows.sort(key=lambda r: (r["material_class_name"], r["invoice_date"], r["invoice_number"]))
    return rows
```

- [ ] **Step 2: Проверить синтаксис**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'from crud.calculations import compute_calculations, compute_full_deviation, compute_export_rows, _aggregate_by_class, _months_in_range; print(\"OK\")'"
```

Ожидаем: `OK`

---

## Task 6: Обновить импорты в роутерах

**Files:**
- Modify: `backend/routers/projects.py`
- Modify: `backend/routers/invoices.py`
- Modify: `backend/routers/material_classes.py`
- Modify: `backend/routers/reference_prices.py`
- Modify: `backend/routers/dashboard.py`
- Modify: `backend/routers/export.py`
- Modify: `backend/routers/suppliers.py`

Во всех файлах заменить `import crud` → прямые импорты, а все вызовы `crud.fn(...)` → просто `fn(...)`.

- [ ] **Step 1: routers/projects.py**

Заменить строку `import crud` на:
```python
from crud.projects import get_projects, get_project, create_project, update_project, delete_project
```
Заменить все `crud.get_projects` → `get_projects`, `crud.create_project` → `create_project`, и т.д.

- [ ] **Step 2: routers/invoices.py**

Заменить `import crud` на:
```python
from crud.documents import get_documents, get_document, create_document, delete_document
from crud.suppliers import get_or_create_supplier
```
Заменить все `crud.X(` → `X(`.

- [ ] **Step 3: routers/material_classes.py**

Заменить `import crud` на:
```python
from crud.materials import get_material_classes, get_or_create_material_class, delete_material_class
```
Заменить все `crud.X(` → `X(`.

- [ ] **Step 4: routers/reference_prices.py**

Заменить `import crud` на:
```python
from crud.projects import get_reference_prices, create_reference_price, update_reference_price, delete_reference_price
```
Заменить все `crud.X(` → `X(`.

- [ ] **Step 5: routers/dashboard.py**

Заменить `import crud` на:
```python
from crud.calculations import compute_full_deviation, compute_calculations
```
Заменить все `crud.X(` → `X(`.

- [ ] **Step 6: routers/export.py**

Заменить `import crud` на:
```python
from crud.calculations import compute_export_rows
```
Заменить `crud.compute_export_rows(` → `compute_export_rows(`.

- [ ] **Step 7: routers/suppliers.py**

Заменить `import crud` на:
```python
from crud.suppliers import (
    get_suppliers_with_stats,
    get_supplier,
    get_supplier_project_stats,
    get_supplier_invoices_list,
    get_or_create_supplier,
    get_supplier_duplicates,
    get_supplier_detail,
    update_supplier,
    delete_supplier,
    merge_suppliers,
)
```
Заменить все `crud.X(` → `X(`.

- [ ] **Step 8: pdf_parser.py**

`pdf_parser.py` вызывает три функции из `crud` (сейчас `import crud` на строке 10):
- `crud.VALID_CALC_ROLES` → из `crud.materials`
- `crud.get_or_create_material_class(...)` → из `crud.materials`
- `crud.create_invoice(...)` → из `crud.documents`

Заменить `import crud` на:
```python
from crud.documents import create_invoice
from crud.materials import VALID_CALC_ROLES, get_or_create_material_class
```
Заменить все `crud.X` → `X`.

---

## Task 7: Обновить тесты и удалить crud.py

**Files:**
- Modify: `backend/tests/unit/test_crud_recalculate.py`
- Modify: `backend/tests/integration/test_export.py`
- Modify: `backend/tests/integration/test_suppliers.py`
- Delete: `backend/crud.py`

- [ ] **Step 1: Обновить импорты в test_crud_recalculate.py**

Заменить `import crud` на:
```python
from crud.calculations import compute_calculations, compute_full_deviation
```
Заменить все `crud.compute_calculations(` → `compute_calculations(` и `crud.compute_full_deviation(` → `compute_full_deviation(`.

- [ ] **Step 2: Обновить импорты в test_export.py**

Заменить `import crud` на:
```python
from crud.calculations import compute_export_rows
```
Заменить все `crud.compute_export_rows(` → `compute_export_rows(`.

- [ ] **Step 3: Обновить импорты в test_suppliers.py**

Заменить `import crud` на:
```python
from crud.suppliers import get_or_create_supplier
```
Заменить все `crud.get_or_create_supplier(` → `get_or_create_supplier(`.

- [ ] **Step 4: Удалить crud.py**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && rm crud.py"
```

- [ ] **Step 5: Запустить unit-тесты**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1"
```

Ожидаем: все тесты зелёные, никаких `ModuleNotFoundError` или `ImportError`.

- [ ] **Step 6: Запустить linter**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1"
```

Ожидаем: no errors.

- [ ] **Step 7: Коммит и пуш в отдельную ветку**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git checkout -b refactor/crud-package && git add backend/crud/ backend/routers/ backend/pdf_parser.py backend/tests/unit/test_crud_recalculate.py backend/tests/integration/test_export.py backend/tests/integration/test_suppliers.py && git rm backend/crud.py && git commit -m 'refactor: split crud.py into crud/ package with direct imports' && git push -u origin refactor/crud-package"
```

> Мержить в `main` не нужно — ветка `refactor/crud-package` остаётся открытой до review.

---

## Примечание: циклические импорты

`crud/documents.py` импортирует `get_or_create_supplier` из `crud/suppliers.py` — это однонаправленная зависимость, циклов нет. `crud/suppliers.py` импортирует `_aggregate_by_class` из `crud/calculations.py` через локальный импорт внутри функции `_compute_supplier_project_deviation` — это намеренно, чтобы избежать кругового импорта между `suppliers` и `calculations`.

Если понадобится убрать локальный импорт — можно вынести `_aggregate_by_class` в отдельный `crud/_shared.py`, но сейчас это preemptive complexity.

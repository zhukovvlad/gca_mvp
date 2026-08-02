# Supplier Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Позволить пользователю исключать поставщиков из расчётов на уровне объекта через чекбоксы во вкладке «Поставщики».

**Architecture:** Новая таблица `project_supplier_exclusions(project_id, supplier_id, reason, created_at)`. CRUD-функции в `crud/supplier_exclusions.py`. Новые эндпоинты в `routers/projects.py`. Расчётные функции (`compute_calculations`, `compute_full_deviation`, `compute_export_rows`) принимают `excluded_supplier_ids` и фильтруют инвойсы до подсчёта. Фронтенд: новые хуки + переработка таба «Поставщики» в `ProjectPage.tsx`.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), Alembic (миграция), pytest (тесты), React/TypeScript/TanStack Query/shadcn/ui (frontend), Vitest+MSW (frontend тесты).

---

## File Map

**Create:**
- `backend/crud/supplier_exclusions.py` — CRUD для exclusions
- `backend/alembic/versions/XXXX_add_project_supplier_exclusions.py` — миграция
- `backend/tests/unit/test_supplier_exclusions.py` — unit-тесты CRUD
- `backend/tests/integration/test_project_suppliers.py` — интеграционные тесты эндпоинтов

**Modify:**
- `backend/crud/calculations.py` — добавить параметр `excluded_supplier_ids` в три функции
- `backend/routers/projects.py` — добавить 4 новых эндпоинта
- `backend/routers/dashboard.py` — прокидывать exclusions в `compute_calculations` / `compute_full_deviation`
- `backend/routers/export.py` — прокидывать exclusions в `compute_export_rows`
- `backend/tests/test_auth_coverage.py` — зарегистрировать новые публичные пути (нет), просто убедиться что тест проходит
- `frontend/src/services/api/projects.ts` — добавить `projectSuppliersApi`, `supplierExclusionsApi`
- `frontend/src/services/queryKeys.ts` — добавить ключи `projectSuppliers`, `supplierExclusions`
- `frontend/src/services/queries.ts` — добавить хуки `useProjectSuppliers`, `useSupplierExclusions`, `useToggleSupplierExclusion`
- `frontend/src/pages/ProjectPage.tsx` — переработать таб «Поставщики» + баннер в «Обзор»
- `frontend/src/test/handlers.ts` — добавить MSW-хендлеры для новых эндпоинтов

---

## Task 1: Alembic-миграция и ORM-модель

**Files:**
- Create: `backend/alembic/versions/XXXX_add_project_supplier_exclusions.py`
- Modify: `backend/models.py`

- [ ] **Step 1: Сгенерировать миграцию**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate 2>&1 || true"
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && source ../.venv/Scripts/activate 2>/dev/null || true && alembic revision --autogenerate -m 'add_project_supplier_exclusions' 2>&1"
```

Если autogenerate не работает без модели, создай файл миграции вручную (шаг 2).

- [ ] **Step 2: Написать миграцию вручную** (если autogenerate не сработал)

Создай файл `backend/alembic/versions/<timestamp>_add_project_supplier_exclusions.py`:

```python
"""add project_supplier_exclusions

Revision ID: <auto>
Revises: <предыдущая ревизия>
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = '<auto>'
down_revision = '<предыдущая>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_supplier_exclusions",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(now() AT TIME ZONE 'utc')"), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "supplier_id"),
    )


def downgrade() -> None:
    op.drop_table("project_supplier_exclusions")
```

- [ ] **Step 3: Добавить ORM-модель в `backend/models.py`**

После класса `Supplier` добавь:

```python
class ProjectSupplierExclusion(Base):
    __tablename__ = "project_supplier_exclusions"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
```

- [ ] **Step 4: Накатить миграцию**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate 2>&1"
```

Ожидаемый вывод: `Running upgrade ... -> ..., add project_supplier_exclusions`

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/alembic/versions/
git commit -m "feat: add project_supplier_exclusions table and ORM model"
```

---

## Task 2: CRUD-функции для exclusions

**Files:**
- Create: `backend/crud/supplier_exclusions.py`
- Create: `backend/tests/unit/test_supplier_exclusions.py`

- [ ] **Step 1: Написать failing-тесты**

Создай `backend/tests/unit/test_supplier_exclusions.py`:

```python
"""Unit-тесты для crud/supplier_exclusions.py (без БД — чистые функции через mock)."""
from unittest.mock import MagicMock, call

import pytest


def _make_db(rows=None):
    """Минимальный мок SQLAlchemy Session."""
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = rows or []
    return db


class TestGetExcludedSupplierIds:
    def test_returns_empty_set_when_no_exclusions(self):
        from crud.supplier_exclusions import get_excluded_supplier_ids
        db = _make_db(rows=[])
        result = get_excluded_supplier_ids(db, project_id=1)
        assert result == set()

    def test_returns_set_of_supplier_ids(self):
        from crud.supplier_exclusions import get_excluded_supplier_ids
        row1 = MagicMock()
        row1.supplier_id = 5
        row2 = MagicMock()
        row2.supplier_id = 12
        db = _make_db(rows=[row1, row2])
        result = get_excluded_supplier_ids(db, project_id=1)
        assert result == {5, 12}


class TestSetSupplierExcluded:
    def test_adds_exclusion_when_excluded_true(self):
        from crud.supplier_exclusions import set_supplier_excluded
        from models import ProjectSupplierExclusion
        db = MagicMock()
        existing_q = MagicMock()
        db.query.return_value = existing_q
        existing_q.filter.return_value = existing_q
        existing_q.first.return_value = None  # не существует

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=True, reason="Аварийная закупка")

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert isinstance(added, ProjectSupplierExclusion)
        assert added.project_id == 1
        assert added.supplier_id == 7
        assert added.reason == "Аварийная закупка"
        db.commit.assert_called_once()

    def test_noop_when_adding_already_existing_exclusion(self):
        from crud.supplier_exclusions import set_supplier_excluded
        db = MagicMock()
        existing_q = MagicMock()
        db.query.return_value = existing_q
        existing_q.filter.return_value = existing_q
        existing_q.first.return_value = MagicMock()  # уже существует

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=True)

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_deletes_exclusion_when_excluded_false(self):
        from crud.supplier_exclusions import set_supplier_excluded
        existing = MagicMock()
        db = MagicMock()
        existing_q = MagicMock()
        db.query.return_value = existing_q
        existing_q.filter.return_value = existing_q
        existing_q.first.return_value = existing

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=False)

        db.delete.assert_called_once_with(existing)
        db.commit.assert_called_once()

    def test_noop_when_removing_nonexistent_exclusion(self):
        from crud.supplier_exclusions import set_supplier_excluded
        db = MagicMock()
        existing_q = MagicMock()
        db.query.return_value = existing_q
        existing_q.filter.return_value = existing_q
        existing_q.first.return_value = None  # нет записи

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=False)

        db.delete.assert_not_called()
        db.commit.assert_not_called()
```

- [ ] **Step 2: Запустить тесты — убедиться что падают**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | tail -20"
```

Ожидаемый вывод: `ModuleNotFoundError: No module named 'crud.supplier_exclusions'`

- [ ] **Step 3: Создать `backend/crud/supplier_exclusions.py`**

```python
from sqlalchemy.orm import Session

from models import ProjectSupplierExclusion


def get_excluded_supplier_ids(db: Session, project_id: int) -> set[int]:
    """Возвращает множество supplier_id, исключённых из расчётов для данного проекта."""
    rows = (
        db.query(ProjectSupplierExclusion)
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
    """Добавить или убрать исключение поставщика для проекта. Идемпотентно."""
    existing = (
        db.query(ProjectSupplierExclusion)
        .filter(
            ProjectSupplierExclusion.project_id == project_id,
            ProjectSupplierExclusion.supplier_id == supplier_id,
        )
        .first()
    )
    if excluded:
        if existing is None:
            db.add(ProjectSupplierExclusion(
                project_id=project_id,
                supplier_id=supplier_id,
                reason=reason,
            ))
            db.commit()
    else:
        if existing is not None:
            db.delete(existing)
            db.commit()
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | tail -20"
```

Ожидаемый вывод: все тесты в `test_supplier_exclusions.py` PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/crud/supplier_exclusions.py backend/tests/unit/test_supplier_exclusions.py
git commit -m "feat: add supplier_exclusions CRUD with unit tests"
```

---

## Task 3: Фильтрация exclusions в расчётных функциях

**Files:**
- Modify: `backend/crud/calculations.py`

Все три функции получают новый параметр `excluded_supplier_ids: set[int] | None = None`. Фильтр добавляется в запрос invoice_ids.

- [ ] **Step 1: Написать failing unit-тест**

Добавь в конец `backend/tests/unit/test_crud_recalculate.py` (или создай новый файл если там нет подходящего места — проверь что в нём):

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && head -30 backend/tests/unit/test_crud_recalculate.py 2>&1"
```

Если файл существует и содержит integration-тесты с БД — добавлять туда не нужно, напиши отдельный файл `backend/tests/integration/test_calculations_exclusions.py`:

```python
"""Интеграционные тесты: excluded_supplier_ids фильтрует инвойсы из расчётов."""
from datetime import date


def test_excluded_supplier_removed_from_calculations(client, factories):
    """Поставщик A исключён — его инвойсы не входят в avg_price."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")

    supplier_a = factories.SupplierFactory.create()
    supplier_b = factories.SupplierFactory.create()

    doc_a = factories.DocumentFactory.create(project=project)
    inv_a = factories.InvoiceFactory.create(
        document=doc_a, supplier_id=supplier_a.id, date=date(2026, 3, 10), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_a, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    doc_b = factories.DocumentFactory.create(project=project)
    inv_b = factories.InvoiceFactory.create(
        document=doc_b, supplier_id=supplier_b.id, date=date(2026, 3, 20), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_b, material_class=mc, item_type="material",
        quantity=10.0, unit_price=7000.0, amount=70000.0, vat_amount=14000.0,
    )

    from crud.calculations import compute_calculations

    # Без исключений: avg = (108000 + 84000) / 20 = 9600
    rows_all = compute_calculations(
        client.app.dependency_overrides,  # нужна db_session, см. ниже
        project.id,
    )
    # Этот тест использует db_session напрямую, не через client
```

Стоп — `compute_calculations` принимает `db: Session`, не `client`. Используй `db_session` напрямую:

```python
"""Интеграционные тесты: excluded_supplier_ids фильтрует инвойсы из расчётов."""
from datetime import date

import pytest


def test_excluded_supplier_removed_from_calculations(db_session, factories):
    """Поставщик A исключён — его счета не участвуют в avg_price."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")

    supplier_a = factories.SupplierFactory.create()
    supplier_b = factories.SupplierFactory.create()

    doc_a = factories.DocumentFactory.create(project=project)
    inv_a = factories.InvoiceFactory.create(
        document=doc_a, supplier_id=supplier_a.id, date=date(2026, 3, 10), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_a, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    doc_b = factories.DocumentFactory.create(project=project)
    inv_b = factories.InvoiceFactory.create(
        document=doc_b, supplier_id=supplier_b.id, date=date(2026, 3, 20), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_b, material_class=mc, item_type="material",
        quantity=10.0, unit_price=7000.0, amount=70000.0, vat_amount=14000.0,
    )

    from crud.calculations import compute_calculations

    # Без исключений: avg = (90000+18000 + 70000+14000) / 20 = 9600
    rows_all = compute_calculations(db_session, project.id)
    assert len(rows_all) == 1
    assert rows_all[0]["avg_price"] == 9600.0

    # Исключаем supplier_a: avg = (70000+14000) / 10 = 8400
    rows_excl = compute_calculations(
        db_session, project.id, excluded_supplier_ids={supplier_a.id}
    )
    assert len(rows_excl) == 1
    assert rows_excl[0]["avg_price"] == 8400.0


def test_null_supplier_id_not_affected_by_exclusion(db_session, factories):
    """Инвойс без supplier_id всегда участвует в расчётах, даже если передан excluded_supplier_ids."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")

    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(
        document=doc, supplier_id=None, date=date(2026, 3, 15), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=5.0, unit_price=8000.0, amount=40000.0, vat_amount=8000.0,
    )

    from crud.calculations import compute_calculations

    # excluded_supplier_ids={999} не должен убрать инвойс без supplier_id
    rows = compute_calculations(db_session, project.id, excluded_supplier_ids={999})
    assert len(rows) == 1
    assert rows[0]["total_qty"] == 5.0
```

- [ ] **Step 2: Запустить тест — убедиться что падает**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | grep -E 'FAILED|ERROR|test_calculations_exclusions' | head -20"
```

Ожидаемый вывод: `TypeError: compute_calculations() got an unexpected keyword argument 'excluded_supplier_ids'`

- [ ] **Step 3: Изменить `compute_calculations` в `backend/crud/calculations.py`**

Изменить сигнатуру функции (строка ~59):

```python
def compute_calculations(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
) -> list[dict]:
```

Добавить импорт в начало файла:

```python
from sqlalchemy import func, or_
```

(заменить строку `from sqlalchemy import func`)

Найти блок с `invoice_ids_month` (строка ~98) и добавить фильтр сразу после `.filter(...)`:

```python
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
                or_(
                    Invoice.supplier_id.is_(None),
                    Invoice.supplier_id.notin_(excluded_supplier_ids),
                )
            )
        invoice_ids_month = [row[0] for row in invoice_ids_month_q.all()]
```

Заменить существующий блок `invoice_ids_month = [...]` (строки ~98–110) на код выше.

- [ ] **Step 4: Изменить `compute_full_deviation` в `backend/crud/calculations.py`**

```python
def compute_full_deviation(
    db: Session,
    project_id: int,
    period_start: date,
    period_end: date,
    excluded_supplier_ids: set[int] | None = None,
) -> float | None:
    """Compute total deviation_amount for a project over [period_start, period_end].
    Delegates to compute_calculations() — единый источник истины.
    Returns None if no reference prices are available for any class (not 0.0)."""
    rows = compute_calculations(db, project_id, period_start, period_end, excluded_supplier_ids=excluded_supplier_ids)
    amounts = [r["deviation_amount"] for r in rows if r["deviation_amount"] is not None]
    return round(sum(amounts), 2) if amounts else None
```

- [ ] **Step 5: Изменить `compute_export_rows` в `backend/crud/calculations.py`**

Изменить сигнатуру (строка ~294):

```python
def compute_export_rows(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
) -> list[dict]:
```

Найти блок `invoices_raw = (...)` (строка ~327) и добавить фильтр:

```python
    invoices_raw_q = (
        db.query(Invoice.id, Invoice.date, Invoice.number, Invoice.supplier_name, Invoice.vat_rate)
        .join(Document, Invoice.document_id == Document.id)
        .filter(
            Document.project_id == project_id,
            Invoice.date >= period_start,
            Invoice.date <= period_end,
        )
        .order_by(Invoice.date, Invoice.number)
    )
    if excluded_supplier_ids:
        invoices_raw_q = invoices_raw_q.filter(
            or_(
                Invoice.supplier_id.is_(None),
                Invoice.supplier_id.notin_(excluded_supplier_ids),
            )
        )
    invoices_raw = invoices_raw_q.all()
```

Заменить существующий блок `invoices_raw = (...)` (строки ~327–338) на код выше.

- [ ] **Step 6: Запустить тесты — убедиться что проходят**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | grep -E 'PASSED|FAILED|ERROR|test_calculations_exclusions' | head -20"
```

Ожидаемый вывод: оба теста PASSED.

- [ ] **Step 7: Запустить полный backend-тест — убедиться что ничего не сломал**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1 | tail -10"
```

Ожидаемый вывод: все тесты PASSED.

- [ ] **Step 8: Commit**

```bash
git add backend/crud/calculations.py backend/tests/integration/test_calculations_exclusions.py
git commit -m "feat: add excluded_supplier_ids param to compute_calculations/full_deviation/export_rows"
```

---

## Task 4: Эндпоинты в routers/projects.py

**Files:**
- Modify: `backend/routers/projects.py`
- Create: `backend/tests/integration/test_project_suppliers.py`

- [ ] **Step 1: Написать failing интеграционные тесты**

Создай `backend/tests/integration/test_project_suppliers.py`:

```python
"""Интеграционные тесты эндпоинтов поставщиков и исключений проекта."""
from datetime import date


def test_get_project_suppliers_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    assert response.json() == []


def test_get_project_suppliers_returns_suppliers_with_invoice_count(client, factories):
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    doc = factories.DocumentFactory.create(project=project)
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)

    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == supplier.id
    assert body[0]["name"] == supplier.name
    assert body[0]["invoice_count"] == 2


def test_get_project_suppliers_excludes_null_supplier_invoices(client, factories):
    """Инвойсы без supplier_id не должны попадать в список поставщиков."""
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    factories.InvoiceFactory.create(document=doc, supplier_id=None)

    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    assert response.json() == []


def test_get_supplier_exclusions_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.status_code == 200
    assert response.json() == []


def test_add_and_remove_supplier_exclusion(client, factories):
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    # Добавить исключение
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={"reason": "Аварийная закупка"},
    )
    assert response.status_code == 204

    # Проверить список
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.json() == [supplier.id]

    # Снять исключение
    response = client.delete(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}"
    )
    assert response.status_code == 204

    # Список пустой
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.json() == []


def test_add_exclusion_idempotent(client, factories):
    """POST дважды — второй вызов не возвращает ошибку."""
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={},
    )
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={},
    )
    assert response.status_code == 204


def test_add_exclusion_unknown_supplier_returns_404(client, factories):
    project = factories.ProjectFactory.create()
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/99999",
        json={},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Запустить тесты — убедиться что падают**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | grep -E 'FAILED|ERROR|test_project_suppliers' | head -20"
```

Ожидаемый вывод: `404 Not Found` для всех эндпоинтов.

- [ ] **Step 3: Добавить эндпоинты в `backend/routers/projects.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from crud.projects import create_project, delete_project, get_projects, update_project
from crud.supplier_exclusions import get_excluded_supplier_ids, set_supplier_excluded
from database import get_db
from models import Document, Invoice, Supplier

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    contract_number: str | None = None


class ExclusionCreate(BaseModel):
    reason: str | None = None


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = get_projects(db)
    return [{"id": p.id, "name": p.name, "contract_number": p.contract_number, "doc_count": len(p.documents)} for p in projects]


@router.post("")
def create_project_route(data: ProjectCreate, db: Session = Depends(get_db)):
    project = create_project(db, data.name, data.contract_number)
    return {"id": project.id, "name": project.name, "contract_number": project.contract_number}


@router.put("/{project_id}")
def update_project_route(project_id: int, data: ProjectCreate, db: Session = Depends(get_db)):
    project = update_project(db, project_id, data.name, data.contract_number)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"id": project.id, "name": project.name, "contract_number": project.contract_number}


@router.delete("/{project_id}")
def delete_project_route(project_id: int, db: Session = Depends(get_db)):
    project = delete_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"message": "Удалено"}


@router.get("/{project_id}/suppliers")
def list_project_suppliers(project_id: int, db: Session = Depends(get_db)):
    """Список поставщиков проекта с кол-вом счетов. Инвойсы без supplier_id не включаются."""
    rows = (
        db.query(
            Supplier.id,
            Supplier.name,
            Supplier.inn,
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.supplier_id == Supplier.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project_id)
        .group_by(Supplier.id, Supplier.name, Supplier.inn)
        .order_by(Supplier.name)
        .all()
    )
    return [
        {"id": r.id, "name": r.name, "inn": r.inn, "invoice_count": r.invoice_count}
        for r in rows
    ]


@router.get("/{project_id}/supplier-exclusions")
def list_supplier_exclusions(project_id: int, db: Session = Depends(get_db)):
    """Список supplier_id, исключённых из расчётов для данного проекта."""
    return sorted(get_excluded_supplier_ids(db, project_id))


@router.post("/{project_id}/supplier-exclusions/{supplier_id}", status_code=204)
def add_supplier_exclusion(
    project_id: int,
    supplier_id: int,
    data: ExclusionCreate,
    db: Session = Depends(get_db),
):
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    set_supplier_excluded(db, project_id, supplier_id, excluded=True, reason=data.reason)
    return Response(status_code=204)


@router.delete("/{project_id}/supplier-exclusions/{supplier_id}", status_code=204)
def remove_supplier_exclusion(
    project_id: int,
    supplier_id: int,
    db: Session = Depends(get_db),
):
    set_supplier_excluded(db, project_id, supplier_id, excluded=False)
    return Response(status_code=204)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | grep -E 'PASSED|FAILED|ERROR|test_project_suppliers' | head -20"
```

Ожидаемый вывод: все тесты PASSED.

- [ ] **Step 5: Проверить auth coverage guard**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | grep -E 'auth_coverage|PASSED|FAILED' | head -10"
```

Новые эндпоинты подключены через `prefix="/api/projects"` с `_auth_dep` — guard должен пройти без изменений.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/projects.py backend/tests/integration/test_project_suppliers.py
git commit -m "feat: add project suppliers and supplier-exclusions endpoints"
```

---

## Task 5: Прокидывание exclusions в dashboard и export роутеры

**Files:**
- Modify: `backend/routers/dashboard.py`
- Modify: `backend/routers/export.py`

- [ ] **Step 1: Обновить `backend/routers/dashboard.py`**

Добавить импорт:

```python
from crud.supplier_exclusions import get_excluded_supplier_ids
```

В функции `get_project_summary` (строка ~56) перед вызовом `compute_full_deviation`:

```python
    excluded = get_excluded_supplier_ids(db, project_id)
    full_deviation = None
    if first_invoice_date and last_invoice_date:
        period_start = first_invoice_date.replace(day=1)
        last_day = monthrange(last_invoice_date.year, last_invoice_date.month)[1]
        period_end = last_invoice_date.replace(day=last_day)
        full_deviation = compute_full_deviation(
            db, project_id, period_start, period_end,
            excluded_supplier_ids=excluded or None,
        )
```

В функции `list_calculations` (строка ~133) в ветке `project_id is not None`:

```python
    if project_id is None:
        projects = db.query(Project).all()
        rows: list[dict] = []
        for p in projects:
            rows.extend(compute_calculations(db, p.id, period_start, period_end, material_class_id))
    else:
        excluded = get_excluded_supplier_ids(db, project_id)
        rows = compute_calculations(
            db, project_id, period_start, period_end, material_class_id,
            excluded_supplier_ids=excluded or None,
        )
```

- [ ] **Step 2: Обновить `backend/routers/export.py`**

Добавить импорт (в начало файла, рядом с другими crud-импортами):

```python
from crud.supplier_exclusions import get_excluded_supplier_ids
```

Найти вызов `compute_export_rows` в роутере (строка ~конец файла) и добавить параметр:

```python
    excluded = get_excluded_supplier_ids(db, project_id)
    rows = compute_export_rows(
        db,
        project_id=project_id,
        period_start=period_start,
        period_end=period_end,
        material_class_id=material_class_id,
        excluded_supplier_ids=excluded or None,
    )
```

- [ ] **Step 3: Запустить полный backend-тест**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1 | tail -10"
```

Ожидаемый вывод: все тесты PASSED.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/dashboard.py backend/routers/export.py
git commit -m "feat: apply supplier exclusions in dashboard and export routers"
```

---

## Task 6: Frontend — API-клиент и хуки

**Files:**
- Modify: `frontend/src/services/api/projects.ts`
- Modify: `frontend/src/services/queryKeys.ts`
- Modify: `frontend/src/services/queries.ts`

- [ ] **Step 1: Расширить `frontend/src/services/api/projects.ts`**

```typescript
import api from "@/lib/api";
import type {
  Project,
  ProjectCreateInput,
  ProjectUpdateInput,
} from "@/types/project";
import type { ID } from "@/types/common";

export interface ProjectSupplier {
  id: number;
  name: string;
  inn: string | null;
  invoice_count: number;
}

export const projectsApi = {
  async list(): Promise<Project[]> {
    const { data } = await api.get<Project[]>("/projects");
    return data;
  },
  async create(input: ProjectCreateInput): Promise<Project> {
    const { data } = await api.post<Project>("/projects", input);
    return data;
  },
  async update(id: ID, input: ProjectUpdateInput): Promise<Project> {
    const { data } = await api.put<Project>(`/projects/${id}`, input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/projects/${id}`);
  },
  async getSuppliers(projectId: ID): Promise<ProjectSupplier[]> {
    const { data } = await api.get<ProjectSupplier[]>(`/projects/${projectId}/suppliers`);
    return data;
  },
  async getSupplierExclusions(projectId: ID): Promise<number[]> {
    const { data } = await api.get<number[]>(`/projects/${projectId}/supplier-exclusions`);
    return data;
  },
  async addSupplierExclusion(projectId: ID, supplierId: ID, reason?: string): Promise<void> {
    await api.post(`/projects/${projectId}/supplier-exclusions/${supplierId}`, { reason: reason ?? null });
  },
  async removeSupplierExclusion(projectId: ID, supplierId: ID): Promise<void> {
    await api.delete(`/projects/${projectId}/supplier-exclusions/${supplierId}`);
  },
};
```

- [ ] **Step 2: Добавить ключи в `frontend/src/services/queryKeys.ts`**

```typescript
import type { ID } from "@/types/common";

export const qk = {
  projects: { all: ["projects"] as const },
  materialClasses: { all: ["material-classes"] as const },
  referencePrices: {
    all: (projectId?: ID, materialClassId?: ID) => {
      const base = ["reference-prices"] as const;
      if (!projectId && !materialClassId) return base;
      return [...base, ...(projectId ? [projectId] : []), ...(materialClassId ? [{ materialClassId }] : [])] as const;
    },
  },
  documents: {
    list: (projectId?: ID) =>
      projectId ? (["documents", projectId] as const) : (["documents"] as const),
    detail: (docId: ID) => ["document", docId] as const,
  },
  dashboard: {
    summary: (projectId: ID) => ["dashboard", "summary", projectId] as const,
    invoices: (projectId: ID) => ["dashboard", "invoices", projectId] as const,
    calculations: (projectId: ID, periodStart?: string, periodEnd?: string) =>
      ["dashboard", "calculations", projectId, periodStart, periodEnd] as const,
    calculationsAll: ["dashboard", "calculations", "all"] as const,
    monthly: (projectId: ID) => ["dashboard", "monthly", projectId] as const,
  },
  suppliers: {
    all: ["suppliers"] as const,
    detail: (id: ID) => ["suppliers", id] as const,
    projects: (id: ID) => ["suppliers", id, "projects"] as const,
    invoices: (id: ID, projectId?: ID) =>
      projectId !== undefined
        ? (["suppliers", id, "invoices", projectId] as const)
        : (["suppliers", id, "invoices"] as const),
  },
  projectSuppliers: (projectId: ID) => ["project-suppliers", projectId] as const,
  supplierExclusions: (projectId: ID) => ["supplier-exclusions", projectId] as const,
  settings: { current: ["settings"] as const },
};
```

- [ ] **Step 3: Добавить хуки в `frontend/src/services/queries.ts`**

В конец файла добавить:

```typescript
// ========== Project Suppliers & Exclusions ==========

export function useProjectSuppliers(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.projectSuppliers(projectId) : ["project-suppliers-disabled"],
    queryFn: () => projectsApi.getSuppliers(projectId!),
    enabled: projectId !== null,
  });
}

export function useSupplierExclusions(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.supplierExclusions(projectId) : ["supplier-exclusions-disabled"],
    queryFn: async () => {
      const ids = await projectsApi.getSupplierExclusions(projectId!);
      return new Set(ids);
    },
    enabled: projectId !== null,
  });
}

export function useToggleSupplierExclusion(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      supplierId,
      excluded,
      reason,
    }: {
      supplierId: ID;
      excluded: boolean;
      reason?: string;
    }) => {
      if (!projectId) return Promise.resolve();
      return excluded
        ? projectsApi.addSupplierExclusion(projectId, supplierId, reason)
        : projectsApi.removeSupplierExclusion(projectId, supplierId);
    },
    onSuccess: (_data, vars) => {
      if (!projectId) return;
      // Инвалидируем exclusions, расчёты и summary — они теперь изменились
      qc.invalidateQueries({ queryKey: qk.supplierExclusions(projectId) });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculations(projectId) });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
    },
  });
}
```

- [ ] **Step 4: Запустить typecheck**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -10"
```

Ожидаемый вывод: `Found 0 errors.`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/api/projects.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts
git commit -m "feat: add projectSuppliers and supplierExclusions API client and hooks"
```

---

## Task 7: Frontend — переработка таба «Поставщики» и баннер

**Files:**
- Modify: `frontend/src/pages/ProjectPage.tsx`
- Modify: `frontend/src/test/handlers.ts`

- [ ] **Step 1: Добавить MSW-хендлеры в `frontend/src/test/handlers.ts`**

Найди файл и добавь хендлеры для новых эндпоинтов:

```typescript
// Project suppliers
http.get("/api/projects/:projectId/suppliers", () => {
  return HttpResponse.json([]);
}),
http.get("/api/projects/:projectId/supplier-exclusions", () => {
  return HttpResponse.json([]);
}),
http.post("/api/projects/:projectId/supplier-exclusions/:supplierId", () => {
  return new HttpResponse(null, { status: 204 });
}),
http.delete("/api/projects/:projectId/supplier-exclusions/:supplierId", () => {
  return new HttpResponse(null, { status: 204 });
}),
```

- [ ] **Step 2: Запустить frontend-тесты — убедиться что все проходят до изменений в ProjectPage**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -15"
```

Ожидаемый вывод: все тесты PASSED.

- [ ] **Step 3: Добавить импорты и хуки в `frontend/src/pages/ProjectPage.tsx`**

В блок импортов добавить:

```typescript
import { useProjectSuppliers, useSupplierExclusions, useToggleSupplierExclusion } from "@/services/queries";
import { Checkbox } from "@/components/ui/checkbox";
```

В блок `// ── queries ──` добавить:

```typescript
  const projectSuppliersQ = useProjectSuppliers(projectId);
  const supplierExclusionsQ = useSupplierExclusions(projectId);
  const toggleExclusion = useToggleSupplierExclusion(projectId);
```

Добавить state для popover исключения (рядом с другими useState):

```typescript
  const [exclusionPopover, setExclusionPopover] = useState<{
    supplierId: number;
    reason: string;
  } | null>(null);
```

- [ ] **Step 4: Заменить таб «Поставщики» в `ProjectPage.tsx`**

Найти блок `<TabsContent value="suppliers"` (строка ~886) и заменить целиком:

```tsx
          <TabsContent value="suppliers" className="mt-6">
            {projectSuppliersQ.isLoading ? (
              <Skeleton className="h-32" />
            ) : (projectSuppliersQ.data ?? []).length === 0 ? (
              <EmptyState
                title="Нет поставщиков"
                description="Загрузите счета-фактуры, чтобы увидеть поставщиков."
                action={
                  <Button onClick={() => setUploadOpen(true)}>Загрузить</Button>
                }
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border-subtle bg-surface">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-xs text-fg-tertiary">
                      <th className="px-4 py-2 font-medium w-12 text-center" title="Снимите чекбокс, чтобы исключить поставщика из расчётов">В расчётах</th>
                      <th className="px-4 py-2 font-medium">Поставщик</th>
                      <th className="px-4 py-2 font-medium text-right">Счетов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(projectSuppliersQ.data ?? []).map((s) => {
                      const excluded = supplierExclusionsQ.data?.has(s.id) ?? false;
                      const isPopoverOpen = exclusionPopover?.supplierId === s.id;
                      return (
                        <tr
                          key={s.id}
                          className="border-b border-border-subtle last:border-0 hover:bg-surface-hover"
                        >
                          <td className="px-4 py-2 text-center">
                            <Checkbox
                              checked={!excluded}
                              onCheckedChange={(checked) => {
                                if (checked) {
                                  // Включить обратно — без подтверждения
                                  toggleExclusion.mutate({ supplierId: s.id, excluded: false });
                                } else {
                                  // Исключить — открыть popover для ввода причины
                                  setExclusionPopover({ supplierId: s.id, reason: "" });
                                }
                              }}
                            />
                          </td>
                          <td className="px-4 py-2 text-fg">
                            <div>
                              <span className={excluded ? "text-fg-tertiary line-through" : ""}>
                                {s.name}
                              </span>
                              {s.inn && (
                                <span className="ml-2 text-xs text-fg-tertiary">
                                  ИНН {s.inn}
                                </span>
                              )}
                            </div>
                            {isPopoverOpen && (
                              <div className="mt-2 p-3 rounded-lg border border-border-subtle bg-surface shadow-md space-y-2">
                                <p className="text-xs text-fg-secondary">Причина исключения (необязательно)</p>
                                <input
                                  autoFocus
                                  className="w-full rounded border border-border-subtle px-2 py-1 text-sm bg-bg text-fg focus:outline-none focus:ring-1 focus:ring-accent"
                                  placeholder="Аварийная закупка, нерепрезентативная цена..."
                                  value={exclusionPopover.reason}
                                  onChange={(e) =>
                                    setExclusionPopover((prev) =>
                                      prev ? { ...prev, reason: e.target.value } : null
                                    )
                                  }
                                  onKeyDown={(e) => {
                                    if (e.key === "Escape") setExclusionPopover(null);
                                    if (e.key === "Enter") {
                                      toggleExclusion.mutate({
                                        supplierId: s.id,
                                        excluded: true,
                                        reason: exclusionPopover.reason || undefined,
                                      });
                                      setExclusionPopover(null);
                                    }
                                  }}
                                />
                                <div className="flex gap-2 justify-end">
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setExclusionPopover(null)}
                                  >
                                    Отмена
                                  </Button>
                                  <Button
                                    size="sm"
                                    onClick={() => {
                                      toggleExclusion.mutate({
                                        supplierId: s.id,
                                        excluded: true,
                                        reason: exclusionPopover.reason || undefined,
                                      });
                                      setExclusionPopover(null);
                                    }}
                                  >
                                    Исключить
                                  </Button>
                                </div>
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-fg-secondary">
                            {s.invoice_count}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
```

- [ ] **Step 5: Добавить баннер в таб «Обзор»**

Найти в TabsContent value="overview" строку с KPI (после `summaryQ.data && (() => {`) и добавить баннер после закрывающего тега KPI-блока (после `</>` и `})()`):

```tsx
            {/* Exclusion banner */}
            {(supplierExclusionsQ.data?.size ?? 0) > 0 && (
              <div className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface px-4 py-2 text-sm text-fg-secondary -mt-2">
                <span>
                  {supplierExclusionsQ.data!.size}{" "}
                  {pluralRu(supplierExclusionsQ.data!.size, "поставщик исключён", "поставщика исключено", "поставщиков исключено")}{" "}
                  из расчётов
                </span>
                <button
                  className="ml-auto text-xs underline hover:text-fg"
                  onClick={() => setActiveTab("suppliers")}
                >
                  Управление
                </button>
              </div>
            )}
```

Убедись что `pluralRu` импортирован (он уже есть в импортах `ProjectPage.tsx` из `@/lib/format`).

- [ ] **Step 6: Запустить typecheck**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -10"
```

Если `Checkbox` не импортируется — проверь путь. В shadcn/ui это `@/components/ui/checkbox`.

- [ ] **Step 7: Запустить frontend-тесты**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -15"
```

Ожидаемый вывод: все тесты PASSED.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/ProjectPage.tsx frontend/src/test/handlers.ts
git commit -m "feat: supplier exclusion checkbox in project suppliers tab + overview banner"
```

---

## Task 8: Финальная проверка

- [ ] **Step 1: Запустить полный тест-сьют**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test 2>&1 | tail -15"
```

Ожидаемый вывод: все тесты PASSED (backend unit + backend integration + frontend).

- [ ] **Step 2: Запустить линтеры**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1 | tail -10"
```

Ожидаемый вывод: no errors.

- [ ] **Step 3: Финальный commit (если есть незакоммиченные изменения)**

```bash
git status
```

Если чисто — готово. Если что-то осталось:

```bash
git add -p
git commit -m "chore: cleanup after supplier exclusion feature"
```

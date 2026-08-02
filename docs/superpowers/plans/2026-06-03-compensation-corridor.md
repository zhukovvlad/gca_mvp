# Compensation Corridor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-material-class compensation corridor (a tolerance band around the base price, configured per project) that computes the money actually settled under the contract — only the portion of a price change beyond the band.

**Architecture:** A new `compensation_corridors` table binds `(project × material_class) → corridor_pct`. The nonlinear compensation formula is computed inside `compute_calculations` (the single source of truth for price analytics) from the **monthly average price**, since `compensation(monthly avg) ≠ Σ compensation(per row)`. The dashboard endpoint and Excel export consume the result. A new "Коридоры" tab in the project page manages the percentages.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy (sync) / Alembic / pytest + factory_boy + respx (BE); React 18 / TypeScript / TanStack Query v5 / Vitest + MSW v2 (FE).

**Spec:** `docs/superpowers/specs/2026-06-03-compensation-corridor-design.md`

**Commands (Windows):** all `just` commands run via Git bash:
`& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <command> 2>&1"`

---

## File Structure

**Backend — create:**
- `backend/crud/compensation_corridors.py` — CRUD for the corridor table (get list, get map, upsert, delete).
- `backend/alembic/versions/2026_06_03_1200-b8c9d0e1f2a3_add_compensation_corridors.py` — migration.
- `backend/tests/unit/test_compensation.py` — pure-formula unit tests (no DB).
- `backend/tests/integration/test_compensation_corridors.py` — CRUD + `compute_calculations` integration tests.

**Backend — modify:**
- `backend/models.py` — add `CompensationCorridor` model.
- `backend/crud/calculations.py` — load corridors, compute compensation per row in `compute_calculations`.
- `backend/routers/projects.py` — add 3 corridor routes (same pattern as supplier-exclusions).
- `backend/routers/dashboard.py` — serialize 3 new fields in `/calculations`.
- `backend/routers/export.py` — add 2 Excel columns (Коридор %, Компенсация ₽).
- `backend/tests/factories.py` — add `CompensationCorridorFactory`.

**Frontend — create:**
- `frontend/src/types/compensationCorridor.ts` — corridor types.
- `frontend/src/services/api/compensationCorridors.ts` — API methods.
- `frontend/src/components/projects/CorridorsTab.tsx` — the new tab.
- `frontend/src/components/projects/CorridorsTab.test.tsx` — tab tests.

**Frontend — modify:**
- `frontend/src/types/dashboard.ts` — add 3 fields to `DashboardCalculation`.
- `frontend/src/services/queryKeys.ts` — add `compensationCorridors` key.
- `frontend/src/services/queries.ts` — add corridor hooks.
- `frontend/src/pages/ProjectPage.tsx` — mount the tab + add a compensation column to the calculations table.
- `frontend/src/test/handlers.ts` — MSW handlers for 3 new endpoints.

---

## Task 1: Database model + migration

**Files:**
- Modify: `backend/models.py` (after `ProjectSupplierExclusion`, ~line 215)
- Create: `backend/alembic/versions/2026_06_03_1200-b8c9d0e1f2a3_add_compensation_corridors.py`

- [ ] **Step 1: Add the model to `backend/models.py`**

Insert after the `ProjectSupplierExclusion` class (around line 215, before `class Invoice`):

```python
class CompensationCorridor(Base):
    """Коридор компенсации: допуск (%) вокруг базовой цены, в пределах которого
    удорожание/удешевление не компенсируется. Задаётся per (проект × класс материала),
    не периодичен (действует весь срок договора).

    Семантика: нет строки → класс некомпенсируемый; corridor_pct=0 → компенсируется
    любое отклонение (нет мёртвой зоны); corridor_pct=X → допуск ±X%.
    """
    __tablename__ = "compensation_corridors"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    material_class_id = Column(
        Integer, ForeignKey("material_classes.id", ondelete="CASCADE"), primary_key=True
    )
    corridor_pct = Column(Float, nullable=False)  # 5.0 = ±5%; хранится в процентах, не в долях
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
    updated_at = Column(
        DateTime,
        server_default=sa_text("(now() AT TIME ZONE 'utc')"),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )
```

(`Column`, `Float`, `Integer`, `ForeignKey`, `DateTime`, `sa_text`, `datetime`, `UTC` are all already imported at the top of `models.py`.)

- [ ] **Step 2: Create the migration file**

Path: `backend/alembic/versions/2026_06_03_1200-b8c9d0e1f2a3_add_compensation_corridors.py`

```python
"""add compensation_corridors

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-03 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compensation_corridors",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_class_id", sa.Integer(), nullable=False),
        sa.Column("corridor_pct", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_class_id"], ["material_classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "material_class_id"),
    )


def downgrade() -> None:
    op.drop_table("compensation_corridors")
```

- [ ] **Step 3: Verify the migration is the new head and applies cleanly**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate 2>&1"`
Expected: alembic upgrades to `b8c9d0e1f2a3` with no error. (If `a7b8c9d0e1f2` is not the current head, run `cd backend && alembic heads` via bash to find the real head and update `down_revision`.)

- [ ] **Step 4: Commit**

```bash
git add backend/models.py backend/alembic/versions/2026_06_03_1200-b8c9d0e1f2a3_add_compensation_corridors.py
git commit -m "feat(model): add compensation_corridors table"
```

---

## Task 2: CRUD module

**Files:**
- Create: `backend/crud/compensation_corridors.py`
- Test: `backend/tests/integration/test_compensation_corridors.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_compensation_corridors.py`:

```python
from crud.compensation_corridors import (
    delete_corridor,
    get_corridor_map,
    get_corridors,
    set_corridor,
)
from tests.factories import MaterialClassFactory, ProjectFactory


def test_set_corridor_creates_then_get_map(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")

    set_corridor(db_session, project.id, mc.id, 5.0)

    assert get_corridor_map(db_session, project.id) == {mc.id: 5.0}


def test_set_corridor_is_idempotent_upsert(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")

    set_corridor(db_session, project.id, mc.id, 5.0)
    set_corridor(db_session, project.id, mc.id, 7.0)  # overwrite, no duplicate row

    rows = get_corridors(db_session, project.id)
    assert len(rows) == 1
    assert rows[0].corridor_pct == 7.0


def test_delete_corridor_idempotent(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    set_corridor(db_session, project.id, mc.id, 5.0)

    assert delete_corridor(db_session, project.id, mc.id) is True
    assert delete_corridor(db_session, project.id, mc.id) is False  # already gone
    assert get_corridor_map(db_session, project.id) == {}


def test_zero_corridor_is_stored(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    set_corridor(db_session, project.id, mc.id, 0.0)
    assert get_corridor_map(db_session, project.id) == {mc.id: 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: FAIL — `ModuleNotFoundError: No module named 'crud.compensation_corridors'`.

- [ ] **Step 3: Write the CRUD module**

Create `backend/crud/compensation_corridors.py`:

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CompensationCorridor


def get_corridors(db: Session, project_id: int) -> list[CompensationCorridor]:
    """Все строки коридоров проекта (для таба — имена классов джойнятся в роутере)."""
    return (
        db.query(CompensationCorridor)
        .filter(CompensationCorridor.project_id == project_id)
        .all()
    )


def get_corridor_map(db: Session, project_id: int) -> dict[int, float]:
    """{material_class_id: corridor_pct} — для compute_calculations."""
    return {
        row.material_class_id: row.corridor_pct
        for row in get_corridors(db, project_id)
    }


def set_corridor(db: Session, project_id: int, material_class_id: int, corridor_pct: float) -> None:
    """Upsert процента коридора. Идемпотентно и race-safe через composite PK."""
    stmt = (
        pg_insert(CompensationCorridor)
        .values(project_id=project_id, material_class_id=material_class_id, corridor_pct=corridor_pct)
        .on_conflict_do_update(
            index_elements=["project_id", "material_class_id"],
            set_={"corridor_pct": corridor_pct},
        )
    )
    db.execute(stmt)
    db.commit()


def delete_corridor(db: Session, project_id: int, material_class_id: int) -> bool:
    """Снять класс с компенсации. Возвращает True если строка была удалена."""
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_class_id == material_class_id,
        )
        .delete()
    )
    db.commit()
    return deleted > 0
```

- [ ] **Step 4: Add the factory to `backend/tests/factories.py`**

Add `CompensationCorridor` to the model imports (line 11-23 import block) and append this factory at the end of the file:

```python
class CompensationCorridorFactory(_BaseFactory):
    class Meta:
        model = CompensationCorridor

    project = factory.SubFactory(ProjectFactory)
    material_class = factory.SubFactory(MaterialClassFactory)
    corridor_pct = 5.0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: PASS — 4 tests in `test_compensation_corridors.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/crud/compensation_corridors.py backend/tests/integration/test_compensation_corridors.py backend/tests/factories.py
git commit -m "feat(crud): compensation corridor CRUD with idempotent upsert"
```

---

## Task 3: Compensation formula in `compute_calculations`

**Files:**
- Create: `backend/tests/unit/test_compensation.py`
- Modify: `backend/crud/calculations.py` (`compute_calculations`, lines 59-293)

We first extract the pure formula into a standalone function so it can be unit-tested without a DB, then call it from `compute_calculations`.

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_compensation.py`:

```python
import pytest

from crud.calculations import compute_compensation_per_unit


@pytest.mark.parametrize(
    "avg_price, ref_price, corridor_pct, expected",
    [
        (110.0, 100.0, 5.0, 5.0),    # overrun beyond corridor → +5
        (90.0, 100.0, 5.0, -5.0),    # saving beyond corridor → -5
        (103.0, 100.0, 5.0, 0.0),    # inside [95;105] → 0
        (105.0, 100.0, 5.0, 0.0),    # exactly on upper boundary → 0
        (95.0, 100.0, 5.0, 0.0),     # exactly on lower boundary → 0
        (110.0, 100.0, 0.0, 10.0),   # corridor 0% → compensation == deviation
        (90.0, 100.0, 0.0, -10.0),   # corridor 0% both ways
    ],
)
def test_compensation_per_unit(avg_price, ref_price, corridor_pct, expected):
    assert compute_compensation_per_unit(avg_price, ref_price, corridor_pct) == expected


def test_compensation_none_when_no_ref_price():
    assert compute_compensation_per_unit(110.0, None, 5.0) is None
    assert compute_compensation_per_unit(110.0, 0.0, 5.0) is None


def test_compensation_none_when_corridor_not_set():
    assert compute_compensation_per_unit(110.0, 100.0, None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | tail -20"`
Expected: FAIL — `ImportError: cannot import name 'compute_compensation_per_unit'`.

- [ ] **Step 3: Add the pure formula function to `backend/crud/calculations.py`**

Insert near the top, after the imports (after line 7) and before `_months_in_range`:

```python
def compute_compensation_per_unit(
    avg_price: float,
    ref_price: float | None,
    corridor_pct: float | None,
) -> float | None:
    """Компенсация на единицу объёма (нелинейная: 0 внутри коридора, P−B(1±k) вне его).

    Возвращает None если класс некомпенсируемый (corridor_pct is None) или нет базовой
    цены (ref_price falsy). Возвращает 0.0 если цена внутри коридора.

    Знак: + удорожание (доплата поставщику), − экономия (возврат заказчику).
    """
    if corridor_pct is None or not ref_price or ref_price <= 0:
        return None
    k = corridor_pct / 100.0
    upper = ref_price * (1 + k)
    lower = ref_price * (1 - k)
    if avg_price > upper:
        return round(avg_price - upper, 2)
    if avg_price < lower:
        return round(avg_price - lower, 2)
    return 0.0
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | tail -20"`
Expected: PASS — all `test_compensation.py` cases green.

- [ ] **Step 5: Wire the formula into `compute_calculations`**

In `backend/crud/calculations.py`, inside `compute_calculations`:

(a) After the `class_name_map` line (line 100), add a corridor map loaded once per call:

```python
    # Names populated lazily per month and cached across months to avoid a full-table scan
    class_name_map: dict[int, str] = {}

    # Corridor percentages per material class for this project (loaded once).
    # Local import avoids a circular import at module load time.
    from crud.compensation_corridors import get_corridor_map
    corridor_by_class: dict[int, float] = get_corridor_map(db, project_id)
```

(b) Inside the per-class loop, replace the result-dict append (lines 277-291) so it computes and includes the three new fields. The full replacement block (from `ref = ref_by_class.get(cid)` through the `results.append`):

```python
            ref = ref_by_class.get(cid)
            ref_price = ref.price if ref else None
            deviation_pct = None
            deviation_amount = None
            if ref_price and ref_price > 0:
                deviation_pct = round((avg_price - ref_price) / ref_price * 100, 2)
                deviation_amount = round((avg_price - ref_price) * qty, 2)

            corridor_pct = corridor_by_class.get(cid)
            compensation_per_unit = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
            compensation_amount = (
                round(compensation_per_unit * qty, 2)
                if compensation_per_unit is not None
                else None
            )

            results.append({
                "project_id": project_id,
                "material_class_id": cid,
                "material_class_name": class_name_map.get(cid, "?"),
                "period_start": month_start,
                "period_end": month_end,
                "material_total": round(contrib["mat_with_vat"], 2),
                "delivery_total": round(contrib["shared_with_vat"], 2),  # доставка + присадки
                "total_qty": round(qty, 3),
                "avg_price": round(avg_price, 2),
                "invoice_count": len(contrib["invoice_ids"]),
                "reference_price": ref_price,
                "deviation_pct": deviation_pct,
                "deviation_amount": deviation_amount,
                "corridor_pct": corridor_pct,
                "compensation_per_unit": compensation_per_unit,
                "compensation_amount": compensation_amount,
            })
```

- [ ] **Step 6: Write an integration test for the wiring**

Append to `backend/tests/integration/test_compensation_corridors.py`:

```python
from datetime import date

from crud.calculations import compute_calculations
from tests.factories import (
    CompensationCorridorFactory,
    DocumentFactory,
    InvoiceFactory,
    InvoiceItemFactory,
    ReferencePriceFactory,
)


def _make_invoice_with_item(db_session, project, mc, *, qty, unit_price, inv_date):
    doc = DocumentFactory.create(project=project)
    inv = InvoiceFactory.create(document=doc, date=inv_date, vat_rate=0.0)
    InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=qty, unit_price=unit_price, amount=qty * unit_price, vat_amount=0.0,
    )
    return inv


def test_compute_calculations_includes_compensation(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    # base price 100, corridor 5% → upper 105; avg 110 → comp_per_unit = 5, qty 2 → amount 10
    ReferencePriceFactory.create(
        project=project, material_class=mc, price=100.0,
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
    )
    CompensationCorridorFactory.create(project=project, material_class=mc, corridor_pct=5.0)
    _make_invoice_with_item(db_session, project, mc, qty=2.0, unit_price=110.0, inv_date=date(2026, 3, 15))

    rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
    row = next(r for r in rows if r["material_class_id"] == mc.id)
    assert row["corridor_pct"] == 5.0
    assert row["compensation_per_unit"] == 5.0
    assert row["compensation_amount"] == 10.0


def test_compute_calculations_no_corridor_means_none(db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    ReferencePriceFactory.create(
        project=project, material_class=mc, price=100.0,
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
    )
    # no CompensationCorridor row → non-compensated
    _make_invoice_with_item(db_session, project, mc, qty=2.0, unit_price=110.0, inv_date=date(2026, 3, 15))

    rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
    row = next(r for r in rows if r["material_class_id"] == mc.id)
    assert row["corridor_pct"] is None
    assert row["compensation_per_unit"] is None
    assert row["compensation_amount"] is None
```

- [ ] **Step 7: Run all backend tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1 | tail -25"`
Expected: PASS — the two new integration tests and the unit tests are green; nothing else regresses.

- [ ] **Step 8: Commit**

```bash
git add backend/crud/calculations.py backend/tests/unit/test_compensation.py backend/tests/integration/test_compensation_corridors.py
git commit -m "feat(calc): compute monthly compensation in compute_calculations"
```

---

## Task 4: API routes for corridors

**Files:**
- Modify: `backend/routers/projects.py` (add routes after the supplier-exclusion routes, ~line 106)
- Test: `backend/tests/integration/test_compensation_corridors.py` (append API tests)

- [ ] **Step 1: Write the failing API test**

Append to `backend/tests/integration/test_compensation_corridors.py`:

```python
def test_put_and_list_corridor_via_api(client, db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")

    r = client.put(
        f"/api/projects/{project.id}/compensation-corridors/{mc.id}",
        json={"corridor_pct": 5.0},
    )
    assert r.status_code == 200

    r = client.get(f"/api/projects/{project.id}/compensation-corridors")
    assert r.status_code == 200
    body = r.json()
    assert body == [
        {
            "material_class_id": mc.id,
            "material_class_name": "В25",
            "material_type": "concrete",
            "corridor_pct": 5.0,
        }
    ]


def test_put_corridor_rejects_out_of_range(client, db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    r = client.put(
        f"/api/projects/{project.id}/compensation-corridors/{mc.id}",
        json={"corridor_pct": 150.0},
    )
    assert r.status_code == 422


def test_delete_corridor_via_api(client, db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    client.put(
        f"/api/projects/{project.id}/compensation-corridors/{mc.id}",
        json={"corridor_pct": 5.0},
    )
    r = client.delete(f"/api/projects/{project.id}/compensation-corridors/{mc.id}")
    assert r.status_code == 204
    assert client.get(f"/api/projects/{project.id}/compensation-corridors").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: FAIL — 404/405 (routes not defined yet).

- [ ] **Step 3: Add routes to `backend/routers/projects.py`**

(a) Extend the imports at the top (line 6-9 area):

```python
from crud.compensation_corridors import (
    delete_corridor,
    get_corridors,
    set_corridor,
)
from crud.supplier_exclusions import get_excluded_supplier_ids, set_supplier_excluded
from database import get_db
from models import Document, Invoice, MaterialClass, Project, Supplier
```

(`MaterialClass` is added to the existing models import.)

(b) Add a request body model near the other Pydantic models (after `ExclusionCreate`, ~line 20):

```python
class CorridorUpsert(BaseModel):
    corridor_pct: float = Field(ge=0, le=100)
```

And add `Field` to the pydantic import at the top: `from pydantic import BaseModel, Field`.

(c) Append the routes after `remove_supplier_exclusion` (~line 106):

```python
@router.get("/{project_id}/compensation-corridors")
def list_compensation_corridors(project_id: int, db: Session = Depends(get_db)):
    """Коридоры компенсации проекта с именами классов материалов."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    corridors = get_corridors(db, project_id)
    class_ids = [c.material_class_id for c in corridors]
    name_map = {
        mc.id: (mc.name, mc.material_type)
        for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(class_ids)).all()
    }
    return [
        {
            "material_class_id": c.material_class_id,
            "material_class_name": name_map.get(c.material_class_id, ("?", "?"))[0],
            "material_type": name_map.get(c.material_class_id, ("?", "?"))[1],
            "corridor_pct": c.corridor_pct,
        }
        for c in corridors
    ]


@router.put("/{project_id}/compensation-corridors/{material_class_id}")
def upsert_compensation_corridor(
    project_id: int,
    material_class_id: int,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    if not db.query(MaterialClass).filter(MaterialClass.id == material_class_id).first():
        raise HTTPException(status_code=404, detail="Класс материала не найден")
    set_corridor(db, project_id, material_class_id, data.corridor_pct)
    return {"material_class_id": material_class_id, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/compensation-corridors/{material_class_id}", status_code=204)
def delete_compensation_corridor(
    project_id: int,
    material_class_id: int,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    delete_corridor(db, project_id, material_class_id)
    return Response(status_code=204)
```

(`Response` is already imported in `projects.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: PASS — the 3 new API tests green.

- [ ] **Step 5: Run auth coverage guardian**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1 | tail -15"`
Expected: PASS — `test_auth_coverage.py` automatically covers the new routes (401/403 without a token).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/projects.py backend/tests/integration/test_compensation_corridors.py
git commit -m "feat(api): compensation corridor GET/PUT/DELETE routes"
```

---

## Task 5: Expose new fields in the dashboard endpoint

**Files:**
- Modify: `backend/routers/dashboard.py` (serialization block, lines 176-192)
- Test: `backend/tests/integration/test_compensation_corridors.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_compensation_corridors.py`:

```python
def test_dashboard_calculations_exposes_compensation(client, db_session, factories):
    project = ProjectFactory.create()
    mc = MaterialClassFactory.create(material_type="concrete", name="В25")
    ReferencePriceFactory.create(
        project=project, material_class=mc, price=100.0,
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
    )
    CompensationCorridorFactory.create(project=project, material_class=mc, corridor_pct=5.0)
    _make_invoice_with_item(db_session, project, mc, qty=2.0, unit_price=110.0, inv_date=date(2026, 3, 15))

    r = client.get(f"/api/dashboard/calculations?project_id={project.id}")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["material_class_id"] == mc.id)
    assert row["corridor_pct"] == 5.0
    assert row["compensation_per_unit"] == 5.0
    assert row["compensation_amount"] == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: FAIL — `KeyError: 'corridor_pct'` (serialization omits the new fields).

- [ ] **Step 3: Add the three fields to the serialization in `backend/routers/dashboard.py`**

In the `return [ {...} for r in rows ]` block (lines 176-192), add three keys after `"deviation_amount"`:

```python
            "deviation_pct": r["deviation_pct"],
            "deviation_amount": r["deviation_amount"],
            "corridor_pct": r["corridor_pct"],
            "compensation_per_unit": r["compensation_per_unit"],
            "compensation_amount": r["compensation_amount"],
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -20"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/dashboard.py backend/tests/integration/test_compensation_corridors.py
git commit -m "feat(api): expose compensation fields in dashboard calculations"
```

---

## Task 6: Excel export columns

**Files:**
- Modify: `backend/routers/export.py`

Compensation is nonlinear → computed per month on the Python side and placed only at the monthly-subtotal and class-grand-total levels. We build a lookup from `compute_calculations` keyed by `(class_id, year, month)` and read it when rendering month headers and the class total. Per-invoice data rows leave both new columns blank.

- [ ] **Step 1: Add the two columns to `_COLUMNS`**

In `backend/routers/export.py`, append two entries to the `_COLUMNS` list (after the `"Откл. от плана, ₽"` entry, ~line 98):

```python
    ("Откл. от плана, ₽",           18, _FMT_MONEY,      "right"),   # P 16  formula
    ("Коридор, %",                  11, _FMT_PCT_RATE,  "center"),  # Q 17  static (decimal)
    ("Компенсация, ₽",              18, _FMT_MONEY,      "right"),   # R 18  Python value
```

Note: `corridor_pct` is stored as a percent (5.0). To display via `_FMT_PCT_RATE` (`0%`, expects a decimal), write `corridor_pct / 100.0` into the cell.

- [ ] **Step 2: Build the compensation lookup in `export_excel`**

In `export_excel` (after `rows = compute_export_rows(...)`, ~line 376), add a parallel monthly computation and a lookup map:

```python
    from crud.calculations import compute_calculations
    monthly_rows = compute_calculations(
        db, project_id, period_start, period_end, material_class_id,
        excluded_supplier_ids=excluded or None,
    )
    # (class_id, year, month) → {"corridor_pct": float|None, "compensation_amount": float|None}
    comp_by_class_month: dict[tuple[int, int, int], dict] = {
        (m["material_class_id"], m["period_start"].year, m["period_start"].month): {
            "corridor_pct": m["corridor_pct"],
            "compensation_amount": m["compensation_amount"],
        }
        for m in monthly_rows
    }
```

- [ ] **Step 3: Thread the lookup into `_write_class_section`**

Change the signature of `_write_class_section` (line 160) to accept the lookup and the class id, and pass them from the call site.

Call site (~line 437-438) — `groupby` yields rows that carry `material_class_id`, so read it from the first row of the group:

```python
        for class_name, group in groupby(rows, key=lambda r: r["material_class_name"]):
            group_rows = list(group)
            cur = _write_class_section(
                ws, class_name, group_rows, cur,
                comp_by_class_month=comp_by_class_month,
                material_class_id=group_rows[0]["material_class_id"],
            )
```

Signature:

```python
def _write_class_section(
    ws,
    class_name: str,
    rows: list[dict],
    start_row: int,
    comp_by_class_month: dict[tuple[int, int, int], dict],
    material_class_id: int,
) -> int:
```

- [ ] **Step 4: Write compensation into each month header and the class total**

In `_write_class_section`, inside the month loop, after the existing `_hc(15, ...)` deviation-% line (~line 265-266), add the corridor % and compensation for that month:

```python
        month_key = (material_class_id, year, month)
        month_comp = comp_by_class_month.get(month_key, {})
        _corridor = month_comp.get("corridor_pct")
        _comp_amt = month_comp.get("compensation_amount")
        _hc(17, (_corridor / 100.0) if _corridor is not None else None, fmt=_FMT_PCT_RATE)
        _hc(18, _comp_amt, font=_dev_font(_comp_amt or 0, bold=True), fmt=_FMT_MONEY)
```

After the month loop, compute the class-level compensation total (sum of monthly compensations for this class) and pass it to the grand-total row. Just before the `_write_grand_total_row(...)` call (~line 340), compute:

```python
    class_comp_total = sum(
        v["compensation_amount"]
        for (cid, _y, _m), v in comp_by_class_month.items()
        if cid == material_class_id and v["compensation_amount"] is not None
    )
```

- [ ] **Step 5: Add the compensation cell to the grand-total row**

`_write_grand_total_row` (line 111) needs to write column 18. Add a parameter `comp_total: float | None` to its signature and, inside the function (after the `_c(15, ...)` deviation line, ~line 155), write:

```python
    _c(17, None)  # Коридор % — not aggregated at class level
    _c(18, comp_total, font=_dev_font(comp_total or 0, bold=True, size=12), fmt=_FMT_MONEY)
```

Update the call (~line 340) to pass `comp_total=class_comp_total`.

- [ ] **Step 6: Add a footnote explaining the blank per-invoice cells**

In `export_excel`, extend the footer text (~line 444) to a second note. Replace the single footer line with two stacked lines, or append to the existing string:

```python
        value="* Стоимость доставки и прочих включений распределена пропорционально объёму м³ "
              "каждого класса материала в рамках каждой СФ.  "
              "** Компенсация считается от средней цены за месяц и показана в строках месяца и итога; "
              "по отдельным СФ не определяется.",
```

- [ ] **Step 7: Verify the export renders without error**

There is an existing export test; run the backend suite to confirm nothing breaks:

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1 | tail -25"`
Expected: PASS — existing export tests still green (the workbook builds; new columns/cells don't raise).

- [ ] **Step 8: Lint backend**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1 | tail -20"`
Expected: ruff clean (fix any import-order / unused-import issues it flags).

- [ ] **Step 9: Commit**

```bash
git add backend/routers/export.py
git commit -m "feat(export): add corridor % and monthly compensation columns to Excel"
```

---

## Task 7: Frontend types, API, query hooks

**Files:**
- Create: `frontend/src/types/compensationCorridor.ts`
- Create: `frontend/src/services/api/compensationCorridors.ts`
- Modify: `frontend/src/types/dashboard.ts` (add 3 fields)
- Modify: `frontend/src/services/queryKeys.ts` (add key)
- Modify: `frontend/src/services/queries.ts` (add hooks)
- Modify: `frontend/src/test/handlers.ts` (MSW handlers)

- [ ] **Step 1: Create the corridor type**

Create `frontend/src/types/compensationCorridor.ts`:

```typescript
export interface CompensationCorridor {
  material_class_id: number;
  material_class_name: string;
  material_type: string;
  corridor_pct: number;
}
```

- [ ] **Step 2: Add the 3 fields to `DashboardCalculation`**

In `frontend/src/types/dashboard.ts`, add to the `DashboardCalculation` interface (after `deviation_amount`):

```typescript
  deviation_pct: number | null;
  deviation_amount: number | null;
  /** Процент коридора компенсации; null → класс некомпенсируемый. */
  corridor_pct: number | null;
  /** Компенсация на единицу объёма; null → не применимо, 0 → внутри коридора. */
  compensation_per_unit: number | null;
  /** Компенсация за период по классу (₽); null → не применимо. */
  compensation_amount: number | null;
```

- [ ] **Step 3: Create the API module**

Create `frontend/src/services/api/compensationCorridors.ts`:

```typescript
import api from "@/lib/api";
import type { ID } from "@/types/common";
import type { CompensationCorridor } from "@/types/compensationCorridor";

export const compensationCorridorsApi = {
  async list(projectId: ID): Promise<CompensationCorridor[]> {
    const { data } = await api.get<CompensationCorridor[]>(
      `/projects/${projectId}/compensation-corridors`,
    );
    return data;
  },
  async set(projectId: ID, materialClassId: ID, corridorPct: number): Promise<void> {
    await api.put(
      `/projects/${projectId}/compensation-corridors/${materialClassId}`,
      { corridor_pct: corridorPct },
    );
  },
  async remove(projectId: ID, materialClassId: ID): Promise<void> {
    await api.delete(`/projects/${projectId}/compensation-corridors/${materialClassId}`);
  },
};
```

- [ ] **Step 4: Add the query key**

In `frontend/src/services/queryKeys.ts`, add to the `qk` object (after `supplierExclusions`):

```typescript
  supplierExclusions: (projectId: ID) => ["supplier-exclusions", projectId] as const,
  compensationCorridors: (projectId: ID) => ["compensation-corridors", projectId] as const,
};
```

- [ ] **Step 5: Add the query hooks**

In `frontend/src/services/queries.ts`, add the import at the top with the other api imports:

```typescript
import { compensationCorridorsApi } from "./api/compensationCorridors";
```

And append the hooks (near `useToggleSupplierExclusion`, after line 450):

```typescript
export function useCompensationCorridors(projectId: ID | null) {
  return useQuery({
    queryKey: projectId
      ? qk.compensationCorridors(projectId)
      : ["compensation-corridors-disabled"],
    queryFn: () => compensationCorridorsApi.list(projectId!),
    enabled: projectId !== null,
  });
}

export function useSetCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ materialClassId, corridorPct }: { materialClassId: ID; corridorPct: number }) => {
      if (!projectId) return Promise.resolve();
      return compensationCorridorsApi.set(projectId, materialClassId, corridorPct);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.compensationCorridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}

export function useDeleteCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (materialClassId: ID) => {
      if (!projectId) return Promise.resolve();
      return compensationCorridorsApi.remove(projectId, materialClassId);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.compensationCorridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}
```

- [ ] **Step 6: Add MSW handlers**

In `frontend/src/test/handlers.ts`, add after the supplier-exclusion handlers (~line 120):

```typescript
  http.get("/api/projects/:projectId/compensation-corridors", () => HttpResponse.json([])),
  http.put("/api/projects/:projectId/compensation-corridors/:materialClassId", () =>
    HttpResponse.json({ material_class_id: 1, corridor_pct: 5 }),
  ),
  http.delete("/api/projects/:projectId/compensation-corridors/:materialClassId", () =>
    new HttpResponse(null, { status: 204 }),
  ),
```

- [ ] **Step 7: Typecheck**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -20"`
Expected: tsc clean (the new `corridor_pct`/`compensation_*` fields on `DashboardCalculation` must not break existing consumers — they're additive and nullable).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/compensationCorridor.ts frontend/src/services/api/compensationCorridors.ts frontend/src/types/dashboard.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts frontend/src/test/handlers.ts
git commit -m "feat(fe): compensation corridor types, api, query hooks"
```

---

## Task 8: CorridorsTab component

**Files:**
- Create: `frontend/src/components/projects/CorridorsTab.tsx`
- Test: `frontend/src/components/projects/CorridorsTab.test.tsx`

The tab lists the project's material classes (from `useMaterialClasses`) joined with corridors (from `useCompensationCorridors`). A class with a corridor shows its %, an "изменить" affordance, and a "снять" button (DELETE, no dialog). A class without a corridor shows a "Сделать компенсируемым" inline input.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/projects/CorridorsTab.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import { CorridorsTab } from "./CorridorsTab";

const CLASSES = [
  { id: 1, material_type: "concrete", name: "В25", created_at: "2026-01-01T00:00:00Z" },
  { id: 2, material_type: "rebar", name: "d12", created_at: "2026-01-01T00:00:00Z" },
];

function mockClasses() {
  server.use(http.get("/api/material-classes", () => HttpResponse.json(CLASSES)));
}

describe("CorridorsTab", () => {
  it("shows compensated class with its percent and non-compensated with add button", async () => {
    mockClasses();
    server.use(
      http.get("/api/projects/:projectId/compensation-corridors", () =>
        HttpResponse.json([
          { material_class_id: 1, material_class_name: "В25", material_type: "concrete", corridor_pct: 5 },
        ]),
      ),
    );

    renderWithProviders(<CorridorsTab projectId={42} />);

    expect(await screen.findByText("В25")).toBeInTheDocument();
    // compensated → shows 5%
    expect(await screen.findByText(/5/)).toBeInTheDocument();
    // non-compensated rebar → shows the make-compensated affordance
    expect(await screen.findByText("d12")).toBeInTheDocument();
  });

  it("sends PUT when entering a percent for a non-compensated class", async () => {
    const onPut = vi.fn();
    mockClasses();
    server.use(
      http.get("/api/projects/:projectId/compensation-corridors", () => HttpResponse.json([])),
      http.put(
        "/api/projects/:projectId/compensation-corridors/:materialClassId",
        async ({ params, request }) => {
          onPut({ materialClassId: params.materialClassId, body: await request.json() });
          return HttpResponse.json({ material_class_id: 1, corridor_pct: 5 });
        },
      ),
    );

    renderWithProviders(<CorridorsTab projectId={42} />);

    const addButtons = await screen.findAllByRole("button", { name: /Сделать компенсируемым/ });
    await userEvent.click(addButtons[0]);
    const input = await screen.findByLabelText("Процент коридора");
    await userEvent.type(input, "5{Enter}");

    await waitFor(() => expect(onPut).toHaveBeenCalledWith({
      materialClassId: "1",
      body: { corridor_pct: 5 },
    }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -25"`
Expected: FAIL — cannot resolve `./CorridorsTab`.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/projects/CorridorsTab.tsx`:

```typescript
import { useMemo, useState } from "react";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  useMaterialClasses,
  useCompensationCorridors,
  useSetCorridor,
  useDeleteCorridor,
} from "@/services/queries";
import type { ID } from "@/types/common";

export function CorridorsTab({ projectId }: { projectId: ID }) {
  const classesQ = useMaterialClasses();
  const corridorsQ = useCompensationCorridors(projectId);
  const setCorridor = useSetCorridor(projectId);
  const deleteCorridor = useDeleteCorridor(projectId);

  const [editingId, setEditingId] = useState<ID | null>(null);
  const [draft, setDraft] = useState("");

  const corridorByClass = useMemo(() => {
    const m = new Map<ID, number>();
    for (const c of corridorsQ.data ?? []) m.set(c.material_class_id, c.corridor_pct);
    return m;
  }, [corridorsQ.data]);

  if (classesQ.isLoading || corridorsQ.isLoading) {
    return <Skeleton className="h-40" />;
  }

  const classes = classesQ.data ?? [];

  function startEdit(id: ID, current: number | undefined) {
    setEditingId(id);
    setDraft(current != null ? String(current) : "");
  }

  function commit(id: ID) {
    const pct = parseFloat(draft.replace(",", "."));
    if (Number.isFinite(pct) && pct >= 0 && pct <= 100) {
      setCorridor.mutate({ materialClassId: id, corridorPct: pct });
    }
    setEditingId(null);
    setDraft("");
  }

  return (
    <Surface padding="none" className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="text-xs text-fg-tertiary hover:bg-transparent">
            <TableHead className="font-medium">Класс</TableHead>
            <TableHead className="font-medium text-right">Коридор, %</TableHead>
            <TableHead className="font-medium text-right">Действия</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {classes.map((mc) => {
            const pct = corridorByClass.get(mc.id);
            const isEditing = editingId === mc.id;
            return (
              <TableRow key={mc.id}>
                <TableCell className="text-fg">{mc.name}</TableCell>
                <TableCell className="text-right font-mono">
                  {isEditing ? (
                    <input
                      aria-label="Процент коридора"
                      autoFocus
                      className="w-20 rounded border border-border-subtle bg-surface px-2 py-1 text-right"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commit(mc.id);
                        if (e.key === "Escape") { setEditingId(null); setDraft(""); }
                      }}
                      onBlur={() => commit(mc.id)}
                    />
                  ) : pct != null ? (
                    `${pct}%`
                  ) : (
                    <span className="text-fg-tertiary">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {pct != null ? (
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => startEdit(mc.id, pct)}>
                        Изменить
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteCorridor.mutate(mc.id)}
                      >
                        Снять
                      </Button>
                    </div>
                  ) : (
                    !isEditing && (
                      <Button variant="ghost" size="sm" onClick={() => startEdit(mc.id, undefined)}>
                        Сделать компенсируемым
                      </Button>
                    )
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Surface>
  );
}
```

Note: verify the actual import paths for `Button`, `Skeleton`, `Surface`, and `useMaterialClasses` against the codebase before finalizing (e.g. `useMaterialClasses` may be named differently — grep `services/queries.ts` for the material-classes hook). Adjust imports to match.

- [ ] **Step 4: Run test to verify it passes**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -25"`
Expected: PASS — both `CorridorsTab` tests green. Fix import names if the test reports unresolved modules.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/projects/CorridorsTab.tsx frontend/src/components/projects/CorridorsTab.test.tsx
git commit -m "feat(fe): CorridorsTab for managing compensation percentages"
```

---

## Task 9: Mount the tab + compensation column in ProjectPage

**Files:**
- Modify: `frontend/src/pages/ProjectPage.tsx`

- [ ] **Step 1: Import the tab**

Add near the other project-component imports (~line 17-18):

```typescript
import { CorridorsTab } from "@/components/projects/CorridorsTab";
```

- [ ] **Step 2: Add the tab trigger**

In the `TabsList` (~line 393), add a trigger after "Базовые цены":

```typescript
            <TabsTrigger value="prices" data-testid="project-tab-prices">Базовые цены</TabsTrigger>
            <TabsTrigger value="corridors" data-testid="project-tab-corridors">Коридоры</TabsTrigger>
```

- [ ] **Step 3: Add the tab content**

After the `prices` `TabsContent` block closes, add (place it logically near the prices tab content):

```typescript
          <TabsContent value="corridors" className="mt-6">
            {projectId !== null && <CorridorsTab projectId={projectId} />}
          </TabsContent>
```

Note: ProjectPage holds the id in `projectId: ID | null` (from `useParams` → `Number(id)`, line 80), **not** `project.id` (`project` may be `null`). Use `projectId` with the null guard above — same variable the other tabs and queries use.

- [ ] **Step 4: Add the compensation column header to the calculations table**

In the calculations table header (~line 539), add a header after "Откл.₽":

```typescript
                      <TableHead className="font-medium text-right">Откл.₽</TableHead>
                      <TableHead className="font-medium text-right">Компенсация</TableHead>
                      <TableHead className="font-medium text-right">Объём</TableHead>
```

- [ ] **Step 5: Add the compensation cell to each data row**

In the calculations table body, after the deviation-amount `TableCell` (~line 598, before the Объём cell), add:

```typescript
                        <TableCell
                          className={
                            "text-right font-mono " +
                            (c.compensation_amount == null
                              ? "text-fg-secondary"
                              : c.compensation_amount > 0
                              ? "text-danger-text"
                              : c.compensation_amount < 0
                              ? "text-accent-text"
                              : "text-fg-secondary")
                          }
                        >
                          {c.compensation_amount !== null
                            ? formatMoney(c.compensation_amount)
                            : "—"}
                        </TableCell>
```

(`formatMoney` is already imported in this file.)

- [ ] **Step 6: Typecheck and run frontend tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -15"`
Expected: tsc clean.

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -25"`
Expected: PASS — existing ProjectPage tests still green (the added column/tab are additive; MSW handlers from Task 7 cover the new endpoints; the calculations handler returns `[]` so the new column renders nothing in those tests).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ProjectPage.tsx
git commit -m "feat(fe): mount Коридоры tab and compensation column in ProjectPage"
```

---

## Task 10: Full verification + docs

**Files:**
- Modify: `CLAUDE.md` (document the feature)

- [ ] **Step 1: Run the full test suite + lint + typecheck**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test 2>&1 | tail -30"`
Expected: PASS — backend + frontend.

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1 | tail -20"`
Expected: clean.

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -15"`
Expected: clean.

- [ ] **Step 2: Document the feature in CLAUDE.md**

Add a section after the "Методология расчёта avg_price" block describing:
- the `CompensationCorridor` model (`(project × material_class)`, `corridor_pct` 0–100, non-periodic);
- three-state semantics (no row / 0% / X%);
- the nonlinear formula and why it's monthly (not summable per-row);
- that `compute_calculations` is where it's computed (single source of truth) and `compute_compensation_per_unit` is the pure formula;
- the API routes (`GET/PUT/DELETE /api/projects/{id}/compensation-corridors`);
- the Excel columns (Q Коридор %, R Компенсация ₽ — month/total only);
- the Коридоры tab.

Also add `CompensationCorridor` to the models list in the project-structure section.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document compensation corridor feature in CLAUDE.md"
```

---

## Self-Review notes (addressed during planning)

- **Spec coverage:** model (T1), formula+monthly computation (T3), CRUD+idempotency (T2), API GET/PUT/DELETE + validation (T4), dashboard fields (T5), Excel 2 columns month/total-only (T6), tab + DELETE-without-dialog (T8/T9), calculations-screen column (T9), unit+integration+auth-coverage+factory tests (T2/T3/T4/T5/T8). All spec sections map to a task.
- **`None` vs `0.0`:** `compute_compensation_per_unit` returns `None` (non-compensated / no base) vs `0.0` (inside corridor) — tested in T3 unit cases; UI/Excel branch on `null` → "—", `0` → "0 ₽"/grey.
- **Type consistency:** `compute_compensation_per_unit(avg_price, ref_price, corridor_pct)` signature is identical across T3 definition and T3 call site; field names `corridor_pct`/`compensation_per_unit`/`compensation_amount` are identical across BE dict (T3), dashboard serialization (T5), FE type (T7), and FE consumers (T9).
- **Resolved during planning:** `useMaterialClasses` hook exists (`services/queries.ts:71`); `Button`/`Surface`/`Skeleton` all live in `@/components/ui-domain/` with `variant="ghost"` + `size="sm"` valid; ProjectPage uses `projectId: ID | null` (line 80), not `project.id` — T9 fixed to use it with a null guard.
- **One remaining flag for the implementer:** confirm the real alembic head for `down_revision` before applying the migration (T1 Step 3) — the plan assumes `a7b8c9d0e1f2` (the organization_kind migration) is current head; run `alembic heads` if `just db-migrate` errors on a missing revision.

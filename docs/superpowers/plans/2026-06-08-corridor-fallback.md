# Corridor Fallback & Per-Project Compensability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `(project_id, material_class_id)` corridor table with a hierarchical type→class fallback system and explicit per-project `is_compensable` flag.

**Architecture:** Single `compensation_corridors` table with surrogate PK, `material_type` XOR `material_class_id` targeting, `is_compensable` boolean + nullable `corridor_pct`. Batch resolution in Python (one query per project, two dicts). Whitelist default: no row = not compensable.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, PostgreSQL, React 18 + TypeScript + TanStack Query v5 + shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-06-08-corridor-fallback-design.md`

---

## File Structure

### Backend — new/modified

| File | Responsibility |
|---|---|
| `backend/models.py:218-238` | Modify: restructure `CompensationCorridor` model |
| `backend/crud/compensation_corridors.py` | Rewrite: new CRUD (get_corridor_map, resolve_corridor, upsert/delete type/class, resolved matrix) |
| `backend/crud/calculations.py:126-129,307-313` | Modify: integrate new `resolve_corridor` (needs `material_type` per class) |
| `backend/routers/projects.py:7,25-27,115-161` | Modify: replace old corridor endpoints with new type/class endpoints |
| `backend/alembic/versions/2026_06_08_..._corridor_fallback.py` | Create: migration DROP old + CREATE new |
| `backend/tests/factories.py:154-160` | Modify: update `CompensationCorridorFactory` |
| `backend/tests/unit/test_resolve_corridor.py` | Create: unit tests for resolve_corridor + Pydantic validator |
| `backend/tests/unit/test_compensation.py` | Modify: add test for `is_compensable=false` path |
| `backend/tests/integration/test_compensation_corridors.py` | Rewrite: tests for new API + calculation integration |

### Frontend — new/modified

| File | Responsibility |
|---|---|
| `frontend/src/types/compensationCorridor.ts` | Rewrite: types for resolved matrix response |
| `frontend/src/services/api/compensationCorridors.ts` | Rewrite: new API functions (type/class CRUD) |
| `frontend/src/services/queryKeys.ts:44` | Modify: update key name |
| `frontend/src/services/queries.ts:453-495` | Rewrite: new hooks (4 mutations + 1 query) |
| `frontend/src/components/projects/CorridorsTab.tsx` | Rewrite: grouped table UI |
| `frontend/src/test/handlers.ts:124-131` | Modify: MSW handlers for new endpoints |

---

## Task 1: Alembic Migration

**Files:**
- Create: `backend/alembic/versions/2026_06_08_1200-d0e1f2a3b4c5_corridor_fallback.py`

- [ ] **Step 1: Create migration file**

```bash
cd /c/Users/zhukov_v/Projects/UDP && just db-revision "corridor_fallback"
```

Then replace the generated content with:

```python
"""corridor_fallback

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-08 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("compensation_corridors")

    op.create_table(
        "compensation_corridors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_type", sa.String(), nullable=True),
        sa.Column("material_class_id", sa.Integer(), nullable=True),
        sa.Column("is_compensable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("corridor_pct", sa.Numeric(5, 2), nullable=True),
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
        sa.CheckConstraint(
            "(material_type IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive",
        ),
        sa.CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable",
        ),
    )
    op.create_index(
        "uq_corridor_project_type",
        "compensation_corridors",
        ["project_id", "material_type"],
        unique=True,
        postgresql_where=sa.text("material_class_id IS NULL"),
    )
    op.create_index(
        "uq_corridor_project_class",
        "compensation_corridors",
        ["project_id", "material_class_id"],
        unique=True,
        postgresql_where=sa.text("material_type IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("compensation_corridors")

    op.create_table(
        "compensation_corridors",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_class_id", sa.Integer(), nullable=False),
        sa.Column("corridor_pct", sa.Numeric(5, 2), nullable=False),
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
```

- [ ] **Step 2: Run migration**

```bash
just db-migrate
```

Expected: migration applies cleanly; old `compensation_corridors` data is dropped, new table created with constraints and partial unique indexes.

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(db): corridor fallback migration — DROP old composite PK, CREATE new with surrogate id + type/class hierarchy"
```

---

## Task 2: Model + CRUD (resolve_corridor)

**Files:**
- Modify: `backend/models.py:218-238`
- Rewrite: `backend/crud/compensation_corridors.py`
- Modify: `backend/tests/factories.py:154-160`

- [ ] **Step 1: Update CompensationCorridor model**

Replace `backend/models.py` lines 218-238 (the entire `CompensationCorridor` class) with:

```python
class CompensationCorridor(Base):
    """Corridor rule for a project: type-level default or class-level override.

    Exactly one of material_type / material_class_id is set (CHECK constraint).
    is_compensable=true requires corridor_pct (CHECK constraint).
    Whitelist default: no row → not compensable.
    """
    __tablename__ = "compensation_corridors"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    material_type = Column(String, nullable=True)
    material_class_id = Column(
        Integer, ForeignKey("material_classes.id", ondelete="CASCADE"), nullable=True
    )
    is_compensable = Column(Boolean, nullable=False, default=False)
    corridor_pct = Column(Numeric(5, 2), nullable=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
    updated_at = Column(
        DateTime,
        server_default=sa_text("(now() AT TIME ZONE 'utc')"),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    __table_args__ = (
        CheckConstraint(
            "(material_type IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive",
        ),
        CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable",
        ),
        Index(
            "uq_corridor_project_type", "project_id", "material_type",
            unique=True, postgresql_where=text("material_class_id IS NULL"),
        ),
        Index(
            "uq_corridor_project_class", "project_id", "material_class_id",
            unique=True, postgresql_where=text("material_type IS NULL"),
        ),
    )
```

Add imports at top of `models.py` if not already present: `Boolean` (already there), `CheckConstraint`, `Index`, `text`:

```python
from sqlalchemy import CheckConstraint, Index, text
```

(`Boolean`, `Column`, `Integer`, `String`, `Numeric`, `DateTime`, `ForeignKey` should already be imported.)

- [ ] **Step 2: Rewrite `crud/compensation_corridors.py`**

Replace the entire file:

```python
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CompensationCorridor, MaterialClass


def get_corridor_rows(db: Session, project_id: int) -> list[CompensationCorridor]:
    """All corridor rows for project (type-level + class-level)."""
    return (
        db.query(CompensationCorridor)
        .filter(CompensationCorridor.project_id == project_id)
        .all()
    )


def get_corridor_map(
    db: Session, project_id: int,
) -> tuple[dict[int, CompensationCorridor], dict[str, CompensationCorridor]]:
    """Single query → (by_class, by_type) dicts for Python-side resolution."""
    rows = get_corridor_rows(db, project_id)
    by_class = {r.material_class_id: r for r in rows if r.material_class_id is not None}
    by_type = {r.material_type: r for r in rows if r.material_type is not None}
    return by_class, by_type


def resolve_corridor(
    by_class: dict[int, CompensationCorridor],
    by_type: dict[str, CompensationCorridor],
    class_id: int,
    material_type: str,
) -> tuple[bool | None, Decimal | None]:
    """Resolve corridor for a material class: class → type → None (not compensable).

    Returns (compensable, corridor_pct):
      (None, None)  — no row, default = not compensable
      (False, None) — explicitly disabled
      (True, pct)   — enabled, pct guaranteed by DB constraint
    """
    row = by_class.get(class_id) or by_type.get(material_type)
    if row is None:
        return None, None
    if not row.is_compensable:
        return False, None
    return True, row.corridor_pct


def set_type_corridor(
    db: Session, project_id: int, material_type: str,
    is_compensable: bool, corridor_pct: Decimal | None,
) -> None:
    """Upsert type-level corridor rule. Race-safe via partial unique index."""
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type=material_type,
        material_class_id=None,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_type"],
        index_where=text("material_class_id IS NULL"),
        set_={
            "is_compensable": stmt.excluded.is_compensable,
            "corridor_pct": stmt.excluded.corridor_pct,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def set_class_corridor(
    db: Session, project_id: int, material_class_id: int,
    is_compensable: bool, corridor_pct: Decimal | None,
) -> None:
    """Upsert class-level corridor override. Race-safe via partial unique index."""
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type=None,
        material_class_id=material_class_id,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_class_id"],
        index_where=text("material_type IS NULL"),
        set_={
            "is_compensable": stmt.excluded.is_compensable,
            "corridor_pct": stmt.excluded.corridor_pct,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def delete_type_corridor(db: Session, project_id: int, material_type: str) -> bool:
    """Remove type-level rule. Returns True if row existed."""
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_type == material_type,
            CompensationCorridor.material_class_id.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def delete_class_corridor(db: Session, project_id: int, material_class_id: int) -> bool:
    """Remove class-level override. Returns True if row existed."""
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_class_id == material_class_id,
            CompensationCorridor.material_type.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def build_resolved_matrix(db: Session, project_id: int) -> dict:
    """Build the full resolved matrix for the corridors GET endpoint.

    Returns {types: [...], classes: [...]} where each class has its resolved
    is_compensable, corridor_pct, level ("type"/"class"/"default"), has_override.
    """
    by_class, by_type = get_corridor_map(db, project_id)
    all_classes = db.query(MaterialClass).order_by(MaterialClass.material_type, MaterialClass.name).all()

    # Distinct types from material classes
    all_types = sorted({mc.material_type for mc in all_classes})

    types_out = []
    for mt in all_types:
        rule = by_type.get(mt)
        if rule:
            types_out.append({
                "material_type": mt,
                "is_compensable": rule.is_compensable,
                "corridor_pct": rule.corridor_pct,
                "has_rule": True,
            })
        else:
            types_out.append({
                "material_type": mt,
                "is_compensable": None,
                "corridor_pct": None,
                "has_rule": False,
            })

    classes_out = []
    for mc in all_classes:
        has_override = mc.id in by_class
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type)
        if has_override:
            level = "class"
        elif mc.material_type in by_type:
            level = "type"
        else:
            level = "default"
        classes_out.append({
            "material_class_id": mc.id,
            "material_class_name": mc.name,
            "material_type": mc.material_type,
            "is_compensable": compensable if compensable is not None else False,
            "corridor_pct": pct,
            "level": level,
            "has_override": has_override,
        })

    return {"types": types_out, "classes": classes_out}
```

- [ ] **Step 3: Update factory**

In `backend/tests/factories.py`, replace the `CompensationCorridorFactory` (lines 154-160):

```python
class CompensationCorridorFactory(_BaseFactory):
    class Meta:
        model = CompensationCorridor

    project_id = factory.LazyAttribute(lambda _: ProjectFactory.create().id)
    material_type = None
    material_class_id = factory.LazyAttribute(lambda _: MaterialClassFactory.create().id)
    is_compensable = True
    corridor_pct = Decimal("5.00")
```

Add `from decimal import Decimal` at the top of factories.py if not already imported.

- [ ] **Step 4: Verify model loads**

```bash
just test-backend-unit -k "test_security" --no-header -q 2>&1 | tail -3
```

Expected: tests pass (model imports work, no syntax errors).

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/crud/compensation_corridors.py backend/tests/factories.py
git commit -m "feat: restructure CompensationCorridor model + CRUD with type/class fallback"
```

---

## Task 3: Unit Tests for resolve_corridor

**Files:**
- Create: `backend/tests/unit/test_resolve_corridor.py`
- Modify: `backend/tests/unit/test_compensation.py`

- [ ] **Step 1: Write unit tests for resolve_corridor**

Create `backend/tests/unit/test_resolve_corridor.py`:

```python
"""Unit tests for corridor fallback resolution — no DB required."""
from decimal import Decimal
from unittest.mock import SimpleNamespace

import pytest

from crud.compensation_corridors import resolve_corridor

D = Decimal


def _row(is_compensable: bool, corridor_pct: Decimal | None) -> SimpleNamespace:
    """Fake row mimicking CompensationCorridor attributes."""
    return SimpleNamespace(is_compensable=is_compensable, corridor_pct=corridor_pct)


class TestResolveCorridorFallback:
    """Class-level override → type-level fallback → default (not compensable)."""

    def test_no_rows_returns_none(self):
        compensable, pct = resolve_corridor({}, {}, 1, "concrete")
        assert compensable is None
        assert pct is None

    def test_type_level_compensable(self):
        by_type = {"concrete": _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, "concrete")
        assert compensable is True
        assert pct == D("5.00")

    def test_type_level_not_compensable(self):
        by_type = {"rebar": _row(False, None)}
        compensable, pct = resolve_corridor({}, by_type, 1, "rebar")
        assert compensable is False
        assert pct is None

    def test_class_override_wins_over_type(self):
        by_type = {"concrete": _row(True, D("5.00"))}
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 42, "concrete")
        assert compensable is True
        assert pct == D("7.00")

    def test_class_override_can_disable_over_type_enabled(self):
        by_type = {"concrete": _row(True, D("5.00"))}
        by_class = {42: _row(False, None)}
        compensable, pct = resolve_corridor(by_class, by_type, 42, "concrete")
        assert compensable is False
        assert pct is None

    def test_class_override_can_enable_over_type_disabled(self):
        by_type = {"rebar": _row(False, None)}
        by_class = {55: _row(True, D("3.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 55, "rebar")
        assert compensable is True
        assert pct == D("3.00")

    def test_unrelated_type_not_matched(self):
        by_type = {"concrete": _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, "rebar")
        assert compensable is None
        assert pct is None

    def test_unrelated_class_not_matched(self):
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, {}, 99, "concrete")
        assert compensable is None
        assert pct is None
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
just test-backend-unit -k "test_resolve_corridor" -v
```

Expected: all 8 tests PASS.

- [ ] **Step 3: Add test for is_compensable=false in test_compensation.py**

Append to `backend/tests/unit/test_compensation.py`:

```python
def test_compensation_none_when_not_compensable():
    """resolve_corridor returns (False, None) → caller skips compute_compensation_per_unit.
    But if someone passes corridor_pct=None to the function, it returns None."""
    assert compute_compensation_per_unit(D("110"), D("100"), None) is None
```

- [ ] **Step 4: Run full unit tests**

```bash
just test-backend-unit -v
```

Expected: all unit tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/unit/test_resolve_corridor.py backend/tests/unit/test_compensation.py
git commit -m "test: unit tests for corridor fallback resolution"
```

---

## Task 4: Pydantic Schema + Router Endpoints

**Files:**
- Modify: `backend/routers/projects.py:1-27,115-161`

- [ ] **Step 1: Update imports and Pydantic schema**

In `backend/routers/projects.py`, replace the imports (line 7) and `CorridorUpsert` (lines 25-26):

Old import line 7:
```python
from crud.compensation_corridors import delete_corridor, get_corridors, set_corridor
```

New:
```python
from crud.compensation_corridors import (
    build_resolved_matrix,
    delete_class_corridor,
    delete_type_corridor,
    set_class_corridor,
    set_type_corridor,
)
```

Replace `CorridorUpsert` (line 25-26):

```python
class CorridorUpsert(BaseModel):
    is_compensable: bool
    corridor_pct: Decimal | None = None

    @model_validator(mode="after")
    def validate_pct_logic(self) -> "CorridorUpsert":
        if self.is_compensable and self.corridor_pct is None:
            raise ValueError("corridor_pct обязателен, если is_compensable=True")
        if not self.is_compensable:
            self.corridor_pct = None
        if self.corridor_pct is not None and not (0 <= self.corridor_pct <= 100):
            raise ValueError("corridor_pct должен быть от 0 до 100")
        return self
```

Add `model_validator` import:
```python
from pydantic import BaseModel, Field, model_validator
```

- [ ] **Step 2: Replace corridor endpoints**

Delete old endpoints (lines 115-161: `list_compensation_corridors`, `upsert_compensation_corridor`, `delete_compensation_corridor`) and replace with:

```python
# --- Corridors (fallback hierarchy) ---


@router.get("/{project_id}/corridors")
def list_corridors(project_id: int, db: Session = Depends(get_db)):
    """Resolved corridor matrix: all material types + classes with resolved status."""
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    return build_resolved_matrix(db, project_id)


@router.put("/{project_id}/corridors/type/{material_type}")
def upsert_type_corridor(
    project_id: int,
    material_type: str,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    set_type_corridor(db, project_id, material_type, data.is_compensable, data.corridor_pct)
    return {"material_type": material_type, "is_compensable": data.is_compensable, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/corridors/type/{material_type}", status_code=204)
def remove_type_corridor(
    project_id: int,
    material_type: str,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    delete_type_corridor(db, project_id, material_type)
    return Response(status_code=204)


@router.put("/{project_id}/corridors/class/{material_class_id}")
def upsert_class_corridor(
    project_id: int,
    material_class_id: int,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    if not db.query(MaterialClass).filter(MaterialClass.id == material_class_id).first():
        raise HTTPException(status_code=404, detail="Класс материала не найден")
    set_class_corridor(db, project_id, material_class_id, data.is_compensable, data.corridor_pct)
    return {"material_class_id": material_class_id, "is_compensable": data.is_compensable, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/corridors/class/{material_class_id}", status_code=204)
def remove_class_corridor(
    project_id: int,
    material_class_id: int,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    delete_class_corridor(db, project_id, material_class_id)
    return Response(status_code=204)
```

- [ ] **Step 3: Verify auth coverage test still passes**

```bash
just test-backend-unit -k "test_auth_coverage" -v
```

Expected: new routes are covered (they inherit `get_current_user` from the router). If the test lists new uncovered routes, add them.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/projects.py
git commit -m "feat(api): corridor endpoints with type/class hierarchy + CorridorUpsert validator"
```

---

## Task 5: Integrate resolve_corridor into compute_calculations

**Files:**
- Modify: `backend/crud/calculations.py:4,7,126-129,264-267,307-313`

- [ ] **Step 1: Update corridor loading**

In `backend/crud/calculations.py`, replace the corridor loading block (lines 126-129):

Old:
```python
    from crud.compensation_corridors import get_corridor_map  # noqa: PLC0415
    corridor_by_class: dict[int, float] = get_corridor_map(db, project_id)
```

New:
```python
    from crud.compensation_corridors import get_corridor_map, resolve_corridor  # noqa: PLC0415
    corridor_by_class, corridor_by_type = get_corridor_map(db, project_id)
```

- [ ] **Step 2: Expand class_name_map to include material_type**

Replace the `class_name_map` declaration (line 124) and the lazy-load block (lines 264-267):

Line 124, old:
```python
    class_name_map: dict[int, str] = {}
```

New:
```python
    class_name_map: dict[int, str] = {}
    class_type_map: dict[int, str] = {}
```

Lines 264-267, old:
```python
        missing_ids = [cid for cid in class_ids if cid not in class_name_map]
        if missing_ids:
            for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(missing_ids)).all():
                class_name_map[mc.id] = mc.name
```

New:
```python
        missing_ids = [cid for cid in class_ids if cid not in class_name_map]
        if missing_ids:
            for mc in db.query(MaterialClass).filter(MaterialClass.id.in_(missing_ids)).all():
                class_name_map[mc.id] = mc.name
                class_type_map[mc.id] = mc.material_type
```

- [ ] **Step 3: Replace corridor resolution in the calculation loop**

Replace lines 307-313:

Old:
```python
            corridor_pct = corridor_by_class.get(cid)
            compensation_per_unit = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
            compensation_amount = (
                money_round(compensation_per_unit * qty, 2)
                if compensation_per_unit is not None
                else None
            )
```

New:
```python
            compensable, corridor_pct = resolve_corridor(
                corridor_by_class, corridor_by_type, cid, class_type_map.get(cid, ""),
            )
            if not compensable:
                compensation_per_unit = None
                compensation_amount = None
            else:
                compensation_per_unit = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
                compensation_amount = (
                    money_round(compensation_per_unit * qty, 2)
                    if compensation_per_unit is not None
                    else None
                )
```

- [ ] **Step 4: Run unit tests**

```bash
just test-backend-unit -v
```

Expected: all PASS (resolve_corridor tests use mocks, not DB).

- [ ] **Step 5: Commit**

```bash
git add backend/crud/calculations.py
git commit -m "feat: integrate corridor fallback resolution into compute_calculations"
```

---

## Task 6: Integration Tests

**Files:**
- Rewrite: `backend/tests/integration/test_compensation_corridors.py`

- [ ] **Step 1: Rewrite integration tests**

Replace the entire file `backend/tests/integration/test_compensation_corridors.py`:

```python
"""Integration tests for corridor fallback hierarchy (Spec 2)."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from crud.calculations import compute_calculations
from crud.compensation_corridors import (
    delete_class_corridor,
    delete_type_corridor,
    get_corridor_map,
    resolve_corridor,
    set_class_corridor,
    set_type_corridor,
)
from tests.factories import (
    DocumentFactory,
    InvoiceFactory,
    InvoiceItemFactory,
    MaterialClassFactory,
    ProjectFactory,
    ReferencePriceFactory,
)

D = Decimal


# --- CRUD tests ---

class TestTypeCorridorCrud:
    def test_set_type_creates_row(self, db_session, factories):
        project = ProjectFactory.create()
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        assert "concrete" in by_type
        assert by_type["concrete"].corridor_pct == D("5.00")
        assert by_type["concrete"].is_compensable is True

    def test_set_type_upsert_overwrites(self, db_session, factories):
        project = ProjectFactory.create()
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        set_type_corridor(db_session, project.id, "concrete", True, D("7.00"))
        _, by_type = get_corridor_map(db_session, project.id)
        assert by_type["concrete"].corridor_pct == D("7.00")

    def test_set_type_not_compensable(self, db_session, factories):
        project = ProjectFactory.create()
        set_type_corridor(db_session, project.id, "rebar", False, None)
        _, by_type = get_corridor_map(db_session, project.id)
        assert by_type["rebar"].is_compensable is False
        assert by_type["rebar"].corridor_pct is None

    def test_delete_type_idempotent(self, db_session, factories):
        project = ProjectFactory.create()
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        assert delete_type_corridor(db_session, project.id, "concrete") is True
        assert delete_type_corridor(db_session, project.id, "concrete") is False


class TestClassCorridorCrud:
    def test_set_class_creates_row(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В25")
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        by_class, _ = get_corridor_map(db_session, project.id)
        assert mc.id in by_class
        assert by_class[mc.id].corridor_pct == D("7.00")

    def test_delete_class_idempotent(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В25")
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        assert delete_class_corridor(db_session, project.id, mc.id) is True
        assert delete_class_corridor(db_session, project.id, mc.id) is False


# --- Fallback resolution with real DB ---

class TestFallbackResolution:
    def test_class_override_wins(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В40")
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, "concrete")
        assert compensable is True
        assert pct == D("7.00")

    def test_class_disables_over_type_enabled(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В50")
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, False, None)
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, _ = resolve_corridor(by_class, by_type, mc.id, "concrete")
        assert compensable is False

    def test_class_enables_over_type_disabled(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="rebar", name="d12")
        set_type_corridor(db_session, project.id, "rebar", False, None)
        set_class_corridor(db_session, project.id, mc.id, True, D("3.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, "rebar")
        assert compensable is True
        assert pct == D("3.00")

    def test_no_rows_means_not_compensable(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="other", name="Песок")
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, "other")
        assert compensable is None
        assert pct is None


# --- Calculation integration ---

def _make_invoice_with_item(db_session, project, mc, *, qty, unit_price, inv_date):
    doc = DocumentFactory.create(project=project)
    inv = InvoiceFactory.create(document=doc, date=inv_date, vat_rate=D("0"))
    InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=qty, unit_price=unit_price, amount=qty * unit_price, vat_amount=D("0"),
    )
    return inv


class TestCalculationIntegration:
    def test_type_level_corridor_applies_to_class(self, db_session, factories):
        """Type-level corridor 5% → class inherits → compensation calculated."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В25")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] == D("5.00")
        assert rows[0]["compensation_amount"] == D("10.00")

    def test_not_compensable_returns_none(self, db_session, factories):
        """No corridor row → class not compensable → compensation fields are None."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В25")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] is None
        assert rows[0]["compensation_amount"] is None
        assert rows[0]["corridor_pct"] is None

    def test_class_override_disables_compensation(self, db_session, factories):
        """Type enabled, class override disables → no compensation for that class."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В40")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        set_type_corridor(db_session, project.id, "concrete", True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, False, None)
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] is None


# --- API endpoint tests ---

class TestCorridorApi:
    def test_get_corridors_empty(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.get(f"/api/projects/{project.id}/corridors")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert "classes" in data

    def test_put_type_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        assert resp.status_code == 200
        assert resp.json()["is_compensable"] is True

    def test_put_type_not_compensable(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/rebar",
            json={"is_compensable": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_compensable"] is False

    def test_put_compensable_without_pct_returns_422(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True},
        )
        assert resp.status_code == 422

    def test_delete_type_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        resp = client.delete(f"/api/projects/{project.id}/corridors/type/concrete")
        assert resp.status_code == 204

    def test_put_class_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В40")
        resp = client.put(
            f"/api/projects/{project.id}/corridors/class/{mc.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        assert resp.status_code == 200

    def test_delete_class_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type="concrete", name="В40")
        client.put(
            f"/api/projects/{project.id}/corridors/class/{mc.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        resp = client.delete(f"/api/projects/{project.id}/corridors/class/{mc.id}")
        assert resp.status_code == 204

    def test_resolved_matrix_shows_inheritance(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc1 = MaterialClassFactory.create(material_type="concrete", name="В25")
        mc2 = MaterialClassFactory.create(material_type="concrete", name="В40")
        client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        client.put(
            f"/api/projects/{project.id}/corridors/class/{mc2.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        resp = client.get(f"/api/projects/{project.id}/corridors")
        data = resp.json()
        classes = {c["material_class_id"]: c for c in data["classes"]}
        # В25 inherits from type
        assert classes[mc1.id]["level"] == "type"
        assert classes[mc1.id]["corridor_pct"] == 5.0
        assert classes[mc1.id]["has_override"] is False
        # В40 has own override
        assert classes[mc2.id]["level"] == "class"
        assert classes[mc2.id]["corridor_pct"] == 7.0
        assert classes[mc2.id]["has_override"] is True
```

- [ ] **Step 2: Run integration tests**

```bash
just test-backend-integration -k "test_compensation_corridors" -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run full backend test suite**

```bash
just test-backend
```

Expected: all PASS. If old tests reference removed endpoints/functions, fix import errors.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_compensation_corridors.py
git commit -m "test: integration tests for corridor fallback hierarchy + API + calculation"
```

---

## Task 7: Frontend Types + API Client

**Files:**
- Rewrite: `frontend/src/types/compensationCorridor.ts`
- Rewrite: `frontend/src/services/api/compensationCorridors.ts`

- [ ] **Step 1: Update types**

Replace `frontend/src/types/compensationCorridor.ts`:

```typescript
export interface CorridorTypeRule {
  material_type: string;
  is_compensable: boolean | null; // null = not configured
  corridor_pct: number | null;
  has_rule: boolean;
}

export interface CorridorClassResolved {
  material_class_id: number;
  material_class_name: string;
  material_type: string;
  is_compensable: boolean;
  corridor_pct: number | null;
  level: "type" | "class" | "default";
  has_override: boolean;
}

export interface CorridorMatrix {
  types: CorridorTypeRule[];
  classes: CorridorClassResolved[];
}

export interface CorridorUpsertPayload {
  is_compensable: boolean;
  corridor_pct?: number | null;
}
```

- [ ] **Step 2: Update API client**

Replace `frontend/src/services/api/compensationCorridors.ts`:

```typescript
import api from "@/lib/api";
import type { ID } from "@/types/common";
import type { CorridorMatrix, CorridorUpsertPayload } from "@/types/compensationCorridor";

export const corridorsApi = {
  async getMatrix(projectId: ID): Promise<CorridorMatrix> {
    const { data } = await api.get<CorridorMatrix>(`/projects/${projectId}/corridors`);
    return data;
  },

  async setType(projectId: ID, materialType: string, payload: CorridorUpsertPayload): Promise<void> {
    await api.put(`/projects/${projectId}/corridors/type/${materialType}`, payload);
  },

  async deleteType(projectId: ID, materialType: string): Promise<void> {
    await api.delete(`/projects/${projectId}/corridors/type/${materialType}`);
  },

  async setClass(projectId: ID, materialClassId: ID, payload: CorridorUpsertPayload): Promise<void> {
    await api.put(`/projects/${projectId}/corridors/class/${materialClassId}`, payload);
  },

  async deleteClass(projectId: ID, materialClassId: ID): Promise<void> {
    await api.delete(`/projects/${projectId}/corridors/class/${materialClassId}`);
  },
};
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/compensationCorridor.ts frontend/src/services/api/compensationCorridors.ts
git commit -m "feat(frontend): corridor types + API client for fallback hierarchy"
```

---

## Task 8: Frontend Query Hooks + MSW Handlers

**Files:**
- Modify: `frontend/src/services/queryKeys.ts:44`
- Modify: `frontend/src/services/queries.ts:453-495`
- Modify: `frontend/src/test/handlers.ts:124-131`

- [ ] **Step 1: Update query key**

In `frontend/src/services/queryKeys.ts`, replace the `compensationCorridors` key (line 44):

```typescript
corridors: (projectId: ID) => ["corridors", projectId] as const,
```

- [ ] **Step 2: Replace query hooks**

In `frontend/src/services/queries.ts`, replace the three corridor hooks (lines ~453-495) with:

```typescript
// --- Corridors (fallback hierarchy) ---

export function useCorridors(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.corridors(projectId) : ["corridors-disabled"],
    queryFn: () => corridorsApi.getMatrix(projectId!),
    enabled: projectId !== null,
  });
}

export function useSetTypeCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ materialType, payload }: { materialType: string; payload: CorridorUpsertPayload }) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.setType(projectId, materialType, payload);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}

export function useDeleteTypeCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (materialType: string) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.deleteType(projectId, materialType);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}

export function useSetClassCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ materialClassId, payload }: { materialClassId: ID; payload: CorridorUpsertPayload }) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.setClass(projectId, materialClassId, payload);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}

export function useDeleteClassCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (materialClassId: ID) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.deleteClass(projectId, materialClassId);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
    },
  });
}
```

Update the import at the top of queries.ts — replace the old `compensationCorridorsApi` import with:

```typescript
import { corridorsApi } from "@/services/api/compensationCorridors";
import type { CorridorUpsertPayload } from "@/types/compensationCorridor";
```

- [ ] **Step 3: Update MSW handlers**

In `frontend/src/test/handlers.ts`, replace the corridor handlers (lines ~124-131):

```typescript
  // Corridors (fallback hierarchy)
  http.get("/api/projects/:projectId/corridors", () =>
    HttpResponse.json({ types: [], classes: [] }),
  ),
  http.put("/api/projects/:projectId/corridors/type/:materialType", () =>
    HttpResponse.json({ material_type: "concrete", is_compensable: true, corridor_pct: 5 }),
  ),
  http.delete("/api/projects/:projectId/corridors/type/:materialType", () =>
    new HttpResponse(null, { status: 204 }),
  ),
  http.put("/api/projects/:projectId/corridors/class/:materialClassId", () =>
    HttpResponse.json({ material_class_id: 1, is_compensable: true, corridor_pct: 7 }),
  ),
  http.delete("/api/projects/:projectId/corridors/class/:materialClassId", () =>
    new HttpResponse(null, { status: 204 }),
  ),
```

- [ ] **Step 4: Run typecheck**

```bash
just typecheck-frontend
```

Expected: no type errors. Fix any references to old `useCompensationCorridors` / `useSetCorridor` / `useDeleteCorridor` — they should only exist in `CorridorsTab.tsx` which we'll rewrite next.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/ frontend/src/test/handlers.ts
git commit -m "feat(frontend): corridor query hooks + MSW handlers for fallback hierarchy"
```

---

## Task 9: CorridorsTab Component Rewrite

**Files:**
- Rewrite: `frontend/src/components/projects/CorridorsTab.tsx`

- [ ] **Step 1: Rewrite the component**

Replace `frontend/src/components/projects/CorridorsTab.tsx`:

```tsx
import { useState } from "react";

import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ID } from "@/types/common";
import type { CorridorClassResolved, CorridorTypeRule } from "@/types/compensationCorridor";
import {
  useCorridors,
  useDeleteClassCorridor,
  useDeleteTypeCorridor,
  useSetClassCorridor,
  useSetTypeCorridor,
} from "@/services/queries";

interface Props {
  projectId: ID;
}

const TYPE_LABELS: Record<string, string> = {
  concrete: "Бетон",
  rebar: "Арматура",
  other: "Прочее",
};

export function CorridorsTab({ projectId }: Props) {
  const { data: matrix, isLoading } = useCorridors(projectId);
  const setType = useSetTypeCorridor(projectId);
  const deleteType = useDeleteTypeCorridor(projectId);
  const setClass = useSetClassCorridor(projectId);
  const deleteClass = useDeleteClassCorridor(projectId);

  const [editingClass, setEditingClass] = useState<ID | null>(null);
  const [editPct, setEditPct] = useState("");
  const [editCompensable, setEditCompensable] = useState(true);

  const [editingType, setEditingType] = useState<string | null>(null);
  const [typePct, setTypePct] = useState("");

  if (isLoading || !matrix) {
    return <div className="p-4 text-fg-muted">Загрузка…</div>;
  }

  const typeMap = new Map(matrix.types.map((t) => [t.material_type, t]));
  const grouped = new Map<string, CorridorClassResolved[]>();
  for (const cls of matrix.classes) {
    const list = grouped.get(cls.material_type) ?? [];
    list.push(cls);
    grouped.set(cls.material_type, list);
  }

  const allTypes = [...new Set([...typeMap.keys(), ...grouped.keys()])].sort();

  function handleTypeToggle(mt: string, rule: CorridorTypeRule | undefined) {
    if (rule?.has_rule) {
      deleteType.mutate(mt);
    } else {
      setEditingType(mt);
      setTypePct("5");
    }
  }

  function handleTypeSave(mt: string) {
    const pct = parseFloat(typePct);
    if (isNaN(pct) || pct < 0 || pct > 100) return;
    setType.mutate({ materialType: mt, payload: { is_compensable: true, corridor_pct: pct } });
    setEditingType(null);
  }

  function handleTypeDisable(mt: string) {
    setType.mutate({ materialType: mt, payload: { is_compensable: false } });
    setEditingType(null);
  }

  function startClassEdit(cls: CorridorClassResolved) {
    setEditingClass(cls.material_class_id);
    setEditPct(cls.corridor_pct?.toString() ?? "5");
    setEditCompensable(cls.is_compensable);
  }

  function handleClassSave(classId: ID) {
    if (editCompensable) {
      const pct = parseFloat(editPct);
      if (isNaN(pct) || pct < 0 || pct > 100) return;
      setClass.mutate({ materialClassId: classId, payload: { is_compensable: true, corridor_pct: pct } });
    } else {
      setClass.mutate({ materialClassId: classId, payload: { is_compensable: false } });
    }
    setEditingClass(null);
  }

  return (
    <Surface padding="none" className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[200px]">Материал</TableHead>
            <TableHead className="w-[120px]">Статус</TableHead>
            <TableHead className="w-[100px]">Коридор, %</TableHead>
            <TableHead className="w-[120px]">Источник</TableHead>
            <TableHead className="w-[140px]">Действия</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {allTypes.map((mt) => {
            const rule = typeMap.get(mt);
            const classes = grouped.get(mt) ?? [];
            return (
              <TypeGroup
                key={mt}
                materialType={mt}
                rule={rule}
                classes={classes}
                editingType={editingType}
                typePct={typePct}
                onTypePctChange={setTypePct}
                onTypeToggle={handleTypeToggle}
                onTypeSave={handleTypeSave}
                onTypeDisable={handleTypeDisable}
                onTypeEditCancel={() => setEditingType(null)}
                editingClass={editingClass}
                editPct={editPct}
                editCompensable={editCompensable}
                onEditPctChange={setEditPct}
                onEditCompensableChange={setEditCompensable}
                onClassEdit={startClassEdit}
                onClassSave={handleClassSave}
                onClassEditCancel={() => setEditingClass(null)}
                onClassDelete={(id) => deleteClass.mutate(id)}
              />
            );
          })}
        </TableBody>
      </Table>
    </Surface>
  );
}

interface TypeGroupProps {
  materialType: string;
  rule: CorridorTypeRule | undefined;
  classes: CorridorClassResolved[];
  editingType: string | null;
  typePct: string;
  onTypePctChange: (v: string) => void;
  onTypeToggle: (mt: string, rule: CorridorTypeRule | undefined) => void;
  onTypeSave: (mt: string) => void;
  onTypeDisable: (mt: string) => void;
  onTypeEditCancel: () => void;
  editingClass: ID | null;
  editPct: string;
  editCompensable: boolean;
  onEditPctChange: (v: string) => void;
  onEditCompensableChange: (v: boolean) => void;
  onClassEdit: (cls: CorridorClassResolved) => void;
  onClassSave: (id: ID) => void;
  onClassEditCancel: () => void;
  onClassDelete: (id: ID) => void;
}

function TypeGroup({
  materialType,
  rule,
  classes,
  editingType,
  typePct,
  onTypePctChange,
  onTypeToggle,
  onTypeSave,
  onTypeDisable,
  onTypeEditCancel,
  editingClass,
  editPct,
  editCompensable,
  onEditPctChange,
  onEditCompensableChange,
  onClassEdit,
  onClassSave,
  onClassEditCancel,
  onClassDelete,
}: TypeGroupProps) {
  const label = TYPE_LABELS[materialType] ?? materialType;
  const isEditing = editingType === materialType;

  return (
    <>
      {/* Type header row */}
      <TableRow className="bg-surface-sunken font-medium">
        <TableCell>{label}</TableCell>
        <TableCell>
          {rule?.has_rule ? (
            rule.is_compensable ? (
              <span className="text-green-600">Вкл.</span>
            ) : (
              <span className="text-red-500">Выкл.</span>
            )
          ) : (
            <span className="text-fg-muted">Не настроено</span>
          )}
        </TableCell>
        <TableCell>
          {isEditing ? (
            <Input
              className="w-20 h-7"
              value={typePct}
              onChange={(e) => onTypePctChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onTypeSave(materialType);
                if (e.key === "Escape") onTypeEditCancel();
              }}
              autoFocus
            />
          ) : rule?.corridor_pct != null ? (
            `${rule.corridor_pct}%`
          ) : (
            "—"
          )}
        </TableCell>
        <TableCell />
        <TableCell className="flex gap-2">
          {isEditing ? (
            <>
              <Button size="sm" variant="default" onClick={() => onTypeSave(materialType)}>
                Сохранить
              </Button>
              <Button size="sm" variant="outline" onClick={() => onTypeDisable(materialType)}>
                Выкл.
              </Button>
              <Button size="sm" variant="ghost" onClick={onTypeEditCancel}>
                Отмена
              </Button>
            </>
          ) : rule?.has_rule ? (
            <Button size="sm" variant="ghost" onClick={() => onTypeToggle(materialType, rule)}>
              Снять
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => onTypeToggle(materialType, rule)}>
              Настроить
            </Button>
          )}
        </TableCell>
      </TableRow>

      {/* Class rows */}
      {classes.map((cls) => {
        const isClassEditing = editingClass === cls.material_class_id;
        return (
          <TableRow key={cls.material_class_id}>
            <TableCell className="pl-8">{cls.material_class_name}</TableCell>
            <TableCell>
              {isClassEditing ? (
                <Switch checked={editCompensable} onCheckedChange={onEditCompensableChange} />
              ) : cls.is_compensable ? (
                <span className="text-green-600">✓</span>
              ) : cls.level === "default" ? (
                <span className="text-fg-muted">—</span>
              ) : (
                <span className="text-red-500">✗</span>
              )}
            </TableCell>
            <TableCell>
              {isClassEditing && editCompensable ? (
                <Input
                  className="w-20 h-7"
                  value={editPct}
                  onChange={(e) => onEditPctChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onClassSave(cls.material_class_id);
                    if (e.key === "Escape") onClassEditCancel();
                  }}
                  autoFocus
                />
              ) : cls.corridor_pct != null ? (
                `${cls.corridor_pct}%`
              ) : (
                "—"
              )}
            </TableCell>
            <TableCell>
              {cls.has_override ? (
                <span className="text-xs font-medium text-accent">[своё]</span>
              ) : cls.level === "type" ? (
                <span className="text-xs text-fg-muted">(наследовано)</span>
              ) : null}
            </TableCell>
            <TableCell className="flex gap-2">
              {isClassEditing ? (
                <>
                  <Button size="sm" variant="default" onClick={() => onClassSave(cls.material_class_id)}>
                    Сохранить
                  </Button>
                  <Button size="sm" variant="ghost" onClick={onClassEditCancel}>
                    Отмена
                  </Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="ghost" onClick={() => onClassEdit(cls)}>
                    Изменить
                  </Button>
                  {cls.has_override && (
                    <Button size="sm" variant="ghost" onClick={() => onClassDelete(cls.material_class_id)}>
                      ×
                    </Button>
                  )}
                </>
              )}
            </TableCell>
          </TableRow>
        );
      })}
    </>
  );
}
```

- [ ] **Step 2: Fix any remaining imports in other files**

Search for old hook names:

```bash
cd /c/Users/zhukov_v/Projects/UDP && grep -r "useCompensationCorridors\|useSetCorridor\|useDeleteCorridor" frontend/src/ --include="*.ts" --include="*.tsx" -l
```

Update any files that import the old hooks.

- [ ] **Step 3: Run typecheck + frontend tests**

```bash
just typecheck-frontend && just test-frontend
```

Expected: no type errors, all tests pass. Some existing CorridorsTab tests may need updating if they exist.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/projects/CorridorsTab.tsx
git commit -m "feat(frontend): CorridorsTab rewrite with grouped type/class fallback UI"
```

---

## Task 10: CLAUDE.md Update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md corridor documentation**

Replace the "Коридор компенсации" section with updated semantics reflecting the new model: surrogate PK, `material_type` XOR `material_class_id`, `is_compensable` per-project, whitelist default (no row = not compensable), class→type→no-row fallback, resolved matrix endpoint. Update the API path from `/compensation-corridors` to `/corridors`. Update `CompensationCorridor` field list in the models section.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for corridor fallback hierarchy (Spec 2)"
```

---

## Task 11: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
just test
```

Expected: all backend + frontend tests PASS.

- [ ] **Step 2: Run lint**

```bash
just lint
```

Expected: clean.

- [ ] **Step 3: Manual smoke test**

Start backend + frontend:

```bash
just dev-backend &
just dev-frontend &
```

1. Open a project → "Коридоры" tab
2. Set type-level corridor for "Бетон" at 5%
3. Verify all concrete classes show "наследовано" with 5%
4. Override one class (В40) to 7% → verify "[своё]" badge
5. Delete the override → verify B40 falls back to 5%
6. Disable "Арматура" type → verify all rebar classes show "Выкл."
7. Override d12 to enabled at 3% → verify d12 shows "[своё]" enabled
8. Check calculations tab → verify compensation only for configured classes

- [ ] **Step 4: Final commit if any fixes needed**

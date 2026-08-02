# Units Refactoring — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form `material_type` (String) and `unit` (String) with normalized reference tables (`units_of_measure`, `unit_aliases`, `material_types`), normalize invoice-item units at write-time, and make the calculator unit/dimension-aware.

**Architecture:** New reference tables seeded via Alembic. `InvoiceItem` keeps the raw unit (`raw_unit`) plus write-time-computed `normalized_unit_id/quantity/unit_price`. `MaterialClass`, `ReferencePrice`, `CompensationCorridor` gain FKs to the reference tables. The calculator (`crud/calculations.py`) aggregates on normalized quantities, guards against dimension mismatches, and distributes delivery cost by quantity within a single dimension or by amount across mixed dimensions. The corridor resolver switches its type-level key from the `material_type` string to `material_type_id`; the corridor HTTP API stays string-based (code→id mapped at the router).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, PostgreSQL (Neon), pytest + factory_boy + respx. Run everything through `just`.

**Dependency:** Applies on top of migration head `d0e1f2a3b4c5` (corridor-fallback). New migration's `down_revision = "d0e1f2a3b4c5"`.

**Spec:** `docs/superpowers/specs/2026-06-08-units-refactoring-design.md` (R4).

**Scope note:** This plan is backend-only. The frontend (ref-price unit dropdown, `raw_unit` rename, unknown-unit warning, `useUnits`/`useMaterialTypes`) is a separate plan: `2026-06-08-units-refactoring-frontend.md`.

**Deployment ordering / contract compatibility (no break window):** The invoice payload renames `unit`→`raw_unit`. To avoid breaking the not-yet-updated frontend during the gap between backend and frontend deploys, this plan makes the rename **backward-compatible on both directions** (Task B3):
- **Output:** `_serialize_document` and the dashboard invoice serializer emit BOTH `raw_unit` (new) and a legacy `unit` (same value). The old frontend keeps reading `unit`; the new frontend reads `raw_unit`.
- **Input:** `InvoiceItemEdit.raw_unit` accepts either JSON key via `validation_alias=AliasChoices("raw_unit", "unit")`, so an old frontend POSTing `unit` still works.
- **Cleanup:** after the frontend plan ships, drop the legacy `unit` output key and the input alias (tracked in `docs/TECH_DEBT.md`, Task F2). Production is not yet live, so a coordinated deploy is also acceptable — but the dual-compat above means ordering is not load-bearing.

---

## Shell / command conventions

All commands run through Git bash on Windows. Suite runs use `just`:

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1"
```

Single-file pytest runs:

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_unit_normalization.py -v 2>&1"
```

Integration tests need `TEST_DATABASE_URL` (loaded from repo-root `.env.test`). They run Alembic to `head` once per session, so the migration's seed data is present in the test DB.

---

## File Structure

**New files:**
- `backend/crud/units.py` — pure `normalize_unit_key`, seed-data constants, `load_alias_map`, `normalize_item`, `invariant_holds`. Single source of truth shared by runtime + migration + tests.
- `backend/routers/units.py` — read-only `GET /api/units`, `/api/units/{id}/aliases`, `/api/material-types`.
- `backend/alembic/versions/2026_06_08_1300-e1f2a3b4c5d6_units_refactoring.py` — schema + seed + backfill + tighten + down.
- `backend/tests/unit/test_unit_normalization.py`
- `backend/tests/unit/test_dimension_guard.py`
- `backend/tests/unit/test_delivery_distribution.py`
- `backend/tests/integration/test_units_api.py`
- `backend/tests/integration/test_normalization_integration.py`
- `backend/tests/integration/test_calculations_with_units.py`
- `backend/tests/integration/test_reference_prices_unit.py`

**Modified files:**
- `backend/models.py` — enums + 3 new models + column changes on 4 tables.
- `backend/crud/materials.py` — `get_or_create_material_class` + `get_material_classes` resolve `material_type` code↔id.
- `backend/crud/documents.py` — `create_invoice` normalizes units.
- `backend/crud/calculations.py` — normalized aggregation, dimension guard, delivery distribution, new output fields.
- `backend/crud/compensation_corridors.py` — type key string→`material_type_id`.
- `backend/routers/projects.py` — corridor endpoints map code→id.
- `backend/routers/invoices.py` — `raw_unit` rename, renormalization on edit, unknown-unit warnings, `_doc_has_issues`.
- `backend/routers/dashboard.py` — `_has_issues`, `raw_unit` in serializer, new calc fields passthrough.
- `backend/routers/reference_prices.py` — `unit_id` field + validations.
- `backend/routers/material_classes.py` — emit `material_type` code via join.
- `backend/main.py` — register units router.
- `backend/tests/factories.py` — new factories + `material_type_id`/`raw_unit`.
- `backend/tests/unit/test_resolve_corridor.py` — int type keys.
- `backend/tests/integration/test_compensation_corridors.py` — corridor CRUD signature changes (only if it calls CRUD directly).
- `docs/agent/database.md`, `docs/agent/calculations.md`, `docs/agent/pdf-parsing.md`, `docs/TECH_DEBT.md` — doc sync (Task F2). Project docs live in `docs/agent/*` (per `AGENTS.md`); do not edit `CLAUDE.md`.

---

# Milestone A — Schema, models, reference data

## Task A1: Pure unit utilities + seed data (`crud/units.py`)

**Files:**
- Create: `backend/crud/units.py`
- Test: `backend/tests/unit/test_unit_normalization.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_unit_normalization.py`:

```python
"""Unit tests for unit normalization — no DB required."""
from decimal import Decimal

import pytest

from crud.units import invariant_holds, normalize_unit_key


class TestNormalizeUnitKey:
    @pytest.mark.parametrize("raw,expected", [
        ("т", "т"),
        ("Т", "т"),
        (" Т ", "т"),
        ("тн", "тн"),
        ("м³", "м3"),          # NFKC: U+00B3 → "3"
        ("м3", "м3"),
        ("куб.м.", "куб.м"),   # trailing dot stripped
        ("кв  м", "кв м"),     # internal whitespace collapsed
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_unit_key(raw) == expected

    def test_m3_unicode_and_digit_collapse_to_same_key(self):
        assert normalize_unit_key("м³") == normalize_unit_key("м3")


class TestInvariantHolds:
    def test_exact(self):
        assert invariant_holds(Decimal("5"), Decimal("8000"), Decimal("40000")) is True

    def test_within_abs_tolerance_1_rub(self):
        # 5 * 8000 = 40000; amount off by exactly 1.00 → pass (tol = max(1, 0.1%))
        assert invariant_holds(Decimal("5"), Decimal("8000"), Decimal("40001")) is True

    def test_just_over_abs_tolerance(self):
        # Small amount so the absolute floor (1₽) dominates the relative tol:
        # expected 1*1 = 1; amount 2.01 → off by 1.01 > max(1, 0.1%·2.01) = 1 → fail
        assert invariant_holds(Decimal("1"), Decimal("1"), Decimal("2.01")) is False

    def test_within_rel_tolerance(self):
        # 1000 * 1 = 1000; 0.1% = 1.0; amount off by exactly 1.0 → pass
        assert invariant_holds(Decimal("1000"), Decimal("1"), Decimal("1001")) is True

    def test_just_over_rel_tolerance(self):
        # 1000 * 1 = 1000; 0.1% = 1.0; amount off by 1.01 → fail
        assert invariant_holds(Decimal("1000"), Decimal("1"), Decimal("1001.01")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_unit_normalization.py -v 2>&1"`
Expected: FAIL with `ModuleNotFoundError: No module named 'crud.units'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/crud/units.py`:

```python
"""Units of measure: normalization helpers and seed data.

Single source of truth used by runtime (create_invoice), the Alembic migration,
and tests. normalize_unit_key MUST be identical everywhere — see spec §3.1.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

# --- Pure normalization key -------------------------------------------------

def normalize_unit_key(raw: str | None) -> str:
    """Canonical lookup key for a raw unit string.

    NFKC folds м³ (U+00B3) → м3, NBSP → space; then collapse internal whitespace,
    lowercase, strip trailing dots ("куб.м." → "куб.м").
    """
    s = unicodedata.normalize("NFKC", raw or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(".")


# --- Invariant guard --------------------------------------------------------

def invariant_holds(
    quantity: Decimal,
    unit_price: Decimal,
    amount: Decimal,
    tol_abs: Decimal = Decimal("1"),
    tol_rel: Decimal = Decimal("0.001"),
) -> bool:
    """True if quantity*unit_price ≈ amount within max(1₽, 0.1%).

    Checks consistency of the source invoice row. The multiplier cancels in
    normalized values, so this is independent of normalization (spec §4.1).
    """
    if quantity is None or unit_price is None or amount is None:
        return False
    expected = quantity * unit_price
    tol = max(tol_abs, abs(amount) * tol_rel)
    return abs(expected - amount) <= tol


# --- Seed data (consumed by the migration and tests) ------------------------
# Base units first; derived units reference base by code. multiplier is a string
# (parsed to Decimal) to avoid float imprecision in the Numeric audit trail.

UNITS_SEED: list[dict] = [
    {"code": "TON", "name": "Тонна",      "symbol": "т",  "dimension": "mass",   "base_code": None,  "multiplier": "1"},
    {"code": "KG",  "name": "Килограмм",  "symbol": "кг", "dimension": "mass",   "base_code": "TON", "multiplier": "0.001"},
    {"code": "M3",  "name": "Куб. метр",  "symbol": "м³", "dimension": "volume", "base_code": None,  "multiplier": "1"},
    {"code": "L",   "name": "Литр",       "symbol": "л",  "dimension": "volume", "base_code": "M3",  "multiplier": "0.001"},
    {"code": "M",   "name": "Метр",       "symbol": "м",  "dimension": "length", "base_code": None,  "multiplier": "1"},
    {"code": "PCS", "name": "Штука",      "symbol": "шт", "dimension": "count",  "base_code": None,  "multiplier": "1"},
]

# normalized key → unit code. Keys are already normalize_unit_key()-ed
# (NFKC folds м³→м3, so only "м3" is listed).
ALIASES_SEED: dict[str, str] = {
    "т": "TON", "тн": "TON", "тонн": "TON", "тонна": "TON", "t": "TON", "ton": "TON",
    "кг": "KG", "kg": "KG",
    "м3": "M3", "m3": "M3", "куб": "M3", "куб.м": "M3", "куб м": "M3",
    "л": "L", "l": "L",
    "м": "M", "m": "M", "пог.м": "M", "п.м": "M",
    "шт": "PCS", "штук": "PCS", "pcs": "PCS",
}

MATERIAL_TYPES_SEED: list[dict] = [
    {"code": "concrete", "name": "Бетон",    "default_unit_code": "M3"},
    {"code": "rebar",    "name": "Арматура", "default_unit_code": "TON"},
    {"code": "other",    "name": "Прочее",   "default_unit_code": None},
]


# --- Runtime alias map + normalize_item -------------------------------------

@dataclass(frozen=True)
class AliasEntry:
    """Resolved alias: which canonical base unit + conversion to apply."""
    base_unit_id: int      # normalized_unit_id to store (base unit of the dimension)
    multiplier: Decimal    # to_base_multiplier of the matched (possibly derived) unit
    dimension: str
    base_symbol: str


@dataclass(frozen=True)
class NormalizationResult:
    normalized_unit_id: int
    normalized_quantity: Decimal
    normalized_unit_price: Decimal


def normalize_item(
    raw_unit: str | None,
    quantity: Decimal,
    unit_price: Decimal,
    aliases: dict[str, AliasEntry],
) -> NormalizationResult | None:
    """Normalize one invoice item. None if the unit is unknown (no alias).

    normalized_quantity = quantity * multiplier
    normalized_unit_price = unit_price / multiplier
    normalized_unit_id = base unit of the matched unit's dimension
    """
    entry = aliases.get(normalize_unit_key(raw_unit))
    if entry is None:
        return None
    return NormalizationResult(
        normalized_unit_id=entry.base_unit_id,
        normalized_quantity=quantity * entry.multiplier,
        normalized_unit_price=unit_price / entry.multiplier,
    )


def load_alias_map(db) -> dict[str, AliasEntry]:
    """Build {normalized raw_text → AliasEntry} from the seeded reference tables.

    Resolves each alias's unit to its base unit (or itself), capturing the
    conversion multiplier and the base unit's dimension/symbol.
    """
    from models import UnitAlias, UnitOfMeasure  # local import avoids cycle

    units = {u.id: u for u in db.query(UnitOfMeasure).all()}
    out: dict[str, AliasEntry] = {}
    for alias in db.query(UnitAlias).all():
        unit = units.get(alias.unit_id)
        if unit is None:
            continue
        base = units.get(unit.base_unit_id) if unit.base_unit_id else unit
        out[alias.raw_text] = AliasEntry(
            base_unit_id=base.id,
            multiplier=unit.to_base_multiplier,
            dimension=base.dimension,
            base_symbol=base.symbol,
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_unit_normalization.py -v 2>&1"`
Expected: PASS (all parametrized cases + invariant cases).

- [ ] **Step 5: Lint + commit**

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint-backend 2>&1"
git add backend/crud/units.py backend/tests/unit/test_unit_normalization.py
git commit -m "feat(units): pure normalize_unit_key, invariant guard, seed data"
```

---

## Task A2: ORM models — enums, reference tables, column changes

**Files:**
- Modify: `backend/models.py`

No new test in this task — the models are exercised by the migration smoke test (Task A4) and downstream tasks. This task makes the ORM match the post-migration schema.

- [ ] **Step 1: Add the two new enums**

In `backend/models.py`, after the `ProjectRole` enum (line ~44), add:

```python
class UnitDimension(str, enum.Enum):
    """Физическая размерность единицы измерения."""
    mass = "mass"
    volume = "volume"
    length = "length"
    count = "count"


class ItemType(str, enum.Enum):
    """Роль строки счёта в расчёте (ортогональна material_type)."""
    material = "material"
    delivery = "delivery"
    other = "other"
```

- [ ] **Step 2: Add the three reference-table models**

In `backend/models.py`, add these models just before `class MaterialClass` (line ~149):

```python
class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)   # TON, KG, M3, L, M, PCS
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    dimension = Column(
        SqlEnum(UnitDimension, name="ck_unit_dimension", native_enum=False),
        nullable=False,
    )
    base_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )
    to_base_multiplier = Column(Numeric(30, 15), nullable=False, server_default=sa_text("1"))

    base_unit = relationship("UnitOfMeasure", remote_side=[id])

    __table_args__ = (
        CheckConstraint(
            "(base_unit_id IS NOT NULL) OR (to_base_multiplier = 1)",
            name="ck_unit_base_multiplier",
        ),
    )


class UnitAlias(Base):
    __tablename__ = "unit_aliases"

    id = Column(Integer, primary_key=True)
    raw_text = Column(String, nullable=False, unique=True)  # normalize_unit_key() output
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="CASCADE"), nullable=False
    )

    unit = relationship("UnitOfMeasure")


class MaterialType(Base):
    __tablename__ = "material_types"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)   # concrete / rebar / other
    name = Column(String, nullable=False)
    default_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )

    default_unit = relationship("UnitOfMeasure")
```

- [ ] **Step 3: Change `MaterialClass`**

Replace the `material_type` column and relationships in `class MaterialClass`:

```python
class MaterialClass(Base):
    __tablename__ = "material_classes"

    id = Column(Integer, primary_key=True, index=True)
    material_type_id = Column(
        Integer, ForeignKey("material_types.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    name = Column(String, nullable=False)  # В15, В40
    calc_role = Column(String, nullable=False, default="base")  # base / additive / exclude
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    material_type = relationship("MaterialType")
    reference_prices = relationship("ReferencePrice", back_populates="material_class")
    invoice_items = relationship("InvoiceItem", back_populates="material_class")
```

- [ ] **Step 4: Change `ReferencePrice`**

Add the `unit_id` column + relationship to `class ReferencePrice` (after `price`):

```python
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=False
    )
```

and add (with the other relationships):

```python
    unit = relationship("UnitOfMeasure")
```

- [ ] **Step 5: Change `InvoiceItem`**

Replace the `unit` and `item_type` columns in `class InvoiceItem`:

```python
class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    raw_name = Column(String)
    item_type = Column(
        SqlEnum(ItemType, name="ck_item_type", native_enum=False), nullable=False
    )
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=True)
    quantity = Column(Numeric(15, 4), nullable=False)
    raw_unit = Column(String)
    normalized_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    normalized_quantity = Column(Numeric(20, 6), nullable=True)
    normalized_unit_price = Column(Numeric(24, 6), nullable=True)
    unit_price = Column(Numeric(19, 4), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    vat_amount = Column(Numeric(15, 2))

    invoice = relationship("Invoice", back_populates="items")
    material_class = relationship("MaterialClass", back_populates="invoice_items")
    normalized_unit = relationship("UnitOfMeasure")
```

- [ ] **Step 6: Change `CompensationCorridor` target column + constraints**

Replace the `material_type` column and `__table_args__` in `class CompensationCorridor`:

```python
    material_type_id = Column(
        Integer, ForeignKey("material_types.id", ondelete="RESTRICT"), nullable=True
    )
```

(remove the old `material_type = Column(String, nullable=True)` line), and replace `__table_args__`:

```python
    __table_args__ = (
        CheckConstraint(
            "(material_type_id IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type_id IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive",
        ),
        CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable",
        ),
        Index(
            "uq_corridor_project_type", "project_id", "material_type_id",
            unique=True, postgresql_where=sa_text("material_class_id IS NULL"),
        ),
        Index(
            "uq_corridor_project_class", "project_id", "material_class_id",
            unique=True, postgresql_where=sa_text("material_type_id IS NULL"),
        ),
    )
```

- [ ] **Step 7: Verify models import cleanly**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -c 'import models; print(\"ok\")' 2>&1"`
Expected: prints `ok` (no SQLAlchemy mapper errors).

- [ ] **Step 8: Commit**

```
git add backend/models.py
git commit -m "feat(units): ORM models for units/material_types + column changes"
```

---

## Task A3: Alembic migration (schema + seed + backfill + tighten + down)

**Files:**
- Create: `backend/alembic/versions/2026_06_08_1300-e1f2a3b4c5d6_units_refactoring.py`

This single migration does Steps 1–5 of spec §7. Seed/backfill data live in one revision so backfill always precedes the DROP COLUMNs.

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/2026_06_08_1300-e1f2a3b4c5d6_units_refactoring.py`:

```python
"""units refactoring: units_of_measure, unit_aliases, material_types + FK migration

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-08 13:00:00.000000
"""
from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# Reuse the single source of truth for seed data + normalization.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/
from crud.units import ALIASES_SEED, MATERIAL_TYPES_SEED, UNITS_SEED, normalize_unit_key  # noqa: E402

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KNOWN_MATERIAL_TYPES = {"concrete", "rebar", "other"}


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: new tables ──────────────────────────────────────────────
    op.create_table(
        "units_of_measure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=False),
        sa.Column("base_unit_id", sa.Integer(), nullable=True),
        sa.Column("to_base_multiplier", sa.Numeric(30, 15), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(["base_unit_id"], ["units_of_measure.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("code", name="uq_units_of_measure_code"),
        sa.CheckConstraint("dimension IN ('mass','volume','length','count')", name="ck_unit_dimension"),
        sa.CheckConstraint("(base_unit_id IS NOT NULL) OR (to_base_multiplier = 1)", name="ck_unit_base_multiplier"),
    )
    op.create_table(
        "unit_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["units_of_measure.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("raw_text", name="uq_unit_aliases_raw_text"),
    )
    op.create_table(
        "material_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("default_unit_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["default_unit_id"], ["units_of_measure.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("code", name="uq_material_types_code"),
    )

    op.add_column("material_classes", sa.Column("material_type_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_material_classes_material_type_id", "material_classes",
        "material_types", ["material_type_id"], ["id"], ondelete="RESTRICT",
    )

    op.alter_column("invoice_items", "unit", new_column_name="raw_unit")
    op.add_column("invoice_items", sa.Column("normalized_unit_id", sa.Integer(), nullable=True))
    op.add_column("invoice_items", sa.Column("normalized_quantity", sa.Numeric(20, 6), nullable=True))
    op.add_column("invoice_items", sa.Column("normalized_unit_price", sa.Numeric(24, 6), nullable=True))
    op.create_foreign_key(
        "fk_invoice_items_normalized_unit_id", "invoice_items",
        "units_of_measure", ["normalized_unit_id"], ["id"], ondelete="RESTRICT",
    )

    # item_type CHECK — pre-check existing data, then constrain
    bad = conn.execute(sa.text(
        "SELECT COUNT(*) FROM invoice_items WHERE item_type NOT IN ('material','delivery','other')"
    )).scalar()
    if bad:
        raise RuntimeError(f"{bad} invoice_items rows have item_type outside material/delivery/other")
    op.create_check_constraint(
        "ck_item_type", "invoice_items", "item_type IN ('material','delivery','other')"
    )

    op.add_column("reference_prices", sa.Column("unit_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_reference_prices_unit_id", "reference_prices",
        "units_of_measure", ["unit_id"], ["id"], ondelete="RESTRICT",
    )

    op.add_column("compensation_corridors", sa.Column("material_type_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_corridor_material_type_id", "compensation_corridors",
        "material_types", ["material_type_id"], ["id"], ondelete="RESTRICT",
    )

    # ── Step 2: seed reference data ─────────────────────────────────────
    code_to_unit_id: dict[str, int] = {}
    # base units first (base_code is None), then derived
    for row in sorted(UNITS_SEED, key=lambda r: r["base_code"] is not None):
        base_id = code_to_unit_id.get(row["base_code"]) if row["base_code"] else None
        uid = conn.execute(sa.text(
            "INSERT INTO units_of_measure (code, name, symbol, dimension, base_unit_id, to_base_multiplier) "
            "VALUES (:code, :name, :symbol, :dimension, :base_unit_id, :mult) RETURNING id"
        ), {
            "code": row["code"], "name": row["name"], "symbol": row["symbol"],
            "dimension": row["dimension"], "base_unit_id": base_id,
            "mult": Decimal(row["multiplier"]),
        }).scalar()
        code_to_unit_id[row["code"]] = uid

    for key, unit_code in ALIASES_SEED.items():
        conn.execute(sa.text(
            "INSERT INTO unit_aliases (raw_text, unit_id) VALUES (:raw, :uid)"
        ), {"raw": key, "uid": code_to_unit_id[unit_code]})

    mt_code_to_id: dict[str, int] = {}
    for row in MATERIAL_TYPES_SEED:
        du = code_to_unit_id.get(row["default_unit_code"]) if row["default_unit_code"] else None
        mid = conn.execute(sa.text(
            "INSERT INTO material_types (code, name, default_unit_id) "
            "VALUES (:code, :name, :du) RETURNING id"
        ), {"code": row["code"], "name": row["name"], "du": du}).scalar()
        mt_code_to_id[row["code"]] = mid

    m3_id = code_to_unit_id["M3"]

    # ── Step 3: guards + backfill ───────────────────────────────────────
    # Guard 1: every material_classes.material_type is known
    distinct_types = [r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT material_type FROM material_classes"
    ))]
    unknown = set(distinct_types) - _KNOWN_MATERIAL_TYPES
    if unknown:
        raise RuntimeError(f"material_classes has unknown material_type values: {sorted(unknown)}")

    # Guard 2: all reference_prices belong to concrete classes (read OLD string column)
    ref_types = [r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT mc.material_type "
        "FROM reference_prices rp JOIN material_classes mc ON rp.material_class_id = mc.id"
    ))]
    if set(ref_types) - {"concrete"}:
        raise RuntimeError(
            f"reference_prices reference non-concrete classes {sorted(set(ref_types))}; "
            "backfill 'all M3' is invalid — add explicit unit mapping"
        )

    conn.execute(sa.text(
        "UPDATE material_classes SET material_type_id = "
        "(SELECT id FROM material_types WHERE code = material_classes.material_type)"
    ))
    conn.execute(sa.text("UPDATE reference_prices SET unit_id = :m3"), {"m3": m3_id})
    conn.execute(sa.text(
        "UPDATE compensation_corridors SET material_type_id = "
        "(SELECT id FROM material_types WHERE code = compensation_corridors.material_type) "
        "WHERE material_type IS NOT NULL"
    ))

    # invoice_items normalization (Python-side key normalization, not SQL lower/trim)
    # aliases_by_key: normalized key → (base_unit_id, multiplier)
    rows = conn.execute(sa.text(
        "SELECT a.raw_text, COALESCE(u.base_unit_id, u.id) AS base_id, u.to_base_multiplier AS mult "
        "FROM unit_aliases a JOIN units_of_measure u ON a.unit_id = u.id"
    )).all()
    aliases_by_key = {r.raw_text: (r.base_id, r.mult) for r in rows}

    distinct_raw = [r[0] for r in conn.execute(sa.text(
        "SELECT DISTINCT raw_unit FROM invoice_items WHERE raw_unit IS NOT NULL"
    ))]
    for raw in distinct_raw:
        match = aliases_by_key.get(normalize_unit_key(raw))
        if not match:
            continue
        base_id, mult = match
        conn.execute(sa.text(
            "UPDATE invoice_items SET "
            "normalized_unit_id = :base, "
            "normalized_quantity = quantity * :mult, "
            "normalized_unit_price = unit_price / :mult "
            "WHERE raw_unit = :raw"
        ), {"base": base_id, "mult": mult, "raw": raw})

    # ── Step 4: tighten ─────────────────────────────────────────────────
    op.alter_column("material_classes", "material_type_id", nullable=False)
    op.drop_column("material_classes", "material_type")
    op.create_index("ix_material_classes_material_type_id", "material_classes", ["material_type_id"])

    op.alter_column("reference_prices", "unit_id", nullable=False)

    op.create_index("ix_invoice_items_normalized_unit_id", "invoice_items", ["normalized_unit_id"])

    # compensation_corridors: rebuild both partial indexes + CHECK onto material_type_id
    op.drop_index("uq_corridor_project_type", table_name="compensation_corridors")
    op.drop_index("uq_corridor_project_class", table_name="compensation_corridors")
    op.drop_constraint("chk_corridor_target_exclusive", "compensation_corridors", type_="check")
    op.drop_column("compensation_corridors", "material_type")
    op.create_check_constraint(
        "chk_corridor_target_exclusive", "compensation_corridors",
        "(material_type_id IS NOT NULL AND material_class_id IS NULL) OR "
        "(material_type_id IS NULL AND material_class_id IS NOT NULL)",
    )
    op.create_index(
        "uq_corridor_project_type", "compensation_corridors",
        ["project_id", "material_type_id"], unique=True,
        postgresql_where=sa.text("material_class_id IS NULL"),
    )
    op.create_index(
        "uq_corridor_project_class", "compensation_corridors",
        ["project_id", "material_class_id"], unique=True,
        postgresql_where=sa.text("material_type_id IS NULL"),
    )


def downgrade() -> None:
    conn = op.get_bind()

    # material_classes: restore string column
    op.add_column("material_classes", sa.Column("material_type", sa.String(), nullable=True))
    conn.execute(sa.text(
        "UPDATE material_classes SET material_type = "
        "(SELECT code FROM material_types WHERE id = material_classes.material_type_id)"
    ))
    op.alter_column("material_classes", "material_type", nullable=False)
    op.drop_index("ix_material_classes_material_type_id", table_name="material_classes")
    op.drop_constraint("fk_material_classes_material_type_id", "material_classes", type_="foreignkey")
    op.drop_column("material_classes", "material_type_id")

    # compensation_corridors: back to string structure (corridor-fallback shape)
    op.drop_index("uq_corridor_project_type", table_name="compensation_corridors")
    op.drop_index("uq_corridor_project_class", table_name="compensation_corridors")
    op.drop_constraint("chk_corridor_target_exclusive", "compensation_corridors", type_="check")
    op.add_column("compensation_corridors", sa.Column("material_type", sa.String(), nullable=True))
    conn.execute(sa.text(
        "UPDATE compensation_corridors SET material_type = "
        "(SELECT code FROM material_types WHERE id = compensation_corridors.material_type_id) "
        "WHERE material_type_id IS NOT NULL"
    ))
    op.drop_constraint("fk_corridor_material_type_id", "compensation_corridors", type_="foreignkey")
    op.drop_column("compensation_corridors", "material_type_id")
    op.create_check_constraint(
        "chk_corridor_target_exclusive", "compensation_corridors",
        "(material_type IS NOT NULL AND material_class_id IS NULL) OR "
        "(material_type IS NULL AND material_class_id IS NOT NULL)",
    )
    op.create_index(
        "uq_corridor_project_type", "compensation_corridors",
        ["project_id", "material_type"], unique=True,
        postgresql_where=sa.text("material_class_id IS NULL"),
    )
    op.create_index(
        "uq_corridor_project_class", "compensation_corridors",
        ["project_id", "material_class_id"], unique=True,
        postgresql_where=sa.text("material_type IS NULL"),
    )

    # invoice_items
    op.drop_index("ix_invoice_items_normalized_unit_id", table_name="invoice_items")
    op.drop_constraint("fk_invoice_items_normalized_unit_id", "invoice_items", type_="foreignkey")
    op.drop_column("invoice_items", "normalized_unit_price")
    op.drop_column("invoice_items", "normalized_quantity")
    op.drop_column("invoice_items", "normalized_unit_id")
    op.drop_constraint("ck_item_type", "invoice_items", type_="check")
    op.alter_column("invoice_items", "raw_unit", new_column_name="unit")

    # reference_prices
    op.drop_constraint("fk_reference_prices_unit_id", "reference_prices", type_="foreignkey")
    op.drop_column("reference_prices", "unit_id")

    # drop reference tables
    op.drop_table("unit_aliases")
    op.drop_table("material_types")
    op.drop_table("units_of_measure")
```

- [ ] **Step 2: Apply migration to the test DB (up)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head 2>&1"`
Expected: `Running upgrade d0e1f2a3b4c5 -> e1f2a3b4c5d6`, no errors.

- [ ] **Step 3: Verify round-trip (down then up)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && DATABASE_URL=$TEST_DATABASE_URL alembic downgrade -1 && DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head 2>&1"`
Expected: downgrade then upgrade both succeed with no errors.

- [ ] **Step 4: Commit**

```
git add backend/alembic/versions/2026_06_08_1300-e1f2a3b4c5d6_units_refactoring.py
git commit -m "feat(units): alembic migration — schema, seed, backfill, down"
```

---

## Task A4: Update factories + migration smoke test

**Files:**
- Modify: `backend/tests/factories.py`
- Create: `backend/tests/integration/test_normalization_integration.py` (smoke part only here; flow tests added in B3)

The test DB now has seeded `material_types` (concrete/rebar/other) and `units_of_measure`. Factories resolve seeded IDs by code via the session.

- [ ] **Step 1: Update factories**

In `backend/tests/factories.py`, update imports:

```python
from models import (
    CompensationCorridor,
    Document,
    Invoice,
    InvoiceItem,
    MaterialClass,
    MaterialType,
    Organization,
    OrgRole,
    Project,
    ProjectRole,
    ReferencePrice,
    Supplier,
    UnitOfMeasure,
    User,
)
```

Add seed-lookup helpers after `_register_session`:

```python
def _unit_id(code: str) -> int:
    session = _session_holder["session"]
    return session.query(UnitOfMeasure).filter_by(code=code).one().id


def _material_type_id(code: str) -> int:
    session = _session_holder["session"]
    return session.query(MaterialType).filter_by(code=code).one().id
```

Replace `MaterialClassFactory`:

```python
class MaterialClassFactory(_BaseFactory):
    class Meta:
        model = MaterialClass

    # Default to concrete/В25. Tests override material_type_code to switch type.
    class Params:
        material_type_code = "concrete"

    material_type_id = factory.LazyAttribute(lambda obj: _material_type_id(obj.material_type_code))
    name = factory.LazyAttribute(
        lambda obj: {"concrete": "В25", "rebar": "d12", "other": "X"}.get(obj.material_type_code, "X")
    )
    calc_role = "base"
```

Replace `ReferencePriceFactory` (add `unit`):

```python
class ReferencePriceFactory(_BaseFactory):
    class Meta:
        model = ReferencePrice

    project = factory.SubFactory(ProjectFactory)
    material_class = factory.SubFactory(MaterialClassFactory)
    unit_id = factory.LazyAttribute(lambda _: _unit_id("M3"))
    price = 8000.0
    period_start = date(2026, 1, 1)
    period_end = date(2026, 12, 31)
    source = "контракт"
```

Replace `InvoiceItemFactory` (raw_unit + normalized fields for м³ default):

```python
class InvoiceItemFactory(_BaseFactory):
    class Meta:
        model = InvoiceItem

    invoice = factory.SubFactory(InvoiceFactory)
    raw_name = "Бетон В25"
    item_type = "material"
    quantity = 5.0
    raw_unit = "м3"
    normalized_unit_id = factory.LazyAttribute(lambda _: _unit_id("M3"))
    normalized_quantity = factory.LazyAttribute(lambda obj: obj.quantity)
    unit_price = 8000.0
    normalized_unit_price = factory.LazyAttribute(lambda obj: obj.unit_price)
    amount = factory.LazyAttribute(lambda obj: obj.quantity * obj.unit_price)
    vat_amount = factory.LazyAttribute(lambda obj: round(obj.amount * 0.20, 2))
```

Replace `CompensationCorridorFactory` (material_type_id instead of material_type):

```python
class CompensationCorridorFactory(_BaseFactory):
    class Meta:
        model = CompensationCorridor

    project_id = factory.LazyAttribute(lambda _: ProjectFactory.create().id)
    material_type_id = None
    material_class_id = factory.LazyAttribute(lambda _: MaterialClassFactory.create().id)
    is_compensable = True
    corridor_pct = Decimal("5.00")
```

- [ ] **Step 2: Write the smoke test**

Create `backend/tests/integration/test_normalization_integration.py`:

```python
"""Integration tests for unit normalization (write-time)."""
from crud.units import load_alias_map
from models import MaterialType, UnitAlias, UnitOfMeasure


class TestSeedData:
    def test_units_seeded(self, db_session):
        codes = {u.code for u in db_session.query(UnitOfMeasure).all()}
        assert {"TON", "KG", "M3", "L", "M", "PCS"} <= codes

    def test_material_types_seeded(self, db_session):
        codes = {m.code for m in db_session.query(MaterialType).all()}
        assert {"concrete", "rebar", "other"} == codes

    def test_aliases_resolve_to_base(self, db_session):
        amap = load_alias_map(db_session)
        # "кг" → base unit TON (mass), multiplier 0.001
        kg = amap["кг"]
        ton_id = db_session.query(UnitOfMeasure).filter_by(code="TON").one().id
        assert kg.base_unit_id == ton_id
        assert str(kg.multiplier) == "0.001000000000000"  # Numeric(30,15)
        assert kg.dimension == "mass"

    def test_factory_builds_material_class(self, factories, db_session):
        mc = factories.MaterialClassFactory.create(material_type_code="rebar")
        assert mc.material_type.code == "rebar"
```

- [ ] **Step 3: Run the smoke test**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_normalization_integration.py -v 2>&1"`
Expected: PASS. (If `multiplier` string assertion mismatches, adjust to the exact Numeric(30,15) repr printed.)

- [ ] **Step 4: Commit**

```
git add backend/tests/factories.py backend/tests/integration/test_normalization_integration.py
git commit -m "test(units): factories for seeded reference data + seed smoke test"
```

---

# Milestone B — Write-time normalization

## Task B1: `get_or_create_material_class` + `get_material_classes` resolve code↔id

**Files:**
- Modify: `backend/crud/materials.py`
- Modify: `backend/routers/material_classes.py`
- Test: `backend/tests/integration/test_material_classes.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_material_classes.py`:

```python
class TestMaterialTypeResolution:
    def test_create_resolves_material_type_code(self, client):
        resp = client.post("/api/material-classes", json={"name": "В30", "material_type": "concrete"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "В30"
        assert body["material_type"] == "concrete"

    def test_list_emits_material_type_code(self, client, factories):
        factories.MaterialClassFactory.create(material_type_code="rebar", name="d10")
        resp = client.get("/api/material-classes")
        assert resp.status_code == 200
        rebars = [c for c in resp.json() if c["material_type"] == "rebar"]
        assert any(c["name"] == "d10" for c in rebars)

    def test_create_unknown_type_returns_422(self, client):
        resp = client.post("/api/material-classes", json={"name": "X", "material_type": "wood"})
        assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_material_classes.py::TestMaterialTypeResolution -v 2>&1"`
Expected: FAIL (currently passes `material_type` string straight to ORM column that no longer exists → error / 500).

- [ ] **Step 3: Update `crud/materials.py`**

Replace the material-class functions in `backend/crud/materials.py`:

```python
import logging

from sqlalchemy.orm import Session

from models import InvoiceItem, MaterialClass, MaterialType, ReferencePrice

logger = logging.getLogger(__name__)

VALID_CALC_ROLES = {"base", "additive", "exclude"}


class UnknownMaterialType(ValueError):
    """Raised when a material_type code is not in the material_types table."""


def _material_type_id_by_code(db: Session, code: str) -> int:
    mt = db.query(MaterialType).filter(MaterialType.code == code).first()
    if mt is None:
        raise UnknownMaterialType(code)
    return mt.id


def get_material_classes(db: Session, material_type: str = None):
    q = (
        db.query(MaterialClass)
        .join(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .order_by(MaterialType.code, MaterialClass.name)
    )
    if material_type:
        q = q.filter(MaterialType.code == material_type)
    return q.all()


def get_material_class(db: Session, class_id: int):
    return db.query(MaterialClass).filter(MaterialClass.id == class_id).first()


def get_or_create_material_class(
    db: Session, name: str, material_type: str, calc_role: str = "base"
) -> MaterialClass:
    if calc_role not in VALID_CALC_ROLES:
        raise ValueError(f"Unknown calc_role {calc_role!r}; allowed: {sorted(VALID_CALC_ROLES)}")
    material_type_id = _material_type_id_by_code(db, material_type)
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type_id == material_type_id
    ).first()
    if not mc:
        mc = MaterialClass(name=name, material_type_id=material_type_id, calc_role=calc_role)
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

- [ ] **Step 4: Update `routers/material_classes.py`**

Replace `backend/routers/material_classes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from crud.materials import (
    UnknownMaterialType,
    delete_material_class,
    get_material_classes,
    get_or_create_material_class,
)
from database import get_db

router = APIRouter()


class MaterialClassCreate(BaseModel):
    name: str
    material_type: str  # concrete / rebar / other


@router.get("")
def list_material_classes(material_type: str | None = None, db: Session = Depends(get_db)):
    classes = get_material_classes(db, material_type)
    return [
        {"id": mc.id, "name": mc.name, "material_type": mc.material_type.code}
        for mc in classes
    ]


@router.post("")
def create_material_class(data: MaterialClassCreate, db: Session = Depends(get_db)):
    try:
        mc = get_or_create_material_class(db, data.name, data.material_type)
    except UnknownMaterialType:
        raise HTTPException(status_code=422, detail=f"Неизвестный тип материала: {data.material_type}")
    return {"id": mc.id, "name": mc.name, "material_type": mc.material_type.code}


@router.delete("/{class_id}")
def delete_material_class_route(class_id: int, db: Session = Depends(get_db)):
    mc = delete_material_class(db, class_id)
    if not mc:
        raise HTTPException(status_code=404, detail="Класс не найден")
    return {"message": "Удалено"}
```

- [ ] **Step 5: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_material_classes.py -v 2>&1"`
Expected: PASS (new + existing).

- [ ] **Step 6: Commit**

```
git add backend/crud/materials.py backend/routers/material_classes.py backend/tests/integration/test_material_classes.py
git commit -m "feat(units): material_class resolves material_type code↔id"
```

---

## Task B2: Normalize units in `create_invoice`

**Files:**
- Modify: `backend/crud/documents.py`
- Test: `backend/tests/integration/test_normalization_integration.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_normalization_integration.py`:

```python
from datetime import date
from decimal import Decimal

from crud.documents import create_invoice


class TestCreateInvoiceNormalization:
    def test_kg_normalized_to_ton(self, db_session, factories):
        doc = factories.DocumentFactory.create()
        inv = create_invoice(
            db_session, document_id=doc.id, number="N1", invoice_date=date(2026, 3, 1),
            supplier_name=None, supplier_inn=None, vat_rate=20.0, confidence=0.9,
            items=[{
                "raw_name": "Арматура", "item_type": "material", "material_class_id": None,
                "quantity": 5000, "unit": "кг", "unit_price": 0.05,
                "amount": 250, "vat_amount": None,
            }],
        )
        item = inv.items[0]
        assert item.raw_unit == "кг"
        assert item.normalized_quantity == Decimal("5.000000")        # 5000 * 0.001
        assert item.normalized_unit_price == Decimal("50.000000")     # 0.05 / 0.001

    def test_unknown_unit_leaves_normalized_null(self, db_session, factories):
        doc = factories.DocumentFactory.create()
        inv = create_invoice(
            db_session, document_id=doc.id, number="N2", invoice_date=date(2026, 3, 1),
            supplier_name=None, supplier_inn=None, vat_rate=20.0, confidence=0.9,
            items=[{
                "raw_name": "Странное", "item_type": "material", "material_class_id": None,
                "quantity": 1, "unit": "бухта", "unit_price": 100,
                "amount": 100, "vat_amount": None,
            }],
        )
        item = inv.items[0]
        assert item.raw_unit == "бухта"
        assert item.normalized_unit_id is None
        assert item.normalized_quantity is None
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_normalization_integration.py::TestCreateInvoiceNormalization -v 2>&1"`
Expected: FAIL (`create_invoice` still sets `unit=`, no normalization).

- [ ] **Step 3: Update `create_invoice`**

In `backend/crud/documents.py`, add the import at top:

```python
from crud.units import load_alias_map, normalize_item
```

Replace the item-building loop inside `create_invoice` (the `for item in items:` block) with:

```python
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
```

Note: `item["item_type"]` stays a raw string ("material"/etc.); the `SqlEnum(ItemType, native_enum=False)` column accepts it (value == name).

- [ ] **Step 4: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_normalization_integration.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/crud/documents.py backend/tests/integration/test_normalization_integration.py
git commit -m "feat(units): normalize invoice-item units in create_invoice"
```

---

## Task B3: `raw_unit` rename + renormalization + warnings in invoice PUT; `has_issues`

**Files:**
- Modify: `backend/routers/invoices.py`
- Modify: `backend/routers/dashboard.py`
- Test: `backend/tests/integration/test_normalization_integration.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_normalization_integration.py`:

```python
class TestInvoiceEditRenormalization:
    def test_edit_renormalizes_and_warns_on_unknown_unit(self, client, factories):
        inv = factories.InvoiceFactory.create()
        item = factories.InvoiceItemFactory.create(invoice=inv, raw_unit="т", quantity=2)
        payload = {
            "number": inv.number, "date": inv.date.isoformat(),
            "supplier_name": None, "supplier_inn": None, "vat_rate": 20.0,
            "items": [{
                "id": item.id, "raw_name": "Арматура", "item_type": "material",
                "material_class_id": None, "quantity": 2, "raw_unit": "бухта",
                "unit_price": 100, "amount": 200, "vat_amount": None,
            }],
        }
        resp = client.put(f"/api/invoices/{inv.id}", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert any(w["code"] == "unknown_unit" for w in body.get("warnings", []))

    def test_edit_known_unit_no_warning(self, client, factories):
        inv = factories.InvoiceFactory.create()
        item = factories.InvoiceItemFactory.create(invoice=inv)
        payload = {
            "number": inv.number, "date": inv.date.isoformat(),
            "supplier_name": None, "supplier_inn": None, "vat_rate": 20.0,
            "items": [{
                "id": item.id, "raw_name": "Бетон", "item_type": "material",
                "material_class_id": None, "quantity": 3, "raw_unit": "м3",
                "unit_price": 8000, "amount": 24000, "vat_amount": None,
            }],
        }
        resp = client.put(f"/api/invoices/{inv.id}", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("warnings", []) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_normalization_integration.py::TestInvoiceEditRenormalization -v 2>&1"`
Expected: FAIL (`InvoiceItemEdit` has `unit`, not `raw_unit`; no warnings; no renormalization).

- [ ] **Step 3: Update `routers/invoices.py`**

In `backend/routers/invoices.py`:

(a) Update imports — add `Decimal`, the units helpers, and pydantic alias helpers. Change the existing `from pydantic import BaseModel, Field` line to:

```python
from decimal import Decimal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from crud.units import load_alias_map, normalize_item
from models import Invoice, InvoiceItem, MaterialClass  # MaterialClass kept for existing use
```

(b) Change `InvoiceItemEdit.unit` → `raw_unit`, accepting the legacy `unit` key on input (transition compat — see Deployment ordering note):

```python
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
```

(c) Extend `_doc_has_issues` to flag un-normalized material rows:

```python
def _doc_has_issues(doc) -> bool:
    """Документ требует проверки, если есть позиции, по которым нельзя считать аналитику:
    нет количества, нет описания, или единица измерения материала не нормализована."""
    from crud.units import invariant_holds  # noqa: PLC0415
    for inv in doc.invoices:
        if not inv.items:
            return True
        for item in inv.items:
            if (item.quantity or 0) <= 0:
                return True
            if not (item.raw_name or "").strip():
                return True
            # Material rows must normalize to a base unit; otherwise they cannot be aggregated.
            if item.item_type == "material" and item.normalized_unit_id is None:
                return True
            if not invariant_holds(item.quantity, item.unit_price, item.amount):
                return True
    return False
```

(d) In `_serialize_document`, serialize `raw_unit` + normalized fields, and ALSO emit a legacy `unit` key (same value) for transition compat (see Deployment ordering note):

```python
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
```

(e) Rewrite the item-update loop in `update_invoice` to renormalize and collect warnings. Replace the block from `incoming_ids = ...` through `db.commit()` / `return ...` with:

```python
    incoming_ids = {item.id for item in data.items if item.id is not None}
    existing = {item.id: item for item in invoice.items}

    for existing_id, existing_item in existing.items():
        if existing_id not in incoming_ids:
            db.delete(existing_item)

    aliases = load_alias_map(db)
    warnings: list[dict] = []

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
```

(f) `from decimal import Decimal` is already added in (a) — confirm it is present at the top of `invoices.py`.

- [ ] **Step 4: Update `routers/dashboard.py` serializer + `_has_issues`**

In `backend/routers/dashboard.py`, `list_project_invoices`:

Replace `_has_issues` with:

```python
    def _has_issues(inv):
        from crud.units import invariant_holds  # noqa: PLC0415
        if not inv.items:
            return True
        for it in inv.items:
            if (it.quantity or 0) <= 0:
                return True
            if not (it.raw_name or "").strip():
                return True
            if it.item_type == "material" and it.normalized_unit_id is None:
                return True
            if not invariant_holds(it.quantity, it.unit_price, it.amount):
                return True
        return False
```

Change the item serialization `"unit": item.unit` → `"raw_unit": item.raw_unit` (plus the legacy `unit` alias):

```python
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
```

- [ ] **Step 5: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_normalization_integration.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add backend/routers/invoices.py backend/routers/dashboard.py backend/tests/integration/test_normalization_integration.py
git commit -m "feat(units): raw_unit rename, renormalize on edit, unknown-unit warnings, has_issues"
```

---

# Milestone C — Calculator (corridor key, dimension guard, delivery distribution)

## Task C1: Corridor resolver type key string → `material_type_id`

**Files:**
- Modify: `backend/crud/compensation_corridors.py`
- Modify: `backend/routers/projects.py`
- Modify: `backend/tests/unit/test_resolve_corridor.py`
- Test: `backend/tests/integration/test_compensation_corridors.py` (adjust if it calls CRUD directly)

- [ ] **Step 1: Update the unit test to int type keys**

Rewrite `backend/tests/unit/test_resolve_corridor.py` to key `by_type` by `material_type_id` (int) and pass an int 4th arg:

```python
"""Unit tests for corridor fallback resolution — no DB required."""
from decimal import Decimal
from types import SimpleNamespace

from crud.compensation_corridors import resolve_corridor

D = Decimal
CONCRETE = 10  # stand-in material_type_id
REBAR = 20


def _row(is_compensable: bool, corridor_pct: Decimal | None) -> SimpleNamespace:
    return SimpleNamespace(is_compensable=is_compensable, corridor_pct=corridor_pct)


class TestResolveCorridorFallback:
    def test_no_rows_returns_none(self):
        compensable, pct = resolve_corridor({}, {}, 1, CONCRETE)
        assert compensable is None
        assert pct is None

    def test_type_level_compensable(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, CONCRETE)
        assert compensable is True
        assert pct == D("5.00")

    def test_type_level_not_compensable(self):
        by_type = {REBAR: _row(False, None)}
        compensable, pct = resolve_corridor({}, by_type, 1, REBAR)
        assert compensable is False
        assert pct is None

    def test_class_override_wins_over_type(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 42, CONCRETE)
        assert compensable is True
        assert pct == D("7.00")

    def test_class_override_can_disable_over_type_enabled(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        by_class = {42: _row(False, None)}
        compensable, pct = resolve_corridor(by_class, by_type, 42, CONCRETE)
        assert compensable is False
        assert pct is None

    def test_class_override_can_enable_over_type_disabled(self):
        by_type = {REBAR: _row(False, None)}
        by_class = {55: _row(True, D("3.00"))}
        compensable, pct = resolve_corridor(by_class, by_type, 55, REBAR)
        assert compensable is True
        assert pct == D("3.00")

    def test_unrelated_type_not_matched(self):
        by_type = {CONCRETE: _row(True, D("5.00"))}
        compensable, pct = resolve_corridor({}, by_type, 1, REBAR)
        assert compensable is None
        assert pct is None

    def test_unrelated_class_not_matched(self):
        by_class = {42: _row(True, D("7.00"))}
        compensable, pct = resolve_corridor(by_class, {}, 99, CONCRETE)
        assert compensable is None
        assert pct is None
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_resolve_corridor.py -v 2>&1"`
Expected: PASS already (resolver still works with any hashable key) — these are equivalent. If PASS, that's fine; the substantive change is in `get_corridor_map`/CRUD/router below. Proceed.

- [ ] **Step 3: Update `crud/compensation_corridors.py`**

Replace `backend/crud/compensation_corridors.py` with the `material_type_id`-keyed version:

```python
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CompensationCorridor, MaterialClass, MaterialType


def get_corridor_rows(db: Session, project_id: int) -> list[CompensationCorridor]:
    return (
        db.query(CompensationCorridor)
        .filter(CompensationCorridor.project_id == project_id)
        .all()
    )


def get_corridor_map(
    db: Session, project_id: int,
) -> tuple[dict[int, CompensationCorridor], dict[int, CompensationCorridor]]:
    """Single query → (by_class, by_type) dicts. by_type keyed by material_type_id."""
    rows = get_corridor_rows(db, project_id)
    by_class = {r.material_class_id: r for r in rows if r.material_class_id is not None}
    by_type = {r.material_type_id: r for r in rows if r.material_type_id is not None}
    return by_class, by_type


def resolve_corridor(
    by_class: dict[int, CompensationCorridor],
    by_type: dict[int, CompensationCorridor],
    class_id: int,
    material_type_id: int,
) -> tuple[bool | None, Decimal | None]:
    """Resolve corridor for a material class: class → type → None (not compensable)."""
    row = by_class.get(class_id) or by_type.get(material_type_id)
    if row is None:
        return None, None
    if not row.is_compensable:
        return False, None
    return True, row.corridor_pct


def set_type_corridor(
    db: Session, project_id: int, material_type_id: int,
    is_compensable: bool, corridor_pct: Decimal | None,
) -> None:
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type_id=material_type_id,
        material_class_id=None,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_type_id"],
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
    stmt = pg_insert(CompensationCorridor).values(
        project_id=project_id,
        material_type_id=None,
        material_class_id=material_class_id,
        is_compensable=is_compensable,
        corridor_pct=corridor_pct,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "material_class_id"],
        index_where=text("material_type_id IS NULL"),
        set_={
            "is_compensable": stmt.excluded.is_compensable,
            "corridor_pct": stmt.excluded.corridor_pct,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def delete_type_corridor(db: Session, project_id: int, material_type_id: int) -> bool:
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_type_id == material_type_id,
            CompensationCorridor.material_class_id.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def delete_class_corridor(db: Session, project_id: int, material_class_id: int) -> bool:
    deleted = (
        db.query(CompensationCorridor)
        .filter(
            CompensationCorridor.project_id == project_id,
            CompensationCorridor.material_class_id == material_class_id,
            CompensationCorridor.material_type_id.is_(None),
        )
        .delete()
    )
    db.commit()
    return deleted > 0


def build_resolved_matrix(db: Session, project_id: int) -> dict:
    """Resolved matrix for the corridors GET endpoint. Emits material_type CODE strings
    for the frontend (unchanged contract), resolving internally by material_type_id."""
    by_class, by_type = get_corridor_map(db, project_id)
    all_classes = (
        db.query(MaterialClass)
        .join(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .order_by(MaterialType.code, MaterialClass.name)
        .all()
    )
    # id → code for all material types present on classes
    type_code: dict[int, str] = {
        mt.id: mt.code for mt in db.query(MaterialType).all()
    }

    all_type_ids = sorted({mc.material_type_id for mc in all_classes})

    types_out = []
    for mt_id in all_type_ids:
        rule = by_type.get(mt_id)
        types_out.append({
            "material_type": type_code[mt_id],
            "is_compensable": rule.is_compensable if rule else None,
            "corridor_pct": rule.corridor_pct if rule else None,
            "has_rule": rule is not None,
        })

    classes_out = []
    for mc in all_classes:
        has_override = mc.id in by_class
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        if has_override:
            level = "class"
        elif mc.material_type_id in by_type:
            level = "type"
        else:
            level = "default"
        classes_out.append({
            "material_class_id": mc.id,
            "material_class_name": mc.name,
            "material_type": type_code[mc.material_type_id],
            "is_compensable": compensable if compensable is not None else False,
            "corridor_pct": pct,
            "level": level,
            "has_override": has_override,
        })

    return {"types": types_out, "classes": classes_out}
```

- [ ] **Step 4: Update `routers/projects.py` corridor endpoints (code→id)**

In `backend/routers/projects.py`:

(a) Add `MaterialType` to the models import:

```python
from models import Document, Invoice, MaterialClass, MaterialType, Project, Supplier
```

(b) Add a helper above the corridor endpoints (after the supplier-exclusion routes):

```python
def _resolve_material_type_id(db: Session, code: str) -> int:
    mt = db.query(MaterialType).filter(MaterialType.code == code).first()
    if mt is None:
        raise HTTPException(status_code=404, detail=f"Тип материала не найден: {code}")
    return mt.id
```

(c) Update the two type-level endpoints to map code→id:

```python
@router.put("/{project_id}/corridors/type/{material_type}")
def upsert_type_corridor(
    project_id: int,
    material_type: str,
    data: CorridorUpsert,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    mt_id = _resolve_material_type_id(db, material_type)
    set_type_corridor(db, project_id, mt_id, data.is_compensable, data.corridor_pct)
    return {"material_type": material_type, "is_compensable": data.is_compensable, "corridor_pct": data.corridor_pct}


@router.delete("/{project_id}/corridors/type/{material_type}", status_code=204)
def remove_type_corridor(
    project_id: int,
    material_type: str,
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Проект не найден")
    mt_id = _resolve_material_type_id(db, material_type)
    delete_type_corridor(db, project_id, mt_id)
    return Response(status_code=204)
```

(The class-level endpoints are unchanged — they already use `material_class_id`.)

- [ ] **Step 5: Adjust corridor integration tests if needed**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_compensation_corridors.py -v 2>&1"`
If failures arise from direct CRUD calls using `material_type="concrete"`, update those calls to `material_type_id=_material_type_id_by_code(db, "concrete")` (import from `crud.materials`) or to resolve via a `MaterialType` query. HTTP-level tests using `/corridors/type/concrete` should remain green (router maps code→id).

- [ ] **Step 6: Run unit + corridor integration tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_resolve_corridor.py tests/integration/test_compensation_corridors.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 7: Commit**

```
git add backend/crud/compensation_corridors.py backend/routers/projects.py backend/tests/unit/test_resolve_corridor.py backend/tests/integration/test_compensation_corridors.py
git commit -m "refactor(units): corridor resolver keys by material_type_id; API stays code-based"
```

---

## Task C2: Dimension guard + delivery distribution helpers (pure)

**Files:**
- Modify: `backend/crud/calculations.py` (add pure helpers)
- Test: `backend/tests/unit/test_delivery_distribution.py`
- Test: `backend/tests/unit/test_dimension_guard.py`

Extract the dimension-aware allocation and guard into pure, unit-testable functions before wiring them into the SQL calculator (Task C3).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_delivery_distribution.py`:

```python
"""Unit tests for dimension-aware delivery allocation — no DB."""
from decimal import Decimal
from types import SimpleNamespace

from crud.calculations import compute_shared_shares

D = Decimal


def _row(class_id, dimension, qty, amount):
    return SimpleNamespace(
        material_class_id=class_id, dimension=dimension,
        qty=D(qty), mat_total=D(amount),
    )


class TestComputeSharedShares:
    def test_mono_dimension_splits_by_quantity(self):
        rows = [_row(1, "volume", "30", "300"), _row(2, "volume", "10", "900")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("30") / D("40")
        assert shares[2] == D("10") / D("40")

    def test_mixed_dimension_splits_by_amount(self):
        rows = [_row(1, "volume", "50", "1000"), _row(2, "mass", "2", "3000")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("1000") / D("4000")
        assert shares[2] == D("3000") / D("4000")

    def test_mixed_dimension_zero_amount_no_split(self):
        rows = [_row(1, "volume", "50", "0"), _row(2, "mass", "2", "0")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")
        assert shares[2] == D("0")

    def test_partial_zero_amount_in_mixed(self):
        rows = [_row(1, "volume", "50", "0"), _row(2, "mass", "2", "4000")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")
        assert shares[2] == D("1")

    def test_mono_zero_qty_no_split(self):
        rows = [_row(1, "volume", "0", "0")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")

    def test_duplicate_class_id_accumulates_not_last_wins(self):
        # Same class appears twice (e.g. two normalized-unit rows) — basis must sum,
        # not drop one. Mixed dims here → amount basis; 1000 + 3000 share vs 4000 total.
        rows = [
            _row(1, "volume", "50", "1000"),
            _row(1, "mass", "2", "3000"),
            _row(2, "mass", "1", "4000"),
        ]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("4000") / D("8000")  # 1000+3000 summed, not last-wins 3000
        assert shares[2] == D("4000") / D("8000")
```

Create `backend/tests/unit/test_dimension_guard.py`:

```python
"""Unit tests for the ref-price dimension guard — no DB."""
from crud.calculations import dimension_matches


class TestDimensionMatches:
    def test_same_dimension_ok(self):
        assert dimension_matches("volume", "volume") is True

    def test_different_dimension_blocked(self):
        assert dimension_matches("volume", "mass") is False

    def test_none_class_dimension_blocked(self):
        # class with no normalized unit → cannot compare → blocked
        assert dimension_matches(None, "volume") is False

    def test_none_ref_dimension_blocked(self):
        assert dimension_matches("volume", None) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_delivery_distribution.py tests/unit/test_dimension_guard.py -v 2>&1"`
Expected: FAIL (`compute_shared_shares` / `dimension_matches` not defined).

- [ ] **Step 3: Add pure helpers to `crud/calculations.py`**

Add near the top of `backend/crud/calculations.py` (after `compute_compensation_per_unit`):

```python
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
```

- [ ] **Step 4: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_delivery_distribution.py tests/unit/test_dimension_guard.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/crud/calculations.py backend/tests/unit/test_delivery_distribution.py backend/tests/unit/test_dimension_guard.py
git commit -m "feat(units): pure dimension guard + dimension-aware delivery share helpers"
```

---

## Task C3: Wire normalized aggregation + guard + delivery into `compute_calculations`

**Files:**
- Modify: `backend/crud/calculations.py`
- Test: `backend/tests/integration/test_calculations_with_units.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_calculations_with_units.py`:

```python
"""Integration: compute_calculations with normalized units + dimension guard."""
from datetime import date
from decimal import Decimal

from crud.calculations import compute_calculations
from crud.units import load_alias_map, normalize_item
from models import InvoiceItem, UnitOfMeasure


def _add_item(db, invoice, material_class, raw_unit, quantity, unit_price):
    aliases = load_alias_map(db)
    q = Decimal(str(quantity))
    p = Decimal(str(unit_price))
    norm = normalize_item(raw_unit, q, p, aliases)
    item = InvoiceItem(
        invoice_id=invoice.id, raw_name="x", item_type="material",
        material_class_id=material_class.id, quantity=q, raw_unit=raw_unit,
        unit_price=p, amount=q * p, vat_amount=q * p * Decimal("0.2"),
        normalized_unit_id=norm.normalized_unit_id if norm else None,
        normalized_quantity=norm.normalized_quantity if norm else None,
        normalized_unit_price=norm.normalized_unit_price if norm else None,
    )
    db.add(item)
    db.commit()
    return item


class TestCalculationsWithUnits:
    def test_kg_aggregated_as_tons(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        # 2000 kg rebar @ 60 ₽/kg → 2 t @ 60000 ₽/t
        _add_item(db_session, inv, mc, raw_unit="кг", quantity=2000, unit_price=60)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["total_qty"] == Decimal("2.000")          # tons, not 2000
        assert row["dimension_mismatch"] is False
        assert row["unit_symbol"] == "т"

    def test_dimension_mismatch_blocks_deviation(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d10")
        # ref price in TON (mass) but item normalized to M (length, "пог.м")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="пог.м", quantity=100, unit_price=500)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["dimension_mismatch"] is True
        assert row["deviation_pct"] is None
        assert row["compensation_amount"] is None

    def test_unnormalized_rows_excluded(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В25")
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="бухта", quantity=5, unit_price=1000)  # unknown → NULL

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert all(r["material_class_id"] != mc.id for r in rows)

    def test_intra_class_dimension_mix_flagged(self, db_session, factories):
        # One class with two normalized dimensions in the same invoice (т + пог.м) → flagged,
        # not silently summed (mass + length).
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="т", quantity=2, unit_price=60000)
        _add_item(db_session, inv, mc, raw_unit="пог.м", quantity=100, unit_price=500)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["dimension_mismatch"] is True
        assert row["deviation_pct"] is None
        assert row["compensation_amount"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_calculations_with_units.py -v 2>&1"`
Expected: FAIL (`compute_calculations` still uses `quantity`, has no `dimension_mismatch`/`unit_symbol`).

- [ ] **Step 3: Rewrite `compute_calculations`**

> **Preserved semantics (do not change):**
> - **avg_price is with-VAT** and is compared to `ref_price`, which is entered with VAT
>   (per `docs/agent/calculations.md` → «Методология avg_price»). This with-VAT-vs-with-VAT
>   comparison is intentional and unchanged.
> - **Delivery for a fully-unnormalized invoice is intentionally dropped.** `base_rows`
>   filters `normalized_unit_id IS NOT NULL`; if every material row of an invoice is
>   unnormalized, that invoice contributes no base rows, so its delivery cost is not
>   distributed anywhere. This is acceptable because the document is flagged `has_issues`
>   (Task B3) and surfaces in the «Ошибки» tab for manual fixing — the cost is not lost,
>   it is quarantined until the unit is corrected.

Replace `_aggregate_by_class` and `compute_calculations` in `backend/crud/calculations.py`. Update the top import to include `UnitOfMeasure`:

```python
from models import Document, Invoice, InvoiceItem, MaterialClass, ReferencePrice, UnitOfMeasure
```

Replace `_aggregate_by_class` with a dimension-aware version:

```python
def _aggregate_by_class(base_rows, shared_per_invoice: dict[int, Decimal]) -> dict[int, dict]:
    """Distribute shared costs across base classes per invoice using dimension-aware shares.

    base_rows: rows with (invoice_id, material_class_id, mat_total, mat_vat, qty, dimension, symbol).
      qty is the SUM of normalized_quantity; mat_total is SUM(amount) excl VAT.
    Returns dict[class_id -> {mat_with_vat, shared_with_vat, qty, dimensions, symbol, invoice_ids}].
    """
    from collections import defaultdict

    rows_by_invoice: dict[int, list] = defaultdict(list)
    for row in base_rows:
        rows_by_invoice[row.invoice_id].append(row)

    class_contrib: dict[int, dict] = {}
    for inv_id, rows in rows_by_invoice.items():
        shares = compute_shared_shares(rows)            # one share per class_id
        shared_total = shared_per_invoice.get(inv_id, Decimal("0"))
        # Per-ROW accumulation: material, qty, dimensions (a class may have >1 row when
        # it spans dimensions — these MUST sum across rows).
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
        # Per-CLASS shared accrual: exactly ONCE per (invoice, class). Iterating over
        # `rows` here would double-count a class that has >1 row (shares is per-class).
        for cid, share in shares.items():
            class_contrib[cid]["shared_with_vat"] += shared_total * share
    return class_contrib
```

> **Intra-class dimension mix.** A class normally maps to one base unit, so it yields
> one row per invoice. If a class somehow has rows in two dimensions (e.g. a rebar
> class with both т and пог.м), the grouped query returns two rows; `dimensions`
> collects both, and `compute_calculations` treats `len(dimensions) > 1` as a
> `dimension_mismatch` (deviation/compensation nulled, row flagged) rather than
> silently summing mass + volume.

Now replace `compute_calculations`. Key changes vs the original:
- base-rows query filters `normalized_unit_id IS NOT NULL`, sums `normalized_quantity` as `qty`, joins `UnitOfMeasure` for `dimension`/`symbol`, and is NOT filtered by `material_class_id` (the output filter is applied at the end, preserving the full-invoice delivery denominator).
- drops the separate `base_qty_per_invoice` query (shares derived in `_aggregate_by_class`).
- caches `class_type_id_map` (cid → material_type_id) for the corridor resolver.
- adds dimension guard against the ref price's unit dimension; emits `unit_symbol`, `dimension_mismatch`.

```python
def compute_calculations(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
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

        # Additives (material + calc_role=additive), normalized rows only
        additive_per_invoice: dict[int, Decimal] = {}
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
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                MaterialClass.calc_role == "additive",
            )
            .group_by(InvoiceItem.invoice_id)
            .all()
        ):
            additive_per_invoice[row.invoice_id] = row.total_with_vat

        shared_per_invoice = {
            inv_id: delivery_per_invoice.get(inv_id, Decimal("0")) + additive_per_invoice.get(inv_id, Decimal("0"))
            for inv_id in set(delivery_per_invoice) | set(additive_per_invoice)
        }

        class_contrib = _aggregate_by_class(base_rows, shared_per_invoice)
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
```

- [ ] **Step 3b: Add the shared-accrual regression unit test**

Now that `_aggregate_by_class` has its new 2-arg signature, add a pure unit test guarding against double-counting shared cost on a multi-dimension class. Update the import line at the top of `backend/tests/unit/test_delivery_distribution.py`:

```python
from crud.calculations import _aggregate_by_class, compute_shared_shares
```

and append this helper + test classes to the file:

```python
def _agg_row(invoice_id, class_id, dimension, qty, mat_total, mat_vat, symbol="т"):
    return SimpleNamespace(
        invoice_id=invoice_id, material_class_id=class_id, dimension=dimension,
        qty=D(qty), mat_total=D(mat_total), mat_vat=D(mat_vat), symbol=symbol,
    )


class TestAggregateByClassSharedOnce:
    def test_multi_dim_class_shared_accrued_once(self):
        # One class, two dimension rows in one invoice, delivery=500.
        # The only class's share is 1.0 → shared must accrue exactly 500, not 1000.
        rows = [
            _agg_row(1, 1, "mass", "2", "1000", "200"),
            _agg_row(1, 1, "length", "100", "3000", "600"),
        ]
        contrib = _aggregate_by_class(rows, {1: D("500")})
        assert contrib[1]["shared_with_vat"] == D("500")     # once, not double
        assert contrib[1]["mat_with_vat"] == D("4800")       # (1000+200)+(3000+600), per-row sum
        assert contrib[1]["qty"] == D("102")                 # 2 + 100, per-row sum
        assert len(contrib[1]["dimensions"]) == 2            # flagged downstream

    def test_two_classes_shared_split_sums_to_total(self):
        # Mixed dims across classes → amount basis; full delivery distributed, no overflow.
        rows = [
            _agg_row(1, 1, "volume", "50", "1000", "200"),
            _agg_row(1, 2, "mass", "2", "3000", "600"),
        ]
        contrib = _aggregate_by_class(rows, {1: D("800")})
        total_shared = contrib[1]["shared_with_vat"] + contrib[2]["shared_with_vat"]
        assert total_shared == D("800")
```

- [ ] **Step 4: Run the new + existing calculation/exclusion tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_delivery_distribution.py tests/integration/test_calculations_with_units.py tests/integration/test_calculations_exclusions.py tests/integration/test_dashboard.py -v 2>&1"`
Expected: PASS. If `test_calculations_exclusions.py`/`test_dashboard.py` fail because their fixtures build items via `InvoiceItemFactory` (now normalized to м³) — they should still produce м³ rows and pass; fix any that asserted on raw `quantity` semantics by switching expectations to normalized values (м³ == raw for the м³ default).

- [ ] **Step 5: Commit**

```
git add backend/crud/calculations.py backend/tests/integration/test_calculations_with_units.py backend/tests/unit/test_delivery_distribution.py
git commit -m "feat(units): compute_calculations on normalized qty + dimension guard + delivery distribution"
```

---

## Task C4: Pass new calc fields through the dashboard endpoint

**Files:**
- Modify: `backend/routers/dashboard.py`

- [ ] **Step 1: Add the new fields to the `/calculations` response**

In `backend/routers/dashboard.py`, in `list_calculations`, add `unit_symbol` and `dimension_mismatch` to each serialized dict:

```python
    return [
        {
            "project_id": r["project_id"],
            "material_class_id": r["material_class_id"],
            "material_class_name": r["material_class_name"],
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
        for r in rows
    ]
```

- [ ] **Step 2: Run dashboard tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_dashboard.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 3: Commit**

```
git add backend/routers/dashboard.py
git commit -m "feat(units): expose unit_symbol + dimension_mismatch in /dashboard/calculations"
```

---

# Milestone D — Read endpoints + ref-price validation

## Task D1: `GET /api/units`, `/api/units/{id}/aliases`, `/api/material-types`

**Files:**
- Create: `backend/routers/units.py`
- Modify: `backend/main.py`
- Test: `backend/tests/integration/test_units_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_units_api.py`:

```python
class TestUnitsApi:
    def test_list_units(self, client):
        resp = client.get("/api/units")
        assert resp.status_code == 200
        codes = {u["code"] for u in resp.json()}
        assert {"TON", "KG", "M3", "L", "M", "PCS"} <= codes
        ton = next(u for u in resp.json() if u["code"] == "TON")
        assert ton["dimension"] == "mass"
        assert ton["symbol"] == "т"

    def test_list_aliases_for_ton(self, client):
        units = client.get("/api/units").json()
        ton_id = next(u["id"] for u in units if u["code"] == "TON")
        resp = client.get(f"/api/units/{ton_id}/aliases")
        assert resp.status_code == 200
        raw = {a["raw_text"] for a in resp.json()}
        assert "т" in raw and "тонн" in raw

    def test_list_material_types(self, client):
        resp = client.get("/api/material-types")
        assert resp.status_code == 200
        by_code = {m["code"]: m for m in resp.json()}
        assert set(by_code) == {"concrete", "rebar", "other"}
        assert by_code["concrete"]["default_unit"]["code"] == "M3"
        assert by_code["other"]["default_unit"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_units_api.py -v 2>&1"`
Expected: FAIL (404 — router not registered).

- [ ] **Step 3: Create the router**

Create `backend/routers/units.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import MaterialType, UnitAlias, UnitOfMeasure

router = APIRouter()


@router.get("")
def list_units(db: Session = Depends(get_db)):
    units = db.query(UnitOfMeasure).order_by(UnitOfMeasure.dimension, UnitOfMeasure.code).all()
    return [
        {
            "id": u.id,
            "code": u.code,
            "name": u.name,
            "symbol": u.symbol,
            "dimension": u.dimension,
            "base_unit_id": u.base_unit_id,
        }
        for u in units
    ]


@router.get("/{unit_id}/aliases")
def list_unit_aliases(unit_id: int, db: Session = Depends(get_db)):
    if not db.query(UnitOfMeasure).filter(UnitOfMeasure.id == unit_id).first():
        raise HTTPException(status_code=404, detail="Единица не найдена")
    aliases = db.query(UnitAlias).filter(UnitAlias.unit_id == unit_id).order_by(UnitAlias.raw_text).all()
    return [{"id": a.id, "raw_text": a.raw_text, "unit_id": a.unit_id} for a in aliases]
```

Create a second router for material types in the same file (mounted at a different prefix):

```python
material_types_router = APIRouter()


@material_types_router.get("")
def list_material_types(db: Session = Depends(get_db)):
    types = db.query(MaterialType).order_by(MaterialType.code).all()
    return [
        {
            "id": mt.id,
            "code": mt.code,
            "name": mt.name,
            "default_unit": (
                {"id": mt.default_unit.id, "code": mt.default_unit.code, "symbol": mt.default_unit.symbol}
                if mt.default_unit else None
            ),
        }
        for mt in types
    ]
```

- [ ] **Step 4: Register routers in `main.py`**

In `backend/main.py`, update the import line 21:

```python
from routers import dashboard, export, invoices, material_classes, projects, reference_prices, suppliers, units
```

and add after the `material_classes` include (line ~129):

```python
app.include_router(units.router, prefix="/api/units", tags=["units"], dependencies=_auth_dep)
app.include_router(units.material_types_router, prefix="/api/material-types", tags=["material-types"], dependencies=_auth_dep)
```

- [ ] **Step 5: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_units_api.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 6: Commit**

```
git add backend/routers/units.py backend/main.py backend/tests/integration/test_units_api.py
git commit -m "feat(units): read-only /api/units, /api/units/:id/aliases, /api/material-types"
```

---

## Task D2: `reference_prices` — `unit_id` field + validations

**Files:**
- Modify: `backend/routers/reference_prices.py`
- Modify: `backend/crud/projects.py` (the `create_reference_price` signature — verify and extend)
- Test: `backend/tests/integration/test_reference_prices_unit.py`

- [ ] **Step 1: Inspect `create_reference_price`**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && grep -n 'def create_reference_price\|def update_reference_price' crud/projects.py 2>&1"`
Expected: shows current signatures (no `unit_id`). You'll extend `create_reference_price` to accept and store `unit_id`.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/integration/test_reference_prices_unit.py`:

```python
from datetime import date

from models import MaterialType, UnitOfMeasure


def _unit_id(db, code):
    return db.query(UnitOfMeasure).filter_by(code=code).one().id


class TestReferencePriceUnit:
    def test_create_with_base_unit_ok(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В25")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "M3"),
            "price": 8000, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 200

    def test_derived_unit_rejected(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "KG"),  # derived, not base
            "price": 60, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 422

    def test_wrong_dimension_rejected(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В30")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "TON"),  # mass for a concrete (volume) class
            "price": 8000, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 422

    def test_other_type_allows_any_base_unit(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="other", name="Песок")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "TON"),  # default_unit is NULL → skip dim check
            "price": 500, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 200
```

- [ ] **Step 3: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_reference_prices_unit.py -v 2>&1"`
Expected: FAIL (no `unit_id` field; no validation).

- [ ] **Step 4: Extend `create_reference_price` in `crud/projects.py`**

Add `unit_id` to the `create_reference_price` signature and the `ReferencePrice(...)` constructor. Locate the function and update it, e.g.:

```python
def create_reference_price(db, project_id, material_class_id, price, period_start, period_end, source=None, unit_id=None):
    rp = ReferencePrice(
        project_id=project_id,
        material_class_id=material_class_id,
        unit_id=unit_id,
        price=price,
        period_start=period_start,
        period_end=period_end,
        source=source,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return rp
```

(Adjust to match the existing body; only `unit_id` is new.)

- [ ] **Step 5: Update `routers/reference_prices.py`**

Add `unit_id` to the create schema and validate. Replace the create section:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from crud.projects import create_reference_price, delete_reference_price, get_reference_prices, update_reference_price
from database import get_db
from models import MaterialClass, UnitOfMeasure

router = APIRouter()


class ReferencePriceCreate(BaseModel):
    project_id: int
    material_class_id: int
    unit_id: int
    price: Decimal
    period_start: date
    period_end: date
    source: str | None = None


def _validate_ref_unit(db: Session, material_class_id: int, unit_id: int) -> None:
    unit = db.query(UnitOfMeasure).filter(UnitOfMeasure.id == unit_id).first()
    if unit is None:
        raise HTTPException(status_code=422, detail="Единица измерения не найдена")
    # Validation 1: must be a base unit
    if unit.base_unit_id is not None:
        raise HTTPException(status_code=422, detail="Базовая цена задаётся только в базовой единице (т, м³, м, шт)")
    # Validation 2: dimension must match the class's material_type default unit (when defined)
    mc = (
        db.query(MaterialClass)
        .filter(MaterialClass.id == material_class_id)
        .first()
    )
    if mc is None:
        raise HTTPException(status_code=422, detail="Класс материала не найден")
    default_unit = mc.material_type.default_unit
    if default_unit is not None and default_unit.dimension != unit.dimension:
        raise HTTPException(
            status_code=422,
            detail=f"Размерность единицы ({unit.dimension}) не совпадает с типом материала ({default_unit.dimension})",
        )


@router.post("")
def create_reference_price_route(data: ReferencePriceCreate, db: Session = Depends(get_db)):
    _validate_ref_unit(db, data.material_class_id, data.unit_id)
    rp = create_reference_price(
        db, data.project_id, data.material_class_id, data.price,
        data.period_start, data.period_end, data.source, unit_id=data.unit_id,
    )
    return {"id": rp.id}
```

Keep `from datetime import date` and `from decimal import Decimal` imports at top. Update the GET/PATCH serializers to include `unit` info:

In `list_reference_prices` and `update_reference_price_route`, add to each returned dict:

```python
            "unit_id": rp.unit_id,
            "unit_symbol": rp.unit.symbol if rp.unit else None,
```

> **`unit_id` is immutable after create.** `ReferencePriceUpdate` (the PATCH schema)
> deliberately does NOT include `unit_id` — only `price`/`period_start`/`period_end`/`source`.
> So the create-only `_validate_ref_unit` cannot be bypassed via PATCH: there is no path
> to change the unit on an existing row. To change a reference price's unit, delete and
> recreate it. Do not add `unit_id` to `ReferencePriceUpdate` without also calling
> `_validate_ref_unit` in `update_reference_price_route`.

- [ ] **Step 6: Run tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_reference_prices_unit.py tests/integration/test_reference_prices.py -v 2>&1"`
Expected: PASS. Existing `test_reference_prices.py` creates rows via factory (which now sets `unit_id`); any POST in that file must include `unit_id` — add it where needed.

- [ ] **Step 7: Commit**

```
git add backend/routers/reference_prices.py backend/crud/projects.py backend/tests/integration/test_reference_prices_unit.py backend/tests/integration/test_reference_prices.py
git commit -m "feat(units): reference_prices require unit_id with base-unit + dimension validation"
```

---

# Milestone E — Excel export: raw + normalized column blocks

## Task E1: `compute_export_rows` normalized fields + two-block Excel layout

**Files:**
- Modify: `backend/crud/calculations.py` (`compute_export_rows`)
- Modify: `backend/routers/export.py`
- Test: `backend/tests/integration/test_export.py` (extend)

This is the highest-risk task: inserting 3 columns (two raw + one extra calc-block split) shifts every formula's column letters. Read `routers/export.py` fully before editing and change column letters consistently.

**Authoritative old→new column map (verify EVERY formula against this table).** The old layout had 18 columns (A–R); the new layout has 21 (A–U). A/B/C (Дата/Номер/Поставщик) are unchanged; columns D and E are NEW raw fields; everything from the old «Объём» onward shifts right.

| New | Header | Old | Notes |
|---|---|---|---|
| A | Дата УПД | A | unchanged |
| B | Номер УПД | B | unchanged |
| C | Поставщик | C | unchanged |
| **D** | **Кол-во по документу** | — | NEW raw (`raw_qty`) |
| **E** | **Ед. изм. по документу** | — | NEW raw (`raw_unit`) |
| **F** | **Расчётное кол-во** | D (Объём) | normalized `qty`; SUM/denominator base |
| **G** | **Базовая ед. изм.** | — | NEW (`unit_symbol`) |
| H | Базовая цена | E | ref price |
| I | Ставка НДС | F | vat rate |
| J | Материал без НДС | G | static |
| K | Доставка без НДС | H | static |
| L | Прочее без НДС | I | static |
| M | Итого без НДС | J | `=J+K+L` |
| N | Материал с НДС | K | `=J*(1+I)` |
| O | Доставка с НДС | L | `=K*(1+I)` |
| P | Прочее с НДС | M | `=L*(1+I)` |
| Q | Итого с НДС | N | `=N+O+P` |
| R | Откл. % | O | formula |
| S | Откл. ₽ | P | formula |
| T | Коридор, % | Q | aggregate rows only |
| U | Компенсация, ₽ | R | aggregate rows only |

Key shifts to remember: weighted-average SUMPRODUCT denominators move from **D → F**; the ref-price column used in deviation/denominator moves from **E → H**; deviation `%`/`₽` move from O/P → **R/S**. Data rows write A–L then formulas M–S (T/U stay blank on data rows — corridor/compensation are written only on month-header and class-total rows, exactly as in the original). Note: as in the prior layout, the trailing aggregate-only columns are left unstyled on data rows (no fill/border) — this is **pre-existing behavior, not a regression**. If you want a visually uniform table fill, optionally write empty styled cells for columns 20–21 in the data-row loop; this is cosmetic and not required.

- [ ] **Step 1: Update `compute_export_rows` to emit raw + normalized fields**

In `backend/crud/calculations.py`, in `compute_export_rows`:

(a) The base-rows query must use normalized quantity and join units. Change the `base_q` query to:
- filter `InvoiceItem.normalized_unit_id.isnot(None)`
- `func.sum(InvoiceItem.normalized_quantity).label("qty")` (normalized) and `func.sum(InvoiceItem.quantity).label("raw_qty")`
- add `UnitOfMeasure.symbol.label("symbol")`, `UnitOfMeasure.dimension.label("dimension")` via `.join(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)`
- also select a representative `raw_unit` via `func.max(InvoiceItem.raw_unit).label("raw_unit")`
- group by the unit symbol/dimension columns too.

(b) The total-base-qty denominator and delivery/additive allocation must use `compute_shared_shares` per invoice (dimension-aware) instead of `qty / total_base_qty`. Build a `shares_by_inv_class` map: group base_rows by invoice, call `compute_shared_shares(rows)`.

(c) Each output row adds: `raw_qty` (Кол-во по документу), `raw_unit` (Ед. изм. по документу), `qty` stays the normalized "Расчётное кол-во", `unit_symbol` (Базовая ед. изм.). The per-unit math (`mat_per_m3` etc.) divides by normalized `qty`.

Replace `compute_export_rows` with:

```python
def compute_export_rows(
    db: Session,
    project_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
    material_class_id: int | None = None,
    excluded_supplier_ids: set[int] | None = None,
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
        )
        .all()
    )
    if not base_rows:
        return []

    invoice_ids = list({r.invoice_id for r in base_rows})

    # Dimension-aware share per (invoice, class)
    rows_by_invoice = defaultdict(list)
    for r in base_rows:
        rows_by_invoice[r.invoice_id].append(r)
    share_by_inv_class: dict[tuple[int, int], Decimal] = {}
    for inv_id, rows in rows_by_invoice.items():
        for cid, share in compute_shared_shares(rows).items():
            share_by_inv_class[(inv_id, cid)] = share

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

    additive_per_inv: dict[int, Decimal] = {}
    additive_excl_per_inv: dict[int, Decimal] = {}
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
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            InvoiceItem.invoice_id.in_(invoice_ids),
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "additive",
        )
        .group_by(InvoiceItem.invoice_id)
        .all()
    ):
        additive_per_inv[r.invoice_id] = r.total_with_vat
        additive_excl_per_inv[r.invoice_id] = r.excl_vat

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
        inv_id = br.invoice_id
        cid = br.material_class_id
        qty = br.qty  # normalized
        if qty is None or qty <= 0:
            continue

        share = share_by_inv_class.get((inv_id, cid), Decimal("0"))
        mat_with_vat = br.mat_total + br.mat_vat
        delivery_alloc = delivery_per_inv.get(inv_id, Decimal("0")) * share
        additive_alloc = additive_per_inv.get(inv_id, Decimal("0")) * share
        delivery_excl_alloc = delivery_excl_per_inv.get(inv_id, Decimal("0")) * share
        additive_excl_alloc = additive_excl_per_inv.get(inv_id, Decimal("0")) * share

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
```

- [ ] **Step 2: Restructure the Excel columns in `routers/export.py`**

The two new raw columns go directly after «Поставщик» (col C). New layout — insert «Кол-во по документу» (D) and «Ед. изм. по документу» (E), then «Расчётное кол-во» (F) + «Базовая ед. изм.» (G), shifting everything else by **+3** vs the old layout (old D «Объём» → new F; old E «Базовая цена» → new H, etc.).

Replace `_COLUMNS` with:

```python
# Col 1=A Дата, 2=B Номер, 3=C Поставщик
# Raw block:  4=D Кол-во по документу, 5=E Ед. изм. по документу
# Calc block: 6=F Расчётное кол-во,    7=G Базовая ед. изм.
# 8=H Базовая цена, 9=I Ставка НДС
# 10=J Бетон без НДС, 11=K Доставка без НДС, 12=L Прочее без НДС, 13=M Итого без НДС (=J+K+L)
# 14=N Бетон с НДС (=J*(1+I)), 15=O Доставка с НДС (=K*(1+I)), 16=P Прочее с НДС (=L*(1+I))
# 17=Q Итого с НДС (=N+O+P), 18=R Откл.% , 19=S Откл.₽
# 20=T Коридор %, 21=U Компенсация ₽
_COLUMNS = [
    ("Дата УПД",                  13, _FMT_DATE,      "center"),  # A  1
    ("Номер УПД",                 14, "@",            "left"),    # B  2
    ("Поставщик",                 30, "@",            "left"),    # C  3
    ("Кол-во по документу",       14, _FMT_QTY,       "right"),   # D  4  raw
    ("Ед. изм. по документу",     14, "@",            "center"),  # E  5  raw
    ("Расчётное кол-во",          14, _FMT_QTY,       "right"),   # F  6  normalized
    ("Базовая ед. изм.",          12, "@",            "center"),  # G  7  normalized
    ("Базовая цена",              16, _FMT_MONEY,     "right"),   # H  8
    ("Ставка НДС, %",             10, _FMT_PCT_RATE,  "center"),  # I  9
    ("Материал без НДС",          16, _FMT_MONEY,     "right"),   # J 10  static
    ("Доставка без НДС",          16, _FMT_MONEY,     "right"),   # K 11  static
    ("Прочее без НДС",            16, _FMT_MONEY,     "right"),   # L 12  static
    ("Итого без НДС",             16, _FMT_MONEY,     "right"),   # M 13  =J+K+L
    ("Материал с НДС",            16, _FMT_MONEY,     "right"),   # N 14  =J*(1+I)
    ("Доставка с НДС",            16, _FMT_MONEY,     "right"),   # O 15  =K*(1+I)
    ("Прочее с НДС",              16, _FMT_MONEY,     "right"),   # P 16  =L*(1+I)
    ("Итого с НДС",               16, _FMT_MONEY,     "right"),   # Q 17  =N+O+P
    ("Откл. от плана, %",         16, _FMT_PCT,       "right"),   # R 18  formula
    ("Откл. от плана, ₽",         16, _FMT_MONEY,     "right"),   # S 19  formula
    ("Коридор, %",                11, _FMT_PCT_RATE,  "center"),  # T 20  static
    ("Компенсация, ₽",            16, _FMT_MONEY,     "right"),   # U 21  Python value
]
_N_COLS = len(_COLUMNS)
```

- [ ] **Step 3: Re-letter formulas in `_write_grand_total_row`**

Update column indices/letters (old→new): qty D→F, base price E→H, vat F→I, material G→J, delivery H→K, other I→L, total-excl J→M, mat-vat K→N, delivery-vat L→O, other-vat M→P, total-vat N→Q, dev% O→R, dev₽ P→S, corridor Q→T, comp R→U. Replace the function body's column references accordingly:

```python
def _write_grand_total_row(
    ws, row_num, label, fill, label_font, data_font, data_ranges, dev_total_py, comp_total=None,
):
    r = row_num

    def _c(col_idx, value, font=None, fmt=None, h="right"):
        cell = ws.cell(row=r, column=col_idx, value=value)
        cell.fill = fill
        cell.font = font or data_font
        cell.border = _BORDER
        cell.alignment = _align(h=h)
        if fmt:
            cell.number_format = fmt
        return cell

    _c(1, label, font=label_font, h="left")
    for ci in (2, 3, 4, 5, 7):   # B,C, raw-qty D, raw-unit E, base-unit G — blank/not aggregated
        _c(ci, None)

    sum_f = ",".join(f"F{s}:F{e}" for s, e in data_ranges)
    _c(6, f"=SUM({sum_f})", fmt=_FMT_QTY)        # F Расчётное кол-во
    _c(8, None)   # H Базовая цена — not averaged
    _c(9, None)   # I Ставка НДС — not averaged

    # Weighted averages: (Σ SUMPRODUCT(col, F)) / SUM(all F)
    for ci, cl in ((10, "J"), (11, "K"), (12, "L"), (14, "N"), (15, "O"), (16, "P")):
        sp = "+".join(f"SUMPRODUCT({cl}{s}:{cl}{e},F{s}:F{e})" for s, e in data_ranges)
        _c(ci, f'=IFERROR(({sp})/SUM({sum_f}),"")', fmt=_FMT_MONEY)

    _c(13, f"=J{r}+K{r}+L{r}", fmt=_FMT_MONEY)   # M Итого без НДС
    _c(17, f"=N{r}+O{r}+P{r}", fmt=_FMT_MONEY)   # Q Итого с НДС

    sum_s = ",".join(f"S{s}:S{e}" for s, e in data_ranges)
    _c(19, f'=IF(COUNT({sum_s})=0,"",SUM({sum_s}))', font=_dev_font(dev_total_py, bold=True, size=12), fmt=_FMT_MONEY)

    denom = "+".join(f"SUMPRODUCT((H{s}:H{e}>0)*H{s}:H{e}*F{s}:F{e})" for s, e in data_ranges)
    _c(18, f'=IFERROR(S{r}/({denom}),"")', font=_dev_font(dev_total_py, bold=True, size=12), fmt=_FMT_PCT)

    _c(20, None)  # T Коридор % — not aggregated at class level
    _c(21, comp_total, font=_dev_font(comp_total or 0, bold=True, size=12), fmt=_FMT_MONEY)

    ws.row_dimensions[r].height = 24
```

- [ ] **Step 4: Re-letter formulas in `_write_class_section`**

Update the month-header (`_hc`) and data-row (`_dc`) blocks. Month header weighted averages key on F (was D); base/vat blanks at H/I; material/delivery/other at J/K/L and N/O/P; totals at M/Q; dev at R/S; corridor/comp at T/U. Data rows write raw block (D raw_qty, E raw_unit) and calc block (F qty, G unit_symbol, H ref_price, I vat_rate, J/K/L static) then formulas M/N/O/P/Q/R/S.

Replace the month-header `_hc` calls:

```python
        _hc(6,  f"=SUM(F{s}:F{e})", fmt=_FMT_QTY)
        _hc(8,  None)   # H Базовая — not averaged
        _hc(9,  None)   # I Ставка НДС
        _hc(10, f'=IFERROR(SUMPRODUCT(J{s}:J{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(11, f'=IFERROR(SUMPRODUCT(K{s}:K{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(12, f'=IFERROR(SUMPRODUCT(L{s}:L{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(13, f"=J{rh}+K{rh}+L{rh}", fmt=_FMT_MONEY)
        _hc(14, f'=IFERROR(SUMPRODUCT(N{s}:N{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(15, f'=IFERROR(SUMPRODUCT(O{s}:O{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(16, f'=IFERROR(SUMPRODUCT(P{s}:P{e},F{s}:F{e})/SUM(F{s}:F{e}),"")', fmt=_FMT_MONEY)
        _hc(17, f"=N{rh}+O{rh}+P{rh}", fmt=_FMT_MONEY)
        _hc(19, f'=IF(COUNT(S{s}:S{e})=0,"",SUM(S{s}:S{e}))', font=_dev_font(month_dev, bold=True), fmt=_FMT_MONEY)
        _hc(18, f'=IFERROR(S{rh}/SUMPRODUCT((H{s}:H{e}>0)*H{s}:H{e}*F{s}:F{e}),"")',
            font=_dev_font(month_dev, bold=True), fmt=_FMT_PCT)
        month_key = (material_class_id, year, month)
        month_comp = (comp_by_class_month or {}).get(month_key, {})
        _corridor = month_comp.get("corridor_pct")
        _comp_amt = month_comp.get("compensation_amount")
        _hc(20, (_corridor / Decimal("100")) if _corridor is not None else None, fmt=_FMT_PCT_RATE)
        _hc(21, _comp_amt, font=_dev_font(_comp_amt or 0, bold=True), fmt=_FMT_MONEY)
```

Update the merged label range for the month header from columns 1–3 (unchanged — still A–C) and the blank fill cells loop `for _ci in (2, 3)` stays. (The month label still spans A–C.)

Replace the data-row static-value loop and formula block:

```python
            for col_idx, val in enumerate([
                r["invoice_date"],                # A 1
                _safe_str(r["invoice_number"]),   # B 2
                _safe_str(r["supplier_name"]),    # C 3
                r["raw_qty"],                     # D 4  Кол-во по документу
                _safe_str(r["raw_unit"]),         # E 5  Ед. изм. по документу
                r["qty"],                         # F 6  Расчётное кол-во
                _safe_str(r["unit_symbol"]),      # G 7  Базовая ед. изм.
                r["ref_price"],                   # H 8  Базовая цена
                r["vat_rate"],                    # I 9  Ставка НДС
                r["mat_per_m3_excl_vat"],         # J 10 Материал без НДС
                r["delivery_per_m3_excl_vat"],    # K 11 Доставка без НДС
                r["other_per_m3_excl_vat"],       # L 12 Прочее без НДС
            ], start=1):
                _, _, num_fmt, h_align = _COLUMNS[col_idx - 1]
                cell = ws.cell(row=cur, column=col_idx, value=val)
                cell.fill = row_fill
                cell.font = row_font
                cell.border = _BORDER
                cell.alignment = _align(h=h_align)
                if num_fmt:
                    cell.number_format = num_fmt

            n = cur

            def _dc(col_idx, value, font=None, fmt=_FMT_MONEY, _fill=row_fill, _font=row_font, _row=n):
                cell = ws.cell(row=_row, column=col_idx, value=value)
                cell.fill = _fill
                cell.font = font or _font
                cell.border = _BORDER
                cell.alignment = _align(h="right")
                if fmt:
                    cell.number_format = fmt

            _dc(13, f"=J{n}+K{n}+L{n}")               # M Итого без НДС
            _dc(14, f"=J{n}*(1+I{n})")                # N Материал с НДС
            _dc(15, f"=K{n}*(1+I{n})")                # O Доставка с НДС
            _dc(16, f"=L{n}*(1+I{n})")                # P Прочее с НДС
            _dc(17, f"=N{n}+O{n}+P{n}")               # Q Итого с НДС
            _dc(18, f'=IFERROR(IF(H{n}>0,(Q{n}-H{n})/H{n},""),"")',
                font=_dev_font(r["deviation_pct"]), fmt=_FMT_PCT)   # R Откл.%
            _dc(19, f'=IFERROR(IF(H{n}>0,(Q{n}-H{n})*F{n},""),"")',
                font=_dev_font(r["deviation_amount"]))             # S Откл.₽
```

- [ ] **Step 5: Run export tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_export.py -v 2>&1"`
Expected: PASS. The existing `test_export.py` likely asserts the file is generated and may check column headers/values — update any header-text or column-index assertions to the new layout. Add an assertion that the «Кол-во по документу» and «Расчётное кол-во» headers both exist.

- [ ] **Step 6: Add a normalized-export assertion**

Append to `backend/tests/integration/test_export.py`:

```python
def test_export_has_raw_and_calc_columns(client, factories, db_session):
    from openpyxl import load_workbook
    from io import BytesIO
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
    from models import UnitOfMeasure
    ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, unit_id=ton.id, price=60000,
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    # 2000 kg rebar
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class_id=mc.id, raw_unit="кг", quantity=2000,
        normalized_unit_id=ton.id, normalized_quantity=2, unit_price=60, normalized_unit_price=60000,
        amount=120000,
    )
    resp = client.get("/api/export/excel", params={"project_id": project.id})
    assert resp.status_code == 200
    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    all_text = {c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)}
    assert "Кол-во по документу" in all_text
    assert "Расчётное кол-во" in all_text
    assert "Базовая ед. изм." in all_text
```

Run again to confirm PASS.

- [ ] **Step 7: Commit**

```
git add backend/crud/calculations.py backend/routers/export.py backend/tests/integration/test_export.py
git commit -m "feat(units): Excel export — raw + normalized column blocks, normalized per-unit math"
```

---

# Milestone F — Final verification + docs

## Task F1: Full backend suite + lint

**Files:** none (verification).

- [ ] **Step 1: Run the auth-coverage guardian (new endpoints must require auth)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/test_auth_coverage.py -v 2>&1"`
Expected: PASS — `/api/units*` and `/api/material-types` are registered with `_auth_dep`, so unauthenticated calls return 401/403.

- [ ] **Step 2: Run the full backend test suite**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1"`
Expected: all PASS. Investigate and fix any remaining failures in older tests that referenced `material_type` strings or `unit` (e.g. `test_crud_recalculate.py`, `test_compensation.py`) — update them to seeded `material_type_code`/`raw_unit`/normalized fields.

- [ ] **Step 3: Lint**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint-backend 2>&1"`
Expected: clean (ruff). Fix any issues.

- [ ] **Step 4: Commit any test fixups**

```
git add -A
git commit -m "test(units): update remaining tests for normalized schema"
```

---

## Task F2: Documentation sync

Per `AGENTS.md`, the canonical detail docs live in `docs/agent/*`. Update the specific files and sections below; do not edit any `CLAUDE.md`. Then confirm `AGENTS.md`'s "где искать детали" map still holds (it does — same files, no new doc added).

**Files:**
- Modify: `docs/agent/database.md` — `# Модели БД и связи`, `## Точность Decimal по колонкам`
- Modify: `docs/agent/calculations.md` — `## Методология avg_price`, `## Экспорт Excel`, `## Коридор компенсации`
- Modify: `docs/agent/pdf-parsing.md` — material_type resolution note
- Modify: `docs/TECH_DEBT.md` — deferred backlog + legacy-alias cleanup

- [ ] **Step 1: `docs/agent/database.md`**

In `# Модели БД и связи`: add `UnitOfMeasure`, `UnitAlias`, `MaterialType`; change `MaterialClass` (`material_type` String → `material_type_id` FK → `material_types`, ON DELETE RESTRICT, indexed); change `InvoiceItem` (`unit` → `raw_unit` + `normalized_unit_id`/`normalized_quantity`/`normalized_unit_price`; `item_type` now `ck_item_type` enum); add `reference_prices.unit_id` (NOT NULL, base-unit only); note `compensation_corridors.material_type` → `material_type_id` FK. In `## Точность Decimal по колонкам`: add `units_of_measure.to_base_multiplier` NUMERIC(30,15), `invoice_items.normalized_quantity` NUMERIC(20,6), `normalized_unit_price` NUMERIC(24,6).

- [ ] **Step 2: `docs/agent/calculations.md`**

In `## Методология avg_price`: aggregation uses `SUM(normalized_quantity)` per-material_class (one class = one dimension); the dimension guard (class base-unit dimension vs ref-price unit dimension; intra-class mix → flagged) blocks `dimension_mismatch` rows from deviation/compensation; delivery distribution is mono-dimension → by `normalized_quantity`, mixed → by `amount`, with zero/unnormalized edges. In `## Экспорт Excel`: two column blocks (raw «Кол-во/Ед. изм. по документу» + calc «Расчётное кол-во/Базовая ед. изм.»), per-unit math on normalized qty, new 21-column A–U layout. In `## Коридор компенсации`: target column is now `material_type_id` (resolver keys by id); HTTP API stays code-based (`/corridors/type/{material_type}` maps code→id).

- [ ] **Step 3: `docs/agent/pdf-parsing.md`**

Note that the parser still returns raw `unit` + `material_type` code unchanged; `create_invoice` normalizes the unit at write-time, and `get_or_create_material_class` resolves the `material_type` code → `material_type_id` (unknown code → 422 / row left unclassified).

- [ ] **Step 4: `docs/TECH_DEBT.md`**

Add the spec §2 backlog items (density cross-dimension conversion, self-learning aliases, lazy reprocess endpoint, пог.м→т for rebar). Also add a tracked cleanup item: **drop the legacy `unit` output key and the `InvoiceItemEdit` `unit` input alias** (added in Task B3 for FE/BE transition compat) once the frontend plan has shipped and no client reads/writes `unit`.

- [ ] **Step 5: `docs/testing.md`**

`docs/testing.md` is a living document ("обновлять при каждом существенном изменении (добавил тесты…)"). Update it for the new backend tests added by this plan:
- Bump the TL;DR counts table (backend unit + integration file/test counts) to include `test_unit_normalization.py`, `test_dimension_guard.py`, `test_delivery_distribution.py`, `test_units_api.py`, `test_normalization_integration.py`, `test_calculations_with_units.py`, `test_reference_prices_unit.py`.
- In "что покрыто", note units normalization + dimension guard + delivery distribution + `/api/units`/`/api/material-types` + ref-price unit validation are now covered; remove `/calculations` from the gaps list if the new coverage closes it.

- [ ] **Step 6: Commit**

```
git add docs/agent docs/TECH_DEBT.md docs/testing.md
git commit -m "docs(units): sync agent docs + testing.md + tech-debt"
```

---

## Self-review reminders for the executor

- **Type/name consistency:** the corridor resolver's 4th positional arg is now `material_type_id` (int); `get_corridor_map` returns `by_type` keyed by `material_type_id`. The calculator passes `class_type_id_map.get(cid)`.
- **`item_type` enum:** assigning the raw string (`"material"`) is correct — `SqlEnum(ItemType, native_enum=False)` value == name.
- **Excel letters:** after inserting 3 columns the mapping is old→new: D→F (qty), E→H (base price), F→I (vat), G→J, H→K, I→L, J→M, K→N, L→O, M→P, N→Q, O→R, P→S, Q→T, R→U. Verify every formula references the new letters.
- **Migration runs in CI test DB** automatically (conftest upgrades to head); the smoke tests in `test_normalization_integration.py` confirm seed data.

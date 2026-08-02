# Float → Decimal Migration Implementation Plan (Spec 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all financial columns from `Float` to `Numeric`, make the Python calculation layer work end-to-end in `decimal.Decimal` with RU arithmetic rounding, and serialize Decimal→float at the JSON edge — eliminating floating-point drift and 1С reconciliation errors without changing business logic or the frontend.

**Architecture:** `Decimal` flows from DB (Numeric columns) through the calculation layer to the serialization edge. Defense against Decimal/float mixing is **boundary normalization** (no mypy): force Decimal at the write path (LLM→DB), API payloads, SQL literals, and SQL-row reads. A global `DecimalJSONResponse` converts Decimal→float for hand-built dict responses so the frontend contract (numbers) is unchanged.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy (sync) / Alembic / PostgreSQL (Neon) / pytest + factory_boy.

**Spec:** `docs/superpowers/specs/2026-06-04-decimal-migration-design.md`

**Commands (Windows):** prefer the Bash tool with the backend cwd. `just` commands run via Git bash:
`& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <command> 2>&1"`
Pytest directly: `cd backend && python -m pytest <path> -v`.

**Ordering rationale:** The model type flip (Task 2) makes the DB return Decimal, which breaks float arithmetic until the code is fixed. Therefore the code-layer fixes (Tasks 3–7) are landed in the **same logical sequence right after** the migration, and the full suite is only expected green at Task 8. Between Task 2 and Task 8, expect intermediate test failures — each task narrows them. Task 1 (the `money_round` helper) and Task 7b (serializer) are independent and could land first, so they do.

---

## File Structure

**Create:**
- `backend/finance.py` — `money_round` helper (pure, no deps). Alongside `security.py`.
- `backend/tests/unit/test_finance.py` — `money_round` unit tests.
- `backend/alembic/versions/2026_06_04_1200-c9d0e1f2a3b4_float_to_numeric.py` — type migration.

**Modify:**
- `backend/models.py` — 7 columns Float→Numeric.
- `backend/main.py` — `DecimalJSONResponse` + `default_response_class`.
- `backend/crud/documents.py` — write-path Decimal normalization.
- `backend/crud/calculations.py` — Decimal arithmetic + SQL literal casts + `money_round`.
- `backend/crud/suppliers.py` — Decimal arithmetic + SQL literal casts + `money_round`.
- `backend/routers/reference_prices.py` — `price` payload → Decimal.
- `backend/routers/projects.py` — `CorridorUpsert.corridor_pct` → Decimal.
- `backend/routers/export.py` — corridor `/ 100.0` → Decimal divisor.
- `backend/tests/unit/test_compensation.py` — expect Decimal returns + HALF_UP.

---

## Task 1: `money_round` helper (independent, lands first)

**Files:**
- Create: `backend/finance.py`
- Test: `backend/tests/unit/test_finance.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_finance.py`:

```python
from decimal import Decimal

import pytest

from finance import money_round


@pytest.mark.parametrize(
    "value, expected",
    [
        (Decimal("2.345"), Decimal("2.35")),   # HALF_UP: .5 → up (banker's would give 2.34)
        (Decimal("2.355"), Decimal("2.36")),   # HALF_UP
        (Decimal("2.344"), Decimal("2.34")),   # below half → down
        (Decimal("100.00"), Decimal("100.00")),
        (Decimal("-2.345"), Decimal("-2.34")),  # HALF_UP on negatives rounds toward +inf at .5
    ],
)
def test_money_round_half_up(value, expected):
    assert money_round(value) == expected


def test_money_round_accepts_float_via_str():
    # str() conversion avoids binary float imprecision: Decimal(0.1) != Decimal("0.1")
    assert money_round(0.1 + 0.2) == Decimal("0.30")


def test_money_round_accepts_str_and_int():
    assert money_round("5") == Decimal("5.00")
    assert money_round(5) == Decimal("5.00")


def test_money_round_places():
    assert money_round(Decimal("1.23456"), places=4) == Decimal("1.2346")
    assert money_round(Decimal("1.5"), places=0) == Decimal("2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_finance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finance'`.

- [ ] **Step 3: Create the helper**

Create `backend/finance.py`:

```python
from decimal import ROUND_HALF_UP, Decimal
from typing import Union


def money_round(value: Union[Decimal, float, str, int], places: int = 2) -> Decimal:
    """Округляет финансовые значения по правилам РФ (ROUND_HALF_UP, 0.5 → вверх).

    Арифметическое (не банковское) округление — для сверки с УПД/1С/ФНС.
    Принимает любой базовый тип; float конвертируется через str(), что отсекает
    бинарную микропогрешность (Decimal(0.1) != Decimal("0.1")).
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    exp = Decimal(f"0.{'0' * places}") if places > 0 else Decimal("1")
    return value.quantize(exp, rounding=ROUND_HALF_UP)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_finance.py -v`
Expected: PASS — all cases green.

- [ ] **Step 5: Commit**

```bash
git add backend/finance.py backend/tests/unit/test_finance.py
git commit -m "feat(finance): money_round helper (RU ROUND_HALF_UP)"
```

---

## Task 2: Model types + migration

**Files:**
- Modify: `backend/models.py`
- Create: `backend/alembic/versions/2026_06_04_1200-c9d0e1f2a3b4_float_to_numeric.py`

- [ ] **Step 1: Add `Numeric` to the models imports**

In `backend/models.py`, add `Numeric` to the existing `from sqlalchemy import (...)` block (it currently imports `Float`; keep `Float` only if still used elsewhere — after this task it is not, so remove it):

```python
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
```

(Remove `Float` from the import — verify with a grep that no other `Float` usage remains: `grep -n "Float" backend/models.py`.)

- [ ] **Step 2: Change the 7 columns**

In `backend/models.py`:

`ReferencePrice.price`:
```python
    price = Column(Numeric(19, 4), nullable=False)
```

`Invoice.vat_rate`:
```python
    vat_rate = Column(Numeric(5, 2), default=20.0)
```

`InvoiceItem` (4 columns):
```python
    quantity = Column(Numeric(15, 4), nullable=False)
    unit_price = Column(Numeric(19, 4), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    vat_amount = Column(Numeric(15, 2))
```

`CompensationCorridor.corridor_pct`:
```python
    corridor_pct = Column(Numeric(5, 2), nullable=False)  # 5.00 = ±5%
```

- [ ] **Step 3: Create the migration**

Confirm current head first: `cd backend && python -m alembic heads` (expected `b8c9d0e1f2a3`).

Create `backend/alembic/versions/2026_06_04_1200-c9d0e1f2a3b4_float_to_numeric.py`:

```python
"""float to numeric for financial columns

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-04 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, numeric_type, old_float)
_COLS = [
    ("reference_prices", "price", "NUMERIC(19,4)"),
    ("invoices", "vat_rate", "NUMERIC(5,2)"),
    ("invoice_items", "quantity", "NUMERIC(15,4)"),
    ("invoice_items", "unit_price", "NUMERIC(19,4)"),
    ("invoice_items", "amount", "NUMERIC(15,2)"),
    ("invoice_items", "vat_amount", "NUMERIC(15,2)"),
    ("compensation_corridors", "corridor_pct", "NUMERIC(5,2)"),
]


def upgrade() -> None:
    for table, col, ntype in _COLS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {col} TYPE {ntype} '
            f'USING {col}::numeric'
        )


def downgrade() -> None:
    for table, col, _ntype in _COLS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {col} TYPE DOUBLE PRECISION '
            f'USING {col}::double precision'
        )
```

- [ ] **Step 4: Apply the migration**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate 2>&1"`
Expected: upgrades to `c9d0e1f2a3b4` with no error.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/alembic/versions/2026_06_04_1200-c9d0e1f2a3b4_float_to_numeric.py
git commit -m "feat(model): migrate financial columns Float→Numeric"
```

(Suite is intentionally red after this until Task 8 — DB now returns Decimal into float arithmetic. Proceed.)

---

## Task 3: Write-path Decimal normalization (`crud/documents.py`)

**Files:**
- Modify: `backend/crud/documents.py`

LLM-parsed values arrive as Python `float`. Wrap them in `Decimal(str(...))` at write time so float imprecision never enters the Numeric columns (psycopg would otherwise binary-cast float→Decimal).

- [ ] **Step 1: Add a local Decimal helper + imports**

At the top of `backend/crud/documents.py`, add:

```python
from decimal import Decimal
```

And near the top of the module (after imports), add a small helper:

```python
def _dec(value) -> Decimal | None:
    """LLM/JSON float → Decimal через str() (отсекает бинарную погрешность). None-safe."""
    return None if value is None else Decimal(str(value))
```

- [ ] **Step 2: Normalize the Invoice + InvoiceItem writes**

In `create_invoice`, change the `Invoice(...)` construction so `vat_rate` is normalized:

```python
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
```

And the `InvoiceItem(...)` loop:

```python
    for item in items:
        db_item = InvoiceItem(
            invoice_id=invoice.id,
            raw_name=item["raw_name"],
            item_type=item["item_type"],
            material_class_id=item.get("material_class_id"),
            quantity=_dec(item["quantity"]),
            unit=item.get("unit"),
            unit_price=_dec(item["unit_price"]),
            amount=_dec(item["amount"]),
            vat_amount=_dec(item.get("vat_amount")),
        )
        db.add(db_item)
```

- [ ] **Step 3: Commit**

```bash
git add backend/crud/documents.py
git commit -m "feat(write): normalize LLM-parsed values to Decimal on invoice write"
```

---

## Task 4: SQL literal casts (`calculations.py` + `suppliers.py`)

**Files:**
- Modify: `backend/crud/calculations.py` (lines 164, 214, 237, 424, 474, 499)
- Modify: `backend/crud/suppliers.py` (lines 186, 245, 310, 355, 378, 457)

Replace `func.coalesce(Invoice.vat_rate, 20.0)` with a type-bound Decimal literal so the COALESCE stays NUMERIC (no float mixing inside the DB expression).

- [ ] **Step 1: Add imports to both files**

In `backend/crud/calculations.py`, ensure the top imports include `literal` and `Decimal`:

```python
from decimal import Decimal

from sqlalchemy import func, literal, or_
```

(It already imports `func, or_` — add `literal`. Add the `decimal` import.)

In `backend/crud/suppliers.py`, add the same:

```python
from decimal import Decimal

from sqlalchemy import ... , literal, ...   # add literal to the existing sqlalchemy import
```

(Verify the existing sqlalchemy import line in suppliers.py and append `literal`.)

- [ ] **Step 2: Replace the 6 sites in `calculations.py`**

At lines 164, 214, 237, 424, 474, 499, replace each:

```python
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, 20.0) / 100
```

with:

```python
                    InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
```

(The `/ 100` integer divisor is fine — NUMERIC / integer stays NUMERIC in PostgreSQL. Use `replace_all` carefully: the exact string differs by indentation per site, so edit each occurrence.)

- [ ] **Step 3: Replace the 6 sites in `suppliers.py`**

At lines 186, 245, 310, 355, 378, 457, apply the same replacement of
`func.coalesce(Invoice.vat_rate, 20.0)` → `func.coalesce(Invoice.vat_rate, literal(Decimal("20.0")))`.

- [ ] **Step 4: Verify no float literals remain in COALESCE**

Run: `grep -n "coalesce(Invoice.vat_rate, 20.0)" backend/crud/calculations.py backend/crud/suppliers.py`
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add backend/crud/calculations.py backend/crud/suppliers.py
git commit -m "feat(sql): type-bound Decimal literal in vat_rate COALESCE"
```

---

## Task 5: Decimal arithmetic in `compute_calculations` + `compute_export_rows`

**Files:**
- Modify: `backend/crud/calculations.py`

The aggregates now return `Decimal`. Stop coercing them to `float`; keep them Decimal through the arithmetic and round with `money_round`. The proportional `share` ratio must also be Decimal so `Decimal * share` does not raise.

- [ ] **Step 1: Import `money_round` and `Decimal`**

At the top of `backend/crud/calculations.py` add (Decimal added in Task 4; ensure `money_round`):

```python
from finance import money_round
```

- [ ] **Step 2: Make `_aggregate_by_class` Decimal-native**

Replace the body of `_aggregate_by_class` (lines 60-79) so accumulators and reads are Decimal:

```python
    class_contrib: dict[int, dict] = {}
    for row in base_rows:
        cid = row.material_class_id
        inv_id = row.invoice_id
        qty_base_in_inv = base_qty_per_invoice.get(inv_id, Decimal("0"))
        if qty_base_in_inv <= 0:
            continue
        share = row.qty / qty_base_in_inv   # Decimal / Decimal → Decimal
        shared = shared_per_invoice.get(inv_id, Decimal("0")) * share
        if cid not in class_contrib:
            class_contrib[cid] = {
                "mat_with_vat": Decimal("0"),
                "shared_with_vat": Decimal("0"),
                "qty": Decimal("0"),
                "invoice_ids": set(),
            }
        class_contrib[cid]["mat_with_vat"] += row.mat_total + row.mat_vat
        class_contrib[cid]["shared_with_vat"] += shared
        class_contrib[cid]["qty"] += row.qty
        class_contrib[cid]["invoice_ids"].add(inv_id)
```

Note: `base_qty_per_invoice` and `shared_per_invoice` are built elsewhere in this function from query rows — ensure those builders store Decimal (they read `func.sum(...)` results, already Decimal; remove any `float(...)` wrapping there). Grep within the function for `float(` and convert each to keep Decimal.

- [ ] **Step 3: Make the compute loop Decimal**

Replace the per-class compute block (lines 291-331) so arithmetic is Decimal and rounding uses `money_round`:

```python
        for cid, contrib in class_contrib.items():
            qty = contrib["qty"]
            if qty <= 0:
                continue
            # avg_price с НДС — корректно для сравнения с базовыми ценами (тоже с НДС)
            avg_price = (contrib["mat_with_vat"] + contrib["shared_with_vat"]) / qty

            ref = ref_by_class.get(cid)
            ref_price = ref.price if ref else None  # Decimal | None
            deviation_pct = None
            deviation_amount = None
            if ref_price and ref_price > 0:
                deviation_pct = money_round((avg_price - ref_price) / ref_price * 100, 2)
                deviation_amount = money_round((avg_price - ref_price) * qty, 2)

            corridor_pct = corridor_by_class.get(cid)
            compensation_per_unit = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
            compensation_amount = (
                money_round(compensation_per_unit * qty, 2)
                if compensation_per_unit is not None
                else None
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
                "invoice_count": len(contrib["invoice_ids"]),
                "reference_price": ref_price,
                "deviation_pct": deviation_pct,
                "deviation_amount": deviation_amount,
                "corridor_pct": corridor_pct,
                "compensation_per_unit": compensation_per_unit,
                "compensation_amount": compensation_amount,
            })
```

- [ ] **Step 4: Update `compute_compensation_per_unit` to be Decimal-pure**

Replace the helper (lines ~10-30) so literals are Decimal and it rounds with `money_round`:

```python
def compute_compensation_per_unit(
    avg_price,
    ref_price,
    corridor_pct,
):
    """Компенсация на единицу объёма (нелинейная). Все величины — Decimal.

    None если класс некомпенсируемый (corridor_pct is None) или нет базовой цены.
    0 если внутри коридора. Знак: + удорожание, − экономия.
    """
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
```

- [ ] **Step 5: Make `compute_export_rows` Decimal**

In the result-row builder (lines 552-610), remove `float(...)` wrappers and convert literals. Key changes:

```python
    rows: list[dict] = []
    for br in base_rows:
        inv_id = br.invoice_id
        cid = br.material_class_id
        qty = br.qty                      # Decimal
        if qty <= 0:
            continue

        total_base_qty = total_base_qty_per_inv.get(inv_id, Decimal("0"))
        share = qty / total_base_qty if total_base_qty > 0 else Decimal("0")

        mat_with_vat = br.mat_total + br.mat_vat
        delivery_alloc = delivery_per_inv.get(inv_id, Decimal("0")) * share
        additive_alloc = additive_per_inv.get(inv_id, Decimal("0")) * share
        delivery_excl_alloc = delivery_excl_per_inv.get(inv_id, Decimal("0")) * share
        additive_excl_alloc = additive_excl_per_inv.get(inv_id, Decimal("0")) * share

        mat_per_m3_excl_vat = br.mat_total / qty
        mat_per_m3 = mat_with_vat / qty
        delivery_per_m3_excl_vat = delivery_excl_alloc / qty
        delivery_per_m3 = delivery_alloc / qty
        other_per_m3_excl_vat = additive_excl_alloc / qty
        other_per_m3 = additive_alloc / qty
        total_per_m3 = mat_per_m3 + delivery_per_m3 + other_per_m3

        inv = invoice_map[inv_id]
        vat_rate_decimal = (inv.vat_rate if inv.vat_rate is not None else Decimal("20.0")) / Decimal("100")
        ref_price = _ref_price(cid, inv.date)
        deviation_pct = (
            money_round((total_per_m3 - ref_price) / ref_price * 100, 2)
            if ref_price and ref_price > 0
            else None
        )
        deviation_amount = (
            money_round((total_per_m3 - ref_price) * qty, 2)
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
            "qty": money_round(qty, 6),
            "ref_price": ref_price,
            "mat_per_m3_excl_vat": money_round(mat_per_m3_excl_vat, 6),
            "vat_rate": vat_rate_decimal,
            "mat_per_m3": money_round(mat_per_m3, 6),
            "delivery_per_m3_excl_vat": money_round(delivery_per_m3_excl_vat, 6),
            "delivery_per_m3": money_round(delivery_per_m3, 6),
            "other_per_m3_excl_vat": money_round(other_per_m3_excl_vat, 6),
            "other_per_m3": money_round(other_per_m3, 6),
            "total_per_m3": money_round(total_per_m3, 6),
            "deviation_pct": deviation_pct,
            "deviation_amount": deviation_amount,
        })
```

Also fix the dict-builders for `base_qty_per_invoice`, `delivery_per_inv`, etc. earlier in `compute_export_rows` (lines ~462, 486-487, 513-514, 556, 563, 569): remove `float(...)` wrappers so they store Decimal (they read `func.sum` results → already Decimal). Grep `float(` in the function and convert each.

- [ ] **Step 6: Grep for leftover float() in the file**

Run: `grep -n "float(" backend/crud/calculations.py`
Expected: only legitimate non-money uses (if any). Every `float(row.*)` / `float(br.*)` on a money/qty column must be gone.

- [ ] **Step 7: Run the compensation + calc-related tests**

Run: `cd backend && python -m pytest tests/unit/test_compensation.py tests/integration/test_compensation_corridors.py -v 2>&1 | tail -30`
Expected: compensation tests may fail on **type** (expecting float `5.0`, getting `Decimal("5.00")`) — that's fixed in Task 8. Integration tests that compare via `==` to ints/floats: Decimal compares equal to int/float of same value (`Decimal("5.00") == 5.0` is True), so those should pass. Triage: failures should be type-representation only, not value errors.

- [ ] **Step 8: Commit**

```bash
git add backend/crud/calculations.py
git commit -m "feat(calc): Decimal-native arithmetic with money_round in calculations"
```

---

## Task 6: Decimal arithmetic in `suppliers.py`

**Files:**
- Modify: `backend/crud/suppliers.py`

- [ ] **Step 1: Import money_round + Decimal**

Ensure top of `backend/crud/suppliers.py` has (Decimal added in Task 4):

```python
from finance import money_round
```

- [ ] **Step 2: Make `_compute_supplier_project_deviation` Decimal**

Convert the float wrappers and arithmetic. At the `base_qty_per_invoice` / `shared_per_invoice` builders (lines 343, 368, 393) and the deviation accumulation (lines 436-445):

```python
        base_qty_per_invoice[row.invoice_id] = row.total_qty   # was float(row.total_qty)
```

```python
        shared_per_invoice[row.invoice_id] = (
            shared_per_invoice.get(row.invoice_id, Decimal("0")) + row.total_with_vat
        )
```

(apply at both line 368 and 393 sites)

Deviation accumulation:

```python
        if ref and ref.price and ref.price > 0:
            total_deviation += (avg_price - ref.price) * qty
            reference_total += ref.price * qty
    ...
    deviation_amount = money_round(total_deviation, 2)
    deviation_pct = money_round(total_deviation / reference_total * 100, 2) if reference_total > 0 else None
```

Ensure `total_deviation` / `reference_total` are initialized to `Decimal("0")`, and `avg_price`/`qty` in that scope are Decimal (they derive from the Decimal builders above).

- [ ] **Step 3: Convert turnover/volume serialization reads**

The turnover/volume aggregates (lines 227, 272, 490-491, 537) currently do `float(turnover)` for the response dict. Since the `DecimalJSONResponse` (Task 7b) handles Decimal→float at the edge, these `float(...)` wrappers can stay (harmless) OR be removed for consistency. **Keep them** — removing is churn with no benefit, and explicit float in a known-serialized dict is clear. (Decision: leave the response-dict `float(...)` casts as-is; they are not arithmetic, just edge conversion.)

The similarity-score path (lines 150, 153, 176) operates on a `func.similarity` ratio, not money — leave as float.

- [ ] **Step 4: Grep for money-arithmetic float leftovers**

Run: `grep -n "float(" backend/crud/suppliers.py`
Expected: remaining `float(...)` are only (a) response-dict edge casts (turnover/volume/amount) and (b) the similarity-score path. No `float()` inside money arithmetic that feeds deviation/turnover sums.

- [ ] **Step 5: Run supplier tests**

Run: `cd backend && python -m pytest tests/integration/test_suppliers.py -v 2>&1 | tail -25`
Expected: PASS (Decimal == float/int comparisons hold; response dicts cast to float explicitly).

- [ ] **Step 6: Commit**

```bash
git add backend/crud/suppliers.py
git commit -m "feat(suppliers): Decimal arithmetic with money_round in supplier aggregates"
```

---

## Task 7: API payloads + serialization edge

**Files:**
- Modify: `backend/routers/reference_prices.py`, `backend/routers/projects.py`, `backend/routers/export.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Payload schemas → Decimal**

In `backend/routers/reference_prices.py`, change `price` in both `ReferencePriceCreate` and `ReferencePriceUpdate`:

```python
from decimal import Decimal
...
    price: Decimal
```

(For `ReferencePriceUpdate.price` keep it `Decimal | None = None` and keep the existing `not_null` validator behavior.)

In `backend/routers/projects.py`, change `CorridorUpsert`:

```python
from decimal import Decimal
...
class CorridorUpsert(BaseModel):
    corridor_pct: Decimal = Field(ge=0, le=100)
```

- [ ] **Step 2: Fix export corridor divisor**

In `backend/routers/export.py` line 280, the `_corridor / 100.0` divides a Decimal (corridor_pct from compute_calculations) by a float → TypeError. Change to:

```python
from decimal import Decimal
...
        _hc(17, (_corridor / Decimal("100")) if _corridor is not None else None, fmt=_FMT_PCT_RATE)
```

(`corridor_pct` is now Decimal; openpyxl accepts Decimal cell values.)

- [ ] **Step 3: Add `DecimalJSONResponse` to `main.py`**

In `backend/main.py`, after the existing imports, add:

```python
import json
from decimal import Decimal
from typing import Any


def _decimal_encoder(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class DecimalJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), default=_decimal_encoder,
        ).encode("utf-8")
```

And set it as the default response class:

```python
app = FastAPI(
    title="УПД Трекер цен",
    version="2.0.0",
    lifespan=lifespan,
    default_response_class=DecimalJSONResponse,
)
```

- [ ] **Step 4: Commit**

```bash
git add backend/routers/reference_prices.py backend/routers/projects.py backend/routers/export.py backend/main.py
git commit -m "feat(api): Decimal payloads + global Decimal→float JSON response"
```

---

## Task 8: Update tests for Decimal + HALF_UP, full verification

**Files:**
- Modify: `backend/tests/unit/test_compensation.py`

- [ ] **Step 1: Update compensation unit tests to expect Decimal**

`compute_compensation_per_unit` now returns `Decimal`. Update `backend/tests/unit/test_compensation.py` so expectations are Decimal and inputs are Decimal (matching production, where these arrive as Decimal):

```python
from decimal import Decimal

import pytest

from crud.calculations import compute_compensation_per_unit

D = Decimal


@pytest.mark.parametrize(
    "avg_price, ref_price, corridor_pct, expected",
    [
        (D("110"), D("100"), D("5"), D("5.00")),    # overrun beyond corridor
        (D("90"), D("100"), D("5"), D("-5.00")),    # saving beyond corridor
        (D("103"), D("100"), D("5"), D("0")),       # inside [95;105]
        (D("105"), D("100"), D("5"), D("0")),       # exactly upper boundary
        (D("95"), D("100"), D("5"), D("0")),        # exactly lower boundary
        (D("110"), D("100"), D("0"), D("10.00")),   # corridor 0% → == deviation
        (D("90"), D("100"), D("0"), D("-10.00")),
    ],
)
def test_compensation_per_unit(avg_price, ref_price, corridor_pct, expected):
    assert compute_compensation_per_unit(avg_price, ref_price, corridor_pct) == expected


def test_compensation_none_when_no_ref_price():
    assert compute_compensation_per_unit(D("110"), None, D("5")) is None
    assert compute_compensation_per_unit(D("110"), D("0"), D("5")) is None


def test_compensation_none_when_corridor_not_set():
    assert compute_compensation_per_unit(D("110"), D("100"), None) is None
```

- [ ] **Step 2: Run unit tests**

Run: `cd backend && python -m pytest tests/unit/ -v 2>&1 | tail -25`
Expected: PASS — finance, compensation, and existing unit tests green.

- [ ] **Step 3: Run the full backend suite**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1 | tail -30"`
Expected: PASS except the known pre-existing failure `test_dashboard.py::test_monthly_summary_aggregates_by_month` (documented as failing before this work — a VAT assertion `48000 vs 51600`, unrelated to Decimal). Every other test green. If new failures appear, triage: most likely a leftover float literal in an arithmetic path or a missed `float(row.*)` — grep and fix.

- [ ] **Step 4: Verify the export still builds (Decimal cells)**

Run: `cd backend && python -m pytest tests/integration/test_export.py -v 2>&1 | tail -20`
Expected: PASS — openpyxl writes Decimal cells; formulas and the 18-column layout unchanged.

- [ ] **Step 5: Lint**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1 | tail -15"`
Expected: ruff clean. Fix import-order / unused (e.g. removed `Float`, unused `round`).

- [ ] **Step 6: Commit**

```bash
git add backend/tests/unit/test_compensation.py
git commit -m "test: expect Decimal returns + HALF_UP in compensation tests"
```

---

## Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

In the tech-stack / methodology sections, document:
- Financial columns are `Numeric` (precision/scale table); calculation layer works in `Decimal` end-to-end.
- `backend/finance.py::money_round` — RU arithmetic rounding (ROUND_HALF_UP), used everywhere instead of `round()` in money math.
- Boundary normalization: write-path (`crud/documents.py` `_dec`), API payloads (Decimal), SQL `literal(Decimal("20.0"))` in `COALESCE(vat_rate, …)`, row reads stay Decimal.
- `DecimalJSONResponse` in `main.py` (`default_response_class`) converts Decimal→float at the JSON edge; frontend contract unchanged (numbers).
- Update the existing **VAT guard** note: the COALESCE fallback is now `literal(Decimal("20.0"))`, not `20.0`.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document Decimal migration in CLAUDE.md"
```

---

## Self-Review notes (addressed during planning)

- **Spec coverage:** Task 1 (Task 2 spec — money_round), Task 2 (Task 1 spec — models+migration), Task 3 (Task 3a spec — write path), Task 4 (Task 3c spec — SQL literals), Tasks 5–6 (Task 3d spec — Decimal arithmetic + money_round in compute_*), Task 7 (Task 3b + Task 4 spec — payloads + serializer + export divisor), Task 8 (Task 5 spec — tests). All spec tasks mapped.
- **`float()` retention decision:** response-dict edge casts in `suppliers.py` (turnover/volume/amount) and the similarity-score path are intentionally **kept** as float — they are not money arithmetic and the serializer handles Decimal anyway. Documented in Task 6 Step 3 to prevent a future reviewer "fixing" them.
- **Decimal == float/int:** Python `Decimal("5.00") == 5.0` and `== 5` are True, so integration tests comparing API JSON (float, post-serializer) to expected numbers stay valid without rewrites.
- **Known pre-existing failure:** `test_monthly_summary_aggregates_by_month` fails before and after (unrelated VAT assertion) — Task 8 Step 3 calls it out so the implementer doesn't chase it.
- **Type consistency:** `money_round(value, places=2) -> Decimal` signature identical across finance.py (Task 1), calculations.py (Task 5), suppliers.py (Task 6). `compute_compensation_per_unit` returns Decimal everywhere (Task 5 def, Task 8 tests).
- **Ordering caveat:** suite is red between Task 2 and Task 8 by design — stated in the header and at Task 2 Step 5.
- **Flag for implementer:** confirm alembic head is `b8c9d0e1f2a3` before Task 2 Step 3; if not, update `down_revision`.

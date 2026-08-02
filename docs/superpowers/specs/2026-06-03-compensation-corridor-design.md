# Compensation Corridor — Design Spec

**Date:** 2026-06-03
**Status:** Approved, ready for implementation planning

## Problem

The app currently computes **deviation** — how far the actual average price has moved
from the reference (base) price. Deviation is pure analytics: it reports the full gap
regardless of size.

Construction contracts, however, define a **compensation corridor** (коридор компенсации):
a tolerance band around the base price within which neither party pays. Only the portion
of the price change *beyond* the corridor is compensated — the customer pays the supplier
on overruns, the customer is reimbursed on savings. This is the actual money settled under
the contract, and it differs from deviation by the dead zone inside the corridor.

This spec adds compensation calculation on top of the existing deviation machinery.

## Domain rules (from the requirement)

- The corridor is a percentage, **specific per material class** (concrete 5%, rebar 7%, …),
  but **configured at the project (contract) level**. It is a `(project × material_class) → %`
  binding.
- The corridor applies for the **entire contract duration** — it is **not periodic**
  (unlike reference prices, which have `period_start`/`period_end`).
- The corridor works **both directions**: it compensates both price increases (удорожание)
  and savings on price decreases (экономия при удешевлении).
- Some materials are compensated (have a corridor), others are not — historically split into
  two contract appendices ("компенсируемые материалы с коридором" / "материалы без коридора").

### Formula (per unit of volume)

Let `P` = actual average price (avg_price, VAT-inclusive), `B` = reference price,
`k` = corridor as a fraction (5% → 0.05):

```
P > B*(1+k):   compensation = P − B*(1+k)     (> 0, overrun beyond corridor)
P < B*(1−k):   compensation = P − B*(1−k)     (< 0, saving beyond corridor)
otherwise:     compensation = 0               (inside the corridor)
```

Per volume: `compensation_amount = compensation_per_unit × qty`.

Sign convention (same as deviation): **`+` = удорожание** (customer pays supplier),
**`−` = экономия** (reimbursed to customer). Red for `+`, green for `−` in UI/Excel.

Worked examples (B=100, k=0.05):

| P   | computation        | result    |
|-----|--------------------|-----------|
| 110 | 110 − 100×1.05     | **+5 ₽**  |
| 90  | 90 − 100×0.95      | **−5 ₽**  |
| 103 | inside [95;105]    | **0 ₽**   |

### Three states per material class (semantics of corridor_pct)

| State                      | Meaning                                                    |
|----------------------------|------------------------------------------------------------|
| **no row**                 | non-compensated class — compensation is **not computed**   |
| **row with `corridor_pct = 0`** | compensated, no dead zone — compensation == deviation |
| **row with `corridor_pct = X`** | compensated with ±X% tolerance band                   |

The `0%` case makes the feature continuous: the current deviation behavior is exactly
"compensation at a 0% corridor". A larger corridor introduces the dead zone.

## Nonlinearity → monthly computation

Compensation is **nonlinear** (zero inside the band, a shifted line beyond it). Therefore,
unlike the current deviation, it **cannot be computed per invoice row and summed** —
`compensation(monthly average) ≠ Σ compensation(per row)`. It MUST be computed from the
**monthly average price**, which is exactly the granularity `compute_calculations` already
produces (one row per `class × month`). Monthly-level computation is both the methodologically
correct approach and the explicit requirement ("компенсацию в эксель отчете надо считать
помесячно").

## Architecture decision

**Compensation lives in `compute_calculations`** — the single source of truth for price
analytics (per CLAUDE.md). It already emits monthly `class × month` rows with `avg_price`
and `reference_price` — the exact level compensation needs. We extend it additively: load
the project's corridors once, add three fields per row. The dashboard router and Excel export
get the values for free, with one formula implementation (no FE/BE duplication of a nonlinear
formula).

Rejected alternatives: a separate `compute_compensations` wrapper (a layer for its own sake);
computing on the frontend (would force a second Python implementation for Excel, risking drift).

## Data model

New table — a `(project × material_class)` binding. No period (corridor is non-periodic).

```python
class CompensationCorridor(Base):
    __tablename__ = "compensation_corridors"

    project_id        = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    material_class_id = Column(Integer, ForeignKey("material_classes.id", ondelete="CASCADE"), primary_key=True)
    corridor_pct      = Column(Float, nullable=False)   # 5.0 = ±5%; stored as percent, not fraction
    created_at        = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
    updated_at        = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"),
                               onupdate=lambda: datetime.now(UTC).replace(tzinfo=None))  # naive UTC, per CLAUDE.md
```

Decisions:
- **Composite PK** `(project_id, material_class_id)` — one corridor per class per project,
  idempotent upsert. Same pattern as `ProjectSupplierExclusion`.
- **`corridor_pct` stored as a percent** (5.0, not 0.05) — matches how the user enters/sees it;
  the formula divides by 100.
- **Range `0 <= corridor_pct <= 100`** — 0 is allowed (compensated class with no dead zone).
- **`ondelete=CASCADE`** on both FKs — deleting a project/class cleans up corridors.

Alembic migration: create table + FK indexes.

## Computation (`compute_calculations` extension)

Load corridors once per call (like `ref_rows`):

```python
corridor_by_class: dict[int, float] = {
    c.material_class_id: c.corridor_pct
    for c in db.query(CompensationCorridor)
              .filter(CompensationCorridor.project_id == project_id)
              .all()
}
```

Per `class × month` row, after `avg_price` and `ref_price` are known:

```python
corridor_pct = corridor_by_class.get(cid)          # None → non-compensated class
compensation_per_unit = None
compensation_amount = None
if corridor_pct is not None and ref_price and ref_price > 0:
    k = corridor_pct / 100.0
    upper = ref_price * (1 + k)
    lower = ref_price * (1 - k)
    if avg_price > upper:
        comp = avg_price - upper
    elif avg_price < lower:
        comp = avg_price - lower
    else:
        comp = 0.0
    compensation_per_unit = round(comp, 2)
    compensation_amount   = round(comp * qty, 2)
```

Three new fields added to each result dict: `corridor_pct`, `compensation_per_unit`,
`compensation_amount`. Existing fields are untouched.

Edge cases:
- **No reference price** (`ref_price is None`) → compensation `None` (can't measure without a base;
  consistent with current deviation behavior).
- **Non-compensated class** (no corridor row) → all three fields `None`.
- **`None` vs `0.0`**: `None` = "not applicable / cannot compute"; `0.0` = "computed, fell inside
  the corridor". The distinction matters for aggregation and for UI display (dash vs "0 ₽").

`compute_full_deviation` is **not** modified (it is about deviation). A future
`compute_full_compensation` aggregate can be added when the dashboard needs a project-wide
compensation total — out of scope for this version (compensation surfaces only on the project
screen + Excel).

## API

### CRUD module — `backend/crud/compensation_corridors.py`

Modeled on `supplier_exclusions.py`:

```python
get_corridors(db, project_id) -> list[CompensationCorridor]      # for the tab (join names in router)
get_corridor_map(db, project_id) -> dict[int, float]             # {class_id: pct} for computation
set_corridor(db, project_id, material_class_id, corridor_pct)    # upsert, idempotent
delete_corridor(db, project_id, material_class_id) -> bool       # remove compensability
```

`set_corridor` uses `INSERT ... ON CONFLICT (project_id, material_class_id) DO UPDATE`
(idempotent via composite PK), same shape as `get_or_create_supplier`.

### Router — `backend/routers/compensation_corridors.py`

Mounted under the project prefix (like supplier-exclusions):

```
GET    /api/projects/{id}/compensation-corridors
       → [{ material_class_id, material_class_name, material_type, corridor_pct }]
PUT    /api/projects/{id}/compensation-corridors/{material_class_id}
       body: { corridor_pct: float }   → 200; validates 0 <= pct <= 100 (else 422)
DELETE /api/projects/{id}/compensation-corridors/{material_class_id}
       → 204; remove the class from compensation (idempotent)
```

Decisions:
- **`PUT` for upsert** (not POST) — idempotent by `(project, class)`; "put a value at this key".
- **`GET` joins `MaterialClass`** for human-readable names (like reference_prices).
- The list of project material classes for the tab's selector is already fetchable by the FE —
  no extra endpoint.

### Existing routers — expose new fields

- `GET /dashboard/calculations` — add `corridor_pct`, `compensation_per_unit`,
  `compensation_amount` to the serialized rows (additive; serialization block at
  `dashboard.py` lines 176–192).

## Excel export

Compensation is nonlinear → computed **from the monthly average** on the Python side, placed as
a ready number at the **monthly-subtotal and class-grand-total** levels. It is **not** present on
individual invoice rows (undefined there methodologically).

Current `compute_export_rows` returns per-invoice rows; monthly averages are Excel SUMPRODUCT
formulas. Compensation cannot be a formula over per-row cells (nonlinear). Solution: in `export.py`,
additionally call `compute_calculations` (monthly) alongside `compute_export_rows` (per-invoice),
build a map `{(material_class_id, year, month): {corridor_pct, compensation_amount}}`, and read the
ready compensation when rendering the monthly subtotal. The class total = Python sum of monthly
compensations (not a formula — same nonlinearity reason).

New columns (2), appended after "Откл. ₽":

```
Q 17  Коридор, %       — corridor_pct (static; empty for non-compensated classes)
R 18  Компенсация, ₽   — ready number from Python (month/total); blank on per-invoice rows
```

Decisions and edge cases:
- Column **R is filled only on monthly subtotals and the class grand total**; blank on individual
  invoice rows. A footnote in the header explains why.
- **Class total** = sum of monthly compensations (Python sum).
- **Non-compensated class** (no corridor) → Q and R empty across the whole class block; the class is
  still shown (it has a deviation).
- **Color** via the existing `_dev_font` logic (red `+` overrun / green `−` saving).
- The "corridor boundary" column was considered and dropped (avoid extra columns).

## Frontend

### New "Коридоры" tab in `ProjectPage`

- New `TabsTrigger value="corridors"` next to "Базовые цены".
- Component `CorridorsTab.tsx` in `components/projects/`. A table of the project's material classes
  with an editable "Коридор, %" field:
  - class **with corridor** → shows % + "изменить" / "снять компенсацию" (DELETE);
  - class **without corridor** → "Сделать компенсируемым" button (inline % input → PUT);
  - styled like the reference-prices tab / `InvoiceTable`.
- Inline % entry with Enter/Escape (same pattern as the supplier-exclusions tab).
- **DELETE without a confirmation dialog** — a single non-destructive number, trivially restorable.

### Compensation in the calculations tab (screen)

- The calculations tab renders rows from `/dashboard/calculations`. Add a "Компенсация" column next
  to "Отклонение" (`compensation_amount`).
- Color/sign via the same helpers as deviation (red `+` / green `−`).
- Non-compensated class → dash "—"; inside the corridor → "0 ₽".

### Data layer (TanStack Query)

- `services/queries.ts`: `useCompensationCorridors(projectId)`, `useSetCorridor()`,
  `useDeleteCorridor()` — invalidate the corridors key **and** `calculations` (changing a %
  changes the computation).
- `services/queryKeys.ts`: `compensationCorridors(projectId)` key.
- `services/api/`: methods for the new endpoints.
- `types/`: new `compensationCorridor.ts` + extend the calculations-row type with the three fields.

## Testing

### Backend
- **Unit** (`tests/unit/test_compensation.py`, no DB): the formula across all branches, isolated
  from SQL. Cases: P=110/B=100/k=5%→+5; P=90→−5; P=103→0; k=0% → compensation == deviation
  (continuity); `ref_price=None`→`None`; non-compensated class→`None`; exact boundary
  (P==B*(1+k))→0.
- **Integration** (`tests/integration/`): `compute_calculations` with real corridors via factories —
  a compensated and a non-compensated class in one project, monthly breakdown, verify
  `compensation_amount` sums correctly across months.
- **CRUD**: `set_corridor` idempotency (repeated PUT overwrites, no duplicate); `delete` idempotency.
- **Auth coverage**: new routes are picked up automatically by `test_auth_coverage.py`
  (hits every route without a token → expects 401/403).
- **Factory**: `CompensationCorridorFactory` in `tests/factories.py`.

### Frontend (MSW v2)
- Handlers for the 3 new endpoints in `handlers.ts`.
- `CorridorsTab.test.tsx`: render compensated vs non-compensated rows; PUT on % entry;
  DELETE removes compensability (no dialog).
- A test for the compensation column rendering in the calculations tab.

## Out of scope (this version)

- Compensation on the dashboard (only the project screen + Excel for now).
- Project-wide compensation aggregate (`compute_full_compensation`).
- Periodic corridors (corridor is contract-lifetime, non-periodic by requirement).

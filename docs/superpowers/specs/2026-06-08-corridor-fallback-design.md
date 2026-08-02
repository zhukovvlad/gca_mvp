# Spec 2: Corridor Fallback & Per-Project Compensability

**Date:** 2026-06-08
**Status:** Approved
**Depends on:** Spec 1 (Float -> Decimal migration) — shipped

---

## Context

Current `CompensationCorridor` is bound to `(project_id, material_class_id)`, forcing granular per-class setup (e.g. every concrete grade individually). Compensability is implicit — determined by row presence.

**Goal:** Introduce a Fallback pattern (type-level defaults with class-level overrides) and explicit per-project `is_compensable` flag, unified in a single table.

---

## Design Decisions (from brainstorm)

| Question | Decision |
|---|---|
| `is_compensable=false` semantics | Hard block at the resolved level; class-level override can flip type-level in either direction |
| Type `other` for fallback | Treated equally — type-level corridors work for all material types |
| `is_compensable` scope | Per-project (not global on MaterialClass) |
| Default (no row) | Not compensable (whitelist pattern) |
| Bulk type disable | Type-level flag `is_compensable=false` — one row disables all classes of that type |
| Override direction | Class-level always wins over type-level (both true→false and false→true) |
| Storage approach | Variant A — single table with `is_compensable` + `corridor_pct` |
| UI layout | Single table grouped by material type |
| Migration | DROP old + CREATE new (clean slate, data re-entered via new UI) |

---

## 1. Data Model

### CompensationCorridor (restructured)

```python
class CompensationCorridor(Base):
    __tablename__ = "compensation_corridors"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # Hierarchy (exactly one must be set)
    material_type = Column(String, nullable=True)
    material_class_id = Column(Integer, ForeignKey("material_classes.id", ondelete="CASCADE"), nullable=True)

    # Commercial terms
    is_compensable = Column(Boolean, nullable=False, default=False)
    corridor_pct = Column(Numeric(5, 2), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Exactly one target filled
        CheckConstraint(
            "(material_type IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive"
        ),
        # If compensable, corridor_pct is mandatory
        CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable"
        ),
        # Partial unique indexes
        Index('uq_corridor_project_type', 'project_id', 'material_type',
              unique=True, postgresql_where=text("material_class_id IS NULL")),
        Index('uq_corridor_project_class', 'project_id', 'material_class_id',
              unique=True, postgresql_where=text("material_type IS NULL")),
    )
```

### Semantics

| Situation | Meaning |
|---|---|
| No row for class AND no row for type | Not compensable (whitelist default) |
| Type-level row with `is_compensable=false` | All classes of this type disabled (unless class-level override) |
| Type-level row with `is_compensable=true, corridor_pct=X` | All classes inherit X% (unless class-level override) |
| Class-level row with `is_compensable=false` | This specific class disabled (overrides type) |
| Class-level row with `is_compensable=true, corridor_pct=Y` | This class uses Y% (overrides type) |

### Fallback resolution order

```
class-level row found? → use it (is_compensable + corridor_pct)
         ↓ no
type-level row found?  → use it (is_compensable + corridor_pct)
         ↓ no
no row                 → not compensable (default)
```

Class-level always wins. Override works in both directions (true→false, false→true).

---

## 2. API

Base path: `/api/projects/{project_id}/corridors`

Old endpoints (`/compensation-corridors/*`) are removed — no backward compatibility needed.

### Endpoints

| Method | Path | Body | Description |
|---|---|---|---|
| `GET` | `/corridors` | — | Resolved matrix: all material classes with resolved status |
| `PUT` | `/corridors/type/{material_type}` | `CorridorUpsert` | Upsert type-level rule |
| `DELETE` | `/corridors/type/{material_type}` | — | Remove type-level rule (204) |
| `PUT` | `/corridors/class/{material_class_id}` | `CorridorUpsert` | Upsert class-level override |
| `DELETE` | `/corridors/class/{material_class_id}` | — | Remove class-level override (204) |

### Request schema

```python
class CorridorUpsert(BaseModel):
    is_compensable: bool
    corridor_pct: Decimal | None = None

    @model_validator(mode='after')
    def validate_pct_logic(self) -> 'CorridorUpsert':
        if self.is_compensable and self.corridor_pct is None:
            raise ValueError("corridor_pct обязателен, если is_compensable=True")
        if not self.is_compensable:
            self.corridor_pct = None  # Clear garbage from client
        return self
```

### GET response (resolved matrix)

Backend JOINs `material_classes` with `compensation_corridors` and returns the full resolved state for every class in the project:

```json
{
  "types": [
    {
      "material_type": "concrete",
      "is_compensable": true,
      "corridor_pct": 5.00,
      "has_rule": true
    },
    {
      "material_type": "rebar",
      "is_compensable": false,
      "corridor_pct": null,
      "has_rule": true
    },
    {
      "material_type": "other",
      "is_compensable": null,
      "corridor_pct": null,
      "has_rule": false
    }
  ],
  "classes": [
    {
      "material_class_id": 1,
      "material_class_name": "В15",
      "material_type": "concrete",
      "is_compensable": true,
      "corridor_pct": 5.00,
      "level": "type",
      "has_override": false
    },
    {
      "material_class_id": 3,
      "material_class_name": "В40",
      "material_type": "concrete",
      "is_compensable": true,
      "corridor_pct": 7.00,
      "level": "class",
      "has_override": true
    },
    {
      "material_class_id": 5,
      "material_class_name": "d12",
      "material_type": "rebar",
      "is_compensable": true,
      "corridor_pct": 3.00,
      "level": "class",
      "has_override": true
    },
    {
      "material_class_id": 6,
      "material_class_name": "d16",
      "material_type": "rebar",
      "is_compensable": false,
      "corridor_pct": null,
      "level": "type",
      "has_override": false
    }
  ]
}
```

- `level`: `"type"` (inherited from type rule) or `"class"` (own override)
- `has_override`: whether a class-level row exists (enables `[×]` button in UI)
- `has_rule`: whether a type-level row exists in DB
- For types: `is_compensable=null` + `has_rule=false` means "not configured" (distinct from `is_compensable=false` + `has_rule=true` meaning "explicitly disabled")

### Upsert implementation note

Partial unique indexes (`uq_corridor_project_type`, `uq_corridor_project_class`) require PostgreSQL-native upsert — `session.merge()` won't work correctly as it resolves conflicts by PK only.

Use `sqlalchemy.dialects.postgresql.insert` with explicit `index_where`:

```python
from sqlalchemy.dialects.postgresql import insert

# Example: type-level upsert
stmt = insert(CompensationCorridor).values(
    project_id=project_id,
    material_type=target_type,
    is_compensable=payload.is_compensable,
    corridor_pct=payload.corridor_pct
)
stmt = stmt.on_conflict_do_update(
    index_elements=['project_id', 'material_type'],
    index_where=(CompensationCorridor.material_class_id.is_(None)),
    set_={
        'is_compensable': stmt.excluded.is_compensable,
        'corridor_pct': stmt.excluded.corridor_pct,
        'updated_at': func.now()
    }
)
db.execute(stmt)
```

This guarantees atomicity (race-safe if two users save settings for the same type simultaneously) and avoids a preliminary SELECT.

### DELETE behavior

- `DELETE .../type/{type}` — removes type-level rule. All classes without own override become "not configured" (default = not compensable).
- `DELETE .../class/{class_id}` — removes class-level override. Class falls back to type-level rule (or default).
- Both idempotent: 204 whether row existed or not.

---

## 3. Business Logic (Calculation Layer)

### Batch corridor resolution

```python
def get_corridor_map(db, project_id):
    """Single query → two dicts for Python-side resolution."""
    rows = db.query(CompensationCorridor).filter_by(project_id=project_id).all()
    by_class = {r.material_class_id: r for r in rows if r.material_class_id}
    by_type  = {r.material_type: r for r in rows if r.material_type}
    return by_class, by_type

def resolve_corridor(by_class, by_type, class_id, material_type):
    """class → type → None (not compensable)."""
    row = by_class.get(class_id) or by_type.get(material_type)
    if row is None:
        return None, None          # no row → default → not compensable
    if not row.is_compensable:
        return False, None         # explicitly disabled
    return True, row.corridor_pct  # enabled, pct guaranteed by CheckConstraint
```

### Integration in compute_calculations

```python
by_class, by_type = get_corridor_map(db, project_id)

for cid in class_ids:
    compensable, corridor_pct = resolve_corridor(
        by_class, by_type, cid, class_material_type[cid]
    )
    if not compensable:
        # compensation_per_unit = None, compensation_amount = None
        continue
    comp = compute_compensation_per_unit(avg_price, ref_price, corridor_pct)
    # ... rest of calculation
```

### Return value semantics

| Situation | `compensation_per_unit` | `compensation_amount` |
|---|---|---|
| No row (default) | `None` | `None` |
| `is_compensable=false` | `None` | `None` |
| Inside corridor | `Decimal("0")` | `Decimal("0")` |
| Outside corridor | `Decimal(...)` | `Decimal(...)` |

Calculation layer returns `None` for both "not configured" and "explicitly disabled" — **no UI status leakage**. Frontend resolves the distinction via GET `/corridors` resolved matrix when needed.

### compute_compensation_per_unit (unchanged)

The pure function remains the same — it receives `corridor_pct` only when compensable. Signature: `compute_compensation_per_unit(avg_price, ref_price, corridor_pct) → Decimal | None`.

---

## 4. Migration

Single Alembic migration:

### upgrade

1. `DROP TABLE compensation_corridors` (old composite PK structure)
2. `CREATE TABLE compensation_corridors` with new schema:
   - Surrogate `id` PK
   - `project_id`, `material_type`, `material_class_id`, `is_compensable`, `corridor_pct`
   - `created_at`, `updated_at`
   - 3 constraints: `chk_corridor_target_exclusive`, `chk_corridor_pct_required_if_compensable`, plus 2 partial unique indexes

Table starts empty → all classes default to not compensable → users configure via new UI.

### downgrade

Explicit reverse: DROP new table, CREATE old structure with composite PK `(project_id, material_class_id)` and `corridor_pct Numeric(5,2) NOT NULL`. Data is lost on rollback (acceptable — schema consistency for old code is the goal).

---

## 5. Frontend (CorridorsTab)

### UI structure — single table grouped by material type

```
┌──────────────────────────────────────────────────────────┐
│ Бетон                          [toggle ON] Коридор: 5%   │  ← type-level
├──────────────────────────────────────────────────────────┤
│  В15    ✓ компенсируется   5.00%  (наследовано)          │
│  В25    ✓ компенсируется   5.00%  (наследовано)          │
│  В40    ✓ компенсируется   7.00%  [своё]  [×]           │  ← class override
│  В50    ✗ выключено         —     [своё]  [×]           │  ← class override false
├──────────────────────────────────────────────────────────┤
│ Арматура                       [toggle OFF]              │  ← type-level false
├──────────────────────────────────────────────────────────┤
│  d12    ✓ компенсируется   3.00%  [своё]  [×]           │  ← class override true
│  d16    ✗ выключено         —     (наследовано)          │
├──────────────────────────────────────────────────────────┤
│ Прочее                         [не настроено]            │  ← no row
├──────────────────────────────────────────────────────────┤
│  Песок   — не настроено                                  │
│  Щебень  — не настроено                                  │
└──────────────────────────────────────────────────────────┘
```

### Behavior

- **Type header**: toggle (on/off) + corridor_pct input. Toggle creates/removes type-level rule via `PUT/DELETE .../corridors/type/{type}`.
- **Class row**: shows resolved state. `(наследовано)` = from type, `[своё]` = has class-level override.
- **`[×]` button**: `DELETE .../corridors/class/{id}` — removes override, class falls back to type.
- **Click on class row**: inline edit — toggle + pct field, `PUT .../corridors/class/{id}`.
- **Type without rule** ("Прочее"): shows "не настроено", all classes also "не настроено".

### Data hooks

- `useMaterialClasses()` — existing, unchanged
- `useCorridors(projectId)` — resolved matrix from new GET
- `useSetTypeCorridor()`, `useDeleteTypeCorridor()` — type-level mutations
- `useSetClassCorridor()`, `useDeleteClassCorridor()` — class-level mutations

All mutations invalidate `corridors` + `calculations` query keys.

---

## 6. Excel Export

- **Column Q** "Коридор, %" — resolved `corridor_pct`. Empty if not compensable.
- **Column R** "Компенсация, ₽" — `compensation_amount`. `None` → empty, `0` → `0`, number → number.
- No change to export format — source (type/class) is a config detail, not relevant for the report.
- `compute_export_rows` uses the same `resolve_corridor` as `compute_calculations` → automatic consistency.

---

## Files to modify

### Backend
- `models.py` — restructure `CompensationCorridor`
- `crud/compensation_corridors.py` — new CRUD: `get_corridor_map`, `resolve_corridor`, type/class upsert/delete, resolved matrix builder
- `crud/calculations.py` — integrate new `resolve_corridor` (replace old `get_corridor_map` call)
- `routers/projects.py` — new corridor endpoints (or split into `routers/corridors.py`)
- `alembic/versions/` — migration: DROP old + CREATE new
- `tests/unit/` — test `resolve_corridor` pure function, Pydantic validator
- `tests/integration/` — test API endpoints, resolved matrix, calculation integration

### Frontend
- `components/projects/CorridorsTab.tsx` — rewrite with grouped UI
- `types/compensationCorridor.ts` — update types for resolved matrix
- `services/queries.ts` + `queryKeys.ts` — new hooks and keys
- `services/api/` — new API functions for type/class corridors
- `test/handlers.ts` — MSW handlers for new endpoints
- `CorridorsTab.test.tsx` — tests for new UI behavior

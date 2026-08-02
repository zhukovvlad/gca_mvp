# Float → Decimal Migration — Design Spec (Spec 1 of 2)

**Date:** 2026-06-04
**Status:** Approved, ready for implementation planning

## Context

The УПД tracker stores financial values (prices, sums, VAT, percentages) as `Float`. This
risks accumulated floating-point error and — more importantly for this product — copeck-level
divergence from counterparties' documents computed in **1С**. We migrate the storage to
`Numeric` and make the Python calculation layer work strictly with `decimal.Decimal`.

**The business logic does not change** — only precision and type-safety. Behaviour (avg_price,
deviation, compensation, monthly turnover) stays identical; the numbers just stop drifting.

This is **Spec 1 of a two-spec refactor**. Spec 2 (compensation-corridor fallback restructure +
`is_compensable` flag) is deliberately separated: Numeric is a mechanical, cross-cutting precision
change with a clean rollback point; the corridor restructure is a business-logic change with new UI.
Numeric ships first so the corridor work in Spec 2 is built on a Decimal foundation. Spec 2 is
**out of scope here.**

`mypy` is **not** present in the project (linter is ruff, which does not type-check Decimal/float
mixing across variables). Introducing mypy on the legacy codebase is its own large effort and is
**out of scope** — defense against Decimal/float mixing is built on strict **boundary normalization**
instead: data is forced to Decimal at the few points where it enters the calculation layer.

## Strategy: end-to-end Decimal (chosen)

`Decimal` flows through the entire calculation layer; conversion to `float` happens only at the
JSON-serialization edge and Excel write. Rejected alternatives:

- **Decimal in DB only, float in Python** — a half-measure; copeck bugs just move from the DB into
  RAM. The calculation layer would still drift.
- **Hybrid (Decimal some places, float others)** — the worst case: guarantees `Decimal + float`
  `TypeError`s sprinkled through the logs.

## Blast radius (from codebase exploration)

`Numeric` columns are read by SQLAlchemy as `decimal.Decimal`. The risky surfaces:

- **~13 SQL aggregates** with `COALESCE(Invoice.vat_rate, 20.0)` (float literal inside a Decimal
  expression) across `crud/calculations.py`, `crud/suppliers.py`, `routers/dashboard.py`.
- **Python arithmetic** mixing Decimal with float → `TypeError` (`Decimal + float` always raises):
  `compute_compensation_per_unit`, `compute_calculations`, `_compute_supplier_project_deviation`,
  `compute_export_rows`.
- **JSON serialization** of hand-built dicts (not Pydantic models) in `routers/invoices.py`,
  `routers/dashboard.py`, `routers/reference_prices.py`, `routers/projects.py` — `Decimal` is not
  JSON-serializable by stdlib `json`.
- **`round()` semantics** — `round()` on Decimal uses banker's rounding (≠ float).
- **Write path** (`crud/documents.py`) — values parsed from the LLM response arrive as Python
  `float` and are written straight into Numeric columns; psycopg binary-casts float→Decimal,
  leaking float imprecision into the DB *at write time*, bypassing read-side defenses.

## Task 1 — SQLAlchemy models + migration

Change `Float` → `Numeric` with explicit precision/scale:

| Model | Field | Type |
|---|---|---|
| `ReferencePrice` | `price` | `Numeric(19, 4)` |
| `Invoice` | `vat_rate` | `Numeric(5, 2)` |
| `InvoiceItem` | `quantity` | `Numeric(15, 4)` |
| `InvoiceItem` | `unit_price` | `Numeric(19, 4)` |
| `InvoiceItem` | `amount` | `Numeric(15, 2)` |
| `InvoiceItem` | `vat_amount` | `Numeric(15, 2)` |
| `CompensationCorridor` | `corridor_pct` | `Numeric(5, 2)` |

Alembic migration: `ALTER COLUMN ... TYPE NUMERIC(...) USING column_name::numeric` for each
existing column so PostgreSQL casts existing data server-side. Down-revision = current head
(`b8c9d0e1f2a3`); confirm with `alembic heads` before writing.

## Task 2 — domain rounding rule (RU arithmetic, not banker's)

Default banker's rounding (`ROUND_HALF_EVEN`) is wrong for reconciling with Russian УПД: RU
accounting/1С/ФНS use arithmetic rounding (`ROUND_HALF_UP`, 0.5 → up). Using banker's would
*create* copeck divergence with 1С counterparties — the exact bug this refactor removes.

**New module `backend/finance.py`** (alongside `security.py`; NOT `utils/` — `utils.py` is a module,
not a package, and a `utils/` package would break `from utils import get_client_ip, utcnow`):

```python
from decimal import Decimal, ROUND_HALF_UP
from typing import Union


def money_round(value: Union[Decimal, float, str, int], places: int = 2) -> Decimal:
    """Округляет финансовые значения по правилам РФ (ROUND_HALF_UP, 0.5 → вверх).

    Принимает любой базовый тип, конвертируя float через str() — это отсекает
    бинарную микропогрешность float до квантования.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))  # str() обязателен: Decimal(0.1) != Decimal("0.1")
    exp = Decimal(f"0.{'0' * places}") if places > 0 else Decimal("1")
    return value.quantize(exp, rounding=ROUND_HALF_UP)
```

Use `money_round` everywhere a financial value is rounded, replacing every `round()` in the
calculation functions.

## Task 3 — boundary normalization (the core defense)

Force Decimal at the three points where data enters the calculation layer, so float never reaches
the arithmetic.

**3a. Write path (`crud/documents.py`) — the most dangerous gap.** In `create_invoice`, the LLM-parsed
`items` dicts and `vat_rate` are Python floats written into Numeric columns. Wrap them in
`Decimal(str(...))` at write time so float imprecision never enters the DB:

```python
invoice = Invoice(
    ...
    vat_rate=Decimal(str(vat_rate)) if vat_rate is not None else None,
    ...
)
for item in items:
    db_item = InvoiceItem(
        ...
        quantity=Decimal(str(item["quantity"])),
        unit_price=Decimal(str(item["unit_price"])),
        amount=Decimal(str(item["amount"])),
        vat_amount=Decimal(str(item["vat_amount"])) if item.get("vat_amount") is not None else None,
    )
```

(Prefer a small local helper to avoid repetition, e.g. `_dec(v)` returning `Decimal(str(v))` or
`None`.)

**3b. API payloads (Pydantic).** In request schemas accepting financial data, change `float` → `Decimal`
(e.g. `ReferencePriceCreate.price`, `ReferencePriceUpdate.price`, `CorridorUpsert.corridor_pct`).
Pydantic v2 coerces incoming JSON numbers/strings to Decimal. Range validators (`Field(ge=0, le=100)`)
work unchanged on Decimal.

**3c. SQL aggregate literals.** Replace float literals inside SQL expressions. Prefer
`literal(Decimal("20.0"))` over raw `text("20.0::numeric")` — `literal` is type-bound, dialect-safe,
and idiomatic:

```python
from sqlalchemy import literal
# func.coalesce(Invoice.vat_rate, 20.0)  →
func.coalesce(Invoice.vat_rate, literal(Decimal("20.0")))
```

Apply to all ~13 `COALESCE(vat_rate, 20.0)` sites and the `/ 100` divisor where it feeds a Decimal
column (use `/ literal(Decimal("100"))` or rely on Numeric-by-Numeric division — verify the generated
SQL keeps NUMERIC).

**3d. Python reads from SQL rows.** Where `compute_*` reads aggregate results (`row.mat_total`,
`row.qty`, `row.total_with_vat`, etc.), these are already Decimal once columns are Numeric — remove
the existing `float(...)` wrappers and keep them Decimal. Replace bare float literals in the
arithmetic with Decimal (`/ 100` → `/ Decimal("100")`, `1 + k` where k is Decimal works as-is).
`compute_compensation_per_unit` already does Decimal-safe math once `ref_price`/`corridor_pct`
arrive as Decimal — verify `100.0` → `Decimal("100")` there too, and its `round()` → `money_round`.

## Task 4 — global serialization (Python → JSON)

Stdlib `json` cannot serialize `Decimal` in dicts returned outside Pydantic models. Convert
Decimal → float at the serialization edge so the frontend is untouched (JS uses IEEE-754 doubles;
fine for UI rendering, and DB+Python precision is preserved).

In `main.py`, add a custom default response class:

```python
import json
from decimal import Decimal
from typing import Any
from fastapi.responses import JSONResponse


def decimal_encoder(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class DecimalJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), default=decimal_encoder,
        ).encode("utf-8")


app = FastAPI(..., default_response_class=DecimalJSONResponse)
```

Note: this is **defense in depth**. FastAPI runs returned dicts through `jsonable_encoder` (which
itself handles Decimal→float) before `render()`; `DecimalJSONResponse` is the safety net for
`Response`/`StreamingResponse` and any path that bypasses the model encoder. The Excel export
(`routers/export.py`) returns a binary `StreamingResponse` — unaffected; openpyxl writes Decimal
cells fine.

## Task 5 — tests

Update existing compensation/calculation unit + integration tests for `ROUND_HALF_UP`:

- Add a `money_round` unit test with the canonical boundary case on **Decimal input** (not float, to
  avoid float-repr dependence): `money_round(Decimal("2.345")) == Decimal("2.35")` (HALF_UP);
  banker's would give `2.34`. Also `money_round(Decimal("2.355")) == Decimal("2.36")`.
- `compute_compensation_per_unit` tests: values now return `Decimal`, not float — assert against
  `Decimal("5.00")` etc. (or compare via `money_round`). The existing cases (110/100/5%→5,
  90→−5, 103→0, 0%→deviation) hold; update expected types.
- Integration tests reading API responses get **float** back (serialization edge), so JSON
  assertions stay numeric — verify they still pass with the new precision.
- Confirm `block_real_openrouter`/factory-based tests still pass; `InvoiceItemFactory` produces
  floats — they pass through `Decimal(str(...))` at the write path, so DB rows are exact.

## Out of scope (this spec)

- Compensation-corridor fallback restructure + `is_compensable` flag → **Spec 2**.
- Introducing mypy / static type-checking → backlog, separate effort.
- Decimal-as-string on the JSON wire (`C`) → would force a frontend rewrite (formatMoney, Recharts);
  not needed.
- Frontend changes → none; the serialization edge keeps the API contract (numbers) identical.

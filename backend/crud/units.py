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
    quantity: Decimal | None,
    unit_price: Decimal | None,
    amount: Decimal | None,
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


def item_has_issues(item) -> bool:
    """True if an invoice item blocks analytics: no quantity, no description,
    an un-normalized material unit, or a quantity*price≈amount invariant break.

    Shared by the invoices and dashboard routers' has-issues checks so the
    business rules cannot drift between the two endpoints.
    """
    if (item.quantity or 0) <= 0:
        return True
    if not (item.raw_name or "").strip():
        return True
    if item.item_type == "material" and item.normalized_unit_id is None:
        return True
    return not invariant_holds(item.quantity, item.unit_price, item.amount)


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
    if entry.multiplier == 0:
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
        out[normalize_unit_key(alias.raw_text)] = AliasEntry(
            base_unit_id=base.id,
            multiplier=unit.to_base_multiplier,
            dimension=base.dimension,
            base_symbol=base.symbol,
        )
    return out

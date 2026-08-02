"""Units of measure: normalization helpers and seed data.

Single source of truth used by runtime, the Alembic migration, and tests.
normalize_unit_key MUST be identical everywhere.
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


# --- Seed data (consumed by the migration and tests) ------------------------
# Base units first; derived units reference base by code. multiplier is a string
# (parsed to Decimal) to avoid float imprecision in the Numeric audit trail.
# Алиасы «м2»/«кв.м»/«м²» обязательны (AGENTS.md §4).

UNITS_SEED: list[dict] = [
    {"code": "TON",  "name": "Тонна",      "symbol": "т",     "dimension": "mass",   "base_code": None,  "multiplier": "1"},
    {"code": "KG",   "name": "Килограмм",  "symbol": "кг",    "dimension": "mass",   "base_code": "TON", "multiplier": "0.001"},
    {"code": "M3",   "name": "Куб. метр",  "symbol": "м³",    "dimension": "volume", "base_code": None,  "multiplier": "1"},
    {"code": "L",    "name": "Литр",       "symbol": "л",     "dimension": "volume", "base_code": "M3",  "multiplier": "0.001"},
    {"code": "M2",   "name": "Кв. метр",   "symbol": "м²",    "dimension": "area",   "base_code": None,  "multiplier": "1"},
    {"code": "M",    "name": "Метр",       "symbol": "м",     "dimension": "length", "base_code": None,  "multiplier": "1"},
    {"code": "PCS",  "name": "Штука",      "symbol": "шт",    "dimension": "count",  "base_code": None,  "multiplier": "1"},
    {"code": "SET",  "name": "Комплект",   "symbol": "компл", "dimension": "count",  "base_code": None,  "multiplier": "1"},
    {"code": "MON",  "name": "Месяц",      "symbol": "мес",   "dimension": "time",   "base_code": None,  "multiplier": "1"},
]

# normalized key → unit code. Keys are already normalize_unit_key()-ed
# (NFKC folds м³→м3 and м²→м2, so only the folded forms are listed).
ALIASES_SEED: dict[str, str] = {
    "т": "TON", "тн": "TON", "тонн": "TON", "тонна": "TON", "t": "TON", "ton": "TON",
    "кг": "KG", "kg": "KG",
    "м3": "M3", "m3": "M3", "куб": "M3", "куб.м": "M3", "куб м": "M3",
    "л": "L", "l": "L",
    "м2": "M2", "m2": "M2", "кв.м": "M2", "кв м": "M2", "кв. м": "M2",
    "м": "M", "m": "M", "пог.м": "M", "п.м": "M", "м.п": "M", "мп": "M",
    "шт": "PCS", "штук": "PCS", "pcs": "PCS",
    "компл": "SET", "комплект": "SET", "к-т": "SET", "кт": "SET",
    "мес": "MON", "месяц": "MON",
}


# --- Runtime alias map ------------------------------------------------------

@dataclass(frozen=True)
class AliasEntry:
    """Resolved alias: which canonical base unit + conversion to apply."""
    base_unit_id: int      # base unit of the dimension
    multiplier: Decimal    # to_base_multiplier of the matched (possibly derived) unit
    dimension: str
    base_symbol: str


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

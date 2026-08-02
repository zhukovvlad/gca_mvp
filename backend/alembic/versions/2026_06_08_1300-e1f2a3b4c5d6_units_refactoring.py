"""units refactoring: units_of_measure, unit_aliases, material_types + FK migration

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-08 13:00:00.000000
"""
# Reuse the single source of truth for seed data + normalization.
import sys
from decimal import Decimal
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

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

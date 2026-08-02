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


# (table, column, numeric_type)
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
            f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {ntype} "
            f"USING {col}::numeric"
        )


def downgrade() -> None:
    for table, col, _ntype in _COLS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {col} TYPE DOUBLE PRECISION "
            f"USING {col}::double precision"
        )

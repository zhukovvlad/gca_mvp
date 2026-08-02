"""drop_price_calculations_table

Revision ID: a1b2c3d4e5f6
Revises: b3c7e9f12a45
Create Date: 2026-05-17 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b3c7e9f12a45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("price_calculations")


def downgrade() -> None:
    raise NotImplementedError("price_calculations table was dropped intentionally — no rollback")

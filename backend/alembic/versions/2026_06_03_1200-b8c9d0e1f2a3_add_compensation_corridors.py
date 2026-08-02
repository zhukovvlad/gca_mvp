"""add compensation_corridors

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-03 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compensation_corridors",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_class_id", sa.Integer(), nullable=False),
        sa.Column("corridor_pct", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_class_id"], ["material_classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "material_class_id"),
    )


def downgrade() -> None:
    op.drop_table("compensation_corridors")

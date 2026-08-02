"""corridor_fallback

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-08 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("compensation_corridors")

    op.create_table(
        "compensation_corridors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_type", sa.String(), nullable=True),
        sa.Column("material_class_id", sa.Integer(), nullable=True),
        sa.Column("is_compensable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("corridor_pct", sa.Numeric(5, 2), nullable=True),
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
        sa.CheckConstraint(
            "(material_type IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive",
        ),
        sa.CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable",
        ),
    )
    op.create_index(
        "uq_corridor_project_type",
        "compensation_corridors",
        ["project_id", "material_type"],
        unique=True,
        postgresql_where=sa.text("material_class_id IS NULL"),
    )
    op.create_index(
        "uq_corridor_project_class",
        "compensation_corridors",
        ["project_id", "material_class_id"],
        unique=True,
        postgresql_where=sa.text("material_type IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("compensation_corridors")

    op.create_table(
        "compensation_corridors",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("material_class_id", sa.Integer(), nullable=False),
        sa.Column("corridor_pct", sa.Numeric(5, 2), nullable=False),
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

"""add_organization_kind

Добавляет колонку organizations.kind (роль организации: customer / contractor).

NOTE: kind хранится как VARCHAR с CHECK constraint (native_enum=False в модели),
как и org_role/project_role. Колонка NOT NULL с server_default='customer' —
существующие организации становятся заказчиками (типичный кейс для УПД-трекера:
заказчик загружает счета подрядчиков). server_default заполняет существующие
строки в момент ALTER TABLE.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-05-30 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "kind",
            sa.String(),
            sa.CheckConstraint(
                "kind IN ('customer', 'contractor')", name="ck_organizations_kind"
            ),
            nullable=False,
            server_default="customer",
        ),
    )


def downgrade() -> None:
    # CHECK constraint удаляется вместе с колонкой
    op.drop_column("organizations", "kind")

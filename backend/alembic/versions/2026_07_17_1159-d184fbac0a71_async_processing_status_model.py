"""async processing status model

Revision ID: d184fbac0a71
Revises: 1859523e53de
Create Date: 2026-07-17 11:59:59.077294

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd184fbac0a71'
down_revision: Union[str, None] = '1859523e53de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ставит NOT NULL + server_default='pending' на status, добавляет поля статусной модели."""
    # NOT NULL безопасен: приложение всегда проставляло status (ORM default 'parsed'),
    # NULL-строк в documents.status нет. Совмещаем с установкой server_default,
    # чтобы модель (nullable=False) и БД не расходились.
    op.alter_column("documents", "status", server_default="pending", nullable=False)
    op.add_column("documents", sa.Column("processing_started_at", sa.DateTime(), nullable=True))
    op.add_column("documents", sa.Column("last_error", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("processing_run_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Откатывает поля статусной модели и server_default/NOT NULL на status."""
    op.drop_column("documents", "processing_run_id")
    op.drop_column("documents", "last_error")
    op.drop_column("documents", "processing_started_at")
    op.alter_column("documents", "status", server_default=None, nullable=True)

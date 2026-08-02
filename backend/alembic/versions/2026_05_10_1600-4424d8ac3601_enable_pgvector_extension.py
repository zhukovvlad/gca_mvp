"""enable pgvector extension

Revision ID: 4424d8ac3601
Revises: 754960ccaf19
Create Date: 2026-05-10 16:00:56.222310

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4424d8ac3601'
down_revision: Union[str, None] = '754960ccaf19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")

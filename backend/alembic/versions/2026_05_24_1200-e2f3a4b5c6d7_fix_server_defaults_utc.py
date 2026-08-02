"""fix_server_defaults_utc

Заменяет server_default=now() на (now() AT TIME ZONE 'utc') для всех
auth-таблиц с created_at. Гарантирует UTC независимо от timezone Postgres-сессии.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-24 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC_DEFAULT = sa.text("(now() AT TIME ZONE 'utc')")
_OLD_DEFAULT = sa.text("now()")

_TABLES = ["organizations", "users", "project_organizations", "refresh_tokens"]


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "created_at", server_default=_UTC_DEFAULT)


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "created_at", server_default=_OLD_DEFAULT)

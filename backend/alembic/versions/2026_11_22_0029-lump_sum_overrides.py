"""Поправки «комплект или ставка» (спека строк-сумм §3).

Таблица `lump_sum_overrides`: решение администратора о строке сметы, `verdict` — `lump`
или `rate`. Отсутствие поправки — отсутствие строки. Ключ — строка сметы, каскад по ней;
автор — `users`, `RESTRICT`.

Выражение CHECK продублировано константой в `models.py` (миграция не импортирует
`models.py`); паритет — `test_lump_sum_load.py::TestSchema`.

`downgrade` удаляет таблицу вместе с поправками.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

CK_LUMP_SUM_VERDICT = "verdict IN ('lump', 'rate')"
CK_NAME = "ck_lump_sum_overrides_verdict"


def upgrade() -> None:
    op.create_table(
        "lump_sum_overrides",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_lump_sum_overrides"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_lump_sum_overrides_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_lump_sum_overrides_set_by"
        ),
        sa.CheckConstraint(CK_LUMP_SUM_VERDICT, name=CK_NAME),
    )


def downgrade() -> None:
    op.drop_table("lump_sum_overrides")

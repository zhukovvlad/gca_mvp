"""Решения «не ошибка» по подозрительным строкам договоров (спека «Подозрительные строки» §1 п. 1).

`not_error_marks` — по строке на основание: выброс цены `(family_id, unit_id, direction)` или
двойник `neighbor_item_id`. Каскады по строке сметы, соседу и семье; автор и единица —
`RESTRICT`. `UNIQUE NULLS NOT DISTINCT`: у двойника поля выброса `NULL`, и без него второй такой
же ряд прошёл бы.

Выражения CHECK и имя формы продублированы константами в `models.py` (миграция не импортирует
`models.py`); паритет — `test_suspicious_schema.py`.

`downgrade` удаляет таблицу вместе с решениями.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

CK_MARK_REASON = "char_length(reason) BETWEEN 1 AND 300"
CK_NOT_ERROR_SHAPE = "ck_not_error_marks_shape"
CK_NOT_ERROR_SHAPE_EXPR = (
    "(kind = 'outlier' AND family_id IS NOT NULL AND unit_id IS NOT NULL "
    "AND direction IS NOT NULL AND neighbor_item_id IS NULL) OR "
    "(kind = 'twin' AND neighbor_item_id IS NOT NULL AND family_id IS NULL "
    "AND unit_id IS NULL AND direction IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "not_error_marks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("neighbor_item_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_not_error_marks"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_not_error_marks_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="CASCADE",
            name="fk_not_error_marks_family",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units_of_measure.id"], ondelete="RESTRICT",
            name="fk_not_error_marks_unit",
        ),
        sa.ForeignKeyConstraint(
            ["neighbor_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_not_error_marks_neighbor",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_not_error_marks_set_by"
        ),
        sa.CheckConstraint("kind IN ('outlier', 'twin')", name="ck_not_error_marks_kind"),
        sa.CheckConstraint("direction IN ('up', 'down')", name="ck_not_error_marks_direction"),
        sa.CheckConstraint(CK_NOT_ERROR_SHAPE_EXPR, name=CK_NOT_ERROR_SHAPE),
        sa.CheckConstraint(CK_MARK_REASON, name="ck_not_error_marks_reason"),
    )
    # PG15+: NULLS NOT DISTINCT — у двойника поля выброса NULL, и два одинаковых ряда различались бы
    op.create_unique_constraint(
        "uq_not_error_marks_key",
        "not_error_marks",
        ["position_item_id", "kind", "family_id", "unit_id", "direction", "neighbor_item_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_not_error_marks_position_item_id", "not_error_marks", ["position_item_id"])


def downgrade() -> None:
    op.drop_table("not_error_marks")

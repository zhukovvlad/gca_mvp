"""Отметки «не аналог» (спека «Не аналог» §1).

`price_error_marks` — «ошибка в цене»: отметка строки договора, ключ — строка сметы.
`not_analog_marks` — «не аналог в этом тендере»: тройка (тендер, ключ формы, строка договора).
Ключ формы — наименование строки КП, приведённое `normalize_title`, и единица (может быть NULL,
поэтому уникальность `NULLS NOT DISTINCT`). Каскады по строке сметы и тендеру; автор и единица —
`RESTRICT`.

Выражение CHECK причины продублировано константой в `models.py` (миграция не импортирует
`models.py`); паритет — `test_not_analog_schema.py`.

`downgrade` удаляет обе таблицы вместе с отметками.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

CK_MARK_REASON = "char_length(reason) BETWEEN 1 AND 300"


def upgrade() -> None:
    op.create_table(
        "price_error_marks",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_price_error_marks"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_price_error_marks_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_price_error_marks_set_by"
        ),
        sa.CheckConstraint(CK_MARK_REASON, name="ck_price_error_marks_reason"),
    )
    op.create_table(
        "not_analog_marks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("form_title", sa.Text(), nullable=False),
        sa.Column("form_unit_id", sa.Integer(), nullable=True),
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_not_analog_marks"),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tenders.id"], ondelete="CASCADE", name="fk_not_analog_marks_tender"
        ),
        sa.ForeignKeyConstraint(
            ["form_unit_id"], ["units_of_measure.id"], ondelete="RESTRICT",
            name="fk_not_analog_marks_form_unit",
        ),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_not_analog_marks_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_not_analog_marks_set_by"
        ),
        sa.CheckConstraint(CK_MARK_REASON, name="ck_not_analog_marks_reason"),
        sa.CheckConstraint("form_title <> ''", name="ck_not_analog_marks_form_title"),
    )
    # PG15+: NULLS NOT DISTINCT — две отметки на ключ формы без единицы различались бы по NULL
    op.create_unique_constraint(
        "uq_not_analog_marks_key",
        "not_analog_marks",
        ["tender_id", "form_title", "form_unit_id", "position_item_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_not_analog_marks_tender_id", "not_analog_marks", ["tender_id"])


def downgrade() -> None:
    op.drop_table("not_analog_marks")
    op.drop_table("price_error_marks")

"""Решения по строкам КП и отправленные файлы (спека 4б §2.13).

Пять новых таблиц; существующие не меняются.

**`kp_line_agreements`**: «Договорились» по строке КП, цена в нетто. CHECK цены
называет конечность явно: `NaN` в Postgres больше любого числа, и голое `> 0`
его пропустило бы (`docs/pitfalls/db.md`).

**`kp_line_comments`**: собственный комментарий к строке, до 2000 знаков; пустая
строка допустима.

**`kp_line_rungs`**: ручная ступень работы строки; `key jsonb` — снимок без
внешних ключей (вариант и семья могут уйти, тогда ступень не действует).

**`kp_family_rungs`**: ручная ступень системы для семьи в смете;
`first_main_parameter_id` — снимок `family_parameters.id` без внешнего ключа
(версии схем удаляются каскадом, ступень не должна уходить вместе с ними).
Равенство `(level = 'p') = (first_main_parameter_id IS NOT NULL)` тотально: обе
стороны не `NULL`.

**`kp_sent_files`**: отправленный подрядчику файл — непрозрачный `file_key`,
имя, хэш, размер, ставка НДС файла и подпись ступени.

Отсутствие решения — отсутствие строки, а не `NULL`. Все внешние ключи на
пользователя — `RESTRICT`; на строку КП, смету, семью и оффер — `CASCADE`.

**`downgrade`** отказывает при строке в любой из пяти таблиц и называет таблицу и
счёт: решения человека и отправленные файлы не удаляются откатом.

Литералы CHECK продублированы константами в `models.py` (миграция не импортирует
`models.py` — она обязана быть неизменной во времени); паритет —
`test_kp_file_schema.py`.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

# Литералы дублируют models.py: миграция обязана быть неизменной во времени.
CK_KP_AGREEMENT_NET_PRICE = "net_price > 0 AND net_price <> 'NaN' AND net_price <> 'Infinity'"
CK_KP_COMMENT_LENGTH = "char_length(text) <= 2000"
CK_KP_RUNG_LEVEL = "level IN ('v', 'p', 'f')"
CK_KP_RUNG_SCOPE = "scope IN ('cls', 'all')"
CK_KP_FAMILY_RUNG_PARAMETER = "(level = 'p') = (first_main_parameter_id IS NOT NULL)"

TABLES = (
    "kp_line_agreements",
    "kp_line_comments",
    "kp_line_rungs",
    "kp_family_rungs",
    "kp_sent_files",
)


def _set_at() -> sa.Column:
    return sa.Column(
        "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.create_table(
        "kp_line_agreements",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("net_price", sa.Numeric(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        _set_at(),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_kp_line_agreements"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_kp_line_agreements_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_line_agreements_set_by"
        ),
        sa.CheckConstraint(CK_KP_AGREEMENT_NET_PRICE, name="ck_kp_line_agreements_net_price"),
    )

    op.create_table(
        "kp_line_comments",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        _set_at(),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_kp_line_comments"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_kp_line_comments_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_line_comments_set_by"
        ),
        sa.CheckConstraint(CK_KP_COMMENT_LENGTH, name="ck_kp_line_comments_text_length"),
    )

    op.create_table(
        "kp_line_rungs",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", postgresql.JSONB(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        _set_at(),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_kp_line_rungs"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_kp_line_rungs_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_line_rungs_set_by"
        ),
        sa.CheckConstraint(CK_KP_RUNG_LEVEL, name="ck_kp_line_rungs_level"),
        sa.CheckConstraint(CK_KP_RUNG_SCOPE, name="ck_kp_line_rungs_scope"),
    )

    op.create_table(
        "kp_family_rungs",
        sa.Column("estimate_id", sa.BigInteger(), nullable=False),
        sa.Column("work_family_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("first_main_parameter_id", sa.BigInteger(), nullable=True),
        sa.Column("set_by", sa.Integer(), nullable=False),
        _set_at(),
        sa.PrimaryKeyConstraint("estimate_id", "work_family_id", name="pk_kp_family_rungs"),
        sa.ForeignKeyConstraint(
            ["estimate_id"], ["estimates.id"], ondelete="CASCADE",
            name="fk_kp_family_rungs_estimate",
        ),
        sa.ForeignKeyConstraint(
            ["work_family_id"], ["work_families.id"], ondelete="CASCADE",
            name="fk_kp_family_rungs_work_family",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_family_rungs_set_by"
        ),
        sa.CheckConstraint(CK_KP_RUNG_LEVEL, name="ck_kp_family_rungs_level"),
        sa.CheckConstraint(CK_KP_RUNG_SCOPE, name="ck_kp_family_rungs_scope"),
        sa.CheckConstraint(
            CK_KP_FAMILY_RUNG_PARAMETER, name="ck_kp_family_rungs_first_main_parameter"
        ),
    )

    op.create_table(
        "kp_sent_files",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.BigInteger(), nullable=False),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("vat_rate", sa.Numeric(), nullable=False),
        sa.Column("level_label", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kp_sent_files"),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], ondelete="CASCADE", name="fk_kp_sent_files_offer"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_kp_sent_files_created_by",
        ),
        sa.UniqueConstraint("file_key", name="uq_kp_sent_files_file_key"),
    )


def _downgrade_blockers(bind) -> dict[str, int]:
    """Пять счётчиков, каждый сам по себе держит откат: решения человека и
    отправленные файлы независимы друг от друга. Вынесены в функцию, чтобы тест
    проверил каждый отдельно."""
    return {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in TABLES
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    counts = ", ".join(f"{table} — {blockers[table]}" for table in TABLES)
    return (
        f"Откат 0023 невозможен: строк в таблицах решений и отправленных файлов: {counts}. "
        "Это решения человека и выданные подрядчику файлы; удалить их — решение человека, "
        "а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    for table in reversed(TABLES):
        op.drop_table(table)

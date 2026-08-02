"""Initial schema: extensions (pgvector, btree_gist), auth, units of measure.

pgvector и btree_gist включаются ПЕРВОЙ миграцией (AGENTS.md §3, §9 фаза 2):
vector нужен колонке catalog_positions.embedding (фаза 2), btree_gist —
EXCLUDE-констрейнту rate_standards.

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # --- auth ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(now() AT TIME ZONE 'utc')")),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(now() AT TIME ZONE 'utc')")),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # --- units of measure ----------------------------------------------------
    op.create_table(
        "units_of_measure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=False),
        sa.Column(
            "base_unit_id",
            sa.Integer(),
            sa.ForeignKey("units_of_measure.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("to_base_multiplier", sa.Numeric(30, 15), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("code", name="uq_units_of_measure_code"),
        sa.CheckConstraint(
            "(base_unit_id IS NOT NULL) OR (to_base_multiplier = 1)",
            name="ck_unit_base_multiplier",
        ),
    )

    op.create_table(
        "unit_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column(
            "unit_id",
            sa.Integer(),
            sa.ForeignKey("units_of_measure.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("raw_text", name="uq_unit_aliases_raw_text"),
    )

    # --- seed units + aliases ------------------------------------------------
    # Данные и нормализация ключей — единый источник истины crud/units.py.
    from crud.units import ALIASES_SEED, UNITS_SEED, normalize_unit_key

    conn = op.get_bind()
    ids: dict[str, int] = {}
    for u in UNITS_SEED:
        base_id = ids[u["base_code"]] if u["base_code"] else None
        row = conn.execute(
            sa.text(
                "INSERT INTO units_of_measure (code, name, symbol, dimension, base_unit_id, to_base_multiplier) "
                "VALUES (:code, :name, :symbol, :dimension, :base_id, :mult) RETURNING id"
            ),
            {
                "code": u["code"], "name": u["name"], "symbol": u["symbol"],
                "dimension": u["dimension"], "base_id": base_id, "mult": u["multiplier"],
            },
        ).one()
        ids[u["code"]] = row[0]

    for raw, code in ALIASES_SEED.items():
        conn.execute(
            sa.text("INSERT INTO unit_aliases (raw_text, unit_id) VALUES (:raw, :unit_id)"),
            {"raw": normalize_unit_key(raw), "unit_id": ids[code]},
        )


def downgrade() -> None:
    op.drop_table("unit_aliases")
    op.drop_table("units_of_measure")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS vector")

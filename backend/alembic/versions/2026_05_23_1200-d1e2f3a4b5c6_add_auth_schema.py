"""add_auth_schema

Добавляет таблицы для аутентификации и изоляции данных по организациям:
- organizations — тенант (единица изоляции)
- users — пользователи с хэшами паролей
- project_organizations — роли организаций на проектах (customer / contractor)
- refresh_tokens — отзываемые refresh-токены (хранится sha256-хэш)

Также добавляет колонки в существующие таблицы:
- projects.customer_org_id
- documents.file_hash, uploaded_by_org_id, uploaded_by_user_id

NOTE: org_role и project_role созданы как VARCHAR с CHECK constraint
(native_enum=False в моделях). Сделано сознательно: PG ENUM требует
ALTER TYPE для добавления значений, что плохо поддерживается autogenerate
в Alembic и блокирует параллельные миграции. НЕ менять на native PG ENUM.

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-05-23 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Создаём таблицу organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("inn", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organizations_inn", "organizations", ["inn"])

    # 2. Создаём таблицу users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # VARCHAR + CHECK вместо PG ENUM (см. примечание в docstring)
        sa.Column(
            "org_role",
            sa.String(),
            sa.CheckConstraint("org_role IN ('superadmin', 'admin', 'member')", name="ck_users_org_role"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    # ix_users_email не создаём — UniqueConstraint("email") уже создаёт уникальный индекс в PG
    op.create_index("ix_users_org_id", "users", ["org_id"])

    # 3. Создаём таблицу project_organizations
    op.create_table(
        "project_organizations",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "project_role",
            sa.String(),
            sa.CheckConstraint("project_role IN ('customer', 'contractor')", name="ck_project_organizations_role"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "org_id"),
    )

    # 4. Создаём таблицу refresh_tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    # ix_refresh_tokens_token_hash не создаём — UniqueConstraint("token_hash") уже создаёт индекс в PG

    # 5. Добавляем колонки в projects
    op.add_column(
        "projects",
        sa.Column("customer_org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
    )
    op.create_index("ix_projects_customer_org_id", "projects", ["customer_org_id"])

    # 6. Добавляем колонки в documents
    op.add_column("documents", sa.Column("file_hash", sa.String(64), nullable=True))
    op.add_column(
        "documents",
        sa.Column("uploaded_by_org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_documents_file_hash", "documents", ["file_hash"])
    op.create_index("ix_documents_uploaded_by_org_id", "documents", ["uploaded_by_org_id"])

    # 7. Уникальный constraint: дедупликация файлов по проекту через file_hash
    op.create_unique_constraint(
        "uq_documents_project_file_hash", "documents", ["project_id", "file_hash"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_documents_project_file_hash", "documents", type_="unique")
    op.drop_index("ix_documents_uploaded_by_org_id", "documents")
    op.drop_index("ix_documents_file_hash", "documents")
    op.drop_column("documents", "uploaded_by_user_id")
    op.drop_column("documents", "uploaded_by_org_id")
    op.drop_column("documents", "file_hash")

    op.drop_index("ix_projects_customer_org_id", "projects")
    op.drop_column("projects", "customer_org_id")

    op.drop_table("refresh_tokens")
    op.drop_table("project_organizations")
    op.drop_index("ix_users_org_id", "users")
    op.drop_table("users")
    op.drop_index("ix_organizations_inn", "organizations")
    op.drop_table("organizations")

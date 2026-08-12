"""estimate_category_overrides + источник 'manual' на position_items.

Решение о статье раздела хранится отдельной таблицей и применяется
пересчётом (спека §2.2, §2.3).

`position_item_id` в первичном ключе: «одно решение на раздел» держит схема, а не
код, и повторный `PUT` попадает в конфликт по этому же ключу. Колонки `estimate_id`
нет намеренно — она выводится соединением и, будучи продублированной, могла бы
разойтись с настоящей сметой строки; «решение сгорает вместе со сметой» выполняет
существующий каскад estimates → lots → proposals → position_items.

Того, что цель — именно строка-РАЗДЕЛ этой сметы, схема не выражает: для составного
FK понадобился бы уникальный ключ по (id, is_chapter), а частичный уникальный индекс
FK не обслуживает. Проверяет сервис под блокировкой (спека §2.2).

`users.id` — `integer`, не `bigint`; тип колонки `assigned_by` повторяет его.

Литералы записаны строками: миграция обязана быть неизменной во времени (то же
правило, что у 0002–0010).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SOURCE_CHECK_0011 = "category_source IS NULL OR category_source IN ('file','manual')"
SOURCE_CHECK_0010 = "category_source IS NULL OR category_source = 'file'"


def upgrade() -> None:
    op.create_table(
        "estimate_category_overrides",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("work_category_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_estimate_category_overrides"),
        sa.ForeignKeyConstraint(
            ["position_item_id"],
            ["position_items.id"],
            ondelete="CASCADE",
            name="fk_estimate_category_overrides_position_item_id",
        ),
        sa.ForeignKeyConstraint(
            ["work_category_id"],
            ["work_categories.id"],
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_work_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_assigned_by",
        ),
    )
    op.create_index(
        "idx_estimate_category_overrides_work_category_id",
        "estimate_category_overrides",
        ["work_category_id"],
    )
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", SOURCE_CHECK_0011
    )


def downgrade() -> None:
    # Откат отказывается САМ и с объяснением, а не роняет сырую ошибку
    # PostgreSQL: констрейнт версии 0010 запрещает 'manual', и живые решения
    # сделали бы `create_check_constraint` непроходимым (то же правило, которым
    # 0003 отказывается откатываться после многокилобайтного наименования).
    #
    # Проверяются ОБЕ таблицы, и это не перестраховка. Материализованных строк
    # 'manual' может не быть при живом решении: если структура разделов
    # предложения не определена, решение записано, а материализация не
    # состоялась. Проверка только по `position_items` в этом случае молча снесла
    # бы таблицу решений вместе с работой аналитика.
    bind = op.get_bind()
    manual = bind.execute(
        sa.text("SELECT count(*) FROM position_items WHERE category_source = 'manual'")
    ).scalar_one()
    decisions = bind.execute(
        sa.text("SELECT count(*) FROM estimate_category_overrides")
    ).scalar_one()
    if manual or decisions:
        raise RuntimeError(
            f"Откат 0011 невозможен: ручных решений о статьях — {decisions}, "
            f"материализованных строк-разделов с category_source='manual' — {manual}. "
            "Констрейнт версии 0010 такие значения запрещает, а таблица решений "
            "исчезла бы вместе с работой аналитика. Снимите ручные решения через API "
            "либо удалите сметы, к которым они относятся, — это потеря данных и "
            "решение человека, а не миграции."
        )
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", SOURCE_CHECK_0010
    )
    op.drop_index(
        "idx_estimate_category_overrides_work_category_id",
        table_name="estimate_category_overrides",
    )
    op.drop_table("estimate_category_overrides")

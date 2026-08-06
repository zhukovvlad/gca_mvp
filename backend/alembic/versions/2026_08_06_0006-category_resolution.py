"""Привязка строк сметы к статьям классификатора: четыре колонки на position_items.

Паспорт проекта агрегирует деньги по статьям, а привязки в БД нет. Статья стоит в
файле только на строках-разделах и не на всех; принадлежность строки разделу задаёт
ТОЛЬКО порядок строк файла — нумерация колонки B дублируется, и у дублей бывают
разные статьи, поэтому join по номеру раздела в денежной логике запрещён (спека Ф3
§1.1). Отсюда `chapter_item_id` — ссылка на конкретную физическую строку.

**Self-FK составной.** `(proposal_id, chapter_item_id) -> (proposal_id, id)` делает
привязку к строке ЧУЖОЙ сметы непредставимой, а не «маловероятной». Цель FK требует
`UNIQUE (proposal_id, id)`; этот индекс ЗАМЕНЯЕТ `idx_position_items_proposal_id`,
потому что `proposal_id` — его левый префикс (замер на 60 000 строк: поиск по
proposal_id идёт Bitmap Index Scan по составному индексу при включённом
enable_seqscan; на таблице в 15 строк планировщик берёт Seq Scan и этот факт не
показывает вовсе).

**ON DELETE RESTRICT на обоих FK.** Замерено на PG 16.14 для СОСТАВНОГО FK
отдельно от одноколоночного эксперимента фазы 7: каскад `proposals -> position_items`
сносит все строки предложения одним оператором и RESTRICT ему не мешает (то есть
replace-флоу работает), а точечное удаление строки-раздела со ссылками даёт громкую
ошибку FK вместо тихого уноса подразделов.

Инвариант «цель ссылки — строка-РАЗДЕЛ» в PostgreSQL декларативно не выражается
(подзапросы в CHECK запрещены); он держится резолвером и закреплён тестами.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Выражения дублируют models.PositionItem намеренно: миграция обязана быть
# неизменной во времени (то же правило, что у литералов в 0004 и 0005).
CK_ARTICLE_ONLY_ON_CHAPTERS = (
    "is_chapter OR (smr_article_raw IS NULL AND work_category_id IS NULL "
    "AND category_source IS NULL)"
)
CK_SOURCE_PAIRS = "(work_category_id IS NULL) = (category_source IS NULL)"
CK_SOURCE_VALUES = "category_source IS NULL OR category_source = 'file'"


def upgrade() -> None:
    op.add_column("position_items", sa.Column("smr_article_raw", sa.Text(), nullable=True))
    op.add_column("position_items", sa.Column("work_category_id", sa.BigInteger(), nullable=True))
    op.add_column("position_items", sa.Column("category_source", sa.Text(), nullable=True))
    op.add_column("position_items", sa.Column("chapter_item_id", sa.BigInteger(), nullable=True))

    op.create_foreign_key(
        "fk_position_items_work_category_id",
        "position_items",
        "work_categories",
        ["work_category_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Порядок важен: сначала создаётся составной UNIQUE, только потом снимается
    # прежний индекс — таблица ни в один момент не остаётся без индекса по proposal_id.
    op.create_unique_constraint(
        "uq_position_items_proposal_id_id", "position_items", ["proposal_id", "id"]
    )
    op.drop_index("idx_position_items_proposal_id", table_name="position_items")
    op.create_foreign_key(
        "fk_position_items_chapter",
        "position_items",
        "position_items",
        ["proposal_id", "chapter_item_id"],
        ["proposal_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_position_items_article_only_on_chapters", "position_items", CK_ARTICLE_ONLY_ON_CHAPTERS
    )
    op.create_check_constraint(
        "ck_position_items_category_source_pairs", "position_items", CK_SOURCE_PAIRS
    )
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", CK_SOURCE_VALUES
    )

    op.create_index(
        "idx_position_items_work_category_id",
        "position_items",
        ["work_category_id"],
        postgresql_where=sa.text("work_category_id IS NOT NULL"),
    )
    op.create_index(
        "idx_position_items_chapter_item_id",
        "position_items",
        ["chapter_item_id"],
        postgresql_where=sa.text("chapter_item_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_position_items_chapter_item_id", table_name="position_items")
    op.drop_index("idx_position_items_work_category_id", table_name="position_items")
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.drop_constraint("ck_position_items_category_source_pairs", "position_items", type_="check")
    op.drop_constraint(
        "ck_position_items_article_only_on_chapters", "position_items", type_="check"
    )
    # Составной FK снимается ДО UNIQUE: он на него ссылается.
    op.drop_constraint("fk_position_items_chapter", "position_items", type_="foreignkey")
    op.create_index("idx_position_items_proposal_id", "position_items", ["proposal_id"])
    op.drop_constraint("uq_position_items_proposal_id_id", "position_items", type_="unique")
    op.drop_constraint("fk_position_items_work_category_id", "position_items", type_="foreignkey")
    op.drop_column("position_items", "chapter_item_id")
    op.drop_column("position_items", "category_source")
    op.drop_column("position_items", "work_category_id")
    op.drop_column("position_items", "smr_article_raw")

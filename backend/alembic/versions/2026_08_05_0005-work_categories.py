"""Классификатор видов работ компании: справочник статей СМР.

Паспорт проекта (фаза 7) агрегирует деньги по крупным статьям фиксированного
классификатора, а классификатора в БД не было. Канонический источник — корпоративный
шаблон (`samples/Шаблон.xlsx`, в репозиторий не попадает); коды и названия статей
коммитить можно — это справочник, а не коммерческие данные.

**`is_bucket` — generated column, а не хранимый флаг.** Корзина полностью
определяется кодом, поэтому запись в колонку запрещена самой БД: соврать нельзя
даже правкой мимо приложения. Иначе через будущую админку появилась бы «корзина
5.1», и подсветка в паспорте начала бы лгать. Прецедент формы — `fts_vector` в 0002.

**В выражениях нет ни одного бэкслэша: точка задана как `[.]`.** Замер на PG 16.14:
при `standard_conforming_strings = off` обычный литерал `'\\.'` теряет
экранирование, регулярка превращается в `^[0-9]+(.[0-9]+)*$` — и код `1x2`
принимается МОЛЧА. Хуже того, поймать такую подмену тестом нельзя: при дефолтном
`on` определения обычного литерала и `E'…'` в БД побайтово равны (PostgreSQL не
хранит лексическую форму). `[.]` снимает проблему целиком — терять нечего.

**Непустое название проверяется явным набором символов, а не `[[:space:]]`.**
Покрытие POSIX-класса зависит от `LC_CTYPE` базы: замер на двух базах показал, что
на ctype `en-US` название из неразрывных пробелов отвергается, а на ctype `C` —
принимается. Одна схема с двумя ответами — это не инвариант. Набор задан кодовыми
точками через `chr()`, поэтому вердикт одинаков всюду. U+2009/U+3000/U+200B в набор
не входят — явно принятая граница (обоснование целиком — спека Ф1 §2.1.2).

Сид — 362 строки литералом (следующая ревизия правил не меняет: миграция неизменна
во времени, поэтому значения здесь, а не импортом из crud, как в 0001).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Выражения дублируют models.WorkCategory намеренно: миграция обязана быть
# неизменной во времени (то же правило, что у литералов в 0004). Расхождение ловит
# test_schema_constraints.py::TestWorkCategoriesSchema.
CODE_REGEX = "^[0-9]+([.][0-9]+)*$"
IS_BUCKET_EXPRESSION = "code = '99' OR code LIKE '%.99'"
TITLE_BLANK_CHARS = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"


def upgrade() -> None:
    op.create_table(
        "work_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            sa.ForeignKey("work_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "is_bucket",
            sa.Boolean(),
            sa.Computed(IS_BUCKET_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("code", name="uq_work_categories_code"),
        sa.UniqueConstraint("sort_order", name="uq_work_categories_sort_order"),
        sa.CheckConstraint(f"code ~ '{CODE_REGEX}'", name="ck_work_categories_code"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_work_categories_not_self_parent"
        ),
        sa.CheckConstraint(
            f"btrim(title, {TITLE_BLANK_CHARS}) <> ''", name="ck_work_categories_title_not_blank"
        ),
    )


def downgrade() -> None:
    op.drop_table("work_categories")

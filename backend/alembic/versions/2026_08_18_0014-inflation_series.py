"""Справочник рядов индексов инфляции и их значения по годам.

Существующих данных миграция НЕ трогает — только добавляет две таблицы
(спека `2026-08-18-inflation-adjustment-design.md` §2.11).

**Справочник создаётся ПУСТЫМ, и это решение, а не недоделка.** Безымянные
«официальный» и «неофициальный» без конкретной методики и источника заводить
нечем: название несёт конкретный показатель («Росстат, ИПЦ, декабрь к декабрю»),
а ряд, который нельзя защитить заявленным способом, создавать не из чего (§2.6).
Первый ряд заводит человек через интерфейс.

**`btrim(...) <> ''` при `NOT NULL` — не перестраховка.** Пустая строка проходит
`NOT NULL`, а «источник» из пробелов означает ряд, на вопрос «откуда 8,3 %»
(§1 п. 4 `AGENTS.md`) не отвечающий вовсе. Схема обязана такой ряд отвергать: он
печатается на листе выгрузки, то есть в артефакте защиты перед банком.

**`ondelete` у внешнего ключа не задан намеренно.** `DELETE` не предусмотрен ни
для рядов, ни для значений (§2.10): ошибочное значение исправляется `UPDATE`,
ненужный ряд архивируется через `is_active`. Каскад описывал бы удаление,
которого в контракте нет.

**Уникальность — ПАРОЙ `(series_id, year)`.** Рядов несколько по построению
(§2.6), и один и тот же год у двух рядов — норма: официальная публикация и
внутренняя оценка расходятся именно по годам.

Ограничения на непрерывность ряда в схеме НЕТ (§2.9): пропуск ловится проверкой
покрытия при чтении — она точнее (покрытие есть объединение требуемых годов
выборки, а не диапазон) и не мешает вводить годы вразнобой.

Выражения `CHECK` продублированы константами в шапке и повторены в `models.py`
намеренно: миграция обязана быть неизменной во времени (то же правило, что у
0004–0013). Расхождение ловят parity-тесты `test_schema_constraints.py` —
`alembic check` выражений `CHECK` не сравнивает.

`downgrade` роняет обе таблицы, то есть уносит заведённые ряды. Отказываться, как
это делает 0011, здесь нечего: таблицы принадлежат самой фиче, и снятие фичи есть
снятие её данных — тогда как 0011 упирался в существующие данные ЧУЖОЙ таблицы.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-18
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

CK_SERIES_NAME_NOT_BLANK = "btrim(name) <> ''"
CK_VALUES_COEFFICIENT_POSITIVE = "coefficient > 0"
CK_VALUES_SOURCE_NOT_BLANK = "btrim(source) <> ''"


def upgrade() -> None:
    op.create_table(
        "inflation_series",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inflation_series"),
        sa.UniqueConstraint("name", name="uq_inflation_series_name"),
        sa.CheckConstraint(CK_SERIES_NAME_NOT_BLANK, name="ck_inflation_series_name_not_blank"),
    )
    op.create_table(
        "inflation_index_values",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("coefficient", sa.Numeric(), nullable=False),
        sa.Column("is_forecast", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inflation_index_values"),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["inflation_series.id"],
            name="fk_inflation_index_values_series_id",
        ),
        sa.UniqueConstraint(
            "series_id", "year", name="uq_inflation_index_values_series_year"
        ),
        sa.CheckConstraint(
            CK_VALUES_COEFFICIENT_POSITIVE,
            name="ck_inflation_index_values_coefficient_positive",
        ),
        sa.CheckConstraint(
            CK_VALUES_SOURCE_NOT_BLANK, name="ck_inflation_index_values_source_not_blank"
        ),
    )


def downgrade() -> None:
    op.drop_table("inflation_index_values")
    op.drop_table("inflation_series")

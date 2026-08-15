"""Полезная площадь объекта.

Третья площадь — вводимая, как надземная и подземная, но в `area_total_sp` она
НЕ входит: полезная — часть общей, а не третье слагаемое (спека §2.2). Выражение
генерируемой колонки поэтому не трогается ни на символ. Причина не вкусовая: по
`area_total_sp` считается ₽/м² в паспорте проекта, в паспорте фазы 6 и в отчётах,
и сделать полезную слагаемым значило бы сменить знаменатель всех удельных
показателей задним числом.

`CHECK` «не больше общей» записан через СЛАГАЕМЫЕ, а не через `area_total_sp`
(спека §2.3): результат тот же, а зависимости от того, разрешает ли PostgreSQL
ссылку на генерируемую колонку в `CHECK` другой колонки, нет — выяснять это
замером пришлось бы ради нулевой выгоды.

Парой с надземной и подземной полезная НЕ связана (§2.4): её можно завести
отдельно, и наоборот. Следствие названо границей: при `NULL`-паре сумма
слагаемых `NULL`, `CHECK` даёт `NULL` и ПРОПУСКАЕТ — полезная без общей проходит
без сравнения. Данные приходят кусками, и запрещать ввод полезной до ввода пары
дороже, чем терпеть непроверяемый случай.

Выражения дублируют models.py намеренно: миграция обязана быть неизменной во
времени (то же правило, что у 0004-0009). Расхождение ловят parity-тесты
test_schema_constraints.py — `alembic check` для CHECK-выражений бесполезен.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-15
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

CK_AREA_USEFUL_NON_NEGATIVE = "area_useful_sp IS NULL OR area_useful_sp >= 0"
CK_AREA_USEFUL_WITHIN_TOTAL = (
    "area_useful_sp IS NULL "
    "OR area_useful_sp <= area_aboveground_sp + area_underground_sp"
)


def upgrade() -> None:
    op.add_column("objects", sa.Column("area_useful_sp", sa.Numeric(), nullable=True))
    op.create_check_constraint(
        "ck_objects_area_useful_sp_non_negative", "objects", CK_AREA_USEFUL_NON_NEGATIVE
    )
    op.create_check_constraint(
        "ck_objects_area_useful_sp_within_total", "objects", CK_AREA_USEFUL_WITHIN_TOTAL
    )


def downgrade() -> None:
    op.drop_constraint("ck_objects_area_useful_sp_within_total", "objects", type_="check")
    op.drop_constraint("ck_objects_area_useful_sp_non_negative", "objects", type_="check")
    op.drop_column("objects", "area_useful_sp")

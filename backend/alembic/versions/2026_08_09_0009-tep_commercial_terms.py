"""ТЭП объекта и коммерческие условия договора.

Общая площадь ВЫЧИСЛЯЕТСЯ, а не хранится (спека §2.2): решение пользователя —
два числа на входе, третье на выходе. Цена решения названа границей §5 п. 1:
опечатка в слагаемом ничем не ловится, сверять сумму не с чем.

Части `>= 0`, целое `> 0` — армы разные по причине, а не по недосмотру.
Объект без подземной части законен и его подземная площадь равна НУЛЮ, а не
«неизвестна» (различение, за которое Ф4б заплатила четырьмя снятиями).
А ноль в общей — это знаменатель руб/м², и деление на ноль в Ф6 закрывается
здесь, схемой, а не проверкой в коде паспорта.

Выражения дублируют models.py намеренно: миграция обязана быть неизменной во
времени (то же правило, что у 0004-0008). Расхождение ловят parity-тесты
test_schema_constraints.py — `alembic check` для Computed и CHECK бесполезен.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

AREA_TOTAL_EXPRESSION = "area_aboveground_sp + area_underground_sp"

CK_AREA_ABOVE = "area_aboveground_sp IS NULL OR area_aboveground_sp >= 0"
CK_AREA_UNDER = "area_underground_sp IS NULL OR area_underground_sp >= 0"
CK_AREA_TOTAL = "area_total_sp IS NULL OR area_total_sp > 0"
# Для пары тождественно num_nonnulls(...) IN (0, 2), но не зависит от системной
# функции и повторяет идиому 0006. Выражение никогда не даёт NULL.
CK_AREA_PAIR = "(area_aboveground_sp IS NULL) = (area_underground_sp IS NULL)"

_PCT_FIELDS = ("advance_pct", "bank_guarantee_pct", "retention_pct")
_NOTE_FIELDS = ("advance_note", "bank_guarantee_note", "retention_note")


def _ck_pct(name: str) -> str:
    return f"{name} IS NULL OR ({name} >= 0 AND {name} <= 100)"


def upgrade() -> None:
    op.add_column("objects", sa.Column("area_aboveground_sp", sa.Numeric(), nullable=True))
    op.add_column("objects", sa.Column("area_underground_sp", sa.Numeric(), nullable=True))
    op.add_column(
        "objects",
        sa.Column(
            "area_total_sp",
            sa.Numeric(),
            sa.Computed(AREA_TOTAL_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_objects_area_aboveground_sp_non_negative", "objects", CK_AREA_ABOVE
    )
    op.create_check_constraint(
        "ck_objects_area_underground_sp_non_negative", "objects", CK_AREA_UNDER
    )
    op.create_check_constraint("ck_objects_area_total_sp_positive", "objects", CK_AREA_TOTAL)
    op.create_check_constraint("ck_objects_areas_both_or_neither", "objects", CK_AREA_PAIR)

    for name in _PCT_FIELDS:
        op.add_column("contracts", sa.Column(name, sa.Numeric(), nullable=True))
        op.create_check_constraint(f"ck_contracts_{name}_range", "contracts", _ck_pct(name))
    for name in _NOTE_FIELDS:
        op.add_column("contracts", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    for name in _NOTE_FIELDS:
        op.drop_column("contracts", name)
    for name in _PCT_FIELDS:
        op.drop_constraint(f"ck_contracts_{name}_range", "contracts", type_="check")
        op.drop_column("contracts", name)

    op.drop_constraint("ck_objects_areas_both_or_neither", "objects", type_="check")
    op.drop_constraint("ck_objects_area_total_sp_positive", "objects", type_="check")
    op.drop_constraint("ck_objects_area_underground_sp_non_negative", "objects", type_="check")
    op.drop_constraint("ck_objects_area_aboveground_sp_non_negative", "objects", type_="check")

    # Вычисляемая колонка снимается ПЕРВОЙ: она зависит от обоих слагаемых.
    # Порядок замерен шагом 6 плана, а не выведен из документации.
    op.drop_column("objects", "area_total_sp")
    op.drop_column("objects", "area_underground_sp")
    op.drop_column("objects", "area_aboveground_sp")

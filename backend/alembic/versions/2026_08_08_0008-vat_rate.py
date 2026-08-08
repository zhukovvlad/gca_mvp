"""Ставка НДС из шапки ценового блока: колонка `proposals.vat_rate`.

Ставка заявлена в шапке ценового блока подрядчика (спека Ф4б §1.1) и потому
является фактом файла, а не нашим выводом (§2.4): деление по блоку итогов
служит перекрёстной проверкой и никогда — источником значения, поэтому
колонки `vat_rate_source` в схеме нет — источник у сохраняемого значения ровно
один.

**Хранение — в процентных пунктах** (`20`, не `0.20`, §2.9): так же читается
человеком в БД, как написано в файле.

**`CHECK` — запрет непредставимого состояния, а не основной фильтр.** Импорт
делает собственную защитную конверсию (`Decimal`, `is_finite()`, диапазон
0..100) ДО вставки строки: `_money` для ставки не годится, потому что
`Decimal("NaN")` и `Decimal("Infinity")` конструируются без исключения, а
`'NaN'::numeric` в PostgreSQL больше любого числа — `CHECK` дал бы
`IntegrityError` и уронил бы весь импорт вместо записи `NULL` (спека §2.9).
`CHECK` при этом остаётся: он стережёт непредставимое состояние на случай
правки мимо приложения, прямо в psql, — тот же принцип, что у
`ck_rate_standards_rate_positive`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Выражение дублирует models.Proposal намеренно: миграция обязана быть
# неизменной во времени (то же правило, что у литералов в 0004-0007).
# Расхождение ловят parity-тесты test_schema_constraints.py.
CK_VAT_RATE = "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 100)"


def upgrade() -> None:
    op.add_column("proposals", sa.Column("vat_rate", sa.Numeric(), nullable=True))
    op.create_check_constraint("ck_proposals_vat_rate", "proposals", CK_VAT_RATE)


def downgrade() -> None:
    op.drop_constraint("ck_proposals_vat_rate", "proposals", type_="check")
    op.drop_column("proposals", "vat_rate")

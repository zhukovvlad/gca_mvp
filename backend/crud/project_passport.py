"""Паспорт проекта по статьям классификатора (фаза 7, Ф6).

Модуль отдельный от `crud/analytics.py` намеренно. Там код фазы 6 со своим
правилом «последняя смета» (`AGENTS.md` §6); здесь — правило «исходная смета»
(спека Ф6 §2.4). Это два разных правила, и вторая копия первого была бы
дефектом, а не экономией. Спека §2.1 требует код фазы 6 не трогать.
"""
from __future__ import annotations

import sqlalchemy as sa

# ---------------------------------------------------------------------------
#  VIEW прямых сумм по статьям как объект SQLAlchemy
# ---------------------------------------------------------------------------

#: `v_category_totals` создаётся raw SQL в миграции 0010 и потому невидим для
#: `Base.metadata` — ровно как `v_position_deviations` фазы 2. Отражение
#: объявлено здесь, чтобы запросы собирались `select()`-ом, а не склеивались из
#: строк: строковый SQL не проверяется ничем до попадания в БД.
#:
#: Цена отражения — оно может разъехаться с настоящим VIEW. Это ловит
#: `test_category_totals_view.py::test_declared_view_columns_match_the_database`,
#: сверяя объявление с `information_schema`; без сверки переименованная в
#: миграции колонка проявилась бы ошибкой выполнения на живом стенде.
CATEGORY_TOTALS = sa.table(
    "v_category_totals",
    sa.column("estimate_id", sa.BigInteger),
    sa.column("work_category_id", sa.BigInteger),
    sa.column("source", sa.Text),
    sa.column("amount", sa.Numeric),
    sa.column("row_count", sa.Integer),
    sa.column("rows_with_amount", sa.Integer),
    sa.column("rows_not_finite", sa.Integer),
)

#: Объявленный порядок колонок — он же ожидаемый в БД (`ordinal_position`).
DECLARED_CATEGORY_TOTALS_COLUMNS = tuple(c.name for c in CATEGORY_TOTALS.columns)

#: Значения колонки `source`. В SQL миграции они литералы (миграция неизменна во
#: времени), здесь — имена для читающего кода.
SOURCE_POSITIONS = "positions"
SOURCE_ADDITIONAL_WORKS = "additional_works"

"""Предикат цены в VIEW отклонений: `unit_cost_total IS NOT NULL` → «конечно и
больше нуля» (спека правила цены, §2.3, §1.2; `docs/superpowers/specs/
2026-09-09-price-predicate-design.md`; AGENTS.md §4).

Задача 4 плана `fix/price-predicate` — последняя из четырёх, готовящих это
изменение (план, «Решения плана», п. 3): агрегаты матрицы (`crud/analytics.py`,
`_cell_groups_cte`/`_cell_weights_cte`) и позиционные читатели (`key_rates`
паспорта, drill-down — `_priced_positions_select`/`_all_positions_select`) уже
применяют `_price_ok`/`_weight_ok` САМИ, поверх `PositionItem`/`DEVIATION_
INPUTS`, а не полагаются на фильтр VIEW (задачи 2 и 3, коммиты `9ab55ab`,
`8c981ef`). Поэтому эта миграция не меняет поведение читателей — она лишь
убирает последнее место, где старое условие (`IS NOT NULL`) ещё жило текстом
VIEW, и делает `v_position_deviation_inputs` согласованным с правилом цены
само по себе, а не только через читателей.

**Что меняется.** Условие `pi.unit_cost_total IS NOT NULL` заменяется тем же
предикатом, что `crud.analytics._price_ok`: значение конечно и больше нуля.
Перечисление нефинитных значений записано тем же приёмом, что уже применяет
`v_category_totals` в самой миграции 0012 (`<> 'NaN'::numeric` и далее по
списку) — второго синтаксиса для одного и того же факта не заводится.
Состав и порядок колонок VIEW не меняются ни на одну колонку — правится
только `WHERE`.

**Член `pi.unit_cost_total <> '-Infinity'::numeric` инертен, и это факт, а не
недоработка.** В PostgreSQL `'-Infinity'::numeric > 0` уже ложно само по
себе, поэтому условие `pi.unit_cost_total > 0` одно отсекает `-Infinity` и
без соседа по перечислению; убрать этот член из текста ниже — и результат
`upgrade()` не изменится ни на одной строке ни на одном входе (проверено
руками при разработке: временное удаление члена не красит ни один тест).
Член оставлен, потому что перечисление заявляет полноту — «все три нефинитных
значения исключены явно», а не «эти два, а третье и так не пройдёт», и это
тот же довод, что уже держит одноимённый член в `_not_finite`
(`crud/analytics.py`) и в `V_CATEGORY_TOTALS_WITH_PROPOSAL` миграции 0012.

**`downgrade` восстанавливает текст миграции 0012 дословно** (правило §9:
миграции задним числом не правятся). `V_POSITION_DEVIATION_INPUTS_0012` ниже —
копия константы `V_POSITION_DEVIATION_INPUTS` из
`2026_08_13_0012-vat_rate_recalculation.py`, дословная КАК ЗНАЧЕНИЕ строки
(проверено в тесте `test_deviations_view.py::TestFrozenCopyMatchesMigration
0012` сравнением живых объектов, импортированных из обоих файлов, — не
считается вручную и не сверяется одноразово). Уточнение по формулировке:
файл 0012 хранится в CRLF, этот файл — в LF, поэтому побайтового совпадения
самих ФАЙЛОВ на диске нет и не обещается; Python нормализует переносы строк
внутри тройных кавычек ДО компиляции (universal newlines) одинаково для
обоих окончаний, и потому совпадают именно распакованные строковые
константы, что и есть предмет DoD 2. Исходный файл 0012 этим изменением не
тронут ни одним символом.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-09
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

V_POSITION_DEVIATION_INPUTS = """
CREATE VIEW v_position_deviation_inputs AS
SELECT
    pi.id                                            AS position_item_id,
    pi.proposal_id,
    p.lot_id,
    l.estimate_id,
    e.contract_id,
    c.object_id,
    c.rate_class_id,
    pi.catalog_position_id,
    pi.job_title_in_proposal,
    pi.unit_id,
    pi.unit_cost_total,
    COALESCE(pi.suggested_quantity, pi.quantity)     AS weight,
    COALESCE(e.data_prepared_on_date, c.signed_date) AS comparison_date,
    rs.id                                            AS rate_standard_id,
    rs.standard_unit_rate,
    COALESCE(e.vat_rate_base_override, p.vat_rate)   AS vat_rate_base,
    e.vat_rate_target
FROM position_items pi
JOIN proposals        p  ON p.id  = pi.proposal_id
JOIN lots             l  ON l.id  = p.lot_id
JOIN estimates        e  ON e.id  = l.estimate_id
JOIN contracts        c  ON c.id  = e.contract_id
JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
LEFT JOIN rate_standards rs
       ON rs.catalog_position_id = pi.catalog_position_id
      AND rs.rate_class_id       = c.rate_class_id
      AND daterange(rs.valid_from, rs.valid_to, '[)')
          @> COALESCE(e.data_prepared_on_date, c.signed_date)
WHERE pi.is_chapter = false
  AND pi.unit_cost_total > 0
  AND pi.unit_cost_total <> 'NaN'::numeric
  AND pi.unit_cost_total <> 'Infinity'::numeric
  AND pi.unit_cost_total <> '-Infinity'::numeric
  AND cp.kind = 'POSITION'
"""

# Дословная копия V_POSITION_DEVIATION_INPUTS из миграции 0012 — нужна для
# downgrade (§9: миграции задним числом не правятся). Текст файла 0012 этим
# изменением не тронут.
V_POSITION_DEVIATION_INPUTS_0012 = """
CREATE VIEW v_position_deviation_inputs AS
SELECT
    pi.id                                            AS position_item_id,
    pi.proposal_id,
    p.lot_id,
    l.estimate_id,
    e.contract_id,
    c.object_id,
    c.rate_class_id,
    pi.catalog_position_id,
    pi.job_title_in_proposal,
    pi.unit_id,
    pi.unit_cost_total,
    COALESCE(pi.suggested_quantity, pi.quantity)     AS weight,
    COALESCE(e.data_prepared_on_date, c.signed_date) AS comparison_date,
    rs.id                                            AS rate_standard_id,
    rs.standard_unit_rate,
    COALESCE(e.vat_rate_base_override, p.vat_rate)   AS vat_rate_base,
    e.vat_rate_target
FROM position_items pi
JOIN proposals        p  ON p.id  = pi.proposal_id
JOIN lots             l  ON l.id  = p.lot_id
JOIN estimates        e  ON e.id  = l.estimate_id
JOIN contracts        c  ON c.id  = e.contract_id
JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
LEFT JOIN rate_standards rs
       ON rs.catalog_position_id = pi.catalog_position_id
      AND rs.rate_class_id       = c.rate_class_id
      AND daterange(rs.valid_from, rs.valid_to, '[)')
          @> COALESCE(e.data_prepared_on_date, c.signed_date)
WHERE pi.is_chapter = false
  AND pi.unit_cost_total IS NOT NULL
  AND cp.kind = 'POSITION'
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_position_deviation_inputs")
    op.execute(V_POSITION_DEVIATION_INPUTS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_position_deviation_inputs")
    op.execute(V_POSITION_DEVIATION_INPUTS_0012)

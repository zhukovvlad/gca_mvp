"""VIEW v_category_totals — прямые суммы по статьям классификатора.

Фаза 7, Ф6. Гранулярность — ПРЯМЫЕ суммы, без roll-up (спека §2.2): экрану
нужны обе величины сразу — итог поддерева и собственные деньги родителя, — а
выводить собственные разностью «родитель минус дети» значит считать вычитанием
то, что и так есть в источнике. Roll-up делает Python.

Путь позиции до статьи — ТОЛЬКО через `chapter_item_id`. Join по номеру раздела
запрещён (спека §1.5, запрет Ф3): в файле строка с номером раздела 6.4 несёт
статью 6.6, то есть нумерация файла и коды классификатора — разные оси.
Итогам, записанным в самих строках-разделах, не верим: сумма статьи — это
сумма её позиций.

Фильтра по `amendment_no` здесь НЕТ намеренно (спека §2.2): VIEW с именем
«итоги по статьям», молча игнорирующий половину смет, — тот же класс ловушки,
что `total_cost_with_vat` со значением «без НДС», за который заплатила Ф4a.
Выбор исходной сметы делает CRUD явным условием.

Фильтр годности в `amount` — защита, а не перестраховка (замер спеки §1.11):
`numeric` принимает `NaN`, `Infinity` и `-Infinity`, а `_money` при импорте их
не отсекает (открытый хвост Ф4), и `CHECK`-а на `position_items.total_cost_total`
нет. Одна такая строка превратила бы в `NaN` сумму статьи, все её родительские
суммы, итог договора, все доли и все сектора кольца разом, а пара
противоположных бесконечностей даёт `NaN` даже вдвоём. Негодные строки уходят в
собственный счётчик — факт порчи становится громким, а не молча съеденным.
Сравнение `x = 'NaN'::numeric` в PostgreSQL истинно, поэтому фильтр выразим.

Литералы записаны строками: миграция обязана быть неизменной во времени (то же
правило, что у 0002–0009).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-10
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

V_CATEGORY_TOTALS = """
CREATE VIEW v_category_totals AS
SELECT
    l.estimate_id,
    ch.work_category_id,
    'positions'::text AS source,
    SUM(pi.total_cost_total) FILTER (
        WHERE pi.total_cost_total <> 'NaN'::numeric
          AND pi.total_cost_total <> 'Infinity'::numeric
          AND pi.total_cost_total <> '-Infinity'::numeric
    ) AS amount,
    COUNT(*)::int AS row_count,
    COUNT(*) FILTER (
        WHERE pi.total_cost_total IS NOT NULL
          AND pi.total_cost_total <> 'NaN'::numeric
          AND pi.total_cost_total <> 'Infinity'::numeric
          AND pi.total_cost_total <> '-Infinity'::numeric
    )::int AS rows_with_amount,
    COUNT(*) FILTER (
        WHERE pi.total_cost_total IS NOT NULL
          AND NOT (
                  pi.total_cost_total <> 'NaN'::numeric
              AND pi.total_cost_total <> 'Infinity'::numeric
              AND pi.total_cost_total <> '-Infinity'::numeric
          )
    )::int AS rows_not_finite
FROM position_items pi
JOIN proposals p ON p.id = pi.proposal_id
JOIN lots      l ON l.id = p.lot_id
LEFT JOIN position_items ch
       ON ch.id = pi.chapter_item_id
      AND ch.proposal_id = pi.proposal_id
WHERE pi.is_chapter = false
GROUP BY l.estimate_id, ch.work_category_id
UNION ALL
SELECT
    l.estimate_id,
    aw.work_category_id,
    'additional_works'::text AS source,
    SUM(aw.total_amount) FILTER (
        WHERE aw.total_amount <> 'NaN'::numeric
          AND aw.total_amount <> 'Infinity'::numeric
          AND aw.total_amount <> '-Infinity'::numeric
    ) AS amount,
    COUNT(*)::int AS row_count,
    COUNT(*) FILTER (
        WHERE aw.total_amount IS NOT NULL
          AND aw.total_amount <> 'NaN'::numeric
          AND aw.total_amount <> 'Infinity'::numeric
          AND aw.total_amount <> '-Infinity'::numeric
    )::int AS rows_with_amount,
    COUNT(*) FILTER (
        WHERE aw.total_amount IS NOT NULL
          AND NOT (
                  aw.total_amount <> 'NaN'::numeric
              AND aw.total_amount <> 'Infinity'::numeric
              AND aw.total_amount <> '-Infinity'::numeric
          )
    )::int AS rows_not_finite
FROM estimate_additional_works aw
JOIN proposals p ON p.id = aw.proposal_id
JOIN lots      l ON l.id = p.lot_id
GROUP BY l.estimate_id, aw.work_category_id
"""


def upgrade() -> None:
    op.execute(V_CATEGORY_TOTALS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_category_totals")

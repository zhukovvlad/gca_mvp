"""Ручные ставки НДС на смете, нетто-ось сравнения.

Три изменения схемы, все — следствия спеки:

1. Четыре колонки на `estimates`: база, назначенная человеком; ставка показа;
   автор и время последней правки. `proposals.vat_rate` не трогается — он
   остаётся неприкосновенным фактом файла. Тип автора — `integer`, как
   `users.id` (то же уточнение, что в 0011).

2. `v_position_deviations` → `v_position_deviation_inputs`. Колонка
   `deviation_pct` УБРАНА: она считалась на валовой цене, а норматив объявлен
   ценой без НДС, и оставленная колонка стала бы молча неверной. Имя меняется
   вместе со смыслом — VIEW без отклонения, названный `…_deviations`, это тот
   же класс ошибки, что `total_cost_with_vat` со значением «без НДС».

3. `v_category_totals` группируется ДОПОЛНИТЕЛЬНО по предложению и его базовой
   ставке — до уровня, на котором множитель пересчёта постоянен.

Литералы записаны строками: миграция обязана быть неизменной во времени.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
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
  AND pi.unit_cost_total IS NOT NULL
  AND cp.kind = 'POSITION'
"""

# Дословная копия объявления из миграции 0002 — нужна для downgrade.
V_POSITION_DEVIATIONS = """
CREATE VIEW v_position_deviations AS
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
    (pi.unit_cost_total / rs.standard_unit_rate - 1) * 100 AS deviation_pct
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

V_CATEGORY_TOTALS_WITH_PROPOSAL = """
CREATE VIEW v_category_totals AS
SELECT
    l.estimate_id,
    p.id AS proposal_id,
    COALESCE(e.vat_rate_base_override, p.vat_rate) AS vat_rate_base,
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
JOIN estimates e ON e.id = l.estimate_id
LEFT JOIN position_items ch
       ON ch.id = pi.chapter_item_id
      AND ch.proposal_id = pi.proposal_id
WHERE pi.is_chapter = false
GROUP BY l.estimate_id, p.id, COALESCE(e.vat_rate_base_override, p.vat_rate),
         ch.work_category_id
UNION ALL
SELECT
    l.estimate_id,
    p.id AS proposal_id,
    COALESCE(e.vat_rate_base_override, p.vat_rate) AS vat_rate_base,
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
JOIN estimates e ON e.id = l.estimate_id
GROUP BY l.estimate_id, p.id, COALESCE(e.vat_rate_base_override, p.vat_rate),
         aw.work_category_id
"""

# Дословная копия объявления из миграции 0010 — нужна для downgrade.
V_CATEGORY_TOTALS_0010 = """
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
    op.add_column("estimates", sa.Column("vat_rate_base_override", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_target", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_updated_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "estimates", sa.Column("vat_rate_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_estimates_vat_rate_base_override",
        "estimates",
        "vat_rate_base_override IS NULL "
        "OR (vat_rate_base_override >= 0 AND vat_rate_base_override <= 100)",
    )
    op.create_check_constraint(
        "ck_estimates_vat_rate_target",
        "estimates",
        "vat_rate_target IS NULL OR (vat_rate_target >= 0 AND vat_rate_target <= 100)",
    )
    op.create_foreign_key(
        "fk_estimates_vat_rate_updated_by_id",
        "estimates",
        "users",
        ["vat_rate_updated_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute("DROP VIEW IF EXISTS v_position_deviations")
    op.execute(V_POSITION_DEVIATION_INPUTS)
    op.execute("DROP VIEW IF EXISTS v_category_totals")
    op.execute(V_CATEGORY_TOTALS_WITH_PROPOSAL)


def downgrade() -> None:
    # VIEW снимаются ДО колонок: оба ссылаются на vat_rate_base_override.
    op.execute("DROP VIEW IF EXISTS v_category_totals")
    op.execute("DROP VIEW IF EXISTS v_position_deviation_inputs")
    op.drop_constraint("fk_estimates_vat_rate_updated_by_id", "estimates", type_="foreignkey")
    op.drop_constraint("ck_estimates_vat_rate_target", "estimates", type_="check")
    op.drop_constraint("ck_estimates_vat_rate_base_override", "estimates", type_="check")
    op.drop_column("estimates", "vat_rate_updated_at")
    op.drop_column("estimates", "vat_rate_updated_by_id")
    op.drop_column("estimates", "vat_rate_target")
    op.drop_column("estimates", "vat_rate_base_override")
    op.execute(V_CATEGORY_TOTALS_0010)
    op.execute(V_POSITION_DEVIATIONS)

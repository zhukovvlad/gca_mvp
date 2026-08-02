"""Domain schema: contracts, estimates, catalog, rate standards, import jobs.

Фаза 2 по AGENTS.md §9: перенос таблиц tenders-go (@121718bf45df,
migration/000001_init_schema_consolidated + 000002_add_fts_to_catalog) с
переименованием tenders → estimates, плюс новые сущности контура ГП —
contracts, rate_classes, rate_standards, import_jobs, estimate_raw_data,
catalog_positions.normalized_job_title и VIEW v_position_deviations.

Расширения vector и btree_gist включены миграцией 0001.

Что НЕ переносится из tenders-go (вне скоупа MVP, AGENTS.md §2):
  tender_types / tender_chapters / tender_categories — рубрикатор тендеров;
  executors, persons, winners — роли тендерного процесса;
  lots_md_documents / lots_chunks — RAG-инфраструктура;
  suggested_merges (+ миграции 000003–000008 поверх неё) — воркфлоу слияний;
  users / user_sessions — auth свой, из udp-tenders (миграция 0001).

Raw SQL через op.execute() — там, где Alembic не выражает объект декларативно
(AGENTS.md §11); downgrade снимает их явно, до DROP TABLE.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02
"""
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Литералы статусов дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы.
CATALOG_KINDS = "'POSITION', 'HEADER', 'LOT_HEADER', 'TRASH', 'TO_REVIEW'"
CATALOG_STATUSES = "'pending_indexing', 'active', 'deprecated', 'archived', 'na'"
IMPORT_JOB_STATUSES = "'pending', 'parsing', 'importing', 'matching', 'done', 'error'"
IMPORT_JOB_TERMINAL = "'done', 'error'"

FTS_EXPRESSION = "to_tsvector('simple'::regconfig, COALESCE(standard_job_title, ''::text))"

# VIEW отклонений (AGENTS.md §4).
#   * дата сравнения — data_prepared_on_date сметы, фолбэк signed_date договора;
#   * норматив ищется по классу ДОГОВОРА (снимок), не объекта;
#   * благодаря EXCLUDE на rate_standards LEFT JOIN даёт максимум одну строку;
#   * нет норматива → deviation_pct = NULL (в UI «нет норматива», не 0);
#   * исключены: разделы, строки без цены и позиции с каталожной строкой
#     kind <> 'POSITION' (в т.ч. ещё не сматченные — INNER JOIN отсекает NULL).
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


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    # --- справочники ---------------------------------------------------------
    op.create_table(
        "rate_classes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("title", name="uq_rate_classes_title"),
    )

    op.create_table(
        "objects",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column(
            "rate_class_id",
            sa.BigInteger(),
            sa.ForeignKey("rate_classes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("title", name="uq_objects_title"),
    )
    op.create_index("ix_objects_rate_class_id", "objects", ["rate_class_id"])

    op.create_table(
        "contractors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("inn", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column("accreditation", sa.String(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("inn", name="uq_contractors_inn"),
    )

    # --- договор -------------------------------------------------------------
    op.create_table(
        "contracts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("object_id", sa.BigInteger(), sa.ForeignKey("objects.id"), nullable=False),
        sa.Column(
            "contractor_id", sa.BigInteger(), sa.ForeignKey("contractors.id"), nullable=False
        ),
        # Снимок класса на момент создания договора (AGENTS.md §4).
        sa.Column(
            "rate_class_id", sa.BigInteger(), sa.ForeignKey("rate_classes.id"), nullable=False
        ),
        sa.Column("contract_number", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("signer", sa.String(), nullable=True),
        sa.Column("signed_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("contract_number", name="uq_contracts_contract_number"),
        sa.CheckConstraint(
            "total_amount IS NULL OR total_amount >= 0",
            name="ck_contracts_total_amount_non_negative",
        ),
    )
    op.create_index("ix_contracts_object_id", "contracts", ["object_id"])
    op.create_index("ix_contracts_contractor_id", "contracts", ["contractor_id"])
    op.create_index("ix_contracts_rate_class_id", "contracts", ["rate_class_id"])

    # --- задания импорта -----------------------------------------------------
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("contract_id", sa.BigInteger(), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("amendment_no", sa.Integer(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("positions_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("matched_cache", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("matched_exact", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "matched_nonposition", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("to_review", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _created_at(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("file_key", name="uq_import_jobs_file_key"),
        sa.CheckConstraint(f"status IN ({IMPORT_JOB_STATUSES})", name="ck_import_jobs_status"),
        sa.CheckConstraint(
            "amendment_no IS NULL OR amendment_no > 0", name="ck_import_jobs_amendment_no"
        ),
    )
    op.create_index("ix_import_jobs_contract_id", "import_jobs", ["contract_id", "amendment_no"])
    op.create_index(
        "ix_import_jobs_active",
        "import_jobs",
        ["id"],
        postgresql_where=sa.text(f"status NOT IN ({IMPORT_JOB_TERMINAL})"),
    )

    # --- сметы ---------------------------------------------------------------
    op.create_table(
        "estimates",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("contract_id", sa.BigInteger(), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("amendment_no", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("data_prepared_on_date", sa.Date(), nullable=True),
        sa.Column(
            "import_job_id",
            sa.BigInteger(),
            sa.ForeignKey("import_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "amendment_no IS NULL OR amendment_no > 0", name="ck_estimates_amendment_no"
        ),
    )
    # Индекс по contract_id не создаётся: uq_estimates_contract_amendment
    # (см. raw SQL ниже) — полный индекс с ведущей contract_id, дубль по
    # ведущей колонке дал бы только write-амплификацию.
    op.create_index("ix_estimates_import_job_id", "estimates", ["import_job_id"])

    op.create_table(
        "estimate_raw_data",
        sa.Column(
            "estimate_id",
            sa.BigInteger(),
            sa.ForeignKey("estimates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        _created_at(),
    )

    # --- лоты и предложения --------------------------------------------------
    # FK на estimates — ON DELETE CASCADE (в tenders-go было без каскада):
    # replace-флоу удаляет смету целиком (AGENTS.md §5, правило 3).
    op.create_table(
        "lots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "estimate_id",
            sa.BigInteger(),
            sa.ForeignKey("estimates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lot_key", sa.String(), nullable=False),
        sa.Column("lot_title", sa.String(), nullable=False),
        sa.Column("lot_key_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("estimate_id", "lot_key", name="uq_lots_estimate_lot_key"),
    )
    op.create_index(
        "idx_gin_lots_key_parameters", "lots", ["lot_key_parameters"], postgresql_using="gin"
    )

    op.create_table(
        "proposals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "lot_id", sa.BigInteger(), sa.ForeignKey("lots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "contractor_id", sa.BigInteger(), sa.ForeignKey("contractors.id"), nullable=False
        ),
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("contractor_coordinate", sa.String(255), nullable=True),
        sa.Column("contractor_width", sa.Integer(), nullable=True),
        sa.Column("contractor_height", sa.Integer(), nullable=True),
        _created_at(),
        _updated_at(),
        # Ровно одно предложение на лот (AGENTS.md §4): единственный подрядчик.
        sa.UniqueConstraint("lot_id", name="uq_proposals_lot_id"),
    )
    op.create_index("ix_proposals_contractor_id", "proposals", ["contractor_id"])

    op.create_table(
        "proposal_additional_info",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.BigInteger(),
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("info_key", sa.Text(), nullable=False),
        sa.Column("info_value", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("proposal_id", "info_key", name="uq_proposal_additional_info_key"),
    )

    op.create_table(
        "proposal_summary_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.BigInteger(),
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary_key", sa.Text(), nullable=False),
        sa.Column("job_title", sa.Text(), nullable=False),
        sa.Column("materials_cost", sa.Numeric(), nullable=True),
        sa.Column("works_cost", sa.Numeric(), nullable=True),
        sa.Column("indirect_costs_cost", sa.Numeric(), nullable=True),
        sa.Column("total_cost", sa.Numeric(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("proposal_id", "summary_key", name="uq_proposal_summary_lines_key"),
    )

    # --- каталожный контур ---------------------------------------------------
    op.create_table(
        "catalog_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("standard_job_title", sa.Text(), nullable=False),
        sa.Column("normalized_job_title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="TO_REVIEW"),
        sa.Column("status", sa.String(50), nullable=False, server_default="na"),
        sa.Column(
            "unit_id",
            sa.Integer(),
            sa.ForeignKey("units_of_measure.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "fts_vector",
            postgresql.TSVECTOR(),
            sa.Computed(FTS_EXPRESSION, persisted=True),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(f"kind IN ({CATALOG_KINDS})", name="ck_catalog_positions_kind"),
        sa.CheckConstraint(f"status IN ({CATALOG_STATUSES})", name="ck_catalog_positions_status"),
    )
    op.create_index(
        "ix_catalog_positions_standard_job_title", "catalog_positions", ["standard_job_title"]
    )
    op.create_index("idx_catalog_positions_kind", "catalog_positions", ["kind"])
    op.create_index("idx_cp_status", "catalog_positions", ["status"])
    op.create_index(
        "idx_cp_kind_review",
        "catalog_positions",
        ["id"],
        postgresql_where=sa.text("kind = 'TO_REVIEW'"),
    )
    op.create_index(
        "idx_cp_status_pending",
        "catalog_positions",
        ["id"],
        postgresql_where=sa.text("status = 'pending_indexing'"),
    )
    op.create_index(
        "idx_catalog_positions_fts", "catalog_positions", ["fts_vector"], postgresql_using="gin"
    )
    op.create_index(
        "idx_cp_kind_pos_hnsw",
        "catalog_positions",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("kind = 'POSITION'"),
    )

    op.create_table(
        "matching_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("norm_version", sa.SmallInteger(), nullable=False),
        sa.Column("job_title_text", sa.Text(), nullable=False),
        sa.Column("unit_text", sa.Text(), nullable=True),
        sa.Column(
            "catalog_position_id",
            sa.BigInteger(),
            sa.ForeignKey("catalog_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_matching_cache_source"),
        # Правило TTL целиком (AGENTS.md §4): автоматическая запись обязана
        # иметь срок, ручное решение из Review — обязано не иметь.
        sa.CheckConstraint(
            "(source = 'manual' AND expires_at IS NULL)"
            " OR (source = 'auto' AND expires_at IS NOT NULL)",
            name="ck_matching_cache_ttl_by_source",
        ),
    )
    op.create_index("idx_matching_cache_catalog_id", "matching_cache", ["catalog_position_id"])
    op.create_index("idx_matching_cache_expires_at", "matching_cache", ["expires_at"])

    op.create_table(
        "position_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.BigInteger(),
            sa.ForeignKey("proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "catalog_position_id",
            sa.BigInteger(),
            sa.ForeignKey("catalog_positions.id"),
            nullable=True,
        ),
        sa.Column("position_key_in_proposal", sa.String(255), nullable=False),
        sa.Column("comment_organizer", sa.Text(), nullable=True),
        sa.Column("comment_contractor", sa.Text(), nullable=True),
        sa.Column("item_number_in_proposal", sa.String(50), nullable=True),
        sa.Column("chapter_number_in_proposal", sa.String(50), nullable=True),
        sa.Column("job_title_in_proposal", sa.Text(), nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units_of_measure.id"), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("suggested_quantity", sa.Numeric(), nullable=True),
        sa.Column("total_cost_for_organizer_quantity", sa.Numeric(), nullable=True),
        sa.Column("unit_cost_materials", sa.Numeric(), nullable=True),
        sa.Column("unit_cost_works", sa.Numeric(), nullable=True),
        sa.Column("unit_cost_indirect_costs", sa.Numeric(), nullable=True),
        sa.Column("unit_cost_total", sa.Numeric(), nullable=True),
        sa.Column("total_cost_materials", sa.Numeric(), nullable=True),
        sa.Column("total_cost_works", sa.Numeric(), nullable=True),
        sa.Column("total_cost_indirect_costs", sa.Numeric(), nullable=True),
        sa.Column("total_cost_total", sa.Numeric(), nullable=True),
        sa.Column("deviation_from_baseline_cost", sa.Numeric(), nullable=True),
        sa.Column("is_chapter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("chapter_ref_in_proposal", sa.String(50), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint(
            "proposal_id", "position_key_in_proposal", name="uq_position_items_proposal_id_key"
        ),
    )
    op.create_index("idx_position_items_proposal_id", "position_items", ["proposal_id"])
    op.create_index("idx_position_items_catalog_id", "position_items", ["catalog_position_id"])
    op.create_index("idx_position_items_unit_id", "position_items", ["unit_id"])

    # --- нормативы -----------------------------------------------------------
    op.create_table(
        "rate_standards",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "catalog_position_id",
            sa.BigInteger(),
            sa.ForeignKey("catalog_positions.id"),
            nullable=False,
        ),
        sa.Column(
            "rate_class_id", sa.BigInteger(), sa.ForeignKey("rate_classes.id"), nullable=False
        ),
        sa.Column("standard_unit_rate", sa.Numeric(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("inflation_index", sa.Numeric(), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("standard_unit_rate > 0", name="ck_rate_standards_rate_positive"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_rate_standards_period"
        ),
        sa.CheckConstraint(
            "inflation_index IS NULL OR inflation_index > 0",
            name="ck_rate_standards_inflation_index",
        ),
    )
    op.create_index("ix_rate_standards_class", "rate_standards", ["rate_class_id"])

    # =====================================================================
    # Объекты, которые Alembic не выражает декларативно (AGENTS.md §11)
    # =====================================================================

    # Идентичность работы = нормализованная пара (название, единица).
    # COALESCE(unit_id,-1) — чтобы позиции без единицы тоже конфликтовали
    # между собой (в обычном UNIQUE два NULL различны). Этот же индекс —
    # арбитр ON CONFLICT в get-or-create матчинга (§5, шаг 4.3).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_catalog_positions_norm_unit
        ON catalog_positions (normalized_job_title, COALESCE(unit_id, -1))
        """
    )

    # PG16: NULLS NOT DISTINCT — исходную смету (amendment_no IS NULL) нельзя
    # завести дважды для одного договора.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_estimates_contract_amendment
        ON estimates (contract_id, amendment_no) NULLS NOT DISTINCT
        """
    )

    # Лок импорта: одновременно активен максимум один job на пару
    # (contract_id, amendment_no); разные допсоглашения одного договора
    # импортируются параллельно (§4).
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_pair
        ON import_jobs (contract_id, COALESCE(amendment_no, -1))
        WHERE status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )

    # Запрет пересечения периодов действия норматива для пары
    # (позиция, класс). NULL в valid_to → бесконечная верхняя граница,
    # COALESCE не нужен. btree_gist включён миграцией 0001.
    op.execute(
        """
        ALTER TABLE rate_standards
        ADD CONSTRAINT ex_rate_standards_no_overlap
        EXCLUDE USING gist (
            catalog_position_id WITH =,
            rate_class_id WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&
        )
        """
    )

    op.execute(V_POSITION_DEVIATIONS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_position_deviations")

    op.execute("ALTER TABLE rate_standards DROP CONSTRAINT IF EXISTS ex_rate_standards_no_overlap")
    op.execute("DROP INDEX IF EXISTS uq_import_jobs_active_pair")
    op.execute("DROP INDEX IF EXISTS uq_estimates_contract_amendment")
    op.execute("DROP INDEX IF EXISTS uq_catalog_positions_norm_unit")

    op.drop_table("rate_standards")
    op.drop_table("position_items")
    op.drop_table("matching_cache")
    op.drop_table("catalog_positions")
    op.drop_table("proposal_summary_lines")
    op.drop_table("proposal_additional_info")
    op.drop_table("proposals")
    op.drop_table("lots")
    op.drop_table("estimate_raw_data")
    op.drop_table("estimates")
    op.drop_table("import_jobs")
    op.drop_table("contracts")
    op.drop_table("contractors")
    op.drop_table("objects")
    op.drop_table("rate_classes")

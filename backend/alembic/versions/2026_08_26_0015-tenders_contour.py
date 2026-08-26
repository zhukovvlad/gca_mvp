"""Тендерный контур: тендеры, раунды, участники, предложения; три владельца сметы,
два владельца задания, аудит разбора, канон ИНН (спека 2026-08-26-tenders-contour-design.md §2.1–§2.2).

**Составные FK `offers → tender_rounds (id, tender_id)` и `→ offer_packages
(id, tender_id)`** держат ячейку в одном тендере структурно. Все три колонки
`offers` NOT NULL — PostgreSQL проверяет составной FK как MATCH SIMPLE, и NULL в
любой колонке отключил бы проверку.

**`RESTRICT` от пакета, `CASCADE` от раунда — осознанная асимметрия.** Пакет —
участник со всей историей; его удаление проходит только командой, которая
явно удаляет offers (спека §2.11).

**Пересечение частичных индексов — условие работоспособности.**
`uq_estimates_contract_amendment` объявлен NULLS NOT DISTINCT; при
`contract_id IS NULL` все сметы предложений схлопнулись бы в одну. Оба прежних
индекса пересоздаются с `WHERE contract_id IS NOT NULL`.

**Миграция данных ИНН — с отказом.** Канон (только ASCII-цифры) вычисляется
для каждого подрядчика; пустой канон либо два подрядчика с одним каноном —
отказ с обоими id, потому что схлопнуть их молча значит слить две компании.
CHECK добавляется ПОСЛЕ обновления.

**`downgrade` отказывает** по трём счётчикам отдельно: tenders, round-owned
import_jobs, estimates без contract_id. Каждый назван — откат обязан сказать,
что именно его держит (то же правило, что у 0011).

Выражения CHECK продублированы константами в models.py; parity —
test_tenders_schema.py.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-26
"""
import re

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

IMPORT_JOB_TERMINAL = "'done', 'error'"

CK_TENDERS_TITLE = "btrim(title) <> ''"
CK_TENDERS_NUMBER = "btrim(tender_number) <> ''"
CK_ROUNDS_STAGE_NO = "stage_no > 0"
CK_ESTIMATES_OWNER = "num_nonnulls(contract_id, offer_id, round_id) = 1"
CK_ESTIMATES_AMENDMENT_OWNER = "contract_id IS NOT NULL OR amendment_no IS NULL"
CK_PROPOSALS_BASELINE = (
    "(is_baseline = true AND contractor_id IS NULL) "
    "OR (is_baseline = false AND contractor_id IS NOT NULL)"
)
CK_JOBS_OWNER = "num_nonnulls(contract_id, round_id) = 1"
CK_JOBS_AMENDMENT_OWNER = "contract_id IS NOT NULL OR amendment_no IS NULL"
CK_JOBS_PARSED_PAIR = "(parsed_data IS NULL) = (parser_version IS NULL)"
CK_JOBS_ESTIMATES_CREATED = "estimates_created IS NULL OR estimates_created > 0"
CK_CONTRACTORS_INN = "inn ~ '^[0-9]+$'"

_NON_DIGIT = re.compile(r"[^0-9]")


def _canonicalize_existing_inns(bind) -> None:
    rows = bind.execute(sa.text("SELECT id, inn FROM contractors ORDER BY id")).all()
    seen: dict[str, int] = {}
    updates: list[tuple[int, str]] = []
    for contractor_id, inn in rows:
        canonical = _NON_DIGIT.sub("", inn or "")
        if not canonical:
            raise RuntimeError(
                f"Миграция 0015 невозможна: у подрядчика id={contractor_id} ИНН не содержит "
                "ни одной цифры. Исправьте карточку и повторите."
            )
        if canonical in seen:
            raise RuntimeError(
                f"Миграция 0015 невозможна: подрядчики id={seen[canonical]} и id={contractor_id} "
                f"после канонизации ИНН совпадают ({canonical}). Слить их — решение человека, "
                "не миграции."
            )
        seen[canonical] = contractor_id
        if canonical != inn:
            updates.append((contractor_id, canonical))
    for contractor_id, canonical in updates:
        bind.execute(
            sa.text("UPDATE contractors SET inn = :inn WHERE id = :id"),
            {"inn": canonical, "id": contractor_id},
        )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "tenders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("tender_number", sa.Text(), nullable=False),
        sa.Column("rate_class_id", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_tenders"),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], name="fk_tenders_object_id"),
        sa.ForeignKeyConstraint(["rate_class_id"], ["rate_classes.id"], name="fk_tenders_rate_class_id"),
        sa.UniqueConstraint("tender_number", name="uq_tenders_tender_number"),
        sa.CheckConstraint(CK_TENDERS_TITLE, name="ck_tenders_title_not_blank"),
        sa.CheckConstraint(CK_TENDERS_NUMBER, name="ck_tenders_number_not_blank"),
    )
    op.create_index("ix_tenders_object_id", "tenders", ["object_id"])
    op.create_index("ix_tenders_rate_class_id", "tenders", ["rate_class_id"])

    op.create_table(
        "tender_rounds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_no", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("held_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_tender_rounds"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE", name="fk_tender_rounds_tender_id"),
        sa.UniqueConstraint("tender_id", "stage_no", name="uq_tender_rounds_tender_stage"),
        sa.UniqueConstraint("id", "tender_id", name="uq_tender_rounds_id_tender"),
        sa.CheckConstraint(CK_ROUNDS_STAGE_NO, name="ck_tender_rounds_stage_no"),
    )

    op.create_table(
        "offer_packages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("contractor_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_offer_packages"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE", name="fk_offer_packages_tender_id"),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"], ondelete="RESTRICT", name="fk_offer_packages_contractor_id"),
        sa.UniqueConstraint("tender_id", "contractor_id", name="uq_offer_packages_tender_contractor"),
        sa.UniqueConstraint("id", "tender_id", name="uq_offer_packages_id_tender"),
    )
    op.create_index("ix_offer_packages_contractor_id", "offer_packages", ["contractor_id"])

    op.create_table(
        "offers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.ForeignKeyConstraint(
            ["round_id", "tender_id"], ["tender_rounds.id", "tender_rounds.tender_id"],
            ondelete="CASCADE", name="fk_offers_round",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "tender_id"], ["offer_packages.id", "offer_packages.tender_id"],
            ondelete="RESTRICT", name="fk_offers_package",
        ),
        sa.UniqueConstraint("round_id", "package_id", name="uq_offers_round_package"),
    )
    op.create_index("ix_offers_package_id", "offers", ["package_id"])

    # --- estimates: три владельца ---
    op.alter_column("estimates", "contract_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("estimates", sa.Column("offer_id", sa.BigInteger(), nullable=True))
    op.add_column("estimates", sa.Column("round_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_estimates_offer_id", "estimates", "offers", ["offer_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_estimates_round_id", "estimates", "tender_rounds", ["round_id"], ["id"], ondelete="CASCADE")
    op.create_check_constraint("ck_estimates_owner", "estimates", CK_ESTIMATES_OWNER)
    op.create_check_constraint("ck_estimates_amendment_owner", "estimates", CK_ESTIMATES_AMENDMENT_OWNER)
    op.execute("DROP INDEX uq_estimates_contract_amendment")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_estimates_contract_amendment
        ON estimates (contract_id, amendment_no) NULLS NOT DISTINCT
        WHERE contract_id IS NOT NULL
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_estimates_offer ON estimates (offer_id) WHERE offer_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_estimates_round ON estimates (round_id) WHERE round_id IS NOT NULL")

    # --- proposals: baseline без подрядчика ---
    op.alter_column("proposals", "contractor_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_check_constraint("ck_proposals_baseline_contractor", "proposals", CK_PROPOSALS_BASELINE)

    # --- import_jobs: два владельца, аудит разбора ---
    op.alter_column("import_jobs", "contract_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("import_jobs", sa.Column("round_id", sa.BigInteger(), nullable=True))
    op.add_column("import_jobs", sa.Column("parsed_data", JSONB(), nullable=True))
    op.add_column("import_jobs", sa.Column("parser_version", sa.Text(), nullable=True))
    op.add_column("import_jobs", sa.Column("estimates_created", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_import_jobs_round_id", "import_jobs", "tender_rounds", ["round_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_import_jobs_round_id", "import_jobs", ["round_id"])
    op.create_check_constraint("ck_import_jobs_owner", "import_jobs", CK_JOBS_OWNER)
    op.create_check_constraint("ck_import_jobs_amendment_owner", "import_jobs", CK_JOBS_AMENDMENT_OWNER)
    op.create_check_constraint("ck_import_jobs_parsed_pair", "import_jobs", CK_JOBS_PARSED_PAIR)
    op.create_check_constraint("ck_import_jobs_estimates_created", "import_jobs", CK_JOBS_ESTIMATES_CREATED)
    op.execute("DROP INDEX uq_import_jobs_active_pair")
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_pair
        ON import_jobs (contract_id, COALESCE(amendment_no, -1))
        WHERE contract_id IS NOT NULL AND status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_round
        ON import_jobs (round_id)
        WHERE round_id IS NOT NULL AND status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )

    # --- contractors: канон ИНН — сначала данные, потом CHECK ---
    _canonicalize_existing_inns(bind)
    op.create_check_constraint("ck_contractors_inn_canonical", "contractors", CK_CONTRACTORS_INN)


def _downgrade_blockers(bind) -> dict[str, int]:
    """Три счётчика, каждый из которых сам по себе держит откат. Вынесены в
    функцию, чтобы тест проверил каждую ветвь отдельно — в валидной схеме
    дочерние строки без тендера не существуют, и одним живым downgrade все
    три диагностики не увидеть."""
    return {
        "tenders": bind.execute(sa.text("SELECT count(*) FROM tenders")).scalar_one(),
        "round_jobs": bind.execute(
            sa.text("SELECT count(*) FROM import_jobs WHERE round_id IS NOT NULL")
        ).scalar_one(),
        "ownerless_estimates": bind.execute(
            sa.text("SELECT count(*) FROM estimates WHERE contract_id IS NULL")
        ).scalar_one(),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0015 невозможен: тендеров — {blockers['tenders']}, заданий импорта раундов — "
        f"{blockers['round_jobs']}, смет предложений и baseline — {blockers['ownerless_estimates']}. "
        "Это загруженные файлы и результаты импорта; удалить их — решение человека, а не "
        "миграции. Удалите тендеры через приложение и повторите откат."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    op.drop_constraint("ck_contractors_inn_canonical", "contractors", type_="check")

    op.execute("DROP INDEX uq_import_jobs_active_round")
    op.execute("DROP INDEX uq_import_jobs_active_pair")
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_pair
        ON import_jobs (contract_id, COALESCE(amendment_no, -1))
        WHERE status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )
    for name in ("ck_import_jobs_estimates_created", "ck_import_jobs_parsed_pair",
                 "ck_import_jobs_amendment_owner", "ck_import_jobs_owner"):
        op.drop_constraint(name, "import_jobs", type_="check")
    op.drop_index("ix_import_jobs_round_id", table_name="import_jobs")
    op.drop_constraint("fk_import_jobs_round_id", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "estimates_created")
    op.drop_column("import_jobs", "parser_version")
    op.drop_column("import_jobs", "parsed_data")
    op.drop_column("import_jobs", "round_id")
    op.alter_column("import_jobs", "contract_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_constraint("ck_proposals_baseline_contractor", "proposals", type_="check")
    op.alter_column("proposals", "contractor_id", existing_type=sa.BigInteger(), nullable=False)

    op.execute("DROP INDEX uq_estimates_round")
    op.execute("DROP INDEX uq_estimates_offer")
    op.execute("DROP INDEX uq_estimates_contract_amendment")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_estimates_contract_amendment
        ON estimates (contract_id, amendment_no) NULLS NOT DISTINCT
        """
    )
    op.drop_constraint("ck_estimates_amendment_owner", "estimates", type_="check")
    op.drop_constraint("ck_estimates_owner", "estimates", type_="check")
    op.drop_constraint("fk_estimates_round_id", "estimates", type_="foreignkey")
    op.drop_constraint("fk_estimates_offer_id", "estimates", type_="foreignkey")
    op.drop_column("estimates", "round_id")
    op.drop_column("estimates", "offer_id")
    op.alter_column("estimates", "contract_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_table("offers")
    op.drop_table("offer_packages")
    op.drop_table("tender_rounds")
    op.drop_table("tenders")

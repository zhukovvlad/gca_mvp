"""Отметка победителя тендера и договор из КП: таблица `tender_awards`, три новые
колонки и составные ключи (спека `2026-10-08-tender-award-design.md` §2.2).

Новая таблица `tender_awards` — отметки победителя; история не удаляется:
«договор не заключён» закрывает отметку. Колонки: `contracts.tender_award_id`
(основание «по тендеру»), `estimates.source_award_id` (смета — копия КП отметки),
`import_jobs.source_award_id` (задание — копия КП).

**Составные ключи.** Оферта, участник, КП и подрядчик отметки, а также объект и
подрядчик договора обязаны принадлежать одной отметке структурно. Целями служат
четыре новых `UNIQUE` (`tenders (id, object_id)`, `offer_packages (id,
contractor_id)`, `offers (id, tender_id, package_id)`, `estimates (id,
offer_id)`) и два на `contracts`. Ключи без явного `ON DELETE` — `NO ACTION`:
для плоского каскада выбор не несущий (спека §1.3), но это умолчание проекта.
Составной ключ с одной nullable колонкой (`MATCH SIMPLE`) не проверяется, пока
она `NULL`; поэтому у `source_award_id` стоит `CHECK` на `contract_id IS NOT
NULL` — иначе копия у сметы без договора прошла бы мимо ключа. Оба `CHECK`
тотальны: каждая ветвь сравнивает только `IS [NOT] NULL`.

**Частичный уникальный индекс** `uq_tender_awards_active (tender_id) WHERE
not_concluded_on IS NULL` объявлен и в модели, и здесь — не более одной
действующей отметки на тендер.

Данных миграция не переносит: новые колонки `NULL`, отметок нет. `downgrade`
снимает всё в обратном порядке и данных не теряет, пока отметок нет; при отметках
он их удаляет вместе со ссылками на них — это решение человека, как у прочих
откатов.

Выражения `CHECK` продублированы константами в `models.py` (миграция не
импортирует `models.py` — она обязана быть неизменной во времени); parity —
`test_tender_award_schema.py`.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-08
"""
import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_tender_award_schema.py::TestParityWithMigration.
TENDER_AWARD_NOT_CONCLUDED = (
    "(not_concluded_on IS NULL AND not_concluded_by IS NULL "
    "AND not_concluded_at IS NULL AND not_concluded_note IS NULL) "
    "OR (not_concluded_on IS NOT NULL AND not_concluded_by IS NOT NULL "
    "AND not_concluded_at IS NOT NULL)"
)
TENDER_AWARD_NOTE_NOT_BLANK = "not_concluded_note IS NULL OR btrim(not_concluded_note) <> ''"
TENDER_AWARD_KP_INN_CANONICAL = "kp_inn ~ '^[0-9]+$'"
ESTIMATE_SOURCE_AWARD_ORIGINAL = (
    "source_award_id IS NULL OR (contract_id IS NOT NULL AND amendment_no IS NULL)"
)
IMPORT_JOB_SOURCE_AWARD_ORIGINAL = (
    "source_award_id IS NULL OR (contract_id IS NOT NULL AND amendment_no IS NULL)"
)


def upgrade() -> None:
    # --- 1. Цели составных ключей на существующих таблицах ----------------
    op.create_unique_constraint("uq_tenders_id_object", "tenders", ["id", "object_id"])
    op.create_unique_constraint(
        "uq_offer_packages_id_contractor", "offer_packages", ["id", "contractor_id"]
    )
    op.create_unique_constraint(
        "uq_offers_id_tender_package", "offers", ["id", "tender_id", "package_id"]
    )
    op.create_unique_constraint("uq_estimates_id_offer", "estimates", ["id", "offer_id"])

    # --- 2. Отметки победителя --------------------------------------------
    op.create_table(
        "tender_awards",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("contractor_id", sa.BigInteger(), nullable=False),
        sa.Column("estimate_id", sa.BigInteger(), nullable=False),
        sa.Column("kp_inn", sa.Text(), nullable=False),
        sa.Column(
            "awarded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("awarded_by", sa.Integer(), nullable=False),
        sa.Column("not_concluded_on", sa.Date(), nullable=True),
        sa.Column("not_concluded_note", sa.Text(), nullable=True),
        sa.Column("not_concluded_by", sa.Integer(), nullable=True),
        sa.Column("not_concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tender_awards"),
        sa.ForeignKeyConstraint(
            ["tender_id", "object_id"], ["tenders.id", "tenders.object_id"],
            ondelete="CASCADE", name="fk_tender_awards_tender",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id", "tender_id", "package_id"],
            ["offers.id", "offers.tender_id", "offers.package_id"],
            name="fk_tender_awards_offer",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "contractor_id"],
            ["offer_packages.id", "offer_packages.contractor_id"],
            name="fk_tender_awards_package",
        ),
        sa.ForeignKeyConstraint(
            ["estimate_id", "offer_id"], ["estimates.id", "estimates.offer_id"],
            name="fk_tender_awards_kp_estimate",
        ),
        sa.ForeignKeyConstraint(
            ["awarded_by"], ["users.id"], ondelete="RESTRICT", name="fk_tender_awards_awarded_by"
        ),
        sa.ForeignKeyConstraint(
            ["not_concluded_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_tender_awards_not_concluded_by",
        ),
        sa.UniqueConstraint(
            "id", "object_id", "contractor_id", name="uq_tender_awards_id_object_contractor"
        ),
        sa.CheckConstraint(TENDER_AWARD_KP_INN_CANONICAL, name="ck_tender_awards_kp_inn_canonical"),
        sa.CheckConstraint(TENDER_AWARD_NOT_CONCLUDED, name="ck_tender_awards_not_concluded"),
        sa.CheckConstraint(TENDER_AWARD_NOTE_NOT_BLANK, name="ck_tender_awards_note_not_blank"),
    )
    op.create_index(
        "uq_tender_awards_active", "tender_awards", ["tender_id"], unique=True,
        postgresql_where=sa.text("not_concluded_on IS NULL"),
    )
    op.create_index("ix_tender_awards_tender_id", "tender_awards", ["tender_id"])
    op.create_index("ix_tender_awards_offer_id", "tender_awards", ["offer_id"])
    op.create_index("ix_tender_awards_package_id", "tender_awards", ["package_id"])
    op.create_index("ix_tender_awards_estimate_id", "tender_awards", ["estimate_id"])

    # --- 3. Договор: основание «по тендеру» -------------------------------
    op.add_column("contracts", sa.Column("tender_award_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_contracts_tender_award", "contracts", ["tender_award_id"])
    op.create_unique_constraint(
        "uq_contracts_id_tender_award", "contracts", ["id", "tender_award_id"]
    )
    op.create_foreign_key(
        "fk_contracts_tender_award", "contracts", "tender_awards",
        ["tender_award_id", "object_id", "contractor_id"], ["id", "object_id", "contractor_id"],
    )

    # --- 4. Смета: копия КП отметки основания -----------------------------
    op.add_column("estimates", sa.Column("source_award_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_estimates_source_award", "estimates", ESTIMATE_SOURCE_AWARD_ORIGINAL
    )
    op.create_foreign_key(
        "fk_estimates_source_award", "estimates", "contracts",
        ["contract_id", "source_award_id"], ["id", "tender_award_id"],
    )

    # --- 5. Задание импорта: копия КП -------------------------------------
    op.add_column("import_jobs", sa.Column("source_award_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_import_jobs_source_award", "import_jobs", "tender_awards",
        ["source_award_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_import_jobs_source_award", "import_jobs", IMPORT_JOB_SOURCE_AWARD_ORIGINAL
    )


def downgrade() -> None:
    # Обратный порядок: сначала то, что ссылается на отметки и на ключи договора.
    op.drop_constraint("ck_import_jobs_source_award", "import_jobs", type_="check")
    op.drop_constraint("fk_import_jobs_source_award", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "source_award_id")

    op.drop_constraint("fk_estimates_source_award", "estimates", type_="foreignkey")
    op.drop_constraint("ck_estimates_source_award", "estimates", type_="check")
    op.drop_column("estimates", "source_award_id")

    op.drop_constraint("fk_contracts_tender_award", "contracts", type_="foreignkey")
    op.drop_constraint("uq_contracts_id_tender_award", "contracts", type_="unique")
    op.drop_constraint("uq_contracts_tender_award", "contracts", type_="unique")
    op.drop_column("contracts", "tender_award_id")

    op.drop_table("tender_awards")

    op.drop_constraint("uq_estimates_id_offer", "estimates", type_="unique")
    op.drop_constraint("uq_offers_id_tender_package", "offers", type_="unique")
    op.drop_constraint("uq_offer_packages_id_contractor", "offer_packages", type_="unique")
    op.drop_constraint("uq_tenders_id_object", "tenders", type_="unique")

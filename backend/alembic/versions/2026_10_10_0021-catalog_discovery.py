"""Открытие семей и категории семей: справочник категорий, колонка категории у
семей, четвёртый вид задания, три таблицы группы ответа и новый тип журнала
(спека `2026-10-09-catalog-discovery-design.md` §2.2).

Четыре новые таблицы, в порядке зависимостей: `family_categories` (справочник;
три строки заводит сама миграция), `family_drafts` (группа ответа открытия),
`family_draft_members` (контексты за группой) и `family_category_proposals`
(предложение категории активной семье).

**Задания.** `kind` получает значение `family_discovery`. Две существующие
равносильности переписаны под четвёртый вид: предмет-контекст есть у
`family_suggestion` и `context_values` (у открытия предмет — единица), версия
схемы — у `family_schema` и `context_values`; третье ограничение
`ck_semantic_jobs_discovery_subject` запрещает открытию контекст и семью. Для
заданий прежних видов новые выражения равносильны прежним — `upgrade` не меняет
ни одной строки `semantic_jobs`.

**Три уникальных индекса — raw SQL** (выражение и частичность не
выражаются `UniqueConstraint`): `uq_family_categories_title` (имя категории без
учёта регистра и крайних пробелов), `uq_semantic_jobs_discovery_live` (одно
живое открытие на единицу; `NULL`-единица законна — `COALESCE(unit_id, -1)`) и
`uq_family_drafts_not_work_per_job` (одна группа «не работа» на открытие).
Зарегистрированы в `alembic/env.py` (`RAW_SQL_INDEXES`).

**Категория у семьи.** `work_families.family_category_id` допускает `NULL`, и
ограничения «активна ⟹ категория» нет намеренно: у ранее активных семей поле
пусто до прохода разметки.

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): форма группы
`ck_family_drafts_shape` — один тотальный предикат из трёх полных ветвей, в
ветви `new` стоят `IS NOT NULL` перед `btrim(...)`; остальные равносильности
сравнивают голым равенством только `NOT NULL` сторону с `IS [NOT] NULL`.

**`downgrade`** отказывает при ненулевом счётчике любого из шести носителей
(черновики, предложения категорий, задания открытия, семьи с категорией,
события `context_reopened`, категории без `seed_key`) и называет каждый; на
пустых носителях возвращает прежние выражения CHECK заданий и список типов
журнала.

Выражения CHECK и списки `IN (...)` продублированы константами в `models.py`
(миграция не импортирует `models.py` — она обязана быть неизменной во времени);
parity — `test_catalog_discovery_schema.py`.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-10
"""
import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы.
SEMANTIC_JOB_KINDS = "'family_suggestion', 'family_schema', 'context_values', 'family_discovery'"
DRAFT_GROUPS = "'new', 'existing', 'not_work'"
DRAFT_STATUSES = "'open', 'activated', 'merged', 'discarded', 'superseded'"
CATEGORY_PROPOSAL_STATUSES = "'open', 'applied', 'superseded'"
SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged', "
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged', "
    "'context_reopened'"
)

# Значения до 0021 — для возврата `downgrade` (дублируют 0019 побуквенно).
PREVIOUS_SEMANTIC_JOB_KINDS = "'family_suggestion', 'family_schema', 'context_values'"
PREVIOUS_SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged', "
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged'"
)
PREVIOUS_CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = "(kind <> 'family_schema') = (context_id IS NOT NULL)"
PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = (
    "(kind <> 'family_suggestion') = (schema_id IS NOT NULL)"
)

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_catalog_discovery_schema.py::TestParityWithMigration.
CK_FAMILY_CATEGORY_TITLE_NOT_BLANK = "btrim(title) <> ''"
CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK = "btrim(definition) <> ''"
CK_FAMILY_AUTHOR_IFF_NOT_SEED = "(created_by IS NULL) = (seed_key IS NOT NULL)"

CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = (
    "(kind IN ('family_suggestion', 'context_values')) = (context_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = (
    "(kind IN ('family_schema', 'context_values')) = (schema_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT = (
    "kind <> 'family_discovery' OR (context_id IS NULL AND family_id IS NULL)"
)

CK_DRAFT_SHAPE = (
    "(grp = 'new' AND title IS NOT NULL AND definition IS NOT NULL "
    "AND btrim(title) <> '' AND btrim(definition) <> '' "
    "AND existing_family_id IS NULL) "
    "OR (grp = 'existing' AND existing_family_id IS NOT NULL AND title IS NULL "
    "AND definition IS NULL AND family_category_id IS NULL AND similar_family_id IS NULL) "
    "OR (grp = 'not_work' AND title IS NULL AND definition IS NULL "
    "AND family_category_id IS NULL AND existing_family_id IS NULL "
    "AND similar_family_id IS NULL)"
)
CK_DRAFT_ACTIVATED_PAIR = "(status = 'activated') = (activated_family_id IS NOT NULL)"
CK_DRAFT_MERGED_PAIR = (
    "(status = 'merged') = (num_nonnulls(merged_into_draft_id, merged_into_family_id) = 1)"
)
CK_DRAFT_MERGE_TARGET_AT_MOST_ONE = "num_nonnulls(merged_into_draft_id, merged_into_family_id) <= 1"
CK_DRAFT_NOT_SELF_MERGED = "merged_into_draft_id IS NULL OR merged_into_draft_id <> id"
CK_DRAFT_DECISION_ONLY_NEW = "grp = 'new' OR status IN ('open', 'superseded')"
CK_DRAFT_DECIDED_BY_PAIR = (
    "(status IN ('activated', 'merged', 'discarded')) = (decided_by IS NOT NULL)"
)
CK_DRAFT_DECIDED_AT_PAIR = "(decided_by IS NULL) = (decided_at IS NULL)"
CK_DRAFT_EDITED_PAIR = "(edited_by IS NULL) = (edited_at IS NULL)"

CK_PROPOSAL_APPLIED_PAIR = "(status = 'applied') = (decided_by IS NOT NULL)"
CK_PROPOSAL_DECIDED_AT_PAIR = "(decided_by IS NULL) = (decided_at IS NULL)"

# Три строки справочника (макет К2): ключ, имя, определение — их видит модель.
SEED_CATEGORIES = (
    (
        "work",
        "Работа",
        "Строительно-монтажная работа или материал с объёмом в своей единице.",
    ),
    (
        "engineering_system",
        "Инженерная система",
        "Инженерная система здания или её часть, оцениваемая комплектом.",
    ),
    (
        "costs_services",
        "Затраты и услуги",
        "Не работа на объекте, а обеспечение и сопровождение: гарантии, страхование, "
        "коммунальные расходы, охрана, уборка, временные здания, документация.",
    ),
)


def _now_column(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    # --- 1. Справочник категорий ------------------------------------------
    op.create_table(
        "family_categories",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("seed_key", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        _now_column(),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_family_categories"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_family_categories_created_by",
        ),
        sa.UniqueConstraint("seed_key", name="uq_family_categories_seed_key"),
        sa.CheckConstraint(
            CK_FAMILY_CATEGORY_TITLE_NOT_BLANK, name="ck_family_categories_title_not_blank"
        ),
        sa.CheckConstraint(
            CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK,
            name="ck_family_categories_definition_not_blank",
        ),
        sa.CheckConstraint(
            CK_FAMILY_AUTHOR_IFF_NOT_SEED, name="ck_family_categories_author_iff_not_seed"
        ),
    )
    # Имя без учёта регистра и крайних пробелов — raw SQL (RAW_SQL_INDEXES).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_categories_title
        ON family_categories (lower(btrim(title)))
        """
    )
    categories = sa.table(
        "family_categories",
        sa.column("seed_key", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("definition", sa.Text()),
    )
    op.bulk_insert(
        categories,
        [
            {"seed_key": seed_key, "title": title, "definition": definition}
            for seed_key, title, definition in SEED_CATEGORIES
        ],
    )

    # --- 2. Категория у семьи ----------------------------------------------
    op.add_column("work_families", sa.Column("family_category_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_work_families_family_category_id", "work_families", "family_categories",
        ["family_category_id"], ["id"], ondelete="RESTRICT",
    )

    # --- 3. Задания: четвёртый вид ------------------------------------------
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({SEMANTIC_JOB_KINDS})"
    )
    op.drop_constraint("ck_semantic_jobs_context_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_context_subject", "semantic_jobs", CK_SEMANTIC_JOBS_CONTEXT_SUBJECT
    )
    op.drop_constraint("ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND
    )
    op.create_check_constraint(
        "ck_semantic_jobs_discovery_subject", "semantic_jobs", CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT
    )
    # Одно живое открытие на единицу — raw SQL (RAW_SQL_INDEXES); `NULL`-единица —
    # законная, как в ключе корзины.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_semantic_jobs_discovery_live
        ON semantic_jobs (COALESCE(unit_id, -1))
        WHERE kind = 'family_discovery' AND status IN ('pending', 'running', 'privacy_hold')
        """
    )

    # --- 4. Группа ответа открытия -------------------------------------------
    op.create_table(
        "family_drafts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("grp", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("family_category_id", sa.BigInteger(), nullable=True),
        sa.Column("existing_family_id", sa.BigInteger(), nullable=True),
        sa.Column("similar_family_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("activated_family_id", sa.BigInteger(), nullable=True),
        sa.Column("merged_into_draft_id", sa.BigInteger(), nullable=True),
        sa.Column("merged_into_family_id", sa.BigInteger(), nullable=True),
        sa.Column("edited_by", sa.Integer(), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        _now_column(),
        sa.PrimaryKeyConstraint("id", name="pk_family_drafts"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="RESTRICT", name="fk_family_drafts_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units_of_measure.id"], ondelete="RESTRICT",
            name="fk_family_drafts_unit_id",
        ),
        sa.ForeignKeyConstraint(
            ["family_category_id"], ["family_categories.id"], ondelete="SET NULL",
            name="fk_family_drafts_family_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["existing_family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_drafts_existing_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["similar_family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_drafts_similar_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["activated_family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_drafts_activated_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_drafts_merged_into_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["edited_by"], ["users.id"], ondelete="RESTRICT", name="fk_family_drafts_edited_by",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], ondelete="RESTRICT", name="fk_family_drafts_decided_by",
        ),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_family_drafts_job_ordinal"),
        sa.UniqueConstraint("id", "job_id", name="uq_family_drafts_id_job"),
        # Слияние — только внутри открытия: составной FK на `(id, job_id)`.
        sa.ForeignKeyConstraint(
            ["merged_into_draft_id", "job_id"], ["family_drafts.id", "family_drafts.job_id"],
            ondelete="RESTRICT", name="fk_family_drafts_merged_into_job",
        ),
        sa.CheckConstraint(f"grp IN ({DRAFT_GROUPS})", name="ck_family_drafts_grp"),
        sa.CheckConstraint(f"status IN ({DRAFT_STATUSES})", name="ck_family_drafts_status"),
        sa.CheckConstraint(CK_DRAFT_SHAPE, name="ck_family_drafts_shape"),
        sa.CheckConstraint(CK_DRAFT_ACTIVATED_PAIR, name="ck_family_drafts_activated_pair"),
        sa.CheckConstraint(CK_DRAFT_MERGED_PAIR, name="ck_family_drafts_merged_pair"),
        sa.CheckConstraint(
            CK_DRAFT_MERGE_TARGET_AT_MOST_ONE, name="ck_family_drafts_merge_target_at_most_one"
        ),
        sa.CheckConstraint(CK_DRAFT_NOT_SELF_MERGED, name="ck_family_drafts_not_self_merged"),
        sa.CheckConstraint(CK_DRAFT_DECISION_ONLY_NEW, name="ck_family_drafts_decision_only_new"),
        sa.CheckConstraint(CK_DRAFT_DECIDED_BY_PAIR, name="ck_family_drafts_decided_by_pair"),
        sa.CheckConstraint(CK_DRAFT_DECIDED_AT_PAIR, name="ck_family_drafts_decided_at_pair"),
        sa.CheckConstraint(CK_DRAFT_EDITED_PAIR, name="ck_family_drafts_edited_pair"),
    )
    # Одна группа «не работа» на открытие — raw SQL (RAW_SQL_INDEXES).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_drafts_not_work_per_job
        ON family_drafts (job_id)
        WHERE grp = 'not_work'
        """
    )

    # --- 5. Контексты за группой ----------------------------------------------
    op.create_table(
        "family_draft_members",
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("name_index", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("draft_id", "context_id", name="pk_family_draft_members"),
        sa.ForeignKeyConstraint(
            ["draft_id", "job_id"], ["family_drafts.id", "family_drafts.job_id"],
            ondelete="CASCADE", name="fk_family_draft_members_draft_job",
        ),
        sa.ForeignKeyConstraint(
            ["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT",
            name="fk_family_draft_members_context_id",
        ),
        sa.UniqueConstraint("job_id", "context_id", name="uq_family_draft_members_job_context"),
    )

    # --- 6. Предложение категории активной семье ---------------------------------
    op.create_table(
        "family_category_proposals",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("family_category_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id", "family_id", name="pk_family_category_proposals"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="RESTRICT",
            name="fk_family_category_proposals_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_category_proposals_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["family_category_id"], ["family_categories.id"], ondelete="CASCADE",
            name="fk_family_category_proposals_family_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_family_category_proposals_decided_by",
        ),
        sa.CheckConstraint(
            f"status IN ({CATEGORY_PROPOSAL_STATUSES})", name="ck_family_category_proposals_status"
        ),
        sa.CheckConstraint(
            CK_PROPOSAL_APPLIED_PAIR, name="ck_family_category_proposals_applied_pair"
        ),
        sa.CheckConstraint(
            CK_PROPOSAL_DECIDED_AT_PAIR, name="ck_family_category_proposals_decided_at_pair"
        ),
    )

    # --- 7. Журнал: тип `context_reopened` -------------------------------------
    # Предмет типа — контекст: `ck_semantic_events_subject_by_type` не меняется.
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({SEMANTIC_EVENT_TYPES_SQL})",
    )


def _downgrade_blockers(bind) -> dict[str, int]:
    """Шесть счётчиков, каждый сам по себе держит откат. Вынесены в функцию,
    чтобы тест проверил каждую ветвь отдельно: в валидной схеме черновики без
    предложений категорий, семья с категорией без всего остального и событие без
    заданий существуют одновременно."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "family_drafts": count("SELECT count(*) FROM family_drafts"),
        "family_category_proposals": count("SELECT count(*) FROM family_category_proposals"),
        "semantic_jobs_discovery": count(
            "SELECT count(*) FROM semantic_jobs WHERE kind = 'family_discovery'"
        ),
        "work_families_with_category": count(
            "SELECT count(*) FROM work_families WHERE family_category_id IS NOT NULL"
        ),
        "semantic_events_reopened": count(
            "SELECT count(*) FROM semantic_events WHERE event_type = 'context_reopened'"
        ),
        "family_categories_custom": count(
            "SELECT count(*) FROM family_categories WHERE seed_key IS NULL"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0021 невозможен: черновиков открытия — {blockers['family_drafts']}, "
        f"предложений категорий — {blockers['family_category_proposals']}, "
        f"заданий открытия — {blockers['semantic_jobs_discovery']}, "
        f"семей с категорией — {blockers['work_families_with_category']}, "
        f"событий context_reopened — {blockers['semantic_events_reopened']}, "
        f"категорий без seed_key — {blockers['family_categories_custom']}. Это решения "
        "человека и оплаченные ответы модели; удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # Журнал.
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({PREVIOUS_SEMANTIC_EVENT_TYPES_SQL})",
    )

    # Таблицы фичи — в обратном порядке зависимостей.
    op.drop_table("family_category_proposals")
    op.drop_table("family_draft_members")
    op.execute("DROP INDEX uq_family_drafts_not_work_per_job")
    op.drop_table("family_drafts")

    # Задания: прежние выражения.
    op.execute("DROP INDEX uq_semantic_jobs_discovery_live")
    op.drop_constraint("ck_semantic_jobs_discovery_subject", "semantic_jobs", type_="check")
    op.drop_constraint("ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_id_by_kind", "semantic_jobs",
        PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND,
    )
    op.drop_constraint("ck_semantic_jobs_context_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_context_subject", "semantic_jobs",
        PREVIOUS_CK_SEMANTIC_JOBS_CONTEXT_SUBJECT,
    )
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({PREVIOUS_SEMANTIC_JOB_KINDS})"
    )

    # Категория у семьи и справочник.
    op.drop_constraint("fk_work_families_family_category_id", "work_families", type_="foreignkey")
    op.drop_column("work_families", "family_category_id")
    op.execute("DROP INDEX uq_family_categories_title")
    op.drop_table("family_categories")

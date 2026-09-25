"""Семантический контур: семьи работ, корзины, контексты, правила маршрутизации,
членство и журнал (спека 2026-09-22-catalog-families-design.md §2.3).

Шесть таблиц, в порядке зависимостей: `work_families`, `context_buckets`,
`catalog_contexts`, `context_routing_rules`, `context_members`,
`semantic_events`.

**Составные FK — дважды.** `context_routing_rules` и `context_members` держат
и `bucket_id`, и `context_id`; составной FK на `catalog_contexts (bucket_id,
id)` проверяет разом существование контекста и его принадлежность НУЖНОЙ
корзине — структурная гарантия вместо инварианта, который пришлось бы
стеречь тестом (тот же приём, что у `offers` тендерного контура).

**Три уникальных индекса — raw SQL**, потому что PostgreSQL-выражение
(`COALESCE`, `lower(btrim(...))`) или частичность (`WHERE`) не выражаются
`UniqueConstraint`: `uq_work_families_active_name_unit`,
`uq_context_buckets_position_category`, `uq_catalog_contexts_default_per_bucket`.
Они зарегистрированы в `alembic/env.py` (`RAW_SQL_INDEXES`), иначе `alembic
check` предлагал бы их удалить как «лишние» на каждом прогоне. Три частичных
индекса `context_members` (без выражений, только `WHERE`) декларативны и в
этот список не входят.

**Трёхзначная логика CHECK.** Происхождение семьи контекста
(`CK_CONTEXT_FAMILY_PROVENANCE`) — ОДИН тотальный предикат из двух полных
ветвей, а не пара равносильностей: пара пропускала бы одинокий `family_by`
при трёх пустых полях (`CHECK` вычислялся бы в `NULL`, не в `FALSE`).
Подробности — `docs/pitfalls/db.md`.

**Журнал — закрытый список событий, предмет по явному множеству типов, не по
префиксу имени** (`CK_EVENT_SUBJECT_BY_TYPE`). На вставках в таблицу
множество и префикс `family_%` неразличимы: `context_family_assigned`
(переименованное событие) не начинается с `family_`, и оба предиката дают на
нём одинаковый ответ. Свидетель против префикса — историческое имя
`family_assigned`, вычисленное СТАНДАЛОНОМ, вне таблицы и вне закрытого
списка `event_type` (`test_semantic_schema.py::TestEventSubjectByTypeExpression`):
множество даёт `TRUE` (предмет — контекст), префикс дал бы `FALSE`.

**`downgrade` отказывает по ЧЕТЫРЁМ счётчикам отдельно:** `work_families`,
`catalog_contexts`, `context_members`, `semantic_events`. Одного счётчика мало
— семьи появляются раньше первого контекста, контексты переживают уход всех
членств, а журнал переживает всё остальное. Откат проходит только на чистой
базе (seed — отдельная команда, вне этой задачи).

Выражения CHECK продублированы константами в `models.py`; parity —
`test_semantic_schema.py`.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-22
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы
# (models.py — не frozen artifact, эта миграция — frozen).
FAMILY_STATUSES = "'draft', 'active', 'archived'"
SEMANTIC_KINDS = "'WORK', 'SYSTEM', 'UNKNOWN'"
NAME_ROLES = "'WORK', 'LOCATION_ONLY', 'GENERIC_WORK'"
SEMANTIC_STATES = "'SUGGESTED', 'CONFIRMED', 'NOT_APPLICABLE'"
MEMBERSHIP_STATES = "'CURRENT', 'STALE'"
DECISION_SOURCES = "'rule', 'manual'"
FAMILY_SOURCES = "'manual', 'suggestion'"
ROUTED_BY_VALUES = "'default', 'rule', 'manual'"
COMPARABILITY_REASONS = "'insufficient_description'"
SEMANTIC_EVENT_TYPES = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged'"
)

# Одиннадцать констант CHECK — побуквенно равны одноимённым в models.py;
# расхождение ловит test_semantic_schema.py::TestParityWithMigration.
CK_FAMILY_ACTIVE_NEEDS_DEFINITION = (
    "status <> 'active' OR (definition IS NOT NULL AND btrim(definition) <> '')"
)
CK_FAMILY_ACTIVATION_PAIR = "(activated_at IS NULL) = (activated_by IS NULL)"
CK_FAMILY_AUTHOR_IFF_NOT_SEED = "(created_by IS NULL) = (seed_key IS NOT NULL)"
CK_CONTEXT_FAMILY_PROVENANCE = (
    "(work_family_id IS NULL AND family_source IS NULL AND family_at IS NULL AND family_by IS NULL) "
    "OR (work_family_id IS NOT NULL AND family_source IS NOT NULL AND family_at IS NOT NULL "
    "AND (family_by IS NOT NULL) = (family_source = 'manual'))"
)
CK_CONTEXT_KIND_SOURCE_PAIR = "(semantic_kind_source = 'manual') = (semantic_kind_by IS NOT NULL)"
CK_CONTEXT_NAME_ROLE_SOURCE_PAIR = "(name_role_source = 'manual') = (name_role_by IS NOT NULL)"
CK_MEMBER_RULE_PAIR = "(routed_by = 'rule') = (routing_rule_id IS NOT NULL)"
CK_MEMBER_CONFLICT_PAIR = "(conflict_at IS NULL) = (conflict_from_context_id IS NULL)"
CK_EVENT_ONE_SUBJECT = "num_nonnulls(context_id, family_id) = 1"
CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged')) = (family_id IS NOT NULL)"
)
CK_EVENT_PAYLOAD_NOT_EMPTY = "jsonb_typeof(payload) = 'object' AND payload <> '{}'::jsonb"


def upgrade() -> None:
    op.create_table(
        "work_families",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("seed_key", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("activated_by", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_work_families"),
        sa.ForeignKeyConstraint(["unit_id"], ["units_of_measure.id"], ondelete="RESTRICT", name="fk_work_families_unit_id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_work_families_created_by"),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"], ondelete="RESTRICT", name="fk_work_families_activated_by"),
        sa.UniqueConstraint("seed_key", name="uq_work_families_seed_key"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_work_families_title_not_blank"),
        sa.CheckConstraint(f"status IN ({FAMILY_STATUSES})", name="ck_work_families_status"),
        sa.CheckConstraint(CK_FAMILY_ACTIVE_NEEDS_DEFINITION, name="ck_work_families_active_needs_definition"),
        sa.CheckConstraint(CK_FAMILY_ACTIVATION_PAIR, name="ck_work_families_activation_pair"),
        sa.CheckConstraint(CK_FAMILY_AUTHOR_IFF_NOT_SEED, name="ck_work_families_author_iff_not_seed"),
    )
    # Частичный уникальный индекс по выражению (lower/btrim + COALESCE) —
    # raw SQL, зарегистрирован в alembic/env.py RAW_SQL_INDEXES.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_work_families_active_name_unit
        ON work_families (lower(btrim(title)), COALESCE(unit_id, -1))
        WHERE status = 'active'
        """
    )

    op.create_table(
        "context_buckets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("catalog_position_id", sa.BigInteger(), nullable=False),
        sa.Column("work_category_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_context_buckets"),
        sa.ForeignKeyConstraint(["catalog_position_id"], ["catalog_positions.id"], ondelete="RESTRICT", name="fk_context_buckets_catalog_position_id"),
        sa.ForeignKeyConstraint(["work_category_id"], ["work_categories.id"], ondelete="RESTRICT", name="fk_context_buckets_work_category_id"),
    )
    # UNIQUE по выражению с COALESCE — raw SQL (env.py RAW_SQL_INDEXES).
    # «Отсутствие статьи — своя корзина, одна на строку» (спека §2.2): COALESCE
    # делает NULL обычным сравнимым значением, поэтому вторая безстатейная
    # корзина той же каталожной строки отвергается как дубль.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_context_buckets_position_category
        ON context_buckets (catalog_position_id, COALESCE(work_category_id, -1))
        """
    )

    op.create_table(
        "catalog_contexts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_id", sa.BigInteger(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("work_family_id", sa.BigInteger(), nullable=True),
        sa.Column("family_source", sa.Text(), nullable=True),
        sa.Column("family_by", sa.Integer(), nullable=True),
        sa.Column("family_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("semantic_kind", sa.Text(), nullable=False),
        sa.Column("semantic_kind_source", sa.Text(), nullable=False),
        sa.Column("semantic_kind_by", sa.Integer(), nullable=True),
        sa.Column("semantic_kind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name_role", sa.Text(), nullable=False),
        sa.Column("name_role_source", sa.Text(), nullable=False),
        sa.Column("name_role_by", sa.Integer(), nullable=True),
        sa.Column("name_role_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("place_dictionary_version", sa.SmallInteger(), nullable=False),
        sa.Column("semantic_state", sa.Text(), nullable=False),
        sa.Column("comparability_reason", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_contexts"),
        sa.ForeignKeyConstraint(["bucket_id"], ["context_buckets.id"], ondelete="RESTRICT", name="fk_catalog_contexts_bucket_id"),
        sa.ForeignKeyConstraint(["work_family_id"], ["work_families.id"], ondelete="RESTRICT", name="fk_catalog_contexts_work_family_id"),
        sa.ForeignKeyConstraint(["family_by"], ["users.id"], ondelete="RESTRICT", name="fk_catalog_contexts_family_by"),
        sa.ForeignKeyConstraint(["semantic_kind_by"], ["users.id"], ondelete="RESTRICT", name="fk_catalog_contexts_semantic_kind_by"),
        sa.ForeignKeyConstraint(["name_role_by"], ["users.id"], ondelete="RESTRICT", name="fk_catalog_contexts_name_role_by"),
        sa.UniqueConstraint("bucket_id", "id", name="uq_catalog_contexts_bucket_id"),
        sa.CheckConstraint(f"semantic_kind IN ({SEMANTIC_KINDS})", name="ck_catalog_contexts_semantic_kind"),
        sa.CheckConstraint(f"semantic_kind_source IN ({DECISION_SOURCES})", name="ck_catalog_contexts_semantic_kind_source"),
        sa.CheckConstraint(f"name_role IN ({NAME_ROLES})", name="ck_catalog_contexts_name_role"),
        sa.CheckConstraint(f"name_role_source IN ({DECISION_SOURCES})", name="ck_catalog_contexts_name_role_source"),
        sa.CheckConstraint(f"semantic_state IN ({SEMANTIC_STATES})", name="ck_catalog_contexts_semantic_state"),
        sa.CheckConstraint(f"family_source IS NULL OR family_source IN ({FAMILY_SOURCES})", name="ck_catalog_contexts_family_source"),
        sa.CheckConstraint(
            f"comparability_reason IS NULL OR comparability_reason IN ({COMPARABILITY_REASONS})",
            name="ck_catalog_contexts_comparability_reason",
        ),
        sa.CheckConstraint(CK_CONTEXT_KIND_SOURCE_PAIR, name="ck_catalog_contexts_kind_source_pair"),
        sa.CheckConstraint(CK_CONTEXT_NAME_ROLE_SOURCE_PAIR, name="ck_catalog_contexts_name_role_source_pair"),
        sa.CheckConstraint(CK_CONTEXT_FAMILY_PROVENANCE, name="ck_catalog_contexts_family_provenance"),
    )
    # Частичный уникальный индекс (флаг + условие) — raw SQL (env.py RAW_SQL_INDEXES).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_catalog_contexts_default_per_bucket
        ON catalog_contexts (bucket_id)
        WHERE is_default AND archived_at IS NULL
        """
    )

    op.create_table(
        "context_routing_rules",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("predicate", JSONB(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_context_routing_rules"),
        sa.ForeignKeyConstraint(["bucket_id"], ["context_buckets.id"], ondelete="CASCADE", name="fk_context_routing_rules_bucket_id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_context_routing_rules_created_by"),
        sa.ForeignKeyConstraint(
            ["bucket_id", "context_id"], ["catalog_contexts.bucket_id", "catalog_contexts.id"],
            name="fk_context_routing_rules_bucket_context",
        ),
        sa.UniqueConstraint("bucket_id", "ordinal", name="uq_context_routing_rules_bucket_ordinal"),
    )

    op.create_table(
        "context_members",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_id", sa.BigInteger(), nullable=False),
        sa.Column("membership_state", sa.Text(), nullable=False),
        sa.Column("routed_by", sa.Text(), nullable=False),
        sa.Column("routing_rule_id", sa.BigInteger(), nullable=True),
        sa.Column("conflict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_from_context_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_context_members"),
        sa.ForeignKeyConstraint(["position_item_id"], ["position_items.id"], ondelete="CASCADE", name="fk_context_members_position_item_id"),
        sa.ForeignKeyConstraint(["routing_rule_id"], ["context_routing_rules.id"], ondelete="SET NULL", name="fk_context_members_routing_rule_id"),
        sa.ForeignKeyConstraint(["conflict_from_context_id"], ["catalog_contexts.id"], ondelete="RESTRICT", name="fk_context_members_conflict_from_context_id"),
        sa.ForeignKeyConstraint(
            ["bucket_id", "context_id"], ["catalog_contexts.bucket_id", "catalog_contexts.id"],
            ondelete="RESTRICT", name="fk_context_members_bucket_context",
        ),
        sa.CheckConstraint(f"membership_state IN ({MEMBERSHIP_STATES})", name="ck_context_members_membership_state"),
        sa.CheckConstraint(f"routed_by IN ({ROUTED_BY_VALUES})", name="ck_context_members_routed_by"),
        sa.CheckConstraint(CK_MEMBER_RULE_PAIR, name="ck_context_members_rule_pair"),
        sa.CheckConstraint(CK_MEMBER_CONFLICT_PAIR, name="ck_context_members_conflict_pair"),
    )
    # Три индекса по context_id — обычная колонка плюс WHERE, без выражений:
    # выразимы декларативно, в RAW_SQL_INDEXES не входят (как idx_cp_kind_review).
    op.create_index("idx_context_members_context_id", "context_members", ["context_id"])
    op.create_index(
        "idx_context_members_context_id_stale", "context_members", ["context_id"],
        postgresql_where=sa.text("membership_state = 'STALE'"),
    )
    op.create_index(
        "idx_context_members_context_id_conflict", "context_members", ["context_id"],
        postgresql_where=sa.text("conflict_at IS NOT NULL"),
    )

    op.create_table(
        "semantic_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=True),
        sa.Column("family_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_events"),
        sa.ForeignKeyConstraint(["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT", name="fk_semantic_events_context_id"),
        sa.ForeignKeyConstraint(["family_id"], ["work_families.id"], ondelete="RESTRICT", name="fk_semantic_events_family_id"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT", name="fk_semantic_events_actor_id"),
        sa.CheckConstraint(f"event_type IN ({SEMANTIC_EVENT_TYPES})", name="ck_semantic_events_event_type"),
        sa.CheckConstraint(CK_EVENT_ONE_SUBJECT, name="ck_semantic_events_one_subject"),
        sa.CheckConstraint(CK_EVENT_SUBJECT_BY_TYPE, name="ck_semantic_events_subject_by_type"),
        sa.CheckConstraint(CK_EVENT_PAYLOAD_NOT_EMPTY, name="ck_semantic_events_payload_not_empty"),
    )


def _downgrade_blockers(bind) -> dict[str, int]:
    """Четыре счётчика, каждый сам по себе держит откат. Вынесены в функцию,
    чтобы тест проверил каждую ветвь отдельно — в валидной схеме семьи без
    контекстов, контексты без членств и события без всего остального
    существуют одновременно, и один живой downgrade не покажет все четыре
    диагностики сразу."""
    return {
        "work_families": bind.execute(sa.text("SELECT count(*) FROM work_families")).scalar_one(),
        "catalog_contexts": bind.execute(sa.text("SELECT count(*) FROM catalog_contexts")).scalar_one(),
        "context_members": bind.execute(sa.text("SELECT count(*) FROM context_members")).scalar_one(),
        "semantic_events": bind.execute(sa.text("SELECT count(*) FROM semantic_events")).scalar_one(),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0017 невозможен: семей — {blockers['work_families']}, контекстов — "
        f"{blockers['catalog_contexts']}, членств — {blockers['context_members']}, "
        f"событий журнала — {blockers['semantic_events']}. Это принятые семантические "
        "решения оператора и загруженный seed; удалить их — решение человека, а не "
        "миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    op.drop_table("semantic_events")
    op.drop_table("context_members")
    op.drop_table("context_routing_rules")
    op.execute("DROP INDEX uq_catalog_contexts_default_per_bucket")
    op.drop_table("catalog_contexts")
    op.execute("DROP INDEX uq_context_buckets_position_category")
    op.drop_table("context_buckets")
    op.execute("DROP INDEX uq_work_families_active_name_unit")
    op.drop_table("work_families")

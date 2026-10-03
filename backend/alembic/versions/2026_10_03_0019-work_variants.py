"""Варианты работ и схемы параметров семей: шесть таблиц, расширение контекстов,
заданий, предложений, журнала и удержанных пачек (спека
`2026-10-02-catalog-variants-design.md` §2.4).

Шесть новых таблиц, в порядке зависимостей: `family_parameter_schemas` (версия
схемы семьи), `family_parameters` (параметр версии), `family_parameter_values`
(значение закрытого списка), `work_variants` (семья + версия схемы + набор
значений), `work_variant_values` (набор построчно) и `context_parameter_values`
(значения контекста по текущей версии схемы его семьи).

**Цикл FK.** `family_parameter_schemas.job_id` → `semantic_jobs` существует с
самого начала, а `semantic_jobs.schema_id` → `family_parameter_schemas`
добавляется ПОСЛЕ создания таблицы версий; `catalog_contexts.pending_suggestion_id`
→ `family_suggestions` — отдельными `ALTER`, когда все цели существуют.

**Отложенные FK — ровно два**, оба `DEFERRABLE INITIALLY DEFERRED`: контекст →
вариант `(work_variant_id, work_family_id)` и вариант → версия схемы
`(schema_id, family_id)`. Слияние семей меняет семью у контекста, варианта и
версии схемы в одной транзакции, и ни один порядок немедленных проверок её не
проходит; закоммиченное состояние инвариант выдерживает всегда. Все прочие FK
проверяются немедленно. Составной FK по умолчанию `MATCH SIMPLE` и при пустой
семье пару не проверяет вовсе, поэтому рядом стоит CHECK «вариант ⟹ семья».

**Три уникальных индекса — raw SQL**, потому что частичность (`WHERE`) и
выражения (`COALESCE`) не выражаются `UniqueConstraint`:
`uq_family_parameter_schemas_frozen` и `uq_family_parameter_schemas_building`
(одна текущая версия и одна пересборка за раз),
`uq_semantic_jobs_subject_request_hash` (ключ заданий по виду, предмету и
хэшу запроса; заменяет `uq_semantic_jobs_context_request_hash`, который был
обычным `UNIQUE` 0018). Они зарегистрированы в `alembic/env.py`
(`RAW_SQL_INDEXES`).

**Порядок для `semantic_jobs`:** `kind` nullable → `UPDATE` существующих строк в
`'family_suggestion'` → `NOT NULL` → CHECK-и и новый ключ. Серверного
умолчания у `kind` нет.

**Формат отпечатков удержанных пачек.** Элемент `held_fingerprints` — объект с
ключами `kind`, `context_id`, `family_id`, `schema_id`, `request_hash`
(отсутствующий предмет — `null`); список отсортирован по `(kind, context_id or
-1, family_id or -1, schema_id or -1, request_hash)`; `fingerprints_hash` —
sha256 канонической JSON-сериализации этого списка. Миграция переписывает
прежние пары `[context_id, request_hash]` в объекты с
`kind='family_suggestion'` и пересчитывает хэш; `downgrade` возвращает пары и
прежний хэш (sha256 списка пар, отсортированного по `(context_id,
request_hash)`).

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): `CK_CONTEXT_PENDING` —
один тотальный предикат из двух полных ветвей; равносильности стоят голым
равенством только на NOT NULL стороне, вторая сторона — `IS [NOT] NULL` либо
`IN`, не дающие `NULL` на NOT NULL колонке.

**`downgrade`** отказывает при ненулевом счётчике любого из десяти носителей
(варианты, версии схем, значения контекстов, контексты с вариантом, контексты с
ожиданием, контексты с источником семьи `auto_suggestion`, задания новых видов,
предложения с новыми решениями, события шести новых типов, пачки с отпечатками
не `family_suggestion`) и называет каждый. Источник `auto_suggestion` на
контексте держит отдельный счётчик: старый CHECK списка источников не пропустил
бы такую строку при возврате. На
пустых носителях откат проходит, возвращает ключ
`uq_semantic_jobs_context_request_hash` и пачкам — пары с прежним хэшем.

Выражения CHECK и списки `IN (...)` продублированы константами в `models.py`
(миграция не импортирует `models.py` — она обязана быть неизменной во
времени); parity — `test_work_variants_schema.py`.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-03
"""
import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы.
SCHEMA_STATUSES = "'building', 'frozen', 'superseded', 'cancelled'"
SCHEMA_ORIGINS = "'model', 'manual'"
VALUE_ORIGINS = "'schema', 'extension', 'manual'"
VARIANT_STATUSES = "'active', 'archived'"
VALUE_SOURCES = "'name', 'path', 'manual', 'path_conflict', 'none'"
VARIANT_SPLIT_HINTS = "'path_conflict'"
SEMANTIC_JOB_KINDS = "'family_suggestion', 'family_schema', 'context_values'"
FAMILY_SOURCES = "'manual', 'suggestion', 'auto_suggestion'"
SUGGESTION_DECISIONS = (
    "'accepted', 'rejected', 'other_family', 'family_created', "
    "'accepted_pending', 'auto_accepted', 'auto_pending', 'auto_superseded'"
)
SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged', "
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged'"
)

# Значения до 0019 — для возврата `downgrade` (дублируют 0017/0018 побуквенно).
PREVIOUS_FAMILY_SOURCES = "'manual', 'suggestion'"
PREVIOUS_SUGGESTION_DECISIONS = "'accepted', 'rejected', 'other_family', 'family_created'"
PREVIOUS_SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged'"
)

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_work_variants_schema.py::TestParityWithMigration.
CK_SCHEMA_CANCELLED_PAIR = "(status = 'cancelled') = (cancelled_at IS NOT NULL)"
CK_SCHEMA_FROZEN_AT_PAIR = "(status IN ('frozen', 'superseded')) = (frozen_at IS NOT NULL)"
CK_SCHEMA_SUPERSEDED_AT_PAIR = "(status = 'superseded') = (superseded_at IS NOT NULL)"
CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR = "(origin = 'manual') = (frozen_by IS NOT NULL)"

CK_PARAMETER_ORDINAL_RANGE = "ordinal BETWEEN 1 AND 3"
CK_PARAMETER_NAME_NOT_BLANK = "btrim(name) <> ''"

CK_PARAMETER_VALUE_NOT_BLANK = "btrim(value) <> '' AND value_norm <> ''"
CK_PARAMETER_VALUE_NOT_SELF_MERGED = "merged_into_id IS NULL OR merged_into_id <> id"

CK_VARIANT_ARCHIVED_PAIR = "(status = 'archived') = (archived_at IS NOT NULL)"
CK_VARIANT_MERGED_NEEDS_ARCHIVED = "merged_into_id IS NULL OR status = 'archived'"

CK_CONTEXT_VALUE_SOURCE_PAIR = "(value_id IS NULL) = (source IN ('path_conflict', 'none'))"

CK_CONTEXT_VARIANT_NEEDS_FAMILY = "work_variant_id IS NULL OR work_family_id IS NOT NULL"
CK_CONTEXT_VARIANT_AT_PAIR = "(work_variant_id IS NULL) = (variant_at IS NULL)"
CK_CONTEXT_VARIANT_PATHS_HASH_PAIR = "(work_variant_id IS NULL) = (variant_paths_hash IS NULL)"
CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT = "variant_split_hint IS NULL OR work_variant_id IS NOT NULL"
CK_CONTEXT_PENDING = (
    "(pending_family_id IS NULL AND pending_family_source IS NULL AND pending_suggestion_id IS NULL "
    "AND pending_by IS NULL AND pending_threshold IS NULL AND pending_at IS NULL) "
    "OR (pending_family_id IS NOT NULL AND pending_family_source IS NOT NULL "
    "AND pending_at IS NOT NULL "
    "AND (pending_family_source = 'manual') = (pending_by IS NOT NULL) "
    "AND (pending_family_source <> 'manual') = (pending_suggestion_id IS NOT NULL) "
    "AND (pending_family_source = 'auto_suggestion') = (pending_threshold IS NOT NULL))"
)

CK_SEMANTIC_JOBS_SCHEMA_SUBJECT = (
    "(kind = 'family_schema') = (context_id IS NULL AND family_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = "(kind <> 'family_schema') = (context_id IS NOT NULL)"
CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = "(kind <> 'family_suggestion') = (schema_id IS NOT NULL)"
CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND = (
    "result_suggestion_id IS NULL OR kind = 'family_suggestion'"
)

CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR = (
    "(decided_by IS NULL) = "
    "(decision IS NULL OR decision IN ('auto_accepted', 'auto_pending', 'auto_superseded'))"
)
PREVIOUS_CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR = "(decision IS NULL) = (decided_by IS NULL)"

CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged', 'family_schema_frozen', "
    "'family_schema_value_added', 'family_variants_merged')) = (family_id IS NOT NULL)"
)
PREVIOUS_CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged')) = (family_id IS NOT NULL)"
)

# Шесть типов журнала, которые вводит эта миграция (для счётчика `downgrade`).
NEW_EVENT_TYPES_SQL = (
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged'"
)
NEW_DECISIONS_SQL = "'accepted_pending', 'auto_accepted', 'auto_pending', 'auto_superseded'"

_CONTEXT_NEW_COLUMNS = (
    "work_variant_id", "variant_at", "variant_paths_hash", "variant_split_hint",
    "pending_family_id", "pending_family_source", "pending_suggestion_id", "pending_by",
    "pending_threshold", "pending_at",
)


# ---------------------------------------------------------------------------
#  Формат отпечатков пачек: канон нового формата и пары прежнего
# ---------------------------------------------------------------------------

def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fingerprint_sort_key(item: dict) -> tuple:
    return (
        item["kind"],
        item["context_id"] or -1,
        item["family_id"] or -1,
        item["schema_id"] or -1,
        item["request_hash"],
    )


def _fingerprints_to_objects(held) -> list[dict]:
    """Пары `[context_id, request_hash]` → объекты `kind='family_suggestion'`;
    уже объекты остаются как есть. Результат отсортирован по каноническому
    ключу."""
    items = []
    for element in held:
        if isinstance(element, dict):
            items.append(element)
        else:
            context_id, request_hash = element
            items.append(
                {
                    "kind": "family_suggestion",
                    "context_id": context_id,
                    "family_id": None,
                    "schema_id": None,
                    "request_hash": request_hash,
                }
            )
    return sorted(items, key=_fingerprint_sort_key)


def _fingerprints_to_pairs(held) -> list[list]:
    """Объекты `kind='family_suggestion'` → пары `[context_id, request_hash]`,
    отсортированные по `(context_id, request_hash)`; уже пары остаются как
    есть. Объект другого вида сюда не доходит: `downgrade` отказывает раньше."""
    pairs = []
    for element in held:
        if isinstance(element, dict):
            pairs.append([element["context_id"], element["request_hash"]])
        else:
            pairs.append([element[0], element[1]])
    return sorted(pairs)


def _rewrite_batches(bind, *, to_objects: bool) -> None:
    rows = bind.execute(
        sa.text("SELECT id, held_fingerprints FROM semantic_reconcile_batches ORDER BY id")
    ).all()
    for batch_id, held in rows:
        rewritten = _fingerprints_to_objects(held) if to_objects else _fingerprints_to_pairs(held)
        bind.execute(
            sa.text(
                "UPDATE semantic_reconcile_batches "
                "SET held_fingerprints = CAST(:held AS jsonb), fingerprints_hash = :hash "
                "WHERE id = :id"
            ),
            {
                "held": json.dumps(rewritten, ensure_ascii=False),
                "hash": _canonical_hash(rewritten),
                "id": batch_id,
            },
        )


def _now_column(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    # --- 1. Версия схемы семьи -------------------------------------------
    op.create_table(
        "family_parameter_schemas",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("frozen_by", sa.Integer(), nullable=True),
        _now_column(),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_family_parameter_schemas"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_parameter_schemas_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="SET NULL",
            name="fk_family_parameter_schemas_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["frozen_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_family_parameter_schemas_frozen_by",
        ),
        sa.UniqueConstraint("family_id", "version", name="uq_family_parameter_schemas_family_version"),
        sa.UniqueConstraint("id", "family_id", name="uq_family_parameter_schemas_id_family"),
        sa.CheckConstraint(
            f"status IN ({SCHEMA_STATUSES})", name="ck_family_parameter_schemas_status"
        ),
        sa.CheckConstraint(
            f"origin IN ({SCHEMA_ORIGINS})", name="ck_family_parameter_schemas_origin"
        ),
        sa.CheckConstraint(CK_SCHEMA_CANCELLED_PAIR, name="ck_family_parameter_schemas_cancelled_pair"),
        sa.CheckConstraint(CK_SCHEMA_FROZEN_AT_PAIR, name="ck_family_parameter_schemas_frozen_at_pair"),
        sa.CheckConstraint(
            CK_SCHEMA_SUPERSEDED_AT_PAIR, name="ck_family_parameter_schemas_superseded_at_pair"
        ),
        sa.CheckConstraint(
            CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR, name="ck_family_parameter_schemas_origin_frozen_by_pair"
        ),
    )
    # Частичные уникальные индексы — raw SQL (alembic/env.py RAW_SQL_INDEXES).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_parameter_schemas_frozen
        ON family_parameter_schemas (family_id)
        WHERE status = 'frozen'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_parameter_schemas_building
        ON family_parameter_schemas (family_id)
        WHERE status = 'building'
        """
    )

    # --- 2. Параметр версии ----------------------------------------------
    op.create_table(
        "family_parameters",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_family_parameters"),
        sa.ForeignKeyConstraint(
            ["schema_id"], ["family_parameter_schemas.id"], ondelete="CASCADE",
            name="fk_family_parameters_schema_id",
        ),
        sa.UniqueConstraint("schema_id", "ordinal", name="uq_family_parameters_schema_ordinal"),
        sa.UniqueConstraint("id", "schema_id", name="uq_family_parameters_id_schema"),
        sa.CheckConstraint(CK_PARAMETER_ORDINAL_RANGE, name="ck_family_parameters_ordinal_range"),
        sa.CheckConstraint(CK_PARAMETER_NAME_NOT_BLANK, name="ck_family_parameters_name_not_blank"),
    )

    # --- 3. Значение закрытого списка ------------------------------------
    op.create_table(
        "family_parameter_values",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("merged_into_id", sa.BigInteger(), nullable=True),
        _now_column(),
        sa.PrimaryKeyConstraint("id", name="pk_family_parameter_values"),
        sa.ForeignKeyConstraint(
            ["parameter_id"], ["family_parameters.id"], ondelete="CASCADE",
            name="fk_family_parameter_values_parameter_id",
        ),
        sa.UniqueConstraint(
            "parameter_id", "value_norm", name="uq_family_parameter_values_parameter_value_norm"
        ),
        sa.UniqueConstraint("id", "parameter_id", name="uq_family_parameter_values_id_parameter"),
        sa.ForeignKeyConstraint(
            ["merged_into_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT",
            name="fk_family_parameter_values_merged_into",
        ),
        sa.CheckConstraint(
            f"origin IN ({VALUE_ORIGINS})", name="ck_family_parameter_values_origin"
        ),
        sa.CheckConstraint(
            CK_PARAMETER_VALUE_NOT_BLANK, name="ck_family_parameter_values_value_not_blank"
        ),
        sa.CheckConstraint(
            CK_PARAMETER_VALUE_NOT_SELF_MERGED, name="ck_family_parameter_values_not_self_merged"
        ),
    )

    # --- 4. Вариант -------------------------------------------------------
    op.create_table(
        "work_variants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.Column("values_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("merged_into_id", sa.BigInteger(), nullable=True),
        _now_column(),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_work_variants"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_work_variants_family_id",
        ),
        # ОТЛОЖЕННЫЙ: слияние семей переписывает семью у варианта и версии схемы.
        sa.ForeignKeyConstraint(
            ["schema_id", "family_id"],
            ["family_parameter_schemas.id", "family_parameter_schemas.family_id"],
            ondelete="RESTRICT", deferrable=True, initially="DEFERRED",
            name="fk_work_variants_schema_family",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"], ["work_variants.id"], ondelete="RESTRICT",
            name="fk_work_variants_merged_into_id",
        ),
        sa.UniqueConstraint("schema_id", "values_key", name="uq_work_variants_schema_values_key"),
        sa.UniqueConstraint("id", "family_id", name="uq_work_variants_id_family"),
        sa.UniqueConstraint("id", "schema_id", name="uq_work_variants_id_schema"),
        sa.CheckConstraint(f"status IN ({VARIANT_STATUSES})", name="ck_work_variants_status"),
        sa.CheckConstraint(CK_VARIANT_ARCHIVED_PAIR, name="ck_work_variants_archived_pair"),
        sa.CheckConstraint(
            CK_VARIANT_MERGED_NEEDS_ARCHIVED, name="ck_work_variants_merged_needs_archived"
        ),
    )

    # --- 5. Набор значений варианта построчно ----------------------------
    op.create_table(
        "work_variant_values",
        sa.Column("variant_id", sa.BigInteger(), nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_id", sa.BigInteger(), nullable=False),
        sa.Column("value_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("variant_id", "parameter_id", name="pk_work_variant_values"),
        # Составной FK заменяет одиночный variant_id → work_variants: проверяет
        # и существование варианта, и то, что схема строки — схема варианта.
        sa.ForeignKeyConstraint(
            ["variant_id", "schema_id"], ["work_variants.id", "work_variants.schema_id"],
            ondelete="CASCADE", name="fk_work_variant_values_variant_schema",
        ),
        sa.ForeignKeyConstraint(
            ["parameter_id", "schema_id"],
            ["family_parameters.id", "family_parameters.schema_id"],
            ondelete="RESTRICT", name="fk_work_variant_values_parameter_schema",
        ),
        sa.ForeignKeyConstraint(
            ["value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT", name="fk_work_variant_values_value_parameter",
        ),
    )

    # --- 6. Новые колонки контекстов --------------------------------------
    op.add_column("catalog_contexts", sa.Column("work_variant_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "catalog_contexts", sa.Column("variant_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("catalog_contexts", sa.Column("variant_paths_hash", sa.Text(), nullable=True))
    op.add_column("catalog_contexts", sa.Column("variant_split_hint", sa.Text(), nullable=True))
    op.add_column("catalog_contexts", sa.Column("pending_family_id", sa.BigInteger(), nullable=True))
    op.add_column("catalog_contexts", sa.Column("pending_family_source", sa.Text(), nullable=True))
    op.add_column(
        "catalog_contexts", sa.Column("pending_suggestion_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("catalog_contexts", sa.Column("pending_by", sa.Integer(), nullable=True))
    op.add_column("catalog_contexts", sa.Column("pending_threshold", sa.Numeric(), nullable=True))
    op.add_column(
        "catalog_contexts", sa.Column("pending_at", sa.DateTime(timezone=True), nullable=True)
    )
    # ОТЛОЖЕННЫЙ: семья варианта = семья контекста; слияние семей меняет обе в
    # одной транзакции. MATCH SIMPLE при пустой семье пару не проверяет — эту
    # лазейку закрывает CHECK ниже.
    op.create_foreign_key(
        "fk_catalog_contexts_work_variant_family", "catalog_contexts", "work_variants",
        ["work_variant_id", "work_family_id"], ["id", "family_id"],
        ondelete="RESTRICT", deferrable=True, initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_catalog_contexts_pending_family_id", "catalog_contexts", "work_families",
        ["pending_family_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_catalog_contexts_pending_suggestion_id", "catalog_contexts", "family_suggestions",
        ["pending_suggestion_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_catalog_contexts_pending_by", "catalog_contexts", "users",
        ["pending_by"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("ck_catalog_contexts_family_source", "catalog_contexts", type_="check")
    op.create_check_constraint(
        "ck_catalog_contexts_family_source", "catalog_contexts",
        f"family_source IS NULL OR family_source IN ({FAMILY_SOURCES})",
    )
    op.create_check_constraint(
        "ck_catalog_contexts_pending_family_source", "catalog_contexts",
        f"pending_family_source IS NULL OR pending_family_source IN ({FAMILY_SOURCES})",
    )
    op.create_check_constraint(
        "ck_catalog_contexts_variant_split_hint", "catalog_contexts",
        f"variant_split_hint IS NULL OR variant_split_hint IN ({VARIANT_SPLIT_HINTS})",
    )
    op.create_check_constraint(
        "ck_catalog_contexts_variant_needs_family", "catalog_contexts",
        CK_CONTEXT_VARIANT_NEEDS_FAMILY,
    )
    op.create_check_constraint(
        "ck_catalog_contexts_variant_at_pair", "catalog_contexts", CK_CONTEXT_VARIANT_AT_PAIR
    )
    op.create_check_constraint(
        "ck_catalog_contexts_variant_paths_hash_pair", "catalog_contexts",
        CK_CONTEXT_VARIANT_PATHS_HASH_PAIR,
    )
    op.create_check_constraint(
        "ck_catalog_contexts_split_hint_needs_variant", "catalog_contexts",
        CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT,
    )
    op.create_check_constraint("ck_catalog_contexts_pending", "catalog_contexts", CK_CONTEXT_PENDING)

    # --- 7. Значения контекста по текущей версии схемы -------------------
    op.create_table(
        "context_parameter_values",
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_id", sa.BigInteger(), nullable=False),
        sa.Column("value_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        _now_column(),
        sa.PrimaryKeyConstraint("context_id", "parameter_id", name="pk_context_parameter_values"),
        sa.ForeignKeyConstraint(
            ["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT",
            name="fk_context_parameter_values_context_id",
        ),
        sa.ForeignKeyConstraint(
            ["parameter_id", "schema_id"],
            ["family_parameters.id", "family_parameters.schema_id"],
            ondelete="RESTRICT", name="fk_context_parameter_values_parameter_schema",
        ),
        sa.ForeignKeyConstraint(
            ["value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT", name="fk_context_parameter_values_value_parameter",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="SET NULL",
            name="fk_context_parameter_values_job_id",
        ),
        sa.CheckConstraint(
            f"source IN ({VALUE_SOURCES})", name="ck_context_parameter_values_source"
        ),
        sa.CheckConstraint(
            CK_CONTEXT_VALUE_SOURCE_PAIR, name="ck_context_parameter_values_source_value_pair"
        ),
    )

    # --- 8. Задания: вид, предмет, новый ключ -----------------------------
    op.add_column("semantic_jobs", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("semantic_jobs", sa.Column("family_id", sa.BigInteger(), nullable=True))
    op.add_column("semantic_jobs", sa.Column("schema_id", sa.BigInteger(), nullable=True))
    op.add_column("semantic_jobs", sa.Column("paths_hash", sa.Text(), nullable=True))
    op.execute("UPDATE semantic_jobs SET kind = 'family_suggestion'")
    op.alter_column("semantic_jobs", "kind", nullable=False)
    op.alter_column("semantic_jobs", "context_id", nullable=True)
    op.create_foreign_key(
        "fk_semantic_jobs_family_id", "semantic_jobs", "work_families",
        ["family_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_semantic_jobs_schema_id", "semantic_jobs", "family_parameter_schemas",
        ["schema_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({SEMANTIC_JOB_KINDS})"
    )
    op.create_check_constraint(
        "ck_semantic_jobs_schema_subject", "semantic_jobs", CK_SEMANTIC_JOBS_SCHEMA_SUBJECT
    )
    op.create_check_constraint(
        "ck_semantic_jobs_context_subject", "semantic_jobs", CK_SEMANTIC_JOBS_CONTEXT_SUBJECT
    )
    op.create_check_constraint(
        "ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND
    )
    op.create_check_constraint(
        "ck_semantic_jobs_result_suggestion_kind", "semantic_jobs",
        CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND,
    )
    op.drop_constraint("uq_semantic_jobs_context_request_hash", "semantic_jobs", type_="unique")
    # Ключ по виду, предмету и хэшу запроса — raw SQL (RAW_SQL_INDEXES); версия
    # схемы — часть предмета: пересборка без смены имён даёт тот же request_hash.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_semantic_jobs_subject_request_hash
        ON semantic_jobs (
            kind, COALESCE(context_id, -1), COALESCE(family_id, -1), COALESCE(schema_id, -1),
            request_hash
        )
        """
    )

    # --- 9. Предложения: новые решения ------------------------------------
    op.drop_constraint("ck_family_suggestions_decision", "family_suggestions", type_="check")
    op.create_check_constraint(
        "ck_family_suggestions_decision", "family_suggestions",
        f"decision IS NULL OR decision IN ({SUGGESTION_DECISIONS})",
    )
    op.drop_constraint(
        "ck_family_suggestions_decision_author_pair", "family_suggestions", type_="check"
    )
    op.create_check_constraint(
        "ck_family_suggestions_decision_author_pair", "family_suggestions",
        CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR,
    )

    # --- 10. Журнал: шесть типов и предмет по типу -------------------------
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({SEMANTIC_EVENT_TYPES_SQL})",
    )
    op.drop_constraint("ck_semantic_events_subject_by_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_subject_by_type", "semantic_events", CK_EVENT_SUBJECT_BY_TYPE
    )

    # --- 11. Удержанные пачки: формат отпечатков --------------------------
    _rewrite_batches(op.get_bind(), to_objects=True)


def _downgrade_blockers(bind) -> dict[str, int]:
    """Десять счётчиков, каждый сам по себе держит откат. Вынесены в функцию,
    чтобы тест проверил каждую ветвь отдельно: в валидной схеме варианты без
    значений контекста, ожидание без варианта и событие без всего остального
    существуют одновременно."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "work_variants": count("SELECT count(*) FROM work_variants"),
        "family_parameter_schemas": count("SELECT count(*) FROM family_parameter_schemas"),
        "context_parameter_values": count("SELECT count(*) FROM context_parameter_values"),
        "catalog_contexts_with_variant": count(
            "SELECT count(*) FROM catalog_contexts WHERE work_variant_id IS NOT NULL"
        ),
        "catalog_contexts_with_pending": count(
            "SELECT count(*) FROM catalog_contexts WHERE pending_family_id IS NOT NULL"
        ),
        "catalog_contexts_auto_source": count(
            "SELECT count(*) FROM catalog_contexts WHERE family_source = 'auto_suggestion'"
        ),
        "semantic_jobs_new_kinds": count(
            "SELECT count(*) FROM semantic_jobs WHERE kind <> 'family_suggestion'"
        ),
        "family_suggestions_new_decisions": count(
            f"SELECT count(*) FROM family_suggestions WHERE decision IN ({NEW_DECISIONS_SQL})"
        ),
        "semantic_events_new_types": count(
            f"SELECT count(*) FROM semantic_events WHERE event_type IN ({NEW_EVENT_TYPES_SQL})"
        ),
        "semantic_reconcile_batches_other_kinds": count(
            "SELECT count(*) FROM semantic_reconcile_batches b WHERE EXISTS ("
            "SELECT 1 FROM jsonb_array_elements(b.held_fingerprints) AS e "
            "WHERE jsonb_typeof(e) = 'object' AND e->>'kind' <> 'family_suggestion')"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0019 невозможен: вариантов — {blockers['work_variants']}, "
        f"версий схем — {blockers['family_parameter_schemas']}, "
        f"значений контекстов — {blockers['context_parameter_values']}, "
        f"контекстов с вариантом — {blockers['catalog_contexts_with_variant']}, "
        f"контекстов с ожиданием — {blockers['catalog_contexts_with_pending']}, "
        f"контекстов с источником семьи auto_suggestion — "
        f"{blockers['catalog_contexts_auto_source']}, "
        f"заданий новых видов — {blockers['semantic_jobs_new_kinds']}, "
        f"предложений с новыми решениями — {blockers['family_suggestions_new_decisions']}, "
        f"событий новых типов — {blockers['semantic_events_new_types']}, "
        f"удержанных пачек с отпечатками других видов — "
        f"{blockers['semantic_reconcile_batches_other_kinds']}. Это решения человека и "
        "оплаченные ответы модели; удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # Пачки — обратно в пары с прежним хэшем (все отпечатки уже family_suggestion).
    _rewrite_batches(bind, to_objects=False)

    # Журнал.
    op.drop_constraint("ck_semantic_events_subject_by_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_subject_by_type", "semantic_events", PREVIOUS_CK_EVENT_SUBJECT_BY_TYPE
    )
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({PREVIOUS_SEMANTIC_EVENT_TYPES_SQL})",
    )

    # Предложения.
    op.drop_constraint(
        "ck_family_suggestions_decision_author_pair", "family_suggestions", type_="check"
    )
    op.create_check_constraint(
        "ck_family_suggestions_decision_author_pair", "family_suggestions",
        PREVIOUS_CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR,
    )
    op.drop_constraint("ck_family_suggestions_decision", "family_suggestions", type_="check")
    op.create_check_constraint(
        "ck_family_suggestions_decision", "family_suggestions",
        f"decision IS NULL OR decision IN ({PREVIOUS_SUGGESTION_DECISIONS})",
    )

    # Задания: ключ возвращается обычным UNIQUE, как в 0018.
    op.execute("DROP INDEX uq_semantic_jobs_subject_request_hash")
    op.create_unique_constraint(
        "uq_semantic_jobs_context_request_hash", "semantic_jobs", ["context_id", "request_hash"]
    )
    for name in (
        "ck_semantic_jobs_result_suggestion_kind",
        "ck_semantic_jobs_schema_id_by_kind",
        "ck_semantic_jobs_context_subject",
        "ck_semantic_jobs_schema_subject",
        "ck_semantic_jobs_kind",
    ):
        op.drop_constraint(name, "semantic_jobs", type_="check")
    op.drop_constraint("fk_semantic_jobs_schema_id", "semantic_jobs", type_="foreignkey")
    op.drop_constraint("fk_semantic_jobs_family_id", "semantic_jobs", type_="foreignkey")
    op.alter_column("semantic_jobs", "context_id", nullable=False)
    for column in ("paths_hash", "schema_id", "family_id", "kind"):
        op.drop_column("semantic_jobs", column)

    # Значения контекстов и новые колонки контекстов.
    op.drop_table("context_parameter_values")
    for name in (
        "ck_catalog_contexts_pending",
        "ck_catalog_contexts_split_hint_needs_variant",
        "ck_catalog_contexts_variant_paths_hash_pair",
        "ck_catalog_contexts_variant_at_pair",
        "ck_catalog_contexts_variant_needs_family",
        "ck_catalog_contexts_variant_split_hint",
        "ck_catalog_contexts_pending_family_source",
    ):
        op.drop_constraint(name, "catalog_contexts", type_="check")
    op.drop_constraint("ck_catalog_contexts_family_source", "catalog_contexts", type_="check")
    op.create_check_constraint(
        "ck_catalog_contexts_family_source", "catalog_contexts",
        f"family_source IS NULL OR family_source IN ({PREVIOUS_FAMILY_SOURCES})",
    )
    for name in (
        "fk_catalog_contexts_pending_by",
        "fk_catalog_contexts_pending_suggestion_id",
        "fk_catalog_contexts_pending_family_id",
        "fk_catalog_contexts_work_variant_family",
    ):
        op.drop_constraint(name, "catalog_contexts", type_="foreignkey")
    for column in reversed(_CONTEXT_NEW_COLUMNS):
        op.drop_column("catalog_contexts", column)

    # Таблицы вариантов и схем — в обратном порядке зависимостей.
    op.drop_table("work_variant_values")
    op.drop_table("work_variants")
    op.drop_table("family_parameter_values")
    op.drop_table("family_parameters")
    op.execute("DROP INDEX uq_family_parameter_schemas_building")
    op.execute("DROP INDEX uq_family_parameter_schemas_frozen")
    op.drop_table("family_parameter_schemas")

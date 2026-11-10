"""Упрощение управления каталогом, часть 1 (спека `2026-11-10-catalog-simplify-design.md`
§2.3, §2.6, §2.7, §2.9, §2.13).

Пять новых таблиц, новые столбцы в четырёх существующих, пятый вид заданий очереди.

**Составное значение** (§2.3): `family_parameter_values.composite_kind` (`all`/`any`) и
`composite_key` (`<вид>:<id,id,…>`) — парой; частичный уникальный индекс — одно действующее
составное на `(параметр, ключ)`. **`family_value_parts`**: части составного; оба составных
внешних ключа идут через `(id, parameter_id)` — часть всегда того же параметра.

**Решения человека текстом, а не id** (§2.6): `composite_vetoes` и `composite_veto_parts`
(вето состава), `value_not_duplicates` («не дубль»), `catalog_contexts.auto_split_blocked_at`
и `auto_split_blocked_by` («автоделение не трогает»). Id меняются с каждой версией схемы,
нормы действующих значений — нет.

**`catalog_auto_actions`** (§2.7): журнал того, что система сделала сама. Параметр и значение
— необязательные ссылки с `ON DELETE SET NULL`: перестройка схемы удаляет их, а запись
журнала остаётся.

**`context_routing_rules.created_by`** теперь допускает `NULL`: правило заводит и
автоделение, а не только человек.

**`semantic_jobs`**: вид `auto_split` (предмет — контекст; равносильность предмета-контекста
переписана под него) и `split_level` — только у этого вида и не отрицательный.

**`kp_line_targets.kind`** + `contractor` (§2.9).

**`downgrade`** отказывает, ничего не удалив, если есть хоть что-то из перечисленного в
`_downgrade_blockers`: части и составные значения, решения человека, цели `contractor`,
записи журнала, правила без автора. Задания `auto_split` — очередь, а не решение человека:
откат удаляет их, иначе прежний CHECK вида не встанет (как 0024 поступила с
`section_zone` и `releasable_params`).

Литералы CHECK продублированы константами в `models.py` (миграция не импортирует
`models.py` — она обязана быть неизменной во времени); паритет —
`test_simplify_schema.py::TestParityWithMigration`.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во времени.
SEMANTIC_JOB_KINDS = (
    "'family_suggestion', 'family_schema', 'context_values', 'family_discovery', "
    "'section_zone', 'releasable_params', 'auto_split'"
)

# Значения до 0025 — для возврата `downgrade` (дублируют 0024 и 0021 побуквенно).
PREVIOUS_SEMANTIC_JOB_KINDS = (
    "'family_suggestion', 'family_schema', 'context_values', 'family_discovery', "
    "'section_zone', 'releasable_params'"
)
PREVIOUS_CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = (
    "(kind IN ('family_suggestion', 'context_values')) = (context_id IS NOT NULL)"
)
PREVIOUS_CK_KP_TARGET_KIND = "kind IN ('freshest', 'analog')"

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_simplify_schema.py::TestParityWithMigration.
CK_PARAMETER_VALUE_COMPOSITE_KIND = "composite_kind IN ('all', 'any')"
CK_PARAMETER_VALUE_COMPOSITE_PAIR = "(composite_kind IS NULL) = (composite_key IS NULL)"
CK_VALUE_PART_NOT_SELF = "value_id <> part_value_id"
CK_COMPOSITE_VETO_KIND = "kind IN ('all', 'any')"
CK_AUTO_ACTION_KIND = "kind IN ('split', 'value_added', 'composite', 'equated')"
CK_KP_TARGET_KIND = "kind IN ('freshest', 'analog', 'contractor')"
CK_KP_TARGET_ANALOG = "(kind = 'analog') = (analog_position_item_id IS NOT NULL)"
CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = (
    "(kind IN ('family_suggestion', 'context_values', 'auto_split')) = "
    "(context_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_SPLIT_LEVEL = (
    "split_level IS NULL OR (kind = 'auto_split' AND split_level >= 0)"
)


def upgrade() -> None:
    # --- 1. Составное значение ---------------------------------------------------------------
    op.add_column("family_parameter_values", sa.Column("composite_kind", sa.Text(), nullable=True))
    op.add_column("family_parameter_values", sa.Column("composite_key", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_family_parameter_values_composite_kind", "family_parameter_values",
        CK_PARAMETER_VALUE_COMPOSITE_KIND,
    )
    op.create_check_constraint(
        "ck_family_parameter_values_composite_pair", "family_parameter_values",
        CK_PARAMETER_VALUE_COMPOSITE_PAIR,
    )
    # Частичный уникальный индекс — raw SQL (RAW_SQL_INDEXES в alembic/env.py).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_parameter_values_composite_key
        ON family_parameter_values (parameter_id, composite_key)
        WHERE composite_key IS NOT NULL AND merged_into_id IS NULL AND retired_at IS NULL
        """
    )

    # --- 2. Части составного ---------------------------------------------------------------------
    op.create_table(
        "family_value_parts",
        sa.Column("value_id", sa.BigInteger(), nullable=False),
        sa.Column("part_value_id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("value_id", "part_value_id", name="pk_family_value_parts"),
        sa.ForeignKeyConstraint(
            ["value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="CASCADE", name="fk_family_value_parts_value",
        ),
        sa.ForeignKeyConstraint(
            ["part_value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="CASCADE", name="fk_family_value_parts_part",
        ),
        sa.CheckConstraint(CK_VALUE_PART_NOT_SELF, name="ck_family_value_parts_not_self"),
    )

    # --- 3. Вето состава ---------------------------------------------------------------------------
    op.create_table(
        "composite_vetoes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_name_norm", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_composite_vetoes"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_composite_vetoes_family",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_composite_vetoes_set_by"
        ),
        sa.CheckConstraint(CK_COMPOSITE_VETO_KIND, name="ck_composite_vetoes_kind"),
    )
    op.create_table(
        "composite_veto_parts",
        sa.Column("veto_id", sa.BigInteger(), nullable=False),
        sa.Column("part_norm", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("veto_id", "part_norm", name="pk_composite_veto_parts"),
        sa.ForeignKeyConstraint(
            ["veto_id"], ["composite_vetoes.id"], ondelete="CASCADE",
            name="fk_composite_veto_parts_veto",
        ),
    )

    # --- 4. «Не дубль» --------------------------------------------------------------------------------
    op.create_table(
        "value_not_duplicates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("parameter_name_norm", sa.Text(), nullable=False),
        sa.Column("text_norm", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_value_not_duplicates"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_value_not_duplicates_family",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_value_not_duplicates_set_by"
        ),
        sa.UniqueConstraint(
            "family_id", "parameter_name_norm", "text_norm", "value_norm",
            name="uq_value_not_duplicates_family_param_text_value",
        ),
    )

    # --- 5. Контекст: «автоделение не трогает» ---------------------------------------------------------
    op.add_column(
        "catalog_contexts", sa.Column("auto_split_blocked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("catalog_contexts", sa.Column("auto_split_blocked_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_catalog_contexts_auto_split_blocked_by", "catalog_contexts", "users",
        ["auto_split_blocked_by"], ["id"], ondelete="RESTRICT",
    )

    # --- 6. Журнал ИИ ------------------------------------------------------------------------------------
    op.create_table(
        "catalog_auto_actions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=True),
        sa.Column("parameter_id", sa.BigInteger(), nullable=True),
        sa.Column("value_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by", sa.Integer(), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_auto_actions"),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_catalog_auto_actions_family",
        ),
        sa.ForeignKeyConstraint(
            ["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT",
            name="fk_catalog_auto_actions_context",
        ),
        sa.ForeignKeyConstraint(
            ["parameter_id"], ["family_parameters.id"], ondelete="SET NULL",
            name="fk_catalog_auto_actions_parameter",
        ),
        sa.ForeignKeyConstraint(
            ["value_id"], ["family_parameter_values.id"], ondelete="SET NULL",
            name="fk_catalog_auto_actions_value",
        ),
        sa.ForeignKeyConstraint(
            ["undone_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_catalog_auto_actions_undone_by",
        ),
        sa.CheckConstraint(CK_AUTO_ACTION_KIND, name="ck_catalog_auto_actions_kind"),
    )

    # --- 7. Правило маршрутизации без автора ---------------------------------------------------------------
    op.alter_column("context_routing_rules", "created_by", existing_type=sa.Integer(), nullable=True)

    # --- 8. Задания очереди: вид auto_split и уровень деления ----------------------------------------------
    op.add_column("semantic_jobs", sa.Column("split_level", sa.Integer(), nullable=True))
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({SEMANTIC_JOB_KINDS})"
    )
    op.drop_constraint("ck_semantic_jobs_context_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_context_subject", "semantic_jobs", CK_SEMANTIC_JOBS_CONTEXT_SUBJECT
    )
    op.create_check_constraint(
        "ck_semantic_jobs_split_level", "semantic_jobs", CK_SEMANTIC_JOBS_SPLIT_LEVEL
    )

    # --- 9. Цель строки КП: «с подрядчиком» ---------------------------------------------------------------------
    op.drop_constraint("ck_kp_line_targets_kind", "kp_line_targets", type_="check")
    op.create_check_constraint("ck_kp_line_targets_kind", "kp_line_targets", CK_KP_TARGET_KIND)


def _downgrade_blockers(bind) -> dict[str, int]:
    """Счётчики того, что откат потерял бы: каждый сам по себе держит откат. Вынесены в
    функцию, чтобы тест проверил каждый отдельно.

    Задания `auto_split` в счётчиках нет: это очередь, откат её удаляет."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "family_value_parts": count("SELECT count(*) FROM family_value_parts"),
        "composite_values": count(
            "SELECT count(*) FROM family_parameter_values WHERE composite_kind IS NOT NULL"
        ),
        "composite_vetoes": count("SELECT count(*) FROM composite_vetoes"),
        "value_not_duplicates": count("SELECT count(*) FROM value_not_duplicates"),
        "contexts_auto_split_blocked": count(
            "SELECT count(*) FROM catalog_contexts WHERE auto_split_blocked_at IS NOT NULL"
        ),
        "kp_line_targets_contractor": count(
            "SELECT count(*) FROM kp_line_targets WHERE kind = 'contractor'"
        ),
        "catalog_auto_actions": count("SELECT count(*) FROM catalog_auto_actions"),
        "routing_rules_without_author": count(
            "SELECT count(*) FROM context_routing_rules WHERE created_by IS NULL"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    counts = ", ".join(f"{name} — {count}" for name, count in blockers.items() if count)
    return (
        f"Откат 0025 невозможен: есть данные, которых в прежней схеме нет: {counts}. Это "
        "составные значения, решения человека (вето состава, «не дубль», пометка контекста), "
        "цели строк КП «с подрядчиком», записи журнала ИИ и правила маршрутизации без автора; "
        "удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # --- 9. Цель строки КП ----------------------------------------------------------------------------
    op.drop_constraint("ck_kp_line_targets_kind", "kp_line_targets", type_="check")
    op.create_check_constraint(
        "ck_kp_line_targets_kind", "kp_line_targets", PREVIOUS_CK_KP_TARGET_KIND
    )

    # --- 8. Задания: auto_split — очередь, отката не держит ------------------------------------------------
    op.execute("DELETE FROM semantic_jobs WHERE kind = 'auto_split'")
    op.drop_constraint("ck_semantic_jobs_split_level", "semantic_jobs", type_="check")
    op.drop_constraint("ck_semantic_jobs_context_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_context_subject", "semantic_jobs",
        PREVIOUS_CK_SEMANTIC_JOBS_CONTEXT_SUBJECT,
    )
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({PREVIOUS_SEMANTIC_JOB_KINDS})"
    )
    op.drop_column("semantic_jobs", "split_level")

    # --- 7. Правило маршрутизации: автор снова обязателен (правил без автора нет — проверено) ----------------
    op.alter_column("context_routing_rules", "created_by", existing_type=sa.Integer(), nullable=False)

    # --- 6. Журнал ИИ --------------------------------------------------------------------------------------
    op.drop_table("catalog_auto_actions")

    # --- 5. Контекст ---------------------------------------------------------------------------------------
    op.drop_constraint(
        "fk_catalog_contexts_auto_split_blocked_by", "catalog_contexts", type_="foreignkey"
    )
    op.drop_column("catalog_contexts", "auto_split_blocked_by")
    op.drop_column("catalog_contexts", "auto_split_blocked_at")

    # --- 4. «Не дубль», 3. вето, 2. части ------------------------------------------------------------------
    op.drop_table("value_not_duplicates")
    op.drop_table("composite_veto_parts")
    op.drop_table("composite_vetoes")
    op.drop_table("family_value_parts")

    # --- 1. Составное значение -----------------------------------------------------------------------------
    op.execute("DROP INDEX uq_family_parameter_values_composite_key")
    op.drop_constraint(
        "ck_family_parameter_values_composite_pair", "family_parameter_values", type_="check"
    )
    op.drop_constraint(
        "ck_family_parameter_values_composite_kind", "family_parameter_values", type_="check"
    )
    op.drop_column("family_parameter_values", "composite_key")
    op.drop_column("family_parameter_values", "composite_kind")

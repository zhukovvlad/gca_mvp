"""Лестница аналогов: цепочки разделов и зоны, «можно отпустить», выбор цели строки КП
(спека `2026-11-07-analog-ladder-design.md` §2.18).

Три новые таблицы, три новых столбца в существующих, два новых вида заданий очереди;
таблица ручных ступеней работ `kp_line_rungs` уходит.

**`section_chains`**: цепочка названий разделов (уникален `md5(chain_key)`), зона из четырёх
кодов и её источник (правило, модель, человек), предложение модели (только у цепочки без
зоны), автор решения человека. `zone` и `zone_source` задаются парой; источник `human`
равносилен наличию автора.

**`position_items.section_chain_id`**: цепочка позиции; у глав — `NULL` (CHECK), индекс.

**`family_parameters.releasable`**: «можно отпустить», только у главного параметра;
**`releasable_proposals`**: предложение модели по параметру, исчезает вместе с ним.

**`semantic_jobs`**: столбец `section_chain_id` (предмет задания `section_zone`), виды
`section_zone` и `releasable_params`, переписанные равносильности предмета (предмет
`releasable_params` — семья и версия схемы), уникальный ключ заданий с
`COALESCE(section_chain_id, -1)`.

**`kp_line_targets`**: выбор цели строки КП — самая свежая или конкретный аналог;
`analog_position_item_id` без внешнего ключа (строка договора может уйти с заменой сметы).

**`kp_line_rungs`** удаляется. **`upgrade` отказывает**, если в ней есть строки: ручные
ступени работ — решения человека, и они не переносятся (образец `_downgrade_blockers`
0022). Отказ наступает до любой правки схемы.

**`downgrade`** отказывает при решениях человека: строка `kp_line_targets`, цепочка с
источником `human`, параметр с `releasable IS NOT NULL` и `origin = 'human'`. На пустых
носителях удаляет новое (вместе с заданиями двух новых видов и ответами модели) и
возвращает пустую `kp_line_rungs` с прежними ограничениями.

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): равносильность авторства
`(zone_source IS NOT DISTINCT FROM 'human') = (decided_by IS NOT NULL)` тотальна и при
`NULL` в `zone_source`; голое `=` пропустило бы автора у цепочки без решения.

Литералы CHECK продублированы константами в `models.py` (миграция не импортирует
`models.py` — она обязана быть неизменной во времени); паритет —
`test_analog_schema.py::TestParityWithMigration`.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во времени.
SEMANTIC_JOB_KINDS = (
    "'family_suggestion', 'family_schema', 'context_values', 'family_discovery', "
    "'section_zone', 'releasable_params'"
)

# Значения до 0024 — для возврата `downgrade` (дублируют 0019 и 0021 побуквенно).
PREVIOUS_SEMANTIC_JOB_KINDS = (
    "'family_suggestion', 'family_schema', 'context_values', 'family_discovery'"
)
PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_SUBJECT = (
    "(kind = 'family_schema') = (context_id IS NULL AND family_id IS NOT NULL)"
)
PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = (
    "(kind IN ('family_schema', 'context_values')) = (schema_id IS NOT NULL)"
)
# Ограничения `kp_line_rungs` из 0023 — для возврата пустой таблицы.
CK_KP_RUNG_LEVEL = "level IN ('v', 'p', 'f')"
CK_KP_RUNG_SCOPE = "scope IN ('cls', 'all')"

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_analog_schema.py::TestParityWithMigration.
CK_SECTION_CHAIN_ZONE = "zone IN ('underground', 'aboveground', 'territory', 'whole')"
CK_SECTION_CHAIN_ZONE_SOURCE = "zone_source IN ('rule', 'model', 'human')"
CK_SECTION_CHAIN_ZONE_PAIR = "(zone IS NULL) = (zone_source IS NULL)"
CK_SECTION_CHAIN_PROPOSED_ZONE = (
    "proposed_zone IN ('underground', 'aboveground', 'territory', 'whole', 'unknown')"
)
CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED = (
    "zone IS NULL OR (proposed_zone IS NULL AND proposed_confidence IS NULL "
    "AND proposed_reason IS NULL)"
)
CK_SECTION_CHAIN_HUMAN_DECIDED_BY = (
    "(zone_source IS NOT DISTINCT FROM 'human') = (decided_by IS NOT NULL)"
)
CK_POSITION_ITEM_CHAIN_NOT_CHAPTER = "NOT is_chapter OR section_chain_id IS NULL"
CK_FAMILY_PARAMETER_RELEASABLE_MAIN = "releasable IS NULL OR role = 'main'"
CK_RELEASABLE_CONFIDENCE = "confidence BETWEEN 0 AND 1"
CK_KP_TARGET_KIND = "kind IN ('freshest', 'analog')"
CK_KP_TARGET_ANALOG = "(kind = 'analog') = (analog_position_item_id IS NOT NULL)"
CK_SEMANTIC_JOBS_SCHEMA_SUBJECT = (
    "(kind IN ('family_schema', 'releasable_params')) = "
    "(context_id IS NULL AND family_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = (
    "(kind IN ('family_schema', 'context_values', 'releasable_params')) = "
    "(schema_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_SECTION_CHAIN_SUBJECT = (
    "(kind = 'section_zone') = (section_chain_id IS NOT NULL)"
)


def _manual_rung_count(bind) -> int:
    return bind.execute(sa.text("SELECT count(*) FROM kp_line_rungs")).scalar_one()


def _upgrade_refusal(manual_rungs: int) -> str | None:
    if manual_rungs == 0:
        return None
    return (
        f"Обновление 0024 невозможно: есть ручные ступени работ: {manual_rungs} — снимите их "
        "на экране 4б до обновления. Ручная ступень работы — решение человека, а в лестнице "
        "аналогов её места нет; удалить её — решение человека, а не миграции."
    )


def upgrade() -> None:
    refusal = _upgrade_refusal(_manual_rung_count(op.get_bind()))
    if refusal is not None:
        raise RuntimeError(refusal)

    # --- 1. Цепочки разделов ------------------------------------------------------
    # Ключ — полный нормализованный текст (на стенде до 8185 байт), а btree не
    # индексирует значения длиннее ~2704 байт: уникальность — по md5(chain_key)
    # (raw SQL, RAW_SQL_INDEXES в alembic/env.py).
    op.create_table(
        "section_chains",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("chain_key", sa.Text(), nullable=False),
        sa.Column("titles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("zone", sa.Text(), nullable=True),
        sa.Column("zone_source", sa.Text(), nullable=True),
        sa.Column("proposed_zone", sa.Text(), nullable=True),
        sa.Column("proposed_confidence", sa.Numeric(), nullable=True),
        sa.Column("proposed_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_section_chains"),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], ondelete="RESTRICT", name="fk_section_chains_decided_by"
        ),
        sa.CheckConstraint(CK_SECTION_CHAIN_ZONE, name="ck_section_chains_zone"),
        sa.CheckConstraint(CK_SECTION_CHAIN_ZONE_SOURCE, name="ck_section_chains_zone_source"),
        sa.CheckConstraint(CK_SECTION_CHAIN_ZONE_PAIR, name="ck_section_chains_zone_pair"),
        sa.CheckConstraint(CK_SECTION_CHAIN_PROPOSED_ZONE, name="ck_section_chains_proposed_zone"),
        sa.CheckConstraint(
            CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED,
            name="ck_section_chains_proposal_only_undecided",
        ),
        sa.CheckConstraint(
            CK_SECTION_CHAIN_HUMAN_DECIDED_BY, name="ck_section_chains_human_decided_by"
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_section_chains_chain_key_md5 ON section_chains (md5(chain_key))"
    )

    # --- 2. Позиция: цепочка ----------------------------------------------------------
    op.add_column("position_items", sa.Column("section_chain_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_position_items_section_chain", "position_items", "section_chains",
        ["section_chain_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_position_items_section_chain_not_chapter", "position_items",
        CK_POSITION_ITEM_CHAIN_NOT_CHAPTER,
    )
    op.create_index("idx_position_items_section_chain_id", "position_items", ["section_chain_id"])

    # --- 3. Параметр: «можно отпустить» и предложение модели --------------------------------
    op.add_column("family_parameters", sa.Column("releasable", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_family_parameters_releasable_main", "family_parameters",
        CK_FAMILY_PARAMETER_RELEASABLE_MAIN,
    )
    op.create_table(
        "releasable_proposals",
        sa.Column("parameter_id", sa.BigInteger(), nullable=False),
        sa.Column("releasable", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("parameter_id", name="pk_releasable_proposals"),
        sa.ForeignKeyConstraint(
            ["parameter_id"], ["family_parameters.id"], ondelete="CASCADE",
            name="fk_releasable_proposals_parameter",
        ),
        sa.CheckConstraint(CK_RELEASABLE_CONFIDENCE, name="ck_releasable_proposals_confidence"),
    )

    # --- 4. Задания: предмет-цепочка, два вида, ключ ----------------------------------------
    op.add_column("semantic_jobs", sa.Column("section_chain_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_semantic_jobs_section_chain", "semantic_jobs", "section_chains",
        ["section_chain_id"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({SEMANTIC_JOB_KINDS})"
    )
    op.drop_constraint("ck_semantic_jobs_schema_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_subject", "semantic_jobs", CK_SEMANTIC_JOBS_SCHEMA_SUBJECT
    )
    op.drop_constraint("ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND
    )
    op.create_check_constraint(
        "ck_semantic_jobs_section_chain_subject", "semantic_jobs",
        CK_SEMANTIC_JOBS_SECTION_CHAIN_SUBJECT,
    )
    # Ключ заданий — raw SQL (RAW_SQL_INDEXES): цепочка — часть предмета.
    op.execute("DROP INDEX uq_semantic_jobs_subject_request_hash")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_semantic_jobs_subject_request_hash
        ON semantic_jobs (
            kind, COALESCE(context_id, -1), COALESCE(family_id, -1), COALESCE(schema_id, -1),
            COALESCE(section_chain_id, -1), request_hash
        )
        """
    )

    # --- 5. Выбор цели строки КП ---------------------------------------------------------------
    op.create_table(
        "kp_line_targets",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("analog_position_item_id", sa.BigInteger(), nullable=True),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_kp_line_targets"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_kp_line_targets_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_line_targets_set_by"
        ),
        sa.CheckConstraint(CK_KP_TARGET_KIND, name="ck_kp_line_targets_kind"),
        sa.CheckConstraint(CK_KP_TARGET_ANALOG, name="ck_kp_line_targets_analog"),
    )

    # --- 6. Ручные ступени работ уходят (таблица пуста — проверено в начале) --------------------
    op.drop_table("kp_line_rungs")


def _downgrade_blockers(bind) -> dict[str, int]:
    """Три счётчика, каждый сам по себе держит откат: выбор цели, решение человека о зоне
    и ответ человека «можно отпустить» независимы друг от друга. Вынесены в функцию, чтобы
    тест проверил каждый отдельно."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "kp_line_targets": count("SELECT count(*) FROM kp_line_targets"),
        "section_chains_human": count(
            "SELECT count(*) FROM section_chains WHERE zone_source = 'human'"
        ),
        "family_parameters_releasable": count(
            "SELECT count(*) FROM family_parameters "
            "WHERE releasable IS NOT NULL AND origin = 'human'"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    counts = ", ".join(f"{name} — {count}" for name, count in blockers.items())
    return (
        f"Откат 0024 невозможен: решений человека: {counts}. Это выбор цели строки КП, зона "
        "раздела и ответ «можно отпустить»; удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # --- 6. Ручные ступени работ: пустая таблица с прежними ограничениями -----------------------
    op.create_table(
        "kp_line_rungs",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", postgresql.JSONB(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_kp_line_rungs"),
        sa.ForeignKeyConstraint(
            ["position_item_id"], ["position_items.id"], ondelete="CASCADE",
            name="fk_kp_line_rungs_position_item",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], ondelete="RESTRICT", name="fk_kp_line_rungs_set_by"
        ),
        sa.CheckConstraint(CK_KP_RUNG_LEVEL, name="ck_kp_line_rungs_level"),
        sa.CheckConstraint(CK_KP_RUNG_SCOPE, name="ck_kp_line_rungs_scope"),
    )

    # --- 5. Выбор цели строки КП ---------------------------------------------------------------
    op.drop_table("kp_line_targets")

    # --- 4. Задания: задания двух новых видов — машинные строки, отката не держат ------------
    op.execute("DELETE FROM semantic_jobs WHERE kind IN ('section_zone', 'releasable_params')")
    op.execute("DROP INDEX uq_semantic_jobs_subject_request_hash")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_semantic_jobs_subject_request_hash
        ON semantic_jobs (
            kind, COALESCE(context_id, -1), COALESCE(family_id, -1), COALESCE(schema_id, -1),
            request_hash
        )
        """
    )
    op.drop_constraint("ck_semantic_jobs_section_chain_subject", "semantic_jobs", type_="check")
    op.drop_constraint("ck_semantic_jobs_schema_id_by_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_id_by_kind", "semantic_jobs",
        PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND,
    )
    op.drop_constraint("ck_semantic_jobs_schema_subject", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_schema_subject", "semantic_jobs",
        PREVIOUS_CK_SEMANTIC_JOBS_SCHEMA_SUBJECT,
    )
    op.drop_constraint("ck_semantic_jobs_kind", "semantic_jobs", type_="check")
    op.create_check_constraint(
        "ck_semantic_jobs_kind", "semantic_jobs", f"kind IN ({PREVIOUS_SEMANTIC_JOB_KINDS})"
    )
    op.drop_constraint("fk_semantic_jobs_section_chain", "semantic_jobs", type_="foreignkey")
    op.drop_column("semantic_jobs", "section_chain_id")

    # --- 3. Параметр: «можно отпустить» и предложение модели ---------------------------------
    op.drop_table("releasable_proposals")
    op.drop_constraint(
        "ck_family_parameters_releasable_main", "family_parameters", type_="check"
    )
    op.drop_column("family_parameters", "releasable")

    # --- 2. Позиция: цепочка ----------------------------------------------------------------
    op.drop_index("idx_position_items_section_chain_id", table_name="position_items")
    op.drop_constraint(
        "ck_position_items_section_chain_not_chapter", "position_items", type_="check"
    )
    op.drop_constraint("fk_position_items_section_chain", "position_items", type_="foreignkey")
    op.drop_column("position_items", "section_chain_id")

    # --- 1. Цепочки разделов -----------------------------------------------------------------
    op.drop_table("section_chains")

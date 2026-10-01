"""Очередь семантических предложений: пять таблиц (спека
`2026-09-28-semantic-suggestions-design.md` §2.4) — задание, попытка вызова
провайдера, предложение, удержанная пачка сверх потолка события и состояние
исполнителя захвата (ровно одна строка).

Порядок создания учитывает зависимости FK: `semantic_reconcile_batches` не
зависит ни от чего нового, поэтому создаётся первой (`semantic_jobs.batch_id`
на неё ссылается); `semantic_jobs.result_suggestion_id` и
`family_suggestions.job_id` образуют ЦИКЛ — обе таблицы создаются без этой FK,
она добавляется отдельным `op.create_foreign_key` ПОСЛЕ `family_suggestions`.
`semantic_worker_state.paused_attempt_id` → `semantic_job_attempts` цикла НЕ
образует (`semantic_job_attempts` создана раньше `semantic_worker_state`) и
объявлена inline, `ON DELETE RESTRICT`: `SET NULL` здесь недостижим — CHECK
запрещает NULL при `claim_paused=true`, единственном состоянии, откуда FK мог
бы сработать.

**Два частичных уникальных индекса — raw SQL** (PostgreSQL-условие `WHERE` не
выражается декларативным `UniqueConstraint`), зарегистрированы в
`alembic/env.py` (`RAW_SQL_INDEXES`): `uq_family_suggestions_context_id_published`
(не больше одного опубликованного предложения на контекст) и
`uq_semantic_reconcile_batches_fingerprints_held` (одна удержанная пачка на
набор отпечатков; этот же индекс — арбитр `INSERT ... ON CONFLICT
(fingerprints_hash) WHERE status='held' DO NOTHING`, задача 6).

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): NOT NULL колонки
`status`/`claim_paused`/`is_published` делают каждую равносильность ниже
тотальной — голое равенство стоит только на NOT NULL стороне, вторая сторона
всегда `IS [NOT] NULL` (булево, никогда `NULL`).

**`downgrade`** отказывает, если в `semantic_jobs`, `family_suggestions` или
`semantic_reconcile_batches` есть строки (оплаченные ответы модели и аудит —
потеря данных при откате); строка `semantic_worker_state` не считается блокером
— миграция сама вставляет её на `upgrade` и сама убирает на `downgrade`. На
пустой очереди (в том числе с данными фичи 1) откат проходит.

Выражения CHECK и списки `IN (...)` продублированы константами в `models.py`
(миграция не импортирует `models.py` — она обязана быть неизменной во
времени); parity — `test_semantic_queue_schema.py`.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы.
SEMANTIC_JOB_STATUSES = "'pending', 'running', 'done', 'error', 'cancelled', 'privacy_hold'"
SEMANTIC_CANCEL_REASONS = (
    "'input_changed', 'not_applicable', 'privacy_declined', 'stale_hold'"
)
SEMANTIC_ATTEMPT_OUTCOMES = (
    "'ok', 'transient_error', 'permanent_error', 'schema_error', 'lost_claim'"
)
SUGGESTION_UNPUBLISHED_REASONS = (
    "'stale_fingerprint', 'lost_claim', 'context_not_applicable', 'rejected'"
)
SUGGESTION_DECISIONS = "'accepted', 'rejected', 'other_family', 'family_created'"
RECONCILE_BATCH_SOURCES = "'import', 'operation', 'mass', 'unit_reask', 'config_reask'"
RECONCILE_BATCH_STATUSES = "'held', 'approved', 'discarded'"

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_semantic_queue_schema.py::TestParityWithMigration.
CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR = (
    "(status = 'cancelled') = (cancel_reason IS NOT NULL)"
)
CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR = "(status = 'running') = (claim_token IS NOT NULL)"
CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES = (
    "status <> 'privacy_hold' OR privacy_matches IS NOT NULL"
)

CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR = "(decision IS NULL) = (decided_by IS NULL)"
CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR = "(decision IS NULL) = (decided_at IS NULL)"
CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON = (
    "NOT is_published OR unpublished_reason IS NULL"
)

CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY = "(status = 'held') = (decided_by IS NULL)"
CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT = "(status = 'held') = (decided_at IS NULL)"

CK_WORKER_STATE_PAUSED_REASON_PAIR = "claim_paused = (paused_reason IS NOT NULL)"
CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR = "claim_paused = (paused_attempt_id IS NOT NULL)"
CK_WORKER_STATE_PAUSED_AT_PAIR = "claim_paused = (paused_at IS NOT NULL)"
CK_WORKER_STATE_RESUMED_PAIR = "(last_resumed_by IS NULL) = (last_resumed_at IS NULL)"
CK_WORKER_STATE_SINGLETON_ID = "id = 1"


def upgrade() -> None:
    op.create_table(
        "semantic_reconcile_batches",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("import_job_id", sa.BigInteger(), nullable=True),
        sa.Column("unit_id", sa.BigInteger(), nullable=True),
        sa.Column("held_fingerprints", JSONB(), nullable=False),
        sa.Column("fingerprints_hash", sa.Text(), nullable=False),
        sa.Column("contexts_count", sa.Integer(), nullable=False),
        sa.Column("reserve_estimate_usd", sa.Numeric(), nullable=False),
        sa.Column("cached_estimate_usd", sa.Numeric(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_reconcile_batches"),
        sa.ForeignKeyConstraint(
            ["import_job_id"], ["import_jobs.id"], ondelete="SET NULL",
            name="fk_semantic_reconcile_batches_import_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_semantic_reconcile_batches_decided_by",
        ),
        sa.CheckConstraint(
            f"source IN ({RECONCILE_BATCH_SOURCES})", name="ck_semantic_reconcile_batches_source"
        ),
        sa.CheckConstraint(
            f"status IN ({RECONCILE_BATCH_STATUSES})", name="ck_semantic_reconcile_batches_status"
        ),
        sa.CheckConstraint(
            CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY,
            name="ck_semantic_reconcile_batches_held_no_decided_by",
        ),
        sa.CheckConstraint(
            CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT,
            name="ck_semantic_reconcile_batches_held_no_decided_at",
        ),
    )
    # Частичный UNIQUE по (fingerprints_hash) WHERE status='held' — raw SQL,
    # зарегистрирован в alembic/env.py RAW_SQL_INDEXES. Арбитр ON CONFLICT
    # задачи 6 — предикат буквально `status = 'held'` (решение оркестратора).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_semantic_reconcile_batches_fingerprints_held
        ON semantic_reconcile_batches (fingerprints_hash)
        WHERE status = 'held'
        """
    )

    op.create_table(
        "semantic_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("retry_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("attempts_in_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claim_token", PgUUID(as_uuid=True), nullable=True),
        sa.Column("last_error_class", sa.Text(), nullable=True),
        # result_suggestion_id — БЕЗ FK здесь: family_suggestions ещё не
        # существует (цикл). FK добавляется ниже, после создания обеих таблиц.
        sa.Column("result_suggestion_id", sa.BigInteger(), nullable=True),
        sa.Column("privacy_matches", JSONB(), nullable=True),
        sa.Column("privacy_released_matches", JSONB(), nullable=True),
        sa.Column("privacy_decided_by", sa.Integer(), nullable=True),
        sa.Column("privacy_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unit_id", sa.BigInteger(), nullable=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model_requested", sa.Text(), nullable=False),
        sa.Column("place_dictionary_version", sa.SmallInteger(), nullable=False),
        sa.Column("candidates_hash", sa.Text(), nullable=False),
        sa.Column("prefix_hash", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("response_schema_version", sa.Text(), nullable=False),
        sa.Column("serialization_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_jobs"),
        sa.ForeignKeyConstraint(
            ["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT",
            name="fk_semantic_jobs_context_id",
        ),
        sa.ForeignKeyConstraint(
            ["privacy_decided_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_semantic_jobs_privacy_decided_by",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["semantic_reconcile_batches.id"], ondelete="SET NULL",
            name="fk_semantic_jobs_batch_id",
        ),
        sa.UniqueConstraint(
            "context_id", "request_hash", name="uq_semantic_jobs_context_request_hash"
        ),
        sa.CheckConstraint(f"status IN ({SEMANTIC_JOB_STATUSES})", name="ck_semantic_jobs_status"),
        sa.CheckConstraint(
            f"cancel_reason IS NULL OR cancel_reason IN ({SEMANTIC_CANCEL_REASONS})",
            name="ck_semantic_jobs_cancel_reason",
        ),
        sa.CheckConstraint(
            CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR,
            name="ck_semantic_jobs_status_cancel_reason_pair",
        ),
        sa.CheckConstraint(
            CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR, name="ck_semantic_jobs_status_claim_token_pair"
        ),
        sa.CheckConstraint(
            CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES,
            name="ck_semantic_jobs_privacy_hold_requires_matches",
        ),
    )
    op.create_index(
        "idx_semantic_jobs_status_unit_next_attempt", "semantic_jobs",
        ["status", "unit_id", "next_attempt_at"],
    )

    op.create_table(
        "semantic_job_attempts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("claim_token", PgUUID(as_uuid=True), nullable=False),
        sa.Column("retry_generation", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("actual_model", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("reserve_usd", sa.Numeric(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(), nullable=True),
        sa.Column("reserve_exceeded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("prefix_hash", sa.Text(), nullable=False),
        sa.Column("privacy_dictionary_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_job_attempts"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="CASCADE",
            name="fk_semantic_job_attempts_job_id",
        ),
        sa.CheckConstraint(
            f"outcome IS NULL OR outcome IN ({SEMANTIC_ATTEMPT_OUTCOMES})",
            name="ck_semantic_job_attempts_outcome",
        ),
    )
    op.create_index("idx_semantic_job_attempts_prefix_hash", "semantic_job_attempts", ["prefix_hash"])
    op.create_index("idx_semantic_job_attempts_started_at", "semantic_job_attempts", ["started_at"])

    op.create_table(
        "family_suggestions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("candidates_hash", sa.Text(), nullable=False),
        sa.Column("candidates_snapshot", JSONB(), nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=True),
        sa.Column("new_family_name", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("unpublished_reason", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_family_suggestions"),
        sa.ForeignKeyConstraint(
            ["context_id"], ["catalog_contexts.id"], ondelete="RESTRICT",
            name="fk_family_suggestions_context_id",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["semantic_jobs.id"], ondelete="CASCADE",
            name="fk_family_suggestions_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["semantic_job_attempts.id"], ondelete="RESTRICT",
            name="fk_family_suggestions_attempt_id",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"], ["work_families.id"], ondelete="SET NULL",
            name="fk_family_suggestions_family_id",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_family_suggestions_decided_by",
        ),
        sa.CheckConstraint(
            f"unpublished_reason IS NULL OR unpublished_reason IN ({SUGGESTION_UNPUBLISHED_REASONS})",
            name="ck_family_suggestions_unpublished_reason",
        ),
        sa.CheckConstraint(
            f"decision IS NULL OR decision IN ({SUGGESTION_DECISIONS})",
            name="ck_family_suggestions_decision",
        ),
        sa.CheckConstraint(
            CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR,
            name="ck_family_suggestions_decision_author_pair",
        ),
        sa.CheckConstraint(
            CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR, name="ck_family_suggestions_decision_at_pair"
        ),
        sa.CheckConstraint(
            CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON,
            name="ck_family_suggestions_published_no_unpublished_reason",
        ),
    )
    # Частичный UNIQUE по (context_id) WHERE is_published — raw SQL,
    # зарегистрирован в alembic/env.py RAW_SQL_INDEXES.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_family_suggestions_context_id_published
        ON family_suggestions (context_id)
        WHERE is_published
        """
    )

    # Цикл замкнут: semantic_jobs.result_suggestion_id -> family_suggestions,
    # добавлен отдельно теперь, когда обе таблицы существуют.
    op.create_foreign_key(
        "fk_semantic_jobs_result_suggestion_id",
        "semantic_jobs", "family_suggestions",
        ["result_suggestion_id"], ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "semantic_worker_state",
        # autoincrement=False: PK — фиксированный singleton `id=1` (CHECK
        # ниже), не суррогатный счётчик; без этого PostgreSQL завёл бы
        # smallserial/последовательность.
        sa.Column("id", sa.SmallInteger(), nullable=False, autoincrement=False),
        sa.Column("claim_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("paused_attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resumed_by", sa.Integer(), nullable=True),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_worker_state"),
        # ON DELETE RESTRICT, не SET NULL: SET NULL здесь недостижим — CHECK
        # ck_semantic_worker_state_paused_attempt_pair запрещает NULL при
        # claim_paused=true, единственном состоянии, откуда FK мог бы
        # сработать. Цикла с этой FK нет — semantic_job_attempts создана
        # раньше, FK объявлена inline.
        sa.ForeignKeyConstraint(
            ["paused_attempt_id"], ["semantic_job_attempts.id"], ondelete="RESTRICT",
            name="fk_semantic_worker_state_paused_attempt_id",
        ),
        sa.ForeignKeyConstraint(
            ["last_resumed_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_semantic_worker_state_last_resumed_by",
        ),
        sa.CheckConstraint(CK_WORKER_STATE_SINGLETON_ID, name="ck_semantic_worker_state_singleton_id"),
        sa.CheckConstraint(
            CK_WORKER_STATE_PAUSED_REASON_PAIR, name="ck_semantic_worker_state_paused_reason_pair"
        ),
        sa.CheckConstraint(
            CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR, name="ck_semantic_worker_state_paused_attempt_pair"
        ),
        sa.CheckConstraint(
            CK_WORKER_STATE_PAUSED_AT_PAIR, name="ck_semantic_worker_state_paused_at_pair"
        ),
        sa.CheckConstraint(CK_WORKER_STATE_RESUMED_PAIR, name="ck_semantic_worker_state_resumed_pair"),
    )
    # Ровно одна строка (id=1, claim_paused=false) — захват не работает без
    # неё (задача 10); миграция заводит её сама.
    op.execute("INSERT INTO semantic_worker_state (id, claim_paused) VALUES (1, false)")


def _downgrade_blockers(bind) -> dict[str, int]:
    """Три счётчика, каждый сам по себе держит откат. `semantic_worker_state`
    НЕ считается: её единственная строка — служебная, заведена самой
    миграцией, а не оператором."""
    return {
        "semantic_jobs": bind.execute(sa.text("SELECT count(*) FROM semantic_jobs")).scalar_one(),
        "family_suggestions": bind.execute(
            sa.text("SELECT count(*) FROM family_suggestions")
        ).scalar_one(),
        "semantic_reconcile_batches": bind.execute(
            sa.text("SELECT count(*) FROM semantic_reconcile_batches")
        ).scalar_one(),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0018 невозможен: заданий — {blockers['semantic_jobs']}, "
        f"предложений — {blockers['family_suggestions']}, удержанных пачек — "
        f"{blockers['semantic_reconcile_batches']}. Это оплаченные ответы "
        "модели и аудит; удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    op.drop_table("semantic_worker_state")

    op.drop_constraint("fk_semantic_jobs_result_suggestion_id", "semantic_jobs", type_="foreignkey")
    op.execute("DROP INDEX uq_family_suggestions_context_id_published")
    op.drop_table("family_suggestions")

    op.drop_table("semantic_job_attempts")

    op.drop_table("semantic_jobs")

    op.execute("DROP INDEX uq_semantic_reconcile_batches_fingerprints_held")
    op.drop_table("semantic_reconcile_batches")

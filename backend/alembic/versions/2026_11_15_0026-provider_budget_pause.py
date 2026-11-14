"""Пауза опросчика до сброса лимита провайдера (спека
`2026-11-15-provider-budget-pause-design.md` §2.6).

Исход попытки `budget_refused` — провайдер отказал по деньгам (402/403): CHECK
`ck_semantic_job_attempts_outcome` пересоздаётся с ним.

`semantic_worker_state` получает пять полей паузы по времени: `budget_paused_until`,
`budget_pause_attempt_id` (FK на `semantic_job_attempts`, `ON DELETE RESTRICT`),
`budget_pause_source` (`limit_source` отказа), `budget_limit_usd`, `budget_spend_usd`.
CHECK `ck_semantic_worker_state_budget_pause_triple` — срок, попытка и источник все
`NULL` или все заданы; `ck_semantic_worker_state_budget_sums` — суммы без срока не бывают.
Оба предиката тотальны: каждый операнд — `IS [NOT] NULL`, а не сравнение.

**`downgrade`** отказывает, ничего не изменив, если есть попытка `budget_refused` или стоит
пауза по времени: прежняя схема их не выразит, а молча стереть — потерять учёт денег.

Литералы CHECK продублированы в `models.py` (миграция обязана быть неизменной во времени);
паритет — `test_provider_budget_schema.py`.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-15
"""
import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

CK_OUTCOME = "ck_semantic_job_attempts_outcome"
PREVIOUS_OUTCOMES = "'ok', 'transient_error', 'permanent_error', 'schema_error', 'lost_claim'"
OUTCOMES = PREVIOUS_OUTCOMES + ", 'budget_refused'"

CK_BUDGET_PAUSE_TRIPLE = (
    "(budget_paused_until IS NULL AND budget_pause_attempt_id IS NULL "
    "AND budget_pause_source IS NULL) OR "
    "(budget_paused_until IS NOT NULL AND budget_pause_attempt_id IS NOT NULL "
    "AND budget_pause_source IS NOT NULL)"
)
CK_BUDGET_SUMS = (
    "budget_paused_until IS NOT NULL OR "
    "(budget_limit_usd IS NULL AND budget_spend_usd IS NULL)"
)

_STATE_COLUMNS = (
    "budget_paused_until", "budget_pause_attempt_id", "budget_pause_source",
    "budget_limit_usd", "budget_spend_usd",
)


def _outcome_check(values: str) -> str:
    return f"outcome IS NULL OR outcome IN ({values})"


def upgrade() -> None:
    op.drop_constraint(CK_OUTCOME, "semantic_job_attempts", type_="check")
    op.create_check_constraint(CK_OUTCOME, "semantic_job_attempts", _outcome_check(OUTCOMES))

    op.add_column(
        "semantic_worker_state",
        sa.Column("budget_paused_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "semantic_worker_state",
        sa.Column(
            "budget_pause_attempt_id", sa.BigInteger(),
            sa.ForeignKey("semantic_job_attempts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "semantic_worker_state", sa.Column("budget_pause_source", sa.Text(), nullable=True)
    )
    op.add_column(
        "semantic_worker_state", sa.Column("budget_limit_usd", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "semantic_worker_state", sa.Column("budget_spend_usd", sa.Numeric(), nullable=True)
    )
    op.create_check_constraint(
        "ck_semantic_worker_state_budget_pause_triple", "semantic_worker_state",
        CK_BUDGET_PAUSE_TRIPLE,
    )
    op.create_check_constraint(
        "ck_semantic_worker_state_budget_sums", "semantic_worker_state", CK_BUDGET_SUMS
    )


def _downgrade_blockers(bind) -> dict[str, int]:
    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "budget_refused_attempts": count(
            "SELECT count(*) FROM semantic_job_attempts WHERE outcome = 'budget_refused'"
        ),
        "budget_pause_standing": count(
            "SELECT count(*) FROM semantic_worker_state WHERE budget_paused_until IS NOT NULL"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    counts = ", ".join(f"{name} — {count}" for name, count in blockers.items() if count)
    return (
        f"Откат 0026 невозможен: есть данные, которых в прежней схеме нет: {counts}. Это "
        "попытки с исходом budget_refused (учёт отказов провайдера по деньгам) и стоящая "
        "пауза по времени; удалить их — решение человека, а не миграции."
    )


def downgrade() -> None:
    refusal = _downgrade_refusal(_downgrade_blockers(op.get_bind()))
    if refusal is not None:
        raise RuntimeError(refusal)

    op.drop_constraint("ck_semantic_worker_state_budget_sums", "semantic_worker_state", type_="check")
    op.drop_constraint(
        "ck_semantic_worker_state_budget_pause_triple", "semantic_worker_state", type_="check"
    )
    for column in reversed(_STATE_COLUMNS):
        op.drop_column("semantic_worker_state", column)

    op.drop_constraint(CK_OUTCOME, "semantic_job_attempts", type_="check")
    op.create_check_constraint(
        CK_OUTCOME, "semantic_job_attempts", _outcome_check(PREVIOUS_OUTCOMES)
    )

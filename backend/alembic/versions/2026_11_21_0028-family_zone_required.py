"""Флажок семьи «зона обязательна» (спека `2026-11-20-zone-as-preference-design.md` §4 п. 1).

`work_families.zone_required` — подбор аналога обязан держаться зоны строки; автор и время
последней смены — `zone_required_changed_by` / `zone_required_changed_at`. Автор без времени
не бывает (`ck_work_families_zone_required_author`); время без автора — запись этой
миграции (стартовый список).

Стартовый список (`ZONE_REQUIRED_SEED`): у неархивных семей с таким названием
(`lower(btrim(title))`, во всех единицах) флажок включается, время — `now()`, автор пуст.
Названия, которых в базе не нашлось, печатаются в вывод; база без них проходит.

Выражение CHECK продублировано константой в `models.py` (миграция не импортирует `models.py`);
паритет — `test_zone_required_schema.py::TestParityWithMigration`.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-21
"""
import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

CK_FAMILY_ZONE_REQUIRED_AUTHOR = (
    "zone_required_changed_by IS NULL OR zone_required_changed_at IS NOT NULL"
)
CK_NAME = "ck_work_families_zone_required_author"

ZONE_REQUIRED_SEED: tuple[str, ...] = (
    "двери",
    "облицовка керамогранитом",
    "покраска стен",
    "вывоз и утилизация грунта",
    "светильники",
    "монтаж воздуховодов вентиляции",
    "изоляция воздуховодов",
)


def upgrade() -> None:
    op.add_column(
        "work_families",
        sa.Column("zone_required", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "work_families",
        sa.Column(
            "zone_required_changed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "work_families",
        sa.Column("zone_required_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(CK_NAME, "work_families", CK_FAMILY_ZONE_REQUIRED_AUTHOR)

    bind = op.get_bind()
    found = bind.execute(
        sa.text(
            "UPDATE work_families SET zone_required = true, zone_required_changed_at = now() "
            "WHERE lower(btrim(title)) = ANY(:titles) AND status <> 'archived' "
            "RETURNING lower(btrim(title))"
        ),
        {"titles": list(ZONE_REQUIRED_SEED)},
    ).scalars().all()
    missing = [title for title in ZONE_REQUIRED_SEED if title not in set(found)]
    if missing:
        print("0028: не найдены семьи стартового списка «зона обязательна»: " + "; ".join(missing))


def downgrade() -> None:
    op.drop_constraint(CK_NAME, "work_families", type_="check")
    op.drop_column("work_families", "zone_required_changed_at")
    op.drop_column("work_families", "zone_required_changed_by")
    op.drop_column("work_families", "zone_required")

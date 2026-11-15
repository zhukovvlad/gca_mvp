"""Справочные аналоги (спека `2026-11-15-reference-analogs-design.md` §2.6, §2.13, §2.17).

**`family_links`** (§2.6): связь пары семей. Пара упорядочена (`family_lo_id < family_hi_id`)
и уникальна; единицы сторон запоминаются на момент записи (`unit_lo_id`, `unit_hi_id`, не
пусты и различны); вид — `thickness`, `width`, `density` или `constant`. У `constant` —
множитель `factor > 0`, непустое основание `basis`, направление `factor_from_id` (одна из двух
семей) и ни одного параметра; у прочих видов множитель, основание и направление пусты, задан
хотя бы один из параметров сторон. Принадлежность параметра семье, действующая версия схемы и
соответствие единиц виду — проверка сервиса, CHECK их не видит. Все внешние ключи — `RESTRICT`.

**`kp_line_targets`**: вид цели `reference`; `analog_position_item_id` обязателен ровно при
`kind IN ('analog', 'reference')`. Снимок справочной цели (§2.13): `ref_origin`
(`zone`/`link`/`search`) и `ref_without_analogs` заданы ровно при `kind = 'reference'`;
`ref_method` (`link`/`manual`) задан вместе с `ref_factor > 0` и непустым `ref_basis`;
`ref_link_id` (внешний ключ `family_links`, `ON DELETE SET NULL`) — только при
`ref_method = 'link'`. Удаление связи оставляет цель: снимок не меняется.

**`downgrade`** отказывает, ничего не удалив, если есть хоть одна связь или справочная цель
(`_downgrade_blockers`); на пустых таблицах снимает всё новое и возвращает прежние CHECK вида
и аналога цели.

Литералы CHECK продублированы константами в `models.py` (миграция не импортирует `models.py` —
она обязана быть неизменной во времени); паритет —
`test_ref_analogs_schema.py::TestParityWithMigration`.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-15
"""
import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

# Значения до 0027 — для возврата `downgrade` (дублируют 0025 и 0024 побуквенно).
PREVIOUS_CK_KP_TARGET_KIND = "kind IN ('freshest', 'analog', 'contractor')"
PREVIOUS_CK_KP_TARGET_ANALOG = "(kind = 'analog') = (analog_position_item_id IS NOT NULL)"

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_ref_analogs_schema.py::TestParityWithMigration.
_BLANK = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"
CK_KP_TARGET_KIND = "kind IN ('freshest', 'analog', 'contractor', 'reference')"
CK_KP_TARGET_ANALOG = (
    "(kind IN ('analog', 'reference')) = (analog_position_item_id IS NOT NULL)"
)
CK_KP_TARGET_REF_ORIGIN_PRESENT = "(kind = 'reference') = (ref_origin IS NOT NULL)"
CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT = (
    "(kind = 'reference') = (ref_without_analogs IS NOT NULL)"
)
CK_KP_TARGET_REF_ORIGIN = "ref_origin IN ('zone', 'link', 'search')"
CK_KP_TARGET_REF_METHOD = "ref_method IN ('link', 'manual')"
CK_KP_TARGET_REF_CONVERSION = (
    "(ref_method IS NULL AND ref_factor IS NULL AND ref_basis IS NULL) OR "
    "(ref_method IS NOT NULL AND ref_factor IS NOT NULL AND ref_factor > 0 "
    f"AND ref_basis IS NOT NULL AND btrim(ref_basis, {_BLANK}) <> '')"
)
CK_KP_TARGET_REF_LINK = "ref_link_id IS NULL OR ref_method IS NOT DISTINCT FROM 'link'"
CK_FAMILY_LINK_ORDER = "family_lo_id < family_hi_id"
CK_FAMILY_LINK_UNITS_DIFFER = "unit_lo_id <> unit_hi_id"
CK_FAMILY_LINK_KIND = "kind IN ('thickness', 'width', 'density', 'constant')"
CK_FAMILY_LINK_SHAPE = (
    "(kind = 'constant' AND factor IS NOT NULL AND factor > 0 "
    f"AND basis IS NOT NULL AND btrim(basis, {_BLANK}) <> '' "
    "AND factor_from_id IS NOT NULL "
    "AND (factor_from_id = family_lo_id OR factor_from_id = family_hi_id) "
    "AND param_lo_id IS NULL AND param_hi_id IS NULL) OR "
    "(kind <> 'constant' AND factor IS NULL AND basis IS NULL AND factor_from_id IS NULL "
    "AND (param_lo_id IS NOT NULL OR param_hi_id IS NOT NULL))"
)


def upgrade() -> None:
    # --- 1. Связь семей ------------------------------------------------------------------------
    op.create_table(
        "family_links",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("family_lo_id", sa.BigInteger(), nullable=False),
        sa.Column("family_hi_id", sa.BigInteger(), nullable=False),
        sa.Column("unit_lo_id", sa.Integer(), nullable=False),
        sa.Column("unit_hi_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("param_lo_id", sa.BigInteger(), nullable=True),
        sa.Column("param_hi_id", sa.BigInteger(), nullable=True),
        sa.Column("factor_from_id", sa.BigInteger(), nullable=True),
        sa.Column("factor", sa.Numeric(), nullable=True),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_family_links"),
        sa.UniqueConstraint("family_lo_id", "family_hi_id", name="uq_family_links_pair"),
        sa.ForeignKeyConstraint(
            ["family_lo_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_links_family_lo",
        ),
        sa.ForeignKeyConstraint(
            ["family_hi_id"], ["work_families.id"], ondelete="RESTRICT",
            name="fk_family_links_family_hi",
        ),
        sa.ForeignKeyConstraint(
            ["unit_lo_id"], ["units_of_measure.id"], ondelete="RESTRICT",
            name="fk_family_links_unit_lo",
        ),
        sa.ForeignKeyConstraint(
            ["unit_hi_id"], ["units_of_measure.id"], ondelete="RESTRICT",
            name="fk_family_links_unit_hi",
        ),
        sa.ForeignKeyConstraint(
            ["param_lo_id"], ["family_parameters.id"], ondelete="RESTRICT",
            name="fk_family_links_param_lo",
        ),
        sa.ForeignKeyConstraint(
            ["param_hi_id"], ["family_parameters.id"], ondelete="RESTRICT",
            name="fk_family_links_param_hi",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_family_links_created_by"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], ondelete="RESTRICT", name="fk_family_links_updated_by"
        ),
        sa.CheckConstraint(CK_FAMILY_LINK_ORDER, name="ck_family_links_order"),
        sa.CheckConstraint(CK_FAMILY_LINK_UNITS_DIFFER, name="ck_family_links_units_differ"),
        sa.CheckConstraint(CK_FAMILY_LINK_KIND, name="ck_family_links_kind"),
        sa.CheckConstraint(CK_FAMILY_LINK_SHAPE, name="ck_family_links_shape"),
    )

    # --- 2. Цель строки КП: справочная и её снимок --------------------------------------------------
    op.add_column("kp_line_targets", sa.Column("ref_origin", sa.Text(), nullable=True))
    op.add_column("kp_line_targets", sa.Column("ref_method", sa.Text(), nullable=True))
    op.add_column("kp_line_targets", sa.Column("ref_factor", sa.Numeric(), nullable=True))
    op.add_column("kp_line_targets", sa.Column("ref_basis", sa.Text(), nullable=True))
    op.add_column("kp_line_targets", sa.Column("ref_link_id", sa.BigInteger(), nullable=True))
    op.add_column("kp_line_targets", sa.Column("ref_without_analogs", sa.Boolean(), nullable=True))
    op.create_foreign_key(
        "fk_kp_line_targets_ref_link", "kp_line_targets", "family_links",
        ["ref_link_id"], ["id"], ondelete="SET NULL",
    )

    op.drop_constraint("ck_kp_line_targets_kind", "kp_line_targets", type_="check")
    op.create_check_constraint("ck_kp_line_targets_kind", "kp_line_targets", CK_KP_TARGET_KIND)
    op.drop_constraint("ck_kp_line_targets_analog", "kp_line_targets", type_="check")
    op.create_check_constraint("ck_kp_line_targets_analog", "kp_line_targets", CK_KP_TARGET_ANALOG)
    for name, expression in _NEW_TARGET_CHECKS.items():
        op.create_check_constraint(name, "kp_line_targets", expression)


_NEW_TARGET_CHECKS = {
    "ck_kp_line_targets_ref_origin_present": CK_KP_TARGET_REF_ORIGIN_PRESENT,
    "ck_kp_line_targets_ref_without_analogs_present": CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT,
    "ck_kp_line_targets_ref_origin": CK_KP_TARGET_REF_ORIGIN,
    "ck_kp_line_targets_ref_method": CK_KP_TARGET_REF_METHOD,
    "ck_kp_line_targets_ref_conversion": CK_KP_TARGET_REF_CONVERSION,
    "ck_kp_line_targets_ref_link": CK_KP_TARGET_REF_LINK,
}


def _downgrade_blockers(bind) -> dict[str, int]:
    """Счётчики того, что откат потерял бы: каждый сам по себе держит откат. Вынесены в
    функцию, чтобы тест проверил каждый отдельно."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "family_links": count("SELECT count(*) FROM family_links"),
        "kp_line_targets_reference": count(
            "SELECT count(*) FROM kp_line_targets WHERE kind = 'reference'"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    counts = ", ".join(f"{name} — {count}" for name, count in blockers.items() if count)
    return (
        f"Откат 0027 невозможен: есть данные, которых в прежней схеме нет: {counts}. Это "
        "связи семей и цели строк КП «справочная»; удалить их — решение человека, а не "
        "миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # --- 2. Цель строки КП ---------------------------------------------------------------------
    for name in reversed(list(_NEW_TARGET_CHECKS)):
        op.drop_constraint(name, "kp_line_targets", type_="check")
    op.drop_constraint("ck_kp_line_targets_analog", "kp_line_targets", type_="check")
    op.create_check_constraint(
        "ck_kp_line_targets_analog", "kp_line_targets", PREVIOUS_CK_KP_TARGET_ANALOG
    )
    op.drop_constraint("ck_kp_line_targets_kind", "kp_line_targets", type_="check")
    op.create_check_constraint(
        "ck_kp_line_targets_kind", "kp_line_targets", PREVIOUS_CK_KP_TARGET_KIND
    )
    op.drop_constraint("fk_kp_line_targets_ref_link", "kp_line_targets", type_="foreignkey")
    op.drop_column("kp_line_targets", "ref_without_analogs")
    op.drop_column("kp_line_targets", "ref_link_id")
    op.drop_column("kp_line_targets", "ref_basis")
    op.drop_column("kp_line_targets", "ref_factor")
    op.drop_column("kp_line_targets", "ref_method")
    op.drop_column("kp_line_targets", "ref_origin")

    # --- 1. Связь семей ------------------------------------------------------------------------
    op.drop_table("family_links")

"""Схема «справочных аналогов» (миграция 0027; спека справочных аналогов §2.6, §2.13, §2.17).

Новое: связь семей (`family_links`), вид цели строки КП `reference` и снимок справочной
цели (`kp_line_targets.ref_*`).

Каждый тест нарушения ловит `IntegrityError` и сверяет `diag.constraint_name` с
ожидаемым именем: нарушение другого ограничения не засчитывается. Вход строится по
одному на каждое заявление проверки, остальная строка допустима
(`docs/pitfalls/db.md`). `upgrade` и `downgrade` — на отдельной scratch-базе
(`@pytest.mark.migration_roundtrip`).
"""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_FAMILY_LINK_KIND,
    CK_FAMILY_LINK_ORDER,
    CK_FAMILY_LINK_SHAPE,
    CK_FAMILY_LINK_UNITS_DIFFER,
    CK_KP_TARGET_ANALOG,
    CK_KP_TARGET_KIND,
    CK_KP_TARGET_REF_CONVERSION,
    CK_KP_TARGET_REF_LINK,
    CK_KP_TARGET_REF_METHOD,
    CK_KP_TARGET_REF_ORIGIN,
    CK_KP_TARGET_REF_ORIGIN_PRESENT,
    CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT,
    KP_TARGET_KINDS,
    LINK_KINDS,
    REF_METHODS,
    REF_ORIGINS,
    FamilyLink,
    KpLineTarget,
)
from tests.integration.test_analog_schema import _target
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_work_variants_schema import (
    _family,
    _param,
    _schema,
    _scratch_alembic,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load(pattern: str, alias: str):
    path = next(BACKEND_ROOT.glob(f"alembic/versions/{pattern}"))
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_0027():
    return _load("*0027-reference_analogs.py", "_migration_0027_ref_analogs_test")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _user_id(factories) -> int:
    return factories.UserFactory.create().id


def _unit(session, code: str) -> int:
    return session.execute(
        sa.text("SELECT id FROM units_of_measure WHERE code = :c"), {"c": code}
    ).scalar_one()


class _Pair:
    """Две семьи (`lo.id < hi.id`), по параметру у каждой и две различные единицы."""

    def __init__(self, session, factories):
        first, second = _family(session), _family(session)
        self.lo, self.hi = sorted((first, second), key=lambda f: f.id)
        self.param_lo = _param(session, _schema(session, factories, self.lo))
        self.param_hi = _param(session, _schema(session, factories, self.hi))
        self.unit_lo = _unit(session, "M2")
        self.unit_hi = _unit(session, "M3")
        self.user = _user_id(factories)


def _link(session, pair: _Pair, **overrides) -> FamilyLink:
    """Допустимая связь `thickness`; тест нарушения переопределяет ровно одно условие."""
    values = dict(
        family_lo_id=pair.lo.id, family_hi_id=pair.hi.id,
        unit_lo_id=pair.unit_lo, unit_hi_id=pair.unit_hi, kind="thickness",
        param_lo_id=pair.param_lo.id, param_hi_id=pair.param_hi.id,
        created_by=pair.user, updated_by=pair.user,
    )
    values.update(overrides)
    row = FamilyLink(**values)
    session.add(row)
    session.flush()
    return row


def _constant(pair: _Pair, **overrides) -> dict:
    """Допустимая `constant`: переопределения передаются в `_link`."""
    values = dict(
        kind="constant", param_lo_id=None, param_hi_id=None, factor=Decimal("1.5"),
        basis="насыпная плотность по паспорту", factor_from_id=pair.hi.id,
    )
    values.update(overrides)
    return values


def _reference(session, factories, **overrides) -> KpLineTarget:
    """Допустимая справочная цель без пересчёта."""
    values = dict(
        kind="reference",
        analog_position_item_id=factories.PositionItemFactory.create().id,
        ref_origin="zone", ref_without_analogs=False,
    )
    values.update(overrides)
    return _target(session, factories, **values)


def _count(session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _live_constraints(session, table: str) -> set[str]:
    rows = session.execute(
        sa.text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
        {"t": table},
    ).all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
#  1. Таблица, столбцы, имена ограничений — против независимого перечня
# ---------------------------------------------------------------------------

_LINK_CONSTRAINTS = {
    "pk_family_links", "uq_family_links_pair",
    "fk_family_links_family_lo", "fk_family_links_family_hi",
    "fk_family_links_unit_lo", "fk_family_links_unit_hi",
    "fk_family_links_param_lo", "fk_family_links_param_hi",
    "fk_family_links_created_by", "fk_family_links_updated_by",
    "ck_family_links_order", "ck_family_links_units_differ", "ck_family_links_kind",
    "ck_family_links_shape",
}

_NEW_TARGET_CONSTRAINTS = {
    "fk_kp_line_targets_ref_link",
    "ck_kp_line_targets_ref_origin_present", "ck_kp_line_targets_ref_without_analogs_present",
    "ck_kp_line_targets_ref_origin", "ck_kp_line_targets_ref_method",
    "ck_kp_line_targets_ref_conversion", "ck_kp_line_targets_ref_link",
}


class TestTablesColumnsAndConstraintNames:
    def test_family_links_carries_the_convention_names(self, db_session):
        assert _live_constraints(db_session, "family_links") == _LINK_CONSTRAINTS

    def test_the_new_target_constraints_are_named(self, db_session):
        assert _live_constraints(db_session, "kp_line_targets") >= _NEW_TARGET_CONSTRAINTS

    @pytest.mark.parametrize(
        ("table", "column", "expected"),
        [
            ("family_links", "family_lo_id", "NO"),
            ("family_links", "family_hi_id", "NO"),
            ("family_links", "unit_lo_id", "NO"),
            ("family_links", "unit_hi_id", "NO"),
            ("family_links", "kind", "NO"),
            ("family_links", "param_lo_id", "YES"),
            ("family_links", "param_hi_id", "YES"),
            ("family_links", "factor_from_id", "YES"),
            ("family_links", "factor", "YES"),
            ("family_links", "basis", "YES"),
            ("family_links", "created_by", "NO"),
            ("family_links", "created_at", "NO"),
            ("family_links", "updated_by", "NO"),
            ("family_links", "updated_at", "NO"),
            ("kp_line_targets", "ref_origin", "YES"),
            ("kp_line_targets", "ref_method", "YES"),
            ("kp_line_targets", "ref_factor", "YES"),
            ("kp_line_targets", "ref_basis", "YES"),
            ("kp_line_targets", "ref_link_id", "YES"),
            ("kp_line_targets", "ref_without_analogs", "YES"),
        ],
    )
    def test_nullability(self, db_session, table, column, expected):
        nullable = db_session.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
        assert nullable == expected

    def test_the_revision_chain(self):
        migration = _migration_0027()
        assert (migration.revision, migration.down_revision) == ("0027", "0026")

    def test_the_closed_sets(self):
        assert LINK_KINDS == ("thickness", "width", "density", "constant")
        assert REF_ORIGINS == ("zone", "link", "search")
        assert REF_METHODS == ("link", "manual")
        assert KP_TARGET_KINDS == ("freshest", "analog", "contractor", "reference")

    def test_the_foreign_key_actions(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint "
                "WHERE contype = 'f' AND conname LIKE 'fk_family_links_%' "
                "OR conname = 'fk_kp_line_targets_ref_link'"
            )
        ).all()
        # 'r' = RESTRICT, 'n' = SET NULL
        assert dict(rows) == {
            "fk_family_links_family_lo": "r", "fk_family_links_family_hi": "r",
            "fk_family_links_unit_lo": "r", "fk_family_links_unit_hi": "r",
            "fk_family_links_param_lo": "r", "fk_family_links_param_hi": "r",
            "fk_family_links_created_by": "r", "fk_family_links_updated_by": "r",
            "fk_kp_line_targets_ref_link": "n",
        }


# ---------------------------------------------------------------------------
#  2. Связь семей: допустимые строки и по одному входу на каждое условие
# ---------------------------------------------------------------------------

class TestFamilyLinkChecks:
    def test_a_thickness_link_with_both_parameters_is_accepted(self, db_session, factories):
        pair = _Pair(db_session, factories)
        assert _link(db_session, pair).id is not None

    @pytest.mark.parametrize("side", ["param_lo_id", "param_hi_id"])
    def test_a_parameter_kind_link_with_one_parameter_is_accepted(
        self, db_session, factories, side
    ):
        pair = _Pair(db_session, factories)
        other = "param_hi_id" if side == "param_lo_id" else "param_lo_id"
        assert _link(db_session, pair, kind="density", **{other: None}).id is not None

    @pytest.mark.parametrize("from_side", ["lo", "hi"])
    def test_a_constant_link_with_either_direction_is_accepted(
        self, db_session, factories, from_side
    ):
        pair = _Pair(db_session, factories)
        row = _link(db_session, pair, **_constant(pair, factor_from_id=getattr(pair, from_side).id))
        assert row.factor == Decimal("1.5")

    def test_equal_families_are_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_order"):
            _link(db_session, pair, family_hi_id=pair.lo.id)

    def test_reversed_order_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_order"):
            _link(db_session, pair, family_lo_id=pair.hi.id, family_hi_id=pair.lo.id)

    def test_a_second_link_of_the_same_pair_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        _link(db_session, pair)
        with rejected_by(db_session, "uq_family_links_pair"):
            _link(db_session, pair, kind="width")

    def test_equal_units_are_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_units_differ"):
            _link(db_session, pair, unit_hi_id=pair.unit_lo)

    def test_an_unknown_kind_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_kind"):
            _link(db_session, pair, kind="length")

    def test_a_constant_without_a_factor_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, factor=None))

    @pytest.mark.parametrize("factor", [Decimal("0"), Decimal("-2")])
    def test_a_constant_with_a_non_positive_factor_is_rejected(
        self, db_session, factories, factor
    ):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, factor=factor))

    @pytest.mark.parametrize("basis", [None, "", "   "])
    def test_a_constant_without_a_basis_is_rejected(self, db_session, factories, basis):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, basis=basis))

    def test_a_constant_without_a_direction_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, factor_from_id=None))

    def test_a_constant_directed_from_a_third_family_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        third = _family(db_session)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, factor_from_id=third.id))

    @pytest.mark.parametrize("side", ["param_lo_id", "param_hi_id"])
    def test_a_constant_with_a_parameter_is_rejected(self, db_session, factories, side):
        pair = _Pair(db_session, factories)
        parameter = pair.param_lo if side == "param_lo_id" else pair.param_hi
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **_constant(pair, **{side: parameter.id}))

    def test_a_parameter_kind_without_either_parameter_is_rejected(self, db_session, factories):
        pair = _Pair(db_session, factories)
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, param_lo_id=None, param_hi_id=None)

    @pytest.mark.parametrize(
        "extra",
        [
            {"factor": Decimal("1.5")},
            {"basis": "основание"},
            {"factor_from_id": "hi"},
        ],
        ids=["factor", "basis", "direction"],
    )
    def test_a_parameter_kind_with_a_constant_field_is_rejected(
        self, db_session, factories, extra
    ):
        pair = _Pair(db_session, factories)
        extra = {k: (pair.hi.id if v == "hi" else v) for k, v in extra.items()}
        with rejected_by(db_session, "ck_family_links_shape"):
            _link(db_session, pair, **extra)


# ---------------------------------------------------------------------------
#  3. Цель строки КП: справочная и снимок
# ---------------------------------------------------------------------------

class TestReferenceTargetChecks:
    def test_a_reference_target_without_conversion_is_accepted(self, db_session, factories):
        row = _reference(db_session, factories)
        assert (row.ref_method, row.ref_factor, row.ref_basis, row.ref_link_id) == (
            None, None, None, None,
        )

    def test_a_manual_conversion_without_a_link_is_accepted(self, db_session, factories):
        row = _reference(
            db_session, factories, ref_origin="search", ref_method="manual",
            ref_factor=Decimal("0.667"), ref_basis="по паспорту",
        )
        assert row.ref_factor == Decimal("0.667")

    def test_a_link_conversion_with_a_link_is_accepted(self, db_session, factories):
        link = _link(db_session, _Pair(db_session, factories))
        row = _reference(
            db_session, factories, ref_origin="link", ref_method="link",
            ref_factor=Decimal("2"), ref_basis="связь", ref_link_id=link.id,
        )
        assert row.ref_link_id == link.id

    def test_a_link_conversion_whose_link_was_deleted_is_accepted(self, db_session, factories):
        row = _reference(
            db_session, factories, ref_method="link", ref_factor=Decimal("2"),
            ref_basis="связь", ref_link_id=None,
        )
        assert row.ref_method == "link"

    def test_an_analog_target_without_a_snapshot_is_accepted(self, db_session, factories):
        item = factories.PositionItemFactory.create()
        row = _target(
            db_session, factories, kind="analog", analog_position_item_id=item.id
        )
        assert row.ref_origin is None

    def test_a_reference_target_without_an_analog_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_analog"):
            _reference(db_session, factories, analog_position_item_id=None)

    def test_a_target_of_another_kind_with_an_analog_is_rejected(self, db_session, factories):
        item = factories.PositionItemFactory.create()
        with rejected_by(db_session, "ck_kp_line_targets_analog"):
            _target(db_session, factories, kind="contractor", analog_position_item_id=item.id)

    def test_a_reference_target_without_an_origin_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_origin_present"):
            _reference(db_session, factories, ref_origin=None)

    def test_a_reference_target_without_the_without_analogs_flag_is_rejected(
        self, db_session, factories
    ):
        with rejected_by(db_session, "ck_kp_line_targets_ref_without_analogs_present"):
            _reference(db_session, factories, ref_without_analogs=None)

    def test_an_analog_target_with_an_origin_is_rejected(self, db_session, factories):
        item = factories.PositionItemFactory.create()
        with rejected_by(db_session, "ck_kp_line_targets_ref_origin_present"):
            _target(
                db_session, factories, kind="analog", analog_position_item_id=item.id,
                ref_origin="zone",
            )

    def test_an_analog_target_with_the_without_analogs_flag_is_rejected(
        self, db_session, factories
    ):
        item = factories.PositionItemFactory.create()
        with rejected_by(db_session, "ck_kp_line_targets_ref_without_analogs_present"):
            _target(
                db_session, factories, kind="analog", analog_position_item_id=item.id,
                ref_without_analogs=True,
            )

    def test_an_unknown_origin_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_origin"):
            _reference(db_session, factories, ref_origin="catalog")

    def test_an_unknown_method_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_method"):
            _reference(
                db_session, factories, ref_method="auto", ref_factor=Decimal("2"),
                ref_basis="основание",
            )

    def test_a_method_without_a_factor_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(db_session, factories, ref_method="manual", ref_basis="основание")

    def test_a_zero_factor_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(
                db_session, factories, ref_method="manual", ref_factor=Decimal("0"),
                ref_basis="основание",
            )

    @pytest.mark.parametrize("basis", [None, "", "  "])
    def test_a_method_without_a_basis_is_rejected(self, db_session, factories, basis):
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(
                db_session, factories, ref_method="manual", ref_factor=Decimal("2"),
                ref_basis=basis,
            )

    def test_a_factor_without_a_method_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(db_session, factories, ref_factor=Decimal("2"))

    def test_a_basis_without_a_method_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(db_session, factories, ref_basis="основание")

    def test_a_factor_and_basis_without_a_method_is_rejected(self, db_session, factories):
        """Обратная сторона равносильности: множитель и основание допустимы, нет только
        способа. Два входа выше нарушают ещё и пару «множитель — основание», поэтому
        условие «способ обязателен» само по себе держит только этот вход."""
        with rejected_by(db_session, "ck_kp_line_targets_ref_conversion"):
            _reference(
                db_session, factories, ref_factor=Decimal("2"), ref_basis="основание"
            )

    def test_a_link_with_a_manual_method_is_rejected(self, db_session, factories):
        link = _link(db_session, _Pair(db_session, factories))
        with rejected_by(db_session, "ck_kp_line_targets_ref_link"):
            _reference(
                db_session, factories, ref_method="manual", ref_factor=Decimal("2"),
                ref_basis="основание", ref_link_id=link.id,
            )

    def test_a_link_without_a_method_is_rejected(self, db_session, factories):
        link = _link(db_session, _Pair(db_session, factories))
        with rejected_by(db_session, "ck_kp_line_targets_ref_link"):
            _reference(db_session, factories, ref_link_id=link.id)

    def test_deleting_a_link_nulls_the_reference_and_keeps_the_target(
        self, db_session, factories
    ):
        link = _link(db_session, _Pair(db_session, factories))
        target = _reference(
            db_session, factories, ref_origin="link", ref_method="link",
            ref_factor=Decimal("2"), ref_basis="связь", ref_link_id=link.id,
        )
        db_session.execute(sa.text("DELETE FROM family_links WHERE id = :i"), {"i": link.id})
        row = db_session.execute(
            sa.text(
                "SELECT kind, ref_origin, ref_method, ref_factor, ref_link_id "
                "FROM kp_line_targets WHERE position_item_id = :p"
            ),
            {"p": target.position_item_id},
        ).one()
        assert tuple(row) == ("reference", "link", "link", Decimal("2"), None)


# ---------------------------------------------------------------------------
#  4. Паритет литералов CHECK модели и миграции
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    # Независимый перечень текстов: не вызов проверяемого кода.
    _CK_CASES = [
        (CK_KP_TARGET_KIND, "CK_KP_TARGET_KIND",
         "kind IN ('freshest', 'analog', 'contractor', 'reference')"),
        (CK_KP_TARGET_ANALOG, "CK_KP_TARGET_ANALOG",
         "(kind IN ('analog', 'reference')) = (analog_position_item_id IS NOT NULL)"),
        (CK_KP_TARGET_REF_ORIGIN_PRESENT, "CK_KP_TARGET_REF_ORIGIN_PRESENT",
         "(kind = 'reference') = (ref_origin IS NOT NULL)"),
        (CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT, "CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT",
         "(kind = 'reference') = (ref_without_analogs IS NOT NULL)"),
        (CK_KP_TARGET_REF_ORIGIN, "CK_KP_TARGET_REF_ORIGIN",
         "ref_origin IN ('zone', 'link', 'search')"),
        (CK_KP_TARGET_REF_METHOD, "CK_KP_TARGET_REF_METHOD",
         "ref_method IN ('link', 'manual')"),
        (CK_KP_TARGET_REF_CONVERSION, "CK_KP_TARGET_REF_CONVERSION",
         "(ref_method IS NULL AND ref_factor IS NULL AND ref_basis IS NULL) OR "
         "(ref_method IS NOT NULL AND ref_factor IS NOT NULL AND ref_factor > 0 "
         "AND ref_basis IS NOT NULL AND btrim(ref_basis, "
         "' ' || chr(9) || chr(10) || chr(13) || chr(160)) <> '')"),
        (CK_KP_TARGET_REF_LINK, "CK_KP_TARGET_REF_LINK",
         "ref_link_id IS NULL OR ref_method IS NOT DISTINCT FROM 'link'"),
        (CK_FAMILY_LINK_ORDER, "CK_FAMILY_LINK_ORDER", "family_lo_id < family_hi_id"),
        (CK_FAMILY_LINK_UNITS_DIFFER, "CK_FAMILY_LINK_UNITS_DIFFER", "unit_lo_id <> unit_hi_id"),
        (CK_FAMILY_LINK_KIND, "CK_FAMILY_LINK_KIND",
         "kind IN ('thickness', 'width', 'density', 'constant')"),
        (CK_FAMILY_LINK_SHAPE, "CK_FAMILY_LINK_SHAPE",
         "(kind = 'constant' AND factor IS NOT NULL AND factor > 0 "
         "AND basis IS NOT NULL AND btrim(basis, "
         "' ' || chr(9) || chr(10) || chr(13) || chr(160)) <> '' "
         "AND factor_from_id IS NOT NULL "
         "AND (factor_from_id = family_lo_id OR factor_from_id = family_hi_id) "
         "AND param_lo_id IS NULL AND param_hi_id IS NULL) OR "
         "(kind <> 'constant' AND factor IS NULL AND basis IS NULL AND factor_from_id IS NULL "
         "AND (param_lo_id IS NOT NULL OR param_hi_id IS NOT NULL))"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0027(), name)
        assert model_expr == independent

    def test_the_closed_sets_match_the_check_literals(self):
        def listed(values):
            return ", ".join(f"'{v}'" for v in values)

        assert f"kind IN ({listed(LINK_KINDS)})" == CK_FAMILY_LINK_KIND
        assert f"ref_origin IN ({listed(REF_ORIGINS)})" == CK_KP_TARGET_REF_ORIGIN
        assert f"ref_method IN ({listed(REF_METHODS)})" == CK_KP_TARGET_REF_METHOD
        assert f"kind IN ({listed(KP_TARGET_KINDS)})" == CK_KP_TARGET_KIND

    def test_the_previous_literals_kept_for_downgrade_equal_the_earlier_migration(self):
        m27 = _migration_0027()
        m25 = _load("*0025-catalog_simplify.py", "_migration_0025_ref_analogs_test")
        m24 = _load("*0024-analog_ladder.py", "_migration_0024_ref_analogs_test")
        assert m27.PREVIOUS_CK_KP_TARGET_KIND == m25.CK_KP_TARGET_KIND
        assert m27.PREVIOUS_CK_KP_TARGET_ANALOG == m24.CK_KP_TARGET_ANALOG

    _MODEL_CHECKS = {
        "family_links": {
            "ck_family_links_order": "CK_FAMILY_LINK_ORDER",
            "ck_family_links_units_differ": "CK_FAMILY_LINK_UNITS_DIFFER",
            "ck_family_links_kind": "CK_FAMILY_LINK_KIND",
            "ck_family_links_shape": "CK_FAMILY_LINK_SHAPE",
        },
        "kp_line_targets": {
            "ck_kp_line_targets_kind": "CK_KP_TARGET_KIND",
            "ck_kp_line_targets_analog": "CK_KP_TARGET_ANALOG",
            "ck_kp_line_targets_ref_origin_present": "CK_KP_TARGET_REF_ORIGIN_PRESENT",
            "ck_kp_line_targets_ref_without_analogs_present":
                "CK_KP_TARGET_REF_WITHOUT_ANALOGS_PRESENT",
            "ck_kp_line_targets_ref_origin": "CK_KP_TARGET_REF_ORIGIN",
            "ck_kp_line_targets_ref_method": "CK_KP_TARGET_REF_METHOD",
            "ck_kp_line_targets_ref_conversion": "CK_KP_TARGET_REF_CONVERSION",
            "ck_kp_line_targets_ref_link": "CK_KP_TARGET_REF_LINK",
        },
    }

    @pytest.mark.parametrize("table", list(_MODEL_CHECKS))
    def test_the_model_declares_the_migration_checks(self, table):
        """`alembic check` CHECK-выражения не сравнивает: что модель объявляет те же
        ограничения с теми же текстами, что миграция, держит только этот тест. Перечень
        полный (все CHECK обеих таблиц), поэтому фильтра по именам нет: лишнее CHECK
        модели, которого нет в миграциях, тоже красное."""
        from models import Base

        declared = {
            c.name: str(c.sqltext)
            for c in Base.metadata.tables[table].constraints
            if isinstance(c, sa.CheckConstraint)
        }
        migration = _migration_0027()
        expected = {
            name: getattr(migration, constant)
            for name, constant in self._MODEL_CHECKS[table].items()
        }
        assert declared == expected

    def test_the_live_check_definitions_match_the_new_shape(self, db_session):
        """Живая схема: вид цели допускает `reference`, аналог обязателен при двух видах."""
        defs = dict(
            db_session.execute(
                sa.text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST('kp_line_targets' AS regclass) AND contype = 'c'"
                )
            ).all()
        )
        assert "'reference'" in defs["ck_kp_line_targets_kind"]
        assert "'reference'" in defs["ck_kp_line_targets_analog"]


# ---------------------------------------------------------------------------
#  5. Миграция: счётчики отката, отказ, пустой откат
# ---------------------------------------------------------------------------

_BLOCKER_NAMES = ("family_links", "kp_line_targets_reference")


def _table_names(conn) -> set:
    return {
        row[0]
        for row in conn.execute(
            sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
    }


def _constraint_defs(conn) -> dict:
    return dict(
        conn.execute(
            sa.text(
                "SELECT c.conrelid::regclass::text || '.' || c.conname, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = 'public'"
            )
        ).all()
    )


def _index_defs(conn) -> dict:
    return dict(
        conn.execute(
            sa.text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
        ).all()
    )


def _columns(conn) -> set:
    return {
        tuple(row)
        for row in conn.execute(
            sa.text(
                "SELECT table_name, column_name, is_nullable, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
    }


def _revision(conn) -> str:
    return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


class TestDowngradeRefusalLogic:
    """`_downgrade_refusal` — чистая логика на вручную построенных словарях: каждый
    счётчик единственный положительный, значение (5) отличительное."""

    @pytest.mark.parametrize("name", _BLOCKER_NAMES)
    def test_each_counter_alone_blocks_and_is_named(self, name):
        blockers = {n: 0 for n in _BLOCKER_NAMES}
        blockers[name] = 5
        refusal = _migration_0027()._downgrade_refusal(blockers)
        assert refusal is not None
        assert "0027" in refusal
        assert f"{name} — 5" in refusal
        for other in _BLOCKER_NAMES:
            if other != name:
                assert f"{other} — 5" not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0027()._downgrade_refusal({n: 0 for n in _BLOCKER_NAMES}) is None

    def test_the_counters_are_exactly_the_expected_names(self, db_session):
        blockers = _migration_0027()._downgrade_blockers(db_session.connection())
        assert tuple(blockers) == _BLOCKER_NAMES


class TestDowngradeBlockersLive:
    """Каждый вид данных — свой вход на живой схеме: считается ровно его счётчик."""

    _ZERO = {name: 0 for name in _BLOCKER_NAMES}

    def _blockers(self, db_session) -> dict:
        return _migration_0027()._downgrade_blockers(db_session.connection())

    def test_empty_tables_do_not_block(self, db_session):
        assert self._blockers(db_session) == self._ZERO

    def test_a_link_raises_exactly_its_counter(self, db_session, factories):
        _link(db_session, _Pair(db_session, factories))
        assert self._blockers(db_session) == {**self._ZERO, "family_links": 1}

    def test_a_reference_target_raises_exactly_its_counter(self, db_session, factories):
        _reference(db_session, factories)
        assert self._blockers(db_session) == {**self._ZERO, "kp_line_targets_reference": 1}

    def test_an_older_target_does_not_block(self, db_session, factories):
        _target(db_session, factories, kind="contractor")
        assert self._blockers(db_session) == self._ZERO


#: Данные по одному на каждый вид. Внешние ключи при вставке отключены
#: (`session_replication_role = replica`): тест проверяет отказ отката, а не цепочку
#: родительских строк.
_SEED_ONE = {
    "family_links": (
        "INSERT INTO family_links (family_lo_id, family_hi_id, unit_lo_id, unit_hi_id, kind, "
        "param_lo_id, created_by, updated_by) VALUES (1, 2, 1, 2, 'thickness', 1, 1, 1)"
    ),
    "kp_line_targets_reference": (
        "INSERT INTO kp_line_targets (position_item_id, kind, analog_position_item_id, set_by, "
        "ref_origin, ref_without_analogs) VALUES (1, 'reference', 2, 1, 'zone', true)"
    ),
}


@pytest.mark.migration_roundtrip
class TestDowngradeRefusedByEachKind:
    @pytest.mark.parametrize("name", list(_SEED_ONE))
    def test_downgrade_is_refused_and_leaves_the_schema_untouched(self, name):
        assert set(_SEED_ONE) == set(_BLOCKER_NAMES)
        with _scratch_alembic(f"0027 downgrade refusal {name}") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0027")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    conn.execute(sa.text(_SEED_ONE[name]))
                    before = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                    data_before = _migration_0027()._downgrade_blockers(conn)
                # Вход достигает ровно своего счётчика.
                assert data_before == {**{n: 0 for n in _BLOCKER_NAMES}, name: 1}

                with pytest.raises(RuntimeError, match="0027") as exc:
                    command.downgrade(cfg, "0026")

                assert f"{name} — 1" in str(exc.value)
                with engine.connect() as conn:
                    assert _revision(conn) == "0027"
                    assert (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    ) == before
                    # «Ничего не удалив» — по данным, а не по тексту сообщения.
                    assert _migration_0027()._downgrade_blockers(conn) == data_before
            finally:
                engine.dispose()


@pytest.mark.migration_roundtrip
class TestDowngradeOnEmptyDatabase:
    def test_downgrade_removes_the_new_and_restores_the_old_schema_exactly(self):
        with _scratch_alembic("0027 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0026")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                command.upgrade(cfg, "0027")
                with engine.connect() as conn:
                    upgraded = _table_names(conn)
                    upgraded_columns = _columns(conn)
                    upgraded_constraints = _constraint_defs(conn)
                assert "family_links" in upgraded
                assert upgraded_columns != before[3]
                assert "'reference'" in upgraded_constraints["kp_line_targets.ck_kp_line_targets_kind"]

                command.downgrade(cfg, "0026")
                with engine.connect() as conn:
                    assert _revision(conn) == "0026"
                    after = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                # Прежние CHECK вида и аналога цели возвращены побуквенно.
                assert after == before

                command.upgrade(cfg, "0027")
                with engine.connect() as conn:
                    assert _table_names(conn) == upgraded
                    assert _columns(conn) == upgraded_columns
                    assert _constraint_defs(conn) == upgraded_constraints
            finally:
                engine.dispose()


# ---------------------------------------------------------------------------
#  6. Таблица — явно в _DOMAIN_TABLES, очистка её чистит
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_the_table_is_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        assert "family_links" in _DOMAIN_TABLES

    def test_truncate_clears_family_links(self, db_engine, committing_db, committing_factories):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        _link(session, _Pair(session, committing_factories))
        session.commit()

        def count():
            with db_engine.connect() as conn:
                return _count(conn, "family_links")

        assert count() == 1
        session.close()
        _truncate_domain_tables(db_engine)
        assert count() == 0

"""Схема «упрощения управления каталогом» (миграция 0025; спека упрощения каталога
§2.3, §2.6, §2.7, §2.9, §2.13).

Новое: составные значения (`family_parameter_values.composite_kind/composite_key`,
`family_value_parts`), решения человека текстом (`composite_vetoes`,
`composite_veto_parts`, `value_not_duplicates`, пометка контекста
`auto_split_blocked_*`), журнал ИИ (`catalog_auto_actions`), правило маршрутизации
без автора, вид задания `auto_split` с `semantic_jobs.split_level`, цель строки КП
`contractor`.

Каждый тест нарушения ловит `IntegrityError` и сверяет `diag.constraint_name` с
ожидаемым именем: нарушение другого ограничения не засчитывается. Вход строится по
одному на каждое заявление проверки (`docs/pitfalls/db.md`). `upgrade` и `downgrade`
— на отдельной scratch-базе (`@pytest.mark.migration_roundtrip`).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    AUTO_ACTION_KINDS,
    CK_AUTO_ACTION_KIND,
    CK_COMPOSITE_VETO_KIND,
    CK_KP_TARGET_ANALOG,
    CK_KP_TARGET_KIND,
    CK_PARAMETER_VALUE_COMPOSITE_KIND,
    CK_PARAMETER_VALUE_COMPOSITE_PAIR,
    CK_SEMANTIC_JOBS_CONTEXT_SUBJECT,
    CK_SEMANTIC_JOBS_SPLIT_LEVEL,
    CK_VALUE_PART_NOT_SELF,
    COMPOSITE_KINDS,
    KP_TARGET_KINDS,
    SEMANTIC_JOB_KINDS,
    CatalogAutoAction,
    CompositeVeto,
    CompositeVetoPart,
    ContextRoutingRule,
    FamilyValuePart,
    SemanticJobKind,
    ValueNotDuplicate,
)
from tests.integration.test_analog_schema import _target
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_schema import _bucket, _context, _job
from tests.integration.test_work_variants_schema import (
    _family,
    _param,
    _schema,
    _scratch_alembic,
    _uid,
    _value,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_NEW_TABLES = (
    "family_value_parts", "composite_vetoes", "composite_veto_parts",
    "value_not_duplicates", "catalog_auto_actions",
)


def _load(pattern: str, alias: str):
    path = next(BACKEND_ROOT.glob(f"alembic/versions/{pattern}"))
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_0025():
    return _load("*0025-catalog_simplify.py", "_migration_0025_simplify_test")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _user_id(factories) -> int:
    return factories.UserFactory.create().id


def _two_values(session, factories):
    """Два простых значения одного параметра."""
    parameter = _param(session, _schema(session, factories))
    return parameter, _value(session, parameter, "Knauf"), _value(session, parameter, "Gyproc")


def _composite(session, parameter, key, *, text=None, **overrides):
    text = text or f"составное {_uid()}"
    return _value(
        session, parameter, text, origin="extension",
        composite_kind=overrides.pop("composite_kind", "all"), composite_key=key, **overrides,
    )


def _part(session, composite, part, **overrides) -> FamilyValuePart:
    values = dict(
        value_id=composite.id, part_value_id=part.id, parameter_id=composite.parameter_id
    )
    values.update(overrides)
    row = FamilyValuePart(**values)
    session.add(row)
    session.flush()
    return row


def _veto(session, factories, family=None, **overrides) -> CompositeVeto:
    family = family or _family(session)
    values = dict(
        family_id=family.id, parameter_name_norm="марка", kind="all", value_norm="knauf",
        set_by=_user_id(factories),
    )
    values.update(overrides)
    row = CompositeVeto(**values)
    session.add(row)
    session.flush()
    return row


def _not_duplicate(session, factories, family=None, **overrides) -> ValueNotDuplicate:
    family = family or _family(session)
    values = dict(
        family_id=family.id, parameter_name_norm="марка", text_norm="кнауф",
        value_norm="knauf", set_by=_user_id(factories),
    )
    values.update(overrides)
    row = ValueNotDuplicate(**values)
    session.add(row)
    session.flush()
    return row


def _action(session, family=None, **overrides) -> CatalogAutoAction:
    family = family or _family(session)
    values = dict(kind="split", family_id=family.id, payload={"level": 1})
    values.update(overrides)
    row = CatalogAutoAction(**values)
    session.add(row)
    session.flush()
    return row


def _count(session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _live_constraints(session, table: str) -> set[str]:
    rows = session.execute(
        sa.text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
        {"t": table},
    ).all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
#  1. Таблицы, столбцы, имена ограничений — против независимого перечня
# ---------------------------------------------------------------------------

_EXPECTED_CONSTRAINTS = {
    "family_value_parts": {
        "pk_family_value_parts", "fk_family_value_parts_value", "fk_family_value_parts_part",
        "ck_family_value_parts_not_self",
    },
    "composite_vetoes": {
        "pk_composite_vetoes", "fk_composite_vetoes_family", "fk_composite_vetoes_set_by",
        "ck_composite_vetoes_kind",
    },
    "composite_veto_parts": {"pk_composite_veto_parts", "fk_composite_veto_parts_veto"},
    "value_not_duplicates": {
        "pk_value_not_duplicates", "fk_value_not_duplicates_family",
        "fk_value_not_duplicates_set_by", "uq_value_not_duplicates_family_param_text_value",
    },
    "catalog_auto_actions": {
        "pk_catalog_auto_actions", "fk_catalog_auto_actions_family",
        "fk_catalog_auto_actions_context", "fk_catalog_auto_actions_parameter",
        "fk_catalog_auto_actions_value", "fk_catalog_auto_actions_undone_by",
        "ck_catalog_auto_actions_kind",
    },
}


class TestTablesColumnsAndConstraintNames:
    def test_the_five_tables_exist(self, db_session):
        found = {
            row[0]
            for row in db_session.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert set(_NEW_TABLES) <= found

    @pytest.mark.parametrize("table", _NEW_TABLES)
    def test_constraints_carry_the_convention_names(self, db_session, table):
        assert _live_constraints(db_session, table) == _EXPECTED_CONSTRAINTS[table]

    def test_the_new_checks_and_keys_of_the_old_tables_are_named(self, db_session):
        assert {
            "ck_family_parameter_values_composite_kind",
            "ck_family_parameter_values_composite_pair",
        } <= _live_constraints(db_session, "family_parameter_values")
        assert "fk_catalog_contexts_auto_split_blocked_by" in _live_constraints(
            db_session, "catalog_contexts"
        )
        assert "ck_semantic_jobs_split_level" in _live_constraints(db_session, "semantic_jobs")

    @pytest.mark.parametrize(
        ("table", "column", "expected"),
        [
            ("family_parameter_values", "composite_kind", "YES"),
            ("family_parameter_values", "composite_key", "YES"),
            ("catalog_contexts", "auto_split_blocked_at", "YES"),
            ("catalog_contexts", "auto_split_blocked_by", "YES"),
            ("semantic_jobs", "split_level", "YES"),
            ("context_routing_rules", "created_by", "YES"),
            ("context_routing_rules", "bucket_id", "NO"),
            ("catalog_auto_actions", "family_id", "NO"),
            ("catalog_auto_actions", "payload", "NO"),
            ("catalog_auto_actions", "context_id", "YES"),
            ("catalog_auto_actions", "parameter_id", "YES"),
            ("catalog_auto_actions", "value_id", "YES"),
            ("catalog_auto_actions", "undone_at", "YES"),
            ("catalog_auto_actions", "undone_by", "YES"),
            ("catalog_auto_actions", "restored_at", "YES"),
            ("catalog_auto_actions", "created_at", "NO"),
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
        migration = _migration_0025()
        assert (migration.revision, migration.down_revision) == ("0025", "0024")

    def test_the_closed_sets(self):
        assert COMPOSITE_KINDS == ("all", "any")
        assert AUTO_ACTION_KINDS == ("split", "value_added", "composite", "equated")
        assert KP_TARGET_KINDS == ("freshest", "analog", "contractor")
        assert {kind.value for kind in SemanticJobKind} == {
            "family_suggestion", "family_schema", "context_values", "family_discovery",
            "section_zone", "releasable_params", "auto_split",
        }


# ---------------------------------------------------------------------------
#  2. Составное значение: вид, ключ, частичный уникальный индекс
# ---------------------------------------------------------------------------

class TestCompositeColumns:
    @pytest.mark.parametrize("kind", ["all", "any"])
    def test_a_composite_with_a_listed_kind_and_a_key_is_accepted(
        self, db_session, factories, kind
    ):
        parameter, *_ = _two_values(db_session, factories)
        row = _composite(db_session, parameter, f"{kind}:1,2", composite_kind=kind)
        assert (row.composite_kind, row.composite_key) == (kind, f"{kind}:1,2")

    def test_a_plain_value_without_either_is_accepted(self, db_session, factories):
        parameter, plain, _ = _two_values(db_session, factories)
        assert (plain.composite_kind, plain.composite_key) == (None, None)

    @pytest.mark.parametrize("bad", ["some", "ALL", ""])
    def test_a_kind_outside_the_two_is_rejected(self, db_session, factories, bad):
        parameter, *_ = _two_values(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_composite_kind"):
            _composite(db_session, parameter, "some:1,2", composite_kind=bad)

    def test_a_kind_without_a_key_is_rejected(self, db_session, factories):
        parameter, *_ = _two_values(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_composite_pair"):
            _value(db_session, parameter, "без ключа", composite_kind="all")

    def test_a_key_without_a_kind_is_rejected(self, db_session, factories):
        parameter, *_ = _two_values(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_composite_pair"):
            _value(db_session, parameter, "без вида", composite_key="all:1,2")

    def test_two_live_composites_with_one_key_in_a_parameter_are_rejected(
        self, db_session, factories
    ):
        parameter, *_ = _two_values(db_session, factories)
        _composite(db_session, parameter, "all:1,2")
        with rejected_by(db_session, "uq_family_parameter_values_composite_key"):
            _composite(db_session, parameter, "all:1,2")

    def test_a_merged_composite_does_not_hold_the_key(self, db_session, factories):
        parameter, plain, _ = _two_values(db_session, factories)
        _composite(db_session, parameter, "all:1,2", merged_into_id=plain.id)
        assert _composite(db_session, parameter, "all:1,2").id is not None

    def test_a_retired_composite_does_not_hold_the_key(self, db_session, factories):
        parameter, *_ = _two_values(db_session, factories)
        _composite(db_session, parameter, "all:1,2", retired_at=sa.func.now())
        assert _composite(db_session, parameter, "all:1,2").id is not None

    def test_the_same_key_in_another_parameter_is_accepted(self, db_session, factories):
        first, *_ = _two_values(db_session, factories)
        second, *_ = _two_values(db_session, factories)
        _composite(db_session, first, "all:1,2")
        assert _composite(db_session, second, "all:1,2").id is not None

    def test_the_index_is_registered_in_env(self):
        env_text = (BACKEND_ROOT / "alembic" / "env.py").read_bytes().decode("utf-8")
        assert '"uq_family_parameter_values_composite_key"' in env_text


# ---------------------------------------------------------------------------
#  3. family_value_parts
# ---------------------------------------------------------------------------

class TestFamilyValueParts:
    def test_a_part_of_the_same_parameter_is_accepted(self, db_session, factories):
        parameter, first, second = _two_values(db_session, factories)
        composite = _composite(db_session, parameter, "all:1,2")
        assert _part(db_session, composite, first).part_value_id == first.id
        assert _part(db_session, composite, second).part_value_id == second.id

    def test_a_part_of_another_parameter_is_rejected(self, db_session, factories):
        parameter, *_ = _two_values(db_session, factories)
        _, foreign, _ = _two_values(db_session, factories)
        composite = _composite(db_session, parameter, "all:1,2")
        with rejected_by(db_session, "fk_family_value_parts_part"):
            _part(db_session, composite, foreign)

    def test_a_composite_of_another_parameter_is_rejected(self, db_session, factories):
        parameter, first, _ = _two_values(db_session, factories)
        other, *_ = _two_values(db_session, factories)
        foreign_composite = _composite(db_session, other, "all:1,2")
        with rejected_by(db_session, "fk_family_value_parts_value"):
            _part(db_session, foreign_composite, first, parameter_id=parameter.id)

    def test_a_value_cannot_be_its_own_part(self, db_session, factories):
        parameter, first, _ = _two_values(db_session, factories)
        with rejected_by(db_session, "ck_family_value_parts_not_self"):
            _part(db_session, first, first)

    def test_a_part_is_listed_once(self, db_session, factories):
        parameter, first, _ = _two_values(db_session, factories)
        composite = _composite(db_session, parameter, "all:1,2")
        _part(db_session, composite, first)
        with rejected_by(db_session, "pk_family_value_parts"):
            _part(db_session, composite, first)

    def test_deleting_the_composite_removes_its_parts_only(self, db_session, factories):
        parameter, first, second = _two_values(db_session, factories)
        gone = _composite(db_session, parameter, "all:1,2")
        kept = _composite(db_session, parameter, "any:1,2", composite_kind="any")
        _part(db_session, gone, first)
        _part(db_session, kept, second)
        db_session.execute(sa.text("DELETE FROM family_parameter_values WHERE id = :i"), {"i": gone.id})
        left = db_session.execute(sa.text("SELECT value_id FROM family_value_parts")).scalars().all()
        assert left == [kept.id]

    def test_deleting_the_parameter_removes_its_composites_and_parts(self, db_session, factories):
        parameter, first, _ = _two_values(db_session, factories)
        composite = _composite(db_session, parameter, "all:1,2")
        _part(db_session, composite, first)
        db_session.execute(sa.text("DELETE FROM family_parameters WHERE id = :i"), {"i": parameter.id})
        assert _count(db_session, "family_value_parts") == 0


# ---------------------------------------------------------------------------
#  4. Решения человека текстом: вето состава и «не дубль»
# ---------------------------------------------------------------------------

class TestVetoes:
    @pytest.mark.parametrize("kind", COMPOSITE_KINDS)
    def test_a_listed_kind_is_accepted(self, db_session, factories, kind):
        assert _veto(db_session, factories, kind=kind).kind == kind

    @pytest.mark.parametrize("bad", ["some", "ALL", ""])
    def test_an_unlisted_kind_is_rejected(self, db_session, factories, bad):
        with rejected_by(db_session, "ck_composite_vetoes_kind"):
            _veto(db_session, factories, kind=bad)

    @pytest.mark.parametrize(
        "column", ["family_id", "parameter_name_norm", "kind", "value_norm", "set_by"]
    )
    def test_every_veto_column_is_required(self, db_session, factories, column):
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _veto(db_session, factories, **{column: None})
        assert exc.value.orig.diag.column_name == column

    def test_a_part_is_listed_once_per_veto(self, db_session, factories):
        veto = _veto(db_session, factories)
        db_session.add(CompositeVetoPart(veto_id=veto.id, part_norm="a"))
        db_session.add(CompositeVetoPart(veto_id=veto.id, part_norm="b"))
        db_session.flush()
        with rejected_by(db_session, "pk_composite_veto_parts"):
            db_session.add(CompositeVetoPart(veto_id=veto.id, part_norm="a"))

    def test_one_part_text_may_belong_to_two_vetoes(self, db_session, factories):
        first, second = _veto(db_session, factories), _veto(db_session, factories)
        db_session.add(CompositeVetoPart(veto_id=first.id, part_norm="a"))
        db_session.add(CompositeVetoPart(veto_id=second.id, part_norm="a"))
        db_session.flush()
        assert _count(db_session, "composite_veto_parts") == 2

    def test_deleting_a_veto_removes_its_parts_only(self, db_session, factories):
        gone, kept = _veto(db_session, factories), _veto(db_session, factories)
        db_session.add(CompositeVetoPart(veto_id=gone.id, part_norm="a"))
        db_session.add(CompositeVetoPart(veto_id=kept.id, part_norm="b"))
        db_session.flush()
        db_session.execute(sa.text("DELETE FROM composite_vetoes WHERE id = :i"), {"i": gone.id})
        left = db_session.execute(sa.text("SELECT veto_id FROM composite_veto_parts")).scalars().all()
        assert left == [kept.id]

    def test_a_user_with_a_veto_cannot_be_deleted(self, db_session, factories):
        veto = _veto(db_session, factories)
        with rejected_by(db_session, "fk_composite_vetoes_set_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": veto.set_by})

    def test_a_family_with_a_veto_cannot_be_deleted(self, db_session, factories):
        veto = _veto(db_session, factories)
        with rejected_by(db_session, "fk_composite_vetoes_family"):
            db_session.execute(sa.text("DELETE FROM work_families WHERE id = :i"), {"i": veto.family_id})

    def test_set_at_is_filled_by_the_server(self, db_session, factories):
        veto = _veto(db_session, factories)
        db_session.refresh(veto)
        assert veto.set_at is not None and veto.set_at.tzinfo is not None


class TestNotDuplicates:
    def test_the_same_pair_twice_is_rejected(self, db_session, factories):
        family = _family(db_session)
        _not_duplicate(db_session, factories, family)
        with rejected_by(db_session, "uq_value_not_duplicates_family_param_text_value"):
            _not_duplicate(db_session, factories, family)

    @pytest.mark.parametrize(
        "other",
        [
            {"parameter_name_norm": "толщина"},
            {"text_norm": "другой текст"},
            {"value_norm": "gyproc"},
        ],
        ids=["parameter", "text", "value"],
    )
    def test_a_pair_that_differs_in_one_column_is_accepted(self, db_session, factories, other):
        family = _family(db_session)
        _not_duplicate(db_session, factories, family)
        assert _not_duplicate(db_session, factories, family, **other).id is not None

    def test_the_same_pair_in_another_family_is_accepted(self, db_session, factories):
        _not_duplicate(db_session, factories)
        assert _not_duplicate(db_session, factories).id is not None

    @pytest.mark.parametrize(
        "column", ["family_id", "parameter_name_norm", "text_norm", "value_norm", "set_by"]
    )
    def test_every_column_is_required(self, db_session, factories, column):
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _not_duplicate(db_session, factories, **{column: None})
        assert exc.value.orig.diag.column_name == column

    def test_a_user_with_a_decision_cannot_be_deleted(self, db_session, factories):
        row = _not_duplicate(db_session, factories)
        with rejected_by(db_session, "fk_value_not_duplicates_set_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": row.set_by})

    def test_a_family_with_a_decision_cannot_be_deleted(self, db_session, factories):
        row = _not_duplicate(db_session, factories)
        with rejected_by(db_session, "fk_value_not_duplicates_family"):
            db_session.execute(sa.text("DELETE FROM work_families WHERE id = :i"), {"i": row.family_id})


# ---------------------------------------------------------------------------
#  5. Журнал ИИ
# ---------------------------------------------------------------------------

class TestAutoActions:
    @pytest.mark.parametrize("kind", AUTO_ACTION_KINDS)
    def test_every_listed_kind_is_accepted(self, db_session, kind):
        assert _action(db_session, kind=kind).kind == kind

    @pytest.mark.parametrize("bad", ["undo", "Split", ""])
    def test_a_kind_outside_the_four_is_rejected(self, db_session, bad):
        with rejected_by(db_session, "ck_catalog_auto_actions_kind"):
            _action(db_session, kind=bad)

    def test_the_subjects_are_optional_and_the_family_is_not(self, db_session):
        row = _action(db_session)
        assert (row.context_id, row.parameter_id, row.value_id) == (None, None, None)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _action(db_session, family_id=None)
            db_session.flush()
        assert exc.value.orig.diag.column_name == "family_id"

    def test_the_payload_is_required(self, db_session):
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _action(db_session, payload=None)
        assert exc.value.orig.diag.column_name == "payload"

    def test_a_row_round_trips_with_every_subject_and_the_undo_marks(self, db_session, factories):
        parameter, value, _ = _two_values(db_session, factories)
        context = _context(db_session, factories)
        family = _family(db_session)
        user_id = _user_id(factories)
        row = _action(
            db_session, family, context_id=context.id, parameter_id=parameter.id,
            value_id=value.id, undone_at=sa.func.now(), undone_by=user_id,
            restored_at=sa.func.now(), payload={"groups": [1, 2]},
        )
        db_session.refresh(row)
        assert (row.context_id, row.parameter_id, row.value_id, row.undone_by) == (
            context.id, parameter.id, value.id, user_id,
        )
        assert row.payload == {"groups": [1, 2]}
        assert row.created_at is not None and row.created_at.tzinfo is not None

    def test_a_family_with_an_action_cannot_be_deleted(self, db_session):
        row = _action(db_session)
        with rejected_by(db_session, "fk_catalog_auto_actions_family"):
            db_session.execute(sa.text("DELETE FROM work_families WHERE id = :i"), {"i": row.family_id})

    def test_a_context_with_an_action_cannot_be_deleted(self, db_session, factories):
        context = _context(db_session, factories)
        _action(db_session, context_id=context.id)
        with rejected_by(db_session, "fk_catalog_auto_actions_context"):
            db_session.execute(sa.text("DELETE FROM catalog_contexts WHERE id = :i"), {"i": context.id})

    def test_a_user_who_undid_an_action_cannot_be_deleted(self, db_session, factories):
        user_id = _user_id(factories)
        _action(db_session, undone_at=sa.func.now(), undone_by=user_id)
        with rejected_by(db_session, "fk_catalog_auto_actions_undone_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": user_id})

    def test_deleting_the_parameter_keeps_the_action_and_clears_its_subjects(
        self, db_session, factories
    ):
        parameter, value, _ = _two_values(db_session, factories)
        row = _action(db_session, parameter_id=parameter.id, value_id=value.id)
        db_session.execute(sa.text("DELETE FROM family_parameters WHERE id = :i"), {"i": parameter.id})
        db_session.refresh(row)
        assert (row.parameter_id, row.value_id) == (None, None)


# ---------------------------------------------------------------------------
#  6. Контекст: пометка «автоделение не трогает»; правило без автора
# ---------------------------------------------------------------------------

class TestContextAndRoutingRule:
    def test_a_context_takes_the_block_mark(self, db_session, factories):
        user_id = _user_id(factories)
        context = _context(
            db_session, factories, auto_split_blocked_at=sa.func.now(),
            auto_split_blocked_by=user_id,
        )
        db_session.refresh(context)
        assert context.auto_split_blocked_by == user_id
        assert context.auto_split_blocked_at is not None

    def test_a_context_without_the_mark_is_accepted(self, db_session, factories):
        context = _context(db_session, factories)
        assert (context.auto_split_blocked_at, context.auto_split_blocked_by) == (None, None)

    def test_a_user_who_set_the_mark_cannot_be_deleted(self, db_session, factories):
        user_id = _user_id(factories)
        _context(db_session, factories, auto_split_blocked_at=sa.func.now(),
                 auto_split_blocked_by=user_id)
        with rejected_by(db_session, "fk_catalog_contexts_auto_split_blocked_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": user_id})

    def test_a_routing_rule_without_an_author_is_inserted(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        context = _context(db_session, factories, bucket=bucket)
        rule = ContextRoutingRule(
            bucket_id=bucket.id, ordinal=1, predicate={"k": 1}, context_id=context.id,
            created_by=None,
        )
        db_session.add(rule)
        db_session.flush()
        assert rule.created_by is None

    def test_a_routing_rule_with_an_author_is_still_inserted(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        context = _context(db_session, factories, bucket=bucket)
        user_id = _user_id(factories)
        rule = ContextRoutingRule(
            bucket_id=bucket.id, ordinal=1, predicate={"k": 1}, context_id=context.id,
            created_by=user_id,
        )
        db_session.add(rule)
        db_session.flush()
        assert rule.created_by == user_id


# ---------------------------------------------------------------------------
#  7. Цель строки КП «с подрядчиком»
# ---------------------------------------------------------------------------

class TestKpTargetContractor:
    def test_the_contractor_target_without_an_analog_is_accepted(self, db_session, factories):
        assert _target(db_session, factories, kind="contractor").kind == "contractor"

    def test_the_contractor_target_with_an_analog_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_analog"):
            _target(db_session, factories, kind="contractor", analog_position_item_id=7)

    @pytest.mark.parametrize("kind", ["freshest", "analog"])
    def test_the_two_older_kinds_are_still_accepted(self, db_session, factories, kind):
        analog = 7 if kind == "analog" else None
        assert _target(db_session, factories, kind=kind, analog_position_item_id=analog).kind == kind

    @pytest.mark.parametrize("bad", ["Contractor", "", "oldest"])
    def test_an_unlisted_kind_is_rejected(self, db_session, factories, bad):
        with rejected_by(db_session, "ck_kp_line_targets_kind"):
            _target(db_session, factories, kind=bad)


# ---------------------------------------------------------------------------
#  8. Задание очереди «автоделение»
# ---------------------------------------------------------------------------

class TestAutoSplitJob:
    def test_an_auto_split_job_on_a_context_is_accepted(self, db_session, factories):
        job = _job(db_session, factories, kind="auto_split", split_level=0)
        assert (job.kind, job.split_level) == ("auto_split", 0)

    def test_an_auto_split_job_without_a_level_is_accepted(self, db_session, factories):
        assert _job(db_session, factories, kind="auto_split").split_level is None

    def test_an_auto_split_job_without_a_context_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_context_subject"):
            _job(db_session, factories, kind="auto_split", context_id=None, split_level=1)

    def test_a_level_on_another_kind_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_split_level"):
            _job(db_session, factories, split_level=1)

    def test_a_negative_level_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_split_level"):
            _job(db_session, factories, kind="auto_split", split_level=-1)

    def test_the_older_kinds_keep_their_context_rule(self, db_session, factories):
        assert _job(db_session, factories).kind == "family_suggestion"
        with rejected_by(db_session, "ck_semantic_jobs_context_subject"):
            _job(db_session, factories, context_id=None)

    def test_the_job_key_still_tells_two_contexts_apart(self, db_session, factories):
        first = _context(db_session, factories)
        second = _context(db_session, factories)
        _job(db_session, factories, first, kind="auto_split", request_hash="same")
        _job(db_session, factories, second, kind="auto_split", request_hash="same")
        with rejected_by(db_session, "uq_semantic_jobs_subject_request_hash"):
            _job(db_session, factories, first, kind="auto_split", request_hash="same")


# ---------------------------------------------------------------------------
#  9. Паритет модели и миграции
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    _CK_CASES = [
        (CK_PARAMETER_VALUE_COMPOSITE_KIND, "CK_PARAMETER_VALUE_COMPOSITE_KIND",
         "composite_kind IN ('all', 'any')"),
        (CK_PARAMETER_VALUE_COMPOSITE_PAIR, "CK_PARAMETER_VALUE_COMPOSITE_PAIR",
         "(composite_kind IS NULL) = (composite_key IS NULL)"),
        (CK_VALUE_PART_NOT_SELF, "CK_VALUE_PART_NOT_SELF", "value_id <> part_value_id"),
        (CK_COMPOSITE_VETO_KIND, "CK_COMPOSITE_VETO_KIND", "kind IN ('all', 'any')"),
        (CK_AUTO_ACTION_KIND, "CK_AUTO_ACTION_KIND",
         "kind IN ('split', 'value_added', 'composite', 'equated')"),
        (CK_KP_TARGET_KIND, "CK_KP_TARGET_KIND", "kind IN ('freshest', 'analog', 'contractor')"),
        (CK_KP_TARGET_ANALOG, "CK_KP_TARGET_ANALOG",
         "(kind = 'analog') = (analog_position_item_id IS NOT NULL)"),
        (SEMANTIC_JOB_KINDS, "SEMANTIC_JOB_KINDS",
         "'family_suggestion', 'family_schema', 'context_values', 'family_discovery', "
         "'section_zone', 'releasable_params', 'auto_split'"),
        (CK_SEMANTIC_JOBS_CONTEXT_SUBJECT, "CK_SEMANTIC_JOBS_CONTEXT_SUBJECT",
         "(kind IN ('family_suggestion', 'context_values', 'auto_split')) = "
         "(context_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_SPLIT_LEVEL, "CK_SEMANTIC_JOBS_SPLIT_LEVEL",
         "split_level IS NULL OR (kind = 'auto_split' AND split_level >= 0)"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0025(), name)
        assert model_expr == independent

    def test_the_closed_sets_match_the_check_literals(self):
        def listed(values):
            return ", ".join(f"'{v}'" for v in values)

        assert f"composite_kind IN ({listed(COMPOSITE_KINDS)})" == CK_PARAMETER_VALUE_COMPOSITE_KIND
        assert f"kind IN ({listed(COMPOSITE_KINDS)})" == CK_COMPOSITE_VETO_KIND
        assert f"kind IN ({listed(AUTO_ACTION_KINDS)})" == CK_AUTO_ACTION_KIND
        assert f"kind IN ({listed(KP_TARGET_KINDS)})" == CK_KP_TARGET_KIND

    def test_the_previous_literals_kept_for_downgrade_equal_the_earlier_migrations(self):
        m25 = _migration_0025()
        m24 = _load("*0024-analog_ladder.py", "_migration_0024_simplify_test")
        m21 = _load("*0021-catalog_discovery.py", "_migration_0021_simplify_test")
        assert m25.PREVIOUS_SEMANTIC_JOB_KINDS == m24.SEMANTIC_JOB_KINDS
        assert m25.PREVIOUS_CK_KP_TARGET_KIND == m24.CK_KP_TARGET_KIND
        assert m25.PREVIOUS_CK_SEMANTIC_JOBS_CONTEXT_SUBJECT == m21.CK_SEMANTIC_JOBS_CONTEXT_SUBJECT

    _MODEL_CHECKS = {
        "family_parameter_values": {
            "ck_family_parameter_values_composite_kind": "CK_PARAMETER_VALUE_COMPOSITE_KIND",
            "ck_family_parameter_values_composite_pair": "CK_PARAMETER_VALUE_COMPOSITE_PAIR",
        },
        "family_value_parts": {"ck_family_value_parts_not_self": "CK_VALUE_PART_NOT_SELF"},
        "composite_vetoes": {"ck_composite_vetoes_kind": "CK_COMPOSITE_VETO_KIND"},
        "catalog_auto_actions": {"ck_catalog_auto_actions_kind": "CK_AUTO_ACTION_KIND"},
        "kp_line_targets": {
            "ck_kp_line_targets_kind": "CK_KP_TARGET_KIND",
            "ck_kp_line_targets_analog": "CK_KP_TARGET_ANALOG",
        },
        "semantic_jobs": {
            "ck_semantic_jobs_context_subject": "CK_SEMANTIC_JOBS_CONTEXT_SUBJECT",
            "ck_semantic_jobs_split_level": "CK_SEMANTIC_JOBS_SPLIT_LEVEL",
        },
    }

    @pytest.mark.parametrize("table", list(_MODEL_CHECKS))
    def test_the_model_declares_the_migration_checks(self, table):
        """`alembic check` CHECK-выражения не сравнивает: что модель объявляет те же
        ограничения с теми же текстами, что миграция, держит только этот тест. Берутся
        только перечисленные имена: остальные CHECK таблицы — чужих миграций."""
        from models import Base

        declared = {
            c.name: str(c.sqltext)
            for c in Base.metadata.tables[table].constraints
            if isinstance(c, sa.CheckConstraint) and c.name in self._MODEL_CHECKS[table]
        }
        migration = _migration_0025()
        expected = {
            name: getattr(migration, constant)
            for name, constant in self._MODEL_CHECKS[table].items()
        }
        assert declared == expected

    def test_the_foreign_key_actions(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f' AND conname = ANY(:n)"
            ),
            {
                "n": [
                    "fk_family_value_parts_value", "fk_family_value_parts_part",
                    "fk_composite_vetoes_family", "fk_composite_vetoes_set_by",
                    "fk_composite_veto_parts_veto", "fk_value_not_duplicates_family",
                    "fk_value_not_duplicates_set_by", "fk_catalog_auto_actions_family",
                    "fk_catalog_auto_actions_context", "fk_catalog_auto_actions_parameter",
                    "fk_catalog_auto_actions_value", "fk_catalog_auto_actions_undone_by",
                    "fk_catalog_contexts_auto_split_blocked_by",
                ]
            },
        ).all()
        # 'c' = CASCADE, 'r' = RESTRICT, 'n' = SET NULL
        assert dict(rows) == {
            "fk_family_value_parts_value": "c",
            "fk_family_value_parts_part": "c",
            "fk_composite_vetoes_family": "r",
            "fk_composite_vetoes_set_by": "r",
            "fk_composite_veto_parts_veto": "c",
            "fk_value_not_duplicates_family": "r",
            "fk_value_not_duplicates_set_by": "r",
            "fk_catalog_auto_actions_family": "r",
            "fk_catalog_auto_actions_context": "r",
            "fk_catalog_auto_actions_parameter": "n",
            "fk_catalog_auto_actions_value": "n",
            "fk_catalog_auto_actions_undone_by": "r",
            "fk_catalog_contexts_auto_split_blocked_by": "r",
        }


# ---------------------------------------------------------------------------
#  10. Миграция: счётчики отката, пустой откат
# ---------------------------------------------------------------------------

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


_BLOCKER_NAMES = (
    "family_value_parts", "composite_values", "composite_vetoes", "value_not_duplicates",
    "contexts_auto_split_blocked", "kp_line_targets_contractor", "catalog_auto_actions",
    "routing_rules_without_author",
)


class TestDowngradeRefusalLogic:
    """`_downgrade_refusal` — чистая логика на вручную построенных словарях: каждый
    счётчик единственный положительный, значение (5) отличительное."""

    @pytest.mark.parametrize("name", _BLOCKER_NAMES)
    def test_each_counter_alone_blocks_and_is_named(self, name):
        blockers = {n: 0 for n in _BLOCKER_NAMES}
        blockers[name] = 5
        refusal = _migration_0025()._downgrade_refusal(blockers)
        assert refusal is not None
        assert "0025" in refusal
        assert f"{name} — 5" in refusal
        for other in _BLOCKER_NAMES:
            if other != name:
                assert f"{other} — 5" not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0025()._downgrade_refusal({n: 0 for n in _BLOCKER_NAMES}) is None

    def test_the_counters_are_exactly_the_expected_names(self, db_session):
        blockers = _migration_0025()._downgrade_blockers(db_session.connection())
        assert tuple(blockers) == _BLOCKER_NAMES


class TestDowngradeBlockersLive:
    """Каждый вид данных — свой вход на живой схеме: считается ровно его счётчик."""

    _ZERO = {name: 0 for name in _BLOCKER_NAMES}

    def _blockers(self, db_session) -> dict:
        return _migration_0025()._downgrade_blockers(db_session.connection())

    def test_empty_tables_do_not_block(self, db_session):
        assert self._blockers(db_session) == self._ZERO

    def test_a_part_row_raises_its_counter_and_the_composite_counter(self, db_session, factories):
        parameter, first, _ = _two_values(db_session, factories)
        composite = _composite(db_session, parameter, "all:1,2")
        _part(db_session, composite, first)
        assert self._blockers(db_session) == {
            **self._ZERO, "family_value_parts": 1, "composite_values": 1,
        }

    def test_a_composite_value_without_parts_raises_only_its_counter(self, db_session, factories):
        parameter, *_ = _two_values(db_session, factories)
        _composite(db_session, parameter, "all:1,2")
        assert self._blockers(db_session) == {**self._ZERO, "composite_values": 1}

    def test_a_veto_raises_exactly_its_counter(self, db_session, factories):
        _veto(db_session, factories)
        assert self._blockers(db_session) == {**self._ZERO, "composite_vetoes": 1}

    def test_a_not_duplicate_raises_exactly_its_counter(self, db_session, factories):
        _not_duplicate(db_session, factories)
        assert self._blockers(db_session) == {**self._ZERO, "value_not_duplicates": 1}

    def test_a_blocked_context_raises_exactly_its_counter(self, db_session, factories):
        _context(
            db_session, factories, auto_split_blocked_at=sa.func.now(),
            auto_split_blocked_by=_user_id(factories),
        )
        assert self._blockers(db_session) == {**self._ZERO, "contexts_auto_split_blocked": 1}

    def test_a_contractor_target_raises_exactly_its_counter(self, db_session, factories):
        _target(db_session, factories, kind="contractor")
        assert self._blockers(db_session) == {**self._ZERO, "kp_line_targets_contractor": 1}

    def test_an_older_target_does_not_block(self, db_session, factories):
        _target(db_session, factories, kind="freshest")
        assert self._blockers(db_session) == self._ZERO

    def test_an_action_raises_exactly_its_counter(self, db_session):
        _action(db_session)
        assert self._blockers(db_session) == {**self._ZERO, "catalog_auto_actions": 1}

    def test_a_rule_without_an_author_raises_exactly_its_counter(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        context = _context(db_session, factories, bucket=bucket)
        db_session.add(
            ContextRoutingRule(
                bucket_id=bucket.id, ordinal=1, predicate={"k": 1}, context_id=context.id,
                created_by=None,
            )
        )
        db_session.flush()
        assert self._blockers(db_session) == {**self._ZERO, "routing_rules_without_author": 1}

    def test_a_rule_with_an_author_does_not_block(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        context = _context(db_session, factories, bucket=bucket)
        db_session.add(
            ContextRoutingRule(
                bucket_id=bucket.id, ordinal=1, predicate={"k": 1}, context_id=context.id,
                created_by=_user_id(factories),
            )
        )
        db_session.flush()
        assert self._blockers(db_session) == self._ZERO


#: Данные по одному на каждый вид. Внешние ключи при вставке отключены
#: (`session_replication_role = replica`): тест проверяет отказ отката, а не цепочку
#: родительских строк.
_SEED_ONE = {
    "family_value_parts": (
        "INSERT INTO family_value_parts (value_id, part_value_id, parameter_id) VALUES (1, 2, 1)"
    ),
    "composite_values": (
        "INSERT INTO family_parameter_values "
        "(parameter_id, value, value_norm, origin, composite_kind, composite_key) "
        "VALUES (1, 'a + b', 'a + b', 'extension', 'all', 'all:1,2')"
    ),
    "composite_vetoes": (
        "INSERT INTO composite_vetoes (family_id, parameter_name_norm, kind, value_norm, set_by) "
        "VALUES (1, 'p', 'all', 'v', 1)"
    ),
    "value_not_duplicates": (
        "INSERT INTO value_not_duplicates "
        "(family_id, parameter_name_norm, text_norm, value_norm, set_by) "
        "VALUES (1, 'p', 't', 'v', 1)"
    ),
    "contexts_auto_split_blocked": (
        "INSERT INTO catalog_contexts "
        "(bucket_id, semantic_kind, semantic_kind_source, semantic_kind_at, name_role, "
        " name_role_source, name_role_at, place_dictionary_version, semantic_state, "
        " auto_split_blocked_at, auto_split_blocked_by) "
        "VALUES (1, 'WORK', 'rule', now(), 'WORK', 'rule', now(), 1, 'SUGGESTED', now(), 1)"
    ),
    "kp_line_targets_contractor": (
        "INSERT INTO kp_line_targets (position_item_id, kind, set_by) VALUES (1, 'contractor', 1)"
    ),
    "catalog_auto_actions": (
        "INSERT INTO catalog_auto_actions (kind, family_id, payload) "
        "VALUES ('split', 1, CAST('{}' AS jsonb))"
    ),
    "routing_rules_without_author": (
        "INSERT INTO context_routing_rules (bucket_id, ordinal, predicate, context_id, created_by) "
        "VALUES (1, 1, CAST('{}' AS jsonb), 1, NULL)"
    ),
}


#: Задание `auto_split` рядом с данными-блокером: это единственные строки, которые
#: откат удаляет сам (до сужения CHECK вида). Отказ обязан оставить и их.
_SEED_AUTO_SPLIT_JOB = (
    "INSERT INTO semantic_jobs (kind, context_id, split_level, request_hash, status, "
    "prompt_version, model_requested, place_dictionary_version, candidates_hash, prefix_hash, "
    "input_hash, response_schema_version, serialization_version) VALUES ('auto_split', 1, 1, "
    "'split-job', 'pending', '1', 'm', 1, 'c', 'p', 'i', '1', '1')"
)


def _data_state(conn) -> tuple:
    """Данные, которые откат удалил бы: счётчики-блокеры и задания `auto_split`."""
    return (
        _migration_0025()._downgrade_blockers(conn),
        conn.execute(
            sa.text("SELECT count(*) FROM semantic_jobs WHERE kind = 'auto_split'")
        ).scalar_one(),
    )


@pytest.mark.migration_roundtrip
class TestDowngradeRefusedByEachKind:
    @pytest.mark.parametrize("name", list(_SEED_ONE))
    def test_downgrade_is_refused_and_leaves_the_schema_untouched(self, name):
        assert set(_SEED_ONE) == set(_BLOCKER_NAMES)
        with _scratch_alembic(f"0025 downgrade refusal {name}") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0025")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    conn.execute(sa.text(_SEED_ONE[name]))
                    conn.execute(sa.text(_SEED_AUTO_SPLIT_JOB))
                    before = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                    data_before = _data_state(conn)
                # Вход достигает ровно своего счётчика, и задание очереди есть.
                assert data_before == ({**{n: 0 for n in _BLOCKER_NAMES}, name: 1}, 1)

                with pytest.raises(RuntimeError, match="0025") as exc:
                    command.downgrade(cfg, "0024")

                assert f"{name} — 1" in str(exc.value)
                with engine.connect() as conn:
                    assert _revision(conn) == "0025"
                    assert (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    ) == before
                    # «Ничего не удалив» — по данным, а не по тексту сообщения.
                    assert _data_state(conn) == data_before
            finally:
                engine.dispose()


@pytest.mark.migration_roundtrip
class TestDowngradeOnEmptyDatabase:
    def test_downgrade_removes_the_new_and_restores_the_old_schema_exactly(self):
        with _scratch_alembic("0025 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0024")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                command.upgrade(cfg, "0025")
                with engine.connect() as conn:
                    upgraded = _table_names(conn)
                    upgraded_columns = _columns(conn)
                assert set(_NEW_TABLES) <= upgraded
                assert upgraded_columns != before[3]

                command.downgrade(cfg, "0024")
                with engine.connect() as conn:
                    assert _revision(conn) == "0024"
                    after = (
                        _table_names(conn), _constraint_defs(conn), _index_defs(conn),
                        _columns(conn),
                    )
                assert after == before
                with engine.connect() as conn:
                    nullable = conn.execute(
                        sa.text(
                            "SELECT is_nullable FROM information_schema.columns "
                            "WHERE table_name = 'context_routing_rules' AND column_name = 'created_by'"
                        )
                    ).scalar_one()
                assert nullable == "NO"

                command.upgrade(cfg, "0025")
                with engine.connect() as conn:
                    assert _table_names(conn) == upgraded
                    assert _columns(conn) == upgraded_columns
            finally:
                engine.dispose()

    def test_auto_split_jobs_go_and_the_older_jobs_stay(self):
        """Задание очереди — не решение человека: откат его удаляет, иначе прежний CHECK
        вида не встал бы; задания прежних видов остаются."""
        from tests.integration.test_catalog_discovery_schema import _seed_three_job_kinds

        with _scratch_alembic("0025 downgrade jobs") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0025")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    _seed_three_job_kinds(conn)
                    context_id = conn.execute(
                        sa.text("SELECT min(id) FROM catalog_contexts")
                    ).scalar_one()
                    conn.execute(
                        sa.text(
                            "INSERT INTO semantic_jobs (kind, context_id, split_level, request_hash, "
                            "status, prompt_version, model_requested, place_dictionary_version, "
                            "candidates_hash, prefix_hash, input_hash, response_schema_version, "
                            "serialization_version) VALUES ('auto_split', :c, 1, 'split-job', "
                            "'pending', '1', 'm', 1, 'c', 'p', 'i', '1', '1')"
                        ),
                        {"c": context_id},
                    )
                    assert conn.execute(sa.text("SELECT count(*) FROM semantic_jobs")).scalar_one() == 4

                command.downgrade(cfg, "0024")

                with engine.connect() as conn:
                    assert _revision(conn) == "0024"
                    kinds = conn.execute(
                        sa.text("SELECT kind FROM semantic_jobs ORDER BY kind")
                    ).scalars().all()
                assert kinds == ["context_values", "family_schema", "family_suggestion"]
            finally:
                engine.dispose()


# ---------------------------------------------------------------------------
#  11. Пять таблиц — явно в _DOMAIN_TABLES, очистка их чистит
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_the_five_tables_are_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in _NEW_TABLES:
            assert table in _DOMAIN_TABLES

    def test_truncate_clears_the_five_tables(self, db_engine, committing_db, committing_factories):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        parameter, first, second = _two_values(session, committing_factories)
        composite = _composite(session, parameter, "all:1,2")
        _part(session, composite, first)
        veto = _veto(session, committing_factories)
        session.add(CompositeVetoPart(veto_id=veto.id, part_norm="a"))
        _not_duplicate(session, committing_factories)
        _action(session)
        session.commit()

        def counts():
            with db_engine.connect() as conn:
                return {table: _count(conn, table) for table in _NEW_TABLES}

        assert counts() == {table: 1 for table in _NEW_TABLES}
        session.close()
        _truncate_domain_tables(db_engine)
        assert counts() == {table: 0 for table in _NEW_TABLES}

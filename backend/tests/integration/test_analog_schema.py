"""Схема лестницы аналогов (миграция 0024; спека §2.18).

Три новые таблицы — `section_chains`, `releasable_proposals`, `kp_line_targets`;
новые столбцы — `position_items.section_chain_id`, `family_parameters.releasable`,
`semantic_jobs.section_chain_id`; `kp_line_rungs` удаляется. Каждый тест нарушения
ловит `IntegrityError` и сверяет `diag.constraint_name` с ожидаемым именем: нарушение
другого ограничения не засчитывается.

CHECK в PostgreSQL отвергает только `FALSE`, `NULL` проходит: предикаты здесь тотальны,
а входы строятся по одному на каждое заявление проверки, в том числе «заполнено только
зависимое поле» (`docs/pitfalls/db.md`).

`upgrade` и `downgrade` — на отдельной scratch-базе (`@pytest.mark.migration_roundtrip`).
"""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_FAMILY_PARAMETER_RELEASABLE_MAIN,
    CK_KP_TARGET_ANALOG,
    CK_KP_TARGET_KIND,
    CK_POSITION_ITEM_CHAIN_NOT_CHAPTER,
    CK_RELEASABLE_CONFIDENCE,
    CK_SECTION_CHAIN_HUMAN_DECIDED_BY,
    CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED,
    CK_SECTION_CHAIN_PROPOSED_ZONE,
    CK_SECTION_CHAIN_ZONE,
    CK_SECTION_CHAIN_ZONE_PAIR,
    CK_SECTION_CHAIN_ZONE_SOURCE,
    CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND,
    CK_SEMANTIC_JOBS_SCHEMA_SUBJECT,
    CK_SEMANTIC_JOBS_SECTION_CHAIN_SUBJECT,
    KP_TARGET_KINDS,
    SEMANTIC_JOB_KINDS,
    ZONE_SOURCES,
    ZONES,
    KpLineTarget,
    ReleasableProposal,
    SectionChain,
    SemanticJobKind,
)
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_schema import _job
from tests.integration.test_work_variants_schema import (
    _chain,
    _family,
    _param,
    _schema,
    _scratch_alembic,
    _uid,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_NEW_TABLES = ("section_chains", "releasable_proposals", "kp_line_targets")


def _migration_0024():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0024-analog_ladder.py"))
    spec = importlib.util.spec_from_file_location("_migration_0024_analog_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _user_id(factories) -> int:
    return factories.UserFactory.create().id


def _section_chain(session, **overrides) -> SectionChain:
    values = dict(chain_key=f"chain-{_uid()}", titles=["Раздел А", "Подраздел Б"])
    values.update(overrides)
    row = SectionChain(**values)
    session.add(row)
    session.flush()
    return row


def _target(session, factories, item=None, **overrides) -> KpLineTarget:
    item = item or factories.PositionItemFactory.create()
    values = dict(
        position_item_id=item.id, kind="freshest", analog_position_item_id=None,
        set_by=_user_id(factories),
    )
    values.update(overrides)
    row = KpLineTarget(**values)
    session.add(row)
    session.flush()
    return row


def _proposal(session, parameter, **overrides) -> ReleasableProposal:
    values = dict(
        parameter_id=parameter.id, releasable=True, confidence=Decimal("0.9"),
        reason="Причина", request_hash=f"hash-{_uid()}",
    )
    values.update(overrides)
    row = ReleasableProposal(**values)
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
#  1. Таблицы, столбцы и имена ограничений — против независимого перечня
# ---------------------------------------------------------------------------

_EXPECTED_CONSTRAINTS = {
    "section_chains": {
        "pk_section_chains", "uq_section_chains_chain_key", "fk_section_chains_decided_by",
        "ck_section_chains_zone", "ck_section_chains_zone_source",
        "ck_section_chains_zone_pair", "ck_section_chains_proposed_zone",
        "ck_section_chains_proposal_only_undecided", "ck_section_chains_human_decided_by",
    },
    "releasable_proposals": {
        "pk_releasable_proposals", "fk_releasable_proposals_parameter",
        "ck_releasable_proposals_confidence",
    },
    "kp_line_targets": {
        "pk_kp_line_targets", "fk_kp_line_targets_position_item", "fk_kp_line_targets_set_by",
        "ck_kp_line_targets_kind", "ck_kp_line_targets_analog",
    },
}


class TestTablesColumnsAndConstraintNames:
    def test_the_three_tables_exist_and_the_manual_rung_table_is_gone(self, db_session):
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
        assert "kp_line_rungs" not in found

    @pytest.mark.parametrize("table", _NEW_TABLES)
    def test_constraints_carry_the_convention_names(self, db_session, table):
        assert _live_constraints(db_session, table) == _EXPECTED_CONSTRAINTS[table]

    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("position_items", "section_chain_id"),
            ("family_parameters", "releasable"),
            ("semantic_jobs", "section_chain_id"),
        ],
    )
    def test_the_new_columns_are_nullable(self, db_session, table, column):
        nullable = db_session.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
        assert nullable == "YES"

    def test_the_new_checks_and_keys_of_the_old_tables_are_named(self, db_session):
        assert {
            "ck_position_items_section_chain_not_chapter", "fk_position_items_section_chain",
        } <= _live_constraints(db_session, "position_items")
        assert "ck_family_parameters_releasable_main" in _live_constraints(
            db_session, "family_parameters"
        )
        assert {
            "ck_semantic_jobs_section_chain_subject", "fk_semantic_jobs_section_chain",
        } <= _live_constraints(db_session, "semantic_jobs")

    def test_the_revision_chain(self):
        migration = _migration_0024()
        assert (migration.revision, migration.down_revision) == ("0024", "0023")

    def test_the_closed_sets(self):
        assert ZONES == ("underground", "aboveground", "territory", "whole")
        assert ZONE_SOURCES == ("rule", "model", "human")
        assert KP_TARGET_KINDS == ("freshest", "analog")
        assert {kind.value for kind in SemanticJobKind} == {
            "family_suggestion", "family_schema", "context_values", "family_discovery",
            "section_zone", "releasable_params",
        }


# ---------------------------------------------------------------------------
#  2. section_chains
# ---------------------------------------------------------------------------

class TestSectionChain:
    @pytest.mark.parametrize("zone", ZONES)
    def test_every_listed_zone_is_accepted_with_a_rule_source(self, db_session, zone):
        row = _section_chain(db_session, zone=zone, zone_source="rule")
        assert row.zone == zone

    def test_a_chain_without_a_decision_is_accepted(self, db_session):
        row = _section_chain(db_session)
        assert (row.zone, row.zone_source, row.decided_by) == (None, None, None)

    @pytest.mark.parametrize("bad", ["Underground", "", "unknown", "building"])
    def test_a_zone_outside_the_four_codes_is_rejected(self, db_session, bad):
        with rejected_by(db_session, "ck_section_chains_zone"):
            _section_chain(db_session, zone=bad, zone_source="rule")

    def test_a_zone_without_a_source_is_rejected(self, db_session):
        with rejected_by(db_session, "ck_section_chains_zone_pair"):
            _section_chain(db_session, zone="whole", zone_source=None)

    def test_a_source_without_a_zone_is_rejected(self, db_session):
        with rejected_by(db_session, "ck_section_chains_zone_pair"):
            _section_chain(db_session, zone=None, zone_source="rule")

    def test_an_unlisted_source_is_rejected(self, db_session):
        with rejected_by(db_session, "ck_section_chains_zone_source"):
            _section_chain(db_session, zone="whole", zone_source="robot")

    def test_the_human_source_without_an_author_is_rejected(self, db_session):
        with rejected_by(db_session, "ck_section_chains_human_decided_by"):
            _section_chain(db_session, zone="whole", zone_source="human")

    def test_an_author_with_a_non_human_source_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_section_chains_human_decided_by"):
            _section_chain(
                db_session, zone="whole", zone_source="model", decided_by=_user_id(factories)
            )

    def test_an_author_alone_without_any_decision_is_rejected(self, db_session, factories):
        """Заполнено ТОЛЬКО зависимое поле: при `NULL` в `zone_source` голое
        `(zone_source = 'human') = (decided_by IS NOT NULL)` пропустило бы строку."""
        with rejected_by(db_session, "ck_section_chains_human_decided_by"):
            _section_chain(db_session, decided_by=_user_id(factories))

    def test_a_human_decision_with_an_author_is_accepted(self, db_session, factories):
        row = _section_chain(
            db_session, zone="territory", zone_source="human", decided_by=_user_id(factories)
        )
        assert row.decided_by is not None

    @pytest.mark.parametrize(
        "proposal",
        [
            {"proposed_zone": "whole"},
            {"proposed_confidence": Decimal("0.5")},
            {"proposed_reason": "Причина"},
        ],
        ids=["zone", "confidence", "reason"],
    )
    def test_a_proposal_next_to_a_decided_zone_is_rejected(self, db_session, proposal):
        with rejected_by(db_session, "ck_section_chains_proposal_only_undecided"):
            _section_chain(db_session, zone="whole", zone_source="rule", **proposal)

    def test_a_proposal_on_an_undecided_chain_is_accepted_including_unknown(self, db_session):
        row = _section_chain(
            db_session, proposed_zone="unknown", proposed_confidence=Decimal("0.3"),
            proposed_reason="Не определить",
        )
        assert row.proposed_zone == "unknown"

    def test_a_proposed_zone_outside_the_codes_is_rejected(self, db_session):
        with rejected_by(db_session, "ck_section_chains_proposed_zone"):
            _section_chain(db_session, proposed_zone="roof")

    def test_the_chain_key_is_unique(self, db_session):
        _section_chain(db_session, chain_key="same")
        with rejected_by(db_session, "uq_section_chains_chain_key"):
            _section_chain(db_session, chain_key="same")

    def test_titles_round_trip_as_an_array(self, db_session):
        row = _section_chain(db_session, titles=["Первый", "Второй", "Третий"])
        db_session.expire(row)
        assert row.titles == ["Первый", "Второй", "Третий"]

    def test_a_user_with_a_decision_cannot_be_deleted(self, db_session, factories):
        user_id = _user_id(factories)
        _section_chain(db_session, zone="whole", zone_source="human", decided_by=user_id)
        with rejected_by(db_session, "fk_section_chains_decided_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user_id})


# ---------------------------------------------------------------------------
#  3. position_items.section_chain_id
# ---------------------------------------------------------------------------

class TestPositionItemChain:
    def test_a_position_may_point_at_a_chain(self, db_session, factories):
        chain = _section_chain(db_session)
        item = factories.PositionItemFactory.create()
        item.section_chain_id = chain.id
        db_session.flush()
        assert item.section_chain_id == chain.id

    def test_a_chapter_with_a_chain_is_rejected(self, db_session, factories):
        chain = _section_chain(db_session)
        chapter = factories.PositionItemFactory.create(is_chapter=True)
        with rejected_by(db_session, "ck_position_items_section_chain_not_chapter"):
            chapter.section_chain_id = chain.id

    def test_a_chapter_without_a_chain_is_accepted(self, db_session, factories):
        assert factories.PositionItemFactory.create(is_chapter=True).section_chain_id is None

    def test_a_chain_with_a_position_cannot_be_deleted(self, db_session, factories):
        chain = _section_chain(db_session)
        item = factories.PositionItemFactory.create()
        item.section_chain_id = chain.id
        db_session.flush()
        with rejected_by(db_session, "fk_position_items_section_chain"):
            db_session.execute(sa.text("DELETE FROM section_chains WHERE id = :c"), {"c": chain.id})

    def test_a_chain_without_positions_is_deleted(self, db_session):
        chain = _section_chain(db_session)
        db_session.execute(sa.text("DELETE FROM section_chains WHERE id = :c"), {"c": chain.id})
        assert _count(db_session, "section_chains") == 0


# ---------------------------------------------------------------------------
#  4. family_parameters.releasable и releasable_proposals
# ---------------------------------------------------------------------------

class TestReleasable:
    def _parameter(self, db_session, factories, **overrides):
        schema = _schema(db_session, factories)
        parameter = _param(db_session, schema)
        for name, value in overrides.items():
            setattr(parameter, name, value)
        return parameter

    @pytest.mark.parametrize("releasable", [True, False])
    def test_a_main_parameter_takes_either_answer(self, db_session, factories, releasable):
        parameter = self._parameter(db_session, factories, releasable=releasable)
        db_session.flush()
        assert parameter.releasable is releasable

    def test_an_undecided_secondary_parameter_is_accepted(self, db_session, factories):
        parameter = self._parameter(db_session, factories, role="secondary")
        db_session.flush()
        assert parameter.releasable is None

    @pytest.mark.parametrize("releasable", [True, False])
    def test_a_secondary_parameter_with_any_answer_is_rejected(
        self, db_session, factories, releasable
    ):
        parameter = self._parameter(db_session, factories)
        with rejected_by(db_session, "ck_family_parameters_releasable_main"):
            parameter.role = "secondary"
            parameter.releasable = releasable

    @pytest.mark.parametrize(
        "confidence", [Decimal("1.01"), Decimal("-0.01"), Decimal("NaN")],
        ids=["above_one", "below_zero", "nan"],
    )
    def test_a_confidence_outside_zero_one_is_rejected(self, db_session, factories, confidence):
        parameter = self._parameter(db_session, factories)
        with rejected_by(db_session, "ck_releasable_proposals_confidence"):
            _proposal(db_session, parameter, confidence=confidence)

    @pytest.mark.parametrize("confidence", [Decimal("0"), Decimal("1"), Decimal("0.95")])
    def test_a_confidence_inside_zero_one_is_accepted(self, db_session, factories, confidence):
        parameter = self._parameter(db_session, factories)
        assert _proposal(db_session, parameter, confidence=confidence).confidence == confidence

    @pytest.mark.parametrize("column", ["releasable", "confidence", "reason", "request_hash"])
    def test_every_proposal_column_is_required(self, db_session, factories, column):
        parameter = self._parameter(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _proposal(db_session, parameter, **{column: None})
        assert exc.value.orig.diag.column_name == column

    def test_one_proposal_per_parameter(self, db_session, factories):
        parameter = self._parameter(db_session, factories)
        _proposal(db_session, parameter)
        with rejected_by(db_session, "pk_releasable_proposals"):
            _proposal(db_session, parameter)

    def test_deleting_the_parameter_removes_its_proposal_only(self, db_session, factories):
        schema = _schema(db_session, factories)
        first, second = _param(db_session, schema, 1), _param(db_session, schema, 2)
        _proposal(db_session, first)
        _proposal(db_session, second)
        db_session.execute(sa.text("DELETE FROM family_parameters WHERE id = :p"), {"p": first.id})
        left = db_session.execute(sa.text("SELECT parameter_id FROM releasable_proposals")).scalars().all()
        assert left == [second.id]


# ---------------------------------------------------------------------------
#  5. kp_line_targets
# ---------------------------------------------------------------------------

class TestKpLineTarget:
    def test_the_freshest_target_without_an_analog_is_accepted(self, db_session, factories):
        assert _target(db_session, factories).kind == "freshest"

    def test_the_analog_target_with_an_analog_is_accepted(self, db_session, factories):
        row = _target(db_session, factories, kind="analog", analog_position_item_id=7)
        assert row.analog_position_item_id == 7

    def test_the_analog_target_without_an_analog_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_analog"):
            _target(db_session, factories, kind="analog", analog_position_item_id=None)

    def test_the_freshest_target_with_an_analog_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_kp_line_targets_analog"):
            _target(db_session, factories, kind="freshest", analog_position_item_id=7)

    @pytest.mark.parametrize("bad", ["Freshest", "", "oldest"])
    def test_an_unlisted_kind_is_rejected(self, db_session, factories, bad):
        with rejected_by(db_session, "ck_kp_line_targets_kind"):
            _target(db_session, factories, kind=bad)

    def test_the_analog_has_no_foreign_key(self, db_session, factories):
        """Строка договора может уйти с заменой сметы: выбор тогда не действует, но
        строка выбора остаётся."""
        row = _target(db_session, factories, kind="analog", analog_position_item_id=987654321)
        assert row.analog_position_item_id == 987654321

    def test_one_target_per_line(self, db_session, factories):
        first = _target(db_session, factories)
        with rejected_by(db_session, "pk_kp_line_targets"):
            db_session.add(
                KpLineTarget(
                    position_item_id=first.position_item_id, kind="freshest",
                    set_by=_user_id(factories),
                )
            )

    def test_set_at_is_filled_by_the_server(self, db_session, factories):
        row = _target(db_session, factories)
        db_session.refresh(row)
        assert row.set_at is not None and row.set_at.tzinfo is not None

    def test_deleting_the_line_removes_its_target_only(self, db_session, factories):
        item = factories.PositionItemFactory.create()
        _target(db_session, factories, item)
        other = _target(db_session, factories)
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :i"), {"i": item.id})
        left = db_session.execute(sa.text("SELECT position_item_id FROM kp_line_targets")).scalars().all()
        assert left == [other.position_item_id]

    def test_a_user_with_a_target_cannot_be_deleted(self, db_session, factories):
        row = _target(db_session, factories)
        with rejected_by(db_session, "fk_kp_line_targets_set_by"):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": row.set_by})


# ---------------------------------------------------------------------------
#  6. semantic_jobs: два новых вида и предмет-цепочка
# ---------------------------------------------------------------------------

class TestSemanticJobKinds:
    def _section_job(self, db_session, factories, chain=None, **overrides):
        chain = chain or _section_chain(db_session)
        values = dict(kind="section_zone", context_id=None, section_chain_id=chain.id)
        values.update(overrides)
        return _job(db_session, factories, **values)

    def test_a_section_zone_job_on_a_chain_is_accepted(self, db_session, factories):
        job = self._section_job(db_session, factories)
        assert job.kind == "section_zone"

    def test_a_section_zone_job_with_a_family_is_rejected(self, db_session, factories):
        family = _family(db_session)
        with rejected_by(db_session, "ck_semantic_jobs_schema_subject"):
            self._section_job(db_session, factories, family_id=family.id)

    def test_a_section_zone_job_without_a_chain_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_section_chain_subject"):
            _job(db_session, factories, kind="section_zone", context_id=None)

    def test_a_family_suggestion_job_with_a_chain_is_rejected(self, db_session, factories):
        chain = _section_chain(db_session)
        with rejected_by(db_session, "ck_semantic_jobs_section_chain_subject"):
            _job(db_session, factories, section_chain_id=chain.id)

    def test_a_releasable_params_job_on_a_family_schema_is_accepted(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        job = _job(
            db_session, factories, kind="releasable_params", context_id=None,
            family_id=family.id, schema_id=schema.id,
        )
        assert job.kind == "releasable_params"

    def test_a_releasable_params_job_without_a_schema_is_rejected(self, db_session, factories):
        family = _family(db_session)
        with rejected_by(db_session, "ck_semantic_jobs_schema_id_by_kind"):
            _job(
                db_session, factories, kind="releasable_params", context_id=None,
                family_id=family.id,
            )

    def test_the_job_key_includes_the_chain(self, db_session, factories):
        first, second = _section_chain(db_session), _section_chain(db_session)
        self._section_job(db_session, factories, first, request_hash="same")
        self._section_job(db_session, factories, second, request_hash="same")
        with rejected_by(db_session, "uq_semantic_jobs_subject_request_hash"):
            self._section_job(db_session, factories, first, request_hash="same")

    def test_a_chain_with_a_job_cannot_be_deleted(self, db_session, factories):
        chain = _section_chain(db_session)
        self._section_job(db_session, factories, chain)
        with rejected_by(db_session, "fk_semantic_jobs_section_chain"):
            db_session.execute(sa.text("DELETE FROM section_chains WHERE id = :c"), {"c": chain.id})


# ---------------------------------------------------------------------------
#  7. Паритет модели и миграции
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    _CK_CASES = [
        (CK_SECTION_CHAIN_ZONE, "CK_SECTION_CHAIN_ZONE",
         "zone IN ('underground', 'aboveground', 'territory', 'whole')"),
        (CK_SECTION_CHAIN_ZONE_SOURCE, "CK_SECTION_CHAIN_ZONE_SOURCE",
         "zone_source IN ('rule', 'model', 'human')"),
        (CK_SECTION_CHAIN_ZONE_PAIR, "CK_SECTION_CHAIN_ZONE_PAIR",
         "(zone IS NULL) = (zone_source IS NULL)"),
        (CK_SECTION_CHAIN_PROPOSED_ZONE, "CK_SECTION_CHAIN_PROPOSED_ZONE",
         "proposed_zone IN ('underground', 'aboveground', 'territory', 'whole', 'unknown')"),
        (CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED, "CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED",
         "zone IS NULL OR (proposed_zone IS NULL AND proposed_confidence IS NULL "
         "AND proposed_reason IS NULL)"),
        (CK_SECTION_CHAIN_HUMAN_DECIDED_BY, "CK_SECTION_CHAIN_HUMAN_DECIDED_BY",
         "(zone_source IS NOT DISTINCT FROM 'human') = (decided_by IS NOT NULL)"),
        (CK_POSITION_ITEM_CHAIN_NOT_CHAPTER, "CK_POSITION_ITEM_CHAIN_NOT_CHAPTER",
         "NOT is_chapter OR section_chain_id IS NULL"),
        (CK_FAMILY_PARAMETER_RELEASABLE_MAIN, "CK_FAMILY_PARAMETER_RELEASABLE_MAIN",
         "releasable IS NULL OR role = 'main'"),
        (CK_RELEASABLE_CONFIDENCE, "CK_RELEASABLE_CONFIDENCE", "confidence BETWEEN 0 AND 1"),
        (CK_KP_TARGET_KIND, "CK_KP_TARGET_KIND", "kind IN ('freshest', 'analog')"),
        (CK_KP_TARGET_ANALOG, "CK_KP_TARGET_ANALOG",
         "(kind = 'analog') = (analog_position_item_id IS NOT NULL)"),
        (SEMANTIC_JOB_KINDS, "SEMANTIC_JOB_KINDS",
         "'family_suggestion', 'family_schema', 'context_values', 'family_discovery', "
         "'section_zone', 'releasable_params'"),
        (CK_SEMANTIC_JOBS_SCHEMA_SUBJECT, "CK_SEMANTIC_JOBS_SCHEMA_SUBJECT",
         "(kind IN ('family_schema', 'releasable_params')) = "
         "(context_id IS NULL AND family_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND, "CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND",
         "(kind IN ('family_schema', 'context_values', 'releasable_params')) = "
         "(schema_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_SECTION_CHAIN_SUBJECT, "CK_SEMANTIC_JOBS_SECTION_CHAIN_SUBJECT",
         "(kind = 'section_zone') = (section_chain_id IS NOT NULL)"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0024(), name)
        assert model_expr == independent

    def test_the_closed_sets_match_the_check_literals(self):
        zones = ", ".join(f"'{v}'" for v in ZONES)
        sources = ", ".join(f"'{v}'" for v in ZONE_SOURCES)
        kinds = ", ".join(f"'{v}'" for v in KP_TARGET_KINDS)
        assert f"zone IN ({zones})" == CK_SECTION_CHAIN_ZONE
        assert f"zone_source IN ({sources})" == CK_SECTION_CHAIN_ZONE_SOURCE
        assert f"kind IN ({kinds})" == CK_KP_TARGET_KIND

    _MODEL_CHECKS = {
        "section_chains": {
            "ck_section_chains_zone": "CK_SECTION_CHAIN_ZONE",
            "ck_section_chains_zone_source": "CK_SECTION_CHAIN_ZONE_SOURCE",
            "ck_section_chains_zone_pair": "CK_SECTION_CHAIN_ZONE_PAIR",
            "ck_section_chains_proposed_zone": "CK_SECTION_CHAIN_PROPOSED_ZONE",
            "ck_section_chains_proposal_only_undecided": "CK_SECTION_CHAIN_PROPOSAL_ONLY_UNDECIDED",
            "ck_section_chains_human_decided_by": "CK_SECTION_CHAIN_HUMAN_DECIDED_BY",
        },
        "releasable_proposals": {"ck_releasable_proposals_confidence": "CK_RELEASABLE_CONFIDENCE"},
        "kp_line_targets": {
            "ck_kp_line_targets_kind": "CK_KP_TARGET_KIND",
            "ck_kp_line_targets_analog": "CK_KP_TARGET_ANALOG",
        },
    }

    @pytest.mark.parametrize("table", list(_MODEL_CHECKS))
    def test_the_model_declares_the_migration_checks(self, table):
        """`alembic check` CHECK-выражения не сравнивает: что модель объявляет те же
        ограничения с теми же текстами, что миграция, держит только этот тест."""
        from models import Base

        declared = {
            c.name: str(c.sqltext)
            for c in Base.metadata.tables[table].constraints
            if isinstance(c, sa.CheckConstraint)
        }
        migration = _migration_0024()
        expected = {
            name: getattr(migration, constant)
            for name, constant in self._MODEL_CHECKS[table].items()
        }
        assert declared == expected

    def test_the_foreign_key_actions(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f' AND conname IN "
                "('fk_section_chains_decided_by', 'fk_position_items_section_chain', "
                "'fk_releasable_proposals_parameter', 'fk_kp_line_targets_position_item', "
                "'fk_kp_line_targets_set_by', 'fk_semantic_jobs_section_chain')"
            )
        ).all()
        # 'c' = CASCADE, 'r' = RESTRICT
        assert dict(rows) == {
            "fk_section_chains_decided_by": "r",
            "fk_position_items_section_chain": "r",
            "fk_releasable_proposals_parameter": "c",
            "fk_kp_line_targets_position_item": "c",
            "fk_kp_line_targets_set_by": "r",
            "fk_semantic_jobs_section_chain": "r",
        }


# ---------------------------------------------------------------------------
#  8. Миграция: К1 при upgrade, отказы и пустой откат при downgrade
# ---------------------------------------------------------------------------

def _table_names(conn) -> set:
    return {
        row[0]
        for row in conn.execute(
            sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
    }


def _constraint_names(conn) -> set:
    return {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint c JOIN pg_namespace n "
                "ON n.oid = c.connamespace WHERE n.nspname = 'public'"
            )
        )
    }


def _column_names(conn, table: str) -> set:
    return {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
    }


def _revision(conn) -> str:
    return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


class TestDowngradeRefusalLogic:
    """`_downgrade_refusal` — чистая логика на вручную построенных словарях: каждый
    счётчик единственный положительный, значение (5) отличительное."""

    _NAMES = ("kp_line_targets", "section_chains_human", "family_parameters_releasable")

    @pytest.mark.parametrize("name", _NAMES)
    def test_each_counter_alone_blocks_and_is_named(self, name):
        blockers = {n: 0 for n in self._NAMES}
        blockers[name] = 5
        refusal = _migration_0024()._downgrade_refusal(blockers)
        assert refusal is not None
        assert "0024" in refusal
        assert f"{name} — 5" in refusal
        for other in self._NAMES:
            if other != name:
                assert f"{other} — 5" not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0024()._downgrade_refusal({n: 0 for n in self._NAMES}) is None


class TestDowngradeBlockersLive:
    """Каждый вид решения — свой вход на живой схеме: считается ровно его счётчик."""

    _ZERO = {"kp_line_targets": 0, "section_chains_human": 0, "family_parameters_releasable": 0}

    def _blockers(self, db_session) -> dict:
        return _migration_0024()._downgrade_blockers(db_session.connection())

    def test_empty_tables_do_not_block(self, db_session):
        assert self._blockers(db_session) == self._ZERO

    def test_a_target_row_raises_exactly_its_counter(self, db_session, factories):
        _target(db_session, factories)
        assert self._blockers(db_session) == {**self._ZERO, "kp_line_targets": 1}

    def test_a_human_chain_raises_exactly_its_counter(self, db_session, factories):
        _section_chain(
            db_session, zone="whole", zone_source="human", decided_by=_user_id(factories)
        )
        assert self._blockers(db_session) == {**self._ZERO, "section_chains_human": 1}

    def test_a_chain_of_the_rule_or_the_model_does_not_block(self, db_session):
        _section_chain(db_session, zone="whole", zone_source="rule")
        _section_chain(db_session, zone="territory", zone_source="model")
        assert self._blockers(db_session) == self._ZERO

    def test_a_human_releasable_answer_raises_exactly_its_counter(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        parameter.releasable = True
        parameter.origin = "human"
        db_session.flush()
        assert self._blockers(db_session) == {**self._ZERO, "family_parameters_releasable": 1}

    def test_a_releasable_answer_of_the_model_does_not_block(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        parameter.releasable = True
        db_session.flush()
        assert self._blockers(db_session) == self._ZERO

    def test_a_human_parameter_without_an_answer_does_not_block(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        parameter.origin = "human"
        db_session.flush()
        assert self._blockers(db_session) == self._ZERO


@pytest.mark.migration_roundtrip
class TestUpgradeRefusesManualRungs:
    def test_a_manual_work_rung_stops_upgrade_and_changes_nothing(self):
        with _scratch_alembic("0024 upgrade refusal") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0023")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    conn.execute(
                        sa.text(
                            "INSERT INTO kp_line_rungs (position_item_id, level, scope, key, set_by) "
                            "VALUES (1, 'v', 'cls', CAST('{}' AS jsonb), 1)"
                        )
                    )
                with pytest.raises(RuntimeError) as exc:
                    command.upgrade(cfg, "0024")
                assert (
                    "есть ручные ступени работ: 1 — снимите их на экране 4б до обновления"
                    in str(exc.value)
                )
                with engine.connect() as conn:
                    assert _revision(conn) == "0023"
                    assert "kp_line_rungs" in _table_names(conn)
                    assert not set(_NEW_TABLES) & _table_names(conn)
                    rows = conn.execute(sa.text("SELECT count(*) FROM kp_line_rungs")).scalar_one()
                assert rows == 1
            finally:
                engine.dispose()

    def test_an_empty_manual_rung_table_does_not_stop_upgrade(self):
        with _scratch_alembic("0024 upgrade on empty") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0024")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    assert _revision(conn) == "0024"
                    assert "kp_line_rungs" not in _table_names(conn)
            finally:
                engine.dispose()


@pytest.mark.migration_roundtrip
class TestDowngradeOnEmptyDatabase:
    def test_downgrade_removes_the_new_and_restores_the_empty_rung_table(self):
        with _scratch_alembic("0024 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0023")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before_tables = _table_names(conn)
                    before_constraints = _constraint_names(conn)
                    before_columns = {
                        t: _column_names(conn, t)
                        for t in ("position_items", "family_parameters", "semantic_jobs")
                    }
                    rung_defs = conn.execute(
                        sa.text(
                            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = CAST('kp_line_rungs' AS regclass) ORDER BY conname"
                        )
                    ).all()
                command.upgrade(cfg, "0024")
                with engine.connect() as conn:
                    upgraded = _table_names(conn)
                assert set(_NEW_TABLES) <= upgraded
                assert "kp_line_rungs" not in upgraded

                command.downgrade(cfg, "0023")
                with engine.connect() as conn:
                    assert _revision(conn) == "0023"
                    assert _table_names(conn) == before_tables
                    assert _constraint_names(conn) == before_constraints
                    for table, columns in before_columns.items():
                        assert _column_names(conn, table) == columns
                    assert conn.execute(sa.text("SELECT count(*) FROM kp_line_rungs")).scalar_one() == 0
                    restored = conn.execute(
                        sa.text(
                            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = CAST('kp_line_rungs' AS regclass) ORDER BY conname"
                        )
                    ).all()
                    old_index = conn.execute(
                        sa.text(
                            "SELECT indexdef FROM pg_indexes "
                            "WHERE indexname = 'uq_semantic_jobs_subject_request_hash'"
                        )
                    ).scalar_one()
                assert restored == rung_defs
                assert "section_chain_id" not in old_index

                command.upgrade(cfg, "0024")
                with engine.connect() as conn:
                    assert _table_names(conn) == upgraded
            finally:
                engine.dispose()


#: Решение человека — по одному на каждый вид. Внешние ключи при вставке отключены
#: (`session_replication_role = replica`): тест проверяет отказ отката, а не цепочку
#: родительских строк.
_SEED_ONE_DECISION = {
    "kp_line_targets": (
        "INSERT INTO kp_line_targets (position_item_id, kind, set_by) VALUES (1, 'freshest', 1)"
    ),
    "section_chains_human": (
        "INSERT INTO section_chains (chain_key, titles, zone, zone_source, decided_by) "
        "VALUES ('k', ARRAY['t'], 'whole', 'human', 1)"
    ),
    "family_parameters_releasable": (
        "UPDATE family_parameters SET releasable = true, origin = 'human'"
    ),
}


@pytest.mark.migration_roundtrip
class TestDowngradeRefusedByEachDecision:
    @pytest.mark.parametrize("name", list(_SEED_ONE_DECISION))
    def test_downgrade_is_refused_and_leaves_the_schema_untouched(self, name):
        with _scratch_alembic(f"0024 downgrade refusal {name}") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0024")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    if name == "family_parameters_releasable":
                        conn.execute(
                            sa.text(
                                "INSERT INTO family_parameters "
                                "(schema_id, ordinal, name, name_norm) VALUES (1, 1, 'p', 'p')"
                            )
                        )
                    conn.execute(sa.text(_SEED_ONE_DECISION[name]))

                with pytest.raises(RuntimeError, match="0024") as exc:
                    command.downgrade(cfg, "0023")

                assert f"{name} — 1" in str(exc.value)
                for other in _SEED_ONE_DECISION:
                    if other != name:
                        assert f"{other} — 1" not in str(exc.value)
                with engine.connect() as conn:
                    assert _revision(conn) == "0024"
                    assert set(_NEW_TABLES) <= _table_names(conn)
            finally:
                engine.dispose()

    def test_a_releasable_answer_of_the_model_does_not_stop_downgrade(self):
        """Ответ модели (`origin = 'model'`) — не решение человека: откат его не
        сохраняет, но и не отказывает."""
        with _scratch_alembic("0024 downgrade model answer") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0024")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    conn.execute(
                        sa.text(
                            "INSERT INTO family_parameters "
                            "(schema_id, ordinal, name, name_norm, releasable) "
                            "VALUES (1, 1, 'p', 'p', true)"
                        )
                    )
                command.downgrade(cfg, "0023")
                with engine.connect() as conn:
                    assert _revision(conn) == "0023"
            finally:
                engine.dispose()


# ---------------------------------------------------------------------------
#  9. Три таблицы — явно в _DOMAIN_TABLES, очистка их чистит
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_the_three_tables_are_listed_explicitly_and_the_old_one_is_not(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in _NEW_TABLES:
            assert table in _DOMAIN_TABLES
        assert "kp_line_rungs" not in _DOMAIN_TABLES

    def test_truncate_clears_the_three_tables(
        self, db_engine, committing_db, committing_factories
    ):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        _section_chain(session)
        _target(session, committing_factories)
        schema = _schema(session, committing_factories)
        _proposal(session, _param(session, schema))
        session.commit()

        def counts():
            with db_engine.connect() as conn:
                return {table: _count(conn, table) for table in _NEW_TABLES}

        assert counts() == {table: 1 for table in _NEW_TABLES}
        session.close()
        _truncate_domain_tables(db_engine)
        assert counts() == {table: 0 for table in _NEW_TABLES}

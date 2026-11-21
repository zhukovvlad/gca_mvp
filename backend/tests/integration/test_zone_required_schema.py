"""Схема флажка семьи «зона обязательна» (миграция 0028; спека зоны как предпочтения §4 п. 1).

Каждый тест нарушения ловит `IntegrityError` и сверяет `diag.constraint_name` с ожидаемым
именем. `upgrade` и `downgrade` — на отдельной scratch-базе (`@pytest.mark.migration_roundtrip`),
стартовый список проверяется на строках, вставленных SQL между 0027 и 0028.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import CK_FAMILY_ZONE_REQUIRED_AUTHOR
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_work_variants_schema import _family, _scratch_alembic

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Независимый перечень спеки §4 п. 1, а не `ZONE_REQUIRED_SEED` из миграции.
SPEC_SEED = (
    "двери",
    "облицовка керамогранитом",
    "покраска стен",
    "вывоз и утилизация грунта",
    "светильники",
    "монтаж воздуховодов вентиляции",
    "изоляция воздуховодов",
)


def _migration_0028():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0028-family_zone_required.py"))
    spec = importlib.util.spec_from_file_location("_migration_0028_zone_required_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
#  CHECK «автор без времени не бывает»
# ---------------------------------------------------------------------------

class TestAuthorCheck:
    def test_author_without_time_is_rejected_by_name(self, db_session, factories):
        author = factories.UserFactory.create().id
        with rejected_by(db_session, "ck_work_families_zone_required_author"):
            _family(
                db_session, zone_required=True, zone_required_changed_by=author,
                zone_required_changed_at=None,
            )

    def test_time_without_author_is_allowed(self, db_session):
        family = _family(
            db_session, zone_required=True,
            zone_required_changed_at=datetime.now(UTC),
        )
        assert family.zone_required_changed_by is None

    def test_author_with_time_is_allowed(self, db_session, factories):
        author = factories.UserFactory.create().id
        family = _family(
            db_session, zone_required=True, zone_required_changed_by=author,
            zone_required_changed_at=datetime.now(UTC),
        )
        assert family.zone_required_changed_by == author

    def test_default_is_false_without_author_and_time(self, db_session):
        family = _family(db_session)
        db_session.refresh(family)
        assert (family.zone_required, family.zone_required_changed_by,
                family.zone_required_changed_at) == (False, None, None)


class TestParityWithMigration:
    def test_model_expression_equals_migration_and_independent_literal(self):
        assert CK_FAMILY_ZONE_REQUIRED_AUTHOR == _migration_0028().CK_FAMILY_ZONE_REQUIRED_AUTHOR
        assert CK_FAMILY_ZONE_REQUIRED_AUTHOR == (
            "zone_required_changed_by IS NULL OR zone_required_changed_at IS NOT NULL"
        )

    def test_constraint_name_matches_the_model(self):
        from models import WorkFamily

        names = {c.name for c in WorkFamily.__table__.constraints}
        assert "ck_work_families_zone_required_author" in names
        assert _migration_0028().CK_NAME == "ck_work_families_zone_required_author"

    def test_seed_list_is_the_seven_titles_of_the_spec(self):
        assert _migration_0028().ZONE_REQUIRED_SEED == SPEC_SEED


# ---------------------------------------------------------------------------
#  Миграция на scratch-базе
# ---------------------------------------------------------------------------

_INSERT = (
    "INSERT INTO work_families (title, unit_id, definition, status, seed_key) "
    "VALUES (:title, (SELECT id FROM units_of_measure WHERE code = :unit), "
    "'определение', :status, :key) RETURNING id"
)


def _insert_family(conn, title, *, unit="M2", status="active", key):
    return conn.execute(
        sa.text(_INSERT), {"title": title, "unit": unit, "status": status, "key": key}
    ).scalar_one()


def _flags(conn) -> dict:
    return {
        row.id: (row.zone_required, row.zone_required_changed_at is not None,
                 row.zone_required_changed_by)
        for row in conn.execute(
            sa.text(
                "SELECT id, zone_required, zone_required_changed_at, zone_required_changed_by "
                "FROM work_families"
            )
        )
    }


@pytest.mark.migration_roundtrip
class TestMigrationSeed:
    def test_seed_list_flags_only_listed_non_archived_families(self, capsys):
        with _scratch_alembic("0028 seed") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0027")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    ids = {
                        "draft_upper": _insert_family(conn, "  ДВЕРИ  ", status="draft", key="k1"),
                        "active_mixed": _insert_family(
                            conn, "Облицовка Керамогранитом", key="k2"),
                        "archived": _insert_family(
                            conn, "покраска стен", status="archived", key="k3"),
                        "same_title_unit_a": _insert_family(conn, "светильники", key="k4"),
                        "same_title_unit_b": _insert_family(
                            conn, "светильники", unit="SHT", key="k5"),
                        "other": _insert_family(conn, "Устройство стяжки", key="k6"),
                    }
                capsys.readouterr()
                command.upgrade(cfg, "0028")
                out = capsys.readouterr().out
                with engine.connect() as conn:
                    flags = _flags(conn)
            finally:
                engine.dispose()
        # (zone_required, changed_at задан, changed_by)
        assert flags[ids["draft_upper"]] == (True, True, None)
        assert flags[ids["active_mixed"]] == (True, True, None)
        assert flags[ids["archived"]] == (False, False, None)
        assert flags[ids["same_title_unit_a"]] == (True, True, None)
        assert flags[ids["same_title_unit_b"]] == (True, True, None)
        assert flags[ids["other"]] == (False, False, None)

        # Ненайденные названия печатаются в вывод; найденные — нет. «покраска стен» есть
        # только в архиве — для списка она не найдена.
        assert "не найдены" in out
        tail = out.split("не найдены", 1)[1]
        for missing in ("покраска стен", "вывоз и утилизация грунта",
                        "монтаж воздуховодов вентиляции", "изоляция воздуховодов"):
            assert missing in tail
        for found in ("двери", "облицовка керамогранитом", "светильники"):
            assert found not in tail

    def test_database_without_listed_families_passes(self, capsys):
        with _scratch_alembic("0028 empty seed") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0027")
            capsys.readouterr()
            command.upgrade(cfg, "0028")
            out = capsys.readouterr().out
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    assert _flags(conn) == {}
            finally:
                engine.dispose()
        for title in SPEC_SEED:
            assert title in out


@pytest.mark.migration_roundtrip
class TestMigrationDowngrade:
    def test_downgrade_removes_three_columns_and_the_constraint(self):
        with _scratch_alembic("0028 downgrade") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0027")
            engine = sa.create_engine(scratch_url)
            try:
                def shape():
                    with engine.connect() as conn:
                        columns = {
                            row[0]
                            for row in conn.execute(sa.text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = 'work_families'"))
                        }
                        constraints = {
                            row[0]
                            for row in conn.execute(sa.text(
                                "SELECT conname FROM pg_constraint "
                                "WHERE conrelid = 'work_families'::regclass"))
                        }
                    return columns, constraints

                before = shape()
                command.upgrade(cfg, "0028")
                columns, constraints = shape()
                assert {"zone_required", "zone_required_changed_by",
                        "zone_required_changed_at"} <= columns
                assert "ck_work_families_zone_required_author" in constraints

                command.downgrade(cfg, "0027")
                assert shape() == before
            finally:
                engine.dispose()

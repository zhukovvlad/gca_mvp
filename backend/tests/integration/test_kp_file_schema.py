"""Схема хранимых решений по строкам КП и отправленных файлов (миграция 0023;
спека 4б §2.13).

Пять таблиц: `kp_line_agreements`, `kp_line_comments`, `kp_line_rungs`,
`kp_family_rungs`, `kp_sent_files`. Каждый тест нарушения ловит `IntegrityError`
и сверяет `diag.constraint_name` с ожидаемым именем: нарушение другого
ограничения не засчитывается.

CHECK в PostgreSQL отвергает только `FALSE`: `NULL` проходит. Поэтому предикаты
здесь тотальны, а входы строятся по одному на каждое заявление проверки.

`upgrade` и `downgrade` на пустой и непустой схеме — на отдельной scratch-базе
(`@pytest.mark.migration_roundtrip`).
"""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_KP_AGREEMENT_NET_PRICE,
    CK_KP_COMMENT_LENGTH,
    CK_KP_FAMILY_RUNG_PARAMETER,
    CK_KP_RUNG_LEVEL,
    CK_KP_RUNG_SCOPE,
    KP_COMMENT_MAX,
    KP_RUNG_LEVELS,
    KP_RUNG_SCOPES,
    KpFamilyRung,
    KpLineAgreement,
    KpLineComment,
    KpLineRung,
    KpSentFile,
)
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_work_variants_schema import _family, _scratch_alembic, _uid

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_TABLES = (
    "kp_line_agreements",
    "kp_line_comments",
    "kp_line_rungs",
    "kp_family_rungs",
    "kp_sent_files",
)


def _migration_0023():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0023-kp_decisions.py"))
    spec = importlib.util.spec_from_file_location("_migration_0023_kp_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _world(session, factories) -> dict:
    """Строка КП с её сметой, пользователь, оффер и семья — всё, на что ссылаются
    пять таблиц."""
    item = factories.PositionItemFactory.create()
    return {
        "item": item,
        "estimate_id": item.proposal.lot.estimate_id,
        "user_id": factories.UserFactory.create().id,
        "offer": factories.OfferFactory.create(),
        "family": _family(session),
    }


def _agreement(session, world, **overrides) -> KpLineAgreement:
    values = dict(
        position_item_id=world["item"].id, net_price=Decimal("100.5"), set_by=world["user_id"]
    )
    values.update(overrides)
    row = KpLineAgreement(**values)
    session.add(row)
    session.flush()
    return row


def _comment(session, world, **overrides) -> KpLineComment:
    values = dict(position_item_id=world["item"].id, text="Комментарий", set_by=world["user_id"])
    values.update(overrides)
    row = KpLineComment(**values)
    session.add(row)
    session.flush()
    return row


def _line_rung(session, world, **overrides) -> KpLineRung:
    values = dict(
        position_item_id=world["item"].id, level="v", scope="cls", key={"variant_id": 7},
        set_by=world["user_id"],
    )
    values.update(overrides)
    row = KpLineRung(**values)
    session.add(row)
    session.flush()
    return row


def _family_rung(session, world, **overrides) -> KpFamilyRung:
    values = dict(
        estimate_id=world["estimate_id"], work_family_id=world["family"].id, level="f",
        scope="all", first_main_parameter_id=None, set_by=world["user_id"],
    )
    values.update(overrides)
    row = KpFamilyRung(**values)
    session.add(row)
    session.flush()
    return row


def _sent_file(session, world, **overrides) -> KpSentFile:
    values = dict(
        offer_id=world["offer"].id, file_key=f"key-{_uid()}", filename="file.xlsx",
        file_sha256="a" * 64, size_bytes=12345, vat_rate=Decimal("0.12"), level_label="Работа",
        created_by=world["user_id"],
    )
    values.update(overrides)
    row = KpSentFile(**values)
    session.add(row)
    session.flush()
    return row


def _live_constraints(session, table: str) -> dict[str, str]:
    rows = session.execute(
        sa.text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass)"
        ),
        {"t": table},
    ).all()
    return dict(rows)


# ---------------------------------------------------------------------------
#  1. Таблицы и имена ограничений — против независимого перечня
# ---------------------------------------------------------------------------

_EXPECTED_CONSTRAINTS = {
    "kp_line_agreements": {
        "pk_kp_line_agreements", "fk_kp_line_agreements_position_item",
        "fk_kp_line_agreements_set_by", "ck_kp_line_agreements_net_price",
    },
    "kp_line_comments": {
        "pk_kp_line_comments", "fk_kp_line_comments_position_item",
        "fk_kp_line_comments_set_by", "ck_kp_line_comments_text_length",
    },
    "kp_line_rungs": {
        "pk_kp_line_rungs", "fk_kp_line_rungs_position_item", "fk_kp_line_rungs_set_by",
        "ck_kp_line_rungs_level", "ck_kp_line_rungs_scope",
    },
    "kp_family_rungs": {
        "pk_kp_family_rungs", "fk_kp_family_rungs_estimate", "fk_kp_family_rungs_work_family",
        "fk_kp_family_rungs_set_by", "ck_kp_family_rungs_level", "ck_kp_family_rungs_scope",
        "ck_kp_family_rungs_first_main_parameter",
    },
    "kp_sent_files": {
        "pk_kp_sent_files", "fk_kp_sent_files_offer", "fk_kp_sent_files_created_by",
        "uq_kp_sent_files_file_key",
    },
}


class TestTablesAndConstraintNames:
    def test_the_five_tables_exist(self, db_session):
        found = {
            row[0]
            for row in db_session.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name LIKE 'kp\\_%'"
                )
            )
        }
        assert found == set(_TABLES)

    @pytest.mark.parametrize("table", _TABLES)
    def test_constraints_carry_the_convention_names(self, db_session, table):
        assert set(_live_constraints(db_session, table)) == _EXPECTED_CONSTRAINTS[table]

    def test_only_the_parameter_snapshot_is_nullable(self, db_session):
        """Отсутствие решения — отсутствие строки: `NULL` допустим ровно в одном
        столбце из всех пяти таблиц."""
        nullable = {
            (table, column)
            for table, column in db_session.execute(
                sa.text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name LIKE 'kp\\_%' "
                    "AND is_nullable = 'YES'"
                )
            )
        }
        assert nullable == {("kp_family_rungs", "first_main_parameter_id")}

    def test_the_revision_chain(self):
        migration = _migration_0023()
        assert (migration.revision, migration.down_revision) == ("0023", "0022")


# ---------------------------------------------------------------------------
#  2. kp_line_agreements.net_price: конечно и больше нуля
# ---------------------------------------------------------------------------

class TestAgreementNetPrice:
    @pytest.mark.parametrize(
        "bad",
        [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")],
        ids=["zero", "negative", "nan", "infinity"],
    )
    def test_a_non_price_is_rejected(self, db_session, factories, bad):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_line_agreements_net_price"):
            db_session.add(
                KpLineAgreement(
                    position_item_id=world["item"].id, net_price=bad, set_by=world["user_id"]
                )
            )

    def test_the_smallest_positive_price_is_accepted(self, db_session, factories):
        world = _world(db_session, factories)
        row = _agreement(db_session, world, net_price=Decimal("0.0000001"))
        db_session.refresh(row)
        assert row.net_price == Decimal("0.0000001")

    def test_a_price_with_twenty_digits_is_kept_exactly(self, db_session, factories):
        world = _world(db_session, factories)
        exact = Decimal("12345678901234567890.123456")
        row = _agreement(db_session, world, net_price=exact)
        db_session.refresh(row)
        assert row.net_price == exact

    def test_a_null_price_is_rejected(self, db_session, factories):
        world = _world(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _agreement(db_session, world, net_price=None)
        assert exc.value.orig.diag.column_name == "net_price"

    def test_set_at_is_filled_by_the_server(self, db_session, factories):
        world = _world(db_session, factories)
        row = _agreement(db_session, world)
        db_session.refresh(row)
        assert row.set_at is not None and row.set_at.tzinfo is not None

    def test_one_agreement_per_line(self, db_session, factories):
        world = _world(db_session, factories)
        _agreement(db_session, world)
        with rejected_by(db_session, "pk_kp_line_agreements"):
            db_session.add(
                KpLineAgreement(
                    position_item_id=world["item"].id, net_price=Decimal("1"),
                    set_by=world["user_id"],
                )
            )

    def test_a_missing_line_is_rejected_by_the_foreign_key(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "fk_kp_line_agreements_position_item"):
            db_session.add(
                KpLineAgreement(
                    position_item_id=987654321, net_price=Decimal("1"), set_by=world["user_id"]
                )
            )


# ---------------------------------------------------------------------------
#  3. kp_line_comments.text: до 2000 знаков, пустая строка допустима
# ---------------------------------------------------------------------------

class TestCommentLength:
    def test_the_limit_constant(self):
        assert KP_COMMENT_MAX == 2000

    def test_an_empty_string_is_accepted(self, db_session, factories):
        world = _world(db_session, factories)
        row = _comment(db_session, world, text="")
        db_session.refresh(row)
        assert row.text == ""

    def test_exactly_two_thousand_characters_are_accepted(self, db_session, factories):
        world = _world(db_session, factories)
        row = _comment(db_session, world, text="я" * 2000)
        db_session.refresh(row)
        assert len(row.text) == 2000

    def test_two_thousand_and_one_characters_are_rejected(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_line_comments_text_length"):
            db_session.add(
                KpLineComment(
                    position_item_id=world["item"].id, text="я" * 2001, set_by=world["user_id"]
                )
            )

    def test_a_null_text_is_rejected(self, db_session, factories):
        world = _world(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _comment(db_session, world, text=None)
        assert exc.value.orig.diag.column_name == "text"


# ---------------------------------------------------------------------------
#  4. Ступени: level in (v, p, f), scope in (cls, all) — в обеих таблицах
# ---------------------------------------------------------------------------

class TestRungEnums:
    def test_the_constants(self):
        assert KP_RUNG_LEVELS == ("v", "p", "f")
        assert KP_RUNG_SCOPES == ("cls", "all")

    @pytest.mark.parametrize("level", ["v", "f"])
    def test_a_line_rung_accepts_the_listed_levels(self, db_session, factories, level):
        world = _world(db_session, factories)
        row = _line_rung(db_session, world, level=level)
        assert row.level == level

    def test_a_line_rung_accepts_the_parameter_level(self, db_session, factories):
        world = _world(db_session, factories)
        row = _line_rung(db_session, world, level="p")
        assert row.level == "p"

    @pytest.mark.parametrize("scope", ["cls", "all"])
    def test_a_line_rung_accepts_the_listed_scopes(self, db_session, factories, scope):
        world = _world(db_session, factories)
        assert _line_rung(db_session, world, scope=scope).scope == scope

    @pytest.mark.parametrize("bad", ["x", "V", "", "w"])
    def test_a_line_rung_rejects_an_unlisted_level(self, db_session, factories, bad):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_line_rungs_level"):
            _line_rung(db_session, world, level=bad)

    @pytest.mark.parametrize("bad", ["x", "ALL", "", "object"])
    def test_a_line_rung_rejects_an_unlisted_scope(self, db_session, factories, bad):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_line_rungs_scope"):
            _line_rung(db_session, world, scope=bad)

    @pytest.mark.parametrize("bad", ["x", "V", ""])
    def test_a_family_rung_rejects_an_unlisted_level(self, db_session, factories, bad):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_family_rungs_level"):
            _family_rung(db_session, world, level=bad)

    @pytest.mark.parametrize("bad", ["x", "ALL", ""])
    def test_a_family_rung_rejects_an_unlisted_scope(self, db_session, factories, bad):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_family_rungs_scope"):
            _family_rung(db_session, world, scope=bad)

    @pytest.mark.parametrize("scope", ["cls", "all"])
    def test_a_family_rung_accepts_the_listed_scopes(self, db_session, factories, scope):
        world = _world(db_session, factories)
        assert _family_rung(db_session, world, scope=scope).scope == scope

    def test_the_snapshot_key_round_trips_as_json(self, db_session, factories):
        world = _world(db_session, factories)
        snapshot = {"variant_id": 7, "values": [1, 2], "note": None}
        row = _line_rung(db_session, world, key=snapshot)
        db_session.expire(row)
        assert row.key == snapshot

    def test_a_null_snapshot_key_is_rejected(self, db_session, factories):
        world = _world(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            db_session.add(
                KpLineRung(
                    position_item_id=world["item"].id, level="v", scope="cls", key=sa.null(),
                    set_by=world["user_id"],
                )
            )
            db_session.flush()
        assert exc.value.orig.diag.column_name == "key"

    def test_one_rung_per_line(self, db_session, factories):
        world = _world(db_session, factories)
        _line_rung(db_session, world)
        with rejected_by(db_session, "pk_kp_line_rungs"):
            _line_rung(db_session, world, level="f")


# ---------------------------------------------------------------------------
#  5. kp_family_rungs: (level = 'p') = (first_main_parameter_id IS NOT NULL)
# ---------------------------------------------------------------------------

class TestFamilyRungParameterEquivalence:
    def test_the_parameter_level_with_a_parameter_is_accepted(self, db_session, factories):
        world = _world(db_session, factories)
        row = _family_rung(db_session, world, level="p", first_main_parameter_id=41)
        assert row.first_main_parameter_id == 41

    @pytest.mark.parametrize("level", ["v", "f"])
    def test_another_level_without_a_parameter_is_accepted(self, db_session, factories, level):
        world = _world(db_session, factories)
        assert _family_rung(db_session, world, level=level).level == level

    def test_the_parameter_level_without_a_parameter_is_rejected(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_family_rungs_first_main_parameter"):
            _family_rung(db_session, world, level="p", first_main_parameter_id=None)

    @pytest.mark.parametrize("level", ["f", "v"])
    def test_another_level_with_a_parameter_is_rejected(self, db_session, factories, level):
        world = _world(db_session, factories)
        with rejected_by(db_session, "ck_kp_family_rungs_first_main_parameter"):
            _family_rung(db_session, world, level=level, first_main_parameter_id=41)

    def test_the_parameter_snapshot_has_no_foreign_key(self, db_session, factories):
        world = _world(db_session, factories)
        row = _family_rung(db_session, world, level="p", first_main_parameter_id=987654321)
        assert row.first_main_parameter_id == 987654321

    def test_one_rung_per_estimate_and_family(self, db_session, factories):
        world = _world(db_session, factories)
        _family_rung(db_session, world)
        with rejected_by(db_session, "pk_kp_family_rungs"):
            _family_rung(db_session, world, level="v")

    def test_another_family_in_the_same_estimate_is_a_separate_row(self, db_session, factories):
        world = _world(db_session, factories)
        _family_rung(db_session, world)
        other = _family_rung(db_session, world, work_family_id=_family(db_session).id)
        assert other.work_family_id != world["family"].id

    def test_a_missing_estimate_is_rejected_by_the_foreign_key(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "fk_kp_family_rungs_estimate"):
            _family_rung(db_session, world, estimate_id=987654321)

    def test_a_missing_family_is_rejected_by_the_foreign_key(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "fk_kp_family_rungs_work_family"):
            _family_rung(db_session, world, work_family_id=987654321)


# ---------------------------------------------------------------------------
#  6. kp_sent_files
# ---------------------------------------------------------------------------

class TestSentFiles:
    def test_a_file_row_is_stored(self, db_session, factories):
        world = _world(db_session, factories)
        row = _sent_file(db_session, world, size_bytes=5_000_000_000, vat_rate=Decimal("0"))
        db_session.refresh(row)
        assert row.id is not None
        assert row.size_bytes == 5_000_000_000
        assert row.vat_rate == Decimal("0")
        assert row.created_at is not None and row.created_at.tzinfo is not None

    def test_the_file_key_is_unique(self, db_session, factories):
        world = _world(db_session, factories)
        _sent_file(db_session, world, file_key="same-key")
        with rejected_by(db_session, "uq_kp_sent_files_file_key"):
            _sent_file(db_session, world, file_key="same-key")

    def test_one_offer_keeps_many_files(self, db_session, factories):
        world = _world(db_session, factories)
        first = _sent_file(db_session, world)
        second = _sent_file(db_session, world)
        assert first.id != second.id

    def test_a_missing_offer_is_rejected_by_the_foreign_key(self, db_session, factories):
        world = _world(db_session, factories)
        with rejected_by(db_session, "fk_kp_sent_files_offer"):
            _sent_file(db_session, world, offer_id=987654321)

    @pytest.mark.parametrize(
        "column", ["filename", "file_sha256", "size_bytes", "vat_rate", "level_label"]
    )
    def test_every_column_is_required(self, db_session, factories, column):
        world = _world(db_session, factories)
        with pytest.raises(sa.exc.IntegrityError) as exc, db_session.begin_nested():
            _sent_file(db_session, world, **{column: None})
        assert exc.value.orig.diag.column_name == column


# ---------------------------------------------------------------------------
#  7. Каскады и RESTRICT
# ---------------------------------------------------------------------------

def _count(session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


class TestCascades:
    def test_deleting_the_line_removes_its_agreement_comment_and_rung(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        _agreement(db_session, world)
        _comment(db_session, world)
        _line_rung(db_session, world)
        db_session.execute(
            sa.text("DELETE FROM position_items WHERE id = :i"), {"i": world["item"].id}
        )
        assert [_count(db_session, t) for t in _TABLES[:3]] == [0, 0, 0]

    def test_deleting_another_line_leaves_the_rows_alone(self, db_session, factories):
        world = _world(db_session, factories)
        other = factories.PositionItemFactory.create()
        _agreement(db_session, world)
        _comment(db_session, world)
        _line_rung(db_session, world)
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :i"), {"i": other.id})
        assert [_count(db_session, t) for t in _TABLES[:3]] == [1, 1, 1]

    def test_deleting_the_estimate_removes_its_family_rungs_only(self, db_session, factories):
        world = _world(db_session, factories)
        other_estimate = factories.PositionItemFactory.create().proposal.lot.estimate_id
        _family_rung(db_session, world)
        _family_rung(db_session, world, estimate_id=other_estimate)
        db_session.execute(
            sa.text("DELETE FROM estimates WHERE id = :e"), {"e": world["estimate_id"]}
        )
        left = db_session.execute(sa.text("SELECT estimate_id FROM kp_family_rungs")).scalars().all()
        assert left == [other_estimate]

    def test_deleting_the_family_removes_its_rungs(self, db_session, factories):
        world = _world(db_session, factories)
        _family_rung(db_session, world)
        db_session.execute(
            sa.text("DELETE FROM work_families WHERE id = :f"), {"f": world["family"].id}
        )
        assert _count(db_session, "kp_family_rungs") == 0

    def test_deleting_the_offer_removes_its_sent_files_only(self, db_session, factories):
        world = _world(db_session, factories)
        other_offer = factories.OfferFactory.create()
        _sent_file(db_session, world)
        _sent_file(db_session, world, offer_id=other_offer.id)
        db_session.execute(
            sa.text("DELETE FROM offers WHERE id = :o"), {"o": world["offer"].id}
        )
        left = db_session.execute(sa.text("SELECT offer_id FROM kp_sent_files")).scalars().all()
        assert left == [other_offer.id]


class TestUserRestrict:
    _MAKERS = {
        "kp_line_agreements": (_agreement, "fk_kp_line_agreements_set_by"),
        "kp_line_comments": (_comment, "fk_kp_line_comments_set_by"),
        "kp_line_rungs": (_line_rung, "fk_kp_line_rungs_set_by"),
        "kp_family_rungs": (_family_rung, "fk_kp_family_rungs_set_by"),
        "kp_sent_files": (_sent_file, "fk_kp_sent_files_created_by"),
    }

    @pytest.mark.parametrize("table", _TABLES)
    def test_a_user_with_a_row_cannot_be_deleted(self, db_session, factories, table):
        world = _world(db_session, factories)
        maker, constraint = self._MAKERS[table]
        maker(db_session, world)
        with rejected_by(db_session, constraint):
            db_session.execute(
                sa.text("DELETE FROM users WHERE id = :u"), {"u": world["user_id"]}
            )

    def test_a_user_without_rows_is_deleted(self, db_session, factories):
        world = _world(db_session, factories)
        _agreement(db_session, world)
        stranger = factories.UserFactory.create().id
        db_session.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": stranger})
        assert db_session.execute(
            sa.text("SELECT count(*) FROM users WHERE id = :u"), {"u": stranger}
        ).scalar_one() == 0


# ---------------------------------------------------------------------------
#  8. Паритет модели и миграции
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    _CK_CASES = [
        (
            CK_KP_AGREEMENT_NET_PRICE, "CK_KP_AGREEMENT_NET_PRICE",
            "net_price > 0 AND net_price <> 'NaN' AND net_price <> 'Infinity'",
        ),
        (CK_KP_COMMENT_LENGTH, "CK_KP_COMMENT_LENGTH", "char_length(text) <= 2000"),
        (CK_KP_RUNG_LEVEL, "CK_KP_RUNG_LEVEL", "level IN ('v', 'p', 'f')"),
        (CK_KP_RUNG_SCOPE, "CK_KP_RUNG_SCOPE", "scope IN ('cls', 'all')"),
        (
            CK_KP_FAMILY_RUNG_PARAMETER, "CK_KP_FAMILY_RUNG_PARAMETER",
            "(level = 'p') = (first_main_parameter_id IS NOT NULL)",
        ),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0023(), name)
        assert model_expr == independent

    def test_the_closed_sets_match_the_check_literals(self):
        listed = ", ".join(f"'{v}'" for v in KP_RUNG_LEVELS)
        assert f"level IN ({listed})" == CK_KP_RUNG_LEVEL
        listed = ", ".join(f"'{v}'" for v in KP_RUNG_SCOPES)
        assert f"scope IN ({listed})" == CK_KP_RUNG_SCOPE
        limit = f"char_length(text) <= {KP_COMMENT_MAX}"
        assert limit == CK_KP_COMMENT_LENGTH

    def test_the_live_checks_carry_the_literals(self, db_session):
        agreements = _live_constraints(db_session, "kp_line_agreements")
        assert "NaN" in agreements["ck_kp_line_agreements_net_price"]
        assert "Infinity" in agreements["ck_kp_line_agreements_net_price"]
        comments = _live_constraints(db_session, "kp_line_comments")
        assert "2000" in comments["ck_kp_line_comments_text_length"]
        family = _live_constraints(db_session, "kp_family_rungs")
        assert "first_main_parameter_id IS NOT NULL" in family["ck_kp_family_rungs_first_main_parameter"]

    def test_the_foreign_key_actions(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT conrelid::regclass::text, conname, confdeltype FROM pg_constraint "
                "WHERE contype = 'f' AND conrelid::regclass::text LIKE 'kp\\_%'"
            )
        ).all()
        actions = {name: kind for _table, name, kind in rows}
        # 'c' = CASCADE, 'r' = RESTRICT
        assert actions == {
            "fk_kp_line_agreements_position_item": "c",
            "fk_kp_line_agreements_set_by": "r",
            "fk_kp_line_comments_position_item": "c",
            "fk_kp_line_comments_set_by": "r",
            "fk_kp_line_rungs_position_item": "c",
            "fk_kp_line_rungs_set_by": "r",
            "fk_kp_family_rungs_estimate": "c",
            "fk_kp_family_rungs_work_family": "c",
            "fk_kp_family_rungs_set_by": "r",
            "fk_kp_sent_files_offer": "c",
            "fk_kp_sent_files_created_by": "r",
        }


    _MODEL_CHECKS = {
        KpLineAgreement: {"ck_kp_line_agreements_net_price": "CK_KP_AGREEMENT_NET_PRICE"},
        KpLineComment: {"ck_kp_line_comments_text_length": "CK_KP_COMMENT_LENGTH"},
        KpLineRung: {
            "ck_kp_line_rungs_level": "CK_KP_RUNG_LEVEL",
            "ck_kp_line_rungs_scope": "CK_KP_RUNG_SCOPE",
        },
        KpFamilyRung: {
            "ck_kp_family_rungs_level": "CK_KP_RUNG_LEVEL",
            "ck_kp_family_rungs_scope": "CK_KP_RUNG_SCOPE",
            "ck_kp_family_rungs_first_main_parameter": "CK_KP_FAMILY_RUNG_PARAMETER",
        },
        KpSentFile: {},
    }

    @pytest.mark.parametrize(
        "model", list(_MODEL_CHECKS), ids=[model.__tablename__ for model in _MODEL_CHECKS]
    )
    def test_the_model_declares_the_migration_checks(self, model):
        """`alembic check` CHECK-выражения не сравнивает: что модель объявляет те же
        ограничения с теми же текстами, что миграция, держит только этот тест."""
        migration = _migration_0023()
        declared = {
            c.name: str(c.sqltext)
            for c in model.__table__.constraints
            if isinstance(c, sa.CheckConstraint)
        }
        expected = {
            name: getattr(migration, constant)
            for name, constant in self._MODEL_CHECKS[model].items()
        }
        assert declared == expected

    def test_the_models_declare_the_foreign_key_actions(self):
        declared = {
            fk.name: fk.ondelete
            for model in self._MODEL_CHECKS
            for fk in model.__table__.foreign_key_constraints
        }
        assert declared == {
            "fk_kp_line_agreements_position_item": "CASCADE",
            "fk_kp_line_agreements_set_by": "RESTRICT",
            "fk_kp_line_comments_position_item": "CASCADE",
            "fk_kp_line_comments_set_by": "RESTRICT",
            "fk_kp_line_rungs_position_item": "CASCADE",
            "fk_kp_line_rungs_set_by": "RESTRICT",
            "fk_kp_family_rungs_estimate": "CASCADE",
            "fk_kp_family_rungs_work_family": "CASCADE",
            "fk_kp_family_rungs_set_by": "RESTRICT",
            "fk_kp_sent_files_offer": "CASCADE",
            "fk_kp_sent_files_created_by": "RESTRICT",
        }


# ---------------------------------------------------------------------------
#  9. downgrade: счётчики, отказ, живые прогоны
# ---------------------------------------------------------------------------

_COUNTER_PHRASES = {table: f"{table} — 5" for table in _TABLES}


class TestDowngradeRefusalLogic:
    """`_downgrade_refusal` — чистая логика на вручную построенных словарях: каждый
    счётчик единственный положительный, значение (5) отличительное."""

    _ZERO = {table: 0 for table in _TABLES}

    def test_there_are_five_counters(self):
        assert tuple(_COUNTER_PHRASES) == _TABLES

    @pytest.mark.parametrize("table", _TABLES)
    def test_each_counter_alone_blocks_and_is_named(self, table):
        blockers = dict(self._ZERO)
        blockers[table] = 5
        refusal = _migration_0023()._downgrade_refusal(blockers)
        assert refusal is not None
        assert "0023" in refusal
        assert _COUNTER_PHRASES[table] in refusal
        for other, phrase in _COUNTER_PHRASES.items():
            if other != table:
                assert phrase not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0023()._downgrade_refusal(dict(self._ZERO)) is None


class TestDowngradeBlockersLive:
    """Каждая таблица — свой вход на живой схеме: считается ровно её счётчик."""

    _MAKERS = {
        "kp_line_agreements": _agreement,
        "kp_line_comments": _comment,
        "kp_line_rungs": _line_rung,
        "kp_family_rungs": _family_rung,
        "kp_sent_files": _sent_file,
    }

    def _blockers(self, db_session) -> dict:
        return _migration_0023()._downgrade_blockers(db_session.connection())

    def test_empty_tables_do_not_block(self, db_session):
        assert self._blockers(db_session) == {table: 0 for table in _TABLES}

    @pytest.mark.parametrize("table", _TABLES)
    def test_each_table_alone_raises_exactly_its_counter(self, db_session, factories, table):
        world = _world(db_session, factories)
        self._MAKERS[table](db_session, world)
        assert self._blockers(db_session) == {t: (1 if t == table else 0) for t in _TABLES}

    def test_counters_count_rows(self, db_session, factories):
        world = _world(db_session, factories)
        _sent_file(db_session, world)
        _sent_file(db_session, world)
        assert self._blockers(db_session)["kp_sent_files"] == 2


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


def _table_names(conn) -> set:
    return {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
    }


@pytest.mark.migration_roundtrip
class TestDowngradeOnEmptyDatabase:
    def test_downgrade_removes_the_five_tables_and_upgrade_restores_them(self):
        with _scratch_alembic("0023 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0022")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before_tables = _table_names(conn)
                    before_constraints = _constraint_names(conn)
                command.upgrade(cfg, "0023")
                with engine.connect() as conn:
                    upgraded = _table_names(conn)
                    revision_up = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                assert upgraded - before_tables == set(_TABLES)
                assert revision_up == "0023"

                command.downgrade(cfg, "0022")
                with engine.connect() as conn:
                    after_tables = _table_names(conn)
                    after_constraints = _constraint_names(conn)
                    revision_down = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                assert after_tables == before_tables
                assert after_constraints == before_constraints
                assert revision_down == "0022"

                command.upgrade(cfg, "0023")
                with engine.connect() as conn:
                    assert _table_names(conn) == upgraded
            finally:
                engine.dispose()


#: Строка в одной таблице. Внешние ключи при вставке отключены
#: (`session_replication_role = replica`): тест проверяет счётчик отката, а не
#: цепочку родительских строк.
_SEED_ONE_ROW = {
    "kp_line_agreements": (
        "INSERT INTO kp_line_agreements (position_item_id, net_price, set_by) VALUES (1, 1, 1)"
    ),
    "kp_line_comments": (
        "INSERT INTO kp_line_comments (position_item_id, text, set_by) VALUES (1, '', 1)"
    ),
    "kp_line_rungs": (
        "INSERT INTO kp_line_rungs (position_item_id, level, scope, key, set_by) "
        "VALUES (1, 'v', 'cls', CAST('{}' AS jsonb), 1)"
    ),
    "kp_family_rungs": (
        "INSERT INTO kp_family_rungs (estimate_id, work_family_id, level, scope, set_by) "
        "VALUES (1, 1, 'f', 'all', 1)"
    ),
    "kp_sent_files": (
        "INSERT INTO kp_sent_files (offer_id, file_key, filename, file_sha256, size_bytes, "
        "vat_rate, level_label, created_by) "
        "VALUES (1, 'k', 'f.xlsx', 'a', 1, 0.12, 'x', 1)"
    ),
}


@pytest.mark.migration_roundtrip
class TestDowngradeRefusedByEachTable:
    @pytest.mark.parametrize("table", _TABLES)
    def test_downgrade_is_refused_and_leaves_the_schema_untouched(self, table):
        with _scratch_alembic(f"0023 downgrade refusal {table}") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0023")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
                    conn.execute(sa.text(_SEED_ONE_ROW[table]))

                with pytest.raises(RuntimeError, match="0023") as exc:
                    command.downgrade(cfg, "0022")

                assert f"{table} — 1" in str(exc.value)
                for other in _TABLES:
                    if other != table:
                        assert f"{other} — 1" not in str(exc.value)
                with engine.connect() as conn:
                    revision = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    tables = _table_names(conn)
                assert revision == "0023"
                assert set(_TABLES) <= tables
            finally:
                engine.dispose()


# ---------------------------------------------------------------------------
#  10. Пять таблиц — явно в _DOMAIN_TABLES, очистка их чистит
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_the_five_tables_are_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in _TABLES:
            assert table in _DOMAIN_TABLES

    def test_truncate_clears_the_five_tables(
        self, db_engine, committing_db, committing_factories
    ):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        world = _world(session, committing_factories)
        _agreement(session, world)
        _comment(session, world)
        _line_rung(session, world)
        _family_rung(session, world)
        _sent_file(session, world)
        session.commit()

        def counts():
            with db_engine.connect() as conn:
                return {table: _count(conn, table) for table in _TABLES}

        assert counts() == {table: 1 for table in _TABLES}
        session.close()
        _truncate_domain_tables(db_engine)
        assert counts() == {table: 0 for table in _TABLES}

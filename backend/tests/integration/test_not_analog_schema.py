"""Данные «Не аналог»: миграция 0030, таблицы `price_error_marks` и `not_analog_marks`, загрузчики
`crud/not_analog.py` (спека «Не аналог» §1).

Названия и числа выдуманы; ограничения проверяются по имени из `diag.constraint_name`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from crud.not_analog import Mark, load_not_analog_marks, load_price_error_marks
from models import (
    CK_MARK_REASON,
    NotAnalogMark,
    PositionItem,
    PriceErrorMark,
    Tender,
    UnitOfMeasure,
    User,
)
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_api import _unit_id
from tests.integration.test_work_variants_schema import _scratch_alembic

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Scene:
    def __init__(self, db, factories, admin):
        self.db = db
        self.f = factories
        self.admin = admin

    def item(self):
        """Строка сметы договора (носитель отметки)."""
        contract = self.f.ContractFactory.create()
        estimate = self.f.EstimateFactory.create(contract=contract)
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(lot=lot, contractor=contract.contractor)
        return self.f.PositionItemFactory.create(proposal=proposal, is_chapter=False)

    def tender(self):
        return self.f.TenderFactory.create()

    def price_error(self, item, reason="опечатка в цене", by=None):
        mark = PriceErrorMark(position_item_id=item.id, reason=reason, set_by=(by or self.admin).id)
        self.db.add(mark)
        self.db.flush()
        return mark

    def not_analog(
        self, tender, item, *, title="монтаж емкости", unit_id=None, reason="другая работа", by=None
    ):
        mark = NotAnalogMark(
            tender_id=tender.id, form_title=title, form_unit_id=unit_id,
            position_item_id=item.id, reason=reason, set_by=(by or self.admin).id,
        )
        self.db.add(mark)
        self.db.flush()
        return mark


@pytest.fixture
def scene(db_session, factories, admin_user):
    return Scene(db_session, factories, admin_user)


def _count(db, model):
    return db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


class TestSchema:
    def test_price_error_columns(self, db_session):
        columns = {
            row[0]: (row[1], row[2])
            for row in db_session.execute(sa.text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'price_error_marks'"))
        }
        assert columns == {
            "position_item_id": ("bigint", "NO"),
            "reason": ("text", "NO"),
            "set_by": ("integer", "NO"),
            "set_at": ("timestamp with time zone", "NO"),
        }

    def test_not_analog_columns(self, db_session):
        columns = {
            row[0]: (row[1], row[2])
            for row in db_session.execute(sa.text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'not_analog_marks'"))
        }
        assert columns == {
            "id": ("bigint", "NO"),
            "tender_id": ("bigint", "NO"),
            "form_title": ("text", "NO"),
            "form_unit_id": ("integer", "YES"),
            "position_item_id": ("bigint", "NO"),
            "reason": ("text", "NO"),
            "set_by": ("integer", "NO"),
            "set_at": ("timestamp with time zone", "NO"),
        }

    @pytest.mark.parametrize("reason", ["", "я" * 301])
    def test_price_error_reason_length_is_checked_by_name(self, scene, reason):
        item = scene.item()
        with rejected_by(scene.db, "ck_price_error_marks_reason"):
            scene.db.add(PriceErrorMark(position_item_id=item.id, reason=reason, set_by=scene.admin.id))

    @pytest.mark.parametrize("reason", ["", "я" * 301])
    def test_not_analog_reason_length_is_checked_by_name(self, scene, reason):
        item, tender = scene.item(), scene.tender()
        with rejected_by(scene.db, "ck_not_analog_marks_reason"):
            scene.db.add(NotAnalogMark(
                tender_id=tender.id, form_title="x", position_item_id=item.id,
                reason=reason, set_by=scene.admin.id,
            ))

    @pytest.mark.parametrize("reason", ["я", "я" * 300])
    def test_reason_boundaries_are_accepted(self, scene, reason):
        scene.price_error(scene.item(), reason=reason)
        scene.not_analog(scene.tender(), scene.item(), reason=reason)

    def test_empty_form_title_is_rejected_by_name(self, scene):
        item, tender = scene.item(), scene.tender()
        with rejected_by(scene.db, "ck_not_analog_marks_form_title"):
            scene.db.add(NotAnalogMark(
                tender_id=tender.id, form_title="", position_item_id=item.id,
                reason="причина", set_by=scene.admin.id,
            ))

    @pytest.mark.parametrize("with_unit", [False, True])
    def test_triple_is_unique_even_with_a_null_unit(self, scene, with_unit):
        item, tender = scene.item(), scene.tender()
        unit = _unit_id(scene.db, "M2") if with_unit else None
        scene.not_analog(tender, item, unit_id=unit)
        with rejected_by(scene.db, "uq_not_analog_marks_key"):
            scene.db.add(NotAnalogMark(
                tender_id=tender.id, form_title="монтаж емкости", form_unit_id=unit,
                position_item_id=item.id, reason="ещё раз", set_by=scene.admin.id,
            ))

    def test_other_unit_title_item_or_tender_is_a_different_mark(self, scene):
        item, other_item, tender = scene.item(), scene.item(), scene.tender()
        scene.not_analog(tender, item, unit_id=None)
        scene.not_analog(tender, item, unit_id=_unit_id(scene.db, "M2"))
        scene.not_analog(tender, item, title="другое наименование")
        scene.not_analog(tender, other_item)
        scene.not_analog(scene.tender(), item)

    def test_deleting_the_item_cascades_both_kinds(self, scene):
        item, tender = scene.item(), scene.tender()
        scene.price_error(item)
        scene.not_analog(tender, item)
        scene.db.execute(sa.delete(PositionItem).where(PositionItem.id == item.id))
        assert _count(scene.db, PriceErrorMark) == 0
        assert _count(scene.db, NotAnalogMark) == 0

    def test_deleting_the_tender_cascades_tender_marks_only(self, scene):
        item, tender = scene.item(), scene.tender()
        scene.price_error(item)
        scene.not_analog(tender, item)
        scene.db.execute(sa.delete(Tender).where(Tender.id == tender.id))
        assert _count(scene.db, NotAnalogMark) == 0
        assert _count(scene.db, PriceErrorMark) == 1

    @pytest.mark.parametrize(
        ("kind", "constraint"),
        [("price_error", "fk_price_error_marks_set_by"), ("tender", "fk_not_analog_marks_set_by")],
    )
    def test_deleting_the_author_is_restricted(self, scene, factories, kind, constraint):
        author = factories.UserFactory.create()
        item = scene.item()
        if kind == "price_error":
            scene.price_error(item, by=author)
        else:
            scene.not_analog(scene.tender(), item, by=author)
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.execute(sa.delete(User).where(User.id == author.id))
        assert exc.value.orig.diag.constraint_name == constraint

    def test_deleting_the_form_unit_is_restricted(self, scene):
        unit_id = _unit_id(scene.db, "M2")
        scene.not_analog(scene.tender(), scene.item(), unit_id=unit_id)
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.execute(sa.delete(UnitOfMeasure).where(UnitOfMeasure.id == unit_id))
        assert exc.value.orig.diag.constraint_name == "fk_not_analog_marks_form_unit"


def _migration_0030():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0030-not_analog_marks.py"))
    spec = importlib.util.spec_from_file_location("_migration_0030_not_analog_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParityWithMigration:
    def test_model_expression_equals_migration_and_independent_literal(self):
        assert CK_MARK_REASON == _migration_0030().CK_MARK_REASON
        assert CK_MARK_REASON == "char_length(reason) BETWEEN 1 AND 300"

    def test_revision_chain(self):
        module = _migration_0030()
        assert (module.revision, module.down_revision) == ("0030", "0029")


@pytest.mark.migration_roundtrip
class TestMigrationRoundtrip:
    def test_upgrade_creates_and_downgrade_drops_both_tables(self):
        with _scratch_alembic("0030 roundtrip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0029")
            engine = sa.create_engine(scratch_url)
            try:
                def exists(table):
                    with engine.connect() as conn:
                        return conn.execute(
                            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
                        ).scalar_one()

                assert (exists("price_error_marks"), exists("not_analog_marks")) == (False, False)
                command.upgrade(cfg, "0030")
                assert (exists("price_error_marks"), exists("not_analog_marks")) == (True, True)
                with engine.connect() as conn:
                    names = {
                        row[0]
                        for row in conn.execute(sa.text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'not_analog_marks'::regclass"))
                    }
                assert {
                    "pk_not_analog_marks", "ck_not_analog_marks_reason", "ck_not_analog_marks_form_title",
                    "uq_not_analog_marks_key", "fk_not_analog_marks_tender", "fk_not_analog_marks_form_unit",
                    "fk_not_analog_marks_position_item", "fk_not_analog_marks_set_by",
                } <= names
                command.downgrade(cfg, "0029")
                assert (exists("price_error_marks"), exists("not_analog_marks")) == (False, False)
            finally:
                engine.dispose()


class TestLoaders:
    def test_tender_marks_are_keyed_by_form_then_item_with_author_email(self, scene):
        tender, other = scene.tender(), scene.tender()
        a, b = scene.item(), scene.item()
        unit = _unit_id(scene.db, "M2")
        scene.not_analog(tender, a, title="монтаж", unit_id=unit, reason="р1")
        scene.not_analog(tender, b, title="монтаж", unit_id=unit, reason="р2")
        scene.not_analog(tender, a, title="монтаж", unit_id=None, reason="р3")
        scene.not_analog(other, a, title="монтаж", unit_id=unit, reason="чужой тендер")

        marks = load_not_analog_marks(scene.db, tender.id)

        assert set(marks) == {("монтаж", unit), ("монтаж", None)}
        assert set(marks[("монтаж", unit)]) == {a.id, b.id}
        mark = marks[("монтаж", unit)][a.id]
        assert isinstance(mark, Mark)
        assert (mark.kind, mark.reason, mark.set_by_id, mark.set_by_email) == (
            "tender", "р1", scene.admin.id, scene.admin.email
        )
        assert mark.set_at is not None
        assert marks[("монтаж", None)][a.id].reason == "р3"
        assert load_not_analog_marks(scene.db, other.id)[("монтаж", unit)][a.id].reason == "чужой тендер"

    def test_tender_without_marks_gives_an_empty_mapping(self, scene):
        assert dict(load_not_analog_marks(scene.db, scene.tender().id)) == {}

    def test_price_error_marks_carry_the_author_and_skip_unmarked_rows(self, scene):
        marked, plain = scene.item(), scene.item()
        scene.price_error(marked, reason="опечатка")

        marks = load_price_error_marks(scene.db, [marked.id, plain.id])

        assert set(marks) == {marked.id}
        mark = marks[marked.id]
        assert (mark.kind, mark.reason, mark.set_by_id, mark.set_by_email) == (
            "price_error", "опечатка", scene.admin.id, scene.admin.email
        )

    def test_empty_input_issues_no_query(self, scene):
        statements: list[str] = []

        def _record(_conn, _cursor, statement, *_args, **_kwargs):
            statements.append(statement)

        engine = scene.db.get_bind()
        sa.event.listen(engine, "before_cursor_execute", _record)
        try:
            assert load_price_error_marks(scene.db, []) == {}
            assert load_price_error_marks(scene.db, set()) == {}
        finally:
            sa.event.remove(engine, "before_cursor_execute", _record)
        assert statements == []

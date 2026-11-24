"""Данные «Подозрительных строк»: миграция 0031, таблица `not_error_marks`, кэш индекса
(спека §1 п. 1 и п. 3).

Названия и числа выдуманы; ограничения проверяются по имени из `diag.constraint_name`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from config import settings
from models import (
    CK_MARK_REASON,
    CK_NOT_ERROR_SHAPE,
    CK_NOT_ERROR_SHAPE_EXPR,
    NotErrorMark,
    PositionItem,
    UnitOfMeasure,
    User,
    WorkFamily,
)
from services.status_cache import StatusCache, suspicion_cache, typical_rates_cache
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_api import _active_family, _unit_id
from tests.integration.test_work_variants_schema import _scratch_alembic

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Scene:
    def __init__(self, db, factories, admin):
        self.db = db
        self.f = factories
        self.admin = admin

    def item(self):
        contract = self.f.ContractFactory.create()
        estimate = self.f.EstimateFactory.create(contract=contract)
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(lot=lot, contractor=contract.contractor)
        return self.f.PositionItemFactory.create(proposal=proposal, is_chapter=False)

    def family(self, title="Устройство стяжки"):
        return _active_family(
            self.db, title=title, unit_name="M2", actor_id=self.admin.id
        )

    def bare_family(self, title="Голая семья"):
        """Семья без журнала и схемы: её можно удалить (журнал держит активную RESTRICT-ссылкой)."""
        family = WorkFamily(title=title, status="draft", created_by=self.admin.id)
        self.db.add(family)
        self.db.flush()
        return family

    def lone_unit(self):
        """Единица, на которую ссылается только решение «не ошибка»."""
        m2 = self.db.get(UnitOfMeasure, _unit_id(self.db, "M2"))
        unit = UnitOfMeasure(code="ZZSUSP", name="тест", symbol="зз", dimension=m2.dimension)
        self.db.add(unit)
        self.db.flush()
        return unit

    def outlier(self, item, family, *, unit_id=None, direction="up", reason="проверено", by=None):
        mark = NotErrorMark(
            position_item_id=item.id, kind="outlier", family_id=family.id,
            unit_id=unit_id or _unit_id(self.db, "M2"), direction=direction,
            reason=reason, set_by=(by or self.admin).id,
        )
        self.db.add(mark)
        self.db.flush()
        return mark

    def twin(self, item, neighbor, *, reason="проверено", by=None):
        mark = NotErrorMark(
            position_item_id=item.id, kind="twin", neighbor_item_id=neighbor.id,
            reason=reason, set_by=(by or self.admin).id,
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
    def test_columns(self, db_session):
        columns = {
            row[0]: (row[1], row[2])
            for row in db_session.execute(sa.text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'not_error_marks'"))
        }
        assert columns == {
            "id": ("bigint", "NO"),
            "position_item_id": ("bigint", "NO"),
            "kind": ("text", "NO"),
            "family_id": ("integer", "YES"),
            "unit_id": ("integer", "YES"),
            "direction": ("text", "YES"),
            "neighbor_item_id": ("bigint", "YES"),
            "reason": ("text", "NO"),
            "set_by": ("integer", "NO"),
            "set_at": ("timestamp with time zone", "NO"),
        }

    def test_valid_outlier_and_twin_are_accepted(self, scene):
        item, neighbor = scene.item(), scene.item()
        scene.outlier(item, scene.family(), direction="down")
        scene.twin(item, neighbor)
        assert _count(scene.db, NotErrorMark) == 2

    def test_shape_constraint_name_is_exported(self):
        assert CK_NOT_ERROR_SHAPE == "ck_not_error_marks_shape"

    @pytest.mark.parametrize("missing", ["direction", "family_id", "unit_id"])
    def test_outlier_without_a_basis_field_is_rejected_by_shape(self, scene, missing):
        item, family = scene.item(), scene.family()
        fields = dict(
            position_item_id=item.id, kind="outlier", family_id=family.id,
            unit_id=_unit_id(scene.db, "M2"), direction="up", reason="р", set_by=scene.admin.id,
        )
        fields[missing] = None
        with rejected_by(scene.db, CK_NOT_ERROR_SHAPE):
            scene.db.add(NotErrorMark(**fields))

    def test_outlier_with_a_neighbor_is_rejected_by_shape(self, scene):
        item, other, family = scene.item(), scene.item(), scene.family()
        with rejected_by(scene.db, CK_NOT_ERROR_SHAPE):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="outlier", family_id=family.id,
                unit_id=_unit_id(scene.db, "M2"), direction="up",
                neighbor_item_id=other.id, reason="р", set_by=scene.admin.id,
            ))

    @pytest.mark.parametrize("extra", ["family_id", "unit_id", "direction"])
    def test_twin_with_an_outlier_field_is_rejected_by_shape(self, scene, extra):
        item, neighbor, family = scene.item(), scene.item(), scene.family()
        fields = dict(
            position_item_id=item.id, kind="twin", neighbor_item_id=neighbor.id,
            reason="р", set_by=scene.admin.id,
        )
        fields[extra] = {
            "family_id": family.id, "unit_id": _unit_id(scene.db, "M2"), "direction": "up",
        }[extra]
        with rejected_by(scene.db, CK_NOT_ERROR_SHAPE):
            scene.db.add(NotErrorMark(**fields))

    def test_twin_without_a_neighbor_is_rejected_by_shape(self, scene):
        item = scene.item()
        with rejected_by(scene.db, CK_NOT_ERROR_SHAPE):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="twin", reason="р", set_by=scene.admin.id,
            ))

    def test_unknown_kind_and_direction_are_rejected_by_name(self, scene):
        item, family = scene.item(), scene.family()
        with rejected_by(scene.db, {"ck_not_error_marks_kind", CK_NOT_ERROR_SHAPE}):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="other", reason="р", set_by=scene.admin.id,
            ))
        with rejected_by(scene.db, "ck_not_error_marks_direction"):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="outlier", family_id=family.id,
                unit_id=_unit_id(scene.db, "M2"), direction="sideways",
                reason="р", set_by=scene.admin.id,
            ))

    @pytest.mark.parametrize("reason", ["", "я" * 301])
    def test_reason_length_is_checked_by_name(self, scene, reason):
        item, neighbor = scene.item(), scene.item()
        with rejected_by(scene.db, "ck_not_error_marks_reason"):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="twin", neighbor_item_id=neighbor.id,
                reason=reason, set_by=scene.admin.id,
            ))

    @pytest.mark.parametrize("reason", ["я", "я" * 300])
    def test_reason_boundaries_are_accepted(self, scene, reason):
        scene.twin(scene.item(), scene.item(), reason=reason)

    def test_second_twin_row_with_null_outlier_fields_is_rejected(self, scene):
        item, neighbor = scene.item(), scene.item()
        scene.twin(item, neighbor)
        with rejected_by(scene.db, "uq_not_error_marks_key"):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="twin", neighbor_item_id=neighbor.id,
                reason="ещё раз", set_by=scene.admin.id,
            ))

    def test_second_outlier_with_same_basis_is_rejected(self, scene):
        item, family = scene.item(), scene.family()
        scene.outlier(item, family)
        with rejected_by(scene.db, "uq_not_error_marks_key"):
            scene.db.add(NotErrorMark(
                position_item_id=item.id, kind="outlier", family_id=family.id,
                unit_id=_unit_id(scene.db, "M2"), direction="up",
                reason="ещё раз", set_by=scene.admin.id,
            ))

    def test_other_direction_neighbor_or_item_is_a_different_row(self, scene):
        item, other, neighbor, family = scene.item(), scene.item(), scene.item(), scene.family()
        scene.outlier(item, family, direction="up")
        scene.outlier(item, family, direction="down")
        scene.outlier(other, family, direction="up")
        scene.twin(item, neighbor)
        scene.twin(item, other)
        scene.twin(other, neighbor)
        assert _count(scene.db, NotErrorMark) == 6

    def test_deleting_the_item_cascades(self, scene):
        item, neighbor = scene.item(), scene.item()
        scene.outlier(item, scene.family())
        scene.twin(item, neighbor)
        scene.db.execute(sa.delete(PositionItem).where(PositionItem.id == item.id))
        assert _count(scene.db, NotErrorMark) == 0

    def test_deleting_the_neighbor_cascades_only_its_twin_rows(self, scene):
        item, neighbor, other = scene.item(), scene.item(), scene.item()
        scene.twin(item, neighbor)
        scene.twin(item, other)
        scene.outlier(item, scene.family())
        scene.db.execute(sa.delete(PositionItem).where(PositionItem.id == neighbor.id))
        remaining = scene.db.execute(
            sa.select(NotErrorMark.kind, NotErrorMark.neighbor_item_id)
        ).all()
        assert sorted(remaining, key=lambda r: r[0]) == [("outlier", None), ("twin", other.id)]

    def test_deleting_the_family_cascades_outlier_rows(self, scene):
        item, neighbor, family = scene.item(), scene.item(), scene.bare_family()
        scene.outlier(item, family)
        scene.twin(item, neighbor)
        scene.db.execute(sa.delete(WorkFamily).where(WorkFamily.id == family.id))
        assert scene.db.execute(sa.select(NotErrorMark.kind)).scalars().all() == ["twin"]

    def test_deleting_the_author_is_restricted(self, scene, factories):
        author = factories.UserFactory.create()
        scene.twin(scene.item(), scene.item(), by=author)
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.execute(sa.delete(User).where(User.id == author.id))
        assert exc.value.orig.diag.constraint_name == "fk_not_error_marks_set_by"

    def test_deleting_the_unit_is_restricted(self, scene):
        unit_id = scene.lone_unit().id
        scene.outlier(scene.item(), scene.bare_family(), unit_id=unit_id)
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.execute(sa.delete(UnitOfMeasure).where(UnitOfMeasure.id == unit_id))
        assert exc.value.orig.diag.constraint_name == "fk_not_error_marks_unit"


def _migration_0031():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0031-not_error_marks.py"))
    spec = importlib.util.spec_from_file_location("_migration_0031_suspicious_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParityWithMigration:
    def test_revision_chain(self):
        module = _migration_0031()
        assert (module.revision, module.down_revision) == ("0031", "0030")

    def test_shape_name_and_expression_equal_migration(self):
        module = _migration_0031()
        assert CK_NOT_ERROR_SHAPE == module.CK_NOT_ERROR_SHAPE
        assert module.CK_NOT_ERROR_SHAPE == "ck_not_error_marks_shape"
        assert CK_NOT_ERROR_SHAPE_EXPR == module.CK_NOT_ERROR_SHAPE_EXPR
        assert CK_MARK_REASON == module.CK_MARK_REASON


@pytest.mark.migration_roundtrip
class TestMigrationRoundtrip:
    def test_upgrade_creates_and_downgrade_drops_the_table(self):
        with _scratch_alembic("0031 roundtrip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0030")
            engine = sa.create_engine(scratch_url)
            try:
                def exists():
                    with engine.connect() as conn:
                        return conn.execute(
                            sa.text("SELECT to_regclass('public.not_error_marks') IS NOT NULL")
                        ).scalar_one()

                assert exists() is False
                command.upgrade(cfg, "0031")
                assert exists() is True
                with engine.connect() as conn:
                    names = {
                        row[0]
                        for row in conn.execute(sa.text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'not_error_marks'::regclass"))
                    }
                    indexes = {
                        row[0]
                        for row in conn.execute(sa.text(
                            "SELECT indexname FROM pg_indexes WHERE tablename = 'not_error_marks'"))
                    }
                assert {
                    "pk_not_error_marks", "ck_not_error_marks_kind", "ck_not_error_marks_direction",
                    "ck_not_error_marks_shape", "ck_not_error_marks_reason",
                    "uq_not_error_marks_key", "fk_not_error_marks_position_item",
                    "fk_not_error_marks_family", "fk_not_error_marks_unit",
                    "fk_not_error_marks_neighbor", "fk_not_error_marks_set_by",
                } <= names
                assert "ix_not_error_marks_position_item_id" in indexes
                command.downgrade(cfg, "0030")
                assert exists() is False
            finally:
                engine.dispose()


class TestSuspicionCache:
    def test_is_a_separate_instance_with_its_own_ttl_setting(self):
        assert isinstance(suspicion_cache, StatusCache)
        assert suspicion_cache is not typical_rates_cache
        from config import Settings

        assert Settings.model_fields["SUSPICION_CACHE_TTL_S"].default == 300

    def test_ttl_is_read_from_the_setting(self, monkeypatch):
        calls = []

        def build():
            calls.append(1)
            return len(calls)

        monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 300)
        monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 0)
        suspicion_cache.invalidate()
        try:
            assert suspicion_cache.get(build) == 1
            assert suspicion_cache.get(build) == 2  # срок 0 — кэш выключен
            monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
            assert suspicion_cache.get(build) == 3
            assert suspicion_cache.get(build) == 3  # срок 300 — значение держится
        finally:
            suspicion_cache.invalidate()

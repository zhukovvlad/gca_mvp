"""Маршруты поправки «комплект или ставка» — `PUT/DELETE /api/v1/position-items/{id}/lump-sum`
(спека строк-сумм §3 п. 2). Сцена — та же, что у загрузчика (`test_lump_sum_load.Scene`)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    ContextMember,
    FamilyParameterSchema,
    LumpSumOverride,
    PositionItem,
    Proposal,
    UserRole,
    WorkVariant,
)
from tests.integration.test_lump_sum_load import Scene

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def scene(db_session, factories, admin_client):
    return Scene(db_session, factories, admin_client.user)


def _url(item_id):
    return f"/api/v1/position-items/{item_id}/lump-sum"


def _variant(scene, item):
    """Контекст строки получает вариант семьи (сцена ставит семью, вариант — нет)."""
    context = scene.db.execute(
        sa.select(CatalogContext)
        .join(ContextMember, ContextMember.context_id == CatalogContext.id)
        .where(ContextMember.position_item_id == item.id)
    ).scalar_one()
    schema = scene.db.execute(
        sa.select(FamilyParameterSchema).where(
            FamilyParameterSchema.family_id == context.work_family_id
        )
    ).scalar_one()
    variant = WorkVariant(
        family_id=context.work_family_id, schema_id=schema.id,
        values_key=f"v{item.id}", status="active",
    )
    scene.db.add(variant)
    scene.db.flush()
    scene.db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context.id)
        .values(
            work_variant_id=variant.id,
            variant_at=dt.datetime(2026, 11, 22, tzinfo=dt.UTC),
            variant_paths_hash="h",
        )
    )
    scene.db.flush()
    return context.id


def _good_item(scene, **kwargs):
    _, proposal = scene.contract_estimate()
    item = scene.line(proposal, **kwargs)
    _variant(scene, item)
    return item


def _row(scene, item):
    scene.db.expire_all()
    return scene.db.get(LumpSumOverride, item.id)


class TestPut:
    def test_writes_value_author_and_time(self, scene, admin_client):
        item = _good_item(scene)
        response = admin_client.put(_url(item.id), json={"verdict": "lump"})
        assert response.status_code == 200
        body = response.json()
        assert body["position_item_id"] == item.id and body["verdict"] == "lump"
        assert body["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
        row = _row(scene, item)
        assert row.verdict == "lump" and row.set_by == admin_client.user.id
        assert dt.datetime.fromisoformat(body["set_at"]) == row.set_at

    def test_offer_estimate_line_is_accepted(self, scene, admin_client):
        _, proposal = scene.offer_estimate()
        item = scene.line(proposal)
        _variant(scene, item)
        assert admin_client.put(_url(item.id), json={"verdict": "rate"}).status_code == 200

    def test_same_value_keeps_author_and_time_even_for_another_admin(
        self, scene, admin_client, factories
    ):
        item = _good_item(scene)
        first = admin_client.put(_url(item.id), json={"verdict": "lump"}).json()
        other = factories.UserFactory.create(role=UserRole.admin)
        admin_client.set_user(other)
        again = admin_client.put(_url(item.id), json={"verdict": "lump"})
        assert again.status_code == 200
        assert again.json() == first
        assert _row(scene, item).set_by != other.id

    def test_changed_value_gets_new_author_and_time(self, scene, admin_client, factories):
        item = _good_item(scene)
        first = admin_client.put(_url(item.id), json={"verdict": "lump"}).json()
        other = factories.UserFactory.create(role=UserRole.admin)
        admin_client.set_user(other)
        changed = admin_client.put(_url(item.id), json={"verdict": "rate"})
        assert changed.status_code == 200
        body = changed.json()
        assert body["verdict"] == "rate" and body["set_by"]["id"] == other.id
        assert dt.datetime.fromisoformat(body["set_at"]) > dt.datetime.fromisoformat(first["set_at"])
        assert _row(scene, item).verdict == "rate"

    def test_unknown_vat_base_and_zero_price_are_accepted(self, scene, admin_client):
        _, proposal = scene.contract_estimate()
        scene.db.execute(
            sa.update(Proposal).where(Proposal.id == proposal.id).values(vat_rate=None)
        )
        no_base = scene.line(proposal)
        _variant(scene, no_base)
        _, proposal2 = scene.contract_estimate()
        zero = scene.line(proposal2, price=D("0"))
        _variant(scene, zero)
        assert admin_client.put(_url(no_base.id), json={"verdict": "lump"}).status_code == 200
        assert admin_client.put(_url(zero.id), json={"verdict": "rate"}).status_code == 200

    def test_other_verdict_is_a_validation_error(self, scene, admin_client):
        item = _good_item(scene)
        assert admin_client.put(_url(item.id), json={"verdict": "other"}).status_code == 422
        assert _row(scene, item) is None

    def test_row_is_locked_for_update(self, scene, admin_client):
        item = _good_item(scene)
        statements: list[str] = []

        def spy(conn, cursor, statement, *args):
            statements.append(statement)

        engine = scene.db.get_bind().engine
        sa.event.listen(engine, "before_cursor_execute", spy)
        try:
            admin_client.put(_url(item.id), json={"verdict": "lump"})
        finally:
            sa.event.remove(engine, "before_cursor_execute", spy)
        assert any("FROM position_items" in s and "FOR UPDATE" in s for s in statements)


class TestNotFound:
    def test_missing_row_is_404_on_both_routes(self, admin_client):
        for call in (
            lambda: admin_client.put(_url(999_999_999), json={"verdict": "lump"}),
            lambda: admin_client.delete(_url(999_999_999)),
        ):
            response = call()
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "position_item_not_found"


class TestNotApplicable:
    def _expect(self, admin_client, item):
        response = admin_client.put(_url(item.id), json={"verdict": "lump"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "lump_sum_not_applicable"

    def test_applicable_control(self, scene, admin_client):
        item = _good_item(scene)
        assert admin_client.put(_url(item.id), json={"verdict": "lump"}).status_code == 200

    def test_chapter(self, scene, admin_client):
        item = _good_item(scene)
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == item.id).values(is_chapter=True)
        )
        self._expect(admin_client, item)

    def test_system_context(self, scene, admin_client):
        item = _good_item(scene)
        scene.db.execute(sa.update(CatalogContext).values(semantic_kind="SYSTEM"))
        self._expect(admin_client, item)

    def test_no_family(self, scene, admin_client):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal, family=None)
        self._expect(admin_client, item)

    def test_family_without_variant(self, scene, admin_client):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)
        self._expect(admin_client, item)

    def test_archived_context(self, scene, admin_client):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal, archived=True)
        _variant(scene, item)
        self._expect(admin_client, item)

    def test_round_estimate(self, scene, admin_client):
        _, proposal = scene.round_estimate()
        item = scene.line(proposal)
        _variant(scene, item)
        self._expect(admin_client, item)

    def test_no_membership(self, scene, admin_client):
        item = _good_item(scene)
        scene.db.execute(sa.delete(ContextMember).where(ContextMember.position_item_id == item.id))
        self._expect(admin_client, item)


class TestDelete:
    def test_removes_an_existing_override(self, scene, admin_client):
        item = _good_item(scene)
        scene.override(item, "lump")
        assert admin_client.delete(_url(item.id)).status_code == 204
        assert _row(scene, item) is None

    def test_without_a_record_is_204(self, scene, admin_client):
        item = _good_item(scene)
        assert admin_client.delete(_url(item.id)).status_code == 204

    def test_works_for_a_line_where_the_rule_is_not_applicable(self, scene, admin_client):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)  # без варианта
        scene.override(item, "lump")
        assert admin_client.delete(_url(item.id)).status_code == 204
        assert _row(scene, item) is None


class TestMember:
    def test_member_is_forbidden_on_both_routes(self, db_session, factories, member_client):
        scene = Scene(db_session, factories, factories.UserFactory.create(role=UserRole.admin))
        item = _good_item(scene)
        assert member_client.put(_url(item.id), json={"verdict": "lump"}).status_code == 403
        assert member_client.delete(_url(item.id)).status_code == 403
        assert _row(scene, item) is None

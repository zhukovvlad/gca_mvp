"""Маршруты отметок «ошибка в цене» и «не аналог в этом тендере» (спека «Не аналог» §2).

Сцена — та же, что у загрузчика поправки «комплект» (`test_lump_sum_load.Scene`): строка договора
с каталожной позицией и контекстом; строка КП — строка сметы оффера этапа тендера.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.not_analog import load_not_analog_marks
from models import (
    CatalogContext,
    ContextMember,
    NotAnalogMark,
    PositionItem,
    PriceErrorMark,
    UserRole,
)
from services.zone_rules import normalize_title
from tests.integration.test_lump_sum_load import Scene

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def scene(db_session, factories, admin_client):
    return Scene(db_session, factories, admin_client.user)


def _pe_url(item_id):
    return f"/api/v1/position-items/{item_id}/price-error"


def _na_url(kp, item_id):
    estimate = kp["estimate"]
    offer = estimate.offer
    return (
        f"/api/v1/tenders/{offer.round.tender_id}/rounds/{offer.round_id}/our-price/offers/"
        f"{offer.id}/lines/{kp['item'].id}/not-analog/{item_id}"
    )


def _kp(scene, *, title=None, unit="M2", offer_estimate=None):
    """Строка КП: смета оффера этапа тендера (по умолчанию новая)."""
    if offer_estimate is None:
        estimate, proposal = scene.offer_estimate()
    else:
        estimate, proposal = offer_estimate
    item = scene.line(proposal, unit=unit)
    if title is not None:
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == item.id).values(job_title_in_proposal=title)
        )
        scene.db.flush()
        scene.db.refresh(item)
    return {"estimate": estimate, "proposal": proposal, "item": item}


def _contract_item(scene, **kwargs):
    _, proposal = scene.contract_estimate()
    return scene.line(proposal, **kwargs)


def _pe_row(scene, item):
    scene.db.expire_all()
    return scene.db.get(PriceErrorMark, item.id)


def _na_rows(scene):
    scene.db.expire_all()
    return scene.db.execute(sa.select(NotAnalogMark)).scalars().all()


class TestPriceErrorPut:
    def test_writes_reason_author_time_and_returns_mark_view(self, scene, admin_client):
        item = _contract_item(scene)
        response = admin_client.put(_pe_url(item.id), json={"reason": "  лишний ноль  "})
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "price_error" and body["reason"] == "лишний ноль"
        assert body["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
        row = _pe_row(scene, item)
        assert row.reason == "лишний ноль" and row.set_by == admin_client.user.id
        assert dt.datetime.fromisoformat(body["set_at"]) == row.set_at

    def test_same_reason_keeps_author_and_time_even_for_another_admin(
        self, scene, admin_client, factories
    ):
        item = _contract_item(scene)
        first = admin_client.put(_pe_url(item.id), json={"reason": "опечатка"}).json()
        other = factories.UserFactory.create(role=UserRole.admin)
        admin_client.set_user(other)
        again = admin_client.put(_pe_url(item.id), json={"reason": "опечатка"})
        assert again.status_code == 200
        assert again.json() == first
        assert _pe_row(scene, item).set_by != other.id

    def test_changed_reason_gets_new_author_and_time(self, scene, admin_client, factories):
        item = _contract_item(scene)
        first = admin_client.put(_pe_url(item.id), json={"reason": "опечатка"}).json()
        other = factories.UserFactory.create(role=UserRole.admin)
        admin_client.set_user(other)
        changed = admin_client.put(_pe_url(item.id), json={"reason": "другая причина"})
        assert changed.status_code == 200
        body = changed.json()
        assert body["reason"] == "другая причина" and body["set_by"]["id"] == other.id
        assert dt.datetime.fromisoformat(body["set_at"]) > dt.datetime.fromisoformat(first["set_at"])

    @pytest.mark.parametrize("reason", ["", "   ", "x" * 301])
    def test_bad_reason_is_a_validation_error(self, scene, admin_client, reason):
        item = _contract_item(scene)
        assert admin_client.put(_pe_url(item.id), json={"reason": reason}).status_code == 422
        assert _pe_row(scene, item) is None

    def test_reason_of_300_is_accepted_and_trimmed_before_counting(self, scene, admin_client):
        item = _contract_item(scene)
        response = admin_client.put(_pe_url(item.id), json={"reason": " " + "x" * 300 + " "})
        assert response.status_code == 200
        assert len(_pe_row(scene, item).reason) == 300

    def test_row_is_locked_for_update(self, scene, admin_client):
        item = _contract_item(scene)
        statements: list[str] = []

        def spy(conn, cursor, statement, *args):
            statements.append(statement)

        engine = scene.db.get_bind().engine
        sa.event.listen(engine, "before_cursor_execute", spy)
        try:
            admin_client.put(_pe_url(item.id), json={"reason": "опечатка"})
        finally:
            sa.event.remove(engine, "before_cursor_execute", spy)
        assert any("FROM position_items" in s and "FOR UPDATE" in s for s in statements)

    def test_line_without_variant_zero_price_and_kit_are_accepted(self, scene, admin_client):
        plain = _contract_item(scene)  # вариант не назначен
        zero = _contract_item(scene, price=D("0"))
        kit = _contract_item(scene)
        scene.override(kit, "lump")
        for item in (plain, zero, kit):
            assert admin_client.put(_pe_url(item.id), json={"reason": "r"}).status_code == 200


class TestPriceErrorDeleteAndErrors:
    def test_delete_removes_and_without_record_is_204(self, scene, admin_client):
        item = _contract_item(scene)
        admin_client.put(_pe_url(item.id), json={"reason": "r"})
        assert admin_client.delete(_pe_url(item.id)).status_code == 204
        assert _pe_row(scene, item) is None
        assert admin_client.delete(_pe_url(item.id)).status_code == 204

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_missing_row_is_404(self, admin_client, method):
        kwargs = {"json": {"reason": "r"}} if method == "put" else {}
        response = getattr(admin_client, method)(_pe_url(999_999_999), **kwargs)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "position_item_not_found"

    def _expect_not_applicable(self, admin_client, item):
        response = admin_client.put(_pe_url(item.id), json={"reason": "r"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "not_analog_not_applicable"

    def test_kp_estimate_is_not_applicable(self, scene, admin_client):
        _, proposal = scene.offer_estimate()
        self._expect_not_applicable(admin_client, scene.line(proposal))

    def test_round_estimate_is_not_applicable(self, scene, admin_client):
        _, proposal = scene.round_estimate()
        self._expect_not_applicable(admin_client, scene.line(proposal))

    def test_chapter_is_not_applicable(self, scene, admin_client):
        item = _contract_item(scene)
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == item.id).values(is_chapter=True)
        )
        self._expect_not_applicable(admin_client, item)

    def test_system_context_is_not_applicable(self, scene, admin_client):
        item = _contract_item(scene)
        scene.db.execute(sa.update(CatalogContext).values(semantic_kind="SYSTEM"))
        self._expect_not_applicable(admin_client, item)

    def test_applicable_control_without_membership(self, scene, admin_client):
        item = _contract_item(scene)
        scene.db.execute(sa.delete(ContextMember).where(ContextMember.position_item_id == item.id))
        assert admin_client.put(_pe_url(item.id), json={"reason": "r"}).status_code == 200


class TestTenderMark:
    def test_writes_mark_on_the_form_key_of_the_kp_line(self, scene, admin_client):
        kp = _kp(scene, title="  Устройство   Стяжки ")
        item = _contract_item(scene)
        response = admin_client.put(_na_url(kp, item.id), json={"reason": "другой материал"})
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "tender" and body["reason"] == "другой материал"
        assert body["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
        (row,) = _na_rows(scene)
        offer = kp["estimate"].offer
        assert row.tender_id == offer.round.tender_id
        assert row.form_title == normalize_title("Устройство Стяжки")
        assert row.form_unit_id == kp["item"].unit_id
        assert row.position_item_id == item.id
        assert dt.datetime.fromisoformat(body["set_at"]) == row.set_at
        marks = load_not_analog_marks(scene.db, row.tender_id)
        assert marks[(row.form_title, row.form_unit_id)][item.id].reason == "другой материал"

    def test_second_kp_line_with_the_same_key_is_one_record(self, scene, admin_client):
        first = _kp(scene, title="Стяжка")
        second = _kp(scene, title="  стяжка ", offer_estimate=(first["estimate"], first["proposal"]))
        scene.db.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == second["item"].id)
            .values(unit_id=first["item"].unit_id)
        )
        scene.db.flush()
        item = _contract_item(scene)
        one = admin_client.put(_na_url(first, item.id), json={"reason": "r"}).json()
        two = admin_client.put(_na_url(second, item.id), json={"reason": "r"})
        assert two.status_code == 200 and two.json() == one
        assert len(_na_rows(scene)) == 1

    def test_same_reason_keeps_author_changed_reason_replaces_it(self, scene, admin_client, factories):
        kp = _kp(scene)
        item = _contract_item(scene)
        first = admin_client.put(_na_url(kp, item.id), json={"reason": "r"}).json()
        other = factories.UserFactory.create(role=UserRole.admin)
        admin_client.set_user(other)
        assert admin_client.put(_na_url(kp, item.id), json={"reason": "r"}).json() == first
        changed = admin_client.put(_na_url(kp, item.id), json={"reason": "новая"}).json()
        assert changed["reason"] == "новая" and changed["set_by"]["id"] == other.id
        assert len(_na_rows(scene)) == 1

    @pytest.mark.parametrize("reason", ["", "  ", "x" * 301])
    def test_bad_reason_is_a_validation_error(self, scene, admin_client, reason):
        kp = _kp(scene)
        item = _contract_item(scene)
        assert admin_client.put(_na_url(kp, item.id), json={"reason": reason}).status_code == 422
        assert _na_rows(scene) == []

    def test_reason_of_300_is_accepted(self, scene, admin_client):
        kp = _kp(scene)
        item = _contract_item(scene)
        assert admin_client.put(_na_url(kp, item.id), json={"reason": "x" * 300}).status_code == 200

    def test_delete_removes_and_without_record_is_204(self, scene, admin_client):
        kp = _kp(scene)
        item = _contract_item(scene)
        admin_client.put(_na_url(kp, item.id), json={"reason": "r"})
        assert admin_client.delete(_na_url(kp, item.id)).status_code == 204
        assert _na_rows(scene) == []
        assert admin_client.delete(_na_url(kp, item.id)).status_code == 204

    # Отказ откатывает сессию теста (она общая с маршрутом), поэтому на каждый вызов — своя сцена.
    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_missing_contract_row_is_404(self, scene, admin_client, method):
        kp = _kp(scene)
        kwargs = {"json": {"reason": "r"}} if method == "put" else {}
        response = getattr(admin_client, method)(_na_url(kp, 999_999_999), **kwargs)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "position_item_not_found"

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_kp_line_outside_the_offer_is_404(self, scene, admin_client, method):
        kp = _kp(scene)
        stranger = _kp(scene)
        item = _contract_item(scene)
        url = _na_url(kp, item.id).replace(
            f"/lines/{kp['item'].id}/", f"/lines/{stranger['item'].id}/"
        )
        kwargs = {"json": {"reason": "r"}} if method == "put" else {}
        response = getattr(admin_client, method)(url, **kwargs)
        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)  # существующий 404 строки, без кода
        assert _na_rows(scene) == []

    def _expect_not_applicable(self, admin_client, kp, item):
        response = admin_client.put(_na_url(kp, item.id), json={"reason": "r"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "not_analog_not_applicable"

    def test_kp_estimate_row_is_not_applicable(self, scene, admin_client):
        kp = _kp(scene)
        _, proposal = scene.offer_estimate()
        self._expect_not_applicable(admin_client, kp, scene.line(proposal))

    def test_chapter_is_not_applicable(self, scene, admin_client):
        kp = _kp(scene)
        item = _contract_item(scene)
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == item.id).values(is_chapter=True)
        )
        self._expect_not_applicable(admin_client, kp, item)

    def test_system_context_is_not_applicable(self, scene, admin_client):
        kp = _kp(scene)
        item = _contract_item(scene)
        scene.db.execute(
            sa.update(CatalogContext)
            .where(
                CatalogContext.id.in_(
                    sa.select(ContextMember.context_id).where(ContextMember.position_item_id == item.id)
                )
            )
            .values(semantic_kind="SYSTEM")
        )
        self._expect_not_applicable(admin_client, kp, item)

    def test_line_without_variant_zero_price_and_kit_are_accepted(self, scene, admin_client):
        kp = _kp(scene)
        zero = _contract_item(scene, price=D("0"))
        kit = _contract_item(scene)
        scene.override(kit, "lump")
        for item in (zero, kit):
            assert admin_client.put(_na_url(kp, item.id), json={"reason": "r"}).status_code == 200

    def test_row_is_locked_for_update(self, scene, admin_client):
        kp = _kp(scene)
        item = _contract_item(scene)
        statements: list[str] = []

        def spy(conn, cursor, statement, *args):
            statements.append(statement)

        engine = scene.db.get_bind().engine
        sa.event.listen(engine, "before_cursor_execute", spy)
        try:
            admin_client.put(_na_url(kp, item.id), json={"reason": "r"})
        finally:
            sa.event.remove(engine, "before_cursor_execute", spy)
        assert any("FROM position_items" in s and "FOR UPDATE" in s for s in statements)


class TestMember:
    def test_member_is_forbidden_on_all_four_routes(self, db_session, factories, member_client):
        scene = Scene(db_session, factories, factories.UserFactory.create(role=UserRole.admin))
        kp = _kp(scene)
        item = _contract_item(scene)
        assert member_client.put(_pe_url(item.id), json={"reason": "r"}).status_code == 403
        assert member_client.delete(_pe_url(item.id)).status_code == 403
        assert member_client.put(_na_url(kp, item.id), json={"reason": "r"}).status_code == 403
        assert member_client.delete(_na_url(kp, item.id)).status_code == 403
        assert _pe_row(scene, item) is None and _na_rows(scene) == []

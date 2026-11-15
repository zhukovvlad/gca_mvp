"""Цель строки-работы: выбор аналога, мутации `PUT|DELETE …/target`, источник комментария и файла
(спека лестницы аналогов §2.14, §2.15, §2.17, §2.24).

Сцены строятся помощниками `test_analog_scene` (цепочка с зоной, строки истории с датой подготовки сметы).
Названия и числа выдуманы. Каждое правило имеет обе стороны: вход, где оно срабатывает, и соседний,
нарушающий ровно одно условие. Состояние `kp_line_targets` читается столбцами: ответ роутера пишет в
обход identity map.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import kp_decisions as kd
from crud import tenders as crud_tenders
from models import CatalogContext, KpLineTarget, PositionItem, User, UserRole
from tests import comparison_fixtures as fx
from tests.integration.test_analog_scene import _hist, _kp_part, _line
from tests.integration.test_kp_file_scene import _agree
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    TOLERANCE,
    _inflation_scene,
    _json,
    _line_of,
    _ok,
    _pair,
    _panel,
    _panel_get,
    _second_variant,
)
from tests.integration.test_our_price_offer_api import _history as _sys_history
from tests.integration.test_our_price_offer_api import _put as _sys_line
from tests.integration.test_our_price_round_api import SERIES_NAME, Stage
from tests.integration.test_our_price_round_api import _get as _round_get

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def admin_client(admin_client, db_session):
    """Клиент администратора, который перед каждым запросом закрепляет сцену: `rollback` роутера
    откатывает транзакцию сессии, а в проде сцена к запросу уже закоммичена."""
    send = admin_client.request

    def request(*args, **kwargs):
        db_session.commit()
        return send(*args, **kwargs)

    admin_client.request = request
    return admin_client


# ---------------------------------------------------------------------------
#  Сцены и помощники
# ---------------------------------------------------------------------------


@dataclass
class Scene:
    rnd: object
    part: object
    item: object
    #: низкая 100 (2025-01), средняя 120 (2026-02), свежая 150 (2026-08), выброс 900 (2026-05)
    ids: dict[str, int]
    chain: object
    contracts: dict[str, object]


def _scene(stage, world, *, kp=D("160")) -> Scene:
    """Четыре аналога одной ступени; цена КП `kp`; этап 2026-08, участник без ряда ИПЦ."""
    chain = world.chain(("Раздел А",), "whole")
    rnd, part = _kp_part(stage)
    ids, contracts = {}, {}
    spec = [
        ("low", D("100"), dt.date(2025, 1, 1)),
        ("mid", D("120"), dt.date(2026, 2, 1)),
        ("fresh", D("150"), dt.date(2026, 8, 1)),
        ("out", D("900"), dt.date(2026, 5, 1)),
    ]
    for key, cost, prepared in spec:
        contract, _, item = _hist(stage, cost, chain=chain, prepared=prepared)
        ids[key], contracts[key] = item.id, contract
    item = _line(stage, part.proposal, kp, chain=chain)
    stage.db.flush()
    return Scene(rnd, part, item, ids, chain, contracts)


def _target_url(stage, scene, item=None, *, tender=None, round_=None, offer=None) -> str:
    return (
        f"/api/v1/tenders/{(tender or stage.tender).id}/rounds/{(round_ or scene.rnd).id}"
        f"/our-price/offers/{(offer or scene.part.offer).id}/lines/{getattr(item or scene.item, 'id', item)}/target"
    )


def _put(client, stage, scene, body, *, item=None, **params):
    return client.put(_target_url(stage, scene, item), json=body, params=params)


def _rows(db, item=None):
    query = sa.select(KpLineTarget.kind, KpLineTarget.analog_position_item_id)
    if item is not None:
        query = query.where(KpLineTarget.position_item_id == item.id)
    return db.execute(query).all()


def _code(response) -> str:
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail["code"]


def _save(db, item, kind, analog_id, user_id):
    """Выбор в обход маршрута: для сцен, где запись уже не прошла бы проверку."""
    db.add(KpLineTarget(position_item_id=item.id, kind=kind, analog_position_item_id=analog_id, set_by=user_id))
    db.commit()


def _preview(client, stage, scene, **params) -> dict:
    url = f"/api/v1/tenders/{stage.tender.id}/rounds/{scene.rnd.id}/our-price/offers/{scene.part.offer.id}/export-preview"
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return _json(response)


def _preview_row(body: dict, item) -> dict:
    (row,) = (r for r in body["rows"] if r.get("position_item_id") == item.id)
    return row


def _is_admin(db, user_id) -> bool:
    return db.get(User, user_id).role == UserRole.admin


def _tiles(client, stage, scene, **params):
    """Суммы и плитки, которые выбор цели менять не вправе: этап и участник целиком, без строк."""
    offer = _ok(client, stage.tender, scene.rnd, scene.part, **params)
    rnd = _round_get(client, stage.tender, scene.rnd, **params)
    assert rnd.status_code == 200
    return offer, _json(rnd)


def _without_target_fields(offer: dict) -> dict:
    """Ответ участника без полей, которые выбор цели вправе менять."""
    copy = {**offer, "lines": [{k: v for k, v in line.items() if k not in ("target_kind", "choice")} for line in offer["lines"]]}
    return copy


# ---------------------------------------------------------------------------
#  Запись выбора
# ---------------------------------------------------------------------------


class TestPutTarget:
    def test_freshest_is_204_with_no_body_and_one_row(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)

        response = _put(admin_client, stage, scene, {"kind": "freshest"})

        assert (response.status_code, response.content) == (204, b"")
        assert _rows(db_session, scene.item) == [("freshest", None)]

    def test_the_row_is_signed_by_the_admin_who_wrote_it(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        choice = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]["choice"]
        (set_by,) = db_session.execute(sa.select(KpLineTarget.set_by)).scalars()

        assert choice["set_by"]["id"] == set_by
        assert _is_admin(db_session, set_by)

    def test_an_analog_is_204_and_the_row_names_the_analog_line(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)

        response = _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["mid"]})

        assert response.status_code == 204
        assert _rows(db_session, scene.item) == [("analog", scene.ids["mid"])]

    def test_the_last_write_wins_on_one_row(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["mid"]}).status_code == 204

        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204
        assert _rows(db_session, scene.item) == [("freshest", None)]

        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["low"]}).status_code == 204
        assert _rows(db_session, scene.item) == [("analog", scene.ids["low"])]

    @pytest.mark.parametrize("which", ["out", "unknown", "foreign_family_line"])
    def test_an_analog_outside_the_remainder_is_422_analog_unavailable_and_writes_nothing(
        self, admin_client, stage, world, db_session, which
    ):
        scene = _scene(stage, world)
        analog_id = {"out": scene.ids["out"], "unknown": 10**9, "foreign_family_line": scene.item.id}[which]

        response = _put(admin_client, stage, scene, {"kind": "analog", "analog_id": analog_id})

        assert (response.status_code, _code(response)) == (422, "analog_unavailable")
        assert _rows(db_session) == []

    def test_an_analog_of_a_rung_the_line_is_not_on_is_unavailable(self, admin_client, stage, world, db_session, factories):
        scene = _scene(stage, world)
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        _, _, foreign = _hist(stage, D("90"), chain=scene.chain, rate_class=other_class)
        db_session.flush()

        response = _put(admin_client, stage, scene, {"kind": "analog", "analog_id": foreign.id})

        assert (response.status_code, _code(response)) == (422, "analog_unavailable")

    def test_freshest_without_a_dated_analog_is_422_no_freshest(self, admin_client, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()
        scene = Scene(rnd, part, item, {}, chain, {})

        response = _put(admin_client, stage, scene, {"kind": "freshest"})

        assert (response.status_code, _code(response)) == (422, "no_freshest")
        assert _rows(db_session) == []

    def test_an_analog_choice_is_still_allowed_when_there_is_no_freshest(self, admin_client, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _, _, analog = _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()
        scene = Scene(rnd, part, item, {}, chain, {})

        response = _put(admin_client, stage, scene, {"kind": "analog", "analog_id": analog.id})

        assert response.status_code == 204

    def test_a_system_line_is_422_target_not_applicable(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        system = _line(stage, scene.part.proposal, D("160"), chain=scene.chain, kind="SYSTEM")
        db_session.flush()

        response = _put(admin_client, stage, scene, {"kind": "freshest"}, item=system)

        assert (response.status_code, _code(response)) == (422, "target_not_applicable")
        assert _rows(db_session) == []

    def test_a_comparable_system_with_a_freshest_is_still_422_target_not_applicable(
        self, admin_client, stage, world, db_session
    ):
        """Система сравнима и самая свежая у неё есть: отказ держит только условие «строка — работа»,
        а не «строка `comparable`» (у системы предыдущего теста истории нет)."""
        for total, prepared in (("100000", dt.date(2025, 1, 10)), ("90000", dt.date(2026, 6, 15))):
            _, estimate, _ = _sys_history(stage, D("1"), total=D(total), kind="SYSTEM")
            estimate.data_prepared_on_date = prepared
        rnd, part = _kp_part(stage)
        system = _sys_line(stage, part.proposal, "R", D("1"), total=D("95000"), kind="SYSTEM")
        db_session.flush()
        scene = Scene(rnd, part, system, {}, None, {})
        line = _line_of(_ok(admin_client, stage.tender, rnd, part), system)
        assert (line["kind"], line["state"]) == ("system", "comparable")
        assert line["freshest"] is not None

        response = _put(admin_client, stage, scene, {"kind": "freshest"})

        assert (response.status_code, _code(response)) == (422, "target_not_applicable")
        assert _rows(db_session) == []

    @pytest.mark.parametrize("why", ["no_history", "unit_mismatch", "no_price"])
    def test_a_work_that_is_not_comparable_is_422_target_not_applicable(
        self, admin_client, stage, world, db_session, why
    ):
        scene = _scene(stage, world)
        if why == "no_history":
            item = _line(stage, scene.part.proposal, D("160"), chain=scene.chain, variant=_second_variant(stage))
        elif why == "unit_mismatch":
            item = _line(stage, scene.part.proposal, D("5000"), chain=scene.chain)
        else:
            item = _line(stage, scene.part.proposal, D("0"), chain=scene.chain)
        db_session.flush()

        response = _put(admin_client, stage, scene, {"kind": "freshest"}, item=item)

        assert (response.status_code, _code(response)) == (422, "target_not_applicable")
        assert _rows(db_session) == []

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "analog"},
            {"kind": "freshest", "analog_id": 1},
            {"kind": "lowest"},
            {},
        ],
    )
    def test_a_body_that_does_not_fit_the_kind_is_422_and_writes_nothing(self, admin_client, stage, world, db_session, body):
        scene = _scene(stage, world)

        response = _put(admin_client, stage, scene, body)

        assert response.status_code == 422
        assert _rows(db_session) == []


class TestDeleteTarget:
    def test_delete_removes_the_row_and_returns_the_line_to_the_lowest(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        response = admin_client.delete(_target_url(stage, scene))

        assert (response.status_code, response.content) == (204, b"")
        assert _rows(db_session) == []
        line = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]
        assert (line["target_kind"], line["choice"]) == ("lowest", None)

    def test_delete_without_a_row_is_the_same_204(self, admin_client, stage, world):
        scene = _scene(stage, world)

        assert admin_client.delete(_target_url(stage, scene)).status_code == 204

    def test_delete_leaves_the_choice_of_another_line_alone(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        neighbour = _line(stage, scene.part.proposal, D("155"), chain=scene.chain)
        db_session.flush()
        assert _put(admin_client, stage, scene, {"kind": "freshest"}, item=neighbour).status_code == 204

        assert admin_client.delete(_target_url(stage, scene)).status_code == 204
        assert len(_rows(db_session, neighbour)) == 1


class TestRightsAndOwnership:
    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_member_is_forbidden_and_writes_nothing(self, member_client, stage, world, db_session, method):
        scene = _scene(stage, world)
        url = _target_url(stage, scene)

        response = member_client.delete(url) if method == "DELETE" else member_client.put(url, json={"kind": "freshest"})

        assert response.status_code == 403
        assert _rows(db_session) == []

    def test_member_delete_does_not_remove_an_existing_choice(self, member_client, stage, world, db_session, admin_user):
        scene = _scene(stage, world)
        _save(db_session, scene.item, "freshest", None, admin_user.id)

        assert member_client.delete(_target_url(stage, scene)).status_code == 403
        assert len(_rows(db_session, scene.item)) == 1

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_a_line_of_another_participant_is_404(self, admin_client, stage, world, db_session, method):
        scene = _scene(stage, world)
        neighbour = stage.participant(scene.rnd, "Подрядчик Б", vat=D("0"))
        foreign = _line(stage, neighbour.proposal, D("160"), chain=scene.chain)
        db_session.flush()
        url = _target_url(stage, scene, foreign)

        response = admin_client.delete(url) if method == "DELETE" else admin_client.put(url, json={"kind": "freshest"})

        assert response.status_code == 404
        assert _rows(db_session) == []

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_a_chapter_is_404(self, admin_client, stage, world, db_session, method):
        scene = _scene(stage, world)
        chapter = db_session.execute(
            sa.select(PositionItem.id).where(
                PositionItem.proposal_id == scene.part.proposal.id, PositionItem.is_chapter.is_(True)
            )
        ).scalar_one()
        url = _target_url(stage, scene, chapter)

        response = admin_client.delete(url) if method == "DELETE" else admin_client.put(url, json={"kind": "freshest"})

        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    @pytest.mark.parametrize("foreign", ["tender", "round"])
    def test_a_foreign_tender_or_round_is_404(self, admin_client, stage, world, db_session, factories, method, foreign):
        scene = _scene(stage, world)
        kwargs = (
            {"tender": factories.TenderFactory.create()}
            if foreign == "tender"
            else {"round_": factories.TenderRoundFactory.create()}
        )
        url = _target_url(stage, scene, **kwargs)

        response = admin_client.delete(url) if method == "DELETE" else admin_client.put(url, json={"kind": "freshest"})

        assert response.status_code == 404


# ---------------------------------------------------------------------------
#  Что выбор меняет в ответах чтения
# ---------------------------------------------------------------------------


class TestReads:
    def test_without_a_choice_the_target_is_the_lowest_and_there_is_no_choice(self, member_client, stage, world):
        scene = _scene(stage, world)

        line = _panel(member_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        assert (line["target_kind"], line["choice"]) == ("lowest", None)
        assert _pair(line["file_ours"]) == (D("100"), D("100"))

    @pytest.mark.parametrize(
        ("body", "kind", "net"),
        [
            ({"kind": "freshest"}, "freshest", D("150")),
            ({"kind": "analog", "analog": "mid"}, "analog", D("120")),
            ({"kind": "analog", "analog": "low"}, "analog", D("100")),
        ],
    )
    def test_an_acting_choice_sets_the_target_kind_and_the_file_ours(
        self, admin_client, stage, world, body, kind, net
    ):
        scene = _scene(stage, world)
        payload = {"kind": body["kind"]}
        if "analog" in body:
            payload["analog_id"] = scene.ids[body["analog"]]
        assert _put(admin_client, stage, scene, payload).status_code == 204

        line = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        assert line["target_kind"] == kind
        assert _pair(line["file_ours"]) == (net, net)
        assert line["choice"]["kind"] == kind
        assert line["choice"]["analog_id"] == payload.get("analog_id")
        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (True, None)

    def test_the_choice_carries_the_author_and_the_time_in_the_panel_and_the_offer_row(
        self, admin_client, stage, world, db_session
    ):
        scene = _scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]
        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        for line in (panel, row):
            author = line["choice"]["set_by"]
            assert _is_admin(db_session, author["id"])
            assert author["email"] == db_session.get(User, author["id"]).email
            assert isinstance(line["choice"]["set_at"], str)
        assert (row["target_kind"], row["choice"]) == (panel["target_kind"], panel["choice"])

    def test_the_agreed_price_stays_the_target_over_a_choice(self, admin_client, stage, world, db_session, admin_user):
        scene = _scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204
        _agree(db_session, scene.item, D("90"), admin_user)
        db_session.commit()

        line = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        assert line["target_kind"] == "agreed"
        assert line["choice"]["active"] is True

    def test_the_choice_changes_the_comment_the_source_and_the_preview_of_the_file(
        self, admin_client, stage, world, db_session
    ):
        scene = _scene(stage, world)
        before_panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        before_row = _preview_row(_preview(admin_client, stage, scene), scene.item)
        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["mid"]}).status_code == 204

        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        row = _preview_row(_preview(admin_client, stage, scene), scene.item)

        (mid,) = (a for a in panel["analogs"] if a["analog_id"] == scene.ids["mid"])
        (low,) = (a for a in panel["analogs"] if a["analog_id"] == scene.ids["low"])
        assert "эта работа стоила 100,00 ₽" in before_panel["comment"]["text"]
        assert "эта работа стоила 120,00 ₽" in panel["comment"]["text"]
        assert "Просим снизить до 120,00 ₽" in panel["comment"]["text"]
        assert row["comment"] == panel["comment"]["text"]
        assert D(row["ours"]) == D("120") and D(before_row["ours"]) == D("100")
        assert row["source"] == f"{mid['object_title']}, договор № {mid['number']}, {mid['year']} г.: 120,00 ₽ за {row['unit']} без НДС"
        assert before_row["source"].startswith(f"{low['object_title']}, договор № {low['number']}")
        assert row["deviation_pct"] != before_row["deviation_pct"]

    def test_freshest_below_the_kp_price_leaves_no_comment_when_the_kp_is_between_the_two(
        self, admin_client, stage, world, db_session
    ):
        scene = _scene(stage, world, kp=D("130"))
        before = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["comment"]
        assert before["kind"] == "template"
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        comment = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["comment"]
        row = _preview_row(_preview(admin_client, stage, scene), scene.item)

        assert comment["kind"] is None and comment["text"] is None
        assert (row["comment"], row["comment_kind"]) == (None, None)
        assert row["deviation_pct"].startswith("-")

    def test_the_inflation_column_and_the_header_years_follow_the_chosen_analog(
        self, admin_client, stage, world, db_session
    ):
        series_id = fx.series_with_years(db_session, SERIES_NAME, {2025: "1.100", 2026: "1.100"})
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _, _, old = _hist(stage, D("80"), chain=chain, prepared=dt.date(2024, 12, 1))
        _, _, new = _hist(stage, D("110"), chain=chain, prepared=dt.date(2026, 8, 1))
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()
        scene = Scene(rnd, part, item, {"old": old.id, "new": new.id}, chain, {})
        params = {"inflation_series_id": series_id}

        lowest_row = _preview_row(_preview(admin_client, stage, scene, **params), item)
        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": new.id}, **params).status_code == 204
        chosen = _preview(admin_client, stage, scene, **params)
        chosen_row = _preview_row(chosen, item)

        assert lowest_row["inflation"] is not None
        assert chosen_row["inflation"] is None
        assert chosen["header"]["inflation_years"] == []


# ---------------------------------------------------------------------------
#  К7: выбор цели не меняет суммы и плитки
# ---------------------------------------------------------------------------


class TestChoiceDoesNotMoveTheTotals:
    @pytest.mark.parametrize("choice", ["freshest", "mid", "low"])
    def test_the_tiles_and_every_amount_are_equal_before_and_after_the_put(
        self, admin_client, stage, world, db_session, choice
    ):
        """К7: суммы и плитки (этап и участник) побайтно равны; меняются только `target_kind`, `choice`,
        `file_ours`, предпросмотр файла и комментарий."""
        scene = _scene(stage, world, kp=D("160"))
        # вторая строка: цена КП между самой низкой и самой свежей, чтобы выбор менял и её комментарий
        second = _line(stage, scene.part.proposal, D("130"), chain=scene.chain)
        db_session.flush()
        offer_before, round_before = _tiles(admin_client, stage, scene)
        preview_before = _preview(admin_client, stage, scene)
        body = {"kind": "freshest"} if choice == "freshest" else {"kind": "analog", "analog_id": scene.ids[choice]}

        assert _put(admin_client, stage, scene, body).status_code == 204
        offer_after, round_after = _tiles(admin_client, stage, scene)
        preview_after = _preview(admin_client, stage, scene)

        assert round_after == round_before
        assert offer_after["participants"] == offer_before["participants"]
        assert offer_after["articles"] == offer_before["articles"]
        assert offer_after["total"] == offer_before["total"]
        assert _without_target_fields(offer_after) == _without_target_fields(offer_before)
        changed_line = _line_of(offer_after, scene.item)
        assert changed_line["target_kind"] == ("freshest" if choice == "freshest" else "analog")
        assert _line_of(offer_before, scene.item)["target_kind"] == "lowest"
        assert _line_of(offer_after, second)["target_kind"] == "lowest"
        if choice != "low":
            assert _preview_row(preview_after, scene.item)["ours"] != _preview_row(preview_before, scene.item)["ours"]
            assert _preview_row(preview_after, scene.item)["source"] != _preview_row(preview_before, scene.item)["source"]
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        assert panel["line"]["lowest"] == _line_of(offer_before, scene.item)["lowest"]
        assert panel["line"]["to_lowest"] == _line_of(offer_before, scene.item)["to_lowest"]

    def test_the_second_line_comment_changes_with_the_freshest_target_but_not_its_amounts(
        self, admin_client, stage, world, db_session
    ):
        scene = _scene(stage, world)
        second = _line(stage, scene.part.proposal, D("130"), chain=scene.chain)
        db_session.flush()
        offer_before = _ok(admin_client, stage.tender, scene.rnd, scene.part)
        assert _line_of(offer_before, second)["comment_kind"] == "template"

        assert _put(admin_client, stage, scene, {"kind": "freshest"}, item=second).status_code == 204
        offer_after = _ok(admin_client, stage.tender, scene.rnd, scene.part)

        line_before, line_after = _line_of(offer_before, second), _line_of(offer_after, second)
        assert line_after["comment_kind"] is None
        for key in ("to_lowest", "over_lowest", "over_freshest", "below", "lowest", "freshest", "state"):
            assert line_after[key] == line_before[key]
        assert offer_after["participants"] == offer_before["participants"]


# ---------------------------------------------------------------------------
#  Ленивый сброс: запись остаётся, расчёт идёт на самой низкой
# ---------------------------------------------------------------------------


class TestLazyReset:
    def _baseline(self, client, stage, scene):
        return _without_target_fields(_ok(client, stage.tender, scene.rnd, scene.part))

    def test_analog_unavailable_for_an_outlier_keeps_the_record_and_the_lowest(
        self, admin_client, stage, world, db_session, admin_user
    ):
        scene = _scene(stage, world)
        baseline = self._baseline(admin_client, stage, scene)
        _save(db_session, scene.item, "analog", scene.ids["out"], admin_user.id)

        offer = _ok(admin_client, stage.tender, scene.rnd, scene.part)
        line = _line_of(offer, scene.item)

        assert line["choice"]["active"] is False
        assert line["choice"]["reset_reason"] == "analog_unavailable"
        assert (line["choice"]["kind"], line["choice"]["analog_id"]) == ("analog", scene.ids["out"])
        assert line["target_kind"] == "lowest"
        assert _without_target_fields(offer) == baseline
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]
        assert _pair(panel["file_ours"]) == (D("100"), D("100"))
        assert len(_rows(db_session, scene.item)) == 1

    def test_an_analog_whose_line_left_with_the_estimate_replacement_is_200_analog_unavailable(
        self, admin_client, stage, world, db_session, admin_user
    ):
        """Review Focus 4: строка договора ушла с заменой сметы — ответ 200, а не 500 и не 404."""
        scene = _scene(stage, world)
        _save(db_session, scene.item, "analog", scene.ids["mid"], admin_user.id)
        assert _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)["choice"]["active"] is True
        replacement, proposal = world.estimate(scene.contracts["mid"], amendment_no=1, vat_rate=D("0"))
        replacement.data_prepared_on_date = dt.date(2026, 3, 1)
        _line(stage, proposal, D("125"), chain=scene.chain)
        db_session.commit()

        response = admin_client.get(
            f"/api/v1/tenders/{stage.tender.id}/rounds/{scene.rnd.id}/our-price/offers/{scene.part.offer.id}"
        )
        line = _line_of(_json(response), scene.item)

        assert response.status_code == 200
        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "analog_unavailable")
        assert line["target_kind"] == "lowest"
        panel_response = _panel_get(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        assert panel_response.status_code == 200

    def test_a_work_that_became_a_system_is_kind_changed(self, admin_client, stage, world, db_session, admin_user):
        """Review Focus 6: строка-работа, ставшая системой, не действует с `kind_changed`."""
        scene = _scene(stage, world)
        _save(db_session, scene.item, "freshest", None, admin_user.id)
        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == scene.item.context_id).values(semantic_kind="SYSTEM")
        )
        db_session.commit()

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert line["kind"] == "system"
        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "kind_changed")
        assert len(_rows(db_session, scene.item)) == 1

    def test_a_line_that_stopped_being_comparable_is_not_comparable_and_has_no_target(
        self, admin_client, stage, world, db_session, admin_user
    ):
        scene = _scene(stage, world)
        _save(db_session, scene.item, "freshest", None, admin_user.id)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == scene.item.id)
            .values(unit_cost_total=D("5000"), total_cost_total=D("50000"))
        )
        db_session.commit()

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert line["state"] == "unit_mismatch"
        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "not_comparable")
        assert line["target_kind"] is None

    def test_freshest_without_a_freshest_figure_is_no_freshest(
        self, admin_client, stage, world, db_session, admin_user
    ):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()
        scene = Scene(rnd, part, item, {}, chain, {})
        _save(db_session, item, "freshest", None, admin_user.id)

        line = _line_of(_ok(admin_client, stage.tender, rnd, part), item)

        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "no_freshest")
        assert line["target_kind"] == "lowest"
        assert scene.ids == {}

    def test_the_record_acts_again_once_the_condition_is_back(self, admin_client, stage, world, db_session, admin_user):
        scene = _scene(stage, world)
        _save(db_session, scene.item, "analog", scene.ids["mid"], admin_user.id)
        original_price = db_session.execute(
            sa.select(PositionItem.unit_cost_total).where(PositionItem.id == scene.item.id)
        ).scalar_one()
        db_session.execute(
            sa.update(PositionItem).where(PositionItem.id == scene.item.id).values(unit_cost_total=D("5000"), total_cost_total=D("50000"))
        )
        db_session.commit()
        assert _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)["choice"]["active"] is False
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == scene.item.id)
            .values(unit_cost_total=original_price, total_cost_total=original_price * 10)
        )
        db_session.commit()

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert (line["choice"]["active"], line["target_kind"]) == (True, "analog")


# ---------------------------------------------------------------------------
#  К8: ряд ИПЦ запроса в обе стороны
# ---------------------------------------------------------------------------


def _ipc_scene(stage, world, db_session, *, first_nominal, factor, others=("100", "110")):
    """Три аналога: смета первого на год старше (множитель `factor`), смет остальных — с периодом этапа.

    Этап 2026-12; ряд — один год 2026 с коэффициентом `factor`: у сметы 2025-12 к 2026-12 один целый год,
    у остальных периоды совпадают с целью, множитель 1. Возвращает `(Scene, series_id)`.
    """
    chain = world.chain(("Раздел А",), "whole")
    rnd = stage.round(held_on=dt.date(2026, 12, 15))
    part = stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=dt.date(2026, 12, 1))
    ids, contracts = {}, {}
    for key, nominal, prepared in (
        ("first", D(first_nominal), dt.date(2025, 12, 1)),
        ("b", D(others[0]), dt.date(2026, 12, 1)),
        ("c", D(others[1]), dt.date(2026, 12, 1)),
    ):
        contract, _, item = _hist(stage, nominal, chain=chain, prepared=prepared)
        ids[key], contracts[key] = item.id, contract
    item = _line(stage, part.proposal, D("120"), chain=chain)
    db_session.flush()
    series_id = fx.series_with_years(db_session, SERIES_NAME, {2026: factor})
    return Scene(rnd, part, item, ids, chain, contracts), series_id


def _analog_row(panel: dict, analog_id: int) -> dict:
    (row,) = (a for a in panel["analogs"] if a["analog_id"] == analog_id)
    return row


class TestSeriesOfTheRequest:
    """К8: остаток от приведения зависит от ряда в обе стороны, и проверка записи идёт с рядом запроса."""

    def test_forward_an_analog_that_is_an_outlier_without_the_series_is_in_the_remainder_with_it(
        self, admin_client, stage, world, db_session
    ):
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal="10", factor="3")
        first = scene.ids["first"]
        plain = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        adjusted = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item, inflation_series_id=series_id)

        assert _analog_row(plain, first)["selectable"] is False
        assert _analog_row(plain, first)["outlier_ratio"] is not None
        assert _analog_row(adjusted, first)["selectable"] is True
        assert abs(D(_analog_row(adjusted, first)["price"]["net"]) - D("30")) < TOLERANCE

    def test_forward_put_with_the_series_is_204_and_without_it_is_422_analog_unavailable(
        self, admin_client, stage, world, db_session
    ):
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal="10", factor="3")
        first = {"kind": "analog", "analog_id": scene.ids["first"]}

        refused = _put(admin_client, stage, scene, first)
        assert (refused.status_code, _code(refused)) == (422, "analog_unavailable")
        assert _rows(db_session) == []

        accepted = _put(admin_client, stage, scene, first, inflation_series_id=series_id)
        assert accepted.status_code == 204
        assert _rows(db_session, scene.item) == [("analog", scene.ids["first"])]

    def test_forward_the_read_acts_with_the_series_and_resets_without_it_and_acts_again(
        self, admin_client, stage, world, db_session
    ):
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal="10", factor="3")
        first = scene.ids["first"]
        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": first}, inflation_series_id=series_id).status_code == 204

        with_series = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item, inflation_series_id=series_id)["line"]
        without = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]
        again = _line_of(
            _ok(admin_client, stage.tender, scene.rnd, scene.part, inflation_series_id=series_id), scene.item
        )

        assert (with_series["target_kind"], with_series["choice"]["active"]) == ("analog", True)
        assert abs(D(with_series["file_ours"]["net"]) - D("30")) < TOLERANCE
        assert (without["target_kind"], without["choice"]["active"], without["choice"]["reset_reason"]) == (
            "lowest",
            False,
            "analog_unavailable",
        )
        assert (again["target_kind"], again["choice"]["active"]) == ("analog", True)
        assert len(_rows(db_session, scene.item)) == 1

    def test_mirror_an_analog_in_the_remainder_without_the_series_is_an_outlier_with_it(
        self, admin_client, stage, world, db_session
    ):
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal="100", factor="6")
        first = {"kind": "analog", "analog_id": scene.ids["first"]}
        plain = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        adjusted = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item, inflation_series_id=series_id)

        assert _analog_row(plain, scene.ids["first"])["selectable"] is True
        assert _analog_row(adjusted, scene.ids["first"])["selectable"] is False

        accepted = _put(admin_client, stage, scene, first)
        assert accepted.status_code == 204
        refused = _put(admin_client, stage, scene, first, inflation_series_id=series_id)
        assert (refused.status_code, _code(refused)) == (422, "analog_unavailable")
        # отказ не тронул прежнюю запись
        assert _rows(db_session, scene.item) == [("analog", scene.ids["first"])]

    def test_mirror_the_read_resets_with_the_series_and_acts_again_without_it(
        self, admin_client, stage, world, db_session
    ):
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal="100", factor="6")
        assert _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["first"]}).status_code == 204

        with_series = _line_of(
            _ok(admin_client, stage.tender, scene.rnd, scene.part, inflation_series_id=series_id), scene.item
        )
        without = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert (with_series["choice"]["active"], with_series["choice"]["reset_reason"]) == (False, "analog_unavailable")
        assert (without["choice"]["active"], without["target_kind"]) == (True, "analog")

    @pytest.mark.parametrize("scene_kind", ["forward", "mirror"])
    def test_the_panel_selectable_equals_the_outcome_of_the_put_at_the_same_series(
        self, admin_client, stage, world, db_session, scene_kind
    ):
        first, factor = ("10", "3") if scene_kind == "forward" else ("100", "6")
        scene, series_id = _ipc_scene(stage, world, db_session, first_nominal=first, factor=factor)
        for params in ({}, {"inflation_series_id": series_id}):
            panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item, **params)
            for analog in panel["analogs"]:
                response = _put(
                    admin_client, stage, scene, {"kind": "analog", "analog_id": analog["analog_id"]}, **params
                )

                assert (response.status_code == 204) == analog["selectable"], (params, analog["analog_id"])
                if response.status_code != 204:
                    assert _code(response) == "analog_unavailable"

    @pytest.mark.parametrize(
        ("kind", "code"),
        [
            ("target", "inflation_target_missing"),
            ("amendment", "amendment_date_missing"),
            ("years", "missing_inflation_years"),
        ],
    )
    def test_the_series_refusals_of_the_put_are_the_codes_and_texts_of_the_read(
        self, admin_client, stage, db_session, kind, code
    ):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, kind)
        scene = Scene(rnd, part, item, {}, None, {})
        params = {"inflation_series_id": series_id}

        response = _put(admin_client, stage, scene, {"kind": "freshest"}, **params)
        read = _panel_get(admin_client, stage.tender, rnd, part, item, **params)

        assert response.status_code == read.status_code == 422
        assert response.json()["detail"]["code"] == code
        assert response.json()["detail"] == read.json()["detail"]
        assert _rows(db_session) == []

    def test_an_unknown_series_is_404_like_the_read(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        params = {"inflation_series_id": 10**9}

        response = _put(admin_client, stage, scene, {"kind": "freshest"}, **params)
        read = _panel_get(admin_client, stage.tender, scene.rnd, scene.part, scene.item, **params)

        assert response.status_code == read.status_code == 404
        assert _rows(db_session) == []

    def test_the_series_is_not_stored_with_the_choice(self, db_session):
        columns = {column.name for column in KpLineTarget.__table__.columns}

        assert columns == {
            "position_item_id", "kind", "analog_position_item_id", "set_by", "set_at",
            "ref_origin", "ref_method", "ref_factor", "ref_basis", "ref_link_id", "ref_without_analogs",
        }


# ---------------------------------------------------------------------------
#  Счёт решений
# ---------------------------------------------------------------------------


class TestDecisionCounts:
    def test_the_round_and_the_participant_count_the_choices(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        second = _line(stage, scene.part.proposal, D("155"), chain=scene.chain)
        db_session.flush()
        assert kd.count_round_decisions(db_session, scene.rnd.id).targets == 0
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204
        assert _put(admin_client, stage, scene, {"kind": "freshest"}, item=second).status_code == 204

        round_counts = kd.count_round_decisions(db_session, scene.rnd.id)
        package_counts = kd.count_package_decisions(db_session, scene.part.offer.package_id)

        assert (round_counts.targets, round_counts.total) == (2, 2)
        assert (package_counts.targets, package_counts.total) == (2, 2)

    def test_the_choice_of_another_round_is_not_counted(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        other_rnd = stage.round()
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        assert kd.count_round_decisions(db_session, other_rnd.id).targets == 0

    def test_the_participant_deletion_token_and_composition_name_the_choice(self, admin_client, stage, world, db_session):
        scene = _scene(stage, world)
        package_id = scene.part.offer.package_id
        before = crud_tenders.participant_deletion_preview(db_session, stage.tender.id, package_id)
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204

        after = crud_tenders.participant_deletion_preview(db_session, stage.tender.id, package_id)

        assert (before["decisions_count"], after["decisions_count"]) == (0, 1)
        assert before["confirmation_token"] != after["confirmation_token"]

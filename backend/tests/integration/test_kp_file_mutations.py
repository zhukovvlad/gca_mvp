"""Мутации решений по строкам КП: шесть маршрутов `PUT|DELETE …/lines/{position_item_id}/
{agreed|comment|rung}` (спека 4б §2.3–§2.6, §2.11, §2.15).

Сцены — помощники сцены решений и экрана этапа; названия и числа выдуманы, эталоны —
числа и строки, посчитанные руками. Каждое правило проверено с обеих сторон: вход, где
оно срабатывает, и соседний вход, нарушающий ровно одно условие. Состояние таблиц
решений читается столбцами, а не сущностями: ответ роутера пишет в обход identity map.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import FamilyParameter, KpFamilyRung, KpLineAgreement, KpLineComment
from services.our_price import RUNG_ORDER
from tests.integration.test_kp_file_scene import (
    SET_AT,
    _agree,
    _comment,
    _family_rung,
    _system_scene,
    _user,
    _work_scene,
)
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    _history,
    _line_of,
    _panel,
    _put,
    _second_variant,
)
from tests.integration.test_our_price_offer_api import _ok as _offer_ok
from tests.integration.test_our_price_round_api import Stage

pytestmark = pytest.mark.integration

D = Decimal
ROUTES = ("agreed", "comment", "rung")
SIX = (
    ("PUT", "agreed"),
    ("DELETE", "agreed"),
    ("PUT", "comment"),
    ("DELETE", "comment"),
    ("PUT", "rung"),
    ("DELETE", "rung"),
)
#: Пары, которые на строке-работе дают 204: `PUT …/rung` у работы — 422 `rung_not_applicable`.
SIX_OK_ON_WORK = tuple(pair for pair in SIX if pair != ("PUT", "rung"))
BODIES = {
    "agreed": {"value": "100", "basis": "net"},
    "comment": {"text": "Текст"},
    "rung": {"level": "v", "scope": "cls"},
}
LONG = D("100.00000000000000000001")


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def admin_client(admin_client, db_session):
    """Клиент администратора, который перед каждым запросом закрепляет сцену и посеянные
    решения: `rollback` роутера откатывает транзакцию сессии, а в проде сцена к запросу
    уже закоммичена. Без этого отказ уносил бы с собой и саму сцену теста."""
    send = admin_client.request

    def request(*args, **kwargs):
        db_session.commit()
        return send(*args, **kwargs)

    admin_client.request = request
    return admin_client


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _url(stage, rnd, part, item, route, *, tender=None, round_=None) -> str:
    return (
        f"/api/v1/tenders/{(tender or stage.tender).id}/rounds/{(round_ or rnd).id}"
        f"/our-price/offers/{part.offer.id}/lines/{getattr(item, 'id', item)}/{route}"
    )


def _send(client, method, url, route, body=None):
    if method == "DELETE":
        return client.delete(url)
    return client.put(url, json=BODIES[route] if body is None else body)


def _agreement(db, item):
    return db.execute(
        sa.select(KpLineAgreement.net_price, KpLineAgreement.set_by, KpLineAgreement.set_at).where(
            KpLineAgreement.position_item_id == item.id
        )
    ).all()


def _comment_rows(db, item):
    return db.execute(
        sa.select(KpLineComment.text, KpLineComment.set_by, KpLineComment.set_at).where(
            KpLineComment.position_item_id == item.id
        )
    ).all()


def _family_rows(db, estimate):
    return db.execute(
        sa.select(
            KpFamilyRung.work_family_id,
            KpFamilyRung.level,
            KpFamilyRung.scope,
            KpFamilyRung.first_main_parameter_id,
            KpFamilyRung.set_by,
        ).where(KpFamilyRung.estimate_id == estimate.id)
    ).all()


def _set_at(db, column, *where):
    return db.execute(sa.select(column).where(*where)).scalar_one()


def _everything(db) -> tuple[int, int, int]:
    """Число строк во всех трёх таблицах решений."""
    return tuple(
        db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
        for model in (KpLineAgreement, KpLineComment, KpFamilyRung)
    )


def _code(response) -> str:
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail["code"]


def _plain_scene(stage, *, vat, price):
    rnd = stage.round()
    part = stage.participant(rnd, "Подрядчик А", vat=vat)
    return rnd, part, stage.kp(part, price)


def _two_mains(stage):
    """Второй главный параметр схемы: у строк варианта появляется первый главный (`p`).
    Вызывается после сборки сцены: `_second_variant` ждёт у схемы единственный параметр."""
    return stage.world.param(stage.schema, 2, "Материал")


def _first_param_id(db, stage) -> int:
    return db.execute(
        sa.select(FamilyParameter.id).where(FamilyParameter.schema_id == stage.schema.id, FamilyParameter.ordinal == 1)
    ).scalar_one()


def _two_group_system_scene(stage):
    """Семья-система с двумя вариантами в одном КП; в истории класса — договор только
    варианта 1. На «точном варианте» у группы варианта 2 договоров ноль, на «семье» группа
    одна — с договором."""
    _history(stage, D("1"), total=D(100000), kind="SYSTEM")
    second = _second_variant(stage)
    rnd = stage.round()
    part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
    item = _put(stage, part.proposal, "R", D("1"), total=D(90000), kind="SYSTEM")
    other = _put(stage, part.proposal, "R", D("1"), total=D(50000), kind="SYSTEM", variant=second)
    return rnd, part, item, other


def _seed(db, item, route, user):
    if route == "agreed":
        _agree(db, item, D("90"), user)
    else:
        _comment(db, item, "Прежний", user)


# ---------------------------------------------------------------------------
#  Права и отказы доступа
# ---------------------------------------------------------------------------


class TestRights:
    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_member_is_forbidden_and_writes_nothing(self, member_client, stage, db_session, method, route):
        rnd, part, item = _work_scene(stage)

        response = _send(member_client, method, _url(stage, rnd, part, item, route), route)

        assert response.status_code == 403
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize(("method", "route"), SIX_OK_ON_WORK)
    def test_admin_gets_204_without_a_body(self, admin_client, stage, method, route):
        rnd, part, item = _work_scene(stage)

        response = _send(admin_client, method, _url(stage, rnd, part, item, route), route)

        assert response.status_code == 204
        assert response.content == b""

    def test_member_delete_does_not_remove_an_existing_decision(self, member_client, stage, db_session):
        rnd, part, item = _work_scene(stage)
        _agree(db_session, item, D("90"), _user(stage.f))

        assert member_client.delete(_url(stage, rnd, part, item, "agreed")).status_code == 403
        assert len(_agreement(db_session, item)) == 1


class TestOwnership:
    """404 на строку чужого КП, на главу и на оффер чужого этапа — у всех шести маршрутов."""

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_line_of_another_participant_is_404(self, admin_client, stage, db_session, method, route):
        rnd, part, _ = _work_scene(stage)
        neighbour = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        foreign = stage.kp(neighbour, D("150"))

        response = _send(admin_client, method, _url(stage, rnd, part, foreign, route), route)

        assert response.status_code == 404
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_chapter_is_not_a_line(self, admin_client, stage, method, route):
        rnd, part, _ = _work_scene(stage)
        chapter = stage.chapter(part.proposal, "R")

        assert _send(admin_client, method, _url(stage, rnd, part, chapter, route), route).status_code == 404

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_unknown_line_is_404(self, admin_client, stage, method, route):
        rnd, part, _ = _work_scene(stage)

        assert _send(admin_client, method, _url(stage, rnd, part, 987654321, route), route).status_code == 404

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_offer_of_another_round_of_the_same_tender_is_404(self, admin_client, stage, method, route):
        rnd, part, item = _work_scene(stage)
        other = stage.round()

        assert _send(admin_client, method, _url(stage, other, part, item, route), route).status_code == 404

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_round_of_another_tender_is_404(self, admin_client, stage, factories, method, route):
        rnd, part, item = _work_scene(stage)
        foreign_round = factories.TenderRoundFactory.create()

        response = _send(admin_client, method, _url(stage, rnd, part, item, route, round_=foreign_round), route)

        assert response.status_code == 404

    @pytest.mark.parametrize(("method", "route"), SIX)
    def test_other_tender_in_the_url_is_404(self, admin_client, stage, factories, method, route):
        rnd, part, item = _work_scene(stage)
        foreign_tender = factories.TenderFactory.create()

        response = _send(admin_client, method, _url(stage, rnd, part, item, route, tender=foreign_tender), route)

        assert response.status_code == 404

    @pytest.mark.parametrize("route", ROUTES)
    def test_offer_without_an_estimate_is_404(self, admin_client, stage, route):
        rnd, _, item = _work_scene(stage)
        empty = stage.participant(rnd, "Подрядчик Б", estimate=False)

        assert _send(admin_client, "PUT", _url(stage, rnd, empty, item, route), route).status_code == 404

    @pytest.mark.parametrize("route", ("agreed", "comment"))
    def test_own_line_is_204_next_to_the_foreign_one(self, admin_client, stage, route):
        rnd, part, item = _work_scene(stage)
        stage.participant(rnd, "Подрядчик Б", vat=D("0"))

        assert _send(admin_client, "PUT", _url(stage, rnd, part, item, route), route).status_code == 204


# ---------------------------------------------------------------------------
#  «Договорились»
# ---------------------------------------------------------------------------


class TestAgreed:
    def _put(self, client, stage, rnd, part, item, value, basis, *, raw=None):
        body = {"value": value, "basis": basis} if raw is None else raw
        return client.put(_url(stage, rnd, part, item, "agreed"), json=body)

    def test_shown_value_is_stored_net_of_the_kp_base(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        response = self._put(admin_client, stage, rnd, part, item, "122", "shown")

        assert response.status_code == 204
        ((net, set_by, _),) = _agreement(db_session, item)
        assert net == D("100")
        assert set_by == admin_client.user.id

    def test_shown_value_is_not_rounded(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("20"), price=D("120"))

        assert self._put(admin_client, stage, rnd, part, item, "100", "shown").status_code == 204

        ((net, _, _),) = _agreement(db_session, item)
        assert abs(net - D(250) / 3) < D("1e-20")  # 100 * 100 / 120, без копеек
        assert net != net.quantize(D("0.01"))

    def test_net_value_is_stored_as_given_to_the_last_digit(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        assert self._put(admin_client, stage, rnd, part, item, str(LONG), "net").status_code == 204

        ((net, _, _),) = _agreement(db_session, item)
        assert net == LONG

    def test_net_is_accepted_when_the_base_is_unknown(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=None, price=D("122"))

        assert self._put(admin_client, stage, rnd, part, item, "100", "net").status_code == 204

        ((net, _, _),) = _agreement(db_session, item)
        assert net == D("100")

    def test_shown_with_an_unknown_base_is_422_and_writes_nothing(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=None, price=D("122"))

        response = self._put(admin_client, stage, rnd, part, item, "122", "shown")

        assert response.status_code == 422
        assert _code(response) == "unknown_vat_base"
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize(
        "value", ["0", "0.0", "-1", "-0.00000000000000000001", "NaN", "sNaN", "Infinity", "-Infinity", "abc", "", "1,5"]
    )
    @pytest.mark.parametrize("basis", ["net", "shown"])
    def test_value_that_is_not_a_price_is_invalid_agreed(self, admin_client, stage, db_session, value, basis):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        response = self._put(admin_client, stage, rnd, part, item, value, basis)

        assert response.status_code == 422
        assert _code(response) == "invalid_agreed"
        assert _everything(db_session) == (0, 0, 0)

    def test_the_smallest_positive_value_is_a_price(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        assert self._put(admin_client, stage, rnd, part, item, "0.00000000000000000001", "net").status_code == 204

        assert _agreement(db_session, item)[0][0] == D("0.00000000000000000001")

    @pytest.mark.parametrize("value", [122, 122.5, None, True])
    def test_a_json_number_instead_of_a_string_is_a_form_error(self, admin_client, stage, db_session, value):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        response = self._put(admin_client, stage, rnd, part, item, None, None, raw={"value": value, "basis": "net"})

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)  # форма FastAPI, не код домена
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize("raw", [{"value": "100"}, {"basis": "net"}, {"value": "100", "basis": "gross"}, {}])
    def test_missing_field_or_unknown_basis_is_a_form_error(self, admin_client, stage, db_session, raw):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        response = self._put(admin_client, stage, rnd, part, item, None, None, raw=raw)

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert _everything(db_session) == (0, 0, 0)

    def test_repeated_put_overwrites_value_author_and_time(self, admin_client, stage, db_session, factories):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))
        _agree(db_session, item, D("90"), _user(factories))  # прежнее решение, SET_AT

        assert self._put(admin_client, stage, rnd, part, item, "100", "net").status_code == 204

        ((net, set_by, set_at),) = _agreement(db_session, item)
        assert (net, set_by) == (D("100"), admin_client.user.id)
        assert set_at > SET_AT

    def test_repeated_put_leaves_exactly_one_row(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        for value in ("100", "101", "102"):
            assert self._put(admin_client, stage, rnd, part, item, value, "net").status_code == 204

        assert [row[0] for row in _agreement(db_session, item)] == [D("102")]

    def test_delete_removes_only_this_line(self, admin_client, stage, db_session, factories):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))
        neighbour = stage.kp(part, D("130"))
        user = _user(factories)
        _agree(db_session, item, D("90"), user)
        _agree(db_session, neighbour, D("95"), user)

        assert admin_client.delete(_url(stage, rnd, part, item, "agreed")).status_code == 204

        assert _agreement(db_session, item) == []
        assert [row[0] for row in _agreement(db_session, neighbour)] == [D("95")]

    def test_delete_of_a_missing_decision_is_204(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))

        assert admin_client.delete(_url(stage, rnd, part, item, "agreed")).status_code == 204
        assert _everything(db_session) == (0, 0, 0)

    def test_the_decision_is_read_back_by_the_offer_view(self, admin_client, stage):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))
        assert self._put(admin_client, stage, rnd, part, item, "122", "shown").status_code == 204

        line = _line_of(_offer_ok(admin_client, stage.tender, rnd, part), item)

        assert D(line["agreed"]["net"]) == D("100")
        assert D(line["agreed"]["shown"]) == D("122")
        assert line["agreed"]["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}


# ---------------------------------------------------------------------------
#  Свой комментарий
# ---------------------------------------------------------------------------


class TestComment:
    def _put(self, client, stage, rnd, part, item, text):
        return client.put(_url(stage, rnd, part, item, "comment"), json={"text": text})

    @pytest.mark.parametrize("text", ["a" * 2000, "ж" * 2000])
    def test_two_thousand_characters_are_stored(self, admin_client, stage, db_session, text):
        rnd, part, item = _work_scene(stage)

        assert self._put(admin_client, stage, rnd, part, item, text).status_code == 204

        assert _comment_rows(db_session, item)[0][0] == text

    @pytest.mark.parametrize("text", ["a" * 2001, "ж" * 2001])
    def test_two_thousand_and_one_is_422_before_any_write(self, admin_client, stage, db_session, text):
        rnd, part, item = _work_scene(stage)

        response = self._put(admin_client, stage, rnd, part, item, text)

        assert response.status_code == 422
        assert _code(response) == "comment_too_long"
        assert _everything(db_session) == (0, 0, 0)

    def test_the_failed_put_keeps_the_previous_comment(self, admin_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        _comment(db_session, item, "Прежний", _user(factories))

        assert self._put(admin_client, stage, rnd, part, item, "x" * 2001).status_code == 422

        assert [row[0] for row in _comment_rows(db_session, item)] == ["Прежний"]

    def test_text_is_kept_verbatim(self, admin_client, stage, db_session):
        rnd, part, item = _work_scene(stage)
        text = "  =СУММА(A1)\n- строка  "

        assert self._put(admin_client, stage, rnd, part, item, text).status_code == 204

        assert _comment_rows(db_session, item)[0][0] == text

    def test_empty_text_is_a_decision_and_the_line_reads_custom(self, admin_client, stage, db_session):
        rnd, part, item = _work_scene(stage)
        assert _line_of(_offer_ok(admin_client, stage.tender, rnd, part), item)["comment_kind"] == "template"

        assert self._put(admin_client, stage, rnd, part, item, "").status_code == 204

        assert [row[0] for row in _comment_rows(db_session, item)] == [""]
        assert _line_of(_offer_ok(admin_client, stage.tender, rnd, part), item)["comment_kind"] == "custom"
        assert _panel(admin_client, stage.tender, rnd, part, item)["comment"]["text"] == ""

    def test_repeated_put_overwrites_text_author_and_time(self, admin_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        _comment(db_session, item, "Прежний", _user(factories))

        assert self._put(admin_client, stage, rnd, part, item, "Новый").status_code == 204

        ((text, set_by, set_at),) = _comment_rows(db_session, item)
        assert (text, set_by) == ("Новый", admin_client.user.id)
        assert set_at > SET_AT

    @pytest.mark.parametrize("text", [123, None, ["a"]])
    def test_a_non_string_is_a_form_error(self, admin_client, stage, db_session, text):
        rnd, part, item = _work_scene(stage)

        response = admin_client.put(_url(stage, rnd, part, item, "comment"), json={"text": text})

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert _everything(db_session) == (0, 0, 0)

    def test_delete_returns_the_template_and_missing_is_204(self, admin_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        _comment(db_session, item, "Свой", _user(factories))
        url = _url(stage, rnd, part, item, "comment")

        assert admin_client.delete(url).status_code == 204
        assert admin_client.delete(url).status_code == 204

        assert _comment_rows(db_session, item) == []
        assert _line_of(_offer_ok(admin_client, stage.tender, rnd, part), item)["comment_kind"] == "template"

    def test_delete_removes_only_this_line(self, admin_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        neighbour = stage.kp(part, D("130"))
        user = _user(factories)
        _comment(db_session, item, "Свой", user)
        _comment(db_session, neighbour, "Соседа", user)

        assert admin_client.delete(_url(stage, rnd, part, item, "comment")).status_code == 204

        assert [row[0] for row in _comment_rows(db_session, neighbour)] == ["Соседа"]


# ---------------------------------------------------------------------------
#  Ступень работы
# ---------------------------------------------------------------------------


class TestWorkRung:
    """У строки-работы ручной ступени нет (миграция 0024): `PUT` — 422 `rung_not_applicable`,
    `DELETE` — 204 без действия."""

    def _put(self, client, stage, rnd, part, item, level, scope):
        return client.put(_url(stage, rnd, part, item, "rung"), json={"level": level, "scope": scope})

    @pytest.mark.parametrize(
        ("level", "scope"),
        [(rung.level, rung.scope) for rung in RUNG_ORDER] + [("v", "x"), ("x", "cls"), ("", "")],
    )
    def test_put_on_a_work_line_is_rung_not_applicable_for_any_pair(
        self, admin_client, stage, db_session, level, scope
    ):
        rnd, part, item = _work_scene(stage)

        response = self._put(admin_client, stage, rnd, part, item, level, scope)

        assert (response.status_code, _code(response)) == (422, "rung_not_applicable")
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize("raw", [{"level": "v"}, {"scope": "cls"}, {}, {"level": 1, "scope": "cls"}])
    def test_an_incomplete_body_is_a_form_error(self, admin_client, stage, db_session, raw):
        rnd, part, item = _work_scene(stage)

        response = admin_client.put(_url(stage, rnd, part, item, "rung"), json=raw)

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert _everything(db_session) == (0, 0, 0)

    @pytest.mark.parametrize("kind", ["no_price", "no_variant"])
    def test_a_line_that_did_not_reach_the_ladder_is_rung_not_applicable_too(
        self, admin_client, stage, kind
    ):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("0")) if kind == "no_price" else _put(stage, part.proposal, "R", D("150"), variant=None)
        assert _line_of(_offer_ok(admin_client, stage.tender, rnd, part), item)["rungs"] == []

        response = self._put(admin_client, stage, rnd, part, item, "v", "cls")

        assert (response.status_code, _code(response)) == (422, "rung_not_applicable")

    def test_the_refusal_keeps_the_family_rung_and_the_other_decisions(
        self, admin_client, stage, db_session, factories
    ):
        rnd, part, item = _work_scene(stage)
        user = _user(factories)
        _family_rung(db_session, part.estimate, stage.family, "f", "all", user)
        _agree(db_session, item, D("90"), user)
        _comment(db_session, item, "Прежний", user)
        before = _everything(db_session)

        assert self._put(admin_client, stage, rnd, part, item, "v", "all").status_code == 422

        assert _everything(db_session) == before
        assert [row[:3] for row in _family_rows(db_session, part.estimate)] == [(stage.family.id, "f", "all")]
        assert _agreement(db_session, item)[0][:2] == (D("90"), user.id)
        assert _comment_rows(db_session, item)[0][:2] == ("Прежний", user.id)

    def test_delete_on_a_work_line_is_204_and_removes_nothing(self, admin_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        user = _user(factories)
        _family_rung(db_session, part.estimate, stage.family, "f", "all", user)
        _agree(db_session, item, D("90"), user)
        _comment(db_session, item, "Прежний", user)
        before = _everything(db_session)
        url = _url(stage, rnd, part, item, "rung")

        assert admin_client.delete(url).status_code == 204
        assert admin_client.delete(url).status_code == 204

        assert _everything(db_session) == before
        assert [row[:3] for row in _family_rows(db_session, part.estimate)] == [(stage.family.id, "f", "all")]


# ---------------------------------------------------------------------------
#  Ступень системы
# ---------------------------------------------------------------------------


class TestSystemRung:
    def _put(self, client, stage, rnd, part, item, level, scope):
        return client.put(_url(stage, rnd, part, item, "rung"), json={"level": level, "scope": scope})

    def test_put_writes_the_family_rung_of_this_estimate_only(self, admin_client, stage, db_session):
        rnd, first, second, item, _ = _system_scene(stage)

        assert self._put(admin_client, stage, rnd, first, item, "f", "all").status_code == 204

        assert _family_rows(db_session, first.estimate) == [(stage.family.id, "f", "all", None, admin_client.user.id)]
        assert _family_rows(db_session, second.estimate) == []

    def test_the_parameter_level_stores_the_first_main_parameter(self, admin_client, stage, db_session):
        rnd, first, _, item, _ = _system_scene(stage)
        second_param = _two_mains(stage)
        panel = _panel(admin_client, stage.tender, rnd, first, item)["line"]
        assert any(r["level"] == "p" and r["selectable"] for r in panel["rungs"])

        assert self._put(admin_client, stage, rnd, first, item, "p", "all").status_code == 204

        ((_, level, scope, parameter_id, _),) = _family_rows(db_session, first.estimate)
        assert (level, scope) == ("p", "all")
        assert parameter_id == _first_param_id(db_session, stage)
        assert parameter_id != second_param.id

    def test_the_non_parameter_level_stores_no_parameter(self, admin_client, stage, db_session):
        rnd, first, _, item, _ = _system_scene(stage)
        _two_mains(stage)

        assert self._put(admin_client, stage, rnd, first, item, "v", "cls").status_code == 204

        assert _family_rows(db_session, first.estimate)[0][3] is None

    def test_repeated_put_overwrites_the_family_rung(self, admin_client, stage, db_session, factories):
        rnd, first, _, item, _ = _system_scene(stage)
        _family_rung(db_session, first.estimate, stage.family, "v", "all", _user(factories))

        assert self._put(admin_client, stage, rnd, first, item, "f", "cls").status_code == 204

        assert _family_rows(db_session, first.estimate) == [(stage.family.id, "f", "cls", None, admin_client.user.id)]
        assert _set_at(db_session, KpFamilyRung.set_at, KpFamilyRung.estimate_id == first.estimate.id) > SET_AT

    @pytest.mark.parametrize(("old", "new"), [(("p", "all"), ("v", "cls")), (("v", "cls"), ("p", "all"))])
    def test_repeated_put_rewrites_the_parameter_snapshot_with_the_level(
        self, admin_client, stage, db_session, factories, old, new
    ):
        """Перезапись меняет снимок параметра вместе с уровнем: иначе `p` → `v` оставил бы
        параметр у уровня `v` (а `v` → `p` — пустой параметр у `p`), и CHECK таблицы уронил
        бы запрос в 500."""
        rnd, first, _, item, _ = _system_scene(stage)
        _two_mains(stage)
        param = _first_param_id(db_session, stage)
        first_main = param if old[0] == "p" else None
        _family_rung(db_session, first.estimate, stage.family, old[0], old[1], _user(factories), first_main=first_main)

        assert self._put(admin_client, stage, rnd, first, item, *new).status_code == 204

        ((_, level, scope, parameter_id, set_by),) = _family_rows(db_session, first.estimate)
        assert (level, scope, set_by) == (*new, admin_client.user.id)
        assert parameter_id == (param if new[0] == "p" else None)

    def test_a_rung_empty_in_another_group_of_the_family_is_rung_empty(self, admin_client, stage, db_session):
        """`selectable` системы — у каждой группы семьи, а не у группы самой строки: у строки
        на «точном варианте» договор есть, у соседней группы той же семьи — нет."""
        rnd, part, item, _ = _two_group_system_scene(stage)
        listed = {
            (r["level"], r["scope"]): r for r in _panel(admin_client, stage.tender, rnd, part, item)["line"]["rungs"]
        }
        assert listed[("v", "cls")]["contracts"] == 1
        assert listed[("v", "cls")]["selectable"] is False

        response = self._put(admin_client, stage, rnd, part, item, "v", "cls")

        assert (response.status_code, _code(response)) == (422, "rung_empty")
        assert _everything(db_session) == (0, 0, 0)

    def test_the_decision_agrees_with_the_panel_on_every_rung(self, admin_client, stage, db_session):
        rnd, part, item, _ = _two_group_system_scene(stage)
        panel = _panel(admin_client, stage.tender, rnd, part, item)["line"]
        listed = {(r["level"], r["scope"]): r["selectable"] for r in panel["rungs"]}
        assert any(listed.values()) and not all(listed.values())  # на фикстуре есть оба исхода

        for rung in RUNG_ORDER:
            response = self._put(admin_client, stage, rnd, part, item, rung.level, rung.scope)
            key = (rung.level, rung.scope)
            if key not in listed:
                assert (response.status_code, _code(response)) == (422, "unknown_rung"), key
            elif listed[key]:
                assert response.status_code == 204, key
            else:
                assert (response.status_code, _code(response)) == (422, "rung_empty"), key

    def test_a_system_line_without_a_key_has_no_ladder_and_delete_touches_nothing(
        self, admin_client, stage, db_session, factories
    ):
        """Системная строка без варианта: ключа нет, а значит, нет и семьи для ступени —
        `PUT` — `no_ladder`, `DELETE` ничего не снимает."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("1"), total=D(90000), kind="SYSTEM", variant=None)
        _family_rung(db_session, part.estimate, stage.family, "f", "all", _user(factories))

        put = self._put(admin_client, stage, rnd, part, item, "v", "cls")
        assert (put.status_code, _code(put)) == (422, "no_ladder")
        assert admin_client.delete(_url(stage, rnd, part, item, "rung")).status_code == 204

        assert [row[:3] for row in _family_rows(db_session, part.estimate)] == [(stage.family.id, "f", "all")]

    def test_delete_removes_the_family_rung_of_this_estimate_only(
        self, admin_client, stage, db_session, factories
    ):
        rnd, first, second, item, other = _system_scene(stage)
        user = _user(factories)
        _family_rung(db_session, first.estimate, stage.family, "f", "all", user)
        _family_rung(db_session, second.estimate, stage.family, "f", "all", user)

        assert admin_client.delete(_url(stage, rnd, first, item, "rung")).status_code == 204

        assert _family_rows(db_session, first.estimate) == []
        assert [row[:3] for row in _family_rows(db_session, second.estimate)] == [(stage.family.id, "f", "all")]
        assert _line_of(_offer_ok(admin_client, stage.tender, rnd, first), item)["manual_rung"] is None

    def test_delete_of_a_missing_family_rung_is_204(self, admin_client, stage, db_session):
        rnd, first, _, item, _ = _system_scene(stage)

        assert admin_client.delete(_url(stage, rnd, first, item, "rung")).status_code == 204
        assert _everything(db_session) == (0, 0, 0)

    def test_a_system_family_rung_without_contracts_is_rung_empty(self, admin_client, stage, db_session):
        other = stage.f.RateClassFactory.create(title="Класс Ч")
        _history(stage, D("1"), total=D(100000), kind="SYSTEM", rate_class=other)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("1"), total=D(90000), kind="SYSTEM")

        empty = self._put(admin_client, stage, rnd, part, item, "v", "cls")
        full = self._put(admin_client, stage, rnd, part, item, "v", "all")

        assert (empty.status_code, _code(empty)) == (422, "rung_empty")
        assert full.status_code == 204
        assert [row[:3] for row in _family_rows(db_session, part.estimate)] == [(stage.family.id, "v", "all")]

    def test_the_failed_put_keeps_the_family_rung(self, admin_client, stage, db_session, factories):
        rnd, first, _, item, _ = _system_scene(stage)
        _family_rung(db_session, first.estimate, stage.family, "v", "all", _user(factories))

        response = self._put(admin_client, stage, rnd, first, item, "p", "all")  # уровня `p` у семьи нет

        assert (response.status_code, _code(response)) == (422, "unknown_rung")
        assert [row[:3] for row in _family_rows(db_session, first.estimate)] == [(stage.family.id, "v", "all")]

    def test_the_screen_counts_on_the_family_rung(self, admin_client, stage):
        """Мутация, затем чтение: ступень семьи «семья, класс» даёт расхождение единиц."""
        rnd, first, _, item, _ = _system_scene(stage)
        before = _line_of(_offer_ok(admin_client, stage.tender, rnd, first), item)
        assert (before["rung"]["level"], before["state"]) == ("v", "comparable")

        assert self._put(admin_client, stage, rnd, first, item, "f", "cls").status_code == 204

        after = _line_of(_offer_ok(admin_client, stage.tender, rnd, first), item)
        assert (after["rung"]["level"], after["rung"]["scope"], after["rung"]["auto"]) == ("f", "cls", False)
        assert after["state"] == "unit_mismatch"
        assert (after["manual_rung"]["target"], after["manual_rung"]["active"]) == ("family", True)


# ---------------------------------------------------------------------------
#  Отказ откатывает: таблицы решений не получают новых строк
# ---------------------------------------------------------------------------


class TestRefusalRollsBack:
    @pytest.mark.parametrize(
        ("route", "body", "code"),
        [
            ("agreed", {"value": "0", "basis": "net"}, "invalid_agreed"),
            ("agreed", {"value": "122", "basis": "shown"}, "unknown_vat_base"),
            ("comment", {"text": "x" * 2001}, "comment_too_long"),
            ("rung", {"level": "v", "scope": "x"}, "rung_not_applicable"),
        ],
    )
    def test_a_refusal_leaves_all_three_tables_as_they_were(self, admin_client, stage, db_session, route, body, code):
        rnd, part, item = _plain_scene(stage, vat=None, price=D("122"))
        user = _user(stage.f)
        _agree(db_session, item, D("90"), user)
        _comment(db_session, item, "Прежний", user)
        before = _everything(db_session)

        response = admin_client.put(_url(stage, rnd, part, item, route), json=body)

        assert (response.status_code, _code(response)) == (422, code)
        assert _everything(db_session) == before
        assert _agreement(db_session, item)[0][:2] == (D("90"), user.id)
        assert _comment_rows(db_session, item)[0][:2] == ("Прежний", user.id)

    def test_the_agreed_price_stays_when_the_decision_is_an_amendment_of_another_line(
        self, admin_client, stage, db_session
    ):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))
        neighbour = stage.kp(part, D("130"))
        _agree(db_session, neighbour, D("95"), _user(stage.f))

        assert (
            admin_client.put(_url(stage, rnd, part, item, "agreed"), json={"value": "0", "basis": "net"}).status_code
            == 422
        )

        assert [row[0] for row in _agreement(db_session, neighbour)] == [D("95")]
        assert _agreement(db_session, item) == []


class TestTheRouterCommits:
    """Ответ 204 значит, что решение закрепил роутер: откат сессии сразу после ответа его не
    уносит. Переопределённый `admin_client` коммитит перед КАЖДЫМ запросом, а чтение идёт той
    же сессией, поэтому без этой проверки пропущенный `commit` роутера не был бы виден."""

    @pytest.mark.parametrize(("method", "route"), tuple(pair for pair in SIX if pair[1] != "rung"))
    def test_the_answer_survives_a_rollback_of_the_session(
        self, admin_client, stage, db_session, factories, method, route
    ):
        rnd, part, item = _work_scene(stage)
        if method == "DELETE":
            _seed(db_session, item, route, _user(factories))
        before = _everything(db_session)

        assert _send(admin_client, method, _url(stage, rnd, part, item, route), route).status_code == 204
        db_session.rollback()

        expected = list(before)
        expected[ROUTES.index(route)] += 1 if method == "PUT" else -1
        assert _everything(db_session) == tuple(expected)

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_the_family_rung_answer_survives_a_rollback_of_the_session(
        self, admin_client, stage, db_session, factories, method
    ):
        rnd, first, _, item, _ = _system_scene(stage)
        if method == "DELETE":
            _family_rung(db_session, first.estimate, stage.family, "f", "all", _user(factories))
        before = _everything(db_session)

        assert _send(admin_client, method, _url(stage, rnd, first, item, "rung"), "rung").status_code == 204
        db_session.rollback()

        expected = list(before)
        expected[2] += 1 if method == "PUT" else -1
        assert _everything(db_session) == tuple(expected)


class TestTimestampsAreAware:
    def test_the_stored_time_is_recent(self, admin_client, stage, db_session):
        rnd, part, item = _plain_scene(stage, vat=D("22"), price=D("122"))
        assert admin_client.put(_url(stage, rnd, part, item, "agreed"), json=BODIES["agreed"]).status_code == 204

        (_, _, set_at) = _agreement(db_session, item)[0]

        assert abs(dt.datetime.now(dt.UTC) - set_at) < dt.timedelta(minutes=5)

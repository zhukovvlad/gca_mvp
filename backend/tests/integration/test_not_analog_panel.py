"""«Не аналог» в сцене и панели строки (спека «Не аналог» §3 п. 2, 5, 6; §4; §5 п. 2; §7 п. 2, 3, 5; §9 п. 2, 10, 14).

Сцены строятся помощниками экрана 4а и панели справочных блоков; названия, числа и семьи выдуманы.
Тендерные отметки ставятся МАРШРУТОМ: ключ формы маршрут считает своим запросом, а сцена читает его из
`KpLine.form_key` загрузчика — согласие двух вычислений доказывает только отметка, записанная маршрутом.
Ставки истории — 0 %, поэтому нетто истории равно цене; ставка КП — 20 %.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import PositionItem
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _json, _line_of, _ok, _other_family, _pair, _panel, _url
from tests.integration.test_our_price_round_api import Stage
from tests.integration.test_ref_analogs_panel import Linked, _contract_row, _kp_part, _row, _set_value, _strict
from tests.integration.test_ref_analogs_search import _search
from tests.integration.test_semantic_queue_api import _unit_id
from tests.integration.test_simplify_target import _parts_scene

pytestmark = pytest.mark.integration

D = Decimal
TITLE = "Устройство стяжки"


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _retitle(stage, item, title) -> None:
    stage.db.execute(sa.update(PositionItem).where(PositionItem.id == item.id).values(job_title_in_proposal=title))
    stage.db.flush()
    stage.db.refresh(item)


def _kp_line(stage, part, title=TITLE, price=D("144"), **kwargs):
    item = _row(stage, part.proposal, price, title=title, **kwargs)
    _retitle(stage, item, title)
    return item


def _history(stage, prices=("100", "120", "140"), **kwargs):
    """Строки договоров истории класса тендера, по одной в договоре; в порядке цен."""
    return [_contract_row(stage, D(price), title="Подготовка", **kwargs)[1] for price in prices]


def _mark_tender(client, stage, rnd, part, kp_item, contract_item, reason="не тот материал") -> dict:
    stage.db.flush()
    response = client.put(
        f"{_url(stage.tender, rnd, part)}/lines/{kp_item.id}/not-analog/{contract_item.id}", json={"reason": reason}
    )
    assert response.status_code == 200, response.text
    return _json(response)


def _mark_price_error(client, stage, contract_item, reason="лишний ноль") -> None:
    stage.db.flush()
    response = client.put(f"/api/v1/position-items/{contract_item.id}/price-error", json={"reason": reason})
    assert response.status_code == 200, response.text


def _clear_price_error(client, stage, contract_item) -> None:
    stage.db.flush()
    assert client.delete(f"/api/v1/position-items/{contract_item.id}/price-error").status_code == 204


def _ids(cards) -> list[int]:
    return [card["analog_id"] for card in cards]


def _scene(stage, admin_client, prices=("100", "120", "140")):
    """Три строки истории, строка КП и тендерная отметка на первой строке истории."""
    rows = _history(stage, prices)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])
    return rows, rnd, part, kp


def _second_tender_scene(stage):
    """Второй тендер того же класса: этап, участник и строка КП. `stage.tender` не меняется после вызова."""
    first = stage.tender
    stage.tender = stage.f.TenderFactory.create(object=first.object, rate_class=stage.rate_class)
    stage._stage_no = 0
    try:
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик Д")
        kp = _kp_line(stage, part)
        tender = stage.tender
    finally:
        stage.tender = first
    return tender, rnd, part, kp


# ---------------------------------------------------------------------------
#  Панель: «Исключены»
# ---------------------------------------------------------------------------


def test_panel_excluded_card_carries_the_analog_fields_step_and_the_mark(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert _ids(body["analogs"]) == [rows[1].id, rows[2].id]
    (card,) = body["excluded"]
    assert card["analog_id"] == rows[0].id
    assert _pair(card["price"]) == (D("100"), D("120"))
    assert _pair(card["nominal"]) == (D("100"), D("120"))
    assert card["title"] == "Подготовка" and card["unit"] == "м²"
    assert card["step"] == {"rung": 1, "zone_scope": "own"}
    assert card["zone"] == "whole"
    for key in ("contract_id", "object_title", "number", "year", "rate_class", "contractor_id", "contractor_title",
                "section_titles", "article", "chapter_path", "unit", "with_this_contractor", "place_match"):
        assert key in card
    assert "outlier_ratio" not in card and "is_lowest" not in card and "selectable" not in card
    (mark,) = card["marks"]
    assert mark["kind"] == "tender" and mark["reason"] == "не тот материал"
    assert mark["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
    assert isinstance(mark["set_at"], str)


def test_panel_excluded_card_fields_equal_the_analog_card_of_the_same_row_before_the_mark(admin_client, stage) -> None:
    """Оракул полей — карточка той же строки среди аналогов до отметки (спека §7 п. 2: «поля аналога из
    analogs[] без outlier_ratio и меток цели»): договор, объект, подрядчик, период, путь и статья строки
    договора, место, цены в ставке участника — те же значения, а не только те же ключи."""
    rows = _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    before = {card["analog_id"]: card for card in _panel(admin_client, stage.tender, rnd, part, kp)["analogs"]}
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])
    (card,) = _panel(admin_client, stage.tender, rnd, part, kp)["excluded"]
    analog = before[rows[0].id]
    shared = set(analog) - {"outlier_ratio", "is_lowest", "is_freshest", "selectable", "released_values"}
    assert shared <= set(card)
    assert {key: card[key] for key in shared} == {key: analog[key] for key in shared}
    assert analog["place_match"] > 0 and analog["period"] is not None and analog["chapter_path"]


def test_panel_never_returns_the_internal_base_reason(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    for pid in [row.id for row in rows[1:]]:
        _mark_tender(admin_client, stage, rnd, part, kp, next(r for r in rows if r.id == pid))
    response = admin_client.get(f"{_url(stage.tender, rnd, part)}/lines/{kp.id}")
    assert response.status_code == 200
    assert "base_reason" not in response.text
    assert "base_reason" not in admin_client.get(_url(stage.tender, rnd, part)).text


def test_panel_without_marks_has_an_empty_excluded_list(admin_client, stage) -> None:
    _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert body["excluded"] == [] and len(body["analogs"]) == 3


def test_panel_line_without_a_ladder_reason_tied_to_marks_has_an_empty_excluded_list(admin_client, stage) -> None:
    _history(stage)
    family, variant = _other_family(stage)
    rnd, part = _kp_part(stage)
    kp = _row(stage, part.proposal, D("144"), family=family, variant=variant, title="Прочее")
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert body["line"]["state"] == "no_history" and body["line"]["no_history_reason"] == "no_family"
    assert body["excluded"] == []


def test_panel_price_error_mark_goes_first_then_the_tender_mark(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    _mark_price_error(admin_client, stage, rows[0], "опечатка в цене")
    (card,) = _panel(admin_client, stage.tender, rnd, part, kp)["excluded"]
    assert [m["kind"] for m in card["marks"]] == ["price_error", "tender"]
    assert [m["reason"] for m in card["marks"]] == ["опечатка в цене", "не тот материал"]


def test_panel_price_error_alone_excludes_the_row_for_every_line(admin_client, stage) -> None:
    rows = _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_price_error(admin_client, stage, rows[0])
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert _ids(body["analogs"]) == [rows[1].id, rows[2].id]
    (card,) = body["excluded"]
    assert card["analog_id"] == rows[0].id and [m["kind"] for m in card["marks"]] == ["price_error"]


def test_panel_row_with_both_marks_after_clearing_the_price_error_stays_excluded_here_only(
    admin_client, stage
) -> None:
    """Review Focus 5: после снятия «ошибки в цене» строка в `excluded` этого тендера с одной тендерной
    отметкой, а в другом тендере — снова среди аналогов."""
    rows, rnd, part, kp = _scene(stage, admin_client)
    _mark_price_error(admin_client, stage, rows[0])
    other_tender, other_rnd, other_part, other_kp = _second_tender_scene(stage)
    _clear_price_error(admin_client, stage, rows[0])

    here = _panel(admin_client, stage.tender, rnd, part, kp)
    (card,) = here["excluded"]
    assert card["analog_id"] == rows[0].id and [m["kind"] for m in card["marks"]] == ["tender"]
    assert rows[0].id not in _ids(here["analogs"])

    there = _panel(admin_client, other_tender, other_rnd, other_part, other_kp)
    assert there["excluded"] == []
    assert rows[0].id in _ids(there["analogs"])


def test_panel_trace_lists_every_excluded_row_in_the_order_of_the_walk(admin_client, stage) -> None:
    rows = _history(stage, ("100", "110", "120", "140"))
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])
    _mark_price_error(admin_client, stage, rows[1])
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert sorted(_ids(body["excluded"])) == sorted([rows[0].id, rows[1].id])
    assert _ids(body["analogs"]) == [rows[2].id, rows[3].id]
    assert all(card["step"] == {"rung": 1, "zone_scope": "own"} for card in body["excluded"])


def test_panel_excluded_follows_the_order_of_the_walk_not_the_row_ids(admin_client, stage) -> None:
    """Порядок `excluded` — порядок следа (спека §3 п. 5, §7 п. 2): пара своей зоны раньше пары других зон.
    Строка другой зоны заведена первой — её id меньше, поэтому порядок по id дал бы обратный список."""
    chain = stage.world.chain(("Корпус", "Подземная часть"), "underground")
    under_marked = _contract_row(stage, D("200"), chain=chain, title="Подготовка")[1]
    under_free = _contract_row(stage, D("220"), chain=chain, title="Подготовка")[1]
    own = _contract_row(stage, D("100"), title="Подготовка")[1]
    assert under_marked.id < own.id
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_tender(admin_client, stage, rnd, part, kp, own)
    _mark_price_error(admin_client, stage, under_marked)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert _ids(body["analogs"]) == [under_free.id]
    assert _ids(body["excluded"]) == [own.id, under_marked.id]
    assert [card["step"] for card in body["excluded"]] == [
        {"rung": 1, "zone_scope": "own"},
        {"rung": 1, "zone_scope": "other"},
    ]
    assert [[m["kind"] for m in card["marks"]] for card in body["excluded"]] == [["tender"], ["price_error"]]


def test_panel_i_beam_scenario_one_card_with_its_estimate_path_and_two_analogs(admin_client, stage) -> None:
    """Двутавр: строка своей зоны исключена, ступень найдена в другой зоне — в `excluded` одна карточка
    с путём её сметы и парой своей зоны, в `analogs` — две строки другой зоны."""
    own = _contract_row(stage, D("100"), title="Подготовка")[1]
    chain = stage.world.chain(("Корпус", "Подземная часть"), "underground")
    under = [_contract_row(stage, D(price), chain=chain, title="Подготовка")[1] for price in ("200", "220")]
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_tender(admin_client, stage, rnd, part, kp, own)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert sorted(_ids(body["analogs"])) == sorted(row.id for row in under)
    (card,) = body["excluded"]
    assert card["analog_id"] == own.id
    assert card["step"] == {"rung": 1, "zone_scope": "own"}
    assert card["chapter_path"] and all({"chapter_item_id", "title", "number"} <= set(s) for s in card["chapter_path"])
    assert card["chapter_path"][0]["title"].startswith("Раздел")


def test_panel_excluded_card_article_is_the_article_of_the_contract_row_not_of_the_kp_line(
    admin_client, stage
) -> None:
    rows = _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part, cat="R2")
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    (card,) = body["excluded"]
    assert card["article"]["id"] == stage.cats["R"].id
    assert body["line"]["article"]["id"] == stage.cats["R2"].id


def test_panel_loads_details_and_price_error_marks_once(admin_client, stage, monkeypatch) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    _mark_price_error(admin_client, stage, rows[1])
    import crud.our_price as module

    calls = {"details": 0, "price_errors": 0}
    real_details, real_marks = module.load_analog_details, module.load_price_error_marks

    def details(*args, **kwargs):
        calls["details"] += 1
        return real_details(*args, **kwargs)

    def marks(*args, **kwargs):
        calls["price_errors"] += 1
        return real_marks(*args, **kwargs)

    monkeypatch.setattr(module, "load_analog_details", details)
    monkeypatch.setattr(module, "load_price_error_marks", marks)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert len(body["excluded"]) == 2
    assert calls == {"details": 1, "price_errors": 1}


# ---------------------------------------------------------------------------
#  Ключ тендерной отметки (критическое свойство §9 п. 2) — отметка поставлена маршрутом
# ---------------------------------------------------------------------------


def _applies(body, row) -> bool:
    excluded = _ids(body["excluded"])
    analogs = _ids(body["analogs"])
    assert (row.id in excluded) != (row.id in analogs)
    return row.id in excluded


def test_key_applies_to_the_same_text_in_another_section_of_the_same_tender(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    other = _kp_line(stage, part, cat="R2")
    assert _applies(_panel(admin_client, stage.tender, rnd, part, other), rows[0])


def test_key_applies_to_all_offers_and_all_rounds_of_the_tender(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    neighbour = stage.participant(rnd, "Подрядчик Б")
    neighbour_kp = _kp_line(stage, neighbour)
    next_rnd = stage.round()
    later = stage.participant(next_rnd, "Подрядчик В")
    later_kp = _kp_line(stage, later)
    again = stage.participant(next_rnd, "Подрядчик А", package=part.offer.package)
    again_kp = _kp_line(stage, again)
    for r, p, k in ((rnd, neighbour, neighbour_kp), (next_rnd, later, later_kp), (next_rnd, again, again_kp)):
        assert _applies(_panel(admin_client, stage.tender, r, p, k), rows[0])


def test_key_does_not_apply_in_another_tender(admin_client, stage) -> None:
    rows, *_ = _scene(stage, admin_client)
    tender, rnd, part, kp = _second_tender_scene(stage)
    body = _panel(admin_client, tender, rnd, part, kp)
    assert not _applies(body, rows[0]) and body["excluded"] == []


def test_key_does_not_apply_to_another_unit(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    other = _kp_line(stage, part, cat="R2")
    stage.db.execute(
        sa.update(PositionItem).where(PositionItem.id == other.id).values(unit_id=_unit_id(stage.db, "M3"))
    )
    stage.db.flush()
    body = _panel(admin_client, stage.tender, rnd, part, other)
    assert not _applies(body, rows[0]) and body["excluded"] == []


def test_key_does_not_apply_to_another_text(admin_client, stage) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    other = _kp_line(stage, part, title="Устройство стяжки полусухой", cat="R2")
    body = _panel(admin_client, stage.tender, rnd, part, other)
    assert not _applies(body, rows[0]) and body["excluded"] == []


@pytest.mark.parametrize(
    "variant",
    ["  УСТРОЙСТВО   стяжки ", "Устройство стяжки", "устройство стяжки"],
    ids=["case_and_spaces", "nbsp", "lower"],
)
def test_key_applies_after_normalising_case_and_whitespace(admin_client, stage, variant) -> None:
    rows, rnd, part, kp = _scene(stage, admin_client)
    other = _kp_line(stage, part, title=variant, cat="R2")
    assert _applies(_panel(admin_client, stage.tender, rnd, part, other), rows[0])


def test_key_applies_after_normalising_yo(admin_client, stage) -> None:
    rows = _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part, title="Стяжка под ёлочку")
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])
    other = _kp_line(stage, part, title="СТЯЖКА  ПОД  ЕЛОЧКУ", cat="R2")
    assert _applies(_panel(admin_client, stage.tender, rnd, part, other), rows[0])


# ---------------------------------------------------------------------------
#  Разбор: причина `excluded`
# ---------------------------------------------------------------------------


def test_breakdown_and_panel_carry_the_reason_excluded_when_every_candidate_is_marked(admin_client, stage) -> None:
    rows = _history(stage)
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    for row in rows:
        _mark_tender(admin_client, stage, rnd, part, kp, row)
    panel = _panel(admin_client, stage.tender, rnd, part, kp)
    assert panel["line"]["state"] == "no_history" and panel["line"]["no_history_reason"] == "excluded"
    assert panel["analogs"] == [] and sorted(_ids(panel["excluded"])) == sorted(r.id for r in rows)
    line = _line_of(_ok(admin_client, stage.tender, rnd, part), kp)
    assert line["state"] == "no_history" and line["no_history_reason"] == "excluded"


def test_a_line_with_a_neighbour_key_is_not_affected_by_the_mark(admin_client, stage) -> None:
    """Цифры соседней строки КП с другим ключом — те же, что без отметки."""
    rows, rnd, part, kp = _scene(stage, admin_client)
    other = _kp_line(stage, part, title="Устройство иной стяжки", cat="R2")
    marked = _line_of(_ok(admin_client, stage.tender, rnd, part), kp)
    free = _line_of(_ok(admin_client, stage.tender, rnd, part), other)
    assert marked["position_item_id"] != free["position_item_id"]
    assert free["range"]["min"]["net"] == "100"
    assert marked["range"]["min"]["net"] == "120"


# ---------------------------------------------------------------------------
#  Блок А при `excluded` над `no_zone_match` (Review Focus 1, §9 п. 10)
# ---------------------------------------------------------------------------


def _zone_scene(stage, admin_client):
    """Семья с флажком; единственная строка своей зоны исключена; в подземной зоне — четыре строки."""
    _strict(stage)
    own = _contract_row(stage, D("100"), title="Подготовка")[1]
    chain = stage.world.chain(("Корпус", "Подземная часть"), "underground")
    under = [_contract_row(stage, D(price), chain=chain, title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    _mark_tender(admin_client, stage, rnd, part, kp, own)
    return own, under, rnd, part, kp


def test_zone_block_opens_over_excluded_in_the_panel(admin_client, stage) -> None:
    own, under, rnd, part, kp = _zone_scene(stage, admin_client)
    body = _panel(admin_client, stage.tender, rnd, part, kp)
    assert body["line"]["no_history_reason"] == "excluded"
    (card,) = body["excluded"]
    assert card["analog_id"] == own.id and card["step"] == {"rung": 1, "zone_scope": "own"}
    (group,) = body["reference_zones"]
    assert group["zone"] == "underground"
    assert {row["analog_id"] for row in group["rows"]} == {row.id for row in under}
    assert body["line"]["other_zones"] == [{"zone": "underground", "lines": 4, "contracts": 4}]


def test_zone_block_opens_over_excluded_in_the_search_scope(admin_client, stage) -> None:
    """Область поиска: строки подземной зоны входят в набор блока А — выброс получает отношение к медиане."""
    own, under, rnd, part, kp = _zone_scene(stage, admin_client)
    body = _search(admin_client, stage, rnd, part, kp, "Подготовка")
    found = {row["analog_id"]: row for row in body["rows"]}
    assert {row.id for row in under} <= set(found)
    assert found[under[3].id]["outlier_ratio"] == "10"
    assert [found[row.id]["outlier_ratio"] for row in under[:3]] == [None, None, None]


# ---------------------------------------------------------------------------
#  Справки панели берут `excluded` строки КП (спека §3 п. 2, §5 п. 2; §9 п. 3) — по поверхности
# ---------------------------------------------------------------------------


def test_parts_reference_of_the_panel_drops_the_row_with_the_tender_mark(admin_client, stage, world, db_session) -> None:
    """«По частям»: строка «плиты 36» с тендерной отметкой на ключ сборной строки не считается в части —
    аналогов плит два, самая низкая — 40."""
    _types, scene = _parts_scene(stage, world, db_session)
    before = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["parts_reference"][0]["parts"][0]
    assert (before["analogs"], before["lowest"]["analog_id"]) == (3, scene.ids["p36"])
    _mark_tender(admin_client, stage, scene.rnd, scene.part, scene.item, SimpleNamespace(id=scene.ids["p36"]))
    plates = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["parts_reference"][0]["parts"][0]
    assert (plates["analogs"], plates["lowest"]["analog_id"]) == (2, scene.ids["p40"])
    assert _pair(plates["min"]) == (D("40"), D("40"))


def test_link_block_of_the_panel_drops_the_row_with_the_tender_mark(admin_client, stage) -> None:
    """Блок связей: строка связанной семьи с тендерной отметкой на ключ строки КП в группе не стоит, вторая
    строка остаётся — группа не закрылась, а отфильтрована."""
    scene = Linked(stage)
    _, second = _contract_row(
        stage, D("1200"), family=scene.ref_family, variant=scene.ref_variant, unit="M2", title="Подготовка"
    )
    _set_value(stage, second, scene.ref_schema, scene.ref_param, "100 мм")
    stage.db.flush()
    (before,) = scene.panel(admin_client)["reference_links"]
    assert {row["analog_id"] for row in before["rows"]} == {scene.ref_item.id, second.id}
    _mark_tender(admin_client, stage, scene.rnd, scene.part, scene.kp_item, scene.ref_item)
    (group,) = scene.panel(admin_client)["reference_links"]
    assert {row["analog_id"] for row in group["rows"]} == {second.id}

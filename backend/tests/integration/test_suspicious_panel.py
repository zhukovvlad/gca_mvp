"""Подозрительные строки в панели строки КП (спека `2026-11-24-suspicious-lines-design.md` §2 п. 7,
§4 п. 3, §7 п. 7; план, задача 5).

Сцена — этап с участником и строкой КП (`Stage`), история — договоры класса тендера по одной строке
(`_contract_row`): три договора «10, 100, 100» дают выброс вниз у самой дешёвой строки. Названия и числа
выдуманы. Ставки истории — 0 %, ставка КП — 20 % (если не названа другая).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import crud.our_price as crud_our_price
from config import settings
from models import Contract, Estimate, NotErrorMark, PositionItem, SemanticJob
from services.status_cache import suspicion_cache
from tests import comparison_fixtures as fx
from tests.integration.test_not_analog_panel import _kp_line
from tests.integration.test_our_price_load import World, count_statements
from tests.integration.test_our_price_offer_api import _pair, _panel
from tests.integration.test_our_price_round_api import FULL_YEARS, SERIES_NAME, Stage
from tests.integration.test_ref_analogs_panel import _contract_row, _kp_part, _row
from tests.integration.test_semantic_queue_api import _unit_id

pytestmark = pytest.mark.integration

D = Decimal
LIST_URL = "/api/v1/suspicious-lines"
HISTORY_TITLE = "Подготовка"


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _scene(stage, prices=("10", "100", "100")):
    """История из `prices` (по договору на цену), участник и строка КП. Первая строка — самая дешёвая."""
    history = [_contract_row(stage, D(price), title=HISTORY_TITLE) for price in prices]
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part)
    stage.db.flush()
    return SimpleNamespace(
        rnd=rnd,
        part=part,
        kp=kp,
        contracts=[contract for contract, _ in history],
        rows=[row for _, row in history],
    )


def _panel_of(client, stage, s, **params) -> dict:
    return _panel(client, stage.tender, s.rnd, s.part, s.kp, **params)


def _analog(body, item) -> dict:
    (found,) = (a for a in body["analogs"] if a["analog_id"] == item.id)
    return found


def _listed(client, item) -> dict:
    response = client.get(LIST_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    (found,) = (r for r in body["unresolved"] + body["resolved"] if r["position_item_id"] == item.id)
    return found


def _outlier_basis(stage, direction="down", family=None) -> dict:
    return {
        "kind": "outlier",
        "family_id": (family or stage.family).id,
        "unit_id": _unit_id(stage.db, "M2"),
        "direction": direction,
    }


def _decide(stage, item, *, direction="down", family=None, neighbor=None, reason="другое изделие") -> None:
    values = {"position_item_id": item.id, "reason": reason, "set_by": stage.world.admin.id}
    if neighbor is not None:
        values.update(kind="twin", neighbor_item_id=neighbor.id)
    else:
        values.update(
            kind="outlier",
            family_id=(family or stage.family).id,
            unit_id=_unit_id(stage.db, "M2"),
            direction=direction,
        )
    stage.db.add(NotErrorMark(**values))
    stage.db.flush()


def _twin_of(stage, item, price="10"):
    """Соседка по смете сразу после строки: та же цена, другая единица, без семьи — двойник строки.
    Материалы и работы заданы, чтобы сверять их у соседа."""
    twin = stage.world.line(
        item.proposal, family=None, variant=None, unit_cost=D(price), total=D(price) * 10, title="Соседка"
    )
    stage.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == twin.context_id))
    stage.db.execute(
        sa.update(PositionItem)
        .where(PositionItem.id == twin.id)
        .values(
            unit_id=_unit_id(stage.db, "PCS"),
            unit_cost_materials=D("6"),
            unit_cost_works=D("4"),
            total_cost_total=D("100.456"),
        )
    )
    stage.db.flush()
    return twin


# ---------------------------------------------------------------------------
#  Поля аналога
# ---------------------------------------------------------------------------


def test_analog_with_a_fact_carries_the_suspicion_equal_to_the_list_item(admin_client, stage) -> None:
    s = _scene(stage)
    cheap = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    item = _listed(admin_client, s.rows[0])

    suspicion = cheap["suspicion"]
    assert suspicion["facts"] == item["facts"] and len(item["facts"]) == 1
    assert suspicion["facts"][0]["kind"] == "outlier" and suspicion["facts"][0]["direction"] == "down"
    assert suspicion["facts"][0]["ratio"] == "0.1" and suspicion["facts"][0]["usual"] == "100"
    assert suspicion["net"] == item["net"] == "10"
    assert suspicion["prices"] == item["prices"]
    assert [c["is_own"] for c in suspicion["prices"]["contracts"]] == [True, False, False]
    assert suspicion["prices"]["family_title"] == stage.family.title
    assert cheap["not_error"] == []


def test_analog_without_facts_and_decisions_has_both_fields_empty(admin_client, stage) -> None:
    s = _scene(stage)
    body = _panel_of(admin_client, stage, s)
    for row in s.rows[1:]:
        found = _analog(body, row)
        assert found["suspicion"] is None and found["not_error"] == []


def test_analog_keys_are_added_without_touching_the_others(admin_client, stage) -> None:
    s = _scene(stage)
    found = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    assert {"suspicion", "not_error", "released_values", "price", "nominal", "outlier_ratio"} <= set(found)


def test_all_facts_decided_gives_null_suspicion_and_the_records(admin_client, stage) -> None:
    s = _scene(stage)
    _decide(stage, s.rows[0], reason="первая")
    found = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    assert found["suspicion"] is None
    (decision,) = found["not_error"]
    assert decision["basis"]["kind"] == "outlier" and decision["basis"]["direction"] == "down"
    assert decision["basis"]["family_title"] == stage.family.title
    assert decision["reason"] == "первая"
    assert decision["set_by"] == {"id": stage.world.admin.id, "email": stage.world.admin.email}
    assert isinstance(decision["set_at"], str)
    assert [d for d in _listed(admin_client, s.rows[0])["not_error"]] == found["not_error"]


def test_partly_decided_keeps_only_undecided_facts_and_all_decisions_newest_first(admin_client, stage) -> None:
    s = _scene(stage)
    twin = _twin_of(stage, s.rows[0])
    _decide(stage, s.rows[0], reason="старое")
    _decide(stage, s.rows[0], neighbor=twin, reason="новое")
    found = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    # решены оба основания: подозрения нет, решений два, новое сверху
    assert found["suspicion"] is None
    assert [d["reason"] for d in found["not_error"]] == ["новое", "старое"]
    assert found["not_error"][0]["basis"]["neighbor"]["title"] == "Соседка"

    stage.db.execute(sa.delete(NotErrorMark).where(NotErrorMark.reason == "новое"))
    stage.db.flush()
    again = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    assert [f["kind"] for f in again["suspicion"]["facts"]] == ["twin"]
    assert [d["reason"] for d in again["not_error"]] == ["старое"]


def test_decision_with_a_family_outside_the_index_still_gets_its_title(admin_client, stage) -> None:
    s = _scene(stage)
    other, _ = stage.world.family("Семья без группы")
    _decide(stage, s.rows[1], family=other, direction="up")
    found = _analog(_panel_of(admin_client, stage, s), s.rows[1])
    assert found["suspicion"] is None
    assert found["not_error"][0]["basis"]["family_title"] == other.title


def test_member_gets_the_same_fields(member_client, stage) -> None:
    s = _scene(stage)
    _decide(stage, s.rows[1], direction="up")
    body = _panel_of(member_client, stage, s)
    cheap, decided = _analog(body, s.rows[0]), _analog(body, s.rows[1])
    assert [f["kind"] for f in cheap["suspicion"]["facts"]] == ["outlier"]
    assert cheap["suspicion"]["net"] == "10" and cheap["suspicion"]["prices"] is not None
    assert decided["suspicion"] is None and [d["basis"]["direction"] for d in decided["not_error"]] == ["up"]


# ---------------------------------------------------------------------------
#  Не зависят от режима НДС и ряда ИПЦ
# ---------------------------------------------------------------------------


def test_suspicion_does_not_depend_on_the_vat_mode_but_the_analog_price_does(admin_client, stage) -> None:
    s = _scene(stage)  # участник со ставкой 20 %
    other = stage.participant(s.rnd, "Подрядчик Б", vat=D("0"))
    other_kp = _kp_line(stage, other)
    stage.db.flush()
    with_vat = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    without = _analog(_panel(admin_client, stage.tender, s.rnd, other, other_kp), s.rows[0])

    assert _pair(with_vat["price"]) == (D("10"), D("12"))
    assert _pair(without["price"]) == (D("10"), D("10"))
    assert with_vat["price"] != without["price"]
    assert with_vat["suspicion"] == without["suspicion"] and with_vat["suspicion"]["net"] == "10"


def test_suspicion_does_not_depend_on_the_inflation_series_but_the_analog_price_does(
    admin_client, stage, db_session
) -> None:
    s = _scene(stage)
    series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
    ids = [contract.id for contract in s.contracts]
    db_session.execute(sa.update(Contract).where(Contract.id.in_(ids)).values(signed_date=dt.date(2025, 2, 20)))
    db_session.execute(sa.update(Estimate).where(Estimate.contract_id.in_(ids)).values(data_prepared_on_date=None))
    db_session.flush()
    plain = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    adjusted = _analog(_panel_of(admin_client, stage, s, inflation_series_id=series_id), s.rows[0])

    assert adjusted["price"] != plain["price"]
    assert D(adjusted["price"]["net"]) > D(plain["price"]["net"])
    assert adjusted["suspicion"] == plain["suspicion"] and plain["suspicion"]["net"] == "10"


# ---------------------------------------------------------------------------
#  Договор объекта тендера
# ---------------------------------------------------------------------------


def test_the_contract_of_the_tender_object_counts_in_the_statistics_but_is_not_an_analog(
    admin_client, stage
) -> None:
    s = _scene(stage)
    own = stage.world.contract(object_=stage.tender.object, rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(own, vat_rate=D("0"))
    object_row = _row(stage, proposal, D("100"), title=HISTORY_TITLE)
    stage.db.flush()

    body = _panel_of(admin_client, stage, s)
    assert object_row.id not in {a["analog_id"] for a in body["analogs"]}
    assert own.id not in {a["contract_id"] for a in body["analogs"]}
    suspicion = _analog(body, s.rows[0])["suspicion"]
    assert suspicion["facts"][0]["contracts"] == 4
    contracts = suspicion["prices"]["contracts"]
    assert own.id in {c["contract_id"] for c in contracts} and len(contracts) == 4
    assert next(c for c in contracts if c["contract_id"] == own.id)["object_title"] == stage.tender.object.title
    assert contracts == _listed(admin_client, s.rows[0])["prices"]["contracts"]


# ---------------------------------------------------------------------------
#  Двойник: сосед вне пула
# ---------------------------------------------------------------------------


def test_twin_neighbor_of_the_panel_equals_the_neighbor_of_the_list_item(admin_client, stage) -> None:
    s = _scene(stage)
    twin = _twin_of(stage, s.rows[0])
    found = _analog(_panel_of(admin_client, stage, s), s.rows[0])
    item = _listed(admin_client, s.rows[0])

    assert [f["kind"] for f in found["suspicion"]["facts"]] == ["outlier", "twin"]
    panel_twin = found["suspicion"]["facts"][1]
    listed_twin = item["facts"][1]
    assert panel_twin == listed_twin
    neighbor = panel_twin["neighbor"]
    assert neighbor["position_item_id"] == twin.id and neighbor["title"] == "Соседка"
    assert (neighbor["quantity"], neighbor["materials"], neighbor["works"], neighbor["total"]) == (
        "10", "6", "4", "100.46",
    )
    assert neighbor["unit_price"] == "10" and neighbor["is_twin"] is True
    assert found["suspicion"]["facts"] == item["facts"]


# ---------------------------------------------------------------------------
#  Число запросов
# ---------------------------------------------------------------------------


def _extra_queries(admin_client, stage, s, monkeypatch) -> tuple[int, int]:
    """Запросов панели с тёплым индексом: без новых полей (подмена) и с ними; обе — на одной сцене."""
    monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
    suspicion_cache.invalidate()
    _panel_of(admin_client, stage, s)  # прогрев индекса

    real = crud_our_price.analog_suspicions
    monkeypatch.setattr(
        crud_our_price,
        "analog_suspicions",
        lambda db, contract_of: {i: {"suspicion": None, "not_error": []} for i in contract_of},
    )
    with count_statements(stage.db) as before:
        _panel_of(admin_client, stage, s)
    monkeypatch.setattr(crud_our_price, "analog_suspicions", real)
    with count_statements(stage.db) as after:
        _panel_of(admin_client, stage, s)
    return before[0], after[0]


def test_panel_costs_one_query_more_without_twins(admin_client, stage, monkeypatch) -> None:
    s = _scene(stage)
    before, after = _extra_queries(admin_client, stage, s, monkeypatch)
    assert after - before == 1, (before, after)


def test_panel_costs_one_more_query_for_the_neighbors_of_a_twin_fact(admin_client, stage, monkeypatch) -> None:
    s = _scene(stage)
    _twin_of(stage, s.rows[0])
    before, after = _extra_queries(admin_client, stage, s, monkeypatch)
    assert after - before == 2, (before, after)


def test_panel_costs_one_more_query_for_the_neighbors_of_a_twin_decision(admin_client, stage, monkeypatch) -> None:
    s = _scene(stage)
    twin = _twin_of(stage, s.rows[0], price="500")  # не двойник: цены разные, решение старое
    _decide(stage, s.rows[1], neighbor=twin)
    before, after = _extra_queries(admin_client, stage, s, monkeypatch)
    assert after - before == 2, (before, after)


def test_panel_with_the_tender_object_contract_in_the_group_costs_one_query_more(
    admin_client, stage, monkeypatch
) -> None:
    """Договор объекта тендера не в истории панели, но в группе аналога: его подпись — из кэша, без запроса."""
    s = _scene(stage)
    own = stage.world.contract(object_=stage.tender.object, rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(own, vat_rate=D("0"))
    _row(stage, proposal, D("100"), title=HISTORY_TITLE)
    stage.db.flush()
    before, after = _extra_queries(admin_client, stage, s, monkeypatch)
    assert after - before == 1, (before, after)
    contracts = _analog(_panel_of(admin_client, stage, s), s.rows[0])["suspicion"]["prices"]["contracts"]
    assert next(c for c in contracts if c["contract_id"] == own.id)["object_title"] == stage.tender.object.title


def test_warm_panel_does_not_rebuild_the_index_or_its_labels(admin_client, stage, monkeypatch) -> None:
    """Подписи договоров, семьи и единицы лежат в кэше вместе с индексом: договор объекта тендера
    (его нет в истории панели) называется без запроса."""
    s = _scene(stage)
    own = stage.world.contract(object_=stage.tender.object, rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(own, vat_rate=D("0"))
    _row(stage, proposal, D("100"), title=HISTORY_TITLE)
    stage.db.flush()
    monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
    suspicion_cache.invalidate()
    cold = _analog(_panel_of(admin_client, stage, s), s.rows[0])["suspicion"]
    warm = _analog(_panel_of(admin_client, stage, s), s.rows[0])["suspicion"]
    assert warm == cold
    assert next(c for c in warm["prices"]["contracts"] if c["contract_id"] == own.id)["object_title"]

"""Индекс, список и счётчики «подозрительных строк договоров» (спека
`2026-11-24-suspicious-lines-design.md` §2 п. 6–7, §3 п. 3–4, §4; план, задача 3).

Сцена — та же, что у загрузчика поправки «комплект» (`test_lump_sum_load.Scene`): строка договора с
каталожной позицией и контекстом; строка получает вариант (`_variant`), иначе она не попадает в
`load_work_history`. Названия и числа выдуманы. Три договора «10, 100, 100» — однородная группа
(Q1 = 55, Q3 = 100) с обычной ценой 100: дешёвая строка — выброс вниз (×0,1).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from config import settings
from crud.suspicious_lines import (
    DecidedLine,
    LineDetails,
    neighbor_row_view,
    price_head,
    suspicion_index,
)
from models import NotErrorMark, PositionItem, PriceErrorMark, SectionChain, UnitOfMeasure, WorkCategory
from money.vat import gross_to_net
from services import import_pipeline
from services.status_cache import suspicion_cache
from services.zone_rules import chain_key
from tests.integration.test_lump_sum_api import _variant
from tests.integration.test_lump_sum_load import Scene
from tests.integration.test_not_analog_api import _contract_item, _kp, _na_url
from tests.integration.test_semantic_queue_api import _unit_id

pytestmark = pytest.mark.integration

D = Decimal
URL = "/api/v1/suspicious-lines"
COUNTS = URL + "/counts"
T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def scene(db_session, factories, admin_client):
    return Scene(db_session, factories, admin_client.user)


# ---------------------------------------------------------------------------
#  Помощники сцены
# ---------------------------------------------------------------------------


def _contract(scene, **kwargs):
    estimate, proposal = scene.contract_estimate(**kwargs)
    return SimpleNamespace(estimate=estimate, proposal=proposal, contract_id=estimate.contract_id)


def _chain(scene) -> SectionChain:
    """Цепочка разделов с зоной «вся»: без цепочки `load_work_history` строку не отдаёт."""
    if not hasattr(scene, "_chain"):
        titles = ("Общий раздел",)
        scene._chain = SectionChain(chain_key=chain_key(titles), titles=list(titles), zone="whole", zone_source="rule")
        scene.db.add(scene._chain)
        scene.db.flush()
    return scene._chain


def _item(scene, contract, price, *, unit="M2", number=None, **kwargs):
    """Строка договора: единица строки сметы выставлена, у строки с семьёй есть вариант."""
    item = scene.line(contract.proposal, price=price, unit=unit, **kwargs)
    values = {"unit_id": _unit_id(scene.db, unit), "section_chain_id": _chain(scene).id}
    if number is not None:
        values["item_number_in_proposal"] = number
    scene.db.execute(sa.update(PositionItem).where(PositionItem.id == item.id).values(**values))
    if kwargs.get("family", "default") is not None:
        _variant(scene, item)
    scene.db.flush()
    return item


def _symbol(scene, code):
    return scene.db.execute(
        sa.select(UnitOfMeasure.symbol).where(UnitOfMeasure.id == _unit_id(scene.db, code))
    ).scalar_one()


def _price_error(scene, item, *, at=None, reason="опечатка"):
    values = {"position_item_id": item.id, "reason": reason, "set_by": scene.admin.id}
    if at is not None:
        values["set_at"] = at
    scene.db.add(PriceErrorMark(**values))
    scene.db.flush()


def _not_error(scene, item, *, direction="down", neighbor=None, at=None, reason="другое изделие", user=None):
    values = {
        "position_item_id": item.id,
        "reason": reason,
        "set_by": (user or scene.admin).id,
    }
    if neighbor is not None:
        values.update(kind="twin", neighbor_item_id=neighbor.id)
    else:
        values.update(kind="outlier", family_id=scene.family().id, unit_id=_unit_id(scene.db, "M2"), direction=direction)
    if at is not None:
        values["set_at"] = at
    scene.db.add(NotErrorMark(**values))
    scene.db.flush()


def _three(scene):
    """«Три договора 10, 100, 100»: дешёвая строка — выброс вниз."""
    a, b, c = (_contract(scene) for _ in range(3))
    cheap = _item(scene, a, D("10"))
    _item(scene, b, D("100"))
    _item(scene, c, D("100"))
    return SimpleNamespace(cheap=cheap, a=a, b=b, c=c)


def _get(client, url=URL, **params):
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(rows):
    return [row["position_item_id"] for row in rows]


def _wide(scene):
    """Шесть договоров: четыре по 100, договор Y с одной строкой за 10 и договор X со строками 1500 и 1000
    (между ними — строка без семьи). Выбросы: Y вниз, обе строки X вверх."""
    for _ in range(4):
        _item(scene, _contract(scene), D("100"))
    y = _contract(scene)
    cheap = _item(scene, y, D("10"))
    x = _contract(scene)
    first = _item(scene, x, D("1500"))
    _item(scene, x, D("77"), family=None)
    second = _item(scene, x, D("1000"))
    return SimpleNamespace(x=x, y=y, cheap=cheap, first=first, second=second)


# ---------------------------------------------------------------------------
#  Пункт выброса
# ---------------------------------------------------------------------------


class TestOutlierItem:
    def test_facts_head_and_group_prices(self, scene, admin_client):
        s = _three(scene)
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == s.cheap.id).values(item_number_in_proposal="7.1")
        )
        scene.db.flush()
        body = _get(admin_client)
        assert _ids(body["unresolved"]) == [s.cheap.id] and body["resolved"] == []
        item = body["unresolved"][0]
        assert item["contract"]["id"] == s.a.contract_id
        assert (item["item_number"], item["latest"], item["unit"]) == ("7.1", True, _symbol(scene, "M2"))
        assert (item["net"], item["gross"], item["raw"], item["price_note"]) == ("10", None, None, None)
        family, unit = scene.family(), _unit_id(scene.db, "M2")
        assert item["facts"] == [
            {
                "basis": {"kind": "outlier", "family_id": family.id, "unit_id": unit, "direction": "down"},
                "kind": "outlier",
                "direction": "down",
                "ratio": "0.1",
                "usual": "100",
                "family_title": family.title,
                "unit": _symbol(scene, "M2"),
                "contracts": 3,
            }
        ]
        assert item["previous"] == []
        prices = item["prices"]
        assert (prices["family_title"], prices["unit"], prices["usual"]) == (
            family.title, _symbol(scene, "M2"), "100",
        )
        assert [(c["contract_id"], c["lines"], c["price"], c["low"], c["high"], c["is_own"]) for c in prices["contracts"]] == [
            (s.a.contract_id, 1, "10", "10", "10", True),
            (s.b.contract_id, 1, "100", "100", "100", False),
            (s.c.contract_id, 1, "100", "100", "100", False),
        ]
        assert all(c["object_title"] for c in prices["contracts"])

    @pytest.mark.parametrize(
        ("before", "after", "expected_before", "expected_after"),
        [(5, 5, 3, 3), (0, 5, 0, 3), (5, 0, 3, 0), (1, 2, 1, 2)],
    )
    def test_neighbors_are_up_to_three_lines_without_chapters(
        self, scene, admin_client, before, after, expected_before, expected_after
    ):
        a, b, c = (_contract(scene) for _ in range(3))
        earlier = [_item(scene, a, D(1000 + i), family=None) for i in range(before)]
        if before:  # глава перед строкой места не занимает
            scene.f.PositionItemFactory.create(
                proposal=a.proposal, is_chapter=True, unit_cost_total=None, total_cost_total=None
            )
        cheap = _item(scene, a, D("10"))
        later = [_item(scene, a, D(2000 + i), family=None) for i in range(after)]
        _item(scene, b, D("100"))
        _item(scene, c, D("100"))
        neighbors = _get(admin_client)["unresolved"][0]["neighbors"]
        expected = _ids_of(earlier[before - expected_before :] if before else []) + [cheap.id] + _ids_of(
            later[:expected_after]
        )
        assert _ids(neighbors) == expected
        assert [row["position_item_id"] for row in neighbors if row["is_self"]] == [cheap.id]
        assert not any(row["is_twin"] for row in neighbors)

    def test_where_is_the_article_and_the_path_of_the_section(self, scene, admin_client):
        s = _three(scene)
        chapter = scene.f.PositionItemFactory.create(
            proposal=s.a.proposal, is_chapter=True, job_title_in_proposal="Полы", item_number_in_proposal="3",
            unit_cost_total=None, total_cost_total=None,
        )
        root = WorkCategory(code="901", title="Отделка", sort_order=901)
        scene.db.add(root)
        scene.db.flush()
        category = WorkCategory(code="901.1", title="Полы и покрытия", parent_id=root.id, sort_order=1)
        scene.db.add(category)
        scene.db.flush()
        scene.db.execute(sa.update(PositionItem).where(PositionItem.id == chapter.id).values(work_category_id=category.id, category_source="manual"))
        scene.db.execute(
            sa.update(PositionItem).where(PositionItem.id == s.cheap.id).values(chapter_item_id=chapter.id)
        )
        scene.db.flush()
        where = _get(admin_client)["unresolved"][0]["where"]
        assert where["chapter_path"] == [{"chapter_item_id": chapter.id, "title": "Полы", "number": "3"}]
        assert where["article"] == {
            "id": category.id,
            "code": "901.1",
            "title": "Полы и покрытия",
            "chain": [
                {"id": root.id, "code": "901", "title": "Отделка"},
                {"id": category.id, "code": "901.1", "title": "Полы и покрытия"},
            ],
        }

    def test_a_line_outside_any_section_has_an_empty_place(self, scene, admin_client):
        _three(scene)
        assert _get(admin_client)["unresolved"][0]["where"] == {"article": None, "chapter_path": []}


def _ids_of(items):
    return [item.id for item in items]


# ---------------------------------------------------------------------------
#  Пункт двойника
# ---------------------------------------------------------------------------


class TestTwinItem:
    def _scene(self, scene, *, vat=D("0")):
        a = _contract(scene, vat_rate=vat)
        line = _item(scene, a, D("500"), number="4.1")
        twin = _item(scene, a, D("500"), unit="PCS", family=None, number="4.2")
        scene.db.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == twin.id)
            .values(unit_cost_materials=D("300"), unit_cost_works=D("200"), total_cost_total=D("5000.456"))
        )
        scene.db.flush()
        return line, twin

    def test_neighbor_outside_the_pool_carries_its_numbers(self, scene, admin_client):
        line, twin = self._scene(scene)
        body = _get(admin_client)
        assert _ids(body["unresolved"]) == [line.id]  # соседка без семьи сама в списке не стоит
        item = body["unresolved"][0]
        expected = {
            "position_item_id": twin.id,
            "item_number": "4.2",
            "title": twin.job_title_in_proposal,
            "unit": _symbol(scene, "PCS"),
            "quantity": "10",
            "unit_price": "500",
            "materials": "300",
            "works": "200",
            "total": "5000.46",
            "gross": False,
            "is_self": False,
            "is_twin": True,
        }
        assert item["facts"] == [
            {"basis": {"kind": "twin", "neighbor_item_id": twin.id}, "kind": "twin", "neighbor": expected}
        ]
        row = next(r for r in item["neighbors"] if r["position_item_id"] == twin.id)
        assert row == expected
        me = next(r for r in item["neighbors"] if r["position_item_id"] == line.id)
        assert (me["is_self"], me["is_twin"]) == (True, False)

    def test_neighbor_money_is_net_of_the_estimate_base(self, scene, admin_client):
        _, twin = self._scene(scene, vat=D("20"))
        row = _get(admin_client)["unresolved"][0]["facts"][0]["neighbor"]
        assert row["unit_price"] == format(gross_to_net(D("500"), D("20")), "f")
        assert row["materials"] == format(gross_to_net(D("300"), D("20")), "f")
        assert row["total"] == "4167.05"  # 5000.456 / 1.2 = 4167.0466…, до копеек
        assert row["gross"] is False


class TestNeighborRowMoney:
    """`neighbor_row_view` — деньги по правилам §4 п. 2: конечное значение (и 0, и отрицательное) — нетто,
    база неизвестна — как в смете с `gross: true`, нечисловое — как хранится, пусто — `null`."""

    @staticmethod
    def _details(**kwargs):
        values = dict(
            position_item_id=1, item_number=None, title="Работа", unit=None, quantity=None,
            unit_cost_total=None, unit_cost_materials=None, unit_cost_works=None, total_cost_total=None,
            vat_base=D("20"), category_id=None, chapter_item_id=None,
        )
        values.update(kwargs)
        return LineDetails(**values)

    def test_zero_and_negative_are_values_and_empty_is_null(self):
        row = neighbor_row_view(
            self._details(unit_cost_total=D("0"), unit_cost_materials=D("-120"), unit_cost_works=None,
                          total_cost_total=D("-1200.004")),
            is_self=False, is_twin=False,
        )
        assert (row["unit_price"], row["materials"], row["works"]) == ("0", "-100", None)
        assert row["total"] == "-1000.00"
        assert row["gross"] is False

    def test_unknown_base_is_as_in_the_estimate_with_the_flag(self):
        row = neighbor_row_view(
            self._details(vat_base=None, unit_cost_total=D("120"), unit_cost_materials=D("-5"), total_cost_total=D("1200.005")),
            is_self=True, is_twin=False,
        )
        assert (row["unit_price"], row["materials"], row["total"]) == ("120", "-5", "1200.01")
        assert row["gross"] is True and row["is_self"] is True

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
    @pytest.mark.parametrize("base", [D("20"), None])
    def test_non_finite_is_as_stored(self, raw, base):
        row = neighbor_row_view(
            self._details(vat_base=base, unit_cost_total=D(raw), unit_cost_works=D(raw), total_cost_total=D(raw)),
            is_self=False, is_twin=False,
        )
        assert (row["unit_price"], row["works"], row["total"]) == (raw, raw, raw)


# ---------------------------------------------------------------------------
#  «Разобраны» (критическое §7 п. 6, 6а)
# ---------------------------------------------------------------------------


class TestResolved:
    def test_price_error_on_the_cheap_line_leaves_no_facts_and_the_line_resolved(self, scene, admin_client):
        s = _three(scene)
        response = admin_client.put(f"/api/v1/position-items/{s.cheap.id}/price-error", json={"reason": "лишний ноль"})
        assert response.status_code == 200
        body = _get(admin_client)
        assert body["unresolved"] == [] and body["contracts"] == []
        (item,) = body["resolved"]
        assert item["position_item_id"] == s.cheap.id
        assert item["facts"] == [] and item["not_error"] == []
        assert item["price_error"]["kind"] == "price_error" and item["price_error"]["reason"] == "лишний ноль"
        assert item["price_error"]["set_by"]["id"] == admin_client.user.id
        assert (item["net"], item["price_note"]) == ("10", None)

    def test_price_error_on_a_kit(self, scene, admin_client):
        contract = _contract(scene)
        kit = _item(scene, contract, D("5000"))
        scene.override(kit, "lump")
        _price_error(scene, kit)
        (item,) = _get(admin_client)["resolved"]
        assert item["position_item_id"] == kit.id and item["facts"] == []
        assert (item["net"], item["price_note"], item["latest"]) == ("5000", None, True)

    def test_price_error_on_a_line_of_the_previous_estimate(self, scene, admin_client, factories):
        contract = factories.ContractFactory.create()
        estimates = []
        for amendment in (None, 1):
            estimate = factories.EstimateFactory.create(contract=contract, amendment_no=amendment)
            lot = factories.LotFactory.create(estimate=estimate)
            proposal = factories.ProposalFactory.create(lot=lot, contractor=contract.contractor, vat_rate=D("0"))
            estimates.append(SimpleNamespace(estimate=estimate, proposal=proposal, contract_id=contract.id))
        old = _item(scene, estimates[0], D("100"))
        new = _item(scene, estimates[1], D("100"))
        _price_error(scene, old)
        _price_error(scene, new)
        body = _get(admin_client)
        by_id = {row["position_item_id"]: row for row in body["resolved"]}
        assert set(by_id) == {old.id, new.id}
        assert (by_id[old.id]["latest"], by_id[new.id]["latest"]) == (False, True)
        assert by_id[old.id]["facts"] == []

    def test_price_error_on_a_line_with_unknown_vat_base(self, scene, admin_client):
        contract = _contract(scene, vat_rate=None)
        line = _item(scene, contract, D("120"))
        _price_error(scene, line)
        (item,) = _get(admin_client)["resolved"]
        assert item["position_item_id"] == line.id and item["facts"] == []
        assert (item["net"], item["gross"], item["raw"], item["price_note"]) == (None, "120", None, "vat_unknown")

    @pytest.mark.parametrize(
        ("price", "note", "raw"),
        [
            (None, "no_price", None),
            (D("0"), "no_price", None),
            (D("-100"), "negative", "-100"),
            (D("NaN"), "not_finite", "NaN"),
            (D("Infinity"), "not_finite", "Infinity"),
            (D("-Infinity"), "not_finite", "-Infinity"),
        ],
    )
    def test_unusable_price_of_a_marked_line_gets_its_own_note(self, scene, admin_client, price, note, raw):
        contract = _contract(scene)
        line = _item(scene, contract, price)
        _price_error(scene, line)
        (item,) = _get(admin_client)["resolved"]
        assert item["position_item_id"] == line.id
        assert (item["price_note"], item["raw"], item["net"], item["gross"]) == (note, raw, None, None)

    def test_a_known_finite_price_outside_the_pool_is_net_of_the_base(self, scene, admin_client):
        contract = _contract(scene, vat_rate=D("20"))
        line = _item(scene, contract, D("120"), family=None)  # не кандидат: семьи нет
        _price_error(scene, line)
        (item,) = _get(admin_client)["resolved"]
        assert (item["net"], item["price_note"]) == ("100", None)

    def test_the_five_notes_are_five_separate_branches_of_price_head(self):
        def head(price, base=D("0")):
            return price_head(DecidedLine(1, 1, True, price, base), None)

        notes = [head(None)["price_note"], head(D("0"))["price_note"], head(D("-100"))["price_note"],
                 head(D("NaN"))["price_note"], head(D("120"), None)["price_note"], head(D("120"))["price_note"]]
        assert notes == ["no_price", "no_price", "negative", "not_finite", "vat_unknown", None]
        # неизвестная база не скрывает отрицательное и нечисловое значение
        assert head(D("-100"), None)["price_note"] == "negative"
        assert head(D("NaN"), None)["price_note"] == "not_finite"

    def test_not_error_decisions_carry_basis_reason_author_and_time(self, scene, admin_client, factories):
        s = _three(scene)
        _not_error(scene, s.cheap, at=T0, reason="другое изделие")
        body = _get(admin_client)
        assert body["unresolved"] == []
        (item,) = body["resolved"]
        assert item["price_error"] is None
        family = scene.family()
        assert [f["kind"] for f in item["facts"]] == ["outlier"]  # правило её всё ещё ловит
        (decision,) = item["not_error"]
        assert decision == {
            "basis": {
                "kind": "outlier", "family_id": family.id, "unit_id": _unit_id(scene.db, "M2"),
                "direction": "down", "family_title": family.title, "unit": _symbol(scene, "M2"),
            },
            "reason": "другое изделие",
            "set_by": {"id": admin_client.user.id, "email": admin_client.user.email},
            "set_at": decision["set_at"],
        }
        assert dt.datetime.fromisoformat(decision["set_at"]) == T0

    def test_twin_decision_names_the_neighbor(self, scene, admin_client):
        contract = _contract(scene)
        line = _item(scene, contract, D("500"))
        twin = _item(scene, contract, D("500"), unit="PCS", family=None, number="9.9")
        _not_error(scene, line, neighbor=twin)
        (item,) = _get(admin_client)["resolved"]
        assert item["not_error"][0]["basis"] == {
            "kind": "twin",
            "neighbor_item_id": twin.id,
            "neighbor": {"item_number": "9.9", "title": twin.job_title_in_proposal},
        }

    def test_decision_on_another_basis_keeps_the_line_unresolved_with_previous(self, scene, admin_client):
        s = _three(scene)
        _not_error(scene, s.cheap, direction="up", at=T0)
        body = _get(admin_client)
        assert body["resolved"] == []
        (item,) = body["unresolved"]
        assert [f["direction"] for f in item["facts"]] == ["down"]
        assert [d["basis"]["direction"] for d in item["previous"]] == ["up"]

    def test_the_line_is_in_exactly_one_block(self, scene, admin_client):
        w = _wide(scene)
        _not_error(scene, w.first, direction="up", at=T0)
        _price_error(scene, w.cheap)
        body = _get(admin_client)
        everything = _ids(body["unresolved"]) + _ids(body["resolved"])
        assert len(everything) == len(set(everything))
        assert w.first.id in _ids(body["resolved"]) and w.second.id in _ids(body["unresolved"])

    def test_resolved_are_ordered_by_the_latest_decision_newest_first(self, scene, admin_client):
        contract = _contract(scene)
        lines = [_item(scene, contract, D("100"), family=None) for _ in range(4)]
        _price_error(scene, lines[0], at=T0)
        _price_error(scene, lines[1], at=T0 + dt.timedelta(hours=2))
        _price_error(scene, lines[2], at=T0 + dt.timedelta(hours=1))
        # самая старая отметка, но свежее решение «не ошибка» — по свежему
        _price_error(scene, lines[3], at=T0 - dt.timedelta(days=3))
        _not_error(scene, lines[3], at=T0 + dt.timedelta(hours=3))
        order = _ids(_get(admin_client)["resolved"])
        assert order == [lines[3].id, lines[1].id, lines[2].id, lines[0].id]

    def test_equal_times_fall_back_to_the_item_id(self, scene, admin_client):
        contract = _contract(scene)
        lines = [_item(scene, contract, D("100"), family=None) for _ in range(3)]
        for line in reversed(lines):
            _price_error(scene, line, at=T0)
        assert _ids(_get(admin_client)["resolved"]) == sorted(_ids_of(lines))


# ---------------------------------------------------------------------------
#  Порядок, фильтр и счётчики
# ---------------------------------------------------------------------------


class TestOrderFilterAndCounts:
    def test_unresolved_are_by_contract_then_by_place(self, scene, admin_client):
        w = _wide(scene)
        body = _get(admin_client)
        assert _ids(body["unresolved"]) == [w.cheap.id, w.first.id, w.second.id]
        assert [row["contract"]["id"] for row in body["unresolved"]] == [w.y.contract_id] + [w.x.contract_id] * 2

    def test_contracts_are_those_with_unresolved_with_their_counts(self, scene, admin_client):
        w = _wide(scene)
        _not_error(scene, w.first, direction="up")
        contracts = _get(admin_client)["contracts"]
        assert [(c["contract_id"], c["unresolved"]) for c in contracts] == [
            (w.y.contract_id, 1), (w.x.contract_id, 1),
        ]
        assert all(c["number"] and c["object_title"] for c in contracts)

    def test_filter_narrows_both_blocks_but_not_the_contracts(self, scene, admin_client):
        w = _wide(scene)
        _not_error(scene, w.first, direction="up")
        _price_error(scene, w.cheap)
        full = _get(admin_client)
        narrowed = _get(admin_client, contract_id=w.x.contract_id)
        assert _ids(narrowed["unresolved"]) == [w.second.id]
        assert _ids(narrowed["resolved"]) == [w.first.id]
        assert narrowed["contracts"] == full["contracts"]
        other = _get(admin_client, contract_id=w.y.contract_id)
        assert other["unresolved"] == [] and _ids(other["resolved"]) == [w.cheap.id]

    def test_unknown_contract_is_404(self, admin_client):
        assert admin_client.get(URL, params={"contract_id": 999_999_999}).status_code == 404

    def test_counts_are_unresolved_only_and_equal_the_list_and_its_filter(self, scene, admin_client):
        w = _wide(scene)
        _not_error(scene, w.first, direction="up")
        counts = _get(admin_client, COUNTS)
        body = _get(admin_client)
        assert counts["total"] == len(body["unresolved"]) == 2
        assert counts["by_contract"] == {str(w.y.contract_id): 1, str(w.x.contract_id): 1}
        for contract_id, number in ((w.y.contract_id, 1), (w.x.contract_id, 1)):
            filtered = _get(admin_client, contract_id=contract_id)
            assert counts["by_contract"][str(contract_id)] == len(filtered["unresolved"]) == number

    def test_counts_without_suspicion_are_empty(self, admin_client):
        assert _get(admin_client, COUNTS) == {"total": 0, "by_contract": {}}

    def test_contracts_with_zero_are_not_listed(self, scene, admin_client):
        w = _wide(scene)
        _price_error(scene, w.cheap)
        counts = _get(admin_client, COUNTS)
        assert str(w.y.contract_id) not in counts["by_contract"]


# ---------------------------------------------------------------------------
#  Права
# ---------------------------------------------------------------------------


class TestPermissions:
    @pytest.mark.parametrize("url", [URL, COUNTS])
    def test_member_gets_403(self, member_client, url):
        assert member_client.get(url).status_code == 403


# ---------------------------------------------------------------------------
#  Кэш индекса (§7 п. 10)
# ---------------------------------------------------------------------------


def _count_statements(db, action):
    statements: list[str] = []

    def spy(conn, cursor, statement, *args):
        statements.append(statement)

    connection = db.connection()
    sa.event.listen(connection, "before_cursor_execute", spy)
    try:
        result = action()
    finally:
        sa.event.remove(connection, "before_cursor_execute", spy)
    return result, statements


def _warm(monkeypatch):
    """Кэш включён и прогрет маркером: `get` вернёт его, пока никто не сбросил."""
    monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
    suspicion_cache.invalidate()
    assert suspicion_cache.get(lambda: "warm") == "warm"
    assert suspicion_cache.get(lambda: "other") == "warm"  # предусловие: значение держится


def _is_cold() -> bool:
    return suspicion_cache.get(lambda: "fresh") == "fresh"


class TestIndexCache:
    def test_second_call_does_not_read_the_database(self, scene, monkeypatch):
        _three(scene)
        monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
        suspicion_cache.invalidate()
        first, built = _count_statements(scene.db, lambda: suspicion_index(scene.db))
        second, repeated = _count_statements(scene.db, lambda: suspicion_index(scene.db))
        assert built, "первый вызов обязан построить индекс из базы"
        assert repeated == [] and second is first
        assert len(first.facts) == 1

    def test_listing_does_not_rebuild_the_index_either(self, scene, admin_client, monkeypatch):
        _three(scene)
        monkeypatch.setattr(settings, "SUSPICION_CACHE_TTL_S", 300)
        suspicion_cache.invalidate()
        _get(admin_client)
        _, statements = _count_statements(scene.db, lambda: suspicion_index(scene.db))
        assert statements == []

    def test_price_error_put_resets_it(self, scene, admin_client, monkeypatch):
        item = _contract_item(scene)
        _warm(monkeypatch)
        assert admin_client.put(f"/api/v1/position-items/{item.id}/price-error", json={"reason": "r"}).status_code == 200
        assert _is_cold()

    def test_price_error_delete_resets_it(self, scene, admin_client, monkeypatch):
        item = _contract_item(scene)
        admin_client.put(f"/api/v1/position-items/{item.id}/price-error", json={"reason": "r"})
        _warm(monkeypatch)
        assert admin_client.delete(f"/api/v1/position-items/{item.id}/price-error").status_code == 204
        assert _is_cold()

    def test_lump_sum_put_resets_it(self, scene, admin_client, monkeypatch):
        item = _item(scene, _contract(scene), D("100"))
        _warm(monkeypatch)
        assert admin_client.put(f"/api/v1/position-items/{item.id}/lump-sum", json={"verdict": "lump"}).status_code == 200
        assert _is_cold()

    def test_lump_sum_delete_resets_it(self, scene, admin_client, monkeypatch):
        item = _item(scene, _contract(scene), D("100"))
        admin_client.put(f"/api/v1/position-items/{item.id}/lump-sum", json={"verdict": "lump"})
        _warm(monkeypatch)
        assert admin_client.delete(f"/api/v1/position-items/{item.id}/lump-sum").status_code == 204
        assert _is_cold()

    def test_contract_delete_resets_it(self, scene, admin_client, monkeypatch):
        contract = scene.f.ContractFactory.create()
        _warm(monkeypatch)
        assert admin_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
        assert _is_cold()

    def test_failed_price_error_put_does_not_reset_it(self, admin_client, monkeypatch):
        _warm(monkeypatch)
        assert admin_client.put("/api/v1/position-items/999999999/price-error", json={"reason": "r"}).status_code == 404
        assert suspicion_cache.get(lambda: "fresh") == "warm"

    @pytest.mark.parametrize("fails", [False, True])
    def test_end_of_an_import_job_resets_it(self, monkeypatch, fails):
        def run(*args, **kwargs):
            if fails:
                raise RuntimeError("импорт упал")

        monkeypatch.setattr(import_pipeline, "_run_import_job", run)
        _warm(monkeypatch)
        if fails:
            with pytest.raises(RuntimeError):
                import_pipeline.run_import_job(1, session_factory=None, storage=None)
        else:
            import_pipeline.run_import_job(1, session_factory=None, storage=None)
        assert _is_cold()

    def test_a_tender_decision_write_does_not_reset_it(self, scene, admin_client, monkeypatch):
        """Запись решения торга (здесь — «не аналог в этом тендере») не из закрытого списка: индекс остаётся,
        хотя прослойка `main.py` сбрасывает на любой не-GET другие кэши."""
        kp = _kp(scene)
        item = _contract_item(scene)
        _warm(monkeypatch)
        assert admin_client.put(_na_url(kp, item.id), json={"reason": "иное"}).status_code == 200
        assert suspicion_cache.get(lambda: "fresh") == "warm"

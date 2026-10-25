"""Разбор участника и панель строки «Против нашей цены» (спека 4а §2.12, §2.14):
`GET …/our-price/offers/{offer_id}` и `GET …/offers/{offer_id}/lines/{position_item_id}`.

Сцены строятся помощниками экрана этапа; названия и числа выдуманы. Эталоны —
числа, посчитанные руками. Ставки истории — 0 %, поэтому нетто равно валовой цене;
ставки КП — 0 % или 20 %. Каждое правило имеет обе стороны: вход, где оно
срабатывает, и соседний вход, нарушающий ровно одно условие.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import FamilyParameter, SemanticJob, UnitOfMeasure
from tests import comparison_fixtures as fx
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_round_api import (
    FACTOR_FEB_2025,
    FULL_YEARS,
    SERIES_NAME,
    TARGET_MISSING_TEXT,
    Stage,
    _article,
    _cell,
    _dated_history,
    _keys,
    _participant,
    count_statements,
)
from tests.integration.test_our_price_round_api import _get as _round_get
from tests.integration.test_our_price_round_api import _ok as _round_ok
from tests.integration.test_our_price_signal import _job, _pause

pytestmark = pytest.mark.integration

D = Decimal

LINE_KEYS = {
    "position_item_id",
    "article_id",
    "title",
    "unit",
    "quantity",
    "section_ref",
    "family",
    "variant_label",
    "secondary",
    "state",
    "waiting_admin",
    "kind",
    "kp",
    "rung",
    "rungs",
    "range",
    "ours",
    "position",
    "to_ours",
    "bargain",
    "below",
    "group",
    "unit_mismatch",
}
PANEL_KEYS = {"line", "group_lines", "contracts", "vat_dropped"}
TOLERANCE = D("1e-20")


def _no_float(text: str):
    raise AssertionError(f"в ответе дробный литерал JSON: {text}")


def _json(response) -> dict:
    """Разбор без `float`: любой дробный литерал роняет тест."""
    return json.loads(response.text, parse_float=_no_float)


def _url(tender, rnd, part) -> str:
    return f"/api/v1/tenders/{tender.id}/rounds/{rnd.id}/our-price/offers/{part.offer.id}"


def _get(client, tender, rnd, part, **params):
    return client.get(_url(tender, rnd, part), params=params)


def _ok(client, tender, rnd, part, **params) -> dict:
    response = _get(client, tender, rnd, part, **params)
    assert response.status_code == 200, response.text
    return _json(response)


def _panel_get(client, tender, rnd, part, item, **params):
    return client.get(f"{_url(tender, rnd, part)}/lines/{item.id}", params=params)


def _panel(client, tender, rnd, part, item, **params) -> dict:
    response = _panel_get(client, tender, rnd, part, item, **params)
    assert response.status_code == 200, response.text
    return _json(response)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else D(value)


def _pair(pair: dict | None) -> tuple[Decimal, Decimal | None] | None:
    return None if pair is None else (D(pair["net"]), _dec(pair["shown"]))


def _line_of(body: dict, item) -> dict:
    (found,) = (line for line in body["lines"] if line["position_item_id"] == item.id)
    return found


def _offer_article(body: dict, stage_: Stage, cat: str) -> dict:
    (found,) = (a for a in body["articles"] if a["work_category_id"] == stage_.cats[cat].id)
    return found


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


_OWN = object()


def _put(stage_, proposal, cat, unit_cost, *, qty=D("10"), total=None, variant=_OWN, kind=None, family=None):
    """Строка в главе статьи `cat`; `variant=None` — без семьи и варианта, иначе
    вариант семьи сцены (по умолчанию её собственный), `family` — другая семья."""
    variant = stage_.variant if variant is _OWN else variant
    item = stage_.world.line(
        proposal,
        chapter=stage_.chapter(proposal, cat),
        unit_cost=unit_cost,
        total=unit_cost * qty if total is None else total,
        suggested=qty,
        quantity=qty,
        family=(family or stage_.family) if variant is not None else None,
        variant=variant,
        kind=kind,
    )
    stage_.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == item.context_id))
    return item


def _history(stage_, unit_cost, *, vat=D("0"), rate_class="own", contractor=None, cat="R", **kwargs):
    """Договор истории с одной строкой; `rate_class="own"` — класс тендера."""
    contract = stage_.world.contract(
        area=D("1000"),
        rate_class=stage_.rate_class if rate_class == "own" else rate_class,
        contractor=contractor,
    )
    estimate, proposal = stage_.world.estimate(contract, vat_rate=vat)
    _put(stage_, proposal, cat, unit_cost, **kwargs)
    stage_.db.flush()
    return contract, estimate, proposal


def _second_variant(stage_, text="80 мм"):
    param = stage_.db.execute(
        sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage_.schema.id)
    ).scalar_one()
    return stage_.world.variant(stage_.family, stage_.schema, {1: stage_.world.value(param, text)})


def _other_family(stage_):
    """Вторая семья с вариантом, у которой в договорах ничего нет: `(семья, вариант)`."""
    family, schema = stage_.world.family("Семья Я")
    param = stage_.world.param(schema, 1, "Толщина")
    return family, stage_.world.variant(family, schema, {1: stage_.world.value(param, "50 мм")})


def _history_pair(stage_):
    """История класса: ставки 100 и 140 за единицу."""
    stage_.history(D("100"))
    stage_.history(D("140"))


def _scene(stage_):
    """История класса: ставки 100 и 140; КП участника (НДС 20 %): нетто 120 за единицу."""
    stage_.history(D("100"))
    stage_.history(D("140"))
    rnd = stage_.round()
    part = stage_.participant(rnd, "Подрядчик А")
    item = stage_.kp(part, D("144"))
    return rnd, part, item


def _system_scene(stage_, history_totals=(100000, 140000), kp_totals=(30000, 90000)):
    """Системы: история — ₽/м² 100 и 140 (площадь договоров 1000); КП без НДС с
    двумя системными строками одного варианта, суммы нетто 30 000 и 90 000."""
    for total in history_totals:
        _history(stage_, D("1"), total=D(total), kind="SYSTEM")
    rnd = stage_.round()
    part = stage_.participant(rnd, "Подрядчик А", vat=D("0"))
    items = [_put(stage_, part.proposal, "R", D("1"), total=D(total), kind="SYSTEM") for total in kp_totals]
    return rnd, part, items


# ---------------------------------------------------------------------------
#  Маршрут: отказы, доступ, чтение без записи, без float
# ---------------------------------------------------------------------------


class TestOfferRoute:
    def test_unknown_tender_is_404(self, member_client, stage):
        rnd, part, _ = _scene(stage)

        response = member_client.get(f"/api/v1/tenders/987654321/rounds/{rnd.id}/our-price/offers/{part.offer.id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Тендер 987654321 не найден."

    def test_round_of_another_tender_is_404(self, member_client, stage, factories):
        _, part, _ = _scene(stage)
        foreign = factories.TenderRoundFactory.create()

        assert _get(member_client, stage.tender, foreign, part).status_code == 404

    def test_unknown_offer_is_404(self, member_client, stage):
        rnd, _, _ = _scene(stage)

        response = member_client.get(f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}/our-price/offers/987654321")

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_offer_of_another_round_of_the_same_tender_is_404(self, member_client, stage):
        _, part, _ = _scene(stage)
        other = stage.round()

        assert _get(member_client, stage.tender, other, part).status_code == 404

    def test_offer_of_another_tender_is_404(self, member_client, stage, factories):
        rnd, _, _ = _scene(stage)
        foreign_offer = factories.OfferFactory.create()

        response = member_client.get(
            f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}/our-price/offers/{foreign_offer.id}"
        )

        assert response.status_code == 404

    def test_foreign_offer_with_a_series_is_404_not_422(self, member_client, stage, factories, db_session):
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage.round(held_on=None)
        foreign_offer = factories.OfferFactory.create()

        response = member_client.get(
            f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}/our-price/offers/{foreign_offer.id}",
            params={"inflation_series_id": series_id},
        )

        assert response.status_code == 404

    def test_participant_without_an_estimate_is_404(self, member_client, stage):
        rnd = stage.round()
        empty = stage.participant(rnd, "Подрядчик Б", estimate=False)

        assert _get(member_client, stage.tender, rnd, empty).status_code == 404

    def test_the_neighbour_with_an_estimate_is_200(self, member_client, stage):
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик Б", estimate=False)
        part = stage.participant(rnd, "Подрядчик А")
        stage.kp(part, D("144"))

        assert _get(member_client, stage.tender, rnd, part).status_code == 200

    def test_member_reads(self, member_client, stage):
        """Только `member_client`: фикстуры ролей делят переопределение
        `get_current_user` одного приложения, и с `admin_client` в том же тесте
        запрос «от member» уходит от admin."""
        rnd, part, _ = _scene(stage)

        assert _get(member_client, stage.tender, rnd, part).status_code == 200

    def test_the_endpoint_writes_nothing(self, member_client, stage, db_session):
        rnd, part, _ = _scene(stage)
        with count_statements(db_session) as seen:
            assert _get(member_client, stage.tender, rnd, part).status_code == 200
            assert _get(member_client, stage.tender, rnd, part, inflation_series_id=987654321).status_code == 404

        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert seen and writes == []

    def test_response_has_no_float_literal(self, member_client, stage):
        rnd, part, _ = _scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)  # `_json` падает на любом дробном литерале

        assert body["lines"] and body["articles"]


# ---------------------------------------------------------------------------
#  Шапка разбора
# ---------------------------------------------------------------------------


class TestOfferHeader:
    def test_head_blocks_match_the_round_screen(self, member_client, stage):
        rnd, part, _ = _scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)
        round_body = _round_ok(member_client, stage.tender, rnd)

        assert set(body) == {"tender", "round", "progress", "history", "participants", "articles", "total", "lines"}
        assert body["tender"] == round_body["tender"]
        assert body["round"] == round_body["round"]
        assert body["history"]["contracts"] == round_body["history"]["contracts"]
        assert body["participants"] == round_body["participants"]

    def test_history_is_shown_in_the_rate_of_this_participant(self, member_client, stage):
        """На экране этапа ставки двух участников разные — общая колонка в нетто; в
        разборе участник один, и история в его ставке."""
        stage.history(D("100"))
        rnd = stage.round()
        first = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        second = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(first, D("144"))
        stage.kp(second, D("120"))

        assert _round_ok(member_client, stage.tender, rnd)["history"]["shown_rate"] is None
        body = _ok(member_client, stage.tender, rnd, first)

        assert _dec(body["history"]["shown_rate"]) == D("20")
        (row,) = body["history"]["per_sqm"]
        assert (_dec(row["net"]), _dec(row["shown"])) == (D("1"), D("1.2"))

    def test_participant_with_an_unknown_base_has_no_shown_rate(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        stage.kp(part, D("144"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert body["history"]["shown_rate"] is None
        assert body["history"]["per_sqm"][0]["shown"] is None

    def test_progress_counts_the_lines_of_this_kp_only(self, member_client, stage, db_session):
        rnd = stage.round()
        first = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        second = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.world.chapter(first.proposal, None)  # глава — не строка
        running = stage.kp(first, D("100"), variant=False)
        stuck = stage.kp(first, D("100"), variant=False)
        stage.kp(first, D("100"), variant=False)  # ни сигнала, ни варианта
        stage.kp(first, D("100"))
        other = stage.kp(second, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=running.context_id)
        _job(db_session, "family_suggestion", "error", context_id=stuck.context_id)
        _job(db_session, "family_suggestion", "pending", context_id=other.context_id)

        mine = _ok(member_client, stage.tender, rnd, first)["progress"]
        theirs = _ok(member_client, stage.tender, rnd, second)["progress"]

        assert mine == {"lines_total": 4, "in_progress": 1, "waiting_admin": 1}
        assert theirs == {"lines_total": 1, "in_progress": 1, "waiting_admin": 0}
        round_progress = _round_ok(member_client, stage.tender, rnd)["progress"]
        assert mine["lines_total"] + theirs["lines_total"] == round_progress["lines_total"] == 5
        assert mine["in_progress"] + theirs["in_progress"] == round_progress["in_progress"] == 2

    def test_all_lines_come_in_the_order_of_the_estimate_without_chapters(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        items = [stage.kp(part, D("100"), cat="R2" if index % 2 else "R") for index in range(7)]

        body = _ok(member_client, stage.tender, rnd, part)

        assert [line["position_item_id"] for line in body["lines"]] == [item.id for item in items]
        assert body["progress"]["lines_total"] == 7


# ---------------------------------------------------------------------------
#  Форма строки и деньги
# ---------------------------------------------------------------------------


class TestLineForm:
    def test_work_line_has_the_whole_form(self, member_client, stage):
        rnd, part, item = _scene(stage)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert set(line) == LINE_KEYS
        assert line["article_id"] == stage.cats["R"].id
        assert line["title"] == item.job_title_in_proposal
        assert line["unit"] == stage.db.get(UnitOfMeasure, item.unit_id).symbol
        assert _dec(line["quantity"]) == D("10")
        assert line["section_ref"] == (item.item_number_in_proposal or item.position_key_in_proposal)
        assert line["family"] == "Семья Т"
        assert line["variant_label"] == "Семья Т · 50 мм"
        assert line["secondary"] == []
        assert line["state"] == "comparable"
        assert line["waiting_admin"] is False
        assert line["kind"] == "work"
        assert line["group"] is None
        assert line["unit_mismatch"] is None

    def test_article_id_is_the_root_of_the_article(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        first = stage.kp(part, D("100"), cat="R")
        second = stage.kp(part, D("100"), cat="R2")
        loose = stage.kp(part, D("100"), cat=None)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, first)["article_id"] == stage.cats["R"].id
        assert _line_of(body, second)["article_id"] == stage.cats["R2"].id
        assert _line_of(body, loose)["article_id"] is None

    def test_money_fields_are_pairs_with_the_kp_rate(self, member_client, stage):
        rnd, part, item = _scene(stage)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        # нетто 120 за единицу, нетто 1200 за 10; ставка КП 20 %
        assert _pair(line["kp"]["unit_price"]) == (D("120"), D("144"))
        assert line["kp"]["total"] == {"net": "1200.00", "shown": "1440.00"}
        assert {k: _pair(v) for k, v in line["range"].items()} == {
            "min": (D("100"), D("120")),
            "max": (D("140"), D("168")),
            "median": (D("120"), D("144")),
        }
        assert _pair(line["ours"]["value"]) == (D("100"), D("120"))
        assert line["ours"]["from_all_classes"] is False
        assert line["position"] == "in"
        assert line["to_ours"] == {"net": "200.00", "shown": "240.00"}
        assert line["bargain"] == {"net": "200.00", "shown": "240.00"}
        assert line["below"] is None

    def test_unit_rates_are_not_rounded_and_sums_are(self, member_client, stage):
        _history_pair(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144.5"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        net = line["kp"]["unit_price"]["net"]
        assert abs(D(net) - D("144.5") / D("1.2")) < TOLERANCE
        assert len(net.split(".")[1]) > 2
        sums = {"total": line["kp"]["total"], "to_ours": line["to_ours"], "bargain": line["bargain"]}
        for field, pair in sums.items():
            for value in pair.values():
                assert len(value.split(".")[1]) == 2, (field, value)

    def test_range_and_our_rate_are_not_rounded(self, member_client, stage):
        """Ставки договоров с долями копейки: вилка и наша расценка отдаются как есть,
        квантование любого из четырёх полей меняет число."""
        stage.history(D("100.123"))
        stage.history(D("140.456"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("120"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["rung"]["level"] == "v"
        assert {k: D(v["net"]) for k, v in line["range"].items()} == {
            "min": D("100.123"),
            "max": D("140.456"),
            "median": D("120.2895"),
        }
        assert D(line["ours"]["value"]["net"]) == D("100.123")

    def test_our_rate_from_all_classes_is_flagged(self, member_client, stage, factories):
        """Договоров класса тендера на ступени нет — наша расценка по всем классам."""
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        _history(stage, D("100"), rate_class=other_class)
        _history(stage, D("140"), rate_class=other_class)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("120"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert (line["rung"]["level"], line["rung"]["scope"]) == ("v", "all")
        assert _pair(line["ours"]["value"]) == (D("100"), D("100"))
        assert line["ours"]["from_all_classes"] is True

    def test_rungs_list_the_available_rungs_with_contract_counts(self, member_client, stage):
        v2 = _second_variant(stage)
        stage.history(D("100"))
        stage.history(D("140"))
        _history(stage, D("60"), variant=v2)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}
        assert line["rungs"] == [
            {"level": "v", "scope": "cls", "contracts": 2},
            {"level": "v", "scope": "all", "contracts": 2},
            {"level": "f", "scope": "cls", "contracts": 3},
            {"level": "f", "scope": "all", "contracts": 3},
        ]

    def test_a_second_main_parameter_adds_the_p_rungs(self, member_client, stage, world):
        world.param(stage.schema, 2, "Длина")
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert [(r["level"], r["scope"], r["contracts"]) for r in line["rungs"]] == [
            ("v", "cls", 2),
            ("v", "all", 2),
            ("p", "cls", 2),
            ("p", "all", 2),
            ("f", "cls", 2),
            ("f", "all", 2),
        ]

    def test_secondary_parameters_are_named_pairs(self, member_client, stage, world, db_session):
        from models import ContextParameterValue

        color = world.param(stage.schema, 3, "Цвет", role="secondary")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"))
        db_session.add(
            ContextParameterValue(
                context_id=item.context_id,
                schema_id=stage.schema.id,
                parameter_id=color.id,
                value_id=world.value(color, "серый"),
                source="manual",
            )
        )
        db_session.flush()

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["secondary"] == [{"name": "Цвет", "value": "серый"}]

    def test_signed_difference_when_the_kp_is_under_our_rate(self, member_client, stage, factories):
        """Вилка 100-200, наша расценка 200, КП 150, объём 1: разница к нашей расценке
        `-50`, в торг она не идёт, а ниже минимума ступени цена не опускается."""
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        _history(stage, D("200"))
        _history(stage, D("100"), rate_class=other_class)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("150"), qty=D("1"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["rung"] == {"level": "v", "scope": "all", "auto": True, "threshold_met": True}
        assert _pair(line["ours"]["value"]) == (D("200"), D("200"))
        assert {k: _pair(v) for k, v in line["range"].items()}["min"] == (D("100"), D("100"))
        assert line["to_ours"] == {"net": "-50.00", "shown": "-50.00"}
        assert line["bargain"] == {"net": "0.00", "shown": "0.00"}
        assert line["below"] is None

    def test_price_under_the_minimum_gives_a_negative_below_and_a_zero_bargain(self, member_client, stage):
        _history_pair(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("96"))  # нетто 80 против минимума 100

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["position"] == "below"
        assert line["to_ours"] == {"net": "-200.00", "shown": "-240.00"}
        assert line["bargain"] == {"net": "0.00", "shown": "0.00"}
        assert line["below"] == {"net": "-200.00", "shown": "-240.00"}

    def test_price_over_the_maximum_is_above(self, member_client, stage):
        _history_pair(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("192"))  # нетто 160 против максимума 140

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["position"] == "above"
        assert line["to_ours"] == {"net": "600.00", "shown": "720.00"}
        assert line["below"] is None

    def test_unit_mismatch_keeps_a_signed_difference_but_no_bargain_or_below(self, member_client, stage):
        _history_pair(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("840"))  # нетто 700 против медианы 120

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "unit_mismatch"
        assert line["to_ours"] == {"net": "6000.00", "shown": "7200.00"}
        assert line["bargain"] is None
        assert line["below"] is None
        assert abs(D(line["unit_mismatch"]["kp_ratio"]) - D("5.8333333333333333333333333333")) < TOLERANCE
        assert line["unit_mismatch"]["contracts"] == []

    def test_a_mismatch_of_a_contract_alone_names_the_contract(self, member_client, stage):
        _history(stage, D("100"))
        _history(stage, D("110"))
        wide, _, _ = _history(stage, D("700"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("120"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "unit_mismatch"
        assert line["unit_mismatch"]["kp_ratio"] is None
        (flagged,) = line["unit_mismatch"]["contracts"]
        assert flagged["contract_id"] == wide.id
        assert abs(D(flagged["ratio"]) - D("6.6666666666666666666666666667")) < TOLERANCE

    def test_line_without_history_has_no_comparison(self, member_client, stage):
        stage.history(D("100"))
        family2, variant2 = _other_family(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("100"), variant=variant2, family=family2)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "no_history"
        assert line["rung"] is None
        assert [r["contracts"] for r in line["rungs"]] == [0, 0, 0, 0]
        for key in ("range", "ours", "position", "to_ours", "bargain", "below", "group", "unit_mismatch"):
            assert line[key] is None, key
        assert _pair(line["kp"]["unit_price"]) == (D("100"), D("100"))

    def test_line_without_a_variant_has_no_rung_and_no_rungs(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "no_variant"
        assert (line["rung"], line["rungs"], line["family"], line["variant_label"]) == (None, [], None, None)
        assert line["kp"]["total"] == {"net": "1000.00", "shown": "1000.00"}

    def test_line_without_a_price_keeps_its_zero_money(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        item = stage.kp(part, D("0"), total=D("0"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "no_price"
        assert line["kp"]["total"] == {"net": "0.00", "shown": "0.00"}
        assert _pair(line["kp"]["unit_price"]) == (D("0"), D("0"))
        assert (line["to_ours"], line["bargain"], line["below"]) == (None, None, None)


# ---------------------------------------------------------------------------
#  Восемь состояний, «ждёт администратора», группы систем
# ---------------------------------------------------------------------------


class TestLineStates:
    def test_one_line_of_each_of_the_eight_states(self, member_client, world, db_session, factories):
        stage_ = Stage(world, db_session, factories, area=None)
        _history_pair(stage_)
        family2, variant2 = _other_family(stage_)
        rnd = stage_.round()
        part = stage_.participant(rnd, "Подрядчик А")
        expected = {
            "comparable": stage_.kp(part, D("144")),
            "unit_mismatch": stage_.kp(part, D("840")),
            "no_history": _put(stage_, part.proposal, "R", D("144"), variant=variant2, family=family2),
            "no_variant": stage_.kp(part, D("144"), variant=False),
            "no_price": stage_.kp(part, D("0"), total=D("0")),
            "in_progress": stage_.kp(part, D("144"), variant=False),
            "no_area": _put(stage_, part.proposal, "R", D("1"), total=D("5000"), kind="SYSTEM"),
        }
        _job(db_session, "family_suggestion", "pending", context_id=expected["in_progress"].context_id)
        blank = stage_.participant(rnd, "Подрядчик Б", vat=None)
        expected["unknown_vat_base"] = stage_.kp(blank, D("144"))

        states = {}
        for who in (part, blank):
            body = _ok(member_client, stage_.tender, rnd, who)
            states.update({line["position_item_id"]: line["state"] for line in body["lines"]})

        assert states == {item.id: state for state, item in expected.items()}
        assert len(set(states.values())) == 8

    def test_unknown_base_participant_is_200_with_blank_totals(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        first = stage.kp(part, D("144"))
        stage.kp(part, D("96"), cat="R2")

        body = _ok(member_client, stage.tender, rnd, part)

        (only,) = body["participants"]
        assert (only["state"], only["totals"]) == ("unknown_vat_base", None)
        assert len(body["lines"]) == 2
        assert {line["state"] for line in body["lines"]} == {"unknown_vat_base"}
        assert body["articles"]
        for article in (*body["articles"], body["total"]):
            assert (article["per_sqm"], article["whole"], article["ours_class_per_sqm"]) == (None, None, None)
        assert all(a["lines_totals"] is None for a in body["articles"])
        line = _line_of(body, first)
        assert (line["kp"]["unit_price"], line["kp"]["total"]) == (None, None)
        assert (line["rung"], line["rungs"], line["to_ours"]) == (None, [], None)


class TestWaitingAdmin:
    def test_no_variant_with_a_stuck_job_waits_for_the_admin(self, member_client, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stuck = stage.kp(part, D("100"), variant=False)
        quiet = stage.kp(part, D("100"), variant=False)
        _job(db_session, "family_suggestion", "error", context_id=stuck.context_id)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, stuck)["waiting_admin"] is True
        assert _line_of(body, quiet)["waiting_admin"] is False
        assert body["progress"]["waiting_admin"] == 1

    def test_a_line_with_a_variant_does_not_wait_even_with_the_flag_up(self, member_client, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"))
        _job(db_session, "family_suggestion", "error", context_id=item.context_id)

        assert _line_of(_ok(member_client, stage.tender, rnd, part), item)["waiting_admin"] is False

    def test_a_line_in_progress_does_not_wait_even_with_a_stuck_job(self, member_client, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=item.context_id)
        _job(db_session, "family_suggestion", "error", context_id=item.context_id)

        body = _ok(member_client, stage.tender, rnd, part)

        line = _line_of(body, item)
        assert (line["state"], line["waiting_admin"]) == ("in_progress", False)
        assert body["progress"] == {"lines_total": 1, "in_progress": 1, "waiting_admin": 0}

    def test_paused_claim_turns_a_live_job_into_waiting(self, member_client, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=item.context_id)
        _pause(db_session)

        body = _ok(member_client, stage.tender, rnd, part)

        line = _line_of(body, item)
        assert (line["state"], line["waiting_admin"]) == ("no_variant", True)
        assert body["progress"] == {"lines_total": 1, "in_progress": 0, "waiting_admin": 1}


class TestSystemGroups:
    def test_system_lines_of_one_family_form_a_group_with_shares(self, member_client, stage):
        rnd, part, (small, large) = _system_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        first, second = _line_of(body, small), _line_of(body, large)
        assert (first["kind"], second["kind"]) == ("system", "system")
        assert (first["state"], second["state"]) == ("comparable", "comparable")
        for line, share in ((first, D("0.25")), (second, D("0.75"))):
            assert line["group"]["lines"] == 2
            assert D(line["group"]["share"]) == share
            assert _pair(line["group"]["kp_per_sqm"]) == (D("120"), D("120"))
        # группа: (120 - 100) * 1000 = 20 000; доли 0.25 и 0.75
        assert first["to_ours"] == {"net": "5000.00", "shown": "5000.00"}
        assert second["to_ours"] == {"net": "15000.00", "shown": "15000.00"}
        assert first["bargain"] == {"net": "5000.00", "shown": "5000.00"}
        assert first["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}

    def test_group_money_uses_the_participant_rate(self, member_client, stage):
        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        gross = [_put(stage, part.proposal, "R", D("1"), total=D(t), kind="SYSTEM") for t in (36000, 108000)]

        line = _line_of(_ok(member_client, stage.tender, rnd, part), gross[0])

        # нетто 30 000 и 90 000: цена группы 120, ₽/м² в ставке КП — 144
        assert _pair(line["group"]["kp_per_sqm"]) == (D("120"), D("144"))
        assert line["to_ours"] == {"net": "5000.00", "shown": "6000.00"}

    def test_group_price_and_share_are_not_rounded(self, member_client, stage):
        """Нетто строк 30 001,5 и 90 000: цена группы 120,0015 за м², доли — бесконечные
        дроби; квантование любой из них меняет число."""
        rnd, part, (small, _) = _system_scene(stage, kp_totals=("30001.5", "90000"))

        line = _line_of(_ok(member_client, stage.tender, rnd, part), small)

        assert D(line["group"]["kp_per_sqm"]["net"]) == D("120.0015")
        share = D(line["group"]["share"])
        assert abs(share - D("30001.5") / D("120001.5")) < TOLERANCE
        assert len(line["group"]["share"].split(".")[1]) > 2

    def test_group_is_present_on_a_system_line_without_history(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        lines = [_put(stage, part.proposal, "R", D("1"), total=D(t), kind="SYSTEM") for t in (30000, 90000)]

        body = _ok(member_client, stage.tender, rnd, part)

        for item, share in zip(lines, (D("0.25"), D("0.75")), strict=True):
            line = _line_of(body, item)
            assert line["state"] == "no_history"
            assert line["group"]["lines"] == 2
            assert D(line["group"]["share"]) == share
            assert _pair(line["group"]["kp_per_sqm"]) == (D("120"), D("120"))
            assert (line["to_ours"], line["range"], line["ours"]) == (None, None, None)

    def test_a_system_line_outside_every_group_has_no_group(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        priced = _put(stage, part.proposal, "R", D("1"), total=D("30000"), kind="SYSTEM")
        free = _put(stage, part.proposal, "R", D("0"), total=D("0"), kind="SYSTEM")

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, priced)["group"] is not None
        outside = _line_of(body, free)
        assert (outside["state"], outside["kind"], outside["group"]) == ("no_price", "system", None)

    def test_a_work_line_never_has_a_group(self, member_client, stage):
        rnd, part, item = _scene(stage)

        assert _line_of(_ok(member_client, stage.tender, rnd, part), item)["group"] is None


# ---------------------------------------------------------------------------
#  Суммы: те же, что на экране этапа
# ---------------------------------------------------------------------------


def _totals_scene(stage_):
    """История 100 и 140. Статья R: нетто 120 и 130 (торг 200 и 300). Статья R2: нетто
    80 (ниже минимума) и 700 (разные единицы)."""
    stage_.history(D("100"))
    stage_.history(D("140"))
    rnd = stage_.round()
    part = stage_.participant(rnd, "Подрядчик А")
    stage_.kp(part, D("144"))
    stage_.kp(part, D("156"))
    stage_.kp(part, D("96"), cat="R2")
    stage_.kp(part, D("840"), cat="R2")
    return rnd, part


class TestTotals:
    def test_participant_totals_equal_the_round_screen_and_the_hand_numbers(self, member_client, stage):
        rnd, part = _totals_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)
        round_body = _round_ok(member_client, stage.tender, rnd)

        (only,) = body["participants"]
        assert only["totals"] == _participant(round_body, part)["totals"]
        assert only["totals"] == {
            "bargain": {"net": "500.00", "shown": "600.00"},
            "below": {"net": "-200.00", "shown": "-240.00"},
            "below_lines": 1,
            "unit_mismatch_lines": 1,
            "unit_mismatch_amount": {"net": "7000.00", "shown": "8400.00"},
            "not_comparable": {"total": {"net": "0.00", "shown": "0.00"}, "by_state": {}},
        }

    def test_lines_totals_of_an_article_sum_the_lines_of_that_article(self, member_client, stage):
        rnd, part = _totals_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        zero = {"net": "0.00", "shown": "0.00"}
        assert _offer_article(body, stage, "R")["lines_totals"] == {
            "bargain": {"net": "500.00", "shown": "600.00"},
            "below": zero,
            "below_lines": 0,
            "unit_mismatch_lines": 0,
            "unit_mismatch_amount": zero,
            "not_comparable": {"total": zero, "by_state": {}},
        }
        assert _offer_article(body, stage, "R2")["lines_totals"] == {
            "bargain": zero,
            "below": {"net": "-200.00", "shown": "-240.00"},
            "below_lines": 1,
            "unit_mismatch_lines": 1,
            "unit_mismatch_amount": {"net": "7000.00", "shown": "8400.00"},
            "not_comparable": {"total": zero, "by_state": {}},
        }

    def test_not_comparable_amounts_are_summed_by_state_in_lines_totals(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("100"), variant=False)
        stage.kp(part, D("50"), variant=False, qty=D("2"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert _offer_article(body, stage, "R")["lines_totals"]["not_comparable"] == {
            "total": {"net": "1100.00", "shown": "1100.00"},
            "by_state": {"no_variant": {"lines": 2, "amount": {"net": "1100.00", "shown": "1100.00"}}},
        }

    def test_article_fields_equal_the_cell_of_the_round_screen(self, member_client, stage):
        rnd, part = _totals_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)
        round_body = _round_ok(member_client, stage.tender, rnd)

        assert [a["work_category_id"] for a in body["articles"]] == [stage.cats["R"].id, stage.cats["R2"].id]
        for cat in ("R", "R2"):
            mine, theirs = _offer_article(body, stage, cat), _cell(_article(round_body, stage, cat), part)
            for key in ("per_sqm", "whole", "unit_mismatch_lines", "ours_class_per_sqm"):
                assert mine[key] == theirs[key], (cat, key)
            round_article = _article(round_body, stage, cat)
            assert {k: mine[k] for k in ("code", "title", "sort_order")} == {
                k: round_article[k] for k in ("code", "title", "sort_order")
            }
        # нетто статьи R: 1200 + 1300 = 2500 на 1000 м²
        assert _pair(_offer_article(body, stage, "R")["per_sqm"]) == (D("2.5"), D("3"))

    def test_total_row_is_flat_and_equals_the_round_cell(self, member_client, stage):
        rnd, part = _totals_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)
        round_cell = _cell(_round_ok(member_client, stage.tender, rnd)["total"], part)

        total = body["total"]
        assert (total["work_category_id"], total["title"]) == (None, "Итого на м² объекта")
        for key in ("per_sqm", "whole", "unit_mismatch_lines", "ours_class_per_sqm"):
            assert total[key] == round_cell[key], key
        assert "lines_totals" not in total

    def test_lines_of_a_group_split_between_articles_sum_to_the_participant_total(self, member_client, stage):
        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        _put(stage, part.proposal, "R", D("1"), total=D("30000"), kind="SYSTEM")
        _put(stage, part.proposal, "R2", D("1"), total=D("90000"), kind="SYSTEM")

        body = _ok(member_client, stage.tender, rnd, part)

        shares = [D(_offer_article(body, stage, cat)["lines_totals"]["bargain"]["net"]) for cat in ("R", "R2")]
        assert shares == [D("5000.00"), D("15000.00")]
        assert D(body["participants"][0]["totals"]["bargain"]["net"]) == sum(shares)

    def test_unit_mismatch_lines_of_the_article_count_only_that_article(self, member_client, stage):
        rnd, part = _totals_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _offer_article(body, stage, "R2")["unit_mismatch_lines"] == 1
        assert _offer_article(body, stage, "R")["unit_mismatch_lines"] == 0
        assert body["total"]["unit_mismatch_lines"] == 1


# ---------------------------------------------------------------------------
#  Панель строки: авто, предпросмотр ступени, отказ ступени
# ---------------------------------------------------------------------------


class TestLinePanelAuto:
    def test_panel_has_the_four_blocks_and_the_same_line_as_the_breakdown(self, member_client, stage):
        rnd, part, item = _scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, item)
        offer_line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert set(panel) == PANEL_KEYS
        assert panel["line"] == offer_line
        assert json.dumps(panel["line"], sort_keys=False) == json.dumps(offer_line, sort_keys=False)
        assert panel["group_lines"] == []

    def test_panel_line_equals_the_breakdown_line_for_every_state(self, member_client, world, db_session, factories):
        stage_ = Stage(world, db_session, factories, area=None)
        stage_.history(D("100"))
        stage_.history(D("140"))
        rnd = stage_.round()
        part = stage_.participant(rnd, "Подрядчик А")
        items = [
            stage_.kp(part, D("144")),
            stage_.kp(part, D("840")),
            stage_.kp(part, D("144"), variant=False),
            stage_.kp(part, D("0"), total=D("0")),
            _put(stage_, part.proposal, "R", D("1"), total=D("5000"), kind="SYSTEM"),
        ]
        body = _ok(member_client, stage_.tender, rnd, part)

        for item in items:
            panel = _panel(member_client, stage_.tender, rnd, part, item)
            assert panel["line"] == _line_of(body, item), item.id

    def test_contracts_of_the_auto_rung_carry_the_contract_card(self, member_client, stage):
        own = stage.participant(stage.round(), "Подрядчик Я")
        low, _, _ = _history(stage, D("100"), contractor=own.contractor)
        high, _, _ = _history(stage, D("140"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", package=own.offer.package)
        item = stage.kp(part, D("144"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        first, second = panel["contracts"]
        assert set(first) == {
            "contract_id",
            "object_title",
            "number",
            "year",
            "rate_class",
            "contractor_id",
            "rate",
            "is_lower_bound",
            "with_this_contractor",
            "ratio",
        }
        assert first["contract_id"] == low.id
        assert first["object_title"] == low.object.title
        assert first["number"] == low.contract_number
        assert first["year"] == 2025
        assert first["rate_class"] == {"id": stage.rate_class.id, "title": "Класс Т"}
        assert first["contractor_id"] == low.contractor_id
        assert _pair(first["rate"]) == (D("100"), D("120"))
        assert (first["is_lower_bound"], first["with_this_contractor"], first["ratio"]) == (True, True, None)
        assert second["contract_id"] == high.id
        assert _pair(second["rate"]) == (D("140"), D("168"))
        assert (second["is_lower_bound"], second["with_this_contractor"]) == (False, False)

    def test_every_contract_at_the_minimum_is_a_lower_bound(self, member_client, stage):
        _history(stage, D("100"))
        _history(stage, D("100"))
        _history(stage, D("140"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("120"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert [c["is_lower_bound"] for c in panel["contracts"]] == [True, True, False]

    def test_lower_bound_is_the_minimum_of_the_rung_not_our_rate(self, member_client, stage, factories):
        """Ступень `v·all`: договор своего класса 200 (наша расценка) и чужого 100 —
        нижняя граница у договора 100, а не у нашей расценки."""
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        ours, _, _ = _history(stage, D("200"))
        cheaper, _, _ = _history(stage, D("100"), rate_class=other_class)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("150"), qty=D("1"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert _pair(panel["line"]["ours"]["value"]) == (D("200"), D("200"))
        bounds = {c["contract_id"]: c["is_lower_bound"] for c in panel["contracts"]}
        assert bounds == {ours.id: False, cheaper.id: True}

    def test_ratio_is_filled_for_the_contract_that_breaks_the_scale(self, member_client, stage):
        _history(stage, D("100"))
        _history(stage, D("110"))
        wide, _, _ = _history(stage, D("700"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("120"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        ratios = {c["contract_id"]: c["ratio"] for c in panel["contracts"]}
        assert [r is None for cid, r in ratios.items() if cid != wide.id] == [True, True]
        assert abs(D(ratios[wide.id]) - D("6.6666666666666666666666666667")) < TOLERANCE
        assert ratios[wide.id] == panel["line"]["unit_mismatch"]["contracts"][0]["ratio"]

    def test_line_that_did_not_reach_the_ladder_has_no_contracts_and_no_dropped(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        silent = stage.kp(part, D("100"), variant=False)
        _history(stage, D("100"), vat=None)  # единственный договор семьи выпал по базе НДС
        lonely = stage.kp(part, D("100"))

        for item in (silent, lonely):
            panel = _panel(member_client, stage.tender, rnd, part, item)
            assert panel["line"]["rung"] is None, item.id
            assert (panel["contracts"], panel["vat_dropped"], panel["group_lines"]) == ([], [], [])

    def test_unknown_base_participant_gets_a_panel_without_contracts(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        item = stage.kp(part, D("100"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["state"] == "unknown_vat_base"
        assert (panel["contracts"], panel["vat_dropped"]) == ([], [])

    def test_system_panel_lists_the_group_lines_and_the_group_contracts(self, member_client, stage):
        rnd, part, (small, large) = _system_scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)
        body = _ok(member_client, stage.tender, rnd, part)

        assert [line["position_item_id"] for line in panel["group_lines"]] == [small.id, large.id]
        assert panel["group_lines"] == [_line_of(body, small), _line_of(body, large)]
        assert [_pair(c["rate"]) for c in panel["contracts"]] == [(D("100"), D("100")), (D("140"), D("140"))]
        assert [c["is_lower_bound"] for c in panel["contracts"]] == [True, False]

    def test_group_lines_hold_every_group_line_of_the_family(self, member_client, stage):
        """Строки одной семьи на разных вариантах — разные группы: в `group_lines`
        попадают строки всех групп семьи, каждая в полной форме со своим `group`;
        системная строка без цены и строка другой семьи не входят."""
        v2 = _second_variant(stage)
        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
            _history(stage, D("1"), total=D(total), kind="SYSTEM", variant=v2)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        mine = _put(stage, part.proposal, "R", D("1"), total=D("30000"), kind="SYSTEM")
        mate = _put(stage, part.proposal, "R", D("1"), total=D("90000"), kind="SYSTEM")
        apart = _put(stage, part.proposal, "R", D("1"), total=D("50000"), kind="SYSTEM", variant=v2)
        _put(stage, part.proposal, "R", D("0"), total=D("0"), kind="SYSTEM")  # без цены: вне групп
        family2, variant2 = _other_family(stage)
        _put(stage, part.proposal, "R", D("1"), total=D("7000"), kind="SYSTEM", variant=variant2, family=family2)

        panel = _panel(member_client, stage.tender, rnd, part, mine)
        other = _panel(member_client, stage.tender, rnd, part, apart)
        body = _ok(member_client, stage.tender, rnd, part)

        expected = [mine.id, mate.id, apart.id]
        assert [line["position_item_id"] for line in panel["group_lines"]] == expected
        assert [line["position_item_id"] for line in other["group_lines"]] == expected
        assert panel["group_lines"] == [_line_of(body, item) for item in (mine, mate, apart)]
        groups = [line["group"]["lines"] for line in panel["group_lines"]]
        assert groups == [2, 2, 1]

    def test_panel_is_404_for_unknown_foreign_and_chapter_lines(self, member_client, stage, factories):
        rnd, part, item = _scene(stage)
        neighbour = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        foreign_line = stage.kp(neighbour, D("100"))
        chapter = stage.chapter(part.proposal, "R")

        class _Item:
            id = 987654321

        assert _panel_get(member_client, stage.tender, rnd, part, _Item).status_code == 404
        assert _panel_get(member_client, stage.tender, rnd, part, foreign_line).status_code == 404
        assert _panel_get(member_client, stage.tender, rnd, part, chapter).status_code == 404
        assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

    def test_panel_of_a_line_of_another_round_is_404(self, member_client, stage):
        rnd, part, _ = _scene(stage)
        other_round = stage.round()
        elsewhere = stage.participant(other_round, "Подрядчик В", vat=D("0"))
        item = stage.kp(elsewhere, D("100"))

        assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 404
        assert _panel_get(member_client, stage.tender, rnd, elsewhere, item).status_code == 404

    def test_panel_of_a_participant_without_an_estimate_is_404(self, member_client, stage):
        rnd, _, item = _scene(stage)
        empty = stage.participant(rnd, "Подрядчик Б", estimate=False)

        assert _panel_get(member_client, stage.tender, rnd, empty, item).status_code == 404

    def test_member_reads_the_panel(self, member_client, stage):
        rnd, part, item = _scene(stage)

        assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

    def test_the_panel_writes_nothing(self, member_client, stage, db_session):
        rnd, part, item = _scene(stage)
        with count_statements(db_session) as seen:
            assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200
            assert _panel_get(member_client, stage.tender, rnd, part, item, level="f", scope="all").status_code == 200
            assert _panel_get(member_client, stage.tender, rnd, part, item, level="x", scope="all").status_code == 422

        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert seen and writes == []

    def test_panel_has_no_float_literal(self, member_client, stage):
        rnd, part, item = _scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, item, level="f", scope="all")

        assert panel["contracts"]  # `_json` падает на любом дробном литерале


class TestRungPreview:
    def _wide_scene(self, stage_, factories):
        """Вариант 1: договоры 100 и 140 своего класса. Вариант 2: 60 своего класса и 300
        чужого. КП (НДС 20 %): нетто 120 за 10 единиц на варианте 1."""
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        v2 = _second_variant(stage_)
        stage_.history(D("100"))
        stage_.history(D("140"))
        _history(stage_, D("60"), variant=v2)
        _history(stage_, D("300"), variant=v2, rate_class=other_class)
        rnd = stage_.round()
        part = stage_.participant(rnd, "Подрядчик А")
        return rnd, part, stage_.kp(part, D("144"))

    def test_preview_f_all_changes_the_rung_the_range_and_the_money(self, member_client, stage, factories):
        rnd, part, item = self._wide_scene(stage, factories)
        auto = _panel(member_client, stage.tender, rnd, part, item)["line"]

        line = _panel(member_client, stage.tender, rnd, part, item, level="f", scope="all")["line"]

        assert auto["rung"]["auto"] is True
        assert (auto["rung"]["level"], auto["rung"]["scope"]) == ("v", "cls")
        assert line["rung"] == {"level": "f", "scope": "all", "auto": False, "threshold_met": True}
        assert {k: _pair(v)[0] for k, v in line["range"].items()} == {
            "min": D("60"),
            "max": D("300"),
            "median": D("120"),
        }
        assert _pair(line["ours"]["value"]) == (D("60"), D("72"))
        assert line["ours"]["from_all_classes"] is False
        # (120 - 60) * 10 вместо (120 - 100) * 10
        assert line["to_ours"] == {"net": "600.00", "shown": "720.00"}
        assert line["bargain"] == {"net": "600.00", "shown": "720.00"}
        assert line["to_ours"] != auto["to_ours"]
        assert line["range"] != auto["range"] and line["ours"] != auto["ours"]
        assert line["rungs"] == auto["rungs"]
        assert line["state"] == auto["state"] == "comparable"

    def test_preview_changes_the_contracts_of_the_panel(self, member_client, stage, factories):
        rnd, part, item = self._wide_scene(stage, factories)

        auto = _panel(member_client, stage.tender, rnd, part, item)
        wide = _panel(member_client, stage.tender, rnd, part, item, level="f", scope="all")
        narrow = _panel(member_client, stage.tender, rnd, part, item, level="f", scope="cls")

        assert len(auto["contracts"]) == 2
        assert sorted(_pair(c["rate"])[0] for c in wide["contracts"]) == [D("60"), D("100"), D("140"), D("300")]
        assert sorted(_pair(c["rate"])[0] for c in narrow["contracts"]) == [D("60"), D("100"), D("140")]
        assert [c["is_lower_bound"] for c in wide["contracts"] if _pair(c["rate"])[0] == D("60")] == [True]

    def test_preview_of_the_auto_rung_still_reads_as_a_manual_choice(self, member_client, stage):
        rnd, part, item = _scene(stage)

        line = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="cls")["line"]
        auto = _panel(member_client, stage.tender, rnd, part, item)["line"]

        assert line["rung"] == {"level": "v", "scope": "cls", "auto": False, "threshold_met": True}
        assert auto == {**line, "rung": auto["rung"]}

    def test_preview_does_not_touch_the_screen_sums(self, member_client, stage, factories):
        rnd, part, item = self._wide_scene(stage, factories)
        before = _ok(member_client, stage.tender, rnd, part)

        _panel(member_client, stage.tender, rnd, part, item, level="f", scope="all")
        after = _ok(member_client, stage.tender, rnd, part)

        assert before == after
        assert _line_of(after, item)["rung"]["auto"] is True

    def test_preview_on_a_line_without_history_is_a_rung_without_contracts(self, member_client, stage):
        stage.history(D("100"))
        family2, variant2 = _other_family(stage)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("100"), variant=variant2, family=family2)

        panel = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="all")

        assert panel["line"]["state"] == "no_history"
        assert panel["line"]["rung"] == {"level": "v", "scope": "all", "auto": False, "threshold_met": False}
        assert (panel["contracts"], panel["vat_dropped"]) == ([], [])

    def test_p_rungs_preview_when_the_family_has_a_second_main(self, member_client, stage, world):
        world.param(stage.schema, 2, "Длина")
        rnd, part, item = _scene(stage)

        line = _panel(member_client, stage.tender, rnd, part, item, level="p", scope="cls")["line"]

        assert line["rung"] == {"level": "p", "scope": "cls", "auto": False, "threshold_met": True}
        assert line["state"] == "comparable"

    def test_system_preview_moves_the_whole_family_group(self, member_client, stage):
        v2 = _second_variant(stage)
        _history(stage, D("1"), total=D("100000"), kind="SYSTEM")
        _history(stage, D("1"), total=D("140000"), kind="SYSTEM")
        _history(stage, D("1"), total=D("60000"), kind="SYSTEM", variant=v2)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        small = _put(stage, part.proposal, "R", D("1"), total=D("30000"), kind="SYSTEM")
        large = _put(stage, part.proposal, "R", D("1"), total=D("90000"), kind="SYSTEM")

        auto = _panel(member_client, stage.tender, rnd, part, small)
        wide = _panel(member_client, stage.tender, rnd, part, small, level="f", scope="cls")
        other_line = _panel(member_client, stage.tender, rnd, part, large, level="f", scope="cls")

        assert auto["line"]["rung"]["auto"] is True
        assert wide["line"]["rung"] == {"level": "f", "scope": "cls", "auto": False, "threshold_met": True}
        assert sorted(_pair(c["rate"])[0] for c in wide["contracts"]) == [D("60"), D("100"), D("140")]
        # группа: (120 - 60) * 1000 = 60 000; доля 0.25
        assert wide["line"]["to_ours"] == {"net": "15000.00", "shown": "15000.00"}
        assert [line["position_item_id"] for line in wide["group_lines"]] == [small.id, large.id]
        # предпросмотр у системной строки — для всей семьи: у соседней строки та же ступень
        assert other_line["line"]["rung"] == wide["line"]["rung"]
        assert other_line["line"]["to_ours"] == {"net": "45000.00", "shown": "45000.00"}

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"level": "v"}, id="level-only"),
            pytest.param({"scope": "cls"}, id="scope-only"),
            pytest.param({"level": "x", "scope": "cls"}, id="bad-level"),
            pytest.param({"level": "v", "scope": "x"}, id="bad-scope"),
            pytest.param({"level": "V", "scope": "cls"}, id="upper-level"),
            pytest.param({"level": "", "scope": ""}, id="empty-both"),
            pytest.param({"level": "v", "scope": ""}, id="empty-scope"),
            pytest.param({"level": "p", "scope": "cls"}, id="p-cls-without-a-second-main"),
            pytest.param({"level": "p", "scope": "all"}, id="p-all-without-a-second-main"),
        ],
    )
    def test_unknown_rung_inputs_are_one_422_code(self, member_client, stage, params):
        rnd, part, item = _scene(stage)

        response = _panel_get(member_client, stage.tender, rnd, part, item, **params)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "unknown_rung"
        assert isinstance(detail["message"], str) and detail["message"]

    @pytest.mark.parametrize("level", ["v", "f"])
    @pytest.mark.parametrize("scope", ["cls", "all"])
    def test_every_available_pair_is_accepted(self, member_client, stage, level, scope):
        rnd, part, item = _scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, item, level=level, scope=scope)

        assert panel["line"]["rung"]["level"] == level
        assert panel["line"]["rung"]["scope"] == scope

    def test_line_without_a_key_has_no_rung_to_choose(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)

        response = _panel_get(member_client, stage.tender, rnd, part, item, level="v", scope="cls")

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unknown_rung"
        assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

    def test_line_that_did_not_reach_the_ladder_has_no_rung_to_choose(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("0"), total=D("0"))  # ключ есть, цена не проходит

        response = _panel_get(member_client, stage.tender, rnd, part, item, level="v", scope="cls")

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unknown_rung"

    def test_unknown_rung_of_a_missing_line_stays_404(self, member_client, stage):
        rnd, part, _ = _scene(stage)

        class _Item:
            id = 987654321

        assert _panel_get(member_client, stage.tender, rnd, part, _Item, level="x", scope="cls").status_code == 404


# ---------------------------------------------------------------------------
#  Выпавшие из-за базы НДС договоры
# ---------------------------------------------------------------------------


class TestVatDropped:
    def test_a_contract_with_an_unknown_base_is_dropped_not_listed(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        dropped, _, _ = _history(stage, D("120"), vat=None)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert dropped.id not in {c["contract_id"] for c in panel["contracts"]}
        assert panel["vat_dropped"] == [
            {
                "contract_id": dropped.id,
                "object_title": dropped.object.title,
                "number": dropped.contract_number,
                "year": 2025,
            }
        ]

    def test_without_dropped_contracts_the_list_is_empty(self, member_client, stage):
        rnd, part, item = _scene(stage)

        assert _panel(member_client, stage.tender, rnd, part, item)["vat_dropped"] == []

    def test_a_dropped_contract_of_another_class_appears_only_on_the_all_scope(self, member_client, stage, factories):
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        stage.history(D("100"))
        stage.history(D("140"))
        foreign, _, _ = _history(stage, D("120"), vat=None, rate_class=other_class)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))

        narrow = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="cls")
        wide = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="all")

        assert narrow["vat_dropped"] == []
        assert [d["contract_id"] for d in wide["vat_dropped"]] == [foreign.id]

    def test_a_dropped_contract_of_another_variant_is_not_on_the_variant_rung(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        elsewhere, _, _ = _history(stage, D("120"), vat=None, variant=_second_variant(stage))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))

        on_variant = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="cls")
        on_family = _panel(member_client, stage.tender, rnd, part, item, level="f", scope="cls")

        assert on_variant["vat_dropped"] == []
        assert [d["contract_id"] for d in on_family["vat_dropped"]] == [elsewhere.id]

    def test_system_panel_lists_the_dropped_contracts_of_the_group_key(self, member_client, stage):
        dropped, _, _ = _history(stage, D("1"), total=D("120000"), kind="SYSTEM", vat=None)
        rnd, part, (small, _) = _system_scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        assert [d["contract_id"] for d in panel["vat_dropped"]] == [dropped.id]
        assert dropped.id not in {c["contract_id"] for c in panel["contracts"]}


# ---------------------------------------------------------------------------
#  ИПЦ
# ---------------------------------------------------------------------------


def _inflation_scene(stage_, db_session, kind):
    """Сцена с одним из трёх отказов приведения (или без отказа): `(rnd, part, item, series_id)`."""
    if kind == "target":
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        stage_.history(D("100"))
        rnd = stage_.round(held_on=None)
        part = stage_.participant(rnd, "Подрядчик А", vat=D("0"), prepared=None)
    elif kind == "amendment":
        contract, _, _ = stage_.history(D("100"))
        amendment, _ = stage_.world.estimate(contract, amendment_no=1)
        amendment.data_prepared_on_date = None
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage_.round(held_on=dt.date(2026, 8, 10))
        part = stage_.participant(rnd, "Подрядчик А", vat=D("0"))
    elif kind == "years":
        _dated_history(stage_)
        series_id = fx.series_with_years(db_session, SERIES_NAME, {2026: "1.060"})
        rnd = stage_.round(held_on=dt.date(2026, 8, 10))
        part = stage_.participant(rnd, "Подрядчик А", vat=D("0"))
    else:
        _dated_history(stage_)
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage_.round(held_on=dt.date(2026, 8, 10))
        part = stage_.participant(rnd, "Подрядчик А", vat=D("0"))
    item = stage_.kp(part, D("120"))
    db_session.flush()
    return rnd, part, item, series_id


class TestInflation:
    @pytest.mark.parametrize(
        ("kind", "code"),
        [
            ("target", "inflation_target_missing"),
            ("amendment", "amendment_date_missing"),
            ("years", "missing_inflation_years"),
        ],
    )
    @pytest.mark.parametrize("endpoint", ["offer", "panel"])
    def test_refusals_are_the_codes_and_texts_of_the_round_screen(
        self, member_client, stage, db_session, kind, code, endpoint
    ):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, kind)
        params = {"inflation_series_id": series_id}

        if endpoint == "offer":
            response = _get(member_client, stage.tender, rnd, part, **params)
        else:
            response = _panel_get(member_client, stage.tender, rnd, part, item, **params)
        reference = _round_get(member_client, stage.tender, rnd, **params)

        assert response.status_code == reference.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == code
        assert detail == reference.json()["detail"]
        if kind == "target":
            assert detail["message"] == TARGET_MISSING_TEXT

    @pytest.mark.parametrize("kind", ["target", "amendment", "years"])
    @pytest.mark.parametrize("which", ["foreign-line", "chapter", "missing-line"])
    def test_missing_line_is_404_before_any_refusal(self, member_client, stage, db_session, kind, which):
        """Принадлежность строки проверяется раньше приведения: сцена, которая при
        своей строке отдала бы 422, при чужой строке, главе или несуществующей
        строке отдаёт 404."""
        rnd, part, item, series_id = _inflation_scene(stage, db_session, kind)
        params = {"inflation_series_id": series_id}
        if which == "foreign-line":
            # КП соседа без даты подготовки: иначе у этапа появился бы целевой месяц,
            # и сцена `target` перестала бы отказывать
            neighbour = stage.participant(rnd, "Подрядчик Б", vat=D("0"), prepared=None)
            target = stage.kp(neighbour, D("100"))
        elif which == "chapter":
            target = stage.chapter(part.proposal, "R")
        else:

            class _Missing:
                id = 987654321

            target = _Missing
        # та же сцена со своей строкой отказывает: 404 ниже — порядок проверок, а не
        # пропавший отказ
        assert _panel_get(member_client, stage.tender, rnd, part, item, **params).status_code == 422

        response = _panel_get(member_client, stage.tender, rnd, part, target, **params)

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    @pytest.mark.parametrize("endpoint", ["offer", "panel"])
    def test_unknown_series_is_404(self, member_client, stage, endpoint):
        rnd, part, item = _scene(stage)

        if endpoint == "offer":
            response = _get(member_client, stage.tender, rnd, part, inflation_series_id=987654321)
        else:
            response = _panel_get(member_client, stage.tender, rnd, part, item, inflation_series_id=987654321)

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_without_a_series_the_answer_has_no_inflation_key_anywhere(self, member_client, stage, db_session):
        rnd, part, item, _ = _inflation_scene(stage, db_session, "ok")

        offer = _ok(member_client, stage.tender, rnd, part)
        panel = _panel(member_client, stage.tender, rnd, part, item)

        for body in (offer, panel):
            assert not {k for k in _keys(body) if "inflat" in k or k in {"nominal", "factor"}}

    def test_with_a_series_the_breakdown_carries_the_block_and_adjusted_history(self, member_client, stage, db_session):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")

        body = _ok(member_client, stage.tender, rnd, part, inflation_series_id=series_id)
        nominal = _ok(member_client, stage.tender, rnd, part)

        assert body["inflation"]["series_id"] == series_id
        assert body["inflation"]["target_month"] == "2026-08"
        assert body["inflation"]["sentence"] == f"Цены приведены к августу 2026 по ряду «{SERIES_NAME}»."
        # ставка ступени приведена: (120 - 100 * k) * 10
        assert _line_of(nominal, item)["bargain"]["net"] == "200.00"
        assert _line_of(body, item)["bargain"]["net"] == "88.97"
        # КП не приводится
        assert _line_of(body, item)["kp"] == _line_of(nominal, item)["kp"]
        assert body["participants"][0]["per_sqm"] == nominal["participants"][0]["per_sqm"]

    def test_with_a_series_the_panel_contracts_are_adjusted(self, member_client, stage, db_session):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")

        panel = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)

        (contract,) = panel["contracts"]
        assert abs(D(contract["rate"]["net"]) - D("100") * FACTOR_FEB_2025) < D("1e-18")
        assert panel["line"]["bargain"]["net"] == "88.97"

    def test_the_series_answer_of_the_panel_line_equals_the_breakdown_line(self, member_client, stage, db_session):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")

        panel = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)
        body = _ok(member_client, stage.tender, rnd, part, inflation_series_id=series_id)

        assert panel["line"] == _line_of(body, item)


# ---------------------------------------------------------------------------
#  Скорость
# ---------------------------------------------------------------------------


class TestQueryCount:
    def test_statement_count_does_not_grow_with_the_lines(self, member_client, stage, db_session):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("144"))
        with count_statements(db_session) as small:
            assert _get(member_client, stage.tender, rnd, part).status_code == 200
        with count_statements(db_session) as small_panel:
            assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

        for _ in range(12):
            stage.kp(part, D("120"))
            stage.kp(part, D("120"), cat="R2")
        with count_statements(db_session) as large:
            body = _ok(member_client, stage.tender, rnd, part)
        with count_statements(db_session) as large_panel:
            assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

        assert body["progress"]["lines_total"] == 25
        assert len(small) == len(large)
        assert len(small_panel) == len(large_panel)


# ---------------------------------------------------------------------------
#  Спека называет то, что делает код
# ---------------------------------------------------------------------------


class TestSpecNamesTheContract:
    SPEC = "docs/superpowers/specs/2026-10-23-our-price-design.md"

    def _section(self) -> str:
        text = (pathlib.Path(__file__).resolve().parents[3] / self.SPEC).read_text(encoding="utf-8")
        start = text.index("### 2.12.")
        end = text.index("\n### ", start + 1)
        return " ".join(text[start:end].split())

    def test_offer_form_names_the_new_fields(self):
        section = self._section()
        for name in ("waiting_admin: bool", "to_ours", "kp_per_sqm", "lines_totals", "progress"):
            assert name in section, name
        assert "`bargain` и `below` — только у `comparable`" in section

    def test_panel_answer_names_the_four_blocks_and_the_contract_card(self):
        section = self._section()
        assert "{line, group_lines, contracts, vat_dropped}" in section
        for name in ("is_lower_bound", "with_this_contractor", "contractor_id", "rate_class"):
            assert name in section, name

    def test_unknown_rung_is_one_code_for_three_inputs(self):
        section = self._section()
        assert "один код `unknown_rung`" in section
        for phrase in ("неполная пара", "неизвестное значение", "ступень вне доступных"):
            assert phrase in section, phrase

    def test_participant_without_an_estimate_is_named_as_404(self):
        assert "участник без сметы — 404" in self._section()

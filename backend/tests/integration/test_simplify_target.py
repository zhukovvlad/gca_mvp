"""Лестница на экране «Против нашей цены»: форма значения, «по частям» и цель «с этим подрядчиком»
(спека упрощения каталога §2.2, §2.8, §2.9).

Сцены строятся помощниками `test_analog_scene` и `test_analog_targets`. Названия и числа выдуманы.
Каждое правило имеет обе стороны: вход, где оно срабатывает, и соседний, нарушающий ровно одно условие.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.our_price_load import load_kp_lines
from models import FamilyParameter, PositionItem
from services.composite_values import find_or_create_composite
from tests.integration.test_analog_scene import _hist, _kp_part, _line, _load_work, _second_variant, _writes
from tests.integration.test_analog_targets import (
    Scene,
    _code,
    _preview,
    _preview_row,
    _put,
    _rows,
    _save,
    _tiles,
    _without_target_fields,
)
from tests.integration.test_kp_file_scene import _agree
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _line_of, _ok, _pair, _panel
from tests.integration.test_our_price_round_api import Stage, count_statements

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
    """Клиент администратора, который перед каждым запросом закрепляет сцену (как в `test_analog_targets`)."""
    send = admin_client.request

    def request(*args, **kwargs):
        db_session.commit()
        return send(*args, **kwargs)

    admin_client.request = request
    return admin_client


# ---------------------------------------------------------------------------
#  Цель «с этим подрядчиком»: сцена с четырьмя аналогами одной ступени
# ---------------------------------------------------------------------------


def _contractor_scene(stage, world, *, mine_cost=D("130"), mine_class="own", kp=D("160")) -> Scene:
    """Аналоги: низкий 100 (2025-01), средний 120 (2026-02), свежий 150 (2026-08) — чужих подрядчиков;
    свой — `mine_cost` (2026-04), договор подрядчика участника. Этап 2026-08, цена КП `kp`."""
    chain = world.chain(("Раздел А",), "whole")
    rnd, part = _kp_part(stage)
    ids, contracts = {}, {}
    spec = [
        ("low", D("100"), dt.date(2025, 1, 1), None),
        ("mid", D("120"), dt.date(2026, 2, 1), None),
        ("fresh", D("150"), dt.date(2026, 8, 1), None),
        ("mine", mine_cost, dt.date(2026, 4, 1), part.contractor),
    ]
    for key, cost, prepared, contractor in spec:
        extra = {} if key != "mine" or mine_class == "own" else {"rate_class": mine_class}
        contract, _, item = _hist(stage, cost, chain=chain, prepared=prepared, contractor=contractor, **extra)
        ids[key], contracts[key] = item.id, contract
    item = _line(stage, part.proposal, kp, chain=chain)
    stage.db.flush()
    return Scene(rnd, part, item, ids, chain, contracts)


class TestContractorFigure:
    def test_the_row_and_the_panel_carry_the_figure_of_the_contractor_next_to_the_freshest(
        self, admin_client, stage, world
    ):
        scene = _contractor_scene(stage, world)

        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        for line in (row, panel):
            assert line["freshest"]["analog_id"] == scene.ids["fresh"]
            assert line["contractor"]["analog_id"] == scene.ids["mine"]
            assert line["contractor"]["contract_id"] == scene.contracts["mine"].id
            assert _pair(line["contractor"]["value"]) == (D("130"), D("130"))
            assert line["contractor"]["period"] == "2026-04"
        assert row["contractor"] == panel["contractor"]

    def test_the_kp_above_the_contractor_gives_a_positive_difference_per_unit_and_in_total(
        self, admin_client, stage, world
    ):
        scene = _contractor_scene(stage, world, kp=D("160"))  # свой 130, количество 10

        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        for line in (row, panel):
            assert _pair(line["to_contractor_unit"]) == (D("30"), D("30"))
            assert _pair(line["to_contractor"]) == (D("300"), D("300"))
            # соседнее число свежей (150): разница к ней своя, а не к подрядчику
            assert _pair(line["to_freshest_unit"]) == (D("10"), D("10"))

    def test_the_kp_below_the_contractor_gives_a_negative_difference(self, admin_client, stage, world):
        scene = _contractor_scene(stage, world, kp=D("120"))

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert _pair(line["to_contractor_unit"]) == (D("-10"), D("-10"))
        assert _pair(line["to_contractor"]) == (D("-100"), D("-100"))

    def test_without_a_contractor_figure_the_difference_is_null(self, admin_client, stage, world):
        scene = _contractor_scene(stage, world, mine_cost=D("900"))  # выброс: цифры подрядчика нет

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert line["contractor"] is None
        assert line["to_contractor"] is None and line["to_contractor_unit"] is None
        assert line["to_freshest"] is not None  # у свежей разница есть: null не общий

    def test_without_an_analog_of_the_contractor_there_is_no_figure(self, admin_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _hist(stage, D("100"), chain=chain)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        stage.db.flush()

        line = _line_of(_ok(admin_client, stage.tender, rnd, part), item)

        assert (line["state"], line["contractor"]) == ("comparable", None)

    def test_an_outlier_of_the_contractor_is_not_a_figure(self, admin_client, stage, world):
        scene = _contractor_scene(stage, world, mine_cost=D("900"))

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert line["state"] == "comparable"
        assert line["contractor"] is None

    def test_an_analog_of_the_contractor_outside_the_chosen_rung_is_not_a_figure(
        self, admin_client, stage, world, factories
    ):
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        scene = _contractor_scene(stage, world, mine_class=other_class)

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert (line["work_rung"]["no"], line["contractor"]) == (1, None)

    def test_a_system_and_an_unpriced_row_have_none(self, admin_client, stage, world):
        scene = _contractor_scene(stage, world)
        system = _line(stage, scene.part.proposal, D("160"), chain=scene.chain, kind="SYSTEM")
        unpriced = _line(stage, scene.part.proposal, D("0"), chain=scene.chain)
        stage.db.flush()

        body = _ok(admin_client, stage.tender, scene.rnd, scene.part)

        assert _line_of(body, system)["contractor"] is None
        assert _line_of(body, unpriced)["contractor"] is None


class TestPutContractor:
    def test_it_is_204_and_the_row_has_no_analog(self, admin_client, stage, world, db_session):
        scene = _contractor_scene(stage, world)

        response = _put(admin_client, stage, scene, {"kind": "contractor"})

        assert (response.status_code, response.content) == (204, b"")
        assert _rows(db_session, scene.item) == [("contractor", None)]

    def test_without_a_figure_it_is_422_no_contractor_analog_and_writes_nothing(
        self, admin_client, stage, world, db_session
    ):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _hist(stage, D("100"), chain=chain)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()

        response = _put(admin_client, stage, Scene(rnd, part, item, {}, chain, {}), {"kind": "contractor"})

        assert (response.status_code, _code(response)) == (422, "no_contractor_analog")
        assert _rows(db_session) == []

    def test_a_contract_of_the_contractor_that_is_an_outlier_is_422_no_contractor_analog(
        self, admin_client, stage, world, db_session
    ):
        scene = _contractor_scene(stage, world, mine_cost=D("900"))

        response = _put(admin_client, stage, scene, {"kind": "contractor"})

        assert (response.status_code, _code(response)) == (422, "no_contractor_analog")
        assert _rows(db_session) == []

    def test_a_row_that_is_not_comparable_is_target_not_applicable_before_the_figure_is_looked_at(
        self, admin_client, stage, world, db_session
    ):
        scene = _contractor_scene(stage, world)
        item = _line(stage, scene.part.proposal, D("160"), chain=scene.chain, variant=_second_variant(stage))
        db_session.flush()

        response = _put(admin_client, stage, scene, {"kind": "contractor"}, item=item)

        assert (response.status_code, _code(response)) == (422, "target_not_applicable")

    def test_an_analog_id_does_not_go_with_the_contractor_kind(self, admin_client, stage, world, db_session):
        scene = _contractor_scene(stage, world)

        response = _put(admin_client, stage, scene, {"kind": "contractor", "analog_id": scene.ids["mine"]})

        assert response.status_code == 422
        assert _rows(db_session) == []

    def test_the_last_write_wins_between_the_three_kinds(self, admin_client, stage, world, db_session):
        scene = _contractor_scene(stage, world)

        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204
        assert _put(admin_client, stage, scene, {"kind": "freshest"}).status_code == 204
        assert _rows(db_session, scene.item) == [("freshest", None)]
        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204
        assert _rows(db_session, scene.item) == [("contractor", None)]


class TestContractorTarget:
    def test_an_acting_choice_sets_the_target_the_file_ours_the_source_and_the_comment(
        self, admin_client, stage, world
    ):
        scene = _contractor_scene(stage, world)
        before_panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        before_row = _preview_row(_preview(admin_client, stage, scene), scene.item)
        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204

        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        row = _preview_row(_preview(admin_client, stage, scene), scene.item)
        line = panel["line"]
        (mine,) = (a for a in panel["analogs"] if a["analog_id"] == scene.ids["mine"])

        assert line["target_kind"] == "contractor"
        assert (line["choice"]["kind"], line["choice"]["analog_id"]) == ("contractor", None)
        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (True, None)
        assert _pair(line["file_ours"]) == (D("130"), D("130"))
        assert D(row["ours"]) == D("130") and D(before_row["ours"]) == D("100")
        assert "эта работа стоила 130,00 ₽" in panel["comment"]["text"]
        assert "эта работа стоила 100,00 ₽" in before_panel["comment"]["text"]
        assert row["comment"] == panel["comment"]["text"]
        assert row["source"].startswith(f"{mine['object_title']}, договор № {mine['number']}")

    def test_the_choice_does_not_move_the_totals_or_the_tiles(self, admin_client, stage, world):
        scene = _contractor_scene(stage, world)
        offer_before, round_before = _tiles(admin_client, stage, scene)

        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204
        offer_after, round_after = _tiles(admin_client, stage, scene)

        assert round_after == round_before
        assert offer_after["participants"] == offer_before["participants"]
        assert offer_after["articles"] == offer_before["articles"]
        assert offer_after["total"] == offer_before["total"]
        assert _without_target_fields(offer_after) == _without_target_fields(offer_before)
        assert _line_of(offer_before, scene.item)["target_kind"] == "lowest"
        assert _line_of(offer_after, scene.item)["target_kind"] == "contractor"

    def test_a_saved_choice_whose_analog_left_is_reset_with_no_contractor_and_the_target_is_the_lowest(
        self, admin_client, stage, world, db_session, admin_user
    ):
        scene = _contractor_scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == scene.ids["mine"])
            .values(unit_cost_total=D("900"), total_cost_total=D("9000"))
        )
        db_session.commit()

        offer = _ok(admin_client, stage.tender, scene.rnd, scene.part)
        line = _line_of(offer, scene.item)

        assert line["choice"]["active"] is False
        assert line["choice"]["reset_reason"] == "no_contractor"
        assert (line["choice"]["kind"], line["choice"]["analog_id"]) == ("contractor", None)
        assert line["target_kind"] == "lowest"
        assert len(_rows(db_session, scene.item)) == 1
        assert _pair(_panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]["file_ours"]) == (
            D("100"),
            D("100"),
        )

    def test_a_saved_choice_with_no_figure_from_the_start_is_reset_too(
        self, admin_client, stage, world, db_session, admin_user
    ):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _hist(stage, D("100"), chain=chain)
        item = _line(stage, part.proposal, D("160"), chain=chain)
        db_session.flush()
        _save(db_session, item, "contractor", None, admin_user.id)

        line = _line_of(_ok(admin_client, stage.tender, rnd, part), item)

        assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "no_contractor")

    def test_the_agreed_price_stays_the_target_over_a_contractor_choice(
        self, admin_client, stage, world, db_session, admin_user
    ):
        scene = _contractor_scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": "contractor"}).status_code == 204
        _agree(db_session, scene.item, D("90"), admin_user)
        db_session.commit()

        line = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        assert line["target_kind"] == "agreed"
        assert line["choice"]["active"] is True


class TestTheTargetIsAStrategy:
    """Цель — стратегия, а не аналог (спека §2.9, §2.16): запись хранит вид, число пересчитывается при
    каждом чтении. Сервер, сохранивший аналог вместо вида, оставил бы цель на прежнем аналоге."""

    @pytest.mark.parametrize(
        ("kind", "cost", "prepared", "own", "before"),
        [
            # свой подрядчик: 2026-07 свежее его прежнего 2026-04
            pytest.param("contractor", D("140"), dt.date(2026, 7, 1), True, D("130"), id="contractor"),
            # чужой подрядчик: тот же месяц, что у самой свежей 150, но ниже по цене — ничья дат решается ценой
            pytest.param("freshest", D("140"), dt.date(2026, 8, 1), False, D("150"), id="freshest"),
        ],
    )
    def test_a_newer_analog_of_the_remainder_becomes_the_target_without_a_new_put(
        self, admin_client, stage, world, db_session, kind, cost, prepared, own, before
    ):
        scene = _contractor_scene(stage, world)
        assert _put(admin_client, stage, scene, {"kind": kind}).status_code == 204
        first = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        assert _pair(first["line"]["file_ours"]) == (before, before)

        _, _, newcomer = _hist(
            stage, cost, chain=scene.chain, prepared=prepared, contractor=scene.part.contractor if own else None
        )
        db_session.flush()
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        line = panel["line"]

        assert newcomer.id in {a["analog_id"] for a in panel["analogs"] if a["selectable"]}
        assert line[kind]["analog_id"] == newcomer.id
        assert line["target_kind"] == kind
        assert (line["choice"]["kind"], line["choice"]["analog_id"], line["choice"]["active"]) == (kind, None, True)
        assert _pair(line["file_ours"]) == (D("140"), D("140"))
        assert "эта работа стоила 140,00 ₽" in panel["comment"]["text"]
        assert _rows(db_session, scene.item) == [(kind, None)]


# ---------------------------------------------------------------------------
#  Форма значения: загрузка вместе со значениями варианта
# ---------------------------------------------------------------------------


@dataclass
class Types:
    param: object
    plates: int
    beams: int
    columns: int
    both: int
    variants: dict[str, object]


def _types(stage, world, db) -> Types:
    """Главный параметр семьи сцены — отпускаемый; значения «плиты», «балки», «колонны» и составное
    «плиты + балки + колонны» с вариантом у каждого."""
    param = db.execute(sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id)).scalar_one()
    param.releasable = True
    plates, beams, columns = (world.value(param, text) for text in ("плиты", "балки", "колонны"))
    both = find_or_create_composite(
        db, parameter_id=param.id, kind="all", part_ids=[plates, beams, columns], context_id=None, source_text=None
    )
    variants = {
        name: world.variant(stage.family, stage.schema, {1: value_id})
        for name, value_id in (("plates", plates), ("beams", beams), ("columns", columns), ("both", both))
    }
    db.flush()
    return Types(param, plates, beams, columns, both, variants)


class TestFormLoading:
    def test_a_composite_value_of_the_variant_is_its_id_and_the_others_are_simple(self, stage, world, db_session):
        types = _types(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        _, part = _kp_part(stage)
        composite_item = _line(stage, part.proposal, D("100"), chain=chain, variant=types.variants["both"])
        simple_item = _line(stage, part.proposal, D("100"), chain=chain, variant=types.variants["plates"])
        _, _, composite_history = _hist(stage, D("100"), chain=chain, variant=types.variants["both"])
        _, _, simple_history = _hist(stage, D("100"), chain=chain, variant=types.variants["beams"])
        db_session.flush()

        rows = {
            r.line.position_item_id: r.line for r in load_kp_lines(db_session, [part.estimate.id])[part.estimate.id]
        }
        history = {h.position_item_id: h for h in _load_work(db_session, stage)[0]}

        assert dict(rows[composite_item.id].forms) == {types.param.id: types.both}
        assert dict(rows[simple_item.id].forms) == {types.param.id: "simple"}
        assert dict(history[composite_history.id].forms) == {types.param.id: types.both}
        assert dict(history[simple_history.id].forms) == {types.param.id: "simple"}

    def test_a_variant_on_a_superseded_schema_has_no_forms(self, stage, world, db_session):
        types = _types(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain, variant=types.variants["both"])
        world.supersede(stage.family, stage.schema)
        db_session.flush()

        (row,) = (
            r
            for r in load_kp_lines(db_session, [part.estimate.id])[part.estimate.id]
            if r.line.position_item_id == item.id
        )

        assert (dict(row.line.main_values), dict(row.line.forms)) == ({}, {})

    def test_the_statements_of_the_loaders_do_not_grow_with_the_rows(self, stage, world, db_session):
        types = _types(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        _, part = _kp_part(stage)
        _line(stage, part.proposal, D("100"), chain=chain, variant=types.variants["both"])
        _hist(stage, D("100"), chain=chain, variant=types.variants["both"])
        with count_statements(db_session) as small:
            load_kp_lines(db_session, [part.estimate.id])
            _load_work(db_session, stage)
        for index in range(8):
            name = ("plates", "beams", "columns", "both")[index % 4]
            _line(stage, part.proposal, D("100") + index, chain=chain, variant=types.variants[name])
            _hist(stage, D("100") + index, chain=chain, variant=types.variants[name])
        db_session.flush()
        with count_statements(db_session) as large:
            load_kp_lines(db_session, [part.estimate.id])
            _load_work(db_session, stage)

        assert len(small) == len(large)
        assert _writes(large) == []


# ---------------------------------------------------------------------------
#  Сборная строка на экране: ступень 2 по форме, причина composite, «по частям»
# ---------------------------------------------------------------------------


def _parts_scene(stage, world, db, *, with_composite_candidate=False, kp=D("100")):
    """Сборная строка КП (плиты + балки + колонны) за `kp`. Плиты 36, 40, 42 — в договорах класса тендера,
    балки 45, 48, 51 — только в другом классе, колонн нет нигде. Составной кандидат (по желанию) — 100."""
    types = _types(stage, world, db)
    chain = world.chain(("Раздел А",), "whole")
    other_class = stage.f.RateClassFactory.create(title="Класс Ч")
    rnd, part = _kp_part(stage)
    ids, contracts = {}, {}
    for key, cost, variant, rate_class in [
        ("p36", D("36"), "plates", "own"),
        ("p40", D("40"), "plates", "own"),
        ("p42", D("42"), "plates", "own"),
        ("b45", D("45"), "beams", other_class),
        ("b48", D("48"), "beams", other_class),
        ("b51", D("51"), "beams", other_class),
    ]:
        contract, _, item = _hist(stage, cost, chain=chain, variant=types.variants[variant], rate_class=rate_class)
        ids[key], contracts[key] = item.id, contract
    if with_composite_candidate:
        contract, _, item = _hist(stage, D("100"), chain=chain, variant=types.variants["both"])
        ids["both"], contracts["both"] = item.id, contract
    item = _line(stage, part.proposal, kp, chain=chain, variant=types.variants["both"])
    db.flush()
    return types, Scene(rnd, part, item, ids, chain, contracts)


class TestCompositeRowOnTheScreen:
    def test_a_composite_row_with_no_composite_candidate_has_no_history_with_the_reason_composite(
        self, admin_client, stage, world, db_session
    ):
        _, scene = _parts_scene(stage, world, db_session)

        line = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert (line["state"], line["no_history_reason"], line["analogs_count"]) == ("no_history", "composite", 0)
        assert line["lowest"] is None and line["contractor"] is None

    def test_a_composite_row_takes_the_composite_candidate_of_its_own_variant_on_rung_one(
        self, admin_client, stage, world, db_session
    ):
        _, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)

        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)

        assert panel["line"]["state"] == "comparable"
        assert panel["line"]["work_rung"]["no"] == 1
        assert [a["analog_id"] for a in panel["analogs"]] == [scene.ids["both"]]

    def test_a_simple_row_does_not_take_a_composite_candidate_on_rung_two(self, admin_client, stage, world, db_session):
        """Строка «колонны» без своей истории берёт на ступени 2 простые «плиты» и не берёт «плиты + балки +
        колонны»."""
        types, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)
        simple = _line(stage, scene.part.proposal, D("38"), chain=scene.chain, variant=types.variants["columns"])
        db_session.flush()

        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, simple)
        analog_ids = {a["analog_id"] for a in panel["analogs"]}

        assert panel["line"]["work_rung"]["no"] == 2
        assert analog_ids == {scene.ids["p36"], scene.ids["p40"], scene.ids["p42"]}


class TestPartsReference:
    def test_every_part_has_its_own_fork_lowest_and_class_mark(self, admin_client, stage, world, db_session):
        types, scene = _parts_scene(stage, world, db_session)

        body = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        (parameter,) = body["parts_reference"]
        plates, beams, columns = parameter["parts"]

        assert (parameter["parameter_id"], parameter["parameter_name"], parameter["kind"]) == (
            types.param.id,
            "Толщина",
            "all",
        )
        assert [p["value_id"] for p in parameter["parts"]] == [types.plates, types.beams, types.columns]
        assert (plates["value"], plates["analogs"], plates["contracts"], plates["other_class"]) == (
            "плиты",
            3,
            3,
            False,
        )
        assert (_pair(plates["min"]), _pair(plates["max"])) == ((D("36"), D("36")), (D("42"), D("42")))
        assert plates["lowest"]["analog_id"] == scene.ids["p36"]
        assert _pair(plates["lowest"]["value"]) == (D("36"), D("36"))
        assert plates["lowest"]["object_title"] == scene.contracts["p36"].object.title
        assert plates["lowest"]["contract_number"] == scene.contracts["p36"].contract_number
        assert plates["lowest"]["period"] == "2025-04"
        assert (beams["value"], beams["analogs"], beams["contracts"], beams["other_class"]) == ("балки", 3, 3, True)
        assert (_pair(beams["min"]), _pair(beams["max"])) == ((D("45"), D("45")), (D("51"), D("51")))
        assert beams["lowest"]["analog_id"] == scene.ids["b45"]

    def test_a_part_without_analogs_is_shown_with_no_analogs_and_the_row_gets_no_target(
        self, admin_client, stage, world, db_session
    ):
        _, scene = _parts_scene(stage, world, db_session)

        body = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        columns = body["parts_reference"][0]["parts"][2]

        assert columns["value"] == "колонны"
        assert (columns["analogs"], columns["contracts"], columns["other_class"]) == (0, 0, False)
        assert (columns["min"], columns["max"], columns["lowest"]) == (None, None, None)
        assert body["line"]["target_kind"] is None
        assert body["analogs"] == []

    def test_the_reference_does_not_count_a_composite_candidate_as_a_part(self, admin_client, stage, world, db_session):
        types, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)

        body = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        parts = {p["value_id"]: p for p in body["parts_reference"][0]["parts"]}

        assert [p["analogs"] for p in parts.values()] == [3, 3, 0]

    def test_a_simple_row_has_an_empty_reference(self, admin_client, stage, world, db_session):
        types, scene = _parts_scene(stage, world, db_session)
        simple = _line(stage, scene.part.proposal, D("38"), chain=scene.chain, variant=types.variants["plates"])
        db_session.flush()

        assert _panel(admin_client, stage.tender, scene.rnd, scene.part, simple)["parts_reference"] == []

    def test_only_the_panel_carries_it_and_the_totals_do_not_move(self, admin_client, stage, world, db_session):
        _, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)

        offer = _ok(admin_client, stage.tender, scene.rnd, scene.part)
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)

        assert "parts_reference" not in offer
        assert all("parts_reference" not in line for line in offer["lines"])
        assert "parts_reference" not in panel["line"]
        # справка не входит в цифры: аналог строки один — составной, его вилка и суммы без частей
        row = _line_of(offer, scene.item)
        assert (row["analogs_count"], _pair(row["lowest"]["value"])) == (1, (D("100"), D("100")))

    def test_the_reading_writes_nothing_and_the_statements_do_not_grow_with_the_analogs(
        self, admin_client, stage, world, db_session
    ):
        types, scene = _parts_scene(stage, world, db_session)
        with count_statements(db_session) as small:
            _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)
        for index in range(6):
            name = ("plates", "beams", "columns")[index % 3]
            _hist(stage, D("60") + index, chain=scene.chain, variant=types.variants[name])
        db_session.flush()
        with count_statements(db_session) as large:
            body = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)

        assert [p["analogs"] for p in body["parts_reference"][0]["parts"]] == [5, 2, 2]
        assert len(small) == len(large)
        assert _writes(large) == []

    def test_an_analog_of_a_part_cannot_become_the_target(self, admin_client, stage, world, db_session):
        _, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)

        response = _put(admin_client, stage, scene, {"kind": "analog", "analog_id": scene.ids["p36"]})

        assert (response.status_code, _code(response)) == (422, "analog_unavailable")
        assert _rows(db_session) == []


class TestCompositeParameter:
    def test_a_composite_row_without_candidates_names_the_parameter_in_the_row_and_the_panel(
        self, admin_client, stage, world, db_session
    ):
        types, scene = _parts_scene(stage, world, db_session)

        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)
        panel = _panel(admin_client, stage.tender, scene.rnd, scene.part, scene.item)["line"]

        for line in (row, panel):
            assert line["no_history_reason"] == "composite"
            assert line["composite_parameter"] == {"id": types.param.id, "name": "Толщина"}

    def test_a_simple_row_has_no_composite_parameter(self, admin_client, stage, world, db_session):
        types, scene = _parts_scene(stage, world, db_session)
        simple = _line(stage, scene.part.proposal, D("38"), chain=scene.chain, variant=types.variants["plates"])
        db_session.flush()

        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), simple)

        assert row["composite_parameter"] is None

    def test_a_composite_row_with_candidates_still_names_the_parameter(self, admin_client, stage, world, db_session):
        types, scene = _parts_scene(stage, world, db_session, with_composite_candidate=True)

        row = _line_of(_ok(admin_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert row["state"] == "comparable"
        assert row["composite_parameter"] == {"id": types.param.id, "name": "Толщина"}

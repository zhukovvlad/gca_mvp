"""Ближайшее значение шкалы в ответах разбора и панели «Против нашей цены» (спека ближайшего значения
§3, §6 п. 8).

Сцена «плита»: главные параметры «Толщина» (обязательный), «Класс бетона», «Марка» (отпускаемые шкалы) и
«Отделка» (отпускаемый, не шкала). Строка КП — плита 50 мм B35 W12. Названия и числа выдуманы; эталоны
посчитаны руками.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import FamilyParameter
from tests.integration.test_analog_scene import _hist, _kp_part, _line
from tests.integration.test_analog_targets import _save
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _line_of, _ok, _panel
from tests.integration.test_our_price_round_api import Stage
from tests.integration.test_ref_analogs_panel import _strict

pytestmark = pytest.mark.integration

D = Decimal

CLASS = "Класс бетона"
MARK = "Марка"
FINISH = "Отделка"
THICKNESS = "Толщина"


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


def _mains(stage, world, db, *, classes=("B35", "B40", "B60"), marks=("W8", "W12")):
    thickness = db.execute(
        sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id, FamilyParameter.ordinal == 1)
    ).scalar_one()
    thickness.name = THICKNESS
    klass = world.param(stage.schema, 2, CLASS)
    mark = world.param(stage.schema, 3, MARK)
    finish = world.param(stage.schema, 4, FINISH)
    for parameter in (klass, mark, finish):
        parameter.releasable = True
    db.flush()
    values = {"t50": world.value(thickness, "50 мм"), "fX": world.value(finish, "Икс"), "fY": world.value(finish, "Игрек")}
    values.update({text: world.value(klass, text) for text in classes})
    values.update({text: world.value(mark, text) for text in marks})
    return SimpleNamespace(v=values)


def _variant(stage, world, mains, klass, mark, finish="fX"):
    pick = lambda key: None if key is None else mains.v[key]  # noqa: E731
    return world.variant(
        stage.family, stage.schema, {1: mains.v["t50"], 2: pick(klass), 3: pick(mark), 4: pick(finish)}
    )


def _history_row(stage, world, mains, chain, klass, mark):
    return _hist(stage, D("100"), chain=chain, variant=_variant(stage, world, mains, klass, mark))[2]


def _kp_line(stage, world, db, mains, chain, klass="B35", mark="W12", finish="fX"):
    rnd, part = _kp_part(stage)
    item = _line(
        stage, part.proposal, D("120"), chain=chain, variant=_variant(stage, world, mains, klass, mark, finish)
    )
    db.flush()
    return rnd, part, item


_SAME = object()


def _plate_scene(stage, world, db, *, chain=None, kp_chain=_SAME):
    """B40 W12 в двух договорах, B40 W8 в одном, B60 W12 в трёх; строка КП — B35 W12."""
    mains = _mains(stage, world, db)
    chain = chain or world.chain(("Раздел А",), "whole")
    rows = {
        key: _history_row(stage, world, mains, chain, klass, mark)
        for key, klass, mark in (
            (1, "B40", "W12"),
            (2, "B40", "W12"),
            (3, "B40", "W8"),
            (4, "B60", "W12"),
            (5, "B60", "W12"),
            (6, "B60", "W12"),
        )
    }
    rnd, part, item = _kp_line(stage, world, db, mains, chain if kp_chain is _SAME else kp_chain)
    return SimpleNamespace(mains=mains, rows=rows, rnd=rnd, part=part, item=item, chain=chain)


def _row_and_panel(client, stage, scene):
    row = _line_of(_ok(client, stage.tender, scene.rnd, scene.part), scene.item)
    panel = _panel(client, stage.tender, scene.rnd, scene.part, scene.item)
    return row, panel


KLASS_VIEW = {
    "name": CLASS,
    "kp_value": "B35",
    "values": ["B40"],
    "kp_contracts": 0,
    "direction": "up",
    "without_value": False,
    "after": [],
}
MARK_VIEW = {
    "name": MARK,
    "kp_value": "W12",
    "values": ["W8", "W12"],
    "kp_contracts": 2,
    "direction": "down",
    "without_value": False,
    "after": [CLASS],
}


def _match_and_nearest(row):
    return {p["name"]: (p["match"], p["nearest"]) for p in row["params"]}


class TestRowOfTheSlab:
    def test_nearest_comes_in_parameter_order_with_every_field_and_released_without_them(
        self, member_client, stage, world, db_session
    ):
        scene = _plate_scene(stage, world, db_session)

        row, panel = _row_and_panel(member_client, stage, scene)

        assert row["work_rung"]["no"] == 2
        assert row["work_rung"]["nearest"] == [KLASS_VIEW, MARK_VIEW]
        assert row["work_rung"]["released"] == [FINISH]
        assert panel["line"]["work_rung"] == row["work_rung"]

    def test_contracts_count_is_the_number_of_distinct_analog_contracts(self, member_client, stage, world, db_session):
        """Четвёртый договор с двумя строками: аналогов четыре, договоров три (у КП B40 W12 в трёх)."""
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        for _ in range(2):
            _history_row(stage, world, mains, chain, "B40", "W12")
        contract = stage.world.contract(area=D("1000"), rate_class=stage.rate_class)
        estimate, proposal = stage.world.estimate(contract, amendment_no=None, vat_rate=D("0"))
        estimate.data_prepared_on_date = dt.date(2025, 4, 1)
        for _ in range(2):
            _line(stage, proposal, D("100"), chain=chain, variant=_variant(stage, world, mains, "B40", "W12"))
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert row["analogs_count"] == 4
        assert row["contracts_count"] == 3

    def test_params_carry_match_and_nearest_only_where_the_ladder_narrowed(
        self, member_client, stage, world, db_session
    ):
        scene = _plate_scene(stage, world, db_session)

        row, _ = _row_and_panel(member_client, stage, scene)

        assert _match_and_nearest(row) == {
            THICKNESS: ("required", None),
            CLASS: ("nearest", {"values": ["B40"], "without_value": False}),
            MARK: ("nearest", {"values": ["W8", "W12"], "without_value": False}),
            FINISH: ("ignored", None),
        }

    def test_a_scale_whose_numbers_all_equal_the_kp_is_matched_and_has_no_nearest(
        self, member_client, stage, world, db_session
    ):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        for _ in range(3):
            _history_row(stage, world, mains, chain, "B35", "W12")
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain, "B35", "W12", "fY")

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert row["work_rung"]["no"] == 2
        assert row["work_rung"]["nearest"] == []
        assert row["work_rung"]["released"] == [FINISH]
        found = _match_and_nearest(row)
        assert (found[CLASS], found[MARK]) == (("matched", None), ("matched", None))

    def test_a_row_without_a_rung_has_no_work_rung_and_no_contracts(self, member_client, stage, world, db_session):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert row["work_rung"] is None
        assert row["contracts_count"] == 0
        assert all(p["nearest"] is None for p in row["params"])

    def test_rung_one_has_an_empty_nearest_and_no_new_match_values(self, member_client, stage, world, db_session):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        for _ in range(3):
            _history_row(stage, world, mains, chain, "B35", "W12")
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert row["work_rung"]["no"] == 1
        assert row["work_rung"]["nearest"] == []
        assert row["contracts_count"] == 3
        assert {p["match"] for p in row["params"] if p["role"] == "main"} == {"required"}
        assert all(p["nearest"] is None for p in row["params"])

    def test_the_catalog_rule_of_a_scale_stays_ignorable_without_a_rung(self, member_client, stage, world, db_session):
        """Основание `catalog`, шкала отпускаемая: «можно не учитывать», как и раньше (уточнение дизайна)."""
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert row["params_basis"] == "catalog"
        found = _match_and_nearest(row)
        assert (found[CLASS], found[MARK]) == (("ignored", None), ("ignored", None))


class TestSideOfTheCell:
    def test_the_arrow_side_is_up_or_down_for_numbers_that_differ_and_null_otherwise(
        self, member_client, stage, world, db_session
    ):
        scene = _plate_scene(stage, world, db_session)

        _, panel = _row_and_panel(member_client, stage, scene)

        assert [c["name"] for c in panel["analog_columns"]] == [CLASS, MARK, FINISH]
        sides = {a["analog_id"]: [c["side"] for c in a["released_values"]] for a in panel["analogs"]}
        # B40 при КП B35 — вверх; W12 при W12 и нешкальная «Отделка» — без стороны; W8 при W12 — вниз
        assert sides == {
            scene.rows[1].id: ["up", None, None],
            scene.rows[2].id: ["up", None, None],
            scene.rows[3].id: ["up", "down", None],
        }

    def test_a_matched_number_in_another_letter_form_has_no_side(self, member_client, stage, world, db_session):
        """КП B35, аналог «В35» кириллицей: число равно, текст другой — стрелки нет, значение отличается."""
        mains = _mains(stage, world, db_session, classes=("B35", "В35", "B40"))
        chain = world.chain(("Раздел А",), "whole")
        rows = [_history_row(stage, world, mains, chain, "В35", "W12") for _ in range(3)]
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        panel = _panel(member_client, stage.tender, rnd, part, item)

        cell = {a["analog_id"]: a for a in panel["analogs"]}[rows[0].id]["released_values"][0]
        assert (cell["value"], cell["differs"], cell["side"]) == ("В35", True, None)

    def test_an_analog_without_a_number_has_no_side(self, member_client, stage, world, db_session):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        numbered = [_history_row(stage, world, mains, chain, "B40", "W12") for _ in range(2)]
        unset = _history_row(stage, world, mains, chain, None, "W12")
        rnd, part, item = _kp_line(stage, world, db_session, mains, chain)

        row = _line_of(_ok(member_client, stage.tender, rnd, part), item)
        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert row["work_rung"]["nearest"][0]["without_value"] is True
        by_id = {a["analog_id"]: a for a in panel["analogs"]}
        assert (by_id[unset.id]["released_values"][0]["value"], by_id[unset.id]["released_values"][0]["side"]) == (
            None,
            None,
        )
        assert [by_id[a.id]["released_values"][0]["side"] for a in numbered] == ["up", "up"]


class TestZoneGroup:
    def test_a_zone_group_carries_the_same_nearest_as_the_ladder_of_the_row(
        self, member_client, stage, world, db_session
    ):
        _strict(stage)
        underground = world.chain(("Корпус", "Подземная часть"), "underground")
        scene = _plate_scene(stage, world, db_session, chain=underground, kp_chain=world.chain(("Раздел Б",), "aboveground"))

        row, panel = _row_and_panel(member_client, stage, scene)

        assert row["no_history_reason"] == "no_zone_match"
        (group,) = panel["reference_zones"]
        assert (group["zone"], group["rung_no"]) == ("underground", 2)
        assert group["nearest"] == [KLASS_VIEW, MARK_VIEW]
        assert group["released"] == [FINISH]


class TestAnalogTargetAfterNarrowing:
    def test_a_target_on_a_row_the_narrowing_dropped_resets_with_analog_unavailable(
        self, member_client, stage, world, db_session, admin_user
    ):
        scene = _plate_scene(stage, world, db_session)
        _save(db_session, scene.item, "analog", scene.rows[6].id, admin_user.id)

        row = _line_of(_ok(member_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert row["choice"]["active"] is False
        assert row["choice"]["reset_reason"] == "analog_unavailable"

    def test_a_target_inside_the_narrowed_set_stays_active(self, member_client, stage, world, db_session, admin_user):
        scene = _plate_scene(stage, world, db_session)
        _save(db_session, scene.item, "analog", scene.rows[3].id, admin_user.id)

        row = _line_of(_ok(member_client, stage.tender, scene.rnd, scene.part), scene.item)

        assert (row["choice"]["active"], row["choice"]["reset_reason"]) == (True, None)

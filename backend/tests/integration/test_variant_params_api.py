"""Параметры варианта в ответах разбора и панели «Против нашей цены» (спека параметров варианта
§2.3, §2.4, §2.5, §2.10).

Сцены строятся помощниками `test_analog_scene`: главные параметры «Толщина» (обязательный),
«Марка» и «Отделка» (отпускаемые). Эталон правил — таблица §2.3, записанная литералами.
Названия и числа выдуманы.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud.our_price_load import load_kp_lines
from models import ContextParameterValue, FamilyParameter
from services.composite_values import find_or_create_composite
from tests.integration.test_analog_scene import _hist, _kp_part, _line, _load_work
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    _line_of,
    _ok,
    _panel,
    _put,
    _system_scene,
)
from tests.integration.test_our_price_round_api import Stage, count_statements

pytestmark = pytest.mark.integration

D = Decimal

PARAM_KEYS = {
    "name",
    "role",
    "value",
    "off_list",
    "composite",
    "source",
    "refreshing",
    "catalog_value",
    "match",
}


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


def _mains(stage, world, db):
    """Главные параметры семьи сцены: «Толщина» (1, обязательный), «Марка» (2) и «Отделка» (3) — отпускаемые."""
    thickness = db.execute(
        sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id, FamilyParameter.ordinal == 1)
    ).scalar_one()
    mark = world.param(stage.schema, 2, "Марка")
    finish = world.param(stage.schema, 3, "Отделка")
    mark.releasable = True
    finish.releasable = True
    db.flush()
    values = {
        "t50": world.value(thickness, "50 мм"),
        "t80": world.value(thickness, "80 мм"),
        "mA": world.value(mark, "А"),
        "mB": world.value(mark, "Б"),
        "fX": world.value(finish, "Икс"),
        "fY": world.value(finish, "Игрек"),
    }
    mAB = find_or_create_composite(
        db, parameter_id=mark.id, kind="all", part_ids=[values["mA"], values["mB"]], context_id=None, source_text=None
    )
    values["mAB"] = mAB
    return SimpleNamespace(thickness=thickness, mark=mark, finish=finish, v=values)


def _variant(stage, world, mains, thickness, mark, finish):
    """Вариант семьи по ключам значений `mains.v`; `None` — «не уточнено»."""
    pick = lambda key: None if key is None else mains.v[key]  # noqa: E731
    return world.variant(stage.family, stage.schema, {1: pick(thickness), 2: pick(mark), 3: pick(finish)})


def _params(line: dict) -> dict[str, dict]:
    return {p["name"]: p for p in line["params"]}


def _match(line: dict) -> list:
    return [p["match"] for p in line["params"]]


def _kp_and_panel(client, stage, rnd, part, item):
    row = _line_of(_ok(client, stage.tender, rnd, part), item)
    panel = _panel(client, stage.tender, rnd, part, item)
    return row, panel


def _released_scene(stage, world, db, *, history, kp=("t50", "mA", "fX")):
    """Строка КП и договоры истории; `history` — перечень `(ключи варианта, цена)`."""
    mains = _mains(stage, world, db)
    chain = world.chain(("Раздел А",), "whole")
    for keys, cost in history:
        _hist(stage, cost, chain=chain, variant=_variant(stage, world, mains, *keys))
    rnd, part = _kp_part(stage)
    item = _line(stage, part.proposal, D("120"), chain=chain, variant=_variant(stage, world, mains, *kp))
    db.flush()
    return mains, rnd, part, item, chain


class TestParamsOfTheRow:
    def test_rung_one_requires_every_main_and_the_row_equals_the_panel_line(
        self, member_client, stage, world, db_session
    ):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[(("t50", "mA", "fX"), D("100"))])

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == 1
        assert (row["params_basis"], panel["line"]["params_basis"]) == ("rung", "rung")
        assert [p["name"] for p in row["params"]] == ["Толщина", "Марка", "Отделка"]
        assert _match(row) == ["required", "required", "required"]
        assert row["params"] == panel["line"]["params"]
        assert all(set(p) == PARAM_KEYS for p in row["params"])

    def test_rung_two_requires_the_unreleased_and_ignores_a_released_simple_main(
        self, member_client, stage, world, db_session
    ):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[(("t50", "mB", "fY"), D("100"))])

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == 2
        assert row["params_basis"] == "rung"
        assert _match(row) == ["required", "ignored", "ignored"]
        assert row["params"] == panel["line"]["params"]

    def test_a_composite_released_main_is_matched_by_the_set(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(
            stage, world, db_session, history=[(("t50", "mAB", "fY"), D("100"))], kp=("t50", "mAB", "fX")
        )

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == 2
        assert _match(row) == ["required", "same_set", "ignored"]
        assert _params(row)["Марка"]["composite"] is True
        assert _params(row)["Марка"]["value"] == "А + Б"
        assert _params(row)["Отделка"]["composite"] is False

    def test_rung_three_requires_every_main_even_for_released_ones(
        self, member_client, stage, world, db_session, factories
    ):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        same = _variant(stage, world, mains, "t50", "mA", "fX")
        _hist(stage, D("100"), chain=chain, variant=same, rate_class=other_class)
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain, variant=same)
        db_session.flush()

        row, _ = _kp_and_panel(member_client, stage, rnd, part, item)

        assert row["work_rung"]["no"] == 3
        assert (row["params_basis"], _match(row)) == ("rung", ["required", "required", "required"])

    def test_a_work_with_a_variant_and_no_rung_shows_the_catalog_rule(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[])

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert row["work_rung"] is None
        assert (row["params_basis"], _match(row)) == ("catalog", ["required", "ignored", "ignored"])
        assert row["params"] == panel["line"]["params"]

    def test_a_composite_main_under_the_catalog_rule_is_same_set(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[], kp=("t50", "mAB", "fX"))

        row, _ = _kp_and_panel(member_client, stage, rnd, part, item)

        assert (row["params_basis"], _match(row)) == ("catalog", ["required", "same_set", "ignored"])

    def test_a_line_without_a_variant_has_no_basis_and_no_params(self, member_client, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain, variant=None)
        db_session.flush()

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert (row["params_basis"], row["params"]) == (None, [])
        assert (panel["line"]["params_basis"], panel["line"]["params"]) == (None, [])

    def test_a_system_on_a_rung_requires_the_mains_of_its_variant(self, member_client, stage, world, db_session):
        rnd, part, items = _system_scene(stage)

        row, panel = _kp_and_panel(member_client, stage, rnd, part, items[0])

        assert panel["line"]["rung"]["level"] == "v"
        assert (row["params_basis"], _match(row)) == ("rung", ["required"])
        assert row["params"] == panel["line"]["params"]

    def test_a_system_without_a_rung_has_no_basis_and_null_matches(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("0"), total=D("0"), kind="SYSTEM")
        stage.db.flush()

        row, _ = _kp_and_panel(member_client, stage, rnd, part, item)

        assert (row["state"], row["rung"]) == ("no_price", None)
        assert row["params_basis"] is None
        assert [p["name"] for p in row["params"]] == ["Толщина"]
        assert _match(row) == [None]

    def test_a_secondary_is_ignored_on_a_rung(self, member_client, stage, world, db_session):
        world.param(stage.schema, 4, "Цвет", role="secondary")
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[(("t50", "mA", "fX"), D("100"))])

        row, panel = _kp_and_panel(member_client, stage, rnd, part, item)

        assert row["work_rung"]["no"] == 1
        assert [(p["name"], p["role"], p["match"]) for p in row["params"] if p["role"] == "secondary"] == [
            ("Цвет", "secondary", "ignored")
        ]
        assert row["params"] == panel["line"]["params"]

    def test_a_secondary_has_no_match_without_a_basis(self, member_client, stage, world):
        world.param(stage.schema, 2, "Цвет", role="secondary")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("0"), total=D("0"), kind="SYSTEM")
        stage.db.flush()

        row, _ = _kp_and_panel(member_client, stage, rnd, part, item)

        assert row["params_basis"] is None
        assert [(p["name"], p["match"]) for p in row["params"]] == [("Толщина", None), ("Цвет", None)]


class TestReleasedColumns:
    def test_the_columns_are_the_released_mains_by_ordinal_and_each_analog_has_a_value_per_column(
        self, member_client, stage, world, db_session
    ):
        _, rnd, part, item, _ = _released_scene(
            stage,
            world,
            db_session,
            history=[(("t50", "mB", "fX"), D("100")), (("t50", "mA", "fY"), D("110"))],
        )

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["work_rung"] == {"no": 2, "released": ["Марка", "Отделка"], "other_class": False}
        assert panel["analog_columns"] == [{"name": "Марка"}, {"name": "Отделка"}]
        by_value = {
            a["released_values"][0]["value"] + "/" + a["released_values"][1]["value"]: a for a in panel["analogs"]
        }
        assert by_value["Б/Икс"]["released_values"] == [
            {"value": "Б", "conflict": False, "differs": True},
            {"value": "Икс", "conflict": False, "differs": False},
        ]
        assert by_value["А/Игрек"]["released_values"] == [
            {"value": "А", "conflict": False, "differs": False},
            {"value": "Игрек", "conflict": False, "differs": True},
        ]

    def test_a_composite_value_is_shown_by_its_text(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(
            stage, world, db_session, history=[(("t50", "mAB", "fY"), D("100"))], kp=("t50", "mAB", "fX")
        )

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (analog,) = panel["analogs"]
        assert analog["released_values"][0] == {"value": "А + Б", "conflict": False, "differs": False}

    @pytest.mark.parametrize(
        ("own_class", "history", "expected_rung"),
        [
            (True, ("t50", "mA", "fX"), 1),
            (False, ("t50", "mA", "fX"), 3),
            (True, ("t50", "mB", "fX"), 2),
            (False, ("t50", "mB", "fX"), 4),
        ],
    )
    def test_columns_exist_only_on_rungs_two_and_four(
        self, member_client, stage, world, db_session, factories, own_class, history, expected_rung
    ):
        mains = _mains(stage, world, db_session)
        chain = world.chain(("Раздел А",), "whole")
        extra = {} if own_class else {"rate_class": factories.RateClassFactory.create(title="Класс Ч")}
        _hist(stage, D("100"), chain=chain, variant=_variant(stage, world, mains, *history), **extra)
        rnd, part = _kp_part(stage)
        item = _line(
            stage, part.proposal, D("120"), chain=chain, variant=_variant(stage, world, mains, "t50", "mA", "fX")
        )
        db_session.flush()

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == expected_rung
        assert len(panel["analogs"]) == 1
        if expected_rung in (2, 4):
            assert panel["analog_columns"] == [{"name": "Марка"}, {"name": "Отделка"}]
            assert len(panel["analogs"][0]["released_values"]) == 2
        else:
            assert panel["analog_columns"] == []
            assert panel["analogs"][0]["released_values"] == []

    def test_a_row_without_a_rung_and_a_system_have_empty_columns(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[])
        no_rung = _panel(member_client, stage.tender, rnd, part, item)
        s_rnd, s_part, s_items = _system_scene(stage)
        system = _panel(member_client, stage.tender, s_rnd, s_part, s_items[0])

        assert (no_rung["analog_columns"], no_rung["analogs"]) == ([], [])
        assert (system["analog_columns"], system["analogs"]) == ([], [])

    def test_a_conflict_marks_the_value_and_differs_even_with_the_same_value_id(
        self, member_client, stage, world, db_session
    ):
        mains, rnd, part, item, chain = _released_scene(stage, world, db_session, history=[])
        _, _, analog = _hist(stage, D("100"), chain=chain, variant=_variant(stage, world, mains, "t50", "mA", "fX"))
        db_session.add(
            ContextParameterValue(
                context_id=analog.context_id,
                schema_id=stage.schema.id,
                parameter_id=mains.mark.id,
                value_id=None,
                source="path_conflict",
            )
        )
        db_session.flush()

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == 2
        (found,) = panel["analogs"]
        assert found["released_values"][0]["conflict"] is True
        assert found["released_values"][0]["differs"] is True
        assert found["released_values"][1] == {"value": "Икс", "conflict": False, "differs": False}

    def test_a_conflict_of_the_kp_row_differs_even_with_the_same_value_id(
        self, member_client, stage, world, db_session
    ):
        mains, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[(("t50", "mA", "fX"), D("100"))])
        db_session.add(
            ContextParameterValue(
                context_id=item.context_id,
                schema_id=stage.schema.id,
                parameter_id=mains.mark.id,
                value_id=None,
                source="path_conflict",
            )
        )
        db_session.flush()

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["work_rung"]["no"] == 2
        (found,) = panel["analogs"]
        assert found["released_values"] == [
            {"value": "А", "conflict": False, "differs": True},
            {"value": "Икс", "conflict": False, "differs": False},
        ]

    def test_two_empty_values_without_a_conflict_do_not_differ(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(
            stage, world, db_session, history=[(("t50", None, "fY"), D("100"))], kp=("t50", None, "fX")
        )

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (analog,) = panel["analogs"]
        assert analog["released_values"][0] == {"value": None, "conflict": False, "differs": False}
        assert analog["released_values"][1]["differs"] is True

    def test_an_empty_analog_value_against_a_filled_one_differs(self, member_client, stage, world, db_session):
        _, rnd, part, item, _ = _released_scene(stage, world, db_session, history=[(("t50", None, "fX"), D("100"))])

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (analog,) = panel["analogs"]
        assert analog["released_values"][0] == {"value": None, "conflict": False, "differs": True}


class TestEveryAnalogMatchesTheRow:
    def test_composite_released_row_takes_only_analogs_with_the_same_required_values_and_form(
        self, member_client, stage, world, db_session
    ):
        """Сквозное: строка КП — составная «Марка» и обязательная «Толщина». Среди кандидатов есть
        отличающиеся простым значением «Отделки» (лестница берёт), с простой «Маркой» и с другой толщиной
        (не берёт). Каждый аналог ответа сверяется со строкой КП по `match` ответа."""
        mains, rnd, part, item, _ = _released_scene(
            stage,
            world,
            db_session,
            history=[
                (("t50", "mAB", "fY"), D("100")),
                (("t50", "mAB", None), D("105")),
                (("t50", "mA", "fX"), D("90")),
                (("t80", "mAB", "fX"), D("80")),
            ],
            kp=("t50", "mAB", "fX"),
        )
        panel = _panel(member_client, stage.tender, rnd, part, item)
        history = {line.position_item_id: line for line in _load_work(db_session, stage)[0]}
        kp_line = next(
            r.line
            for r in load_kp_lines(db_session, [part.estimate.id])[part.estimate.id]
            if r.line.position_item_id == item.id
        )
        match = {p["name"]: p["match"] for p in panel["line"]["params"]}
        by_id = {"Толщина": mains.thickness.id, "Марка": mains.mark.id, "Отделка": mains.finish.id}

        assert panel["line"]["work_rung"]["no"] == 2
        assert len(panel["analogs"]) == 2
        for analog in panel["analogs"]:
            line = history[analog["analog_id"]]
            for name, rule in match.items():
                parameter_id = by_id[name]
                if rule == "required":
                    assert line.main_values[parameter_id] == kp_line.main_values[parameter_id]
                if rule == "same_set":
                    assert line.forms[parameter_id] == kp_line.forms[parameter_id]

        # Обратная сторона: каждый кандидат сцены, совпадающий со строкой КП по `match` ответа, взят
        # аналогом. Без неё правило, ослабленное до `ignored` у составного отпускаемого, прошло бы:
        # кандидат с простой «Маркой» удовлетворял бы ему, а лестница его не берёт.
        def fits(line) -> bool:
            return all(
                (rule != "required" or line.main_values.get(by_id[name]) == kp_line.main_values.get(by_id[name]))
                and (rule != "same_set" or line.forms.get(by_id[name]) == kp_line.forms.get(by_id[name]))
                for name, rule in match.items()
            )

        assert len(history) == 4
        assert {analog["analog_id"] for analog in panel["analogs"]} == {
            line.position_item_id for line in history.values() if fits(line)
        }


class TestStatements:
    def test_the_panel_runs_the_same_statements_on_one_and_five_rung_two_analogs(
        self, member_client, stage, world, db_session
    ):
        mains, rnd, part, item, chain = _released_scene(
            stage, world, db_session, history=[(("t50", "mB", "fX"), D("100"))]
        )
        db_session.commit()
        with count_statements(db_session) as small:
            first = _panel(member_client, stage.tender, rnd, part, item)
        for index in range(4):
            _hist(stage, D("101") + index, chain=chain, variant=_variant(stage, world, mains, "t50", "mB", "fY"))
        db_session.commit()
        with count_statements(db_session) as large:
            second = _panel(member_client, stage.tender, rnd, part, item)

        assert (len(first["analogs"]), len(second["analogs"])) == (1, 5)
        assert len(small) == len(large)

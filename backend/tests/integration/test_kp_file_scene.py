"""Сцена «Против нашей цены» читает решения по строкам КП (спека 4б §2.5, §2.6, §2.11).

`crud/kp_decisions.py` загружает решения одним вызовом на все сметы этапа; этап,
разбор участника и панель строки считают одну сцену с сохранёнными ступенями, а
строки несут `agreed`, `comment_kind` и `manual_rung`. Сцены строятся помощниками
экрана этапа; названия и числа выдуманы, эталоны — числа и строки, посчитанные
руками. Каждое правило проверено с обеих сторон: вход, где оно срабатывает, и
соседний вход, нарушающий ровно одно условие.

Цена истории — 0 %, поэтому нетто равно валовой цене; КП участников — 0 % либо 20 %.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import ROUND_HALF_UP, Decimal

import pytest
import sqlalchemy as sa

from crud import kp_decisions as kd
from crud.our_price_load import load_kp_lines
from models import (
    EstimateRawData,
    KpFamilyRung,
    KpLineAgreement,
    KpLineComment,
    KpLineRung,
    PositionItem,
    UnitOfMeasure,
)
from services.our_price import LineKey, Rung, SavedFamilyRung, SavedWorkRung
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    FACTOR_FEB_2025,
    LINE_KEYS,
    PANEL_KEYS,
    SERIES_NAME,
    _history,
    _inflation_scene,
    _line_of,
    _offer_article,
    _pair,
    _panel,
    _panel_get,
    _put,
    _second_variant,
)
from tests.integration.test_our_price_offer_api import _get as _offer_get
from tests.integration.test_our_price_offer_api import _json as _offer_json
from tests.integration.test_our_price_offer_api import _ok as _offer_ok
from tests.integration.test_our_price_round_api import (
    Stage,
    _article,
    _cell,
    _participant,
    count_statements,
)
from tests.integration.test_our_price_round_api import _ok as _round_ok

pytestmark = pytest.mark.integration

D = Decimal
SET_AT = dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.UTC)


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники: решения и сцены
# ---------------------------------------------------------------------------


def _agree(db, item, net, user, *, at=SET_AT):
    db.add(KpLineAgreement(position_item_id=item.id, net_price=net, set_by=user.id, set_at=at))
    db.flush()


def _comment(db, item, text, user, *, at=SET_AT):
    db.add(KpLineComment(position_item_id=item.id, text=text, set_by=user.id, set_at=at))
    db.flush()


def _line_rung(db, item, level, scope, key, user, *, at=SET_AT):
    db.add(KpLineRung(position_item_id=item.id, level=level, scope=scope, key=key, set_by=user.id, set_at=at))
    db.flush()


def _family_rung(db, estimate, family, level, scope, user, *, first_main=None, at=SET_AT):
    db.add(
        KpFamilyRung(
            estimate_id=estimate.id,
            work_family_id=family.id,
            level=level,
            scope=scope,
            first_main_parameter_id=first_main,
            set_by=user.id,
            set_at=at,
        )
    )
    db.flush()


def _work_scene(stage, *, kp_price=D("150")):
    """История класса: вариант 1 — 100 и 140, вариант 2 — 10; КП (НДС 0 %) на варианте 1
    с ценой `kp_price` за единицу при объёме 10.

    Авто — «точный вариант, класс»: ставки 100 и 140, расценка 100, перебор
    `(150 − 100) × 10 = 500`. Ступень «семья, класс» берёт и вариант 2: ставки
    100, 140 и 10 — договор с ценой 10 при медиане остальных 120 выходит за границу
    `0,2`, и строка получает расхождение единиц.
    """
    stage.history(D("100"))
    stage.history(D("140"))
    _history(stage, D("10"), variant=_second_variant(stage))
    rnd = stage.round()
    part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
    item = stage.kp(part, kp_price)
    return rnd, part, item


def _system_scene(stage):
    """Две сметы этапа с одной системной строкой семьи: сумма 90 000 при площади 1000,
    то есть 90 ₽/м². История — вариант 1: 100 000 и 140 000 (100 и 140 ₽/м²),
    вариант 2: 10 000 (10 ₽/м²). Авто — «точный вариант, класс»; ступень «семья,
    класс» добавляет вариант 2 и даёт расхождение единиц (10 против медианы 120)."""
    for total in (100000, 140000):
        _history(stage, D("1"), total=D(total), kind="SYSTEM")
    _history(stage, D("1"), total=D(10000), kind="SYSTEM", variant=_second_variant(stage))
    rnd = stage.round()
    first = stage.participant(rnd, "Подрядчик А", vat=D("0"))
    second = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
    items = [
        _put(stage, part.proposal, "R", D("1"), total=D(90000), kind="SYSTEM") for part in (first, second)
    ]
    return rnd, first, second, items[0], items[1]


def _user(factories):
    return factories.UserFactory.create()


def _who(user) -> dict:
    return {"id": user.id, "email": user.email}


def _iso(text: str) -> dt.datetime:
    """Ревью: время в ответе — ISO 8601 с `T` и смещением, как у соседних полей 4а;
    `fromisoformat` одна приняла бы и `str(datetime)` с пробелом."""
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d", text), text
    return dt.datetime.fromisoformat(text)


# ---------------------------------------------------------------------------
#  Снимок ключа ступени
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_to_json_names_the_key_of_each_level(self):
        assert kd.snapshot_to_json("v", 7) == {"level": "v", "variant_id": 7}
        assert kd.snapshot_to_json("p", (5, (3, 9))) == {
            "level": "p",
            "family_id": 5,
            "parameter_id": 3,
            "value_id": 9,
        }
        assert kd.snapshot_to_json("f", 5) == {"level": "f", "family_id": 5}

    def test_unspecified_first_value_stays_null(self):
        assert kd.snapshot_to_json("p", (5, (3, None))) == {
            "level": "p",
            "family_id": 5,
            "parameter_id": 3,
            "value_id": None,
        }

    def test_from_json_gives_the_hashable_the_core_compares(self):
        assert kd.snapshot_from_json({"level": "v", "variant_id": 7}) == 7
        assert kd.snapshot_from_json({"level": "p", "family_id": 5, "parameter_id": 3, "value_id": 9}) == (5, (3, 9))
        assert kd.snapshot_from_json({"level": "p", "family_id": 5, "parameter_id": 3, "value_id": None}) == (
            5,
            (3, None),
        )
        assert kd.snapshot_from_json({"level": "f", "family_id": 5}) == 5

    @pytest.mark.parametrize("first_main", [(3, 9), (3, None)])
    def test_the_pair_is_mutually_inverse_on_the_three_levels(self, first_main):
        key = LineKey(family_id=5, variant_id=7, first_main=first_main)
        for level in ("v", "p", "f"):
            stored = json.loads(json.dumps(kd.snapshot_to_json(level, key.at(level))))
            assert kd.snapshot_from_json(stored) == key.at(level), level


# ---------------------------------------------------------------------------
#  Загрузка решений
# ---------------------------------------------------------------------------


class TestLoadDecisions:
    def test_each_table_lands_in_its_own_estimate(self, stage, db_session, factories):
        rnd = stage.round()
        a = stage.participant(rnd, "Подрядчик А")
        b = stage.participant(rnd, "Подрядчик Б")
        c = stage.participant(rnd, "Подрядчик В")
        item_a1 = stage.kp(a, D("100"))
        item_a2 = stage.kp(a, D("110"))
        stage.kp(b, D("100"))
        stage.kp(c, D("100"))
        u1, u2 = _user(factories), _user(factories)
        net = D("100.00000000000000000001")
        _agree(db_session, item_a1, net, u1)
        _comment(db_session, item_a2, "Текст", u2)
        _line_rung(
            db_session,
            item_a2,
            "p",
            "all",
            {"level": "p", "family_id": 7, "parameter_id": 3, "value_id": None},
            u1,
        )
        _family_rung(db_session, a.estimate, stage.family, "p", "all", u2, first_main=3)
        _family_rung(db_session, b.estimate, stage.family, "f", "cls", u1)

        found = kd.load_decisions(db_session, [a.estimate.id, b.estimate.id, c.estimate.id])

        assert set(found) == {a.estimate.id, b.estimate.id, c.estimate.id}
        first = found[a.estimate.id]
        assert first.agreements == {item_a1.id: (net, SET_AT, u1.id, u1.email)}
        assert first.comments == {item_a2.id: ("Текст", SET_AT, u2.id, u2.email)}
        assert dict(first.saved.work) == {item_a2.id: SavedWorkRung(Rung("p", "all"), (7, (3, None)))}
        assert dict(first.saved.family) == {stage.family.id: SavedFamilyRung(Rung("p", "all"), 3)}
        assert first.rung_meta == {
            ("line", item_a2.id): (SET_AT, u1.id, u1.email),
            ("family", stage.family.id): (SET_AT, u2.id, u2.email),
        }
        second = found[b.estimate.id]
        assert (second.agreements, second.comments, dict(second.saved.work)) == ({}, {}, {})
        assert dict(second.saved.family) == {stage.family.id: SavedFamilyRung(Rung("f", "cls"), None)}
        assert second.rung_meta == {("family", stage.family.id): (SET_AT, u1.id, u1.email)}
        empty = found[c.estimate.id]
        assert (empty.agreements, empty.comments, dict(empty.saved.work), dict(empty.saved.family)) == (
            {},
            {},
            {},
            {},
        )
        assert empty.rung_meta == {}

    def test_decisions_of_estimates_not_asked_for_stay_out(self, stage, db_session, factories):
        """Ревью: решения смет, которых нет в запросе (соседний этап, другой тендер),
        не попадают в ответ и не роняют загрузку."""
        rnd = stage.round()
        asked = stage.participant(rnd, "Подрядчик А")
        other = stage.participant(rnd, "Подрядчик Б")
        mine = stage.kp(asked, D("100"))
        foreign = stage.kp(other, D("100"))
        user = _user(factories)
        _agree(db_session, foreign, D("90"), user)
        _comment(db_session, foreign, "Текст", user)
        _line_rung(db_session, foreign, "f", "all", {"level": "f", "family_id": stage.family.id}, user)
        _family_rung(db_session, other.estimate, stage.family, "f", "all", user)
        _agree(db_session, mine, D("95"), user)

        found = kd.load_decisions(db_session, [asked.estimate.id])

        assert set(found) == {asked.estimate.id}
        only = found[asked.estimate.id]
        assert only.agreements == {mine.id: (D("95"), SET_AT, user.id, user.email)}
        assert (only.comments, dict(only.saved.work), dict(only.saved.family), only.rung_meta) == ({}, {}, {}, {})

    def test_empty_comment_text_is_a_decision(self, stage, db_session, factories):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("100"))
        user = _user(factories)
        _comment(db_session, item, "", user)

        found = kd.load_decisions(db_session, [part.estimate.id])

        assert found[part.estimate.id].comments == {item.id: ("", SET_AT, user.id, user.email)}

    def test_statement_count_does_not_depend_on_estimates_lines_or_decisions(self, stage, db_session, factories):
        rnd = stage.round()
        lone = stage.participant(rnd, "Подрядчик А")
        stage.kp(lone, D("100"))
        small_ids = [lone.estimate.id]
        with count_statements(db_session) as small:
            kd.load_decisions(db_session, small_ids)

        user = _user(factories)
        parts = [lone]
        for index in range(4):
            part = stage.participant(rnd, f"Подрядчик {index}")
            parts.append(part)
            for _ in range(5):
                item = stage.kp(part, D("100"))
                _agree(db_session, item, D("90"), user)
                _comment(db_session, item, "Текст", user)
                _line_rung(db_session, item, "f", "all", {"level": "f", "family_id": 1}, user)
            _family_rung(db_session, part.estimate, stage.family, "f", "all", user)
        large_ids = [part.estimate.id for part in parts]
        with count_statements(db_session) as large:
            kd.load_decisions(db_session, large_ids)

        assert len(small) == len(large) == 4
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in large)


# ---------------------------------------------------------------------------
#  Тексты единиц
# ---------------------------------------------------------------------------


def _raw_data(units_by_lot: dict[str, dict[str, object]]) -> dict:
    """Форма разбора файла: лот → предложение → позиции подрядчика → единица."""
    return {
        "lots": {
            lot_key: {"proposals": {"1": {"contractor_items": {"positions": {k: {"unit": u} for k, u in units.items()}}}}}
            for lot_key, units in units_by_lot.items()
        }
    }


class TestUnitTexts:
    def test_file_text_then_unit_name_then_empty(self, stage, world, db_session, factories):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        items = [stage.kp(part, D("100")) for _ in range(4)]
        for index, item in enumerate(items):
            item.position_key_in_proposal = str(index + 1)
        items[3].unit_id = None
        db_session.flush()
        lot_key = part.proposal.lot.lot_key
        db_session.add(
            EstimateRawData(
                estimate_id=part.estimate.id,
                raw_data=_raw_data({lot_key: {"1": "кв.м", "2": None, "3": "   ", "4": None}}),
                parser_version="t",
            )
        )
        db_session.flush()
        name = db_session.get(UnitOfMeasure, world.unit_id).name
        assert name

        texts = kd.load_unit_texts(db_session, part.estimate.id)

        assert texts == {items[0].id: "кв.м", items[1].id: name, items[2].id: name, items[3].id: ""}

    def test_the_key_is_lot_and_position_not_position_alone(self, stage, world, db_session, factories):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        first = stage.kp(part, D("100"))
        second_lot = factories.LotFactory.create(estimate=part.estimate)
        second_proposal = factories.ProposalFactory.create(lot=second_lot, contractor=part.contractor, vat_rate=D("20"))
        second = world.line(second_proposal, family=stage.family, variant=stage.variant)
        first.position_key_in_proposal = "1"
        second.position_key_in_proposal = "1"
        db_session.flush()
        db_session.add(
            EstimateRawData(
                estimate_id=part.estimate.id,
                raw_data=_raw_data({part.proposal.lot.lot_key: {"1": "кв.м"}, second_lot.lot_key: {"1": "шт"}}),
                parser_version="t",
            )
        )
        db_session.flush()

        texts = kd.load_unit_texts(db_session, part.estimate.id)

        assert texts == {first.id: "кв.м", second.id: "шт"}

    def test_estimate_without_raw_data_falls_back_to_the_unit_name(self, stage, world, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("100"))
        name = db_session.get(UnitOfMeasure, world.unit_id).name

        assert kd.load_unit_texts(db_session, part.estimate.id) == {item.id: name}

    def test_chapters_are_not_lines(self, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("100"))
        chapters = db_session.execute(
            sa.select(PositionItem.id).where(
                PositionItem.proposal_id == part.proposal.id, PositionItem.is_chapter.is_(True)
            )
        ).scalars().all()
        assert chapters

        assert set(kd.load_unit_texts(db_session, part.estimate.id)) == {item.id}


class TestKpSourceLine:
    def test_line_of_the_kp_row(self, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        work = _put(stage, part.proposal, "R", D("144"), qty=D("12.5"), total=D("1800"))
        work.item_number_in_proposal = "2.1"
        db_session.flush()
        system = _put(stage, part.proposal, "R", D("1"), total=D("90000"), kind="SYSTEM")
        rows = {r.line.position_item_id: r for r in load_kp_lines(db_session, [part.estimate.id])[part.estimate.id]}

        source_work = kd.kp_source_line(rows[work.id], "кв.м")
        source_system = kd.kp_source_line(rows[system.id], "")

        assert (source_work.position_item_id, source_work.is_chapter, source_work.is_system) == (work.id, False, False)
        assert (source_work.unit_text, source_work.quantity) == ("кв.м", D("12.5"))
        assert (source_work.unit_price, source_work.total) == (D("144"), D("1800"))
        assert source_work.title == rows[work.id].title
        assert source_work.number == "2.1"
        assert (source_system.is_system, source_system.unit_text) == (True, "")
        assert source_system.total == D("90000")


class TestSourceLineNumber:
    def test_missing_item_number_stays_none_while_the_screen_label_shows_the_key(self, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("100"))
        item.item_number_in_proposal = None
        item.position_key_in_proposal = "7"
        db_session.flush()
        (row,) = (r for r in load_kp_lines(db_session, [part.estimate.id])[part.estimate.id] if r.line.position_item_id == item.id)

        assert row.section_ref == "7"
        assert kd.kp_source_line(row, "").number is None


# ---------------------------------------------------------------------------
#  Сохранённая ступень работы: этап, разбор, панель считают одно
# ---------------------------------------------------------------------------


class TestSavedWorkRung:
    def test_without_a_decision_the_line_is_auto_and_comparable(self, member_client, stage):
        rnd, part, item = _work_scene(stage)

        line = _line_of(_offer_ok(member_client, stage.tender, rnd, part), item)

        assert line["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}
        assert (line["state"], _pair(line["bargain"])) == ("comparable", (D("500"), D("500")))
        assert line["manual_rung"] is None

    def test_a_saved_rung_changes_the_line_the_stage_and_the_cell_together(
        self, member_client, stage, db_session, admin_user
    ):
        rnd, part, item = _work_scene(stage)
        _line_rung(
            db_session, item, "f", "cls", {"level": "f", "family_id": stage.family.id}, admin_user
        )

        offer = _offer_ok(member_client, stage.tender, rnd, part)
        round_ = _round_ok(member_client, stage.tender, rnd)

        line = _line_of(offer, item)
        assert line["rung"] == {"level": "f", "scope": "cls", "auto": False, "threshold_met": True}
        assert line["state"] == "unit_mismatch"
        assert line["bargain"] is None
        assert line["manual_rung"] == {
            "level": "f",
            "scope": "cls",
            "active": True,
            "reset_reason": None,
            "target": "line",
            "set_by": _who(admin_user),
            "set_at": line["manual_rung"]["set_at"],
        }
        assert _iso(line["manual_rung"]["set_at"]) == SET_AT
        totals = _participant(offer, part)["totals"]
        assert totals["unit_mismatch_lines"] == 1
        assert _pair(totals["unit_mismatch_amount"]) == (D("1500"), D("1500"))
        assert _pair(totals["bargain"]) == (D("0"), D("0"))
        assert _participant(round_, part)["totals"] == totals
        assert _offer_article(offer, stage, "R")["unit_mismatch_lines"] == 1
        assert _cell(_article(round_, stage, "R"), part)["unit_mismatch_lines"] == 1
        assert _cell(round_["total"], part)["unit_mismatch_lines"] == 1

    def test_the_panel_reads_the_same_saved_rung(self, member_client, stage, db_session, admin_user):
        rnd, part, item = _work_scene(stage)
        _line_rung(
            db_session, item, "f", "cls", {"level": "f", "family_id": stage.family.id}, admin_user
        )

        line = _panel(member_client, stage.tender, rnd, part, item)["line"]

        assert line["rung"]["level"] == "f"
        assert line["rung"]["auto"] is False
        assert line["state"] == "unit_mismatch"

    def test_a_preview_choice_wins_over_the_saved_rung(self, member_client, stage, db_session, admin_user):
        rnd, part, item = _work_scene(stage)
        _line_rung(
            db_session, item, "f", "cls", {"level": "f", "family_id": stage.family.id}, admin_user
        )

        line = _panel(member_client, stage.tender, rnd, part, item, level="v", scope="all")["line"]

        assert line["rung"] == {"level": "v", "scope": "all", "auto": False, "threshold_met": True}
        assert line["manual_rung"]["active"] is True
        assert line["manual_rung"]["level"] == "f"

    def test_the_audit_comes_from_the_line_record(self, member_client, stage, db_session, factories):
        rnd, part, item = _work_scene(stage)
        author = _user(factories)
        other = _user(factories)
        other_item = stage.kp(part, D("150"))
        _line_rung(db_session, item, "f", "cls", {"level": "f", "family_id": stage.family.id}, author)
        _line_rung(db_session, other_item, "v", "cls", {"level": "v", "variant_id": stage.variant.id}, other)

        body = _offer_ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, item)["manual_rung"]["set_by"] == _who(author)
        assert _line_of(body, other_item)["manual_rung"]["set_by"] == _who(other)


class TestSavedRungThatDoesNotAct:
    def test_changed_key_is_a_200_with_a_reason_and_the_auto_numbers(
        self, member_client, stage, db_session, admin_user
    ):
        rnd, part, item = _work_scene(stage)
        other_variant = _second_variant(stage, "90 мм")
        _line_rung(
            db_session, item, "v", "cls", {"level": "v", "variant_id": other_variant.id}, admin_user
        )

        body = _offer_ok(member_client, stage.tender, rnd, part)

        line = _line_of(body, item)
        assert line["manual_rung"]["active"] is False
        assert line["manual_rung"]["reset_reason"] == "key_changed"
        assert (line["manual_rung"]["level"], line["manual_rung"]["scope"]) == ("v", "cls")
        assert line["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}
        assert (line["state"], _pair(line["bargain"])) == ("comparable", (D("500"), D("500")))

    def test_reading_writes_nothing_and_keeps_the_record(self, member_client, stage, db_session, admin_user):
        rnd, part, item = _work_scene(stage)
        other_variant = _second_variant(stage, "90 мм")
        _line_rung(
            db_session, item, "v", "cls", {"level": "v", "variant_id": other_variant.id}, admin_user
        )
        _agree(db_session, item, D("120"), admin_user)
        _comment(db_session, item, "Текст", admin_user)

        with count_statements(db_session) as seen:
            assert member_client.get(f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}/our-price").status_code == 200
            assert _offer_get(member_client, stage.tender, rnd, part).status_code == 200
            assert _panel_get(member_client, stage.tender, rnd, part, item).status_code == 200

        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []
        kept = db_session.execute(sa.select(KpLineRung.position_item_id, KpLineRung.level)).all()
        assert [tuple(row) for row in kept] == [(item.id, "v")]

    def test_work_record_on_a_system_line_is_kind_changed_and_the_family_rung_acts(
        self, member_client, stage, db_session, factories
    ):
        rnd, part, _second, item, _other = _system_scene(stage)
        line_author, family_author = _user(factories), _user(factories)
        _line_rung(
            db_session, item, "v", "cls", {"level": "v", "variant_id": stage.variant.id}, line_author
        )

        before = _line_of(_offer_ok(member_client, stage.tender, rnd, part), item)

        assert before["manual_rung"] == {
            "level": "v",
            "scope": "cls",
            "active": False,
            "reset_reason": "kind_changed",
            "target": "line",
            "set_by": _who(line_author),
            "set_at": before["manual_rung"]["set_at"],
        }
        assert before["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}

        _family_rung(db_session, part.estimate, stage.family, "f", "cls", family_author)
        after = _line_of(_offer_ok(member_client, stage.tender, rnd, part), item)

        assert after["rung"]["level"] == "f"
        assert after["rung"]["auto"] is False
        assert after["manual_rung"]["reset_reason"] == "kind_changed"
        assert after["manual_rung"]["set_by"] == _who(line_author)


class TestSavedFamilyRung:
    def test_the_rung_of_one_kp_does_not_act_on_the_other_kp_of_the_stage(
        self, member_client, stage, db_session, factories
    ):
        rnd, first, second, first_item, second_item = _system_scene(stage)
        author = _user(factories)
        _family_rung(db_session, first.estimate, stage.family, "f", "cls", author)

        offer_first = _offer_ok(member_client, stage.tender, rnd, first)
        offer_second = _offer_ok(member_client, stage.tender, rnd, second)
        round_ = _round_ok(member_client, stage.tender, rnd)

        acting = _line_of(offer_first, first_item)
        assert acting["rung"] == {"level": "f", "scope": "cls", "auto": False, "threshold_met": True}
        assert acting["state"] == "unit_mismatch"
        assert acting["manual_rung"] == {
            "level": "f",
            "scope": "cls",
            "active": True,
            "reset_reason": None,
            "target": "family",
            "set_by": _who(author),
            "set_at": acting["manual_rung"]["set_at"],
        }
        untouched = _line_of(offer_second, second_item)
        assert untouched["rung"] == {"level": "v", "scope": "cls", "auto": True, "threshold_met": True}
        assert untouched["state"] == "comparable"
        assert untouched["manual_rung"] is None
        assert _participant(round_, first)["totals"]["unit_mismatch_lines"] == 1
        assert _participant(round_, second)["totals"]["unit_mismatch_lines"] == 0
        assert _participant(round_, first)["totals"] == _participant(offer_first, first)["totals"]
        assert _participant(round_, second)["totals"] == _participant(offer_second, second)["totals"]

    def test_the_panel_of_a_system_line_reads_the_family_rung(self, member_client, stage, db_session, admin_user):
        rnd, first, _second, item, _other = _system_scene(stage)
        _family_rung(db_session, first.estimate, stage.family, "f", "cls", admin_user)

        panel = _panel(member_client, stage.tender, rnd, first, item)

        assert panel["line"]["rung"]["level"] == "f"
        assert panel["line"]["manual_rung"]["target"] == "family"
        assert {member["position_item_id"] for member in panel["group_lines"]} == {item.id}
        assert panel["group_lines"][0]["manual_rung"]["target"] == "family"


# ---------------------------------------------------------------------------
#  «Договорились»
# ---------------------------------------------------------------------------


class TestAgreed:
    def test_the_line_carries_net_shown_author_and_time(self, member_client, stage, db_session, admin_user):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        item = stage.kp(part, D("144"))
        stage.kp(part, D("144"))
        _agree(db_session, item, D("125.00000000000000000001"), admin_user)

        body = _offer_ok(member_client, stage.tender, rnd, part)

        agreed = _line_of(body, item)["agreed"]
        assert set(agreed) == {"net", "shown", "set_by", "set_at"}
        assert D(agreed["net"]) == D("125.00000000000000000001")
        assert D(agreed["shown"]) == D("150.000000000000000000012")
        assert agreed["set_by"] == _who(admin_user)
        assert _iso(agreed["set_at"]) == SET_AT
        untouched = [line for line in body["lines"] if line["position_item_id"] != item.id]
        assert [line["agreed"] for line in untouched] == [None]

    def test_unknown_base_gives_no_shown_and_keeps_the_net(self, member_client, stage, db_session, admin_user):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        item = stage.kp(part, D("144"))
        _agree(db_session, item, D("125"), admin_user)

        body = _offer_ok(member_client, stage.tender, rnd, part)

        agreed = _line_of(body, item)["agreed"]
        assert (D(agreed["net"]), agreed["shown"]) == (D("125"), None)

    def test_the_agreement_changes_no_total(self, member_client, stage, db_session, admin_user):
        rnd, part, item = _work_scene(stage)
        without = _participant(_offer_ok(member_client, stage.tender, rnd, part), part)["totals"]
        _agree(db_session, item, D("120"), admin_user)

        offer = _offer_ok(member_client, stage.tender, rnd, part)
        round_ = _round_ok(member_client, stage.tender, rnd)

        line = _line_of(offer, item)
        assert line["agreed"] is not None
        assert _pair(line["bargain"]) == (D("500"), D("500"))
        assert _pair(line["to_ours"]) == (D("500"), D("500"))
        assert _participant(offer, part)["totals"] == without
        assert _participant(round_, part)["totals"] == without


# ---------------------------------------------------------------------------
#  Комментарий: вид в строке, текст в панели
# ---------------------------------------------------------------------------

CUSTOM = "Свой текст"


def _comment_scene(stage, db_session, admin_user):
    """История класса — 100 и 140 на одном варианте (расценка 100); КП без НДС.
    Восемь строк по случаям комментария."""
    cheap, _estimate, _proposal = stage.history(D("100"))
    stage.history(D("140"))
    rnd = stage.round()
    part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
    items = {
        "over": stage.kp(part, D("150")),
        "over_custom": stage.kp(part, D("150")),
        "over_empty": stage.kp(part, D("150")),
        "equal": stage.kp(part, D("100")),
        "equal_custom": stage.kp(part, D("100")),
        "below": stage.kp(part, D("90")),
        "agreed_above_kp": stage.kp(part, D("150")),
        "agreed_below_kp": stage.kp(part, D("150")),
    }
    _comment(db_session, items["over_custom"], CUSTOM, admin_user)
    _comment(db_session, items["over_empty"], "", admin_user)
    _comment(db_session, items["equal_custom"], CUSTOM, admin_user)
    _agree(db_session, items["agreed_above_kp"], D("160"), admin_user)
    _agree(db_session, items["agreed_below_kp"], D("140"), admin_user)
    return rnd, part, items, cheap


class TestCommentKind:
    def test_every_case_of_the_rows(self, member_client, stage, db_session, admin_user):
        rnd, part, items, _cheap = _comment_scene(stage, db_session, admin_user)

        response = _offer_get(member_client, stage.tender, rnd, part)
        body = _offer_json(response)

        kinds = {name: _line_of(body, item)["comment_kind"] for name, item in items.items()}
        assert kinds == {
            "over": "template",
            "over_custom": "custom",
            "over_empty": "custom",
            "equal": None,
            "equal_custom": "custom",
            "below": "template",
            "agreed_above_kp": None,
            "agreed_below_kp": "template",
        }
        assert CUSTOM not in response.text
        assert all("comment" not in line for line in body["lines"])

    def test_unknown_base_has_no_template_but_keeps_the_custom_mark(
        self, member_client, stage, db_session, admin_user
    ):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        plain = stage.kp(part, D("144"))
        custom = stage.kp(part, D("144"))
        _comment(db_session, custom, CUSTOM, admin_user)

        body = _offer_ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, plain)["comment_kind"] is None
        assert _line_of(body, custom)["comment_kind"] == "custom"

    def test_the_panel_of_a_template_line(self, member_client, stage, db_session, admin_user):
        rnd, part, items, cheap = _comment_scene(stage, db_session, admin_user)
        item = items["over"]
        db_session.add(
            EstimateRawData(
                estimate_id=part.estimate.id,
                raw_data=_raw_data({part.proposal.lot.lot_key: {item.position_key_in_proposal: "кв.м"}}),
                parser_version="t",
            )
        )
        db_session.flush()
        history_object = cheap.object.title
        expected = (
            f"Ваша цена — 150,00 ₽ за кв.м без НДС. В нашем договоре по объекту {history_object}, 2025 г., "
            "эта работа стоила 100,00 ₽ за кв.м без НДС. Просим снизить до 100,00 ₽ или обосновать разницу."
        )

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert set(panel) == PANEL_KEYS
        assert panel["comment"] == {"kind": "template", "text": expected, "template_text": expected}
        assert panel["line"]["comment_kind"] == "template"

    def test_the_panel_of_a_custom_line_keeps_the_template_for_the_button(
        self, member_client, stage, db_session, admin_user
    ):
        rnd, part, items, _cheap = _comment_scene(stage, db_session, admin_user)

        custom = _panel(member_client, stage.tender, rnd, part, items["over_custom"])["comment"]
        template = _panel(member_client, stage.tender, rnd, part, items["over"])["comment"]

        assert custom["kind"] == "custom"
        assert custom["text"] == CUSTOM
        assert custom["template_text"] == template["template_text"]
        assert custom["template_text"] is not None

    def test_empty_custom_text_is_a_custom_comment_not_an_absence(
        self, member_client, stage, db_session, admin_user
    ):
        rnd, part, items, _cheap = _comment_scene(stage, db_session, admin_user)

        comment = _panel(member_client, stage.tender, rnd, part, items["over_empty"])["comment"]

        assert comment["kind"] == "custom"
        assert comment["text"] == ""
        assert comment["template_text"] is not None

    def test_the_panel_of_a_line_without_a_case(self, member_client, stage, db_session, admin_user):
        rnd, part, items, _cheap = _comment_scene(stage, db_session, admin_user)

        plain = _panel(member_client, stage.tender, rnd, part, items["equal"])["comment"]
        custom = _panel(member_client, stage.tender, rnd, part, items["equal_custom"])["comment"]

        assert plain == {"kind": None, "text": None, "template_text": None}
        assert custom == {"kind": "custom", "text": CUSTOM, "template_text": None}

    def test_the_agreed_case_reads_the_agreement(self, member_client, stage, db_session, admin_user):
        rnd, part, items, _cheap = _comment_scene(stage, db_session, admin_user)

        agreed = _panel(member_client, stage.tender, rnd, part, items["agreed_below_kp"])["comment"]
        calm = _panel(member_client, stage.tender, rnd, part, items["agreed_above_kp"])["comment"]

        assert agreed["kind"] == "template"
        assert agreed["template_text"].startswith("Согласовано: 140,00 ₽ за ")
        assert calm == {"kind": None, "text": None, "template_text": None}

    def test_panel_group_lines_carry_the_decision_fields_too(self, member_client, stage, db_session, admin_user):
        rnd, first, _second, item, _other = _system_scene(stage)
        _agree(db_session, item, D("80"), admin_user)

        panel = _panel(member_client, stage.tender, rnd, first, item)

        assert set(panel["group_lines"][0]) == LINE_KEYS
        assert panel["group_lines"][0]["agreed"]["net"] == "80"


class TestTemplateInputsOfTheScene:
    """Ревью: что сцена передаёт во вход шаблона — подрядчика участника, ряд ИПЦ с
    целевым месяцем и площадь тендера. Каждый тест меняет ровно один вход."""

    def test_a_contract_with_this_contractor_is_named_as_such(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.history(D("100"), contractor=part.contractor)
        stage.history(D("140"))
        item = stage.kp(part, D("150"))

        comment = _panel(member_client, stage.tender, rnd, part, item)["comment"]

        assert comment["kind"] == "template"
        assert "В нашем договоре с вами по объекту " in comment["template_text"]

    def test_the_series_and_its_target_month_reach_the_text(self, member_client, stage, db_session):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
        goal = str((D("100") * FACTOR_FEB_2025).quantize(D("0.01"), rounding=ROUND_HALF_UP)).replace(".", ",")

        with_series = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)["comment"]
        nominal = _panel(member_client, stage.tender, rnd, part, item)["comment"]

        assert with_series["kind"] == nominal["kind"] == "template"
        assert f"; с учётом инфляции ({SERIES_NAME}, к 08.2026) — {goal} ₽. Просим снизить до {goal} ₽" in (
            with_series["template_text"]
        )
        assert "инфляци" not in nominal["template_text"]

    def test_a_system_above_ours_needs_the_tender_area(self, member_client, stage):
        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _put(stage, part.proposal, "R", D("1"), total=D(150000), kind="SYSTEM")

        line = _line_of(_offer_ok(member_client, stage.tender, rnd, part), item)
        comment = _panel(member_client, stage.tender, rnd, part, item)["comment"]

        assert line["comment_kind"] == comment["kind"] == "template"
        assert "для вашего объекта это 100 000,00 ₽ за весь объём" in comment["template_text"]


# ---------------------------------------------------------------------------
#  Какие ступени можно сохранить
# ---------------------------------------------------------------------------


class TestSelectable:
    def test_work_rung_is_selectable_by_its_own_contracts(self, member_client, stage, factories):
        other_class = factories.RateClassFactory.create(title="Класс Д")
        _history(stage, D("100"), rate_class=other_class)
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("150"))

        line = _line_of(_offer_ok(member_client, stage.tender, rnd, part), item)

        assert [(r["level"], r["scope"], r["contracts"], r["selectable"]) for r in line["rungs"]] == [
            ("v", "cls", 0, False),
            ("v", "all", 1, True),
            ("f", "cls", 0, False),
            ("f", "all", 1, True),
        ]

    def test_system_rung_is_selectable_by_every_group_of_the_family(self, member_client, stage):
        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        covered = _put(stage, part.proposal, "R", D("1"), total=D(30000), kind="SYSTEM")
        bare = _put(stage, part.proposal, "R", D("1"), total=D(30000), kind="SYSTEM", variant=_second_variant(stage))

        body = _offer_ok(member_client, stage.tender, rnd, part)

        def rungs(item):
            return [(r["level"], r["scope"], r["contracts"], r["selectable"]) for r in _line_of(body, item)["rungs"]]

        assert rungs(covered) == [("v", "cls", 2, False), ("v", "all", 2, False), ("f", "cls", 2, True), ("f", "all", 2, True)]
        assert rungs(bare) == [("v", "cls", 0, False), ("v", "all", 0, False), ("f", "cls", 2, True), ("f", "all", 2, True)]


# ---------------------------------------------------------------------------
#  Скорость и форма
# ---------------------------------------------------------------------------


class TestShape:
    def test_row_keys_grow_by_exactly_the_three_decision_fields(self, member_client, stage):
        rnd, part, _item = _work_scene(stage)

        body = _offer_ok(member_client, stage.tender, rnd, part)

        assert all(set(line) == LINE_KEYS for line in body["lines"])


class TestQueryCount:
    def test_statements_do_not_grow_with_lines_and_decisions(self, member_client, stage, db_session, admin_user):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        second = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        item = stage.kp(part, D("150"))
        stage.kp(second, D("150"))

        def decide(line):
            _agree(db_session, line, D("120"), admin_user)
            _comment(db_session, line, "Текст", admin_user)
            _line_rung(db_session, line, "f", "all", {"level": "f", "family_id": stage.family.id}, admin_user)

        decide(item)
        base = f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}/our-price"
        offer_url = f"{base}/offers/{part.offer.id}"
        panel_url = f"{offer_url}/lines/{item.id}"
        with count_statements(db_session) as small_round:
            assert member_client.get(base).status_code == 200
        with count_statements(db_session) as small_offer:
            assert member_client.get(offer_url).status_code == 200
        with count_statements(db_session) as small_panel:
            assert member_client.get(panel_url).status_code == 200

        third = stage.participant(rnd, "Подрядчик В", vat=D("0"))
        fourth = stage.participant(rnd, "Подрядчик Г", vat=D("0"))
        for _ in range(9):
            decide(stage.kp(part, D("150")))
            decide(stage.kp(second, D("150")))
            decide(stage.kp(third, D("150")))
            decide(stage.kp(fourth, D("150")))
        with count_statements(db_session) as large_round:
            assert member_client.get(base).status_code == 200
        with count_statements(db_session) as large_offer:
            body = _offer_json(member_client.get(offer_url))
        with count_statements(db_session) as large_panel:
            assert member_client.get(panel_url).status_code == 200

        assert body["progress"]["lines_total"] == 10
        assert len(small_round) == len(large_round)
        assert len(small_offer) == len(large_offer)
        assert len(small_panel) == len(large_panel)

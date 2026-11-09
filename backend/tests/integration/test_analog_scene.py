"""Загрузка и ответы чтения «Против нашей цены» с лестницей аналогов работ
(спека лестницы аналогов §2.3, §2.9, §2.10, §2.12, §2.16, §2.17, §2.24).

Сцены строятся помощниками экрана 4а: по умолчанию строки лежат в общем разделе с зоной «вся».
Здесь цепочки заводятся явно: зона, названия, состояние задания `section_zone`. Названия и числа
выдуманы. Каждое правило имеет обе стороны: вход, где оно срабатывает, и соседний, нарушающий
ровно одно условие.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.our_price_load import load_history, load_kp_lines, load_work_history
from models import ContextParameterValue, FamilyParameter, SemanticJob, WorkVariantValue
from services.analog_ladder import FamilyLadderInfo
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    TOLERANCE,
    _inflation_scene,
    _line_of,
    _ok,
    _other_family,
    _pair,
    _panel,
    _panel_get,
    _second_variant,
)
from tests.integration.test_our_price_round_api import Stage, count_statements
from tests.integration.test_our_price_round_api import _get as _round_get
from tests.integration.test_our_price_signal import _pause

pytestmark = pytest.mark.integration

D = Decimal
_OWN = object()


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники сцены
# ---------------------------------------------------------------------------


def _line(stage, proposal, unit_cost, *, chain, qty=D("10"), variant=_OWN, family=None, kind=None, archived=False):
    """Строка в главе статьи R; `variant=None` — без варианта; `chain=None` — без цепочки."""
    variant = stage.variant if variant is _OWN else variant
    item = stage.world.line(
        proposal,
        chapter=stage.chapter(proposal, "R"),
        unit_cost=unit_cost,
        total=unit_cost * qty,
        suggested=qty,
        quantity=qty,
        family=(family or stage.family) if variant is not None else None,
        variant=variant,
        kind=kind,
        archived_context=archived,
        chain=chain,
    )
    stage.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == item.context_id))
    return item


def _hist(
    stage,
    unit_cost,
    *,
    chain,
    prepared=dt.date(2025, 4, 1),
    amendment_no=None,
    vat=D("0"),
    rate_class="own",
    contractor=None,
    object_=None,
    **kwargs,
):
    """Договор истории с одной строкой работы; `prepared` — дата подготовки его сметы."""
    contract = stage.world.contract(
        area=D("1000"),
        rate_class=stage.rate_class if rate_class == "own" else rate_class,
        contractor=contractor,
        object_=object_,
    )
    estimate, proposal = stage.world.estimate(contract, amendment_no=amendment_no, vat_rate=vat)
    estimate.data_prepared_on_date = prepared
    item = _line(stage, proposal, unit_cost, chain=chain, **kwargs)
    stage.db.flush()
    return contract, estimate, item


def _kp_part(stage, *, vat=D("0"), held_on=dt.date(2026, 8, 10)):
    rnd = stage.round(held_on=held_on)
    return rnd, stage.participant(rnd, "Подрядчик А", vat=vat)


def _job(db, kind, status, **subject):
    """Задание очереди с предметом `subject`; парные поля статуса заполнены под CHECK-и."""
    job = SemanticJob(
        kind=kind,
        request_hash=uuid.uuid4().hex,
        status=status,
        claim_token=uuid.uuid4() if status == "running" else None,
        cancel_reason="input_changed" if status == "cancelled" else None,
        privacy_matches=(
            [{"text": "иное имя", "kind": "contractor", "where": "context"}] if status == "privacy_hold" else None
        ),
        prompt_version="1",
        model_requested="m",
        place_dictionary_version=1,
        candidates_hash="c",
        prefix_hash="p",
        input_hash="i",
        response_schema_version="1",
        serialization_version="1",
        **subject,
    )
    db.add(job)
    db.flush()
    return job


def _zone_job(db, chain, status):
    return _job(db, "section_zone", status, section_chain_id=chain.id)


def _releasable_job(db, family_id, schema_id, status):
    return _job(db, "releasable_params", status, family_id=family_id, schema_id=schema_id)


def _writes(seen) -> list[str]:
    return [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


# ---------------------------------------------------------------------------
#  Загрузчик истории по работам
# ---------------------------------------------------------------------------


def _estimate_of(db, stage) -> dict[int, int]:
    contracts, _ = load_history(db, exclude_object_id=stage.tender.object_id)
    return {contract.contract_id: contract.estimate_id for contract in contracts}


def _load_work(db, stage):
    return load_work_history(db, estimate_of=_estimate_of(db, stage))


def _history_ids(db, stage) -> set[int]:
    lines, _ = _load_work(db, stage)
    return {line.position_item_id for line in lines}


class TestWorkHistoryLoader:
    def test_a_line_carries_the_ladder_fields(self, stage, world, db_session):
        chain = world.chain(("Подземная часть", "  ПАРКИНГ "), "underground")
        _, _, item = _hist(stage, D("100"), chain=chain)

        lines, _ = _load_work(db_session, stage)

        (line,) = lines
        param = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id)
        ).scalar_one()
        value_id = db_session.execute(
            sa.select(WorkVariantValue.value_id).where(
                WorkVariantValue.variant_id == stage.variant.id, WorkVariantValue.parameter_id == param.id
            )
        ).scalar_one()
        assert line.position_item_id == item.id
        assert (line.family_id, line.variant_id, line.schema_id) == (stage.family.id, stage.variant.id, stage.schema.id)
        assert (line.chain_id, line.zone) == (chain.id, "underground")
        assert line.chain_titles == frozenset({"подземная часть", "паркинг"})
        assert dict(line.main_values) == {param.id: value_id}
        assert (line.unit_price, line.weight, line.vat_base) == (D("100"), D("10"), D("0"))

    @pytest.mark.parametrize(
        "why",
        [
            "tender_object",
            "kp_estimate",
            "system",
            "no_chain",
            "undetermined_zone",
            "archived_context",
            "no_variant",
            "zero_price",
            "zero_weight",
        ],
    )
    def test_a_line_that_is_not_an_analog_source_stays_out(self, stage, world, db_session, why):
        chain = world.chain(("Раздел А",), "whole")
        _, _, valid = _hist(stage, D("100"), chain=chain)
        if why == "tender_object":
            offending = _hist(stage, D("100"), chain=chain, object_=stage.tender.object)[2]
        elif why == "kp_estimate":
            offending = _line(stage, stage.participant(stage.round(), "Подрядчик Н").proposal, D("100"), chain=chain)
        elif why == "system":
            offending = _hist(stage, D("100"), chain=chain, kind="SYSTEM")[2]
        elif why == "no_chain":
            offending = _hist(stage, D("100"), chain=None)[2]
        elif why == "undetermined_zone":
            offending = _hist(stage, D("100"), chain=world.chain(("Раздел Б",), None))[2]
        elif why == "archived_context":
            offending = _hist(stage, D("100"), chain=chain, archived=True)[2]
        elif why == "no_variant":
            offending = _hist(stage, D("100"), chain=chain, variant=None)[2]
        elif why == "zero_price":
            offending = _hist(stage, D("0"), chain=chain)[2]
        else:
            offending = _hist(stage, D("100"), chain=chain, qty=D("0"))[2]
        db_session.flush()

        ids = _history_ids(db_session, stage)

        assert valid.id in ids
        assert offending.id not in ids

    def test_only_the_latest_estimate_of_a_contract_is_history(self, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        contract = world.contract()
        base, base_proposal = world.estimate(contract)
        old = _line(stage, base_proposal, D("100"), chain=chain)
        amendment, amendment_proposal = world.estimate(contract, amendment_no=1)
        new = _line(stage, amendment_proposal, D("110"), chain=chain)
        db_session.flush()

        assert _history_ids(db_session, stage) == {new.id}
        assert old.id not in _history_ids(db_session, stage)

    def test_a_variant_on_a_superseded_schema_has_no_main_values(self, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _, _, item = _hist(stage, D("100"), chain=chain)
        old_schema = stage.schema
        new_schema = world.supersede(stage.family, old_schema)

        lines, families = _load_work(db_session, stage)

        (line,) = lines
        assert line.position_item_id == item.id
        assert (line.schema_id, dict(line.main_values)) == (old_schema.id, {})
        assert families[stage.family.id].schema_id == new_schema.id

    def test_family_info_lists_active_main_parameters_in_order_and_the_released_ones(self, stage, world, db_session):
        thickness = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id)
        ).scalar_one()
        mark = world.param(stage.schema, 2, "Марка")
        mark.releasable = True
        grade = world.param(stage.schema, 3, "Класс")
        grade.releasable = False
        world.param(stage.schema, 4, "Цвет", role="secondary")
        world.param(stage.schema, 5, "Старый", archived=True)
        db_session.flush()

        _, families = _load_work(db_session, stage)

        assert families[stage.family.id] == FamilyLadderInfo(
            schema_id=stage.schema.id,
            main_parameter_ids=(thickness.id, mark.id, grade.id),
            releasable_ids=frozenset({mark.id}),
            names={thickness.id: "Толщина", mark.id: "Марка", grade.id: "Класс"},
        )

    def test_the_statement_count_does_not_grow_with_the_lines_and_the_writes_are_none(self, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain)
        with count_statements(db_session) as small:
            _load_work(db_session, stage)
        v2 = _second_variant(stage)
        for index in range(6):
            _hist(stage, D("100") + index, chain=world.chain((f"Раздел {index}",), "whole"), variant=v2)
        with count_statements(db_session) as large:
            lines, _ = _load_work(db_session, stage)

        assert len(lines) == 7
        assert len(small) == len(large)
        assert _writes(large) == []


# ---------------------------------------------------------------------------
#  Загрузчик строк КП: цепочка, зона, сигнал каталога
# ---------------------------------------------------------------------------


def _kp_row(db, part, item):
    rows = load_kp_lines(db, [part.estimate.id])[part.estimate.id]
    (row,) = (r for r in rows if r.line.position_item_id == item.id)
    return row.line


class TestKpLineLoader:
    def test_the_line_carries_the_chain_the_zone_and_the_variant_values(self, stage, world, db_session):
        chain = world.chain(("Кровля", "Рулонная"), "aboveground")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)
        param = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id)
        ).scalar_one()

        line = _kp_row(db_session, part, item)

        assert (line.section_chain_id, line.zone) == (chain.id, "aboveground")
        assert line.chain_titles == frozenset({"кровля", "рулонная"})
        assert line.schema_id == stage.schema.id
        assert set(line.main_values) == {param.id}
        assert line.zone_waiting is False

    def test_a_line_without_a_chain_has_no_zone(self, stage, db_session):
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=None)

        line = _kp_row(db_session, part, item)

        assert (line.section_chain_id, line.zone, line.chain_titles, line.zone_waiting) == (
            None,
            None,
            frozenset(),
            False,
        )

    @pytest.mark.parametrize(
        ("status", "in_progress", "waiting"),
        [
            pytest.param("pending", True, False, id="pending"),
            pytest.param("running", True, False, id="running"),
            pytest.param("error", False, True, id="error"),
            pytest.param("privacy_hold", False, True, id="privacy_hold"),
            pytest.param("done", False, False, id="done-without-a-proposal"),
        ],
    )
    def test_zone_job_status_gives_in_progress_or_waiting(self, stage, world, db_session, status, in_progress, waiting):
        chain = world.chain(("Раздел Б",), None)
        _zone_job(db_session, chain, status)
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)

        line = _kp_row(db_session, part, item)

        assert (line.in_progress, line.zone_waiting) == (in_progress, waiting)

    def test_a_disputed_zone_waits_for_the_administrator_without_a_job(self, stage, world, db_session):
        chain = world.chain(("Раздел Б",), None, proposed="territory")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)

        line = _kp_row(db_session, part, item)

        assert (line.in_progress, line.zone_waiting) == (False, True)

    def test_an_undetermined_zone_without_a_job_and_a_proposal_waits_for_nobody(self, stage, world, db_session):
        chain = world.chain(("Раздел Б",), None)
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)

        line = _kp_row(db_session, part, item)

        assert (line.in_progress, line.zone_waiting) == (False, False)

    def test_a_live_job_of_a_chain_with_a_zone_is_not_in_progress(self, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _zone_job(db_session, chain, "pending")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)

        assert _kp_row(db_session, part, item).in_progress is False

    def test_a_paused_claim_turns_the_live_zone_job_into_waiting(self, stage, world, db_session):
        chain = world.chain(("Раздел Б",), None)
        _zone_job(db_session, chain, "pending")
        _pause(db_session)
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)

        line = _kp_row(db_session, part, item)

        assert (line.in_progress, line.zone_waiting) == (False, True)

    def test_the_zone_signal_does_not_reach_a_system_or_a_line_without_a_variant(self, stage, world, db_session):
        chain = world.chain(("Раздел Б",), None)
        _zone_job(db_session, chain, "pending")
        _, part = _kp_part(stage)
        work = _line(stage, part.proposal, D("100"), chain=chain)
        system = _line(stage, part.proposal, D("100"), chain=chain, kind="SYSTEM")
        bare = _line(stage, part.proposal, D("100"), chain=chain, variant=None)

        assert _kp_row(db_session, part, work).in_progress is True
        assert _kp_row(db_session, part, system).in_progress is False
        assert _kp_row(db_session, part, bare).in_progress is False

    @pytest.mark.parametrize(
        ("status", "on_current", "expected"),
        [
            pytest.param("pending", True, True, id="pending-current"),
            pytest.param("running", True, True, id="running-current"),
            pytest.param("pending", False, False, id="pending-superseded"),
            pytest.param("error", True, False, id="error-current"),
            pytest.param("privacy_hold", True, False, id="privacy_hold-current"),
        ],
    )
    def test_releasable_job_makes_the_line_in_progress_only_when_live_on_the_current_schema(
        self, stage, world, db_session, status, on_current, expected
    ):
        chain = world.chain(("Раздел А",), "whole")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)
        old_schema = stage.schema
        schema = world.supersede(stage.family, old_schema)
        _releasable_job(db_session, stage.family.id, (schema if on_current else old_schema).id, status)

        assert _kp_row(db_session, part, item).in_progress is expected

    def test_a_releasable_job_of_another_family_does_not_touch_the_line(self, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("100"), chain=chain)
        family2, variant2 = _other_family(stage)
        _releasable_job(db_session, family2.id, variant2.schema_id, "pending")

        assert _kp_row(db_session, part, item).in_progress is False

    def test_the_statement_count_does_not_grow_with_the_lines_and_the_chains(self, stage, world, db_session):
        _, part = _kp_part(stage)
        _line(stage, part.proposal, D("100"), chain=world.chain(("Раздел А",), "whole"))
        with count_statements(db_session) as small:
            load_kp_lines(db_session, [part.estimate.id])
        for index in range(6):
            chain = world.chain((f"Раздел {index}",), None)
            _zone_job(db_session, chain, "pending")
            _line(stage, part.proposal, D("100"), chain=chain)
        with count_statements(db_session) as large:
            rows = load_kp_lines(db_session, [part.estimate.id])[part.estimate.id]

        assert len(rows) == 7
        assert len(small) == len(large)
        assert _writes(large) == []


# ---------------------------------------------------------------------------
#  Состояния строки в ответе
# ---------------------------------------------------------------------------


def _scene_with_chain(stage, chain, *, price=D("100")):
    rnd, part = _kp_part(stage)
    return rnd, part, _line(stage, part.proposal, price, chain=chain)


class TestStates:
    def test_a_work_without_a_zone_is_no_zone_with_the_empty_comparison(self, member_client, stage, world):
        chain = world.chain(("Раздел Б",), None)
        _hist(stage, D("100"), chain=world.chain(("Раздел А",), "whole"))
        rnd, part, item = _scene_with_chain(stage, chain)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "no_zone"
        assert (line["zone"], line["section_chain_id"]) == (None, chain.id)
        assert (line["work_rung"], line["no_history_reason"], line["analogs_count"]) == (None, None, 0)
        for key in ("lowest", "freshest", "range", "position", "to_lowest", "to_freshest", "over_lowest", "below"):
            assert line[key] is None, key
        assert (line["target_kind"], line["choice"]) == (None, None)

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            pytest.param("disputed", True, id="disputed"),
            pytest.param("error", True, id="error"),
            pytest.param("privacy_hold", True, id="privacy_hold"),
            pytest.param("undetermined", False, id="undetermined-without-a-job"),
        ],
    )
    def test_no_zone_waits_for_the_administrator_by_the_state_of_the_chain(
        self, member_client, stage, world, db_session, kind, expected
    ):
        chain = world.chain(("Раздел Б",), None, proposed="territory" if kind == "disputed" else None)
        if kind in ("error", "privacy_hold"):
            _zone_job(db_session, chain, kind)
        rnd, part, item = _scene_with_chain(stage, chain)

        body = _ok(member_client, stage.tender, rnd, part)
        line = _line_of(body, item)

        assert line["state"] == "no_zone"
        assert line["waiting_admin"] is expected
        assert line["suggestion_queue"] is None

    @pytest.mark.parametrize(
        ("kind", "waits"),
        [
            pytest.param("disputed", True, id="disputed"),
            pytest.param("error", True, id="error"),
            pytest.param("live", False, id="live-job"),
            pytest.param("undetermined", False, id="undetermined"),
        ],
    )
    def test_the_progress_counts_waiting_admin_by_the_rule_of_the_line_flag(
        self, member_client, stage, world, db_session, kind, waits
    ):
        chain = world.chain(("Раздел Б",), None, proposed="territory" if kind == "disputed" else None)
        if kind in ("error", "live"):
            _zone_job(db_session, chain, "error" if kind == "error" else "pending")
        rnd, part, item = _scene_with_chain(stage, chain)

        offer = _ok(member_client, stage.tender, rnd, part)
        round_body = _round_get(member_client, stage.tender, rnd).json()

        line = _line_of(offer, item)
        assert line["waiting_admin"] is waits
        assert offer["progress"]["waiting_admin"] == int(waits)
        assert round_body["progress"]["waiting_admin"] == int(waits)

    def test_a_live_zone_job_makes_the_line_in_progress_and_counts_in_the_progress(
        self, member_client, stage, world, db_session
    ):
        chain = world.chain(("Раздел Б",), None)
        _zone_job(db_session, chain, "pending")
        rnd, part, item = _scene_with_chain(stage, chain)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, item)["state"] == "in_progress"
        assert body["progress"] == {"lines_total": 1, "in_progress": 1, "waiting_admin": 0}

    def test_a_live_releasable_job_makes_the_line_in_progress(self, member_client, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain)
        rnd, part, item = _scene_with_chain(stage, chain)
        assert _line_of(_ok(member_client, stage.tender, rnd, part), item)["state"] == "comparable"
        _releasable_job(db_session, stage.family.id, stage.schema.id, "running")

        assert _line_of(_ok(member_client, stage.tender, rnd, part), item)["state"] == "in_progress"

    def test_the_states_above_no_zone_win_over_it(self, member_client, stage, world):
        chain = world.chain(("Раздел Б",), None)
        rnd, part = _kp_part(stage)
        no_price = _line(stage, part.proposal, D("0"), chain=chain)
        no_variant = _line(stage, part.proposal, D("100"), chain=chain, variant=None)
        no_zone = _line(stage, part.proposal, D("100"), chain=chain)
        blank = stage.participant(rnd, "Подрядчик Б", vat=None)
        unknown_base = _line(stage, blank.proposal, D("100"), chain=chain)

        body = _ok(member_client, stage.tender, rnd, part)
        blank_body = _ok(member_client, stage.tender, rnd, blank)

        assert _line_of(body, no_price)["state"] == "no_price"
        assert _line_of(body, no_variant)["state"] == "no_variant"
        assert _line_of(body, no_zone)["state"] == "no_zone"
        assert _line_of(blank_body, unknown_base)["state"] == "unknown_vat_base"

    def test_a_system_does_not_need_a_zone(self, member_client, stage, world):
        no_zone = world.chain(("Раздел Б",), None)
        rnd, part = _kp_part(stage)
        system = _line(stage, part.proposal, D("1"), chain=no_zone, kind="SYSTEM")

        line = _line_of(_ok(member_client, stage.tender, rnd, part), system)

        assert line["state"] == "no_history"  # а не no_zone
        assert (line["zone"], line["section_chain_id"], line["work_rung"]) == (None, None, None)


# ---------------------------------------------------------------------------
#  Аналоги по зоне и ступеням
# ---------------------------------------------------------------------------


class TestAnalogs:
    def test_only_the_analogs_of_the_zone_of_the_line_count(self, member_client, stage, world):
        underground = world.chain(("Подземная часть",), "underground")
        above = world.chain(("Кровля",), "aboveground")
        _hist(stage, D("100"), chain=underground)
        _hist(stage, D("50"), chain=above)
        rnd, part, item = _scene_with_chain(stage, underground, price=D("110"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["zone"] == "underground"
        assert panel["line"]["analogs_count"] == 1
        assert _pair(panel["line"]["lowest"]["value"]) == (D("100"), D("100"))
        assert len(panel["analogs"]) == 1

    @pytest.mark.parametrize(
        ("history", "reason"),
        [
            pytest.param("other_family", "no_family", id="no_family"),
            pytest.param("other_variant", "no_params", id="no_params"),
            pytest.param("other_zone", "no_zone_match", id="no_zone_match"),
        ],
    )
    def test_no_history_gives_the_first_reason_by_the_candidates(self, member_client, stage, world, history, reason):
        chain = world.chain(("Подземная часть",), "underground")
        other = world.chain(("Кровля",), "aboveground")
        if history == "other_family":
            family2, variant2 = _other_family(stage)
            _hist(stage, D("100"), chain=chain, variant=variant2, family=family2)
        elif history == "other_variant":
            _hist(stage, D("100"), chain=chain, variant=_second_variant(stage))
        else:
            _hist(stage, D("100"), chain=other)
        rnd, part, item = _scene_with_chain(stage, chain)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "no_history"
        assert line["no_history_reason"] == reason
        assert (line["work_rung"], line["analogs_count"]) == (None, 0)

    def test_path_conflict_of_the_main_parameter_is_not_a_match(self, member_client, stage, world, db_session):
        chain = world.chain(("Раздел А",), "whole")
        _, _, hist_item = _hist(stage, D("100"), chain=chain)
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain)
        param = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id)
        ).scalar_one()

        control = _line_of(_ok(member_client, stage.tender, rnd, part), item)
        assert (control["state"], control["work_rung"]["no"]) == ("comparable", 1)

        for line_item in (hist_item, item):
            db_session.execute(
                sa.delete(ContextParameterValue).where(ContextParameterValue.context_id == line_item.context_id)
            )
            db_session.add(
                ContextParameterValue(
                    context_id=line_item.context_id,
                    schema_id=stage.schema.id,
                    parameter_id=param.id,
                    value_id=None,
                    source="path_conflict",
                )
            )
        db_session.flush()

        lines, _ = _load_work(db_session, stage)
        assert [line.conflicted for line in lines] == [frozenset({param.id})]
        kp_row = _kp_row(db_session, part, item)
        assert kp_row.conflicted == frozenset({param.id})
        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)
        assert (line["state"], line["no_history_reason"]) == ("no_history", "path_conflict")

    def _released_scene(self, stage, world, db_session, *, own_class, factories=None):
        mark = world.param(stage.schema, 2, "Марка")
        mark.releasable = True
        db_session.flush()
        kp_variant = world.variant(
            stage.family, stage.schema, {1: _value_of(stage, 1, "50 мм"), 2: world.value(mark, "А")}
        )
        history_variant = world.variant(
            stage.family, stage.schema, {1: _value_of(stage, 1, "50 мм"), 2: world.value(mark, "Б")}
        )
        chain = world.chain(("Раздел А",), "whole")
        if own_class:
            _hist(stage, D("100"), chain=chain, variant=history_variant)
        else:
            other_class = factories.RateClassFactory.create(title="Класс Ч")
            _hist(stage, D("100"), chain=chain, variant=history_variant, rate_class=other_class)
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain, variant=kp_variant)
        return rnd, part, item

    def test_rung_two_names_the_released_parameter_and_stays_in_the_class(
        self, member_client, stage, world, db_session
    ):
        rnd, part, item = self._released_scene(stage, world, db_session, own_class=True)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "comparable"
        assert line["work_rung"] == {"no": 2, "released": ["Марка"], "other_class": False}

    def test_rung_four_is_the_other_class_with_the_released_parameter(
        self, member_client, stage, world, db_session, factories
    ):
        rnd, part, item = self._released_scene(stage, world, db_session, own_class=False, factories=factories)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["work_rung"] == {"no": 4, "released": ["Марка"], "other_class": True}
        assert line["lowest"]["from_all_classes"] is True

    def test_without_a_released_parameter_the_other_variant_is_not_an_analog(
        self, member_client, stage, world, db_session
    ):
        mark = world.param(stage.schema, 2, "Марка")  # обязателен: `releasable IS NULL`
        db_session.flush()
        kp_variant = world.variant(
            stage.family, stage.schema, {1: _value_of(stage, 1, "50 мм"), 2: world.value(mark, "А")}
        )
        history_variant = world.variant(
            stage.family, stage.schema, {1: _value_of(stage, 1, "50 мм"), 2: world.value(mark, "Б")}
        )
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, variant=history_variant)
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain, variant=kp_variant)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert (line["state"], line["no_history_reason"]) == ("no_history", "no_params")

    def test_a_contract_of_the_tender_object_and_the_neighbour_kp_are_not_in_the_reference(
        self, member_client, stage, world
    ):
        chain = world.chain(("Раздел А",), "whole")
        valid, _, _ = _hist(stage, D("100"), chain=chain)
        winner, _, _ = _hist(stage, D("60"), chain=chain, object_=stage.tender.object)
        rnd, part = _kp_part(stage)
        neighbour = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        _line(stage, neighbour.proposal, D("50"), chain=chain)
        item = _line(stage, part.proposal, D("110"), chain=chain)

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert [a["contract_id"] for a in panel["analogs"]] == [valid.id]
        assert winner.id not in {a["contract_id"] for a in panel["analogs"]}
        assert _pair(panel["line"]["lowest"]["value"]) == (D("100"), D("100"))

    def test_the_reference_is_ordered_by_place_then_date_then_price(self, member_client, stage, world):
        near = world.chain(("Корпус 1", "Подземная часть"), "underground")
        far = world.chain(("Корпус 7", "Подземная часть"), "underground")
        _, _, close = _hist(stage, D("130"), chain=near, prepared=dt.date(2025, 1, 1))
        _, _, distant = _hist(stage, D("90"), chain=far, prepared=dt.date(2026, 7, 1))
        rnd, part, item = _scene_with_chain(stage, near, price=D("110"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        # место: у ближнего две общие главы, у дальнего одна; цена и дата порядка не меняют
        assert [a["analog_id"] for a in panel["analogs"]] == [close.id, distant.id]
        assert [a["place_match"] for a in panel["analogs"]] == [2, 1]
        assert [a["section_titles"] for a in panel["analogs"]] == [["Корпус 1", "Подземная часть"], ["Корпус 7", "Подземная часть"]]


def _value_of(stage, ordinal: int, text: str) -> int:
    """Идентификатор значения параметра сцены по тексту (значение, на котором стоит её вариант)."""
    param = stage.db.execute(
        sa.select(FamilyParameter).where(FamilyParameter.schema_id == stage.schema.id, FamilyParameter.ordinal == ordinal)
    ).scalar_one()
    return stage.world.value(param, text)


# ---------------------------------------------------------------------------
#  Самая низкая и самая свежая в ответе
# ---------------------------------------------------------------------------


class TestFreshest:
    def _dated(self, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        _, _, early = _hist(stage, D("100"), chain=chain, prepared=dt.date(2025, 1, 10))
        late_contract, _, late = _hist(stage, D("130"), chain=chain, prepared=dt.date(2026, 6, 15))
        rnd, part = _kp_part(stage, vat=D("20"))
        item = _line(stage, part.proposal, D("144"), chain=chain)  # нетто 120 за 10
        return rnd, part, item, early, late, late_contract

    def test_freshest_is_the_analog_nearest_to_the_stage_month_and_lowest_is_the_cheapest(
        self, member_client, stage, world
    ):
        rnd, part, item, early, late, late_contract = self._dated(stage, world)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["lowest"]["analog_id"] == early.id
        assert _pair(line["lowest"]["value"]) == (D("100"), D("120"))
        freshest = line["freshest"]
        assert (freshest["analog_id"], freshest["contract_id"], freshest["period"]) == (
            late.id,
            late_contract.id,
            "2026-06",
        )
        assert _pair(freshest["value"]) == (D("130"), D("156"))
        assert line["to_lowest"] == {"net": "200.00", "shown": "240.00"}
        assert line["to_freshest"] == {"net": "-100.00", "shown": "-120.00"}
        # за единицу: цена КП 120 против 100 и 130; без округления до копеек и без веса
        assert _pair(line["to_lowest_unit"]) == (D("20"), D("24"))
        assert _pair(line["to_freshest_unit"]) == (D("-10"), D("-12"))
        assert line["over_lowest"] == {"net": "200.00", "shown": "240.00"}
        assert line["over_freshest"] == {"net": "0.00", "shown": "0.00"}
        assert line["target_kind"] == "lowest"

    def test_the_panel_marks_the_lowest_and_the_freshest_analog(self, member_client, stage, world):
        rnd, part, item, early, late, _ = self._dated(stage, world)

        panel = _panel(member_client, stage.tender, rnd, part, item)

        flags = {a["analog_id"]: (a["is_lowest"], a["is_freshest"], a["period"]) for a in panel["analogs"]}
        assert flags == {early.id: (True, False, "2025-01"), late.id: (False, True, "2026-06")}

    def test_an_amendment_without_a_date_gives_no_freshest_and_the_line_stays_comparable(
        self, member_client, stage, world
    ):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1)
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("120"), chain=chain)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "comparable"
        assert line["freshest"] is None and line["to_freshest"] is None and line["over_freshest"] is None
        assert line["to_freshest_unit"] is None and _pair(line["to_lowest_unit"]) == (D("20"), D("20"))
        assert _pair(line["lowest"]["value"]) == (D("100"), D("100"))

    def test_the_stage_month_comes_from_the_round_even_without_a_series(self, member_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=dt.date(2026, 8, 1))
        _, _, nearest = _hist(stage, D("100"), chain=chain, prepared=dt.date(2024, 1, 1))
        rnd, part = _kp_part(stage, held_on=dt.date(2024, 2, 20))
        item = _line(stage, part.proposal, D("120"), chain=chain)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        # цена равна у обоих: ближе к февралю 2024 — смета января 2024
        assert line["freshest"]["analog_id"] == nearest.id

    def test_a_price_tie_of_the_lowest_goes_to_the_contract_with_this_contractor(self, member_client, stage, world):
        """§2.9: при равной цене самая низкая — аналог договора с подрядчиком этого КП. Его строка
        заведена второй (больший `position_item_id`), даты цены равны: без подрядчика выиграл бы чужой."""
        chain = world.chain(("Раздел А",), "whole")
        rnd, part = _kp_part(stage)
        _, _, other = _hist(stage, D("100"), chain=chain)
        own_contract, _, own = _hist(stage, D("100"), chain=chain, contractor=part.contractor)
        item = _line(stage, part.proposal, D("120"), chain=chain)
        assert other.id < own.id

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert (line["lowest"]["analog_id"], line["lowest"]["contract_id"]) == (own.id, own_contract.id)

    def test_a_unit_mismatch_line_keeps_to_freshest_but_has_no_over_freshest(self, member_client, stage, world):
        """§2.12: «выше самых свежих» — только у `comparable`; у «разных единиц» разница есть, суммы нет."""
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=dt.date(2026, 1, 1))
        _hist(stage, D("120"), chain=chain, prepared=dt.date(2026, 6, 1))
        rnd, part = _kp_part(stage)
        item = _line(stage, part.proposal, D("1500"), chain=chain)  # 1500 / 110 > 5

        line = _line_of(_ok(member_client, stage.tender, rnd, part), item)

        assert line["state"] == "unit_mismatch"
        assert line["to_freshest"] == {"net": "13800.00", "shown": "13800.00"}  # (1500 - 120) * 10
        assert _pair(line["to_freshest_unit"]) == (D("1380"), D("1380"))
        assert (line["over_freshest"], line["over_lowest"], line["target_kind"]) == (None, None, None)


# ---------------------------------------------------------------------------
#  Итоги: покрытие самой свежей (К6)
# ---------------------------------------------------------------------------


def _totals(client, stage, rnd, part, **params) -> dict:
    return _ok(client, stage.tender, rnd, part, **params)["participants"][0]["totals"]


class TestCoverage:
    def test_row_1_no_comparable_lines(self, member_client, stage, world):
        rnd, part, _ = _scene_with_chain(stage, world.chain(("Раздел А",), "whole"))  # истории нет

        totals = _totals(member_client, stage, rnd, part)

        assert totals["over_freshest"] is None
        assert (totals["comparable_lines"], totals["freshest_lines"]) == (0, 0)
        assert totals["over_lowest"] == {"net": "0.00", "shown": "0.00"}

    def test_row_2_no_comparable_line_has_a_freshest_gives_null_not_zero(self, member_client, stage, world):
        """К6: ни у одной сравнимой строки нет самой свежей — `null`, а не `0`."""
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1)
        rnd, part = _kp_part(stage)
        _line(stage, part.proposal, D("120"), chain=chain)

        totals = _totals(member_client, stage, rnd, part)

        assert totals["over_freshest"] is None
        assert (totals["comparable_lines"], totals["freshest_lines"]) == (1, 0)
        assert totals["over_lowest"] == {"net": "200.00", "shown": "200.00"}

    def test_row_3_the_freshest_exists_for_part_of_the_lines(self, member_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        v2 = _second_variant(stage)
        _hist(stage, D("100"), chain=chain, prepared=dt.date(2026, 6, 1))
        _hist(stage, D("100"), chain=chain, prepared=None, amendment_no=1, variant=v2)
        rnd, part = _kp_part(stage)
        _line(stage, part.proposal, D("120"), chain=chain)
        _line(stage, part.proposal, D("120"), chain=chain, variant=v2)

        totals = _totals(member_client, stage, rnd, part)

        assert (totals["comparable_lines"], totals["freshest_lines"]) == (2, 1)
        assert totals["over_freshest"] == {"net": "200.00", "shown": "200.00"}  # только строка с самой свежей
        assert totals["over_lowest"] == {"net": "400.00", "shown": "400.00"}

    def test_row_4_every_comparable_line_has_a_freshest(self, member_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=dt.date(2026, 6, 1))
        rnd, part = _kp_part(stage)
        _line(stage, part.proposal, D("120"), chain=chain)
        _line(stage, part.proposal, D("120"), chain=chain)

        totals = _totals(member_client, stage, rnd, part)

        assert (totals["comparable_lines"], totals["freshest_lines"]) == (2, 2)
        assert totals["over_freshest"] == {"net": "400.00", "shown": "400.00"}

    def test_the_coverage_follows_the_articles(self, member_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain, prepared=dt.date(2026, 6, 1))
        rnd, part = _kp_part(stage)
        _line(stage, part.proposal, D("120"), chain=chain)
        body = _ok(member_client, stage.tender, rnd, part)

        (article,) = (a for a in body["articles"] if a["lines_totals"] is not None)
        assert article["lines_totals"]["comparable_lines"] == 1
        assert article["lines_totals"]["freshest_lines"] == 1
        assert body["participants"][0]["totals"] == article["lines_totals"]


# ---------------------------------------------------------------------------
#  Чтение не пишет (К9), типы JSON
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_the_three_reads_write_nothing_on_the_new_paths(self, member_client, stage, world, db_session):
        """К9: чтение не пишет. Сцена трогает все новые пути: цепочки, задания зон и признака,
        справку аналогов, ряд ИПЦ."""
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
        chain = world.chain(("Раздел Б",), None, proposed="territory")
        disputed = _line(stage, part.proposal, D("100"), chain=chain)
        pending = world.chain(("Раздел В",), None)
        _zone_job(db_session, pending, "pending")
        busy = _line(stage, part.proposal, D("100"), chain=pending)
        db_session.flush()

        with count_statements(db_session) as seen:
            offer = _ok(member_client, stage.tender, rnd, part, inflation_series_id=series_id)
            panel = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)
            assert _panel_get(member_client, stage.tender, rnd, part, item, level="v", scope="cls").status_code == 422
            round_body = _round_get(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert round_body.status_code == 200
        assert _line_of(offer, disputed)["state"] == "no_zone"
        assert _line_of(offer, busy)["state"] == "in_progress"
        assert panel["analogs"]
        assert seen and _writes(seen) == []

    def test_the_answers_carry_no_float_literal(self, member_client, stage, world, db_session):
        rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100.3333"), chain=chain)
        _hist(stage, D("90000"), chain=chain)

        # `_json` и `_panel` падают на любом дробном литерале
        offer = _ok(member_client, stage.tender, rnd, part, inflation_series_id=series_id)
        panel = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)

        assert offer["lines"] and panel["analogs"]
        for analog in panel["analogs"]:
            for key in ("price", "nominal"):
                assert isinstance(analog[key]["net"], str) and isinstance(analog[key]["shown"], str)
            assert analog["outlier_ratio"] is None or isinstance(analog["outlier_ratio"], str)
        totals = offer["participants"][0]["totals"]
        for key in ("over_lowest", "below"):
            assert isinstance(totals[key]["net"], str)
        assert isinstance(totals["comparable_lines"], int)

    def test_a_ratio_of_an_outlier_is_a_string_with_the_exact_value(self, member_client, stage, world):
        chain = world.chain(("Раздел А",), "whole")
        _hist(stage, D("100"), chain=chain)
        _hist(stage, D("110"), chain=chain)
        _hist(stage, D("700"), chain=chain)
        rnd, part, item = _scene_with_chain(stage, chain, price=D("120"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (ratio,) = (a["outlier_ratio"] for a in panel["analogs"] if a["outlier_ratio"] is not None)
        assert abs(D(ratio) - D("6.6666666666666666666666666667")) < TOLERANCE

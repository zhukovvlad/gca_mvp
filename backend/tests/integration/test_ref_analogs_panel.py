"""Блоки А и Г в панели строки, `other_zones` в разборе оффера (спека справочных аналогов §2.4, §2.5,
§2.8, §2.9, §2.11, §2.16, §2.20).

Сцены строятся помощниками экрана 4а; семьи, названия и числа выдуманы. Эталоны — независимые
литералы `Decimal`. Ставки истории — 0 %, поэтому нетто истории равно цене; ставка КП — 20 %.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest
import sqlalchemy as sa

from crud.our_price_load import load_history, load_work_history
from models import CatalogContext, ContextParameterValue, FamilyParameterSchema, PositionItem, SemanticJob
from services.family_links import LinkInput, create_link
from services.price_basis import _CONTEXT
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _line_of, _ok, _pair, _panel, _second_variant
from tests.integration.test_our_price_round_api import Stage, count_statements
from tests.integration.test_semantic_queue_api import _active_family, _unit_id

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники сцены
# ---------------------------------------------------------------------------


def _row(stage, proposal, unit_cost, *, family=None, variant=None, chain=None, cat="R", title=None, unit=None):
    """Строка в главе статьи `cat`; семья и вариант — сцены, если не названы; `unit` — символ единицы
    каталожной позиции строки (по умолчанию м²)."""
    qty = D("10")
    extra = {} if chain is None else {"chain": chain}
    default_unit = stage.world.unit_id
    if unit is not None:
        # единица каталожной позиции должна совпасть с единицей семьи уже при назначении семьи
        stage.world.unit_id = _unit_id(stage.db, unit)
    item = stage.world.line(
        proposal,
        chapter=stage.chapter(proposal, cat),
        unit_cost=unit_cost,
        total=unit_cost * qty,
        suggested=qty,
        quantity=qty,
        family=family or stage.family,
        variant=variant or stage.variant,
        **extra,
    )
    if title is not None:
        # каталожная позиция уникальна по названию и единице: наименование в смете задаём после
        item.job_title_in_proposal = title
    stage.world.unit_id = default_unit
    stage.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == item.context_id))
    return item


def _contract_row(stage, unit_cost, *, contractor=None, rate_class="own", year=2025, **kwargs):
    """Договор истории с одной строкой; возвращает `(договор, строка)`."""
    contract = stage.world.contract(
        rate_class=stage.rate_class if rate_class == "own" else rate_class, contractor=contractor, year=year
    )
    _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
    return contract, _row(stage, proposal, unit_cost, **kwargs)


def _kp_part(stage, *, vat=D("20")):
    rnd = stage.round()
    return rnd, stage.participant(rnd, "Подрядчик А", vat=vat)


def _family_with_param(stage, title, unit, param_name):
    """Активная семья в единице `unit` с одним главным параметром и вариантом: `(семья, схема, параметр, вариант)`."""
    family = _active_family(stage.db, title=title, unit_name=unit, actor_id=stage.world.admin.id)
    schema = stage.db.execute(
        sa.select(FamilyParameterSchema).where(FamilyParameterSchema.family_id == family.id)
    ).scalar_one()
    param = stage.world.param(schema, 1, param_name)
    variant = stage.world.variant(family, schema, {1: stage.world.value(param, "50 мм")})
    return family, schema, param, variant


def _set_value(stage, item, schema, param, text):
    stage.db.add(
        ContextParameterValue(
            context_id=item.context_id,
            schema_id=schema.id,
            parameter_id=param.id,
            value_id=stage.world.value(param, text),
            source="manual",
        )
    )
    stage.db.flush()


class Linked:
    """Строка КП в семье `kp_unit` без своих аналогов и строка договора связанной семьи в `ref_unit`."""

    def __init__(self, stage, *, kp_unit="M3", ref_unit="M2", kind="thickness", text="100 мм", price=D("1107.24"),
                 kp_price=D("14400")):
        self.stage = stage
        self.kp_family, self.kp_schema, self.kp_param, self.kp_variant = _family_with_param(
            stage, "Семья К", kp_unit, "Толщина"
        )
        self.ref_family, self.ref_schema, self.ref_param, self.ref_variant = _family_with_param(
            stage, "Семья С", ref_unit, "Толщина слоя"
        )
        self.contract, self.ref_item = _contract_row(
            stage, price, family=self.ref_family, variant=self.ref_variant, unit=ref_unit, title="Подготовка"
        )
        if text is not None:
            _set_value(stage, self.ref_item, self.ref_schema, self.ref_param, text)
        self.rnd, self.part = _kp_part(stage)
        self.kp_item = _row(
            stage, self.part.proposal, kp_price, family=self.kp_family, variant=self.kp_variant, unit=kp_unit
        )
        self.link_id = create_link(
            stage.db,
            body=LinkInput(
                family_a_id=self.kp_family.id, family_b_id=self.ref_family.id, kind=kind,
                param_a_id=self.kp_param.id, param_b_id=self.ref_param.id,
            ),
            actor_id=stage.world.admin.id,
        )
        stage.db.flush()

    def panel(self, client) -> dict:
        return _panel(client, self.stage.tender, self.rnd, self.part, self.kp_item)


def _only_group(body: dict) -> dict:
    (group,) = body["reference_links"]
    return group


# ---------------------------------------------------------------------------
#  Загрузчик: единица строки истории
# ---------------------------------------------------------------------------


def test_ref_analogs_the_work_history_line_carries_the_catalog_position_unit(stage, db_session) -> None:
    family, _, _, variant = _family_with_param(stage, "Семья Д", "M3", "Толщина")
    _, item = _contract_row(stage, D("100"), family=family, variant=variant, unit="M3")
    contracts, _ = load_history(db_session, exclude_object_id=stage.tender.object_id)
    lines, _ = load_work_history(db_session, estimate_of={c.contract_id: c.estimate_id for c in contracts})
    (line,) = lines
    assert line.position_item_id == item.id
    assert line.unit_id == _unit_id(db_session, "M3")


# ---------------------------------------------------------------------------
#  Блок Г: сценарий подбетонки (критическое свойство «деньги пересчёта»)
# ---------------------------------------------------------------------------


def test_ref_analogs_subbase_scenario_panel_gives_11072_40_per_cubic_metre(member_client, stage) -> None:
    """Строка КП в м³ без своих аналогов, связь «толщина» с семьёй в м², значение «100 мм»:
    1 107,24 ₽/м² → 11 072,40 ₽/м³, основание «толщина 100 мм»."""
    scene = Linked(stage)
    body = scene.panel(member_client)
    group = _only_group(body)
    (row,) = group["rows"]
    assert (group["link_id"], group["kind"], group["other_class"]) == (scene.link_id, "thickness", False)
    assert group["family"] == {"id": scene.ref_family.id, "title": "Семья С", "unit": "м²"}
    assert row["analog_id"] == scene.ref_item.id
    assert row["ids"] == [scene.ref_item.id]
    assert row["title"] == "Подготовка"
    assert row["unit"] == "м²"
    assert _pair(row["price"])[0] == D("1107.24")
    conversion = dict(row["conversion"])
    assert D(conversion.pop("factor")) == D("0.1")
    assert conversion == {"method": "link", "factor_shown": "0,1", "basis": "толщина 100 мм", "link_id": scene.link_id}
    assert _pair(row["kp_unit_price"])[0] == D("11072.4")
    assert row["no_conversion_reason"] is None
    assert row["param_value"] == "100 мм"
    assert body["reference_zones"] == []


def test_ref_analogs_panel_money_pairs_are_net_and_shown_in_the_participant_rate(member_client, stage) -> None:
    """Ставка КП 20 %: цена истории 1 107,24 (нетто) показана 1 328,688."""
    row = _only_group(Linked(stage).panel(member_client))["rows"][0]
    assert _pair(row["price"]) == (D("1107.24"), D("1328.688"))
    assert _pair(row["nominal"]) == (D("1107.24"), D("1328.688"))
    assert _pair(row["kp_unit_price"]) == (D("11072.4"), D("13286.88"))


def test_ref_analogs_panel_deviation_is_kp_net_over_the_converted_price(member_client, stage) -> None:
    """Нетто КП 14 400 / 1,2 = 12 000 за м³; цена в единице КП 11 072,4: отклонение +8,3...%."""
    row = _only_group(Linked(stage).panel(member_client))["rows"][0]
    with localcontext(_CONTEXT):
        expected = (D("12000") / D("11072.4") - 1) * 100
    assert D(row["deviation_pct"]) == expected
    assert D("8.37") < D(row["deviation_pct"]) < D("8.38")


def test_ref_analogs_panel_factor_is_the_exact_n_and_round_trips_for_the_inverse_direction(
    member_client, stage
) -> None:
    """Строка договора в м³ (большая единица), строка КП в м²: N = 1 / 0,3 — не обрывается; строка
    `factor` возвращается клиентом как `expected_factor` и обязана дать то же `Decimal`."""
    scene = Linked(stage, kp_unit="M2", ref_unit="M3", text="300 мм", price=D("5000"), kp_price=D("1200"))
    row = _only_group(scene.panel(member_client))["rows"][0]
    factor = row["conversion"]["factor"]
    assert factor.startswith("3.33333333333333")
    assert "E" not in factor
    with localcontext(_CONTEXT):
        assert D(factor) == D(1) / D("0.3")
    assert row["conversion"]["basis"] == "толщина 300 мм"
    assert row["conversion"]["factor_shown"] == "≈ 3,3333"
    with localcontext(_CONTEXT):
        assert D(row["kp_unit_price"]["net"]) == D("5000") / (D(1) / D("0.3"))


def test_ref_analogs_panel_a_whole_number_factor_is_not_written_with_an_exponent(member_client, stage) -> None:
    """1 / 0,1 = 10: запись `10`, не `1E+1`."""
    scene = Linked(stage, kp_unit="M2", ref_unit="M3", text="100 мм", price=D("5000"), kp_price=D("1200"))
    conversion = _only_group(scene.panel(member_client))["rows"][0]["conversion"]
    assert conversion["factor"] == "10"
    assert conversion["factor_shown"] == "10"


def test_ref_analogs_panel_a_value_that_is_not_a_number_gives_the_reason_and_no_price(member_client, stage) -> None:
    row = _only_group(Linked(stage, text="переменная").panel(member_client))["rows"][0]
    assert row["conversion"] is None
    assert row["no_conversion_reason"] == "значение «переменная» — не число"
    assert row["kp_unit_price"] is None
    assert row["deviation_pct"] is None
    assert row["outlier_ratio"] is None
    assert row["param_value"] == "переменная"


def test_ref_analogs_panel_a_row_without_a_value_gives_the_reason_with_the_parameter_name(
    member_client, stage
) -> None:
    row = _only_group(Linked(stage, text=None).panel(member_client))["rows"][0]
    assert row["no_conversion_reason"] == "у строки нет значения «Толщина слоя»"
    assert row["conversion"] is None


def test_ref_analogs_panel_a_link_that_stopped_acting_gives_no_group(member_client, stage, db_session) -> None:
    """Единица семьи изменилась после записи связи: связь не действует, блока Г нет."""
    scene = Linked(stage)
    db_session.execute(
        sa.text("UPDATE work_families SET unit_id = :u WHERE id = :f"),
        {"u": _unit_id(db_session, "M"), "f": scene.ref_family.id},
    )
    db_session.flush()
    assert scene.panel(member_client)["reference_links"] == []


def test_ref_analogs_panel_a_comparable_row_has_no_reference_blocks(member_client, stage) -> None:
    stage.history(D("100"))
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    body = _panel(member_client, stage.tender, rnd, part, item)
    assert body["line"]["state"] == "comparable"
    assert (body["reference_zones"], body["reference_links"]) == ([], [])
    assert "other_zones" not in body["line"]


# ---------------------------------------------------------------------------
#  Блок А
# ---------------------------------------------------------------------------


def _underground(stage):
    return stage.world.chain(("Корпус", "Подземная часть"), "underground")


def test_ref_analogs_panel_other_zone_block_lists_the_rung_rows_with_marks_and_paths(member_client, stage) -> None:
    chain = _underground(stage)
    rows = []
    for price in ("100", "100", "100", "1000"):
        rows.append(_contract_row(stage, D(price), chain=chain)[1])
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    body = _panel(member_client, stage.tender, rnd, part, item)

    assert body["line"]["state"] == "no_history"
    assert body["line"]["no_history_reason"] == "no_zone_match"
    (group,) = body["reference_zones"]
    assert (group["zone"], group["rung_no"], group["released"], group["other_class"]) == ("underground", 1, [], False)
    by_id = {r["analog_id"]: r for r in group["rows"]}
    assert set(by_id) == {r.id for r in rows}
    assert by_id[rows[3].id]["outlier_ratio"] == "10"
    assert all(by_id[r.id]["outlier_ratio"] is None for r in rows[:3])
    assert all(r["conversion"] is None and r["zone"] == "underground" for r in group["rows"])
    assert [p["title"] for p in by_id[rows[0].id]["chapter_paths"][0]]
    assert body["line"]["other_zones"] == [{"zone": "underground", "lines": 4, "contracts": 4}]


def test_ref_analogs_panel_other_zone_rows_fold_within_one_contract_and_keep_the_least_id(
    member_client, stage
) -> None:
    """Две строки одного договора с равными наименованием и ценой — одна строка `×2`; пути разделов
    у них разные. `other_zones` считает строки до свёртки."""
    chain = _underground(stage)
    contract = stage.world.contract(rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
    first = _row(stage, proposal, D("100"), chain=chain, cat="R", title="Подготовка")
    second = _row(stage, proposal, D("100"), chain=chain, cat="R2", title="Подготовка")
    other = _row(stage, proposal, D("101"), chain=chain, cat="R", title="Подготовка")
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    body = _panel(member_client, stage.tender, rnd, part, item)

    (group,) = body["reference_zones"]
    folded = {r["analog_id"]: r for r in group["rows"]}
    assert set(folded) == {first.id, other.id}
    assert folded[first.id]["ids"] == sorted([first.id, second.id])
    assert len(folded[first.id]["chapter_paths"]) == 2
    assert folded[other.id]["ids"] == [other.id]
    assert body["line"]["other_zones"] == [{"zone": "underground", "lines": 3, "contracts": 1}]


def test_ref_analogs_panel_marks_with_this_contractor(member_client, stage) -> None:
    chain = _underground(stage)
    rnd, part = _kp_part(stage)
    _, mine = _contract_row(stage, D("100"), chain=chain, contractor=part.contractor)
    _, foreign = _contract_row(stage, D("100"), chain=chain)
    item = stage.kp(part, D("144"))
    body = _panel(member_client, stage.tender, rnd, part, item)
    flags = {r["analog_id"]: r["with_this_contractor"] for r in body["reference_zones"][0]["rows"]}
    assert flags == {mine.id: True, foreign.id: False}


def test_ref_analogs_panel_a_row_with_no_params_match_has_no_other_zone_block(member_client, stage) -> None:
    """Вариант в другой зоне не совпал ни с чем: причина `no_params`, блока нет и подписи нет."""
    chain = _underground(stage)
    other_family, _, _, other_variant = _family_with_param(stage, "Семья Ж", "M2", "Толщина")
    _contract_row(stage, D("100"), chain=chain)
    rnd, part = _kp_part(stage)
    item = _row(stage, part.proposal, D("144"), family=other_family, variant=other_variant)
    body = _panel(member_client, stage.tender, rnd, part, item)
    assert body["line"]["no_history_reason"] == "no_family"
    assert (body["reference_zones"], body["reference_links"]) == ([], [])
    assert "other_zones" not in body["line"]


# ---------------------------------------------------------------------------
#  Разбор оффера: `other_zones` без новых запросов (§2.20)
# ---------------------------------------------------------------------------


def test_ref_analogs_offer_view_marks_no_zone_match_rows_with_other_zones(member_client, stage) -> None:
    chain = _underground(stage)
    _contract_row(stage, D("100"), chain=chain)
    _contract_row(stage, D("110"), chain=chain)
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    line = _line_of(_ok(member_client, stage.tender, rnd, part), item)
    assert line["other_zones"] == [{"zone": "underground", "lines": 2, "contracts": 2}]


def test_ref_analogs_offer_view_has_no_other_zones_field_without_groups(member_client, stage) -> None:
    stage.history(D("100"))
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    line = _line_of(_ok(member_client, stage.tender, rnd, part), item)
    assert line["state"] == "comparable"
    assert "other_zones" not in line


def test_ref_analogs_offer_view_other_zones_cost_no_new_queries(member_client, stage, db_session) -> None:
    """Число запросов разбора не зависит от справочных данных: строка с группами блока А (аналог в
    другой зоне) и та же строка, у которой аналог в её зоне, но с другим вариантом (`no_params`, групп
    нет), — те же запросы, ни одного к связям или значениям параметров."""
    chain = _underground(stage)
    _, moved = _contract_row(stage, D("100"), chain=chain)
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    _ok(member_client, stage.tender, rnd, part)  # прогрев кэшей процесса

    with count_statements(db_session) as with_groups:
        body = _ok(member_client, stage.tender, rnd, part)
    line = _line_of(body, item)  # чтение `item.id` может само обратиться к базе: вне счётчика
    assert line["no_history_reason"] == "no_zone_match"
    assert line["other_zones"] == [{"zone": "underground", "lines": 1, "contracts": 1}]

    other_variant = _second_variant(stage)
    db_session.execute(
        sa.update(CatalogContext).where(CatalogContext.id == moved.context_id).values(work_variant_id=other_variant.id)
    )
    db_session.execute(
        sa.update(PositionItem).where(PositionItem.id == moved.id).values(section_chain_id=stage.world.whole_chain.id)
    )
    db_session.flush()
    with count_statements(db_session) as without:
        body = _ok(member_client, stage.tender, rnd, part)
    line = _line_of(body, item)
    assert line["no_history_reason"] == "no_params"
    assert "other_zones" not in line

    assert len(with_groups) == len(without)
    joined = " ".join(with_groups).lower()
    assert "family_links" not in joined


def test_ref_analogs_panel_other_zones_field_is_the_same_as_in_the_offer_view(member_client, stage) -> None:
    chain = _underground(stage)
    _contract_row(stage, D("100"), chain=chain)
    _contract_row(stage, D("100"), chain=chain)
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    offer = _line_of(_ok(member_client, stage.tender, rnd, part), item)
    panel = _panel(member_client, stage.tender, rnd, part, item)["line"]
    assert offer["other_zones"] == panel["other_zones"]

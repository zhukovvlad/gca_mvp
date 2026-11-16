"""Справочная цель строки КП: запись, чтение, `choice` (спека справочных аналогов §2.13–§2.16, §2.19).

Сцены строятся помощниками панели и поиска справочных строк; названия, семьи и числа выдуманы. Эталоны —
независимые литералы `Decimal` и строки. Ставки истории — 0 %, поэтому нетто истории равно цене; ставка
КП — 20 %. Состояние `kp_line_targets` читается столбцами: ответ роутера пишет в обход identity map.
Каждое правило отказа проверено входом, отличающимся от принятого ровно одним: порядок отказов — по
таблице §2.13.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import CatalogContext, CatalogPosition, ContextMember, ContextParameterValue, KpLineTarget, UserRole
from services.family_links import LinkInput, create_link, delete_link
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _inflation_scene, _json, _line_of, _ok, _panel, _put, _url
from tests.integration.test_our_price_round_api import Stage, count_statements
from tests.integration.test_our_price_round_api import _ok as _round_ok
from tests.integration.test_ref_analogs_panel import (
    Linked,
    _contract_row,
    _family_with_param,
    _kp_part,
    _row,
    _set_value,
    _underground,
)
from tests.integration.test_semantic_queue_api import _unit_id

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
    """Клиент администратора, который перед каждым запросом закрепляет сцену: `rollback` роутера
    откатывает транзакцию сессии, а в проде сцена к запросу уже закоммичена."""
    send = admin_client.request

    def request(*args, **kwargs):
        db_session.commit()
        return send(*args, **kwargs)

    admin_client.request = request
    return admin_client


# ---------------------------------------------------------------------------
#  Помощники сцены
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    """Строка КП `item` участника `part` этапа `rnd` и строки договоров сцены."""

    stage: object
    rnd: object
    part: object
    item: object
    rows: list

    @property
    def url(self) -> str:
        return f"{_url(self.stage.tender, self.rnd, self.part)}/lines/{self.item.id}"

    def put(self, client, body, **params):
        return client.put(f"{self.url}/target", json=body, params=params)

    def delete(self, client):
        return client.delete(f"{self.url}/target")

    def panel(self, client, **params) -> dict:
        return _panel(client, self.stage.tender, self.rnd, self.part, self.item, **params)

    def offer(self, client, **params) -> dict:
        return _ok(client, self.stage.tender, self.rnd, self.part, **params)

    def offer_line(self, client, **params) -> dict:
        return _line_of(self.offer(client, **params), self.item)

    def stored(self):
        return _stored(self.stage.db, self.item)

    def search(self, client, q, **params) -> dict:
        self.stage.db.flush()
        response = client.get(f"{self.url}/reference-search", params={"q": q, **params})
        assert response.status_code == 200, response.text
        return _json(response)


def _stored(db, item):
    return db.execute(
        sa.select(
            KpLineTarget.kind,
            KpLineTarget.analog_position_item_id,
            KpLineTarget.ref_origin,
            KpLineTarget.ref_method,
            KpLineTarget.ref_factor,
            KpLineTarget.ref_basis,
            KpLineTarget.ref_link_id,
            KpLineTarget.ref_without_analogs,
        ).where(KpLineTarget.position_item_id == item.id)
    ).one_or_none()


def _no_row(ctx) -> bool:
    return ctx.stored() is None


def _zone_scene(stage, prices=("100", "100", "100", "1000")) -> Ctx:
    """Строка КП без ступени в своей зоне (`no_zone_match`) и строки договоров в подземной зоне: блок А."""
    chain = _underground(stage)
    rows = [_contract_row(stage, D(price), chain=chain, title="Подготовка")[1] for price in prices]
    rnd, part = _kp_part(stage)
    return Ctx(stage, rnd, part, stage.kp(part, D("144")), rows)


def _own_scene(stage, prices=("100", "100", "100", "1000")) -> Ctx:
    """Строка КП со своей ступенью (`comparable`): строки договоров в её зоне и семье."""
    rows = [_contract_row(stage, D(price), title="Подготовка")[1] for price in prices]
    rnd, part = _kp_part(stage)
    return Ctx(stage, rnd, part, stage.kp(part, D("144")), rows)


def _linked(stage, **kwargs) -> tuple[Linked, Ctx]:
    scene = Linked(stage, **kwargs)
    return scene, Ctx(stage, scene.rnd, scene.part, scene.kp_item, [scene.ref_item])


def _linked_set(stage) -> tuple[Linked, Ctx]:
    """Строка КП в м³ и четыре строки связанной семьи в м² со значением «100 мм»: цены 100, 100, 100, 1000."""
    scene, ctx = _linked(stage, price=D("100"))
    for price in ("100", "100", "1000"):
        _, item = _contract_row(
            stage, D(price), family=scene.ref_family, variant=scene.ref_variant, unit="M2", title="Подготовка"
        )
        _set_value(stage, item, scene.ref_schema, scene.ref_param, "100 мм")
        ctx.rows.append(item)
    return scene, ctx


def _body(analog_id, origin="search", conversion=None, **extra) -> dict:
    return {"kind": "reference", "analog_id": analog_id, "origin": origin, "conversion": conversion, **extra}


def _manual(factor, basis="по справке") -> dict:
    return {"method": "manual", "factor": factor, "basis": basis}


def _link_conversion(row: dict) -> dict:
    """Пересчёт по связи из строки панели или поиска, отданный без изменений."""
    conversion = row["conversion"]
    return {
        "method": "link",
        "link_id": conversion["link_id"],
        "expected_factor": conversion["factor"],
        "expected_basis": conversion["basis"],
    }


def _panel_row(panel: dict, analog_id: int) -> dict:
    (found,) = (row for group in panel["reference_links"] for row in group["rows"] if row["analog_id"] == analog_id)
    return found


def _code(response) -> str:
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail["code"]


def _refused(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code, detail
    return detail


def _save_ctx(client, ctx, body) -> None:
    response = ctx.put(client, body)
    assert response.status_code == 204, response.text


def _set_text(stage, scene, text) -> None:
    """Значение параметра строки договора связи — другой текст."""
    stage.db.execute(
        sa.update(ContextParameterValue)
        .where(
            ContextParameterValue.context_id == scene.ref_item.context_id,
            ContextParameterValue.parameter_id == scene.ref_param.id,
        )
        .values(value_id=stage.world.value(scene.ref_param, text))
    )
    stage.db.flush()


def _change_unit(db, family, unit) -> None:
    """Единица семьи сменилась (у семьи есть контексты, поэтому `set_unit` отказал бы): связь перестаёт действовать."""
    db.execute(sa.text("UPDATE work_families SET unit_id = :u WHERE id = :f"), {"u": _unit_id(db, unit), "f": family.id})
    db.flush()


def _amend(stage, contract, prices=("100",), **kwargs) -> list:
    """Допсоглашение договора: новая последняя смета с копиями строк; прежняя смета перестаёт быть справочной."""
    _, proposal = stage.world.estimate(contract, amendment_no=1, vat_rate=D("0"))
    return [_row(stage, proposal, D(price), title="Подготовка", **kwargs) for price in prices]


# ---------------------------------------------------------------------------
#  Отказы по таблице §2.13, по порядку
# ---------------------------------------------------------------------------


def test_ref_analogs_target_a_system_row_is_refused_before_anything_else(admin_client, stage) -> None:
    contract = stage.world.contract(rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
    _put(stage, proposal, "R", D("100"))
    rnd, part = _kp_part(stage)
    system = _put(stage, part.proposal, "R", D("1"), total=D("5000"), kind="SYSTEM")
    ctx = Ctx(stage, rnd, part, system, [])
    detail = _refused(ctx.put(admin_client, _body(987654321, conversion=_manual("2"))), 422, "target_not_applicable")
    assert detail["message"]
    assert _no_row(ctx)


def test_ref_analogs_target_a_work_row_with_the_same_input_passes_the_system_check(admin_client, stage) -> None:
    """Соседний вход: строка-работа, тот же отсутствующий аналог — отказ уже другой."""
    ctx = _zone_scene(stage)
    _refused(ctx.put(admin_client, _body(987654321)), 422, "reference_unavailable")


def _unavailable_row(stage, kind) -> int:
    if kind == "previous_estimate":
        contract = stage.world.contract(rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        old = _row(stage, proposal, D("100"), title="Подготовка")
        _amend(stage, contract)
        return old.id
    if kind == "object_contract":
        contract = stage.world.contract(object_=stage.tender.object, rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        return _row(stage, proposal, D("100"), title="Подготовка").id
    if kind == "system_context":
        contract = stage.world.contract(rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        return _put(stage, proposal, "R", D("100"), kind="SYSTEM").id
    if kind == "chapter":
        contract = stage.world.contract(rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        _row(stage, proposal, D("100"), title="Подготовка")
        return stage.chapter(proposal, "R").id
    if kind == "no_price":
        contract = stage.world.contract(rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        return _row(stage, proposal, D("0"), title="Подготовка").id
    if kind == "below_nominal":
        contract = stage.world.contract(rate_class=stage.rate_class)
        _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
        return _row(stage, proposal, D("0.99"), title="Подготовка").id
    assert kind == "missing"
    return 987654321


@pytest.mark.parametrize(
    "kind",
    ["previous_estimate", "object_contract", "system_context", "chapter", "no_price", "below_nominal", "missing"],
)
def test_ref_analogs_target_a_contract_row_that_is_not_a_reference_row_is_refused(admin_client, stage, kind) -> None:
    ctx = _zone_scene(stage)
    analog_id = _unavailable_row(stage, kind)
    _refused(ctx.put(admin_client, _body(analog_id)), 422, "reference_unavailable")
    assert _no_row(ctx)


def test_ref_analogs_target_a_reference_row_of_the_scene_is_accepted(admin_client, stage) -> None:
    """Соседний вход: строка договора из блока А — то же тело принимается."""
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone")).status_code == 204


def test_ref_analogs_target_unavailable_row_is_refused_before_the_conversion_checks(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    analog_id = _unavailable_row(stage, "system_context")
    _refused(ctx.put(admin_client, _body(analog_id, conversion=_manual("2"))), 422, "reference_unavailable")


@pytest.mark.parametrize("conversion", [_manual("2"), {"method": "link", "link_id": 1, "expected_factor": "1", "expected_basis": "x"}])
def test_ref_analogs_target_a_conversion_between_equal_units_is_refused(admin_client, stage, conversion) -> None:
    ctx = _zone_scene(stage)
    _refused(ctx.put(admin_client, _body(ctx.rows[0].id, "zone", conversion)), 422, "conversion_not_needed")
    assert _no_row(ctx)


def test_ref_analogs_target_equal_units_without_a_conversion_are_accepted(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone")).status_code == 204


def _other_unit_row(stage, unit="M", price=D("700")):
    family, _, _, variant = _family_with_param(stage, f"Семья {unit}", unit, "Толщина")
    return _contract_row(stage, price, family=family, variant=variant, unit=unit, title="Подготовка иная")[1]


def test_ref_analogs_target_different_units_without_a_conversion_are_refused(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    _refused(ctx.put(admin_client, _body(other.id)), 422, "conversion_required")
    assert _no_row(ctx)


def test_ref_analogs_target_a_row_without_a_unit_needs_a_conversion(admin_client, stage, db_session) -> None:
    ctx = _zone_scene(stage)
    db_session.execute(
        sa.update(CatalogPosition).where(CatalogPosition.id == ctx.rows[0].catalog_position_id).values(unit_id=None)
    )
    db_session.flush()
    _refused(ctx.put(admin_client, _body(ctx.rows[0].id, "zone")), 422, "conversion_required")
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone", _manual("1"))).status_code == 204


def test_ref_analogs_target_a_kp_row_without_a_unit_needs_a_conversion(admin_client, stage, db_session) -> None:
    ctx = _zone_scene(stage)
    db_session.execute(
        sa.update(CatalogPosition).where(CatalogPosition.id == ctx.item.catalog_position_id).values(unit_id=None)
    )
    db_session.flush()
    _refused(ctx.put(admin_client, _body(ctx.rows[0].id, "zone")), 422, "conversion_required")


def test_ref_analogs_target_a_link_that_stopped_acting_is_not_applicable(admin_client, stage, db_session) -> None:
    scene, ctx = _linked(stage)
    body = _body(scene.ref_item.id, "link", _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id)))
    _change_unit(db_session, scene.ref_family, "кг")
    _refused(ctx.put(admin_client, body), 422, "link_not_applicable")
    assert _no_row(ctx)


def test_ref_analogs_target_a_link_of_other_families_is_not_applicable(admin_client, stage, db_session) -> None:
    scene, ctx = _linked(stage)
    third, _, third_param, _ = _family_with_param(stage, "Семья Х", "M2", "Толщина слоя")
    foreign_link = create_link(
        db_session,
        body=LinkInput(
            family_a_id=scene.kp_family.id, family_b_id=third.id, kind="thickness",
            param_a_id=scene.kp_param.id, param_b_id=third_param.id,
        ),
        actor_id=stage.world.admin.id,
    )
    conversion = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    assert conversion["link_id"] == scene.link_id
    _refused(
        ctx.put(admin_client, _body(scene.ref_item.id, "link", {**conversion, "link_id": foreign_link})),
        422,
        "link_not_applicable",
    )
    assert _no_row(ctx)


def test_ref_analogs_target_an_unknown_link_is_not_applicable(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    conversion = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _refused(
        ctx.put(admin_client, _body(scene.ref_item.id, "link", {**conversion, "link_id": 987654321})),
        422,
        "link_not_applicable",
    )


@pytest.mark.parametrize("text", ["переменная", None])
def test_ref_analogs_target_a_row_without_a_number_cannot_take_the_link(admin_client, stage, text) -> None:
    scene, ctx = _linked(stage, text=text)
    conversion = {"method": "link", "link_id": scene.link_id, "expected_factor": "0.1", "expected_basis": "толщина 100 мм"}
    _refused(ctx.put(admin_client, _body(scene.ref_item.id, "link", conversion)), 422, "link_not_applicable")
    assert _no_row(ctx)
    assert ctx.put(admin_client, _body(scene.ref_item.id, "search", _manual("0,1", "вручную"))).status_code == 204


def test_ref_analogs_target_a_link_for_a_row_in_a_family_without_one_is_not_applicable(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    other = _other_unit_row(stage)
    conversion = {"method": "link", "link_id": scene.link_id, "expected_factor": "0.1", "expected_basis": "толщина 100 мм"}
    _refused(ctx.put(admin_client, _body(other.id, "search", conversion)), 422, "link_not_applicable")


def test_ref_analogs_target_changed_parameter_value_gives_the_stale_conversion_and_no_target(
    admin_client, stage
) -> None:
    """Критическое свойство К10: пока диалог открыт, значение параметра 100 → 200 мм; запись со старым
    `expected_factor` — `409` с текущими `factor` и `basis`, цели нет."""
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    assert D(seen["expected_factor"]) == D("0.1")
    _set_text(stage, scene, "200 мм")

    detail = _refused(ctx.put(admin_client, _body(scene.ref_item.id, "link", seen)), 409, "conversion_stale")
    assert D(detail["factor"]) == D("0.2")
    assert detail["basis"] == "толщина 200 мм"
    assert _no_row(ctx)

    fresh = {**seen, "expected_factor": detail["factor"], "expected_basis": detail["basis"]}
    assert ctx.put(admin_client, _body(scene.ref_item.id, "link", fresh)).status_code == 204
    stored = ctx.stored()
    assert (stored.ref_factor, stored.ref_basis) == (D("0.2"), "толщина 200 мм")


def test_ref_analogs_target_changed_basis_with_the_same_factor_is_stale(admin_client, stage) -> None:
    """«0,1 м» даёт то же `N`, что «100 мм», но другое основание: сравнение основания — по строке."""
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _set_text(stage, scene, "0,1 м")
    detail = _refused(ctx.put(admin_client, _body(scene.ref_item.id, "link", seen)), 409, "conversion_stale")
    assert D(detail["factor"]) == D("0.1")
    assert detail["basis"] == "толщина 0,1 м"
    assert _no_row(ctx)


def test_ref_analogs_target_expected_factor_is_compared_as_a_decimal(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    same_number = {**seen, "expected_factor": "0.1000"}
    assert ctx.put(admin_client, _body(scene.ref_item.id, "link", same_number)).status_code == 204


@pytest.mark.parametrize("expected", ["0.1001", "abc", ""])
def test_ref_analogs_target_a_different_or_unreadable_expected_factor_is_stale(admin_client, stage, expected) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    detail = _refused(
        ctx.put(admin_client, _body(scene.ref_item.id, "link", {**seen, "expected_factor": expected})),
        409,
        "conversion_stale",
    )
    assert D(detail["factor"]) == D("0.1")
    assert _no_row(ctx)


def test_ref_analogs_target_expected_basis_is_compared_as_a_string(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _refused(
        ctx.put(admin_client, _body(scene.ref_item.id, "link", {**seen, "expected_basis": seen["expected_basis"] + " "})),
        409,
        "conversion_stale",
    )


@pytest.mark.parametrize(
    ("factor", "basis"),
    [
        pytest.param("0", "по справке", id="zero"),
        pytest.param("-1", "по справке", id="negative"),
        pytest.param("abc", "по справке", id="not-a-number"),
        pytest.param("", "по справке", id="empty-factor"),
        pytest.param("0,1234567", "по справке", id="seven-decimals"),
        pytest.param("1000001", "по справке", id="above-a-million"),
        pytest.param("2", "", id="empty-basis"),
        pytest.param("2", "   ", id="blank-basis"),
        pytest.param("2", "я" * 301, id="basis-of-301-characters"),
    ],
)
def test_ref_analogs_target_an_invalid_manual_conversion_is_refused(admin_client, stage, factor, basis) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    _refused(ctx.put(admin_client, _body(other.id, "search", _manual(factor, basis))), 422, "manual_conversion_invalid")
    assert _no_row(ctx)


@pytest.mark.parametrize(
    ("factor", "basis", "stored"),
    [
        pytest.param("0,123456", "я" * 300, D("0.123456"), id="six-decimals-and-300-characters"),
        pytest.param("1000000", "x", D("1000000"), id="a-million"),
        pytest.param(" 2,5 ", "  по справке  ", D("2.5"), id="comma-and-padding"),
    ],
)
def test_ref_analogs_target_manual_conversion_at_the_boundary_is_accepted(
    admin_client, stage, factor, basis, stored
) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    assert ctx.put(admin_client, _body(other.id, "search", _manual(factor, basis))).status_code == 204
    row = ctx.stored()
    assert (row.ref_method, row.ref_factor, row.ref_link_id) == ("manual", stored, None)
    assert row.ref_basis == basis.strip()


def test_ref_analogs_target_invalid_manual_conversion_is_refused_before_the_outlier_check(admin_client, stage) -> None:
    scene, ctx = _linked_set(stage)
    outlier = ctx.rows[3]
    _refused(
        ctx.put(admin_client, _body(outlier.id, "link", _manual("0"))), 422, "manual_conversion_invalid"
    )


def test_ref_analogs_target_a_stale_conversion_is_refused_before_the_outlier_check(admin_client, stage) -> None:
    scene, ctx = _linked_set(stage)
    outlier = ctx.rows[3]
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), outlier.id))
    _refused(
        ctx.put(admin_client, _body(outlier.id, "link", {**seen, "expected_factor": "0.5"})), 409, "conversion_stale"
    )


def test_ref_analogs_target_a_needless_conversion_is_refused_before_the_outlier_check(admin_client, stage) -> None:
    """Помеченная строка блока А в единице КП: с пересчётом — `conversion_not_needed`, а не `409`."""
    ctx = _zone_scene(stage)
    outlier = ctx.rows[3]
    _refused(ctx.put(admin_client, _body(outlier.id, "zone", _manual("2"))), 422, "conversion_not_needed")
    assert _no_row(ctx)


def test_ref_analogs_target_a_missing_conversion_is_refused_before_the_outlier_check(admin_client, stage) -> None:
    """Помеченная строка блока Г в другой единице: без пересчёта — `conversion_required`, а не `409`."""
    scene, ctx = _linked_set(stage)
    outlier = ctx.rows[3]
    _refused(ctx.put(admin_client, _body(outlier.id, "link")), 422, "conversion_required")
    assert _no_row(ctx)


def test_ref_analogs_target_an_inapplicable_link_is_refused_before_the_outlier_check(admin_client, stage) -> None:
    scene, ctx = _linked_set(stage)
    outlier = ctx.rows[3]
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), outlier.id))
    _refused(
        ctx.put(admin_client, _body(outlier.id, "link", {**seen, "link_id": 987654321})), 422, "link_not_applicable"
    )
    assert _no_row(ctx)


# ---------------------------------------------------------------------------
#  Пометка выброса (К3): на строке блока, строке поиска и строке блока Г
# ---------------------------------------------------------------------------


def test_ref_analogs_target_a_marked_block_a_row_needs_the_confirmation(admin_client, stage) -> None:
    """Критическое свойство К3, строка блока А: цена 1000 против 100 — выброс, отношение 10."""
    ctx = _zone_scene(stage)
    outlier = ctx.rows[3]
    detail = _refused(ctx.put(admin_client, _body(outlier.id, "zone")), 409, "outlier_confirmation_required")
    assert D(detail["ratio"]) == D("10")
    assert _no_row(ctx)
    assert ctx.put(admin_client, _body(outlier.id, "zone", confirm_outlier=True)).status_code == 204
    assert ctx.stored().analog_position_item_id == outlier.id


def test_ref_analogs_target_an_unmarked_row_of_the_same_set_needs_no_confirmation(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone")).status_code == 204


def test_ref_analogs_target_confirmation_of_an_unmarked_row_changes_nothing(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone", confirm_outlier=True)).status_code == 204


def test_ref_analogs_target_a_marked_search_row_needs_the_confirmation(admin_client, stage) -> None:
    """Критическое свойство К3, строка поиска: выброс собственной ступени строки КП. Блоков А и Г у строки
    нет — пометка приходит только тем путём, которым её видит поиск."""
    ctx = _own_scene(stage)
    panel = ctx.panel(admin_client)
    assert panel["line"]["state"] == "comparable"
    assert (panel["reference_zones"], panel["reference_links"]) == ([], [])
    found = {row["analog_id"]: row for row in ctx.search(admin_client, "Подготовка")["rows"]}
    outlier = ctx.rows[3]
    assert found[outlier.id]["outlier_ratio"] == "10"
    detail = _refused(ctx.put(admin_client, _body(outlier.id)), 409, "outlier_confirmation_required")
    assert detail["ratio"] == found[outlier.id]["outlier_ratio"]
    assert _no_row(ctx)
    assert ctx.put(admin_client, _body(outlier.id, confirm_outlier=True)).status_code == 204
    assert found[ctx.rows[0].id]["outlier_ratio"] is None
    assert ctx.put(admin_client, _body(ctx.rows[0].id)).status_code == 204


def test_ref_analogs_target_a_marked_link_group_row_needs_the_confirmation(admin_client, stage) -> None:
    """Строка блока Г с пересчётом по связи: отношение считается по ценам в единице КП."""
    scene, ctx = _linked_set(stage)
    outlier = ctx.rows[3]
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), outlier.id))
    body = _body(outlier.id, "link", seen)
    detail = _refused(ctx.put(admin_client, body), 409, "outlier_confirmation_required")
    assert D(detail["ratio"]) == D("10")
    assert ctx.put(admin_client, {**body, "confirm_outlier": True}).status_code == 204
    assert ctx.stored().ref_link_id == scene.link_id


# ---------------------------------------------------------------------------
#  Снимок
# ---------------------------------------------------------------------------


def test_ref_analogs_target_search_row_body_is_accepted_without_changes(admin_client, stage) -> None:
    """Тело, собранное из `conversion` строки поиска без изменений: цель записана, `ref_link_id` и
    `ref_factor` равны отданным поиском."""
    scene, ctx = _linked(stage)
    found = {row["analog_id"]: row for row in ctx.search(admin_client, "Подготовка")["rows"]}
    row = found[scene.ref_item.id]
    assert row["conversion_kind"] == "link"
    assert ctx.put(admin_client, _body(scene.ref_item.id, "search", _link_conversion(row))).status_code == 204
    stored = ctx.stored()
    assert stored.ref_link_id == row["conversion"]["link_id"] == scene.link_id
    assert stored.ref_factor == D(row["conversion"]["factor"]) == D("0.1")
    assert (stored.ref_method, stored.ref_basis, stored.ref_origin) == ("link", "толщина 100 мм", "search")


def test_ref_analogs_target_snapshot_without_a_conversion(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    stored = ctx.stored()
    assert tuple(stored) == ("reference", ctx.rows[0].id, "zone", None, None, None, None, True)


def test_ref_analogs_target_snapshot_of_a_link_target(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    stored = ctx.stored()
    assert stored.kind == "reference"
    assert (stored.ref_origin, stored.ref_method, stored.ref_link_id) == ("link", "link", scene.link_id)
    assert (stored.ref_factor, stored.ref_basis) == (D("0.1"), "толщина 100 мм")
    assert stored.ref_without_analogs is True


def test_ref_analogs_target_snapshot_of_a_manual_target_keeps_the_exact_factor(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    _save_ctx(admin_client, ctx, _body(other.id, "search", _manual("0,3333", "по паспорту")))
    stored = ctx.stored()
    assert (stored.ref_method, stored.ref_factor, stored.ref_basis, stored.ref_link_id) == (
        "manual", D("0.3333"), "по паспорту", None,
    )
    assert stored.ref_origin == "search"


def test_ref_analogs_target_snapshot_of_the_inverse_link_keeps_the_server_factor_to_the_last_digit(
    admin_client, stage
) -> None:
    """Строка договора в м³, строка КП в м²: `N = 1 / 0,3` не обрывается; в снимке ровно то, что отдала панель."""
    scene, ctx = _linked(stage, kp_unit="M2", ref_unit="M3", text="300 мм", price=D("5000"), kp_price=D("1200"))
    row = _panel_row(ctx.panel(admin_client), scene.ref_item.id)
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", _link_conversion(row)))
    assert ctx.stored().ref_factor == D(row["conversion"]["factor"])
    assert D(row["conversion"]["factor"]) > D("3.3333333333333333333333333")


def test_ref_analogs_target_without_analogs_is_false_when_the_kp_row_has_its_own_rung(admin_client, stage) -> None:
    ctx = _own_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    assert ctx.stored().ref_without_analogs is False


def test_ref_analogs_target_without_analogs_is_true_for_a_row_without_a_ladder(admin_client, stage) -> None:
    contract = stage.world.contract(rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(contract, vat_rate=D("0"))
    row = _row(stage, proposal, D("100"), title="Подготовка")
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"), variant=False)
    ctx = Ctx(stage, rnd, part, item, [row])
    assert ctx.offer_line(admin_client)["state"] == "no_variant"
    _save_ctx(admin_client, ctx, _body(row.id))
    assert ctx.stored().ref_without_analogs is True


@pytest.mark.parametrize("other", [{"kind": "freshest"}, {"kind": "analog", "analog_id": "own"}, {"kind": "contractor"}])
def test_ref_analogs_target_overwriting_with_another_kind_clears_the_snapshot(admin_client, stage, other) -> None:
    ctx = _own_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "search"))
    assert ctx.stored().ref_origin == "search"
    body = dict(other)
    if body.get("analog_id") == "own":
        body["analog_id"] = ctx.rows[1].id
    if body["kind"] == "contractor":
        # у строки нет договора подрядчика КП: «с этим подрядчиком» отказывает, цель-справка остаётся
        _refused(ctx.put(admin_client, body), 422, "no_contractor_analog")
        assert ctx.stored().kind == "reference"
        return
    assert ctx.put(admin_client, body).status_code == 204
    stored = ctx.stored()
    assert stored.kind == other["kind"]
    assert tuple(stored)[2:] == (None, None, None, None, None, None)


def test_ref_analogs_target_a_reference_overwrites_an_analog_choice(admin_client, stage) -> None:
    ctx = _own_scene(stage)
    assert ctx.put(admin_client, {"kind": "analog", "analog_id": ctx.rows[1].id}).status_code == 204
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    stored = ctx.stored()
    assert (stored.kind, stored.analog_position_item_id, stored.ref_origin) == ("reference", ctx.rows[0].id, "search")


def test_ref_analogs_target_a_second_reference_replaces_the_first_snapshot(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    _save_ctx(admin_client, ctx, _body(other.id, "search", _manual("2", "по справке")))
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    assert tuple(ctx.stored()) == ("reference", ctx.rows[0].id, "zone", None, None, None, None, True)


def test_ref_analogs_target_delete_removes_the_reference(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    assert ctx.delete(admin_client).status_code == 204
    assert _no_row(ctx)


def test_ref_analogs_target_unknown_body_shapes_are_422(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, {"kind": "bogus"}).status_code == 422
    assert ctx.put(admin_client, {"kind": "reference", "analog_id": ctx.rows[0].id}).status_code == 422
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "elsewhere")).status_code == 422
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone", {"method": "guess"})).status_code == 422
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone", {"method": "manual", "factor": "2"})).status_code == 422
    assert _no_row(ctx)


def _state_scene(stage, state) -> Ctx:
    """Одна строка КП в заданном состоянии и справочные строки договоров в её единице."""
    rows = [_contract_row(stage, D("100"), title="Подготовка")[1] for _ in range(2)]
    rnd, part = _kp_part(stage)
    if state == "comparable":
        item = stage.kp(part, D("144"))
    elif state == "unit_mismatch":
        item = stage.kp(part, D("840"))
    elif state == "no_history":
        family, _, _, variant = _family_with_param(stage, "Семья Я", "M2", "Толщина")
        item = _row(stage, part.proposal, D("144"), family=family, variant=variant)
    elif state == "no_variant":
        item = stage.kp(part, D("144"), variant=False)
    elif state == "no_zone":
        item = _row(stage, part.proposal, D("144"), chain=stage.world.chain(("Раздел Б",), None))
    else:
        assert state == "no_price"
        item = stage.kp(part, D("0"), total=D("0"))
    return Ctx(stage, rnd, part, item, rows)


@pytest.mark.parametrize("state", ["comparable", "no_history", "no_variant", "no_zone", "unit_mismatch", "no_price"])
def test_ref_analogs_target_is_accepted_in_every_state_of_the_line(admin_client, stage, state) -> None:
    ctx = _state_scene(stage, state)
    assert ctx.offer_line(admin_client)["state"] == state
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    line = ctx.offer_line(admin_client)
    assert line["state"] == state
    assert line["target_kind"] == "reference"
    assert (line["choice"]["kind"], line["choice"]["active"], line["choice"]["reset_reason"]) == ("reference", True, None)


# ---------------------------------------------------------------------------
#  Чтение: значение, карточка, подсказки
# ---------------------------------------------------------------------------


def _net(pair: dict) -> Decimal:
    return D(pair["net"])


def test_ref_analogs_target_the_card_of_a_link_target_carries_the_converted_value(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    panel = ctx.panel(admin_client)
    choice = panel["line"]["choice"]
    assert set(choice) == {
        "kind", "analog_id", "origin", "conversion", "active", "reset_reason", "hints", "reference", "set_by", "set_at",
    }
    assert (choice["kind"], choice["analog_id"], choice["active"], choice["reset_reason"], choice["origin"]) == (
        "reference", scene.ref_item.id, True, None, "link",
    )
    assert choice["conversion"] == {"method": "link", "factor": seen["expected_factor"], "basis": "толщина 100 мм", "link_id": scene.link_id}
    assert choice["hints"] == {"link_now": None, "link_gone": False, "own_analogs": False}
    assert choice["set_by"]["id"] == admin_client.user.id
    reference = choice["reference"]
    assert reference["analog_id"] == scene.ref_item.id
    assert _net(reference["price"]) == D("1107.24")
    assert _net(reference["kp_unit_price"]) == D("11072.4")
    assert reference["unit"] == "м²"
    assert reference["contract"]["id"] == scene.contract.id
    assert reference["conversion"] == choice["conversion"]
    assert reference["param_value"] == "100 мм"
    assert panel["line"]["target_kind"] == "reference"


def test_ref_analogs_target_the_offer_card_equals_the_panel_card(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    line = ctx.offer_line(admin_client)
    assert line["choice"] == ctx.panel(admin_client)["line"]["choice"]
    assert line["target_kind"] == "reference"
    assert line["choice"]["reference"] is not None


def test_ref_analogs_target_without_a_conversion_the_value_is_the_price_divided_by_one(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert choice["conversion"] is None
    assert _net(choice["reference"]["kp_unit_price"]) == D("100")
    assert choice["origin"] == "zone"


def test_ref_analogs_target_a_row_outside_the_blocks_still_has_its_row_in_the_card(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    other = _other_unit_row(stage)
    _save_ctx(admin_client, ctx, _body(other.id, "search", _manual("2", "по справке")))
    panel = ctx.panel(admin_client)
    assert other.id not in {r["analog_id"] for g in panel["reference_zones"] for r in g["rows"]}
    reference = panel["line"]["choice"]["reference"]
    assert reference["analog_id"] == other.id
    assert reference["conversion"] == {"method": "manual", "factor": "2", "basis": "по справке"}
    assert _net(reference["kp_unit_price"]) == D("350")
    assert reference["outlier_ratio"] is None
    assert ctx.offer_line(admin_client)["choice"]["reference"] == reference


def test_ref_analogs_target_the_card_shows_the_mark_by_the_current_calculation(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    outlier = ctx.rows[3]
    _save_ctx(admin_client, ctx, _body(outlier.id, "zone", confirm_outlier=True))
    reference = ctx.panel(admin_client)["line"]["choice"]["reference"]
    assert reference["outlier_ratio"] == "10"
    assert ctx.offer_line(admin_client)["choice"]["reference"]["outlier_ratio"] == "10"


def test_ref_analogs_target_edited_parameter_value_does_not_change_the_value_but_gives_the_hint(
    admin_client, stage
) -> None:
    """Критическое свойство К4: значение параметра 100 → 200 мм после выбора; цифра и `ref_factor` прежние,
    подсказка `link_now` с `N′ = 0,2`."""
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    _set_text(stage, scene, "200 мм")
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert _net(choice["reference"]["kp_unit_price"]) == D("11072.4")
    assert D(choice["conversion"]["factor"]) == D("0.1")
    assert D(choice["hints"]["link_now"]["factor"]) == D("0.2")
    assert choice["hints"]["link_now"]["basis"] == "толщина 200 мм"
    assert choice["hints"]["link_gone"] is False
    assert ctx.stored().ref_factor == D("0.1")
    assert ctx.offer_line(admin_client)["choice"]["hints"] == choice["hints"]


def test_ref_analogs_target_edited_constant_link_gives_the_hint_and_keeps_the_value(admin_client, stage, db_session) -> None:
    """Постоянная связь: правка `factor` 0,1 → 0,2 цифру не меняет, подсказка `link_now` с `N′`."""
    from models import FamilyLink

    kp_family, _, _, kp_variant = _family_with_param(stage, "Семья К", "M3", "Толщина")
    ref_family, _, _, ref_variant = _family_with_param(stage, "Семья Т", "T", "Толщина")
    contract, ref_item = _contract_row(
        stage, D("1000"), family=ref_family, variant=ref_variant, unit="T", title="Подготовка"
    )
    rnd, part = _kp_part(stage)
    kp_item = _row(stage, part.proposal, D("1500"), family=kp_family, variant=kp_variant, unit="M3")
    link_id = create_link(
        db_session,
        body=LinkInput(
            family_a_id=kp_family.id, family_b_id=ref_family.id, kind="constant",
            factor_from_family_id=ref_family.id, factor="0.1", basis="по паспорту",
        ),
        actor_id=stage.world.admin.id,
    )
    ctx = Ctx(stage, rnd, part, kp_item, [ref_item])
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), ref_item.id))
    assert D(seen["expected_factor"]) == D("0.1")
    _save_ctx(admin_client, ctx, _body(ref_item.id, "link", seen))
    db_session.execute(sa.update(FamilyLink).where(FamilyLink.id == link_id).values(factor=D("0.2")))
    db_session.flush()
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert _net(choice["reference"]["kp_unit_price"]) == D("10000")
    assert D(choice["hints"]["link_now"]["factor"]) == D("0.2")
    assert choice["hints"]["link_now"]["basis"] == "по паспорту"
    assert ctx.stored().ref_factor == D("0.1")


def test_ref_analogs_target_deleted_link_does_not_change_the_value_and_gives_link_gone(admin_client, stage, db_session) -> None:
    """Критическое свойство К4: связь удалена — цифра и `ref_factor` прежние, `ref_link_id` пуст."""
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    delete_link(db_session, scene.link_id)
    db_session.flush()
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert _net(choice["reference"]["kp_unit_price"]) == D("11072.4")
    assert choice["hints"] == {"link_now": None, "link_gone": True, "own_analogs": False}
    assert choice["conversion"]["link_id"] is None
    stored = ctx.stored()
    assert (stored.ref_link_id, stored.ref_factor, stored.ref_method) == (None, D("0.1"), "link")


def test_ref_analogs_target_a_manual_target_never_gets_a_link_hint(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "search", _manual("0,25", "по справке")))
    _set_text(stage, scene, "200 мм")
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert choice["hints"] == {"link_now": None, "link_gone": False, "own_analogs": False}
    assert _net(choice["reference"]["kp_unit_price"]) == D("4428.96")


def test_ref_analogs_target_own_analogs_hint_appears_with_an_own_rung(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    assert ctx.panel(admin_client)["line"]["choice"]["hints"]["own_analogs"] is False
    _contract_row(stage, D("120"), title="Подготовка")
    _contract_row(stage, D("130"), title="Подготовка")
    panel = ctx.panel(admin_client)
    assert panel["line"]["state"] == "comparable"
    choice = panel["line"]["choice"]
    assert choice["hints"]["own_analogs"] is True
    assert (choice["active"], panel["line"]["target_kind"]) == (True, "reference")


def test_ref_analogs_target_a_target_chosen_with_own_analogs_gets_no_own_analogs_hint(admin_client, stage) -> None:
    ctx = _own_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    assert ctx.panel(admin_client)["line"]["choice"]["hints"]["own_analogs"] is False


def test_ref_analogs_target_the_state_of_the_line_never_resets_the_target(admin_client, stage) -> None:
    """Строка была `no_history` и стала `comparable`: справочная цель действует."""
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    assert ctx.offer_line(admin_client)["state"] == "no_history"
    _contract_row(stage, D("120"), title="Подготовка")
    _contract_row(stage, D("130"), title="Подготовка")
    line = ctx.offer_line(admin_client)
    assert line["state"] == "comparable"
    assert (line["choice"]["active"], line["choice"]["reset_reason"], line["target_kind"]) == (True, None, "reference")


def test_ref_analogs_target_a_row_that_became_a_system_resets_the_target(admin_client, stage, db_session) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    db_session.execute(
        sa.update(CatalogContext).where(CatalogContext.id == ctx.item.context_id).values(semantic_kind="SYSTEM")
    )
    db_session.flush()
    choice = ctx.offer_line(admin_client)["choice"]
    assert (choice["kind"], choice["active"], choice["reset_reason"]) == ("reference", False, "kind_changed")
    assert choice["reference"] is None
    assert ctx.stored() is not None


def test_ref_analogs_target_a_replaced_estimate_resets_the_target(admin_client, stage) -> None:
    """Критическое свойство К5: смета договора заменена допсоглашением — строка перестала быть справочной;
    запись остаётся, цель — как без выбора."""
    chain = _underground(stage)
    contract, row = _contract_row(stage, D("100"), chain=chain, title="Подготовка")
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"))
    ctx = Ctx(stage, rnd, part, item, [row])
    _save_ctx(admin_client, ctx, _body(row.id, "zone"))
    assert ctx.offer_line(admin_client)["choice"]["active"] is True

    _amend(stage, contract, chain=chain)
    line = ctx.offer_line(admin_client)
    assert (line["choice"]["active"], line["choice"]["reset_reason"]) == (False, "reference_unavailable")
    assert line["choice"]["reference"] is None
    assert line["target_kind"] is None
    assert ctx.stored() is not None
    panel = ctx.panel(admin_client)
    assert panel["line"]["choice"] == line["choice"]


def test_ref_analogs_target_a_reset_comparable_row_goes_back_to_the_lowest(admin_client, stage) -> None:
    contract, row = _contract_row(stage, D("100"), title="Подготовка")
    rnd, part = _kp_part(stage)
    ctx = Ctx(stage, rnd, part, stage.kp(part, D("144")), [row])
    _save_ctx(admin_client, ctx, _body(row.id))
    assert ctx.offer_line(admin_client)["target_kind"] == "reference"
    _amend(stage, contract, prices=("100", "110"))
    line = ctx.offer_line(admin_client)
    assert line["state"] == "comparable"
    assert (line["choice"]["active"], line["choice"]["reset_reason"], line["target_kind"]) == (
        False, "reference_unavailable", "lowest",
    )


def test_ref_analogs_target_agreed_is_stronger_than_the_reference(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    assert ctx.offer_line(admin_client)["target_kind"] == "reference"
    response = admin_client.put(f"{ctx.url}/agreed", json={"value": "150", "basis": "net"})
    assert response.status_code == 204, response.text
    line = ctx.offer_line(admin_client)
    assert line["target_kind"] == "agreed"
    assert (line["choice"]["kind"], line["choice"]["active"]) == ("reference", True)


def test_ref_analogs_target_the_choice_of_a_ladder_target_keeps_its_old_shape(admin_client, stage) -> None:
    ctx = _own_scene(stage)
    assert ctx.put(admin_client, {"kind": "freshest"}).status_code == 204
    choice = ctx.offer_line(admin_client)["choice"]
    assert set(choice) == {"kind", "analog_id", "active", "reset_reason", "set_by", "set_at"}
    assert ctx.offer_line(admin_client)["target_kind"] == "freshest"


def test_ref_analogs_target_the_value_takes_the_read_inflation_series(admin_client, stage, db_session) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
    ctx = Ctx(stage, rnd, part, item, [])
    (analog,) = ctx.panel(admin_client, inflation_series_id=series_id)["analogs"]
    adjusted = _net(analog["price"])
    assert adjusted != _net(analog["nominal"]) == D("100")
    response = ctx.put(admin_client, _body(analog["analog_id"]), inflation_series_id=series_id)
    assert response.status_code == 204, response.text
    with_series = ctx.panel(admin_client, inflation_series_id=series_id)["line"]["choice"]["reference"]
    assert _net(with_series["kp_unit_price"]) == adjusted
    without = ctx.panel(admin_client)["line"]["choice"]["reference"]
    assert _net(without["kp_unit_price"]) == D("100")
    assert ctx.stored().ref_factor is None


# ---------------------------------------------------------------------------
#  Справочная цель не входит ни в какие суммы (К1)
# ---------------------------------------------------------------------------


_TARGET_FIELDS = ("choice", "target_kind")


def _without_target(line: dict) -> dict:
    return {key: value for key, value in line.items() if key not in _TARGET_FIELDS}


def _offer_without_target(body: dict, item) -> dict:
    return {**body, "lines": [_without_target(line) if line["position_item_id"] == item.id else line for line in body["lines"]]}


def test_ref_analogs_target_changes_no_offer_total_or_line_figure_of_a_comparable_row(admin_client, stage) -> None:
    """Критическое свойство К1: разбор оффера со справочной целью и без неё равен целиком, кроме `choice` и
    `target_kind` самой строки — итоги, `to_lowest`, `to_freshest`, вилка, состояния, корзины."""
    ctx = _own_scene(stage)
    before = ctx.offer(admin_client)
    panel_before = ctx.panel(admin_client)
    round_before = _round_ok(admin_client, stage.tender, ctx.rnd)
    _save_ctx(admin_client, ctx, _body(ctx.rows[3].id, confirm_outlier=True))
    after = ctx.offer(admin_client)
    assert _line_of(after, ctx.item)["target_kind"] == "reference"
    assert _line_of(before, ctx.item)["target_kind"] == "lowest"
    assert _offer_without_target(after, ctx.item) == _offer_without_target(before, ctx.item)
    assert _round_ok(admin_client, stage.tender, ctx.rnd) == round_before
    line = _line_of(after, ctx.item)
    assert line["to_lowest"] is not None and line["lowest"] is not None
    # цифра цели отлична от самой низкой и самой свежей: подмешай её в сумму — равенство выше разошлось бы
    reference = _net(line["choice"]["reference"]["kp_unit_price"])
    assert reference == D("1000")
    assert reference not in (_net(line["lowest"]["value"]), _net(line["freshest"]["value"]))
    # панель: те же цифры, кроме полей цели и того, что строит файл (цель файла — отдельное правило)
    panel_after = ctx.panel(admin_client)

    def figures(panel: dict) -> dict:
        return {
            **{key: value for key, value in panel.items() if key not in ("line", "comment")},
            "line": {k: v for k, v in panel["line"].items() if k not in (*_TARGET_FIELDS, "file_ours")},
        }

    assert figures(panel_after) == figures(panel_before)


def test_ref_analogs_target_changes_nothing_in_the_panel_figures_of_a_row_without_analogs(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    before = ctx.panel(admin_client)
    offer_before = ctx.offer(admin_client)
    seen = _link_conversion(_panel_row(before, scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    after = ctx.panel(admin_client)
    assert _offer_without_target(ctx.offer(admin_client), ctx.item) == _offer_without_target(offer_before, ctx.item)

    def figures(panel: dict) -> dict:
        return {
            **{key: value for key, value in panel.items() if key not in ("line", "comment")},
            "line": {k: v for k, v in panel["line"].items() if k not in (*_TARGET_FIELDS, "file_ours")},
        }

    assert figures(after) == figures(before)


# ---------------------------------------------------------------------------
#  Права (К7)
# ---------------------------------------------------------------------------


def _member(admin_client, factories):
    member = factories.UserFactory.create(role=UserRole.member)
    admin_client.set_user(member)
    return member


def test_ref_analogs_target_a_member_cannot_put_a_reference_target(admin_client, stage, factories) -> None:
    ctx = _zone_scene(stage)
    _member(admin_client, factories)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone")).status_code == 403
    assert _no_row(ctx)


def test_ref_analogs_target_a_member_cannot_delete_a_target(admin_client, stage, factories) -> None:
    ctx = _zone_scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id, "zone"))
    _member(admin_client, factories)
    assert ctx.delete(admin_client).status_code == 403
    assert ctx.stored() is not None


def test_ref_analogs_target_an_admin_puts_and_deletes(admin_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(admin_client, _body(ctx.rows[0].id, "zone")).status_code == 204
    assert ctx.delete(admin_client).status_code == 204


def test_ref_analogs_target_a_member_reads_the_panel_the_search_and_the_card(admin_client, stage, factories) -> None:
    scene, ctx = _linked(stage)
    seen = _link_conversion(_panel_row(ctx.panel(admin_client), scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    _member(admin_client, factories)
    panel = ctx.panel(admin_client)
    assert panel["line"]["choice"]["reference"]["analog_id"] == scene.ref_item.id
    assert ctx.search(admin_client, "Подготовка")["rows"]
    assert ctx.offer_line(admin_client)["target_kind"] == "reference"


def test_ref_analogs_target_an_anonymous_client_gets_401(anon_client, stage) -> None:
    ctx = _zone_scene(stage)
    assert ctx.put(anon_client, _body(ctx.rows[0].id, "zone")).status_code == 401
    assert ctx.delete(anon_client).status_code == 401
    assert _no_row(ctx)


# ---------------------------------------------------------------------------
#  Сквозной К9: единица связанной семьи сменилась
# ---------------------------------------------------------------------------


def test_ref_analogs_target_changed_unit_of_the_linked_family_keeps_the_target(admin_client, stage, db_session) -> None:
    """Критическое свойство К9: цель по связи плотности сохранена, затем единица семьи `F'` т → кг. Блока Г
    в панели нет, связь `units_changed`, значение цели и `ref_factor` прежние, подсказка `link_gone`."""
    scene, ctx = _linked(stage, kp_unit="M3", ref_unit="т", kind="density", text="2,4 т/м3", price=D("1000"), kp_price=D("2400"))
    row = _panel_row(ctx.panel(admin_client), scene.ref_item.id)
    assert D(row["conversion"]["factor"]) > D("0.41666")
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", _link_conversion(row)))
    before = ctx.panel(admin_client)["line"]["choice"]
    assert _net(before["reference"]["kp_unit_price"]) == D("2400")
    stored_factor = ctx.stored().ref_factor

    _change_unit(db_session, scene.ref_family, "кг")
    db_session.commit()
    panel = ctx.panel(admin_client)
    assert panel["reference_links"] == []
    response = admin_client.get(f"/api/v1/semantic/families/{scene.ref_family.id}/links")
    assert response.status_code == 200, response.text
    (link,) = response.json()["items"]
    assert (link["state"], link["inactive_reason"]) == ("inactive", "units_changed")
    choice = panel["line"]["choice"]
    assert _net(choice["reference"]["kp_unit_price"]) == D("2400")
    assert choice["hints"] == {"link_now": None, "link_gone": True, "own_analogs": False}
    assert choice["active"] is True
    assert ctx.stored().ref_factor == stored_factor
    assert ctx.stored().ref_link_id == scene.link_id


# ---------------------------------------------------------------------------
#  Число запросов не растёт с числом справочных целей (спека §2.20)
# ---------------------------------------------------------------------------


def _store_link_target(db, stage, scene, item) -> None:
    """Справочная цель по связи в обход маршрута: снимок такой же, какой записал бы `PUT`."""
    db.add(
        KpLineTarget(
            position_item_id=item.id,
            kind="reference",
            analog_position_item_id=scene.ref_item.id,
            set_by=stage.world.admin.id,
            ref_origin="link",
            ref_method="link",
            ref_factor=D("0.1"),
            ref_basis="толщина 100 мм",
            ref_link_id=scene.link_id,
            ref_without_analogs=True,
        )
    )
    db.flush()


def _linked_lines(stage, count):
    scene, ctx = _linked(stage)
    items = [scene.kp_item]
    for index in range(count - 1):
        items.append(
            _row(
                stage, scene.part.proposal, D("14400"), family=scene.kp_family, variant=scene.kp_variant,
                unit="M3", title=f"Работа КП {index}",
            )
        )
    for item in items:
        _set_value(stage, item, scene.kp_schema, scene.kp_param, "50 мм")
    stage.db.flush()
    return scene, items


def test_ref_analogs_target_offer_view_statements_do_not_grow_with_the_number_of_targets(
    admin_client, stage, db_session
) -> None:
    scene, items = _linked_lines(stage, 5)
    ctx = Ctx(stage, scene.rnd, scene.part, items[0], [scene.ref_item])
    _store_link_target(db_session, stage, scene, items[0])
    ctx.offer(admin_client)  # прогрев кэшей процесса
    with count_statements(db_session) as one:
        body = ctx.offer(admin_client)
    assert sum(1 for line in body["lines"] if line["target_kind"] == "reference") == 1
    for item in items[1:4]:
        _store_link_target(db_session, stage, scene, item)
    with count_statements(db_session) as four:
        body = ctx.offer(admin_client)
    assert sum(1 for line in body["lines"] if line["target_kind"] == "reference") == 4
    assert all(line["choice"]["hints"] == {"link_now": None, "link_gone": False, "own_analogs": False}
               for line in body["lines"] if line["target_kind"] == "reference")
    assert len(one) == len(four)


def test_ref_analogs_target_panel_statements_do_not_depend_on_targets_of_other_lines(
    admin_client, stage, db_session
) -> None:
    scene, items = _linked_lines(stage, 5)
    ctx = Ctx(stage, scene.rnd, scene.part, items[4], [scene.ref_item])
    _store_link_target(db_session, stage, scene, items[0])
    ctx.panel(admin_client)
    with count_statements(db_session) as one:
        ctx.panel(admin_client)
    for item in items[1:4]:
        _store_link_target(db_session, stage, scene, item)
    with count_statements(db_session) as four:
        panel = ctx.panel(admin_client)
    assert panel["line"]["choice"] is None
    assert len(one) == len(four)


def test_ref_analogs_target_panel_of_a_target_line_does_not_grow_with_other_targets(
    admin_client, stage, db_session
) -> None:
    scene, items = _linked_lines(stage, 5)
    ctx = Ctx(stage, scene.rnd, scene.part, items[0], [scene.ref_item])
    _store_link_target(db_session, stage, scene, items[0])
    ctx.panel(admin_client)
    with count_statements(db_session) as one:
        first = ctx.panel(admin_client)
    for item in items[1:4]:
        _store_link_target(db_session, stage, scene, item)
    with count_statements(db_session) as four:
        second = ctx.panel(admin_client)
    assert first["line"]["choice"] == second["line"]["choice"]
    assert len(one) == len(four)


def test_ref_analogs_target_a_link_missing_from_the_loaded_ones_is_gone(admin_client, stage, db_session) -> None:
    """Связь снимка существует, но это связь других семей: среди загруженных её нет."""
    scene, ctx = _linked(stage)
    third, _, third_param, _ = _family_with_param(stage, "Семья Х", "M3", "Толщина слоя")
    fourth, _, fourth_param, _ = _family_with_param(stage, "Семья Ю", "M2", "Толщина слоя")
    foreign = create_link(
        db_session,
        body=LinkInput(
            family_a_id=third.id, family_b_id=fourth.id, kind="thickness",
            param_a_id=third_param.id, param_b_id=fourth_param.id,
        ),
        actor_id=stage.world.admin.id,
    )
    db_session.add(
        KpLineTarget(
            position_item_id=scene.kp_item.id, kind="reference", analog_position_item_id=scene.ref_item.id,
            set_by=stage.world.admin.id, ref_origin="link", ref_method="link", ref_factor=D("0.1"),
            ref_basis="толщина 100 мм", ref_link_id=foreign, ref_without_analogs=True,
        )
    )
    db_session.flush()
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert choice["hints"] == {"link_now": None, "link_gone": True, "own_analogs": False}
    assert _net(choice["reference"]["kp_unit_price"]) == D("11072.4")


# ---------------------------------------------------------------------------
#  Семьи строк для «Запомнить для семей»
# ---------------------------------------------------------------------------


def test_ref_analogs_target_rows_and_the_line_carry_their_families(admin_client, stage) -> None:
    scene, ctx = _linked(stage)
    panel = ctx.panel(admin_client)
    assert panel["line"]["family_id"] == scene.kp_family.id
    assert _panel_row(panel, scene.ref_item.id)["family_id"] == scene.ref_family.id
    found = {row["analog_id"]: row for row in ctx.search(admin_client, "Подготовка")["rows"]}
    assert found[scene.ref_item.id]["family_id"] == scene.ref_family.id
    seen = _link_conversion(_panel_row(panel, scene.ref_item.id))
    _save_ctx(admin_client, ctx, _body(scene.ref_item.id, "link", seen))
    choice = ctx.panel(admin_client)["line"]["choice"]
    assert choice["reference"]["family_id"] == scene.ref_family.id
    assert ctx.offer_line(admin_client)["choice"]["reference"]["family_id"] == scene.ref_family.id


def test_ref_analogs_target_a_zone_row_carries_the_family_of_the_line_and_a_row_without_one_is_null(
    admin_client, stage
) -> None:
    ctx = _zone_scene(stage)
    panel = ctx.panel(admin_client)
    zone_rows = [row for group in panel["reference_zones"] for row in group["rows"]]
    assert zone_rows
    assert {row["family_id"] for row in zone_rows} == {panel["line"]["family_id"]} != {None}
    bare = _row(stage, stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))[1], D("100"), title="Подготовка без семьи")
    stage.db.execute(sa.delete(ContextMember).where(ContextMember.position_item_id == bare.id))
    stage.db.flush()
    found = {row["analog_id"]: row for row in ctx.search(admin_client, "без семьи")["rows"]}
    assert found[bare.id]["family_id"] is None
    no_family = Ctx(stage, ctx.rnd, ctx.part, stage.kp(ctx.part, D("144"), variant=False), [])
    assert no_family.panel(admin_client)["line"]["family_id"] is None

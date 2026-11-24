"""«Не аналог»: цели, файл подрядчику, комментарий и поиск (спека «Не аналог» §5 п. 1, 3; §6; §7 п. 3, 4; §9 п. 8, 9, 14).

Сцены строятся помощниками справочной цели и панели; названия и числа выдуманы. Ставки истории — 0 %, ставка КП
— 20 %: цена 100 нетто даёт 120 с НДС. Отметки ставятся МАРШРУТАМИ (`PUT`) от `admin`. Эталоны — литералы.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.integration.test_kp_file_export import _num, _xlsx_line
from tests.integration.test_not_analog_panel import (
    TITLE,
    _clear_price_error,
    _history,
    _kp_line,
    _mark_price_error,
    _mark_tender,
)
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _pair
from tests.integration.test_our_price_round_api import Stage
from tests.integration.test_ref_analogs_file import _preview
from tests.integration.test_ref_analogs_file import _row as _file_row
from tests.integration.test_ref_analogs_panel import _kp_part, _row
from tests.integration.test_ref_analogs_search import _kp_scene, _search
from tests.integration.test_ref_analogs_target import _body, _own_scene, _refused, _save_ctx

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def admin_client(admin_client, db_session, tmp_storage):
    """Клиент `admin`, закрепляющий сцену перед запросом и пишущий в каталог теста."""
    from main import app
    from storage import get_storage

    send = admin_client.request

    def request(*args, **kwargs):
        Session.commit(db_session)
        return send(*args, **kwargs)

    admin_client.request = request
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    return admin_client


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _tender_mark(client, ctx, contract_item, reason="не тот материал") -> None:
    _mark_tender(client, ctx.stage, ctx.rnd, ctx.part, ctx.item, contract_item, reason)


def _clear_tender(client, ctx, contract_item) -> None:
    ctx.stage.db.flush()
    response = client.delete(f"{ctx.url}/not-analog/{contract_item.id}")
    assert response.status_code == 204, response.text


def _surfaces(client, ctx) -> dict:
    """Наша цена строки на трёх поверхностях и тексты комментария, как их отдают маршруты."""
    panel = ctx.panel(client)
    preview = _file_row(_preview(client, ctx), ctx.item)
    xlsx = _xlsx_line(client, ctx)
    return {
        "choice": panel["line"]["choice"],
        "panel_ours": _pair(panel["line"]["file_ours"])[1],
        "panel_comment": panel["comment"]["template_text"],
        "preview_ours": D(preview["ours"]) if preview["ours"] is not None else None,
        "preview_comment": preview["comment"],
        "xlsx_ours": _num(xlsx["Наша расценка за ед."]),
        "xlsx_comment": xlsx["Комментарий"],
    }


def _assert_one_price(seen: dict, price: Decimal) -> None:
    assert seen["panel_ours"] == seen["preview_ours"] == seen["xlsx_ours"] == price
    assert seen["panel_comment"] == seen["preview_comment"] == seen["xlsx_comment"]
    assert f"{price:.2f}".replace(".", ",") in seen["panel_comment"]


def _scene(stage):
    """Строка КП с четырьмя аналогами своей ступени: 100, 100, 100 и самая низкая 90 (108 с НДС)."""
    return _own_scene(stage, prices=("100", "100", "100", "90"))


# ---------------------------------------------------------------------------
#  Одна цена на трёх поверхностях (спека §9 п. 9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["price_error", "tender"])
def test_not_analog_targets_a_reference_target_on_a_marked_row_gives_one_price_on_three_surfaces(
    admin_client, stage, kind
) -> None:
    ctx = _scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    before = _surfaces(admin_client, ctx)
    assert before["choice"]["active"] is True and before["choice"]["reset_reason"] is None
    _assert_one_price(before, D("120"))

    if kind == "price_error":
        _mark_price_error(admin_client, stage, ctx.rows[0])
    else:
        _tender_mark(admin_client, ctx, ctx.rows[0])
    marked = _surfaces(admin_client, ctx)

    assert marked["choice"]["kind"] == "reference" and marked["choice"]["analog_id"] == ctx.rows[0].id
    assert marked["choice"]["active"] is False and marked["choice"]["reset_reason"] == "excluded"
    _assert_one_price(marked, D("108"))
    assert ctx.stored() is not None  # запись цели остаётся

    if kind == "price_error":
        _clear_price_error(admin_client, stage, ctx.rows[0])
    else:
        _clear_tender(admin_client, ctx, ctx.rows[0])
    after = _surfaces(admin_client, ctx)

    assert after["choice"]["active"] is True and after["choice"]["reset_reason"] is None
    _assert_one_price(after, D("120"))


@pytest.mark.parametrize("kind", ["price_error", "tender"])
def test_not_analog_targets_a_comparable_row_with_an_analog_target_on_a_marked_row_takes_the_lowest_of_the_rest(
    admin_client, stage, kind
) -> None:
    """Цель `analog` на строку, которую отметили, а другие аналоги есть: экран — `excluded`; файл — самая низкая
    из оставшихся (90 нетто, 108 с НДС)."""
    ctx = _scene(stage)
    assert ctx.put(admin_client, {"kind": "analog", "analog_id": ctx.rows[0].id}).status_code == 204
    _assert_one_price(_surfaces(admin_client, ctx), D("120"))

    if kind == "price_error":
        _mark_price_error(admin_client, stage, ctx.rows[0])
    else:
        _tender_mark(admin_client, ctx, ctx.rows[0])
    seen = _surfaces(admin_client, ctx)

    assert seen["choice"]["active"] is False and seen["choice"]["reset_reason"] == "excluded"
    _assert_one_price(seen, D("108"))


def test_not_analog_targets_a_tender_mark_of_another_form_does_not_reset_the_target(admin_client, stage) -> None:
    ctx = _scene(stage)
    other = _kp_line(stage, ctx.part, title="Совсем другая работа")
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))

    _mark_tender(admin_client, stage, ctx.rnd, ctx.part, other, ctx.rows[0])
    seen = _surfaces(admin_client, ctx)

    assert seen["choice"]["active"] is True and seen["choice"]["reset_reason"] is None
    _assert_one_price(seen, D("120"))


def test_not_analog_targets_a_mark_on_another_row_does_not_reset_the_target(admin_client, stage) -> None:
    ctx = _scene(stage)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))

    _mark_price_error(admin_client, stage, ctx.rows[1])
    seen = _surfaces(admin_client, ctx)

    assert seen["choice"]["active"] is True
    _assert_one_price(seen, D("120"))


# ---------------------------------------------------------------------------
#  Запись справочной цели (спека §6 п. 3)
# ---------------------------------------------------------------------------


def test_not_analog_targets_put_reference_target_refuses_a_price_error_row(admin_client, stage) -> None:
    ctx = _scene(stage)
    _mark_price_error(admin_client, stage, ctx.rows[0])

    detail = _refused(ctx.put(admin_client, _body(ctx.rows[0].id)), 422, "reference_is_excluded")

    assert detail["message"]
    assert ctx.stored() is None


def test_not_analog_targets_put_reference_target_refuses_a_row_of_the_tender_marks_of_this_line(
    admin_client, stage
) -> None:
    ctx = _scene(stage)
    _tender_mark(admin_client, ctx, ctx.rows[0])

    _refused(ctx.put(admin_client, _body(ctx.rows[0].id)), 422, "reference_is_excluded")

    assert ctx.stored() is None


def test_not_analog_targets_put_reference_target_checks_the_mark_before_the_conversion(admin_client, stage) -> None:
    """Единицы равны, а пересчёт передан — без отметки отказ `conversion_not_needed`; с отметкой раньше него
    `reference_is_excluded`."""
    ctx = _scene(stage)
    manual = {"method": "manual", "factor": "2", "basis": "по справке"}
    _refused(ctx.put(admin_client, _body(ctx.rows[0].id, conversion=manual)), 422, "conversion_not_needed")

    _mark_price_error(admin_client, stage, ctx.rows[0])

    _refused(ctx.put(admin_client, _body(ctx.rows[0].id, conversion=manual)), 422, "reference_is_excluded")


def test_not_analog_targets_put_reference_target_ignores_a_tender_mark_of_another_form(admin_client, stage) -> None:
    ctx = _scene(stage)
    other = _kp_line(stage, ctx.part, title="Совсем другая работа")
    _mark_tender(admin_client, stage, ctx.rnd, ctx.part, other, ctx.rows[0])

    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))

    assert ctx.stored() is not None


def test_not_analog_targets_put_reference_target_accepts_the_row_after_the_mark_is_cleared(admin_client, stage) -> None:
    ctx = _scene(stage)
    _mark_price_error(admin_client, stage, ctx.rows[0])
    _refused(ctx.put(admin_client, _body(ctx.rows[0].id)), 422, "reference_is_excluded")

    _clear_price_error(admin_client, stage, ctx.rows[0])

    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))


# ---------------------------------------------------------------------------
#  Поиск: метки и свёртка (спека §7 п. 4, §9 п. 14)
# ---------------------------------------------------------------------------


def _search_ids(body: dict) -> list[int]:
    return [row["analog_id"] for row in body["rows"]]


def test_not_analog_targets_search_rows_carry_the_marks_that_apply_to_the_kp_line_only(admin_client, stage) -> None:
    rows = _history(stage, ("100", "120", "140"))
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part, title=TITLE)
    kp_other = _kp_line(stage, part, title="Совсем другая работа")
    before = _search(admin_client, stage, rnd, part, kp, "Подготовка")
    assert all(row["marks"] == [] for row in before["rows"])

    _mark_tender(admin_client, stage, rnd, part, kp, rows[0], "тендерная")
    _mark_tender(admin_client, stage, rnd, part, kp_other, rows[1], "чужая форма")
    _mark_price_error(admin_client, stage, rows[2], "ошибка цены")
    body = _search(admin_client, stage, rnd, part, kp, "Подготовка")

    marks = {row["analog_id"]: row["marks"] for row in body["rows"]}
    assert [(m["kind"], m["reason"]) for m in marks[rows[0].id]] == [("tender", "тендерная")]
    assert marks[rows[1].id] == []
    assert [(m["kind"], m["reason"]) for m in marks[rows[2].id]] == [("price_error", "ошибка цены")]
    (mark,) = marks[rows[0].id]
    assert mark["set_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
    assert isinstance(mark["set_at"], str)
    # состав, порядок и счётчики прежние
    assert _search_ids(body) == _search_ids(before)
    assert (body["total_lines"], body["truncated"]) == (before["total_lines"], before["truncated"])

    _mark_price_error(admin_client, stage, rows[0], "и ошибка цены")
    both = {row["analog_id"]: row["marks"] for row in _search(admin_client, stage, rnd, part, kp, "Подготовка")["rows"]}
    assert [m["kind"] for m in both[rows[0].id]] == ["price_error", "tender"]  # «ошибка в цене» первой


def test_not_analog_targets_search_rows_of_a_kp_line_with_the_other_form_key_see_no_tender_mark(
    admin_client, stage
) -> None:
    rows = _history(stage, ("100",))
    rnd, part = _kp_part(stage)
    kp = _kp_line(stage, part, title=TITLE)
    kp_other = _kp_line(stage, part, title="Совсем другая работа")
    _mark_tender(admin_client, stage, rnd, part, kp, rows[0])

    body = _search(admin_client, stage, rnd, part, kp_other, "Подготовка")

    assert [row["marks"] for row in body["rows"]] == [[]]


def test_not_analog_targets_search_does_not_fold_a_marked_row_with_its_twin(admin_client, stage) -> None:
    """Две строки одного договора с равными наименованием, ценой и зоной сворачиваются; отмеченная остаётся
    отдельной строкой выдачи, чтобы метка не досталась представителю и не ушла со скрытой строкой."""
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    first = _row(stage, proposal, D("1000"), title="Бетон", cat="R")
    twin = _row(stage, proposal, D("1000"), title="Бетон", cat="R2")
    rnd, part, item = _kp_scene(stage)
    folded = _search(admin_client, stage, rnd, part, item, "Бетон")
    assert [row["ids"] for row in folded["rows"]] == [[first.id, twin.id]]

    _mark_price_error(admin_client, stage, twin)
    body = _search(admin_client, stage, rnd, part, item, "Бетон")

    assert sorted(_search_ids(body)) == sorted([first.id, twin.id])
    assert all(len(row["ids"]) == 1 for row in body["rows"])
    marks = {row["analog_id"]: row["marks"] for row in body["rows"]}
    assert marks[first.id] == []
    assert [m["kind"] for m in marks[twin.id]] == ["price_error"]
    assert body["total_lines"] == folded["total_lines"] == 2


def test_not_analog_targets_search_does_not_fold_a_tender_marked_row_with_its_twin(admin_client, stage) -> None:
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    first = _row(stage, proposal, D("1000"), title="Бетон", cat="R")
    twin = _row(stage, proposal, D("1000"), title="Бетон", cat="R2")
    rnd, part, item = _kp_scene(stage)

    _mark_tender(admin_client, stage, rnd, part, item, twin)
    body = _search(admin_client, stage, rnd, part, item, "Бетон")

    assert sorted(_search_ids(body)) == sorted([first.id, twin.id])
    assert {row["analog_id"]: [m["kind"] for m in row["marks"]] for row in body["rows"]} == {
        first.id: [],
        twin.id: ["tender"],
    }


def test_not_analog_targets_search_does_not_fold_two_marked_twins_with_each_other(admin_client, stage) -> None:
    """Строка с отметкой не сворачивается ни с кем, в том числе с отмеченной близняшкой (спека §5 п. 1): ключ
    свёртки несёт id отмеченной строки, а не только наличие отметки, иначе метка и закрытый выбор цели скрытой
    строки достались бы представителю."""
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    first = _row(stage, proposal, D("1000"), title="Бетон", cat="R")
    twin = _row(stage, proposal, D("1000"), title="Бетон", cat="R2")
    rnd, part, item = _kp_scene(stage)

    _mark_price_error(admin_client, stage, first)
    _mark_tender(admin_client, stage, rnd, part, item, twin)
    body = _search(admin_client, stage, rnd, part, item, "Бетон")

    assert sorted(_search_ids(body)) == sorted([first.id, twin.id])
    assert all(len(row["ids"]) == 1 for row in body["rows"])
    assert {row["analog_id"]: [m["kind"] for m in row["marks"]] for row in body["rows"]} == {
        first.id: ["price_error"],
        twin.id: ["tender"],
    }


def test_not_analog_targets_the_panel_rows_of_the_reference_blocks_carry_an_empty_marks_list(admin_client, stage) -> None:
    """Строка справочного блока панели собирается той же функцией, что строка поиска: поле `marks` есть всегда."""
    from tests.integration.test_ref_analogs_target import _linked, _panel_row

    scene, ctx = _linked(stage)
    row = _panel_row(ctx.panel(admin_client), scene.ref_item.id)
    assert row["marks"] == []


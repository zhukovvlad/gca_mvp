"""Справочная цель в файле подрядчику и шаблонном комментарии (спека справочных аналогов §2.15, §2.20).

Сцены строятся помощниками справочной цели; названия и числа выдуманы. Эталоны — независимые литералы:
ставки истории 0 %, поэтому нетто истории равно цене; ставка КП — 20 %. Реквизиты договора и путь разделов
читаются из панели (это имена из сцены, а не вычисления), деньги и тексты записаны руками.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.test_kp_file_scene import _agree, _comment
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _inflation_scene, _json, _pair
from tests.integration.test_our_price_round_api import SERIES_NAME, Stage, count_statements
from tests.integration.test_ref_analogs_target import (
    Ctx,
    _amend,
    _body,
    _link_conversion,
    _linked,
    _net,
    _own_scene,
    _panel_row,
    _save_ctx,
)

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
#  Помощники
# ---------------------------------------------------------------------------


def _preview(client, ctx, **params) -> dict:
    url = (
        f"/api/v1/tenders/{ctx.stage.tender.id}/rounds/{ctx.rnd.id}"
        f"/our-price/offers/{ctx.part.offer.id}/export-preview"
    )
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return _json(response)


def _row(body: dict, item) -> dict:
    (found,) = (r for r in body["rows"] if r.get("position_item_id") == item.id)
    return found


def _link_target(client, ctx, scene) -> dict:
    """Строка договора связи выбрана целью с пересчётом по связи; возвращает строку панели."""
    row = _panel_row(ctx.panel(client), scene.ref_item.id)
    _save_ctx(client, ctx, _body(scene.ref_item.id, origin="link", conversion=_link_conversion(row)))
    return row


def _path_text(row: dict) -> str:
    (path,) = row["chapter_paths"]
    return " → ".join(step["title"] for step in path)


# ---------------------------------------------------------------------------
#  Строка файла: подбетонка, 1 107,24 ₽/м² → 11 072,40 ₽/м³
# ---------------------------------------------------------------------------


def test_ref_analogs_file_a_row_without_history_gets_the_converted_target(admin_client, stage) -> None:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    panel_row = _link_target(admin_client, ctx, scene)

    row = _row(_preview(admin_client, ctx), ctx.item)

    contract = panel_row["contract"]
    unit = row["unit"]
    assert row["state"] == "no_history"
    assert D(row["ours"]) == D("13286.88")
    assert D(row["deviation_pct"]) == D("8.4")
    assert row["source"] == (
        f"{contract['object_title']}, договор № {contract['number']}, {contract['year']} г.: "
        f"сопоставимая работа ({_path_text(panel_row)}), 1 328,69 ₽ за м² с НДС 20 %; "
        f"1 м² = 0,1 {unit} (толщина 100 мм): 13 286,88 ₽ за {unit} с НДС 20 %"
    )
    assert row["comment_kind"] == "template"
    assert row["comment"] == (
        f"Ваша цена — 14 400,00 ₽ за {unit} с НДС 20 %. "
        f"В нашем договоре по объекту {contract['object_title']}, {contract['year']} г., "
        f"сопоставимая работа стоила 1 328,69 ₽ за м² с НДС 20 %; 1 м² = 0,1 {unit} (толщина 100 мм), "
        f"то есть 13 286,88 ₽ за {unit}. Просим снизить до 13 286,88 ₽ или обосновать разницу."
    )
    assert row["inflation"] is None


def test_ref_analogs_file_a_row_without_a_reference_target_in_the_same_scene_has_no_target(admin_client, stage) -> None:
    _, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))

    row = _row(_preview(admin_client, ctx), ctx.item)

    assert (row["ours"], row["deviation_pct"], row["source"], row["comment"], row["comment_kind"]) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_ref_analogs_file_a_comparable_row_takes_the_reference_target_instead_of_the_lowest(admin_client, stage) -> None:
    """Свои аналоги строки 90 (самая низкая), 100, 100, 100; цель — строка со 100: «Наша расценка» 120 с НДС, а
    не 108."""
    ctx = _own_scene(stage, prices=("100", "100", "100", "90"))
    before = _row(_preview(admin_client, ctx), ctx.item)
    assert D(before["ours"]) == D("108")

    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    row = _row(_preview(admin_client, ctx), ctx.item)

    assert row["state"] == "comparable"
    assert D(row["ours"]) == D("120")
    assert D(row["deviation_pct"]) == D("20.0")
    assert "сопоставимая работа" in row["source"]
    assert "100,00" not in row["source"]  # цитата — 120,00 с НДС 20 %
    assert "120,00 ₽ за" in row["source"]
    assert row["comment_kind"] == "template"
    assert row["comment"].endswith("Просим снизить до 120,00 ₽ или обосновать разницу.")


def test_ref_analogs_file_a_price_below_the_reference_target_has_no_template(admin_client, stage) -> None:
    """Цена КП 100 нетто (120 с НДС) ниже цели 150 нетто (180): шаблона нет, отклонение со знаком."""
    ctx = _own_scene(stage, prices=("150", "150", "150", "150"))
    stage.db.flush()
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))

    row = _row(_preview(admin_client, ctx), ctx.item)

    assert D(row["ours"]) == D("180")
    assert D(row["deviation_pct"]) == D("-20.0")
    assert (row["comment"], row["comment_kind"]) == (None, None)


# ---------------------------------------------------------------------------
#  Итоги файла не зависят от справочной цели
# ---------------------------------------------------------------------------


def test_ref_analogs_file_the_header_columns_and_all_other_rows_are_equal_with_and_without_the_target(
    admin_client, stage
) -> None:
    ctx = _own_scene(stage, prices=("100", "100", "100", "90"))
    other = stage.kp(ctx.part, D("150"))
    stage.db.flush()
    before = _preview(admin_client, ctx)

    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    after = _preview(admin_client, ctx)

    assert after["header"] == before["header"]
    assert after["columns"] == before["columns"]
    assert [r for r in after["rows"] if r.get("position_item_id") != ctx.item.id] == [
        r for r in before["rows"] if r.get("position_item_id") != ctx.item.id
    ]
    assert _row(after, other) == _row(before, other)
    keep = ("kind", "position_item_id", "number", "title", "unit", "quantity", "kp_unit_price", "kp_total", "state")
    assert {k: _row(after, ctx.item)[k] for k in keep} == {k: _row(before, ctx.item)[k] for k in keep}
    assert _row(after, ctx.item)["ours"] != _row(before, ctx.item)["ours"]


# ---------------------------------------------------------------------------
#  Сброс, «Договорились», свой комментарий
# ---------------------------------------------------------------------------


def test_ref_analogs_file_a_reset_target_goes_into_the_file_as_without_a_choice(admin_client, stage) -> None:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    _link_target(admin_client, ctx, scene)
    assert _row(_preview(admin_client, ctx), ctx.item)["ours"] is not None

    _amend(stage, scene.contract)  # прежняя смета договора перестала быть справочной
    row = _row(_preview(admin_client, ctx), ctx.item)

    assert ctx.stored() is not None
    assert (row["ours"], row["deviation_pct"], row["source"], row["comment"]) == (None, None, None, None)


def test_ref_analogs_file_agreed_stands_over_the_reference_target(admin_client, stage, db_session, admin_user) -> None:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    _link_target(admin_client, ctx, scene)
    # до договорённости справочная цель в файле действует — иначе «Договорились» нечего перекрывать
    assert "сопоставимая работа" in _row(_preview(admin_client, ctx), ctx.item)["source"]
    _agree(db_session, ctx.item, D("10000"), admin_user)

    row = _row(_preview(admin_client, ctx), ctx.item)

    assert D(row["ours"]) == D("12000")
    assert row["source"] == "Договорились 01.10.2026"
    assert row["comment"].startswith("Согласовано: 12 000,00 ₽ за ")
    assert "сопоставимая работа" not in row["comment"]


def test_ref_analogs_file_an_own_comment_goes_as_it_is(admin_client, stage, db_session, admin_user) -> None:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    _link_target(admin_client, ctx, scene)
    _comment(db_session, ctx.item, "Свой текст", admin_user)

    row = _row(_preview(admin_client, ctx), ctx.item)

    assert (row["comment"], row["comment_kind"]) == ("Свой текст", "custom")
    assert D(row["ours"]) == D("13286.88")


# ---------------------------------------------------------------------------
#  ИПЦ
# ---------------------------------------------------------------------------


def test_ref_analogs_file_the_inflation_column_and_the_header_years_follow_the_reference_contract(
    admin_client, stage, db_session
) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
    ctx = Ctx(stage, rnd, part, item, [])
    (analog,) = ctx.panel(admin_client, inflation_series_id=series_id)["analogs"]
    adjusted = _net(analog["price"])
    assert adjusted != D("100")
    response = ctx.put(admin_client, _body(analog["analog_id"]), inflation_series_id=series_id)
    assert response.status_code == 204, response.text

    body = _preview(admin_client, ctx, inflation_series_id=series_id)
    row = _row(body, item)

    assert D(row["ours"]) == adjusted
    # строка договора цели — тот же единственный аналог строки, поэтому цифры совпали бы и без справочной
    # цели; что файл взял именно её, видно по форме «Источника»
    assert "сопоставимая работа" in row["source"]
    assert row["inflation"].startswith(f"{SERIES_NAME}, к 08.2026: ")
    assert "инфляции" in row["comment"]
    # смета договора — 2025-02, цель 2026-08: годы 2025 и 2026
    assert [fact["year"] for fact in body["header"]["inflation_years"]] == [2025, 2026]


def test_ref_analogs_file_without_a_series_there_is_no_inflation_column(admin_client, stage, db_session) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
    ctx = Ctx(stage, rnd, part, item, [])
    (analog,) = ctx.panel(admin_client, inflation_series_id=series_id)["analogs"]
    _save_ctx(admin_client, ctx, _body(analog["analog_id"]))

    body = _preview(admin_client, ctx)
    row = _row(body, item)

    assert D(row["ours"]) == D("100")
    assert (row["inflation"], body["header"]["inflation_years"]) == (None, None)


# ---------------------------------------------------------------------------
#  Запросы и панель
# ---------------------------------------------------------------------------


def test_ref_analogs_file_the_statement_count_does_not_grow_with_the_number_of_targets(admin_client, stage) -> None:
    ctx = _own_scene(stage, prices=("100", "100", "100", "90"))
    second = stage.kp(ctx.part, D("150"))
    stage.db.flush()
    second_ctx = Ctx(stage, ctx.rnd, ctx.part, second, ctx.rows)
    _save_ctx(admin_client, ctx, _body(ctx.rows[0].id))
    _preview(admin_client, ctx)  # прогрев кэшей процесса
    with count_statements(stage.db) as one:
        _preview(admin_client, ctx)

    _save_ctx(admin_client, second_ctx, _body(ctx.rows[1].id))
    _preview(admin_client, ctx)
    with count_statements(stage.db) as two:
        body = _preview(admin_client, ctx)

    # обе цели действуют: «Источник» обеих строк — в форме справочной цели («сопоставимая работа»), а не
    # своих аналогов, которые у обеих строк тоже есть; иначе вторая цель могла бы молча выпасть из файла
    assert all("сопоставимая работа" in _row(body, item)["source"] for item in (ctx.item, second))
    assert len(two) == len(one)


def test_ref_analogs_file_the_panel_template_and_file_ours_equal_the_file(admin_client, stage) -> None:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    _link_target(admin_client, ctx, scene)

    file_row = _row(_preview(admin_client, ctx), ctx.item)
    panel = ctx.panel(admin_client)

    assert panel["comment"]["template_text"] == file_row["comment"]
    assert _pair(panel["line"]["file_ours"])[1] == D(file_row["ours"])
    assert ctx.offer_line(admin_client)["comment_kind"] == "template"

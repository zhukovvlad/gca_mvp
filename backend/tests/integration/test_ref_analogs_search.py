"""Поиск справочных строк договоров из панели строки КП (спека справочных аналогов §2.3, §2.8,
§2.10–§2.12, §2.16, §2.20).

Сцены строятся помощниками экрана 4а и панели справочных блоков; названия, числа и семьи
выдуманы. Эталоны — независимые литералы `Decimal` и строки. Ставки истории — 0 %, поэтому нетто
истории равно цене; ставка КП — 20 %.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.common import DomainError
from crud.our_price_load import load_history, load_kp_lines, load_work_history
from crud.reference_search import SEARCH_LIMIT, parse_query
from models import CatalogPosition, ContextMember, PositionItem
from parser.sanitize_text import NormalizationUnavailableError
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _inflation_scene, _json, _panel, _put, _url
from tests.integration.test_our_price_round_api import Stage, count_statements
from tests.integration.test_our_price_signal import _job
from tests.integration.test_ref_analogs_panel import (
    Linked,
    _contract_row,
    _family_with_param,
    _kp_part,
    _row,
    _set_value,
    _underground,
)

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _search_get(client, stage, rnd, part, item, q, **params):
    stage.db.flush()  # наименование строки, заданное после создания, ещё не в базе
    return client.get(f"{_url(stage.tender, rnd, part)}/lines/{item.id}/reference-search", params={"q": q, **params})


def _search(client, stage, rnd, part, item, q, **params) -> dict:
    response = _search_get(client, stage, rnd, part, item, q, **params)
    assert response.status_code == 200, response.text
    return _json(response)


def _ids(body: dict) -> list[int]:
    return [row["analog_id"] for row in body["rows"]]


def _normalized(stage, item, text) -> None:
    """Нормализованное наименование каталожной позиции строки (по умолчанию — наименование в нижнем регистре)."""
    stage.db.execute(
        sa.update(CatalogPosition).where(CatalogPosition.id == item.catalog_position_id).values(normalized_job_title=text)
    )
    stage.db.flush()


def _kp_scene(stage, price=D("144")):
    rnd, part = _kp_part(stage)
    return rnd, part, stage.kp(part, price)


def _without_kind(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "conversion_kind"}


@pytest.fixture
def lemmas(monkeypatch):
    """Подменяет лемматизатор поиска словарём; слова вне словаря остаются как есть."""

    def install(table: dict[str, str]):
        monkeypatch.setattr(
            "crud.reference_search.normalize_job_title_with_lemmatization", lambda word: table.get(word, word)
        )

    return install


# ---------------------------------------------------------------------------
#  Запрос
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "   ", " а ", "x", "_", "a" * 101, "aa bb cc dd ee ff gg", "a b c"],
    ids=["empty", "blank", "one_char_padded", "one_char", "underscore", "101_chars", "seven_words", "short_words"],
)
def test_ref_analogs_search_query_that_cannot_be_searched_is_refused(text) -> None:
    with pytest.raises(DomainError) as refused:
        parse_query(text)
    assert (refused.value.status_code, refused.value.code) == (422, "search_query_invalid")


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("ab", ("ab",)),
        ("  a bb  ", ("bb",)),
        ("aa bb cc dd ee ff", ("aa", "bb", "cc", "dd", "ee", "ff")),
        ("a" * 100, ("a" * 100,)),
        ("засыпка   песком", ("засыпка", "песком")),
    ],
)
def test_ref_analogs_search_query_is_split_into_words_of_two_characters_or_more(text, words) -> None:
    assert parse_query(text) == words


def test_ref_analogs_search_refusal_is_a_coded_422(member_client, stage) -> None:
    rnd, part, item = _kp_scene(stage)
    response = _search_get(member_client, stage, rnd, part, item, "x")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "search_query_invalid"


def test_ref_analogs_search_finds_word_forms_by_the_lemma_and_the_text(member_client, stage, lemmas) -> None:
    """Запрос «засыпка песком»: «песок» находится по лемме, «песком» — по тексту; «щебнем» — нет."""
    lemmas({"песком": "песок", "засыпка": "засыпка"})
    _, by_lemma = _contract_row(stage, D("100"), title="Засыпка песок")
    _normalized(stage, by_lemma, "засыпка песок")
    _, by_text = _contract_row(stage, D("100"), title="Засыпка песком")
    _normalized(stage, by_text, "работа 1")
    _, other = _contract_row(stage, D("100"), title="Засыпка щебнем")
    _normalized(stage, other, "засыпка щебень")
    rnd, part, item = _kp_scene(stage)
    assert set(_ids(_search(member_client, stage, rnd, part, item, "засыпка песком"))) == {by_lemma.id, by_text.id}


def test_ref_analogs_search_each_word_may_match_in_its_own_column(member_client, stage, lemmas) -> None:
    """Слово «засыпка» есть только в наименовании сметы, «песком» — только леммой в каталожной позиции:
    строка подходит. Условие «все слова в одном столбце» её бы потеряло."""
    lemmas({"песком": "песок"})
    _, mixed = _contract_row(stage, D("100"), title="Засыпка пазух")
    _normalized(stage, mixed, "песок обратный")
    _, half = _contract_row(stage, D("100"), title="Засыпка пазух")
    _normalized(stage, half, "щебень обратный")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "засыпка песком")) == [mixed.id]


def test_ref_analogs_search_every_word_must_be_found(member_client, stage, lemmas) -> None:
    lemmas({})
    _, sand = _contract_row(stage, D("100"), title="Засыпка песком")
    _, gravel = _contract_row(stage, D("100"), title="Засыпка щебнем")
    rnd, part, item = _kp_scene(stage)
    assert set(_ids(_search(member_client, stage, rnd, part, item, "засыпка"))) == {sand.id, gravel.id}
    assert _ids(_search(member_client, stage, rnd, part, item, "засыпка щебнем")) == [gravel.id]
    assert _ids(_search(member_client, stage, rnd, part, item, "засыпка кирпичом")) == []


@pytest.mark.parametrize(
    ("hit", "miss", "query"),
    [("a_", "ab", "a_"), ("100%", "1005", "100%"), ("c\\d", "cd", "c\\d")],
    ids=["underscore", "percent", "backslash"],
)
def test_ref_analogs_search_escapes_pattern_characters_in_both_columns(
    member_client, stage, lemmas, hit, miss, query
) -> None:
    """Лемма — само слово: экранирование проверяется и по наименованию в смете, и по нормализованному
    наименованию каталожной позиции."""
    lemmas({})
    _, found = _contract_row(stage, D("100"), title=hit)
    _normalized(stage, found, hit.lower())
    _, skipped = _contract_row(stage, D("100"), title=miss)
    _normalized(stage, skipped, miss.lower())
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, query)) == [found.id]


def test_ref_analogs_search_without_a_lemmatizer_looks_in_the_text_only(member_client, stage, monkeypatch) -> None:
    def unavailable(_word):
        raise NormalizationUnavailableError("модель не установлена")

    monkeypatch.setattr("crud.reference_search.normalize_job_title_with_lemmatization", unavailable)
    _, by_text = _contract_row(stage, D("100"), title="Засыпка песком")
    _normalized(stage, by_text, "работа 1")
    _, by_lemma_only = _contract_row(stage, D("100"), title="Засыпка песок")
    _normalized(stage, by_lemma_only, "засыпка песок")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "засыпка песком")) == [by_text.id]


# ---------------------------------------------------------------------------
#  Состав
# ---------------------------------------------------------------------------


def test_ref_analogs_search_does_not_find_rows_of_the_tender_object_contract(member_client, stage) -> None:
    """Договор объекта тендера в историю не входит (критическое свойство К6)."""
    own = stage.world.contract(object_=stage.tender.object, rate_class=stage.rate_class)
    _, proposal = stage.world.estimate(own, vat_rate=D("0"))
    _row(stage, proposal, D("100"), title="Подготовка объекта")
    _, foreign = _contract_row(stage, D("100"), title="Подготовка чужая")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "Подготовка")) == [foreign.id]


def test_ref_analogs_search_looks_only_in_the_latest_estimate_of_a_contract(member_client, stage) -> None:
    contract = stage.world.contract(rate_class=stage.rate_class)
    _, old_proposal = stage.world.estimate(contract, vat_rate=D("0"))
    _row(stage, old_proposal, D("100"), title="Подготовка прежняя")
    _, new_proposal = stage.world.estimate(contract, amendment_no=1, vat_rate=D("0"))
    latest = _row(stage, new_proposal, D("100"), title="Подготовка новая")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "Подготовка")) == [latest.id]


def test_ref_analogs_search_finds_rows_without_a_zone_or_a_family(member_client, stage) -> None:
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    no_zone = _row(stage, proposal, D("100"), title="Ничья зона")
    stage.db.execute(sa.update(PositionItem).where(PositionItem.id == no_zone.id).values(section_chain_id=None))
    no_family = stage.line(proposal, "R", D("100"), variant=False)
    no_family.job_title_in_proposal = "Ничья семья"
    stage.db.flush()
    rnd, part, item = _kp_scene(stage)
    zone_row = _search(member_client, stage, rnd, part, item, "Ничья зона")["rows"][0]
    assert zone_row["analog_id"] == no_zone.id
    assert zone_row["zone"] is None
    family_row = _search(member_client, stage, rnd, part, item, "Ничья семья")["rows"][0]
    assert family_row["analog_id"] == no_family.id
    assert family_row["zone"] == "whole"


def test_ref_analogs_search_does_not_find_chapters_system_rows_or_rows_without_a_price(member_client, stage) -> None:
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    good = _row(stage, proposal, D("100"), title="Отбор годная")
    _row(stage, proposal, D("0"), title="Отбор нулевая")
    system = stage.world.line(
        proposal, chapter=stage.chapter(proposal, "R"), unit_cost=D("1"), total=D("5000"), kind="SYSTEM", title="Отбор система"
    )
    assert system.id != good.id
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "Отбор")) == [good.id]


def test_ref_analogs_search_units_switch_keeps_only_the_unit_of_the_kp_row(member_client, stage) -> None:
    metre_family, _, _, metre_variant = _family_with_param(stage, "Семья М", "M", "Толщина")
    _, same = _contract_row(stage, D("100"), title="Единица своя")
    _, other = _contract_row(
        stage, D("100"), title="Единица чужая", family=metre_family, variant=metre_variant, unit="M"
    )
    rnd, part, item = _kp_scene(stage)
    assert set(_ids(_search(member_client, stage, rnd, part, item, "Единица"))) == {same.id, other.id}
    assert set(_ids(_search(member_client, stage, rnd, part, item, "Единица", units="all"))) == {same.id, other.id}
    assert _ids(_search(member_client, stage, rnd, part, item, "Единица", units="kp")) == [same.id]


def test_ref_analogs_search_orders_by_unit_zone_place_price_and_id(member_client, stage) -> None:
    """KP: м², зона «вся», цепочка «Общий раздел». Ожидаемый порядок: своя единица и зона, ближе место,
    дешевле; затем своя единица другой зоны; затем другая единица."""
    metre_family, _, _, metre_variant = _family_with_param(stage, "Семья М", "M", "Толщина")
    far_chain = stage.world.chain(("Другой раздел",), "whole")
    under = _underground(stage)
    _, a = _contract_row(stage, D("300"), title="Порядок а")
    _, b = _contract_row(stage, D("200"), title="Порядок б", chain=far_chain)
    _, c = _contract_row(stage, D("100"), title="Порядок в", chain=under)
    _, d = _contract_row(stage, D("50"), title="Порядок г", family=metre_family, variant=metre_variant, unit="M")
    _, e = _contract_row(stage, D("200"), title="Порядок д")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "Порядок")) == [e.id, a.id, b.id, c.id, d.id]


def test_ref_analogs_search_equal_prices_are_ordered_by_row_id(member_client, stage) -> None:
    _, first = _contract_row(stage, D("100"), title="Равные первая")
    _, second = _contract_row(stage, D("100"), title="Равные вторая")
    rnd, part, item = _kp_scene(stage)
    assert _ids(_search(member_client, stage, rnd, part, item, "Равные")) == sorted([first.id, second.id])


# ---------------------------------------------------------------------------
#  Пересчёт
# ---------------------------------------------------------------------------


def test_ref_analogs_search_row_carries_the_conversion_the_target_will_accept(member_client, stage) -> None:
    scene = Linked(stage)
    _, same_unit = _contract_row(
        stage, D("500"), family=scene.kp_family, variant=scene.kp_variant, unit="M3", title="Подготовка своя"
    )
    metre_family, _, _, metre_variant = _family_with_param(stage, "Семья Н", "M", "Толщина")
    _, manual = _contract_row(
        stage, D("700"), family=metre_family, variant=metre_variant, unit="M", title="Подготовка иная"
    )
    _, no_value = _contract_row(
        stage, D("200"), family=scene.ref_family, variant=scene.ref_variant, unit="M2", title="Подготовка без числа"
    )
    body = _search(member_client, stage, scene.rnd, scene.part, scene.kp_item, "Подготовка")
    by_id = {row["analog_id"]: row for row in body["rows"]}
    assert set(by_id) == {scene.ref_item.id, same_unit.id, manual.id, no_value.id}

    link = by_id[scene.ref_item.id]
    assert link["conversion_kind"] == "link"
    conversion = dict(link["conversion"])
    assert D(conversion.pop("factor")) == D("0.1")
    assert conversion == {"method": "link", "factor_shown": "0,1", "basis": "толщина 100 мм", "link_id": scene.link_id}
    assert D(link["kp_unit_price"]["net"]) == D("11072.4")
    assert link["param_value"] == "100 мм"

    none = by_id[same_unit.id]
    assert (none["conversion_kind"], none["conversion"], none["no_conversion_reason"]) == ("none", None, None)
    assert D(none["kp_unit_price"]["net"]) == D("500")

    manual_row = by_id[manual.id]
    assert (manual_row["conversion_kind"], manual_row["conversion"], manual_row["no_conversion_reason"]) == (
        "manual_needed",
        None,
        None,
    )
    assert manual_row["kp_unit_price"] is None
    assert manual_row["deviation_pct"] is None
    assert manual_row["outlier_ratio"] is None

    gap = by_id[no_value.id]
    assert (gap["conversion_kind"], gap["conversion"]) == ("manual_needed", None)
    assert gap["no_conversion_reason"] == "у строки нет значения «Толщина слоя»"
    assert gap["kp_unit_price"] is None
    # `needs_conversion` — тот же предикат по id единиц, что у сервера; `none` ровно когда он ложен
    assert {row["analog_id"]: row["needs_conversion"] for row in body["rows"]} == {
        scene.ref_item.id: True,
        same_unit.id: False,
        manual.id: True,
        no_value.id: True,
    }
    assert all((row["conversion_kind"] == "none") == (not row["needs_conversion"]) for row in body["rows"])


def test_ref_analogs_search_a_kp_row_without_a_catalog_unit_needs_a_conversion_for_every_row(
    member_client, stage, db_session
) -> None:
    """Единица строки КП не задана: у строки договора с тем же символом единицы пересчёт всё равно нужен."""
    scene = Linked(stage)
    _, same_symbol = _contract_row(
        stage, D("500"), family=scene.kp_family, variant=scene.kp_variant, unit="M3", title="Подготовка своя"
    )
    db_session.execute(
        sa.update(CatalogPosition).where(CatalogPosition.id == scene.kp_item.catalog_position_id).values(unit_id=None)
    )
    db_session.flush()
    body = _search(member_client, stage, scene.rnd, scene.part, scene.kp_item, "Подготовка")
    row = next(r for r in body["rows"] if r["analog_id"] == same_symbol.id)
    assert row["unit"] == "м³"
    assert (row["needs_conversion"], row["conversion_kind"]) == (True, "manual_needed")


# ---------------------------------------------------------------------------
#  Пометка выброса: та же, что в панели
# ---------------------------------------------------------------------------


def _linked_set(stage):
    """Строка КП в м³ и четыре строки связанной семьи в м² со значением «100 мм»: цены 100, 100, 100 и 1000."""
    scene = Linked(stage, price=D("100"))
    items = [scene.ref_item]
    for price in ("100", "100", "1000"):
        _, item = _contract_row(
            stage, D(price), family=scene.ref_family, variant=scene.ref_variant, unit="M2", title="Подготовка"
        )
        _set_value(stage, item, scene.ref_schema, scene.ref_param, "100 мм")
        items.append(item)
    return scene, items


def test_ref_analogs_search_rows_of_a_link_group_equal_the_panel_rows(member_client, stage) -> None:
    scene, items = _linked_set(stage)
    panel = _panel(member_client, stage.tender, scene.rnd, scene.part, scene.kp_item)
    (group,) = panel["reference_links"]
    body = _search(member_client, stage, scene.rnd, scene.part, scene.kp_item, "Подготовка")
    found = {row["analog_id"]: row for row in body["rows"]}
    assert set(found) == {item.id for item in items}
    panel_rows = {row["analog_id"]: row for row in group["rows"]}
    assert set(panel_rows) == set(found)
    for pid, row in panel_rows.items():
        assert _without_kind(found[pid]) == row
    assert panel_rows[items[3].id]["outlier_ratio"] == "10"
    assert all(panel_rows[item.id]["outlier_ratio"] is None for item in items[:3])


def test_ref_analogs_search_rows_of_a_zone_group_equal_the_panel_rows(member_client, stage) -> None:
    chain = _underground(stage)
    rows = [_contract_row(stage, D(price), chain=chain, title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part, item = _kp_scene(stage)
    panel = _panel(member_client, stage.tender, rnd, part, item)
    (group,) = panel["reference_zones"]
    body = _search(member_client, stage, rnd, part, item, "Подготовка")
    panel_rows = {row["analog_id"]: row for row in group["rows"]}
    assert set(panel_rows) == {row.id for row in rows}
    assert set(_ids(body)) == set(panel_rows)
    for found in body["rows"]:
        assert _without_kind(found) == panel_rows[found["analog_id"]]
    assert panel_rows[rows[3].id]["outlier_ratio"] == "10"


def test_ref_analogs_search_rows_of_the_own_rung_carry_the_ladder_mark(member_client, stage) -> None:
    rows = [_contract_row(stage, D(price), title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part, item = _kp_scene(stage)
    panel = _panel(member_client, stage.tender, rnd, part, item)
    analogs = {analog["analog_id"]: analog for analog in panel["analogs"]}
    assert set(analogs) == {row.id for row in rows}
    body = _search(member_client, stage, rnd, part, item, "Подготовка")
    assert {row["analog_id"]: row["outlier_ratio"] for row in body["rows"]} == {
        pid: analog["outlier_ratio"] for pid, analog in analogs.items()
    }
    assert analogs[rows[3].id]["outlier_ratio"] == "10"
    for found in body["rows"]:
        assert found["price"] == analogs[found["analog_id"]]["price"]


def test_ref_analogs_search_other_zone_rows_carry_no_mark_while_the_own_rung_exists(member_client, stage) -> None:
    """У строки КП своя ступень есть — блока А в панели нет, строки другой зоны вне наборов: в поиске
    без пометки даже при цене в десять раз выше соседей своей зоны."""
    for price in ("100", "100", "100"):
        _contract_row(stage, D(price), title="Подготовка")
    chain = _underground(stage)
    under = [_contract_row(stage, D(price), chain=chain, title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part, item = _kp_scene(stage)
    panel = _panel(member_client, stage.tender, rnd, part, item)
    assert panel["reference_zones"] == []
    assert len(panel["analogs"]) == 3
    found = {row["analog_id"]: row for row in _search(member_client, stage, rnd, part, item, "Подготовка")["rows"]}
    assert {row.id for row in under} <= set(found)
    assert [found[row.id]["outlier_ratio"] for row in under] == [None, None, None, None]


def test_ref_analogs_search_does_not_build_the_stage_scene(member_client, stage, monkeypatch) -> None:
    """Сцена этапа и разбор оффера поиску не нужны: подменённые на отказ, они его не роняют."""
    rows = [_contract_row(stage, D(price), title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part, item = _kp_scene(stage)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("поиск строит сцену этапа")

    monkeypatch.setattr("crud.our_price._build_scene", forbidden)
    monkeypatch.setattr("crud.our_price._evaluate", forbidden)
    body = _search(member_client, stage, rnd, part, item, "Подготовка")
    assert {row["analog_id"]: row["outlier_ratio"] for row in body["rows"]} == {
        rows[0].id: None,
        rows[1].id: None,
        rows[2].id: None,
        rows[3].id: "10",
    }


def test_ref_analogs_search_works_from_a_kp_row_without_a_family(member_client, stage) -> None:
    """Строка КП без варианта до лестницы не доходит: поиск отвечает, наборов нет — пометок нет."""
    rows = [_contract_row(stage, D(price), title="Подготовка")[1] for price in ("100", "100", "100", "1000")]
    rnd, part = _kp_part(stage)
    item = stage.kp(part, D("144"), variant=False)
    body = _search(member_client, stage, rnd, part, item, "Подготовка")
    assert {row["analog_id"]: row["outlier_ratio"] for row in body["rows"]} == {row.id: None for row in rows}


def test_ref_analogs_search_row_outside_every_set_has_no_mark(member_client, stage) -> None:
    """Строка другой семьи без связи не лежит ни в одном наборе: цена в десятки раз выше, пометки нет."""
    other_family, _, _, other_variant = _family_with_param(stage, "Семья Ч", "M2", "Толщина")
    for price in ("100", "100", "100"):
        _contract_row(stage, D(price), title="Вне набора")
    _, far = _contract_row(stage, D("90000"), title="Вне набора", family=other_family, variant=other_variant)
    rnd, part, item = _kp_scene(stage)
    found = {row["analog_id"]: row for row in _search(member_client, stage, rnd, part, item, "Вне набора")["rows"]}
    assert found[far.id]["outlier_ratio"] is None
    assert D(found[far.id]["kp_unit_price"]["net"]) == D("90000")


def test_ref_analogs_search_with_a_series_gives_the_panel_prices(member_client, stage, db_session) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
    panel = _panel(member_client, stage.tender, rnd, part, item, inflation_series_id=series_id)
    (analog,) = panel["analogs"]
    body = _search(member_client, stage, rnd, part, item, "Работа", inflation_series_id=series_id)
    (found,) = body["rows"]
    assert found["price"] == analog["price"]
    assert found["nominal"] == analog["nominal"] == {"net": "100", "shown": "100"}
    plain = _search(member_client, stage, rnd, part, item, "Работа")["rows"][0]
    assert plain["price"] == {"net": "100", "shown": "100"}


# ---------------------------------------------------------------------------
#  Предел и свёртка
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distinct", "expected_rows", "expected_truncated", "expected_total"),
    [(SEARCH_LIMIT + 1, SEARCH_LIMIT, True, SEARCH_LIMIT + 2), (SEARCH_LIMIT, SEARCH_LIMIT, False, SEARCH_LIMIT + 1)],
    ids=["over_the_limit", "at_the_limit"],
)
def test_ref_analogs_search_limit_is_counted_after_folding(
    member_client, stage, distinct, expected_rows, expected_truncated, expected_total
) -> None:
    """Одна лишняя строка — повтор первой (тот же договор, наименование, цена): сворачивается. Предел и
    `truncated` считаются по свёрнутым строкам, `total_lines` — по строкам до свёртки."""
    _, proposal = stage.world.estimate(stage.world.contract(rate_class=stage.rate_class), vat_rate=D("0"))
    for number in range(distinct):
        _row(stage, proposal, D(1000) + number, title="Бетон", cat="R")
    _row(stage, proposal, D(1000), title="Бетон", cat="R2")
    rnd, part, item = _kp_scene(stage)
    body = _search(member_client, stage, rnd, part, item, "Бетон")
    assert len(body["rows"]) == expected_rows
    assert body["truncated"] is expected_truncated
    assert body["total_lines"] == expected_total
    assert max(len(row["ids"]) for row in body["rows"]) == 2


# ---------------------------------------------------------------------------
#  Отказы
# ---------------------------------------------------------------------------


def test_ref_analogs_search_is_not_applicable_to_a_system_row(member_client, stage) -> None:
    rnd, part = _kp_part(stage)
    system = _put(stage, part.proposal, "R", D("1"), total=D("5000"), kind="SYSTEM")
    response = _search_get(member_client, stage, rnd, part, system, "Подготовка")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "reference_not_applicable"


def test_ref_analogs_search_row_of_another_offer_is_not_found(member_client, stage) -> None:
    rnd, part, _item = _kp_scene(stage)
    other = stage.participant(rnd, "Подрядчик Б")
    foreign = stage.kp(other, D("144"))
    assert _search_get(member_client, stage, rnd, part, foreign, "Подготовка").status_code == 404
    assert _search_get(member_client, stage, rnd, other, foreign, "Подготовка").status_code == 200


def test_ref_analogs_search_unknown_row_or_offer_of_another_round_is_not_found(member_client, stage) -> None:
    rnd, part, item = _kp_scene(stage)
    missing = member_client.get(f"{_url(stage.tender, rnd, part)}/lines/987654321/reference-search", params={"q": "ab"})
    assert missing.status_code == 404
    other_rnd = stage.round()
    other_part = stage.participant(other_rnd, "Подрядчик В")
    assert _search_get(member_client, stage, rnd, other_part, item, "Подготовка").status_code == 404


# ---------------------------------------------------------------------------
#  Скорость и чтение без записи
# ---------------------------------------------------------------------------


def test_ref_analogs_search_statements_do_not_grow_with_the_kp_estimate_and_never_write(
    member_client, stage, db_session
) -> None:
    for price in ("100", "110", "120"):
        _contract_row(stage, D(price), title="Подготовка")
    rnd, small_part = _kp_part(stage)
    small_item = stage.kp(small_part, D("144"))
    large_part = stage.participant(rnd, "Подрядчик Б")
    large_item = stage.kp(large_part, D("144"))
    for price in range(150, 190):
        stage.kp(large_part, D(price))
    db_session.flush()
    _search(member_client, stage, rnd, small_part, small_item, "Подготовка")  # прогрев кэшей процесса
    _search(member_client, stage, rnd, large_part, large_item, "Подготовка")

    with count_statements(db_session) as small:
        small_body = _search(member_client, stage, rnd, small_part, small_item, "Подготовка")
    with count_statements(db_session) as large:
        large_body = _search(member_client, stage, rnd, large_part, large_item, "Подготовка")

    assert len(small_body["rows"]) == len(large_body["rows"]) == 3
    assert len(small) == len(large)
    writes = [s for s in (*small, *large) if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == []


# ---------------------------------------------------------------------------
#  Загрузчики с фильтрами
# ---------------------------------------------------------------------------


def test_ref_analogs_load_kp_lines_for_one_row_equals_the_row_of_the_full_call(stage, db_session) -> None:
    rnd, part = _kp_part(stage)
    plain = stage.kp(part, D("144"))
    no_family = stage.kp(part, D("150"), variant=False)
    system = _put(stage, part.proposal, "R", D("1"), total=D("5000"), kind="SYSTEM")
    db_session.flush()
    estimate_id = part.estimate.id
    full = load_kp_lines(db_session, [estimate_id])[estimate_id]
    assert {row.line.position_item_id for row in full} == {plain.id, no_family.id, system.id}
    for row in full:
        only = load_kp_lines(db_session, [estimate_id], only_position_item_id=row.line.position_item_id)
        assert only == {estimate_id: [row]}
    assert load_kp_lines(db_session, [estimate_id], only_position_item_id=987654321) == {estimate_id: []}


def test_ref_analogs_load_kp_lines_for_one_row_keeps_the_catalog_signal_of_the_full_call(stage, db_session) -> None:
    """Сигнал каталога одной строки равен сигналу полного вызова и тогда, когда он поднят: строка с живым
    заданием — «в работе», с заданием в ошибке — «ждёт администратора»."""
    rnd, part = _kp_part(stage)
    busy = stage.kp(part, D("144"))
    failed = stage.kp(part, D("150"))
    quiet = stage.kp(part, D("160"))
    db_session.flush()

    def context_of(item) -> int:
        return db_session.execute(
            sa.select(ContextMember.context_id).where(ContextMember.position_item_id == item.id)
        ).scalar_one()

    _job(db_session, "family_suggestion", "pending", context_id=context_of(busy))
    _job(db_session, "family_suggestion", "error", context_id=context_of(failed))
    estimate_id = part.estimate.id
    full = {row.line.position_item_id: row for row in load_kp_lines(db_session, [estimate_id])[estimate_id]}
    assert (full[busy.id].line.in_progress, full[failed.id].line.waiting_admin) == (True, True)
    assert (full[quiet.id].line.in_progress, full[quiet.id].line.waiting_admin) == (False, False)
    for pid, row in full.items():
        assert load_kp_lines(db_session, [estimate_id], only_position_item_id=pid) == {estimate_id: [row]}


def test_ref_analogs_load_work_history_family_filter_keeps_only_the_named_families(stage, db_session) -> None:
    other_family, _, _, other_variant = _family_with_param(stage, "Семья Ф", "M2", "Толщина")
    _, own = _contract_row(stage, D("100"))
    _, other = _contract_row(stage, D("100"), family=other_family, variant=other_variant)
    contracts, _ = load_history(db_session, exclude_object_id=stage.tender.object_id)
    estimate_of = {c.contract_id: c.estimate_id for c in contracts}

    everything, families = load_work_history(db_session, estimate_of=estimate_of)
    only_other, only_families = load_work_history(db_session, estimate_of=estimate_of, family_ids={other_family.id})
    nothing, _ = load_work_history(db_session, estimate_of=estimate_of, family_ids=set())

    assert {line.position_item_id for line in everything} == {own.id, other.id}
    assert [line.position_item_id for line in only_other] == [other.id]
    assert only_other == [line for line in everything if line.position_item_id == other.id]
    assert only_families == families
    assert nothing == []

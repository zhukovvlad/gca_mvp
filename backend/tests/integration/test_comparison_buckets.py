"""Три корзины, режимы НДС, медианы — ОДИН агрегат (спека §2.2, §2.3, §2.5).

Слой 3 архитектуры сравнения договоров (план, раздел «Архитектура»): поверх
роллапов задачи 2 и союза строк задачи 3 строится `build_comparison` — полный
агрегат СРАЗУ по трём корзинам (ДГП/ДС/Итого), с тремя режимами показа НДС и
медианой, которая всегда считается по нетто.

Допсоглашений в системе НЕТ ни одного (спека §6): весь материал этого файла
идёт на ИСКУССТВЕННЫХ фикстурах (`fx.contract_with_amendment` и локальные
сборщики ниже) — путь корзин живыми данными не исполняется.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud import comparison as cmp
from crud.common import DomainError
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники: разбор ответа build_comparison в форму, удобную для проверки
# ---------------------------------------------------------------------------

def _ns(value):
    """dict/list ответа build_comparison -> объект с атрибутным доступом
    (`cell.base.net`), чтобы не таскать по тесту голые словарные ключи."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _ns(v) for key, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


def _row(agg: dict, code: str) -> dict:
    matches = [r for r in agg["rows"] if r["code"] == code]
    assert matches, f"строки с кодом {code!r} нет в ответе build_comparison"
    return matches[0]


def _cells(agg: dict, *, code: str) -> dict:
    """contract_id -> ячейка (`.base`/`.amendments`/`.total`) строки `code`."""
    row = _row(agg, code)
    return {cell["contract_id"]: _ns(cell) for cell in row["cells"]}


def _totals(agg: dict) -> dict:
    return {cell["contract_id"]: _ns(cell) for cell in agg["totals"]}


def _medians(agg: dict, *, code: str, bucket: str):
    return _ns(_row(agg, code)["medians"][bucket])


# ---------------------------------------------------------------------------
#  Локальные сборщики — специфичны для сценариев корзин, в comparison_fixtures
#  не выносились: используются РОВНО одним тестом каждый.
# ---------------------------------------------------------------------------

def _dod_8e_contract(db, factories):
    """Статья "1" — и в ДГП (ставка определена), и в ДС (ставка НЕ определена).
    Статья "2" — только в ДГП (спека §2.3.2, DoD 8е).

    Ставка ДС не определена через РАЗНОГЛАСИЕ двух предложений допсоглашения:
    `amd_a` (со строками по статье "1", 20 %) и `amd_b` (БЕЗ единой строки —
    только для разногласия, 22 %). Пустое предложение попадает в
    `declared_rates` (грузится из Lot/Proposal напрямую, без VIEW), поэтому
    гасит ставку показа СМЕТЫ, но не претендует ни на одну строку.
    """
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal("50000"), area_underground_sp=Decimal("50000"),
    )
    contract = factories.ContractFactory.create(object=obj)
    base_estimate = factories.EstimateFactory.create(contract=contract, amendment_no=None)
    amd_estimate = factories.EstimateFactory.create(contract=contract, amendment_no=1)

    base_proposal = fx.make_proposal(factories, estimate=base_estimate, vat_rate=fx.VAT_20)
    fx.seed_chapter_with_positions(db, factories, proposal=base_proposal, code="1", amounts=["1200.00"])
    fx.seed_chapter_with_positions(db, factories, proposal=base_proposal, code="2", amounts=["600.00"])

    amd_a = fx.make_proposal(factories, estimate=amd_estimate, vat_rate=fx.VAT_20, lot_key="amd_a")
    fx.seed_chapter_with_positions(db, factories, proposal=amd_a, code="1", amounts=["600.00"])
    fx.make_proposal(factories, estimate=amd_estimate, vat_rate=fx.VAT_22, lot_key="amd_b")

    db.flush()
    return contract


class _QueryCounter:
    def __init__(self):
        self.total = 0


def _count_queries(session):
    """Считает запросы соединения сессии. Listener СНИМАЕТСЯ в finally —
    тот же приём, что в `test_comparison_rollup.py` (задача 2)."""

    @contextlib.contextmanager
    def _cm():
        counter = _QueryCounter()
        connection = session.connection()

        def on_execute(conn, cursor, statement, parameters, context, executemany):
            counter.total += 1

        sa.event.listen(connection, "after_cursor_execute", on_execute)
        try:
            yield counter
        finally:
            sa.event.remove(connection, "after_cursor_execute", on_execute)

    return _cm()


# ---------------------------------------------------------------------------
#  Step 1: корзины складываются в итог (DoD 5)
# ---------------------------------------------------------------------------

def test_buckets_sum_to_total(db_session, factories):
    """ДГП + ДС = Итого — точное равенство посчитанных корзин (DoD 5)."""
    contract = fx.contract_with_amendment(
        db_session, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="1000.00", amd="200.00",
    )

    agg = cmp.build_comparison(db_session, [contract.id], vat_mode="net")
    cell = _cells(agg, code="1")[contract.id]

    assert cell.base.state == cmp.VALUE and cell.amendments.state == cmp.VALUE, (
        "предпосылка: обе корзины непусты — иначе сложение проверяло бы None + None"
    )
    assert cell.base.net + cell.amendments.net == cell.total.net


# ---------------------------------------------------------------------------
#  Step 2: медиана считается ПО КОРЗИНЕ, а не берётся от «Итого» (спека §2.2)
# ---------------------------------------------------------------------------

def test_median_is_computed_per_bucket(db_session, factories):
    """Отклонения в режиме «ДС» — от медианы ДС, а не от медианы «Итого».

    У всех трёх договоров РАВНЫЕ суммы «Итого» (100+300 = 200+200 = 300+100):
    если бы медиана ДС ошибочно бралась от «Итого», отклонения ДС оказались бы
    нулевыми у всех троих. Суммы ДС при этом РАЗНЫЕ — медиана, посчитанная
    верно по самой корзине ДС, даёт нулевое отклонение только у среднего.
    """
    a = fx.contract_with_amendment(db_session, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
                                    base="100.00", amd="300.00")
    b = fx.contract_with_amendment(db_session, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
                                    base="200.00", amd="200.00")
    c = fx.contract_with_amendment(db_session, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
                                    base="300.00", amd="100.00")
    ids = [a.id, b.id, c.id]

    agg = cmp.build_comparison(db_session, ids, vat_mode="net")
    cells = _cells(agg, code="1")

    total_nets = {cells[cid].total.net for cid in ids}
    assert len(total_nets) == 1, (
        "предпосылка: суммы «Итого» у всех трёх РАВНЫ — иначе тест не отличал бы "
        "правильную медиану по корзине от ошибочной по «Итого»"
    )
    amd_nets = {cells[cid].amendments.net for cid in ids}
    assert len(amd_nets) == 3, "предпосылка: суммы ДС у всех трёх РАЗНЫЕ"

    assert cells[b.id].amendments.deviation_pct == Decimal(0), (
        "медиана ДС обязана считаться по самой корзине ДС: средний договор — на медиане"
    )
    assert cells[a.id].amendments.deviation_pct > Decimal(0)
    assert cells[c.id].amendments.deviation_pct < Decimal(0)


# ---------------------------------------------------------------------------
#  Step 3: отклонения одинаковы во всех трёх режимах показа (DoD 10)
# ---------------------------------------------------------------------------

def test_deviations_are_identical_in_all_three_modes(db_session, factories):
    """Спека §2.5 правило 2, DoD 10 — ось сравнения ВСЕГДА нетто."""
    a = fx.contract_with_area(db_session, factories, {"1": ["1160.00"]}, vat_rate=Decimal("16"))
    b = fx.contract_with_area(db_session, factories, {"1": ["1440.00"]}, vat_rate=Decimal("20"))
    c = fx.contract_with_area(db_session, factories, {"1": ["1708.00"]}, vat_rate=Decimal("22"))
    ids = [a, b, c]

    devs = {}
    for mode, rate in (("own", None), ("single", Decimal("20")), ("net", None)):
        agg = cmp.build_comparison(db_session, ids, vat_mode=mode, single_rate=rate)
        cells = _cells(agg, code="1")
        devs[mode] = {cid: cells[cid].total.deviation_pct for cid in ids}

    assert all(v is not None for v in devs["net"].values()), (
        "предпосылка: медиана обязана быть вычислена — три сопоставимых значения"
    )
    assert devs["own"] == devs["net"]
    assert devs["single"] == devs["net"]


# ---------------------------------------------------------------------------
#  Step 4: shown_per_sqm следует режиму, net_per_sqm (вход медианы) — нет
# ---------------------------------------------------------------------------

def test_shown_per_sqm_follows_mode_but_median_input_does_not(db_session, factories):
    """`shown_per_sqm` меняется с режимом показа, `net_per_sqm` — нет."""
    contract = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]}, vat_rate=Decimal("20"))

    net_agg = cmp.build_comparison(db_session, [contract], vat_mode="net")
    single_agg = cmp.build_comparison(db_session, [contract], vat_mode="single", single_rate=Decimal("10"))

    net_cell = _cells(net_agg, code="1")[contract].total
    single_cell = _cells(single_agg, code="1")[contract].total

    assert net_cell.net_per_sqm == single_cell.net_per_sqm, "нетто-удельная не зависит от режима"
    assert net_cell.shown_per_sqm != single_cell.shown_per_sqm, (
        "предпосылка: смена режима обязана менять ПОКАЗАННУЮ величину"
    )


# ---------------------------------------------------------------------------
#  Step 5: display_rate_undefined гасит ЯЧЕЙКУ, а не столбец (DoD 8е)
# ---------------------------------------------------------------------------

def test_display_rate_undefined_gates_cell_not_column(db_session, factories):
    """Статья "1" есть и в ДГП (ставка определена), и в ДС (ставка НЕ
    определена) -> "ДГП" число, "ДС" и "Итого" пусты с причиной.

    Контрольный случай ТОЙ ЖЕ фикстуры: статья "2" — только в ДГП -> "ДС"
    даёт НОЛЬ, а не причину, а "Итого" равно числу ДГП: проблемная смета по
    статье "2" пуста и ячейку не гасит (спека §2.2, §2.3.2).
    """
    contract = _dod_8e_contract(db_session, factories)

    agg = cmp.build_comparison(db_session, [contract.id], vat_mode="own")
    row1 = _cells(agg, code="1")[contract.id]
    row2 = _cells(agg, code="2")[contract.id]

    assert row1.base.state == cmp.VALUE and row1.base.net is not None, (
        "предпосылка: ДГП по статье 1 расценена"
    )
    assert row1.base.shown is not None
    assert "display_rate_undefined" not in row1.base.incomplete_reasons

    assert row1.amendments.state == cmp.VALUE, "предпосылка: у ДС есть строки по статье 1"
    assert row1.amendments.net is not None, "ось нетто НЕ гасится причиной показа"
    assert row1.amendments.shown is None
    assert "display_rate_undefined" in row1.amendments.incomplete_reasons

    assert row1.total.shown is None
    assert "display_rate_undefined" in row1.total.incomplete_reasons

    # Контрольный случай: статья только в ДГП.
    assert row2.amendments.state == cmp.ZERO, "нет строк по статье 2 в ДС -> ноль, не причина"
    assert row2.amendments.incomplete_reasons == []
    assert row2.total.net == row2.base.net, "«Итого» равно числу ДГП — проблемная смета не гасит"
    assert "display_rate_undefined" not in row2.total.incomplete_reasons


def test_display_rate_undefined_leaves_deviation_equal_to_net_mode(db_session, factories):
    """Резолюция противоречия §2.5/§2.3.2/DoD 10 (бриф оркестратора): причина
    гасит ПОКАЗ, не ось. В режиме «своя ставка» ячейка статьи 1 пуста
    (`display_rate_undefined`), но её `deviation_pct` РАВЕН отклонению той же
    ячейки в режиме «нетто» — медиана считается по нетто и не замечает
    погашенного показа.
    """
    special = _dod_8e_contract(db_session, factories)
    y = fx.contract_with_area(db_session, factories, {"1": ["960.00"]}, vat_rate=fx.VAT_20)
    z = fx.contract_with_area(db_session, factories, {"1": ["2640.00"]}, vat_rate=fx.VAT_20)
    ids = [special.id, y, z]

    own_agg = cmp.build_comparison(db_session, ids, vat_mode="own")
    net_agg = cmp.build_comparison(db_session, ids, vat_mode="net")

    own_cell = _cells(own_agg, code="1")[special.id].total
    net_cell = _cells(net_agg, code="1")[special.id].total

    assert own_cell.shown is None and "display_rate_undefined" in own_cell.incomplete_reasons, (
        "предпосылка: причина действительно гасит показ в режиме «своя ставка»"
    )
    assert own_cell.deviation_pct is not None, "предпосылка: медиана вычислена — три сопоставимых"
    assert own_cell.deviation_pct == net_cell.deviation_pct


# ---------------------------------------------------------------------------
#  Step 6: список ставок и предвыбор (DoD 8ж)
# ---------------------------------------------------------------------------

def test_rate_options_and_preselection(db_session, factories):
    """Список ставок — союз определённых ставок показа; предвыбор — самая
    частая, при равенстве — бо́льшая (спека §2.3.2, DoD 8ж)."""
    a = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("16"))
    b = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("20"))
    c = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("22"))

    options, preselected = cmp.rate_options(db_session, [a, b, c])

    assert options == [Decimal("16"), Decimal("20"), Decimal("22")]
    assert preselected == Decimal("22"), "три ставки, каждая по разу -> ничья -> бо́льшая"


def test_rate_options_preselection_falls_back_to_base_rates_when_no_display_rate(db_session, factories):
    """Край DoD 8ж: ставка показа не определена НИ У ОДНОЙ сметы выборки ->
    предвыбор считается по известным базовым ставкам групп, список непуст.
    """
    a = fx.contract_with_disagreeing_rates(db_session, factories, rates=[Decimal("20"), Decimal("22")])
    b = fx.contract_with_disagreeing_rates(db_session, factories, rates=[Decimal("20"), Decimal("22")])

    rollups = cmp.load_rollups(db_session, [a, b])
    for contract_rollups in rollups.values():
        for rollup in contract_rollups:
            assert rollup.display_rate is None, (
                "предпосылка: ставка показа НЕ определена ни у одной сметы"
            )

    options, preselected = cmp.rate_options(db_session, [a, b])

    assert options == [Decimal("20"), Decimal("22")], "список берётся из известных баз групп"
    assert preselected == Decimal("22"), (
        "20 и 22 встречаются по разу У КАЖДОЙ из двух смет -> ничья по частоте -> бо́льшая"
    )


# ---------------------------------------------------------------------------
#  Step 7: объект без area_total_sp — ₽/м² прочерк, вне медианы (DoD 13)
# ---------------------------------------------------------------------------

def test_object_without_area_has_no_per_sqm_and_is_excluded_from_median(db_session, factories):
    """Объект без `area_total_sp`: ₽/м² — прочерк, и он не входит в медиану."""
    obj = factories.ObjectFactory.create()  # без area_aboveground_sp/area_underground_sp
    contract = factories.ContractFactory.create(object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal, code="1", amounts=["1200.00"])
    db_session.flush()

    other_a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    other_b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})
    other_c = fx.contract_with_area(db_session, factories, {"1": ["3600.00"]})

    agg = cmp.build_comparison(db_session, [contract.id, other_a, other_b, other_c], vat_mode="net")
    cell = _cells(agg, code="1")[contract.id].total
    median = _medians(agg, code="1", bucket="total")

    assert cell.net is not None, "предпосылка: сумма вычислима — отсутствует именно ₽/м²"
    assert cell.net_per_sqm is None, "нет ТЭП -> прочерк, не ноль"
    assert contract.id not in median.contract_ids, "ячейка без ТЭП не входит в медиану"


# ---------------------------------------------------------------------------
#  Step 8: DoD 12 — искусственная фикстура, медианы нет
# ---------------------------------------------------------------------------

def test_two_positive_two_zero_one_absent_has_no_median(db_session, factories):
    """Две положительные, две нулевые, одна отсутствующая -> после исключения
    нулей и пустой остаётся ДВА сопоставимых значения -> медианы нет (DoD 12,
    правила 3-5). Переписано со строки стенда на искусственную фикстуру: два
    договора исторического примера удалены 2026-08-17.
    """
    p1 = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    p2 = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})
    z1 = fx.contract_with_area(db_session, factories, {"1": ["0.00"]})
    z2 = fx.contract_with_area(db_session, factories, {"1": ["0.00"]})
    absent = fx.contract_with_area(db_session, factories, {"2": ["600.00"]})
    ids = [p1, p2, z1, z2, absent]

    agg = cmp.build_comparison(db_session, ids, vat_mode="net")
    cells = _cells(agg, code="1")

    assert cells[absent].total.state == cmp.ABSENT, "предпосылка: статьи 1 у absent нет вовсе"
    assert cells[z1].total.state == cmp.ZERO and cells[z2].total.state == cmp.ZERO, (
        "предпосылка: у z1/z2 статья есть и расценена в ноль"
    )
    assert cells[p1].total.net_per_sqm is not None and cells[p2].total.net_per_sqm is not None, (
        "предпосылка: у p1/p2 есть и сумма, и ТЭП"
    )

    median = _medians(agg, code="1", bucket="total")
    assert median.comparable_count == 2, "после исключения нулей и пустой остаётся ДВА значения"
    assert median.value is None, "меньше трёх сопоставимых -> медианы нет вовсе (правило 5)"
    for contract_id in ids:
        assert cells[contract_id].total.deviation_pct is None, "подсветки в строке нет вовсе"


# ---------------------------------------------------------------------------
#  Step 9 (дополнительно, DoD 5в): «Нераспределённое» — обе половины
# ---------------------------------------------------------------------------

def test_unallocated_is_in_grand_total_but_excluded_from_median(db_session, factories):
    """«Нераспределённое»: входит в «Итого по договору», НЕ входит в медиану
    (спека §2.1.4, DoD 5в) — половина, отложенная задачей 3 на задачу 4."""
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal("50000"), area_underground_sp=Decimal("50000"),
    )
    contract = factories.ContractFactory.create(object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal, code="1", amounts=["1200.00"])
    fx.seed_unallocated_positions(db_session, factories, proposal=proposal, amounts=["600.00"])

    other_a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    other_b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(db_session, [contract.id, other_a, other_b], vat_mode="net")

    unallocated_cell = _cells(agg, code="::unallocated")[contract.id].total
    assert unallocated_cell.net == cmp.net_of(Decimal("600.00"), fx.VAT_20)

    total_cell = _totals(agg)[contract.id].total
    expected_grand = cmp.net_of(Decimal("1200.00"), fx.VAT_20) + cmp.net_of(Decimal("600.00"), fx.VAT_20)
    assert total_cell.net == expected_grand, "остаток обязан войти в «Итого по договору»"

    unallocated_median = _medians(agg, code="::unallocated", bucket="total")
    assert unallocated_median.value is None and unallocated_median.comparable_count == 0, (
        "«Нераспределённое» никогда не входит в медиану (спека §2.1.4, DoD 5в)"
    )
    assert contract.id not in unallocated_median.contract_ids


# ---------------------------------------------------------------------------
#  Step 10: ДС из ДВУХ смет с разными ставками (DoD 8в)
# ---------------------------------------------------------------------------

def test_amendments_bucket_from_two_estimates_with_different_rates(db_session, factories):
    """Корзина ДС из ДВУХ смет разных ставок: сумма корзины = сумма смет,
    каждая в своей ставке; подпись перечисляет обе, не сворачивая (DoD 8в)."""
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal("50000"), area_underground_sp=Decimal("50000"),
    )
    contract = factories.ContractFactory.create(object=obj)
    base_estimate = factories.EstimateFactory.create(contract=contract, amendment_no=None)
    amd1 = factories.EstimateFactory.create(contract=contract, amendment_no=1)
    amd2 = factories.EstimateFactory.create(contract=contract, amendment_no=2)

    base_proposal = fx.make_proposal(factories, estimate=base_estimate, vat_rate=fx.VAT_20)
    fx.seed_chapter_with_positions(db_session, factories, proposal=base_proposal, code="1", amounts=["1200.00"])
    amd1_proposal = fx.make_proposal(factories, estimate=amd1, vat_rate=fx.VAT_20, lot_key="amd1")
    fx.seed_chapter_with_positions(db_session, factories, proposal=amd1_proposal, code="1", amounts=["600.00"])
    amd2_proposal = fx.make_proposal(factories, estimate=amd2, vat_rate=fx.VAT_22, lot_key="amd2")
    fx.seed_chapter_with_positions(db_session, factories, proposal=amd2_proposal, code="1", amounts=["610.00"])

    agg = cmp.build_comparison(db_session, [contract.id], vat_mode="own")
    cell = _cells(agg, code="1")[contract.id]

    expected_amendments_shown = (
        cmp.net_to_gross(cmp.net_of(Decimal("600.00"), fx.VAT_20), fx.VAT_20)
        + cmp.net_to_gross(cmp.net_of(Decimal("610.00"), fx.VAT_22), fx.VAT_22)
    )
    assert cell.amendments.shown == expected_amendments_shown

    column = next(c for c in agg["columns"] if c["contract_id"] == contract.id)
    caption = column["composition_caption"]
    assert "20" in caption and "22" in caption and "№1" in caption and "№2" in caption, (
        f"подпись обязана перечислить ОБЕ ставки ДС по номерам: {caption!r}"
    )


# ---------------------------------------------------------------------------
#  Step 11: меньше трёх сопоставимых -> подсветки в строке нет (DoD 11)
# ---------------------------------------------------------------------------

def test_fewer_than_three_comparable_gives_no_highlight(db_session, factories):
    """Ровно ДВА сопоставимых значения -> медианы и подсветки нет (DoD 11)."""
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(db_session, [a, b], vat_mode="net")
    cells = _cells(agg, code="1")
    median = _medians(agg, code="1", bucket="total")

    assert median.comparable_count == 2
    assert median.value is None
    assert cells[a].total.deviation_pct is None
    assert cells[b].total.deviation_pct is None


# ---------------------------------------------------------------------------
#  Step 12: число запросов не растёт с размером выборки
# ---------------------------------------------------------------------------

def test_build_comparison_query_count_does_not_grow_with_selection(db_session, factories):
    """`build_comparison` — постоянное число запросов, НЕ зависящее от размера
    выборки (план, задача 4; тот же приём, что задача 2)."""
    ids = [fx.contract_with_area(db_session, factories, {"1": ["1200.00"]}) for _ in range(3)]

    with _count_queries(db_session) as c1:
        cmp.build_comparison(db_session, ids[:1], vat_mode="own")
    with _count_queries(db_session) as c3:
        cmp.build_comparison(db_session, ids, vat_mode="own")

    assert c1.total == c3.total, (
        f"запросов было {c1.total} на один договор и {c3.total} на три — где-то запрос на смету"
    )


# ---------------------------------------------------------------------------
#  Край входа: «единая ставка» без ставки и неизвестный режим
# ---------------------------------------------------------------------------

def test_single_mode_without_rate_opens_on_preselection(db_session, factories):
    """DoD 8ж: режим «единая» обязан открываться С ЧИСЛАМИ, а ссылка вправе не
    нести ставку. Без подстановки предвыбора весь лист был бы пуст — и пуст БЕЗ
    причины: данные в порядке, `incomplete_reasons` тут сказать нечего.

    Отдаваемый `single_rate` — ставка, в которой числа ДЕЙСТВИТЕЛЬНО показаны:
    подпись состава строится по этому полю, и `None` в нём был бы неправдой.
    """
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]}, vat_rate=fx.VAT_20)
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]}, vat_rate=fx.VAT_20)

    agg = cmp.build_comparison(db_session, [a, b], vat_mode="single", single_rate=None)
    cells = _cells(agg, code="1")

    assert agg["rate_preselected"] == fx.VAT_20, "предпосылка: предвыбор вычислим"
    assert agg["single_rate"] == fx.VAT_20, "ставка подставлена из предвыбора, а не осталась None"
    assert cells[a].total.shown is not None, "числа обязаны показываться"
    assert cells[a].total.incomplete_reasons == [], "пустота без причины недопустима"
    assert "20" in agg["caption"], f"подпись обязана назвать ставку показа: {agg['caption']!r}"


def test_unknown_vat_mode_is_a_request_error(db_session, factories):
    """Неизвестный режим — ошибка ЗАПРОСА (400), и узнаётся она на входе.

    Иначе агрегат проделал бы всю работу и упал `ValueError`-ом на подписи, то
    есть отдал бы 500 там, где виноват запрос.
    """
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})

    with pytest.raises(DomainError) as raised:
        cmp.build_comparison(db_session, [a], vat_mode="gross")

    assert raised.value.status_code == 400

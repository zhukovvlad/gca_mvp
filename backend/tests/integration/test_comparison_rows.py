"""Союз узлов выборки, состояния ячеек, синтетические строки (спека §2.1-§2.1.4).

Слой 2 архитектуры сравнения договоров (план, раздел «Архитектура»): поверх
роллапов задачи 1 строится союз статей по всей выборке, ячейка получает
состояние (`ABSENT`/`ZERO`/`VALUE`) и список причин неполноты, а «Без
подстатьи» / «Нераспределённое» — синтетические строки по тем же правилам
союза.

Половина утверждения про «Нераспределённое» (что оно НЕ входит в медиану)
проверяется в задаче 4: `build_comparison` и медианы появляются только там,
здесь их нет — см. пояснение в основном плане, задача 3, пункт (b) ревизии
оркестратора.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Step 1: союз выборки
# ---------------------------------------------------------------------------

def test_union_row_is_present_for_both_contracts(db_session, factories):
    """Статья есть у одного договора выборки -> строка есть у ОБОИХ (спека §2.1.1)."""
    a = fx.contract_with(db_session, factories, {"1": ["100.00"], "2": ["200.00"]})
    b = fx.contract_with(db_session, factories, {"1": ["300.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a, b]))
    codes = {r.code for r in rows if r.kind == "category"}

    assert "1" in codes, "предпосылка: статья 1 есть у обоих"
    assert "2" in codes, "статья 2 есть только у договора a — строка обязана быть и у b"


# ---------------------------------------------------------------------------
#  Step 2: пустая у всех строка отбрасывается, включая корень
# ---------------------------------------------------------------------------

def test_row_empty_for_everyone_is_dropped_including_root(db_session, factories):
    """Пустая у ВСЕХ сравниваемых строка не показывается — даже КОРЕНЬ (спека §2.1.1).

    `build_tree` возвращает все 21 корень безусловно (общий скелет паспорта);
    союз выборки затем отбрасывает те, что пусты у всей выборки. Прежняя
    редакция спеки путала это с «215 + четыре пустых корня» — исправлено.
    """
    a = fx.contract_with(db_session, factories, {"1": ["100.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a]))
    codes = {r.code for r in rows if r.kind == "category"}

    assert "1" in codes, "предпосылка: статья 1 есть у a"
    assert "2" not in codes, "корень без данных у всей выборки обязан выпасть"
    assert len(codes) < 21, "не все 21 корень должны попасть в союз одной статьи"


# ---------------------------------------------------------------------------
#  Step 3: ABSENT против ZERO
# ---------------------------------------------------------------------------

def test_absent_and_zero_are_different_states(db_session, factories):
    """Статьи нет -> ABSENT; есть и расценена в ноль -> ZERO (спека §2.1.2).

    Слой ячеек здесь ещё БЕЗ корзин (задача 4): `cells_for` даёт `CellNet`
    напрямую, `.state` читается без обёртки `.total` — интерфейс задачи 4
    (`Cell(base, amendments, total)`) сюда не относится.
    """
    a = fx.contract_with(db_session, factories, {"1": ["0.00"], "2": ["200.00"]})
    b = fx.contract_with(db_session, factories, {"2": ["300.00"]})

    rollups = cmp.load_rollups(db_session, [a, b])
    cells = cmp.cells_for(rollups, code="1")

    assert cells[a].state == cmp.ZERO, "статья 1 у a есть и расценена в ноль"
    assert cells[b].state == cmp.ABSENT, "статьи 1 у b нет вовсе"


# ---------------------------------------------------------------------------
#  Step 4: строка «Без подстатьи» — по счётчику, а не по сумме
# ---------------------------------------------------------------------------

def test_own_row_appears_with_zero_direct_sum(db_session, factories):
    """Строка «Без подстатьи» — по СЧЁТЧИКУ прямых строк, а не по сумме (план шаг 4).

    Прямая сумма статьи «3» — ноль, но счётчик прямых строк не пуст: без
    строки раскрытая ветка «3» не сошлась бы со своим ребёнком «3.1».
    """
    a = fx.contract_with(db_session, factories, {"3": ["0.00"], "3.1": ["100.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a]))
    own_rows = [r for r in rows if r.kind == "own" and r.parent_code == "3"]

    assert len(own_rows) == 1, "прямые строки есть (сумма ноль) — строка обязана быть"


def test_own_row_value_equals_own_net(db_session, factories):
    """Строка «Без подстатьи» равна `own_net` = positions + допработы (DoD 5б).

    Форма фикстуры — та же, что в задаче 2 (`test_own_net_sums_two_sources_exactly`):
    допработа на РОДИТЕЛЬСКОЙ статье «3», у которой есть непустая подстатья «3.1» —
    именно здесь вычитание «родитель минус дети» дало бы другой ответ.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3", amounts=["500.00"])
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3.1", amounts=["100.00"])
    fx.seed_additional_work(db_session, factories, proposal=proposal,
                            code="3", amount="200.00")

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    cells = cmp.cells_for(rollups, code="3::own")

    expected = cmp.net_of(Decimal("500.00"), fx.VAT_20) + cmp.net_of(Decimal("200.00"), fx.VAT_20)
    assert cells[estimate.contract_id].net == expected


# ---------------------------------------------------------------------------
#  Step 5: «Нераспределённое» — половина, относящаяся к этому слою
# ---------------------------------------------------------------------------

def test_unallocated_row_present_and_equals_remainder(db_session, factories):
    """«Нераспределённое»: строка присутствует, netto равен остатку (спека §2.1.4).

    Проверяется только эта половина. «НЕ входит в медиану» (вторая половина
    DoD 5в) относится к `build_comparison`, которого в этой задаче ещё нет —
    её пишет задача 4 (см. заголовок модуля).
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])
    fx.seed_unallocated_positions(db_session, factories, proposal=proposal, amounts=["50.00"])

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    rows = cmp.build_rows(rollups)
    assert any(r.kind == "unallocated" for r in rows), "строка обязана быть"

    cells = cmp.cells_for(rollups, code="::unallocated")
    assert cells[estimate.contract_id].net == cmp.net_of(Decimal("50.00"), fx.VAT_20)


# ---------------------------------------------------------------------------
#  Step 6-7: неполнота ячейки — целиком, и причины совмещаются
# ---------------------------------------------------------------------------

def test_incomplete_cell_is_empty_entirely(db_session, factories):
    """Одна расценённая и одна нерасценённая позиция в одной статье -> ячейка
    пуста ЦЕЛИКОМ, с причиной `unpriced_rows` (DoD 8а).

    Партиальная сумма реально ВЫЧИСЛИМА роллапом (проверяется отдельно ниже) —
    правило намеренно её отбрасывает: единица неполноты — ячейка (спека §2.1.3).
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00", None])

    cid = fx.category_id(db_session, "1")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    branch = rollup.direct[cid][cmp.SOURCE_POSITIONS]
    assert branch.net == cmp.net_of(Decimal("100.00"), fx.VAT_20), (
        "предпосылка: партиальная сумма реально вычислима роллапом, иначе тест "
        "проверял бы отсутствие числа, а не его подавление"
    )

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    cell = cmp.cells_for(rollups, code="1")[estimate.contract_id]

    assert cell.net is None, "неполная ячейка гасится ЦЕЛИКОМ, а не частично"
    assert cell.incomplete_reasons == frozenset({"unpriced_rows"})


def test_two_coinciding_reasons_are_both_carried(db_session, factories):
    """Ячейка с двумя причинами неполноты несёт ОБЕ, а не старшую (DoD 8д).

    Одна группа этой же статьи даёт `unpriced_rows` (нерасценённая позиция),
    другая — `vat_base_unknown` (предложение без заявленной ставки НДС).
    """
    estimate = factories.EstimateFactory.create()
    proposal_unpriced = fx.make_proposal(factories, estimate=estimate, lot_key="lot_unpriced")
    proposal_unknown_vat = fx.make_proposal(factories, estimate=estimate, vat_rate=None,
                                            lot_key="lot_unknown_vat")

    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal_unpriced,
                                   code="1", amounts=["100.00", None])
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal_unknown_vat,
                                   code="1", amounts=["50.00"])

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    cell = cmp.cells_for(rollups, code="1")[estimate.contract_id]

    assert cell.incomplete_reasons == frozenset({"unpriced_rows", "vat_base_unknown"})
    assert cell.net is None

"""Справочные строки договоров: решение «комплект или ставка» — по цене нетто базы сметы
(спека строк-сумм §9 п. 2, путь «Найти в договорах» и справочной цели).

Ставки и поправки подменены: проверяется только то, что `reference_lines_from_rows` отдаёт правилу
базу НДС строки, а не сравнивает записанную цену."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

import crud.reference_lines as rl
from services.lump_sum import TypicalRate, TypicalRates

D = Decimal
FAMILY, UNIT_M2 = 7, 3


def _row(pid: int, price: str, vat_base: str) -> SimpleNamespace:
    return SimpleNamespace(
        position_item_id=pid,
        contract_id=1,
        estimate_id=10,
        title="Стяжка",
        unit_id=UNIT_M2,
        unit_symbol="м²",
        family_id=FAMILY,
        context_id=100,
        zone=None,
        chain_titles=(),
        chapter_item_id=None,
        unit_cost_total=D(price),
        vat_base=D(vat_base),
        weight=D(1),
        variant_id=200,
        archived_at=None,
        family_title="Стяжка",
    )


@pytest.fixture
def rates(monkeypatch):
    def install(typical: str) -> None:
        fixed = TypicalRates(
            rates={(FAMILY, UNIT_M2): TypicalRate(net=D(typical), lines=3)},
            unit_codes={UNIT_M2: "M2"},
            unit_labels={UNIT_M2: "м²"},
        )
        monkeypatch.setattr(rl, "typical_rates", lambda db: fixed)
        monkeypatch.setattr(rl, "load_lump_overrides", lambda db, ids: {})

    return install


def test_the_same_net_price_with_and_without_vat_gets_one_decision_at_the_threshold(rates):
    """600 ₽ при базе 20 % и 500 ₽ при базе 0 % — обе 500 ₽ нетто: при ставке 100 ₽ обе — комплект
    (граница включена), при 100,01 ₽ — обе ставка. Сравнение по записанной цене развело бы их."""
    rows = [_row(1, "600", "20"), _row(2, "500", "0")]
    rates("100")
    lines = rl.reference_lines_from_rows(None, rows, None)
    assert [lines[1].lump.is_lump, lines[2].lump.is_lump] == [True, True]
    rates("100.01")
    lines = rl.reference_lines_from_rows(None, rows, None)
    assert [lines[1].lump.is_lump, lines[2].lump.is_lump] == [False, False]
    assert lines[1].lump.net == lines[2].lump.net == D(500)


def test_a_gross_price_over_the_threshold_whose_net_is_under_it_is_a_rate(rates):
    """550 ₽ с НДС 20 % — 458,33 ₽ нетто: при ставке 100 ₽ (порог 500) — ставка, хотя записанная цена выше порога."""
    rates("100")
    line = rl.reference_lines_from_rows(None, [_row(1, "550", "20")], None)[1]
    assert line.lump.is_lump is False

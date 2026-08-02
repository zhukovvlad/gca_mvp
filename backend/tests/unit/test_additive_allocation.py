"""Unit-тесты разноски shared-затрат (спека §5.4):
доставка — на весь счёт, additive — внутри своего material_type."""
from collections import namedtuple
from decimal import Decimal

from crud.calculations import _aggregate_by_class

# Строка base-материала: как из SQL-запроса (см. compute_calculations)
Row = namedtuple("Row", "invoice_id material_class_id mat_total mat_vat qty dimension symbol type_id")

CONCRETE, REBAR = 1, 2  # material_type_id


def test_mono_type_invoice_unchanged():
    """Моно-направленный счёт: additive+delivery достаются классам типа — побитово
    как старый общий котёл (регрессия текущего поведения)."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 101, Decimal("40000"), Decimal("8000"), Decimal("5"), "volume", "м³", CONCRETE),
    ]
    delivery = {10: Decimal("3000")}
    additive = {(10, CONCRETE): Decimal("1500")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    # моно-размерность → доли по qty: 10/15 и 5/15; (3000+1500) в тех же долях
    assert contrib[100]["shared_with_vat"] == Decimal("4500") * Decimal("10") / Decimal("15")
    assert contrib[101]["shared_with_vat"] == Decimal("4500") * Decimal("5") / Decimal("15")


def test_mixed_invoice_additive_scoped_to_own_type():
    """Смешанный счёт: additive типа concrete входит только бетонным классам;
    delivery — всем (микс размерностей → по amount)."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    delivery = {10: Decimal("5000")}
    additive = {(10, CONCRETE): Decimal("1000")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    # delivery по amount (80000 vs 20000): 4000 бетону, 1000 арматуре
    # additive только бетону (единственный класс типа → доля 1)
    assert contrib[100]["shared_with_vat"] == Decimal("4000") + Decimal("1000")
    assert contrib[200]["shared_with_vat"] == Decimal("1000")


def test_additive_without_own_type_base_rows_not_allocated():
    """Edge case §5.4: additive типа concrete в счёте только с base-классами rebar
    → не входит ни в чей avg_price."""
    rows = [
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    delivery = {}
    additive = {(10, CONCRETE): Decimal("1000")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    assert contrib[200]["shared_with_vat"] == Decimal("0")


def test_additive_two_types_independent():
    """Два типа с собственными additive: каждый котёл — только своим классам."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    additive = {(10, CONCRETE): Decimal("300"), (10, REBAR): Decimal("700")}
    contrib = _aggregate_by_class(rows, {}, additive)
    assert contrib[100]["shared_with_vat"] == Decimal("300")
    assert contrib[200]["shared_with_vat"] == Decimal("700")

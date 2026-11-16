"""Арифметика пересчёта справочных аналогов (спека справочных аналогов §2.7, §2.8, §2.10, §2.11, §2.13).

Чистые функции без базы. Эталоны — независимые литералы `Decimal`.
"""
from __future__ import annotations

import typing
from decimal import Decimal, localcontext

import pytest

import models
from services import reference_math as rm
from services.price_basis import _CONTEXT


def test_ref_analogs_link_kinds_match_models() -> None:
    assert typing.get_args(rm.LinkKind) == models.LINK_KINDS


# --- link_number: числа ---------------------------------------------------

@pytest.mark.parametrize(
    ("kind", "text", "name", "x", "basis"),
    [
        ("thickness", "100 мм", "Толщина", "0.1", "толщина 100 мм"),
        ("thickness", "100", "Толщина, мм", "0.1", "толщина 100 мм"),
        ("thickness", "100,5", "Толщина, мм", "0.1005", "толщина 100,5 мм"),
        ("thickness", "12 см", "Толщина", "0.12", "толщина 12 см"),
        ("thickness", "0.5 м", "Толщина", "0.5", "толщина 0,5 м"),
        ("density", "1.5 т/м3", "Плотность", "1.5", "плотность 1,5 т/м3"),
        ("width", "300 мм", "Ширина", "0.3", "ширина 300 мм"),
        ("density", "1,5 т/м³", "Плотность", "1.5", "плотность 1,5 т/м³"),
        ("density", "1,5 т/м3", "Плотность", "1.5", "плотность 1,5 т/м3"),
        ("density", "1500 кг/м³", "Плотность", "1.5", "плотность 1500 кг/м³"),
        ("density", "2,4 г/см³", "Плотность", "2.4", "плотность 2,4 г/см³"),
        ("density", "1500", "Плотность, кг/м3", "1.5", "плотность 1500 кг/м3"),
        # единица текста главнее единицы имени, когда обе есть и различны
        ("thickness", "12 см", "Толщина, мм", "0.12", "толщина 12 см"),
        # единица имени — после последней запятой, а не после первой
        ("thickness", "100", "Толщина слоя, утеплитель, мм", "0.1", "толщина 100 мм"),
    ],
)
def test_ref_analogs_link_number_is_a_number(kind, text, name, x, basis) -> None:
    got = rm.link_number(kind, text, name, source=None, composite=False)
    assert got == rm.LinkNumber(x=Decimal(x), basis=basis)


@pytest.mark.parametrize(
    ("kind", "text", "name"),
    [
        ("thickness", "100-200 мм", "Толщина, мм"),
        ("thickness", "до 100 мм", "Толщина, мм"),
        ("thickness", "переменная", "Толщина, мм"),
        ("thickness", "D400", "Толщина, мм"),
        ("thickness", "Ø530х8 мм", "Толщина, мм"),
        ("thickness", "0 мм", "Толщина, мм"),
        ("thickness", "100", "Толщина"),
        ("density", "150", "Поверхностная плотность, г/м2"),
        ("density", "100 мм", "Плотность"),
        ("thickness", "1,5 т/м³", "Толщина"),
        ("thickness", "100 шт", "Толщина, мм"),
        ("thickness", "не уточнено", "Толщина, мм"),
        ("thickness", "", "Толщина, мм"),
        ("thickness", None, "Толщина, мм"),
    ],
)
def test_ref_analogs_link_number_not_a_number(kind, text, name) -> None:
    assert rm.link_number(kind, text, name, source=None, composite=False) is None


def test_ref_analogs_link_number_flags_make_not_a_number() -> None:
    args = ("thickness", "100 мм", "Толщина")
    assert rm.link_number(*args, source="path_conflict", composite=False) is None
    assert rm.link_number(*args, source="off_list", composite=False) is None
    assert rm.link_number(*args, source=None, composite=True) is None
    # соседний вход: другой источник число не отнимает
    assert rm.link_number(*args, source="confirmed", composite=False) is not None


def test_ref_analogs_link_number_constant_has_no_number() -> None:
    assert rm.link_number("constant", "100 мм", "Толщина, мм", source=None, composite=False) is None


def test_ref_analogs_unit_pairs() -> None:
    assert rm.UNIT_PAIRS == {
        "thickness": ("м²", "м³"),
        "width": ("м", "м²"),
        "density": ("м³", "т"),
    }


# --- link_factor / constant_factor ------------------------------------------

def test_ref_analogs_link_factor_smaller_unit_is_x() -> None:
    n = rm.link_factor("thickness", Decimal("0.1"), contract_unit_is_smaller=True)
    assert n == Decimal("0.1")


def test_ref_analogs_link_factor_larger_unit_is_inverse() -> None:
    n = rm.link_factor("thickness", Decimal("0.1"), contract_unit_is_smaller=False)
    assert n == Decimal("10")


def test_ref_analogs_link_factor_inverse_not_rounded() -> None:
    n = rm.link_factor("density", Decimal("0.15"), contract_unit_is_smaller=False)
    with localcontext(_CONTEXT):
        expected = Decimal(1) / Decimal("0.15")
    assert n == expected
    assert n != Decimal("6.6667")
    assert len(n.as_tuple().digits) > 50


def test_ref_analogs_constant_factor_direction() -> None:
    assert rm.constant_factor(Decimal("4"), from_is_contract_family=True) == Decimal("4")
    assert rm.constant_factor(Decimal("4"), from_is_contract_family=False) == Decimal("0.25")


# --- price_in_kp_unit -------------------------------------------------------

def test_ref_analogs_price_in_kp_unit_divides() -> None:
    assert rm.price_in_kp_unit(Decimal("1107.24"), Decimal("0.1")) == Decimal("11072.40")


def test_ref_analogs_price_by_link_both_directions() -> None:
    # толщина 100 мм: договор в м² (меньшая), КП в м³ — и зеркально договор в м³, КП в м²
    x = rm.link_number("thickness", "100 мм", "Толщина", source=None, composite=False).x
    per_m3 = rm.price_in_kp_unit(
        Decimal("1107.24"), rm.link_factor("thickness", x, contract_unit_is_smaller=True)
    )
    per_m2 = rm.price_in_kp_unit(
        Decimal("11072.40"), rm.link_factor("thickness", x, contract_unit_is_smaller=False)
    )
    assert per_m3 == Decimal("11072.40")
    assert per_m2 == Decimal("1107.24")


def test_ref_analogs_price_uses_exact_factor_not_printed() -> None:
    exact = rm.link_factor("density", Decimal("1.5"), contract_unit_is_smaller=False)
    printed = Decimal(rm.format_factor(exact)[0].replace(",", "."))
    price = Decimal("1000")
    assert rm.price_in_kp_unit(price, exact) != rm.price_in_kp_unit(price, printed)
    assert rm.price_in_kp_unit(price, exact) == Decimal("1500")


# --- format_factor ----------------------------------------------------------

@pytest.mark.parametrize(
    ("n", "text", "approx"),
    [
        ("0.1", "0,1", False),
        ("10", "10", False),
        ("1", "1", False),
        ("1.2500", "1,25", False),
        ("0.12345", "0,1235", True),
        ("100.0001", "100,0001", False),
        ("100.00001", "100", True),
    ],
)
def test_ref_analogs_format_factor(n, text, approx) -> None:
    assert rm.format_factor(Decimal(n)) == (text, approx)


def test_ref_analogs_format_factor_inverse_is_approximate() -> None:
    n = rm.link_factor("density", Decimal("1.5"), contract_unit_is_smaller=False)
    assert rm.format_factor(n) == ("0,6667", True)


# --- parse_manual_factor ----------------------------------------------------

@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("0,1", "0.1"),
        ("0.1", "0.1"),
        (" 2 ", "2"),
        ("0,000001", "0.000001"),
        ("1000000", "1000000"),
    ],
)
def test_ref_analogs_manual_factor_accepts(text, value) -> None:
    assert rm.parse_manual_factor(text) == Decimal(value)


@pytest.mark.parametrize(
    "text",
    ["0", "0,0", "-1", "0,1234567", "1000001", "1000000,1", "abc", "NaN", "Infinity", "", "1e3", "1 000"],
)
def test_ref_analogs_manual_factor_rejects(text) -> None:
    assert rm.parse_manual_factor(text) is None


# --- outlier_ratios_of ------------------------------------------------------

def test_ref_analogs_outliers_single_is_not_outlier() -> None:
    assert rm.outlier_ratios_of([(7, Decimal("100"))]) == {7: None}


def test_ref_analogs_outliers_empty() -> None:
    assert rm.outlier_ratios_of([]) == {}


def test_ref_analogs_outliers_marks_far_price_only() -> None:
    prices = [(1, Decimal("100")), (2, Decimal("110")), (4, Decimal("120")), (3, Decimal("1000"))]
    got = rm.outlier_ratios_of(prices)
    assert got[1] is None
    assert got[2] is None
    assert got[4] is None
    # медиана остальных для 3 — 110
    with localcontext(_CONTEXT):
        assert got[3] == Decimal("1000") / Decimal("110")


def test_ref_analogs_outliers_boundary_not_included() -> None:
    # две цены: каждая сравнивается с единственной другой; ровно 5 — не выброс
    got = rm.outlier_ratios_of([(1, Decimal("500")), (2, Decimal("100"))])
    assert got[1] is None
    assert got[2] is None


def test_ref_analogs_outliers_low_side() -> None:
    got = rm.outlier_ratios_of([(1, Decimal("1000")), (2, Decimal("1000")), (3, Decimal("100"))])
    assert got[3] == Decimal("0.1")
    assert got[1] is None


# --- fold_rows --------------------------------------------------------------

def _key(contract=1, title="Работа", unit=1, price="10", factor="1", basis="", zone="whole", mark=None):
    return rm.FoldKey(
        contract=contract, title=title, unit_id=unit, price=Decimal(price),
        factor=Decimal(factor), basis=basis, zone=zone,
        mark=None if mark is None else Decimal(mark),
    )


def _fold(rows):
    return rm.fold_rows(rows, key=lambda r: r[0], pid=lambda r: r[1])


def test_ref_analogs_fold_equal_rows_merge() -> None:
    a, b = (_key(), 9), (_key(), 4)
    got = _fold([a, b])
    assert got == [(b, (4, 9))]


def test_ref_analogs_fold_price_equality_is_value_equality() -> None:
    a, b = (_key(price="10.0"), 2), (_key(price="10.00"), 1)
    assert len(_fold([a, b])) == 1


@pytest.mark.parametrize(
    "other",
    [
        {"price": "10.01"},
        {"zone": "underground"},
        {"mark": "6"},
        {"contract": 2},
        {"title": "Другая"},
        {"unit": 2},
        {"factor": "2"},
        {"basis": "толщина 100 мм"},
    ],
)
def test_ref_analogs_fold_differing_key_does_not_merge(other) -> None:
    got = _fold([(_key(), 1), (_key(**other), 2)])
    assert [ids for _, ids in got] == [(1,), (2,)]


def test_ref_analogs_fold_mark_present_vs_absent_and_other_ratio() -> None:
    rows = [(_key(mark="6"), 1), (_key(), 2), (_key(mark="7"), 3), (_key(mark="6"), 4)]
    got = _fold(rows)
    assert [ids for _, ids in got] == [(1, 4), (2,), (3,)]


def test_ref_analogs_fold_keeps_first_occurrence_order() -> None:
    rows = [(_key(contract=2), 5), (_key(contract=1), 1), (_key(contract=2), 3)]
    got = _fold(rows)
    assert [(r[1], ids) for r, ids in got] == [(3, (3, 5)), (1, (1,))]


@pytest.mark.parametrize(
    ("contract_unit_id", "kp_unit_id", "expected"),
    [(1, 1, True), (1, 2, False), (None, 1, False), (1, None, False), (None, None, False)],
)
def test_units_equal_needs_both_ids_and_equality(contract_unit_id, kp_unit_id, expected) -> None:
    assert rm.units_equal(contract_unit_id, kp_unit_id) is expected

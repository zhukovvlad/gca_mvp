"""Тесты чтения колонок подрядчика — прежде всего денежного контракта.

Новый файл фазы 3. У исходника `app/excel_parser/parse_contractor_row.py`
тестов не было; здесь проверяется единственное отступление от него —
преобразование денег в десятичные строки (AGENTS.md §3, §11) — и раскладка
колонок по ширине блока.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from openpyxl import Workbook

from parser.parse_contractor_row import (
    MONEY_KEYS,
    SUPPORTED_CONTRACTOR_COLSPANS,
    money_to_json,
    parse_contractor_row,
)


class TestMoneyToJson:
    """Деньги: `numeric` в БД ↔ `Decimal` в Python ↔ строки в JSON, никаких float."""

    def test_float_becomes_decimal_string(self):
        assert money_to_json(14998746.74) == "14998746.74"

    def test_conversion_does_not_carry_the_binary_tail(self):
        """Ровно та ошибка, ради которой конвертация делается в парсере.

        `Decimal(float)` берёт двоичное значение целиком; `Decimal(str(float))` —
        кратчайшее представление, которое round-trip'ится точно.
        """
        assert Decimal(14998746.74) != Decimal("14998746.74")
        assert str(Decimal(14998746.74)) == "14998746.74000000022351741790771484375"
        assert Decimal(money_to_json(14998746.74)) == Decimal("14998746.74")

    def test_int_becomes_string(self):
        assert money_to_json(1500) == "1500"

    def test_empty_cell_stays_none(self):
        """Пустая стоимость → NULL, не 0 (AGENTS.md §3)."""
        assert money_to_json(None) is None

    def test_zero_is_preserved_as_zero(self):
        """Ноль — это ноль, а не пустое значение."""
        assert Decimal(money_to_json(0.0)) == 0
        assert Decimal(money_to_json(0)) == 0

    def test_non_finite_becomes_none(self):
        """`nan`/`inf` — не сумма; в jsonb им делать нечего."""
        assert money_to_json(float("nan")) is None
        assert money_to_json(float("inf")) is None

    def test_text_passes_through(self):
        """Строку ошибки Excel разбирает `postprocess.replace_div0_with_null`."""
        assert money_to_json("#DIV/0!") == "#DIV/0!"

    def test_result_is_always_accepted_by_decimal(self):
        """Контракт с фазой 4: она делает `Decimal(value)` без подготовки."""
        for raw in (0, 0.0, 1, -1.5, 1234.56, 14998746.74, 1e12):
            assert Decimal(money_to_json(raw)) == Decimal(str(raw))


class TestParseContractorRow:
    """Раскладка колонок и типы значений на синтетическом листе."""

    @staticmethod
    def _sheet_with_row(values: list) -> tuple:
        """Лист с одной заполненной строкой начиная с колонки J."""
        ws = Workbook().active
        for offset, value in enumerate(values):
            ws.cell(row=2, column=10 + offset, value=value)
        contractor = {"column_start": 10, "merged_shape": {"colspan": len(values)}}
        return ws, contractor

    def test_gp_layout_maps_eleven_columns(self):
        """colspan 11 — раскладка сметы ГП J..T (docs/phase0-input-data.md)."""
        ws, contractor = self._sheet_with_row(
            [2.5, 10.0, 20.0, 30.0, 60.0, 25.0, 50.0, 75.0, 150.0, 300.0, "комментарий"]
        )

        result = parse_contractor_row(ws, 2, contractor)

        assert result["suggested_quantity"] == 2.5
        assert result["unit_cost"] == {"materials": "10.0", "works": "20.0", "indirect_costs": "30.0", "total": "60.0"}
        assert result["total_cost"] == {"materials": "25.0", "works": "50.0", "indirect_costs": "75.0", "total": "150.0"}
        assert result["total_cost_for_organizer_quantity"] == "300.0"
        assert result["comment_contractor"] == "комментарий"

    def test_quantity_stays_a_number(self):
        """Требование «строки» относится к деньгам; количество остаётся числом."""
        ws, contractor = self._sheet_with_row([2.5, 10.0, 20.0, 30.0, 60.0, 25.0, 50.0, 75.0, 150.0, 300.0, None])

        result = parse_contractor_row(ws, 2, contractor)

        assert isinstance(result["suggested_quantity"], float)

    def test_every_money_key_is_covered_by_the_layout(self):
        """`MONEY_KEYS` не должен разъехаться с реально читаемыми колонками."""
        ws, contractor = self._sheet_with_row([1.0] * 11)

        result = parse_contractor_row(ws, 2, contractor)

        for key in MONEY_KEYS:
            head, _, tail = key.partition(".")
            value = result[head][tail] if tail else result[head]
            assert isinstance(value, str), key

    @pytest.mark.parametrize("colspan", SUPPORTED_CONTRACTOR_COLSPANS)
    def test_supported_colspans_parse(self, colspan):
        ws, contractor = self._sheet_with_row([1.0] * colspan)

        assert parse_contractor_row(ws, 2, contractor)

    def test_unsupported_colspan_raises_value_error(self):
        """Внутренний инвариант; наружу такие файлы не пускает оркестратор."""
        ws, contractor = self._sheet_with_row([1.0] * 12)

        with pytest.raises(ValueError, match="Неподдерживаемый colspan"):
            parse_contractor_row(ws, 2, contractor)

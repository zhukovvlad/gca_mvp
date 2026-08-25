"""Тесты чтения колонок подрядчика — прежде всего денежного контракта.

Новый файл фазы 3. У исходника `app/excel_parser/parse_contractor_row.py`
тестов не было; здесь проверяется единственное отступление от него —
преобразование денег в десятичные строки (AGENTS.md §3, §11) — и позиционную
раскладку колонок в разрешённом блоке подрядчика.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from openpyxl import Workbook

from parser.parse_contractor_row import (
    MONEY_KEYS,
    money_to_json,
    parse_contractor_row,
)

from .sheet_builders import COLUMNS_BY_WIDTH, KEYS_8, KEYS_9, KEYS_10, KEYS_12, KEYS_GP_11, resolved


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

    def test_bool_becomes_none(self):
        """`True` в денежной ячейке — не сумма: `Decimal(True)` дал бы 1 тихо."""
        assert money_to_json(True) is None
        assert money_to_json(False) is None

    def test_text_passes_through(self):
        """Строку ошибки Excel гасит `postprocess.replace_excel_errors_with_null`."""
        assert money_to_json("#DIV/0!") == "#DIV/0!"

    def test_result_is_always_accepted_by_decimal(self):
        """Контракт с фазой 4: она делает `Decimal(value)` без подготовки."""
        for raw in (0, 0.0, 1, -1.5, 1234.56, 14998746.74, 1e12):
            assert Decimal(money_to_json(raw)) == Decimal(str(raw))


class TestParseContractorRow:
    """Раскладка колонок и типы значений на синтетическом листе."""

    @staticmethod
    def _sheet_with_row(values: list, columns: tuple[str, ...]) -> tuple:
        """Лист с одной заполненной строкой начиная с колонки J, и разрешённый
        блок под неё (`resolved` из sheet_builders — без листа и шапки)."""
        ws = Workbook().active
        for offset, value in enumerate(values):
            ws.cell(row=2, column=10 + offset, value=value)
        contractor = resolved(10, columns)
        return ws, contractor

    def test_gp_layout_maps_eleven_columns(self):
        """KEYS_GP_11 — раскладка сметы ГП J..T (docs/phase0-input-data.md)."""
        ws, contractor = self._sheet_with_row(
            [2.5, 10.0, 20.0, 30.0, 60.0, 25.0, 50.0, 75.0, 150.0, 300.0, "комментарий"], KEYS_GP_11
        )

        result = parse_contractor_row(ws, 2, contractor)

        assert result["suggested_quantity"] == 2.5
        assert result["unit_cost"] == {"materials": "10.0", "works": "20.0", "indirect_costs": "30.0", "total": "60.0"}
        assert result["total_cost"] == {"materials": "25.0", "works": "50.0", "indirect_costs": "75.0", "total": "150.0"}
        assert result["total_cost_for_organizer_quantity"] == "300.0"
        assert result["comment_contractor"] == "комментарий"

    def test_quantity_stays_a_number(self):
        """Требование «строки» относится к деньгам; количество остаётся числом."""
        ws, contractor = self._sheet_with_row(
            [2.5, 10.0, 20.0, 30.0, 60.0, 25.0, 50.0, 75.0, 150.0, 300.0, None], KEYS_GP_11
        )

        result = parse_contractor_row(ws, 2, contractor)

        assert isinstance(result["suggested_quantity"], float)

    def test_every_money_key_is_covered_by_the_layout(self):
        """`MONEY_KEYS` не должен разъехаться с реально читаемыми колонками.

        Только `KEYS_12` несёт все десять денежных ключей сразу — восемь
        обязательных плюс `total_cost_for_organizer_quantity` и
        `deviation_from_baseline_cost`.
        """
        ws, contractor = self._sheet_with_row([1.0] * len(KEYS_12), KEYS_12)

        result = parse_contractor_row(ws, 2, contractor)

        for key in MONEY_KEYS:
            head, _, tail = key.partition(".")
            value = result[head][tail] if tail else result[head]
            assert isinstance(value, str), key

    @pytest.mark.parametrize("columns", list(COLUMNS_BY_WIDTH.values()), ids=[str(w) for w in COLUMNS_BY_WIDTH])
    def test_supported_widths_parse(self, columns):
        ws, contractor = self._sheet_with_row([1.0] * len(columns), columns)

        assert parse_contractor_row(ws, 2, contractor)

    @pytest.mark.parametrize(
        ("columns", "expected"),
        [
            (
                KEYS_8,
                {
                    "unit_cost": {"materials": "10", "works": "11", "indirect_costs": "12", "total": "13"},
                    "total_cost": {"materials": "14", "works": "15", "indirect_costs": "16", "total": "17"},
                },
            ),
            (
                KEYS_9,
                {
                    "unit_cost": {"materials": "10", "works": "11", "indirect_costs": "12", "total": "13"},
                    "total_cost": {"materials": "14", "works": "15", "indirect_costs": "16", "total": "17"},
                    "comment_contractor": 18,
                },
            ),
            (
                KEYS_10,
                {
                    "suggested_quantity": 10,
                    "unit_cost": {"materials": "11", "works": "12", "indirect_costs": "13", "total": "14"},
                    "total_cost": {"materials": "15", "works": "16", "indirect_costs": "17", "total": "18"},
                    "comment_contractor": 19,
                },
            ),
            (
                KEYS_GP_11,
                {
                    "suggested_quantity": 10,
                    "unit_cost": {"materials": "11", "works": "12", "indirect_costs": "13", "total": "14"},
                    "total_cost": {"materials": "15", "works": "16", "indirect_costs": "17", "total": "18"},
                    "total_cost_for_organizer_quantity": "19",
                    "comment_contractor": 20,
                },
            ),
            (
                KEYS_12,
                {
                    "suggested_quantity": 10,
                    "unit_cost": {"materials": "11", "works": "12", "indirect_costs": "13", "total": "14"},
                    "total_cost": {"materials": "15", "works": "16", "indirect_costs": "17", "total": "18"},
                    "total_cost_for_organizer_quantity": "19",
                    "comment_contractor": 20,
                    "deviation_from_baseline_cost": "21",
                },
            ),
        ],
        ids=["8", "9", "10", "11", "12"],
    )
    def test_column_lift_keeps_the_positional_layout(self, columns, expected):
        """Регрессия на позиционную раскладку колонок подрядчика из разрешённой
        геометрии (наследие width-based инференции) — сейчас читается разрешённый
        набор ключей `contractor.layout.column_keys` и их позиции. Молчаливая
        поломка здесь задела бы весь позиционный разбор. В каждую ячейку
        блока кладётся её физический номер колонки (блок начинается с
        колонки 10), результат сверяется поколоночно; эталон записан
        литералами намеренно — не выводится из `columns`, иначе обе стороны
        сравнения поехали бы вместе при поломке.

        Ширина 10: десятая колонка — `comment_contractor` (число, не строка:
        комментарий не денежный ключ), а `total_cost_for_organizer_quantity`
        у этой раскладки нет вовсе (спека §2.8) — не только «другое значение»,
        как раньше, а другой НАБОР ключей.
        """
        ws, contractor = self._sheet_with_row(list(range(10, 10 + len(columns))), columns)

        result = parse_contractor_row(ws, 2, contractor)

        assert result == expected

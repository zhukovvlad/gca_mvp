"""Чтение колонок одного подрядчика из строки листа.

Перенос `app/excel_parser/parse_contractor_row.py` из
`parser_tender_xlsx@0e178c0`. Раскладка колонок перенесена без изменений;
единственное отступление — денежные значения не отдаются как `float`
(см. `money_to_json`).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from openpyxl.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
)

# Ширины блока подрядчика, для которых известен смысл колонок. Всё остальное
# структурно неразбираемо: `estimate._validate_contractor_blocks` отсекает такие
# файлы `EstimateParseError` до того, как дело дойдёт сюда.
SUPPORTED_CONTRACTOR_COLSPANS = (8, 9, 10, 11)

# Денежные колонки блока подрядчика — ключи в том же составном виде, в каком их
# отдаёт `get_column_keys`. Количества (`suggested_quantity`) сюда не входят.
MONEY_KEYS = frozenset(
    {
        f"{JSON_KEY_UNIT_COST}.{JSON_KEY_MATERIALS}",
        f"{JSON_KEY_UNIT_COST}.{JSON_KEY_WORKS}",
        f"{JSON_KEY_UNIT_COST}.{JSON_KEY_INDIRECT_COSTS}",
        f"{JSON_KEY_UNIT_COST}.{JSON_KEY_TOTAL}",
        f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_MATERIALS}",
        f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_WORKS}",
        f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_INDIRECT_COSTS}",
        f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_TOTAL}",
        JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    }
)


def money_to_json(value: Any) -> Any:
    """Приводит денежное значение ячейки к десятичной строке.

    Деньги в проекте — `numeric` в БД, `Decimal` в Python, **строки в JSON**;
    `float` запрещён (AGENTS.md §3, §11). openpyxl отдаёт числовую ячейку
    именно как `float`, и результат парсера — это JSON, который ложится в
    `estimate_raw_data.raw_data`, поэтому конвертация делается здесь, на границе
    чтения файла. Иначе импорт фазы 4 получил бы `float` и `Decimal(14998746.74)`
    дал бы двоичный мусор вместо `Decimal("14998746.74")`.

    Преобразование идёт через `str(...)`, то есть через кратчайшее представление
    double: оно round-trip'ится точно и не тянет за собой двоичный хвост.

    Args:
        value: значение ячейки.

    Returns:
        Десятичную строку для чисел, `None` для пустой ячейки и для нечисловых
        `nan`/`inf` (пустая стоимость → NULL, а не 0 — AGENTS.md §3). Нечисловые
        значения (например, строка ошибки Excel) возвращаются как есть — их
        разбирает `postprocess.replace_div0_with_null`.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(Decimal(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return str(Decimal(str(value)))
    return value


def parse_contractor_row(ws: Worksheet, row_index: int, contractor: dict[str, Any]) -> dict[str, Any]:
    """Извлекает значения колонок подрядчика из одной строки.

    Набор колонок определяется шириной блока подрядчика (`colspan`); составные
    ключи вида "unit_cost.materials" разворачиваются во вложенные словари.
    Денежные колонки отдаются десятичными строками (`money_to_json`).

    Args:
        ws: лист Excel.
        row_index: номер строки (1-индексация).
        contractor: словарь подрядчика; нужны `column_start` и
            `merged_shape.colspan`.

    Returns:
        Словарь значений ячеек с вложенными блоками `unit_cost` и `total_cost`.

    Raises:
        ValueError: если `colspan` не входит в `SUPPORTED_CONTRACTOR_COLSPANS`.
            В штатном пайплайне сюда не доходит: такие файлы отвергает
            `estimate._validate_contractor_blocks`.
    """

    def get_column_keys(colspan: int) -> list[str]:
        """Порядок ключей колонок подрядчика для заданной ширины блока.

        Порядок соответствует физическому порядку колонок на листе. Для сметы
        ГП (colspan 11) это J..T.

        Raises:
            ValueError: при неподдерживаемом `colspan`.
        """
        uc_mat = f"{JSON_KEY_UNIT_COST}.{JSON_KEY_MATERIALS}"
        uc_wrk = f"{JSON_KEY_UNIT_COST}.{JSON_KEY_WORKS}"
        uc_ind = f"{JSON_KEY_UNIT_COST}.{JSON_KEY_INDIRECT_COSTS}"
        uc_tot = f"{JSON_KEY_UNIT_COST}.{JSON_KEY_TOTAL}"

        tc_mat = f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_MATERIALS}"
        tc_wrk = f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_WORKS}"
        tc_ind = f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_INDIRECT_COSTS}"
        tc_tot = f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_TOTAL}"

        if colspan == 11:
            return [
                JSON_KEY_SUGGESTED_QUANTITY,
                uc_mat,
                uc_wrk,
                uc_ind,
                uc_tot,
                tc_mat,
                tc_wrk,
                tc_ind,
                tc_tot,
                JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
                JSON_KEY_COMMENT_CONTRACTOR,
            ]
        elif colspan == 10:
            return [
                JSON_KEY_SUGGESTED_QUANTITY,
                uc_mat,
                uc_wrk,
                uc_ind,
                uc_tot,
                tc_mat,
                tc_wrk,
                tc_ind,
                tc_tot,
                JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
            ]
        elif colspan == 9:
            return [
                uc_mat,
                uc_wrk,
                uc_ind,
                uc_tot,
                tc_mat,
                tc_wrk,
                tc_ind,
                tc_tot,
                JSON_KEY_COMMENT_CONTRACTOR,
            ]
        elif colspan == 8:
            return [uc_mat, uc_wrk, uc_ind, uc_tot, tc_mat, tc_wrk, tc_ind, tc_tot]
        else:
            expected = ", ".join(str(value) for value in SUPPORTED_CONTRACTOR_COLSPANS)
            raise ValueError(f"Неподдерживаемый colspan подрядчика: {colspan}. Ожидались значения {expected}.")

    def map_to_nested_dict(cells: list[Cell], keys: list[str]) -> dict[str, Any]:
        """Раскладывает значения ячеек по ключам, разворачивая точки во вложенность."""
        result_dict: dict[str, Any] = {}
        for key_str, cell_obj in zip(keys, cells, strict=True):
            key_parts = key_str.split(".")
            current_level_dict = result_dict
            for part in key_parts[:-1]:
                current_level_dict = current_level_dict.setdefault(part, {})
            value = cell_obj.value
            current_level_dict[key_parts[-1]] = money_to_json(value) if key_str in MONEY_KEYS else value
        return result_dict

    contractor_col_start: int = contractor["column_start"]
    contractor_colspan: int = contractor["merged_shape"]["colspan"]

    list_of_keys = get_column_keys(contractor_colspan)

    cells_to_parse: list[Cell] = [
        ws.cell(row=row_index, column=col_idx)
        for col_idx in range(contractor_col_start, contractor_col_start + contractor_colspan)
    ]

    return map_to_nested_dict(cells_to_parse, list_of_keys)

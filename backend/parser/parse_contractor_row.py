"""Чтение колонок одного подрядчика из строки листа.

Перенос `app/excel_parser/parse_contractor_row.py` из
`parser_tender_xlsx@0e178c0` без изменений логики.
"""

from __future__ import annotations

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


def parse_contractor_row(ws: Worksheet, row_index: int, contractor: dict[str, Any]) -> dict[str, Any]:
    """Извлекает значения колонок подрядчика из одной строки.

    Набор колонок определяется шириной блока подрядчика (`colspan`); составные
    ключи вида "unit_cost.materials" разворачиваются во вложенные словари.

    Args:
        ws: лист Excel.
        row_index: номер строки (1-индексация).
        contractor: словарь подрядчика; нужны `column_start` и
            `merged_shape.colspan`.

    Returns:
        Словарь значений ячеек с вложенными блоками `unit_cost` и `total_cost`.

    Raises:
        ValueError: если `colspan` не входит в {8, 9, 10, 11}.
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
            raise ValueError(f"Неподдерживаемый colspan подрядчика: {colspan}. Ожидались значения 8, 9, 10 или 11.")

    def map_to_nested_dict(cells: list[Cell], keys: list[str]) -> dict[str, Any]:
        """Раскладывает значения ячеек по ключам, разворачивая точки во вложенность."""
        result_dict: dict[str, Any] = {}
        for key_str, cell_obj in zip(keys, cells, strict=True):
            key_parts = key_str.split(".")
            current_level_dict = result_dict
            for part in key_parts[:-1]:
                current_level_dict = current_level_dict.setdefault(part, {})
            current_level_dict[key_parts[-1]] = cell_obj.value
        return result_dict

    contractor_col_start: int = contractor["column_start"]
    contractor_colspan: int = contractor["merged_shape"]["colspan"]

    list_of_keys = get_column_keys(contractor_colspan)

    cells_to_parse: list[Cell] = [
        ws.cell(row=row_index, column=col_idx)
        for col_idx in range(contractor_col_start, contractor_col_start + contractor_colspan)
    ]

    return map_to_nested_dict(cells_to_parse, list_of_keys)

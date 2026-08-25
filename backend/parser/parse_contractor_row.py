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
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
)
from .resolve_contractor import ResolvedContractor

# Денежные колонки блока подрядчика — ключи в том же составном виде, в каком их
# отдаёт раскладка подрядчика. Количества (`suggested_quantity`) сюда не входят.
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
        # Р2: доля — та же дисциплина строк, что и деньги; `#DIV/0!` гасит
        # postprocess, ключ при невалидной базе вычищает `_clean_deviation_fields`.
        JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
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
        Десятичную строку для чисел; `None` для пустой ячейки, `bool` и
        нечисловых `nan`/`inf` — всё это «стоимости нет», а пустая стоимость →
        NULL, не 0 (AGENTS.md §3). Прочие нечисловые значения возвращаются как
        есть — строки ошибок Excel гасит
        `postprocess.replace_excel_errors_with_null`, даты переводит в строки
        `postprocess.stringify_temporal_values`.
    """
    if value is None or isinstance(value, bool):
        # bool — не сумма: True в денежной ячейке дал бы Decimal(True) == 1
        # в фазе 4, тихо. Та же судьба, что у nan/inf.
        return None
    if isinstance(value, int):
        return str(Decimal(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return str(Decimal(str(value)))
    return value


def parse_contractor_row(ws: Worksheet, row_index: int, contractor: ResolvedContractor) -> dict[str, Any]:
    """Извлекает значения колонок подрядчика из одной строки.

    Раскладка приходит УЖЕ РАЗРЕШЁННОЙ (`resolve_contractor`): набор и порядок
    ключей задаёт `contractor.layout.column_keys`, а ширина блока — это просто
    число ключей, а не отдельно хранимый `colspan`. Составные ключи вида
    "unit_cost.materials" разворачиваются во вложенные словари. Денежные
    колонки отдаются десятичными строками (`money_to_json`).

    Args:
        ws: лист Excel.
        row_index: номер строки (1-индексация).
        contractor: разрешённый блок подрядчика.

    Returns:
        Словарь значений ячеек с вложенными блоками `unit_cost` и `total_cost`.
    """

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

    contractor_col_start: int = contractor.geometry["column_start"]
    list_of_keys = list(contractor.layout.column_keys)

    cells_to_parse: list[Cell] = [
        ws.cell(row=row_index, column=col_idx)
        for col_idx in range(contractor_col_start, contractor_col_start + len(list_of_keys))
    ]

    return map_to_nested_dict(cells_to_parse, list_of_keys)

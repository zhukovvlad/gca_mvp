"""Карта объединённых ячеек листа Excel.

Перенос `app/excel_parser/build_merged_shape_map.py` из
`parser_tender_xlsx@0e178c0`.

Отличие от исходника: карта строится по границам диапазонов
(`min_row/max_row/min_col/max_col`), а не обходом `ws[merged_range.coord]`.
Результат тот же, но openpyxl не создаёт объекты ячеек — на листах, где
`ws.max_column` раздут пустой стилизованной ячейкой в XFD, обход через `ws[...]`
материализует лишние ячейки (docs/phase0-input-data.md).
"""

from __future__ import annotations

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


def build_merged_shape_map(ws: Worksheet) -> dict[str, dict[str, int]]:
    """Возвращает карта «координата ячейки → размеры её объединённого диапазона».

    Каждой ячейке, входящей в объединённый диапазон, сопоставляется
    `{"rowspan": N, "colspan": M}` всего диапазона.

    Пример для листа с объединением A1:C2::

        {"A1": {"rowspan": 2, "colspan": 3}, "B1": {...}, ..., "C2": {...}}

    Args:
        ws: лист Excel.

    Returns:
        Словарь координат. Ячейки вне объединений в нём отсутствуют.
    """
    merged_map: dict[str, dict[str, int]] = {}

    if not (hasattr(ws, "merged_cells") and hasattr(ws.merged_cells, "ranges")):
        return merged_map

    for merged_range in ws.merged_cells.ranges:
        shape = {
            "rowspan": merged_range.max_row - merged_range.min_row + 1,
            "colspan": merged_range.max_col - merged_range.min_col + 1,
        }
        for col in range(merged_range.min_col, merged_range.max_col + 1):
            column_letter = get_column_letter(col)
            for row in range(merged_range.min_row, merged_range.max_row + 1):
                merged_map[f"{column_letter}{row}"] = dict(shape)

    return merged_map


def merged_rows_in_first_column(ws: Worksheet) -> frozenset[int]:
    """Номера строк, у которых ячейка в колонке A входит в объединённый диапазон.

    Объединённая ячейка в первой колонке — признак конца блока позиций и начала
    итогов (AGENTS.md §11); на этом признаке держится досрочный выход
    `get_lot_positions` и поиск блока `get_summary`.

    Зачем отдельный индекс: исходник проверял признак линейным перебором всех
    объединённых диапазонов на КАЖДОЙ строке. На реальной смете (2613 диапазонов,
    ~2576 строк) это 6,7 млн проверок вхождения — замеренные 4,7 с на сотню
    строк, то есть больше трёх минут на файл. Индекс строится один раз за
    O(диапазонов).
    """
    if not (hasattr(ws, "merged_cells") and hasattr(ws.merged_cells, "ranges")):
        return frozenset()

    rows: set[int] = set()
    for merged_range in ws.merged_cells.ranges:
        if merged_range.min_col == 1:
            rows.update(range(merged_range.min_row, merged_range.max_row + 1))
    return frozenset(rows)

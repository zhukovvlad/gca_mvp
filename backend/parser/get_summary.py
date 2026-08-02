"""Чтение блока итогов (summary).

Перенос `app/excel_parser/get_summary.py` из `parser_tender_xlsx@0e178c0`.

Отличия от исходника:

1. **Начало поиска.** Исходник искал блок итогов от константы
   `START_INDEXING_POSITION_ROW = 13`. Константа убрана вместе с ней же в
   `get_lot_positions`; теперь floor передаётся явно (`search_start_row`) —
   это строка начала позиций. Так поиск не может наткнуться на объединённую
   ячейку шапки таблицы (в смете ГП это A9:A10 «№ п/п»).
2. **Стоимость проверок** — индекс объединённых строк и ограниченная проверка
   пустоты, как в `get_lot_positions`.
"""

from __future__ import annotations

import logging
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from .build_merged_shape_map import merged_rows_in_first_column
from .constants import (
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INITIAL_COST,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_TOTAL_COST_VAT,
    JSON_KEY_VAT,
    TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST,
    TABLE_PARSE_INITIAL_COST,
)
from .parse_contractor_row import parse_contractor_row
from .sheet import contractor_last_column, row_is_empty

log = logging.getLogger(__name__)


def get_summary(ws: Worksheet, contractor: dict[str, Any], search_start_row: int) -> dict[str, Any]:
    """Извлекает итоговые строки подрядчика из блока итогов внизу таблицы.

    Блок итогов общий для всего листа (не привязан к лоту). Его началом
    считается первая строка от `search_start_row`, где ячейка колонки A входит
    в объединённый диапазон; концом — первая полностью пустая строка.

    Каждой строке присваивается семантический ключ по тексту в колонке A
    ("итого"+"ндс" → `total_cost_with_vat`, "ндс" → `vat`, отклонение от
    расчётной стоимости, первоначальная стоимость); если ничего не подошло —
    `merged_{номер_строки}`.

    Args:
        ws: лист Excel.
        contractor: словарь подрядчика для `parse_contractor_row`.
        search_start_row: строка, с которой начинать поиск блока итогов
            (строка начала позиций).

    Returns:
        Словарь итоговых строк; пустой, если блок не найден.
    """
    summary: dict[str, Any] = {}

    merged_first_column_rows = merged_rows_in_first_column(ws)
    last_column = contractor_last_column(contractor)

    summary_start_row = -1
    for row_num in range(search_start_row, ws.max_row + 1):
        if row_num in merged_first_column_rows:
            summary_start_row = row_num
            break

    if summary_start_row == -1:
        log.debug("Блок summary не найден на листе.")
        return {}

    for current_row_num in range(summary_start_row, ws.max_row + 1):
        if row_is_empty(ws, current_row_num, last_column):
            break  # Пустая строка означает конец блока

        first_cell_value = ws.cell(row=current_row_num, column=1).value

        summary_label_raw = str(first_cell_value).strip().lower() if first_cell_value is not None else ""
        summary_key = f"merged_{current_row_num}"

        if "итого" in summary_label_raw and "ндс" in summary_label_raw:
            summary_key = JSON_KEY_TOTAL_COST_VAT
        elif "в том числе ндс" in summary_label_raw or (
            "ндс" in summary_label_raw and "итого" not in summary_label_raw
        ):
            summary_key = JSON_KEY_VAT
        elif TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST in summary_label_raw:
            summary_key = JSON_KEY_DEVIATION_FROM_CALCULATED_COST
        elif TABLE_PARSE_INITIAL_COST in summary_label_raw:
            summary_key = JSON_KEY_INITIAL_COST

        summary_item_data: dict[str, Any] = {JSON_KEY_JOB_TITLE: first_cell_value}
        summary_item_data.update(parse_contractor_row(ws, current_row_num, contractor))

        summary[summary_key] = summary_item_data

    return summary

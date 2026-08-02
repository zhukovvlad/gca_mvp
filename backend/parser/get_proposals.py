"""Сборка предложений подрядчиков по одному лоту.

Перенос `app/excel_parser/get_proposals.py` из `parser_tender_xlsx@0e178c0`.

Отличие от исходника: в `get_summary` передаётся начало блока позиций
(`start_row`) — раньше поиск итогов стартовал от константы 13, см. `get_summary`.

Про «один подрядчик» (AGENTS.md §4): отдельной ветки для сметы ГП не нужно.
`read_contractors` возвращает ячейку-маркер и за ней ровно один заголовок
подрядчика, обход с индекса 1 даёт ровно одно предложение `contractor_1`.
Слой proposals сохраняется как есть — это задел на возврат тендеров (§4).
"""

from __future__ import annotations

from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_COORDINATE,
    JSON_KEY_CONTRACTOR_HEIGHT,
    JSON_KEY_CONTRACTOR_INDEX,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_CONTRACTOR_WIDTH,
)
from .get_additional_info import get_additional_info
from .get_lot_positions import get_lot_positions
from .get_summary import get_summary
from .read_contractors import read_contractors


def get_proposals(ws: Worksheet, start_row: int, end_row: int) -> dict[str, dict[str, Any]]:
    """Собирает предложения всех подрядчиков для одного лота.

    Позиции берутся строго в границах лота, итоги и дополнительная информация —
    общие по всему листу.

    Реквизиты подрядчика (ИНН, адрес, аккредитация) читаются из трёх строк под
    заголовком, и только если заголовок занимает одну строку (`rowspan == 1`).

    Args:
        ws: лист Excel.
        start_row: первая строка лота.
        end_row: последняя строка лота.

    Returns:
        Словарь `{"contractor_1": {...}, ...}`. Пустой, если подрядчики не найдены.
    """
    contractors_list: list[dict[str, Any]] | None = read_contractors(ws)
    proposals: dict[str, dict[str, Any]] = {}

    if not contractors_list:
        return proposals

    # Индекс 0 — ячейка-маркер "Наименование контрагента", подрядчики идут за ней.
    for i in range(1, len(contractors_list)):
        contractor_details = contractors_list[i]

        contractor_name: str | None = contractor_details.get("value")
        contractor_row_start: int | None = contractor_details.get("row_start")
        contractor_col_start: int | None = contractor_details.get("column_start")
        contractor_coordinate: str | None = contractor_details.get("coordinate")

        merged_shape: dict[str, int] = contractor_details.get("merged_shape", {})
        rowspan: int = merged_shape.get("rowspan", 1)
        colspan: int = merged_shape.get("colspan", 1)

        inn_val: Any = None
        address_val: Any = None
        accreditation_val: Any = None

        if rowspan == 1 and contractor_row_start is not None and contractor_col_start is not None:
            inn_val = ws.cell(row=contractor_row_start + 1, column=contractor_col_start).value
            address_val = ws.cell(row=contractor_row_start + 2, column=contractor_col_start).value
            accreditation_val = ws.cell(row=contractor_row_start + 3, column=contractor_col_start).value

        positions_data = get_lot_positions(ws, contractor_details, lot_start_row=start_row, lot_end_row=end_row)
        summary_data = get_summary(ws, contractor_details, search_start_row=start_row)

        contractor_items_data = {
            JSON_KEY_CONTRACTOR_POSITIONS: positions_data,
            JSON_KEY_CONTRACTOR_SUMMARY: summary_data,
        }

        contractor_additional_info_data = get_additional_info(ws, contractor_details)

        proposal_key = f"{JSON_KEY_CONTRACTOR_INDEX}{i}"
        proposals[proposal_key] = {
            JSON_KEY_CONTRACTOR_TITLE: contractor_name,
            JSON_KEY_CONTRACTOR_INN: inn_val,
            JSON_KEY_CONTRACTOR_ADDRESS: address_val,
            JSON_KEY_CONTRACTOR_ACCREDITATION: accreditation_val,
            JSON_KEY_CONTRACTOR_COORDINATE: contractor_coordinate,
            JSON_KEY_CONTRACTOR_WIDTH: colspan,
            JSON_KEY_CONTRACTOR_HEIGHT: rowspan,
            JSON_KEY_CONTRACTOR_ITEMS: contractor_items_data,
            JSON_KEY_CONTRACTOR_ADDITIONAL_INFO: contractor_additional_info_data,
        }

    return proposals

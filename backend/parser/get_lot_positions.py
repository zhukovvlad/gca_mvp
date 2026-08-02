"""Чтение позиций одного лота для одного подрядчика.

Перенос `app/excel_parser/get_lot_positions.py` из `parser_tender_xlsx@0e178c0`.

Два отличия от исходника (оба — по факту прогона на реальной смете ГП,
docs/phase3-parser.md):

1. **Начало блока позиций.** Исходник брал `max(START_INDEXING_POSITION_ROW=13,
   lot_start_row)`. В смете ГП лот начинается со строки 11, и константа молча
   съедала две первые строки — включая строку-раздел лота. Авторитетна граница
   лота: она найдена по реальному маркеру "Лот №", а не угадана. Константа
   убрана.
2. **Стоимость проверок.** Признак «ячейка в колонке A объединена» брался
   линейным перебором всех объединённых диапазонов на каждой строке, а проверка
   пустоты строки разворачивала `ws[row]` до `ws.max_column`. На реальном файле
   это давало минуты на смету. Теперь: индекс объединённых строк строится один
   раз, пустота проверяется в пределах блока подрядчика.
"""

from __future__ import annotations

import logging
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from .build_merged_shape_map import merged_rows_in_first_column
from .constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_JOB_TITLE_NORMALIZED,
    JSON_KEY_NUMBER,
    JSON_KEY_QUANTITY,
    JSON_KEY_UNIT,
)
from .get_items_dict import get_items_dict
from .parse_contractor_row import parse_contractor_row
from .sanitize_text import normalize_job_title_with_lemmatization
from .sheet import contractor_last_column, row_is_empty

log = logging.getLogger(__name__)


def get_lot_positions(
    ws: Worksheet, contractor: dict[str, Any], lot_start_row: int, lot_end_row: int
) -> dict[str, Any]:
    """Извлекает позиции подрядчика строго в границах лота.

    Обход идёт от `lot_start_row` до `lot_end_row` включительно и **досрочно
    прекращается** на первой строке, где ячейка колонки A входит в объединённый
    диапазон: это признак конца блока позиций и начала итогов (AGENTS.md §11).
    Полностью пустые строки пропускаются.

    Общие колонки читаются по фиксированным позициям: A — номер, B — номер
    раздела, C — статья СМР, D — наименование, F — комментарий организатора,
    G — единица измерения, H — количество организатора. Колонки подрядчика
    читает `parse_contractor_row`.

    Args:
        ws: лист Excel.
        contractor: словарь подрядчика; нужны `column_start` и `merged_shape`.
        lot_start_row: первая строка лота.
        lot_end_row: последняя строка лота (включительно).

    Returns:
        Словарь `{"1": {...}, "2": {...}}` — ключи это порядковые номера позиций
        внутри лота в виде строк.
    """
    positions: dict[str, Any] = {}
    item_index: int = 1

    merged_first_column_rows = merged_rows_in_first_column(ws)
    last_column = contractor_last_column(contractor)

    for current_row_num in range(lot_start_row, lot_end_row + 1):
        # Объединённая ячейка в первой колонке — конец блока позиций.
        if current_row_num in merged_first_column_rows:
            log.debug(
                "get_lot_positions: строка %s содержит объединённую ячейку — конец блока позиций",
                current_row_num,
            )
            break

        if row_is_empty(ws, current_row_num, last_column):
            log.debug("get_lot_positions: строка %s пустая — пропускаем", current_row_num)
            continue

        item = get_items_dict(contractor["merged_shape"]["colspan"])
        item[JSON_KEY_NUMBER] = ws.cell(row=current_row_num, column=1).value
        item[JSON_KEY_CHAPTER_NUMBER] = ws.cell(row=current_row_num, column=2).value
        item[JSON_KEY_ARTICLE_SMR] = ws.cell(row=current_row_num, column=3).value
        original_job_title = ws.cell(row=current_row_num, column=4).value
        item[JSON_KEY_JOB_TITLE] = original_job_title
        item[JSON_KEY_JOB_TITLE_NORMALIZED] = normalize_job_title_with_lemmatization(original_job_title)
        item[JSON_KEY_COMMENT_ORGANIZER] = ws.cell(row=current_row_num, column=6).value
        item[JSON_KEY_UNIT] = ws.cell(row=current_row_num, column=7).value
        item[JSON_KEY_QUANTITY] = ws.cell(row=current_row_num, column=8).value

        contractor_specific_data = parse_contractor_row(ws, current_row_num, contractor)
        item.update(contractor_specific_data)

        positions[str(item_index)] = item
        item_index += 1

    return positions

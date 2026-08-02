"""Чтение шапки документа: идентификатор, название, объект, адрес.

Перенос `app/excel_parser/read_headers.py` из `parser_tender_xlsx@0e178c0`.

Отличие от исходника — диапазон сканирования. Исходник жёстко читал строки 3–5;
в смете ГП шапка на строку выше ("Предмет тендера" в строке 2), и на реальном
файле исходный диапазон терял `tender_id` и `tender_title` (замер —
docs/phase3-parser.md). Диапазон вынесен в константы и расширен вверх до строки 2.
Для тендерных таблиц поведение не меняется: при дублировании ключа побеждает
нижняя строка.
"""

from __future__ import annotations

from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    HEADER_SCAN_ROW_END,
    HEADER_SCAN_ROW_START,
    JSON_KEY_TENDER_ADDRESS,
    JSON_KEY_TENDER_ID,
    JSON_KEY_TENDER_OBJECT,
    JSON_KEY_TENDER_TITLE,
    MAX_HEADER_SCAN_COLUMN,
    TABLE_PARSE_ADDRESS,
    TABLE_PARSE_OBJECT,
    TABLE_PARSE_TENDER_SUBJECT,
)
from .sanitize_text import sanitize_text


def read_headers(ws: Worksheet) -> dict[str, str | None]:
    """Извлекает заголовочную информацию из строк `HEADER_SCAN_ROW_START..END`.

    В каждой строке метка ожидается первым непустым значением, а искомое
    значение — вторым. Поиск меток регистрозависимый и по префиксу.

    "Предмет тендера" дополнительно разбирается на идентификатор и название:
    идентификатор — до первого пробела (символ "№" снимается), название —
    остаток строки. Если пробела нет, идентификатор используется и как название.

    Args:
        ws: лист Excel.

    Returns:
        Словарь с ключами `tender_id`, `tender_title`, `tender_object`,
        `tender_address`. Ненайденные значения — None.
    """
    header_data: dict[str, str | None] = {
        JSON_KEY_TENDER_ID: None,
        JSON_KEY_TENDER_TITLE: None,
        JSON_KEY_TENDER_OBJECT: None,
        JSON_KEY_TENDER_ADDRESS: None,
    }

    for row_num in range(HEADER_SCAN_ROW_START, HEADER_SCAN_ROW_END + 1):
        current_row_non_empty_values: list[str] = []
        for row_cells in ws.iter_rows(min_row=row_num, max_row=row_num, max_col=MAX_HEADER_SCAN_COLUMN):
            for cell in row_cells:
                if cell.value is not None:
                    cell_str_value = str(cell.value).strip()
                    if cell_str_value:
                        current_row_non_empty_values.append(cell_str_value)

        if not current_row_non_empty_values:
            continue

        first_cell_text = current_row_non_empty_values[0]

        if first_cell_text.startswith(TABLE_PARSE_TENDER_SUBJECT):
            if len(current_row_non_empty_values) > 1:
                tender_details_full_str = sanitize_text(current_row_non_empty_values[1])
                parts = tender_details_full_str.split(" ", 1)

                id_candidate = parts[0].replace("№", "").strip()
                header_data[JSON_KEY_TENDER_ID] = id_candidate if id_candidate else None

                if len(parts) > 1:
                    title_candidate = parts[1].strip()
                    header_data[JSON_KEY_TENDER_TITLE] = title_candidate if title_candidate else None
                elif id_candidate:
                    # Пробела после идентификатора нет — он же используется как название
                    header_data[JSON_KEY_TENDER_TITLE] = id_candidate

        elif first_cell_text.startswith(TABLE_PARSE_OBJECT):
            if len(current_row_non_empty_values) > 1:
                object_text = current_row_non_empty_values[1].strip()
                header_data[JSON_KEY_TENDER_OBJECT] = object_text if object_text else None

        elif first_cell_text.startswith(TABLE_PARSE_ADDRESS) and len(current_row_non_empty_values) > 1:
            address_text = current_row_non_empty_values[1].strip()
            header_data[JSON_KEY_TENDER_ADDRESS] = address_text if address_text else None

    return header_data

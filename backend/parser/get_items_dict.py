"""Шаблон словаря одной позиции сметы.

Перенос `app/excel_parser/get_items_dict.py` из `parser_tender_xlsx@0e178c0`,
переписанный под фичу «колонки по заголовкам» (спека §2.4, §2.8). Прежняя
редакция строила поля подрядчика по ширине блока (`colspan`) — своей второй
таблицей, отдельной от той, что читает ячейки в `parse_contractor_row`, и
рисковавшей разъехаться с ней молча. Теперь шаблон строится по РАЗРЕШЁННОЙ
раскладке (`BlockLayout.column_keys`) — тому же перечню ключей.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_NUMBER,
    JSON_KEY_QUANTITY,
    JSON_KEY_UNIT,
)
from .resolve_contractor import BlockLayout


def get_items_dict(layout: BlockLayout) -> dict[str, Any]:
    """Шаблон позиции: общие поля + поля подрядчика по разрешённой раскладке.

    Шаблон строится по ТОМУ ЖЕ перечню ключей, которым читаются ячейки
    (BlockLayout.column_keys), поэтому разъехаться с parse_contractor_row не
    может по построению. У блока, где колонки нет, ключа в шаблоне нет вовсе —
    отсутствующий ключ, а не null (спека §2.8): все потребители читают позицию
    через .get(...) (estimate_import.py) и .pop(..., None) (_clean_deviation_fields).
    """
    item: dict[str, Any] = {
        JSON_KEY_NUMBER: None,
        JSON_KEY_CHAPTER_NUMBER: None,
        JSON_KEY_ARTICLE_SMR: None,
        JSON_KEY_JOB_TITLE: None,
        JSON_KEY_COMMENT_ORGANIZER: None,
        JSON_KEY_UNIT: None,
        JSON_KEY_QUANTITY: None,
    }
    for key in layout.column_keys:
        head, _, tail = key.partition(".")
        if tail:
            item.setdefault(head, {})[tail] = None
        else:
            item[key] = None
    return item

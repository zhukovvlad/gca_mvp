"""Шаблон словаря одной позиции сметы.

Перенос `app/excel_parser/get_items_dict.py` из `parser_tender_xlsx@0e178c0`
без изменений логики.

Набор полей подрядчика определяется шириной его блока (`colspan`) и обязан
совпадать со списком ключей в `parse_contractor_row.get_column_keys` — иначе
шаблон и заполнение разъедутся. В смете ГП блок подрядчика — 11 колонок
(J..T: предлагаемое количество, цена за единицу ×4, стоимость всего ×4,
стоимость за объёмы заказчика, комментарий участника).
"""

from __future__ import annotations

from typing import Any

from .constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_MATERIALS,
    JSON_KEY_NUMBER,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_QUANTITY,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
)


def get_items_dict(contractor_colspan: int) -> dict[str, Any]:
    """Возвращает шаблон позиции: общие поля + поля подрядчика под `colspan`.

    Все значения — None; блоки `unit_cost`/`total_cost` — независимые вложенные
    словари.

    Общие поля (всегда): number, chapter_number, article_smr, job_title,
    comment_organizer, unit, quantity.

    Поля подрядчика:

    * colspan 11 — suggested_quantity, unit_cost, total_cost,
      total_cost_for_organizer_quantity, comment_contractor;
    * colspan 10 — то же без comment_contractor;
    * colspan 9 — unit_cost, total_cost, comment_contractor;
    * colspan 8 — unit_cost, total_cost.

    Args:
        contractor_colspan: ширина блока подрядчика. Поддерживаются 8, 9, 10, 11.

    Returns:
        Словарь-шаблон. При неподдерживаемом `colspan` — общие поля плюс ключ
        "error" с описанием (позиция всё равно попадёт в raw_data, а импорт
        фазы 4 увидит явный маркер проблемы).
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

    cost_block_template: dict[str, Any] = {
        JSON_KEY_MATERIALS: None,
        JSON_KEY_WORKS: None,
        JSON_KEY_INDIRECT_COSTS: None,
        JSON_KEY_TOTAL: None,
    }

    contractor_specific_data: dict[str, Any]

    if contractor_colspan == 11:
        contractor_specific_data = {
            JSON_KEY_SUGGESTED_QUANTITY: None,
            JSON_KEY_UNIT_COST: cost_block_template.copy(),
            JSON_KEY_TOTAL_COST: cost_block_template.copy(),
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST: None,
            JSON_KEY_COMMENT_CONTRACTOR: None,
        }
    elif contractor_colspan == 10:
        contractor_specific_data = {
            JSON_KEY_SUGGESTED_QUANTITY: None,
            JSON_KEY_UNIT_COST: cost_block_template.copy(),
            JSON_KEY_TOTAL_COST: cost_block_template.copy(),
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST: None,
        }
    elif contractor_colspan == 9:
        contractor_specific_data = {
            JSON_KEY_UNIT_COST: cost_block_template.copy(),
            JSON_KEY_TOTAL_COST: cost_block_template.copy(),
            JSON_KEY_COMMENT_CONTRACTOR: None,
        }
    elif contractor_colspan == 8:
        contractor_specific_data = {
            JSON_KEY_UNIT_COST: cost_block_template.copy(),
            JSON_KEY_TOTAL_COST: cost_block_template.copy(),
        }
    else:
        contractor_specific_data = {"error": f"Unknown contractor_colspan: {contractor_colspan}"}

    return {**item, **contractor_specific_data}

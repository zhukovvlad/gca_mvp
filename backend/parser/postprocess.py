"""Постобработка распарсенной структуры.

Перенос `app/excel_parser/postprocess.py` из `parser_tender_xlsx@0e178c0`.

Два отступления от исходника, оба — про пригодность результата как JSON
(`estimate_raw_data.raw_data`, AGENTS.md §4):

* `replace_excel_errors_with_null` (бывш. `replace_div0_with_null`) гасит все
  литералы ошибок Excel, а не только деление на ноль: `#N/A` или `#REF!` в
  денежной колонке — такое же «значения нет», как `#DIV/0!`, и не должны
  доезжать до фазы 4 строкой, неотличимой по смыслу от суммы;
* `stringify_temporal_values` переводит `datetime`/`date`/`time`/`timedelta`
  в ISO-строки: openpyxl отдаёт date-форматированные ячейки объектами, а
  `json.dumps` их не сериализует — контракт «data кладётся в jsonb как есть»
  иначе держался бы на удаче входных данных.

Здесь же закрывается половина требования «адаптация без baseline» (AGENTS.md
§5.2): в смете ГП предложения «Расчетная стоимость» нет, и
`normalize_lots_json_structure` штатно подставляет заглушку и вычищает поля
отклонений у подрядчика. Отклонения от норматива в GCA считает VIEW
`v_position_deviation_inputs` (переименован из `v_position_deviations`
миграцией 0012), а `position_items.deviation_from_baseline_cost`
остаётся NULL (AGENTS.md §4).
"""

from __future__ import annotations

import copy
import datetime as dt
import logging
from typing import Any

from .constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_CHAPTER_REF,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_INDEX,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL_COST,
    TABLE_PARSE_BASELINE_COST,
)

log = logging.getLogger(__name__)

# Литералы ошибок Excel (кэшированные значения при data_only=True) плюс два
# исторических варианта деления на ноль из исходника. Сравнение — по
# strip().lower().
EXCEL_ERROR_STRINGS = {
    # деление на ноль — набор исходника
    "div/0",
    "#div/0!",
    "деление на 0",
    # остальные ошибки Excel
    "#n/a",
    "#name?",
    "#null!",
    "#num!",
    "#ref!",
    "#value!",
    "#spill!",
    "#calc!",
    "#getting_data",
}

BASELINE_MISSING_TITLE = "Расчетная стоимость отсутствует"


class DataIntegrityError(Exception):
    """Структура данных не соответствует ожиданиям."""


# --- Вспомогательные ("приватные") функции ---


def _separate_proposals(
    proposals_in_lot: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Разделяет предложения на «Расчетную стоимость» и предложения подрядчиков.

    Returns:
        Кортеж (baseline или None, словарь «реальных» предложений по названию).
    """
    baseline_proposal = None
    actual_proposals = {}

    for proposal_data in proposals_in_lot.values():
        title = str(proposal_data.get(JSON_KEY_CONTRACTOR_TITLE, "")).strip().lower()
        if title == TABLE_PARSE_BASELINE_COST:
            baseline_proposal = proposal_data
        elif title:  # предложения без названия отбрасываем
            actual_proposals[proposal_data[JSON_KEY_CONTRACTOR_TITLE]] = proposal_data

    return baseline_proposal, actual_proposals


def _is_value_zero(value: Any) -> bool:
    """Проверяет, что значение «нулевое» — числом, строкой или None."""
    if value in (None, 0, "0", "0.0", "", "0,0"):
        return True
    return bool(isinstance(value, str) and value.strip().lower() in {"0", "0.0", "0,0", "none"})


def _is_baseline_valid(baseline_proposal: dict[str, Any] | None) -> bool:
    """Есть ли в «Расчетной стоимости» хотя бы одно ненулевое итоговое значение."""
    if not baseline_proposal:
        return False

    summary_data = baseline_proposal.get(JSON_KEY_CONTRACTOR_ITEMS, {}).get(JSON_KEY_CONTRACTOR_SUMMARY, {})
    all_total_values = []
    for summary_block in summary_data.values():
        if isinstance(summary_block, dict):
            total_cost_detail = summary_block.get(JSON_KEY_TOTAL_COST, {})
            if isinstance(total_cost_detail, dict):
                all_total_values.extend(total_cost_detail.values())

    return any(not _is_value_zero(val) for val in all_total_values)


def _clean_deviation_fields(proposals: dict[str, Any]) -> dict[str, Any]:
    """Возвращает копию предложений без полей отклонения от расчётной стоимости."""
    cleaned_proposals = copy.deepcopy(proposals)
    for proposal in cleaned_proposals.values():
        items_data = proposal.get(JSON_KEY_CONTRACTOR_ITEMS, {})
        if not isinstance(items_data, dict):
            continue

        for pos_item in items_data.get(JSON_KEY_CONTRACTOR_POSITIONS, {}).values():
            if isinstance(pos_item, dict):
                pos_item.pop(JSON_KEY_DEVIATION_FROM_CALCULATED_COST, None)

        if JSON_KEY_CONTRACTOR_SUMMARY in items_data:
            items_data[JSON_KEY_CONTRACTOR_SUMMARY].pop(JSON_KEY_DEVIATION_FROM_CALCULATED_COST, None)

    return cleaned_proposals


# --- Основные публичные функции ---


def normalize_lots_json_structure(data: dict[str, Any]) -> dict[str, Any]:
    """Нормализует структуру данных по лотам.

    Для каждого лота: отделяет «Расчетную стоимость», проверяет её значимость,
    при невалидной чистит поля отклонений у подрядчиков, аннотирует позиции
    иерархией и переиндексирует подрядчиков в `contractor_1..N`.

    Args:
        data: исходный словарь тендера/сметы.

    Returns:
        Новый словарь; исходный не изменяется.
    """
    processed_data = copy.deepcopy(data)
    lots_data = processed_data.get(JSON_KEY_LOTS, {})

    for lot_content in lots_data.values():
        proposals_in_lot = lot_content.get(JSON_KEY_PROPOSALS, {})

        baseline_proposal, actual_proposals = _separate_proposals(proposals_in_lot)

        if _is_baseline_valid(baseline_proposal) and baseline_proposal:
            baseline_proposal.pop(JSON_KEY_CONTRACTOR_ADDITIONAL_INFO, None)
            lot_content[JSON_KEY_BASELINE_PROPOSAL] = baseline_proposal
        else:
            lot_content[JSON_KEY_BASELINE_PROPOSAL] = {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}
            actual_proposals = _clean_deviation_fields(actual_proposals)

        reindexed_proposals = {}
        for idx, proposal in enumerate(actual_proposals.values(), 1):
            items = proposal.get(JSON_KEY_CONTRACTOR_ITEMS, {})
            positions = items.get(JSON_KEY_CONTRACTOR_POSITIONS)

            if positions:
                items[JSON_KEY_CONTRACTOR_POSITIONS] = annotate_structure_fields(positions)

            reindexed_proposals[f"{JSON_KEY_CONTRACTOR_INDEX}{idx}"] = proposal

        lot_content[JSON_KEY_PROPOSALS] = reindexed_proposals

    return processed_data


def replace_excel_errors_with_null(data: Any) -> Any:
    """Рекурсивно заменяет строки ошибок Excel на None.

    Ошибка в ячейке — «значения нет», а не значение: в денежном поле строка
    вида `#N/A` была бы неотличима по типу от суммы (деньги в raw_data — тоже
    строки), и фаза 4 узнала бы о ней только на `Decimal(value)`.
    """
    if isinstance(data, dict):
        return {k: replace_excel_errors_with_null(v) for k, v in data.items()}
    if isinstance(data, list):
        return [replace_excel_errors_with_null(item) for item in data]
    if isinstance(data, str) and data.strip().lower() in EXCEL_ERROR_STRINGS:
        return None
    return data


def stringify_temporal_values(data: Any) -> Any:
    """Рекурсивно переводит значения дат и времени в ISO-строки.

    openpyxl отдаёт date-форматированные ячейки объектами `datetime`/`date`/
    `time` (длительности вида `[h]:mm` — `timedelta`), а `json.dumps` их не
    сериализует. Без этой замены контракт «`ParseResult.data` кладётся в jsonb
    без своего энкодера» держался бы на том, что дат во входном файле пока не
    встречалось.
    """
    if isinstance(data, dict):
        return {k: stringify_temporal_values(v) for k, v in data.items()}
    if isinstance(data, list):
        return [stringify_temporal_values(item) for item in data]
    if isinstance(data, dt.datetime | dt.date | dt.time):
        return data.isoformat()
    if isinstance(data, dt.timedelta):
        return str(data)
    return data


def annotate_structure_fields(
    positions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Проставляет позициям `is_chapter` и `chapter_ref`.

    Разделом считается позиция с непустым `chapter_number`. Подраздел "1.1"
    ссылается на "1", раздел верхнего уровня — на None, обычная позиция — на
    текущий активный раздел.

    Args:
        positions: словарь позиций с ключами-номерами в виде строк.

    Returns:
        Новый словарь с аннотированными позициями.

    Raises:
        DataIntegrityError: если какая-то позиция не словарь.
    """
    if not isinstance(positions, dict):
        return {}

    positions_copy = copy.deepcopy(positions)

    try:
        sorted_items: list[tuple[str, dict[str, Any]]] = sorted(positions_copy.items(), key=lambda item: int(item[0]))
    except (ValueError, TypeError):
        log.warning(
            "Не удалось отсортировать позиции по числовым ключам: %s. Логика 'chapter_ref' может быть нарушена.",
            list(positions.keys()),
        )
        sorted_items = list(positions_copy.items())

    current_chapter_number: str | None = None
    for key, pos_item in sorted_items:
        if not isinstance(pos_item, dict):
            raise DataIntegrityError(
                f"Обнаружена некорректная позиция с ключом '{key}'. "
                f"Ожидался словарь, но получен тип {type(pos_item).__name__}."
            )

        section_num = pos_item.get(JSON_KEY_CHAPTER_NUMBER)
        is_chapter = bool(section_num)
        pos_item[JSON_KEY_IS_CHAPTER] = is_chapter

        if is_chapter:
            current_chapter_number = str(section_num)
            if "." in current_chapter_number:
                pos_item[JSON_KEY_CHAPTER_REF] = ".".join(current_chapter_number.split(".")[:-1])
            else:
                pos_item[JSON_KEY_CHAPTER_REF] = None
        else:
            pos_item[JSON_KEY_CHAPTER_REF] = current_chapter_number

    return dict(sorted_items)

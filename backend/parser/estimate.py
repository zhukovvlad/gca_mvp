"""Оркестратор разбора сметы ГП.

Точка входа парсера: XLSX → JSON-структура, которая ложится в
`estimate_raw_data.raw_data` и разбирается импортом фазы 4 (AGENTS.md §5, шаги 2–3).

Соответствует `app/parse.py` исходника, но без всего, что относилось к
тендерному конвейеру: регистрации на Go-сервере, генерации markdown, чанков и
архивации файлов. Здесь только разбор.

Модуль не знает ни о БД, ни о FastAPI — это условие фазы 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import IO, Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from .constants import JSON_KEY_EXECUTOR, JSON_KEY_LOTS
from .layout import check_estimate_layout
from .postprocess import normalize_lots_json_structure, replace_div0_with_null
from .read_contractors import read_contractors
from .read_executer_block import read_executer_block
from .read_headers import read_headers
from .read_lots_and_boundaries import find_lot_starts, read_lots_and_boundaries

log = logging.getLogger(__name__)

# Версия формата разбора. Пишется в `estimate_raw_data.parser_version`
# (AGENTS.md §4): по ней видно, каким кодом получен сохранённый JSON.
#
# Это НЕ `norm_version` из §4: версия нормализации наименований живёт отдельно и
# вводится в фазе 4 вместе с матчингом, потому что её инкремент требует миграции
# перевыпуска ключей `matching_cache` (AGENTS.md §11).
PARSER_VERSION = "1.0.0"


class EstimateParseError(Exception):
    """Файл не разбирается как смета ГП.

    Поднимается только на структурно непригодных файлах. Всё, что можно
    прочитать с оговорками, читается и попадает в `ParseResult.warnings`.
    """


@dataclass(frozen=True)
class ParseResult:
    """Результат разбора одной сметы."""

    data: dict[str, Any]
    """Полная JSON-структура для `estimate_raw_data.raw_data`."""

    parser_version: str = PARSER_VERSION
    """Версия парсера, которой получен `data`."""

    warnings: list[str] = field(default_factory=list)
    """Некритичные расхождения; фаза 4 кладёт их в `import_jobs.warnings`."""


def _select_worksheet(wb: openpyxl.Workbook, warnings: list[str]) -> Worksheet:
    """Возвращает лист со сметой, предупреждая о лишних листах."""
    if not wb.sheetnames:
        raise EstimateParseError("В книге нет ни одного листа.")

    if len(wb.sheetnames) > 1:
        warnings.append(
            f"В книге {len(wb.sheetnames)} листов ({', '.join(wb.sheetnames)}); "
            f"разобран первый — «{wb.sheetnames[0]}»."
        )

    return wb[wb.sheetnames[0]]


def parse_worksheet(ws: Worksheet) -> ParseResult:
    """Разбирает уже открытый лист.

    Отделено от `parse_estimate`, чтобы тесты могли подавать синтетические листы
    без временных файлов.

    Args:
        ws: лист Excel со сметой.

    Returns:
        `ParseResult` с полной структурой и предупреждениями.

    Raises:
        EstimateParseError: не найдена строка заголовков контрагентов, нет
            подрядчиков или нет маркера лота.
    """
    warnings: list[str] = []

    contractors = read_contractors(ws)
    if not contractors:
        raise EstimateParseError(
            "Не найдена строка заголовков контрагентов: ни в одной из строк "
            "4–10 нет ячейки, начинающейся с «Наименование контрагента». "
            "Похоже, это не смета ГП в ожидаемом формате."
        )
    if len(contractors) < 2:
        raise EstimateParseError(
            "Строка заголовков контрагентов найдена, но самого подрядчика в ней нет — "
            "справа от ячейки «Наименование контрагента» пусто."
        )

    lot_starts = find_lot_starts(ws)
    if not lot_starts:
        raise EstimateParseError(
            "Не найден маркер лота: в колонке D нет ячейки, начинающейся с «Лот №». "
            "Без него не определить границы блока позиций."
        )

    warnings.extend(check_estimate_layout(ws, contractors, lot_starts))

    data: dict[str, Any] = {
        **read_headers(ws),
        JSON_KEY_EXECUTOR: read_executer_block(ws),
        JSON_KEY_LOTS: read_lots_and_boundaries(ws),
    }
    data = normalize_lots_json_structure(data)
    data = replace_div0_with_null(data)

    return ParseResult(data=data, warnings=warnings)


def parse_estimate(source: str | IO[bytes]) -> ParseResult:
    """Разбирает XLSX-файл сметы ГП.

    Книга открывается с `data_only=True`: парсер читает кэшированные значения
    формул, самих формул он не видит.

    Args:
        source: путь к файлу либо открытый двоичный поток.

    Returns:
        `ParseResult` с полной структурой и предупреждениями.

    Raises:
        EstimateParseError: файл не разбирается как смета ГП.
    """
    wb = openpyxl.load_workbook(source, data_only=True)
    try:
        warnings: list[str] = []
        ws = _select_worksheet(wb, warnings)
        result = parse_worksheet(ws)
        return ParseResult(
            data=result.data,
            parser_version=result.parser_version,
            warnings=warnings + result.warnings,
        )
    finally:
        wb.close()

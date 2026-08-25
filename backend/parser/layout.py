"""Проверка раскладки колонок сметы ГП.

Смысл колонки блока подрядчика разрешает `resolve_contractor` по подписям
двухъярусной шапки (спека §2.2, §2.6) — здесь этот смысл больше не выводится и
не проверяется. Этот модуль сверяет только НАБОР разрешённых ключей блока с
объявленным эталоном сметы ГП (`GP_EXPECTED_KEYS`) и считает подрядчиков и
лотов.

Опаснее всего первая колонка блока — `suggested_quantity`. На ней держится вес
позиции в средневзвешенной ставке (AGENTS.md §6, вывод фазы 0), то есть вся
сквозная матрица. Поэтому раскладка проверяется явно, а расхождения уходят
предупреждениями в `import_jobs.warnings` (AGENTS.md §5) — они не блокируют
импорт, но видны человеку.

Здесь только предупреждения: семантический контракт (неизвестная пара, повтор
ключа, пропуск обязательного) — отказ, и живёт только в `resolve_contractor`
(спека §2.6). До него же геометрию (объединение заголовка) проверяет
`estimate._validate_contractor_geometry`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_SUGGESTED_QUANTITY,
    TABLE_PARSE_SUGGESTED_QUANTITY,
)
from .resolve_contractor import REQUIRED_COLUMN_KEYS, ResolvedContractor

# Ширина блока подрядчика в смете ГП: J..T — предлагаемое количество,
# цена за единицу ×4, стоимость всего ×4, стоимость за объёмы заказчика,
# комментарий участника (docs/phase0-input-data.md).
#
# Мёртвый код (фича «колонки по заголовкам», спека §2.7): смысл колонок больше
# не выводится из числа колонок. Снесёт задача 5 вместе с
# `find_suggested_quantity_header` ниже — не эта задача (план фичи, задача 3).
GP_CONTRACTOR_COLSPAN = 11


def find_suggested_quantity_header(ws: Worksheet, contractor: dict[str, Any], search_end_row: int) -> str | None:
    """Ищет заголовок первой колонки блока подрядчика.

    Сканируется колонка `contractor["column_start"]` от строки под заголовком
    подрядчика до `search_end_row` (не включая) — то есть шапка таблицы между
    реквизитами контрагента и первой строкой данных.

    Args:
        ws: лист Excel.
        contractor: словарь подрядчика от `read_contractors`.
        search_end_row: первая строка данных (маркер лота).

    Returns:
        Текст последней непустой ячейки в этом диапазоне либо None.
    """
    column = contractor.get("column_start")
    row_start = contractor.get("row_start")
    if column is None or row_start is None:
        return None

    found: str | None = None
    for row in range(row_start + 1, search_end_row):
        value = ws.cell(row=row, column=column).value
        if isinstance(value, str) and value.strip():
            found = value.strip()
    return found


#: Эталон набора ключей сметы ГП (спека §2.5): восемь обязательных денежных
#: плюс три из четырёх опциональных. deviation_from_baseline_cost сюда не
#: входит: его присутствие означает расчётную стоимость, которой у сметы ГП
#: нет (AGENTS.md §4) — лишний ключ даёт предупреждение.
GP_EXPECTED_KEYS = frozenset(REQUIRED_COLUMN_KEYS) | {
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_COMMENT_CONTRACTOR,
}


def check_estimate_layout(
    contractors: Sequence[ResolvedContractor],
    lot_starts: list[dict[str, Any]],
) -> list[str]:
    """Сверяет разрешённые раскладки с эталоном сметы ГП.

    Только предупреждения: файл уже разобран по фактическим подписям, решение
    о нём принимает человек. Отказы живут в resolve_contractor (§2.6).
    Прежняя формулировка «блок занимает N колонок вместо 11» исчезла вместе с
    самой мыслью: число колонок больше ничего не значит.
    """
    warnings: list[str] = []

    if len(contractors) != 1:
        warnings.append(
            f"В смете ГП ожидается один подрядчик, найдено {len(contractors)}. "
            "Разобраны все найденные блоки — проверьте файл."
        )
    if len(lot_starts) != 1:
        warnings.append(
            f"В смете ожидается один лот, найдено {len(lot_starts)}. Проверьте файл."
        )

    for resolved in contractors:
        keys = set(resolved.layout.column_keys)
        missing = sorted(GP_EXPECTED_KEYS - keys)
        extra = sorted(keys - GP_EXPECTED_KEYS)
        if not missing and not extra:
            continue
        parts = []
        if missing:
            parts.append(f"отсутствуют: {', '.join(missing)}")
        if extra:
            parts.append(f"лишние: {', '.join(extra)}")
        title = resolved.geometry.get("value")
        warnings.append(
            f"Набор колонок блока подрядчика «{title}» не совпадает с ожидаемым "
            f"для сметы ГП — {'; '.join(parts)}. Файл разобран по фактическим "
            "подписям шапки; вес позиции в сквозной матрице берётся из "
            f"«{TABLE_PARSE_SUGGESTED_QUANTITY}», проверьте файл."
        )
    return warnings

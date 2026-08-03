"""Проверка раскладки колонок сметы ГП.

Новый модуль фазы 3. Парсер определяет смысл колонок подрядчика **по ширине его
объединённого блока** (`parse_contractor_row.get_column_keys`), а не по тексту
заголовков. Это работает, пока файл выглядит так, как мы замерили, и молча
превращается в мусор, если раскладка поедет: та же ячейка станет читаться как
другое поле.

Опаснее всего первая колонка блока — `suggested_quantity`. На ней держится вес
позиции в средневзвешенной ставке (AGENTS.md §6, вывод фазы 0), то есть вся
сквозная матрица. Поэтому раскладка проверяется явно, а расхождения уходят
предупреждениями в `import_jobs.warnings` (AGENTS.md §5) — они не блокируют
импорт, но видны человеку.

Здесь только предупреждения — то есть только те расхождения, при которых файл
всё-таки разбирается. Ширины блока, для которых раскладка вообще неизвестна,
отсекает `estimate._validate_contractor_blocks` — `EstimateParseError` до начала
разбора. Иначе предупреждение обещало бы импорт, которого не будет.

Вариативность шапки — главный непокрытый риск фазы 3 (один образец,
docs/phase0-input-data.md), и эти предупреждения — то, чем он отслеживается.
"""

from __future__ import annotations

from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from .constants import TABLE_PARSE_SUGGESTED_QUANTITY

# Ширина блока подрядчика в смете ГП: J..T — предлагаемое количество,
# цена за единицу ×4, стоимость всего ×4, стоимость за объёмы заказчика,
# комментарий участника (docs/phase0-input-data.md).
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


def check_estimate_layout(
    ws: Worksheet,
    contractors: list[dict[str, Any]],
    lot_starts: list[dict[str, Any]],
) -> list[str]:
    """Сверяет раскладку листа с ожидаемой раскладкой сметы ГП.

    Проверяется: ровно один подрядчик, ширина его блока = 11 колонок, заголовок
    первой колонки блока — «Предлагаемое количество», ровно один лот.

    Args:
        ws: лист Excel.
        contractors: результат `read_contractors` целиком (нулевой элемент —
            ячейка-маркер, дальше подрядчики).
        lot_starts: результат `find_lot_starts`.

    Returns:
        Список человекочитаемых предупреждений; пустой, если раскладка ожидаемая.
    """
    warnings: list[str] = []

    contractor_headers = contractors[1:]
    if len(contractor_headers) != 1:
        warnings.append(
            f"В смете ожидается один подрядчик, найдено {len(contractor_headers)}. "
            "Импортирован будет каждый найденный блок — проверьте файл."
        )

    if len(lot_starts) != 1:
        warnings.append(
            f"В смете ожидается один лот, найдено {len(lot_starts)}. Проверьте файл."
        )

    if not contractor_headers or not lot_starts:
        return warnings

    first_lot_row = lot_starts[0]["start_row"]

    for contractor in contractor_headers:
        title = contractor.get("value")
        colspan = (contractor.get("merged_shape") or {}).get("colspan", 1)

        if colspan != GP_CONTRACTOR_COLSPAN:
            # Сюда доходят только поддерживаемые ширины (8, 9, 10): раскладка
            # известна, файл разберётся, но это не смета ГП — например, при
            # colspan 8/9 нет колонки «Предлагаемое количество», и весом позиции
            # станет `quantity` через COALESCE (AGENTS.md §6).
            warnings.append(
                f"Блок подрядчика «{title}» занимает {colspan} колонок вместо "
                f"{GP_CONTRACTOR_COLSPAN}, ожидаемых для сметы ГП. Смысл колонок "
                "определяется их числом, поэтому стоимости могли быть прочитаны "
                "не из тех ячеек."
            )
            continue

        header = find_suggested_quantity_header(ws, contractor, first_lot_row)
        if header is None:
            warnings.append(
                f"Не найден заголовок первой колонки блока подрядчика «{title}» — "
                f"ожидался «{TABLE_PARSE_SUGGESTED_QUANTITY}». "
                "Вес позиции в сквозной матрице берётся именно из этой колонки."
            )
        elif not header.lower().startswith(TABLE_PARSE_SUGGESTED_QUANTITY.lower()):
            warnings.append(
                f"Первая колонка блока подрядчика «{title}» озаглавлена «{header}», "
                f"ожидалось «{TABLE_PARSE_SUGGESTED_QUANTITY}». Вес позиции в "
                "сквозной матрице берётся именно из этой колонки — проверьте файл."
            )

    return warnings

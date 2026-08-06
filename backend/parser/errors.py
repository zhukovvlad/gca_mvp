"""Исключения парсера.

Вынесено из `estimate.py` отдельным модулем, потому что `get_lot_positions`
тоже отвергает структурно непригодные файлы, а импорт из `estimate` замкнул бы
цикл: `estimate` → `read_lots_and_boundaries` → `get_proposals` →
`get_lot_positions`. Модуль намеренно ничего не импортирует из пакета — он
нижний слой.
"""

from __future__ import annotations


class EstimateParseError(Exception):
    """Файл не разбирается как смета ГП.

    Поднимается только на структурно непригодных файлах. Всё, что можно
    прочитать с оговорками, читается и попадает в `ParseResult.warnings`.
    """

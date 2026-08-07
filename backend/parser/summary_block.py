"""Ядро разбора блока итогов сметы.

Чистые функции: ни `Worksheet`, ни `openpyxl`, ни БД. Обход листа живёт в
`get_summary`, здесь — только правила (спека Ф4a §2.2–§2.7).

Почему модуль вообще есть: блок итогов — три строки и метка в колонке A, то
есть всё содержательное в нём проверяется без файла. Раньше правила сидели
внутри обхода, и единственным способом их проверить был разбор xlsx.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INITIAL_COST,
    JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    JSON_KEY_VAT_AMOUNT,
    TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST,
    TABLE_PARSE_INITIAL_COST,
    TABLE_PARSE_SUMMARY_EXCLUDING_VAT,
    TABLE_PARSE_SUMMARY_INCLUDING_VAT,
    TABLE_PARSE_SUMMARY_VAT,
)
from .sheet import normalized_cell_text

#: Предел примеров в агрегированном предупреждении. Своя константа, а не импорт
#: из `services.category_resolution`: `parser` не зависит от `services` (условие
#: фазы 3), и инвертировать направление ради одного числа нельзя.
MAX_SUMMARY_WARNING_EXAMPLES = 5

#: Метки, распознаваемые ТОЧНЫМ равенством нормализованных форм.
_EXACT_KEY_BY_LABEL: dict[str, str] = {
    normalized_cell_text(TABLE_PARSE_SUMMARY_INCLUDING_VAT).casefold(): JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    normalized_cell_text(TABLE_PARSE_SUMMARY_VAT).casefold(): JSON_KEY_VAT_AMOUNT,
    normalized_cell_text(TABLE_PARSE_SUMMARY_EXCLUDING_VAT).casefold(): JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
}

#: Тендерные метки: их константы — маркеры для поиска ВНУТРИ строки, а полного
#: текста реального тендерного заголовка у нас нет (спека §1.5 факт 3).
_SUBSTRING_KEY_BY_MARKER: tuple[tuple[str, str], ...] = (
    (TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST, JSON_KEY_DEVIATION_FROM_CALCULATED_COST),
    (TABLE_PARSE_INITIAL_COST, JSON_KEY_INITIAL_COST),
)


@dataclass(frozen=True)
class SummaryRow:
    """Одна физическая строка блока итогов."""

    row: int
    """Номер строки листа — он же источник фолбэк-ключа `merged_{row}`."""

    label: Any
    """Сырое значение ячейки колонки A."""

    values: dict[str, Any] = field(default_factory=dict)
    """Результат `parse_contractor_row` для этой строки."""


@dataclass(frozen=True)
class KeyAssignment:
    """Ключи по одному на строку плюс факты о том, что пошло не так."""

    keys: list[str]
    unrecognized: list[tuple[int, str]]
    duplicated: list[tuple[int, str, int]]


def assign_summary_keys(rows: Sequence[SummaryRow]) -> KeyAssignment:
    """Назначает каждой строке блока ключ, и каждой — свой.

    Три шага (спека §2.2): нормализация, точное равенство для трёх налоговых
    меток (подстрока — для двух тендерных), инъективность. Третий шаг не
    украшение: без него две строки с одинаковой меткой снова затёрли бы друг
    друга, и точность метки коллизию бы не закрыла.

    Предусловие: номера строк во входе (`SummaryRow.row`) попарно различны —
    это физические строки листа, и совпадение двух номеров означает ошибку
    вызывающего кода, а не особенность файла, поэтому такой вход не
    принимается молча.

    Raises:
        ValueError: среди `rows` есть повторяющийся номер строки; сообщение
            называет этот номер.
    """
    seen_rows: set[int] = set()
    for item in rows:
        if item.row in seen_rows:
            raise ValueError(f"Повторяющийся номер строки блока итогов: {item.row}")
        seen_rows.add(item.row)

    keys: list[str] = []
    unrecognized: list[tuple[int, str]] = []
    duplicated: list[tuple[int, str, int]] = []
    owner_row_by_key: dict[str, int] = {}

    for item in rows:
        shown = normalized_cell_text(item.label)
        key = _recognize(shown)

        if key is None:
            unrecognized.append((item.row, shown))
            key = f"merged_{item.row}"
        elif key in owner_row_by_key:
            duplicated.append((item.row, shown, owner_row_by_key[key]))
            key = f"merged_{item.row}"

        owner_row_by_key.setdefault(key, item.row)
        keys.append(key)

    return KeyAssignment(keys=keys, unrecognized=unrecognized, duplicated=duplicated)


def _recognize(shown: str) -> str | None:
    """Ключ по нормализованной метке либо None."""
    folded = shown.casefold()
    exact = _EXACT_KEY_BY_LABEL.get(folded)
    if exact is not None:
        return exact
    for marker, key in _SUBSTRING_KEY_BY_MARKER:
        if marker in folded:
            return key
    return None


def _examples(items: list[str]) -> str:
    """Перечень с усечением — агрегированные предупреждения (Global Constraints)."""
    shown = "; ".join(items[:MAX_SUMMARY_WARNING_EXAMPLES])
    hidden = len(items) - MAX_SUMMARY_WARNING_EXAMPLES
    return f"{shown}{f'; …и ещё {hidden}' if hidden > 0 else ''}"

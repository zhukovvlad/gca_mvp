"""Ядро разбора блока итогов сметы.

Чистые функции: ни `Worksheet`, ни `openpyxl`, ни БД. Обход листа живёт в
`get_summary`, здесь — только правила (спека Ф4a §2.2–§2.7).

Почему модуль вообще есть: блок итогов — три строки и метка в колонке A, то
есть всё содержательное в нём проверяется без файла. Раньше правила сидели
внутри обхода, и единственным способом их проверить был разбор xlsx.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .constants import (
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_INITIAL_COST,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_MATERIALS,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    JSON_KEY_VAT_AMOUNT,
    JSON_KEY_WORKS,
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


#: Денежные колонки блока `total_cost`, по которым идёт сверка.
_MONEY_COLUMNS: tuple[str, ...] = (
    JSON_KEY_MATERIALS,
    JSON_KEY_WORKS,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_TOTAL,
)


@dataclass(frozen=True)
class ArithmeticReport:
    """Результат сверки `including = excluding + vat_amount` по колонкам."""

    broken: list[str]
    """Колонки, где равенство не выполнилось; текст несёт все три числа."""

    unverified: list[str]
    """Колонки, где сверить было нечем: неполная тройка либо негодное значение."""


def to_decimal(value: Any) -> tuple[Decimal | None, str | None]:
    """Безопасная конверсия денежного значения парсера.

    `money_to_json` отдаёт деньги десятичными СТРОКАМИ, а нечисловой текст
    ячейки пропускает как есть, поэтому сложение без явной конверсии не
    определено (спека §2.6).

    Returns:
        `(Decimal, None)` — годное значение;
        `(None, None)` — пусто (`None` либо строка, пустая после нормализации);
        `(None, сырьё)` — негодное: не конвертируется либо не `is_finite()`.
        Негодным считается и `Infinity`: `Infinity == Infinity + 100` истинно,
        то есть без этого фильтра противоречивый файл выглядел бы сошедшимся.
    """
    if value is None:
        return None, None
    shown = normalized_cell_text(value)
    if shown == "":
        return None, None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, shown
    if not number.is_finite():
        return None, shown
    return number, None


def check_arithmetic(lines: Mapping[str, Any]) -> ArithmeticReport:
    """Сверяет `including = excluding + vat_amount` по четырём колонкам.

    Сверка выполняется, только если в блоке присутствуют все три налоговые
    строки. Конверсия и проверка годности идут ДО сложения и сравнения:
    `-Infinity + Infinity` бросает `InvalidOperation` уже на сложении, а `sNaN` —
    на сравнении, поэтому «обернуть сверку в try» фильтр не заменяет.
    """
    broken: list[str] = []
    unverified: list[str] = []

    needed = (JSON_KEY_TOTAL_COST_INCLUDING_VAT, JSON_KEY_VAT_AMOUNT, JSON_KEY_TOTAL_COST_EXCLUDING_VAT)
    if not all(key in lines for key in needed):
        return ArithmeticReport(broken=broken, unverified=unverified)

    blocks = [(lines[key].get(JSON_KEY_TOTAL_COST) or {}) for key in needed]

    for column in _MONEY_COLUMNS:
        raw = [block.get(column) for block in blocks]
        converted = [to_decimal(value) for value in raw]

        bad = [problem for _, problem in converted if problem is not None]
        if bad:
            unverified.append(f"«{column}»: негодное значение {', '.join(repr(item) for item in bad)}")
            continue

        numbers = [number for number, _ in converted]
        if all(number is None for number in numbers):
            continue
        if any(number is None for number in numbers):
            missing = [
                name
                for name, number in zip(("с НДС", "НДС", "без НДС"), numbers, strict=True)
                if number is None
            ]
            unverified.append(f"«{column}»: нет значений — {', '.join(missing)}")
            continue

        including, vat, excluding = numbers
        if including != excluding + vat:
            broken.append(f"«{column}»: с НДС {including}, НДС {vat}, без НДС {excluding}")

    return ArithmeticReport(broken=broken, unverified=unverified)


@dataclass(frozen=True)
class SummaryBlock:
    """Разобранный блок итогов и всё, что о нём надо сказать человеку."""

    lines: dict[str, Any]
    """Ключ → строка блока; форма та же, что у прежнего `get_summary`."""

    warnings: list[str]
    """Parser warnings (сессия A): каждое описывает ФАЙЛ, а не домен."""


def build_summary_block(rows: Sequence[SummaryRow], *, search_start_row: int) -> SummaryBlock:
    """Собирает блок итогов из физических строк и объясняет отклонения.

    Инвариант: `len(lines) == len(rows)` при любом входе (спека §2.3). Пустой
    `rows` означает ровно одно — блок не найден: найденный блок начинается со
    строки с меткой в колонке A, то есть непустой.
    """
    if not rows:
        return SummaryBlock(
            lines={},
            warnings=[
                f"Блок итогов не найден: от строки {search_start_row} до конца листа "
                "нет ни одной строки с объединённой ячейкой в колонке A. "
                "Итоговых сумм у сметы не будет."
            ],
        )

    assignment = assign_summary_keys(rows)
    lines: dict[str, Any] = {}
    for key, item in zip(assignment.keys, rows, strict=True):
        lines[key] = {JSON_KEY_JOB_TITLE: item.label, **item.values}

    warnings: list[str] = []

    if assignment.unrecognized:
        places = [f"строка {row}: «{label}»" for row, label in assignment.unrecognized]
        warnings.append(
            f"В блоке итогов не распознано меток: {len(places)} — {_examples(places)}. "
            "Значения сохранены под техническими ключами `merged_<строка>`; "
            "проверьте форму файла."
        )

    if assignment.duplicated:
        places = [
            f"строка {row}: «{label}» уже была в строке {first} — записана как `merged_{row}`"
            for row, label, first in assignment.duplicated
        ]
        warnings.append(
            f"В блоке итогов метка встретилась дважды: {len(places)} — {_examples(places)}. "
            "Вторая строка не перезаписала первую."
        )

    vat_line = lines.get(JSON_KEY_VAT_AMOUNT)
    if vat_line is not None and _has_no_money(vat_line):
        row = next(item.row for key, item in zip(assignment.keys, rows, strict=True) if key == JSON_KEY_VAT_AMOUNT)
        warnings.append(
            f"В строке «{TABLE_PARSE_SUMMARY_VAT}» (строка {row}) суммы не указаны. "
            "Сумма НДС по этой смете неизвестна."
        )

    if JSON_KEY_TOTAL_COST_INCLUDING_VAT not in lines:
        # Перечень усекается, как и во всех прочих предупреждениях блока: список
        # растёт по числу строк блока, и без усечения это единственное место, где
        # текст предупреждения ничем не ограничен.
        present = _examples([f"«{normalized_cell_text(item.label)}»" for item in rows])
        warnings.append(
            f"Валовое ИТОГО отсутствует: строки «{TABLE_PARSE_SUMMARY_INCLUDING_VAT}» в блоке нет. "
            f"В блоке есть: {present}. Сумма не восстанавливалась сложением — "
            "парсер отдаёт только то, что есть в файле."
        )

    report = check_arithmetic(lines)
    if report.broken:
        warnings.append(
            f"Арифметика НДС не сходится в колонках: {len(report.broken)} — "
            f"{_examples(report.broken)}. Числа взяты из файла и не исправлялись."
        )
    if report.unverified:
        # «неполных ИЛИ негодных»: сюда же попадают `n/a`, `NaN`, `Infinity` —
        # текст обязан покрывать оба входа ветки, иначе он уже собственного
        # условия (та же ошибка, что «НДС не заявлен» в §2.7).
        warnings.append(
            f"Арифметика НДС не проверена из-за неполных или негодных данных: "
            f"{len(report.unverified)} — {_examples(report.unverified)}."
        )

    return SummaryBlock(lines=lines, warnings=warnings)


def _has_no_money(line: Mapping[str, Any]) -> bool:
    """Ни одной годной или хотя бы непустой суммы в строке."""
    block = line.get(JSON_KEY_TOTAL_COST) or {}
    return all(to_decimal(block.get(column)) == (None, None) for column in _MONEY_COLUMNS)

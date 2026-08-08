"""Ядро распознавания ставки НДС из шапки ценового блока подрядчика.

Ставка заявляется суффиксом групповой шапки (спека Ф4б §2.2): полная метка
сравнению не подлежит — метка существует в двух формах, а режим налогообложения
в её головной части переменная. Здесь живут распознавание суффикса,
согласование двух шапок, перекрёстная сверка с блоком итогов и тексты четырёх
предупреждений — то есть всё, что проверяется без файла. Обход листа и чтение
самих ячеек остаются в `get_proposals`.

Ни `Worksheet`, ни `openpyxl`, ни импортов из `services/` — направление
зависимости зафиксировано Ф4a и не меняется. `to_decimal`, `_MONEY_COLUMNS`,
`ARITHMETIC_PRECISION` и `_examples` берутся из `summary_block` импортом, а не
копией: годность денежного значения и состав денежных колонок обязаны иметь
одну правду на весь блок итогов.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

from .constants import JSON_KEY_TOTAL_COST, JSON_KEY_TOTAL_COST_EXCLUDING_VAT, JSON_KEY_VAT_AMOUNT
from .sheet import normalized_cell_text
from .summary_block import _MONEY_COLUMNS, ARITHMETIC_PRECISION, _examples, to_decimal

#: Ставка заявляется суффиксом групповой шапки. Точное равенство полной метке
#: не годится: метка существует в двух формах, а режим налогообложения в её
#: головной части — переменная (спека §2.2). Якорь на конец строки обязателен —
#: без него метка с припиской после процента молча сошла бы за известный формат.
VAT_RATE_SUFFIX_RE = re.compile(r"с учетом ндс\s+(\d+(?:[.,]\d+)?)\s*%\s*$")
VAT_RATE_MIN = Decimal(0)
VAT_RATE_MAX = Decimal(100)

#: Допуск сверки выведенной ставки с заявленной, в процентных пунктах.
#: Обе границы вилки замерены: снизу шум построчного округления — максимум
#: 4.08e-10 п.п. на реальных офертах и 1.98e-10 на fixture; сверху наименьшая
#: реальная разница ставок — 2 п.п. (18 против 20). Константа отделяет
#: известный класс ошибки от известного шума, а не ловит сколь угодно тонкое
#: расхождение (спека §2.5).
VAT_RATE_TOLERANCE = Decimal("0.01")

#: Контекст, в котором выполняется деление при сверке ставки.
#:
#: Строится ЯВНО и не наследует глобальный: `localcontext()` без аргумента
#: копирует текущий контекст ВМЕСТЕ С ЕГО ТРАПАМИ, поэтому включённый где-то
#: `Inexact` или `Rounded` превращал бы штатное деление в «сверка не проведена»
#: — замерено: `1/3` при глобальном трапе на `Inexact` даёт четыре колонки
#: «деление невозможно». Спека §2.4 округление здесь **разрешает** (частное
#: почти никогда не представимо конечной десятичной дробью), и это требование
#: обязано держаться кодом, а не совпадением с умолчаниями интерпретатора.
#:
#: Список `traps` задаёт набор ПОЛНОСТЬЮ: три сигнала включены, все прочие
#: выключены. Зеркально Ф4a, где проверялось сложение и трапы на неточность,
#: наоборот, были обязательны.
#:
#: Объект модуль-уровневый и не мутируется: `localcontext(ctx)` работает с
#: копией переданного контекста.
_DIVISION_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

#: Человекочитаемые названия двух групп колонок ценового блока. Используются
#: только в текстах предупреждений — чтобы «шапки заявляют разные ставки» и
#: «ставка не получена» называли обе группы, а не только их метки (спека §2.7).
#: Постоянны для любого файла и любого лота, поэтому не мешают дедупликации
#: `dict.fromkeys` в `estimate.py` (спека §2.8).
_UNIT_COST_GROUP_TITLE = "Цена за единицу"
_TOTAL_COST_GROUP_TITLE = "Стоимость всего"


@dataclass(frozen=True)
class LabelReading:
    """Результат разбора одной групповой метки ценового блока."""

    rate: Decimal | None
    """Ставка в процентных пунктах либо `None`, если метка её не заявила."""

    shown: str
    """Метка после `normalized_cell_text` — для показа человеку, регистр исходный."""

    problem: str | None
    """Фактическая причина отсутствия ставки; `None`, когда ставка распознана."""


@dataclass(frozen=True)
class DeclaredRate:
    """Ставка, согласованная по двум групповым шапкам одного ценового блока."""

    rate: Decimal | None
    """Ставка в процентных пунктах, только если обе шапки согласились."""

    warnings: list[str]
    """Parser warnings (сессия A); не каскадируют — не более одного элемента."""


def read_label_rate(label: Any) -> LabelReading:
    """Извлекает заявленную ставку НДС из одной групповой метки.

    Нормализация — `normalized_cell_text(...).casefold()`, тот же нормализатор,
    что везде в парсере: схлопывает пробелы (включая неразрывный) и снимает
    регистр до сравнения с регулярным выражением. Метка без распознаваемого
    суффикса, включая метку со словами «без НДС», даёт `rate=None`: «цены без
    НДС» — утверждение о ценах, а не о ставке, подставлять туда ноль было бы
    повторением дефекта, отклонённого ещё в Ф4a (спека §2.2).
    """
    shown = normalized_cell_text(label)
    match = VAT_RATE_SUFFIX_RE.search(shown.casefold())
    if match is None:
        return LabelReading(rate=None, shown=shown, problem="суффикс ставки НДС в метке не найден")

    raw_number = match.group(1)
    rate = Decimal(raw_number.replace(",", "."))
    if rate < VAT_RATE_MIN or rate > VAT_RATE_MAX:
        return LabelReading(
            rate=None,
            shown=shown,
            problem=f"заявленная ставка {raw_number}% вне допустимого диапазона 0..100",
        )
    return LabelReading(rate=rate, shown=shown, problem=None)


def resolve_declared_rate(unit_cost_label: Any, total_cost_label: Any) -> DeclaredRate:
    """Согласует ставку НДС по двум групповым шапкам одного ценового блока.

    Ставка принимается только при согласии обеих шапок (спека §2.3): они
    описывают один и тот же ценовой блок, и расхождение между ними значит, что
    неизвестно, какая из двух — правда, а «не знаем» в этом проекте пишется как
    `None`, а не выбором одной из версий.
    """
    unit_reading = read_label_rate(unit_cost_label)
    total_reading = read_label_rate(total_cost_label)

    if unit_reading.rate is not None and total_reading.rate is not None:
        if unit_reading.rate == total_reading.rate:
            return DeclaredRate(rate=unit_reading.rate, warnings=[])
        return DeclaredRate(rate=None, warnings=[_rates_disagree_warning(unit_reading, total_reading)])

    return DeclaredRate(rate=None, warnings=[_rate_not_obtained_warning(unit_reading, total_reading)])


def _describe_reading(reading: LabelReading) -> str:
    """Фактическое состояние одной прочитанной метки — для текста предупреждения."""
    if reading.problem is not None:
        return reading.problem
    return f"заявлено {reading.rate}%"


def _rate_not_obtained_warning(unit_reading: LabelReading, total_reading: LabelReading) -> str:
    """Текст «ставка не получена»: фактические метки обеих групп и причину.

    Условие охватывает три ветки согласия (§2.3): обе метки молчат, значение
    дала только одна, значение вне `0..100` хотя бы у одной. Во всех трёх текст
    строится одинаково — по фактическому состоянию каждой стороны.
    """
    return (
        "Ставка НДС не получена из шапки ценового блока: "
        f"«{_UNIT_COST_GROUP_TITLE}» («{unit_reading.shown}») — {_describe_reading(unit_reading)}; "
        f"«{_TOTAL_COST_GROUP_TITLE}» («{total_reading.shown}») — {_describe_reading(total_reading)}."
    )


def _rates_disagree_warning(unit_reading: LabelReading, total_reading: LabelReading) -> str:
    """Текст «шапки заявляют разные ставки»: оба значения и обе группы колонок."""
    return (
        "Шапки ценового блока заявляют разные ставки НДС: "
        f"«{_UNIT_COST_GROUP_TITLE}» («{unit_reading.shown}») — {unit_reading.rate}%; "
        f"«{_TOTAL_COST_GROUP_TITLE}» («{total_reading.shown}») — {total_reading.rate}%."
    )


@dataclass(frozen=True)
class RateCheckReport:
    """Результат сверки заявленной ставки НДС с блоком итогов по колонкам `_MONEY_COLUMNS`."""

    mismatched: list[str]
    """Колонки, где выведенная ставка расходится с заявленной больше допуска
    `VAT_RATE_TOLERANCE`, либо НДС заявлен при нулевой базе — оба случая говорят
    о противоречии в файле, а не о нехватке данных для проверки."""

    unverified: list[str]
    """Колонки, где сверить было нечем: неполная пара значений блока итогов,
    негодное значение (с фактическим сырьём) либо деление бросило `DecimalException`."""


@dataclass(frozen=True)
class VatRateResult:
    """Итог Ф4б: заявленная ставка НДС плюс все предупреждения о ней."""

    rate: Decimal | None
    """Ставка в процентных пунктах, только если обе шапки согласились (§2.3)."""

    warnings: list[str]
    """Parser warnings (сессия A). Не каскадируют (§2.7): если ставка не получена,
    сверка с блоком итогов не запускается вовсе, и «сверка не проведена» не
    добавляется — второе предупреждение не несло бы ничего сверх первого."""


def check_rate_against_summary(rate: Decimal, lines: Mapping[str, Any]) -> RateCheckReport:
    """Сверяет заявленную ставку с отношением `vat_amount / total_cost_excluding_vat * 100`.

    Перекрёстная проверка, не источник значения (спека §2.4): эталон приезжает
    из блока итогов, разобранного другим кодом и по другим правилам, поэтому
    одна ошибка не сдвигает обе стороны сравнения сразу
    ([verifying-guards.md], слой 5).

    Сверка вообще не запускается, если в `lines` нет обоих ключей `vat_amount`
    и `total_cost_excluding_vat` — и предупреждения при этом не даёт. Это не
    пропуск, а отказ от второго сообщения об уже названном факте: о неполноте
    самого блока итогов уже говорят предупреждения Ф4a (спека §2.6). Та же
    форма, что у `check_arithmetic`, которая при неполном составе строк
    возвращает пустой отчёт.

    Годность каждого значения определяется ТЕМ ЖЕ `to_decimal`, что у Ф4a —
    одна правда о годности на весь блок итогов. Деление выполняется в
    `_DIVISION_CONTEXT` — контексте, заданном явно, а не унаследованном от
    глобального: точность `ARITHMETIC_PRECISION`, трапы ровно на `Overflow`,
    `DivisionByZero` и `InvalidOperation`, всё остальное выключено. Трапов на
    `Inexact` и `Rounded` нет намеренно — частное почти никогда не представимо
    конечной десятичной дробью, и деление неточно по своей природе (спека §2.4).
    Глобальный контекст приложения при этом не меняется.
    """
    mismatched: list[str] = []
    unverified: list[str] = []

    needed = (JSON_KEY_VAT_AMOUNT, JSON_KEY_TOTAL_COST_EXCLUDING_VAT)
    if not all(key in lines for key in needed):
        return RateCheckReport(mismatched=mismatched, unverified=unverified)

    vat_block = lines[JSON_KEY_VAT_AMOUNT].get(JSON_KEY_TOTAL_COST) or {}
    excluding_block = lines[JSON_KEY_TOTAL_COST_EXCLUDING_VAT].get(JSON_KEY_TOTAL_COST) or {}

    for column in _MONEY_COLUMNS:
        vat_number, vat_problem = to_decimal(vat_block.get(column))
        excluding_number, excluding_problem = to_decimal(excluding_block.get(column))

        problems = [item for item in (vat_problem, excluding_problem) if item is not None]
        if problems:
            unverified.append(f"«{column}»: негодное значение {', '.join(repr(item) for item in problems)}")
            continue

        if vat_number is None and excluding_number is None:
            continue

        if vat_number is None or excluding_number is None:
            missing = [
                name
                for name, number in zip(("НДС", "без НДС"), (vat_number, excluding_number), strict=True)
                if number is None
            ]
            unverified.append(f"«{column}»: нет значения — {', '.join(missing)}")
            continue

        if vat_number == 0 and excluding_number == 0:
            continue

        if excluding_number == 0:
            mismatched.append(f"«{column}»: НДС {vat_number} заявлен при нулевой базе — противоречие в файле")
            continue

        try:
            with localcontext(_DIVISION_CONTEXT):
                derived = vat_number / excluding_number * 100
                difference = abs(derived - rate)
        except DecimalException:
            unverified.append(f"«{column}»: деление невозможно — НДС {vat_number}, без НДС {excluding_number}")
            continue

        if difference > VAT_RATE_TOLERANCE:
            mismatched.append(
                f"«{column}»: заявлено {rate}%, выведено из блока итогов {derived}%, разница {difference} п.п."
            )

    return RateCheckReport(mismatched=mismatched, unverified=unverified)


def build_vat_rate(unit_cost_label: Any, total_cost_label: Any, lines: Mapping[str, Any]) -> VatRateResult:
    """Собирает итог Ф4б: согласие двух шапок, затем сверка с блоком итогов.

    Порядок задан некаскадированием (спека §2.7): если ставка не получена —
    обе метки молчат, значение дала только одна, значение вне диапазона либо
    шапки заявляют разные ставки, — сверка не запускается вовсе, и функция
    выходит с предупреждениями `resolve_declared_rate`. Иначе выполняется
    сверка, и её предупреждения (если есть) добавляются к результату.
    """
    declared = resolve_declared_rate(unit_cost_label, total_cost_label)
    if declared.rate is None:
        return VatRateResult(rate=None, warnings=declared.warnings)

    warnings: list[str] = list(declared.warnings)
    report = check_rate_against_summary(declared.rate, lines)

    if report.mismatched:
        warnings.append(
            f"Заявленная ставка НДС расходится с блоком итогов: {len(report.mismatched)} — "
            f"{_examples(report.mismatched)}."
        )
    if report.unverified:
        warnings.append(
            f"Сверка ставки НДС с блоком итогов не проведена: {len(report.unverified)} — "
            f"{_examples(report.unverified)}."
        )

    return VatRateResult(rate=declared.rate, warnings=warnings)

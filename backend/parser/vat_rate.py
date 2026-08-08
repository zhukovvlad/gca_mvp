"""Ядро распознавания ставки НДС из шапки ценового блока подрядчика.

Ставка заявляется суффиксом групповой шапки (спека Ф4б §2.2): полная метка
сравнению не подлежит — метка существует в двух формах, а режим налогообложения
в её головной части переменная. Модуль читает только текст двух ячеек и решает,
о чём они говорят; обход листа и сверка с блоком итогов сюда не входят (задача 4).
Ни `Worksheet`, ни `openpyxl`, ни импортов из `services/` — направление
зависимости зафиксировано Ф4a и не меняется.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .sheet import normalized_cell_text

#: Ставка заявляется суффиксом групповой шапки. Точное равенство полной метке
#: не годится: метка существует в двух формах, а режим налогообложения в её
#: головной части — переменная (спека §2.2). Якорь на конец строки обязателен —
#: без него метка с припиской после процента молча сошла бы за известный формат.
VAT_RATE_SUFFIX_RE = re.compile(r"с учетом ндс\s+(\d+(?:[.,]\d+)?)\s*%\s*$")
VAT_RATE_MIN = Decimal(0)
VAT_RATE_MAX = Decimal(100)

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

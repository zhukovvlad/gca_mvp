"""Числа и диапазоны значений параметров семьи (спека 3в §2.4, §2.6).

Чистые функции без базы данных и без `float`: границы и числа — `Decimal`.
Одна реализация разбора числа и отнесения к диапазону служит и ответу модели
(запись частичного ответа), и перестройке значений параметра.

Диапазон — полуинтервал `[lo, hi)`: нижняя граница входит, верхняя нет. У
крайнего «до …» нижней границы нет (`lo is None`), у «… и более» нет верхней
(`hi is None`).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

#: Строка правила в разделе параметра-диапазона блока схемы запроса значений:
#: значения — диапазоны, число ищется в наименовании или разделах, нижняя
#: граница входит, верхняя нет, нет числа — `none`. Роль параметра в запрос не
#: попадает.
RANGE_RULE_LINE: Final = (
    "Значения этого параметра — диапазоны чисел. Найди число в наименовании или в "
    "разделах и выбери диапазон, в который оно попадает: нижняя граница входит в "
    "диапазон, верхняя нет. Если числа нет — \"kind\": \"none\"."
)

_EN_DASH: Final = "–"

# Одно слово единицы: буквы, необязательная цифра степени («м3», «м²»); через «/»
# — второе слово («кг/м3»).
_UNIT_WORD = r"[^\W\d_]+[\d²³]?"
_NUMBER_RE: Final = re.compile(
    rf"(?P<number>\d+(?:[.,]\d+)?)(?:\s*{_UNIT_WORD}(?:/{_UNIT_WORD})*)?"
)


@dataclass(frozen=True)
class RangeValue:
    """Значение диапазона: подпись и границы. `lo is None` — «до …»,
    `hi is None` — «… и более»."""

    label: str
    lo: Decimal | None
    hi: Decimal | None


def _number_text(number: Decimal) -> str:
    """Число без лишних нулей и с десятичной запятой: `120.50` -> «120,5»."""
    normalized = number.normalize()
    return format(normalized, "f").replace(".", ",")


def parse_single_number(text: str) -> Decimal | None:
    """Единственное число в тексте ответа: запятая или точка как разделитель,
    после числа необязательное слово единицы («4 мм», «131,73 кг/м3», «5,1»).
    Любое другое — `None`: «до 100 мм», «от 40 мм», «40-50», «1/2», пустая
    строка, «В25»."""
    match = _NUMBER_RE.fullmatch(text.strip())
    if match is None:
        return None
    return Decimal(match.group("number").replace(",", "."))


def build_range_values(start: Decimal, stop: Decimal, step: Decimal) -> tuple[RangeValue, ...]:
    """Значения диапазона по `{from, to, step}`: «до `start`», внутренние
    «`a`–`b`» с шагом `step` (последнее упирается в `stop`), «`stop` и более».

    Raises:
        ValueError: `start >= stop` или `step <= 0`.
    """
    if start >= stop:
        raise ValueError("начало диапазона должно быть меньше конца")
    if step <= 0:
        raise ValueError("шаг должен быть положительным")
    values = [RangeValue(f"до {_number_text(start)}", None, start)]
    lower = start
    while lower < stop:
        upper = min(lower + step, stop)
        values.append(
            RangeValue(f"{_number_text(lower)}{_EN_DASH}{_number_text(upper)}", lower, upper)
        )
        lower = upper
    values.append(RangeValue(f"{_number_text(stop)} и более", stop, None))
    return tuple(values)


def _holds(value: RangeValue, number: Decimal) -> bool:
    return (value.lo is None or number >= value.lo) and (value.hi is None or number < value.hi)


def range_for_number(values: Sequence[RangeValue], number: Decimal) -> RangeValue:
    """Значение, которому принадлежит число: нижняя граница включается, верхняя
    нет (`140` — «140–160», `139,99` — «120–140»).

    Raises:
        ValueError: ни одно значение не содержит число (набор не покрывает ось).
    """
    for value in values:
        if _holds(value, number):
            return value
    raise ValueError(f"число {number} не входит ни в один диапазон")


def containing_range(
    values: Sequence[RangeValue], lo: Decimal | None, hi: Decimal | None
) -> RangeValue | None:
    """Единственное значение, целиком содержащее диапазон `[lo, hi)` (`None`
    у границы — бесконечность); `None`, если такого нет."""
    for value in values:
        lower_ok = value.lo is None or (lo is not None and value.lo <= lo)
        upper_ok = value.hi is None or (hi is not None and hi <= value.hi)
        if lower_ok and upper_ok:
            return value
    return None

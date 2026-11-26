"""Шкала значений параметра семьи (спека ближайшего значения §1).

Параметр — шкала, если все его значения одной формы «приставка? число единица?» с положительным
числом: «B25, B30», «200 мм, 250 мм», «EI60, EI90». Чистая функция без `db`: на вход тексты
значений одного параметра, на выход число каждого значения либо «не шкала».
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

#: Приставка — подряд символы без цифр и пробелов; число — цифры с необязательной дробной частью
#: через точку или запятую; единица — хвост без цифр.
_PARSE = re.compile(r"\s*(?P<prefix>[^\d\s]*)\s*(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[^\d]*)")

#: Знаки составных и диапазонных записей: «600×600», «50-60», «100/200», «2*3» (без учёта регистра).
_FORBIDDEN_SIGNS = frozenset("+×xх*-–—/")  # х — кириллическая «х»

#: Кириллические «в» и «м» в приставке приводятся к латинским «b» и «m»: «В40» и «B40» — одно.
_LATIN_OF = str.maketrans({"в": "b", "м": "m"})


@dataclass(frozen=True)
class Scale:
    #: value_id -> число значения
    numbers: Mapping[int, Decimal]
    #: value_id -> текст значения
    texts: Mapping[int, str]


def _forbidden(text: str) -> bool:
    folded = text.casefold()
    if any(sign in folded for sign in _FORBIDDEN_SIGNS):
        return True
    words = folded.split()
    return "или" in words or (bool(words) and words[0] in ("до", "от"))


def scale_numbers(values: Sequence[tuple[int, str]]) -> Scale | None:
    """Шкала по значениям параметра `(value_id, текст)`; `None` — параметр не шкала."""
    if len(values) < 2:
        return None
    numbers: dict[int, Decimal] = {}
    shapes: set[tuple[str, str]] = set()
    for value_id, text in values:
        if _forbidden(text):
            return None
        match = _PARSE.fullmatch(text)
        if match is None:
            return None
        number = Decimal(match["number"].replace(",", "."))
        if number <= 0:
            return None
        shapes.add((match["prefix"].casefold().translate(_LATIN_OF), match["unit"].casefold().strip(" .")))
        if len(shapes) > 1:
            return None
        numbers[value_id] = number
    return Scale(numbers=numbers, texts={value_id: text for value_id, text in values})

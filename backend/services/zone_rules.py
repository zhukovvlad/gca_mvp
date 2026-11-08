"""Правило зоны цепочки разделов (спека лестницы аналогов §2.4).

Чистые функции без базы: ключ цепочки и зона по названиям разделов. Зона —
свойство цепочки, а не строки: наименование строки правило не смотрит.

Названия просматриваются от ближней главы к корню. Первое название, в котором
совпал ровно один из трёх шаблонов, даёт зону; название, где совпали два или три,
останавливает просмотр без ответа (выше может стоять однозначное название, но
оно уже не отвечает за ближний раздел). Зону `whole` правило не даёт: её ставит
модель или человек.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final, Literal

Zone = Literal["underground", "aboveground", "territory", "whole"]

#: Разделитель названий в ключе цепочки.
CHAIN_SEPARATOR: Final = " / "

_WHITESPACE = re.compile(r"\s+")

# Шаблоны §2.4, по ключу цепочки (нижний регистр, `ё` -> `е`, пробелы схлопнуты).
_UNDERGROUND = re.compile(
    r"подзем|подвал|паркинг|автостоян|ниже\s*отм|минус\s*\d|-\s*\d\s*эт"
)
_ABOVEGROUND = re.compile(r"надзем|выше\s*отм|этаж|кровл|чердак|фасад|мансард")
_TERRITORY = re.compile(
    r"благоустр|территор|двор|площадк|озелен|дорог|проезд|тротуар|\bмаф\b|газон"
    r"|наружн\w* освещ|водоотвод|покрыти\w* по грунту|парк"
)

_TEMPLATES: Final[tuple[tuple[Zone, re.Pattern[str]], ...]] = (
    ("underground", _UNDERGROUND),
    ("aboveground", _ABOVEGROUND),
    ("territory", _TERRITORY),
)


def normalize_title(title: str) -> str:
    """Название раздела в виде ключа: нижний регистр, `ё` -> `е`, любой пробельный
    символ (в том числе неразрывный) — пробел, пробелы схлопнуты, края срезаны."""
    return _WHITESPACE.sub(" ", title.lower().replace("ё", "е")).strip()


def chain_key(titles: Sequence[str]) -> str:
    """Ключ цепочки: нормализованные названия сверху вниз через `" / "`."""
    return CHAIN_SEPARATOR.join(normalize_title(title) for title in titles)


def rule_zone(titles: Sequence[str]) -> Zone | None:
    """Зона по правилу §2.4; `None` — правило не ответило (нет совпадений либо
    ближайшее совпавшее название несёт два или три шаблона)."""
    for title in reversed(titles):
        text = normalize_title(title)
        matched = [zone for zone, pattern in _TEMPLATES if pattern.search(text)]
        if len(matched) == 1:
            return matched[0]
        if matched:
            return None
    return None

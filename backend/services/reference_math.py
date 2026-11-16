"""Арифметика пересчёта справочных аналогов (спека справочных аналогов §2.7, §2.8, §2.10, §2.11, §2.13).

Чистые функции без базы: число из значения параметра, множитель `N`, его печать,
разбор ручного `N`, ядро пометки выброса и свёртка строк. Деньги и множители —
`Decimal` в контексте `price_basis._CONTEXT`, без промежуточного округления;
округляется только печать `N`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Final, Literal, TypeVar

from services.parameter_ranges import parse_single_number
from services.price_basis import _CONTEXT, median, outside

LinkKind = Literal["thickness", "width", "density", "constant"]

#: (меньшая, большая) единицы вида по `symbol`; у `constant` пары нет.
UNIT_PAIRS: Mapping[LinkKind, tuple[str, str]] = {
    "thickness": ("м²", "м³"),
    "width": ("м", "м²"),
    "density": ("м³", "т"),
}

_BASIS_WORD: Final[Mapping[str, str]] = {
    "thickness": "толщина",
    "width": "ширина",
    "density": "плотность",
}

_LENGTH_SCALE: Final[Mapping[str, Decimal]] = {
    "мм": Decimal("0.001"),
    "см": Decimal("0.01"),
    "м": Decimal(1),
}
_DENSITY_SCALE: Final[Mapping[str, Decimal]] = {
    "т/м3": Decimal(1),
    "кг/м3": Decimal("0.001"),
    "г/см3": Decimal(1),
}
_SCALE: Final[Mapping[str, Mapping[str, Decimal]]] = {
    "thickness": _LENGTH_SCALE,
    "width": _LENGTH_SCALE,
    "density": _DENSITY_SCALE,
}

_NOT_A_NUMBER_SOURCES: Final = frozenset({"path_conflict", "off_list"})
_TEXT_RE: Final = re.compile(r"\s*(?P<number>[-−]?\d+(?:[.,]\d+)?)\s*(?P<unit>.*?)\s*")
_MANUAL_RE: Final = re.compile(r"\d+(?:\.\d{1,6})?")
_MANUAL_MAX: Final = Decimal(10) ** 6
_PRINT_STEP: Final = Decimal("0.0001")


@dataclass(frozen=True)
class LinkNumber:
    x: Decimal  # в метрах | т/м³
    basis: str  # «толщина 100 мм»


@dataclass(frozen=True)
class Conversion:
    method: Literal["link", "manual"]
    factor: Decimal  # точный N
    basis: str
    link_id: int | None


def _unit_key(unit: str) -> str:
    return unit.strip().lower().replace("³", "3").replace("²", "2")


def link_number(
    kind: LinkKind,
    value_text: str | None,
    parameter_name: str,
    *,
    source: str | None,
    composite: bool,
) -> LinkNumber | None:
    """`x` строки из значения параметра её стороны связи; `None` — «не число».

    Единица — последнее слово текста после числа, иначе часть имени параметра после
    последней запятой. В основании `n` пишется с десятичной запятой, единица остаётся как в источнике."""
    if kind not in _SCALE or composite or source in _NOT_A_NUMBER_SOURCES or not value_text:
        return None
    number = parse_single_number(value_text)
    if number is None or number <= 0:
        return None
    match = _TEXT_RE.fullmatch(value_text)
    if match is None:
        return None
    unit = match.group("unit")
    if not unit and "," in parameter_name:
        unit = parameter_name.rsplit(",", 1)[1].strip()
    scale = _SCALE[kind].get(_unit_key(unit))
    if scale is None:
        return None
    with localcontext(_CONTEXT):
        x = number * scale
    return LinkNumber(x=x, basis=f"{_BASIS_WORD[kind]} {match.group('number').replace('.', ',')} {unit}")


def link_factor(kind: LinkKind, x: Decimal, *, contract_unit_is_smaller: bool) -> Decimal:
    """`N` вида с параметром: единица строки договора меньшая → `x`, большая → `1 / x`."""
    if kind not in UNIT_PAIRS:
        raise ValueError(f"у вида {kind!r} нет параметра")
    if contract_unit_is_smaller:
        return x
    with localcontext(_CONTEXT):
        return Decimal(1) / x


def constant_factor(factor: Decimal, *, from_is_contract_family: bool) -> Decimal:
    """`N` постоянной связи: семья договора — левая часть равенства → `factor`, иначе `1 / factor`."""
    if from_is_contract_family:
        return factor
    with localcontext(_CONTEXT):
        return Decimal(1) / factor


def units_equal(contract_unit_id: int | None, kp_unit_id: int | None) -> bool:
    """Единицы строки договора и строки КП равны: обе заданы и совпали по id каталога (спека §2.13).
    Пересчёт нужен всегда, когда это ложно, в том числе когда одна из единиц пуста."""
    return contract_unit_id is not None and contract_unit_id == kp_unit_id


def price_in_kp_unit(price: Decimal, factor: Decimal) -> Decimal:
    """Цена в единице строки КП — цена ÷ `N`, по точному `N`."""
    with localcontext(_CONTEXT):
        return price / factor


def format_factor(n: Decimal) -> tuple[str, bool]:
    """Печать `N`: до четырёх знаков, без хвостовых нулей, десятичная запятая; второе
    значение — округление изменило число (печатать «≈»)."""
    with localcontext(_CONTEXT):
        rounded = n.quantize(_PRINT_STEP, rounding=ROUND_HALF_UP)
    text = format(rounded.normalize(), "f").replace(".", ",")
    return text, rounded != n


def factor_shown(n: Decimal) -> str:
    """Печатная форма `N` для ответов API (спека справочных аналогов §2.8): запятая, до 4 знаков,
    «≈ » перед числом, если округление его изменило."""
    text, approximate = format_factor(n)
    return f"≈ {text}" if approximate else text


def parse_manual_factor(text: str) -> Decimal | None:
    """Ручной `N` (§2.13): десятичная запятая или точка, > 0, не более 6 знаков, ≤ 10⁶."""
    cleaned = text.strip().replace(",", ".")
    if _MANUAL_RE.fullmatch(cleaned) is None:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0 or value > _MANUAL_MAX:
        return None
    return value


def outlier_ratios_of(prices: Sequence[tuple[int, Decimal]]) -> dict[int, Decimal | None]:
    """position_item_id -> отношение цены к медиане цен остальных, если оно за границами
    (выброс); иначе `None`. Один элемент выбросом не бывает."""
    ratios: dict[int, Decimal | None] = {}
    with localcontext(_CONTEXT):
        for index, (pid, price) in enumerate(prices):
            ratio: Decimal | None = None
            if len(prices) >= 2:
                others = [p for i, (_, p) in enumerate(prices) if i != index]
                candidate_ratio = price / median(others)
                if outside(candidate_ratio):
                    ratio = candidate_ratio
            ratios[pid] = ratio
    return ratios


@dataclass(frozen=True)
class FoldKey:
    """Форма ключа свёртки: строки сворачиваются, только когда равны все поля."""

    contract: Hashable
    title: str
    unit_id: int | None
    price: Decimal
    factor: Decimal
    basis: str
    zone: str | None
    mark: Decimal | None  # отношение выброса; `None` — пометки нет


RowT = TypeVar("RowT")


def fold_rows(
    rows: Sequence[RowT], key: Callable[[RowT], Hashable], pid: Callable[[RowT], int]
) -> list[tuple[RowT, tuple[int, ...]]]:
    """Свёртка строк с равным ключом: (представитель, id по возрастанию).

    Представитель — строка с наименьшим `pid`. Порядок результата — порядок первого
    появления каждой группы во входе: вызывающий сортирует вход заранее."""
    groups: dict[Hashable, list[RowT]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return [
        (min(group, key=pid), tuple(sorted(pid(r) for r in group))) for group in groups.values()
    ]

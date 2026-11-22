"""Правило «строка похожа на комплект» (спека `2026-11-22-lump-sum-lines-design.md` §1).

Чистый модуль: ни базы, ни SQLAlchemy. Загрузчики (`crud/lump_sum.py`, загрузчики сравнения)
готовят входы — цену, вес, базу НДС, поправку, обычные ставки, — а `lump_check` отвечает одной
функцией на всех поверхностях (спека §1 п. 6). Вариант, глава, `SYSTEM`, вид сметы — фильтр
вызывающего: функция отвечает `None` только по цене, весу, базе НДС и единице.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from money.price import is_price, is_weight
from money.vat import gross_to_net

#: Мерные единицы по `units_of_measure.code`; остальные коды — штучные.
MEASURE_UNIT_CODES: frozenset[str] = frozenset({"M", "M2", "M3", "TON", "KG"})
MEASURE_THRESHOLD = Decimal(5)
PIECE_THRESHOLD = Decimal(50)
#: Пул ключа меньше этого обычной ставки не имеет.
TYPICAL_MIN_LINES = 3

LumpVerdict = Literal["lump", "rate"]

_ONE = Decimal(1)


@dataclass(frozen=True)
class TypicalRate:
    net: Decimal
    lines: int


@dataclass(frozen=True)
class TypicalRates:
    rates: Mapping[tuple[int, int], TypicalRate]   # (family_id, unit_id)
    unit_codes: Mapping[int, str]                  # unit_id -> units_of_measure.code
    unit_labels: Mapping[int, str]                 # unit_id -> подпись единицы


@dataclass(frozen=True)
class LumpOverride:
    verdict: LumpVerdict
    set_by_id: int
    set_by_email: str
    set_at: datetime


@dataclass(frozen=True)
class LumpCheck:
    rule: bool                       # ответ правила
    override: LumpOverride | None    # поправка администратора
    is_lump: bool                    # итог: поправка бьёт правило
    measure: bool                    # единица мерная
    unit_id: int
    typical: TypicalRate | None
    net: Decimal                     # цена строки нетто
    threshold: Decimal               # порог единицы строки


def lump_check(
    *,
    family_id: int,
    unit_id: int | None,
    unit_price: Decimal | None,
    weight: Decimal | None,
    vat_base: Decimal | None,
    override: LumpOverride | None,
    rates: TypicalRates,
) -> LumpCheck | None:
    """Решение «комплект или ставка» по готовым входам; `None` — правило неприменимо по цене,
    весу, базе НДС или единице (строка остаётся как сегодня)."""
    if unit_id is None or vat_base is None:
        return None
    if unit_price is None or not is_price(unit_price):
        return None
    if weight is None or not is_weight(weight):
        return None
    net = gross_to_net(unit_price, vat_base)
    measure = rates.unit_codes[unit_id] in MEASURE_UNIT_CODES
    threshold = MEASURE_THRESHOLD if measure else PIECE_THRESHOLD
    typical = rates.rates.get((family_id, unit_id))
    if weight != _ONE:
        rule = False
    elif typical is not None:
        rule = net >= threshold * typical.net
    else:
        rule = measure
    is_lump = rule if override is None else override.verdict == "lump"
    return LumpCheck(
        rule=rule,
        override=override,
        is_lump=is_lump,
        measure=measure,
        unit_id=unit_id,
        typical=typical,
        net=net,
        threshold=threshold,
    )

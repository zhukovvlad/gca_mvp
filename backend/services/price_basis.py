"""Общее ядер «Нашей цены» (4а) и лестницы аналогов работ.

Перенесено из `services/our_price.py` без изменения поведения: контекст точности,
границы расхождения единиц, медиана, проверка «за границами» и договор истории.
Модуль лист в графе зависимостей: из `services` он не импортирует ничего.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from money.inflation import YearMonth
from parser.summary_block import ARITHMETIC_PRECISION

#: Границы отношения цены к медиане, за которыми единицы учёта считаются разными.
MISMATCH_HIGH: Decimal = Decimal(5)
MISMATCH_LOW: Decimal = Decimal("0.2")

_TWO = Decimal(2)

#: Контекст явный и не наследует глобальный: промежуточных округлений в
#: расчёте нет, а ловушки включены, чтобы деление на ноль не прошло молча.
_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)


@dataclass(frozen=True)
class HistoryContract:
    contract_id: int
    estimate_id: int
    number: str
    object_title: str
    year: int
    rate_class_id: int
    rate_class_title: str
    contractor_id: int
    contractor_title: str
    area: Decimal | None
    #: Период цены последней сметы договора (лестница работ); `None` — даты нет.
    period: YearMonth | None = None


def median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / _TWO


def outside(ratio: Decimal) -> bool:
    """Границы `5` и `0.2` не включены."""
    return ratio > MISMATCH_HIGH or ratio < MISMATCH_LOW

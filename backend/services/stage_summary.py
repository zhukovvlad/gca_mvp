"""Свод по этапам одного участника — чистый расчёт (спека
2026-08-27-stage-summary-design.md §2.5–§2.13, §2.16).

Модуль чистый: ни Session, ни ORM, ни импортов из `crud` — литералы на входе,
литералы на выходе; чтение БД и сборка JSON — `crud/stage_summary.py`.
Деление — только `percent_change`, и оно достижимо ТОЛЬКО при базе > 0 (§2.6):
`DivisionByZero` здесь — дефект расчёта, а не отказ пользователю.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from parser.summary_block import ARITHMETIC_PRECISION

STATE_AMOUNT = "amount"
STATE_REMOVED = "removed"
STATE_NOT_EVALUATED = "not_evaluated"
STATE_ABSENT = "absent"

KIND_PERCENT = "percent"
KIND_ABS_ONLY = "abs_only"
KIND_APPEARED = "appeared"
KIND_REAPPEARED = "reappeared"
KIND_REMOVED = "removed"
KIND_DISAPPEARED = "disappeared"
KIND_NONE = "none"

DIR_UP = "up"
DIR_DOWN = "down"
DIR_FLAT = "flat"

REASON_FIRST_COLUMN = "first_column"
REASON_UNKNOWN_VAT_BASE = "unknown_vat_base"
REASON_NO_AMOUNTS = "no_amounts"
REASON_UNALLOCATED = "unallocated"
REASON_ABSENT_ENDPOINT = "absent_endpoint"

_HUNDRED = Decimal(100)
#: Тот же приём, что `money.vat._VAT_CONTEXT` и `crud.comparison._DIV_CONTEXT`:
#: явные трапы БЕЗ Inexact. Приватный контекст соседа не импортируется.
_DIV_CONTEXT = Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])


@dataclass(frozen=True)
class CellInput:
    gross: Decimal | None
    additional_works_gross: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int


@dataclass(frozen=True)
class Change:
    kind: str
    value: Decimal | None
    direction: str | None
    reason: str | None


@dataclass(frozen=True)
class Contribution:
    value: Decimal | None
    direction: str | None
    reason: str | None


_NONE_CHANGE = Change(KIND_NONE, None, None, REASON_NO_AMOUNTS)


def cell_states(gross_by_column: Sequence[Decimal | None]) -> list[str]:
    """§2.5: `removed` — по ВСЕМ предыдущим колонкам пути, не по предыдущему шагу."""
    states: list[str] = []
    priced_before = False
    for gross in gross_by_column:
        if gross is None:
            states.append(STATE_ABSENT)
        elif gross != 0:
            states.append(STATE_AMOUNT)
            priced_before = True
        else:
            states.append(STATE_REMOVED if priced_before else STATE_NOT_EVALUATED)
    return states


def direction_of(delta: Decimal) -> str:
    """Направление — по величине, три состояния; равенство — `flat`, не рост."""
    if delta > 0:
        return DIR_UP
    if delta < 0:
        return DIR_DOWN
    return DIR_FLAT


def percent_change(start: Decimal, end: Decimal) -> Decimal:
    """§2.6: процент считается только при строго положительной базе; при
    `start <= 0` — `AssertionError`, деление здесь недостижимо, а не «безопасно
    обработано» (см. докстроку модуля)."""
    assert start > 0, "percent_change достижим только при положительной базе (§2.6)"
    with localcontext(_DIV_CONTEXT):
        return (end / start - 1) * _HUNDRED


def change_between(prev_state: str, cur_state: str, prev_shown: Decimal | None, cur_shown: Decimal | None,
                   *, unavailable_reason: str | None) -> Change:
    """§2.6: матрица переходов между состояниями клетки; несёт контракт
    достижимости деления — `percent_change` вызывается только из ветки
    `amount → amount` и только когда `prev_shown > 0`, иначе величина считается
    вычитанием (`abs_only`), без обращения к `percent_change`."""
    if unavailable_reason is not None:
        return Change(KIND_NONE, None, None, unavailable_reason)
    if prev_state == STATE_AMOUNT and cur_state == STATE_AMOUNT:
        assert prev_shown is not None and cur_shown is not None
        if prev_shown > 0:
            value = percent_change(prev_shown, cur_shown)
        else:
            value = cur_shown - prev_shown
            return Change(KIND_ABS_ONLY, value, direction_of(value), None)
        return Change(KIND_PERCENT, value, direction_of(cur_shown - prev_shown), None)
    if prev_state == STATE_AMOUNT and cur_state == STATE_REMOVED:
        return Change(KIND_REMOVED, None, None, None)
    if prev_state == STATE_AMOUNT and cur_state == STATE_ABSENT:
        return Change(KIND_DISAPPEARED, None, None, None)
    if cur_state == STATE_AMOUNT and prev_state == STATE_REMOVED:
        return Change(KIND_REAPPEARED, None, None, None)
    if cur_state == STATE_AMOUNT:  # prev ∈ {not_evaluated, absent}
        return Change(KIND_APPEARED, None, None, None)
    return _NONE_CHANGE


def contribution_between(first_state: str, last_state: str, first_shown: Decimal | None, last_shown: Decimal | None,
                         *, unavailable_reason: str | None) -> Contribution:
    """§2.7: `absent` не равен нулю — на любом конце даёт `null`; нулевые состояния — ноль денег."""
    if unavailable_reason is not None:
        return Contribution(None, None, unavailable_reason)
    if first_state == STATE_ABSENT or last_state == STATE_ABSENT:
        return Contribution(None, None, REASON_ABSENT_ENDPOINT)
    delta = (last_shown or Decimal(0)) - (first_shown or Decimal(0))
    return Contribution(delta, direction_of(delta), None)

"""Правило ставки статьи: классификация единиц и свёртка строк-носителей
(спека 2026-08-24-passport-volumes-design.md §2.2, §2.5, §2.6; план, задача 1).

Модуль чистый, тем же доводом, что `services/category_rollup.py`: ни
`Session`, ни ORM, ни SQL, ни импортов из `crud` — только простые значения
(`CarrierRow`) на входе и результат свёртки (`ArticleFold`) на выходе. Позднее
задача кладёт SQL-запрос и поля ответа сверху этого модуля; здесь — только
правило.

Масштабируемость единицы определяется ИСКЛЮЧЕНИЕМ: единица масштабируема, если
она не входит в `NON_SCALABLE_UNIT_CODES`. Полноту классификации (что в
справочнике нет кода, оставшегося без разбора) сторожит тест
(`test_the_two_unit_groups_are_disjoint_and_cover_the_seed` и интеграционный
тест по таблице), а не перечисление в коде (§2.5) — новый код единицы,
добавленный в справочник, но не упомянутый ни в одном множестве, роняет тест,
а не получает поведение по умолчанию молча.

Негодное слагаемое (пропущенная либо не-конечная сумма, пропущенный либо
неположительный объём) гасит статью целиком — свёртка не выдаёт ставку по
части строк-носителей: частичная ставка хуже пустой клетки, потому что
неотличима от полной (§2.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from money.vat import AmountStatus, restate_gross
from parser.summary_block import ARITHMETIC_PRECISION

#: Контекст строится ЯВНО и не наследует глобальный — тот же довод и та же
#: форма, что у `_VAT_CONTEXT` в `money/vat.py`: `localcontext()` без аргумента
#: копирует контекст вместе с его трапами, а `prec = 28` по умолчанию делает
#: сложение неассоциативным на длинных слагаемых, то есть зависимым от порядка
#: строк запроса. При `prec = 100` слагаемые файлового масштаба не округляются,
#: и свёртка точна.
_RATE_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

NON_SCALABLE_UNIT_CODES: frozenset[str] = frozenset({"SET", "MON"})
SCALABLE_UNIT_CODES: frozenset[str] = frozenset({"TON", "KG", "M3", "L", "M2", "M", "PCS"})


def is_scalable_unit(code: str | None) -> bool:
    return code is not None and code not in NON_SCALABLE_UNIT_CODES


@dataclass(frozen=True)
class CarrierRow:
    amount: Decimal | None
    vat_rate_base: Decimal | None
    unit_code: str | None
    unit_symbol: str | None
    volume: Decimal | None


@dataclass(frozen=True)
class ArticleFold:
    rows: int
    amount: Decimal | None
    amount_ok: bool
    unit_code: str | None
    unit_symbol: str | None
    unit_missing: bool
    unit_conflict: bool
    volume: Decimal | None
    volume_missing: bool
    volume_nonpositive: bool


def fold_carrier_rows(rows: Sequence[CarrierRow], effective_rate: Decimal | None) -> ArticleFold:
    if not rows:
        return ArticleFold(
            rows=0,
            amount=None,
            amount_ok=False,
            unit_code=None,
            unit_symbol=None,
            unit_missing=False,
            unit_conflict=False,
            volume=None,
            volume_missing=False,
            volume_nonpositive=False,
        )

    # `restate_gross` держит свой контекст сам; здесь под явным контекстом идёт
    # НАКОПЛЕНИЕ — именно оно зависит от `prec` и от порядка строк.
    amount_ok = True
    with localcontext(_RATE_CONTEXT):
        amount = Decimal(0)
        for row in rows:
            restated = restate_gross(row.amount, row.vat_rate_base, effective_rate)
            if restated.amount is None or restated.status is AmountStatus.NOT_FINITE:
                amount_ok = False
                continue
            amount += restated.amount

    codes = {row.unit_code for row in rows}
    unit_missing = None in codes
    known = codes - {None}
    unit_conflict = len(known) > 1

    volume_missing = any(row.volume is None for row in rows)
    volume_nonpositive = any(row.volume is not None and (not row.volume.is_finite() or row.volume <= 0) for row in rows)
    volume = None
    if not volume_missing and not volume_nonpositive:
        with localcontext(_RATE_CONTEXT):
            volume = Decimal(0)
            for row in rows:
                volume += row.volume

    single = next(iter(known)) if len(known) == 1 and not unit_missing else None
    symbol = next((r.unit_symbol for r in rows if r.unit_code == single), None) if single else None

    return ArticleFold(
        rows=len(rows),
        amount=amount if amount_ok else None,
        amount_ok=amount_ok,
        unit_code=single,
        unit_symbol=symbol,
        unit_missing=unit_missing,
        unit_conflict=unit_conflict,
        volume=volume,
        volume_missing=volume_missing,
        volume_nonpositive=volume_nonpositive,
    )

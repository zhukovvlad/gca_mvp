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
from enum import StrEnum

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


class RateState(StrEnum):
    """Состояние ставки статьи — исчерпывающий автомат с приоритетом (§2.5),
    ровно тот же список, что несёт контракт ответа (§2.10)."""

    NO_CARRIER = "no_carrier"
    ADDITIONAL_WORKS = "additional_works"
    AMOUNT_MISSING = "amount_missing"
    UNIT_MISSING = "unit_missing"
    UNIT_CONFLICT = "unit_conflict"
    UNIT_NOT_SCALABLE = "unit_not_scalable"
    VOLUME_MISSING = "volume_missing"
    VOLUME_NONPOSITIVE = "volume_nonpositive"
    VOLUME_INCONSISTENT = "volume_inconsistent"
    RATE = "rate"


class RateNote(StrEnum):
    """Уточнение для `RateState.VOLUME_INCONSISTENT`, иначе `None` (§2.4, §2.10).

    Третье значение, `UNVERIFIABLE`, заведено ревизией У3 варианта «б»: ребёнок
    без прочитанной единицы (`unit_missing` либо `unit_conflict` в его свёртке)
    не несёт единицы узла, и сходимость по нему проверить нечем — но подпись
    `MIXED_UNITS` утверждала бы про него ложное (единица не «отличается», она
    неизвестна). Два состояния под одним значением — тот самый дефект
    `docs/insights/one-value-two-states.md`, поэтому подпись отдельная.
    """

    OVERSHOOT = "overshoot"
    MIXED_UNITS = "mixed_units"
    UNVERIFIABLE = "unverifiable"


#: Допуск сходимости на одно слагаемое (§2.4): объёмы в смете округлены до
#: сотых, и допуск накапливается по числу слагаемых — `ε = TOLERANCE_PER_SUMMAND
#: * n`. Порог выбран рассуждением, а не замером (§4): на стенде сходимость
#: точная и допуск ни разу не понадобился.
TOLERANCE_PER_SUMMAND: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class ArticleRate:
    """Итог автомата состояния для одного узла свода статей (§2.5, §2.10).

    `volume` и `unit_rate` — `None` во всех состояниях, кроме `RateState.RATE`;
    `unit` — символ единицы (`fold.unit_symbol`), когда он известен, независимо
    от состояния (уточнение У2); `note` — не `None` только при
    `RateState.VOLUME_INCONSISTENT`.
    """

    state: RateState
    note: RateNote | None
    unit: str | None
    volume: Decimal | None
    unit_rate: Decimal | None


def convergence_note(fold: ArticleFold, children: Sequence[ArticleFold]) -> RateNote | None:
    """Проверка сходимости объёма узла с объёмами детей (§2.4), три проверки
    строго в этом порядке — арифметика последней, потому что при смешении
    единиц она бессмысленна:

    1. **Смешение.** Ребёнок с ИЗВЕСТНОЙ единицей, отличной от единицы узла,
       делает объём узла непроверяемым — величины разных единиц не складываются
       нигде, ни здесь, ни в диагностике.
    2. **Непроверяемость** (уточнение У3, вариант «б»). Ребёнок без прочитанной
       единицы (`unit_code is None`) не несёт единицы узла и складывать его
       объём с объёмами братьев нельзя — не потому, что она отличается, а
       потому, что она не известна. Порядок 1 перед 2 намеренный: определённая
       находка полезнее отсутствия сведений, а узел гасится в обоих случаях
       одинаково — различается только подпись.
    3. **Арифметика.** Сумма объёмов детей `S`, допуск `ε = TOLERANCE_PER_SUMMAND
       * n`; перебор (`S > P + ε`) гасит ставку узла, недобор и равенство —
       норма (не все дети несут код статьи).

    Пустой `children` — сразу `None`: без детей сходимость не проверяема и не
    нужна. Сумма считается внутри `localcontext(_RATE_CONTEXT)`, как и вся
    арифметика фичи (Global Constraint 16).
    """
    if not children:
        return None

    if any(child.unit_code is not None and child.unit_code != fold.unit_code for child in children):
        return RateNote.MIXED_UNITS

    if any(child.unit_code is None for child in children):
        return RateNote.UNVERIFIABLE

    with localcontext(_RATE_CONTEXT):
        total = Decimal(0)
        for child in children:
            total += child.volume
        epsilon = TOLERANCE_PER_SUMMAND * len(children)
        if total > fold.volume + epsilon:
            return RateNote.OVERSHOOT

    return None


def resolve_rate(
    fold: ArticleFold, *, has_extras: bool, children: Sequence[ArticleFold]
) -> ArticleRate:
    """Автомат состояния ставки статьи (§2.5) — линейная цепочка проверок в
    порядке контракта, первое совпадение выигрывает всегда: порядок здесь
    ЕСТЬ правило, а не деталь реализации (§2.10, DoD 15, DoD 16).

    Проверки 1 и 2 (`additional_works`, `no_carrier`) взаимоисключающи: узел
    либо несёт строку с кодом (`fold.rows > 0`), либо нет. Оговорка «нет
    допработ» (`and has_extras`) внутри условия первой проверки — единственное,
    что отличает «допработы есть, носителя нет» от «смета статью не называет
    вовсе»; она выражена именно оговоркой, а не порядком, и переставить эти две
    проверки местами не изменило бы ничего, раз их условия не пересекаются.
    DoD 7 снимает поэтому оговорку, а не порядок.

    Смешение единиц детей (проверка 9, `convergence_note`) ловится ПРЕДИКАТОМ
    по кодам единиц, а не арифметическим сравнением суммы объёмов с объёмом
    узла: сумма разноразмерных величин не имеет смысла, и её числовое совпадение
    с объёмом узла было бы случайным — при другом числе комплектов узел получил
    бы ставку молча. Цена этого решения — узел с любым разноединичным ребёнком
    ставки не получает, даже если его собственный объём указан верно (§2.4).
    """

    def _dead_end(state: RateState) -> ArticleRate:
        """Тупиковая ветка (проверки 1–8): ставки нет, `note` нет — он есть
        только у `VOLUME_INCONSISTENT` (проверка 9), а `volume`/`unit_rate`
        нет ни у одной ветки, кроме `RATE` (проверка 10). `unit` — всегда
        `fold.unit_symbol`: `fold_carrier_rows` уже гарантирует, что он `None`
        ровно там, где единица неизвестна или конфликтна, так что отдельного
        условия под каждое из восьми состояний не требуется."""
        return ArticleRate(state=state, note=None, unit=fold.unit_symbol, volume=None, unit_rate=None)

    if fold.rows == 0 and has_extras:
        return _dead_end(RateState.ADDITIONAL_WORKS)
    if fold.rows == 0:
        return _dead_end(RateState.NO_CARRIER)
    if not fold.amount_ok:
        return _dead_end(RateState.AMOUNT_MISSING)
    if fold.unit_missing:
        return _dead_end(RateState.UNIT_MISSING)
    if fold.unit_conflict:
        return _dead_end(RateState.UNIT_CONFLICT)
    if not is_scalable_unit(fold.unit_code):
        return _dead_end(RateState.UNIT_NOT_SCALABLE)
    if fold.volume_missing:
        return _dead_end(RateState.VOLUME_MISSING)
    if fold.volume_nonpositive:
        return _dead_end(RateState.VOLUME_NONPOSITIVE)

    note = convergence_note(fold, children)
    if note is not None:
        return ArticleRate(
            state=RateState.VOLUME_INCONSISTENT, note=note,
            unit=fold.unit_symbol, volume=None, unit_rate=None,
        )

    # Проверки выше уже гарантируют конечный числитель (`amount_ok`) и строго
    # положительный конечный знаменатель (не `volume_missing`, не
    # `volume_nonpositive`) — трапы `_RATE_CONTEXT` здесь недостижимы, они стоят
    # утверждением, а не обработкой. Ambient-контекст нельзя: он приходит с
    # чужими трапами, и включённый где-то `Inexact` превратил бы штатное деление
    # в исключение — а частное почти никогда представимо конечной дробью.
    # Округление не здесь: его делает граница ответа, за пределами этого модуля.
    with localcontext(_RATE_CONTEXT):
        unit_rate = fold.amount / fold.volume

    return ArticleRate(
        state=RateState.RATE, note=None,
        unit=fold.unit_symbol, volume=fold.volume, unit_rate=unit_rate,
    )

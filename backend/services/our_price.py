"""Расчёт «Нашей цены»: ключи, индекс истории, лестница, состояния, суммы.

Чистый расчёт без `db` и без SQL (спека 4а §2.3, §2.5, §2.7, §2.8, §2.18):
входы — уже собранные dataclass-ы, выход — результат по строкам и суммы.
Загрузка из базы живёт в `crud/`, здесь её нет.

Все деньги — `Decimal`, промежуточного округления нет: арифметика идёт в
явном контексте высокой точности, `quantize_money` вызывается на границе
ответа. Делители лестницы положительны по построению: медиана ступени — из
ставок, прошедших `is_price`; сумма весов — из весов, прошедших `is_weight`;
площадь договора проверяется тем же предикатом.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from enum import Enum
from typing import Literal

from money.inflation import adjust_amount
from money.price import is_price, is_weight
from money.vat import gross_to_net
from parser.summary_block import ARITHMETIC_PRECISION

Level = Literal["v", "p", "f"]
Scope = Literal["cls", "all"]

#: Минимум договоров на ступени, чтобы считать её опорной (спека 4а §2.5).
RUNG_THRESHOLD: int = 2
#: Границы отношения цены к медиане, за которыми единицы учёта считаются разными.
MISMATCH_HIGH: Decimal = Decimal(5)
MISMATCH_LOW: Decimal = Decimal("0.2")

_ZERO = Decimal(0)
_TWO = Decimal(2)

#: Контекст явный и не наследует глобальный: промежуточных округлений в
#: расчёте нет, а ловушки включены, чтобы деление на ноль не прошло молча.
_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)


class LineState(str, Enum):
    """Состояние строки КП. Порядок объявления — порядок проверки (спека §2.7)."""

    in_progress = "in_progress"
    unknown_vat_base = "unknown_vat_base"
    no_price = "no_price"
    no_area = "no_area"
    no_variant = "no_variant"
    no_history = "no_history"
    unit_mismatch = "unit_mismatch"
    comparable = "comparable"


@dataclass(frozen=True)
class Rung:
    level: Level
    scope: Scope


#: Ступени в порядке перебора: сначала сужаем по классу, потом по параметрам.
RUNG_ORDER: tuple[Rung, ...] = (
    Rung("v", "cls"),
    Rung("v", "all"),
    Rung("p", "cls"),
    Rung("p", "all"),
    Rung("f", "cls"),
    Rung("f", "all"),
)


@dataclass(frozen=True)
class LineKey:
    family_id: int
    variant_id: int
    #: (parameter_id, value_id) первого главного параметра; `None` — уровня `p` нет.
    first_main: tuple[int, int | None] | None

    def at(self, level: Level) -> Hashable | None:
        """Ключ строки на уровне; `None` только для `p` без первого главного."""
        if level == "v":
            return self.variant_id
        if level == "p":
            if self.first_main is None:
                return None
            return (self.family_id, self.first_main)
        return self.family_id


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
    area: Decimal | None


@dataclass(frozen=True)
class HistoryLine:
    """Валовые величины строки последней сметы договора."""

    contract_id: int
    key: LineKey
    is_system: bool
    unit_price: Decimal | None
    total: Decimal | None
    weight: Decimal | None
    vat_base: Decimal | None


@dataclass(frozen=True)
class ContractRate:
    contract_id: int
    #: Нетто за единицу (у системы — за м²); после приведения, если план передан.
    net: Decimal


@dataclass(frozen=True)
class KpLine:
    """Валовые величины строки КП."""

    position_item_id: int
    unit_price: Decimal | None
    total: Decimal | None
    weight: Decimal | None
    key: LineKey | None
    is_system: bool
    in_progress: bool
    waiting_admin: bool


@dataclass(frozen=True)
class RungCount:
    rung: Rung
    contracts: int


@dataclass(frozen=True)
class RateRange:
    min: Decimal
    max: Decimal
    median: Decimal


@dataclass(frozen=True)
class Mismatch:
    #: `None` — по КП расхождения нет.
    kp_ratio: Decimal | None
    #: (contract_id, ставка / медиана остальных договоров ступени)
    contracts: tuple[tuple[int, Decimal], ...]


@dataclass(frozen=True)
class RungChoice:
    position_item_id: int
    rung: Rung


@dataclass(frozen=True)
class LineResult:
    position_item_id: int
    state: LineState
    kp_unit_net: Decimal | None
    kp_total_net: Decimal | None
    rung: Rung | None
    auto: bool
    threshold_met: bool
    rungs: tuple[RungCount, ...]
    contracts: tuple[ContractRate, ...]
    range: RateRange | None
    ours: Decimal | None
    ours_from_all_classes: bool
    position: Literal["above", "in", "below"] | None
    #: Подписанная разница к нашей расценке: (цена КП - ours) * вес * доля.
    to_ours: Decimal | None
    bargain: Decimal | None
    below: Decimal | None
    #: Индекс в `OfferEvaluation.groups`.
    group_index: int | None
    group_lines: int | None
    share: Decimal | None
    group_per_sqm: Decimal | None
    mismatch: Mismatch | None


@dataclass(frozen=True)
class GroupResult:
    """Группа системы в КП: точные деньги на группу."""

    family_id: int
    key: Hashable
    rung: Rung | None
    state: LineState
    #: position_item_id строк группы
    lines: tuple[int, ...]
    net: Decimal | None
    per_sqm: Decimal | None
    to_ours: Decimal | None
    bargain: Decimal | None
    below: Decimal | None


@dataclass(frozen=True)
class OfferEvaluation:
    lines: dict[int, LineResult]
    groups: tuple[GroupResult, ...]


@dataclass(frozen=True)
class StateBucket:
    lines: int
    amount: Decimal | None


@dataclass(frozen=True)
class OfferTotals:
    bargain: Decimal
    below: Decimal
    below_lines: int
    unit_mismatch_lines: int
    unit_mismatch_amount: Decimal
    not_comparable_total: Decimal
    not_comparable: dict[LineState, StateBucket]


# --- индекс истории -------------------------------------------------------------

_LEVELS: tuple[Level, ...] = ("v", "p", "f")
_IndexKey = tuple[Level, Hashable, bool]


class HistoryIndex:
    """Ставки договоров по ключам трёх уровней, раздельно для работ и систем.

    Ставка работы договора — `Σ gross_to_net(unit_price, база) * w / Σ w` по его
    строкам ключа. Ставка системы — `Σ gross_to_net(total, база)` по его строкам
    ключа, делённая на площадь его объекта. Строка с неизвестной базой в ставку
    не входит; договор, у которого по ключу выпали все пригодные строки, есть в
    `vat_dropped` и отсутствует в `rates`. Договор без площади в ставку системы
    не входит и в `vat_dropped` не попадает. Ставка проходит `is_price`.
    """

    def __init__(
        self,
        contracts: Sequence[HistoryContract],
        lines: Sequence[HistoryLine],
        *,
        factor_by_estimate: Mapping[int, Decimal] | None,
    ) -> None:
        self._contracts = {c.contract_id: c for c in contracts}
        self._rates: dict[_IndexKey, list[ContractRate]] = {}
        self._dropped: dict[_IndexKey, list[int]] = {}

        # (ключ индекса, договор) -> [сумма нетто*вес | нетто, сумма весов, строк выпало]
        sums: dict[tuple[_IndexKey, int], list[Decimal]] = {}
        counted: dict[tuple[_IndexKey, int], int] = defaultdict(int)
        dropped_lines: dict[tuple[_IndexKey, int], int] = defaultdict(int)

        with localcontext(_CONTEXT):
            for line in lines:
                contract = self._contracts.get(line.contract_id)
                if contract is None:
                    continue
                if line.is_system:
                    if not is_price(line.total) or not is_price(contract.area):
                        continue
                else:
                    if not is_price(line.unit_price) or not is_weight(line.weight):
                        continue
                for level in _LEVELS:
                    key_value = line.key.at(level)
                    if key_value is None:
                        continue
                    slot = ((level, key_value, line.is_system), line.contract_id)
                    if line.vat_base is None:
                        dropped_lines[slot] += 1
                        continue
                    acc = sums.setdefault(slot, [_ZERO, _ZERO])
                    if line.is_system:
                        acc[0] += gross_to_net(line.total, line.vat_base)
                    else:
                        acc[0] += gross_to_net(line.unit_price, line.vat_base) * line.weight
                        acc[1] += line.weight
                    counted[slot] += 1

            rates: dict[_IndexKey, dict[int, Decimal]] = defaultdict(dict)
            for slot, acc in sums.items():
                index_key, contract_id = slot
                contract = self._contracts[contract_id]
                rate = acc[0] / contract.area if index_key[2] else acc[0] / acc[1]
                if factor_by_estimate is not None:
                    rate = adjust_amount(rate, factor_by_estimate[contract.estimate_id])
                if is_price(rate):
                    rates[index_key][contract_id] = rate

        for index_key, by_contract in rates.items():
            self._rates[index_key] = [ContractRate(cid, by_contract[cid]) for cid in sorted(by_contract)]
        dropped: dict[_IndexKey, set[int]] = defaultdict(set)
        for slot in dropped_lines:
            if counted.get(slot, 0) == 0:
                dropped[slot[0]].add(slot[1])
        self._dropped = {key: sorted(ids) for key, ids in dropped.items()}

    def rates(self, level: Level, key: Hashable, *, is_system: bool, class_id: int | None) -> list[ContractRate]:
        """Ставки договоров ключа; `class_id=None` — все классы."""
        found = self._rates.get((level, key, is_system), [])
        if class_id is None:
            return list(found)
        return [r for r in found if self._contracts[r.contract_id].rate_class_id == class_id]

    def vat_dropped(self, level: Level, key: Hashable, *, is_system: bool) -> list[int]:
        """Договоры ключа, выпавшие из-за неизвестной базы НДС."""
        return list(self._dropped.get((level, key, is_system), []))

    def contract(self, contract_id: int) -> HistoryContract:
        return self._contracts[contract_id]


# --- лестница -------------------------------------------------------------------


def available_rungs(key: LineKey) -> tuple[Rung, ...]:
    """Шесть ступеней, а без первого главного параметра — четыре без `p`."""
    if key.first_main is None:
        return tuple(rung for rung in RUNG_ORDER if rung.level != "p")
    return RUNG_ORDER


def auto_rung(counts: Sequence[RungCount]) -> Rung | None:
    """Первая ступень с `RUNG_THRESHOLD` договорами и больше; иначе первая с одним."""
    for count in counts:
        if count.contracts >= RUNG_THRESHOLD:
            return count.rung
    for count in counts:
        if count.contracts >= 1:
            return count.rung
    return None


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / _TWO


def rate_range(rates: Sequence[ContractRate]) -> RateRange:
    """Минимум, максимум и медиана ставок ступени; при чётном числе — среднее двух средних."""
    if not rates:
        raise ValueError("rate_range: на ступени нет ставок")
    values = [r.net for r in rates]
    return RateRange(min=min(values), max=max(values), median=_median(values))


def _outside(ratio: Decimal) -> bool:
    return ratio > MISMATCH_HIGH or ratio < MISMATCH_LOW


def unit_mismatch(kp_net: Decimal, rates: Sequence[ContractRate]) -> Mismatch | None:
    """Расхождение единиц учёта: по КП против медианы ступени и по договорам.

    Границы `5` и `0.2` не включены. Договор сравнивается с медианой остальных
    договоров ступени; при одном договоре эта проверка не делается.
    """
    with localcontext(_CONTEXT):
        median = rate_range(rates).median
        ratio = kp_net / median
        kp_ratio = ratio if _outside(ratio) else None

        flagged: list[tuple[int, Decimal]] = []
        if len(rates) >= RUNG_THRESHOLD:
            for index, rate in enumerate(rates):
                others = [r.net for i, r in enumerate(rates) if i != index]
                contract_ratio = rate.net / _median(others)
                if _outside(contract_ratio):
                    flagged.append((rate.contract_id, contract_ratio))

    if kp_ratio is None and not flagged:
        return None
    return Mismatch(kp_ratio=kp_ratio, contracts=tuple(flagged))


# --- состояния строки -------------------------------------------------------------


def _net(value: Decimal | None, base: Decimal | None) -> Decimal | None:
    if value is None or base is None or not value.is_finite():
        return None
    return gross_to_net(value, base)


def _result(
    line: KpLine,
    state: LineState,
    kp_unit_net: Decimal | None,
    kp_total_net: Decimal | None,
    **fields,
) -> LineResult:
    values = {
        "rung": None,
        "auto": True,
        "threshold_met": False,
        "rungs": (),
        "contracts": (),
        "range": None,
        "ours": None,
        "ours_from_all_classes": False,
        "position": None,
        "to_ours": None,
        "bargain": None,
        "below": None,
        "group_index": None,
        "group_lines": None,
        "share": None,
        "group_per_sqm": None,
        "mismatch": None,
    }
    values.update(fields)
    return LineResult(
        position_item_id=line.position_item_id,
        state=state,
        kp_unit_net=kp_unit_net,
        kp_total_net=kp_total_net,
        **values,
    )


def _rung_rates(history: HistoryIndex, key: LineKey, rung: Rung, *, tender_class_id: int) -> list[ContractRate]:
    return history.rates(
        rung.level,
        key.at(rung.level),
        is_system=False,
        class_id=tender_class_id if rung.scope == "cls" else None,
    )


def _evaluate_work(
    line: KpLine,
    history: HistoryIndex,
    *,
    kp_unit_net: Decimal,
    kp_total_net: Decimal | None,
    tender_class_id: int,
    choice: RungChoice | None,
) -> LineResult:
    key = line.key
    assert key is not None and line.weight is not None
    rungs = available_rungs(key)
    counts = tuple(
        RungCount(rung, len(_rung_rates(history, key, rung, tender_class_id=tender_class_id))) for rung in rungs
    )

    chosen = choice is not None and choice.position_item_id == line.position_item_id
    if chosen:
        if choice.rung not in rungs:
            raise ValueError(f"у строки {line.position_item_id} нет ступени {choice.rung}")
        rung: Rung | None = choice.rung
    else:
        rung = auto_rung(counts)

    rung_rates = _rung_rates(history, key, rung, tender_class_id=tender_class_id) if rung else []
    if not rung_rates:
        return _result(
            line,
            LineState.no_history,
            kp_unit_net,
            kp_total_net,
            rung=rung,
            auto=not chosen,
            rungs=counts,
        )

    threshold_met = len(rung_rates) >= RUNG_THRESHOLD
    price_range = rate_range(rung_rates)
    class_rates = [r for r in rung_rates if history.contract(r.contract_id).rate_class_id == tender_class_id]
    ours_from_all = not class_rates
    ours = min(r.net for r in (class_rates or rung_rates))

    if kp_unit_net > price_range.max:
        position: Literal["above", "in", "below"] = "above"
    elif kp_unit_net < price_range.min:
        position = "below"
    else:
        position = "in"

    mismatch = unit_mismatch(kp_unit_net, rung_rates)
    with localcontext(_CONTEXT):
        to_ours = (kp_unit_net - ours) * line.weight
        if mismatch is None:
            state = LineState.comparable
            bargain: Decimal | None = max(_ZERO, to_ours)
            below = (kp_unit_net - price_range.min) * line.weight if kp_unit_net < price_range.min else None
        else:
            state = LineState.unit_mismatch
            bargain = None
            below = None

    return _result(
        line,
        state,
        kp_unit_net,
        kp_total_net,
        rung=rung,
        auto=not chosen,
        threshold_met=threshold_met,
        rungs=counts,
        contracts=tuple(rung_rates),
        range=price_range,
        ours=ours,
        ours_from_all_classes=ours_from_all,
        position=position,
        to_ours=to_ours,
        bargain=bargain,
        below=below,
        mismatch=mismatch,
    )


def evaluate_offer(
    lines: Sequence[KpLine],
    history: HistoryIndex,
    *,
    kp_vat_base: Decimal | None,
    tender_area: Decimal | None,
    tender_class_id: int,
    choice: RungChoice | None = None,
) -> OfferEvaluation:
    """Состояние и сравнение каждой строки КП; порядок проверок — спека §2.7."""
    results: dict[int, LineResult] = {}
    for line in lines:
        kp_unit_net = _net(line.unit_price, kp_vat_base)
        kp_total_net = _net(line.total, kp_vat_base)

        state: LineState | None = None
        if line.in_progress:
            state = LineState.in_progress
        elif kp_vat_base is None:
            state = LineState.unknown_vat_base
        elif not line.is_system and not (is_price(line.unit_price) and is_weight(line.weight)):
            state = LineState.no_price
        elif line.key is None:
            state = LineState.no_variant
        elif line.is_system:
            raise NotImplementedError("системы сравниваются группами")

        if state is not None:
            results[line.position_item_id] = _result(line, state, kp_unit_net, kp_total_net)
        else:
            assert kp_unit_net is not None
            results[line.position_item_id] = _evaluate_work(
                line,
                history,
                kp_unit_net=kp_unit_net,
                kp_total_net=kp_total_net,
                tender_class_id=tender_class_id,
                choice=choice,
            )
    return OfferEvaluation(lines=results, groups=())


# --- суммы ------------------------------------------------------------------------


def _totals(results: Iterable[LineResult]) -> OfferTotals:
    bargain = _ZERO
    below = _ZERO
    below_lines = 0
    mismatch_lines = 0
    mismatch_amount = _ZERO
    buckets: dict[LineState, list] = {}

    with localcontext(_CONTEXT):
        for result in results:
            amount = result.kp_total_net
            if result.state is LineState.comparable:
                if result.bargain is not None:
                    bargain += result.bargain
                if result.below is not None:
                    below += result.below
                    below_lines += 1
            elif result.state is LineState.unit_mismatch:
                mismatch_lines += 1
                if amount is not None:
                    mismatch_amount += amount
            else:
                bucket = buckets.setdefault(result.state, [0, _ZERO])
                bucket[0] += 1
                if amount is not None:
                    bucket[1] += amount

        not_comparable = {
            state: StateBucket(
                lines=count,
                amount=None if state is LineState.unknown_vat_base else amount,
            )
            for state, (count, amount) in buckets.items()
        }
        total = _ZERO
        for bucket in not_comparable.values():
            if bucket.amount is not None:
                total += bucket.amount

    return OfferTotals(
        bargain=bargain,
        below=below,
        below_lines=below_lines,
        unit_mismatch_lines=mismatch_lines,
        unit_mismatch_amount=mismatch_amount,
        not_comparable_total=total,
        not_comparable=not_comparable,
    )


def offer_totals(evaluation: OfferEvaluation) -> OfferTotals:
    """Итоги участника по всем строкам КП."""
    return _totals(evaluation.lines.values())


def lines_totals(evaluation: OfferEvaluation, position_item_ids: Iterable[int]) -> OfferTotals:
    """Суммы по подмножеству строк (статье); неизвестные id пропускаются."""
    return _totals(evaluation.lines[pid] for pid in dict.fromkeys(position_item_ids) if pid in evaluation.lines)

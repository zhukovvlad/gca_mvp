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
    contractor_title: str
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
    #: Та же ставка до множителя ИПЦ; без плана приведения равна `net`.
    nominal: Decimal
    #: Число строк договора, сложивших ставку по этому ключу.
    lines: int


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


ResetReason = Literal["kind_changed", "key_changed", "rung_unavailable", "rung_empty"]


@dataclass(frozen=True)
class SavedWorkRung:
    """Сохранённая ступень строки: ступень и снимок ключа строки на её уровне."""

    rung: Rung
    #: Значение `LineKey.at(rung.level)` на момент выбора.
    key_snapshot: Hashable


@dataclass(frozen=True)
class SavedFamilyRung:
    """Сохранённая ступень семьи системы внутри одного КП."""

    rung: Rung
    first_main_parameter_id: int | None


@dataclass(frozen=True)
class SavedRungs:
    #: position_item_id -> ступень строки
    work: Mapping[int, SavedWorkRung]
    #: family_id -> ступень семьи (внутри одного КП)
    family: Mapping[int, SavedFamilyRung]


@dataclass(frozen=True)
class ManualRung:
    """Статус сохранённой ступени у строки: чья она и действует ли."""

    rung: Rung
    target: Literal["line", "family"]
    active: bool
    reset_reason: ResetReason | None


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
    #: Сохранённая ступень строки или её семьи; `None` — сохранённой нет.
    manual: ManualRung | None = None
    #: Ступени, которые можно сохранить: у работы — с договорами, у системы —
    #: с договорами у каждой группы семьи; без лестницы — пусто.
    selectable: frozenset[Rung] = frozenset()


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

            rates: dict[_IndexKey, dict[int, ContractRate]] = defaultdict(dict)
            for slot, acc in sums.items():
                index_key, contract_id = slot
                contract = self._contracts[contract_id]
                nominal = acc[0] / contract.area if index_key[2] else acc[0] / acc[1]
                rate = nominal
                if factor_by_estimate is not None:
                    rate = adjust_amount(nominal, factor_by_estimate[contract.estimate_id])
                if is_price(rate):
                    rates[index_key][contract_id] = ContractRate(contract_id, rate, nominal, counted[slot])

        for index_key, by_contract in rates.items():
            self._rates[index_key] = [by_contract[cid] for cid in sorted(by_contract)]
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
        "selectable": frozenset(),
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


@dataclass(frozen=True)
class _Comparison:
    state: LineState
    threshold_met: bool
    range: RateRange
    ours: Decimal
    ours_from_all_classes: bool
    position: Literal["above", "in", "below"]
    mismatch: Mismatch | None
    to_ours: Decimal
    bargain: Decimal | None
    below: Decimal | None


def _compare(
    history: HistoryIndex,
    price: Decimal,
    rung_rates: Sequence[ContractRate],
    *,
    quantity: Decimal,
    tender_class_id: int,
) -> _Comparison:
    """Цена против ставок ступени; `quantity` — вес работы или площадь для системы."""
    price_range = rate_range(rung_rates)
    class_rates = [r for r in rung_rates if history.contract(r.contract_id).rate_class_id == tender_class_id]
    ours = min(r.net for r in (class_rates or rung_rates))

    if price > price_range.max:
        position: Literal["above", "in", "below"] = "above"
    elif price < price_range.min:
        position = "below"
    else:
        position = "in"

    mismatch = unit_mismatch(price, rung_rates)
    with localcontext(_CONTEXT):
        to_ours = (price - ours) * quantity
        if mismatch is None:
            state = LineState.comparable
            bargain: Decimal | None = max(_ZERO, to_ours)
            below = (price - price_range.min) * quantity if price < price_range.min else None
        else:
            state = LineState.unit_mismatch
            bargain = None
            below = None

    return _Comparison(
        state=state,
        threshold_met=len(rung_rates) >= RUNG_THRESHOLD,
        range=price_range,
        ours=ours,
        ours_from_all_classes=not class_rates,
        position=position,
        mismatch=mismatch,
        to_ours=to_ours,
        bargain=bargain,
        below=below,
    )


def _work_manual(
    key: LineKey | None, record: SavedWorkRung, history: HistoryIndex, *, tender_class_id: int
) -> ManualRung:
    """Действует ли сохранённая ступень работы: четыре условия по порядку (спека 4б §2.5).

    Условие «строка — работа» проверяет вызывающий: сюда попадают только работы.
    """
    reason: ResetReason | None = None
    if key is None or key.at(record.rung.level) != record.key_snapshot:
        reason = "key_changed"
    elif record.rung not in available_rungs(key):
        reason = "rung_unavailable"
    elif not _rung_rates(history, key, record.rung, tender_class_id=tender_class_id):
        reason = "rung_empty"
    return ManualRung(record.rung, "line", reason is None, reason)


def _evaluate_work(
    line: KpLine,
    history: HistoryIndex,
    *,
    kp_unit_net: Decimal,
    kp_total_net: Decimal | None,
    tender_class_id: int,
    choice: RungChoice | None,
    saved: SavedRungs | None,
) -> LineResult:
    key = line.key
    assert key is not None and line.weight is not None
    rungs = available_rungs(key)
    counts = tuple(
        RungCount(rung, len(_rung_rates(history, key, rung, tender_class_id=tender_class_id))) for rung in rungs
    )
    selectable = frozenset(count.rung for count in counts if count.contracts >= 1)

    record = saved.work.get(line.position_item_id) if saved is not None else None
    manual = _work_manual(key, record, history, tender_class_id=tender_class_id) if record is not None else None

    chosen = choice is not None and choice.position_item_id == line.position_item_id
    pinned = manual is not None and manual.active
    if chosen:
        if choice.rung not in rungs:
            raise ValueError(f"у строки {line.position_item_id} нет ступени {choice.rung}")
        rung: Rung | None = choice.rung
    elif pinned:
        rung = manual.rung
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
            manual=manual,
            selectable=selectable,
        )

    cmp = _compare(history, kp_unit_net, rung_rates, quantity=line.weight, tender_class_id=tender_class_id)
    return _result(
        line,
        cmp.state,
        kp_unit_net,
        kp_total_net,
        rung=rung,
        auto=not (chosen or pinned),
        threshold_met=cmp.threshold_met,
        rungs=counts,
        contracts=tuple(rung_rates),
        range=cmp.range,
        ours=cmp.ours,
        ours_from_all_classes=cmp.ours_from_all_classes,
        position=cmp.position,
        to_ours=cmp.to_ours,
        bargain=cmp.bargain,
        below=cmp.below,
        mismatch=cmp.mismatch,
        manual=manual,
        selectable=selectable,
    )


# --- системы: одна ступень на семью внутри КП ----------------------------------------


@dataclass(frozen=True)
class _SystemEntry:
    line: KpLine
    key: LineKey
    kp_unit_net: Decimal | None
    kp_total_net: Decimal


def _system_rates(
    history: HistoryIndex, rung: Rung, key_value: Hashable, *, tender_class_id: int
) -> list[ContractRate]:
    return history.rates(
        rung.level,
        key_value,
        is_system=True,
        class_id=tender_class_id if rung.scope == "cls" else None,
    )


def _line_part(amount: Decimal | None, line_net: Decimal, group_net: Decimal) -> Decimal | None:
    """Доля группового денежного итога строки: одно деление, без поправки остатка."""
    return None if amount is None else amount * line_net / group_net


def _family_manual(
    record: SavedFamilyRung,
    entries: Sequence[_SystemEntry],
    rungs: Sequence[Rung],
    counts: Mapping[Rung, Mapping[Hashable, int]],
) -> ManualRung:
    """Действует ли сохранённая ступень семьи: три условия по порядку (спека 4б §2.6).

    Ступень `p` есть в `rungs` только при первом главном параметре у всех строк
    семьи, поэтому после проверки доступности он у каждой строки задан.
    """
    reason: ResetReason | None = None
    if record.rung not in rungs:
        reason = "rung_unavailable"
    elif record.rung.level == "p" and any(
        entry.key.first_main[0] != record.first_main_parameter_id for entry in entries
    ):
        reason = "key_changed"
    elif min(counts[record.rung].values()) < 1:
        reason = "rung_empty"
    return ManualRung(record.rung, "family", reason is None, reason)


def _evaluate_family(
    entries: Sequence[_SystemEntry],
    history: HistoryIndex,
    *,
    tender_area: Decimal,
    tender_class_id: int,
    choice: RungChoice | None,
    saved: SavedRungs | None,
    first_group_index: int,
) -> tuple[dict[int, LineResult], list[GroupResult]]:
    """Одна ступень на всю семью: строки делятся по ключу ступени, группа — одна цена."""
    rungs = (
        RUNG_ORDER
        if all(entry.key.first_main is not None for entry in entries)
        else tuple(rung for rung in RUNG_ORDER if rung.level != "p")
    )
    partitions: dict[Level, dict[Hashable, list[_SystemEntry]]] = {}
    for level in {rung.level for rung in rungs}:
        by_key: dict[Hashable, list[_SystemEntry]] = {}
        for entry in entries:
            by_key.setdefault(entry.key.at(level), []).append(entry)
        partitions[level] = by_key
    counts: dict[Rung, dict[Hashable, int]] = {
        rung: {
            key_value: len(_system_rates(history, rung, key_value, tender_class_id=tender_class_id))
            for key_value in partitions[rung.level]
        }
        for rung in rungs
    }

    selectable = frozenset(rung for rung in rungs if min(counts[rung].values()) >= 1)
    family_record = saved.family.get(entries[0].key.family_id) if saved is not None else None
    family_manual = _family_manual(family_record, entries, rungs, counts) if family_record is not None else None

    chosen = choice is not None and any(entry.line.position_item_id == choice.position_item_id for entry in entries)
    pinned = family_manual is not None and family_manual.active
    if chosen:
        if choice.rung not in rungs:
            raise ValueError(f"у семьи {entries[0].key.family_id} нет ступени {choice.rung}")
        rung = choice.rung
    elif pinned:
        rung = family_manual.rung
    else:
        rung = RUNG_ORDER[-1]
        for threshold in (RUNG_THRESHOLD, 1):
            met = next((r for r in rungs if min(counts[r].values()) >= threshold), None)
            if met is not None:
                rung = met
                break

    results: dict[int, LineResult] = {}
    groups: list[GroupResult] = []
    for key_value, members in partitions[rung.level].items():
        group_index = first_group_index + len(groups)
        with localcontext(_CONTEXT):
            group_net = sum((m.kp_total_net for m in members), _ZERO)
            per_sqm = group_net / tender_area
        rates = _system_rates(history, rung, key_value, tender_class_id=tender_class_id)
        cmp = (
            _compare(history, per_sqm, rates, quantity=tender_area, tender_class_id=tender_class_id) if rates else None
        )
        groups.append(
            GroupResult(
                family_id=members[0].key.family_id,
                key=key_value,
                rung=rung,
                state=cmp.state if cmp else LineState.no_history,
                lines=tuple(m.line.position_item_id for m in members),
                net=group_net,
                per_sqm=per_sqm,
                to_ours=cmp.to_ours if cmp else None,
                bargain=cmp.bargain if cmp else None,
                below=cmp.below if cmp else None,
            )
        )
        for member in members:
            line_counts = tuple(RungCount(r, counts[r][member.key.at(r.level)]) for r in rungs)
            work_record = saved.work.get(member.line.position_item_id) if saved is not None else None
            manual = (
                ManualRung(work_record.rung, "line", False, "kind_changed")
                if work_record is not None
                else family_manual
            )
            with localcontext(_CONTEXT):
                share = member.kp_total_net / group_net
                fields = dict(
                    rung=rung,
                    auto=not (chosen or pinned),
                    rungs=line_counts,
                    manual=manual,
                    selectable=selectable,
                    group_index=group_index,
                    group_lines=len(members),
                    share=share,
                    group_per_sqm=per_sqm,
                )
                if cmp is not None:
                    fields.update(
                        threshold_met=cmp.threshold_met,
                        contracts=tuple(rates),
                        range=cmp.range,
                        ours=cmp.ours,
                        ours_from_all_classes=cmp.ours_from_all_classes,
                        position=cmp.position,
                        to_ours=_line_part(cmp.to_ours, member.kp_total_net, group_net),
                        bargain=_line_part(cmp.bargain, member.kp_total_net, group_net),
                        below=_line_part(cmp.below, member.kp_total_net, group_net),
                        mismatch=cmp.mismatch,
                    )
            results[member.line.position_item_id] = _result(
                member.line,
                cmp.state if cmp else LineState.no_history,
                member.kp_unit_net,
                member.kp_total_net,
                **fields,
            )
    return results, groups


def _before_ladder_manual(
    line: KpLine, history: HistoryIndex, *, tender_class_id: int, saved: SavedRungs | None
) -> ManualRung | None:
    """Статус сохранённой ступени строки, которую состояние развело до лестницы.

    Ступень не применяется и не сбрасывается: смысл проверяется по ключу строки.
    """
    record = saved.work.get(line.position_item_id) if saved is not None else None
    if record is None:
        return None
    if line.is_system:
        return ManualRung(record.rung, "line", False, "kind_changed")
    return _work_manual(line.key, record, history, tender_class_id=tender_class_id)


def evaluate_offer(
    lines: Sequence[KpLine],
    history: HistoryIndex,
    *,
    kp_vat_base: Decimal | None,
    tender_area: Decimal | None,
    tender_class_id: int,
    choice: RungChoice | None = None,
    saved: SavedRungs | None = None,
) -> OfferEvaluation:
    """Состояние и сравнение каждой строки КП; порядок проверок — спека §2.7.

    `choice` — ступень из предпросмотра, она побеждает сохранённую `saved` на своей
    строке или семье. Недействующая сохранённая ступень расчёт не меняет и не
    роняет: строка идёт на авто и несёт `manual` с причиной.
    """
    results: dict[int, LineResult] = {}
    family_entries: dict[int, list[_SystemEntry]] = {}
    for line in lines:
        kp_unit_net = _net(line.unit_price, kp_vat_base)
        kp_total_net = _net(line.total, kp_vat_base)

        state: LineState | None = None
        if line.in_progress:
            state = LineState.in_progress
        elif kp_vat_base is None:
            state = LineState.unknown_vat_base
        elif line.is_system:
            if not is_price(line.total):
                state = LineState.no_price
            elif not is_price(tender_area):
                state = LineState.no_area
            elif line.key is None:
                state = LineState.no_variant
        elif not (is_price(line.unit_price) and is_weight(line.weight)):
            state = LineState.no_price
        elif line.key is None:
            state = LineState.no_variant

        if state is not None:
            results[line.position_item_id] = _result(
                line,
                state,
                kp_unit_net,
                kp_total_net,
                manual=_before_ladder_manual(line, history, tender_class_id=tender_class_id, saved=saved),
            )
        elif line.is_system:
            assert line.key is not None and kp_total_net is not None
            family_entries.setdefault(line.key.family_id, []).append(
                _SystemEntry(line, line.key, kp_unit_net, kp_total_net)
            )
        else:
            assert kp_unit_net is not None
            results[line.position_item_id] = _evaluate_work(
                line,
                history,
                kp_unit_net=kp_unit_net,
                kp_total_net=kp_total_net,
                tender_class_id=tender_class_id,
                choice=choice,
                saved=saved,
            )

    groups: list[GroupResult] = []
    for entries in family_entries.values():
        assert tender_area is not None
        family_results, family_groups = _evaluate_family(
            entries,
            history,
            tender_area=tender_area,
            tender_class_id=tender_class_id,
            choice=choice,
            saved=saved,
            first_group_index=len(groups),
        )
        results.update(family_results)
        groups.extend(family_groups)

    ordered = {line.position_item_id: results[line.position_item_id] for line in lines}
    return OfferEvaluation(lines=ordered, groups=tuple(groups))


# --- суммы ------------------------------------------------------------------------


def _totals(results: Iterable[LineResult], groups: Sequence[GroupResult] | None = None) -> OfferTotals:
    """Суммы по строкам; с `groups` деньги систем берутся по группе один раз, а не по долям."""
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
                by_group = groups is not None and result.group_index is not None
                if result.bargain is not None and not by_group:
                    bargain += result.bargain
                if result.below is not None:
                    if not by_group:
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

        for group in groups or ():
            if group.state is LineState.comparable:
                if group.bargain is not None:
                    bargain += group.bargain
                if group.below is not None:
                    below += group.below

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
    return _totals(evaluation.lines.values(), evaluation.groups)


def lines_totals(evaluation: OfferEvaluation, position_item_ids: Iterable[int]) -> OfferTotals:
    """Суммы по подмножеству строк (статье); неизвестные id пропускаются."""
    return _totals(evaluation.lines[pid] for pid in dict.fromkeys(position_item_ids) if pid in evaluation.lines)

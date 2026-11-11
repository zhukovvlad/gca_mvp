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
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from enum import Enum
from typing import Literal

from money.inflation import YearMonth, adjust_amount
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.analog_ladder import (
    MIN_NOMINAL,
    Analog,
    FamilyLadderInfo,
    Figure,
    Form,
    KpWork,
    WorkHistory,
    WorkHistoryLine,
    WorkLadder,
    date_rank,
    ladder_for,
)
from services.price_basis import (  # noqa: F401  (потребители ядра 4а импортируют эти имена отсюда)
    _CONTEXT,
    MISMATCH_HIGH,
    MISMATCH_LOW,
    HistoryContract,
)
from services.price_basis import (
    median as _median,
)
from services.price_basis import (
    outside as _outside,
)
from services.zone_rules import Zone

Level = Literal["v", "p", "f"]
Scope = Literal["cls", "all"]

#: Минимум договоров на ступени, чтобы считать её опорной (спека 4а §2.5).
RUNG_THRESHOLD: int = 2

_ZERO = Decimal(0)


class LineState(str, Enum):
    """Состояние строки КП. Порядок объявления — порядок проверки (спека §2.7)."""

    in_progress = "in_progress"
    unknown_vat_base = "unknown_vat_base"
    no_price = "no_price"
    no_area = "no_area"
    no_variant = "no_variant"
    no_zone = "no_zone"
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
    #: Цепочка разделов строки (работа); `None` — у строки нет цепочки.
    section_chain_id: int | None = None
    #: Зона цепочки; `None` — не определена (состояние `no_zone`).
    zone: Zone | None = None
    #: Нормализованные названия цепочки (множество) — близость места справки.
    chain_titles: frozenset[str] = frozenset()
    #: Версия схемы варианта строки.
    schema_id: int | None = None
    #: parameter_id -> value_id | `None`; только если вариант на действующей версии схемы.
    main_values: Mapping[int, int | None] = field(default_factory=dict)
    #: Цепочка ждёт решения администратора (спорная зона или задание в `error`/`privacy_hold`).
    zone_waiting: bool = False
    #: Главные параметры контекста строки со значением `path_conflict` (версия схемы варианта).
    conflicted: frozenset[int] = frozenset()
    #: parameter_id -> форма значения (id составного либо `"simple"`); только у варианта на действующей версии.
    forms: Mapping[int, Form] = field(default_factory=dict)


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


ResetReason = Literal["key_changed", "rung_unavailable", "rung_empty"]


@dataclass(frozen=True)
class SavedFamilyRung:
    """Сохранённая ступень семьи системы внутри одного КП."""

    rung: Rung
    first_main_parameter_id: int | None


@dataclass(frozen=True)
class SavedRungs:
    #: family_id -> ступень семьи системы (внутри одного КП); ручной ступени работы нет
    family: Mapping[int, SavedFamilyRung]


@dataclass(frozen=True)
class ManualRung:
    """Статус сохранённой ступени семьи системы: чья она и действует ли."""

    rung: Rung
    target: Literal["family"]
    active: bool
    reset_reason: ResetReason | None


TargetKind = Literal["freshest", "analog", "contractor"]
TargetResetReason = Literal[
    "kind_changed", "not_comparable", "analog_unavailable", "no_freshest", "no_contractor"
]
#: Сохранённый выбор цели: вид и аналог (`position_item_id` строки договора; у «самой свежей» — `None`).
SavedTarget = tuple[TargetKind, int | None]


@dataclass(frozen=True)
class TargetChoice:
    """Статус сохранённого выбора цели строки-работы: чей он и действует ли (спека лестницы аналогов §2.14)."""

    kind: TargetKind
    analog_id: int | None
    active: bool
    reset_reason: TargetResetReason | None


def resolve_target(
    saved: SavedTarget | None, result: LineResult, *, is_system: bool
) -> TargetChoice | None:
    """Статус выбора по сохранённой записи `(вид, аналог)` и расчёту строки.

    Условия идут по таблице §2.14, первое невыполненное даёт причину. Запись остаётся и при сбросе:
    расчёт идёт на самой низкой.
    """
    if saved is None:
        return None
    kind, analog_id = saved
    reason: TargetResetReason | None = None
    if is_system:
        reason = "kind_changed"
    elif result.state is not LineState.comparable:
        reason = "not_comparable"
    elif kind == "analog" and (result.ladder is None or analog_id not in result.ladder.remainder_ids):
        reason = "analog_unavailable"
    elif kind == "freshest" and result.freshest is None:
        reason = "no_freshest"
    elif kind == "contractor" and result.ladder.contractor is None:
        reason = "no_contractor"
    return TargetChoice(kind, analog_id, reason is None, reason)


def target_analog(result: LineResult, choice: TargetChoice | None) -> Analog | None:
    """Аналог цели сравнимой работы: действующий выбор, иначе самая низкая; `None` — цели нет."""
    ladder = result.ladder
    if ladder is None or result.state is not LineState.comparable:
        return None
    if choice is not None and choice.active:
        if choice.kind == "analog":
            analog_id = choice.analog_id
        elif choice.kind == "contractor":
            analog_id = ladder.contractor.analog_id
        else:
            analog_id = result.freshest.analog_id
    else:
        analog_id = ladder.lowest.analog_id
    return next(a for a in ladder.analogs if a.position_item_id == analog_id)


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
    #: Самая низкая расценка (в ответе — `lowest`): у работы — цена аналога, у системы — ставка.
    ours: Decimal | None
    #: Самая низкая взята из другого класса (у работы — ступени 3 и 4).
    ours_from_all_classes: bool
    position: Literal["above", "in", "below"] | None
    #: Подписанная разница к самой низкой: (цена КП - ours) * вес * доля (в ответе — `to_lowest`).
    to_ours: Decimal | None
    #: «КП выше самых низких» строки: max(0, to_ours) у сравнимой (в ответе — `over_lowest`).
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
    #: Ступени системы, которые можно сохранить: с договорами у каждой группы семьи; у работы пусто.
    selectable: frozenset[Rung] = frozenset()
    #: Лестница аналогов работы; `None` — у систем и у работ, не дошедших до лестницы.
    ladder: WorkLadder | None = None
    #: Самая свежая расценка; `None` — её нет (ни у одного аналога или договора нет даты).
    freshest: Figure | None = None
    to_freshest: Decimal | None = None
    #: Разница за единицу к самой низкой: цена КП за единицу - ours, без веса и доли
    #: (в ответе — `to_lowest_unit`). У системы — ₽/м² группы, одна на всех её членов.
    to_ours_unit: Decimal | None = None
    #: То же к самой свежей (в ответе — `to_freshest_unit`).
    to_freshest_unit: Decimal | None = None
    #: Разница к цифре подрядчика участника (у работы; в ответе — `to_contractor`, `to_contractor_unit`).
    to_contractor: Decimal | None = None
    to_contractor_unit: Decimal | None = None


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
    freshest: Figure | None = None
    to_freshest: Decimal | None = None
    #: `max(0, to_freshest)` у сравнимой группы с самой свежей; иначе `None`.
    over_freshest: Decimal | None = None


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
    #: «КП выше самых низких» (в ответе — `over_lowest`).
    bargain: Decimal
    #: «КП выше самых свежих»; `None` — ни у одной сравнимой строки нет самой свежей (§2.12).
    over_freshest: Decimal | None
    comparable_lines: int
    freshest_lines: int
    below: Decimal
    below_lines: int
    unit_mismatch_lines: int
    unit_mismatch_amount: Decimal
    not_comparable_total: Decimal
    not_comparable: dict[LineState, StateBucket]


# --- индекс истории -------------------------------------------------------------


def _symbolic(unit_price: Decimal | None, vat_base: Decimal) -> bool:
    """Цена за единицу есть, конечна и по номиналу нетто меньше 1 ₽ (спека §2.13 п. 1)."""
    return unit_price is not None and unit_price.is_finite() and gross_to_net(unit_price, vat_base) < MIN_NOMINAL


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

    Строка договора системы с номиналом нетто цены за единицу меньше `MIN_NOMINAL` (1 ₽) в
    сумму ключа не входит — до сложения и деления на площадь; ставка за м² порогу не
    подчиняется. Номинал — без множителя ИПЦ. Строка без цены за единицу порогом не режется.
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
                    if line.is_system and _symbolic(line.unit_price, line.vat_base):
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

    @property
    def contracts(self) -> Mapping[int, HistoryContract]:
        """Договоры истории по идентификатору (для лестницы работ)."""
        return self._contracts


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


def rate_range(rates: Sequence[ContractRate]) -> RateRange:
    """Минимум, максимум и медиана ставок ступени; при чётном числе — среднее двух средних."""
    if not rates:
        raise ValueError("rate_range: на ступени нет ставок")
    values = [r.net for r in rates]
    return RateRange(min=min(values), max=max(values), median=_median(values))


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
    freshest: Figure | None
    to_freshest: Decimal | None
    over_freshest: Decimal | None
    to_ours_unit: Decimal
    to_freshest_unit: Decimal | None


def _freshest_rate(
    history: HistoryIndex, rates: Sequence[ContractRate], target_month: YearMonth | None
) -> Figure | None:
    """Самая свежая ставка системы (спека §2.13 п. 3): договор с периодом сметы, ближайшим к дате КП.

    Ничьи — меньшая ставка, меньший `contract_id`. Договор без периода в кандидаты не входит.
    """
    dated = [r for r in rates if history.contract(r.contract_id).period is not None]
    if not dated:
        return None
    best = min(
        dated,
        key=lambda r: (date_rank(history.contract(r.contract_id).period, target_month), r.net, r.contract_id),
    )
    return Figure(best.net, None, best.contract_id)


def _compare(
    history: HistoryIndex,
    price: Decimal,
    rung_rates: Sequence[ContractRate],
    *,
    quantity: Decimal,
    tender_class_id: int,
    target_month: YearMonth | None,
) -> _Comparison:
    """Цена системы против ставок ступени; `quantity` — площадь объекта.

    Самая низкая и самая свежая берутся из одного охвата: класс тендера, а при его пустоте — все.
    """
    price_range = rate_range(rung_rates)
    class_rates = [r for r in rung_rates if history.contract(r.contract_id).rate_class_id == tender_class_id]
    scope_rates = class_rates or rung_rates
    ours = min(r.net for r in scope_rates)
    freshest = _freshest_rate(history, scope_rates, target_month)

    if price > price_range.max:
        position: Literal["above", "in", "below"] = "above"
    elif price < price_range.min:
        position = "below"
    else:
        position = "in"

    mismatch = unit_mismatch(price, rung_rates)
    with localcontext(_CONTEXT):
        to_ours_unit = price - ours
        to_freshest_unit = None if freshest is None else price - freshest.value
        to_ours = to_ours_unit * quantity
        to_freshest = None if to_freshest_unit is None else to_freshest_unit * quantity
        if mismatch is None:
            state = LineState.comparable
            bargain: Decimal | None = max(_ZERO, to_ours)
            below = (price - price_range.min) * quantity if price < price_range.min else None
            over_freshest = None if to_freshest is None else max(_ZERO, to_freshest)
        else:
            state = LineState.unit_mismatch
            bargain = None
            below = None
            over_freshest = None

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
        freshest=freshest,
        to_freshest=to_freshest,
        over_freshest=over_freshest,
        to_ours_unit=to_ours_unit,
        to_freshest_unit=to_freshest_unit,
    )


def kp_work_of(line: KpLine, net: Decimal) -> KpWork:
    """Строка КП-работа для лестницы: `net` — нетто цены за единицу."""
    key = line.key
    assert key is not None
    return KpWork(
        position_item_id=line.position_item_id,
        family_id=key.family_id,
        variant_id=key.variant_id,
        schema_id=line.schema_id,
        main_values=line.main_values,
        zone=line.zone,
        chain_titles=line.chain_titles,
        net=net,
        conflicted=line.conflicted,
        forms=line.forms,
    )


def _evaluate_work(
    line: KpLine,
    *,
    kp_unit_net: Decimal,
    kp_total_net: Decimal | None,
    work_history: WorkHistory,
    tender_class_id: int,
    tender_contractor_id: int,
    target_month: YearMonth | None,
) -> LineResult:
    """Работа через лестницу аналогов (спека §2.8, §2.9). Ступеней 4а у неё нет."""
    key = line.key
    assert key is not None and line.weight is not None
    ladder = ladder_for(
        kp_work_of(line, kp_unit_net),
        work_history,
        tender_class_id=tender_class_id,
        tender_contractor_id=tender_contractor_id,
        target_month=target_month,
    )
    if ladder.rung_no is None:
        return _result(line, LineState.no_history, kp_unit_net, kp_total_net, ladder=ladder)
    if ladder.all_outliers:
        return _result(
            line,
            LineState.unit_mismatch,
            kp_unit_net,
            kp_total_net,
            ladder=ladder,
            mismatch=Mismatch(kp_ratio=None, contracts=()),
        )

    assert ladder.lowest is not None
    remainder = [a.net for a in ladder.analogs if a.position_item_id in ladder.remainder_ids]
    price_range = RateRange(min=min(remainder), max=max(remainder), median=_median(remainder))
    if kp_unit_net > price_range.max:
        position: Literal["above", "in", "below"] = "above"
    elif kp_unit_net < price_range.min:
        position = "below"
    else:
        position = "in"
    mismatch = None if ladder.kp_ratio is None else Mismatch(kp_ratio=ladder.kp_ratio, contracts=())
    comparable = mismatch is None
    with localcontext(_CONTEXT):
        to_ours_unit = kp_unit_net - ladder.lowest.value
        to_freshest_unit = None if ladder.freshest is None else kp_unit_net - ladder.freshest.value
        to_ours = to_ours_unit * line.weight
        to_freshest = None if to_freshest_unit is None else to_freshest_unit * line.weight
        contractor = ladder.contractor
        to_contractor_unit = None if contractor is None else kp_unit_net - contractor.value
        to_contractor = None if to_contractor_unit is None else to_contractor_unit * line.weight
        bargain = max(_ZERO, to_ours) if comparable else None
        below = (kp_unit_net - price_range.min) * line.weight if comparable and position == "below" else None
    return _result(
        line,
        LineState.comparable if comparable else LineState.unit_mismatch,
        kp_unit_net,
        kp_total_net,
        ladder=ladder,
        range=price_range,
        ours=ladder.lowest.value,
        ours_from_all_classes=ladder.other_class,
        position=position,
        to_ours=to_ours,
        bargain=bargain,
        below=below,
        mismatch=mismatch,
        freshest=ladder.freshest,
        to_freshest=to_freshest,
        to_ours_unit=to_ours_unit,
        to_freshest_unit=to_freshest_unit,
        to_contractor=to_contractor,
        to_contractor_unit=to_contractor_unit,
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
    target_month: YearMonth | None,
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
            _compare(
                history,
                per_sqm,
                rates,
                quantity=tender_area,
                tender_class_id=tender_class_id,
                target_month=target_month,
            )
            if rates
            else None
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
                freshest=cmp.freshest if cmp else None,
                to_freshest=cmp.to_freshest if cmp else None,
                over_freshest=cmp.over_freshest if cmp else None,
            )
        )
        for member in members:
            line_counts = tuple(RungCount(r, counts[r][member.key.at(r.level)]) for r in rungs)
            with localcontext(_CONTEXT):
                share = member.kp_total_net / group_net
                fields = dict(
                    rung=rung,
                    auto=not (chosen or pinned),
                    rungs=line_counts,
                    manual=family_manual,
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
                        freshest=cmp.freshest,
                        to_freshest=_line_part(cmp.to_freshest, member.kp_total_net, group_net),
                        to_ours_unit=cmp.to_ours_unit,
                        to_freshest_unit=cmp.to_freshest_unit,
                    )
            results[member.line.position_item_id] = _result(
                member.line,
                cmp.state if cmp else LineState.no_history,
                member.kp_unit_net,
                member.kp_total_net,
                **fields,
            )
    return results, groups


def evaluate_offer(
    lines: Sequence[KpLine],
    history: HistoryIndex,
    *,
    work_history: Sequence[WorkHistoryLine] | WorkHistory,
    families: Mapping[int, FamilyLadderInfo],
    kp_vat_base: Decimal | None,
    tender_area: Decimal | None,
    tender_class_id: int,
    tender_contractor_id: int,
    target_month: YearMonth | None,
    factors: Mapping[int, Decimal] | None,
    choice: RungChoice | None = None,
    saved: SavedRungs | None = None,
) -> OfferEvaluation:
    """Состояние и сравнение каждой строки КП; порядок проверок — спека лестницы аналогов §2.10.

    Работы идут через лестницу аналогов (`work_history`, `families`), системы — через ступени 4а
    (`history`). `target_month` — месяц этапа: к нему ближе всего самая свежая расценка. `factors`
    — множитель ИПЦ по смете аналога; `None` — без приведения. `choice` — ступень системы из
    предпросмотра, она побеждает сохранённую `saved` на своей семье; у работы ступеней нет.
    `work_history` — строки истории либо уже разложенная по семьям `WorkHistory` сцены (тогда она
    обязана быть собрана из тех же `families`, договоров `history` и `factors`).
    """
    if isinstance(work_history, WorkHistory):
        if (
            work_history.families is not families
            or work_history.contracts is not history.contracts
            or work_history.factors is not factors
        ):
            raise ValueError("WorkHistory собрана не из тех семей, договоров или множителей, что у оценки")
        ladder_history = work_history
    else:
        ladder_history = WorkHistory(work_history, families, history.contracts, factors=factors)
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
        elif line.zone is None:
            state = LineState.no_zone

        if state is not None:
            results[line.position_item_id] = _result(line, state, kp_unit_net, kp_total_net)
        elif line.is_system:
            assert line.key is not None and kp_total_net is not None
            family_entries.setdefault(line.key.family_id, []).append(
                _SystemEntry(line, line.key, kp_unit_net, kp_total_net)
            )
        else:
            assert kp_unit_net is not None
            results[line.position_item_id] = _evaluate_work(
                line,
                kp_unit_net=kp_unit_net,
                kp_total_net=kp_total_net,
                work_history=ladder_history,
                tender_class_id=tender_class_id,
                tender_contractor_id=tender_contractor_id,
                target_month=target_month,
            )

    groups: list[GroupResult] = []
    for entries in family_entries.values():
        assert tender_area is not None
        family_results, family_groups = _evaluate_family(
            entries,
            history,
            tender_area=tender_area,
            tender_class_id=tender_class_id,
            target_month=target_month,
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
    """Суммы по строкам; с `groups` деньги систем берутся по группе один раз, а не по долям.

    `over_freshest` — сумма по сравнимым строкам с самой свежей; без таких строк `None`, а не ноль
    (спека §2.12): сумма пустого набора нулём не показывается.
    """
    bargain = _ZERO
    over_freshest = _ZERO
    comparable_lines = 0
    freshest_lines = 0
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
                comparable_lines += 1
                if result.to_freshest is not None:
                    freshest_lines += 1
                    if not by_group:
                        over_freshest += max(_ZERO, result.to_freshest)
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
                if group.over_freshest is not None:
                    over_freshest += group.over_freshest
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
        over_freshest=over_freshest if freshest_lines else None,
        comparable_lines=comparable_lines,
        freshest_lines=freshest_lines,
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

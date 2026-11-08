"""Лестница аналогов работ: кандидаты, ступени, выбросы, цифры (спека лестницы аналогов §2.8, §2.9, §2.11).

Чистый расчёт без `db`: входы — уже собранные dataclass-ы, выход — `WorkLadder`.
Загрузка строк истории и состояния строки КП (`no_price`, `no_zone` и прочие) —
дело вызывающего; здесь только то, что зависит от аналогов.

Деньги — только `Decimal`, арифметика в явном контексте `price_basis`, без
промежуточного округления. Модуль не знает ядра 4а: общее (`median`, `outside`,
`HistoryContract`) живёт в `services/price_basis.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

from money.inflation import YearMonth, adjust_amount
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.price_basis import _CONTEXT, HistoryContract, median, outside
from services.zone_rules import Zone

#: Нетто за единицу без приведения меньше этого — цена символическая, не аналог (§2.8 п. 4).
MIN_NOMINAL: Decimal = Decimal(1)

RungNo = Literal[1, 2, 3, 4]
NoHistoryReason = Literal["no_family", "no_params", "no_zone_match"]

_RUNGS: tuple[RungNo, ...] = (1, 2, 3, 4)


@dataclass(frozen=True)
class KpWork:
    """Строка КП-работа, у которой есть цена, вариант и семья."""

    position_item_id: int
    family_id: int
    variant_id: int
    #: Версия схемы варианта; значения главных параметров — только если она действующая.
    schema_id: int | None
    #: parameter_id -> value_id (`None` — «не уточнено»)
    main_values: Mapping[int, int | None]
    zone: Zone | None
    #: Нормализованные названия цепочки разделов (множество).
    chain_titles: frozenset[str]
    #: Нетто цены за единицу, без приведения.
    net: Decimal


@dataclass(frozen=True)
class WorkHistoryLine:
    """Строка последней сметы договора по работе, валовая."""

    position_item_id: int
    contract_id: int
    family_id: int
    chain_id: int
    zone: Zone | None
    chain_titles: frozenset[str]
    variant_id: int
    schema_id: int | None
    #: parameter_id -> value_id; только если вариант на действующей версии схемы.
    main_values: Mapping[int, int | None]
    unit_price: Decimal | None
    weight: Decimal | None
    vat_base: Decimal | None


@dataclass(frozen=True)
class FamilyLadderInfo:
    """Сведения семьи для ступеней 2 и 4 (действующая версия схемы)."""

    schema_id: int
    #: Активные главные параметры в порядке `ordinal`.
    main_parameter_ids: tuple[int, ...]
    releasable_ids: frozenset[int]
    names: Mapping[int, str]


@dataclass(frozen=True)
class Analog:
    position_item_id: int
    contract_id: int
    #: С приведением, если есть множитель.
    net: Decimal
    #: Без приведения.
    nominal: Decimal
    period: YearMonth | None
    place_match: int
    #: Цена к медиане цен остальных аналогов ступени; `None` — аналог в остатке.
    outlier_ratio: Decimal | None


@dataclass(frozen=True)
class Figure:
    value: Decimal
    analog_id: int | None
    contract_id: int | None


@dataclass(frozen=True)
class WorkLadder:
    rung_no: RungNo | None
    #: Имена отпускаемых параметров (ступени 2 и 4), по порядку `ordinal`.
    released: tuple[str, ...]
    other_class: bool
    zone: Zone | None
    #: Справка: все аналоги ступени, включая выбросы, в порядке §2.11.
    analogs: tuple[Analog, ...]
    remainder_ids: frozenset[int]
    lowest: Figure | None
    freshest: Figure | None
    all_outliers: bool
    kp_ratio: Decimal | None
    no_history_reason: NoHistoryReason | None
    vat_dropped: tuple[int, ...]


@dataclass(frozen=True)
class _Candidate:
    line: WorkHistoryLine
    contract: HistoryContract
    nominal: Decimal
    net: Decimal


def _months(period: YearMonth) -> int:
    return period.year * 12 + period.month


def date_rank(period: YearMonth | None, target: YearMonth | None) -> tuple[int, int]:
    """Чем меньше, тем ближе к дате КП; без цели — тем позже, тем ближе; без даты — в конец."""
    if period is None:
        return (1, 0)
    if target is None:
        return (0, -_months(period))
    return (0, abs(_months(period) - _months(target)))


def evaluate_work_ladder(
    kp: KpWork,
    lines: Sequence[WorkHistoryLine],
    families: Mapping[int, FamilyLadderInfo],
    contracts: Mapping[int, HistoryContract],
    *,
    tender_class_id: int,
    tender_contractor_id: int,
    target_month: YearMonth | None,
    factors: Mapping[int, Decimal] | None,
) -> WorkLadder:
    """Ступень, справка, остаток и цифры строки КП-работы.

    `lines` — строки последних смет договоров истории (договоры объекта тендера
    и сметы КП загрузчик не отдаёт; договор вне `contracts` пропускается).
    `factors` — множитель ИПЦ по `estimate_id` сметы аналога; `None` — без приведения.
    """
    info = families.get(kp.family_id)
    releasable_ladder = (
        info is not None
        and kp.schema_id == info.schema_id
        and any(p in info.releasable_ids for p in info.main_parameter_ids)
    )
    required = (
        tuple(p for p in info.main_parameter_ids if p not in info.releasable_ids)
        if info is not None
        else ()
    )

    def variant_match(line: WorkHistoryLine) -> bool:
        return line.variant_id == kp.variant_id

    def family_match(line: WorkHistoryLine) -> bool:
        if not releasable_ladder or info is None or line.schema_id != info.schema_id:
            return False
        return all(line.main_values.get(p) == kp.main_values.get(p) for p in required)

    def zone_match(line: WorkHistoryLine) -> bool:
        return kp.zone is not None and line.zone == kp.zone

    candidates: list[_Candidate] = []
    unknown_base: list[tuple[WorkHistoryLine, HistoryContract]] = []
    for line in lines:
        contract = contracts.get(line.contract_id)
        if contract is None or line.family_id != kp.family_id:
            continue
        if not is_price(line.unit_price) or not is_weight(line.weight) or line.zone is None:
            continue
        assert line.unit_price is not None
        if line.vat_base is None:
            unknown_base.append((line, contract))
            continue
        nominal = gross_to_net(line.unit_price, line.vat_base)
        if nominal < MIN_NOMINAL:
            continue
        net = nominal if factors is None else adjust_amount(nominal, factors[contract.estimate_id])
        candidates.append(_Candidate(line, contract, nominal, net))

    def in_rung(rung_no: RungNo, line: WorkHistoryLine, contract: HistoryContract) -> bool:
        if not zone_match(line):
            return False
        own_class = contract.rate_class_id == tender_class_id
        if own_class != (rung_no in (1, 2)):
            return False
        if rung_no in (1, 3):
            return variant_match(line)
        return family_match(line)

    def dropped_on(rung_no: RungNo, picked_ids: set[int]) -> set[int]:
        return {
            contract.contract_id
            for line, contract in unknown_base
            if in_rung(rung_no, line, contract) and contract.contract_id not in picked_ids
        }

    def _dropped_on_first_rung() -> tuple[int, ...]:
        """Без ступени — выпавшие на первой ступени, где они нашлись бы (как в 4а)."""
        for rung_no in _RUNGS:
            found = dropped_on(rung_no, set())
            if found:
                return tuple(sorted(found))
        return ()

    chosen: RungNo | None = None
    picked: list[_Candidate] = []
    for rung_no in _RUNGS:
        picked = [c for c in candidates if in_rung(rung_no, c.line, c.contract)]
        if picked:
            chosen = rung_no
            break

    if chosen is None:
        return WorkLadder(
            rung_no=None,
            released=(),
            other_class=False,
            zone=kp.zone,
            analogs=(),
            remainder_ids=frozenset(),
            lowest=None,
            freshest=None,
            all_outliers=False,
            kp_ratio=None,
            no_history_reason=_no_history_reason(candidates, lambda ln: variant_match(ln) or family_match(ln)),
            vat_dropped=_dropped_on_first_rung(),
        )

    with localcontext(_CONTEXT):
        outlier_ratio: dict[int, Decimal | None] = {}
        for index, cand in enumerate(picked):
            ratio: Decimal | None = None
            if len(picked) >= 2:
                others = [c.net for i, c in enumerate(picked) if i != index]
                candidate_ratio = cand.net / median(others)
                if outside(candidate_ratio):
                    ratio = candidate_ratio
            outlier_ratio[cand.line.position_item_id] = ratio

        remainder = [c for c in picked if outlier_ratio[c.line.position_item_id] is None]
        all_outliers = not remainder
        kp_ratio: Decimal | None = None
        if remainder:
            ratio = kp.net / median([c.net for c in remainder])
            kp_ratio = ratio if outside(ratio) else None

    def rank(c: _Candidate) -> tuple[tuple[int, int], int]:
        return date_rank(c.contract.period, target_month), c.line.position_item_id

    def place(c: _Candidate) -> int:
        return len(kp.chain_titles & c.line.chain_titles)

    lowest = freshest = None
    if remainder:
        low = min(
            remainder,
            key=lambda c: (
                c.net,
                0 if c.contract.contractor_id == tender_contractor_id else 1,
                *rank(c),
            ),
        )
        lowest = Figure(low.net, low.line.position_item_id, low.contract.contract_id)
        dated = [c for c in remainder if c.contract.period is not None]
        if dated:
            fresh = min(
                dated,
                key=lambda c: (date_rank(c.contract.period, target_month), -place(c), c.net, c.line.position_item_id),
            )
            freshest = Figure(fresh.net, fresh.line.position_item_id, fresh.contract.contract_id)

    reference = sorted(
        picked,
        key=lambda c: (-place(c), date_rank(c.contract.period, target_month), c.net, c.line.position_item_id),
    )
    analogs = tuple(
        Analog(
            position_item_id=c.line.position_item_id,
            contract_id=c.contract.contract_id,
            net=c.net,
            nominal=c.nominal,
            period=c.contract.period,
            place_match=place(c),
            outlier_ratio=outlier_ratio[c.line.position_item_id],
        )
        for c in reference
    )

    dropped = dropped_on(chosen, {c.contract.contract_id for c in picked})

    return WorkLadder(
        rung_no=chosen,
        released=_released_names(chosen, info),
        other_class=chosen in (3, 4),
        zone=kp.zone,
        analogs=analogs,
        remainder_ids=frozenset(c.line.position_item_id for c in remainder),
        lowest=lowest,
        freshest=freshest,
        all_outliers=all_outliers,
        kp_ratio=kp_ratio,
        no_history_reason=None,
        vat_dropped=tuple(sorted(dropped)),
    )


def _released_names(rung_no: RungNo, info: FamilyLadderInfo | None) -> tuple[str, ...]:
    if rung_no in (1, 3) or info is None:
        return ()
    return tuple(info.names[p] for p in info.main_parameter_ids if p in info.releasable_ids)


def _no_history_reason(
    candidates: Sequence[_Candidate],
    matches_params: Callable[[WorkHistoryLine], bool],
) -> NoHistoryReason:
    """Первая причина по кандидатам семьи любого класса (§2.10)."""
    if not candidates:
        return "no_family"
    if not any(matches_params(c.line) for c in candidates):
        return "no_params"
    return "no_zone_match"

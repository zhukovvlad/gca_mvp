"""Лестница аналогов работ: кандидаты, ступени, выбросы, цифры (спека лестницы аналогов §2.8, §2.9, §2.11).

Чистый расчёт без `db`: входы — уже собранные dataclass-ы, выход — `WorkLadder`.
Загрузка строк истории и состояния строки КП (`no_price`, `no_zone` и прочие) —
дело вызывающего; здесь только то, что зависит от аналогов.

Деньги — только `Decimal`, арифметика в явном контексте `price_basis`, без
промежуточного округления. Модуль не знает ядра 4а: общее (`median`, `outside`,
`HistoryContract`) живёт в `services/price_basis.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def _required_ids(info: FamilyLadderInfo | None) -> tuple[int, ...]:
    """Главные параметры, которые ступени 2 и 4 не отпускают, по порядку `ordinal`."""
    if info is None:
        return ()
    return tuple(p for p in info.main_parameter_ids if p not in info.releasable_ids)


def _values_key(main_values: Mapping[int, int | None], required: Sequence[int]) -> tuple[int | None, ...]:
    """Значения неотпускаемых главных параметров: равенство ключей — совпадение по всем им."""
    return tuple(main_values.get(p) for p in required)


_ValuesKey = tuple[int | None, ...]
_Unknown = tuple[WorkHistoryLine, HistoryContract]


@dataclass(frozen=True)
class _FamilyPool:
    """Кандидаты одной семьи, не зависящие от строки КП, разложенные под ступени.

    Ступени 1 и 3 берут кандидатов зоны строки КП с её вариантом (`by_variant`), ступени 2 и 4 —
    кандидатов зоны на действующей версии схемы семьи с теми же значениями неотпускаемых главных
    параметров (`by_values`); класс договора ступень отбирает сама. Внутри каждого списка — порядок
    строк истории, как при сплошном переборе.
    """

    #: Все кандидаты семьи любого класса и любой зоны (для причины «нет истории»).
    candidates: tuple[_Candidate, ...]
    #: (зона, вариант) -> кандидаты.
    by_variant: Mapping[tuple[Zone, int], tuple[_Candidate, ...]]
    #: (зона, ключ значений) -> кандидаты на действующей версии схемы семьи.
    by_values: Mapping[tuple[Zone, _ValuesKey], tuple[_Candidate, ...]]
    #: Строки с неизвестной базой НДС, разложенные так же.
    unknown_by_variant: Mapping[tuple[Zone, int], tuple[_Unknown, ...]]
    unknown_by_values: Mapping[tuple[Zone, _ValuesKey], tuple[_Unknown, ...]]
    #: Варианты кандидатов.
    variant_ids: frozenset[int]
    #: Ключи значений у кандидатов на действующей версии схемы семьи.
    values_keys: frozenset[_ValuesKey]


class WorkHistory:
    """Строки истории по работам, разложенные по семье, с кандидатами, посчитанными один раз.

    Кандидат строки КП (§2.8: договор в истории, цена и вес пригодны, зона определена, номинал не
    символический) зависит только от семьи строки, договоров и множителей ИПЦ, поэтому считается
    на семью при первом обращении и переиспользуется всеми строками КП сцены. Живёт один запрос:
    состояния между запросами не держит.
    """

    def __init__(
        self,
        lines: Sequence[WorkHistoryLine],
        families: Mapping[int, FamilyLadderInfo],
        contracts: Mapping[int, HistoryContract],
        *,
        factors: Mapping[int, Decimal] | None,
    ) -> None:
        self.families = families
        self.contracts = contracts
        self.factors = factors
        self._lines_by_family: dict[int, list[WorkHistoryLine]] = {}
        for line in lines:
            self._lines_by_family.setdefault(line.family_id, []).append(line)
        self._pools: dict[int, _FamilyPool] = {}

    def pool(self, family_id: int) -> _FamilyPool:
        found = self._pools.get(family_id)
        if found is None:
            found = self._pools[family_id] = self._build_pool(family_id)
        return found

    def _build_pool(self, family_id: int) -> _FamilyPool:
        candidates: list[_Candidate] = []
        unknown_base: list[tuple[WorkHistoryLine, HistoryContract]] = []
        for line in self._lines_by_family.get(family_id, ()):
            contract = self.contracts.get(line.contract_id)
            if contract is None:
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
            net = nominal if self.factors is None else adjust_amount(nominal, self.factors[contract.estimate_id])
            candidates.append(_Candidate(line, contract, nominal, net))

        info = self.families.get(family_id)
        required = _required_ids(info)

        def values_key(line: WorkHistoryLine) -> _ValuesKey | None:
            """Ключ значений строки на действующей версии схемы семьи; иначе `None`."""
            if info is None or line.schema_id != info.schema_id:
                return None
            return _values_key(line.main_values, required)

        by_variant: dict[tuple[Zone, int], list[_Candidate]] = {}
        by_values: dict[tuple[Zone, _ValuesKey], list[_Candidate]] = {}
        for cand in candidates:
            zone = cand.line.zone
            assert zone is not None
            by_variant.setdefault((zone, cand.line.variant_id), []).append(cand)
            key = values_key(cand.line)
            if key is not None:
                by_values.setdefault((zone, key), []).append(cand)
        unknown_by_variant: dict[tuple[Zone, int], list[_Unknown]] = {}
        unknown_by_values: dict[tuple[Zone, _ValuesKey], list[_Unknown]] = {}
        for line, contract in unknown_base:
            assert line.zone is not None
            unknown_by_variant.setdefault((line.zone, line.variant_id), []).append((line, contract))
            key = values_key(line)
            if key is not None:
                unknown_by_values.setdefault((line.zone, key), []).append((line, contract))
        return _FamilyPool(
            candidates=tuple(candidates),
            by_variant={k: tuple(v) for k, v in by_variant.items()},
            by_values={k: tuple(v) for k, v in by_values.items()},
            unknown_by_variant={k: tuple(v) for k, v in unknown_by_variant.items()},
            unknown_by_values={k: tuple(v) for k, v in unknown_by_values.items()},
            variant_ids=frozenset(c.line.variant_id for c in candidates),
            values_keys=frozenset(key for _, key in by_values),
        )


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
    Для многих строк одной сцены — `ladder_for` с одной `WorkHistory`.
    """
    return ladder_for(
        kp,
        WorkHistory(lines, families, contracts, factors=factors),
        tender_class_id=tender_class_id,
        tender_contractor_id=tender_contractor_id,
        target_month=target_month,
    )


def ladder_for(
    kp: KpWork,
    history: WorkHistory,
    *,
    tender_class_id: int,
    tender_contractor_id: int,
    target_month: YearMonth | None,
) -> WorkLadder:
    """`evaluate_work_ladder` на разложенной истории: тот же расчёт, кандидаты — из пула семьи."""
    info = history.families.get(kp.family_id)
    releasable_ladder = (
        info is not None
        and kp.schema_id == info.schema_id
        and any(p in info.releasable_ids for p in info.main_parameter_ids)
    )
    kp_values = _values_key(kp.main_values, _required_ids(info))
    pool = history.pool(kp.family_id)

    # Ступень берёт строки зоны строки КП (вне её — ни одна, §2.8): ступени 1 и 3 — с вариантом
    # строки КП, ступени 2 и 4 — с её значениями неотпускаемых параметров на действующей версии
    # схемы, и только если у строки КП доступна лестница отпускаемых. Класс: 1 и 2 — класс тендера.
    def rung_slice(rung_no: RungNo, by_variant: Mapping, by_values: Mapping) -> tuple:
        if kp.zone is None:
            return ()
        if rung_no in (1, 3):
            return by_variant.get((kp.zone, kp.variant_id), ())
        if not releasable_ladder:
            return ()
        return by_values.get((kp.zone, kp_values), ())

    def class_fits(rung_no: RungNo, contract: HistoryContract) -> bool:
        return (contract.rate_class_id == tender_class_id) == (rung_no in (1, 2))

    def dropped_on(rung_no: RungNo, picked_ids: set[int]) -> set[int]:
        return {
            contract.contract_id
            for _line, contract in rung_slice(rung_no, pool.unknown_by_variant, pool.unknown_by_values)
            if class_fits(rung_no, contract) and contract.contract_id not in picked_ids
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
        picked = [c for c in rung_slice(rung_no, pool.by_variant, pool.by_values) if class_fits(rung_no, c.contract)]
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
            no_history_reason=_no_history_reason(pool, kp.variant_id, kp_values if releasable_ladder else None),
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
    pool: _FamilyPool,
    variant_id: int,
    values_key: tuple[int | None, ...] | None,
) -> NoHistoryReason:
    """Первая причина по кандидатам семьи любого класса и любой зоны (§2.10). Параметры совпали —
    у кандидата тот же вариант либо (ступени 2 и 4 доступны, `values_key` задан) те же значения
    неотпускаемых главных параметров на действующей версии схемы."""
    if not pool.candidates:
        return "no_family"
    if variant_id not in pool.variant_ids and (values_key is None or values_key not in pool.values_keys):
        return "no_params"
    return "no_zone_match"

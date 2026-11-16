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
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Literal

from money.inflation import YearMonth, adjust_amount
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.price_basis import _CONTEXT, HistoryContract, median, outside
from services.reference_math import outlier_ratios_of
from services.zone_rules import Zone

#: Нетто за единицу без приведения меньше этого — цена символическая, не аналог (§2.8 п. 4).
MIN_NOMINAL: Decimal = Decimal(1)

RungNo = Literal[1, 2, 3, 4]
NoHistoryReason = Literal["no_family", "path_conflict", "composite", "no_params", "no_zone_match"]
#: Форма значения контекста (спека упрощения каталога §2.2): id составного значения либо `"simple"`
#: (простое, «не уточнено» и конфликт).
Form = Literal["simple"] | int

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
    #: Главные параметры контекста строки с `source = 'path_conflict'` (на версии схемы варианта):
    #: конфликт — не значение и не совпадает ни с чем (спека §2.8).
    conflicted: frozenset[int] = frozenset()
    #: parameter_id -> форма значения; составные значения — только у варианта на действующей версии.
    forms: Mapping[int, Form] = field(default_factory=dict)


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
    #: Главные параметры контекста строки с `source = 'path_conflict'` (на версии схемы варианта).
    conflicted: frozenset[int] = frozenset()
    #: parameter_id -> форма значения, как у строки КП.
    forms: Mapping[int, Form] = field(default_factory=dict)
    #: Единица строки — `catalog_positions.unit_id` её каталожной позиции; нужна справочным блокам для пересчёта.
    unit_id: int | None = None


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
    #: Самая свежая расценка среди аналогов остатка с подрядчиком участника КП (спека упрощения §2.9).
    contractor: Figure | None = None


@dataclass(frozen=True)
class CompositeValue:
    """Составное значение строки КП: вид (`all` | `any`) и части (id, текст) по возрастанию id."""

    kind: str
    parts: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class PartReference:
    """Справка по одной части составного значения (спека упрощения каталога §2.8)."""

    value_id: int
    value: str
    #: Все кандидаты части выбранного класса, включая выбросы.
    analogs: int
    contracts: int
    #: Вилка остатка (без выбросов); `None` — остатка нет.
    min: Decimal | None
    max: Decimal | None
    lowest_analog_id: int | None
    lowest_contract_id: int | None
    #: Кандидатов класса тендера нет, взяты из другого класса.
    other_class: bool


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


def _releasable_ids(info: FamilyLadderInfo | None) -> tuple[int, ...]:
    """Главные параметры, которые ступени 2 и 4 отпускают, по порядку `ordinal`."""
    if info is None:
        return ()
    return tuple(p for p in info.main_parameter_ids if p in info.releasable_ids)


def _form(forms: Mapping[int, Form], conflicted: frozenset[int], parameter_id: int) -> Form:
    """Форма значения (спека упрощения §2.2): составное — свой id, остальное и конфликт — простое."""
    if parameter_id in conflicted:
        return "simple"
    return forms.get(parameter_id, "simple")


def _values_key(
    main_values: Mapping[int, int | None],
    forms: Mapping[int, Form],
    conflicted: frozenset[int],
    required: Sequence[int],
    releasable: Sequence[int],
) -> tuple[int | str | None, ...]:
    """Ключ ступеней 2 и 4 (спека упрощения §2.8): значения неотпускаемых главных параметров, затем
    формы отпускаемых. Равенство ключей — совпадение по всем им."""
    return (
        *(main_values.get(p) for p in required),
        *(_form(forms, conflicted, p) for p in releasable),
    )


_ValuesKey = tuple[int | str | None, ...]
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
    #: Варианты кандидатов без конфликтных главных параметров.
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
        releasable = _releasable_ids(info)

        def values_key(line: WorkHistoryLine) -> _ValuesKey | None:
            """Ключ значений строки на действующей версии схемы семьи; иначе `None`. Конфликт по
            неотпускаемому параметру — не значение: ключа нет (спека §2.8)."""
            if info is None or line.schema_id != info.schema_id or line.conflicted & set(required):
                return None
            return _values_key(line.main_values, line.forms, line.conflicted, required, releasable)

        by_variant: dict[tuple[Zone, int], list[_Candidate]] = {}
        by_values: dict[tuple[Zone, _ValuesKey], list[_Candidate]] = {}
        for cand in candidates:
            zone = cand.line.zone
            assert zone is not None
            if not cand.line.conflicted:
                by_variant.setdefault((zone, cand.line.variant_id), []).append(cand)
            key = values_key(cand.line)
            if key is not None:
                by_values.setdefault((zone, key), []).append(cand)
        unknown_by_variant: dict[tuple[Zone, int], list[_Unknown]] = {}
        unknown_by_values: dict[tuple[Zone, _ValuesKey], list[_Unknown]] = {}
        for line, contract in unknown_base:
            assert line.zone is not None
            if not line.conflicted:
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
            variant_ids=frozenset(variant_id for _, variant_id in by_variant),
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
    required = _required_ids(info)
    kp_values = _values_key(kp.main_values, kp.forms, kp.conflicted, required, _releasable_ids(info))
    # Конфликт строки КП: ступени 1 и 3 не берут её вовсе; 2 и 4 — только если конфликт по
    # неотпускаемому параметру (отпускаемый и так отпущен).
    by_variant_blocked = bool(kp.conflicted)
    by_values_blocked = bool(kp.conflicted & set(required))
    pool = history.pool(kp.family_id)

    # Ступень берёт строки зоны строки КП (вне её — ни одна, §2.8): ступени 1 и 3 — с вариантом
    # строки КП, ступени 2 и 4 — с её значениями неотпускаемых параметров на действующей версии
    # схемы, и только если у строки КП доступна лестница отпускаемых. Класс: 1 и 2 — класс тендера.
    def rung_slice(rung_no: RungNo, by_variant: Mapping, by_values: Mapping) -> tuple:
        if kp.zone is None:
            return ()
        if rung_no in (1, 3):
            return () if by_variant_blocked else by_variant.get((kp.zone, kp.variant_id), ())
        if not releasable_ladder or by_values_blocked:
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
            no_history_reason=_no_history_reason(
                pool,
                kp.variant_id,
                kp_values if releasable_ladder and not by_values_blocked else None,
                conflicted=by_variant_blocked and (by_values_blocked or not releasable_ladder),
                composite=any(form != "simple" and p not in kp.conflicted for p, form in kp.forms.items()),
            ),
            vat_dropped=_dropped_on_first_rung(),
        )

    outlier_ratio = _outlier_ratios(picked)
    remainder = [c for c in picked if outlier_ratio[c.line.position_item_id] is None]
    all_outliers = not remainder
    kp_ratio: Decimal | None = None
    if remainder:
        with localcontext(_CONTEXT):
            ratio = kp.net / median([c.net for c in remainder])
            kp_ratio = ratio if outside(ratio) else None

    def place(c: _Candidate) -> int:
        return len(kp.chain_titles & c.line.chain_titles)

    lowest = freshest = contractor = None
    if remainder:
        low = _lowest_of(remainder, contractor_id=tender_contractor_id, target_month=target_month)
        lowest = Figure(low.net, low.line.position_item_id, low.contract.contract_id)
        fresh = _freshest_of(remainder, place=place, target_month=target_month)
        if fresh is not None:
            freshest = Figure(fresh.net, fresh.line.position_item_id, fresh.contract.contract_id)
        mine = _freshest_of(
            [c for c in remainder if c.contract.contractor_id == tender_contractor_id],
            place=place,
            target_month=target_month,
        )
        if mine is not None:
            contractor = Figure(mine.net, mine.line.position_item_id, mine.contract.contract_id)

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
        contractor=contractor,
    )


def _outlier_ratios(picked: Sequence[_Candidate]) -> dict[int, Decimal | None]:
    """position_item_id -> отношение цены кандидата к медиане цен остальных, если оно за границами
    (выброс); иначе `None`. Один кандидат выбросом не бывает."""
    return outlier_ratios_of([(c.line.position_item_id, c.net) for c in picked])


def _lowest_of(
    remainder: Sequence[_Candidate], *, contractor_id: int | None, target_month: YearMonth | None
) -> _Candidate:
    """Самая низкая цена; ничьи — договор подрядчика КП, ближе к дате КП, меньший id строки."""
    return min(
        remainder,
        key=lambda c: (
            c.net,
            0 if c.contract.contractor_id == contractor_id else 1,
            date_rank(c.contract.period, target_month),
            c.line.position_item_id,
        ),
    )


def _freshest_of(
    candidates: Sequence[_Candidate], *, place: Callable[[_Candidate], int], target_month: YearMonth | None
) -> _Candidate | None:
    """Самая свежая: договор с периодом, ближайшим к дате КП; ничьи — ближе место, ниже цена, меньший id."""
    dated = [c for c in candidates if c.contract.period is not None]
    if not dated:
        return None
    return min(
        dated,
        key=lambda c: (date_rank(c.contract.period, target_month), -place(c), c.net, c.line.position_item_id),
    )


def parts_reference(
    kp: KpWork,
    history: WorkHistory,
    *,
    tender_class_id: int | None,
    composites: Mapping[int, CompositeValue],
    tender_contractor_id: int | None = None,
    target_month: YearMonth | None = None,
) -> list[dict]:
    """Справка «по частям» для сборной строки КП (спека упрощения каталога §2.8); на цифры строки не влияет.

    Для каждого составного главного параметра строки (`composites`: parameter_id -> значение) и каждой его
    части `p` — кандидаты той же семьи, версии схемы и зоны, у которых этот параметр равен `p` по `value_id`,
    даже если он отпускаемый; остальные главные параметры — по ключу ступени 2. Сначала класс тендера,
    пусто — другой класс. Выбросы — как в лестнице, отдельно по каждой части. Части целью не становятся.
    """
    info = history.families.get(kp.family_id)
    if info is None:
        return []
    required = _required_ids(info)
    releasable = _releasable_ids(info)
    blocked = bool(kp.conflicted & set(required))
    candidates = history.pool(kp.family_id).candidates
    result: list[dict] = []
    for parameter_id in info.main_parameter_ids:
        composite = composites.get(parameter_id)
        if composite is None or _form(kp.forms, kp.conflicted, parameter_id) == "simple":
            continue
        rest_required = tuple(p for p in required if p != parameter_id)
        rest_releasable = tuple(p for p in releasable if p != parameter_id)
        kp_rest = _values_key(kp.main_values, kp.forms, kp.conflicted, rest_required, rest_releasable)
        unusable = {parameter_id, *required}
        matching = [
            c
            for c in candidates
            if not blocked
            and c.line.zone == kp.zone
            and c.line.schema_id == info.schema_id
            and not c.line.conflicted & unusable
            and _values_key(c.line.main_values, c.line.forms, c.line.conflicted, rest_required, rest_releasable)
            == kp_rest
        ]
        parts = []
        for value_id, text in composite.parts:
            matched = [c for c in matching if c.line.main_values.get(parameter_id) == value_id]
            in_class = [c for c in matched if c.contract.rate_class_id == tender_class_id]
            picked = in_class or matched
            ratios = _outlier_ratios(picked)
            remainder = [c for c in picked if ratios[c.line.position_item_id] is None]
            low = (
                _lowest_of(remainder, contractor_id=tender_contractor_id, target_month=target_month)
                if remainder
                else None
            )
            parts.append(
                PartReference(
                    value_id=value_id,
                    value=text,
                    analogs=len(picked),
                    contracts=len({c.contract.contract_id for c in picked}),
                    min=min((c.net for c in remainder), default=None),
                    max=max((c.net for c in remainder), default=None),
                    lowest_analog_id=None if low is None else low.line.position_item_id,
                    lowest_contract_id=None if low is None else low.contract.contract_id,
                    other_class=bool(matched) and not in_class,
                )
            )
        result.append(
            {
                "parameter_id": parameter_id,
                "parameter_name": info.names[parameter_id],
                "kind": composite.kind,
                "parts": parts,
            }
        )
    return result


def _released_names(rung_no: RungNo, info: FamilyLadderInfo | None) -> tuple[str, ...]:
    if rung_no in (1, 3) or info is None:
        return ()
    return tuple(info.names[p] for p in info.main_parameter_ids if p in info.releasable_ids)


def _no_history_reason(
    pool: _FamilyPool,
    variant_id: int,
    values_key: _ValuesKey | None,
    *,
    conflicted: bool = False,
    composite: bool = False,
) -> NoHistoryReason:
    """Первая причина по кандидатам семьи любого класса и любой зоны (§2.10). Параметры совпали —
    у кандидата тот же вариант либо (ступени 2 и 4 доступны, `values_key` задан) тот же ключ ступеней
    2 и 4. `conflicted` — у строки КП есть конфликтный главный параметр, мешающий на всех ступенях:
    причина раньше `no_params`. `composite` — у строки составное главное значение (спека упрощения
    каталога §2.8): причина раньше `no_params` и `no_zone_match`."""
    if not pool.candidates:
        return "no_family"
    if conflicted:
        return "path_conflict"
    if composite:
        return "composite"
    if variant_id not in pool.variant_ids and (values_key is None or values_key not in pool.values_keys):
        return "no_params"
    return "no_zone_match"

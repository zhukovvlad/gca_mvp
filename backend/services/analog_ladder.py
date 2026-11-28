"""Лестница аналогов работ: кандидаты, ступени, выбросы, цифры (спека лестницы аналогов §2.8, §2.9, §2.11).

Чистый расчёт без `db`: входы — уже собранные dataclass-ы, выход — `WorkLadder`.
Загрузка строк истории и состояния строки КП (`no_price`, `no_zone` и прочие) —
дело вызывающего; здесь только то, что зависит от аналогов.

Деньги — только `Decimal`, арифметика в явном контексте `price_basis`, без
промежуточного округления. Модуль не знает ядра 4а: общее (`median`, `outside`,
`HistoryContract`) живёт в `services/price_basis.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import Literal

from money.inflation import YearMonth, adjust_amount
from money.price import is_price, is_weight
from money.vat import gross_to_net
from services.price_basis import _CONTEXT, HistoryContract, median, outside
from services.reference_math import outlier_ratios_of
from services.value_scale import Scale
from services.zone_rules import Zone

#: Нетто за единицу без приведения меньше этого — цена символическая, не аналог (§2.8 п. 4).
MIN_NOMINAL: Decimal = Decimal(1)

#: Сколько различных договоров должно набраться у сужения по шкале, прежде чем круги кончатся
#: (спека ближайшего значения §2 п. 4); без настройки.
MIN_CONTRACTS = 3

RungNo = Literal[1, 2, 3, 4]
NoHistoryReason = Literal["excluded", "no_family", "path_conflict", "composite", "no_params", "no_zone_match"]
#: Область подбора (спека «зона как предпочтение» §1 п. 1): `own` — зона строки КП; `other` — все остальные
#: зоны и строки договоров без зоны; `any` — вся история семьи (у строки КП без зоны).
ZoneScope = Literal["own", "other", "any"]
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
    #: Семья с флажком «зона обязательна»: подбор только в зоне строки КП (спека «зона как предпочтение» §1 п. 2).
    zone_required: bool = False
    #: Строки договоров с тендерной отметкой «не аналог» на ключ формы строки (спека «Не аналог» §3 п. 2):
    #: не кандидаты для этой строки КП у читателей пула из закрытого списка (`ladder_for`, `parts_reference`,
    #: блоки зон и связей). Для другой строки КП та же строка — кандидат.
    excluded: frozenset[int] = frozenset()


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
    #: Строка похожа на комплект (спека строк-сумм §1, с учётом поправки): не кандидат пула нигде.
    lump: bool = False
    #: На строке отметка «ошибка в цене» (спека «Не аналог» §3 п. 1): не кандидат пула.
    price_error: bool = False


@dataclass(frozen=True)
class FamilyLadderInfo:
    """Сведения семьи для ступеней 2 и 4 (действующая версия схемы)."""

    schema_id: int
    #: Активные главные параметры в порядке `ordinal`.
    main_parameter_ids: tuple[int, ...]
    releasable_ids: frozenset[int]
    names: Mapping[int, str]
    #: parameter_id -> шкала главного точного параметра; только параметры-шкалы (спека ближайшего значения §1).
    scales: Mapping[int, Scale] = field(default_factory=dict)


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
    #: Зона строки договора; `None` — у строки зоны нет.
    zone: Zone | None = None


@dataclass(frozen=True)
class Figure:
    value: Decimal
    analog_id: int | None
    contract_id: int | None


#: Куда сдвинуты числа взятого набора относительно числа КП (спека ближайшего значения §2 п. 7).
Direction = Literal["up", "down", "both"]


@dataclass(frozen=True)
class NearestParam:
    """Итог сужения по одному параметру-шкале (спека ближайшего значения §2 п. 7)."""

    parameter_id: int
    name: str
    kp_value: str
    #: Тексты значений с числом в окончательном наборе, по возрастанию числа, затем по id значения.
    values: tuple[str, ...]
    #: Договоров с числом, равным числу КП, среди кандидатов шага этого параметра (до его сужения).
    kp_contracts: int
    #: Куда сдвинуты числа окончательного набора, отличные от числа КП; `None` — таких нет.
    direction: Direction | None
    #: В окончательном наборе есть кандидаты без числа этого параметра.
    without_value: bool
    #: Имена шкал, сузивших набор раньше этого параметра (не пропущенных), по порядку.
    after: tuple[str, ...]


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
    #: Область, в которой найдена ступень; `None` ровно тогда, когда `rung_no is None`.
    zone_scope: ZoneScope | None = None
    #: Причина прежней цепочки `_no_history_reason` по обычному пулу (внутреннее поле, в ответ API не
    #: выходит; спека «Не аналог» §3 п. 6): у строки без ступени равна `no_history_reason`, а у причины
    #: `excluded` показывает, что было бы без следа; у строки со ступенью — `None`.
    base_reason: NoHistoryReason | None = None
    #: Шкалы, по которым набор сужен до ближайших значений, по порядку параметров (спека ближайшего значения §2).
    nearest: tuple[NearestParam, ...] = ()
    #: Шкалы, у которых в окончательном наборе все числа равны числу КП и нет кандидатов без числа.
    matched: frozenset[int] = frozenset()


#: Пара «область, ступень» подбора.
ExcludedStep = tuple[ZoneScope, RungNo]


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
    #: Область, из которой взяты кандидаты части; `None` — кандидатов нет.
    zone_scope: ZoneScope | None = None


@dataclass(frozen=True)
class _Candidate:
    line: WorkHistoryLine
    contract: HistoryContract
    nominal: Decimal
    net: Decimal


#: След исключённого: отмеченные кандидаты и первая пара подбора, где каждый стоял бы.
ExcludedTrace = tuple[tuple[_Candidate, ExcludedStep], ...]


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


MatchRule = Literal["required", "ignored", "same_set", "nearest", "matched"]
ParamsBasis = Literal["rung", "catalog"]


@dataclass(frozen=True)
class ParamsRule:
    """Как учитывается параметр варианта строки при подборе аналогов (спека параметров варианта §2.3)."""

    #: `rung` — правило описывает фактический подбор; `catalog` — что сделала бы лестница по настройке
    #: каталога; `None` — система без ступени или строка без варианта.
    basis: ParamsBasis | None
    #: parameter_id главного -> правило; пусто при `basis = None`.
    main: Mapping[int, MatchRule]
    #: Правило второстепенных: `ignored` при `basis ≠ None`, иначе `None`.
    secondary: MatchRule | None


def _rung_two_rules(
    main_ids: Sequence[int],
    forms: Mapping[int, Form],
    conflicted: frozenset[int],
    info: FamilyLadderInfo | None,
) -> dict[int, MatchRule]:
    """Правила ступеней 2 и 4: неотпускаемый — значением, отпускаемый — формой (составная или нет).

    Без отпускаемых все главные — `required`: это и есть «ступени 2/4 недоступны» таблицы §2.3."""
    releasable = set(_releasable_ids(info))
    rules: dict[int, MatchRule] = {}
    for parameter_id in main_ids:
        if parameter_id not in releasable:
            rules[parameter_id] = "required"
        elif _form(forms, conflicted, parameter_id) == "simple":
            rules[parameter_id] = "ignored"
        else:
            rules[parameter_id] = "same_set"
    return rules


#: Пустое отображение для значения по умолчанию: изменяемое `{}` в сигнатуре запрещено.
_NO_NEAREST: Mapping[int, NearestParam] = MappingProxyType({})


def params_rule(
    *,
    main_ids: Sequence[int],
    has_variant: bool,
    is_system: bool,
    schema_id: int | None,
    forms: Mapping[int, Form],
    conflicted: frozenset[int],
    info: FamilyLadderInfo | None,
    work_rung: RungNo | None,
    system_level: Literal["v", "p", "f"] | None,
    nearest: Mapping[int, NearestParam] = _NO_NEAREST,
    matched: frozenset[int] = frozenset(),
) -> ParamsRule:
    """Правило «при подборе» для главных параметров варианта строки (спека параметров варианта §2.3).

    `main_ids` — главные параметры схемы варианта по `ordinal`; `info` — семья на действующей схеме.
    `nearest` и `matched` — шкалы, сужённые до ближайших значений и совпавшие (спека ближайшего значения §3
    п. 3): они подменяют правило отпускаемого параметра только при фактическом подборе на ступенях 2 и 4;
    у систем, ступеней 1 и 3 и основания `catalog` правило прежнее.
    """
    if not has_variant:
        return ParamsRule(basis=None, main={}, secondary=None)
    if is_system:
        if system_level is None:
            return ParamsRule(basis=None, main={}, secondary=None)
        if system_level == "v":
            system_rules: dict[int, MatchRule] = {p: "required" for p in main_ids}
        elif system_level == "f":
            system_rules = {p: "ignored" for p in main_ids}
        else:
            system_rules = {p: ("required" if i == 0 else "ignored") for i, p in enumerate(main_ids)}
        return ParamsRule(basis="rung", main=system_rules, secondary="ignored")
    if work_rung in (1, 3):
        return ParamsRule(basis="rung", main={p: "required" for p in main_ids}, secondary="ignored")
    if work_rung in (2, 4):
        rules = _rung_two_rules(main_ids, forms, conflicted, info)
        for parameter_id in main_ids:
            if parameter_id in nearest:
                rules[parameter_id] = "nearest"
            elif parameter_id in matched:
                rules[parameter_id] = "matched"
        return ParamsRule(basis="rung", main=rules, secondary="ignored")
    if info is not None and schema_id == info.schema_id:
        return ParamsRule(basis="catalog", main=_rung_two_rules(main_ids, forms, conflicted, info), secondary="ignored")
    return ParamsRule(basis="catalog", main={p: "required" for p in main_ids}, secondary="ignored")


_ValuesKey = tuple[int | str | None, ...]
_Unknown = tuple[WorkHistoryLine, HistoryContract]


@dataclass(frozen=True)
class _FamilyPool:
    """Кандидаты одной семьи, не зависящие от строки КП, разложенные под ступени.

    Ступени 1 и 3 берут кандидатов области подбора (`own`, `other`, `any`; спека «зона как предпочтение»
    §1) с вариантом строки КП, ступени 2 и 4 — кандидатов области на действующей версии схемы семьи с теми
    же значениями неотпускаемых главных параметров. `own` читается из срезов с зоной в ключе
    (`by_variant`, `by_values`), `other` и `any` — из срезов без зоны (`any_by_*`; `other` отбрасывает
    зону строки КП). Класс договора ступень отбирает сама. Внутри каждого списка — порядок строк истории,
    как при сплошном переборе.
    """

    #: Все кандидаты семьи любого класса, любой зоны и без зоны (для причины «нет истории»).
    candidates: tuple[_Candidate, ...]
    #: (зона, вариант) -> кандидаты.
    by_variant: Mapping[tuple[Zone | None, int], tuple[_Candidate, ...]]
    #: (зона, ключ значений) -> кандидаты на действующей версии схемы семьи.
    by_values: Mapping[tuple[Zone | None, _ValuesKey], tuple[_Candidate, ...]]
    #: Строки с неизвестной базой НДС, разложенные так же.
    unknown_by_variant: Mapping[tuple[Zone | None, int], tuple[_Unknown, ...]]
    unknown_by_values: Mapping[tuple[Zone | None, _ValuesKey], tuple[_Unknown, ...]]
    #: То же без зоны в ключе (все зоны и строки без зоны): из них собираются области `other` и `any`.
    any_by_variant: Mapping[int, tuple[_Candidate, ...]]
    any_by_values: Mapping[_ValuesKey, tuple[_Candidate, ...]]
    unknown_any_by_variant: Mapping[int, tuple[_Unknown, ...]]
    unknown_any_by_values: Mapping[_ValuesKey, tuple[_Unknown, ...]]
    #: Варианты кандидатов без конфликтных главных параметров.
    variant_ids: frozenset[int]
    #: Ключи значений у кандидатов на действующей версии схемы семьи.
    values_keys: frozenset[_ValuesKey]


class WorkHistory:
    """Строки истории по работам, разложенные по семье, с кандидатами, посчитанными один раз.

    Кандидат строки КП (§2.8: договор в истории, цена и вес пригодны, номинал не символический;
    строка без зоны — кандидат, кроме семей с флажком «зона обязательна») зависит только от семьи
    строки, договоров и множителей ИПЦ, поэтому считается на семью при первом обращении и переиспользуется всеми строками КП сцены. Разделяется запросами через кэш сцен; пулы заполняются
    идемпотентно.
    """

    def __init__(
        self,
        lines: Sequence[WorkHistoryLine],
        families: Mapping[int, FamilyLadderInfo],
        contracts: Mapping[int, HistoryContract],
        *,
        factors: Mapping[int, Decimal] | None,
        zone_required: frozenset[int] = frozenset(),
    ) -> None:
        self.families = families
        self.contracts = contracts
        self.factors = factors
        #: Семьи с флажком «зона обязательна»: пул такой семьи не берёт строки договоров без зоны.
        self.zone_required = zone_required
        self._lines_by_family: dict[int, list[WorkHistoryLine]] = {}
        self._lump_ids: set[int] = set()
        self._price_error_ids: set[int] = set()
        self._marked_families: set[int] = set()
        for line in lines:
            self._lines_by_family.setdefault(line.family_id, []).append(line)
            if line.lump:
                self._lump_ids.add(line.position_item_id)
            if line.price_error:
                self._marked_families.add(line.family_id)
                self._price_error_ids.add(line.position_item_id)
        self._pools: dict[int, _FamilyPool] = {}
        self._unmarked_pools: dict[int, _FamilyPool] = {}

    def lines_of(self, family_id: int) -> Sequence[WorkHistoryLine]:
        """Строки истории семьи, как загружены; без запросов."""
        return self._lines_by_family.get(family_id, ())

    def is_lump(self, position_item_id: int) -> bool:
        """Строка договора истории похожа на комплект (по строкам истории; вне истории — нет)."""
        return position_item_id in self._lump_ids

    def is_price_error(self, position_item_id: int) -> bool:
        """Строка договора истории с отметкой «ошибка в цене» (по строкам истории; вне истории — нет)."""
        return position_item_id in self._price_error_ids

    def pool(self, family_id: int) -> _FamilyPool:
        found = self._pools.get(family_id)
        if found is None:
            found = self._pools[family_id] = self._build_pool(family_id)
        return found

    def has_marks(self, family_id: int) -> bool:
        """В семье есть строка истории с отметкой «ошибка в цене» (без построения пула)."""
        return family_id in self._marked_families

    def pool_unmarked(self, family_id: int) -> _FamilyPool:
        """Пул семьи без пропуска «ошибки в цене» (спека «Не аналог» §3 п. 4): строится лениво и держится
        отдельно от `pool`; `lump` в нём пропущен так же."""
        found = self._unmarked_pools.get(family_id)
        if found is None:
            found = self._unmarked_pools[family_id] = self._build_pool(family_id, skip_price_errors=False)
        return found

    def _build_pool(self, family_id: int, *, skip_price_errors: bool = True) -> _FamilyPool:
        candidates: list[_Candidate] = []
        unknown_base: list[tuple[WorkHistoryLine, HistoryContract]] = []
        for line in self._lines_by_family.get(family_id, ()):
            contract = self.contracts.get(line.contract_id)
            if contract is None:
                continue
            if line.lump:  # комплект договора не кандидат нигде: единственное место отбора (спека строк-сумм §4 п. 4)
                continue
            if skip_price_errors and line.price_error:  # «ошибка в цене» не кандидат нигде: единственное место отбора (спека «Не аналог» §3 п. 1)
                continue
            if not is_price(line.unit_price) or not is_weight(line.weight):
                continue
            if line.zone is None and family_id in self.zone_required:
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

        by_variant: dict[tuple[Zone | None, int], list[_Candidate]] = {}
        by_values: dict[tuple[Zone | None, _ValuesKey], list[_Candidate]] = {}
        any_by_variant: dict[int, list[_Candidate]] = {}
        any_by_values: dict[_ValuesKey, list[_Candidate]] = {}
        for cand in candidates:
            zone = cand.line.zone
            if not cand.line.conflicted:
                by_variant.setdefault((zone, cand.line.variant_id), []).append(cand)
                any_by_variant.setdefault(cand.line.variant_id, []).append(cand)
            key = values_key(cand.line)
            if key is not None:
                by_values.setdefault((zone, key), []).append(cand)
                any_by_values.setdefault(key, []).append(cand)
        unknown_by_variant: dict[tuple[Zone | None, int], list[_Unknown]] = {}
        unknown_by_values: dict[tuple[Zone | None, _ValuesKey], list[_Unknown]] = {}
        unknown_any_by_variant: dict[int, list[_Unknown]] = {}
        unknown_any_by_values: dict[_ValuesKey, list[_Unknown]] = {}
        for line, contract in unknown_base:
            if not line.conflicted:
                unknown_by_variant.setdefault((line.zone, line.variant_id), []).append((line, contract))
                unknown_any_by_variant.setdefault(line.variant_id, []).append((line, contract))
            key = values_key(line)
            if key is not None:
                unknown_by_values.setdefault((line.zone, key), []).append((line, contract))
                unknown_any_by_values.setdefault(key, []).append((line, contract))
        return _FamilyPool(
            candidates=tuple(candidates),
            by_variant={k: tuple(v) for k, v in by_variant.items()},
            by_values={k: tuple(v) for k, v in by_values.items()},
            unknown_by_variant={k: tuple(v) for k, v in unknown_by_variant.items()},
            unknown_by_values={k: tuple(v) for k, v in unknown_by_values.items()},
            any_by_variant={k: tuple(v) for k, v in any_by_variant.items()},
            any_by_values={k: tuple(v) for k, v in any_by_values.items()},
            unknown_any_by_variant={k: tuple(v) for k, v in unknown_any_by_variant.items()},
            unknown_any_by_values={k: tuple(v) for k, v in unknown_any_by_values.items()},
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
    zone_required: frozenset[int] = frozenset(),
) -> WorkLadder:
    """Ступень, справка, остаток и цифры строки КП-работы.

    `lines` — строки последних смет договоров истории (договоры объекта тендера
    и сметы КП загрузчик не отдаёт; договор вне `contracts` пропускается).
    `factors` — множитель ИПЦ по `estimate_id` сметы аналога; `None` — без приведения.
    Для многих строк одной сцены — `ladder_for` с одной `WorkHistory`.
    """
    return ladder_for(
        kp,
        WorkHistory(lines, families, contracts, factors=factors, zone_required=zone_required),
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
    pool = history.pool(kp.family_id)
    search = _Search(kp, history, pool, kp.excluded, tender_class_id)
    scopes = search.scopes
    dropped_on = search.dropped_on

    def _dropped_on_first_rung() -> tuple[int, ...]:
        """Без ступени — выпавшие в первой паре «область, ступень», где они нашлись бы (порядок подбора)."""
        for scope in scopes:
            for rung_no in _RUNGS:
                found = dropped_on(scope, rung_no, set())
                if found:
                    return tuple(sorted(found))
        return ()

    # «Ступень найдена» решает переход в следующую область, а не «цена посчитана»: выбросы области не отменяют.
    first = search.first_pair()
    chosen: RungNo | None = None
    chosen_scope: ZoneScope | None = None
    picked: list[_Candidate] = []
    if first is not None:
        chosen_scope, chosen, picked = first

    if chosen is None:
        base_reason = _no_history_reason(
            pool,
            kp.variant_id,
            search.kp_values if search.releasable_ladder and not search.by_values_blocked else None,
            conflicted=search.by_variant_blocked and (search.by_values_blocked or not search.releasable_ladder),
            composite=any(form != "simple" and p not in kp.conflicted for p, form in kp.forms.items()),
            zone_strict=search.strict,
        )
        reason: NoHistoryReason = base_reason
        # Пул без отметок — только у строки без ступени и только при отметках (спека «Не аналог» §3 п. 6).
        if history.has_marks(kp.family_id) or kp.excluded:
            unmarked = _Search(kp, history, history.pool_unmarked(kp.family_id), frozenset(), tender_class_id)
            if unmarked.first_pair() is not None:
                reason = "excluded"
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
            no_history_reason=reason,
            vat_dropped=_dropped_on_first_rung(),
            base_reason=base_reason,
        )
    assert chosen_scope is not None

    narrowed: _Narrowed | None = None
    if chosen in (2, 4):
        narrowed = _narrow_scales(kp, info, picked)
        picked = narrowed.picked

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
            zone=c.line.zone,
        )
        for c in reference
    )

    dropped = dropped_on(
        chosen_scope,
        chosen,
        {c.contract.contract_id for c in picked},
        accepts=narrowed.accepts if narrowed is not None and narrowed.accepts else None,
    )
    nearest = narrowed.nearest if narrowed is not None else ()
    matched = narrowed.matched if narrowed is not None else frozenset()

    return WorkLadder(
        rung_no=chosen,
        released=_released_names(chosen, info, skip={n.parameter_id for n in nearest} | matched),
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
        zone_scope=chosen_scope,
        nearest=nearest,
        matched=matched,
    )


class _Search:
    """Подбор ступени по одному пулу семьи для одной строки КП (`ladder_for` и `excluded_trace`).

    `excluded` — тендерные отметки строки КП: их строки не берёт ни срез кандидатов (`known`), ни срез
    выпавших (`dropped_on`); пул без отметок ищут с пустым `excluded`.
    """

    def __init__(
        self, kp: KpWork, history: WorkHistory, pool: _FamilyPool, excluded: frozenset[int], tender_class_id: int
    ) -> None:
        self.kp = kp
        self.pool = pool
        self.excluded = excluded
        self.tender_class_id = tender_class_id
        info = history.families.get(kp.family_id)
        self.releasable_ladder = (
            info is not None
            and kp.schema_id == info.schema_id
            and any(p in info.releasable_ids for p in info.main_parameter_ids)
        )
        required = _required_ids(info)
        self.kp_values = _values_key(kp.main_values, kp.forms, kp.conflicted, required, _releasable_ids(info))
        # Конфликт строки КП: ступени 1 и 3 не берут её вовсе; 2 и 4 — только если конфликт по
        # неотпускаемому параметру (отпускаемый и так отпущен).
        self.by_variant_blocked = bool(kp.conflicted)
        self.by_values_blocked = bool(kp.conflicted & set(required))
        # Область (спека «зона как предпочтение» §1 п. 2): семья с флажком — только своя зона; строка КП без
        # зоны — вся история; иначе своя зона, а если в ней ни одна ступень ничего не нашла — все остальные.
        self.strict = kp.zone_required or kp.family_id in history.zone_required
        self.scopes = _scopes(kp.zone, strict=self.strict)
        self.scales: Mapping[int, Scale] = {} if info is None else info.scales

    # Ступень берёт строки области: ступени 1 и 3 — с вариантом строки КП, ступени 2 и 4 — с её значениями
    # неотпускаемых параметров на действующей версии схемы, и только если у строки КП доступна лестница
    # отпускаемых. Класс: 1 и 2 — класс тендера.
    def rung_slice(self, scope: ZoneScope, rung_no: RungNo, zoned: Mapping, anywhere: Mapping) -> tuple:
        kp = self.kp
        if rung_no in (1, 3):
            if self.by_variant_blocked:
                return ()
            tail: int | _ValuesKey = kp.variant_id
        else:
            if not self.releasable_ladder or self.by_values_blocked:
                return ()
            tail = self.kp_values
        if scope == "own":
            items: tuple = () if kp.zone is None else zoned.get((kp.zone, tail), ())
        else:
            everywhere = anywhere.get(tail, ())
            items = everywhere if scope == "any" else tuple(i for i in everywhere if _zone_of(i) != kp.zone)
        if self.excluded:
            items = tuple(item for item in items if _line_of(item).position_item_id not in self.excluded)
        return items

    def known(self, scope: ZoneScope, rung_no: RungNo) -> tuple:
        return self.rung_slice(scope, rung_no, *_known_maps(rung_no, self.pool))

    def class_fits(self, rung_no: RungNo, contract: HistoryContract) -> bool:
        return (contract.rate_class_id == self.tender_class_id) == (rung_no in (1, 2))

    def dropped_on(
        self,
        scope: ZoneScope,
        rung_no: RungNo,
        picked_ids: set[int],
        accepts: Mapping[int, tuple[frozenset[int], bool]] | None = None,
    ) -> set[int]:
        """Договоры без НДС той же пары, не попавшие в набор. С `accepts` (сужение по шкалам, спека ближайшего
        значения §2 п. 5) строка проходит, только если по каждому суженному параметру у неё число из взятых
        кругов либо числа нет и круг без числа остался."""
        unknown = self.rung_slice(scope, rung_no, *_unknown_maps(rung_no, self.pool))
        return {
            contract.contract_id
            for line, contract in unknown
            if self.class_fits(rung_no, contract)
            and contract.contract_id not in picked_ids
            and (accepts is None or self._accepted(line, accepts))
        }

    def _accepted(self, line: WorkHistoryLine, accepts: Mapping[int, tuple[frozenset[int], bool]]) -> bool:
        for p, (value_ids, without_kept) in accepts.items():
            value_id = _number_value(line, p, self.scales[p])
            if not (value_id in value_ids if value_id is not None else without_kept):
                return False
        return True

    def pairs(self) -> list[ExcludedStep]:
        return [(scope, rung_no) for scope in self.scopes for rung_no in _RUNGS]

    def picked_in(self, scope: ZoneScope, rung_no: RungNo) -> list[_Candidate]:
        return [c for c in self.known(scope, rung_no) if self.class_fits(rung_no, c.contract)]

    def first_pair(self) -> tuple[ZoneScope, RungNo, list[_Candidate]] | None:
        """Первая пара «область, ступень», где нашлись кандидаты класса; `None` — ступени нет."""
        for scope, rung_no in self.pairs():
            picked = self.picked_in(scope, rung_no)
            if picked:
                return scope, rung_no, picked
        return None


def excluded_trace(kp: KpWork, history: WorkHistory, *, tender_class_id: int) -> ExcludedTrace:
    """След исключённого (спека «Не аналог» §3 п. 5): отмеченные кандидаты (с «ошибкой в цене» или из
    `kp.excluded`) в парах «область, ступень» от `S0` до `S1` включительно, каждый — с первой парой, где он
    стоял бы. `S0` — первая пара подбора по пулу без отметок и без `kp.excluded`, `S1` — первая пара по
    обычному пулу с отметками (или конец обхода); `S0` нет — след пуст. Порядок — обход пар, внутри пары —
    порядок строк истории в пуле. Пул без отметок строится только при отметках в семье или непустом
    `kp.excluded`, в том числе у строки со ступенью."""
    if not (history.has_marks(kp.family_id) or kp.excluded):
        return ()
    clean = _Search(kp, history, history.pool_unmarked(kp.family_id), frozenset(), tender_class_id)
    marked = _Search(kp, history, history.pool(kp.family_id), kp.excluded, tender_class_id)
    trace: list[tuple[_Candidate, ExcludedStep]] = []
    seen: set[int] = set()
    started = False
    for scope, rung_no in clean.pairs():
        clean_picked = clean.picked_in(scope, rung_no)
        if not started:
            if not clean_picked:
                continue
            started = True
        for cand in clean_picked:
            pid = cand.line.position_item_id
            if pid in seen or not (cand.line.price_error or pid in kp.excluded):
                continue
            seen.add(pid)
            trace.append((cand, (scope, rung_no)))
        if marked.picked_in(scope, rung_no):
            break
    return tuple(trace)


def _line_of(item: _Candidate | _Unknown) -> WorkHistoryLine:
    return item.line if isinstance(item, _Candidate) else item[0]


def _scopes(zone: Zone | None, *, strict: bool) -> tuple[ZoneScope, ...]:
    """Области подбора в порядке обхода (спека «зона как предпочтение» §1 п. 2)."""
    if strict:
        return ("own",)
    return ("any",) if zone is None else ("own", "other")


def _zone_of(item: _Candidate | _Unknown) -> Zone | None:
    return item.line.zone if isinstance(item, _Candidate) else item[0].zone


def _known_maps(rung_no: RungNo, pool: _FamilyPool) -> tuple[Mapping, Mapping]:
    """Кандидаты ступени с зоной в ключе (область `own`) и без неё (`other`, `any`)."""
    if rung_no in (1, 3):
        return pool.by_variant, pool.any_by_variant
    return pool.by_values, pool.any_by_values


def _unknown_maps(rung_no: RungNo, pool: _FamilyPool) -> tuple[Mapping, Mapping]:
    if rung_no in (1, 3):
        return pool.unknown_by_variant, pool.unknown_any_by_variant
    return pool.unknown_by_values, pool.unknown_any_by_values


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
    части `p` — кандидаты той же семьи и версии схемы из области подбора части (§1 п. 6: `own`, нет — `other`,
    у строки без зоны — `any`, у семьи с флажком — только `own`), у которых этот параметр равен `p` по `value_id`,
    даже если он отпускаемый; остальные главные параметры — по ключу ступени 2. Сначала класс тендера,
    пусто — другой класс. Выбросы — как в лестнице, отдельно по каждой части. Части целью не становятся.
    """
    info = history.families.get(kp.family_id)
    if info is None:
        return []
    required = _required_ids(info)
    releasable = _releasable_ids(info)
    blocked = bool(kp.conflicted & set(required))
    candidates = [c for c in history.pool(kp.family_id).candidates if c.line.position_item_id not in kp.excluded]
    strict = kp.zone_required or kp.family_id in history.zone_required
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
            and c.line.schema_id == info.schema_id
            and not c.line.conflicted & unusable
            and _values_key(c.line.main_values, c.line.forms, c.line.conflicted, rest_required, rest_releasable)
            == kp_rest
        ]
        parts = []
        for value_id, text in composite.parts:
            in_parameter = [c for c in matching if c.line.main_values.get(parameter_id) == value_id]
            matched, zone_scope = _part_scope(in_parameter, kp.zone, strict=strict)
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
                    zone_scope=zone_scope,
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


def _part_scope(
    matched: list[_Candidate], zone: Zone | None, *, strict: bool
) -> tuple[list[_Candidate], ZoneScope | None]:
    """Кандидаты части и их область (спека «зона как предпочтение» §1 п. 6): своя зона; нет в ней — остальные
    и строки без зоны; у строки КП без зоны — все; у семьи с флажком — только своя зона."""
    if zone is None:
        if strict:
            return [], None
        return matched, "any" if matched else None
    own = [c for c in matched if c.line.zone == zone]
    if own:
        return own, "own"
    if strict:
        return [], None
    other = [c for c in matched if c.line.zone != zone]
    return other, "other" if other else None


def _released_names(
    rung_no: RungNo, info: FamilyLadderInfo | None, skip: Collection[int] = ()
) -> tuple[str, ...]:
    """Имена отпускаемых параметров, кроме `skip` — шкал, по которым набор сужен или совпал."""
    if rung_no in (1, 3) or info is None:
        return ()
    return tuple(info.names[p] for p in info.main_parameter_ids if p in info.releasable_ids and p not in skip)


def _number_value(line: WorkHistoryLine, parameter_id: int, scale: Scale) -> int | None:
    """value_id строки, если у неё есть число шкалы (спека ближайшего значения §2 п. 3), иначе `None`:
    значение не уточнено, конфликт, снято или слито и ещё не пересвязано."""
    if parameter_id in line.conflicted:
        return None
    value_id = line.main_values.get(parameter_id)
    return value_id if value_id in scale.numbers else None


def _distance(value: Decimal, target: Decimal) -> Decimal:
    """Расстояние до числа КП — отношение большего числа к меньшему (спека ближайшего значения §2 п. 4)."""
    with localcontext(_CONTEXT):
        return max(value, target) / min(value, target)


@dataclass(frozen=True)
class _Narrowed:
    picked: list[_Candidate]
    nearest: tuple[NearestParam, ...]
    matched: frozenset[int]
    #: parameter_id не пропущенной шкалы -> (value_id взятых кругов, остался ли круг без числа).
    accepts: Mapping[int, tuple[frozenset[int], bool]]
    #: Пропущенные шкалы и шкалы без чисел в окончательном наборе.
    released_scales: frozenset[int]


@dataclass(frozen=True)
class _Step:
    parameter_id: int
    target: Decimal
    kp_contracts: int
    after: tuple[str, ...]


def _narrow_scales(kp: KpWork, info: FamilyLadderInfo | None, picked: Sequence[_Candidate]) -> _Narrowed:
    """Сужение набора ступени 2 или 4 до ближайших значений шкал (спека ближайшего значения §2 п. 2–7).

    Шкалы идут по порядку параметров, каждая работает на наборе предыдущей. Первый круг берётся всегда,
    поэтому набор не пустеет. Пометки по параметрам строятся по окончательному набору, после всех шкал."""
    current = list(picked)
    if info is None or not info.scales:
        return _Narrowed(current, (), frozenset(), {}, frozenset())
    skipped: set[int] = set()
    accepts: dict[int, tuple[frozenset[int], bool]] = {}
    steps: list[_Step] = []
    earlier: list[str] = []
    for p in _releasable_ids(info):
        scale = info.scales.get(p)
        if scale is None:
            continue
        kp_value = kp.main_values.get(p)
        if (
            p in kp.conflicted
            or _form(kp.forms, kp.conflicted, p) != "simple"
            or kp_value is None
            or kp_value not in scale.numbers
        ):
            skipped.add(p)
            continue
        target = scale.numbers[kp_value]
        by_distance: dict[Decimal, list[_Candidate]] = {}
        without: list[_Candidate] = []
        kp_contracts: set[int] = set()
        for cand in current:
            value_id = _number_value(cand.line, p, scale)
            if value_id is None:
                without.append(cand)
                continue
            number = scale.numbers[value_id]
            if number == target:
                kp_contracts.add(cand.contract.contract_id)
            by_distance.setdefault(_distance(number, target), []).append(cand)
        if not by_distance:
            skipped.add(p)
            continue
        # Круги по возрастанию расстояния; круг без числа последний и только когда числовые кончились при
        # договорах < MIN_CONTRACTS. Круг: (расстояние или None для круга без числа, кандидаты, добавил ли
        # он новый договор). Хвост без нового договора отбрасывается (спека ред. 5, §2 п. 4).
        rounds: list[tuple[Decimal | None, list[_Candidate], bool]] = []
        contracts: set[int] = set()
        for distance in sorted(by_distance):
            before = len(contracts)
            contracts.update(c.contract.contract_id for c in by_distance[distance])
            rounds.append((distance, by_distance[distance], len(contracts) > before))
            if len(contracts) >= MIN_CONTRACTS:
                break
        if len(contracts) < MIN_CONTRACTS:
            before = len(contracts)
            contracts.update(c.contract.contract_id for c in without)
            rounds.append((None, without, len(contracts) > before))
        while len(rounds) > 1 and not rounds[-1][2]:
            rounds.pop()
        without_kept = rounds[-1][0] is None
        reach = [d for d, _, _ in rounds if d is not None][-1]
        taken = [c for _, group, _ in rounds for c in group]
        accepts[p] = (
            frozenset(vid for vid, number in scale.numbers.items() if _distance(number, target) <= reach),
            without_kept,
        )
        steps.append(_Step(p, target, len(kp_contracts), tuple(earlier)))
        earlier.append(info.names[p])
        keep = {c.line.position_item_id for c in taken}
        current = [c for c in current if c.line.position_item_id in keep]

    nearest: list[NearestParam] = []
    matched: set[int] = set()
    released = set(skipped)
    for step in steps:
        p = step.parameter_id
        scale = info.scales[p]
        found: dict[int, Decimal] = {}
        without_value = False
        for cand in current:
            value_id = _number_value(cand.line, p, scale)
            if value_id is None:
                without_value = True
            else:
                found[value_id] = scale.numbers[value_id]
        if not found:
            released.add(p)
            continue
        others = [n for n in found.values() if n != step.target]
        if not others and not without_value:
            matched.add(p)
            continue
        higher = any(n > step.target for n in others)
        lower = any(n < step.target for n in others)
        direction: Direction | None = None
        if others:
            direction = "both" if higher and lower else "up" if higher else "down"
        ordered = sorted(found, key=lambda vid: (found[vid], vid))
        kp_value_id = kp.main_values[p]
        assert kp_value_id is not None
        nearest.append(
            NearestParam(
                parameter_id=p,
                name=info.names[p],
                kp_value=scale.texts[kp_value_id],
                values=tuple(scale.texts[vid] for vid in ordered),
                kp_contracts=step.kp_contracts,
                direction=direction,
                without_value=without_value,
                after=step.after,
            )
        )
    return _Narrowed(current, tuple(nearest), frozenset(matched), accepts, frozenset(released))


def _no_history_reason(
    pool: _FamilyPool,
    variant_id: int,
    values_key: _ValuesKey | None,
    *,
    conflicted: bool = False,
    composite: bool = False,
    zone_strict: bool = False,
) -> NoHistoryReason:
    """Причина прежней цепочки по кандидатам семьи любого класса и любой зоны (§2.10); `excluded` ставит
    `ladder_for` поверх неё. Первая причина цепочки (§2.10). Параметры совпали —
    у кандидата тот же вариант либо (ступени 2 и 4 доступны, `values_key` задан) тот же ключ ступеней
    2 и 4. `conflicted` — у строки КП есть конфликтный главный параметр, мешающий на всех ступенях:
    причина раньше `no_params`. `composite` — у строки составное главное значение (спека упрощения
    каталога §2.8): причина раньше `no_params` и `no_zone_match`. `no_zone_match` — только у семьи с
    флажком «зона обязательна» (`zone_strict`): у остальных зона не отсекает, и цепочка кончается `no_params`."""
    if not pool.candidates:
        return "no_family"
    if conflicted:
        return "path_conflict"
    if composite:
        return "composite"
    if variant_id not in pool.variant_ids and (values_key is None or values_key not in pool.values_keys):
        return "no_params"
    return "no_zone_match" if zone_strict else "no_params"

"""Правила «подозрительных строк договоров» (спека `2026-11-24-suspicious-lines-design.md` §2).

Чистый модуль: ни базы, ни SQLAlchemy. Загрузчики готовят входы — строки истории работ
(`WorkHistoryLine`) и соседей по смете (`NeighborRow`), — а `build_suspicion_index` считает
статистику групп и факты подозрения, `state_of` — состояние строки в списке.

Точность держится рациональной арифметикой: нетто кандидата, цены договоров, обычная цена,
квартили и ВСЕ сравнения (однородность `Q3 <= MAX_SPREAD * Q1`, выброс, кратность двойника)
считаются в `fractions.Fraction`, то есть без округления; границы «ровно 10», «ровно 2» держатся
буквально при любой ставке НДС. `Decimal` — только на выходе (`SuspicionIndex.net` и
`OutlierFact.net` — из `gross_to_net`, как везде в приложении; остальное переводится из `Fraction`
под явным контекстом). Кратность `fold = max(r, 1 / r)` для `r = net / usual`: условие
`d >= ln 2` (`d = |ln r|`) равно `fold >= 2`, а «у кого `d` больше» равно «у кого `fold` больше».
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from fractions import Fraction
from typing import Literal

from money.price import is_price
from money.vat import gross_to_net
from parser.summary_block import ARITHMETIC_PRECISION
from services.analog_ladder import MIN_NOMINAL, WorkHistoryLine

#: Договоров в группе не меньше этого — обычная цена есть.
MIN_CONTRACTS = 3
#: Однородная группа: Q3 / Q1 цен договоров не больше этого.
MAX_SPREAD = Decimal(2)
#: Выброс: цена строки в столько раз и более выше или ниже обычной.
OUTLIER_RATIO = Decimal(10)
#: Двойник годен, если его цена дальше обычной во столько раз и более.
TWIN_FOLD = Decimal(2)
#: Соседи — на 1..TWIN_WINDOW мест раньше или позже.
TWIN_WINDOW = 3

#: Перевод точных рациональных в `Decimal` — явный контекст, не глобальный.
_CONTEXT = Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])

GroupKey = tuple[int, int]  # (family_id, unit_id)


@dataclass(frozen=True)
class OutlierBasis:
    family_id: int
    unit_id: int
    direction: Literal["up", "down"]


@dataclass(frozen=True)
class TwinBasis:
    neighbor_item_id: int


Basis = OutlierBasis | TwinBasis


@dataclass(frozen=True)
class OutlierFact:
    basis: OutlierBasis
    ratio: Decimal
    usual: Decimal
    net: Decimal


@dataclass(frozen=True)
class TwinFact:
    basis: TwinBasis


Fact = OutlierFact | TwinFact


@dataclass(frozen=True)
class NeighborRow:
    """Строка сметы без глав с номером места."""

    position_item_id: int
    estimate_id: int
    place: int
    unit_id: int | None
    unit_cost_total: Decimal | None


@dataclass(frozen=True)
class ContractPrice:
    contract_id: int
    lines: int
    price: Decimal
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class GroupStats:
    family_id: int
    unit_id: int
    usual: Decimal | None
    homogeneous: bool
    contracts: tuple[ContractPrice, ...]


@dataclass(frozen=True)
class SuspicionIndex:
    facts: Mapping[int, tuple[Fact, ...]]
    rows: Mapping[int, tuple[NeighborRow, ...]]  # смета -> строки без глав по месту
    groups: Mapping[GroupKey, GroupStats]
    group_of: Mapping[int, GroupKey]
    net: Mapping[int, Decimal]


def basis_of(fact: Fact) -> Basis:
    return fact.basis


def _dec(value: Fraction) -> Decimal:
    """Точное рациональное -> `Decimal` под явным контекстом (не наследует глобальный)."""
    with localcontext(_CONTEXT):
        return Decimal(value.numerator) / Decimal(value.denominator)


def _quartiles_exact(values: Sequence[Fraction]) -> tuple[Fraction, Fraction]:
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[2]


def quartiles(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """Q1 и Q3 методом «включительно»; считаются точно, в `Decimal` переводится результат."""
    q1, q3 = _quartiles_exact([Fraction(v) for v in values])
    return _dec(q1), _dec(q3)


def _fold(net: Fraction, usual: Fraction) -> Fraction:
    """Кратность отклонения цены от обычной: `max(net / usual, usual / net)`."""
    return net / usual if net >= usual else usual / net


def _candidate_net(line: WorkHistoryLine) -> Fraction | None:
    """Точное нетто кандидата; `None` — строка не кандидат."""
    if line.lump or line.vat_base is None or line.unit_id is None:
        return None
    if line.unit_price is None or not is_price(line.unit_price):
        return None
    net = Fraction(line.unit_price) * 100 / (100 + Fraction(line.vat_base))
    return net if net >= Fraction(MIN_NOMINAL) else None


def _group_stats(
    key: GroupKey, members: Sequence[WorkHistoryLine], exact: Mapping[int, Fraction]
) -> tuple[GroupStats, Fraction | None]:
    by_contract: dict[int, list[Fraction]] = defaultdict(list)
    for line in members:
        if not line.price_error:
            by_contract[line.contract_id].append(exact[line.position_item_id])
    priced = sorted(
        ((cid, prices, statistics.median(prices)) for cid, prices in by_contract.items()),
        key=lambda t: (t[2], t[0]),
    )
    contracts = tuple(
        ContractPrice(
            contract_id=cid,
            lines=len(prices),
            price=_dec(price),
            low=_dec(min(prices)),
            high=_dec(max(prices)),
        )
        for cid, prices, price in priced
    )
    usual: Fraction | None = None
    homogeneous = False
    if len(priced) >= MIN_CONTRACTS:
        prices_only = [t[2] for t in priced]
        usual = statistics.median(prices_only)
        q1, q3 = _quartiles_exact(prices_only)
        homogeneous = q1 > 0 and q3 <= Fraction(MAX_SPREAD) * q1
    stats = GroupStats(
        family_id=key[0],
        unit_id=key[1],
        usual=None if usual is None else _dec(usual),
        homogeneous=homogeneous,
        contracts=contracts,
    )
    return stats, usual


def _outlier_fact(
    net: Fraction, net_decimal: Decimal, key: GroupKey, stats: GroupStats, usual: Fraction | None
) -> OutlierFact | None:
    if not stats.homogeneous or usual is None or stats.usual is None:
        return None
    ratio_limit = Fraction(OUTLIER_RATIO)
    direction: Literal["up", "down"]
    if net >= ratio_limit * usual:
        direction = "up"
    elif ratio_limit * net <= usual:
        direction = "down"
    else:
        return None
    return OutlierFact(
        basis=OutlierBasis(family_id=key[0], unit_id=key[1], direction=direction),
        ratio=_dec(net / usual),
        usual=stats.usual,
        net=net_decimal,
    )


def _twin_suspects(
    a: int,
    b: int,
    candidates: Mapping[int, WorkHistoryLine],
    fold_of: Mapping[int, Fraction | None],
) -> list[int]:
    """Кто из пары подозрителен (спека §2 п. 4); `fold_of` — по кандидатам."""
    marked = [i for i in (a, b) if i in candidates and candidates[i].price_error]
    if marked:
        return marked
    twin_fold = Fraction(TWIN_FOLD)
    eligible = []
    for i in (a, b):
        if i not in candidates:
            continue
        fold = fold_of[i]
        if fold is None or fold >= twin_fold:
            eligible.append(i)
    if len(eligible) == 2:
        fa, fb = fold_of[a], fold_of[b]
        if fa is not None and fb is not None:
            if fa == fb:
                return eligible
            return [a] if fa > fb else [b]
    return eligible


def build_suspicion_index(lines: Sequence[WorkHistoryLine], neighbors: Sequence[NeighborRow]) -> SuspicionIndex:
    exact: dict[int, Fraction] = {}
    net: dict[int, Decimal] = {}
    candidates: dict[int, WorkHistoryLine] = {}
    group_of: dict[int, GroupKey] = {}
    members: dict[GroupKey, list[WorkHistoryLine]] = defaultdict(list)
    for line in lines:
        n = _candidate_net(line)
        if n is None or line.unit_id is None or line.unit_price is None or line.vat_base is None:
            continue
        key = (line.family_id, line.unit_id)
        candidates[line.position_item_id] = line
        exact[line.position_item_id] = n
        net[line.position_item_id] = gross_to_net(line.unit_price, line.vat_base)
        group_of[line.position_item_id] = key
        members[key].append(line)
    groups: dict[GroupKey, GroupStats] = {}
    usual_of: dict[GroupKey, Fraction | None] = {}
    for key, ms in members.items():
        groups[key], usual_of[key] = _group_stats(key, ms, exact)

    rows_by_estimate: dict[int, list[NeighborRow]] = defaultdict(list)
    for row in neighbors:
        rows_by_estimate[row.estimate_id].append(row)
    rows = {eid: tuple(sorted(rs, key=lambda r: r.place)) for eid, rs in rows_by_estimate.items()}

    facts: dict[int, list[Fact]] = defaultdict(list)
    fold_of: dict[int, Fraction | None] = {}
    for item_id in candidates:
        key = group_of[item_id]
        usual = usual_of[key]
        fact = _outlier_fact(exact[item_id], net[item_id], key, groups[key], usual)
        if fact is not None:
            facts[item_id].append(fact)
        fold_of[item_id] = None if usual is None else _fold(exact[item_id], usual)

    twin_facts: dict[int, list[TwinFact]] = defaultdict(list)
    for estimate_rows in rows.values():
        for i, left in enumerate(estimate_rows):
            for j in range(i + 1, len(estimate_rows)):
                right = estimate_rows[j]
                if right.place - left.place > TWIN_WINDOW:
                    break
                a, b = left.position_item_id, right.position_item_id
                if a not in candidates and b not in candidates:
                    continue
                if not (is_price(left.unit_cost_total) and is_price(right.unit_cost_total)):
                    continue
                if left.unit_cost_total != right.unit_cost_total:
                    continue
                if left.unit_id is None or right.unit_id is None or left.unit_id == right.unit_id:
                    continue
                for suspect in _twin_suspects(a, b, candidates, fold_of):
                    other = b if suspect == a else a
                    twin_facts[suspect].append(TwinFact(basis=TwinBasis(neighbor_item_id=other)))
    for item_id, twins in twin_facts.items():
        facts[item_id].extend(sorted(twins, key=lambda f: f.basis.neighbor_item_id))

    return SuspicionIndex(
        facts={k: tuple(v) for k, v in facts.items()},
        rows=rows,
        groups=groups,
        group_of=group_of,
        net=net,
    )


def state_of(
    facts: Sequence[Fact], decided: Collection[Basis], price_error: bool
) -> Literal["unresolved", "resolved"] | None:
    """Состояние строки в списке (спека §2 п. 7); `None` — строки в списке нет."""
    if price_error:
        return "resolved"
    if any(basis_of(f) not in decided for f in facts):
        return "unresolved"
    if decided:
        return "resolved"
    return None

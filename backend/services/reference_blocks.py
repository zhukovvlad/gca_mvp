"""Справочные блоки строки КП: та же работа в другой зоне (А) и семья по связи с пересчётом (Г)
(спека справочных аналогов §2.4, §2.5, §2.8–§2.11).

Чистый расчёт без `db`: входы — `WorkHistory` сцены (кандидаты семьи уже посчитаны), состояния
связей и значения параметров, загруженные вызывающим. Здесь же пересчёт строки договора в единицу
строки КП (`conversion_for`), пометки выбросов одной функцией (`reference_marks`) и свёртка строк
группы. Деньги и множители — `Decimal` в контексте `price_basis._CONTEXT`.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from crud.reference_lines import ParamText
from money.inflation import YearMonth
from services.analog_ladder import (
    KpWork,
    RungNo,
    WorkHistory,
    WorkLadder,
    date_rank,
    ladder_for,
)
from services.family_links import LinkState
from services.reference_math import (
    UNIT_PAIRS,
    Conversion,
    FoldKey,
    LinkKind,
    LinkNumber,
    constant_factor,
    fold_rows,
    link_factor,
    link_number,
    outlier_ratios_of,
    price_in_kp_unit,
    units_equal,
)
from services.zone_rules import Zone

#: Порядок зон блока А (спека §2.4).
ZONE_ORDER: tuple[Zone, ...] = typing.get_args(Zone)

_STALE = "параметр устарел"


@dataclass(frozen=True)
class RefRow:
    """Строка договора в блоке: цена, пересчёт в единицу строки КП и пометка выброса."""

    position_item_id: int
    contract_id: int
    zone: Zone | None
    #: Единица строки договора (`catalog_positions.unit_id`).
    unit_id: int | None
    #: Цена без приведения.
    nominal: Decimal
    #: Цена с приведением по ИПЦ, если ряд выбран; иначе равна номиналу.
    net: Decimal
    #: Пересчёт «по связи»; `None` — единицы равны либо пересчёта нет.
    conversion: Conversion | None
    #: Почему пересчёта по связи нет, когда он нужен (спека §2.8); `None` — причины нет.
    no_conversion_reason: str | None
    #: Цена в единице строки КП; `None` — пересчёта нет и единицы различны.
    kp_unit_net: Decimal | None
    #: Отношение цены к медиане цен набора, если оно за границами; иначе `None`.
    outlier_ratio: Decimal | None
    #: Значение параметра пересчёта строки договора, как записано.
    param_value: str | None
    #: Семья строки договора; `None` — у строки нет семьи.
    family_id: int | None = None


@dataclass(frozen=True)
class ZoneGroup:
    zone: Zone
    rung_no: RungNo
    released: tuple[str, ...]
    other_class: bool
    rows: tuple[RefRow, ...]


@dataclass(frozen=True)
class LinkGroup:
    link_id: int
    family_id: int
    kind: LinkKind
    other_class: bool
    rows: tuple[RefRow, ...]


# ---------------------------------------------------------------------------
#  Блок А
# ---------------------------------------------------------------------------


def opens_zone_block(ladder: WorkLadder) -> bool:
    """Блок А открыт (спека «Не аналог» §5 п. 2): причина `no_zone_match`, либо `excluded` над ней —
    исключённая строка своей зоны не должна прятать строки других зон."""
    reason = ladder.no_history_reason
    return reason == "no_zone_match" or (reason == "excluded" and ladder.base_reason == "no_zone_match")


def zone_groups(
    kp: KpWork,
    history: WorkHistory,
    *,
    tender_class_id: int,
    tender_contractor_id: int,
    target_month: YearMonth | None,
) -> tuple[ZoneGroup, ...]:
    """Блок А: у строки с причиной `no_zone_match` — по группе на каждую другую зону, где есть
    ступень. Ступень, подпись и порядок строк — как у `ladder_for` для строки КП с той зоной."""
    if kp.zone is None:
        return ()

    def ladder_in(zone: Zone) -> WorkLadder:
        return ladder_for(
            dataclasses.replace(kp, zone=zone),
            history,
            tender_class_id=tender_class_id,
            tender_contractor_id=tender_contractor_id,
            target_month=target_month,
        )

    if not opens_zone_block(ladder_in(kp.zone)):
        return ()
    units = {c.line.position_item_id: c.line.unit_id for c in history.pool(kp.family_id).candidates}
    groups: list[ZoneGroup] = []
    for zone in ZONE_ORDER:
        if zone == kp.zone:
            continue
        ladder = ladder_in(zone)
        if ladder.rung_no is None:
            continue
        rows = tuple(
            RefRow(
                position_item_id=analog.position_item_id,
                contract_id=analog.contract_id,
                zone=zone,
                unit_id=units.get(analog.position_item_id),
                nominal=analog.nominal,
                net=analog.net,
                conversion=None,
                no_conversion_reason=None,
                kp_unit_net=analog.net,
                outlier_ratio=analog.outlier_ratio,
                param_value=None,
                family_id=kp.family_id,
            )
            for analog in ladder.analogs
        )
        groups.append(
            ZoneGroup(zone=zone, rung_no=ladder.rung_no, released=ladder.released, other_class=ladder.other_class, rows=rows)
        )
    return tuple(groups)


def other_zones(groups: Sequence[ZoneGroup]) -> list[dict]:
    """Подпись строки в таблице (спека §2.5): по записи на группу, строки и договоры — до свёртки."""
    return [
        {
            "zone": group.zone,
            "lines": len(group.rows),
            "contracts": len({row.contract_id for row in group.rows}),
        }
        for group in groups
    ]


# ---------------------------------------------------------------------------
#  Пересчёт строки договора (§2.8)
# ---------------------------------------------------------------------------


def _link_between(links: Sequence[LinkState], first: int | None, second: int | None) -> LinkState | None:
    """Действующая связь семей `first` и `second`; пара уникальна, поэтому не больше одной."""
    if first is None or second is None:
        return None
    for state in links:
        row = state.link
        if state.active and {row.family_lo_id, row.family_hi_id} == {first, second} and first != second:
            return state
    return None


def _side(state: LinkState, family_id: int) -> tuple[int | None, bool, str | None]:
    """Параметр стороны семьи `family_id`: (id, жив ли, имя)."""
    row = state.link
    if family_id == row.family_lo_id:
        return row.param_lo_id, state.live_lo, None if state.lo is None else state.lo.param_name
    return row.param_hi_id, state.live_hi, None if state.hi is None else state.hi.param_name


def _number(
    state: LinkState, family_id: int, position_item_id: int, values: Mapping[tuple[int, int], ParamText]
) -> LinkNumber | None:
    """`x` строки из значения параметра стороны семьи `family_id`; `None` — нет числа."""
    param_id, live, _ = _side(state, family_id)
    if param_id is None or not live:
        return None
    text = values.get((position_item_id, param_id))
    if text is None:
        return None
    return link_number(state.link.kind, text.text, text.parameter_name, source=text.source, composite=text.composite)


def link_units(states: Sequence[LinkState]) -> dict[int, str]:
    """Символы единиц по id для сторон заданных связей — вход `conversion_for`."""
    return {
        unit_id: symbol
        for state in states
        for unit_id, symbol in (
            (state.link.unit_lo_id, None if state.lo is None else state.lo.unit),
            (state.link.unit_hi_id, None if state.hi is None else state.hi.unit),
        )
        if symbol is not None
    }


def link_param_id(links: Sequence[LinkState], contract_family_id: int | None, kp_family_id: int | None) -> int | None:
    """Параметр стороны семьи строки договора у действующей связи с семьёй строки КП; `None` — связи
    нет или у вида нет параметра (`constant`)."""
    state = _link_between(links, contract_family_id, kp_family_id)
    if state is None or contract_family_id is None:
        return None
    return _side(state, contract_family_id)[0]


def _unlisted_text(text: ParamText) -> str:
    if text.source == "path_conflict":
        return "конфликт по разделам"
    return text.text or "не уточнено"


def conversion_for(
    *,
    contract_unit_id: int | None,
    kp_unit_id: int | None,
    contract_family_id: int | None,
    kp_family_id: int | None,
    links: Sequence[LinkState],
    units: Mapping[int, str],
    values: Mapping[tuple[int, int], ParamText],
    position_item_id: int,
) -> tuple[Conversion | None, str | None, bool]:
    """Пересчёт строки договора в единицу строки КП: `(N, причина «без пересчёта», нужен ли пересчёт)`.

    Единицы заданы и равны — пересчёта нет. Иначе по действующей связи семей, если единицы строк
    — единицы сторон связи; нет связи или единицы не те — пересчёт нужен, `N` вводит человек
    (причины нет). Связь есть, а числа нет — причина по спеке §2.8. `units` — символы единиц по
    id: по ним у видов с параметром решается, меньшая единица у строки договора или большая."""
    if units_equal(contract_unit_id, kp_unit_id):
        return None, None, False
    manual: tuple[Conversion | None, str | None, bool] = (None, None, True)
    state = _link_between(links, contract_family_id, kp_family_id)
    if state is None or contract_family_id is None:
        return manual
    row = state.link
    contract_is_lo = contract_family_id == row.family_lo_id
    contract_side_unit = row.unit_lo_id if contract_is_lo else row.unit_hi_id
    kp_side_unit = row.unit_hi_id if contract_is_lo else row.unit_lo_id
    if contract_unit_id != contract_side_unit or kp_unit_id != kp_side_unit:
        return manual
    if row.kind == "constant":
        if row.factor is None or not row.basis:
            return manual
        factor = constant_factor(row.factor, from_is_contract_family=row.factor_from_id == contract_family_id)
        return Conversion("link", factor, row.basis, row.id), None, True
    kind = typing.cast(LinkKind, row.kind)
    smaller, larger = UNIT_PAIRS[kind]
    contract_symbol = units.get(contract_unit_id) if contract_unit_id is not None else None
    kp_symbol = units.get(kp_unit_id) if kp_unit_id is not None else None
    if {contract_symbol, kp_symbol} != {smaller, larger}:
        return manual
    param_id, live, name = _side(state, contract_family_id)
    if param_id is None:
        return manual
    if not live:
        return None, _STALE, True
    text = values.get((position_item_id, param_id))
    if text is None:
        return None, f"у строки нет значения «{name}»", True
    number = link_number(kind, text.text, text.parameter_name, source=text.source, composite=text.composite)
    if number is None:
        return None, f"значение «{_unlisted_text(text)}» — не число", True
    factor = link_factor(kind, number.x, contract_unit_is_smaller=contract_symbol == smaller)
    return Conversion("link", factor, number.basis, row.id), None, True


# ---------------------------------------------------------------------------
#  Блок Г
# ---------------------------------------------------------------------------


def _other_side(state: LinkState, family_id: int) -> int | None:
    row = state.link
    if family_id == row.family_lo_id:
        return row.family_hi_id
    if family_id == row.family_hi_id:
        return row.family_lo_id
    return None


def link_value_pairs(kp: KpWork, history: WorkHistory, links: Sequence[LinkState]) -> set[tuple[int, int]]:
    """Пары `(position_item_id, parameter_id)`, значения которых нужны блоку Г: у строк связанных
    семей в зоне строки КП — параметр их стороны, у строки КП — параметр её стороны; только живые
    стороны действующих связей с параметром."""
    pairs: set[tuple[int, int]] = set()
    if kp.zone is None:
        return pairs
    for state in links:
        other = _other_side(state, kp.family_id)
        if other is None or not state.active or state.link.kind == "constant":
            continue
        param_id, live, _ = _side(state, other)
        if param_id is not None and live:
            pairs.update(
                (c.line.position_item_id, param_id)
                for c in history.pool(other).candidates
                if c.line.zone == kp.zone
            )
        kp_param, kp_live, _ = _side(state, kp.family_id)
        if kp_param is not None and kp_live:
            pairs.add((kp.position_item_id, kp_param))
    return pairs


def link_groups(
    kp: KpWork,
    kp_unit_id: int | None,
    history: WorkHistory,
    links: Sequence[LinkState],
    units: Mapping[int, str],
    values: Mapping[tuple[int, int], ParamText],
    *,
    tender_class_id: int,
    target_month: YearMonth | None,
) -> tuple[LinkGroup, ...]:
    """Блок Г: по группе на действующую связь семьи строки КП. Строки — кандидаты связанной семьи
    в зоне строки КП: класс тендера, иначе другой класс. Порядок — сначала строки с тем же `x`, что
    у строки КП (если её сторона жива и у неё число), затем порядок справки лестницы по цене в
    единице КП; строки без пересчёта — в конце, без пометки и вне набора выбросов."""
    if kp.zone is None:
        return ()
    groups: list[LinkGroup] = []
    for state in links:
        other = _other_side(state, kp.family_id)
        if other is None or not state.active:
            continue
        candidates = [
            c
            for c in history.pool(other).candidates
            if c.line.zone == kp.zone and c.line.position_item_id not in kp.excluded
        ]
        in_class = [c for c in candidates if c.contract.rate_class_id == tender_class_id]
        picked = in_class or candidates
        if not picked:
            continue
        kp_number = _number(state, kp.family_id, kp.position_item_id, values)
        built = []
        for cand in picked:
            line = cand.line
            conversion, reason, needed = conversion_for(
                contract_unit_id=line.unit_id,
                kp_unit_id=kp_unit_id,
                contract_family_id=other,
                kp_family_id=kp.family_id,
                links=[state],
                units=units,
                values=values,
                position_item_id=line.position_item_id,
            )
            if conversion is not None:
                kp_unit_net: Decimal | None = price_in_kp_unit(cand.net, conversion.factor)
            else:
                kp_unit_net = None if needed else cand.net
            param_id, _, _ = _side(state, other)
            text = None if param_id is None else values.get((line.position_item_id, param_id))
            number = _number(state, other, line.position_item_id, values)
            same_x = kp_number is not None and number is not None and number.x == kp_number.x
            built.append((cand, conversion, reason, kp_unit_net, None if text is None else text.text, same_x))
        ratios = outlier_ratios_of([(c.line.position_item_id, net) for c, _, _, net, _, _ in built if net is not None])

        def order(item) -> tuple:
            cand, _conversion, _reason, kp_unit_net, _text, same_x = item
            return (
                kp_unit_net is None,
                not same_x,
                -len(kp.chain_titles & cand.line.chain_titles),
                date_rank(cand.contract.period, target_month),
                cand.net if kp_unit_net is None else kp_unit_net,
                cand.line.position_item_id,
            )

        rows = tuple(
            RefRow(
                position_item_id=cand.line.position_item_id,
                contract_id=cand.contract.contract_id,
                zone=cand.line.zone,
                unit_id=cand.line.unit_id,
                nominal=cand.nominal,
                net=cand.net,
                conversion=conversion,
                no_conversion_reason=reason,
                kp_unit_net=kp_unit_net,
                outlier_ratio=ratios.get(cand.line.position_item_id),
                param_value=param_value,
                family_id=other,
            )
            for cand, conversion, reason, kp_unit_net, param_value, _same in sorted(built, key=order)
        )
        groups.append(
            LinkGroup(
                link_id=state.link.id,
                family_id=other,
                kind=typing.cast(LinkKind, state.link.kind),
                other_class=not in_class,
                rows=rows,
            )
        )
    return tuple(groups)


# ---------------------------------------------------------------------------
#  Пометки и свёртка
# ---------------------------------------------------------------------------


def reference_marks(
    ladder: WorkLadder | None, zones: Sequence[ZoneGroup], links: Sequence[LinkGroup]
) -> dict[int, Decimal | None]:
    """Строка договора -> отношение к медиане её набора, если она выброс; `None` — в наборе, но не
    выброс. Наборы: своя ступень (отношения даёт сама лестница), группа блока А, группа блока Г
    (только строки с ценой в единице КП). Строки вне наборов в словаре нет (спека §2.10)."""
    marks: dict[int, Decimal | None] = {}
    if ladder is not None:
        marks.update({analog.position_item_id: analog.outlier_ratio for analog in ladder.analogs})
    for group in zones:
        marks.update({row.position_item_id: row.outlier_ratio for row in group.rows})
    for linked in links:
        marks.update({row.position_item_id: row.outlier_ratio for row in linked.rows if row.kp_unit_net is not None})
    return marks


def fold_group_rows(
    rows: Sequence[RefRow], titles: Mapping[int, str], lump: Mapping[int, typing.Hashable] | None = None
) -> list[tuple[RefRow, tuple[int, ...]]]:
    """Свёртка строк группы (спека §2.11): одного договора, с равными наименованием, единицей,
    ценой, пересчётом (или причиной его отсутствия), зоной и пометкой. Порядок — первого появления.

    `lump` — строка -> решение «комплект или ставка» (поиск): строки с разным решением не
    сворачиваются, иначе метка и меню поправки скрытой строки достались бы представителю."""

    def key(row: RefRow) -> FoldKey:
        conversion = row.conversion
        return FoldKey(
            contract=row.contract_id,
            title=titles[row.position_item_id],
            unit_id=row.unit_id,
            price=row.net,
            factor=Decimal(1) if conversion is None else conversion.factor,
            basis=(row.no_conversion_reason or "") if conversion is None else conversion.basis,
            zone=row.zone,
            mark=row.outlier_ratio,
            lump=None if lump is None else lump.get(row.position_item_id),
        )

    return fold_rows(rows, key, lambda row: row.position_item_id)

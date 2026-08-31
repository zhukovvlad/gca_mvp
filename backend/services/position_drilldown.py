"""Попозиционное раскрытие статьи — чистый расчёт (спека
2026-08-30-position-drilldown-design.md §2.2–§2.6, §2.8).

Модуль чистый: ни Session, ни ORM — литералы на входе, литералы на выходе;
чтение БД и сборка JSON — в `crud/position_drilldown.py` (Task 4/5). Состояния,
виды изменения и ось измерения БЕРУТСЯ типами из `services/stage_summary.py`
(§2.5, §2.8), а не пересказываются заново — см. докстроку `cell_states` и
`change_between` там.

Task 2: типы модуля и `group_cells` — ячейки одной группы на всех колонках.
Task 3 (эта задача): отбор объяснителей, порядок строк, свёрнутые строки и
сходимость статьи — `compute_drilldown`.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from services import stage_summary as ss

KIND_POSITION = "position"
KIND_ADDITIONAL_WORKS = "additional_works"
KIND_UNMATCHED = "unmatched"
KIND_COLLAPSED = "collapsed_appeared_disappeared"
KIND_REST = "rest"
KIND_RANK = {KIND_POSITION: 0, KIND_ADDITIONAL_WORKS: 1, KIND_UNMATCHED: 2}
REASON_NO_ROWS_IN_SUBTREE = "no_rows_in_subtree"
COVERAGE = Decimal("0.9")
PARTIAL_CAP = 10


@dataclass(frozen=True)
class DrillColumn:
    offer_id: int
    estimate_id: int
    round_id: int
    stage_no: int
    label: str | None
    held_on: dt.date | None
    vat_rate_base: Decimal | None


@dataclass(frozen=True)
class GroupStage:
    """Строки группы на одном этапе. Ключа этапа нет в stages группы ⟺ строк
    нет вовсе (absent); gross == 0 при rows > 0 — «ноль», не отсутствие."""
    gross: Decimal
    rows: int
    quantities: tuple[Decimal, ...] = ()   # suggested_quantity, отсортированы
    unit: str | None = None


@dataclass(frozen=True)
class GroupInput:
    kind: str                        # KIND_POSITION | KIND_ADDITIONAL_WORKS | KIND_UNMATCHED
    catalog_position_id: int | None  # только у position
    chapter_ref_raw: str | None      # только у additional_works
    title: str
    stages: Mapping[int, GroupStage]  # индекс колонки -> данные
    #: Ключ, по которому строки собраны в эту группу (§2.2): у работ —
    #: каталожная позиция, у допработ — ПАРА «лот + ссылка» (§2.7), у
    #: непривязанных — константа. В контракт ответа НЕ выходит, живёт только
    #: внутри расчёта: им замыкается ключ порядка, иначе две допработы разных
    #: лотов с одной ссылкой и равным по модулю вкладом ничем не разводятся —
    #: `chapter_ref_raw` у них один и тот же (следствие ревизии ключа §2.7).
    key: tuple = ()


@dataclass(frozen=True)
class DrillCell:
    state: str
    shown: Decimal | None            # число только при state == amount и известной базе
    unavailable_reason: str | None   # unknown_vat_base | None
    quantities: tuple[Decimal, ...]
    quantity_unit: str | None
    quantity_changed: bool
    estimate_rows: int
    change: ss.Change


@dataclass(frozen=True)
class DrillContribution:
    value: Decimal | None            # None только у свёрнутых строк
    direction: str | None


@dataclass(frozen=True)
class DrillConvergence:
    stage_no: int
    article_amount: Decimal | None   # сумма поддерева в показанных величинах
    shown_sum: Decimal | None        # сумма показанных строк (money_at)
    converged: bool | None           # None при недоступной колонке
    reason: str | None               # unknown_vat_base | None


@dataclass(frozen=True)
class DrillRow:
    kind: str                        # пять видов, включая KIND_COLLAPSED / KIND_REST
    row_key: str                     # УСТОЙЧИВАЯ ИДЕНТИЧНОСТЬ строки в ответе
                                     # (§2.11): вид плюс ключ группировки. Клиент
                                     # берёт её ключом React как есть — свой ключ
                                     # из kind + chapter_ref_raw у двух лотов
                                     # совпал бы, и строки схлопнулись бы.
    catalog_position_id: int | None
    chapter_ref_raw: str | None
    lot_key: str | None              # только у additional_works: лот из ключа
                                     # группировки; в пилюлю попадает, лишь когда
                                     # в ответе больше одного лота (§2.7)
    title: str
    ambiguous: bool                  # «несколько строк сметы»: rows > 1 хоть на одном этапе
    group_count: int | None          # только у свёрнутых
    cells: list[DrillCell]
    bargain: ss.Change
    contribution: DrillContribution


@dataclass(frozen=True)
class DrilldownResult:
    rows: list[DrillRow]
    convergence: list[DrillConvergence]
    reason: str | None               # no_rows_in_subtree | unknown_vat_base | None
    display: ss.TaxBasis


def money_at(cell: DrillCell) -> Decimal:
    """Денежное значение ячейки для арифметики вклада и сходимости: 0 у всех
    состояний без суммы (§2.6 — вклад от нуля на концах; РАСХОЖДЕНИЕ с
    contribution_between фичи 3 сознательное и ограничено уровнем строк)."""
    return cell.shown if cell.state == ss.STATE_AMOUNT and cell.shown is not None else Decimal(0)


def group_cells(group: GroupInput, columns: Sequence[DrillColumn], basis: ss.TaxBasis) -> list[DrillCell]:
    gross = [group.stages[i].gross if i in group.stages else None for i in range(len(columns))]
    states = ss.cell_states(gross)
    cells: list[DrillCell] = []
    prev_quantities: tuple[Decimal, ...] | None = None   # предыдущий этап ПРИСУТСТВИЯ
    for idx, (column, state) in enumerate(zip(columns, states, strict=True)):
        stage = group.stages.get(idx)
        reason = ss.REASON_UNKNOWN_VAT_BASE if (column.vat_rate_base is None or basis.basis == ss.TAX_NONE) else None
        shown = None if reason else (ss.to_shown(gross[idx], column.vat_rate_base, basis)
                                     if state == ss.STATE_AMOUNT else None)
        quantities = stage.quantities if stage else ()
        # Сравнение МНОЖЕСТВАМИ, не кортежами: §2.4 определяет объём этапа как
        # МНОЖЕСТВО значений suggested_quantity, а не мультимножество; поставщик
        # (`load_groups`, Task 4) гарантирует отсортированные РАЗЛИЧНЫЕ значения
        # через `array_agg(DISTINCT ...)`, так что дублей сюда не попадает.
        changed = bool(quantities) and prev_quantities is not None and set(quantities) != set(prev_quantities)
        if stage is not None:
            prev_quantities = quantities
        prev = cells[idx - 1] if idx else None
        change = (ss.Change(ss.KIND_NONE, None, None, ss.REASON_FIRST_COLUMN) if idx == 0
                  else ss.change_between(prev.state, state, prev.shown, shown,
                                         unavailable_reason=reason or prev.unavailable_reason))
        cells.append(DrillCell(state, shown, reason, quantities, stage.unit if stage else None,
                               changed, stage.rows if stage else 0, change))
    return cells


def _order_tag(key: tuple) -> str:
    """Ключ группировки строкой — чтобы сравнение не упиралось в разные типы
    (`int` у работ против `str` у лота и ссылки)."""
    return "|".join(str(part) for part in key)


def _order_key(contribution: Decimal, g: GroupInput) -> tuple:
    """§2.3/§2.9: полный ключ — иначе порядок зависел бы от выдачи БД; id ЧИСЛОМ.

    Замыкается `g.key` — ключом группировки: тройки «модуль, вид, id/ссылка»
    перестало хватать, когда в ключ допработы вошёл лот (§2.7): две работы
    разных лотов с одной ссылкой и равным вкладом тройка не разводит вовсе.
    Сравниваются кортежи одного вида (см. `_order_tag`), а не разнотипные."""
    return (-abs(contribution), KIND_RANK.get(g.kind, 0), g.catalog_position_id or 0,
            g.chapter_ref_raw or "", _order_tag(g.key))


def row_key(kind: str, key: tuple) -> str:
    """Идентичность строки для клиента (§2.11): вид плюс ключ группировки.

    Собирается ЗДЕСЬ, а не на клиенте: клиентский ключ из `kind` и
    `chapter_ref_raw` совпал бы у двух допработ разных лотов, и React схлопнул
    бы две строки в одну (третий круг ревью плана 31.08.2026)."""
    return ":".join([kind, *(str(part) for part in key)])


def _row_from_group(g: GroupInput, cells: list[DrillCell]) -> DrillRow:
    first, last = cells[0], cells[-1]
    reason = first.unavailable_reason or last.unavailable_reason
    bargain = ss.change_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason)
    value = money_at(last) - money_at(first)
    return DrillRow(g.kind, row_key(g.kind, g.key), g.catalog_position_id, g.chapter_ref_raw,
                    lot_key=g.key[0] if g.kind == KIND_ADDITIONAL_WORKS else None,
                    title=g.title,
                    ambiguous=any(c.estimate_rows > 1 for c in cells), group_count=None,
                    cells=cells, bargain=bargain,
                    contribution=DrillContribution(value, ss.direction_of(value)))


def _collapsed_row(kind: str, members: list[DrillRow], amounts: list[Decimal],
                   columns: Sequence[DrillColumn], unavailable: list[str | None]) -> DrillRow:
    cells = [DrillCell(ss.STATE_AMOUNT, None if unavailable[i] else amounts[i], unavailable[i],
                       (), None, False, sum(m.cells[i].estimate_rows for m in members),
                       ss.Change(ss.KIND_NONE, None, None, None))
             for i in range(len(columns))]
    # У свёрнутых строк ключа группировки нет — их идентичность это сам вид:
    # обеих строк в ответе не больше одной каждой.
    return DrillRow(kind, kind, None, None, None, "", ambiguous=False,
                    group_count=len(members), cells=cells,
                    bargain=ss.Change(ss.KIND_NONE, None, None, None),
                    contribution=DrillContribution(None, None))


def compute_drilldown(columns: Sequence[DrillColumn], groups: Sequence[GroupInput]) -> DrilldownResult:
    basis = ss.pick_tax_basis([c.vat_rate_base for c in columns])
    if not groups:
        return DrilldownResult([], [], REASON_NO_ROWS_IN_SUBTREE, basis)
    ends_unknown = (basis.basis == ss.TAX_NONE
                    or columns[0].vat_rate_base is None or columns[-1].vat_rate_base is None)
    if ends_unknown:
        return DrilldownResult([], [], ss.REASON_UNKNOWN_VAT_BASE, basis)

    unavailable = [ss.REASON_UNKNOWN_VAT_BASE if c.vat_rate_base is None else None for c in columns]
    pairs = [(g, _row_from_group(g, group_cells(g, columns, basis))) for g in groups]
    # Разбиение на три класса — ПО ИНДЕКСАМ отсортированного списка, а не по
    # `id()` уже собранных строк: у свёрнутой строки свой объект, её участники в
    # `shown_rows` не попадают, и проверка «не показан» пропустила бы их в
    # «прочие» второй раз — лишняя строка с нулевым остатком при зелёной
    # сходимости (найдено ревью плана 31.08.2026). Каждая группа обязана попасть
    # РОВНО В ОДИН класс, и здесь это видно из построения: три среза одного
    # порядка, без пересечений.
    order = sorted(range(len(pairs)), key=lambda i: _order_key(pairs[i][1].contribution.value, pairs[i][0]))

    article_delta = sum((r.contribution.value for _, r in pairs), Decimal(0))
    floor = (1 - COVERAGE) * abs(article_delta)
    cumulative = Decimal(0)
    taken = 0
    while taken < len(order) and abs(article_delta - cumulative) > floor:
        cumulative += pairs[order[taken]][1].contribution.value
        taken += 1
    explainer_idx = order[:taken]
    tail_idx = order[taken:]

    partial_idx = [i for i in tail_idx if len(pairs[i][0].stages) < len(columns)]
    named_idx, folded_idx = partial_idx[:PARTIAL_CAP], partial_idx[PARTIAL_CAP:]
    rest_idx = [i for i in tail_idx if i not in set(partial_idx)]

    shown_rows = [pairs[i][1] for i in explainer_idx + named_idx]
    if folded_idx:
        folded_rows = [pairs[i][1] for i in folded_idx]
        amounts = [sum(money_at(r.cells[i]) for r in folded_rows) for i in range(len(columns))]
        shown_rows.append(_collapsed_row(KIND_COLLAPSED, folded_rows, amounts, columns, unavailable))

    rest_members = [pairs[i][1] for i in rest_idx]
    article_gross = [sum((g.stages[i].gross for g in groups if i in g.stages), Decimal(0))
                     for i in range(len(columns))]
    article_shown = [None if unavailable[i] else ss.to_shown(article_gross[i], columns[i].vat_rate_base, basis)
                     for i in range(len(columns))]
    if rest_members:
        remainder = [Decimal(0) if article_shown[i] is None
                     else article_shown[i] - sum(money_at(r.cells[i]) for r in shown_rows)
                     for i in range(len(columns))]
        shown_rows.append(_collapsed_row(KIND_REST, rest_members, remainder, columns, unavailable))

    convergence = []
    for i, column in enumerate(columns):
        if unavailable[i]:
            convergence.append(DrillConvergence(column.stage_no, None, None, None, unavailable[i]))
            continue
        shown_sum = sum(money_at(r.cells[i]) for r in shown_rows)
        convergence.append(DrillConvergence(column.stage_no, article_shown[i], shown_sum,
                                            article_shown[i] == shown_sum, None))
    return DrilldownResult(shown_rows, convergence, None, basis)

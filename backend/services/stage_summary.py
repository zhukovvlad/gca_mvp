"""Свод по этапам одного участника — чистый расчёт (спека
2026-08-27-stage-summary-design.md §2.5–§2.13, §2.16).

Модуль чистый: ни Session, ни ORM, ни импортов из `crud` — литералы на входе,
литералы на выходе; чтение БД и сборка JSON — `crud/stage_summary.py`.
Деление — только `percent_change`, и оно достижимо ТОЛЬКО при базе > 0 (§2.6):
`DivisionByZero` здесь — дефект расчёта, а не отказ пользователю.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from money.vat import gross_to_net
from parser.summary_block import ARITHMETIC_PRECISION
from services.category_rollup import (
    SOURCE_ADDITIONAL_WORKS,
    CategoryNode,
    CategoryRef,
    DirectTotals,
    build_tree,
)

STATE_AMOUNT = "amount"
STATE_REMOVED = "removed"
STATE_NOT_EVALUATED = "not_evaluated"
STATE_ABSENT = "absent"

KIND_PERCENT = "percent"
KIND_ABS_ONLY = "abs_only"
KIND_APPEARED = "appeared"
KIND_REAPPEARED = "reappeared"
KIND_REMOVED = "removed"
KIND_DISAPPEARED = "disappeared"
KIND_NONE = "none"

DIR_UP = "up"
DIR_DOWN = "down"
DIR_FLAT = "flat"

REASON_FIRST_COLUMN = "first_column"
REASON_UNKNOWN_VAT_BASE = "unknown_vat_base"
REASON_NO_AMOUNTS = "no_amounts"
REASON_UNALLOCATED = "unallocated"
REASON_ABSENT_ENDPOINT = "absent_endpoint"

_HUNDRED = Decimal(100)
#: Тот же приём, что `money.vat._VAT_CONTEXT` и `crud.comparison._DIV_CONTEXT`:
#: явные трапы БЕЗ Inexact. Приватный контекст соседа не импортируется.
_DIV_CONTEXT = Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])


@dataclass(frozen=True)
class CellInput:
    gross: Decimal | None
    additional_works_gross: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int


@dataclass(frozen=True)
class Change:
    kind: str
    value: Decimal | None
    direction: str | None
    reason: str | None


@dataclass(frozen=True)
class Contribution:
    value: Decimal | None
    direction: str | None
    reason: str | None


_NONE_CHANGE = Change(KIND_NONE, None, None, REASON_NO_AMOUNTS)


def cell_states(gross_by_column: Sequence[Decimal | None]) -> list[str]:
    """§2.5: `removed` — по ВСЕМ предыдущим колонкам пути, не по предыдущему шагу."""
    states: list[str] = []
    priced_before = False
    for gross in gross_by_column:
        if gross is None:
            states.append(STATE_ABSENT)
        elif gross != 0:
            states.append(STATE_AMOUNT)
            priced_before = True
        else:
            states.append(STATE_REMOVED if priced_before else STATE_NOT_EVALUATED)
    return states


def direction_of(delta: Decimal) -> str:
    """Направление — по величине, три состояния; равенство — `flat`, не рост."""
    if delta > 0:
        return DIR_UP
    if delta < 0:
        return DIR_DOWN
    return DIR_FLAT


def percent_change(start: Decimal, end: Decimal) -> Decimal:
    """§2.6: процент считается только при строго положительной базе; при
    `start <= 0` — `AssertionError`, деление здесь недостижимо, а не «безопасно
    обработано» (см. докстроку модуля)."""
    assert start > 0, "percent_change достижим только при положительной базе (§2.6)"
    with localcontext(_DIV_CONTEXT):
        return (end / start - 1) * _HUNDRED


def change_between(prev_state: str, cur_state: str, prev_shown: Decimal | None, cur_shown: Decimal | None,
                   *, unavailable_reason: str | None) -> Change:
    """§2.6: матрица переходов между состояниями клетки; несёт контракт
    достижимости деления — `percent_change` вызывается только из ветки
    `amount → amount` и только когда `prev_shown > 0`, иначе величина считается
    вычитанием (`abs_only`), без обращения к `percent_change`."""
    if unavailable_reason is not None:
        return Change(KIND_NONE, None, None, unavailable_reason)
    if prev_state == STATE_AMOUNT and cur_state == STATE_AMOUNT:
        assert prev_shown is not None and cur_shown is not None
        if prev_shown > 0:
            value = percent_change(prev_shown, cur_shown)
        else:
            value = cur_shown - prev_shown
            return Change(KIND_ABS_ONLY, value, direction_of(value), None)
        return Change(KIND_PERCENT, value, direction_of(cur_shown - prev_shown), None)
    if prev_state == STATE_AMOUNT and cur_state == STATE_REMOVED:
        return Change(KIND_REMOVED, None, None, None)
    if prev_state == STATE_AMOUNT and cur_state == STATE_ABSENT:
        return Change(KIND_DISAPPEARED, None, None, None)
    if cur_state == STATE_AMOUNT and prev_state == STATE_REMOVED:
        return Change(KIND_REAPPEARED, None, None, None)
    if cur_state == STATE_AMOUNT:  # prev ∈ {not_evaluated, absent}
        return Change(KIND_APPEARED, None, None, None)
    return _NONE_CHANGE


def contribution_between(first_state: str, last_state: str, first_shown: Decimal | None, last_shown: Decimal | None,
                         *, unavailable_reason: str | None) -> Contribution:
    """§2.7: `absent` не равен нулю — на любом конце даёт `null`; нулевые состояния — ноль денег."""
    if unavailable_reason is not None:
        return Contribution(None, None, unavailable_reason)
    if first_state == STATE_ABSENT or last_state == STATE_ABSENT:
        return Contribution(None, None, REASON_ABSENT_ENDPOINT)
    delta = (last_shown or Decimal(0)) - (first_shown or Decimal(0))
    return Contribution(delta, direction_of(delta), None)


TAX_GROSS = "gross"
TAX_NET = "net"
TAX_NONE = "none"
TAX_REASON_SINGLE = "single_rate"
TAX_REASON_MIXED = "mixed_rates"
TAX_REASON_NO_KNOWN = "no_known_rates"
VAT_KNOWN = "known"
VAT_UNKNOWN = "unknown_vat_base"
TRACK_NON_POSITIVE = "non_positive_total"
TRACK_NO_COMPARABLE = "no_comparable_totals"
CONV_FILE_TOTAL_UNAVAILABLE = "file_total_unavailable"


@dataclass(frozen=True)
class ColumnInput:
    offer_id: int
    estimate_id: int
    round_id: int
    stage_no: int
    label: str | None
    held_on: dt.date | None
    vat_rate_base: Decimal | None
    direct: Mapping[int | None, Mapping[str, DirectTotals]]
    file_total_gross: Decimal | None
    overrides_count: int
    overrides_last_at: dt.datetime | None


@dataclass(frozen=True)
class TaxBasis:
    basis: str
    reason: str
    rates_by_column: list[Decimal | None] | None


def pick_tax_basis(rates: Sequence[Decimal | None]) -> TaxBasis:
    """§2.8: три случая по множеству ИЗВЕСТНЫХ ставок среди выбранных колонок."""
    known = {r for r in rates if r is not None}
    if not known:
        return TaxBasis(TAX_NONE, TAX_REASON_NO_KNOWN, None)
    if len(known) == 1:
        return TaxBasis(TAX_GROSS, TAX_REASON_SINGLE, None)
    return TaxBasis(TAX_NET, TAX_REASON_MIXED, list(rates))


def to_shown(gross: Decimal | None, rate: Decimal | None, basis: TaxBasis) -> Decimal | None:
    """§2.8: `None` при неизвестной ставке колонки или при `basis = none` —
    сумма недоступна на любой оси, не только на подписанной валовой."""
    if gross is None or rate is None or basis.basis == TAX_NONE:
        return None
    return gross if basis.basis == TAX_GROSS else gross_to_net(gross, rate)


@dataclass(frozen=True)
class Cell:
    state: str
    shown: Decimal | None
    unavailable_reason: str | None
    additional_works_shown: Decimal | None
    rows: CellInput
    change: Change


@dataclass(frozen=True)
class TotalCell:
    """§2.16 (ревизия 28.08.2026, внешнее ревью PR #34): «Итого» — АГРЕГАТ, а не
    статья, и у него НЕТ состояния — ни поля, ни смысла: «снято» / «не
    оценивалась» / «нет в файле» неприменимы к сумме. Отсюда и дырка в наборе
    полей относительно `Cell` — нет `state`, нет `additional_works_shown` (§2.16
    прямо перечисляет, что у типа отсутствует). При известной оси `shown`
    ВСЕГДА число (включая ноль и пустую колонку без единой строки); `None`
    возможен единственно при `unavailable_reason = unknown_vat_base`."""
    shown: Decimal | None
    unavailable_reason: str | None
    rows: CellInput
    change: Change


@dataclass(frozen=True)
class SummaryRow:
    ref: CategoryRef | None
    is_unallocated: bool
    cells: list[Cell]
    bargain: Change
    contribution: Contribution
    children: list[SummaryRow]


@dataclass(frozen=True)
class Convergence:
    categories_sum_gross: Decimal
    file_total_gross: Decimal | None
    converged: bool | None
    delta: Decimal | None
    reason: str | None


@dataclass(frozen=True)
class ColumnOut:
    input: ColumnInput
    vat_state: str
    total_shown: Decimal | None
    total_change: Change
    bar_height_pct: Decimal | None
    convergence: Convergence


@dataclass(frozen=True)
class Kpi:
    stages_selected: int
    categories_with_amount: int
    categories_total: int
    first_to_last: Change


@dataclass(frozen=True)
class Track:
    available: bool
    reason: str | None


@dataclass(frozen=True)
class SummaryResult:
    columns: list[ColumnOut]
    rows: list[SummaryRow]
    unallocated: SummaryRow
    total_cells: list[TotalCell]
    display: TaxBasis
    kpi: Kpi
    track: Track


def _extra_subtree(node: CategoryNode, direct: Mapping[int | None, Mapping[str, DirectTotals]]) -> Decimal | None:
    """Допработы статьи = свои + всех потомков (как `node.total` у `build_tree`):
    ветвь у ребёнка обязана быть видна и в родителе (§2.4). None — ветви нет нигде в поддереве."""
    own = direct.get(node.ref.id, {}).get(SOURCE_ADDITIONAL_WORKS)
    parts = [own.amount] if own is not None and own.amount is not None else []
    parts.extend(v for v in (_extra_subtree(c, direct) for c in node.children) if v is not None)
    return sum(parts) if parts else None


def _gross_or_zero(amount: Decimal | None, row_count: int) -> Decimal | None:
    """RESOLUTION B: `CellInput.gross is None` ⟺ `row_count == 0` (контракт Task 1,
    инвариант `DirectTotals` в `category_rollup.py`). Строк нет вовсе — `None`
    («не в файле»); строки есть, но ни одна не оценена — `Decimal(0)` («не
    оценивалась»), а не `None`: `build_tree.total is None` слабее (он молчит про
    `rows == 0`, только про `rows_priced == 0`), поэтому здесь решение принимается
    заново, а не переносится из `node.total`/суммы прямых сумм как есть."""
    if row_count == 0:
        return None
    return amount if amount is not None else Decimal(0)


def _node_inputs(node: CategoryNode | None, direct_key: int | None,
                 direct: Mapping[int | None, Mapping[str, DirectTotals]]) -> CellInput:
    """Валовые входы одной пары (колонка, статья). Для статьи — из свёрнутого узла
    `build_tree` (поддерево целиком); для Нераспределённого — из `direct[None]`."""
    if node is not None:
        return CellInput(gross=_gross_or_zero(node.total, node.rows),
                         additional_works_gross=_extra_subtree(node, direct),
                         row_count=node.rows, rows_with_amount=node.rows_priced, rows_not_finite=node.rows_not_finite)
    branches = direct.get(direct_key, {})
    row_count = sum(b.row_count for b in branches.values())
    amounts = [b.amount for b in branches.values() if b.amount is not None]
    extra = branches.get(SOURCE_ADDITIONAL_WORKS)
    return CellInput(gross=_gross_or_zero(sum(amounts) if amounts else None, row_count),
                     additional_works_gross=None if extra is None else extra.amount,
                     row_count=row_count,
                     rows_with_amount=sum(b.rows_with_amount for b in branches.values()),
                     rows_not_finite=sum(b.rows_not_finite for b in branches.values()))


def _change_step(idx: int, *, state: str, shown: Decimal | None, reason: str | None,
                 prev_state: str | None, prev_shown: Decimal | None, prev_reason: str | None) -> Change:
    """Один шаг цепочки «изменение к предыдущей выбранной колонке» — общий для
    построчных ячеек (`_cells`) и для пересчитанной строки «Итого»
    (Resolution C): первая колонка всегда `none`/`first_column`, дальше —
    `change_between` со своей причиной недоступности или причиной предыдущей
    ячейки. Вынесен, чтобы не держать эту цепочку в двух местах раздельно."""
    if idx == 0:
        return Change(KIND_NONE, None, None, REASON_FIRST_COLUMN)
    return change_between(prev_state, state, prev_shown, shown, unavailable_reason=reason or prev_reason)


def _cells(inputs: list[CellInput], columns: Sequence[ColumnInput], basis: TaxBasis) -> list[Cell]:
    states = cell_states([i.gross for i in inputs])
    cells: list[Cell] = []
    for idx, (inp, column, state) in enumerate(zip(inputs, columns, states, strict=True)):
        reason = REASON_UNKNOWN_VAT_BASE if (column.vat_rate_base is None or basis.basis == TAX_NONE) else None
        shown = None if reason else (to_shown(inp.gross, column.vat_rate_base, basis) if state == STATE_AMOUNT else None)
        extra = None if reason else to_shown(inp.additional_works_gross, column.vat_rate_base, basis)
        prev = cells[idx - 1] if idx > 0 else None
        change = _change_step(idx, state=state, shown=shown, reason=reason,
                              prev_state=prev.state if prev else None,
                              prev_shown=prev.shown if prev else None,
                              prev_reason=prev.unavailable_reason if prev else None)
        cells.append(Cell(state, shown, reason, extra, inp, change))
    return cells


def _endpoints(cells: list[Cell]) -> tuple[Change, Contribution]:
    first, last = cells[0], cells[-1]
    reason = first.unavailable_reason or last.unavailable_reason
    return (change_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason),
            contribution_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason))


def _numeric_change(start: Decimal | None, end: Decimal | None, *, reason: str | None) -> Change:
    """§2.16 (ревизия 28.08.2026): изменение ИТОГА — числом, не по матрице
    состояний §2.6: у `TotalCell` состояния нет вовсе, а сумма нулей — это ноль,
    не «неизвестно». Недоступный конец с любой стороны — `none` с причиной;
    положительная предыдущая величина — процент (та же `percent_change`, что и
    у построчных ячеек); ноль или отрицательная — абсолютная дельта. Общая
    функция для шага «к предыдущей выбранной» (`_total_change_step`) и для
    торга «первый → последний» (`_total_endpoint_change`, `kpi.first_to_last`) —
    одно правило, а не два его пересказа."""
    if reason is not None:
        return Change(KIND_NONE, None, None, reason)
    assert start is not None and end is not None
    delta = end - start
    if start > 0:
        return Change(KIND_PERCENT, percent_change(start, end), direction_of(delta), None)
    return Change(KIND_ABS_ONLY, delta, direction_of(delta), None)


def _total_change_step(idx: int, *, amount: Decimal | None, reason: str | None,
                       prev_amount: Decimal | None, prev_reason: str | None) -> Change:
    """Шаг цепочки «Итого» к предыдущей выбранной колонке — числовое правило
    `_numeric_change`, не `change_between`. Первая колонка — как у построчных
    ячеек, `none`/`first_column`; текущая причина недоступности приоритетнее
    причины предыдущей колонки (тот же порядок, что у `_change_step` построчных
    ячеек)."""
    if idx == 0:
        return Change(KIND_NONE, None, None, REASON_FIRST_COLUMN)
    return _numeric_change(prev_amount, amount, reason=reason or prev_reason)


def _total_endpoint_change(cells: Sequence[TotalCell]) -> Change:
    """`kpi.first_to_last` — то же числовое правило между КРАЙНИМИ выбранными
    колонками (как `bargain` у строки статьи через `_endpoints`), но без
    состояний: первая причина недоступности приоритетнее последней — тот же
    порядок, что у `_endpoints` построчных ячеек."""
    first, last = cells[0], cells[-1]
    reason = first.unavailable_reason or last.unavailable_reason
    return _numeric_change(first.shown, last.shown, reason=reason)


def sort_key(contribution: Contribution, sort_order: int) -> tuple:
    """§2.13: по убыванию |вклада|, `None` — после числовых, оба — вторично по `sort_order`."""
    if contribution.value is None:
        return (1, Decimal(0), sort_order)
    return (0, -abs(contribution.value), sort_order)


def _row(ref: CategoryRef, nodes_by_column: list[CategoryNode | None],
        columns: Sequence[ColumnInput], basis: TaxBasis) -> SummaryRow | None:
    inputs = [_node_inputs(node, ref.id, col.direct) for node, col in zip(nodes_by_column, columns, strict=True)]
    cells = _cells(inputs, columns, basis)
    child_refs: dict[int, CategoryRef] = {}
    child_nodes: dict[int, list[CategoryNode | None]] = {}
    for idx, node in enumerate(nodes_by_column):
        for child in (node.children if node else ()):
            child_refs[child.ref.id] = child.ref
            child_nodes.setdefault(child.ref.id, [None] * len(columns))[idx] = child
    children = [r for cid, cref in child_refs.items()
                if (r := _row(cref, child_nodes[cid], columns, basis)) is not None]
    bargain, contribution = _endpoints(cells)
    row = SummaryRow(ref, False, cells, bargain, contribution,
                     sorted(children, key=lambda r: sort_key(r.contribution, r.ref.sort_order)))
    if ref.parent_id is not None and all(c.rows.gross in (None, Decimal(0)) for c in cells):
        return None          # §2.14: вложенный узел виден только при ненулевой сумме хоть в одной колонке
    return row


def compute_summary(columns: Sequence[ColumnInput], categories: Sequence[CategoryRef]) -> SummaryResult:
    basis = pick_tax_basis([c.vat_rate_base for c in columns])
    trees = [build_tree(categories, c.direct) for c in columns]
    roots_by_id = {node.ref.id: [t[i] if i < len(t) else None for t in trees] for i, node in enumerate(trees[0])}
    # build_tree отдаёт корни в одном порядке для всех колонок (общий справочник) — индекс i общий.
    rows = [r for rid, nodes in roots_by_id.items() if (r := _row(nodes[0].ref, nodes, columns, basis)) is not None]
    rows.sort(key=lambda r: sort_key(r.contribution, r.ref.sort_order))

    unalloc_inputs = [_node_inputs(None, None, c.direct) for c in columns]
    unalloc_cells = _cells(unalloc_inputs, columns, basis)
    _, unalloc_contribution = _endpoints(unalloc_cells)
    unallocated = SummaryRow(None, True, unalloc_cells, Change(KIND_NONE, None, None, REASON_UNALLOCATED),
                             unalloc_contribution, [])

    totals_shown: list[Decimal | None] = []
    totals_gross: list[Decimal] = []
    total_row_counts: list[int] = []
    total_rows_with_amount: list[int] = []
    total_rows_not_finite: list[int] = []
    for idx, column in enumerate(columns):
        gross_parts = [r.cells[idx].rows.gross for r in rows] + [unalloc_cells[idx].rows.gross]
        totals_gross.append(sum(p for p in gross_parts if p is not None) or Decimal(0))
        # Счётчики строк итога — НАСТОЯЩИЕ, а не нули: сумма по корням плюс
        # «Нераспределённое», как у `totals_gross` выше. Дважды не считаются:
        # `rows.row_count` корня уже включает ВСЁ поддерево целиком
        # (`build_tree._build_node`, `rows = own + extra + Σ children.rows`),
        # поэтому дети сюда не заходят — иначе строка посчиталась бы дважды,
        # один раз в ребёнке и один раз в родителе.
        row_inputs = [r.cells[idx].rows for r in rows] + [unalloc_cells[idx].rows]
        total_row_counts.append(sum(ri.row_count for ri in row_inputs))
        total_rows_with_amount.append(sum(ri.rows_with_amount for ri in row_inputs))
        total_rows_not_finite.append(sum(ri.rows_not_finite for ri in row_inputs))
        if column.vat_rate_base is None or basis.basis == TAX_NONE:
            totals_shown.append(None)
        else:
            # §2.16 (ревизия 28.08.2026): сумма при известной оси — ВСЕГДА число,
            # включая ноль и колонку без единой строки вовсе (`sum([]) or
            # Decimal(0)` покрывает оба: и «все ячейки сократились до нуля», и
            # «ячеек, несущих сумму, не было вовсе» — раньше вырожденный
            # `total_row_counts[idx] == 0` уходил в `None` отдельной веткой,
            # чего у «Итого»-агрегата, в отличие от статьи, быть не должно.
            shown_parts = [r.cells[idx].shown for r in rows] + [unalloc_cells[idx].shown]
            totals_shown.append(sum(p for p in shown_parts if p is not None) or Decimal(0))

    # RESOLUTION C (сохранена): shown «Итого» — сумма ПОКАЗАННЫХ строк
    # (`totals_shown`), а не отдельная свёртка валового итога колонки — тем
    # самым `columns[].total_change`, `total.cells[].change` и
    # `kpi.first_to_last` читают ОДИН ряд чисел, а не «change по валовому ряду +
    # shown по показанному». У `TotalCell` состояния нет — change считается
    # ЧИСЛЕННО (`_total_change_step`/`_numeric_change`), не через матрицу
    # состояний §2.6: сумма нулей — ноль, а не «неизвестно» (§2.16, ревизия
    # 28.08.2026 по внешнему ревью PR #34 — прежняя редакция типизировала итог
    # как `Cell` и на нулевом итоге с живыми строками публиковала состояние
    # «снято» вместе с суммой «0.00»).
    total_cells: list[TotalCell] = []
    for idx in range(len(columns)):
        shown = totals_shown[idx]
        reason = REASON_UNKNOWN_VAT_BASE if shown is None else None
        rows_counter = CellInput(None, None, total_row_counts[idx], total_rows_with_amount[idx],
                                 total_rows_not_finite[idx])
        prev = total_cells[idx - 1] if idx > 0 else None
        change = _total_change_step(idx, amount=shown, reason=reason,
                                    prev_amount=prev.shown if prev else None,
                                    prev_reason=prev.unavailable_reason if prev else None)
        total_cells.append(TotalCell(shown, reason, rows_counter, change))

    comparable = [t for t in totals_shown if t is not None]
    if not comparable:
        track = Track(False, TRACK_NO_COMPARABLE)
    elif any(t <= 0 for t in comparable):
        track = Track(False, TRACK_NON_POSITIVE)
    else:
        track = Track(True, None)
    max_total = max(comparable) if track.available else None

    columns_out: list[ColumnOut] = []
    for idx, column in enumerate(columns):
        file_total = column.file_total_gross
        if file_total is None:
            conv = Convergence(totals_gross[idx], None, None, None, CONV_FILE_TOTAL_UNAVAILABLE)
        else:
            delta = totals_gross[idx] - file_total
            conv = Convergence(totals_gross[idx], file_total, delta == 0, delta, None)
        shown = totals_shown[idx]
        with localcontext(_DIV_CONTEXT):
            bar = None if (shown is None or max_total is None) else shown / max_total * _HUNDRED
        columns_out.append(ColumnOut(column, VAT_UNKNOWN if column.vat_rate_base is None else VAT_KNOWN,
                                     shown, total_cells[idx].change, bar, conv))

    last_idx = len(columns) - 1
    kpi = Kpi(stages_selected=len(columns),
              categories_with_amount=sum(1 for r in rows if r.cells[last_idx].rows.gross not in (None, Decimal(0))),
              categories_total=len(rows),
              first_to_last=_total_endpoint_change(total_cells))
    return SummaryResult(columns_out, rows, unallocated, total_cells, basis, kpi, track)

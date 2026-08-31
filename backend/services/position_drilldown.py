"""Попозиционное раскрытие статьи — чистый расчёт (спека
2026-08-30-position-drilldown-design.md §2.2–§2.6, §2.8).

Модуль чистый: ни Session, ни ORM — литералы на входе, литералы на выходе;
чтение БД и сборка JSON — в `crud/position_drilldown.py` (Task 4/5). Состояния,
виды изменения и ось измерения БЕРУТСЯ типами из `services/stage_summary.py`
(§2.5, §2.8), а не пересказываются заново — см. докстроку `cell_states` и
`change_between` там.

Эта задача (Task 2): типы модуля и `group_cells` — ячейки одной группы на всех
колонках. Отбор объяснителей, порядок строк, свёрнутые строки и сходимость
статьи — `compute_drilldown`, Task 3, здесь НЕ реализуется.
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

# Попозиционное раскрытие статьи — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кнопка «Работы» на строке статьи свода разворачивает под ней строки
работ, объясняющие изменение статьи, — теми же колонками этапов, со сходимостью
к числу строки свода.

**Architecture:** Третий уровень ВНУТРИ существующей таблицы свода (ни страницы,
ни модалки). Новый ленивый эндпоинт
`GET /api/v1/tenders/{tender_id}/stage-summary/{work_category_id}` — чистый
расчёт в `services/position_drilldown.py` (переиспользует словарь состояний и
ось НДС фичи 3 типами), чтение в `crud/position_drilldown.py` постоянным числом
запросов. Единственная правка контракта фичи 3 — булево `has_drilldown_rows` на
строках свода. Разложение раскрывает ПОДДЕРЕВО статьи; ключ работы — каталожная
позиция БЕЗ статьи; допработы построчно по `chapter_ref_raw`.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (маркер `integration`); React +
TanStack Query + shadcn/ui + vitest.

**Spec:** `docs/superpowers/specs/2026-08-30-position-drilldown-design.md`
(гейт 2 закрыт 31.08.2026, восемь кругов ревью). Числа спеки — из блока замеров
секции `#inline` макета `2026-08-29-position-drilldown-mockup.html`.

## Global Constraints

- **Миграции нет, новых полей в БД нет** (спека §4). Правка контракта фичи 3 —
  ровно одна: `has_drilldown_rows` в ответе свода (§2.12).
- **Словарь состояний и переходов — ТИПАМИ из фичи 3**: `cell_states`,
  `change_between`, `pick_tax_basis`, `to_shown`, `percent_change`,
  `direction_of` из `backend/services/stage_summary.py`; на фронте —
  `STATE_LABEL`, `KIND_LABEL` из `cellCopy.ts`. Новых значений и словарей
  состояний не заводить, существующие не переименовывать (§2.5, §6.3).
- **Число запросов к БД не зависит от числа выбранных колонок** (§2.11) —
  проверяется счётчиком, как в тестах фичи 3.
- **Деньги** — `quantize_money` + `decimal_json`; проценты — один знак
  (`Decimal("0.1")`, ROUND_HALF_UP), как в своде.
- **Фронт** — только shadcn/ui и токены приложения (не имена макета), обе темы
  (§2.12). Печатного слоя нет.
- **Правило конечности** сумм — как у VIEW `v_category_totals`: суммируются
  только строки, где `total_cost_total` не `NaN` и не `±Infinity` (§1.7).
- Бэкенд: `uv run ruff check` (не format), тесты — `uv run pytest`, маркер
  `integration` для БД-тестов. Фронт: `npm run lint` по ВСЕМУ фронтенду
  (не `eslint <путь>`), `npm test`. Перед пушем — `just ci` (в конвейер не
  заворачивать, код возврата читать отдельно).
- Кириллица в выводе дочернего python — `PYTHONIOENCODING=utf-8`.
- Константы разложения: `COVERAGE = Decimal("0.9")`, `PARTIAL_CAP = 10` (§2.3).

---

### Task 1: `has_drilldown_rows` в ответе свода

Единственная правка контракта фичи 3 (§2.12, §4): строка свода сообщает, есть ли
в ПОДДЕРЕВЕ статьи строки разложения хотя бы в одной выбранной колонке — по
ОБЕИМ ветвям (позиции И допработы), §2.1. `SummaryRow.cells[*].rows.row_count`
уже несёт число строк всего поддерева по обеим ветвям
(`build_tree._build_node`: `rows = own + extra + Σ children.rows` — см.
комментарий в `compute_summary` около `total_row_counts`), поэтому вычисление —
одна строка в сериализаторе.

**Files:**
- Modify: `backend/crud/stage_summary.py` (функция `_row`, ~строка 202)
- Test: `backend/tests/integration/test_stage_summary_api.py`

**Interfaces:**
- Consumes: `ss.SummaryRow` (есть), `CellInput.row_count` (есть).
- Produces: поле `has_drilldown_rows: bool` в каждом объекте `rows[*]` (и
  рекурсивно в `children`, и в `unallocated` — правило одно на сериализатор).
  Task 10 (фронт) читает его из `StageSummaryRow`.

- [ ] **Step 1: Написать падающие тесты**

В `backend/tests/integration/test_stage_summary_api.py`, в класс с тестами формы
ответа (рядом с `test_both_view_branches_and_additional_works_amount`):

```python
    def test_has_drilldown_rows_true_for_article_with_rows_and_false_without(self, db_session, grid):
        data = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        by_code = {r["code"]: r for r in data["rows"]}
        assert by_code["6"]["has_drilldown_rows"] is True          # позиции есть
        assert by_code["2"]["has_drilldown_rows"] is True          # строки есть, суммы 60 -> 0
        empty = next(r for r in data["rows"] if all(c["rows"]["row_count"] == 0 for c in r["cells"]))
        assert empty["has_drilldown_rows"] is False

    def test_has_drilldown_rows_counts_both_branches_and_the_subtree(self, db_session, factories):
        """Негативные проверки §6.2/§6.3 на уровне свода: статья с ОДНИМИ
        допработами и узел БЕЗ собственных строк, но со строками потомка,
        оба получают True — проверка одних position_items или одних прямых
        строк статьи обязана здесь краснеть."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        # Раздел 1 -> статья 6.1 (потомок 6, СОБСТВЕННЫХ строк у 6 нет);
        # раздел 2 -> статья 2, работ ноль, только допработа по ссылке «2».
        def payload(amount_61: str, extra: str):
            positions = [
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                         article_smr="6.1", number="1"),
                position(job_title="Работа фасада", unit="м2", quantity=1, suggested_quantity=1,
                         unit_cost_total=amount_61, total_cost_total=amount_61,
                         chapter_ref="1", number="2"),
                position(job_title="Раздел 2", is_chapter=True, chapter_number="2",
                         article_smr="2", number="3"),
            ]
            summary = {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", "144.00"),
                       JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "0"),
                       JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", "144.00")}
            return proposal(positions, vat_rate="20", summary=summary,
                            additional_works=additional_works_row(total=extra),
                            additional_info=svedeniya_info(f"2 Допработы участка - {extra} руб."))
        for n, rnd in rounds.items():
            import_round(db_session, tender_round=rnd,
                         data=round_payload([payload("120.00", "24.00")]), parser_version="4.0.0",
                         import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                         category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)
        ).scalars().all()
        data = crud_ss.build_stage_summary(db_session, tender.id, offers)
        by_code = {r["code"]: r for r in data["rows"]}
        assert by_code["6"]["has_drilldown_rows"] is True   # поддерево: строки только у 6.1
        assert by_code["2"]["has_drilldown_rows"] is True   # только допработы
```

Если резолвер не знает кода «6.1», взять из справочника любой существующий код
потомка со своим родителем: `db_session.execute(sa.select(WorkCategory.code)
.where(WorkCategory.parent_id.isnot(None))).first()` — и подставить его и код
его родителя; тест не должен зависеть от конкретной пары.

- [ ] **Step 2: Прогнать тесты, убедиться в падении**

Из `backend/`: `uv run pytest tests/integration/test_stage_summary_api.py -k has_drilldown -x -q`
Ожидание: `KeyError: 'has_drilldown_rows'`.

- [ ] **Step 3: Реализация**

В `backend/crud/stage_summary.py`, в `_row`, добавить поле:

```python
def _row(r: ss.SummaryRow) -> dict:
    return {"work_category_id": None if r.ref is None else r.ref.id,
            "code": None if r.ref is None else r.ref.code,
            "title": "Нераспределённое" if r.ref is None else r.ref.title,
            "is_unallocated": r.is_unallocated,
            # §2.12 спеки фичи 4: есть ли в ПОДДЕРЕВЕ строки разложения хотя бы
            # в одной выбранной колонке. row_count узла уже включает всё
            # поддерево по ОБЕИМ ветвям (позиции + допработы) — см. комментарий
            # у total_row_counts в services/stage_summary.compute_summary.
            "has_drilldown_rows": any(c.rows.row_count > 0 for c in r.cells),
            "cells": [_cell(c) for c in r.cells],
            ...
```

- [ ] **Step 4: Прогнать тесты**

`uv run pytest tests/integration/test_stage_summary_api.py -q` — все зелёные
(старые тесты формы ответа поле не перечисляют исчерпывающе — проверить, что
ни один не упал).

- [ ] **Step 5: Commit**

```bash
git add backend/crud/stage_summary.py backend/tests/integration/test_stage_summary_api.py
git commit -m "feat(position-drilldown): has_drilldown_rows в ответе свода — единственная правка контракта фичи 3"
```

---

### Task 2: Чистый расчёт — группы и ячейки (`services/position_drilldown.py`)

Чистый модуль без Session и ORM, литералы на входе и выходе — как
`services/stage_summary.py`. Эта задача: типы, ячейки группы (состояния,
изменения, объёмы), «Торг» и вклад. Отбор объяснителей — Task 3.

**Files:**
- Create: `backend/services/position_drilldown.py`
- Test: `backend/tests/unit/test_position_drilldown.py`

**Interfaces:**
- Consumes (из `services/stage_summary.py`): `cell_states`, `change_between`,
  `pick_tax_basis`, `to_shown`, `direction_of`, `Change`, `TaxBasis`, константы
  `STATE_*`, `KIND_*`, `REASON_FIRST_COLUMN`, `REASON_UNKNOWN_VAT_BASE`.
- Produces (для Task 3, 4, 5):

```python
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

@dataclass(frozen=True)
class DrillCell:
    state: str
    shown: Decimal | None            # число только при state == amount и известной базе
    unavailable_reason: str | None   # unknown_vat_base | None
    quantities: tuple[Decimal, ...]
    quantity_unit: str | None
    quantity_changed: bool
    estimate_rows: int
    change: Change

@dataclass(frozen=True)
class DrillContribution:
    value: Decimal | None            # None только у свёрнутых строк
    direction: str | None

@dataclass(frozen=True)
class DrillRow:
    kind: str                        # пять видов, включая KIND_COLLAPSED / KIND_REST
    catalog_position_id: int | None
    chapter_ref_raw: str | None
    title: str
    ambiguous: bool                  # «несколько строк сметы»: rows > 1 хоть на одном этапе
    group_count: int | None          # только у свёрнутых
    cells: list[DrillCell]
    bargain: Change
    contribution: DrillContribution

def money_at(cell: DrillCell) -> Decimal:
    """Денежное значение ячейки для арифметики вклада и сходимости: 0 у всех
    состояний без суммы (§2.6 — вклад от нуля на концах; РАСХОЖДЕНИЕ с
    contribution_between фичи 3 сознательное и ограничено уровнем строк)."""

def group_cells(group: GroupInput, columns: Sequence[DrillColumn], basis: TaxBasis) -> list[DrillCell]
```

- [ ] **Step 1: Написать падающие тесты**

`backend/tests/unit/test_position_drilldown.py`:

```python
"""Чистый расчёт разложения статьи (спека 2026-08-30-position-drilldown-design.md
§2.2–§2.6, §2.9, §2.13). Без БД: литералы на входе и выходе."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from services import position_drilldown as pd
from services import stage_summary as ss

D = Decimal


def col(idx: int, rate: str | None = "20") -> pd.DrillColumn:
    return pd.DrillColumn(offer_id=idx, estimate_id=100 + idx, round_id=10 + idx, stage_no=idx + 1,
                          label=None, held_on=dt.date(2026, 3, 1 + idx),
                          vat_rate_base=None if rate is None else D(rate))


COLS4 = [col(0), col(1), col(2), col(3)]
BASIS = ss.pick_tax_basis([c.vat_rate_base for c in COLS4])


def grp(kind=pd.KIND_POSITION, cpid=1, ref=None, title="Работа", **stages) -> pd.GroupInput:
    """stages: s0=..., s1=... — GroupStage по индексу колонки."""
    return pd.GroupInput(kind=kind, catalog_position_id=cpid if kind == pd.KIND_POSITION else None,
                         chapter_ref_raw=ref, title=title,
                         stages={int(k[1:]): v for k, v in stages.items()})


class TestGroupCells:
    def test_absent_iff_zero_estimate_rows(self):
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.state for c in cells] == ["amount", "absent", "absent", "amount"]
        assert [(c.estimate_rows == 0) == (c.state == "absent") for c in cells] == [True] * 4

    def test_hole_in_the_middle_is_disappeared_then_appeared_not_removed(self):
        """Дыра §2.5: средние этапы absent, переходы disappeared/appeared,
        «снято» не появляется ни разу."""
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert cells[1].change.kind == "disappeared"
        assert cells[3].change.kind == "appeared"
        assert all(c.change.kind != "removed" and c.state != "removed" for c in cells)

    def test_zero_after_amount_is_removed_and_zero_before_is_not_evaluated(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s1=pd.GroupStage(D("50"), 1),
                s2=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("0"), 1))
        states = [c.state for c in pd.group_cells(g, COLS4, BASIS)]
        assert states == ["not_evaluated", "amount", "removed", "removed"]

    def test_appeared_is_impossible_on_first_column(self):
        g = grp(s0=pd.GroupStage(D("100"), 1))
        first = pd.group_cells(g, COLS4, BASIS)[0]
        assert first.change.kind == "none" and first.change.reason == "first_column"

    def test_quantity_changed_compares_with_previous_present_stage(self):
        """Как mark_volume_steps генератора: absent-этап пропускается, «предыдущий»
        — предыдущий этап ПРИСУТСТВИЯ."""
        g = grp(s0=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s2=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s3=pd.GroupStage(D("10"), 2, (D("6"), D("11")), "шт"))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.quantity_changed for c in cells] == [False, False, False, True]

    def test_unknown_middle_column_carries_reason_and_no_amount(self):
        cols = [col(0), col(1, rate=None), col(2), col(3)]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("100"), 1), s1=pd.GroupStage(D("90"), 1),
                s2=pd.GroupStage(D("80"), 1), s3=pd.GroupStage(D("70"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[1].shown is None and cells[1].unavailable_reason == "unknown_vat_base"
        assert cells[0].shown == D("100") and cells[2].unavailable_reason is None

    def test_net_axis_recomputes_shown_per_column_rate(self):
        cols = [col(0, "20"), col(1, "22")]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("120"), 1), s1=pd.GroupStage(D("122"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[0].shown == D("100") and cells[1].shown == D("100")

    def test_money_at_is_zero_for_absent_and_zero_states(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert pd.money_at(cells[0]) == D("0")   # not_evaluated
        assert pd.money_at(cells[1]) == D("0")   # absent
        assert pd.money_at(cells[3]) == D("90")
```

- [ ] **Step 2: Прогнать, убедиться в падении**

`uv run pytest tests/unit/test_position_drilldown.py -x -q` — ModuleNotFoundError.

- [ ] **Step 3: Реализация**

`backend/services/position_drilldown.py` — датаклассы из блока Interfaces выше и:

```python
def money_at(cell: DrillCell) -> Decimal:
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
```

- [ ] **Step 4: Прогнать тесты** — `uv run pytest tests/unit/test_position_drilldown.py -q`, PASS;
  `uv run ruff check services/position_drilldown.py tests/unit/test_position_drilldown.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/position_drilldown.py backend/tests/unit/test_position_drilldown.py
git commit -m "feat(position-drilldown): чистый расчёт — ячейки группы, словарь фичи 3 типами"
```

---

### Task 3: Чистый расчёт — отбор, порядок, свёрнутые, сходимость

**Files:**
- Modify: `backend/services/position_drilldown.py`
- Test: `backend/tests/unit/test_position_drilldown.py`

**Interfaces:**
- Produces (для Task 5):

```python
@dataclass(frozen=True)
class DrillConvergence:
    stage_no: int
    article_amount: Decimal | None   # сумма поддерева в показанных величинах
    shown_sum: Decimal | None        # сумма показанных строк (money_at)
    converged: bool | None           # None при недоступной колонке
    reason: str | None               # unknown_vat_base | None

@dataclass(frozen=True)
class DrilldownResult:
    rows: list[DrillRow]
    convergence: list[DrillConvergence]
    reason: str | None               # no_rows_in_subtree | unknown_vat_base | None
    display: ss.TaxBasis

def compute_drilldown(columns: Sequence[DrillColumn], groups: Sequence[GroupInput]) -> DrilldownResult
```

Правила (§2.3, §2.6, §2.9, §2.13, §2.8):
- ось: `ss.pick_tax_basis` по ставкам колонок — тот же выбор, что у свода;
- `groups` пуст → `reason = no_rows_in_subtree`, `rows = []`, `convergence = []`;
- база неизвестна на ПЕРВОЙ или ПОСЛЕДНЕЙ колонке (или ось `none`) →
  `reason = unknown_vat_base`, `rows = []`, `convergence = []`; неизвестная
  СРЕДНЯЯ колонка разложение не запрещает (её ячейки несут причину, её
  convergence — `converged: null` с причиной);
- вклад группы = `money_at(последняя) − money_at(первая)`;
- `article_delta` = Σ вкладов всех групп (= движение суммы поддерева);
- полный ключ порядка: `(-abs(contribution), KIND_RANK.get(kind, 0),
  catalog_position_id or 0, chapter_ref_raw or "")` — id сравнивается ЧИСЛОМ;
- объяснители: в порядке ключа, добавлять, ПОКА
  `abs(article_delta − cumulative) > (1 − COVERAGE) * abs(article_delta)`;
  при `article_delta == 0` объяснителей нет вовсе;
- класс 2: из оставшихся — группы, которых нет хотя бы на одном выбранном этапе
  (`len(stages) < len(columns)`), в порядке ключа; первые `PARTIAL_CAP`
  поимённо, остальные — одна строка `KIND_COLLAPSED`
  (`title = "ещё N работ появились или исчезли"` НЕ формируется здесь — title
  свёрнутых строк собирает фронт; здесь `title = ""`, `group_count = N`,
  ячейки — суммы `money_at` групп мешка, `estimate_rows` — сумма строк);
- класс 3: все оставшиеся группы — одна строка `KIND_REST`: `group_count`,
  ячейка колонки = ОСТАТОК `article_shown[i] − Σ money_at(показанных)` (включая
  мешок класса 2), а не собственная свёртка — сходимость держится конструкцией
  (§2.13); строки `KIND_REST` нет, если остаточных групп ноль;
- у свёрнутых строк (`KIND_COLLAPSED`, `KIND_REST`): ячейки
  `state = "amount"`, `change = Change("none", None, None, None)`,
  `quantities = ()`, `bargain = Change("none", None, None, None)`,
  `contribution = DrillContribution(None, None)` — процентов и «Торга» нет
  (§2.3);
- порядок строк: объяснители, затем поимённые класса 2, затем мешок, затем
  `rest` (§2.9: свёрнутые последними);
- convergence по колонкам: обычная — `article_amount = to_shown(Σ gross всех
  групп)`, `shown_sum = Σ money_at ячеек показанных строк`, `converged =
  (article_amount == shown_sum)` (расхождение допускается нулевое, §2.13),
  `reason = None`; недоступная колонка — все значения `None`, `converged =
  None`, `reason = unknown_vat_base`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `backend/tests/unit/test_position_drilldown.py`:

```python
def one_stage_grp(idx_amounts: dict[int, str], *, kind=pd.KIND_POSITION, cpid=1, ref=None,
                  title="Работа", rows=1) -> pd.GroupInput:
    return pd.GroupInput(kind=kind, catalog_position_id=cpid if kind == pd.KIND_POSITION else None,
                         chapter_ref_raw=ref, title=title,
                         stages={i: pd.GroupStage(D(a), rows) for i, a in idx_amounts.items()})


COLS2 = [col(0), col(3)]
BASIS2 = ss.pick_tax_basis([c.vat_rate_base for c in COLS2])


class TestComputeDrilldown:
    def test_explainers_stop_when_cumulative_covers_90_percent(self):
        groups = [one_stage_grp({0: "100", 1: "10"}, cpid=1),      # вклад -90
                  one_stage_grp({0: "50", 1: "45"}, cpid=2),       # вклад -5
                  one_stage_grp({0: "30", 1: "29"}, cpid=3)]       # вклад -1
        out = pd.compute_drilldown(COLS2, groups)
        # delta = -96; после первой группы |−96 − (−90)| = 6 <= 9.6 — хватает одной
        assert out.rows[0].catalog_position_id == 1
        assert out.rows[-1].kind == pd.KIND_REST and out.rows[-1].group_count == 2

    def test_zero_delta_takes_no_explainers_but_shows_other_classes(self):
        groups = [one_stage_grp({0: "50", 1: "50"}, cpid=1),
                  one_stage_grp({1: "20"}, cpid=2),                 # появилась: класс 2
                  one_stage_grp({0: "20"}, cpid=3)]                 # исчезла: класс 2
        out = pd.compute_drilldown(COLS2, groups)
        kinds = [(r.kind, r.catalog_position_id) for r in out.rows]
        assert (pd.KIND_POSITION, 1) not in kinds                   # объяснителей нет
        assert {(k, i) for k, i in kinds if k == pd.KIND_POSITION} == {
            (pd.KIND_POSITION, 2), (pd.KIND_POSITION, 3)}
        assert out.rows[-1].kind == pd.KIND_REST

    def test_full_order_key_breaks_ties_by_kind_then_numeric_id_then_ref(self):
        groups = [one_stage_grp({0: "0", 1: "10"}, cpid=10),
                  one_stage_grp({0: "0", 1: "10"}, cpid=2),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.2"),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.10")]
        out = pd.compute_drilldown(COLS2, groups)
        head = [(r.kind, r.catalog_position_id, r.chapter_ref_raw) for r in out.rows[:4]]
        # равный |вклад| 10: позиции раньше допработ; id числом (2 < 10); ссылки строкой
        assert head == [("position", 2, None), ("position", 10, None),
                        ("additional_works", None, "3.2.10"), ("additional_works", None, "3.2.2")]

    def test_partial_cap_folds_the_tail_into_collapsed_row(self):
        # big объясняет 112 из delta=124 один: |124−112| = 12 <= 12.4 — отбор
        # останавливается, и все 12 появившихся уходят во второй класс.
        big = one_stage_grp({0: "100", 1: "212"}, cpid=99)
        born = [one_stage_grp({1: "1"}, cpid=i) for i in range(1, 13)]   # 12 появившихся
        out = pd.compute_drilldown(COLS2, [big] + born)
        collapsed = [r for r in out.rows if r.kind == pd.KIND_COLLAPSED]
        assert len(collapsed) == 1 and collapsed[0].group_count == 2       # 12 - PARTIAL_CAP
        assert collapsed[0].bargain.kind == "none" and collapsed[0].contribution.value is None
        named_born = [r for r in out.rows
                      if r.kind == pd.KIND_POSITION and r.catalog_position_id != 99
                      and r.contribution.value == D("1")]
        assert len(named_born) == pd.PARTIAL_CAP                            # десять поимённо

    def test_rest_carries_the_remainder_and_convergence_is_exact(self):
        groups = [one_stage_grp({0: "100", 1: "10"}, cpid=1),
                  one_stage_grp({0: "33.33", 1: "33.33"}, cpid=2),
                  one_stage_grp({0: "66.67", 1: "66.67"}, cpid=3)]
        out = pd.compute_drilldown(COLS2, groups)
        rest = out.rows[-1]
        assert rest.kind == pd.KIND_REST
        for conv, idx in zip(out.convergence, range(2), strict=True):
            shown = sum(pd.money_at(r.cells[idx]) for r in out.rows)
            assert conv.converged is True and conv.shown_sum == conv.article_amount == shown

    def test_empty_groups_is_no_rows_in_subtree(self):
        out = pd.compute_drilldown(COLS2, [])
        assert out.reason == pd.REASON_NO_ROWS_IN_SUBTREE and out.rows == [] and out.convergence == []

    def test_unknown_endpoint_refuses_with_unknown_vat_base(self):
        cols = [col(0, rate=None), col(1)]
        out = pd.compute_drilldown(cols, [one_stage_grp({0: "10", 1: "20"})])
        assert out.reason == "unknown_vat_base" and out.rows == []

    def test_unknown_middle_column_builds_and_marks_only_that_column(self):
        cols = [col(0), col(1, rate=None), col(2)]
        g = pd.GroupInput(kind=pd.KIND_POSITION, catalog_position_id=1, chapter_ref_raw=None,
                          title="Работа", stages={0: pd.GroupStage(D("10"), 1),
                                                  1: pd.GroupStage(D("20"), 1),
                                                  2: pd.GroupStage(D("30"), 1)})
        out = pd.compute_drilldown(cols, [g])
        assert out.reason is None
        assert out.convergence[1].converged is None and out.convergence[1].reason == "unknown_vat_base"
        assert out.convergence[0].converged is True and out.convergence[2].converged is True

    def test_wholly_appeared_article_still_gets_a_drilldown(self):
        """Негативная §6.2: статья, целиком появившаяся (absent_endpoint у свода),
        разложение ПОЛУЧАЕТ — предикат не цепляется за contribution фичи 3."""
        out = pd.compute_drilldown(COLS2, [one_stage_grp({1: "100"}, cpid=1)])
        assert out.reason is None and len(out.rows) >= 1
        assert out.rows[0].bargain.kind == "appeared"
        assert out.rows[0].contribution.value == D("100")

    def test_bargain_of_appeared_row_is_a_pill_not_a_percent(self):
        out = pd.compute_drilldown(COLS2, [one_stage_grp({1: "100"}, cpid=1),
                                           one_stage_grp({0: "1", 1: "1"}, cpid=2)])
        row = next(r for r in out.rows if r.catalog_position_id == 1)
        assert row.bargain.kind == "appeared" and row.bargain.value is None

    def test_ambiguous_flag_from_any_stage_with_more_than_one_row(self):
        g = one_stage_grp({0: "10", 1: "20"}, cpid=1, rows=2)
        out = pd.compute_drilldown(COLS2, [g])
        assert out.rows[0].ambiguous is True
```

- [ ] **Step 2: Прогнать, убедиться в падении** — `AttributeError: compute_drilldown`.

- [ ] **Step 3: Реализация**

```python
def _order_key(contribution: Decimal, g: GroupInput) -> tuple:
    """§2.3/§2.9: полный ключ — иначе порядок зависел бы от выдачи БД; id ЧИСЛОМ."""
    return (-abs(contribution), KIND_RANK.get(g.kind, 0), g.catalog_position_id or 0, g.chapter_ref_raw or "")


def _row_from_group(g: GroupInput, cells: list[DrillCell]) -> DrillRow:
    first, last = cells[0], cells[-1]
    reason = first.unavailable_reason or last.unavailable_reason
    bargain = ss.change_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason)
    value = money_at(last) - money_at(first)
    return DrillRow(g.kind, g.catalog_position_id, g.chapter_ref_raw, g.title,
                    ambiguous=any(c.estimate_rows > 1 for c in cells), group_count=None,
                    cells=cells, bargain=bargain,
                    contribution=DrillContribution(value, ss.direction_of(value)))


def _collapsed_row(kind: str, members: list[DrillRow], amounts: list[Decimal],
                   columns: Sequence[DrillColumn], unavailable: list[str | None]) -> DrillRow:
    cells = [DrillCell(ss.STATE_AMOUNT, None if unavailable[i] else amounts[i], unavailable[i],
                       (), None, False, sum(m.cells[i].estimate_rows for m in members),
                       ss.Change(ss.KIND_NONE, None, None, None))
             for i in range(len(columns))]
    return DrillRow(kind, None, None, "", ambiguous=False, group_count=len(members),
                    cells=cells, bargain=ss.Change(ss.KIND_NONE, None, None, None),
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
    rows_by_group = [(g, _row_from_group(g, group_cells(g, columns, basis))) for g in groups]
    rows_by_group.sort(key=lambda gr: _order_key(gr[1].contribution.value, gr[0]))

    article_delta = sum((r.contribution.value for _, r in rows_by_group), Decimal(0))
    floor = (1 - COVERAGE) * abs(article_delta)
    explainers: list[DrillRow] = []
    cumulative = Decimal(0)
    queue = list(rows_by_group)
    while queue and abs(article_delta - cumulative) > floor:
        g, row = queue.pop(0)
        explainers.append(row)
        cumulative += row.contribution.value

    partial = [(g, r) for g, r in queue if len(g.stages) < len(columns)]
    named, folded = partial[:PARTIAL_CAP], partial[PARTIAL_CAP:]
    shown_rows = explainers + [r for _, r in named]
    if folded:
        folded_rows = [r for _, r in folded]
        amounts = [sum(money_at(r.cells[i]) for r in folded_rows) for i in range(len(columns))]
        shown_rows.append(_collapsed_row(KIND_COLLAPSED, folded_rows, amounts, columns, unavailable))

    shown_keys = {id(r) for r in shown_rows}
    rest_members = [r for _, r in rows_by_group if id(r) not in shown_keys]
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
```

Замечания к реализации: `money_at` у `KIND_COLLAPSED`/`KIND_REST` читает
`shown` при `state == amount` — свёрнутые входят в `shown_sum` своей суммой,
поэтому сходимость точная по построению; `folded`-мешок кладётся ДО расчёта
`rest`-остатка, чтобы остаток его учитывал.

- [ ] **Step 4: Прогнать** — `uv run pytest tests/unit/test_position_drilldown.py -q` PASS;
  `uv run ruff check services/position_drilldown.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/position_drilldown.py backend/tests/unit/test_position_drilldown.py
git commit -m "feat(position-drilldown): отбор объяснителей, полный ключ порядка, свёрнутые строки, сходимость"
```

---

### Task 4: Чтение входов — поддерево, группы, допработы (`crud/position_drilldown.py`)

Чтение постоянным числом запросов; группировка БЕЗ статьи (переезд
родитель → потомок группу не рвёт); допработы по ссылке с подписью с последнего
этапа. Колонка — СМЕТА: у сметы бывает несколько лотов/предложений, поэтому все
запросы идут через `Lot.estimate_id`, а не по одному `proposal_id`.

**Files:**
- Create: `backend/crud/position_drilldown.py`
- Test: `backend/tests/integration/test_position_drilldown_api.py`

**Interfaces:**
- Consumes: `pd.GroupInput`, `pd.GroupStage`, константы `pd.KIND_*`;
  модели `PositionItem`, `CatalogPosition`, `UnitOfMeasure`,
  `EstimateAdditionalWork`, `WorkCategory`, `Proposal`, `Lot`.
- Produces (для Task 5):

```python
UNMATCHED_TITLE = "Строки без каталожной привязки"

def subtree_ids(db: Session, work_category_id: int) -> list[int]
    # id статьи и всех потомков; ОДИН запрос всех категорий, обход в Python

def load_groups(db: Session, estimate_ids: Sequence[int], subtree: Sequence[int]) -> list[pd.GroupInput]
    # ДВА запроса (позиции, допработы), не зависящих от числа смет
```

`load_groups`:
- позиции: `SELECT lot.estimate_id, pi.catalog_position_id,
  min(cp.standard_job_title), sum(конечных total_cost_total), count(*),
  array_agg(distinct pi.suggested_quantity), min(u.symbol)` c join строки-раздела
  (`chapter = aliased(PositionItem)`, `chapter.id == pi.chapter_item_id AND
  chapter.proposal_id == pi.proposal_id`), `WHERE lot.estimate_id IN :estimates
  AND NOT pi.is_chapter AND chapter.work_category_id IN :subtree`,
  `GROUP BY lot.estimate_id, pi.catalog_position_id`. Конечность — предикат
  VIEW: `CASE WHEN total_cost_total IN ('NaN','Infinity','-Infinity') THEN NULL
  ELSE total_cost_total END` под `sum` (сравнение с `'NaN'::numeric` в Postgres
  корректно); NULL-суммы `sum` игнорирует сам.
- ключ группы: `catalog_position_id` (число) — статья в ключ НЕ входит;
  `catalog_position_id IS NULL` → одна группа `KIND_UNMATCHED` на поддерево с
  `title = UNMATCHED_TITLE`;
- допработы: `SELECT lot.estimate_id, aw.chapter_ref_raw,
  (array_agg(aw.title ORDER BY aw.ordinal))[1], sum(aw.total_amount), count(*)`
  через `Proposal.lot_id`, `WHERE lot.estimate_id IN :estimates AND
  aw.work_category_id IN :subtree`, `GROUP BY lot.estimate_id,
  aw.chapter_ref_raw` (SQLAlchemy: `sa.func.array_agg(
  sa.dialects.postgresql.aggregate_order_by(EstimateAdditionalWork.title,
  EstimateAdditionalWork.ordinal))[1]`);
- ключ группы допработ: `chapter_ref_raw`; подпись — наименование с ПОСЛЕДНЕГО
  этапа присутствия (колонки собираются по возрастанию `stage_no`, присваивание
  перетирает прежнее — как `load_groups` генератора), внутри этапа — первая по
  `ordinal` (уже в SQL);
- `quantities` — отсортированный tuple ненулевых… точнее: НЕ-None значений
  `suggested_quantity`; `unit` — `min(symbol)`.

- [ ] **Step 1: Написать падающие тесты**

`backend/tests/integration/test_position_drilldown_api.py`:

```python
"""Разложение статьи: чтение входов, сборка групп, HTTP-контракт (спека
2026-08-30-position-drilldown-design.md §2.1, §2.2, §2.7, §2.11).

Сметы строятся НАСТОЯЩИМ import_round — как в test_stage_summary_api.py."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import position_drilldown as crud_pd
from crud import stage_summary as crud_ss
from crud.common import DomainError
from models import Estimate, Offer, PositionItem, TenderRound, WorkCategory
from services import position_drilldown as pd_service
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import (
    additional_works_row,
    position,
    proposal,
    round_payload,
    svedeniya_info,
)
from tests.integration.test_stage_summary_api import chaptered, count_queries, grid  # noqa: F401

pytestmark = pytest.mark.integration

D = Decimal


def estimates_of(db, offer_ids):
    return db.execute(
        sa.select(Estimate.id).join(Offer, Offer.id == Estimate.offer_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Estimate.offer_id.in_(offer_ids)).order_by(TenderRound.stage_no)
    ).scalars().all()


def category_id(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


class TestLoadGroups:
    def test_same_catalog_position_in_parent_and_child_is_one_group(self, db_session, factories):
        """Негативная §6.1: ключ группы — каталожная позиция БЕЗ статьи. Одна и
        та же работа (то же наименование и единица => та же каталожная позиция,
        matching get-or-create) на этапе 1 лежит в статье-родителе, на этапе 2 —
        в статье-потомке; групп в поддереве родителя обязана быть ОДНА, без
        исчезновения. Ключ со статьёй здесь краснеет двумя группами."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        parent_code, child_code = "6", "6.1"
        for n, art in ((1, parent_code), (2, child_code)):
            positions = [position(job_title="Раздел", is_chapter=True, chapter_number="1",
                                  article_smr=art, number="1"),
                         position(job_title="Фасад корпуса", unit="м2", quantity=1, suggested_quantity=5,
                                  unit_cost_total="100.00", total_cost_total="100.00",
                                  chapter_ref="1", number="2")]
            import_round(db_session, tender_round=rounds[n],
                         data=round_payload([proposal(positions, vat_rate="20")]),
                         parser_version="4.0.0", import_job_id=None, replace=False,
                         unit_resolver=UnitResolver(db_session),
                         category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        estimates = estimates_of(db_session, offers)
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, parent_code))
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        works = [g for g in groups if g.kind == pd_service.KIND_POSITION]
        assert len(works) == 1
        assert set(works[0].stages) == {0, 1}          # оба этапа, исчезновения нет

    def test_extras_are_rows_by_ref_and_title_comes_from_the_last_stage(self, db_session, factories):
        """§2.7: две ссылки с одним наименованием — ДВЕ группы (ключ по
        наименованию краснеет); подпись группы — с последнего этапа присутствия."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        info = {
            1: svedeniya_info("1.1 Монолитные конструкции - 10.00 руб.",
                              "1.2 Монолитные конструкции - 20.00 руб."),
            2: svedeniya_info("1.1 Монолитные конструкции, уточнено - 12.00 руб.",
                              "1.2 Монолитные конструкции - 21.00 руб."),
        }
        for n, rnd in rounds.items():
            positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                                  article_smr="6", number="1"),
                         position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total="5.00", total_cost_total="5.00",
                                  chapter_ref="1", number="2")]
            import_round(db_session, tender_round=rnd,
                         data=round_payload([proposal(positions, vat_rate="20",
                                                      additional_works=additional_works_row(total="30.00"),
                                                      additional_info=info[n])]),
                         parser_version="4.0.0", import_job_id=None, replace=False,
                         unit_resolver=UnitResolver(db_session),
                         category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        groups = crud_pd.load_groups(db_session, estimates_of(db_session, offers), subtree)
        extras = sorted((g for g in groups if g.kind == pd_service.KIND_ADDITIONAL_WORKS),
                        key=lambda g: g.chapter_ref_raw)
        assert [g.chapter_ref_raw for g in extras] == ["1.1", "1.2"]
        assert extras[0].title == "Монолитные конструкции, уточнено"   # последний этап
        assert set(extras[0].stages) == {0, 1}

    def test_unmatched_rows_fold_into_one_group_per_subtree(self, db_session, grid):
        """Фикстурная ветка §5: привязку снимаем руками (на живой базе её
        снимает только недоработанный матчинг, 0 из 64 505)."""
        db_session.execute(sa.update(PositionItem).where(PositionItem.is_chapter.is_(False))
                           .values(catalog_position_id=None))
        db_session.flush()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        estimates = estimates_of(db_session, grid.path)
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        unmatched = [g for g in groups if g.kind == pd_service.KIND_UNMATCHED]
        assert len(unmatched) == 1 and unmatched[0].title == crud_pd.UNMATCHED_TITLE

    def test_query_count_does_not_grow_with_columns(self, db_session, grid):
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        two = estimates_of(db_session, grid.path[:2])
        three = estimates_of(db_session, grid.path)
        with count_queries(db_session) as c2:
            crud_pd.load_groups(db_session, two, subtree)
        with count_queries(db_session) as c3:
            crud_pd.load_groups(db_session, three, subtree)
        assert c2["n"] == c3["n"]
```

Примечание для исполнителя: `chaptered` из тестов свода строит `summary`-блок
сам — если хелперу выше нужен итог, скопировать словарь `summary` из
`chaptered`, а не изобретать свой. Если `proposal(...)` требует непустой
`summary`, передать тот же блок из трёх строк, что в `chaptered`.

- [ ] **Step 2: Прогнать, убедиться в падении** —
`uv run pytest tests/integration/test_position_drilldown_api.py -x -q` → ModuleNotFoundError.

- [ ] **Step 3: Реализация** `backend/crud/position_drilldown.py`:

```python
"""Разложение статьи: чтение входов постоянным числом запросов (спека
2026-08-30-position-drilldown-design.md §2.1, §2.2, §2.7, §2.11).

Арифметика — services/position_drilldown.py; здесь запросы и сборка GroupInput.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, aliased

from models import (
    CatalogPosition,
    EstimateAdditionalWork,
    Lot,
    PositionItem,
    Proposal,
    UnitOfMeasure,
    WorkCategory,
)
from services import position_drilldown as pd

UNMATCHED_TITLE = "Строки без каталожной привязки"
_NOT_FINITE = (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"))


def subtree_ids(db: Session, work_category_id: int) -> list[int]:
    """Статья и все потомки. Один запрос всего справочника: категорий сотни,
    и обход в Python дешевле рекурсивного SQL (тот же приём — subtree_ids
    генератора макета)."""
    children: dict[int | None, list[int]] = defaultdict(list)
    for cid, parent in db.execute(sa.select(WorkCategory.id, WorkCategory.parent_id)).all():
        children[parent].append(cid)
    out, stack = [], [work_category_id]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children[current])
    return out


def load_groups(db: Session, estimate_ids: Sequence[int], subtree: Sequence[int]) -> list[pd.GroupInput]:
    column_of = {e: i for i, e in enumerate(estimate_ids)}
    chapter = aliased(PositionItem)
    finite = sa.case((PositionItem.total_cost_total.in_(_NOT_FINITE), None),
                     else_=PositionItem.total_cost_total)
    position_rows = db.execute(
        sa.select(Lot.estimate_id, PositionItem.catalog_position_id,
                  sa.func.min(CatalogPosition.standard_job_title),
                  sa.func.sum(finite), sa.func.count(),
                  sa.func.array_agg(sa.distinct(PositionItem.suggested_quantity)),
                  sa.func.min(UnitOfMeasure.symbol))
        .select_from(PositionItem)
        .join(chapter, sa.and_(chapter.id == PositionItem.chapter_item_id,
                               chapter.proposal_id == PositionItem.proposal_id))
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)), PositionItem.is_chapter.is_(False),
               chapter.work_category_id.in_(list(subtree)))
        .group_by(Lot.estimate_id, PositionItem.catalog_position_id)
    ).all()

    groups: dict[object, dict] = {}
    for estimate_id, cat_id, title, amount, rows, quantities, unit in position_rows:
        key = pd.KIND_UNMATCHED if cat_id is None else cat_id
        group = groups.setdefault(key, {
            "kind": pd.KIND_UNMATCHED if cat_id is None else pd.KIND_POSITION,
            "catalog_position_id": cat_id, "chapter_ref_raw": None,
            "title": UNMATCHED_TITLE if cat_id is None else title, "stages": {}})
        group["stages"][column_of[estimate_id]] = pd.GroupStage(
            gross=Decimal(amount or 0), rows=rows,
            quantities=tuple(sorted(q for q in quantities if q is not None)), unit=unit)

    extra_rows = db.execute(
        sa.select(Lot.estimate_id, EstimateAdditionalWork.chapter_ref_raw,
                  sa.func.array_agg(aggregate_order_by(EstimateAdditionalWork.title,
                                                       EstimateAdditionalWork.ordinal))[1],
                  sa.func.sum(EstimateAdditionalWork.total_amount), sa.func.count())
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)),
               EstimateAdditionalWork.work_category_id.in_(list(subtree)))
        .group_by(Lot.estimate_id, EstimateAdditionalWork.chapter_ref_raw)
        .order_by(Lot.estimate_id, EstimateAdditionalWork.chapter_ref_raw)
    ).all()
    ordered = sorted(extra_rows, key=lambda r: column_of[r[0]])
    for estimate_id, ref, title, amount, rows in ordered:
        key = ("extra", ref)
        group = groups.setdefault(key, {"kind": pd.KIND_ADDITIONAL_WORKS,
                                        "catalog_position_id": None, "chapter_ref_raw": ref,
                                        "title": title, "stages": {}})
        group["title"] = title    # подпись с ПОСЛЕДНЕГО этапа присутствия (§2.7)
        group["stages"][column_of[estimate_id]] = pd.GroupStage(gross=Decimal(amount or 0), rows=rows)

    return [pd.GroupInput(kind=g["kind"], catalog_position_id=g["catalog_position_id"],
                          chapter_ref_raw=g["chapter_ref_raw"], title=g["title"], stages=g["stages"])
            for g in groups.values()]
```

- [ ] **Step 4: Прогнать** — тесты Task 4 PASS; `uv run ruff check crud/position_drilldown.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/crud/position_drilldown.py backend/tests/integration/test_position_drilldown_api.py
git commit -m "feat(position-drilldown): чтение входов — поддерево, группы без статьи, допработы по ссылке"
```

---

### Task 5: Сериализация, эндпоинт, коды отказов

**Files:**
- Modify: `backend/crud/stage_summary.py` (сериализаторы `_money`/`_pct`/`_change`
  → публичные `money_str`/`pct_str`/`change_json`)
- Modify: `backend/crud/position_drilldown.py` (`build_position_drilldown` + сериализация)
- Modify: `backend/routers/tenders.py` (новый маршрут)
- Test: `backend/tests/integration/test_position_drilldown_api.py`

**Interfaces:**
- Produces:

```python
# crud/stage_summary.py — переименование приватных сериализаторов в публичные
# (правило сериализации change/денег/процентов ОДНО на свод и разложение, §2.5):
def money_str(v: Decimal | None) -> str | None      # бывший _money
def pct_str(v: Decimal | None) -> str | None        # бывший _pct
def change_json(c: ss.Change) -> dict               # бывший _change

# crud/position_drilldown.py:
CODE_WC_NOT_FOUND = "work_category_not_found"
def build_position_drilldown(db: Session, tender_id: int, work_category_id: int,
                             offer_ids: Sequence[int]) -> dict
```

- Маршрут: `GET /api/v1/tenders/{tender_id}/stage-summary/{work_category_id}?offers=…`
  — тот же паттерн, что `stage_summary` в `backend/routers/tenders.py:77`
  (включая обработку `DomainError`, как она там устроена — скопировать
  обёртку соседнего маршрута дословно).
- Форма ответа — §2.11 спеки ДОСЛОВНО (см. пример в спеке):
  `work_category {id, code, title}`, `columns[] {offer_id, estimate_id,
  round_id, stage_no, label, held_on}`, `display {tax_basis, reason}`,
  `rows[]`, `convergence[] {stage_no, article_amount, shown_sum, converged,
  reason}`, `reason`.
- Ячейка строки: `{state, amount, amount_unavailable_reason, quantity,
  quantity_unit, quantity_changed, estimate_rows, change}`. **`quantity` —
  строка**: значения `suggested_quantity` группы этапа, сырые (как в базе,
  §2.11 — пример показывает `"8726.397168"`), при нескольких — через `+`
  (`"6+11"`); `null`, если объёма нет (допработы, unmatched, свёрнутые,
  absent). Форматирование разрядов и одного знака после запятой (§2.4) — на
  клиенте, Task 8.
- Строка: `{kind, catalog_position_id, chapter_ref_raw, title, ambiguous,
  group_count, cells, bargain, contribution {value, direction, reason: null}}`.
- Проверки в `build_position_drilldown`, по порядку: тендер (`db.get(Tender)`,
  404 `tender_not_found` — «Свой код, а не get_tender», как у свода);
  `work_category_id` (`db.get(WorkCategory)`, 404 `work_category_not_found`);
  `crud_ss.validate_selection` (те же пять кодов, §2.11);
  `crud_ss.load_inputs` → `DrillColumn` (offer_id/estimate_id/round_id/
  stage_no/label/held_on/vat_rate_base из `ss.ColumnInput` — ставки
  считаются ТЕМ ЖЕ кодом, что у свода); `subtree_ids` → `load_groups` →
  `pd.compute_drilldown` → сериализация.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `backend/tests/integration/test_position_drilldown_api.py`:

```python
class TestEndpointContract:
    def _drill(self, db, grid, code="6", offers=None):
        return crud_pd.build_position_drilldown(
            db, grid.tender.id, category_id(db, code), offers or grid.path)

    def test_response_shape_and_cell_row_invariant(self, db_session, grid):
        data = self._drill(db_session, grid)
        assert data["work_category"]["code"] == "6" and data["reason"] is None
        assert [c["stage_no"] for c in data["columns"]] == [1, 2, 4]
        assert data["display"]["tax_basis"] == "gross" and data["display"]["reason"] == "single_rate"
        for row in data["rows"]:
            assert len(row["cells"]) == len(data["columns"])
            for cell in row["cells"]:
                assert (cell["estimate_rows"] == 0) == (cell["state"] == "absent")

    def test_convergence_matches_the_summary_row_number(self, db_session, grid):
        """§2.13: article_amount == числу строки свода, shown_sum == article_amount."""
        summary = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        summary_row = next(r for r in summary["rows"] if r["code"] == "6")
        data = self._drill(db_session, grid)
        for idx, conv in enumerate(data["convergence"]):
            assert conv["converged"] is True
            expected = summary_row["cells"][idx]["amount"] or "0.00"
            assert conv["article_amount"] == expected == conv["shown_sum"]

    def test_extras_money_is_inside_the_rows_or_totals_do_not_converge(self, db_session, grid):
        """§1.5: сумма статьи в своде = позиции ПЛЮС допработы; в grid у статьи 6
        на этапе 2 допработы 24.00 — без их строки сходимость упала бы."""
        data = self._drill(db_session, grid)
        kinds = {r["kind"] for r in data["rows"]}
        assert "additional_works" in kinds
        assert all(c["converged"] is True for c in data["convergence"])

    def test_unknown_work_category_is_404(self, db_session, grid):
        with pytest.raises(DomainError) as e:
            crud_pd.build_position_drilldown(db_session, grid.tender.id, 10**9, grid.path)
        assert e.value.status == 404 and e.value.code == crud_pd.CODE_WC_NOT_FOUND

    def test_selection_refusals_are_the_summary_codes(self, db_session, grid):
        with pytest.raises(DomainError) as e:
            self._drill(db_session, grid, offers=[grid.path[0]])
        assert e.value.code == "too_few_offers"

    def test_empty_subtree_is_no_rows_in_subtree_not_an_error(self, db_session, grid):
        empty_code = db_session.execute(
            sa.select(WorkCategory.code)
            .where(~WorkCategory.id.in_(sa.select(PositionItem.work_category_id)
                                        .where(PositionItem.work_category_id.isnot(None))),
                   WorkCategory.parent_id.is_(None))
        ).scalars().first()
        data = self._drill(db_session, grid, code=empty_code)
        assert data["reason"] == "no_rows_in_subtree" and data["rows"] == []

    def test_article_with_only_descendant_rows_gets_a_drilldown(self, db_session, factories):
        """Негативная §6.2: узел без собственных строк, но со строками потомка,
        no_rows_in_subtree НЕ получает и отдаёт непустое разложение (живой
        случай стенда: 18 и 22 таких узла по трассам, §1.7)."""
        tender, offers = _subtree_only_grid(db_session, factories)
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] is None and len(data["rows"]) >= 1
        assert all(c["converged"] is True for c in data["convergence"])

    def test_query_count_does_not_grow_with_columns(self, db_session, grid):
        wc = category_id(db_session, "6")
        with count_queries(db_session) as c2:
            crud_pd.build_position_drilldown(db_session, grid.tender.id, wc, grid.path[:2])
        with count_queries(db_session) as c3:
            crud_pd.build_position_drilldown(db_session, grid.tender.id, wc, grid.path)
        assert c2["n"] == c3["n"]
```

Модульный хелпер к тесту `test_article_with_only_descendant_rows_gets_a_drilldown`
(рядом с `estimates_of` в `test_position_drilldown_api.py`; строит ту же
картину, что тест Task 1, но живёт в этом файле — импортировать тестовые
хелперы между файлами задач нельзя):

```python
def _subtree_only_grid(db_session, factories):
    """Два раунда; все строки лежат в статье 6.1 — у статьи 6 собственных строк нет."""
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
    db_session.flush()
    for n, amount in ((1, "120.00"), (2, "96.00")):
        positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                              article_smr="6.1", number="1"),
                     position(job_title="Фасадная работа", unit="м2", quantity=1, suggested_quantity=1,
                              unit_cost_total=amount, total_cost_total=amount,
                              chapter_ref="1", number="2")]
        import_round(db_session, tender_round=rounds[n],
                     data=round_payload([proposal(positions, vat_rate="20")]),
                     parser_version="4.0.0", import_job_id=None, replace=False,
                     unit_resolver=UnitResolver(db_session),
                     category_resolver=CategoryResolver.from_db(db_session))
    db_session.flush()
    offers = db_session.execute(
        sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
    return tender, offers
```

(Если код «6.1» в справочнике отсутствует — взять первую пару
родитель/потомок из `WorkCategory`, как оговорено в Task 1.)

Плюс HTTP-тест маршрута (клиент — как в соседних тестах роутера, поискать
`client.get(f"/api/v1/tenders/{...}/stage-summary"` в
`backend/tests/integration/` и повторить паттерн):

```python
    def test_http_route_returns_the_same_payload(self, client, db_session, grid):
        wc = category_id(db_session, "6")
        offers = "&".join(f"offers={o}" for o in grid.path)
        response = client.get(f"/api/v1/tenders/{grid.tender.id}/stage-summary/{wc}?{offers}")
        assert response.status_code == 200
        assert response.json()["work_category"]["code"] == "6"
```

- [ ] **Step 2: Прогнать, убедиться в падении.**

- [ ] **Step 3: Реализация**

3a. В `crud/stage_summary.py` переименовать `_money → money_str`,
`_pct → pct_str`, `_change → change_json` (со всеми употреблениями в модуле;
тесты приватные имена не используют — проверено). Докстрока: правило
сериализации одно на свод и разложение.

3b. В `crud/position_drilldown.py`:

```python
CODE_WC_NOT_FOUND = "work_category_not_found"


def _cell_json(c: pd.DrillCell) -> dict:
    quantity = "+".join(str(q) for q in c.quantities) if c.quantities else None
    return {"state": c.state, "amount": crud_ss.money_str(c.shown),
            "amount_unavailable_reason": c.unavailable_reason,
            "quantity": quantity, "quantity_unit": c.quantity_unit,
            "quantity_changed": c.quantity_changed, "estimate_rows": c.estimate_rows,
            "change": crud_ss.change_json(c.change)}


def _row_json(r: pd.DrillRow) -> dict:
    return {"kind": r.kind, "catalog_position_id": r.catalog_position_id,
            "chapter_ref_raw": r.chapter_ref_raw, "title": r.title,
            "ambiguous": r.ambiguous, "group_count": r.group_count,
            "cells": [_cell_json(c) for c in r.cells],
            "bargain": crud_ss.change_json(r.bargain),
            "contribution": {"value": crud_ss.money_str(r.contribution.value),
                             "direction": r.contribution.direction, "reason": None}}


def build_position_drilldown(db: Session, tender_id: int, work_category_id: int,
                             offer_ids: Sequence[int]) -> dict:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.", code=crud_ss.CODE_TENDER_NOT_FOUND,
                          context={"offers": sorted(offer_ids)})
    category = db.get(WorkCategory, work_category_id)
    if category is None:
        raise DomainError(404, f"Статья {work_category_id} не найдена.", code=CODE_WC_NOT_FOUND,
                          context={"offers": sorted(offer_ids)})
    selection = crud_ss.validate_selection(db, tender_id, offer_ids)
    inputs = crud_ss.load_inputs(db, selection)
    columns = [pd.DrillColumn(offer_id=c.offer_id, estimate_id=c.estimate_id, round_id=c.round_id,
                              stage_no=c.stage_no, label=c.label, held_on=c.held_on,
                              vat_rate_base=c.vat_rate_base) for c in inputs]
    groups = load_groups(db, [c.estimate_id for c in columns],
                         subtree_ids(db, work_category_id))
    result = pd.compute_drilldown(columns, groups)
    return {
        "work_category": {"id": category.id, "code": category.code, "title": category.title},
        "columns": [{"offer_id": c.offer_id, "estimate_id": c.estimate_id, "round_id": c.round_id,
                     "stage_no": c.stage_no, "label": c.label, "held_on": iso(c.held_on)}
                    for c in columns],
        "display": {"tax_basis": result.display.basis, "reason": result.display.reason},
        "rows": [_row_json(r) for r in result.rows],
        "convergence": [{"stage_no": v.stage_no, "article_amount": crud_ss.money_str(v.article_amount),
                         "shown_sum": crud_ss.money_str(v.shown_sum),
                         "converged": v.converged, "reason": v.reason} for v in result.convergence],
        "reason": result.reason,
    }
```

(`iso` — из `crud.common`, как в своде; `Tender` добавить в импорты моделей;
`load_inputs` уже читает ставки тем же кодом, что свод, — ось разойтись не
может по построению, §2.8.)

3c. Маршрут в `backend/routers/tenders.py` рядом со `stage_summary`:

```python
@router.get("/{tender_id}/stage-summary/{work_category_id}")
def stage_position_drilldown(tender_id: int, work_category_id: int,
                             offers: list[int] = Query(default=[]), db: Session = Depends(get_db)):
    """Разложение статьи свода по работам (спека 2026-08-30-position-drilldown-design.md §2.11)."""
    return decimal_json(crud_position_drilldown.build_position_drilldown(
        db, tender_id, work_category_id, offers))
```

— с той же обработкой `DomainError`, что у соседнего `stage_summary`
(посмотреть строки 77–95 и повторить дословно, включая импорты).

- [ ] **Step 4: Прогнать** — `uv run pytest tests/integration/test_position_drilldown_api.py tests/integration/test_stage_summary_api.py tests/unit/test_position_drilldown.py -q`; `uv run ruff check crud/ routers/tenders.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/crud/stage_summary.py backend/crud/position_drilldown.py backend/routers/tenders.py backend/tests/integration/test_position_drilldown_api.py
git commit -m "feat(position-drilldown): эндпоинт разложения — сериализация §2.11, коды отказов свода"
```

---

### Task 6: Нетто-ось и недоступные колонки — интеграционно

Ветки §6.2, которые Task 3 доказал литералами, доказать на настоящем импорте:
ось `net` при расходящихся ставках, неизвестная база у середины и у конца.

**Files:**
- Test: `backend/tests/integration/test_position_drilldown_api.py`

**Interfaces:** Consumes: всё из Task 5.

- [ ] **Step 1: Написать падающие тесты** (образцы построения смет с razными
ставками — в `TestTaxBasis` файла `test_stage_summary_api.py`: `chaptered(...,
vat_rate="22")` и `vat_rate=None`; повторить их паттерн):

```python
class TestTaxAxis:
    def _tender(self, db_session, factories, rates):
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n)
                  for n in range(1, len(rates) + 1)}
        db_session.flush()
        for n, rate in enumerate(rates, start=1):
            import_round(db_session, tender_round=rounds[n],
                         data=round_payload([chaptered({"1": ("6", "120.00")}, vat_rate=rate,
                                                       total="120.00")]),
                         parser_version="4.0.0", import_job_id=None, replace=False,
                         unit_resolver=UnitResolver(db_session),
                         category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        return tender, offers

    def test_mixed_rates_recompute_sums_to_net_and_still_converge(self, db_session, factories):
        tender, offers = self._tender(db_session, factories, ["20", "22"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["display"]["tax_basis"] == "net"
        assert data["rows"][0]["cells"][0]["amount"] == "100.00"        # 120 / 1.20
        assert all(c["converged"] is True for c in data["convergence"])

    def test_unknown_base_at_the_endpoint_returns_empty_rows_with_reason(self, db_session, factories):
        tender, offers = self._tender(db_session, factories, [None, "20"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] == "unknown_vat_base" and data["rows"] == [] and data["convergence"] == []

    def test_unknown_base_in_the_middle_marks_only_its_column(self, db_session, factories):
        tender, offers = self._tender(db_session, factories, ["20", None, "20"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] is None
        middle = data["convergence"][1]
        assert middle["converged"] is None and middle["reason"] == "unknown_vat_base"
        assert data["rows"][0]["cells"][1]["amount_unavailable_reason"] == "unknown_vat_base"
```

(Как «сделать ставку неизвестной» — посмотреть в `TestTaxBasis` свода: там
либо `vat_rate=None` в payload, либо стирание `Proposal.vat_rate` UPDATE-ом;
повторить их приём, не изобретать.)

- [ ] **Step 2: Прогнать, убедиться в падении** (если Task 5 полностью
реализован — тесты могут сразу пройти; тогда убедиться, что они КРАСНЕЮТ при
поломке: временно заменить в `compute_drilldown` условие `ends_unknown` на
`False` — `test_unknown_base_at_the_endpoint…` обязан упасть — и вернуть).

- [ ] **Step 3: Реализация** — по ожиданию, ветки уже написаны в Task 3/5;
чинить только найденные расхождения.

- [ ] **Step 4: Прогнать всё бэкенд-множество фичи** —
`uv run pytest tests/unit/test_position_drilldown.py tests/integration/test_position_drilldown_api.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/test_position_drilldown_api.py
git commit -m "test(position-drilldown): нетто-ось и неизвестная база на настоящем импорте"
```

---

### Task 7: Прогон сходимости по стенду (DoD 2)

Скрипт для приёмки: по ОБЕИМ трассам стенда, по всем узлам с непустым
поддеревом (113 и 189, блок замеров) — `converged == true` в каждой колонке и
`article_amount` равен числу строки свода. Не CI-тест: ходит в живой `gca_dev`.

**Files:**
- Create: `backend/scripts/check_drilldown_convergence.py`

**Interfaces:**
- Consumes: `crud.position_drilldown.build_position_drilldown`,
  `crud.stage_summary.build_stage_summary`, DSN из `backend/.env`
  (паттерн — функция `dsn()` в `gen_position_drilldown_inline.py`; но здесь
  нужна Session: собрать `sessionmaker` на `DATABASE_URL`, как делает
  `backend/database.py` — посмотреть и переиспользовать `SessionLocal`,
  если он читает тот же `.env`).
- Produces: печать `узлов проверено N, сходимость N/N` по каждой трассе;
  ненулевой код возврата при любом расхождении.

- [ ] **Step 1: Написать скрипт.** Вход: два тендера стенда, трассы АНТТЕК —
  предложения этапов 1–4; offer_id колонок взять из ответа свода
  (`columns[].offer_id`), сами выборки — как в журнале фичи: тендер 2 и
  тендер 3, участник АНТТЕК, все четыре этапа. Скрипт: для каждого тендера
  найти offer_ids участника по этапам (SQL по образцу `offers_of` из тестов),
  построить свод, взять все `rows` рекурсивно с `has_drilldown_rows == true`,
  для каждой вызвать `build_position_drilldown` и сверить: `reason is None`,
  все `converged is True`, `article_amount == cells[i].amount` строки свода
  (у обеих сторон `None`-суммы считать нулём, «0.00» == null-случай сверить
  явно). Не печатать кириллицу в терминал без `PYTHONIOENCODING=utf-8`.
- [ ] **Step 2: Прогнать на стенде:**
  `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_drilldown_convergence.py`
  Ожидание: `113/113` и `189/189` (числа — из блока замеров; расхождение с
  ними — повод разобраться, не подгонять).
- [ ] **Step 3: ruff** — `uv run ruff check scripts/check_drilldown_convergence.py`.
- [ ] **Step 4: Commit**

```bash
git add backend/scripts/check_drilldown_convergence.py
git commit -m "feat(position-drilldown): скрипт приёмки — сходимость всех узлов обеих трасс стенда"
```

Вывод прогона сохранить — он пойдёт в devlog (Task 13).

---

### Task 8: Фронт — типы, api, ключ запроса, хук

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/services/api/domain.ts`
- Modify: `frontend/src/services/queryKeys.ts`
- Modify: `frontend/src/services/queries.ts`
- Test: `frontend/src/services/queries.tenders.test.tsx` (паттерн соседних тестов)

**Interfaces:**
- Produces:

```ts
// types/domain.ts
export interface StageSummaryRow { ...; has_drilldown_rows: boolean; ... }  // добавить поле

export type StagePositionsRowKind =
  | "position" | "additional_works" | "unmatched"
  | "collapsed_appeared_disappeared" | "rest";

export interface StagePositionsCell {
  state: CellState;
  amount: Decimal | null;
  amount_unavailable_reason: "unknown_vat_base" | null;
  quantity: string | null;          // сырые значения через "+", формат — клиент
  quantity_unit: string | null;
  quantity_changed: boolean;
  estimate_rows: number;
  change: StageSummaryChange;
}

export interface StagePositionsRow {
  kind: StagePositionsRowKind;
  catalog_position_id: number | null;
  chapter_ref_raw: string | null;
  title: string;
  ambiguous: boolean;
  group_count: number | null;
  cells: StagePositionsCell[];
  bargain: StageSummaryChange;
  contribution: { value: Decimal | null; direction: Direction | null; reason: null };
}

export interface StagePositions {
  work_category: { id: number; code: string; title: string };
  columns: { offer_id: number; estimate_id: number; round_id: number;
             stage_no: number; label: string | null; held_on: string | null }[];
  display: { tax_basis: "gross" | "net" | "none";
             reason: "single_rate" | "mixed_rates" | "no_known_rates" };
  rows: StagePositionsRow[];
  convergence: { stage_no: number; article_amount: Decimal | null; shown_sum: Decimal | null;
                 converged: boolean | null; reason: "unknown_vat_base" | null }[];
  reason: "no_rows_in_subtree" | "unknown_vat_base" | null;
}

// api/domain.ts (рядом со stageSummary, тот же paramsSerializer):
stagePositions: (tenderId: number, workCategoryId: number, offerIds: number[]): Promise<StagePositions> =>
  api.get<StagePositions>(`/v1/tenders/${tenderId}/stage-summary/${workCategoryId}`,
    { params: { offers: offerIds }, paramsSerializer: { indexes: null } }).then((r) => r.data),

// queryKeys.ts (рядом со stageSummary):
/** Разложение статьи свода (спека 2026-08-30-position-drilldown-design.md §2.12). */
stagePositions: (tenderId: number, workCategoryId: number, offerIds: number[]) =>
  ["tenders", "stage-positions", tenderId, workCategoryId,
   [...offerIds].sort((a, b) => a - b)] as const,

// queries.ts:
export function useStagePositions(tenderId: number | undefined, workCategoryId: number,
                                  offerIds: number[], enabled: boolean) {
  return useQuery({
    queryKey: qk.tenders.stagePositions(tenderId ?? 0, workCategoryId, offerIds),
    queryFn: () => tendersApi.stagePositions(tenderId as number, workCategoryId, offerIds),
    enabled: enabled && tenderId !== undefined && offerIds.length >= 1,
    // §6.3: раскрытие шлёт РОВНО ОДИН запрос и не шлёт повторно при
    // сворачивании и повторном раскрытии — кэш ключа, без повторной загрузки.
    staleTime: Infinity,
    retry: false,
  });
}
```

- [ ] **Step 1: Написать падающий тест** в `queries.tenders.test.tsx`, блоком
  `describe("useStagePositions", …)` рядом с `describe("useStageSummary", …)`
  и ТЕМ ЖЕ инструментарием (msw: найти, где объявлен хендлер
  `/v1/tenders/:id/stage-summary` — по строке `stage-summary` в
  `frontend/src/test/`, — и добавить рядом хендлер
  `/v1/tenders/:tenderId/stage-summary/:workCategoryId`, отдающий минимальный
  валидный `StagePositions` с `work_category.id` из параметра пути):

```tsx
describe("useStagePositions", () => {
  afterEach(() => {
    resetHandlerState();
  });

  it("грузит разложение и кладёт его под канонический ключ", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStagePositions(300, 22, [7002, 7001], true), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.work_category.id).toBe(22);
    // ключ канонический: порядок id не создаёт второй записи кэша
    expect(qc.getQueryData(qk.tenders.stagePositions(300, 22, [7001, 7002]))).toBeDefined();
  });

  it("enabled=false — запрос не уходит (ленивость §2.1)", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStagePositions(300, 22, [7001, 7002], false), {
      wrapper: wrapperFor(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });
});
```

Плюс: mock-фикстуры свода в тестах фронта получают `has_drilldown_rows`
(tsc покажет все места — прогнать `npx tsc -b` и добить фикстуры).

- [ ] **Step 2: Прогнать** — `npm test -- queries.tenders` падает (нет экспорта),
  `npx tsc -b` падает на фикстурах.
- [ ] **Step 3: Реализовать** блок Interfaces выше; добить фикстуры.
- [ ] **Step 4: Прогнать** — `npm test -- queries.tenders`, `npx tsc -b` зелёные.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/services/api/domain.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts frontend/src/services/queries.tenders.test.tsx
git commit -m "feat(position-drilldown): фронт — типы, api, ключ stage-positions, useStagePositions"
```

Фикстуры с `has_drilldown_rows` коммитить тем же коммитом.

---

### Task 9: Фронт — подписи, форматирование, ячейка работы

**Files:**
- Create: `frontend/src/pages/tenders/summary/drilldownCopy.ts`
- Create: `frontend/src/pages/tenders/summary/drilldownData.ts`
- Create: `frontend/src/pages/tenders/summary/PositionCell.tsx`
- Test: `frontend/src/pages/tenders/summary/PositionCell.test.tsx`
- Test: `frontend/src/pages/tenders/summary/drilldownData.test.ts`

**Interfaces:**
- Consumes: `STATE_LABEL`, `KIND_LABEL`, `REASON_LABEL` из `cellCopy.ts`
  (§6.3 — НОВЫХ словарей состояний не заводить); `ChangeBadge` из
  `SummaryCell.tsx`; `formatDecimalMoney` из `@/lib/format`.
- Produces:

```ts
// drilldownCopy.ts — подписи БЛОКА (не состояний):
export const WORKS_BUTTON_LABEL = "Работы";
export const worksHeading = (code: string) =>
  `Почему изменился итог ${code} — работы статьи и подстатей`;
export const WORKS_SUBHEADING =
  "объясняют ту же сумму, что и строки подстатей выше, другим разрезом; итог сходится из показанного";
export const AMBIGUOUS_PILL = "несколько строк сметы";
export const extraPill = (ref: string) => `допработы · ${ref}`;
export const UNMATCHED_HINT =
  "У строк нет каталожной привязки — недоработан матчинг, строка диагностическая";
export const collapsedTitle = (n: number) => string;   // «ещё N работ появились или исчезли»
export const restTitle = (n: number) => string;        // «прочие N строк статьи»
export const NO_ROWS_LABEL = "В поддереве статьи нет строк ни в одной выбранной колонке";
export const DRILLDOWN_ERROR_LABEL = "Не удалось построить разложение";
export const RETRY_LABEL = "Повторить";

// drilldownData.ts:
/** N кнопки «Работы · N» — число ГРУПП разложения, включая свёрнутые (§2.1). */
export function drilldownGroupCount(rows: StagePositionsRow[]): number;
/** Объём этапа: сырые значения через "+", каждое форматируется разрядами и
 *  не длиннее одного знака после запятой (§2.4): "6+11" -> "6 + 11". */
export function formatQuantity(quantity: string | null): string | null;
/** Устойчивый ключ строки для React: kind + id | ref. */
export function drilldownRowKey(row: StagePositionsRow): string;
```

`PositionCell` — ячейка работы, три этажа (§2.4): сумма (или подпись состояния
из `STATE_LABEL`), объём (`formatQuantity` + `quantity_unit`, тоном
`text-warning-text` при `quantity_changed`), изменение (`ChangeBadge` — но на
первой колонке и у `kind: none` ничего). У строки без объёма третьего этажа нет
(§6.3). Правило повтора §2.5: при `state == removed` вид изменения `removed` в
ячейке не повторяется (это правило уже в `SummaryCell` свода — посмотреть, как
там, и повторить приём).

- [ ] **Step 1: Написать падающие тесты**

`drilldownData.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { drilldownGroupCount, formatQuantity } from "./drilldownData";
import type { StagePositionsRow } from "@/types/domain";

const row = (kind: StagePositionsRow["kind"], group_count: number | null = null) =>
  ({ kind, group_count, catalog_position_id: null, chapter_ref_raw: null, title: "",
     ambiguous: false, cells: [], bargain: { kind: "none", value: null, direction: null, reason: null },
     contribution: { value: null, direction: null, reason: null } }) as StagePositionsRow;

describe("drilldownGroupCount", () => {
  it("считает свёрнутые по group_count, остальные по одному — N кнопки §2.1", () => {
    const rows = [row("position"), row("additional_works"),
                  row("collapsed_appeared_disappeared", 3), row("rest", 2)];
    expect(drilldownGroupCount(rows)).toBe(7);
  });
});

describe("formatQuantity", () => {
  it("форматирует каждое значение разрядами и одним знаком, сохраняя '+'", () => {
    expect(formatQuantity("8726.397168")).toBe("8 726,4");
    expect(formatQuantity("6+11")).toBe("6 + 11");
    expect(formatQuantity(null)).toBeNull();
  });
});
```

(Точный разделитель разрядов — что даёт `Intl.NumberFormat("ru-RU")` в jsdom;
если в тестовом окружении неразрывный пробел — сравнивать с `\u00a0`/`\u202f`
через `.replace(/\s/g, " ")`, как это делают соседние тесты форматирования —
поискать `formatDecimalMoney` в тестах и повторить приём.)

`PositionCell.test.tsx` — компонент рендерится строкой таблицы, поэтому
обёртка `<table><tbody><tr>…</tr></tbody></table>` обязательна (jsdom ругается
на `<td>` вне таблицы):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StagePositionsCell } from "@/types/domain";
import { KIND_LABEL, STATE_LABEL } from "./cellCopy";
import { PositionCell } from "./PositionCell";

function cell(overrides: Partial<StagePositionsCell>): StagePositionsCell {
  return {
    state: "amount", amount: "1000.00", amount_unavailable_reason: null,
    quantity: null, quantity_unit: null, quantity_changed: false,
    estimate_rows: 1,
    change: { kind: "none", value: null, direction: null, reason: "first_column" },
    ...overrides,
  };
}

function renderCell(c: StagePositionsCell) {
  return render(
    <table><tbody><tr><PositionCell cell={c} /></tr></tbody></table>
  );
}

describe("PositionCell — три этажа (§2.4)", () => {
  it("сумма, объём и изменение стоят в одной ячейке", () => {
    renderCell(cell({ quantity: "8726.397168", quantity_unit: "м²",
                      change: { kind: "percent", value: "-5.0", direction: "down", reason: null } }));
    const td = screen.getByRole("cell");
    expect(td).toHaveTextContent("1 000,00");
    expect(td).toHaveTextContent("8 726,4");
    expect(td).toHaveTextContent("м²");
    expect(td).toHaveTextContent("-5");
  });

  it("изменившийся объём выделен тоном, неизменившийся — нет (§6.3)", () => {
    const { rerender } = renderCell(cell({ quantity: "6+11", quantity_unit: "шт", quantity_changed: true }));
    expect(screen.getByTestId("cell-quantity")).toHaveClass("text-warning-text");
    rerender(<table><tbody><tr>
      <PositionCell cell={cell({ quantity: "6", quantity_unit: "шт" })} />
    </tr></tbody></table>);
    expect(screen.getByTestId("cell-quantity")).not.toHaveClass("text-warning-text");
  });

  it("у ячейки без объёма третьего этажа нет (§6.3)", () => {
    renderCell(cell({}));
    expect(screen.queryByTestId("cell-quantity")).toBeNull();
  });

  it("состояние без суммы печатает подпись из STATE_LABEL, не число", () => {
    renderCell(cell({ state: "absent", amount: null, estimate_rows: 0 }));
    expect(screen.getByRole("cell")).toHaveTextContent(STATE_LABEL.absent);
  });

  it("исчезнувшая печатает «нет в файле», а не «снято» (§6.3)", () => {
    renderCell(cell({ state: "absent", amount: null, estimate_rows: 0,
                      change: { kind: "disappeared", value: null, direction: null, reason: null } }));
    expect(screen.getByRole("cell")).toHaveTextContent(KIND_LABEL.disappeared);
    expect(screen.getByRole("cell")).not.toHaveTextContent(KIND_LABEL.removed);
  });

  it("«снято» состоянием не повторяется видом изменения (§2.5)", () => {
    renderCell(cell({ state: "removed", amount: null,
                      change: { kind: "removed", value: null, direction: null, reason: null } }));
    const matches = screen.getByRole("cell").textContent?.match(/снято/g) ?? [];
    expect(matches).toHaveLength(1);
  });
});
```

(Точная печать `1 000,00` — как `formatDecimalMoney`; если разряды с
` `, сравнивать через ту же нормализацию, что соседние тесты формата.)

- [ ] **Step 2: Прогнать, убедиться в падении.**
- [ ] **Step 3: Реализовать** три файла. `formatQuantity`: разбить по `"+"`,
  каждое — `new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 })
  .format(Number(part))`, соединить `" + "`. `drilldownGroupCount`:
  `rows.reduce((n, r) => n + (r.kind === "collapsed_appeared_disappeared" ||
  r.kind === "rest" ? (r.group_count ?? 0) : 1), 0)`.
- [ ] **Step 4: Прогнать** — vitest + `npm run lint` (по всему фронту) зелёные.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/tenders/summary/drilldownCopy.ts frontend/src/pages/tenders/summary/drilldownData.ts frontend/src/pages/tenders/summary/PositionCell.tsx frontend/src/pages/tenders/summary/PositionCell.test.tsx frontend/src/pages/tenders/summary/drilldownData.test.ts
git commit -m "feat(position-drilldown): фронт — ячейка работы и подписи блока"
```

---

### Task 10: Фронт — блок разложения `PositionDrilldown`

Строки блока — строки ТОЙ ЖЕ таблицы свода (те же колонки этапов, «Торг»,
«Вклад»), поэтому компонент возвращает фрагмент `<TableRow>` и монтируется
внутри `<TableBody>` под строкой статьи.

**Files:**
- Create: `frontend/src/pages/tenders/summary/PositionDrilldown.tsx`
- Modify: `frontend/src/test/fixtures.ts` — добавить `sampleStagePositions`:
  валидный `StagePositions` с двумя колонками и четырьмя строками (position с
  объёмом и `disappeared`-концом; additional_works с `chapter_ref_raw: "1.2"`;
  collapsed с `group_count: 3`; rest с `group_count: 2`), `converged: true` в
  обеих колонках; инварианты фикстуры — рядом в `fixtures.test.ts` по образцу
  соседних (сходимость: сумма ячеек строк равна `article_amount` колонки;
  `estimate_rows == 0 ⟺ state == "absent"`).
- Test: `frontend/src/pages/tenders/summary/PositionDrilldown.test.tsx`

**Interfaces:**
- Consumes: `useStagePositions` (Task 8), `PositionCell`, `drilldownCopy`,
  `drilldownData`, `ChangeBadge`, `ContributionValue`-подход (свой аналог —
  функция в `StageSummaryTable.tsx` приватная, скопировать её JSX-приём, не
  экспортировать из чужого файла), `REASON_LABEL` (unknown_vat_base),
  `Skeleton`, `Button`.
- Produces:

```tsx
export function PositionDrilldown(props: {
  tenderId: number;
  workCategoryId: number;
  articleCode: string;
  offerIds: number[];
  columnsCount: number;   // для colSpan = columnsCount + 3
  open: boolean;          // блок скрыт шевроном корня или кнопкой — строки не рисуются,
                          // но компонент ОСТАЁТСЯ смонтированным (кэш и счётчик N)
  onCount?: (n: number) => void;  // сообщить N родителю для подписи кнопки
}): JSX.Element | null
```

Поведение:
- хук `useStagePositions(tenderId, workCategoryId, offerIds, enabled: true)` —
  компонент монтируется ТОЛЬКО после первого открытия (родитель, Task 11),
  поэтому `enabled` не зависит от `open`;
- по приходу данных вызвать `onCount(drilldownGroupCount(rows))` (в
  `useEffect` по `data`);
- `open === false` → `null` (строки скрыты, запрос не повторяется — кэш);
- загрузка → одна строка: `<TableRow><TableCell colSpan={columnsCount + 3}>
  <Skeleton className="h-16" /></TableCell></TableRow>` (строка-скелет во всю
  ширину, §2.12);
- ошибка → строка с `DRILLDOWN_ERROR_LABEL` (плюс код причины из
  `apiErrorCode`, если он есть в `ERROR_LABEL`) и кнопкой `RETRY_LABEL` →
  `refetch()` (§2.12);
- `reason` в ответе → строка с подписью: `no_rows_in_subtree` →
  `NO_ROWS_LABEL`; `unknown_vat_base` → `REASON_LABEL.unknown_vat_base`;
- иначе: строка-заголовок блока (`worksHeading(articleCode)` +
  `WORKS_SUBHEADING` второй строкой, colSpan на всю ширину), затем строки:
  - первая ячейка: наименование (`line-clamp-2` + полный текст в `title`,
    §2.10) и пилюли: `ambiguous` → `AMBIGUOUS_PILL`; `kind ===
    "additional_works"` → `extraPill(chapter_ref_raw)`; `kind === "unmatched"`
    → пилюля с `UNMATCHED_HINT` в title; свёрнутые (`collapsed…`, `rest`) —
    `collapsedTitle(group_count)` / `restTitle(group_count)` вместо
    наименования, приглушённым тоном (`text-fg-tertiary`);
  - этапные ячейки — `PositionCell`;
  - «Торг» — `ChangeBadge change={row.bargain} dashOnNone` (у свёрнутых kind
    `none` → прочерк);
  - «Вклад» — число с тоном направления или прочерк (тот же приём, что
    `ContributionValue` свода).
- Ключи строк — `drilldownRowKey`.

- [ ] **Step 1: Написать падающие тесты** (`PositionDrilldown.test.tsx`).
  Мокать `useStagePositions` через `vi.mock("@/services/queries", …)` — тем же
  приёмом, что `StageSummaryPage.test.tsx` мокает `useStageSummary` (посмотреть
  и повторить форму мока, включая частичный `importActual`). Рендерить внутри
  `<table><tbody>` — фрагмент строк вне таблицы падает в jsdom:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sampleStagePositions } from "@/test/fixtures";
import { useStagePositions } from "@/services/queries";
import { KIND_LABEL } from "./cellCopy";
import { NO_ROWS_LABEL, RETRY_LABEL, extraPill, worksHeading } from "./drilldownCopy";
import { drilldownGroupCount } from "./drilldownData";
import { PositionDrilldown } from "./PositionDrilldown";

vi.mock("@/services/queries", async (importActual) => ({
  ...(await importActual<object>()),
  useStagePositions: vi.fn(),
}));
const mocked = vi.mocked(useStagePositions);

const PROPS = { tenderId: 300, workCategoryId: 22, articleCode: "3.1",
                offerIds: [7001, 7002], columnsCount: 2, open: true };

function renderRows(props = {}) {
  return render(
    <table><tbody><PositionDrilldown {...PROPS} {...props} /></tbody></table>
  );
}

function success(data = sampleStagePositions) {
  mocked.mockReturnValue({ isPending: false, isError: false, data,
                           refetch: vi.fn() } as never);
}

describe("PositionDrilldown (§2.1, §2.12, §6.3)", () => {
  it("заголовок блока называет вопрос и статью, строки — под ним", () => {
    success();
    renderRows();
    expect(screen.getByText(worksHeading("3.1"))).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
  });

  it("скелет на загрузке — одной строкой во всю ширину (§2.12)", () => {
    mocked.mockReturnValue({ isPending: true, isError: false, data: undefined,
                             refetch: vi.fn() } as never);
    renderRows();
    const row = screen.getByRole("row");
    expect(within(row).getByRole("cell")).toHaveAttribute("colspan", "5"); // columnsCount + 3
  });

  it("отказ — текст причины и кнопка «Повторить», клик зовёт refetch (§2.12)", async () => {
    const refetch = vi.fn();
    mocked.mockReturnValue({ isPending: false, isError: true, data: undefined,
                             error: new Error("boom"), refetch } as never);
    renderRows();
    await userEvent.setup().click(screen.getByRole("button", { name: RETRY_LABEL }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("reason=no_rows_in_subtree печатает подпись, а не пустоту", () => {
    success({ ...sampleStagePositions, rows: [], convergence: [], reason: "no_rows_in_subtree" });
    renderRows();
    expect(screen.getByText(NO_ROWS_LABEL)).toBeInTheDocument();
  });

  it("свёрнутые строки не несут процентов и «Торга» (§2.3)", () => {
    success();
    renderRows();
    for (const kindRow of screen.getAllByTestId(/drill-row-(collapsed|rest)/)) {
      expect(within(kindRow).queryByText(/%/)).toBeNull();
    }
  });

  it("пилюля «допработы · 1.2» у kind=additional_works (§2.7)", () => {
    success();
    renderRows();
    expect(screen.getByText(extraPill("1.2"))).toBeInTheDocument();
  });

  it("исчезнувшая работа печатает «нет в файле», а не «снято» (§6.3)", () => {
    success();
    renderRows();
    expect(screen.getAllByText(KIND_LABEL.disappeared).length).toBeGreaterThan(0);
  });

  it("open=false не рисует строк, но запрос остаётся включённым (кэш и счётчик N)", () => {
    success();
    renderRows({ open: false });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(mocked).toHaveBeenCalledWith(300, 22, [7001, 7002], true);
  });

  it("onCount получает число групп с учётом свёрнутых (§2.1: N кнопки)", () => {
    success();
    const onCount = vi.fn();
    renderRows({ onCount });
    expect(onCount).toHaveBeenCalledWith(drilldownGroupCount(sampleStagePositions.rows));
  });
});
```

(Строки получают `data-testid={`drill-row-${kind}`}` — коротко: `collapsed`
для `collapsed_appeared_disappeared`, `rest`, `position`, `extra`, `unmatched`;
тест выше на это опирается.)

- [ ] **Step 2: Прогнать, убедиться в падении.**
- [ ] **Step 3: Реализовать.**
- [ ] **Step 4: Прогнать** — vitest, `npm run lint`, `npx tsc -b`.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/tenders/summary/PositionDrilldown.tsx frontend/src/pages/tenders/summary/PositionDrilldown.test.tsx
git commit -m "feat(position-drilldown): фронт — блок разложения строками таблицы свода"
```

---

### Task 11: Фронт — кнопка «Работы · N» и вложенные раскрытия

**Files:**
- Modify: `frontend/src/pages/tenders/summary/StageSummaryTable.tsx`
- Test: `frontend/src/pages/tenders/summary/StageSummaryTable.test.tsx`

**Interfaces:**
- Consumes: `has_drilldown_rows` (Task 1/8), `PositionDrilldown` (Task 10),
  `WORKS_BUTTON_LABEL` (Task 9).
- Produces: три НЕЗАВИСИМЫХ ключа раскрытия (§2.1): `expandedIds` (подстатьи,
  есть), `worksOpenIds` (блок работ), `worksMountedIds` (блок когда-либо
  открывался — компонент остаётся смонтированным ради кэша и счётчика N).
  Правило видимости: строка скрыта, если закрыт хотя бы один из её ключей —
  шеврон корня гасит и подстатьи, и работы.

Поведение (§2.1, §2.12, §6.3):
- кнопка рисуется при `row.work_category_id !== null && row.has_drilldown_rows`
  — НЕ по пустоте `children`;
- подпись: `Работы` до первой загрузки; после (`onCount` вернул N) —
  `Работы · N`; N не пропадает при сворачивании (компонент смонтирован,
  состояние счётчика — `Map<number, number>` в `StageSummaryTable`);
- кнопка — `aria-expanded`, слово, а не второй шеврон; отдельна от шеврона
  подстатей;
- `PositionDrilldown` монтируется после первого открытия
  (`worksMountedIds.has(id)`), рисуется при `worksOpenIds.has(id)` И статья
  видима (все предки раскрыты — компонент стоит в JSX под строкой статьи,
  поэтому «предки раскрыты» уже обеспечено структурой: свёрнутый предок не
  рендерит детей вовсе; передать `open={worksOpenIds.has(id)}`);
- `StageSummaryTable` получает новые пропсы `tenderId: number` и
  `offerIds: number[]` (для запроса разложения) — прокинуть из
  `StageSummaryPage.tsx` (`<StageSummaryTable summary={summary} tenderId={id!}
  offerIds={offerIds} />`).

- [ ] **Step 1: Написать падающие тесты** в `StageSummaryTable.test.tsx`.
  Сначала механика: таблица получает пропсы `tenderId={300}
  offerIds={[7001, 7002]}` — обновить ВСЕ существующие рендеры файла (и
  `StageSummaryPage.test.tsx`, если он рендерит таблицу). Мок
  `useStagePositions` — тот же `vi.mock`, что в `PositionDrilldown.test.tsx`.
  Новый блок:

```tsx
describe("Кнопка «Работы · N» и вложенные раскрытия (§2.1, §6.3)", () => {
  function summaryWith(flags: Record<string, boolean>): StageSummary {
    return { ...sampleStageSummary,
             rows: sampleStageSummary.rows.map((r) =>
               ({ ...r, has_drilldown_rows: flags[r.code ?? ""] ?? r.has_drilldown_rows })) };
  }

  function renderTable(summary = sampleStageSummary) {
    return render(<StageSummaryTable summary={summary} tenderId={300} offerIds={[7001, 7002]} />);
  }

  it("кнопка рисуется по has_drilldown_rows, а не по children", () => {
    // «6» в фикстуре — с детьми; выключаем её флаг, включаем у бездетной «2»:
    // при правиле «по children» обе проверки ниже красные.
    renderTable(summaryWith({ "6": false, "2": true }));
    expect(within(screen.getByTestId(rowTestId("2"))).getByRole("button", { name: /Работы/ })).toBeInTheDocument();
    expect(within(screen.getByTestId(rowTestId("6"))).queryByRole("button", { name: /Работы/ })).toBeNull();
  });

  it("клик открывает блок; сворачивание и повторное раскрытие хук не выключают (один запрос — кэш ключа)", async () => {
    const user = userEvent.setup();
    successStagePositions();          // mocked.mockReturnValue(...) с sampleStagePositions
    renderTable(summaryWith({ "2": true }));
    const button = screen.getByRole("button", { name: /Работы/ });
    await user.click(button);
    expect(screen.getByText(worksHeading("2"))).toBeInTheDocument();
    await user.click(button);         // свернуть
    expect(screen.queryByText(worksHeading("2"))).toBeNull();
    await user.click(button);         // раскрыть снова
    // хук всё время вызывался с enabled=true — запрос не пересоздаётся,
    // данные из кэша (staleTime: Infinity, Task 8)
    expect(vi.mocked(useStagePositions)).toHaveBeenLastCalledWith(300, expect.any(Number), [7001, 7002], true);
  });

  it("после загрузки кнопка — «Работы · N», и N не пропадает при сворачивании (§2.1)", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable(summaryWith({ "2": true }));
    const button = screen.getByRole("button", { name: /Работы/ });
    expect(button).not.toHaveTextContent("·");        // до первой загрузки счётчика нет
    await user.click(button);
    const n = drilldownGroupCount(sampleStagePositions.rows);
    expect(screen.getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Работы · ${n}` }));
    expect(screen.getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();
  });

  it("шеврон корня гасит и подстатьи, и работы; их собственные ключи независимы", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable(summaryWith({ "6": true }));           // «6» — с детьми и с работами
    await user.click(within(screen.getByTestId(rowTestId("6"))).getByRole("button", { name: /Раскрыть/ }));
    await user.click(within(screen.getByTestId(rowTestId("6"))).getByRole("button", { name: /Работы/ }));
    expect(screen.getByText(worksHeading("6"))).toBeInTheDocument();
    // Сворачиваем ПОДСТАТЬИ — блок работ остаётся
    await user.click(within(screen.getByTestId(rowTestId("6"))).getByRole("button", { name: /Свернуть/ }));
    expect(screen.getByText(worksHeading("6"))).toBeInTheDocument();
  });

  it("у «Нераспределённого» кнопки нет", () => {
    renderTable();
    expect(within(screen.getByTestId("row-unallocated")).queryByRole("button", { name: /Работы/ })).toBeNull();
  });
});
```

`rowTestId(code)` — маленький локальный хелпер: найти в фикстуре строку по
коду и вернуть `row-${work_category_id}` (data-testid строк уже устроен так,
см. `CategoryRowGroup`). Первый тест переписать через него же — черновой
вариант с `rowIdByCode` в примере выше заменить на `rowTestId`.
`successStagePositions()` — локальный хелпер, оборачивающий
`vi.mocked(useStagePositions).mockReturnValue({ isPending: false,
isError: false, data: sampleStagePositions, refetch: vi.fn() } as never)`.

Про «шеврон корня гасит всё»: в реализации блок работ ребёнка живёт в JSX под
строкой ребёнка, и свёрнутый корень не рендерит детей вовсе — этот случай
покрывается тестом раскрытий выше (блок «6» стоит под строкой «6» и виден,
пока видима сама строка); отдельного теста на корень не надо, у корня-статьи
блок гасится тем же правилом видимости строки.

- [ ] **Step 2: Прогнать, убедиться в падении.**
- [ ] **Step 3: Реализовать.**
- [ ] **Step 4: Прогнать всё фронтовое** — `npm test`, `npm run lint`, `npx tsc -b`.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/tenders/summary/StageSummaryTable.tsx frontend/src/pages/tenders/summary/StageSummaryTable.test.tsx frontend/src/pages/tenders/summary/StageSummaryPage.tsx
git commit -m "feat(position-drilldown): кнопка «Работы · N» и вложенные раскрытия в своде"
```

(`StageSummaryPage.tsx` — если менялись пропсы, тем же коммитом; его тест
дополнить прокидыванием.)

---

### Task 12: Браузерная приёмка — раскладка и сверка с макетом (DoD 1, 5, 6)

Не unit-этап: живой стенд, системный Chrome через playwright в scratchpad
(`channel: "chrome"`; готовый проект прошлой сессии —
`C:\Users\zhukov_v\AppData\Local\Temp\claude\c--Users-zhukov-v-Projects-GCA-MVP\5436d5a9-4ab3-45b9-b452-fb5767c6deb2\scratchpad`,
если потерян — `npm i playwright` с `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`).
Бэкенд `just dev-backend` (8259), фронт `just dev-frontend` (5173/5174 —
читать лог СВОЕГО vite). Учётка стенда: `admin@example.com` / `gca-admin-2026`.

- [ ] **Step 1 (DoD 1):** на стенде открыть свод тендера 2 (АНТТЕК, этапы
  1–4), раскрыть 8.3 и 3.1: 8.3 — три объяснителя, исчезнувшая «Входная дверь в
  здание», четыре появившихся; 3.1 — появление щебня, исчезновение
  геотекстиля, строка допработ. **Пилюли — «нет в файле», не «снято»** (§5
  DoD 1 — приёмка обязана смотреть на слово).
- [ ] **Step 2 (DoD 5):** замер числом на РЕАЛИЗАЦИИ, скриптом по образцу
  `measure.mjs`: 1280 и 1100 px, обе темы; нет горизонтальной прокрутки
  страницы (`scrollWidth == clientWidth`); таблица не шире контейнера; доля
  колонки подписи на САМОМ ДЛИННОМ наименовании; правые колонки достижимы.
- [ ] **Step 3 (DoD 6):** машинная сверка макета с реализацией ДО показа
  (AGENTS §11): открыть макет и реализацию, снять вычисленные стили узлов
  (высота строки работы, вес/тон подписи, отступы блока, вид кнопки) и
  сопоставить по свойствам скриптом (образец — сверка фичи 3, devlog §9).
- [ ] **Step 4:** расхождения чинить или письменно фиксировать долгом;
  результаты замеров сохранить для devlog.
- [ ] **Step 5: Commit** (если были правки стилей):

```bash
git add frontend/src/pages/tenders/summary/
git commit -m "fix(position-drilldown): раскладка по замеру на стенде и сверке с макетом"
```

---

### Task 13: Документы — AGENTS §11, рамка контура, devlog, insights

**Files:**
- Modify: `AGENTS.md` (§11)
- Modify: `docs/proposals/2026-08-25-tenders-model.md` (§4)
- Create: `docs/insights/` — два файла (посмотреть именование соседних файлов
  в каталоге и повторить стиль)
- Create: devlog фичи (посмотреть, где лежат devlog фич 1–3 —
  `docs/superpowers/devlogs/` или рядом — и положить туда же)

Содержание (спека §2.14):
- [ ] **AGENTS §11**: добавить — `max-width` у `td` в АВТОРАЗМЕТКЕ не
  обязывает; долю ширины мерить на САМОМ ДЛИННОМ содержимом; зелёный замер на
  коротком содержимом потолок не проверяет вовсе (с числами §1.6: 519/1238
  против 358,7/1200).
- [ ] **Рамка §4**: (а) хвост фичи 3 — «PR #34, смержен», снять «в работе»;
  (б) раздел фичи 4 — ссылки на спеку и план (devlog добавить после мержа).
- [ ] **Insight 1**: «после правки решения выписать всё зависящее и пройти
  список инструментом» — материал: восемь кругов ревью, семь — один класс
  (журнал `.superpowers/sdd/2026-08-29-position-drilldown/NEXT-SESSION.md` §6).
- [ ] **Insight 2**: «замер обязан строить данные той же моделью, что и экран»
  — материал: спека §1.8 (таблица двух моделей) и §1.4 (507/294 → 480/156).
- [ ] **Devlog**: ход гейтов, восемь кругов, вывод прогона Task 7, замеры
  Task 12.
- [ ] **Commit**:

```bash
git add AGENTS.md docs/proposals/2026-08-25-tenders-model.md docs/insights/ docs/superpowers/
git commit -m "docs(position-drilldown): AGENTS §11, рамка §4, insights, devlog"
```

---

### Task 14: Финал — CI и PR

- [ ] `cd backend && uv run ruff check . && uv run pytest -q` — зелёные.
- [ ] `cd frontend && npm run lint && npx tsc -b && npm test` — зелёные.
- [ ] `just ci` (не в конвейере; код возврата отдельно: `just ci > ci.log 2>&1; echo $?`).
  Флака `max_locks_per_transaction` — перепрогнать; `df -h /tmp` при
  подозрении на диск.
- [ ] Пуш ветки `feat/position-drilldown`, PR со ссылками на спеку и план
  (REQUIRED SUB-SKILL: superpowers:finishing-a-development-branch).

---

## Self-review (выполнен при написании)

- **Покрытие спеки:** §2.1 (кнопка, поддерево, has_drilldown_rows, N) — Tasks
  1, 3, 5, 10, 11; §2.2 (три вида групп, ключи) — Tasks 3, 4; §2.3 (классы,
  COVERAGE, PARTIAL_CAP) — Task 3; §2.4 (три этажа, объём) — Tasks 4, 9;
  §2.5–§2.6 (словарь типами, вклад от нуля) — Tasks 2, 3; §2.7 (допработы по
  ссылке, подпись, пилюли) — Tasks 4, 9, 10; §2.8 (ось) — Tasks 2, 6; §2.9
  (порядок) — Task 3; §2.10 (обрезка) — Task 10; §2.11 (эндпоинт, форма,
  запросы) — Tasks 5, 6; §2.12 (экран, ленивость, скелет, отказ) — Tasks
  8–11; §2.13 (сходимость) — Tasks 3, 5, 7; §2.14 (документы) — Task 13;
  §5 DoD 1–7 — Tasks 12 (1, 5, 6), 7 (2), 6 (3), 1/3/4/5/6 (4), 14 (7).
- **Фикстуры DoD 4:** неизвестная база у конца/середины — Task 6; неконечная
  сумма — предикат Task 4 (юнит на фильтр можно добавить при ревью Task 4);
  строка без привязки — Task 4; статья только с допработами — Task 1 (свод) и
  Task 5 (эндпоинт, хелпер `_subtree_only_grid`); несколько строк под одной
  ссылкой с разными наименованиями (подпись по ordinal) — покрыта SQL
  `array_agg(order by ordinal)` Task 4: добавить в Task 4 тест с двумя
  строками «Сведений» с ОДНОЙ ссылкой в одном предложении (одна группа,
  `ambiguous`, rows=2, подпись — первая по файлу) — исполнителю Task 4
  включить его в Step 1 по образцу соседнего;
  переименованная при сохранённой ссылке — Task 4
  (`test_extras_are_rows_by_ref_and_title_comes_from_the_last_stage`);
  статья с пустым поддеревом — Task 5; нулевое движение — Task 3; целиком
  появившаяся — Task 3.
- **Согласованность имён:** `pd.compute_drilldown`, `pd.GroupInput`,
  `pd.GroupStage`, `pd.DrillColumn`, `crud_pd.load_groups`,
  `crud_pd.subtree_ids`, `crud_pd.build_position_drilldown`,
  `crud_ss.money_str/pct_str/change_json`, `useStagePositions`,
  `drilldownGroupCount`, `PositionDrilldown`, `PositionCell` — единые во всех
  задачах.

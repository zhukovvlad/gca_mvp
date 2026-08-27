# Свод по этапам, по статьям — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Выбранные на решётке карточки тендера предложения одного участника
становятся сводом «статьи × этапы» с изменениями, «Торгом», «Вкладом»,
сходимостью и объявленными осями — по одному серверному расчёту, без новой схемы.

**Architecture:** Чистый расчётный модуль `services/stage_summary.py`
(состояния ячеек, матрица переходов, проценты под `Decimal`, сортировка,
видимость дерева) над данными, которые тонкий слой `crud/stage_summary.py`
читает постоянным числом запросов из `v_category_totals`, итогов файлов и
ручных решений; один эндпоинт `GET /api/v1/tenders/{id}/stage-summary` в
существующем роутере; на клиенте — плитки-переключатели в `OfferGrid` и
страница `/tenders/:id/summary`, которая только рисует ответ.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 / PostgreSQL 16 / pytest;
React 19 / TanStack Query 5 / shadcn/ui (`Toggle`) / vitest + msw.

**Spec:** [`docs/superpowers/specs/2026-08-27-stage-summary-design.md`](../specs/2026-08-27-stage-summary-design.md)
(гейт 2 закрыт 27.08.2026 после двух кругов внешнего ревью). План на спеку
**ссылается** и не пересказывает: при расхождении побеждает спека (`AGENTS.md`
§9.2). Форма ответа — спека §2.16, она обязательна дословно. Макет —
[`2026-08-27-stage-summary-mockup.html`](../specs/2026-08-27-stage-summary-mockup.html).

**Ветка:** `feat/stage-summary` — **уже существует**, в ней макет, спека и
ревизия `AGENTS.md` v6.12. Новой ветки не заводить; PR один.

---

## Global Constraints

Требования спеки, действующие в КАЖДОЙ задаче.

1. **Своей миграции нет, схема не меняется.** `backend/parser/`, импорт,
   матчинг, каталог, единицы, `project_passport.py`, `analytics.py`,
   `comparison.py` — не правятся ни строкой.
2. **Источник величины — `v_category_totals` через `CATEGORY_TOTALS`
   (`crud/project_passport.py`)**, обе ветви `source`; собственных сумм по
   `position_items` нет (§2.4). Свёртка — `services/category_rollup.build_tree`.
3. **Четыре состояния ячейки** `amount | removed | not_evaluated | absent`;
   `rows{row_count, rows_with_amount, rows_not_finite}` — характеристика поверх
   (§2.5). `removed` — по предыдущим **выбранным** колонкам.
4. **Путь = выбранные колонки**, порядок по `stage_no` независимо от порядка в
   запросе (§2.6).
5. **Матрица переходов §2.6 целиком**; `change.kind ∈ {percent, abs_only,
   appeared, reappeared, removed, disappeared, none}`; процент только при базе
   `> 0`, деление при базе `≤ 0` **не вызывается**; `direction ∈ {up, down, flat}`
   по величинам.
6. **Ось НДС — три случая по множеству известных ставок** (§2.8); колонка
   `unknown_vat_base` — суммы `null` с `amount_unavailable_reason` на любой оси,
   состояние ячейки сохранено, дельты `none`.
7. **Сходимость — всегда в исходных валовых деньгах**; `converged = null` ⇔
   файловый итог недоступен (§2.9). Итог колонки, трасса и `kpi.first_to_last`
   — сумма корней плюс «Нераспределённое» на оси показа, не файловый итог.
8. **`additional_works_amount` независим от состояния ячейки**;
   `contribution.value` «Нераспределённого» — число, `bargain` без процента
   (§2.16).
9. **Считает сервер, до квантования**: `Decimal`, `_DIV_CONTEXT`; в JSON —
   строки, квантованные `quantize_money` и до 0,1 п.п. Клиент не сравнивает и
   не делит Decimal-строки — `bar_height_pct`, `track.available`, `kpi` приходят
   готовыми (§2.11, §2.12).
10. **Число SQL-запросов не растёт с числом колонок** (§2.12).
11. **Отказы — `DomainError` с `code`** через `raise_domain_error`; `detail` —
    один объект `{code, message, offers}` у 404 и 422 (§2.3, §2.16).
12. **Права:** чтение — любая авторизованная роль (`_auth_dep` роутера);
    разнос на сметах предложений — `member`, без правок кода (§2.10).
13. **Фронтенд — только shadcn/ui** (`Toggle`, `Tooltip`, существующие
    `ui-domain`), токены приложения (не имена макета), обе темы; состояние
    ячейки рендерится **по данным**, не выводом клиента.
14. **Карточка без выбора сохраняет прежние факты**: состав решётки, три
    состояния ячейки, «Итого с НДС»; утверждения тестов о фактах остаются, DOM
    меняется (DoD 6).
15. **Кириллица в терминал — только с `PYTHONIOENCODING=utf-8`**; тесты
    интеграции против `gca_test` — один прогон за раз; `just ci` перед пушем.

---

## Решения плана (спека молчит — зафиксировано на гейте 3)

| | Решение | Основание |
|---|---|---|
| **Р1** | Расчёт — чистый модуль `services/stage_summary.py` (dataclass'ы `ColumnInput`, `CellInput`, `Cell`, `Change`, `Contribution`, `SummaryRow`, `ColumnOut`, функции `cell_states`, `change_between`, `contribution_between`, `pick_tax_basis`, `sort_rows`, `visible_tree`, `compute_summary`); чтение БД — `crud/stage_summary.py` (`load_inputs`, `build_stage_summary`) | тот же раздел труда, что `category_rollup` ↔ `project_passport`: чистая арифметика тестируется литералами без БД |
| **Р2** | Ручные решения по колонке считаются одним запросом по `EstimateCategoryOverride` (`count`, `max(assigned_at)`) по списку смет, а не вызовом `_manual_assignments` на каждую смету | `_manual_assignments` тянет `_section_metrics` (дерево разделов) и делает N запросов на смету — нарушило бы ограничение 10; спека §1.2 требует не второй копии *дерева*, а признака и даты, которые уже есть в таблице решений |
| **Р3** | Дерево статей строится `build_tree` по колонке, затем объединяется в один скелет по `work_category_id` (`visible_tree`): корни всегда, вложенный узел — если хотя бы в одной колонке `total` ненулевой | спека §2.14: `build_tree` сохраняет узлы с явным нулём (`rows > 0`), фильтрация после свёртки |
| **Р4** | Ставка колонки — по `vat_rate_base` строк `CATEGORY_TOTALS` этой сметы: одна общая → известна; разные или `NULL` → `unknown_vat_base`; у сметы без строк VIEW — `_vat_rate`-правило единогласия по `Proposal.vat_rate` не нужно: такая смета не проходит проверку `offer_has_no_estimate`? — нет, смета есть; для неё ставка читается запросом по `Proposal.vat_rate` через `Lot.estimate_id`, тем же правилом единогласия, что `_vat_rate` | VIEW не даёт строк у сметы без позиций; правило единогласия уже записано в паспорте и повторяется одним `select` |
| **Р5** | Проценты квантуются `Decimal("0.1")` с `ROUND_HALF_UP`, деньги — `quantize_money`; `bar_height_pct` — `Decimal("0.1")` | §2.12: округление на выходе; половина — вверх, как у `roundDecimalPercent` на клиенте |
| **Р6** | Плитка — `Toggle` shadcn с `aria-pressed`, иконка `Check` из lucide абсолютом в углу; состояние выбора — `useState<Set<number>>` в `TenderCardPage`, передаётся в `OfferGrid` пропами `selectedOfferIds`, `onToggleOffer`, `onSelectParticipant` | спека §2.1; выбор локален для карточки |
| **Р7** | URL свода — `?offers=27&offers=29` (повторяющийся параметр), читается `searchParams.getAll("offers")`; FastAPI — `offers: list[int] = Query(...)` | спека §2.2; `getAll` уже в react-router, у FastAPI `list[int]` в Query — штатно |
| **Р8** | Хук `useStageSummary(tenderId, offerIds)` с ключом `qk.tenders.stageSummary(tenderId, sortedIds)`; `enabled` при `offerIds.length >= 1` — отказ `too_few_offers` приходит с сервера и рендерится как пустое состояние, а не гасится клиентом | пустые состояния по кодам отказа (§2.14) обязаны быть достижимы с сервера |
| **Р9** | Сортировка по `sort_order` `WorkCategory` как вторичный ключ: поле существует, числовые сегменты кода не нужны | проверено `grep` — `WorkCategory.sort_order`, `nullable=False` |
| **Р10** | Тестовые сметы предложений в интеграции строятся `import_round` с `round_payload` (образец `_grid` в `test_tenders_crud.py`), статьи — через `article_smr` у строк-разделов | единственный путь, которым `work_category_id` появляется у строки; фабрики `PositionItemFactory` статью не ставят |

---

## Проверенные символы

Проверено `grep`-ом по дереву 27.08.2026 (`AGENTS.md` §9.1). Существующее —
брать как есть; помеченное «заводится» создаётся названной задачей.

| Символ | Где | Статус |
|---|---|---|
| `CATEGORY_TOTALS` (колонки `estimate_id, proposal_id, vat_rate_base, work_category_id, source, amount, row_count, rows_with_amount, rows_not_finite`) | `backend/crud/project_passport.py` | существует, импортируется |
| `CategoryRef(id, code, title, parent_id, is_bucket, sort_order)`, `DirectTotals(amount, row_count, rows_with_amount, rows_not_finite)`, `CategoryNode(ref, total, rows, rows_priced, rows_not_finite, own, …, children)`, `build_tree(categories, direct)`, `SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS` | `backend/services/category_rollup.py` | существует |
| `estimate_total_including_vat(db, estimate_id)` | `backend/crud/estimate_totals.py` | существует (по одной смете; вызывается по разу на колонку — это N вызовов при N колонках, см. Р2 и Task 3: заменяется одним запросом `estimate_totals_including_vat(db, estimate_ids)` — **заводится Task 3** рядом, старая функция не меняется) |
| `gross_to_net(gross, base)`, `quantize_money(value)`, `_VAT_CONTEXT` | `backend/money/vat.py` | существует |
| `_DIV_CONTEXT`, `ARITHMETIC_PRECISION` | `backend/crud/comparison.py`, `backend/parser/summary_block.py` | существует; в новом модуле контекст объявляется своим `Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])` — импортировать приватный `_DIV_CONTEXT` из `comparison` нельзя |
| `DomainError(status_code, detail, code=None, context=None)`, `iso` | `backend/crud/common.py` | существует |
| `raise_domain_error` | `backend/routers/domain_errors.py` | существует |
| `decimal_json` | `backend/responses.py` | существует |
| `router` (`/api/v1/tenders`), `get_tender`, `crud_tenders` | `backend/routers/tenders.py` | существует, дополняется Task 5 |
| `get_tender`, `get_tender_card` | `backend/crud/tenders.py` | существует |
| `Tender`, `TenderRound(stage_no, label, held_on)`, `OfferPackage(tender_id, contractor_id)`, `Offer(tender_id, round_id, package_id)`, `Estimate(offer_id, round_id, contract_id)`, `EstimateCategoryOverride(position_item_id, work_category_id, assigned_at, assigned_by)`, `WorkCategory(id, code, title, parent_id, is_bucket, sort_order)`, `Lot`, `Proposal(vat_rate)`, `PositionItem(is_chapter)`, `Contractor` | `backend/models.py` | существует |
| `TenderFactory`, `TenderRoundFactory`, `OfferPackageFactory`, `OfferFactory`, `EstimateFactory`, `LotFactory`, `ProposalFactory`, `PositionItemFactory`, `ContractorFactory`, `UserFactory` | `backend/tests/factories.py` | существует |
| `db_session`, `factories`, `client` | `backend/tests/conftest.py` | существует |
| `member_client`, `admin_client`, `admin_user`, `category_id` | `backend/tests/integration/conftest.py` | существует |
| `round_payload`, `proposal(positions, title=, inn=, vat_rate=, additional_works=)`, `position(job_title=, is_chapter=, chapter_number=, chapter_ref=, article_smr=, unit_cost_total=, total_cost_total=, number=)`, `additional_works_row`, `summary_line` | `backend/tests/payloads.py` | существует |
| `import_round(db, tender_round=, data=, parser_version=, import_job_id=, replace=, unit_resolver=, category_resolver=)` | `backend/services/round_import.py` | существует |
| `UnitResolver`, `CategoryResolver.from_db` | `backend/services/unit_resolution.py`, `category_resolution.py` | существует |
| `set_override(db, estimate_id=, position_item_id=, work_category_id=, user=…)` — точная сигнатура читается в `services/category_override.py:129` исполнителем Task 4 | `backend/services/category_override.py` | существует |
| `_count_queries` | `backend/tests/integration/test_dashboard_api.py` | существует, образец (копируется в тест Task 4 — общий хелпер не заводится) |
| `_grid`, `P_A`, `P_B` | `backend/tests/integration/test_tenders_crud.py` | существует, образец |
| `tendersApi`, `api` | `frontend/src/services/api/domain.ts`, `frontend/src/lib/api.ts` | существует, дополняется Task 6 |
| `qk.tenders.{all, list, card, roundJobs}` | `frontend/src/services/queryKeys.ts` | существует, дополняется Task 6 |
| `useTender`, `apiErrorCode`, `apiErrorContext`, `apiErrorStatus` | `frontend/src/services/queries.ts` | существует |
| `TenderCard`, `TenderCell`, `TenderParticipant`, `TenderRoundRow`, `Decimal` (= `string`) | `frontend/src/types/domain.ts` | существует |
| `OfferGrid({card, selectedRoundId, onSelectRound})` | `frontend/src/components/tenders/OfferGrid.tsx` | существует, правится Task 7 |
| `TenderCardPage` | `frontend/src/pages/tenders/TenderCardPage.tsx` | существует, правится Task 7 |
| `Toggle` | `frontend/src/components/ui/toggle.tsx` | существует |
| `Tooltip`, `TooltipTrigger`, `TooltipContent` | `frontend/src/components/ui/tooltip.tsx` | существует |
| `Surface`, `EmptyState`, `PageHeader`, `Breadcrumbs`, `StatusPill`, `KpiCard`, `Skeleton` | `frontend/src/components/ui-domain/` | существует |
| `formatDecimalMoney`, `roundDecimalPercent`, `formatDate` | `frontend/src/lib/format.ts` | существует |
| `cn` | `frontend/src/lib/utils.ts` | существует |
| `renderWithProviders`, `server`, `handlerState`, `resetHandlerState`, `sampleTenderCard` | `frontend/src/test/utils.tsx`, `server.ts`, `handlers.ts`, `fixtures.ts` | существует, дополняется |
| `Check` | `lucide-react` | существует |
| `services/stage_summary.py` — все имена Р1 | — | **заводится Task 1–2** |
| `crud/stage_summary.py`: `load_inputs`, `build_stage_summary`; `crud/estimate_totals.estimate_totals_including_vat` | — | **заводится Task 3** |
| `routers/tenders.py::stage_summary` | — | **заводится Task 5** |
| `StageSummary`, `StageSummaryColumn`, `StageSummaryRow`, `StageSummaryCell`, `StageSummaryChange` (типы), `tendersApi.stageSummary`, `qk.tenders.stageSummary`, `useStageSummary`, `sampleStageSummary`, `handlerState.stageSummaryOutcome` | frontend | **заводится Task 6** |
| `StageSummaryPage`, `StageSummaryTable`, `StageSummaryTrack`, `SummaryCell`, `cellCopy.ts` | `frontend/src/pages/tenders/summary/` | **заводится Task 8** |
| `docs/devlog/2026-08-27-stage-summary.md` | — | **заводится Task 9** |

---

## Структура файлов

**Создаётся**

| Файл | Ответственность |
|---|---|
| `backend/services/stage_summary.py` | Чистый расчёт: состояния, матрица, проценты, ось, сортировка, видимость, сходимость, KPI, трасса — из литералов в литералы |
| `backend/crud/stage_summary.py` | Проверка выбора, чтение входов постоянным числом запросов, сборка ответа §2.16 |
| `backend/tests/unit/test_stage_summary.py` | Табличные тесты чистого модуля, шпион на делении |
| `backend/tests/integration/test_stage_summary_api.py` | Отказы (снятием защиты), ветви VIEW и разнос, ось НДС трёх случаев, сходимость, KPI, порядок колонок, счётчик запросов, права |
| `frontend/src/pages/tenders/summary/StageSummaryPage.tsx` (+test) | Страница: чтение URL, хук, шапка, оси, пустые состояния |
| `frontend/src/pages/tenders/summary/StageSummaryTrack.tsx` | Трасса торга блоками по `bar_height_pct` |
| `frontend/src/pages/tenders/summary/StageSummaryTable.tsx` (+test) | Таблица: дерево, раскрытие, правые колонки, «Нераспределённое», «Итого» |
| `frontend/src/pages/tenders/summary/SummaryCell.tsx` | Одна ячейка по `state`/`change`/`rows`/`amount_unavailable_reason` |
| `frontend/src/pages/tenders/summary/cellCopy.ts` | Подписи состояний, видов изменения, причин — константы вне файла с компонентом (eslint `only-export-components`) |
| `frontend/src/pages/tenders/summary/summaryTokens.test.ts` | Каждая CSS-переменная компонентов свода существует в `index.css` |
| `docs/devlog/2026-08-27-stage-summary.md` | Отступления, замеры, границы |

**Правится**

| Файл | Что именно |
|---|---|
| `backend/crud/estimate_totals.py` | `estimate_totals_including_vat(db, estimate_ids) -> dict[int, Decimal \| None]` — одно чтение на список; старая функция не трогается |
| `backend/routers/tenders.py` | `GET /{tender_id}/stage-summary` |
| `frontend/src/types/domain.ts`, `services/api/domain.ts`, `services/queryKeys.ts`, `services/queries.ts` | типы §2.16, API, ключ, хук |
| `frontend/src/test/fixtures.ts`, `handlers.ts` | `sampleStageSummary`, хендлер с исходами |
| `frontend/src/components/tenders/OfferGrid.tsx` | плитки-переключатели, клик по имени участника, недоступность чужих строк |
| `frontend/src/pages/tenders/TenderCardPage.tsx` (+test) | состояние выбора, счётчик, кнопка «Свод по этапам (N)» |
| `frontend/src/App.tsx` | маршрут `/tenders/:tenderId/summary` |
| `docs/proposals/2026-08-25-tenders-model.md` | ссылка на план (после гейта 3), devlog и PR (Task 9) |

**Не трогать:** `backend/parser/**`, `backend/crud/project_passport.py`,
`analytics.py`, `comparison.py`, `services/category_*.py`, `round_import.py`,
`estimate_import.py`, `backend/models.py`, `alembic/`, `backend/tests/conftest.py`.

---
## Task 1: Чистый расчёт — состояния ячейки, матрица переходов, процент, вклад

**Files:**
- Create: `backend/services/stage_summary.py`
- Test: `backend/tests/unit/test_stage_summary.py`

**Interfaces:**
- Consumes: `Decimal`, `Context` из stdlib; `ARITHMETIC_PRECISION` из `parser/summary_block.py`.
- Produces (для Task 2–3):

```python
# services/stage_summary.py — контракт этой задачи
STATE_AMOUNT = "amount"; STATE_REMOVED = "removed"; STATE_NOT_EVALUATED = "not_evaluated"; STATE_ABSENT = "absent"
KIND_PERCENT = "percent"; KIND_ABS_ONLY = "abs_only"; KIND_APPEARED = "appeared"; KIND_REAPPEARED = "reappeared"
KIND_REMOVED = "removed"; KIND_DISAPPEARED = "disappeared"; KIND_NONE = "none"
DIR_UP = "up"; DIR_DOWN = "down"; DIR_FLAT = "flat"
REASON_FIRST_COLUMN = "first_column"; REASON_UNKNOWN_VAT_BASE = "unknown_vat_base"
REASON_NO_AMOUNTS = "no_amounts"; REASON_UNALLOCATED = "unallocated"; REASON_ABSENT_ENDPOINT = "absent_endpoint"

@dataclass(frozen=True)
class CellInput:            # одна пара (колонка, статья) ДО расчёта состояний
    gross: Decimal | None   # сумма обеих ветвей в исходных валовых деньгах; None ⇔ row_count == 0
    additional_works_gross: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int

@dataclass(frozen=True)
class Change:
    kind: str
    value: Decimal | None     # НЕквантованное; квантует сборка ответа
    direction: str | None
    reason: str | None

@dataclass(frozen=True)
class Contribution:
    value: Decimal | None
    direction: str | None
    reason: str | None

def cell_states(gross_by_column: Sequence[Decimal | None]) -> list[str]
def direction_of(delta: Decimal) -> str
def change_between(prev_state: str, cur_state: str, prev_shown: Decimal | None, cur_shown: Decimal | None,
                   *, unavailable_reason: str | None) -> Change
def contribution_between(first_state: str, last_state: str, first_shown: Decimal | None, last_shown: Decimal | None,
                         *, unavailable_reason: str | None) -> Contribution
def percent_change(start: Decimal, end: Decimal) -> Decimal   # ТОЛЬКО при start > 0; иначе AssertionError
```

Семантика — спека §2.5 (состояния), §2.6 (матрица, база процента, направление),
§2.7 (вклад). `shown` — сумма на оси показа (валовая либо нетто, решает Task 2);
`unavailable_reason` ≠ `None` — колонка `unknown_vat_base`, и тогда `Change`/`Contribution`
— `none`/`null` с этой причиной независимо от состояний.

- [ ] **Step 1: Тесты состояний и матрицы (падают: модуля нет)**

```python
# backend/tests/unit/test_stage_summary.py
"""Чистый расчёт свода по этапам без БД (спека §2.5–§2.7).

Каждая клетка матрицы §2.6 — отдельный случай; `absent`, `not_evaluated` и
`removed` — три разных факта, и тесты не сводят их к «пусто/есть».
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from services import stage_summary as ss

D = Decimal


class TestCellStates:
    def test_nonzero_is_amount_and_zero_after_amount_is_removed(self):
        assert ss.cell_states([D("10"), D("0")]) == [ss.STATE_AMOUNT, ss.STATE_REMOVED]

    def test_zero_never_priced_before_is_not_evaluated(self):
        assert ss.cell_states([D("0"), D("0")]) == [ss.STATE_NOT_EVALUATED, ss.STATE_NOT_EVALUATED]

    def test_second_zero_in_a_row_stays_removed_not_not_evaluated(self):
        """«По предыдущему шагу» пометило бы вторую нулевую как «не оценивалась» — §2.5."""
        assert ss.cell_states([D("10"), D("0"), D("0")]) == [ss.STATE_AMOUNT, ss.STATE_REMOVED, ss.STATE_REMOVED]

    def test_none_is_absent_and_does_not_count_as_priced(self):
        assert ss.cell_states([None, D("0"), D("5")]) == [ss.STATE_ABSENT, ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT]

    def test_negative_sum_is_amount(self):
        assert ss.cell_states([D("-3")]) == [ss.STATE_AMOUNT]

    def test_path_is_only_the_given_columns(self):
        """Путь = выбранные колонки (§2.6): история невыбранных сюда не попадает по построению."""
        assert ss.cell_states([D("0"), D("7")]) == [ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT]


class TestChangeMatrix:
    def c(self, ps, cs, pv, cv, reason=None):
        return ss.change_between(ps, cs, pv, cv, unavailable_reason=reason)

    def test_amount_to_amount_positive_base_is_percent(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("200"), D("190"))
        assert ch.kind == ss.KIND_PERCENT and ch.value == D("-5") and ch.direction == ss.DIR_DOWN

    def test_amount_to_amount_negative_base_is_abs_only(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-100"), D("-80"))
        assert ch.kind == ss.KIND_ABS_ONLY and ch.value == D("20") and ch.direction == ss.DIR_UP

    def test_amount_to_amount_negative_base_downward(self):
        """Оба знака у величины, чей знак участвует в решении (AGENTS §11)."""
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-100"), D("-120"))
        assert ch.kind == ss.KIND_ABS_ONLY and ch.value == D("-20") and ch.direction == ss.DIR_DOWN

    def test_equal_amounts_are_flat_not_up(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("50"), D("50"))
        assert ch.kind == ss.KIND_PERCENT and ch.value == D("0") and ch.direction == ss.DIR_FLAT

    def test_amount_to_removed(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_REMOVED, D("50"), D("0"))
        assert ch.kind == ss.KIND_REMOVED and ch.value is None and ch.direction is None

    def test_amount_to_absent_is_disappeared_not_removed(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_ABSENT, D("50"), None)
        assert ch.kind == ss.KIND_DISAPPEARED

    def test_not_evaluated_to_amount_and_absent_to_amount_are_appeared(self):
        assert self.c(ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT, D("0"), D("5")).kind == ss.KIND_APPEARED
        assert self.c(ss.STATE_ABSENT, ss.STATE_AMOUNT, None, D("5")).kind == ss.KIND_APPEARED

    def test_removed_to_amount_is_reappeared(self):
        assert self.c(ss.STATE_REMOVED, ss.STATE_AMOUNT, D("0"), D("5")).kind == ss.KIND_REAPPEARED

    @pytest.mark.parametrize("ps,cs", [
        (ss.STATE_REMOVED, ss.STATE_REMOVED),
        (ss.STATE_ABSENT, ss.STATE_NOT_EVALUATED),
        (ss.STATE_REMOVED, ss.STATE_ABSENT),
        (ss.STATE_NOT_EVALUATED, ss.STATE_NOT_EVALUATED),
    ])
    def test_transitions_without_amounts_are_none_with_reason(self, ps, cs):
        ch = self.c(ps, cs, None, None)
        assert ch.kind == ss.KIND_NONE and ch.value is None and ch.direction is None
        assert ch.reason == ss.REASON_NO_AMOUNTS

    def test_unknown_vat_base_wins_over_states(self):
        ch = self.c(ss.STATE_AMOUNT, ss.STATE_AMOUNT, None, None, reason=ss.REASON_UNKNOWN_VAT_BASE)
        assert ch.kind == ss.KIND_NONE and ch.reason == ss.REASON_UNKNOWN_VAT_BASE


class TestDivisionIsUnreachableForNonPositiveBase:
    def test_percent_change_is_not_called_when_base_is_zero_or_negative(self):
        """Утверждение о ПОТОКЕ (docs/insights/data-flow-assertions-for-order.md):
        при базе ≤ 0 деление не вызывается вовсе — не «не падает», а не зовётся."""
        with patch.object(ss, "percent_change", wraps=ss.percent_change) as spy:
            ss.change_between(ss.STATE_NOT_EVALUATED, ss.STATE_AMOUNT, D("0"), D("5"), unavailable_reason=None)
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("-1"), D("5"), unavailable_reason=None)
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("0"), D("5"), unavailable_reason=None)
        assert spy.call_count == 0

    def test_percent_change_is_called_exactly_once_for_positive_base(self):
        with patch.object(ss, "percent_change", wraps=ss.percent_change) as spy:
            ss.change_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("10"), D("5"), unavailable_reason=None)
        assert spy.call_count == 1

    def test_percent_change_refuses_non_positive_base(self):
        with pytest.raises(AssertionError):
            ss.percent_change(D("0"), D("5"))


class TestContribution:
    def test_number_when_both_ends_have_amounts(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, D("100"), D("80"), unavailable_reason=None)
        assert c.value == D("-20") and c.direction == ss.DIR_DOWN and c.reason is None

    def test_zero_states_count_as_zero_money(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_REMOVED, D("100"), D("0"), unavailable_reason=None)
        assert c.value == D("-100")

    def test_absent_endpoint_gives_null_with_reason(self):
        c = ss.contribution_between(ss.STATE_ABSENT, ss.STATE_AMOUNT, None, D("5"), unavailable_reason=None)
        assert c.value is None and c.direction is None and c.reason == ss.REASON_ABSENT_ENDPOINT

    def test_unknown_vat_base_gives_null_with_reason(self):
        c = ss.contribution_between(ss.STATE_AMOUNT, ss.STATE_AMOUNT, None, None,
                                    unavailable_reason=ss.REASON_UNKNOWN_VAT_BASE)
        assert c.value is None and c.reason == ss.REASON_UNKNOWN_VAT_BASE
```

- [ ] **Step 2: Прогнать — убедиться, что падает на импорте**

Run: `cd backend && uv run pytest tests/unit/test_stage_summary.py -q`
Expected: `ImportError`/`ModuleNotFoundError: services.stage_summary`.

- [ ] **Step 3: Минимальная реализация**

```python
# backend/services/stage_summary.py
"""Свод по этапам одного участника — чистый расчёт (спека
2026-08-27-stage-summary-design.md §2.5–§2.13, §2.16).

Модуль чистый: ни Session, ни ORM, ни импортов из `crud` — литералы на входе,
литералы на выходе; чтение БД и сборка JSON — `crud/stage_summary.py`.
Деление — только `percent_change`, и оно достижимо ТОЛЬКО при базе > 0 (§2.6):
`DivisionByZero` здесь — дефект расчёта, а не отказ пользователю.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from parser.summary_block import ARITHMETIC_PRECISION

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
    assert start > 0, "percent_change достижим только при положительной базе (§2.6)"
    with localcontext(_DIV_CONTEXT):
        return (end / start - 1) * _HUNDRED


def change_between(prev_state: str, cur_state: str, prev_shown: Decimal | None, cur_shown: Decimal | None,
                   *, unavailable_reason: str | None) -> Change:
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
```

- [ ] **Step 4: Прогнать — все зелёные**

Run: `cd backend && uv run pytest tests/unit/test_stage_summary.py -q`
Expected: все PASS.

- [ ] **Step 5: Снятие защиты — доказать, что тест о потоке живой**

Временно заменить в `change_between` условие `if prev_shown > 0:` на `if prev_shown >= 0:` и прогнать `TestDivisionIsUnreachableForNonPositiveBase` — ожидается **красный** (`percent_change` вызван на нулевой базе → `AssertionError` внутри и/или `call_count == 1`). Вернуть условие. Записать исход в отчёт задачи.

- [ ] **Step 6: ruff и коммит**

```bash
cd backend && uv run ruff check services/stage_summary.py tests/unit/test_stage_summary.py
git add backend/services/stage_summary.py backend/tests/unit/test_stage_summary.py
git commit -m "feat(stage-summary): чистый расчёт — состояния ячейки, матрица переходов, процент, вклад"
```

---

## Task 2: Чистый расчёт — ось НДС, сходимость, дерево, сортировка, KPI, трасса, сборка

**Files:**
- Modify: `backend/services/stage_summary.py`
- Test: `backend/tests/unit/test_stage_summary.py` (дописать)

**Interfaces:**
- Consumes: Task 1; `CategoryRef`, `DirectTotals`, `build_tree`, `SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS` из `services/category_rollup.py`; `gross_to_net` из `money/vat.py`.
- Produces (для Task 3):

```python
TAX_GROSS = "gross"; TAX_NET = "net"; TAX_NONE = "none"
TAX_REASON_SINGLE = "single_rate"; TAX_REASON_MIXED = "mixed_rates"; TAX_REASON_NO_KNOWN = "no_known_rates"
VAT_KNOWN = "known"; VAT_UNKNOWN = "unknown_vat_base"
TRACK_NON_POSITIVE = "non_positive_total"; TRACK_NO_COMPARABLE = "no_comparable_totals"
CONV_FILE_TOTAL_UNAVAILABLE = "file_total_unavailable"

@dataclass(frozen=True)
class ColumnInput:
    offer_id: int; estimate_id: int; round_id: int; stage_no: int; label: str | None; held_on: date | None
    vat_rate_base: Decimal | None                      # None ⇔ unknown_vat_base
    direct: Mapping[int | None, Mapping[str, DirectTotals]]   # как в `_direct_totals`, ВАЛОВЫЕ; ключ None — Нераспределённое
    file_total_gross: Decimal | None                    # estimate_total_including_vat
    overrides_count: int; overrides_last_at: datetime | None

@dataclass(frozen=True)
class TaxBasis:
    basis: str; reason: str; rates_by_column: list[Decimal | None] | None

def pick_tax_basis(rates: Sequence[Decimal | None]) -> TaxBasis
def to_shown(gross: Decimal | None, rate: Decimal | None, basis: TaxBasis) -> Decimal | None   # None при unknown или basis none
def sort_key(contribution: Contribution, sort_order: int) -> tuple
def compute_summary(columns: Sequence[ColumnInput], categories: Sequence[CategoryRef]) -> SummaryResult
```

`SummaryResult` — dataclass с полями ровно по спеке §2.16 (`columns: list[ColumnOut]`,
`rows: list[SummaryRow]`, `unallocated: SummaryRow`, `total_cells: list[Cell]`,
`display: TaxBasis`, `kpi: Kpi`, `track: Track`), все числа — `Decimal` без квантования;
`Cell(state, shown, unavailable_reason, additional_works_shown, rows: CellInput, change: Change)`,
`SummaryRow(ref: CategoryRef | None, is_unallocated, cells, bargain: Change, contribution, children)`,
`ColumnOut(input: ColumnInput, vat_state, total_shown, total_change, bar_height_pct, convergence: Convergence)`,
`Convergence(categories_sum_gross, file_total_gross, converged: bool | None, delta, reason)`,
`Kpi(stages_selected, categories_with_amount, categories_total, first_to_last: Change)` — `last_stage_positions`
и `stages_loaded` считает `crud` (нужна БД), `Track(available, reason)`.

Семантика: §2.8 (три случая по множеству известных ставок), §2.9 (сходимость всегда
в валовых; итог колонки = Σ корней + Нераспределённое на оси показа), §2.11 (KPI,
`bar_height_pct` = `total_shown / max_comparable × 100`, `track.available`),
§2.13 (сортировка внутри уровня по `|contribution|` убыв., затем `sort_order`;
`value is None` — после числовых), §2.14 (видимость детей).

- [ ] **Step 1: Тесты (падают: имён нет)**

```python
# дописать в backend/tests/unit/test_stage_summary.py
import datetime as dt

from services.category_rollup import SOURCE_ADDITIONAL_WORKS, SOURCE_POSITIONS, CategoryRef, DirectTotals


def ref(id_, code, parent_id=None, sort_order=0):
    return CategoryRef(id=id_, code=code, title=f"Статья {code}", parent_id=parent_id, is_bucket=False, sort_order=sort_order)


def dt_(amount, rows=1):
    return DirectTotals(amount=None if amount is None else D(amount), row_count=rows,
                        rows_with_amount=rows if amount is not None else 0, rows_not_finite=0)


def col(offer_id, stage_no, rate, direct, file_total=None, overrides=0):
    return ss.ColumnInput(offer_id=offer_id, estimate_id=offer_id * 10, round_id=stage_no, stage_no=stage_no,
                          label=None, held_on=None, vat_rate_base=None if rate is None else D(rate), direct=direct,
                          file_total_gross=None if file_total is None else D(file_total),
                          overrides_count=overrides, overrides_last_at=None)


CATS = [ref(6, "6", sort_order=6), ref(2, "2", sort_order=2), ref(26, "2.6", parent_id=2, sort_order=1)]


class TestTaxBasis:
    def test_single_known_rate_is_gross(self):
        tb = ss.pick_tax_basis([D("20"), D("20"), None])
        assert tb.basis == ss.TAX_GROSS and tb.reason == ss.TAX_REASON_SINGLE and tb.rates_by_column is None

    def test_mixed_known_rates_is_net_with_rates_listed(self):
        tb = ss.pick_tax_basis([D("20"), D("0")])
        assert tb.basis == ss.TAX_NET and tb.reason == ss.TAX_REASON_MIXED and tb.rates_by_column == [D("20"), D("0")]

    def test_no_known_rates_is_none(self):
        tb = ss.pick_tax_basis([None, None])
        assert tb.basis == ss.TAX_NONE and tb.reason == ss.TAX_REASON_NO_KNOWN

    def test_unknown_column_is_unavailable_on_gross_axis(self):
        """20 % + неизвестная: известная валовая, неизвестная — без суммы (§2.8)."""
        tb = ss.pick_tax_basis([D("20"), None])
        assert tb.basis == ss.TAX_GROSS
        assert ss.to_shown(D("120"), D("20"), tb) == D("120")
        assert ss.to_shown(D("120"), None, tb) is None

    def test_net_axis_divides_by_own_rate(self):
        tb = ss.pick_tax_basis([D("20"), D("0")])
        assert ss.to_shown(D("120"), D("20"), tb) == D("100")
        assert ss.to_shown(D("100"), D("0"), tb) == D("100")


class TestComputeSummary:
    def two_columns(self):
        c1 = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("120")}, 2: {SOURCE_POSITIONS: dt_("60")},
                              26: {SOURCE_POSITIONS: dt_("12")}, None: {SOURCE_POSITIONS: dt_("6")}}, file_total="198")
        c2 = col(2, 2, "20", {6: {SOURCE_POSITIONS: dt_("96"), SOURCE_ADDITIONAL_WORKS: dt_("24")},
                              2: {SOURCE_POSITIONS: dt_("0")}, None: {SOURCE_POSITIONS: dt_("0", rows=0)}}, file_total="130")
        return [c1, c2]

    def test_total_is_sum_of_roots_plus_unallocated_not_file_total(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.columns[0].total_shown == D("198")      # 120 + 72 (60+12) + 6
        assert r.columns[1].total_shown == D("120")      # 120 + 0 + 0; файл говорит 130 → не сходится
        assert r.columns[1].convergence.converged is False and r.columns[1].convergence.delta == D("-10")
        assert r.columns[0].convergence.converged is True

    def test_convergence_null_when_file_total_missing(self):
        cols = self.two_columns()
        cols[0] = ss.ColumnInput(**{**cols[0].__dict__, "file_total_gross": None})
        r = ss.compute_summary(cols, CATS)
        assert r.columns[0].convergence.converged is None
        assert r.columns[0].convergence.reason == ss.CONV_FILE_TOTAL_UNAVAILABLE

    def test_convergence_is_computed_in_gross_even_on_net_axis(self):
        cols = self.two_columns()
        cols[1] = ss.ColumnInput(**{**cols[1].__dict__, "vat_rate_base": D("0")})
        r = ss.compute_summary(cols, CATS)
        assert r.display.basis == ss.TAX_NET
        assert r.columns[0].total_shown == D("165")      # 198 / 1.2
        assert r.columns[0].convergence.categories_sum_gross == D("198") and r.columns[0].convergence.converged is True

    def test_additional_works_amount_independent_of_state(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("-24"), SOURCE_ADDITIONAL_WORKS: dt_("24")}})
        r = ss.compute_summary([c, c], CATS)
        row6 = next(row for row in r.rows if row.ref.id == 6)
        cell = row6.cells[0]
        assert cell.state == ss.STATE_NOT_EVALUATED and cell.shown is None
        assert cell.additional_works_shown == D("24")

    def test_rows_sorted_by_abs_contribution_within_level_then_sort_order(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert [row.ref.code for row in r.rows] == ["2", "6"]          # |−72| > |0|
        assert r.rows[0].contribution.value == D("-72") and r.rows[1].contribution.value == D("0")

    def test_roots_always_present_children_only_with_nonzero_somewhere(self):
        cats = CATS + [ref(7, "7", sort_order=7), ref(27, "2.7", parent_id=2, sort_order=2)]
        r = ss.compute_summary(self.two_columns(), cats)
        assert {row.ref.code for row in r.rows} == {"2", "6", "7"}
        two = next(row for row in r.rows if row.ref.code == "2")
        assert [c.ref.code for c in two.children] == ["2.6"]        # 2.7 без строк — скрыт

    def test_child_with_explicit_zero_everywhere_is_hidden(self):
        c = col(1, 1, "20", {2: {SOURCE_POSITIONS: dt_("10")}, 26: {SOURCE_POSITIONS: dt_("0")}})
        r = ss.compute_summary([c, c], CATS)
        two = next(row for row in r.rows if row.ref.code == "2")
        assert two.children == []

    def test_unallocated_row_bargain_has_no_percent_but_contribution_is_number(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.unallocated.is_unallocated
        assert r.unallocated.bargain.kind == ss.KIND_NONE and r.unallocated.bargain.reason == ss.REASON_UNALLOCATED
        assert r.unallocated.contribution.value == D("-6")

    def test_kpi_counts_nonzero_roots_of_last_column_both_signs(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("5")}, 2: {SOURCE_POSITIONS: dt_("-5")}})
        r = ss.compute_summary([c, c], CATS)
        assert r.kpi.categories_with_amount == 2 and r.kpi.categories_total == 2

    def test_track_heights_are_server_side_and_max_is_100(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.track.available is True
        assert r.columns[0].bar_height_pct == D("100")
        assert r.columns[1].bar_height_pct == D("120") / D("198") * 100

    def test_track_unavailable_on_non_positive_total(self):
        c = col(1, 1, "20", {6: {SOURCE_POSITIONS: dt_("-5")}})
        r = ss.compute_summary([c, c], CATS)
        assert r.track.available is False and r.track.reason == ss.TRACK_NON_POSITIVE

    def test_unknown_column_does_not_disable_track_but_has_no_bar(self):
        cols = self.two_columns()
        cols[1] = ss.ColumnInput(**{**cols[1].__dict__, "vat_rate_base": None})
        r = ss.compute_summary(cols, CATS)
        assert r.track.available is True
        assert r.columns[1].bar_height_pct is None and r.columns[1].vat_state == ss.VAT_UNKNOWN
        assert r.columns[1].total_shown is None
        assert all(cell.unavailable_reason == ss.REASON_UNKNOWN_VAT_BASE for cell in r.rows[0].cells[1:2])
        assert r.columns[1].total_change.kind == ss.KIND_NONE

    def test_all_unknown_means_basis_none_and_no_comparable_totals(self):
        cols = [ss.ColumnInput(**{**c.__dict__, "vat_rate_base": None}) for c in self.two_columns()]
        r = ss.compute_summary(cols, CATS)
        assert r.display.basis == ss.TAX_NONE
        assert r.track.available is False and r.track.reason == ss.TRACK_NO_COMPARABLE
        assert r.rows[0].cells[0].state in (ss.STATE_AMOUNT, ss.STATE_NOT_EVALUATED)   # состояния на месте

    def test_first_column_change_is_none_with_first_column_reason(self):
        r = ss.compute_summary(self.two_columns(), CATS)
        assert r.rows[0].cells[0].change.kind == ss.KIND_NONE
        assert r.rows[0].cells[0].change.reason == ss.REASON_FIRST_COLUMN
```

- [ ] **Step 2: Прогнать — падает на `AttributeError` (`pick_tax_basis`)**

Run: `cd backend && uv run pytest tests/unit/test_stage_summary.py -q`

- [ ] **Step 3: Реализация**

```python
# дописать в backend/services/stage_summary.py
import datetime as dt
from collections.abc import Mapping

from money.vat import gross_to_net
from services.category_rollup import CategoryNode, CategoryRef, DirectTotals, SOURCE_ADDITIONAL_WORKS, build_tree

TAX_GROSS = "gross"; TAX_NET = "net"; TAX_NONE = "none"
TAX_REASON_SINGLE = "single_rate"; TAX_REASON_MIXED = "mixed_rates"; TAX_REASON_NO_KNOWN = "no_known_rates"
VAT_KNOWN = "known"; VAT_UNKNOWN = "unknown_vat_base"
TRACK_NON_POSITIVE = "non_positive_total"; TRACK_NO_COMPARABLE = "no_comparable_totals"
CONV_FILE_TOTAL_UNAVAILABLE = "file_total_unavailable"


@dataclass(frozen=True)
class ColumnInput:
    offer_id: int; estimate_id: int; round_id: int; stage_no: int
    label: str | None; held_on: dt.date | None
    vat_rate_base: Decimal | None
    direct: Mapping[int | None, Mapping[str, DirectTotals]]
    file_total_gross: Decimal | None
    overrides_count: int
    overrides_last_at: dt.datetime | None


@dataclass(frozen=True)
class TaxBasis:
    basis: str; reason: str; rates_by_column: list[Decimal | None] | None


def pick_tax_basis(rates: Sequence[Decimal | None]) -> TaxBasis:
    known = {r for r in rates if r is not None}
    if not known:
        return TaxBasis(TAX_NONE, TAX_REASON_NO_KNOWN, None)
    if len(known) == 1:
        return TaxBasis(TAX_GROSS, TAX_REASON_SINGLE, None)
    return TaxBasis(TAX_NET, TAX_REASON_MIXED, list(rates))


def to_shown(gross: Decimal | None, rate: Decimal | None, basis: TaxBasis) -> Decimal | None:
    if gross is None or rate is None or basis.basis == TAX_NONE:
        return None
    return gross if basis.basis == TAX_GROSS else gross_to_net(gross, rate)


@dataclass(frozen=True)
class Cell:
    state: str; shown: Decimal | None; unavailable_reason: str | None
    additional_works_shown: Decimal | None; rows: CellInput; change: Change


@dataclass(frozen=True)
class SummaryRow:
    ref: CategoryRef | None; is_unallocated: bool; cells: list[Cell]
    bargain: Change; contribution: Contribution; children: list["SummaryRow"]


@dataclass(frozen=True)
class Convergence:
    categories_sum_gross: Decimal; file_total_gross: Decimal | None
    converged: bool | None; delta: Decimal | None; reason: str | None


@dataclass(frozen=True)
class ColumnOut:
    input: ColumnInput; vat_state: str; total_shown: Decimal | None
    total_change: Change; bar_height_pct: Decimal | None; convergence: Convergence


@dataclass(frozen=True)
class Kpi:
    stages_selected: int; categories_with_amount: int; categories_total: int; first_to_last: Change


@dataclass(frozen=True)
class Track:
    available: bool; reason: str | None


@dataclass(frozen=True)
class SummaryResult:
    columns: list[ColumnOut]; rows: list[SummaryRow]; unallocated: SummaryRow
    total_cells: list[Cell]; display: TaxBasis; kpi: Kpi; track: Track


def _node_inputs(node: CategoryNode | None, direct_key: int | None, direct) -> CellInput:
    """Валовые входы одной пары (колонка, статья). Для статьи — из свёрнутого узла
    `build_tree` (поддерево целиком); для Нераспределённого — из `direct[None]`."""
    if node is not None:
        extra = direct.get(node.ref.id, {}).get(SOURCE_ADDITIONAL_WORKS)
        return CellInput(gross=node.total, additional_works_gross=None if extra is None else extra.amount,
                         row_count=node.rows, rows_with_amount=node.rows_priced, rows_not_finite=node.rows_not_finite)
    branches = direct.get(direct_key, {})
    amounts = [b.amount for b in branches.values() if b.amount is not None]
    extra = branches.get(SOURCE_ADDITIONAL_WORKS)
    return CellInput(gross=sum(amounts) if amounts else None,
                     additional_works_gross=None if extra is None else extra.amount,
                     row_count=sum(b.row_count for b in branches.values()),
                     rows_with_amount=sum(b.rows_with_amount for b in branches.values()),
                     rows_not_finite=sum(b.rows_not_finite for b in branches.values()))


def _cells(inputs: list[CellInput], columns: Sequence[ColumnInput], basis: TaxBasis) -> list[Cell]:
    states = cell_states([i.gross for i in inputs])
    cells: list[Cell] = []
    for idx, (inp, column, state) in enumerate(zip(inputs, columns, states, strict=True)):
        reason = REASON_UNKNOWN_VAT_BASE if (column.vat_rate_base is None or basis.basis == TAX_NONE) else None
        shown = None if reason else (to_shown(inp.gross, column.vat_rate_base, basis) if state == STATE_AMOUNT else None)
        extra = None if reason else to_shown(inp.additional_works_gross, column.vat_rate_base, basis)
        if idx == 0:
            change = Change(KIND_NONE, None, None, REASON_FIRST_COLUMN)
        else:
            prev = cells[idx - 1]
            change = change_between(prev.state, state, prev.shown, shown,
                                    unavailable_reason=reason or prev.unavailable_reason)
        cells.append(Cell(state, shown, reason, extra, inp, change))
    return cells


def _endpoints(cells: list[Cell]) -> tuple[Change, Contribution]:
    first, last = cells[0], cells[-1]
    reason = first.unavailable_reason or last.unavailable_reason
    return (change_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason),
            contribution_between(first.state, last.state, first.shown, last.shown, unavailable_reason=reason))


def sort_key(contribution: Contribution, sort_order: int) -> tuple:
    if contribution.value is None:
        return (1, Decimal(0), sort_order)
    return (0, -abs(contribution.value), sort_order)


def _row(ref: CategoryRef, nodes_by_column: list[CategoryNode | None], columns, basis) -> SummaryRow | None:
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
    for idx, column in enumerate(columns):
        gross_parts = [r.cells[idx].rows.gross for r in rows] + [unalloc_cells[idx].rows.gross]
        totals_gross.append(sum(p for p in gross_parts if p is not None) or Decimal(0))
        if column.vat_rate_base is None or basis.basis == TAX_NONE:
            totals_shown.append(None)
        else:
            shown_parts = [r.cells[idx].shown for r in rows] + [unalloc_cells[idx].shown]
            totals_shown.append(sum(p for p in shown_parts if p is not None) or Decimal(0))

    total_inputs = [CellInput(g, None, 0, 0, 0) for g in totals_gross]
    total_cells = _cells(total_inputs, columns, basis)
    # у итога shown — сумма показанных, а не свёртка состояний: подменяем поле
    total_cells = [Cell(c.state, s, c.unavailable_reason, None, c.rows, c.change)
                   for c, s in zip(total_cells, totals_shown, strict=True)]

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

    last = columns_out[-1]
    last_idx = len(columns) - 1
    kpi = Kpi(stages_selected=len(columns),
              categories_with_amount=sum(1 for r in rows if r.cells[last_idx].rows.gross not in (None, Decimal(0))),
              categories_total=len(rows),
              first_to_last=_endpoints(total_cells)[0])
    return SummaryResult(columns_out, rows, unallocated, total_cells, basis, kpi, track)
```

- [ ] **Step 4: Прогнать всё, поправить арифметику до зелёного**

Run: `cd backend && uv run pytest tests/unit/test_stage_summary.py -q`
Expected: PASS. Если `to_shown` на нетто даёт хвост точности (`165.000…`), сравнивать в тесте через `== D("165")` всё равно верно для `Decimal` — равенство числовое.

- [ ] **Step 5: Снятия защиты (два)**

1. В `_row` убрать условие видимости (`return None`) → `test_child_with_explicit_zero_everywhere_is_hidden` красный.
2. В `compute_summary` заменить `totals_gross` на `column.file_total_gross` при расчёте `Convergence.categories_sum_gross` → `test_total_is_sum_of_roots_plus_unallocated_not_file_total` красный.
Вернуть обе строки; исходы — в отчёт.

- [ ] **Step 6: ruff и коммит**

```bash
cd backend && uv run ruff check services/stage_summary.py tests/unit/test_stage_summary.py
git add backend/services/stage_summary.py backend/tests/unit/test_stage_summary.py
git commit -m "feat(stage-summary): ось НДС, сходимость, дерево и сортировка, KPI и трасса — чистая сборка"
```

---
## Task 3: `crud/stage_summary.py` — проверка выбора и чтение входов постоянным числом запросов

**Files:**
- Modify: `backend/crud/estimate_totals.py` (добавить `estimate_totals_including_vat`)
- Create: `backend/crud/stage_summary.py`
- Test: `backend/tests/integration/test_stage_summary_api.py` (первая часть: фикстуры, отказы, порядок, счётчик запросов)

**Interfaces:**
- Consumes: `compute_summary`, `ColumnInput`, константы Task 2; `CATEGORY_TOTALS` из `crud/project_passport.py`; `build_tree`-совместимый `direct`; `DomainError`; модели.
- Produces:

```python
# crud/estimate_totals.py
def estimate_totals_including_vat(db: Session, estimate_ids: Sequence[int]) -> dict[int, Decimal | None]
    # то же правило единогласия, что estimate_total_including_vat, одним запросом на список;
    # ключ есть у КАЖДОГО переданного id (None — итог недоступен)

# crud/stage_summary.py
CODE_OFFER_NOT_FOUND = "offer_not_found"; CODE_TOO_FEW = "too_few_offers"
CODE_ONE_PER_ROUND = "one_offer_per_round"; CODE_SINGLE_PARTICIPANT = "single_participant"
CODE_NO_ESTIMATE = "offer_has_no_estimate"

def validate_selection(db, tender_id: int, offer_ids: Sequence[int]) -> list[Row]   # Offer+Estimate.id+TenderRound, по stage_no
def load_inputs(db, selection) -> list[ColumnInput]
def build_stage_summary(db, tender_id: int, offer_ids: Sequence[int]) -> dict           # JSON §2.16, Decimal квантованы
```

Правила отказов — спека §2.3; контекст `offers` — предложения-виновники (§2.16).
Запросов к БД в `build_stage_summary` — **фиксированное число** (тендер+участник,
выбор, VIEW по списку смет, ставки по списку, итоги по списку, решения по списку,
позиции последней сметы, справочник статей) — не зависит от числа колонок.

- [ ] **Step 1: Фикстура и тесты отказов + счётчик (падают: модуля нет)**

```python
# backend/tests/integration/test_stage_summary_api.py
"""Свод по этапам: проверка выбора, чтение входов, HTTP-контракт (спека §2.3, §2.16).

Сметы предложений строятся НАСТОЯЩИМ `import_round` (образец `_grid` в
`test_tenders_crud.py`): только так у строк появляется `work_category_id`, а
у допработ — статья через ссылку «Сведений» на раздел.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import stage_summary as crud_ss
from crud.common import DomainError
from models import Contractor, Estimate, Offer, OfferPackage, TenderRound
from services import stage_summary as ss
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from parser.constants import JSON_KEY_TOTAL_COST_EXCLUDING_VAT, JSON_KEY_TOTAL_COST_INCLUDING_VAT, JSON_KEY_VAT_AMOUNT
from tests.payloads import additional_works_row, position, proposal, round_payload, summary_line, svedeniya_info

pytestmark = pytest.mark.integration

D = Decimal


def chaptered(code_by_chapter: dict[str, tuple[str, str]], *, vat_rate="20", additional=None, inn="7700000001",
              title="ООО А", total="1200.00"):
    """Предложение: разделы с article_smr и по одной работе под каждым.
    `code_by_chapter` = {"1": ("6", "120.00"), "2": ("2", "60.00")} — статья и сумма работы."""
    positions, n = [], 1
    for chapter, (code, amount) in code_by_chapter.items():
        positions.append(position(job_title=f"Раздел {chapter}", is_chapter=True, chapter_number=chapter,
                                  article_smr=code, number=str(n))); n += 1
        positions.append(position(job_title=f"Работа {chapter}", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total=amount, total_cost_total=amount, chapter_ref=chapter, number=str(n))); n += 1
    summary = {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", total),
               JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "0"),
               JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", total)}
    return proposal(positions, title=title, inn=inn, vat_rate=vat_rate, summary=summary,
                    additional_works=additional,
                    additional_info=svedeniya_info("1 Допработы по разделу - 24.00 руб.") if additional else None)


@contextmanager
def count_queries(db_session):
    """Копия `_count_queries` из test_dashboard_api.py — общий хелпер не заводится (Р10)."""
    counter = {"n": 0}
    bind = db_session.get_bind()

    def _tick(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    sa.event.listen(bind, "before_cursor_execute", _tick)
    try:
        yield counter
    finally:
        sa.event.remove(bind, "before_cursor_execute", _tick)


@pytest.fixture
def grid(db_session, factories):
    """Тендер, 3 раунда (stage_no 1, 2, 4 — пропуск номера законен), участники А и Б.
    А: во всех трёх; Б: только в 2-м. Суммы А по статьям: р1 6→120, 2→60; р2 6→96(+24 допработ), 2→0; р4 6→90."""
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2, 4)}
    db_session.flush()
    payloads = {
        1: [chaptered({"1": ("6", "120.00"), "2": ("2", "60.00")}, total="180.00")],
        2: [chaptered({"1": ("6", "96.00"), "2": ("2", "0")}, total="120.00", additional=additional_works_row(total="24.00")),
            chaptered({"1": ("6", "200.00")}, inn="7700000002", title="ООО Б", total="200.00")],
        4: [chaptered({"1": ("6", "90.00")}, total="90.00")],
    }
    for n, rnd in rounds.items():
        import_round(db_session, tender_round=rnd, data=round_payload(payloads[n]), parser_version="4.0.0",
                     import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                     category_resolver=CategoryResolver.from_db(db_session))
    db_session.flush()

    def offers_of(inn):
        return db_session.execute(
            sa.select(Offer.id)
            .join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id, Contractor.inn == inn)
            .order_by(TenderRound.stage_no)
        ).scalars().all()

    class G: pass
    g = G(); g.tender = tender; g.rounds = rounds
    g.a = offers_of("7700000001")   # [o1, o2, o4]
    g.b = offers_of("7700000002")   # [o2b]
    return g


class TestSelectionRefusals:
    def _code(self, db_session, tender_id, offers):
        with pytest.raises(DomainError) as e:
            crud_ss.build_stage_summary(db_session, tender_id, offers)
        return e.value

    def test_offer_of_other_tender_is_404(self, db_session, factories, grid):
        other = factories.TenderFactory.create(); db_session.flush()
        err = self._code(db_session, other.id, grid.a[:2])
        assert err.status_code == 404 and err.code == crud_ss.CODE_OFFER_NOT_FOUND
        assert set(err.context["offers"]) == set(grid.a[:2])

    def test_too_few_offers_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, grid.a[:1])
        assert err.status_code == 422 and err.code == crud_ss.CODE_TOO_FEW

    def test_two_offers_of_one_round_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, [grid.a[1], grid.b[0]])
        # оба предложения из раунда 2 — но они ещё и разных участников; порядок проверок:
        # раунд раньше участника (спека §2.3 таблица), поэтому код — one_offer_per_round
        assert err.code == crud_ss.CODE_ONE_PER_ROUND and set(err.context["offers"]) == {grid.a[1], grid.b[0]}

    def test_mixed_participants_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, [grid.a[0], grid.b[0]])
        assert err.code == crud_ss.CODE_SINGLE_PARTICIPANT and grid.b[0] in err.context["offers"]

    def test_offer_without_estimate_is_422(self, db_session, grid):
        db_session.execute(sa.delete(Estimate).where(Estimate.offer_id == grid.a[2]))
        db_session.flush()
        err = self._code(db_session, grid.tender.id, grid.a)
        assert err.code == crud_ss.CODE_NO_ESTIMATE and err.context["offers"] == [grid.a[2]]


class TestSelectionShape:
    def test_columns_follow_stage_no_regardless_of_request_order(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, [grid.a[2], grid.a[0], grid.a[1]])
        assert [c["stage_no"] for c in body["columns"]] == [1, 2, 4]
        assert body["participant"]["rounds_with_estimate"] == 3
        assert body["kpi"]["stages_selected"] == 3 and body["kpi"]["stages_loaded"] == 3

    def test_query_count_does_not_grow_with_columns(self, db_session, grid):
        with count_queries(db_session) as two:
            crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a[:2])
        with count_queries(db_session) as three:
            crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert three["n"] == two["n"]
```

- [ ] **Step 2: Прогнать — падает на импорте `crud.stage_summary`**

Run: `cd backend && TEST_DATABASE_URL=... uv run pytest tests/integration/test_stage_summary_api.py -q` (через `just test-int-local-k stage_summary`).

- [ ] **Step 3: `estimate_totals_including_vat` — одно чтение на список**

```python
# дописать в backend/crud/estimate_totals.py
from collections.abc import Sequence


def estimate_totals_including_vat(db: Session, estimate_ids: Sequence[int]) -> dict[int, Decimal | None]:
    """То же правило единогласия, что выше, для списка смет за ДВА запроса
    (спека свода §2.12: число запросов не растёт с числом колонок).
    Ключ есть у каждого переданного id; `None` — итог недоступен."""
    ids = list(dict.fromkeys(estimate_ids))
    result: dict[int, Decimal | None] = {i: None for i in ids}
    if not ids:
        return result
    proposals = db.execute(
        sa.select(Lot.estimate_id, Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id.in_(ids))
    ).all()
    by_estimate: dict[int, list[int]] = {}
    for estimate_id, proposal_id in proposals:
        by_estimate.setdefault(estimate_id, []).append(proposal_id)
    all_proposals = [p for ps in by_estimate.values() for p in ps]
    totals = dict(db.execute(
        sa.select(ProposalSummaryLine.proposal_id, ProposalSummaryLine.total_cost).where(
            ProposalSummaryLine.proposal_id.in_(all_proposals or [-1]),
            ProposalSummaryLine.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
        )
    ).all())
    for estimate_id, proposal_ids in by_estimate.items():
        values = [totals.get(p) for p in proposal_ids]
        if any(v is None or not v.is_finite() for v in values) or len(set(values)) != 1:
            continue
        result[estimate_id] = values[0]
    return result
```

- [ ] **Step 4: `crud/stage_summary.py`**

```python
# backend/crud/stage_summary.py
"""Свод по этапам одного участника — проверка выбора, чтение входов, JSON
(спека 2026-08-27-stage-summary-design.md §2.3, §2.12, §2.16).

Арифметика — в `services/stage_summary.py`; здесь только запросы (фиксированное
число, не зависящее от числа колонок) и квантование на выходе.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.estimate_totals import estimate_totals_including_vat
from crud.project_passport import CATEGORY_TOTALS
from crud.tenders import get_tender
from models import (Contractor, Estimate, EstimateCategoryOverride, Lot, Offer, OfferPackage, PositionItem,
                    Proposal, TenderRound, WorkCategory)
from money.vat import quantize_money
from services import stage_summary as ss
from services.category_rollup import CategoryRef, DirectTotals

CODE_OFFER_NOT_FOUND = "offer_not_found"
CODE_TOO_FEW = "too_few_offers"
CODE_ONE_PER_ROUND = "one_offer_per_round"
CODE_SINGLE_PARTICIPANT = "single_participant"
CODE_NO_ESTIMATE = "offer_has_no_estimate"

_PCT = Decimal("0.1")


def _refuse(status: int, code: str, message: str, offers: Sequence[int]) -> DomainError:
    return DomainError(status, message, code=code, context={"offers": sorted(offers)})


def validate_selection(db: Session, tender_id: int, offer_ids: Sequence[int]):
    """Пять проверок §2.3 в порядке таблицы спеки; результат — строки
    (Offer, Estimate.id | None, TenderRound) по возрастанию stage_no."""
    wanted = list(dict.fromkeys(offer_ids))
    rows = db.execute(
        sa.select(Offer, Estimate.id, TenderRound)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .outerjoin(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.id.in_(wanted or [-1]), Offer.tender_id == tender_id)
        .order_by(TenderRound.stage_no)
    ).all()
    found = {r[0].id for r in rows}
    missing = [o for o in wanted if o not in found]
    if missing:
        raise _refuse(404, CODE_OFFER_NOT_FOUND, "Предложение не найдено в этом тендере.", missing)
    if len(rows) < 2:
        raise _refuse(422, CODE_TOO_FEW, "Для свода нужны хотя бы два предложения.", wanted)
    by_round: dict[int, list[int]] = {}
    for offer, _, rnd in rows:
        by_round.setdefault(rnd.id, []).append(offer.id)
    dup = [o for offers in by_round.values() if len(offers) > 1 for o in offers]
    if dup:
        raise _refuse(422, CODE_ONE_PER_ROUND, "В одном раунде можно выбрать только одно предложение.", dup)
    packages = {offer.package_id for offer, _, _ in rows}
    if len(packages) > 1:
        raise _refuse(422, CODE_SINGLE_PARTICIPANT, "Свод строится по одному участнику.", [r[0].id for r in rows])
    no_estimate = [offer.id for offer, estimate_id, _ in rows if estimate_id is None]
    if no_estimate:
        raise _refuse(422, CODE_NO_ESTIMATE, "У предложения нет сметы — раунд был заменён другим файлом.", no_estimate)
    return rows


def _direct_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, dict]:
    """`{estimate_id -> {work_category_id | None -> {source -> DirectTotals}}}` — ВАЛОВЫЕ,
    накоплением по строкам VIEW (как `_direct_totals` паспорта, но без пересчёта ставки)."""
    out: dict[int, dict] = {e: {} for e in estimate_ids}
    for row in db.execute(sa.select(CATEGORY_TOTALS).where(CATEGORY_TOTALS.c.estimate_id.in_(estimate_ids))).all():
        bucket = out[row.estimate_id].setdefault(row.work_category_id, {})
        prev = bucket.get(row.source)
        amount = row.amount if prev is None else (
            None if (prev.amount is None and row.amount is None) else (prev.amount or 0) + (row.amount or 0))
        bucket[row.source] = DirectTotals(
            amount=amount,
            row_count=row.row_count + (prev.row_count if prev else 0),
            rows_with_amount=row.rows_with_amount + (prev.rows_with_amount if prev else 0),
            rows_not_finite=row.rows_not_finite + (prev.rows_not_finite if prev else 0),
        )
    return out


def _rates_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, Decimal | None]:
    """Единогласие заявленных ставок предложений сметы (правило `_vat_rate` паспорта), одним запросом."""
    rates: dict[int, set] = {e: set() for e in estimate_ids}
    for estimate_id, rate in db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).all():
        rates[estimate_id].add(rate)
    return {e: (next(iter(s)) if len(s) == 1 and None not in s else None) for e, s in rates.items()}


def _overrides_by_estimate(db: Session, estimate_ids: list[int]) -> dict[int, tuple[int, object]]:
    rows = db.execute(
        sa.select(Lot.estimate_id, sa.func.count(), sa.func.max(EstimateCategoryOverride.assigned_at))
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids)).group_by(Lot.estimate_id)
    ).all()
    found = {e: (n, last) for e, n, last in rows}
    return {e: found.get(e, (0, None)) for e in estimate_ids}


def load_inputs(db: Session, selection) -> list[ss.ColumnInput]:
    estimate_ids = [estimate_id for _, estimate_id, _ in selection]
    direct = _direct_by_estimate(db, estimate_ids)
    rates = _rates_by_estimate(db, estimate_ids)
    totals = estimate_totals_including_vat(db, estimate_ids)
    overrides = _overrides_by_estimate(db, estimate_ids)
    return [
        ss.ColumnInput(offer_id=offer.id, estimate_id=estimate_id, round_id=rnd.id, stage_no=rnd.stage_no,
                       label=rnd.label, held_on=rnd.held_on, vat_rate_base=rates[estimate_id],
                       direct=direct[estimate_id], file_total_gross=totals[estimate_id],
                       overrides_count=overrides[estimate_id][0], overrides_last_at=overrides[estimate_id][1])
        for offer, estimate_id, rnd in selection
    ]


def _money(v: Decimal | None) -> str | None:
    q = quantize_money(v)
    return None if q is None else str(q)


def _pct(v: Decimal | None) -> str | None:
    return None if v is None else str(v.quantize(_PCT, rounding=ROUND_HALF_UP))


def _change(c: ss.Change) -> dict:
    value = _pct(c.value) if c.kind == ss.KIND_PERCENT else _money(c.value)
    return {"kind": c.kind, "value": value, "direction": c.direction, "reason": c.reason}


def _cell(c: ss.Cell) -> dict:
    return {"state": c.state, "amount": _money(c.shown), "amount_unavailable_reason": c.unavailable_reason,
            "additional_works_amount": _money(c.additional_works_shown),
            "rows": {"row_count": c.rows.row_count, "rows_with_amount": c.rows.rows_with_amount,
                     "rows_not_finite": c.rows.rows_not_finite},
            "change": _change(c.change)}


def _row(r: ss.SummaryRow) -> dict:
    return {"work_category_id": None if r.ref is None else r.ref.id,
            "code": None if r.ref is None else r.ref.code,
            "title": "Нераспределённое" if r.ref is None else r.ref.title,
            "is_unallocated": r.is_unallocated,
            "cells": [_cell(c) for c in r.cells],
            "bargain": _change(r.bargain),
            "contribution": {"value": _money(r.contribution.value), "direction": r.contribution.direction,
                             "reason": r.contribution.reason},
            "children": [_row(ch) for ch in r.children]}


def build_stage_summary(db: Session, tender_id: int, offer_ids: Sequence[int]) -> dict:
    tender = get_tender(db, tender_id)
    selection = validate_selection(db, tender_id, offer_ids)
    columns = load_inputs(db, selection)
    package_id = selection[0][0].package_id
    package, contractor = db.execute(
        sa.select(OfferPackage, Contractor).join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(OfferPackage.id == package_id)
    ).one()
    rounds_with_estimate = db.execute(
        sa.select(sa.func.count()).select_from(Offer).join(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.package_id == package_id)
    ).scalar_one()
    last_estimate_id = columns[-1].estimate_id
    last_positions = db.execute(
        sa.select(sa.func.count()).select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == last_estimate_id, PositionItem.is_chapter.is_(False))
    ).scalar_one()
    refs = [CategoryRef(id=c.id, code=c.code, title=c.title, parent_id=c.parent_id, is_bucket=c.is_bucket,
                        sort_order=c.sort_order) for c in db.execute(sa.select(WorkCategory)).scalars().all()]

    result = ss.compute_summary(columns, refs)

    return {
        "tender": {"id": tender.id, "tender_number": tender.tender_number, "title": tender.title,
                   "object_title": tender.object.title},
        "participant": {"package_id": package.id, "contractor_id": contractor.id, "title": contractor.title,
                        "inn": contractor.inn, "rounds_with_estimate": rounds_with_estimate},
        "columns": [{
            "kind": "round", "offer_id": c.input.offer_id, "estimate_id": c.input.estimate_id,
            "round_id": c.input.round_id, "stage_no": c.input.stage_no, "label": c.input.label,
            "held_on": iso(c.input.held_on), "vat_rate_base": None if c.input.vat_rate_base is None else str(c.input.vat_rate_base),
            "vat_state": c.vat_state, "total": _money(c.total_shown), "total_change": _change(c.total_change),
            "bar_height_pct": _pct(c.bar_height_pct),
            "manual_overrides": {"count": c.input.overrides_count, "last_at": iso(c.input.overrides_last_at)},
            "convergence": {"categories_sum": _money(c.convergence.categories_sum_gross),
                            "file_total": _money(c.convergence.file_total_gross),
                            "converged": c.convergence.converged, "delta": _money(c.convergence.delta),
                            "reason": c.convergence.reason},
        } for c in result.columns],
        "rows": [_row(r) for r in result.rows],
        "unallocated": _row(result.unallocated),
        "total": {"cells": [_cell(c) for c in result.total_cells]},
        "display": {"tax_basis": result.display.basis, "reason": result.display.reason,
                    "rates_by_column": None if result.display.rates_by_column is None
                    else [None if r is None else str(r) for r in result.display.rates_by_column],
                    "price_level": "nominal"},
        "kpi": {"stages_selected": result.kpi.stages_selected, "stages_loaded": rounds_with_estimate,
                "last_stage_positions": last_positions, "categories_with_amount": result.kpi.categories_with_amount,
                "categories_total": result.kpi.categories_total, "first_to_last": _change(result.kpi.first_to_last)},
        "track": {"available": result.track.available, "reason": result.track.reason},
    }
```

- [ ] **Step 5: Прогнать; поправить фикстуру, если `import_round` требует иных ключей payload**

Run: `just test-int-local-k stage_summary`
Expected: PASS. Если `chaptered` не проходит `_validate_payload`, читать сообщение отказа и доводить payload до формы `round_payload` из `test_tenders_crud.py` — утверждения тестов не менять.

- [ ] **Step 6: Снятие защит — по одному отказу за раз**

Для каждого из пяти отказов закомментировать его `raise` в `validate_selection` и прогнать `TestSelectionRefusals` — соответствующий тест обязан покраснеть **сам и только он** (вход каждого теста нарушает ровно одно ограничение; у `test_two_offers_of_one_round_is_422` при снятой проверке раунда упадёт следующая — `single_participant` — и тест покраснеет на коде: это ожидаемо и записывается). Вернуть.

- [ ] **Step 7: ruff и коммит**

```bash
cd backend && uv run ruff check crud/stage_summary.py crud/estimate_totals.py tests/integration/test_stage_summary_api.py
git add backend/crud/stage_summary.py backend/crud/estimate_totals.py backend/tests/integration/test_stage_summary_api.py
git commit -m "feat(stage-summary): проверка выбора и чтение входов свода постоянным числом запросов"
```

---

## Task 4: Интеграция расчёта на живых данных — ветви VIEW, разнос, ось НДС, сходимость, KPI

**Files:**
- Test: `backend/tests/integration/test_stage_summary_api.py` (дописать)

**Interfaces:**
- Consumes: `build_stage_summary`, фикстура `grid`, `set_override` из `services/category_override.py` (сигнатура: `db, *, estimate_id, position_item_id, work_category_id, note, user_id`), `admin_user`, `category_id`.

- [ ] **Step 1: Тесты**

```python
def _row(body, code):
    return next(r for r in body["rows"] if r["code"] == code)


def _estimate_of(db, offer_id):
    return db.execute(sa.select(Estimate).where(Estimate.offer_id == offer_id)).scalar_one()


class TestAmounts:
    def test_both_view_branches_and_additional_works_amount(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        six = _row(body, "6")
        assert six["cells"][1]["amount"] == "120.00"                # 96 позиций + 24 допработ (§2.4)
        assert six["cells"][1]["additional_works_amount"] == "24.00"
        assert six["cells"][0]["additional_works_amount"] is None   # ветви нет вовсе

    def test_states_along_selected_path(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        two = _row(body, "2")
        assert [c["state"] for c in two["cells"]] == ["amount", "removed", "absent"]
        assert two["cells"][1]["change"]["kind"] == "removed"
        # removed → absent: отдельной дельты нет (матрица §2.6), факт виден по состояниям
        assert two["cells"][2]["change"] == {"kind": "none", "value": None, "direction": None, "reason": "no_amounts"}
        assert two["contribution"] == {"value": None, "direction": None, "reason": "absent_endpoint"}

    def test_excluding_middle_column_changes_neighbours(self, db_session, grid):
        full = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        short = crud_ss.build_stage_summary(db_session, grid.tender.id, [grid.a[0], grid.a[2]])
        assert _row(full, "6")["cells"][1]["change"]["value"] == "0.0"      # 120 → 120 (96 + 24 допработ)
        assert _row(full, "6")["cells"][2]["change"]["value"] == "-25.0"    # 120 → 90 относительно раунда 2
        assert _row(short, "6")["cells"][1]["change"]["value"] == "-25.0"   # 120 → 90 относительно раунда 1
        # путь = выбранные: без раунда 2 статья «2» идёт amount → absent, то есть «нет в файле», а не «снято»
        assert _row(full, "2")["cells"][1]["change"]["kind"] == "removed"
        assert _row(short, "2")["cells"][1]["change"]["kind"] == "disappeared"
        assert len(short["columns"]) == 2
        assert short["participant"]["rounds_with_estimate"] == 3 and short["kpi"]["stages_selected"] == 2

    def test_manual_override_moves_money_and_is_signed(self, db_session, grid, admin_user):
        from services.category_override import set_override
        est = _estimate_of(db_session, grid.a[0])
        chapter = db_session.execute(
            sa.select(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == est.id, PositionItem.is_chapter.is_(True), PositionItem.chapter_number == "2")
        ).scalar_one()
        seven = db_session.execute(sa.select(WorkCategory.id).where(WorkCategory.code == "7")).scalar_one()
        set_override(db_session, estimate_id=est.id, position_item_id=chapter.id, work_category_id=seven,
                     note=None, user_id=admin_user.id)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert _row(body, "7")["cells"][0]["amount"] == "60.00"
        assert _row(body, "2")["cells"][0]["state"] == "absent"
        assert body["columns"][0]["manual_overrides"]["count"] == 1
        assert body["columns"][0]["manual_overrides"]["last_at"] is not None
        assert body["columns"][1]["manual_overrides"] == {"count": 0, "last_at": None}


class TestVatAxis:
    def _set_rate(self, db, offer_id, rate):
        est = _estimate_of(db, offer_id)
        db.execute(sa.update(Proposal).where(Proposal.lot_id.in_(sa.select(Lot.id).where(Lot.estimate_id == est.id)))
                   .values(vat_rate=rate))
        db.flush()

    def test_single_rate_is_gross(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["display"]["tax_basis"] == "gross" and body["display"]["reason"] == "single_rate"
        assert body["columns"][0]["total"] == "180.00"

    def test_mixed_rates_is_net_with_rates_listed(self, db_session, grid):
        self._set_rate(db_session, grid.a[1], Decimal("0"))
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["display"]["tax_basis"] == "net"
        assert [Decimal(r) for r in body["display"]["rates_by_column"]] == [D("20"), D("0"), D("20")]
        assert body["columns"][0]["total"] == "150.00" and body["columns"][1]["total"] == "120.00"
        assert body["columns"][0]["convergence"]["categories_sum"] == "180.00"   # сходимость в валовых (§2.9)

    def test_twenty_plus_unknown_keeps_known_gross(self, db_session, grid):
        self._set_rate(db_session, grid.a[1], None)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["display"]["tax_basis"] == "gross"
        col = body["columns"][1]
        assert col["vat_state"] == "unknown_vat_base" and col["total"] is None and col["bar_height_pct"] is None
        cell = _row(body, "6")["cells"][1]
        assert cell["state"] == "amount" and cell["amount"] is None
        assert cell["amount_unavailable_reason"] == "unknown_vat_base"
        assert cell["change"] == {"kind": "none", "value": None, "direction": None, "reason": "unknown_vat_base"}
        assert _row(body, "6")["cells"][2]["change"]["reason"] == "unknown_vat_base"
        assert body["track"]["available"] is True
        assert col["convergence"]["converged"] is True      # сходимость от ставки не зависит

    def test_all_unknown_disables_track_and_basis(self, db_session, grid):
        for o in grid.a:
            self._set_rate(db_session, o, None)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["display"]["tax_basis"] == "none"
        assert body["track"] == {"available": False, "reason": "no_comparable_totals"}
        assert _row(body, "6")["cells"][0]["state"] == "amount"


class TestConvergenceAndKpi:
    def test_convergence_null_when_file_total_not_unanimous(self, db_session, grid):
        est = _estimate_of(db_session, grid.a[0])
        db_session.execute(sa.delete(ProposalSummaryLine).where(
            ProposalSummaryLine.proposal_id.in_(sa.select(Proposal.id).join(Lot).where(Lot.estimate_id == est.id))))
        db_session.flush()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["columns"][0]["convergence"]["converged"] is None
        assert body["columns"][0]["convergence"]["reason"] == "file_total_unavailable"
        assert body["columns"][0]["total"] == "180.00"                # итог колонки — не файловый (§2.9)

    def test_kpi_last_stage_positions_excludes_chapters(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        assert body["kpi"]["last_stage_positions"] == 1
        assert body["kpi"]["categories_with_amount"] == 1
        assert body["kpi"]["categories_total"] == len(body["rows"])

    def test_rows_not_finite_reaches_cell(self, db_session, grid):
        est = _estimate_of(db_session, grid.a[2])
        db_session.execute(sa.update(PositionItem).where(
            PositionItem.proposal_id.in_(sa.select(Proposal.id).join(Lot).where(Lot.estimate_id == est.id)),
            PositionItem.is_chapter.is_(False)).values(total_cost_total=Decimal("NaN")))
        db_session.flush()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a)
        rows = _row(body, "6")["cells"][2]["rows"]
        assert rows == {"row_count": 1, "rows_with_amount": 0, "rows_not_finite": 1}
```

Импорты дописать: `ProposalSummaryLine`, `PositionItem`, `Proposal`, `Lot`, `WorkCategory` из `models`.

- [ ] **Step 2: Прогнать — ожидаются падения только там, где реализация Task 3 расходится с числами**

Run: `just test-int-local-k stage_summary`
Разбирать каждое падение: если расходится сумма — сначала проверить фикстуру запросом к `v_category_totals` (`select * from v_category_totals where estimate_id = …`), потом код. Утверждения с числами не править без записи причины в отчёт.

- [ ] **Step 3: Снятие защиты — сходимость в валовых**

В `compute_summary` подменить `totals_gross[idx]` на `totals_shown[idx] or 0` при расчёте `Convergence` → `test_mixed_rates_is_net_with_rates_listed` красный на `categories_sum`. Вернуть.

- [ ] **Step 4: Коммит**

```bash
git add backend/tests/integration/test_stage_summary_api.py
git commit -m "test(stage-summary): ветви VIEW, разнос, три случая оси НДС, сходимость, KPI на живой БД"
```

---

## Task 5: Эндпоинт `GET /api/v1/tenders/{tender_id}/stage-summary`

**Files:**
- Modify: `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_stage_summary_api.py` (дописать `TestHttp`)

**Interfaces:**
- Consumes: `build_stage_summary`; `raise_domain_error`; `decimal_json`; `Query`.
- Produces: маршрут `GET /api/v1/tenders/{tender_id}/stage-summary?offers=…` → 200 JSON §2.16; 404/422 `detail: {code, message, offers}`.

- [ ] **Step 1: HTTP-тесты**

```python
class TestHttp:
    URL = "/api/v1/tenders/{tid}/stage-summary"

    def test_member_reads_summary(self, member_client, db_session, grid):
        db_session.flush()
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": grid.a})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["stage_no"] for c in body["columns"]] == [1, 2, 4]
        assert body["display"]["price_level"] == "nominal"
        assert isinstance(body["columns"][0]["total"], str)      # Decimal — строкой (decimal_json)

    def test_422_detail_is_object_with_code_and_offers(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": [grid.a[0], grid.b[0]]})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "single_participant"
        assert set(r.json()["detail"]["offers"]) == {grid.a[0], grid.b[0]}

    def test_404_detail_has_same_shape(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": [grid.a[0], 999999]})
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "offer_not_found", "message": r.json()["detail"]["message"],
                                      "offers": [999999]}

    def test_missing_offers_param_is_422_too_few(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=grid.tender.id))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "too_few_offers"

    def test_unknown_tender_is_404(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=999999), params={"offers": grid.a})
        assert r.status_code == 404
```

- [ ] **Step 2: Прогнать — 404 «Not Found» от FastAPI на маршруте**

- [ ] **Step 3: Роутер**

```python
# backend/routers/tenders.py — добавить импорт и обработчик
from crud import stage_summary as crud_stage_summary

@router.get("/{tender_id}/stage-summary")
def stage_summary(tender_id: int, offers: list[int] = Query(default=[]), db: Session = Depends(get_db)):
    """Свод по этапам одного участника (спека 2026-08-27-stage-summary-design.md §2.3, §2.16).

    `offers` — повторяющийся параметр. Пустой список НЕ отдаётся ошибкой валидации
    FastAPI: он доходит до домена и получает `too_few_offers` — тот же код, что у
    одного предложения, чтобы клиент различал причины по `code`, а не по форме 422.
    """
    try:
        return decimal_json(crud_stage_summary.build_stage_summary(db, tender_id, offers))
    except DomainError as e:
        raise_domain_error(e)
```

Маршрут объявить **до** `@router.get("/{tender_id}")`? — нет нужды: у FastAPI сегмент `/stage-summary` статический и не конфликтует с `/{tender_id}`; но `get_tender(db, tender_id)` внутри `build_stage_summary` даёт 404 без `code` для чужого тендера — это `DomainError(404, …)` без кода, `detail` строкой; тест `test_unknown_tender_is_404` утверждает только статус.

- [ ] **Step 4: Прогнать — PASS; полный `just test-backend-unit` и точечный интеграционный**

- [ ] **Step 5: Коммит**

```bash
cd backend && uv run ruff check routers/tenders.py
git add backend/routers/tenders.py backend/tests/integration/test_stage_summary_api.py
git commit -m "feat(stage-summary): GET /tenders/{id}/stage-summary — отказы кодами, Decimal строками"
```

---
## Task 6: Фронтенд — типы §2.16, API, ключ, хук, фикстура и хендлер msw

**Files:**
- Modify: `frontend/src/types/domain.ts`, `frontend/src/services/api/domain.ts`, `frontend/src/services/queryKeys.ts`, `frontend/src/services/queries.ts`, `frontend/src/test/fixtures.ts`, `frontend/src/test/handlers.ts`
- Test: `frontend/src/services/queries.tenders.test.tsx` (дописать)

**Interfaces:**
- Produces:

```ts
// types/domain.ts — форма спеки §2.16, поле в поле
export type CellState = "amount" | "removed" | "not_evaluated" | "absent";
export type ChangeKind = "percent" | "abs_only" | "appeared" | "reappeared" | "removed" | "disappeared" | "none";
export type Direction = "up" | "down" | "flat";
export interface StageSummaryChange { kind: ChangeKind; value: Decimal | null; direction: Direction | null; reason: string | null; }
export interface StageSummaryCell {
  state: CellState; amount: Decimal | null; amount_unavailable_reason: "unknown_vat_base" | null;
  additional_works_amount: Decimal | null;
  rows: { row_count: number; rows_with_amount: number; rows_not_finite: number };
  change: StageSummaryChange;
}
export interface StageSummaryRow {
  work_category_id: number | null; code: string | null; title: string; is_unallocated: boolean;
  cells: StageSummaryCell[]; bargain: StageSummaryChange;
  contribution: { value: Decimal | null; direction: Direction | null; reason: string | null };
  children: StageSummaryRow[];
}
export interface StageSummaryColumn {
  kind: "round"; offer_id: number; estimate_id: number; round_id: number; stage_no: number;
  label: string | null; held_on: string | null; vat_rate_base: Decimal | null;
  vat_state: "known" | "unknown_vat_base"; total: Decimal | null; total_change: StageSummaryChange;
  bar_height_pct: Decimal | null; manual_overrides: { count: number; last_at: string | null };
  convergence: { categories_sum: Decimal; file_total: Decimal | null; converged: boolean | null; delta: Decimal | null; reason: "file_total_unavailable" | null };
}
export interface StageSummary {
  tender: { id: number; tender_number: string; title: string; object_title: string };
  participant: { package_id: number; contractor_id: number; title: string; inn: string; rounds_with_estimate: number };
  columns: StageSummaryColumn[]; rows: StageSummaryRow[]; unallocated: StageSummaryRow; total: { cells: StageSummaryCell[] };
  display: { tax_basis: "gross" | "net" | "none"; reason: "single_rate" | "mixed_rates" | "no_known_rates"; rates_by_column: (Decimal | null)[] | null; price_level: "nominal" };
  kpi: { stages_selected: number; stages_loaded: number; last_stage_positions: number; categories_with_amount: number; categories_total: number; first_to_last: StageSummaryChange };
  track: { available: boolean; reason: "non_positive_total" | "no_comparable_totals" | null };
}
export type StageSummaryErrorCode = "offer_not_found" | "too_few_offers" | "one_offer_per_round" | "single_participant" | "offer_has_no_estimate";
export interface StageSummaryErrorDetail { code: StageSummaryErrorCode; message: string; offers: number[] }

// api/domain.ts
tendersApi.stageSummary: (tenderId: number, offerIds: number[]) => Promise<StageSummary>
   // api.get(`/v1/tenders/${tenderId}/stage-summary`, { params: { offers: offerIds }, paramsSerializer: { indexes: null } })
   // indexes: null — axios сериализует массив как offers=1&offers=2 (без скобок), как ждёт FastAPI
// queryKeys.ts
qk.tenders.stageSummary: (tenderId: number, offerIds: number[]) => ["tenders", "stage-summary", tenderId, [...offerIds].sort((a, b) => a - b)] as const
// queries.ts
export function useStageSummary(tenderId: number | undefined, offerIds: number[])
   // enabled: tenderId !== undefined && offerIds.length >= 1 (Р8); retry: false — 4xx не повторяются
// test/fixtures.ts
export const sampleStageSummary: StageSummary   // 3 колонки (stage 1, 2, 4), строки «6» (с ребёнком «6.99»), «2», unallocated, все виды изменения хоть раз
// test/handlers.ts
handlerState.stageSummaryOutcome: "ok" | "single_participant" | "too_few_offers" | "offer_not_found" | "unknown_vat" | "track_unavailable"
```

- [ ] **Step 1: Тест хука (падает: экспорта нет)**

```tsx
// дописать в queries.tenders.test.tsx
import { useStageSummary } from "./queries";

describe("useStageSummary", () => {
  it("отдаёт свод с тремя колонками по возрастанию stage_no", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7002, 7001, 7004]), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.columns.map((c) => c.stage_no)).toEqual([1, 2, 4]);
    // ключ канонический: порядок id в URL не создаёт второй записи кэша
    expect(qc.getQueryData(qk.tenders.stageSummary(300, [7001, 7002, 7004]))).toBeDefined();
  });

  it("single_participant → 422 с кодом и списком offers в detail", async () => {
    handlerState.stageSummaryOutcome = "single_participant";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001, 7101]), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorStatus(result.current.error)).toBe(422);
    expect(apiErrorCode(result.current.error)).toBe("single_participant");
    expect(apiErrorContext<{ offers: number[] }>(result.current.error)?.offers).toEqual([7001, 7101]);
  });

  it("offer_not_found → 404 с тем же объектом detail", async () => {
    handlerState.stageSummaryOutcome = "offer_not_found";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001, 9999]), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorStatus(result.current.error)).toBe(404);
    expect(apiErrorCode(result.current.error)).toBe("offer_not_found");
  });

  it("при одном offer запрос УХОДИТ и получает too_few_offers — клиент отказ не подменяет", async () => {
    handlerState.stageSummaryOutcome = "too_few_offers";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001]), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorCode(result.current.error)).toBe("too_few_offers");
  });

  it("при пустом выборе запрос не уходит", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, []), { wrapper: wrapperFor(qc) });
    expect(result.current.fetchStatus).toBe("idle");
  });
});
```

- [ ] **Step 2: Прогнать — `just test-frontend-file src/services/queries.tenders.test.tsx` → падает**

- [ ] **Step 3: Типы, API, ключ, хук — по контракту выше**

```ts
// queries.ts
export function useStageSummary(tenderId: number | undefined, offerIds: number[]) {
  return useQuery({
    queryKey: qk.tenders.stageSummary(tenderId ?? 0, offerIds),
    queryFn: () => tendersApi.stageSummary(tenderId as number, offerIds),
    enabled: tenderId !== undefined && offerIds.length >= 1,
    retry: false,
  });
}
```

- [ ] **Step 4: Фикстура и хендлер**

`sampleStageSummary` — числа те же, что в фикстуре бэкенда Task 3 (`180 / 120 / 90`, статья «6» 120→120→90, «2» 60→снято→нет в файле, «Нераспределённое» 0), плюс ребёнок «6.99» у «6» с суммами `12 / 24 / 0` (removed в 3-й). `columns[*].bar_height_pct`: `"100.0" / "66.7" / "50.0"`; `manual_overrides` у 2-й колонки `{count: 1, last_at: "2026-08-26T10:00:00Z"}`. `display` — `gross/single_rate`. Кол-во `cells` везде = 3.

```ts
// handlers.ts
http.get("/api/v1/tenders/:id/stage-summary", ({ request }) => {
  const offers = new URL(request.url).searchParams.getAll("offers").map(Number);
  const refuse = (status: number, code: string) =>
    HttpResponse.json({ detail: { code, message: `Отказ ${code}`, offers } }, { status });
  switch (handlerState.stageSummaryOutcome) {
    case "single_participant": return refuse(422, "single_participant");
    case "too_few_offers":     return refuse(422, "too_few_offers");
    case "offer_not_found":    return refuse(404, "offer_not_found");
    case "unknown_vat":        return HttpResponse.json(stageSummaryWithUnknownSecondColumn());
    case "track_unavailable":  return HttpResponse.json({ ...sampleStageSummary, track: { available: false, reason: "non_positive_total" } });
    default:                   return HttpResponse.json(sampleStageSummary);
  }
}),
```

`stageSummaryWithUnknownSecondColumn()` — копия фикстуры, где у колонки 2 `vat_state: "unknown_vat_base"`, `total: null`, `bar_height_pct: null`, у всех её ячеек `amount: null`, `amount_unavailable_reason: "unknown_vat_base"`, `change: {kind: "none", reason: "unknown_vat_base"}`; у ячеек колонки 3 `change.reason: "unknown_vat_base"`. Добавить поле в `HandlerState`, значение по умолчанию и сброс в `resetHandlerState`.

- [ ] **Step 5: Прогнать хук-тесты и `tsc -b` — зелёные**

Run: `cd frontend && npx tsc -b --noEmit && just test-frontend-file src/services/queries.tenders.test.tsx`

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/types/domain.ts frontend/src/services/api/domain.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts frontend/src/test/fixtures.ts frontend/src/test/handlers.ts frontend/src/services/queries.tenders.test.tsx
git commit -m "feat(stage-summary): типы ответа свода, tendersApi.stageSummary, useStageSummary, фикстура и хендлер"
```

---

## Task 7: Решётка — плитки-переключатели, выбор участника, кнопка «Свод по этапам (N)»

**Files:**
- Modify: `frontend/src/components/tenders/OfferGrid.tsx`, `frontend/src/pages/tenders/TenderCardPage.tsx`
- Test: `frontend/src/pages/tenders/TenderCardPage.test.tsx` (дописать блок)

**Interfaces:**
- Consumes: `Toggle` (`pressed`, `onPressedChange`, `disabled`), `Check` из lucide, `TenderCard`.
- Produces:

```ts
interface OfferGridProps {
  card: TenderCard; selectedRoundId: number | undefined; onSelectRound: (roundId: number) => void;
  selectedOfferIds: ReadonlySet<number>;            // выбранные предложения (одного участника)
  onToggleOffer: (offerId: number, packageId: number) => void;
  onSelectParticipant: (packageId: number) => void; // клик по имени — все сметы участника
}
```

Правила — спека §2.1: плитка только у ячейки с `offer_id` и `estimate_id`; чужие строки `disabled` с `title="Свод строится по одному участнику"`, пока выбрана хотя бы одна плитка; клик по имени — все сметы участника разом; кнопка активна от двух.

- [ ] **Step 1: Тесты карточки (падают)**

```tsx
// дописать в TenderCardPage.test.tsx — фикстура sampleTenderCard: Альфа имеет смету только в раунде 1 (7001).
// Для этого блока хендлер отдаёт карточку, где у Альфы сметы в ОБОИХ раундах: расширить `tenderCardFor`
// вариантом handlerState.tenderRoundState = "both-loaded" (offer 7003/estimate 8003 у Альфы в раунде 2).
describe("Выбор предложений для свода (спека свода §2.1)", () => {
  it("плитки есть только у ячеек со сметой; у «—» и «нет сметы» плиток нет", async () => {
    handlerState.tenderRoundState = "both-loaded";
    renderCard();
    await screen.findByText("ООО Альфа");
    const tiles = screen.getAllByRole("button", { pressed: false });
    // две сметы Альфы = две плитки; у Беты («—» и «нет сметы») — ни одной
    expect(tiles.filter((t) => t.getAttribute("aria-pressed") !== null)).toHaveLength(2);
  });

  it("нажатие плитки выбирает; кнопка активна от двух; чужие плитки недоступны", async () => {
    handlerState.tenderRoundState = "both-loaded-with-beta";   // у Беты тоже смета в раунде 2
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");
    const button = screen.getByRole("button", { name: /Свод по этапам/ });
    expect(button).toBeDisabled();

    const alfaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const alfaTiles = within(alfaRow).getAllByRole("button", { pressed: false });
    await user.click(alfaTiles[0]);
    expect(alfaTiles[0]).toHaveAttribute("aria-pressed", "true");
    expect(button).toBeDisabled();
    expect(screen.getByText(/выбрано 1 смета · ООО Альфа/)).toBeInTheDocument();

    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaTile = within(betaRow).getByRole("button", { pressed: false });
    expect(betaTile).toBeDisabled();
    expect(betaTile).toHaveAttribute("title", "Свод строится по одному участнику");

    await user.click(alfaTiles[1]);
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("Свод по этапам (2)");
    // ссылка ведёт на свод с выбранными offer_id повторяющимся параметром
    expect(button.closest("a")).toHaveAttribute("href", "/tenders/300/summary?offers=7001&offers=7003");
  });

  it("клик по имени участника выбирает все его сметы; повторный снимает", async () => {
    handlerState.tenderRoundState = "both-loaded";
    const user = userEvent.setup();
    renderCard();
    const name = await screen.findByRole("button", { name: "ООО Альфа" });
    await user.click(name);
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(2);
    await user.click(name);
    expect(screen.queryAllByRole("button", { pressed: true })).toHaveLength(0);
  });

  it("без выбора карточка сохраняет факты решётки: «—», «нет сметы», суммы", async () => {
    renderCard();
    await screen.findByText("ООО Альфа");
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    expect(within(betaRow).getAllByRole("cell")[1]).toHaveTextContent("—");
    expect(within(betaRow).getAllByRole("cell")[2]).toHaveTextContent("нет сметы");
    expect(screen.getByText(/1\s200,00/)).toBeInTheDocument();
  });
});
```

Существующий тест «решётка 2×2» остаётся как есть — он утверждает факты (DoD 6); если он читает ячейки по индексу и плитка добавляет вложенный элемент, `toHaveTextContent` по-прежнему проходит.

- [ ] **Step 2: Прогнать — падают на отсутствии плиток/кнопки**

- [ ] **Step 3: Фикстуры карточки для тестов выбора**

В `handlers.ts` расширить `tenderCardFor` двумя состояниями `handlerState.tenderRoundState`:
`"both-loaded"` — у Альфы в раунде 2 ячейка `{offer_id: 7003, estimate_id: 8003, total_including_vat: "1100.00"}`;
`"both-loaded-with-beta"` — то же плюс у Беты в раунде 2 `{offer_id: 7002, estimate_id: 8002, total_including_vat: "1300.00"}`.
Тип поля `tenderRoundState` дополнить этими литералами. Существующие состояния не менять.

- [ ] **Step 4: `OfferGrid` — плитка вместо текста суммы**

```tsx
// внутри map по ячейкам, ветка «обе есть»
const offerId = cell.offer_id;
const pressed = selectedOfferIds.has(offerId);
const foreign = selectedOfferIds.size > 0 && !pressed && selectedPackageId !== participant.package_id;
return (
  <TableCell key={round.id} className="text-right">
    <Toggle
      variant="outline" size="sm"
      pressed={pressed}
      disabled={foreign}
      title={foreign ? "Свод строится по одному участнику" : pressed ? "В своде" : "Взять в свод"}
      onPressedChange={() => onToggleOffer(offerId, participant.package_id)}
      className={cn("relative tabular-nums", pressed && "border-accent-primary bg-accent-primary-soft text-accent-primary-text font-semibold")}
    >
      {formatDecimalMoney(cell.total_including_vat)}
      {pressed && <Check aria-hidden className="absolute -right-1.5 -top-1.5 size-3.5 rounded-full bg-accent-primary p-0.5 text-action-primary-text" />}
    </Toggle>
  </TableCell>
);
```

`selectedPackageId` вычисляется в `OfferGrid` из `card.cells` по первому выбранному `offer_id`. Имя участника — `<button type="button" className="font-medium text-fg hover:underline" onClick={() => onSelectParticipant(participant.package_id)} title="Выбрать все этапы участника">{participant.title}</button>`. `dark:`-двойники классам заливки писать явно (грабля §11: утилиты `dark:` примитива перебивают токен-классы) — `dark:bg-accent-primary-soft`.

- [ ] **Step 5: `TenderCardPage` — состояние и кнопка**

```tsx
const [selectedOfferIds, setSelectedOfferIds] = useState<ReadonlySet<number>>(new Set());
const selectedParticipant = card?.participants.find((p) =>
  card.cells.some((c) => c.offer_id !== null && selectedOfferIds.has(c.offer_id) && c.package_id === p.package_id));

function toggleOffer(offerId: number) {
  setSelectedOfferIds((prev) => { const next = new Set(prev); next.has(offerId) ? next.delete(offerId) : next.add(offerId); return next; });
}
function selectParticipant(packageId: number) {
  const own = (card?.cells ?? []).filter((c) => c.package_id === packageId && c.offer_id !== null && c.estimate_id !== null).map((c) => c.offer_id as number);
  setSelectedOfferIds((prev) => (own.every((id) => prev.has(id)) && own.length > 0 ? new Set() : new Set(own)));
}
const summaryQuery = new URLSearchParams();
[...selectedOfferIds].sort((a, b) => a - b).forEach((id) => summaryQuery.append("offers", String(id)));
const summaryHref = `/tenders/${card?.id}/summary?${summaryQuery.toString()}`;
```

Над решёткой (`actions` заголовка либо строка над `OfferGrid`): подпись `выбрано {n} {pluralRu-склонение «смета/сметы/смет»} · {участник}` и `<Button render={<Link to={summaryHref} />} disabled={n < 2} title={n < 2 ? "выберите хотя бы два этапа" : undefined}>Свод по этапам ({n})</Button>` — `render`-проп у `Button` уже используется в этом файле (`К списку тендеров`). При `disabled` `Link` не рендерить, а рендерить обычную кнопку — иначе disabled-ссылка кликабельна.

- [ ] **Step 6: Прогнать карточку и весь `tsc -b`, eslint**

Run: `cd frontend && npx tsc -b --noEmit && npx eslint src/components/tenders src/pages/tenders && just test-frontend-file src/pages/tenders/TenderCardPage.test.tsx`

- [ ] **Step 7: Коммит**

```bash
git add frontend/src/components/tenders/OfferGrid.tsx frontend/src/pages/tenders/TenderCardPage.tsx frontend/src/pages/tenders/TenderCardPage.test.tsx frontend/src/test/handlers.ts frontend/src/test/fixtures.ts
git commit -m "feat(stage-summary): плитки выбора предложений на решётке, кнопка «Свод по этапам (N)»"
```

---

## Task 8: Страница свода — шапка, оси, трасса, таблица, состояния, маршрут

**Files:**
- Create: `frontend/src/pages/tenders/summary/StageSummaryPage.tsx`, `StageSummaryTrack.tsx`, `StageSummaryTable.tsx`, `SummaryCell.tsx`, `cellCopy.ts`
- Test: `frontend/src/pages/tenders/summary/StageSummaryPage.test.tsx`, `StageSummaryTable.test.tsx`, `summaryTokens.test.ts`
- Modify: `frontend/src/App.tsx` (маршрут `/tenders/:tenderId/summary`)

**Interfaces:**
- Consumes: `useStageSummary`, типы Task 6, `Breadcrumbs`, `PageHeader`, `Surface`, `EmptyState`, `Skeleton`, `StatusPill`, `KpiCard`, `Tooltip*`, `formatDecimalMoney`, `roundDecimalPercent`, `formatDate`, `cn`, `useSearchParams().getAll("offers")`.
- Produces: `cellCopy.ts` — словари подписей:

```ts
export const STATE_LABEL: Record<Exclude<CellState, "amount">, string> = { removed: "снято", not_evaluated: "не оценивалась", absent: "—" };
export const KIND_LABEL: Record<Exclude<ChangeKind, "percent" | "abs_only" | "none">, string> =
  { appeared: "появилась", reappeared: "вернулась", removed: "снято", disappeared: "нет в файле" };
export const REASON_LABEL: Record<string, string> = {
  first_column: "первая колонка", unknown_vat_base: "база НДС неизвестна — сравнивать нечем",
  no_amounts: "суммы нет ни на одном конце", unallocated: "дельта «Нераспределённого» не читается как уступка",
  absent_endpoint: "статьи нет в файле на одном из концов", file_total_unavailable: "итог файла не единогласен",
  non_positive_total: "итог одного из этапов неположителен — столбики не строятся",
  no_comparable_totals: "ни у одного этапа нет сопоставимого итога",
};
export const TAX_LABEL = { gross: (rate: string) => `Все суммы — с НДС ${rate} %, ставка одна во всех выбранных этапах`,
  net: (rates: string) => `Все суммы — без НДС: ставки этапов расходятся (${rates})`,
  none: "Сопоставимых сумм нет: база НДС неизвестна у всех выбранных этапов" };
```

Рендер — **по данным**: ни одно поле не выводится клиентом из другого (§2.14, Global 13). Состав экрана — макет; `SummaryCell` рисует по `state`/`change`/`rows`/`amount_unavailable_reason`; трасса — блоки высотой `bar_height_pct`%, при `track.available === false` — `EmptyState` с `REASON_LABEL[track.reason]`; колонка `unknown_vat_base` — штрихованный слот (`bg-[repeating-linear-gradient(...)]` на токенах) без числа; раскрытие детей — `useState<Set<number>>`, кнопка с `aria-expanded`; правые колонки «Торг», «Вклад»; «Нераспределённое» и «Итого» — `<tfoot>`; под шапкой колонки — `manual_overrides` («разнос: N решений · дата» / «без ручного разноса»); в «Итого» — три состояния `convergence`; значок неполноты `◐` в `Tooltip` с текстом «учтено X из N строк» и, при `rows_not_finite > 0`, «неконечных значений: k»; на нетто-оси подпись у «Итого»: «Δ сходимости измерена в исходных деньгах файла». Таблица в `Surface padding="none" className="overflow-x-auto"`, первая колонка `sticky left-0`.

- [ ] **Step 1: Тесты страницы и таблицы (падают)**

```tsx
// StageSummaryPage.test.tsx
function renderSummary(route = "/tenders/300/summary?offers=7002&offers=7001&offers=7004", opts = {}) {
  return renderWithProviders(
    <Routes><Route path="/tenders/:tenderId/summary" element={<StageSummaryPage />} /></Routes>,
    { initialRoute: route, ...opts });
}

describe("Свод по этапам — страница (спека §2.2, §2.11, §2.14)", () => {
  it("шапка: участник, «3 из 3», подпись валовой оси и номинального уровня", async () => {
    renderSummary();
    expect(await screen.findByRole("heading", { name: /Свод по этапам · ООО Альфа/ })).toBeInTheDocument();
    expect(screen.getByText(/этапов в своде 3 из 3/)).toBeInTheDocument();
    expect(screen.getByText(/Все суммы — с НДС 20 %/)).toBeInTheDocument();
    expect(screen.getByText(/номинальные/i)).toBeInTheDocument();
  });

  it("трасса: высота столбика — из bar_height_pct, а не из сравнения сумм клиентом", async () => {
    renderSummary();
    const bars = await screen.findAllByTestId("track-bar");
    expect(bars.map((b) => b.style.height)).toEqual(["100%", "66.7%", "50%"]);
  });

  it("track.available=false → объяснение вместо столбиков, таблица остаётся", async () => {
    handlerState.stageSummaryOutcome = "track_unavailable";
    renderSummary();
    expect(await screen.findByText(/столбики не строятся/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("track-bar")).toHaveLength(0);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("колонка unknown_vat_base: слот без числа, ячейки без сумм с причиной, ось остаётся валовой", async () => {
    handlerState.stageSummaryOutcome = "unknown_vat";
    renderSummary();
    await screen.findByRole("table");
    expect(screen.getByTestId("track-slot-unknown")).toBeInTheDocument();
    expect(screen.getByText(/Все суммы — с НДС 20 %/)).toBeInTheDocument();
    const six = screen.getByText("Фасадные работы").closest("tr") as HTMLElement;
    expect(within(six).getAllByRole("cell")[2]).toHaveTextContent("—");
    expect(within(six).getAllByRole("cell")[2]).toHaveAttribute("title", expect.stringMatching(/база НДС неизвестна/));
  });

  it.each([
    ["single_participant", /по одному участнику/],
    ["offer_not_found", /не найдено/],
    ["too_few_offers", /хотя бы два/],
  ])("отказ %s → пустое состояние с причиной и ссылкой на решётку", async (outcome, text) => {
    handlerState.stageSummaryOutcome = outcome as typeof handlerState.stageSummaryOutcome;
    renderSummary();
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /К решётке тендера/ })).toHaveAttribute("href", "/tenders/300");
  });

  it("без ?offers — пустое состояние без запроса", async () => {
    renderSummary("/tenders/300/summary");
    expect(await screen.findByText(/Выберите предложения на решётке/)).toBeInTheDocument();
  });
});
```

```tsx
// StageSummaryTable.test.tsx — рендер с sampleStageSummary напрямую, без сети
describe("Таблица свода — состояния по данным (спека §2.5–§2.7, §2.9, §2.13)", () => {
  it("порядок строк — как пришёл с сервера; «Нераспределённое» и «Итого» в tfoot", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const bodyRows = within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
    expect(bodyRows[0]).toHaveTextContent("Котлован");          // |−60| > |−30|
    const foot = within(screen.getAllByRole("rowgroup")[2]).getAllByRole("row");
    expect(foot[0]).toHaveTextContent("Нераспределённое");
    expect(foot[1]).toHaveTextContent("Итого по предложению");
  });

  it.each([
    ["removed", "снято"], ["not_evaluated", "не оценивалась"], ["absent", "—"],
  ])("state=%s рисует «%s» — по полю, а не по сумме", (state, label) => {
    const cell = { ...sampleStageSummary.rows[0].cells[0], state, amount: "999.00" } as StageSummaryCell;
    render(<table><tbody><tr><SummaryCell cell={cell} /></tr></tbody></table>);
    expect(screen.getByRole("cell")).toHaveTextContent(label);
    expect(screen.getByRole("cell")).not.toHaveTextContent("999");
  });

  it.each([
    ["appeared", "появилась"], ["reappeared", "вернулась"], ["disappeared", "нет в файле"], ["removed", "снято"],
  ])("change.kind=%s подписан «%s»", (kind, label) => {
    const cell = { ...sampleStageSummary.rows[0].cells[1], state: "amount", amount: "5.00",
      change: { kind, value: null, direction: null, reason: null } } as StageSummaryCell;
    render(<table><tbody><tr><SummaryCell cell={cell} /></tr></tbody></table>);
    expect(screen.getByRole("cell")).toHaveTextContent(label);
  });

  it("direction трёх состояний: up/down/flat — знак и тон, flat не окрашен как рост", () => {
    for (const [direction, value, cls] of [["up", "+5.0", "text-accent-primary-text"], ["down", "-5.0", "text-danger-text"], ["flat", "0.0", "text-fg-tertiary"]] as const) {
      const cell = { ...sampleStageSummary.rows[0].cells[1], change: { kind: "percent", value, direction, reason: null } } as StageSummaryCell;
      const { unmount } = render(<table><tbody><tr><SummaryCell cell={cell} /></tr></tbody></table>);
      expect(screen.getByTestId("change")).toHaveClass(cls);
      unmount();
    }
  });

  it("неполнота: значок и подсказка «учтено X из N строк», отдельно неконечные", async () => {
    const cell = { ...sampleStageSummary.rows[0].cells[0], rows: { row_count: 40, rows_with_amount: 37, rows_not_finite: 1 } };
    render(<table><tbody><tr><SummaryCell cell={cell} /></tr></tbody></table>);
    expect(screen.getByLabelText(/учтено 37 из 40 строк.*неконечных значений: 1/)).toBeInTheDocument();
  });

  it("сходимость трёх состояний в «Итого»", () => {
    const s = structuredClone(sampleStageSummary);
    s.columns[1].convergence = { ...s.columns[1].convergence, converged: false, delta: "-10.00" };
    s.columns[2].convergence = { categories_sum: "90.00", file_total: null, converged: null, delta: null, reason: "file_total_unavailable" };
    render(<StageSummaryTable summary={s} />);
    const total = screen.getByText("Итого по предложению").closest("tr") as HTMLElement;
    expect(total).toHaveTextContent("сходится");
    expect(total).toHaveTextContent("не сходится: Δ −10,00");
    expect(total).toHaveTextContent("сверка невозможна: итог файла не единогласен");
  });

  it("раскрытие статьи показывает детей и помечается aria-expanded", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const toggle = screen.getByRole("button", { name: /Раскрыть Фасадные работы/ });
    expect(screen.queryByText("Прочее (фасады)")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Прочее (фасады)")).toBeInTheDocument();
  });

  it("подпись разноса под шапкой колонки", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    expect(screen.getByText(/разнос: 1 решение · 26\.08\.2026/)).toBeInTheDocument();
    expect(screen.getAllByText(/без ручного разноса/)).toHaveLength(2);
  });
});
```

```ts
// summaryTokens.test.ts — по образцу chartTokens.test.ts
import { readFileSync } from "node:fs";
import page from "./StageSummaryPage.tsx?raw";
import table from "./StageSummaryTable.tsx?raw";
import track from "./StageSummaryTrack.tsx?raw";
import cell from "./SummaryCell.tsx?raw";

it("каждая var(--…) компонентов свода объявлена в index.css", () => {
  const css = readFileSync(new URL("../../../index.css", import.meta.url), "utf8");
  const declared = new Set([...css.matchAll(/--([a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  const used = new Set([page, table, track, cell].flatMap((s) => [...s.matchAll(/var\(--([a-z0-9-]+)/g)].map((m) => m[1])));
  expect(used.size).toBeGreaterThan(0);          // предпосылка: токены вообще используются
  expect([...used].filter((t) => !declared.has(t))).toEqual([]);
});
```

- [ ] **Step 2: Прогнать — падают на отсутствии модулей**

- [ ] **Step 3: Компоненты**

`StageSummaryPage`: `useParams` → `tenderId`; `useSearchParams().getAll("offers").map(Number).filter(Number.isFinite)`; без offers → `EmptyState title="Свод не построен" description="Выберите предложения на решётке тендера"` со ссылкой; `useStageSummary`; `isPending` → `Skeleton`; `isError` → `EmptyState` с `REASON`-текстом по `apiErrorCode` (карта кодов отказа → текст: `offer_not_found` «Предложение не найдено в этом тендере», `too_few_offers` «Для свода нужны хотя бы два этапа», `one_offer_per_round` «В одном раунде — одно предложение», `single_participant` «Выбранные предложения принадлежат разным участникам — свод строится по одному участнику», `offer_has_no_estimate` «У предложения нет сметы: раунд был заменён другим файлом») и `Button render={<Link to={`/tenders/${tenderId}`}>}` «К решётке тендера»; успех → `Breadcrumbs` (Тендеры → номер → Свод по этапам), `PageHeader serif title={`Свод по этапам · ${participant.title}`} subtitle={`${tender.title} · ${tender.object_title} · этапов в своде ${kpi.stages_selected} из ${kpi.stages_loaded}`}`, ряд `KpiCard` ×5, подпись осей (`TAX_LABEL[display.tax_basis]`, «Цены номинальные, без приведения к ценовому уровню месяца»), `StageSummaryTrack`, `StageSummaryTable`.

`StageSummaryTrack`: `summary.track.available ? columns.map(bar) : <EmptyState …>`; столбик — `<div data-testid="track-bar" style={{ height: `${Number(col.bar_height_pct)}%` }} />` (число только для CSS-высоты — не сравнение и не деление); колонка с `vat_state === "unknown_vat_base"` — `<div data-testid="track-slot-unknown" className="… bg-[repeating-linear-gradient(45deg,var(--border-subtle)_0_4px,transparent_4px_8px)]" title={REASON_LABEL.unknown_vat_base} />`; под столбиком `formatDecimalMoney(col.total)` и `ChangeBadge change={col.total_change}`.

`SummaryCell({cell})`: `<td className="text-right tabular-nums" title={cell.amount_unavailable_reason ? REASON_LABEL[...] : undefined}>`; если `amount_unavailable_reason` → «—»; иначе по `state`: `amount` → `formatDecimalMoney(cell.amount)`, прочие → `STATE_LABEL[state]` пилюлей (`StatusPill tone="warning"` для `removed`, `neutral` для `not_evaluated`); под числом `ChangeBadge` (`data-testid="change"`, тон по `direction`: `up → text-accent-primary-text`, `down → text-danger-text`, `flat → text-fg-tertiary`; `kind ∈ KIND_LABEL` → пилюля с подписью; `none` → ничего); значок неполноты при `rows_with_amount < row_count`: `<Tooltip><TooltipTrigger aria-label={`Сумма неполна: учтено ${rows_with_amount} из ${row_count} строк${rows_not_finite ? `; неконечных значений: ${rows_not_finite}` : ""}`}>◐</TooltipTrigger><TooltipContent>…</TooltipContent></Tooltip>`. Процент — `roundDecimalPercent(value).text`; `abs_only` — `formatDecimalMoney(value)` с подписью «Δ, без %».

`StageSummaryTable({summary})`: `<Table>` из shadcn в `Surface padding="none" className="overflow-x-auto"`; `thead` — «Статья классификатора» (`sticky left-0 bg-surface`), по колонке `Этап {stage_no}{label ? ` · ${label}` : ""}` и под ней `manual_overrides.count ? `разнос: ${count} ${plural} · ${formatDate(last_at)}` : "без ручного разноса"`, затем «Торг: первый → последний», «Вклад в итог»; `tbody` — строки в порядке ответа, у строки с `children.length > 0` кнопка `aria-label={`Раскрыть ${title}`}` и `aria-expanded`; дети — `pl-8 bg-surface-sunken`; `tfoot` — `unallocated` (бейдж «обязательная строка», `bargain` → «без %» с title `REASON_LABEL.unallocated`, `contribution.value` числом) и «Итого по предложению» с `total.cells` и под каждым — сходимость: `converged === true` «сходится», `false` `не сходится: Δ ${formatDecimalMoney(delta)}`, `null` `сверка невозможна: ${REASON_LABEL[reason]}`; при `display.tax_basis === "net"` — строка под таблицей «Δ сходимости измерена в исходных деньгах файла».

Маршрут в `App.tsx` после `/tenders/:tenderId`: `<Route path="/tenders/:tenderId/summary" element={<StageSummaryPage />} />` с импортом `StageSummaryPage from "@/pages/tenders/summary/StageSummaryPage"`.

- [ ] **Step 4: Прогнать всё фронтовое; `tsc -b`, eslint**

Run: `cd frontend && npx tsc -b --noEmit && npx eslint src/pages/tenders && npx vitest run src/pages/tenders src/services`
Expected: PASS. `only-export-components`: константы — только в `cellCopy.ts`.

- [ ] **Step 5: Снятие защиты — рендер по данным**

В `SummaryCell` временно выводить «снято» из `amount === "0.00"` вместо `state` → тест `state=%s рисует … по полю, а не по сумме` красный. Вернуть.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/pages/tenders/summary frontend/src/App.tsx
git commit -m "feat(stage-summary): страница свода — шапка, оси, трасса, таблица состояний по данным, маршрут"
```

---

## Task 9: Финал — `just ci`, стенд, замеры, снимки, devlog, ссылки рамки, PR

**Files:**
- Create: `docs/devlog/2026-08-27-stage-summary.md`
- Modify: `docs/proposals/2026-08-25-tenders-model.md` (ссылки на devlog и PR)

- [ ] **Step 1: `just ci`** — код возврата 0, числа прошедших записать (бэкенд было 2225/6, фронтенд 686).

- [ ] **Step 2: Стенд `gca_dev`** (`just dev-backend` :8259, `just dev-frontend`; учётка стенда — в памяти проекта, не в доке). Playwright — отдельный npm-проект в scratchpad, `channel: "chrome"`, тема через `localStorage.theme` + reload. Проверить и записать **счётчиками и дайджестами, без имён и сумм**:
  - свод участника с четырьмя раундами: `converged = true` у каждой колонки (DoD 3);
  - `additional_works_amount` ненулевой хотя бы у одной статьи;
  - у участника с непустым «Нераспределённым» строка непуста;
  - исключение среднего раунда меняет проценты соседей и подпись «N из M»;
  - ручное решение на смете предложения → подпись разноса под колонкой;
  - карточка без выбора — те же ячейки и «Итого с НДС», что до фичи.

- [ ] **Step 3: Замер раскладки** (DoD 4): на стенде только 4 раунда — для 8 и 10 колонок временно отдать ответ с 10 колонками через msw-фикстуру в dev-режиме **либо** через Playwright `page.route` подменить ответ `stage-summary` на фикстуру с 10 колонками; окна 1280 и 1100, все статьи раскрыты, обе темы: `scrollWidth > clientWidth` у контейнера таблицы, `document.body.scrollWidth <= innerWidth`, первая колонка `getBoundingClientRect().left` неизменна при прокрутке контейнера. Числа — в devlog.

- [ ] **Step 4: Снимки всего экрана в обеих темах** (DoD 5): решётка с выбором и недоступными строками; свод; свод с раскрытой статьёй; после наведения на ◐; пустое состояние отказа. Расхождения с макетом — правка либо запись в долг с причиной.

- [ ] **Step 5: devlog** — по образцу `docs/devlog/2026-08-26-tenders-contour.md`: задачи и коммиты; отступления от плана; грабли; соответствие «требование спеки → тест» списком (DoD 2), границы (DoD 7: смешанные ставки и `unknown_vat_base` — фикстурами; `too_few_offers` — сервер); замеры стенда и раскладки; `just ci`.

- [ ] **Step 6: Рамка** — в разделе «Фича 3» заменить «план — после гейта 3 · devlog и PR — на финале» ссылками на план, devlog и номер PR (отдельный docs-коммит).

- [ ] **Step 7: PR** `feat/stage-summary → main` со ссылками на спеку и план; после мержа — ветку удалить.

---

## Соответствие DoD спеки задачам

| DoD | Задачи |
|---|---|
| 1. `just ci` зелёный | 9 |
| 2. «требование → тест» списком | 9 (devlog), опора — 1–8 |
| 3. Стенд: сходимость, допработы, «Нераспределённое», исключение раунда, разнос | 9; те же свойства фикстурой — 4 |
| 4. Замер раскладки 8/10 колонок | 9 |
| 5. Снимки обеих тем, состояния взаимодействия | 9 |
| 6. Карточка без выбора сохраняет факты | 7 (тест «без выбора карточка сохраняет факты») |
| 7. Смешанные ставки и `unknown_vat_base` фикстурами, граница в devlog | 2, 4, 6, 8; 9 |

Требования спеки без DoD-номера: §2.3 отказы — 3, 5; §2.4 ветви и `rows` — 2, 4; §2.5–§2.7 — 1, 4; §2.8 три случая — 2, 4, 8; §2.9 сходимость и источник итога — 2, 4, 8; §2.10 разнос — 4, 8; §2.11 KPI/трасса — 2, 4, 8; §2.12 запросы — 3; §2.13 сортировка — 2, 8; §2.14 видимость детей — 2, 8; §2.16 форма — 3, 5, 6.

## Порядок и точки ревью

Строго 1 → 9, ревью после каждой задачи (`per-task-review-is-mandatory`); негативные проверки и снятия защит не делегируются. Отдельные точки внимания: после Task 2 — снятия защит видимости и валовой сходимости; после Task 3 — пять снятий отказов и постоянство числа запросов; после Task 7 — старые тесты карточки не меняют утверждений о фактах; после Task 8 — снятие «рендер по данным».

# ProjectPage Load Perf — PR-2: переиспользование расчёта + честный лоадер

> **Исполнение:** в superpowers-совместимом харнессе — через superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans, задача за задачей. В иных харнессах (напр. Codex) — исполнять шаги по чекбоксам напрямую; sub-skill необязателен.

**Goal:** Убрать второй последовательный прогон `compute_calculations` на первой загрузке (summary отдаёт готовые calc-rows, фронт переиспользует их через `initialData`) и заменить молчаливую деградацию при ошибке summary на честное состояние ошибки с «Повторить».

**Architecture:** Бэкенд `/summary` начинает возвращать поле `calculations` (уже посчитанные строки, общий сериализатор с `/calculations`). Фронт в `useDashboardCalculations` через `initialData` читает эти строки из кэша summary → на первой отрисовке запрос `/calculations` не уходит. Состояния страницы выстроены в явную лестницу; сетевая ошибка summary больше не маскируется под пустой проект.

**Tech Stack:** Backend — FastAPI, SQLAlchemy (sync). Frontend — React 19, TS strict, TanStack Query v5, Vitest + MSW, shadcn/ui. Команды — через `just`.

**Spec:** `docs/superpowers/specs/2026-07-20-projectpage-load-perf-design.md` §1, §2, §4.

## Global Constraints

- Поставка одним PR (бэк+фронт), main не в сломанном состоянии.
- Поле `calculations` в ответе summary — форма строки **идентична** `/dashboard/calculations` (общий сериализатор).
- Порядок строк `compute_calculations` не детерминирован — тесты равенства сортируют обе стороны.
- TS strict: `projectId: ID | null` не передаётся в функции, ожидающие `ID`, без явного гварда/`as ID`.
- Docstring у каждой функции/метода (включая тесты).
- Команды проекта — **только через `just`** (AGENTS.md), никаких прямых `npx`/`cd backend`. Точечный фронт-прогон — через рецепт `just test-frontend-file <path>` (добавляется в Task 5). Каждую проверку запускать **отдельной** командой без `| tail`/`&&`/`;` — иначе код возврата берётся от `tail`/последней команды и упавший тест выглядит зелёным.
- Windows shell: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd>"`.
- Перед завершением PR: `just lint`, затем **отдельно** `just test`.

---

## File Structure

- Modify: `backend/routers/dashboard.py` — хелпер `_serialize_calc_row`, поле `calculations` в `/summary`.
- Create: `backend/tests/unit/test_calc_serialize.py` — юнит-тест сериализатора.
- Create: `backend/tests/integration/test_summary_calculations.py` — интеграционные тесты поля + инвариант.
- Modify: `frontend/src/types/dashboard.ts` — опциональное поле `calculations?`.
- Modify: `frontend/src/test/fixtures.ts` — фикстуры summary с calc-rows.
- Modify: `justfile` — рецепт `test-frontend-file` (точечный фронт-прогон через `just`).
- Modify: `frontend/src/services/queries.ts` — `initialData` в `useDashboardCalculations`.
- Create: `frontend/src/services/useDashboardCalculations.test.tsx` — хук-тесты посева.
- Modify: `frontend/src/pages/ProjectPage.tsx` — цельный skeleton + лестница состояний + состояния ошибки.
- Modify: `frontend/src/pages/ProjectPage.test.tsx` — тесты лоадера/ошибки + инверсия :1059.

---

## Task 1: Backend — общий сериализатор `_serialize_calc_row`

Тело dict-строки сейчас заинлайнено в `/calculations` ([dashboard.py:360-382](../../../backend/routers/dashboard.py)). Выносим в хелпер, чтобы `/summary` (Task 2) отдавал байт-в-байт ту же форму.

**Files:**
- Modify: `backend/routers/dashboard.py`
- Create: `backend/tests/unit/test_calc_serialize.py`

**Interfaces:**
- Produces: `_serialize_calc_row(r: dict) -> dict` в `routers/dashboard.py` — принимает строку из `compute_calculations` (с `date`-объектами в `period_start`/`period_end`), возвращает JSON-сериализуемый dict с ключами: `project_id, material_class_id, material_class_name, direction, period_start, period_end, material_total, delivery_total, total_qty, avg_price, unit_symbol, dimension_mismatch, invoice_count, reference_price, deviation_pct, deviation_amount, corridor_pct, compensation_per_unit, compensation_amount`.

- [ ] **Step 1: Написать падающий юнит-тест**

Create `backend/tests/unit/test_calc_serialize.py`:

```python
"""Юнит-тест сериализатора строки расчёта — общая форма для /summary и /calculations."""
from datetime import date
from decimal import Decimal

from routers.dashboard import _serialize_calc_row


def _raw_row() -> dict:
    """Минимальная сырая строка compute_calculations (date-объекты, Decimal)."""
    return {
        "project_id": 1, "material_class_id": 10, "material_class_name": "В25",
        "direction": "concrete", "period_start": date(2026, 1, 1), "period_end": date(2026, 1, 31),
        "material_total": Decimal("100000"), "delivery_total": Decimal("0"),
        "total_qty": Decimal("10"), "avg_price": Decimal("9600"), "unit_symbol": "м³",
        "dimension_mismatch": False, "invoice_count": 1, "reference_price": None,
        "deviation_pct": None, "deviation_amount": None, "corridor_pct": None,
        "compensation_per_unit": None, "compensation_amount": None,
    }


def test_serialize_calc_row_isoformats_dates():
    """period_start/end сериализуются в ISO-строки."""
    out = _serialize_calc_row(_raw_row())
    assert out["period_start"] == "2026-01-01"
    assert out["period_end"] == "2026-01-31"


def test_serialize_calc_row_keys_stable():
    """Набор ключей фиксирован — контракт формы для обоих эндпоинтов."""
    out = _serialize_calc_row(_raw_row())
    assert set(out.keys()) == {
        "project_id", "material_class_id", "material_class_name", "direction",
        "period_start", "period_end", "material_total", "delivery_total", "total_qty",
        "avg_price", "unit_symbol", "dimension_mismatch", "invoice_count",
        "reference_price", "deviation_pct", "deviation_amount", "corridor_pct",
        "compensation_per_unit", "compensation_amount",
    }
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Юнит-тест без БД — рецепт `test-unit-k` гоняет `pytest tests/unit -k`:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k calc_serialize"
```
Expected: FAIL — `ImportError: cannot import name '_serialize_calc_row'`.

- [ ] **Step 3: Реализовать хелпер и применить в `/calculations`**

В `backend/routers/dashboard.py` добавить функцию (над `list_calculations`):

```python
def _serialize_calc_row(r: dict) -> dict:
    """JSON-форма строки compute_calculations — единый контракт /summary и /calculations."""
    return {
        "project_id": r["project_id"],
        "material_class_id": r["material_class_id"],
        "material_class_name": r["material_class_name"],
        "direction": r["direction"],
        "period_start": r["period_start"].isoformat(),
        "period_end": r["period_end"].isoformat(),
        "material_total": r["material_total"],
        "delivery_total": r["delivery_total"],
        "total_qty": r["total_qty"],
        "avg_price": r["avg_price"],
        "unit_symbol": r["unit_symbol"],
        "dimension_mismatch": r["dimension_mismatch"],
        "invoice_count": r["invoice_count"],
        "reference_price": r["reference_price"],
        "deviation_pct": r["deviation_pct"],
        "deviation_amount": r["deviation_amount"],
        "corridor_pct": r["corridor_pct"],
        "compensation_per_unit": r["compensation_per_unit"],
        "compensation_amount": r["compensation_amount"],
    }
```

Заменить `return [ {...} for r in rows ]` в конце `list_calculations` на:

```python
    return [_serialize_calc_row(r) for r in rows]
```

- [ ] **Step 4: Запустить — юнит зелёный, регресс /calculations не сломан**

Две отдельные команды (не через `&&` — код возврата не должен маскироваться):
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k calc_serialize"
```
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-local"
```
Expected: обе PASS (юнит + существующие integration `test_dashboard*`, `test_calculations*`).

- [ ] **Step 5: Commit**

```
git add backend/routers/dashboard.py backend/tests/unit/test_calc_serialize.py
git commit -m "refactor(dashboard): вынести _serialize_calc_row — общая форма /summary и /calculations"
```

---

## Task 2: Backend — `/summary` возвращает `calculations`

`/summary` уже считает `calc_rows` за полный период ([dashboard.py:225](../../../backend/routers/dashboard.py)). Добавляем их сериализацию в ответ.

**Files:**
- Modify: `backend/routers/dashboard.py` — return-dict `get_project_summary`.
- Create: `backend/tests/integration/test_summary_calculations.py`

**Interfaces:**
- Consumes: `_serialize_calc_row` (Task 1), `calc_rows` (локальная переменная в `get_project_summary`).
- Produces: ключ `"calculations": list[dict]` в JSON `/dashboard/summary`.

- [ ] **Step 1: Написать падающий интеграционный тест**

Create `backend/tests/integration/test_summary_calculations.py`:

```python
"""Интеграционные тесты: /summary отдаёт calc-rows и они совпадают с /calculations."""
from datetime import date


def _sort(rows: list[dict]) -> list[dict]:
    """Сортировка обеих сторон — порядок compute_calculations не детерминирован."""
    return sorted(rows, key=lambda r: (r["period_start"], r["material_class_id"]))


def test_summary_includes_calculations(client, factories):
    """Проект с данными: summary['calculations'] непуст и совпадает с /calculations."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base", name="В25")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10), vat_rate=20.0)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    calc = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()

    assert len(summary["calculations"]) > 0
    assert _sort(summary["calculations"]) == _sort(calc)


def test_summary_calculations_empty_for_project_without_invoices(client, factories):
    """Пустой проект: calculations == []."""
    project = factories.ProjectFactory.create()
    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert summary["calculations"] == []
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-local-k summary_calculations"
```
Expected: FAIL — `KeyError: 'calculations'`.

- [ ] **Step 3: Добавить поле в ответ summary**

В `backend/routers/dashboard.py`, в return-dict функции `get_project_summary` добавить ключ (рядом с `directions`):

```python
        "calculations": [_serialize_calc_row(r) for r in calc_rows],
```

(`calc_rows` уже определён выше как `list[dict]` — пуст, если нет счетов.)

- [ ] **Step 4: Запустить — зелёные**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-local-k summary_calculations"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/routers/dashboard.py backend/tests/integration/test_summary_calculations.py
git commit -m "feat(dashboard): /summary возвращает готовые calc-rows (устранение второго прогона на фронте)"
```

---

## Task 3: Backend — тест-страж инварианта периода (исключённый поставщик на краю)

summary считает период по **нефильтрованным** границам, `/calculations` — по границам **с учётом** исключений. Строж фиксирует, что выход всё равно идентичен.

**Files:**
- Modify: `backend/tests/integration/test_summary_calculations.py`

**Interfaces:**
- Consumes: `_sort` (хелпер из Task 2 Step 1, тот же файл), модель `ProjectSupplierExclusion`.

- [ ] **Step 1: Написать тест-страж**

Добавить в `backend/tests/integration/test_summary_calculations.py`:

```python
def test_summary_calculations_equals_endpoint_with_excluded_edge_supplier(client, db_session, factories):
    """Исключённый поставщик держит самую раннюю дату → границы периода summary
    (нефильтрованные) и /calculations (с исключениями) различаются, но выход идентичен
    (пустые месяцы пропускаются через continue)."""
    from models import ProjectSupplierExclusion

    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base", name="В25")

    excluded = factories.SupplierFactory.create()
    kept = factories.SupplierFactory.create()

    # Исключённый поставщик — самый ранний счёт (край диапазона).
    doc_e = factories.DocumentFactory.create(project=project)
    inv_e = factories.InvoiceFactory.create(
        document=doc_e, supplier_id=excluded.id, date=date(2026, 1, 5), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_e, material_class=mc, item_type="material",
        quantity=10.0, unit_price=5000.0, amount=50000.0, vat_amount=10000.0,
    )
    # Оставленный поставщик — позже.
    doc_k = factories.DocumentFactory.create(project=project)
    inv_k = factories.InvoiceFactory.create(
        document=doc_k, supplier_id=kept.id, date=date(2026, 3, 10), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_k, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    db_session.add(ProjectSupplierExclusion(project_id=project.id, supplier_id=excluded.id))
    db_session.commit()

    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    calc = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()

    assert _sort(summary["calculations"]) == _sort(calc)
    # Санити: остался только оставленный поставщик (январь исключён и пропущен).
    assert all(r["period_start"] >= "2026-03-01" for r in summary["calculations"])
```

- [ ] **Step 2: Запустить — зелёный (страж проходит на корректной реализации)**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-local-k summary_calculations"
```
Expected: PASS. (Если FAIL — значит равенство «по построению» нарушено; это находка, а не флак.)

- [ ] **Step 3: Commit**

```
git add backend/tests/integration/test_summary_calculations.py
git commit -m "test(dashboard): страж инварианта summary.calculations == /calculations (исключённый поставщик на краю)"
```

---

## Task 4: Frontend — опциональное поле типа + фикстуры

**Files:**
- Modify: `frontend/src/types/dashboard.ts`
- Modify: `frontend/src/test/fixtures.ts`

**Interfaces:**
- Produces: `DashboardSummary.calculations?: DashboardCalculation[]`; фикстуры `sampleCalcRowConcrete`, `sampleCalcRowRebar`, `sampleCalcRowOther`, `sampleSummaryMultiWithCalcs`, `sampleSummaryMonoWithCalcs`.

- [ ] **Step 1: Добавить опциональное поле в тип**

В `frontend/src/types/dashboard.ts`, в интерфейс `DashboardSummary` добавить (после `directions`):

```ts
  /** Готовые строки расчёта за полный период (без direction-фильтра). Опционально:
   * старый бэкенд поля не отдаёт → фронт фолбэчит на сетевой /calculations (§2). */
  calculations?: DashboardCalculation[];
```

- [ ] **Step 2: Добавить фикстуры**

В `frontend/src/test/fixtures.ts` добавить (после существующих summary-фикстур; тип — `DashboardCalculation`):

```ts
import type { DashboardCalculation, DashboardSummary } from "@/types/dashboard";

const _calcBase = {
  period_start: "2026-01-01", period_end: "2026-01-31",
  reference_price: null, deviation_pct: null, deviation_amount: null,
  corridor_pct: null, compensation_per_unit: null, compensation_amount: null,
  material_total: 100000, delivery_total: 0, total_qty: 10, invoice_count: 1,
};

export const sampleCalcRowConcrete: DashboardCalculation = {
  ..._calcBase, material_class_id: 10, material_class_name: "В25",
  direction: "concrete", avg_price: 9600,
};
export const sampleCalcRowRebar: DashboardCalculation = {
  ..._calcBase, material_class_id: 20, material_class_name: "А500С Ø12",
  direction: "rebar", avg_price: 62000,
};
/** Строка типа other — для проверки, что моно-фильтр её отсекает. */
export const sampleCalcRowOther: DashboardCalculation = {
  ..._calcBase, material_class_id: 99, material_class_name: "Прочий материал",
  direction: "other", avg_price: 1234,
};

/** Мульти-объект с готовыми calc-rows (для теста «0 вызовов /calculations»). */
export const sampleSummaryMultiWithCalcs: DashboardSummary = {
  ...sampleDashboardSummaryMulti,
  calculations: [sampleCalcRowConcrete, sampleCalcRowRebar],
};
/** Моно-объект: в calc-rows есть чужая other-строка, которую фильтр обязан отсечь. */
export const sampleSummaryMonoWithCalcs: DashboardSummary = {
  ...sampleDashboardSummary,
  calculations: [sampleCalcRowConcrete, sampleCalcRowOther],
};
```

Если `sampleDashboardSummary`/`sampleDashboardSummaryMulti` не типизированы как `DashboardSummary` — не приводить их к типу насильно, но новые `sample*WithCalcs` типизировать явно (спред совместим).

- [ ] **Step 3: Проверить typecheck**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend"
```
Expected: без ошибок (фикстуры соответствуют `DashboardCalculation`/`DashboardSummary`).

- [ ] **Step 4: Commit**

```
git add frontend/src/types/dashboard.ts frontend/src/test/fixtures.ts
git commit -m "feat(types): DashboardSummary.calculations? + фикстуры summary с calc-rows"
```

---

## Task 5: Frontend — `initialData` в `useDashboardCalculations`

**Files:**
- Modify: `frontend/src/services/queries.ts` — `useDashboardCalculations` (~318-332).
- Create: `frontend/src/services/useDashboardCalculations.test.tsx` — хук-тесты посева.

**Interfaces:**
- Consumes: `DashboardSummary.calculations?` (Task 4), `qk.dashboard.summary`/`qk.dashboard.calculations`, фикстуры `sampleSummary*WithCalcs` (Task 4).
- Produces: `useDashboardCalculations` с `initialData` из кэша summary.

Тесты — на уровне **хука** (`renderHook`), а не страницы: так проверяется контракт `initialData` напрямую (фильтр по direction, guard периода, backward-compat, счётчик сети), без хрупкой привязки к разметке (строки с `reference_price=null` уходят в склеенный футер DeviationChart — точный текст-матч там ненадёжен). Timing «observer до summary» уже подтверждён отдельным probe при выборе `initialData` (спека §2.1).

- [ ] **Step 1: Рецепт точечного фронт-прогона + падающие тесты хука**

Сначала добавить в `justfile` (рядом с `test-frontend`, ~строка 96) рецепт (правило «только через just»):

```makefile
# Точечный фронт-прогон одного файла
test-frontend-file file:
    cd frontend && npx vitest run {{file}}
```

Затем создать `frontend/src/services/useDashboardCalculations.test.tsx`:

```tsx
/** Тесты посева calc-rows из summary через initialData (§2). */
import { describe, it, expect } from "vitest";
import type { ReactNode } from "react";
import { render, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { qk } from "@/services/queryKeys";
import { useDashboardCalculations, useDashboardSummary } from "@/services/queries";
import {
  sampleSummaryMultiWithCalcs,
  sampleSummaryMonoWithCalcs,
  sampleDashboardSummaryMulti,
} from "@/test/fixtures";

describe("useDashboardCalculations: посев из summary (§2)", () => {
  /** Prod-like клиент: staleTime>0, иначе посев мгновенно протухнет (test util ставит 0). */
  function prodLikeClient(): QueryClient {
    return new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000, gcTime: Infinity } },
    });
  }

  /** Счётчик сетевых запросов к /calculations. */
  function countCalculations(): string[] {
    const seen: string[] = [];
    server.use(
      http.get("/api/dashboard/calculations", ({ request }) => {
        seen.push(request.url);
        return HttpResponse.json([]);
      }),
    );
    return seen;
  }

  /** Обёртка renderHook с заранее посеянным summary в кэше клиента. */
  function wrapperWithSummary(qc: QueryClient, summary: unknown) {
    qc.setQueryData(qk.dashboard.summary(1), summary);
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  }

  it("cold-lifecycle: пустой кэш → summary приходит → initialData гасит /calculations", async () => {
    // Настоящий сценарий §2.1: calc-observer создаётся disabled ДО прихода summary;
    // initialData переоценивается на ре-рендерах, пока нет данных, и подхватывает
    // summary при его резолве (permanentная версия probe). Ловит регресс, если
    // TanStack Query перестанет переоценивать initialData на стабильном ключе.
    const qc = prodLikeClient();
    const seen = countCalculations();
    server.use(
      http.get("/api/dashboard/summary", () => HttpResponse.json(sampleSummaryMultiWithCalcs)),
    );
    function Harness() {
      const s = useDashboardSummary(1);
      const c = useDashboardCalculations(1, undefined, undefined, undefined, { enabled: !!s.data });
      return <div data-ready={s.data ? "1" : "0"} data-calc={c.data ? "1" : "0"} />;
    }
    const { container } = render(<Harness />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    });
    await waitFor(() => expect(container.querySelector('[data-ready="1"]')).toBeTruthy());
    await waitFor(() => expect(container.querySelector('[data-calc="1"]')).toBeTruthy());
    expect(seen).toHaveLength(0); // /calculations не ушёл ни разу за весь lifecycle
  });

  it("дефолт (период не задан): данные из посева, /calculations НЕ уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    const { result } = renderHook(
      () => useDashboardCalculations(1, undefined, undefined, undefined),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMultiWithCalcs) },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(sampleSummaryMultiWithCalcs.calculations!.length);
    expect(seen).toHaveLength(0);
  });

  it("direction-фильтр: чужая other-строка отсечена, /calculations не уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    const { result } = renderHook(
      () => useDashboardCalculations(1, undefined, undefined, "concrete"),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMonoWithCalcs) },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.every((r) => r.direction === "concrete")).toBe(true);
    expect(result.current.data!.some((r) => r.material_class_name === "Прочий материал")).toBe(false);
    expect(seen).toHaveLength(0);
  });

  it("изменённый период: посев не применяется, /calculations уходит на сервер", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    renderHook(
      () => useDashboardCalculations(1, "2026-02-01", "2026-02-28", undefined),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMultiWithCalcs) },
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  });

  it("старый бэкенд без calculations: посева нет, /calculations уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    renderHook(
      () => useDashboardCalculations(1, undefined, undefined, undefined),
      { wrapper: wrapperWithSummary(qc, sampleDashboardSummaryMulti) }, // без поля calculations
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  });
});
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend-file src/services/useDashboardCalculations.test.tsx"
```
Expected: FAIL — cold-lifecycle и дефолт-тест видят ≥1 вызов `/calculations` / `data` undefined (посева ещё нет).

- [ ] **Step 3: Реализовать `initialData`**

В `frontend/src/services/queries.ts` заменить тело `useDashboardCalculations`. Добавить `useQueryClient` (уже импортирован) и `DashboardSummary` в импорты типов, если ещё нет:

```ts
export function useDashboardCalculations(
  projectId: ID | null,
  periodStart?: string,
  periodEnd?: string,
  direction?: string,
  options?: { enabled?: boolean },
) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: projectId
      ? qk.dashboard.calculations(projectId, periodStart, periodEnd, direction)
      : ["dashboard", "calculations", "none"],
    queryFn: () => dashboardApi.calculations(projectId as ID, periodStart, periodEnd, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
    // Переиспользуем calc-rows из уже загруженного summary: на первой отрисовке дефолтного
    // вида (период не задан) запрос не уходит. Изменённый период / старый бэк без поля /
    // projectId===null (query disabled) → undefined → сеть. Клиентский фильтр по direction
    // эквивалентен бэкенд-фильтру (применяется после аллокации). §2 спеки.
    initialData: () => {
      if (projectId === null || periodStart || periodEnd) return undefined;
      const s = qc.getQueryData<DashboardSummary>(qk.dashboard.summary(projectId));
      if (s?.calculations === undefined) return undefined;
      return direction
        ? s.calculations.filter((r) => r.direction === direction)
        : s.calculations;
    },
    initialDataUpdatedAt: () =>
      projectId === null
        ? undefined
        : qc.getQueryState(qk.dashboard.summary(projectId))?.dataUpdatedAt,
  });
}
```

Добавить импорт типа, если отсутствует: `import type { ... DashboardSummary } from "@/types/dashboard";` (проверить существующий импорт-блок из `@/types/dashboard` в queries.ts; если его нет — добавить).

- [ ] **Step 4: Запустить — зелёные**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend-file src/services/useDashboardCalculations.test.tsx"
```
Expected: PASS (5 тестов: cold-lifecycle + дефолт + фильтр + период + старый бэк).

- [ ] **Step 5: Прогнать ProjectPage-тесты — регресса нет**

Существующие тесты используют дефолтную summary-фикстуру без `calculations` → `initialData` фолбэчит на сеть, поведение не меняется.
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend-file src/pages/ProjectPage.test.tsx"
```
Expected: PASS.

- [ ] **Step 6: Commit**

Рабочее дерево содержит несвязанные изменения `justfile` (MinIO) — стадировать **только** hunk с рецептом `test-frontend-file` (`git add -p justfile`), не весь файл.

```
git add -p justfile
git add frontend/src/services/queries.ts frontend/src/services/useDashboardCalculations.test.tsx
git commit -m "perf(frontend): переиспользование summary.calculations через initialData + рецепт test-frontend-file"
```

---

## Task 6: Frontend — цельный skeleton + честная лестница состояний

Спека §4.1–4.2: пока грузится summary — **цельный** skeleton (шапка + KPI + контент, не разрозненные куски); ошибка summary/списка — состояние с «Повторить»; legacy — только для настоящего пустого проекта. Реализуем ранними возвратами в порядке лестницы: `projectsQ.isLoading` → `projectsQ.isError` → not-found → `summaryQ.isLoading` → `summaryQ.isError` → обычная страница.

> **Про `Button`:** ProjectPage импортирует `@/components/ui-domain/Button` ([:11](../../../frontend/src/pages/ProjectPage.tsx)), у которого проп `loading` **есть** (сам рисует `Loader2` + `disabled`, [Button.tsx:13](../../../frontend/src/components/ui-domain/Button.tsx)) и уже используется в этом файле (диалоги цен). `loading={q.isFetching}` — корректно, НЕ путать с base-ui `ui/button` (там `loading` нет).

> **Про `retry: 2` (спека §4.3):** сознательно **не добавляем** точечный `retry: 2` на `useDashboardSummary`. Глобальный `retry: 1` (App.tsx:31) уже покрывает транзиентные 5xx; per-query `retry: 2` переопределил бы `retry:false` тест-клиента и с экспоненциальным `retryDelay` замедлил/зафлачил бы error-тесты — выигрыш маргинальный. Оставляем глобальный `retry: 1` (это отклонение от §4.3 фиксируется здесь осознанно).

**Files:**
- Modify: `frontend/src/pages/ProjectPage.tsx` — `ProjectPageSkeleton` + ранние возвраты + разводка `isLegacy`.
- Modify: `frontend/src/pages/ProjectPage.test.tsx` — тесты лоадера/ошибки/ретрая + инверсия :1059.

**Interfaces:**
- Consumes: `projectsQ`, `summaryQ` (уже в компоненте), `Breadcrumbs`, `EmptyState`, `Button`, `Skeleton` (все импортированы).

- [ ] **Step 1: Тесты — цельный skeleton, ошибки, ретрай-цикл**

В `frontend/src/pages/ProjectPage.test.tsx` добавить `delay` в импорт из `msw`: `import { http, HttpResponse, delay, type JsonBodyType } from "msw";`. Заменить тест `"summary error: degrades to legacy tabs instead of infinite skeleton"` (~:1059) и добавить кейсы:

```tsx
    it("summary loading: цельный skeleton, без табов/switcher (§4.2)", async () => {
      server.use(
        http.get("/api/dashboard/summary", async () => {
          await delay("infinite");                 // держим загрузку summary
          return HttpResponse.json(sampleDashboardSummaryMulti);
        }),
      );
      renderProject();
      expect(await screen.findByTestId("project-page-skeleton")).toBeInTheDocument();
      expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
      expect(screen.queryByTestId("direction-switcher")).not.toBeInTheDocument();
    });

    it("summary error: состояние ошибки с «Повторить», НЕ legacy-табы", async () => {
      server.use(
        http.get("/api/dashboard/summary", () => new HttpResponse(null, { status: 500 })),
      );
      renderProject();
      expect(await screen.findByText("Не удалось загрузить сводку")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
      expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
    });

    it("summary 500 → «Повторить» → 200: нормальный экран", async () => {
      let calls = 0;
      server.use(
        http.get("/api/dashboard/summary", () => {
          calls += 1;
          return calls === 1
            ? new HttpResponse(null, { status: 500 })
            : HttpResponse.json(sampleDashboardSummaryMulti);
        }),
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByRole("button", { name: "Повторить" }));
      await screen.findByTestId("direction-switcher");   // сводка догрузилась
    });

    it("projects error: ошибка списка, НЕ «Объект не найден»", async () => {
      server.use(
        http.get("/api/projects", () => new HttpResponse(null, { status: 500 })),
      );
      renderProject();
      expect(await screen.findByText("Не удалось загрузить объекты")).toBeInTheDocument();
      expect(screen.queryByText("Объект не найден")).not.toBeInTheDocument();
    });
```

(Тест-клиент `createTestQueryClient` ставит `retry: false` — первый 500 сразу даёт ошибку, ретрай-цикл детерминирован. Кейс «empty object: legacy tabs» :1052 не трогаем — п.6 лестницы.)

- [ ] **Step 2: Запустить — убедиться, что падает**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend-file src/pages/ProjectPage.test.tsx"
```
Expected: FAIL на 4 новых кейсах (нет `project-page-skeleton`, текстов ошибок).

- [ ] **Step 3: Ранний возврат для ошибки списка объектов**

В `frontend/src/pages/ProjectPage.tsx` после блока `if (projectsQ.isLoading) {...}` (~611) и ПЕРЕД `if (!project || projectId === null) {...}` вставить:

```tsx
  if (projectsQ.isError) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Не удалось загрузить объекты"
          description="Проверьте соединение и повторите."
          action={
            <Button variant="secondary" loading={projectsQ.isFetching} onClick={() => projectsQ.refetch()}>
              Повторить
            </Button>
          }
        />
      </div>
    );
  }
```

- [ ] **Step 4: Компонент `ProjectPageSkeleton` + ранний возврат при загрузке summary**

В `frontend/src/pages/ProjectPage.tsx` добавить file-local компонент (рядом с `TabBarSlot`):

```tsx
/** Цельный скелетон страницы проекта на время загрузки summary (§4.2). */
function ProjectPageSkeleton() {
  return (
    <div className="container-page py-8 space-y-6" data-testid="project-page-skeleton">
      <Skeleton className="h-4 w-40" />       {/* breadcrumbs */}
      <Skeleton className="h-8 w-1/3" />       {/* заголовок */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
      <Skeleton className="h-[240px]" />       {/* контент */}
    </div>
  );
}
```

Ранний возврат — ПОСЛЕ проверки not-found (`if (!project ...)`), ДО деклараций handler-функций:

```tsx
  if (summaryQ.isLoading) {
    return <ProjectPageSkeleton />;
  }
```

- [ ] **Step 5: Ранний возврат при ошибке summary**

Сразу после блока загрузки (Step 4):

```tsx
  if (summaryQ.isError) {
    return (
      <div className="container-page py-8">
        <Breadcrumbs items={[{ label: "Объекты", to: "/projects" }, { label: project.name }]} />
        <div className="mt-6">
          <EmptyState
            title="Не удалось загрузить сводку"
            description="Данные объекта временно недоступны."
            action={
              <Button loading={summaryQ.isFetching} onClick={() => summaryQ.refetch()}>
                Повторить
              </Button>
            }
          />
        </div>
      </div>
    );
  }
```

(`project` здесь заведомо существует — прошли not-found; breadcrumbs дают навигацию.)

- [ ] **Step 6: Развязать `isLegacy` с ошибкой summary**

В `frontend/src/pages/ProjectPage.tsx` изменить резолв `direction` и `isLegacy` (~468-476). Ветки ошибки/загрузки summary теперь обрабатываются ранними возвратами (Steps 4-5), поэтому в основном рендере summary заведомо успешен:

```tsx
  // undefined = summary ещё не резолвился в этом кадре — НЕ 'all'.
  const direction: string | undefined =
    directions === undefined ? undefined
    : rawDirection === "all" ? "all"
    : directions.some((d) => d.code === rawDirection) ? (rawDirection as string)
    : directions.length === 1 ? directions[0].code
    : "all";

  // Legacy — ТОЛЬКО настоящий пустой проект (ADR #11); ошибка summary разведена выше.
  const isLegacy = directions !== undefined && directions.length === 0;
```

Удалить переменную `summaryFailed` (использовалась только в старой маскировке) — других её использований в файле нет. Существующая ветка контента `direction === undefined ? <skeleton>` становится недостижимой на успехе (summary уже загружен) — оставить как безвредный fallback, не трогать.

- [ ] **Step 7: Запустить весь файл фронта — зелёные**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend-file src/pages/ProjectPage.test.tsx"
```
Expected: PASS (4 новых кейса + существующие, включая `empty object: legacy tabs` :1052 — п.6 лестницы).

- [ ] **Step 8: Commit**

```
git add frontend/src/pages/ProjectPage.tsx frontend/src/pages/ProjectPage.test.tsx
git commit -m "fix(frontend): цельный skeleton + честная ошибка summary/списка с «Повторить» (лестница состояний §4)"
```

---

## Финальная проверка PR-2

- [ ] **Step 1: Финальный lint (отдельной командой)**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint"
```
Expected: PASS.

- [ ] **Step 2: Финальный полный тест (отдельной командой)**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test"
```
Expected: PASS (бэк + фронт), без маскировки кода возврата.

---

## Self-Review

- **Spec coverage §1:** `_serialize_calc_row` → Task 1; поле `calculations` в summary → Task 2; опциональный тип + фикстуры → Task 4; тест-страж инварианта → Task 3. ✓
- **Spec coverage §2:** `initialData` (форма из §2.2, guard projectId/period, фильтр direction, backward-compat) → Task 5 Step 3; хук-тесты «0 вызовов» / фильтр / изменённый период / старый бэк → Task 5 Step 1. ✓
- **Spec coverage §4:** цельный skeleton (`ProjectPageSkeleton` + ранний возврат), лестница состояний, разводка `isLegacy`, ошибка summary/списка с retry (`loading`), инверсия :1059, ретрай-цикл → Task 6. `retry: 2` из §4.3 сознательно исключён (заметка в Task 6). ✓
- **Placeholder scan:** нет — весь код и команды конкретны.
- **Type/name consistency:** `_serialize_calc_row` (Task 1) → Task 2; фикстуры `sampleSummary*WithCalcs` (Task 4) → Task 5 хук-тесты; `useDashboardCalculations` с `initialData` (Task 5) — предмет хук-тестов Task 5.
- **Exit-code честность:** проверки — отдельные команды без `| tail`/`&&`; финал — `just lint` и `just test` раздельно.

---

## Замер (гейт на вариант B/C)

Ручная одноразовая процедура (инструментализация **не коммитится**). Снять в **трёх точках**: baseline (до PR-1), после PR-1, после PR-2 — чтобы отделить эффект индексов от эффекта устранения дубля.

- [ ] **Данные.** На реалистичном наборе (или засеять: N счетов × M позиций за ≥12 месяцев в одном проекте). Зафиксировать форму набора (месяцы/счета/позиции) — она часть результата.
- [ ] **`/summary` p50/p95.** DevTools → Network, disable cache; **≥20** холодных перезаходов на `/projects/:id` (между заходами — `location.reload()`); записать **p50 и p95** времени ответа `/summary` (гейт задан спекой в терминах p95, поэтому нужно достаточно прогонов; `max` из 5 — НЕ p95). Удобно экспортировать HAR и посчитать перцентили скриптом.
- [ ] **Число вызовов `/calculations` на холодной загрузке.** В том же Network — счётчик запросов к `/dashboard/calculations` при первой отрисовке (после PR-2 ожидается **0**).
- [ ] **time-to-ready-screen.** Временный локальный патч (НЕ коммитить): `performance.mark("nav")` при входе на маршрут + `performance.mark("calc-ready")` в эффекте, когда calc-данные впервые доступны (`!isLoading && data`), затем `performance.measure("ready","nav","calc-ready")`; читать `performance.getEntriesByName("ready")[0].duration` в консоли; 5 холодных прогонов, медиана. После замера **откатить патч**.
- [ ] **SQL-count в `compute_calculations`** (опц.): временно залогировать число запросов (SQLAlchemy `event`/эхо) на один `/summary`; откатить.
- [ ] **`EXPLAIN (ANALYZE, BUFFERS)`** ключевых запросов до/после индексов (для точек baseline/после-PR-1).
- [ ] **Зафиксировать** все числа в описании соответствующего PR (таблица «до/после»).
- [ ] **Гейт:** если `/summary` **p95** всё ещё >~400–500 мс на реалистичных данных → открывать вариант B отдельным планом (B целит во внутренний расчёт `/summary`).

## Execution Handoff

См. отдельный запрос ниже.

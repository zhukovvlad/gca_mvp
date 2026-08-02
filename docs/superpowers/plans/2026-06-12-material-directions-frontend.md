# План: направления материалов — frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** переключатель направлений на странице объекта (`?direction=` в URL), режим сводки «Все направления», скоуп табов направлением, чистка хардкодов бетона.

**Architecture:** состояние — в URL (`?direction=`, `?view=errors`), `useSearchParams`. Трёхзначный `direction: undefined | 'all' | code` с гейтом запросов до прихода summary (спека §7.2). Четыре режима рендера (§7.3): скелетоны / legacy (пустой объект) / сводка «Все» (+view=errors) / табы направления. Summary — один запрос без direction; срез направления выбирает фронт.

**Tech Stack:** React 19, TS strict, React Router 7 (`useSearchParams`), TanStack Query v5, shadcn/ui, vitest + MSW + testing-library.

**Спека:** `docs/superpowers/specs/2026-06-11-material-directions-design.md` (R5) — §3 (UX), §7 (frontend), §8.2 (тесты).

**Зависимость:** backend-план (`2026-06-12-material-directions-backend.md`) уже смержен — API отдаёт новый shape summary и принимает `direction`.

**Команды:** `just test-frontend` · `just typecheck-frontend` · `just lint`.
**Ветка:** `feat/material-directions-frontend` от main (после мержа backend).

---

## Контекст для исполнителя (прочитать ДО кода)

| Что | Где |
|---|---|
| Страница объекта (вся работа здесь) | `frontend/src/pages/ProjectPage.tsx` |
| Хуки данных | `frontend/src/services/queries.ts:275-311` (dashboard), `:427-433` (projectSuppliers), `:123-133` (referencePrices) |
| Query keys | `frontend/src/services/queryKeys.ts` |
| API-клиенты | `frontend/src/services/api/dashboard.ts`, `api/reports.ts`, `api/referencePrices.ts` |
| Типы | `frontend/src/types/dashboard.ts` |
| KpiCard (breakdown уже есть) | `frontend/src/components/ui-domain/KpiCard.tsx` |
| DeviationChart (хардкоды :148, :349) | `frontend/src/components/projects/DeviationChart.tsx` |
| MonthlyTab (хардкод :284) | `frontend/src/components/projects/MonthlyTab.tsx` |
| ErrorDocsTab | `frontend/src/components/projects/ErrorDocsTab.tsx` |
| Тест-инфра: `renderWithProviders(initialRoute)`, MSW `server`, фикстуры | `frontend/src/test/utils.tsx`, `test/server.ts`, `test/fixtures.ts`, `test/handlers.ts` |
| Пример тестов страницы | `frontend/src/pages/ProjectPage.test.tsx` |

---

### Task 1: типы, API-клиенты, query keys, хуки, фикстуры

**Files:**
- Modify: `frontend/src/types/dashboard.ts`
- Modify: `frontend/src/services/api/dashboard.ts`, `frontend/src/services/api/reports.ts`, `frontend/src/services/api/referencePrices.ts`
- Modify: `frontend/src/services/queryKeys.ts`, `frontend/src/services/queries.ts`
- Modify: `frontend/src/test/fixtures.ts`

- [ ] **Step 1: Типы** — в `types/dashboard.ts`:

```ts
export interface DirectionSummary {
  code: string;
  name: string;
  /** Оборот направления по позициям, ₽ с НДС (спека §5.1). */
  turnover: number;
  /** Σ deviation_amount классов направления; null — нет базовых цен. */
  overpayment: number | null;
  /** Объём в родной единице направления (только base-классы, §5.2). */
  volume: number | null;
  volume_unit: string | null;
  /** Base-позиции, не вошедшие в объём (другая размерность / нет нормализации). */
  volume_excluded_count: number;
  invoice_count: number;
  mixed_invoice_count: number;
}

export interface DashboardSummary {
  // ...существующие поля без изменений...
  /** Направления с данными, без типа other (ADR #9); порядок — по id типа. */
  directions: DirectionSummary[];
  /** Счета с позициями ≥2 направлений (§5.5). */
  mixed_invoice_count: number;
  /** Счета без единой direction-позиции — хвост «· N проч.» в KPI. */
  other_invoice_count: number;
  delivery_total: number;
  /** item_type='other' + классы типа other + позиции без класса (§5.1). */
  other_total: number;
  /** @deprecated «попугаи» при миксе единиц — не использовать (TECH_DEBT). */
  total_qty: number;
}

export interface DashboardCalculation {
  // ...существующие поля...
  /** Code типа материала класса ('concrete' | 'rebar' | 'other' | ...). */
  direction: string;
}

export interface MonthlyBucketRaw {
  // ...существующие поля...
  volume_unit: string | null;
}
```

- [ ] **Step 2: API-клиенты.** `api/dashboard.ts` — опциональный `direction` у `invoices`, `calculations`, `monthlySummary` (в `params` только если задан):

```ts
  async invoices(projectId: ID, direction?: string): Promise<DashboardInvoices> {
    const params: Record<string, string | number> = { project_id: projectId };
    if (direction) params.direction = direction;
    const { data } = await api.get<DashboardInvoices>("/dashboard/invoices", { params });
    return data;
  },
```

(аналогично для `calculations` — параметр после periodEnd, и `monthlySummary`). `api/reports.ts` — `direction?: string` в `ExcelExportInput` (axios сам не отправит undefined-параметр? отправит как пусто — добавлять в объект только при наличии, как в dashboard). `api/referencePrices.ts` — `direction?: string` в `list`.

- [ ] **Step 3: Query keys** (`queryKeys.ts`) — direction в конце ключа, `"all"`-режим кодируем `undefined → "all"` для стабильности:

```ts
  dashboard: {
    summary: (projectId: ID) => ["dashboard", "summary", projectId] as const,   // без direction (§6.1)
    invoices: (projectId: ID, direction?: string) =>
      ["dashboard", "invoices", projectId, direction ?? "all"] as const,
    calculations: (projectId: ID, periodStart?: string, periodEnd?: string, direction?: string) =>
      ["dashboard", "calculations", projectId, periodStart, periodEnd, direction ?? "all"] as const,
    calculationsAll: ["dashboard", "calculations", "all"] as const,
    monthly: (projectId: ID, direction?: string) =>
      ["dashboard", "monthly", projectId, direction ?? "all"] as const,
  },
  // ...
  projectSuppliers: (projectId: ID, direction?: string) =>
    ["project-suppliers", projectId, direction ?? "all"] as const,
```

`referencePrices.all` — добавить direction в хвост по той же схеме.

- [ ] **Step 4: Хуки** (`queries.ts`). Сигнатуры: `direction?: string` + `options?: { enabled?: boolean }` (гейт от гонки, спека §7.2). Образец:

```ts
export function useDashboardInvoices(
  projectId: ID | null,
  direction?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.invoices(projectId, direction) : ["dashboard", "invoices", "none"],
    queryFn: () => dashboardApi.invoices(projectId as ID, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
  });
}
```

Так же: `useDashboardCalculations(projectId, periodStart?, periodEnd?, direction?, options?)`, `useDashboardMonthlySummary(projectId, direction?, options?)`, `useProjectSuppliers(projectId, direction?, options?)`. `useReferencePrices` — `direction` в options (`{ enabled?, materialClassId?, direction? }`). Существующие вызовы хуков в других страницах (Dashboard, SupplierPage…) НЕ трогать — новые параметры опциональны.

- [ ] **Step 5: Фикстуры** (`test/fixtures.ts`):

```ts
export const sampleDirectionConcrete = {
  code: "concrete", name: "Бетон",
  turnover: 220000, overpayment: null,
  volume: 31.5, volume_unit: "м³", volume_excluded_count: 0,
  invoice_count: 5, mixed_invoice_count: 0,
};

export const sampleDashboardSummary = {
  // ...существующие поля как были...
  directions: [sampleDirectionConcrete],
  mixed_invoice_count: 0,
  other_invoice_count: 0,
  delivery_total: 30000,
  other_total: 0,
};

/** Объект с двумя направлениями — для тестов сводки «Все». */
export const sampleDashboardSummaryMulti = {
  ...sampleDashboardSummary,
  total_amount: 400000,
  directions: [
    { ...sampleDirectionConcrete },
    { code: "rebar", name: "Арматура", turnover: 120000, overpayment: 15000,
      volume: 12.4, volume_unit: "т", volume_excluded_count: 1,
      invoice_count: 2, mixed_invoice_count: 1 },
  ],
  mixed_invoice_count: 1,
  other_invoice_count: 1,
};

/** Пустой объект (без счетов) — legacy-режим (ADR #11). */
export const sampleDashboardSummaryEmpty = {
  ...sampleDashboardSummary,
  doc_count: 0, invoice_count: 0, total_amount: 0, material_amount: 0,
  delivery_amount: 0, other_amount: 0, total_qty: 0,
  first_invoice_date: null, last_invoice_date: null, full_deviation_amount: null,
  directions: [], mixed_invoice_count: 0, other_invoice_count: 0,
  delivery_total: 0, other_total: 0,
};
```

В `sampleMonthlySummary` добавить `volume_unit: null` в обе строки. Дефолтный MSW-хендлер summary (см. `test/handlers.ts`) продолжает отдавать `sampleDashboardSummary`.

- [ ] **Step 6: Verify** — `just typecheck-frontend` чисто; `just test-frontend` — все существующие тесты зелёные (КРИТИЧНО: моно-фикстура `sampleDashboardSummary` с `directions: [concrete]` приведёт к авто-дефолту «Бетон» ПОСЛЕ Task 4 — на этом шаге поведение страницы ещё не изменилось).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types frontend/src/services frontend/src/test/fixtures.ts
git commit -m "feat(directions-fe): types, api params, query keys, hooks with direction + enabled gate"
```

---

### Task 2: компонент `DirectionSwitcher`

**Files:**
- Create: `frontend/src/components/projects/DirectionSwitcher.tsx`
- Test: `frontend/src/components/projects/DirectionSwitcher.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DirectionSwitcher } from "./DirectionSwitcher";

const DIRECTIONS = [
  { code: "concrete", name: "Бетон" },
  { code: "rebar", name: "Арматура" },
];

describe("DirectionSwitcher", () => {
  it("renders «Все направления» first, then directions in order", () => {
    render(<DirectionSwitcher directions={DIRECTIONS} value="all" onChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Все направления", "Бетон", "Арматура"]);
  });

  it("marks active segment with aria-selected", () => {
    render(<DirectionSwitcher directions={DIRECTIONS} value="rebar" onChange={() => {}} />);
    expect(screen.getByTestId("direction-rebar")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-selected", "false");
  });

  it("calls onChange with code on click", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<DirectionSwitcher directions={DIRECTIONS} value="all" onChange={onChange} />);
    await user.click(screen.getByTestId("direction-rebar"));
    expect(onChange).toHaveBeenCalledWith("rebar");
  });

  it("renders nothing when directions is empty (legacy mode, ADR #11)", () => {
    const { container } = render(<DirectionSwitcher directions={[]} value="all" onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run** `just test-frontend` → FAIL (нет модуля).

- [ ] **Step 3: Implement** — segmented control на токенах темы (готового ToggleGroup в проекте нет; стиль — как активные состояния в Tabs/Button):

```tsx
import { cn } from "@/lib/utils";

interface DirectionSwitcherProps {
  directions: { code: string; name: string }[];
  /** 'all' | code направления */
  value: string;
  onChange: (code: string) => void;
}

/** Переключатель направлений (спека §3.1, §7.1). Скрыт у пустого объекта. */
export function DirectionSwitcher({ directions, value, onChange }: DirectionSwitcherProps) {
  if (directions.length === 0) return null;
  const items = [{ code: "all", name: "Все направления" }, ...directions];
  return (
    <div
      role="tablist"
      aria-label="Направления"
      data-testid="direction-switcher"
      className="inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-surface-sunken p-1"
    >
      {items.map((d) => (
        <button
          key={d.code}
          type="button"
          role="tab"
          aria-selected={value === d.code}
          data-testid={`direction-${d.code}`}
          onClick={() => onChange(d.code)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm transition-colors",
            value === d.code
              ? "bg-surface text-fg shadow-sm"
              : "text-fg-secondary hover:text-fg",
          )}
        >
          {d.name}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit**

```bash
git add frontend/src/components/projects/DirectionSwitcher.tsx frontend/src/components/projects/DirectionSwitcher.test.tsx
git commit -m "feat(directions-fe): DirectionSwitcher segmented control"
```

---

### Task 3: `KpiCard` — мультизначный вариант «Объёмы»

**Files:**
- Modify: `frontend/src/components/ui-domain/KpiCard.tsx`
- Test: `frontend/src/components/ui-domain/KpiCard.test.tsx` (создать, если нет)

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { KpiCard } from "./KpiCard";

it("renders multi-value rows (name left, value right) instead of single value", () => {
  render(
    <KpiCard
      label="Объёмы"
      values={[
        { label: "Бетон", value: "5 677,5 м³" },
        { label: "Арматура", value: "124,8 т" },
      ]}
    />,
  );
  expect(screen.getByText("5 677,5 м³")).toBeInTheDocument();
  expect(screen.getByText("Арматура")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run** → FAIL (нет пропа `values`, `value` обязателен).

- [ ] **Step 3: Implement** — `value` становится опциональным, добавляется `values`; существующие использования не меняются:

```tsx
interface KpiCardProps {
  label: string;
  value?: string;
  /** Мультизначный вариант (KPI «Объёмы»): строка на направление — имя слева,
   * значение в родной единице справа. Имя в именительном падеже («Бетон — 5 677,5 м³»):
   * никаких склонений в коде — формат масштабируется на любое будущее направление. */
  values?: { label: string; value: string }[];
  // ...остальные пропсы без изменений...
}
```

В JSX вместо текущего value-блока:

```tsx
      {values && values.length > 0 ? (
        <div className="mt-2 space-y-1">
          {values.map((v) => (
            <div key={v.label} className="flex items-baseline justify-between gap-2 leading-snug">
              <span className="text-sm text-fg-secondary">{v.label}</span>
              <span className="font-mono text-lg text-fg">{v.value}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className={cn("mt-2 font-mono text-2xl text-fg", valueClassName)}>
          {value}
          {suffix && <span className="ml-1.5 text-sm font-normal text-fg-secondary">{suffix}</span>}
        </div>
      )}
```

- [ ] **Step 4: Run** → PASS (+ существующие тесты). **Step 5: Commit**

```bash
git add frontend/src/components/ui-domain/KpiCard.tsx frontend/src/components/ui-domain/KpiCard.test.tsx
git commit -m "feat(directions-fe): KpiCard multi-value variant for volumes"
```

---

### Task 4: `DeviationChart` — нейтральный заголовок, top-N, секции по направлениям

**Files:**
- Modify: `frontend/src/components/projects/DeviationChart.tsx`
- Test: `frontend/src/components/projects/DeviationChart.test.tsx` (дополнить)

- [ ] **Step 1: Write the failing tests** (дополнить существующий файл; смотри в нём, как собираются `calculations`-пропсы):

```tsx
const CALC = (over: Partial<DashboardCalculation>): DashboardCalculation => ({
  material_class_id: 1, material_class_name: "В25",
  period_start: "2026-03-01", period_end: "2026-03-31",
  avg_price: 9000, reference_price: 8000, deviation_pct: 12.5, deviation_amount: 10000,
  corridor_pct: null, compensation_per_unit: null, compensation_amount: null,
  material_total: 90000, delivery_total: 0, total_qty: 10, invoice_count: 1,
  direction: "concrete",
  ...over,
});

it("renders neutral title instead of concrete hardcode", () => {
  render(<DeviationChart calculations={[CALC({})]} />);
  expect(screen.getByText("Отклонения от базовых цен")).toBeInTheDocument();
  expect(screen.queryByText(/классам бетона/)).not.toBeInTheDocument();
});

it("groups sections by direction with subtotal and open link", async () => {
  const onOpen = vi.fn();
  render(
    <DeviationChart
      periodFilterActive
      calculations={[
        CALC({ material_class_id: 1, material_class_name: "В25", deviation_amount: 10000 }),
        CALC({ material_class_id: 2, material_class_name: "А500С Ø12", direction: "rebar", deviation_amount: 5000 }),
      ]}
      groups={[
        { code: "concrete", name: "Бетон", onOpen },
        { code: "rebar", name: "Арматура", onOpen },
      ]}
    />,
  );
  expect(screen.getByTestId("deviation-group-concrete")).toHaveTextContent("Бетон");
  expect(screen.getByTestId("deviation-group-rebar")).toHaveTextContent("Арматура");
  const user = userEvent.setup();
  await user.click(screen.getAllByRole("button", { name: /открыть/ })[0]);
  expect(onOpen).toHaveBeenCalled();
});

it("limits bars to topN by absolute deviation amount", () => {
  const calcs = Array.from({ length: 8 }, (_, i) =>
    CALC({ material_class_id: i + 1, material_class_name: `В${i + 10}`, deviation_amount: (i + 1) * 1000 }));
  render(<DeviationChart periodFilterActive calculations={calcs} topN={5} />);
  // топ-5 по |deviation_amount| — В17..В13; В12 (3000) не попадает
  expect(screen.queryByText("В12")).not.toBeInTheDocument();
  expect(screen.getByText("В17")).toBeInTheDocument();
});
```

(Если recharts в jsdom не рендерит текст осей — взять паттерн ассертов из существующих тестов DeviationChart; «не попадает в топ» можно проверять по числу `rect`-баров.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** Изменения в `DeviationChart.tsx`:

3a. Новые пропсы:

```tsx
interface Props {
  // ...существующие...
  /** Заголовок/подзаголовок шапки. Дефолты — нейтральные (спека §7.5). */
  title?: string;
  subtitle?: string;
  /** Ограничить число классов в графике топ-N по |deviation_amount|. */
  topN?: number;
  /** Режим сводки «Все»: секции по direction. Строки с direction вне списка
   * (тип other) не рендерятся ни в секциях, ни в сноске (спека §6.2). */
  groups?: { code: string; name: string; onOpen: () => void }[];
}
```

3b. `FilterHeader` получает `title`/`subtitle` пропсы; оба хардкода «Отклонения по классам бетона» / «относительно базовой цены» (строки 148–149 и 349–350) заменить на `{title}` / `{subtitle}` с дефолтами `"Отклонения от базовых цен"` / `"относительно базовой цены"`.

3c. Вынести существующий рендер «банер + бары + сноска» в внутренний под-компонент `ChartBody({ calcs, topN, showFooter, onConfigurePrice, displayStart, displayEnd })` — это текущий JSX от Period summary banner до footer, где:
- `displayCalcs` считается от `calcs` (логика агрегации не меняется);
- после `withPrice` применить топ: `const shown = topN ? [...withPrice].sort((a, b) => Math.abs(b.deviation_amount ?? 0) - Math.abs(a.deviation_amount ?? 0)).slice(0, topN) : withPrice;` и строить `data`/высоту от `shown`.

3d. Корневой рендер:

```tsx
      {groups ? (
        <>
          {groups.map((g) => {
            const groupCalcs = calculations.filter((c) => c.direction === g.code);
            if (groupCalcs.length === 0) return null;
            const subtotal = groupCalcs.reduce(
              (s, c) => (c.deviation_amount != null ? (s ?? 0) + c.deviation_amount : s),
              null as number | null,
            );
            return (
              <div key={g.code} data-testid={`deviation-group-${g.code}`}>
                <div className="flex items-center justify-between px-5 pt-4 pb-1">
                  <span className="text-sm font-medium">{g.name}</span>
                  <span className="flex items-center gap-3 text-xs">
                    {subtotal != null && (
                      <span className={subtotal > 0 ? "text-danger-text" : "text-accent-text"}>
                        {subtotal > 0 ? `+${formatMoney(subtotal)}` : formatMoney(subtotal)}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={g.onOpen}
                      className="text-accent-text hover:underline"
                    >
                      открыть →
                    </button>
                  </span>
                </div>
                <ChartBody calcs={groupCalcs} topN={topN ?? 5} showFooter={false} ... />
              </div>
            );
          })}
          {/* Общая сноска «без базовой цены» — по классам направлений (other отфильтрован groups-кодами) */}
          <ChartFooterWithoutPrice calcs={calculations.filter((c) => groups.some((g) => g.code === c.direction))} />
        </>
      ) : (
        <ChartBody calcs={calculations} topN={topN} showFooter onConfigurePrice={onConfigurePrice} ... />
      )}
```

(`ChartFooterWithoutPrice` — текущий footer-блок, вынесенный для переиспользования; в grouped-режиме `onConfigurePrice` не передаётся — кнопки «Настроить» на «Все» нет, спека §3.2. Точная декомпозиция — на усмотрение исполнителя, требования: один кард, шапка с фильтром одна, банер переплаты в группах не дублировать — рендерить его только в негруппированном режиме.)

- [ ] **Step 4: Run** `just test-frontend` → новые и существующие тесты DeviationChart PASS (существующие проверяют негруппированный режим — регрессия).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/projects/DeviationChart.tsx frontend/src/components/projects/DeviationChart.test.tsx
git commit -m "feat(directions-fe): DeviationChart neutral title, topN, direction sections"
```

---

### Task 5: ProjectPage — URL-состояние, режимы, скоуп, сводка

Самая большая задача. Делается тремя последовательными шагами с тестами после каждого.

**Files:**
- Modify: `frontend/src/pages/ProjectPage.tsx`
- Test: `frontend/src/pages/ProjectPage.test.tsx`

- [ ] **Step 1: Write the failing tests — состояние и режимы** (добавить describe-блок; хелпер `renderProject` уже есть в файле):

```tsx
import { sampleDashboardSummaryMulti, sampleDashboardSummaryEmpty } from "@/test/fixtures";

function mockSummary(payload: unknown) {
  server.use(http.get("/api/dashboard/summary", () => HttpResponse.json(payload)));
}

describe("ProjectPage directions", () => {
  it("mono-object: defaults to its direction with tabs visible (ADR #10)", async () => {
    renderProject(); // дефолтная фикстура: directions=[concrete]
    expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
    expect(screen.getByTestId("direction-concrete")).toHaveAttribute("aria-selected", "true");
  });

  it("multi-object: defaults to «Все» — no tabs, summary KPIs with breakdown", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    renderProject();
    await screen.findByTestId("direction-switcher");
    expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
    expect(screen.getByText("Объёмы")).toBeInTheDocument();
    expect(screen.getByText(/124,8/)).toBeInTheDocument();        // т арматуры
    expect(screen.getByText(/Переплата за весь период|Отклонение/)).toBeInTheDocument();
  });

  it("empty object: legacy tabs, no switcher (ADR #11)", async () => {
    mockSummary(sampleDashboardSummaryEmpty);
    renderProject();
    expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
    expect(screen.queryByTestId("direction-switcher")).not.toBeInTheDocument();
  });

  it("?direction=rebar opens rebar mode directly (criterion #3)", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    renderProject("1", "?direction=rebar");
    expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
    expect(screen.getByTestId("direction-rebar")).toHaveAttribute("aria-selected", "true");
  });

  it("garbage ?direction= falls back to auto-default and never hits API with it", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    const seen: string[] = [];
    server.use(
      http.get("/api/dashboard/invoices", ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("direction") ?? "");
        return HttpResponse.json([]);
      }),
    );
    renderProject("1", "?direction=trash");
    await screen.findByTestId("direction-switcher");
    expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-selected", "true");
    expect(seen).not.toContain("trash");   // гейт §7.2: запрос не ушёл с мусором
  });

  it("switching direction resets active tab to overview and updates URL", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    const user = userEvent.setup();
    renderProject();
    await user.click(await screen.findByTestId("direction-concrete"));
    // в режиме направления видим табы, активен «Обзор»
    expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByTestId("project-tab-invoices"));
    await user.click(screen.getByTestId("direction-rebar"));
    expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("aria-selected", "true");
  });
});
```

Хелпер `renderProject` расширить параметром query: `function renderProject(id = "1", search = "")` → `initialRoute: \`/projects/${id}${search}\``.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement — состояние (§7.2) и каркас режимов (§7.3).** В `ProjectPage.tsx`:

3a. Импорты: `useSearchParams` из react-router-dom, `useEffect`, `DirectionSwitcher`.

3b. Состояние (после `const [activeTab, setActiveTab] = useState("overview");`):

```tsx
  // ── направление: трёхзначное состояние из URL (спека §7.2) ──
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDirection = searchParams.get("direction"); // null | 'all' | code
  const directions = summaryQ.data?.directions;       // undefined пока summary грузится

  // undefined = режим не определён (summary не пришёл) — НЕ 'all'
  const direction: string | undefined =
    directions === undefined ? undefined
    : rawDirection === "all" ? "all"
    : directions.some((d) => d.code === rawDirection) ? (rawDirection as string)
    : directions.length === 1 ? directions[0].code     // автодефолт моно-объекта (ADR #10)
    : "all";

  const isLegacy = directions !== undefined && directions.length === 0; // пустой объект (ADR #11)
  const scopedDirection = direction !== undefined && direction !== "all" ? direction : undefined;
  // ?view=errors читается только на «Все»; в других режимах ИГНОРИРУЕТСЯ, URL не
  // чистим (зафиксированный выбор из §7.2 «игнорируется/удаляется» — игнор дешевле,
  // а changeDirection при явном переключении параметр удаляет)
  const view = direction === "all" ? searchParams.get("view") : null;

  const changeDirection = (code: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("direction", code);
      next.delete("view");
      return next;
    });
  };

  // Сброс вкладки — эффектом на смену direction (back/forward идут мимо onChange, §7.2)
  useEffect(() => {
    setActiveTab("overview");
  }, [direction]);
```

(`summaryQ` объявлен выше блока queries — перенести объявление `summaryQ` выше этого блока, остальные queries ниже.)

3c. Гейт зависимых запросов (заменить текущие вызовы):

```tsx
  const queriesEnabled = direction !== undefined;       // гейт §7.2
  const invoicesQ = useDashboardInvoices(projectId, scopedDirection, { enabled: queriesEnabled });
  const calculationsQ = useDashboardCalculations(
    projectId,
    debouncedPeriodStart || undefined,
    debouncedPeriodEnd || undefined,
    scopedDirection,
    { enabled: queriesEnabled },
  );
  const projectSuppliersQ = useProjectSuppliers(projectId, scopedDirection, { enabled: queriesEnabled });
  const referencePricesQ = useReferencePrices(
    hasValidProjectId ? projectId : undefined,
    { enabled: hasValidProjectId && queriesEnabled, direction: scopedDirection },
  );
```

(`useDocuments` остаётся как есть — ошибки глобальны.)

3d. Каркас рендера после `<UploadSheet ... />` (резерв высоты против дёргания — §3.1):

```tsx
      {/* Переключатель направлений (скрыт у пустого объекта) */}
      {!isLegacy && directions !== undefined && (
        <div className="mt-6">
          <DirectionSwitcher directions={directions} value={direction ?? "all"} onChange={changeDirection} />
        </div>
      )}

      {/* Контент: высота ряда табов зарезервирована во всех режимах */}
      <div className="mt-6 min-h-9">
        {direction === undefined ? (
          <div className="space-y-4"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-[120px]" /></div>
        ) : isLegacy || scopedDirection ? (
          /* существующий <Tabs> ... </Tabs> блок — как был, контент скоупится через хуки */
        ) : view === "errors" ? (
          /* блок «Ошибки объекта» — Step 5 */
        ) : (
          /* сводка «Все направления» — Step 5 */
        )}
      </div>
```

Существующий `<Tabs>`-блок остаётся нетронутым внутри ветки (он уже скоупится данными из хуков). На этом шаге ветки сводки/ошибок могут быть заглушками `<div data-testid="all-summary" />` — их наполняет Step 5.

- [ ] **Step 4: Run state-тесты** — тесты режимов/URL из Step 1 зелёные (тесты про KPI «Объёмы» упадут до Step 5 — допустимо, идём дальше).

- [ ] **Step 5: Implement — сводка «Все направления» (§3.2) и `view=errors`.** Ветка сводки:

```tsx
  const dirAll = summaryQ.data; // shorthand в JSX ниже
```

```tsx
          <div className="space-y-6">
            {/* KPI ×4 */}
            {dirAll && (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard
                  label="Оборот, ₽ с НДС"
                  value={formatMoney(dirAll.total_amount)}
                  breakdown={[
                    ...dirAll.directions.map((d) => ({ label: d.name, value: formatMoney(d.turnover) })),
                    ...(dirAll.delivery_total > 0 ? [{ label: "Доставка", value: formatMoney(dirAll.delivery_total) }] : []),
                    ...(dirAll.other_total > 0 ? [{ label: "Прочее", value: formatMoney(dirAll.other_total) }] : []),
                  ]}
                />
                <KpiCard
                  label="Объёмы"
                  values={dirAll.directions
                    .filter((d) => d.volume !== null)
                    .map((d) => ({
                      label: d.name,                                  // именительный падеж — без склонений (масштабируется на кирпич и далее)
                      value: `${formatNumber(d.volume!)} ${d.volume_unit}`,
                    }))}
                />
                <KpiCard
                  label="Счетов"
                  value={formatNumber(dirAll.invoice_count)}
                  breakdown={[                                        /* breakdown, не suffix: длинная разбивка в строку у числа теснится */
                    ...dirAll.directions.map((d) => ({ label: d.name, value: formatNumber(d.invoice_count) })),
                    ...(dirAll.mixed_invoice_count > 0 ? [{ label: "Смешанные", value: formatNumber(dirAll.mixed_invoice_count) }] : []),
                    ...(dirAll.other_invoice_count > 0 ? [{ label: "Прочие", value: formatNumber(dirAll.other_invoice_count) }] : []),
                  ]}
                />
                {/* Переплата: тот же devLabel/devClass-блок, что в табе «Обзор», + breakdown */}
                <KpiCard
                  label={devKpiLabel}
                  value={devLabel}
                  className={devClass}
                  valueClassName={devValueClass}
                  breakdown={dirAll.directions
                    .filter((d) => d.overpayment !== null)
                    .map((d) => ({ label: d.name, value: formatMoney(d.overpayment!) }))}
                />
              </div>
            )}

            {/* Алерт нераспознанных (§3.2 п.2) — источник тот же, что бейдж «Ошибки» */}
            {errorDocCount > 0 && (
              <button
                type="button"
                data-testid="unrecognized-alert"
                onClick={() => setSearchParams((p) => { const n = new URLSearchParams(p); n.set("view", "errors"); return n; })}
                className="flex w-full items-center justify-between rounded-lg border border-danger-border bg-danger-soft px-4 py-2.5 text-sm text-danger-text hover:opacity-90"
              >
                <span>{errorDocCount} документ{pluralRu(errorDocCount)} не распознан{errorDocCount === 1 ? "" : "ы"} и не учтён{errorDocCount === 1 ? "" : "ы"} в цифрах</span>
                <span>Разобрать →</span>
              </button>
            )}

            {/* Отклонения секциями по направлениям, top-5 (§3.2 п.3) */}
            <DeviationChart
              calculations={calculations}
              periodFilterActive
              topN={5}
              groups={dirAll?.directions.map((d) => ({
                code: d.code, name: d.name, onOpen: () => changeDirection(d.code),
              })) ?? []}
              periodStart={periodStart} periodEnd={periodEnd}
              dataStart={dataStart} dataEnd={dataEnd}
              displayStart={displayStart} displayEnd={displayEnd}
              onPeriodStartChange={setPeriodStart} onPeriodEndChange={setPeriodEnd}
              onPeriodReset={() => { setPeriodStart(""); setPeriodEnd(""); }}
            />
          </div>
```

(devKpiLabel/devLabel/devClass/devValueClass — вынести их вычисление из IIFE таба «Обзор» на уровень компонента, чтобы переиспользовать в обоих режимах. На «Все» НЕТ таблицы периодов и кнопки «Настроить базовые цены» — НЕ передавать `onConfigurePrice`.)

Ветка `view === "errors"`:

```tsx
          <div className="space-y-4" data-testid="project-errors-view">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-lg">Ошибки объекта</h2>
              <button
                type="button"
                className="text-sm text-accent-text hover:underline"
                onClick={() => setSearchParams((p) => { const n = new URLSearchParams(p); n.delete("view"); return n; })}
              >
                ← к сводке
              </button>
            </div>
            <ErrorDocsTab docs={docsQ.data ?? []} />
          </div>
```

(Сверь сигнатуру пропсов ErrorDocsTab с текущим использованием в табе «Ошибки» — передать то же самое.)

- [ ] **Step 6: Write + run остальные тесты режима (§8.2):**

```tsx
  it("«Все»: alert opens errors view, back link returns", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    server.use(http.get("/api/invoices/documents", () =>   // взять реальный путь из useDocuments
      HttpResponse.json([{ id: 1, status: "error", has_issues: true, filename: "x.pdf" }])));
    const user = userEvent.setup();
    renderProject();
    await user.click(await screen.findByTestId("unrecognized-alert"));
    expect(screen.getByTestId("project-errors-view")).toBeInTheDocument();
    expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-selected", "true"); // направление не сменилось
    await user.click(screen.getByRole("button", { name: /к сводке/ }));
    expect(screen.queryByTestId("project-errors-view")).not.toBeInTheDocument();
  });

  it("«Все»: no period table and no configure-prices button", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    renderProject();
    await screen.findByTestId("direction-switcher");
    expect(screen.queryByRole("button", { name: /настроить базовые/i })).not.toBeInTheDocument();
  });

  it("direction mode passes direction to scoped hooks (MSW)", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    const seen: string[] = [];
    server.use(http.get("/api/dashboard/invoices", ({ request }) => {
      seen.push(new URL(request.url).searchParams.get("direction") ?? "none");
      return HttpResponse.json([]);
    }));
    renderProject("1", "?direction=rebar");
    await screen.findByTestId("project-page-tabs-list");
    await waitFor(() => expect(seen).toContain("rebar"));
  });
```

Run: `just test-frontend` → PASS.

- [ ] **Step 7: Implement — мелкие скоупы и чистка хардкодов:**

7a. KPI таба «Обзор» в режиме направления (заменить блок строк ~457–481): значения из среза `const dir = directions?.find((d) => d.code === scopedDirection)`:
- «Оборот, ₽ с НДС» → `value={formatMoney(dir.turnover)}`, suffix-якорь: `` `${Math.round((dir.turnover / dirAll.total_amount) * 100)}% оборота объекта` `` (только если `dirAll.total_amount > 0`); breakdown не передавать;
- «Объём м³» (строка 468) → `label={dir.volume_unit ? `Объём, ${dir.volume_unit}` : "Объём"}`, `value={dir.volume !== null ? formatNumber(dir.volume) : "—"}`, при `dir.volume_excluded_count > 0` — `suffix={`без ${dir.volume_excluded_count} позиц.`}` c `title`-тултипом «не вошли позиции в других единицах»;
- «Счетов» → `value={formatNumber(dir.invoice_count)}`, suffix: `dir.mixed_invoice_count > 0 ? `· ${dir.mixed_invoice_count} смешанных` : undefined`;
- «Переплата» → `dir.overpayment` (вместо `full_deviation_amount`) с теми же danger/accent-классами.
В legacy-режиме (пустой объект) KPI-блок не рендерится (нет данных) — текущая логика `summaryQ.data &&` сохраняет это.

7b. Экспорт (`handleExport`): добавить `direction: scopedDirection` в payload `reportsApi.excelBlob`; имя файла — канон §6.7:

```tsx
      const dirName = scopedDirection ? directions?.find((d) => d.code === scopedDirection)?.name : undefined;
      const dirSuffix = dirName ? `-${dirName}` : "";
      a.download = `отчёт-${safeName || projectId}${dirSuffix}${periodSuffix}.xlsx`;
```

Сверить существующий `periodSuffix` с каноном: сейчас это `` `_${periodStart || ""}–${periodEnd || ""}` `` (подчёркивание + en-dash) — формату §6.7 соответствует, не трогать. Бэкенд-план приводит Content-Disposition к этому же виду — расхождение фронт/бэк закрыто с двух сторон.

7c. MonthlyTab: прокинуть `direction={scopedDirection}` пропом; внутри MonthlyTab — `useDashboardMonthlySummary(projectId, direction)`; заголовок колонки (строка 284) и CSV-заголовок (строка 77): `Объём, ${buckets.find(b => b.volume_unit)?.volume_unit ?? "м³"}`.

7d. Удостовериться: бейдж «Ошибки» (`errorDocCount`) — без изменений, из `useDocuments` (глобален); сортировка классов в таблице периодов работает для «А500С Ø12» (числовая по первому числу в имени — Ø12 < Ø32, регрессии нет).

7e. Вкладка «Коридоры» (§3.3): corridors API не меняется (§6.8) — фильтрация на фронте. Прокинуть в `<CorridorsTab projectId={projectId} direction={scopedDirection} />`; внутри `CorridorsTab.tsx` при наличии `direction` показывать только строку типа направления и его классы (матрица уже знает `material_type` через `TYPE_LABELS`-словарь и данные классов — фильтр по `material_type === direction` для классов и type-уровневых коридоров; без `direction` — как сейчас). Тест: в `CorridorsTab.test.tsx` — с `direction="concrete"` строки rebar-классов не рендерятся.

- [ ] **Step 8: Run** `just test-frontend` + `just typecheck-frontend` → всё зелёное. Существующие тесты ProjectPage: после авто-дефолта моно-объекта поведение совпадает с прежним (табы видны) — правок не требуют; если какой-то тест ожидал отсутствие переключателя — обновить осознанно.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/ProjectPage.tsx frontend/src/pages/ProjectPage.test.tsx frontend/src/components/projects/MonthlyTab.tsx
git commit -m "feat(directions-fe): direction modes on project page — URL state, summary view, scoped tabs"
```

---

### Task 6: финал — приёмка, документация, TECH_DEBT

**Files:**
- Modify: `docs/ui/routes-architecture.md` (навигационная модель)
- Modify: `docs/TECH_DEBT.md`

- [ ] **Step 1:** `just lint` + `just typecheck-frontend` + `just test-frontend` + `just test-backend-integration` → всё зелёное.

- [ ] **Step 2: Прогон критериев приёмки** (спека §11) — пройти чек-лист вручную/тестами, отметить в PR:
пустой объект (legacy) · моно-объект (дефолт «Бетон», сводка совпадает) · мульти (инвариант) · смешанный счёт в обоих направлениях · `?direction=rebar` + back/forward + мусорный code · «Все» без табов/таблицы/кнопки цен + алерт → «Ошибки объекта» · сноска объёма · счётчики табов/глобальные ошибки · идентичность поклассовых цифр · экспорт с суффиксом.

- [ ] **Step 3: Документация.** В `docs/ui/routes-architecture.md` — раздел про страницу объекта: переключатель направлений, `?direction=` / `?view=errors`, четыре режима рендера, правило дефолта (пусто → legacy, одно → направление, иначе «Все»).

- [ ] **Step 4: TECH_DEBT** (`docs/TECH_DEBT.md`) — три записи из спеки §13.5:
1. `DashboardSummary.total_qty` — deprecated («попугаи» при миксе единиц), удалить после стабилизации направлений;
2. полный экспорт на «Все» — плоский список без колонки «Направление»; добавить колонку при реальной потребности смешанных объектов;
3. `SupplierPage.tsx:304` — хардкод «Объём, м³» на странице поставщика (вне scope направлений).

- [ ] **Step 5: Commit + push, PR** `feat/material-directions-frontend` → main; в описании — ссылка на спеку R5 и чек-лист приёмки.

# Deviation Chart Partial Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show material classes on the deviation chart whenever they have at least one month covered by a reference price, instead of dropping the entire class into the "without reference price" banner when any single month is uncovered.

**Architecture:** Frontend-only change inside `DeviationChart.tsx`. Replace the strict `every`-based aggregation (which sets `reference_price`, `deviation_pct`, `deviation_amount` to `null` if any month is uncovered) with subset-aware aggregation over covered months. Surface partial coverage in the existing recharts tooltip via two extra fields (`covered_qty`, `total_qty`) on the bar's payload. No backend changes — `compute_calculations` already returns per-month rows with `reference_price = null` for uncovered months, and `compute_full_deviation` already sums only non-null `deviation_amount` (the top banner is already correct; the bars were not).

**Tech Stack:** React 18, TypeScript, recharts, Vitest, Testing Library.

**Spec reference:** [docs/superpowers/specs/2026-05-28-deviation-chart-partial-coverage-design.md](../specs/2026-05-28-deviation-chart-partial-coverage-design.md)

---

## File Structure

**Files modified:**
- `frontend/src/components/projects/DeviationChart.tsx` — single file, all logic + tooltip change.

**Files created:**
- `frontend/src/components/projects/DeviationChart.test.tsx` — new component-level test file (alongside the component, per CLAUDE.md convention).

**Files NOT touched:**
- `frontend/src/types/dashboard.ts` — `DashboardCalculation` interface stays as-is; partial-coverage metadata is local to `DeviationChart`.
- Backend (`compute_calculations`, etc.) — already correct.

---

## Task 1: Add test scaffolding and the first failing test (full coverage baseline)

**Files:**
- Create: `frontend/src/components/projects/DeviationChart.test.tsx`

This task establishes the test file, the shared fixture helper, and a baseline test that already passes with current code (full coverage). The baseline locks behavior we don't want to break.

- [ ] **Step 1: Create test file with imports, helper, and the full-coverage test**

Create `frontend/src/components/projects/DeviationChart.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeviationChart } from "./DeviationChart";
import type { DashboardCalculation } from "@/types/dashboard";

function calc(overrides: Partial<DashboardCalculation> & Pick<DashboardCalculation, "material_class_id" | "material_class_name" | "period_start" | "period_end" | "total_qty">): DashboardCalculation {
  return {
    avg_price: 0,
    reference_price: null,
    deviation_pct: null,
    deviation_amount: null,
    material_total: 0,
    delivery_total: 0,
    invoice_count: 1,
    ...overrides,
  };
}

describe("DeviationChart — partial coverage", () => {
  it("renders a class on the chart when every month is covered", () => {
    const calculations: DashboardCalculation[] = [
      calc({
        material_class_id: 1,
        material_class_name: "B30",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 100,
        avg_price: 6500,
        reference_price: 6010,
        deviation_pct: 8.15,
        deviation_amount: 49000,
      }),
      calc({
        material_class_id: 1,
        material_class_name: "B30",
        period_start: "2024-02-01",
        period_end: "2024-02-29",
        total_qty: 50,
        avg_price: 6600,
        reference_price: 6010,
        deviation_pct: 9.82,
        deviation_amount: 29500,
      }),
    ];

    render(<DeviationChart calculations={calculations} periodFilterActive={true} />);

    // Class appears on the chart (YAxis tick text)
    expect(screen.getByText("B30")).toBeInTheDocument();
    // No "без базовой цены" banner — full coverage
    expect(screen.queryByText(/без базовой цены/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify baseline passes**

Run: `cd frontend && npx vitest run src/components/projects/DeviationChart.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/projects/DeviationChart.test.tsx
git commit -m "test: add DeviationChart baseline test for full coverage"
```

---

## Task 2: Add the failing partial-coverage test (drives the fix)

**Files:**
- Modify: `frontend/src/components/projects/DeviationChart.test.tsx`

This test is the one that currently fails — it asserts that a class with 2 covered months and 1 uncovered month appears **on the chart** (not in the banner).

- [ ] **Step 1: Add the partial-coverage test**

Append to `frontend/src/components/projects/DeviationChart.test.tsx` inside the `describe` block:

```tsx
  it("renders a class with partial month coverage on the chart, not in the banner", () => {
    // B15: 3 months, 2 with reference_price (covered), 1 without (uncovered).
    const calculations: DashboardCalculation[] = [
      calc({
        material_class_id: 2,
        material_class_name: "B15",
        period_start: "2023-12-01",
        period_end: "2023-12-31",
        total_qty: 20,
        avg_price: 6000,
        material_total: 100000,
        delivery_total: 20000,
        reference_price: null,
        deviation_pct: null,
        deviation_amount: null,
      }),
      calc({
        material_class_id: 2,
        material_class_name: "B15",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 80,
        avg_price: 6200,
        material_total: 400000,
        delivery_total: 96000,
        reference_price: 5490,
        deviation_pct: 12.93,
        deviation_amount: 56800,
      }),
      calc({
        material_class_id: 2,
        material_class_name: "B15",
        period_start: "2024-02-01",
        period_end: "2024-02-29",
        total_qty: 100,
        avg_price: 6300,
        material_total: 500000,
        delivery_total: 130000,
        reference_price: 5490,
        deviation_pct: 14.75,
        deviation_amount: 81000,
      }),
    ];

    render(<DeviationChart calculations={calculations} periodFilterActive={true} />);

    // B15 appears on the chart
    expect(screen.getByText("B15")).toBeInTheDocument();
    // B15 must NOT be listed in the "без базовой цены" banner
    expect(screen.queryByText(/B15.*без базовой цены/)).not.toBeInTheDocument();
    expect(screen.queryByText(/без базовой цены/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails (current code drops B15 into the banner)**

Run: `cd frontend && npx vitest run src/components/projects/DeviationChart.test.tsx`
Expected: FAIL on the new test — `screen.getByText("B15")` won't find the YAxis tick because B15 is currently in `withoutPrice`, not `withPrice`. The "без базовой цены" banner is rendered.

- [ ] **Step 3: Commit (failing test, locks behavior contract)**

```bash
git add frontend/src/components/projects/DeviationChart.test.tsx
git commit -m "test: add failing test for partial reference-price coverage"
```

---

## Task 3: Fix aggregation to handle partial coverage

**Files:**
- Modify: `frontend/src/components/projects/DeviationChart.tsx:248-296` (the `displayCalcs` IIFE inside `periodFilterActive` branch).

- [ ] **Step 1: Replace the strict `every`-based aggregation with subset aggregation**

In `frontend/src/components/projects/DeviationChart.tsx`, replace the entire `periodFilterActive` branch body (lines ~249-288). Find:

```tsx
  // When a period filter is active — aggregate all months by material class.
  // Otherwise — show only the latest calendar month in the data.
  const displayCalcs: DashboardCalculation[] = periodFilterActive
    ? (() => {
        const byClass = new Map<number, DashboardCalculation[]>();
        for (const c of calculations) {
          if (!byClass.has(c.material_class_id)) byClass.set(c.material_class_id, []);
          byClass.get(c.material_class_id)!.push(c);
        }
        return Array.from(byClass.values()).map((rows) => {
          const totalQty = rows.reduce((s, r) => s + (r.total_qty ?? 0), 0);
          const deviationAmount = rows.every((r) => r.deviation_amount === null)
            ? null
            : rows.reduce((s, r) => s + (r.deviation_amount ?? 0), 0);
          // Derive % from totals to avoid double-counting when reference prices vary across months.
          // Null when any included row lacks a usable reference price (null or <= 0).
          const refQtyTotal = rows.every((r) => r.reference_price !== null && r.reference_price > 0)
            ? rows.reduce((s, r) => s + (r.reference_price! * (r.total_qty ?? 0)), 0)
            : null;
          const deviationPct =
            deviationAmount !== null && refQtyTotal !== null && refQtyTotal > 0
              ? (deviationAmount / refQtyTotal) * 100
              : null;
          const totalMaterial = rows.reduce((s, r) => s + (r.material_total ?? 0), 0);
          const totalDelivery = rows.reduce((s, r) => s + (r.delivery_total ?? 0), 0);
          const avgPrice = totalQty > 0 ? (totalMaterial + totalDelivery) / totalQty : 0;
          // reference_price: weighted average when all months have a usable price, else null.
          const referencePrice = rows.every((r) => r.reference_price !== null && r.reference_price > 0)
            ? refQtyTotal! / totalQty
            : null;
          return {
            ...rows[0],
            period_start: rows.reduce((m, r) => (r.period_start < m ? r.period_start : m), rows[0].period_start),
            period_end: rows.reduce((m, r) => (r.period_end > m ? r.period_end : m), rows[0].period_end),
            total_qty: totalQty,
            material_total: totalMaterial,
            delivery_total: totalDelivery,
            avg_price: avgPrice,
            reference_price: referencePrice,
            deviation_amount: deviationAmount,
            deviation_pct: deviationPct,
          };
        });
      })()
```

Replace with:

```tsx
  // When a period filter is active — aggregate all months by material class.
  // Deviation is computed over the subset of months covered by a reference price; if no months are covered the class falls into the "без базовой цены" banner.
  // Otherwise — show only the latest calendar month in the data.
  const displayCalcs: AggregatedCalc[] = periodFilterActive
    ? (() => {
        const byClass = new Map<number, DashboardCalculation[]>();
        for (const c of calculations) {
          if (!byClass.has(c.material_class_id)) byClass.set(c.material_class_id, []);
          byClass.get(c.material_class_id)!.push(c);
        }
        return Array.from(byClass.values()).map((rows) => {
          const coveredRows = rows.filter(
            (r) => r.reference_price !== null && r.reference_price > 0,
          );

          const totalQty = rows.reduce((s, r) => s + (r.total_qty ?? 0), 0);
          const coveredQty = coveredRows.reduce((s, r) => s + (r.total_qty ?? 0), 0);

          const deviationAmount = coveredRows.length === 0
            ? null
            : coveredRows.reduce((s, r) => s + (r.deviation_amount ?? 0), 0);

          const refQtyTotal = coveredRows.length === 0
            ? null
            : coveredRows.reduce(
                (s, r) => s + r.reference_price! * (r.total_qty ?? 0),
                0,
              );

          const deviationPct =
            deviationAmount !== null && refQtyTotal !== null && refQtyTotal > 0
              ? (deviationAmount / refQtyTotal) * 100
              : null;

          const totalMaterial = rows.reduce((s, r) => s + (r.material_total ?? 0), 0);
          const totalDelivery = rows.reduce((s, r) => s + (r.delivery_total ?? 0), 0);
          const avgPrice = totalQty > 0 ? (totalMaterial + totalDelivery) / totalQty : 0;

          // reference_price: weighted average over covered months only.
          const referencePrice =
            coveredRows.length === 0 || coveredQty <= 0
              ? null
              : refQtyTotal! / coveredQty;

          return {
            ...rows[0],
            period_start: rows.reduce(
              (m, r) => (r.period_start < m ? r.period_start : m),
              rows[0].period_start,
            ),
            period_end: rows.reduce(
              (m, r) => (r.period_end > m ? r.period_end : m),
              rows[0].period_end,
            ),
            total_qty: totalQty,
            material_total: totalMaterial,
            delivery_total: totalDelivery,
            avg_price: avgPrice,
            reference_price: referencePrice,
            deviation_amount: deviationAmount,
            deviation_pct: deviationPct,
            covered_qty: coveredRows.length === 0 ? null : coveredQty,
          };
        });
      })()
```

Also update the latest-month branch (line ~290-296) to return `AggregatedCalc`:

Find:

```tsx
    : (() => {
        const latestPeriodEnd = calculations.reduce(
          (max, c) => (c.period_end > max ? c.period_end : max),
          calculations[0].period_end,
        );
        return calculations.filter((c) => c.period_end === latestPeriodEnd);
      })();
```

Replace with:

```tsx
    : (() => {
        const latestPeriodEnd = calculations.reduce(
          (max, c) => (c.period_end > max ? c.period_end : max),
          calculations[0].period_end,
        );
        // In single-month mode partial coverage within a row is impossible — covered_qty mirrors total_qty when ref price exists, else null.
        return calculations
          .filter((c) => c.period_end === latestPeriodEnd)
          .map<AggregatedCalc>((c) => ({
            ...c,
            covered_qty:
              c.reference_price !== null && c.reference_price > 0 ? c.total_qty : null,
          }));
      })();
```

- [ ] **Step 2: Add the local `AggregatedCalc` type near the top of the file**

In `frontend/src/components/projects/DeviationChart.tsx`, find the existing type imports (around line 13):

```tsx
import type { DashboardCalculation } from "@/types/dashboard";
```

Add immediately after the imports block (before `interface Props`):

```tsx
/**
 * Local aggregated row used by DeviationChart only. `covered_qty` is the sum of
 * total_qty across months that have a usable reference_price; null when no
 * months are covered (class falls into the "без базовой цены" banner).
 */
type AggregatedCalc = DashboardCalculation & { covered_qty: number | null };
```

- [ ] **Step 3: Run the full test file**

Run: `cd frontend && npx vitest run src/components/projects/DeviationChart.test.tsx`
Expected: BOTH tests pass — the baseline (Task 1) and the partial-coverage test (Task 2).

- [ ] **Step 4: Run typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/projects/DeviationChart.tsx
git commit -m "fix(deviation-chart): aggregate over covered months for partial coverage"
```

---

## Task 4: Add zero-coverage and mixed-coverage tests

**Files:**
- Modify: `frontend/src/components/projects/DeviationChart.test.tsx`

Lock the other branches of the new logic so future regressions are caught.

- [ ] **Step 1: Add zero-coverage test**

Append to the `describe` block:

```tsx
  it("keeps a class with zero coverage in the 'без базовой цены' banner", () => {
    const calculations: DashboardCalculation[] = [
      calc({
        material_class_id: 3,
        material_class_name: "B7.5",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 30,
        avg_price: 5000,
        material_total: 130000,
        delivery_total: 20000,
        reference_price: null,
        deviation_pct: null,
        deviation_amount: null,
      }),
      // Plus a fully-covered class so the chart is rendered (zero-coverage banner has a different code path when alone)
      calc({
        material_class_id: 4,
        material_class_name: "B30",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 100,
        avg_price: 6500,
        reference_price: 6010,
        deviation_pct: 8.15,
        deviation_amount: 49000,
      }),
    ];

    render(<DeviationChart calculations={calculations} periodFilterActive={true} />);

    // Banner present, mentions B7.5
    expect(screen.getByText(/без базовой цены/)).toBeInTheDocument();
    expect(screen.getByText(/B7\.5/)).toBeInTheDocument();
    // B30 still on the chart
    expect(screen.getByText("B30")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Add mixed-coverage test**

Append:

```tsx
  it("renders mixed coverage correctly: full on chart, partial on chart, zero in banner", () => {
    const calculations: DashboardCalculation[] = [
      // Full coverage: B30
      calc({
        material_class_id: 10,
        material_class_name: "B30",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 100,
        avg_price: 6500,
        reference_price: 6010,
        deviation_pct: 8.15,
        deviation_amount: 49000,
      }),
      // Partial coverage: B15 — Jan covered, Feb uncovered
      calc({
        material_class_id: 11,
        material_class_name: "B15",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 50,
        avg_price: 6200,
        reference_price: 5490,
        deviation_pct: 12.93,
        deviation_amount: 35500,
      }),
      calc({
        material_class_id: 11,
        material_class_name: "B15",
        period_start: "2024-02-01",
        period_end: "2024-02-29",
        total_qty: 30,
        avg_price: 6300,
        reference_price: null,
        deviation_pct: null,
        deviation_amount: null,
      }),
      // Zero coverage: B7.5
      calc({
        material_class_id: 12,
        material_class_name: "B7.5",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 20,
        avg_price: 5000,
        reference_price: null,
        deviation_pct: null,
        deviation_amount: null,
      }),
    ];

    render(<DeviationChart calculations={calculations} periodFilterActive={true} />);

    // Chart contains B30 and B15
    expect(screen.getByText("B30")).toBeInTheDocument();
    expect(screen.getByText("B15")).toBeInTheDocument();
    // Banner contains B7.5 and only B7.5
    const banner = screen.getByText(/без базовой цены/);
    expect(banner.textContent).toMatch(/B7\.5/);
    expect(banner.textContent).not.toMatch(/B30/);
    expect(banner.textContent).not.toMatch(/B15/);
  });
```

- [ ] **Step 3: Add latest-month-mode regression test**

Append:

```tsx
  it("does not change behavior when periodFilterActive is false (latest-month mode)", () => {
    const calculations: DashboardCalculation[] = [
      calc({
        material_class_id: 20,
        material_class_name: "B30",
        period_start: "2024-01-01",
        period_end: "2024-01-31",
        total_qty: 100,
        avg_price: 6500,
        reference_price: 6010,
        deviation_pct: 8.15,
        deviation_amount: 49000,
      }),
      calc({
        material_class_id: 20,
        material_class_name: "B30",
        period_start: "2024-02-01",
        period_end: "2024-02-29",
        total_qty: 50,
        avg_price: 6600,
        reference_price: 6010,
        deviation_pct: 9.82,
        deviation_amount: 29500,
      }),
    ];

    // periodFilterActive defaults to false
    render(<DeviationChart calculations={calculations} />);

    // Latest month rendered (Feb 2024 — the row with period_end "2024-02-29")
    expect(screen.getByText("B30")).toBeInTheDocument();
  });
```

- [ ] **Step 4: Run all tests in the file**

Run: `cd frontend && npx vitest run src/components/projects/DeviationChart.test.tsx`
Expected: ALL tests pass (5 total).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/projects/DeviationChart.test.tsx
git commit -m "test: add zero, mixed, and latest-month coverage tests for DeviationChart"
```

---

## Task 5: Surface partial coverage in the existing chart tooltip

**Files:**
- Modify: `frontend/src/components/projects/DeviationChart.tsx` — the `data` mapping (~line 312) and the `ChartTooltipContent` formatter (~line 400-415).

The existing recharts tooltip already shows "+18.4% (+2 945 068.99 ₽)" on hover. We extend its `formatter` to append "Посчитано по X м³ из Y м³" when the class is partially covered. No new component, no extra wrapping — the tooltip is already there.

- [ ] **Step 1: Add `formatNumber` to the imports**

In `frontend/src/components/projects/DeviationChart.tsx`, find the format import (line ~14):

```tsx
import { formatDate, formatMoney, pluralRu } from "@/lib/format";
```

Replace with:

```tsx
import { formatDate, formatMoney, formatNumber, pluralRu } from "@/lib/format";
```

- [ ] **Step 2: Pass `covered_qty` and `total_qty` through to the chart payload**

Find the `data` mapping (around line 312):

```tsx
  const data = withPrice.map((c) => ({
    name: c.material_class_name,
    value: c.deviation_pct!,
    amount: c.deviation_amount,
    fill: fillFor(c.deviation_pct!),
  }));
```

Replace with:

```tsx
  const data = withPrice.map((c) => ({
    name: c.material_class_name,
    value: c.deviation_pct!,
    amount: c.deviation_amount,
    fill: fillFor(c.deviation_pct!),
    coveredQty: c.covered_qty,
    totalQty: c.total_qty,
  }));
```

- [ ] **Step 3: Extend the tooltip formatter to mention partial coverage**

Find the `ChartTooltipContent` formatter (around line 400-415):

```tsx
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    formatter={(value, _name, item) => {
                      const v = Number(value);
                      const pctStr = `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
                      const amtStr =
                        item.payload?.amount != null
                          ? `  (${formatMoney(item.payload.amount)})`
                          : "";
                      return pctStr + amtStr;
                    }}
                    hideLabel
                  />
                }
              />
```

Replace with:

```tsx
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    formatter={(value, _name, item) => {
                      const v = Number(value);
                      const pctStr = `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
                      const amtStr =
                        item.payload?.amount != null
                          ? `  (${formatMoney(item.payload.amount)})`
                          : "";
                      const coveredQty = item.payload?.coveredQty as number | null | undefined;
                      const totalQty = item.payload?.totalQty as number | undefined;
                      const partial =
                        coveredQty != null &&
                        totalQty != null &&
                        coveredQty < totalQty;
                      const partialStr = partial
                        ? ` · ${formatNumber(coveredQty)} м³ из ${formatNumber(totalQty)} м³`
                        : "";
                      return pctStr + amtStr + partialStr;
                    }}
                    hideLabel
                  />
                }
              />
```

- [ ] **Step 4: Verify all existing tests still pass**

Run: `cd frontend && npx vitest run src/components/projects/DeviationChart.test.tsx`
Expected: 5 tests pass.

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/projects/DeviationChart.tsx
git commit -m "feat(deviation-chart): show partial-coverage detail in chart tooltip"
```

---

## Task 6: Final verification

**Files:** none modified.

- [ ] **Step 1: Run full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: all tests pass. No new failures.

- [ ] **Step 2: Run typecheck and lint**

Run: `cd frontend && npx tsc -b --noEmit && npx eslint src --max-warnings=0`
Expected: clean.

- [ ] **Step 3: Manual smoke test (optional, only if dev environment is running)**

If `just dev-backend` and `just dev-frontend` are already up:

1. Open a project where reference prices were configured starting 2024-01-01 but the project has invoices from December 2023 (the original bug scenario).
2. Verify that classes which previously appeared in the "без базовой цены" banner (B15, B7.5, B10 in the screenshot) now appear **on the chart** with deviation bars.
3. Hover any bar — confirm the tooltip shows percent + amount, and for partially-covered classes also "X м³ из Y м³".
4. Verify the "Переплата" banner number now matches the visible bars (no more silent mismatch with the chart contents).

If no dev environment is running, skip this step — automated tests cover the logic.

- [ ] **Step 4: Final commit if anything was tweaked, otherwise nothing to commit.**

```bash
git status
# If clean — done. Otherwise stage and commit relevant fixes.
```

# Units Refactoring — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the frontend to the units refactoring: reference-price creation requires a unit (`unit_id`) that defaults from the material type, the price list shows the unit, invoice items use `raw_unit` instead of `unit`, and an unknown-unit warning returned by save is shown to the user.

**Architecture:** Read-only `useUnits` / `useMaterialTypes` hooks feed a unit `<EntitySelect>` in the reference-price dialog; choosing a material class auto-selects that type's base unit (`material_class → material_type → default_unit`), which the user can override. The invoice `InvoiceItem.unit` field is renamed to `raw_unit` across types, the edit table, fixtures and the save payload; the PUT response's `warnings[]` is rendered as an inline notice in `Review.tsx`. All tests run against MSW with new `/api/units` and `/api/material-types` handlers.

**Tech Stack:** React 18, TypeScript (strict), Vite, TanStack Query v5, shadcn/ui (base-ui `Select`), Vitest + Testing Library + MSW v2.

**Dependency:** The backend plan (`2026-06-08-units-refactoring-backend.md`) must ship first — it defines the contract this plan consumes: `GET /api/units`, `GET /api/material-types`, `unit_id` (+ `unit_symbol`) on reference prices, `raw_unit` in the invoice payload, and `warnings[]` in the PUT `/api/invoices/:id` response. The backend also dual-emits a legacy `unit` key and accepts `unit` on input, so the rename here is non-breaking regardless of deploy order.

**Spec:** `docs/superpowers/specs/2026-06-08-units-refactoring-design.md` (R4), §5.

---

## Commit discipline (every commit must typecheck green)

TypeScript strict means a type change and its consumers must land in the **same commit** — there is no green state between renaming `InvoiceItem.unit` and updating `ReviewItemsTable`. The three tasks below are therefore grouped so each ends on a green `just typecheck-frontend` + `just test-frontend`:

1. **Task 1 — additive foundation** (new files + hooks + handlers; nothing renamed) → green.
2. **Task 2 — reference-price `unit_id`** (referencePrice type + form + list + tests, together) → green.
3. **Task 3 — invoice `raw_unit` + warnings** (invoice type + api + consumers + fixtures + tests, together) → green.

Do NOT commit a state where `just typecheck-frontend` fails.

---

## Shell / command conventions

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"
```

Single test file:

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && npx vitest run src/pages/ReferencePrices.test.tsx 2>&1"
```

`api` (axios) has `baseURL: "/api"` (`src/lib/api.ts`), so client paths are written without the prefix (`/units`) but MSW handlers register the full path (`/api/units`). MSW uses `onUnhandledRequest: "error"` — register a handler for every endpoint a test touches.

---

## File Structure

**New files:**
- `frontend/src/types/unit.ts` — `Unit`, `UnitDimension`, `MaterialType`.
- `frontend/src/services/api/units.ts` — `unitsApi.list()` + `materialTypesApi.list()`.

**Modified files:**
- `frontend/src/types/referencePrice.ts` — `unit_id` + `unit_symbol`.
- `frontend/src/types/invoice.ts` — `unit` → `raw_unit`; `InvoiceUpdateWarning` + `InvoiceUpdateResult`.
- `frontend/src/services/queryKeys.ts` — `units`, `materialTypes` keys.
- `frontend/src/services/queries.ts` — `useUnits`, `useMaterialTypes`; `useUpdateInvoice` returns `InvoiceUpdateResult`.
- `frontend/src/services/api/invoices.ts` — `update()` return type.
- `frontend/src/pages/ReferencePrices.tsx` — unit `<EntitySelect>` + default-by-type + list unit column.
- `frontend/src/components/review/ReviewItemsTable.tsx` — `raw_unit`.
- `frontend/src/pages/Review.tsx` — render save `warnings[]`.
- `frontend/src/test/handlers.ts` — `/api/units`, `/api/material-types` handlers + fixtures; rename `unit`→`raw_unit` in `sampleDocument` items.

**Intentionally NOT in scope (cut speculative surface):** `unitsApi.aliases` / `GET /api/units/:id/aliases` (no consumer), a `UnitAlias` TS type, `normalized_unit_id` on `InvoiceItem` (never rendered), and a hardcoded `MATERIAL_TYPE_LABELS` map (duplicates `MaterialType.name` from the API). If a future feature needs them, add them with their consumer.

---

## Task 1: Additive foundation — types, hooks, handlers

**Files:**
- Create: `frontend/src/types/unit.ts`
- Create: `frontend/src/services/api/units.ts`
- Modify: `frontend/src/services/queryKeys.ts`
- Modify: `frontend/src/services/queries.ts`
- Modify: `frontend/src/test/handlers.ts`

This task is purely additive — it renames nothing, so the suite stays green throughout.

- [ ] **Step 1: Create `frontend/src/types/unit.ts`**

```typescript
import type { ID } from "@/types/common";

export type UnitDimension = "mass" | "volume" | "length" | "count";

export interface Unit {
  id: ID;
  code: string;       // TON, KG, M3, L, M, PCS
  name: string;
  symbol: string;     // т, кг, м³, …
  dimension: UnitDimension;
  base_unit_id: ID | null;  // null → base unit of its dimension
}

export interface MaterialType {
  id: ID;
  code: string;       // concrete, rebar, other
  name: string;
  default_unit: { id: ID; code: string; symbol: string } | null;
}
```

- [ ] **Step 2: Create `frontend/src/services/api/units.ts`**

```typescript
import api from "@/lib/api";
import type { MaterialType, Unit } from "@/types/unit";

export const unitsApi = {
  async list(): Promise<Unit[]> {
    const { data } = await api.get<Unit[]>("/units");
    return data;
  },
};

export const materialTypesApi = {
  async list(): Promise<MaterialType[]> {
    const { data } = await api.get<MaterialType[]>("/material-types");
    return data;
  },
};
```

- [ ] **Step 3: Add query keys**

In `frontend/src/services/queryKeys.ts`, add to the `qk` object:

```typescript
  units: { all: ["units"] as const },
  materialTypes: { all: ["material-types"] as const },
```

- [ ] **Step 4: Add hooks in `frontend/src/services/queries.ts`**

Add imports at the top (matching the existing style):

```typescript
import { materialTypesApi, unitsApi } from "@/services/api/units";
import type { MaterialType, Unit } from "@/types/unit";
```

Add the hooks near the material-classes hooks:

```typescript
export function useUnits() {
  return useQuery<Unit[]>({
    queryKey: qk.units.all,
    queryFn: () => unitsApi.list(),
    staleTime: Infinity,  // reference data — does not change at runtime
  });
}

export function useMaterialTypes() {
  return useQuery<MaterialType[]>({
    queryKey: qk.materialTypes.all,
    queryFn: () => materialTypesApi.list(),
    staleTime: Infinity,
  });
}
```

- [ ] **Step 5: Add MSW fixtures + handlers**

In `frontend/src/test/handlers.ts`, add fixtures near the other `sample*` objects:

```typescript
const sampleUnits = [
  { id: 1, code: "TON", name: "Тонна", symbol: "т", dimension: "mass", base_unit_id: null },
  { id: 2, code: "KG", name: "Килограмм", symbol: "кг", dimension: "mass", base_unit_id: 1 },
  { id: 3, code: "M3", name: "Куб. метр", symbol: "м³", dimension: "volume", base_unit_id: null },
  { id: 4, code: "M", name: "Метр", symbol: "м", dimension: "length", base_unit_id: null },
  { id: 5, code: "PCS", name: "Штука", symbol: "шт", dimension: "count", base_unit_id: null },
];

const sampleMaterialTypes = [
  { id: 1, code: "concrete", name: "Бетон", default_unit: { id: 3, code: "M3", symbol: "м³" } },
  { id: 2, code: "rebar", name: "Арматура", default_unit: { id: 1, code: "TON", symbol: "т" } },
  { id: 3, code: "other", name: "Прочее", default_unit: null },
];
```

Add to the `handlers` array:

```typescript
  http.get("/api/units", () => HttpResponse.json(sampleUnits)),
  http.get("/api/material-types", () => HttpResponse.json(sampleMaterialTypes)),
```

- [ ] **Step 6: Typecheck + full suite (must be green)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: both PASS (additive change).

- [ ] **Step 7: Commit**

```
git add frontend/src/types/unit.ts frontend/src/services/api/units.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts frontend/src/test/handlers.ts
git commit -m "feat(units-fe): units/material-types types, hooks, MSW handlers"
```

---

## Task 2: Reference-price unit — type + form + list + tests (one green commit)

**Files:**
- Modify: `frontend/src/types/referencePrice.ts`
- Modify: `frontend/src/pages/ReferencePrices.tsx`
- Create: `frontend/src/pages/ReferencePrices.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/ReferencePrices.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import ReferencePrices from "./ReferencePrices";

describe("ReferencePrices — unit selection", () => {
  it("auto-defaults unit from the material type and submits unit_id", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([{ id: 7, name: "Тестовый объект", contract_number: null, doc_count: 0 }]),
      ),
      http.get("/api/material-classes", () =>
        HttpResponse.json([{ id: 11, name: "В25", material_type: "concrete" }]),
      ),
      http.post("/api/reference-prices", async ({ request }) => {
        onCreate(await request.json());
        return HttpResponse.json({ id: 1 });
      }),
    );

    renderWithProviders(<ReferencePrices />);
    await user.click(screen.getByRole("button", { name: "Добавить эталон" }));
    const dialog = await screen.findByRole("dialog");
    // Dialog has three selects in render order: project, material_class, unit.
    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[0]);
    await user.click(await screen.findByText("Тестовый объект"));
    await user.click(combos[1]);
    await user.click(await screen.findByText("В25"));
    // default-by-type now set unit to М3 (concrete.default_unit.id === 3).

    await user.type(within(dialog).getByRole("spinbutton"), "8000");  // price (only number input)
    const dateInputs = dialog.querySelectorAll('input[type="date"]');
    fireEvent.change(dateInputs[0], { target: { value: "2026-01-01" } });
    fireEvent.change(dateInputs[1], { target: { value: "2026-12-31" } });

    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    expect(onCreate.mock.calls[0][0].unit_id).toBe(3);  // concrete → M3
  });

  it("shows the unit symbol in the price list", async () => {
    server.use(
      http.get("/api/reference-prices", () =>
        HttpResponse.json([
          {
            id: 1, project_id: 7, material_class_id: 11, material_class_name: "В25",
            unit_id: 3, unit_symbol: "м³", price: 8000,
            period_start: "2026-01-01", period_end: "2026-12-31", source: null,
          },
        ]),
      ),
    );
    renderWithProviders(<ReferencePrices />);
    expect(await screen.findByText("м³")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && npx vitest run src/pages/ReferencePrices.test.tsx 2>&1"`
Expected: FAIL (no unit select; `unit_id` not sent; no symbol column).

- [ ] **Step 3: Update `frontend/src/types/referencePrice.ts`**

```typescript
export interface ReferencePrice {
  id: ID;
  project_id: ID;
  project_name?: string;
  material_class_id: ID;
  material_class_name?: string;
  unit_id: ID;
  unit_symbol?: string | null;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source: string | null;
}

export interface ReferencePriceCreateInput {
  project_id: ID;
  material_class_id: ID;
  unit_id: ID;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source?: string | null;
}
```

(Leave `ReferencePriceUpdateInput` unchanged — the unit is fixed at creation; the backend has no PATCH path for it.)

- [ ] **Step 4: Update `frontend/src/pages/ReferencePrices.tsx`**

(a) Imports — add `useEffect` + `useRef`, the two hooks; the `Unit` type is inferred (no import needed). (Confirmed: the existing material-classes query var is `const classesQ = useMaterialClasses()`.)

```typescript
import { useEffect, useMemo, useRef, useState } from "react";
```

and extend the `@/services/queries` import to include `useUnits` and `useMaterialTypes`:

```typescript
import {
  useProjects,
  useMaterialClasses,
  useMaterialTypes,
  useUnits,
  useReferencePrices,
  useCreateReferencePrice,
  useDeleteReferencePrice,
} from "@/services/queries";
```

(b) Add `unit_id: ""` to BOTH the `useState` initializer (line ~51) and the `reset()` object (line ~60):

```typescript
  const [form, setForm] = useState({
    project_id: "",
    material_class_id: "",
    unit_id: "",
    price: "",
    period_start: "",
    period_end: "",
    source: "",
  });

  const reset = () =>
    setForm({
      project_id: "",
      material_class_id: "",
      unit_id: "",
      price: "",
      period_start: "",
      period_end: "",
      source: "",
    });
```

(c) Load units + types and add the default-by-type effect (place after `const remove = ...`):

```typescript
  const unitsQ = useUnits();
  const typesQ = useMaterialTypes();
  const baseUnits = useMemo(
    () => (unitsQ.data ?? []).filter((u) => u.base_unit_id === null),
    [unitsQ.data],
  );

  // Default the unit to the material type's base unit ONLY when the class actually
  // changes — never on a query refetch (which would clobber a manual override).
  // `lastDefaultedClass` is marked done only AFTER a default is applied, so if the
  // reference data isn't loaded yet the effect retries when classesQ/typesQ arrive.
  const lastDefaultedClass = useRef<string | null>(null);
  useEffect(() => {
    const cls = form.material_class_id;
    if (!cls) {
      lastDefaultedClass.current = null;
      return;
    }
    if (lastDefaultedClass.current === cls) return;  // already defaulted for this class
    const mc = (classesQ.data ?? []).find((c) => String(c.id) === cls);
    const mt = mc ? (typesQ.data ?? []).find((t) => t.code === mc.material_type) : undefined;
    const defId = mt?.default_unit?.id;
    if (defId == null) return;  // data not ready — retry on next deps change, don't mark done
    lastDefaultedClass.current = cls;
    setForm((f) => ({ ...f, unit_id: String(defId) }));
  }, [form.material_class_id, classesQ.data, typesQ.data]);
```

(d) Require the unit in `canSubmit`:

```typescript
  const canSubmit =
    form.project_id &&
    form.material_class_id &&
    form.unit_id &&
    form.price &&
    form.period_start &&
    form.period_end;
```

(e) Send `unit_id` in the create payload (inside `submit()`):

```typescript
      {
        project_id: Number(form.project_id),
        material_class_id: Number(form.material_class_id),
        unit_id: Number(form.unit_id),
        price: Number(form.price),
        period_start: form.period_start,
        period_end: form.period_end,
        source: form.source.trim() || null,
      },
```

(f) Add the unit `<EntitySelect>` in the dialog, right after the "Класс материала" block (after line ~151):

```tsx
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Единица измерения *
                  </Label>
                  <EntitySelect
                    items={baseUnits}
                    value={form.unit_id ? Number(form.unit_id) : null}
                    onChange={(v) => setForm({ ...form, unit_id: v ? String(v) : "" })}
                    getLabel={(u) => `${u.name} (${u.symbol})`}
                    placeholder="Выберите единицу"
                    disabled={unitsQ.isLoading}
                  />
                </div>
```

(g) Show the unit in the list. Add a header after «Цена» (line ~265):

```tsx
                  <TableHead className="text-right">Цена</TableHead>
                  <TableHead>Ед.</TableHead>
```

and a cell after the price `<TableCell>` (line ~283):

```tsx
                    <TableCell className="text-right">
                      <MoneyCell value={rp.price} />
                    </TableCell>
                    <TableCell className="text-fg-secondary">{rp.unit_symbol ?? "—"}</TableCell>
```

- [ ] **Step 5: Run the tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && npx vitest run src/pages/ReferencePrices.test.tsx 2>&1"`
Expected: PASS. If the base-ui option click is flaky, prefer `findByText` (already used) and confirm `getAllByRole("combobox")` returns the three dialog selects in order.

- [ ] **Step 6: Typecheck + full suite (green)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: both PASS. (Adding required `unit_id` to `ReferencePriceCreateInput` is consumed by the form in this same commit — no other caller constructs that input.)

- [ ] **Step 7: Commit**

```
git add frontend/src/types/referencePrice.ts frontend/src/pages/ReferencePrices.tsx frontend/src/pages/ReferencePrices.test.tsx
git commit -m "feat(units-fe): reference-price unit select (default-by-type) + list column"
```

---

## Task 3: Invoice `raw_unit` rename + save warnings (one green commit)

**Files:**
- Modify: `frontend/src/types/invoice.ts`
- Modify: `frontend/src/services/api/invoices.ts`
- Modify: `frontend/src/services/queries.ts`
- Modify: `frontend/src/components/review/ReviewItemsTable.tsx`
- Modify: `frontend/src/pages/Review.tsx`
- Modify: `frontend/src/test/handlers.ts` (rename fixture field)
- Create: `frontend/src/components/review/ReviewItemsTable.test.tsx`
- Create: `frontend/src/pages/Review.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/review/ReviewItemsTable.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { ReviewItemsTable } from "./ReviewItemsTable";
import type { InvoiceItem } from "@/types/invoice";

function makeItem(over: Partial<InvoiceItem> = {}): InvoiceItem {
  return {
    id: 1, raw_name: "Бетон В25", item_type: "material",
    material_class: null, material_class_id: null,
    quantity: 5, raw_unit: "м3", unit_price: 8000, amount: 40000, vat_amount: 8000,
    ...over,
  };
}

describe("ReviewItemsTable — raw_unit", () => {
  it("renders and edits raw_unit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ReviewItemsTable items={[makeItem()]} onChange={onChange} />);
    const unitInput = screen.getByDisplayValue("м3");
    await user.clear(unitInput);
    await user.type(unitInput, "т");
    expect(onChange).toHaveBeenCalled();
    const last = onChange.mock.calls.at(-1)![0] as InvoiceItem[];
    expect(last[0].raw_unit).toContain("т");
  });
});
```

Create `frontend/src/pages/Review.test.tsx`:

```typescript
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import Review from "./Review";

describe("Review — save warnings", () => {
  it("shows the unknown-unit warning returned by the save call", async () => {
    const user = userEvent.setup();
    server.use(
      http.put("/api/invoices/:id", () =>
        HttpResponse.json({
          message: "Сохранено",
          invoice_id: 1,
          warnings: [{
            field: "raw_unit", code: "unknown_unit",
            message: "Единица измерения «бухта» не найдена в справочнике",
          }],
        }),
      ),
    );

    renderWithProviders(
      <Routes>
        <Route path="/review/:id" element={<Review />} />
      </Routes>,
      { initialRoute: "/review/1" },
    );

    // Items tab is the default. Edit a unit to make the form dirty (enables Save).
    const unitInput = (await screen.findAllByDisplayValue("м3"))[0];
    await user.clear(unitInput);
    await user.type(unitInput, "бухта");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText(/не найдена в справочнике/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && npx vitest run src/components/review/ReviewItemsTable.test.tsx src/pages/Review.test.tsx 2>&1"`
Expected: FAIL (component reads `it.unit`; no warning rendering; fixture item has `unit` not `raw_unit`).

- [ ] **Step 3: Update `frontend/src/types/invoice.ts`**

Rename `unit` → `raw_unit` on `InvoiceItem` and add the result types:

```typescript
export interface InvoiceItem {
  id?: ID;
  raw_name: string;
  item_type: "material" | "delivery" | "other";
  material_class: MaterialClassRef | null;
  material_class_id?: ID | null;
  quantity: number;
  raw_unit: string;
  unit_price: number;
  amount: number;
  vat_amount?: number | null;
}

export interface InvoiceUpdateWarning {
  field: string;
  code: string;
  message: string;
}

export interface InvoiceUpdateResult {
  message: string;
  invoice_id: ID;
  warnings: InvoiceUpdateWarning[];
}
```

- [ ] **Step 4: Update `frontend/src/services/api/invoices.ts`**

Change the `update` method's return type to `InvoiceUpdateResult`:

```typescript
import type { InvoiceUpdateInput, InvoiceUpdateResult } from "@/types/invoice";

// inside invoicesApi:
  async update(id: ID, input: InvoiceUpdateInput): Promise<InvoiceUpdateResult> {
    const { data } = await api.put<InvoiceUpdateResult>(`/invoices/${id}`, input);
    return data;
  },
```

- [ ] **Step 5: Update `useUpdateInvoice` in `frontend/src/services/queries.ts`**

Ensure the mutation result type is `InvoiceUpdateResult` so `onSuccess(data)` exposes `data.warnings`. If the hook uses explicit generics, set them; e.g.:

```typescript
export function useUpdateInvoice() {
  const qc = useQueryClient();
  return useMutation<InvoiceUpdateResult, Error, { id: ID; input: InvoiceUpdateInput }>({
    mutationFn: ({ id, input }) => invoicesApi.update(id, input),
    onSuccess: (_data, { id: _id }) => {
      qc.invalidateQueries({ queryKey: qk.documents.all });  // keep existing invalidation
    },
  });
}
```

(Match the existing hook body — only the result type generic and the `import type { InvoiceUpdateResult }` are new. Do not remove existing invalidations.)

- [ ] **Step 6: Update `frontend/src/components/review/ReviewItemsTable.tsx`**

Rename the unit input (lines ~225–232) to `raw_unit`:

```tsx
{/* Ед. */}
<div className="pt-0.5">
  <Input
    value={it.raw_unit}
    onChange={(e) => update(i, { raw_unit: e.target.value })}
    placeholder="м³"
  />
</div>
```

Grep the file for any other `it.unit` / `unit:` references and rename to `raw_unit`.

- [ ] **Step 7: Render warnings in `frontend/src/pages/Review.tsx`**

(a) Add the type import:

```typescript
import type { InvoiceRow, InvoiceUpdateWarning } from "@/types/invoice";
```

(b) Add warning state with the other hooks (near line ~50, BEFORE the early returns):

```typescript
  const [unitWarnings, setUnitWarnings] = useState<InvoiceUpdateWarning[]>([]);
```

(c) Render the notice using the `Surface` ui-domain wrapper (do NOT hand-roll `rounded-lg border …` on a plain `<div>` — `Surface` already provides exactly that). `Surface` is already imported in `Review.tsx`. Insert directly after the `<PageHeader .../>` element (before the `{/* Сверху */}` block):

```tsx
      {unitWarnings.length > 0 && (
        <Surface tone="sunken" padding="sm" className="mt-4 text-sm">
          <p className="font-medium text-fg">Предупреждения</p>
          {unitWarnings.map((w, i) => (
            <p key={i} className="text-fg-secondary">⚠ {w.message}</p>
          ))}
        </Surface>
      )}
```

(d) Capture warnings in the Save `onSuccess` (the `update.mutate(...)` at lines ~246–259):

```tsx
              onClick={() =>
                update.mutate(
                  {
                    id: inv.id,
                    input: {
                      number: inv.number,
                      date: inv.date,
                      supplier_name: inv.supplier_name,
                      supplier_inn: inv.supplier_inn,
                      vat_rate: inv.vat_rate,
                      items: inv.items,
                    },
                  },
                  {
                    onSuccess: (data) => {
                      setOverrides(null);
                      setUnitWarnings(data.warnings ?? []);
                    },
                  },
                )
              }
```

- [ ] **Step 8: Rename the fixture field in `frontend/src/test/handlers.ts`**

The `sampleDocument` invoice items currently carry `unit`. Rename to `raw_unit` (and set a known value `"м3"` for at least the first item so the Review test can find it). Locate it:

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && grep -rn 'unit:' src/test 2>&1"`

For each invoice item in the `sampleDocument` fixture, replace `unit: "…"` with `raw_unit: "…"` (keep `"м3"` on the first item). If the fixture lives in a separate file (e.g. `src/test/fixtures/*.ts`), edit it there.

- [ ] **Step 9: Run the new tests**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/frontend && npx vitest run src/components/review/ReviewItemsTable.test.tsx src/pages/Review.test.tsx 2>&1"`
Expected: PASS. If `findAllByDisplayValue("м3")` returns nothing, confirm Step 8 set the fixture item's `raw_unit` to `"м3"` and that the items tab is the default (`tab` initial state is `"items"`).

- [ ] **Step 10: Typecheck + full suite (green)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: both PASS. Fix any other test fixture or component that still references `InvoiceItem.unit` (grep `src` for `\.unit\b` and `unit:` on invoice items).

- [ ] **Step 11: Commit**

```
git add frontend/src/types/invoice.ts frontend/src/services/api/invoices.ts frontend/src/services/queries.ts frontend/src/components/review/ReviewItemsTable.tsx frontend/src/components/review/ReviewItemsTable.test.tsx frontend/src/pages/Review.tsx frontend/src/pages/Review.test.tsx frontend/src/test/handlers.ts
git commit -m "feat(units-fe): invoice raw_unit rename + save unknown-unit warnings"
```

---

## Task 4: Final verification + docs

**Files:** `docs/testing.md` (+ any fixups).

- [ ] **Step 1: Full suite + typecheck + lint**

```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint-frontend 2>&1"
```

Expected: all PASS.

- [ ] **Step 2: Update `docs/testing.md`**

`docs/testing.md` is a living document ("обновлять при добавлении тестов"). Update the frontend section:
- Bump the TL;DR counts table (frontend file/test counts) to include `ReferencePrices.test.tsx`, `ReviewItemsTable.test.tsx`, `Review.test.tsx`.
- In "Frontend — покрыто" add reference-price unit selection + invoice `raw_unit` edit + save warnings.
- Remove **"`ReviewItemsTable` inline-edit не тестируется"** from the "Frontend — пробелы" list — this plan now covers it.

- [ ] **Step 3: Commit**

```
git add docs/testing.md
git commit -m "docs(units-fe): update testing.md for new frontend tests"
```

- [ ] **Step 4: Commit any code fixups**

```
git add -A
git commit -m "test(units-fe): fix remaining unit→raw_unit references"
```

---

## Self-review reminders for the executor

- **Green commits only:** each task ends on a passing `just typecheck-frontend`. Never commit a state where the type and its consumers disagree.
- **Contract dependency:** assumes the backend plan shipped (`GET /api/units`, `/api/material-types`, `unit_id`/`unit_symbol`, `warnings[]`). Backend dual-compat means deploy order isn't load-bearing.
- **`raw_unit` everywhere:** grep `frontend/src` for `\.unit\b` and `unit:` on invoice items — every occurrence (incl. fixtures) becomes `raw_unit`.
- **Base units only** in the reference-price select (`base_unit_id === null`) — mirrors the backend's base-unit requirement.
- **Default-by-type** consumes `useMaterialTypes` (`material_class.material_type` → `MaterialType.default_unit`); this is the only consumer of that hook in this plan.
- **base-ui Select in tests:** open via `click(getByRole("combobox"))`, choose via `click(getByText(label))`; scope multi-select dialogs with `within(dialog).getAllByRole("combobox")` in render order (project, material_class, unit).
- **shadcn / ui-domain discipline:** reuse existing components — selects via `EntitySelect`, panels/cards via `Surface` (never hand-roll `rounded-lg border border-border-subtle …` on a plain `<div>`), labels via `Label`, tables via shadcn `Table`. There is no Alert/Callout component (only `alert-dialog` for confirmations), so the warnings banner uses `Surface tone="sunken"`.

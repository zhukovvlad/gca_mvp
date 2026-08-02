# Error Docs Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an «Ошибки» tab to ProjectPage showing documents with parse errors or invoice issues, with actions to view PDF, review, reparse, and delete.

**Architecture:** New `ErrorDocsTab` component filters `DocumentSummary[]` to error docs (`status==="error" || has_issues`), renders a table styled after `InvoiceTable`. `ProjectPage` calls `useDocuments(projectId)` to compute the badge count for the tab trigger and passes the raw docs array to `ErrorDocsTab`. `useReparseDocument` is fixed to invalidate the docs list so the tab refreshes after a reparse.

**Tech Stack:** React 18, TypeScript, TanStack Query v5, shadcn/ui + Tailwind CSS v4, Vitest + Testing Library + MSW v2

---

## File Map

| File | Change |
|---|---|
| `frontend/src/services/queries.ts` | Fix `useReparseDocument` — add `documents.list` invalidation |
| `frontend/src/components/projects/ErrorDocsTab.tsx` | **Create** — new tab component |
| `frontend/src/pages/ProjectPage.tsx` | Add `useDocuments` call, error count, tab trigger + content |

---

## Task 1: Fix `useReparseDocument` to invalidate the docs list

**Files:**
- Modify: `frontend/src/services/queries.ts:161-169`

After reparse the `DocumentSummary` in the list changes (`status`, `has_issues`). Currently only `detail` is invalidated.

- [ ] **Step 1: Open `frontend/src/services/queries.ts` and update `useReparseDocument`**

Replace the `onSuccess` block (lines ~165-169):

```typescript
// BEFORE
onSuccess: (data) => {
  qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
  toast.success("Документ переразобран");
},

// AFTER
onSuccess: (data) => {
  qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
  qc.invalidateQueries({ queryKey: qk.documents.list(data.project_id) });
  qc.invalidateQueries({ queryKey: ["dashboard"] });
  toast.success("Документ переразобран");
},
```

> Note: `data` is `DocumentDetail` which extends `DocumentSummary` — it has `project_id`. The `["dashboard"]` invalidation updates the KPI bar error count.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/services/queries.ts && git commit -m 'fix: invalidate documents list and dashboard after reparse'"
```

---

## Task 2: Create `ErrorDocsTab` component

**Files:**
- Create: `frontend/src/components/projects/ErrorDocsTab.tsx`

The component:
- Accepts `docs: DocumentSummary[]` — already filtered upstream is NOT done here; the component does its own filter for clarity and testability.
- Shows a green «всё ок» card when no errors.
- Shows a table with columns: Документ, Загружен, Статус, СФ, Уверенность, Actions.
- Actions: открыть PDF (`ExternalLink`), разобрать (`FileEdit` → `/documents/:id`), переразобрать (`RefreshCw`), удалить (`Trash2` + `AlertDialog`).
- Uses `useReparseDocument` and `useDeleteDocument` mutations from `queries.ts`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/projects/ErrorDocsTab.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { ErrorDocsTab } from "./ErrorDocsTab";
import type { DocumentSummary } from "@/types/invoice";

const makeDoc = (overrides: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: 1,
  project_id: 10,
  filename: "invoice-2024-01.pdf",
  doc_type: "invoice",
  status: "error",
  uploaded_at: "2024-01-15T10:00:00Z",
  invoice_count: 0,
  has_issues: false,
  ai_confidence: null,
  ...overrides,
});

describe("ErrorDocsTab", () => {
  it("shows positive empty state when no error docs", () => {
    const cleanDoc = makeDoc({ status: "parsed", has_issues: false });
    renderWithProviders(<ErrorDocsTab docs={[cleanDoc]} projectId={10} />);
    expect(screen.getByText(/все документы разобраны успешно/i)).toBeInTheDocument();
  });

  it("renders error doc row with filename", () => {
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} projectId={10} />);
    expect(screen.getByText("invoice-2024-01.pdf")).toBeInTheDocument();
  });

  it("renders has_issues doc with 'Проблемы в СФ' status", () => {
    const doc = makeDoc({ status: "parsed", has_issues: true });
    renderWithProviders(<ErrorDocsTab docs={[doc]} projectId={10} />);
    expect(screen.getByText("Проблемы в СФ")).toBeInTheDocument();
  });

  it("renders status=error doc with 'Ошибка парсинга' status", () => {
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} projectId={10} />);
    expect(screen.getByText("Ошибка парсинга")).toBeInTheDocument();
  });

  it("opens delete confirmation dialog before deleting", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} projectId={10} />);

    await user.click(screen.getByRole("button", { name: /удалить/i }));
    // Dialog must appear
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    // API should NOT have been called yet
  });

  it("deletes document after confirmation", async () => {
    const onDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/documents/:id", () => {
        onDelete();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} projectId={10} />);

    await user.click(screen.getByRole("button", { name: /удалить/i }));
    await user.click(await screen.findByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());
  });

  it("calls reparse endpoint on reparse button click", async () => {
    const onReparse = vi.fn();
    server.use(
      http.post("/api/invoices/documents/:id/reparse", ({ params }) => {
        onReparse(params.id);
        return HttpResponse.json(makeDoc({ id: Number(params.id), status: "parsed" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc({ id: 1 })]} projectId={10} />);

    await user.click(screen.getByRole("button", { name: /переразобрать/i }));
    await waitFor(() => expect(onReparse).toHaveBeenCalledWith("1"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | grep -A5 'ErrorDocsTab'"
```

Expected: multiple failures — component doesn't exist yet.

- [ ] **Step 3: Implement `ErrorDocsTab`**

Create `frontend/src/components/projects/ErrorDocsTab.tsx`:

```typescript
import { useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, FileEdit, RefreshCw, Trash2, CheckCircle2 } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui-domain/Button";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDate } from "@/lib/format";
import { useReparseDocument, useDeleteDocument } from "@/services/queries";
import { invoicesApi } from "@/services/api/invoices";
import type { DocumentSummary } from "@/types/invoice";
import type { ID } from "@/types/common";

interface ErrorDocsTabProps {
  docs: DocumentSummary[];
  projectId: ID;
}

function docStatusConfig(doc: DocumentSummary): { tone: "danger" | "neutral"; label: string } {
  if (doc.status === "error") return { tone: "danger", label: "Ошибка парсинга" };
  return { tone: "neutral", label: "Проблемы в СФ" };
}

export function ErrorDocsTab({ docs }: ErrorDocsTabProps) {
  const errorDocs = docs.filter((d) => d.status === "error" || d.has_issues);
  const reparse = useReparseDocument();
  const deleteDoc = useDeleteDocument();
  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);

  if (errorDocs.length === 0) {
    return (
      <div className="mt-6 rounded-lg border border-accent-border bg-accent-soft p-6 flex items-center gap-3">
        <CheckCircle2 size={20} className="text-accent-text shrink-0" />
        <div>
          <p className="font-medium text-accent-text">Все документы разобраны успешно</p>
          <p className="text-sm text-fg-secondary mt-0.5">Ошибок и проблемных счетов нет.</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="mt-6 overflow-hidden rounded-lg border border-border-subtle bg-surface">
        <Table>
          <TableHeader>
            <TableRow className="text-xs text-fg-tertiary hover:bg-transparent">
              <TableHead className="font-medium">Документ</TableHead>
              <TableHead className="font-medium">Загружен</TableHead>
              <TableHead className="font-medium">Статус</TableHead>
              <TableHead className="font-medium text-right">СФ</TableHead>
              <TableHead className="font-medium text-right">Уверенность</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {errorDocs.map((doc) => {
              const { tone, label } = docStatusConfig(doc);
              const isParsing = reparse.isPending && reparse.variables === doc.id;
              return (
                <TableRow key={doc.id} className="hover:bg-surface-hover">
                  <TableCell className="max-w-[240px]">
                    <span
                      className="block truncate font-medium text-fg"
                      title={doc.filename}
                    >
                      {doc.filename}
                    </span>
                    <span className="text-xs text-fg-tertiary">{doc.doc_type}</span>
                  </TableCell>
                  <TableCell className="text-fg-secondary tabular-nums">
                    {formatDate(doc.uploaded_at)}
                  </TableCell>
                  <TableCell>
                    <StatusPill tone={tone} label={label} dot />
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {doc.invoice_count}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums text-fg-secondary">
                    {doc.ai_confidence != null
                      ? `${Math.round(doc.ai_confidence * 100)}%`
                      : "—"}
                  </TableCell>
                  <TableCell className="pr-3">
                    <div className="flex items-center justify-end gap-1">
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <a
                              href={invoicesApi.documentPdfUrl(doc.id)}
                              target="_blank"
                              rel="noreferrer"
                              aria-label="Открыть PDF"
                            >
                              <Button variant="ghost" size="sm" tabIndex={-1}>
                                <ExternalLink size={14} />
                              </Button>
                            </a>
                          }
                        />
                        <TooltipContent>Открыть PDF</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Link to={`/documents/${doc.id}`} aria-label="Разобрать">
                              <Button variant="ghost" size="sm" tabIndex={-1}>
                                <FileEdit size={14} />
                              </Button>
                            </Link>
                          }
                        />
                        <TooltipContent>Перейти к разбору</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Переразобрать"
                              disabled={isParsing}
                              onClick={() => reparse.mutate(doc.id)}
                            >
                              <RefreshCw
                                size={14}
                                className={isParsing ? "animate-spin" : ""}
                              />
                            </Button>
                          }
                        />
                        <TooltipContent>Переразобрать</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Удалить"
                              className="text-fg-tertiary hover:text-danger"
                              onClick={() => setPendingDelete(doc)}
                            >
                              <Trash2 size={14} />
                            </Button>
                          }
                        />
                        <TooltipContent>Удалить документ</TooltipContent>
                      </Tooltip>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(open) => { if (!open) setPendingDelete(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Удалить «{pendingDelete?.filename}»?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Документ и все связанные счета-фактуры будут удалены без возможности
              восстановления.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteDoc.isPending}
              onClick={() => {
                if (pendingDelete) {
                  deleteDoc.mutate(pendingDelete.id, {
                    onSuccess: () => setPendingDelete(null),
                  });
                }
              }}
            >
              {deleteDoc.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | grep -A5 'ErrorDocsTab'"
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/components/projects/ErrorDocsTab.tsx frontend/src/components/projects/ErrorDocsTab.test.tsx && git commit -m 'feat: add ErrorDocsTab component with reparse and delete actions'"
```

---

## Task 3: Wire `ErrorDocsTab` into `ProjectPage`

**Files:**
- Modify: `frontend/src/pages/ProjectPage.tsx`

Changes needed:
1. Import `ErrorDocsTab` and `useDocuments`.
2. Call `useDocuments(projectId ?? undefined)` and compute `errorDocCount`.
3. Add tab trigger «Ошибки» with badge.
4. Add `TabsContent value="errors"` at the end of the tabs block.

- [ ] **Step 1: Add the import and query call**

In `frontend/src/pages/ProjectPage.tsx`, add to the existing imports at the top:

```typescript
// Add to component imports block (near MonthlyTab):
import { ErrorDocsTab } from "@/components/projects/ErrorDocsTab";

// Add to queries imports block (near useProjectSuppliers):
import {
  // ... existing imports ...
  useDocuments,
} from "@/services/queries";
```

In the `// ── queries ──` section of `ProjectPage` (after line ~184), add:

```typescript
const docsQ = useDocuments(projectId ?? undefined);
const errorDocCount = (docsQ.data ?? []).filter(
  (d) => d.status === "error" || d.has_issues,
).length;
```

- [ ] **Step 2: Add the tab trigger**

In the `<TabsList>` block, after the `По месяцам` trigger, add:

```typescript
<TabsTrigger value="errors" data-testid="project-tab-errors">
  Ошибки
  {errorDocCount > 0 && (
    <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold leading-none text-white">
      {errorDocCount}
    </span>
  )}
</TabsTrigger>
```

- [ ] **Step 3: Add the tab content**

After the `</TabsContent>` closing the `monthly` tab (line ~903), add:

```typescript
{/* ────────── TAB: Ошибки ────────── */}
<TabsContent value="errors">
  {projectId && (
    <ErrorDocsTab
      docs={docsQ.data ?? []}
      projectId={projectId}
    />
  )}
</TabsContent>
```

- [ ] **Step 4: Run typecheck**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"
```

Expected: no errors.

- [ ] **Step 5: Run full frontend test suite**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -20"
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/pages/ProjectPage.tsx && git commit -m 'feat: add Ошибки tab to ProjectPage with badge count'"
```

---

## Self-Review

**Spec coverage:**
- ✅ Tab always visible — tab trigger added unconditionally
- ✅ Red badge when errors > 0 — `bg-danger` badge on trigger
- ✅ Positive card when 0 errors — `bg-accent-soft` empty state
- ✅ Table: filename, uploaded_at, status, invoice_count, ai_confidence, actions
- ✅ Open PDF — `<a href=...>` with `documentPdfUrl`
- ✅ Review link — `<Link to="/documents/:id">`
- ✅ Reparse — `useReparseDocument` mutation, spin animation while pending
- ✅ Delete — `useDeleteDocument` with `AlertDialog` confirmation gate
- ✅ Style matches InvoiceTable — same shadcn Table, StatusPill, ghost Buttons, AlertDialog pattern
- ✅ useReparseDocument list invalidation fixed

**Placeholder scan:** None found.

**Type consistency:**
- `ErrorDocsTab` props: `docs: DocumentSummary[]`, `projectId: ID` — used consistently.
- `docStatusConfig` returns `{ tone: "danger" | "neutral"; label: string }` — matches `StatusPill` `tone` prop values used in InvoiceTable.
- `reparse.variables === doc.id` — `useReparseDocument` `mutationFn` receives `ID` (number), and `doc.id` is `ID`. ✅

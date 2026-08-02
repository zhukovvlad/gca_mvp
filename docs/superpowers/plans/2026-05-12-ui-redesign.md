# UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переработать UI согласно `docs/ui/routes-architecture.md` — сущностно-ориентированная навигация с 5 пунктами, новые страницы поставщиков и номенклатуры, карточка объекта с табами.

**Architecture:** Поэтапно: сначала инфраструктура (роуты/навигация), затем переработка существующих страниц, затем новые. Где backend отсутствует — заглушки с empty state. Ветка: `feat/ui-redesign`.

**Tech Stack:** React + TypeScript, React Router v6, TanStack Query, shadcn/ui, Recharts, Tailwind CSS. Тесты: Vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-05-12-ui-redesign-design.md`

---

## File Map

**Добавить shadcn-компоненты:**
- `src/components/ui/chart.tsx` — через CLI
- `src/components/ui/sheet.tsx` — через CLI

**Новые файлы:**
- `src/pages/Suppliers.tsx` — реестр поставщиков
- `src/pages/SupplierPage.tsx` — карточка поставщика (stub)
- `src/pages/Materials.tsx` — номенклатура (переработка MaterialClasses)
- `src/pages/MaterialPage.tsx` — карточка материала
- `src/components/projects/UploadSheet.tsx` — slide-over загрузки
- `src/components/projects/DeviationChart.tsx` — BarChart отклонений
- `src/components/dashboard/PriceChart.tsx` — LineChart динамики цен
- `src/services/api/documents.ts` — API для GET /documents

**Изменяемые файлы:**
- `src/App.tsx` — роуты
- `src/components/layout/TopNav.tsx` — навигация
- `src/pages/Dashboard.tsx` — полная переработка
- `src/pages/ProjectPage.tsx` — 4 таба + slide-over
- `src/services/api/dashboard.ts` — добавить `calculationsAll()`
- `src/services/queries.ts` — новые хуки
- `src/services/queryKeys.ts` — новые ключи

**Удаляемые файлы:** `src/pages/Upload.tsx` (после переноса логики)

---

## Task 1: Добавить shadcn chart и sheet

**Files:**
- Create: `src/components/ui/chart.tsx`
- Create: `src/components/ui/sheet.tsx`

- [ ] Добавить компоненты через CLI:
```bash
cd frontend
npx shadcn add chart sheet
```
Ожидается: созданы `src/components/ui/chart.tsx` и `src/components/ui/sheet.tsx`.

- [ ] Убедиться что файлы созданы:
```bash
ls src/components/ui/chart.tsx src/components/ui/sheet.tsx
```

- [ ] Commit:
```bash
git add src/components/ui/chart.tsx src/components/ui/sheet.tsx
git commit -m "chore: add shadcn chart and sheet components"
```

---

## Task 2: Инфраструктура — навигация и роуты

**Files:**
- Modify: `src/components/layout/TopNav.tsx`
- Modify: `src/App.tsx`

- [ ] Переработать `TopNav.tsx` — 5 пунктов + аватар с меню:

```tsx
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, Building2, Users, Layers, FileSpreadsheet,
  Settings, LogOut, type LucideIcon,
} from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const NAV: { to: string; icon: LucideIcon; label: string; end?: boolean }[] = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Дашборд", end: true },
  { to: "/projects",  icon: Building2,       label: "Объекты" },
  { to: "/suppliers", icon: Users,           label: "Поставщики" },
  { to: "/materials", icon: Layers,          label: "Номенклатура" },
  { to: "/reports",   icon: FileSpreadsheet, label: "Отчёты" },
];

export function TopNav() {
  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border-subtle bg-surface/95 backdrop-blur">
      <div className="container-page flex h-full items-center gap-6">
        <Logo />
        <nav className="flex flex-1 flex-wrap items-center gap-0.5">
          {NAV.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150",
                  isActive
                    ? "bg-surface-hover text-fg"
                    : "text-fg-secondary hover:bg-surface-hover hover:text-fg"
                )
              }
            >
              <Icon size={14} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-hover text-sm font-medium text-fg hover:bg-surface-hover/80">
                ЗВ
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <NavLink to="/settings" className="flex items-center gap-2">
                  <Settings size={14} /> Настройки
                </NavLink>
              </DropdownMenuItem>
              <DropdownMenuItem className="flex items-center gap-2 text-fg-secondary">
                <LogOut size={14} /> Выйти
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
```

- [ ] Обновить `App.tsx` — новые роуты и редиректы:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster, toast } from "sonner";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import Dashboard from "@/pages/Dashboard";
import Projects from "@/pages/Projects";
import ProjectPage from "@/pages/ProjectPage";
import Suppliers from "@/pages/Suppliers";
import SupplierPage from "@/pages/SupplierPage";
import Materials from "@/pages/Materials";
import MaterialPage from "@/pages/MaterialPage";
import Reports from "@/pages/Reports";
import SettingsPage from "@/pages/Settings";
import Review from "@/pages/Review";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 60_000, refetchOnWindowFocus: false },
    mutations: {
      onError: (error: unknown) => {
        toast.error(error instanceof Error ? error.message : "Произошла ошибка");
      },
    },
  },
});

export default function App() {
  return (
    <ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/projects/:id" element={<ProjectPage />} />
              <Route path="/suppliers" element={<Suppliers />} />
              <Route path="/suppliers/:slug" element={<SupplierPage />} />
              <Route path="/materials" element={<Materials />} />
              <Route path="/materials/:id" element={<MaterialPage />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/documents/:id" element={<Review />} />
              {/* Редиректы устаревших роутов */}
              <Route path="/upload" element={<Navigate to="/projects" replace />} />
              <Route path="/material-classes" element={<Navigate to="/materials" replace />} />
              <Route path="/reference-prices" element={<Navigate to="/projects" replace />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
```

- [ ] Создать заглушки для новых страниц (чтобы App.tsx компилировался):

```tsx
// src/pages/Suppliers.tsx
export default function Suppliers() {
  return <div className="container-page py-8"><p>Поставщики — в разработке</p></div>;
}
```
```tsx
// src/pages/SupplierPage.tsx
export default function SupplierPage() {
  return <div className="container-page py-8"><p>Карточка поставщика — в разработке</p></div>;
}
```
```tsx
// src/pages/Materials.tsx
export default function Materials() {
  return <div className="container-page py-8"><p>Номенклатура — в разработке</p></div>;
}
```
```tsx
// src/pages/MaterialPage.tsx
export default function MaterialPage() {
  return <div className="container-page py-8"><p>Карточка материала — в разработке</p></div>;
}
```

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```
Ожидается: 0 ошибок.

- [ ] Commit:
```bash
git add src/components/layout/TopNav.tsx src/App.tsx src/pages/Suppliers.tsx src/pages/SupplierPage.tsx src/pages/Materials.tsx src/pages/MaterialPage.tsx
git commit -m "feat: update navigation to 5 items and add new routes"
```

---

## Task 3: API — documents и calculations без фильтра

**Files:**
- Create: `src/services/api/documents.ts`
- Modify: `src/services/api/dashboard.ts`
- Modify: `src/services/queryKeys.ts`
- Modify: `src/services/queries.ts`

- [ ] Создать `src/services/api/documents.ts`:

```ts
import api from "@/lib/api";

export interface DocumentSummary {
  id: number;
  project_id: number;
  filename: string;
  doc_type: string;
  status: string;
  uploaded_at: string | null;
  invoice_count: number;
  has_issues: boolean;
  ai_confidence: number | null;
}

export const documentsApi = {
  async list(projectId?: number): Promise<DocumentSummary[]> {
    const { data } = await api.get<DocumentSummary[]>("/documents", {
      params: projectId != null ? { project_id: projectId } : undefined,
    });
    return data;
  },
};
```

- [ ] Добавить `calculationsAll()` в `src/services/api/dashboard.ts`:

```ts
// Добавить после существующего метода calculations():
async calculationsAll(): Promise<DashboardCalculation[]> {
  const { data } = await api.get<DashboardCalculation[]>("/dashboard/calculations");
  return data;
},
```

- [ ] Добавить ключи в `src/services/queryKeys.ts` — найти файл и добавить:

```ts
documents: {
  all: ["documents"] as const,
  byProject: (id: number) => ["documents", id] as const,
},
```

- [ ] Добавить хуки в `src/services/queries.ts`:

```ts
// ========== Documents ==========
export function useDocuments(projectId?: number) {
  return useQuery({
    queryKey: projectId != null ? qk.documents.byProject(projectId) : qk.documents.all,
    queryFn: () => documentsApi.list(projectId),
  });
}

// ========== Dashboard (all projects) ==========
export function useAllCalculations() {
  return useQuery({
    queryKey: [...qk.dashboard.calculations(0), "all"],
    queryFn: () => dashboardApi.calculationsAll(),
  });
}
```

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/services/api/documents.ts src/services/api/dashboard.ts src/services/queryKeys.ts src/services/queries.ts
git commit -m "feat: add documents API and all-projects calculations query"
```

---

## Task 4: Дашборд — кросс-портфельная витрина

**Files:**
- Modify: `src/pages/Dashboard.tsx` (полная переработка)
- Create: `src/components/dashboard/PriceChart.tsx`

- [ ] Создать `src/components/dashboard/PriceChart.tsx`:

```tsx
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { ChartContainer } from "@/components/ui/chart";
import type { DashboardCalculation } from "@/types/dashboard";
import { formatMoney } from "@/lib/format";

interface Props {
  calculations: DashboardCalculation[];
}

export function PriceChart({ calculations }: Props) {
  if (!calculations.length) return (
    <div className="flex h-48 items-center justify-center text-sm text-fg-tertiary">
      Рассчитайте отклонения по объектам, чтобы увидеть динамику
    </div>
  );

  // Группируем по классу: { period_start -> { className: avg_price } }
  const classNames = [...new Set(calculations.map((c) => c.material_class_name).filter(Boolean))];
  const byPeriod = new Map<string, Record<string, number>>();
  calculations.forEach((c) => {
    if (!c.period_start || !c.material_class_name) return;
    if (!byPeriod.has(c.period_start)) byPeriod.set(c.period_start, {});
    byPeriod.get(c.period_start)![c.material_class_name] = c.avg_price;
  });
  const data = [...byPeriod.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([period, values]) => ({ period, ...values }));

  const COLORS = ["var(--color-accent)", "#9CC79A", "#EFB75C", "#F0B0A0", "#7DA876"];

  return (
    <ChartContainer config={{}}>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="period" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v) => formatMoney(v)} tick={{ fontSize: 11 }} width={80} />
          <Tooltip formatter={(v: number) => formatMoney(v)} />
          <Legend />
          {classNames.map((name, i) => (
            <Line
              key={name!}
              type="monotone"
              dataKey={name!}
              stroke={COLORS[i % COLORS.length]}
              dot={false}
              strokeWidth={1.5}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
```

- [ ] Переработать `src/pages/Dashboard.tsx`:

```tsx
import { Link } from "react-router-dom";
import { AlertTriangle, Clock } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { PriceChart } from "@/components/dashboard/PriceChart";

import { useProjects, useDocuments, useAllCalculations } from "@/services/queries";
import { formatMoney, formatNumber, formatDate } from "@/lib/format";

export default function Dashboard() {
  const projectsQ = useProjects();
  const docsQ = useDocuments();
  const calcsQ = useAllCalculations();

  const totalOverpay = (calcsQ.data ?? []).reduce((s, c) => s + (c.deviation_amount ?? 0), 0);
  const totalTurnover = (calcsQ.data ?? []).reduce((s, c) => s + (c.material_total ?? 0) + (c.delivery_total ?? 0), 0);
  const issueCount = (docsQ.data ?? []).filter((d) => d.has_issues).length;
  const issueDocs = (docsQ.data ?? []).filter((d) => d.has_issues).slice(0, 5);

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Сводка по портфелю" subtitle="Аналитика закупок по всем объектам" />

      {/* KPI */}
      <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-3">
        {calcsQ.isLoading ? (
          <><Skeleton className="h-[88px]" /><Skeleton className="h-[88px]" /><Skeleton className="h-[88px]" /></>
        ) : (
          <>
            <KpiCard label="Переплата к базовым" value={totalOverpay ? formatMoney(totalOverpay) : "—"} />
            <KpiCard label="Оборот" value={totalTurnover ? formatMoney(totalTurnover) : "—"} />
            <KpiCard label="Требуют внимания" value={String(issueCount)} />
          </>
        )}
      </div>

      {/* Динамика цен */}
      <Surface className="mt-6">
        <h2 className="mb-4 font-serif text-base font-medium text-fg">Динамика цен на ключевые материалы</h2>
        {calcsQ.isLoading ? <Skeleton className="h-48" /> : <PriceChart calculations={calcsQ.data ?? []} />}
      </Surface>

      {/* Объекты */}
      <Surface className="mt-6">
        <h2 className="mb-4 font-serif text-base font-medium text-fg">
          Объекты
          {projectsQ.data && <span className="ml-2 text-sm font-normal text-fg-tertiary">· {projectsQ.data.length}</span>}
        </h2>
        {projectsQ.isLoading ? (
          <div className="space-y-2"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : (projectsQ.data ?? []).length === 0 ? (
          <EmptyState title="Нет объектов" description="Создайте первый объект в разделе Объекты." />
        ) : (
          <div className="divide-y divide-border-subtle">
            {(projectsQ.data ?? []).map((p) => (
              <Link key={p.id} to={`/projects/${p.id}`}
                className="flex items-center justify-between py-3 text-sm hover:text-accent transition-colors">
                <span className="font-medium">{p.name}</span>
                <span className="text-fg-tertiary">{p.doc_count} доков</span>
              </Link>
            ))}
          </div>
        )}
      </Surface>

      {/* Требуют внимания */}
      {issueDocs.length > 0 && (
        <Surface className="mt-6">
          <h2 className="mb-4 font-serif text-base font-medium text-fg">Требуют внимания</h2>
          <div className="space-y-2">
            {issueDocs.map((d) => (
              <Link key={d.id} to={`/documents/${d.id}`}
                className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-hover transition-colors">
                <AlertTriangle size={14} className="text-warning shrink-0" />
                <span className="flex-1 truncate">{d.filename}</span>
                <span className="text-fg-tertiary flex items-center gap-1">
                  <Clock size={12} /> {d.uploaded_at ? formatDate(d.uploaded_at) : "—"}
                </span>
              </Link>
            ))}
          </div>
        </Surface>
      )}
    </div>
  );
}
```

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/pages/Dashboard.tsx src/components/dashboard/PriceChart.tsx
git commit -m "feat: redesign dashboard as cross-portfolio view with price chart"
```

---

## Task 5: Карточка объекта — UploadSheet + DeviationChart

**Files:**
- Create: `src/components/projects/UploadSheet.tsx`
- Create: `src/components/projects/DeviationChart.tsx`

- [ ] Создать `src/components/projects/DeviationChart.tsx`:

```tsx
import {
  BarChart, Bar, XAxis, YAxis, ReferenceLine, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { ChartContainer } from "@/components/ui/chart";
import type { DashboardCalculation } from "@/types/dashboard";

interface Props {
  calculations: DashboardCalculation[];
  onConfigurePrice?: (className: string) => void;
}

export function DeviationChart({ calculations }: Props) {
  if (!calculations.length) return null;

  const data = calculations.map((c) => ({
    name: c.material_class_name ?? "?",
    value: c.deviation_pct ?? 0,
  }));

  return (
    <ChartContainer config={{}}>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11 }} width={40} />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
          <Tooltip formatter={(v: number) => `${v.toFixed(2)}%`} />
          <Bar dataKey="value" radius={[3, 3, 0, 0]}>
            {data.map((entry, i) => (
              <Cell
                key={i}
                fill={entry.value > 0 ? "var(--color-destructive, #D85A30)" : "#9CC79A"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
```

- [ ] Создать `src/components/projects/UploadSheet.tsx`:

```tsx
import { useState, useCallback } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dropzone } from "@/components/ui-domain/Dropzone";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { uploadApi } from "@/services/api/upload";
import { qk } from "@/services/queryKeys";
import type { ID } from "@/types/common";

interface Props {
  projectId: ID;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UploadSheet({ projectId, open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const [uploading, setUploading] = useState(false);

  const handleFiles = useCallback(async (files: File[]) => {
    setUploading(true);
    let success = 0;
    for (const file of files) {
      try {
        await uploadApi.upload(file, projectId);
        success++;
      } catch {
        toast.error(`Ошибка загрузки: ${file.name}`);
      }
    }
    setUploading(false);
    if (success > 0) {
      qc.invalidateQueries({ queryKey: qk.dashboard.invoices(projectId) });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
      toast.success(`Загружено: ${success} файл(ов)`);
      onOpenChange(false);
    }
  }, [projectId, qc, onOpenChange]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[480px]">
        <SheetHeader>
          <SheetTitle>Добавить счёт</SheetTitle>
        </SheetHeader>
        <div className="mt-6">
          <Dropzone onFiles={handleFiles} disabled={uploading} />
          {uploading && (
            <p className="mt-4 text-center text-sm text-fg-secondary">Загрузка…</p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
```

> **Примечание:** Проверить сигнатуру `Dropzone` — если проп называется иначе (не `onFiles`), адаптировать. Аналогично `uploadApi.upload` — проверить существующий API-сервис.

- [ ] Проверить актуальную сигнатуру Dropzone:
```bash
cd frontend && grep -n "onFiles\|onDrop\|onChange\|interface\|Props" src/components/ui-domain/Dropzone.tsx | head -20
```
Адаптировать `UploadSheet.tsx` если имя пропа отличается.

- [ ] Проверить сигнатуру `uploadApi`:
```bash
grep -n "upload\|function\|async" src/services/api/upload.ts
```
Адаптировать вызов если нужно.

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/components/projects/UploadSheet.tsx src/components/projects/DeviationChart.tsx
git commit -m "feat: add UploadSheet slide-over and DeviationChart components"
```

---

## Task 6: Карточка объекта — 4 таба

**Files:**
- Modify: `src/pages/ProjectPage.tsx`

- [ ] Полностью переработать `src/pages/ProjectPage.tsx`:

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Plus } from "lucide-react";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui-domain/Button";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { DeviationCell } from "@/components/ui-domain/DeviationCell";
import { InvoiceTable } from "@/components/invoices/InvoiceTable";
import { UploadSheet } from "@/components/projects/UploadSheet";
import { DeviationChart } from "@/components/projects/DeviationChart";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";

import {
  useProjects, useDashboardInvoices, useDashboardSummary, useDashboardCalculations,
  useAutoCalculate, useCalculate, useMaterialClasses, useReferencePrices,
  useCreateReferencePrice, useDeleteReferencePrice,
} from "@/services/queries";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import type { ID } from "@/types/common";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? Number(id) : null;

  const [uploadOpen, setUploadOpen] = useState(false);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [rpOpen, setRpOpen] = useState(false);
  const [rpForm, setRpForm] = useState({ material_class_id: "", price: "", period_start: "", period_end: "", source: "" });

  const projectsQ = useProjects();
  const project = projectsQ.data?.find((p) => p.id === projectId) ?? null;
  const summaryQ = useDashboardSummary(projectId!);
  const invoicesQ = useDashboardInvoices(projectId!);
  const calcsQ = useDashboardCalculations(projectId!);
  const classesQ = useMaterialClasses();
  const rpQ = useReferencePrices(projectId ?? undefined);
  const auto = useAutoCalculate();
  const calc = useCalculate();
  const createRp = useCreateReferencePrice();
  const deleteRp = useDeleteReferencePrice();

  if (projectsQ.isLoading) return (
    <div className="container-page py-8 space-y-4">
      <Skeleton className="h-8 w-1/3" /><Skeleton className="h-[120px]" />
    </div>
  );

  if (!project) return (
    <div className="container-page py-8">
      <EmptyState title="Объект не найден"
        action={<Link to="/projects"><Button variant="secondary" leftIcon={<ArrowLeft size={14} />}>К списку</Button></Link>} />
    </div>
  );

  const totalDeviation = (calcsQ.data ?? []).reduce((s, c) => s + (c.deviation_amount ?? 0), 0);

  // Поставщики: агрегация из счетов
  const suppliersMap = new Map<string, { name: string; inn: string | null; count: number }>();
  (invoicesQ.data ?? []).forEach((inv) => {
    const key = inv.supplier_name ?? "Неизвестно";
    if (!suppliersMap.has(key)) suppliersMap.set(key, { name: key, inn: null, count: 0 });
    suppliersMap.get(key)!.count++;
  });
  const suppliers = [...suppliersMap.values()];

  return (
    <div className="container-page py-8">
      <Breadcrumbs items={[{ label: "Объекты", to: "/projects" }, { label: project.name }]} />
      <div className="flex items-start justify-between">
        <PageHeader serif title={project.name}
          subtitle={project.contract_number
            ? `Договор № ${project.contract_number} · создан ${formatDate(project.created_at)}`
            : `Создан ${formatDate(project.created_at)}`} />
        <div className="flex gap-2 pt-1">
          <Button variant="secondary" leftIcon={<Download size={14} />}>Экспорт</Button>
          <Button leftIcon={<Plus size={14} />} onClick={() => setUploadOpen(true)}>Добавить счёт</Button>
        </div>
      </div>

      <UploadSheet projectId={projectId!} open={uploadOpen} onOpenChange={setUploadOpen} />

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="invoices">
            Счета{invoicesQ.data ? ` · ${invoicesQ.data.length}` : ""}
          </TabsTrigger>
          <TabsTrigger value="prices">Базовые цены</TabsTrigger>
          <TabsTrigger value="suppliers">
            Поставщики{suppliers.length ? ` · ${suppliers.length}` : ""}
          </TabsTrigger>
        </TabsList>

        {/* ── Обзор ── */}
        <TabsContent value="overview" className="space-y-6 pt-4">
          {/* Баннер вердикт */}
          {(calcsQ.data ?? []).length > 0 && (
            <div className={`rounded-lg border px-5 py-4 ${totalDeviation > 0 ? "border-destructive/30 bg-destructive/5" : "border-success/30 bg-success/5"}`}>
              <p className="text-sm font-medium">
                {totalDeviation > 0 ? "Переплата по объекту:" : "Экономия по объекту:"}{" "}
                <span className={totalDeviation > 0 ? "text-destructive" : "text-success"}>
                  {formatMoney(Math.abs(totalDeviation))}
                </span>
              </p>
            </div>
          )}

          {/* KPI */}
          {summaryQ.data && (
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
              <KpiCard label="Оборот" value={formatMoney(summaryQ.data.total_amount)} />
              <KpiCard label="Объём, м³" value={formatNumber(summaryQ.data.total_qty)} />
              <KpiCard label="Счетов" value={formatNumber(summaryQ.data.invoice_count)} />
              <KpiCard label="Документов" value={formatNumber(summaryQ.data.doc_count)} />
            </div>
          )}

          {/* Расчёт отклонений */}
          <Surface>
            <h3 className="mb-3 text-sm font-medium">Расчёт отклонений</h3>
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Период с</Label>
                <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} className="w-40" />
              </div>
              <div className="space-y-1">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">По</Label>
                <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} className="w-40" />
              </div>
              <Button
                onClick={() => projectId && periodStart && periodEnd && calc.mutate({ project_id: projectId, period_start: periodStart, period_end: periodEnd })}
                disabled={!periodStart || !periodEnd || calc.isPending}
                loading={calc.isPending}
              >Рассчитать</Button>
              <Button variant="secondary"
                onClick={() => projectId && auto.mutateAsync(projectId).then((r) => { if (r.period_start) setPeriodStart(r.period_start); if (r.period_end) setPeriodEnd(r.period_end); })}
                loading={auto.isPending}
              >Авто</Button>
            </div>
          </Surface>

          {/* Бар-чарт */}
          {(calcsQ.data ?? []).length > 0 && (
            <Surface>
              <h3 className="mb-3 text-sm font-medium">Отклонения по классам</h3>
              <DeviationChart calculations={calcsQ.data ?? []} />
            </Surface>
          )}

          {/* Таблица расчётов */}
          {(calcsQ.data ?? []).length > 0 && (
            <Surface padding="none" className="overflow-x-auto">
              <Table className="min-w-[800px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Класс</TableHead>
                    <TableHead>Период</TableHead>
                    <TableHead className="text-right">Ср. цена</TableHead>
                    <TableHead className="text-right">Эталон</TableHead>
                    <TableHead className="text-right">Откл. %</TableHead>
                    <TableHead className="text-right">Откл. ₽</TableHead>
                    <TableHead className="text-right">Объём</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(calcsQ.data ?? []).map((row, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-medium">{row.material_class_name}</TableCell>
                      <TableCell className="text-fg-secondary">{formatDate(row.period_start)} — {formatDate(row.period_end)}</TableCell>
                      <TableCell className="text-right"><MoneyCell value={row.avg_price} /></TableCell>
                      <TableCell className="text-right"><MoneyCell value={row.reference_price} /></TableCell>
                      <TableCell className="text-right"><DeviationCell value={row.deviation_pct} /></TableCell>
                      <TableCell className="text-right"><MoneyCell value={row.deviation_amount} /></TableCell>
                      <TableCell className="text-right text-fg-secondary tabular-nums">{formatNumber(row.total_qty)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>

        {/* ── Счета ── */}
        <TabsContent value="invoices" className="pt-4">
          {invoicesQ.isLoading ? <Skeleton className="h-40" /> : (invoicesQ.data ?? []).length === 0 ? (
            <EmptyState title="Нет счетов" action={<Button onClick={() => setUploadOpen(true)}>Загрузить</Button>} />
          ) : (
            <Surface padding="none"><InvoiceTable invoices={invoicesQ.data ?? []} /></Surface>
          )}
        </TabsContent>

        {/* ── Базовые цены ── */}
        <TabsContent value="prices" className="pt-4">
          <div className="mb-4 flex justify-end">
            <Dialog open={rpOpen} onOpenChange={setRpOpen}>
              <DialogTrigger render={<Button leftIcon={<Plus size={14} />}>Добавить</Button>} />
              <DialogContent>
                <DialogHeader><DialogTitle>Новая базовая цена</DialogTitle></DialogHeader>
                <div className="space-y-4">
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Класс материала *</Label>
                    <EntitySelect items={classesQ.data} value={rpForm.material_class_id ? Number(rpForm.material_class_id) : null}
                      onChange={(v) => setRpForm({ ...rpForm, material_class_id: v ? String(v) : "" })}
                      getLabel={(c) => c.name} placeholder="Выберите класс" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Цена ₽ *</Label>
                      <Input type="number" step="0.01" value={rpForm.price} onChange={(e) => setRpForm({ ...rpForm, price: e.target.value })} />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Источник</Label>
                      <Input value={rpForm.source} onChange={(e) => setRpForm({ ...rpForm, source: e.target.value })} placeholder="договор / прайс" />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Действует с *</Label>
                      <Input type="date" value={rpForm.period_start} onChange={(e) => setRpForm({ ...rpForm, period_start: e.target.value })} />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Действует по *</Label>
                      <Input type="date" value={rpForm.period_end} onChange={(e) => setRpForm({ ...rpForm, period_end: e.target.value })} />
                    </div>
                  </div>
                </div>
                <DialogFooter>
                  <Button variant="ghost" onClick={() => setRpOpen(false)}>Отмена</Button>
                  <Button loading={createRp.isPending}
                    disabled={!rpForm.material_class_id || !rpForm.price || !rpForm.period_start || !rpForm.period_end}
                    onClick={() => {
                      if (!projectId) return;
                      createRp.mutate({
                        project_id: projectId,
                        material_class_id: Number(rpForm.material_class_id),
                        price: Number(rpForm.price),
                        period_start: rpForm.period_start,
                        period_end: rpForm.period_end,
                        source: rpForm.source || null,
                      }, { onSuccess: () => { setRpOpen(false); setRpForm({ material_class_id: "", price: "", period_start: "", period_end: "", source: "" }); } });
                    }}>Сохранить</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          {rpQ.isLoading ? <Skeleton className="h-40" /> : (rpQ.data ?? []).length === 0 ? (
            <EmptyState title="Нет базовых цен" description="Добавьте первую базовую цену для расчёта отклонений." />
          ) : (
            <Surface padding="none">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Класс</TableHead>
                    <TableHead>Период</TableHead>
                    <TableHead className="text-right">Цена</TableHead>
                    <TableHead>Источник</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(rpQ.data ?? []).map((rp) => (
                    <TableRow key={rp.id}>
                      <TableCell className="font-medium">{rp.material_class_name ?? "—"}</TableCell>
                      <TableCell className="text-fg-secondary">{formatDate(rp.period_start)} — {formatDate(rp.period_end)}</TableCell>
                      <TableCell className="text-right"><MoneyCell value={rp.price} /></TableCell>
                      <TableCell className="text-fg-secondary">{rp.source ?? "—"}</TableCell>
                      <TableCell>
                        <Button variant="ghost" size="sm" onClick={() => { if (window.confirm("Удалить?")) deleteRp.mutate(rp.id); }}>×</Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>

        {/* ── Поставщики ── */}
        <TabsContent value="suppliers" className="pt-4">
          {suppliers.length === 0 ? (
            <EmptyState title="Нет поставщиков" description="Поставщики появятся после загрузки счетов-фактур." />
          ) : (
            <Surface padding="none">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Поставщик</TableHead>
                    <TableHead className="text-right">Счетов</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {suppliers.map((s) => (
                    <TableRow key={s.name}>
                      <TableCell className="font-medium">{s.name}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] Проверить что `useReferencePrices` принимает `number | undefined` (не `ID | undefined`) — если нужно, скорректировать вызов.

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/pages/ProjectPage.tsx
git commit -m "feat: add 4 tabs to ProjectPage with upload sheet and deviation chart"
```

---

## Task 7: Реестр поставщиков (`/suppliers`)

**Files:**
- Modify: `src/pages/Suppliers.tsx`

- [ ] Заменить заглушку реальной реализацией:

```tsx
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { useDocuments } from "@/services/queries";

export default function Suppliers() {
  const docsQ = useDocuments();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const suppliers = useMemo(() => {
    const map = new Map<string, { name: string; inn: string | null; docCount: number }>();
    (docsQ.data ?? []).forEach((doc) => {
      // supplier_name хранится внутри invoices — но documents summary не включает invoices.
      // Используем filename как fallback пока нет агрегированного API.
      // Реальная агрегация будет после добавления backend-эндпоинта.
    });
    return [...map.values()];
  }, [docsQ.data]);

  // Временно: показываем empty state с объяснением пока нет backend агрегации
  return (
    <div className="container-page py-8">
      <PageHeader serif title="Поставщики" subtitle="Компании, с которыми работает портфель" />
      <div className="mt-8">
        <EmptyState
          title="Аналитика по поставщикам"
          description="Для отображения реестра поставщиков требуется обновление сервиса. Данные по поставщикам доступны внутри карточки каждого объекта."
        />
      </div>
    </div>
  );
}
```

> **Почему заглушка:** `GET /documents` не возвращает `supplier_name` на уровне документа — он хранится внутри `invoices[]`. Загружать все документы с полными данными инвойсов слишком дорого. Реестр поставщиков требует отдельного backend-эндпоинта для агрегации. Это явно указано в спеке как stub.

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/pages/Suppliers.tsx
git commit -m "feat: suppliers page with stub pending backend aggregation endpoint"
```

---

## Task 8: Карточка поставщика (`/suppliers/:slug`)

**Files:**
- Modify: `src/pages/SupplierPage.tsx`

- [ ] Заменить заглушку:

```tsx
import { useParams } from "react-router-dom";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/ui-domain/EmptyState";

export default function SupplierPage() {
  const { slug } = useParams<{ slug: string }>();
  const name = slug ? decodeURIComponent(slug) : "Поставщик";

  return (
    <div className="container-page py-8">
      <PageHeader serif title={name} subtitle="Профиль поставщика" />
      <Tabs defaultValue="overview" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="invoices">Счета</TabsTrigger>
          <TabsTrigger value="projects">Объекты</TabsTrigger>
          <TabsTrigger value="compare">Сравнение</TabsTrigger>
        </TabsList>
        {(["overview", "invoices", "projects", "compare"] as const).map((tab) => (
          <TabsContent key={tab} value={tab} className="pt-6">
            <EmptyState
              title="Подробная аналитика по поставщику"
              description="Будет доступна после обновления сервиса."
            />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
```

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/pages/SupplierPage.tsx
git commit -m "feat: supplier detail page with tab structure and empty states"
```

---

## Task 9: Номенклатура (`/materials`, `/materials/:id`)

**Files:**
- Modify: `src/pages/Materials.tsx`
- Modify: `src/pages/MaterialPage.tsx`

- [ ] Переработать `src/pages/Materials.tsx` на основе существующего `MaterialClasses.tsx` — скопировать содержимое и изменить:
  - Заголовок: «Номенклатура»
  - Подзаголовок: «Классы материалов для агрегации цен»
  - Строки таблицы — добавить клик → `navigate(\`/materials/${id}\`)`

```tsx
import { useState } from "react";
import { Plus, Trash2, Layers } from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { StatusPill } from "@/components/ui-domain/StatusPill";

import { useMaterialClasses, useCreateMaterialClass, useDeleteMaterialClass } from "@/services/queries";
import { formatDate } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = { concrete: "Бетон", rebar: "Арматура", other: "Прочее" };

export default function Materials() {
  const list = useMaterialClasses();
  const create = useCreateMaterialClass();
  const remove = useDeleteMaterialClass();
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("concrete");

  const submit = () => {
    if (!name.trim()) return;
    create.mutate({ name: name.trim(), material_type: type }, {
      onSuccess: () => { setOpen(false); setName(""); setType("concrete"); },
    });
  };

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Номенклатура" subtitle="Классы материалов для агрегации цен"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button leftIcon={<Plus size={14} />}>Добавить класс</Button>} />
            <DialogContent>
              <DialogHeader><DialogTitle>Новый класс материала</DialogTitle></DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Тип материала</Label>
                  <Select value={type} onValueChange={(v: string | null) => setType(v ?? "concrete")}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="concrete">Бетон</SelectItem>
                      <SelectItem value="rebar">Арматура</SelectItem>
                      <SelectItem value="other">Прочее</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">Название *</Label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="например, В25, А500С" />
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>Отмена</Button>
                <Button onClick={submit} loading={create.isPending} disabled={!name.trim()}>Добавить</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <div className="mt-6">
        {list.isLoading ? (
          <Surface padding="none"><Skeleton className="h-10" /><Skeleton className="h-10" /></Surface>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState icon={<Layers size={20} />} title="Нет классов" description="Добавьте первый класс — например, бетон В25."
            action={<Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>Добавить класс</Button>} />
        ) : (
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Название</TableHead><TableHead>Тип</TableHead>
                  <TableHead>Создан</TableHead><TableHead className="w-12" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data ?? []).map((c) => (
                  <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/materials/${c.id}`)}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell><StatusPill tone="neutral" label={TYPE_LABELS[c.material_type] ?? c.material_type} /></TableCell>
                    <TableCell className="text-fg-secondary">{formatDate(c.created_at)}</TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm"
                        onClick={(e) => { e.stopPropagation(); if (window.confirm(`Удалить «${c.name}»?`)) remove.mutate(c.id); }}
                        aria-label="Удалить"><Trash2 size={14} /></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Surface>
        )}
      </div>
    </div>
  );
}
```

- [ ] Переработать `src/pages/MaterialPage.tsx`:

```tsx
import { useParams } from "react-router-dom";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { StatusPill } from "@/components/ui-domain/StatusPill";

import { useMaterialClasses, useReferencePrices } from "@/services/queries";
import { formatDate } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = { concrete: "Бетон", rebar: "Арматура", other: "Прочее" };

export default function MaterialPage() {
  const { id } = useParams<{ id: string }>();
  const materialId = id ? Number(id) : null;

  const classesQ = useMaterialClasses();
  const material = classesQ.data?.find((c) => c.id === materialId) ?? null;
  const rpQ = useReferencePrices(); // все базовые цены — фильтруем на фронтенде

  const materialPrices = (rpQ.data ?? []).filter((rp) => rp.material_class_id === materialId);

  if (classesQ.isLoading) return <div className="container-page py-8"><Skeleton className="h-8 w-1/3" /></div>;
  if (!material) return <div className="container-page py-8"><EmptyState title="Материал не найден" /></div>;

  return (
    <div className="container-page py-8">
      <Breadcrumbs items={[{ label: "Номенклатура", to: "/materials" }, { label: material.name }]} />
      <div className="flex items-center gap-3">
        <PageHeader serif title={material.name} />
        <StatusPill tone="neutral" label={TYPE_LABELS[material.material_type] ?? material.material_type} />
      </div>

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="suppliers">Поставщики</TabsTrigger>
          <TabsTrigger value="prices">Базовые цены{materialPrices.length ? ` · ${materialPrices.length}` : ""}</TabsTrigger>
          <TabsTrigger value="projects">Объекты</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-6">
          <EmptyState title="История цен" description="График динамики цен появится после обновления сервиса." />
        </TabsContent>

        <TabsContent value="suppliers" className="pt-6">
          <EmptyState title="Поставщики материала" description="Будет доступно после обновления сервиса." />
        </TabsContent>

        <TabsContent value="prices" className="pt-6">
          {rpQ.isLoading ? <Skeleton className="h-40" /> : materialPrices.length === 0 ? (
            <EmptyState title="Нет базовых цен" description="Базовые цены настраиваются в карточке объекта." />
          ) : (
            <Surface padding="none">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Объект</TableHead>
                    <TableHead>Период</TableHead>
                    <TableHead className="text-right">Базовая цена</TableHead>
                    <TableHead>Источник</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {materialPrices.map((rp) => (
                    <TableRow key={rp.id}>
                      <TableCell className="font-medium">{rp.project_name ?? "—"}</TableCell>
                      <TableCell className="text-fg-secondary">{formatDate(rp.period_start)} — {formatDate(rp.period_end)}</TableCell>
                      <TableCell className="text-right"><MoneyCell value={rp.price} /></TableCell>
                      <TableCell className="text-fg-secondary">{rp.source ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>

        <TabsContent value="projects" className="pt-6">
          <EmptyState title="Объекты с этим материалом" description="Будет доступно после обновления сервиса." />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] Убедиться что `useReferencePrices()` без аргумента работает корректно — проверить сигнатуру:
```bash
cd frontend && grep -n "useReferencePrices\|function useReferencePrices" src/services/queries.ts
```

- [ ] Проверить сборку:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/pages/Materials.tsx src/pages/MaterialPage.tsx
git commit -m "feat: materials registry and material detail page with plan prices tab"
```

---

## Task 10: Финальная проверка и тесты

**Files:**
- Modify: тест-файлы для изменённых страниц

- [ ] Запустить все тесты:
```bash
cd frontend && npm run test
```

- [ ] Если тесты `Dashboard.test.tsx` падают (страница полностью переработана) — обновить MSW handlers и ожидания:
```bash
# Проверить что ломается:
cd frontend && npm run test -- src/pages/Dashboard.test.tsx
```

Обновить `src/test/handlers.ts` — добавить handler для `GET /documents` без фильтра если его нет:
```ts
http.get("/api/documents", () => {
  return HttpResponse.json([]);
}),
```

- [ ] Запустить тесты повторно, убедиться что все проходят:
```bash
cd frontend && npm run test
```

- [ ] Финальная сборка:
```bash
cd frontend && npm run build
```

- [ ] Commit:
```bash
git add src/test/
git commit -m "test: update dashboard tests for new cross-portfolio layout"
```

---

## Self-Review

**Покрытие спека:**
- ✅ Этап 1 (навигация, роуты) — Task 2
- ✅ Этап 2 (дашборд) — Task 4
- ✅ Этап 3 (карточка объекта с табами, UploadSheet) — Task 5, 6
- ✅ Этап 4 (поставщики) — Task 7, 8
- ✅ Этап 5 (номенклатура) — Task 9
- ✅ shadcn chart/sheet — Task 1
- ✅ API слой — Task 3

**Типы:** `DashboardCalculation` используется в Task 4 и 5 — тип уже существует в `src/types/dashboard.ts`. `DocumentSummary` определяется в Task 3 и используется в Task 4.

**Placeholders:** нет TBD/TODO в коде. Заглушки — это намеренные empty state с объяснением.

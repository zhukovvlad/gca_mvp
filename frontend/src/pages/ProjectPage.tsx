import { toast } from "sonner";
import { useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Download, Loader2, Plus, Trash2, Pencil } from "lucide-react";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { InvoiceKpiBar } from "@/components/invoices/InvoiceKpiBar";
import { InvoiceTable } from "@/components/invoices/InvoiceTable";
import { UploadSheet } from "@/components/projects/UploadSheet";
import { DeviationChart } from "@/components/projects/DeviationChart";
import { MonthlyTab } from "@/components/projects/MonthlyTab";
import { ErrorDocsTab } from "@/components/projects/ErrorDocsTab";
import { CorridorsTab } from "@/components/projects/CorridorsTab";
import { DirectionSwitcher } from "@/components/projects/DirectionSwitcher";

import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";

import {
  useProjects,
  useDashboardSummary,
  useDashboardInvoices,
  useDashboardCalculations,
  useReferencePrices,
  useCreateReferencePrice,
  useUpdateReferencePrice,
  useDeleteReferencePrice,
  useMaterialClasses,
  useProjectSuppliers,
  useSupplierExclusions,
  useToggleSupplierExclusion,
  useDocuments,
  useUnits,
} from "@/services/queries";
import { reportsApi } from "@/services/api/reports";
import { isDocBusy } from "@/services/processingRefetchInterval";
import { useDebounce } from "@/lib/useDebounce";
import { useDefaultUnitId } from "@/lib/useDefaultUnitId";

import { formatDate, formatMoney, formatNumber, formatPercent, pluralRu } from "@/lib/format";
import { MONTH_NAMES_RU } from "@/lib/constants";
import type { ID } from "@/types/common";
import type { ReferencePrice } from "@/types/referencePrice";
import type { DashboardSummary, DashboardCalculation } from "@/types/dashboard";
import type { DocumentSummary, DashboardInvoiceRow } from "@/types/invoice";

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

/** KPI «Компенсация» — подпись, значение и danger/accent-классы. Значение —
 * компенсация за пределами коридора: на сводке «Все» (full_compensation_amount),
 * в режиме направления (direction.overpayment). Знак задаёт сторону: + (доплата
 * поставщику) → «подрядчику», − (возврат заказчику) → «подрядчиком». Период
 * вынесен в подвал KPI-ряда, поэтому в знаковых подписях его не дублируем. */
function deviationKpi(amount: number | null | undefined) {
  return {
    label:
      amount != null
        ? amount > 0
          ? "Компенсация подрядчику"
          : "Компенсация подрядчиком"
        : "Компенсация за весь период",
    value:
      amount != null
        ? amount > 0
          ? `+${formatMoney(amount)}`
          : formatMoney(Math.abs(amount))
        : "—",
    className:
      amount != null
        ? amount > 0
          ? "bg-danger-soft border-danger-border"
          : "bg-accent-soft border-accent-border"
        : "",
    valueClassName:
      amount != null
        ? amount > 0
          ? "text-danger-text"
          : "text-accent-text"
        : "",
  };
}

// Резерв высоты строки вкладок: высота = TabsList h-8 + gap-2 контейнера Tabs (§3.1,
// чтобы контент не прыгал между режимами с табами и без них).
function TabBarSlot() {
  return <div aria-hidden className="h-10" />;
}

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

// Известные значения вкладок (TabsTrigger value ниже) — используется для
// валидации deep-link ?tab= при инициализации activeTab (Codex P2, PR #37:
// ретрай дубликата упавшего документа ведёт на /projects/:id?tab=errors).
const PROJECT_TAB_VALUES = [
  "overview",
  "invoices",
  "prices",
  "corridors",
  "suppliers",
  "monthly",
  "errors",
] as const;
type ProjectTabValue = (typeof PROJECT_TAB_VALUES)[number];

/** Валидирует значение ?tab= из URL; невалидное/отсутствующее → "overview". */
function resolveInitialTab(raw: string | null): ProjectTabValue {
  return (PROJECT_TAB_VALUES as readonly string[]).includes(raw ?? "")
    ? (raw as ProjectTabValue)
    : "overview";
}

// ─────────────────────────────────────────────
// File-local sub-views
// ─────────────────────────────────────────────

/** «Все» → view=errors: ошибки объекта (§3.2 п.2) */
function ErrorsView({
  isLoading,
  docs,
  onBack,
}: {
  isLoading: boolean;
  docs: DocumentSummary[];
  onBack: () => void;
}) {
  return (
    <>
      <TabBarSlot />
      <div className="mt-6 space-y-4" data-testid="project-errors-view">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-lg">Ошибки объекта</h2>
          <button
            type="button"
            className="text-sm text-accent-text hover:underline"
            onClick={onBack}
          >
            ← к сводке
          </button>
        </div>
        {isLoading
          ? <Skeleton className="h-32" />
          : <ErrorDocsTab docs={docs} />}
      </div>
    </>
  );
}

/** Сводка «Все направления» (§3.2) */
function AllDirectionsSummaryView({
  summaryData,
  errorDocCount,
  calculations,
  invoices,
  invoicesLoading,
  busyDocIds,
  onOpenErrors,
  changeDirection,
  periodStart,
  periodEnd,
  dataStart,
  dataEnd,
  displayStart,
  displayEnd,
  onPeriodStartChange,
  onPeriodEndChange,
  onPeriodReset,
}: {
  summaryData: DashboardSummary;
  errorDocCount: number;
  calculations: DashboardCalculation[];
  invoices: DashboardInvoiceRow[];
  invoicesLoading: boolean;
  busyDocIds: Set<ID>;
  onOpenErrors: () => void;
  changeDirection: (code: string) => void;
  periodStart: string;
  periodEnd: string;
  dataStart: string;
  dataEnd: string;
  displayStart: string;
  displayEnd: string;
  onPeriodStartChange: (v: string) => void;
  onPeriodEndChange: (v: string) => void;
  onPeriodReset: () => void;
}) {
  const allDev = deviationKpi(summaryData.full_compensation_amount);
  const [showOnlyOther, setShowOnlyOther] = useState(false);
  const invoicesSectionRef = useRef<HTMLDivElement>(null);
  const otherCount = useMemo(
    () => invoices.filter((i) => i.directions.length === 0).length,
    [invoices],
  );
  const shownInvoices = showOnlyOther
    ? invoices.filter((i) => i.directions.length === 0)
    : invoices;
  return (
    <div className="space-y-6">
        {/* KPI ×4 */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <KpiCard
            label="Оборот, ₽ с НДС"
            value={formatMoney(summaryData.total_amount)}
            breakdown={[
              ...summaryData.directions.map((d) => ({ label: d.name, value: formatMoney(d.turnover) })),
              ...(summaryData.delivery_total > 0 ? [{ label: "Доставка", value: formatMoney(summaryData.delivery_total) }] : []),
              ...(summaryData.other_total > 0 ? [{ label: "Прочее", value: formatMoney(summaryData.other_total) }] : []),
            ]}
          />
          {(() => {
            const volValues = summaryData.directions
              .filter((d) => d.volume !== null)
              .map((d) => ({
                label: d.name, // именительный падеж — без склонений (масштабируется на кирпич и далее)
                value: `${formatNumber(d.volume!)} ${d.volume_unit}`,
              }));
            return (
              <KpiCard
                label="Объёмы"
                values={volValues.length > 0 ? volValues : undefined}
                value={volValues.length === 0 ? "—" : undefined}
              />
            );
          })()}
          <KpiCard
            label="Счетов"
            value={formatNumber(summaryData.invoice_count)}
            breakdown={[ /* breakdown, не suffix: длинная разбивка в строку у числа теснится */
              ...summaryData.directions.map((d) => ({ label: d.name, value: formatNumber(d.invoice_count) })),
              ...(summaryData.mixed_invoice_count > 0 ? [{ label: "Смешанные", value: formatNumber(summaryData.mixed_invoice_count) }] : []),
              ...(otherCount > 0 ? [{
                label: "Прочие",
                value: formatNumber(otherCount),
                onClick: () => {
                  setShowOnlyOther(true);
                  invoicesSectionRef.current?.scrollIntoView?.({ behavior: "smooth" });
                },
              }] : []),
            ]}
          />
          <KpiCard
            label={allDev.label}
            value={allDev.value}
            className={allDev.className}
            valueClassName={allDev.valueClassName}
            breakdown={summaryData.directions
              .filter((d) => d.overpayment !== null)
              .map((d) => ({ label: d.name, value: formatMoney(d.overpayment!) }))}
          />
        </div>

        {/* Алерт нераспознанных (§3.2 п.2) — источник тот же, что бейдж «Ошибки» */}
        {errorDocCount > 0 && (
          <button
            type="button"
            data-testid="unrecognized-alert"
            onClick={onOpenErrors}
            className="flex w-full items-center justify-between rounded-lg border border-danger-border bg-danger-soft px-4 py-2.5 text-sm text-danger-text hover:opacity-90"
          >
            <span>
              {formatNumber(errorDocCount)} документ{pluralRu(errorDocCount)}{" "}
              {pluralRu(errorDocCount) === ""
                ? "не распознан и не учтён"
                : "не распознаны и не учтены"}{" "}
              в цифрах
            </span>
            <span>Разобрать →</span>
          </button>
        )}

        {/* Отклонения секциями по направлениям, top-5 (§3.2 п.3) */}
        <DeviationChart
          calculations={calculations}
          periodFilterActive
          topN={5}
          groups={summaryData.directions.map((d) => ({
            code: d.code,
            name: d.name,
            onOpen: () => changeDirection(d.code),
          }))}
          periodStart={periodStart}
          periodEnd={periodEnd}
          dataStart={dataStart}
          dataEnd={dataEnd}
          displayStart={displayStart}
          displayEnd={displayEnd}
          onPeriodStartChange={onPeriodStartChange}
          onPeriodEndChange={onPeriodEndChange}
          onPeriodReset={onPeriodReset}
        />

        {/* Все счета объекта (включая прочие/без направления) */}
        <div ref={invoicesSectionRef} className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg">
              Счета{invoices.length > 0 ? ` · ${invoices.length}` : ""}
            </h2>
            {showOnlyOther && (
              <button
                type="button"
                className="text-sm text-accent-text hover:underline"
                onClick={() => setShowOnlyOther(false)}
              >
                × все счета
              </button>
            )}
          </div>
          {invoicesLoading ? (
            <div className="space-y-2" data-testid="invoices-overview-loading">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : invoices.length === 0 ? (
            <p className="text-sm text-fg-tertiary">Нет счетов.</p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
              <InvoiceTable invoices={shownInvoices} busyDocIds={busyDocIds} />
            </div>
          )}
        </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────
export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const parsed = id ? Number(id) : NaN;
  const projectId: ID | null = Number.isFinite(parsed) && parsed > 0 ? parsed : null;

  // ── upload sheet ──
  const [uploadOpen, setUploadOpen] = useState(false);

  // ── export ──
  const [isExporting, setIsExporting] = useState(false);

  const handleExport = async () => {
    if (!projectId || isExporting) return;
    setIsExporting(true);
    try {
      const blob = await reportsApi.excelBlob({
        project_id: projectId,
        period_start: periodStart || undefined,
        period_end: periodEnd || undefined,
        direction: scopedDirection,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const safeName = String(project?.name ?? projectId)
        .replace(/[\\/:*?"<>|\r\n]/g, "-")
        .trim()
        .replace(/^[ .-]+|[ .-]+$/g, "");
      const periodSuffix = periodStart || periodEnd ? `_${periodStart || ""}–${periodEnd || ""}` : "";
      // Суффикс направления в имени файла — канон §6.7
      const dirName = scopedDirection ? directions?.find((d) => d.code === scopedDirection)?.name : undefined;
      const dirSuffix = dirName ? `-${dirName}` : "";
      a.download = `отчёт-${safeName || projectId}${dirSuffix}${periodSuffix}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL?.(url), 0);
    } catch (err) {
      // axios blob responses return error body as Blob — parse it to get backend detail
      let message = "Не удалось сформировать отчёт";
      try {
        const blob = (err as { response?: { data?: unknown } })?.response?.data;
        if (blob instanceof Blob) {
          const json = JSON.parse(await blob.text()) as { detail?: unknown };
          if (typeof json.detail === "string") message = json.detail;
        }
      } catch {
        // ignore parse errors, keep generic message
      }
      toast.error(message);
    } finally {
      setIsExporting(false);
    }
  };

  // ── calculation period filters ──
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const debouncedPeriodStart = useDebounce(periodStart, 400);
  const debouncedPeriodEnd = useDebounce(periodEnd, 400);

  // ── invoice month filter (set when navigating from «По месяцам» tab) ──
  const [invoiceMonthFilter, setInvoiceMonthFilter] = useState<{ year: number; month: number } | null>(null);

  // ── reference price dialog ──
  const [priceDialogOpen, setPriceDialogOpen] = useState(false);
  const [rpClassId, setRpClassId] = useState<string>("");
  const [rpUnitId, setRpUnitId] = useState<string>("");
  const [rpPrice, setRpPrice] = useState("");
  const [rpStart, setRpStart] = useState("");
  const [rpEnd, setRpEnd] = useState("");
  const [rpSource, setRpSource] = useState("");

  // ── edit reference price dialog ──
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editRpId, setEditRpId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [editStart, setEditStart] = useState("");
  const [editEnd, setEditEnd] = useState("");
  const [editSource, setEditSource] = useState("");

  // ── delete reference price dialog ──
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteRpId, setDeleteRpId] = useState<number | null>(null);

  // ── exclusion inline form ──
  const [exclusionPopover, setExclusionPopover] = useState<{
    supplierId: number;
    reason: string;
  } | null>(null);

  // ── queries ──
  const projectsQ = useProjects();
  const project = projectsQ.data?.find((p) => p.id === projectId) ?? null;

  const summaryQ = useDashboardSummary(projectId);

  // ── направление: трёхзначное состояние из URL (спека §7.2) ──
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDirection = searchParams.get("direction"); // null | 'all' | code

  // ── active tab: инициализируется из ?tab= один раз (ленивый инициализатор),
  // обратная запись в URL при переключении не делается (вне объёма фикса).
  const [activeTab, setActiveTab] = useState(() => resolveInitialTab(searchParams.get("tab")));
  const directions = summaryQ.data?.directions;       // undefined пока summary грузится

  // undefined = summary ещё не резолвился в этом кадре — НЕ 'all'.
  const direction: string | undefined =
    directions === undefined ? undefined
    : rawDirection === "all" ? "all"
    : directions.some((d) => d.code === rawDirection) ? (rawDirection as string)
    : directions.length === 1 ? directions[0].code     // автодефолт моно-объекта (ADR #10)
    : "all";

  // Legacy — ТОЛЬКО настоящий пустой проект (ADR #11); ошибка summary разведена выше.
  const isLegacy = directions !== undefined && directions.length === 0;
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

  // Сброс вкладки на «Обзор» при смене direction — back/forward идут мимо
  // onChange (§7.2). Паттерн «adjust state during render» (react.dev/learn/
  // you-might-not-need-an-effect): синхронно, без лишнего рендера с эффектом.
  // Первоначальное разрешение direction (undefined → значение, пока грузится
  // summary) сбросом не считается — иначе deep-link ?tab= (Codex P2, fix 1)
  // затирался бы «Обзором» сразу после первой загрузки summary.
  const [prevDirection, setPrevDirection] = useState(direction);
  if (prevDirection !== direction) {
    setPrevDirection(direction);
    if (prevDirection !== undefined) {
      setActiveTab("overview");
    }
  }

  // Синхронизация activeTab при внутристраничной навигации на валидный ?tab=
  // (клик по ссылке ретрая дубликата из UploadJobRow меняет query string у уже
  // смонтированного ProjectPage — lazy-инициализатор activeTab выше срабатывает
  // только при первом рендере и такую навигацию не ловит, Codex P2). Невалидный
  // /исчезнувший tabParam вкладку не трогает — переключаем только на известное
  // значение. ВАЖНО: блок идёт ПОСЛЕ сброса вкладки на direction-change выше —
  // ссылка ретрая меняет direction (на all) и tab одновременно; оба setState
  // применяются в одном рендер-проходе, и последний вызов побеждает, поэтому
  // при обратном порядке сброс на «Обзор» затёр бы «Ошибки».
  const tabParam = searchParams.get("tab");
  const [prevTabParam, setPrevTabParam] = useState(tabParam);
  if (prevTabParam !== tabParam) {
    setPrevTabParam(tabParam);
    if (tabParam !== null && (PROJECT_TAB_VALUES as readonly string[]).includes(tabParam)) {
      setActiveTab(tabParam as ProjectTabValue);
    }
  }

  // ── остальные запросы — гейт до определения режима (§7.2) ──
  const queriesEnabled = direction !== undefined;
  const invoicesQ = useDashboardInvoices(projectId, scopedDirection, { enabled: queriesEnabled });
  const calculationsQ = useDashboardCalculations(
    projectId,
    debouncedPeriodStart || undefined,
    debouncedPeriodEnd || undefined,
    scopedDirection,
    { enabled: queriesEnabled },
  );
  const hasValidProjectId = projectId !== null;
  const referencePricesQ = useReferencePrices(
    hasValidProjectId ? projectId : undefined,
    { enabled: hasValidProjectId && queriesEnabled, direction: scopedDirection },
  );
  const materialClassesQ = useMaterialClasses();

  const docsQ = useDocuments(projectId ?? undefined);
  const errorDocCount = (docsQ.data ?? []).filter(
    (d) => d.status === "error" || d.has_issues,
  ).length;
  // document_id документов в обработке — мутации их СФ запрещены (409 бэка, §6).
  const busyDocIds = useMemo(
    () => new Set((docsQ.data ?? []).filter((d) => isDocBusy(d.status)).map((d) => d.id)),
    [docsQ.data],
  );

  // ── project suppliers ──
  const projectSuppliersQ = useProjectSuppliers(projectId, scopedDirection, { enabled: queriesEnabled });
  const supplierExclusionsQ = useSupplierExclusions(projectId);
  const toggleExclusion = useToggleSupplierExclusion(projectId);

  // ── mutations ──
  const createRefPrice = useCreateReferencePrice();
  const updateRefPrice = useUpdateReferencePrice();
  const deleteRefPrice = useDeleteReferencePrice();

  // ── units (reference data for the create-price dialog) ──
  const unitsQ = useUnits();
  const baseUnits = useMemo(
    () => (unitsQ.data ?? []).filter((u) => u.base_unit_id === null),
    [unitsQ.data],
  );
  const getDefaultUnitId = useDefaultUnitId();

  // ── derived ──
  const summaryData = summaryQ.data;
  const calculations = useMemo(() => calculationsQ.data ?? [], [calculationsQ.data]);
  const invoices = useMemo(() => invoicesQ.data ?? [], [invoicesQ.data]);
  const referencePrices = referencePricesQ.data ?? [];
  const materialClasses = materialClassesQ.data ?? [];

  const hasCalculations = calculations.length > 0;

  // Effective period: user's filter OR auto-detected range (cosmetic display only,
  // not sent to the API — API auto-detects when periodStart/periodEnd are empty).
  // Дефолт календаря — точные даты первой/последней СФ (из summary), как в подвале
  // KPI-ряда; calc-строки помесячные (конец месяца) — потому fallback, не источник.
  const dataStart = useMemo(
    () => summaryQ.data?.first_invoice_date
      ?? (calculations.length > 0
        ? calculations.reduce((m, c) => (c.period_start < m ? c.period_start : m), calculations[0].period_start)
        : ""),
    [calculations, summaryQ.data?.first_invoice_date],
  );
  const dataEnd = useMemo(
    () => summaryQ.data?.last_invoice_date
      ?? (calculations.length > 0
        ? calculations.reduce((m, c) => (c.period_end > m ? c.period_end : m), calculations[0].period_end)
        : ""),
    [calculations, summaryQ.data?.last_invoice_date],
  );
  const displayStart = periodStart || dataStart;
  const displayEnd   = periodEnd   || dataEnd;

  const filteredInvoices = useMemo(() => {
    if (!invoiceMonthFilter) return invoices;
    return invoices.filter((inv) => {
      const [yearPart, monthPart] = (inv.date ?? "").split("-");
      return (
        Number(yearPart) === invoiceMonthFilter.year &&
        Number(monthPart) === invoiceMonthFilter.month
      );
    });
  }, [invoices, invoiceMonthFilter]);

  // ── loading / not found ──
  if (projectsQ.isLoading) {
    return (
      <div className="container-page py-8 space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-[120px]" />
      </div>
    );
  }

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

  if (!project || projectId === null) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Объект не найден"
          action={
            <Link to="/projects">
              <Button variant="secondary" leftIcon={<ArrowLeft size={14} />}>
                К списку объектов
              </Button>
            </Link>
          }
        />
      </div>
    );
  }

  if (summaryQ.isLoading) {
    return <ProjectPageSkeleton />;
  }

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

  // ── handlers ──
  function handleAddReferencePrice() {
    if (!projectId || !rpClassId || !rpUnitId || !rpPrice || !rpStart || !rpEnd) return;
    createRefPrice.mutate(
      {
        project_id: projectId,
        material_class_id: Number(rpClassId),
        unit_id: Number(rpUnitId),
        price: Number(rpPrice),
        period_start: rpStart,
        period_end: rpEnd,
        source: rpSource || null,
      },
      {
        onSuccess: () => {
          setPriceDialogOpen(false);
          setRpClassId("");
          setRpUnitId("");
          setRpPrice("");
          setRpStart("");
          setRpEnd("");
          setRpSource("");
        },
      }
    );
  }

  function openEditDialog(rp: ReferencePrice) {
    setEditRpId(rp.id);
    setEditPrice(String(rp.price));
    setEditStart(rp.period_start);
    setEditEnd(rp.period_end);
    setEditSource(rp.source ?? "");
    setEditDialogOpen(true);
  }

  function handleEditReferencePrice() {
    const parsedPrice = parseFloat(editPrice);
    if (!editRpId || !Number.isFinite(parsedPrice) || parsedPrice < 0 || !editStart || !editEnd) return;
    updateRefPrice.mutate(
      {
        id: editRpId,
        input: {
          price: parsedPrice,
          period_start: editStart,
          period_end: editEnd,
          source: editSource || null,
        },
      },
      {
        onSuccess: () => {
          setEditDialogOpen(false);
          setEditRpId(null);
        },
      }
    );
  }

  function openDeleteDialog(id: number) {
    setDeleteRpId(id);
    setDeleteDialogOpen(true);
  }

  function handleDeleteReferencePrice() {
    if (!deleteRpId) return;
    deleteRefPrice.mutate(deleteRpId, {
      onSuccess: () => {
        setDeleteDialogOpen(false);
        setDeleteRpId(null);
      },
      onError: () => {
        setDeleteDialogOpen(false);
        setDeleteRpId(null);
      },
    });
  }

  return (
    <div className="container-page py-8">
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: "Объекты", to: "/projects" },
          { label: project.name },
        ]}
      />

      {/* Header */}
      <div className="mt-3">
        <PageHeader
          serif
          title={project.name}
          subtitle={
            project.contract_number
              ? `Договор № ${project.contract_number} · создан ${formatDate(project.created_at)}`
              : `Создан ${formatDate(project.created_at)}`
          }
          actions={
            <>
              <Button
                variant="secondary"
                leftIcon={<Download size={14} />}
                onClick={handleExport}
                disabled={isExporting || direction === undefined}
              >
                {isExporting ? "Формирую..." : "Экспорт"}
              </Button>
              <Button
                leftIcon={<Plus size={14} />}
                onClick={() => setUploadOpen(true)}
              >
                Добавить счёт
              </Button>
            </>
          }
        />
      </div>

      {/* Документы в фоновой обработке (S1-6): мутации по ним запрещены бэком (409),
          бейджи — видимая обратная связь, пока идёт парсинг/переразбор/deskew.
          Отдельный оборачиваемый ряд (не PageHeader.actions — тот flex без wrap,
          общий с кнопками Export/Добавить счёт, а multi-file upload — штатный
          сценарий с несколькими одновременно busy-документами). */}
      {(docsQ.data ?? []).some((d) => isDocBusy(d.status)) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {(docsQ.data ?? [])
            .filter((d) => isDocBusy(d.status))
            .map((d) => (
              <StatusPill
                key={d.id}
                tone="info"
                label={`Обрабатывается: ${d.filename}`}
                dot
              />
            ))}
        </div>
      )}

      {/* Upload sheet */}
      <UploadSheet
        projectId={projectId}
        open={uploadOpen}
        onOpenChange={setUploadOpen}
      />

      {/* Переключатель направлений (скрыт у пустого объекта) */}
      {!isLegacy && directions !== undefined && (
        <div className="mt-6">
          <DirectionSwitcher
            directions={directions}
            value={direction ?? "all"}
            onChange={changeDirection}
          />
        </div>
      )}

      {/* Контент: слот фиксированной высоты (h-8 TabsList + gap-2) всегда рендерится,
           в tabs-режиме содержит TabsList, в остальных — невидимый резерв (§3.1) */}
      <div className="mt-6">
        {direction === undefined ? (
          <>
            <TabBarSlot />
            <div className="mt-6 space-y-4">
              <Skeleton className="h-8 w-2/3" />
              <Skeleton className="h-[120px]" />
            </div>
          </>
        ) : isLegacy || scopedDirection ? (
          <Tabs value={activeTab} onValueChange={setActiveTab} data-testid="project-page-tabs">
          <TabsList variant="line" data-testid="project-page-tabs-list">
            <TabsTrigger value="overview" data-testid="project-tab-overview">Обзор</TabsTrigger>
            <TabsTrigger value="invoices" data-testid="project-tab-invoices">
              Счета{invoices.length > 0 ? ` · ${invoices.length}` : ""}
            </TabsTrigger>
            <TabsTrigger value="prices" data-testid="project-tab-prices">Базовые цены</TabsTrigger>
            <TabsTrigger value="corridors" data-testid="project-tab-corridors">Коридоры</TabsTrigger>
            <TabsTrigger value="suppliers" data-testid="project-tab-suppliers">
              Поставщики{(projectSuppliersQ.data?.length ?? 0) > 0 ? ` · ${projectSuppliersQ.data!.length}` : ""}
            </TabsTrigger>
            <TabsTrigger value="monthly" data-testid="project-tab-monthly">По месяцам</TabsTrigger>
            <TabsTrigger value="errors" data-testid="project-tab-errors">
              Ошибки
              {errorDocCount > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold leading-none text-white">
                  {errorDocCount}
                </span>
              )}
            </TabsTrigger>
          </TabsList>

          {/* ────────── TAB: Обзор ────────── */}
          <TabsContent value="overview" className="mt-6 space-y-6">
            {/* KPI row */}
            {summaryQ.data && (() => {
              const { first_invoice_date, last_invoice_date, full_compensation_amount } = summaryQ.data;

              // Срез направления (§3.3); undefined в legacy-режиме → старый KPI-блок
              const dir = directions?.find((d) => d.code === scopedDirection);
              const dev = deviationKpi(dir ? dir.overpayment : full_compensation_amount);

              return (
                <>
                  {dir ? (
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      <KpiCard
                        label="Оборот, ₽ с НДС"
                        value={formatMoney(dir.turnover)}
                        caption={
                          summaryQ.data.total_amount > 0
                            ? `${Math.round((dir.turnover / summaryQ.data.total_amount) * 100)}% оборота объекта`
                            : undefined
                        }
                      />
                      <KpiCard
                        label={dir.volume_unit ? `Объём, ${dir.volume_unit}` : "Объём"}
                        value={dir.volume !== null ? formatNumber(dir.volume) : "—"}
                        suffix={
                          dir.volume_excluded_count > 0 ? (
                            <span title="не вошли позиции в других единицах">
                              {`без ${dir.volume_excluded_count} позиц.`}
                            </span>
                          ) : undefined
                        }
                      />
                      <KpiCard
                        label="Счетов"
                        value={formatNumber(dir.invoice_count)}
                        suffix={dir.mixed_invoice_count > 0 ? `· ${dir.mixed_invoice_count} смешанных` : undefined}
                      />
                      <KpiCard
                        label={dev.label}
                        value={dev.value}
                        className={dev.className}
                        valueClassName={dev.valueClassName}
                      />
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      <KpiCard
                        label="Оборот, ₽ с НДС"
                        value={formatMoney(summaryQ.data.total_amount)}
                        breakdown={[
                          { label: "Материалы", value: formatMoney(summaryQ.data.material_amount) },
                          ...(summaryQ.data.delivery_amount > 0 ? [{ label: "Доставка", value: formatMoney(summaryQ.data.delivery_amount) }] : []),
                          ...(summaryQ.data.other_amount > 0 ? [{ label: "Прочее", value: formatMoney(summaryQ.data.other_amount) }] : []),
                        ]}
                      />
                      <KpiCard
                        label="Объём м³"
                        value={formatNumber(summaryQ.data.total_qty)}
                      />
                      <KpiCard
                        label="Счетов"
                        value={formatNumber(summaryQ.data.invoice_count)}
                        suffix={`· ${formatNumber(summaryQ.data.doc_count)} докум.`}
                      />
                      <KpiCard
                        label={dev.label}
                        value={dev.value}
                        className={dev.className}
                        valueClassName={dev.valueClassName}
                      />
                    </div>
                  )}
                  <p className="text-xs text-fg-tertiary -mt-2 px-1">
                    Первый счёт{" "}
                    <span className="text-fg-secondary font-medium">
                      {first_invoice_date ? formatDate(first_invoice_date) : "—"}
                    </span>
                    {" · "}
                    Последний счёт{" "}
                    <span className="text-fg-secondary font-medium">
                      {last_invoice_date ? formatDate(last_invoice_date) : "—"}
                    </span>
                    {(projectSuppliersQ.data?.length ?? 0) > 0 && (
                      <>
                        {" · "}
                        <span className="text-fg-secondary font-medium">{formatNumber(projectSuppliersQ.data!.length)}</span>
                        {` поставщик${pluralRu(projectSuppliersQ.data!.length)}`}
                      </>
                    )}
                  </p>
                </>
              );
            })()}

            {/* Exclusion banner */}
            {(supplierExclusionsQ.data?.size ?? 0) > 0 && (
              <div className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface px-4 py-2 text-sm text-fg-secondary -mt-2">
                <span>
                  {`Исключено ${supplierExclusionsQ.data!.size} поставщик${pluralRu(supplierExclusionsQ.data!.size)} из расчётов`}
                </span>
                <button
                  className="ml-auto text-xs underline hover:text-fg"
                  onClick={() => setActiveTab("suppliers")}
                >
                  Управление
                </button>
              </div>
            )}

            {/* Deviation chart (includes period filter in header) */}
            {summaryQ.data && (
              <DeviationChart
                calculations={calculations}
                periodFilterActive={true}
                onConfigurePrice={() => setActiveTab("prices")}
                periodStart={periodStart}
                periodEnd={periodEnd}
                dataStart={dataStart}
                dataEnd={dataEnd}
                displayStart={displayStart}
                displayEnd={displayEnd}
                onPeriodStartChange={setPeriodStart}
                onPeriodEndChange={setPeriodEnd}
                onPeriodReset={() => { setPeriodStart(""); setPeriodEnd(""); }}
              />
            )}

            {/* Calculations table */}
            {hasCalculations && (
              <Surface padding="none" className="overflow-x-auto">
                <Table className="min-w-max">
                  <TableHeader>
                    <TableRow className="text-xs text-fg-tertiary hover:bg-transparent">
                      <TableHead className="font-medium">Класс</TableHead>
                      <TableHead className="font-medium">Период</TableHead>
                      <TableHead className="font-medium text-right">
                        <div>Ср.цена</div>
                        <div className="text-[10px] font-normal text-fg-tertiary">с НДС</div>
                      </TableHead>
                      <TableHead className="font-medium text-right">
                        <div>Базовая цена</div>
                        <div className="text-[10px] font-normal text-fg-tertiary">с НДС</div>
                      </TableHead>
                      <TableHead className="font-medium text-right">Откл.%</TableHead>
                      <TableHead className="font-medium text-right">Откл.₽</TableHead>
                      <TableHead className="font-medium text-right">Компенсация</TableHead>
                      <TableHead className="font-medium text-right">Объём</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {[...calculations]
                      .sort((a, b) => {
                        const pCmp = a.period_start.localeCompare(b.period_start);
                        if (pCmp !== 0) return pCmp;
                        // Sort by numeric concrete strength (B7.5 < B10 < B15 < B30 ...)
                        const num = (name: string | null | undefined) => {
                          const m = (name ?? "").match(/[\d.]+/);
                          return m ? parseFloat(m[0]) : 0;
                        };
                        return num(a.material_class_name) - num(b.material_class_name);
                      })
                      .map((c) => (
                      <TableRow
                        key={`${c.material_class_name ?? ""}-${c.period_start}-${c.period_end}`}
                      >
                        <TableCell className="text-fg">
                          {c.material_class_name}
                        </TableCell>
                        <TableCell className="text-fg-secondary whitespace-nowrap font-mono text-sm">
                          {formatDate(c.period_start)} — {formatDate(c.period_end)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-fg">
                          {formatMoney(c.avg_price)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-fg-secondary">
                          {c.reference_price !== null
                            ? formatMoney(c.reference_price)
                            : "—"}
                        </TableCell>
                        <TableCell
                          className={
                            "text-right font-mono " +
                            (c.deviation_pct == null
                              ? "text-fg-secondary"
                              : c.deviation_pct > 0
                              ? "text-danger-text"
                              : "text-accent-text")
                          }
                        >
                          {formatPercent(c.deviation_pct, true)}
                        </TableCell>
                        <TableCell
                          className={
                            "text-right font-mono " +
                            (c.deviation_amount == null
                              ? "text-fg-secondary"
                              : c.deviation_amount > 0
                              ? "text-danger-text"
                              : "text-accent-text")
                          }
                        >
                          {c.deviation_amount !== null
                            ? formatMoney(c.deviation_amount)
                            : "—"}
                        </TableCell>
                        <TableCell
                          className={
                            "text-right font-mono " +
                            (c.compensation_amount == null
                              ? "text-fg-secondary"
                              : c.compensation_amount > 0
                              ? "text-danger-text"
                              : c.compensation_amount < 0
                              ? "text-accent-text"
                              : "text-fg-secondary")
                          }
                        >
                          {c.compensation_amount !== null
                            ? formatMoney(c.compensation_amount)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-fg-secondary">
                          {formatNumber(c.total_qty)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Surface>
            )}
          </TabsContent>

          {/* ────────── TAB: Счета ────────── */}
          <TabsContent value="invoices" className="mt-6">
            {invoicesQ.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : invoices.length === 0 ? (
              <EmptyState
                title="Нет счетов-фактур"
                description="Загрузите документы, чтобы они появились здесь."
                action={
                  <Button onClick={() => setUploadOpen(true)}>Загрузить</Button>
                }
              />
            ) : (
              <>
                {invoiceMonthFilter && (
                  <div className="mb-3 flex items-center gap-2">
                    <span className="text-xs text-fg-secondary">
                      Фильтр: {MONTH_NAMES_RU[invoiceMonthFilter.month - 1]} {invoiceMonthFilter.year}
                    </span>
                    <button
                      className="text-xs text-fg-tertiary hover:text-fg underline"
                      onClick={() => setInvoiceMonthFilter(null)}
                    >
                      Сбросить
                    </button>
                  </div>
                )}
                <div className="space-y-6">
                  {projectId && (
                    <InvoiceKpiBar invoices={filteredInvoices} projectId={projectId} />
                  )}
                  <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
                    <InvoiceTable
                      invoices={filteredInvoices}
                      busyDocIds={busyDocIds}
                    />
                  </div>
                </div>
              </>
            )}
          </TabsContent>

          {/* ────────── TAB: Базовые цены ────────── */}
          <TabsContent value="prices" className="mt-6 space-y-4">
            <div className="flex justify-end">
              <Button
                leftIcon={<Plus size={14} />}
                onClick={() => setPriceDialogOpen(true)}
              >
                Добавить
              </Button>
            </div>

            {referencePricesQ.isLoading ? (
              <Skeleton className="h-32" />
            ) : referencePrices.length === 0 ? (
              <EmptyState
                title="Нет базовых цен"
                description="Добавьте базовые цены для расчёта отклонений."
              />
            ) : (
              <Surface padding="none" className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-xs text-fg-tertiary">
                      <th className="px-4 py-2 font-medium">Класс</th>
                      <th className="px-4 py-2 font-medium text-right">
                        <div>Цена</div>
                        <div className="text-[10px] font-normal text-fg-tertiary">с НДС</div>
                      </th>
                      <th className="px-4 py-2 font-medium">Ед.</th>
                      <th className="px-4 py-2 font-medium">Период</th>
                      <th className="px-4 py-2 font-medium">Источник</th>
                      <th className="px-4 py-2 w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {referencePrices.map((rp) => (
                      <tr
                        key={rp.id}
                        className="border-b border-border-subtle last:border-0 hover:bg-surface-hover"
                      >
                        <td className="px-4 py-2 text-fg">
                          {rp.material_class_name ?? `#${rp.material_class_id}`}
                        </td>
                        <td className="px-4 py-2 text-right font-mono text-fg">
                          {formatMoney(rp.price)}
                        </td>
                        <td className="px-4 py-2 text-fg-secondary">{rp.unit_symbol ?? "—"}</td>
                        <td className="px-4 py-2 text-fg-secondary whitespace-nowrap">
                          {formatDate(rp.period_start)} — {formatDate(rp.period_end)}
                        </td>
                        <td className="px-4 py-2 text-fg-secondary">
                          {rp.source ?? "—"}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-1 justify-end">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => openEditDialog(rp)}
                              aria-label="Редактировать"
                              data-testid={`rp-edit-${rp.id}`}
                            >
                              <Pencil size={14} />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => openDeleteDialog(rp.id)}
                              aria-label="Удалить"
                              data-testid={`rp-delete-${rp.id}`}
                            >
                              <Trash2 size={14} />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Surface>
            )}

            {/* Add reference price dialog */}
            <Dialog open={priceDialogOpen} onOpenChange={setPriceDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Добавить базовую цену</DialogTitle>
                </DialogHeader>

                <div className="space-y-3 py-2">
                  {/* Material class selector */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">
                      Класс материала
                    </label>
                    <Select value={rpClassId} onValueChange={(v) => { const id = v ?? ""; setRpClassId(id); setRpUnitId(getDefaultUnitId(id)); }}>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="Выберите класс…" />
                      </SelectTrigger>
                      <SelectContent>
                        {materialClasses.map((mc) => (
                          <SelectItem key={mc.id} value={String(mc.id)}>
                            {mc.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Unit selector */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">
                      Единица измерения
                    </label>
                    <EntitySelect
                      items={baseUnits}
                      value={rpUnitId ? Number(rpUnitId) : null}
                      onChange={(v) => setRpUnitId(v ? String(v) : "")}
                      getLabel={(u) => `${u.name} (${u.symbol})`}
                      placeholder="Выберите единицу…"
                      disabled={unitsQ.isLoading}
                    />
                  </div>

                  {/* Price */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">
                      Цена (₽)
                    </label>
                    <Input
                      type="number"
                      min={0}
                      step={0.01}
                      placeholder="0.00"
                      value={rpPrice}
                      onChange={(e) => setRpPrice(e.target.value)}
                    />
                  </div>

                  {/* Period */}
                  <div className="grid grid-cols-2 gap-2">
                    <div className="flex flex-col gap-1">
                      <label className="text-xs text-fg-secondary">
                        Период с
                      </label>
                      <Input
                        type="date"
                        value={rpStart}
                        onChange={(e) => setRpStart(e.target.value)}
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <label className="text-xs text-fg-secondary">
                        Период по
                      </label>
                      <Input
                        type="date"
                        value={rpEnd}
                        onChange={(e) => setRpEnd(e.target.value)}
                      />
                    </div>
                  </div>

                  {/* Source */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">
                      Источник
                    </label>
                    <Input
                      placeholder="Необязательно"
                      value={rpSource}
                      onChange={(e) => setRpSource(e.target.value)}
                    />
                  </div>
                </div>

                <DialogFooter>
                  <Button
                    onClick={handleAddReferencePrice}
                    loading={createRefPrice.isPending}
                    disabled={
                      !rpClassId || !rpUnitId || !rpPrice || !rpStart || !rpEnd
                    }
                  >
                    Сохранить
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            {/* Edit reference price dialog */}
            <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Редактировать базовую цену</DialogTitle>
                </DialogHeader>
                <div className="space-y-3 py-2">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">Цена (₽)</label>
                    <Input
                      type="number"
                      min={0}
                      step={0.01}
                      placeholder="0.00"
                      value={editPrice}
                      onChange={(e) => setEditPrice(e.target.value)}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="flex flex-col gap-1">
                      <label className="text-xs text-fg-secondary">Период с</label>
                      <Input
                        type="date"
                        value={editStart}
                        onChange={(e) => setEditStart(e.target.value)}
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <label className="text-xs text-fg-secondary">Период по</label>
                      <Input
                        type="date"
                        value={editEnd}
                        onChange={(e) => setEditEnd(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-fg-secondary">Источник</label>
                    <Input
                      placeholder="Необязательно"
                      value={editSource}
                      onChange={(e) => setEditSource(e.target.value)}
                    />
                  </div>
                </div>
                <DialogFooter>
                  <Button
                    onClick={handleEditReferencePrice}
                    loading={updateRefPrice.isPending}
                    disabled={!Number.isFinite(parseFloat(editPrice)) || parseFloat(editPrice) < 0 || !editStart || !editEnd}
                  >
                    Сохранить
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            {/* Delete confirmation dialog */}
            <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Удалить базовую цену?</DialogTitle>
                </DialogHeader>
                <p className="text-sm text-fg-secondary py-2">
                  Это действие нельзя отменить.
                </p>
                <DialogFooter>
                  <Button variant="ghost" onClick={() => setDeleteDialogOpen(false)}>
                    Отмена
                  </Button>
                  <Button
                    data-testid="rp-delete-confirm"
                    onClick={handleDeleteReferencePrice}
                    loading={deleteRefPrice.isPending}
                  >
                    Удалить
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </TabsContent>
          {/* ────────── TAB: Коридоры ────────── */}
          <TabsContent value="corridors" className="mt-6">
            {projectId !== null && <CorridorsTab projectId={projectId} direction={scopedDirection} />}
          </TabsContent>
          {/* ────────── TAB: По месяцам ────────── */}
          <TabsContent value="monthly">
            <MonthlyTab
              projectId={projectId}
              projectName={project.name}
              direction={scopedDirection}
              onNavigateToMonth={(year, month) => {
                setInvoiceMonthFilter({ year, month });
                setActiveTab("invoices");
              }}
            />
          </TabsContent>

          <TabsContent value="suppliers" className="mt-6">
            {projectSuppliersQ.isLoading || supplierExclusionsQ.isLoading ? (
              <Skeleton className="h-32" />
            ) : (projectSuppliersQ.data ?? []).length === 0 ? (
              <EmptyState
                title="Нет поставщиков"
                description="Загрузите счета-фактуры, чтобы увидеть поставщиков."
                action={
                  <Button onClick={() => setUploadOpen(true)}>Загрузить</Button>
                }
              />
            ) : (
              <Surface padding="none" className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-xs text-fg-tertiary">
                      <th className="px-4 py-2 font-medium w-12 text-center" title="Снимите чекбокс, чтобы исключить поставщика из расчётов">В расчётах</th>
                      <th className="px-4 py-2 font-medium">Поставщик</th>
                      <th className="px-4 py-2 font-medium text-right">Счетов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(projectSuppliersQ.data ?? []).map((s) => {
                      const excluded = supplierExclusionsQ.data?.has(s.id) ?? false;
                      const isPopoverOpen = exclusionPopover?.supplierId === s.id;
                      const isThisRowPending =
                        toggleExclusion.isPending &&
                        toggleExclusion.variables?.supplierId === s.id;
                      // Disable all checkboxes while any toggle is in flight to prevent race conditions
                      const isAnyPending = toggleExclusion.isPending;
                      return (
                        <tr
                          key={s.id}
                          className="border-b border-border-subtle last:border-0 hover:bg-surface-hover"
                        >
                          <td className="px-4 py-2 text-center">
                            {isThisRowPending ? (
                              <Loader2 className="mx-auto size-4 animate-spin text-fg-tertiary" />
                            ) : (
                              <Checkbox
                                checked={!excluded}
                                disabled={isAnyPending}
                                aria-label={excluded ? `Включить ${s.name} в расчёты` : `Исключить ${s.name} из расчётов`}
                                onCheckedChange={(checked: boolean) => {
                                  if (checked) {
                                    toggleExclusion.mutate({ supplierId: s.id, excluded: false });
                                  } else {
                                    setExclusionPopover({ supplierId: s.id, reason: "" });
                                  }
                                }}
                              />
                            )}
                          </td>
                          <td className="px-4 py-2 text-fg">
                            <div>
                              <span className={excluded ? "text-fg-tertiary line-through" : ""}>
                                {s.name}
                              </span>
                              {s.inn && (
                                <span className="ml-2 text-xs text-fg-tertiary">
                                  ИНН {s.inn}
                                </span>
                              )}
                            </div>
                            {isPopoverOpen && (
                              <div className="mt-2 p-3 rounded-lg border border-border-subtle bg-surface shadow-md space-y-2">
                                <label
                                  htmlFor={`exclusion-reason-${s.id}`}
                                  className="text-xs text-fg-secondary"
                                >
                                  Причина исключения (необязательно)
                                </label>
                                <input
                                  id={`exclusion-reason-${s.id}`}
                                  autoFocus
                                  className="w-full rounded border border-border-subtle px-2 py-1 text-sm bg-bg text-fg focus:outline-none focus:ring-1 focus:ring-accent"
                                  placeholder="Аварийная закупка, нерепрезентативная цена..."
                                  value={exclusionPopover.reason}
                                  onChange={(e) =>
                                    setExclusionPopover((prev) =>
                                      prev ? { ...prev, reason: e.target.value } : null
                                    )
                                  }
                                  onKeyDown={(e) => {
                                    if (e.key === "Escape") setExclusionPopover(null);
                                    if (e.key === "Enter") {
                                      toggleExclusion.mutate({
                                        supplierId: s.id,
                                        excluded: true,
                                        reason: exclusionPopover.reason || undefined,
                                      });
                                      setExclusionPopover(null);
                                    }
                                  }}
                                />
                                <div className="flex gap-2 justify-end">
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setExclusionPopover(null)}
                                  >
                                    Отмена
                                  </Button>
                                  <Button
                                    size="sm"
                                    onClick={() => {
                                      toggleExclusion.mutate({
                                        supplierId: s.id,
                                        excluded: true,
                                        reason: exclusionPopover.reason || undefined,
                                      });
                                      setExclusionPopover(null);
                                    }}
                                  >
                                    Исключить
                                  </Button>
                                </div>
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-fg-secondary">
                            {s.invoice_count}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Surface>
            )}
          </TabsContent>

          {/* ────────── TAB: Ошибки ────────── */}
          <TabsContent value="errors" className="mt-6">
            {projectId && (
              docsQ.isLoading
                ? <Skeleton className="h-32" />
                : <ErrorDocsTab docs={docsQ.data ?? []} />
            )}
          </TabsContent>
          </Tabs>
        ) : view === "errors" ? (
          /* ────────── «Все» → view=errors: ошибки объекта (§3.2 п.2) ────────── */
          <ErrorsView
            isLoading={docsQ.isLoading}
            docs={docsQ.data ?? []}
            onBack={() =>
              setSearchParams((p) => {
                const n = new URLSearchParams(p);
                n.delete("view");
                return n;
              })
            }
          />
        ) : summaryData ? (
          /* ────────── Сводка «Все направления» (§3.2) ────────── */
          <AllDirectionsSummaryView
            summaryData={summaryData}
            errorDocCount={errorDocCount}
            calculations={calculations}
            invoices={invoices}
            invoicesLoading={invoicesQ.isLoading}
            busyDocIds={busyDocIds}
            onOpenErrors={() =>
              setSearchParams((p) => {
                const n = new URLSearchParams(p);
                n.set("view", "errors");
                return n;
              })
            }
            changeDirection={changeDirection}
            periodStart={periodStart}
            periodEnd={periodEnd}
            dataStart={dataStart}
            dataEnd={dataEnd}
            displayStart={displayStart}
            displayEnd={displayEnd}
            onPeriodStartChange={setPeriodStart}
            onPeriodEndChange={setPeriodEnd}
            onPeriodReset={() => { setPeriodStart(""); setPeriodEnd(""); }}
          />
        ) : null}
      </div>
    </div>
  );
}

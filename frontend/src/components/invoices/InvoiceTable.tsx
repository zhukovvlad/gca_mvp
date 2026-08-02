import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown, ArrowUp, ChevronsUpDown, EyeOff,
  FileEdit, LayoutList, PlusCircle, Search, Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Separator } from "@/components/ui/separator";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui-domain/Button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { formatDate } from "@/lib/format";
import { useSettings, useDeleteInvoice, useDeleteInvoicesBulk } from "@/services/queries";
import { DEFAULT_CONFIDENCE_THRESHOLD } from "@/lib/constants";
import { getStage, type Stage } from "./invoiceStage";
import type { DashboardInvoiceRow } from "@/types/invoice";
import type { ID } from "@/types/common";

interface InvoiceTableProps {
  invoices: DashboardInvoiceRow[];
  /**
   * document_id документов, которые сейчас обрабатываются (pending|processing,
   * см. `isDocBusy`) — их СФ нельзя удалять (зеркалит серверный 409 из
   * `_reject_if_busy`, спека §6). По умолчанию пустое множество — поведение
   * не меняется для вызовов без этого пропа.
   */
  busyDocIds?: ReadonlySet<ID>;
}

type SortColumn = "date" | "supplier" | "total" | "number";
type SortDir = "asc" | "desc";
type PageSize = 10 | 20 | 50;
type ColKey = "number" | "date" | "supplier" | "items" | "total" | "status";

const PAGE_SIZE_OPTIONS: PageSize[] = [10, 20, 50];
const REVIEW_CONFIDENCE_THRESHOLD = DEFAULT_CONFIDENCE_THRESHOLD;

const COL_LABELS: Record<ColKey, string> = {
  number:   "Номер",
  date:     "Дата",
  supplier: "Поставщик",
  items:    "Позиции",
  total:    "Сумма",
  status:   "Статус",
};

const STAGE_CONFIG: Record<Stage, { tone: "success" | "danger" | "neutral"; label: string }> = {
  confirmed: { tone: "success", label: "Подтверждён" },
  review:    { tone: "danger",  label: "Разобрать" },
  pending:   { tone: "neutral", label: "Ожидает" },
};

const STAGE_FILTER_OPTIONS: { stage: Stage; dotClass: string }[] = [
  { stage: "confirmed", dotClass: "bg-accent" },
  { stage: "pending",   dotClass: "bg-neutral-dot" },
  { stage: "review",    dotClass: "bg-danger" },
];

// Стабильная ссылка на дефолт — чтобы вызовы без busyDocIds не пересчитывали
// зависящие от него useMemo на каждый рендер.
const EMPTY_BUSY_DOC_IDS: ReadonlySet<ID> = new Set();

function invoiceTotal(inv: DashboardInvoiceRow): number {
  return inv.items.reduce(
    (s, it) => s + it.amount + (it.vat_amount ?? it.amount * ((inv.vat_rate ?? 20) / 100)),
    0,
  );
}

// ── SortHead / PlainHead — extracted so ESLint doesn't flag them as
//    "components created during render". They receive callbacks as props
//    instead of closing over the parent's state directly.

interface SortHeadProps {
  col: ColKey;
  sortKey: SortColumn;
  activeSortCol: SortColumn;
  hiddenCols: Set<ColKey>;
  onSort: (key: SortColumn, dir: SortDir) => void;
  onHide: (col: ColKey) => void;
  className?: string;
  children: React.ReactNode;
}

function SortHead({ col, sortKey, activeSortCol, hiddenCols, onSort, onHide, className, children }: SortHeadProps) {
  if (hiddenCols.has(col)) return null;
  const isActive = activeSortCol === sortKey;
  return (
    <TableHead className={className}>
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          className={`inline-flex items-center gap-1.5 rounded px-1 -ml-1 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 select-none${isActive ? " text-fg" : ""}`}
        >
          {children}
          <ChevronsUpDown size={14} className={isActive ? "text-accent" : "text-fg-muted"} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={4} className="min-w-36">
          <DropdownMenuItem className="flex items-center gap-2" onClick={() => onSort(sortKey, "asc")}>
            <ArrowUp size={13} className="text-fg-tertiary" />
            По возрастанию
          </DropdownMenuItem>
          <DropdownMenuItem className="flex items-center gap-2" onClick={() => onSort(sortKey, "desc")}>
            <ArrowDown size={13} className="text-fg-tertiary" />
            По убыванию
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className="flex items-center gap-2 text-fg-secondary" onClick={() => onHide(col)}>
            <EyeOff size={13} className="text-fg-tertiary" />
            Скрыть
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </TableHead>
  );
}

function PlainHead({ col, hiddenCols, children, className }: { col: ColKey; hiddenCols: Set<ColKey>; children?: React.ReactNode; className?: string }) {
  if (hiddenCols.has(col)) return null;
  return <TableHead className={className}>{children}</TableHead>;
}

export function InvoiceTable({ invoices, busyDocIds = EMPTY_BUSY_DOC_IDS }: InvoiceTableProps) {
  const settingsQ = useSettings();
  const threshold = settingsQ.data?.confidence_threshold ?? REVIEW_CONFIDENCE_THRESHOLD;
  const deleteInvoice = useDeleteInvoice();
  const deleteBulk = useDeleteInvoicesBulk();

  const [sortCol, setSortCol]       = useState<SortColumn>("date");
  const [sortDir, setSortDir]       = useState<SortDir>("desc");
  const [hiddenCols, setHiddenCols] = useState<Set<ColKey>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedStages, setSelectedStages] = useState<Set<Stage>>(new Set());
  const [pendingDelete, setPendingDelete]   = useState<DashboardInvoiceRow | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [page, setPage]         = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(20);
  const [selectedIds, setSelectedIds] = useState<Set<ID>>(new Set());

  // ── derived ───────────────────────────────────────────────────────────────

  const filtered = useMemo(() => {
    let result = invoices;
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      result = result.filter(
        (inv) =>
          inv.number?.toLowerCase().includes(q) ||
          inv.supplier_name?.toLowerCase().includes(q),
      );
    }
    if (selectedStages.size > 0) {
      result = result.filter((inv) => selectedStages.has(getStage(inv, threshold)));
    }
    return result;
  }, [invoices, searchQuery, selectedStages, threshold]);

  // Trim selectedIds to rows that are still visible after filtering.
  // Computed inline — no useEffect, no extra render.
  const allowedIds = useMemo(() => new Set(filtered.map((inv) => inv.id)), [filtered]);
  const effectiveSelectedIds = useMemo(() => {
    const next = new Set<ID>();
    for (const id of selectedIds) if (allowedIds.has(id)) next.add(id);
    return next.size === selectedIds.size ? selectedIds : next;
  }, [selectedIds, allowedIds]);

  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    // Pre-compute totals once so the comparator doesn't re-reduce on every comparison
    const totals = new Map(filtered.map((inv) => [inv.id, invoiceTotal(inv)]));
    return [...filtered].sort((a, b) => {
      switch (sortCol) {
        case "date":     return dir * a.date.localeCompare(b.date);
        case "supplier": return dir * (a.supplier_name ?? "").localeCompare(b.supplier_name ?? "", "ru");
        case "total":    return dir * ((totals.get(a.id) ?? 0) - (totals.get(b.id) ?? 0));
        case "number":   return dir * (a.number ?? "").localeCompare(b.number ?? "", "ru", { numeric: true });
        default:         return 0;
      }
    });
  }, [filtered, sortCol, sortDir]);

  const totalPages   = Math.max(1, Math.ceil(sorted.length / pageSize));
  const clampedPage  = Math.min(page, totalPages);
  const paged        = sorted.slice((clampedPage - 1) * pageSize, clampedPage * pageSize);
  const fromIdx      = sorted.length === 0 ? 0 : (clampedPage - 1) * pageSize + 1;
  const toIdx        = Math.min(clampedPage * pageSize, sorted.length);
  const allSelected  = sorted.length > 0 && sorted.every((inv) => effectiveSelectedIds.has(inv.id));
  const someSelected  = effectiveSelectedIds.size > 0;

  // document_id по id СФ — чтобы найти busy-документы среди выбранных строк.
  const docIdByInvoiceId = useMemo(
    () => new Map(invoices.map((inv) => [inv.id, inv.document_id])),
    [invoices],
  );
  // Bulk delete запрещён, если ЛЮБАЯ выбранная строка принадлежит busy-документу
  // (зеркалит атомарность серверного bulk-delete: 409 при любом busy, §6).
  const anySelectedBusy = useMemo(
    () => [...effectiveSelectedIds].some((id) => {
      const docId = docIdByInvoiceId.get(id);
      return docId !== undefined && busyDocIds.has(docId);
    }),
    [effectiveSelectedIds, docIdByInvoiceId, busyDocIds],
  );

  // ── helpers ───────────────────────────────────────────────────────────────

  function setSort(col: SortColumn, dir: SortDir) {
    setSortCol(col); setSortDir(dir); setPage(1);
  }

  function toggleHide(col: ColKey) {
    setHiddenCols((prev) => {
      const next = new Set(prev);
      if (next.has(col)) { next.delete(col); } else { next.add(col); }
      return next;
    });
  }

  function toggleStage(stage: Stage) {
    setSelectedStages((prev) => {
      const next = new Set(prev);
      if (next.has(stage)) { next.delete(stage); } else { next.add(stage); }
      return next;
    });
    setPage(1);
  }

  function toggleSelectAll() {
    setSelectedIds(allSelected ? new Set() : new Set(sorted.map((inv) => inv.id)));
  }

  function toggleRow(id: ID) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      // Clamp to currently visible rows so stale ids don't linger after filtering
      for (const k of next) if (!allowedIds.has(k)) next.delete(k);
      return next;
    });
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <>
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <InputGroup className="flex-1 max-w-xs">
          <InputGroupInput
            placeholder="Номер, поставщик…"
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
          />
          <InputGroupAddon align="inline-start">
            <Search size={13} />
          </InputGroupAddon>
        </InputGroup>

        {/* Status facet filter — DropdownMenu (тот же примитив, что «Вид»: корректный
            фокус-менеджмент Base UI Menu, без скролл-джампа портального Popover) */}
        <DropdownMenu>
          <DropdownMenuTrigger
            type="button"
            className="inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-sm text-fg-secondary hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
          >
            <PlusCircle size={13} />
            Статус
            {selectedStages.size > 0 && (
              <>
                <Separator orientation="vertical" className="mx-0.5 h-4" />
                {[...selectedStages].map((stage) => (
                  <Badge key={stage} variant="secondary" className="rounded px-1.5 py-0 text-xs font-normal">
                    {STAGE_CONFIG[stage].label}
                  </Badge>
                ))}
              </>
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" sideOffset={4} className="min-w-44">
            {STAGE_FILTER_OPTIONS.map(({ stage, dotClass }) => (
              <DropdownMenuCheckboxItem
                key={stage}
                checked={selectedStages.has(stage)}
                onCheckedChange={() => toggleStage(stage)}
              >
                <span className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} />
                {STAGE_CONFIG[stage].label}
              </DropdownMenuCheckboxItem>
            ))}
            {selectedStages.size > 0 && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="justify-center text-xs text-fg-tertiary"
                  onClick={() => { setSelectedStages(new Set()); setPage(1); }}
                >
                  Сбросить фильтр
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="ml-auto">
          <DropdownMenu>
            <DropdownMenuTrigger
              type="button"
              className="inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-sm text-fg-secondary hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
            >
              <LayoutList size={14} />
              Вид
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" sideOffset={4} className="min-w-40">
              {(Object.keys(COL_LABELS) as ColKey[]).map((col) => (
                <DropdownMenuCheckboxItem
                  key={col}
                  checked={!hiddenCols.has(col)}
                  onCheckedChange={() => toggleHide(col)}
                >
                  {COL_LABELS[col]}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Bulk action bar */}
      {someSelected && (
        <div className="flex items-center gap-3 border-b border-border-subtle bg-surface-hover px-3 py-2 text-sm">
          <span className="text-fg-secondary">
            Выбрано: <span className="font-medium text-fg">{effectiveSelectedIds.size}</span>
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="text-danger hover:text-danger"
            leftIcon={<Trash2 size={14} />}
            onClick={() => setBulkDeleteOpen(true)}
            disabled={anySelectedBusy}
            title={anySelectedBusy ? "Среди выбранных есть документ в обработке" : undefined}
          >
            Удалить выбранные
          </Button>
          <button
            className="ml-auto text-xs text-fg-tertiary hover:text-fg"
            onClick={() => setSelectedIds(new Set())}
          >
            Снять выделение
          </button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <Table className="min-w-[860px] table-fixed">
          <colgroup>
            <col className="w-8" />
            {!hiddenCols.has("number")   && <col className="w-[5rem]" />}
            {!hiddenCols.has("date")     && <col className="w-[6.5rem]" />}
            {!hiddenCols.has("supplier") && <col className="w-[13rem]" />}
            {!hiddenCols.has("items")    && <col />}
            {!hiddenCols.has("total")    && <col className="w-[9.5rem]" />}
            {!hiddenCols.has("status")   && <col className="w-[7.5rem]" />}
            <col className="w-[7rem]" />
          </colgroup>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-3">
                <Checkbox
                  checked={allSelected}
                  onCheckedChange={toggleSelectAll}
                  aria-label="Выбрать все"
                />
              </TableHead>
              <SortHead col="number"   sortKey="number"   activeSortCol={sortCol} hiddenCols={hiddenCols} onSort={setSort} onHide={toggleHide}>Номер</SortHead>
              <SortHead col="date"     sortKey="date"     activeSortCol={sortCol} hiddenCols={hiddenCols} onSort={setSort} onHide={toggleHide}>Дата</SortHead>
              <SortHead col="supplier" sortKey="supplier" activeSortCol={sortCol} hiddenCols={hiddenCols} onSort={setSort} onHide={toggleHide}>Поставщик</SortHead>
              <PlainHead col="items" hiddenCols={hiddenCols}>Позиции</PlainHead>
              <SortHead col="total" sortKey="total" activeSortCol={sortCol} hiddenCols={hiddenCols} onSort={setSort} onHide={toggleHide} className="text-right">
                <div className="text-right leading-tight">
                  <div>Сумма</div>
                  <div className="text-[10px] font-normal text-fg-tertiary">с НДС</div>
                </div>
              </SortHead>
              <PlainHead col="status" hiddenCols={hiddenCols}>Статус</PlainHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((inv) => {
              const total = invoiceTotal(inv);
              const stage = getStage(inv, threshold);
              const { tone, label } = STAGE_CONFIG[stage];
              const confidencePct =
                inv.ai_confidence != null
                  ? `ИИ: ${Math.round(inv.ai_confidence * 100)}%`
                  : null;
              const verifiedPart =
                stage === "confirmed" && inv.verified_at
                  ? `Проверен ${formatDate(inv.verified_at)}`
                  : null;
              const tooltip = [label, confidencePct, verifiedPart].filter(Boolean).join(" · ");
              const isSelected = effectiveSelectedIds.has(inv.id);
              return (
                <TableRow
                  key={inv.id}
                  className={`hover:bg-surface-hover ${isSelected ? "bg-surface-hover" : ""}`}
                >
                  <TableCell className="pl-3">
                    <Checkbox
                      checked={isSelected}
                      onCheckedChange={() => toggleRow(inv.id)}
                      aria-label={`Выбрать СФ ${inv.number || inv.id}`}
                    />
                  </TableCell>
                  {!hiddenCols.has("number") && (
                    <TableCell className={`font-medium overflow-hidden border-l-2 ${stage === "review" ? "border-danger" : "border-transparent"}`}>
                      <span className="block whitespace-normal break-all" title={inv.number || "—"}>
                        {inv.number || "—"}
                        {inv.directions.length === 0 && (
                          <span
                            className="ml-1.5 rounded bg-surface-sunken px-1 py-0.5 text-[10px] font-normal text-fg-tertiary align-middle"
                            title="Без направления (не бетон/арматура)"
                          >
                            прочее
                          </span>
                        )}
                      </span>
                    </TableCell>
                  )}
                  {!hiddenCols.has("date") && (
                    <TableCell className="text-fg-secondary tabular-nums">
                      {formatDate(inv.date)}
                    </TableCell>
                  )}
                  {!hiddenCols.has("supplier") && (
                    <TableCell className="truncate" title={inv.supplier_name ?? ""}>
                      {inv.supplier_name || "—"}
                    </TableCell>
                  )}
                  {!hiddenCols.has("items") && (
                    <TableCell>
                      <div className="space-y-0.5">
                        {inv.items.slice(0, 3).map((it, i) => (
                          <div key={i} className="truncate text-xs text-fg-secondary" title={it.raw_name ?? ""}>
                            <span className="text-fg-tertiary">{it.material_class || it.item_type}</span>
                            {" · "}
                            {it.raw_name}
                          </div>
                        ))}
                        {inv.items.length > 3 && (
                          <div className="text-xs text-fg-tertiary">и ещё {inv.items.length - 3}</div>
                        )}
                      </div>
                    </TableCell>
                  )}
                  {!hiddenCols.has("total") && (
                    <TableCell className="text-right whitespace-nowrap">
                      <MoneyCell value={total} />
                    </TableCell>
                  )}
                  {!hiddenCols.has("status") && (
                    <TableCell>
                      <span title={tooltip} aria-label={tooltip} tabIndex={0}>
                        <StatusPill tone={tone} label={label} dot />
                      </span>
                    </TableCell>
                  )}
                  <TableCell className="pr-3">
                    <div className="flex items-center justify-end gap-2">
                      <Link to={`/documents/${inv.document_id}`}>
                        <Button variant="ghost" size="sm" aria-label="Редактировать">
                          <FileEdit size={14} />
                        </Button>
                      </Link>
                      {stage === "confirmed" ? (
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <span className="cursor-default">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  aria-label="Удалить"
                                  className="text-fg-tertiary"
                                  disabled
                                >
                                  <Trash2 size={14} />
                                </Button>
                              </span>
                            }
                          />
                          <TooltipContent>
                            Снимите подтверждение перед удалением
                          </TooltipContent>
                        </Tooltip>
                      ) : busyDocIds.has(inv.document_id) ? (
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <span className="cursor-default">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  aria-label="Удалить"
                                  className="text-fg-tertiary"
                                  disabled
                                >
                                  <Trash2 size={14} />
                                </Button>
                              </span>
                            }
                          />
                          <TooltipContent>
                            Документ обрабатывается — дождитесь завершения
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label="Удалить"
                          className="text-fg-tertiary hover:text-danger"
                          onClick={() => setPendingDelete(inv)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
            {sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-sm text-fg-tertiary">
                  Ничего не найдено
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination footer */}
      {sorted.length > 0 && (
        <div className="flex items-center justify-between border-t border-border-subtle px-3 py-2 text-xs text-fg-secondary">
          <span className="tabular-nums">{fromIdx}–{toIdx} из {sorted.length}</span>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-fg-tertiary">Показывать по</span>
              <Select
                value={String(pageSize)}
                onValueChange={(v) => { setPageSize(Number(v) as PageSize); setPage(1); }}
              >
                <SelectTrigger className="h-7 w-16 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Pagination className="w-auto mx-0">
              <PaginationContent className="gap-0">
                <PaginationItem>
                  <PaginationPrevious
                    href="#"
                    text="Назад"
                    onClick={(e) => { e.preventDefault(); setPage(Math.max(1, clampedPage - 1)); }}
                    className={clampedPage === 1 ? "pointer-events-none opacity-40" : ""}
                    aria-disabled={clampedPage === 1}
                  />
                </PaginationItem>
                <PaginationItem>
                  <span className="px-3 tabular-nums text-xs">{clampedPage} / {totalPages}</span>
                </PaginationItem>
                <PaginationItem>
                  <PaginationNext
                    href="#"
                    text="Вперёд"
                    onClick={(e) => { e.preventDefault(); setPage(Math.min(totalPages, clampedPage + 1)); }}
                    className={clampedPage === totalPages ? "pointer-events-none opacity-40" : ""}
                    aria-disabled={clampedPage === totalPages}
                  />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          </div>
        </div>
      )}

      {/* Single delete */}
      <AlertDialog open={!!pendingDelete} onOpenChange={(open) => { if (!open) setPendingDelete(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить СФ «{pendingDelete?.number || "—"}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Счёт-фактура и все её позиции будут удалены без возможности восстановления.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteInvoice.isPending}
              onClick={() => {
                if (pendingDelete) {
                  deleteInvoice.mutate(pendingDelete.id, {
                    onSuccess: () => setPendingDelete(null),
                  });
                }
              }}
            >
              {deleteInvoice.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Bulk delete */}
      <AlertDialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить {effectiveSelectedIds.size} СФ?</AlertDialogTitle>
            <AlertDialogDescription>
              Выбранные счета-фактуры и все их позиции будут удалены без возможности
              восстановления. Подтверждённые СФ будут пропущены.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteBulk.isPending || anySelectedBusy}
              onClick={() => {
                deleteBulk.mutate([...effectiveSelectedIds], {
                  onSuccess: () => {
                    setBulkDeleteOpen(false);
                    setSelectedIds(new Set());
                  },
                });
              }}
            >
              {deleteBulk.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

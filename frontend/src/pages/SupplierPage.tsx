import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { Download, Lightbulb, Pencil } from "lucide-react";
import axios from "axios";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { Button } from "@/components/ui-domain/Button";
import { Surface } from "@/components/ui-domain/Surface";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
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
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import {
  useSupplierDetail,
  useSupplierProjects,
  useSupplierInvoices,
  useUpdateSupplier,
  useMergeSupplier,
} from "@/services/queries";
import { formatMoney, formatNumber, formatDate, pluralRu } from "@/lib/format";
import type { SupplierProjectRow } from "@/types/supplier";
import type { ID } from "@/types/common";

// ─── Edit dialog ─────────────────────────────────────────────────

interface InnConflict {
  id: ID;
  name: string;
}

interface EditSupplierDialogProps {
  open: boolean;
  onClose: () => void;
  supplierId: ID;
  initialName: string;
  initialInn: string | null;
}

function EditSupplierDialog({
  open,
  onClose,
  supplierId,
  initialName,
  initialInn,
}: EditSupplierDialogProps) {
  const navigate = useNavigate();
  const [name, setName] = useState(initialName);
  const [inn, setInn] = useState(initialInn ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);

  // second-step state: merge confirmation
  const [conflict, setConflict] = useState<InnConflict | null>(null);

  const updateMut = useUpdateSupplier();
  const mergeMut = useMergeSupplier();

  const isBusy = updateMut.isPending || mergeMut.isPending;

  function handleOpenChange(v: boolean) {
    if (!v) {
      if (isBusy) return; // block close while mutation is in-flight
      setFieldError(null);
      setConflict(null);
      onClose();
    }
  }

  async function handleSave() {
    const trimmedName = name.trim();
    const trimmedInn = inn.trim() || null;
    if (!trimmedName) {
      setFieldError("Название не может быть пустым");
      return;
    }
    setFieldError(null);

    try {
      await updateMut.mutateAsync({ id: supplierId, input: { name: trimmedName, inn: trimmedInn } });
      onClose();
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        const detail = err.response.data?.detail;
        if (detail && typeof detail === "object" && detail.code === "inn_conflict") {
          // INN belongs to another supplier — offer merge
          setConflict({ id: detail.existing.id, name: detail.existing.name });
          return;
        }
        setFieldError(
          typeof detail === "string"
            ? detail
            : "Поставщик с такими данными уже существует",
        );
        return;
      }
      setFieldError("Не удалось сохранить изменения. Попробуйте ещё раз.");
    }
  }

  async function handleMerge() {
    if (!conflict) return;
    try {
      // current supplier (supplierId) is the source; conflict.id is the target (has the INN)
      await mergeMut.mutateAsync({ targetId: conflict.id, sourceId: supplierId });
      onClose();
      navigate(`/suppliers/${conflict.id}`);
    } catch {
      setFieldError("Не удалось объединить поставщиков. Попробуйте ещё раз.");
      setConflict(null);
    }
  }

  // ── merge confirmation screen ──────────────────────────────────
  if (conflict) {
    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Совместить поставщиков?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-fg-secondary">
            ИНН <span className="font-mono text-fg">{inn.trim()}</span> уже принадлежит поставщику{" "}
            <span className="font-medium text-fg">«{conflict.name}»</span>.
          </p>
          <p className="text-sm text-fg-secondary">
            Все счета текущего поставщика будут перенесены в{" "}
            <span className="font-medium text-fg">«{conflict.name}»</span>, а текущая карточка
            удалена. Это действие нельзя отменить.
          </p>
          <DialogFooter className="mt-2">
            <Button variant="secondary" onClick={() => setConflict(null)} disabled={isBusy}>
              Назад
            </Button>
            <Button variant="danger" loading={isBusy} onClick={handleMerge}>
              Совместить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  // ── edit form ──────────────────────────────────────────────────
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Редактировать поставщика</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-fg-secondary">Название</label>
            <Input
              value={name}
              onChange={(e) => { setName(e.target.value); setFieldError(null); }}
              placeholder="ООО «Поставщик»"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-fg-secondary">ИНН</label>
            <Input
              value={inn}
              onChange={(e) => { setInn(e.target.value); setFieldError(null); }}
              placeholder="7700000000"
              className="font-mono"
            />
          </div>
          {fieldError && (
            <p className="text-xs text-danger">{fieldError}</p>
          )}
        </div>
        <DialogFooter className="mt-2">
          <Button variant="secondary" onClick={onClose} disabled={isBusy}>
            Отмена
          </Button>
          <Button loading={isBusy} onClick={handleSave}>
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Helpers ────────────────────────────────────────────────────

function formatMillions(v: number): string {
  if (Math.abs(v) >= 1_000_000)
    return `${(v / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн ₽`;
  if (Math.abs(v) >= 1_000)
    return `${(v / 1_000).toLocaleString("ru-RU", { maximumFractionDigits: 0 })} тыс ₽`;
  return formatMoney(v);
}

function DeviationBadge({ pct, amount }: { pct: number; amount: number }) {
  const positive = pct > 0.5;
  const negative = pct < -0.5;
  const colorClass = positive
    ? "text-danger"
    : negative
      ? "text-accent"
      : "text-warning";
  const sign = pct > 0 ? "+" : "";
  return (
    <div className="text-right">
      <span className={`text-sm font-semibold ${colorClass}`}>
        {sign}{pct.toFixed(1)}%
      </span>
      <div className="mt-0.5 text-[10px] text-fg-tertiary tabular-nums">
        {pct > 0 ? "+" : ""}{formatMoney(amount)}
      </div>
    </div>
  );
}

// ─── Hint block ─────────────────────────────────────────────────

function OutlierHint({ rows }: { rows: SupplierProjectRow[] }) {
  const withDev = rows.filter((r) => r.deviation_pct !== null);
  if (withDev.length < 2) return null;

  const sorted = [...withDev].sort((a, b) => (b.deviation_pct ?? 0) - (a.deviation_pct ?? 0));
  const highest = sorted[0];
  const rest = sorted.slice(1);
  const restAvg = rest.reduce((s, r) => s + (r.deviation_pct ?? 0), 0) / rest.length;

  if ((highest.deviation_pct ?? 0) - restAvg < 1.5) return null;

  return (
    <div className="mt-3 flex gap-2.5 rounded-lg border border-border-subtle bg-surface-sunken px-4 py-3">
      <Lightbulb size={15} className="mt-0.5 shrink-0 text-accent" />
      <p className="text-xs leading-relaxed text-fg-secondary">
        На объекте «{highest.project_name}» наценка поставщика (
        {(highest.deviation_pct ?? 0) > 0 ? "+" : ""}
        {(highest.deviation_pct ?? 0).toFixed(1)}%) заметно выше, чем на других объектах
        (в среднем {restAvg > 0 ? "+" : ""}
        {restAvg.toFixed(1)}%). Стоит разобраться, почему по этому договору условия хуже.
      </p>
    </div>
  );
}

// ─── Tab: По объектам ────────────────────────────────────────────

function ProjectsTab({ supplierId }: { supplierId: ID }) {
  const navigate = useNavigate();
  const q = useSupplierProjects(supplierId);
  const rows = q.data ?? [];

  const totalTurnover = rows.reduce((s, r) => s + r.turnover, 0);
  const totalVolume = rows.reduce((s, r) => s + r.volume_m3, 0);
  const totalInvoices = rows.reduce((s, r) => s + r.invoice_count, 0);

  if (q.isPending) return <TabSkeleton />;
  if (q.isError)
    return (
      <EmptyState title="Ошибка загрузки" description="Не удалось загрузить данные по объектам." />
    );
  if (rows.length === 0)
    return (
      <EmptyState
        title="Нет данных по объектам"
        description="У поставщика пока нет счетов ни по одному объекту."
      />
    );

  return (
    <>
      <Surface padding="none" className="overflow-x-auto">
        <Table className="min-w-[740px]">
          <TableHeader>
            <TableRow>
              <TableHead>Объект</TableHead>
              <TableHead className="text-right">Оборот</TableHead>
              <TableHead className="text-right">Объём, м³</TableHead>
              <TableHead className="text-right">Счетов</TableHead>
              <TableHead className="text-right">Наценка к плану</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow
                key={r.project_id}
                role="button"
                tabIndex={0}
                className="cursor-pointer hover:bg-surface-hover"
                onClick={() => navigate(`/projects/${r.project_id}`)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/projects/${r.project_id}`); } }}
              >
                <TableCell>
                  <div className="font-medium text-fg">{r.project_name}</div>
                  {r.contract_number && (
                    <div className="mt-0.5 text-xs text-fg-tertiary">{r.contract_number}</div>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(r.turnover)}
                </TableCell>
                <TableCell className="text-right text-fg-secondary tabular-nums">
                  {formatNumber(Math.round(r.volume_m3))}
                </TableCell>
                <TableCell className="text-right text-fg-secondary tabular-nums">
                  {formatNumber(r.invoice_count)}
                </TableCell>
                <TableCell>
                  {r.deviation_pct !== null && r.deviation_amount !== null ? (
                    <DeviationBadge pct={r.deviation_pct} amount={r.deviation_amount} />
                  ) : (
                    <span className="block text-right text-xs text-fg-tertiary">—</span>
                  )}
                </TableCell>
                <TableCell className="text-fg-tertiary">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="m9 18 6-6-6-6" />
                  </svg>
                </TableCell>
              </TableRow>
            ))}

            {/* Итого */}
            <TableRow className="border-t border-border-default bg-surface-sunken font-medium">
              <TableCell className="text-xs uppercase tracking-wide text-fg-secondary">
                Итого
              </TableCell>
              <TableCell className="text-right tabular-nums text-fg">
                {formatMoney(totalTurnover)}
              </TableCell>
              <TableCell className="text-right tabular-nums text-fg">
                {formatNumber(Math.round(totalVolume))}
              </TableCell>
              <TableCell className="text-right tabular-nums text-fg">
                {formatNumber(totalInvoices)}
              </TableCell>
              <TableCell className="text-right text-xs italic text-fg-tertiary">
                не суммируем
              </TableCell>
              <TableCell />
            </TableRow>
          </TableBody>
        </Table>
      </Surface>
      <OutlierHint rows={rows} />
    </>
  );
}

// ─── Tab: Счета ──────────────────────────────────────────────────

function InvoicesTab({ supplierId, projectRows }: { supplierId: ID; projectRows: SupplierProjectRow[] }) {
  const navigate = useNavigate();
  const [projectFilter, setProjectFilter] = useState<string>("__all__");

  const projectId = projectFilter === "__all__" ? undefined : Number(projectFilter) as ID;
  const q = useSupplierInvoices(supplierId, projectId);
  const rows = q.data ?? [];

  if (q.isPending) return <TabSkeleton />;
  if (q.isError)
    return <EmptyState title="Ошибка загрузки" description="Не удалось загрузить счета." />;

  return (
    <div>
      {projectRows.length > 1 && (
        <div className="mb-3">
          <Select value={projectFilter} onValueChange={(v) => setProjectFilter(v ?? "__all__")}>
            <SelectTrigger className="w-56">
              <SelectValue>
                {projectFilter === "__all__"
                  ? "Все объекты"
                  : (projectRows.find((p) => String(p.project_id) === projectFilter)?.project_name ?? "Все объекты")}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Все объекты</SelectItem>
              {projectRows.map((p) => (
                <SelectItem key={p.project_id} value={String(p.project_id)}>
                  {p.project_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState
          title="Счетов не найдено"
          description="По выбранным фильтрам счета отсутствуют."
        />
      ) : (
        <Surface padding="none" className="overflow-x-auto">
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow>
                <TableHead>Номер</TableHead>
                <TableHead>Дата</TableHead>
                <TableHead>Объект</TableHead>
                <TableHead className="text-right">Сумма</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((inv) => (
                <TableRow
                  key={inv.id}
                  role="button"
                  tabIndex={0}
                  className="cursor-pointer hover:bg-surface-hover"
                  onClick={() => navigate(`/documents/${inv.document_id}`)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/documents/${inv.document_id}`); } }}
                >
                  <TableCell className="font-medium">{inv.number}</TableCell>
                  <TableCell className="text-fg-secondary tabular-nums">
                    {formatDate(inv.date)}
                  </TableCell>
                  <TableCell className="text-fg-secondary">{inv.project_name}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(inv.amount)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Surface>
      )}
    </div>
  );
}

// ─── Tab: Обзор ─────────────────────────────────────────────────

function OverviewTab({ supplierId }: { supplierId: ID }) {
  const detailQ = useSupplierDetail(supplierId);
  const projectsQ = useSupplierProjects(supplierId);

  if (detailQ.isPending || projectsQ.isPending) return <TabSkeleton />;
  if (detailQ.isError)
    return <EmptyState title="Ошибка загрузки" />;

  const s = detailQ.data;
  const projects = projectsQ.data ?? [];

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-subtle bg-surface p-5">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-tertiary">
          Реквизиты
        </h3>
        <dl className="grid gap-2 text-sm">
          <div className="flex gap-3">
            <dt className="w-32 text-fg-tertiary">Название</dt>
            <dd className="text-fg">{s.name}</dd>
          </div>
          {s.inn && (
            <div className="flex gap-3">
              <dt className="w-32 text-fg-tertiary">ИНН</dt>
              <dd className="font-mono text-fg">{s.inn}</dd>
            </div>
          )}
          {s.categories.length > 0 && (
            <div className="flex gap-3">
              <dt className="w-32 text-fg-tertiary">Категории</dt>
              <dd className="text-fg">{s.categories.join(", ")}</dd>
            </div>
          )}
          {s.first_invoice_date && (
            <div className="flex gap-3">
              <dt className="w-32 text-fg-tertiary">Первый счёт</dt>
              <dd className="text-fg">{formatDate(s.first_invoice_date)}</dd>
            </div>
          )}
        </dl>
      </div>

      {projects.length > 0 && (
        <div className="rounded-lg border border-border-subtle bg-surface p-5">
          <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-tertiary">
            Объекты ({projects.length})
          </h3>
          <ul className="space-y-2">
            {projects.map((p) => (
              <li key={p.project_id}>
                <Link
                  to={`/projects/${p.project_id}`}
                  className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-surface-hover"
                >
                  <div>
                    <div className="text-sm text-fg">{p.project_name}</div>
                    {p.contract_number && (
                      <div className="text-xs text-fg-tertiary">{p.contract_number}</div>
                    )}
                  </div>
                  <span className="text-sm text-fg-secondary">{formatMoney(p.turnover)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ─── Skeleton ────────────────────────────────────────────────────

function TabSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  );
}

// ─── Main page ───────────────────────────────────────────────────

function SupplierPageContent() {
  const { id } = useParams<{ id: string }>();
  const supplierId = (() => {
    const parsed = Number(id);
    return Number.isInteger(parsed) && parsed > 0 ? parsed as ID : null;
  })();

  const [editOpen, setEditOpen] = useState(false);

  const detailQ = useSupplierDetail(supplierId);
  const projectsQ = useSupplierProjects(supplierId);

  const supplier = detailQ.data;
  const projectRows = projectsQ.data ?? [];

  const title = supplier?.name ?? "Поставщик";
  const subtitle = [
    supplier?.inn ? `ИНН ${supplier.inn}` : null,
    supplier?.categories.join(", ") ?? null,
    projectRows.length > 0 ? `${projectRows.length} объект${pluralRu(projectRows.length)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="container-page py-8">
      <Breadcrumbs
        items={[
          { label: "Поставщики", to: "/suppliers" },
          { label: title },
        ]}
      />

      <div className="mt-4">
        <PageHeader
          serif
          title={detailQ.isPending ? "…" : title}
          subtitle={detailQ.isPending ? "" : subtitle}
          actions={
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="md"
                leftIcon={<Pencil size={14} />}
                onClick={() => setEditOpen(true)}
                disabled={!supplier}
              >
                Редактировать
              </Button>
              <Button variant="secondary" size="md" leftIcon={<Download size={14} />}>
                Экспорт
              </Button>
            </div>
          }
        />
      </div>

      {/* KPI плашки */}
      <div className="mt-6 grid grid-cols-3 gap-3">
        {detailQ.isPending ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))
        ) : (
          <>
            <KpiCard
              label="Оборот"
              value={supplier ? formatMillions(supplier.turnover) : "—"}
            />
            <KpiCard
              label="Объектов"
              value={supplier ? formatNumber(supplier.project_count) : "—"}
            />
            <KpiCard
              label="Счетов"
              value={supplier ? formatNumber(supplier.invoice_count) : "—"}
            />
          </>
        )}
      </div>

      {/* Табы */}
      <Tabs defaultValue="projects" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="projects">
            По объектам{projectRows.length > 0 ? ` · ${projectRows.length}` : ""}
          </TabsTrigger>
          <TabsTrigger value="invoices">
            Счета{supplier?.invoice_count ? ` · ${supplier.invoice_count}` : ""}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-5">
          {supplierId && <OverviewTab supplierId={supplierId} />}
        </TabsContent>

        <TabsContent value="projects" className="pt-5">
          {supplierId && <ProjectsTab supplierId={supplierId} />}
        </TabsContent>

        <TabsContent value="invoices" className="pt-5">
          {supplierId && (
            <InvoicesTab supplierId={supplierId} projectRows={projectRows} />
          )}
        </TabsContent>
      </Tabs>

      {supplierId && supplier && (
        <EditSupplierDialog
          key={`${supplierId}-${editOpen}`}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          supplierId={supplierId}
          initialName={supplier.name}
          initialInn={supplier.inn}
        />
      )}
    </div>
  );
}

/**
 * key={id} пересоздаёт компонент при переходе между поставщиками,
 * сбрасывая editOpen и активную вкладку автоматически.
 */
export default function SupplierPage() {
  const { id } = useParams<{ id: string }>();
  return <SupplierPageContent key={id} />;
}

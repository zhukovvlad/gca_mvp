import { useMemo, Fragment } from "react";
import { FileDown } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { Skeleton } from "@/components/ui-domain/Skeleton";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Button } from "@/components/ui-domain/Button";
import { MONTH_NAMES_RU } from "@/lib/constants";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { useDashboardMonthlySummary } from "@/services/queries";
import { formatMoney, formatNumber } from "@/lib/format";
import type { ID } from "@/types/common";
import type { MonthlyBucketRaw } from "@/types/dashboard";

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────
interface MonthlyBucket {
  year: number;
  month: number;
  total_amount: number;
  total_qty: number;
  invoice_count: number;
  empty: boolean; // нет счетов в этом месяце
}

interface MonthlyTabProps {
  projectId: ID;
  projectName: string;
  /** Скоуп направления (спека §6.4); undefined — все направления / legacy. */
  direction?: string;
  onNavigateToMonth: (year: number, month: number) => void;
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function buildFullSequence(raw: MonthlyBucketRaw[]): MonthlyBucket[] {
  if (raw.length === 0) return [];

  const byKey = new Map<string, MonthlyBucketRaw>();
  for (const r of raw) byKey.set(`${r.year}-${r.month}`, r);

  // Bounds
  const minYear = raw[0].year, minMonth = raw[0].month;
  const maxYear = raw[raw.length - 1].year, maxMonth = raw[raw.length - 1].month;

  const result: MonthlyBucket[] = [];
  let y = minYear, m = minMonth;
  while (y < maxYear || (y === maxYear && m <= maxMonth)) {
    const key = `${y}-${m}`;
    const found = byKey.get(key);
    result.push(
      found
        ? { ...found, empty: false }
        : { year: y, month: m, total_amount: 0, total_qty: 0, invoice_count: 0, empty: true },
    );
    m++;
    if (m > 12) { m = 1; y++; }
  }
  return result;
}

function exportToCsv(rows: MonthlyBucket[], projectName: string, volumeUnit: string) {
  const header = ["Период", "Оборот (₽)", `Объём (${volumeUnit})`, "Счетов"].join(";");
  const lines = rows.map((r) =>
    [
      `${MONTH_NAMES_RU[r.month - 1]} ${r.year}`,
      r.empty ? "0" : String(r.total_amount).replace(".", ","),
      r.empty ? "0" : String(r.total_qty).replace(".", ","),
      r.empty ? "0" : String(r.invoice_count),
    ].join(";"),
  );
  const totals = rows.reduce(
    (acc, r) => ({
      amount: acc.amount + r.total_amount,
      qty: acc.qty + r.total_qty,
      count: acc.count + r.invoice_count,
    }),
    { amount: 0, qty: 0, count: 0 },
  );
  lines.push(
    ["Итого", String(totals.amount).replace(".", ","), String(totals.qty).replace(".", ","), String(totals.count)].join(";"),
  );

  const bom = "\uFEFF";
  const csv = bom + [header, ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `закупки-по-месяцам-${projectName.replace(/[\\/:*?"<>|]/g, "_").slice(0, 80)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─────────────────────────────────────────────
// Chart
// ─────────────────────────────────────────────
const MONTH_SHORT_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function formatAmountShort(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млрд`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн`;
  if (v >= 1_000) return `${(v / 1_000).toLocaleString("ru-RU", { maximumFractionDigits: 0 })} тыс`;
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
}

const chartConfig = {
  total_amount: { label: "Оборот, ₽ с НДС" },
} satisfies ChartConfig;

function TurnoverChart({ buckets }: { buckets: MonthlyBucket[] }) {
  const labelEvery = buckets.length <= 12 ? 1 : buckets.length <= 24 ? 2 : 3;

  const data = buckets.map((b, i) => ({
    key: `${b.year}-${b.month}`,
    label: `${MONTH_SHORT_RU[b.month - 1]}${i === 0 || b.month === 1 ? ` ${b.year}` : ""}`,
    fullLabel: `${MONTH_SHORT_RU[b.month - 1]} ${b.year}`,
    total_amount: b.total_amount,
    empty: b.empty,
    index: i,
  }));

  return (
    <ChartContainer config={chartConfig} className="w-full" style={{ height: 180 }}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid vertical={false} strokeOpacity={0.08} />
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "currentColor", opacity: 0.65 }}
          interval={labelEvery - 1}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11, fill: "currentColor", opacity: 0.65 }}
          tickFormatter={formatAmountShort}
          width={60}
        />
        <ChartTooltip
          cursor={{ fill: "currentColor", fillOpacity: 0.04 }}
          content={
            <ChartTooltipContent
              formatter={(value) => (
                <div className="flex items-baseline justify-between gap-3 w-full">
                  <span className="text-muted-foreground">Оборот</span>
                  <span className="font-mono font-medium tabular-nums">{formatMoney(value as number)}</span>
                </div>
              )}
              labelFormatter={(_, payload) => payload?.[0]?.payload?.fullLabel ?? ""}
            />
          }
        />
        <Bar dataKey="total_amount" radius={[3, 3, 0, 0]} maxBarSize={40}>
          {data.map((d) => (
            <Cell
              key={d.key}
              fill={d.empty ? "currentColor" : "var(--color-accent, #9CC79A)"}
              fillOpacity={d.empty ? 0.1 : 1}
            />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

// ─────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────
export function MonthlyTab({ projectId, projectName, direction, onNavigateToMonth }: MonthlyTabProps) {
  const monthlyQ = useDashboardMonthlySummary(projectId, direction);

  const buckets = useMemo(
    () => buildFullSequence(monthlyQ.data ?? []),
    [monthlyQ.data],
  );

  // Родная единица объёма из ответа (чистка хардкода м³ — спека §7.6)
  const volumeUnit = (monthlyQ.data ?? []).find((b) => b.volume_unit)?.volume_unit ?? "м³";

  const totals = useMemo(
    () =>
      buckets.reduce(
        (acc, b) => ({
          amount: acc.amount + b.total_amount,
          qty: acc.qty + b.total_qty,
          count: acc.count + b.invoice_count,
        }),
        { amount: 0, qty: 0, count: 0 },
      ),
    [buckets],
  );

  // Group rows by year for subheadings
  const byYear = useMemo(() => {
    const map = new Map<number, MonthlyBucket[]>();
    for (const b of buckets) {
      const list = map.get(b.year) ?? [];
      list.push(b);
      map.set(b.year, list);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a - b);
  }, [buckets]);

  const multiYear = byYear.length > 1;

  // ── Loading ──
  if (monthlyQ.isLoading) {
    return (
      <div className="space-y-3 mt-6">
        <Skeleton className="h-[90px]" />
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    );
  }

  // ── Error ──
  if (monthlyQ.isError) {
    return (
      <div className="mt-6">
        <EmptyState
          title="Не удалось загрузить данные"
          description="Попробуйте обновить страницу."
        />
      </div>
    );
  }

  // ── Empty ──
  if (buckets.length === 0) {
    return (
      <div className="mt-6">
        <EmptyState
          title="Нет счетов по этому объекту"
          description="Загрузите счета-фактуры, чтобы увидеть разбивку по месяцам."
        />
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-4">
      {/* Header row: export button */}
      <div className="flex justify-end">
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<FileDown size={14} />}
          onClick={() => exportToCsv(buckets, projectName, volumeUnit)}
        >
          Экспорт CSV
        </Button>
      </div>

      {/* Bar chart */}
      <div className="rounded-lg border border-border-subtle bg-surface px-4 pt-3 pb-1">
        <TurnoverChart buckets={buckets} />
      </div>

      {/* Table */}
      <div className="rounded-lg border border-border-subtle bg-surface">
        <Table>
          <TableHeader>
            <TableRow className="text-xs text-fg-tertiary hover:bg-transparent">
              <TableHead className="font-medium">Месяц</TableHead>
              <TableHead className="font-medium text-right">Оборот, ₽ с НДС</TableHead>
              <TableHead className="font-medium text-right">Объём, {volumeUnit}</TableHead>
              <TableHead className="font-medium text-right">Счетов</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {byYear.map(([year, yearBuckets]) => (
              <Fragment key={year}>
                {multiYear && (
                  <TableRow className="bg-surface-hover hover:bg-surface-hover">
                    <TableCell
                      colSpan={4}
                      className="py-1 text-xs font-semibold text-fg-tertiary tracking-wide uppercase"
                    >
                      {year}
                    </TableCell>
                  </TableRow>
                )}
                {yearBuckets.map((b) => (
                  <MonthRow
                    key={`${b.year}-${b.month}`}
                    bucket={b}
                    onClick={b.empty ? undefined : () => onNavigateToMonth(b.year, b.month)}
                  />
                ))}
              </Fragment>
            ))}
          </TableBody>
          <TableFooter className="font-medium">
            <TableRow>
              <TableCell className="text-fg-secondary text-xs uppercase tracking-wide">Итого</TableCell>
              <TableCell className="text-right font-mono text-fg tabular-nums">
                {formatMoney(totals.amount)}
              </TableCell>
              <TableCell className="text-right font-mono text-fg tabular-nums">
                {formatNumber(totals.qty)}
              </TableCell>
              <TableCell className="text-right font-mono text-fg tabular-nums">
                {formatNumber(totals.count)}
              </TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Month Row
// ─────────────────────────────────────────────
function MonthRow({
  bucket,
  onClick,
}: {
  bucket: MonthlyBucket;
  onClick?: () => void;
}) {
  const label = `${MONTH_NAMES_RU[bucket.month - 1]} ${bucket.year}`;

  if (bucket.empty) {
    return (
      <TableRow
        className="hover:bg-transparent"
        style={{
          backgroundImage:
            "repeating-linear-gradient(-45deg, transparent, transparent 4px, rgba(255,255,255,0.02) 4px, rgba(255,255,255,0.02) 8px)",
        }}
      >
        <TableCell className="text-fg-tertiary">{label}</TableCell>
        <TableCell className="text-right font-mono text-fg-tertiary tabular-nums">—</TableCell>
        <TableCell className="text-right font-mono text-fg-tertiary tabular-nums">—</TableCell>
        <TableCell className="text-right font-mono text-fg-tertiary tabular-nums">—</TableCell>
      </TableRow>
    );
  }

  return (
    <TableRow className="group cursor-pointer" onClick={onClick}>
      <TableCell className="p-0">
        <button
          type="button"
          className="w-full text-left px-2 py-2 text-fg group-hover:text-accent-text transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-sm"
        >
          {label}
        </button>
      </TableCell>
      <TableCell className="text-right font-mono text-fg tabular-nums">
        {formatMoney(bucket.total_amount)}
      </TableCell>
      <TableCell className="text-right font-mono text-fg tabular-nums">
        {formatNumber(bucket.total_qty)}
      </TableCell>
      <TableCell className="text-right font-mono text-fg tabular-nums">
        {formatNumber(bucket.invoice_count)}
      </TableCell>
    </TableRow>
  );
}

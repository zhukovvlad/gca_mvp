import { useState, useMemo } from "react";
import {
  LineChart, Line, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid,
} from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { DashboardCalculation } from "@/types/dashboard";
import { formatMoney } from "@/lib/format";

const LINE_COLORS = [
  "#9CC79A", "#D85A30", "#5B8CDB", "#C4A35A", "#9B6DB5",
  "#E8A87C", "#4ECDC4", "#FFE66D", "#A8DDA8", "#F7AEF8",
];
const MAX_SERIES = 10;

const RANGE_OPTIONS = [
  { label: "3М", months: 3 },
  { label: "6М", months: 6 },
  { label: "12М", months: 12 },
  { label: "Всё", months: null },
] as const;

interface Props {
  calculations: DashboardCalculation[];
}

export function PriceChart({ calculations }: Props) {
  const [rangeMonths, setRangeMonths] = useState<number | null>(6);

  const valid = useMemo(() => {
    if (!rangeMonths) return calculations.filter((c) => c.avg_price > 0);
    // Cutoff relative to the latest data point, not today
    const maxTs = calculations.reduce(
      (m, c) => Math.max(m, new Date(c.period_start).getTime()),
      0
    );
    if (!maxTs) return [];
    const cutoffDate = new Date(maxTs);
    cutoffDate.setMonth(cutoffDate.getMonth() - rangeMonths);
    return calculations.filter(
      (c) => c.avg_price > 0 && new Date(c.period_start) >= cutoffDate
    );
  }, [calculations, rangeMonths]);

  // Unique months sorted chronologically
  const periods = useMemo(
    () => [...new Set(valid.map((c) => c.period_start.slice(0, 7)))].sort(),
    [valid]
  );

  // Top-N material classes by data point count
  const topClasses = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of valid) {
      const name = c.material_class_name ?? "?";
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_SERIES)
      .map(([name]) => name);
  }, [valid]);

  // Sanitized key → display name (Cyrillic names can't be used as CSS var keys)
  const classKeys = useMemo(
    () => topClasses.map((name, i) => ({ key: `cls_${i}`, name })),
    [topClasses]
  );

  const filterBar = (
    <div className="mb-4 flex items-center justify-between">
      <h2 className="font-serif text-base font-medium text-fg">Динамика средних цен по материалам</h2>
      <div className="flex gap-1">
        {RANGE_OPTIONS.map((opt) => (
          <button
            key={opt.label}
            type="button"
            onClick={() => setRangeMonths(opt.months)}
            className={
              "rounded px-2.5 py-1 text-xs font-medium transition-colors " +
              (rangeMonths === opt.months
                ? "bg-accent text-white"
                : "bg-surface-hover text-fg-secondary hover:text-fg")
            }
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );

  if (!valid.length || !topClasses.length) {
    return (
      <>
        {filterBar}
        <div className="flex h-40 items-center justify-center text-sm text-fg-tertiary">
          Нет данных за выбранный период
        </div>
      </>
    );
  }

  // ── LINE CHART (more than one period) ─────────────────────────────────────
  if (periods.length > 1) {
    // Aggregate: average avg_price by (month, material_class)
    const classKeyByName = new Map(classKeys.map(({ name, key }) => [name, key]));
    const sums = new Map<string, { sum: number; cnt: number }>();
    for (const c of valid) {
      const name = c.material_class_name ?? "?";
      const classKey = classKeyByName.get(name);
      if (!classKey) continue;
      const key = `${c.period_start.slice(0, 7)}__${classKey}`;
      const prev = sums.get(key) ?? { sum: 0, cnt: 0 };
      sums.set(key, { sum: prev.sum + c.avg_price, cnt: prev.cnt + 1 });
    }

    const rowMap = new Map<string, Record<string, unknown>>(
      periods.map((p) => [p, { period: p }])
    );
    for (const [key, { sum, cnt }] of sums) {
      const [month, clsKey] = key.split("__");
      const row = rowMap.get(month);
      if (row) row[clsKey] = Math.round(sum / cnt);
    }
    const data = periods.map((p) => rowMap.get(p)!);

    const chartConfig = Object.fromEntries(
      classKeys.map(({ key, name }, i) => [
        key,
        { label: name, color: LINE_COLORS[i % LINE_COLORS.length] },
      ])
    ) satisfies ChartConfig;

    return (
      <>
        {filterBar}
        <ChartContainer config={chartConfig} className="h-[240px] w-full">
          <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis
              tickFormatter={(v) => (typeof v === "number" ? formatMoney(v) : String(v))}
              tick={{ fontSize: 11 }}
              width={90}
            />
            <ChartTooltip
              content={<ChartTooltipContent indicator="line" />}
            />
            <ChartLegend content={<ChartLegendContent />} />
            {classKeys.map(({ key }) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={`var(--color-${key})`}
                dot={periods.length < 6}
                strokeWidth={2}
                connectNulls
              />
            ))}
          </LineChart>
        </ChartContainer>
      </>
    );
  }

  // ── BAR CHART (single period fallback) ────────────────────────────────────
  const barData = valid.slice(0, 10).map((c) => ({
    name: c.material_class_name ?? "?",
    avg: c.avg_price,
    refPrice: c.reference_price ?? null,
    deviation_pct: c.deviation_pct ?? 0,
  }));

  const barConfig: ChartConfig = {
    avg: { label: "Средняя цена", color: "#9CC79A" },
    refPrice: { label: "Эталон", color: "rgba(255,255,255,0.15)" },
  };

  return (
    <>
      {filterBar}
      <ChartContainer config={barConfig} className="h-[240px] w-full">
        <BarChart data={barData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis
            tickFormatter={(v) => (typeof v === "number" ? formatMoney(v) : String(v))}
            tick={{ fontSize: 11 }}
            width={90}
          />
          <ChartTooltip
            content={<ChartTooltipContent indicator="dot" />}
          />
          <ChartLegend content={<ChartLegendContent />} />
          <Bar dataKey="avg" name="avg" radius={[3, 3, 0, 0]}>
            {barData.map((entry, i) => (
              <Cell
                key={`${entry.name}-${i}`}
                fill={
                  entry.refPrice == null
                    ? "#9CA39A"
                    : entry.deviation_pct > 0
                    ? "#D85A30"
                    : "#9CC79A"
                }
              />
            ))}
          </Bar>
          <Bar dataKey="refPrice" name="refPrice" radius={[3, 3, 0, 0]} fill="rgba(255,255,255,0.15)" />
        </BarChart>
      </ChartContainer>
    </>
  );
}

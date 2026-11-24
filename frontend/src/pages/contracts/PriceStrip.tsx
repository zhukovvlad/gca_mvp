import { ReferenceArea, ReferenceDot, ReferenceLine, Scatter, ScatterChart, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { cn } from "@/lib/utils";
import { formatMoney } from "@/pages/tenders/our-price/money";
import type { GroupPricesView } from "@/types/domain";

import { logDomain } from "./logDomain";
import {
  STRIP_LABEL,
  STRIP_LEGEND,
  STRIP_THIS,
  STRIP_USUAL,
} from "./suspiciousCopy";

export interface PriceStripProps {
  /** Цены договоров группы и обычная цена (`GroupPricesView`). */
  prices: GroupPricesView;
  /** Цена самой строки: номинальное нетто. */
  net: string;
  /** Тесный вид — панель строки КП. */
  compact?: boolean;
}

interface Dot {
  x: number;
  y: 0;
  label: string;
  price: string;
  own: boolean;
  me: boolean;
}

/**
 * Цвета — токены приложения, а не имена из макета (`docs/pitfalls/frontend.md`: неразрешимая `var()`
 * молча даёт чёрный `fill`). Существование токенов проверяет `PriceStrip.test.tsx`.
 */
const COLOR = {
  dot: "var(--text-tertiary)",
  ownFill: "var(--bg-surface)",
  ownStroke: "var(--text-secondary)",
  me: "var(--warning)",
  band: "var(--neutral-soft)",
  usual: "var(--text-primary)",
  axis: "var(--border-subtle)",
} as const;

const CONFIG: ChartConfig = { price: { label: "цена", color: COLOR.dot } };

function tickText(value: number): string {
  return `${value.toLocaleString("ru-RU")} ₽`;
}

interface DotShapeProps {
  cx?: number;
  cy?: number;
  payload?: Dot;
}

function DotShape({ cx, cy, payload }: DotShapeProps) {
  if (cx === undefined || cy === undefined || payload === undefined) return <g />;
  if (payload.me) return <circle cx={cx} cy={cy} r={7.5} style={{ fill: COLOR.me }} />;
  if (payload.own) {
    return <circle cx={cx} cy={cy} r={4.5} style={{ fill: COLOR.ownFill, stroke: COLOR.ownStroke, strokeWidth: 1.5 }} />;
  }
  return <circle cx={cx} cy={cy} r={4.5} style={{ fill: COLOR.dot }} />;
}

/**
 * Полоса цен (макет, разделы 1 и 3): точки — цены договоров, пунктир — обычная цена, крупная точка —
 * эта строка, подложка — разброс других договоров; шкала логарифмическая. Один компонент для списка
 * и панели строки КП. Числа здесь — только геометрия; подписи денег печатаются из исходных строк.
 */
export function PriceStrip({ prices, net, compact = false }: PriceStripProps) {
  const netValue = Number(net);
  const usual = Number(prices.usual);
  const dots: Dot[] = prices.contracts.map((c) => ({
    x: Number(c.price),
    y: 0,
    label: c.object_title,
    price: c.price,
    own: c.is_own,
    me: false,
  }));
  const me: Dot = { x: netValue, y: 0, label: STRIP_THIS, price: net, own: false, me: true };
  const others = prices.contracts.filter((c) => !c.is_own).map((c) => Number(c.price));
  const { lo, hi, ticks } = logDomain([...dots.map((d) => d.x), netValue, usual]);

  return (
    <div data-testid="price-strip" className="min-w-0">
      <div className="overflow-x-auto">
        <div className={compact ? "min-w-[320px]" : "min-w-[460px]"}>
          <div role="img" aria-label={STRIP_LABEL}>
            <ChartContainer
              config={CONFIG}
              className={cn("aspect-auto w-full max-w-[620px] justify-start", compact ? "h-[72px]" : "h-[84px]")}
            >
              <ScatterChart margin={{ top: 22, right: 18, bottom: 4, left: 18 }}>
                <XAxis
                  type="number"
                  dataKey="x"
                  scale="log"
                  domain={[lo, hi]}
                  ticks={ticks}
                  tickFormatter={tickText}
                  allowDataOverflow
                  tickLine={{ stroke: COLOR.dot }}
                  axisLine={{ stroke: COLOR.axis }}
                  tick={{ fill: COLOR.dot, fontSize: 10 }}
                />
                <YAxis type="number" dataKey="y" domain={[-1, 1]} hide />
                {others.length > 0 && (
                  <ReferenceArea
                    x1={Math.min(...others)}
                    x2={Math.max(...others)}
                    y1={-0.45}
                    y2={0.45}
                    fill={COLOR.band}
                    fillOpacity={1}
                    stroke="none"
                    ifOverflow="visible"
                  />
                )}
                <ReferenceLine
                  segment={[
                    { x: usual, y: 0 },
                    { x: netValue, y: 0 },
                  ]}
                  stroke={COLOR.me}
                  strokeWidth={2}
                  ifOverflow="visible"
                />
                <ReferenceLine
                  x={usual}
                  stroke={COLOR.usual}
                  strokeDasharray="3 2"
                  ifOverflow="visible"
                  label={{ value: STRIP_USUAL, position: "top", fill: COLOR.usual, fontSize: 10.5, fontWeight: 600 }}
                />
                <ReferenceDot
                  x={netValue}
                  y={0}
                  r={0}
                  ifOverflow="visible"
                  label={{ value: STRIP_THIS, position: "top", offset: 12, fill: COLOR.me, fontSize: 10.5, fontWeight: 600 }}
                />
                <ChartTooltip
                  cursor={false}
                  isAnimationActive={false}
                  content={({ active, payload }) => {
                    const dot = active ? (payload?.[0]?.payload as Dot | undefined) : undefined;
                    if (dot === undefined) return null;
                    return (
                      <div
                        data-testid="price-strip-tooltip"
                        className="rounded-md border border-border-default bg-surface px-2 py-1 text-xs shadow-md"
                      >
                        {dot.label}: <span className="font-mono">{formatMoney(dot.price)}</span>
                      </div>
                    );
                  }}
                />
                <Scatter data={dots} shape={DotShape} isAnimationActive={false} />
                <Scatter data={[me]} shape={DotShape} isAnimationActive={false} />
              </ScatterChart>
            </ChartContainer>
          </div>
        </div>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3.5 gap-y-0.5 text-2xs text-fg-secondary">
        <Legend mark={<i className="inline-block size-2 rounded-full bg-fg-tertiary" />} text={STRIP_LEGEND.contract} />
        <Legend
          mark={<i className="inline-block size-2 rounded-full border-[1.5px] border-fg-secondary bg-surface" />}
          text={STRIP_LEGEND.own}
        />
        <Legend mark={<i className="inline-block h-2.5 border-l-[1.5px] border-dashed border-fg" />} text={STRIP_LEGEND.usual} />
        <Legend mark={<i className="inline-block size-2.5 rounded-full bg-warning" />} text={STRIP_LEGEND.line} />
        <Legend mark={<i className="inline-block h-2 w-[18px] rounded-full border border-border-subtle bg-neutral-soft" />} text={STRIP_LEGEND.band} />
      </div>
    </div>
  );
}

function Legend({ mark, text }: { mark: React.ReactNode; text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {mark}
      {text}
    </span>
  );
}

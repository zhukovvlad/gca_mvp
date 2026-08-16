import { formatDecimalMoney } from "@/lib/format";
import type { DashboardChart, DashboardChartLane } from "@/types/domain";

/** Цвета дорожек — набор `--chart-*`, прошедший валидатор различимости
 *  (`index.css`, §кольцо). Новые цвета на глаз не подбирать. */
const LANE_COLORS = [
  "var(--chart-7)",
  "var(--chart-3)",
  "var(--chart-1)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-2)",
];

interface Scale {
  min: number;
  span: number;
}

function scaleOf(chart: DashboardChart): Scale {
  const values = chart.classes
    .flatMap((lane) => lane.points.map((point) => Number(point.per_sqm)))
    .filter((value) => Number.isFinite(value));
  if (values.length === 0) return { min: 0, span: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  return { min, span: max === min ? 1 : max - min };
}

function offset(value: string, scale: Scale): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return ((numeric - scale.min) / scale.span) * 100;
}

function Lane({
  lane,
  color,
  scale,
}: {
  lane: DashboardChartLane;
  color: string;
  scale: Scale;
}) {
  return (
    <div
      data-testid={`lane-${lane.rate_class_id}`}
      className="grid grid-cols-[10rem_1fr] items-center gap-4"
    >
      <div className="flex items-start gap-2">
        <span
          className="mt-1 h-2.5 w-2.5 flex-none rounded-sm"
          style={{ background: color }}
          aria-hidden="true"
        />
        {/*
          Подпись класса видима на КАЖДОЙ дорожке — этим снимается WARN
          валидатора палитры на контраст зелёного и охристого к белой
          поверхности: различение дорожек никогда не держится на одном цвете.
        */}
        <span className="min-w-0 text-xs text-fg">
          {lane.rate_class_title ?? "Без класса"}
          <em className="mt-0.5 block font-mono text-2xs not-italic text-fg-secondary">
            {lane.spread
              ? `${formatDecimalMoney(lane.spread.min, "", 0)}–${formatDecimalMoney(lane.spread.max, "", 0)}`
              : formatDecimalMoney(lane.points[0]?.per_sqm ?? null, "", 0)}
            <i className="not-italic text-fg-muted"> · {lane.points.length} об.</i>
          </em>
        </span>
      </div>
      <div className="relative h-9 border-b border-border-subtle">
        {/*
          ПОЛОСА РАЗМАХА ТОЛЬКО У КЛАССА С НЕСКОЛЬКИМИ ОБЪЕКТАМИ (решение 11
          макета). У класса с одним объектом размаха не существует, и нарисовать
          его значило бы выдумать факт. Условие стоит на `spread`, который
          сервер отдаёт `null` ровно в этом случае, — второго правила на клиенте
          нет намеренно.
        */}
        {lane.spread && (
          <span
            data-testid={`spread-${lane.rate_class_id}`}
            className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full opacity-40"
            style={{
              background: color,
              left: `${offset(lane.spread.min, scale)}%`,
              width: `${offset(lane.spread.max, scale) - offset(lane.spread.min, scale)}%`,
            }}
          />
        )}
        {lane.points.map((point) => (
          <span
            key={point.object_id}
            data-testid={`dot-${point.object_id}`}
            title={`${point.title} · ${formatDecimalMoney(point.per_sqm, "", 0)} ₽/м²`}
            className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{ background: color, left: `${offset(point.per_sqm, scale)}%` }}
          />
        ))}
      </div>
    </div>
  );
}

export function PerSqmChart({ chart }: { chart: DashboardChart }) {
  const scale = scaleOf(chart);

  return (
    <section className="mt-5 overflow-hidden rounded-lg border border-border-subtle bg-surface">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-border-subtle px-4 py-3">
        <h2 className="text-sm font-medium text-fg">Удельная стоимость объектов</h2>
        <span className="text-xs text-fg-tertiary">
          ₽ за м² с НДС, точка — объект; полоса — размах класса от минимума к максимуму
        </span>
      </div>
      <div className="flex flex-col gap-1 px-4 py-4">
        {chart.classes.map((lane, index) => (
          <Lane
            key={lane.rate_class_id}
            lane={lane}
            color={LANE_COLORS[index % LANE_COLORS.length]}
            scale={scale}
          />
        ))}
      </div>
      <div className="border-t border-border-subtle px-4 py-3 text-xs text-fg-tertiary">
        Вошли {chart.coverage.counted} объект
        {chart.coverage.counted === 1 ? "" : "ов"} из {chart.coverage.total}: у остальных не
        заведена площадь или нет действующей суммы.
      </div>
    </section>
  );
}

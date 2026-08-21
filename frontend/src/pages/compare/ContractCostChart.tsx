import { useId, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ReferenceDot,
  ReferenceLine,
  XAxis,
  YAxis,
  type DotProps,
} from "recharts";

import { Button } from "@/components/ui/button";
import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import { addDecimalStrings } from "@/lib/decimal";
import { formatDecimalMoney, formatNumber } from "@/lib/format";
import { coefficientLevel } from "@/lib/inflation";
import { cn } from "@/lib/utils";
import type { Comparison, ComparisonBucket, ComparisonMedian } from "@/types/domain";

import { buildCostChartBars, costChartAxisTop, type CostChartBar, type CostChartUnit } from "./costChartData";
import { deviationTone, type DeviationTone } from "./deviationTone";
import { BUCKET_LABELS, REASON_LABELS } from "./labels";

/**
 * Диаграмма стоимости договоров (план, задача 11; спека диаграммы стоимости
 * §2.2–§2.5, §2.8–§2.9; DoD 18–29).
 *
 * Данные и верх оси считают чистые функции `buildCostChartBars`/
 * `costChartAxisTop` (`costChartData.ts`) — этот файл только переводит их в
 * `<BarChart>` под `ChartContainer` (`@/components/ui/chart`), тем же
 * приёмом, что `StructureRing` (`src/pages/passport/StructureRing.tsx`)
 * переводит структуру стоимости в `<Pie>`. Своей SVG-геометрии здесь нет:
 * высоты столбцов, положение промежутка и координаты риски номинала считает
 * recharts по домену `[0, top]`, компонент только описывает, ЧТО показать.
 *
 * **Совмещение полос по горизонтали.** Сплошная часть (`solid`) и промежуток
 * к номиналу (`gapRange`, range-bar со значением `[from, to]`) — два `<Bar>`
 * без общего `stackId`, поэтому recharts развёл бы их по горизонтали зазором
 * `barGap` (по умолчанию 4). Здесь у обеих полос одинаковый `barSize` и у
 * графика — `barGap={-BAR_SIZE}`: суммарный сдвиг второй полосы равен нулю, то
 * есть она встаёт РОВНО поверх первой. Проверено числами в
 * `ContractCostChart.test.tsx` (совпадение `x`/`width` у `.recharts-rectangle`
 * двух серий).
 *
 * **Шкалу полотна задают ДВА пропа `<YAxis>`, и сторожит их тест как ПАРУ.**
 * `domain={[0, axis.top]}` объявляет ось от нуля до посчитанного верха,
 * `ticks={axis.ticks}` заставляет сетку встать по тем же засечкам, что рисует
 * жёлоб. Замерено: каждый из них ПО ОТДЕЛЬНОСТИ пинит шкалу сам (recharts
 * расширяет домен до переданных засечек), поэтому снятие одного теста не
 * роняет, а снятие обоих — роняет. Утверждать, что каждый проп проверен, было
 * бы неправдой; оба оставлены сознательно: `domain` не зависит от того, как
 * recharts обходится с засечками, а `ticks` нужны сетке.
 *
 * **Жёлоб оси — вне прокрутки (DoD 29).** Засечки и подписи медиан рисуются
 * ОБЫЧНОЙ вёрсткой (проценты от `axis.top`, не recharts), потому что живут
 * СНАРУЖИ горизонтально прокручиваемого контейнера — как закреплённая первая
 * колонка таблицы сравнения (спека сравнения §2.1.4). Чтобы проценты жёлоба
 * совпадали с пикселями `<BarChart>` внутри прокрутки, у графика нулевые
 * поля (`margin`) и явный домен оси Y `[0, axis.top]` — тот же ноль отступов,
 * что делает жёлоб и полотно одной линейкой.
 *
 * **Единица диаграммы — локальное состояние, не URL** (спека диаграммы
 * стоимости §2.10 её не называет в контракте адреса). Корзина, наоборот,
 * приходит пропом — своего состояния корзины у компонента нет (DoD 27).
 *
 * **Подписи — из общего модуля `labels.ts`, а не пропами.** Спека диаграммы
 * стоимости §2.9 требует те же слова о причинах неполноты, что у таблицы, и
 * `ComparePage.tsx` их экспортировать не может (`react-refresh/only-export-
 * components` даёт `error`, замерено). Первая редакция передавала словарь
 * ПРОПОМ — и тогда «один словарь на экран» держалось на том, что все
 * вызывающие передают один и тот же объект, а тест этого файла уже передавал
 * свой собственный. Общий модуль делает утверждение верным по построению; сам
 * eslint именно это и советует («use a new file to share constants»).
 *
 * **Денежная дисциплина (AGENTS.md §3, план, Global Constraint 1).** Число
 * здесь — только та геометрия, что уже перевели `buildCostChartBars`/
 * `costChartAxisTop` (`value`, `nominalValue`, `gap`, `axis.top`/`ticks`), плюс
 * `Number()` НАД МЕДИАНОЙ ровно в тех местах, где `StructureRing` берёт
 * `Number()` над деньгами для угла сектора — единственно ради позиции линии.
 * Всё показываемое — decimal-строки сервера через `formatDecimalMoney`,
 * `coefficientLevel`, `deviationTone`.
 *
 * **Медиана — две причины отсутствия линии, а не одна (DoD 22, 22г, 24).**
 * `resolveMedianState` различает их по ФОРМЕ ответа, а не по `vat_mode`:
 * поля `shown_per_sqm` НЕТ ВООБЩЕ → «своя ставка», раскладка каждого столбца
 * своим множителем; поле ЕСТЬ и равно `null` → сопоставимых меньше трёх.
 * Единица «Сумма договора» проверяется отдельно и первой: медианы на этой оси
 * не существует, даже если поле пришло (спека диаграммы стоимости §2.4, §2.8).
 * Три причины дают три РАЗНЫЕ подписи намеренно — свернуть их в одну означало
 * бы соврать о причине.
 */

const BAR_SIZE = 40;
const COLUMN_WIDTH_PX = 104;
const PLOT_HEIGHT_PX = 300;

/** Пустой конфиг — модульная константа: свежий объект менял бы identity контекста на каждом рендере. */
const CHART_CONFIG: ChartConfig = {};

const UNIT_LABELS: Record<CostChartUnit, string> = {
  sqm: "₽/м²",
  sum: "Сумма договора",
};

const DEVIATION_TONE_CLASS: Record<DeviationTone, string> = {
  flat: "text-fg-tertiary",
  "up-lo": "bg-warning-soft text-warning-text",
  "up-hi": "bg-warning-soft text-warning-text font-semibold",
  "dn-lo": "bg-accent-soft text-accent-text",
  "dn-hi": "bg-accent-soft text-accent-text font-semibold",
};

// ---------------------------------------------------------------------------
//  Медиана: сплошная линия, точечная (номинал) либо объяснение
// ---------------------------------------------------------------------------

type MedianReason = "sum-axis" | "own-rate" | "thin";

interface MedianLine {
  value: number;
  decimal: string;
}

type MedianState =
  | { kind: "line"; comparableCount: number; line: MedianLine; nominal: MedianLine | null }
  | { kind: "none"; reason: MedianReason; comparableCount: number };

/**
 * Читает ФОРМУ `totals_medians[bucket]`, а не `vat_mode` — правило присутствия
 * уже сформулировано сервером (спека диаграммы стоимости §2.8), повторять его
 * вторым `if` по режиму НДС значило бы завести вторую копию, которая однажды
 * разойдётся с первой.
 */
function resolveMedianState(median: ComparisonMedian, unit: CostChartUnit): MedianState {
  const comparableCount = median.comparable_count;

  // Единица проверяется ПЕРВОЙ и отдельно от присутствия поля (DoD 22г):
  // медианы на оси сумм не существует, даже когда `shown_per_sqm` пришло.
  if (unit === "sum") return { kind: "none", reason: "sum-axis", comparableCount };

  if (!("shown_per_sqm" in median)) return { kind: "none", reason: "own-rate", comparableCount };

  const shownPerSqm = median.shown_per_sqm ?? null;
  if (shownPerSqm === null) return { kind: "none", reason: "thin", comparableCount };

  const nominalField = median.nominal;
  const nominalShownPerSqm = nominalField?.shown_per_sqm ?? null;
  const nominal =
    nominalField !== undefined && nominalShownPerSqm !== null
      ? { value: Number(nominalShownPerSqm), decimal: nominalShownPerSqm }
      : null;

  return {
    kind: "line",
    comparableCount,
    line: { value: Number(shownPerSqm), decimal: shownPerSqm },
    nominal,
  };
}

const MEDIAN_REASON_TEXT: Record<MedianReason, (comparableCount: number) => string> = {
  "sum-axis": () =>
    "Линии медианы нет: медиана выборки существует только на оси ₽/м². Столбцы сумм сравнивают объём договора, а не уровень цены.",
  "own-rate": () =>
    "Линии медианы нет: в режиме «Своя ставка» каждый столбец поднят своим множителем, поэтому любая одна линия противоречила бы плашкам отклонений — они считаны по нетто и от режима показа не зависят. В «Единой» ставка одна на всех, и линия есть.",
  thin: (comparableCount) =>
    `Сопоставимых договоров в выборке ${comparableCount} — медианы нет: сервер считает её при трёх и более.`,
};

// ---------------------------------------------------------------------------
//  Риска номинала — decoration поверх точки, которую считает recharts
// ---------------------------------------------------------------------------

/** Короткая горизонтальная риска на уровне номинала (спека диаграммы
 *  стоимости §2.3). `cx`/`cy` —
 *  координаты, которые посчитал `<ReferenceDot>`; сама риска — decoration
 *  вокруг них, тем же приёмом, что штриховка `<Cell>` у `StructureRing`. */
function NominalTick({ cx, cy }: DotProps) {
  if (cx === undefined || cy === undefined) return <g />;
  const half = BAR_SIZE / 2 + 3;
  return (
    <line
      x1={cx - half}
      x2={cx + half}
      y1={cy}
      y2={cy}
      stroke="var(--accent-text)"
      strokeWidth={2}
    />
  );
}

// ---------------------------------------------------------------------------
//  Подпись под столбцом — вёрсткой, не через `<Label>` (три строки разного тона)
// ---------------------------------------------------------------------------

/**
 * Чип поправки под столбцом: уровень коэффициента и его НАПРАВЛЕНИЕ.
 *
 * Направление берётся точной арифметикой строк — знак `k − 1` через
 * `addDecimalStrings`, — а НЕ разбором человеческого текста `coefficientLevel`.
 * Первая редакция читала `level.text.startsWith("Снижение")`: тон чипа тогда
 * зависел от формулировки в чужом модуле и молча перевернулся бы при её
 * правке, причём ни один тест этого бы не заметил. Разбор ЧИСЛА такого
 * свойства не имеет.
 */
type CoefficientChip =
  | { kind: "mixed"; title: string }
  | { kind: "level"; level: string; down: boolean; title: string };

function resolveCoefficientChip(bar: CostChartBar): CoefficientChip | null {
  if (bar.inflationCoefficient === undefined) return null;
  if (bar.inflationCoefficient === null) {
    // Различитель — `kind`, а не слово «разные»: одно значение на два состояния
    // (`docs/insights/one-value-two-states.md`) однажды разъехалось бы с
    // подписью, и текст стал бы решать поведение.
    return {
      kind: "mixed",
      title: (bar.inflationFactors ?? [])
        .map((factor) => `${factor.label} × ${factor.coefficient}`)
        .join(" · "),
    };
  }
  const level = coefficientLevel(bar.inflationCoefficient);
  const delta = addDecimalStrings(bar.inflationCoefficient, "-1");
  return {
    kind: "level",
    level: level.level,
    down: delta !== null && delta.startsWith("-"),
    title: `множитель × ${bar.inflationCoefficient}`,
  };
}

function ColumnLabel({ bar, unit }: { bar: CostChartBar; unit: CostChartUnit }) {
  const coefficient = resolveCoefficientChip(bar);

  const deviation = bar.deviationPct === null ? null : deviationTone(bar.deviationPct);
  const reasonsText = bar.incompleteReasons.map((reason) => REASON_LABELS[reason]).join(", ");

  return (
    <div
      data-testid={`cost-chart-column-${bar.contractId}`}
      style={{ width: COLUMN_WIDTH_PX }}
      className="flex shrink-0 flex-col items-center gap-0.5 px-1 text-center"
    >
      {/*
        Значение столбца доезжает до скринридера ТЕКСТОМ. `aria-label` на голом
        `div` без роли большинство скринридеров игнорирует, а само число живёт
        только в SVG `<text>` внутри графика — то есть без этой строки его в
        доступном дереве нет вовсе.
      */}
      <span className="sr-only">
        {bar.value === null ? "нет суммы" : formatDecimalMoney(bar.shownDecimal)}
      </span>
      <span className="text-xs font-semibold text-fg">{bar.contractNumber}</span>
      <span className="text-2xs text-fg-secondary">{bar.rateClassTitle}</span>
      {coefficient && (
        <span
          data-testid={`cost-chart-coefficient-${bar.contractId}`}
          title={coefficient.title}
          className={cn(
            "rounded px-1 text-2xs",
            coefficient.kind === "level" && coefficient.down
              ? "bg-accent-soft text-accent-text"
              : coefficient.kind === "level"
                ? "bg-warning-soft text-warning-text"
                : "bg-surface-sunken text-fg-secondary"
          )}
        >
          {coefficient.kind === "mixed" ? "разные" : `поправка ${coefficient.level}`}
        </span>
      )}
      {bar.value === null ? (
        <span data-testid={`cost-chart-nodata-${bar.contractId}`} className="text-2xs text-fg-tertiary">
          {reasonsText ? `нет суммы: ${reasonsText}` : "нет суммы"}
        </span>
      ) : deviation ? (
        <span
          data-testid={`cost-chart-deviation-${bar.contractId}`}
          className={cn("rounded px-1 text-2xs tabular-nums", DEVIATION_TONE_CLASS[deviation.tone])}
        >
          {unit === "sum" ? `к медиане ₽/м² ${deviation.text}` : deviation.text}
        </span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Компонент
// ---------------------------------------------------------------------------

export function ContractCostChart({
  comparison,
  bucket,
}: {
  comparison: Comparison;
  bucket: ComparisonBucket;
}) {
  const rawId = useId().replace(/:/g, "");
  const headingId = `cost-chart-heading-${rawId}`;
  const hatchId = `cost-chart-hatch-${rawId}`;

  // Единица диаграммы — состояние КОМПОНЕНТА, не URL (спека диаграммы
  // стоимости §2.10 её не называет в контракте адреса).
  const [unit, setUnit] = useState<CostChartUnit>("sqm");

  const bars = useMemo(() => buildCostChartBars(comparison, bucket, unit), [comparison, bucket, unit]);
  const bucketMedian = comparison.totals_medians[bucket];
  // Ось считается по ТЕМ ЖЕ барам, что рисуются, — она их и получает.
  const axis = useMemo(() => costChartAxisTop(bars, bucketMedian, unit), [bars, bucketMedian, unit]);
  const median = useMemo(() => resolveMedianState(bucketMedian, unit), [bucketMedian, unit]);

  const chartData = useMemo(
    () =>
      bars.map((bar) => ({
        contractId: bar.contractId,
        solid: bar.value ?? undefined,
        gapRange: bar.gap ? ([bar.gap.from, bar.gap.to] as [number, number]) : undefined,
        shownLabel: bar.value === null ? undefined : formatDecimalMoney(bar.shownDecimal, ""),
      })),
    [bars]
  );

  const innerWidth = bars.length * COLUMN_WIDTH_PX;
  const hasNominal = bars.some((bar) => bar.nominalValue !== null);

  const pct = (value: number) => `${(value / axis.top) * 100}%`;

  return (
    <section
      aria-labelledby={headingId}
      className="mt-4 rounded-lg border border-border-subtle bg-surface p-4"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 id={headingId} className="text-sm font-semibold text-fg">
          Диаграмма стоимости — {BUCKET_LABELS[bucket]}
        </h2>
        <div
          role="group"
          aria-label="Единица диаграммы"
          className="inline-flex overflow-hidden rounded-lg border border-border"
        >
          {(Object.keys(UNIT_LABELS) as CostChartUnit[]).map((value) => (
            <Button
              key={value}
              type="button"
              variant="ghost"
              size="sm"
              aria-pressed={unit === value}
              className={cn("rounded-none", unit === value && "bg-accent-soft text-accent-text")}
              onClick={() => setUnit(value)}
            >
              {UNIT_LABELS[value]}
            </Button>
          ))}
        </div>
      </div>

      <div className="flex items-start">
        {/*
          Жёлоб оси — СНАРУЖИ прокрутки (DoD 29): засечки и подписи медиан
          остаются на месте, как закреплённая первая колонка таблицы сравнения.
          Позиции — проценты от `axis.top`, той же линейкой, что домен `<BarChart>`
          ниже (нулевые поля графика делают проценты и пиксели одной шкалой).
        */}
        <div
          data-testid="cost-chart-gutter"
          className="relative w-20 shrink-0 text-right text-2xs text-fg-tertiary"
          style={{ height: PLOT_HEIGHT_PX }}
        >
          {/*
            Засечки — ЧИСЛА (геометрия оси), поэтому и форматируются числовым
            `formatNumber`, а не денежным `formatDecimalMoney(String(tick))`:
            прежняя редакция прогоняла число через денежный форматтер строкой, и
            на мелких величинах это давало мусор — замерено, `top = 1` даёт
            засечку `0.6000000000000001`. Деньги на диаграмме приходят строками
            сервера и идут своим форматтером; шкала деньгами не является.
          */}
          {axis.ticks.map((tick) => (
            <span
              key={tick}
              className="absolute right-0 translate-y-1/2"
              style={{ bottom: pct(tick) }}
            >
              {formatNumber(tick)}
            </span>
          ))}
          {median.kind === "line" && (
            <span
              data-testid="cost-chart-median-line"
              className="absolute right-0 translate-y-1/2 rounded border border-accent-border bg-accent-soft px-1 text-accent-text"
              style={{ bottom: pct(median.line.value) }}
            >
              медиана {formatDecimalMoney(median.line.decimal, "")}
            </span>
          )}
          {median.kind === "line" && median.nominal && (
            <span
              data-testid="cost-chart-median-line-nominal"
              className="absolute right-0 translate-y-1/2 rounded border border-border-subtle bg-surface-sunken px-1 text-fg-tertiary"
              style={{ bottom: pct(median.nominal.value) }}
            >
              номинал {formatDecimalMoney(median.nominal.decimal, "")}
            </span>
          )}
        </div>

        {/*
          Полотно и подписи под столбцами листаются ОДНИМ скроллером (DoD 29):
          у столбца нижняя граница ширины (`COLUMN_WIDTH_PX`), а `.inner`
          пиксельно шире экрана, когда договоров много. Фактическую прокрутку
          jsdom не измеряет (`scrollWidth`/`clientWidth` там всегда 0) — тест
          проверяет только то, чем прокрутка ЗАПРОШЕНА; сам скролл меряет
          прогон на стенде (план, задача 14).
        */}
        <div data-testid="cost-chart-scroll" className="min-w-0 flex-1 overflow-x-auto">
          <div data-testid="cost-chart-inner" style={{ width: innerWidth, minWidth: "100%" }}>
            <ChartContainer config={CHART_CONFIG} className="aspect-auto w-full" style={{ height: PLOT_HEIGHT_PX }}>
              <BarChart
                data={chartData}
                margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
                barGap={-BAR_SIZE}
              >
                <defs>
                  {/* Штриховка промежутка «номинал ↔ приведённое» — тот же приём
                      узора, что у «Нераспределённого» в `StructureRing`. */}
                  <pattern id={hatchId} width={7} height={7} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                    <rect width={7} height={7} fill="var(--accent-soft)" />
                    <line x1={0} y1={0} x2={0} y2={7} stroke="var(--accent-border)" strokeWidth={3.4} />
                  </pattern>
                </defs>
                {/*
                  Сетка ПО ЗАСЕЧКАМ жёлоба. Она несущая, а не украшение: жёлоб
                  вынесен наружу прокрутки (DoD 29), поэтому у столбца, уехавшего
                  вправо, без линий нет вовсе ничего, с чем сличать высоту. Ticks
                  передаются явно теми же числами, что рисует жёлоб, — иначе
                  recharts выбрал бы свои и две шкалы разошлись бы.
                */}
                <CartesianGrid horizontal vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="contractId" type="category" hide />
                <YAxis domain={[0, axis.top]} ticks={axis.ticks} hide />
                <Bar
                  dataKey="solid"
                  barSize={BAR_SIZE}
                  fill="var(--accent)"
                  radius={[3, 3, 0, 0]}
                  isAnimationActive={false}
                >
                  <LabelList dataKey="shownLabel" position="top" className="fill-fg text-2xs" />
                </Bar>
                {/*
                  Промежуток «номинал ↔ приведённое» — range-bar `[from, to]`,
                  БЕЗ общего `stackId` с `solid` (стек начал бы штриховку от
                  верха предыдущего столбца, а не от нужного интервала).
                  Совмещение по горизонтали — тот же `barSize` и `barGap`
                  графика выше: сдвиг второй полосы равен нулю.
                */}
                <Bar dataKey="gapRange" barSize={BAR_SIZE} fill={`url(#${hatchId})`} isAnimationActive={false} />
                {bars.map(
                  (bar) =>
                    bar.nominalValue !== null && (
                      <ReferenceDot
                        key={bar.contractId}
                        x={bar.contractId}
                        y={bar.nominalValue}
                        r={0}
                        shape={NominalTick}
                      />
                    )
                )}
                {median.kind === "line" && (
                  <ReferenceLine y={median.line.value} stroke="var(--accent)" strokeDasharray="4 4" />
                )}
                {median.kind === "line" && median.nominal && (
                  <ReferenceLine y={median.nominal.value} stroke="var(--fg-tertiary)" strokeDasharray="1 3" />
                )}
              </BarChart>
            </ChartContainer>

            {/*
              Зазора между подписями НЕТ намеренно. recharts раздаёт категории
              РОВНО поровну по ширине полотна, поэтому подпись обязана быть
              шириной ровно в полосу категории: `innerWidth / n`, то есть
              `COLUMN_WIDTH_PX`. Любой `gap` добавляет `(n − 1) × зазор` к сумме
              ширин, полоса подписей перестаёт совпадать с полотном, и подписи
              уезжают вправо тем сильнее, чем правее столбец — при пяти
              договорах последняя уехала бы на треть столбца.
            */}
            <div className="mt-2 flex" style={{ width: innerWidth }}>
              {bars.map((bar) => (
                <ColumnLabel key={bar.contractId} bar={bar} unit={unit} />
              ))}
            </div>
          </div>
        </div>
      </div>

      {median.kind === "none" && (
        <p data-testid="cost-chart-median-note" className="mt-3 text-2xs text-fg-secondary">
          {MEDIAN_REASON_TEXT[median.reason](median.comparableCount)}
        </p>
      )}

      {hasNominal && (
        <p className="mt-2 text-2xs text-fg-tertiary">
          Промежуток к номиналу: шапка внутри столбца — рост, призрак над столбцом — снижение. Риска — цены
          подписания.
        </p>
      )}
    </section>
  );
}

import { Fragment, useId, useMemo, useState } from "react";
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

import {
  SEGMENTED_GROUP_CLASS,
  SEGMENTED_ITEM_ACTIVE_CLASS,
  SEGMENTED_ITEM_CLASS,
} from "@/components/ui-domain/controlStyles";
import { Button } from "@/components/ui/button";
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { addDecimalStrings } from "@/lib/decimal";
import { formatDate, formatDecimalMoney } from "@/lib/format";
import { coefficientLevel } from "@/lib/inflation";
import { MONTH_NAMES_RU } from "@/lib/constants";
import { cn } from "@/lib/utils";
import type { Comparison, ComparisonBucket, ComparisonMedian } from "@/types/domain";

import {
  buildCostChartBars,
  costChartAxisTop,
  costChartValueLosesDigits,
  formatCostChartValue,
  type CostChartBar,
  type CostChartUnit,
} from "./costChartData";
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
 * **Показ округляется до копеек, и это осознанное решение вызывающего.**
 * `formatDecimalMoney` без `maxFractionDigits` печатает ВСЕ знаки, и так
 * задумано: ставка `1 075,35475` обязана показываться целиком. Здесь величины
 * ВЫЧИСЛЕННЫЕ — частное на площадь и `net_to_gross`, — поэтому округление
 * законно по тому же условию, по которому его берёт таблица этого же экрана
 * (`ComparePage`, ячейки `MoneyCell … maxFractionDigits={2}`). Первая редакция
 * не просила округления, и на стенде плашка медианы печатала сто знаков:
 * настоящая медиана — периодическая дробь, а в фикстурах стояли круглые суммы.
 * Точное значение уходит в `title` плашек — и ТОЛЬКО когда округление что-то
 * изменило, как это делает `MoneyCell`. У подписи над столбцом и у текста для
 * скринридера такого маршрута нет (SVG-текст подсказки не несёт), и точное
 * число там взять негде — оно доступно в таблице ниже, на том же экране.
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

/** Образец штриховки для легенды: узор SVG в чипе не переиспользовать, поэтому CSS-градиент — тем же приёмом, что у кольца паспорта. */
const HATCH_SWATCH =
  "repeating-linear-gradient(45deg, var(--accent-primary-soft) 0 2px, var(--accent-primary-border) 2px 4px)";

/**
 * `YYYY-MM` → «август 2026», и подпись строится как «к ценам НА август 2026».
 *
 * Предлог «на» выбран не для красоты: он требует винительного падежа, а у всех
 * двенадцати названий месяцев (мужской род, неодушевлённые) винительный совпадает
 * с именительным — значит хватает общего `MONTH_NAMES_RU` и второй таблицы имён
 * заводить не надо. Макет обходился двумя жёстко прописанными строками в
 * дательном («августу 2026»), потому что месяцев в нём ровно два; настоящий экран
 * принимает любой.
 */
function formatTargetMonth(targetMonth: string): string {
  const [year, month] = targetMonth.split("-");
  const name = MONTH_NAMES_RU[Number(month) - 1];
  return name ? `${name.toLowerCase()} ${year}` : targetMonth;
}

/**
 * Ширина столбца — 78px, число из макета (`.bar { width: min(78px, 86%) }`).
 *
 * **Процентная половина правила у нас не может сработать ни разу, и поэтому не
 * написана.** У столбца есть нижняя граница слота `COLUMN_WIDTH_PX = 104`
 * (`.inner` шириной `bars.length * 104` под горизонтальной прокруткой), значит
 * категория recharts никогда не уже 104px, а `86 % × 104 = 89.4 > 78` — верх
 * всегда берёт пиксельный зажим. Писать вместо числа измеряемую долю значило бы
 * завести `ResizeObserver`, три места пересчёта (`barSize`, `barGap` и половина
 * риски номинала) и ветку, которая не исполняется.
 *
 * Прежнее значение 40px не имело обоснования и делало столбец в шесть раз уже
 * своей подписи: замерено — 40px при слоте 247.6px, то есть 16 % против
 * макетных 85 %.
 */
const BAR_SIZE = 78;
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
      stroke="var(--accent-primary-text)"
      strokeWidth={2}
    />
  );
}

// ---------------------------------------------------------------------------
//  Подпись под столбцом — вёрсткой, не через `<Label>` (три строки разного тона)
// ---------------------------------------------------------------------------

/**
 * Подсказка с точным значением — ТОЛЬКО когда округление что-то изменило.
 *
 * Тот же контракт, что у `MoneyCell` (`src/components/ui-domain/MoneyCell.tsx`),
 * и та же причина: подсказка, повторяющая видимое, ничего не сообщает и лишь
 * мешает. Первая редакция ставила её безусловно, и над «медиана 50 000,00»
 * висело «Точное значение: 50 000,00».
 */
function exactTitle(decimal: string, unit: CostChartUnit): string | undefined {
  if (!costChartValueLosesDigits(decimal, unit)) return undefined;
  return `Точное значение: ${formatDecimalMoney(decimal, "")}`;
}

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

// ---------------------------------------------------------------------------
//  Подсказка при наведении (макет, `.tip`)
// ---------------------------------------------------------------------------

/**
 * Единица в подсказке — суффиксом к числу: `formatCostChartValue` печатает
 * величину, но не говорит, в чём она. Над столбцом это не нужно — там единица
 * объявлена переключателем и засечками жёлоба, — а в подсказке рядом стоят
 * площадь в м² и процент отклонения, и безымянное число читалось бы четвёртым
 * видом величины.
 */
const UNIT_SUFFIX: Record<CostChartUnit, string> = { sqm: " ₽/м²", sum: " ₽" };

function tooltipValue(decimal: string | null, unit: CostChartUnit): string {
  const text = formatCostChartValue(decimal, unit);
  return text === null ? "—" : text + UNIT_SUFFIX[unit];
}

/**
 * Подсказка столбца — СВОЙ `content` у `<ChartTooltip>`, а не
 * `ChartTooltipContent` из `@/components/ui/chart`.
 *
 * Причина не в оформлении: готовый `ChartTooltipContent` раскладывает payload
 * ПО СЕРИЯМ — образец цвета, имя серии, значение. У нас серий две и обе
 * служебные (`solid` — приведённое, `gapRange` — промежуток к номиналу), а
 * человеку нужны не они, а факты договора: класс, номинал, коэффициент,
 * отклонение, налоговый состав, площадь. Список пар из макета (`.tip dl`) —
 * `<dl>`, и это его естественная разметка.
 *
 * **Ни одно число здесь не считается.** Всё — decimal-строки ответа через
 * `formatCostChartValue`; отклонение и уровень коэффициента берут те же
 * `deviationTone`/`coefficientLevel`, что чип под столбцом, поэтому подсказка не
 * может разойтись с подписью, которую объясняет.
 *
 * Подсказка МЫШИНАЯ, и это осознанная граница: столбцы — прямоугольники SVG
 * recharts, фокус на них не наводится. Клавиатуре и скринридеру те же числа
 * доступны иначе — `sr-only`-строкой значения в подписи столбца и полной
 * таблицей статей ниже на том же экране. Макет открывает подсказку и на
 * `focus`, потому что там колонки — `div[tabindex]`; повторять это здесь значило
 * бы завести второй, невидимый слой интерактивных узлов поверх графика.
 */
export function CostChartTooltip({
  active,
  label,
  bars,
  unit,
}: {
  active?: boolean;
  label?: string | number;
  bars: CostChartBar[];
  unit: CostChartUnit;
}) {
  if (!active) return null;
  // `label` — значение `dataKey` оси X, то есть `contractId`. Тип у recharts
  // размытый, поэтому сличение по строке, а не приведением к числу.
  const bar = bars.find((row) => String(row.contractId) === String(label));
  if (!bar) return null;

  const coefficient = resolveCoefficientChip(bar);
  const deviation = bar.deviationPct === null ? null : deviationTone(bar.deviationPct);
  // Номинал приходит ТОЛЬКО при приведении; без него приведённое и есть цены
  // подписания, и особого случая у макета тут нет.
  const nominalDecimal = bar.nominalDecimal ?? bar.shownDecimal;

  const rows: [string, string][] = [
    ["Класс объекта", bar.rateClassTitle],
    ["В ценах подписания", tooltipValue(nominalDecimal, unit)],
  ];
  if (coefficient) {
    rows.push([
      "Коэффициент",
      coefficient.kind === "mixed"
        ? `разные (${coefficient.title})`
        : /*
            Множитель ОКРУГЛЁН до четырёх знаков, и округление — строковое
            (`formatDecimalMoney` без валюты), а не через `Number()`: §3
            AGENTS.md действует и на показ. Сервер отдаёт множитель полной
            точности — замер на стенде дал
            `0.9028265360528709543841903724151045`, тридцать четыре знака,
            которые растягивали подсказку и ничего не сообщали. Точное значение
            остаётся в подсказке чипа под столбцом.
          */
          `× ${formatDecimalMoney(bar.inflationCoefficient, "", 4)} (${coefficient.level})`,
    ]);
    rows.push([
      coefficient.kind === "level" && coefficient.down ? "Приведено, снижение" : "Приведено, рост",
      tooltipValue(bar.shownDecimal, unit),
    ]);
  }
  rows.push(["Отклонение от медианы", deviation ? deviation.text : "—"]);
  rows.push(["Ставка НДС договора", bar.compositionCaption]);
  if (bar.areaTotalSp !== null) {
    // Площадь — тем же форматом, что в шапке колонки таблицы (`MoneyCell` без
    // валюты), и из той же decimal-строки: `Number()` здесь не нужен вовсе.
    rows.push(["Площадь", `${formatDecimalMoney(bar.areaTotalSp, "", 2)} м²`]);
  }

  return (
    <div
      data-testid={`cost-chart-tooltip-${bar.contractId}`}
      className="min-w-56 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-xs shadow-lg"
    >
      <div className="font-semibold text-fg">{bar.contractNumber}</div>
      <div className="mb-1.5 text-2xs text-fg-tertiary">
        {bar.objectTitle} · {bar.contractorTitle} · подписан {formatDate(bar.signedDate)}
      </div>
      <dl className="grid grid-cols-[auto_auto] justify-between gap-x-3.5 gap-y-0.5">
        {rows.map(([term, value]) => (
          <Fragment key={term}>
            <dt className="text-fg-secondary">{term}</dt>
            <dd className="m-0 text-right tabular-nums text-fg">{value}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function ColumnLabel({ bar, unit }: { bar: CostChartBar; unit: CostChartUnit }) {
  const coefficient = resolveCoefficientChip(bar);

  const deviation = bar.deviationPct === null ? null : deviationTone(bar.deviationPct);
  const reasonsText = bar.incompleteReasons.map((reason) => REASON_LABELS[reason]).join(", ");

  return (
    <div
      data-testid={`cost-chart-column-${bar.contractId}`}
      style={{ minWidth: COLUMN_WIDTH_PX }}
      className="flex flex-1 shrink-0 basis-0 flex-col items-center gap-0.5 px-1 text-center"
    >
      {/*
        Значение столбца доезжает до скринридера ТЕКСТОМ. `aria-label` на голом
        `div` без роли большинство скринридеров игнорирует, а само число живёт
        только в SVG `<text>` внутри графика — то есть без этой строки его в
        доступном дереве нет вовсе.
      */}
      <span className="sr-only">
        {bar.value === null ? "нет суммы" : formatDecimalMoney(bar.shownDecimal, "₽", 2)}
      </span>
      <span className="text-xs font-semibold text-fg">{bar.contractNumber}</span>
      {/*
        Дата подписания (макет, `.xdt`) — между номером и классом. Без неё
        горизонтальная ось диаграммы не подписана вовсе: столбцы идут ОТ СТАРЫХ
        К НОВЫМ (DoD 18), то есть ось это время, а прочесть с экрана, какое
        именно, было нельзя — порядок приходилось принимать на веру.
      */}
      <span className="text-2xs text-fg-tertiary">{formatDate(bar.signedDate)}</span>
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
          {unit === "sum" ? `к медиане ₽/м² ${deviation.text}` : `к медиане ${deviation.text}`}
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
        shownLabel: bar.value === null ? undefined : (formatCostChartValue(bar.shownDecimal, unit) ?? undefined),
      })),
    [bars, unit]
  );

  const innerWidth = bars.length * COLUMN_WIDTH_PX;
  const hasNominal = bars.some((bar) => bar.nominalValue !== null);
  /*
    Подписи легенды берутся из ОТВЕТА, а не собираются из состояния экрана: ряд
    и целевой месяц называет блок `inflation`, который приходит только при
    сосчитанном приведении, — то есть ровно тогда, когда легенда и нужна.
  */
  const adjustedCaption = comparison.inflation
    ? `приведено к ценам на ${formatTargetMonth(comparison.inflation.target_month)} по ряду «${comparison.inflation.series_name}»`
    : "приведено к ценовому уровню цели";
  /** «нетто» либо «в ставке показа N %» — тот же различитель, что у метки легенды. */
  const medianAxisSuffix =
    comparison.vat_mode === "single" && comparison.single_rate !== null
      ? ` в ставке показа ${comparison.single_rate} %`
      : " нетто";
  const medianAxisCaption =
    unit === "sqm" && comparison.vat_mode === "single" && comparison.single_rate !== null
      ? ` (в ставке показа ${comparison.single_rate} %, ₽/м²)`
      : " (нетто, ₽/м²)";

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
        {/*
          Тот же сегментный переключатель, что «Показатель» и «НДС» в панели, и
          то же оформление из `controlStyles`: своё «нажато» здесь означало бы,
          что на экране два разных языка выбора — светло-зелёный у диаграммы и
          тёмный у панели.
        */}
        <div role="group" aria-label="Единица диаграммы" className={SEGMENTED_GROUP_CLASS}>
          {(Object.keys(UNIT_LABELS) as CostChartUnit[]).map((value) => (
            <Button
              key={value}
              type="button"
              variant="ghost"
              size="sm"
              aria-pressed={unit === value}
              className={cn(SEGMENTED_ITEM_CLASS, unit === value && SEGMENTED_ITEM_ACTIVE_CLASS)}
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
            Засечки форматируются ТЕМ ЖЕ правилом, что значения столбцов
            (`formatCostChartValue`): иначе шкала и подписи над столбцами
            говорили бы в разных единицах — «34 123 456 789» на засечке против
            «34,12 млрд» над столбцом. Округление до целых рубли/миллиардов
            попутно съедает и мусор двоичного деления, из-за которого прежняя
            редакция печатала `0.6000000000000001` при `top = 1`.
          */}
          {axis.ticks.map((tick) => (
            <span
              key={tick}
              className="absolute right-0 translate-y-1/2"
              style={{ bottom: pct(tick) }}
            >
              {formatCostChartValue(String(tick), unit)}
            </span>
          ))}
          {median.kind === "line" && (
            <span
              data-testid="cost-chart-median-line"
              title={exactTitle(median.line.decimal, unit)}
              className="absolute right-0 translate-y-1/2 rounded border border-accent-border bg-accent-soft px-1 text-accent-text"
              style={{ bottom: pct(median.line.value) }}
            >
              медиана {formatCostChartValue(median.line.decimal, unit)}
            </span>
          )}
          {median.kind === "line" && median.nominal && (
            <span
              data-testid="cost-chart-median-line-nominal"
              title={exactTitle(median.nominal.decimal, unit)}
              className="absolute right-0 translate-y-1/2 rounded border border-border-subtle bg-surface-sunken px-1 text-fg-tertiary"
              style={{ bottom: pct(median.nominal.value) }}
            >
              номинал {formatCostChartValue(median.nominal.decimal, unit)}
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
          <div data-testid="cost-chart-inner" style={{ minWidth: innerWidth }}>
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
                    <rect width={7} height={7} fill="var(--accent-primary-soft)" />
                    <line x1={0} y1={0} x2={0} y2={7} stroke="var(--accent-primary-border)" strokeWidth={3.4} />
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
                {/*
                  `cursor={false}` — серую полосу под курсором recharts рисует во
                  всю высоту полотна, и она перекрывала бы штриховку промежутка и
                  риску номинала, то есть ровно то, ради чего диаграмма и
                  сделана. Наведённый столбец называет заголовок подсказки; о
                  том, почему подсветки самого столбца нет вовсе, — у `<Bar>`
                  ниже.

                  СНЯТИЕ ЭТОЙ СТРОКИ НЕ РОНЯЕТ НИ ОДНОГО ТЕСТА (замерено: 133
                  зелёных без неё), и это не пропуск, а предел прогона:
                  активность подсказки recharts решает по геометрии полотна, а в
                  jsdom ширина графика ноль. Содержимое подсказки сторожат тесты
                  `CostChartTooltip` напрямую, её появление — только замер в
                  браузере (`docs/insights/unobservable-in-the-runner.md`).
                */}
                <ChartTooltip cursor={false} content={<CostChartTooltip bars={bars} unit={unit} />} />
                {/*
                  ПОДСВЕТКИ НАВЕДЁННОГО СТОЛБЦА НЕТ, и это не пропуск.

                  Макет темнит сплошную часть (`.col:hover .seg-base`), и первая
                  редакция повторила это пропом `activeBar` — он ломает
                  двухслойное совмещение, на котором держится вся диаграмма.
                  Замерено: `activeBar` заставляет recharts разносить серию на
                  слои `recharts-active-bar`/`recharts-inactive-bar`, наведённый
                  прямоугольник переезжает в активный слой, а при уходе курсора
                  НЕ возвращается в неактивный — под `.recharts-bar` остаётся
                  семь прямоугольников из восьми, а восьмой висит снаружи и
                  закрашивает штриховку промежутка сплошным. Столбец, на который
                  навели, терял шапку «номинал ↔ приведённое» до следующего
                  перерендера. Найдено пользователем по снимку; снятие пропа
                  возвращает все восемь прямоугольников на место в обоих
                  состояниях (замер — devlog §4.17).

                  Наведённый столбец называет сама подсказка — номером договора в
                  заголовке.
                */}
                <Bar
                  dataKey="solid"
                  barSize={BAR_SIZE}
                  fill="var(--accent-primary)"
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
                  <ReferenceLine y={median.line.value} stroke="var(--accent-primary)" strokeDasharray="4 4" />
                )}
                {median.kind === "line" && median.nominal && (
                  <ReferenceLine y={median.nominal.value} stroke="var(--text-tertiary)" strokeDasharray="1 3" />
                )}
              </BarChart>
            </ChartContainer>

            {/*
              recharts раздаёт категории РОВНО поровну по ширине полотна, поэтому
              подпись обязана быть шириной в полосу категории — а полоса это
              `ширина полотна / n`, и ширина полотна НЕ равна `COLUMN_WIDTH_PX × n`:
              полотно тянется на всю доступную ширину, а пиксельная константа —
              только НИЖНЯЯ граница (спека диаграммы стоимости §2.5: «у столбца
              нижняя граница ширины»). Отсюда `w-full` у полосы и `flex-1 basis-0`
              у подписи: делят то же, что полотно, и так же поровну.

              Первая редакция утверждала здесь обратное — что ширина подписи и
              есть `COLUMN_WIDTH_PX`, — и ставила её фиксированной. На стенде
              подписи разъехались со столбцами на 645 px. Зазора между подписями
              нет по отдельной причине: любой `gap` добавляет `(n − 1) × зазор` к
              сумме ширин, и полоса перестаёт совпадать с полотном.
            */}
            <div className="mt-2 flex w-full">
              {bars.map((bar) => (
                <ColumnLabel key={bar.contractId} bar={bar} unit={unit} />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/*
        Дубль чисел медианы ТЕКСТОМ — приём согласованного макета (`medcap` его
        генератора), и он несущий, а не декоративный: жёлоб узкий, и когда
        приведённая медиана оказывается близко к номинальной, плашки в нём
        накрывают друг друга. Замерено на стенде при цели «январь 2025»: плашка
        номинала полностью закрыла плашку текущей медианы, и число, по которому
        читатель сличает столбцы, стало невидимым. Порога «насколько близко»
        здесь нет намеренно — вместо ветки, которая однажды соврёт, числа
        печатаются всегда.
      */}
      {median.kind === "line" && (
        <p data-testid="cost-chart-median-caption" className="mt-2 text-2xs text-fg-secondary">
          Медиана выборки — {formatCostChartValue(median.line.decimal, unit)} ₽/м²
          {medianAxisSuffix}
          {median.nominal
            ? `; в номинале — ${formatCostChartValue(median.nominal.decimal, unit)}`
            : ""}
        </p>
      )}

      {median.kind === "none" && (
        <p data-testid="cost-chart-median-note" className="mt-3 text-2xs text-fg-secondary">
          {MEDIAN_REASON_TEXT[median.reason](median.comparableCount)}
        </p>
      )}

      {/*
        Легенда — как в согласованном макете: у каждой метки СВОЙ образец, а не
        строка текста. Появляется только при приведении: без него на полотне нет
        ни промежутка, ни риски, и объяснять было бы нечего. Знак поправки
        легенда НЕ называет — знаки в выборке бывают разными одновременно, и его
        говорит положение промежутка плюс чип под столбцом (спека диаграммы
        стоимости §2.3, последний пункт).
      */}
      {(hasNominal || median.kind === "line") && (
        <ul data-testid="cost-chart-legend" className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
          {hasNominal && (
            <>
              <li className="flex items-center gap-1.5 text-2xs text-fg-secondary">
                <span
                  className="inline-block size-2.5 rounded-sm"
                  style={{ background: "var(--accent-primary)" }}
                />
                {adjustedCaption}
              </li>
              <li className="flex items-center gap-1.5 text-2xs text-fg-secondary">
                <span
                  className="inline-block size-2.5 rounded-sm border border-accent-border"
                  style={{ backgroundImage: HATCH_SWATCH }}
                />
                промежуток к номиналу: шапка — рост, призрак над столбцом — снижение
              </li>
              <li className="flex items-center gap-1.5 text-2xs text-fg-secondary">
                <span
                  className="inline-block h-0 w-4 border-t-2"
                  style={{ borderColor: "var(--accent-primary-text)" }}
                />
                номинал: цены подписания
              </li>
            </>
          )}
          {median.kind === "line" && (
            <li className="flex items-center gap-1.5 text-2xs text-fg-secondary">
              <span
                className="inline-block h-0 w-4 border-t-2 border-dashed"
                style={{ borderColor: "var(--accent-primary)" }}
              />
              медиана выборки{medianAxisCaption}
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

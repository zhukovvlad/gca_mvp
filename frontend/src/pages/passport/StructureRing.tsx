import { useId } from "react";
import { Cell, Pie, PieChart } from "recharts";

import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import { addDecimalStrings } from "@/lib/decimal";
import { roundDecimal } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ProjectPassport, ProjectPassportCategory } from "@/types/domain";

/**
 * Кольцо структуры стоимости по статьям классификатора (Ф6 фазы 7, спека §2.10;
 * план, задача 9).
 *
 * Строится на shadcn `components/ui/chart.tsx` поверх `recharts` — те же
 * примитивы, что и везде в проекте: секторы своей SVG-геометрией не считаем,
 * это работа `<Pie>`. Референс раскладки — `buildRing()` в
 * `docs/superpowers/specs/2026-08-09-project-passport-mockup.html`, одобренный
 * на гейте 1; файл там форма, а не код для копирования.
 *
 * **Композиция (правило 1 §2.10).** Топ-8 статей ВЕРХНЕГО уровня
 * (`parent_id === null`) по сумме, дальше свёрнутая «Остальные статьи (N)»,
 * дальше «Нераспределённое». Статья с неизвестной суммой (`total === null`) в
 * кольцо не попадает вовсе и в «Остальные» не входит — свернуть неизвестное
 * слагаемое в «Остальные» молча превратило бы его в ноль (правило 2 §2.10).
 *
 * **Пустое состояние (правило 6 §2.10).** Доля сектора — это деление на
 * `totals.amount`; когда оно `null` либо ровно ноль, кольцо не строится
 * ВООБЩЕ — ни одного деления, вместо диаграммы и легенды одна строка. Два
 * разных факта о данных получают ДВЕ разные формулировки (тот же принцип, что
 * у `PassportHeader`/`CategoryTable`): «сумма не определена» — известны не все
 * слагаемые; «сумма равна нулю» — известны все, и это ноль. Смешивать их
 * нельзя — это два разных факта о смете.
 *
 * **Денежная дисциплина (AGENTS.md §3).** Сортировка, решающая, ЧТО войдёт в
 * топ-8, идёт по decimal-строкам через {@link compareDecimalStrings} —
 * `Number()` здесь запрещён так же, как и в форматировании: он решает не КАК
 * показано, а ЧТО показано, и на суммах ГП, не влезающих в double без потерь,
 * ошибка была бы в последнем разряде и невидимой. Единственное необходимое
 * исключение — `geometryValue` у {@link RingSlice}: recharts считает углы
 * секторов в пикселях и требует `number`. Ничего ПОКАЗЫВАЕМОГО из этого поля
 * не берётся: проценты — из `share_pct` сервера (или их точной суммы
 * decimal-строками через `addDecimalStrings`), деньги — из `total`/`amount`
 * строк сервера.
 *
 * **«Нераспределённое» дополнительно заштриховано** (правило §2.10) — цвет не
 * единственный носитель смысла (доступность), и это же снимает соседство с
 * жёлтым слотом палитры. Штриховка сектора — SVG `<pattern>` в `<defs>` самого
 * графика; штриховка чипа легенды — CSS `repeating-linear-gradient`, тем же
 * приёмом, что в одобренном мокапе (сектор и чип рисуются в разных технологиях,
 * узор — тот же).
 *
 * **Граница наблюдаемости.** В jsdom `recharts` не даёт реальных размеров
 * контейнера, поэтому тесты этого экрана утверждают ЛЕГЕНДУ, ТЕКСТЫ и пустые
 * состояния — никогда геометрию секторов. Геометрию и палитру в обеих темах
 * проверяет замер в браузере (план, задача 12); здесь это названо, а не
 * замолчано.
 */

const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Сравнивает две decimal-строки денег БЕЗ приведения к `number` (см. докстроку
 * файла): результат сравнения решает, какие статьи войдут в топ-8 — то есть
 * ЧТО показывается, а не только как оно выглядит.
 *
 * Порядок сравнения: знак → число цифр целой части → сами цифры целой части
 * слева направо → дробная часть слева направо (более короткая дополняется
 * нулями справа). Строки одного знака с одинаковым числом цифр целой части
 * сравниваются лексикографически — для цифровых строк равной длины это и есть
 * числовое сравнение.
 *
 * @returns отрицательное, если `a < b`; положительное, если `a > b`; ноль при
 *   равенстве либо если один из аргументов — не decimal-строка (сюда не должны
 *   попадать значения, не прошедшие фильтр {@link hasKnownTotal}).
 */
function compareDecimalStrings(a: string, b: string): number {
  const ma = DECIMAL_RE.exec(a.trim());
  const mb = DECIMAL_RE.exec(b.trim());
  if (!ma || !mb) return 0;

  const negA = ma[1] === "-";
  const negB = mb[1] === "-";
  if (negA !== negB) return negA ? -1 : 1;

  const wholeA = ma[2].replace(/^0+(?=\d)/, "");
  const wholeB = mb[2].replace(/^0+(?=\d)/, "");

  let cmp: number;
  if (wholeA.length !== wholeB.length) {
    cmp = wholeA.length - wholeB.length;
  } else if (wholeA !== wholeB) {
    cmp = wholeA < wholeB ? -1 : 1;
  } else {
    const fracLen = Math.max((ma[3] ?? "").length, (mb[3] ?? "").length);
    const fracA = (ma[3] ?? "").padEnd(fracLen, "0");
    const fracB = (mb[3] ?? "").padEnd(fracLen, "0");
    cmp = fracA === fracB ? 0 : fracA < fracB ? -1 : 1;
  }

  return negA ? -cmp : cmp;
}

/** Ноль ли decimal-строка — те же знаки, что у `CategoryTable.isZeroDecimal` и
 *  `PassportHeader.isZeroDecimal`: свой маленький экземпляр здесь же и по той
 *  же причине (см. комментарий у `CategoryTable`) — общий модуль ради трёх строк
 *  стоит явности на месте использования дороже, чем экономит. */
function isZeroDecimal(value: string): boolean {
  return /^-?0+(\.0+)?$/.test(value.trim());
}

/**
 * Доля в процентах с точностью 0,01 — той же целочисленной арифметикой
 * (`roundDecimal`), что и `CategoryTable.formatSharePct`. В отличие от неё,
 * `null` здесь означает «процент не показываем вовсе», а не печатаем прочерк:
 * легенде кольца нечего противопоставить плейсхолдеру (правило 5 §2.10 прямо
 * разрешает опустить процент, если его сумма неудобна, — но не получить его
 * через `Number()`).
 */
function formatShareText(value: string | null): string | null {
  if (value === null) return null;
  return `${roundDecimal(value, 2).replace(".", ",")} %`;
}

function hasKnownTotal(
  category: ProjectPassportCategory
): category is ProjectPassportCategory & { total: string } {
  return category.total !== null;
}

interface RingSlice {
  key: string;
  /** `null` — у «Остальные…» и «Нераспределённого» кода классификатора нет. */
  code: string | null;
  title: string;
  shareText: string | null;
  fill: string;
  isUnalloc?: boolean;
  /** Единственная точка перевода decimal-строки денег в `number` во всём
   *  компоненте (см. докстроку файла) — только ради угла сектора в recharts. */
  geometryValue: number;
}

/** Собирает срезы кольца: топ-8 известных корней, свёрнутый остаток,
 *  «Нераспределённое» — правила 1-2 §2.10. Вызывается только когда
 *  `totals.amount` уже проверен как известный и ненулевой (см. `StructureRing`). */
function buildSlices(passport: ProjectPassport): RingSlice[] {
  const { categories, unallocated } = passport;

  const roots = categories.filter((c) => c.parent_id === null);
  // Статья с неизвестной суммой не идёт дальше этой точки — ни в топ-8, ни в
  // «Остальные» (правило 2 §2.10): свернуть неизвестное молча значило бы
  // притвориться, что оно — ноль.
  const known = roots.filter(hasKnownTotal);
  const sorted = [...known].sort((a, b) => compareDecimalStrings(b.total, a.total));

  const top = sorted.slice(0, 8);
  const rest = sorted.slice(8);

  const slices: RingSlice[] = top.map((category, index) => ({
    key: `cat-${category.id}`,
    code: category.code,
    title: category.title,
    shareText: formatShareText(category.share_pct),
    fill: `var(--chart-${index + 1})`,
    geometryValue: Number(category.total),
  }));

  if (rest.length > 0) {
    // Сумма долей «Остальных» — точными decimal-строками (правило 5 §2.10): как
    // только у одного из свёрнутых статей доля неизвестна, показывать частичную
    // сумму нельзя — это была бы обманчивая цифра, а не честный пропуск.
    const restShare = rest.reduce<string | null>((sum, category) => {
      if (sum === null || category.share_pct === null) return null;
      return addDecimalStrings(sum, category.share_pct);
    }, "0");
    // Деньги для геометрии сектора складываются отдельно от долей и не влияют
    // ни на что показываемое — см. докстроку файла про `geometryValue`.
    const restAmount = rest.reduce(
      (sum, category) => addDecimalStrings(sum, category.total) ?? sum,
      "0"
    );

    slices.push({
      key: "rest",
      code: null,
      title: `Остальные статьи (${rest.length})`,
      shareText: restShare === null ? null : formatShareText(restShare),
      fill: "var(--chart-rest)",
      geometryValue: Number(restAmount),
    });
  }

  if (unallocated.amount !== null) {
    slices.push({
      key: "unalloc",
      code: null,
      title: "Нераспределённое",
      shareText: formatShareText(unallocated.share_pct),
      // Флэт-цвет — запасной вариант (легенда и сектор ниже сами подставляют
      // штриховку по `isUnalloc`; см. докстроку файла про два разных приёма).
      fill: "var(--warning)",
      isUnalloc: true,
      geometryValue: Number(unallocated.amount),
    });
  }

  return slices;
}

function EmptyRingSection({ headingId, message }: { headingId: string; message: string }) {
  return (
    <section
      aria-labelledby={headingId}
      className="border border-border-subtle bg-surface px-6 py-4"
    >
      <h2 id={headingId} className="sr-only">
        Структура стоимости по статьям
      </h2>
      <p className="text-sm text-fg-secondary">{message}</p>
    </section>
  );
}

export function StructureRing({ passport }: { passport: ProjectPassport }) {
  const rawId = useId().replace(/:/g, "");
  const headingId = `structure-ring-heading-${rawId}`;
  const hatchId = `structure-ring-hatch-${rawId}`;

  const { totals } = passport;

  // Пустое состояние — правило 6 §2.10: ДО первого деления, не после него.
  if (totals.amount === null) {
    return (
      <EmptyRingSection
        headingId={headingId}
        message="структура не строится: сумма по смете не определена"
      />
    );
  }
  if (isZeroDecimal(totals.amount)) {
    return (
      <EmptyRingSection
        headingId={headingId}
        message="структура не строится: сумма по смете равна нулю"
      />
    );
  }

  const slices = buildSlices(passport);
  const chartConfig: ChartConfig = Object.fromEntries(
    slices.map((slice): [string, { label: string }] => [slice.key, { label: slice.title }])
  );

  return (
    <section aria-labelledby={headingId} className="border border-border-subtle bg-surface">
      <h2 id={headingId} className="sr-only">
        Структура стоимости по статьям
      </h2>
      <div className="flex flex-wrap items-center gap-6 px-6 py-4">
        <ChartContainer
          config={chartConfig}
          className="aspect-square h-[200px] w-[200px] shrink-0"
        >
          <PieChart>
            <defs>
              {/* Штриховка сектора «Нераспределённого» — тот же узор, что в
                  одобренном мокапе (`buildRing()`): цвет не единственный
                  носитель смысла, и заодно снимается соседство с жёлтым слотом
                  палитры (правило §2.10). */}
              <pattern
                id={hatchId}
                width={7}
                height={7}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width={7} height={7} fill="var(--warning-soft)" />
                <line x1={0} y1={0} x2={0} y2={7} stroke="var(--warning)" strokeWidth={3.4} />
              </pattern>
            </defs>
            <Pie
              data={slices}
              dataKey="geometryValue"
              nameKey="key"
              innerRadius="60%"
              outerRadius="88%"
              paddingAngle={1}
              stroke="var(--bg-surface)"
              strokeWidth={1}
              isAnimationActive={false}
            >
              {slices.map((slice) => (
                <Cell key={slice.key} fill={slice.isUnalloc ? `url(#${hatchId})` : slice.fill} />
              ))}
            </Pie>
          </PieChart>
        </ChartContainer>

        <ul
          data-testid="structure-ring-legend"
          className="grid min-w-0 flex-1 gap-x-6 gap-y-1 sm:grid-cols-2"
        >
          {slices.map((slice) => (
            <li key={slice.key} className="flex items-baseline gap-1.5 text-xs">
              <span
                aria-hidden="true"
                className={cn(
                  "size-2.5 shrink-0 self-center rounded-[2px]",
                  slice.isUnalloc && "outline outline-1 -outline-offset-1 outline-warning-border"
                )}
                style={
                  slice.isUnalloc
                    ? {
                        backgroundImage:
                          "repeating-linear-gradient(45deg, var(--warning) 0 2px, var(--warning-soft) 2px 4px)",
                      }
                    : { backgroundColor: slice.fill }
                }
              />
              {slice.code && <span className="font-mono text-fg-secondary">{slice.code} ·</span>}
              <span
                className={cn(
                  "truncate",
                  slice.isUnalloc ? "font-semibold text-warning-text" : "text-fg"
                )}
              >
                {slice.title}
              </span>
              {slice.shareText && (
                <span className="ml-auto font-mono text-fg-secondary tabular-nums">
                  {slice.shareText}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

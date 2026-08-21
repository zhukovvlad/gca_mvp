import type {
  Comparison,
  ComparisonBucket,
  ComparisonCellState,
  ComparisonIncompleteReason,
  ComparisonInflationFactor,
  ComparisonMedian,
} from "@/types/domain";

/**
 * Диаграмма стоимости договоров — чистые функции данных и оси (план,
 * задача 11; в задаче 11 их две: этот файл и, вторым коммитом, сам
 * компонент). Никакого React, DOM и запросов здесь нет — `Comparison` уже
 * прочитан и подан целиком, функции только выбирают форму и переводят
 * геометрию в `number`.
 *
 * **Почему отдельно от компонента.** DoD 19–21 и 28–28а (спека диаграммы
 * стоимости) требуют проверки утверждением о числе, а не картинкой: через
 * отрисованный `<BarChart>` эти пункты пришлось бы читать разбором SVG-путей.
 * Чистая функция считается один раз и проверяется числом напрямую (план,
 * раздел «Архитектура», подраздел «Диаграмма: recharts под shadcn»).
 *
 * **Денежная дисциплина (AGENTS.md §3, план, Global Constraint 1).** Ни одна
 * денежная строка здесь не складывается и не умножается. Единственное
 * допущенное действие — перевод decimal-строки в `number` РОВНО там, где
 * число нужно recharts/алгоритму округления оси как геометрия: высота
 * столбца, уровень риски, границы промежутка, верх и засечки оси. То же
 * единственное исключение, что у `geometryValue` в `StructureRing`
 * (`src/pages/passport/StructureRing.tsx`) — показываемые величины остаются
 * строками сервера (`shownDecimal`, `nominalDecimal`, `deviationPct`,
 * `inflationCoefficient`), в `number` уходит только то, из чего считается
 * пиксель.
 *
 * **Правило присутствия номинала (спека диаграммы стоимости §2.8, §2.9).**
 * Ключ `nominal` у ячейки бакета — признак СОСЧИТАННОГО приведения: его нет
 * ВОВСЕ без приведения, а не `null`. Поэтому промежуток «номинал ↔
 * приведённое» и риска номинала у столбца присутствуют тогда и только тогда,
 * когда `nominal` пришёл — без особого случая на равенство значений (спека
 * диаграммы стоимости §2.3: риска рисуется всегда при приведении, а у
 * промежутка нет порога минимальной высоты).
 */

/** Единица диаграммы (спека диаграммы стоимости §2.2): «₽/м²» либо сумма договора. */
export type CostChartUnit = "sqm" | "sum";

/**
 * Промежуток «номинал ↔ приведённое» одного столбца (спека диаграммы
 * стоимости §2.3, DoD 20). `from`/`to` — уже геометрия (`number`), а не
 * деньги: `from = min(номинал, приведённое)`, `to = max(...)`.
 *
 * Признака направления здесь НЕТ намеренно. Спека диаграммы стоимости §2.3
 * требует ОДНОЙ штриховки, знак которой читается ПОЛОЖЕНИЕМ: при росте
 * интервал лежит внутри столбца, при снижении — над ним, и то и другое
 * выражено самими `from`/`to`. Первая редакция несла `isIncrease`, которого не
 * читал никто, кроме тестов на него же, — а докстрока объясняла им механизм,
 * которого нет (`docs/insights/verifying-guards.md`, слой 10).
 *
 * Присутствует ТОЛЬКО когда у ячейки есть `nominal` — то есть приведение
 * сосчитано. Присутствует и при равных значениях (`from === to`): риска
 * номинала рисуется без особого случая, порога минимальной высоты нет.
 * Прямоугольника нулевой высоты recharts при этом не рисует вовсе — проверено.
 */
export interface CostChartGap {
  from: number;
  to: number;
}

/**
 * Один столбец диаграммы — договор выборки в выбранной корзине и единице
 * (спека диаграммы стоимости §2.2, §2.9). Форма несёт всё, что подпишет
 * компонент, БЕЗ единого собственного вычисления с его стороны — величины
 * взяты из ответа, а не пересчитаны (план, задача 11).
 */
export interface CostChartBar {
  contractId: number;
  contractNumber: string;
  objectTitle: string;
  rateClassId: number;
  rateClassTitle: string;

  /**
   * Геометрия верха сплошной части — `Number(shownDecimal)`, `null`, когда
   * `shownDecimal` не пришёл (договор без суммы, спека диаграммы стоимости
   * §2.9). Верх сплошного ВСЕГДА равен приведённому значению (DoD 19); когда
   * приведения нет, приведённое и есть номинал — особого случая нет.
   */
  value: number | null;
  /** То же значение decimal-строкой сервера — подпись у верха сплошной части (DoD 21). */
  shownDecimal: string | null;

  /** Геометрия уровня риски номинала. `null`, если приведения нет. */
  nominalValue: number | null;
  /** Номинал decimal-строкой — для подписи, если компонент захочет её показать. */
  nominalDecimal: string | null;

  gap: CostChartGap | null;

  /** Отклонение ЭТОЙ ячейки (той же корзины) от медианы — decimal-строка сервера, не пересчитывается. */
  deviationPct: string | null;

  /**
   * Множитель приведения КОЛОНКИ (не ячейки): `undefined` — приведения нет;
   * `null` — сметы договора приведены РАЗНЫМИ коэффициентами (см.
   * `inflationFactors`); decimal-строка — единый коэффициент (спека
   * диаграммы стоимости §2.8, `ComparisonColumn.inflation_coefficient`).
   */
  inflationCoefficient: string | null | undefined;
  /** Разбивка по сметам, когда `inflationCoefficient === null`. */
  inflationFactors: ComparisonInflationFactor[] | undefined;

  /** Состояние ячейки сервера (спека сравнения §2.1.2) — «absent» отличимо от «есть, но погашено причинами». */
  state: ComparisonCellState;
  /** Причины неполноты (спека сравнения §2.1.3) — источник подписи «нет суммы: …» (спека диаграммы стоимости §2.9). */
  incompleteReasons: ComparisonIncompleteReason[];
}

/** Верх и засечки оси (спека диаграммы стоимости §2.2, §6; DoD 28, 28а). */
export interface CostChartAxisTop {
  top: number;
  /** По возрастанию, начиная с `0`; `top` входит, когда пул непуст. */
  ticks: number[];
}

/**
 * Столбцы диаграммы для одной корзины и единицы (спека диаграммы стоимости
 * §2.2, §2.9; спека сравнения §2.2 для самих корзин).
 *
 * **Порядок — от старых договоров к новым (DoD 18).** `comparison.columns`
 * приходит `signed_date DESC, id DESC` (спека сравнения §2.1) — тот же
 * порядок, что у колонок таблицы; диаграмма разворачивает его САМА, а не
 * ждёт разворота от сервера, ровно как это делает генератор макета
 * (`for c in reversed(base["columns"])`, блок сборки колонок): таблица
 * читает договоры справа хронологически, а горизонтальная ось диаграммы —
 * время, и время читается слева направо.
 *
 * **Договор без суммы остаётся в ряду (спека диаграммы стоимости §2.9).**
 * Ячейка сопоставляется по `contract_id`, а не по позиции — `totals`
 * гарантированно несёт ровно одну ячейку на каждую колонку (то же
 * соответствие использует таблица). Если `shown`/`shown_per_sqm` этой ячейки
 * `null`, столбца нет (`value: null`), но бар в массиве остаётся — место и
 * причина (`incompleteReasons`) сохраняются, выборка не худеет молча.
 */
export function buildCostChartBars(
  comparison: Comparison,
  bucket: ComparisonBucket,
  unit: CostChartUnit
): CostChartBar[] {
  const totalsByContract = new Map(comparison.totals.map((cell) => [cell.contract_id, cell]));

  // DoD 18: разворот `signed_date DESC, id DESC` → от старых к новым, тем же
  // приёмом, что генератор макета применяет к своим колонкам.
  const orderedColumns = [...comparison.columns].reverse();

  return orderedColumns.map((column) => {
    const cell = totalsByContract.get(column.contract_id);
    if (!cell) {
      // Недостижимо по контракту `Comparison`: `totals` несёт ровно одну
      // ячейку на каждую колонку выборки (спека сравнения §2.2). Громкий
      // отказ — а не молчаливый пропуск столбца, который читатель принял бы
      // за «в выборке четыре договора», хотя их пять.
      throw new Error(
        `costChartData: totals не несёт ячейку для колонки ${column.contract_id}`
      );
    }
    const bucketCell = cell[bucket];

    const shownDecimal = unit === "sqm" ? bucketCell.shown_per_sqm : bucketCell.shown;
    const nominalDecimal =
      (unit === "sqm" ? bucketCell.nominal?.shown_per_sqm : bucketCell.nominal?.shown) ?? null;

    const value = shownDecimal === null ? null : Number(shownDecimal);
    // Номинал существует как геометрия ТОЛЬКО когда ключ `nominal` пришёл
    // (сосчитанное приведение) и у него самого есть значение — параллельно
    // ячейке, а не отдельным решением (спека диаграммы стоимости §2.8).
    const nominalValue =
      bucketCell.nominal !== undefined && nominalDecimal !== null ? Number(nominalDecimal) : null;

    return {
      contractId: column.contract_id,
      contractNumber: column.contract_number,
      objectTitle: column.object_title,
      rateClassId: column.rate_class_id,
      rateClassTitle: column.rate_class_title,
      value,
      shownDecimal,
      nominalValue,
      nominalDecimal,
      gap: buildGap(value, nominalValue),
      deviationPct: bucketCell.deviation_pct,
      inflationCoefficient: column.inflation_coefficient,
      inflationFactors: column.inflation_factors,
      state: bucketCell.state,
      incompleteReasons: bucketCell.incomplete_reasons,
    };
  });
}

/**
 * Промежуток «номинал ↔ приведённое» (DoD 20) — `null`, если хоть одно из
 * значений отсутствует: нет столбца — нечего противопоставлять номиналу
 * (договор без суммы, спека диаграммы стоимости §2.9), нет номинала —
 * приведения не было. Порога минимальной высоты нет: `from`/`to` не
 * подгоняются, даже если равны (спека диаграммы стоимости §2.3).
 */
function buildGap(value: number | null, nominalValue: number | null): CostChartGap | null {
  if (value === null || nominalValue === null) return null;
  return { from: Math.min(value, nominalValue), to: Math.max(value, nominalValue) };
}

/**
 * Верх оси для той же корзины и единицы (спека диаграммы стоимости §2.2,
 * §6; DoD 28, 28а).
 *
 * **Ось строится по ПРИМЕНЁННОМУ состоянию, а не по двум состояниям сразу
 * (DoD 28а).** Функция не принимает «номинал» и «приведено» отдельно — она
 * читает ровно тот `Comparison`, который сейчас на экране: без приведения
 * `nominal` в ответе нет, и пул состоит из показанных значений; с
 * приведением пул вмещает и приведённые столбцы, и номинал (риски,
 * призраки), и обе медианы. Выбор ряда сам по себе не меняет ось — он не
 * запускает пересчёт `Comparison`; ось шевелится только когда компонент
 * получил НОВЫЙ ответ после «Привести».
 *
 * **Функция принимает УЖЕ ПОСЧИТАННЫЕ бары, а не `Comparison`.** Пул тогда
 * содержит ровно то, что нарисовано ИМЕННО ЭТИМИ барами, — по построению, а не
 * по дисциплине вызывающего. Первая редакция считала бары внутри себя из
 * `(comparison, bucket, unit)`: утверждение «пул это то, что нарисовано»
 * держалось там на том, что вызывающий передал в обе функции одну и ту же
 * тройку, — тот же класс, что словарь подписей, переданный пропом. Заодно
 * пропал второй проход по колонкам.
 *
 * **Медиана входит в пул ТОЛЬКО на оси ₽/м² (`unit === "sqm"`)** — на оси
 * сумм медианы не существует вовсе (спека диаграммы стоимости §2.4).
 * Присутствие поля `shown_per_sqm` у `totals_medians[bucket]` само решает,
 * допускает ли режим НДС эту ось (`net`/`single` — да, `own` — нет,
 * спека диаграммы стоимости §2.8, §2.10): вторая проверка `vat_mode` здесь
 * не нужна, она была бы второй копией того же правила. Присутствие
 * `median.nominal.shown_per_sqm` добавляет в пул НОМИНАЛЬНУЮ медиану ровно
 * когда она есть — точечная линия «номинал» диаграммы (DoD 23).
 */
export function costChartAxisTop(
  bars: CostChartBar[],
  median: ComparisonMedian,
  unit: CostChartUnit
): CostChartAxisTop {
  const pool: number[] = [];
  for (const bar of bars) {
    if (bar.value !== null) pool.push(bar.value);
    if (bar.nominalValue !== null) pool.push(bar.nominalValue);
  }

  if (unit === "sqm") {
    if (median.shown_per_sqm != null) pool.push(Number(median.shown_per_sqm));
    if (median.nominal?.shown_per_sqm != null) pool.push(Number(median.nominal.shown_per_sqm));
  }

  return niceAxisTop(pool);
}

/** «Круглые» шаги, испытанные по порядку от самого мелкого (генератор макета, `nice_axis`). */
const NICE_MULTIPLIERS = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];

/**
 * Порт `nice_axis` из генератора макета
 * (`docs/superpowers/specs/2026-08-19-comparison-cost-chart-mockup.gen.py`) —
 * тот же алгоритм: число интервалов подбирается от 4 до 6, шаг остаётся
 * «круглым» (1/2/2,5/5 × степень десяти).
 *
 * **Про потолок.** Генератор нарочно вычисляет число интервалов явным
 * сравнением (`n = floor(vmax/step)`, затем `+1`, если `step*n < vmax`), а
 * не выражением вида `-(-vmax // step)`: у `Decimal` оператор `//` усекает
 * частное К НУЛЮ, а не вниз, и на ОТРИЦАТЕЛЬНОМ делимом (`-vmax`) это не
 * совпадает с округлением вниз — на 205 879 при шаге 50 000 такое выражение
 * давало 4 интервала вместо 5, верх оси оказывался НИЖЕ самого высокого
 * столбца. Здесь ниже — то же явное сравнение, скопированное буквально, а не
 * `Math.ceil(vmax / step)`: с положительными `number` эти две формы совпадают
 * (`Math.floor` и усечение к нулю для положительных чисел — одно и то же, и
 * грабля в JS не воспроизводима), но у явной формы то преимущество, что порт
 * читается рядом с оригиналом строка к строке, и следующая правка алгоритма
 * не обязана помнить, почему одна версия безопасна, а другая нет.
 *
 * Защита `assert max(pool) <= t` генератора здесь — брошенная ошибка: пустой
 * пул не может нарушить включение (в диаграмме тогда просто нечего рисовать).
 */
function niceAxisTop(pool: number[]): CostChartAxisTop {
  if (pool.length === 0) return { top: 1, ticks: [0] };

  const vmax = Math.max(...pool);
  if (vmax <= 0) return { top: 1, ticks: [0] };

  const magnitude = 10 ** Math.floor(Math.log10(vmax)) / 100;

  for (const multiplier of NICE_MULTIPLIERS) {
    const step = magnitude * multiplier;
    let n = Math.floor(vmax / step);
    if (step * n < vmax) n += 1;
    if (n >= 4 && n <= 6) {
      const top = step * n;
      assertPoolFits(pool, vmax, top);
      return { top, ticks: Array.from({ length: n + 1 }, (_, i) => step * i) };
    }
  }

  assertPoolFits(pool, vmax, vmax);
  return { top: vmax, ticks: [0, vmax] };
}

/** Соответствие защите `assert max(pool) <= t` генератора макета — громко, а не молча. */
function assertPoolFits(pool: number[], vmax: number, top: number): void {
  if (vmax > top) {
    throw new Error(
      `costChartData: верх оси ${top} ниже максимума ${vmax} из пула [${pool.join(", ")}]`
    );
  }
}

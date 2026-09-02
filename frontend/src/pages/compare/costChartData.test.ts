/**
 * @vitest-environment node
 *
 * Тесты чистых функций: DOM здесь не наблюдается ни разу, а jsdom стоит около
 * секунды на файл. Выигрыш — на ТОЧЕЧНОМ прогоне (эти девять файлов: 9,1 с →
 * 3,8 с), а НЕ на полном наборе: там окружения поднимаются параллельно, и
 * 42,3 с накопленного `environment` уходят в тень тяжёлых компонентных файлов
 * (замер 2026-09-02: 98,6 с до, 100,2 с после — в пределах разброса). Признак
 * «нужен ли jsdom» объявляется ФАЙЛОМ, а не глобом в конфиге: глоб пришлось бы
 * держать в синхронизации с деревом, и молчаливое возвращение файла в jsdom
 * заметить было бы нечем.
 */
import { describe, expect, it } from "vitest";

import { buildCostChartBars, costChartAxisTop } from "./costChartData";
import type {
  Comparison,
  ComparisonBucket,
  ComparisonBucketCell,
  ComparisonCell,
  ComparisonCellState,
  ComparisonColumn,
  ComparisonIncompleteReason,
  ComparisonMedian,
  ComparisonVatMode,
} from "@/types/domain";

/*
 * Диаграмма стоимости договоров — числовые тесты `buildCostChartBars` и
 * `costChartAxisTop` (план, задача 11; спека диаграммы стоимости §2.2–§2.9,
 * §6). Фикстуры собираются здесь маленькими хелперами — `src/test/fixtures.ts`
 * не трогается, а колонки/ячейки существующей фикстуры сравнения устроены
 * для таблицы, а не для геометрии диаграммы.
 */

// ---------------------------------------------------------------------------
//  Маленькие хелперы фикстуры — только то, что читают проверяемые функции
// ---------------------------------------------------------------------------

function makeColumn(params: {
  contractId: number;
  signedDate: string;
  rateClassId?: number;
  inflationCoefficient?: string | null;
}): ComparisonColumn {
  return {
    contract_id: params.contractId,
    contract_number: `Д-${params.contractId}`,
    object_title: `Объект ${params.contractId}`,
    contractor_title: `Подрядчик ${params.contractId}`,
    rate_class_id: params.rateClassId ?? 1,
    rate_class_title: "бизнес",
    signed_date: params.signedDate,
    area_total_sp: null,
    advance_pct: null,
    bank_guarantee_pct: null,
    retention_pct: null,
    composition_caption: "20 %",
    inflation_coefficient: params.inflationCoefficient,
  };
}

/** Ячейка одной корзины: сплошная часть, номинал (когда приведение есть) и причины неполноты. */
function makeBucketCell(params: {
  shown: string | null;
  shownPerSqm: string | null;
  state?: ComparisonCellState;
  deviationPct?: string | null;
  reasons?: ComparisonIncompleteReason[];
  nominalShown?: string | null;
  nominalShownPerSqm?: string | null;
}): ComparisonBucketCell {
  const hasNominal = params.nominalShown !== undefined || params.nominalShownPerSqm !== undefined;
  return {
    net: params.shown,
    shown: params.shown,
    net_per_sqm: params.shownPerSqm,
    shown_per_sqm: params.shownPerSqm,
    state: params.state ?? "value",
    deviation_pct: params.deviationPct ?? null,
    incomplete_reasons: params.reasons ?? [],
    ...(hasNominal
      ? {
          nominal: {
            net: params.nominalShown ?? null,
            shown: params.nominalShown ?? null,
            net_per_sqm: params.nominalShownPerSqm ?? null,
            shown_per_sqm: params.nominalShownPerSqm ?? null,
          },
        }
      : {}),
  };
}

/** Ячейка «Итого» одного договора — три корзины разом; по умолчанию все три равны (допсоглашений нет). */
function makeTotalsCell(
  contractId: number,
  total: ComparisonBucketCell,
  overrides: Partial<Record<ComparisonBucket, ComparisonBucketCell>> = {}
): ComparisonCell {
  return {
    contract_id: contractId,
    base: overrides.base ?? total,
    amendments: overrides.amendments ?? makeBucketCell({ shown: null, shownPerSqm: null, state: "absent" }),
    total: overrides.total ?? total,
  };
}

function makeMedian(params: {
  value: string | null;
  comparableCount?: number;
  contractIds?: number[];
  shownPerSqm?: string | null;
  nominalValue?: string | null;
  nominalShownPerSqm?: string | null;
}): ComparisonMedian {
  const median: ComparisonMedian = {
    value: params.value,
    comparable_count: params.comparableCount ?? 3,
    contract_ids: params.contractIds ?? [],
  };
  if (params.shownPerSqm !== undefined) median.shown_per_sqm = params.shownPerSqm;
  if (params.nominalValue !== undefined) {
    median.nominal = { value: params.nominalValue };
    if (params.nominalShownPerSqm !== undefined) {
      median.nominal.shown_per_sqm = params.nominalShownPerSqm;
    }
  }
  return median;
}

const EMPTY_MEDIAN: ComparisonMedian = {
  value: null,
  comparable_count: 0,
  contract_ids: [],
  shown_per_sqm: null,
};

function makeComparison(params: {
  columns: ComparisonColumn[];
  totals: ComparisonCell[];
  totalMedian?: ComparisonMedian;
  baseMedian?: ComparisonMedian;
  vatMode?: ComparisonVatMode;
}): Comparison {
  return {
    vat_mode: params.vatMode ?? "net",
    single_rate: null,
    rate_options: [],
    rate_preselected: null,
    caption: "тест",
    columns: params.columns,
    available_rate_classes: [],
    rows: [],
    totals: params.totals,
    totals_medians: {
      base: params.baseMedian ?? EMPTY_MEDIAN,
      amendments: EMPTY_MEDIAN,
      total: params.totalMedian ?? EMPTY_MEDIAN,
    },
  };
}

// ---------------------------------------------------------------------------
//  buildCostChartBars
// ---------------------------------------------------------------------------

describe("buildCostChartBars", () => {
  it("DoD 18: разворачивает signed_date DESC в старые -> новые по id, а не по позиции входа", () => {
    // Вход — КАК ОТДАЁТ СЕРВЕР (спека сравнения §2.1, DoD 3): новые сначала.
    const columns = [
      makeColumn({ contractId: 30, signedDate: "2026-06-01" }),
      makeColumn({ contractId: 20, signedDate: "2025-06-01" }),
      makeColumn({ contractId: 10, signedDate: "2024-06-01" }),
    ];
    // `totals` в ПЕРЕМЕШАННОМ порядке — если бы функция читала по позиции, а
    // не по `contract_id`, это тест бы поймал.
    const totals = [
      makeTotalsCell(10, makeBucketCell({ shown: "100.00", shownPerSqm: "100.00" })),
      makeTotalsCell(30, makeBucketCell({ shown: "300.00", shownPerSqm: "300.00" })),
      makeTotalsCell(20, makeBucketCell({ shown: "200.00", shownPerSqm: "200.00" })),
    ];
    const comparison = makeComparison({ columns, totals });

    const bars = buildCostChartBars(comparison, "total", "sqm");

    // Утверждение о ПОСЛЕДОВАТЕЛЬНОСТИ id, не о «первый элемент такой-то»:
    // сломанная сортировка (например, оставленный входной порядок) даст
    // [30, 20, 10] и тест упадёт.
    expect(bars.map((bar) => bar.contractId)).toEqual([10, 20, 30]);
    expect(bars.map((bar) => bar.value)).toEqual([100, 200, 300]);
  });

  it("бросает громкую ошибку, если totals не несёт ячейку для колонки", () => {
    const columns = [makeColumn({ contractId: 1, signedDate: "2025-01-01" })];
    const comparison = makeComparison({ columns, totals: [] });
    expect(() => buildCostChartBars(comparison, "total", "sqm")).toThrow(/1/);
  });

  it("DoD 19-21: оба знака поправки в одном наборе — верх сплошной части, промежуток, риска, знак положением", () => {
    // Числа — буквально пример спеки диаграммы стоимости §2.3: «поправка
    // +3,7 % либо −10,2 %» — не выдуманы для теста.
    const growth = makeBucketCell({
      shown: "103700.00",
      shownPerSqm: "103700.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const decline = makeBucketCell({
      shown: "89800.00",
      shownPerSqm: "89800.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      // Вход — КАК ОТДАЁТ СЕРВЕР: `signed_date DESC` (новые сначала, id=2
      // подписан позже id=1); `buildCostChartBars` сама разворачивает это в
      // старые -> новые (DoD 18), поэтому на выходе growth (id=1) идёт первым.
      columns: [
        makeColumn({ contractId: 2, signedDate: "2025-01-01" }),
        makeColumn({ contractId: 1, signedDate: "2024-01-01" }),
      ],
      totals: [makeTotalsCell(1, growth), makeTotalsCell(2, decline)],
    });

    const [growthBar, declineBar] = buildCostChartBars(comparison, "total", "sqm");

    // DoD 19: верх сплошной части — ВСЕГДА приведённое, при обоих знаках.
    expect(growthBar.value).toBe(103700);
    expect(growthBar.shownDecimal).toBe("103700.00");
    expect(declineBar.value).toBe(89800);
    expect(declineBar.shownDecimal).toBe("89800.00");
    // не отдельным флагом легенды.

    // Риска номинала — на уровне номинала В ОБОИХ случаях, без особого случая.
    expect(growthBar.nominalValue).toBe(100000);
    expect(declineBar.nominalValue).toBe(100000);

    // DoD 21: подпись значения — decimal-строка ПРИВЕДЁННОГО (верха сплошной
    // части), а не номинала. Если бы верх меток указывал на номинал, у
    // declineBar здесь стояло бы "100000.00".
    expect(declineBar.shownDecimal).not.toBe(declineBar.nominalDecimal);
  });

  it("DoD 20: порога минимальной высоты нет — промежуток меньше пикселя остаётся точным", () => {
    const cell = makeBucketCell({
      shown: "100000.05",
      shownPerSqm: "100000.05",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    // Если бы функция подгоняла тонкий промежуток к минимуму (старая редакция
    // макета, спека диаграммы стоимости §2.3), `to - from` было бы БОЛЬШЕ этой
    // крохотной разницы. Здесь — ровно она, без округления и без порога.
    expect(bar.gap).not.toBeNull();
    expect(bar.gap!.from).toBe(100000);
    expect(bar.gap!.to).toBe(100000.05);
    expect(bar.gap!.to - bar.gap!.from).toBeCloseTo(0.05, 10);
  });

  it("DoD 20: риска рисуется всегда при приведении, даже когда номинал и приведённое совпали", () => {
    const cell = makeBucketCell({
      shown: "100000.00",
      shownPerSqm: "100000.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    // `nominal` пришёл (коэффициент 1.0000) — промежуток присутствует, хотя
    // от=до: это и есть «без особого случая на равенство».
    expect(bar.nominalValue).toBe(100000);
  });

  it("когда приведения нет вовсе — ни промежутка, ни номинала, особого случая не заведено", () => {
    const cell = makeBucketCell({ shown: "115481.00", shownPerSqm: "115481.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    expect(bar.value).toBe(115481);
    expect(bar.nominalValue).toBeNull();
    expect(bar.gap).toBeNull();
  });

  it("спека диаграммы стоимости §2.9: договор без суммы остаётся в ряду, с причиной, а не выпадает", () => {
    const priced = makeBucketCell({ shown: "115481.00", shownPerSqm: "115481.00" });
    const unpriced = makeBucketCell({
      shown: null,
      shownPerSqm: null,
      reasons: ["unpriced_rows"],
    });
    const comparison = makeComparison({
      // `signed_date DESC` на входе (id=2 подписан позже) — разворот в
      // старые -> новые (DoD 18) ставит id=1 первым.
      columns: [
        makeColumn({ contractId: 2, signedDate: "2024-07-26" }),
        makeColumn({ contractId: 1, signedDate: "2024-05-28" }),
      ],
      totals: [makeTotalsCell(1, unpriced), makeTotalsCell(2, priced)],
    });

    const bars = buildCostChartBars(comparison, "total", "sqm");

    // Место осталось: два договора на входе — два бара на выходе, порядок
    // сохранён (ДоД 18 не нарушен).
    expect(bars).toHaveLength(2);
    expect(bars[0].contractId).toBe(1);
    expect(bars[0].value).toBeNull();
    expect(bars[0].gap).toBeNull();
    expect(bars[0].incompleteReasons).toEqual(["unpriced_rows"]);
  });

  it("следует выбранной корзине — читает cell[bucket], а не всегда total", () => {
    const totalCell = makeBucketCell({ shown: "500.00", shownPerSqm: "500.00" });
    const baseCell = makeBucketCell({ shown: "300.00", shownPerSqm: "300.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, totalCell, { base: baseCell })],
    });

    expect(buildCostChartBars(comparison, "total", "sqm")[0].value).toBe(500);
    expect(buildCostChartBars(comparison, "base", "sqm")[0].value).toBe(300);
  });

  it("единица «сумма договора» читает shown/nominal.shown, а не per_sqm", () => {
    const cell = makeBucketCell({
      shown: "34000000000.00",
      shownPerSqm: "203890.00",
      nominalShown: "30000000000.00",
      nominalShownPerSqm: "180000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2026-06-26" })],
      totals: [makeTotalsCell(1, cell)],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sum");

    expect(bar.value).toBe(34000000000);
    expect(bar.nominalValue).toBe(30000000000);
  });

  it("коэффициент приведения колонки и отклонение ячейки едут как decimal-строки сервера, без пересчёта", () => {
    const cell = makeBucketCell({
      shown: "168838.00",
      shownPerSqm: "168838.00",
      nominalShown: "115481.00",
      nominalShownPerSqm: "115481.00",
      deviationPct: "7.00",
    });
    const comparison = makeComparison({
      columns: [
        makeColumn({ contractId: 1, signedDate: "2024-07-26", inflationCoefficient: "1.4620" }),
      ],
      totals: [makeTotalsCell(1, cell)],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    expect(bar.inflationCoefficient).toBe("1.4620");
    expect(bar.deviationPct).toBe("7.00");
  });

  /*
   * ДВА РАЗНЫХ ФАКТА, которые сервер отдаёт одним пустым `shown_per_sqm`:
   * суммы нет вовсе и сумма есть, но площади нет. Причины в
   * `incomplete_reasons` про второй случай МОЛЧАТ — они описывают саму сумму,
   * — поэтому различитель обязан быть отдельным полем. Найдено внешним ревью
   * PR: диаграмма писала «нет суммы» над договором с полной стоимостью.
   */
  it("сумма есть, ТЭП нет: на оси ₽/м² это НЕ «нет суммы», а отдельный флаг", () => {
    const column = makeColumn({ contractId: 1, signedDate: "2025-01-01" });
    expect(column.area_total_sp).toBeNull();
    const comparison = makeComparison({
      columns: [column],
      // Ровно то, что отдаёт сервер без ТЭП: `shown` есть, `shown_per_sqm` пуст,
      // причин неполноты НЕТ (`backend/crud/comparison.py`: `shown_per_sqm`
      // считается только при непустой `area_total_sp`).
      totals: [makeTotalsCell(1, makeBucketCell({ shown: "34000000000.00", shownPerSqm: null }))],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    expect(bar.value).toBeNull();
    expect(bar.incompleteReasons).toEqual([]);
    expect(bar.perSqmBlockedByArea).toBe(true);
  });

  it("на оси СУММ тот же договор пустым не остаётся, и флаг снят", () => {
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, makeBucketCell({ shown: "34000000000.00", shownPerSqm: null }))],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sum");

    expect(bar.value).toBe(34000000000);
    expect(bar.perSqmBlockedByArea).toBe(false);
  });

  it("нет ни суммы, ни площади — флаг снят: причина здесь ДРУГАЯ", () => {
    /*
     * Парой к первому: без этого утверждения флаг мог бы стоять всегда, когда
     * нет площади, и «нет суммы: без цены» подменилось бы разговором о ТЭП.
     */
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [
        makeTotalsCell(
          1,
          makeBucketCell({ shown: null, shownPerSqm: null, reasons: ["unpriced_rows"] })
        ),
      ],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    expect(bar.value).toBeNull();
    expect(bar.perSqmBlockedByArea).toBe(false);
    expect(bar.incompleteReasons).toEqual(["unpriced_rows"]);
  });

  it("площадь заведена — флаг снят даже при пустом ₽/м²", () => {
    /*
     * Достижимо в режиме «своя ставка»: агрегат гасит `shown`/`shown_per_sqm`
     * причиной `display_rate_undefined`, а площадь при этом есть. Разговор о ТЭП
     * там был бы неправдой.
     */
    const column = makeColumn({ contractId: 1, signedDate: "2025-01-01" });
    column.area_total_sp = "166756.90";
    const comparison = makeComparison({
      columns: [column],
      totals: [
        makeTotalsCell(
          1,
          makeBucketCell({
            shown: null,
            shownPerSqm: null,
            reasons: ["display_rate_undefined"],
          })
        ),
      ],
    });

    const [bar] = buildCostChartBars(comparison, "total", "sqm");

    expect(bar.perSqmBlockedByArea).toBe(false);
  });
});

// ---------------------------------------------------------------------------
//  costChartAxisTop
// ---------------------------------------------------------------------------

describe("costChartAxisTop", () => {
  it("грабли потолка: 205 879 при шаге 50 000 обязаны дать 5 интервалов, а не 4 (генератор макета, nice_axis)", () => {
    // Старое выражение с усечением к нулю (`-(-vmax // step)` над `Decimal`)
    // давало здесь 4 интервала и верх оси 200 000 — НИЖЕ максимума. Явное
    // сравнение с коррекцией (см. докстроку `niceAxisTop`) обязано дать 5.
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, makeBucketCell({ shown: "205879.00", shownPerSqm: "205879.00" }))],
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    expect(axis.top).toBe(250000);
    expect(Math.max(...axis.ticks)).toBe(axis.top);
    expect(axis.ticks).toEqual([0, 50000, 100000, 150000, 200000, 250000]);
    // Соответствие защите `assert max(pool) <= t` генератора.
    expect(205879).toBeLessThanOrEqual(axis.top);
  });

  it("DoD 28: верх оси вмещает столбец, риску/призрак снижения И медиану выше самого высокого столбца", () => {
    // Столбец в СНИЖЕНИИ: приведённое 50 000, номинал 60 000 (призрак стоит
    // НАД столбцом — выше самого бара). Медиана текущего состояния — 90 000,
    // выше обоих. Пул: {50000, 60000, 90000}.
    const cell = makeBucketCell({
      shown: "50000.00",
      shownPerSqm: "50000.00",
      nominalShown: "60000.00",
      nominalShownPerSqm: "60000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "90000.00", shownPerSqm: "90000.00" }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    // nice_axis(90000): шаг 20 000, n = 5, верх 100 000 — расчёт по
    // `niceAxisTop`, тому же алгоритму, что `nice_axis` генератора макета.
    expect(axis.top).toBe(100000);
    /*
      Роль сторожа здесь играет САМО ЧИСЛО: без медианы в пуле максимум был бы
      50 000 и верх оси стал бы 50 000. `assertPoolFits` этого не поймал бы —
      он сравнивает верх с максимумом ТОГО ЖЕ пула, поэтому недостача в пуле
      уменьшает обе стороны сравнения разом и защита молчит. Проверено
      снятием: без медианы падает это утверждение, а не защита.

      Призрак снижения (60 000) в ЭТОМ наборе тоже не сторожится — медиана его
      перекрывает. Ему нужен свой набор, он ниже.
    */
  });

  it("DoD 28: призрак снижения ОДИН поднимает верх оси, когда он выше и столбца, и медианы", () => {
    /*
      Набор, где максимум пула — именно номинал: приведённое 50 000, номинал
      120 000 (снижение, призрак стоит НАД столбцом), медиана 40 000, ниже
      обоих. Предыдущий набор это свойство не проверял: там медиана 90 000
      перекрывала призрак 60 000, и исключение номинала из пула не меняло
      верха оси вовсе — измерено снятием, тест оставался зелёным.
    */
    const cell = makeBucketCell({
      shown: "50000.00",
      shownPerSqm: "50000.00",
      nominalShown: "120000.00",
      nominalShownPerSqm: "120000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "40000.00", shownPerSqm: "40000.00" }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    // nice_axis(120000): шаг 20 000, n=6, верх ровно 120 000. Без призрака в
    // пуле максимумом стал бы столбец 50 000, верх оси стал бы 50 000 — и
    // призрак рисовался бы ВЫШЕ полотна.
    expect(axis.top).toBe(120000);
    expect(axis.top).toBeGreaterThanOrEqual(120000);
  });

  it("DoD 28: НОМИНАЛЬНАЯ медиана тоже входит в пул, когда она выше приведённой и выше столбцов", () => {
    const cell = makeBucketCell({
      shown: "50000.00",
      shownPerSqm: "50000.00",
      nominalShown: "45000.00",
      nominalShownPerSqm: "45000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({
        value: "60000.00",
        shownPerSqm: "60000.00",
        nominalValue: "90000.00",
        nominalShownPerSqm: "90000.00",
      }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    // Пул: {50000, 45000, 60000, 90000} -> максимум 90000 -> nice_axis(90000)
    // = верх 100 000 (шаг 20 000, n = 5) по `niceAxisTop`.
    expect(axis.top).toBe(100000);
  });

  it("на оси сумм медианы нет — она в пул не входит, даже если поле пришло", () => {
    const cell = makeBucketCell({ shown: "50000.00", shownPerSqm: "50000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      // Огромная медиана — если бы unit="sum" её всё же учитывал, верх оси
      // ушёл бы в область миллиарда, а не остался около 50 000.
      totalMedian: makeMedian({ value: "999999999.00", shownPerSqm: "999999999.00" }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sum"), comparison.totals_medians["total"], "sum");

    expect(axis.top).toBe(50000);
  });

  it("в «Своей ставке» у медианы нет поля shown_per_sqm вовсе — в пул она не попадает", () => {
    const cell = makeBucketCell({ shown: "50000.00", shownPerSqm: "50000.00" });
    const comparison = makeComparison({
      vatMode: "own",
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      // `shownPerSqm` не передан — как ключ, отсутствующий у сервера в `own`.
      totalMedian: makeMedian({ value: "999999999.00" }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    expect(axis.top).toBe(50000);
  });

  it("пустой пул (нет ни одной суммы) — верх 1, засечка только на нуле", () => {
    const cell = makeBucketCell({ shown: null, shownPerSqm: null, reasons: ["unpriced_rows"] });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    expect(axis).toEqual({ top: 1, ticks: [0] });
  });

  it("DoD 28а: выборка без «люкса», НОМИНАЛ — верх оси 150 000 (числа §1.1 спеки диаграммы стоимости)", () => {
    // Нетто ₽/м² без приведения: ДГП-3С10 без цены, ВЕР1-ГП 115 481, СДП-1-МР
    // 110 762, 12-СИТ-МР 144 119 — буквально таблица §1.1. Медиана после
    // снятия «люкса» там же — 115 481, ниже максимума столбца и на верх оси
    // не влияет.
    // Вход — `signed_date DESC` (новые сначала), как отдаёт сервер; на верх
    // оси порядок не влияет (пул — множество значений), но фикстура следует
    // реальному контракту ради читаемости.
    const columns = [
      makeColumn({ contractId: 4, signedDate: "2026-07-06" }), // 12-СИТ-МР
      makeColumn({ contractId: 3, signedDate: "2025-02-20" }), // СДП-1-МР
      makeColumn({ contractId: 2, signedDate: "2024-07-26" }), // ВЕР1-ГП
      makeColumn({ contractId: 1, signedDate: "2024-05-28" }), // ДГП-3С10
    ];
    const totals = [
      makeTotalsCell(1, makeBucketCell({ shown: null, shownPerSqm: null, reasons: ["unpriced_rows"] })),
      makeTotalsCell(2, makeBucketCell({ shown: "115481.00", shownPerSqm: "115481.00" })),
      makeTotalsCell(3, makeBucketCell({ shown: "110762.00", shownPerSqm: "110762.00" })),
      makeTotalsCell(4, makeBucketCell({ shown: "144119.00", shownPerSqm: "144119.00" })),
    ];
    const comparison = makeComparison({
      columns,
      totals,
      totalMedian: makeMedian({
        value: "115481.00",
        shownPerSqm: "115481.00",
        comparableCount: 3,
        contractIds: [2, 3, 4],
      }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    expect(axis.top).toBe(150000);
  });

  it("DoD 28а: та же выборка, ПРИВЕДЕНО по «Фактической инфляции» — верх оси 200 000", () => {
    /*
     * Максимум пула — 168 838 (ВЕР1-ГП): буквальное число спеки диаграммы
     * стоимости §1.1, «приведено к 08.2026 по „Фактической инфляции“
     * (k = 1,4620)» от номинала 115 481. Приведённые значения СДП-1-МР и
     * 12-СИТ-МР под этим же рядом на 08.2026 спекой не названы — здесь они
     * подставлены иллюстративно, НИЖЕ 168 838, чтобы не завысить пул мимо
     * проверяемого числа; сам факт «выше максимума» проверен отдельными
     * тестами DoD 28 выше, этот тест — только именованные числа DoD 28а.
     */
    const columns = [
      makeColumn({ contractId: 4, signedDate: "2026-07-06" }), // 12-СИТ-МР
      makeColumn({ contractId: 3, signedDate: "2025-02-20" }), // СДП-1-МР
      makeColumn({ contractId: 2, signedDate: "2024-07-26", inflationCoefficient: "1.4620" }), // ВЕР1-ГП
      makeColumn({ contractId: 1, signedDate: "2024-05-28" }), // ДГП-3С10
    ];
    const totals = [
      makeTotalsCell(
        1,
        makeBucketCell({ shown: null, shownPerSqm: null, reasons: ["unpriced_rows"] })
      ),
      makeTotalsCell(
        2,
        makeBucketCell({
          shown: "168838.00",
          shownPerSqm: "168838.00",
          nominalShown: "115481.00",
          nominalShownPerSqm: "115481.00",
        })
      ),
      makeTotalsCell(
        3,
        makeBucketCell({
          shown: "118000.00",
          shownPerSqm: "118000.00",
          nominalShown: "110762.00",
          nominalShownPerSqm: "110762.00",
        })
      ),
      makeTotalsCell(
        4,
        makeBucketCell({
          shown: "150000.00",
          shownPerSqm: "150000.00",
          nominalShown: "144119.00",
          nominalShownPerSqm: "144119.00",
        })
      ),
    ];
    const comparison = makeComparison({
      columns,
      totals,
      totalMedian: makeMedian({
        value: "130000.00",
        shownPerSqm: "130000.00",
        comparableCount: 3,
        contractIds: [2, 3, 4],
        nominalValue: "115481.00",
        nominalShownPerSqm: "115481.00",
      }),
    });

    const axis = costChartAxisTop(buildCostChartBars(comparison, "total", "sqm"), comparison.totals_medians["total"], "sqm");

    expect(axis.top).toBe(200000);
  });
});

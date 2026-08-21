import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ContractCostChart } from "./ContractCostChart";
import { REASON_LABELS } from "./labels";
import type {
  Comparison,
  ComparisonBucketCell,
  ComparisonCell,
  ComparisonCellState,
  ComparisonColumn,
  ComparisonIncompleteReason,
  ComparisonMedian,
  ComparisonVatMode,
} from "@/types/domain";

/*
 * Компонент диаграммы стоимости (план, задача 11; спека диаграммы стоимости
 * §2.2–§2.5, §2.8–§2.9; DoD 18–29). Компонент получает готовый `Comparison` и
 * корзину пропами и своего запроса не делает — тест поэтому прямой рендер,
 * без MSW и без страницы `ComparePage`.
 *
 * **Фикстуры — свои, а не из `src/test/fixtures.ts`.** Тот же довод, что у
 * `costChartData.test.ts`: колонки и ячейки общей фикстуры сравнения устроены
 * для таблицы, а не для геометрии диаграммы. Маленькие хелперы ниже — почти
 * буквальная копия хелперов `costChartData.test.ts` (тот же контракт типов),
 * продублированная по той же причине: два файла теста читают разные вещи —
 * там числа `buildCostChartBars`/`costChartAxisTop`, здесь — отрисованный
 * компонент.
 *
 * **Подписи причин НЕ дублируются фикстурой.** Прежняя редакция собирала свой
 * `REASON_LABELS` с теми же текстами — и тогда «диаграмма подписывает
 * причины теми же словами, что таблица» не проверялось ничем: тест сверял
 * компонент со СВОЕЙ копией словаря. Здесь читается общий модуль `labels.ts`,
 * тот самый, который рисует таблица (спека диаграммы стоимости §2.9).
 */

// ---------------------------------------------------------------------------
//  Мок recharts — жёсткий размер вместо `ResponsiveContainer` (см. докстроку
//  `ContractCostChart.tsx`, абзац о совмещении полос): без него `<BarChart>`
//  под `ChartContainer` не рисует в jsdom ни одного `.recharts-rectangle`.
//  Нужен ТОЛЬКО тесту геометрии (`x`/`width` двух полос) — остальные тесты
//  читают собственную вёрстку компонента (жёлоб, подписи под столбцами) и в
//  этом моке не нуждаются, но он не мешает и им.
// ---------------------------------------------------------------------------
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => (
      <actual.ResponsiveContainer width={600} height={300}>
        {children as never}
      </actual.ResponsiveContainer>
    ),
  };
});

// ---------------------------------------------------------------------------
//  Маленькие хелперы фикстуры
// ---------------------------------------------------------------------------

function makeColumn(params: {
  contractId: number;
  signedDate: string;
  rateClassTitle?: string;
  inflationCoefficient?: string | null;
}): ComparisonColumn {
  return {
    contract_id: params.contractId,
    contract_number: `Д-${params.contractId}`,
    object_title: `Объект ${params.contractId}`,
    contractor_title: `Подрядчик ${params.contractId}`,
    rate_class_id: 1,
    rate_class_title: params.rateClassTitle ?? "бизнес",
    signed_date: params.signedDate,
    area_total_sp: null,
    advance_pct: null,
    bank_guarantee_pct: null,
    retention_pct: null,
    composition_caption: "20 %",
    inflation_coefficient: params.inflationCoefficient,
  };
}

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

function makeTotalsCell(
  contractId: number,
  total: ComparisonBucketCell,
  overrides: Partial<{ base: ComparisonBucketCell; amendments: ComparisonBucketCell }> = {}
): ComparisonCell {
  return {
    contract_id: contractId,
    base: overrides.base ?? total,
    amendments:
      overrides.amendments ?? makeBucketCell({ shown: null, shownPerSqm: null, state: "absent" }),
    total,
  };
}

function makeMedian(params: {
  value: string | null;
  comparableCount?: number;
  shownPerSqm?: string | null;
  nominalValue?: string | null;
  nominalShownPerSqm?: string | null;
}): ComparisonMedian {
  const median: ComparisonMedian = {
    value: params.value,
    comparable_count: params.comparableCount ?? 3,
    contract_ids: [],
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

/**
 * Пустая медиана незанятых корзин. `shown_per_sqm: null` — не украшение: в
 * режимах `net`/`single` сервер отдаёт поле у КАЖДОЙ корзины (спека диаграммы
 * стоимости §2.10), и фикстура без него описывала состояние, которого сервер не
 * выдаёт, — тогда диаграмма в `net` объясняла бы отсутствие линии «своей
 * ставкой». Предпосылка фикстуры обязана быть достижимой.
 */
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
      base: EMPTY_MEDIAN,
      amendments: EMPTY_MEDIAN,
      total: params.totalMedian ?? EMPTY_MEDIAN,
    },
  };
}

/** Три договора с ценой — общий костяк для тестов о медиане/единице. */
function threeContractComparison(totalMedian: ComparisonMedian, vatMode?: ComparisonVatMode): Comparison {
  const cells = [
    makeBucketCell({ shown: "100000000.00", shownPerSqm: "100000.00" }),
    makeBucketCell({ shown: "115000000.00", shownPerSqm: "115000.00" }),
    makeBucketCell({ shown: "150000000.00", shownPerSqm: "150000.00" }),
  ];
  return makeComparison({
    columns: [1, 2, 3].map((id) => makeColumn({ contractId: id, signedDate: `202${id}-01-01` })),
    totals: cells.map((cell, i) => makeTotalsCell(i + 1, cell)),
    totalMedian,
    vatMode,
  });
}

function renderChart(comparison: Comparison, bucket: "total" | "base" | "amendments" = "total") {
  return render(
    <ContractCostChart
      comparison={comparison}
      bucket={bucket}
    />
  );
}

// ---------------------------------------------------------------------------
//  Медиана (DoD 22, 22г, 23, 24)
// ---------------------------------------------------------------------------

describe("ContractCostChart — медиана", () => {
  it("DoD 22: линия медианы есть при net и при single (поле shown_per_sqm пришло)", () => {
    for (const vatMode of ["net", "single"] as const) {
      const comparison = threeContractComparison(
        makeMedian({ value: "115000.00", shownPerSqm: "115000.00" }),
        vatMode
      );
      const { unmount } = renderChart(comparison);
      expect(screen.getByTestId("cost-chart-median-line")).toHaveTextContent("медиана");
      expect(screen.queryByTestId("cost-chart-median-note")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("DoD 22: нет линии в «Своей ставке» — поле shown_per_sqm ОТСУТСТВУЕТ, а не равно null", () => {
    // `makeMedian` без `shownPerSqm` — ключа нет вовсе, как в ответе сервера при `vat_mode=own`.
    const comparison = threeContractComparison(makeMedian({ value: "115000.00" }), "own");
    renderChart(comparison);

    expect(screen.queryByTestId("cost-chart-median-line")).not.toBeInTheDocument();
    const note = screen.getByTestId("cost-chart-median-note");
    expect(note).toHaveTextContent("своим множителем");
    // Разные причины — разные подписи: этот текст принадлежит ДРУГОЙ причине
    // (сопоставимых меньше трёх) и не имеет права появиться здесь.
    expect(note).not.toHaveTextContent("медианы нет: сервер считает её при трёх и более");
  });

  it("DoD 24: меньше трёх сопоставимых — поле shown_per_sqm ПРИСУТСТВУЕТ и равно null, объяснение другое, плашек отклонений нет", () => {
    const median = makeMedian({ value: null, shownPerSqm: null, comparableCount: 1 });
    // Отклонения у ячеек тоже null — тем же правилом, каким сервер гасит их при
    // отсутствии медианы (спека сравнения §2.5, правило 5): фикстура следует
    // ответу, а не придумывает его.
    const cell = makeBucketCell({ shown: "100000000.00", shownPerSqm: "100000.00", deviationPct: null });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: median,
    });
    renderChart(comparison);

    expect(screen.queryByTestId("cost-chart-median-line")).not.toBeInTheDocument();
    const note = screen.getByTestId("cost-chart-median-note");
    expect(note).toHaveTextContent("Сопоставимых договоров в выборке 1");
    expect(note).not.toHaveTextContent("своим множителем");
    expect(screen.queryByTestId("cost-chart-deviation-1")).not.toBeInTheDocument();
  });

  it("DoD 23: при приведении линий две, вторая подписана «номинал»", () => {
    const comparison = threeContractComparison(
      makeMedian({
        value: "115000.00",
        shownPerSqm: "150000.00",
        nominalValue: "115000.00",
        nominalShownPerSqm: "115000.00",
      })
    );
    renderChart(comparison);

    /*
      Плашки несут РАЗНЫЕ числа, и это проверяется числами, а не словами
      «медиана»/«номинал»: подмена источника у одной из них (например, обе
      читают номинальную медиану) слова не меняет вовсе — измерено, такая
      подмена проходила молча. Числа фикстуры разведены нарочно.
    */
    const current = screen.getByTestId("cost-chart-median-line");
    const nominalChip = screen.getByTestId("cost-chart-median-line-nominal");
    expect(current).toHaveTextContent("медиана");
    expect(current.textContent).toMatch(/150.000/);
    expect(nominalChip).toHaveTextContent("номинал");
    expect(nominalChip.textContent).toMatch(/115.000/);
    // И это РАЗНЫЕ числа: приведение двигает и суммы, и медиану, поэтому без
    // второй линии движение столбцов читалось бы как движение отклонений.
    expect(current.textContent).not.toBe(nominalChip.textContent);
    expect(current.style.bottom).not.toBe(nominalChip.style.bottom);
  });

  it("DoD 22г: на оси сумм линии нет, ХОТЯ поле shown_per_sqm пришло — негативный", async () => {
    const user = userEvent.setup();
    const comparison = threeContractComparison(
      makeMedian({ value: "115000.00", shownPerSqm: "115000.00" })
    );
    renderChart(comparison);
    expect(screen.getByTestId("cost-chart-median-line")).toBeInTheDocument();

    // Переключаем диаграмму (не таблицу!) на «Сумма договора».
    await user.click(screen.getByRole("button", { name: "Сумма договора" }));

    // Если бы правило было «линия тогда и только тогда, когда поле пришло»
    // (свёрнутая проверка, которую спека диаграммы стоимости §2.4/§2.8 прямо
    // запрещает), линия осталась бы — снятие проверки единицы обязано
    // ронять именно это утверждение.
    expect(screen.queryByTestId("cost-chart-median-line")).not.toBeInTheDocument();
    const note = screen.getByTestId("cost-chart-median-note");
    expect(note).toHaveTextContent("только на оси");
    // И это ДРУГАЯ причина, а не «своя ставка» — тексты не должны совпасть.
    expect(note).not.toHaveTextContent("своим множителем");
  });
});

// ---------------------------------------------------------------------------
//  Единица диаграммы: плашка «к медиане ₽/м²» на оси сумм (DoD 26)
// ---------------------------------------------------------------------------

describe("ContractCostChart — единица диаграммы", () => {
  it("DoD 26: на оси сумм плашка отклонения подписана «к медиане ₽/м²» — негативный", async () => {
    const user = userEvent.setup();
    const cell = makeBucketCell({
      shown: "150000000.00",
      shownPerSqm: "150000.00",
      deviationPct: "15.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "130000.00", shownPerSqm: "130000.00" }),
    });
    renderChart(comparison);

    // На оси ₽/м² плашка есть, но БЕЗ пометки «к медиане ₽/м²» — единица одна,
    // уточнять нечего.
    expect(screen.getByTestId("cost-chart-deviation-1")).not.toHaveTextContent("к медиане ₽/м²");

    await user.click(screen.getByRole("button", { name: "Сумма договора" }));

    // Без этой пометки процент рядом с суммой прочитался бы как отклонение от
    // медианы СУММ, чего он не значит (спека диаграммы стоимости §2.4,
    // DoD 26). Снятие пометки обязано ронять это утверждение.
    expect(screen.getByTestId("cost-chart-deviation-1")).toHaveTextContent("к медиане ₽/м²");
  });
});

// ---------------------------------------------------------------------------
//  Корзина, договор без суммы (DoD 25, 27)
// ---------------------------------------------------------------------------

describe("ContractCostChart — корзина и неполные данные", () => {
  it("DoD 27: следует переключателю корзины, показывает подпись и данные ИМЕННО этой корзины", () => {
    const totalCell = makeBucketCell({ shown: "150000000.00", shownPerSqm: "150000.00" });
    const baseCell = makeBucketCell({ shown: "90000000.00", shownPerSqm: "90000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, totalCell, { base: baseCell })],
    });

    // `formatDecimalMoney` группирует разряды НЕРАЗРЫВНЫМ пробелом (U+00A0,
    // `src/lib/format.ts`), не обычным — сравнение ниже учитывает это явно.
    const { unmount } = renderChart(comparison, "total");
    expect(screen.getByRole("heading", { name: /Итого/ })).toBeInTheDocument();
    expect(screen.getByTestId("cost-chart-column-1")).toHaveTextContent(/150.000/);
    unmount();

    renderChart(comparison, "base");
    expect(screen.getByRole("heading", { name: /ДГП/ })).toBeInTheDocument();
    expect(screen.getByTestId("cost-chart-column-1")).toHaveTextContent(/90.000/);
  });

  it("DoD 25: договор без суммы остаётся в ряду, с причиной из incompleteReasons, а не выпадает", () => {
    const priced = makeBucketCell({ shown: "115000000.00", shownPerSqm: "115000.00" });
    const unpriced = makeBucketCell({
      shown: null,
      shownPerSqm: null,
      reasons: ["unpriced_rows"],
    });
    const comparison = makeComparison({
      columns: [
        makeColumn({ contractId: 1, signedDate: "2024-05-01" }),
        makeColumn({ contractId: 2, signedDate: "2024-07-01" }),
      ],
      totals: [makeTotalsCell(1, unpriced), makeTotalsCell(2, priced)],
    });
    renderChart(comparison);

    // Место осталось: оба договора дали по столбцу-подписи.
    expect(screen.getByTestId("cost-chart-column-1")).toBeInTheDocument();
    expect(screen.getByTestId("cost-chart-column-2")).toBeInTheDocument();
    // Слова причины читаются из ОБЩЕГО словаря экрана, а не переписаны в тест:
    // так утверждение «диаграмма подписывает причины теми же словами, что
    // таблица» (спека диаграммы стоимости §2.9) держится на одном источнике, а
    // не на совпадении двух копий.
    expect(screen.getByTestId("cost-chart-nodata-1")).toHaveTextContent(
      `нет суммы: ${REASON_LABELS.unpriced_rows}`
    );
  });
});

// ---------------------------------------------------------------------------
//  Геометрия: совмещение полос по горизонтали (barGap), DoD 20
// ---------------------------------------------------------------------------

describe("ContractCostChart — геометрия столбца", () => {
  it("x и width сплошной полосы и промежутка к номиналу совпадают — иначе штриховка уезжает сбоку", () => {
    // Один столбец с промежутком: ровно два `.recharts-rectangle` в SVG — по
    // одному на серию `solid` и `gapRange`. Без парного `barSize`/`barGap={-barSize}`
    // recharts развёл бы их своим дефолтным `barGap` (4px), и `x` разошлись бы.
    const cell = makeBucketCell({
      shown: "103700.00",
      shownPerSqm: "103700.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });
    const { container } = renderChart(comparison);

    const rects = container.querySelectorAll(".recharts-rectangle");
    expect(rects).toHaveLength(2);
    const [a, b] = Array.from(rects);
    expect(a.getAttribute("x")).not.toBeNull();
    expect(a.getAttribute("x")).toBe(b.getAttribute("x"));
    expect(a.getAttribute("width")).toBe(b.getAttribute("width"));
  });

  it("на оси ₽/м² деньги показаны ЦЕЛЫМИ рублями, а точное значение — в подсказке", () => {
    /*
      Дефект, найденный ПРОГОНОМ НА СТЕНДЕ, а не тестами: `formatDecimalMoney`
      без `maxFractionDigits` печатает все знаки — так задумано, — и диаграмма не
      просила округления. Настоящая медиана стенда оказалась периодической
      дробью, и плашка печатала сотню знаков. Ни один тест этого не поймал:
      во ВСЕХ фикстурах стояли круглые суммы вида «50000.00», где лишних знаков
      нет вовсе (`docs/insights/verifying-guards.md`, слой 12).

      Формат взят из согласованного макета (`fmt_value` его генератора): на
      ₽/м² — целые рубли. Копейки там шум, и они же разгоняли плашку медианы
      поверх подписи засечки — тоже замерено на стенде.
    */
    const PERIODIC = "129799.975056627364341352";
    const cell = makeBucketCell({
      shown: "2100000.949999999999999999",
      shownPerSqm: PERIODIC,
      nominalShown: "2000000.5",
      nominalShownPerSqm: "115480.681111111111111111",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({
        value: PERIODIC,
        shownPerSqm: PERIODIC,
        nominalValue: "115480.681111111111111111",
        nominalShownPerSqm: "115480.681111111111111111",
      }),
    });
    const { container } = renderChart(comparison);

    const chip = screen.getByTestId("cost-chart-median-line");
    expect(chip).toHaveTextContent(/^медиана 129.800$/);
    // Разряды группируются НЕРАЗРЫВНЫМ пробелом, поэтому сравнение — регуляркой.
    expect(chip.getAttribute("title")).toMatch(/129.799,975056627364341352/);

    const nominalChip = screen.getByTestId("cost-chart-median-line-nominal");
    expect(nominalChip).toHaveTextContent(/^номинал 115.481$/);
    expect(nominalChip.getAttribute("title")).toMatch(/115.480,681111111111111111/);

    // Подпись над столбцом — тем же правилом. Подсказки у SVG-текста нет, и
    // точное значение там взять негде: оно доступно в таблице того же экрана.
    const svgText = [...container.querySelectorAll("svg text")].map((el) => el.textContent ?? "");
    expect(svgText.some((t) => /^129.800$/.test(t))).toBe(true);
    expect(svgText.some((t) => t.length > 12)).toBe(false);
  });

  it("на оси сумм деньги показаны МИЛЛИАРДАМИ, а не полным числом рублей", async () => {
    /*
      Второе правило того же формата макета: сумма договора идёт миллиардами с
      двумя знаками. Без него под столбцом стояло бы «34 123 456 789,00», и
      подписи засечек оси — тоже. Деление на миллиард сделано точной арифметикой
      строк, не `Number()`.
    */
    const cell = makeBucketCell({ shown: "34123456789.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000.00", shownPerSqm: "50000.00" }),
    });
    const { container } = renderChart(comparison);

    await userEvent.click(screen.getByRole("button", { name: "Сумма договора" }));

    const svgText = [...container.querySelectorAll("svg text")].map((el) => el.textContent ?? "");
    expect(svgText.some((t) => /^34,12 млрд$/.test(t))).toBe(true);
    expect(svgText.some((t) => /34.123.456.789/.test(t))).toBe(false);
  });

  it("подсказка с точным значением НЕ ставится, когда показ ничего не потерял", () => {
    // Тот же контракт, что у `MoneyCell`: подсказка, повторяющая видимое, лишь
    // мешает. Первая редакция ставила её безусловно.
    const cell = makeBucketCell({ shown: "100000", shownPerSqm: "100000" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000", shownPerSqm: "50000" }),
    });
    renderChart(comparison);

    expect(screen.getByTestId("cost-chart-median-line")).not.toHaveAttribute("title");
  });

  it("числа медианы дублируются ТЕКСТОМ: в жёлобе плашки накрывают друг друга", () => {
    /*
      Приём согласованного макета (`medcap`), пропущенный первой редакцией.
      Замерено на стенде: при близких приведённой и номинальной медианах плашка
      номинала полностью закрыла плашку текущей, и главное число экрана стало
      невидимым. Строка ниже держит оба числа независимо от того, где стоят
      плашки, — поэтому и порога «насколько близко» здесь нет.
    */
    const cell = makeBucketCell({
      shown: "129484.00",
      shownPerSqm: "129484.00",
      nominalShown: "129800.00",
      nominalShownPerSqm: "129800.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({
        value: "129484.00",
        shownPerSqm: "129484.00",
        nominalValue: "129800.00",
        nominalShownPerSqm: "129800.00",
      }),
    });
    renderChart(comparison);

    const caption = screen.getByTestId("cost-chart-median-caption");
    expect(caption.textContent).toMatch(/129.484/);
    expect(caption.textContent).toMatch(/в номинале — 129.800/);
    expect(caption.textContent).toContain("нетто");

    // Плашки при этом стоят почти вплотную — то самое состояние, из-за которого
    // строка и понадобилась: разница позиций меньше высоты плашки.
    const a = Number.parseFloat(screen.getByTestId("cost-chart-median-line").style.bottom);
    const b = Number.parseFloat(screen.getByTestId("cost-chart-median-line-nominal").style.bottom);
    expect(Math.abs(a - b)).toBeLessThan(3);
  });

  it("легенда объясняет промежуток, риску и медиану — образцами, а не одной строкой текста", () => {
    /*
      Легенда была потеряна целиком: макет рисует четыре метки с образцами, а
      первая редакция компонента свела их к одной серой строке под полотном.
      Видно это было только на живом экране — пока цвета не разрешались, там и
      объяснять было нечего.

      Проверяется СОСТАВ: приведение даёт три метки (приведённое, промежуток,
      номинал) плюс медиану, и подпись приведения называет ряд и целевой месяц
      ИЗ ОТВЕТА, а не из состояния экрана.
    */
    const cell = makeBucketCell({
      shown: "103700.00",
      shownPerSqm: "103700.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "100000.00", shownPerSqm: "100000.00" }),
    });
    comparison.inflation = {
      series_id: 2,
      series_name: "Фактическая инфляция",
      series_note: null,
      series_updated_at: null,
      target_month: "2026-08",
      has_forecast: false,
      used_years: [],
    };
    renderChart(comparison);

    const legend = screen.getByTestId("cost-chart-legend");
    const items = within(legend)
      .getAllByRole("listitem")
      .map((li) => li.textContent ?? "");
    expect(items).toHaveLength(4);
    expect(items[0]).toBe("приведено к ценам на август 2026 по ряду «Фактическая инфляция»");
    expect(items[1]).toContain("промежуток к номиналу");
    expect(items[2]).toContain("номинал: цены подписания");
    expect(items[3]).toBe("медиана выборки (нетто, ₽/м²)");
  });

  it("без приведения легенда несёт ТОЛЬКО медиану: промежутка и риски на полотне нет", () => {
    const cell = makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000.00", shownPerSqm: "50000.00" }),
    });
    renderChart(comparison);

    const items = within(screen.getByTestId("cost-chart-legend"))
      .getAllByRole("listitem")
      .map((li) => li.textContent ?? "");
    expect(items).toHaveLength(1);
    expect(items[0]).toContain("медиана выборки");
  });

  it("в «Единой» легенда медианы называет СТАВКУ ПОКАЗА, а не нетто", async () => {
    const cell = makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000.00", shownPerSqm: "60000.00" }),
      vatMode: "single",
    });
    comparison.single_rate = "20.00";
    renderChart(comparison);

    expect(within(screen.getByTestId("cost-chart-legend")).getByText(/в ставке показа 20.00 %/)).toBeInTheDocument();

    // А на оси сумм линии медианы нет вовсе — значит и метки её в легенде нет.
    await userEvent.click(screen.getByRole("button", { name: "Сумма договора" }));
    expect(screen.queryByTestId("cost-chart-legend")).not.toBeInTheDocument();
  });

  it("риска номинала нарисована НА ПОЛОТНЕ и стоит на уровне номинала, а не только в подписи", () => {
    /*
      Дыра, найденная снятием: до этого теста весь SVG, кроме двух
      прямоугольников, не проверялся ничем — удаление всех `<ReferenceDot>`
      оставляло набор ЗЕЛЁНЫМ, хотя DoD 20 требует риску на уровне номинала.
      Проверяется КООРДИНАТА: полотно 300 px, домен `[0, top]`, поэтому
      `y = height * (1 - номинал/top)`. Совпадение по формуле — а не «элемент
      есть» — ловит и риску, съехавшую на приведённое значение.
    */
    const cell = makeBucketCell({
      shown: "50000.00",
      shownPerSqm: "50000.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "40000.00", shownPerSqm: "40000.00" }),
    });
    const { container } = renderChart(comparison);

    // Верх оси на этом наборе — 100 000 (максимум пула это сам номинал),
    // значит риска обязана лежать на самом верху полотна: y = 0.
    const dots = container.querySelectorAll(".recharts-reference-dot line");
    expect(dots).toHaveLength(1);
    expect(dots[0].getAttribute("y1")).toBe("0");
    expect(dots[0].getAttribute("y1")).toBe(dots[0].getAttribute("y2"));

    // И она НЕ на уровне приведённого значения (то было бы y = 150).
    expect(dots[0].getAttribute("y1")).not.toBe("150");
  });

  it("полотно и жёлоб — ОДНА линейка: домен оси задан верхом из costChartAxisTop", () => {
    /*
      Дыра, найденная снятием: удаление `domain={[0, axis.top]}` не роняло НИ
      ОДНОГО теста из 542, потому что во всех прежних фикстурах максимум данных
      совпадал с верхом оси, и авто-домен recharts случайно давал то же самое.
      Здесь они РАЗВЕДЕНЫ: столбец 205 879 даёт верх оси 250 000 (грабля потолка
      из генератора макета), медиана 125 000 — ровно половина этого верха.
      Без домена recharts взял бы за верх сам столбец, и линия медианы уехала бы
      на y ≈ 129,6 вместо 150: жёлоб (проценты от `axis.top`) и полотно
      перестали бы быть одной линейкой, о чём докстрока компонента и говорит.
    */
    const cell = makeBucketCell({ shown: "205879.00", shownPerSqm: "205879.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "125000.00", shownPerSqm: "125000.00" }),
    });
    const { container } = renderChart(comparison);

    const lines = container.querySelectorAll(".recharts-reference-line line");
    expect(lines).toHaveLength(1);
    expect(lines[0].getAttribute("y1")).toBe("150");
  });

  it("жёлоб размечен ТОЙ ЖЕ шкалой: засечка и плашка медианы стоят по своим долям верха оси", () => {
    /*
      Вторая половина того же шва, и тоже была не закрыта: сдвиг шкалы жёлоба на
      10 % и подмена числа в плашке медианы на номинальную медиану не роняли
      ничего — ни одна проверка не читала ни позицию засечки, ни само число.
    */
    const cell = makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000.00", shownPerSqm: "50000.00" }),
    });
    render(<ContractCostChart comparison={comparison} bucket="total" />);

    // Верх оси 100 000: засечка 50 000 обязана стоять на половине высоты.
    const gutter = screen.getByTestId("cost-chart-gutter");
    const tickAtHalf = Array.from(gutter.querySelectorAll("span")).find(
      (el) => el.style.bottom === "50%"
    );
    expect(tickAtHalf).toBeDefined();

    // И плашка медианы — там же.
    const chip = screen.getByTestId("cost-chart-median-line");
    expect(chip.style.bottom).toBe("50%");
  });

  it("DoD 21: подпись значения стоит у верха СПЛОШНОЙ части, а не у верха всей метки", () => {
    /*
      Проверяется на СНИЖЕНИИ — только там два уровня расходятся: сплошная часть
      кончается на приведённом 89 800, а метка целиком дотягивается до номинала
      100 000. Подпись, привязанная к верху метки, показала бы на номинал, то
      есть указывала бы не на то число, которое написано. Снятие `<LabelList>`
      до этого теста не роняло ничего.
    */
    const cell = makeBucketCell({
      shown: "89800.00",
      shownPerSqm: "89800.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });
    const { container } = renderChart(comparison);

    const texts = Array.from(container.querySelectorAll("svg text"));
    const label = texts.find((el) => /89.800/.test(el.textContent ?? ""));
    expect(label).toBeDefined();

    // Верх оси на этом наборе — 100 000 (максимум пула это номинал), полотно
    // 300 px. Верх СПЛОШНОЙ части: y = 300 × (1 − 89 800/100 000) = 30,6;
    // верх метки (номинал) — y = 0. Подпись стоит НАД сплошным верхом, то есть
    // её y лежит между ними и заведомо больше нуля.
    const y = Number.parseFloat(label!.getAttribute("y") ?? "NaN");
    expect(y).toBeGreaterThan(0);
    expect(y).toBeLessThanOrEqual(31);
  });

  it("промежуток к номиналу ЗАШТРИХОВАН, а не залит тем же цветом, что сплошная часть", () => {
    /*
      Тоже дыра, найденная снятием: и удаление `<pattern>` из `<defs>`, и замена
      штриховки на сплошной цвет проходили молча. Штриховка — единственное, что
      говорит «эта часть столбца про поправку, а не про цену» (DoD 20).
    */
    const cell = makeBucketCell({
      shown: "103700.00",
      shownPerSqm: "103700.00",
      nominalShown: "100000.00",
      nominalShownPerSqm: "100000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });
    const { container } = renderChart(comparison);

    const rects = Array.from(container.querySelectorAll(".recharts-rectangle"));
    expect(rects).toHaveLength(2);
    const gapFill = rects[1].getAttribute("fill") ?? "";
    expect(gapFill).toMatch(/^url\(#/);
    expect(gapFill).not.toBe(rects[0].getAttribute("fill"));

    // Узор, на который ссылается заливка, обязан существовать — иначе ссылка
    // висячая и промежуток рисуется прозрачным.
    const patternId = gapFill.slice(5, -1);
    expect(container.querySelector(`defs #${patternId}`)).not.toBeNull();
  });

  it("сетка по засечкам нарисована внутри прокрутки: столбцу справа не с чем было бы сличать высоту", () => {
    /*
      Жёлоб вынесен НАРУЖУ прокрутки (DoD 29), поэтому линии сетки — не
      украшение: у столбца, уехавшего вправо, без них не остаётся ничего, с чем
      сличать высоту. Макет их рисует; первая редакция компонента — нет.
    */
    const cell = makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
    });
    const { container } = renderChart(comparison);

    const grid = container.querySelectorAll(".recharts-cartesian-grid-horizontal line");
    // Засечек у верха оси 100 000 шесть (шаг 20 000), значит и линий столько же.
    expect(grid.length).toBeGreaterThanOrEqual(5);
    expect(container.querySelectorAll(".recharts-cartesian-grid-vertical line")).toHaveLength(0);
  });

  it("линия медианы нарисована НА ПОЛОТНЕ, а не только плашкой в жёлобе", () => {
    /*
      Та же дыра с другой стороны: удаление обоих `<ReferenceLine>` из графика
      набор тоже НЕ роняло — все проверки медианы читали плашку жёлоба, то есть
      собственную вёрстку компонента. Плашка объявляет число, но линия — это
      то, что человек сличает со столбцами, и DoD 22 требует именно её.
    */
    const cell = makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({ value: "50000.00", shownPerSqm: "50000.00" }),
    });
    const { container } = renderChart(comparison);

    // Верх оси 100 000, медиана 50 000 → ровно середина полотна: y = 150.
    const lines = container.querySelectorAll(".recharts-reference-line line");
    expect(lines).toHaveLength(1);
    expect(lines[0].getAttribute("y1")).toBe("150");
    expect(lines[0].getAttribute("y1")).toBe(lines[0].getAttribute("y2"));
  });

  it("при приведении на полотне ДВЕ линии медианы, а не одна", () => {
    const cell = makeBucketCell({
      shown: "100000.00",
      shownPerSqm: "100000.00",
      nominalShown: "80000.00",
      nominalShownPerSqm: "80000.00",
    });
    const comparison = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, cell)],
      totalMedian: makeMedian({
        value: "50000.00",
        shownPerSqm: "50000.00",
        nominalValue: "40000.00",
        nominalShownPerSqm: "40000.00",
      }),
    });
    const { container } = renderChart(comparison);

    expect(container.querySelectorAll(".recharts-reference-line line")).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
//  Прокрутка и минимальная ширина столбца (DoD 29)
//
//  ГРАНИЦА НАБЛЮДАЕМОСТИ: в jsdom `scrollWidth`/`clientWidth` всегда равны
//  нулю (тот же предел, что у теста закрепления первой колонки таблицы
//  сравнения на этом экране, см. `ComparePage.test.tsx`), поэтому тест ниже
//  НЕ измеряет фактическую прокрутку — он проверяет только то, ЧЕМ она
//  запрошена: контейнер с классом прокрутки, полотно шире одного столбца при
//  росте выборки, одинаковая минимальная ширина у каждого столбца, и жёлоб
//  оси, лежащий СНАРУЖИ этого контейнера (а не просто скрытый внутри него).
//  Фактическую прокрутку меряет прогон на стенде (план, задача 14).
// ---------------------------------------------------------------------------

describe("ContractCostChart — прокрутка", () => {
  it("DoD 29: скролл запрошен структурой — overflow-x-auto, растущее полотно, жёлоб оси снаружи", () => {
    const oneColumn = makeComparison({
      columns: [makeColumn({ contractId: 1, signedDate: "2025-01-01" })],
      totals: [makeTotalsCell(1, makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" }))],
    });
    const fiveColumns = makeComparison({
      columns: [1, 2, 3, 4, 5].map((id) => makeColumn({ contractId: id, signedDate: `202${id}-01-01` })),
      totals: [1, 2, 3, 4, 5].map((id) =>
        makeTotalsCell(id, makeBucketCell({ shown: "100000.00", shownPerSqm: "100000.00" }))
      ),
    });

    const { container: containerOne } = renderChart(oneColumn);
    const scrollOne = screen.getByTestId("cost-chart-scroll");
    expect(scrollOne.className).toMatch(/overflow-x-auto/);
    const innerOne = screen.getByTestId("cost-chart-inner");
    const minWidthOne = Number.parseFloat(String(innerOne.style.minWidth));
    expect(minWidthOne).toBeGreaterThan(0);
    // Инлайновой `width` у полотна быть НЕ должно: именно она вместе с
    // `minWidth: 100%` и убивала прокрутку — полотно всегда равнялось
    // контейнеру, и `scrollWidth` не превышал `clientWidth` никогда.
    expect(innerOne.style.width).toBe("");

    // Жёлоб оси — родной сиблинг прокручиваемого контейнера, а не его потомок.
    const gutterOne = screen.getByTestId("cost-chart-gutter");
    expect(scrollOne.contains(gutterOne)).toBe(false);
    expect(containerOne.contains(gutterOne)).toBe(true);

    render(<ContractCostChart comparison={fiveColumns} bucket="total" />);
    const innerMany = screen.getAllByTestId("cost-chart-inner").at(-1)!;
    const minWidthMany = Number.parseFloat(String(innerMany.style.minWidth));
    expect(innerMany.style.width).toBe("");

    // Нижняя граница ширины полотна растёт с числом договоров — из неё и берётся
    // прокрутка: без неё столбцы на большой выборке схлопнулись бы в волоски.
    expect(minWidthMany).toBeGreaterThan(minWidthOne * 3);

    /*
      ПОДПИСИ ДЕЛЯТ ту же ширину, что полотно, и делят поровну: `flex-1
      basis-0` у каждой, `w-full` у полосы, а пиксели в `minWidth` — только
      нижняя граница. Фиксированную ширину подписи ставить НЕЛЬЗЯ, и это
      измерено на стенде, а не выведено: полотно тянется на всю доступную
      ширину, поэтому при фиксированных 104 px центр последней подписи оказался
      на 645 px левее своего столбца. В jsdom объявленные ширины при этом
      сходились — раскладку он не считает, — то есть прежняя редакция этого
      теста была зелёной ровно на дефекте.

      Отсюда форма утверждений ниже: проверяется то, ЧЕМ раскладка запрошена —
      равная нижняя граница у всех подписей, `flex-1 basis-0`, отсутствие
      зазора, `w-full` у полосы и отсутствие фиксированных ширин. Фактическое
      совпадение центров меряет прогон на стенде (план, задача 14).
    */
    const columns = [1, 2, 3, 4, 5].map((id) => screen.getAllByTestId(`cost-chart-column-${id}`).at(-1)!);
    const minWidths = columns.map((el) => Number.parseFloat(String(el.style.minWidth)));
    expect(new Set(minWidths).size).toBe(1);
    expect(minWidths[0]).toBeGreaterThan(0);
    for (const el of columns) {
      expect(el.className).toMatch(/\bflex-1\b/);
      expect(el.className).toMatch(/\bbasis-0\b/);
      expect(el.style.width).toBe("");
    }

    const strip = columns[0].parentElement!;
    // `flex-1 basis-0` у подписей мертвы без `display:flex` у полосы: смени
    // `flex` на `grid`, и каждая подпись растянется на всю ширину. Это несущая
    // половина механизма, и в первой редакции этого утверждения не было.
    expect(strip.className).toMatch(/\bflex\b/);
    expect(strip.className).toMatch(/\bw-full\b/);
    expect(strip.className).not.toMatch(/\bgap-/);
    expect(strip.style.width).toBe("");

    // И вторая половина связки: полотно тоже тянется на всю ширину `.inner`.
    // Дай `ChartContainer` фиксированную ширину — и подписи снова разъедутся со
    // столбцами, молча.
    const chart = innerMany.querySelector('[data-slot="chart"]')!;
    expect(chart.className).toMatch(/\bw-full\b/);
  });
});

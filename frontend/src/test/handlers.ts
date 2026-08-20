import { http, HttpResponse } from "msw";

import { multiplyDecimalStrings } from "@/lib/decimal";

import {
  sampleAdminUsers,
  sampleComparison,
  sampleContractCard,
  sampleContractors,
  sampleContracts,
  sampleFailedJob,
  sampleImportJobs,
  sampleRunningJob,
  sampleObjects,
  sampleRateClasses,
  sampleInflationSeries,
  sampleInflationValues,
  sampleRateStandards,
  sampleReviewQueue,
  sampleAppSettings,
  sampleDashboard,
  sampleDashboardAttention,
  sampleDashboardAttentionClean,
  sampleMatrix,
  sampleMatrixCellDetail,
  sampleProjectPassport,
} from "./fixtures";
import type {
  Comparison,
  ComparisonBucketCell,
  ComparisonMedian,
  InflationSeries,
  ComparisonVatMode,
  EstimateRow,
  ImportJobStatus,
  ProjectPassport,
} from "@/types/domain";

/**
 * Мутируемое состояние обработчиков. Сбрасывается между тестами через
 * `resetHandlerState()` (вызов — в `setup.ts`), иначе загрузка из одного теста
 * влияла бы на статус задания в другом.
 */
interface HandlerState {
  /** Последовательность статусов, которую отдаёт поллинг задания. */
  jobStatuses: ImportJobStatus[];
  /** Сколько раз опросили статус — по нему выбирается следующий статус. */
  jobPolls: number;
  /** Ответ следующей загрузки: 202 (обычно), 200 (идемпотентно) либо 409. */
  uploadOutcome: "created" | "idempotent" | "conflict";
  /** Была ли последняя загрузка с `replace=true`. */
  lastUploadReplace: boolean;
  /** Пакетные решения Review: что пришло последним. */
  lastBatch: { ids: number[]; kind: string } | null;
  /** Пропустить ли одну строку в пакете — проверка ветки `skipped`. */
  batchSkipsFirst: boolean;
  /** Исход скачивания исходника: файл на месте, задания нет (404), удалён (410). */
  fileOutcome: "ok" | "missing" | "purged";
  /**
   * Текущее значение `passport_top_n`. Мутируется PATCH-ем настроек. Паспорт
   * проекта (Ф6 фазы 7) от него не зависит — поле осталось ради самих
   * настроек: экран Settings и его тесты по-прежнему читают/пишут это число.
   */
  passportTopN: number;
  /**
   * Исход паспорта ПРОЕКТА (Ф6 фазы 7, задача 6) — граничные случаи спеки
   * §2.6, §2.9: не только «нет сметы», но и «нет ТЭП», «сумма неизвестна»,
   * «сумма ровно ноль», «данные повреждены».
   */
  projectPassportOutcome:
    | "full"
    | "no-estimate"
    | "no-tep"
    | "empty-total"
    | "zero-total"
    | "corrupted"
    | "error";
  /** Отдать пустую матрицу — и различить «нет договоров» от «нет работ». */
  matrixOutcome: "rows" | "no-rows" | "no-columns" | "pending-review";
  /**
   * Сколько расценённых позиций ждут ручного матчинга. Отдельно от `matrixOutcome`:
   * пустой результат ПОИСКА может сосуществовать с непустой очередью, и именно на
   * этом сочетании экран раньше называл неверную причину.
   */
  positionsPendingReview: number;
  /** Последний запрос выгрузки: по нему тест проверяет, что фильтры доехали. */
  lastReportRequest: { report: string; params: Record<string, string> } | null;
  /**
   * Переопределяет `estimates[]` ответа `GET /contracts/:id`, когда задано
   * (находка ревью PR #16 — счёт решений в диалоге замены обязан быть тем,
   * что сервер держит В МОМЕНТ конфликта, а не тем, что застряло в проп-кэше
   * карточки у вызывающего). `null` — отдавать фикстуру как есть.
   */
  contractCardEstimatesOverride: EstimateRow[] | null;
  /**
   * Заваливает `GET /contracts/:id` 500-й ошибкой, когда `true` (находка
   * ревью PR #16, finding 3): рефетч карточки на 409 обязан провалиться, а не
   * молча вернуть фикстуру, — так тест видит именно ветку `isError`, а не
   * успешный ответ.
   */
  contractCardFails: boolean;
  /**
   * Сколько раз запрашивали диагностики второго таба.
   *
   * Проверка ОТСУТСТВИЯ запроса требует сигнала, общего для обеих ветвей: у
   * `member` данных на экране нет ни при безусловном хуке (сервер ответит
   * `403`), ни при условном, — поэтому «ничего не видно» ничего не доказывает.
   * Доказывает счётчик вызовов и утверждение «ровно 0».
   */
  attentionRequests: number;
  /**
   * Исход приведения: числа, либо один из двух кодов отказа. Управляемый, потому
   * что при отказе экран делает ВТОРОЙ, номинальный запрос (§2.9), и различить их
   * на неуправляемом хендлере было бы нечем.
   */
  inflationOutcome: "adjusted" | "missing-years" | "amendment-date";
  /** Сколько раз запрашивалось приведение — по нему видно перезапрос после правки. */
  inflationRequests: number;
  /** Ряды индексов: состояние, потому что тесты проверяют переходы архивации. */
  inflationSeries: InflationSeries[];
  /** Последнее тело запроса рядов — по нему тест видит, что ушло ОДНИМ запросом. */
  lastInflationBody: unknown;
  inflationPatches: number;
  /** Исход диагностик: обычный набор либо «всё сходится» (макет, панель ok). */
  attentionOutcome: "issues" | "clean";
}

export const handlerState: HandlerState = {
  jobStatuses: ["done"],
  jobPolls: 0,
  uploadOutcome: "created",
  lastUploadReplace: false,
  lastBatch: null,
  batchSkipsFirst: false,
  fileOutcome: "ok",
  passportTopN: sampleAppSettings.passport_top_n,
  projectPassportOutcome: "full",
  matrixOutcome: "rows",
  positionsPendingReview: 0,
  lastReportRequest: null,
  contractCardEstimatesOverride: null,
  contractCardFails: false,
  attentionRequests: 0,
  attentionOutcome: "issues",
  inflationOutcome: "adjusted",
  inflationRequests: 0,
  inflationSeries: sampleInflationSeries,
  lastInflationBody: null,
  inflationPatches: 0,
};

export function resetHandlerState() {
  handlerState.jobStatuses = ["done"];
  handlerState.jobPolls = 0;
  handlerState.uploadOutcome = "created";
  handlerState.lastUploadReplace = false;
  handlerState.lastBatch = null;
  handlerState.batchSkipsFirst = false;
  handlerState.fileOutcome = "ok";
  handlerState.passportTopN = sampleAppSettings.passport_top_n;
  handlerState.projectPassportOutcome = "full";
  handlerState.matrixOutcome = "rows";
  handlerState.positionsPendingReview = 0;
  handlerState.lastReportRequest = null;
  handlerState.contractCardEstimatesOverride = null;
  handlerState.contractCardFails = false;
  handlerState.attentionRequests = 0;
  handlerState.attentionOutcome = "issues";
}

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

/**
 * Паспорт проекта, приведённый к одному из граничных случаев `HandlerState.
 * projectPassportOutcome` (спека §2.6, §2.9). Возвращает НОВЫЙ объект — не
 * мутирует `sampleProjectPassport`, иначе один тест испортил бы фикстуру для
 * следующего.
 */
function projectPassportForOutcome(
  outcome: HandlerState["projectPassportOutcome"]
): ProjectPassport {
  const base = sampleProjectPassport;
  switch (outcome) {
    case "full":
      return base;

    case "no-estimate":
      // Договор без сметы (правило 8 CRUD) — карточка есть, файла ещё нет:
      // дерево статей остаётся полным скелетом, но без единой суммы. Без
      // сметы нет ни строк дерева разноса, ни действующих ручных решений —
      // сервер отдаёт их пустыми списками явно (не наследует из `base`,
      // иначе этот вариант описывал бы состояние, которого бэкенд не может
      // произвести: договор без сметы с деревом «Нераспределённого» и живым
      // ручным решением внутри него). `category_options` — справочник
      // классификатора целиком, от сметы не зависит и остаётся полным.
      return {
        ...base,
        estimate: null,
        totals: { ...base.totals, amount: null, per_sqm: null, delta_to_file_total: null },
        categories: base.categories.map((c) => ({
          ...c,
          total: null,
          rows: 0,
          rows_priced: 0,
          rows_not_finite: 0,
          share_pct: null,
          per_sqm: null,
          own: null,
          own_rows: 0,
          own_rows_priced: 0,
          own_rows_not_finite: 0,
          extras: [],
        })),
        unallocated: {
          ...base.unallocated,
          amount: null,
          rows: 0,
          rows_priced: 0,
          rows_not_finite: 0,
          share_pct: null,
          per_sqm: null,
          chapters: 0,
          rows_outside_structure: 0,
          extras: [],
          sections: [],
        },
        manual_assignments: [],
      };

    case "no-tep":
      // ТЭП объекта не заведены (спека §2.3 фазы 5) — площадей нет, и `per_sqm`
      // обязан стать `null` ВЕЗДЕ, а не только у объекта: делить на
      // отсутствующую площадь нельзя нигде (правило 6).
      return {
        ...base,
        object: {
          ...base.object,
          area_aboveground_sp: null,
          area_underground_sp: null,
          area_total_sp: null,
          // Полезная тоже `null`: имя исхода означает «ТЭП НЕ ЗАВЕДЕНЫ», то
          // есть ни одной площади. С появлением четвёртой колонки (спека
          // 2026-08-15) исход, унаследовавший полезную из `base`, описывал бы
          // ДРУГОЕ состояние — «заведена одна полезная», у которого свой,
          // отличный текст.
          area_useful_sp: null,
        },
        totals: { ...base.totals, per_sqm: null },
        categories: base.categories.map((c) => ({ ...c, per_sqm: null })),
        unallocated: { ...base.unallocated, per_sqm: null },
      };

    case "empty-total":
      // Сумма НЕИЗВЕСТНА (не ноль!), хотя файловый итог известен — сверка
      // (правило 12) требует ДВА известных операнда, поэтому дельта тоже
      // `null`. Статьи, у которых есть строки, показывают `total: null`, а не
      // ноль: строки есть, их сумма просто не сложилась.
      return {
        ...base,
        totals: {
          ...base.totals,
          amount: null,
          per_sqm: null,
          delta_to_file_total: null,
        },
        categories: base.categories.map((c) =>
          c.rows > 0
            ? { ...c, total: null, per_sqm: null, share_pct: null }
            : { ...c, share_pct: null }
        ),
        unallocated: { ...base.unallocated, amount: null, per_sqm: null, share_pct: null },
      };

    case "zero-total":
      // Сумма РОВНО ноль — знаменатель непригоден для доли (правило `_share_
      // pct`: `grand_total == 0` даёт `None` точно так же, как `None`), и это
      // ОТЛИЧИМО от «сумма неизвестна» выше: там `amount: null`, здесь —
      // настоящий `"0.00"`.
      return {
        ...base,
        totals: { ...base.totals, amount: "0.00" },
        categories: base.categories.map((c) => ({ ...c, share_pct: null })),
        unallocated: { ...base.unallocated, share_pct: null },
      };

    case "corrupted":
      // Мусор в исходных числах (открытый хвост Ф4, спека §1.11): часть строк
      // не `is_finite()`, и сверка с файлом расходится — обе аномалии видны
      // одновременно, третья причина «непонятно, что не так» не годится.
      return {
        ...base,
        totals: {
          ...base.totals,
          positions_rows_not_finite: 5,
          file_total_including_vat: "4750000.00",
          delta_to_file_total: "-50000.00",
        },
      };

    case "error":
      // Обрабатывается отдельной веткой хендлера ниже — сюда не доходит.
      return base;
  }
}

function jobPayload(status: ImportJobStatus) {
  return {
    ...sampleImportJobs[0],
    status,
    // Счётчики и смета появляются только у завершённого задания: до `done`
    // смета в БД ещё не лежит (§5).
    estimate_id: status === "done" ? 500 : null,
    error_text: status === "error" ? "Не удалось разобрать файл." : null,
  };
}

/**
 * Значение линии `shown_per_sqm` медианы «Итого» в режиме `single` — заранее
 * посчитанная строка-константа (Global Constraint 1 плана: денежная
 * арифметика в JS, включая `Number()` над деньгами, запрещена).
 *
 * Посчитано ВРУЧНУЮ для value медианы фикстуры `sampleComparison.totals_medians`
 * ("1501.88") при ЭФФЕКТИВНОЙ ставке показа `rate_preselected` ("20.00" —
 * подставляется обработчиком, когда запрос не задал `single_rate` своим
 * значением): `net_to_gross(net, target) = net * (100 + target) / 100`
 * (`backend/money/vat.py`), то есть 1501.88 * 120 / 100 = 180225.60 / 100 =
 * 1802.2560.
 *
 * Годится ТОЛЬКО для этого значения медианы: если фикстура когда-нибудь
 * изменит "1501.88", константу нужно пересчитать вручную ещё раз — здесь
 * нет обратной проверки.
 */
const TOTALS_MEDIAN_SINGLE_SHOWN_PER_SQM = "1802.2560";

/**
 * Добавляет `shown_per_sqm` медиане «Итого» по правилу присутствия спеки
 * диаграммы §2.8 (DoD 22б): ключ ЕСТЬ при `net` и `single`, ЕГО НЕТ при `own`.
 * В режиме `net` значение равно самому `value` (сервер отражает нетто без
 * пересчёта — `_shown_per_sqm_value`); в режиме `single` — заранее посчитанная
 * константа выше, `null`, если `value` сам `null` (правило самосогласованности
 * задачи: «значение `null`, только если `value` равно `null`»).
 */
function totalsMedianWithShownPerSqm(
  median: ComparisonMedian,
  vatMode: ComparisonVatMode
): ComparisonMedian {
  if (vatMode === "own") return median;
  const shownPerSqm =
    median.value === null
      ? null
      : vatMode === "net"
        ? median.value
        : TOTALS_MEDIAN_SINGLE_SHOWN_PER_SQM;
  return { ...median, shown_per_sqm: shownPerSqm };
}

/** `totalsMedianWithShownPerSqm` над ВСЕМИ трёх корзинами `totals_medians`. */
function totalsMediansWithMode(
  medians: Comparison["totals_medians"],
  vatMode: ComparisonVatMode
): Comparison["totals_medians"] {
  return {
    base: totalsMedianWithShownPerSqm(medians.base, vatMode),
    amendments: totalsMedianWithShownPerSqm(medians.amendments, vatMode),
    total: totalsMedianWithShownPerSqm(medians.total, vatMode),
  };
}

/**
 * Приведённый агрегат сравнения — из номинального, УМНОЖЕНИЕМ (спека §2.5).
 *
 * Множители у двух рядов РАЗНЫЕ намеренно: одинаковые означали бы, что селектор
 * меняет подпись, не меняя чисел, — та же ложь, только незаметнее (дефект макета,
 * §7 спеки). Поэтому тест «смена ряда меняет и подпись, и числа» на этой фикстуре
 * доказуем.
 *
 * Первая колонка получает РАСХОЖДЕНИЕ множителей (`inflation_coefficient: null`
 * плюс `inflation_factors`): случай «в договоре ДГП 2024 года и ДС 2026-го» на
 * стенде не воспроизводится вовсе — допсоглашений там ноль, — и без фикстуры чип
 * «разные» остался бы непроверенным.
 *
 * **Номинал (спека §2.8, §2.10, DoD 13/17)** приезжает ТОЛЬКО здесь — это
 * единственный путь, где приведение вообще посчитано (`seriesId` задан). У
 * `totals[]` номинал — денежное подмножество ИСХОДНОЙ (нескаленной) ячейки
 * `base.totals[]`, БЕЗ `state`/`deviation_pct` (`_nominal_bucket_cell_dict`);
 * строки дерева (`rows[].cells[]`) номинала не получают вовсе (DoD 17), и
 * `scaleCells` их сериализатор НЕ трогает. У `totals_medians[bucket]` номинал —
 * `{value, shown_per_sqm?}` той же, ДОНОМИНАЛЬНОЙ, медианы (`base.totals_medians`,
 * которая уже несёт `shown_per_sqm` по правилу присутствия выше).
 *
 * **`totals_medians` ОБЯЗАН масштабироваться тем же `factor`, что и `totals`**
 * (смежный дефект, найденный ревью: до этой правки медиана приезжала
 * номинальной, то есть равной самой себе после приведения — столбцы сдвигались,
 * линия медианы нет, что противоречит DoD 16 и обесценивает DoD 23). Множитель
 * применяется к `value` И к `shown_per_sqm` одинаково: `net_to_gross` линеен по
 * нетто (`net_to_gross(net·k, ставка) = net_to_gross(net, ставка)·k`), поэтому
 * масштабирование обеих величин ОДНИМ И ТЕМ ЖЕ точным умножением строк
 * (`multiplyDecimalStrings`, тот же приём, что у `scaleCell` ниже) даёт то же
 * число, что дал бы пересчёт `net_to_gross` от уже приведённого нетто — без
 * повторного деления и без второй захардкоженной константы.
 */
function adjustedComparison(
  base: Comparison,
  seriesId: number,
  targetMonth: string | null
): Comparison {
  const factor = seriesId === 2 ? "1.2670" : "1.1744";
  const seriesName =
    sampleInflationSeries.find((row) => row.id === seriesId)?.name ?? "неизвестный ряд";
  const month = targetMonth ?? "2026-08";

  const scale = (value: string | null): string | null =>
    value === null ? null : multiplyDecimalStrings(value, factor);

  const scaleCell = (cell: ComparisonBucketCell): ComparisonBucketCell => ({
    ...cell,
    net: scale(cell.net),
    shown: scale(cell.shown),
    net_per_sqm: scale(cell.net_per_sqm),
    shown_per_sqm: scale(cell.shown_per_sqm),
  });

  const scaleCells = (cells: Comparison["totals"]): Comparison["totals"] =>
    cells.map((cell) => ({
      ...cell,
      base: scaleCell(cell.base),
      amendments: scaleCell(cell.amendments),
      total: scaleCell(cell.total),
    }));

  /** Денежное подмножество НОМИНАЛЬНОЙ ячейки — вход `nominal` у `totals[]`. */
  const nominalCellDict = (cell: ComparisonBucketCell) => ({
    net: cell.net,
    shown: cell.shown,
    net_per_sqm: cell.net_per_sqm,
    shown_per_sqm: cell.shown_per_sqm,
  });

  /** `scaleCell` ПЛЮС `nominal` — ТОЛЬКО для `totals[]` (DoD 17: строки его не несут). */
  const scaleCellWithNominal = (cell: ComparisonBucketCell): ComparisonBucketCell => ({
    ...scaleCell(cell),
    nominal: nominalCellDict(cell),
  });

  const scaleTotalsCells = (cells: Comparison["totals"]): Comparison["totals"] =>
    cells.map((cell) => ({
      ...cell,
      base: scaleCellWithNominal(cell.base),
      amendments: scaleCellWithNominal(cell.amendments),
      total: scaleCellWithNominal(cell.total),
    }));

  /** Денежное подмножество НОМИНАЛЬНОЙ медианы — вход `nominal` у `totals_medians`. */
  const nominalMedianDict = (median: ComparisonMedian): { value: string | null; shown_per_sqm?: string | null } => {
    const out: { value: string | null; shown_per_sqm?: string | null } = { value: median.value };
    if (median.shown_per_sqm !== undefined) out.shown_per_sqm = median.shown_per_sqm;
    return out;
  };

  /** Медиана «Итого» масштабированная ПЛЮС `nominal` доскаленной (см. докстроку выше). */
  const scaleTotalsMedian = (median: ComparisonMedian): ComparisonMedian => ({
    ...median,
    value: scale(median.value),
    ...(median.shown_per_sqm !== undefined ? { shown_per_sqm: scale(median.shown_per_sqm) } : {}),
    nominal: nominalMedianDict(median),
  });

  return {
    ...base,
    caption: `${base.caption} Цены приведены к августу 2026 по ряду «${seriesName}».`,
    columns: base.columns.map((column, index) =>
      index === 0
        ? {
            ...column,
            inflation_coefficient: null,
            inflation_factors: [
              { label: "ДГП", coefficient: factor },
              { label: "ДС №1", coefficient: "1.0000" },
            ],
          }
        : { ...column, inflation_coefficient: factor }
    ),
    rows: base.rows.map((row) => ({
      ...row,
      cells: scaleCells(row.cells),
      medians: {
        base: { ...row.medians.base, value: scale(row.medians.base.value) },
        amendments: { ...row.medians.amendments, value: scale(row.medians.amendments.value) },
        total: { ...row.medians.total, value: scale(row.medians.total.value) },
      },
    })),
    totals: scaleTotalsCells(base.totals),
    totals_medians: {
      base: scaleTotalsMedian(base.totals_medians.base),
      amendments: scaleTotalsMedian(base.totals_medians.amendments),
      total: scaleTotalsMedian(base.totals_medians.total),
    },
    inflation: {
      series_id: seriesId,
      series_name: seriesName,
      series_note: sampleInflationSeries.find((row) => row.id === seriesId)?.note ?? null,
      series_updated_at: "2026-01-12T10:00:00+03:00",
      target_month: month,
      has_forecast: true,
      used_years: sampleInflationValues[seriesId] ?? [],
    },
  };
}

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  // Auth
  http.get("/api/auth/me", () =>
    HttpResponse.json({ id: 1, email: "test@example.com", role: "admin" })
  ),
  http.post("/api/auth/login", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/logout", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/refresh", () => HttpResponse.json({ status: "ok" })),

  // Units
  http.get("/api/units", () =>
    HttpResponse.json([
      { id: 1, code: "TON", name: "Тонна", symbol: "т", dimension: "mass", base_unit_id: null },
      { id: 3, code: "M3", name: "Куб. метр", symbol: "м³", dimension: "volume", base_unit_id: null },
      { id: 5, code: "M2", name: "Кв. метр", symbol: "м²", dimension: "area", base_unit_id: null },
    ])
  ),

  // Admin: пользователи
  http.get("/api/admin/users", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const pageNo = Number(url.searchParams.get("page") ?? 1) || 1;
    const page_size = Number(url.searchParams.get("page_size") ?? 20) || 20;
    const filtered = q
      ? sampleAdminUsers.filter((u) => u.email.toLowerCase().includes(q))
      : sampleAdminUsers;
    const start = (pageNo - 1) * page_size;
    return HttpResponse.json({
      items: filtered.slice(start, start + page_size),
      total: filtered.length,
      page: pageNo,
      page_size,
    });
  }),
  http.post("/api/admin/users", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      {
        id: 100,
        email: body.email ?? "new@example.com",
        role: body.role ?? "member",
        is_active: body.is_active ?? true,
      },
      { status: 201 }
    );
  }),
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(body, "role") && body.role === null) {
      return HttpResponse.json({ detail: "Поле role не может быть null" }, { status: 422 });
    }
    if (Object.prototype.hasOwnProperty.call(body, "is_active") && body.is_active === null) {
      return HttpResponse.json({ detail: "Поле is_active не может быть null" }, { status: 422 });
    }
    return HttpResponse.json({
      id: Number(params.id),
      email: "a.petrov@example.com",
      role: (body.role as string) ?? "admin",
      is_active: (body.is_active as boolean) ?? true,
    });
  }),
  http.post("/api/admin/users/:id/reset-password", ({ params }) =>
    HttpResponse.json({ id: Number(params.id), email: "a.petrov@example.com", password: "Xk7m-Pq9L-vf2Z" })
  ),

  // --- Справочники (фаза 5) ---
  http.get("/api/v1/rate-classes", () => HttpResponse.json(sampleRateClasses)),
  http.post("/api/v1/rate-classes", async ({ request }) => {
    const body = (await request.json()) as { title: string; description?: string | null };
    if (sampleRateClasses.some((c) => c.title === body.title)) {
      return HttpResponse.json(
        { detail: "Класс объектов с таким названием уже есть." },
        { status: 409 }
      );
    }
    return HttpResponse.json(
      {
        id: 3,
        title: body.title,
        description: body.description ?? null,
        contracts_count: 0,
        objects_count: 0,
        standards_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),
  http.delete("/api/v1/rate-classes/:id", ({ params }) => {
    const rateClass = sampleRateClasses.find((c) => c.id === Number(params.id));
    if (rateClass && (rateClass.contracts_count > 0 || rateClass.standards_count > 0)) {
      return HttpResponse.json(
        {
          detail: `Класс «${rateClass.title}» удалить нельзя: на него ссылаются договоры (${rateClass.contracts_count}) и нормативы (${rateClass.standards_count}).`,
        },
        { status: 409 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/v1/objects", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    const items = q
      ? sampleObjects.filter((o) =>
          `${o.title} ${o.address}`.toLowerCase().includes(q)
        )
      : sampleObjects;
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/objects/:id", ({ params }) => {
    const found = sampleObjects.find((o) => o.id === Number(params.id));
    if (!found) {
      return HttpResponse.json({ detail: `Объект ${params.id} не найден.` }, { status: 404 });
    }
    return HttpResponse.json(found);
  }),
  http.post("/api/v1/objects", async ({ request }) => {
    const body = (await request.json()) as { title: string };
    return HttpResponse.json(
      {
        id: 11,
        title: body.title,
        address: "",
        rate_class_id: null,
        rate_class_title: null,
        area_aboveground_sp: null,
        area_underground_sp: null,
        area_total_sp: null,
        area_useful_sp: null,
        contracts_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),
  http.patch("/api/v1/objects/:id", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const found = sampleObjects.find((o) => o.id === Number(params.id));
    return HttpResponse.json({ ...(found ?? sampleObjects[0]), ...body, id: Number(params.id) });
  }),

  http.get("/api/v1/contractors", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    const items = q
      ? sampleContractors.filter((c) => `${c.title} ${c.inn}`.toLowerCase().includes(q))
      : sampleContractors;
    return HttpResponse.json(page(items));
  }),
  http.post("/api/v1/contractors", async ({ request }) => {
    const body = (await request.json()) as { title: string; inn: string };
    return HttpResponse.json(
      {
        id: 21,
        title: body.title,
        inn: body.inn,
        address: "",
        accreditation: "",
        contracts_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),

  // --- Договоры ---
  http.get("/api/v1/contracts", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const rateClassId = url.searchParams.get("rate_class_id");
    let items = sampleContracts;
    if (q) {
      items = items.filter((c) =>
        [c.contract_number, c.title ?? "", c.object_title, c.contractor_title]
          .join(" ")
          .toLowerCase()
          .includes(q)
      );
    }
    if (rateClassId) {
      items = items.filter((c) => c.rate_class_id === Number(rateClassId));
    }
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/contracts/:id/import-jobs", () =>
    HttpResponse.json([...sampleImportJobs, sampleFailedJob, sampleRunningJob])
  ),
  http.get("/api/v1/contracts/:id", ({ params }) => {
    if (Number(params.id) !== sampleContractCard.id) {
      return HttpResponse.json({ detail: "Договор не найден." }, { status: 404 });
    }
    if (handlerState.contractCardFails) {
      return HttpResponse.json(
        { detail: "Не удалось загрузить карточку договора." },
        { status: 500 }
      );
    }
    if (handlerState.contractCardEstimatesOverride === null) {
      return HttpResponse.json(sampleContractCard);
    }
    return HttpResponse.json({
      ...sampleContractCard,
      estimates: handlerState.contractCardEstimatesOverride,
    });
  }),
  http.post("/api/v1/contracts", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      { ...sampleContractCard, id: 102, contract_number: body.contract_number },
      { status: 201 }
    );
  }),
  http.patch("/api/v1/contracts/:id", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...sampleContractCard, ...body });
  }),
  http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 })),

  // --- Загрузка сметы и поллинг ---
  http.post("/api/v1/estimates/upload", async ({ request }) => {
    // `request.formData()` здесь неприменим: под jsdom `File` не тот, который
    // признаёт undici внутри msw, и парсер multipart падает на ассерте
    // (`webidl.is.File`). Тело читается текстом, а нужное поле — по имени: это
    // дефект окружения, и обходить его в обработчике правильнее, чем менять
    // боевой транспорт под тест.
    const body = await request.text();
    handlerState.lastUploadReplace = /name="replace"[\s\S]*?\btrue\b/.test(body);

    if (handlerState.uploadOutcome === "conflict" && !handlerState.lastUploadReplace) {
      return HttpResponse.json(
        {
          detail:
            "Смета уже загружена (estimate_id=500); для замены повторите запрос с replace=true.",
        },
        { status: 409 }
      );
    }
    if (handlerState.uploadOutcome === "idempotent") {
      // 200, а не 202: ничего не создано и ничего не запущено (§5, правило 1).
      return HttpResponse.json(jobPayload("done"), { status: 200 });
    }
    return HttpResponse.json(jobPayload(handlerState.jobStatuses[0] ?? "pending"), {
      status: 202,
    });
  }),
  http.get("/api/v1/import-jobs/:id/file", () => {
    if (handlerState.fileOutcome === "missing") {
      return HttpResponse.json({ detail: "Задание импорта не найдено." }, { status: 404 });
    }
    if (handlerState.fileOutcome === "purged") {
      // 410: запись задания жива (это аудит), а файл удалён ретенцией (§8).
      return HttpResponse.json(
        { detail: "Файл задания удалён при очистке хранилища." },
        { status: 410 }
      );
    }
    return new HttpResponse("PK-fake-xlsx", {
      headers: { "Content-Type": "application/octet-stream" },
    });
  }),
  http.get("/api/v1/import-jobs/:id", () => {
    const index = Math.min(handlerState.jobPolls, handlerState.jobStatuses.length - 1);
    handlerState.jobPolls += 1;
    return HttpResponse.json(jobPayload(handlerState.jobStatuses[index]));
  }),

  // --- Каталог и Review ---
  http.get("/api/v1/catalog-positions", () =>
    HttpResponse.json([
      { id: 800, standard_job_title: "Кладка кирпичная", unit_id: 3, unit_code: "M3", unit_name: "Куб. метр" },
    ])
  ),
  http.get("/api/v1/review/queue", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const withoutUnit = url.searchParams.get("without_unit") === "true";
    const sort = url.searchParams.get("sort") ?? "positions";

    let items = [...sampleReviewQueue];
    if (q) items = items.filter((i) => i.standard_job_title.toLowerCase().includes(q));
    if (withoutUnit) items = items.filter((i) => i.unit_id === null);
    items.sort((a, b) =>
      sort === "title"
        ? a.standard_job_title.localeCompare(b.standard_job_title)
        : b.position_count - a.position_count
    );
    return HttpResponse.json({ items, total: items.length, page: 1, page_size: 50 });
  }),
  http.get("/api/v1/review/targets", () =>
    HttpResponse.json([
      { id: 801, standard_job_title: "Стяжка цементная", unit_id: 5, unit_code: "M2", unit_name: "Кв. метр" },
    ])
  ),
  http.post("/api/v1/review/:id/merge", async ({ params, request }) => {
    const body = (await request.json()) as { target_id: number };
    return HttpResponse.json({
      to_review_id: Number(params.id),
      target: {
        id: body.target_id,
        standard_job_title: "Стяжка цементная",
        normalized_job_title: "стяжка цементный",
        kind: "POSITION",
        unit_id: 5,
        unit_code: "M2",
        unit_name: "Кв. метр",
      },
      moved_positions: 42,
    });
  }),
  http.post("/api/v1/review/:id/kind", async ({ params, request }) => {
    const body = (await request.json()) as { kind: string };
    return HttpResponse.json({
      id: Number(params.id),
      standard_job_title: "Стяжка неведомая",
      normalized_job_title: "стяжка неведомый",
      kind: body.kind,
      unit_id: 5,
      unit_code: "M2",
      unit_name: "Кв. метр",
    });
  }),
  http.post("/api/v1/review/batch-kind", async ({ request }) => {
    const body = (await request.json()) as { ids: number[]; kind: string };
    handlerState.lastBatch = body;
    const ids = [...body.ids].sort((a, b) => a - b);
    if (handlerState.batchSkipsFirst && ids.length > 0) {
      return HttpResponse.json({
        kind: body.kind,
        applied: ids.slice(1),
        skipped: [
          {
            id: ids[0],
            reason: `Каталожная строка ${ids[0]} имеет kind=POSITION, а операция применима к TO_REVIEW.`,
          },
        ],
      });
    }
    return HttpResponse.json({ kind: body.kind, applied: ids, skipped: [] });
  }),

  // --- Нормативы ---
  // --- Ряды индексов инфляции (спека 2026-08-18 §2.12) ----------------------
  //
  // Состояние живёт в `handlerState.inflationSeries`, потому что тесты вкладки
  // проверяют ПЕРЕХОДЫ: «В архив» обязан увести ряд из активных, а «Вернуть в
  // активные» — вернуть. На неизменяемой фикстуре второй шаг был бы недоказуем.
  http.get("/api/v1/inflation-series", ({ request }) => {
    const includeArchived = new URL(request.url).searchParams.get("include_archived");
    const rows = handlerState.inflationSeries;
    return HttpResponse.json(includeArchived ? rows : rows.filter((row) => row.is_active));
  }),

  http.get("/api/v1/inflation-series/:id/values", ({ params }) =>
    HttpResponse.json(sampleInflationValues[Number(params.id)] ?? [])
  ),

  http.post("/api/v1/inflation-series", async ({ request }) => {
    const body = (await request.json()) as {
      name: string;
      note: string | null;
      values: { year: number }[];
    };
    handlerState.lastInflationBody = body;
    const years = body.values.map((value) => value.year);
    const created: InflationSeries = {
      id: 90 + handlerState.inflationSeries.length,
      name: body.name,
      note: body.note,
      is_active: true,
      year_from: years.length > 0 ? Math.min(...years) : null,
      year_to: years.length > 0 ? Math.max(...years) : null,
      value_count: years.length,
      created_at: "2026-08-19T12:00:00+03:00",
      updated_at: "2026-08-19T12:00:00+03:00",
    };
    handlerState.inflationSeries = [...handlerState.inflationSeries, created];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch("/api/v1/inflation-series/:id", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastInflationBody = body;
    handlerState.inflationPatches += 1;
    const id = Number(params.id);
    const target = handlerState.inflationSeries.find((row) => row.id === id);
    if (!target) return new HttpResponse(null, { status: 404 });
    // Архивный ряд правится ТОЛЬКО телом `{is_active: true}` в одиночку (§2.10):
    // хендлер повторяет это правило, иначе тест двух шагов проходил бы и на
    // клиенте, который шлёт разморозку вместе с правкой.
    const unfreezeOnly =
      body.is_active === true && Object.keys(body).length === 1;
    if (!target.is_active && !unfreezeOnly) {
      return HttpResponse.json(
        { detail: `Ряд «${target.name}» в архиве и не правится.` },
        { status: 409 }
      );
    }
    const updated: InflationSeries = {
      ...target,
      ...(typeof body.name === "string" ? { name: body.name } : {}),
      ...("note" in body ? { note: (body.note as string | null) ?? null } : {}),
      ...(typeof body.is_active === "boolean" ? { is_active: body.is_active } : {}),
    };
    handlerState.inflationSeries = handlerState.inflationSeries.map((row) =>
      row.id === id ? updated : row
    );
    return HttpResponse.json(updated);
  }),

  http.get("/api/v1/rate-standards", ({ request }) => {
    const url = new URL(request.url);
    const onDate = url.searchParams.get("on_date");
    let items = sampleRateStandards;
    if (onDate) {
      // Тот же полуинтервал [valid_from, valid_to), что у EXCLUDE и VIEW.
      items = items.filter(
        (s) => s.valid_from <= onDate && (s.valid_to === null || s.valid_to > onDate)
      );
    }
    return HttpResponse.json(page(items));
  }),
  http.post("/api/v1/rate-standards", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...sampleRateStandards[0], id: 302, ...body }, { status: 201 });
  }),
  http.post("/api/v1/rate-standards/:id/reapprove", async ({ params, request }) => {
    const body = (await request.json()) as { valid_from: string; standard_unit_rate?: string };
    const previous = sampleRateStandards.find((s) => s.id === Number(params.id));
    return HttpResponse.json({
      previous: { ...previous, valid_to: body.valid_from },
      current: {
        ...previous,
        id: 303,
        standard_unit_rate: body.standard_unit_rate ?? "1075.35475",
        valid_from: body.valid_from,
        valid_to: null,
      },
    });
  }),
  http.delete("/api/v1/rate-standards/:id", () => new HttpResponse(null, { status: 204 })),

  // --- Настройки (фаза 6, §7.4) ---
  http.get("/api/v1/settings", () =>
    HttpResponse.json({ ...sampleAppSettings, passport_top_n: handlerState.passportTopN })
  ),
  http.patch("/api/v1/settings", async ({ request }) => {
    const body = (await request.json()) as { passport_top_n: number };
    // Диапазон проверяет сервер, и его отказ объясняет причину (раскладка экрана
    // паспорта фазы 6). Обработчик воспроизводит именно это поведение, а не
    // «принимает всё».
    if (
      !Number.isInteger(body.passport_top_n) ||
      body.passport_top_n < sampleAppSettings.passport_top_n_min ||
      body.passport_top_n > sampleAppSettings.passport_top_n_max
    ) {
      return HttpResponse.json(
        {
          detail:
            `Число ключевых расценок должно быть от ${sampleAppSettings.passport_top_n_min} до ` +
            `${sampleAppSettings.passport_top_n_max}. Верхняя граница — не прихоть: она ` +
            "подобрана под раскладку экрана паспорта фазы 6, а не взята произвольно.",
        },
        { status: 422 }
      );
    }
    handlerState.passportTopN = body.passport_top_n;
    return HttpResponse.json({ ...sampleAppSettings, passport_top_n: body.passport_top_n });
  }),

  // --- Аналитика (фаза 6, §6, §7.4–§7.5) ---
  // Паспорт проекта по статьям классификатора (Ф6 фазы 7, задача 6).
  http.get("/api/v1/analytics/project-passport/:contractId", () => {
    if (handlerState.projectPassportOutcome === "error") {
      return HttpResponse.json(
        { detail: "Не удалось построить паспорт проекта." },
        { status: 500 }
      );
    }
    return HttpResponse.json(projectPassportForOutcome(handlerState.projectPassportOutcome));
  }),

  // Ручной разнос разделов по статьям (спека разноса §2.6). Ответ — сводка
  // изменений, НЕ паспорт (форма паспорта объявлена ровно один раз в фикстуре).
  http.put(
    "/api/v1/estimates/:estimateId/category-overrides/:positionItemId",
    async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as {
        work_category_id?: unknown;
        note?: string | null;
      };
      if (typeof body.work_category_id !== "number") {
        return HttpResponse.json(
          { detail: "Поле work_category_id обязательно." },
          { status: 422 }
        );
      }
      return HttpResponse.json({
        chapters_updated: 1,
        additional_works_updated: 0,
        chapters_manual: 1,
      });
    }
  ),
  http.delete("/api/v1/estimates/:estimateId/category-overrides/:positionItemId", () =>
    HttpResponse.json({ chapters_updated: 1, additional_works_updated: 0, chapters_manual: 0 })
  ),

  http.get("/api/v1/analytics/dashboard", () => HttpResponse.json(sampleDashboard)),

  /**
   * Диагностики второго таба. Счётчик вызовов инкрементируется ДО ветвления по
   * исходу: тест «`member` не отправляет ни одного запроса» смотрит именно на
   * него, а не на содержимое ответа.
   */
  http.get("/api/v1/analytics/dashboard/attention", () => {
    handlerState.attentionRequests += 1;
    return HttpResponse.json(
      handlerState.attentionOutcome === "clean"
        ? sampleDashboardAttentionClean
        : sampleDashboardAttention
    );
  }),

  http.get("/api/v1/analytics/matrix", ({ request }) => {
    const url = new URL(request.url);
    if (handlerState.matrixOutcome === "no-columns") {
      return HttpResponse.json({ ...sampleMatrix, columns: [], rows: [], total: 0 });
    }
    if (handlerState.matrixOutcome === "no-rows") {
      return HttpResponse.json({ ...sampleMatrix, rows: [], total: 0 });
    }
    if (handlerState.matrixOutcome === "pending-review") {
      // Сметы загружены и расценены, но каталог ещё не разобран — состояние,
      // которое нашёл прогон стенда фазы 6.
      return HttpResponse.json({
        ...sampleMatrix,
        rows: [],
        total: 0,
        positions_pending_review: 1830,
      });
    }
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const rows = q
      ? sampleMatrix.rows.filter((r) => r.job_title.toLowerCase().includes(q))
      : sampleMatrix.rows;
    return HttpResponse.json({
      ...sampleMatrix,
      rows,
      total: rows.length,
      page: Number(url.searchParams.get("page") ?? 1),
      // Счётчик очереди НЕ зависит от `q`: он про выборку, а не про поиск.
      positions_pending_review: handlerState.positionsPendingReview,
    });
  }),

  http.get("/api/v1/analytics/matrix/cell", () => HttpResponse.json(sampleMatrixCellDetail)),

  /**
   * Сравнение договоров (спека 2026-08-17, задача 8). Выборка (`ids`/`all` +
   * фильтры) игнорируется намеренно — фикстура одна и та же, тест страницы
   * проверяет клиентское поведение (дерево, переключатели, URL), а не то,
   * что сервер умеет фильтровать (это покрыто `test_comparison_api.py`).
   * `vat_mode`/`single_rate` эхом отражаются в ответе — иначе тест
   * восстановления режима из URL не смог бы отличить «страница прочитала
   * URL» от «страница показывает то, что всегда приходит с сервера».
   */
  http.get("/api/v1/analytics/comparison", ({ request }) => {
    const url = new URL(request.url);
    const vatMode = (url.searchParams.get("vat_mode") ?? "own") as ComparisonVatMode;
    const singleRateParam = url.searchParams.get("single_rate");
    const singleRate =
      vatMode === "single" ? (singleRateParam ?? sampleComparison.rate_preselected) : null;

    const seriesId = url.searchParams.get("inflation_series_id");
    const base: Comparison = {
      ...sampleComparison,
      vat_mode: vatMode,
      single_rate: singleRate,
      // Правило присутствия §2.8/DoD 22б не зависит от приведения: ключ
      // `shown_per_sqm` обязан появляться в `net`/`single` и без него — сам
      // факт запроса приведения тут ни при чём (см. докстроку хелпера).
      totals_medians: totalsMediansWithMode(sampleComparison.totals_medians, vatMode),
    };
    if (!seriesId) return HttpResponse.json(base);

    handlerState.inflationRequests += 1;

    // Отказ приведения — структурированный `422` с кодом и контекстом (§2.12).
    // Управляется `handlerState.inflationOutcome`, потому что экран обязан
    // показать НОМИНАЛЬНЫЙ вариант с баннером, а это второй запрос: на
    // неуправляемом хендлере отличить его от первого было бы нечем.
    if (handlerState.inflationOutcome === "missing-years") {
      return HttpResponse.json(
        {
          detail: {
            code: "missing_inflation_years",
            message: "Не заданы коэффициенты за годы: 2024, 2026.",
            missing_years: [2024, 2026],
          },
        },
        { status: 422 }
      );
    }
    if (handlerState.inflationOutcome === "amendment-date") {
      return HttpResponse.json(
        {
          detail: {
            code: "amendment_date_missing",
            message: "У допсоглашений нет собственной даты подготовки: ГП-0007 ДС №1.",
            estimate_ids: [7],
          },
        },
        { status: 422 }
      );
    }

    return HttpResponse.json(
      adjustedComparison(base, Number(seriesId), url.searchParams.get("target_month"))
    );
  }),

  // --- Выгрузки §7.6 ---
  //
  // Отдаём непустой blob с настоящим media type: экран не разбирает содержимое, но
  // разбирает отказы, а `responseType: "blob"` меняет форму ответа axios — на
  // JSON-заглушке этого пути было бы не видно. Параметры запроса сохраняются в
  // состоянии, чтобы тест мог проверить, что фильтры доехали до сервера.
  http.get("/api/v1/reports/contract-summary", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "contract-summary",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
  /**
   * Третий файл §7.6 (`AGENTS.md` v6.8) — выгрузка сравнения. Параметры
   * сохраняются тем же способом, что у двух других: тест страницы сравнения
   * проверяет, что лист запрошен с ТЕМ ЖЕ режимом НДС, что открыт на экране,
   * иначе числа файла и экрана разошлись бы (спека §2.7).
   */
  http.get("/api/v1/reports/comparison", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "comparison",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
  http.get("/api/v1/reports/bank-comparison", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "bank-comparison",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
];

import type { AdminUser } from "@/types/admin";
import type {
  AppSettings,
  InflationSeries,
  InflationSeriesValue,
  Comparison,
  ComparisonBucketCell,
  ComparisonCell,
  ComparisonIncompleteReason,
  ComparisonRow,
  ContractCard,
  Dashboard,
  DashboardAttention,
  ContractImportJob,
  ContractRow,
  Contractor,
  ObjectItem,
  RateClass,
  RateStandard,
  Matrix,
  MatrixCellDetail,
  MatrixColumn,
  MatrixRow,
  ProjectPassport,
  ReviewQueueItem,
  TenderCard,
  TenderRow,
} from "@/types/domain";

export const sampleAdminUsers: AdminUser[] = [
  { id: 1, email: "a.petrov@example.com", role: "admin", is_active: true, created_at: "2026-08-01T10:00:00Z" },
  { id: 2, email: "i.orlova@example.com", role: "member", is_active: true, created_at: "2026-08-01T11:00:00Z" },
];

// ---------------------------------------------------------------------------
//  Домен фазы 5. Данные синтетические: реальные сметы из samples/ никуда не
//  выносятся, включая суммы (политика docs/phase0-input-data.md).
// ---------------------------------------------------------------------------

export const sampleRateClasses: RateClass[] = [
  {
    id: 1,
    title: "Жилые дома",
    description: "Многоквартирные жилые дома",
    contracts_count: 2,
    objects_count: 1,
    standards_count: 1,
    created_at: "2026-01-10T09:00:00Z",
    updated_at: "2026-01-10T09:00:00Z",
  },
  {
    id: 2,
    title: "Промышленные",
    description: null,
    contracts_count: 0,
    objects_count: 0,
    standards_count: 0,
    created_at: "2026-01-11T09:00:00Z",
    updated_at: "2026-01-11T09:00:00Z",
  },
];

export const sampleObjects: ObjectItem[] = [
  {
    id: 10,
    title: "ЖК Северный",
    address: "ул. Полярная, 1",
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    // ТЭП заведены — на этой записи стоят тесты живой суммы (спека §2.10).
    area_aboveground_sp: "62399.70",
    area_underground_sp: "13341.30",
    area_total_sp: "75741.00",
    // Полезная заведена и МЕНЬШЕ общей — часть общей, а не третье слагаемое:
    // 75741.00 остаётся суммой пары (спека 2026-08-15 §2.2).
    area_useful_sp: "54210.00",
    contracts_count: 1,
    created_at: null,
    updated_at: null,
  },
  {
    id: 11,
    title: "ЖК Южный",
    address: "ул. Солнечная, 7",
    rate_class_id: 2,
    rate_class_title: "Промышленные",
    // ТЭП не заведены — законное состояние NULL/NULL (спека §2.3); на ней стоит
    // тест пустого состояния.
    area_aboveground_sp: null,
    area_underground_sp: null,
    area_total_sp: null,
    area_useful_sp: null,
    contracts_count: 0,
    created_at: null,
    updated_at: null,
  },
];

export const sampleContractors: Contractor[] = [
  {
    id: 20,
    title: "ООО СтройПодряд",
    inn: "123456789012",
    address: "г. Тест, ул. Подрядная, 1",
    accreditation: "да",
    contracts_count: 1,
    created_at: null,
    updated_at: null,
  },
  {
    id: 21,
    title: "ТОО Монолит",
    inn: "987654321098",
    address: "г. Тест, ул. Бетонная, 4",
    accreditation: "нет",
    contracts_count: 0,
    created_at: null,
    updated_at: null,
  },
];

export const sampleContracts: ContractRow[] = [
  {
    id: 100,
    contract_number: "ГП-2026-001",
    title: "Генподряд на ЖК Северный",
    object_id: 10,
    object_title: "ЖК Северный",
    contractor_id: 20,
    contractor_title: "ООО СтройПодряд",
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    signer: "Иванов И.И.",
    signed_date: "2026-03-01",
    total_amount: "1234567890.12",
    estimates_count: 1,
    created_at: "2026-03-01T10:00:00Z",
    updated_at: "2026-03-01T10:00:00Z",
  },
  {
    id: 101,
    contract_number: "ГП-2026-002",
    title: null,
    object_id: 11,
    object_title: "ЖК Южный",
    contractor_id: 21,
    contractor_title: "ТОО Монолит",
    rate_class_id: 2,
    rate_class_title: "Промышленные",
    signer: null,
    signed_date: "2026-02-01",
    total_amount: null,
    estimates_count: 0,
    created_at: "2026-02-01T10:00:00Z",
    updated_at: "2026-02-01T10:00:00Z",
  },
];

export const sampleContractCard: ContractCard = {
  ...sampleContracts[0],
  notes: "Проверить индексацию в 2027",
  // Коммерческие условия (спека §2.5): аванс с комментарием, БГ без процента
  // (условие в виде свободного текста), удержание не заведено вовсе.
  advance_pct: "30",
  advance_note: "30% в течение 10 банковских дней с даты подписания",
  bank_guarantee_pct: null,
  bank_guarantee_note: "траншами по графику поставки",
  retention_pct: "5",
  retention_note: null,
  estimates: [
    {
      id: 500,
      amendment_no: null,
      title: "Смета к договору",
      data_prepared_on_date: "2026-03-10",
      import_job_id: 900,
      positions_count: 1830,
      category_overrides_count: 0,
      created_at: "2026-03-11T08:00:00Z",
    },
  ],
};

export const sampleImportJobs: ContractImportJob[] = [
  {
    id: 900,
    owner_type: "contract",
    contract_id: 100,
    amendment_no: null,
    filename: "смета-актуальная.xlsx",
    file_sha256: "a".repeat(64),
    status: "done",
    error_text: null,
    warnings: ["Единица измерения «пог.м» не найдена (позиций: 3)."],
    counters: {
      positions_total: 1830,
      matched_cache: 400,
      matched_exact: 330,
      matched_nonposition: 100,
      to_review: 1000,
    },
    estimate_id: 500,
    estimates_created: 1,
    is_current: true,
    created_at: "2026-03-11T08:00:00Z",
    started_at: "2026-03-11T08:00:01Z",
    finished_at: "2026-03-11T08:00:18Z",
  },
  {
    id: 899,
    owner_type: "contract",
    contract_id: 100,
    amendment_no: null,
    filename: "смета-вытесненная.xlsx",
    file_sha256: "b".repeat(64),
    status: "done",
    error_text: null,
    warnings: [],
    counters: {
      positions_total: 1800,
      matched_cache: 0,
      matched_exact: 0,
      matched_nonposition: 0,
      to_review: 1800,
    },
    estimate_id: null,
    estimates_created: 1,
    is_current: false,
    created_at: "2026-03-05T08:00:00Z",
    started_at: "2026-03-05T08:00:01Z",
    finished_at: "2026-03-05T08:00:20Z",
  },
];

/** Задание, упавшее с ошибкой: сметы не создавало никогда. */
export const sampleFailedJob: ContractImportJob = {
  id: 898,
  owner_type: "contract",
  contract_id: 100,
  amendment_no: null,
  filename: "смета-битая.xlsx",
  file_sha256: "c".repeat(64),
  status: "error",
  error_text: "Не удалось разобрать файл: не найдена шапка сметы.",
  warnings: [],
  counters: {
    positions_total: 0,
    matched_cache: 0,
    matched_exact: 0,
    matched_nonposition: 0,
    to_review: 0,
  },
  estimate_id: null,
  estimates_created: null,
  is_current: false,
  created_at: "2026-03-04T08:00:00Z",
  started_at: "2026-03-04T08:00:01Z",
  finished_at: "2026-03-04T08:00:03Z",
};

/** Задание в работе: сметы ещё нет, но и «вытеснено заменой» о нём — ложь. */
export const sampleRunningJob: ContractImportJob = {
  ...sampleFailedJob,
  id: 897,
  filename: "смета-в-работе.xlsx",
  status: "matching",
  error_text: null,
  created_at: "2026-03-03T08:00:00Z",
  finished_at: null,
};

export const sampleReviewQueue: ReviewQueueItem[] = [
  {
    id: 700,
    standard_job_title: "Стяжка неведомая",
    normalized_job_title: "стяжка неведомый",
    unit_id: 5,
    unit_code: "M2",
    unit_name: "Кв. метр",
    position_count: 42,
    sample_titles: ["Стяжка пола 50мм", "Стяжка пола 50 мм"],
    created_at: "2026-03-11T08:00:10Z",
  },
  {
    id: 701,
    standard_job_title: "Кладка непонятная",
    normalized_job_title: "кладка непонятный",
    unit_id: null,
    unit_code: null,
    unit_name: null,
    position_count: 3,
    sample_titles: ["Кладка стен"],
    created_at: "2026-03-11T08:00:11Z",
  },
];

export const sampleRateStandards: RateStandard[] = [
  {
    id: 300,
    catalog_position_id: 800,
    catalog_position_title: "Кладка кирпичная",
    unit_code: "M3",
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    standard_unit_rate: "1000.33",
    valid_from: "2025-01-01",
    valid_to: null,
    inflation_index: null,
    approved_by: "Совет директоров",
    approved_at: null,
    note: null,
    created_at: null,
    updated_at: null,
  },
  {
    id: 301,
    catalog_position_id: 801,
    catalog_position_title: "Стяжка цементная",
    unit_code: "M2",
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    standard_unit_rate: "550.00",
    valid_from: "2024-01-01",
    valid_to: "2025-01-01",
    inflation_index: null,
    approved_by: null,
    approved_at: null,
    note: null,
    created_at: null,
    updated_at: null,
  },
];

// ---------------------------------------------------------------------------
//  Аналитика фазы 6. Данные синтетические — реальные сметы из samples/ никуда
//  не выносятся, включая суммы (политика docs/phase0-input-data.md).
//
//  Числа подобраны так, чтобы каждый случай §10 был представлен и различим:
//  превышение норматива, ровно по нормативу (0 %) и ОТСУТСТВИЕ норматива. Без
//  третьего случая тесты не отличили бы «нет норматива» от «0 %».
// ---------------------------------------------------------------------------

export const sampleAppSettings: AppSettings = {
  passport_top_n: 15,
  passport_top_n_min: 1,
  passport_top_n_max: 20,
  updated_at: "2026-08-04T09:00:00Z",
};

/** Наименование на килобайты — то, на чём фаза 5 обожглась (§11 AGENTS.md). */
export const longJobTitle =
  "Устройство монолитных конструкций с полной спецификацией: " +
  Array.from({ length: 40 }, (_, i) => `позиция ${i + 1} по ведомости ГОСТ ${20000 + i}`).join("; ");

export const sampleMatrixColumns: MatrixColumn[] = [
  {
    contract_id: 10,
    contract_number: "ГП-0114",
    object_id: 1,
    object_title: "ЖК Северный",
    contractor_title: 'ООО "Подрядчик"',
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    estimate_id: 500,
    amendment_no: 1,
    comparison_date: "2025-04-01",
  },
  {
    contract_id: 11,
    contract_number: "ГП-0131",
    object_id: 1,
    object_title: "ЖК Северный",
    contractor_title: 'ООО "Второй"',
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    estimate_id: 501,
    amendment_no: null,
    comparison_date: "2026-01-15",
  },
  {
    contract_id: 12,
    contract_number: "ГП-0140",
    object_id: 2,
    object_title: "БЦ Восточный",
    contractor_title: 'ООО "Третий"',
    rate_class_id: 2,
    rate_class_title: "Административные",
    estimate_id: 502,
    amendment_no: null,
    comparison_date: "2026-05-01",
  },
];

export const sampleMatrixRows: MatrixRow[] = [
  {
    catalog_position_id: 701,
    job_title: "Кладка кирпичная",
    unit_code: "M3",
    row_amount: "18000000.00",
    // Обе ячейки этой строки — с известной базой НДС, вес посчитан по обеим.
    row_amount_incomplete: false,
    cells: [
      {
        contract_id: 10,
        rate: "12000.50",
        amount: "10800450.00",
        standard_unit_rate: "10000.00",
        deviation_pct: "20.005000000000000000",
        deviation_reason: null,
      },
      // Второй договор дешевле норматива — знак отклонения обязан быть виден.
      {
        contract_id: 11,
        rate: "9500.00",
        amount: "7199550.00",
        standard_unit_rate: "10000.00",
        deviation_pct: "-5.000000000000000000",
        deviation_reason: null,
      },
      // У третьего работы в смете нет вовсе: ячейки не будет — и это НЕ «нет
      // норматива». §10 требует различать эти случаи.
    ],
  },
  {
    catalog_position_id: 703,
    job_title: longJobTitle,
    unit_code: "M2",
    row_amount: "192000.00",
    row_amount_incomplete: false,
    cells: [
      {
        contract_id: 12,
        /*
          Пересчёт НДС (задача 3, приложение оркестратора п.4): `rate`/`amount`
          квантуются ДО КОПЕЕК на границе ответа (`quantize_money` в
          `crud/analytics.py::_fold_cell`), поэтому длинного хвоста деления
          `numeric` в реальном ответе больше не бывает — прежнее значение
          "640.503222935929" проверяло формат, которого API больше не отдаёт.
          Округление показа (`MoneyCell`/tooltip с точным значением) по-прежнему
          покрыто на `per_sqm` паспорта (`ProjectPassportPage.test.tsx`).
        */
        rate: "640.50",
        amount: "12800.00",
        // Норматива нет вовсе (а не «база неизвестна») — отсюда deviation_reason.
        standard_unit_rate: null,
        deviation_pct: null,
        deviation_reason: "no_standard",
      },
    ],
  },
];

export const sampleMatrix: Matrix = {
  columns: sampleMatrixColumns,
  rows: sampleMatrixRows,
  total: 2,
  page: 1,
  page_size: 50,
  positions_pending_review: 0,
  positions_non_work: 0,
};

// ---------------------------------------------------------------------------
//  Паспорт проекта по статьям классификатора (фаза 7, Ф6, спека §2.6).
//
//  Форма зеркалит `backend/crud/project_passport.py::get_project_passport`.
//  Числа подобраны так, что СУММА `total` всех корней плюс `unallocated.amount`
//  РОВНО равна `totals.amount` — посчитано вручную десятичными строками (см.
//  комментарий у `totals` ниже), а не округлено на глаз: иначе инвариантные
//  проверки экранов задач 7-9 не значили бы ничего. `share_pct`/`per_sqm`
//  каждой строки посчитаны от ОДНОГО знаменателя (`totals.amount` = 4 700 000,
//  `object.area_total_sp` = 47 000) — того же, что использует бэкенд (спека
//  §2.6, правило 5).
//
//  Дерево (плоский список `categories`, порядок глубины — правило 2):
//  - «Земляные работы» (корень) с двумя детьми: «Разработка грунта» (деньги) и
//    «Водопонижение» (total РОВНО ноль — отличимо от отсутствия статьи).
//  - «Кровельные работы» (корень) — `is_bucket: true`.
//  - «Отделочные работы» (корень) ОТСУТСТВУЕТ в смете: `total: null, rows: 0`.
//  - «Инженерные сети» (корень) — есть `own` (собственные деньги узла) И дети,
//    и ДВА раздела в `own_sections`; один из детей, «Пусконаладочные работы»,
//    — лист, чьё единственное содержимое — строка `extras`.
//  - Ещё пять «плоских» корней с разными суммами — девятый нужен, чтобы кольцо
//    «топ-8» экрана задачи 8 имело что свернуть в «Остальные».
//  - «Прочие работы» (корень "10") несёт ТРЕТИЙ own_sections — с
//    `source: "manual"`, единственный в фикстуре (у "04" оба — "file"): без
//    него различие source: "file"/"manual" не имело бы покрытия вовсе.
//
//  Разнос (задачи 7-9, спека разноса §2.6): `unallocated.sections` — дерево
//  из трёх узлов (комментарий у `unallocated` ниже), `manual_assignments` —
//  одно действующее решение (комментарий у него ниже), `category_options` —
//  классификатор целиком, включая один вложенный код без строк, которого в
//  `categories` нет (комментарий у него ниже).
export const longAdvanceNote =
  "Аванс перечисляется траншами по графику поставки материалов и оборудования: " +
  Array.from(
    { length: 5 },
    (_, i) =>
      `транш ${i + 1} — не позднее 10 рабочих дней с даты письменной заявки подрядчика по форме приложения №${i + 1}`
  ).join("; ") +
  ".";

export const sampleProjectPassport: ProjectPassport = {
  contract: {
    id: 12,
    contract_number: "ГП-0212",
    title: "Генеральный подряд на строительство",
    signer: "Смирнов А.В.",
    signed_date: "2025-05-15",
    object_id: 3,
    object_title: "ЖК Заречный",
    contractor_title: 'ООО "СтройГарант"',
    rate_class_title: "Жилые дома",
    advance_pct: "30",
    advance_note: longAdvanceNote,
    bank_guarantee_pct: "10",
    bank_guarantee_note:
      "Гарантия открывается на весь срок строительства и продлевается на период гарантийных обязательств.",
    retention_pct: "5",
    retention_note: "Удержание перечисляется после подписания итогового акта приёмки.",
    object_contracts_count: 1,
  },
  object: {
    id: 3,
    title: "ЖК Заречный",
    area_underground_sp: "7000.00",
    area_aboveground_sp: "40000.00",
    area_total_sp: "47000.00",
    // Полезная в знаменатель ₽/м² НЕ входит: он остаётся 47 000 (спека §2.2),
    // поэтому все посчитанные ниже `per_sqm` этой фикстурой не меняются.
    area_useful_sp: "33500.00",
  },
  estimate: {
    id: 600,
    amendment_no: null,
    title: "Смета исходная",
    data_prepared_on_date: "2025-06-01",
    parser_version: "1.4.0",
    vat_rate: "20",
    // Ставки не правились — законное состояние по умолчанию (спека пересчёта §2.7).
    vat_rate_base_override: null,
    vat_rate_target: null,
    vat_rate_updated_at: null,
    // Без поправок ставка показа совпадает с заявленной (задача 10) —
    // `effective_display_rate` без override/target возвращает `declared`.
    vat_display_rate: "20",
  },
  totals: {
    // 500000 + 300000 + 175000 + 900000 + 800000 + 700000 + 600000 + 400000 +
    // 200000 (корни) + 125000 (unallocated) = 4700000.00 — сложено вручную.
    amount: "4700000.00",
    per_sqm: "100",
    positions_rows: 461,
    positions_rows_priced: 461,
    positions_rows_not_finite: 0,
    additional_works_rows: 2,
    // Валовое ИТОГО файла равно табличной сумме — тишина это нормальный вид
    // (расхождения нет), поэтому дельта ровно ноль.
    file_total_including_vat: "4700000.00",
    delta_to_file_total: "0.00",
    // Сверка нетто (спека пересчёта §2.10): предложение одно, ставка одна —
    // сравнимо и сходится. Расхождение (`mismatch`) — забота фикстур задачи 10.
    net_reconciliation: { status: "ok", delta: "0.00", mismatched_proposal_ids: [] },
  },
  categories: [
    {
      id: 1,
      code: "01",
      title: "Земляные работы",
      parent_id: null,
      is_bucket: false,
      sort_order: 10,
      total: "500000.00",
      rows: 51,
      rows_priced: 51,
      rows_not_finite: 0,
      share_pct: "10.638297872340425531",
      per_sqm: "10.63829787234042553191489362",
      own: null,
      own_rows: 0,
      own_rows_priced: 0,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      // Корень без собственной строки — деньги целиком у детей (own: null
      // выше); носителя кода "01" в смете нет, допработ у узла тоже нет.
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 2,
      code: "01.01",
      title: "Разработка грунта",
      parent_id: 1,
      is_bucket: false,
      sort_order: 10,
      total: "500000.00",
      rows: 50,
      rows_priced: 50,
      rows_not_finite: 0,
      share_pct: "10.638297872340425531",
      per_sqm: "10.63829787234042553191489362",
      own: "500000.00",
      own_rows: 50,
      own_rows_priced: 50,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      // Единственный узел фикстуры со ставкой (задача 6 плана). `unit_rate` —
      // ДЕЛЕНИЕ total/volume: 500000.00 / 3000.00 = 166,(6), бесконечная
      // периодическая дробь — но, В ОТЛИЧИЕ от `per_sqm` выше, `unit_rate`
      // сервер квантует ДО отдачи, безусловно (`quantize_money`, §2.10
      // ревизия «ставка квантуется всегда»): по проводу приходит уже
      // "166.67", а не хвост из `_RATE_CONTEXT` (`prec = 100`). Это форма
      // стенда, а не сокращение теста: округлённое и точное значение здесь
      // совпадают, и `MoneyCell` поэтому НЕ кладёт `title` вовсе (см. тест
      // на ставку в `PassportRates.test.tsx`).
      unit: "м³",
      volume: "3000.00",
      unit_rate: "166.67",
      rate_state: "rate",
      rate_note: null,
    },
    {
      id: 3,
      code: "01.02",
      title: "Водопонижение",
      parent_id: 1,
      is_bucket: false,
      sort_order: 20,
      // Ровно ноль — и это ОТЛИЧИМО от «статьи нет в смете» (id 5 ниже, где
      // total: null): здесь строки есть, они просто ничего не стоят.
      total: "0.00",
      rows: 1,
      rows_priced: 1,
      rows_not_finite: 0,
      // ФОРМА СТЕНДА, а не аккуратная короткая десятичная (замер плана Ф6a §1.2):
      // ноль от деления сервер отдаёт как "0" — `Decimal('0E+2')` через
      // `format(·, 'f')`. Прежняя запись "0.000000000000000000" уже сама несла
      // два знака и потому была зелена там, где стенд красен.
      share_pct: "0",
      per_sqm: "0",
      own: "0.00",
      own_rows: 1,
      own_rows_priced: 1,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 4,
      code: "99",
      title: "Кровельные работы",
      parent_id: null,
      // Бакет (спека §2.2) — заглушка-корень, а не листовая статья.
      is_bucket: true,
      sort_order: 20,
      total: "300000.00",
      rows: 30,
      rows_priced: 30,
      rows_not_finite: 0,
      share_pct: "6.382978723404255319",
      per_sqm: "6.382978723404255319148936170",
      own: "300000.00",
      own_rows: 30,
      own_rows_priced: 30,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 5,
      code: "03",
      title: "Отделочные работы",
      parent_id: null,
      is_bucket: false,
      sort_order: 30,
      // Статья ОТСУТСТВУЕТ в смете: null, а не 0 — и rows: 0, а не пропущена.
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
      own_sections: [],
      // Статьи нет в смете вовсе — учебниковый no_carrier (нет ни строки, ни допработ).
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 6,
      code: "04",
      title: "Инженерные сети",
      parent_id: null,
      is_bucket: false,
      sort_order: 40,
      // 100000 (own) + 50000 (Внутренние сети) + 25000 (Пусконаладочные) = 175000.
      total: "175000.00",
      rows: 16,
      rows_priced: 16,
      rows_not_finite: 0,
      share_pct: "3.723404255319148936",
      per_sqm: "3.723404255319148936170212766",
      own: "100000.00",
      own_rows: 10,
      own_rows_priced: 10,
      own_rows_not_finite: 0,
      extras: [],
      // Два раздела дали узлу его собственные деньги — задача 5 спеки §2.9 п.6.
      // Оба source: "file" — раздел пришёл из файла, а не назначен вручную.
      own_sections: [
        { id: 1, number: "4.1", title: "Раздел «Водоснабжение и водоотведение»", source: "file" },
        { id: 2, number: "4.2", title: "Раздел «Электроснабжение сетей»", source: "file" },
      ],
      // Единственный unit_not_scalable фикстуры (задача 6 плана): своя строка
      // есть (own > 0), единица «Комплект» — количество не измеряет объём работ.
      unit: "компл",
      volume: null,
      unit_rate: null,
      rate_state: "unit_not_scalable",
      rate_note: null,
    },
    {
      id: 7,
      code: "04.01",
      title: "Внутренние сети водоснабжения",
      parent_id: 6,
      is_bucket: false,
      sort_order: 10,
      total: "50000.00",
      rows: 5,
      rows_priced: 5,
      rows_not_finite: 0,
      share_pct: "1.063829787234042553",
      per_sqm: "1.063829787234042553191489362",
      own: "50000.00",
      own_rows: 5,
      own_rows_priced: 5,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      // Единственный volume_missing фикстуры (задача 6 плана): единица
      // известна («м» — погонный метр труб), а объём на строке не указан.
      unit: "м",
      volume: null,
      unit_rate: null,
      rate_state: "volume_missing",
      rate_note: null,
    },
    {
      id: 8,
      code: "04.02",
      title: "Пусконаладочные работы",
      parent_id: 6,
      is_bucket: false,
      sort_order: 20,
      // Лист, чьё ЕДИНСТВЕННОЕ содержимое — строка допработы: own: null (своих
      // позиций нет), total равен сумме `extras`.
      total: "25000.00",
      rows: 1,
      rows_priced: 1,
      rows_not_finite: 0,
      share_pct: "0.531914893617021276",
      per_sqm: "0.5319148936170212765957446809",
      own: null,
      own_rows: 0,
      own_rows_priced: 0,
      own_rows_not_finite: 0,
      extras: [
        {
          id: 401,
          ordinal: 1,
          title: "Пусконаладочные работы по инженерным сетям (доп. соглашение к смете)",
          amount: "25000.00",
        },
      ],
      own_sections: [],
      // Единственный additional_works фикстуры (задача 6 плана): строки-
      // носителя нет, вся сумма узла — из extras выше; единицы нет по природе
      // данных (спека §2.5, состояние 1).
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "additional_works",
      rate_note: null,
    },
    {
      id: 9,
      code: "05",
      title: "Фасадные работы",
      parent_id: null,
      is_bucket: false,
      sort_order: 50,
      total: "900000.00",
      rows: 90,
      rows_priced: 90,
      rows_not_finite: 0,
      share_pct: "19.148936170212765957",
      per_sqm: "19.14893617021276595744680851",
      own: "900000.00",
      own_rows: 90,
      own_rows_priced: 90,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 10,
      code: "06",
      title: "Устройство кровли",
      parent_id: null,
      is_bucket: false,
      sort_order: 60,
      total: "800000.00",
      rows: 80,
      rows_priced: 80,
      rows_not_finite: 0,
      share_pct: "17.021276595744680851",
      per_sqm: "17.02127659574468085106382979",
      own: "800000.00",
      own_rows: 80,
      own_rows_priced: 80,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 11,
      code: "07",
      title: "Электромонтажные работы",
      parent_id: null,
      is_bucket: false,
      sort_order: 70,
      total: "700000.00",
      rows: 70,
      rows_priced: 70,
      rows_not_finite: 0,
      share_pct: "14.893617021276595744",
      per_sqm: "14.89361702127659574468085106",
      own: "700000.00",
      own_rows: 70,
      own_rows_priced: 70,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 12,
      code: "08",
      title: "Слаботочные системы",
      parent_id: null,
      is_bucket: false,
      sort_order: 80,
      total: "600000.00",
      rows: 60,
      rows_priced: 60,
      rows_not_finite: 0,
      share_pct: "12.765957446808510638",
      per_sqm: "12.76595744680851063829787234",
      own: "600000.00",
      own_rows: 60,
      own_rows_priced: 60,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      id: 13,
      code: "09",
      title: "Благоустройство",
      parent_id: null,
      is_bucket: false,
      sort_order: 90,
      total: "400000.00",
      rows: 40,
      rows_priced: 40,
      rows_not_finite: 0,
      share_pct: "8.510638297872340425",
      per_sqm: "8.510638297872340425531914894",
      own: "400000.00",
      own_rows: 40,
      own_rows_priced: 40,
      own_rows_not_finite: 0,
      extras: [],
      own_sections: [],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
    {
      // Девятая статья с деньгами — топ-8 кольца задачи 8 обязан свернуть её
      // в «Остальные».
      id: 14,
      code: "10",
      title: "Прочие работы",
      parent_id: null,
      is_bucket: false,
      sort_order: 100,
      total: "200000.00",
      rows: 20,
      rows_priced: 20,
      rows_not_finite: 0,
      share_pct: "4.255319148936170212",
      per_sqm: "4.255319148936170212765957447",
      own: "200000.00",
      own_rows: 20,
      own_rows_priced: 20,
      own_rows_not_finite: 0,
      extras: [],
      // Раздел «Устройство эстакад» (см. `manual_assignments` ниже) отнесён
      // сюда решением — source: "manual", в отличие от двух файловых записей
      // категории "04" выше. Оба значения `source` в одной фикстуре — иначе
      // задачи 8-9 не могли бы написать различающий тест «файл против
      // решения» без правки фикстуры (внешнее ревью).
      own_sections: [
        { id: 3, number: "5.3", title: "Раздел «Устройство эстакад»", source: "manual" },
      ],
      unit: null,
      volume: null,
      unit_rate: null,
      rate_state: "no_carrier",
      rate_note: null,
    },
  ],
  unallocated: {
    // 115000 (позиции без статьи, дерево ниже) + 10000 (допработа без статьи,
    // extras ниже) = 125000.
    amount: "125000.00",
    // ВСЕ позиции без статьи, а не только те, что сидят в дереве: 5 (сумма
    // own_rows по дереву ниже: 3 + 0 + 2) + 1 (rows_outside_structure) = 6.
    // Из них расценены только 2 (own_rows_priced у 5003) плюс сама
    // внестроечная позиция — 3; три строки узла 5001 намеренно НЕ расценены
    // (см. комментарий у дерева).
    rows: 6,
    rows_priced: 3,
    rows_not_finite: 0,
    share_pct: "2.659574468085106382",
    per_sqm: "2.659574468085106382978723404",
    chapters: 2,
    rows_outside_structure: 1,
    extras: [
      {
        id: 501,
        ordinal: 1,
        title: "Непредвиденные расходы вне структуры сметы",
        amount: "10000.00",
      },
    ],
    /*
      Дерево нераспределённого (задачи 7-9, спека разноса §2.6). `chapters: 2`
      считает разделы, под которыми есть хотя бы одна СВОЯ позиция (own_rows >
      0) — НЕ то же самое, что «корень дерева»: 5001 и 5003 держат свои
      позиции (own_rows 3 и 2) и оба входят в счётчик; 5002 — рехунг-корень БЕЗ
      единой своей позиции (own_rows: 0), в счётчик не входит, хотя и остаётся
      узлом дерева (у него есть ребёнок).

      Денежная свёртка складывает только ИЗВЕСТНЫЕ слагаемые (см. доккомент
      `amount`/`subtree_amount` в domain.ts): 5001 своих денег не несёт вовсе
      (`amount: null` — три позиции есть, но ни одна не расценена), поэтому в
      сумму 115000 идёт только 5003 (единственное известное слагаемое во всём
      дереве). Строки складываются ВСЕГДА, независимо от цены: 3 (5001) + 0
      (5002 own) + 2 (5003) = 5 own_rows по дереву — отсюда и `rows: 6` выше
      (5 + 1 внестроечная).
    */
    sections: [
      {
        // Свои позиции ЕСТЬ (`rows: 3` > 0 — узел входит в `chapters`), но ни
        // одна не расценена: `amount: null` здесь значит НЕ «позиций нет», а
        // «расценённой суммы среди них нет» (задача 7, правка по ревью —
        // прежняя фикстура путала эти два случая, ставя `rows: 0` рядом с
        // `amount: null`). Лист без детей — `subtree_amount` тоже `null`,
        // складывать было бы просто нечего.
        position_item_id: 5001,
        parent_position_item_id: null,
        number: "5.1",
        title: "Раздел «Устройство временных сооружений»",
        depth: 0,
        amount: null,
        subtree_amount: null,
        rows: 3,
        rows_priced: 0,
        rows_not_finite: 0,
        // Файл молчал — клетка «Статья СМР» была пустой.
        smr_article_raw: null,
      },
      {
        // Второй корень: СВОИХ позиций нет вовсе (`amount: null`, `rows: 0` —
        // здесь `null` означает именно это, второй, а не первый случай выше).
        // Все деньги дерева — у единственного ребёнка. `subtree_amount` при
        // этом НЕ null: он суммирует поддерево и показывает цену решения на
        // этой вершине (задачи 8-9 экрана разноса должны отличать эти два
        // поля). `rows`/`rows_priced` — тоже свёрнутые (2/2, от ребёнка), а не
        // «свои» — узел без своих строк всё равно несёт ненулевые счётчики.
        position_item_id: 5002,
        parent_position_item_id: null,
        number: "5.2",
        title: "Раздел «Демонтажные работы»",
        depth: 0,
        amount: null,
        subtree_amount: "115000.00",
        rows: 2,
        rows_priced: 2,
        rows_not_finite: 0,
        // Файл НЕ молчал — в клетке лежал нечитаемый текст, а не пустота.
        // Это другая причина, чем `null` у соседних узлов.
        smr_article_raw: "см. примечание к смете",
      },
      {
        // Лист под 5002 — единственное известное слагаемое всего дерева.
        position_item_id: 5003,
        parent_position_item_id: 5002,
        number: "5.2.1",
        title: "Подраздел «Демонтаж перегородок»",
        depth: 1,
        amount: "115000.00",
        subtree_amount: "115000.00",
        rows: 2,
        rows_priced: 2,
        rows_not_finite: 0,
        smr_article_raw: null,
      },
    ],
  },
  /*
    Одно действующее ручное решение: раздел «Устройство эстакад» отнесён на
    статью "10" (id 14, «Прочие работы»), которая ЕСТЬ в `categories` выше —
    build_tree держит КОРНИ безусловно (см. доккомент `ProjectPassportCategoryOption`
    в domain.ts), и код "10" — двузначный, то есть корневой, поэтому он не
    может быть отсутствующим примером (внешнее ревью нашло это и в прежней
    версии фикстуры: код "11" был двузначным/корневым и одновременно
    заявлен «отсутствующим», что по построению build_tree невозможно, а
    материализованные строки решения ниже сделали бы его сумму видимой в
    любом случае). Отсутствующий пример — в `category_options` ниже, и это
    ВЛОЖЕННЫЙ код без строк, а не корневой.

    `amount`/`subtree_amount`/`rows`* этой записи — ПОЛНАЯ файловая свёртка
    самого раздела 5004 (см. доккомент `ProjectPassportManualAssignment` в
    domain.ts: эти числа НЕ обязаны складываться с суммой целевой категории —
    `ProjectPassportSection` не несёт денег, и `own`/`total` статьи "10" уже
    накрывают эти строки по факту файла независимо от того, разбит ли этот
    own на секции).
  */
  manual_assignments: [
    {
      position_item_id: 5004,
      parent_position_item_id: null,
      number: "5.3",
      title: "Раздел «Устройство эстакад»",
      depth: 0,
      amount: "60000.00",
      subtree_amount: "60000.00",
      rows: 3,
      rows_priced: 3,
      rows_not_finite: 0,
      smr_article_raw: "уточнить у сметчика",
      work_category_id: 14,
      category_code: "10",
      category_title: "Прочие работы",
      assigned_by_email: "analyst@example.com",
      assigned_at: "2026-08-05T09:15:00Z",
      note: "Отнесено на «Прочие работы» по решению главного инженера.",
    },
  ],
  // Полный классификатор для выбора статьи при разносе (задачи 7-9). Коды
  // "01".."10" зеркалят корни/подстатьи `categories` выше. Последний вариант
  // (id 16, код "10.01") — ВЛОЖЕННЫЙ ребёнок корня "10", и в `categories` его
  // НЕТ: build_tree прячет узел глубже первого уровня, если в его поддереве
  // вообще нет строк (`rows > 0`) — а этот код строк не набрал НИКОГДА,
  // поэтому невидим в дереве категорий, но выбираем в него разносить всё
  // равно можно (то, ради чего `category_options`, а не `categories`, вообще
  // существует — см. доккомент типа в domain.ts).
  category_options: [
    { id: 1, code: "01", title: "Земляные работы", is_bucket: false },
    { id: 2, code: "01.01", title: "Разработка грунта", is_bucket: false },
    { id: 3, code: "01.02", title: "Водопонижение", is_bucket: false },
    { id: 4, code: "99", title: "Кровельные работы", is_bucket: true },
    { id: 5, code: "03", title: "Отделочные работы", is_bucket: false },
    { id: 6, code: "04", title: "Инженерные сети", is_bucket: false },
    { id: 7, code: "04.01", title: "Внутренние сети водоснабжения", is_bucket: false },
    { id: 8, code: "04.02", title: "Пусконаладочные работы", is_bucket: false },
    { id: 9, code: "05", title: "Фасадные работы", is_bucket: false },
    { id: 10, code: "06", title: "Устройство кровли", is_bucket: false },
    { id: 11, code: "07", title: "Электромонтажные работы", is_bucket: false },
    { id: 12, code: "08", title: "Слаботочные системы", is_bucket: false },
    { id: 13, code: "09", title: "Благоустройство", is_bucket: false },
    { id: 14, code: "10", title: "Прочие работы", is_bucket: false },
    // Вложенный, без единой строки — отсутствует в `categories`, см. комментарий выше.
    { id: 16, code: "10.01", title: "Демонтаж временных ограждений", is_bucket: false },
  ],
  /*
    Охват ставками (спека объёма и ставки §2.8, §2.10; задача 6 плана — только
    тип и согласованное число, печать самой строки охвата это задача 7).

    Неперекрывающийся набор §2.3 — статьи, названные в смете, внутри которых
    нет других названных статей: "01" и "04" из него ИСКЛЮЧЕНЫ, потому что у
    каждой есть названные дети ("01.01"/"01.02" и "04.01"/"04.02" —
    соответственно). "03" исключена по другой причине: total: null, rows: 0 —
    её нет в смете вовсе. Остаются 11: "01.01", "01.02", "99", "04.01",
    "04.02", "05", "06", "07", "08", "09", "10" — отсюда articles_total: 11.

    Из них состоянием rate несёт только "01.01" (id 2) — отсюда
    articles_with_rate: 1. money_share — её total ("500000.00") к
    totals.amount ("4700000.00"): 500000 / 4700000 = 0.106382978723404255319…,
    та же бесконечная дробь, что несёт `share_pct` узлов "01"/"01.01" выше
    (500000 — их общий total). НО, в отличие от `share_pct`, `money_share`
    сервер квантует до сотых процента безусловно (§2.10, ревизия «money_share
    квантуется» — тем же приёмом и по тому же доводу, что `unit_rate`): по
    проводу приходит "10.64", а не хвост `_RATE_CONTEXT`.
    positions_rows_priced (461) === positions_rows (461) у totals — суммы
    известны полностью, поэтому money_share_state: "complete".
  */
  rate_coverage: {
    articles_with_rate: 1,
    articles_total: 11,
    money_share: "10.64",
    money_share_state: "complete",
  },
};

export const sampleMatrixCellDetail: MatrixCellDetail = {
  contract_id: 10,
  catalog_position_id: 701,
  estimate_id: 500,
  amendment_no: 1,
  items: [
    {
      position_item_id: 9001,
      job_title: "Кладка кирпичная наружных стен",
      unit_code: "M3",
      weight: "30",
      unit_cost_total: "100.00",
      // База НДС известна — нетто выведено из валового (спека пересчёта §2.5).
      unit_cost_net: "83.33",
      vat_rate_base: "20",
      total_cost_total: "3000.00",
      standard_unit_rate: "100.00",
      deviation_pct: "0.000000000000000000",
      deviation_reason: null,
    },
    {
      position_item_id: 9002,
      job_title: "Кладка кирпичная внутренних стен",
      unit_code: "M3",
      weight: "20",
      unit_cost_total: "200.00",
      unit_cost_net: "166.67",
      vat_rate_base: "20",
      total_cost_total: "4000.00",
      standard_unit_rate: "100.00",
      deviation_pct: "100.000000000000000000",
      deviation_reason: null,
    },
  ],
};

// ---------------------------------------------------------------------------
//  Стартовый дашборд (спека 2026-08-16). Данные вымышленные: ни одного
//  реального контрагента, объекта и суммы (политика samples/).
// ---------------------------------------------------------------------------
//
// Фикстура НАРОЧНО неоднородна и повторяет разобранные на гейте 1 случаи:
//  · охваты у трёх слагаемых площади РАЗНЫЕ (2/4, 2/4, 1/4) — общий счётчик
//    «не заведена у N» это различие скрывал бы;
//  · один класс с ДВУМЯ объектами (полоса размаха есть) и один с ОДНИМ
//    (полосы нет) — иначе половину диаграммы нечем было бы проверить;
//  · объект без площади в рейтинге ЕСТЬ, а точки на диаграмме у него НЕТ.

export const sampleDashboard: Dashboard = {
  money: {
    amount: "2291000000.00",
    coverage: {
      total: 6,
      counted: 3,
      reasons: { no_estimate: 2, amendment: 0, no_rate: 1, incomplete: 0 },
    },
  },
  areas: {
    total: { value: "143300.00", coverage: { total: 4, counted: 2 } },
    aboveground: { value: "118900.00", coverage: { total: 4, counted: 2 } },
    underground: { value: "24400.00", coverage: { total: 4, counted: 2 } },
    useful: { value: "61700.00", coverage: { total: 4, counted: 1 } },
    largest: { object_id: 1, title: "ЖК «Северная гряда», корп. 2", area_total_sp: "78400.00" },
    smallest: { object_id: 2, title: "Детский сад на 240 мест", area_total_sp: "64900.00" },
  },
  counters: { objects: 4, classes: 2, contracts: 6, contracts_with_estimate: 4 },
  per_sqm: {
    max: {
      object_id: 2,
      title: "Детский сад на 240 мест",
      per_sqm: "12254.00",
      area_total_sp: "64900.00",
      rate_class_title: "Жилой дом",
    },
    min: {
      object_id: 1,
      title: "ЖК «Северная гряда», корп. 2",
      per_sqm: "9500.00",
      area_total_sp: "78400.00",
      rate_class_title: "Жилой дом",
    },
    coverage: { total: 4, counted: 2 },
  },
  ranking: [
    {
      object_id: 2,
      title: "Детский сад на 240 мест",
      rate_class_id: 1,
      rate_class_title: "Жилой дом",
      area_total_sp: "64900.00",
      amount: "795300000.00",
      per_sqm: "12254.00",
      display_rate: "0",
      contract: {
        id: 12,
        contract_number: "ДГП-121-ТУ",
        signed_date: "2025-03-14",
        contractor_title: "СтройМонтажСервис",
      },
    },
    {
      object_id: 3,
      title: "Складской комплекс «Восточный»",
      rate_class_id: 2,
      rate_class_title: "Склад",
      area_total_sp: null,
      amount: "750900000.00",
      per_sqm: null,
      display_rate: "12",
      contract: {
        id: 13,
        contract_number: "ДГП-097-ТУ",
        signed_date: "2024-11-21",
        contractor_title: "ПромСтройАльянс",
      },
    },
    {
      object_id: 1,
      title: "ЖК «Северная гряда», корп. 2",
      rate_class_id: 1,
      rate_class_title: "Жилой дом",
      area_total_sp: "78400.00",
      amount: "744800000.00",
      per_sqm: "9500.00",
      display_rate: "20",
      contract: {
        id: 11,
        contract_number: "ДГП-118-ТУ",
        signed_date: "2025-03-14",
        contractor_title: "СтройМонтажСервис",
      },
    },
  ],
  ranking_coverage: {
    total: 4,
    counted: 3,
    reasons: { many_contracts: 1, no_contracts: 0, no_counted_contract: 0 },
  },
  chart: {
    classes: [
      {
        rate_class_id: 1,
        rate_class_title: "Жилой дом",
        points: [
          {
            object_id: 1,
            title: "ЖК «Северная гряда», корп. 2",
            per_sqm: "9500.00",
            amount: "744800000.00",
            area_total_sp: "78400.00",
          },
          {
            object_id: 2,
            title: "Детский сад на 240 мест",
            per_sqm: "12254.00",
            amount: "795300000.00",
            area_total_sp: "64900.00",
          },
        ],
        spread: { min: "9500.00", max: "12254.00" },
      },
      {
        rate_class_id: 2,
        rate_class_title: "Гостиница",
        points: [
          {
            object_id: 4,
            title: "Гостиница «Приморская»",
            per_sqm: "21592.00",
            amount: "512800000.00",
            area_total_sp: "23750.00",
          },
        ],
        spread: null,
      },
    ],
    coverage: { total: 4, counted: 2 },
  },
};

export const sampleDashboardAttention: DashboardAttention = {
  estimates_without_vat_rate: 2,
  objects_with_several_contracts: 1,
  contracts_without_estimate: 3,
  objects_without_area: 4,
  failed_imports_30d: 1,
};

/** Пустое состояние второго таба: чинить нечего (макет, панель ok). */
export const sampleDashboardAttentionClean: DashboardAttention = {
  estimates_without_vat_rate: 0,
  objects_with_several_contracts: 0,
  contracts_without_estimate: 0,
  objects_without_area: 0,
  failed_imports_30d: 0,
};

// ---------------------------------------------------------------------------
//  Сравнение договоров (спека 2026-08-17, задача 8)
// ---------------------------------------------------------------------------
//
// Четыре договора, а не три-минимум задачи: три несут `area_total_sp` (нужно
// РОВНО три сопоставимых значения для строки «1» — испытание подсветки, §2.5
// правило 5), четвёртый (204) без ТЭП — испытание прочерка ₽/м² без нуля
// (§2.4). Порядок колонок — `signed_date DESC, id DESC` (спека §2.1, DoD 3),
// как их отдал бы сервер: 204 (новее всех) → 203 → 202 → 201 (старше всех).

/** Ячейка без статьи в смете — прочерк во всех трёх осях (спека §2.1.2). */
function absentBucket(): ComparisonBucketCell {
  return {
    net: null,
    shown: null,
    net_per_sqm: null,
    shown_per_sqm: null,
    state: "absent",
    deviation_pct: null,
    incomplete_reasons: [],
  };
}

/** Статья есть, расценена в ноль (спека §2.1.2) — не путать с `absentBucket`. */
function zeroBucket(perSqm: string | null = "0.00"): ComparisonBucketCell {
  return {
    net: "0.00",
    shown: "0.00",
    net_per_sqm: perSqm,
    shown_per_sqm: perSqm,
    state: "zero",
    deviation_pct: null,
    incomplete_reasons: [],
  };
}

/** Число показано; `perSqm`/`deviationPct` — `null`, когда нет ТЭП или строка не сопоставима. */
function valueBucket(
  net: string,
  shown: string,
  perSqm: { net: string; shown: string } | null,
  deviationPct: string | null = null
): ComparisonBucketCell {
  return {
    net,
    shown,
    net_per_sqm: perSqm?.net ?? null,
    shown_per_sqm: perSqm?.shown ?? null,
    state: "value",
    deviation_pct: deviationPct,
    incomplete_reasons: [],
  };
}

/**
 * Ячейка погашена причинами неполноты (спека §2.1.3) — `state` остаётся
 * `"value"` (статья ЕСТЬ), а `shown`/`net` гаснут в `null`. Ровно так
 * ведёт себя `_resolve_cell`/`_build_bucket_cell` на бэкенде: гасит число,
 * не факт присутствия строки.
 */
function blankedBucket(reasons: ComparisonIncompleteReason[]): ComparisonBucketCell {
  return {
    net: null,
    shown: null,
    net_per_sqm: null,
    shown_per_sqm: null,
    state: "value",
    deviation_pct: null,
    incomplete_reasons: reasons,
  };
}

const COMPARISON_CONTRACT_IDS = [204, 203, 202, 201] as const;

/** Ячейка "Итого" == "ДГП" (допсоглашений в фикстуре нет, ДС — везде ноль/прочерк). */
function comparisonCellFromTotal(
  contractId: number,
  total: ComparisonBucketCell,
  amendments: ComparisonBucketCell = total.state === "absent" ? absentBucket() : zeroBucket(null)
): ComparisonCell {
  return { contract_id: contractId, base: total, amendments, total };
}

export const sampleComparisonColumns = [
  {
    contract_id: 204,
    contract_number: "ДГП-204",
    object_title: "Объект D",
    contractor_title: "Подрядчик D",
    rate_class_id: 1,
    rate_class_title: "Класс A",
    signed_date: "2026-06-01",
    // ТЭП не заведены — ₽/м² договора обязан быть прочерком, не нулём (§2.4).
    area_total_sp: null,
    advance_pct: null,
    bank_guarantee_pct: null,
    retention_pct: null,
    composition_caption: "20 %",
  },
  {
    contract_id: 203,
    contract_number: "ДГП-203",
    object_title: "Объект C",
    contractor_title: "Подрядчик C",
    rate_class_id: 1,
    rate_class_title: "Класс A",
    signed_date: "2026-01-01",
    area_total_sp: "75741.00",
    advance_pct: "30.00",
    bank_guarantee_pct: "10.00",
    retention_pct: "5.00",
    composition_caption: "20 %",
  },
  {
    contract_id: 202,
    contract_number: "ДГП-202",
    object_title: "Объект B",
    contractor_title: "Подрядчик B",
    rate_class_id: 1,
    rate_class_title: "Класс A",
    signed_date: "2025-06-01",
    area_total_sp: "166756.90",
    advance_pct: null,
    bank_guarantee_pct: null,
    retention_pct: null,
    composition_caption: "22 %",
  },
  {
    contract_id: 201,
    contract_number: "ДГП-201",
    object_title: "Объект A",
    contractor_title: "Подрядчик A",
    rate_class_id: 2,
    rate_class_title: "Класс B",
    signed_date: "2025-01-01",
    area_total_sp: "79692.37",
    advance_pct: null,
    bank_guarantee_pct: null,
    retention_pct: null,
    composition_caption: "16 %",
  },
];

/**
 * Строка «1» («Земляные работы») — РОВНО три сопоставимых значения
 * (203, 202, 201; медиана 1 500,00 ₽/м²), 204 исключён из медианы отсутствием
 * ТЭП. Ровно та граница, на которой правило 5 §2.5 включает подсветку (DoD 11
 * — «меньше трёх — подсветки нет» проверяется на строке «2» ниже, где
 * сопоставимых 0).
 */
const comparisonRow1: ComparisonRow = {
  kind: "category",
  category_id: 1,
  code: "1",
  title: "Земляные работы",
  level: 1,
  parent_code: null,
  cells: COMPARISON_CONTRACT_IDS.map((id) => {
    if (id === 204) {
      return comparisonCellFromTotal(id, valueBucket("1200000.00", "1440000.00", null));
    }
    if (id === 203) {
      return comparisonCellFromTotal(
        id,
        valueBucket("75741000.00", "90889200.00", { net: "1000.00", shown: "1200.00" }, "-33.33")
      );
    }
    if (id === 202) {
      return comparisonCellFromTotal(
        id,
        valueBucket(
          "333513800.00",
          "406886836.00",
          { net: "2000.00", shown: "2440.00" },
          "33.33"
        )
      );
    }
    return comparisonCellFromTotal(
      id,
      valueBucket("119538555.00", "138664803.60", { net: "1500.00", shown: "1740.00" }, "0.00")
    );
  }),
  medians: {
    base: { value: "1500.00", comparable_count: 3, contract_ids: [203, 202, 201] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: "1500.00", comparable_count: 3, contract_ids: [203, 202, 201] },
  },
};

/** Дочерняя статья «1.1» — испытание раскрытия ветки (DoD 16). */
const comparisonRow11: ComparisonRow = {
  kind: "category",
  category_id: 11,
  code: "1.1",
  title: "Разработка грунта",
  level: 2,
  parent_code: "1",
  cells: COMPARISON_CONTRACT_IDS.map((id) =>
    comparisonCellFromTotal(id, valueBucket("500000.00", "600000.00", null))
  ),
  medians: {
    base: { value: null, comparable_count: 0, contract_ids: [] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: null, comparable_count: 0, contract_ids: [] },
  },
};

/** Синтетическая строка «Без подстатьи» статьи «1» (спека §2.1, own + прямые допработы). */
const comparisonRow1Own: ComparisonRow = {
  kind: "own",
  category_id: 1,
  code: "1::own",
  title: "Без подстатьи",
  level: 2,
  parent_code: "1",
  cells: COMPARISON_CONTRACT_IDS.map((id) =>
    comparisonCellFromTotal(id, valueBucket("100000.00", "120000.00", null))
  ),
  medians: {
    base: { value: null, comparable_count: 0, contract_ids: [] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: null, comparable_count: 0, contract_ids: [] },
  },
};

/**
 * Строка «2» — три РАЗНЫХ написания пустоты и ноля в одной строке (спека
 * §2.1.2, §2.1.3): 204 — число, 203 — настоящий ноль, 202 — прочерк (статьи
 * нет), 201 — ячейка погашена ДВУМЯ причинами сразу (порядок — как отдаёт
 * сервер, `sorted()`: `not_finite_rows` раньше `unpriced_rows`). Ни одно
 * значение сюда не входит в медиану (ноль/прочерк/погашенная исключены §2.5
 * правила 3-4, а у 204 нет ТЭП) — сопоставимых 0, строка проверяет DoD 11.
 */
const comparisonRow2: ComparisonRow = {
  kind: "category",
  category_id: 2,
  code: "2",
  title: "Благоустройство, дороги",
  level: 1,
  parent_code: null,
  cells: [
    comparisonCellFromTotal(204, valueBucket("500000.00", "600000.00", null)),
    comparisonCellFromTotal(203, zeroBucket("0.00")),
    comparisonCellFromTotal(202, absentBucket()),
    comparisonCellFromTotal(201, blankedBucket(["not_finite_rows", "unpriced_rows"])),
  ],
  medians: {
    base: { value: null, comparable_count: 0, contract_ids: [] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: null, comparable_count: 0, contract_ids: [] },
  },
};

/** «Нераспределённое» — последняя строка, вне медианы (спека §2.1.4). */
const comparisonRowUnallocated: ComparisonRow = {
  kind: "unallocated",
  category_id: null,
  code: "::unallocated",
  title: "Нераспределённое",
  level: 1,
  parent_code: null,
  cells: [
    comparisonCellFromTotal(204, valueBucket("50000.00", "60000.00", null)),
    comparisonCellFromTotal(203, absentBucket()),
    comparisonCellFromTotal(202, absentBucket()),
    comparisonCellFromTotal(201, absentBucket()),
  ],
  medians: {
    base: { value: null, comparable_count: 0, contract_ids: [] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: null, comparable_count: 0, contract_ids: [] },
  },
};

const comparisonTotalsCells: ComparisonCell[] = [
  // Сумма НАМЕРЕННО с длинным хвостом, как её и отдаёт агрегат: он не квантует
  // деньги (иначе `ДГП + ДС = Итого` разошлось бы на копейку, DoD 5), а нетто —
  // частное от `gross_to_net`, почти никогда не представимое конечной дробью.
  // Круглые значения здесь скрыли настоящий дефект показа: на стенде экран
  // печатал «14 011 951 126,949999999999999982 ₽». Не заменять на круглое.
  comparisonCellFromTotal(204, valueBucket("1750000.00", "2100000.949999999999999982", null)),
  comparisonCellFromTotal(
    203,
    valueBucket("76241000.00", "91489200.00", { net: "1006.66", shown: "1207.99" }, "-33.11")
  ),
  comparisonCellFromTotal(
    202,
    valueBucket("333513800.00", "406886836.00", { net: "2000.00", shown: "2440.00" }, "32.90")
  ),
  comparisonCellFromTotal(
    201,
    valueBucket("119688555.00", "138839203.60", { net: "1501.88", shown: "1742.18" }, "0.21")
  ),
];

export const sampleComparison: Comparison = {
  vat_mode: "own",
  single_rate: null,
  rate_options: ["16.00", "20.00", "22.00"],
  rate_preselected: "20.00",
  caption:
    "Суммы — каждая в своей ставке договора: договор виден таким, каким существует. " +
    "Отклонения посчитаны без НДС.",
  columns: sampleComparisonColumns,
  // Классы РАЗНОГО размера намеренно: с равными `count` тест не заметил бы
  // перепутанных местами полей фасета. Порядок — по `title` (спека §2.7), и он
  // обязан совпадать с тем, что отдаёт сервер, а не с порядком колонок.
  available_rate_classes: [
    { id: 1, title: "Класс A", count: 3 },
    { id: 2, title: "Класс B", count: 1 },
  ],
  rows: [comparisonRow1, comparisonRow11, comparisonRow1Own, comparisonRow2, comparisonRowUnallocated],
  totals: comparisonTotalsCells,
  totals_medians: {
    base: { value: "1501.88", comparable_count: 3, contract_ids: [203, 202, 201] },
    amendments: { value: null, comparable_count: 0, contract_ids: [] },
    total: { value: "1501.88", comparable_count: 3, contract_ids: [203, 202, 201] },
  },
};


// ---------------------------------------------------------------------------
//  Ряды индексов инфляции (спека 2026-08-18 §2.6, §2.12)
// ---------------------------------------------------------------------------
//
//  Два активных ряда и один архивный. Два активных нужны не для полноты: у них
//  РАЗНЫЕ коэффициенты, потому что одинаковые означали бы, что селектор меняет
//  подпись, не меняя чисел, — та же ложь, только незаметнее (дефект макета, §7
//  спеки). Архивный нужен, чтобы проверять обратимость архивации и то, что в
//  селектор выбора он не попадает.

export const sampleInflationSeries: InflationSeries[] = [
  {
    id: 1,
    name: "Росстат, ИПЦ, декабрь к декабрю",
    note: "официальная публикация, по РФ",
    is_active: true,
    year_from: 2024,
    year_to: 2026,
    value_count: 3,
    created_at: "2026-01-12T10:00:00+03:00",
    updated_at: "2026-01-12T10:00:00+03:00",
  },
  {
    id: 2,
    name: "Внутренняя оценка ПЭО",
    note: "смета строительных ресурсов",
    is_active: true,
    year_from: 2024,
    year_to: 2026,
    value_count: 3,
    created_at: "2026-08-04T09:30:00+03:00",
    updated_at: "2026-08-04T09:30:00+03:00",
  },
  {
    id: 3,
    name: "Ряд 2024 года, выведен из обращения",
    note: null,
    is_active: false,
    year_from: 2024,
    year_to: 2024,
    value_count: 1,
    created_at: "2024-02-01T09:00:00+03:00",
    updated_at: "2024-02-01T09:00:00+03:00",
  },
];

export const sampleInflationValues: Record<number, InflationSeriesValue[]> = {
  1: [
    { year: 2024, coefficient: "1.0750", source: "бюллетень 01.2025", is_forecast: false,
      created_at: "2026-01-12T10:00:00+03:00", updated_at: "2026-01-12T10:00:00+03:00" },
    { year: 2025, coefficient: "1.0830", source: "бюллетень 01.2026", is_forecast: false,
      created_at: "2026-01-12T10:00:00+03:00", updated_at: "2026-01-12T10:00:00+03:00" },
    { year: 2026, coefficient: "1.0600", source: "прогноз Минэка 12.2025", is_forecast: true,
      created_at: "2026-01-12T10:00:00+03:00", updated_at: "2026-01-12T10:00:00+03:00" },
  ],
  2: [
    { year: 2024, coefficient: "1.1200", source: "внутренний расчёт", is_forecast: false,
      created_at: "2026-08-04T09:30:00+03:00", updated_at: "2026-08-04T09:30:00+03:00" },
    { year: 2025, coefficient: "1.1500", source: "внутренний расчёт", is_forecast: false,
      created_at: "2026-08-04T09:30:00+03:00", updated_at: "2026-08-04T09:30:00+03:00" },
    { year: 2026, coefficient: "1.0900", source: "внутренний расчёт", is_forecast: true,
      created_at: "2026-08-04T09:30:00+03:00", updated_at: "2026-08-04T09:30:00+03:00" },
  ],
  3: [
    { year: 2024, coefficient: "1.0800", source: "архивная публикация", is_forecast: false,
      created_at: "2024-02-01T09:00:00+03:00", updated_at: "2024-02-01T09:00:00+03:00" },
  ],
};

// ---------------------------------------------------------------------------
//  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13, §2.14).
//  Данные синтетические — реальные тендеры, участники и суммы из samples/
//  никуда не выносятся (политика docs/phase0-input-data.md).
// ---------------------------------------------------------------------------

export const sampleTenders: TenderRow[] = [
  { id: 300, tender_number: "Т-2026-001", title: "Генподряд на строительство", object_id: 10, object_title: "ЖК Северный",
    rate_class_id: 1, rate_class_title: "Жилые дома", rounds_count: 2, participants_count: 2, created_at: "2026-08-01T08:00:00Z" },
];

export const sampleTenderCard: TenderCard = {
  ...sampleTenders[0],
  notes: null,
  object_address: "ул. Северная, 1",
  rounds: [
    { id: 3001, stage_no: 1, label: "Первичные предложения", held_on: "2026-06-01",
      latest_job: { id: 9101, status: "done", filename: "r1.xlsx", finished_at: "2026-06-02T10:00:00Z", created_at: "2026-06-02T09:00:00Z" },
      current_job_id: 9101, baseline_estimate_id: 8100, baseline_total_including_vat: "1150.00" },
    { id: 3002, stage_no: 2, label: null, held_on: null,
      latest_job: null, current_job_id: null, baseline_estimate_id: null, baseline_total_including_vat: null },
  ],
  participants: [
    { package_id: 501, contractor_id: 20, title: "ООО Альфа", inn: "7700000001" },
    { package_id: 502, contractor_id: 21, title: "ООО Бета", inn: "7700000002" },
  ],
  cells: [
    { round_id: 3001, package_id: 501, offer_id: 7001, estimate_id: 8001, total_including_vat: "1200.00" },
    { round_id: 3001, package_id: 502, offer_id: null, estimate_id: null, total_including_vat: null },
    { round_id: 3002, package_id: 501, offer_id: null, estimate_id: null, total_including_vat: null },
    { round_id: 3002, package_id: 502, offer_id: 7002, estimate_id: null, total_including_vat: null },
  ],
};

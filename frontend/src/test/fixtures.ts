import type { AdminUser } from "@/types/admin";
import type {
  AppSettings,
  ContractCard,
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
  Passport,
  ReviewQueueItem,
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
  estimates: [
    {
      id: 500,
      amendment_no: null,
      title: "Смета к договору",
      data_prepared_on_date: "2026-03-10",
      import_job_id: 900,
      positions_count: 1830,
      created_at: "2026-03-11T08:00:00Z",
    },
  ],
};

export const sampleImportJobs: ContractImportJob[] = [
  {
    id: 900,
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
    is_current: true,
    created_at: "2026-03-11T08:00:00Z",
    started_at: "2026-03-11T08:00:01Z",
    finished_at: "2026-03-11T08:00:18Z",
  },
  {
    id: 899,
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
    is_current: false,
    created_at: "2026-03-05T08:00:00Z",
    started_at: "2026-03-05T08:00:01Z",
    finished_at: "2026-03-05T08:00:20Z",
  },
];

/** Задание, упавшее с ошибкой: сметы не создавало никогда. */
export const sampleFailedJob: ContractImportJob = {
  id: 898,
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

export const samplePassport: Passport = {
  contract: {
    id: 10,
    contract_number: "ГП-0114",
    title: "Генеральный подряд",
    object_id: 1,
    object_title: "ЖК Северный",
    contractor_id: 1,
    contractor_title: 'ООО "Подрядчик"',
    rate_class_id: 1,
    rate_class_title: "Жилые дома",
    signer: "Петров П.П.",
    signed_date: "2025-03-01",
    total_amount: "1234567890.12",
    notes: null,
  },
  estimate: {
    id: 500,
    amendment_no: 1,
    title: "Смета с ДС 1",
    data_prepared_on_date: "2025-04-01",
  },
  top_n: 15,
  key_rates: [
    {
      position_item_id: 9001,
      catalog_position_id: 701,
      job_title: "Кладка кирпичная наружных стен",
      catalog_job_title: "кладка кирпичная",
      unit_code: "M3",
      weight: "1200",
      unit_cost_total: "12000.50",
      total_cost_total: "14400600.00",
      standard_unit_rate: "10000.00",
      deviation_pct: "20.005000000000000000",
    },
    {
      position_item_id: 9002,
      catalog_position_id: 702,
      job_title: "Стяжка пола цементная",
      catalog_job_title: "стяжка пола",
      unit_code: "M2",
      weight: "8400",
      unit_cost_total: "900.00",
      total_cost_total: "7560000.00",
      // Ровно по нормативу — это НОЛЬ, и он обязан быть отличим от «нет норматива».
      standard_unit_rate: "900.00",
      deviation_pct: "0.000000000000000000",
    },
    {
      position_item_id: 9003,
      catalog_position_id: 703,
      job_title: longJobTitle,
      catalog_job_title: longJobTitle,
      unit_code: "M2",
      weight: "300",
      unit_cost_total: "640.00",
      total_cost_total: "192000.00",
      // Норматива нет: §4 требует NULL, а не 0.
      standard_unit_rate: null,
      deviation_pct: null,
    },
  ],
  totals: {
    positions_priced: 1100,
    positions_shown: 3,
    priced_amount: "22152600.00",
    with_standard: 2,
    without_standard: 1098,
    over_standard: 1,
  },
};

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
    cells: [
      {
        contract_id: 10,
        rate: "12000.50",
        standard_unit_rate: "10000.00",
        deviation_pct: "20.005000000000000000",
      },
      // Второй договор дешевле норматива — знак отклонения обязан быть виден.
      {
        contract_id: 11,
        rate: "9500.00",
        standard_unit_rate: "10000.00",
        deviation_pct: "-5.000000000000000000",
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
    cells: [
      { contract_id: 12, rate: "640.00", standard_unit_rate: null, deviation_pct: null },
    ],
  },
];

export const sampleMatrix: Matrix = {
  columns: sampleMatrixColumns,
  rows: sampleMatrixRows,
  total: 2,
  page: 1,
  page_size: 50,
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
      total_cost_total: "3000.00",
      standard_unit_rate: "100.00",
      deviation_pct: "0.000000000000000000",
    },
    {
      position_item_id: 9002,
      job_title: "Кладка кирпичная внутренних стен",
      unit_code: "M3",
      weight: "20",
      unit_cost_total: "200.00",
      total_cost_total: "4000.00",
      standard_unit_rate: "100.00",
      deviation_pct: "100.000000000000000000",
    },
  ],
};

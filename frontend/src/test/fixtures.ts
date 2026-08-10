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
  ProjectPassport,
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
    // ТЭП заведены — на этой записи стоят тесты живой суммы (спека §2.10).
    area_aboveground_sp: "62399.70",
    area_underground_sp: "13341.30",
    area_total_sp: "75741.00",
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
      {
        contract_id: 12,
        // Длинная дробь — не украшение: ровно в таком виде приезжает
        // средневзвешенная ставка (деление `numeric` доводит результат до своей
        // шкалы). Без неё в фикстуре тест округления показа ничего не проверял бы.
        rate: "640.503222935929",
        standard_unit_rate: null,
        deviation_pct: null,
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
  },
  estimate: {
    id: 600,
    amendment_no: null,
    title: "Смета исходная",
    data_prepared_on_date: "2025-06-01",
    parser_version: "1.4.0",
    vat_rate: "20",
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
      own_sections: [
        { id: 1, number: "4.1", title: "Раздел «Водоснабжение и водоотведение»" },
        { id: 2, number: "4.2", title: "Раздел «Электроснабжение сетей»" },
      ],
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
      own_sections: [],
    },
  ],
  unallocated: {
    // 115000 (позиции без статьи) + 10000 (допработа без статьи) = 125000.
    amount: "125000.00",
    rows: 6,
    rows_priced: 6,
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

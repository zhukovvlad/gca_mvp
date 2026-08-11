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
      category_overrides_count: 0,
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
      // Оба source: "file" — раздел пришёл из файла, а не назначен вручную.
      own_sections: [
        { id: 1, number: "4.1", title: "Раздел «Водоснабжение и водоотведение»", source: "file" },
        { id: 2, number: "4.2", title: "Раздел «Электроснабжение сетей»", source: "file" },
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
      // Раздел «Устройство эстакад» (см. `manual_assignments` ниже) отнесён
      // сюда решением — source: "manual", в отличие от двух файловых записей
      // категории "04" выше. Оба значения `source` в одной фикстуре — иначе
      // задачи 8-9 не могли бы написать различающий тест «файл против
      // решения» без правки фикстуры (внешнее ревью).
      own_sections: [
        { id: 3, number: "5.3", title: "Раздел «Устройство эстакад»", source: "manual" },
      ],
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

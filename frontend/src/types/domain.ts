/**
 * Типы домена фазы 5 (AGENTS.md §4, §7).
 *
 * **Деньги — строки, а не number.** Это не стилистика: §3 требует
 * `numeric ↔ Decimal ↔ строка в JSON`, и объявить их `number` значило бы
 * прогонять суммы через double на последнем шаге. Форматируются через
 * `formatDecimalMoney` / `MoneyCell`, в поля ввода уходят как есть.
 *
 * Даты — ISO-строки (`YYYY-MM-DD` для date, полный ISO для timestamptz).
 */

/** Точное десятичное значение: сумма, ставка, коэффициент. */
export type Decimal = string;

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
//  Справочники
// ---------------------------------------------------------------------------

export interface RateClass {
  id: number;
  title: string;
  description: string | null;
  contracts_count: number;
  objects_count: number;
  standards_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface RateClassInput {
  title: string;
  description?: string | null;
}

export interface ObjectItem {
  id: number;
  title: string;
  address: string;
  rate_class_id: number | null;
  rate_class_title: string | null;
  /** ТЭП объекта (спека §2.2, §2.3): две вводимые площади, третья вычисляемая. */
  area_aboveground_sp: Decimal | null;
  area_underground_sp: Decimal | null;
  /** `null` — «ТЭП не заведены»; вычисляется в БД, напрямую не задаётся. */
  area_total_sp: Decimal | null;
  contracts_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ObjectInput {
  title: string;
  address?: string | null;
  rate_class_id?: number | null;
  area_aboveground_sp?: Decimal | null;
  area_underground_sp?: Decimal | null;
}

export interface Contractor {
  id: number;
  title: string;
  inn: string;
  address: string;
  accreditation: string;
  contracts_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContractorInput {
  title: string;
  inn: string;
  address?: string | null;
  accreditation?: string | null;
}

// ---------------------------------------------------------------------------
//  Договоры
// ---------------------------------------------------------------------------

export interface ContractRow {
  id: number;
  contract_number: string;
  title: string | null;
  object_id: number;
  object_title: string;
  contractor_id: number;
  contractor_title: string;
  /** Снимок класса на момент создания договора (§4) — не класс объекта сейчас. */
  rate_class_id: number;
  rate_class_title: string;
  signer: string | null;
  signed_date: string;
  total_amount: Decimal | null;
  estimates_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface EstimateRow {
  id: number;
  /** `null` — исходная смета, иначе номер допсоглашения (§4). */
  amendment_no: number | null;
  title: string | null;
  data_prepared_on_date: string | null;
  import_job_id: number | null;
  positions_count: number;
  created_at: string | null;
}

/**
 * Коммерческие условия договора: три пары «процент + комментарий» (спека §2.5).
 *
 * Живут только в карточке, не в списке ({@link ContractRow}) — список это
 * выбор, а не карточка, и нести туда шесть ключей ради него незачем.
 */
export interface ContractCard extends ContractRow {
  notes: string | null;
  estimates: EstimateRow[];
  advance_pct: Decimal | null;
  advance_note: string | null;
  bank_guarantee_pct: Decimal | null;
  bank_guarantee_note: string | null;
  retention_pct: Decimal | null;
  retention_note: string | null;
}

export interface ContractInput {
  object_id: number;
  contractor_id: number;
  contract_number: string;
  signed_date: string;
  rate_class_id?: number | null;
  title?: string | null;
  signer?: string | null;
  total_amount?: Decimal | null;
  notes?: string | null;
  advance_pct?: Decimal | null;
  advance_note?: string | null;
  bank_guarantee_pct?: Decimal | null;
  bank_guarantee_note?: string | null;
  retention_pct?: Decimal | null;
  retention_note?: string | null;
}

// ---------------------------------------------------------------------------
//  Задания импорта (контракт фазы 4)
// ---------------------------------------------------------------------------

export type ImportJobStatus =
  | "pending"
  | "parsing"
  | "importing"
  | "matching"
  | "done"
  | "error";

/** Статусы, после которых поллинг обязан прекратиться (§7 брифинга фазы 5). */
export const TERMINAL_JOB_STATUSES: ImportJobStatus[] = ["done", "error"];

export interface ImportJobCounters {
  positions_total: number;
  matched_cache: number;
  matched_exact: number;
  matched_nonposition: number;
  to_review: number;
}

export interface ImportJob {
  id: number;
  contract_id: number;
  amendment_no: number | null;
  filename: string;
  file_sha256: string;
  status: ImportJobStatus;
  error_text: string | null;
  warnings: string[];
  counters: ImportJobCounters;
  /** Смета ТЕКУЩЕЙ пары (contract_id, amendment_no), а не «этого задания». */
  estimate_id: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** Задание в истории договора: плюс признак «держит актуальную смету». */
export interface ContractImportJob extends ImportJob {
  is_current: boolean;
}

export interface UploadEstimateInput {
  file: File;
  contract_id: number;
  amendment_no?: number | null;
  replace?: boolean;
}

// ---------------------------------------------------------------------------
//  Каталог и ручной матчинг
// ---------------------------------------------------------------------------

export interface CatalogPositionRow {
  id: number;
  standard_job_title: string;
  unit_id: number | null;
  unit_code: string | null;
  unit_name: string | null;
}

export type ManualKind = "POSITION" | "HEADER" | "TRASH";

/**
 * Потолок пакетной разметки — зеркало `MAX_BATCH_SIZE` из `routers/review.py`.
 *
 * Нужен на клиенте, чтобы объяснить ограничение **до** отправки и по-русски:
 * иначе сервер отвечает 422 от Pydantic с текстом вида «List should have at most
 * 200 items», из которого не следует, что делать. Клиентская проверка называет и
 * число, и причину (пакет держит строки заблокированными до конца транзакции).
 *
 * Выделение при отказе **не теряется** — `applyBatch` снимает его только после
 * успеха. В первой редакции здесь было сказано обратное; ошибку нашло внешнее
 * ревью, и она стоила отдельного разбора: смысл клиентской проверки в понятности
 * сообщения, а не в спасении выделения.
 */
export const MAX_REVIEW_BATCH = 200;

export interface ReviewQueueItem {
  id: number;
  standard_job_title: string;
  normalized_job_title: string;
  unit_id: number | null;
  unit_code: string | null;
  unit_name: string | null;
  /** Сколько позиций смет ссылается на строку — вес работы в очереди (§6.5). */
  position_count: number;
  /** До трёх различных наименований из смет — чтобы понять, что за работа. */
  sample_titles: string[];
  created_at: string | null;
}

export type ReviewSort = "positions" | "title";

export interface ReviewQueueParams {
  q?: string;
  unit_id?: number;
  without_unit?: boolean;
  sort?: ReviewSort;
  page?: number;
  page_size?: number;
}

export interface MergeResult {
  to_review_id: number;
  target: CatalogPositionRow & { normalized_job_title: string; kind: string };
  moved_positions: number;
}

export interface BatchKindResult {
  kind: ManualKind;
  applied: number[];
  skipped: { id: number; reason: string }[];
}

// ---------------------------------------------------------------------------
//  Нормативы
// ---------------------------------------------------------------------------

export interface RateStandard {
  id: number;
  catalog_position_id: number;
  catalog_position_title: string;
  unit_code: string | null;
  rate_class_id: number;
  rate_class_title: string;
  standard_unit_rate: Decimal;
  valid_from: string;
  /** `null` — период открыт (бесконечная верхняя граница, §4). */
  valid_to: string | null;
  inflation_index: Decimal | null;
  approved_by: string | null;
  approved_at: string | null;
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RateStandardInput {
  catalog_position_id: number;
  rate_class_id: number;
  standard_unit_rate: Decimal;
  valid_from: string;
  valid_to?: string | null;
  inflation_index?: Decimal | null;
  approved_by?: string | null;
  note?: string | null;
}

export interface RateStandardParams {
  rate_class_id?: number;
  catalog_position_id?: number;
  q?: string;
  on_date?: string;
  page?: number;
  page_size?: number;
}

export interface ReapproveInput {
  valid_from: string;
  standard_unit_rate?: Decimal | null;
  inflation_index?: Decimal | null;
  approved_by?: string | null;
  note?: string | null;
}

/** Переутверждение возвращает обе строки: история продолжена, а не переписана. */
export interface ReapproveResult {
  previous: RateStandard;
  current: RateStandard;
}

// ---------------------------------------------------------------------------
//  Единицы измерения (эндпоинт фазы 1)
// ---------------------------------------------------------------------------

export interface Unit {
  id: number;
  code: string;
  name: string;
  symbol: string;
  dimension: string;
  base_unit_id: number | null;
}

// ---------------------------------------------------------------------------
//  Аналитика фазы 6: настройки, паспорт, матрица (AGENTS.md §6, §7.4, §7.5)
// ---------------------------------------------------------------------------

/**
 * Настройки приложения. Границы `passport_top_n` приходят **с сервера**, а не
 * зашиты здесь: они выражают `CHECK` в БД (миграция 0004), и вторая их копия во
 * фронтенде разъехалась бы с первой при первом же изменении.
 */
export interface AppSettings {
  passport_top_n: number;
  passport_top_n_min: number;
  passport_top_n_max: number;
  updated_at: string | null;
}

/** Реквизиты договора для паспорта (§1 пункт 2). */
export interface PassportContract {
  id: number;
  contract_number: string;
  title: string | null;
  object_id: number;
  object_title: string;
  contractor_id: number;
  contractor_title: string;
  rate_class_id: number;
  rate_class_title: string;
  signer: string | null;
  signed_date: string;
  total_amount: Decimal | null;
  notes: string | null;
}

export interface PassportEstimate {
  id: number;
  /** `null` — исходная смета (§4). */
  amendment_no: number | null;
  title: string | null;
  data_prepared_on_date: string | null;
}

/** Строка «ключевых расценок»: позиция последней сметы (§7.4). */
export interface PassportKeyRate {
  position_item_id: number;
  catalog_position_id: number;
  /** Формулировка ИЗ СМЕТЫ — паспорт документ по конкретному договору. */
  job_title: string;
  /** Каталожное название: по нему подобран норматив. */
  catalog_job_title: string;
  unit_code: string | null;
  weight: Decimal | null;
  unit_cost_total: Decimal;
  total_cost_total: Decimal | null;
  /** `null` — норматива на дату сметы нет (§4); это НЕ ноль. */
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
}

export interface PassportTotals {
  /** Всего расценённых работ в смете — совокупность, из которой взят топ. */
  positions_priced: number;
  /** Сколько строк показано: длина топа, не обязательно равна `top_n`. */
  positions_shown: number;
  priced_amount: Decimal | null;
  with_standard: number;
  without_standard: number;
  /** Только превышение: ровно по нормативу — не превышение (§10). */
  over_standard: number;
  /**
   * Расценённые позиции, чья работа ещё не утверждена в каталоге (TO_REVIEW).
   *
   * Объясняет пустой топ при непустой смете: VIEW отклонений берёт только
   * `kind='POSITION'` (§4). Найдено прогоном стенда — экран называл неверную
   * причину («не заполнена цена»), отправляя искать проблему не там.
   */
  positions_pending_review: number;
  /**
   * Расценённые позиции, чья каталожная строка помечена как НЕ-работа
   * (`HEADER`/`TRASH`/`LOT_HEADER`).
   *
   * Третья причина пустого паспорта, и она не равна ни «ждут матчинга», ни «нет
   * цены»: такие строки уже разобраны (§5.4.3), исправлять их не нужно. Появилась
   * после правки по замечанию ревью — до неё они ошибочно попадали в «ждут матчинга».
   */
  positions_non_work: number;
}

export interface Passport {
  contract: PassportContract;
  /** `null` — смета ещё не загружена; паспорт печатается по реквизитам. */
  estimate: PassportEstimate | null;
  top_n: number;
  key_rates: PassportKeyRate[];
  totals: PassportTotals;
}

/** Колонка матрицы — договор выборки (§6, группировка по объекту). */
export interface MatrixColumn {
  contract_id: number;
  contract_number: string;
  object_id: number;
  object_title: string;
  contractor_title: string;
  rate_class_id: number;
  rate_class_title: string;
  estimate_id: number;
  amendment_no: number | null;
  comparison_date: string | null;
}

/** Ячейка: средневзвешенная ставка работы по договору (§6). */
export interface MatrixCell {
  contract_id: number;
  rate: Decimal;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
}

export interface MatrixRow {
  catalog_position_id: number;
  job_title: string;
  unit_code: string | null;
  /** Вес строки в деньгах — по нему строки упорядочены (§6.4 отчёта фазы 6). */
  row_amount: Decimal | null;
  /** Ячейки только тех договоров, где работа встречается: список, не объект. */
  cells: MatrixCell[];
}

export interface Matrix {
  columns: MatrixColumn[];
  rows: MatrixRow[];
  total: number;
  page: number;
  page_size: number;
  /** См. `PassportTotals.positions_pending_review`; здесь — по договорам выборки. */
  positions_pending_review: number;
  /** См. `PassportTotals.positions_non_work`; здесь — по договорам выборки. */
  positions_non_work: number;
}

export interface MatrixParams {
  rate_class_id?: number;
  date_from?: string;
  date_to?: string;
  q?: string;
  page?: number;
  page_size?: number;
}

/** Drill-down по ячейке: позиции, сложившиеся в средневзвешенную ставку (§6). */
export interface MatrixCellItem {
  position_item_id: number;
  job_title: string;
  unit_code: string | null;
  weight: Decimal | null;
  unit_cost_total: Decimal;
  total_cost_total: Decimal | null;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
}

export interface MatrixCellDetail {
  contract_id: number;
  catalog_position_id: number;
  estimate_id: number;
  amendment_no: number | null;
  items: MatrixCellItem[];
}

/**
 * Параметры отчёта «для банка» (§7.6).
 *
 * Те же, что у матрицы, и это намеренно: экран и файл обязаны показывать одно и то
 * же, иначе расхождение цифр придётся объяснять банку. Текстового поиска по работе
 * здесь нет — отчёт по подстроке названия банку не нужен.
 */
export interface BankComparisonParams {
  date_from?: string;
  date_to?: string;
  rate_class_id?: number;
}

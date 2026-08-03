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
  contracts_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ObjectInput {
  title: string;
  address?: string | null;
  rate_class_id?: number | null;
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

export interface ContractCard extends ContractRow {
  notes: string | null;
  estimates: EstimateRow[];
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

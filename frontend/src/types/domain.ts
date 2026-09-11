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
  /** `null` — общая площадь не заведена; вычисляется в БД, напрямую не задаётся. */
  area_total_sp: Decimal | null;
  /**
   * Полезная площадь — ЧАСТЬ общей, а не третье слагаемое: в `area_total_sp`
   * не входит и ни в одном расчёте не участвует (спека 2026-08-15 §2.2, §4).
   * Парой с надземной и подземной не связана — может быть заведена одна (§2.4).
   */
  area_useful_sp: Decimal | null;
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
  area_useful_sp?: Decimal | null;
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
  /**
   * Число ручных решений о статьях, сделанных ПО ЭТОЙ смете (задача 6).
   * Посметный, не по договору: форма замены предупреждает об утрате решений
   * именно заменяемой пары (contract_id, amendment_no), а паспорт для этого
   * не годится — он всегда про смету с `amendment_no IS NULL`.
   */
  category_overrides_count: number;
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
  /** Владелец задания (спека контура §2.13): договор либо раунд тендера. */
  owner_type: "contract" | "round";
  contract_id?: number;
  amendment_no?: number | null;
  tender_id?: number;
  round_id?: number;
  /** Сметы, созданные ЭТИМ заданием — только у раунда. */
  estimate_ids?: number[];
  filename: string;
  file_sha256: string;
  status: ImportJobStatus;
  error_text: string | null;
  warnings: string[];
  counters: ImportJobCounters;
  /** Смета ТЕКУЩЕЙ пары (contract_id, amendment_no), а не «этого задания». */
  estimate_id?: number | null;
  /**
   * 1 у договора, N(+1) у раунда; `null` у заданий до 0015 — И у задания,
   * которое ещё не завершилось (или завершилось `error`): счётчик появляется
   * вместе со сметами, на `done`, а не заранее.
   */
  estimates_created: number | null;
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
//  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13)
// ---------------------------------------------------------------------------

export interface TenderRow {
  id: number;
  tender_number: string;
  title: string;
  object_id: number;
  object_title: string;
  /** Снимок класса на момент торга (§4) — не класс объекта сейчас. */
  rate_class_id: number;
  rate_class_title: string;
  rounds_count: number;
  participants_count: number;
  created_at: string | null;
}

export interface TenderRoundRow {
  id: number;
  stage_no: number;
  label: string | null;
  held_on: string | null;
  latest_job: { id: number; status: ImportJobStatus; filename: string; finished_at: string | null; created_at: string | null } | null;
  /** Текущий done-job с полным набором смет (§2.12); null — файла нет или состав изменён. */
  current_job_id: number | null;
  baseline_estimate_id: number | null;
  baseline_total_including_vat: Decimal | null;
  /** Разделов раунда в состояниях unassigned+partial+conflict (спека этапного
   *  разноса §2.6); null — у раунда нет offer-смет, триггер разноса не рисуется. */
  unallocated_pending_sections: number | null;
}

export interface TenderParticipant {
  package_id: number;
  contractor_id: number;
  title: string;
  inn: string;
}

/** Ячейка решётки. Три состояния данными, не выводом клиента (§2.13):
 *  offer_id null — не участвовал; offer_id есть, estimate_id null — предложение
 *  было, сметы сейчас нет; оба есть — смета загружена. */
export interface TenderCell {
  round_id: number;
  package_id: number;
  offer_id: number | null;
  estimate_id: number | null;
  total_including_vat: Decimal | null;
}

export interface TenderCard extends Omit<TenderRow, "rounds_count" | "participants_count"> {
  notes: string | null;
  object_address: string | null;
  rounds: TenderRoundRow[];
  participants: TenderParticipant[];
  cells: TenderCell[];
}

export interface TenderInput {
  object_id: number;
  title: string;
  tender_number: string;
  rate_class_id?: number | null;
  notes?: string | null;
}

export interface RoundInput {
  stage_no: number;
  label?: string | null;
  held_on?: string | null;
}

export interface RoundImportJob extends ImportJob {
  is_current: boolean;
}

export interface UploadRoundInput {
  file: File;
  tender_id: number;
  round_id: number;
  replace?: boolean;
}

export interface ParticipantDeletionPreview {
  code: "confirmation_required";
  message: string;
  rounds_count: number;
  estimates_count: number;
  positions_count: number;
  overrides_count: number;
  confirmation_token: string;
}

// ---------------------------------------------------------------------------
//  Этапный разнос Нераспределённого (спека 2026-09-01-round-unallocated-design.md)
// ---------------------------------------------------------------------------

export type RoundSectionState = "unassigned" | "partial" | "conflict";
export type SectionKey = [lot_key: string, position_key_in_proposal: string];

export interface RoundSectionPartial {
  assigned: number;
  total: number;
  notes: (string | null)[];
}

export interface RoundSectionConflict {
  categories: { id: number; code: string; title: string }[];
  notes: (string | null)[];
  audit_differs: boolean;
}

interface RoundUnallocatedSectionBase {
  lot_key: string;
  position_key_in_proposal: string;
  parent_key: SectionKey | null;
  depth: number;
  number: string | null;
  title: string;
  smr_article_raw: string | null;
  rows: number;
}

export type RoundUnallocatedSection = RoundUnallocatedSectionBase & (
  | { state: "unassigned" }
  | { state: "partial"; partial: RoundSectionPartial }
  | { state: "conflict"; conflict: RoundSectionConflict }
);

export interface RoundManualAssignment {
  lot_key: string;
  position_key_in_proposal: string;
  number: string | null;
  title: string;
  rows: number;
  work_category_id: number;
  category_code: string;
  category_title: string;
  assigned_by_email: string;
  assigned_at: string;
  note: string | null;
}

export type RoundDiagnosticCode = "outside_structure" | "structure_disabled" | "unresolved_chapter_ref";

export interface RoundDiagnostic {
  code: RoundDiagnosticCode;
  contractor_title: string;
  title: string;
  rows: number;
}

export interface RoundUnallocated {
  round: { id: number; stage_no: number; label: string | null; held_on: string | null };
  offers_count: number;
  sections: RoundUnallocatedSection[];
  manual: RoundManualAssignment[];
  diagnostics: RoundDiagnostic[];
  category_options: ProjectPassportCategoryOption[];
}

export interface SetRoundCategoryOverrideInput {
  tenderId: number;
  roundId: number;
  lotKey: string;
  positionKey: string;
  workCategoryId: number;
  /** ВСЕГДА уходит в тело: null — явная очистка (§2.4). */
  note: string | null;
}

export interface ClearRoundCategoryOverrideInput {
  tenderId: number;
  roundId: number;
  lotKey: string;
  positionKey: string;
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

/**
 * Почему у ячейки нет ставки (правило цены, спека §2.5, задача 9 плана
 * 2026-09-09). Пять фактов о ПРИСУТСТВИИ входящих строк, разные и не сводимые
 * к одному прочерку:
 * - `unknown_vat_base` — база НДС хотя бы одной входящей строки неизвестна;
 * - `not_finite` — сегодняшний источник ЖИВОЙ: у ячейки нет ни одной входящей
 *   строки (предикат цены отсеял все), а среди позиций работы этого
 *   договора, исключённых из ставки, есть хотя бы одна с нефинитной ценой
 *   или весом (`_cell_without_ingesting` читает `any_not_finite` —
 *   `backend/crud/analytics.py`). Одноимённая ветка внутри `_fold_cell`
 *   (сумма/ставка сама стала `NaN`/`Infinity`) после предиката цены —
 *   практически недостижима: нефинитная цена или вес до свода уже не
 *   доезжают, ветка оставлена защитой на будущее, а не действующим путём;
 * - `no_weight` — ни одна входящая строка не несёт положительного веса
 *   (`_fold_cell`, `backend/crud/analytics.py`) — защитная ветка на случай
 *   потери единственной строки с положительным весом; на стороне
 *   `_cell_without_ingesting` (входящих строк нет вовсе) причина с тем же
 *   именем достижима уже сегодня;
 * - `negative_only` — среди позиций работы этого договора есть цены, но все
 *   отрицательные — входящих нет вовсе;
 * - `no_price` — фолбэк: ни одна позиция работы этого договора не несёт
 *   пригодной цены.
 *
 * Приоритет между причинами задаёт бэкенд (`_fold_cell`/
 * `_cell_without_ingesting`), фронт лишь показывает готовое значение.
 */
export type CellRateReason =
  | "unknown_vat_base"
  | "not_finite"
  | "no_weight"
  | "negative_only"
  | "no_price";

/**
 * `Record<CellRateReason, true>` — не публичный тип, только опора для
 * {@link CELL_RATE_REASONS} ниже: присвоение объектного литерала этому типу
 * требует РОВНО пяти ключей союза (лишний или забытый — ошибка `tsc`), тем же
 * механизмом, что и `Record<RateState, string | null>` в `rateLabels.ts`.
 */
const CELL_RATE_REASON_KEYS: Record<CellRateReason, true> = {
  unknown_vat_base: true,
  not_finite: true,
  no_weight: true,
  negative_only: true,
  no_price: true,
};

/**
 * Все значения {@link CellRateReason} одним массивом (ревью задачи 9: рукописный
 * список причин в тесте не заметил бы забытое или дублирующее значение —
 * `Object.keys` берёт их из объекта, чью полноту стережёт `tsc`, а не из
 * переписанного вручную литерала). Порядок — порядок объявления типа, не
 * значим для потребителей.
 */
export const CELL_RATE_REASONS = Object.keys(CELL_RATE_REASON_KEYS) as CellRateReason[];

/**
 * Ячейка: средневзвешенная ставка работы по договору (§6).
 *
 * Пересчёт НДС (спека пересчёта §2.4-2.5): хотя бы одна неизвестная база НДС
 * среди предложений, сложившихся в ячейку, гасит `rate`, `amount` и
 * `deviation_pct` ЦЕЛИКОМ — показать средневзвешенное по части строк значило
 * бы выдать неполную величину за полную. `standard_unit_rate` при этом НЕ
 * гаснет: норматив от НДС не зависит и есть нетто по определению (спека §2.5)
 * — на экране законно возможна строка, где ставка пуста, а норматив показан.
 *
 * **Две оси состояния (правило цены, спека §2.5, задача 9 плана
 * 2026-09-09).** `rate_reason` — почему нет ставки, `null` означает ровно
 * «ставка есть». `deviation_reason` — почему нет отклонения ЯЧЕЙКИ; когда
 * `rate_reason` не пуст, `deviation_reason` **всегда** равен `"no_rate"`
 * (сравнивать нечего вообще — другой факт, чем «сравнили и норматива не
 * нашли», который законен только при посчитанной ставке). До этой задачи обе
 * причины жили в одном поле `deviation_reason`, где `unknown_vat_base`/
 * `no_weight` означали «нет ставки», а `no_standard`/`not_finite` — «есть
 * ставка, нет отклонения»: два разных факта под одним именем. Задача 9 плана
 * разводит их по двум полям (`rate_reason`/{@link CellRateReason} и
 * `deviation_reason` ниже).
 */
export interface MatrixCell {
  contract_id: number;
  rate: Decimal | null;
  /** Нетто-вес ЭТОЙ ячейки (спека §2.6); `null` вместе с `rate`, когда `rate_reason` не пуст. */
  amount: Decimal | null;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
  /** См. {@link CellRateReason}. `null` — ставка есть. */
  rate_reason: CellRateReason | null;
  /**
   * Почему отклонения нет: разные факты нельзя сводить к одному прочерку.
   * `no_standard` — норматива на дату сравнения вовсе нет, сравнивать не с
   * чем (ставка при этом есть — `rate_reason` пуст). `not_finite` — дефект 1,
   * круг 3 (ре-ревью Codex, PR #21): средневзвешенная ставка ячейки цела и
   * конечна (иначе сработал бы `rate_reason: "not_finite"`), а нефинитным
   * оказался сам норматив, участвующий в делении, — до этой правки утечка
   * была бы видна буквальным `"NaN"`. `no_rate` — ставки нет вовсе
   * (`rate_reason` не пуст): единственное значение, законное вместе с
   * непустым `rate_reason`.
   */
  deviation_reason: "no_standard" | "not_finite" | "no_rate" | null;
}

export interface MatrixRow {
  catalog_position_id: number;
  job_title: string;
  unit_code: string | null;
  /** Вес строки в деньгах — по нему строки упорядочены (§6.4 отчёта фазы 6). */
  row_amount: Decimal | null;
  /**
   * Вес строки посчитан НЕ по всему, что в строке есть. Причин ДВЕ, и вторую
   * завело правило цены (спека `docs/superpowers/specs/2026-09-09-price-
   * predicate-design.md` §2.6):
   * - в каком-то договоре база НДС неизвестна — такая ячейка гаснет целиком и
   *   в вес не входит;
   * - из ставки исключена позиция с НЕНУЛЕВЫМ вкладом (цена или вес
   *   отрицательны либо нефинитны) — её вклад в вес не попал.
   *
   * Вторая причина НЕ означает, что ячейка скрыта: при смешанном наборе ставка
   * считается по вошедшим позициям и показывается, а флаг всё равно поднят.
   * Поэтому «флаг поднят» не равно «что-то не показано» — прежняя редакция
   * этого комментария утверждала обратное и устарела вместе со своим
   * механизмом. Текст на экране — сноска под таблицей в `MatrixPage.tsx`,
   * держать их согласованными.
   *
   * Признак обязателен на экране — иначе частичная сумма выглядит полной.
   */
  row_amount_incomplete: boolean;
  /** Ячейки только тех договоров, где работа встречается: список, не объект. */
  cells: MatrixCell[];
}

export interface Matrix {
  columns: MatrixColumn[];
  rows: MatrixRow[];
  total: number;
  page: number;
  page_size: number;
  /** Расценённые позиции, чья работа ещё не утверждена в каталоге (TO_REVIEW) — по договорам выборки. */
  positions_pending_review: number;
  /** Расценённые позиции, чья каталожная строка помечена как НЕ-работа (`HEADER`/`TRASH`/`LOT_HEADER`) — по договорам выборки. */
  positions_non_work: number;
  /**
   * Позиции сметы (`is_chapter = false`), не несущие пригодной цены (пустая,
   * нулевая, отрицательная либо не число) — по договорам выборки (правило
   * цены, спека §2.9, задача 5 плана 2026-09-09). **Условие НЕ смотрит на
   * состояние каталожной строки вовсе** (`_without_price_condition`,
   * `backend/crud/analytics.py`): позиция считается здесь независимо от того,
   * ждёт ли она матчинга, размечена как не-работа или уже утверждена
   * работой, — предикат цены делит счётчики ПЕРВЫМ, раньше состояния
   * каталога. Поэтому этот счётчик, взятый один, **не доказывает и не
   * опровергает**, разобран ли каталог у стоящих за ним позиций: нулевое
   * пересечение с `positions_pending_review`/`positions_non_work` означает
   * лишь то, что предикат цены отнёс каждую позицию РОВНО в одну из трёх
   * причин, а не то, что позиции без цены обязательно уже размечены.
   */
  positions_without_price: number;
}

export interface MatrixParams {
  rate_class_id?: number;
  date_from?: string;
  date_to?: string;
  q?: string;
  page?: number;
  page_size?: number;
}

/**
 * Причина невхождения ОДНОЙ строки drill-down в ставку ячейки — четыре
 * значения (`_row_exclusion_reason`, `backend/crud/analytics.py`; правило
 * цены, спека §2.8, задача 3/9 плана 2026-09-09). См. {@link MatrixCellItem.
 * excluded_reason}.
 */
export type CellItemExcludedReason = "no_price" | "negative" | "not_finite" | "no_weight";

const CELL_ITEM_EXCLUDED_REASON_KEYS: Record<CellItemExcludedReason, true> = {
  no_price: true,
  negative: true,
  not_finite: true,
  no_weight: true,
};

/**
 * Все значения {@link CellItemExcludedReason} одним массивом — тот же приём,
 * что у {@link CELL_RATE_REASONS}, и по той же причине (ревью задачи 9).
 */
export const CELL_ITEM_EXCLUDED_REASONS = Object.keys(
  CELL_ITEM_EXCLUDED_REASON_KEYS
) as CellItemExcludedReason[];

/**
 * Drill-down по ячейке: ВСЕ позиции работы последней сметы договора, а не
 * только вошедшие в среднюю ставку (правило цены, спека §2.8, задача 3/9
 * плана 2026-09-09).
 *
 * До задачи 3 плана правила цены носителем был `weight > 0` поверх VIEW
 * отклонений — строка без пригодной цены или веса в списке попросту не
 * появлялась. Носитель сменился на `_all_positions_select`: список несёт и
 * невошедшие строки, а `included`/`excluded_reason` называют, вошла ли строка
 * и почему нет, — иначе утверждение экрана «работа есть, цены нет» нечем было
 * бы подтвердить (ячейка `rate_reason: "no_price"` обязана открыть непустой
 * список строк).
 *
 * Пересчёт НДС (спека §2.4-2.5): `unit_cost_total` — валовое ИЗ ФАЙЛА, без
 * изменений; `unit_cost_net` — выведенное нетто той же строки, `null`, когда
 * строка не вошла либо база строки неизвестна; `vat_rate_base` — база МЕЖДУ
 * ними. Три подписи рядом — обещание §2.5 «валовое, нетто и база рядом»
 * выполняется на экране, а не только в JSON.
 */
export interface MatrixCellItem {
  position_item_id: number;
  job_title: string;
  unit_code: string | null;
  weight: Decimal | null;
  /**
   * Валовое из файла. `null` достижим (замечание внешнего ревью Codex, №3):
   * носитель — `_all_positions_select`, отдающий ВСЕ позиции, включая те, у
   * которых `PositionItem.unit_cost_total` сохранён пустым (`excluded_
   * reason: "no_price"` несёт как ноль, так и настоящий `NULL`). `Decimal`
   * без `| null` был неверен — экран это уже переживал молча (`MoneyCell`/
   * `formatDecimalMoney` трактуют `null` как «нет значения», прочерк), но
   * тип обязан называть то, что реально приходит.
   */
  unit_cost_total: Decimal | null;
  /** Ставка без НДС — та, что вошла в ячейку; `null`, если строка не вошла или база неизвестна. */
  unit_cost_net: Decimal | null;
  vat_rate_base: Decimal | null;
  total_cost_total: Decimal | null;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
  /**
   * Почему отклонения нет: разные факты нельзя сводить к одному прочерку.
   * `not_finite` — Дефект 1 ре-ревью Codex (PR #21): `unit_cost_total`
   * пришёл `NaN`/`Infinity` открытым хвостом Ф4 (§5.6) — норматив у строки
   * может БЫТЬ, база НДС может быть ИЗВЕСТНА, но сама величина не число, и
   * это не «нет норматива» и не «неизвестна база» (`backend/crud/
   * analytics.py::_net_deviation`). `no_rate` здесь не появляется никогда —
   * этот код придуман для ЯЧЕЙКИ (`MatrixCell`), а не для позиции (Global
   * Constraints плана правила цены).
   *
   * У невошедшей строки (`included` ложно) ОБА поля — `deviation_pct` и это
   * — пусты, но по ДРУГОЙ причине, чем «нет норматива»: причину невхождения
   * называет `excluded_reason`, а различитель, читаемый ПЕРВЫМ, —
   * `included` (`docs/insights/one-value-two-states.md`).
   */
  deviation_reason: "no_standard" | "unknown_vat_base" | "not_finite" | null;
  /** Вошла ли строка в среднюю ставку ячейки (спека §2.8, задача 9 плана правила цены). */
  included: boolean;
  /** Причина невхождения; не `null` тогда и только тогда, когда `included` ложно. */
  excluded_reason: CellItemExcludedReason | null;
}

export interface MatrixCellDetail {
  contract_id: number;
  catalog_position_id: number;
  estimate_id: number;
  amendment_no: number | null;
  items: MatrixCellItem[];
}

/**
 * Строка `key_rates[]` паспорта ФАЗЫ 6 (`GET /api/v1/analytics/passport/{id}`).
 *
 * Фронт этот эндпоинт не вызывает — паспорт объекта фазы 6 заменён паспортом
 * проекта фазы 7 (спека §1.3). Тип заведён, чтобы контракт не разошёлся молча:
 * пересчёт НДС провёл `unit_cost_net`/`vat_rate_base`/`deviation_reason` и сюда
 * тоже (тот же `_priced_positions_select`, что у drill-down, из соображения
 * симметрии), а роутер отдаёт `dict` через `decimal_json` без `response_model`
 * — несоответствие типов бэкенд не заметит, только фронт увидел бы «поле
 * пропало» задним числом.
 */
export interface PassportKeyRate {
  position_item_id: number;
  catalog_position_id: number;
  job_title: string;
  catalog_job_title: string;
  unit_code: string | null;
  weight: Decimal | null;
  unit_cost_total: Decimal;
  unit_cost_net: Decimal | null;
  vat_rate_base: Decimal | null;
  total_cost_total: Decimal | null;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
  /** См. `MatrixCellItem.deviation_reason` — тот же `_net_deviation`. */
  deviation_reason: "no_standard" | "unknown_vat_base" | "not_finite" | null;
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

// ---------------------------------------------------------------------------
//  Паспорт проекта по статьям классификатора (фаза 7, Ф6, спека §2.6)
// ---------------------------------------------------------------------------
//
//  Форма — зеркало `backend/crud/project_passport.py::get_project_passport`,
//  ключ в ключ: там сказано «форма ответа — решённый контракт, ключи и
//  вложенность менять нельзя». Старые `Passport*` (фаза 6) удалены задачей 11:
//  экран паспорта объекта переехал на этот тип.
//
//  Деньги — decimal-СТРОКИ (`Decimal`), а не `number`, и это касается КАЖДОГО
//  поля ниже, отмеченного этим типом: `share_pct`, `per_sqm`, `total`, `own`,
//  `amount`, `file_total_including_vat`, `delta_to_file_total`, обеих площадей
//  и `area_total_sp`, трёх `*_pct` условий договора, `vat_rate`. Приводить их
//  к `number` нельзя НИГДЕ — только форматировать на слое представления
//  (`formatDecimalMoney`/`MoneyCell`).

/** Реквизиты и коммерческие условия договора для паспорта проекта (спека §2.6). */
export interface ProjectPassportContract {
  id: number;
  contract_number: string;
  title: string | null;
  signer: string | null;
  signed_date: string;
  object_id: number;
  object_title: string;
  contractor_title: string;
  rate_class_title: string;
  advance_pct: Decimal | null;
  advance_note: string | null;
  bank_guarantee_pct: Decimal | null;
  bank_guarantee_note: string | null;
  retention_pct: Decimal | null;
  retention_note: string | null;
  /** Сколько договоров у объекта всего — для бейджа на экране (правило 7). */
  object_contracts_count: number;
}

/** ТЭП объекта для паспорта проекта — те же четыре величины, что у {@link ObjectItem}. */
export interface ProjectPassportObject {
  id: number;
  title: string;
  area_underground_sp: Decimal | null;
  area_aboveground_sp: Decimal | null;
  area_total_sp: Decimal | null;
  /** Показывается, но в ₽/м² не участвует: знаменатель — общая (спека §2.2). */
  area_useful_sp: Decimal | null;
}

/** Исходная смета договора (правило «исходная», не «последняя» — спека §2.4). */
export interface ProjectPassportEstimate {
  id: number;
  /** `null` — исходная смета не имеет допсоглашения. */
  amendment_no: number | null;
  title: string | null;
  data_prepared_on_date: string | null;
  /** Может законно отсутствовать (правило 9). */
  parser_version: string | null;
  /** `null` при разногласии ставок предложений или их отсутствии (правило 11). */
  vat_rate: Decimal | null;
  /** База, назначенная человеком; `null` — база берётся из файла (спека пересчёта §2.7). */
  vat_rate_base_override: Decimal | null;
  /** Ставка показа; `null` — показываем в базовой (спека пересчёта §2.7). */
  vat_rate_target: Decimal | null;
  /** Когда правили ставки; `null` — поправок нет. */
  vat_rate_updated_at: string | null;
  /**
   * Ставка, в которой ФАКТИЧЕСКИ показаны деньги паспорта (`totals.amount`,
   * `per_sqm`, суммы статей) — приоритет цель → назначенная база → единогласная
   * заявленная (спека пересчёта §5.1, `money.vat.effective_display_rate`,
   * `crud/project_passport.py:1321`). `null` — при разногласии заявленных
   * ставок предложений: единой ставки нет, пересчёт не применяется.
   *
   * Считается на сервере ОДИН раз и приходит уже готовым — фронт обязан читать
   * это поле, а не выводить эффективную ставку заново арифметикой из
   * `vat_rate`/`vat_rate_base_override`/`vat_rate_target`: правило приоритета
   * уже реализовано на сервере, и вторая копия того же правила разъедется
   * молча при первой же его правке (задача 10, приложение оркестратора п. 4).
   */
  vat_display_rate: Decimal | null;
}

/** Ответ `PATCH /v1/estimates/{id}/vat` (спека пересчёта §2.7) — новое состояние ставок сметы. */
export interface EstimateVatState {
  estimate_id: number;
  vat_rate_base_override: Decimal | null;
  vat_rate_target: Decimal | null;
  vat_rate_updated_at: string | null;
}

/** Строка допработы вне VIEW: гранулярность нужна поштучно (правило 13). */
export interface ProjectPassportExtra {
  id: number;
  ordinal: number;
  title: string;
  amount: Decimal;
}

/** Раздел сметы, давший узлу его собственные деньги (правило 14). */
export interface ProjectPassportSection {
  id: number;
  number: string | null;
  title: string;
  /** `'file'` — раздел получил статью из клетки «Статья СМР»; `'manual'` — статья назначена вручную (спека разноса). */
  source: "file" | "manual";
}

// ---------------------------------------------------------------------------
//  Объём и ставка ₽/ед. в таблице статей (спека 2026-08-24, §2.5, §2.10)
// ---------------------------------------------------------------------------
//
//  Состояние вычисляется на сервере и приходит полем — клиент НЕ выводит его
//  из комбинации `null`-ов: правило одно, носитель один (§2.5). Проверки идут
//  строго сверху вниз, первое совпадение выигрывает — у узла ровно одно
//  состояние. Порядок здесь — порядок таблицы §2.5, не алфавит.

/**
 * Состояние ставки узла свода — ровно одно из одиннадцати, на КАЖДОМ узле,
 * включая корни классификатора и статьи ручного разноса (спека §2.5, §2.10).
 * Клиент читает готовое значение, а не пересчитывает его из `unit`/`volume`/
 * `unit_rate`.
 *
 * `amount_zero` (правило цены, задача 6 плана 2026-09-09) стоит в автомате
 * бэкенда девятой проверкой — ПОСЛЕ `volume_nonpositive`, ДО
 * `volume_inconsistent` (`backend/services/article_rates.py::RateState`):
 * строки-носители есть, сумма свёртки конечна и равна нулю, единица
 * масштабируема, объём конечен и положителен. Отличается от `amount_missing`
 * (сумма не прочитана вовсе) тем, что величина ИЗВЕСТНА и равна нулю — тот же
 * принцип, что у `total`/`share_pct` узла (`null` ≠ ноль, оба отличимы).
 */
export type RateState =
  | "no_carrier"
  | "additional_works"
  | "amount_missing"
  | "unit_missing"
  | "unit_conflict"
  | "unit_not_scalable"
  | "volume_missing"
  | "volume_nonpositive"
  | "amount_zero"
  | "volume_inconsistent"
  | "rate";

/**
 * Уточнение состояния `volume_inconsistent` — ТРИ причины одного гашения
 * ставки (спека §2.10, ревизия гейта 3): смешанные единицы детей, перебор
 * объёма родителя, либо сходимость, которую нечем проверить (ребёнок сам
 * несёт `unit_missing`/`unit_conflict`). `null` вне этого состояния.
 */
export type RateNote = "overshoot" | "mixed_units" | "unverifiable";

/** Узел дерева статей — элемент ПЛОСКОГО списка `categories` (правило 2). */
export interface ProjectPassportCategory {
  id: number;
  code: string;
  title: string;
  parent_id: number | null;
  is_bucket: boolean;
  sort_order: number;
  /** `null` — статья отсутствует в смете, а не «ноль»; ноль тоже возможен и отличим. */
  total: Decimal | null;
  rows: number;
  rows_priced: number;
  rows_not_finite: number;
  /** Доля от `totals.amount` — единый знаменатель для ВСЕХ строк (правило 5). */
  share_pct: Decimal | null;
  per_sqm: Decimal | null;
  /** Собственные деньги узла (без детей) — НЕ «родитель минус дети» (см. crud). */
  own: Decimal | null;
  own_rows: number;
  own_rows_priced: number;
  own_rows_not_finite: number;
  extras: ProjectPassportExtra[];
  own_sections: ProjectPassportSection[];
  /** СИМВОЛ единицы (`units_of_measure.symbol`): «м²», «м³», «шт»; `null` при `unit_missing` — не имя (спека §2.10, ревизия гейта 3). */
  unit: string | null;
  /** Объём с носителя статьи — строки, несущей код узла. `null` во всех состояниях, КРОМЕ `rate` (спека §2.5, §2.10). */
  volume: Decimal | null;
  /** Приведённая сумма / объём (§2.2). `null` во всех состояниях, КРОМЕ `rate`. */
  unit_rate: Decimal | null;
  /** См. {@link RateState}. */
  rate_state: RateState;
  /** См. {@link RateNote} — не `null` ровно при `rate_state === "volume_inconsistent"`. */
  rate_note: RateNote | null;
}

/**
 * Пять исходов охвата ставками (§2.8) — состояние ОТДЕЛЬНОЕ от `money_share`,
 * а не перегруженный `null`: «итог паспорта неизвестен» и «суммы требуют
 * проверки» — разные факты с разными действиями (`docs/insights/
 * one-value-two-states.md`). Проверки — строго сверху вниз, первое совпадение
 * выигрывает.
 */
export type MoneyShareState = "complete" | "partial" | "no_articles" | "total_unavailable" | "out_of_range";

/**
 * Охват ставками — строка §2.8 под таблицей статей («покрыто K % цены
 * договора: ставка есть у N статей из M в неперекрывающемся наборе»). Печать
 * самой строки — задача 7, не эта: здесь только контракт поля.
 */
export interface ProjectPassportRateCoverage {
  /** N — статьи неперекрывающегося набора §2.3 со состоянием `rate`. */
  articles_with_rate: number;
  /** M — размер неперекрывающегося набора §2.3 (не справочник и не все видимые строки дерева). */
  articles_total: number;
  /** K — decimal-строка `0..100`; `null` при `money_share_state !== "complete" | "partial"`. */
  money_share: Decimal | null;
  /** См. {@link MoneyShareState}. */
  money_share_state: MoneyShareState;
}

/** Раздел сметы без статьи — узел ДЕРЕВА разносимого (спека разноса §2.6). */
export interface ProjectPassportUnallocatedSection {
  position_item_id: number;
  /** `null` — узел верхнего уровня внутри нераспределённой части. */
  parent_position_item_id: number | null;
  number: string | null;
  title: string;
  depth: number;
  /**
   * Свои прямые позиции (`SUM` только по расценённым). `null` — нет ни одной
   * своей расценённой суммы, а это ДВА разных случая, а не один: своих строк
   * нет вовсе (`rows: 0`) — и своих строк ЕСТЬ, но ни одна не расценена
   * (`rows > 0`, `rows_priced === 0`). Различает их именно пара `rows`/
   * `rows_priced` рядом, а не сам `amount`.
   */
  amount: Decimal | null;
  /** Итог поддерева — именно он показывает цену решения на этой вершине. */
  subtree_amount: Decimal | null;
  rows: number;
  rows_priced: number;
  rows_not_finite: number;
  /**
   * Что стояло в клетке «Статья СМР»; `null` — файл молчал. Различие причин
   * выражается ровно этим полем, отдельного `reason` нет намеренно.
   */
  smr_article_raw: string | null;
}

/**
 * Действующее ручное решение о статье раздела (спека разноса).
 *
 * **`subtree_amount`/`rows`/`rows_priced`/`rows_not_finite` здесь значат
 * ДРУГОЕ, чем в {@link ProjectPassportUnallocated.sections}, хотя поля
 * называются одинаково.** Там — обрезанная свёртка ТОЛЬКО наследуемой (ещё
 * нераспределённой) части поддерева. Здесь — ПОЛНАЯ файловая свёртка по всей
 * структуре под решённым разделом, без разбора статей внутренних узлов, и она
 * НЕ обрезана намеренно: если внутри поддерева решённого раздела есть своё
 * вложенное решение (или свой валидный файловый код), его деньги всё равно
 * входят в файловую свёртку внешнего решения. Эти числа НЕ складываются между
 * вложенными решениями и не сравнимы с числами `unallocated.sections[]`
 * напрямую — это учётная запись «сколько денег лежит под этим решением по
 * факту файла», а не «сколько денег это решение продолжает двигать сейчас».
 */
export interface ProjectPassportManualAssignment extends ProjectPassportUnallocatedSection {
  work_category_id: number;
  category_code: string;
  category_title: string;
  assigned_by_email: string;
  assigned_at: string;
  note: string | null;
}

/**
 * Вариант выбора статьи при ручном разносе. Источник — `category_options`, а
 * НЕ `categories`: `build_tree` прячет вложенные узлы дерева без строк, а
 * разносить надо в том числе в статьи, которых в смете ещё нет вовсе.
 */
export interface ProjectPassportCategoryOption {
  id: number;
  code: string;
  title: string;
  is_bucket: boolean;
}

/** «Нераспределённое»: деньги без статьи, с двумя РАЗНЫМИ причинами (правило спеки §2.6). */
export interface ProjectPassportUnallocated {
  amount: Decimal | null;
  rows: number;
  rows_priced: number;
  rows_not_finite: number;
  share_pct: Decimal | null;
  per_sqm: Decimal | null;
  /** Разделы без статьи, под которыми есть хотя бы одна позиция. */
  chapters: number;
  /** Позиции вовсе без ссылки на раздел — другая причина, считается отдельно. */
  rows_outside_structure: number;
  extras: ProjectPassportExtra[];
  /** Дерево разделов без статьи (спека разноса §2.6) — экран разноса показывает иерархию, не плоский список. */
  sections: ProjectPassportUnallocatedSection[];
}

/** Вердикт сверки выведенного нетто с файловым (спека пересчёта §2.10). */
export type NetReconciliationStatus = "ok" | "mismatch" | "unknown_base" | "not_applicable";

/** Сверка выведенного нетто с заявленным в файле, по предложениям сметы (§2.10). */
export interface NetReconciliation {
  status: NetReconciliationStatus;
  /** Decimal-строка; `null` — сравнимых предложений нет. */
  delta: Decimal | null;
  mismatched_proposal_ids: number[];
}

export interface ProjectPassportTotals {
  /** Сумма ТОЛЬКО известных слагаемых (корни дерева + `unallocated`, правило 3). */
  amount: Decimal | null;
  per_sqm: Decimal | null;
  positions_rows: number;
  positions_rows_priced: number;
  positions_rows_not_finite: number;
  additional_works_rows: number;
  /** Файловое «Итого включая НДС» по всем предложениям сметы (спека §2.5). */
  file_total_including_vat: Decimal | null;
  /** Требует ДВА известных операнда — `null`, если хотя бы один неизвестен (правило 12). */
  delta_to_file_total: Decimal | null;
  /** Расхождение выведенного нетто с файловым, по предложениям (спека пересчёта §2.10). */
  net_reconciliation: NetReconciliation;
}

/** Паспорт проекта по статьям классификатора (Ф6 фазы 7, спека §2.6). */
export interface ProjectPassport {
  contract: ProjectPassportContract;
  object: ProjectPassportObject;
  /** `null` — у договора ещё нет сметы; дерево статей — пустой скелет (правило 8). */
  estimate: ProjectPassportEstimate | null;
  totals: ProjectPassportTotals;
  categories: ProjectPassportCategory[];
  unallocated: ProjectPassportUnallocated;
  /** Действующие ручные решения о статьях — по всем разделам сметы (спека разноса). Допработы следуют производно от разделов. */
  manual_assignments: ProjectPassportManualAssignment[];
  /** Варианты для выбора статьи при разносе — см. {@link ProjectPassportCategoryOption}. */
  category_options: ProjectPassportCategoryOption[];
  /** Охват ставками (спека объёма и ставки §2.8, §2.10) — см. {@link ProjectPassportRateCoverage}. */
  rate_coverage: ProjectPassportRateCoverage;
}

// ---------------------------------------------------------------------------
//  Ручной разнос разделов по статьям (спека разноса)
// ---------------------------------------------------------------------------

/**
 * Сводка изменений после разноса — НЕ паспорт целиком: форма паспорта
 * объявлена ровно один раз (см. {@link ProjectPassport} выше), и вторая её
 * копия здесь разошлась бы с первой при первом же изменении.
 */
export interface CategoryOverrideChangeSummary {
  chapters_updated: number;
  additional_works_updated: number;
  /**
   * Строк (не решений!), чья ДЕЙСТВУЮЩАЯ статья ручная — свои и унаследованные
   * от родителя вместе. Число решений может быть меньше: одно решение на
   * вершине дерева накрывает статьёй все строки поддерева.
   */
  chapters_manual: number;
}

/**
 * Назначить статью разделу (`PUT .../category-overrides/{id}`).
 *
 * `contractId` эндпоинту не нужен — но нужен инвалидации: запрос паспорта
 * ключуется договором, а не сметой (см. `useSetCategoryOverride`).
 */
export interface SetCategoryOverrideInput {
  contractId: number;
  estimateId: number;
  positionItemId: number;
  workCategoryId: number;
  note?: string | null;
}

/** Снять ручное решение (`DELETE .../category-overrides/{id}`) — см. {@link SetCategoryOverrideInput}. */
export interface ClearCategoryOverrideInput {
  contractId: number;
  estimateId: number;
  positionItemId: number;
}

// ---------------------------------------------------------------------------
//  Стартовый дашборд (спека 2026-08-16)
// ---------------------------------------------------------------------------

/**
 * ОХВАТОВ ДВА, и типы это сохраняют. Договорный охват защищает деньги,
 * объектный — рейтинг и диаграмму; у каждого свой набор причин, поэтому
 * `reasons` типизирован раздельно, а не общим `Record<string, number>`:
 * общая запись позволила бы сложить счётчики разных сущностей, чего решение 9
 * макета прямо запрещает («5 договоров» и «1 объект» — разные вещи).
 */
export type DashboardContractReason =
  | "no_estimate"
  | "amendment"
  | "no_rate"
  | "incomplete";

/**
 * «Нет договоров вовсе» и «договоры есть, но ни один не учтён» — РАЗНЫЕ причины
 * (находка ревью Codex). Вторую объясняет договорная половина охвата, первую не
 * объясняет никто, и слитые в одну они прятали объект без договоров.
 */
export type DashboardObjectReason =
  | "many_contracts"
  | "no_contracts"
  | "no_counted_contract";

export interface DashboardCoverage {
  total: number;
  counted: number;
}

export interface DashboardContractCoverage extends DashboardCoverage {
  reasons: Record<DashboardContractReason, number>;
}

export interface DashboardObjectCoverage extends DashboardCoverage {
  reasons: Record<DashboardObjectReason, number>;
}

/** Слагаемое площади со СВОИМ охватом (решение 4 макета). */
export interface DashboardAreaBlock {
  value: Decimal | null;
  coverage: DashboardCoverage;
}

export interface DashboardNamedArea {
  object_id: number;
  title: string;
  area_total_sp: Decimal | null;
}

export interface DashboardAreas {
  total: DashboardAreaBlock;
  aboveground: DashboardAreaBlock;
  underground: DashboardAreaBlock;
  useful: DashboardAreaBlock;
  largest: DashboardNamedArea | null;
  smallest: DashboardNamedArea | null;
}

export interface DashboardCounters {
  objects: number;
  classes: number;
  contracts: number;
  contracts_with_estimate: number;
}

export interface DashboardPerSqmPoint {
  object_id: number;
  title: string;
  per_sqm: Decimal | null;
  area_total_sp: Decimal | null;
  rate_class_title: string | null;
}

export interface DashboardPerSqmExtremes {
  max: DashboardPerSqmPoint | null;
  min: DashboardPerSqmPoint | null;
  coverage: DashboardCoverage;
}

export interface DashboardRankingCard {
  object_id: number;
  title: string;
  rate_class_id: number | null;
  rate_class_title: string | null;
  area_total_sp: Decimal | null;
  amount: Decimal | null;
  per_sqm: Decimal | null;
  display_rate: Decimal | null;
  contract: {
    id: number | null;
    contract_number: string | null;
    signed_date: string | null;
    contractor_title: string | null;
  };
}

export interface DashboardChartPoint {
  object_id: number;
  title: string;
  per_sqm: Decimal;
  amount: Decimal | null;
  area_total_sp: Decimal | null;
}

export interface DashboardChartLane {
  rate_class_id: number;
  rate_class_title: string | null;
  points: DashboardChartPoint[];
  /** `null` у класса с ОДНИМ объектом: размаха не существует (решение 11). */
  spread: { min: Decimal; max: Decimal } | null;
}

export interface DashboardChart {
  classes: DashboardChartLane[];
  coverage: DashboardCoverage;
}

export interface Dashboard {
  money: { amount: Decimal; coverage: DashboardContractCoverage };
  areas: DashboardAreas;
  counters: DashboardCounters;
  per_sqm: DashboardPerSqmExtremes;
  ranking: DashboardRankingCard[];
  ranking_coverage: DashboardObjectCoverage;
  chart: DashboardChart;
}

/**
 * Пять счётчиков решения 12 — БЕЗ списков затронутых сущностей: списки нужны
 * были бы действиям, а действия отложены решением гейта 3.
 */
export interface DashboardAttention {
  estimates_without_vat_rate: number;
  objects_with_several_contracts: number;
  contracts_without_estimate: number;
  objects_without_area: number;
  failed_imports_30d: number;
}

// ---------------------------------------------------------------------------
//  Свод по этапам (спека 2026-08-27-stage-summary-design.md §2.16)
// ---------------------------------------------------------------------------

/**
 * Состояние ячейки свода: конкретное значение, снято, не оценено, отсутствует в смете.
 */
export type CellState = "amount" | "removed" | "not_evaluated" | "absent";

/**
 * Вид изменения: процент, только сумма, появление/исчезновение, отсутствие изменения.
 */
export type ChangeKind = "percent" | "abs_only" | "appeared" | "reappeared" | "removed" | "disappeared" | "none";

/**
 * Направление изменения: рост, снижение, без изменения.
 */
export type Direction = "up" | "down" | "flat";

/**
 * Причина, по которой `Change.reason` называет отсутствие числа (спека
 * §2.16, `Change.reason`): закрытое множество, а не открытая строка — иначе
 * компилятор не поймает код, который сервер перестал присылать (находка
 * ревью PR: словарь подписей `cellCopy.REASON_LABEL` держался открытым только
 * из-за этого поля).
 */
export type StageSummaryChangeReason = "first_column" | "unknown_vat_base" | "no_amounts" | "unallocated";

/**
 * Изменение между этапами: вид, значение, направление, причина отсутствия.
 */
export interface StageSummaryChange {
  kind: ChangeKind;
  value: Decimal | null;
  direction: Direction | null;
  reason: StageSummaryChangeReason | null;
}

/**
 * Ячейка таблицы свода — ячейка СТАТЬИ (спека §2.16: инварианты про `state`
 * относятся только к ней). Строка «Итого» несёт другой тип — {@link StageSummaryTotalCell}.
 */
export interface StageSummaryCell {
  state: CellState;
  amount: Decimal | null;
  amount_unavailable_reason: "unknown_vat_base" | null;
  additional_works_amount: Decimal | null;
  rows: { row_count: number; rows_with_amount: number; rows_not_finite: number };
  change: StageSummaryChange;
}

/**
 * Ячейка строки «Итого» (спека §2.16, ревизия 28.08.2026 по внешнему ревью
 * PR #34) — АГРЕГАТ, а не статья, и структурно другой тип, а не {@link StageSummaryCell}
 * с той же формой: у него нет поля `state` (состояния «снято»/«не оценивалась»/
 * «нет в файле» к сумме неприменимы по смыслу — сумма нулей равна нулю, а не
 * «неизвестна») и нет `additional_works_amount` (спека прямо перечисляет обе
 * дырки в наборе полей). При известной оси `amount` — ВСЕГДА число, включая
 * `"0.00"` и колонку без единой строки; `null` возможен единственно при
 * `amount_unavailable_reason = "unknown_vat_base"`. Прежняя редакция
 * типизировала итог как `StageSummaryCell` и на нулевом итоге с живыми
 * строками рисовала пилюлю «снято» вместе с суммой «0.00» — нарушение
 * инвариантов контракта; правка структурная, а не патч одного поля.
 */
export interface StageSummaryTotalCell {
  amount: Decimal | null;
  amount_unavailable_reason: "unknown_vat_base" | null;
  rows: { row_count: number; rows_with_amount: number; rows_not_finite: number };
  change: StageSummaryChange;
}

/**
 * Причина, по которой у статьи нет числового вклада в итог (спека §2.16,
 * `Row.contribution.reason`) — закрытое множество, зеркало
 * {@link StageSummaryChangeReason} по тому же основанию.
 */
export type StageSummaryContributionReason = "absent_endpoint" | "unknown_vat_base";

/**
 * Строка свода: статья, ячейки по этапам, изменение всей статьи, дети статьи.
 */
export interface StageSummaryRow {
  work_category_id: number | null;
  code: string | null;
  title: string;
  is_unallocated: boolean;
  /**
   * Есть ли у статьи попозиционное разложение (спека 2026-08-30-position-
   * drilldown-design.md §2.12): единственная правка контракта фичи 3.
   * Считается по ОБЕИМ ветвям и по всему поддереву — статья с одними
   * допработами или без своих строк, но со строками потомков, тоже получает
   * `true`. У «Нераспределённого» — всегда `false`: разложение адресуется
   * `work_category_id`, которого у этой строки нет (`crud/stage_summary.py`).
   */
  has_drilldown_rows: boolean;
  /**
   * Число ГРУПП попозиционного разложения в поддереве статьи (спека
   * 2026-08-30-position-drilldown-design.md §2.1, §2.2; ветка
   * `feat/drilldown-polish`) — единственный источник N для кнопки
   * «Работы · N»: до этой правки число приходило только ПОСЛЕ первой загрузки
   * самого блока разложения (`PositionDrilldown` сообщал его вызовом
   * `onCount`), и до клика кнопка не несла числа вовсе. Сервер считает то же
   * значение НЕЗАВИСИМО от `has_drilldown_rows` (другим запросом, сверёткой
   * ключей `load_groups`) и проверяет тестом, что они не могут разойтись:
   * поле равно 0 ТОГДА И ТОЛЬКО ТОГДА, когда `has_drilldown_rows` ложно. У
   * «Нераспределённого» — всегда 0 (`crud/stage_summary.py`), по той же
   * причине, что и у `has_drilldown_rows`: адресовать разложение нечем.
   */
  drilldown_group_count: number;
  cells: StageSummaryCell[];
  bargain: StageSummaryChange;
  contribution: { value: Decimal | null; direction: Direction | null; reason: StageSummaryContributionReason | null };
  children: StageSummaryRow[];
}

/**
 * Колонка таблицы свода: метаданные раунда, ставки НДС, итог, сходимость.
 */
export interface StageSummaryColumn {
  kind: "round";
  offer_id: number;
  estimate_id: number;
  round_id: number;
  stage_no: number;
  label: string | null;
  held_on: string | null;
  vat_rate_base: Decimal | null;
  vat_state: "known" | "unknown_vat_base";
  total: Decimal | null;
  total_change: StageSummaryChange;
  bar_height_pct: Decimal | null;
  manual_overrides: { count: number; last_at: string | null };
  convergence: {
    categories_sum: Decimal;
    file_total: Decimal | null;
    converged: boolean | null;
    delta: Decimal | null;
    reason: "file_total_unavailable" | null;
  };
}

/**
 * Свод по этапам одного участника: строки и колонки таблицы, КПИ, трек.
 */
export interface StageSummary {
  tender: { id: number; tender_number: string; title: string; object_title: string };
  participant: {
    package_id: number;
    contractor_id: number;
    title: string;
    inn: string;
    rounds_with_estimate: number;
    stages: { stage_no: number; label: string | null; offer_id: number; selected: boolean }[];
  };
  columns: StageSummaryColumn[];
  rows: StageSummaryRow[];
  unallocated: StageSummaryRow;
  total: { cells: StageSummaryTotalCell[] };
  display: {
    tax_basis: "gross" | "net" | "none";
    reason: "single_rate" | "mixed_rates" | "no_known_rates";
    rates_by_column: (Decimal | null)[] | null;
    price_level: "nominal";
  };
  kpi: {
    stages_selected: number;
    stages_loaded: number;
    last_stage_positions: number;
    categories_with_amount: number;
    categories_total: number;
    first_to_last: StageSummaryChange;
  };
  track: { available: boolean; reason: "non_positive_total" | "no_comparable_totals" | null };
}

/**
 * Код ошибки отказа свода.
 */
export type StageSummaryErrorCode =
  | "tender_not_found"
  | "offer_not_found"
  | "too_few_offers"
  | "one_offer_per_round"
  | "single_participant"
  | "offer_has_no_estimate";

/**
 * Деталь ошибки отказа свода.
 */
export interface StageSummaryErrorDetail {
  code: StageSummaryErrorCode;
  message: string;
  offers: number[];
}

// ---------------------------------------------------------------------------
//  Попозиционное раскрытие статьи (спека 2026-08-30-position-drilldown-
//  design.md §2.11, §2.12): третий уровень свода по этапам — GET
//  /v1/tenders/{tender_id}/stage-summary/{work_category_id}?offers=…
// ---------------------------------------------------------------------------

export type StagePositionsRowKind =
  | "position"
  | "additional_works"
  | "unmatched"
  | "collapsed_appeared_disappeared"
  | "rest";

export interface StagePositionsCell {
  state: CellState;
  amount: Decimal | null;
  amount_unavailable_reason: "unknown_vat_base" | null;
  /** Сырые значения через "+" (несколько строк сметы) — формат берёт на себя клиент. */
  quantity: string | null;
  quantity_unit: string | null;
  quantity_changed: boolean;
  estimate_rows: number;
  change: StageSummaryChange;
}

export interface StagePositionsRow {
  kind: StagePositionsRowKind;
  /**
   * Устойчивая идентичность строки в ответе (§2.11) — ключ React берётся
   * ОТСЮДА, а не собирается из `kind` + `chapter_ref_raw`: две строки
   * `additional_works` разных лотов делят одну и ту же ссылку раздела и под
   * собранным ключом схлопнулись бы в одну (третий круг ревью плана
   * 31.08.2026). У свёрнутых строк здесь `"collapsed"`/`"rest"`.
   */
  row_key: string;
  catalog_position_id: number | null;
  chapter_ref_raw: string | null;
  /**
   * Лот из ключа группировки; заполнен ТОЛЬКО у `kind === "additional_works"`.
   * В пилюлю экран выносит его лишь когда в ответе больше одного лота (§2.7) —
   * иначе одинаковые ссылки одного лота получили бы лишнее слово в подписи.
   */
  lot_key: string | null;
  title: string;
  ambiguous: boolean;
  /** У свёрнутых строк — сколько групп внутри; иначе `null`. */
  group_count: number | null;
  cells: StagePositionsCell[];
  bargain: StageSummaryChange;
  contribution: { value: Decimal | null; direction: Direction | null; reason: null };
}

export interface StagePositions {
  work_category: { id: number; code: string; title: string };
  columns: {
    offer_id: number;
    estimate_id: number;
    round_id: number;
    stage_no: number;
    label: string | null;
    held_on: string | null;
  }[];
  display: {
    tax_basis: "gross" | "net" | "none";
    reason: "single_rate" | "mixed_rates" | "no_known_rates";
  };
  rows: StagePositionsRow[];
  /** Обещание §2.13, проверяемое тестом, а не глазами — сумма строк равна показанному итогу статьи. */
  convergence: {
    stage_no: number;
    article_amount: Decimal | null;
    shown_sum: Decimal | null;
    converged: boolean | null;
    reason: "unknown_vat_base" | null;
  }[];
  /** Статья без строк в поддереве ни в одной колонке — не ошибка (§2.11). */
  reason: "no_rows_in_subtree" | "unknown_vat_base" | null;
}

// ---------------------------------------------------------------------------
//  Сравнение договоров (спека 2026-08-17, задача 8)
// ---------------------------------------------------------------------------
//
// Форма — зеркало `backend/crud/comparison.py::build_comparison` (см. также
// `_cell_entry`, `_bucket_cell_dict`, `_median_dict`, `_load_columns`): ключи
// и вложенность как в ответе сервера, менять нельзя.
//
// Деньги — decimal-СТРОКИ (`Decimal`), НЕ `number` (§3): `net`, `shown`,
// `net_per_sqm`, `shown_per_sqm`, `deviation_pct`, `value` медианы,
// `area_total_sp`, три `*_pct` условия договора, `single_rate`,
// `rate_options`, `rate_preselected`. Приводить их к `number` нельзя нигде —
// только на слое показа (`MoneyCell`/`roundDecimalPercent`).

// ---------------------------------------------------------------------------
//  Поправка на инфляцию (спека 2026-08-18 §2.6, §2.11, §2.12)
// ---------------------------------------------------------------------------
//
//  `coefficient` — decimal-СТРОКА (`Decimal`), как все numeric проекта (§3
//  AGENTS.md). Приводить его к `number` нельзя нигде: в JS `number` это
//  IEEE-754 double, а коэффициент участвует в расчёте, который защищают перед
//  банком. Расшифровка уровня считается точной арифметикой строк
//  (`lib/inflation.ts`), а не через `Number()`.

/** Ряд индексов инфляции — именованный показатель, а не «официальный» (§2.6). */
export interface InflationSeries {
  id: number;
  name: string;
  /** Примечание ряда. Именно ЕГО показывает полоса уровней на `/compare` (§2.12). */
  note: string | null;
  is_active: boolean;
  /** Охват годов: `null` у ряда без значений — он законен, годы вводят вразнобой. */
  year_from: number | null;
  year_to: number | null;
  value_count: number;
  created_at: string;
  updated_at: string;
}

/** Значение ряда за один год: `k(y)` — декабрь года `y` к декабрю `y−1` (§2.3). */
export interface InflationSeriesValue {
  year: number;
  coefficient: Decimal;
  /** Источник ГОДА. Печатается на листе выгрузки, на экране сравнения НЕ показывается. */
  source: string;
  is_forecast: boolean;
  created_at: string;
  updated_at: string;
}

/** Год в теле запроса — ВСЕ ТРИ поля обязательны: это описание года целиком (§2.12). */
export interface InflationSeriesValueInput {
  year: number;
  coefficient: Decimal;
  source: string;
  is_forecast: boolean;
}

export interface InflationSeriesInput {
  name: string;
  note?: string | null;
  values: InflationSeriesValueInput[];
}

/**
 * Тело `PATCH`. Годы, не перечисленные в `values`, ОСТАЮТСЯ — это `PATCH`, а
 * `DELETE` запрещён (§2.10).
 *
 * `{ is_active: true }` в ОДИНОЧКУ размораживает архивный ряд; вместе с полями
 * либо с `values` (даже пустым массивом) сервер отвечает `409`. Поэтому кнопки
 * «Вернуть в активные» и «Изменить» — два разных запроса, а не один.
 */
export interface InflationSeriesPatch {
  name?: string;
  note?: string | null;
  is_active?: boolean;
  values?: InflationSeriesValueInput[];
}

/** Использованный год приведения: пять фактов, из которых лист печатает все (§2.10). */
export interface ComparisonInflationYear {
  year: number;
  coefficient: Decimal;
  source: string;
  is_forecast: boolean;
  updated_at: string | null;
}

/**
 * Метаданные сосчитанного приведения. Приходят ТОЛЬКО когда приведение
 * посчитано: `"inflation": null` в номинальном ответе был бы нарушением
 * инварианта «без приведения ответ не меняется ни одним ключом» (DoD 1).
 *
 * `series_note` и `series_updated_at` обязаны приходить ЗДЕСЬ, а не вторым
 * запросом к списку рядов: по прямой ссылке ряд может оказаться архивным, а в
 * списке для выбора архивных нет — второй запрос их бы не нашёл, и полоса
 * уровней осталась бы без примечания и без даты правки (§2.12).
 */
export interface ComparisonInflation {
  series_id: number;
  series_name: string;
  series_note: string | null;
  series_updated_at: string | null;
  /** `YYYY-MM`. Разрешает СЕРВЕР в названной таймзоне; клиент пишет его в URL (§2.7). */
  target_month: string;
  has_forecast: boolean;
  /** Пустой массив — законное состояние: цель совпала с месяцем сметы (DoD 5). */
  used_years: ComparisonInflationYear[];
}

/** Множитель одной сметы договора — для чипа «разные» и его подсказки (решение плана №1). */
export interface ComparisonInflationFactor {
  /** «ДГП» либо «ДС №1» — тот же словарь, которым говорит `composition_caption`. */
  label: string;
  coefficient: Decimal;
}

/**
 * Контекст отказов приведения (спека §2.12). Ключи лежат РЯДОМ с `code` и
 * `message`, а не вложенным узлом, поэтому тип описывает именно их.
 */
export interface MissingInflationYearsContext {
  missing_years: number[];
}

export interface AmendmentDateMissingContext {
  /** МАШИННЫЙ контекст: человеку не показывается. Формулировку собрал сервер. */
  estimate_ids: number[];
}

export type ComparisonVatMode = "own" | "single" | "net";

/** Три корзины спеки §2.2: базовый договор, допсоглашения, итог. */
export type ComparisonBucket = "base" | "amendments" | "total";

/**
 * Фасет классов ставки в выборке — спека ДИАГРАММЫ СТОИМОСТИ §2.7 (в проекте две
 * спеки сравнения с одинаковой нумерацией разделов, поэтому она названа по имени).
 *
 * Приходит ВСЕГДА: и без сужения, и при пустой выборке. Считается по выборке ДО
 * сужения КЛАССАМИ, но ПОСЛЕ остальных фильтров (`q`, `object_id`,
 * `contractor_id`) — DoD 9. Именно поэтому сужение обратимо: снятый чип есть чем
 * вернуть, тогда как после сужения в ответе остались бы только уцелевшие
 * договоры и экран забыл бы о существовании остальных классов.
 */
export interface ComparisonRateClassFacet {
  id: number;
  title: string;
  /**
   * Число договоров этого класса в выборке ДО сужения классами, но ПОСЛЕ
   * остальных фильтров. Не «до любого сужения»: `q`/`object_id`/`contractor_id`
   * фасет уже сузили.
   */
  count: number;
}

export type ComparisonRowKind = "category" | "own" | "unallocated";

/**
 * Состояние ячейки (спека §2.1.2): `absent` — статьи нет ни в одной смете
 * договора (прочерк); `zero` — статья есть и расценена в ноль (число «0»);
 * `value` — статья есть. `value` НЕ означает «число показано»: при непустых
 * `incomplete_reasons` та же ячейка несёт `shown: null` — состояние отвечает
 * за НЕТТО (`net`), а не за то, что видит человек в выбранном режиме показа
 * (`backend/crud/comparison.py::_build_bucket_cell` — `state=axis.state`
 * берётся до применения режима).
 */
export type ComparisonCellState = "absent" | "zero" | "value";

/**
 * Причины неполноты ячейки (спека §2.1.3, §2.3.2). Совмещаются — ячейка
 * может нести несколько сразу, сервер отдаёт список отсортированным
 * (`sorted()`, `_bucket_cell_dict`). `display_rate_undefined` возникает
 * только в режиме «своя ставка».
 */
export type ComparisonIncompleteReason =
  | "unpriced_rows"
  | "not_finite_rows"
  | "vat_base_unknown"
  | "display_rate_undefined";

/**
 * Ячейка ОДНОЙ корзины ОДНОГО договора над ОДНОЙ строкой (спека §2.2, §2.3).
 *
 * ДВЕ удельные величины: `net_per_sqm` — вход медианы, не зависит от режима
 * показа; `shown_per_sqm` — то, что видит человек, следует режиму. Путать их
 * нельзя (план, задача 4): показ обязан читать `shown_per_sqm`, медиана и
 * подсветка всегда считаются по `net_per_sqm` на сервере (спека §2.5 правило
 * 2) — на клиенте `net_per_sqm` не участвует ни в чём, кроме диагностики.
 */
export interface ComparisonBucketCell {
  net: Decimal | null;
  shown: Decimal | null;
  net_per_sqm: Decimal | null;
  shown_per_sqm: Decimal | null;
  state: ComparisonCellState;
  /** `null`, если строка не входит в медиану ЭТОЙ корзины (§2.5 правила 3-5). */
  deviation_pct: Decimal | null;
  incomplete_reasons: ComparisonIncompleteReason[];
  /**
   * Денежное подмножество ячейки при сосчитанном приведении — спека диаграммы
   * стоимости §2.10 и §2.8:
   * РОВНО четыре денежные величины БЕЗ `state` и `deviation_pct` (они у
   * номинала не нужны — экран берёт их у приведённой ячейки того же бакета).
   * Приходит ТОЛЬКО при приведении: в номинальном ответе ключа нет ВОВСЕ, а не
   * `null` (DoD 13). Тип общий со строками дерева, но сервер посылает `nominal`
   * только у «Итого» — строки его не получают (DoD 17).
   */
  nominal?: {
    net: Decimal | null;
    shown: Decimal | null;
    net_per_sqm: Decimal | null;
    shown_per_sqm: Decimal | null;
  };
}

/** Ячейка договора над строкой — ВСЕ ТРИ корзины сразу (спека §2.2, §2.7). */
export interface ComparisonCell {
  contract_id: number;
  base: ComparisonBucketCell;
  amendments: ComparisonBucketCell;
  total: ComparisonBucketCell;
}

/**
 * Медиана строки/корзины (спека §2.5). `value: null` — сопоставимых меньше
 * трёх (правило 5, DoD 11); `comparable_count` и `contract_ids` приходят
 * ВСЕГДА, даже тогда — экрану нужно число, чтобы сказать «сопоставимых
 * меньше трёх», а не просто молчать.
 *
 * `shown_per_sqm` — ставка показа медианы (спека диаграммы стоимости §2.10,
 * только у «Итого»):
 * присутствие поля говорит, допускает ли режим НДС единую ось (`net`/`single`,
 * но НЕ `own`). Значение `null` — медианы нет (сопоставимых меньше трёх).
 * Поле НЕ приходит у строк дерева (`rows[].medians`), только у «Итого»
 * (`totals_medians`).
 */
export interface ComparisonMedian {
  value: Decimal | null;
  comparable_count: number;
  contract_ids: number[];
  shown_per_sqm?: Decimal | null;
  /**
   * Медиана НОМИНАЛА — приходит только при сосчитанном приведении (спека
   * диаграммы стоимости §2.10),
   * и только у «Итого». Диаграмма рисует её второй, точечной линией с подписью
   * «номинал» (DoD 23): приведение двигает и суммы, и медиану, поэтому без второй
   * линии движение столбцов читалось бы как движение отклонений.
   *
   * Форма — ПОДМНОЖЕСТВО самой медианы, а не второй `ComparisonMedian`:
   * `comparable_count` и `contract_ids` остаются общими на обе величины, потому
   * что множество сопоставимых договоров приведение не меняет (коэффициент
   * строго положителен, а умножение на положительное не делает ненулевое нулевым).
   * Второй счётчик был бы вторым источником истины об одном множестве.
   *
   * `shown_per_sqm` внутри подчиняется ТОМУ ЖЕ правилу присутствия, что снаружи:
   * есть при `net`/`single`, отсутствует при `own`.
   */
  nominal?: {
    value: Decimal | null;
    shown_per_sqm?: Decimal | null;
  };
}

/** Шапка колонки — договор выборки, отсортированные `signed_date DESC, id DESC` (спека §2.1). */
export interface ComparisonColumn {
  contract_id: number;
  contract_number: string;
  object_title: string;
  contractor_title: string;
  rate_class_id: number;
  rate_class_title: string;
  signed_date: string;
  /** `null` — ТЭП объекта не заведены; ₽/м² договора — прочерк (спека §2.4). */
  area_total_sp: Decimal | null;
  advance_pct: Decimal | null;
  bank_guarantee_pct: Decimal | null;
  retention_pct: Decimal | null;
  /**
   * Подпись состава колонки — например «20 %», «ДГП 20 % · ДС 20 %» либо
   * «ДГП 20 % · ДС №1 20 %, №2 22 %».
   *
   * Сервер отдаёт её ВСЕГДА, но печатается она ТОЛЬКО в режиме «своя ставка» —
   * и на экране, и на листе. Причина в §2.3.2: состав по договору нужен там, где
   * показ смешивает разные ставки; в режимах «единая» и «нетто» у выборки одна
   * общая ось, и состав объявляет `caption` страницы (AGENTS.md §10 v6.8 требует
   * объявить состав на поверхности, а не печатать его в каждом режиме).
   *
   * Прежняя редакция этой докстроки утверждала «печатается ВСЕГДА, а не только в
   * режиме „своя ставка“» — прямо противоположное поведению обеих поверхностей и
   * тесту, который утверждает её ОТСУТСТВИЕ в режиме «нетто». Ложное обоснование
   * в этом проекте считается самостоятельным дефектом: оно расходится
   * копированием и дороже отсутствующего.
   */
  composition_caption: string;
  /**
   * Множитель приведения колонки — УРОВНЕМ его показывает чип, множитель уезжает
   * в подсказку (§2.12, DoD 33).
   *
   * Приходит только при сосчитанном приведении. `null` означает «сметы договора
   * приведены РАЗНЫМИ множителями» — тогда рядом приходит `inflation_factors` с
   * разбивкой, и чип показывает «разные». Без разбивки арифметика колонки
   * перестала бы быть проверяемой: корзины множителей не показывают.
   */
  inflation_coefficient?: Decimal | null;
  /** Приходит ТОЛЬКО когда коэффициенты смет расходятся (решение плана №1). */
  inflation_factors?: ComparisonInflationFactor[];
}

/**
 * Строка сравнения — статья классификатора либо синтетическая строка
 * (спека §2.1, §2.1.1, §2.1.4). Список ПЛОСКИЙ, уже в порядке чтения дерева:
 * статья, все её потомки, затем её строка «Без подстатьи» (`kind: "own"`)
 * последней; «Нераспределённое» (`kind: "unallocated"`) — последняя строка
 * всей таблицы. `level`/`parent_code` восстанавливают иерархию на клиенте —
 * тем же способом, что `CategoryTable.tsx` паспорта восстанавливает дерево
 * из `parent_id`, только по коду вместо числового id (own-строка кода не
 * несёт своего числового узла классификатора).
 */
export interface ComparisonRow {
  kind: ComparisonRowKind;
  category_id: number | null;
  code: string;
  title: string;
  /** Глубина+1: корни статей — уровень 1 (та же единица, что у паспорта). */
  level: number;
  /** Код родителя; `null` у корня и у «Нераспределённого». */
  parent_code: string | null;
  /** В ТОМ ЖЕ порядке, что `columns`; `contract_id` на каждой ячейке — для сверки без опоры на порядок. */
  cells: ComparisonCell[];
  medians: Record<ComparisonBucket, ComparisonMedian>;
}

/**
 * Ответ `GET /v1/analytics/comparison` (спека 2026-08-17, план — задача 5).
 *
 * Один агрегат на экран и Excel-лист (спека §2.7) — тип общий для обоих
 * потребителей на бэкенде, но фронт вызывает только экранный путь.
 */
export interface Comparison {
  vat_mode: ComparisonVatMode;
  /**
   * Ставка, в которой ФАКТИЧЕСКИ показаны числа режима «единая» — предвыбор
   * сервера, если запрос его не задал (DoD 8ж). Читать нужно ЭТО поле, а не
   * то, что ушло в запросе: ссылка без ставки обязана открыться с числами.
   */
  single_rate: Decimal | null;
  rate_options: Decimal[];
  rate_preselected: Decimal | null;
  /**
   * Подпись налогового состава денег (AGENTS.md §10 v6.8) — печатается на
   * поверхности всегда, а не только в подсказке (тултип объясняет расчёт, а
   * не заменяет объявление состава).
   */
  caption: string;
  columns: ComparisonColumn[];
  available_rate_classes: ComparisonRateClassFacet[];
  rows: ComparisonRow[];
  totals: ComparisonCell[];
  totals_medians: Record<ComparisonBucket, ComparisonMedian>;
  /** Есть ТОЛЬКО при сосчитанном приведении (спека инфляции §2.12, DoD 1). */
  inflation?: ComparisonInflation;
}

/**
 * Параметры запроса (спека §2.3, §2.6). Выборка передаётся СТРОКАМИ как в
 * URL — `ids` через запятую либо `all` вместе с фильтрами списка договоров
 * (тот же контракт, что `routers.contracts.list_contracts`): страница
 * сравнения лишь ПЕРЕДАЁТ то, что уже собрал `ContractsPage` в адресе, не
 * разбирая числа туда и обратно. `page`/`page_size` намеренно отсутствуют —
 * сравнение берёт выборку целиком (§2.6).
 */
export interface ComparisonParams {
  ids?: string;
  all?: string;
  q?: string;
  object_id?: string;
  contractor_id?: string;
  /**
   * Сужение выборки по классам ставки: список id через запятую (`2,3`), а не
   * одиночное значение — ревизия §2.6 спеки диаграммы стоимости: класс перестал
   * быть ФОРМОЙ выборки
   * и стал её сужением, поэтому законно сочетается с `ids` и принимает список.
   */
  rate_class_id?: string;
  vat_mode?: ComparisonVatMode;
  /** Действует только в режиме `single`; без него сервер подставляет `rate_preselected`. */
  single_rate?: string;
  /**
   * Ряд индексов инфляции. Без него приведения нет вовсе; `target_month` без
   * него — `400` (умолчательного ряда не существует, §2.12).
   */
  inflation_series_id?: string;
  /**
   * Целевой ценовой уровень, `YYYY-MM`. Пустой при выбранном ряде читается как
   * «текущий месяц, разрешит сервер»: клиент `Date.now()` не использует — это
   * часы читателя, и два человека получили бы два ответа (§2.7).
   */
  target_month?: string;
}

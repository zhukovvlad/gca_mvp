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
 * Ячейка: средневзвешенная ставка работы по договору (§6).
 *
 * Пересчёт НДС (спека пересчёта §2.4-2.5): хотя бы одна неизвестная база НДС
 * среди предложений, сложившихся в ячейку, гасит `rate`, `amount` и
 * `deviation_pct` ЦЕЛИКОМ — показать средневзвешенное по части строк значило
 * бы выдать неполную величину за полную. `standard_unit_rate` при этом НЕ
 * гаснет: норматив от НДС не зависит и есть нетто по определению (спека §2.5)
 * — на экране законно возможна строка, где ставка пуста, а норматив показан.
 */
export interface MatrixCell {
  contract_id: number;
  rate: Decimal | null;
  /** Нетто-вес ЭТОЙ ячейки (спека §2.6); `null` вместе с `rate` при неизвестной базе. */
  amount: Decimal | null;
  standard_unit_rate: Decimal | null;
  deviation_pct: Decimal | null;
  /**
   * Почему отклонения нет: разные факты нельзя сводить к одному прочерку.
   * `no_weight` — райдер задачи 10 (`_fold_cell`, `backend/crud/analytics.py:743`):
   * защитная ветка, недостижимая сегодня (CTE фильтрует `weight > 0`), но код
   * причины должен быть заведён в типе заранее, а не молча дать `undefined`.
   * `not_finite` — дефект 1, круг 3 (ре-ревью Codex, PR #21): `_fold_cell`
   * теперь тоже гасит `rate`/`amount` ЦЕЛИКОМ и называет причину честно,
   * когда средневзвешенная ставка ячейки (или сумма, из которой она
   * получена) оказывается `NaN`/`Infinity` — до этой правки утечка была бы
   * видна в самой ячейке буквальным `"NaN"`.
   */
  deviation_reason: "no_standard" | "unknown_vat_base" | "no_weight" | "not_finite" | null;
}

export interface MatrixRow {
  catalog_position_id: number;
  job_title: string;
  unit_code: string | null;
  /** Вес строки в деньгах — по нему строки упорядочены (§6.4 отчёта фазы 6). */
  row_amount: Decimal | null;
  /**
   * Вес строки посчитан НЕ по всем её ячейкам: хотя бы в одном договоре база
   * НДС неизвестна, и такая ячейка не показывается и в вес не входит.
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
 * Drill-down по ячейке: позиции, сложившиеся в средневзвешенную ставку (§6).
 *
 * Пересчёт НДС (спека §2.4-2.5): `unit_cost_total` — валовое ИЗ ФАЙЛА, без
 * изменений; `unit_cost_net` — выведенное нетто той же строки, `null`, когда
 * база строки неизвестна; `vat_rate_base` — база МЕЖДУ ними, тоже `null` в
 * этом случае. Три подписи рядом — обещание §2.5 «валовое, нетто и база
 * рядом» выполняется на экране, а не только в JSON.
 */
export interface MatrixCellItem {
  position_item_id: number;
  job_title: string;
  unit_code: string | null;
  weight: Decimal | null;
  unit_cost_total: Decimal;
  /** Ставка без НДС — та, что вошла в ячейку; `null`, если база неизвестна. */
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
   * analytics.py::_net_deviation`).
   */
  deviation_reason: "no_standard" | "unknown_vat_base" | "not_finite" | null;
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

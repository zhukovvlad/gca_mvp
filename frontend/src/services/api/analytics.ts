/**
 * Транспорт аналитики фазы 6 (AGENTS.md §6, §7.4–§7.6).
 *
 * `baseURL` клиента — `"/api"`, поэтому пути начинаются с `/v1` и `/api` НЕ
 * дублируется (те же грабли, что у `domain.ts`).
 *
 * Все денежные поля приходят **строками** (§3) и типизированы как `Decimal`
 * (= `string`). Приводить их к `number` нельзя нигде — форматирование через
 * `MoneyCell`/`formatDecimalMoney`, отклонения через `DeviationCell`.
 */
import api from "@/lib/api";
import type {
  AppSettings,
  BankComparisonParams,
  CategoryOverrideChangeSummary,
  ClearCategoryOverrideInput,
  Comparison,
  ComparisonParams,
  Dashboard,
  DashboardAttention,
  Matrix,
  MatrixCellDetail,
  MatrixParams,
  ProjectPassport,
  SetCategoryOverrideInput,
} from "@/types/domain";

export const settingsApi = {
  get: (): Promise<AppSettings> => api.get<AppSettings>("/v1/settings").then((r) => r.data),

  update: (passport_top_n: number): Promise<AppSettings> =>
    api.patch<AppSettings>("/v1/settings", { passport_top_n }).then((r) => r.data),
};

/**
 * Сравнение договоров (спека 2026-08-17 §2.1–§2.6).
 *
 * Стоит рядом с матрицей и паспортом, а не в `domain.ts`: транспортный файл
 * соответствует пространству имён API, а эндпоинт живёт под `/v1/analytics/`.
 * `domain.ts` держит договоры, сметы, каталог и нормативы — вызов аналитики
 * там ломал бы это соответствие.
 */
export const comparisonApi = {
  get: (params: ComparisonParams): Promise<Comparison> =>
    api.get<Comparison>("/v1/analytics/comparison", { params }).then((r) => r.data),
};

export const analyticsApi = {
  /** Основной таб стартового дашборда (спека 2026-08-16 §2.8) — читает и `member`. */
  dashboard: (): Promise<Dashboard> =>
    api.get<Dashboard>("/v1/analytics/dashboard").then((r) => r.data),

  /**
   * Таб «На что обратить внимание» — только `admin` (403 у `member`).
   *
   * Эндпоинт ОТДЕЛЬНЫЙ, и вызывать его нужно только когда вкладка положена:
   * безусловный хук штатно генерирует запрещённые запросы и засоряет журнал
   * сервера отказами, тогда как §2.8 разводит эндпоинты ровно затем, чтобы
   * клиент и не пытался.
   */
  dashboardAttention: (): Promise<DashboardAttention> =>
    api.get<DashboardAttention>("/v1/analytics/dashboard/attention").then((r) => r.data),

  matrix: (params?: MatrixParams): Promise<Matrix> =>
    api.get<Matrix>("/v1/analytics/matrix", { params }).then((r) => r.data),

  matrixCell: (contract_id: number, catalog_position_id: number): Promise<MatrixCellDetail> =>
    api
      .get<MatrixCellDetail>("/v1/analytics/matrix/cell", {
        params: { contract_id, catalog_position_id },
      })
      .then((r) => r.data),

  /** Паспорт проекта по статьям классификатора (Ф6 фазы 7, спека §2.6). */
  projectPassport: (contractId: number): Promise<ProjectPassport> =>
    api
      .get<ProjectPassport>(`/v1/analytics/project-passport/${contractId}`)
      .then((r) => r.data),

  /**
   * Назначить статью разделу вручную (спека разноса).
   *
   * Ответ — сводка изменений, а не паспорт: `contractId` входа сюда не идёт,
   * он нужен только вызывающей стороне (инвалидация запроса паспорта).
   */
  setCategoryOverride: ({
    estimateId,
    positionItemId,
    workCategoryId,
    note,
  }: SetCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
    api
      .put<CategoryOverrideChangeSummary>(
        `/v1/estimates/${estimateId}/category-overrides/${positionItemId}`,
        { work_category_id: workCategoryId, note }
      )
      .then((r) => r.data),

  /** Снять ручное решение — раздел возвращается к статье из файла (или к «Нераспределённому»), допработы следуют производно. */
  clearCategoryOverride: ({
    estimateId,
    positionItemId,
  }: ClearCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
    api
      .delete<CategoryOverrideChangeSummary>(
        `/v1/estimates/${estimateId}/category-overrides/${positionItemId}`
      )
      .then((r) => r.data),
};

/**
 * Выгрузки §7.6. Ответ — `blob`: это файл, а не JSON.
 *
 * Имя файла сервер присылает в `Content-Disposition` (`filename*=UTF-8''…`), но
 * прочитать его из ответа `axios` можно только если сервер разрешил заголовок
 * браузеру. Проще и надёжнее собрать имя на клиенте — оно и так известно из
 * параметров, а расхождение с серверным именем ни на что не влияет.
 */
export const reportsApi = {
  contractSummary: (contractId: number): Promise<Blob> =>
    api
      .get<Blob>("/v1/reports/contract-summary", {
        params: { contract_id: contractId },
        responseType: "blob",
      })
      .then((r) => r.data),

  /**
   * Выгрузка сравнения договоров — ТРЕТИЙ файл §7.6 (`AGENTS.md` v6.8).
   *
   * Параметры — те же, что у экрана (§2.6, §2.3): выборка (`ids` либо `all=1`
   * с фильтрами) и режим показа НДС. Иначе лист отвечал бы на другой вопрос,
   * чем открытая страница, а спека §2.7 требует один агрегат на оба
   * представления.
   */
  comparison: (params: ComparisonParams): Promise<Blob> =>
    api
      .get<Blob>("/v1/reports/comparison", { params, responseType: "blob" })
      .then((r) => r.data),

  bankComparison: (params: BankComparisonParams): Promise<Blob> =>
    api
      .get<Blob>("/v1/reports/bank-comparison", { params, responseType: "blob" })
      .then((r) => r.data),
};

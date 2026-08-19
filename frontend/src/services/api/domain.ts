/**
 * Транспорт для эндпоинтов фазы 5 (AGENTS.md §7).
 *
 * `baseURL` клиента — `"/api"`, а эндпоинты фазы 4–5 живут на `/api/v1/...`,
 * поэтому пути здесь начинаются с `/v1` и `/api` НЕ дублируется (грабли §7
 * брифинга).
 */
import api from "@/lib/api";
import type { ID } from "@/types/common";
import type {
  InflationSeries,
  InflationSeriesInput,
  InflationSeriesPatch,
  InflationSeriesValue,
  BatchKindResult,
  CatalogPositionRow,
  ContractCard,
  ContractImportJob,
  ContractInput,
  ContractRow,
  Contractor,
  ContractorInput,
  Decimal,
  EstimateVatState,
  ImportJob,
  ManualKind,
  MergeResult,
  ObjectItem,
  ObjectInput,
  Paginated,
  RateClass,
  RateClassInput,
  RateStandard,
  RateStandardInput,
  RateStandardParams,
  ReapproveInput,
  ReapproveResult,
  ReviewQueueItem,
  ReviewQueueParams,
  UploadEstimateInput,
  Unit,
} from "@/types/domain";

// ---------------------------------------------------------------------------
//  Справочники
// ---------------------------------------------------------------------------

export const referencesApi = {
  listRateClasses: (): Promise<RateClass[]> =>
    api.get<RateClass[]>("/v1/rate-classes").then((r) => r.data),

  createRateClass: (input: RateClassInput): Promise<RateClass> =>
    api.post<RateClass>("/v1/rate-classes", input).then((r) => r.data),

  updateRateClass: (id: number, input: Partial<RateClassInput>): Promise<RateClass> =>
    api.patch<RateClass>(`/v1/rate-classes/${id}`, input).then((r) => r.data),

  deleteRateClass: (id: number): Promise<void> =>
    api.delete(`/v1/rate-classes/${id}`).then(() => undefined),

  listObjects: (params?: { q?: string; page?: number; page_size?: number }): Promise<Paginated<ObjectItem>> =>
    api.get<Paginated<ObjectItem>>("/v1/objects", { params }).then((r) => r.data),

  getObject: (id: number): Promise<ObjectItem> =>
    api.get<ObjectItem>(`/v1/objects/${id}`).then((r) => r.data),

  createObject: (input: ObjectInput): Promise<ObjectItem> =>
    api.post<ObjectItem>("/v1/objects", input).then((r) => r.data),

  updateObject: (id: number, input: Partial<ObjectInput>): Promise<ObjectItem> =>
    api.patch<ObjectItem>(`/v1/objects/${id}`, input).then((r) => r.data),

  listContractors: (params?: { q?: string; page?: number; page_size?: number }): Promise<Paginated<Contractor>> =>
    api.get<Paginated<Contractor>>("/v1/contractors", { params }).then((r) => r.data),

  createContractor: (input: ContractorInput): Promise<Contractor> =>
    api.post<Contractor>("/v1/contractors", input).then((r) => r.data),

  updateContractor: (id: number, input: Partial<ContractorInput>): Promise<Contractor> =>
    api.patch<Contractor>(`/v1/contractors/${id}`, input).then((r) => r.data),

  listUnits: (): Promise<Unit[]> => api.get<Unit[]>("/units").then((r) => r.data),
};

// ---------------------------------------------------------------------------
//  Договоры
// ---------------------------------------------------------------------------

export interface ContractListParams {
  q?: string;
  object_id?: number;
  contractor_id?: number;
  rate_class_id?: number;
  page?: number;
  page_size?: number;
}

export const contractsApi = {
  list: (params?: ContractListParams): Promise<Paginated<ContractRow>> =>
    api.get<Paginated<ContractRow>>("/v1/contracts", { params }).then((r) => r.data),

  get: (id: number): Promise<ContractCard> =>
    api.get<ContractCard>(`/v1/contracts/${id}`).then((r) => r.data),

  importJobs: (id: number): Promise<ContractImportJob[]> =>
    api.get<ContractImportJob[]>(`/v1/contracts/${id}/import-jobs`).then((r) => r.data),

  create: (input: ContractInput): Promise<ContractCard> =>
    api.post<ContractCard>("/v1/contracts", input).then((r) => r.data),

  update: (id: number, input: Partial<ContractInput>): Promise<ContractCard> =>
    api.patch<ContractCard>(`/v1/contracts/${id}`, input).then((r) => r.data),

  remove: (id: number): Promise<void> =>
    api.delete(`/v1/contracts/${id}`).then(() => undefined),
};

// ---------------------------------------------------------------------------
//  Загрузка смет и задания импорта (контракты фазы 4)
// ---------------------------------------------------------------------------

export const estimatesApi = {
  /**
   * Загрузка сметы. Отвечает `202` с заданием в статусе `pending` — смета
   * появится позже, и экран обязан это показывать, а не считать загрузку
   * завершённой (грабли §7 брифинга).
   *
   * `409` — не ошибка, а развилка: смета уже есть, нужен `replace=true`
   * (право `admin`).
   */
  upload: ({ file, contract_id, amendment_no, replace }: UploadEstimateInput): Promise<ImportJob> => {
    const form = new FormData();
    form.append("file", file);
    form.append("contract_id", String(contract_id));
    if (amendment_no !== null && amendment_no !== undefined) {
      form.append("amendment_no", String(amendment_no));
    }
    if (replace) form.append("replace", "true");
    return api.post<ImportJob>("/v1/estimates/upload", form).then((r) => r.data);
  },

  getJob: (jobId: number): Promise<ImportJob> =>
    api.get<ImportJob>(`/v1/import-jobs/${jobId}`).then((r) => r.data),

  /**
   * Скачивание исходника — **через API-клиент, а не ссылкой** `<a href>`.
   *
   * Экран обязан различать 404 («задания нет») и 410 («запись аудита есть, файл
   * удалён ретенцией §8») — это требование §5. Обычная ссылка отдала бы разбор
   * статуса браузеру, и человек увидел бы сырой JSON вместо объяснения, какой из
   * двух случаев произошёл.
   */
  downloadFile: (jobId: number): Promise<Blob> =>
    api
      .get<Blob>(`/v1/import-jobs/${jobId}/file`, { responseType: "blob" })
      .then((r) => r.data),

  /**
   * Правка ставок НДС сметы (спека пересчёта §2.7).
   *
   * Ответ идёт через `decimal_json` на бэкенде (`routers/estimate_vat.py`) —
   * ставки и время приезжают строками, а не `float`/`number`: `Decimal` во
   * фронте — это строка (`types/domain.ts:13`), и приводить их к `number`
   * здесь нельзя ни на входе, ни на выходе (§3).
   */
  setVat: (
    estimateId: ID,
    input: { base_override?: Decimal | null; target?: Decimal | null }
  ): Promise<EstimateVatState> =>
    api.patch<EstimateVatState>(`/v1/estimates/${estimateId}/vat`, input).then((r) => r.data),
};

// ---------------------------------------------------------------------------
//  Каталог и ручной матчинг
// ---------------------------------------------------------------------------

export const catalogApi = {
  search: (q: string, unit_id?: number): Promise<CatalogPositionRow[]> =>
    api
      .get<CatalogPositionRow[]>("/v1/catalog-positions", { params: { q, unit_id } })
      .then((r) => r.data),
};

export const reviewApi = {
  queue: (params?: ReviewQueueParams): Promise<Paginated<ReviewQueueItem>> =>
    api.get<Paginated<ReviewQueueItem>>("/v1/review/queue", { params }).then((r) => r.data),

  targets: (q: string, unit_id?: number): Promise<CatalogPositionRow[]> =>
    api
      .get<CatalogPositionRow[]>("/v1/review/targets", { params: { q, unit_id } })
      .then((r) => r.data),

  merge: (toReviewId: number, targetId: number): Promise<MergeResult> =>
    api
      .post<MergeResult>(`/v1/review/${toReviewId}/merge`, { target_id: targetId })
      .then((r) => r.data),

  setKind: (toReviewId: number, kind: ManualKind): Promise<CatalogPositionRow> =>
    api.post<CatalogPositionRow>(`/v1/review/${toReviewId}/kind`, { kind }).then((r) => r.data),

  batchKind: (ids: number[], kind: ManualKind): Promise<BatchKindResult> =>
    api.post<BatchKindResult>("/v1/review/batch-kind", { ids, kind }).then((r) => r.data),
};

// ---------------------------------------------------------------------------
//  Нормативы
// ---------------------------------------------------------------------------

/**
 * Ряды индексов инфляции (спека 2026-08-18 §2.12). `DELETE` нет нигде: ошибочное
 * значение исправляется правкой, ненужный ряд архивируется через `is_active`.
 */
export const inflationSeriesApi = {
  list: (includeArchived = false): Promise<InflationSeries[]> =>
    api
      .get<InflationSeries[]>("/v1/inflation-series", {
        params: includeArchived ? { include_archived: 1 } : undefined,
      })
      .then((r) => r.data),

  values: (id: ID): Promise<InflationSeriesValue[]> =>
    api.get<InflationSeriesValue[]>(`/v1/inflation-series/${id}/values`).then((r) => r.data),

  create: (input: InflationSeriesInput): Promise<InflationSeries> =>
    api.post<InflationSeries>("/v1/inflation-series", input).then((r) => r.data),

  update: (id: ID, input: InflationSeriesPatch): Promise<InflationSeries> =>
    api.patch<InflationSeries>(`/v1/inflation-series/${id}`, input).then((r) => r.data),
};

export const rateStandardsApi = {
  list: (params?: RateStandardParams): Promise<Paginated<RateStandard>> =>
    api.get<Paginated<RateStandard>>("/v1/rate-standards", { params }).then((r) => r.data),

  create: (input: RateStandardInput): Promise<RateStandard> =>
    api.post<RateStandard>("/v1/rate-standards", input).then((r) => r.data),

  update: (id: number, input: Partial<RateStandardInput>): Promise<RateStandard> =>
    api.patch<RateStandard>(`/v1/rate-standards/${id}`, input).then((r) => r.data),

  reapprove: (id: number, input: ReapproveInput): Promise<ReapproveResult> =>
    api.post<ReapproveResult>(`/v1/rate-standards/${id}/reapprove`, input).then((r) => r.data),

  remove: (id: number): Promise<void> =>
    api.delete(`/v1/rate-standards/${id}`).then(() => undefined),
};

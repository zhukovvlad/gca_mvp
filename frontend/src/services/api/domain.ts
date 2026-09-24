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
  AcceptTargetDecisionInput,
  AcceptTargetDecisionResult,
  AcceptTransferInput,
  AcceptTransferResult,
  ArchiveContextInput,
  AssignFamilyInput,
  ConfirmKindInput,
  ContextCardData,
  ContextsPage,
  ContextsParams,
  InflationSeries,
  InflationSeriesInput,
  InflationSeriesPatch,
  InflationSeriesValue,
  BatchKindResult,
  CatalogPositionRow,
  CategoryOverrideChangeSummary,
  ClearRoundCategoryOverrideInput,
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
  MergeContextsResult,
  MergeFamiliesResult,
  MergeResult,
  MoveMembersInput,
  MoveMembersResult,
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
  RoundImportJob,
  RoundInput,
  RoundUnallocated,
  SetNameRoleInput,
  SetRoundCategoryOverrideInput,
  SplitContextInput,
  SplitContextResult,
  StagePositions,
  StageSummary,
  TenderCard,
  TenderInput,
  TenderRow,
  TransferProposalResponse,
  UploadEstimateInput,
  UploadRoundInput,
  Unit,
  WorkFamily,
  WorkFamilyInput,
  WorkFamilyPatch,
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

// ---------------------------------------------------------------------------
//  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13, §2.14)
// ---------------------------------------------------------------------------

export const tendersApi = {
  list: (params?: { q?: string; page?: number; page_size?: number }): Promise<Paginated<TenderRow>> =>
    api.get<Paginated<TenderRow>>("/v1/tenders", { params }).then((r) => r.data),
  get: (id: number): Promise<TenderCard> =>
    api.get<TenderCard>(`/v1/tenders/${id}`).then((r) => r.data),
  create: (input: TenderInput): Promise<TenderCard> =>
    api.post<TenderCard>("/v1/tenders", input).then((r) => r.data),
  update: (id: number, input: Partial<Pick<TenderInput, "title" | "notes">>): Promise<TenderCard> =>
    api.patch<TenderCard>(`/v1/tenders/${id}`, input).then((r) => r.data),
  remove: (id: number): Promise<void> => api.delete(`/v1/tenders/${id}`).then(() => undefined),
  createRound: (tenderId: number, input: RoundInput): Promise<TenderCard> =>
    api.post<TenderCard>(`/v1/tenders/${tenderId}/rounds`, input).then((r) => r.data),
  updateRound: (tenderId: number, roundId: number, input: Partial<Omit<RoundInput, "stage_no">>): Promise<TenderCard> =>
    api.patch<TenderCard>(`/v1/tenders/${tenderId}/rounds/${roundId}`, input).then((r) => r.data),
  removeRound: (tenderId: number, roundId: number): Promise<void> =>
    api.delete(`/v1/tenders/${tenderId}/rounds/${roundId}`).then(() => undefined),
  roundImportJobs: (tenderId: number, roundId: number): Promise<RoundImportJob[]> =>
    api.get<RoundImportJob[]>(`/v1/tenders/${tenderId}/rounds/${roundId}/import-jobs`).then((r) => r.data),
  uploadRound: ({ file, tender_id, round_id, replace }: UploadRoundInput): Promise<ImportJob> => {
    const form = new FormData();
    form.append("file", file);
    if (replace) form.append("replace", "true");
    return api.post<ImportJob>(`/v1/tenders/${tender_id}/rounds/${round_id}/upload`, form).then((r) => r.data);
  },
  /** Без token — сервер отвечает 409 `confirmation_required` с preview; с token — 204. */
  removeParticipant: (tenderId: number, packageId: number, confirmationToken?: string): Promise<void> =>
    api.delete(`/v1/tenders/${tenderId}/participants/${packageId}`, {
      params: confirmationToken ? { confirmation_token: confirmationToken } : undefined,
    }).then(() => undefined),
  /** Свод по этапам одного участника (спека §2.16). */
  stageSummary: (tenderId: number, offerIds: number[]): Promise<StageSummary> =>
    api
      .get<StageSummary>(`/v1/tenders/${tenderId}/stage-summary`, {
        params: { offers: offerIds },
        paramsSerializer: { indexes: null },
      })
      .then((r) => r.data),
  /** Попозиционное раскрытие статьи свода (спека 2026-08-30-position-drilldown-design.md §2.11). */
  stagePositions: (tenderId: number, workCategoryId: number, offerIds: number[]): Promise<StagePositions> =>
    api
      .get<StagePositions>(`/v1/tenders/${tenderId}/stage-summary/${workCategoryId}`, {
        params: { offers: offerIds },
        paramsSerializer: { indexes: null },
      })
      .then((r) => r.data),
  roundUnallocated: (tenderId: number, roundId: number): Promise<RoundUnallocated> =>
    api.get<RoundUnallocated>(`/v1/tenders/${tenderId}/rounds/${roundId}/unallocated`).then((r) => r.data),
  setRoundCategoryOverride: ({ tenderId, roundId, lotKey, positionKey, workCategoryId, note }: SetRoundCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
    api.put<CategoryOverrideChangeSummary>(`/v1/tenders/${tenderId}/rounds/${roundId}/category-overrides`,
      { lot_key: lotKey, position_key_in_proposal: positionKey, work_category_id: workCategoryId, note }).then((r) => r.data),
  clearRoundCategoryOverride: ({ tenderId, roundId, lotKey, positionKey }: ClearRoundCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
    api.delete<CategoryOverrideChangeSummary>(`/v1/tenders/${tenderId}/rounds/${roundId}/category-overrides`,
      { data: { lot_key: lotKey, position_key_in_proposal: positionKey } }).then((r) => r.data),
  /**
   * Книга «Изменения КП» — лист на каждого участника с двумя и более сметами
   * (спека 2026-09-16-tender-changes-export-design.md §2.1, §2.11). Собирается
   * по ВСЕМ этапам всех сравнимых участников — выбор на решётке карточки
   * здесь не участвует, поэтому вход один: `tenderId`.
   */
  changesExport: (tenderId: number): Promise<Blob> =>
    api
      .get<Blob>(`/v1/tenders/${tenderId}/changes-export`, { responseType: "blob" })
      .then((r) => r.data),
};

// ---------------------------------------------------------------------------
//  Семьи и контексты (спека 2026-09-22-catalog-families-design.md §2.10,
//  `backend/routers/semantic.py`) — право `admin` на каждом маршруте.
// ---------------------------------------------------------------------------

export const semanticApi = {
  listFamilies: (params?: { status?: WorkFamily["status"]; unit_id?: number }): Promise<WorkFamily[]> =>
    api
      .get<{ items: WorkFamily[] }>("/v1/semantic/families", { params })
      .then((r) => r.data.items),

  createFamily: (input: WorkFamilyInput): Promise<WorkFamily> =>
    api.post<WorkFamily>("/v1/semantic/families", input).then((r) => r.data),

  updateFamily: (id: number, input: WorkFamilyPatch): Promise<WorkFamily> =>
    api.patch<WorkFamily>(`/v1/semantic/families/${id}`, input).then((r) => r.data),

  activateFamily: (id: number): Promise<WorkFamily> =>
    api.post<WorkFamily>(`/v1/semantic/families/${id}/activate`).then((r) => r.data),

  archiveFamily: (id: number): Promise<WorkFamily> =>
    api.post<WorkFamily>(`/v1/semantic/families/${id}/archive`).then((r) => r.data),

  mergeFamilies: (id: number, targetFamilyId: number): Promise<MergeFamiliesResult> =>
    api
      .post<MergeFamiliesResult>(`/v1/semantic/families/${id}/merge`, { target_family_id: targetFamilyId })
      .then((r) => r.data),

  listContexts: (params?: ContextsParams): Promise<ContextsPage> =>
    api.get<ContextsPage>("/v1/semantic/contexts", { params }).then((r) => r.data),

  contextCard: (id: number): Promise<ContextCardData> =>
    api.get<ContextCardData>(`/v1/semantic/contexts/${id}`).then((r) => r.data),

  confirmKind: (contextId: number, input: ConfirmKindInput): Promise<ContextCardData> =>
    api.post<ContextCardData>(`/v1/semantic/contexts/${contextId}/kind`, input).then((r) => r.data),

  setNameRole: (contextId: number, input: SetNameRoleInput): Promise<ContextCardData> =>
    api
      .post<ContextCardData>(`/v1/semantic/contexts/${contextId}/name-role`, input)
      .then((r) => r.data),

  assignFamily: (contextId: number, input: AssignFamilyInput): Promise<ContextCardData> =>
    api.post<ContextCardData>(`/v1/semantic/contexts/${contextId}/family`, input).then((r) => r.data),

  splitContext: (contextId: number, input: SplitContextInput): Promise<SplitContextResult> =>
    api.post<SplitContextResult>(`/v1/semantic/contexts/${contextId}/split`, input).then((r) => r.data),

  mergeContexts: (contextId: number, targetContextId: number): Promise<MergeContextsResult> =>
    api
      .post<MergeContextsResult>(`/v1/semantic/contexts/${contextId}/merge`, { target_context_id: targetContextId })
      .then((r) => r.data),

  archiveContext: (contextId: number, input: ArchiveContextInput): Promise<{ context_id: number; archived: boolean }> =>
    api
      .post<{ context_id: number; archived: boolean }>(`/v1/semantic/contexts/${contextId}/archive`, input)
      .then((r) => r.data),

  moveMembers: (input: MoveMembersInput): Promise<MoveMembersResult> =>
    api.post<MoveMembersResult>("/v1/semantic/members/move", input).then((r) => r.data),

  transferProposal: (positionItemId: number): Promise<TransferProposalResponse> =>
    api
      .get<TransferProposalResponse>(`/v1/semantic/members/${positionItemId}/transfer-proposal`)
      .then((r) => r.data),

  acceptTransfer: (positionItemId: number, input: AcceptTransferInput): Promise<AcceptTransferResult> =>
    api
      .post<AcceptTransferResult>(`/v1/semantic/members/${positionItemId}/transfer`, input)
      .then((r) => r.data),

  acceptTargetDecision: (input: AcceptTargetDecisionInput): Promise<AcceptTargetDecisionResult> =>
    api
      .post<AcceptTargetDecisionResult>("/v1/semantic/members/accept-target-decision", input)
      .then((r) => r.data),
};

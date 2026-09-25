import { http, HttpResponse } from "msw";

import { multiplyDecimalStrings } from "@/lib/decimal";

import {
  sampleAdminUsers,
  sampleComparison,
  sampleContractCard,
  sampleContractors,
  sampleContracts,
  sampleFailedJob,
  sampleImportJobs,
  sampleRunningJob,
  sampleObjects,
  sampleRateClasses,
  sampleInflationSeries,
  sampleInflationValues,
  sampleRateStandards,
  sampleReviewQueue,
  sampleAppSettings,
  sampleDashboard,
  sampleDashboardAttention,
  sampleDashboardAttentionClean,
  sampleMatrix,
  sampleMatrixCellDetail,
  sampleProjectPassport,
  sampleRoundUnallocated,
  sampleStageSummary,
  stagePositionsResponse,
  stageSummaryAllUnknown,
  stageSummaryNet,
  stageSummaryWithUnknownSecondColumn,
  sampleTenderCard,
  sampleTenders,
} from "./fixtures";
import type {
  Comparison,
  ComparisonBucketCell,
  ComparisonMedian,
  ContextCardData,
  ContextMemberRow,
  ContextRow,
  InflationSeries,
  ComparisonVatMode,
  EstimateRow,
  ImportJobStatus,
  ProjectPassport,
  RoundImportJob,
  SemanticEventEntry,
  TenderCard,
  WorkFamily,
} from "@/types/domain";

/**
 * Фикстура контекста — полная форма карточки (`ContextCardData`) плюс три
 * булевых признака ПРО ЧЛЕНСТВА, которых сама карточка не несёт
 * (`crud/semantic.py::context_card` отдаёт членства поштучно, но агрегатных
 * флагов устаревшего/конфликтного членства у контекста нет — они существуют
 * только как предикаты фильтра очереди, `crud/semantic.py::ContextFilters`,
 * и из `members` фикстуры не выводятся). Флаги здесь нужны,
 * чтобы очередь `/v1/semantic/contexts` могла отвечать на
 * `has_stale_members`/`has_conflicting_members`/`has_no_members` тем же
 * способом, что и бэкенд — тремя независимыми предикатами, а не одним
 * общим, — и вырезаются перед сериализацией в JSON (`toContextRow`/
 * `toContextCard`): наружу уходит ровно форма ответа бэкенда, не более.
 */
interface SemanticContextFixture extends ContextCardData {
  hasStaleMembers: boolean;
  hasConflictingMembers: boolean;
  hasNoMembers: boolean;
}

/**
 * Мутируемое состояние обработчиков. Сбрасывается между тестами через
 * `resetHandlerState()` (вызов — в `setup.ts`), иначе загрузка из одного теста
 * влияла бы на статус задания в другом.
 */
interface HandlerState {
  /** Последовательность статусов, которую отдаёт поллинг задания. */
  jobStatuses: ImportJobStatus[];
  /** Сколько раз опросили статус — по нему выбирается следующий статус. */
  jobPolls: number;
  /** Ответ следующей загрузки: 202 (обычно), 200 (идемпотентно) либо 409. */
  uploadOutcome: "created" | "idempotent" | "conflict";
  /** Была ли последняя загрузка с `replace=true`. */
  lastUploadReplace: boolean;
  /** Пакетные решения Review: что пришло последним. */
  lastBatch: { ids: number[]; kind: string } | null;
  /** Пропустить ли одну строку в пакете — проверка ветки `skipped`. */
  batchSkipsFirst: boolean;
  /** Исход скачивания исходника: файл на месте, задания нет (404), удалён (410). */
  fileOutcome: "ok" | "missing" | "purged";
  /**
   * Текущее значение `passport_top_n`. Мутируется PATCH-ем настроек. Паспорт
   * проекта (Ф6 фазы 7) от него не зависит — поле осталось ради самих
   * настроек: экран Settings и его тесты по-прежнему читают/пишут это число.
   */
  passportTopN: number;
  /**
   * Исход паспорта ПРОЕКТА (Ф6 фазы 7, задача 6) — граничные случаи спеки
   * §2.6, §2.9: не только «нет сметы», но и «нет ТЭП», «сумма неизвестна»,
   * «сумма ровно ноль», «данные повреждены».
   */
  projectPassportOutcome:
    | "full"
    | "no-estimate"
    | "no-tep"
    | "empty-total"
    | "zero-total"
    | "corrupted"
    | "error";
  /** Отдать пустую матрицу — и различить «нет договоров» от «нет работ». */
  matrixOutcome: "rows" | "no-rows" | "no-columns" | "pending-review";
  /**
   * Сколько расценённых позиций ждут ручного матчинга. Отдельно от `matrixOutcome`:
   * пустой результат ПОИСКА может сосуществовать с непустой очередью, и именно на
   * этом сочетании экран раньше называл неверную причину.
   */
  positionsPendingReview: number;
  /** Последний запрос выгрузки: по нему тест проверяет, что фильтры доехали. */
  lastReportRequest: { report: string; params: Record<string, string> } | null;
  /**
   * Переопределяет `estimates[]` ответа `GET /contracts/:id`, когда задано
   * (находка ревью PR #16 — счёт решений в диалоге замены обязан быть тем,
   * что сервер держит В МОМЕНТ конфликта, а не тем, что застряло в проп-кэше
   * карточки у вызывающего). `null` — отдавать фикстуру как есть.
   */
  contractCardEstimatesOverride: EstimateRow[] | null;
  /**
   * Заваливает `GET /contracts/:id` 500-й ошибкой, когда `true` (находка
   * ревью PR #16, finding 3): рефетч карточки на 409 обязан провалиться, а не
   * молча вернуть фикстуру, — так тест видит именно ветку `isError`, а не
   * успешный ответ.
   */
  contractCardFails: boolean;
  /**
   * Сколько раз запрашивали диагностики второго таба.
   *
   * Проверка ОТСУТСТВИЯ запроса требует сигнала, общего для обеих ветвей: у
   * `member` данных на экране нет ни при безусловном хуке (сервер ответит
   * `403`), ни при условном, — поэтому «ничего не видно» ничего не доказывает.
   * Доказывает счётчик вызовов и утверждение «ровно 0».
   */
  attentionRequests: number;
  /**
   * Исход приведения: числа, либо один из двух кодов отказа. Управляемый, потому
   * что при отказе экран делает ВТОРОЙ, номинальный запрос (§2.9), и различить их
   * на неуправляемом хендлере было бы нечем.
   */
  inflationOutcome: "adjusted" | "missing-years" | "amendment-date";
  /** Сколько раз запрашивалось приведение — по нему видно перезапрос после правки. */
  inflationRequests: number;
  /** Ряды индексов: состояние, потому что тесты проверяют переходы архивации. */
  inflationSeries: InflationSeries[];
  /** Последнее тело запроса рядов — по нему тест видит, что ушло ОДНИМ запросом. */
  lastInflationBody: unknown;
  inflationPatches: number;
  /** Исход диагностик: обычный набор либо «всё сходится» (макет, панель ok). */
  attentionOutcome: "issues" | "clean";
  /**
   * Граничные случаи карточки тендера (спека контура §2.13, §2.12): раунд с
   * baseline как есть в фикстуре, раунд без baseline, раунд без единого job
   * (файла нет), раунд с job, но составом, изменившимся после загрузки
   * (`current_job_id: null` при живой ячейке со сметой).
   */
  tenderRoundState:
    | "loaded"
    | "loaded-no-baseline"
    | "empty"
    | "changed"
    | "both-loaded"
    | "both-loaded-with-beta"
    | "second-round-no-estimate";
  /** Исход `DELETE .../participants/:pid` — протокол `confirmation_token` (§2.11). */
  participantDeleteOutcome: "preview" | "stale" | "deleted" | "active";
  /** Была ли последняя загрузка раунда с `replace=true`. */
  lastRoundUploadReplace: boolean;
  /**
   * Какой КОД несёт 409 загрузки раунда, когда `uploadOutcome === "conflict"`
   * (находка внешнего ревью PR #33, finding 2): `replace_required` — «раунд
   * уже загружен» (единственный код, на который панель открывает диалог
   * замены), `active_import` — «идёт чужой импорт» (тот же код, что у гонки
   * на индексе бэкенда) — эта причина ВСЕГДА побеждает `replace`, как и на
   * бэкенде: проверка активного импорта идёт раньше проверки «есть ли что
   * заменять».
   */
  roundUploadConflictCode: "replace_required" | "active_import";
  /**
   * Исход `DELETE /api/v1/tenders/:id` — зеркалит отказ, которым `DELETE
   * .../participants/:pid` уже отвечает на активный импорт (§2.11): пока
   * раунд грузится, тендер целиком удалить тоже нельзя.
   */
  tenderDeleteOutcome: "ok" | "active";
  /**
   * Исход `GET /api/v1/tenders/:id/stage-summary` (спека 2026-08-27-stage-summary-design.md
   * §2.16, Task 6): свод валовой с ставкой 20%, нетто с ставками [20, 0, 20],
   * со всеми unknown, со второй колонкой unknown, или один из кодов отказа.
   */
  stageSummaryOutcome:
    | "ok"
    | "net"
    | "unknown_vat"
    | "track_non_positive"
    | "track_no_comparable"
    | "tender_not_found"
    | "offer_not_found"
    | "too_few_offers"
    | "one_offer_per_round"
    | "single_participant"
    | "offer_has_no_estimate";
  /** Последние PUT и DELETE к category-overrides — для проверки тела. */
  roundOverrideRequests: Array<{ method: string; body: Record<string, unknown> }>;
  /**
   * Исход `GET /api/v1/tenders/:id/changes-export` (спека
   * 2026-09-16-tender-changes-export-design.md §2.1, §2.11, план фичи, Task
   * 5): `ok` — книга (непустой blob с настоящим media type, тем же приёмом,
   * что у трёх выгрузок §7.6 выше); `no_comparable` — структурированный `422`
   * СЕРВЕРНЫМ текстом, которым тест проверяет, что `toastReportError` достаёт
   * сообщение из блоб-ответа, а не подменяет его строкой axios.
   */
  changesExportOutcome: "ok" | "no_comparable";
  /** Последний id тендера, для которого запрашивалась книга «Изменения КП». */
  lastChangesExportTenderId: number | null;

  /**
   * Семьи работ (спека 2026-09-22-catalog-families-design.md §2.7, §2.10;
   * `backend/routers/semantic.py`). Мутируемый массив — CRUD-хендлеры пишут
   * в него напрямую, `resetHandlerState()` возвращает `initialWorkFamilies()`.
   */
  workFamilies: WorkFamily[];
  nextWorkFamilyId: number;
  lastCreateFamilyRequest: Record<string, unknown> | null;
  lastUpdateFamilyRequest: { id: number; body: Record<string, unknown> } | null;
  lastMergeFamiliesRequest: { id: number; targetFamilyId: number } | null;

  /** Контексты каталога — очередь и карточка (спека §2.10). */
  semanticContexts: SemanticContextFixture[];
  lastConfirmKindRequest: { contextId: number; body: Record<string, unknown> } | null;
  lastSetNameRoleRequest: { contextId: number; body: Record<string, unknown> } | null;
  lastAssignFamilyRequest: { contextId: number; body: Record<string, unknown> } | null;
  lastSplitContextRequest: { contextId: number; body: Record<string, unknown> } | null;
  lastMergeContextRequest: { contextId: number; targetContextId: number } | null;
  lastArchiveContextRequest: { contextId: number; body: Record<string, unknown> } | null;
  lastMoveMembersRequest: Record<string, unknown> | null;
  lastAcceptTransferRequest: { positionItemId: number; body: Record<string, unknown> } | null;
  lastAcceptTargetDecisionRequest: number[] | null;
}

// ---------------------------------------------------------------------------
//  Семьи и контексты — фикстуры (спека 2026-09-22-catalog-families-design.md
//  §2.7, §2.10).
// ---------------------------------------------------------------------------

/**
 * `position_item_id` мембершипов, на которых завязаны действия карточки
 * контекста (`ContextCard.tsx`): строки членств фикстуры несут эти id, а
 * обработчики `transfer-proposal`/`transfer`/`accept-target-decision` отвечают
 * по ним, поэтому тесты ссылаются на те же константы.
 */
export const STALE_POSITION_ITEM_ID = 9101;
export const CURRENT_POSITION_ITEM_ID = 9102;
export const CONFLICT_POSITION_ITEM_IDS = [9201, 9202];
/** Членство разом устаревшее И конфликтное — сочетание, достижимое через
 * override категории, задевший членство, уже отмеченное слиянием в Review. */
export const STALE_AND_CONFLICT_POSITION_ITEM_ID = 9203;

function isoNow(): string {
  return "2026-09-24T10:00:00Z";
}

/**
 * 42 черновика — штатное первое состояние после seed (план задачи 7 и
 * задачи 13, «Утверждения»). `id` 1 несёт определение и две привязки (правка
 * единицы и архивирование должны отказать и назвать число); `id` 2 —
 * черновик БЕЗ определения (кнопка активации недоступна). Семьи 43-44 не
 * входят в 42: они не `draft`, и их присутствие в фикстуре доказывает, что
 * счёт «42» на экране — результат ФИЛЬТРА по статусу, а не длины массива.
 */
function initialWorkFamilies(): WorkFamily[] {
  const drafts: WorkFamily[] = Array.from({ length: 42 }, (_, i) => {
    const id = i + 1;
    const linked = id === 1;
    const hasDefinition = id === 1;
    return {
      id,
      title: id === 2 ? "Устройство покрытий полов" : `Семья работ №${id}`,
      // id 5 — «Кв. метр» справочника `/api/units` (`src/test/handlers.ts`
      // ниже): фильтр по единице (P2) должен опираться на РЕАЛЬНЫЙ каталог
      // единиц, а не на произвольное число, которого там нет.
      unit_id: 5,
      unit_code: "M2",
      definition: hasDefinition
        ? "Оштукатуривание стен и потолков цементно-песчаным раствором."
        : null,
      status: "draft",
      seed_key: `seed-${id}`,
      created_by: null,
      created_at: isoNow(),
      updated_at: isoNow(),
      activated_by: null,
      activated_at: null,
      archived_at: null,
      context_count: linked ? 2 : 0,
    };
  });
  const active: WorkFamily = {
    id: 43,
    title: "Кровельные работы",
    // Другая единица (id 3, «Куб. метр») — фильтр по единице (P2) обязан
    // РАЗЛИЧАТЬ семьи, а не только принимать значение.
    unit_id: 3,
    unit_code: "M3",
    definition: "Устройство кровельного покрытия.",
    status: "active",
    seed_key: null,
    created_by: 1,
    created_at: isoNow(),
    updated_at: isoNow(),
    activated_by: 1,
    activated_at: isoNow(),
    archived_at: null,
    context_count: 0,
  };
  const archived: WorkFamily = {
    id: 44,
    title: "Демонтажные работы (снята)",
    unit_id: 5,
    unit_code: "M2",
    definition: "Демонтаж конструкций.",
    status: "archived",
    seed_key: null,
    created_by: 1,
    created_at: isoNow(),
    updated_at: isoNow(),
    activated_by: 1,
    activated_at: isoNow(),
    archived_at: isoNow(),
    context_count: 0,
  };
  return [...drafts, active, archived];
}

/**
 * Шесть контекстов, по одному на состояние, которое проверяют тесты экрана:
 * обычный, устаревшее членство, конфликт решений, `insufficient_description`
 * без семьи, пустой (без членств), архивный (доказывает отсутствие действия
 * восстановления).
 */
function initialSemanticContexts(): SemanticContextFixture[] {
  const base = {
    is_default: false,
    unit_id: 11,
    unit_code: "м2",
    work_category_id: 77,
    work_category_code: "05.02",
    work_category_title: "Отделочные работы",
    work_category_source: "file",
    place_dictionary_version: 1,
    events: [] as SemanticEventEntry[],
  };
  const event = (id: number, type: string): SemanticEventEntry => ({
    id,
    event_type: type,
    payload: {},
    actor_id: 1,
    created_at: isoNow(),
  });
  /**
   * Членство поштучно — по умолчанию `CURRENT`, без
   * конфликта; переопределения задают `STALE`/`conflict_at` там, где тест
   * этого требует. Каждая фикстура-контекст сама решает, какие членства ей
   * нести, — список НЕ вычисляется из `hasStaleMembers`/`hasConflictingMembers`
   * (те остаются флагами ТОЛЬКО для фильтра очереди, как и на бэкенде).
   */
  const member = (
    positionItemId: number,
    jobTitle: string,
    overrides: Partial<Omit<ContextMemberRow, "position_item_id" | "job_title">> = {}
  ): ContextMemberRow => ({
    position_item_id: positionItemId,
    job_title: jobTitle,
    estimate_id: 5001,
    membership_state: "CURRENT",
    conflict_at: null,
    conflict_from_context_id: null,
    routed_by: "default",
    ...overrides,
  });

  const ordinary: SemanticContextFixture = {
    ...base,
    id: 601,
    bucket_id: 701,
    archived_at: null,
    catalog_position_id: 8001,
    standard_job_title: "Штукатурка стен цементно-песчаным раствором",
    semantic_kind: "WORK",
    semantic_kind_source: "rule",
    semantic_kind_by: null,
    semantic_kind_at: null,
    name_role: "WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: null,
    semantic_state: "SUGGESTED",
    work_family_id: 1,
    family_title: "Семья работ №1",
    family_source: "manual",
    family_by: 1,
    family_at: isoNow(),
    member_count: 3,
    bucket_contexts: [
      { id: 601, is_default: true, archived_at: null, member_count: 3 },
      { id: 750, is_default: false, archived_at: null, member_count: 1 },
    ],
    members: [
      member(71001, "Штукатурка стен, ось А-Б"),
      member(71002, "Штукатурка стен, ось Б-В"),
      member(71003, "Штукатурка стен, ось В-Г"),
    ],
    members_truncated: false,
    events: [event(1, "context_created")],
    hasStaleMembers: false,
    hasConflictingMembers: false,
    hasNoMembers: false,
  };

  const stale: SemanticContextFixture = {
    ...base,
    id: 602,
    bucket_id: 702,
    archived_at: null,
    catalog_position_id: 8002,
    standard_job_title: "Устройство покрытий полов из линолеума",
    semantic_kind: "WORK",
    semantic_kind_source: "rule",
    semantic_kind_by: null,
    semantic_kind_at: null,
    name_role: "WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: null,
    semantic_state: "SUGGESTED",
    work_family_id: null,
    family_title: null,
    family_source: null,
    family_by: null,
    family_at: null,
    member_count: 2,
    bucket_contexts: [{ id: 602, is_default: true, archived_at: null, member_count: 2 }],
    members: [
      member(STALE_POSITION_ITEM_ID, "Устройство покрытий полов, ось 1", {
        membership_state: "STALE",
      }),
      member(CURRENT_POSITION_ITEM_ID, "Устройство покрытий полов, ось 2"),
    ],
    members_truncated: false,
    events: [event(2, "context_created"), event(3, "members_marked_stale")],
    hasStaleMembers: true,
    hasConflictingMembers: false,
    hasNoMembers: false,
  };

  const conflicted: SemanticContextFixture = {
    ...base,
    id: 603,
    bucket_id: 703,
    archived_at: null,
    catalog_position_id: 8003,
    standard_job_title: "Отделка потолков водоэмульсионным составом",
    semantic_kind: "WORK",
    semantic_kind_source: "manual",
    semantic_kind_by: 1,
    semantic_kind_at: isoNow(),
    name_role: "WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: null,
    semantic_state: "CONFIRMED",
    work_family_id: 1,
    family_title: "Семья работ №1",
    family_source: "manual",
    family_by: 1,
    family_at: isoNow(),
    member_count: 3,
    bucket_contexts: [
      { id: 603, is_default: true, archived_at: null, member_count: 3 },
      { id: 760, is_default: false, archived_at: null, member_count: 0 },
    ],
    members: [
      member(CONFLICT_POSITION_ITEM_IDS[0], "Отделка потолков, ось 1", {
        conflict_at: isoNow(),
        conflict_from_context_id: 601,
        routed_by: "manual",
      }),
      member(CONFLICT_POSITION_ITEM_IDS[1], "Отделка потолков, ось 2", {
        conflict_at: isoNow(),
        conflict_from_context_id: 601,
        routed_by: "manual",
      }),
      // Устаревшее И конфликтное разом (override категории задел членство,
      // уже отмеченное слиянием в Review) — экран обязан показать ДВА
      // конфликтных действия и НИ ОДНОГО действия переноса устаревшего.
      member(STALE_AND_CONFLICT_POSITION_ITEM_ID, "Отделка потолков, ось 3", {
        membership_state: "STALE",
        conflict_at: isoNow(),
        conflict_from_context_id: 601,
        routed_by: "manual",
      }),
    ],
    members_truncated: false,
    events: [event(4, "context_created"), event(5, "context_merged")],
    hasStaleMembers: false,
    hasConflictingMembers: true,
    hasNoMembers: false,
  };

  const insufficientDescription: SemanticContextFixture = {
    ...base,
    id: 604,
    bucket_id: 704,
    archived_at: null,
    catalog_position_id: 8004,
    standard_job_title: "Светильники",
    // Другая статья, чем у остальных пяти фикстур (77/«Отделочные работы»):
    // фильтр по статье (P2) обязан РАЗЛИЧАТЬ контексты, а не только принимать значение.
    work_category_id: 88,
    work_category_code: "07.01",
    work_category_title: "Электромонтажные работы",
    semantic_kind: "WORK",
    semantic_kind_source: "rule",
    semantic_kind_by: null,
    semantic_kind_at: null,
    name_role: "GENERIC_WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: "insufficient_description",
    semantic_state: "SUGGESTED",
    work_family_id: null,
    family_title: null,
    family_source: null,
    family_by: null,
    family_at: null,
    member_count: 4,
    bucket_contexts: [{ id: 604, is_default: true, archived_at: null, member_count: 4 }],
    members: [
      member(74001, "Светильники, секция 1"),
      member(74002, "Светильники, секция 2"),
      member(74003, "Светильники, секция 3"),
      member(74004, "Светильники, секция 4"),
    ],
    members_truncated: false,
    events: [event(6, "context_created")],
    hasStaleMembers: false,
    hasConflictingMembers: false,
    hasNoMembers: false,
  };

  const empty: SemanticContextFixture = {
    ...base,
    id: 605,
    bucket_id: 705,
    archived_at: null,
    catalog_position_id: 8005,
    standard_job_title: "Разборка временных перегородок",
    semantic_kind: "SYSTEM",
    semantic_kind_source: "manual",
    semantic_kind_by: 1,
    semantic_kind_at: isoNow(),
    name_role: "WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: null,
    semantic_state: "CONFIRMED",
    work_family_id: null,
    family_title: null,
    family_source: null,
    family_by: null,
    family_at: null,
    member_count: 0,
    bucket_contexts: [{ id: 605, is_default: false, archived_at: null, member_count: 0 }],
    members: [],
    members_truncated: false,
    events: [event(7, "context_created"), event(8, "members_moved")],
    hasStaleMembers: false,
    hasConflictingMembers: false,
    hasNoMembers: true,
  };

  const archivedContext: SemanticContextFixture = {
    ...base,
    id: 606,
    bucket_id: 706,
    archived_at: isoNow(),
    catalog_position_id: 8006,
    standard_job_title: "Гидроизоляция фундамента (снят)",
    semantic_kind: "WORK",
    semantic_kind_source: "rule",
    semantic_kind_by: null,
    semantic_kind_at: null,
    name_role: "WORK",
    name_role_source: "rule",
    name_role_by: null,
    name_role_at: null,
    comparability_reason: null,
    semantic_state: "SUGGESTED",
    work_family_id: null,
    family_title: null,
    family_source: null,
    family_by: null,
    family_at: null,
    member_count: 0,
    bucket_contexts: [{ id: 606, is_default: false, archived_at: isoNow(), member_count: 0 }],
    members: [],
    members_truncated: false,
    events: [event(9, "context_created"), event(10, "context_archived")],
    hasStaleMembers: false,
    hasConflictingMembers: false,
    hasNoMembers: true,
  };

  return [ordinary, stale, conflicted, insufficientDescription, empty, archivedContext];
}

/** Проекция фикстуры в форму ответа `GET /v1/semantic/contexts` — ровно поля `ContextRow`, три служебных флага не уходят наружу. */
function toContextRow(fixture: SemanticContextFixture): ContextRow {
  return {
    id: fixture.id,
    bucket_id: fixture.bucket_id,
    is_default: fixture.is_default,
    semantic_kind: fixture.semantic_kind,
    semantic_kind_source: fixture.semantic_kind_source,
    name_role: fixture.name_role,
    name_role_source: fixture.name_role_source,
    semantic_state: fixture.semantic_state,
    comparability_reason: fixture.comparability_reason,
    work_family_id: fixture.work_family_id,
    family_title: fixture.family_title,
    work_category_id: fixture.work_category_id,
    work_category_code: fixture.work_category_code,
    work_category_title: fixture.work_category_title,
    catalog_position_id: fixture.catalog_position_id,
    standard_job_title: fixture.standard_job_title,
    unit_code: fixture.unit_code,
    archived_at: fixture.archived_at,
  };
}

/** Проекция фикстуры в форму ответа `GET /v1/semantic/contexts/:id` — ровно поля `ContextCardData`, три служебных флага не уходят наружу. */
function toContextCard(fixture: SemanticContextFixture): ContextCardData {
  return {
    id: fixture.id,
    bucket_id: fixture.bucket_id,
    is_default: fixture.is_default,
    archived_at: fixture.archived_at,
    catalog_position_id: fixture.catalog_position_id,
    standard_job_title: fixture.standard_job_title,
    unit_id: fixture.unit_id,
    unit_code: fixture.unit_code,
    work_category_id: fixture.work_category_id,
    work_category_code: fixture.work_category_code,
    work_category_title: fixture.work_category_title,
    work_category_source: fixture.work_category_source,
    semantic_kind: fixture.semantic_kind,
    semantic_kind_source: fixture.semantic_kind_source,
    semantic_kind_by: fixture.semantic_kind_by,
    semantic_kind_at: fixture.semantic_kind_at,
    name_role: fixture.name_role,
    name_role_source: fixture.name_role_source,
    name_role_by: fixture.name_role_by,
    name_role_at: fixture.name_role_at,
    place_dictionary_version: fixture.place_dictionary_version,
    comparability_reason: fixture.comparability_reason,
    semantic_state: fixture.semantic_state,
    work_family_id: fixture.work_family_id,
    family_title: fixture.family_title,
    family_source: fixture.family_source,
    family_by: fixture.family_by,
    family_at: fixture.family_at,
    member_count: fixture.member_count,
    bucket_contexts: fixture.bucket_contexts,
    members: fixture.members,
    members_truncated: fixture.members_truncated,
    events: fixture.events,
  };
}

export const handlerState: HandlerState = {
  jobStatuses: ["done"],
  jobPolls: 0,
  uploadOutcome: "created",
  lastUploadReplace: false,
  lastBatch: null,
  batchSkipsFirst: false,
  fileOutcome: "ok",
  passportTopN: sampleAppSettings.passport_top_n,
  projectPassportOutcome: "full",
  matrixOutcome: "rows",
  positionsPendingReview: 0,
  lastReportRequest: null,
  contractCardEstimatesOverride: null,
  contractCardFails: false,
  attentionRequests: 0,
  attentionOutcome: "issues",
  inflationOutcome: "adjusted",
  inflationRequests: 0,
  inflationSeries: sampleInflationSeries,
  lastInflationBody: null,
  inflationPatches: 0,
  tenderRoundState: "loaded",
  participantDeleteOutcome: "preview",
  lastRoundUploadReplace: false,
  roundUploadConflictCode: "replace_required",
  tenderDeleteOutcome: "ok",
  stageSummaryOutcome: "ok",
  roundOverrideRequests: [],
  changesExportOutcome: "ok",
  lastChangesExportTenderId: null,
  workFamilies: initialWorkFamilies(),
  nextWorkFamilyId: 1000,
  lastCreateFamilyRequest: null,
  lastUpdateFamilyRequest: null,
  lastMergeFamiliesRequest: null,
  semanticContexts: initialSemanticContexts(),
  lastConfirmKindRequest: null,
  lastSetNameRoleRequest: null,
  lastAssignFamilyRequest: null,
  lastSplitContextRequest: null,
  lastMergeContextRequest: null,
  lastArchiveContextRequest: null,
  lastMoveMembersRequest: null,
  lastAcceptTransferRequest: null,
  lastAcceptTargetDecisionRequest: null,
};

export function resetHandlerState() {
  handlerState.jobStatuses = ["done"];
  handlerState.jobPolls = 0;
  handlerState.uploadOutcome = "created";
  handlerState.lastUploadReplace = false;
  handlerState.lastBatch = null;
  handlerState.batchSkipsFirst = false;
  handlerState.fileOutcome = "ok";
  handlerState.passportTopN = sampleAppSettings.passport_top_n;
  handlerState.projectPassportOutcome = "full";
  handlerState.matrixOutcome = "rows";
  handlerState.positionsPendingReview = 0;
  handlerState.lastReportRequest = null;
  handlerState.contractCardEstimatesOverride = null;
  handlerState.contractCardFails = false;
  handlerState.attentionRequests = 0;
  handlerState.attentionOutcome = "issues";
  handlerState.tenderRoundState = "loaded";
  handlerState.participantDeleteOutcome = "preview";
  handlerState.lastRoundUploadReplace = false;
  handlerState.roundUploadConflictCode = "replace_required";
  handlerState.tenderDeleteOutcome = "ok";
  handlerState.stageSummaryOutcome = "ok";
  handlerState.roundOverrideRequests = [];
  handlerState.changesExportOutcome = "ok";
  handlerState.lastChangesExportTenderId = null;
  handlerState.workFamilies = initialWorkFamilies();
  handlerState.nextWorkFamilyId = 1000;
  handlerState.lastCreateFamilyRequest = null;
  handlerState.lastUpdateFamilyRequest = null;
  handlerState.lastMergeFamiliesRequest = null;
  handlerState.semanticContexts = initialSemanticContexts();
  handlerState.lastConfirmKindRequest = null;
  handlerState.lastSetNameRoleRequest = null;
  handlerState.lastAssignFamilyRequest = null;
  handlerState.lastSplitContextRequest = null;
  handlerState.lastMergeContextRequest = null;
  handlerState.lastArchiveContextRequest = null;
  handlerState.lastMoveMembersRequest = null;
  handlerState.lastAcceptTransferRequest = null;
  handlerState.lastAcceptTargetDecisionRequest = null;
}

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

/**
 * Паспорт проекта, приведённый к одному из граничных случаев `HandlerState.
 * projectPassportOutcome` (спека §2.6, §2.9). Возвращает НОВЫЙ объект — не
 * мутирует `sampleProjectPassport`, иначе один тест испортил бы фикстуру для
 * следующего.
 */
function projectPassportForOutcome(
  outcome: HandlerState["projectPassportOutcome"]
): ProjectPassport {
  const base = sampleProjectPassport;
  switch (outcome) {
    case "full":
      return base;

    case "no-estimate":
      // Договор без сметы (правило 8 CRUD) — карточка есть, файла ещё нет:
      // дерево статей остаётся полным скелетом, но без единой суммы. Без
      // сметы нет ни строк дерева разноса, ни действующих ручных решений —
      // сервер отдаёт их пустыми списками явно (не наследует из `base`,
      // иначе этот вариант описывал бы состояние, которого бэкенд не может
      // произвести: договор без сметы с деревом «Нераспределённого» и живым
      // ручным решением внутри него). `category_options` — справочник
      // классификатора целиком, от сметы не зависит и остаётся полным.
      return {
        ...base,
        estimate: null,
        totals: { ...base.totals, amount: null, per_sqm: null, delta_to_file_total: null },
        categories: base.categories.map((c) => ({
          ...c,
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
        })),
        unallocated: {
          ...base.unallocated,
          amount: null,
          rows: 0,
          rows_priced: 0,
          rows_not_finite: 0,
          share_pct: null,
          per_sqm: null,
          chapters: 0,
          rows_outside_structure: 0,
          extras: [],
          sections: [],
        },
        manual_assignments: [],
      };

    case "no-tep":
      // ТЭП объекта не заведены (спека §2.3 фазы 5) — площадей нет, и `per_sqm`
      // обязан стать `null` ВЕЗДЕ, а не только у объекта: делить на
      // отсутствующую площадь нельзя нигде (правило 6).
      return {
        ...base,
        object: {
          ...base.object,
          area_aboveground_sp: null,
          area_underground_sp: null,
          area_total_sp: null,
          // Полезная тоже `null`: имя исхода означает «ТЭП НЕ ЗАВЕДЕНЫ», то
          // есть ни одной площади. С появлением четвёртой колонки (спека
          // 2026-08-15) исход, унаследовавший полезную из `base`, описывал бы
          // ДРУГОЕ состояние — «заведена одна полезная», у которого свой,
          // отличный текст.
          area_useful_sp: null,
        },
        totals: { ...base.totals, per_sqm: null },
        categories: base.categories.map((c) => ({ ...c, per_sqm: null })),
        unallocated: { ...base.unallocated, per_sqm: null },
      };

    case "empty-total":
      // Сумма НЕИЗВЕСТНА (не ноль!), хотя файловый итог известен — сверка
      // (правило 12) требует ДВА известных операнда, поэтому дельта тоже
      // `null`. Статьи, у которых есть строки, показывают `total: null`, а не
      // ноль: строки есть, их сумма просто не сложилась.
      return {
        ...base,
        totals: {
          ...base.totals,
          amount: null,
          per_sqm: null,
          delta_to_file_total: null,
        },
        categories: base.categories.map((c) =>
          c.rows > 0
            ? { ...c, total: null, per_sqm: null, share_pct: null }
            : { ...c, share_pct: null }
        ),
        unallocated: { ...base.unallocated, amount: null, per_sqm: null, share_pct: null },
      };

    case "zero-total":
      // Сумма РОВНО ноль — знаменатель непригоден для доли (правило `_share_
      // pct`: `grand_total == 0` даёт `None` точно так же, как `None`), и это
      // ОТЛИЧИМО от «сумма неизвестна» выше: там `amount: null`, здесь —
      // настоящий `"0.00"`.
      return {
        ...base,
        totals: { ...base.totals, amount: "0.00" },
        categories: base.categories.map((c) => ({ ...c, share_pct: null })),
        unallocated: { ...base.unallocated, share_pct: null },
      };

    case "corrupted":
      // Мусор в исходных числах (открытый хвост Ф4, спека §1.11): часть строк
      // не `is_finite()`, и сверка с файлом расходится — обе аномалии видны
      // одновременно, третья причина «непонятно, что не так» не годится.
      return {
        ...base,
        totals: {
          ...base.totals,
          positions_rows_not_finite: 5,
          file_total_including_vat: "4750000.00",
          delta_to_file_total: "-50000.00",
        },
      };

    case "error":
      // Обрабатывается отдельной веткой хендлера ниже — сюда не доходит.
      return base;
  }
}

export function jobPayload(status: ImportJobStatus) {
  return {
    ...sampleImportJobs[0],
    status,
    // Счётчики и смета появляются только у завершённого задания: до `done`
    // смета в БД ещё не лежит (§5).
    estimate_id: status === "done" ? 500 : null,
    error_text: status === "error" ? "Не удалось разобрать файл." : null,
  };
}

/**
 * Карточка тендера, приведённая к одному из граничных случаев `HandlerState.
 * tenderRoundState` (спека контура §2.12, §2.13). Возвращает НОВЫЙ объект —
 * не мутирует `sampleTenderCard`, иначе один тест испортил бы фикстуру для
 * следующего.
 */
function tenderCardFor(state: HandlerState["tenderRoundState"]): TenderCard {
  const [round1, round2] = sampleTenderCard.rounds;
  switch (state) {
    case "loaded":
      return sampleTenderCard;

    case "loaded-no-baseline":
      return {
        ...sampleTenderCard,
        rounds: [{ ...round1, baseline_estimate_id: null, baseline_total_including_vat: null }, round2],
      };

    case "empty":
      // Раунд без единого job (файла нет) — латест job и текущий job тоже null,
      // и все ячейки раунда 3001 обязаны стать «не участвовал» (offer_id null).
      // Разнос (§2.6): у раунда без offer-смет счётчик обязан стать null.
      return {
        ...sampleTenderCard,
        rounds: [{ ...round1, latest_job: null, current_job_id: null, unallocated_pending_sections: null }, round2],
        cells: sampleTenderCard.cells.map((cell) =>
          cell.round_id === round1.id
            ? { ...cell, offer_id: null, estimate_id: null, total_including_vat: null }
            : cell
        ),
      };

    case "changed":
      // Состав после загрузки изменился (например участника удалили): job есть,
      // но он больше не "текущий" — а ячейка (3001, 501) со сметой остаётся как
      // была в фикстуре (её никто не трогал).
      return {
        ...sampleTenderCard,
        rounds: [{ ...round1, current_job_id: null }, round2],
      };

    case "both-loaded":
      // Плитки выбора для свода (спека свода §2.1, задача 7): у Альфы смета в
      // ОБОИХ раундах, а не только в первом — иначе клик по имени участника
      // выбирал бы одну плитку и тест не отличил бы «выбрать все сметы» от
      // «выбрать единственную».
      return {
        ...sampleTenderCard,
        rounds: [round1, { ...round2, unallocated_pending_sections: 0 }],
        cells: sampleTenderCard.cells.map((cell) =>
          cell.round_id === round2.id && cell.package_id === 501
            ? { ...cell, offer_id: 7003, estimate_id: 8003, total_including_vat: "1100.00" }
            : cell
        ),
      };

    case "both-loaded-with-beta":
      // То же самое плюс у Беты тоже смета во втором раунде (была только
      // offer_id без estimate_id) — доказывает, что чужая плитка недоступна
      // именно потому, что выбор уже сделан по другому участнику, а не потому,
      // что у Беты вообще нет плиток.
      return {
        ...sampleTenderCard,
        rounds: [round1, { ...round2, unallocated_pending_sections: 0 }],
        cells: sampleTenderCard.cells.map((cell) => {
          if (cell.round_id === round2.id && cell.package_id === 501) {
            return { ...cell, offer_id: 7003, estimate_id: 8003, total_including_vat: "1100.00" };
          }
          if (cell.round_id === round2.id && cell.package_id === 502) {
            return { ...cell, offer_id: 7002, estimate_id: 8002, total_including_vat: "1300.00" };
          }
          return cell;
        }),
      };

    case "second-round-no-estimate":
      // Альфа участвовала во втором раунде (offer_id есть), но смета туда не
      // загрузилась (estimate_id остаётся null — "нет сметы"). Клик по имени
      // участника обязан взять только первую смету (задача 7, находка ревью):
      // фильтр по обоим id — не только по offer_id — иначе предложение без
      // сметы попало бы в выбор молча, без единой плитки на экране.
      return {
        ...sampleTenderCard,
        rounds: [round1, { ...round2, unallocated_pending_sections: null }],
        cells: sampleTenderCard.cells.map((cell) =>
          cell.round_id === round2.id && cell.package_id === 501 ? { ...cell, offer_id: 7003 } : cell
        ),
      };
  }
}

/**
 * Значение линии `shown_per_sqm` медианы «Итого» в режиме `single` — заранее
 * посчитанная строка-константа (Global Constraint 1 плана: денежная
 * арифметика в JS, включая `Number()` над деньгами, запрещена).
 *
 * Посчитано ВРУЧНУЮ для value медианы фикстуры `sampleComparison.totals_medians`
 * ("1501.88") при ЭФФЕКТИВНОЙ ставке показа `rate_preselected` ("20.00" —
 * подставляется обработчиком, когда запрос не задал `single_rate` своим
 * значением): `net_to_gross(net, target) = net * (100 + target) / 100`
 * (`backend/money/vat.py`), то есть 1501.88 * 120 / 100 = 180225.60 / 100 =
 * 1802.2560.
 *
 * Годится ТОЛЬКО для этого значения медианы: если фикстура когда-нибудь
 * изменит "1501.88", константу нужно пересчитать вручную ещё раз — здесь
 * нет обратной проверки.
 */
const TOTALS_MEDIAN_SINGLE_SHOWN_PER_SQM = "1802.2560";

/**
 * Добавляет `shown_per_sqm` медиане «Итого» по правилу присутствия спеки
 * диаграммы §2.8 (DoD 22б): ключ ЕСТЬ при `net` и `single`, ЕГО НЕТ при `own`.
 * В режиме `net` значение равно самому `value` (сервер отражает нетто без
 * пересчёта — `_shown_per_sqm_value`); в режиме `single` — заранее посчитанная
 * константа выше, `null`, если `value` сам `null` (правило самосогласованности
 * задачи: «значение `null`, только если `value` равно `null`»).
 */
function totalsMedianWithShownPerSqm(
  median: ComparisonMedian,
  vatMode: ComparisonVatMode
): ComparisonMedian {
  if (vatMode === "own") return median;
  const shownPerSqm =
    median.value === null
      ? null
      : vatMode === "net"
        ? median.value
        : TOTALS_MEDIAN_SINGLE_SHOWN_PER_SQM;
  return { ...median, shown_per_sqm: shownPerSqm };
}

/** `totalsMedianWithShownPerSqm` над ВСЕМИ трёх корзинами `totals_medians`. */
export function totalsMediansWithMode(
  medians: Comparison["totals_medians"],
  vatMode: ComparisonVatMode
): Comparison["totals_medians"] {
  return {
    base: totalsMedianWithShownPerSqm(medians.base, vatMode),
    amendments: totalsMedianWithShownPerSqm(medians.amendments, vatMode),
    total: totalsMedianWithShownPerSqm(medians.total, vatMode),
  };
}

/**
 * Приведённый агрегат сравнения — из номинального, УМНОЖЕНИЕМ (спека §2.5).
 *
 * Множители у двух рядов РАЗНЫЕ намеренно: одинаковые означали бы, что селектор
 * меняет подпись, не меняя чисел, — та же ложь, только незаметнее (дефект макета,
 * §7 спеки). Поэтому тест «смена ряда меняет и подпись, и числа» на этой фикстуре
 * доказуем.
 *
 * Первая колонка получает РАСХОЖДЕНИЕ множителей (`inflation_coefficient: null`
 * плюс `inflation_factors`): случай «в договоре ДГП 2024 года и ДС 2026-го» на
 * стенде не воспроизводится вовсе — допсоглашений там ноль, — и без фикстуры чип
 * «разные» остался бы непроверенным.
 *
 * **Номинал (спека §2.8, §2.10, DoD 13/17)** приезжает ТОЛЬКО здесь — это
 * единственный путь, где приведение вообще посчитано (`seriesId` задан). У
 * `totals[]` номинал — денежное подмножество ИСХОДНОЙ (нескаленной) ячейки
 * `base.totals[]`, БЕЗ `state`/`deviation_pct` (`_nominal_bucket_cell_dict`);
 * строки дерева (`rows[].cells[]`) номинала не получают вовсе (DoD 17), и
 * `scaleCells` их сериализатор НЕ трогает. У `totals_medians[bucket]` номинал —
 * `{value, shown_per_sqm?}` той же, ДОНОМИНАЛЬНОЙ, медианы (`base.totals_medians`,
 * которая уже несёт `shown_per_sqm` по правилу присутствия выше).
 *
 * **`totals_medians` ОБЯЗАН масштабироваться тем же `factor`, что и `totals`**
 * (смежный дефект, найденный ревью: до этой правки медиана приезжала
 * номинальной, то есть равной самой себе после приведения — столбцы сдвигались,
 * линия медианы нет, что противоречит DoD 16 и обесценивает DoD 23). Множитель
 * применяется к `value` И к `shown_per_sqm` одинаково: `net_to_gross` линеен по
 * нетто (`net_to_gross(net·k, ставка) = net_to_gross(net, ставка)·k`), поэтому
 * масштабирование обеих величин ОДНИМ И ТЕМ ЖЕ точным умножением строк
 * (`multiplyDecimalStrings`, тот же приём, что у `scaleCell` ниже) даёт то же
 * число, что дал бы пересчёт `net_to_gross` от уже приведённого нетто — без
 * повторного деления и без второй захардкоженной константы.
 */
function adjustedComparison(
  base: Comparison,
  seriesId: number,
  targetMonth: string | null
): Comparison {
  const factor = seriesId === 2 ? "1.2670" : "1.1744";
  const seriesName =
    sampleInflationSeries.find((row) => row.id === seriesId)?.name ?? "неизвестный ряд";
  const month = targetMonth ?? "2026-08";

  const scale = (value: string | null): string | null =>
    value === null ? null : multiplyDecimalStrings(value, factor);

  const scaleCell = (cell: ComparisonBucketCell): ComparisonBucketCell => ({
    ...cell,
    net: scale(cell.net),
    shown: scale(cell.shown),
    net_per_sqm: scale(cell.net_per_sqm),
    shown_per_sqm: scale(cell.shown_per_sqm),
  });

  const scaleCells = (cells: Comparison["totals"]): Comparison["totals"] =>
    cells.map((cell) => ({
      ...cell,
      base: scaleCell(cell.base),
      amendments: scaleCell(cell.amendments),
      total: scaleCell(cell.total),
    }));

  /** Денежное подмножество НОМИНАЛЬНОЙ ячейки — вход `nominal` у `totals[]`. */
  const nominalCellDict = (cell: ComparisonBucketCell) => ({
    net: cell.net,
    shown: cell.shown,
    net_per_sqm: cell.net_per_sqm,
    shown_per_sqm: cell.shown_per_sqm,
  });

  /** `scaleCell` ПЛЮС `nominal` — ТОЛЬКО для `totals[]` (DoD 17: строки его не несут). */
  const scaleCellWithNominal = (cell: ComparisonBucketCell): ComparisonBucketCell => ({
    ...scaleCell(cell),
    nominal: nominalCellDict(cell),
  });

  const scaleTotalsCells = (cells: Comparison["totals"]): Comparison["totals"] =>
    cells.map((cell) => ({
      ...cell,
      base: scaleCellWithNominal(cell.base),
      amendments: scaleCellWithNominal(cell.amendments),
      total: scaleCellWithNominal(cell.total),
    }));

  /** Денежное подмножество НОМИНАЛЬНОЙ медианы — вход `nominal` у `totals_medians`. */
  const nominalMedianDict = (median: ComparisonMedian): { value: string | null; shown_per_sqm?: string | null } => {
    const out: { value: string | null; shown_per_sqm?: string | null } = { value: median.value };
    if (median.shown_per_sqm !== undefined) out.shown_per_sqm = median.shown_per_sqm;
    return out;
  };

  /** Медиана «Итого» масштабированная ПЛЮС `nominal` доскаленной (см. докстроку выше). */
  const scaleTotalsMedian = (median: ComparisonMedian): ComparisonMedian => ({
    ...median,
    value: scale(median.value),
    ...(median.shown_per_sqm !== undefined ? { shown_per_sqm: scale(median.shown_per_sqm) } : {}),
    nominal: nominalMedianDict(median),
  });

  return {
    ...base,
    caption: `${base.caption} Цены приведены к августу 2026 по ряду «${seriesName}».`,
    columns: base.columns.map((column, index) =>
      index === 0
        ? {
            ...column,
            inflation_coefficient: null,
            inflation_factors: [
              { label: "ДГП", coefficient: factor },
              { label: "ДС №1", coefficient: "1.0000" },
            ],
          }
        : { ...column, inflation_coefficient: factor }
    ),
    rows: base.rows.map((row) => ({
      ...row,
      cells: scaleCells(row.cells),
      medians: {
        base: { ...row.medians.base, value: scale(row.medians.base.value) },
        amendments: { ...row.medians.amendments, value: scale(row.medians.amendments.value) },
        total: { ...row.medians.total, value: scale(row.medians.total.value) },
      },
    })),
    totals: scaleTotalsCells(base.totals),
    totals_medians: {
      base: scaleTotalsMedian(base.totals_medians.base),
      amendments: scaleTotalsMedian(base.totals_medians.amendments),
      total: scaleTotalsMedian(base.totals_medians.total),
    },
    inflation: {
      series_id: seriesId,
      series_name: seriesName,
      series_note: sampleInflationSeries.find((row) => row.id === seriesId)?.note ?? null,
      series_updated_at: "2026-01-12T10:00:00+03:00",
      target_month: month,
      has_forecast: true,
      used_years: sampleInflationValues[seriesId] ?? [],
    },
  };
}

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  // Auth
  http.get("/api/auth/me", () =>
    HttpResponse.json({ id: 1, email: "test@example.com", role: "admin" })
  ),
  http.post("/api/auth/login", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/logout", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/refresh", () => HttpResponse.json({ status: "ok" })),

  // Units
  http.get("/api/units", () =>
    HttpResponse.json([
      { id: 1, code: "TON", name: "Тонна", symbol: "т", dimension: "mass", base_unit_id: null },
      { id: 3, code: "M3", name: "Куб. метр", symbol: "м³", dimension: "volume", base_unit_id: null },
      { id: 5, code: "M2", name: "Кв. метр", symbol: "м²", dimension: "area", base_unit_id: null },
    ])
  ),

  // Admin: пользователи
  http.get("/api/admin/users", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const pageNo = Number(url.searchParams.get("page") ?? 1) || 1;
    const page_size = Number(url.searchParams.get("page_size") ?? 20) || 20;
    const filtered = q
      ? sampleAdminUsers.filter((u) => u.email.toLowerCase().includes(q))
      : sampleAdminUsers;
    const start = (pageNo - 1) * page_size;
    return HttpResponse.json({
      items: filtered.slice(start, start + page_size),
      total: filtered.length,
      page: pageNo,
      page_size,
    });
  }),
  http.post("/api/admin/users", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      {
        id: 100,
        email: body.email ?? "new@example.com",
        role: body.role ?? "member",
        is_active: body.is_active ?? true,
      },
      { status: 201 }
    );
  }),
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(body, "role") && body.role === null) {
      return HttpResponse.json({ detail: "Поле role не может быть null" }, { status: 422 });
    }
    if (Object.prototype.hasOwnProperty.call(body, "is_active") && body.is_active === null) {
      return HttpResponse.json({ detail: "Поле is_active не может быть null" }, { status: 422 });
    }
    return HttpResponse.json({
      id: Number(params.id),
      email: "a.petrov@example.com",
      role: (body.role as string) ?? "admin",
      is_active: (body.is_active as boolean) ?? true,
    });
  }),
  http.post("/api/admin/users/:id/reset-password", ({ params }) =>
    HttpResponse.json({ id: Number(params.id), email: "a.petrov@example.com", password: "Xk7m-Pq9L-vf2Z" })
  ),

  // --- Справочники (фаза 5) ---
  http.get("/api/v1/rate-classes", () => HttpResponse.json(sampleRateClasses)),
  http.post("/api/v1/rate-classes", async ({ request }) => {
    const body = (await request.json()) as { title: string; description?: string | null };
    if (sampleRateClasses.some((c) => c.title === body.title)) {
      return HttpResponse.json(
        { detail: "Класс объектов с таким названием уже есть." },
        { status: 409 }
      );
    }
    return HttpResponse.json(
      {
        id: 3,
        title: body.title,
        description: body.description ?? null,
        contracts_count: 0,
        objects_count: 0,
        standards_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),
  http.delete("/api/v1/rate-classes/:id", ({ params }) => {
    const rateClass = sampleRateClasses.find((c) => c.id === Number(params.id));
    if (rateClass && (rateClass.contracts_count > 0 || rateClass.standards_count > 0)) {
      return HttpResponse.json(
        {
          detail: `Класс «${rateClass.title}» удалить нельзя: на него ссылаются договоры (${rateClass.contracts_count}) и нормативы (${rateClass.standards_count}).`,
        },
        { status: 409 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/v1/objects", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    const items = q
      ? sampleObjects.filter((o) =>
          `${o.title} ${o.address}`.toLowerCase().includes(q)
        )
      : sampleObjects;
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/objects/:id", ({ params }) => {
    const found = sampleObjects.find((o) => o.id === Number(params.id));
    if (!found) {
      return HttpResponse.json({ detail: `Объект ${params.id} не найден.` }, { status: 404 });
    }
    return HttpResponse.json(found);
  }),
  http.post("/api/v1/objects", async ({ request }) => {
    const body = (await request.json()) as { title: string };
    return HttpResponse.json(
      {
        id: 11,
        title: body.title,
        address: "",
        rate_class_id: null,
        rate_class_title: null,
        area_aboveground_sp: null,
        area_underground_sp: null,
        area_total_sp: null,
        area_useful_sp: null,
        contracts_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),
  http.patch("/api/v1/objects/:id", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const found = sampleObjects.find((o) => o.id === Number(params.id));
    return HttpResponse.json({ ...(found ?? sampleObjects[0]), ...body, id: Number(params.id) });
  }),

  http.get("/api/v1/contractors", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    const items = q
      ? sampleContractors.filter((c) => `${c.title} ${c.inn}`.toLowerCase().includes(q))
      : sampleContractors;
    return HttpResponse.json(page(items));
  }),
  http.post("/api/v1/contractors", async ({ request }) => {
    const body = (await request.json()) as { title: string; inn: string };
    return HttpResponse.json(
      {
        id: 21,
        title: body.title,
        inn: body.inn,
        address: "",
        accreditation: "",
        contracts_count: 0,
        created_at: null,
        updated_at: null,
      },
      { status: 201 }
    );
  }),

  // --- Договоры ---
  http.get("/api/v1/contracts", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const rateClassId = url.searchParams.get("rate_class_id");
    let items = sampleContracts;
    if (q) {
      items = items.filter((c) =>
        [c.contract_number, c.title ?? "", c.object_title, c.contractor_title]
          .join(" ")
          .toLowerCase()
          .includes(q)
      );
    }
    if (rateClassId) {
      items = items.filter((c) => c.rate_class_id === Number(rateClassId));
    }
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/contracts/:id/import-jobs", () =>
    HttpResponse.json([...sampleImportJobs, sampleFailedJob, sampleRunningJob])
  ),
  http.get("/api/v1/contracts/:id", ({ params }) => {
    if (Number(params.id) !== sampleContractCard.id) {
      return HttpResponse.json({ detail: "Договор не найден." }, { status: 404 });
    }
    if (handlerState.contractCardFails) {
      return HttpResponse.json(
        { detail: "Не удалось загрузить карточку договора." },
        { status: 500 }
      );
    }
    if (handlerState.contractCardEstimatesOverride === null) {
      return HttpResponse.json(sampleContractCard);
    }
    return HttpResponse.json({
      ...sampleContractCard,
      estimates: handlerState.contractCardEstimatesOverride,
    });
  }),
  http.post("/api/v1/contracts", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      { ...sampleContractCard, id: 102, contract_number: body.contract_number },
      { status: 201 }
    );
  }),
  http.patch("/api/v1/contracts/:id", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...sampleContractCard, ...body });
  }),
  http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 })),

  // --- Загрузка сметы и поллинг ---
  http.post("/api/v1/estimates/upload", async ({ request }) => {
    // `request.formData()` здесь неприменим: под jsdom `File` не тот, который
    // признаёт undici внутри msw, и парсер multipart падает на ассерте
    // (`webidl.is.File`). Тело читается текстом, а нужное поле — по имени: это
    // дефект окружения, и обходить его в обработчике правильнее, чем менять
    // боевой транспорт под тест.
    const body = await request.text();
    handlerState.lastUploadReplace = /name="replace"[\s\S]*?\btrue\b/.test(body);

    if (handlerState.uploadOutcome === "conflict" && !handlerState.lastUploadReplace) {
      return HttpResponse.json(
        {
          detail:
            "Смета уже загружена (estimate_id=500); для замены повторите запрос с replace=true.",
        },
        { status: 409 }
      );
    }
    if (handlerState.uploadOutcome === "idempotent") {
      // 200, а не 202: ничего не создано и ничего не запущено (§5, правило 1).
      return HttpResponse.json(jobPayload("done"), { status: 200 });
    }
    return HttpResponse.json(jobPayload(handlerState.jobStatuses[0] ?? "pending"), {
      status: 202,
    });
  }),
  http.get("/api/v1/import-jobs/:id/file", () => {
    if (handlerState.fileOutcome === "missing") {
      return HttpResponse.json({ detail: "Задание импорта не найдено." }, { status: 404 });
    }
    if (handlerState.fileOutcome === "purged") {
      // 410: запись задания жива (это аудит), а файл удалён ретенцией (§8).
      return HttpResponse.json(
        { detail: "Файл задания удалён при очистке хранилища." },
        { status: 410 }
      );
    }
    return new HttpResponse("PK-fake-xlsx", {
      headers: { "Content-Type": "application/octet-stream" },
    });
  }),
  http.get("/api/v1/import-jobs/:id", () => {
    const index = Math.min(handlerState.jobPolls, handlerState.jobStatuses.length - 1);
    handlerState.jobPolls += 1;
    return HttpResponse.json(jobPayload(handlerState.jobStatuses[index]));
  }),

  // --- Каталог и Review ---
  http.get("/api/v1/catalog-positions", () =>
    HttpResponse.json([
      { id: 800, standard_job_title: "Кладка кирпичная", unit_id: 3, unit_code: "M3", unit_name: "Куб. метр" },
    ])
  ),
  http.get("/api/v1/review/queue", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const withoutUnit = url.searchParams.get("without_unit") === "true";
    const sort = url.searchParams.get("sort") ?? "positions";

    let items = [...sampleReviewQueue];
    if (q) items = items.filter((i) => i.standard_job_title.toLowerCase().includes(q));
    if (withoutUnit) items = items.filter((i) => i.unit_id === null);
    items.sort((a, b) =>
      sort === "title"
        ? a.standard_job_title.localeCompare(b.standard_job_title)
        : b.position_count - a.position_count
    );
    return HttpResponse.json({ items, total: items.length, page: 1, page_size: 50 });
  }),
  http.get("/api/v1/review/targets", () =>
    HttpResponse.json([
      { id: 801, standard_job_title: "Стяжка цементная", unit_id: 5, unit_code: "M2", unit_name: "Кв. метр" },
    ])
  ),
  http.post("/api/v1/review/:id/merge", async ({ params, request }) => {
    const body = (await request.json()) as { target_id: number };
    return HttpResponse.json({
      to_review_id: Number(params.id),
      target: {
        id: body.target_id,
        standard_job_title: "Стяжка цементная",
        normalized_job_title: "стяжка цементный",
        kind: "POSITION",
        unit_id: 5,
        unit_code: "M2",
        unit_name: "Кв. метр",
      },
      moved_positions: 42,
      warnings: [],
    });
  }),
  http.post("/api/v1/review/:id/kind", async ({ params, request }) => {
    const body = (await request.json()) as { kind: string };
    return HttpResponse.json({
      id: Number(params.id),
      standard_job_title: "Стяжка неведомая",
      normalized_job_title: "стяжка неведомый",
      kind: body.kind,
      unit_id: 5,
      unit_code: "M2",
      unit_name: "Кв. метр",
    });
  }),
  http.post("/api/v1/review/batch-kind", async ({ request }) => {
    const body = (await request.json()) as { ids: number[]; kind: string };
    handlerState.lastBatch = body;
    const ids = [...body.ids].sort((a, b) => a - b);
    if (handlerState.batchSkipsFirst && ids.length > 0) {
      return HttpResponse.json({
        kind: body.kind,
        applied: ids.slice(1),
        skipped: [
          {
            id: ids[0],
            reason: `Каталожная строка ${ids[0]} имеет kind=POSITION, а операция применима к TO_REVIEW.`,
          },
        ],
      });
    }
    return HttpResponse.json({ kind: body.kind, applied: ids, skipped: [] });
  }),

  // --- Нормативы ---
  // --- Ряды индексов инфляции (спека 2026-08-18 §2.12) ----------------------
  //
  // Состояние живёт в `handlerState.inflationSeries`, потому что тесты вкладки
  // проверяют ПЕРЕХОДЫ: «В архив» обязан увести ряд из активных, а «Вернуть в
  // активные» — вернуть. На неизменяемой фикстуре второй шаг был бы недоказуем.
  http.get("/api/v1/inflation-series", ({ request }) => {
    const includeArchived = new URL(request.url).searchParams.get("include_archived");
    const rows = handlerState.inflationSeries;
    return HttpResponse.json(includeArchived ? rows : rows.filter((row) => row.is_active));
  }),

  http.get("/api/v1/inflation-series/:id/values", ({ params }) =>
    HttpResponse.json(sampleInflationValues[Number(params.id)] ?? [])
  ),

  http.post("/api/v1/inflation-series", async ({ request }) => {
    const body = (await request.json()) as {
      name: string;
      note: string | null;
      values: { year: number }[];
    };
    handlerState.lastInflationBody = body;
    const years = body.values.map((value) => value.year);
    const created: InflationSeries = {
      id: 90 + handlerState.inflationSeries.length,
      name: body.name,
      note: body.note,
      is_active: true,
      year_from: years.length > 0 ? Math.min(...years) : null,
      year_to: years.length > 0 ? Math.max(...years) : null,
      value_count: years.length,
      created_at: "2026-08-19T12:00:00+03:00",
      updated_at: "2026-08-19T12:00:00+03:00",
    };
    handlerState.inflationSeries = [...handlerState.inflationSeries, created];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch("/api/v1/inflation-series/:id", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastInflationBody = body;
    handlerState.inflationPatches += 1;
    const id = Number(params.id);
    const target = handlerState.inflationSeries.find((row) => row.id === id);
    if (!target) return new HttpResponse(null, { status: 404 });
    // Архивный ряд правится ТОЛЬКО телом `{is_active: true}` в одиночку (§2.10):
    // хендлер повторяет это правило, иначе тест двух шагов проходил бы и на
    // клиенте, который шлёт разморозку вместе с правкой.
    const unfreezeOnly =
      body.is_active === true && Object.keys(body).length === 1;
    if (!target.is_active && !unfreezeOnly) {
      return HttpResponse.json(
        { detail: `Ряд «${target.name}» в архиве и не правится.` },
        { status: 409 }
      );
    }
    const updated: InflationSeries = {
      ...target,
      ...(typeof body.name === "string" ? { name: body.name } : {}),
      ...("note" in body ? { note: (body.note as string | null) ?? null } : {}),
      ...(typeof body.is_active === "boolean" ? { is_active: body.is_active } : {}),
    };
    handlerState.inflationSeries = handlerState.inflationSeries.map((row) =>
      row.id === id ? updated : row
    );
    return HttpResponse.json(updated);
  }),

  http.get("/api/v1/rate-standards", ({ request }) => {
    const url = new URL(request.url);
    const onDate = url.searchParams.get("on_date");
    let items = sampleRateStandards;
    if (onDate) {
      // Тот же полуинтервал [valid_from, valid_to), что у EXCLUDE и VIEW.
      items = items.filter(
        (s) => s.valid_from <= onDate && (s.valid_to === null || s.valid_to > onDate)
      );
    }
    return HttpResponse.json(page(items));
  }),
  http.post("/api/v1/rate-standards", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...sampleRateStandards[0], id: 302, ...body }, { status: 201 });
  }),
  http.post("/api/v1/rate-standards/:id/reapprove", async ({ params, request }) => {
    const body = (await request.json()) as { valid_from: string; standard_unit_rate?: string };
    const previous = sampleRateStandards.find((s) => s.id === Number(params.id));
    return HttpResponse.json({
      previous: { ...previous, valid_to: body.valid_from },
      current: {
        ...previous,
        id: 303,
        standard_unit_rate: body.standard_unit_rate ?? "1075.35475",
        valid_from: body.valid_from,
        valid_to: null,
      },
    });
  }),
  http.delete("/api/v1/rate-standards/:id", () => new HttpResponse(null, { status: 204 })),

  // --- Настройки (фаза 6, §7.4) ---
  http.get("/api/v1/settings", () =>
    HttpResponse.json({ ...sampleAppSettings, passport_top_n: handlerState.passportTopN })
  ),
  http.patch("/api/v1/settings", async ({ request }) => {
    const body = (await request.json()) as { passport_top_n: number };
    // Диапазон проверяет сервер, и его отказ объясняет причину (раскладка экрана
    // паспорта фазы 6). Обработчик воспроизводит именно это поведение, а не
    // «принимает всё».
    if (
      !Number.isInteger(body.passport_top_n) ||
      body.passport_top_n < sampleAppSettings.passport_top_n_min ||
      body.passport_top_n > sampleAppSettings.passport_top_n_max
    ) {
      return HttpResponse.json(
        {
          detail:
            `Число ключевых расценок должно быть от ${sampleAppSettings.passport_top_n_min} до ` +
            `${sampleAppSettings.passport_top_n_max}. Верхняя граница — не прихоть: она ` +
            "подобрана под раскладку экрана паспорта фазы 6, а не взята произвольно.",
        },
        { status: 422 }
      );
    }
    handlerState.passportTopN = body.passport_top_n;
    return HttpResponse.json({ ...sampleAppSettings, passport_top_n: body.passport_top_n });
  }),

  // --- Аналитика (фаза 6, §6, §7.4–§7.5) ---
  // Паспорт проекта по статьям классификатора (Ф6 фазы 7, задача 6).
  http.get("/api/v1/analytics/project-passport/:contractId", () => {
    if (handlerState.projectPassportOutcome === "error") {
      return HttpResponse.json(
        { detail: "Не удалось построить паспорт проекта." },
        { status: 500 }
      );
    }
    return HttpResponse.json(projectPassportForOutcome(handlerState.projectPassportOutcome));
  }),

  // Ручной разнос разделов по статьям (спека разноса §2.6). Ответ — сводка
  // изменений, НЕ паспорт (форма паспорта объявлена ровно один раз в фикстуре).
  http.put(
    "/api/v1/estimates/:estimateId/category-overrides/:positionItemId",
    async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as {
        work_category_id?: unknown;
        note?: string | null;
      };
      if (typeof body.work_category_id !== "number") {
        return HttpResponse.json(
          { detail: "Поле work_category_id обязательно." },
          { status: 422 }
        );
      }
      return HttpResponse.json({
        chapters_updated: 1,
        additional_works_updated: 0,
        chapters_manual: 1,
      });
    }
  ),
  http.delete("/api/v1/estimates/:estimateId/category-overrides/:positionItemId", () =>
    HttpResponse.json({ chapters_updated: 1, additional_works_updated: 0, chapters_manual: 0 })
  ),

  http.get("/api/v1/analytics/dashboard", () => HttpResponse.json(sampleDashboard)),

  /**
   * Диагностики второго таба. Счётчик вызовов инкрементируется ДО ветвления по
   * исходу: тест «`member` не отправляет ни одного запроса» смотрит именно на
   * него, а не на содержимое ответа.
   */
  http.get("/api/v1/analytics/dashboard/attention", () => {
    handlerState.attentionRequests += 1;
    return HttpResponse.json(
      handlerState.attentionOutcome === "clean"
        ? sampleDashboardAttentionClean
        : sampleDashboardAttention
    );
  }),

  http.get("/api/v1/analytics/matrix", ({ request }) => {
    const url = new URL(request.url);
    if (handlerState.matrixOutcome === "no-columns") {
      return HttpResponse.json({ ...sampleMatrix, columns: [], rows: [], total: 0 });
    }
    if (handlerState.matrixOutcome === "no-rows") {
      return HttpResponse.json({ ...sampleMatrix, rows: [], total: 0 });
    }
    if (handlerState.matrixOutcome === "pending-review") {
      // Сметы загружены и расценены, но каталог ещё не разобран — состояние,
      // которое нашёл прогон стенда фазы 6.
      return HttpResponse.json({
        ...sampleMatrix,
        rows: [],
        total: 0,
        positions_pending_review: 1830,
      });
    }
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const rows = q
      ? sampleMatrix.rows.filter((r) => r.job_title.toLowerCase().includes(q))
      : sampleMatrix.rows;
    return HttpResponse.json({
      ...sampleMatrix,
      rows,
      total: rows.length,
      page: Number(url.searchParams.get("page") ?? 1),
      // Счётчик очереди НЕ зависит от `q`: он про выборку, а не про поиск.
      positions_pending_review: handlerState.positionsPendingReview,
    });
  }),

  http.get("/api/v1/analytics/matrix/cell", () => HttpResponse.json(sampleMatrixCellDetail)),

  /**
   * Сравнение договоров (спека 2026-08-17, задача 8). Выборка (`ids`/`all` +
   * фильтры) игнорируется намеренно — фикстура одна и та же, тест страницы
   * проверяет клиентское поведение (дерево, переключатели, URL), а не то,
   * что сервер умеет фильтровать (это покрыто `test_comparison_api.py`).
   * `vat_mode`/`single_rate` эхом отражаются в ответе — иначе тест
   * восстановления режима из URL не смог бы отличить «страница прочитала
   * URL» от «страница показывает то, что всегда приходит с сервера».
   */
  http.get("/api/v1/analytics/comparison", ({ request }) => {
    const url = new URL(request.url);
    const vatMode = (url.searchParams.get("vat_mode") ?? "own") as ComparisonVatMode;
    const singleRateParam = url.searchParams.get("single_rate");
    const singleRate =
      vatMode === "single" ? (singleRateParam ?? sampleComparison.rate_preselected) : null;

    const seriesId = url.searchParams.get("inflation_series_id");
    const base: Comparison = {
      ...sampleComparison,
      vat_mode: vatMode,
      single_rate: singleRate,
      // Правило присутствия §2.8/DoD 22б не зависит от приведения: ключ
      // `shown_per_sqm` обязан появляться в `net`/`single` и без него — сам
      // факт запроса приведения тут ни при чём (см. докстроку хелпера).
      totals_medians: totalsMediansWithMode(sampleComparison.totals_medians, vatMode),
    };
    if (!seriesId) return HttpResponse.json(base);

    handlerState.inflationRequests += 1;

    // Отказ приведения — структурированный `422` с кодом и контекстом (§2.12).
    // Управляется `handlerState.inflationOutcome`, потому что экран обязан
    // показать НОМИНАЛЬНЫЙ вариант с баннером, а это второй запрос: на
    // неуправляемом хендлере отличить его от первого было бы нечем.
    if (handlerState.inflationOutcome === "missing-years") {
      return HttpResponse.json(
        {
          detail: {
            code: "missing_inflation_years",
            message: "Не заданы коэффициенты за годы: 2024, 2026.",
            missing_years: [2024, 2026],
          },
        },
        { status: 422 }
      );
    }
    if (handlerState.inflationOutcome === "amendment-date") {
      return HttpResponse.json(
        {
          detail: {
            code: "amendment_date_missing",
            message: "У допсоглашений нет собственной даты подготовки: ГП-0007 ДС №1.",
            estimate_ids: [7],
          },
        },
        { status: 422 }
      );
    }

    return HttpResponse.json(
      adjustedComparison(base, Number(seriesId), url.searchParams.get("target_month"))
    );
  }),

  // --- Выгрузки §7.6 ---
  //
  // Отдаём непустой blob с настоящим media type: экран не разбирает содержимое, но
  // разбирает отказы, а `responseType: "blob"` меняет форму ответа axios — на
  // JSON-заглушке этого пути было бы не видно. Параметры запроса сохраняются в
  // состоянии, чтобы тест мог проверить, что фильтры доехали до сервера.
  http.get("/api/v1/reports/contract-summary", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "contract-summary",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
  /**
   * Третий файл §7.6 (`AGENTS.md` v6.8) — выгрузка сравнения. Параметры
   * сохраняются тем же способом, что у двух других: тест страницы сравнения
   * проверяет, что лист запрошен с ТЕМ ЖЕ режимом НДС, что открыт на экране,
   * иначе числа файла и экрана разошлись бы (спека §2.7).
   */
  http.get("/api/v1/reports/comparison", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "comparison",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
  http.get("/api/v1/reports/bank-comparison", ({ request }) => {
    const url = new URL(request.url);
    handlerState.lastReportRequest = {
      report: "bank-comparison",
      params: Object.fromEntries(url.searchParams),
    };
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),

  // --- Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13, §2.14) ---
  http.get("/api/v1/tenders", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase();
    const items = q
      ? sampleTenders.filter((t) =>
          `${t.tender_number} ${t.title} ${t.object_title}`.toLowerCase().includes(q)
        )
      : sampleTenders;
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/tenders/:id", ({ params }) => {
    if (Number(params.id) !== sampleTenderCard.id) {
      return HttpResponse.json({ detail: "Тендер не найден." }, { status: 404 });
    }
    return HttpResponse.json(tenderCardFor(handlerState.tenderRoundState));
  }),
  http.post("/api/v1/tenders", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      { ...sampleTenderCard, id: 301, tender_number: body.tender_number },
      { status: 201 }
    );
  }),
  /**
   * Правка тендера (`useUpdateTender` — только `title`/`notes`). Отвечает
   * ПРИМЕНЁННОЙ карточкой, а не фикстурой как есть, — иначе тест не отличил бы
   * применённую правку от проигнорированной (находка ревью задачи 10).
   */
  http.patch("/api/v1/tenders/:id", async ({ params, request }) => {
    if (Number(params.id) !== sampleTenderCard.id) {
      return HttpResponse.json({ detail: "Тендер не найден." }, { status: 404 });
    }
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...tenderCardFor(handlerState.tenderRoundState), ...body });
  }),
  /**
   * Удаление тендера (`useDeleteTender`). Как и удаление участника, отказывает
   * ПОКА идёт импорт раунда (`handlerState.tenderDeleteOutcome`) — тот же код
   * отказа `active_import`, что у `DELETE .../participants/:pid` ниже, потому
   * что причина отказа буквально та же самая (спека §2.11).
   */
  http.delete("/api/v1/tenders/:id", () => {
    if (handlerState.tenderDeleteOutcome === "active") {
      return HttpResponse.json(
        {
          detail: {
            code: "active_import",
            message: "Импорт раунда выполняется.",
            job_id: 9102,
          },
        },
        { status: 409 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("/api/v1/tenders/:id/rounds", () => HttpResponse.json(sampleTenderCard, { status: 201 })),
  /**
   * Правка раунда (`useUpdateRound` — `label`/`held_on`, не `stage_no`).
   * Отвечает карточкой тендера с ИМЕННО этим раундом обновлённым — та же
   * логика «применённое, а не фиксированное», что у PATCH тендера выше.
   */
  http.patch("/api/v1/tenders/:id/rounds/:rid", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const card = tenderCardFor(handlerState.tenderRoundState);
    const roundId = Number(params.rid);
    return HttpResponse.json({
      ...card,
      rounds: card.rounds.map((round) => (round.id === roundId ? { ...round, ...body } : round)),
    });
  }),
  http.delete("/api/v1/tenders/:id/rounds/:rid", () => new HttpResponse(null, { status: 204 })),
  /**
   * История задний импорта раунда (`useRoundImportJobs`). Два элемента — как у
   * истории договора (`sampleImportJobs`) — чтобы компонент мог отрисовать и
   * ТЕКУЩЕЕ задание (`is_current: true`), и вытесненное им.
   */
  http.get("/api/v1/tenders/:id/rounds/:rid/import-jobs", ({ params }) => {
    const tenderId = Number(params.id);
    const roundId = Number(params.rid);
    const counters = {
      positions_total: 0,
      matched_cache: 0,
      matched_exact: 0,
      matched_nonposition: 0,
      to_review: 0,
    };
    const jobs: RoundImportJob[] = [
      {
        id: 9101,
        owner_type: "round",
        tender_id: tenderId,
        round_id: roundId,
        estimate_ids: [8001, 8002],
        estimates_created: 2,
        filename: "r1.xlsx",
        file_sha256: "b".repeat(64),
        status: "done",
        error_text: null,
        warnings: [],
        counters,
        created_at: "2026-06-02T09:00:00Z",
        started_at: "2026-06-02T09:00:01Z",
        finished_at: "2026-06-02T10:00:00Z",
        is_current: true,
      },
      {
        id: 9100,
        owner_type: "round",
        tender_id: tenderId,
        round_id: roundId,
        estimate_ids: [7999],
        estimates_created: 1,
        filename: "r1-первая-попытка.xlsx",
        file_sha256: "c".repeat(64),
        status: "done",
        error_text: null,
        warnings: [],
        counters,
        created_at: "2026-06-01T09:00:00Z",
        started_at: "2026-06-01T09:00:01Z",
        finished_at: "2026-06-01T09:30:00Z",
        is_current: false,
      },
    ];
    return HttpResponse.json(jobs);
  }),
  http.post("/api/v1/tenders/:id/rounds/:rid/upload", async ({ request, params }) => {
    // Тот же обход jsdom/undici multipart, что у загрузки сметы договора выше:
    // `request.formData()` падает под jsdom, тело читается текстом.
    const body = await request.text();
    handlerState.lastRoundUploadReplace = /name="replace"[\s\S]*?\btrue\b/.test(body);
    if (handlerState.uploadOutcome === "conflict") {
      // `active_import` побеждает `replace` — та же причина, что на бэкенде
      // отдаёт эту причину РАНЬШЕ проверки «есть ли что заменять» (§2.14).
      if (handlerState.roundUploadConflictCode === "active_import") {
        return HttpResponse.json(
          {
            detail: {
              code: "active_import",
              message: "Импорт этого раунда уже выполняется (задание 999, статус «parsing»).",
            },
          },
          { status: 409 }
        );
      }
      if (!handlerState.lastRoundUploadReplace) {
        return HttpResponse.json(
          {
            detail: {
              code: "replace_required",
              message:
                "Раунд уже загружен; для замены всех его смет повторите запрос с replace=true.",
            },
          },
          { status: 409 }
        );
      }
    }
    const job = {
      ...jobPayload(handlerState.jobStatuses[0] ?? "pending"),
      owner_type: "round",
      tender_id: Number(params.id),
      round_id: Number(params.rid),
      estimate_ids: [8001, 8002],
      estimates_created: 2,
    };
    // Задание-владелец "round" сметы ТЕКУЩЕЙ пары не несёт — только `estimate_ids`
    // (спека контура §2.13). Оставлять унаследованное поле смысла "contract"-пути
    // значило бы утверждать то, чего у раундового job нет.
    delete (job as { estimate_id?: unknown }).estimate_id;
    return HttpResponse.json(job, {
      status: handlerState.uploadOutcome === "idempotent" ? 200 : 202,
    });
  }),
  http.delete("/api/v1/tenders/:id/participants/:pid", ({ request }) => {
    const token = new URL(request.url).searchParams.get("confirmation_token");
    if (handlerState.participantDeleteOutcome === "active") {
      return HttpResponse.json(
        {
          detail: {
            code: "active_import",
            message: "Импорт раунда выполняется.",
            job_id: 9102,
          },
        },
        { status: 409 }
      );
    }
    if (!token || handlerState.participantDeleteOutcome === "stale") {
      // Без токена — ВСЕГДА preview (протокол §2.11): сервер отвечает 409 со
      // свежим `confirmation_token`, а не молча требует «пришлите токен».
      // "stale" отличим тем, что даже пришедший токен не совпал — сервер
      // отвечает 409 заново с ДРУГИМ токеном вместо 204.
      return HttpResponse.json(
        {
          detail: {
            code: "confirmation_required",
            message: "Удаление участника требует подтверждения состава.",
            rounds_count: 2,
            estimates_count: 2,
            positions_count: 1830,
            overrides_count: 3,
            confirmation_token: token ? "fresh-token" : "token-1",
          },
        },
        { status: 409 }
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),
  /**
   * Свод по этапам одного участника (спека 2026-08-27-stage-summary-design.md §2.16, Task 6).
   * Ответ выбирается по `handlerState.stageSummaryOutcome`.
   */
  http.get("/api/v1/tenders/:id/stage-summary", ({ request }) => {
    const offers = new URL(request.url).searchParams.getAll("offers").map(Number);
    const refuse = (status: number, code: string) =>
      HttpResponse.json(
        { detail: { code, message: `Отказ ${code}`, offers } },
        { status }
      );

    switch (handlerState.stageSummaryOutcome) {
      case "tender_not_found":
        return refuse(404, "tender_not_found");
      case "offer_not_found":
        return refuse(404, "offer_not_found");
      case "too_few_offers":
        return refuse(422, "too_few_offers");
      case "one_offer_per_round":
        return refuse(422, "one_offer_per_round");
      case "single_participant":
        return refuse(422, "single_participant");
      case "offer_has_no_estimate":
        return refuse(422, "offer_has_no_estimate");
      case "net":
        return HttpResponse.json(stageSummaryNet());
      case "unknown_vat":
        return HttpResponse.json(stageSummaryWithUnknownSecondColumn());
      case "track_non_positive":
        return HttpResponse.json({
          ...sampleStageSummary,
          track: { available: false, reason: "non_positive_total" },
        });
      case "track_no_comparable":
        return HttpResponse.json(stageSummaryAllUnknown());
      default:
        return HttpResponse.json(sampleStageSummary);
    }
  }),
  /**
   * Книга «Изменения КП» (спека 2026-09-16-tender-changes-export-design.md
   * §2.1, §2.11, план фичи, Task 5). Тот же приём блоб-ответа, что у выгрузок
   * §7.6 выше: настоящий media type xlsx на успехе, структурированный `422`
   * СЕРВЕРНЫМ текстом на отказе — им тест `useTenderChangesExport` проверяет,
   * что `toastReportError` достаёт сообщение из блоба, а не из `err.message`.
   */
  http.get("/api/v1/tenders/:id/changes-export", ({ params }) => {
    handlerState.lastChangesExportTenderId = Number(params.id);
    if (handlerState.changesExportOutcome === "no_comparable") {
      return HttpResponse.json(
        {
          detail: {
            code: "no_comparable_participants",
            message: "В тендере нет участников с двумя и более сметами — сравнивать нечего.",
          },
        },
        { status: 422 }
      );
    }
    return new HttpResponse(new Blob(["xlsx-stub"]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    });
  }),
  /**
   * Разложение статьи свода (спека 2026-08-30-position-drilldown-design.md
   * §2.11, Task 8): минимальная валидная фикстура, `work_category.id` — из
   * параметра пути. Отдельного `handlerState` под неё пока не заведено —
   * компоненты (Task 9-11) заведут переключатель исходов, когда он им
   * понадобится; здесь только транспорт.
   *
   * Путь на сегмент длиннее хендлера свода выше — совпасть друг с другом они
   * не могут (`:workCategoryId` — ровно один сегмент, не префикс), порядок
   * объявления на матчинг не влияет.
   */
  http.get("/api/v1/tenders/:tenderId/stage-summary/:workCategoryId", ({ params }) => {
    return HttpResponse.json(stagePositionsResponse(Number(params.workCategoryId)));
  }),
  http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", ({ params }) => {
    if (Number(params.rid) === 3001) {
      return HttpResponse.json(sampleRoundUnallocated);
    }
    return HttpResponse.json({ detail: { code: "round_has_no_offer_estimates", message: "Раунд или его сметы больше недоступны." } }, { status: 404 });
  }),
  http.put("/api/v1/tenders/:id/rounds/:rid/category-overrides", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.roundOverrideRequests.push({ method: "PUT", body });
    return HttpResponse.json({ chapters_updated: 3, additional_works_updated: 1, chapters_manual: 3 });
  }),
  http.delete("/api/v1/tenders/:id/rounds/:rid/category-overrides", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.roundOverrideRequests.push({ method: "DELETE", body });
    return HttpResponse.json({ chapters_updated: 3, additional_works_updated: 1, chapters_manual: 3 });
  }),

  // ---------------------------------------------------------------------
  //  Семьи и контексты (спека 2026-09-22-catalog-families-design.md §2.10,
  //  `backend/routers/semantic.py`) — восемнадцать маршрутов под `admin`.
  // ---------------------------------------------------------------------

  http.get("/api/v1/semantic/families", ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const unitId = url.searchParams.get("unit_id");
    const items = handlerState.workFamilies.filter((family) => {
      if (status && family.status !== status) return false;
      if (unitId && String(family.unit_id) !== unitId) return false;
      return true;
    });
    return HttpResponse.json({ items });
  }),

  http.post("/api/v1/semantic/families", async ({ request }) => {
    const body = (await request.json()) as { title: string; unit_name?: string | null; definition?: string | null };
    handlerState.lastCreateFamilyRequest = body;
    const id = handlerState.nextWorkFamilyId++;
    const family: WorkFamily = {
      id,
      title: body.title,
      unit_id: body.unit_name ? 11 : null,
      unit_code: body.unit_name ?? null,
      definition: body.definition ?? null,
      status: "draft",
      seed_key: null,
      created_by: 1,
      created_at: isoNow(),
      updated_at: isoNow(),
      activated_by: null,
      activated_at: null,
      archived_at: null,
      context_count: 0,
    };
    handlerState.workFamilies.push(family);
    return HttpResponse.json(family, { status: 201 });
  }),

  http.patch("/api/v1/semantic/families/:id", async ({ params, request }) => {
    const id = Number(params.id);
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastUpdateFamilyRequest = { id, body };
    const family = handlerState.workFamilies.find((f) => f.id === id);
    if (!family) {
      return HttpResponse.json({ detail: `семья ${id} не найдена` }, { status: 404 });
    }
    if ("unit_name" in body && family.context_count > 0) {
      return HttpResponse.json(
        {
          detail: {
            code: "unit_change_with_links",
            message: `у семьи ${id} есть привязанные контексты: ${family.context_count}`,
            family_id: id,
            count: family.context_count,
          },
        },
        { status: 409 }
      );
    }
    if (typeof body.title === "string") family.title = body.title;
    if ("definition" in body) family.definition = (body.definition as string | null) ?? null;
    if ("unit_name" in body) {
      const unitName = body.unit_name as string | null;
      family.unit_code = unitName;
      family.unit_id = unitName ? 11 : null;
    }
    family.updated_at = isoNow();
    return HttpResponse.json(family);
  }),

  http.post("/api/v1/semantic/families/:id/activate", ({ params }) => {
    const id = Number(params.id);
    const family = handlerState.workFamilies.find((f) => f.id === id);
    if (!family) {
      return HttpResponse.json({ detail: `семья ${id} не найдена` }, { status: 404 });
    }
    if (!family.definition || !family.definition.trim()) {
      return HttpResponse.json(
        {
          detail: {
            code: "activate_without_definition",
            message: `семья ${id} не может быть активирована без определения`,
            family_id: id,
          },
        },
        { status: 409 }
      );
    }
    family.status = "active";
    family.activated_by = 1;
    family.activated_at = isoNow();
    return HttpResponse.json(family);
  }),

  http.post("/api/v1/semantic/families/:id/archive", ({ params }) => {
    const id = Number(params.id);
    const family = handlerState.workFamilies.find((f) => f.id === id);
    if (!family) {
      return HttpResponse.json({ detail: `семья ${id} не найдена` }, { status: 404 });
    }
    if (family.context_count > 0) {
      return HttpResponse.json(
        {
          detail: {
            code: "archive_with_links",
            message: `у семьи ${id} есть привязанные контексты: ${family.context_count}`,
            family_id: id,
            count: family.context_count,
          },
        },
        { status: 409 }
      );
    }
    family.status = "archived";
    family.archived_at = isoNow();
    return HttpResponse.json(family);
  }),

  http.post("/api/v1/semantic/families/:id/merge", async ({ params, request }) => {
    const id = Number(params.id);
    const body = (await request.json()) as { target_family_id: number };
    handlerState.lastMergeFamiliesRequest = { id, targetFamilyId: body.target_family_id };
    const source = handlerState.workFamilies.find((f) => f.id === id);
    if (source) source.status = "archived";
    const target = handlerState.workFamilies.find((f) => f.id === body.target_family_id);
    if (!target) {
      return HttpResponse.json({ detail: `семья ${body.target_family_id} не найдена` }, { status: 404 });
    }
    // Ответ — строка ЦЕЛЕВОЙ семьи (форма списка), как у остальных мутаций.
    target.context_count += 1;
    return HttpResponse.json(target);
  }),

  http.get("/api/v1/semantic/contexts", ({ request }) => {
    const url = new URL(request.url);
    const params = url.searchParams;
    const catalogQuery = (params.get("catalog_query") ?? "").trim().toLowerCase();
    const workCategoryId = params.get("work_category_id");
    const semanticKind = params.get("semantic_kind");
    const nameRole = params.get("name_role");
    const semanticState = params.get("semantic_state");
    const hasStale = params.get("has_stale_members");
    const hasConflicting = params.get("has_conflicting_members");
    const hasNoMembers = params.get("has_no_members");
    const limit = Number(params.get("limit") ?? 50);
    const offset = Number(params.get("offset") ?? 0);

    const filtered = handlerState.semanticContexts.filter((c) => {
      if (catalogQuery && !c.standard_job_title.toLowerCase().includes(catalogQuery)) return false;
      if (workCategoryId && String(c.work_category_id) !== workCategoryId) return false;
      if (semanticKind && c.semantic_kind !== semanticKind) return false;
      if (nameRole && c.name_role !== nameRole) return false;
      if (semanticState && c.semantic_state !== semanticState) return false;
      if (hasStale !== null && c.hasStaleMembers !== (hasStale === "true")) return false;
      if (hasConflicting !== null && c.hasConflictingMembers !== (hasConflicting === "true")) return false;
      if (hasNoMembers !== null && c.hasNoMembers !== (hasNoMembers === "true")) return false;
      return true;
    });

    const page = filtered.slice(offset, offset + limit).map(toContextRow);
    return HttpResponse.json({ items: page, total: filtered.length, limit, offset });
  }),

  http.get("/api/v1/semantic/contexts/:id", ({ params }) => {
    const id = Number(params.id);
    const context = handlerState.semanticContexts.find((c) => c.id === id);
    if (!context) {
      return HttpResponse.json({ detail: `Контекст ${id} не найден.` }, { status: 404 });
    }
    return HttpResponse.json(toContextCard(context));
  }),

  http.post("/api/v1/semantic/contexts/:id/kind", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastConfirmKindRequest = { contextId, body };
    const context = handlerState.semanticContexts.find((c) => c.id === contextId);
    if (!context) return HttpResponse.json({ detail: `Контекст ${contextId} не найден.` }, { status: 404 });
    if (context.semantic_state === "NOT_APPLICABLE") {
      return HttpResponse.json(
        { detail: { code: "context_not_applicable", message: "контекст неприменим", context_id: contextId } },
        { status: 409 }
      );
    }
    if (body.unconfirm === true) {
      context.semantic_kind_source = "rule";
      context.semantic_kind_by = null;
      context.semantic_kind_at = isoNow();
      context.semantic_state = "SUGGESTED";
      return HttpResponse.json(toContextCard(context));
    }
    if (typeof body.kind === "string") context.semantic_kind = body.kind as ContextCardData["semantic_kind"];
    context.semantic_kind_source = "manual";
    context.semantic_kind_by = 1;
    context.semantic_kind_at = isoNow();
    context.semantic_state = "CONFIRMED";
    return HttpResponse.json(toContextCard(context));
  }),

  http.post("/api/v1/semantic/contexts/:id/name-role", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastSetNameRoleRequest = { contextId, body };
    const context = handlerState.semanticContexts.find((c) => c.id === contextId);
    if (!context) return HttpResponse.json({ detail: `Контекст ${contextId} не найден.` }, { status: 404 });
    context.name_role = body.role as ContextCardData["name_role"];
    context.name_role_source = "manual";
    context.name_role_by = 1;
    context.name_role_at = isoNow();
    return HttpResponse.json(toContextCard(context));
  }),

  http.post("/api/v1/semantic/contexts/:id/family", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as { family_id: number | null };
    handlerState.lastAssignFamilyRequest = { contextId, body };
    const context = handlerState.semanticContexts.find((c) => c.id === contextId);
    if (!context) return HttpResponse.json({ detail: `Контекст ${contextId} не найден.` }, { status: 404 });
    if (body.family_id === null) {
      context.work_family_id = null;
      context.family_title = null;
      context.family_source = null;
      context.family_by = null;
      context.family_at = null;
    } else {
      const family = handlerState.workFamilies.find((f) => f.id === body.family_id);
      if (!family) {
        return HttpResponse.json(
          { detail: { code: "family_not_found", message: `семья ${body.family_id} не найдена`, family_id: body.family_id } },
          { status: 404 }
        );
      }
      context.work_family_id = family.id;
      context.family_title = family.title;
      context.family_source = "manual";
      context.family_by = 1;
      context.family_at = isoNow();
    }
    return HttpResponse.json(toContextCard(context));
  }),

  http.post("/api/v1/semantic/contexts/:id/split", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as { position_item_ids: number[]; rule?: unknown };
    handlerState.lastSplitContextRequest = { contextId, body };
    return HttpResponse.json({
      new_context_id: 9999,
      moved_members: body.position_item_ids.length,
      rule_id: body.rule ? 1 : null,
      default_replaced: !body.rule,
    });
  }),

  http.post("/api/v1/semantic/contexts/:id/merge", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as { target_context_id: number };
    handlerState.lastMergeContextRequest = { contextId, targetContextId: body.target_context_id };
    return HttpResponse.json({ source_context_id: contextId, target_context_id: body.target_context_id, moved_members: 1 });
  }),

  http.post("/api/v1/semantic/contexts/:id/archive", async ({ params, request }) => {
    const contextId = Number(params.id);
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastArchiveContextRequest = { contextId, body };
    const context = handlerState.semanticContexts.find((c) => c.id === contextId);
    if (!context) return HttpResponse.json({ detail: `Контекст ${contextId} не найден.` }, { status: 404 });
    if (context.member_count > 0) {
      return HttpResponse.json(
        { detail: { code: "context_not_empty", message: "в контексте есть членства", context_id: contextId, member_count: context.member_count } },
        { status: 409 }
      );
    }
    context.archived_at = isoNow();
    return HttpResponse.json({ context_id: contextId, archived: true });
  }),

  http.post("/api/v1/semantic/members/move", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastMoveMembersRequest = body;
    // Настоящий сервис допускает на этом маршруте только "manual" — оператор
    // не вправе подписать перенос чужой причиной журнала (review_merge,
    // stale_accepted — они пишутся автоматом другими операциями).
    if (body.reason !== "manual") {
      return HttpResponse.json(
        {
          detail: {
            code: "invalid_reason",
            message: `недопустимая причина переноса: ${JSON.stringify(body.reason)}`,
            reason: body.reason,
          },
        },
        { status: 422 }
      );
    }
    const ids = body.position_item_ids as number[];
    return HttpResponse.json({ target_context_id: body.target_context_id, moved_members: ids.length });
  }),

  http.get("/api/v1/semantic/members/:id/transfer-proposal", ({ params }) => {
    const positionItemId = Number(params.id);
    if (positionItemId === STALE_POSITION_ITEM_ID) {
      return HttpResponse.json({
        position_item_id: positionItemId,
        proposal: {
          position_item_id: positionItemId,
          current_context_id: 602,
          proposed_bucket_id: 750,
          proposed_context_id: 751,
          effective_category_id: 88,
        },
      });
    }
    return HttpResponse.json({ position_item_id: positionItemId, proposal: null });
  }),

  http.post("/api/v1/semantic/members/:id/transfer", async ({ params, request }) => {
    const positionItemId = Number(params.id);
    const body = (await request.json()) as Record<string, unknown>;
    handlerState.lastAcceptTransferRequest = { positionItemId, body };
    if (positionItemId !== STALE_POSITION_ITEM_ID) {
      return HttpResponse.json(
        { detail: { code: "not_stale", message: `членство ${positionItemId} не устарело`, position_item_id: positionItemId } },
        { status: 409 }
      );
    }
    return HttpResponse.json({ position_item_id: positionItemId, moved_members: 1 });
  }),

  http.post("/api/v1/semantic/members/accept-target-decision", async ({ request }) => {
    const body = (await request.json()) as { position_item_ids: number[] };
    handlerState.lastAcceptTargetDecisionRequest = body.position_item_ids;
    const allConflicted = body.position_item_ids.every((id) => CONFLICT_POSITION_ITEM_IDS.includes(id));
    if (!allConflicted) {
      return HttpResponse.json(
        { detail: { code: "not_conflicted", message: "членство не в конфликте", position_item_ids: body.position_item_ids } },
        { status: 409 }
      );
    }
    return HttpResponse.json({ updated_members: body.position_item_ids.length });
  }),
];

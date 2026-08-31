import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import { adminApi } from "./api/admin";
import { analyticsApi, comparisonApi, reportsApi, settingsApi } from "./api/analytics";
import {
  inflationSeriesApi,
  catalogApi,
  contractsApi,
  estimatesApi,
  rateStandardsApi,
  referencesApi,
  reviewApi,
  tendersApi,
  type ContractListParams,
} from "./api/domain";
import { jobRefetchInterval } from "./jobPolling";
import { qk } from "./queryKeys";

import type { ID } from "@/types/common";
import type { AdminUserCreateInput, AdminUserUpdateInput } from "@/types/admin";
import type {
  InflationSeriesInput,
  InflationSeriesPatch,
  BankComparisonParams,
  ClearCategoryOverrideInput,
  ComparisonParams,
  ContractInput,
  ContractorInput,
  Decimal,
  ManualKind,
  ObjectInput,
  RateClassInput,
  RateStandardInput,
  RateStandardParams,
  ReapproveInput,
  MatrixParams,
  ReviewQueueParams,
  RoundInput,
  SetCategoryOverrideInput,
  TenderInput,
} from "@/types/domain";

/** Элемент `detail` при ошибке валидации Pydantic. */
interface ValidationIssue {
  msg?: string;
  loc?: (string | number)[];
}

/**
 * Достаёт человекочитаемую причину отказа из ответа FastAPI.
 *
 * `detail` бывает **двух видов**, и это не мелочь. Доменные отказы
 * (`HTTPException`) кладут туда строку. А ошибки валидации Pydantic — **список**
 * объектов, и сообщение лежит в `msg` каждого, с приставкой «Value error, ».
 *
 * Пока разбиралась только строка, все тексты, написанные в валидаторах, до
 * человека не доходили: он видел «Request failed with status code 422». А это
 * ровно те подсказки, которые нужны в момент ошибки — «сумму передавайте
 * строкой», «поле не может быть null», «в пакете не больше 200 строк».
 */
/**
 * Кодированный доменный отказ: `detail` объектом (спека инфляции §2.12).
 *
 * ТРЕТЬЯ форма `detail`, и без её разбора тост печатал бы `[object Object]`:
 * ветвление по типу здесь не украшение, а условие того, что человек вообще
 * увидит причину.
 */
interface CodedDetail {
  code: string;
  message: string;
  [key: string]: unknown;
}

function codedDetail(err: unknown): CodedDetail | undefined {
  const detail = (err as AxiosError<{ detail?: unknown }>)?.response?.data?.detail;
  if (
    detail !== null &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    typeof (detail as CodedDetail).code === "string" &&
    typeof (detail as CodedDetail).message === "string"
  ) {
    return detail as CodedDetail;
  }
  return undefined;
}

export function apiErrorDetail(err: unknown): string | undefined {
  const detail = (err as AxiosError<{ detail?: string | ValidationIssue[] }>)?.response?.data
    ?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((issue) => (issue?.msg ?? "").replace(/^Value error,\s*/, "").trim())
      .filter(Boolean);
    if (messages.length > 0) return messages.join("; ");
  }

  const coded = codedDetail(err);
  if (coded) return coded.message;

  return undefined;
}

/**
 * Машинный код отказа — по нему экран выбирает ПОВЕДЕНИЕ, а не только текст.
 *
 * Разные коды дают разные баннеры: у `missing_inflation_years` есть кнопка
 * «Заполнить недостающие годы», у `amendment_date_missing` кнопки НЕТ — правкой
 * ряда это не лечится, и предлагать её значило бы звать человека делать работу,
 * которая ничего не исправит (§2.9).
 */
export function apiErrorCode(err: unknown): string | undefined {
  return codedDetail(err)?.code;
}

/**
 * Контекст отказа — ключи лежат РЯДОМ с `code` и `message`, а не вложенным узлом
 * (§2.12), поэтому весь объект и есть контекст.
 */
export function apiErrorContext<T>(err: unknown): T | undefined {
  const coded = codedDetail(err);
  return coded === undefined ? undefined : (coded as unknown as T);
}

export function apiErrorStatus(err: unknown): number | undefined {
  return (err as AxiosError)?.response?.status;
}

export function toastApiError(err: unknown) {
  const detail = apiErrorDetail(err);
  toast.error(detail ?? (err instanceof Error ? err.message : "Произошла ошибка"));
}

// ========== Admin (пользователи) ==========

export function useAdminUsers(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: qk.admin.users(params?.q, params?.page, params?.page_size),
    queryFn: () => adminApi.listUsers(params),
  });
}

export function useCreateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AdminUserCreateInput) => adminApi.createUser(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: toastApiError,
  });
}

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, input }: { userId: ID; input: AdminUserUpdateInput }) =>
      adminApi.updateUser(userId, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Пользователь обновлён");
    },
    onError: toastApiError,
  });
}

export function useResetUserPassword() {
  // Намеренно без инвалидации — возвращает plaintext-пароль, который страница
  // показывает в диалоге. Тост-напоминание вызывается на странице после показа.
  return useMutation({
    mutationFn: (userId: ID) => adminApi.resetPassword(userId),
  });
}

// ========== Справочники (§7.1, §7.3) ==========

export function useRateClasses() {
  return useQuery({ queryKey: qk.rateClasses.list(), queryFn: referencesApi.listRateClasses });
}

export function useUnits() {
  // Справочник единиц меняется редко — держим дольше обычного staleTime.
  return useQuery({
    queryKey: qk.units.all,
    queryFn: referencesApi.listUnits,
    staleTime: 30 * 60_000,
  });
}

export function useCreateRateClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: RateClassInput) => referencesApi.createRateClass(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      toast.success("Класс объектов создан");
    },
    onError: toastApiError,
  });
}

export function useUpdateRateClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<RateClassInput> }) =>
      referencesApi.updateRateClass(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      // Название класса денормализовано в шапку паспорта (`rate_class_title`).
      qc.invalidateQueries({ queryKey: qk.passport.all });
      toast.success("Класс объектов обновлён");
    },
    onError: toastApiError,
  });
}

export function useDeleteRateClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => referencesApi.deleteRateClass(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      toast.success("Класс объектов удалён");
    },
    onError: toastApiError,
  });
}

export function useObjects(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: qk.objects.list(params),
    queryFn: () => referencesApi.listObjects(params),
  });
}

/**
 * Один объект (спека §2.9) — отдельным запросом, а не полями, подмешанными в
 * карточку договора: в карточке уже есть `rate_class_id` (снимок договора), и
 * класс объекта рядом с ним дал бы два поля с одним именем и разным смыслом.
 *
 * `id` необязателен, и `enabled` обязателен вместе с ним: вызывающая сторона
 * узнаёт идентификатор объекта только из загруженной карточки договора, а хуки
 * вызываются до ранних `return`. Без `enabled` запрос уходил бы по подставному
 * `0` при каждом открытии карточки и штатно получал `404` — лишний ошибочный
 * запрос и мусор в журналах. Форма та же, что у `useContract` ниже.
 */
export function useObject(id: number | undefined) {
  return useQuery({
    queryKey: qk.objects.one(id ?? 0),
    queryFn: () => referencesApi.getObject(id as number),
    enabled: id !== undefined,
  });
}

export function useCreateObject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ObjectInput) => referencesApi.createObject(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.objects.all });
      // Счётчик объектов у класса тоже изменился.
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
    },
    onError: toastApiError,
  });
}

export function useUpdateObject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<ObjectInput> }) =>
      referencesApi.updateObject(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.objects.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      // Название объекта денормализовано в договоры, паспорт и колонки матрицы
      // (спека §2.11). Корень, а не карточка: у объекта может быть несколько
      // договоров, и название лежит в каждом.
      qc.invalidateQueries({ queryKey: qk.contracts.all });
      qc.invalidateQueries({ queryKey: qk.passport.all });
      qc.invalidateQueries({ queryKey: qk.matrix.all });
    },
    onError: toastApiError,
  });
}

export function useContractors(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: qk.contractors.list(params),
    queryFn: () => referencesApi.listContractors(params),
  });
}

export function useCreateContractor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ContractorInput) => referencesApi.createContractor(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contractors.all });
    },
    onError: toastApiError,
  });
}

export function useUpdateContractor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<ContractorInput> }) =>
      referencesApi.updateContractor(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      // Название подрядчика денормализовано в шапку паспорта (`contractor_title`).
      qc.invalidateQueries({ queryKey: qk.passport.all });
    },
    onError: toastApiError,
  });
}

// ========== Договоры (§7.1) ==========

export function useContracts(params?: ContractListParams) {
  return useQuery({
    queryKey: qk.contracts.list(params),
    queryFn: () => contractsApi.list(params),
  });
}

export function useContract(id: number | undefined) {
  return useQuery({
    queryKey: qk.contracts.card(id ?? 0),
    queryFn: () => contractsApi.get(id as number),
    enabled: id !== undefined,
  });
}

export function useContractImportJobs(id: number | undefined) {
  return useQuery({
    queryKey: qk.contracts.importJobs(id ?? 0),
    queryFn: () => contractsApi.importJobs(id as number),
    enabled: id !== undefined,
  });
}

export function useCreateContract() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ContractInput) => contractsApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contracts.all });
      qc.invalidateQueries({ queryKey: qk.objects.all });
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      // Новый договор меняет `object_contracts_count` у ВСЕХ паспортов этого
      // объекта: бейдж «у объекта N договоров» предупреждает, что ₽/м² делит
      // разные деньги на одну площадь (спека Ф6 §2.6, обязательство 3 Ф5).
      // Без инвалидации он ещё минуту показывал бы прежнее N.
      qc.invalidateQueries({ queryKey: qk.passport.all });
      toast.success("Договор создан");
    },
    onError: toastApiError,
  });
}

export function useUpdateContract() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<ContractInput> }) =>
      contractsApi.update(id, input),
    onSuccess: (contract) => {
      qc.invalidateQueries({ queryKey: qk.contracts.all });
      // Шапка паспорта проекта денормализует реквизиты договора целиком —
      // номер, подписанта, дату, класс и три коммерческих условия (спека Ф6
      // §2.6). При `staleTime: 60_000` без этой инвалидации правка реквизитов
      // не доезжала бы до уже открытого паспорта целую минуту, и он был бы
      // «свежим» по мнению React Query и устаревшим по факту.
      qc.invalidateQueries({ queryKey: qk.passport.all });
      toast.success(`Договор ${contract.contract_number} обновлён`);
    },
    onError: toastApiError,
  });
}

export function useDeleteContract() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => contractsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.contracts.all });
      // Удаление бьёт по паспорту дважды: у остальных договоров объекта
      // меняется `object_contracts_count`, а паспорт САМОГО удалённого договора
      // остаётся в кэше — и без инвалидации к нему можно вернуться назад и
      // увидеть документ по договору, которого уже нет.
      qc.invalidateQueries({ queryKey: qk.passport.all });
      // Каскад уносит сметы и позиции, поэтому устаревает всё, где договор
      // виден или посчитан (спека §2.7). `qk.contracts.all` накрывает и историю
      // загрузок договора; поллинг задания живёт в ОТДЕЛЬНОМ пространстве
      // `qk.importJobs`, под префикс договоров он не попадает.
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.matrix.all });
      qc.invalidateQueries({ queryKey: qk.dashboard.all });
      // Позиции ушли — фактическая очередь Review изменилась.
      qc.invalidateQueries({ queryKey: qk.review.all });
      // Счётчики договоров в справочниках: ровно их инвалидирует
      // `useCreateContract`, и несимметричность была бы дефектом.
      qc.invalidateQueries({ queryKey: qk.objects.all });
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      toast.success("Договор удалён");
    },
    onError: toastApiError,
  });
}

// ========== Загрузка сметы и поллинг задания (§7.1) ==========

/**
 * Загрузка сметы.
 *
 * **Без общего тоста на ошибку.** `409` здесь — не сбой, а развилка «смета уже
 * загружена, нужна замена» (§5, правило 2), и экран показывает её диалогом, а не
 * красным тостом. Поэтому обработку ошибок целиком ведёт компонент.
 */
export function useUploadEstimate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: estimatesApi.upload,
    onSuccess: (job) => {
      // `contract_id` живёт только у задания-владельца "contract" (§2.13); проверка
      // САМОГО поля рядом с `owner_type` — не только по смыслу, но и чтобы TS
      // сузил `number | undefined` до `number` для инвалидации ниже.
      if (job.owner_type !== "contract" || job.contract_id === undefined) return;
      qc.invalidateQueries({ queryKey: qk.contracts.card(job.contract_id) });
      qc.invalidateQueries({ queryKey: qk.contracts.importJobs(job.contract_id) });
    },
  });
}

/**
 * Скачивание исходного XLSX задания с разбором статуса (§5, §8).
 *
 * Два отказа обязаны звучать по-разному, и это не косметика: 404 значит «такого
 * задания нет», а 410 — «задание есть, это аудит, но файл уже удалён ретенцией».
 * Второе — нормальный ход событий, а не поломка, и человек должен это понять.
 */
export function useDownloadJobFile() {
  return useMutation({
    mutationFn: async ({ jobId, filename }: { jobId: number; filename: string }) => {
      const blob = await estimatesApi.downloadFile(jobId);
      // Клик по временной ссылке — единственный способ отдать blob на диск из
      // браузера. В jsdom createObjectURL отсутствует, поэтому шаг необязательный:
      // тесты проверяют разбор статусов, а не работу файлового диалога.
      if (typeof URL.createObjectURL === "function") {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
      }
      return blob;
    },
    onError: (error) => {
      const status = apiErrorStatus(error);
      if (status === 410) {
        toast.error(
          "Файл удалён при очистке хранилища: запись о загрузке сохранена как аудит, " +
            "но исходник уже недоступен."
        );
        return;
      }
      if (status === 404) {
        toast.error("Задание импорта не найдено.");
        return;
      }
      toastApiError(error);
    },
  });
}

/** Чей job поллит {@link useImportJob} — ровно один из двух, никогда оба сразу. */
export interface ImportJobOwnerRef {
  contractId?: number;
  tenderId?: number;
  /** Только вместе с `tenderId` — какой раунд инвалидировать в истории загрузок. */
  roundId?: number;
}

/**
 * Поллинг задания импорта. Прекращается на `done`/`error`.
 *
 * `ownerRef` — чтобы при завершении обновить владельца: смета (или сметы)
 * появляются в БД позже ответа `202`, и без инвалидации экран показывал бы
 * «смет нет» у успешного задания. Два владельца задания (спека контура §2.13)
 * инвалидируют РАЗНОЕ: договор — свою карточку и историю загрузок (поведение
 * не менялось задачей 10 ни на строку); раунд тендера — карточку тендера,
 * которая несёт решётку и baseline (`ImportJobPanel`, задача 11, читает
 * именно её, чтобы обновить грид после загрузки раунда), и историю загрузок
 * ИМЕННО этого раунда (`qk.tenders.roundJobs`, требует `ownerRef.roundId`) —
 * без неё таблица истории под гридом (та же карточка тендера) держит job «в
 * процессе» без бейджа «актуальный» неопределённо долго: `staleTime` истории —
 * минута, а загрузочный `202` инвалидирует её РАНЬШЕ, пока job ещё pending, не
 * в момент перехода в `done`.
 *
 * **Оба терминальных статуса, не только `done`** (находка внешнего ревью
 * PR #33, finding 1). Загрузочный `202` кладёт `pending`-job в карточку/
 * историю владельца сразу; если импорт затем падает в `error`, и владелец
 * инвалидируется ТОЛЬКО на `done`, ничто больше не обновит эти кэши —
 * `staleTime` карточки/истории минута, рефетча по фокусу нет, и job навсегда
 * остаётся «в процессе» на экране, хотя работа давно закончилась отказом.
 * Владельца (карточку и историю) освежаем на ОБОИХ терминальных статусах;
 * `review.all`/`passport.all` — ТОЛЬКО на `done`: провалившийся импорт не
 * создаёт сметы, и смотреть/пересчитывать нечего.
 */
export function useImportJob(jobId: number | undefined, ownerRef?: ImportJobOwnerRef) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: qk.importJobs.one(jobId ?? 0),
    queryFn: async () => {
      const job = await estimatesApi.getJob(jobId as number);
      const terminal = job.status === "done" || job.status === "error";
      if (terminal) {
        if (ownerRef?.contractId !== undefined) {
          qc.invalidateQueries({ queryKey: qk.contracts.card(ownerRef.contractId) });
          qc.invalidateQueries({ queryKey: qk.contracts.importJobs(ownerRef.contractId) });
        }
        if (ownerRef?.tenderId !== undefined) {
          qc.invalidateQueries({ queryKey: qk.tenders.card(ownerRef.tenderId) });
          if (ownerRef.roundId !== undefined) {
            qc.invalidateQueries({ queryKey: qk.tenders.roundJobs(ownerRef.tenderId, ownerRef.roundId) });
          }
        }
      }
      if (job.status === "done") {
        if (ownerRef?.contractId !== undefined) {
          qc.invalidateQueries({ queryKey: qk.review.all });
          // Успешный импорт МЕНЯЕТ содержимое паспорта целиком: смета появляется
          // или заменяется, а с ней все суммы по статьям. Без этой инвалидации
          // паспорт, открытый до загрузки, ещё минуту показывал бы «смета не
          // загружена» либо суммы прежней сметы (спека Ф6 §2.4).
          qc.invalidateQueries({ queryKey: qk.passport.all });
        }
        if (ownerRef?.tenderId !== undefined) {
          qc.invalidateQueries({ queryKey: qk.review.all });
          // Ревью PR #35, finding 2: раунд после ЗАМЕНЫ переиспользует ТУ ЖЕ
          // строку `Offer` (`services/round_import.py`), значит id
          // предложений не меняются и точечные ключи свода/разложения
          // (построенные из этих id) остаются прежними — без инвалидации
          // `useStageSummary` рефетчил бы по своему обычному `staleTime`, а
          // `useStagePositions` с `staleTime: Infinity` (§6.3) не обновился бы
          // НИКОГДА, и открытое разложение показывало бы старые деньги рядом
          // со свежим сводом до перезагрузки страницы. Префиксные ключи —
          // потому что здесь неизвестно, какие `offerIds` (и для разложения —
          // какой `workCategoryId`) сейчас выбраны на экране.
          qc.invalidateQueries({ queryKey: qk.tenders.stageSummaryForTender(ownerRef.tenderId) });
          qc.invalidateQueries({ queryKey: qk.tenders.stagePositionsForTender(ownerRef.tenderId) });
        }
      }
      return job;
    },
    enabled: jobId !== undefined,
    refetchInterval: (query) => jobRefetchInterval(query.state.data),
  });
}

// ========== Ручной матчинг (§7.2) ==========

export function useReviewQueue(params?: ReviewQueueParams) {
  return useQuery({
    queryKey: qk.review.queue(params),
    queryFn: () => reviewApi.queue(params),
  });
}

export function useMergeTargets(q: string, unitId?: number) {
  return useQuery({
    queryKey: qk.review.targets(q, unitId),
    queryFn: () => reviewApi.targets(q, unitId),
    // Пустой запрос сервер отклонил бы 422 — не спрашиваем.
    enabled: q.trim().length > 0,
  });
}

export function useCatalogSearch(q: string, unitId?: number) {
  return useQuery({
    queryKey: qk.catalog.search(q, unitId),
    queryFn: () => catalogApi.search(q, unitId),
    enabled: q.trim().length > 0,
  });
}

/** Слияние меняет и очередь, и каталог, и нормативы могли переехать на цель. */
function invalidateAfterReviewDecision(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: qk.review.all });
  qc.invalidateQueries({ queryKey: qk.catalog.all });
}

export function useMergeReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ toReviewId, targetId }: { toReviewId: number; targetId: number }) =>
      reviewApi.merge(toReviewId, targetId),
    onSuccess: (result) => {
      invalidateAfterReviewDecision(qc);
      toast.success(
        `Слито с «${result.target.standard_job_title}»: перенесено позиций — ${result.moved_positions}`
      );
    },
    onError: toastApiError,
  });
}

export function useSetReviewKind() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ toReviewId, kind }: { toReviewId: number; kind: ManualKind }) =>
      reviewApi.setKind(toReviewId, kind),
    onSuccess: () => {
      invalidateAfterReviewDecision(qc);
    },
    onError: toastApiError,
  });
}

export function useBatchReviewKind() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, kind }: { ids: number[]; kind: ManualKind }) =>
      reviewApi.batchKind(ids, kind),
    onSuccess: (result) => {
      invalidateAfterReviewDecision(qc);
      // Пропущенные строки — не сбой пакета, а разошедшееся состояние: их
      // разобрал другой оператор. Говорим об этом отдельно от успеха.
      toast.success(`Размечено строк: ${result.applied.length}`);
      if (result.skipped.length > 0) {
        toast.warning(
          `Пропущено: ${result.skipped.length}. ${result.skipped[0]?.reason ?? ""}`
        );
      }
    },
    onError: toastApiError,
  });
}

// ========== Нормативы (§7.3) ==========

export function useRateStandards(params?: RateStandardParams) {
  return useQuery({
    queryKey: qk.rateStandards.list(params),
    queryFn: () => rateStandardsApi.list(params),
  });
}

export function useCreateRateStandard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: RateStandardInput) => rateStandardsApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateStandards.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      toast.success("Норматив создан");
    },
    onError: toastApiError,
  });
}

export function useUpdateRateStandard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<RateStandardInput> }) =>
      rateStandardsApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateStandards.all });
      toast.success("Норматив исправлен");
    },
    onError: toastApiError,
  });
}

export function useReapproveRateStandard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: ReapproveInput }) =>
      rateStandardsApi.reapprove(id, input),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: qk.rateStandards.all });
      toast.success(
        `Переутверждено с ${result.current.valid_from}; прежний период закрыт ${result.previous.valid_to}`
      );
    },
    onError: toastApiError,
  });
}

export function useDeleteRateStandard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => rateStandardsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.rateStandards.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
      toast.success("Норматив удалён");
    },
    onError: toastApiError,
  });
}

// ---------------------------------------------------------------------------
//  Аналитика фазы 6: настройки, паспорт, матрица (§6, §7.4–§7.5)
// ---------------------------------------------------------------------------

export function useAppSettings() {
  return useQuery({
    queryKey: qk.settings.all,
    queryFn: () => settingsApi.get(),
  });
}

export function useUpdateAppSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (passportTopN: number) => settingsApi.update(passportTopN),
    onSuccess: (settings) => {
      qc.invalidateQueries({ queryKey: qk.settings.all });
      // Хвост фазы 6: паспорт объекта зависел от N, и эта инвалидация держала его
      // перерисовку. Паспорт проекта (Ф6 фазы 7) от `passport_top_n` не зависит —
      // после задачи 11 эта строка не обновляет ничего значимого, но и не вредит
      // (лишний рефетч по корню, которого никто не показывает), поэтому не снята.
      qc.invalidateQueries({ queryKey: qk.passport.all });
      toast.success(`Ключевых расценок в паспорте: ${settings.passport_top_n}`);
    },
    onError: toastApiError,
  });
}

/**
 * Паспорт проекта по статьям классификатора (Ф6 фазы 7, спека §2.6, задача 6).
 *
 * Форма та же, что у `useContract`/`useObject` выше: `contractId` необязателен
 * (карточка договора грузится первой), `enabled` держит запрос под замком до
 * появления идентификатора — без него ушёл бы `GET /project-passport/0` при
 * каждом первом рендере со штатным 404 (тот же класс дефекта, что P3 у F5).
 *
 * Ключ — `qk.passport.project`, под тем же корнем `qk.passport.all` (см.
 * комментарий у `qk.passport.project`): инвалидация `useUpdateObject`/
 * `useUpdateAppSettings` уже накрывает паспорт проекта, без правки списка
 * инвалидации.
 */
/** Основной таб стартового дашборда (спека 2026-08-16 §2.8) — читает и `member`. */
export function useDashboard() {
  return useQuery({
    queryKey: qk.dashboard.main(),
    queryFn: analyticsApi.dashboard,
  });
}

/**
 * Диагностики второго таба — **под условием**, а не безусловно.
 *
 * `enabled` здесь не оптимизация. Дорогой расчёт как раз и не запустится:
 * `require_admin` — зависимость, она отклоняет запрос ДО обработчика, и
 * `member` получил бы `403`, ничего не посчитав. Причина другая: безусловный
 * хук штатно генерирует запрещённые запросы, засоряет журнал сервера отказами и
 * делает право видимым только на сервере, тогда как §2.8 разводит эндпоинты,
 * чтобы клиент И НЕ ПЫТАЛСЯ.
 */
export function useDashboardAttention(enabled: boolean) {
  return useQuery({
    queryKey: qk.dashboard.attention(),
    queryFn: analyticsApi.dashboardAttention,
    enabled,
  });
}

export function useProjectPassport(contractId: number | undefined) {
  return useQuery({
    queryKey: qk.passport.project(contractId ?? 0),
    queryFn: () => analyticsApi.projectPassport(contractId as number),
    enabled: contractId !== undefined,
  });
}

/**
 * Назначить статью разделу вручную (спека разноса).
 *
 * `contractId` — отдельное поле входа, хотя эндпоинту оно не нужно: снести
 * нужно кэш запроса паспорта, а он ключуется договором, не сметой (спека
 * разноса, правило про инвалидацию).
 */
export function useSetCategoryOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SetCategoryOverrideInput) => analyticsApi.setCategoryOverride(input),
    onSuccess: (_data, input) => {
      qc.invalidateQueries({ queryKey: qk.passport.project(input.contractId) });
      // Карточка договора несёт свой СОБСТВЕННЫЙ счёт `category_overrides_count`
      // на смету (задача 6, `EstimateUploadPanel`), а не производную от паспорта —
      // без этой инвалидации она оставалась бы устаревшей для ЛЮБОГО потребителя
      // `qk.contracts.card`, не только для формы замены (находка ревью PR #16).
      qc.invalidateQueries({ queryKey: qk.contracts.card(input.contractId) });
    },
    onError: toastApiError,
  });
}

/** Снять ручное решение о статье — см. {@link useSetCategoryOverride}. */
export function useClearCategoryOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClearCategoryOverrideInput) => analyticsApi.clearCategoryOverride(input),
    onSuccess: (_data, input) => {
      qc.invalidateQueries({ queryKey: qk.passport.project(input.contractId) });
      qc.invalidateQueries({ queryKey: qk.contracts.card(input.contractId) });
    },
    onError: toastApiError,
  });
}

/**
 * Правка ставок НДС сметы (спека пересчёта §2.7, задача 9).
 *
 * `contractId` — отдельное поле входа, хотя эндпоинту оно не нужно: правка
 * меняет паспорт ДОГОВОРА (все деньги пересчитываются в ставке показа), а
 * паспорт ключуется договором, не сметой — тот же приём, что у
 * `useSetCategoryOverride`/`useClearCategoryOverride` выше.
 */
export interface SetEstimateVatInput {
  estimateId: ID;
  contractId: ID;
  input: { base_override?: Decimal | null; target?: Decimal | null };
}

export function useSetEstimateVat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ estimateId, input }: SetEstimateVatInput) =>
      estimatesApi.setVat(estimateId, input),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: qk.passport.project(variables.contractId) });
      toast.success("Ставка НДС обновлена");
    },
    onError: toastApiError,
  });
}

export function useMatrix(params: MatrixParams) {
  return useQuery({
    queryKey: qk.matrix.list(params),
    queryFn: () => analyticsApi.matrix(params),
    // Матрица тяжелее списков: держим предыдущую страницу на экране, пока едет
    // следующая, — иначе таблица мигает пустотой на каждом шаге пагинации.
    placeholderData: (previous) => previous,
  });
}

export function useMatrixCell(
  contractId: number | undefined,
  catalogPositionId: number | undefined
) {
  return useQuery({
    queryKey: qk.matrix.cell(contractId ?? 0, catalogPositionId ?? 0),
    queryFn: () => analyticsApi.matrixCell(contractId as number, catalogPositionId as number),
    enabled: contractId !== undefined && catalogPositionId !== undefined,
  });
}

/**
 * Сравнение договоров по статьям классификатора (спека 2026-08-17, задача 8).
 *
 * `enabled` по умолчанию `true`, но страница обязана передать `false`, пока
 * в адресе нет ни `ids`, ни `all=1` (§2.6): без выборки эндпоинт отвечает 400
 * («выборка не задана»), а безусловный хук штатно генерировал бы этот отказ
 * при каждом заходе на голый `/compare` — тот же приём, что у
 * `useDashboardAttention`.
 */
export function useComparison(params: ComparisonParams, enabled = true) {
  return useQuery({
    queryKey: qk.comparison.get(params),
    queryFn: () => comparisonApi.get(params),
    enabled,
  });
}

// ---------------------------------------------------------------------------
//  Выгрузки §7.6
// ---------------------------------------------------------------------------

/**
 * Сохранение blob на диск.
 *
 * Вынесено из `useDownloadJobFile`, потому что выгрузок стало три и повторять этот
 * танец с временной ссылкой в каждой — верный способ разойтись в деталях.
 * `createObjectURL` в jsdom отсутствует, поэтому шаг необязательный: тесты проверяют
 * запрос и разбор отказов, а не работу файлового диалога.
 */
function saveBlob(blob: Blob, filename: string): void {
  if (typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Причина отказа выгрузки — из блоба.
 *
 * `responseType: "blob"` меняет форму тела: при отказе `axios` отдаёт JSON сервера
 * тоже блобом, и `apiErrorDetail` из него ничего не достаёт. Без разбора человек
 * видел бы «Request failed with status code 500» — по-английски и не о том, тогда как
 * сервер объяснил причину (например «Договор 10 не найден»).
 */
async function reportErrorMessage(err: unknown): Promise<string> {
  const data = (err as AxiosError)?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      const detail = (parsed as { detail?: unknown })?.detail;
      if (typeof detail === "string" && detail) return detail;
      // Кодированный отказ приходит и в блоб-пути: выгрузка сравнения отвечает
      // тем же структурированным 422, что экран (DoD 11), и без этой ветки
      // человек увидел бы общее «Не удалось построить файл отчёта» вместо
      // перечня недостающих годов.
      if (
        detail !== null &&
        typeof detail === "object" &&
        typeof (detail as { message?: unknown }).message === "string"
      ) {
        return (detail as { message: string }).message;
      }
    } catch {
      // Тело не JSON — значит объяснения нет, идём к общему сообщению ниже.
    }
  }
  return apiErrorDetail(err) ?? "Не удалось построить файл отчёта.";
}

function toastReportError(err: unknown): void {
  void reportErrorMessage(err).then((message) => toast.error(message));
}

export function useContractSummaryReport() {
  return useMutation({
    mutationFn: async ({ contractId, filename }: { contractId: number; filename: string }) => {
      const blob = await reportsApi.contractSummary(contractId);
      saveBlob(blob, filename);
      return blob;
    },
    onError: toastReportError,
  });
}

/**
 * Выгрузка сравнения договоров в Excel — третий отчёт §7.6 (`AGENTS.md` v6.8).
 *
 * Живёт рядом с двумя другими выгрузками и по той же схеме: `blob` →
 * `saveBlob`. Кнопка стоит НА СТРАНИЦЕ СРАВНЕНИЯ, а не на экране отчётов, и
 * это не вкусовое решение: выгрузке нужна выборка договоров, а `ReportsPage`
 * её дать не может — там есть период и класс, но не набор договоров (§2.6).
 * Без кнопки именно здесь третий отчёт был бы недостижим из интерфейса.
 */
export function useComparisonReport() {
  return useMutation({
    mutationFn: async (params: ComparisonParams) => {
      const blob = await reportsApi.comparison(params);
      saveBlob(blob, "Сравнение договоров.xlsx");
      return blob;
    },
    onError: toastReportError,
  });
}

export function useBankComparisonReport() {
  return useMutation({
    mutationFn: async (params: BankComparisonParams) => {
      const blob = await reportsApi.bankComparison(params);
      saveBlob(blob, "Сравнение с нормативами.xlsx");
      return blob;
    },
    onError: toastReportError,
  });
}

// ---------------------------------------------------------------------------
//  Ряды индексов инфляции (спека 2026-08-18 §2.10, §2.12)
// ---------------------------------------------------------------------------

export function useInflationSeries(includeArchived = false) {
  return useQuery({
    queryKey: qk.inflationSeries.list(includeArchived),
    queryFn: () => inflationSeriesApi.list(includeArchived),
  });
}

export function useInflationSeriesValues(id: number | null) {
  return useQuery({
    queryKey: qk.inflationSeries.values(id ?? 0),
    queryFn: () => inflationSeriesApi.values(id as number),
    enabled: id !== null,
  });
}

export function useCreateInflationSeries() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: InflationSeriesInput) => inflationSeriesApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.inflationSeries.all });
      toast.success("Ряд индексов создан");
    },
    onError: toastApiError,
  });
}

/**
 * Правка ряда — ОДИН запрос на всё окно (§2.12).
 *
 * Инвалидирует и `qk.comparison.all`: после сохранения сравнение обязано
 * перезапроситься (DoD 36). Иначе на экране остались бы числа по прежним
 * коэффициентам при уже новой подписи — ровно то расхождение подписи с числами,
 * против которого написан §2.8.
 */
export function useUpdateInflationSeries() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: InflationSeriesPatch }) =>
      inflationSeriesApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.inflationSeries.all });
      qc.invalidateQueries({ queryKey: qk.comparison.all });
      toast.success("Ряд индексов сохранён");
    },
    onError: toastApiError,
  });
}

// ---------------------------------------------------------------------------
//  Тендеры (§7.8)
// ---------------------------------------------------------------------------

export function useTenders(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({ queryKey: qk.tenders.list(params), queryFn: () => tendersApi.list(params) });
}

export function useTender(id: number | undefined) {
  return useQuery({ queryKey: qk.tenders.card(id ?? 0), queryFn: () => tendersApi.get(id as number), enabled: id !== undefined });
}

export function useRoundImportJobs(tenderId: number | undefined, roundId: number | undefined) {
  return useQuery({
    queryKey: qk.tenders.roundJobs(tenderId ?? 0, roundId ?? 0),
    queryFn: () => tendersApi.roundImportJobs(tenderId as number, roundId as number),
    enabled: tenderId !== undefined && roundId !== undefined,
  });
}

/**
 * Свод по этапам одного участника (спека 2026-08-27-stage-summary-design.md §2.16).
 * Требует хотя бы два предложения (Р8); 4xx кодов не повторяет (retry: false).
 */
export function useStageSummary(tenderId: number | undefined, offerIds: number[]) {
  return useQuery({
    queryKey: qk.tenders.stageSummary(tenderId ?? 0, offerIds),
    queryFn: () => tendersApi.stageSummary(tenderId as number, offerIds),
    enabled: tenderId !== undefined && offerIds.length >= 1,
    retry: false,
  });
}

/**
 * Разложение статьи свода по этапам (спека 2026-08-30-position-drilldown-
 * design.md §2.12). Раскрытие ленивое (§2.1) — `enabled` учитывает и флаг
 * вызывающего, и наличие tenderId/offerIds, поэтому строка со свёрнутым
 * блоком работ не шлёт запрос вовсе.
 */
export function useStagePositions(
  tenderId: number | undefined,
  workCategoryId: number,
  offerIds: number[],
  enabled: boolean
) {
  return useQuery({
    queryKey: qk.tenders.stagePositions(tenderId ?? 0, workCategoryId, offerIds),
    queryFn: () => tendersApi.stagePositions(tenderId as number, workCategoryId, offerIds),
    enabled: enabled && tenderId !== undefined && offerIds.length >= 1,
    // §6.3: раскрытие шлёт РОВНО ОДИН запрос и не шлёт повторно при
    // сворачивании и повторном раскрытии. Двух настроек мало по отдельности:
    // `staleTime` держит данные свежими, пока запрос жив, а `gcTime` — сам
    // запрос, когда наблюдателей не осталось. Наблюдателей теряет РЕАЛЬНЫЙ
    // случай: блок работ подстатьи размонтируется вместе с ней, когда
    // сворачивают статью-предка (свёрнутая строка детей не рендерит вовсе), и
    // с дефолтным gcTime = 5 мин повторное раскрытие ушло бы за данными
    // заново (ревью плана 31.08.2026 — прежняя редакция обещала «компонент
    // остаётся смонтированным», что для потомков неверно).
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });
}

function invalidateTender(qc: ReturnType<typeof useQueryClient>, tenderId: number) {
  qc.invalidateQueries({ queryKey: qk.tenders.card(tenderId) });
  qc.invalidateQueries({ queryKey: qk.tenders.all });
}

export function useCreateTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: tendersApi.create,
    onSuccess: () => { qc.invalidateQueries({ queryKey: qk.tenders.all }); toast.success("Тендер создан"); },
    onError: toastApiError,
  });
}

export function useUpdateTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<Pick<TenderInput, "title" | "notes">> }) => tendersApi.update(id, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useDeleteTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => tendersApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.tenders.all });
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.review.all });
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      toast.success("Тендер удалён");
    },
    onError: toastApiError,
  });
}

export function useCreateRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, input }: { tenderId: number; input: RoundInput }) => tendersApi.createRound(tenderId, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useUpdateRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, roundId, input }: { tenderId: number; roundId: number; input: Partial<Omit<RoundInput, "stage_no">> }) =>
      tendersApi.updateRound(tenderId, roundId, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useDeleteRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, roundId }: { tenderId: number; roundId: number }) => tendersApi.removeRound(tenderId, roundId),
    onSuccess: (_, { tenderId }) => {
      invalidateTender(qc, tenderId);
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.review.all });
      toast.success("Раунд удалён");
    },
    onError: toastApiError,
  });
}

/** Без общего тоста: 409 — развилка «нужна замена раунда», её ведёт компонент. */
export function useUploadRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: tendersApi.uploadRound,
    onSuccess: (job) => { if (job.tender_id !== undefined) invalidateTender(qc, job.tender_id); },
  });
}

/**
 * Удаление участника — протокол token (спека §2.11).
 *
 * Без собственного `onError` этот запрос падал бы в ГЛОБАЛЬНЫЙ обработчик
 * (`App.tsx`), а тот кладёт в тост `error.message` — сырую строку axios вида
 * «Request failed with status code 409». Для 409 `confirmation_required` это
 * враньё: это не сбой, а ОЖИДАЕМЫЙ первый шаг протокола (preview состава),
 * диалог его уже показывает своим текстом. Поэтому именно этот код молчит.
 * Всякий ДРУГОЙ отказ того же запроса (409 `active_import`, 404, 500) —
 * настоящий, и должен дойти до человека по-русски, текстом сервера, — через
 * `toastApiError`, как у остальных мутаций.
 */
export function useDeleteParticipant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, packageId, confirmationToken }: { tenderId: number; packageId: number; confirmationToken?: string }) =>
      tendersApi.removeParticipant(tenderId, packageId, confirmationToken),
    onSuccess: (_, { tenderId }) => {
      invalidateTender(qc, tenderId);
      qc.invalidateQueries({ queryKey: qk.review.all });
      toast.success("Участник удалён; исходные файлы и результаты разбора сохранены");
    },
    onError: (error) => {
      if (apiErrorCode(error) === "confirmation_required") return;
      toastApiError(error);
    },
  });
}

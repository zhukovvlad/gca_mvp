import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import { adminApi } from "./api/admin";
import { analyticsApi, settingsApi } from "./api/analytics";
import {
  catalogApi,
  contractsApi,
  estimatesApi,
  rateStandardsApi,
  referencesApi,
  reviewApi,
  type ContractListParams,
} from "./api/domain";
import { jobRefetchInterval } from "./jobPolling";
import { qk } from "./queryKeys";

import type { ID } from "@/types/common";
import type { AdminUserCreateInput, AdminUserUpdateInput } from "@/types/admin";
import type {
  ContractInput,
  ContractorInput,
  ManualKind,
  ObjectInput,
  RateClassInput,
  RateStandardInput,
  RateStandardParams,
  ReapproveInput,
  MatrixParams,
  ReviewQueueParams,
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
  return undefined;
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

/**
 * Поллинг задания импорта. Прекращается на `done`/`error`.
 *
 * `contractId` — чтобы при завершении обновить карточку: смета появляется в БД
 * позже ответа `202`, и без инвалидации экран показывал бы «смет нет» у
 * успешного задания.
 */
export function useImportJob(jobId: number | undefined, contractId?: number) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: qk.importJobs.one(jobId ?? 0),
    queryFn: async () => {
      const job = await estimatesApi.getJob(jobId as number);
      if (job.status === "done" && contractId !== undefined) {
        qc.invalidateQueries({ queryKey: qk.contracts.card(contractId) });
        qc.invalidateQueries({ queryKey: qk.contracts.importJobs(contractId) });
        qc.invalidateQueries({ queryKey: qk.review.all });
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
      // Паспорт зависит от N — без этой инвалидации уже открытый паспорт остался
      // бы с прежним числом строк, и настройка выглядела бы неработающей.
      qc.invalidateQueries({ queryKey: qk.passport.all });
      toast.success(`Ключевых расценок в паспорте: ${settings.passport_top_n}`);
    },
    onError: toastApiError,
  });
}

/**
 * Паспорт объекта (§7.4).
 *
 * N берёт сервер из БД, поэтому здесь его нет ни в аргументах, ни в ключе:
 * перерисовку при смене настройки делает инвалидация `passport.all` в
 * `useUpdateAppSettings` (см. комментарий у `qk.passport.one`).
 */
export function usePassport(contractId: number | undefined) {
  return useQuery({
    queryKey: qk.passport.one(contractId ?? 0),
    queryFn: () => analyticsApi.passport(contractId as number),
    enabled: contractId !== undefined,
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

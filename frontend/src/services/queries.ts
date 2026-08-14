import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import { adminApi } from "./api/admin";
import { analyticsApi, reportsApi, settingsApi } from "./api/analytics";
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
  BankComparisonParams,
  ClearCategoryOverrideInput,
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
  SetCategoryOverrideInput,
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
        // Успешный импорт МЕНЯЕТ содержимое паспорта целиком: смета появляется
        // или заменяется, а с ней все суммы по статьям. Без этой инвалидации
        // паспорт, открытый до загрузки, ещё минуту показывал бы «смета не
        // загружена» либо суммы прежней сметы (спека Ф6 §2.4).
        qc.invalidateQueries({ queryKey: qk.passport.all });
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import { projectsApi } from "./api/projects";
import { materialClassesApi } from "./api/materialClasses";
import { referencePricesApi } from "./api/referencePrices";
import { materialTypesApi, unitsApi } from "./api/units";
import { invoicesApi } from "./api/invoices";
import { dashboardApi } from "./api/dashboard";
import { uploadApi } from "./api/upload";
import { settingsApi } from "./api/settings";
import { suppliersApi } from "./api/suppliers";
import type { SupplierUpdateInput } from "./api/suppliers";
import { adminApi } from "./api/admin";
import { corridorsApi } from "./api/compensationCorridors";
import type { CorridorUpsertPayload } from "@/types/compensationCorridor";
import { qk } from "./queryKeys";
import { processingRefetchInterval } from "./processingRefetchInterval";

import type { ID } from "@/types/common";
import type { ProjectCreateInput, ProjectUpdateInput } from "@/types/project";
import type { MaterialClassCreateInput } from "@/types/materialClass";
import type { MaterialType, Unit } from "@/types/unit";
import type { ReferencePriceCreateInput, ReferencePriceUpdateInput } from "@/types/referencePrice";
import type { DocumentDetail, InvoiceUpdateInput } from "@/types/invoice";
import type { DashboardSummary } from "@/types/dashboard";
import type { SettingsUpdate } from "./api/settings";
import type {
  AdminUserCreateInput,
  AdminUserUpdateInput,
  OrgCreateInput,
  OrgUpdateInput,
  ProjectLinkInput,
} from "@/types/admin";

// ========== Projects ==========
export function useProjects() {
  return useQuery({ queryKey: qk.projects.all, queryFn: projectsApi.list });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ProjectCreateInput) => projectsApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект создан");
    },
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: ProjectUpdateInput }) =>
      projectsApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект обновлён");
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => projectsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект удалён");
    },
  });
}

// ========== Material classes ==========
export function useMaterialClasses() {
  return useQuery({
    queryKey: qk.materialClasses.all,
    queryFn: materialClassesApi.list,
  });
}

export function useCreateMaterialClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: MaterialClassCreateInput) =>
      materialClassesApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.materialClasses.all });
      toast.success("Класс материала добавлен");
    },
  });
}

export function useDeleteMaterialClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => materialClassesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.materialClasses.all });
      toast.success("Класс материала удалён");
    },
  });
}

// ========== Units ==========
export function useUnits() {
  return useQuery<Unit[]>({
    queryKey: qk.units.all,
    queryFn: () => unitsApi.list(),
    staleTime: Infinity,  // reference data — does not change at runtime
  });
}

export function useMaterialTypes() {
  return useQuery<MaterialType[]>({
    queryKey: qk.materialTypes.all,
    queryFn: () => materialTypesApi.list(),
    staleTime: Infinity,
  });
}

// ========== Reference prices ==========
export function useReferencePrices(
  projectId?: ID,
  options?: { enabled?: boolean; materialClassId?: ID; direction?: string },
) {
  const { enabled, materialClassId, direction } = options ?? {};
  return useQuery({
    queryKey: qk.referencePrices.all(projectId, materialClassId, direction),
    queryFn: () => referencePricesApi.list(projectId, materialClassId, direction),
    enabled,
  });
}

export function useCreateReferencePrice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ReferencePriceCreateInput) =>
      referencePricesApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.referencePrices.all() });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Базовая цена сохранена");
    },
  });
}

export function useUpdateReferencePrice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: ReferencePriceUpdateInput }) =>
      referencePricesApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.referencePrices.all() });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Базовая цена обновлена");
    },
  });
}

export function useDeleteReferencePrice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => referencePricesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.referencePrices.all() });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Базовая цена удалена");
    },
  });
}

// ========== Documents / Invoices ==========
export function useDocument(docId: ID | null | undefined) {
  return useQuery({
    queryKey: qk.documents.detail(docId ?? -1),
    queryFn: () => invoicesApi.getDocument(docId as ID),
    enabled: docId !== null && docId !== undefined,
    refetchInterval: processingRefetchInterval,
  });
}

export function useReparseDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.reparseDocument(docId),
    onSuccess: (data) => {
      // Сеем 202-ответ (status="processing") в кэш ДО инвалидаций — иначе,
      // если фоновая обработка завершится до первого рефетча, детектор
      // терминального перехода (terminalTransition.ts) впервые увидит уже
      // parsed/error без предшествующего processing в Map → перехода не
      // будет зафиксировано → dashboard не инвалидируется (Codex P2, fix 1).
      qc.setQueryData(qk.documents.detail(data.id), data);
      qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
      qc.invalidateQueries({ queryKey: qk.documents.list(data.project_id) });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Обработка запущена");
    },
  });
}

export function useDeskewReparseDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.deskewReparseDocument(docId),
    onSuccess: (data) => {
      // См. комментарий в useReparseDocument — тот же приём сеяния 202 в кэш.
      qc.setQueryData(qk.documents.detail(data.id), data);
      qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
      qc.invalidateQueries({ queryKey: qk.documents.list(data.project_id) });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Обработка запущена");
    },
  });
}

export function useUpdateInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: InvoiceUpdateInput }) =>
      invoicesApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("СФ сохранена");
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.removeDocument(docId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Документ удалён");
    },
  });
}

export function useDeleteInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invoiceId: ID) => invoicesApi.removeInvoice(invoiceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["document"] });
      toast.success("СФ удалена");
    },
  });
}

export function useDeleteInvoicesBulk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: ID[]) => invoicesApi.bulkDeleteInvoices(ids),
    onSuccess: ({ deleted, skipped }) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["document"] });
      if (skipped.length > 0) {
        toast.success(`Удалено ${deleted}, пропущено ${skipped.length} (подтверждены)`);
      } else {
        toast.success(`Удалено ${deleted}`);
      }
    },
  });
}

export function useVerifyInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invoiceId: ID) => invoicesApi.verifyInvoice(invoiceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document"] });
      qc.invalidateQueries({ queryKey: ["dashboard", "invoices"] });
      toast.success("СФ подтверждён");
    },
  });
}

export function useUnverifyInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invoiceId: ID) => invoicesApi.unverifyInvoice(invoiceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document"] });
      qc.invalidateQueries({ queryKey: ["dashboard", "invoices"] });
      toast.success("Подтверждение снято");
    },
  });
}

// ========== Dashboard ==========
export function useDashboardSummary(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.summary(projectId) : ["dashboard", "summary", "none"],
    queryFn: () => dashboardApi.summary(projectId as ID),
    enabled: projectId !== null,
  });
}

export function useDashboardInvoices(
  projectId: ID | null,
  direction?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.invoices(projectId, direction) : ["dashboard", "invoices", "none"],
    queryFn: () => dashboardApi.invoices(projectId as ID, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
  });
}

export function useDashboardCalculations(
  projectId: ID | null,
  periodStart?: string,
  periodEnd?: string,
  direction?: string,
  options?: { enabled?: boolean },
) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: projectId
      ? qk.dashboard.calculations(projectId, periodStart, periodEnd, direction)
      : ["dashboard", "calculations", "none"],
    queryFn: () => dashboardApi.calculations(projectId as ID, periodStart, periodEnd, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
    // Переиспользуем calc-rows из уже загруженного summary: на первой отрисовке дефолтного
    // вида (период не задан) запрос не уходит. Изменённый период / старый бэк без поля /
    // projectId===null (query disabled) → undefined → сеть. Клиентский фильтр по direction
    // эквивалентен бэкенд-фильтру (применяется после аллокации). §2 спеки.
    initialData: () => {
      if (projectId === null || periodStart || periodEnd) return undefined;
      const s = qc.getQueryData<DashboardSummary>(qk.dashboard.summary(projectId));
      if (s?.calculations === undefined) return undefined;
      // direction здесь уже провалидирован вызывающей стороной (scopedDirection на
      // ProjectPage сверяется с summary.directions, "other" туда не попадает) —
      // фильтр по неизвестному коду и падение бэка на 422 недостижимы отсюда.
      return direction
        ? s.calculations.filter((r) => r.direction === direction)
        : s.calculations;
    },
    initialDataUpdatedAt: () =>
      projectId === null
        ? undefined
        : qc.getQueryState(qk.dashboard.summary(projectId))?.dataUpdatedAt,
  });
}

export function useDashboardMonthlySummary(
  projectId: ID | null,
  direction?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.monthly(projectId, direction) : ["dashboard", "monthly", "none"],
    queryFn: () => dashboardApi.monthlySummary(projectId as ID, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
  });
}

// ========== Upload ==========
export function useUploadInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      file,
      onProgress,
    }: {
      projectId: ID;
      file: File;
      onProgress?: (pct: number) => void;
    }) => uploadApi.uploadInvoice(projectId, file, onProgress),
    onSuccess: (data) => {
      // См. комментарий в useReparseDocument — сеем ответ (202 processing или
      // 200 duplicate) в кэш detail до инвалидаций. `duplicate` — служебное
      // поле ответа загрузки, в DocumentDetail не входит; data структурно
      // совместима (excess property check не действует на не-литералы).
      const doc: DocumentDetail = data;
      qc.setQueryData(qk.documents.detail(doc.id), doc);
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

// ========== Settings ==========
export function useSettings() {
  return useQuery({ queryKey: qk.settings.current, queryFn: settingsApi.get });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SettingsUpdate) => settingsApi.update(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.settings.current });
      toast.success("Настройки сохранены");
    },
  });
}

// ========== Documents ==========
export function useDocuments(projectId?: number) {
  return useQuery({
    queryKey: qk.documents.list(projectId),
    queryFn: () => invoicesApi.listDocuments(projectId),
    refetchInterval: processingRefetchInterval,
  });
}

// ========== Dashboard (all projects) ==========
export function useAllCalculations() {
  return useQuery({
    queryKey: qk.dashboard.calculationsAll,
    queryFn: () => dashboardApi.calculationsAll(),
    staleTime: 0,
  });
}

// ========== Suppliers ==========
export function useSuppliers() {
  return useQuery({ queryKey: qk.suppliers.all, queryFn: suppliersApi.list });
}

export function useSupplierDetail(id: ID | null | undefined) {
  return useQuery({
    queryKey: qk.suppliers.detail(id ?? -1),
    queryFn: () => suppliersApi.get(id as ID),
    enabled: id !== null && id !== undefined,
  });
}

export function useSupplierProjects(id: ID | null | undefined) {
  return useQuery({
    queryKey: qk.suppliers.projects(id ?? -1),
    queryFn: () => suppliersApi.getProjects(id as ID),
    enabled: id !== null && id !== undefined,
  });
}

export function useSupplierInvoices(id: ID | null | undefined, projectId?: ID) {
  return useQuery({
    queryKey: qk.suppliers.invoices(id ?? -1, projectId),
    queryFn: () => suppliersApi.getInvoices(id as ID, projectId),
    enabled: id !== null && id !== undefined,
  });
}

export function useUpdateSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: SupplierUpdateInput }) =>
      suppliersApi.update(id, input),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: qk.suppliers.all });
      qc.invalidateQueries({ queryKey: qk.suppliers.detail(id) });
      toast.success("Поставщик обновлён");
    },
  });
}

export function useMergeSupplier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ targetId, sourceId }: { targetId: ID; sourceId: ID }) =>
      suppliersApi.merge(targetId, sourceId),
    onSuccess: (_data, { targetId, sourceId }) => {
      qc.invalidateQueries({ queryKey: qk.suppliers.all });
      qc.invalidateQueries({ queryKey: qk.suppliers.detail(targetId) });
      qc.invalidateQueries({ queryKey: qk.suppliers.projects(targetId) });
      // source удалён на сервере — убираем его кэш полностью
      qc.removeQueries({ queryKey: qk.suppliers.detail(sourceId) });
      qc.removeQueries({ queryKey: qk.suppliers.projects(sourceId) });
      toast.success("Поставщики объединены");
    },
  });
}

// ========== Project Suppliers & Exclusions ==========

export function useProjectSuppliers(
  projectId: ID | null,
  direction?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: projectId ? qk.projectSuppliers(projectId, direction) : ["project-suppliers-disabled"],
    queryFn: () => projectsApi.getSuppliers(projectId!, direction),
    enabled: projectId !== null && (options?.enabled ?? true),
  });
}

export function useSupplierExclusions(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.supplierExclusions(projectId) : ["supplier-exclusions-disabled"],
    queryFn: () => projectsApi.getSupplierExclusions(projectId!),
    select: (ids) => new Set(ids),
    enabled: projectId !== null,
  });
}

export function useToggleSupplierExclusion(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      supplierId,
      excluded,
      reason,
    }: {
      supplierId: ID;
      excluded: boolean;
      reason?: string;
    }) => {
      if (!projectId) return Promise.resolve();
      return excluded
        ? projectsApi.addSupplierExclusion(projectId, supplierId, reason)
        : projectsApi.removeSupplierExclusion(projectId, supplierId);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.supplierExclusions(projectId) });
      // invalidate by prefix so all period variants are matched
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "monthly", projectId] });
    },
  });
}

// ========== Corridors (fallback hierarchy) ==========

export function useCorridors(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.corridors(projectId) : ["corridors-disabled"],
    queryFn: () => corridorsApi.getMatrix(projectId!),
    enabled: projectId !== null,
  });
}

export function useSetTypeCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ materialType, payload }: { materialType: string; payload: CorridorUpsertPayload }) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.setType(projectId, materialType, payload);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
    },
  });
}

export function useDeleteTypeCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (materialType: string) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.deleteType(projectId, materialType);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
    },
  });
}

export function useSetClassCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ materialClassId, payload }: { materialClassId: ID; payload: CorridorUpsertPayload }) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.setClass(projectId, materialClassId, payload);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
    },
  });
}

export function useDeleteClassCorridor(projectId: ID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (materialClassId: ID) => {
      if (!projectId) return Promise.resolve();
      return corridorsApi.deleteClass(projectId, materialClassId);
    },
    onSuccess: () => {
      if (!projectId) return;
      qc.invalidateQueries({ queryKey: qk.corridors(projectId) });
      qc.invalidateQueries({ queryKey: ["dashboard", "calculations", projectId] });
      qc.invalidateQueries({ queryKey: qk.dashboard.calculationsAll });
      qc.invalidateQueries({ queryKey: qk.dashboard.summary(projectId) });
    },
  });
}

// ========== Admin (superuser) ==========

export function useAdminOrganizations() {
  return useQuery({ queryKey: qk.admin.organizations, queryFn: adminApi.listOrganizations });
}

export function useAdminOrganization(id: ID | null | undefined) {
  return useQuery({
    queryKey: qk.admin.organization(id ?? -1),
    queryFn: () => adminApi.getOrganization(id as ID),
    enabled: id !== null && id !== undefined,
  });
}

export function useAdminUsers(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: qk.admin.users(params?.q, params?.page, params?.page_size),
    queryFn: () => adminApi.listUsers(params),
  });
}

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OrgCreateInput) => adminApi.createOrganization(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
    },
    onError: (err: unknown) => {
      const detail = (err as AxiosError<{ detail?: string }>)?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : err instanceof Error ? err.message : "Произошла ошибка");
    },
  });
}

export function useUpdateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: OrgUpdateInput }) =>
      adminApi.updateOrganization(id, input),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
      qc.invalidateQueries({ queryKey: qk.admin.organization(id) });
      toast.success("Организация обновлена");
    },
  });
}

export function useCreateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orgId, input }: { orgId: ID; input: AdminUserCreateInput }) =>
      adminApi.createUser(orgId, input),
    onSuccess: (_data, { orgId }) => {
      qc.invalidateQueries({ queryKey: qk.admin.organization(orgId) });
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (err: unknown) => {
      const detail = (err as AxiosError<{ detail?: string }>)?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : err instanceof Error ? err.message : "Произошла ошибка");
    },
  });
}

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, input }: { userId: ID; input: AdminUserUpdateInput }) =>
      adminApi.updateUser(userId, input),
    onSuccess: (data) => {
      // Карточка конкретной организации (AdminOrgDetail) — чтобы роль/статус обновились сразу
      if (data.org_id) qc.invalidateQueries({ queryKey: qk.admin.organization(data.org_id) });
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Пользователь обновлён");
    },
    onError: (err: unknown) => {
      const detail = (err as AxiosError<{ detail?: string }>)?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : err instanceof Error ? err.message : "Произошла ошибка");
    },
  });
}

export function useResetUserPassword() {
  // Намеренно без инвалидации — возвращает plaintext-пароль, который страница
  // показывает в диалоге. Тост-напоминание вызывается на странице после показа.
  return useMutation({
    mutationFn: (userId: ID) => adminApi.resetPassword(userId),
  });
}

export function useLinkProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orgId, input }: { orgId: ID; input: ProjectLinkInput }) =>
      adminApi.linkProject(orgId, input),
    onSuccess: (_data, { orgId }) => {
      qc.invalidateQueries({ queryKey: qk.admin.organization(orgId) });
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
      toast.success("Доступ к проекту выдан");
    },
  });
}

export function useUnlinkProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orgId, projectId }: { orgId: ID; projectId: ID }) =>
      adminApi.unlinkProject(orgId, projectId),
    onSuccess: (_data, { orgId }) => {
      qc.invalidateQueries({ queryKey: qk.admin.organization(orgId) });
      qc.invalidateQueries({ queryKey: qk.admin.organizations });
      toast.success("Доступ к проекту снят");
    },
  });
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { AxiosError } from "axios";

import { adminApi } from "./api/admin";
import { qk } from "./queryKeys";

import type { ID } from "@/types/common";
import type { AdminUserCreateInput, AdminUserUpdateInput } from "@/types/admin";

// ========== Admin (пользователи) ==========

function toastApiError(err: unknown) {
  const detail = (err as AxiosError<{ detail?: string }>)?.response?.data?.detail;
  toast.error(typeof detail === "string" ? detail : err instanceof Error ? err.message : "Произошла ошибка");
}

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

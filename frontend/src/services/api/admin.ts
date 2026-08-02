import api from "@/lib/api";
import type { ID } from "@/types/common";
import type {
  AdminUser,
  AdminUserCreateInput,
  AdminUserUpdateInput,
  AdminUsersPage,
  ResetPasswordResult,
} from "@/types/admin";

export const adminApi = {
  createUser: (input: AdminUserCreateInput): Promise<AdminUser> =>
    api.post<AdminUser>("/admin/users", input).then((r) => r.data),

  listUsers: (params?: { q?: string; page?: number; page_size?: number }): Promise<AdminUsersPage> =>
    api.get<AdminUsersPage>("/admin/users", { params }).then((r) => r.data),

  updateUser: (userId: ID, input: AdminUserUpdateInput): Promise<AdminUser> =>
    api.patch<AdminUser>(`/admin/users/${userId}`, input).then((r) => r.data),

  resetPassword: (userId: ID): Promise<ResetPasswordResult> =>
    api.post<ResetPasswordResult>(`/admin/users/${userId}/reset-password`).then((r) => r.data),
};

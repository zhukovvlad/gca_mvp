/** Типы для админ-консоли (/api/admin/*). */
import type { ID, ISODateTime } from "@/types/common";
import type { UserRole } from "@/types/auth";

/** Пользователь в админ-списке. */
export interface AdminUser {
  id: ID;
  email: string;
  role: UserRole;
  is_active: boolean;
  created_at?: ISODateTime | null;
}

/** Страница пользователей (GET /api/admin/users). */
export interface AdminUsersPage {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

// --- Входные данные мутаций ---

export interface AdminUserCreateInput {
  email: string;
  password: string;
  role: UserRole;
  is_active?: boolean;
}

export interface AdminUserUpdateInput {
  role?: UserRole;
  is_active?: boolean;
}

/** Ответ на сброс пароля — plaintext возвращается один раз. */
export interface ResetPasswordResult {
  id: ID;
  email: string;
  password: string;
}

/** Типы для админ-консоли суперпользователя (/api/admin/*). */
import type { ID, ISODateTime } from "@/types/common";
import type { OrgKind, OrgRole } from "@/types/auth";

/** Базовые поля организации — то, что возвращают POST/PATCH /api/admin/organizations. */
export interface AdminOrgBase {
  id: ID;
  name: string;
  inn: string | null;
  // Бэкенд гарантирует NOT NULL (server_default='customer')
  kind: OrgKind;
}

/** Строка списка организаций (GET /api/admin/organizations) — с агрегатами. */
export interface AdminOrgListItem extends AdminOrgBase {
  created_at: ISODateTime | null;
  user_count: number;
  project_count: number;
}

/** Пользователь организации (в карточке и в общем списке). */
export interface AdminUser {
  id: ID;
  email: string;
  org_id: ID | null;
  org_role: OrgRole | null;
  is_superuser: boolean;
  is_active: boolean;
  created_at?: ISODateTime | null;
  /** Название организации — присутствует только в GET /api/admin/users. */
  org_name?: string | null;
}

/** Привязка организации к проекту. */
export interface OrgProjectLink {
  project_id: ID;
  project_name: string;
  project_role: OrgKind;
}

/** Детальная карточка организации (GET /api/admin/organizations/{id}). */
export interface AdminOrgDetail {
  id: ID;
  name: string;
  inn: string | null;
  kind: OrgKind;
  created_at: ISODateTime | null;
  users: AdminUser[];
  projects: OrgProjectLink[];
}

/** Страница пользователей (GET /api/admin/users). */
export interface AdminUsersPage {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

// --- Входные данные мутаций ---

export interface OrgCreateInput {
  name: string;
  inn?: string | null;
  kind: OrgKind;
}

export interface OrgUpdateInput {
  name?: string;
  inn?: string | null;
  kind?: OrgKind;
}

export interface AdminUserCreateInput {
  email: string;
  password: string;
  org_role: OrgRole;
  is_active?: boolean;
}

export interface AdminUserUpdateInput {
  org_role?: OrgRole;
  is_active?: boolean;
}

export interface ProjectLinkInput {
  project_id: ID;
  project_role?: OrgKind;
}

/** Ответ на сброс пароля — plaintext возвращается один раз. */
export interface ResetPasswordResult {
  id: ID;
  email: string;
  password: string;
}

/** Типы для аутентификации и профиля пользователя. */

/** Роль организации: заказчик / подрядчик. */
export type OrgKind = "customer" | "contractor";

export interface Organization {
  id: number;
  name: string;
  inn: string | null;
  // Бэкенд гарантирует NOT NULL (server_default='customer'); вся organization
  // может быть null (платформенный суперюзер без org), но если есть — kind задан.
  kind: OrgKind;
}

export type OrgRole = "superadmin" | "admin" | "member";

export interface User {
  id: number;
  email: string;
  org_id: number | null;
  org_role: OrgRole | null;
  is_superuser: boolean;
  organization: Organization | null;
}

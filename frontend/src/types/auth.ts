/** Типы для аутентификации и профиля пользователя (single-tenant). */

/** Роль пользователя: admin — управление, member — работа со сметами. */
export type UserRole = "admin" | "member";

export interface User {
  id: number;
  email: string;
  role: UserRole;
}

import api from "@/lib/api";
import type { User } from "@/types/auth";

export const authApi = {
  /** Логин по email + пароль. Куки устанавливаются сервером. */
  login: (email: string, password: string) =>
    api.post("/auth/login", { email, password }),

  /** Выход — отзыв refresh-токена + очистка куки на сервере. */
  logout: () => api.post("/auth/logout"),

  /** Текущий пользователь по access-токену из куки. */
  me: (): Promise<User> => api.get<User>("/auth/me").then((r) => r.data),
};

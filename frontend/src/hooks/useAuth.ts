import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/services/api/auth";
import type { User } from "@/types/auth";

/** Ключ кэша текущего пользователя — используется для prefill в тестах. */
export const CURRENT_USER_QUERY_KEY = ["currentUser"] as const;

/**
 * Текущий пользователь. Запрашивает GET /api/auth/me один раз при монтировании.
 * staleTime=5 мин — не дёргает сервер на каждый render.
 * retry=false — 401 сразу редиректит, не ждём повторных попыток.
 */
export function useCurrentUser() {
  return useQuery<User>({
    queryKey: CURRENT_USER_QUERY_KEY,
    queryFn: authApi.me,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

/** Мутация логина. После успеха инвалидирует кэш currentUser. */
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      authApi.login(email, password),
    onSuccess: () => qc.invalidateQueries({ queryKey: CURRENT_USER_QUERY_KEY }),
  });
}

/** Мутация выхода. Очищает весь кэш и редиректит на /login. */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      qc.clear(); // обязательно очищаем весь кэш — данные принадлежат сессии
      window.location.href = "/login";
    },
  });
}

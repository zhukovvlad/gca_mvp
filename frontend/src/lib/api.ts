/**
 * Axios-клиент с поддержкой:
 * - httpOnly cookie аутентификации (withCredentials)
 * - CSRF double-submit (X-CSRF-Token заголовок для state-changing запросов)
 * - Автоматического refresh access-токена при 401
 */
import axios, { AxiosHeaders, type InternalAxiosRequestConfig } from "axios";

// Расширяем тип конфига для поддержки флага повторного запроса
declare module "axios" {
  interface InternalAxiosRequestConfig {
    _retry?: boolean;
  }
}

/** Читает значение куки по имени. Поддерживает стандартный формат `a=1; b=2`. */
function getCookie(name: string): string | null {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${escaped}=([^;]+)`));
  return match ? decodeURIComponent(match[1]) : null;
}

const api = axios.create({
  baseURL: "/api",
  withCredentials: true, // cookie шлётся автоматически при каждом запросе
});

// CSRF: добавляем X-CSRF-Token для всех state-changing методов
api.interceptors.request.use((config) => {
  const method = config.method?.toUpperCase();
  if (method && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrf = getCookie("csrf_token");
    if (csrf) {
      config.headers = AxiosHeaders.from(config.headers);
      config.headers.set("X-CSRF-Token", csrf);
    }
  }
  return config;
});

// Авто-refresh: при 401 пробуем обновить access-токен и повторяем запрос
let refreshing: Promise<void> | null = null;

/** Выполняет POST /auth/refresh, возвращает Promise<void> и сбрасывает refreshing после. */
function doRefresh(): Promise<void> {
  return api
    .post("/auth/refresh")
    .then(() => undefined)
    .finally(() => {
      refreshing = null;
    });
}

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config as InternalAxiosRequestConfig | undefined;
    if (!original) return Promise.reject(error);
    if (
      error.response?.status === 401 &&
      !original._retry &&
      original.url !== "/auth/refresh" &&
      original.url !== "/auth/login" &&
      getCookie("csrf_token") !== null // нет куки — нет сессии, refresh бессмысленен
    ) {
      original._retry = true;
      try {
        refreshing = refreshing ?? doRefresh();
        await refreshing;
        return api(original);
      } catch {
        // Refresh не удался — отдаём 401 наверх; ProtectedLayout сделает Navigate /login
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  }
);

export default api;

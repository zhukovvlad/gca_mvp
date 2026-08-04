import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster, toast } from "sonner";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useCurrentUser } from "@/hooks/useAuth";
import Home from "@/pages/Home";
import LoginPage from "@/pages/LoginPage";
import AdminUserCreate from "@/pages/admin/AdminUserCreate";
import AdminUsers from "@/pages/admin/AdminUsers";
import ContractCardPage from "@/pages/contracts/ContractCardPage";
import ContractsPage from "@/pages/contracts/ContractsPage";
import MatrixPage from "@/pages/matrix/MatrixPage";
import PassportPage from "@/pages/passport/PassportPage";
import ReviewPage from "@/pages/review/ReviewPage";
import SettingsPage from "@/pages/settings/SettingsPage";
import StandardsPage from "@/pages/standards/StandardsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 60_000, refetchOnWindowFocus: false },
    mutations: {
      onError: (error: unknown) => {
        toast.error(error instanceof Error ? error.message : "Произошла ошибка");
      },
    },
  },
});

/**
 * Layout-компонент: проверяет наличие авторизованного пользователя.
 * Пока идёт загрузка — ничего не рендерим (избегаем мигания).
 * При ошибке или отсутствии user — редирект на /login.
 */
function ProtectedLayout() {
  const { data: user, isLoading, isError } = useCurrentUser();
  if (isLoading) return null;
  if (isError || !user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

/**
 * Guard для маршрутов администратора.
 * Пока идёт загрузка — ничего не рендерим. Если пользователь не admin —
 * редирект на главную (маршруты /admin/* недоступны member).
 */
export function RequireAdmin() {
  const { data: user, isLoading } = useCurrentUser();
  if (isLoading) return null;
  if (!user || user.role !== "admin") return <Navigate to="/" replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
      <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<ProtectedLayout />}>
              <Route element={<AppShell />}>
                <Route path="/" element={<Home />} />
                {/*
                  Договоры и ручной матчинг доступны и `member` (§3): он читает
                  карточки, грузит сметы и разбирает очередь. Право `admin` на
                  заведение карточек проверяет сервер, а экран лишь не рисует
                  кнопок, которых у member нет (решение §6.2).
                */}
                <Route path="/contracts" element={<ContractsPage />} />
                <Route path="/contracts/:contractId" element={<ContractCardPage />} />
                <Route path="/review" element={<ReviewPage />} />
                {/*
                  Аналитика фазы 6 — чтение, поэтому доступна и `member` (§3):
                  паспорт печатают и без прав на правку справочников.
                */}
                <Route path="/contracts/:contractId/passport" element={<PassportPage />} />
                <Route path="/matrix" element={<MatrixPage />} />
                <Route element={<RequireAdmin />}>
                  {/* Нормативы — право `admin` по букве §3. */}
                  <Route path="/standards" element={<StandardsPage />} />
                  {/* Настройки печатной формы — того же рода, что классы и нормативы (§3). */}
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/admin/users" element={<AdminUsers />} />
                  <Route path="/admin/users/new" element={<AdminUserCreate />} />
                </Route>
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
      </TooltipProvider>
    </ThemeProvider>
  );
}

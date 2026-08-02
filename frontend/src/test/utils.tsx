import { type ReactElement, type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CURRENT_USER_QUERY_KEY } from "@/hooks/useAuth";
import type { User } from "@/types/auth";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Дефолтный пользователь для тестов — org admin. */
const DEFAULT_TEST_USER: User = {
  id: 1,
  email: "test@example.com",
  org_id: 1,
  org_role: "admin",
  is_superuser: false,
  organization: { id: 1, name: "Тест Орг", inn: null, kind: "customer" },
};

interface WrapperProps {
  children: ReactNode;
  queryClient?: QueryClient;
  initialRoute?: string;
}

export function AllProviders({ children, queryClient, initialRoute = "/" }: WrapperProps) {
  const client = queryClient ?? createTestQueryClient();
  return (
    <ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false}>
      <TooltipProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[initialRoute]}>{children}</MemoryRouter>
        </QueryClientProvider>
      </TooltipProvider>
    </ThemeProvider>
  );
}

interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  initialRoute?: string;
  queryClient?: QueryClient;
  /**
   * Предзаполнить кэш `currentUser`.
   * - По умолчанию: DEFAULT_TEST_USER (org admin).
   * - null — кэш остаётся пустым, `useCurrentUser()` сделает запрос `/api/auth/me`.
   *   MSW-хендлер по умолчанию возвращает валидного пользователя, поэтому редиректа
   *   НЕ будет. Чтобы протестировать неавторизованный сценарий, переопределите
   *   хендлер в тесте:
   *   ```ts
   *   server.use(http.get("/api/auth/me", () => new HttpResponse(null, { status: 401 })));
   *   ```
   */
  initialUser?: User | null;
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderWithProvidersOptions = {}
) {
  const { initialRoute, queryClient: qcFromOptions, initialUser = DEFAULT_TEST_USER, ...rest } = options;
  const qc = qcFromOptions ?? createTestQueryClient();

  // Предзаполняем кэш currentUser — ProtectedRoute не будет делать реальный запрос
  if (initialUser !== null) {
    qc.setQueryData(CURRENT_USER_QUERY_KEY, initialUser);
  }

  return render(ui, {
    wrapper: ({ children }) => (
      <AllProviders queryClient={qc} initialRoute={initialRoute}>
        {children}
      </AllProviders>
    ),
    ...rest,
  });
}


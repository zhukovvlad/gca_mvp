import { type ReactElement, type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
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

/** Дефолтный пользователь для тестов — admin. */
const DEFAULT_TEST_USER: User = {
  id: 1,
  email: "test@example.com",
  role: "admin",
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
          {/*
            Toaster есть и в App.tsx. В тестах он нужен потому, что тост — это
            единственный канал, которым экран сообщает об отказе сервера
            (`toastApiError`) и о частично применённом пакете Review: без него
            такие сообщения нельзя проверить, они просто не попадают в DOM.
          */}
          <Toaster />
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
   * - По умолчанию: DEFAULT_TEST_USER (admin).
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


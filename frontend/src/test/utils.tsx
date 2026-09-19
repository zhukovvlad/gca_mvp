import { type ReactElement, type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import { vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CURRENT_USER_QUERY_KEY } from "@/hooks/useAuth";
import type { User } from "@/types/auth";

/**
 * Перехват `saveBlob` (`services/queries.ts`, три выгрузки §7.6 и «Изменения
 * КП»). jsdom не даёт `URL.createObjectURL`, и `saveBlob` тихо ничего не
 * делает без него — этим пользуются тесты, которым нужен только факт запроса.
 * Здесь наоборот: нужно ДОКАЗАТЬ, какое имя дошло бы до диска (внешнее ревью
 * H3: имя обязано нести номер тендера, а не быть зашитой строкой), поэтому
 * оба API подставляются, а клик по временной ссылке перехватывается ДО того,
 * как jsdom пожалуется на переход по `blob:`-адресу.
 */
export function spyOnDownload() {
  const filenames: string[] = [];
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;
  const originalCreateElement = document.createElement.bind(document);
  Object.defineProperty(URL, "createObjectURL", { value: () => "blob:test", configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: () => {}, configurable: true });
  const createElementSpy = vi
    .spyOn(document, "createElement")
    .mockImplementation((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName, options);
      if (tagName === "a") {
        (element as HTMLAnchorElement).click = () => {
          filenames.push((element as HTMLAnchorElement).download);
        };
      }
      return element;
    });
  return {
    lastFilename: () => filenames.at(-1),
    restore: () => {
      createElementSpy.mockRestore();
      Object.defineProperty(URL, "createObjectURL", { value: originalCreateObjectURL, configurable: true });
      Object.defineProperty(URL, "revokeObjectURL", { value: originalRevokeObjectURL, configurable: true });
    },
  };
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Дефолтный пользователь для тестов — admin. */
export const DEFAULT_TEST_USER: User = {
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


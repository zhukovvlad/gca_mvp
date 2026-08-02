/**
 * Регрессионный тест (финальное ревью PR-2, Medium finding): все четыре corridor-мутации
 * обязаны инвалидировать dashboard summary. PR-2 завёл посев useDashboardCalculations
 * из summary.calculations через initialData — если corridor-мутация не трогает summary,
 * его кэш остаётся с ДО-мутационными calc-строками (устаревшие corridor_pct/
 * compensation_amount), и посев на непрогретый direction подставит их как «свежие»
 * (initialDataUpdatedAt наследует старый summary timestamp, staleTime=60s гасит рефетч).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { qk } from "@/services/queryKeys";
import {
  useSetTypeCorridor,
  useDeleteTypeCorridor,
  useSetClassCorridor,
  useDeleteClassCorridor,
} from "@/services/queries";

const PROJECT_ID = 1;

/** QueryClient без ретраев — мутации/запросы должны падать или проходить детерминированно с первой попытки. */
function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

/** Обёртка renderHook с QueryClientProvider поверх переданного клиента. */
function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

/** Извлекает список invalidateQueries-вызовов спая (моков на QueryClient#invalidateQueries) в виде сериализованных queryKey. */
function invalidatedKeysOf(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls.map((args) => JSON.stringify((args[0] as { queryKey?: unknown })?.queryKey));
}

describe("corridor-мутации инвалидируют dashboard summary (Medium finding, финальное ревью PR-2)", () => {
  afterEach(() => {
    /** Возвращает qc.invalidateQueries к оригиналу между тестами (спай ставится на каждый клиент отдельно). */
    vi.restoreAllMocks();
  });

  it("useSetTypeCorridor: onSuccess инвалидирует qk.dashboard.summary(projectId)", async () => {
    server.use(
      http.put("/api/projects/:id/corridors/type/:materialType", () => HttpResponse.json({})),
    );
    const qc = testClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useSetTypeCorridor(PROJECT_ID), { wrapper: wrapper(qc) });

    result.current.mutate({ materialType: "concrete", payload: { is_compensable: true, corridor_pct: 5 } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidatedKeysOf(spy)).toContain(JSON.stringify(qk.dashboard.summary(PROJECT_ID)));
  });

  it("useDeleteTypeCorridor: onSuccess инвалидирует qk.dashboard.summary(projectId)", async () => {
    server.use(
      http.delete("/api/projects/:id/corridors/type/:materialType", () => HttpResponse.json({})),
    );
    const qc = testClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useDeleteTypeCorridor(PROJECT_ID), { wrapper: wrapper(qc) });

    result.current.mutate("concrete");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidatedKeysOf(spy)).toContain(JSON.stringify(qk.dashboard.summary(PROJECT_ID)));
  });

  it("useSetClassCorridor: onSuccess инвалидирует qk.dashboard.summary(projectId)", async () => {
    server.use(
      http.put("/api/projects/:id/corridors/class/:materialClassId", () => HttpResponse.json({})),
    );
    const qc = testClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useSetClassCorridor(PROJECT_ID), { wrapper: wrapper(qc) });

    result.current.mutate({ materialClassId: 1, payload: { is_compensable: true, corridor_pct: 5 } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidatedKeysOf(spy)).toContain(JSON.stringify(qk.dashboard.summary(PROJECT_ID)));
  });

  it("useDeleteClassCorridor: onSuccess инвалидирует qk.dashboard.summary(projectId)", async () => {
    server.use(
      http.delete("/api/projects/:id/corridors/class/:materialClassId", () => HttpResponse.json({})),
    );
    const qc = testClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useDeleteClassCorridor(PROJECT_ID), { wrapper: wrapper(qc) });

    result.current.mutate(1);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidatedKeysOf(spy)).toContain(JSON.stringify(qk.dashboard.summary(PROJECT_ID)));
  });
});

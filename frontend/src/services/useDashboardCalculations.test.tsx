/** Тесты посева calc-rows из summary через initialData (§2). */
import { describe, it, expect } from "vitest";
import type { ReactNode } from "react";
import { render, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { qk } from "@/services/queryKeys";
import { useDashboardCalculations, useDashboardSummary } from "@/services/queries";
import {
  sampleSummaryMultiWithCalcs,
  sampleSummaryMonoWithCalcs,
  sampleDashboardSummaryMulti,
} from "@/test/fixtures";

describe("useDashboardCalculations: посев из summary (§2)", () => {
  /** Prod-like клиент: staleTime>0, иначе посев мгновенно протухнет (test util ставит 0). */
  function prodLikeClient(): QueryClient {
    return new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000, gcTime: Infinity } },
    });
  }

  /** Счётчик сетевых запросов к /calculations. */
  function countCalculations(): string[] {
    const seen: string[] = [];
    server.use(
      http.get("/api/dashboard/calculations", ({ request }) => {
        seen.push(request.url);
        return HttpResponse.json([]);
      }),
    );
    return seen;
  }

  /** Обёртка renderHook с заранее посеянным summary в кэше клиента. */
  function wrapperWithSummary(qc: QueryClient, summary: unknown) {
    qc.setQueryData(qk.dashboard.summary(1), summary);
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  }

  it("cold-lifecycle: пустой кэш → summary приходит → initialData гасит /calculations", async () => {
    // Настоящий сценарий §2.1: calc-observer создаётся disabled ДО прихода summary;
    // initialData переоценивается на ре-рендерах, пока нет данных, и подхватывает
    // summary при его резолве (permanentная версия probe). Ловит регресс, если
    // TanStack Query перестанет переоценивать initialData на стабильном ключе.
    const qc = prodLikeClient();
    const seen = countCalculations();
    server.use(
      http.get("/api/dashboard/summary", () => HttpResponse.json(sampleSummaryMultiWithCalcs)),
    );
    function Harness() {
      const s = useDashboardSummary(1);
      const c = useDashboardCalculations(1, undefined, undefined, undefined, { enabled: !!s.data });
      return <div data-ready={s.data ? "1" : "0"} data-calc={c.data ? "1" : "0"} />;
    }
    const { container } = render(<Harness />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    });
    await waitFor(() => expect(container.querySelector('[data-ready="1"]')).toBeTruthy());
    await waitFor(() => expect(container.querySelector('[data-calc="1"]')).toBeTruthy());
    expect(seen).toHaveLength(0); // /calculations не ушёл ни разу за весь lifecycle
  });

  it("дефолт (период не задан): данные из посева, /calculations НЕ уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    const { result } = renderHook(
      () => useDashboardCalculations(1, undefined, undefined, undefined),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMultiWithCalcs) },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(sampleSummaryMultiWithCalcs.calculations!.length);
    expect(seen).toHaveLength(0);
  });

  it("direction-фильтр: чужая other-строка отсечена, /calculations не уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    const { result } = renderHook(
      () => useDashboardCalculations(1, undefined, undefined, "concrete"),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMonoWithCalcs) },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.every((r) => r.direction === "concrete")).toBe(true);
    expect(result.current.data!.some((r) => r.material_class_name === "Прочий материал")).toBe(false);
    expect(seen).toHaveLength(0);
  });

  it("изменённый период: посев не применяется, /calculations уходит на сервер", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    renderHook(
      () => useDashboardCalculations(1, "2026-02-01", "2026-02-28", undefined),
      { wrapper: wrapperWithSummary(qc, sampleSummaryMultiWithCalcs) },
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  });

  it("старый бэкенд без calculations: посева нет, /calculations уходит", async () => {
    const qc = prodLikeClient();
    const seen = countCalculations();
    renderHook(
      () => useDashboardCalculations(1, undefined, undefined, undefined),
      { wrapper: wrapperWithSummary(qc, sampleDashboardSummaryMulti) }, // без поля calculations
    );
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  });
});

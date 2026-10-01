import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, focusManager } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import {
  useConfirmSuggestions,
  useCreateFamilyFromSuggestion,
  useDiscardBatch,
  useOtherFamily,
  usePrivacyDecline,
  usePrivacyRelease,
  useQueueStatus,
  useReaskConfirm,
  useJobs,
  useRejectSuggestion,
  useResumeWorker,
  useSuggestions,
  useRetryJob,
  useUnitPrivacyRelease,
} from "./queries";
import { server } from "@/test/server";
import { createTestQueryClient } from "@/test/utils";

const SUGGESTIONS = JSON.stringify(["semantic-queue", "suggestions"]);
const JOBS = JSON.stringify(["semantic-queue", "jobs"]);
const STATUS = JSON.stringify(["semantic-queue", "status"]);

/** Запускает мутацию и возвращает ключи, которые она инвалидировала. */
async function invalidatedBy<T>(
  useHook: () => { mutateAsync: (vars: T) => Promise<unknown> },
  vars: T
): Promise<string[]> {
  const queryClient = createTestQueryClient();
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  const { result } = renderHook(useHook, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await result.current.mutateAsync(vars);
  });
  return spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
}

function answerAll() {
  server.use(
    http.post("/api/v1/semantic/*", () =>
      HttpResponse.json({ confirmed: [], skipped: [], claim_paused: false })
    )
  );
}

describe("решения экрана «Предложения»: какие запросы перечитываются", () => {
  it.each([
    ["подтверждение", () => invalidatedBy(useConfirmSuggestions, { ids: [1], familyTitle: "Ф", leftCount: 0 })],
    ["отклонение", () => invalidatedBy(useRejectSuggestion, 1)],
    ["другая семья", () => invalidatedBy(useOtherFamily, { suggestionId: 1, familyId: 2, familyTitle: "Ф" })],
    ["повтор задания", () => invalidatedBy(useRetryJob, 5)],
  ])("%s: очереди и задания, но не сводка", async (_name, run) => {
    answerAll();
    const keys = await run();

    expect(keys).toContain(SUGGESTIONS);
    expect(keys).toContain(JOBS);
    expect(keys).not.toContain(STATUS);
    expect(keys).not.toContain(JSON.stringify(["semantic-queue"]));
  });

  it.each([
    ["перезапрос единицы", () => invalidatedBy(useReaskConfirm, { target: { kind: "unit" as const, unitId: 3, unitCode: "M2" }, previewHash: "h" })],
    ["перезапрос по конфигурации", () => invalidatedBy(useReaskConfirm, { target: { kind: "config" as const }, previewHash: "h" })],
    ["постановка пачки", () => invalidatedBy(useReaskConfirm, { target: { kind: "batch" as const, batchId: 7, source: "mass" as const }, previewHash: "h" })],
    ["отброс пачки", () => invalidatedBy(useDiscardBatch, 7)],
    ["снятие остановки", () => invalidatedBy(useResumeWorker, undefined)],
    ["новая семья", () => invalidatedBy(useCreateFamilyFromSuggestion, { suggestionId: 1, input: { title: "Ф", definition: "Д" } })],
    ["отправка задержанного", () => invalidatedBy(usePrivacyRelease, { jobId: 1, shown: [] })],
    ["отказ от отправки задержанного", () => invalidatedBy(usePrivacyDecline, { jobId: 1, shown: [] })],
    ["отправка всех задержанных единицы", () => invalidatedBy(useUnitPrivacyRelease, { unitId: 3, shown: [] })],
  ])("%s: очереди, задания и сводка", async (_name, run) => {
    answerAll();
    const keys = await run();

    expect(keys).toEqual(expect.arrayContaining([SUGGESTIONS, JOBS, STATUS]));
  });
});

/** Мутация, которой сервер отказал, и ключи, которые её отказ инвалидировал. */
async function invalidatedByFailure<T>(
  useHook: () => { mutateAsync: (vars: T) => Promise<unknown> },
  vars: T
): Promise<string[]> {
  const queryClient = createTestQueryClient();
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  const { result } = renderHook(useHook, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await expect(result.current.mutateAsync(vars)).rejects.toBeTruthy();
  });
  return spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
}

function failAll() {
  server.use(
    http.post("/api/v1/semantic/*", () =>
      HttpResponse.json({ detail: { code: "conflict", message: "Отказ" } }, { status: 409 })
    )
  );
}

describe("отказ решения экрана «Предложения»: какие запросы перечитываются", () => {
  it.each([
    ["подтверждение", () => invalidatedByFailure(useConfirmSuggestions, { ids: [1], familyTitle: "Ф", leftCount: 0 })],
    ["отклонение", () => invalidatedByFailure(useRejectSuggestion, 1)],
    ["другая семья", () => invalidatedByFailure(useOtherFamily, { suggestionId: 1, familyId: 2, familyTitle: "Ф" })],
  ])("%s: очереди и задания, но не сводка", async (_name, run) => {
    failAll();
    const keys = await run();

    expect(keys).toContain(SUGGESTIONS);
    expect(keys).toContain(JOBS);
    expect(keys).not.toContain(STATUS);
  });

  it.each([
    ["отброс пачки", () => invalidatedByFailure(useDiscardBatch, 7)],
    ["снятие остановки", () => invalidatedByFailure(useResumeWorker, undefined)],
    ["новая семья", () => invalidatedByFailure(useCreateFamilyFromSuggestion, { suggestionId: 1, input: { title: "Ф", definition: "Д" } })],
    ["перезапрос единицы", () => invalidatedByFailure(useReaskConfirm, { target: { kind: "unit" as const, unitId: 3, unitCode: "M2" }, previewHash: "h" })],
    ["повтор задания", () => invalidatedByFailure(useRetryJob, 5)],
  ])("%s: очереди, задания и сводка", async (_name, run) => {
    failAll();
    const keys = await run();

    expect(keys).toEqual(expect.arrayContaining([SUGGESTIONS, JOBS, STATUS]));
  });
});

describe("useQueueStatus", () => {
  it("свежая сводка не перечитывается при повторном открытии вкладки", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setDefaultOptions({ queries: { retry: false, gcTime: 60_000 } });
    let calls = 0;
    server.use(
      http.get("/api/v1/semantic/status", () => {
        calls += 1;
        return HttpResponse.json({
          spent_24h_usd: "0",
          daily_budget_usd: "10",
          claim_paused: null,
          held_batches: [],
          stale_units: [],
          config_stale: null,
        });
      })
    );
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const first = renderHook(() => useQueueStatus(), { wrapper });
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    first.unmount();
    const second = renderHook(() => useQueueStatus(), { wrapper });
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true));

    expect(calls).toBe(1);
  });

  it("открытая вкладка перечитывает сводку раз в минуту, а не раньше", async () => {
    const queryClient = createTestQueryClient();
    let calls = 0;
    server.use(
      http.get("/api/v1/semantic/status", () => {
        calls += 1;
        return HttpResponse.json({
          spent_24h_usd: "0",
          daily_budget_usd: "10",
          claim_paused: null,
          held_batches: [],
          stale_units: [],
          config_stale: null,
        });
      })
    );
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    // Таймеры подменяются ДО монтирования: интервал опроса заводится при подписке.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const view = renderHook(() => useQueueStatus(), { wrapper });
      await vi.waitFor(() => expect(view.result.current.isSuccess).toBe(true));
      expect(calls).toBe(1);

      await vi.advanceTimersByTimeAsync(59_000);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(2_000);
      await vi.waitFor(() => expect(calls).toBe(2));
    } finally {
      vi.useRealTimers();
    }
  });

  it("фоновая вкладка сводку не опрашивает", async () => {
    const queryClient = createTestQueryClient();
    let calls = 0;
    server.use(
      http.get("/api/v1/semantic/status", () => {
        calls += 1;
        return HttpResponse.json({
          spent_24h_usd: "0",
          daily_budget_usd: "10",
          claim_paused: null,
          held_batches: [],
          stale_units: [],
          config_stale: null,
        });
      })
    );
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const view = renderHook(() => useQueueStatus(), { wrapper });
      await vi.waitFor(() => expect(view.result.current.isSuccess).toBe(true));
      expect(calls).toBe(1);

      // Вкладка ушла в фон: интервал тикает, но запроса нет.
      focusManager.setFocused(false);
      await vi.advanceTimersByTimeAsync(61_000);
      await vi.advanceTimersByTimeAsync(61_000);
      expect(calls).toBe(1);
    } finally {
      focusManager.setFocused(undefined);
      vi.useRealTimers();
    }
  });
});

describe("очереди и задания: опрос раз в минуту", () => {
  function countingServer() {
    const calls = { suggestions: 0, jobs: 0 };
    server.use(
      http.get("/api/v1/semantic/suggestions", () => {
        calls.suggestions += 1;
        return HttpResponse.json({ groups: [], items: [], total: 0 });
      }),
      http.get("/api/v1/semantic/jobs", () => {
        calls.jobs += 1;
        return HttpResponse.json({ items: [] });
      })
    );
    return calls;
  }

  function wrapperFor() {
    const queryClient = createTestQueryClient();
    return ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }

  it("смонтированные очередь и задания перечитываются через минуту, не раньше", async () => {
    const calls = countingServer();
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const wrapper = wrapperFor();
      const q = renderHook(() => useSuggestions({ queue: "new" }), { wrapper });
      const j = renderHook(() => useJobs("error"), { wrapper });
      await vi.waitFor(() => {
        expect(q.result.current.isSuccess).toBe(true);
        expect(j.result.current.isSuccess).toBe(true);
      });
      expect(calls).toEqual({ suggestions: 1, jobs: 1 });

      await vi.advanceTimersByTimeAsync(59_000);
      expect(calls).toEqual({ suggestions: 1, jobs: 1 });
      await vi.advanceTimersByTimeAsync(2_000);
      await vi.waitFor(() => expect(calls).toEqual({ suggestions: 2, jobs: 2 }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("фоновая вкладка и размонтированная очередь не опрашиваются", async () => {
    const calls = countingServer();
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const wrapper = wrapperFor();
      const q = renderHook(() => useSuggestions({ queue: "new" }), { wrapper });
      const j = renderHook(() => useJobs("error"), { wrapper });
      await vi.waitFor(() => {
        expect(q.result.current.isSuccess).toBe(true);
        expect(j.result.current.isSuccess).toBe(true);
      });

      focusManager.setFocused(false);
      await vi.advanceTimersByTimeAsync(61_000);
      await vi.advanceTimersByTimeAsync(61_000);
      expect(calls).toEqual({ suggestions: 1, jobs: 1 });

      focusManager.setFocused(undefined);
      q.unmount();
      j.unmount();
      await vi.advanceTimersByTimeAsync(61_000);
      expect(calls).toEqual({ suggestions: 1, jobs: 1 });
    } finally {
      focusManager.setFocused(undefined);
      vi.useRealTimers();
    }
  });
});

import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient } from "@/test/utils";

import {
  useAssignFamily,
  useCancelPendingFamily,
  useChangeQueue,
  useMarkNotWork,
  useMarkPositionKind,
  useReopenContext,
} from "./queries";

/**
 * Действия над контекстом и строкой каталога (спека `2026-10-02-catalog-variants-design.md`
 * §2.12): после каждого перечитываются карточка и очередь контекстов, семьи, очереди
 * предложений и счётчики шапки — все они зависят от семьи, варианта и состояния контекста.
 */

const CONTEXTS = JSON.stringify(["semantic-contexts"]);
const CARD = JSON.stringify(["semantic-contexts", "card", 601]);
const STATUS = JSON.stringify(["semantic-queue", "status"]);
const SUGGESTIONS = JSON.stringify(["semantic-queue", "suggestions"]);
const FAMILIES = JSON.stringify(["work-families"]);

function wrapperFor(queryClient: ReturnType<typeof createTestQueryClient>) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

async function invalidatedBy<T>(
  useHook: () => { mutateAsync: (vars: T) => Promise<unknown> },
  vars: T
): Promise<string[]> {
  const queryClient = createTestQueryClient();
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  const { result } = renderHook(useHook, { wrapper: wrapperFor(queryClient) });
  await act(async () => {
    await result.current.mutateAsync(vars);
  });
  return spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
}

describe("действия над контекстом: какие запросы перечитываются", () => {
  it.each([
    ["смена семьи", () => invalidatedBy(useAssignFamily, { contextId: 601, input: { family_id: 43 } })],
    ["отмена ожидания", () => invalidatedBy(useCancelPendingFamily, 601)],
    ["«не работа»", () => invalidatedBy(useMarkNotWork, 601)],
    // Ревью задачи 5: «Вернуть в разбор» меняет состояние контекста так же, как «не работа».
    ["«вернуть в разбор»", () => {
      handlerState.semanticContexts.find((c) => c.id === 601)!.semantic_state = "NOT_APPLICABLE";
      return invalidatedBy(useReopenContext, 601);
    }],
  ])("%s: карточка, очередь контекстов, семьи, очереди и сводка", async (_name, run) => {
    const keys = await run();

    expect(keys).toEqual(expect.arrayContaining([CONTEXTS, CARD, STATUS, SUGGESTIONS, FAMILIES]));
  });

  it("глобальная пометка строки: контексты, очереди и сводка", async () => {
    const keys = await invalidatedBy(useMarkPositionKind, { positionId: 8001, kind: "HEADER" as const });

    expect(keys).toEqual(expect.arrayContaining([CONTEXTS, STATUS, SUGGESTIONS, FAMILIES]));
  });
});

describe("отказ действия над контекстом", () => {
  it("«не работа» с отказом всё равно перечитывает карточку: экран мог устареть", async () => {
    server.use(
      http.post("/api/v1/semantic/contexts/:id/not-work", () =>
        HttpResponse.json({ detail: { code: "context_archived", message: "x" } }, { status: 409 })
      )
    );
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useMarkNotWork(), { wrapper: wrapperFor(queryClient) });

    await act(async () => {
      await expect(result.current.mutateAsync(601)).rejects.toBeTruthy();
    });

    expect(spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))).toContain(CARD);
  });

  it("отмена ожидания с отказом всё равно перечитывает карточку: экран мог устареть", async () => {
    handlerState.contextRefusal = { action: "cancel-pending", code: "family_lock_mismatch", status: 409 };
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCancelPendingFamily(), { wrapper: wrapperFor(queryClient) });

    await act(async () => {
      await expect(result.current.mutateAsync(601)).rejects.toBeTruthy();
    });

    expect(spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))).toContain(CARD);
  });

  it("«вернуть в разбор» с отказом всё равно перечитывает карточку: экран мог устареть", async () => {
    handlerState.contextRefusal = { action: "reopen", code: "context_not_reopenable_state", status: 409 };
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useReopenContext(), { wrapper: wrapperFor(queryClient) });

    await act(async () => {
      await expect(result.current.mutateAsync(601)).rejects.toBeTruthy();
    });

    expect(spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))).toContain(CARD);
  });

  it("глобальная пометка с отказом ничего не перечитывает: диалог остаётся с причиной", async () => {
    handlerState.positionStandards[8001] = [
      { id: 71, rate_class_id: 3, rate_class_title: "Класс Б3", valid_from: "2026-03-01", valid_to: null },
    ];
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useMarkPositionKind(), { wrapper: wrapperFor(queryClient) });

    await act(async () => {
      await expect(result.current.mutateAsync({ positionId: 8001, kind: "HEADER" })).rejects.toBeTruthy();
    });

    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useChangeQueue", () => {
  it("читает очередь смены семьи и отдаёт её группы с прежней семьёй", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useChangeQueue({}, { poll: false }), {
      wrapper: wrapperFor(queryClient),
    });

    await vi.waitFor(() => expect(result.current.groups).toHaveLength(2), { timeout: 8000 });
    expect(result.current.groups[0].from_family_title).toBe("Кровельные работы");
    expect(handlerState.suggestionsRequests.at(-1)).toContain("queue=change");
  });

  it("до ответа групп нет, а не undefined", () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useChangeQueue({}, { poll: false }), {
      wrapper: wrapperFor(queryClient),
    });

    expect(result.current.groups).toEqual([]);
  });
});

import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useAwardWinner,
  useContractCandidates,
  useLinkContract,
  useMarkNotConcluded,
  useRemoveAward,
} from "./queries";
import { qk } from "./queryKeys";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient } from "@/test/utils";

function wrapperFor(queryClient: ReturnType<typeof createTestQueryClient>) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function invalidatedKeys(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.map(([filters]) =>
    JSON.stringify((filters as { queryKey?: unknown } | undefined)?.queryKey)
  );
}

/**
 * Команды отметки победителя (спека Б2 §2.6): тело запроса и то, что после
 * успеха перечитывается. Карточка тендера — всегда; карточка договора — только
 * у привязки (у договора появилось основание).
 */
describe("команды отметки победителя", () => {
  afterEach(() => vi.restoreAllMocks());

  it("useAwardWinner: POST с offer_id, перечитывает карточку тендера", async () => {
    handlerState.tenderRoundState = "both-loaded-with-beta";
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useAwardWinner(300), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync(7002));

    expect(handlerState.lastAwardCommand).toEqual({
      kind: "award",
      tenderId: 300,
      awardId: null,
      body: { offer_id: 7002 },
    });
    expect(invalidatedKeys(spy)).toContain(JSON.stringify(qk.tenders.card(300)));
  });

  it("useRemoveAward: DELETE по id отметки, перечитывает карточку тендера", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useRemoveAward(300), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync(7));

    expect(handlerState.lastAwardCommand).toMatchObject({ kind: "remove", tenderId: 300, awardId: 7 });
    expect(invalidatedKeys(spy)).toContain(JSON.stringify(qk.tenders.card(300)));
  });

  it("useMarkNotConcluded: тело несёт дату и комментарий; пустой комментарий — явный null", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useMarkNotConcluded(300), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ awardId: 7, notConcludedOn: "2026-01-15", note: null }));

    // Точное равенство: утёкшее поле прошло бы частичный матчер молча.
    expect(handlerState.lastAwardCommand?.body).toEqual({ not_concluded_on: "2026-01-15", note: null });
    expect(handlerState.lastAwardCommand).toMatchObject({ kind: "not_concluded", awardId: 7 });
    expect(invalidatedKeys(spy)).toContain(JSON.stringify(qk.tenders.card(300)));
  });

  it("useContractCandidates: без id отметки запроса нет, с id — список кандидатов", async () => {
    const qc = createTestQueryClient();
    const idle = renderHook(() => useContractCandidates(300, undefined), { wrapper: wrapperFor(qc) });
    expect(idle.result.current.fetchStatus).toBe("idle");

    const live = renderHook(() => useContractCandidates(300, 7), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(live.result.current.isSuccess).toBe(true));
    expect(live.result.current.data?.map((c) => c.id)).toEqual([201, 202]);
  });

  // Ревью задачи 9: в приложении staleTime 60 с, поэтому без refetchOnMount
  // "always" повторно открытое окно показало бы список из кэша.
  it("useContractCandidates: окно открыто заново — список перезапрашивается и при свежем кэше", async () => {
    const qc = createTestQueryClient();
    qc.setDefaultOptions({ queries: { retry: false, staleTime: 60_000, gcTime: Infinity } });
    let calls = 0;
    server.use(
      http.get("/api/v1/tenders/:id/awards/:aid/contract-candidates", () => {
        calls += 1;
        return HttpResponse.json(handlerState.awardCandidates);
      })
    );
    const first = renderHook(() => useContractCandidates(300, 7), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    first.unmount();

    renderHook(() => useContractCandidates(300, 7), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(calls).toBe(2));
  });

  // Ревью задачи 9: кэш кандидатов — свой у каждой отметки.
  it("useContractCandidates: список другой отметки не подставляется из кэша", async () => {
    const qc = createTestQueryClient();
    const { result, rerender } = renderHook(({ awardId }) => useContractCandidates(300, awardId), {
      wrapper: wrapperFor(qc),
      initialProps: { awardId: 7 },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    rerender({ awardId: 8 });

    expect(result.current.data).toBeUndefined();
  });

  it("useLinkContract: POST с contract_id, перечитывает карточку тендера И карточку договора", async () => {
    const qc = createTestQueryClient();
    // Без наблюдателя запись с gcTime 0 исчезает сразу — признак устаревания не прочесть.
    qc.setQueryDefaults(qk.contracts.card(201), { gcTime: Infinity });
    qc.setQueryData(qk.contracts.card(201), { id: 201 });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useLinkContract(300), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ awardId: 7, contractId: 201 }));

    expect(handlerState.lastAwardCommand).toEqual({
      kind: "link",
      tenderId: 300,
      awardId: 7,
      body: { contract_id: 201 },
    });
    const keys = invalidatedKeys(spy);
    expect(keys).toContain(JSON.stringify(qk.tenders.card(300)));
    expect(qc.getQueryState(qk.contracts.card(201))?.isInvalidated).toBe(true);
  });

  it("отказ сервера не инвалидирует карточку тендера", async () => {
    server.use(
      http.post("/api/v1/tenders/:id/awards", () =>
        HttpResponse.json({ detail: "В тендере уже отмечен победитель." }, { status: 409 })
      )
    );
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useAwardWinner(300), { wrapper: wrapperFor(qc) });
    await act(async () => {
      await result.current.mutateAsync(7002).catch(() => undefined);
    });

    expect(result.current.isError).toBe(true);
    expect(invalidatedKeys(spy)).not.toContain(JSON.stringify(qk.tenders.card(300)));
  });
});

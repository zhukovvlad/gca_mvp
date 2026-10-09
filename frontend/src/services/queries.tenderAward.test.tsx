import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useAwardWinner,
  useContractCandidates,
  useDeleteContract,
  useLinkContract,
  useMarkNotConcluded,
  useRemoveAward,
  useTender,
  useUpdateContract,
} from "./queries";
import { qk } from "./queryKeys";
import { sampleTenderAward, sampleTenderAwardWithContract } from "@/test/fixtures";
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

/**
 * Правка и удаление договора и карточка тендера: плашка победителя берёт номер,
 * дату и наличие договора из ответа карточки тендера. В приложении staleTime
 * 60 с, поэтому проверка при staleTime 0 прятала бы дефект: кэш тут свежий.
 */
describe("договор меняется, карточка тендера свежа в кэше", () => {
  afterEach(() => vi.restoreAllMocks());

  function freshClientWithCards() {
    const qc = createTestQueryClient();
    qc.setDefaultOptions({ queries: { retry: false, staleTime: 60_000, gcTime: Infinity } });
    qc.setQueryData(qk.tenders.card(300), { id: 300 });
    qc.setQueryData(qk.tenders.card(301), { id: 301 });
    qc.setQueryData(qk.tenders.list(), []);
    return qc;
  }

  function expectCardsInvalidated(qc: ReturnType<typeof createTestQueryClient>) {
    expect(qc.getQueryState(qk.tenders.card(300))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(qk.tenders.card(301))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(qk.tenders.list())?.isInvalidated).toBe(false);
  }

  it("useDeleteContract: все карточки тендеров устарели, список тендеров нет", async () => {
    const qc = freshClientWithCards();
    expect(qc.getQueryState(qk.tenders.card(300))?.isInvalidated).toBe(false);
    const { result } = renderHook(() => useDeleteContract(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync(201));
    expectCardsInvalidated(qc);
  });

  it("useUpdateContract: все карточки тендеров устарели, список тендеров нет", async () => {
    const qc = freshClientWithCards();
    expect(qc.getQueryState(qk.tenders.card(300))?.isInvalidated).toBe(false);
    const { result } = renderHook(() => useUpdateContract(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ id: 201, input: { contract_number: "99" } }));
    expectCardsInvalidated(qc);
  });

  // Ревью правки: сценарий замечания целиком, через настоящий наблюдатель
  // карточки. Тендер открыт (useTender), пользователь ушёл в договор
  // (наблюдатель снят), изменил его и вернулся, пока кэш свежий. Признак
  // isInvalidated доказывает пометку; здесь — что вернувшийся useTender
  // действительно перезапрашивает и отдаёт плашке новый award.contract.
  async function openTenderThenLeave() {
    const qc = createTestQueryClient();
    qc.setDefaultOptions({ queries: { retry: false, staleTime: 60_000, gcTime: Infinity } });
    handlerState.tenderAwardState = { award: sampleTenderAwardWithContract, history: [] };
    const first = renderHook(() => useTender(300), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(first.result.current.data?.award?.contract?.id).toBe(100));
    first.unmount();
    return qc;
  }

  it("useDeleteContract: вернувшийся на тендер useTender видит отметку без договора", async () => {
    const qc = await openTenderThenLeave();

    // Сервер удалил договор: отметка осталась, договора у неё нет.
    handlerState.tenderAwardState = { award: sampleTenderAward, history: [] };
    const del = renderHook(() => useDeleteContract(), { wrapper: wrapperFor(qc) });
    await act(() => del.result.current.mutateAsync(100));

    const back = renderHook(() => useTender(300), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(back.result.current.data?.award?.contract).toBeNull());
    expect(back.result.current.data?.award?.id).toBe(sampleTenderAward.id);
  });

  it("useUpdateContract: вернувшийся на тендер useTender видит новые номер и дату", async () => {
    const qc = await openTenderThenLeave();

    const edited = { id: 100, contract_number: "46/2026-ГП", signed_date: "2026-07-01" };
    handlerState.tenderAwardState = {
      award: { ...sampleTenderAwardWithContract, contract: edited },
      history: [],
    };
    const upd = renderHook(() => useUpdateContract(), { wrapper: wrapperFor(qc) });
    await act(() =>
      upd.result.current.mutateAsync({
        id: 100,
        input: { contract_number: edited.contract_number, signed_date: edited.signed_date },
      })
    );

    const back = renderHook(() => useTender(300), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(back.result.current.data?.award?.contract).toEqual(edited));
  });
});

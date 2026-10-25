import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_SERIES_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";

import { tendersApi } from "./api/domain";
import { qk } from "./queryKeys";
import {
  apiErrorCode,
  OUR_PRICE_POLL_MS,
  useOurPriceLine,
  useOurPriceOffer,
  useOurPriceRound,
} from "./queries";

/**
 * «Против нашей цены»: адреса, опрос, поток обновления и `placeholderData`
 * (спека 4а §2.11, §2.12). Без `sleep`: опрос — фейковые интервалы, ожидание —
 * по счётчику запросов MSW.
 */

const T = OUR_PRICE_TENDER_ID;
const R = OUR_PRICE_ROUND_ID;
const O = OUR_PRICE_OFFER_ID;
const P = OUR_PRICE_LINE_ID;
const S = OUR_PRICE_SERIES_ID;

/** Клиент, в котором неактивные запросы не удаляются: тест читает флаг `isInvalidated`. */
function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 } },
  });
}

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

const requestsOf = (kind: "round" | "offer" | "line") =>
  handlerState.ourPriceRequests.filter((r) => r.kind === kind);

const urlOf = (kind: "round" | "offer" | "line", index = 0) =>
  new URL(requestsOf(kind)[index].url);

/** Интервалы подменяются ДО монтирования: интервал опроса заводится при подписке. */
function fakeIntervals() {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
}

afterEach(() => {
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("адреса", () => {
  it("seriesId = null: запрос этапа и разбора без inflation_series_id, с рядом - с ним", async () => {
    const queryClient = makeClient();
    const wrapper = wrapperFor(queryClient);
    const { result, rerender } = renderHook(
      ({ series }: { series: number | null }) => ({
        round: useOurPriceRound(T, R, series),
        offer: useOurPriceOffer(T, R, O, series),
      }),
      { wrapper, initialProps: { series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.offer.isSuccess).toBe(true));
    expect(urlOf("round").pathname).toBe(`/api/v1/tenders/${T}/rounds/${R}/our-price`);
    expect(urlOf("round").searchParams.has("inflation_series_id")).toBe(false);
    expect(urlOf("offer").pathname).toBe(`/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}`);
    expect(urlOf("offer").searchParams.has("inflation_series_id")).toBe(false);

    rerender({ series: S });
    await vi.waitFor(() => expect(requestsOf("offer")).toHaveLength(2));
    expect(urlOf("round", 1).searchParams.get("inflation_series_id")).toBe(String(S));
    expect(urlOf("offer", 1).searchParams.get("inflation_series_id")).toBe(String(S));
  });

  it("ourPriceLine: rung = null без level и scope, с rung - обе величины", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ rung }: { rung: { level: "v" | "f"; scope: "cls" | "all" } | null }) =>
        useOurPriceLine(T, R, O, P, rung, null),
      { wrapper: wrapperFor(queryClient), initialProps: { rung: null as { level: "v" | "f"; scope: "cls" | "all" } | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    const auto = urlOf("line");
    expect(auto.pathname).toBe(`/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/lines/${P}`);
    expect(auto.searchParams.has("level")).toBe(false);
    expect(auto.searchParams.has("scope")).toBe(false);
    expect(auto.searchParams.has("inflation_series_id")).toBe(false);

    rerender({ rung: { level: "f", scope: "all" } });
    await vi.waitFor(() => expect(requestsOf("line")).toHaveLength(2));
    const manual = urlOf("line", 1);
    expect(manual.searchParams.get("level")).toBe("f");
    expect(manual.searchParams.get("scope")).toBe("all");
  });

  it("ourPriceLine: смена ряда - новый запрос с inflation_series_id (ряд входит в ключ панели)", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ series }: { series: number | null }) => useOurPriceLine(T, R, O, P, null, series),
      { wrapper: wrapperFor(queryClient), initialProps: { series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    rerender({ series: S });
    await vi.waitFor(() => expect(requestsOf("line")).toHaveLength(2));
    expect(urlOf("line", 1).searchParams.get("inflation_series_id")).toBe(String(S));
  });

  it("enabled: false у этапа и разбора - запроса нет", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(
      () => ({
        round: useOurPriceRound(T, R, null, { enabled: false }),
        offer: useOurPriceOffer(T, R, O, null, { enabled: false }),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
    // Соседний включённый запрос того же клиента: MSW живой, счётчики не пусты по случайности.
    const { result: line } = renderHook(() => useOurPriceLine(T, R, O, P, null, null), {
      wrapper: wrapperFor(queryClient),
    });
    await vi.waitFor(() => expect(line.current.isSuccess).toBe(true));
    expect(result.current.round.fetchStatus).toBe("idle");
    expect(result.current.offer.fetchStatus).toBe("idle");
    expect(requestsOf("round")).toHaveLength(0);
    expect(requestsOf("offer")).toHaveLength(0);
  });

  it("ourPriceLine без строки (positionItemId = null) запроса не шлёт", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(() => useOurPriceLine(T, R, O, null, null, null), {
      wrapper: wrapperFor(queryClient),
    });
    // Соседний запрос того же клиента доказывает, что MSW живой и счётчик не пуст по случайности.
    const { result: round } = renderHook(() => useOurPriceRound(T, R, null), {
      wrapper: wrapperFor(queryClient),
    });
    await vi.waitFor(() => expect(round.current.isSuccess).toBe(true));
    expect(result.current.fetchStatus).toBe("idle");
    expect(requestsOf("line")).toHaveLength(0);
  });
});

describe("опрос", () => {
  it.each([
    ["этап", "ourPriceRoundInProgress" as const, "round" as const],
    ["разбор", "ourPriceOfferInProgress" as const, "offer" as const],
  ])("%s: опрашивает раз в OUR_PRICE_POLL_MS, пока in_progress > 0, и замолкает на нуле", async (_n, stateKey, kind) => {
    handlerState[stateKey] = [3, 1, 0];
    fakeIntervals();
    const queryClient = makeClient();
    const { result } = renderHook(
      () => (kind === "round" ? useOurPriceRound(T, R, null) : useOurPriceOffer(T, R, O, null)),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestsOf(kind)).toHaveLength(1);

    // `vi.waitFor` сам двигает фейковые часы, поэтому до границы интервала берём запас.
    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS - 1000);
    expect(requestsOf(kind)).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(requestsOf(kind)).toHaveLength(2));
    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS);
    await vi.waitFor(() => expect(requestsOf(kind)).toHaveLength(3));
    await vi.waitFor(() => expect(result.current.data?.progress.in_progress).toBe(0));

    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS * 4);
    expect(requestsOf(kind)).toHaveLength(3);
  });

  it.each([
    ["этап", "round" as const],
    ["разбор", "offer" as const],
  ])("%s: при in_progress = 0 повторного запроса нет", async (_n, kind) => {
    fakeIntervals();
    const queryClient = makeClient();
    const { result } = renderHook(
      () => (kind === "round" ? useOurPriceRound(T, R, null) : useOurPriceOffer(T, R, O, null)),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS * 4);
    expect(requestsOf(kind)).toHaveLength(1);
  });

  it.each([
    ["этап", "ourPriceRoundInProgress" as const, "round" as const],
    ["разбор", "ourPriceOfferInProgress" as const, "offer" as const],
  ])("%s: в фоновой вкладке не опрашивает", async (_n, stateKey, kind) => {
    handlerState[stateKey] = [3];
    fakeIntervals();
    const queryClient = makeClient();
    const { result } = renderHook(
      () => (kind === "round" ? useOurPriceRound(T, R, null) : useOurPriceOffer(T, R, O, null)),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    focusManager.setFocused(false);
    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS * 4);
    expect(requestsOf(kind)).toHaveLength(1);
  });
});

describe("поток обновления", () => {
  const offerKey = (roundId: number) => qk.tenders.ourPriceOffer(T, roundId, O, null);
  const prefetchOffer = (queryClient: QueryClient, roundId: number) =>
    queryClient.prefetchQuery({
      queryKey: offerKey(roundId),
      queryFn: () => tendersApi.ourPriceOffer(T, roundId, O, null),
    });

  it("опрос этапа 2 → 0 инвалидирует разбор участника этого этапа, а не другого", async () => {
    const queryClient = makeClient();
    await prefetchOffer(queryClient, R);
    await prefetchOffer(queryClient, R + 1);
    const offersBefore = requestsOf("offer").length;
    handlerState.ourPriceRoundInProgress = [2, 0];
    fakeIntervals();
    const { result } = renderHook(() => useOurPriceRound(T, R, null), {
      wrapper: wrapperFor(queryClient),
    });
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    // Первый ответ этапа сравнивать не с чем: ничего не инвалидируется.
    expect(queryClient.getQueryState(offerKey(R))?.isInvalidated).toBe(false);

    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS);
    await vi.waitFor(() => expect(requestsOf("round")).toHaveLength(2));
    await vi.waitFor(() => expect(queryClient.getQueryState(offerKey(R))?.isInvalidated).toBe(true));
    expect(queryClient.getQueryState(offerKey(R + 1))?.isInvalidated).toBe(false);

    // «Следующее чтение идёт в сеть»: даже при `staleTime: Infinity`.
    await queryClient.fetchQuery({
      queryKey: offerKey(R),
      queryFn: () => tendersApi.ourPriceOffer(T, R, O, null),
      staleTime: Infinity,
    });
    expect(requestsOf("offer")).toHaveLength(offersBefore + 1);
  });

  it("опрос этапа без прироста разобранных строк разбор не инвалидирует", async () => {
    const queryClient = makeClient();
    await prefetchOffer(queryClient, R);
    handlerState.ourPriceRoundInProgress = [2, 2];
    fakeIntervals();
    const { result } = renderHook(() => useOurPriceRound(T, R, null), {
      wrapper: wrapperFor(queryClient),
    });
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    const firstAnswerAt = result.current.dataUpdatedAt;
    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS);
    // Второй ответ уже применён (а не только запрошен), прежде чем проверять флаг.
    await vi.waitFor(() => expect(result.current.dataUpdatedAt).toBeGreaterThan(firstAnswerAt));
    expect(requestsOf("round")).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(0);
    expect(queryClient.getQueryState(offerKey(R))?.isInvalidated).toBe(false);
  });

  it("опрос разбора 1 → 0 инвалидирует открытую панель: она перезапрашивается и уже не in_progress", async () => {
    handlerState.ourPriceOfferInProgress = [1, 0];
    handlerState.ourPriceLineStates = ["in_progress", "comparable"];
    fakeIntervals();
    const queryClient = makeClient();
    const { result } = renderHook(
      () => ({
        offer: useOurPriceOffer(T, R, O, null),
        line: useOurPriceLine(T, R, O, P, null, null),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.line.data?.line.state).toBe("in_progress"));
    // Первый ответ разбора панель не трогает.
    expect(requestsOf("line")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS);
    await vi.waitFor(() => expect(requestsOf("offer")).toHaveLength(2));
    await vi.waitFor(() => expect(requestsOf("line")).toHaveLength(2));
    await vi.waitFor(() => expect(result.current.line.data?.line.state).toBe("comparable"));
  });

  it("новый ответ разбора не трогает панели другого участника и не запрашивает закрытые панели", async () => {
    const linePath = (offerId: number, pid: number) =>
      `/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${offerId}/lines/${pid}`;
    const lineRequests = (offerId: number, pid: number) =>
      requestsOf("line").filter((r) => new URL(r.url).pathname === linePath(offerId, pid));
    const otherOffer = O + 1;
    const closedLine = P + 1;
    const otherKey = qk.tenders.ourPriceLine(T, R, otherOffer, P, null, null, null);
    const closedKey = qk.tenders.ourPriceLine(T, R, O, closedLine, null, null, null);
    const queryClient = makeClient();
    // Закешированы, но не открыты: панель другого участника и закрытая панель этого.
    await queryClient.prefetchQuery({
      queryKey: otherKey,
      queryFn: () => tendersApi.ourPriceLine(T, R, otherOffer, P, null, null),
    });
    await queryClient.prefetchQuery({
      queryKey: closedKey,
      queryFn: () => tendersApi.ourPriceLine(T, R, O, closedLine, null, null),
    });
    handlerState.ourPriceOfferInProgress = [1, 0];
    fakeIntervals();
    const { result } = renderHook(
      () => ({
        offer: useOurPriceOffer(T, R, O, null),
        line: useOurPriceLine(T, R, O, P, null, null),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.line.isSuccess).toBe(true));
    expect(lineRequests(O, P)).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS);
    await vi.waitFor(() => expect(lineRequests(O, P)).toHaveLength(2));
    // Префикс — панели ЭТОГО участника: чужая не помечена, закрытая своя помечена, но в сеть не ушла.
    expect(queryClient.getQueryState(otherKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(closedKey)?.isInvalidated).toBe(true);
    expect(lineRequests(O, closedLine)).toHaveLength(1);
    expect(lineRequests(otherOffer, P)).toHaveLength(1);
  });

  it("разбор: placeholder-данные смены ряда панель не инвалидируют, следующий настоящий ответ - инвалидирует", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ series }: { series: number | null }) => ({
        offer: useOurPriceOffer(T, R, O, series),
        // Ключ панели неизменен: её перезапрос может вызвать только инвалидация разбором.
        line: useOurPriceLine(T, R, O, P, null, null),
      }),
      { wrapper: wrapperFor(queryClient), initialProps: { series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.line.isSuccess).toBe(true));
    await vi.waitFor(() => expect(result.current.offer.isSuccess).toBe(true));
    expect(requestsOf("line")).toHaveLength(1);

    rerender({ series: S });
    expect(result.current.offer.isPlaceholderData).toBe(true);
    await vi.waitFor(() => expect(result.current.offer.data?.inflation?.series_id).toBe(S));
    expect(result.current.offer.isPlaceholderData).toBe(false);
    await act(async () => {});
    // Первый настоящий ответ нового ключа — первый, а placeholder ответом не считается.
    expect(requestsOf("line")).toHaveLength(1);

    // Контроль: второй настоящий ответ того же ключа панель перезапрашивает.
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: qk.tenders.ourPriceOffer(T, R, O, S) });
    });
    await vi.waitFor(() => expect(requestsOf("line")).toHaveLength(2));
  });

  it("placeholder-данные инвалидации не вызывают: первый ответ нового ряда не сравнивается с чужим", async () => {
    const queryClient = makeClient();
    await prefetchOffer(queryClient, R);
    handlerState.ourPriceRoundInProgress = [5, 3];
    const { result, rerender } = renderHook(
      ({ series }: { series: number | null }) => useOurPriceRound(T, R, series),
      { wrapper: wrapperFor(queryClient), initialProps: { series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.data?.progress.in_progress).toBe(5));

    // Ряд сменился: на экране прежние данные (5 не разобрано), настоящий ответ — 3.
    rerender({ series: S });
    expect(result.current.isPlaceholderData).toBe(true);
    await vi.waitFor(() => expect(result.current.data?.progress.in_progress).toBe(3));
    expect(result.current.isPlaceholderData).toBe(false);
    expect(queryClient.getQueryState(offerKey(R))?.isInvalidated).toBe(false);
  });
});

describe("placeholderData", () => {
  it("смена ряда у того же этапа отдаёт прежние данные, смена этапа - нет", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ round, series }: { round: number; series: number | null }) => useOurPriceRound(T, round, series),
      { wrapper: wrapperFor(queryClient), initialProps: { round: R, series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    const before = result.current.data;

    rerender({ round: R, series: S });
    expect(result.current.isPlaceholderData).toBe(true);
    expect(result.current.data).toBe(before);
    await vi.waitFor(() => expect(result.current.isPlaceholderData).toBe(false));

    rerender({ round: R + 1, series: S });
    expect(result.current.isPlaceholderData).toBe(false);
    expect(result.current.data).toBeUndefined();
  });

  it("разбор: смена ряда оставляет данные, смена участника - нет", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ offer, series }: { offer: number; series: number | null }) => useOurPriceOffer(T, R, offer, series),
      { wrapper: wrapperFor(queryClient), initialProps: { offer: O, series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));

    rerender({ offer: O, series: S });
    expect(result.current.isPlaceholderData).toBe(true);
    expect(result.current.data).toBeDefined();
    await vi.waitFor(() => expect(result.current.isPlaceholderData).toBe(false));

    rerender({ offer: O + 1, series: S });
    expect(result.current.isPlaceholderData).toBe(false);
    expect(result.current.data).toBeUndefined();
  });

  it("панель: смена ступени оставляет данные, смена строки - нет", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ pid, level }: { pid: number; level: "v" | "f" | null }) =>
        useOurPriceLine(T, R, O, pid, level === null ? null : { level, scope: "cls" }, null),
      { wrapper: wrapperFor(queryClient), initialProps: { pid: P, level: null as "v" | "f" | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));

    rerender({ pid: P, level: "f" });
    expect(result.current.isPlaceholderData).toBe(true);
    expect(result.current.data).toBeDefined();
    await vi.waitFor(() => expect(result.current.isPlaceholderData).toBe(false));

    rerender({ pid: P + 1, level: "f" });
    expect(result.current.isPlaceholderData).toBe(false);
    expect(result.current.data).toBeUndefined();
  });
});

describe("отказ не повторяется при retry: 1 по умолчанию (как у клиента App.tsx)", () => {
  it.each([["этап"], ["разбор"], ["панель"]] as const)("%s: один запрос на 422", async (which) => {
    handlerState.ourPriceRefusal = "inflation_target_missing";
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: 1, gcTime: Infinity, staleTime: 0 } },
    });
    const kind = which === "этап" ? "round" : which === "разбор" ? "offer" : "line";
    const { result } = renderHook(
      () =>
        which === "этап"
          ? useOurPriceRound(T, R, S)
          : which === "разбор"
            ? useOurPriceOffer(T, R, O, S)
            : useOurPriceLine(T, R, O, P, null, S),
      { wrapper: wrapperFor(queryClient) }
    );
    // Запас больше задержки повтора по умолчанию (1 с): с повтором ошибка пришла бы после второго запроса.
    await vi.waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(requestsOf(kind)).toHaveLength(1);
  });
});

describe("отказы приведения", () => {
  it.each([
    ["missing_inflation_years"],
    ["amendment_date_missing"],
    ["inflation_target_missing"],
  ] as const)("%s: с рядом - 422 с кодом, без ряда - 200", async (code) => {
    handlerState.ourPriceRefusal = code;
    const queryClient = makeClient();
    const { result } = renderHook(
      () => ({ withSeries: useOurPriceRound(T, R, S), nominal: useOurPriceRound(T, R, null) }),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.nominal.isSuccess).toBe(true));
    await vi.waitFor(() => expect(result.current.withSeries.isError).toBe(true));
    expect(apiErrorCode(result.current.withSeries.error)).toBe(code);
  });
});

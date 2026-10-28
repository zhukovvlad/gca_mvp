import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import {
  KP_FILE_AGREED_NET,
  KP_FILE_FILENAME,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_SERIES_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { spyOnDownload } from "@/test/utils";
import { qk } from "./queryKeys";
import {
  useDeleteAgreed,
  useDeleteComment,
  useDeleteRung,
  useDownloadSentFile,
  useKpFileExport,
  useKpFilePreview,
  useKpSentFiles,
  usePutAgreed,
  usePutComment,
  usePutRung,
  useSetOfferVatBase,
  useTender,
} from "./queries";

/**
 * Данные фронта «Файла подрядчику» (спека 4б §2.11, §2.14): адреса, тела мутаций,
 * инвалидация, выгрузка и скачивание отправленных. Инвалидацию читаем по флагу
 * `isInvalidated` у запросов без наблюдателей: так видно ровно те ключи, которые
 * мутация пометила, а не те, что перезапросились по случайной цепочке.
 */

const T = OUR_PRICE_TENDER_ID;
const R = OUR_PRICE_ROUND_ID;
const O = OUR_PRICE_OFFER_ID;
const P = OUR_PRICE_LINE_ID;
const S = OUR_PRICE_SERIES_ID;

/** Запросы без наблюдателей не удаляются: тест читает флаг `isInvalidated` посеянных. */
function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 }, mutations: { retry: false } },
  });
}

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

const requestsOf = (kind: (typeof handlerState.kpFileRequests)[number]["kind"]) =>
  handlerState.kpFileRequests.filter((r) => r.kind === kind);

afterEach(() => {
  vi.restoreAllMocks();
});

/** Ключи, которые обязана пометить мутация решения оффера `O` этапа `R`. */
const decisionKeysMarked = () =>
  [
    qk.tenders.ourPriceLine(T, R, O, P, null, null, null),
    qk.tenders.ourPriceLine(T, R, O, P, "f", "all", S),
    qk.tenders.ourPriceOffer(T, R, O, null),
    qk.tenders.ourPriceOffer(T, R, O, S),
    qk.tenders.ourPriceRound(T, R, null),
    qk.tenders.ourPriceRound(T, R, S),
    qk.tenders.kpFilePreview(T, R, O, null),
    qk.tenders.kpFilePreview(T, R, O, S),
  ] as const;

/** Ключи, которых решение по офферу `O` не касается. */
const decisionKeysUntouched = () =>
  [
    qk.tenders.ourPriceLine(T, R, O + 1, P, null, null, S),
    qk.tenders.ourPriceOffer(T, R, O + 1, S),
    qk.tenders.ourPriceRound(T, R + 1, S),
    qk.tenders.kpFilePreview(T, R, O + 1, S),
    qk.tenders.kpFilePreview(T, R + 1, O, S),
    qk.tenders.kpSentFiles(T, R, O),
    qk.tenders.card(T),
  ] as const;

function seed(queryClient: QueryClient, keys: readonly (readonly unknown[])[]) {
  for (const key of keys) queryClient.setQueryData(key, { seeded: true });
}

const invalidated = (queryClient: QueryClient, key: readonly unknown[]) =>
  queryClient.getQueryState(key)?.isInvalidated === true;

describe("запросы", () => {
  it("предпросмотр: адрес без ряда и с рядом, ключ несёт ряд", async () => {
    const queryClient = makeClient();
    const { result, rerender } = renderHook(
      ({ series }: { series: number | null }) => useKpFilePreview(T, R, O, series),
      { wrapper: wrapperFor(queryClient), initialProps: { series: null as number | null } }
    );
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    const first = new URL(requestsOf("preview")[0].url);
    expect(first.pathname).toBe(`/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/export-preview`);
    expect(first.searchParams.has("inflation_series_id")).toBe(false);
    expect(queryClient.getQueryData(qk.tenders.kpFilePreview(T, R, O, null))).toBeDefined();

    rerender({ series: S });
    await vi.waitFor(() => expect(requestsOf("preview")).toHaveLength(2));
    expect(new URL(requestsOf("preview")[1].url).searchParams.get("inflation_series_id")).toBe(String(S));
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(qk.tenders.kpFilePreview(T, R, O, S))).toBeDefined()
    );
  });

  it("предпросмотр несёт шапку с именем файла и столбцы с ключами", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(() => useKpFilePreview(T, R, O, S), { wrapper: wrapperFor(queryClient) });
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.header.filename).toBe(KP_FILE_FILENAME);
    expect(result.current.data?.columns[0]).toEqual({ key: "number", title: "№" });
  });

  it("предпросмотр выключен: запроса нет", async () => {
    const queryClient = makeClient();
    renderHook(() => useKpFilePreview(T, R, O, null, { enabled: false }), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(requestsOf("preview")).toHaveLength(0);
  });

  it("отправленные: адрес списка и форма элемента", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(() => useKpSentFiles(T, R, O), { wrapper: wrapperFor(queryClient) });
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(new URL(requestsOf("sent")[0].url).pathname).toBe(
      `/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/sent-files`
    );
    expect(result.current.data?.[0].created_by).toEqual({ id: 3, email: "admin@example.test" });
    expect(typeof result.current.data?.[0].vat_rate).toBe("string");
  });
});

describe("мутации решений: адрес и тело", () => {
  function renderAll(queryClient: QueryClient) {
    return renderHook(
      () => ({
        putAgreed: usePutAgreed(T, R, O),
        deleteAgreed: useDeleteAgreed(T, R, O),
        putComment: usePutComment(T, R, O),
        deleteComment: useDeleteComment(T, R, O),
        putRung: usePutRung(T, R, O),
        deleteRung: useDeleteRung(T, R, O),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
  }

  type Hooks = ReturnType<typeof renderAll>["result"]["current"];
  const base = `/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/lines/${P}`;
  const cases: Array<{
    name: string;
    method: string;
    path: string;
    body: unknown;
    run: (h: Hooks) => Promise<unknown>;
  }> = [
    {
      name: "usePutAgreed: цена строкой в 24 знака доезжает без потерь",
      method: "PUT",
      path: `${base}/agreed`,
      body: { value: KP_FILE_AGREED_NET, basis: "shown" },
      run: (h) =>
        h.putAgreed.mutateAsync({ positionItemId: P, value: KP_FILE_AGREED_NET, basis: "shown" }),
    },
    {
      name: "useDeleteAgreed",
      method: "DELETE",
      path: `${base}/agreed`,
      body: null,
      run: (h) => h.deleteAgreed.mutateAsync({ positionItemId: P }),
    },
    {
      name: "usePutComment: пустой текст законен",
      method: "PUT",
      path: `${base}/comment`,
      body: { text: "" },
      run: (h) => h.putComment.mutateAsync({ positionItemId: P, text: "" }),
    },
    {
      name: "useDeleteComment",
      method: "DELETE",
      path: `${base}/comment`,
      body: null,
      run: (h) => h.deleteComment.mutateAsync({ positionItemId: P }),
    },
    {
      name: "usePutRung",
      method: "PUT",
      path: `${base}/rung`,
      body: { level: "f", scope: "all" },
      run: (h) => h.putRung.mutateAsync({ positionItemId: P, level: "f", scope: "all" }),
    },
    {
      name: "useDeleteRung",
      method: "DELETE",
      path: `${base}/rung`,
      body: null,
      run: (h) => h.deleteRung.mutateAsync({ positionItemId: P }),
    },
  ];

  it.each(cases)("$name", async ({ method, path, body, run }) => {
    const queryClient = makeClient();
    const { result } = renderAll(queryClient);
    await act(async () => {
      await run(result.current);
    });
    const [request] = requestsOf("decision");
    expect(request.method).toBe(method);
    expect(new URL(request.url).pathname).toBe(path);
    expect(request.body).toEqual(body);
    expect(requestsOf("decision")).toHaveLength(1);
  });

  it.each(cases)("$name: помечает ровно свои ключи", async ({ run }) => {
    const queryClient = makeClient();
    seed(queryClient, [...decisionKeysMarked(), ...decisionKeysUntouched()]);
    const { result } = renderAll(queryClient);
    await act(async () => {
      await run(result.current);
    });
    for (const key of decisionKeysMarked()) expect(invalidated(queryClient, key), JSON.stringify(key)).toBe(true);
    for (const key of decisionKeysUntouched()) {
      expect(invalidated(queryClient, key), JSON.stringify(key)).toBe(false);
    }
  });

  it("правка комментария перезапрашивает показанный предпросмотр, карточку тендера - нет", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(
      () => ({
        card: useTender(T),
        preview: useKpFilePreview(T, R, O, S),
        put: usePutComment(T, R, O),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(result.current.card.isSuccess).toBe(true));
    await vi.waitFor(() => expect(result.current.preview.isSuccess).toBe(true));
    expect(handlerState.tenderCardRequests).toBe(1);
    expect(requestsOf("preview")).toHaveLength(1);

    await act(async () => {
      await result.current.put.mutateAsync({ positionItemId: P, text: "Новый текст" });
    });
    await vi.waitFor(() => expect(requestsOf("preview")).toHaveLength(2));
    expect(handlerState.tenderCardRequests).toBe(1);
  });

  it("отказ сервера: тост с текстом сервера, ключи не помечены", async () => {
    handlerState.kpFileDecisionOutcome = "refused";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const queryClient = makeClient();
    seed(queryClient, [...decisionKeysMarked()]);
    const { result } = renderHook(() => usePutRung(T, R, O), { wrapper: wrapperFor(queryClient) });
    await act(async () => {
      await result.current.mutateAsync({ positionItemId: P, level: "v", scope: "all" }).catch(() => undefined);
    });
    expect(error).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith("Такой ступени у строки нет.");
    for (const key of decisionKeysMarked()) expect(invalidated(queryClient, key)).toBe(false);
  });
});

describe("useKpFileExport", () => {
  async function mountWithPreview(queryClient: QueryClient, series: number | null) {
    const hook = renderHook(
      () => ({
        preview: useKpFilePreview(T, R, O, series),
        export: useKpFileExport(T, R, O, series),
      }),
      { wrapper: wrapperFor(queryClient) }
    );
    await vi.waitFor(() => expect(hook.result.current.preview.isSuccess).toBe(true));
    return hook;
  }

  it("POST с рядом показанного предпросмотра, blob под именем из шапки предпросмотра", async () => {
    const queryClient = makeClient();
    const { result } = await mountWithPreview(queryClient, S);
    const download = spyOnDownload();
    try {
      let blob: Blob | undefined;
      await act(async () => {
        blob = await result.current.export.mutateAsync();
      });
      expect(blob).toBeInstanceOf(Blob);
      const [request] = requestsOf("export");
      expect(request.method).toBe("POST");
      expect(new URL(request.url).pathname).toBe(
        `/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/export`
      );
      expect(new URL(request.url).searchParams.get("inflation_series_id")).toBe(String(S));
      expect(download.lastFilename()).toBe(KP_FILE_FILENAME);
    } finally {
      download.restore();
    }
  });

  it("без ряда в предпросмотре POST идёт без inflation_series_id", async () => {
    const queryClient = makeClient();
    const { result } = await mountWithPreview(queryClient, null);
    const download = spyOnDownload();
    try {
      await act(async () => {
        await result.current.export.mutateAsync();
      });
      expect(new URL(requestsOf("export")[0].url).searchParams.has("inflation_series_id")).toBe(false);
    } finally {
      download.restore();
    }
  });

  it("успех помечает отправленные файлы этого оффера, чужие и предпросмотр не трогает", async () => {
    const queryClient = makeClient();
    const { result } = await mountWithPreview(queryClient, S);
    seed(queryClient, [qk.tenders.kpSentFiles(T, R, O), qk.tenders.kpSentFiles(T, R, O + 1)]);
    const download = spyOnDownload();
    try {
      await act(async () => {
        await result.current.export.mutateAsync();
      });
    } finally {
      download.restore();
    }
    expect(invalidated(queryClient, qk.tenders.kpSentFiles(T, R, O))).toBe(true);
    expect(invalidated(queryClient, qk.tenders.kpSentFiles(T, R, O + 1))).toBe(false);
    expect(invalidated(queryClient, qk.tenders.kpFilePreview(T, R, O, S))).toBe(false);
  });

  it("отказ: тост с текстом сервера из blob, список не помечен, файл не сохранён", async () => {
    handlerState.kpFileExportOutcome = "unknown_vat_base";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const queryClient = makeClient();
    const { result } = await mountWithPreview(queryClient, S);
    seed(queryClient, [qk.tenders.kpSentFiles(T, R, O)]);
    const download = spyOnDownload();
    try {
      await act(async () => {
        await result.current.export.mutateAsync().catch(() => undefined);
      });
      expect(download.lastFilename()).toBeUndefined();
    } finally {
      download.restore();
    }
    await vi.waitFor(() => expect(error).toHaveBeenCalledTimes(1));
    expect(error).toHaveBeenCalledWith("У КП неизвестна база НДС: файл не построить.");
    expect(invalidated(queryClient, qk.tenders.kpSentFiles(T, R, O))).toBe(false);
  });

  it("предпросмотра этого ряда в кеше нет: запроса POST не будет", async () => {
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const queryClient = makeClient();
    const { result } = renderHook(() => useKpFileExport(T, R, O, S), { wrapper: wrapperFor(queryClient) });
    await act(async () => {
      await result.current.mutateAsync().catch(() => undefined);
    });
    expect(requestsOf("export")).toHaveLength(0);
    await vi.waitFor(() => expect(error).toHaveBeenCalledTimes(1));
  });
});

describe("useDownloadSentFile", () => {
  async function run(outcome: "ok" | "missing" | "gone") {
    handlerState.kpFileDownloadOutcome = outcome;
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const queryClient = makeClient();
    const { result } = renderHook(() => useDownloadSentFile(T, R, O), { wrapper: wrapperFor(queryClient) });
    const download = spyOnDownload();
    try {
      await act(async () => {
        await result.current.mutateAsync({ fileId: 11, filename: "Отправленный.xlsx" }).catch(() => undefined);
      });
      return { error, saved: download.lastFilename() };
    } finally {
      download.restore();
    }
  }

  it("успех: GET файла по id и сохранение под именем из списка", async () => {
    const { error, saved } = await run("ok");
    expect(saved).toBe("Отправленный.xlsx");
    expect(error).not.toHaveBeenCalled();
    expect(new URL(requestsOf("download")[0].url).pathname).toBe(
      `/api/v1/tenders/${T}/rounds/${R}/our-price/offers/${O}/sent-files/11/file`
    );
  });

  it("410: файл пропал из хранилища, об этом тост, файла нет", async () => {
    const { error, saved } = await run("gone");
    expect(saved).toBeUndefined();
    expect(error).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith(
      "Файл больше не хранится: он пропал из хранилища. Выгрузите файл заново."
    );
  });

  it("404: файла нет в списке оффера, текст другой, чем у 410", async () => {
    const { error } = await run("missing");
    expect(error).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith("Отправленный файл не найден.");
  });
});

describe("useSetOfferVatBase", () => {
  const marked = () =>
    [
      qk.tenders.ourPriceRound(T, R, null),
      qk.tenders.ourPriceRound(T, R, S),
      qk.tenders.ourPriceOffer(T, R, O, S),
      qk.tenders.ourPriceOffer(T, R, O + 1, null),
      qk.tenders.ourPriceLine(T, R, O, P, null, null, S),
      qk.tenders.kpFilePreview(T, R, O, S),
      qk.tenders.kpFilePreview(T, R, O + 1, null),
    ] as const;
  const untouched = () =>
    [
      qk.tenders.ourPriceRound(T, R + 1, S),
      qk.tenders.ourPriceOffer(T, R + 1, O, S),
      qk.tenders.kpFilePreview(T, R + 1, O, S),
      qk.passport.project(101),
      qk.tenders.card(T),
    ] as const;

  it("шлёт PATCH только с base_override строкой и помечает ключи этапа, не паспорт", async () => {
    const queryClient = makeClient();
    seed(queryClient, [...marked(), ...untouched()]);
    const { result } = renderHook(() => useSetOfferVatBase(T, R), { wrapper: wrapperFor(queryClient) });
    await act(async () => {
      await result.current.mutateAsync({ estimateId: 9001, baseOverride: "12.5" });
    });
    const [request] = requestsOf("vat");
    expect(request.method).toBe("PATCH");
    expect(new URL(request.url).pathname).toBe("/api/v1/estimates/9001/vat");
    expect(request.body).toEqual({ base_override: "12.5" });
    for (const key of marked()) expect(invalidated(queryClient, key), JSON.stringify(key)).toBe(true);
    for (const key of untouched()) expect(invalidated(queryClient, key), JSON.stringify(key)).toBe(false);
  });

  it("сброс базы: base_override = null", async () => {
    const queryClient = makeClient();
    const { result } = renderHook(() => useSetOfferVatBase(T, R), { wrapper: wrapperFor(queryClient) });
    await act(async () => {
      await result.current.mutateAsync({ estimateId: 9001, baseOverride: null });
    });
    expect(requestsOf("vat")[0].body).toEqual({ base_override: null });
  });
});

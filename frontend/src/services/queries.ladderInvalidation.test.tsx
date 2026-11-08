import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_SERIES_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";
import { handlerState, ladderQueues, SCHEMA_FAMILY_ID } from "@/test/handlers";
import type { SchemaEditDraft } from "@/types/domain";
import { semanticApi } from "./api/domain";
import { qk } from "./queryKeys";
import { useDecideChainZone, useDecideReleasable, useUpdateSchema } from "./queries";

/**
 * Решения лестницы (зона, признак) и правка схемы меняют аналоги, а с ними цену и источник в файле
 * подрядчику: предпросмотр перечитывается, список отправленных - нет. Флаг `isInvalidated`
 * читаем у посеянных запросов без наблюдателей.
 */

const T = OUR_PRICE_TENDER_ID;
const R = OUR_PRICE_ROUND_ID;
const O = OUR_PRICE_OFFER_ID;
const S = OUR_PRICE_SERIES_ID;

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

const previewKey = qk.tenders.kpFilePreview(T, R, O, S);
const sentKey = qk.tenders.kpSentFiles(T, R, O);
const ourPriceKey = qk.tenders.ourPriceRound(T, R, S);

function seeded() {
  const queryClient = makeClient();
  for (const key of [previewKey, sentKey, ourPriceKey]) queryClient.setQueryData(key, { seeded: true });
  return queryClient;
}

const invalidated = (queryClient: QueryClient, key: readonly unknown[]) =>
  queryClient.getQueryState(key)?.isInvalidated === true;

describe("решения лестницы и предпросмотр файла подрядчику", () => {
  it("решение зоны: предпросмотр и «Против нашей цены» помечены, отправленные нет", async () => {
    const queryClient = seeded();
    const { result } = renderHook(() => useDecideChainZone(), { wrapper: wrapperFor(queryClient) });
    await act(() => result.current.mutateAsync({ chainId: 1, zone: "whole" }));
    expect(invalidated(queryClient, ourPriceKey)).toBe(true);
    expect(invalidated(queryClient, previewKey)).toBe(true);
    expect(invalidated(queryClient, sentKey)).toBe(false);
  });

  it("решение признака: предпросмотр помечен, отправленные нет", async () => {
    const queryClient = seeded();
    const { result } = renderHook(() => useDecideReleasable(), { wrapper: wrapperFor(queryClient) });
    const row = ladderQueues.proposals.find((p) => p.parameter.id === 11);
    await act(() =>
      result.current.mutateAsync({
        familyId: row!.family.id,
        parameterId: 11,
        releasable: true,
        requestHash: row!.request_hash,
      })
    );
    expect(invalidated(queryClient, previewKey)).toBe(true);
    expect(invalidated(queryClient, sentKey)).toBe(false);
  });
});

describe("правка схемы и расчёт", () => {
  /** Черновики по нынешней схеме семьи; хэш предпросмотра сверяет заглушка сервера. */
  function draftsOfCurrent(): SchemaEditDraft[] {
    return handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters.map((p) => ({
      ordinal: p.ordinal,
      name: p.name,
      role: p.role,
      value_kind: p.value_kind,
      closed: p.closed,
      values: p.values.filter((v) => v.merged_into_id === null).map((v) => v.value),
      range: p.range,
    }));
  }

  it("успех: «Против нашей цены» и предпросмотр помечены, отправленные нет", async () => {
    const queryClient = seeded();
    const drafts = draftsOfCurrent();
    const summary = await semanticApi.schemaEditPreview(SCHEMA_FAMILY_ID, drafts);
    const { result } = renderHook(() => useUpdateSchema(), { wrapper: wrapperFor(queryClient) });
    await act(() =>
      result.current.mutateAsync({
        familyId: SCHEMA_FAMILY_ID,
        parameters: drafts,
        previewHash: summary.preview_hash,
      })
    );
    expect(invalidated(queryClient, ourPriceKey)).toBe(true);
    expect(invalidated(queryClient, previewKey)).toBe(true);
    expect(invalidated(queryClient, sentKey)).toBe(false);
  });

  it("preview_changed: расчёт не трогаем", async () => {
    const queryClient = seeded();
    const { result } = renderHook(() => useUpdateSchema(), { wrapper: wrapperFor(queryClient) });
    await act(async () => {
      await result.current
        .mutateAsync({ familyId: SCHEMA_FAMILY_ID, parameters: draftsOfCurrent(), previewHash: "stale" })
        .catch(() => undefined);
    });
    expect(invalidated(queryClient, ourPriceKey)).toBe(false);
    expect(invalidated(queryClient, previewKey)).toBe(false);
  });
});

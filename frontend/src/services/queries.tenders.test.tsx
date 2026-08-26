import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";

import {
  apiErrorContext,
  apiErrorStatus,
  useDeleteParticipant,
  useImportJob,
  useTender,
  useUploadRound,
} from "./queries";
import { qk } from "./queryKeys";
import { handlerState, resetHandlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient } from "@/test/utils";
import type { ParticipantDeletionPreview } from "@/types/domain";

function wrapperFor(queryClient: ReturnType<typeof createTestQueryClient>) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

/**
 * `useTender` (спека контура §2.13): карточка тендера несёт решётку целиком —
 * округа, участников и ячейки. Проверяем именно ЧИСЛО ячеек фикстуры (четыре
 * — по два раунда на двух участников), а не факт «запрос прошёл»: без этого
 * тест не отличил бы полную решётку от урезанной.
 */
describe("useTender", () => {
  it("отдаёт карточку тендера 300 с четырьмя ячейками решётки", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTender(300), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.id).toBe(300);
    expect(result.current.data?.cells).toHaveLength(4);
  });
});

/**
 * `useDeleteParticipant` — протокол `confirmation_token` (спека §2.11). Без
 * токена сервер ВСЕГДА отвечает 409 со свежим preview, а не молча требует
 * токен, — экран должен получить и статус, и структурированный `detail`, чтобы
 * показать диалог подтверждения, а не красный тост общего вида.
 */
describe("useDeleteParticipant", () => {
  it("без confirmation_token отдаёт 409 с preview (code confirmation_required)", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDeleteParticipant(), {
      wrapper: wrapperFor(queryClient),
    });

    let caught: unknown;
    await act(async () => {
      try {
        await result.current.mutateAsync({ tenderId: 300, packageId: 501 });
      } catch (err) {
        caught = err;
      }
    });

    expect(caught).toBeDefined();
    expect(apiErrorStatus(caught)).toBe(409);
    const preview = apiErrorContext<ParticipantDeletionPreview>(caught);
    expect(preview?.code).toBe("confirmation_required");
    expect(typeof preview?.confirmation_token).toBe("string");
  });
});

/**
 * `useUploadRound` (спека §2.14): `replace=true` обязан доехать до сервера в
 * multipart-теле — граница «замена всех смет раунда» держится ИМЕННО этим
 * полем. Хендлер ставит `handlerState.lastRoundUploadReplace` по факту разбора
 * тела запроса, а не по параметрам вызова — так тест доказывает, что поле
 * действительно ушло по сети, а не осталось только в аргументах мутации.
 */
describe("useUploadRound", () => {
  it("replace: true доезжает до сервера в теле загрузки", async () => {
    resetHandlerState();
    expect(handlerState.lastRoundUploadReplace).toBe(false);

    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useUploadRound(), {
      wrapper: wrapperFor(queryClient),
    });

    await act(async () => {
      await result.current.mutateAsync({
        file: new File(["stub"], "round.xlsx"),
        tender_id: 300,
        round_id: 3001,
        replace: true,
      });
    });

    expect(handlerState.lastRoundUploadReplace).toBe(true);
  });
});

/**
 * `useImportJob` — развилка владельца (спека контура §2.13, задача 10). Job
 * раунда тендера обязан при `done` инвалидировать карточку ТЕНДЕРА
 * (`qk.tenders.card`), а не карточку договора: `ImportJobPanel` (задача 11)
 * читает решётку из карточки тендера, и без этой инвалидации грид не обновится
 * после загрузки. Поведение договорного пути (`ownerRef.contractId`) этой
 * задачей не менялось ни на строку — оно проверено отдельно в
 * `queries.test.tsx` («завершённый импорт инвалидирует корень паспорта»).
 */
describe("useImportJob: инвалидация для раундового задания", () => {
  it("done-задание раунда инвалидирует qk.tenders.card(tenderId)", async () => {
    server.use(
      http.get("/api/v1/import-jobs/:id", () =>
        HttpResponse.json({
          id: 77,
          owner_type: "round",
          tender_id: 300,
          round_id: 3001,
          estimate_ids: [8001, 8002],
          estimates_created: 2,
          filename: "round.xlsx",
          file_sha256: "abc",
          status: "done",
          error_text: null,
          warnings: [],
          counters: {
            positions_total: 0,
            matched_cache: 0,
            matched_exact: 0,
            matched_nonposition: 0,
            to_review: 0,
          },
          created_at: null,
          started_at: null,
          finished_at: null,
        })
      )
    );

    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    renderHook(() => useImportJob(77, { tenderId: 300 }), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.tenders.card(300)));
    });
  });
});

import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

import {
  apiErrorCode,
  apiErrorContext,
  apiErrorStatus,
  useDeleteParticipant,
  useDeleteTender,
  useImportJob,
  useRoundImportJobs,
  useStagePositions,
  useStageSummary,
  useTender,
  useUpdateRound,
  useUpdateTender,
  useUploadRound,
} from "./queries";
import { qk } from "./queryKeys";
import { handlerState, resetHandlerState } from "@/test/handlers";
import { stagePositionsResponse } from "@/test/fixtures";
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
 * `useUpdateTender` (правка §2.14 — только `title`/`notes`, находка ревью
 * задачи 10: у семи из одиннадцати функций `tendersApi` не было хендлера
 * вовсе). Хендлер отвечает ПРИМЕНЁННОЙ карточкой — проверяем, что правка
 * действительно ушла в ответ, а не то, что запрос просто не упал: фиксированная
 * фикстура прошла бы тест «запрос успешен» так же зелено, ничего не доказав
 * про применение патча.
 */
describe("useUpdateTender", () => {
  it("PATCH доезжает до /v1/tenders/:id и возвращает карточку с применённой правкой", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useUpdateTender(), {
      wrapper: wrapperFor(queryClient),
    });

    let card: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      card = await result.current.mutateAsync({
        id: 300,
        input: { title: "Генподряд — переторжка", notes: "перенесён третий раунд" },
      });
    });

    expect(card?.title).toBe("Генподряд — переторжка");
    expect(card?.notes).toBe("перенесён третий раунд");
  });
});

/**
 * `useDeleteTender` (§2.14). Два случая: обычное удаление отвечает `204` (сама
 * мутация ничего не возвращает — `undefined`, но запрос обязан дойти до
 * ПРАВИЛЬНОГО пути `DELETE /v1/tenders/:id`, а не молча удариться в хендлер
 * участника или раунда с тем же методом); отказ при активном импорте зеркалит
 * протокол `DELETE .../participants/:pid` — тем же кодом `active_import`,
 * потому что причина отказа та же самая (спека §2.11).
 */
describe("useDeleteTender", () => {
  it("DELETE /v1/tenders/:id отвечает 204", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDeleteTender(), {
      wrapper: wrapperFor(queryClient),
    });

    await act(async () => {
      await result.current.mutateAsync(300);
    });

    expect(result.current.isSuccess).toBe(true);
  });

  it("при активном импорте отвечает 409 active_import вместо удаления", async () => {
    handlerState.tenderDeleteOutcome = "active";

    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDeleteTender(), {
      wrapper: wrapperFor(queryClient),
    });

    let caught: unknown;
    await act(async () => {
      try {
        await result.current.mutateAsync(300);
      } catch (err) {
        caught = err;
      }
    });

    expect(apiErrorStatus(caught)).toBe(409);
    expect(apiErrorCode(caught)).toBe("active_import");
  });
});

/**
 * `useUpdateRound` (§2.14 — `label`/`held_on`, не `stage_no`). Как и у
 * `useUpdateTender`: хендлер обязан отдать карточку с ИМЕННО этим раундом
 * обновлённым, а другие раунды — нетронутыми, иначе тест не отличил бы
 * «применили патч к правильному раунду» от «применили ко всем».
 */
describe("useUpdateRound", () => {
  it("PATCH доезжает до /v1/tenders/:id/rounds/:rid и правит только этот раунд", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useUpdateRound(), {
      wrapper: wrapperFor(queryClient),
    });

    let card: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      card = await result.current.mutateAsync({
        tenderId: 300,
        roundId: 3001,
        input: { label: "Переторжка", held_on: "2026-07-01" },
      });
    });

    const patched = card?.rounds.find((round) => round.id === 3001);
    const untouched = card?.rounds.find((round) => round.id === 3002);
    expect(patched?.label).toBe("Переторжка");
    expect(patched?.held_on).toBe("2026-07-01");
    // Второй раунд карточки — не участник этого PATCH — обязан остаться как
    // в фикстуре: `null`/`null`, а не подхватить правку первого.
    expect(untouched?.label).toBeNull();
    expect(untouched?.held_on).toBeNull();
  });
});

/**
 * `useRoundImportJobs` (§2.14) — история заданий импорта КОНКРЕТНОГО раунда.
 * Проверяем форму, которую типизирует `RoundImportJob`: массив с хотя бы одним
 * `is_current: true` — без него компонент задачи 11 не смог бы отрисовать
 * «текущее задание раунда», единственный сценарий, ради которого экран вообще
 * запрашивает эту историю.
 */
describe("useRoundImportJobs", () => {
  it("отдаёт историю раунда 3001 с текущим заданием", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useRoundImportJobs(300, 3001), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const jobs = result.current.data ?? [];
    expect(jobs.length).toBeGreaterThan(0);
    expect(jobs.every((job) => job.round_id === 3001 && job.owner_type === "round")).toBe(true);
    expect(jobs.some((job) => job.is_current)).toBe(true);
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

  /**
   * Находка финального ревью контура: `RoundUploadPanel` живёт на том же
   * экране, что и таблица истории загрузок раунда (`useRoundImportJobs`,
   * `TenderCardPage`), но прежде инвалидировала только карточку тендера.
   * История держала `staleTime` в минуту и не видела, что job закончился —
   * строка молча оставалась «в процессе», без бейджа «актуальный»,
   * неопределённо долго. Без этой инвалидации данный тест падал бы: спай не
   * увидел бы ключ `roundJobs` среди вызовов.
   */
  it("done-задание раунда ТАКЖЕ инвалидирует qk.tenders.roundJobs(tenderId, roundId)", async () => {
    server.use(
      http.get("/api/v1/import-jobs/:id", () =>
        HttpResponse.json({
          id: 78,
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

    renderHook(() => useImportJob(78, { tenderId: 300, roundId: 3001 }), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.tenders.roundJobs(300, 3001)));
    });
  });

  /**
   * Договорный путь (`ownerRef.contractId`) этим finding'ом не тронут ни на
   * строку: тот же набор ключей, что и до фикса — карточка договора, история
   * загрузок договора, очередь ручного матчинга, корень паспорта, — и НИЧЕГО
   * из тендерного мира. Три отдельные задачи уже защищали контрактные экраны
   * от коллизии с тендерным контуром; это ревью явно требует не сдвигать их
   * поведение.
   */
  it("done-задание договора инвалидирует ровно контрактный набор ключей — без qk.tenders.*", async () => {
    server.use(
      http.get("/api/v1/import-jobs/:id", () =>
        HttpResponse.json({
          id: 79,
          owner_type: "contract",
          contract_id: 12,
          estimate_id: 8001,
          filename: "contract.xlsx",
          file_sha256: "def",
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

    renderHook(() => useImportJob(79, { contractId: 12 }), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.contracts.card(12)));
    });
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.contracts.importJobs(12)));
    expect(keys).toContain(JSON.stringify(qk.review.all));
    expect(keys).toContain(JSON.stringify(qk.passport.all));
    expect(keys.some((k) => k.includes('"tenders"'))).toBe(false);
  });

  /**
   * Находка внешнего ревью PR #33 (finding 1): раунд ведёт себя так же, как
   * договор выше — после ПРОВАЛЕННОГО импорта ключи карточки тендера (несёт
   * `latest_job` раунда) и истории загрузок ИМЕННО этого раунда обязаны
   * инвалидироваться, иначе `RoundUploadPanel`/таблица истории держат job «в
   * процессе» неопределённо долго. Тест наблюдает через шпион на
   * `invalidateQueries` запрос инвалидации нужных ключей, а не сам перерендер
   * таблицы: что перерендер следует из инвалидации — контракт TanStack Query,
   * здесь не перепроверяется, а наблюдаемое доказательство обновления рендера —
   * прогон на стенде. Гоняем настоящий переход `pending → error` тем же
   * приёмом, что у договора: баг был именно в этом переходе, а не в статичном
   * рендере `error`. `review.all` — НЕ трогаем: у раунда, как и у договора,
   * провал не создаёт смет.
   */
  it("pending → error задания раунда инвалидирует qk.tenders.card и qk.tenders.roundJobs, но НЕ qk.review.all", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    let polls = 0;
    server.use(
      http.get("/api/v1/import-jobs/:id", () => {
        const status = polls === 0 ? "pending" : "error";
        polls += 1;
        return HttpResponse.json({
          id: 80,
          owner_type: "round",
          tender_id: 300,
          round_id: 3001,
          estimate_ids: [],
          estimates_created: null,
          filename: "round.xlsx",
          file_sha256: "abc",
          status,
          error_text: status === "error" ? "Не удалось разобрать файл." : null,
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
        });
      })
    );

    const { result } = renderHook(() => useImportJob(80, { tenderId: 300, roundId: 3001 }), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.data?.status).toBe("pending"));
    // Интервал поллинга — 1500 мс; окно заведомо больше, чтобы дождаться
    // следующего опроса, который вернёт `error`.
    await waitFor(() => expect(result.current.data?.status).toBe("error"), { timeout: 8000 });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.tenders.card(300)));
    expect(keys).toContain(JSON.stringify(qk.tenders.roundJobs(300, 3001)));
    expect(keys).not.toContain(JSON.stringify(qk.review.all));
  });
});

/**
 * `useStageSummary` (спека 2026-08-27-stage-summary-design.md §2.16): свод по этапам
 * одного участника с параллельным выбором нескольких предложений.
 */
describe("useStageSummary", () => {
  afterEach(() => {
    resetHandlerState();
  });

  it("отдаёт свод с тремя колонками по возрастанию stage_no", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7002, 7001, 7004]), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.columns.map((c) => c.stage_no)).toEqual([1, 2, 4]);
    // ключ канонический: порядок id в URL не создаёт второй записи кэша
    expect(qc.getQueryData(qk.tenders.stageSummary(300, [7001, 7002, 7004]))).toBeDefined();
  });

  it("single_participant → 422 с кодом и списком offers в detail", async () => {
    handlerState.stageSummaryOutcome = "single_participant";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001, 7101]), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorStatus(result.current.error)).toBe(422);
    expect(apiErrorCode(result.current.error)).toBe("single_participant");
    expect(apiErrorContext<{ offers: number[] }>(result.current.error)?.offers).toEqual([7001, 7101]);
  });

  it("offer_not_found → 404 с тем же объектом detail", async () => {
    handlerState.stageSummaryOutcome = "offer_not_found";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001, 9999]), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorStatus(result.current.error)).toBe(404);
    expect(apiErrorCode(result.current.error)).toBe("offer_not_found");
  });

  it("при одном offer запрос УХОДИТ и получает too_few_offers — клиент отказ не подменяет", async () => {
    handlerState.stageSummaryOutcome = "too_few_offers";
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, [7001]), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorCode(result.current.error)).toBe("too_few_offers");
  });

  it("при пустом выборе запрос не уходит", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStageSummary(300, []), {
      wrapper: wrapperFor(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });
});

/**
 * `useStagePositions` (спека 2026-08-30-position-drilldown-design.md §2.12):
 * попозиционное разложение статьи свода, третий уровень.
 */
describe("useStagePositions", () => {
  afterEach(() => {
    resetHandlerState();
  });

  it("грузит разложение и кладёт его под канонический ключ", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStagePositions(300, 22, [7002, 7001], true), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.work_category.id).toBe(22);
    // ключ канонический: порядок id не создаёт второй записи кэша
    expect(qc.getQueryData(qk.tenders.stagePositions(300, 22, [7001, 7002]))).toBeDefined();
  });

  it("enabled=false — запрос не уходит (ленивость §2.1)", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useStagePositions(300, 22, [7001, 7002], false), {
      wrapper: wrapperFor(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("размонтирование и повторный монтаж НЕ шлют второй запрос (§6.3)", async () => {
    // Реальный путь потери наблюдателя: свернули статью-предка — блок работ
    // подстатьи размонтировался вместе с ней. Со `staleTime` без `gcTime`
    // тест зелёный лишь пока не истёк сборщик кэша, поэтому здесь считаются
    // ПОПАДАНИЯ В ХЕНДЛЕР, а не состояние хука.
    const qc = createTestQueryClient();
    let hits = 0;
    server.use(
      http.get("/api/v1/tenders/:tenderId/stage-summary/:workCategoryId", () => {
        hits += 1;
        return HttpResponse.json(stagePositionsResponse());
      })
    );
    const first = renderHook(() => useStagePositions(300, 22, [7001, 7002], true), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    first.unmount();
    // Без gcTime у клиента задан gcTime: 0 (createTestQueryClient) — сборщик
    // кэша сам планируется через setTimeout(0) при потере последнего
    // наблюдателя. Отдать событийный цикл здесь ОБЯЗАТЕЛЬНО: без этого тика
    // второй renderHook успевал бы застать запись кэша ещё не удалённой даже
    // без `gcTime: Infinity` в хуке, и проверка не отличала бы исправный хук
    // от сломанного (найдено самопроверкой задачи 8 — снятие `gcTime:
    // Infinity` не краснило тест до этой правки).
    await new Promise((resolve) => setTimeout(resolve, 0));
    const second = renderHook(() => useStagePositions(300, 22, [7001, 7002], true), {
      wrapper: wrapperFor(qc),
    });
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true));
    expect(hits).toBe(1);
  });
});

import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

import {
  apiErrorCode,
  apiErrorContext,
  apiErrorStatus,
  useClearRoundCategoryOverride,
  useDeleteParticipant,
  useDeleteTender,
  useImportJob,
  useRoundImportJobs,
  useRoundUnallocated,
  useSetRoundCategoryOverride,
  useStagePositions,
  useStageSummary,
  useTender,
  useTenderChangesExport,
  useUpdateRound,
  useUpdateTender,
  useUploadRound,
} from "./queries";
import { qk } from "./queryKeys";
import { handlerState, resetHandlerState } from "@/test/handlers";
import { sampleTenderCard, stagePositionsResponse } from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, spyOnDownload } from "@/test/utils";
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
 * `useTenderChangesExport` — книга «Изменения КП» (спека
 * 2026-09-16-tender-changes-export-design.md §2.1, §2.11, план фичи, Task 5).
 * Вход мутации — ОДИН `tenderId`, без предложений: книга собирается по всем
 * сравнимым участникам, выбор на решётке карточки сюда не входит (A5.9
 * предъявляет страница, здесь — контракт самого хука и транспорта).
 */
describe("useTenderChangesExport", () => {
  it("запрашивает книгу по id тендера и отдаёт Blob", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTenderChangesExport(), {
      wrapper: wrapperFor(queryClient),
    });

    let blob: Blob | undefined;
    await act(async () => {
      blob = await result.current.mutateAsync({
        tenderId: sampleTenderCard.id,
        tenderNumber: sampleTenderCard.tender_number,
      });
    });

    expect(blob).toBeInstanceOf(Blob);
    expect(handlerState.lastChangesExportTenderId).toBe(sampleTenderCard.id);
  });

  /**
   * Внешнее ревью H3: имя файла зашивало «Изменения КП.xlsx» — книги разных
   * тендеров были неразличимы на диске, хотя сервер уже отдаёт номер в
   * `Content-Disposition` (`safe_filename_part`). Номер с `/` (нумерация
   * «12/2025» законна) не должен ломать сохранение — фронт обязан чистить
   * его тем же способом, что и бэкенд.
   */
  it("имя файла несёт номер тендера, а «/» в номере не ломает сохранение", async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTenderChangesExport(), {
      wrapper: wrapperFor(queryClient),
    });
    const download = spyOnDownload();
    try {
      await act(async () => {
        await result.current.mutateAsync({ tenderId: sampleTenderCard.id, tenderNumber: "12/2025" });
      });

      expect(download.lastFilename()).toBe("Изменения КП 12-2025.xlsx");
      expect(download.lastFilename()).not.toContain("/");
    } finally {
      download.restore();
    }
  });

  /**
   * `responseType: "blob"` меняет форму ТЕЛА ОТКАЗА тоже — axios отдаёт JSON
   * сервера блобом, а не разобранным объектом (то самое обстоятельство, ради
   * которого заведён `reportErrorMessage`/`toastReportError`: `apiErrorCode`
   * его не разбирает и не обязан — этот путь БЛОБА проверяется здесь
   * отдельно, транспортно, а что ЧЕЛОВЕК видит текст сервера, а не «Request
   * failed with status code 422», предъявляет `TenderCardPage.test.tsx`
   * (A5.10) тем же приёмом, что и у трёх выгрузок §7.6.
   */
  it("422 no_comparable_participants доезжает статусом 422 и телом-блобом с тем же detail, что и у соседних выгрузок", async () => {
    handlerState.changesExportOutcome = "no_comparable";
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTenderChangesExport(), {
      wrapper: wrapperFor(queryClient),
    });

    let caught: unknown;
    await act(async () => {
      try {
        await result.current.mutateAsync({
          tenderId: sampleTenderCard.id,
          tenderNumber: sampleTenderCard.tender_number,
        });
      } catch (err) {
        caught = err;
      }
    });

    expect(apiErrorStatus(caught)).toBe(422);
    const data = (caught as { response?: { data?: unknown } })?.response?.data;
    expect(data).toBeInstanceOf(Blob);
    const parsed = JSON.parse(await (data as Blob).text()) as { detail: { code: string; message: string } };
    expect(parsed.detail.code).toBe("no_comparable_participants");
    expect(parsed.detail.message).toMatch(/В тендере нет участников с двумя и более сметами/);
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
   * Ревью PR #35, finding 2: после ЗАМЕНЫ раунда сервер переиспользует ТУ ЖЕ
   * строку `Offer` (`services/round_import.py`: `on_conflict_do_nothing` по
   * `(round_id, package_id)`), поэтому id предложений не меняются, и точечные
   * ключи `qk.tenders.stageSummary`/`qk.tenders.stagePositions` (собранные из
   * этих id) остаются ПРЕЖНИМИ. `useStagePositions` держит
   * `staleTime: Infinity`/`gcTime: Infinity` (§6.3) — единственное, что может
   * освежить его кэш, это инвалидация; без неё разложение показывало бы
   * старые деньги рядом со свежим сводом до перезагрузки страницы. Проверяем
   * ОБА префиксных ключа — свод и разложение, — а не только один: тест,
   * проверяющий лишь `stageSummary`, не заметил бы половину дефекта, потому
   * что именно у разложения нет иного способа обновиться, кроме инвалидации.
   */
  it("done-задание раунда инвалидирует ОБА префикса — stage-summary и stage-positions — для тендера", async () => {
    server.use(
      http.get("/api/v1/import-jobs/:id", () =>
        HttpResponse.json({
          id: 82,
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

    renderHook(() => useImportJob(82, { tenderId: 300, roundId: 3001 }), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.tenders.stageSummaryForTender(300)));
      expect(keys).toContain(JSON.stringify(qk.tenders.stagePositionsForTender(300)));
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

describe("useRoundUnallocated", () => {
  it("enabled=false — запрос не уходит (ленивый GET, §2.7)", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useRoundUnallocated(300, 3001, false), { wrapper: wrapperFor(qc) });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("грузит нераспределённое раунда 3001", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useRoundUnallocated(300, 3001, true), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const sections = result.current.data?.sections ?? [];
    expect(sections).toHaveLength(5);
    expect(result.current.data?.offers_count).toBe(1);

    // Форма переживает круговой путь через msw целиком, не только счётчики.
    const partialSection = sections.find((s) => s.state === "partial");
    if (partialSection?.state === "partial") {
      expect(partialSection.partial).toEqual({ assigned: 1, total: 2, notes: ["код в файле нечитаем"] });
    } else {
      throw new Error("ожидался раздел в состоянии partial");
    }

    const conflictSections = sections.filter((s) => s.state === "conflict");
    expect(conflictSections).toHaveLength(2);
    const twoCategoryConflict = conflictSections.find((s) => s.state === "conflict" && s.conflict.categories.length === 2);
    if (twoCategoryConflict?.state === "conflict") {
      expect(twoCategoryConflict.conflict.categories).toHaveLength(2);
    } else {
      throw new Error("ожидался конфликт с двумя статьями");
    }
    const auditDiffersConflict = conflictSections.find((s) => s.state === "conflict" && s.conflict.audit_differs);
    if (auditDiffersConflict?.state === "conflict") {
      expect(auditDiffersConflict.conflict.audit_differs).toBe(true);
    } else {
      throw new Error("ожидался конфликт с расходящимся аудитом");
    }

    expect(result.current.data?.manual).toHaveLength(1);
    expect(result.current.data?.manual?.[0]).toMatchObject({
      work_category_id: 13,
      category_code: "09",
      category_title: "Благоустройство",
    });

    expect(result.current.data?.diagnostics).toHaveLength(2);

    const nested = sections.find((s) => s.parent_key !== null);
    expect(nested?.parent_key).toEqual(["lot_1", "3"]);
  });

  it("404 на раунде без offer-смет — ошибка, и запрос НЕ повторяется (retry: false, §2.7)", async () => {
    let hits = 0;
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => {
        hits += 1;
        return HttpResponse.json(
          { detail: { code: "round_has_no_offer_estimates", message: "Раунд или его сметы больше недоступны." } },
          { status: 404 }
        );
      })
    );
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useRoundUnallocated(300, 3002, true), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(apiErrorStatus(result.current.error)).toBe(404);
    expect(hits).toBe(1);
  });
});

describe("раундовые мутации разноса (§2.7)", () => {
  const EXPECTED = [
    qk.tenders.card(300), qk.tenders.stageSummaryForTender(300),
    qk.tenders.stagePositionsForTender(300), qk.tenders.roundUnallocated(300, 3001),
    qk.semanticContexts.all,
  ].map((k) => JSON.stringify(k));

  it("PUT несёт заметку явно (null тоже) и инвалидирует тендерную ветку — и ничего договорного", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useSetRoundCategoryOverride(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ tenderId: 300, roundId: 3001, lotKey: "lot_1", positionKey: "3", workCategoryId: 20, note: null }));
    const body = handlerState.roundOverrideRequests[0].body as Record<string, unknown>;
    expect("note" in body && body.note === null).toBe(true);
    expect(body).toMatchObject({ lot_key: "lot_1", position_key_in_proposal: "3", work_category_id: 20 });
    const keys = spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey));
    expect(new Set(keys)).toEqual(new Set(EXPECTED));
    // Негативно к перекрёстной инвалидации: ни один ключ не начинается с корней договорного контура.
    expect(keys.some((k) => k.startsWith('["passport"') || k.startsWith('["contracts"'))).toBe(false);
  });

  it("DELETE несёт ключ в теле и инвалидирует тот же набор", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useClearRoundCategoryOverride(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ tenderId: 300, roundId: 3001, lotKey: "lot_1", positionKey: "50" }));
    expect(handlerState.roundOverrideRequests[0].method).toBe("DELETE");
    // Точное равенство, не toMatchObject: утёкшее поле (note, work_category_id)
    // прошло бы частичный матчер молча.
    expect(handlerState.roundOverrideRequests[0].body).toEqual({ lot_key: "lot_1", position_key_in_proposal: "50" });
    expect(new Set(spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey)))).toEqual(new Set(EXPECTED));
  });
});

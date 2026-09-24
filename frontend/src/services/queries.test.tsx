import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useClearCategoryOverride,
  useClearRoundCategoryOverride,
  useCreateContract,
  useDeleteContract,
  useImportJob,
  useMergeReview,
  useProjectPassport,
  useSetCategoryOverride,
  useSetEstimateVat,
  useSetRoundCategoryOverride,
  useUpdateContract,
  useUpdateContractor,
  useUpdateObject,
  useUpdateRateClass,
} from "./queries";
import { qk } from "./queryKeys";
import {
  sampleContractCard,
  sampleContracts,
  sampleImportJobs,
  sampleProjectPassport,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient } from "@/test/utils";

/**
 * Инвалидация `useUpdateObject` (спека §2.11).
 *
 * Название объекта денормализовано в три семейства ответов — список/карточку
 * договоров, паспорт фазы 6 и колонки матрицы, — и все три кэшируются. Ключи
 * проверяются ПО ОТДЕЛЬНОСТИ: утверждение «вызвано пять раз» прошло бы и при
 * пяти одинаковых ключах.
 */
describe("useUpdateObject: инвалидация после правки объекта", () => {
  it("правка объекта инвалидирует все кэши, где лежит его название", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useUpdateObject(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync({ id: 10, input: { title: "Новое" } });
    });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.objects.all));
    expect(keys).toContain(JSON.stringify(qk.rateClasses.all));
    expect(keys).toContain(JSON.stringify(qk.contracts.all));
    expect(keys).toContain(JSON.stringify(qk.passport.all));
    expect(keys).toContain(JSON.stringify(qk.matrix.all));
  });
});

/**
 * `useProjectPassport` (задача 6 Ф6, план — обязательство 1, унаследованное
 * из F5): паспорт проекта обязан жить ПОД корнем `qk.passport.all`, а не
 * заводить собственный, — иначе инвалидация `qk.passport.all` в
 * `useUpdateObject`/`useUpdateAppSettings` перестанет его задевать, а тест
 * выше («правка объекта инвалидирует...») этого не заметит: он проверяет
 * только СПИСОК инвалидированных ключей, а не то, накрывает ли хоть один из
 * них ключ паспорта проекта. Заведи Ф6 отдельный корень (например
 * `qk.projectPassport`), тот тест остался бы ЗЕЛЁНЫМ, пока обязательство 1
 * молча сломано. Ключ здесь берётся ИЗ КЭША — то есть из того, что хук
 * реально использовал в рантайме, — а не пересчитывается через
 * `qk.passport.project(...)`: смена корня внутри хука обязана уронить именно
 * этот тест.
 */
/**
 * Заведено по находке внешнего круга. Корень `qk.passport` переиспользован, но
 * инвалидировал его только `useUpdateObject`. Паспорт проекта денормализует
 * реквизиты договора, названия подрядчика и класса, а его суммы целиком зависят
 * от сметы — значит при `staleTime: 60_000` правка любого из этих источников
 * оставляла бы уже открытый паспорт «свежим» по мнению React Query и устаревшим
 * по факту целую минуту. Ключи проверяются ПО ОТДЕЛЬНОСТИ и по каждой мутации
 * своим прогоном: утверждение «вызвано N раз» прошло бы и при N одинаковых.
 */
describe("инвалидация паспорта проекта источниками его данных", () => {
  it.each([
    {
      name: "правка договора",
      run: async (queryClient: ReturnType<typeof createTestQueryClient>) => {
        const { result } = renderHook(() => useUpdateContract(), {
          wrapper: ({ children }) => (
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
          ),
        });
        await act(async () => {
          await result.current.mutateAsync({ id: 1, input: { title: "Новое" } });
        });
      },
    },
    {
      name: "правка подрядчика",
      run: async (queryClient: ReturnType<typeof createTestQueryClient>) => {
        // Общих хендлеров на PATCH справочников в MSW нет — подменяем точечно.
        server.use(
          http.patch("/api/v1/contractors/:id", () =>
            HttpResponse.json({ id: 1, title: "Новый", inn: "7700000000" })
          )
        );
        const { result } = renderHook(() => useUpdateContractor(), {
          wrapper: ({ children }) => (
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
          ),
        });
        await act(async () => {
          await result.current.mutateAsync({ id: 1, input: { title: "Новый" } });
        });
      },
    },
    {
      name: "правка класса объектов",
      run: async (queryClient: ReturnType<typeof createTestQueryClient>) => {
        server.use(
          http.patch("/api/v1/rate-classes/:id", () =>
            HttpResponse.json({ id: 1, title: "Новый", objects_count: 0 })
          )
        );
        const { result } = renderHook(() => useUpdateRateClass(), {
          wrapper: ({ children }) => (
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
          ),
        });
        await act(async () => {
          await result.current.mutateAsync({ id: 1, input: { title: "Новый" } });
        });
      },
    },
    /*
      Два случая ниже — по второму кругу внешнего ревью. Создание и удаление
      договора меняют `object_contracts_count` у ВСЕХ паспортов объекта (бейдж
      «у объекта N договоров»), а удаление вдобавок оставляет в кэше паспорт
      САМОГО удалённого договора — к нему можно вернуться назад и увидеть
      документ по договору, которого уже нет.
    */
    {
      name: "создание договора",
      run: async (queryClient: ReturnType<typeof createTestQueryClient>) => {
        server.use(
          http.post("/api/v1/contracts", () => HttpResponse.json(sampleContracts[0], { status: 201 }))
        );
        const { result } = renderHook(() => useCreateContract(), {
          wrapper: ({ children }) => (
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
          ),
        });
        await act(async () => {
          await result.current.mutateAsync({
            object_id: 1,
            contractor_id: 1,
            rate_class_id: 1,
            contract_number: "ГП-9999",
          } as never);
        });
      },
    },
    {
      name: "удаление договора",
      run: async (queryClient: ReturnType<typeof createTestQueryClient>) => {
        server.use(http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 })));
        const { result } = renderHook(() => useDeleteContract(), {
          wrapper: ({ children }) => (
            <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
          ),
        });
        await act(async () => {
          await result.current.mutateAsync(1);
        });
      },
    },
  ])("$name инвалидирует корень паспорта", async ({ run }) => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    await run(queryClient);

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.passport.all));
  });

  it("завершённый импорт инвалидирует корень паспорта", async () => {
    // Самый весомый случай: успешный импорт МЕНЯЕТ содержимое паспорта целиком —
    // смета появляется или заменяется вместе со всеми суммами по статьям.
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    server.use(
      http.get("/api/v1/estimates/jobs/:jobId", () =>
        HttpResponse.json({ ...sampleImportJobs[0], id: 77, status: "done" })
      )
    );

    renderHook(() => useImportJob(77, { contractId: 12 }), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.passport.all));
    });
  });

  it("завершённый импорт инвалидирует контексты каталога", async () => {
    // Импорт создаёт/обновляет членства контекстов (счётчики на карточках
    // экрана /families) — без этой инвалидации открытый заранее экран
    // показывал бы прежние счётчики весь staleTime.
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    server.use(
      http.get("/api/v1/estimates/jobs/:jobId", () =>
        HttpResponse.json({ ...sampleImportJobs[0], id: 78, status: "done" })
      )
    );

    renderHook(() => useImportJob(78, { contractId: 12 }), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(qk.semanticContexts.all));
    });
  });

  /**
   * Находка внешнего ревью PR #33 (finding 1): после ПРОВАЛЕННОГО импорта
   * ключи карточки и истории договора обязаны инвалидироваться так же, как
   * после успешного, — иначе job навсегда остаётся «в процессе» и в истории, и
   * в `latest_job` карточки (`staleTime` минута, без рефетча по фокусу). Тест
   * наблюдает через шпион на `invalidateQueries` запрос инвалидации нужных
   * ключей, а не перерендер истории: что перерендер следует из инвалидации —
   * контракт самого TanStack Query, здесь не перепроверяется, а наблюдаемое
   * доказательство обновления рендера — прогон на стенде. Прогоняем задание
   * через настоящий переход `pending → error` (а не просто рендерим его сразу
   * `error`), потому что баг был именно в этом переходе: загрузочный `202` уже
   * положил `pending`-job в историю, а поллинг после `error` не обновлял
   * ничего. `review.all`/`passport.all` — НЕ трогаем: проваленный импорт не
   * создаёт сметы, смотреть и пересчитывать нечего.
   */
  it("pending → error задания договора инвалидирует карточку и историю, но НЕ паспорт и НЕ review", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    let polls = 0;
    server.use(
      http.get("/api/v1/import-jobs/:id", () => {
        const status = polls === 0 ? "pending" : "error";
        polls += 1;
        return HttpResponse.json({
          ...sampleImportJobs[0],
          id: 81,
          contract_id: 12,
          status,
          estimate_id: null,
          estimates_created: null,
          error_text: status === "error" ? "Не удалось разобрать файл." : null,
        });
      })
    );

    const { result } = renderHook(() => useImportJob(81, { contractId: 12 }), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await waitFor(() => expect(result.current.data?.status).toBe("pending"));
    // Интервал поллинга — 1500 мс; окно ожидания заведомо больше, чтобы
    // дождаться следующего опроса, который вернёт `error`.
    await waitFor(() => expect(result.current.data?.status).toBe("error"), { timeout: 8000 });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.contracts.card(12)));
    expect(keys).toContain(JSON.stringify(qk.contracts.importJobs(12)));
    expect(keys).not.toContain(JSON.stringify(qk.passport.all));
    expect(keys).not.toContain(JSON.stringify(qk.review.all));
  });
});

/**
 * `useDeleteContract` (спека каскадного удаления §2.7): каскад уносит сметы и
 * позиции договора, поэтому устаревает всё, где договор виден или посчитан —
 * не только `contracts`/`passport` (которые хук уже инвалидировал), но и семь
 * дополнительных корней таблицы §2.7. Ключи проверяются ПО ОТДЕЛЬНОСТИ:
 * утверждение «вызвано девять раз» прошло бы и при девяти одинаковых ключах.
 */
describe("useDeleteContract: инвалидация после каскадного удаления", () => {
  it("удаление договора инвалидирует все поверхности, где он виден", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    server.use(
      http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 }))
    );

    const { result } = renderHook(() => useDeleteContract(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync(1);
    });

    // Ключи проверяются ПО ОТДЕЛЬНОСТИ: «вызвано девять раз» прошло бы и при
    // девяти одинаковых ключах.
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    for (const root of [
      qk.contracts.all,
      qk.importJobs.all,
      qk.passport.all,
      qk.matrix.all,
      qk.dashboard.all,
      qk.review.all,
      qk.objects.all,
      qk.contractors.all,
      qk.rateClasses.all,
    ]) {
      expect(keys).toContain(JSON.stringify(root));
    }
  });
});

describe("useProjectPassport: переиспользование корня паспорта (Ф6)", () => {
  it("корень паспорта переиспользован новым хуком", async () => {
    const queryClient = createTestQueryClient();

    renderHook(() => useProjectPassport(10), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await waitFor(() => {
      expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0);
    });

    const [query] = queryClient.getQueryCache().getAll();
    const key = query.queryKey as unknown[];
    expect(key.slice(0, qk.passport.all.length)).toEqual([...qk.passport.all]);
  });

  /**
   * Без `enabled` хук ушёл бы в `GET /project-passport/0` при первом рендере
   * (тот же класс дефекта, что P3 у F5, и та же техника проверки, что у
   * `ContractCardPage.test.tsx` — «не запрашивает объект по подставному id,
   * пока договор не загружен»): МНОЖЕСТВО запрошенных id, а не «нет нуля»,
   * потому что подставным значением мог бы стать и 0, и строка "undefined".
   */
  it("паспорт не запрашивается, пока договор не известен", async () => {
    const requested: string[] = [];
    server.use(
      http.get("/api/v1/analytics/project-passport/:contractId", ({ params }) => {
        requested.push(String(params.contractId));
        return HttpResponse.json(sampleProjectPassport);
      })
    );

    renderHook(() => useProjectPassport(undefined), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
      ),
    });

    // Даём эффектам шанс отправить запрос, если защиты `enabled` нет.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(new Set(requested)).toEqual(new Set());
  });
});

/**
 * `useSetCategoryOverride` / `useClearCategoryOverride` (спека разноса §2.6):
 * обе мутации обязаны инвалидировать паспорт ДОГОВОРА, потому что эндпоинты
 * PUT/DELETE его не возвращают — они возвращают только сводку изменений
 * (`chapters_updated`/`additional_works_updated`/`chapters_manual`), а не
 * паспорт (форма паспорта объявлена ровно один раз, вторая копия расползлась
 * бы). Без инвалидации экран, уже открытый на паспорте, показывал бы старое
 * «Нераспределённое» — тест должен доказывать именно это, а не сам факт
 * похода за PUT/DELETE.
 *
 * Паспорт заранее кладётся в кэш через `setQueryData` — БЕЗ активного
 * наблюдателя (никакой `useProjectPassport` не рендерится). Так `isInvalidated`
 * не зависит от гонки с фоновым рефетчем: TanStack Query по умолчанию
 * перезапрашивает при инвалидации только АКТИВНЫЕ (наблюдаемые) запросы, а
 * инвалидированный неактивный запрос просто помечается и остаётся в этом виде
 * до следующего наблюдателя. `gcTime` ключа паспорта поднят точечным
 * `setQueryDefaults`: у тестового клиента `gcTime: 0`, и без этого правки
 * запись могла бы уйти в сборку мусора до того, как мутация успеет её
 * инвалидировать — тест был бы хрупким независимо от корректности `onSuccess`.
 *
 * Уберите `onSuccess` в хуке — `isInvalidated` останется `false`, и
 * `toBe(true)` упадёт: проверка не вакуумная.
 *
 * Второй, НЕсвязанный договор (другой ключ `qk.passport.project`) заведён в
 * том же кэше и проверяется отдельно — он обязан остаться `isInvalidated:
 * false`. Без этой половины проверки хук, инвалидирующий `qk.passport.all`
 * (или вовсе всё) целиком, прошёл бы тест так же зелено: узкий ключ
 * `qk.passport.project(contractId)` выбран НАМЕРЕННО, чтобы правка одного
 * договора не роняла кэш паспортов остальных, — и это ровно то, что первая
 * половина проверки одна доказать не может.
 *
 * Карточка договора (`qk.contracts.card`) проверяется в тех же тестах, а не
 * отдельным блоком (находка ревью PR #16): `EstimateUploadPanel` считает
 * `category_overrides_count` из ЭТОГО запроса, а не из паспорта, — без
 * инвалидации карточка оставалась бы устаревшей для формы замены и для
 * любого другого потребителя `useContract`, хотя паспорт уже обновился.
 */
describe("useSetCategoryOverride / useClearCategoryOverride: инвалидация паспорта договора", () => {
  it("после назначения статьи паспорт и карточка ЭТОГО договора помечаются устаревшими, а чужие — нет", async () => {
    const queryClient = createTestQueryClient();
    const passportKey = qk.passport.project(5);
    const otherPassportKey = qk.passport.project(99);
    const cardKey = qk.contracts.card(5);
    const otherCardKey = qk.contracts.card(99);
    queryClient.setQueryDefaults(passportKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(otherPassportKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(cardKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(otherCardKey, { gcTime: 60_000 });
    queryClient.setQueryData(passportKey, sampleProjectPassport);
    queryClient.setQueryData(otherPassportKey, sampleProjectPassport);
    queryClient.setQueryData(cardKey, sampleContractCard);
    queryClient.setQueryData(otherCardKey, sampleContractCard);
    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherPassportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(cardKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherCardKey)?.isInvalidated).toBe(false);

    const { result } = renderHook(() => useSetCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync({
        estimateId: 11,
        positionItemId: 42,
        workCategoryId: 20,
        contractId: 5,
      });
    });

    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(cardKey)?.isInvalidated).toBe(true);
    // Договор 99 ни при чём — его кэш не должен шевельнуться.
    expect(queryClient.getQueryState(otherPassportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherCardKey)?.isInvalidated).toBe(false);
  });

  it("после снятия ручного решения паспорт и карточка ЭТОГО договора помечаются устаревшими, а чужие — нет", async () => {
    const queryClient = createTestQueryClient();
    const passportKey = qk.passport.project(7);
    const otherPassportKey = qk.passport.project(99);
    const cardKey = qk.contracts.card(7);
    const otherCardKey = qk.contracts.card(99);
    queryClient.setQueryDefaults(passportKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(otherPassportKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(cardKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(otherCardKey, { gcTime: 60_000 });
    queryClient.setQueryData(passportKey, sampleProjectPassport);
    queryClient.setQueryData(otherPassportKey, sampleProjectPassport);
    queryClient.setQueryData(cardKey, sampleContractCard);
    queryClient.setQueryData(otherCardKey, sampleContractCard);
    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherPassportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(cardKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherCardKey)?.isInvalidated).toBe(false);

    const { result } = renderHook(() => useClearCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync({ estimateId: 11, positionItemId: 42, contractId: 7 });
    });

    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(cardKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(otherPassportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherCardKey)?.isInvalidated).toBe(false);
  });

  it("назначение и снятие статьи инвалидируют контексты каталога", async () => {
    // `membership_state` контекста (CURRENT/STALE) зависит от статьи
    // раздела — без этой инвалидации карточка/очередь экрана /families,
    // открытые заранее, продолжали бы показывать прежнее состояние.
    const setClient = createTestQueryClient();
    const setInvalidateSpy = vi.spyOn(setClient, "invalidateQueries");

    const { result: setResult } = renderHook(() => useSetCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={setClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await setResult.current.mutateAsync({
        estimateId: 11, positionItemId: 42, workCategoryId: 20, contractId: 5,
      });
    });
    const setKeys = setInvalidateSpy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(setKeys).toContain(JSON.stringify(qk.semanticContexts.all));

    const clearClient = createTestQueryClient();
    const clearInvalidateSpy = vi.spyOn(clearClient, "invalidateQueries");
    const { result: clearResult } = renderHook(() => useClearCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={clearClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await clearResult.current.mutateAsync({ estimateId: 11, positionItemId: 42, contractId: 7 });
    });
    const clearKeys = clearInvalidateSpy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(clearKeys).toContain(JSON.stringify(qk.semanticContexts.all));
  });
});

/**
 * `useSetRoundCategoryOverride`/`useClearRoundCategoryOverride` — тот же
 * факт, что и у сметного разноса выше, только на стороне раунда тендера:
 * `membership_state` контекста зависит от статьи, назначенной строке
 * раунда.
 */
describe("useSetRoundCategoryOverride / useClearRoundCategoryOverride: инвалидация контекстов каталога", () => {
  it("назначение статьи раунда инвалидирует контексты каталога", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useSetRoundCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync({
        tenderId: 1, roundId: 2, lotKey: "lot-1", positionKey: "pos-1", workCategoryId: 20,
        note: null,
      });
    });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.semanticContexts.all));
  });

  it("снятие статьи раунда инвалидирует контексты каталога", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useClearRoundCategoryOverride(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync({
        tenderId: 1, roundId: 2, lotKey: "lot-1", positionKey: "pos-1",
      });
    });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.semanticContexts.all));
  });
});

/**
 * `useSetEstimateVat` (спека пересчёта §2.7, задача 9): правка ставок НДС сметы
 * меняет ВСЕ деньги паспорта договора (ставка показа затрагивает весь пересчёт),
 * поэтому мутация обязана инвалидировать паспорт ИМЕННО этого договора. Форма —
 * как у `useSetCategoryOverride` выше: паспорт заранее кладётся в кэш через
 * `setQueryData` без активного наблюдателя, `gcTime` поднят точечным
 * `setQueryDefaults` (у тестового клиента `gcTime: 0`), а второй,
 * НЕсвязанный договор заведён в том же кэше и проверяется отдельно — узкий
 * ключ `qk.passport.project(contractId)` выбран НАМЕРЕННО, и без второй
 * половины проверки хук, инвалидирующий всё подряд (`qk.passport.all` или
 * весь кэш), прошёл бы тест так же зелено.
 */
describe("useSetEstimateVat: инвалидация паспорта договора", () => {
  it("useSetEstimateVat инвалидирует паспорт этого договора и не трогает чужой", async () => {
    const queryClient = createTestQueryClient();
    const passportKey = qk.passport.project(5);
    const otherKey = qk.passport.project(99);
    queryClient.setQueryDefaults(passportKey, { gcTime: 60_000 });
    queryClient.setQueryDefaults(otherKey, { gcTime: 60_000 });
    queryClient.setQueryData(passportKey, sampleProjectPassport);
    queryClient.setQueryData(otherKey, sampleProjectPassport);
    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(otherKey)?.isInvalidated).toBe(false);

    server.use(
      http.patch("/api/v1/estimates/:id/vat", () =>
        HttpResponse.json({
          estimate_id: 11,
          vat_rate_base_override: null,
          vat_rate_target: "16",
          vat_rate_updated_at: "2026-08-13T10:00:00Z",
        })
      )
    );

    const { result } = renderHook(() => useSetEstimateVat(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync({ estimateId: 11, contractId: 5, input: { target: "16" } });
    });

    expect(queryClient.getQueryState(passportKey)?.isInvalidated).toBe(true);
    // Договор 99 ни при чём — его кэш не должен шевельнуться.
    expect(queryClient.getQueryState(otherKey)?.isInvalidated).toBe(false);
  });
});

/**
 * `useMergeReview` (спека `2026-09-22-catalog-families-design.md` §2.8; план,
 * задача 9): `warnings` — аддитивное поле ответа слияния. Три утверждения из
 * плана различают ВИД тоста и его кратность, а не наличие: `toast.success` о
 * выполненном слиянии есть **всегда** («тоста нет» было бы ложным
 * утверждением при любом входе), поэтому тесты проверяют:
 * непустой `warnings` → `toast.warning` с текстом предупреждения; пустой
 * `warnings` → ни одного `toast.warning`; `toast.success` — РОВНО один в
 * обоих случаях (предупреждение не заменяет и не дублирует подтверждение
 * успеха).
 */
describe("useMergeReview: тосты различают вид, а не наличие", () => {
  const mergeTarget = {
    id: 801,
    standard_job_title: "Стяжка цементная",
    normalized_job_title: "стяжка цементный",
    kind: "POSITION",
    unit_id: 5,
    unit_code: "M2",
    unit_name: "Кв. метр",
  };

  // `vi.spyOn` на `toast.*` держит счётчик вызовов через границы тестов
  // (vitest здесь не настроен на автосброс) — без восстановления второй тест
  // унаследовал бы вызов первого и «пустой warnings» ложно считался бы
  // непустым.
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("непустой warnings вызывает toast.warning с текстом предупреждения", async () => {
    server.use(
      http.post("/api/v1/review/:id/merge", () =>
        HttpResponse.json({
          to_review_id: 10,
          target: mergeTarget,
          moved_positions: 3,
          warnings: ["Слияние свело разные семьи работ: «А» и «Б»."],
        })
      )
    );
    const successSpy = vi.spyOn(toast, "success");
    const warningSpy = vi.spyOn(toast, "warning");

    const { result } = renderHook(() => useMergeReview(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync({ toReviewId: 10, targetId: 801 });
    });

    expect(warningSpy).toHaveBeenCalledTimes(1);
    expect(warningSpy).toHaveBeenCalledWith("Слияние свело разные семьи работ: «А» и «Б».");
    // Третье утверждение — отдельное, не подразумеваемое: предупреждение не
    // заменяет и не дублирует подтверждение успеха.
    expect(successSpy).toHaveBeenCalledTimes(1);
  });

  it("пустой warnings не вызывает ни одного toast.warning", async () => {
    server.use(
      http.post("/api/v1/review/:id/merge", () =>
        HttpResponse.json({
          to_review_id: 10,
          target: mergeTarget,
          moved_positions: 3,
          warnings: [],
        })
      )
    );
    const successSpy = vi.spyOn(toast, "success");
    const warningSpy = vi.spyOn(toast, "warning");

    const { result } = renderHook(() => useMergeReview(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync({ toReviewId: 10, targetId: 801 });
    });

    expect(warningSpy).not.toHaveBeenCalled();
    expect(successSpy).toHaveBeenCalledTimes(1);
  });

  it("слияние архивирует и переселяет контексты — инвалидирует контексты каталога", async () => {
    server.use(
      http.post("/api/v1/review/:id/merge", () =>
        HttpResponse.json({
          to_review_id: 10,
          target: mergeTarget,
          moved_positions: 3,
          warnings: [],
        })
      )
    );
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useMergeReview(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync({ toReviewId: 10, targetId: 801 });
    });

    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(qk.semanticContexts.all));
  });
});

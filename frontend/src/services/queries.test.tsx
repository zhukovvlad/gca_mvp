import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { useProjectPassport, useUpdateObject } from "./queries";
import { qk } from "./queryKeys";
import { sampleProjectPassport } from "@/test/fixtures";
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

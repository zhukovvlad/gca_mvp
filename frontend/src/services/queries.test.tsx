import { act, renderHook } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { useUpdateObject } from "./queries";
import { qk } from "./queryKeys";
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

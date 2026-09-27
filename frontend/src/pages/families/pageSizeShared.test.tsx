import { render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Pager } from "@/components/domain/Pager";
import { AllProviders } from "@/test/utils";

import { usePersistedPageSize } from "./usePersistedPageSize";

/**
 * Один источник допустимых размеров страницы (`components/domain/pageSize.ts`,
 * спека §2.7): и умолчание `pageSizeOptions` у `Pager`, и
 * множество допустимых сохранённых значений у `usePersistedPageSize` обязаны
 * читать ОДНУ константу. Константа здесь подменена нарочно непохожим списком
 * `[7, 20]`: если хоть один потребитель держит свою копию `[10, 20, 50, 100]`,
 * он не отреагирует — хук примет 50 или отвергнет 7, `Pager` покажет 10/50/100.
 * Отдельный файл — `vi.mock` действует на весь модуль теста.
 */
vi.mock("@/components/domain/pageSize", () => ({ PAGE_SIZE_OPTIONS: [7, 20] }));

const KEY = "gca.families.test.pageSize";

describe("PAGE_SIZE_OPTIONS — общий для Pager и usePersistedPageSize", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("хук принимает значение из подменённого списка", () => {
    localStorage.setItem(KEY, "7");
    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
    expect(result.current[0]).toBe(7);
  });

  it("хук отвергает значение, которого в подменённом списке нет (50)", () => {
    localStorage.setItem(KEY, "50");
    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
    expect(result.current[0]).toBe(20);
  });

  it("Pager без своего pageSizeOptions предлагает ровно подменённый список", async () => {
    const user = userEvent.setup();
    render(
      <Pager page={1} total={45} pageSize={20} onPageChange={vi.fn()} onPageSizeChange={vi.fn()} />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["7", "20"]);
  });
});

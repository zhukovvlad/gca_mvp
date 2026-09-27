import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pager } from "./Pager";

/**
 * Контракт base-ui: `Select.onValueChange` получает `string | null`
 * (`SelectRoot.d.ts`, одиночный выбор допускает `null`). Настоящий `Select`
 * в jsdom `null` не выдаёт ни одним жестом, поэтому здесь примитив подменён
 * заглушкой, которая выдаёт оба значения по кнопке, — и проверяется сам
 * `Pager`: `null` не доходит до `onPageSizeChange` (иначе `Number(null)` = 0
 * стал бы размером страницы и `Math.ceil(total / 0)`), строка доходит числом.
 * Отдельный файл — потому что `vi.mock` действует на весь модуль теста.
 */
vi.mock("@/components/ui/select", () => ({
  Select: ({
    onValueChange,
    children,
  }: {
    onValueChange: (v: string | null) => void;
    children: ReactNode;
  }) => (
    <div>
      <button type="button" onClick={() => onValueChange(null)}>
        emit-null
      </button>
      <button type="button" onClick={() => onValueChange("50")}>
        emit-50
      </button>
      {children}
    </div>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: () => null,
  SelectContent: () => null,
  SelectItem: () => null,
}));

describe("Pager: null из onValueChange не становится размером страницы", () => {
  it("null отбрасывается, строка приходит числом", async () => {
    const user = userEvent.setup();
    const onPageSizeChange = vi.fn();
    render(
      <Pager
        page={1}
        total={45}
        pageSize={10}
        onPageChange={vi.fn()}
        onPageSizeChange={onPageSizeChange}
      />
    );

    await user.click(screen.getByRole("button", { name: "emit-null" }));
    expect(onPageSizeChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "emit-50" }));
    expect(onPageSizeChange).toHaveBeenCalledExactlyOnceWith(50);
  });
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./button";

describe("Button — состояние загрузки", () => {
  it("loading: кнопка недоступна, помечена занятой, нажатие не доходит до обработчика", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Сохранить
      </Button>
    );

    const button = screen.getByRole("button", { name: "Сохранить" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button.querySelector("svg.animate-spin")).not.toBeNull();
    await userEvent.setup().click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("без loading: кнопка доступна, не занята и без крутилки; disabled работает как прежде", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Сохранить</Button>);

    const button = screen.getByRole("button", { name: "Сохранить" });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button.querySelector("svg")).toBeNull();
    await userEvent.setup().click(button);
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button disabled onClick={onClick}>
        Сохранить
      </Button>
    );
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Сохранить" })).not.toHaveAttribute("aria-busy");
  });
});

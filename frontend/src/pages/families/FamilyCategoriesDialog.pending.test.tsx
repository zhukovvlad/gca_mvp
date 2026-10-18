import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { FamilyCategoriesDialog } from "./FamilyCategoriesDialog";

/** Состояние ожидания окна «Категории семей»: «Удалить» крутится только у нажатой строки. */

function rowOf(title: string): HTMLElement {
  return screen.getByText(title, { selector: "td" }).closest("tr")!;
}

describe("FamilyCategoriesDialog — ожидание удаления", () => {
  it("«Удалить»: крутится кнопка нажатой строки, у соседней — заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("delete", "/api/v1/semantic/family-categories/:id");
    renderWithProviders(<FamilyCategoriesDialog open onOpenChange={vi.fn()} />);
    await waitForDialogFocus();
    await screen.findByText("Инженерная система");

    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" }));
    await user.click(await screen.findByRole("button", { name: "Удалить категорию" }));

    const own = within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" });
    await waitFor(() => expect(own).toHaveAttribute("aria-busy", "true"));
    expect(own).toBeDisabled();
    const neighbour = within(rowOf("Затраты и услуги")).getByRole("button", { name: "Удалить" });
    expect(neighbour).toBeDisabled();
    expect(neighbour).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() =>
      expect(screen.queryByText("Проектирование", { selector: "td" })).not.toBeInTheDocument()
    );
  });
});

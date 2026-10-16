import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { MarkPositionDialog } from "./MarkPositionDialog";

/** Состояние ожидания «Пометить написание целиком…»: крутится нажатая кнопка, соседняя заблокирована. */

describe("MarkPositionDialog — ожидание пометки", () => {
  it("«Пометить заголовком»: она занята, «Пометить мусором» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const hold = holdRequests("post", "/api/v1/semantic/positions/:id/kind");
    renderWithProviders(
      <MarkPositionDialog positionId={8001} title="Штукатурка стен" open onOpenChange={onOpenChange} />
    );

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Пометить заголовком" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Пометить заголовком" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Пометить мусором" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Пометить мусором" })).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), { timeout: 8000 });
  });
});

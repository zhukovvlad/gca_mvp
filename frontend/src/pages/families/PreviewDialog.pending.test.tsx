import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { PreviewDialog } from "./PreviewDialog";

/** Состояние ожидания окна постановки заданий: подтверждение на время запроса. */

describe("PreviewDialog — ожидание подтверждения", () => {
  it("«Поставить в очередь»: на время запроса кнопка занята, ответ пришёл — окно закрыто", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const hold = holdRequests("post", "/api/v1/semantic/unit-reask");
    renderWithProviders(
      <PreviewDialog target={{ kind: "unit", unitId: 5, unitCode: "M2" }} unitLabel="м²" onClose={onClose} />
    );
    await screen.findByText("214");

    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Поставить в очередь" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();

    hold.release();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { PendingFamilyBlock } from "./PendingFamilyBlock";

/** Состояние ожидания блока «Ожидает семьи»: «Отменить» на время запроса. */

describe("PendingFamilyBlock — ожидание «Отменить»", () => {
  it("на время запроса кнопка занята, ответ пришёл — запрос один", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("delete", "/api/v1/semantic/contexts/:id/pending-family");
    renderWithProviders(
      <PendingFamilyBlock
        contextId={601}
        pending={{
          family_id: 43,
          family_title: "Кровельные работы",
          source: "manual",
          by: 1,
          at: "2026-10-05T09:30:00+00:00",
          threshold: null,
          suggestion_id: null,
        }}
      />
    );
    const button = screen.getByRole("button", { name: "Отменить ожидание семьи" });

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();

    hold.release();
    await waitFor(() => expect(button).not.toHaveAttribute("aria-busy"), { timeout: 8000 });
    expect(hold.seen.count).toBe(1);
  });
});

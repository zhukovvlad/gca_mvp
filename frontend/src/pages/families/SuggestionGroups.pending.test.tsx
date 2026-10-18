import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { SuggestionGroups } from "./SuggestionGroups";

/** Состояние ожидания окна «Другая семья…»: «Назначить» на время запроса. */

describe("SuggestionGroups — ожидание «Другая семья…»", () => {
  it("«Назначить»: на время запроса кнопка занята, ответ пришёл — окно закрыто", async () => {
    const user = userEvent.setup();
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({ ...base, id: 900, title: "Устройство покрытий", unit_code: "M2", unit_symbol: "м²" });
    const hold = holdRequests("post", "/api/v1/semantic/suggestions/:id/other-family");
    renderWithProviders(
      <SuggestionGroups
        groups={structuredClone(handlerState.suggestionGroups)}
        unitLabel={(code) => (code === "M2" ? "м²" : (code ?? "без единицы"))}
      />
    );
    const row = within(screen.getAllByTestId("suggestion-group")[0]).getAllByTestId("suggestion-row")[1];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }));
    await user.click(await screen.findByRole("option", { name: "Устройство покрытий" }));

    await user.click(screen.getByRole("button", { name: "Назначить" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Назначить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Назначить" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(hold.seen.count).toBe(1);
  });
});

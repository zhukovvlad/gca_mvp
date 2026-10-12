import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { discoveryUnitFixture, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { DiscoveryLaunchDialog } from "./DiscoveryLaunchDialog";

/** Состояния ожидания окна запуска открытия: недоступная кнопка до оценки и занятая кнопка запуска. */

function renderDialog() {
  renderWithProviders(<DiscoveryLaunchDialog target={{ unitId: 3, unitLabel: "м³" }} onClose={vi.fn()} />);
}

describe("DiscoveryLaunchDialog — ожидание", () => {
  it("пока оценка грузится: чисел нет и кнопка недоступна; оценка пришла — числа и доступная кнопка", async () => {
    handlerState.discovery.units = [discoveryUnitFixture({ systems: 5 })];
    const hold = holdRequests("post", "/api/v1/semantic/discovery/preview");
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("Различных наименований")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toBeDisabled();

    hold.release();
    await within(dialog).findByText("Различных наименований");
    expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toBeEnabled();
  });

  it("«Открыть семьи»: на время запуска кнопка занята", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture({ systems: 5 })];
    const hold = holdRequests("post", "/api/v1/semantic/discovery");
    renderDialog();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Различных наименований");

    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});

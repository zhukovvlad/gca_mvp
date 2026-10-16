import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

import { SuggestionsTab } from "./SuggestionsTab";

/** Состояния ожидания очереди «Ошибки»: «Повторить» и решения по задержанным. */

async function openErrors(user: ReturnType<typeof userEvent.setup>) {
  renderWithProviders(<SuggestionsTab />);
  await screen.findAllByTestId("suggestion-group");
  await user.click(screen.getByRole("tab", { name: /Ошибки/ }));
  await screen.findByTestId("errors-queue");
}

describe("Очередь «Ошибки» — ожидание действий", () => {
  it("«Повторить»: крутится кнопка нажатого задания, у соседнего — доступна", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/jobs/:id/retry");
    await openErrors(user);
    const jobs = screen.getAllByTestId("error-job");

    await user.click(within(jobs[0]).getByRole("button", { name: "Повторить" }));

    await waitFor(() =>
      expect(within(jobs[0]).getByRole("button", { name: "Повторить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(jobs[0]).getByRole("button", { name: "Повторить" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("error-job")).toHaveLength(1));
  });

  it("«Отправить» задержанного: крутится она, «Не отправлять» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/jobs/:id/privacy-release");
    await openErrors(user);
    const held = screen.getAllByTestId("held-job");

    await user.click(within(held[0]).getByRole("button", { name: "Отправить" }));

    await waitFor(() =>
      expect(within(held[0]).getByRole("button", { name: "Отправить" })).toHaveAttribute("aria-busy", "true")
    );
    const decline = within(held[0]).getByRole("button", { name: "Не отправлять" });
    expect(decline).toBeDisabled();
    expect(decline).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Не отправлять»: крутится она, «Отправить» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/jobs/:id/privacy-decline");
    await openErrors(user);
    const held = screen.getAllByTestId("held-job");

    await user.click(within(held[0]).getByRole("button", { name: "Не отправлять" }));

    await waitFor(() =>
      expect(within(held[0]).getByRole("button", { name: "Не отправлять" })).toHaveAttribute("aria-busy", "true")
    );
    const release = within(held[0]).getByRole("button", { name: "Отправить" });
    expect(release).toBeDisabled();
    expect(release).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Отправить все» у группы единицы: кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/unit-privacy-release");
    await openErrors(user);
    const group = screen.getAllByTestId("unit-hold-group")[0];

    await user.click(within(group).getByRole("button", { name: /Отправить все/ }));

    await waitFor(() =>
      expect(within(group).getByRole("button", { name: /Отправить все/ })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(group).getByRole("button", { name: /Отправить все/ })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});

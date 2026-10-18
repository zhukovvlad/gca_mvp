import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { NewRow } from "@/types/domain";

import { CreateFamilyDialog } from "./CreateFamilyDialog";

/** Состояние ожидания «Завести семью…»: «Сохранить и активировать» на время запроса. */

describe("CreateFamilyDialog — ожидание сохранения", () => {
  it("на время запроса кнопка занята и второе нажатие не уходит; ответ пришёл — запрос один", async () => {
    const user = userEvent.setup();
    const row = structuredClone(handlerState.newRows[0]) as NewRow & { suggestion_id: number };
    const hold = holdRequests("post", "/api/v1/semantic/suggestions/:id/create-family");
    renderWithProviders(<CreateFamilyDialog row={row} unitLabel="м²" onClose={vi.fn()} />);
    await waitForDialogFocus();
    await user.type(screen.getByLabelText(/Определение/), "Входит: шпонки. Не входит: мастики.");
    await user.click(screen.getByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    const save = () => screen.getByRole("button", { name: "Сохранить и активировать" });

    await user.click(save());

    await waitFor(() => expect(save()).toHaveAttribute("aria-busy", "true"));
    expect(save()).toBeDisabled();
    await user.click(save());

    hold.release();
    await waitFor(() => expect(handlerState.createFamilyRequests).toHaveLength(1));
    expect(hold.seen.count).toBe(1);
  });
});

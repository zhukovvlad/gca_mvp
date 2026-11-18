import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { renderWithProviders } from "@/test/utils";

/**
 * Вход по адресу `/families?tab=suggestions&queue=zones&chain=<id>` (ссылка с подписи «ждёт
 * решения администратора» строки-работы, лестница аналогов §2.20): вкладка «Предложения», очередь
 * «Зоны», над ней карточка этой цепочки. Фикстура: цепочка 2 «Временные сети» - спорная.
 */

describe("FamiliesPage: вход по ?chain=", () => {
  it("открывает «Предложения» → «Зоны» и показывает карточку цепочки над очередью", async () => {
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families?tab=suggestions&queue=zones&chain=2" });

    const card = await screen.findByTestId("zone-entry-card");
    await screen.findAllByTestId("zone-row");
    expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveAttribute("aria-selected", "true");
    expect(card).toHaveTextContent("Временные сети");
    expect(screen.getAllByTestId("zone-row").some((row) => row.dataset.highlighted === "true")).toBe(false);
  });
});

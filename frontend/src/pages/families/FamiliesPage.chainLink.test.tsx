import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { renderWithProviders } from "@/test/utils";

/**
 * Вход по адресу `/families?tab=suggestions&queue=zones&chain=<id>` (ссылка с подписи «ждёт
 * решения администратора» строки-работы, лестница аналогов §2.20): вкладка «Предложения», очередь
 * «Зоны», строка цепочки подсвечена. Фикстура: цепочка 2 «Временные сети» - спорная.
 */

describe("FamiliesPage: вход по ?chain=", () => {
  it("открывает «Предложения» → «Зоны» и подсвечивает цепочку", async () => {
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families?tab=suggestions&queue=zones&chain=2" });

    await screen.findAllByTestId("zone-row");
    expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveAttribute("aria-selected", "true");
    const marked = screen.getAllByTestId("zone-row").filter((row) => row.dataset.highlighted === "true");
    expect(marked).toHaveLength(1);
    expect(marked[0]).toHaveTextContent("Временные сети");
  });
});

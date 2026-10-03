import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * «Открыть семью» из вкладки «Предложения» (блок «Задержано проверкой», окно
 * «Завести семью…»): экран переходит на вкладку «Семьи» с этой семьёй в панели.
 * Семья 43 — активная «Кровельные работы»: фильтр «черновики» её не показывает,
 * поэтому переход обязан снять фильтр статуса.
 */
describe("FamiliesPage — переход к семье из «Предложений»", () => {
  it("открывает вкладку «Семьи» с выбранной активной семьёй", async () => {
    const user = userEvent.setup();
    handlerState.unitHoldGroups[0].family_id = 43;
    handlerState.unitHoldGroups[0].family_title = "Кровельные работы";
    renderWithProviders(<FamiliesPage />);

    await user.click(screen.getByRole("tab", { name: "Предложения" }));
    await user.click(await screen.findByRole("tab", { name: /Ошибки/ }));
    await user.click(await screen.findByRole("button", { name: "Открыть семью" }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true")
    );
    expect(await screen.findByDisplayValue("Кровельные работы")).toBeInTheDocument();
  });

  it("фокус, пришедший при открытой вкладке, снимает фильтр единицы и возвращает первую страницу", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<FamiliesTab focusFamilyId={null} />);
    // Черновики м² — 42 семьи, больше одной страницы; семья 43 — м³, фильтр её скрыл бы.
    await user.click(await screen.findByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Кв. метр" }));
    await screen.findByText("Семья работ №1");
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.queryByText("Семья работ №1")).not.toBeInTheDocument());

    rerender(<FamiliesTab focusFamilyId={43} />);

    expect(await screen.findByDisplayValue("Кровельные работы")).toBeInTheDocument();
    expect(await screen.findByText("Семья работ №1")).toBeInTheDocument();

    // Следующая ссылка на другую семью, пока вкладка открыта, открывает её.
    rerender(<FamiliesTab focusFamilyId={null} />);
    rerender(<FamiliesTab focusFamilyId={44} />);
    expect(await screen.findByDisplayValue("Демонтажные работы (снята)")).toBeInTheDocument();
  });

  it("повторный возврат на «Семьи» не открывает семью заново: фильтр «черновики», панели нет", async () => {
    const user = userEvent.setup();
    handlerState.unitHoldGroups[0].family_id = 43;
    renderWithProviders(<FamiliesPage />);

    await user.click(screen.getByRole("tab", { name: "Предложения" }));
    await user.click(await screen.findByRole("tab", { name: /Ошибки/ }));
    await user.click(await screen.findByRole("button", { name: "Открыть семью" }));
    await screen.findByDisplayValue("Кровельные работы");

    await user.click(screen.getByRole("tab", { name: "Предложения" }));
    await user.click(screen.getByRole("tab", { name: "Семьи" }));

    expect(await screen.findAllByText(/Семья работ №/)).not.toHaveLength(0);
    expect(screen.queryByDisplayValue("Кровельные работы")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Статус")).toHaveTextContent("черновик");
  });
});

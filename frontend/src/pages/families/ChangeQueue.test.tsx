import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { ChangeQueue } from "./ChangeQueue";

/**
 * Очередь «Смена семьи» (спека `2026-10-02-catalog-variants-design.md` §2.12): группы
 * «семья → семья + полоса», «Подтвердить» и «Отклонить» — как в очереди «Семья из списка».
 * Данные — `handlerState.changeGroups`: «Кровельные работы → Геотекстиль» (≥ 0,9, две строки)
 * и «Геотекстиль → Кровельные работы» (< 0,7, одна).
 */

const LATER = { timeout: 8000 };

afterEach(() => {
  localStorage.clear();
});

function renderQueue() {
  return renderWithProviders(
    <ChangeQueue groups={handlerState.changeGroups} unitLabel={(code) => (code === "M2" ? "м²" : (code ?? "без единицы"))} />
  );
}

describe("ChangeQueue", () => {
  it("печатает группы «семья → семья» с полосой уверенности и числом строк", () => {
    renderQueue();

    const groups = screen.getAllByTestId("suggestion-group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveTextContent("Кровельные работы → Геотекстиль");
    expect(groups[0]).toHaveTextContent("≥ 0,9");
    expect(groups[0]).toHaveTextContent("м² · 2");
    expect(groups[1]).toHaveTextContent("Геотекстиль → Кровельные работы");
    expect(groups[1]).toHaveTextContent("< 0,7");
  });

  it("группы с одной предложенной семьёй и полосой, но разными прежними семьями, остаются двумя группами", () => {
    const [template] = handlerState.changeGroups;
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithProviders(
      <ChangeQueue
        groups={[
          { ...template, from_family_id: 43, from_family_title: "Кровельные работы" },
          { ...template, from_family_id: 44, from_family_title: "Фасадные работы" },
        ]}
        unitLabel={() => "м²"}
      />
    );

    const groups = screen.getAllByTestId("suggestion-group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveTextContent("Кровельные работы → Геотекстиль");
    expect(groups[1]).toHaveTextContent("Фасадные работы → Геотекстиль");
    // Одинаковый ключ двух групп React ругает в консоль: ключ обязан различать прежнюю семью.
    expect(consoleError.mock.calls.some((call) => String(call[0]).includes("same key"))).toBe(false);
    consoleError.mockRestore();
  });

  it("«Подтвердить отмеченные» шлёт отмеченные предложения группы", async () => {
    const user = userEvent.setup();
    renderQueue();

    const first = screen.getAllByTestId("suggestion-group")[0];
    await user.click(within(first).getByRole("button", { name: "Подтвердить отмеченные 2" }));

    await waitFor(() => expect(handlerState.confirmSuggestionsRequests).toEqual([[21, 22]]), LATER);
  });

  it("исход подтверждения — про смену семьи, а не про «получил семью»", async () => {
    const user = userEvent.setup();
    renderQueue();

    const first = screen.getAllByTestId("suggestion-group")[0];
    await user.click(within(first).getByRole("button", { name: "Подтвердить отмеченные 2" }));

    expect(await screen.findByText(/Смена семьи принята: 2/, {}, LATER)).toBeInTheDocument();
    expect(screen.getByText(/после значений по схеме новой семьи/)).toBeInTheDocument();
    expect(screen.queryByText(/получили семью/)).not.toBeInTheDocument();
  });

  it("«Отклонить» шлёт отклонение строки", async () => {
    const user = userEvent.setup();
    renderQueue();

    const second = screen.getAllByTestId("suggestion-group")[1];
    await user.click(within(second).getByRole("button", { name: "Раскрыть группу" }));
    await user.click(within(second).getByRole("button", { name: "Отклонить" }));

    await waitFor(() => expect(handlerState.rejectSuggestionRequests).toEqual([23]), LATER);
    expect(await screen.findByText(/Семья контекста остаётся прежней/, {}, LATER)).toBeInTheDocument();
  });

  it("«Другая семья…» не предлагает ни предложенную, ни нынешнюю семью контекстов", async () => {
    const user = userEvent.setup();
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    for (const [id, title] of [
      [900, "Устройство покрытий"],
      [901, "Нынешняя семья"],
      [902, "Предложенная семья"],
    ] as const) {
      handlerState.workFamilies.push({ ...base, id, title, unit_code: "M2", unit_symbol: "м²" });
    }
    const [template] = handlerState.changeGroups;
    renderWithProviders(
      <ChangeQueue
        groups={[
          {
            ...template,
            from_family_id: 901,
            from_family_title: "Нынешняя семья",
            family_id: 902,
            family_title: "Предложенная семья",
          },
        ]}
        unitLabel={() => "м²"}
      />
    );

    const row = within(screen.getAllByTestId("suggestion-group")[0]).getAllByTestId("suggestion-row")[0];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }, LATER));

    expect(await screen.findByRole("option", { name: "Устройство покрытий" }, LATER)).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Нынешняя семья" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Предложенная семья" })).not.toBeInTheDocument();
  });

  it("«Другая семья…» в очереди смены не утверждает назначения: у контекста с вариантом оно ждёт значений", async () => {
    const user = userEvent.setup();
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({ ...base, id: 900, title: "Устройство покрытий", unit_code: "M2", unit_symbol: "м²" });
    renderQueue();

    const row = within(screen.getAllByTestId("suggestion-group")[0]).getAllByTestId("suggestion-row")[0];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }, LATER));
    await user.click(await screen.findByRole("option", { name: "Устройство покрытий" }, LATER));
    await user.click(screen.getByRole("button", { name: "Назначить" }));

    await waitFor(() => expect(handlerState.otherFamilyRequests).toEqual([{ suggestionId: 21, familyId: 900 }]), LATER);
    expect(await screen.findByText(/после значений по схеме новой семьи/, {}, LATER)).toBeInTheDocument();
    expect(screen.queryByText(/Назначена семья/)).not.toBeInTheDocument();
  });

  it("страницы — по группам: одиннадцатая группа уходит на вторую страницу", () => {
    const [template] = handlerState.changeGroups;
    const groups = Array.from({ length: 11 }, (_, i) => ({
      ...template,
      from_family_id: 1000 + i,
      from_family_title: `Прежняя ${String(i + 1).padStart(2, "0")}`,
    }));
    renderWithProviders(<ChangeQueue groups={groups} unitLabel={() => "м²"} />);

    const shown = screen.getAllByTestId("suggestion-group");
    expect(shown).toHaveLength(10);
    expect(screen.queryByText(/Прежняя 11/)).not.toBeInTheDocument();
  });

  it("пустая очередь: «Смен семьи нет»", () => {
    renderWithProviders(<ChangeQueue groups={[]} unitLabel={(code) => code ?? "без единицы"} />);

    expect(screen.getByText("Смен семьи нет")).toBeInTheDocument();
    expect(screen.queryAllByTestId("suggestion-group")).toHaveLength(0);
  });
});

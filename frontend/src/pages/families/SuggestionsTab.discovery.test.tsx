import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { discoveryUnitFixture, handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Блок «Открыть семьи» во вкладке «Предложения» (спека 3б §2.13): виден только в очереди
 * «Новая», над таблицей, и не меняет её. Единицы блока — M3 («м³») и M2 («м²»).
 */

async function openNew(user: ReturnType<typeof userEvent.setup>) {
  const { unmount } = renderWithProviders(<SuggestionsTab />);
  await screen.findAllByTestId("suggestion-group");
  await user.click(screen.getByRole("tab", { name: /Новая/ }));
  await screen.findByTestId("new-queue");
  return unmount;
}

/** HTML таблицы без сгенерированных id (`base-ui-_r_…`): они зависят от порядка монтирования, а не от вида. */
function stableHtml(element: HTMLElement): string {
  return element.outerHTML.replace(/(base-ui-)?_r_[a-z0-9]+_/g, "ID");
}

afterEach(() => {
  localStorage.clear();
});

describe("SuggestionsTab — блок «Открыть семьи»", () => {
  it("в «Новой» блок стоит над таблицей, строки подписаны символом единицы", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [
      discoveryUnitFixture(),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2" }),
    ];
    await openNew(user);

    const block = await screen.findByTestId("discovery-block");
    const table = screen.getByTestId("new-queue");
    expect(block.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(block).getAllByTestId("discovery-unit-row")).toHaveLength(2);
    expect(within(block).getByText("м³", { selector: "b" })).toBeInTheDocument();
    expect(within(block).getByText("м²", { selector: "b" })).toBeInTheDocument();
  });

  it("таблица «Новой» с блоком — та же, что без него: колонки, строки, кнопки, подсказка", async () => {
    const user = userEvent.setup();
    const unmount = await openNew(user);
    const without = stableHtml(screen.getByTestId("new-queue"));
    const rowsWithout = screen.getAllByTestId("new-row").length;
    const hintWithout = screen.getByText(/Заведите семью из любой строки/).textContent;
    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();

    // Тот же экран, но у сервера есть единицы для открытия.
    unmount();
    localStorage.clear();
    handlerState.discovery.units = [discoveryUnitFixture()];
    await openNew(userEvent.setup());
    await screen.findByTestId("discovery-block");

    expect(stableHtml(screen.getByTestId("new-queue"))).toBe(without);
    expect(screen.getAllByTestId("new-row")).toHaveLength(rowsWithout);
    expect(screen.getByText(/Заведите семью из любой строки/).textContent).toBe(hintWithout);
    const header = screen.getByTestId("new-queue").firstElementChild as HTMLElement;
    expect(header.textContent).toBe("ЕдиницаИмя от ИИНаименование строкиУверен.");
  });

  it("блок виден и при пустой таблице «Новой» — над подписью «Пусто»", async () => {
    // Ревью задачи 6: ради этого случая блок смонтирован в SuggestionsTab, а не в NewQueue
    // (NewQueue на пустых строках отдаёт только EmptyState).
    const user = userEvent.setup();
    handlerState.newRows = [];
    handlerState.discovery.units = [discoveryUnitFixture()];
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    await user.click(screen.getByRole("tab", { name: /Новая/ }));

    const empty = await screen.findByText("В этой очереди при текущих фильтрах пусто.");
    const block = await screen.findByTestId("discovery-block");
    expect(screen.queryByTestId("new-queue")).not.toBeInTheDocument();
    expect(block.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(block).getByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
  });

  it("в остальных очередях блока нет и единицы для него не запрашиваются", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture()];
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");

    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Смена семьи/ }));
    await user.click(screen.getByRole("tab", { name: /Ошибки/ }));
    await screen.findByTestId("errors-queue");

    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();
    expect(handlerState.discovery.unitsRequests).toBe(0);
  });

  it("фильтр единицы очереди сужает и блок", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [
      discoveryUnitFixture(),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2" }),
    ];
    await openNew(user);
    await screen.findByTestId("discovery-block");

    await user.click(screen.getByLabelText("Единица"));
    await user.click(await screen.findByRole("option", { name: "м²" }));

    await waitFor(() => expect(screen.getAllByTestId("discovery-unit-row")).toHaveLength(1));
    expect(
      within(screen.getByTestId("discovery-unit-row")).getByText("м²", { selector: "b" })
    ).toBeInTheDocument();
  });
});

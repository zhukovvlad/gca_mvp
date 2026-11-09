import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState, ladderQueues } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { parseEntry } from "./entryParams";
import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Очереди «Зоны» и «Можно отпустить» внутри вкладки «Предложения» (лестница аналогов §2.20):
 * вкладки рядом с прежними, счётчики - из `/status`, у этих очередей нет фильтров единицы и полосы.
 */

afterEach(() => {
  localStorage.clear();
});

describe("SuggestionsTab: очереди лестницы аналогов", () => {
  it("вкладки «Зоны» и «Можно отпустить» рядом с прежними; счётчики из сводки /status", async () => {
    handlerState.queueStatus.zones_disputed = 37;
    handlerState.queueStatus.releasable_open = 52;
    renderWithProviders(<SuggestionsTab />);

    await waitFor(() => expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveTextContent("37"));
    expect(screen.getByRole("tab", { name: /^Можно отпустить/ })).toHaveTextContent("52");
    for (const name of [/Семья из списка/, /Смена семьи/, /^Новая/, /Ошибки/]) {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    }
  });

  it("до ответа /status счётчик - заполнитель, а не ноль", () => {
    renderWithProviders(<SuggestionsTab />);

    expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveTextContent("…");
    expect(screen.getByRole("tab", { name: /^Можно отпустить/ })).toHaveTextContent("…");
  });

  it("«Зоны» открывает очередь; фильтров единицы и полосы нет, запрос уходит только у открытой очереди", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    expect(ladderQueues.chainRequests).toHaveLength(0);

    await user.click(screen.getByRole("tab", { name: /^Зоны/ }));

    expect(await screen.findAllByTestId("zone-row")).toHaveLength(3);
    expect(screen.queryByLabelText("Единица")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Уверенность")).not.toBeInTheDocument();
  });

  it("«Можно отпустить» открывает очередь по семьям", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");

    await user.click(screen.getByRole("tab", { name: /^Можно отпустить/ }));

    expect(await screen.findAllByTestId("releasable-family")).toHaveLength(2);
    expect(screen.queryByLabelText("Единица")).not.toBeInTheDocument();
  });

  it("вход по ссылке с ?chain=: сразу очередь «Зоны», строка цепочки подсвечена", async () => {
    renderWithProviders(<SuggestionsTab chainId={2} />);

    await screen.findAllByTestId("zone-row");
    expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveAttribute("aria-selected", "true");
    const highlighted = screen.getAllByTestId("zone-row").filter((r) => r.dataset.highlighted === "true");
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0]).toHaveTextContent("Временные сети");
  });
});

describe("parseEntry: ?chain=", () => {
  it("tab=suggestions&queue=zones&chain=<id> - вход в очередь «Зоны»", () => {
    expect(parseEntry(new URLSearchParams("tab=suggestions&queue=zones&chain=7"))).toEqual({
      tab: "suggestions",
      queue: "zones",
      chainId: 7,
    });
  });

  it("chain без queue тоже ведёт в «Зоны»; некорректный chain и chain без вкладки входом не считаются", () => {
    expect(parseEntry(new URLSearchParams("tab=suggestions&chain=7"))).toEqual({
      tab: "suggestions",
      queue: "zones",
      chainId: 7,
    });
    expect(parseEntry(new URLSearchParams("tab=suggestions&queue=zones&chain=x"))).toBeNull();
    expect(parseEntry(new URLSearchParams("chain=7"))).toBeNull();
  });
});

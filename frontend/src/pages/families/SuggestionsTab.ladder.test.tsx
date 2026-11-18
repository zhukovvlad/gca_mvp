import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { chainRow, handlerState, LADDER_PAGE_SIZE, ladderQueues } from "@/test/handlers";
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

  it("вход по ссылке с ?chain=: сразу очередь «Зоны», над ней карточка этой цепочки, очередь — «Спорные»", async () => {
    renderWithProviders(<SuggestionsTab chainId={2} />);

    const card = await screen.findByTestId("zone-entry-card");
    await screen.findAllByTestId("zone-row");
    expect(screen.getByRole("tab", { name: /^Зоны/ })).toHaveAttribute("aria-selected", "true");
    expect(card).toHaveTextContent("Временные сети");
    expect(ladderQueues.chainRequests[0]).toContain("filter=disputed");
    expect(screen.getByTestId("zones-explain")).toBeInTheDocument();
    for (const row of screen.getAllByTestId("zone-row")) expect(row).not.toHaveAttribute("data-highlighted");
    expect(screen.queryByTestId("zone-not-found")).not.toBeInTheDocument();
  });

  // Стенд 18.11.2026: цепочка 27989 стояла на 69-й странице очереди, и ссылка её не показывала.
  it("цепочка дальше первой страницы и «Спорных», и «Всех»: карточка показывает её разделы, модель и четыре зоны", async () => {
    ladderQueues.chains = [
      ...Array.from({ length: LADDER_PAGE_SIZE + 1 }, (_, i) => chainRow(i + 1)),
      chainRow(900, { titles: ["Устройство кровли", "Тип КС1 - КС8"], proposed_zone: "underground", proposed_confidence: "0.85" }),
    ];
    renderWithProviders(<SuggestionsTab chainId={900} />);

    const card = await screen.findByTestId("zone-entry-card");
    await screen.findAllByTestId("zone-row");
    expect(card).toHaveTextContent("Устройство кровли / Тип КС1 - КС8");
    expect(card).toHaveTextContent("Модель: подземная · уверенность 0,85");
    for (const name of ["Подземная", "Надземная", "Территория", "Весь объект"]) {
      expect(within(card).getByRole("button", { name })).toBeInTheDocument();
    }
    // Её нет в очереди (первая страница — 1…50): карточка не зависит от очереди.
    expect(screen.getAllByTestId("zone-row")).toHaveLength(LADDER_PAGE_SIZE);
    expect(within(screen.getByTestId("zones-queue")).queryByText(/Устройство кровли/)).not.toBeInTheDocument();
  });

  it("без chain карточки нет", async () => {
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    await userEvent.setup().click(screen.getByRole("tab", { name: /^Зоны/ }));

    await screen.findAllByTestId("zone-row");
    expect(screen.queryByTestId("zone-entry-card")).not.toBeInTheDocument();
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

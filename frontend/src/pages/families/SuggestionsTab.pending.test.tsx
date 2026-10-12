import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Состояния ожидания вкладки «Предложения»: кнопки на время запроса, заглушки первой загрузки,
 * полоса перечитывания.
 */

afterEach(() => {
  localStorage.clear();
});

function group(index: number): HTMLElement {
  return screen.getAllByTestId("suggestion-group")[index];
}

describe("SuggestionsTab — ожидание действий", () => {
  it("«Подтвердить отмеченные»: на время запроса кнопка занята, отметки своей группы заблокированы, другая группа свободна", async () => {
    const user = userEvent.setup();
    handlerState.confirmSkippedIds = [1, 2, 3];
    const hold = holdRequests("post", "/api/v1/semantic/suggestions/confirm");
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    // Вторая группа раскрыта: иначе проверка «чужие отметки свободны» шла бы по пустому списку.
    await user.click(within(group(1)).getByRole("button", { name: "Раскрыть группу" }));
    expect(within(group(1)).getAllByRole("checkbox", { name: "Отметить строку" }).length).toBeGreaterThan(0);

    const first = within(group(0));
    await user.click(first.getByRole("button", { name: /Подтвердить отмеченные/ }));

    await waitFor(() =>
      expect(first.getByRole("button", { name: /Подтвердить отмеченные/ })).toHaveAttribute("aria-busy", "true")
    );
    expect(first.getByRole("button", { name: /Подтвердить отмеченные/ })).toBeDisabled();
    for (const checkbox of first.getAllByRole("checkbox", { name: "Отметить строку" })) {
      expect(checkbox).toHaveAttribute("aria-disabled", "true");
    }
    for (const button of first.getAllByRole("button", { name: "Отклонить" })) {
      expect(button).toBeDisabled();
    }
    for (const button of first.getAllByRole("button", { name: "Другая семья…" })) {
      expect(button).toBeDisabled();
    }
    const second = within(group(1));
    expect(second.getByRole("button", { name: /Подтвердить отмеченные/ })).toBeEnabled();
    expect(second.getByRole("button", { name: /Подтвердить отмеченные/ })).not.toHaveAttribute("aria-busy");
    for (const checkbox of second.getAllByRole("checkbox", { name: "Отметить строку" })) {
      expect(checkbox).not.toHaveAttribute("aria-disabled", "true");
    }
    for (const button of second.getAllByRole("button", { name: "Отклонить" })) {
      expect(button).toBeEnabled();
    }

    hold.release();
    await waitFor(() =>
      expect(first.getByRole("button", { name: /Подтвердить отмеченные/ })).toBeEnabled()
    );
    expect(first.getByRole("button", { name: /Подтвердить отмеченные/ })).not.toHaveAttribute("aria-busy");
    for (const checkbox of first.getAllByRole("checkbox", { name: "Отметить строку" })) {
      expect(checkbox).not.toHaveAttribute("aria-disabled", "true");
    }
  });

  it("«Отклонить»: крутится только нажатая кнопка, остальные кнопки группы заблокированы без крутилки", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/suggestions/:id/reject");
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");

    const rows = within(group(0)).getAllByTestId("suggestion-row");
    await user.click(within(rows[0]).getByRole("button", { name: "Отклонить" }));

    await waitFor(() =>
      expect(within(rows[0]).getByRole("button", { name: "Отклонить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(rows[0]).getByRole("button", { name: "Отклонить" })).toBeDisabled();
    expect(within(rows[1]).getByRole("button", { name: "Отклонить" })).toBeDisabled();
    expect(within(rows[1]).getByRole("button", { name: "Отклонить" })).not.toHaveAttribute("aria-busy");
    expect(within(group(1)).getByRole("button", { name: /Подтвердить отмеченные/ })).toBeEnabled();

    hold.release();
    await waitFor(() => expect(handlerState.rejectSuggestionRequests).toEqual([1]));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-row")).toHaveLength(2));
  });
});

describe("SuggestionsTab — первая загрузка", () => {
  it("до ответа заглушки и нет текста «пусто»; пустой ответ — текст «пусто» без заглушек", async () => {
    server.use(
      http.get("/api/v1/semantic/suggestions", () =>
        HttpResponse.json({ queue: "list", groups: [], items: [] })
      )
    );
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");
    renderWithProviders(<SuggestionsTab />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Предложений нет")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Предложений нет")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("непустой ответ: группы вместо заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");
    renderWithProviders(<SuggestionsTab />);
    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();

    hold.release();
    expect(await screen.findAllByTestId("suggestion-group")).toHaveLength(3);
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });
});

describe("SuggestionsTab — первая загрузка очереди «Смена семьи»", () => {
  it("пока очередь смены не пришла — заглушки без текста «пусто»; пришла — группы без заглушек", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/semantic/suggestions", async ({ request }) => {
        if (new URL(request.url).searchParams.get("queue") !== "change") return;
        await gate;
      })
    );
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");

    await user.click(screen.getByRole("tab", { name: /Смена семьи/ }));

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryAllByTestId("suggestion-group")).toHaveLength(0);
    expect(screen.queryByText("Смен семьи нет")).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });
});

describe("SuggestionsTab — перечитывание после действия", () => {
  it("старые группы остаются на месте, пока едут новые; полоса видна и затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument();

    // Перечитывание очереди зависает: ответ на подтверждение уже пришёл, новые данные — нет.
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");
    await user.click(within(group(2)).getByRole("button", { name: /Подтвердить отмеченные/ }));

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getAllByTestId("suggestion-group")).toHaveLength(3);
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });

  // Ревью пачки 2: полоса выбирается по показанной очереди — вход на каждую очередь, а не только на список.
  const noBar = () =>
    waitFor(() => expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument());

  it("очередь «Смена семьи»: после «Отклонить» группы на месте, полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    await user.click(screen.getByRole("tab", { name: /Смена семьи/ }));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");

    await user.click(within(group(0)).getAllByRole("button", { name: "Отклонить" })[0]);

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2);
    hold.release();
    await noBar();
  });

  it("очередь «Новая»: после «Завести семью…» строки на месте, полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    await user.click(screen.getByRole("tab", { name: /Новая/ }));
    await screen.findByTestId("new-queue");
    const row = screen.getAllByTestId("new-row").find((r) => /Гидрошпонка/.test(r.textContent ?? ""))!;
    await user.click(within(row).getByRole("button", { name: "Завести семью…" }));
    await waitForDialogFocus();
    await user.type(screen.getByLabelText(/Определение/), "Входит: шпонки. Не входит: мастики.");
    await user.click(screen.getByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");

    await user.click(screen.getByRole("button", { name: "Сохранить и активировать" }));

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getAllByTestId("new-row")).toHaveLength(4);
    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("new-row")).toHaveLength(3));
    await noBar();
  });

  it("очередь «Ошибки»: после «Повторить» задания на месте, полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SuggestionsTab />);
    await screen.findAllByTestId("suggestion-group");
    await user.click(screen.getByRole("tab", { name: /Ошибки/ }));
    await screen.findByTestId("errors-queue");
    const hold = holdRequests("get", "/api/v1/semantic/jobs");

    await user.click(within(screen.getAllByTestId("error-job")[0]).getByRole("button", { name: "Повторить" }));

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getAllByTestId("error-job")).toHaveLength(2);
    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("error-job")).toHaveLength(1));
    await noBar();
  });
});

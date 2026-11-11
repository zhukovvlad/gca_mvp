import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Счётчик новых записей на вкладке «Журнал ИИ» (спека упрощения каталога §2.7): записи после
 * последнего просмотра, `localStorage` под `gca.journal.seenAt`, всё в `try/catch` — без хранилища
 * счётчика нет, страница работает.
 */

const KEY = "gca.journal.seenAt";
const LATER = { timeout: 8000 };

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("счётчик новых записей журнала", () => {
  it("считает записи новее последнего просмотра", async () => {
    // Новее 10.09 12:12:30: записи 103 (12:13:00), 108 (12:13:30), 105 (12:14), 106 и 107 — пять.
    localStorage.setItem(KEY, "2026-09-10T12:12:30+00:00");
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });

    expect(await screen.findByRole("tab", { name: "Журнал ИИ 5" }, LATER)).toBeInTheDocument();
  });

  it("записей новее просмотра нет — числа нет", async () => {
    localStorage.setItem(KEY, "2099-01-01T00:00:00+00:00");
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });

    await waitFor(() => expect(handlerState.journalRequests.length).toBeGreaterThan(0), LATER);
    // Ответ уже пришёл и учтён: счётчик не появляется.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.getByRole("tab", { name: "Журнал ИИ" })).toBeInTheDocument();
  });

  it("открытие вкладки отмечает просмотр: число пропадает, ключ записан", async () => {
    const user = userEvent.setup();
    const before = Date.now();
    localStorage.setItem(KEY, "2026-09-10T12:12:30+00:00");
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });
    await user.click(await screen.findByRole("tab", { name: "Журнал ИИ 5" }, LATER));

    await waitFor(() => expect(screen.getByRole("tab", { name: "Журнал ИИ" })).toBeInTheDocument(), LATER);
    const stored = localStorage.getItem(KEY);
    expect(stored).not.toBeNull();
    expect(Date.parse(stored as string)).toBeGreaterThan(before);
  });

  it("первого просмотра ещё не было: число не показывается, точка отсчёта записывается", async () => {
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });

    await waitFor(() => expect(localStorage.getItem(KEY)).not.toBeNull(), LATER);
    expect(screen.getByRole("tab", { name: "Журнал ИИ" })).toBeInTheDocument();
  });

  it("хранилище бросает при чтении: счётчика нет, вкладка и журнал работают", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("доступ запрещён");
    });
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });

    const tab = await screen.findByRole("tab", { name: "Журнал ИИ" });
    await user.click(tab);
    await waitFor(() => expect(document.querySelector("[data-journal-id]")).not.toBeNull(), LATER);
    expect(screen.getByRole("tab", { name: "Журнал ИИ" })).toBeInTheDocument();
  });

  it("хранилище бросает при записи: журнал открывается, число не залипает", async () => {
    const user = userEvent.setup();
    localStorage.setItem(KEY, "2026-09-10T12:12:30+00:00");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("квота");
    });
    renderWithProviders(<FamiliesPage />, { initialRoute: "/families" });

    await user.click(await screen.findByRole("tab", { name: "Журнал ИИ 5" }, LATER));

    await waitFor(() => expect(document.querySelector("[data-journal-id]")).not.toBeNull(), LATER);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Журнал ИИ" })).toBeInTheDocument(), LATER);
  });
});

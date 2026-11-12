import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

import { JournalTab } from "./JournalTab";

/** Состояния ожидания «Журнала ИИ»: заглушки первой загрузки, полоса перечитывания, карточка по id. */

const LATER = { timeout: 8000 };

describe("JournalTab — первая загрузка и перечитывание", () => {
  it("до ответа — заглушки без текста «нет»; пустой ответ — «Записей с такими отборами нет» без заглушек", async () => {
    handlerState.journal = [];
    const hold = holdRequests("get", "/api/v1/semantic/journal");
    renderWithProviders(<JournalTab />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Записей с такими отборами нет")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Записей с такими отборами нет", {}, LATER)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("перечитывание: строки на месте, полоса видна, затем пропадает", async () => {
    const queryClient = createTestQueryClient();
    renderWithProviders(<JournalTab />, { queryClient });
    await waitFor(() => expect(document.querySelector("[data-journal-id]")).not.toBeNull(), LATER);
    expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument();
    const hold = holdRequests("get", "/api/v1/semantic/journal");

    void queryClient.invalidateQueries({ queryKey: ["semantic-journal"] });

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" }, LATER)).toBeInTheDocument();
    expect(document.querySelector("[data-journal-id]")).not.toBeNull();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });

  it("ошибка загрузки списка — подпись, а не пустая лента", async () => {
    server.use(http.get("/api/v1/semantic/journal", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<JournalTab />);

    expect(await screen.findByText("Не удалось получить журнал.", {}, LATER)).toBeInTheDocument();
    expect(screen.queryByText("Записей с такими отборами нет")).not.toBeInTheDocument();
  });

  it("запись из адреса открывается карточкой, даже когда её нет на странице списка", async () => {
    server.use(
      http.get("/api/v1/semantic/journal", () =>
        HttpResponse.json({ items: [], total: 0, counts: { split: 0, value_added: 0, composite: 0, equated: 0 } })
      )
    );
    renderWithProviders(<JournalTab />, { initialRoute: "/families?tab=journal&entry=106" });

    expect(
      await screen.findByRole("heading", { name: "«профлист» приравнено к «профнастил»" }, LATER)
    ).toBeInTheDocument();
  });

  it("записи из адреса нет — подпись, а не пустая карточка", async () => {
    renderWithProviders(<JournalTab />, { initialRoute: "/families?tab=journal&entry=99999" });

    expect(await screen.findByText("Записи больше нет: обновите экран.", {}, LATER)).toBeInTheDocument();
  });
});

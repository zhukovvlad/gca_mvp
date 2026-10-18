import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ContextsTab } from "@/pages/families/ContextsTab";
import { holdRequests } from "@/test/hold";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

/** Состояния ожидания списка контекстов: заглушки первой загрузки и полоса перечитывания. */

const FIRST_ROW = "Штукатурка стен цементно-песчаным раствором";

describe("ContextsTab — первая загрузка и перечитывание", () => {
  it("до ответа заглушки без текста «пусто»; пустой ответ — «Контекстов нет» без заглушек", async () => {
    server.use(
      http.get("/api/v1/semantic/contexts", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })
      )
    );
    const hold = holdRequests("get", "/api/v1/semantic/contexts");
    renderWithProviders(<ContextsTab />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Контекстов нет")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Контекстов нет")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("непустой ответ: строки вместо заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/contexts");
    renderWithProviders(<ContextsTab />);
    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();

    hold.release();
    expect(await screen.findByText(FIRST_ROW)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("перечитывание: строки на месте, полоса видна, затем пропадает", async () => {
    const queryClient = createTestQueryClient();
    renderWithProviders(<ContextsTab />, { queryClient });
    await screen.findByText(FIRST_ROW);
    expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument();
    const hold = holdRequests("get", "/api/v1/semantic/contexts");

    void queryClient.invalidateQueries({ queryKey: ["semantic-contexts"] });

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getByText(FIRST_ROW)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });
});

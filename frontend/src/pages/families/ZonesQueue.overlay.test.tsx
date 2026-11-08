import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import { ZonesQueue } from "./ZonesQueue";

/**
 * Серый слой со спиннером над старым списком на время решения (спека лестницы аналогов §2.20):
 * цифры очереди видны под слоем, кнопки решения погашены, слой закрывается вместе с перезапросом.
 */
describe("ZonesQueue: серый слой на решении", () => {
  it("пока PUT зоны идёт, над очередью слой «Сохраняем…», старые строки видны; после ответа слоя нет", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.put("/api/v1/semantic/section-chains/:id/zone", async () => {
        await hold;
        return new HttpResponse(null, { status: 204 });
      })
    );
    renderWithProviders(<ZonesQueue />);
    const rows = await screen.findAllByTestId("zone-row");
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();

    await userEvent.click(within(rows[0]).getByRole("button", { name: "Весь объект" }));

    const overlay = await screen.findByTestId("recalc-overlay");
    expect(overlay).toHaveTextContent(SAVING_LABEL);
    expect(screen.getAllByTestId("zone-row")).toHaveLength(rows.length);
    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), { timeout: 8000 });
  });

  it("слоя нет, пока решения нет", async () => {
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
  });
});

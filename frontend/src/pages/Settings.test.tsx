import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import SettingsPage from "./Settings";

describe("SettingsPage", () => {
  it("отправляет только изменённые поля", async () => {
    let putBody: unknown;
    server.use(
      http.put("*/api/settings", async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ message: "ok" });
      })
    );
    renderWithProviders(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Парсинг" }));
    const threshold = screen.getByRole("spinbutton");
    await userEvent.clear(threshold);
    await userEvent.type(threshold, "0.9");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(putBody).toEqual({ confidence_threshold: 0.9 }));
  });

  it("скрывает поле модели при can_edit_model=false", async () => {
    server.use(
      http.get("*/api/settings", () =>
        HttpResponse.json({
          provider: "gateway", can_edit_model: false, cost_available: false,
          api_key_set: true, model: "m", confidence_threshold: 0.7,
        })
      )
    );
    renderWithProviders(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Парсинг" }));
    expect(screen.queryByPlaceholderText(/anthropic/)).not.toBeInTheDocument();
  });
});

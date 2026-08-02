import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import ReferencePrices from "./ReferencePrices";

describe("ReferencePrices — unit selection", () => {
  it("auto-defaults unit from the material type and submits unit_id", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([{ id: 7, name: "Тестовый объект", contract_number: null, doc_count: 0 }]),
      ),
      http.get("/api/material-classes", () =>
        HttpResponse.json([{ id: 11, name: "В25", material_type: "concrete" }]),
      ),
      http.post("/api/reference-prices", async ({ request }) => {
        onCreate(await request.json());
        return HttpResponse.json({ id: 1 });
      }),
    );

    renderWithProviders(<ReferencePrices />);
    await user.click(screen.getByRole("button", { name: "Добавить эталон" }));
    const dialog = await screen.findByRole("dialog");
    // Dialog has three selects in render order: project, material_class, unit.
    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[0]);
    // base-ui Select renders options as role="option" in a listbox portal
    await user.click(await screen.findByRole("option", { name: "Тестовый объект" }));
    await user.click(combos[1]);
    await user.click(await screen.findByRole("option", { name: "В25" }));
    // default-by-type now set unit to М3 (concrete.default_unit.id === 3).

    await user.type(within(dialog).getByRole("spinbutton"), "8000");  // price (only number input)
    const dateInputs = dialog.querySelectorAll('input[type="date"]');
    fireEvent.change(dateInputs[0], { target: { value: "2026-01-01" } });
    fireEvent.change(dateInputs[1], { target: { value: "2026-12-31" } });

    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    expect(onCreate.mock.calls[0][0].unit_id).toBe(3);  // concrete → M3
  });

  it("manual unit pick after auto-default sends the MANUAL unit_id in POST body", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([{ id: 7, name: "Тестовый объект", contract_number: null, doc_count: 0 }]),
      ),
      http.get("/api/material-classes", () =>
        HttpResponse.json([{ id: 11, name: "В25", material_type: "concrete" }]),
      ),
      http.post("/api/reference-prices", async ({ request }) => {
        onCreate(await request.json());
        return HttpResponse.json({ id: 2 });
      }),
    );

    renderWithProviders(<ReferencePrices />);
    await user.click(screen.getByRole("button", { name: "Добавить эталон" }));
    const dialog = await screen.findByRole("dialog");
    const combos = within(dialog).getAllByRole("combobox");

    // Pick project
    await user.click(combos[0]);
    await user.click(await screen.findByRole("option", { name: "Тестовый объект" }));

    // Pick class — auto-defaults unit to М3 (id=3)
    await user.click(combos[1]);
    await user.click(await screen.findByRole("option", { name: "В25" }));

    // Manually override unit to Тонна (id=1)
    await user.click(combos[2]);
    await user.click(await screen.findByRole("option", { name: "Тонна (т)" }));

    await user.type(within(dialog).getByRole("spinbutton"), "500");
    const dateInputs = dialog.querySelectorAll('input[type="date"]');
    fireEvent.change(dateInputs[0], { target: { value: "2026-01-01" } });
    fireEvent.change(dateInputs[1], { target: { value: "2026-12-31" } });

    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    // Manual pick (id=1) must win over the auto-default (id=3)
    expect(onCreate.mock.calls[0][0].unit_id).toBe(1);
  });

  it("switching to a class with no default unit (Прочее) clears the unit select", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/projects", () =>
        HttpResponse.json([{ id: 7, name: "Тестовый объект", contract_number: null, doc_count: 0 }]),
      ),
      http.get("/api/material-classes", () =>
        HttpResponse.json([
          { id: 11, name: "В25", material_type: "concrete" },
          { id: 12, name: "Прочий материал", material_type: "other" },
        ]),
      ),
    );

    renderWithProviders(<ReferencePrices />);
    await user.click(screen.getByRole("button", { name: "Добавить эталон" }));
    const dialog = await screen.findByRole("dialog");
    const combos = within(dialog).getAllByRole("combobox");

    // Pick project
    await user.click(combos[0]);
    await user.click(await screen.findByRole("option", { name: "Тестовый объект" }));

    // Pick concrete class — unit defaults to М3
    await user.click(combos[1]);
    await user.click(await screen.findByRole("option", { name: "В25" }));

    // Switch to «Прочий материал» (type=other, default_unit=null) — unit must clear
    await user.click(combos[1]);
    await user.click(await screen.findByRole("option", { name: "Прочий материал" }));

    // Unit combobox must show its placeholder (not a selected value)
    expect(combos[2]).toHaveTextContent("Выберите единицу");

    // Submit button must be disabled since unit is empty
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("shows the unit symbol in the price list", async () => {
    server.use(
      http.get("/api/reference-prices", () =>
        HttpResponse.json([
          {
            id: 1, project_id: 7, material_class_id: 11, material_class_name: "В25",
            unit_id: 3, unit_symbol: "м³", price: 8000,
            period_start: "2026-01-01", period_end: "2026-12-31", source: null,
          },
        ]),
      ),
    );
    renderWithProviders(<ReferencePrices />);
    expect(await screen.findByText("м³")).toBeInTheDocument();
  });
});

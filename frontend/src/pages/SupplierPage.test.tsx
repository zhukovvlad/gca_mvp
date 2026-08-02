import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import SupplierPage from "./SupplierPage";

/** Render SupplierPage inside a Route so useParams() extracts the id. */
function renderSupplier(id = "1") {
  return renderWithProviders(
    <Routes>
      <Route path="/suppliers/:id" element={<SupplierPage />} />
    </Routes>,
    { initialRoute: `/suppliers/${id}` },
  );
}

describe("SupplierPage", () => {
  it("renders supplier name in header", async () => {
    renderSupplier();
    await waitFor(() => {
      expect(screen.getAllByText("ООО «ЭРКОН»").length).toBeGreaterThan(0);
    });
  });

  it("renders 3 KPI cards", async () => {
    renderSupplier();
    await waitFor(() => {
      // KPI labels appear in KPI cards; "Оборот" and "Счетов" also appear as table column headers
      expect(screen.getAllByText("Оборот").length).toBeGreaterThan(0);
      expect(screen.getByText("Объектов")).toBeInTheDocument();
      expect(screen.getAllByText("Счетов").length).toBeGreaterThan(0);
    });
  });

  it("renders all three tabs", async () => {
    renderSupplier();
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Обзор" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /По объектам/ })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: /Счета/ })).toBeInTheDocument();
    });
  });

  it("shows project rows in 'По объектам' tab by default", async () => {
    renderSupplier();
    await waitFor(() => {
      expect(screen.getByText("ЖК Радуга")).toBeInTheDocument();
      expect(screen.getByText("Бизнес-центр «Меридиан»")).toBeInTheDocument();
    });
  });

  it("shows 'не суммируем' in totals row", async () => {
    renderSupplier();
    await waitFor(() => {
      expect(screen.getByText("не суммируем")).toBeInTheDocument();
    });
  });

  it("shows Итого row in projects tab", async () => {
    renderSupplier();
    await waitFor(() => {
      expect(screen.getByText("Итого")).toBeInTheDocument();
    });
  });

  it("switches to Обзор tab and shows INN", async () => {
    const user = userEvent.setup();
    renderSupplier();
    const tab = await screen.findByRole("tab", { name: "Обзор" });
    await user.click(tab);
    await waitFor(() => {
      // INN shown as plain number in the details block
      expect(screen.getByText("7723746396")).toBeInTheDocument();
    });
  });

  it("switches to Обзор tab and shows Реквизиты heading", async () => {
    const user = userEvent.setup();
    renderSupplier();
    const tab = await screen.findByRole("tab", { name: "Обзор" });
    await user.click(tab);
    await waitFor(() => {
      expect(screen.getByText("Реквизиты")).toBeInTheDocument();
    });
  });

  it("switches to Счета tab and shows invoice number", async () => {
    const user = userEvent.setup();
    renderSupplier();
    const tab = await screen.findByRole("tab", { name: /Счета/ });
    await user.click(tab);
    await waitFor(() => {
      expect(screen.getByText("А-001")).toBeInTheDocument();
    });
  });

  it("opens edit dialog on 'Редактировать' click", async () => {
    const user = userEvent.setup();
    renderSupplier();
    const editBtn = await screen.findByRole("button", { name: /Редактировать/ });
    await user.click(editBtn);
    await waitFor(() => {
      expect(screen.getByText("Редактировать поставщика")).toBeInTheDocument();
    });
  });

  it("shows field error when saving with empty name", async () => {
    const user = userEvent.setup();
    renderSupplier();
    const editBtn = await screen.findByRole("button", { name: /Редактировать/ });
    await user.click(editBtn);

    const nameInput = await screen.findByDisplayValue("ООО «ЭРКОН»");
    await user.clear(nameInput);

    const saveBtn = screen.getByRole("button", { name: /Сохранить/ });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText("Название не может быть пустым")).toBeInTheDocument();
    });
  });

  it("shows merge confirmation screen on INN conflict (409)", async () => {
    server.use(
      http.put("/api/suppliers/:id", () =>
        HttpResponse.json(
          {
            detail: {
              code: "inn_conflict",
              message: "INN belongs to another supplier",
              existing: { id: 99, name: "ООО Конфликт" },
            },
          },
          { status: 409 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderSupplier();
    const editBtn = await screen.findByRole("button", { name: /Редактировать/ });
    await user.click(editBtn);

    const saveBtn = await screen.findByRole("button", { name: /Сохранить/ });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText("Совместить поставщиков?")).toBeInTheDocument();
      // name appears in two paragraphs of the dialog
      expect(screen.getAllByText(/ООО Конфликт/).length).toBeGreaterThan(0);
    });
  });

  it("shows 'Назад' button in merge confirmation screen", async () => {
    server.use(
      http.put("/api/suppliers/:id", () =>
        HttpResponse.json(
          {
            detail: {
              code: "inn_conflict",
              message: "INN belongs to another supplier",
              existing: { id: 99, name: "ООО Конфликт" },
            },
          },
          { status: 409 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderSupplier();
    const editBtn = await screen.findByRole("button", { name: /Редактировать/ });
    await user.click(editBtn);

    await screen.findByRole("button", { name: /Сохранить/ });
    await user.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Назад/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Совместить/ })).toBeInTheDocument();
    });
  });
});

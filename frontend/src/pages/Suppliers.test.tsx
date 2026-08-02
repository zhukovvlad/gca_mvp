import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "@/test/utils";
import Suppliers from "./Suppliers";

describe("Suppliers", () => {
  it("renders page header", async () => {
    renderWithProviders(<Suppliers />);
    await waitFor(() => {
      expect(screen.getByText("Поставщики")).toBeInTheDocument();
    });
  });

  it("renders supplier name in table", async () => {
    renderWithProviders(<Suppliers />);
    await waitFor(() => {
      expect(screen.getByText("ООО «ЭРКОН»")).toBeInTheDocument();
    });
  });

  it("renders INN in secondary row", async () => {
    renderWithProviders(<Suppliers />);
    await waitFor(() => {
      expect(screen.getByText(/ИНН 7723746396/)).toBeInTheDocument();
    });
  });

  it("renders categories in secondary row", async () => {
    renderWithProviders(<Suppliers />);
    await waitFor(() => {
      // В25 is in categories for sampleSupplier
      expect(screen.getByText(/В25/)).toBeInTheDocument();
    });
  });

  it("renders invoice count and project count columns", async () => {
    renderWithProviders(<Suppliers />);
    await waitFor(() => {
      expect(screen.getByText("Оборот")).toBeInTheDocument();
      expect(screen.getByText("Объектов")).toBeInTheDocument();
      expect(screen.getByText("Счетов")).toBeInTheDocument();
    });
  });

  it("filters out supplier when search has no match", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Suppliers />);
    // wait for supplier to appear first
    await screen.findByText("ООО «ЭРКОН»");

    const input = screen.getByPlaceholderText("Поиск по названию или ИНН");
    await user.type(input, "НесуществующееНазвание");

    await waitFor(() => {
      expect(screen.queryByText("ООО «ЭРКОН»")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("filter by INN keeps matching supplier", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Suppliers />);
    await screen.findByText("ООО «ЭРКОН»");

    const input = screen.getByPlaceholderText("Поиск по названию или ИНН");
    await user.type(input, "7723746396");

    await waitFor(() => {
      expect(screen.getByText("ООО «ЭРКОН»")).toBeInTheDocument();
    });
  });

  it("navigates to supplier card on row click", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/suppliers" element={<Suppliers />} />
        <Route path="/suppliers/:id" element={<div data-testid="supplier-card" />} />
      </Routes>,
      { initialRoute: "/suppliers" },
    );
    const row = await screen.findByText("ООО «ЭРКОН»");
    await user.click(row);
    await waitFor(() => {
      expect(screen.getByTestId("supplier-card")).toBeInTheDocument();
    });
  });
});

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TendersPage from "./TendersPage";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Тендеры» (спека §2.13)", () => {
  it("показывает номер, предмет, объект и счётчики раундов/участников", async () => {
    renderWithProviders(<TendersPage />);

    expect(await screen.findByText("Т-2026-001")).toBeInTheDocument();
    expect(screen.getByText("Генподряд на строительство")).toBeInTheDocument();
    expect(screen.getByText("ЖК Северный")).toBeInTheDocument();

    const row = screen.getByText("Т-2026-001").closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement).getAllByRole("cell");
    // Номер · предмет · объект · класс · раундов · участников (sampleTenders[0]).
    expect(cells.at(-2)).toHaveTextContent("2");
    expect(cells.at(-1)).toHaveTextContent("2");
  });

  it("admin видит кнопку заведения тендера", async () => {
    renderWithProviders(<TendersPage />);
    expect(await screen.findByRole("button", { name: /Новый тендер/ })).toBeInTheDocument();
  });

  it("member кнопки заведения не видит (§6.2)", async () => {
    renderWithProviders(<TendersPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("Т-2026-001");

    expect(screen.queryByRole("button", { name: /Новый тендер/ })).not.toBeInTheDocument();
  });
});

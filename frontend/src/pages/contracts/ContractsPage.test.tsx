import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import ContractsPage from "./ContractsPage";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Договоры» (§7.1)", () => {
  it("показывает договоры с объектом, подрядчиком и классом", async () => {
    renderWithProviders(<ContractsPage />);

    expect(await screen.findByText("ГП-2026-001")).toBeInTheDocument();
    expect(screen.getByText("ЖК Северный")).toBeInTheDocument();
    expect(screen.getByText("ООО СтройПодряд")).toBeInTheDocument();
    expect(screen.getAllByText("Жилые дома").length).toBeGreaterThan(0);
  });

  it("сумму показывает строкой без потери разрядов", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    // 1234567890.12 через double уехало бы в последнем разряде; проверяем именно
    // отформатированную строку (§3, деньги строками).
    expect(screen.getByText(/1\s234\s567\s890,12/)).toBeInTheDocument();
  });

  it("пустая сумма — прочерк, а не ноль", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-002");

    const row = screen.getByText("ГП-2026-002").closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("—");
  });

  it("ищет договор по названию объекта", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.type(screen.getByLabelText("Поиск договоров"), "Южный");

    // Сначала ждём исчезновения непопадающего договора: только это и означает,
    // что отфильтрованный ответ доехал. Обратный порядок ничего не доказывал бы —
    // ГП-2026-002 есть и в неотфильтрованном списке.
    await waitFor(() => {
      expect(screen.queryByText("ГП-2026-001")).not.toBeInTheDocument();
    });
    // findByText, а не getByText: в момент проверки выше список мог быть пуст.
    expect(await screen.findByText("ГП-2026-002")).toBeInTheDocument();
  });

  it("admin видит кнопку заведения договора", async () => {
    renderWithProviders(<ContractsPage />);
    expect(await screen.findByRole("button", { name: /Новый договор/ })).toBeInTheDocument();
  });

  it("member кнопки заведения не видит (§6.2)", async () => {
    renderWithProviders(<ContractsPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("ГП-2026-001");

    expect(screen.queryByRole("button", { name: /Новый договор/ })).not.toBeInTheDocument();
    // И объяснение пустого состояния для него другое — но список не пуст, так что
    // проверяем именно отсутствие действия, а не текст.
  });
});

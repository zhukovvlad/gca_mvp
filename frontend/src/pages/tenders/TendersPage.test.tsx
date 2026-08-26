import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import TendersPage from "./TendersPage";
import { sampleTenders } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Тендеры» (спека §2.13)", () => {
  it("показывает номер, предмет, объект и РАЗЛИЧАЮЩИЕСЯ счётчики раундов/участников по своим колонкам", async () => {
    // Фикстура `sampleTenders[0]` несёт rounds_count === participants_count
    // (2 и 2) — с ней утверждение «обе последние ячейки содержат "2"» прошло
    // бы и при перепутанных местами колонках. Здесь числа разные (3 и 5), и
    // каждое проверяется в СВОЁМ столбце по заголовку, а не по позиции с
    // конца — так тест ловит и перестановку колонок, и подмену значения.
    server.use(
      http.get("/api/v1/tenders", () =>
        HttpResponse.json({
          items: [{ ...sampleTenders[0], rounds_count: 3, participants_count: 5 }],
          total: 1,
          page: 1,
          page_size: 20,
        })
      )
    );
    renderWithProviders(<TendersPage />);

    expect(await screen.findByText("Т-2026-001")).toBeInTheDocument();
    expect(screen.getByText("Генподряд на строительство")).toBeInTheDocument();
    expect(screen.getByText("ЖК Северный")).toBeInTheDocument();

    const table = screen.getByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    const roundsColIdx = headers.findIndex((h) => h === "Раундов");
    const participantsColIdx = headers.findIndex((h) => h === "Участников");
    expect(roundsColIdx).toBeGreaterThan(-1);
    expect(participantsColIdx).toBeGreaterThan(-1);

    const row = screen.getByText("Т-2026-001").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[roundsColIdx]).toHaveTextContent("3");
    expect(cells[participantsColIdx]).toHaveTextContent("5");
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

  /**
   * Finding ревью задачи 12: раньше тест проверял только присутствие кнопки,
   * а не то, что она реально открывает форму — `TenderFormDialog` целиком
   * оставался без интерактивного покрытия на уровне страницы (глубокое
   * поведение диалога — в `TenderFormDialog.test.tsx`, по образцу
   * `ContractFormDialog.test.tsx`).
   */
  it("клик по «Новый тендер» открывает форму создания", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TendersPage />);
    await user.click(await screen.findByRole("button", { name: /Новый тендер/ }));

    expect(await screen.findByRole("heading", { name: "Новый тендер" })).toBeInTheDocument();
  });
});

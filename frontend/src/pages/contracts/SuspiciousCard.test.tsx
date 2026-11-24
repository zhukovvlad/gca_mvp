import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import ContractCardPage from "./ContractCardPage";

/** Карточка договора 100 (`sampleContractCard`): счётчик «Подозрительные строки» только у admin и только при n > 0. */
function renderCard(role: "admin" | "member") {
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:contractId" element={<ContractCardPage />} />
    </Routes>,
    {
      initialRoute: "/contracts/100",
      initialUser: { id: role === "admin" ? 1 : 2, email: `${role}@example.com`, role },
    }
  );
}

function counts(byContract: Record<string, number>) {
  server.use(
    http.get("/api/v1/suspicious-lines/counts", () =>
      HttpResponse.json({ total: Object.values(byContract).reduce((a, b) => a + b, 0), by_contract: byContract })
    )
  );
}

describe("счётчик подозрительных строк на карточке договора", () => {
  it("admin: «Подозрительные строки: n →» ведёт на вкладку с фильтром по договору", async () => {
    counts({ "100": 3, "200": 5 });
    renderCard("admin");

    const link = await screen.findByRole("link", { name: "Подозрительные строки: 3 →" });
    expect(link).toHaveAttribute("href", "/contracts?tab=suspicious&contract=100");
  });

  it("admin: у договора с нулём (его нет в by_contract) ссылки нет", async () => {
    counts({ "200": 5 });
    renderCard("admin");

    await screen.findByText("Иванов И.И.");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("suspicious-link")).not.toBeInTheDocument();
  });

  it("member: ссылки нет и счётчики не запрашиваются", async () => {
    const asked: string[] = [];
    server.use(
      http.get("/api/v1/suspicious-lines/counts", ({ request }) => {
        asked.push(request.url);
        return HttpResponse.json({ total: 3, by_contract: { "100": 3 } });
      })
    );
    renderCard("member");

    await screen.findByText("Иванов И.И.");
    await waitFor(() => expect(screen.queryByTestId("suspicious-link")).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(asked).toEqual([]);
  });
});

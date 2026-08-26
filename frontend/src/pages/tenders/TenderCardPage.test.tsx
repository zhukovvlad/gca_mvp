import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import TenderCardPage from "./TenderCardPage";
import { handlerState } from "@/test/handlers";
import { sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Карточка тендера (спека §2.13, §2.14, задача 12): решётка участник×раунд —
 * ПРЯМОУГОЛЬНИК (у каждого участника есть ячейка в каждом раунде, включая тот,
 * где он впервые появился позже первого этапа), `?round=` выбирает раунд,
 * `BaselineStatus` даёт одно из четырёх состояний §2.14, действия правки и
 * удаления — только `admin`.
 */
function renderCard(options?: Parameters<typeof renderWithProviders>[1] & { initialRoute?: string }) {
  const { initialRoute = "/tenders/300", ...rest } = options ?? {};
  return renderWithProviders(
    <Routes>
      <Route path="/tenders/:tenderId" element={<TenderCardPage />} />
    </Routes>,
    { initialRoute, ...rest }
  );
}

describe("Карточка тендера (§2.13, §2.14)", () => {
  it("решётка 2×2: «—» у (этап 1, Бета), «нет сметы» у (этап 2, Бета)", async () => {
    renderCard();

    expect(await screen.findByText("ООО Альфа")).toBeInTheDocument();
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaCells = within(betaRow).getAllByRole("cell");
    // Участник · этап 1 (3001) · этап 2 (3002) · [удаление]
    expect(betaCells[1]).toHaveTextContent("—");
    expect(betaCells[2]).toHaveTextContent("нет сметы");
  });

  it("без ?round выбран последний по stage_no этап", async () => {
    renderCard();
    // Заголовок панели этапа — `<h2>`, отдельно от кнопки выбора раунда в
    // решётке (у неё та же подпись «Этап N»): проверяем именно панель.
    expect(await screen.findByRole("heading", { name: /Этап 2/, level: 2 })).toBeInTheDocument();
  });

  it("?round=3001 выбирает этап 1 и показывает «Расчётная стоимость загружена» при tenderRoundState=loaded", async () => {
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    expect(await screen.findByRole("heading", { name: /Этап 1/, level: 2 })).toBeInTheDocument();
    expect(screen.getByText(/Расчётная стоимость загружена/)).toBeInTheDocument();
  });

  it('tenderRoundState="changed" → «состав изменён после импорта»', async () => {
    handlerState.tenderRoundState = "changed";
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    expect(await screen.findByText(/Состав раунда изменён после импорта/)).toBeInTheDocument();
  });

  it("member не видит кнопок правки и удаления", async () => {
    renderCard({ initialUser: { id: 2, email: "member@example.com", role: "member" } });
    await screen.findByText("ООО Альфа");

    expect(screen.queryByRole("button", { name: /Правка/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Новый этап/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить этап/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить участника/ })).not.toBeInTheDocument();
  });

  /**
   * Прямоугольник решётки — требование, а не следствие удачных фикстур.
   * Здесь ячейка (этап 1, Бета) убрана из ответа сервера ЦЕЛИКОМ (не просто
   * `offer_id: null` в существующей записи, как в стандартной фикстуре), и
   * грид обязан показать «—», а не молча пропустить клетку — иначе строка
   * Беты имела бы на одну ячейку меньше строки Альфы.
   */
  it("решётка остаётся прямоугольником, даже если сервер не прислал ячейку вовсе", async () => {
    server.use(
      http.get("/api/v1/tenders/:id", () =>
        HttpResponse.json({
          ...sampleTenderCard,
          cells: sampleTenderCard.cells.filter((c) => !(c.round_id === 3001 && c.package_id === 502)),
        })
      )
    );
    renderCard();

    await screen.findByText("ООО Альфа");
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaCells = within(betaRow).getAllByRole("cell");
    expect(betaCells[1]).toHaveTextContent("—");
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

/**
 * Интерактивное покрытие четырёх admin-диалогов карточки (finding ревью
 * задачи 12): раньше тесты проверяли только ОТСУТСТВИЕ кнопок у `member`, ни
 * разу не открывая их как `admin` и не проверяя эффект. По образцу
 * `ContractCardPage.test.tsx` ("admin удаляет договор с карточки и уходит со
 * страницы") — тот же приём, на который уже ссылается комментарий
 * `TenderDeleteDialog` в самом компоненте.
 */
describe("Admin-диалоги карточки тендера — открытие и эффект", () => {
  it('"Новый этап" предлагает СЛЕДУЮЩИЙ по порядку номер и шлёт именно его', async () => {
    // sampleTenderCard несёт ДВА раунда (stage_no 1 и 2) — предложенный номер
    // обязан быть 3. Off-by-one (например nextStageNo = rounds.length без +1,
    // или взятый из последнего round.stage_no без +1) дал бы 2 — тест ловит
    // это и по подписи в диалоге, и по фактически отправленному телу.
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post("/api/v1/tenders/:id/rounds", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(sampleTenderCard, { status: 201 });
      })
    );
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: "Новый этап" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Этап 3");

    await user.type(within(dialog).getByLabelText("Название этапа"), "Третий этап");
    await user.click(within(dialog).getByRole("button", { name: "Создать этап" }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body!.stage_no).toBe(3);
    expect(body!.label).toBe("Третий этап");
  });

  it('"Удалить этап" шлёт id ИМЕННО выбранного раунда (?round=3001), а не первого/последнего', async () => {
    let deletedTenderId: string | undefined;
    let deletedRoundId: string | undefined;
    server.use(
      http.delete("/api/v1/tenders/:id/rounds/:rid", ({ params }) => {
        deletedTenderId = String(params.id);
        deletedRoundId = String(params.rid);
        return new HttpResponse(null, { status: 204 });
      })
    );
    const user = userEvent.setup();
    // Этап 1 (id 3001) выбран явно через `?round=` — если бы кнопка панели
    // слала id какого-то другого раунда (первого в массиве, последнего,
    // «текущего по умолчанию»), этот тест поймал бы несовпадение, а тест по
    // умолчанию (последний раунд) — нет.
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    await screen.findByRole("heading", { name: /Этап 1/, level: 2 });

    await user.click(screen.getByRole("button", { name: "Удалить этап" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Удалить этап 1?");
    await user.click(within(dialog).getByRole("button", { name: "Удалить этап" }));

    await waitFor(() => expect(deletedRoundId).toBe("3001"));
    expect(deletedTenderId).toBe("300");
  });

  it('"Удалить" тендер: гейт по ТОЧНОМУ номеру (обе стороны) и правильный id уходит в запрос', async () => {
    let deletedId: string | undefined;
    server.use(
      http.delete("/api/v1/tenders/:id", ({ params }) => {
        deletedId = String(params.id);
        return new HttpResponse(null, { status: 204 });
      })
    );
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: "Удалить" }));
    const dialog = await screen.findByRole("alertdialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "Удалить тендер" });
    expect(confirmBtn).toBeDisabled();

    const input = within(dialog).getByLabelText(/Введите номер тендера/);
    // Неверная строка НЕ разблокирует кнопку — иначе опечатка удалила бы не
    // тот тендер.
    await user.type(input, "Т-2026-999");
    expect(confirmBtn).toBeDisabled();

    // Верная строка — разблокирует.
    await user.clear(input);
    await user.type(input, sampleTenderCard.tender_number);
    await waitFor(() => expect(confirmBtn).toBeEnabled());

    await user.click(confirmBtn);

    await waitFor(() => expect(deletedId).toBe(String(sampleTenderCard.id)));
    // Успешное удаление уводит на `/tenders` — в тесте зарегистрирован только
    // маршрут карточки (см. `renderCard`), поэтому вся разметка карточки
    // пропадает; тот же приём, что в `ContractCardPage.test.tsx`.
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: sampleTenderCard.tender_number })).not.toBeInTheDocument();
    });
  });
});

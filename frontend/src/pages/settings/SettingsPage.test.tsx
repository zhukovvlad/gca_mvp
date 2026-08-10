import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import SettingsPage from "./SettingsPage";
import { sampleAppSettings } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Экран настроек (§7.4: «N хранится в БД … экран Settings»).
 *
 * Главное, что здесь проверяется, — что границы диапазона берутся **с сервера**, а
 * не зашиты в экран: они выражают `CHECK` в БД (миграция 0004), и вторая их копия
 * разъехалась бы с первой.
 */

function renderSettings() {
  return renderWithProviders(<SettingsPage />, { initialRoute: "/settings" });
}

describe("Настройки", () => {
  it("поле заполнено текущим значением из базы", async () => {
    renderSettings();
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");
    expect(input).toHaveValue(sampleAppSettings.passport_top_n);
  });

  it("границы диапазона приходят с сервера, а не зашиты в экран", async () => {
    renderSettings();
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");

    expect(input).toHaveAttribute("min", String(sampleAppSettings.passport_top_n_min));
    expect(input).toHaveAttribute("max", String(sampleAppSettings.passport_top_n_max));
    // Причина верхней границы подписана: без неё ограничение читается как произвол.
    expect(screen.getByText(/раскладку экрана паспорта фазы 6/)).toBeInTheDocument();
  });

  it("сохраняет новое значение", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");

    await user.clear(input);
    await user.type(input, "10");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText(/Ключевых расценок в паспорте: 10/)).toBeInTheDocument();
    expect(handlerState.passportTopN).toBe(10);
  });

  it("кнопка неактивна, пока значение не изменилось", async () => {
    renderSettings();
    await screen.findByLabelText("Ключевых расценок в паспорте");
    // Иначе «Сохранить» на неизменной форме шлёт запрос и рисует успех, которого
    // не произошло.
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("значение вне диапазона не отправляется и объясняется на месте", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");

    await user.clear(input);
    await user.type(input, "99");

    expect(screen.getByRole("alert")).toHaveTextContent(
      `Введите целое число от ${sampleAppSettings.passport_top_n_min} до ${sampleAppSettings.passport_top_n_max}`
    );
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    // Значение на сервере не тронуто: форма не отправляла заведомо отвергаемое.
    expect(handlerState.passportTopN).toBe(sampleAppSettings.passport_top_n);
  });

  it("отказ сервера доезжает до человека тостом, а не теряется", async () => {
    /*
      Сервер — последний авторитет по диапазону, и его текст объясняет ПРИЧИНУ
      границы. Проверяется именно этот путь: значение, которое форма считает
      допустимым, а сервер отвергает. Так бывает при расхождении границ —
      например, если БД мигрировали, а страница открыта давно. Молчание здесь
      означало бы, что человек не понял, сохранилось ли что-нибудь.
    */
    server.use(
      http.patch("/api/v1/settings", () =>
        HttpResponse.json(
          {
            detail:
              "Верхняя граница — не прихоть: она подобрана под раскладку экрана паспорта фазы 6.",
          },
          { status: 422 }
        )
      )
    );

    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");

    await user.clear(input);
    await user.type(input, "20");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    /*
      Ищем по фразе, которой НЕТ в подсказке формы: подсказка не начинается со
      слов «Верхняя граница — не прихоть», а лишь называет причину той же
      границы своими словами. Совпадающий по смыслу, но не дословно текст в
      обоих местах — намеренно: иначе поиск по общей фразе был бы неоднозначным
      и падал не потому, что тоста нет. Тост — единственный канал сообщений об
      отказах (§7 брифинга).
    */
    expect(await screen.findByText(/Верхняя граница — не прихоть/)).toBeInTheDocument();
    // Значение на сервере не изменилось.
    expect(handlerState.passportTopN).toBe(sampleAppSettings.passport_top_n);
  });
});

import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import SettingsPage from "./SettingsPage";
import PassportPage from "@/pages/passport/PassportPage";
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
    expect(screen.getByText(/на одну страницу А4/)).toBeInTheDocument();
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
      границы (одна страница А4). Проверяется именно этот путь: значение, которое
      форма считает допустимым, а сервер отвергает. Так бывает при расхождении
      границ — например, если БД мигрировали, а страница открыта давно. Молчание
      здесь означало бы, что человек не понял, сохранилось ли что-нибудь.
    */
    server.use(
      http.patch("/api/v1/settings", () =>
        HttpResponse.json(
          { detail: "Верхняя граница — не прихоть: паспорт обязан печататься на одну страницу А4." },
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
      Ищем по фразе, которой НЕТ в подсказке формы. Первая редакция искала
      «паспорт обязан печататься на одну страницу А4» — эта фраза есть и в
      подсказке под полем, поэтому запрос был неоднозначным и падал не потому, что
      тоста нет. Тост — единственный канал сообщений об отказах (§7 брифинга).
    */
    expect(await screen.findByText(/Верхняя граница — не прихоть/)).toBeInTheDocument();
    // Значение на сервере не изменилось.
    expect(handlerState.passportTopN).toBe(sampleAppSettings.passport_top_n);
  });
});

/**
 * Смена N перерисовывает уже открытый паспорт.
 *
 * Отдельный блок, потому что нужен **свой** QueryClient — с `staleTime`, как в
 * приложении (`App.tsx`: 60 с). Тестовый клиент по умолчанию держит `staleTime: 0`,
 * то есть перезапрашивает при каждом монтировании, и на нём эта проверка ничего не
 * значила бы: паспорт обновился бы и без инвалидации. Замером подтверждено —
 * снятие инвалидации при `staleTime: 0` тест не валит, при 60 с валит.
 */
describe("Смена топ-N и открытый паспорт", () => {
  function renderBothScreens() {
    const queryClient = new QueryClient({
      defaultOptions: {
        // Тот же staleTime, что в приложении: иначе проверяется не то поведение.
        queries: { retry: false, gcTime: Infinity, staleTime: 60_000 },
        mutations: { retry: false },
      },
    });
    return renderWithProviders(
      <Routes>
        <Route
          path="/contracts/:contractId/passport"
          element={
            <>
              <Link to="/settings">К настройкам</Link>
              <PassportPage />
            </>
          }
        />
        <Route
          path="/settings"
          element={
            <>
              <Link to="/contracts/10/passport">К паспорту</Link>
              <SettingsPage />
            </>
          }
        />
      </Routes>,
      { initialRoute: "/contracts/10/passport", queryClient }
    );
  }

  it("паспорт показывает новое N после сохранения настройки", async () => {
    const user = userEvent.setup();
    renderBothScreens();

    // Паспорт открыт и закэширован: 3 расценки.
    expect(await screen.findByText(/Показаны 3 из 1100/)).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "К настройкам" }));
    const input = await screen.findByLabelText("Ключевых расценок в паспорте");
    await user.clear(input);
    await user.type(input, "1");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText(/Ключевых расценок в паспорте: 1/);

    await user.click(screen.getByRole("link", { name: "К паспорту" }));

    // Без инвалидации здесь остался бы кэш с тремя расценками, и настройка
    // выглядела бы неработающей.
    expect(await screen.findByText(/Показаны 1 из 1100/)).toBeInTheDocument();
    expect(screen.queryByText("Стяжка пола цементная")).not.toBeInTheDocument();
  });
});

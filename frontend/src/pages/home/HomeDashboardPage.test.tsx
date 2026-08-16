import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import HomeDashboardPage from "./HomeDashboardPage";
import { renderWithProviders } from "@/test/utils";
import { handlerState } from "@/test/handlers";
import type { User } from "@/types/auth";

const MEMBER: User = { id: 2, email: "reader@example.com", role: "member" };

/** Шапка отрисована — точка входа всех утверждений о её содержимом. */
async function renderDashboard(user?: User) {
  renderWithProviders(<HomeDashboardPage />, user ? { initialUser: user } : {});
  await screen.findByText("Итого по базе");
}

describe("HomeDashboardPage — права и вкладки", () => {
  it("admin видит обе вкладки", async () => {
    await renderDashboard();

    expect(screen.getByRole("tab", { name: /Основной дашборд/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /На что обратить внимание/ })).toBeInTheDocument();
  });

  it("у member полосы вкладок НЕТ ВОВСЕ", async () => {
    await renderDashboard(MEMBER);

    // Не «есть, но заблокирована»: у читателя таб один, и полоса из одной
    // вкладки была бы обещанием второй (решение 1 макета).
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("member не отправляет НИ ОДНОГО запроса к диагностикам", async () => {
    // Не про стоимость расчёта: `require_admin` отклоняет запрос ДО обработчика,
    // и `member` получил бы 403, ничего не посчитав. Проверка о другом —
    // безусловный хук штатно генерирует запрещённые запросы и засоряет журнал
    // сервера отказами, тогда как §2.8 разводит эндпоинты, чтобы клиент И НЕ
    // ПЫТАЛСЯ. Отсутствие требует сигнала, общего для обеих ветвей: у member
    // данных на экране нет в обоих случаях, поэтому сигнал — счётчик вызовов.
    await renderDashboard(MEMBER);
    await waitFor(() => expect(screen.getByText(/учтено/)).toBeInTheDocument());

    expect(handlerState.attentionRequests).toBe(0);
  });

  it("admin запрос к диагностикам отправляет", async () => {
    // Обратная половина: без неё «ровно 0» прошло бы и на хуке, сломанном
    // насовсем, — то есть доказывало бы не условность, а неработоспособность.
    await renderDashboard();

    await waitFor(() => expect(handlerState.attentionRequests).toBeGreaterThan(0));
  });
});

describe("HomeDashboardPage — подпись величины", () => {
  it("подпись видна НА СТРАНИЦЕ, а не только в тултипе", async () => {
    // Требование DoD спеки §2.2 и решения 3 макета: читатель обязан видеть, в
    // чём измерены числа, которые он складывает. Тултип у плитки ИТОГО
    // объясняет РАСЧЁТ и подписи не заменяет.
    await renderDashboard();

    expect(
      screen.getByText(
        "Все суммы и удельные показатели — с НДС, в действующей ставке договора"
      )
    ).toBeInTheDocument();
  });
});

describe("HomeDashboardPage — охваты шапки", () => {
  it("охват ИТОГО назван числом и разбивкой причин", async () => {
    await renderDashboard();

    expect(
      screen.getByText("учтено 3 договора из 6: 2 без сметы, 1 без ставки")
    ).toBeInTheDocument();
  });

  it("у трёх слагаемых площади ТРИ раздельных охвата", async () => {
    await renderDashboard();

    const aboveground = screen.getByTestId("area-aboveground");
    const underground = screen.getByTestId("area-underground");
    const useful = screen.getByTestId("area-useful");

    expect(aboveground).toHaveTextContent("2/4");
    expect(underground).toHaveTextContent("2/4");
    // Полезная заводится независимо от пары — её охват ДРУГОЙ, и общий
    // счётчик «не заведена у N» это различие скрывал бы (решение 4 макета).
    expect(useful).toHaveTextContent("1/4");
  });

  it("общая площадь подписана своим охватом", async () => {
    await renderDashboard();

    expect(screen.getByText("по 2 объектам из 4")).toBeInTheDocument();
  });

  it("наибольший и наименьший объект названы", async () => {
    await renderDashboard();

    const extremes = screen.getByTestId("area-extremes");
    expect(extremes).toHaveTextContent("ЖК «Северная гряда», корп. 2");
    expect(extremes).toHaveTextContent("Детский сад на 240 мест");
  });

  it("максимум и минимум ₽/м² подписаны СВОИМ охватом", async () => {
    await renderDashboard();

    expect(screen.getByTestId("per-sqm-max")).toHaveTextContent("из 2");
    expect(screen.getByTestId("per-sqm-min")).toHaveTextContent("из 2");
  });

  it("счётчики договоров и объектов НЕ сложены в одно число", async () => {
    // Решение 9 макета: «5 договоров» и «1 объект» — разные сущности. Число 10
    // (4 объекта + 6 договоров) не должно появиться нигде в шапке.
    await renderDashboard();

    const counters = screen.getByTestId("base-counters");
    expect(counters).toHaveTextContent("4");
    expect(counters).toHaveTextContent("6");
    expect(counters).not.toHaveTextContent("10");
    expect(screen.getByTestId("counter-objects")).toHaveTextContent("в 2 классах");
    expect(screen.getByTestId("counter-contracts")).toHaveTextContent("со сметой — 4");
  });
});

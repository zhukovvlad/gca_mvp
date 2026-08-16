import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import HomeDashboardPage from "./HomeDashboardPage";
import { renderWithProviders } from "@/test/utils";
import { handlerState } from "@/test/handlers";
import { sampleDashboard } from "@/test/fixtures";
import { server } from "@/test/server";
import type { User } from "@/types/auth";

const MEMBER: User = { id: 2, email: "reader@example.com", role: "member" };

async function renderDashboard(user?: User) {
  renderWithProviders(<HomeDashboardPage />, user ? { initialUser: user } : {});
  await screen.findByText("Крупнейшие объекты");
}

describe("Рейтинг объектов", () => {
  it("карточка ведёт в паспорт договора", async () => {
    await renderDashboard();

    const card = screen.getByTestId("rank-card-1");
    expect(within(card).getByRole("link", { name: /Паспорт/ })).toHaveAttribute(
      "href",
      "/contracts/11/passport"
    );
  });

  it("полоса относительной величины — доля от первого объекта", async () => {
    // Рейтинг приходит отсортированным с сервера, эталон — первая карточка.
    // Утверждение парное: 100 % у неё и СТРОГО МЕНЬШЕ у следующей. Без второй
    // половины тест прошёл бы и на полосе, у которой ширина всегда 100 %.
    await renderDashboard();

    expect(screen.getByTestId("rank-bar-2")).toHaveStyle({ width: "100%" });
    const next = Number(
      /width:\s*([\d.]+)%/.exec(
        screen.getByTestId("rank-bar-3").getAttribute("style") ?? ""
      )?.[1]
    );
    expect(next).toBeGreaterThan(0);
    expect(next).toBeLessThan(100);
  });

  it("объект без площади остаётся в рейтинге, но без ₽/м²", async () => {
    await renderDashboard();

    const card = screen.getByTestId("rank-card-3");
    expect(card).toHaveTextContent("площадь не заведена");
    expect(card).toHaveTextContent("₽/м² не считается");
  });
});

describe("Сноска исключённых", () => {
  it("показывает ОБА охвата в своих единицах и НЕ складывает их", async () => {
    // Решение 9 макета: «3 договора» и «1 объект» — разные сущности. Сумма 4
    // не должна появиться нигде в сноске: это величина, которой не существует.
    await renderDashboard();

    const note = screen.getByTestId("excluded-note");
    expect(note).toHaveTextContent("3 договора");
    expect(note).toHaveTextContent("1 объект");
    expect(note).not.toHaveTextContent("4");
  });

  it("объект БЕЗ ДОГОВОРОВ не исчезает молча", async () => {
    // Находка ревью Codex. Единственный ранее показанный объектный счётчик —
    // `many_contracts`; объект без единого договора попадал в
    // `no_contracts`, и когда исключённых ДОГОВОРОВ нет, сноска скрывалась
    // целиком ранним `return null`. Объект пропадал из рейтинга без единого
    // слова, хотя охват его исключение знал.
    server.use(
      http.get("/api/v1/analytics/dashboard", () =>
        HttpResponse.json({
          ...sampleDashboard,
          money: {
            ...sampleDashboard.money,
            coverage: {
              total: 3,
              counted: 3,
              reasons: { no_estimate: 0, amendment: 0, no_rate: 0, incomplete: 0 },
            },
          },
          ranking_coverage: {
            total: 4,
            counted: 3,
            reasons: { many_contracts: 0, no_contracts: 1, no_counted_contract: 0 },
          },
        })
      )
    );

    await renderDashboard();

    const note = screen.getByTestId("excluded-note");
    expect(note).toHaveTextContent("1 объект");
    expect(note).toHaveTextContent("без договоров");
    // Ссылка на таб обещает ЧАСТЬ причин, а не все: `no_contracts` ни одной из
    // пяти проверок решения 12 не виден, и «Причины — во вкладке» отправляло бы
    // читателя туда, где этой причины нет (находка ревью Codex).
    expect(note).toHaveTextContent("Часть причин");
    expect(note).not.toHaveTextContent("Причины — во вкладке");
  });

  it("называет РАЗНЫЕ места исключения", async () => {
    await renderDashboard();

    const note = screen.getByTestId("excluded-note");
    expect(note).toHaveTextContent("их нет и в итогах");
    expect(note).toHaveTextContent("сами договоры в итогах учтены");
  });

  it("ссылка на второй таб видна только admin", async () => {
    await renderDashboard();
    expect(screen.getByTestId("excluded-note")).toHaveTextContent(
      "во вкладке «На что обратить внимание»"
    );
  });

  it("у member ссылки на второй таб нет", async () => {
    await renderDashboard(MEMBER);
    expect(screen.getByTestId("excluded-note")).not.toHaveTextContent(
      "во вкладке «На что обратить внимание»"
    );
  });
});

describe("Диаграмма ₽/м² — обе стороны", () => {
  it("класс с ДВУМЯ объектами рисует полосу размаха", async () => {
    // ПОЛОЖИТЕЛЬНЫЙ случай. Без него компонент, не рисующий полос вовсе,
    // проходит отрицательный тест целиком.
    await renderDashboard();

    expect(screen.getByTestId("spread-1")).toBeInTheDocument();
    expect(within(screen.getByTestId("lane-1")).getAllByTestId(/^dot-/)).toHaveLength(2);
  });

  it("класс с ОДНИМ объектом полосы НЕ рисует", async () => {
    // Решение 11 макета: размаха не существует, рисовать его было бы выдумкой.
    await renderDashboard();

    expect(screen.getByTestId("lane-2")).toBeInTheDocument();
    expect(screen.queryByTestId("spread-2")).not.toBeInTheDocument();
  });

  it("охват диаграммы назван и отличается от охвата рейтинга", async () => {
    await renderDashboard();

    expect(screen.getByText(/Вошли 2 объекта из 4/)).toBeInTheDocument();
  });
});

describe("Таб «На что обратить внимание»", () => {
  async function openAttention() {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    await renderDashboard();
    await user.click(screen.getByRole("tab", { name: /На что обратить внимание/ }));
  }

  it("пять диагностик со своими счётчиками отрисованы", async () => {
    await openAttention();

    const panel = await screen.findByTestId("attention-issues");
    expect(within(panel).getByTestId("attention-estimates_without_vat_rate")).toHaveTextContent(
      "2"
    );
    expect(
      within(panel).getByTestId("attention-objects_with_several_contracts")
    ).toHaveTextContent("1");
    expect(within(panel).getByTestId("attention-contracts_without_estimate")).toHaveTextContent(
      "3"
    );
    expect(within(panel).getByTestId("attention-objects_without_area")).toHaveTextContent("4");
    expect(within(panel).getByTestId("attention-failed_imports_30d")).toHaveTextContent("1");
  });

  it("строка НДС не приписывает разногласию ставок чужую причину", async () => {
    // Находка ревью Codex. Текст макета («Нетто вывести не из чего») писался под
    // наивный запрос решения 12, который ловил только смету без базы. Спека
    // §2.3а перевела счётчик на `effective_display_rate`, и он ловит ВТОРУЮ
    // причину — разногласие ставок предложений. На ней старый текст ложен: базы
    // известны, нетто выводится построчно, не выводится ЕДИНАЯ ставка показа.
    await openAttention();

    const row = await screen.findByTestId("attention-estimates_without_vat_rate");
    expect(row).not.toHaveTextContent("Нетто вывести не из чего");
    expect(row).toHaveTextContent("предложения разошлись");
    expect(row).toHaveTextContent("не входит в денежные итоги");
  });

  it("ДЕЙСТВИЙ НЕТ ВОВСЕ — ни ссылок, ни кнопок", async () => {
    // Проверка идёт ПО РОЛЯМ, а не по адресу ссылки: утверждение «нет ссылок с
    // пустым или `#` адресом» пропустило бы и рабочую ссылку, и `<button>`, то
    // есть доказывало бы не то, что требует спека. v1 показывает счётчики без
    // действий (решение пользователя на гейте 3).
    await openAttention();

    const panel = await screen.findByTestId("attention-issues");
    expect(within(panel).queryAllByRole("link")).toHaveLength(0);
    expect(within(panel).queryAllByRole("button")).toHaveLength(0);
  });

  it("пустое состояние называет СВОЙ охват, а не всю базу", async () => {
    // Находка ревью Codex: «Всё, что база умеет проверить, сходится» —
    // утверждение обо ВСЕЙ базе, тогда как таб проверяет ровно пять вещей.
    // Объект без договоров ни одной из них не виден, и главная в этот самый
    // момент пишет «1 объект без договоров»: два экрана утверждали бы
    // противоположное. Пустое состояние обязано называть свой охват.
    handlerState.attentionOutcome = "clean";
    await openAttention();

    const ok = await screen.findByTestId("attention-ok");
    expect(ok).toHaveTextContent("Пять проверок базы не нашли расхождений");
    expect(ok).not.toHaveTextContent("Всё, что база умеет проверить");
    // «Всё сходится» и список нулей — два разных сообщения, и вместе они
    // противоречат друг другу.
    await waitFor(() =>
      expect(screen.queryByTestId("attention-issues")).not.toBeInTheDocument()
    );
    expect(screen.queryByText("Сметы без ставки НДС")).not.toBeInTheDocument();
  });
});

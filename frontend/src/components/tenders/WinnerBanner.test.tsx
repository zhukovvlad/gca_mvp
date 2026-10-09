import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { WinnerBanner } from "./WinnerBanner";
import {
  sampleAwardHistoryWithNotConcluded,
  sampleObjects,
  sampleTenderAward,
  sampleTenderAwardWithContract,
  sampleTenderCard,
} from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { TenderAward, TenderAwardEvent, TenderCard } from "@/types/domain";

/**
 * Плашка победителя (спека Б2 §2.8, макет экраны 1, 2, 4, 6): четыре состояния.
 * Сумма — «… млн с НДС», `null` — «итог недоступен».
 */
function cardWith(award: TenderAward | null, history: TenderAwardEvent[] = []): TenderCard {
  return { ...sampleTenderCard, award, award_history: history };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="path">{location.pathname}</div>;
}

function renderBanner(card: TenderCard, options?: Parameters<typeof renderWithProviders>[1]) {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/tenders/:id" element={<WinnerBanner card={card} />} />
        <Route path="/contracts/:id" element={<div>карточка договора</div>} />
      </Routes>
      <LocationProbe />
    </>,
    { initialRoute: "/tenders/300", ...options }
  );
}

const MEMBER = { id: 2, email: "member@example.com", role: "member" } as const;

describe("WinnerBanner: победитель не отмечен", () => {
  it("строка подсказки вместо плашки, без действий", () => {
    renderBanner(cardWith(null));

    expect(
      screen.getByText("Победитель не отмечен. Отметка ставится в финальном этапе, когда решение принято.")
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/Победитель —/)).toBeNull();
  });
});

describe("WinnerBanner: отмечен, договора нет", () => {
  it("называет победителя, КП, сумму с НДС, дату и показывает четыре действия", () => {
    renderBanner(cardWith(sampleTenderAward));

    expect(screen.getByText("Победитель — ТОО Монолит")).toBeInTheDocument();
    expect(
      screen.getByText(/КП этапа 2 · 9\s720 млн с НДС · отмечен 20\.01\.2026 · договора пока нет/)
    ).toBeInTheDocument();
    for (const name of [
      "Создать договор",
      "Привязать существующий договор",
      "Договор не заключён",
      "Снять отметку",
    ]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
    expect(screen.queryByRole("link", { name: "Открыть договор" })).toBeNull();
  });

  // Ревью задачи 9 (F3): `outline` несёт `dark:border-input`, перебивающий рамку
  // специфичностью; в jsdom каскада нет — проверяется наличие двойника класса.
  it("«Договор не заключён» держит рамку-предупреждение и в тёмной теме", () => {
    renderBanner(cardWith(sampleTenderAward));

    const button = screen.getByRole("button", { name: "Договор не заключён" });
    expect(button).toHaveClass("border-warning-border", "dark:border-warning-border");
  });

  it("итог «с НДС» не определён — «итог недоступен»", () => {
    renderBanner(cardWith({ ...sampleTenderAward, total_including_vat: null }));

    expect(screen.getByText(/КП этапа 2 · итог недоступен · отмечен/)).toBeInTheDocument();
    expect(screen.queryByText(/млн с НДС/)).toBeNull();
  });

  it("member видит плашку, но не действия", () => {
    renderBanner(cardWith(sampleTenderAward), { initialUser: MEMBER });

    expect(screen.getByText("Победитель — ТОО Монолит")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("WinnerBanner: отмечен, договор есть", () => {
  it("вместо действий — ссылка «Открыть договор»; «Снять» и «Не заключён» нет", () => {
    renderBanner(cardWith(sampleTenderAwardWithContract));

    expect(screen.getByText(/договор № 45\/2026-ГП от 26\.06\.2026/)).toBeInTheDocument();
    expect(screen.queryByText(/договора пока нет/)).toBeNull();
    expect(screen.getByRole("link", { name: "Открыть договор" })).toHaveAttribute("href", "/contracts/100");
    for (const name of [
      "Создать договор",
      "Привязать существующий договор",
      "Договор не заключён",
      "Снять отметку",
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("ссылку видит и member", () => {
    renderBanner(cardWith(sampleTenderAwardWithContract), { initialUser: MEMBER });

    expect(screen.getByRole("link", { name: "Открыть договор" })).toBeInTheDocument();
  });
});

describe("WinnerBanner: история", () => {
  it("есть «не заключён» — история в порядке сервера, «· действующий» только у действующей строки", () => {
    renderBanner(cardWith(sampleTenderAward, sampleAwardHistoryWithNotConcluded));

    const rows = screen.getAllByTestId("award-history-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("22.12.2025");
    expect(rows[0]).toHaveTextContent("отмечен ООО Альфа");
    expect(rows[1]).toHaveTextContent("15.01.2026");
    expect(rows[1]).toHaveTextContent("договор с ООО Альфа не заключён");
    expect(rows[1]).toHaveTextContent("«Не согласовали размер аванса»");
    expect(rows[2]).toHaveTextContent("20.01.2026");
    expect(rows[2]).toHaveTextContent("отмечен ТОО Монолит");
    expect(rows.map((r) => r.textContent?.includes("· действующий"))).toEqual([false, false, true]);
  });

  it("в истории только «отмечен» — блока истории нет", () => {
    renderBanner(cardWith(sampleTenderAward, [sampleAwardHistoryWithNotConcluded[2]]));

    expect(screen.queryByTestId("award-history-row")).toBeNull();
  });

  it("действующей отметки нет, но «не заключён» в истории есть — подсказка и история", () => {
    renderBanner(cardWith(null, sampleAwardHistoryWithNotConcluded.slice(0, 2)));

    expect(screen.getByText(/Победитель не отмечен/)).toBeInTheDocument();
    expect(screen.getAllByTestId("award-history-row")).toHaveLength(2);
    expect(screen.queryByText(/· действующий/)).toBeNull();
  });

  it("запись «не заключён» без комментария не печатает пустые кавычки", () => {
    const history = sampleAwardHistoryWithNotConcluded.slice(0, 2).map((e) => ({ ...e, note: null }));
    renderBanner(cardWith(null, history));

    expect(screen.getAllByTestId("award-history-row")[1]).not.toHaveTextContent("«");
  });
});

describe("WinnerBanner: окна действий", () => {
  it("«Снять отметку» открывает окно подтверждения", async () => {
    const user = userEvent.setup();
    renderBanner(cardWith(sampleTenderAward));

    await user.click(screen.getByRole("button", { name: "Снять отметку" }));

    expect(await screen.findByRole("alertdialog", { name: "Снять отметку?" })).toBeInTheDocument();
  });

  it("«Договор не заключён» открывает окно с датой", async () => {
    const user = userEvent.setup();
    renderBanner(cardWith(sampleTenderAward));

    await user.click(screen.getByRole("button", { name: "Договор не заключён" }));

    const dialog = await screen.findByRole("dialog", { name: "Договор не заключён" });
    expect(within(dialog).getByLabelText("Дата")).toBeInTheDocument();
  });

  it("«Привязать существующий договор» открывает окно со списком кандидатов", async () => {
    const user = userEvent.setup();
    renderBanner(cardWith(sampleTenderAward));

    await user.click(screen.getByRole("button", { name: "Привязать существующий договор" }));

    const dialog = await screen.findByRole("dialog", { name: "Привязать существующий договор" });
    expect(await within(dialog).findAllByRole("radio")).toHaveLength(2);
  });

  it("«Создать договор»: форма «из отметки», объект — object_id карточки тендера, а не её класс", async () => {
    // Снимок класса тендера (rate_class_id 10) не совпадает с объектом: если бы
    // форма получила его вместо object_id, она запросила бы объект 10.
    const requested: number[] = [];
    server.use(
      http.get("/api/v1/objects/:id", ({ params }) => {
        requested.push(Number(params.id));
        return HttpResponse.json(sampleObjects[1]);
      })
    );
    const user = userEvent.setup();
    renderBanner({ ...cardWith(sampleTenderAward), object_id: 11, object_title: "ЖК Южный", rate_class_id: 10 });

    await user.click(screen.getByRole("button", { name: "Создать договор" }));

    expect(await screen.findByText("Новый договор по тендеру")).toBeInTheDocument();
    expect(screen.getByTestId("locked-object")).toHaveTextContent("ЖК Южный");
    await waitFor(() => expect(requested).toContain(11));
    expect(requested).not.toContain(10);
  });

  it("договор создан — переход в карточку договора", async () => {
    const user = userEvent.setup();
    renderBanner(cardWith(sampleTenderAward));

    await user.click(screen.getByRole("button", { name: "Создать договор" }));
    await waitForDialogFocus();
    await waitFor(() => expect(screen.getByTestId("locked-class")).toHaveTextContent("Жилые дома"));
    await user.type(screen.getByLabelText("Номер договора"), "45/2026-ГП");
    await user.type(screen.getByLabelText("Дата подписания"), "2026-06-26");
    await user.click(screen.getByRole("button", { name: "Создать договор" }));

    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/contracts/102"));
    expect(handlerState.lastAwardContract?.awardId).toBe(7);
  });
});


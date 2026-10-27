import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

/**
 * Вход по адресу `/families?tab=suggestions&unit=<id>&context=<id>` (ссылка с
 * подписи «ждёт решения администратора», когда у контекста есть открытое
 * предложение семьи): вкладка «Предложения» с фильтром единицы, группа с
 * предложением этого контекста раскрыта, а само предложение подсвечено.
 * Фикстуры: единица 5 — м² (`M2`), 3 — м³ (`M3`); контекст 3004 — предложение
 * второй (по умолчанию закрытой) группы м², 3005 — группа м³.
 */
const MID_GROUP_CONTEXT = 3004;
const M3_CONTEXT = 3005;
/** Очередь «Новая»: контекст 4012 — предложение новой семьи м²; 4000 — строка единицы без семей, без предложения. */
const NEW_FAMILY_CONTEXT = 4012;
const BARE_NEW_CONTEXT = 4000;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

function renderAt(route: string) {
  return renderWithProviders(
    <>
      <FamiliesPage />
      <LocationProbe />
    </>,
    { initialRoute: route }
  );
}

const highlighted = () => document.querySelectorAll('[data-testid="suggestion-row"][data-highlighted="true"]');
const highlightedNew = () => document.querySelectorAll('[data-testid="new-row"][data-highlighted="true"]');
const unitsAsked = () => handlerState.suggestionsRequests.map((search) => new URLSearchParams(search).get("unit"));

describe("FamiliesPage: вход по адресу ?tab=suggestions&unit=&context=", () => {
  it("открывает «Предложения» с фильтром единицы и подсвечивает предложение контекста в закрытой группе", async () => {
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);

    expect(screen.getByRole("tab", { name: "Предложения" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(highlighted()).toHaveLength(1));
    expect(highlighted()[0]).toHaveTextContent(handlerState.suggestionGroups[1].rows[0].title);
    expect(unitsAsked()).toContain("5");
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("предложение вне очереди (другая единица): короткая заметка и ссылка на карточку контекста", async () => {
    renderAt(`/families?tab=suggestions&unit=5&context=${M3_CONTEXT}`);

    const note = await screen.findByTestId("suggestion-not-found");

    expect(note).toHaveTextContent(/предложение не найдено в очереди/i);
    expect(within(note).getByRole("link", { name: /карточк/i })).toHaveAttribute(
      "href",
      `/families?context=${M3_CONTEXT}`
    );
    expect(highlighted()).toHaveLength(0);
  });

  it("контекст без предложения в очереди: та же заметка", async () => {
    renderAt("/families?tab=suggestions&context=999999");

    expect(await screen.findByTestId("suggestion-not-found")).toBeInTheDocument();
  });

  it("единица `none` — валидный фильтр «без единицы»", async () => {
    renderAt(`/families?tab=suggestions&unit=none&context=${MID_GROUP_CONTEXT}`);

    await screen.findByTestId("suggestion-not-found");
    expect(unitsAsked()).toContain("none");
  });

  it("ссылка из заметки открывает карточку контекста на вкладке «Контексты»", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${M3_CONTEXT}`);
    const note = await screen.findByTestId("suggestion-not-found");

    await user.click(within(note).getByRole("link", { name: /карточк/i }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Контексты" })).toHaveAttribute("aria-selected", "true")
    );
    expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${M3_CONTEXT}`);
  });

  it.each([
    ["единица не число", "tab=suggestions&unit=abc&context=3004"],
    ["единица ноль", "tab=suggestions&unit=0&context=3004"],
    ["контекст не число", "tab=suggestions&unit=5&context=abc"],
    ["контекст ноль", "tab=suggestions&unit=5&context=0"],
    ["контекста нет", "tab=suggestions&unit=5"],
    ["очередь не из списка", "tab=suggestions&queue=change&unit=5&context=3004"],
    ["очередь не названа верно", "tab=suggestions&queue=abc&unit=5&context=3004"],
  ])("%s: страница открывается как обычно", async (_name, query) => {
    renderAt(`/families?${query}`);

    expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("переключение вкладки вручную убирает параметры входа из адреса", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);
    await waitFor(() => expect(highlighted()).toHaveLength(1));

    await user.click(screen.getByRole("tab", { name: "Семьи" }));

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/));
  });

  it("ссылка на ту же страницу, пока она открыта, переводит на вкладку «Предложения»", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Link to={`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`}>ссылка</Link>
        <FamiliesPage />
      </>,
      { initialRoute: "/families" }
    );

    await user.click(screen.getByRole("link", { name: "ссылка" }));

    await waitFor(() => expect(highlighted()).toHaveLength(1));
    expect(screen.getByRole("tab", { name: "Предложения" })).toHaveAttribute("aria-selected", "true");
  });

  it("queue=list называет очередь «Семья из списка» явно", async () => {
    renderAt(`/families?tab=suggestions&queue=list&unit=5&context=${MID_GROUP_CONTEXT}`);

    await waitFor(() => expect(highlighted()).toHaveLength(1));
  });

  it("queue=new открывает очередь «Новая» и подсвечивает предложение новой семьи", async () => {
    renderAt(`/families?tab=suggestions&queue=new&unit=5&context=${NEW_FAMILY_CONTEXT}`);

    await waitFor(() => expect(highlightedNew()).toHaveLength(1));
    expect(screen.getByRole("tab", { name: /Новая/ })).toHaveAttribute("aria-selected", "true");
    expect(highlightedNew()[0]).toHaveTextContent(handlerState.newRows[1].title);
    expect(highlighted()).toHaveLength(0);
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("queue=new: строка без предложения (единица без семей) не считается найденной", async () => {
    renderAt(`/families?tab=suggestions&queue=new&context=${BARE_NEW_CONTEXT}`);

    expect(await screen.findByTestId("suggestion-not-found")).toBeInTheDocument();
    expect(highlightedNew()).toHaveLength(0);
  });

  it("queue=new: предложения нет в очереди (другая единица) — заметка со ссылкой на карточку", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&queue=new&unit=3&context=${NEW_FAMILY_CONTEXT}`);
    const note = await screen.findByTestId("suggestion-not-found");

    await user.click(within(note).getByRole("link", { name: /карточк/i }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Контексты" })).toHaveAttribute("aria-selected", "true")
    );
    expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${NEW_FAMILY_CONTEXT}`);
  });

  it("предложение новой семьи не ищется в очереди «Семья из списка»", async () => {
    renderAt(`/families?tab=suggestions&queue=list&unit=5&context=${NEW_FAMILY_CONTEXT}`);

    expect(await screen.findByTestId("suggestion-not-found")).toBeInTheDocument();
  });
});

/** Строки очереди далеко от начала: страница, «показать все» и прокрутка подбираются под цель. */
describe("FamiliesPage: цель ссылки за пределами первой страницы и первых строк", () => {
  const originalScroll = Element.prototype.scrollIntoView;
  afterEach(() => {
    Element.prototype.scrollIntoView = originalScroll;
  });

  function recordScrolls(): Element[] {
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    return scrolled;
  }

  it("«Семья из списка»: группа цели на второй странице — страница открыта, строка подсвечена и прокручена", async () => {
    const scrolled = recordScrolls();
    const base = handlerState.suggestionGroups[0];
    const fillers = Array.from({ length: 12 }, (_, i) => ({
      ...base,
      family_id: 7000 + i,
      family_title: `Аа семья ${String(i).padStart(2, "0")}`,
      total: 1,
      rows: [{ ...base.rows[0], suggestion_id: 7000 + i, context_id: 97000 + i }],
    }));
    const target = { ...base, family_id: 7999, family_title: "Яя семья цели", total: 1, rows: [{ ...base.rows[0], suggestion_id: 7999, context_id: 97999 }] };
    handlerState.suggestionGroups = [...fillers, target];

    renderAt("/families?tab=suggestions&unit=5&context=97999");

    await waitFor(() => expect(highlighted()).toHaveLength(1));
    expect(scrolled).toContain(highlighted()[0]);
  });

  it("«Семья из списка»: цель дальше видимых строк группы — группа развёрнута целиком", async () => {
    const base = handlerState.suggestionGroups[0];
    const rows = Array.from({ length: 26 }, (_, i) => ({ ...base.rows[0], suggestion_id: 8000 + i, context_id: 98000 + i }));
    handlerState.suggestionGroups = [{ ...base, rows, total: rows.length }];

    renderAt("/families?tab=suggestions&unit=5&context=98025");

    await waitFor(() => expect(highlighted()).toHaveLength(1));
  });

  it("«Новая»: цель на второй странице — страница открыта, строка подсвечена и прокручена", async () => {
    const scrolled = recordScrolls();
    const base = handlerState.newRows[0];
    handlerState.newRows = Array.from({ length: 13 }, (_, i) => ({ ...base, suggestion_id: 9000 + i, context_id: 99000 + i }));

    renderAt("/families?tab=suggestions&queue=new&unit=5&context=99012");

    await waitFor(() => expect(highlightedNew()).toHaveLength(1));
    expect(scrolled).toContain(highlightedNew()[0]);
  });
});

describe("FamiliesPage: заметка «не найдено» и жизнь цели", () => {
  it("пока очередь грузится, заметки «не найдено» нет", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);

    await waitFor(() => expect(hold.seen.count).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() => expect(highlighted()).toHaveLength(1));
  });

  it("заметка относится к очереди цели: в другой очереди её нет", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${M3_CONTEXT}`);
    await screen.findByTestId("suggestion-not-found");

    await user.click(screen.getByRole("tab", { name: /Новая/ }));

    await waitFor(() => expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument());
  });

  it("переход «Открыть семью» изнутри вкладки снимает параметры входа из адреса", async () => {
    const user = userEvent.setup();
    handlerState.unitHoldGroups[0].family_id = 43;
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);
    await waitFor(() => expect(highlighted()).toHaveLength(1));

    await user.click(await screen.findByRole("tab", { name: /Ошибки/ }));
    await user.click(await screen.findByRole("button", { name: "Открыть семью" }));

    await waitFor(() => expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true"));
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/));
  });

  it("переход «Показать контексты» из панели семьи снимает параметры адреса", async () => {
    const user = userEvent.setup();
    renderAt("/families?context=abc");

    await user.click(await screen.findByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: /Показать контексты/ }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Контексты" })).toHaveAttribute("aria-selected", "true")
    );
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/));
  });

  it("«Отклонить» подсвеченной строки: строка ушла из очереди, заметки «не найдено» нет", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);
    await waitFor(() => expect(highlighted()).toHaveLength(1));
    const targetId = handlerState.suggestionGroups[1].rows[0].suggestion_id;

    await user.click(within(highlighted()[0] as HTMLElement).getByRole("button", { name: "Отклонить" }));

    await waitFor(() => expect(handlerState.rejectSuggestionRequests).toEqual([targetId]));
    await waitFor(() => expect(highlighted()).toHaveLength(0));
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("«Подтвердить отмеченные» в группе подсвеченной строки: строка ушла, заметки «не найдено» нет", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);
    await waitFor(() => expect(highlighted()).toHaveLength(1));
    const targetId = handlerState.suggestionGroups[1].rows[0].suggestion_id;
    const group = (highlighted()[0] as HTMLElement).closest('[data-testid="suggestion-group"]') as HTMLElement;

    await user.click(within(group).getByRole("button", { name: /Подтвердить отмеченные/ }));

    await waitFor(() =>
      expect(handlerState.confirmSuggestionsRequests.flat()).toContain(targetId)
    );
    await waitFor(() => expect(highlighted()).toHaveLength(0));
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("«Новая»: пока очередь грузится, заметки «не найдено» нет", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/suggestions");
    renderAt(`/families?tab=suggestions&queue=new&unit=5&context=${NEW_FAMILY_CONTEXT}`);

    await waitFor(() => expect(hold.seen.count).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() => expect(highlightedNew()).toHaveLength(1));
  });

  it("«Новая»: найденная цель, ушедшая из очереди, «не найденной» не становится", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&queue=new&unit=5&context=${NEW_FAMILY_CONTEXT}`);
    await waitFor(() => expect(highlightedNew()).toHaveLength(1));
    handlerState.newRows = handlerState.newRows.filter((r) => r.context_id !== NEW_FAMILY_CONTEXT);

    // Переключение очереди перечитывает «Новую»: решения по цели больше нет в ответе сервера.
    await user.click(screen.getByRole("tab", { name: /Семья из списка/ }));
    await user.click(screen.getByRole("tab", { name: /Новая/ }));

    await waitFor(() => expect(highlightedNew()).toHaveLength(0));
    expect(document.querySelectorAll('[data-testid="new-row"]').length).toBeGreaterThan(0);
    expect(screen.queryByTestId("suggestion-not-found")).not.toBeInTheDocument();
  });

  it("новая ссылка при открытой странице: найденность прежней цели на новую не переносится", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Link to={`/families?tab=suggestions&unit=5&context=${M3_CONTEXT}`}>другая цель</Link>
        <FamiliesPage />
      </>,
      { initialRoute: `/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}` }
    );
    await waitFor(() => expect(highlighted()).toHaveLength(1));

    await user.click(screen.getByRole("link", { name: "другая цель" }));

    expect(await screen.findByTestId("suggestion-not-found")).toBeInTheDocument();
    expect(highlighted()).toHaveLength(0);
  });

  it("ручной уход с вкладки и возврат на «Предложения» — цели больше нет", async () => {
    const user = userEvent.setup();
    renderAt(`/families?tab=suggestions&unit=5&context=${MID_GROUP_CONTEXT}`);
    await waitFor(() => expect(highlighted()).toHaveLength(1));

    await user.click(screen.getByRole("tab", { name: "Семьи" }));
    await user.click(screen.getByRole("tab", { name: "Предложения" }));

    await waitFor(() => expect(document.querySelectorAll('[data-testid="suggestion-row"]').length).toBeGreaterThan(0));
    expect(highlighted()).toHaveLength(0);
  });
});

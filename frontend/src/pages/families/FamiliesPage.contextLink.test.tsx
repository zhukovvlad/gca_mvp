import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Вход по адресу `/families?context=<id>` (ссылка с подписи «ждёт решения
 * администратора» экрана «Против нашей цены»): открывается вкладка контекстов и
 * карточка этого контекста, даже когда в списке его нет.
 */
const LINKED_CONTEXT_ID = 602;

function titleOf(id: number): string {
  return handlerState.semanticContexts.find((c) => c.id === id)!.standard_job_title;
}

/** Заголовок карточки контекста из адреса: `h3` с его названием (в списке названия — ячейки таблицы). */
function cardHeading() {
  const title = titleOf(LINKED_CONTEXT_ID);
  return screen.findByRole("heading", { level: 3, name: (name) => name.includes(title) });
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="loc" data-key={location.key}>
      {location.pathname + location.search}
    </div>
  );
}

function BackProbe() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      назад-проба
    </button>
  );
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

describe("FamiliesPage: вход по адресу ?context=", () => {
  it("открывает вкладку «Контексты» и карточку контекста из адреса", async () => {
    renderAt(`/families?context=${LINKED_CONTEXT_ID}`);

    expect(screen.getByRole("tab", { name: "Контексты" })).toHaveAttribute("aria-selected", "true");
    const selected = await screen.findAllByRole("row", { selected: true });
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveTextContent(titleOf(LINKED_CONTEXT_ID));
    expect(await cardHeading()).toBeInTheDocument();
  });

  it("показывает карточку, даже когда списка контекстов этому контексту нет", async () => {
    server.use(
      http.get("/api/v1/semantic/contexts", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })
      )
    );
    renderAt(`/families?context=${LINKED_CONTEXT_ID}`);

    expect(await screen.findByText("Контекстов нет")).toBeInTheDocument();
    expect(await cardHeading()).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${LINKED_CONTEXT_ID}`);
  });

  it("без параметра открыта вкладка «Семьи», адрес не меняется", async () => {
    renderAt("/families");

    expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/);
  });

  it("выбор другой строки меняет context в адресе", async () => {
    const user = userEvent.setup();
    const other = handlerState.semanticContexts.find((c) => c.id !== LINKED_CONTEXT_ID)!;
    renderAt(`/families?context=${LINKED_CONTEXT_ID}`);
    await screen.findAllByText(titleOf(LINKED_CONTEXT_ID));

    await user.click((await screen.findAllByText(other.standard_job_title))[0]);

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${other.id}`));
  });

  it("выбор строки без адреса сам ставит context в адрес", async () => {
    const user = userEvent.setup();
    const first = handlerState.semanticContexts[0];
    renderAt("/families");
    await user.click(screen.getByRole("tab", { name: "Контексты" }));

    await user.click((await screen.findAllByText(first.standard_job_title))[0]);

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${first.id}`));
  });

  it("выбор, снятый фильтром, убирает context из адреса", async () => {
    const user = userEvent.setup();
    const first = handlerState.semanticContexts[0];
    renderAt("/families");
    await user.click(screen.getByRole("tab", { name: "Контексты" }));
    await user.click((await screen.findAllByText(first.standard_job_title))[0]);
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("context="));

    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "нет такой работы");

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/));
  });

  it("контекст из адреса остаётся выбранным, когда фильтр убрал его из списка", async () => {
    const user = userEvent.setup();
    renderAt(`/families?context=${LINKED_CONTEXT_ID}`);
    await screen.findAllByText(titleOf(LINKED_CONTEXT_ID));

    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "нет такой работы");

    expect(await screen.findByText("Контекстов нет")).toBeInTheDocument();
    expect(await cardHeading()).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${LINKED_CONTEXT_ID}`);
  });

  it.each(["0", "-3", "1.5", "abc", "", "1e2", "0x10", "%207", "1.0"])("context=%s контекста не называет: страница открывается как обычно", async (raw) => {
    renderAt(`/families?context=${raw}`);

    expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("row", { selected: true })).toHaveLength(0);
  });

  it("вход по ссылке не переписывает запись истории, когда адрес уже совпадает с выбором", async () => {
    renderAt(`/families?context=${LINKED_CONTEXT_ID}`);

    expect(await cardHeading()).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveAttribute("data-key", "default");
  });

  it("смена выбора не добавляет шагов истории: «Назад» возвращает туда, откуда пришли по ссылке", async () => {
    const user = userEvent.setup();
    const other = handlerState.semanticContexts.find((c) => c.id !== LINKED_CONTEXT_ID)!;
    renderWithProviders(
      <>
        <Routes>
          <Route path="/from" element={<Link to={`/families?context=${LINKED_CONTEXT_ID}`}>к карточке</Link>} />
          <Route path="/families" element={<FamiliesPage />} />
        </Routes>
        <BackProbe />
        <LocationProbe />
      </>,
      { initialRoute: "/from" }
    );
    await user.click(screen.getByRole("link", { name: "к карточке" }));
    await screen.findAllByText(titleOf(LINKED_CONTEXT_ID));
    await user.click((await screen.findAllByText(other.standard_job_title))[0]);
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${other.id}`));

    await user.click(screen.getByRole("button", { name: "назад-проба" }));

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(/^\/from$/));
  });

  it("пункт меню «Семьи» на открытой ссылке сбрасывает выбор и не возвращает context в адрес", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Link to="/families">пункт меню</Link>
        <Routes>
          <Route path="/families" element={<FamiliesPage />} />
        </Routes>
        <BackProbe />
        <LocationProbe />
      </>,
      { initialRoute: `/families?context=${LINKED_CONTEXT_ID}` }
    );
    expect(await cardHeading()).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "пункт меню" }));

    expect(await screen.findByText("Контекст не выбран")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByTestId("loc")).toHaveTextContent(/^\/families$/);

    await user.click(screen.getByRole("button", { name: "назад-проба" }));

    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${LINKED_CONTEXT_ID}`));
  });

  it("ссылка на другой контекст при открытой странице: его карточка держится, даже когда списка ему нет", async () => {
    const user = userEvent.setup();
    const other = handlerState.semanticContexts.find((c) => c.id !== LINKED_CONTEXT_ID)!;
    server.use(
      http.get("/api/v1/semantic/contexts", () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }))
    );
    renderWithProviders(
      <>
        <Link to={`/families?context=${other.id}`}>другой контекст</Link>
        <Routes>
          <Route path="/families" element={<FamiliesPage />} />
        </Routes>
        <LocationProbe />
      </>,
      { initialRoute: `/families?context=${LINKED_CONTEXT_ID}` }
    );
    expect(await cardHeading()).toBeInTheDocument();
    expect(await screen.findByText("Контекстов нет")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "другой контекст" }));

    expect(
      await screen.findByRole("heading", { level: 3, name: (name) => name.includes(other.standard_job_title) })
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent(`/families?context=${other.id}`);
  });
});

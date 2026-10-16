import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/** Состояния ожидания вкладки «Семьи»: кнопки панели на время запроса, заглушки, полоса. */

afterEach(() => {
  localStorage.clear();
});

describe("FamiliesTab — ожидание действий", () => {
  it("«Сохранить»: на время запроса кнопка занята, ответ пришёл — снова доступна", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesTab />);
    await user.click(await screen.findByText("Семья работ №3"));
    const title = await screen.findByLabelText("Название");
    await user.type(title, " доп");
    const hold = holdRequests("patch", "/api/v1/semantic/families/:id");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();

    hold.release();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Сохранить" })).not.toHaveAttribute("aria-busy")
    );
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
  });

  it("«Активировать»: на время запроса кнопка занята; «Сохранить» рядом не крутится", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesTab />);
    await user.click(await screen.findByText("Семья работ №1"));
    const name = "Активировать семью Семья работ №1";
    await screen.findByRole("button", { name });
    server.use(
      http.post("/api/v1/semantic/families/:id/activate", () => HttpResponse.json({ detail: "x" }, { status: 500 }))
    );
    const hold = holdRequests("post", "/api/v1/semantic/families/:id/activate");
    const button = screen.getByRole("button", { name });
    if (button.hasAttribute("disabled")) {
      // Фикстура без категории: выбрать её, чтобы кнопка стала доступной.
      await user.click(screen.getByRole("combobox", { name: "Категория" }));
      await user.click(await screen.findByRole("option", { name: "Работа" }));
      await waitFor(() => expect(screen.getByRole("button", { name })).toBeEnabled());
    }

    await user.click(screen.getByRole("button", { name }));

    await waitFor(() => expect(screen.getByRole("button", { name })).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("button", { name })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Сохранить" })).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() => expect(screen.getByRole("button", { name })).not.toHaveAttribute("aria-busy"));
  });

  it("«В архив»: после подтверждения окно закрыто, а кнопка в панели занята на время запроса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesTab />);
    await user.click(await screen.findByText("Семья работ №3"));
    const hold = holdRequests("post", "/api/v1/semantic/families/:id/archive");
    await user.click(await screen.findByRole("button", { name: "В архив: семья Семья работ №3" }));
    await user.click(await screen.findByRole("button", { name: "Архивировать" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "В архив: семья Семья работ №3" })).toHaveAttribute(
        "aria-busy",
        "true"
      )
    );
    expect(screen.getByRole("button", { name: "В архив: семья Семья работ №3" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});

/** Ревью пачки 2: окна вкладки, у которых входа на ожидание не было. */
describe("FamiliesTab — ожидание в окнах", () => {
  it("«Создать» в окне новой семьи: на время запроса кнопка занята, ответ пришёл — окно закрыто", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesTab />);
    await screen.findByText("Семья работ №3");
    await user.click(screen.getByRole("button", { name: /Новая семья/ }));
    await user.type(await screen.findByLabelText("Название (обязательно)"), "Новая семья работ");
    const hold = holdRequests("post", "/api/v1/semantic/families");

    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Создать" })).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("button", { name: "Создать" })).toBeDisabled();
    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(hold.seen.count).toBe(1);
  });

  it("«Слить» в окне слияния семей: на время запроса кнопка занята", async () => {
    const user = userEvent.setup();
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({ ...base, id: 990, title: "Другая активная семья" });
    renderWithProviders(<FamiliesTab />);
    await screen.findByText("Семья работ №3");
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "активна" }));
    await user.click(await screen.findByText("Кровельные работы"));
    await user.click(await screen.findByRole("button", { name: "Слить" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Целевая семья" }));
    await user.click(await screen.findByRole("option", { name: "Другая активная семья" }));
    const hold = holdRequests("post", "/api/v1/semantic/families/:id/merge");

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Слить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});

describe("FamiliesTab — первая загрузка и перечитывание", () => {
  it("до ответа заглушки без текста «пусто»; пустой ответ — «Семей нет» без заглушек", async () => {
    server.use(http.get("/api/v1/semantic/families", () => HttpResponse.json({ items: [] })));
    const hold = holdRequests("get", "/api/v1/semantic/families");
    renderWithProviders(<FamiliesTab />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Семей нет")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Семей нет")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("непустой ответ: строки вместо заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/families");
    renderWithProviders(<FamiliesTab />);
    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Семья работ №3")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("после действия список на месте, пока едут новые данные; полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesTab />);
    await user.click(await screen.findByText("Семья работ №3"));
    await user.type(await screen.findByLabelText("Название"), " доп");
    const hold = holdRequests("get", "/api/v1/semantic/families");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect((await screen.findAllByRole("progressbar", { name: "Обновление данных" })).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Семья работ №1").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });
});

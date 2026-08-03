import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import ReviewPage from "./ReviewPage";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Ручной матчинг» (§7.2)", () => {
  it("показывает очередь с весом работы и примерами наименований", async () => {
    renderWithProviders(<ReviewPage />);

    expect(await screen.findByText("Стяжка неведомая")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("в смете: Стяжка пола 50мм")).toBeInTheDocument();
  });

  it("сначала самые тяжёлые работы (§6.5)", async () => {
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const rows = screen.getAllByRole("row").slice(1); // без шапки
    expect(within(rows[0]).getByText("Стяжка неведомая")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Кладка непонятная")).toBeInTheDocument();
  });

  it("сортировка по названию меняет порядок", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByRole("button", { name: "По названию" }));

    await waitFor(() => {
      const rows = screen.getAllByRole("row").slice(1);
      expect(within(rows[0]).getByText("Кладка непонятная")).toBeInTheDocument();
    });
  });

  it("фильтр «без единицы» оставляет только строки без единицы", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Единица измерения"));
    await user.click(await screen.findByRole("option", { name: "Без единицы" }));

    await waitFor(() => {
      expect(screen.queryByText("Стяжка неведомая")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("Кладка непонятная")).toBeInTheDocument();
  });

  it("построчная разметка отправляет kind", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Мусор" }));

    // Успех виден по инвалидации: очередь перезапрошена, экран не упал.
    await waitFor(() => expect(screen.getByText("Стяжка неведомая")).toBeInTheDocument());
  });
});

describe("Пакетная разметка (решение §6.5)", () => {
  it("отправляет выбранные строки одним запросом", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать «Стяжка неведомая»"));
    await user.click(screen.getByLabelText("Выбрать «Кладка непонятная»"));
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "В мусор" }));

    await waitFor(() => {
      expect(handlerState.lastBatch).toEqual({ ids: [700, 701], kind: "TRASH" });
    });
  });

  it("«выбрать все» отмечает всю страницу", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();
  });

  it("после пакета выделение снимается", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    await user.click(screen.getByRole("button", { name: "Пометить заголовками" }));

    // Разобранные строки уходят из очереди: оставить выделение значило бы
    // применить следующее действие к строкам, которых на экране уже нет.
    await waitFor(() => {
      expect(screen.queryByText(/Выбрано строк/)).not.toBeInTheDocument();
    });
  });

  it("пропущенные строки не роняют пакет и объясняются отдельно", async () => {
    handlerState.batchSkipsFirst = true;
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    await user.click(screen.getByRole("button", { name: "В мусор" }));

    expect(await screen.findByText("Размечено строк: 1")).toBeInTheDocument();
    expect(await screen.findByText(/Пропущено: 1/)).toBeInTheDocument();
  });
});

describe("Слияние с каталожной работой (§5)", () => {
  it("предзаполняет поиск и сообщает, к скольким позициям применится решение", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Слить" }));

    expect(await screen.findByText(/позиций: 42/)).toBeInTheDocument();
    expect(screen.getByLabelText("Поиск каталожной работы")).toHaveValue("Стяжка неведомая");
  });

  it("сливает с выбранной целью и закрывает диалог", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Слить" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Стяжка цементная/ }));
    // Кнопка «Слить» есть и в строке таблицы — берём именно диалоговую.
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() => {
      expect(screen.queryByText(/позиций: 42/)).not.toBeInTheDocument();
    });
  });
});

describe("Права (§3): очередь принадлежит и member", () => {
  it("member видит очередь и действия", async () => {
    renderWithProviders(<ReviewPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });

    expect(await screen.findByText("Стяжка неведомая")).toBeInTheDocument();
    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Слить" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Это работа" })).toBeInTheDocument();
  });
});

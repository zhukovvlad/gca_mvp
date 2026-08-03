import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import StandardsPage from "./StandardsPage";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Нормативы» (§7.3)", () => {
  it("показывает ставки строками и различает открытый период", async () => {
    renderWithProviders(<StandardsPage />);

    expect(await screen.findByText("Кладка кирпичная")).toBeInTheDocument();
    // 1000.33 — точная ставка; форматирование не округляет.
    expect(screen.getByText(/1\s000,33/)).toBeInTheDocument();
    expect(screen.getByText("открыт")).toBeInTheDocument();
  });

  it("фильтр «действующие сегодня» отсекает закрытый период", async () => {
    const user = userEvent.setup();
    renderWithProviders(<StandardsPage />);
    await screen.findByText("Стяжка цементная");

    // Именно checkbox: Label с тем же текстом тоже находится по имени.
    await user.click(screen.getByRole("checkbox", { name: "Только действующие сегодня" }));

    // У «Стяжки» период закрыт 2025-01-01, а сегодня — позже: она уходит.
    await waitFor(() => {
      expect(screen.queryByText("Стяжка цементная")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("Кладка кирпичная")).toBeInTheDocument();
  });

  it("переутверждение предлагается только для открытого периода", async () => {
    renderWithProviders(<StandardsPage />);
    await screen.findByText("Кладка кирпичная");

    const open = screen.getByText("Кладка кирпичная").closest("tr") as HTMLElement;
    const closed = screen.getByText("Стяжка цементная").closest("tr") as HTMLElement;

    expect(within(open).getByRole("button", { name: /Переутвердить/ })).toBeInTheDocument();
    // Закрытый переутверждать нечего — сервер на это отвечает 422, и кнопки нет.
    expect(within(closed).queryByRole("button", { name: /Переутвердить/ })).toBeNull();
  });
});

describe("Диалог переутверждения (§4)", () => {
  async function openReapprove(user: ReturnType<typeof userEvent.setup>) {
    renderWithProviders(<StandardsPage />);
    await screen.findByText("Кладка кирпичная");
    const row = screen.getByText("Кладка кирпичная").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: /Переутвердить/ }));
    return screen.findByRole("dialog");
  }

  it("объясняет, что история не переписывается", async () => {
    const user = userEvent.setup();
    await openReapprove(user);

    expect(
      screen.getByText(/отклонения\s+смет до этой даты не изменятся/)
    ).toBeInTheDocument();
  });

  it("предзаполняет ставку как «прежняя × индекс» точно, без float", async () => {
    const user = userEvent.setup();
    await openReapprove(user);

    await user.type(screen.getByLabelText("Индекс инфляции"), "1.075");

    // 1000.33 × 1.075 = 1075.35475 — ровно, без хвоста двоичной дроби.
    expect(screen.getByLabelText("Новая ставка")).toHaveValue("1075.35475");
  });

  it("правка ставки вручную отменяет предзаполнение", async () => {
    const user = userEvent.setup();
    await openReapprove(user);

    await user.type(screen.getByLabelText("Новая ставка"), "1200");
    await user.type(screen.getByLabelText("Индекс инфляции"), "1.5");

    // Перетереть введённое значило бы отменить решение человека.
    expect(screen.getByLabelText("Новая ставка")).toHaveValue("1200");
  });

  it("переутверждает и сообщает, каким числом закрыт прежний период", async () => {
    const user = userEvent.setup();
    await openReapprove(user);

    await user.type(screen.getByLabelText("Действует с"), "2026-01-01");
    await user.type(screen.getByLabelText("Индекс инфляции"), "1.075");
    await user.click(screen.getByRole("button", { name: "Переутвердить" }));

    expect(
      await screen.findByText(/прежний период закрыт 2026-01-01/)
    ).toBeInTheDocument();
  });

  it("без ставки и без индекса переутвердить нельзя", async () => {
    const user = userEvent.setup();
    await openReapprove(user);

    await user.type(screen.getByLabelText("Действует с"), "2026-01-01");
    expect(screen.getByRole("button", { name: "Переутвердить" })).toBeDisabled();
  });
});

describe("Вкладка «Классы объектов» (решение §6.1)", () => {
  it("показывает классы со счётчиками использования", async () => {
    const user = userEvent.setup();
    renderWithProviders(<StandardsPage />);
    await user.click(screen.getByRole("tab", { name: "Классы объектов" }));

    expect(await screen.findByText("Жилые дома")).toBeInTheDocument();
    const row = screen.getByText("Жилые дома").closest("tr") as HTMLElement;
    expect(within(row).getByText("2")).toBeInTheDocument(); // договоров
  });

  it("создаёт класс", async () => {
    const user = userEvent.setup();
    renderWithProviders(<StandardsPage />);
    await user.click(screen.getByRole("tab", { name: "Классы объектов" }));

    await user.type(await screen.findByLabelText("Название класса"), "Социальные объекты");
    await user.click(screen.getByRole("button", { name: /Добавить класс/ }));

    expect(await screen.findByText("Класс объектов создан")).toBeInTheDocument();
  });

  it("дубликат названия объясняется человеку, а не молчит", async () => {
    const user = userEvent.setup();
    renderWithProviders(<StandardsPage />);
    await user.click(screen.getByRole("tab", { name: "Классы объектов" }));

    await user.type(await screen.findByLabelText("Название класса"), "Жилые дома");
    await user.click(screen.getByRole("button", { name: /Добавить класс/ }));

    expect(
      await screen.findByText("Класс объектов с таким названием уже есть.")
    ).toBeInTheDocument();
  });

  it("удаление занятого класса предупреждает, чем он занят", async () => {
    const user = userEvent.setup();
    renderWithProviders(<StandardsPage />);
    await user.click(screen.getByRole("tab", { name: "Классы объектов" }));
    await screen.findByText("Жилые дома");

    await user.click(screen.getByRole("button", { name: "Удалить класс Жилые дома" }));

    expect(await screen.findByText(/договоров — 2, нормативов — 1/)).toBeInTheDocument();
    expect(screen.getByText(/снимок, который держит историю отклонений/)).toBeInTheDocument();
  });
});

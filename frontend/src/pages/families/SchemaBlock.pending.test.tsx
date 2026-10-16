import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaBlock } from "./SchemaBlock";
import { VariantsTable } from "./VariantsTable";

/** Состояния ожидания блока «Схема и варианты»: кнопки действий на время запроса. */

const LATER = { timeout: 8000 };

function renderBlock() {
  const family = handlerState.workFamilies.find((f) => f.id === SCHEMA_FAMILY_ID);
  if (!family) throw new Error("нет семьи со схемой");
  return renderWithProviders(<SchemaBlock family={family} />);
}

async function shown() {
  await screen.findByText("Версия схемы 2", {}, LATER);
}

describe("SchemaBlock и VariantsTable — первая загрузка", () => {
  it("схема: до ответа заглушки, ответ пришёл — версия схемы без заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/families/:id/schema");
    renderBlock();
    // Таблица вариантов грузится сама и со своей заглушкой: ждём её, чтобы осталась только заглушка схемы.
    await screen.findByRole("table", { name: "Варианты семьи" }, LATER);

    expect(screen.getByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Версия схемы 2")).not.toBeInTheDocument();

    hold.release();
    await shown();
    await waitFor(() => expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument(), LATER);
  });

  it("варианты: до ответа заглушки, ответ пришёл — таблица без заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/families/:id/variants");
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Варианты семьи" })).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByRole("table", { name: "Варианты семьи" }, LATER)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });
});

describe("SchemaBlock — ожидание действий", () => {
  it("«Отменить пересборку»: на время запроса кнопка занята, ответ пришёл — запрос один", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    const hold = holdRequests("post", "/api/v1/semantic/families/:id/schema/cancel");
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Отменить пересборку" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Отменить пересборку" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Отменить пересборку" })).toBeDisabled();

    hold.release();
    expect(await screen.findByText("Пересборка схемы отменена.", {}, LATER)).toBeInTheDocument();
    expect(hold.seen.count).toBe(1);
  });

  it("«Сохранить схему» в окне: на время запроса кнопка занята, ответ пришёл — окно закрыто", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();
    await user.click(screen.getByRole("button", { name: "Править схему…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.type(within(dialog).getByLabelText("Значения параметра 2, по одному в строке"), "{Enter}1 мм");
    const hold = holdRequests("patch", "/api/v1/semantic/families/:id/schema");

    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Сохранить схему" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Сохранить схему" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
  });

  it("«Слить» в окне слияния значений: на время запроса кнопка занята", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();
    await user.click(screen.getByRole("button", { name: "Слить значения…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.click(within(dialog).getByRole("combobox", { name: "Параметр" }));
    await user.click(await screen.findByRole("option", { name: "Материал" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Источник" }));
    await user.click(await screen.findByRole("option", { name: "профнастил" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    const hold = holdRequests("post", "/api/v1/semantic/families/:id/schema/values/merge");

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Слить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();

    hold.release();
    expect(await screen.findByText("Значения слиты.", {}, LATER)).toBeInTheDocument();
  });
});

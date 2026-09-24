import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Вкладка «Семьи» экрана `/families` (спека 2026-09-22-catalog-families-design.md
 * §2.7, §2.10). Экран целиком под `RequireAdmin` (проверяется в
 * `FamiliesPage.test.tsx`, тем же входом, что и `/standards`), здесь — только
 * содержимое вкладки, по образцу `InflationSeriesTab.test.tsx`.
 */

async function renderTab() {
  renderWithProviders(<FamiliesTab />);
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
}

function dataRows() {
  const rowgroups = screen.getAllByRole("rowgroup");
  // rowgroups[0] — thead, rowgroups[1] — tbody (тот же порядок, что рендерит `Table`).
  return within(rowgroups[1]).getAllByRole("row");
}

describe("FamiliesTab", () => {
  it("после seed показывает 42 черновика — штатное первое состояние", async () => {
    await renderTab();
    // Фильтр статуса открывается на draft: в фикстуре 44 семьи, из них 42 —
    // draft (id 43 — active, id 44 — archived), и счёт «42» обязан быть
    // результатом ФИЛЬТРА, а не длины массива фикстуры (докстринг
    // `initialWorkFamilies`).
    expect(dataRows()).toHaveLength(42);
  });

  it("кнопка активации недоступна без определения, а с определением доступна", async () => {
    await renderTab();
    // id=2 ("Устройство покрытий полов") — черновик без определения.
    const disabledButton = screen.getByRole("button", {
      name: "Активировать семью Устройство покрытий полов",
    });
    expect(disabledButton).toBeDisabled();

    // id=1 ("Семья работ №1") несёт определение — кнопка активна.
    const enabledButton = screen.getByRole("button", {
      name: "Активировать семью Семья работ №1",
    });
    expect(enabledButton).not.toBeDisabled();
  });

  it("правка единицы недоступна при привязках, и подпись называет их число", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=1 ("Семья работ №1") несёт две привязки (`context_count: 2` в фикстуре).
    const row = screen.getByText("Семья работ №1").closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Правка" }));
    expect(await screen.findByLabelText("Единица")).toBeDisabled();
    expect(
      screen.getByText("Единица недоступна: привязано контекстов — 2")
    ).toBeInTheDocument();
  });

  it("у семьи без привязок единица редактируется, и подписи о недоступности нет", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=3 ("Семья работ №3") — без привязок (`context_count: 0`).
    const row = screen.getByText("Семья работ №3").closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Правка" }));
    expect(await screen.findByLabelText("Единица")).not.toBeDisabled();
    expect(screen.queryByText(/Единица недоступна/)).not.toBeInTheDocument();
  });

  it("путь «дописать определение → активировать» проходит на экране", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=2 ("Устройство покрытий полов") — черновик без определения и без привязок.
    const activateName = "Активировать семью Устройство покрытий полов";
    expect(screen.getByRole("button", { name: activateName })).toBeDisabled();

    const row = screen.getByText("Устройство покрытий полов").closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Правка" }));
    await user.type(await screen.findByLabelText("Определение"), "Устройство покрытий полов из линолеума.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(handlerState.lastUpdateFamilyRequest).toMatchObject({
        id: 2,
        body: { definition: "Устройство покрытий полов из линолеума." },
      })
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: activateName })).not.toBeDisabled()
    );
    await user.click(screen.getByRole("button", { name: activateName }));

    // Активированная семья уходит из фильтра draft: 42 → 41.
    await waitFor(() => expect(dataRows()).toHaveLength(41));
    expect(screen.queryByText("Устройство покрытий полов")).not.toBeInTheDocument();
  });

  it("правка семьи с привязками не шлёт unit_name — иначе сервер отказал бы и определение не сохранилось", async () => {
    const user = userEvent.setup();
    await renderTab();

    const row = screen.getByText("Семья работ №1").closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Правка" }));
    const definition = await screen.findByLabelText("Определение");
    await user.clear(definition);
    await user.type(definition, "Новое определение.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(handlerState.lastUpdateFamilyRequest).not.toBeNull());
    expect(handlerState.lastUpdateFamilyRequest!.id).toBe(1);
    expect(handlerState.lastUpdateFamilyRequest!.body).not.toHaveProperty("unit_name");
    expect(handlerState.lastUpdateFamilyRequest!.body).toMatchObject({ definition: "Новое определение." });
  });

  it("правка семьи без привязок шлёт unit_name из поля единицы", async () => {
    const user = userEvent.setup();
    await renderTab();

    const row = screen.getByText("Семья работ №3").closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Правка" }));
    const unit = await screen.findByLabelText("Единица");
    await user.clear(unit);
    await user.type(unit, "м3");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(handlerState.lastUpdateFamilyRequest).toMatchObject({ id: 3, body: { unit_name: "м3" } })
    );
  });

  it("создание семьи доходит до сервера и появляется в списке", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: /Новая семья/ }));
    await user.type(screen.getByLabelText("Название (обязательно)"), "Новая семья работ");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(handlerState.lastCreateFamilyRequest).toMatchObject({ title: "Новая семья работ" })
    );
    expect(await screen.findByText("Новая семья работ")).toBeInTheDocument();
  });

  it("фильтр по единице сужает список семей (спека §2.10)", async () => {
    const user = userEvent.setup();
    await renderTab();

    // Статус "Любой" — иначе id 43 (active, единица «Куб. метр») исключён
    // фильтром статуса и не докажет, что сужает именно ЕДИНИЦА.
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByText("Любой статус"));
    await waitFor(() => expect(screen.getByText("Кровельные работы")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByText("Куб. метр"));

    await waitFor(() => {
      expect(screen.getByText("Кровельные работы")).toBeInTheDocument();
      expect(screen.queryByText("Семья работ №3")).not.toBeInTheDocument();
    });
  });
});

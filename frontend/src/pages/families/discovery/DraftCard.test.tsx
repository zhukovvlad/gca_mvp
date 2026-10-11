import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { draftFixture, draftsViewFixture, handlerState } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { DraftView, FamilyCategory } from "@/types/domain";

import { DraftCard } from "./DraftCard";

/**
 * Черновик новой семьи: отметка, «похоже на…», категория, «Править…», «Слить с…», «Отбросить».
 * Единица — M3 (id 3): у неё активна семья «Кровельные работы» (id 43), у единицы M2 — нет.
 */

const CATEGORIES: FamilyCategory[] = [
  { id: 1, title: "Работа", definition: "Работа.", seed_key: "work", family_count: 3 },
  { id: 3, title: "Затраты и услуги", definition: "Затраты.", seed_key: null, family_count: 1 },
];

const SIBLING = draftFixture({ id: 302, ordinal: 2, title: "Кондиционирование", rows: 43 });

function seed(draft: DraftView) {
  handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draft, SIBLING] });
}

function renderCard(draft: DraftView, props: Partial<Parameters<typeof DraftCard>[0]> = {}) {
  seed(draft);
  const onCheckedChange = vi.fn();
  renderWithProviders(
    <DraftCard
      draft={draft}
      unitId={3}
      checked
      onCheckedChange={onCheckedChange}
      categories={CATEGORIES}
      otherDrafts={[SIBLING]}
      {...props}
    />
  );
  return { onCheckedChange };
}

describe("DraftCard", () => {
  it("имя, определение, примеры, число строк и «+ N» остальных", () => {
    renderCard(draftFixture());

    expect(screen.getByText("Вентиляция общеобменная")).toBeInTheDocument();
    expect(screen.getByText("Система общеобменной вентиляции здания целиком.")).toBeInTheDocument();
    expect(screen.getByText("Вентиляция — приточные установки")).toBeInTheDocument();
    expect(screen.getByText("+ 64")).toBeInTheDocument();
    expect(screen.getByText("66")).toBeInTheDocument();
    expect(screen.getByText(/строк/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("Работа");
    expect(screen.queryByText(/похоже на/)).not.toBeInTheDocument();
  });

  it("«похоже на активную «…»»: метка и слияние с этой семьёй в одно нажатие", async () => {
    const user = userEvent.setup();
    renderCard(
      draftFixture({
        similar_family_id: 43,
        similar_family_title: "Кровельные работы",
        similar_family_status: "active",
      })
    );

    expect(screen.getByText("похоже на активную «Кровельные работы»")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Слить с «Кровельные работы»" }));

    await waitFor(() =>
      expect(handlerState.discovery.draftRequests).toEqual([
        { action: "merge", draftId: 301, body: { target_family_id: 43 } },
      ])
    );
  });

  it("похожая семья не активна: метка называет статус, кнопки слияния с ней нет", () => {
    renderCard(
      draftFixture({
        similar_family_id: 44,
        similar_family_title: "Демонтажные работы (снята)",
        similar_family_status: "archived",
      })
    );

    expect(
      screen.getByText("похоже на активную «Демонтажные работы (снята)» (в архиве)")
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Слить с «Демонтажные работы/ })
    ).not.toBeInTheDocument();
  });

  it("отметка сообщает наверх новое состояние", async () => {
    const user = userEvent.setup();
    const { onCheckedChange } = renderCard(draftFixture());

    await user.click(screen.getByRole("checkbox", { name: "Отметить черновик «Вентиляция общеобменная»" }));

    expect(onCheckedChange).toHaveBeenCalledWith(false);
  });

  it("выбор категории шлёт PATCH только с категорией; без категории подсказка «Выбрать категорию»", async () => {
    const user = userEvent.setup();
    renderCard(draftFixture({ family_category_id: null, family_category_title: null }));

    expect(screen.getByRole("combobox")).toHaveTextContent("Выбрать категорию");
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Затраты и услуги" }));

    await waitFor(() =>
      expect(handlerState.discovery.draftRequests).toEqual([
        { action: "edit", draftId: 301, body: { family_category_id: 3 } },
      ])
    );
    expect(handlerState.discovery.drafts["3"]?.drafts[0].family_category_title).toBe("Затраты и услуги");
  });

  describe("«Править…»", () => {
    it("уходят только изменённые поля; без изменений и с пустым именем сохранить нельзя", async () => {
      const user = userEvent.setup();
      renderCard(draftFixture());

      await user.click(screen.getByRole("button", { name: "Править…" }));
      const dialog = await screen.findByRole("dialog");
      await waitForDialogFocus();
      const save = within(dialog).getByRole("button", { name: "Сохранить" });
      expect(save).toBeDisabled();

      const title = within(dialog).getByLabelText("Имя");
      await user.clear(title);
      expect(save).toBeDisabled();
      await user.type(title, "Вентиляция приточно-вытяжная");
      expect(save).toBeEnabled();
      await user.click(save);

      await waitFor(() =>
        expect(handlerState.discovery.draftRequests).toEqual([
          { action: "edit", draftId: 301, body: { title: "Вентиляция приточно-вытяжная" } },
        ])
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("определение и категория тоже правятся одним запросом", async () => {
      const user = userEvent.setup();
      renderCard(draftFixture());

      await user.click(screen.getByRole("button", { name: "Править…" }));
      const dialog = await screen.findByRole("dialog");
      await waitForDialogFocus();
      const definition = within(dialog).getByLabelText("Определение");
      await user.clear(definition);
      await user.type(definition, "Новое определение.");
      await user.click(within(dialog).getByRole("combobox", { name: "Категория" }));
      await user.click(await screen.findByRole("option", { name: "Затраты и услуги" }));
      await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

      await waitFor(() =>
        expect(handlerState.discovery.draftRequests).toEqual([
          {
            action: "edit",
            draftId: 301,
            body: { definition: "Новое определение.", family_category_id: 3 },
          },
        ])
      );
    });
  });

  describe("«Слить с…»", () => {
    it("цели: другой открытый черновик и активные семьи ТОЙ ЖЕ единицы, без чужих единиц и самого источника", async () => {
      const user = userEvent.setup();
      // Активная семья ДРУГОЙ единицы (M2, id 5): целью слияния быть не может.
      const roofing = handlerState.workFamilies.find((f) => f.id === 43)!;
      handlerState.workFamilies.push({
        ...roofing,
        id: 77,
        title: "Штукатурка стен",
        unit_id: 5,
        unit_code: "M2",
        unit_symbol: "м²",
      });
      renderCard(draftFixture());

      await user.click(screen.getByRole("button", { name: "Слить с…" }));
      await screen.findByRole("dialog");
      await waitForDialogFocus();
      await user.click(screen.getByRole("combobox", { name: "Цель слияния" }));

      expect(await screen.findByRole("option", { name: "Черновик «Кондиционирование»" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "Активная семья «Кровельные работы»" })).toBeInTheDocument();
      // Сам источник и активная семья другой единицы целью не бывают.
      expect(screen.queryByRole("option", { name: "Черновик «Вентиляция общеобменная»" })).not.toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "Активная семья «Штукатурка стен»" })).not.toBeInTheDocument();
    });

    it("слияние с черновиком шлёт target_draft_id, с семьёй — target_family_id", async () => {
      const user = userEvent.setup();
      renderCard(draftFixture());

      await user.click(screen.getByRole("button", { name: "Слить с…" }));
      const dialog = await screen.findByRole("dialog");
      await waitForDialogFocus();
      expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
      await user.click(within(dialog).getByRole("combobox", { name: "Цель слияния" }));
      await user.click(await screen.findByRole("option", { name: "Черновик «Кондиционирование»" }));
      await user.click(within(dialog).getByRole("button", { name: "Слить" }));

      await waitFor(() =>
        expect(handlerState.discovery.draftRequests).toEqual([
          { action: "merge", draftId: 301, body: { target_draft_id: 302 } },
        ])
      );
    });

    it("слияние с активной семьёй шлёт target_family_id", async () => {
      const user = userEvent.setup();
      renderCard(draftFixture());

      await user.click(screen.getByRole("button", { name: "Слить с…" }));
      const dialog = await screen.findByRole("dialog");
      await waitForDialogFocus();
      await user.click(within(dialog).getByRole("combobox", { name: "Цель слияния" }));
      await user.click(await screen.findByRole("option", { name: "Активная семья «Кровельные работы»" }));
      await user.click(within(dialog).getByRole("button", { name: "Слить" }));

      await waitFor(() =>
        expect(handlerState.discovery.draftRequests).toEqual([
          { action: "merge", draftId: 301, body: { target_family_id: 43 } },
        ])
      );
    });
  });

  it("«Отбросить» шлёт discard; отказ сервера печатается подписью по коду с именем черновика", async () => {
    const user = userEvent.setup();
    handlerState.discovery.refusal = { action: "discard", code: "draft_not_open", status: 409 };
    renderCard(draftFixture());

    await user.click(screen.getByRole("button", { name: "Отбросить" }));

    expect(
      await screen.findByText(
        "Черновик «Вентиляция общеобменная» уже активирован, слит, отброшен или устарел. Слитый и отброшенный можно вернуть."
      )
    ).toBeInTheDocument();
    expect(handlerState.discovery.draftRequests).toEqual([
      { action: "discard", draftId: 301, body: null },
    ]);
  });

  it("подпись отказа активации стоит у черновика", () => {
    renderCard(draftFixture(), { refusal: "У черновика «Вентиляция общеобменная» не выбрана категория." });

    expect(screen.getByTestId("draft-refusal")).toHaveTextContent(
      "У черновика «Вентиляция общеобменная» не выбрана категория."
    );
  });
});

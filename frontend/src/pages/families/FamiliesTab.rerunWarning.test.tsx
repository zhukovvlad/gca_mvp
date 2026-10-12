import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Предупреждение «идёт перезапрос» в окнах действий над семьёй, меняющих список семей в
 * теле запроса предложения: правка названия или определения, архивирование, слияние.
 * Активная семья фикстуры — «Кровельные работы» (id 43, единица м³).
 */

const WARNING_7 =
  "В единице «м³» идёт перезапрос: 7 заданий предложений ещё не выполнены. Это изменение отменит их, а уже полученные, но не разобранные предложения скроются — единицу придётся перезапросить заново.";

function setBusy(familyId: number, busy: number) {
  handlerState.workFamilies.find((f) => f.id === familyId)!.unit_busy_jobs = busy;
}

async function openActivePanel() {
  const user = userEvent.setup();
  renderWithProviders(<FamiliesTab />);
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
  await user.click(screen.getByRole("combobox", { name: "Статус" }));
  await user.click(await screen.findByRole("option", { name: "активна" }));
  await user.click(await screen.findByText("Кровельные работы"));
  await screen.findByLabelText("Определение");
  return user;
}

describe("FamiliesTab — предупреждение о сбросе перезапроса", () => {
  afterEach(() => {
    localStorage.clear();
  });

  describe("правка названия и определения", () => {
    it("правка названия при идущем перезапросе предупреждает числом заданий; без правки предупреждения нет", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();
      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(await screen.findByText(WARNING_7)).toBeInTheDocument();
    });

    it("правка определения при идущем перезапросе предупреждает; возврат прежнего текста снимает предупреждение", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.type(screen.getByLabelText("Определение"), "!");
      expect(await screen.findByText(WARNING_7)).toBeInTheDocument();
      await user.type(screen.getByLabelText("Определение"), "{Backspace}");

      await waitFor(() => expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument());
    });

    it("правка названия без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    it("правка одной лишь категории предупреждения не даёт (категории нет в теле запроса предложения)", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("combobox", { name: "Категория" }));
      await user.click(await screen.findByRole("option", { name: "Проектирование" }));

      await waitFor(() =>
        expect(handlerState.lastUpdateFamilyRequest).toEqual({
          id: 43,
          body: { family_category_id: 4 },
        })
      );
      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    it("правка единицы активной семьи без привязок при идущем перезапросе предупреждает", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();
      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("Единица"), "2");

      expect(await screen.findByText(WARNING_7)).toBeInTheDocument();
    });

    it("правка единицы без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.type(screen.getByLabelText("Единица"), "2");

      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    it("у черновика правка названия предупреждения не даёт: его нет в теле запроса предложения", async () => {
      setBusy(3, 7);
      const user = userEvent.setup();
      renderWithProviders(<FamiliesTab />);
      await user.click(await screen.findByText("Семья работ №3"));
      await screen.findByLabelText("Определение");

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });
  });

  describe("архивирование", () => {
    it("окно архивирования при идущем перезапросе предупреждает числом заданий и не блокирует действие", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "В архив: семья Кровельные работы" }));

      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByText(WARNING_7)).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Архивировать" })).toBeEnabled();
    });

    it("окно архивирования без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "В архив: семья Кровельные работы" }));

      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });
  });

  describe("слияние", () => {
    it("окно слияния при идущем перезапросе предупреждает числом заданий и не блокирует действие", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "Слить" }));

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(WARNING_7)).toBeInTheDocument();
      expect(within(dialog).getByRole("combobox", { name: "Целевая семья" })).toBeEnabled();
    });

    it("окно слияния без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "Слить" }));

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });
  });
});

describe("FamiliesTab — поиск целевой семьи при слиянии", () => {
  afterEach(() => {
    localStorage.clear();
  });

  function activeFamily(id: number, title: string) {
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({ ...base, id, title });
  }

  async function openMerge() {
    const user = await openActivePanel();
    await user.click(screen.getByRole("button", { name: "Слить" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Целевая семья" }));
    return { user, dialog };
  }

  it("поиск сужает список по подстроке без учёта регистра; выбор из найденного шлёт тот же запрос слияния", async () => {
    activeFamily(61, "Устройство стяжки пола");
    activeFamily(62, "Окраска стен");
    const { user, dialog } = await openMerge();
    expect(await screen.findByRole("option", { name: "Окраска стен" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Найти семью…"), "ЯЖКИ П");

    expect(await screen.findByRole("option", { name: "Устройство стяжки пола" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Окраска стен" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "Устройство стяжки пола" }));
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() =>
      expect(handlerState.lastMergeFamiliesRequest).toEqual({ id: 43, targetFamilyId: 61 })
    );
  });

  it("варианты приходят вразнобой — показаны по алфавиту", async () => {
    activeFamily(62, "Штукатурка");
    activeFamily(63, "Армирование");
    await openMerge();

    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Армирование",
      "Штукатурка",
    ]);
  });

  it("пустой результат поиска — «Ничего не найдено»; сама сливаемая семья в целях не находится", async () => {
    activeFamily(62, "Окраска стен");
    const { user } = await openMerge();

    await user.type(screen.getByPlaceholderText("Найти семью…"), "кровельные");

    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });
});

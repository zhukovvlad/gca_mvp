import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState, schemaParameter, schemaValue } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { MergeValuesDialog } from "./MergeValuesDialog";

/**
 * «Слить значения…» (спека `2026-10-02-catalog-variants-design.md` §2.8, §2.12):
 * источник и цель — значения одного параметра. Отказы сервера — подписью по коду.
 */

const LATER = { timeout: 8000 };

function renderDialog() {
  const onOpenChange = vi.fn();
  renderWithProviders(
    <MergeValuesDialog
      schema={handlerState.familySchemas[SCHEMA_FAMILY_ID]}
      open
      onOpenChange={onOpenChange}
    />
  );
  return onOpenChange;
}

async function opened() {
  const dialog = await screen.findByRole("dialog");
  await waitForDialogFocus();
  return dialog;
}

async function pick(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, field: string, option: string) {
  await user.click(within(dialog).getByRole("combobox", { name: field }));
  await user.click(await screen.findByRole("option", { name: option }));
}

async function chooseMaterialMerge(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await pick(user, dialog, "Параметр", "Материал");
  await pick(user, dialog, "Источник", "металло-черепица");
  await pick(user, dialog, "Цель", "металлочерепица");
}

describe("MergeValuesDialog: выбор", () => {
  it("источник и цель недоступны, пока не выбран параметр, и слить нельзя", async () => {
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toBeDisabled();
    expect(within(dialog).getByRole("combobox", { name: "Цель" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
  });

  it("источник и цель предлагают значения только выбранного параметра", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await pick(user, dialog, "Параметр", "Толщина");
    await user.click(within(dialog).getByRole("combobox", { name: "Источник" }));

    expect(await screen.findByRole("option", { name: "0,5 мм" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "0,7 мм" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "профнастил" })).not.toBeInTheDocument();
  });

  it("цель не предлагает выбранный источник", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await pick(user, dialog, "Параметр", "Материал");
    await pick(user, dialog, "Источник", "профнастил");
    await user.click(within(dialog).getByRole("combobox", { name: "Цель" }));

    expect(await screen.findByRole("option", { name: "металлочерепица" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "профнастил" })).not.toBeInTheDocument();
  });

  it("параметры с единственным живым значением в выбор не входят", async () => {
    const parameters = handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters;
    parameters[1].values[1].merged_into_id = 1004;
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("combobox", { name: "Параметр" }));

    expect(await screen.findByRole("option", { name: "Материал" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Толщина" })).not.toBeInTheDocument();
  });

  it("слитое значение не предлагается ни источником, ни целью", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].values[2].merged_into_id = 1002;
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await pick(user, dialog, "Параметр", "Материал");
    await user.click(within(dialog).getByRole("combobox", { name: "Источник" }));

    expect(await screen.findByRole("option", { name: "профнастил" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "металло-черепица" })).not.toBeInTheDocument();
  });

  it("смена параметра сбрасывает источник и цель", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await chooseMaterialMerge(user, dialog);
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeEnabled();
    await pick(user, dialog, "Параметр", "Толщина");

    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
  });

  it.each([
    ["Источник", "0,5 мм"],
    ["Цель", "0,7 мм"],
  ])("после смены параметра выбора одного поля (%s) мало: прежний выбор другого поля не остаётся", async (field, option) => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await chooseMaterialMerge(user, dialog);
    await pick(user, dialog, "Параметр", "Толщина");
    await pick(user, dialog, field, option);

    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
  });

  it("источником выбрана прежняя цель — цель сбрасывается, слить нельзя до нового выбора", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await chooseMaterialMerge(user, dialog);
    await pick(user, dialog, "Источник", "металлочерепица");

    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
    await pick(user, dialog, "Цель", "профнастил");
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeEnabled();
  });
});

describe("MergeValuesDialog: слияние", () => {
  it("шлёт параметр, источник и цель; диалог закрывается", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await chooseMaterialMerge(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(handlerState.schemaRequests).toEqual([
      {
        action: "merge",
        familyId: SCHEMA_FAMILY_ID,
        body: { parameter_id: 101, source_value_id: 1003, target_value_id: 1002 },
      },
    ]);
  });
});

describe("MergeValuesDialog: отказы сервера — подпись по коду", () => {
  // Коды маршрута `values/merge` (`merge_parameter_values`); `merge_schema_building` —
  // отказ слияния СЕМЕЙ, этот маршрут его не даёт.
  const REFUSALS: Array<[string, number, string]> = [
    ["merge_values_other_parameter", 422, "Источник и цель должны быть значениями одного параметра."],
    ["merge_source_merged", 409, "Источник уже слит с другим значением."],
    ["merge_value_cycle", 422, "Это слияние замкнуло бы цепочку синонимов: выберите другую цель."],
    ["value_not_found", 404, "Значение не найдено: обновите экран."],
    ["parameter_not_found", 404, "Параметр не найден: обновите экран."],
  ];

  it("экран устарел: источник уже слит на сервере — обработчик отказывает, как сервис, видна подпись", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    await chooseMaterialMerge(user, dialog);
    const shownSchema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
      ...shownSchema,
      parameters: shownSchema.parameters.map((p, i) =>
        i !== 0 ? p : { ...p, values: p.values.map((v) => (v.id === 1003 ? { ...v, merged_into_id: 1001 } : v)) }
      ),
    };

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent("Источник уже слит с другим значением.");
    expect(document.body.textContent).not.toContain("merge_source_merged");
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it.each(REFUSALS)("%s (%i): диалог остаётся открытым, видна подпись, кода и текста сервера нет", async (code, status, label) => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    await chooseMaterialMerge(user, dialog);
    handlerState.schemaRefusal = { action: "merge", code, status };

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(label);
    expect(document.body.textContent).not.toContain(code);
    expect(document.body.textContent).not.toContain("server text must not reach the screen");
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});

describe("MergeValuesDialog: подстановка и диапазоны (спека 3в §2.11)", () => {
  it("открытое из «⋯» плашки окно несёт выбранные параметр и источник; цель выбирает человек", async () => {
    renderWithProviders(
      <MergeValuesDialog
        schema={handlerState.familySchemas[SCHEMA_FAMILY_ID]}
        open
        onOpenChange={vi.fn()}
        initial={{ parameterId: 101, sourceId: 1003 }}
      />
    );
    const dialog = await opened();

    expect(within(dialog).getByRole("combobox", { name: "Параметр" })).toHaveTextContent("Материал");
    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toHaveTextContent("металло-черепица");
    expect(within(dialog).getByRole("combobox", { name: "Цель" })).not.toHaveTextContent("металлочерепица");
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
  });

  it("с подставленными параметром и источником достаточно выбрать цель: запрос несёт все три id", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <MergeValuesDialog
        schema={handlerState.familySchemas[SCHEMA_FAMILY_ID]}
        open
        onOpenChange={vi.fn()}
        initial={{ parameterId: 101, sourceId: 1003 }}
      />
    );
    const dialog = await opened();

    await pick(user, dialog, "Цель", "металлочерепица");
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(handlerState.schemaRequests[0].body).toEqual({
      parameter_id: 101,
      source_value_id: 1003,
      target_value_id: 1002,
    });
  });

  it("без подстановки поля пусты, как раньше", async () => {
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toBeDisabled();
  });

  it("параметр с диапазонами в выборе не предлагается: их значения строит система", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters.push(
      schemaParameter(104, 3, "Диапазон, мм", [schemaValue(1401, "до 2", "manual"), schemaValue(1402, "2 и более", "manual")], {
        value_kind: "range",
        closed: true,
        range: { start: "2", stop: "2", step: "1" },
      })
    );
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("combobox", { name: "Параметр" }));

    expect(await screen.findByRole("option", { name: "Материал" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Диапазон, мм" })).not.toBeInTheDocument();
  });

  it("отказ merge_value_retired — подпись, кода нет", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    await chooseMaterialMerge(user, dialog);
    handlerState.schemaRefusal = { action: "merge", code: "merge_value_retired", status: 409 };

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Значение выведено из списка: его нельзя слить и нельзя выбрать целью слияния."
    );
    expect(document.body.textContent).not.toContain("merge_value_retired");
  });
});

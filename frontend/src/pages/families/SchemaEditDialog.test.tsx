import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaEditDialog } from "./SchemaEditDialog";

/**
 * «Править схему…» (спека `2026-10-02-catalog-variants-design.md` §2.8, §2.12):
 * имена и списки значений; смысловое переименование и удаление значения сервер
 * отказывает — экран показывает подпись по коду ответа.
 */

const LATER = { timeout: 8000 };

function renderDialog() {
  const onOpenChange = vi.fn();
  renderWithProviders(
    <SchemaEditDialog
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

const VALUES_1 = "Значения параметра 1, по одному в строке";
const VALUES_2 = "Значения параметра 2, по одному в строке";

describe("SchemaEditDialog: форма", () => {
  it("показывает имя и значения каждого параметра, по значению в строке", async () => {
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByLabelText("Параметр 1")).toHaveValue("Материал");
    expect(within(dialog).getByLabelText(VALUES_1)).toHaveValue("профнастил\nметаллочерепица\nметалло-черепица");
    expect(within(dialog).getByLabelText("Параметр 2")).toHaveValue("Толщина");
    expect(within(dialog).getByLabelText(VALUES_2)).toHaveValue("0,5 мм\n0,7 мм");
  });

  it("слитые значения в список правки не входят: они синонимы цели", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].values[2].merged_into_id = 1002;
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByLabelText(VALUES_1)).toHaveValue("профнастил\nметаллочерепица");
  });

  it("«Добавить параметр» даёт третий параметр и пропадает на трёх", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByLabelText("Параметр 3")).toHaveValue("");
    expect(within(dialog).queryByRole("button", { name: "Добавить параметр" })).not.toBeInTheDocument();
  });

  it("на схеме из одного параметра кнопка «Добавить параметр» есть", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters = [
      handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0],
    ];
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeInTheDocument();
  });
});

describe("SchemaEditDialog: сохранение", () => {
  it("добавленное значение уходит полным списком параметра, диалог закрывается", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.type(within(dialog).getByLabelText(VALUES_2), "{Enter}1 мм");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(handlerState.schemaRequests).toEqual([
      {
        action: "update",
        familyId: SCHEMA_FAMILY_ID,
        body: {
          parameters: [
            { ordinal: 1, name: "Материал", values: ["профнастил", "металлочерепица", "металло-черепица"] },
            { ordinal: 2, name: "Толщина", values: ["0,5 мм", "0,7 мм", "1 мм"] },
          ],
        },
      },
    ]);
  });

  it("новый параметр уходит с очередным номером; пустые строки значений отбрасываются", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.type(within(dialog).getByLabelText("Параметр 3"), "Цвет");
    await user.type(within(dialog).getByLabelText("Значения параметра 3, по одному в строке"), "красный{Enter}{Enter}зелёный");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const parameters = (handlerState.schemaRequests[0].body as { parameters: unknown[] }).parameters;
    expect(parameters[2]).toEqual({ ordinal: 3, name: "Цвет", values: ["красный", "зелёный"] });
  });

  it("поправленное написание имени уходит как есть и принимается; решение о смысле остаётся за сервером", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.clear(within(dialog).getByLabelText("Параметр 2"));
    await user.type(within(dialog).getByLabelText("Параметр 2"), "ТОЛЩИНА");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    const parameters = (handlerState.schemaRequests[0].body as { parameters: Array<{ name: string }> }).parameters;
    expect(parameters[1].name).toBe("ТОЛЩИНА");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("строка значений из одних пробелов отбрасывается, а не уходит пустым значением", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.type(within(dialog).getByLabelText(VALUES_2), "{Enter}   {Enter}1 мм");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    const parameters = (handlerState.schemaRequests[0].body as { parameters: Array<{ values: string[] }> }).parameters;
    expect(parameters[1].values).toEqual(["0,5 мм", "0,7 мм", "1 мм"]);
  });
});

describe("SchemaEditDialog: отказ сервера по существу правки (обработчик отказывает, как сервис)", () => {
  it("смысловое переименование параметра — подпись отказа, кода нет, диалог открыт", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.clear(within(dialog).getByLabelText("Параметр 2"));
    await user.type(within(dialog).getByLabelText("Параметр 2"), "Толщина листа");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Параметр нельзя переименовать по смыслу: это новый параметр. Допустима только правка написания."
    );
    expect(document.body.textContent).not.toContain("schema_parameter_renamed");
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[1].name).toBe("Толщина");
  });

  it("стёртое значение — подпись «только слить», схема не меняется", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.clear(within(dialog).getByLabelText(VALUES_2));
    await user.type(within(dialog).getByLabelText(VALUES_2), "0,5 мм");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Значение нельзя удалить — только слить с другим."
    );
    expect(document.body.textContent).not.toContain("schema_value_removed");
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].version).toBe(2);
  });
});

describe("SchemaEditDialog: удаление параметра", () => {
  async function submittedParameters() {
    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    return (handlerState.schemaRequests[0].body as { parameters: Array<{ ordinal: number; name: string }> })
      .parameters;
  }

  it("без удалений тело несёт оба номера как есть", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect((await submittedParameters()).map((p) => p.ordinal)).toEqual([1, 2]);
  });

  it("убранный параметр в тело не входит, остальные сохраняют СВОИ номера без пересчёта", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 1" }));
    expect(within(dialog).queryByLabelText("Параметр 1")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    const parameters = await submittedParameters();
    expect(parameters).toHaveLength(1);
    expect(parameters[0]).toMatchObject({ ordinal: 2, name: "Толщина" });
  });

  const HINT = /сохраните удаление, затем добавьте\s+параметр отдельной правкой/;

  /** Схема из трёх параметров: к фикстуре из двух добавлен «Цвет» с номером 3. */
  function threeParameters() {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters.push({
      id: 103,
      ordinal: 3,
      name: "Цвет",
      values: [{ id: 1006, value: "красный", origin: "schema", merged_into_id: null }],
    });
  }

  it("на схеме с номерами {1, 2} «Добавить параметр» после удаления 1 берёт 3, а не убранный 1 — и сервер правку принимает", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 1" }));
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    expect(within(dialog).queryByLabelText("Параметр 1")).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Параметр 3"), "Цвет");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    const parameters = await submittedParameters();
    expect(parameters.map((p) => p.ordinal).sort()).toEqual([2, 3]);
    expect(parameters.find((p) => p.ordinal === 3)?.name).toBe("Цвет");
    // Исход, ради которого правило введено: обработчик (как `_plan_edit`) не читает правку
    // как смысловое переименование — окно закрывается, подписи отказа нет.
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters.map((p) => p.ordinal)).toEqual([2, 3]);
  });

  it("на схеме из одного параметра два добавления подряд берут разные номера", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters = schema.parameters.filter((p) => p.ordinal === 1);
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByLabelText("Параметр 2")).toHaveValue("");
    expect(within(dialog).getByLabelText("Параметр 3")).toHaveValue("");
  });

  it("на схеме из трёх параметров удаление и добавление в одной правке недоступно: кнопка отключена, причина названа", async () => {
    threeParameters();
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 1" }));

    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeDisabled();
    expect(within(dialog).getByText(HINT)).toBeInTheDocument();
  });

  it("соседний случай: без удаления на схеме {1, 2} добавить можно и подсказки нет", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeEnabled();
    expect(within(dialog).queryByText(HINT)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    expect(within(dialog).getByLabelText("Параметр 3")).toHaveValue("");
  });

  it("после сохранённого удаления (в версии нет номера 1) добавление берёт этот номер", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    threeParameters();
    schema.parameters = schema.parameters.filter((p) => p.ordinal !== 1);
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByLabelText("Параметр 1")).toHaveValue("");
  });

  it("убрать можно все параметры: сервер принимает пустой список", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 1" }));
    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 2" }));
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await submittedParameters()).toEqual([]);
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
  });

  it("параметр, добавленный в этой же правке, можно убрать, и его номер снова свободен", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр 3" }));
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByLabelText("Параметр 3")).toHaveValue("");
  });
});

describe("SchemaEditDialog: отказы сервера — подпись по коду", () => {
  const REFUSALS: Array<[string, number, string]> = [
    [
      "schema_parameter_renamed",
      422,
      "Параметр нельзя переименовать по смыслу: это новый параметр. Допустима только правка написания.",
    ],
    ["schema_value_removed", 422, "Значение нельзя удалить — только слить с другим."],
    ["schema_blank", 422, "Имя параметра и его значения не должны быть пустыми."],
    ["schema_bad_ordinals", 422, "Параметров может быть от одного до трёх, номера не повторяются."],
    ["schema_building", 409, "У семьи идёт пересборка схемы: сначала отмените её."],
    ["schema_no_current", 409, "У семьи ещё нет схемы."],
    ["family_not_active", 409, "Семья не активна: схему можно менять только у активной семьи."],
    ["family_not_found", 404, "Семья не найдена: обновите экран."],
  ];

  it.each(REFUSALS)("%s (%i): диалог остаётся открытым, видна подпись, кода и текста сервера нет", async (code, status, label) => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    handlerState.schemaRefusal = { action: "update", code, status };

    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(label);
    expect(document.body.textContent).not.toContain(code);
    expect(document.body.textContent).not.toContain("server text must not reach the screen");
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("неизвестный код отказа — общая подпись, не сам код", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    handlerState.schemaRefusal = { action: "update", code: "some_future_code", status: 409 };

    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Не удалось выполнить действие. Обновите экран и повторите."
    );
    expect(document.body.textContent).not.toContain("some_future_code");
  });

  it("подпись отказа снимается при повторной отправке", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    handlerState.schemaRefusal = { action: "update", code: "schema_blank", status: 422 };

    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));
    await within(dialog).findByRole("alert", {}, LATER);
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { useFamilySchema } from "@/services/queries";
import { SCHEMA_FAMILY_ID, handlerState, schemaParameter, schemaValue } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaEditDialog } from "../SchemaEditDialog";

/**
 * Редактор выбранного параметра внутри окна правки (спека 3в §2.11): плашки значений, «⋯» →
 * слияние, «только из списка», вид значений, диапазоны, блок «не из списка». Окно монтируется
 * как в блоке схемы — по схеме из запроса, — чтобы слияние и «слить с…» перечитывали её.
 */

const LATER = { timeout: 8000 };
const SAVE = { name: "Сохранить схему" };

function LiveDialog() {
  const query = useFamilySchema(SCHEMA_FAMILY_ID);
  if (!query.data) return null;
  return <SchemaEditDialog schema={query.data} open onOpenChange={() => {}} />;
}

async function opened() {
  renderWithProviders(<LiveDialog />);
  const dialog = await screen.findByRole("dialog");
  await waitForDialogFocus();
  return dialog;
}

async function saveEnabled(dialog: HTMLElement) {
  await waitFor(() => expect(within(dialog).getByRole("button", SAVE)).toBeEnabled(), LATER);
}

function schema() {
  return handlerState.familySchemas[SCHEMA_FAMILY_ID];
}

/** «Толщина, мм» с числовыми значениями: их можно перевести в диапазоны без модели. */
function numericThickness() {
  schema().parameters[1] = schemaParameter(
    102,
    2,
    "Толщина, мм",
    [
      schemaValue(1, "1", "schema", { contexts: 1, rows: 6 }),
      schemaValue(2, "4", "schema", { contexts: 21, rows: 699 }),
      schemaValue(3, "5.1", "extension", { contexts: 1, rows: 1 }),
      schemaValue(4, "10", "schema", { contexts: 6, rows: 42 }),
    ],
    { closed: false }
  );
}

describe("ParameterEditor: плашки значений", () => {
  it("плашка несёт число строк; значение без строк — пунктир; дописанное моделью — оранжевая с отметкой", async () => {
    const dialog = await opened();

    expect(within(dialog).getByText("профнастил")).toHaveTextContent("40");
    expect(within(dialog).getByText("металлочерепица")).toHaveTextContent("9");
    const extension = within(dialog).getByText("металло-черепица");
    expect(extension).toHaveAttribute("data-origin", "extension");
    expect(extension).toHaveTextContent("дописано моделью");
    expect(extension.className).toContain("border-warning-border");
    expect(within(dialog).getByText("профнастил").className).not.toContain("border-dashed");
    expect(within(dialog).getByText(/Оранжевая плашка — значение, дописанное моделью/)).toBeInTheDocument();
    // Пунктир у значения без строк («Толщина»: «0,7 мм» — 0 строк).
    const user = userEvent.setup();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));
    expect(within(dialog).getByText("0,7 мм").className).toContain("border-dashed");
    expect(within(dialog).getByText("0,5 мм").className).not.toContain("border-dashed");
  });

  it("сохранённое значение убрать нельзя: кнопки «Убрать значение» нет, есть «⋯»", async () => {
    const dialog = await opened();

    expect(within(dialog).queryByRole("button", { name: /Убрать значение/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Действия со значением «профнастил»" })).toBeInTheDocument();
  });

  it("новое значение — плашка «новое» с кнопкой «Убрать значение»; повтор по норме и пустой ввод не добавляются", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    const input = within(dialog).getByLabelText("Новое значение");

    await user.type(input, "  Керамика {Enter}");
    await user.type(input, "керамика{Enter}");
    await user.type(input, "ПРОФНАСТИЛ{Enter}");
    await user.type(input, "   {Enter}");

    expect(within(dialog).getAllByText(/^Керамика/)).toHaveLength(1);
    expect(within(dialog).getByText("Керамика")).toHaveTextContent("новое");
    expect(within(dialog).getAllByText("профнастил")).toHaveLength(1);
    // Повтор по норме (регистр) — не новая плашка: ни «керамика», ни «ПРОФНАСТИЛ» не появились.
    expect(within(dialog).queryByText(/^керамика/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/^ПРОФНАСТИЛ/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /^Материал/ })).toHaveTextContent("4 значения");
    expect(input).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "Убрать значение «Керамика»" }));
    expect(within(dialog).queryByText("Керамика")).not.toBeInTheDocument();
  });

  it("у параметра с одним значением «⋯» нет: сливать не с чем", async () => {
    schema().parameters[0] = schemaParameter(101, 1, "Материал", [schemaValue(1001, "профнастил")]);
    const dialog = await opened();

    expect(within(dialog).queryByRole("button", { name: /Действия со значением/ })).not.toBeInTheDocument();
    // Сливать не с чем — но и убрать сохранённое значение нельзя.
    expect(within(dialog).queryByRole("button", { name: /Убрать значение/ })).not.toBeInTheDocument();
  });

  it("подсказка о дописанных моделью — только у параметра, где такое значение есть", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    expect(within(dialog).getByText(/Оранжевая плашка — значение, дописанное моделью/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    expect(within(dialog).queryByText(/Оранжевая плашка — значение, дописанное моделью/)).not.toBeInTheDocument();
  });
});

describe("ParameterEditor: «⋯» → слияние значений", () => {
  it("открывает окно слияния с подставленными параметром и значением; слитое уходит из списка правки", async () => {
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Действия со значением «профнастил»" }));
    await user.click(await screen.findByRole("menuitem", { name: "Слить с другим значением…" }));

    const merge = (await screen.findAllByRole("dialog")).find((d) => within(d).queryByText("Слить значения"));
    expect(merge).toBeDefined();
    expect(within(merge!).getByRole("combobox", { name: "Параметр" })).toHaveTextContent("Материал");
    expect(within(merge!).getByRole("combobox", { name: "Источник" })).toHaveTextContent("профнастил");
    await user.click(within(merge!).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(merge!).getByRole("button", { name: "Слить" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(handlerState.schemaRequests[0]).toEqual({
      action: "merge",
      familyId: SCHEMA_FAMILY_ID,
      body: { parameter_id: 101, source_value_id: 1001, target_value_id: 1002 },
    });
    // Значение стало синонимом: из списка правки оно уходит, иначе сохранение прислало бы его заново.
    await waitFor(() => expect(within(dialog).queryByText("профнастил")).not.toBeInTheDocument(), LATER);
    expect(within(dialog).getByText("металлочерепица")).toBeInTheDocument();
  });
});

describe("ParameterEditor: «только из списка» и вид значений", () => {
  it("«Только из списка» уходит в тело как closed", async () => {
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("checkbox", { name: /Только из списка/ }));
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const body = handlerState.schemaRequests[0].body as { parameters: Array<{ closed: boolean }> };
    expect(body.parameters.map((p) => p.closed)).toEqual([true, false]);
  });

  it("точные → диапазоны → точные: сохранённые значения возвращаются как были", async () => {
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));
    expect(within(dialog).queryByLabelText("Новое значение")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Нижняя граница")).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "Точные" }));

    expect(within(dialog).getByText("профнастил")).toBeInTheDocument();
    expect(within(dialog).getByText("металло-черепица")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Нижняя граница")).not.toBeInTheDocument();
    // Туда и обратно — не правка: параметр не помечен «правлен вами», сохранять нечего.
    await within(dialog).findByRole("region", { name: "Что изменит правка" }, LATER);
    expect(within(dialog).getByRole("button", { name: /^Материал/ })).not.toHaveTextContent("правлен вами");
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
  });

  it("параметр модели, которого коснулась правка, и в редакторе помечен «модели · правлен вами»", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    const editor = within(dialog).getByRole("group", { name: "Редактор параметра" });
    expect(within(editor).getByText("модели")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("checkbox", { name: /Только из списка/ }));

    expect(within(editor).getByText("модели · правлен вами")).toBeInTheDocument();
  });

  it("переключатель вида нажат на текущем виде", async () => {
    const dialog = await opened();

    expect(within(dialog).getByRole("button", { name: "Точные" })).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).getByRole("button", { name: "Диапазоны" })).toHaveAttribute("aria-pressed", "false");
  });
});

describe("ParameterEditor: диапазоны", () => {
  it("поля границ и шаг, подсказка с числом диапазонов, «Только из списка» отмечен и недоступен", async () => {
    const user = userEvent.setup();
    numericThickness();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина, мм/ }));
    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));

    await user.type(within(dialog).getByLabelText("Нижняя граница"), "2");
    await user.type(within(dialog).getByLabelText("Верхняя граница"), "10");
    await user.type(within(dialog).getByLabelText("Шаг"), "2");

    expect(within(dialog).getByText(/Получится 4 диапазона от 2 до 10 и 2 крайних — «до 2» и «10 и более»/)).toBeInTheDocument();
    const closed = within(dialog).getByRole("checkbox", { name: /Только из списка — для диапазонов включено всегда/ });
    expect(closed).toHaveAttribute("aria-disabled", "true");
    expect(closed).toBeChecked();
  });

  it("пока границы не заданы, вместо числа — подсказка, что указать", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));

    expect(within(dialog).getByText(/Укажите границы и шаг/)).toBeInTheDocument();
  });

  it("перевод в диапазоны показывает «что станет с прежними значениями» из сводки и уходит в тело границами без значений", async () => {
    const user = userEvent.setup();
    numericThickness();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина, мм/ }));
    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));
    await user.type(within(dialog).getByLabelText("Нижняя граница"), "2");
    await user.type(within(dialog).getByLabelText("Верхняя граница"), "10");
    await user.type(within(dialog).getByLabelText("Шаг"), "2");

    const table = await within(dialog).findByLabelText("Что станет с прежними значениями", {}, LATER);
    expect(within(table).getByText("1")).toBeInTheDocument();
    expect(table).toHaveTextContent("1 → до 2");
    expect(table).toHaveTextContent("5.1 → 4–6");
    expect(table).toHaveTextContent("10 → 10 и более");
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const sent = (handlerState.schemaRequests[0].body as { parameters: Array<Record<string, unknown>> }).parameters[1];
    expect(sent).toEqual({
      ordinal: 2,
      name: "Толщина, мм",
      role: "main",
      value_kind: "range",
      closed: true,
      values: [],
      range: { start: "2", stop: "10", step: "2" },
    });
    // После сохранения сервер строит подписи: у параметра шесть значений-диапазонов.
    await waitFor(() => expect(schema().parameters[1].values.filter((v) => v.merged_into_id === null)).toHaveLength(6), LATER);
    expect(schema().parameters[1].value_kind).toBe("range");
  });

  it("запятая в границе читается как точка при отправке", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));

    await user.type(within(dialog).getByLabelText("Нижняя граница"), "1,5");
    await user.type(within(dialog).getByLabelText("Верхняя граница"), "3,5");
    await user.type(within(dialog).getByLabelText("Шаг"), "0,5");
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const sent = (handlerState.schemaRequests[0].body as { parameters: Array<{ range: unknown }> }).parameters[0];
    expect(sent.range).toEqual({ start: "1.5", stop: "3.5", step: "0.5" });
  });

  it("у сохранённого диапазона плашки с подписями и числом строк; смена границ прячет их до сохранения", async () => {
    schema().parameters[1] = schemaParameter(
      102,
      2,
      "Толщина, мм",
      [
        schemaValue(11, "до 2", "manual", { rows: 6 }),
        schemaValue(12, "2–4", "manual", { rows: 700 }),
        schemaValue(13, "4 и более", "manual", { rows: 3 }),
      ],
      { value_kind: "range", closed: true, range: { start: "2", stop: "4", step: "2" } }
    );
    const user = userEvent.setup();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина, мм/ }));

    expect(within(dialog).getByText("до 2")).toHaveTextContent("6");
    expect(within(dialog).getByText("2–4")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Действия со значением/ })).not.toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("Шаг"));
    await user.type(within(dialog).getByLabelText("Шаг"), "1");

    expect(within(dialog).queryByText("2–4")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/Получится 2 диапазона от 2 до 4/)).toBeInTheDocument();
  });
});

describe("ParameterEditor: значения не из списка", () => {
  function withOffList() {
    schema().parameters[0] = schemaParameter(
      101,
      1,
      "Материал",
      [
        schemaValue(1001, "профнастил", "schema", { contexts: 3, rows: 40 }),
        schemaValue(1002, "металлочерепица", "schema", { contexts: 1, rows: 9 }),
      ],
      { closed: true, off_list: [{ text_norm: "5 см", text: "5 см", contexts: 3, rows: 7 }] }
    );
  }

  it("блок называет текст, число контекстов и строк и предлагает «Добавить в список» и «Слить с…»", async () => {
    withOffList();
    const dialog = await opened();

    const block = within(dialog).getByRole("region", { name: "Значения не из списка" });
    expect(within(block).getByText("Встретились значения не из списка")).toBeInTheDocument();
    expect(within(block).getByText("5 см")).toHaveTextContent("7");
    expect(within(block).getByText("3 контекста")).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Добавить в список" })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Слить «5 см» с…" })).toBeInTheDocument();
  });

  it("у параметра без значений не из списка блока нет", async () => {
    const dialog = await opened();

    expect(within(dialog).queryByRole("region", { name: "Значения не из списка" })).not.toBeInTheDocument();
  });

  it("«Добавить в список» вносит значение в правку: плашка «новое», блок помечает «будет добавлено», кнопки нет", async () => {
    withOffList();
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить в список" }));

    const block = within(dialog).getByRole("region", { name: "Значения не из списка" });
    expect(within(block).getByText("будет добавлено в список при сохранении")).toBeInTheDocument();
    expect(within(block).queryByRole("button", { name: "Добавить в список" })).not.toBeInTheDocument();
    expect(within(dialog).getAllByText("5 см").some((el) => el.textContent?.includes("новое"))).toBe(true);
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));
    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const sent = (handlerState.schemaRequests[0].body as { parameters: Array<{ values: string[] }> }).parameters[0];
    expect(sent.values).toEqual(["профнастил", "металлочерепица", "5 см"]);
  });

  it("«Слить с…» выбирает значение списка: отдельный вызов, блок после перечитывания исчезает, правка не затронута", async () => {
    withOffList();
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Слить «5 см» с…" }));
    await user.click(await screen.findByRole("menuitem", { name: "профнастил" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(handlerState.schemaRequests[0]).toEqual({
      action: "resolve",
      familyId: SCHEMA_FAMILY_ID,
      body: { parameter_id: 101, text_norm: "5 см", target_value_id: 1001 },
    });
    await waitFor(
      () => expect(within(dialog).queryByRole("region", { name: "Значения не из списка" })).not.toBeInTheDocument(),
      LATER
    );
    expect(within(dialog).getByText("профнастил")).toHaveTextContent("47");
  });

  it("«Слить с…» предлагает только живые значения списка: слитый синоним целью не бывает", async () => {
    withOffList();
    schema().parameters[0].values.push(schemaValue(1009, "проф-настил", "extension", { merged_into_id: 1001 }));
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Слить «5 см» с…" }));

    expect(await screen.findByRole("menuitem", { name: "профнастил" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "металлочерепица" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "проф-настил" })).not.toBeInTheDocument();
  });

  it("«Добавить в список» вносит показанное написание, а не нормализованный ключ", async () => {
    withOffList();
    schema().parameters[0].off_list = [{ text_norm: "5 см", text: "5 См", contexts: 3, rows: 7 }];
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить в список" }));
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    const sent = (handlerState.schemaRequests[0].body as { parameters: Array<{ values: string[] }> }).parameters[0];
    expect(sent.values).toEqual(["профнастил", "металлочерепица", "5 См"]);
  });

  it("после «Слить с…» схема на месте сдвинулась: сводка запрашивается заново, сохранение проходит с первого раза", async () => {
    withOffList();
    const user = userEvent.setup();
    const dialog = await opened();
    await user.type(within(dialog).getByLabelText("Новое значение"), "керамика{Enter}");
    await saveEnabled(dialog);
    const previewsBefore = handlerState.schemaEditPreviews.length;

    await user.click(within(dialog).getByRole("button", { name: "Слить «5 см» с…" }));
    await user.click(await screen.findByRole("menuitem", { name: "профнастил" }));
    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);

    // `revision` выросла: предпросмотр по тому же телу уходит снова, иначе сохранение отказало бы
    // `preview_changed` по устаревшему `preview_hash`.
    await waitFor(() => expect(handlerState.schemaEditPreviews.length).toBeGreaterThan(previewsBefore), LATER);
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));
    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(2), LATER);
    await waitFor(() => expect(schema().revision).toBe(3), LATER);
    expect(within(dialog).queryByText(/Схема изменилась, пока окно было открыто/)).not.toBeInTheDocument();
  });

  it("отказ «Слить с…» перечитывает схему: текст, который отнёс кто-то другой, из блока уходит", async () => {
    withOffList();
    const user = userEvent.setup();
    const dialog = await opened();
    // Другой администратор уже отнёс «5 см»: на сервере его больше нет, ответ — `off_list_not_found`.
    schema().parameters[0] = { ...schema().parameters[0], off_list: [] };
    handlerState.schemaRefusal = { action: "resolve", code: "off_list_not_found", status: 404 };

    await user.click(within(dialog).getByRole("button", { name: "Слить «5 см» с…" }));
    await user.click(await screen.findByRole("menuitem", { name: "профнастил" }));

    await waitFor(
      () => expect(within(dialog).queryByRole("region", { name: "Значения не из списка" })).not.toBeInTheDocument(),
      LATER
    );
  });

  it("отказ «Слить с…» — подпись по коду, блок остаётся", async () => {
    withOffList();
    const user = userEvent.setup();
    const dialog = await opened();
    handlerState.schemaRefusal = { action: "resolve", code: "off_list_not_found", status: 404 };

    await user.click(within(dialog).getByRole("button", { name: "Слить «5 см» с…" }));
    await user.click(await screen.findByRole("menuitem", { name: "профнастил" }));

    const block = within(dialog).getByRole("region", { name: "Значения не из списка" });
    expect(await within(block).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Такого значения не из списка больше нет: обновите экран."
    );
    expect(document.body.textContent).not.toContain("off_list_not_found");
  });
});

describe("ParameterEditor: preview_changed перечитывает схему", () => {
  it("слияние, сделанное другим администратором, после отказа сохранения уходит из правки; своя правка остаётся", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.type(within(dialog).getByLabelText("Новое значение"), "керамика{Enter}");
    await saveEnabled(dialog);
    // Другой администратор слил «профнастил» в «металлочерепица»: `revision` выросла.
    const current = schema();
    handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
      ...current,
      revision: current.revision + 1,
      parameters: current.parameters.map((p) =>
        p.id !== 101 ? p : { ...p, values: p.values.map((v) => (v.id === 1001 ? { ...v, merged_into_id: 1002 } : v)) }
      ),
    };

    await user.click(within(dialog).getByRole("button", SAVE));

    expect(await within(dialog).findByText(/Схема изменилась, пока окно было открыто/, {}, LATER)).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).queryByText("профнастил")).not.toBeInTheDocument(), LATER);
    expect(within(dialog).getByText("керамика")).toBeInTheDocument();
  });
});

describe("ParameterEditor: имя и удаление", () => {
  it("правка написания имени уходит как есть; смысловое переименование предпросмотр отказывает подписью", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    await user.clear(within(dialog).getByLabelText("Имя параметра"));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "ТОЛЩИНА");
    await saveEnabled(dialog);
    await user.clear(within(dialog).getByLabelText("Имя параметра"));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "Толщина листа");

    expect(await within(dialog).findByText(/Параметр нельзя переименовать по смыслу/, {}, LATER)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
  });

  it("у нового параметра пометка «новый», у сохранённого человеческого — «ваш»", async () => {
    schema().parameters[1] = { ...schema().parameters[1], origin: "human" };
    const user = userEvent.setup();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    expect(within(dialog).getAllByText("ваш").length).toBeGreaterThan(0);
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    expect(within(dialog).getAllByText("новый").length).toBeGreaterThan(0);
  });
});

describe("ParameterEditor: слияние из окна правки не трогает правку (I1) и сдвигает исходное состояние", () => {
  async function mergeProfnastilInto(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
    await user.click(within(dialog).getByRole("button", { name: "Действия со значением «профнастил»" }));
    await user.click(await screen.findByRole("menuitem", { name: "Слить с другим значением…" }));
    const merge = (await screen.findAllByRole("dialog")).find((d) => within(d).queryByText("Слить значения"))!;
    await user.click(within(merge).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(merge).getByRole("button", { name: "Слить" }));
  }

  it("слияние при несохранённой правке и готовой сводке: уходит только merge, PATCH схемы нет, окно открыто, правка цела", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.type(within(dialog).getByLabelText("Новое значение"), "керамика{Enter}");
    await saveEnabled(dialog);

    await mergeProfnastilInto(user, dialog);

    await waitFor(() => expect(handlerState.schemaRequests.length).toBeGreaterThanOrEqual(1), LATER);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(handlerState.schemaRequests.map((r) => r.action)).toEqual(["merge"]);
    expect(screen.getByRole("dialog", { name: "Править схему" })).toBeInTheDocument();
    expect(within(dialog).getByText("керамика")).toBeInTheDocument();
    expect(schema().revision).toBe(2);
  });

  it("слияние без правок: «Сохранить» остаётся недоступной — сохранять нечего", async () => {
    const user = userEvent.setup();
    const dialog = await opened();

    await mergeProfnastilInto(user, dialog);

    await waitFor(() => expect(within(dialog).queryByText("профнастил")).not.toBeInTheDocument(), LATER);
    await within(dialog).findByRole("region", { name: "Что изменит правка" }, LATER);
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
  });

  it("слияние при правке: правка цела, «Сохранить» доступна после сводки и несёт только правку", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    await user.type(within(dialog).getByLabelText("Новое значение"), "керамика{Enter}");

    await mergeProfnastilInto(user, dialog);

    await waitFor(() => expect(within(dialog).queryByText("профнастил")).not.toBeInTheDocument(), LATER);
    expect(within(dialog).getByText("керамика")).toBeInTheDocument();
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));
    await waitFor(() => expect(handlerState.schemaRequests.map((r) => r.action)).toEqual(["merge", "update"]), LATER);
    const body = handlerState.schemaRequests[1].body as { parameters: Array<{ values: string[] }> };
    expect(body.parameters[0].values).toEqual(["металлочерепица", "металло-черепица", "керамика"]);
  });
});

describe("ParameterEditor: схема перечитана с чужой правкой, своих правок нет (N1)", () => {
  it("черновики берутся из перечитанной схемы: чужая смена роли видна, сохранять нечего", async () => {
    const user = userEvent.setup();
    const dialog = await opened();
    // Другой администратор сделал «Толщину» второстепенной; слияние из этого окна перечитывает схему.
    schema().parameters[1] = { ...schema().parameters[1], role: "secondary", origin: "human" };

    await user.click(within(dialog).getByRole("button", { name: "Действия со значением «профнастил»" }));
    await user.click(await screen.findByRole("menuitem", { name: "Слить с другим значением…" }));
    const merge = (await screen.findAllByRole("dialog")).find((d) => within(d).queryByText("Слить значения"))!;
    await user.click(within(merge).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(merge).getByRole("button", { name: "Слить" }));
    await waitFor(() => expect(within(dialog).queryByText("профнастил")).not.toBeInTheDocument(), LATER);
    await within(dialog).findByRole("region", { name: "Что изменит правка" }, LATER);

    const secondary = within(dialog).getByRole("region", { name: "Второстепенные — вариант не делят" });
    expect(within(secondary).getByRole("button", { name: /^Толщина/ })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
  });
});

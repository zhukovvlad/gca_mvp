import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState, schemaParameter, schemaValue } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaEditDialog } from "./SchemaEditDialog";

/**
 * «Править схему…» — окно «Список и редактор» (спека 3в §2.5, §2.11): список параметров по
 * ролям, редактор выбранного, сводка предпросмотра и «Сохранить схему». Отказы сервера — подпись
 * по коду, сам код и текст сервера на экран не выходят.
 */

const LATER = { timeout: 8000 };
const SAVE = { name: "Сохранить схему" };

function renderDialog() {
  const onOpenChange = vi.fn();
  renderWithProviders(
    <SchemaEditDialog schema={handlerState.familySchemas[SCHEMA_FAMILY_ID]} open onOpenChange={onOpenChange} />
  );
  return onOpenChange;
}

async function opened() {
  const dialog = await screen.findByRole("dialog");
  await waitForDialogFocus();
  return dialog;
}

/** Сводка по нынешнему телу пришла: «Сохранить» зависит от неё. */
async function summaryShown(dialog: HTMLElement) {
  await within(dialog).findByRole("region", { name: "Что изменит правка" }, LATER);
}

async function saveEnabled(dialog: HTMLElement) {
  await waitFor(() => expect(within(dialog).getByRole("button", SAVE)).toBeEnabled(), LATER);
}

function sentBody(index = 0) {
  return handlerState.schemaRequests[index].body as {
    parameters: Array<Record<string, unknown>>;
    preview_hash: string;
  };
}

/** Схема из десяти параметров: к фикстуре из двух добавлено восемь второстепенных. */
function tenParameters(count = 10) {
  const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
  for (let ordinal = 3; ordinal <= count; ordinal += 1) {
    schema.parameters.push(
      schemaParameter(100 + ordinal, ordinal, `Параметр ${ordinal}`, [schemaValue(2000 + ordinal, "да")], {
        role: "secondary",
      })
    );
  }
}

describe("SchemaEditDialog: раскладка", () => {
  it("окно не выше 90 % экрана, шире базового, без внутренних отступов базового окна", async () => {
    renderDialog();
    const dialog = await opened();

    expect(dialog.className).toContain("max-h-[90vh]");
    expect(dialog.className).toContain("sm:max-w-4xl");
    expect(dialog.className).not.toContain("sm:max-w-sm");
    expect(dialog.className).toContain("overflow-hidden");
  });

  it("список и редактор прокручиваются каждый сам: min-h-0 overflow-y-auto на обеих колонках", async () => {
    renderDialog();
    const dialog = await opened();

    const list = within(dialog).getByRole("navigation", { name: "Параметры" });
    const editor = within(dialog).getByRole("group", { name: "Редактор параметра" });
    for (const column of [list, editor]) {
      expect(column.className).toContain("min-h-0");
      expect(column.className).toContain("overflow-y-auto");
    }
  });

  it("шапка и низ закреплены вне прокручиваемой области: «Сохранить» не внутри колонок", async () => {
    renderDialog();
    const dialog = await opened();

    const save = within(dialog).getByRole("button", SAVE);
    expect(within(dialog).getByRole("navigation", { name: "Параметры" }).contains(save)).toBe(false);
    expect(within(dialog).getByRole("group", { name: "Редактор параметра" }).contains(save)).toBe(false);
    expect(within(dialog).getByRole("heading", { name: "Править схему" })).toBeInTheDocument();
    // Ни «Сохранить», ни заголовок не лежат ни в одном прокручиваемом предке внутри окна —
    // иначе на узком экране они уезжали бы вместе с колонками (раскладку меряет задача 9).
    const heading = within(dialog).getByRole("heading", { name: "Править схему" });
    for (const pinned of [save, heading]) {
      for (let node = pinned.parentElement; node && node !== dialog; node = node.parentElement) {
        expect(node.className).not.toMatch(/overflow-y-auto/);
      }
    }
  });
});

describe("SchemaEditDialog: список параметров", () => {
  it("группы «Главные — определяют вариант» и «Второстепенные — вариант не делят»; пустая группа — «Нет.»", async () => {
    renderDialog();
    const dialog = await opened();

    const main = within(dialog).getByRole("region", { name: "Главные — определяют вариант" });
    const secondary = within(dialog).getByRole("region", { name: "Второстепенные — вариант не делят" });
    expect(within(main).getByRole("button", { name: /^Материал/ })).toBeInTheDocument();
    expect(within(main).getByRole("button", { name: /^Толщина/ })).toBeInTheDocument();
    expect(within(secondary).getByText("Нет.")).toBeInTheDocument();
  });

  it("строка списка — имя, вид, число значений и строк, происхождение", async () => {
    renderDialog();
    const dialog = await opened();

    const item = within(dialog).getByRole("button", { name: /^Материал/ });
    expect(item).toHaveTextContent("3 значения · 49 строк");
    expect(item).toHaveTextContent("модели");
    expect(within(dialog).getByRole("button", { name: /^Толщина/ })).toHaveTextContent("2 значения · 12 строк");
  });

  it("выбран первый главный параметр; щелчок по другому открывает его в редакторе", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    expect(within(dialog).getByLabelText("Имя параметра")).toHaveValue("Материал");
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    expect(within(dialog).getByLabelText("Имя параметра")).toHaveValue("Толщина");
    expect(within(dialog).getByRole("button", { name: /^Толщина/ })).toHaveAttribute("aria-current", "true");
    expect(within(dialog).getByRole("button", { name: /^Материал/ })).not.toHaveAttribute("aria-current");
  });

  it("счётчик «N из 10 параметров»; «Добавить параметр» заводит новый, выбранный и с пометкой «новый»", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    expect(within(dialog).getByText("2 из 10 параметров")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByText("3 из 10 параметров")).toBeInTheDocument();
    const item = within(dialog).getByRole("button", { name: /^Без имени/ });
    expect(item).toHaveAttribute("aria-current", "true");
    expect(item).toHaveTextContent("новый");
    expect(within(dialog).getByLabelText("Имя параметра")).toHaveValue("");
  });

  it("на девяти параметрах добавление доступно и ведёт к пределу; на десяти кнопка недоступна, предел назван", async () => {
    tenParameters(9);
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeEnabled();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    expect(within(dialog).getByText("10 из 10 параметров — предел")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeDisabled();
  });

  it("убрав параметр с предела, кнопка снова доступна", async () => {
    tenParameters(10);
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр" }));

    expect(within(dialog).getByText("9 из 10 параметров")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Добавить параметр" })).toBeEnabled();
  });

  it("смена роли переносит параметр в другую группу", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Второстепенный" }));

    const secondary = within(dialog).getByRole("region", { name: "Второстепенные — вариант не делят" });
    expect(within(secondary).getByRole("button", { name: /^Материал/ })).toBeInTheDocument();
    const main = within(dialog).getByRole("region", { name: "Главные — определяют вариант" });
    expect(within(main).queryByRole("button", { name: /^Материал/ })).not.toBeInTheDocument();
  });

  it("параметр модели, которого коснулась правка, помечен «модели · правлен вами»; нетронутый — «модели»", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    expect(within(dialog).getByRole("button", { name: /^Материал/ })).not.toHaveTextContent("правлен вами");

    await user.click(within(dialog).getByRole("checkbox", { name: /Только из списка/ }));

    expect(within(dialog).getByRole("button", { name: /^Материал/ })).toHaveTextContent("модели · правлен вами");
    expect(within(dialog).getByRole("button", { name: /^Толщина/ })).not.toHaveTextContent("правлен вами");
    // Строка пометок называет «только из списка» у точного параметра (спека 3в §2.11).
    expect(within(dialog).getByRole("button", { name: /^Материал/ })).toHaveTextContent("3 значения · 49 строк · только из списка");
    expect(within(dialog).getByRole("button", { name: /^Толщина/ })).not.toHaveTextContent("только из списка");
  });
});

describe("SchemaEditDialog: предпросмотр и сохранение", () => {
  it("предпросмотр уходит по нынешнему телу — номера, роли, виды, значения без слитых", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].values[2].merged_into_id = 1002;
    renderDialog();
    const dialog = await opened();
    await summaryShown(dialog);

    expect(handlerState.schemaEditPreviews).toHaveLength(1);
    expect(handlerState.schemaEditPreviews[0].body).toEqual({
      parameters: [
        { ordinal: 1, name: "Материал", role: "main", value_kind: "exact", closed: false, values: ["профнастил", "металлочерепица"], range: null },
        { ordinal: 2, name: "Толщина", role: "main", value_kind: "exact", closed: false, values: ["0,5 мм", "0,7 мм"], range: null },
      ],
    });
  });

  it("без правок «Сохранить схему» недоступна даже при готовой сводке; сводка — «варианты не меняются»", async () => {
    renderDialog();
    const dialog = await opened();
    await summaryShown(dialog);

    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
    expect(within(dialog).getByText("Варианты семьи не меняются: их по-прежнему 2.")).toBeInTheDocument();
  });

  it("после правки сводка приходит и «Сохранить» открывается; тело и preview_hash уходят целиком, окно закрывается", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    await user.type(within(dialog).getByLabelText("Новое значение"), "1 мм{Enter}");
    await saveEnabled(dialog);
    const shownHash = handlerState.schemaEditPreviews.length;
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(shownHash).toBeGreaterThan(0);
    const body = sentBody();
    expect(body.parameters[1]).toEqual({
      ordinal: 2,
      name: "Толщина",
      role: "main",
      value_kind: "exact",
      closed: false,
      values: ["0,5 мм", "0,7 мм", "1 мм"],
      range: null,
    });
    expect(body.preview_hash).toMatch(/^edit-43-2-r1-/);
    const saved = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    expect(saved.revision).toBe(2);
    expect(saved.parameters[1].origin).toBe("human");
    expect(saved.parameters[0].origin).toBe("model");
  });

  it("пока предпросмотр по нынешнему телу не вернулся, «Сохранить» недоступна и показано ожидание; ответ — сводка и кнопка", async () => {
    const releases: Array<() => void> = [];
    handlerState.schemaEditPreviewGate = () =>
      new Promise<void>((resolve) => {
        releases.push(resolve);
      });
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    // Первый запрос уходит через паузу после открытия; правка раньше паузы его отменила бы, и
    // тест зависел бы от скорости прогона (ревью задачи 8: падал при отдельном запуске файла).
    await waitFor(() => expect(handlerState.schemaEditPreviews).toHaveLength(1), LATER);
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));
    await user.type(within(dialog).getByLabelText("Новое значение"), "1 мм{Enter}");

    // Первый запрос ушёл при открытии окна, второй — по телу после правки; оба ждут.
    await waitFor(() => expect(handlerState.schemaEditPreviews).toHaveLength(2), LATER);
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
    expect(within(dialog).getByText("Считаю, что изменит правка…")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "Что изменит правка" })).not.toBeInTheDocument();
    await act(async () => {
      releases.forEach((release) => release());
    });

    await summaryShown(dialog);
    expect(within(dialog).getByRole("button", SAVE)).toBeEnabled();
    expect(within(dialog).queryByText("Считаю, что изменит правка…")).not.toBeInTheDocument();
  });

  it("ответ предпросмотра на прежнее тело сохранение не разблокирует: после новой правки кнопка снова ждёт сводку", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));
    await user.type(within(dialog).getByLabelText("Новое значение"), "1 мм{Enter}");
    await saveEnabled(dialog);

    await user.type(within(dialog).getByLabelText("Новое значение"), "2 мм{Enter}");

    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
    await saveEnabled(dialog);
  });

  it("новый параметр уходит с ordinal null, ролью и значениями; сервер выдаёт номер", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "Цвет");
    await user.click(within(dialog).getByRole("button", { name: "Второстепенный" }));
    await user.type(within(dialog).getByLabelText("Новое значение"), "красный{Enter}зелёный{Enter}");
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(sentBody().parameters[2]).toEqual({
      ordinal: null,
      name: "Цвет",
      role: "secondary",
      value_kind: "exact",
      closed: false,
      values: ["красный", "зелёный"],
      range: null,
    });
    await waitFor(() => expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[2]?.ordinal).toBe(3), LATER);
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[2].origin).toBe("human");
  });

  it("добавление главного параметра: сводка говорит «соберутся заново» и называет запрос к модели", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "Цвет");

    expect(await within(dialog).findByText(/соберутся заново: сейчас 2/, {}, LATER)).toBeInTheDocument();
    expect(within(dialog).getByText("У модели запросится «Цвет» — у 12 контекстов.")).toBeInTheDocument();
  });

  it("«Убрать параметр» убирает его из списка и из тела: сервер архивирует", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр" }));
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(sentBody().parameters.map((p) => p.ordinal)).toEqual([2]);
    expect(within(dialog).queryByRole("button", { name: /^Материал/ })).not.toBeInTheDocument();
  });

  it("можно убрать все параметры: сервер принимает пустой список", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр" }));
    await user.click(within(dialog).getByRole("button", { name: "Убрать параметр" }));
    expect(within(dialog).getByText(/Параметров нет/)).toBeInTheDocument();
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(sentBody().parameters).toEqual([]);
  });

  it("«Отмена» закрывает окно и ничего не отправляет", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(handlerState.schemaRequests).toEqual([]);
  });
});

describe("SchemaEditDialog: отказ проверки в предпросмотре", () => {
  it("два параметра с одним именем — подпись вместо сводки, «Сохранить» недоступна, кода на экране нет", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));

    await user.type(within(dialog).getByLabelText("Имя параметра"), "толщина");

    const status = await within(dialog).findByText(/Два параметра называются одинаково/, {}, LATER);
    expect(status).toBeInTheDocument();
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
    expect(within(dialog).queryByRole("region", { name: "Что изменит правка" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("schema_duplicate_parameter");
  });

  it("диапазон с неверными границами — подпись отказа, сохранить нельзя", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));

    await user.click(within(dialog).getByRole("button", { name: "Диапазоны" }));

    expect(await within(dialog).findByText(/Границы диапазона неверны/, {}, LATER)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", SAVE)).toBeDisabled();
  });

  it("отказ снимается, как только тело снова проходит проверку", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: "Добавить параметр" }));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "толщина");
    await within(dialog).findByText(/Два параметра называются одинаково/, {}, LATER);

    await user.clear(within(dialog).getByLabelText("Имя параметра"));
    await user.type(within(dialog).getByLabelText("Имя параметра"), "Цвет");

    await saveEnabled(dialog);
    expect(within(dialog).queryByText(/Два параметра называются одинаково/)).not.toBeInTheDocument();
  });
});

describe("SchemaEditDialog: preview_changed при сохранении", () => {
  it("сводка пересчитывается, правка в окне остаётся, повторное сохранение проходит", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));
    await user.type(within(dialog).getByLabelText("Новое значение"), "1 мм{Enter}");
    await saveEnabled(dialog);
    const previewsBefore = handlerState.schemaEditPreviews.length;
    // Другой администратор правит схему: её `revision` уходит от показанной сводки.
    handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
      ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
      revision: handlerState.familySchemas[SCHEMA_FAMILY_ID].revision + 1,
    };

    await user.click(within(dialog).getByRole("button", SAVE));

    expect(await within(dialog).findByText(/Схема изменилась, пока окно было открыто/, {}, LATER)).toBeInTheDocument();
    expect(handlerState.schemaRequests).toHaveLength(1);
    expect(onOpenChange).not.toHaveBeenCalled();
    // Правка не потеряна: плашка нового значения на месте.
    expect(within(dialog).getByText("1 мм")).toBeInTheDocument();
    await waitFor(() => expect(handlerState.schemaEditPreviews.length).toBeGreaterThan(previewsBefore), LATER);
    await saveEnabled(dialog);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(handlerState.schemaRequests).toHaveLength(2);
    expect(sentBody(1).preview_hash).not.toBe(sentBody(0).preview_hash);
  });
});

describe("SchemaEditDialog: отказы сервера при сохранении — подпись по коду", () => {
  const REFUSALS: Array<[string, number, string]> = [
    ["schema_parameter_renamed", 422, "Параметр нельзя переименовать по смыслу: это новый параметр. Допустима только правка написания."],
    ["schema_value_removed", 422, "Значение нельзя удалить — только слить с другим."],
    ["schema_blank", 422, "Имя параметра и его значения не должны быть пустыми."],
    ["schema_bad_ordinals", 422, "Параметра с таким номером среди действующих нет: обновите экран."],
    ["schema_too_many_parameters", 422, "Параметров может быть не больше десяти."],
    ["schema_ordinals_exhausted", 409, "У схемы кончились номера параметров: пересоберите схему."],
    ["schema_duplicate_parameter", 422, "Два параметра называются одинаково: имена должны различаться."],
    ["schema_bad_range", 422, "Границы диапазона неверны: нижняя должна быть меньше верхней, шаг больше нуля, а число шагов целым."],
    ["schema_range_too_many_values", 422, "В диапазоне слишком много значений: увеличьте шаг или сузьте границы."],
    ["schema_building", 409, "У семьи идёт пересборка схемы: сначала отмените её."],
    ["schema_no_current", 409, "У семьи ещё нет схемы."],
    ["family_not_active", 409, "Семья не активна: схему можно менять только у активной семьи."],
    ["family_not_found", 404, "Семья не найдена: обновите экран."],
  ];

  async function editedDialog() {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    const dialog = await opened();
    await user.click(within(dialog).getByRole("button", { name: /^Толщина/ }));
    await user.type(within(dialog).getByLabelText("Новое значение"), "1 мм{Enter}");
    await saveEnabled(dialog);
    return { user, onOpenChange, dialog };
  }

  it.each(REFUSALS)("%s (%i): окно остаётся открытым, видна подпись, кода и текста сервера нет", async (code, status, label) => {
    const { user, onOpenChange, dialog } = await editedDialog();
    handlerState.schemaRefusal = { action: "update", code, status };

    await user.click(within(dialog).getByRole("button", SAVE));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(label);
    expect(document.body.textContent).not.toContain(code);
    expect(document.body.textContent).not.toContain("server text must not reach the screen");
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("неизвестный код отказа — общая подпись, не сам код", async () => {
    const { user, dialog } = await editedDialog();
    handlerState.schemaRefusal = { action: "update", code: "some_future_code", status: 409 };

    await user.click(within(dialog).getByRole("button", SAVE));

    expect(await within(dialog).findByRole("alert", {}, LATER)).toHaveTextContent(
      "Не удалось выполнить действие. Обновите экран и повторите."
    );
    expect(document.body.textContent).not.toContain("some_future_code");
  });

  it("подпись отказа снимается при повторной отправке", async () => {
    const { user, onOpenChange, dialog } = await editedDialog();
    handlerState.schemaRefusal = { action: "update", code: "schema_blank", status: 422 };

    await user.click(within(dialog).getByRole("button", SAVE));
    await within(dialog).findByRole("alert", {}, LATER);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });
});

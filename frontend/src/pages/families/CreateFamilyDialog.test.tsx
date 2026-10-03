import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { createTestQueryClient, renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { NewRow } from "@/types/domain";

import { CreateFamilyDialog } from "./CreateFamilyDialog";

/**
 * «Завести семью…» (спека semantic-suggestions §2.9): имя подставлено из ответа ИИ и
 * редактируется, единица показана и не редактируется, определение обязательно.
 * Строка — «Гидрошпонка …» из фикстуры очереди «Новая» (имя «Гидрошпонки», м²).
 */

function fixtureRow(): NewRow & { suggestion_id: number } {
  const row = structuredClone(handlerState.newRows[0]);
  return { ...row, suggestion_id: row.suggestion_id as number };
}

function renderDialog(onClose = vi.fn(), onOpenFamily?: (id: number) => void) {
  const queryClient = createTestQueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  renderWithProviders(
    <CreateFamilyDialog row={fixtureRow()} unitLabel="м²" onClose={onClose} onOpenFamily={onOpenFamily} />,
    { queryClient }
  );
  return { onClose, invalidate };
}

const nameInput = () => screen.getByLabelText("Имя") as HTMLInputElement;
const definitionInput = () => screen.getByLabelText(/Определение/) as HTMLTextAreaElement;
const saveButton = () => screen.getByRole("button", { name: "Сохранить и активировать" });

describe("CreateFamilyDialog", () => {
  it("имя подставлено из ответа ИИ, строка названа в описании", () => {
    renderDialog();
    expect(nameInput().value).toBe("Гидрошпонки");
    expect(screen.getByText(/Гидрошпонка ТЕХНОНИКОЛЬ Фундамент ТПС-В-140-1/)).toBeInTheDocument();
  });

  it("ответ «СИСТЕМА» не подставляется именем: поле пустое, кнопка неактивна, пока имя не введено", async () => {
    const user = userEvent.setup();
    const row = {
      ...fixtureRow(),
      new_family_name: "СИСТЕМА",
      is_system: true,
    };
    renderWithProviders(<CreateFamilyDialog row={row} unitLabel="компл" onClose={vi.fn()} />);
    await waitForDialogFocus();

    expect(nameInput().value).toBe("");
    await user.type(definitionInput(), "Входит: дымоудаление. Не входит: вентиляция.");
    expect(saveButton()).toBeDisabled();
    await user.type(nameInput(), "Система дымоудаления");
    expect(saveButton()).toBeEnabled();
  });

  it("ответ системы в другом регистре («Система») тоже не подставляется: признак — is_system сервера", () => {
    const row = { ...fixtureRow(), new_family_name: "Система", is_system: true };
    renderWithProviders(<CreateFamilyDialog row={row} unitLabel="компл" onClose={vi.fn()} />);
    expect(nameInput().value).toBe("");
  });

  it("имя редактируется и в запрос уходит правленое", async () => {
    const user = userEvent.setup();
    renderDialog();
    await waitForDialogFocus();

    await user.clear(nameInput());
    await user.type(nameInput(), "Гидрошпонка деформационных швов");
    await user.type(definitionInput(), "Входит: шпонки. Не входит: мастики.");
    await user.click(saveButton());

    await waitFor(() => expect(handlerState.createFamilyRequests).toHaveLength(1));
    expect(handlerState.createFamilyRequests[0].body.title).toBe("Гидрошпонка деформационных швов");
  });

  it("единица показана и не редактируется: рядом нет ни поля, ни списка", () => {
    renderDialog();
    expect(screen.getByTestId("create-family-unit")).toHaveTextContent("м²");
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("без определения кнопка неактивна", () => {
    renderDialog();
    expect(saveButton()).toBeDisabled();
  });

  it("определение из одних пробелов не считается определением", async () => {
    const user = userEvent.setup();
    renderDialog();
    await waitForDialogFocus();
    await user.type(definitionInput(), "   ");
    expect(saveButton()).toBeDisabled();
  });

  it("имя из одних пробелов при заполненном определении — кнопка неактивна", async () => {
    const user = userEvent.setup();
    renderDialog();
    await waitForDialogFocus();
    await user.clear(nameInput());
    await user.type(nameInput(), "   ");
    await user.type(definitionInput(), "Входит: шпонки. Не входит: мастики.");
    expect(saveButton()).toBeDisabled();
  });

  it("имя и определение заполнены — кнопка активна", async () => {
    const user = userEvent.setup();
    renderDialog();
    await waitForDialogFocus();
    await user.type(definitionInput(), "Входит: шпонки. Не входит: мастики.");
    expect(saveButton()).toBeEnabled();
  });

  it("успех: запрос на предложение строки с обрезанными пробелами, окно закрывается, кэши очереди, семей и контекстов сброшены", async () => {
    const user = userEvent.setup();
    const { onClose, invalidate } = renderDialog();
    await waitForDialogFocus();

    await user.type(definitionInput(), "  Входит: шпонки. Не входит: мастики.  ");
    await user.click(saveButton());

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(handlerState.createFamilyRequests).toEqual([
      {
        suggestionId: 11,
        body: { title: "Гидрошпонки", definition: "Входит: шпонки. Не входит: мастики." },
      },
    ]);
    const keys = invalidate.mock.calls.map((call) =>
      JSON.stringify((call[0] as { queryKey: unknown }).queryKey)
    );
    expect(keys).toEqual(expect.arrayContaining([JSON.stringify(["semantic-queue","suggestions"]), JSON.stringify(["semantic-queue","jobs"]), JSON.stringify(["semantic-queue","status"])]));
    expect(keys).toContain(JSON.stringify(["work-families"]));
    expect(keys).toContain(JSON.stringify(["semantic-contexts"]));
  });

  it("409 family_exists со ссылкой: сообщение и «Открыть семью» ведёт на семью из тела ответа", async () => {
    const user = userEvent.setup();
    handlerState.createFamilyOutcome = "exists";
    const onOpenFamily = vi.fn();
    const { onClose } = renderDialog(vi.fn(), onOpenFamily);
    await waitForDialogFocus();

    await user.type(definitionInput(), "Входит: шпонки.");
    await user.click(saveButton());

    expect(await screen.findByTestId("family-exists")).toHaveTextContent("Такая семья уже есть");
    expect(onClose).not.toHaveBeenCalled();
    // Отказ разбирает окно, а не общий тост с сырым текстом сервера.
    expect(screen.queryByText("такая семья уже есть")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Открыть семью" }));
    expect(onOpenFamily).toHaveBeenCalledWith(43);
  });

  it("409 family_exists без family_id: то же сообщение, ссылки нет", async () => {
    const user = userEvent.setup();
    handlerState.createFamilyOutcome = "exists_null";
    renderDialog(vi.fn(), vi.fn());
    await waitForDialogFocus();

    await user.type(definitionInput(), "Входит: шпонки.");
    await user.click(saveButton());

    expect(await screen.findByTestId("family-exists")).toHaveTextContent("Такая семья уже есть");
    expect(screen.queryByRole("button", { name: "Открыть семью" })).not.toBeInTheDocument();
  });

  it("«Отмена» закрывает окно и ничего не отправляет", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog();
    await waitForDialogFocus();
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onClose).toHaveBeenCalled();
    expect(handlerState.createFamilyRequests).toEqual([]);
  });
});

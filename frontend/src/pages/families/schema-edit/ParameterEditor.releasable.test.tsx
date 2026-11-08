import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { useFamilySchema } from "@/services/queries";
import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaEditDialog } from "../SchemaEditDialog";
import { draftOf } from "./drafts";

/**
 * Флажок «Можно отпустить» в редакторе параметра (лестница аналогов §2.20): только у главного,
 * признак уходит в теле правки только если его тронули, смена роли на второстепенную снимает его.
 */

const LATER = { timeout: 8000 };
const SAVE = { name: "Сохранить схему" };
const FLAG = { name: /Можно отпустить/ };

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

function sentParameters() {
  const sent = handlerState.schemaRequests.find((r) => r.action === "update");
  return (sent?.body as { parameters: Array<Record<string, unknown>> }).parameters;
}

describe("draftOf: признак «можно отпустить»", () => {
  it("черновик по сохранённому параметру ключа releasable не несёт: правка без касания его не шлёт", () => {
    const parameter = handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0];
    parameter.releasable = true;

    expect("releasable" in draftOf(parameter)).toBe(false);
  });
});

describe("ParameterEditor: «Можно отпустить»", () => {
  it("у главного параметра флажок есть, у размеченного отмечен и подписи «не размечен» нет", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable = true;
    const dialog = await opened();

    expect(within(dialog).getByRole("checkbox", FLAG)).toBeChecked();
    expect(within(dialog).queryByText("не размечен")).not.toBeInTheDocument();
  });

  it("неразмеченный: флажок не отмечен, подпись «не размечен»", async () => {
    const dialog = await opened();

    expect(within(dialog).getByRole("checkbox", FLAG)).not.toBeChecked();
    expect(within(dialog).getByText("не размечен")).toBeInTheDocument();
  });

  it("у второстепенного флажка нет; смена роли убирает его и снимает признак", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable = true;
    const dialog = await opened();
    expect(within(dialog).getByRole("checkbox", FLAG)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Второстепенный" }));
    expect(within(dialog).queryByRole("checkbox", FLAG)).not.toBeInTheDocument();

    // Назад в главные: признак снят, а не вернулся прежним.
    await user.click(within(dialog).getByRole("button", { name: "Главный" }));
    expect(within(dialog).getByRole("checkbox", FLAG)).not.toBeChecked();
  });

  it("правка другого поля без касания флажка не шлёт releasable", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable = true;
    const dialog = await opened();

    await user.type(within(dialog).getByLabelText("Новое значение"), "ПВХ{Enter}");
    await waitFor(() => expect(within(dialog).getByRole("button", SAVE)).toBeEnabled(), LATER);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests.some((r) => r.action === "update")).toBe(true), LATER);
    for (const parameter of sentParameters()) expect("releasable" in parameter).toBe(false);
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable).toBe(true);
  });

  it("касание флажка шлёт значение: отметили - true, у нетронутого параметра ключа нет", async () => {
    const user = userEvent.setup();
    const dialog = await opened();

    await user.click(within(dialog).getByRole("checkbox", FLAG));
    await waitFor(() => expect(within(dialog).getByRole("button", SAVE)).toBeEnabled(), LATER);
    await user.click(within(dialog).getByRole("button", SAVE));

    await waitFor(() => expect(handlerState.schemaRequests.some((r) => r.action === "update")).toBe(true), LATER);
    const [first, second] = sentParameters();
    expect(first.releasable).toBe(true);
    expect("releasable" in second).toBe(false);
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable).toBe(true);
  });

  it("снять отмеченный флажок - явный false (обязателен); предпросмотр получает то же тело", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].releasable = true;
    const dialog = await opened();

    await user.click(within(dialog).getByRole("checkbox", FLAG));
    await waitFor(() => expect(within(dialog).getByRole("button", SAVE)).toBeEnabled(), LATER);

    const lastPreview = handlerState.schemaEditPreviews.at(-1)!;
    expect((lastPreview.body.parameters as Array<Record<string, unknown>>)[0].releasable).toBe(false);
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import {
  VAT_BASE_ASSIGN_LABEL,
  VAT_BASE_FIELD_LABEL,
  VAT_BASE_SAVE_FALLBACK,
  VAT_BASE_SAVE_LABEL,
} from "./ourPriceCopy";
import { VatBaseDialog } from "./VatBaseDialog";

/**
 * Диалог «Назначить базу НДС» (спека 4б §2.12): одно поле ставки строкой, право `admin`,
 * отказ сервера в обеих формах `detail` показывается в самом диалоге.
 */

const PATCH_URL = "/api/v1/estimates/:id/vat";

const admin = { id: 1, email: "admin@example.test", role: "admin" as const };
const member = { id: 2, email: "member@example.test", role: "member" as const };

function renderDialog(initialUser: { id: number; email: string; role: "admin" | "member" } = admin) {
  return renderWithProviders(<VatBaseDialog estimateId={9003} tenderId={1} roundId={2} />, { initialUser });
}

const patches = () => handlerState.kpFileRequests.filter((r) => r.kind === "vat");

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: VAT_BASE_ASSIGN_LABEL }));
  await waitForDialogFocus();
}

async function submit(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText(VAT_BASE_FIELD_LABEL), text);
  await user.click(screen.getByRole("button", { name: VAT_BASE_SAVE_LABEL }));
}

describe("VatBaseDialog", () => {
  it("member: ни кнопки, ни диалога", () => {
    renderDialog(member);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("admin: кнопка есть, диалог закрыт до нажатия", async () => {
    renderDialog();
    expect(await screen.findByRole("button", { name: VAT_BASE_ASSIGN_LABEL })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("ставка с запятой уходит строкой с точкой: PATCH на смету, тело только base_override", async () => {
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "12,5");
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].method).toBe("PATCH");
    expect(new URL(patches()[0].url).pathname).toBe("/api/v1/estimates/9003/vat");
    expect(patches()[0].body).toEqual({ base_override: "12.5" });
  });

  it("длинная ставка доходит как есть: строка, не число", async () => {
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "22.000000000000000000001");
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toEqual({ base_override: "22.000000000000000000001" });
  });

  it("значение вне 0–100 клиент не отсекает: проверка на сервере", async () => {
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "150");
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toEqual({ base_override: "150" });
  });

  it("пустое поле ничего не отправляет", async () => {
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    expect(screen.getByRole("button", { name: VAT_BASE_SAVE_LABEL })).toBeDisabled();
    expect(patches()).toHaveLength(0);
  });

  it("успех закрывает диалог", async () => {
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "20");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("отказ строкой (без code): текст сервера в диалоге, диалог остаётся открытым", async () => {
    server.use(http.patch(PATCH_URL, () => HttpResponse.json({ detail: "Смета не найдена." }, { status: 404 })));
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "20");
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Смета не найдена.");
  });

  it("отказ валидации 422 (detail-массив): сообщение поля в диалоге", async () => {
    server.use(
      http.patch(PATCH_URL, () =>
        HttpResponse.json(
          {
            detail: [
              {
                type: "less_than_equal",
                loc: ["body", "base_override"],
                msg: "Input should be less than or equal to 100",
                input: "150",
              },
            ],
          },
          { status: 422 }
        )
      )
    );
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "150");
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Input should be less than or equal to 100");
  });

  it("отказ без текста: запасная подпись", async () => {
    server.use(http.patch(PATCH_URL, () => HttpResponse.json({}, { status: 500 })));
    const user = userEvent.setup();
    renderDialog();
    await open(user);
    await submit(user, "20");
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(VAT_BASE_SAVE_FALLBACK);
  });
});

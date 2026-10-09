import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { RemoveAwardDialog } from "./RemoveAwardDialog";
import { sampleTenderAward } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

/** «Снять отметку?» (макет, экран 3): удаление без следа, текст экрана дословно. */
async function openDialog(onOpenChange: (open: boolean) => void = () => {}) {
  const user = userEvent.setup();
  renderWithProviders(<RemoveAwardDialog tenderId={300} award={sampleTenderAward} onOpenChange={onOpenChange} />);
  await waitForDialogFocus("alertdialog");
  return user;
}

describe("RemoveAwardDialog", () => {
  it("текст экрана 3", async () => {
    await openDialog();

    expect(screen.getByRole("alertdialog", { name: "Снять отметку?" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Отметка удаляется без следа — это для исправления ошибки. Если договор с победителем не заключили, закройте окно и выберите «Договор не заключён», чтобы это осталось в истории."
      )
    ).toBeInTheDocument();
  });

  it("подтверждение шлёт DELETE по id отметки и закрывает окно", async () => {
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));

    await user.click(screen.getByRole("button", { name: "Снять отметку" }));

    await waitFor(() => expect(handlerState.lastAwardCommand).not.toBeNull());
    expect(handlerState.lastAwardCommand).toMatchObject({ kind: "remove", tenderId: 300, awardId: 7 });
    await waitFor(() => expect(closed).toContain(false));
  });

  // Ревью задачи 9: повторное нажатие, пока снятие идёт, недоступно.
  it("пока снятие уходит на сервер, «Снять отметку» недоступна", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.delete("/api/v1/tenders/:id/awards/:aid", async () => {
        await gate;
        return HttpResponse.json({});
      })
    );
    try {
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Снять отметку" }));

      await waitFor(() => expect(screen.getByRole("button", { name: "Снять отметку" })).toBeDisabled());
    } finally {
      release();
    }
  });

  it("«Отмена» закрывает окно без запроса", async () => {
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));

    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(closed).toContain(false);
    expect(handlerState.lastAwardCommand).toBeNull();
  });

  it("отказ сервера показан его текстом, окно остаётся открытым", async () => {
    server.use(
      http.delete("/api/v1/tenders/:id/awards/:aid", () =>
        HttpResponse.json(
          { detail: "Нельзя: по этой отметке заключён договор № 45/2026-ГП. Сначала удалите договор или отвяжите его от тендера." },
          { status: 409 }
        )
      )
    );
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));

    await user.click(screen.getByRole("button", { name: "Снять отметку" }));

    expect(await screen.findByText(/по этой отметке заключён договор № 45\/2026-ГП/)).toBeInTheDocument();
    expect(closed).not.toContain(false);
    expect(screen.getByRole("alertdialog", { name: "Снять отметку?" })).toBeInTheDocument();
  });
});

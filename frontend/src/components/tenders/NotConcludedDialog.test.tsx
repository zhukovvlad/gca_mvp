import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { NotConcludedDialog } from "./NotConcludedDialog";
import { sampleTenderAward } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

/**
 * «Договор не заключён» (макет, экран 3): дата обязательна, комментарий нет;
 * пустой комментарий уходит `null`, а не пустой строкой.
 */
async function openDialog(onOpenChange: (open: boolean) => void = () => {}) {
  const user = userEvent.setup();
  renderWithProviders(<NotConcludedDialog tenderId={300} award={sampleTenderAward} onOpenChange={onOpenChange} />);
  await waitForDialogFocus();
  return user;
}

describe("NotConcludedDialog", () => {
  it("тексты макета: заголовок и пояснение с именем победителя", async () => {
    await openDialog();

    expect(screen.getByRole("dialog", { name: "Договор не заключён" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "ТОО Монолит перестаёт быть победителем. Запись останется в истории тендера, после чего можно отметить другого участника."
      )
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("необязательно")).toBeInTheDocument();
  });

  it("без даты «Записать» недоступна и запрос не уходит; с датой — доступна", async () => {
    const user = await openDialog();
    const submit = screen.getByRole("button", { name: "Записать" });

    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(handlerState.lastAwardCommand).toBeNull();

    await user.type(screen.getByLabelText("Дата"), "2026-01-15");
    expect(submit).toBeEnabled();
  });

  it("отправка формы в обход кнопки без даты запроса не шлёт: уйдёт ровно одна команда — с датой", async () => {
    const sent: string[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/not-concluded")) sent.push(request.url);
    };
    server.events.on("request:start", listener);
    try {
      const user = await openDialog();
      const form = screen.getByLabelText("Дата").closest("form") as HTMLFormElement;

      fireEvent.submit(form);
      await user.type(screen.getByLabelText("Дата"), "2026-01-15");
      fireEvent.submit(form);

      await waitFor(() => expect(handlerState.lastAwardCommand).not.toBeNull());
      expect(handlerState.lastAwardCommand?.body).toEqual({ not_concluded_on: "2026-01-15", note: null });
      expect(sent).toHaveLength(1);
    } finally {
      server.events.removeListener("request:start", listener);
    }
  });

  it("пустой комментарий уходит null; дата и id отметки — в команду; окно закрывается", async () => {
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));

    await user.type(screen.getByLabelText("Дата"), "2026-01-15");
    await user.click(screen.getByRole("button", { name: "Записать" }));

    await waitFor(() => expect(handlerState.lastAwardCommand).not.toBeNull());
    expect(handlerState.lastAwardCommand).toEqual({
      kind: "not_concluded",
      tenderId: 300,
      awardId: 7,
      body: { not_concluded_on: "2026-01-15", note: null },
    });
    await waitFor(() => expect(closed).toContain(false));
  });

  it("комментарий из одних пробелов — тоже null", async () => {
    const user = await openDialog();
    await user.type(screen.getByLabelText("Дата"), "2026-01-15");
    await user.type(screen.getByLabelText("Комментарий"), "   ");
    await user.click(screen.getByRole("button", { name: "Записать" }));
    await waitFor(() => expect(handlerState.lastAwardCommand?.body).toEqual({ not_concluded_on: "2026-01-15", note: null }));
  });

  it("комментарий уходит текстом", async () => {
    const user = await openDialog();
    await user.type(screen.getByLabelText("Дата"), "2026-01-15");
    await user.type(screen.getByLabelText("Комментарий"), " Не согласовали аванс ");
    await user.click(screen.getByRole("button", { name: "Записать" }));

    await waitFor(() =>
      expect(handlerState.lastAwardCommand?.body).toEqual({
        not_concluded_on: "2026-01-15",
        note: "Не согласовали аванс",
      })
    );
  });

  // Ревью задачи 9: защита от повторной записи — и кнопкой, и обработчиком
  // формы; каждая проверена своим входом.
  function holdNotConcluded() {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/tenders/:id/awards/:aid/not-concluded", async ({ request }) => {
        await request.json();
        await gate;
        return HttpResponse.json({});
      })
    );
    return () => release();
  }

  it("пока запись уходит, «Записать» недоступна", async () => {
    const release = holdNotConcluded();
    try {
      const user = await openDialog();
      await user.type(screen.getByLabelText("Дата"), "2026-01-15");
      await user.click(screen.getByRole("button", { name: "Записать" }));

      await waitFor(() => expect(screen.getByRole("button", { name: "Записать" })).toBeDisabled());
    } finally {
      release();
    }
  });

  it("повторная отправка формы, пока запись уходит, второго запроса не шлёт", async () => {
    const sent: string[] = [];
    const listener = ({ request }: { request: Request }) => {
      if (request.url.includes("/not-concluded")) sent.push(request.url);
    };
    server.events.on("request:start", listener);
    const release = holdNotConcluded();
    try {
      const user = await openDialog();
      await user.type(screen.getByLabelText("Дата"), "2026-01-15");
      const form = screen.getByLabelText("Дата").closest("form") as HTMLFormElement;

      fireEvent.submit(form);
      await waitFor(() => expect(sent).toHaveLength(1));
      // Дать экрану перерисоваться с «идёт запись» — без опоры на кнопку.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      fireEvent.submit(form);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });

      expect(sent).toHaveLength(1);
    } finally {
      release();
      server.events.removeListener("request:start", listener);
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
      http.post("/api/v1/tenders/:id/awards/:aid/not-concluded", () =>
        HttpResponse.json(
          { detail: { code: "award_not_active", message: "Отметка уже не действующая — обновите карточку тендера." } },
          { status: 409 }
        )
      )
    );
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));

    await user.type(screen.getByLabelText("Дата"), "2026-01-15");
    await user.click(screen.getByRole("button", { name: "Записать" }));

    expect(await screen.findByText("Отметка уже не действующая — обновите карточку тендера.")).toBeInTheDocument();
    expect(closed).not.toContain(false);
    expect(screen.getByRole("dialog", { name: "Договор не заключён" })).toBeInTheDocument();
  });
});

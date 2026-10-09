import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { LinkContractDialog } from "./LinkContractDialog";
import { sampleTenderAward } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

/**
 * «Привязать существующий договор» (макет, экран 5): кандидаты радиогруппой,
 * сумма «… млн с НДС» либо «итог недоступен», пустой список — своим текстом.
 */
async function openDialog(onOpenChange: (open: boolean) => void = () => {}) {
  const user = userEvent.setup();
  renderWithProviders(<LinkContractDialog tenderId={300} award={sampleTenderAward} onOpenChange={onOpenChange} />);
  await waitForDialogFocus();
  return user;
}

describe("LinkContractDialog", () => {
  it("кандидаты — радиогруппа: номер, дата, объект, подрядчик, сумма с НДС", async () => {
    await openDialog();

    const first = await screen.findByRole("radio", { name: /№ 45\/2026-ГП от 26\.06\.2026/ });
    expect(first).toHaveAttribute("aria-checked", "false");
    const dialog = screen.getByRole("dialog", { name: "Привязать существующий договор" });
    expect(within(dialog).getByText(/ЖК Южный · ТОО Монолит · 9\s701 млн с НДС/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /№ 12\/2024 от 03\.04\.2024/ })).toBeInTheDocument();
    expect(within(dialog).getByText(/ЖК Южный · ТОО Монолит · итог недоступен/)).toBeInTheDocument();
  });

  it("пояснение к «итог недоступен» стоит, пока есть такой кандидат", async () => {
    await openDialog();
    await screen.findAllByRole("radio");

    expect(
      screen.getByText(/«Итог недоступен» — у договора нет сметы либо итог «с НДС» в файле не определён/)
    ).toBeInTheDocument();
  });

  it("у всех кандидатов итог есть — пояснения нет", async () => {
    handlerState.awardCandidates = handlerState.awardCandidates.slice(0, 1);
    await openDialog();
    await screen.findAllByRole("radio");

    expect(screen.queryByText(/«Итог недоступен»/)).toBeNull();
  });

  it("«Привязать» недоступна, пока ничего не выбрано; выбор — один; запрос несёт contract_id выбранного", async () => {
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));
    const submit = screen.getByRole("button", { name: "Привязать" });
    await screen.findAllByRole("radio");
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /№ 45\/2026-ГП/ }));
    await user.click(screen.getByRole("radio", { name: /№ 12\/2024/ }));
    expect(screen.getByRole("radio", { name: /№ 45\/2026-ГП/ })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /№ 12\/2024/ })).toHaveAttribute("aria-checked", "true");
    expect(submit).toBeEnabled();

    await user.click(submit);

    await waitFor(() => expect(handlerState.lastAwardCommand).not.toBeNull());
    expect(handlerState.lastAwardCommand).toEqual({
      kind: "link",
      tenderId: 300,
      awardId: 7,
      body: { contract_id: 202 },
    });
    await waitFor(() => expect(closed).toContain(false));
  });

  it("список пуст — «Нет подходящих договоров», выбирать нечего", async () => {
    handlerState.awardCandidates = [];
    await openDialog();

    expect(await screen.findByText("Нет подходящих договоров")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByRole("button", { name: "Привязать" })).toBeDisabled();
  });

  // Ревью задачи 9: повторная привязка, пока первая идёт, недоступна.
  it("пока привязка уходит на сервер, «Привязать» недоступна", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/tenders/:id/awards/:aid/link", async () => {
        await gate;
        return HttpResponse.json({});
      })
    );
    try {
      const user = await openDialog();
      await user.click(await screen.findByRole("radio", { name: /№ 12\/2024/ }));
      await user.click(screen.getByRole("button", { name: "Привязать" }));

      await waitFor(() => expect(screen.getByRole("button", { name: "Привязать" })).toBeDisabled());
    } finally {
      release();
    }
  });

  // Ревью задачи 9: отказ загрузки списка назван, а не выглядит пустым окном.
  it("список кандидатов не загрузился — окно это называет", async () => {
    server.use(
      http.get("/api/v1/tenders/:id/awards/:aid/contract-candidates", () =>
        HttpResponse.json({ detail: "Ошибка" }, { status: 500 })
      )
    );
    await openDialog();

    expect(await screen.findByText("Не удалось загрузить список договоров.")).toBeInTheDocument();
    expect(screen.queryByText("Нет подходящих договоров")).toBeNull();
  });

  it("«Отмена» закрывает окно без запроса", async () => {
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));
    await screen.findAllByRole("radio");

    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(closed).toContain(false);
    expect(handlerState.lastAwardCommand).toBeNull();
  });

  it("отказ сервера показан его текстом, окно остаётся открытым, выбор сохраняется", async () => {
    server.use(
      http.post("/api/v1/tenders/:id/awards/:aid/link", () =>
        HttpResponse.json(
          { detail: "Договор № 12/2024 уже привязан к тендеру № Т-2026-001." },
          { status: 409 }
        )
      )
    );
    const closed: boolean[] = [];
    const user = await openDialog((open) => closed.push(open));
    await user.click(await screen.findByRole("radio", { name: /№ 12\/2024/ }));

    await user.click(screen.getByRole("button", { name: "Привязать" }));

    expect(await screen.findByText("Договор № 12/2024 уже привязан к тендеру № Т-2026-001.")).toBeInTheDocument();
    expect(closed).not.toContain(false);
    expect(screen.getByRole("radio", { name: /№ 12\/2024/ })).toHaveAttribute("aria-checked", "true");
  });
});

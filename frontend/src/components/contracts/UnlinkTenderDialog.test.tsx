import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { UnlinkTenderDialog } from "./UnlinkTenderDialog";
import { qk } from "@/services/queryKeys";
import { linkedContractCard } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders, waitForDialogFocus } from "@/test/utils";

const contract = linkedContractCard("uploaded_separately");

describe("Окно «Отвязать от тендера» (спека Б2 §2.8)", () => {
  it("называет договор и тендер и объясняет, что останется", async () => {
    renderWithProviders(<UnlinkTenderDialog contract={contract} onOpenChange={() => {}} />);

    expect(
      await screen.findByText("Отвязать договор «ГП-2026-001» от тендера № Т-2026-001?")
    ).toBeInTheDocument();
    expect(screen.getByText(/Договор и его смета останутся/)).toBeInTheDocument();
  });

  it("без договора окна нет", () => {
    renderWithProviders(<UnlinkTenderDialog contract={null} onOpenChange={() => {}} />);

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("подтверждение снимает основание и закрывает окно", async () => {
    const user = userEvent.setup();
    const opened: boolean[] = [];
    renderWithProviders(
      <UnlinkTenderDialog contract={contract} onOpenChange={(value) => opened.push(value)} />
    );
    await waitForDialogFocus("alertdialog");

    await user.click(screen.getByRole("button", { name: "Отвязать" }));

    await waitFor(() => expect(handlerState.lastUnlinkedContractId).toBe(100));
    await waitFor(() => expect(opened).toContain(false));
  });

  it("отмена ничего не отправляет", async () => {
    const user = userEvent.setup();
    const opened: boolean[] = [];
    renderWithProviders(
      <UnlinkTenderDialog contract={contract} onOpenChange={(value) => opened.push(value)} />
    );
    await waitForDialogFocus("alertdialog");

    await user.click(screen.getByRole("button", { name: "Отмена" }));

    await waitFor(() => expect(opened).toContain(false));
    expect(handlerState.lastUnlinkedContractId).toBeNull();
  });

  it("отказ сервера показан его текстом, окно остаётся открытым", async () => {
    server.use(
      http.delete("/api/v1/contracts/:id/tender-award", () =>
        HttpResponse.json(
          { detail: "Импорт сметы этого договора выполняется (задание 512). Дождитесь завершения и повторите." },
          { status: 409 }
        )
      )
    );
    const user = userEvent.setup();
    const opened: boolean[] = [];
    renderWithProviders(
      <UnlinkTenderDialog contract={contract} onOpenChange={(value) => opened.push(value)} />
    );
    await waitForDialogFocus("alertdialog");

    await user.click(screen.getByRole("button", { name: "Отвязать" }));

    expect(await screen.findByText(/Импорт сметы этого договора выполняется/)).toBeInTheDocument();
    expect(opened).not.toContain(false);
  });

  it("после отвязки устаревают карточка договора и карточка тендера основания", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    // `gcTime: 0` у тестового клиента убрал бы записи без наблюдателей; ставим
    // долгий срок именно этим двум ключам, чтобы прочитать их состояние.
    queryClient.setQueryDefaults(qk.contracts.card(100), { gcTime: 60_000 });
    queryClient.setQueryDefaults(qk.tenders.card(300), { gcTime: 60_000 });
    queryClient.setQueryData(qk.contracts.card(100), contract);
    queryClient.setQueryData(qk.tenders.card(300), { id: 300 });
    renderWithProviders(<UnlinkTenderDialog contract={contract} onOpenChange={() => {}} />, {
      queryClient,
    });
    await waitForDialogFocus("alertdialog");

    await user.click(screen.getByRole("button", { name: "Отвязать" }));

    await waitFor(() =>
      expect(queryClient.getQueryState(qk.contracts.card(100))?.isInvalidated).toBe(true)
    );
    expect(queryClient.getQueryState(qk.tenders.card(300))?.isInvalidated).toBe(true);
  });
});

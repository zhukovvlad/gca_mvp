import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ParticipantDeleteDialog } from "./ParticipantDeleteDialog";
import { handlerState } from "@/test/handlers";
import { sampleTenderCard } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

/**
 * Протокол `confirmation_token` (спека §2.11, задача 12): первый запрос — БЕЗ
 * токена — сервер ВСЕГДА отвечает 409 с preview состава; кнопка отправляет
 * ВТОРОЙ запрос уже с токеном preview. Три развилки второго ответа:
 * успех (204), "stale" (409 с НОВЫМ токеном — состав успел измениться) и
 * активный импорт раунда (409 `active_import`, кнопки удаления нет вовсе).
 */
const participant = sampleTenderCard.participants[0];

describe("ParticipantDeleteDialog — протокол confirmation_token (§2.11)", () => {
  it("открытие запрашивает preview и показывает состав удаляемого", async () => {
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />
    );

    expect(await screen.findByText(/Раундов:\s*2/)).toBeInTheDocument();
    expect(screen.getByText(/Смет:\s*2/)).toBeInTheDocument();
    expect(screen.getByText(/Позиций:\s*1\s*830/)).toBeInTheDocument();
    expect(screen.getByText(/вручную:\s*3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Удалить участника" })).toBeEnabled();
  });

  it("нажатие шлёт токен; 204 закрывает диалог", async () => {
    const user = userEvent.setup();
    let closed = false;
    renderWithProviders(
      <ParticipantDeleteDialog
        tenderId={300}
        participant={participant}
        onOpenChange={(open) => {
          if (!open) closed = true;
        }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "Удалить участника" }));
    await waitFor(() => expect(closed).toBe(true));
  });

  it('"stale" — после нажатия показывает обновлённый preview и НЕ закрывается', async () => {
    handlerState.participantDeleteOutcome = "stale";
    const user = userEvent.setup();
    let closed = false;
    renderWithProviders(
      <ParticipantDeleteDialog
        tenderId={300}
        participant={participant}
        onOpenChange={(open) => {
          if (!open) closed = true;
        }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "Удалить участника" }));
    // Свежий preview снова на экране — тот же состав, но диалог не закрылся.
    expect(await screen.findByText(/Раундов:\s*2/)).toBeInTheDocument();
    expect(closed).toBe(false);
  });

  it('"active" — «Импорт раунда выполняется» без кнопки удаления', async () => {
    handlerState.participantDeleteOutcome = "active";
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />
    );

    expect(await screen.findByText(/Импорт раунда выполняется — дождитесь/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить участника" })).toBeNull();
  });
});

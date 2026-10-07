import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { PendingFamily } from "@/types/domain";

import { PendingFamilyBlock } from "./PendingFamilyBlock";

/**
 * Блок «Ожидает семьи» карточки контекста (спека `2026-10-02-catalog-variants-design.md`
 * §2.12): кто поставил, когда, порог автопринятия, «Отменить».
 */

const CONTEXT_ID = 601;
const LATER = { timeout: 8000 };

function manualPending(overrides: Partial<PendingFamily> = {}): PendingFamily {
  return {
    family_id: 43,
    family_title: "Кровельные работы",
    source: "manual",
    by: 1,
    at: "2026-10-05T09:30:00+00:00",
    threshold: null,
    suggestion_id: null,
    ...overrides,
  };
}

function autoPending(): PendingFamily {
  return manualPending({ source: "auto_suggestion", by: null, threshold: "0.95", suggestion_id: 31 });
}

function renderBlock(pending: PendingFamily) {
  return renderWithProviders(<PendingFamilyBlock contextId={CONTEXT_ID} pending={pending} />);
}

describe("PendingFamilyBlock: чтение", () => {
  it("называет семью, человека, дату и то, чего блок ждёт", () => {
    renderBlock(manualPending());

    expect(screen.getByText("Ожидает семьи: «Кровельные работы»")).toBeInTheDocument();
    expect(screen.getByText(/поставил оператор/)).toBeInTheDocument();
    expect(screen.getByText(/05\.10\.2026/)).toBeInTheDocument();
    expect(screen.getByText("ожидает значений по схеме цели")).toBeInTheDocument();
    expect(screen.queryByText(/порог/)).not.toBeInTheDocument();
  });

  it("у автоматического принятия — «автоматически» и порог, которым оно принято", () => {
    renderBlock(autoPending());

    expect(screen.getByText(/поставлено автоматически/)).toBeInTheDocument();
    expect(screen.getByText(/порог 0,95/)).toBeInTheDocument();
    expect(screen.queryByText(/поставил оператор/)).not.toBeInTheDocument();
  });

  it("коды источников на экран не выходят", () => {
    renderBlock(autoPending());

    expect(document.body.textContent).not.toMatch(/auto_suggestion|\bmanual\b|\bsuggestion\b/);
  });
});

describe("PendingFamilyBlock: «Отменить»", () => {
  it("шлёт DELETE по контексту и перечитывает карточку", async () => {
    const user = userEvent.setup();
    renderBlock(manualPending());

    await user.click(screen.getByRole("button", { name: "Отменить ожидание семьи" }));

    await waitFor(() => expect(handlerState.cancelPendingRequests).toEqual([CONTEXT_ID]), LATER);
  });

  it("отказ сервера выходит подписью, а не кодом", async () => {
    handlerState.contextRefusal = { action: "cancel-pending", code: "context_archived", status: 409 };
    const user = userEvent.setup();
    renderBlock(manualPending());

    await user.click(screen.getByRole("button", { name: "Отменить ожидание семьи" }));

    expect(await screen.findByText("Контекст в архиве: менять его нельзя.", {}, LATER)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("context_archived");
  });

  it("неизвестный код отказа — общая подпись", async () => {
    handlerState.contextRefusal = { action: "cancel-pending", code: "some_future_code", status: 409 };
    const user = userEvent.setup();
    renderBlock(manualPending());

    await user.click(screen.getByRole("button", { name: "Отменить ожидание семьи" }));

    expect(
      await screen.findByText("Не удалось выполнить действие. Обновите экран и повторите.", {}, LATER)
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("some_future_code");
  });
});

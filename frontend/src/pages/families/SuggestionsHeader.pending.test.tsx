import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";
import type { HeldBatchInfo, QueueStatus } from "@/types/domain";

import { SuggestionsHeader } from "./SuggestionsHeader";

/** Состояния ожидания шапки «Предложений»: кнопки плашек на время запроса. */

function status(overrides: Partial<QueueStatus> = {}): QueueStatus {
  return {
    spent_24h_usd: "4.2",
    daily_budget_usd: "30",
    claim_paused: null,
    held_batches: [],
    stale_units: [],
    config_stale: null,
    catalog_to_review: 0,
    catalog_position: 0,
    contexts_with_variant: 0,
    contexts_pending: 0,
    families_without_schema: 0,
    ...overrides,
  };
}

function heldBatch(overrides: Partial<HeldBatchInfo> = {}): HeldBatchInfo {
  return {
    batch_id: 7,
    source: "import",
    import_job_id: 21,
    unit_id: null,
    contexts_count: 4210,
    reserve_usd: "17.6",
    expected_cached_usd: "6.05",
    created_at: "2026-09-28T10:00:00+00:00",
    ...overrides,
  };
}

describe("SuggestionsHeader — ожидание действий", () => {
  it("«Снять остановку»: на время запроса кнопка занята, ответ пришёл — снова доступна", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/worker/resume");
    renderWithProviders(
      <SuggestionsHeader
        status={status({ claim_paused: { reason: "reserve_below_actual", attempt_id: 5, paused_at: "2026-09-28T09:00:00+00:00" } })}
        onPreview={vi.fn()}
      />
    );
    const button = within(screen.getByTestId("banner-paused")).getByRole("button", { name: "Снять остановку" });

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();

    hold.release();
    await waitFor(() => expect(button).not.toHaveAttribute("aria-busy"));
    expect(button).toBeEnabled();
  });

  it("«Отбросить» удержанной пачки: на время запроса кнопка занята", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/batches/:id/discard");
    renderWithProviders(<SuggestionsHeader status={status({ held_batches: [heldBatch()] })} onPreview={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Отбросить" });

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();

    hold.release();
    await waitFor(() => expect(button).not.toHaveAttribute("aria-busy"));
  });
});

describe("SuggestionsHeader — одна мутация на несколько плашек", () => {
  it("«Отбросить» удержанной пачки: крутится только нажатая плашка, соседняя заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/batches/:id/discard");
    renderWithProviders(
      <SuggestionsHeader
        status={status({ held_batches: [heldBatch(), heldBatch({ batch_id: 8, import_job_id: 22 })] })}
        onPreview={vi.fn()}
      />
    );
    const banners = screen.getAllByTestId("banner-held");

    await user.click(within(banners[0]).getByRole("button", { name: "Отбросить" }));

    await waitFor(() =>
      expect(within(banners[0]).getByRole("button", { name: "Отбросить" })).toHaveAttribute("aria-busy", "true")
    );
    const neighbour = within(banners[1]).getByRole("button", { name: "Отбросить" });
    expect(neighbour).not.toHaveAttribute("aria-busy");
    expect(neighbour).toBeDisabled();
    hold.release();
  });
});

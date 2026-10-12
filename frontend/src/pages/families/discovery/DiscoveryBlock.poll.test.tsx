import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FETCHING_BAR_DELAY_MS } from "@/pages/families/pending";
import { discoveryUnitFixture, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

import { DiscoveryBlock } from "./DiscoveryBlock";

/** Фоновое перечитывание блока «Открыть семьи»: полоса только у медленного ответа. */

const unitLabel = (code: string | null) => code ?? "без единицы";
const bar = () => screen.queryByRole("progressbar", { name: "Обновление данных" });

describe("DiscoveryBlock — полоса перечитывания", () => {
  it("фоновый опрос быстрее задержки: полосы нет; медленный — есть, затем пропадает", async () => {
    handlerState.discovery.units = [discoveryUnitFixture({ systems: 5 })];
    const queryClient = createTestQueryClient();
    renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} />, { queryClient });
    await screen.findByTestId("discovery-block");

    // Ответ короче задержки: пока запрос в пути, полосы ещё нет.
    const quick = holdRequests("get", "/api/v1/semantic/discovery/units");
    void queryClient.invalidateQueries({ queryKey: ["discovery"] });
    await waitFor(() => expect(quick.seen.count).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, FETCHING_BAR_DELAY_MS / 4));
    expect(bar()).not.toBeInTheDocument();
    quick.release();
    await waitFor(() => expect(handlerState.discovery.unitsRequests).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, FETCHING_BAR_DELAY_MS + 100));
    expect(bar()).not.toBeInTheDocument();

    const hold = holdRequests("get", "/api/v1/semantic/discovery/units");
    void queryClient.invalidateQueries({ queryKey: ["discovery"] });
    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getByTestId("discovery-block")).toBeInTheDocument();
    hold.release();
    await waitFor(() => expect(bar()).not.toBeInTheDocument());
  });
});

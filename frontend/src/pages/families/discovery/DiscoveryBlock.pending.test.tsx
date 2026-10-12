import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { discoveryUnitFixture, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import { DiscoveryBlock } from "./DiscoveryBlock";

/** Первая загрузка блока «Открыть семьи»: заглушка до ответа, затем строки; пустого блока без ответа нет. */

const unitLabel = (code: string | null) => (code === "M3" ? "м³" : (code ?? "без единицы"));

describe("DiscoveryBlock — первая загрузка", () => {
  it("до ответа заглушка, после непустого ответа — строка единицы без заглушки", async () => {
    handlerState.discovery.units = [discoveryUnitFixture({ systems: 5 })];
    const hold = holdRequests("get", "/api/v1/semantic/discovery/units");
    renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByTestId("discovery-block")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("пустой ответ: ни заглушки, ни блока", async () => {
    server.use(http.get("/api/v1/semantic/discovery/units", () => HttpResponse.json({ units: [] })));
    const hold = holdRequests("get", "/api/v1/semantic/discovery/units");
    renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} />);
    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();

    hold.release();
    await waitFor(() => expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument());
    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();
  });
});

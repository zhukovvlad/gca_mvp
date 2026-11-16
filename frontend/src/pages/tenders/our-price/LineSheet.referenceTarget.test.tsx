import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  referenceChoiceFixture,
  referenceLineViewFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { TargetChoice } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ADMIN = { id: 1, email: "admin@example.test", role: "admin" } as const;
const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;

const props: LineSheetProps = {
  tenderId: OUR_PRICE_TENDER_ID,
  roundId: OUR_PRICE_ROUND_ID,
  offerId: OUR_PRICE_OFFER_ID,
  positionItemId: OUR_PRICE_LINE_ID,
  seriesId: null,
  vat: "net",
  vatRate: null,
  onClose: () => {},
};

function serve(choice: TargetChoice | null) {
  server.use(
    http.get(LINE_PATH, () =>
      HttpResponse.json(
        referenceLineViewFixture({
          zones: [],
          line: choice === null ? {} : { choice, target_kind: choice.active ? "reference" : null },
        })
      )
    )
  );
}

const deletes = () =>
  handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.method === "DELETE" && r.url.includes("/target"));

describe("панель строки: карточка справочной цели", () => {
  it("у строки со справочной целью показана карточка, и выгрузка называет её источник", async () => {
    serve(referenceChoiceFixture({ origin: "zone" }));
    renderWithProviders(<LineSheet {...props} />, { initialUser: ADMIN });
    expect(await screen.findByTestId("reference-target")).toHaveTextContent("справочная · другая зона");
    expect(screen.getByTestId("export-reference-source")).toHaveTextContent("Объект Т, договор № Д-27");
  });

  it("у строки без справочной цели карточки нет", async () => {
    serve(null);
    renderWithProviders(<LineSheet {...props} />, { initialUser: ADMIN });
    await screen.findByTestId("sheet-export");
    expect(screen.queryByTestId("reference-target")).not.toBeInTheDocument();
  });

  it("«вернуть самую низкую» шлёт прежний DELETE цели", async () => {
    serve(referenceChoiceFixture());
    renderWithProviders(<LineSheet {...props} />, { initialUser: ADMIN });
    await userEvent.click(await screen.findByRole("button", { name: "вернуть самую низкую" }));
    await waitFor(() => expect(deletes()).toHaveLength(1));
  });

  it("у member кнопки возврата нет", async () => {
    serve(referenceChoiceFixture());
    renderWithProviders(<LineSheet {...props} />, { initialUser: MEMBER });
    await screen.findByTestId("reference-target");
    expect(screen.queryByRole("button", { name: "вернуть самую низкую" })).not.toBeInTheDocument();
  });

  it("сброшенная цель: причина сброса на месте карточки", async () => {
    serve(referenceChoiceFixture({ active: false, reset_reason: "reference_unavailable", reference: null }));
    renderWithProviders(<LineSheet {...props} />, { initialUser: ADMIN });
    expect(await screen.findByTestId("reference-target-inactive")).toHaveTextContent("больше не годится как справочная");
  });
});

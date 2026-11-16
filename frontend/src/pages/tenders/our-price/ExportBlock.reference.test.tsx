import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  agreedFixture,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  referenceChoiceFixture,
  refLinkRowFixture,
  ourPriceLineFixture,
  workLineFixture,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceLine, TargetChoice } from "@/types/domain";

import { ExportBlock } from "./ExportBlock";

function renderBlock(choice: TargetChoice, opts: { line?: Partial<OurPriceLine>; vatView?: "net" | "shown" } = {}) {
  return renderWithProviders(
    <ExportBlock
      tenderId={OUR_PRICE_TENDER_ID}
      roundId={OUR_PRICE_ROUND_ID}
      offerId={OUR_PRICE_OFFER_ID}
      line={workLineFixture({
        position_item_id: OUR_PRICE_LINE_ID,
        unit: "м3",
        choice,
        target_kind: "reference",
        file_ours: { net: "8372.92", shown: "10215.96" },
        ...opts.line,
      })}
      comment={{ kind: null, text: null, template_text: null }}
      canEdit
      vatView={opts.vatView ?? "net"}
      vatRate="22.00"
    />
  );
}

describe("блок «В файл подрядчику»: справочная цель", () => {
  it("подпись источника — происхождение справочной цели", () => {
    renderBlock(referenceChoiceFixture({ origin: "zone" }));
    expect(screen.getByTestId("export-source")).toHaveTextContent("наша расценка · цель: справочная · другая зона");
  });

  it("источник: договор, путь разделов, цитата номинала и единица договора", () => {
    renderBlock(referenceChoiceFixture());
    expect(screen.getByTestId("export-reference-source")).toHaveTextContent(
      "Объект Т, договор № Д-27, 2026 г.: сопоставимая работа (Благоустройство → МАФ), 8 372,92 ₽ за м3 без НДС"
    );
  });

  it("при пересчёте источник добавляет множитель, основание и цену в единице КП", () => {
    renderBlock(
      referenceChoiceFixture({
        origin: "link",
        reference: refLinkRowFixture(),
        conversion: { method: "link", link_id: 41, factor: "0.100", factor_shown: "0,1", basis: "толщина 100 мм" },
      })
    );
    expect(screen.getByTestId("export-reference-source")).toHaveTextContent(
      "1 107,24 ₽ за м2 без НДС; 1 м2 = 0,1 м3 (толщина 100 мм): 11 072,40 ₽ за м3 без НДС"
    );
  });

  it("цитата идёт в базе НДС КП", () => {
    renderBlock(referenceChoiceFixture(), { vatView: "shown" });
    expect(screen.getByTestId("export-reference-source")).toHaveTextContent("10 215,96 ₽ за м3 с НДС 22 %");
  });

  it("«Договорились» перекрывает цель: источника справочной цели нет", () => {
    renderBlock(referenceChoiceFixture(), { line: { agreed: agreedFixture() } });
    expect(screen.queryByTestId("export-reference-source")).not.toBeInTheDocument();
  });

  it("сброшенная справочная цель источника не печатает", () => {
    renderBlock(referenceChoiceFixture({ active: false, reset_reason: "reference_unavailable", reference: null }));
    expect(screen.queryByTestId("export-reference-source")).not.toBeInTheDocument();
  });

  it("обычная цель источника справочной цели не имеет", () => {
    renderWithProviders(
      <ExportBlock
        tenderId={OUR_PRICE_TENDER_ID}
        roundId={OUR_PRICE_ROUND_ID}
        offerId={OUR_PRICE_OFFER_ID}
        line={ourPriceLineFixture()}
        comment={{ kind: null, text: null, template_text: null }}
        canEdit
        vatView="net"
        vatRate="22.00"
      />
    );
    expect(screen.queryByTestId("export-reference-source")).not.toBeInTheDocument();
  });
});

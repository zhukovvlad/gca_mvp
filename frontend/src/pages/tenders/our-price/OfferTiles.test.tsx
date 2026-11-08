import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ourPriceLineFixture, ourPriceOfferFixture, workLineFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { MoneyPair, OurPriceOffer, OurPriceTotals } from "@/types/domain";

import { OfferTiles } from "./OfferTiles";
import { ParticipantCard } from "./ParticipantCard";
import { CARD_LABEL, FILTER_LABEL, freshestCoverageNote } from "./ourPriceCopy";
import { matchesFilter } from "./offerFilter";

const pair = (net: string, shown: string | null = net): MoneyPair => ({ net, shown });

function offerWith(totals: Partial<OurPriceTotals>, lines = ourPriceOfferFixture().lines): OurPriceOffer {
  const offer = ourPriceOfferFixture();
  const participant = { ...offer.participants[0], totals: { ...offer.participants[0].totals!, ...totals } };
  return { ...offer, participants: [participant], lines };
}

function renderTiles(offer: OurPriceOffer, filter: Parameters<typeof OfferTiles>[0]["filter"] = "all") {
  return renderWithProviders(<OfferTiles offer={offer} vat="net" filter={filter} onFilter={() => undefined} />);
}

describe("плитки разбора: пять плиток лестницы аналогов", () => {
  it("«КП на м²» и плитки: выше самых низких, выше самых свежих, ниже самой низкой, разные единицы, не с чем сравнить", () => {
    renderTiles(ourPriceOfferFixture());
    expect(screen.getByTestId("tile-per_sqm")).toBeInTheDocument();
    for (const key of ["above_lowest", "above_freshest", "below_lowest", "unit_mismatch", "not_comparable"] as const) {
      expect(screen.getByTestId(`tile-${key}`)).toHaveTextContent(FILTER_LABEL[key]);
    }
    expect(screen.getByTestId("tile-above_lowest")).toHaveTextContent("КП выше самых низких расценок");
    expect(screen.getByTestId("tile-above_freshest")).toHaveTextContent("КП выше самых свежих расценок");
    expect(screen.getByTestId("tile-below_lowest")).toHaveTextContent("Ниже самой низкой расценки");
    expect(screen.getByTestId("tile-unit_mismatch")).toHaveTextContent("Разные единицы");
    expect(screen.getByTestId("tile-above_lowest")).toHaveTextContent("+120 000,00");
    expect(screen.getByTestId("tile-above_freshest")).toHaveTextContent("+90 000,00");
  });

  it("щелчок по плитке свежих — фильтр, повторный — снятие", () => {
    const picked: string[] = [];
    renderWithProviders(
      <OfferTiles offer={ourPriceOfferFixture()} vat="net" filter="above_freshest" onFilter={(f) => picked.push(f)} />
    );
    expect(screen.getByTestId("tile-above_freshest")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("tile-above_freshest"));
    fireEvent.click(screen.getByTestId("tile-below_lowest"));
    expect(picked).toEqual(["all", "below_lowest"]);
  });
});

/**
 * Подпись покрытия самых свежих по таблице спеки §2.12: «null — не 0». Четыре случая, на
 * плитке и в карточке участника: пусто, нет датированных, часть, все.
 */
describe("покрытие самых свежих расценок (§2.12)", () => {
  const CASES = [
    {
      name: "нет сравнимых строк: «—» без подписи",
      totals: { comparable_lines: 0, freshest_lines: 0, over_freshest: null },
      money: "—",
      note: null,
    },
    {
      name: "сравнимые есть, датированных нет: «—» и «нет датированных расценок»",
      totals: { comparable_lines: 4, freshest_lines: 0, over_freshest: null },
      money: "—",
      note: "у сравнимых строк нет датированных расценок",
    },
    {
      name: "часть: сумма и «по 3 из 4 строк»",
      totals: { comparable_lines: 4, freshest_lines: 3, over_freshest: pair("7000.00", "8540.00") },
      money: "+7 000,00",
      note: "по 3 из 4 строк",
    },
    {
      name: "все: сумма без подписи",
      totals: { comparable_lines: 4, freshest_lines: 4, over_freshest: pair("9000.00", "10980.00") },
      money: "+9 000,00",
      note: null,
    },
  ] as const;

  it.each(CASES)("функция подписи: $name", ({ totals, note }) => {
    expect(freshestCoverageNote(totals)).toBe(note);
  });

  it.each(CASES)("плитка: $name", ({ totals, money, note }) => {
    renderTiles(offerWith(totals));
    const tile = screen.getByTestId("tile-above_freshest");
    expect(tile).toHaveTextContent(money);
    if (note === null) expect(screen.queryByTestId("freshest-coverage")).not.toBeInTheDocument();
    else expect(screen.getByTestId("freshest-coverage")).toHaveTextContent(note);
  });

  it.each(CASES)("карточка участника: $name", ({ totals, money, note }) => {
    const offer = offerWith(totals);
    renderWithProviders(
      <ParticipantCard tenderId={1} roundId={2} participant={offer.participants[0]} view="net" cheapest={false} href={null} />
    );
    const card = screen.getByTestId(`card-${offer.participants[0].offer_id}`);
    expect(card).toHaveTextContent(CARD_LABEL.overFreshest);
    expect(card).toHaveTextContent(money.replace("+", ""));
    if (note === null) expect(screen.queryByTestId("freshest-coverage")).not.toBeInTheDocument();
    else expect(screen.getByTestId("freshest-coverage")).toHaveTextContent(note);
  });

  it("ноль у непустого покрытия печатается нулём, а пустое покрытие — прочерком (null — не 0)", () => {
    renderTiles(offerWith({ comparable_lines: 2, freshest_lines: 2, over_freshest: pair("0.00") }));
    expect(screen.getByTestId("tile-above_freshest")).toHaveTextContent("0,00");
    expect(screen.getByTestId("tile-above_freshest")).not.toHaveTextContent("—");
  });
});

describe("фильтры строк по свежим расценкам", () => {
  it("«выше самых свежих»: сопоставимая строка с over_freshest > 0; null и ноль не входят", () => {
    const above = workLineFixture({ over_freshest: pair("5.00") });
    const zero = workLineFixture({ over_freshest: pair("0.00") });
    const none = workLineFixture({ over_freshest: null, freshest: null, to_freshest: null });
    const mismatch = workLineFixture({ state: "unit_mismatch", over_freshest: pair("5.00") });
    expect(matchesFilter(above, "above_freshest")).toBe(true);
    expect(matchesFilter(zero, "above_freshest")).toBe(false);
    expect(matchesFilter(none, "above_freshest")).toBe(false);
    expect(matchesFilter(mismatch, "above_freshest")).toBe(false);
  });

  it("«выше самых низких» и «ниже самой низкой» — по over_lowest и below", () => {
    expect(matchesFilter(ourPriceLineFixture({ over_lowest: pair("1.00") }), "above_lowest")).toBe(true);
    expect(matchesFilter(ourPriceLineFixture({ over_lowest: pair("0.00") }), "above_lowest")).toBe(false);
    expect(matchesFilter(ourPriceLineFixture({ below: pair("-1.00") }), "below_lowest")).toBe(true);
    expect(matchesFilter(ourPriceLineFixture({ below: null }), "below_lowest")).toBe(false);
  });

  it("новая причина no_zone — в «не с чем сравнить»", () => {
    expect(matchesFilter(workLineFixture({ state: "no_zone" }), "not_comparable")).toBe(true);
  });
});

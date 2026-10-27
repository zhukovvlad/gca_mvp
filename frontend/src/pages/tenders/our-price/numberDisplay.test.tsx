import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
  ourPriceLineViewFixture,
  ourPriceOfferFixture,
  ourPriceRoundFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { MoneyPair, OurPriceLine, OurPriceLineView, OurPriceOffer } from "@/types/domain";

import { LineSheet } from "./LineSheet";
import OurPriceOfferPage from "./OurPriceOfferPage";
import OurPriceRoundPage from "./OurPriceRoundPage";
import { SUB_KOPECK_LABEL } from "./ourPriceCopy";

/** Хвост, которым сервер отдаёт неокруглённые ставки и ₽/м² (спека 4а §2.9). */
const TAIL = "79309941425023643719";

const ROUND_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price";
const OFFER_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId";
const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const STAGE = `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price`;

const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });

const nbsp = new RegExp(String.fromCharCode(160), "g");

/** Каждое значение пары `{net, shown}` в дереве получает длинный хвост после запятой. */
function longFractions<T>(node: T): T {
  if (Array.isArray(node)) return node.map(longFractions) as T;
  if (node === null || typeof node !== "object") return node;
  const source = node as Record<string, unknown>;
  const isPair = typeof source.net === "string";
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (isPair && (key === "net" || key === "shown") && typeof value === "string") {
      out[key] = `${value.split(".")[0]}.${TAIL}`;
    } else {
      out[key] = longFractions(value);
    }
  }
  return out as T;
}

/** Все текстовые узлы и подсказки `title` страницы — то, что видит пользователь. */
function visibleStrings(): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    out.push((node.textContent ?? "").replace(nbsp, " "));
  }
  document.querySelectorAll("[title]").forEach((el) => out.push((el.getAttribute("title") ?? "").replace(nbsp, " ")));
  return out;
}

/** Больше двух цифр после запятой — или нетронутый хвост сервера — нигде на экране. */
function expectNoLongFractions() {
  const strings = visibleStrings();
  expect(strings.filter((s) => /\d,\d{3,}/.test(s))).toEqual([]);
  expect(strings.filter((s) => s.includes(TAIL.slice(0, 8)))).toEqual([]);
}

const text = (testId: string) => (screen.getByTestId(testId).textContent ?? "").replace(nbsp, " ");

describe("экран этапа: числа с длинной дробью", () => {
  it.each([
    ["net", "", "110 000,79", "500 000,79"],
    ["shown", "?vat=shown", "134 200,79", "610 000,79"],
  ])("вид %s: ячейки, вилка, карточка и итоги — не больше двух знаков", async (_view, search, perSqm, whole) => {
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(longFractions(ourPriceRoundFixture()))));
    renderWithProviders(
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price" element={<OurPriceRoundPage />} />
      </Routes>,
      { initialRoute: STAGE + search, initialUser: { id: 1, email: "u@example.com", role: "admin" } }
    );
    await screen.findByTestId("cell-11-7001");
    expect(text("cell-11-7001")).toContain(perSqm);
    expect(text("card-7001")).toContain(perSqm);
    // Общая колонка при разных ставках участников — нетто (спека 4а §2.9), в любом виде.
    expect(text("fork-11")).toContain("90 000,79 – 130 000,79");
    expect(text("cell-11-7001")).toContain(whole);
    expectNoLongFractions();
  });
});

function offerOf(lines: OurPriceLine[]): OurPriceOffer {
  return { ...longFractions(ourPriceOfferFixture()), lines };
}

function longLine(id: number, overrides: Partial<OurPriceLine> = {}): OurPriceLine {
  return longFractions(ourPriceLineFixture({ position_item_id: id, title: `Работа ${id}`, ...overrides }));
}

describe("разбор участника: числа с длинной дробью", () => {
  it.each([
    ["net", "?article=11", "110 000,79", "1 200,79", "900,79 – 1 300,79", "1 000,79", "+20 000,79 ₽"],
    ["shown", "?vat=shown&article=11", "134 200,79", "1 464,79", "1 098,79 – 1 586,79", "1 220,79", "+24 400,79 ₽"],
  ])("вид %s: плитка, полоса, статьи и строки", async (_view, search, perSqm, kp, fork, ours, toOurs) => {
    server.use(
      http.get(OFFER_PATH, () =>
        HttpResponse.json(
          offerOf([
            longLine(5001, { quantity: "2600.123456789" }),
            { ...longLine(5002), kp: { unit_price: pair("0.004", "0.0049"), total: pair("1.00", "1.22") } },
          ])
        )
      )
    );
    renderWithProviders(
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
      </Routes>,
      {
        initialRoute: `${STAGE}/offers/${OUR_PRICE_OFFER_ID}${search}`,
        initialUser: { id: 1, email: "u@example.com", role: "admin" },
      }
    );
    await screen.findByTestId("line-5001");
    expect(text("tile-per_sqm")).toContain(perSqm);
    expect(text("kp-5001")).toBe(`${kp}₽/м2`);
    expect(text("fork-5001")).toBe(fork);
    expect(text("ours-5001")).toContain(ours);
    expect(text("to-ours-5001")).toContain(toOurs);
    expect(text("qty-5001")).toBe("2 600,12");
    expect(text("kp-5002")).toBe(`${SUB_KOPECK_LABEL.replace(nbsp, " ")}₽/м2`);
    expectNoLongFractions();
  });
});

function longView(): OurPriceLineView {
  const base = ourPriceLineViewFixture();
  const member = longLine(5003, {
    group: { lines: 2, share: "0.4999999", kp_per_sqm: pair("1", "1") },
  });
  const long = longFractions({
    ...base,
    line: longLine(OUR_PRICE_LINE_ID, {
      quantity: "2600.123456789",
      group: { lines: 2, share: "0.5000001", kp_per_sqm: pair("1", "1") },
      unit_mismatch: { kp_ratio: "5.3333333333", contracts: [{ contract_id: 101, ratio: "0.2222222222" }] },
    }),
    group_lines: [member],
    contracts: [{ ...base.contracts[0], ratio: "5.3333333333" }],
  });
  return {
    ...long,
    contracts: [
      ...long.contracts,
      { ...base.contracts[0], contract_id: 102, number: "Договор 2", rate: pair("0.004", "0.0049"), ratio: null },
    ],
  };
}

describe("панель строки: числа с длинной дробью", () => {
  it.each([
    ["net", "net", "1 200,79", "1 000,79", "+20 000,79 ₽"],
    ["shown", "shown", "1 464,79", "1 220,79", "+24 400,79 ₽"],
  ] as const)("вид %s: цены, группа, договоры и кратность", async (_view, vat, kp, ours, toOurs) => {
    server.use(http.get(LINE_PATH, () => HttpResponse.json(longView())));
    renderWithProviders(
      <LineSheet
        tenderId={OUR_PRICE_TENDER_ID}
        roundId={OUR_PRICE_ROUND_ID}
        offerId={OUR_PRICE_OFFER_ID}
        positionItemId={OUR_PRICE_LINE_ID}
        seriesId={null}
        vat={vat}
        vatRate="22"
        onClose={() => {}}
      />
    );
    await screen.findByTestId("sheet-prices");
    await waitFor(() => expect(screen.getByTestId("sheet-kp-price")).toBeInTheDocument());
    expect(text("sheet-kp-price")).toContain(kp);
    expect(text("sheet-ours")).toContain(ours);
    expect(text("sheet-to-ours")).toContain(toOurs);
    expect(text("sheet-quantity")).toContain("2 600,12");
    expect(text("contract-102")).toContain(SUB_KOPECK_LABEL);
    expect(text("contract-101")).toContain("×5,3");
    expect(text("unit-warn")).toContain("в 4,5 раз меньше");
    expect(text("unit-warn")).toContain("в 5,3 раз");
    expectNoLongFractions();
  });

  it("системная строка: цена группы за м² и сумма строки в КП — не больше двух знаков", async () => {
    const base = ourPriceLineViewFixture();
    const system = longLine(OUR_PRICE_LINE_ID, {
      kind: "system",
      group: { lines: 1, share: "1", kp_per_sqm: pair("786", "959") },
      unit_mismatch: null,
    });
    server.use(http.get(LINE_PATH, () => HttpResponse.json({ ...base, line: system, group_lines: [], contracts: [] })));
    renderWithProviders(
      <LineSheet
        tenderId={OUR_PRICE_TENDER_ID}
        roundId={OUR_PRICE_ROUND_ID}
        offerId={OUR_PRICE_OFFER_ID}
        positionItemId={OUR_PRICE_LINE_ID}
        seriesId={null}
        vat="net"
        vatRate="22"
        onClose={() => {}}
      />
    );
    await screen.findByTestId("sheet-kp-total");
    expect(text("sheet-kp-price")).toContain("786,79");
    expect(text("sheet-kp-total")).toMatch(/,79 ₽$/);
    expectNoLongFractions();
  });
});

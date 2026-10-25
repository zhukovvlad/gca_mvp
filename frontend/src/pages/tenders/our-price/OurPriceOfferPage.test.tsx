import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import {
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
  ourPriceOfferFixture,
  sampleTenderCard,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { OUR_PRICE_POLL_MS } from "@/services/queries";
import type { MoneyPair, OurPriceLine, OurPriceOffer, TenderCard } from "@/types/domain";

import OurPriceOfferPage from "./OurPriceOfferPage";
import {
  EMPTY_REASON_LABEL,
  errorLabel,
  FILTER_LABEL,
  LINE_STATE_LABEL,
  NO_INFLATION_CAPTION,
  PRICES_CAPTION_NET,
} from "./ourPriceCopy";

const OFFER_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId";
const TENDER_PATH = "/api/v1/tenders/:id";
const STAGE = `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price`;
const ROUTE = `${STAGE}/offers/${OUR_PRICE_OFFER_ID}`;

const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

/** Карточка тендера с этапом 31: участники А (КП), Б (КП без сметы), В (КП); этап 32 — чужой. */
function tenderCard(): TenderCard {
  return {
    ...sampleTenderCard,
    id: OUR_PRICE_TENDER_ID,
    rounds: [
      { ...sampleTenderCard.rounds[0], id: OUR_PRICE_ROUND_ID, stage_no: 3 },
      { ...sampleTenderCard.rounds[0], id: 32, stage_no: 4 },
    ],
    participants: [
      { package_id: 501, contractor_id: 401, title: "Подрядчик А", inn: "1" },
      { package_id: 502, contractor_id: 402, title: "Подрядчик Б", inn: "2" },
      { package_id: 503, contractor_id: 403, title: "Подрядчик В", inn: "3" },
      { package_id: 504, contractor_id: 404, title: "Подрядчик Г", inn: "4" },
      { package_id: 505, contractor_id: 405, title: "Подрядчик Д", inn: "5" },
    ],
    // Ячейки чужого этапа стоят первыми: поиск ячейки без проверки этапа нашёл бы их.
    cells: [
      { round_id: 32, package_id: 501, offer_id: 7009, estimate_id: 9009, total_including_vat: "3.00" },
      { round_id: 32, package_id: 504, offer_id: 7010, estimate_id: 9010, total_including_vat: "4.00" },
      { round_id: OUR_PRICE_ROUND_ID, package_id: 501, offer_id: 7001, estimate_id: 9001, total_including_vat: "1.00" },
      { round_id: OUR_PRICE_ROUND_ID, package_id: 502, offer_id: 7002, estimate_id: null, total_including_vat: null },
      { round_id: OUR_PRICE_ROUND_ID, package_id: 503, offer_id: 7003, estimate_id: 9003, total_including_vat: "2.00" },
      { round_id: OUR_PRICE_ROUND_ID, package_id: 504, offer_id: null, estimate_id: null, total_including_vat: null },
    ],
  };
}

function renderPage(search = "", options: { card?: boolean } = {}) {
  if (options.card !== false) server.use(http.get(TENDER_PATH, () => HttpResponse.json(tenderCard())));
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
      </Routes>
      <LocationProbe />
    </>,
    { initialRoute: ROUTE + search, initialUser: { id: 1, email: "u@example.com", role: "admin" } }
  );
}

const offerRequests = () => handlerState.ourPriceRequests.filter((r) => r.kind === "offer");
const seriesParam = (index: number) => new URL(offerRequests()[index].url).searchParams.get("inflation_series_id");

async function loaded() {
  await screen.findByTestId("tile-per_sqm");
}

function line(id: number, article: number | null, overrides: Partial<OurPriceLine> = {}): OurPriceLine {
  return ourPriceLineFixture({ position_item_id: id, article_id: article, title: `Работа ${id}`, ...overrides });
}

function offerWith(lines: OurPriceLine[], patch: Partial<OurPriceOffer> = {}): OurPriceOffer {
  return { ...ourPriceOfferFixture(), lines, ...patch };
}

function serve(offer: OurPriceOffer) {
  server.use(http.get(OFFER_PATH, () => HttpResponse.json(offer)));
}

const lineRows = () => document.querySelectorAll('[data-testid^="line-"]');
const lineIds = () =>
  [...lineRows()].map((row) => Number(row.getAttribute("data-testid")?.slice("line-".length))).sort((a, b) => a - b);

/** Восемь строк одной статьи: по две на «ниже», по одной на «дороже» и «в вилке», два расхождения единиц, две без сравнения. */
function filterLines(): OurPriceLine[] {
  const below = pair("300.00", "366.00");
  return [
    line(1, 11, { state: "comparable", position: "below", to_ours: pair("-300.00", "-366.00"), bargain: pair("0.00", "0.00"), below }),
    line(2, 11, { state: "comparable", position: "above", to_ours: pair("200.00", "244.00"), bargain: pair("200.00", "244.00"), below: null }),
    line(3, 11, { state: "comparable", position: "below", to_ours: pair("-50.00", "-61.00"), bargain: pair("0.00", "0.00"), below }),
    line(4, 11, { state: "unit_mismatch", position: null, to_ours: pair("900.00", "1098.00"), bargain: pair("900.00", "1098.00"), below }),
    line(5, 11, { state: "unit_mismatch", position: null, to_ours: pair("-90.00", "-109.80"), bargain: pair("90.00", "109.80"), below }),
    line(6, 11, { state: "no_history", position: null, to_ours: null, bargain: null, below: null, range: null, ours: null, rung: null }),
    line(7, 11, { state: "no_variant", waiting_admin: true, position: null, to_ours: null, bargain: null, below: null, range: null, ours: null, rung: null }),
    line(8, 11, { state: "comparable", position: "in", to_ours: pair("-10.00", "-12.20"), bargain: pair("0.00", "0.00"), below: null }),
  ];
}

function filterOffer(): OurPriceOffer {
  const base = ourPriceOfferFixture();
  const participant = base.participants[0];
  return offerWith(filterLines(), {
    participants: [
      {
        ...participant,
        totals: {
          ...participant.totals!,
          below_lines: 2,
          unit_mismatch_lines: 2,
          not_comparable: {
            total: pair("8000.00", "9760.00"),
            by_state: {
              no_history: { lines: 1, amount: pair("5000.00", "6100.00") },
              no_variant: { lines: 1, amount: pair("3000.00", "3660.00") },
            },
          },
        },
      },
    ],
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("состояния экрана", () => {
  it("первая загрузка — скелет без чужих цифр, затем экран; корень — container-page", async () => {
    const hold = holdRequests("get", OFFER_PATH);
    const { container } = renderPage();
    expect(container.querySelector(".container-page .animate-pulse")).not.toBeNull();
    expect(screen.queryByTestId("tile-per_sqm")).not.toBeInTheDocument();
    hold.release();
    await loaded();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(container.querySelector(".container-page [data-testid='round-header']")).not.toBeNull();
  });

  it.each([
    [404, errorLabel(404)],
    [500, errorLabel(500)],
  ])("ответ %i: подпись причины, без скелета; корень — container-page", async (status, label) => {
    server.use(http.get(OFFER_PATH, () => HttpResponse.json({ detail: "x" }, { status })));
    const { container } = renderPage();
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(screen.getByText(label).closest(".container-page")).not.toBeNull();
  });

  it("шапка: участник, этап, дата, объект, класс и подпись цен", async () => {
    renderPage();
    await loaded();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("КП «Подрядчик А» против нашей цены");
    const header = screen.getByTestId("round-header");
    expect(header).toHaveTextContent("10.03.2026");
    expect(header).toHaveTextContent("Объект Т");
    expect(header).toHaveTextContent("Класс 2");
    expect(header).toHaveTextContent(PRICES_CAPTION_NET);
  });
});

describe("путь и переключатель участников", () => {
  it("путь: тендер, этап со ссылкой назад с переключателями адреса, участник без ссылки", async () => {
    renderPage("?vat=shown&inflation=3&article=11");
    await loaded();
    const nav = screen.getByRole("navigation", { name: "Хлебные крошки" });
    expect(within(nav).getByRole("link", { name: "Тендеры" })).toHaveAttribute("href", "/tenders");
    expect(within(nav).getByRole("link", { name: "Тендер Т" })).toHaveAttribute("href", `/tenders/${OUR_PRICE_TENDER_ID}`);
    expect(within(nav).getByRole("link", { name: "этап 3: все участники" })).toHaveAttribute(
      "href",
      `${STAGE}?vat=shown&inflation=3`
    );
    expect(within(nav).queryByRole("link", { name: "Подрядчик А" })).not.toBeInTheDocument();
    expect(nav).toHaveTextContent("Подрядчик А");
  });

  it("возврат к этапу без переключателей — без хвоста адреса", async () => {
    renderPage();
    await loaded();
    const nav = screen.getByRole("navigation", { name: "Хлебные крошки" });
    expect(within(nav).getByRole("link", { name: "этап 3: все участники" })).toHaveAttribute("href", STAGE);
  });

  it("кнопки других участников этапа — из карточки тендера, только с КП; текущий отмечен", async () => {
    renderPage("?vat=shown&inflation=3&article=11");
    await loaded();
    const switcher = await screen.findByTestId("participant-switcher");
    expect(within(switcher).getByRole("link", { name: "Подрядчик В" })).toHaveAttribute(
      "href",
      `${STAGE}/offers/7003?vat=shown&inflation=3`
    );
    expect(within(switcher).queryByRole("link", { name: "Подрядчик Б" })).not.toBeInTheDocument();
    expect(within(switcher).queryByText("Подрядчик Б")).not.toBeInTheDocument();
    expect(within(switcher).queryByText("Подрядчик Г")).not.toBeInTheDocument();
    expect(within(switcher).queryByText("Подрядчик Д")).not.toBeInTheDocument();
    expect(within(switcher).queryByRole("link", { name: "Подрядчик А" })).not.toBeInTheDocument();
    expect(within(switcher).getByText("Подрядчик А").closest("[aria-current]")).not.toBeNull();
    expect(within(switcher).getByRole("link", { name: "Подрядчик В" })).not.toHaveAttribute("aria-current");
    // Имена участников — из карточки тендера: тяжёлый эндпоинт этапа ради них не зовётся.
    expect(handlerState.ourPriceRequests.filter((r) => r.kind === "round")).toHaveLength(0);
  });

  it("в этапе единственное КП — переключателя нет: других участников нет", async () => {
    const card = tenderCard();
    server.use(
      http.get(TENDER_PATH, () =>
        HttpResponse.json({ ...card, cells: card.cells.filter((c) => c.round_id !== OUR_PRICE_ROUND_ID || c.package_id === 501) })
      )
    );
    renderPage("", { card: false });
    await loaded();
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Хлебные крошки" })).toBeInTheDocument());
    expect(screen.queryByTestId("participant-switcher")).not.toBeInTheDocument();
  });

  it("карточка тендера недоступна — экран открыт, переключателя нет", async () => {
    server.use(http.get(TENDER_PATH, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderPage("", { card: false });
    await loaded();
    expect(screen.queryByTestId("participant-switcher")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
});

describe("плитки-фильтры", () => {
  it("счётчики плиток равны totals ответа и числу строк под фильтром", async () => {
    const offer = filterOffer();
    serve(offer);
    renderPage("?article=11");
    await loaded();
    const totals = offer.participants[0].totals!;
    const notComparable = Object.values(totals.not_comparable.by_state).reduce((sum, row) => sum + row.lines, 0);

    const expected = { below_paid: totals.below_lines, unit_mismatch: totals.unit_mismatch_lines, not_comparable: notComparable, above_ours: 1 };
    for (const [filter, count] of Object.entries(expected)) {
      const tile = screen.getByTestId(`tile-${filter}`);
      expect(tile).toHaveAttribute("data-count", String(count));
      fireEvent.click(tile);
      expect(lineRows()).toHaveLength(count);
      fireEvent.click(tile);
    }
    expect(screen.getByTestId("tile-all")).toHaveAttribute("data-count", "8");
    expect(lineRows()).toHaveLength(8);
  });

  it("«дороже»: только сопоставимая строка с bargain > 0; bargain «0.00» и строки разных единиц не входят", async () => {
    serve(filterOffer());
    renderPage("?article=11");
    await loaded();
    fireEvent.click(screen.getByTestId("tile-above_ours"));
    expect(lineIds()).toEqual([2]);
  });

  it("«ниже всего, что платили»: сопоставимые с below; unit_mismatch с заполненным below не входят", async () => {
    serve(filterOffer());
    renderPage("?article=11");
    await loaded();
    fireEvent.click(screen.getByTestId("tile-below_paid"));
    expect(lineIds()).toEqual([1, 3]);
  });

  it("«разные единицы учёта»: обе строки с to_ours 900 и −90 — и ни в «дороже», ни в «ниже»", async () => {
    serve(filterOffer());
    renderPage("?article=11");
    await loaded();
    fireEvent.click(screen.getByTestId("tile-unit_mismatch"));
    expect(lineIds()).toEqual([4, 5]);
    fireEvent.click(screen.getByTestId("tile-unit_mismatch"));
    fireEvent.click(screen.getByTestId("tile-above_ours"));
    expect(lineIds()).not.toContain(4);
    expect(lineIds()).not.toContain(5);
    fireEvent.click(screen.getByTestId("tile-above_ours"));
    fireEvent.click(screen.getByTestId("tile-below_paid"));
    expect(lineIds()).not.toContain(4);
    expect(lineIds()).not.toContain(5);
  });

  it("«не с чем сравнить»: остальные не-сопоставимые, без разных единиц", async () => {
    serve(filterOffer());
    renderPage("?article=11");
    await loaded();
    fireEvent.click(screen.getByTestId("tile-not_comparable"));
    expect(lineIds()).toEqual([6, 7]);
  });

  it("плитка нажата — aria-pressed; повторный щелчок и «Все строки» снимают фильтр", async () => {
    serve(filterOffer());
    renderPage("?article=11");
    await loaded();
    const above = screen.getByTestId("tile-above_ours");
    const all = screen.getByTestId("tile-all");
    expect(all).toHaveAttribute("aria-pressed", "true");
    expect(above).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(above);
    expect(above).toHaveAttribute("aria-pressed", "true");
    expect(all).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("tile-below_paid"));
    expect(above).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(all);
    expect(lineRows()).toHaveLength(8);
    expect(all).toHaveAttribute("aria-pressed", "true");
  });

  it("подписи плиток — названия фильтров; суммы плиток — из totals в выбранном виде", async () => {
    serve(filterOffer());
    renderPage("?vat=shown");
    await loaded();
    expect(screen.getByTestId("tile-above_ours")).toHaveTextContent(FILTER_LABEL.above_ours);
    expect(screen.getByTestId("tile-above_ours")).toHaveTextContent("+146 400,00");
    expect(screen.getByTestId("tile-below_paid")).toHaveTextContent("36 600,00");
    expect(screen.getByTestId("tile-below_paid")).not.toHaveTextContent("+36 600,00");
    expect(screen.getByTestId("tile-unit_mismatch")).toHaveTextContent("6 100,00");
    expect(screen.getByTestId("tile-not_comparable")).toHaveTextContent("9 760,00");
    fireEvent.click(screen.getByRole("switch", { name: /НДС/ }));
    await waitFor(() => expect(screen.getByTestId("tile-above_ours")).toHaveTextContent("120 000,00"));
  });

  it("плитка «КП на м²»: цена, отклонение от медианы; в виде «с НДС» — показанная цена", async () => {
    renderPage();
    await loaded();
    expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent("110 000,00");
    expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent("+10,0 % к медиане наших");
    fireEvent.click(screen.getByRole("switch", { name: /НДС/ }));
    await waitFor(() => expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent("134 200,00"));
  });

  it("отклонение от медианы: плюс — «warning», минус — «info»; нет отклонения — подписи нет", async () => {
    const base = ourPriceOfferFixture();
    const withMedian = (pct: string | null) => ({ ...base, participants: [{ ...base.participants[0], class_median_pct: pct }] });
    serve(withMedian("-5.0"));
    const first = renderPage();
    await loaded();
    expect(screen.getByText("-5,0 % к медиане наших")).toHaveClass("text-info-text");
    first.unmount();
    serve(withMedian("5.0"));
    const second = renderPage();
    await loaded();
    expect(screen.getByText("+5,0 % к медиане наших")).toHaveClass("text-warning-text");
    second.unmount();
    serve(withMedian(null));
    renderPage();
    await loaded();
    expect(screen.getByTestId("tile-per_sqm")).not.toHaveTextContent("медиан");
  });

  it("нет ₽/м² и нет причины — прочерк, а не пустая плитка", async () => {
    const base = ourPriceOfferFixture();
    serve({ ...base, participants: [{ ...base.participants[0], per_sqm: null, per_sqm_reason: null, class_median_pct: null }] });
    renderPage();
    await loaded();
    expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent("—");
  });

  it("нет ₽/м²: вместо цены — причина полем участника, полосы нет", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      participants: [
        { ...base.participants[0], state: "unknown_vat_base", per_sqm: null, per_sqm_reason: "unknown_vat_base", class_median_pct: null, totals: null },
      ],
    });
    renderPage();
    await loaded();
    expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent(EMPTY_REASON_LABEL.unknown_vat_base);
    expect(screen.queryByTestId("per-sqm-strip")).not.toBeInTheDocument();
  });

  it("totals нет: плитки показывают прочерки в суммах, счётчики считаются по строкам, фильтр работает", async () => {
    const base = filterOffer();
    serve({ ...base, participants: [{ ...base.participants[0], totals: null }] });
    renderPage("?article=11");
    await loaded();
    expect(screen.getByTestId("tile-above_ours")).toHaveTextContent("—");
    expect(screen.getByTestId("tile-below_paid")).toHaveAttribute("data-count", "2");
    fireEvent.click(screen.getByTestId("tile-below_paid"));
    expect(lineIds()).toEqual([1, 3]);
  });
});

describe("полоса ₽/м²", () => {
  it("точки договоров с ₽/м²: класс объекта темнее, договор без ₽/м² — без точки; метка КП", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: {
        ...base.history,
        contracts: [
          { ...base.history.contracts[0], id: 101 },
          { ...base.history.contracts[0], id: 102, rate_class: { id: 3, title: "Класс 3" }, object_title: "Объект З" },
          { ...base.history.contracts[0], id: 103 },
        ],
        per_sqm: [
          { contract_id: 101, net: "100000", shown: null },
          { contract_id: 102, net: "80000", shown: null },
        ],
      },
    });
    renderPage();
    await loaded();
    const strip = screen.getByTestId("per-sqm-strip");
    expect(within(strip).getByTestId("strip-dot-101")).toHaveAttribute("data-same-class", "true");
    expect(within(strip).getByTestId("strip-dot-102")).toHaveAttribute("data-same-class", "false");
    expect(within(strip).queryByTestId("strip-dot-103")).not.toBeInTheDocument();
    expect(within(strip).getByTestId("strip-chip-101")).toHaveTextContent("100 000,00");
    expect(within(strip).getByTestId("strip-chip-102")).toHaveTextContent("Объект З");
    expect(within(strip).queryByTestId("strip-chip-103")).not.toBeInTheDocument();
    expect(within(strip).getByTestId("strip-me")).toHaveTextContent("110 000,00");
  });

  it("положение точки — CSS-вычисление над строками: JS числа не считает; границы оси — min и max значений", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, per_sqm: [{ contract_id: 101, net: "100000", shown: null }] },
    });
    renderPage();
    await loaded();
    const strip = screen.getByTestId("per-sqm-strip");
    expect(strip.style.getPropertyValue("--lo")).toBe("100000");
    expect(strip.style.getPropertyValue("--hi")).toBe("110000");
    expect(screen.getByTestId("strip-dot-101").className).toContain("var(--lo)");
    expect(screen.getByTestId("strip-dot-101").className).not.toContain("left-1/2");
    expect(screen.getByTestId("strip-dot-101").style.getPropertyValue("--v")).toBe("100000");
    expect(screen.getByTestId("strip-me").style.getPropertyValue("--v")).toBe("110000");
  });

  it("КП дешевле всех договоров: нижняя граница оси — метка КП, верхняя — самый дорогой договор", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, per_sqm: [{ contract_id: 101, net: "120000", shown: null }] },
    });
    renderPage();
    await loaded();
    const strip = screen.getByTestId("per-sqm-strip");
    expect(strip.style.getPropertyValue("--lo")).toBe("110000");
    expect(strip.style.getPropertyValue("--hi")).toBe("120000");
  });

  it("общая ставка известна, но показ в нетто: полоса в нетто", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, shown_rate: "22.00", per_sqm: [{ contract_id: 101, net: "100000", shown: "122000" }] },
    });
    renderPage();
    await loaded();
    expect(screen.getByTestId("strip-dot-101").style.getPropertyValue("--v")).toBe("100000");
    expect(screen.getByTestId("strip-me").style.getPropertyValue("--v")).toBe("110000");
  });

  it("все значения равны — точки стоят посередине, без деления на ноль", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, per_sqm: [{ contract_id: 101, net: "110000", shown: null }] },
    });
    renderPage();
    await loaded();
    expect(screen.getByTestId("strip-dot-101").className).toContain("left-1/2");
    expect(screen.getByTestId("strip-me").className).toContain("left-1/2");
  });

  it("ось другая только при общей ставке: shown_rate пуст — полоса в нетто даже при ?vat=shown", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, shown_rate: null, per_sqm: [{ contract_id: 101, net: "100000", shown: "122000" }] },
    });
    renderPage("?vat=shown");
    await loaded();
    expect(screen.getByTestId("strip-dot-101").style.getPropertyValue("--v")).toBe("100000");
    expect(screen.getByTestId("strip-me").style.getPropertyValue("--v")).toBe("110000");
  });

  it("общая ставка известна: при ?vat=shown полоса — в показанном виде", async () => {
    const base = ourPriceOfferFixture();
    serve({
      ...base,
      history: { ...base.history, shown_rate: "22.00", per_sqm: [{ contract_id: 101, net: "100000", shown: "122000" }] },
    });
    renderPage("?vat=shown");
    await loaded();
    expect(screen.getByTestId("strip-dot-101").style.getPropertyValue("--v")).toBe("122000");
    expect(screen.getByTestId("strip-me").style.getPropertyValue("--v")).toBe("134200");
  });
});

describe("таблица статей", () => {
  function threeArticles(): OurPriceOffer {
    const base = ourPriceOfferFixture();
    const head = base.articles[0];
    const noTotals = { ...head.lines_totals! };
    return offerWith(
      [
        line(1, 11),
        line(2, 11),
        line(3, 12),
        line(4, null),
      ],
      {
        articles: [
          {
            ...head,
            work_category_id: 11,
            code: "01",
            title: "Статья 1",
            lines_totals: { ...noTotals, bargain: pair("120000.00", "146400.00"), below: pair("30000.00", "36600.00") },
          },
          {
            ...head,
            work_category_id: 12,
            code: "02",
            title: "Статья 2",
            per_sqm: null,
            whole: null,
            ours_class_per_sqm: null,
            lines_totals: { ...noTotals, bargain: pair("60000.00", "73200.00"), below: pair("-90000.00", "-109800.00") },
          },
          {
            ...head,
            work_category_id: null,
            code: null,
            title: "Нераспределённое",
            lines_totals: { ...noTotals, bargain: null, below: null },
          },
        ],
      }
    );
  }

  it("свёрнуто по умолчанию: ни одной строки в DOM; щелчок раскрывает и сворачивает статью", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    expect(lineRows()).toHaveLength(0);
    const toggle = screen.getByTestId("article-toggle-11");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(lineIds()).toEqual([1, 2]);
    fireEvent.click(toggle);
    expect(lineRows()).toHaveLength(0);
  });

  it("раскрытые статьи не мешают друг другу: строки — каждой своей", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    fireEvent.click(screen.getByTestId("article-toggle-12"));
    fireEvent.click(screen.getByTestId("article-toggle-11"));
    expect(lineIds()).toEqual([1, 2, 3]);
  });

  it("?article=<id> раскрывает эту статью при входе, остальные свёрнуты", async () => {
    serve(threeArticles());
    renderPage("?article=12");
    await loaded();
    expect(lineIds()).toEqual([3]);
    expect(screen.getByTestId("article-toggle-12")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("article-toggle-11")).toHaveAttribute("aria-expanded", "false");
  });

  it("?article=unallocated раскрывает «Нераспределённое» — статью без id", async () => {
    serve(threeArticles());
    renderPage("?article=unallocated");
    await loaded();
    expect(lineIds()).toEqual([4]);
  });

  it("?article= с чужим значением ничего не раскрывает", async () => {
    serve(threeArticles());
    renderPage("?article=999");
    await loaded();
    expect(lineRows()).toHaveLength(0);
  });

  it("?article=11abc — не 11", async () => {
    serve(threeArticles());
    renderPage("?article=11abc");
    await loaded();
    expect(lineRows()).toHaveLength(0);
  });

  it("1 831 строка: свёрнутые статьи строк не монтируют, раскрытая — только свои", async () => {
    const lines: OurPriceLine[] = [];
    for (let i = 1; i <= 1831; i++) lines.push(line(i, i <= 600 ? 11 : i <= 1200 ? 12 : null));
    serve({ ...threeArticles(), lines });
    renderPage();
    await loaded();
    expect(lineRows()).toHaveLength(0);
    fireEvent.click(screen.getByTestId("article-toggle-11"));
    expect(lineRows()).toHaveLength(600);
    fireEvent.click(screen.getByTestId("article-toggle-11"));
    fireEvent.click(screen.getByTestId("article-toggle-unallocated"));
    expect(lineRows()).toHaveLength(631);
  }, 30000);

  it("статья без строк под фильтром: подпись вместо пустой таблицы", async () => {
    serve(threeArticles());
    renderPage("?article=12");
    await loaded();
    fireEvent.click(screen.getByTestId("tile-unit_mismatch"));
    expect(lineRows()).toHaveLength(0);
    expect(screen.getByTestId("lines-empty")).toBeInTheDocument();
  });

  it("значения строки статьи: ₽/м², вилка, «целиком», суммы по строкам, пустое — прочерк", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    expect(screen.getByTestId("art-persqm-11")).toHaveTextContent("110 000,00");
    expect(screen.getByTestId("art-fork-11")).toHaveTextContent("90 000,00 – 130 000,00");
    expect(screen.getByTestId("art-whole-11")).toHaveTextContent("+500 000,00");
    expect(screen.getByTestId("art-above-11")).toHaveTextContent("+120 000,00");
    expect(screen.getByTestId("art-below-11")).toHaveTextContent("30 000,00");
    expect(screen.getByTestId("art-persqm-12")).toHaveTextContent("—");
    expect(screen.getByTestId("art-fork-12")).toHaveTextContent("—");
    expect(screen.getByTestId("art-whole-12")).toHaveTextContent("—");
    expect(screen.getByTestId("art-above-unallocated")).toHaveTextContent("—");
    expect(screen.getByTestId("art-below-unallocated")).toHaveTextContent("—");
  });

  it("не с чем сравнить и разные единицы — в одной ячейке: сумма и ⚠ с числом строк", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    const none = screen.getByTestId("art-none-11");
    expect(none).toHaveTextContent("8 000,00");
    expect(none).toHaveTextContent("⚠ 1");
  });

  it("полосы «ниже ← → дороже»: доля от наибольшей суммы по статьям, модуль отрицательной", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    const above11 = screen.getByTestId("art-bar-above-11");
    expect(above11.style.getPropertyValue("--v")).toBe("120000.00");
    expect(above11.style.getPropertyValue("--max")).toBe("120000.00");
    const below12 = screen.getByTestId("art-bar-below-12");
    expect(below12.style.getPropertyValue("--v")).toBe("90000.00");
    expect(below12.style.getPropertyValue("--max")).toBe("120000.00");
    expect(screen.queryByTestId("art-bar-above-unallocated")).not.toBeInTheDocument();
    expect(screen.queryByTestId("art-bar-below-unallocated")).not.toBeInTheDocument();
  });

  it("масштаб полос — наибольший модуль: «ниже» по модулю больше любой «дороже»", async () => {
    const offer = threeArticles();
    offer.articles[1] = {
      ...offer.articles[1],
      lines_totals: { ...offer.articles[1].lines_totals!, below: pair("-200000.00", "-244000.00") },
    };
    serve(offer);
    renderPage();
    await loaded();
    expect(screen.getByTestId("art-bar-above-11").style.getPropertyValue("--max")).toBe("200000.00");
    expect(screen.getByTestId("art-bar-below-12").style.getPropertyValue("--v")).toBe("200000.00");
  });

  it("строка ступени получает класс объекта тендера", async () => {
    renderPage("?article=11");
    await loaded();
    expect(screen.getByTestId("rung-5001")).toHaveTextContent("вариант · Класс 2 · 3 дог.");
  });

  it("новый ?article= на том же участнике раскрывает новую статью, прежняя сворачивается", async () => {
    function GoTo({ to }: { to: string }) {
      const navigate = useNavigate();
      return (
        <button type="button" data-testid="go" onClick={() => navigate(to)}>
          go
        </button>
      );
    }
    serve(threeArticles());
    server.use(http.get(TENDER_PATH, () => HttpResponse.json(tenderCard())));
    renderWithProviders(
      <>
        <Routes>
          <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
        </Routes>
        <GoTo to={`${ROUTE}?article=12`} />
      </>,
      { initialRoute: `${ROUTE}?article=11` }
    );
    await loaded();
    expect(lineIds()).toEqual([1, 2]);
    fireEvent.click(screen.getByTestId("go"));
    await waitFor(() => expect(lineIds()).toEqual([3]));
  });

  it("итоговая строка не раскрывается и стоит первой", async () => {
    serve(threeArticles());
    renderPage();
    await loaded();
    const total = screen.getByTestId("article-total");
    expect(total).toHaveTextContent("Итого на м² объекта");
    expect(within(total).queryByRole("button")).not.toBeInTheDocument();
    const rows = screen.getAllByTestId(/^article-(total|\d+|unallocated)$/);
    expect(rows[0]).toBe(total);
  });

  it("разные единицы: ⚠ только при ненулевом числе строк", async () => {
    const offer = threeArticles();
    offer.articles[1] = { ...offer.articles[1], lines_totals: { ...offer.articles[1].lines_totals!, unit_mismatch_lines: 0 } };
    serve(offer);
    renderPage();
    await loaded();
    expect(screen.getByTestId("art-none-11")).toHaveTextContent("⚠");
    expect(screen.getByTestId("art-none-12")).not.toHaveTextContent("⚠");
    expect(screen.getByTestId("art-none-12")).toHaveTextContent("8 000,00");
  });

  it("у статьи нет сумм по строкам (lines_totals = null): во всех колонках сумм прочерк, полос нет", async () => {
    const offer = threeArticles();
    offer.articles[2] = { ...offer.articles[2], lines_totals: null };
    serve(offer);
    renderPage();
    await loaded();
    for (const col of ["above", "below", "none"]) {
      expect(screen.getByTestId(`art-${col}-unallocated`)).toHaveTextContent("—");
    }
    expect(screen.queryByTestId("art-bar-above-unallocated")).not.toBeInTheDocument();
  });

  it("ни у одной статьи нет сумм по строкам — полос нет вовсе", async () => {
    const offer = threeArticles();
    serve({ ...offer, articles: offer.articles.map((a) => ({ ...a, lines_totals: null })) });
    renderPage();
    await loaded();
    expect(document.querySelectorAll('[data-testid^="art-bar-"]')).toHaveLength(0);
  });

  it("все суммы по строкам нулевые — полос нет: делить на нулевой максимум нечем", async () => {
    const offer = threeArticles();
    const zero = (a: OurPriceOffer["articles"][number]) => ({
      ...a,
      lines_totals: { ...a.lines_totals!, bargain: pair("0.00", "0.00"), below: pair("0.00", "0.00") },
    });
    serve({ ...offer, articles: offer.articles.map(zero) });
    renderPage();
    await loaded();
    expect(document.querySelectorAll('[data-testid^="art-bar-"]')).toHaveLength(0);
  });

  it("раскрытая статья без единой строки в ответе: подпись, а не ошибка", async () => {
    const offer = threeArticles();
    serve({ ...offer, articles: [...offer.articles, { ...offer.articles[0], work_category_id: 13, title: "Статья 3" }] });
    renderPage("?article=13");
    await loaded();
    expect(screen.getByTestId("lines-empty")).toBeInTheDocument();
  });
});

describe("НДС и ИПЦ", () => {
  it("переключатель НДС — без запроса: цифры строки берутся из shown", async () => {
    renderPage("?article=11");
    await loaded();
    expect(offerRequests()).toHaveLength(1);
    expect(screen.getByTestId("to-ours-5001")).toHaveTextContent("+20 000,00");
    fireEvent.click(screen.getByRole("switch", { name: /НДС/ }));
    await waitFor(() => expect(screen.getByTestId("to-ours-5001")).toHaveTextContent("+24 400,00"));
    expect(screen.getByTestId("loc")).toHaveTextContent("vat=shown");
    expect(screen.getByTestId("loc")).toHaveTextContent("article=11");
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(offerRequests()).toHaveLength(1);
  });

  it("amendment_date_missing при прямом заходе: номинальные цифры вторым запросом, баннер, «цены без приведения»", async () => {
    handlerState.ourPriceRefusal = "amendment_date_missing";
    renderPage("?inflation=3&article=11");
    await loaded();
    expect(offerRequests()).toHaveLength(2);
    expect(seriesParam(0)).toBe("3");
    expect(seriesParam(1)).toBeNull();
    expect(screen.getByTestId("inflation-refusal")).toHaveTextContent("У допсоглашения договора 1 нет даты подготовки сметы.");
    expect(screen.getByTestId("round-header")).toHaveTextContent(NO_INFLATION_CAPTION);
    expect(screen.getByTestId("loc")).toHaveTextContent("inflation=3");
    expect(screen.getByTestId("to-ours-5001")).toHaveTextContent("+20 000,00");
  });

  it("missing_inflation_years: баннер называет выбранный ряд; закрытие диалога ряда перезапрашивает разбор", async () => {
    handlerState.ourPriceRefusal = "missing_inflation_years";
    renderPage("?inflation=3");
    await loaded();
    const banner = screen.getByTestId("inflation-refusal");
    await waitFor(() => expect(banner).toHaveTextContent("Выбран ряд «Ряд 2024 года, выведен из обращения»"));
    const before = offerRequests().filter((r) => new URL(r.url).searchParams.get("inflation_series_id") === "3").length;
    await userEvent.click(within(banner).getByRole("button"));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(
        offerRequests().filter((r) => new URL(r.url).searchParams.get("inflation_series_id") === "3").length
      ).toBeGreaterThan(before)
    );
  });

  it("?vat=shown при неизвестной ставке участника: показ нетто, переключатель снят", async () => {
    const base = ourPriceOfferFixture();
    serve({ ...base, participants: [{ ...base.participants[0], vat_rate: null }] });
    renderPage("?vat=shown&article=11");
    await loaded();
    expect(screen.getByRole("switch", { name: /НДС/ })).not.toBeChecked();
    expect(screen.getByTestId("to-ours-5001")).toHaveTextContent("+20 000,00");
    expect(screen.getByTestId("round-header")).toHaveTextContent(PRICES_CAPTION_NET);
  });

  it("с рядом: подпись приведения из ответа", async () => {
    renderPage("?inflation=3");
    await loaded();
    expect(screen.getByTestId("round-header")).toHaveTextContent("Цены приведены к марту 2026");
  });

  it("смена ряда: серый слой поверх прежних цифр, пока идёт запрос", async () => {
    renderPage();
    await loaded();
    const toggle = screen.getByRole("switch", { name: "Привести по ИПЦ" });
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));
    const hold = holdRequests("get", OFFER_PATH);
    fireEvent.click(toggle);
    expect(await screen.findByTestId("recalc-overlay")).toBeInTheDocument();
    expect(screen.getByTestId("tile-per_sqm")).toHaveTextContent("110 000,00");
    hold.release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
  });
});

describe("прежние цифры под слоем — только своего участника", () => {
  it("после отказа ИПЦ смена ряда — серый слой над номинальными цифрами, а не скелет", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(OFFER_PATH, async ({ request }) => {
        const series = new URL(request.url).searchParams.get("inflation_series_id");
        if (series === "3") {
          return HttpResponse.json(
            { detail: { code: "missing_inflation_years", message: "m", missing_years: [2024] } },
            { status: 422 }
          );
        }
        if (series === "2") await gate;
        return HttpResponse.json(ourPriceOfferFixture({ withInflation: series !== null }));
      })
    );
    const { container } = renderPage("?inflation=3");
    await screen.findByTestId("inflation-refusal");
    await loaded();
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(await screen.findByRole("option", { name: /Внутренняя оценка/ }));
    await waitFor(() => expect(screen.queryByTestId("inflation-refusal")).not.toBeInTheDocument());
    expect(screen.getByTestId("recalc-overlay")).toBeInTheDocument();
    expect(screen.getByTestId("tile-per_sqm")).toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
  });

  it("переход к другому участнику: скелет, цифр прежнего участника под слоем нет", async () => {
    function GoTo({ to }: { to: string }) {
      const navigate = useNavigate();
      return (
        <button type="button" data-testid="go" onClick={() => navigate(to)}>
          go
        </button>
      );
    }
    const { container } = renderWithProviders(
      <>
        <Routes>
          <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
        </Routes>
        <GoTo to={`${STAGE}/offers/7003`} />
      </>,
      { initialRoute: ROUTE }
    );
    await loaded();
    const hold = holdRequests("get", OFFER_PATH);
    fireEvent.click(screen.getByTestId("go"));
    await waitFor(() => expect(hold.seen.count).toBe(1));
    expect(screen.queryByTestId("tile-per_sqm")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(container.querySelector(".container-page .animate-pulse")).not.toBeNull();
    hold.release();
  });
});

describe("плашка каталога и опрос", () => {
  it("in_progress 1 → 0: строка из «в разборе» становится сопоставимой без перезагрузки, плашка гаснет", async () => {
    let calls = 0;
    server.use(
      http.get(OFFER_PATH, () => {
        const parsing = calls++ === 0;
        return HttpResponse.json(
          offerWith(
            [
              line(5001, 11, parsing
                ? { state: "in_progress", position: null, to_ours: null, bargain: null, below: null, range: null, ours: null, rung: null }
                : { state: "comparable" }),
            ],
            { progress: { lines_total: 10, in_progress: parsing ? 1 : 0, waiting_admin: 0 } }
          )
        );
      })
    );
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage("?article=11");
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toBeInTheDocument());
    expect(screen.getByTestId("to-ours-5001")).toHaveTextContent(LINE_STATE_LABEL.in_progress);

    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument());
    expect(screen.getByTestId("to-ours-5001")).toHaveTextContent("+20 000,00");
    expect(screen.getByTestId("to-ours-5001")).not.toHaveTextContent(LINE_STATE_LABEL.in_progress);
    expect(calls).toBe(2);
  });

  it("опрос идёт без серого слоя", async () => {
    handlerState.ourPriceOfferInProgress = [3, 3];
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage();
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toBeInTheDocument());
    const hold = holdRequests("get", OFFER_PATH);
    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(hold.seen.count).toBe(1));
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("tile-per_sqm")).toBeInTheDocument();
    hold.release();
  });
});

describe("открытие строки и маршрут", () => {
  it("щелчок по строке отдаёт position_item_id точке входа панели", async () => {
    renderPage("?article=11");
    await loaded();
    expect(screen.getByTestId("offer-page")).not.toHaveAttribute("data-open-line");
    fireEvent.click(screen.getByTestId("line-5001"));
    expect(screen.getByTestId("offer-page")).toHaveAttribute("data-open-line", "5001");
  });

  it("вне RequireAdmin: member открывает разбор по адресу", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ id: 2, email: "m@example.com", role: "member" })));
    window.history.pushState({}, "", ROUTE);
    render(<App />);
    expect(await screen.findByTestId("tile-per_sqm", undefined, { timeout: 5000 })).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});

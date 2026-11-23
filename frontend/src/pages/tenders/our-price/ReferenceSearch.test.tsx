import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  lumpFixture,
  refLinkRowFixture,
  referenceLineViewFixture,
  referenceSearchFixture,
  referenceSearchRowFixture,
  ourPriceLineFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceLineView } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ADMIN = { id: 1, email: "admin@example.test", role: "admin" } as const;
const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;

const baseProps: LineSheetProps = {
  tenderId: OUR_PRICE_TENDER_ID,
  roundId: OUR_PRICE_ROUND_ID,
  offerId: OUR_PRICE_OFFER_ID,
  positionItemId: OUR_PRICE_LINE_ID,
  seriesId: null,
  vat: "net",
  vatRate: null,
  onClose: () => {},
};

function serve(make: () => OurPriceLineView = () => referenceLineViewFixture({ zones: [] })) {
  server.use(http.get(LINE_PATH, () => HttpResponse.json(make())));
}

function renderSheet(user: typeof ADMIN | typeof MEMBER = ADMIN, props: Partial<LineSheetProps> = {}) {
  return renderWithProviders(<LineSheet {...baseProps} {...props} />, { initialUser: user });
}

const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));
const asked = () => handlerState.referenceSearchRequests.map((u) => new URL(u).searchParams);

async function search(text: string) {
  const input = await screen.findByTestId("reference-search-input");
  await userEvent.clear(input);
  await userEvent.type(input, text);
  await userEvent.click(screen.getByRole("button", { name: "Найти" }));
}

describe("поиск по договорам: поле и запрос", () => {
  it("подсказка «ищите по корням слов» и переключатель единиц стоят под полем", async () => {
    serve();
    renderSheet();
    const block = await screen.findByTestId("sheet-reference-search");
    expect(block).toHaveTextContent("ищите по корням слов");
    expect(within(block).getByRole("button", { name: "все единицы" })).toHaveAttribute("aria-pressed", "true");
    expect(within(block).getByRole("button", { name: "как у строки КП" })).toHaveAttribute("aria-pressed", "false");
  });

  it("короче двух знаков запроса нет: кнопка погашена, сервер не спрошен", async () => {
    serve();
    renderSheet();
    await userEvent.type(await screen.findByTestId("reference-search-input"), "а");
    expect(screen.getByRole("button", { name: "Найти" })).toBeDisabled();
    await userEvent.keyboard("{Enter}");
    expect(asked()).toHaveLength(0);
  });

  it("два знака — запрос уходит с q и units=all", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet();
    await search("по");
    await waitFor(() => expect(asked()).toHaveLength(1));
    expect(asked()[0].get("q")).toBe("по");
    expect(asked()[0].get("units")).toBe("all");
    expect(asked()[0].has("inflation_series_id")).toBe(false);
  });

  it("ряд ИПЦ экрана уходит в запрос", async () => {
    serve();
    renderSheet(ADMIN, { seriesId: 7 });
    await search("подготовка");
    await waitFor(() => expect(asked()).toHaveLength(1));
    expect(asked()[0].get("inflation_series_id")).toBe("7");
  });

  it("пробелы по краям обрезаются: запрос — слово", async () => {
    serve();
    renderSheet();
    await search("  подготовка  ");
    await waitFor(() => expect(asked()).toHaveLength(1));
    expect(asked()[0].get("q")).toBe("подготовка");
  });

  it("переключатель «как у строки КП» перезапрашивает с units=kp", async () => {
    serve();
    renderSheet();
    await search("подготовка");
    await waitFor(() => expect(asked()).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "как у строки КП" }));
    await waitFor(() => expect(asked()).toHaveLength(2));
    expect(asked()[1].get("units")).toBe("kp");
    expect(asked()[1].get("q")).toBe("подготовка");
  });

  it("переключатель до запроса ничего не спрашивает", async () => {
    serve();
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: "как у строки КП" }));
    expect(asked()).toHaveLength(0);
  });
});

describe("поиск по договорам: выдача", () => {
  it("строки с зоной, единицей и видом пересчёта", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([
      referenceSearchRowFixture({ analog_id: 9301, conversion_kind: "none" }),
      referenceSearchRowFixture({
        analog_id: 9302,
        unit: "м2",
        zone: null,
        conversion: { method: "link", link_id: 41, factor: "0.100", factor_shown: "0,1", basis: "толщина 100 мм" },
        conversion_kind: "link",
      }),
      referenceSearchRowFixture({ analog_id: 9303, unit: "м2", conversion_kind: "manual_needed" }),
    ]);
    renderSheet();
    await search("подготовка");
    const none = await screen.findByTestId("ref-row-search-9301");
    expect(none).toHaveTextContent("Территория");
    expect(none).not.toHaveTextContent("пересчёт по связи");
    expect(none).not.toHaveTextContent("нужен пересчёт");
    const link = screen.getByTestId("ref-row-search-9302");
    expect(link).toHaveTextContent("зона не определена");
    expect(link).toHaveTextContent("пересчёт по связи");
    expect(screen.getByTestId("ref-row-search-9303")).toHaveTextContent("нужен пересчёт");
  });

  it("выдача длиннее пяти строк показана целиком, без «ещё»", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture(
      Array.from({ length: 8 }, (_, i) => referenceSearchRowFixture({ analog_id: 9400 + i, ids: [9400 + i] }))
    );
    renderSheet();
    await search("подготовка");
    await screen.findByTestId("ref-row-search-9407");
    expect(screen.queryByTestId("search-more")).not.toBeInTheDocument();
  });

  it("truncated: «показаны первые 50, уточните запрос»", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()], {
      truncated: true,
      total_lines: 83,
    });
    renderSheet();
    await search("подготовка");
    expect(await screen.findByTestId("reference-search-truncated")).toHaveTextContent("показаны первые 50, уточните запрос");
    expect(screen.getByTestId("reference-search-found")).toHaveTextContent("83 строки");
  });

  it("без truncated подсказки об уточнении нет", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet();
    await search("подготовка");
    await screen.findByTestId("ref-row-search-9001");
    expect(screen.queryByTestId("reference-search-truncated")).not.toBeInTheDocument();
  });

  it("пустая выдача: «Ничего не нашлось»", async () => {
    serve();
    renderSheet();
    await search("подготовка");
    expect(await screen.findByTestId("reference-search-empty")).toHaveTextContent("Ничего не нашлось.");
  });

  it("422 search_query_invalid: сообщение у поля", async () => {
    serve();
    server.use(
      http.get(`${LINE_PATH}/reference-search`, () =>
        HttpResponse.json({ detail: { code: "search_query_invalid", message: "Плохой запрос." } }, { status: 422 })
      )
    );
    renderSheet();
    await search("подготовка");
    expect(await screen.findByTestId("reference-search-invalid")).toBeInTheDocument();
  });

  it("422 reference_not_applicable: поля поиска нет", async () => {
    serve();
    server.use(
      http.get(`${LINE_PATH}/reference-search`, () =>
        HttpResponse.json(
          { detail: { code: "reference_not_applicable", message: "У системы поиска нет." } },
          { status: 422 }
        )
      )
    );
    renderSheet();
    await search("подготовка");
    await waitFor(() => expect(screen.queryByTestId("reference-search-input")).not.toBeInTheDocument());
  });

  it("у строки-системы раздела поиска нет", async () => {
    serve(() => ({
      ...referenceLineViewFixture({ zones: [] }),
      line: ourPriceLineFixture({ kind: "system" }),
    }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByTestId("reference-search-input")).not.toBeInTheDocument();
  });

  it("member видит выдачу без кнопок «сделать целью»", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet(MEMBER);
    await search("подготовка");
    await screen.findByTestId("ref-row-search-9001");
    expect(screen.queryByRole("button", { name: /сделать целью/ })).not.toBeInTheDocument();
  });
});

describe("поиск по договорам: выбор цели", () => {
  it("строка той же единицы: сразу PUT с origin search и conversion null", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet();
    await search("подготовка");
    await userEvent.click(within(await screen.findByTestId("ref-row-search-9001")).getByRole("button", { name: /сделать целью/ }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9001,
      origin: "search",
      conversion: null,
      confirm_outlier: false,
    });
  });

  it("строка поиска с пересчётом link: диалог, затем PUT с link_id, expected_factor и expected_basis строки", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([
      {
        ...refLinkRowFixture({ analog_id: 9501, ids: [9501] }),
        conversion_kind: "link",
      },
    ]);
    renderSheet();
    await search("подготовка");
    await userEvent.click(within(await screen.findByTestId("ref-row-search-9501")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,1");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9501,
      origin: "search",
      conversion: { method: "link", link_id: 41, expected_factor: "0.100", expected_basis: "толщина 100 мм" },
      confirm_outlier: false,
    });
  });

  it("строка поиска manual_needed: диалог с ручным вводом", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([
      referenceSearchRowFixture({ analog_id: 9601, ids: [9601], unit: "м2", conversion_kind: "manual_needed" }),
    ]);
    renderSheet();
    await search("подготовка");
    await userEvent.click(within(await screen.findByTestId("ref-row-search-9601")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    expect(within(dialog).getByTestId("reference-dialog-manual")).toBeInTheDocument();
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
  });

  it("после записи цели поиск перезапрашивается: метки пересчитаны сервером", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet();
    await search("подготовка");
    await userEvent.click(within(await screen.findByTestId("ref-row-search-9001")).getByRole("button", { name: /сделать целью/ }));
    await waitFor(() => expect(asked().length).toBeGreaterThanOrEqual(2));
  });
});

describe("поиск по договорам: «похоже на комплект»", () => {
  const LUMP_PATH = "/api/v1/position-items/:id/lump-sum";
  const AUTHOR = { id: 7, email: "boss@example.test" };
  const lumpRow = (over: Parameters<typeof lumpFixture>[0] = {}) =>
    referenceSearchRowFixture({ analog_id: 9701, ids: [9701], lump: lumpFixture(over) });

  function serveLump() {
    const asked: Array<{ method: string; id: string; body: unknown }> = [];
    const handle = async ({ request, params }: { request: Request; params: Record<string, unknown> }) => {
      const text = await request.text();
      asked.push({ method: request.method, id: String(params.id), body: text === "" ? null : JSON.parse(text) });
      return request.method === "PUT" ? HttpResponse.json({}) : new HttpResponse(null, { status: 204 });
    };
    server.use(http.put(LUMP_PATH, handle), http.delete(LUMP_PATH, handle));
    return asked;
  }

  it("строка-комплект: метка, подсказка с кратностью, «сделать целью» погашено с подсказкой", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([lumpRow()]);
    renderSheet();
    await search("воздуховод");
    const row = await screen.findByTestId("ref-row-search-9701");
    const mark = within(row).getByTestId("search-lump-mark");
    expect(mark).toHaveTextContent("похоже на комплект");
    expect(mark).not.toHaveTextContent("поправлено вручную");
    expect(mark.getAttribute("title")).toMatch(/^цена в 16\s989 раз выше обычной ставки семьи$/);
    expect(within(row).getByRole("button", { name: /сделать целью/ })).toBeDisabled();
    expect(within(row).getByTestId("ref-row-search-9701-not-target")).toHaveAttribute(
      "title",
      "Похоже на комплект — не цель для ставки"
    );
  });

  it("поправка администратора «это комплект»: метка с «поправлено вручную»", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([
      lumpRow({ rule: false, override: { verdict: "lump", set_by: AUTHOR, set_at: "2026-11-22T09:30:00+00:00" } }),
    ]);
    renderSheet();
    await search("воздуховод");
    expect(await screen.findByTestId("search-lump-mark")).toHaveTextContent("похоже на комплект · поправлено вручную");
  });

  it("строка без признака (is_lump=false и lump=null): метки нет, цель выбирается как раньше", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([
      lumpRow({ is_lump: false, rule: false }),
      referenceSearchRowFixture({ analog_id: 9702, ids: [9702], lump: null }),
    ]);
    renderSheet();
    await search("воздуховод");
    const rate = await screen.findByTestId("ref-row-search-9701");
    const plain = screen.getByTestId("ref-row-search-9702");
    expect(screen.queryByTestId("search-lump-mark")).not.toBeInTheDocument();
    expect(within(rate).getByRole("button", { name: /сделать целью/ })).toBeEnabled();
    expect(within(plain).getByRole("button", { name: /сделать целью/ })).toBeEnabled();
  });

  it("admin: меню строки с тремя положениями; выбор уходит сразу (PUT lump, DELETE для «как решило правило»)", async () => {
    serve();
    const asked = serveLump();
    handlerState.referenceSearchResult = referenceSearchFixture([lumpRow()]);
    renderSheet();
    await search("воздуховод");
    await userEvent.click(await screen.findByTestId("ref-row-search-9701-lump-menu"));
    expect(await screen.findByTestId("ref-row-search-9701-lump-rule")).toHaveTextContent("Как решило правило (комплект)");
    expect(screen.getByTestId("ref-row-search-9701-lump-lump")).toHaveTextContent("Это комплект");
    await userEvent.click(screen.getByTestId("ref-row-search-9701-lump-rate"));
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toEqual({ method: "PUT", id: "9701", body: { verdict: "rate" } });
  });

  it("admin: возврат к правилу — DELETE", async () => {
    serve();
    const asked = serveLump();
    handlerState.referenceSearchResult = referenceSearchFixture([
      lumpRow({ override: { verdict: "rate", set_by: AUTHOR, set_at: "2026-11-22T09:30:00+00:00" }, is_lump: false }),
    ]);
    renderSheet();
    await search("воздуховод");
    await userEvent.click(await screen.findByTestId("ref-row-search-9701-lump-menu"));
    await userEvent.click(await screen.findByTestId("ref-row-search-9701-lump-rule"));
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toEqual({ method: "DELETE", id: "9701", body: null });
  });

  it("member: метка есть, меню поправки нет; строка с lump=null меню не имеет и у admin", async () => {
    serve();
    handlerState.referenceSearchResult = referenceSearchFixture([lumpRow()]);
    const view = renderSheet(MEMBER);
    await search("воздуховод");
    await screen.findByTestId("search-lump-mark");
    expect(screen.queryByTestId("ref-row-search-9701-lump-menu")).not.toBeInTheDocument();
    view.unmount();

    handlerState.referenceSearchResult = referenceSearchFixture([
      referenceSearchRowFixture({ analog_id: 9702, ids: [9702], lump: null }),
    ]);
    renderSheet(ADMIN);
    await search("подготовка");
    await screen.findByTestId("ref-row-search-9702");
    expect(screen.queryByTestId("ref-row-search-9702-lump-menu")).not.toBeInTheDocument();
  });

  it("422 reference_is_lump_sum при выборе цели: панель перечитывается", async () => {
    let lineReads = 0;
    server.use(
      http.get(LINE_PATH, () => {
        lineReads += 1;
        return HttpResponse.json(referenceLineViewFixture({ zones: [] }));
      }),
      http.put(`${LINE_PATH}/target`, () =>
        HttpResponse.json({ detail: { code: "reference_is_lump_sum", message: "Комплект." } }, { status: 422 })
      )
    );
    handlerState.referenceSearchResult = referenceSearchFixture([referenceSearchRowFixture()]);
    renderSheet();
    await search("подготовка");
    const button = within(await screen.findByTestId("ref-row-search-9001")).getByRole("button", { name: /сделать целью/ });
    const before = lineReads;
    await userEvent.click(button);
    await waitFor(() => expect(lineReads).toBeGreaterThan(before));
  });
});

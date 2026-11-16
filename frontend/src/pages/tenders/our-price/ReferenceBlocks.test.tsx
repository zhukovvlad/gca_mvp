import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  chapterPathFixture,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
  refLinkRowFixture,
  refRowFixture,
  referenceLineViewFixture,
  referenceLinkGroupFixture,
  referenceZoneGroupFixture,
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

function serve(make: () => OurPriceLineView) {
  server.use(http.get(LINE_PATH, () => HttpResponse.json(make())));
}

function renderSheet(user: typeof ADMIN | typeof MEMBER = ADMIN, props: Partial<LineSheetProps> = {}) {
  return renderWithProviders(<LineSheet {...baseProps} {...props} />, { initialUser: user });
}

const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));

/** Семь строк блока: разные договоры и цены, чтобы свёртка их не склеила. */
const sevenRows = () =>
  Array.from({ length: 7 }, (_, i) =>
    refRowFixture({
      analog_id: 9001 + i,
      ids: [9001 + i],
      title: `Строка договора ${i + 1}`,
      price: { net: `${8000 + i}.00`, shown: null },
    })
  );

describe("блок А: та же работа в другой зоне", () => {
  it("заголовок, зона группы, ступень, строка договора с ценой и отклонением", async () => {
    serve(() => referenceLineViewFixture());
    renderSheet();
    const block = await screen.findByTestId("sheet-reference-zones");
    expect(block).toHaveTextContent("Справочно: та же работа в другой зоне");
    expect(within(block).getByTestId("reference-zone-territory")).toHaveTextContent("зона: Территория");
    expect(block).toHaveTextContent("тот же вариант");
    const row = within(block).getByTestId("ref-row-zone-territory-9001");
    expect(row).toHaveTextContent("Объект Т");
    expect(row).toHaveTextContent("Подготовка из бетона В7,5, Н=0,10 м");
    expect(row).toHaveTextContent("8 372,92 ₽/м3");
    expect(row).toHaveTextContent("КП +182 %");
  });

  it("подпись панели у строки: нет аналога в своей зоне и где он есть", async () => {
    serve(() => referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: sevenRows() })] }));
    renderSheet();
    expect(await screen.findByTestId("sheet-state")).toHaveTextContent(
      "нет аналога в зоне «Подземная» · есть в зоне «Территория»: 7 строк, 1 договор"
    );
  });

  it("блока нет, когда групп нет: прежняя подпись панели", async () => {
    serve(() => referenceLineViewFixture({ zones: [] }));
    renderSheet();
    expect(await screen.findByTestId("sheet-state")).toHaveTextContent("нет аналога в зоне «подземная часть»");
    expect(screen.queryByTestId("sheet-reference-zones")).not.toBeInTheDocument();
  });

  it("семь строк: пять и «ещё 2»; «ещё N» раскрывает остальные", async () => {
    serve(() => referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: sevenRows() })] }));
    renderSheet();
    const more = await screen.findByTestId("zone-territory-more");
    expect(within(screen.getByTestId("zone-territory-rows")).getAllByTestId(/^ref-row-zone-territory-\d+$/)).toHaveLength(5);
    expect(more).toHaveTextContent("ещё 2");
    await userEvent.click(more);
    expect(within(screen.getByTestId("zone-territory-rows")).getAllByTestId(/^ref-row-zone-territory-\d+$/)).toHaveLength(7);
    expect(screen.getByTestId("zone-territory-more")).toHaveTextContent("свернуть");
  });

  it("ровно пять строк: «ещё» нет", async () => {
    serve(() => referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: sevenRows().slice(0, 5) })] }));
    renderSheet();
    await screen.findByTestId("zone-territory-rows");
    expect(screen.queryByTestId("zone-territory-more")).not.toBeInTheDocument();
  });

  it("свёрнутая строка: «×3» и «в 3 разделах:» со всеми путями", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [
          referenceZoneGroupFixture({
            rows: [
              refRowFixture({
                ids: [9001, 9002, 9003],
                chapter_paths: [
                  chapterPathFixture("Благоустройство", "МАФ"),
                  chapterPathFixture("Благоустройство", "Освещение"),
                  chapterPathFixture("Благоустройство", "Дорожки"),
                ],
              }),
            ],
          }),
        ],
      })
    );
    renderSheet();
    expect(await screen.findByTestId("ref-row-zone-territory-9001-folded")).toHaveTextContent("×3");
    const paths = screen.getByTestId("ref-row-zone-territory-9001-paths");
    expect(paths).toHaveTextContent("в 3 разделах:");
    expect(paths).toHaveTextContent("Благоустройство → МАФ");
    expect(paths).toHaveTextContent("Благоустройство → Освещение");
    expect(paths).toHaveTextContent("Благоустройство → Дорожки");
    expect(paths).toHaveClass("line-clamp-2");
    expect(paths).toHaveAttribute("title", expect.stringContaining("Благоустройство → Дорожки"));
  });

  it("без пересчёта (единицы равны) цены в единице КП нет, отклонение и выброс остаются", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [
          referenceZoneGroupFixture({
            rows: [refRowFixture({ conversion: null, deviation_pct: "182.00", outlier_ratio: "12.00" })],
          }),
        ],
      })
    );
    renderSheet();
    const row = await screen.findByTestId("ref-row-zone-territory-9001");
    expect(within(row).queryByTestId("ref-row-zone-territory-9001-kp-price")).not.toBeInTheDocument();
    expect(within(row).getByTestId("ref-row-zone-territory-9001-deviation")).toHaveTextContent("КП +182 %");
    expect(within(row).getByTestId("ref-row-zone-territory-9001-outlier")).toBeInTheDocument();
  });

  it("строка с обратным N: печать сервера «≈ 3,3333» без правки на клиенте", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [],
        links: [
          referenceLinkGroupFixture({
            rows: [
              refLinkRowFixture({
                conversion: { method: "link", link_id: 41, factor: "3.33333333333333", factor_shown: "≈ 3,3333", basis: "толщина 300 мм" },
              }),
            ],
          }),
        ],
      })
    );
    renderSheet();
    const row = await screen.findByTestId("ref-row-link-41-9101");
    expect(within(row).getByTestId("ref-row-link-41-9101-conversion")).toHaveTextContent("1 м2 = ≈ 3,3333 м3");
  });

  it("несвёрнутая строка: путь без «в N разделах» и без «×»", async () => {
    serve(() => referenceLineViewFixture());
    renderSheet();
    const paths = await screen.findByTestId("ref-row-zone-territory-9001-paths");
    expect(paths).toHaveTextContent("Благоустройство → МАФ");
    expect(paths).not.toHaveTextContent("в 1");
    expect(paths).toHaveClass("line-clamp-2");
    expect(paths).toHaveAttribute("title", "Благоустройство → МАФ");
    expect(screen.queryByTestId("ref-row-zone-territory-9001-folded")).not.toBeInTheDocument();
  });

  it("пометка выброса: «×12 к остальным», выделенная строка", async () => {
    serve(() =>
      referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: [refRowFixture({ outlier_ratio: "12.00" })] })] })
    );
    renderSheet();
    const row = await screen.findByTestId("ref-row-zone-territory-9001");
    expect(within(row).getByTestId("ref-row-zone-territory-9001-outlier")).toHaveTextContent("×12 к остальным");
    expect(row).toHaveAttribute("data-outlier", "true");
  });

  it("строка без пометки не выделена", async () => {
    serve(() => referenceLineViewFixture());
    renderSheet();
    const row = await screen.findByTestId("ref-row-zone-territory-9001");
    expect(row).not.toHaveAttribute("data-outlier");
    expect(screen.queryByTestId("ref-row-zone-territory-9001-outlier")).not.toBeInTheDocument();
  });

  it("договор с этим подрядчиком отмечен", async () => {
    serve(() =>
      referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: [refRowFixture({ with_this_contractor: true })] })] })
    );
    renderSheet();
    expect(await screen.findByTestId("ref-row-zone-territory-9001")).toHaveTextContent("договор с этим подрядчиком");
  });
});

describe("блок Г: семья по связи", () => {
  it.each([
    ["thickness", "Справочно: Устройство бетонной подготовки · м2 · пересчёт через толщину"],
    ["width", "Справочно: Устройство бетонной подготовки · м2 · пересчёт через ширину"],
    ["density", "Справочно: Устройство бетонной подготовки · м2 · пересчёт через плотность"],
  ] as const)("заголовок по виду связи %s", async (kind, title) => {
    serve(() => referenceLineViewFixture({ zones: [], links: [referenceLinkGroupFixture({ kind })] }));
    renderSheet();
    const block = await screen.findByTestId("sheet-reference-link-41");
    expect(within(block).getByRole("heading", { level: 4 })).toHaveTextContent(title);
  });

  it("постоянная связь: «постоянный: 1 т = 0,667 м3» из пересчёта строк", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [],
        links: [
          referenceLinkGroupFixture({
            kind: "constant",
            family: { id: 72, title: "Щебень гравийный", unit: "т" },
            rows: [
              refLinkRowFixture({
                unit: "т",
                conversion: { method: "link", link_id: 41, factor: "0.667", factor_shown: "0,667", basis: "насыпная плотность 1,5 т/м3" },
              }),
            ],
          }),
        ],
      })
    );
    renderSheet();
    const block = await screen.findByTestId("sheet-reference-link-41");
    expect(within(block).getByRole("heading", { level: 4 })).toHaveTextContent(
      "Справочно: Щебень гравийный · т · пересчёт постоянный: 1 т = 0,667 м3"
    );
  });

  it("строка с пересчётом: «1 м2 = 0,1 м3», основание, цена в единице КП и отклонение", async () => {
    serve(() => referenceLineViewFixture({ zones: [], links: [referenceLinkGroupFixture()] }));
    renderSheet();
    const row = await screen.findByTestId("ref-row-link-41-9101");
    expect(within(row).getByTestId("ref-row-link-41-9101-conversion")).toHaveTextContent(
      "1 м2 = 0,1 м3 — толщина 100 мм"
    );
    expect(within(row).getByTestId("ref-row-link-41-9101-kp-price")).toHaveTextContent("11 072,40 ₽/м3");
    expect(within(row).getByTestId("ref-row-link-41-9101-kp-price")).not.toHaveTextContent("≈");
    expect(within(row).getByTestId("ref-row-link-41-9101-deviation")).toHaveTextContent("КП +114 %");
    expect(within(row).getByTestId("ref-row-link-41-9101-price")).toHaveTextContent("1 107,24 ₽/м2");
  });

  it("пять строк и «ещё N» — как у блока А", async () => {
    const rows = Array.from({ length: 6 }, (_, i) => refLinkRowFixture({ analog_id: 9100 + i, ids: [9100 + i] }));
    serve(() => referenceLineViewFixture({ zones: [], links: [referenceLinkGroupFixture({ rows })] }));
    renderSheet();
    const more = await screen.findByTestId("link-41-more");
    expect(more).toHaveTextContent("ещё 1");
    expect(within(screen.getByTestId("link-41-rows")).getAllByTestId(/^ref-row-link-41-\d+$/)).toHaveLength(5);
  });

  it("блоки А и Г независимы: у строки с no_zone_match и связью видны оба", async () => {
    serve(() => referenceLineViewFixture({ links: [referenceLinkGroupFixture()] }));
    renderSheet();
    expect(await screen.findByTestId("sheet-reference-zones")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-reference-link-41")).toBeInTheDocument();
  });

  it("группа из другого класса помечена", async () => {
    serve(() => referenceLineViewFixture({ zones: [], links: [referenceLinkGroupFixture({ other_class: true })] }));
    renderSheet();
    expect(await screen.findByTestId("reference-link-41")).toHaveTextContent("другой класс");
  });
});

describe("«сделать целью»: права и запись", () => {
  it("member видит блоки без кнопок выбора", async () => {
    serve(() => referenceLineViewFixture({ links: [referenceLinkGroupFixture()] }));
    renderSheet(MEMBER);
    expect(await screen.findByTestId("ref-row-zone-territory-9001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /сделать целью/ })).not.toBeInTheDocument();
  });

  it("admin: строка той же единицы, пересчёта нет — сразу PUT с origin zone и conversion null", async () => {
    serve(() => referenceLineViewFixture());
    renderSheet(ADMIN, { seriesId: 3 });
    const row = await screen.findByTestId("ref-row-zone-territory-9001");
    await userEvent.click(within(row).getByRole("button", { name: /сделать целью/ }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9001,
      origin: "zone",
      conversion: null,
      confirm_outlier: false,
    });
    expect(new URL(targets()[0].url).searchParams.get("inflation_series_id")).toBe("3");
    expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument();
  });

  it("строка блока Г той же единицы: origin link", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [],
        links: [referenceLinkGroupFixture({ rows: [refLinkRowFixture({ unit: "м3", conversion: null })] })],
      })
    );
    renderSheet();
    await userEvent.click(within(await screen.findByTestId("ref-row-link-41-9101")).getByRole("button", { name: /сделать целью/ }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toMatchObject({ origin: "link", conversion: null });
  });

  it("помеченная строка: сначала подтверждение, PUT без него не уходит; после — confirm_outlier true", async () => {
    serve(() =>
      referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: [refRowFixture({ outlier_ratio: "12.00" })] })] })
    );
    renderSheet();
    await userEvent.click(within(await screen.findByTestId("ref-row-zone-territory-9001")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    expect(dialog).toHaveTextContent("Строка помечена выбросом");
    expect(within(dialog).getByTestId("reference-dialog-outlier")).toHaveTextContent("×12 к остальным");
    expect(targets()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Всё равно сделать целью" }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toMatchObject({ analog_id: 9001, confirm_outlier: true, conversion: null });
  });

  it("помеченная строка: отмена подтверждения — запроса нет, диалог закрыт", async () => {
    serve(() =>
      referenceLineViewFixture({ zones: [referenceZoneGroupFixture({ rows: [refRowFixture({ outlier_ratio: "12.00" })] })] })
    );
    renderSheet();
    await userEvent.click(within(await screen.findByTestId("ref-row-zone-territory-9001")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
    expect(targets()).toHaveLength(0);
  });

  it("сервер потребовал подтверждения, а пометки на экране нет: диалог с отношением, затем PUT с confirm_outlier true", async () => {
    serve(() => referenceLineViewFixture());
    let asked = 0;
    server.use(
      http.put(`${LINE_PATH}/target`, async ({ request }) => {
        const body = (await request.json()) as { confirm_outlier: boolean };
        asked += 1;
        if (!body.confirm_outlier) {
          return HttpResponse.json(
            { detail: { code: "outlier_confirmation_required", message: "Подтвердите выбор.", ratio: "8.5" } },
            { status: 409 }
          );
        }
        return new HttpResponse(null, { status: 204 });
      })
    );
    renderSheet();
    await userEvent.click(within(await screen.findByTestId("ref-row-zone-territory-9001")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    expect(within(dialog).getByTestId("reference-dialog-outlier")).toHaveTextContent("×8,5 к остальным");
    await userEvent.click(within(dialog).getByRole("button", { name: "Всё равно сделать целью" }));
    await waitFor(() => expect(asked).toBe(2));
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
  });

  it("у системы справочных блоков и поиска нет", async () => {
    serve(() => ({
      ...referenceLineViewFixture({ zones: [], links: [] }),
      line: ourPriceLineFixture({ kind: "system", unit: "м2" }),
    }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByTestId("sheet-reference-search")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sheet-reference-zones")).not.toBeInTheDocument();
  });
});

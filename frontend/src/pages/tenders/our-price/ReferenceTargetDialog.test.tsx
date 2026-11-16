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
  refLinkRowFixture,
  referenceLineViewFixture,
  referenceLinkGroupFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceLineView, RefRow } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";
import { SAVING_LABEL } from "./ourPriceCopy";
import { needsConversion } from "./needsConversion";

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ADMIN = { id: 1, email: "admin@example.test", role: "admin" } as const;

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

const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));

/** Панель с одной строкой блока Г в м2 (единица КП — м3): диалог пересчёта открывается кнопкой строки. */
async function openDialog(row: RefRow = refLinkRowFixture()) {
  serve(() =>
    referenceLineViewFixture({ zones: [], links: [referenceLinkGroupFixture({ rows: [row] })] })
  );
  renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
  const rowEl = await screen.findByTestId(`ref-row-link-41-${row.analog_id}`);
  await userEvent.click(within(rowEl).getByRole("button", { name: /сделать целью/ }));
  return screen.findByTestId("reference-dialog");
}

describe("needsConversion", () => {
  it("равные единицы — пересчёт не нужен", () => {
    expect(needsConversion({ unit: "м3" }, "м3")).toBe(false);
  });

  it("различные единицы — нужен", () => {
    expect(needsConversion({ unit: "м2" }, "м3")).toBe(true);
  });

  it("пустая единица строки договора или строки КП — нужен: равенства нет", () => {
    expect(needsConversion({ unit: null }, "м3")).toBe(true);
    expect(needsConversion({ unit: "м3" }, null)).toBe(true);
    expect(needsConversion({ unit: null }, null)).toBe(true);
  });
});

describe("диалог пересчёта: по связи", () => {
  it("показывает N и основание только для просмотра, полей ввода нет", async () => {
    const dialog = await openDialog();
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,1");
    expect(within(dialog).getByTestId("reference-dialog-basis")).toHaveTextContent("толщина 100 мм");
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("цена в единице КП берётся из ответа сервера", async () => {
    const dialog = await openDialog();
    expect(within(dialog).getByTestId("reference-dialog-kp-price")).toHaveTextContent("11 072,40 ₽/м3");
  });

  it("PUT уходит с N и основанием строки без преобразования: «0.100» остаётся «0.100»", async () => {
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9101,
      origin: "link",
      conversion: { method: "link", link_id: 41, expected_factor: "0.100", expected_basis: "толщина 100 мм" },
      confirm_outlier: false,
    });
  });

  it("успех закрывает диалог", async () => {
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
  });

  it("«Отмена» закрывает диалог, запроса нет", async () => {
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
    expect(targets()).toHaveLength(0);
  });

  it("пока цель пишется — серый слой с подписью сохранения, кнопка погашена", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const dialog = await openDialog();
    server.use(
      http.put(`${LINE_PATH}/target`, async () => {
        await hold;
        return new HttpResponse(null, { status: 204 });
      })
    );
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    expect(await screen.findByTestId("recalc-overlay")).toHaveTextContent(SAVING_LABEL);
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
  });

  it("409 conversion_stale: подменяет N и основание пришедшими, ждёт нового нажатия, второй PUT — с новыми", async () => {
    const dialog = await openDialog();
    const bodies: Array<{ conversion: { expected_factor: string; expected_basis: string } }> = [];
    server.use(
      http.put(`${LINE_PATH}/target`, async ({ request }) => {
        bodies.push((await request.json()) as (typeof bodies)[number]);
        if (bodies.length === 1) {
          return HttpResponse.json(
            {
              detail: {
                code: "conversion_stale",
                message: "Пересчёт по связи изменился.",
                factor: "0.125",
                factor_shown: "0,125",
                basis: "толщина 125 мм",
              },
            },
            { status: 409 }
          );
        }
        return new HttpResponse(null, { status: 204 });
      })
    );
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(within(dialog).getByTestId("reference-dialog-stale")).toBeInTheDocument());
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,125");
    expect(within(dialog).getByTestId("reference-dialog-basis")).toHaveTextContent("толщина 125 мм");
    expect(bodies).toHaveLength(1);
    expect(screen.getByTestId("reference-dialog")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0].conversion).toMatchObject({ expected_factor: "0.100", expected_basis: "толщина 100 мм" });
    expect(bodies[1].conversion).toMatchObject({ expected_factor: "0.125", expected_basis: "толщина 125 мм" });
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
  });

  it("после conversion_stale цена в единице КП — из перезапрошенной панели", async () => {
    let stale = false;
    serve(() =>
      referenceLineViewFixture({
        zones: [],
        links: [
          referenceLinkGroupFixture({
            rows: [
              refLinkRowFixture(
                stale
                  ? {
                      conversion: { method: "link", link_id: 41, factor: "0.125", factor_shown: "0,125", basis: "толщина 125 мм" },
                      kp_unit_price: { net: "8857.92", shown: null },
                    }
                  : {}
              ),
            ],
          }),
        ],
      })
    );
    server.use(
      http.put(`${LINE_PATH}/target`, () => {
        stale = true;
        return HttpResponse.json(
          { detail: { code: "conversion_stale", message: "Изменилось.", factor: "0.125", factor_shown: "0,125", basis: "толщина 125 мм" } },
          { status: 409 }
        );
      })
    );
    renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
    await userEvent.click(within(await screen.findByTestId("ref-row-link-41-9101")).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() =>
      expect(within(dialog).getByTestId("reference-dialog-kp-price")).toHaveTextContent("8 857,92 ₽/м3")
    );
  });
});

describe("диалог пересчёта: ручной ввод", () => {
  const manualRow = () => refLinkRowFixture({ conversion: null, kp_unit_price: null, deviation_pct: null });

  it("связи нет — поля N и основания; «Сделать целью» неактивна, пока они пусты", async () => {
    const dialog = await openDialog(manualRow());
    expect(within(dialog).getByTestId("reference-dialog-manual")).toBeInTheDocument();
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
  });

  it("N без основания — кнопка неактивна", async () => {
    const dialog = await openDialog(manualRow());
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), "0,1");
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
  });

  it("основание из одних пробелов не считается основанием", async () => {
    const dialog = await openDialog(manualRow());
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), "0,1");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "   ");
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
  });

  it.each(["abc", "0", "0,0000001", "-1"])("N «%s» — кнопка неактивна", async (value) => {
    const dialog = await openDialog(manualRow());
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), value);
    await userEvent.type(within(dialog).getByLabelText("Основание"), "толщина 100 мм");
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
  });

  it("N и основание заданы — PUT method manual: запятая → точка, основание обрезано", async () => {
    const dialog = await openDialog(manualRow());
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), "0,667");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "  насыпная плотность  ");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9101,
      origin: "link",
      conversion: { method: "manual", factor: "0.667", basis: "насыпная плотность" },
      confirm_outlier: false,
    });
  });

  it("422 manual_conversion_invalid: текст сервера в диалоге, диалог остаётся открытым", async () => {
    const dialog = await openDialog(manualRow());
    server.use(
      http.put(`${LINE_PATH}/target`, () =>
        HttpResponse.json(
          { detail: { code: "manual_conversion_invalid", message: "Множитель больше 10⁶." } },
          { status: 422 }
        )
      )
    );
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), "5");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "основание");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    expect(await within(dialog).findByTestId("reference-dialog-manual-error")).toHaveTextContent("Множитель больше 10⁶.");
    expect(screen.getByTestId("reference-dialog")).toBeInTheDocument();
  });
});

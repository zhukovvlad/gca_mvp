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
  refRowFixture,
  referenceLineViewFixture,
  referenceLinkGroupFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceLineView, RefRow } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";
import { SAVING_LABEL } from "./ourPriceCopy";

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

  /** Панель, у которой после `409` меняется пересчёт (N 0,125, цена 8 857,92); `getGate` задерживает или ломает этот перезапрос. */
  async function openStale(getGate?: { hold?: Promise<void>; fail?: boolean }) {
    let stale = false;
    const bodies: Array<{ conversion: { expected_factor: string; expected_basis: string } }> = [];
    const newRow = refLinkRowFixture({
      conversion: { method: "link", link_id: 41, factor: "0.125", factor_shown: "0,125", basis: "толщина 125 мм" },
      kp_unit_price: { net: "8857.92", shown: null },
    });
    server.use(
      http.get(LINE_PATH, async () => {
        if (stale && getGate?.hold) await getGate.hold;
        if (stale && getGate?.fail) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(
          referenceLineViewFixture({
            zones: [],
            links: [referenceLinkGroupFixture({ rows: [stale ? newRow : refLinkRowFixture()] })],
          })
        );
      }),
      http.put(`${LINE_PATH}/target`, async ({ request }) => {
        bodies.push((await request.json()) as (typeof bodies)[number]);
        if (bodies.length === 1) {
          stale = true;
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
    renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
    await userEvent.click(
      within(await screen.findByTestId("ref-row-link-41-9101")).getByRole("button", { name: /сделать целью/ })
    );
    const dialog = await screen.findByTestId("reference-dialog");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(within(dialog).getByTestId("reference-dialog-stale")).toBeInTheDocument());
    return { dialog, bodies };
  }

  it("409 conversion_stale: N и основание — пришедшие, цена обновляется, второй PUT — с новыми", async () => {
    const { dialog, bodies } = await openStale();
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,125");
    expect(within(dialog).getByTestId("reference-dialog-basis")).toHaveTextContent("толщина 125 мм");
    expect(bodies).toHaveLength(1);
    await waitFor(() => expect(within(dialog).getByTestId("reference-dialog-submit")).toBeEnabled());
    expect(within(dialog).getByTestId("reference-dialog-kp-price")).toHaveTextContent("8 857,92 ₽/м3");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0].conversion).toMatchObject({ expected_factor: "0.100", expected_basis: "толщина 100 мм" });
    expect(bodies[1].conversion).toMatchObject({ expected_factor: "0.125", expected_basis: "толщина 125 мм" });
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
  });

  it("после 409, пока панель перечитывается: цены и отклонения нет, кнопка погашена; после ответа — новая цена и второй PUT с новым N", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { dialog, bodies } = await openStale({ hold });
    const submit = within(dialog).getByTestId("reference-dialog-submit");
    expect(submit).toBeDisabled();
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,125");
    expect(within(dialog).getByTestId("reference-dialog-stale-refreshing")).toBeInTheDocument();
    expect(within(dialog).queryByTestId("reference-dialog-kp-price")).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent("11 072,40");
    await userEvent.click(submit);
    expect(bodies).toHaveLength(1);
    release();
    await waitFor(() => expect(within(dialog).getByTestId("reference-dialog-kp-price")).toHaveTextContent("8 857,92"));
    expect(within(dialog).queryByTestId("reference-dialog-stale-refreshing")).not.toBeInTheDocument();
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeEnabled();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1].conversion).toMatchObject({ expected_factor: "0.125", expected_basis: "толщина 125 мм" });
  });

  it("после 409 перезапрос панели упал: кнопка остаётся погашенной, старой цены нет", async () => {
    const { dialog, bodies } = await openStale({ fail: true });
    await waitFor(() => expect(within(dialog).getByTestId("reference-dialog-stale-refreshing")).toBeInTheDocument());
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
    expect(within(dialog).queryByTestId("reference-dialog-kp-price")).not.toBeInTheDocument();
    expect(bodies).toHaveLength(1);
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

  it("подписи единиц равны, а needs_conversion = true (у единицы нет id каталога): открывается ручной диалог, PUT — manual", async () => {
    // Решает флаг сервера, а не сравнение подписей: единицы «м3» и «м3» с разными или пустыми id пересчёта требуют.
    const dialog = await openDialog(
      refRowFixture({ analog_id: 9102, ids: [9102], unit: "м3", needs_conversion: true, conversion: null, kp_unit_price: null })
    );
    expect(within(dialog).getByTestId("reference-dialog-manual")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Множитель N" }), "2");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "по справке");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toMatchObject({ analog_id: 9102, conversion: { method: "manual", factor: "2", basis: "по справке" } });
  });

  it("подписи единиц различны, а needs_conversion = false: цель пишется сразу, conversion = null, диалога нет", async () => {
    serve(() =>
      referenceLineViewFixture({
        zones: [],
        links: [
          referenceLinkGroupFixture({
            rows: [refLinkRowFixture({ analog_id: 9103, ids: [9103], needs_conversion: false, conversion: null, kp_unit_price: null })],
          }),
        ],
      })
    );
    renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
    const rowEl = await screen.findByTestId("ref-row-link-41-9103");
    await userEvent.click(within(rowEl).getByRole("button", { name: /сделать целью/ }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toMatchObject({ analog_id: 9103, conversion: null });
    expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument();
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

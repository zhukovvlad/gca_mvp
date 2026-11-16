import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState, schemaParameter } from "@/test/handlers";
import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  refRowFixture,
  referenceLinkGroupFixture,
  referenceLineViewFixture,
  referenceZoneGroupFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { FamilyLinkBody, FamilySchemaParameter, RefRow } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";

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

const LINE_FAMILY = 70;
const ROW_FAMILY = 72;
const REASON = "Значение параметра не число.";

/** Строка договора без связи: единица `unit`, семья 72, пересчёта нет. */
function plainRow(overrides: Partial<RefRow> = {}): RefRow {
  return refRowFixture({
    analog_id: 9301,
    ids: [9301],
    unit: "м2",
    family_id: ROW_FAMILY,
    needs_conversion: true,
    conversion: null,
    no_conversion_reason: REASON,
    kp_unit_price: null,
    deviation_pct: null,
    ...overrides,
  });
}

/** Та же строка после создания связи: пересчёт отдаёт сервер (N отличается от всего, что набрал бы клиент). */
function linkedRow(overrides: Partial<RefRow> = {}): RefRow {
  return plainRow({
    conversion: { method: "link", link_id: 900, factor: "0.125", factor_shown: "0,125", basis: "толщина 125 мм" },
    no_conversion_reason: null,
    ...overrides,
  });
}

function giveFamily(id: number, title: string, unit: string, parameters: FamilySchemaParameter[]) {
  const base = handlerState.workFamilies.find((f) => f.id === 43)!;
  handlerState.workFamilies.push({ ...base, id, title, unit_symbol: unit, status: "active" });
  handlerState.familySchemas[id] = {
    family_id: id,
    status: "frozen",
    version: 1,
    ready_to_build: true,
    build_wait: null,
    building: false,
    values_jobs_live: 0,
    revision: 1,
    parameters,
  };
}

interface Setup {
  row?: RefRow;
  /** Строка, которую панель отдаёт после записи связи; `null` — панель не меняется. */
  after?: RefRow | null;
  lineUnit?: string | null;
  lineFamily?: number | null;
  lineParams?: FamilySchemaParameter[];
  rowParams?: FamilySchemaParameter[];
  rowUnit?: string;
}

const thicknessLine = [schemaParameter(701, 1, "Толщина, мм", []), schemaParameter(702, 2, "Марка", [])];
const thicknessRow = [
  schemaParameter(723, 1, "Толщина диапазон", [], { value_kind: "range" }),
  schemaParameter(721, 2, "Класс", []),
  schemaParameter(722, 3, "Толщина слоя, мм", []),
];

/** Панель одной строки договора из блока А; диалог открывается кнопкой строки. */
async function openDialog(setup: Setup = {}) {
  const row = setup.row ?? plainRow(setup.rowUnit === undefined ? {} : { unit: setup.rowUnit });
  const lineFamily = setup.lineFamily === undefined ? LINE_FAMILY : setup.lineFamily;
  giveFamily(LINE_FAMILY, "Семья КП", setup.lineUnit ?? "м3", setup.lineParams ?? thicknessLine);
  giveFamily(ROW_FAMILY, "Семья договора", row.unit ?? "м2", setup.rowParams ?? thicknessRow);
  const lineFetches = { afterPost: 0 };
  server.use(
    http.get(LINE_PATH, () => {
      const posted = handlerState.familyLinkRequests.length > 0;
      if (posted) lineFetches.afterPost += 1;
      const shown = posted && setup.after !== undefined && setup.after !== null ? setup.after : row;
      return HttpResponse.json(
        referenceLineViewFixture({
          zones: [referenceZoneGroupFixture({ zone: "territory", rows: [shown] })],
          line: { unit: setup.lineUnit ?? "м3", family_id: lineFamily },
        })
      );
    })
  );
  renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
  const rowEl = await screen.findByTestId(`ref-row-zone-territory-${row.analog_id}`);
  await userEvent.click(within(rowEl).getByRole("button", { name: /сделать целью/ }));
  const dialog = await screen.findByTestId("reference-dialog");
  return { dialog, lineFetches };
}

const checkbox = (dialog: HTMLElement) => within(dialog).queryByRole("checkbox", { name: /Запомнить для семей/ });
const links = () => handlerState.familyLinkRequests;
const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));

async function remember(dialog: HTMLElement) {
  const box = checkbox(dialog);
  expect(box).not.toBeNull();
  await userEvent.click(box!);
}

async function pick(label: string | RegExp, option: string | RegExp) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("«Запомнить для семей»: когда флажок виден", () => {
  it("админ, связи нет, обе семьи есть и различны, единицы м2 и м3 — флажок есть", async () => {
    const { dialog } = await openDialog();
    expect(checkbox(dialog)).not.toBeNull();
  });

  it("у строки уже есть пересчёт по связи — флажка нет", async () => {
    const { dialog } = await openDialog({ row: linkedRow() });
    expect(checkbox(dialog)).toBeNull();
  });

  it("у строки КП нет семьи — флажка нет", async () => {
    const { dialog } = await openDialog({ lineFamily: null });
    expect(checkbox(dialog)).toBeNull();
  });

  it("у строки договора нет семьи — флажка нет", async () => {
    const { dialog } = await openDialog({ row: plainRow({ family_id: null }) });
    expect(checkbox(dialog)).toBeNull();
  });

  it("семьи совпадают — флажка нет", async () => {
    const { dialog } = await openDialog({ lineFamily: ROW_FAMILY });
    expect(checkbox(dialog)).toBeNull();
  });

  it("у строки договора нет единицы — флажка нет", async () => {
    const { dialog } = await openDialog({ row: plainRow({ unit: null }) });
    expect(checkbox(dialog)).toBeNull();
  });

  it("строка из блока Г без пересчёта: связь там уже есть по построению — флажка нет", async () => {
    giveFamily(LINE_FAMILY, "Семья КП", "м3", thicknessLine);
    giveFamily(ROW_FAMILY, "Семья договора", "м2", thicknessRow);
    server.use(
      http.get(LINE_PATH, () =>
        HttpResponse.json(
          referenceLineViewFixture({
            zones: [],
            links: [referenceLinkGroupFixture({ rows: [plainRow({ analog_id: 9302 })] })],
            line: { family_id: LINE_FAMILY },
          })
        )
      )
    );
    renderWithProviders(<LineSheet {...baseProps} />, { initialUser: ADMIN });
    const rowEl = await screen.findByTestId("ref-row-link-41-9302");
    await userEvent.click(within(rowEl).getByRole("button", { name: /сделать целью/ }));
    const dialog = await screen.findByTestId("reference-dialog");
    expect(checkbox(dialog)).toBeNull();
  });

  it("выброс при равных единицах — диалог только подтверждает, флажка нет", async () => {
    const { dialog } = await openDialog({ row: plainRow({ unit: "м3", needs_conversion: false, outlier_ratio: "3.2" }) });
    expect(checkbox(dialog)).toBeNull();
  });
});

describe("«Запомнить для семей»: вид с параметром", () => {
  it("предзаполняет параметры по «толщ» (точные, не диапазон), POST — с параметрами сторон и null вместо множителя", async () => {
    const { dialog } = await openDialog();
    await remember(dialog);
    expect(within(dialog).getByRole("combobox", { name: /Параметр семьи строки КП/ })).toHaveTextContent("Толщина, мм");
    expect(within(dialog).getByRole("combobox", { name: /Параметр семьи строки договора/ })).toHaveTextContent(
      "Толщина слоя, мм"
    );
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(links()[0]).toMatchObject({ method: "POST" });
    expect(links()[0].body as FamilyLinkBody).toEqual({
      family_a_id: LINE_FAMILY,
      family_b_id: ROW_FAMILY,
      kind: "thickness",
      param_a_id: 701,
      param_b_id: 722,
      factor_from_family_id: null,
      factor: null,
      basis: null,
    });
  });

  it("параметры не из точных значений в списке не предлагаются", async () => {
    const { dialog } = await openDialog();
    await remember(dialog);
    await userEvent.click(within(dialog).getByRole("combobox", { name: /Параметр семьи строки договора/ }));
    expect(await screen.findByRole("option", { name: "Класс" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Толщина диапазон" })).not.toBeInTheDocument();
  });

  it("после связи цель НЕ пишется: диалог показывает N, основание и цену от сервера, цель — следующим нажатием", async () => {
    const { dialog, lineFetches } = await openDialog({
      after: linkedRow({ kp_unit_price: { net: "7000.00", shown: null }, deviation_pct: "10", outlier_ratio: "2.5" }),
    });
    await remember(dialog);
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(await within(dialog).findByTestId("reference-dialog-link-saved")).toBeInTheDocument();
    expect(lineFetches.afterPost).toBeGreaterThanOrEqual(1);
    expect(within(dialog).getByTestId("reference-dialog-factor")).toHaveTextContent("0,125");
    expect(within(dialog).getByTestId("reference-dialog-basis")).toHaveTextContent("толщина 125 мм");
    expect(within(dialog).getByTestId("reference-dialog-kp-price")).toHaveTextContent("7 000");
    expect(within(dialog).getByTestId("reference-dialog-outlier")).toBeInTheDocument();
    expect(checkbox(dialog)).toBeNull();
    expect(targets()).toHaveLength(0);
    expect(screen.getByTestId("reference-dialog")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({
      kind: "reference",
      analog_id: 9301,
      origin: "zone",
      conversion: { method: "link", link_id: 900, expected_factor: "0.125", expected_basis: "толщина 125 мм" },
      confirm_outlier: true,
    });
    await waitFor(() => expect(screen.queryByTestId("reference-dialog")).not.toBeInTheDocument());
  });

  it("связь заведена, а N сервер не посчитал: диалог остаётся с причиной строки, цели нет", async () => {
    const { dialog } = await openDialog({ after: null });
    await remember(dialog);
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(await within(dialog).findByTestId("reference-dialog-link-note")).toHaveTextContent(REASON);
    expect(targets()).toHaveLength(0);
    expect(screen.getByTestId("reference-dialog")).toBeInTheDocument();
    expect(checkbox(dialog)).toBeNull();
  });

  it("без флажка связь не заводится: запрос только на цель", async () => {
    const { dialog } = await openDialog();
    await userEvent.type(within(dialog).getByLabelText("Множитель N"), "0,125");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "по чертежу");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(links()).toHaveLength(0);
  });

  it("отказ связи 409 link_exists: текст на месте, цели нет", async () => {
    handlerState.familyLinkRefusal = { status: 409, code: "link_exists" };
    const { dialog } = await openDialog({ after: linkedRow() });
    await remember(dialog);
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    expect(await within(dialog).findByTestId("reference-dialog-link-refusal")).toHaveTextContent(
      "связь этой пары уже есть"
    );
    expect(targets()).toHaveLength(0);
  });

  it.each([
    ["ширина", "м2", "м", "Ширина, мм", "Ширина полосы, мм", "width"],
    ["плотность", "т", "м3", "Плотность, т/м3", "Плотность насыпная", "density"],
  ])("%s: единицы подходят виду, предзаполнение по корню слова", async (_name, lineUnit, rowUnit, lineName, rowName, kind) => {
    const { dialog } = await openDialog({
      lineUnit,
      rowUnit,
      lineParams: [schemaParameter(701, 1, "Марка", []), schemaParameter(703, 2, lineName, [])],
      rowParams: [schemaParameter(722, 1, rowName, []), schemaParameter(721, 2, "Класс", [])],
    });
    await remember(dialog);
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(links()[0].body).toMatchObject({ kind, param_a_id: 703, param_b_id: 722 });
  });

  it("параметра с корнем нет — выбор пуст, отправка погашена, пока человек не выберет", async () => {
    const { dialog } = await openDialog({
      lineParams: [schemaParameter(702, 1, "Марка", [])],
      rowParams: [schemaParameter(721, 1, "Класс", [])],
    });
    await remember(dialog);
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
    await pick(/Параметр семьи строки договора/, "Класс");
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeEnabled();
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(links()[0].body).toMatchObject({ kind: "thickness", param_a_id: null, param_b_id: 721 });
  });
});

describe("«Запомнить для семей»: постоянный множитель", () => {
  it("единицы не подходят ни одному виду: только constant, N и основание вводит человек, N уходит строкой с точкой", async () => {
    const { dialog } = await openDialog({ rowUnit: "кг", lineUnit: "м3" });
    await remember(dialog);
    expect(within(dialog).queryByRole("combobox", { name: /Вид связи/ })).not.toBeInTheDocument();
    expect(within(dialog).getByTestId("reference-dialog-submit")).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Множитель N"), "0,5");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "по паспорту поставщика");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(links()[0].body).toEqual({
      family_a_id: LINE_FAMILY,
      family_b_id: ROW_FAMILY,
      kind: "constant",
      param_a_id: null,
      param_b_id: null,
      factor_from_family_id: ROW_FAMILY,
      factor: "0.5",
      basis: "по паспорту поставщика",
    });
  });

  it("м2 и м3: вид можно сменить на «постоянный множитель»", async () => {
    const { dialog } = await openDialog();
    await remember(dialog);
    await pick(/Вид связи/, "постоянный множитель");
    expect(within(dialog).queryByRole("combobox", { name: /Параметр семьи/ })).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Множитель N"), "0.1");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "по проекту");
    await userEvent.click(within(dialog).getByTestId("reference-dialog-submit"));
    await waitFor(() => expect(links()).toHaveLength(1));
    expect(links()[0].body).toMatchObject({
      kind: "constant",
      param_a_id: null,
      param_b_id: null,
      factor: "0.1",
      basis: "по проекту",
    });
  });
});

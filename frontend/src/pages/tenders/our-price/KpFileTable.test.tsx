import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  kpFilePreviewFixture,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { KpFilePreview } from "@/types/domain";

import { KpFileTable } from "./KpFileTable";
import { KP_FILE_OURS_COLUMNS } from "./ourPriceCopy";

function renderTable(preview: KpFilePreview, canEdit = false) {
  return renderWithProviders(
    <KpFileTable
      tenderId={OUR_PRICE_TENDER_ID}
      roundId={OUR_PRICE_ROUND_ID}
      offerId={OUR_PRICE_OFFER_ID}
      preview={preview}
      canEdit={canEdit}
    />
  );
}

const headerTitles = () => within(screen.getByTestId("kp-file-table")).getAllByRole("columnheader").map((h) => h.textContent);
const decisions = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision");

afterEach(() => {
  handlerState.kpFileDecisionOutcome = "ok";
});

describe("KpFileTable: столбцы и строки как в ответе", () => {
  it("без ряда: столбцы ровно из ответа и в его порядке, столбца ИПЦ нет", () => {
    const preview = kpFilePreviewFixture({ withInflation: false });
    renderTable(preview);
    expect(headerTitles()).toEqual(preview.columns.map((c) => c.title));
    expect(headerTitles()).not.toContain("Приведение по ИПЦ");
    expect(screen.queryByTestId(`cell-${OUR_PRICE_LINE_ID}-inflation`)).not.toBeInTheDocument();
  });

  it("с рядом: столбец ИПЦ есть и стоит там, где пришёл, а ячейка несёт текст ответа", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    renderTable(preview);
    expect(headerTitles()).toEqual(preview.columns.map((c) => c.title));
    expect(headerTitles()).toContain("Приведение по ИПЦ");
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-inflation`)).toHaveTextContent("Договор 1: ×1.0873");
  });

  it("столбец, которого нет в ответе, не рисуется, даже если строка несёт ключ", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    preview.columns = preview.columns.filter((c) => c.key !== "source");
    renderTable(preview);
    expect(headerTitles()).not.toContain("Источник");
    expect(screen.queryByTestId(`cell-${OUR_PRICE_LINE_ID}-source`)).not.toBeInTheDocument();
  });

  it("раздел подрядчика — одна ячейка на всю ширину с номером и названием", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    renderTable(preview);
    const section = screen.getByTestId("kp-section-0");
    const cells = within(section).getAllByRole("cell");
    expect(cells).toHaveLength(1);
    expect(cells[0]).toHaveAttribute("colspan", String(preview.columns.length));
    expect(cells[0]).toHaveTextContent("1 Раздел 1");
  });

  it("деньги — строкой ответа с двумя знаками, пустое значение — пустая ячейка, отклонение с «%»", () => {
    renderTable(kpFilePreviewFixture({ withInflation: false }));
    const id = OUR_PRICE_LINE_ID;
    // 22 знака после запятой не теряются и не растягивают ячейку: округление строкой до копеек.
    expect(screen.getByTestId(`cell-${id}-kp_unit_price`).textContent?.replace(/\s/g, " ")).toBe("1 464,00");
    expect(screen.getByTestId(`cell-${id}-kp_total`).textContent?.replace(/\s/g, " ")).toBe("54 900,00");
    expect(screen.getByTestId(`cell-${id}-ours`).textContent?.replace(/\s/g, " ")).toBe("1 220,00");
    // Как в остальных ячейках отклонения: запятая, знак, десятая доля (ответ — "20.0").
    expect(screen.getByTestId(`cell-${id}-deviation_pct`)).toHaveTextContent(/^\+20,0%$/);
    const second = id + 1;
    expect(screen.getByTestId(`cell-${second}-ours`)).toBeEmptyDOMElement();
    expect(screen.getByTestId(`cell-${second}-deviation_pct`)).toBeEmptyDOMElement();
    expect(screen.getByTestId(`cell-${second}-source`)).toBeEmptyDOMElement();
  });

  it("наши столбцы выделены в шапке и в ячейках, остальные нет", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    renderTable(preview);
    const heads = within(screen.getByTestId("kp-file-table")).getAllByRole("columnheader");
    preview.columns.forEach((column, index) => {
      const ours = KP_FILE_OURS_COLUMNS.has(column.key);
      expect(heads[index].getAttribute("data-ours")).toBe(ours ? "true" : null);
      expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-${column.key}`).getAttribute("data-ours")).toBe(
        ours ? "true" : null
      );
    });
    // Перечень наших столбцов — ровно те, что бэкенд кладёт справа от столбцов подрядчика.
    expect([...KP_FILE_OURS_COLUMNS].sort()).toEqual(["comment", "deviation_pct", "inflation", "ours", "source"]);
  });

  it("сортировок и фильтров нет: в шапке таблицы ни одной кнопки", () => {
    renderTable(kpFilePreviewFixture({ withInflation: true }));
    const head = within(screen.getByTestId("kp-file-table")).getAllByRole("columnheader");
    for (const cell of head) expect(within(cell).queryByRole("button")).not.toBeInTheDocument();
    expect(head.some((cell) => cell.hasAttribute("aria-sort"))).toBe(false);
  });
});

describe("KpFileTable: шапка листа", () => {
  it("подписи листа из шапки ответа", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    renderTable(preview);
    const sheet = screen.getByTestId("kp-file-sheet");
    for (const text of [
      preview.header.tender,
      preview.header.object,
      preview.header.round,
      preview.header.contractor,
      preview.header.vat_label,
      preview.header.source_label,
      preview.header.level_label,
    ]) {
      expect(sheet).toHaveTextContent(text);
    }
  });

  it("блок лет ИПЦ: год, коэффициент, источник; прогноз помечен", () => {
    const preview = kpFilePreviewFixture({ withInflation: true });
    preview.header.inflation_years = [
      { year: 2025, coefficient: "1.0873", source: "Источник А", is_forecast: false, updated_at: null },
      { year: 2026, coefficient: "1.0500", source: "Источник Б", is_forecast: true, updated_at: null },
    ];
    renderTable(preview);
    const years = screen.getByTestId("kp-file-years");
    const rows = within(years).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("2025");
    expect(rows[0]).toHaveTextContent("1.0873");
    expect(rows[0]).toHaveTextContent("Источник А");
    expect(rows[0]).not.toHaveTextContent("прогноз");
    expect(rows[1]).toHaveTextContent("прогноз");
  });

  it("без приведения блока лет нет", () => {
    renderTable(kpFilePreviewFixture({ withInflation: false }));
    expect(screen.queryByTestId("kp-file-years")).not.toBeInTheDocument();
  });
});

describe("KpFileTable: комментарий в ячейке", () => {
  it("member: ни поля, ни кнопки правки", () => {
    renderTable(kpFilePreviewFixture(), false);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-comment`)).toHaveTextContent("Свой комментарий");
  });

  it("admin: у каждой строки кнопка правки, поле открывается по нажатию и несёт текст ячейки", async () => {
    const user = userEvent.setup();
    renderTable(kpFilePreviewFixture(), true);
    expect(screen.getAllByRole("button", { name: /править комментарий/i })).toHaveLength(2);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    expect(screen.getByRole("textbox")).toHaveValue("Свой комментарий");
  });

  it("сохранение: PUT с текстом, а пустой текст законен", async () => {
    const user = userEvent.setup();
    renderTable(kpFilePreviewFixture(), true);
    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    const field = screen.getByRole("textbox");
    await user.clear(field);
    await user.type(field, "Новый текст");
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("PUT");
    expect(decisions()[0].url).toContain(`/lines/${OUR_PRICE_LINE_ID}/comment`);
    expect(decisions()[0].body).toEqual({ text: "Новый текст" });

    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    await user.clear(screen.getByRole("textbox"));
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));
    await waitFor(() => expect(decisions()).toHaveLength(2));
    expect(decisions()[1].body).toEqual({ text: "" });
  });

  it("без изменений «Сохранить» неактивна; «Отмена» закрывает поле без запроса", async () => {
    const user = userEvent.setup();
    renderTable(kpFilePreviewFixture(), true);
    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    expect(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`)).toBeDisabled();
    await user.type(screen.getByRole("textbox"), "x");
    expect(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`)).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(decisions()).toHaveLength(0);
  });

  it("comment_too_long: текст отказа стоит у ячейки, поле остаётся открытым, тоста нет", async () => {
    handlerState.kpFileDecisionOutcome = "comment_too_long";
    const user = userEvent.setup();
    renderTable(kpFilePreviewFixture(), true);
    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    await user.type(screen.getByRole("textbox"), "x");
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Комментарий длиннее 2000 символов.");
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-comment`)).toContainElement(alert);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    // Тост отказа уровня поля не показывается: сообщение одно, у ячейки.
    expect(screen.getAllByText("Комментарий длиннее 2000 символов.")).toHaveLength(1);
  });

  it("другой отказ сервера уходит тостом, поле остаётся без текста отказа у ячейки", async () => {
    handlerState.kpFileDecisionOutcome = "refused";
    const user = userEvent.setup();
    renderTable(kpFilePreviewFixture(), true);
    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    await user.type(screen.getByRole("textbox"), "x");
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));
    await screen.findByText("Такой ступени у строки нет.");
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-comment`).querySelector('[role="alert"]')).toBeNull();
  });
});

describe("KpFileTable: объём", () => {
  it("объём печатается до трёх знаков, как в книге, и остаётся числовым столбцом справа", () => {
    const preview = kpFilePreviewFixture();
    const lines = preview.rows.filter((r) => r.kind === "line");
    if (lines[0].kind === "line") lines[0].quantity = "0.001";
    if (lines[1].kind === "line") lines[1].quantity = "1234.5";
    renderTable(preview);
    const text = (id: number) => screen.getByTestId(`cell-${id}-quantity`).textContent?.replace(/\s/g, " ");
    expect(text(OUR_PRICE_LINE_ID)).toBe("0,001");
    expect(text(OUR_PRICE_LINE_ID + 1)).toBe("1 234,5");
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-quantity`)).toHaveClass("text-right");
  });
});

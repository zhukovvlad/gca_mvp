import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import {
  agreedFixture,
  KP_FILE_AGREED_NET,
  KP_FILE_TEMPLATE_TEXT,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
} from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceLine, OurPriceLineView } from "@/types/domain";

import { ExportBlock } from "./ExportBlock";
import {
  AGREED_HINT,
  AGREED_PLACEHOLDER,
  COMMENT_NO_TEMPLATE_HINT,
  COMMENT_PLACEHOLDER,
  COMMENT_TAG,
  COMMENT_TAG_EMPTY,
  COMMENT_TO_TEMPLATE,
  EXPORT_OURS_NOTE,
} from "./ourPriceCopy";

type Comment = OurPriceLineView["comment"];

const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;
const DECISIONS = `/our-price/offers/${OUR_PRICE_OFFER_ID}/lines/${OUR_PRICE_LINE_ID}`;

const templateComment: Comment = { kind: "template", text: KP_FILE_TEMPLATE_TEXT, template_text: KP_FILE_TEMPLATE_TEXT };

function renderBlock(
  opts: {
    line?: Partial<OurPriceLine>;
    comment?: Comment;
    canEdit?: boolean;
    vatView?: "net" | "shown";
    vatRate?: string | null;
  } = {}
) {
  const rate = opts.vatRate === undefined ? "22.00" : opts.vatRate;
  return renderWithProviders(
    <ExportBlock
      tenderId={OUR_PRICE_TENDER_ID}
      roundId={OUR_PRICE_ROUND_ID}
      offerId={OUR_PRICE_OFFER_ID}
      line={ourPriceLineFixture(opts.line)}
      comment={opts.comment ?? templateComment}
      canEdit={opts.canEdit ?? true}
      vatView={opts.vatView ?? "net"}
      vatRate={rate}
    />,
    opts.canEdit === false ? { initialUser: MEMBER } : {}
  );
}

const decisions = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision");
const agreedField = () => screen.getByLabelText(/Договорились/);
const commentField = () => screen.getByLabelText("Комментарий для подрядчика");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("наша расценка справкой и подписи поля", () => {
  it("наша расценка с подписью, что она считается из данных; заглушка и подсказка договорённости", () => {
    renderBlock();
    expect(screen.getByTestId("export-ours")).toHaveTextContent("1 000,00");
    expect(screen.getByTestId("export-ours")).toHaveTextContent(EXPORT_OURS_NOTE);
    expect(agreedField()).toHaveValue("");
    expect(agreedField()).toHaveAttribute("placeholder", AGREED_PLACEHOLDER);
    expect(screen.getByText(AGREED_HINT)).toBeInTheDocument();
  });

  it("подпись поля — единица строки и вид цены: без НДС и с НДС по ставке КП", () => {
    const net = renderBlock();
    expect(screen.getByLabelText("Договорились, м2, без НДС")).toBeInTheDocument();
    net.unmount();
    renderBlock({ vatView: "shown" });
    expect(screen.getByLabelText("Договорились, м2, с НДС 22 %")).toBeInTheDocument();
  });

  it("база КП неизвестна: переключатель «с НДС» не действует, поле — только нетто", async () => {
    renderBlock({ vatView: "shown", vatRate: null });
    expect(screen.getByLabelText("Договорились, м2, без НДС")).toBeInTheDocument();
    await userEvent.type(agreedField(), "10");
    await userEvent.click(screen.getByTestId("agreed-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].body).toEqual({ value: "10", basis: "net" });
  });
});

describe("«Договорились»: когда и что уходит", () => {
  it("значение уходит строкой, как ввёл человек, с basis net; без нажатия запроса нет", async () => {
    renderBlock();
    await userEvent.type(agreedField(), "1234.56789012345678901234");
    expect(decisions()).toHaveLength(0);
    await userEvent.click(screen.getByTestId("agreed-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("PUT");
    expect(decisions()[0].url).toContain(`${DECISIONS}/agreed`);
    expect(decisions()[0].body).toEqual({ value: "1234.56789012345678901234", basis: "net" });
  });

  it("запятая — точкой, пробелы убраны; в виде «с НДС» basis shown", async () => {
    renderBlock({ vatView: "shown" });
    await userEvent.type(agreedField(), "1 234,5");
    await userEvent.click(screen.getByTestId("agreed-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].body).toEqual({ value: "1234.5", basis: "shown" });
  });

  it("Enter в поле сохраняет так же, как кнопка", async () => {
    renderBlock();
    await userEvent.type(agreedField(), "77{Enter}");
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].body).toEqual({ value: "77", basis: "net" });
  });

  it("без правки «Сохранить» неактивна: округлённое на экране значение не затирает точное", async () => {
    renderBlock({ line: { agreed: agreedFixture() } });
    expect(screen.getByTestId("agreed-save")).toBeDisabled();
    await userEvent.type(agreedField(), "{Enter}");
    expect(decisions()).toHaveLength(0);
  });

  it("очистка поля и «Сохранить» — DELETE", async () => {
    renderBlock({ line: { agreed: agreedFixture() } });
    await userEvent.clear(agreedField());
    await userEvent.click(screen.getByTestId("agreed-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("DELETE");
    expect(decisions()[0].url).toContain(`${DECISIONS}/agreed`);
  });

  it("поле показывает сохранённое число двумя знаками: нетто и в ставке КП", () => {
    const net = renderBlock({ line: { agreed: agreedFixture() } });
    expect(agreedField()).toHaveValue("1234.57");
    net.unmount();
    renderBlock({ line: { agreed: agreedFixture() }, vatView: "shown" });
    expect(agreedField()).toHaveValue("1506.17");
  });

  it("вид чисел: 30 знаков в ответе — на экране не больше двух знаков после запятой", () => {
    const wire = "1234.56789012345678901234567890";
    const admin = renderBlock({ line: { agreed: agreedFixture({ net: wire }) } });
    expect(agreedField()).toHaveValue("1234.57");
    expect(document.body.textContent).not.toMatch(/\d[.,]\d{3,}/);
    admin.unmount();
    renderBlock({ canEdit: false, line: { agreed: agreedFixture({ net: wire }) } });
    expect(screen.getByTestId("agreed-value")).toHaveTextContent("1 234,57");
    expect(document.body.textContent).not.toMatch(/\d[.,]\d{3,}/);
  });

  it("отказ invalid_agreed: текст сервера у поля, тоста нет; правка текст снимает", async () => {
    handlerState.kpFileDecisionOutcome = "invalid_agreed";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    renderBlock();
    await userEvent.type(agreedField(), "abc");
    await userEvent.click(screen.getByTestId("agreed-save"));
    expect(await screen.findByTestId("agreed-error")).toHaveTextContent("Цена должна быть числом больше нуля.");
    expect(error).not.toHaveBeenCalled();
    await userEvent.type(agreedField(), "1");
    expect(screen.queryByTestId("agreed-error")).not.toBeInTheDocument();
  });

  it("иной отказ — тост, у поля текста нет", async () => {
    handlerState.kpFileDecisionOutcome = "refused";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    renderBlock();
    await userEvent.type(agreedField(), "10");
    await userEvent.click(screen.getByTestId("agreed-save"));
    await waitFor(() => expect(error).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("agreed-error")).not.toBeInTheDocument();
  });
});

describe("комментарий", () => {
  it("метка: шаблон, свой текст, свой текст (пусто)", () => {
    const a = renderBlock();
    expect(screen.getByTestId("comment-tag")).toHaveTextContent(COMMENT_TAG.template);
    a.unmount();
    const b = renderBlock({ comment: { kind: "custom", text: "Свой", template_text: null } });
    expect(screen.getByTestId("comment-tag")).toHaveTextContent(COMMENT_TAG.custom);
    expect(screen.getByTestId("comment-tag")).not.toHaveTextContent(COMMENT_TAG_EMPTY);
    b.unmount();
    renderBlock({ comment: { kind: "custom", text: "", template_text: null } });
    expect(screen.getByTestId("comment-tag")).toHaveTextContent(COMMENT_TAG_EMPTY);
  });

  it("«Сохранить» шлёт PUT с текстом; запроса на каждое нажатие клавиши нет", async () => {
    renderBlock({ comment: { kind: null, text: null, template_text: null } });
    await userEvent.type(commentField(), "Новый текст");
    expect(decisions()).toHaveLength(0);
    await userEvent.click(screen.getByTestId("comment-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("PUT");
    expect(decisions()[0].url).toContain(`${DECISIONS}/comment`);
    expect(decisions()[0].body).toEqual({ text: "Новый текст" });
  });

  it("очистка поля и «Сохранить» — PUT с пустым текстом, а не DELETE", async () => {
    renderBlock();
    await userEvent.clear(commentField());
    await userEvent.click(screen.getByTestId("comment-save"));
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("PUT");
    expect(decisions()[0].body).toEqual({ text: "" });
  });

  it("без правки «Сохранить» неактивна", () => {
    renderBlock();
    expect(screen.getByTestId("comment-save")).toBeDisabled();
  });

  it("«к шаблону» у своего текста при существующем шаблоне: подсказка — текст шаблона, DELETE", async () => {
    renderBlock({ comment: { kind: "custom", text: "Свой", template_text: KP_FILE_TEMPLATE_TEXT } });
    const button = screen.getByRole("button", { name: COMMENT_TO_TEMPLATE });
    expect(button).toHaveAttribute("title", KP_FILE_TEMPLATE_TEXT);
    await userEvent.click(button);
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("DELETE");
    expect(decisions()[0].url).toContain(`${DECISIONS}/comment`);
  });

  it("«к шаблону» есть и при template_text = null: подсказка «комментария не будет», DELETE", async () => {
    renderBlock({ comment: { kind: "custom", text: "Свой", template_text: null } });
    const button = screen.getByRole("button", { name: COMMENT_TO_TEMPLATE });
    expect(button).toHaveAttribute("title", COMMENT_NO_TEMPLATE_HINT);
    await userEvent.click(button);
    await waitFor(() => expect(decisions()).toHaveLength(1));
    expect(decisions()[0].method).toBe("DELETE");
  });

  it("«к шаблону» нет у шаблонного текста и у строки без комментария", () => {
    const a = renderBlock();
    expect(screen.queryByRole("button", { name: COMMENT_TO_TEMPLATE })).not.toBeInTheDocument();
    a.unmount();
    renderBlock({ comment: { kind: null, text: null, template_text: null } });
    expect(screen.queryByRole("button", { name: COMMENT_TO_TEMPLATE })).not.toBeInTheDocument();
  });

  it("отказ comment_too_long: текст сервера у поля, тоста нет", async () => {
    handlerState.kpFileDecisionOutcome = "comment_too_long";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    renderBlock();
    await userEvent.type(commentField(), "!");
    await userEvent.click(screen.getByTestId("comment-save"));
    expect(await screen.findByTestId("comment-error")).toHaveTextContent("Комментарий длиннее 2000 символов.");
    expect(error).not.toHaveBeenCalled();
  });

  it("иной отказ — тост, у поля текста нет", async () => {
    handlerState.kpFileDecisionOutcome = "refused";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    renderBlock();
    await userEvent.type(commentField(), "!");
    await userEvent.click(screen.getByTestId("comment-save"));
    await waitFor(() => expect(error).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("comment-error")).not.toBeInTheDocument();
  });
});

describe("права", () => {
  it("member: значения есть, ни одного textbox и ни одной кнопки", () => {
    renderBlock({
      canEdit: false,
      line: { agreed: agreedFixture({ net: KP_FILE_AGREED_NET }) },
      comment: { kind: "custom", text: "Свой текст строки", template_text: KP_FILE_TEMPLATE_TEXT },
    });
    const block = screen.getByTestId("export-block");
    expect(within(block).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(block).queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByTestId("agreed-value")).toHaveTextContent("1 234,57");
    expect(screen.getByTestId("comment-text")).toHaveTextContent("Свой текст строки");
    expect(screen.getByTestId("comment-tag")).toHaveTextContent(COMMENT_TAG.custom);
  });

  it("member без решений: «не договаривались» и «комментария нет» вместо полей", () => {
    renderBlock({ canEdit: false, comment: { kind: null, text: null, template_text: null } });
    expect(screen.getByTestId("agreed-value")).toHaveTextContent(AGREED_PLACEHOLDER);
    expect(screen.getByTestId("comment-text")).toHaveTextContent(COMMENT_PLACEHOLDER);
  });
});

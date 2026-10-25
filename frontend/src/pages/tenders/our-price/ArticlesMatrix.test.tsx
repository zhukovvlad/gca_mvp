import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ArticlesMatrix } from "./ArticlesMatrix";
import { ourPriceRoundFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { MoneyPair, OurPriceCell, OurPriceRound, OurPriceRoundArticle } from "@/types/domain";

import { EMPTY_REASON_LABEL, VAT_MIXED_RATES_LABEL } from "./ourPriceCopy";
import type { VatView } from "./money";

const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });
const FORK = { min: pair("100", "122"), max: pair("200", "244") };

function cell(perSqm: MoneyPair | null, over: Partial<OurPriceCell> = {}): OurPriceCell {
  return {
    per_sqm: perSqm,
    whole: perSqm === null ? null : pair("5000.00", "6100.00"),
    unit_mismatch_lines: 0,
    ours_class_per_sqm: FORK,
    ...over,
  };
}

function article(
  id: number | null,
  sortOrder: number | null,
  perSqm: Array<MoneyPair | null>,
  over: Partial<OurPriceRoundArticle> = {}
): OurPriceRoundArticle {
  const offers = [7001, 7002, 7003];
  return {
    work_category_id: id,
    code: id === null ? null : String(id).padStart(2, "0"),
    title: id === null ? "Итого на м² объекта" : `Статья ${id}`,
    sort_order: sortOrder,
    ours_class_per_sqm: FORK,
    cells: offers.map((offer_id, i) => ({ offer_id, ...cell(perSqm[i] ?? null) })),
    ...over,
  };
}

/** Два участника с КП (7001, 7002) и один с неизвестной базой (7003). */
function roundWith(articles: OurPriceRoundArticle[], overrides: Partial<OurPriceRound> = {}): OurPriceRound {
  const base = ourPriceRoundFixture();
  const [a, b, c] = base.participants;
  return {
    ...base,
    participants: [
      a,
      { ...b, state: "ok", estimate_id: 9002, per_sqm: pair("150", "183"), per_sqm_reason: null, vat_rate: "22" },
      c,
    ],
    articles,
    total: article(null, null, [pair("150", "183"), pair("160", "195.2")]),
    ...overrides,
  };
}

function renderMatrix(round: OurPriceRound, view: VatView = "net") {
  return renderWithProviders(
    <ArticlesMatrix
      round={round}
      view={view}
      offerHref={(offerId, articleId) =>
        `/o/${offerId}${articleId === null ? "" : `?article=${articleId}`}`
      }
    />
  );
}

const rowIds = () => screen.getAllByTestId(/^row-/).map((el) => el.getAttribute("data-testid"));
const headerButton = (name: RegExp) => screen.getByRole("button", { name });

describe("ArticlesMatrix", () => {
  it("первая строка «Итого», дальше статьи по sort_order, а не по порядку ответа", () => {
    renderMatrix(
      roundWith([
        article(12, 2, [pair("150", "183")]),
        article(11, 1, [pair("150", "183")]),
        article(13, 3, [pair("150", "183")]),
      ])
    );
    expect(rowIds()).toEqual(["row-total", "row-11", "row-12", "row-13"]);
  });

  it("колонки: статья, вилка класса, по колонке на участника", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "183")])]));
    const head = screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(head).toHaveLength(2 + 3);
    expect(head[2]).toContain("Подрядчик А");
    expect(head[3]).toContain("Подрядчик Б");
    expect(head[4]).toContain("Подрядчик В");
  });

  it("net показывает нетто, shown — поле в ставке участника", () => {
    const round = roundWith([article(11, 1, [pair("110000", "134200")])]);
    const { unmount } = renderMatrix(round, "net");
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00");
    expect(screen.getByTestId("cell-11-7001")).not.toHaveTextContent("134 200,00");
    unmount();
    renderMatrix(round, "shown");
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("134 200,00");
    expect(screen.getByTestId("cell-11-7001")).not.toHaveTextContent("110 000,00");
  });

  it("под ценой — сумма «статья целиком» в рублях выбранного вида", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "183")])]), "shown");
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("6 100,00");
  });

  it("цвет: выше max вилки — dear, ниже min — cheap, внутри и ровно на границах — без цвета", () => {
    renderMatrix(
      roundWith([
        article(11, 1, [pair("200.01", "1"), pair("99.99", "1")]),
        article(12, 2, [pair("200", "1"), pair("100", "1")]),
        article(13, 3, [pair("150", "1"), pair("1000", "1")]),
      ])
    );
    expect(screen.getByTestId("cell-11-7001")).toHaveAttribute("data-tone", "dear");
    expect(screen.getByTestId("cell-11-7002")).toHaveAttribute("data-tone", "cheap");
    expect(screen.getByTestId("cell-12-7001")).not.toHaveAttribute("data-tone");
    expect(screen.getByTestId("cell-12-7002")).not.toHaveAttribute("data-tone");
    expect(screen.getByTestId("cell-13-7001")).not.toHaveAttribute("data-tone");
    expect(screen.getByTestId("cell-13-7002")).toHaveAttribute("data-tone", "dear");
  });

  it("цвет сравнивает нетто даже в режиме shown", () => {
    // shown огромно, а нетто внутри вилки: цвета быть не должно.
    renderMatrix(roundWith([article(11, 1, [pair("150", "99999")])]), "shown");
    expect(screen.getByTestId("cell-11-7001")).not.toHaveAttribute("data-tone");
  });

  it("без вилки класса цвета нет", () => {
    renderMatrix(roundWith([article(11, 1, [pair("999", "1")], { ours_class_per_sqm: null })]));
    expect(screen.getByTestId("cell-11-7001")).not.toHaveAttribute("data-tone");
  });

  it("самая дешёвая по статье подчёркнута, остальные и пустые — нет", () => {
    renderMatrix(roundWith([article(11, 1, [pair("9", "1"), pair("10", "1")])]));
    expect(screen.getByTestId("cell-11-7001")).toHaveAttribute("data-cheapest", "true");
    expect(screen.getByTestId("cell-11-7002")).not.toHaveAttribute("data-cheapest");
    expect(screen.getByTestId("cell-11-7003")).not.toHaveAttribute("data-cheapest");
  });

  it("ничья по минимуму: подчёркнуты обе", () => {
    renderMatrix(roundWith([article(11, 1, [pair("10.0", "1"), pair("10", "1")])]));
    expect(screen.getByTestId("cell-11-7001")).toHaveAttribute("data-cheapest", "true");
    expect(screen.getByTestId("cell-11-7002")).toHaveAttribute("data-cheapest", "true");
  });

  it("сортировка по колонке участника — числовая, не по строке; повторный клик меняет направление; пустые в конце", () => {
    renderMatrix(
      roundWith([
        article(11, 1, [pair("9", "1")]),
        article(12, 2, [pair("10", "1")]),
        article(13, 3, [null]),
        article(14, 4, [pair("100", "1")]),
      ])
    );
    fireEvent.click(headerButton(/Подрядчик А/));
    expect(rowIds()).toEqual(["row-total", "row-11", "row-12", "row-14", "row-13"]);
    fireEvent.click(headerButton(/Подрядчик А/));
    expect(rowIds()).toEqual(["row-total", "row-14", "row-12", "row-11", "row-13"]);
  });

  it("сортировка по вилке класса и возврат к порядку статей", () => {
    renderMatrix(
      roundWith([
        article(11, 1, [pair("150", "1")], { ours_class_per_sqm: { min: pair("90", "1"), max: pair("200", "1") } }),
        article(12, 2, [pair("150", "1")], { ours_class_per_sqm: { min: pair("80", "1"), max: pair("200", "1") } }),
      ])
    );
    fireEvent.click(headerButton(/Наши договоры/));
    expect(rowIds()).toEqual(["row-total", "row-12", "row-11"]);
    fireEvent.click(headerButton(/Статья/));
    expect(rowIds()).toEqual(["row-total", "row-11", "row-12"]);
  });

  it("сортировка по колонке «Статья» тоже меняет направление: повторный клик — обратный порядок номеров", () => {
    renderMatrix(
      roundWith([
        article(11, 1, [pair("150", "1")], { ours_class_per_sqm: { min: pair("80", "1"), max: pair("200", "1") } }),
        article(12, 2, [pair("150", "1")], { ours_class_per_sqm: { min: pair("90", "1"), max: pair("200", "1") } }),
      ])
    );
    // Сначала уводим сортировку на другую колонку, чтобы клики по «Статье» шли из явного состояния.
    fireEvent.click(headerButton(/Наши договоры/));
    fireEvent.click(headerButton(/Статья/));
    expect(rowIds()).toEqual(["row-total", "row-11", "row-12"]);
    fireEvent.click(headerButton(/Статья/));
    expect(rowIds()).toEqual(["row-total", "row-12", "row-11"]);
  });

  it("клик по ячейке с ценой ведёт в разбор с ?article=, итог — без статьи", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "1")])]));
    expect(within(screen.getByTestId("cell-11-7001")).getByRole("link")).toHaveAttribute(
      "href",
      "/o/7001?article=11"
    );
    expect(within(screen.getByTestId("cell-total-7001")).getByRole("link")).toHaveAttribute(
      "href",
      "/o/7001"
    );
  });

  it("ячейка участника с неизвестной базой пуста, причина — полем и подписью, ссылки нет", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "1")])]));
    const empty = screen.getByTestId("cell-11-7003");
    expect(empty).toHaveAttribute("data-reason", "unknown_vat_base");
    expect(empty).toHaveAttribute("title", EMPTY_REASON_LABEL.unknown_vat_base);
    expect(within(empty).queryByRole("link")).not.toBeInTheDocument();
  });

  it("участник без сметы: причина no_estimate", () => {
    const base = ourPriceRoundFixture();
    renderMatrix({ ...base, articles: [article(11, 1, [pair("150", "1")])], total: article(null, null, []) });
    const empty = screen.getByTestId("cell-11-7002");
    expect(empty).toHaveAttribute("data-reason", "no_estimate");
    expect(empty).toHaveAttribute("title", EMPTY_REASON_LABEL.no_estimate);
  });

  it("⚠ с числом строк разных единиц; при нуле значка нет", () => {
    const withMismatch = article(11, 1, [pair("150", "1"), pair("160", "1")]);
    withMismatch.cells[0] = { ...withMismatch.cells[0], unit_mismatch_lines: 3 };
    renderMatrix(roundWith([withMismatch]));
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("⚠ 3");
    expect(screen.getByTestId("cell-11-7002")).not.toHaveTextContent("⚠");
  });

  it("колонка вилки: в режиме shown при одной ставке — shown и подпись ставки", () => {
    const round = roundWith([article(11, 1, [pair("150", "1")])], {
      history: { ...ourPriceRoundFixture().history, shown_rate: "22" },
    });
    renderMatrix(round, "shown");
    expect(screen.getByTestId("fork-11")).toHaveTextContent("122,00");
    expect(screen.getByTestId("fork-11")).not.toHaveTextContent("100,00");
    expect(screen.getByTestId("fork-head")).toHaveTextContent("с НДС 22 %");
  });

  it("колонка вилки: shown_rate = null — остаётся нетто с подписью «ставки участников разные»", () => {
    const round = roundWith([article(11, 1, [pair("150", "1")])]);
    expect(round.history.shown_rate).toBeNull();
    renderMatrix(round, "shown");
    expect(screen.getByTestId("fork-11")).toHaveTextContent("100,00");
    expect(screen.getByTestId("fork-11")).not.toHaveTextContent("122,00");
    expect(screen.getByTestId("fork-head")).toHaveTextContent(VAT_MIXED_RATES_LABEL);
  });

  it("шапка участника в shown несёт ставку именно его: с НДС 22 % и «база неизвестна»", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "1")])]), "shown");
    expect(screen.getByTestId("col-7001")).toHaveTextContent("с НДС 22 %");
    expect(screen.getByTestId("col-7003")).toHaveTextContent("база НДС неизвестна");
  });
});

describe("ArticlesMatrix: правки по ревью", () => {
  it("первый клик по «Статье» сортирует: порядок меняется на обратный, второй возвращает", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150", "1")]), article(12, 2, [pair("150", "1")])]));
    fireEvent.click(headerButton(/Статья/));
    expect(rowIds()).toEqual(["row-total", "row-12", "row-11"]);
    fireEvent.click(headerButton(/Статья/));
    expect(rowIds()).toEqual(["row-total", "row-11", "row-12"]);
  });

  it("«Нераспределённое» ведёт в разбор с ?article=unallocated, «Итого» — без статьи", () => {
    renderMatrix(
      roundWith([article(null, 5, [pair("150", "1")], { title: "Нераспределённое" })])
    );
    expect(within(screen.getByTestId("cell-null-7001")).getByRole("link")).toHaveAttribute(
      "href",
      "/o/7001?article=unallocated"
    );
    expect(within(screen.getByTestId("cell-total-7001")).getByRole("link")).toHaveAttribute(
      "href",
      "/o/7001"
    );
  });

  it("вторая строка ячейки: знак виден, ноль без знака, пояснение под таблицей", () => {
    const art = article(11, 1, [pair("150", "1"), pair("160", "1"), pair("170", "1")]);
    art.cells[0] = { ...art.cells[0], whole: pair("5000.00", "6100.00") };
    art.cells[1] = { ...art.cells[1], whole: pair("-5000.00", "-6100.00") };
    art.cells[2] = { ...art.cells[2], whole: pair("0.00", "0.00") };
    renderMatrix(roundWith([art]));
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("+5 000,00");
    expect(screen.getByTestId("cell-11-7002")).toHaveTextContent("−5 000,00");
    expect(screen.getByTestId("cell-11-7003")).not.toHaveTextContent("+0,00");
    expect(screen.getByTestId("cell-11-7003")).not.toHaveTextContent("−0,00");
    expect(screen.getByTestId("matrix-hint")).toHaveTextContent("разница с нижней границей наших договоров класса");
  });
});

describe("вёрстка: умещается у четырёх участников, дальше — прокрутка с закреплённой первой колонкой", () => {
  it("первая колонка закреплена в шапке и в строках; колонки участников не уже минимума", () => {
    renderMatrix(roundWith([article(1, 1, [pair("100", "122")])]));
    expect(screen.getByRole("columnheader", { name: /Статья/ })).toHaveClass("sticky", "left-0");
    expect(screen.getByTestId("row-1").querySelector("td")).toHaveClass("sticky", "left-0");
  });

  it("название статьи переносится в своей колонке, а не расширяет таблицу", () => {
    renderMatrix(roundWith([article(1, 1, [pair("100", "122")])]));
    expect(screen.getByTestId("row-1").querySelector("td")).toHaveClass("whitespace-normal");
  });
});

describe("сравнения — по полным строкам сервера, а не по напечатанным", () => {
  it("«дешевле всех» в статье: на экране оба 150,00, отмечен тот, чья полная строка меньше", () => {
    renderMatrix(roundWith([article(11, 1, [pair("150.004", "183"), pair("150.001", "183")])]));
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("150,00");
    expect(screen.getByTestId("cell-11-7002")).toHaveTextContent("150,00");
    expect(screen.getByTestId("cell-11-7002")).toHaveAttribute("data-cheapest", "true");
    expect(screen.getByTestId("cell-11-7001")).not.toHaveAttribute("data-cheapest");
  });

  it("окраска: выше максимума вилки на тысячную — «дороже», хотя напечатано 200,00 при максимуме 200,00", () => {
    renderMatrix(roundWith([article(11, 1, [pair("200.004", "244")])]));
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("200,00");
    expect(screen.getByTestId("cell-11-7001")).toHaveAttribute("data-tone", "dear");
  });
});

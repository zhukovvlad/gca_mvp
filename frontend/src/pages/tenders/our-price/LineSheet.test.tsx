import { useState } from "react";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
  ourPriceLineViewFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import { OUR_PRICE_POLL_MS } from "@/services/queries";
import { qk } from "@/services/queryKeys";
import type { MoneyPair, OurPriceLine, OurPriceLineView, OurPriceRungContract } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";
import OurPriceOfferPage from "./OurPriceOfferPage";
import {
  ALL_CLASSES_LABEL,
  CONTRACT_LOWER_BOUND,
  CONTRACT_WITH_CONTRACTOR,
  GROUP_OUTSIDE,
  GROUP_THIS_LINE,
  LINE_STATE_LABEL,
  RUNG_AUTO_TAG,
  SHEET_ERROR_LABEL,
  UNIT_WARN_TITLE,
  VAT_DROPPED_NOTE,
  WAITING_ADMIN_LABEL,
  VAT_NET_LABEL,
  VAT_UNKNOWN_LABEL,
} from "./ourPriceCopy";

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const STAGE = `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price`;
const ROUTE = `${STAGE}/offers/${OUR_PRICE_OFFER_ID}`;

const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });

interface Asked {
  pid: string;
  level: string | null;
  scope: string | null;
}

/** Ответы панели по запросу; запросы записываются, ответ на выбранную ступень можно придержать. */
function serveView(make: (asked: Asked) => OurPriceLineView, opts: { hold?: Promise<void> } = {}) {
  const asked: Asked[] = [];
  server.use(
    http.get(LINE_PATH, async ({ request, params }) => {
      const search = new URL(request.url).searchParams;
      const item: Asked = { pid: String(params.positionItemId), level: search.get("level"), scope: search.get("scope") };
      asked.push(item);
      if (item.level !== null && opts.hold) await opts.hold;
      return HttpResponse.json(make(item));
    })
  );
  return asked;
}

function view(line: Partial<OurPriceLine> = {}, rest: Partial<OurPriceLineView> = {}): OurPriceLineView {
  return { ...ourPriceLineViewFixture(), line: ourPriceLineFixture(line), ...rest };
}

/** Строка на выбранной ступени ответа: автоматическая — когда ступень не просили. */
function onRung(asked: Asked, line: Partial<OurPriceLine> = {}): Partial<OurPriceLine> {
  const base = ourPriceLineFixture();
  return {
    ...line,
    rung: {
      level: (asked.level as "v" | "p" | "f" | null) ?? base.rung!.level,
      scope: (asked.scope as "cls" | "all" | null) ?? base.rung!.scope,
      auto: asked.level === null,
      threshold_met: true,
    },
  };
}

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

function renderSheet(props: Partial<LineSheetProps> = {}) {
  return renderWithProviders(<LineSheet {...baseProps} {...props} />);
}

async function opened() {
  return screen.findByTestId("line-sheet");
}

const rung = (level: string, scope: string) => screen.getByTestId(`rung-${level}-${scope}`);

function contract(id: number, over: Partial<OurPriceRungContract> = {}): OurPriceRungContract {
  return {
    contract_id: id,
    object_title: `Объект ${id}`,
    number: `Договор ${id}`,
    year: 2020 + (id % 10),
    rate_class: { id: 2, title: "Класс 2" },
    contractor_id: 400 + id,
    contractor_title: `Подрядчик ${400 + id}`,
    rate: pair("900", "1098"),
    is_lower_bound: false,
    with_this_contractor: false,
    ratio: null,
    ...over,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("цены панели", () => {
  it("без НДС: подпись, цена КП, объём, разница со знаком, наша расценка справкой", async () => {
    serveView(() => view());
    renderSheet();
    await opened();
    const prices = await screen.findByTestId("sheet-prices");
    expect(prices).toHaveTextContent("без НДС");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 200,00");
    expect(screen.getByTestId("sheet-quantity")).toHaveTextContent("100");
    expect(screen.getByTestId("sheet-to-ours")).toHaveTextContent("+20 000,00");
    expect(screen.getByTestId("sheet-ours")).toHaveTextContent("1 000,00");
    expect(screen.getByTestId("sheet-ours")).not.toHaveTextContent("1 220,00");
    expect(screen.getByTestId("sheet-ours")).not.toHaveTextContent(ALL_CLASSES_LABEL);
    expect(prices).toHaveTextContent("Цвет: серый");
    // Заголовок панели — строка ответа, подзаголовок — её семья и вариант.
    const sheet = screen.getByTestId("line-sheet");
    expect(within(sheet).getByRole("heading", { name: "Работа 1" })).toBeInTheDocument();
    expect(sheet).toHaveTextContent("Семья 1 · Вариант 1");
    // Подпись варианта уже начинается с семьи: во второй раз она не печатается.
    expect(sheet).not.toHaveTextContent("Семья 1 · Семья 1");
  });

  it("цена КП и наша расценка подписаны рублями за единицу измерения строки", async () => {
    serveView(() => view({ unit: "м3" }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 200,00 ₽/м3");
    expect(screen.getByTestId("sheet-ours")).toHaveTextContent("1 000,00 ₽/м3");
    expect(screen.getByTestId("sheet-quantity")).toHaveTextContent("100,00 м3");
  });

  it("строка без единицы: цена подписана просто рублями", async () => {
    serveView(() => view({ unit: null }));
    renderSheet();
    expect(await screen.findByTestId("sheet-kp-price")).toHaveTextContent(/^1 200,00 ₽$/);
  });

  it("система: сравниваемая цена — цена группы за м² объекта; сумма самой строки остаётся справкой", async () => {
    serveView(() =>
      view({
        kind: "system",
        unit: "компл",
        kp: { unit_price: pair("39230327.87", "47860000.00"), total: pair("39230327.87", "47860000.00") },
        group: { lines: 3, share: "1", kp_per_sqm: pair("786", "959") },
      })
    );
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("786,00 ₽/м²");
    expect(screen.getByTestId("sheet-kp-price")).not.toHaveTextContent("39 230 327");
    expect(screen.getByTestId("sheet-kp-total")).toHaveTextContent("39 230 327,87");
    expect(screen.getByTestId("sheet-ours")).toHaveTextContent("₽/м²");
  });

  it("наша расценка взята по всем классам — у неё подпись «все классы»", async () => {
    serveView(() => view({ ours: { value: pair("1000", "1220"), from_all_classes: true } }));
    renderSheet();
    expect(await screen.findByTestId("sheet-ours")).toHaveTextContent(ALL_CLASSES_LABEL);
  });

  it("с НДС: подпись называет ставку и «договоры — по ставке КП», цифры в ставке КП", async () => {
    serveView(() => view());
    renderSheet({ vat: "shown", vatRate: "22.00" });
    expect(await screen.findByTestId("sheet-prices")).toHaveTextContent("с НДС 22 % (договоры — по ставке КП)");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 464,00");
    expect(screen.getByTestId("sheet-to-ours")).toHaveTextContent("+24 400,00");
    expect(screen.getByTestId("sheet-ours")).toHaveTextContent("1 220,00");
  });

  it("с НДС, но без ставки КП: подпись не «без НДС», а «база НДС неизвестна»; цифры — прочерки", async () => {
    serveView(() => view({ kp: { unit_price: pair("1200", null), total: pair("120000.00", null) }, to_ours: pair("20000.00", null), ours: { value: pair("1000", null), from_all_classes: false } }));
    renderSheet({ vat: "shown", vatRate: null });
    const prices = await screen.findByTestId("sheet-prices");
    expect(prices).toHaveTextContent(VAT_UNKNOWN_LABEL);
    expect(prices).not.toHaveTextContent(VAT_NET_LABEL);
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("—");
  });

  it("разница отрицательна — знак «−»", async () => {
    serveView(() => view({ to_ours: pair("-300.00", "-366.00") }));
    renderSheet();
    expect(await screen.findByTestId("sheet-to-ours")).toHaveTextContent("−300,00");
  });

  it("строка без разницы и расценки печатает прочерки, а не нули", async () => {
    serveView(() => view({ to_ours: null, ours: null }));
    renderSheet();
    expect(await screen.findByTestId("sheet-to-ours")).toHaveTextContent("—");
    expect(screen.getByTestId("sheet-ours")).toHaveTextContent("—");
  });

  it("блока «Для выгрузки» нет", async () => {
    serveView(() => view());
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByText(/Для выгрузки/)).not.toBeInTheDocument();
  });
});

describe("лестница", () => {
  it("ступени ответа с числом договоров; авто помечена, выбранная выделена, остальные нет", async () => {
    serveView((asked) => view(onRung(asked)));
    renderSheet();
    await screen.findByTestId("rung-v-cls");
    expect(screen.getAllByRole("radio")).toHaveLength(4);
    expect(rung("v", "cls")).toHaveTextContent("3 дог.");
    expect(rung("v", "all")).toHaveTextContent("5 дог.");
    expect(rung("f", "cls")).toHaveTextContent("4 дог.");
    expect(rung("f", "all")).toHaveTextContent("6 дог.");
    expect(rung("v", "cls")).toHaveAttribute("aria-checked", "true");
    expect(rung("v", "cls")).toHaveTextContent(RUNG_AUTO_TAG);
    // Ступень подписана уровнем и охватом: «класс объекта» / «все классы» (название класса — у договоров).
    expect(rung("v", "cls")).toHaveTextContent("вариант · класс объекта");
    expect(rung("f", "all")).toHaveTextContent("вся семья · все классы");
    expect(rung("f", "all")).not.toHaveTextContent("класс объекта");
    for (const other of [rung("v", "all"), rung("f", "cls"), rung("f", "all")]) {
      expect(other).toHaveAttribute("aria-checked", "false");
      expect(other).not.toHaveTextContent(RUNG_AUTO_TAG);
    }
  });

  it("ступень с 0 договоров не нажимается и запроса не шлёт; ступень с договорами — нажимается", async () => {
    const asked = serveView((a) =>
      view(
        onRung(a, {
          rungs: [
            { level: "v", scope: "cls", contracts: 3 },
            { level: "f", scope: "all", contracts: 0 },
          ],
        })
      )
    );
    renderSheet();
    await screen.findByTestId("rung-f-all");
    expect(rung("f", "all")).toBeDisabled();
    expect(rung("v", "cls")).toBeEnabled();
    fireEvent.click(rung("f", "all"));
    expect(asked).toHaveLength(1);
  });

  it("выбор ступени — запрос с level и scope; серый слой над прежними цифрами; потом выбранная выделена, авто осталась на своей", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const asked = serveView(
      (a) => (a.level === null ? view(onRung(a)) : view(onRung(a, { kp: { unit_price: pair("1300", "1586"), total: null } }), { contracts: [] })),
      { hold }
    );
    renderSheet();
    await screen.findByTestId("rung-v-cls");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 200,00");
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();

    fireEvent.click(rung("v", "all"));
    await waitFor(() => expect(screen.getByTestId("recalc-overlay")).toBeInTheDocument());
    expect(asked[asked.length - 1]).toMatchObject({ level: "v", scope: "all" });
    // Выбранная выделена сразу, пока ответ на неё ещё идёт: прежний ответ стоит на авто.
    expect(rung("v", "all")).toHaveAttribute("aria-checked", "true");
    expect(rung("v", "cls")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 200,00");
    expect(screen.getByTestId("contract-101")).toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 300,00");
    expect(screen.queryByTestId("contract-101")).not.toBeInTheDocument();
    expect(rung("v", "all")).toHaveAttribute("aria-checked", "true");
    expect(rung("v", "cls")).toHaveAttribute("aria-checked", "false");
    expect(rung("v", "cls")).toHaveTextContent(RUNG_AUTO_TAG);
    expect(rung("v", "all")).not.toHaveTextContent(RUNG_AUTO_TAG);
  });

  describe("клавиатура: радиогруппа", () => {
    const withRungs = (contracts: [number, number, number, number]) => (a: Asked) =>
      view(
        onRung(a, {
          rungs: [
            { level: "v", scope: "cls", contracts: contracts[0] },
            { level: "v", scope: "all", contracts: contracts[1] },
            { level: "f", scope: "cls", contracts: contracts[2] },
            { level: "f", scope: "all", contracts: contracts[3] },
          ],
        })
      );

    it("стрелка вправо выбирает следующую ступень и переводит на неё фокус", async () => {
      const asked = serveView(withRungs([3, 5, 4, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      const user = userEvent.setup();
      rung("v", "cls").focus();
      await user.keyboard("{ArrowRight}");
      await waitFor(() => expect(rung("v", "all")).toHaveAttribute("aria-checked", "true"));
      expect(asked[asked.length - 1]).toMatchObject({ level: "v", scope: "all" });
      expect(rung("v", "all")).toHaveFocus();
      expect(rung("v", "cls")).toHaveAttribute("aria-checked", "false");
    });

    it("стрелка влево с первой ступени переходит на последнюю; вниз — как вправо, вверх — как влево", async () => {
      serveView(withRungs([3, 5, 4, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      const user = userEvent.setup();
      rung("v", "cls").focus();
      await user.keyboard("{ArrowLeft}");
      await waitFor(() => expect(rung("f", "all")).toHaveAttribute("aria-checked", "true"));
      await user.keyboard("{ArrowUp}");
      await waitFor(() => expect(rung("f", "cls")).toHaveAttribute("aria-checked", "true"));
      await user.keyboard("{ArrowDown}");
      await waitFor(() => expect(rung("f", "all")).toHaveAttribute("aria-checked", "true"));
    });

    it("ступень без договоров пропускается", async () => {
      serveView(withRungs([3, 5, 0, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      const user = userEvent.setup();
      rung("v", "all").focus();
      await user.keyboard("{ArrowRight}");
      await waitFor(() => expect(rung("f", "all")).toHaveAttribute("aria-checked", "true"));
      expect(rung("f", "cls")).toHaveAttribute("aria-checked", "false");
    });

    it("другая клавиша выбор не меняет и запроса не шлёт", async () => {
      const asked = serveView(withRungs([3, 5, 4, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      const user = userEvent.setup();
      rung("v", "cls").focus();
      await user.keyboard("a");
      expect(asked).toHaveLength(1);
      expect(rung("v", "cls")).toHaveAttribute("aria-checked", "true");
    });

    it("стрелка не прокручивает панель: действие клавиши по умолчанию отменено", async () => {
      serveView(withRungs([3, 5, 4, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      expect(fireEvent.keyDown(rung("v", "cls"), { key: "ArrowDown" })).toBe(false);
      expect(fireEvent.keyDown(rung("v", "cls"), { key: "a" })).toBe(true);
    });

    it("в Tab-порядке одна ступень — выбранная", async () => {
      serveView(withRungs([3, 5, 4, 6]));
      renderSheet();
      await screen.findByTestId("rung-v-cls");
      expect(rung("v", "cls")).toHaveAttribute("tabindex", "0");
      for (const other of [rung("v", "all"), rung("f", "cls"), rung("f", "all")]) {
        expect(other).toHaveAttribute("tabindex", "-1");
      }
    });
  });

  it("«Вернуть автоматическую» есть только при выбранной ступени и снимает выбор", async () => {
    serveView((a) => view(onRung(a)));
    renderSheet();
    await screen.findByTestId("rung-v-cls");
    expect(screen.queryByTestId("rung-reset")).not.toBeInTheDocument();
    fireEvent.click(rung("f", "cls"));
    const reset = await screen.findByTestId("rung-reset");
    fireEvent.click(reset);
    await waitFor(() => expect(rung("v", "cls")).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByTestId("rung-reset")).not.toBeInTheDocument();
    expect(rung("f", "cls")).toHaveAttribute("aria-checked", "false");
  });

  it("смена строки сбрасывает выбор ступени: новая строка открывается на авто", async () => {
    const asked = serveView((a) => view(onRung(a)));
    function Harness() {
      const [pid, setPid] = useState(OUR_PRICE_LINE_ID);
      return (
        <>
          <button type="button" data-testid="next" onClick={() => setPid(5002)}>
            next
          </button>
          <LineSheet {...baseProps} positionItemId={pid} />
        </>
      );
    }
    renderWithProviders(<Harness />);
    await screen.findByTestId("rung-v-cls");
    fireEvent.click(rung("f", "all"));
    await waitFor(() => expect(rung("f", "all")).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(screen.getByTestId("next"));
    await waitFor(() => expect(asked.some((a) => a.pid === "5002")).toBe(true));
    const second = asked.filter((a) => a.pid === "5002");
    expect(second).toHaveLength(1);
    expect(second[0]).toMatchObject({ level: null, scope: null });
    await waitFor(() => expect(rung("v", "cls")).toHaveAttribute("aria-checked", "true"));
    expect(rung("f", "all")).toHaveAttribute("aria-checked", "false");
  });

  it("строка без ступени: подпись состояния и ни одной ступени", async () => {
    serveView(() =>
      view(
        { state: "no_history", position: null, to_ours: null, ours: null, range: null, rung: null, rungs: [] },
        { contracts: [] }
      )
    );
    renderSheet();
    expect(await screen.findByTestId("sheet-state")).toHaveTextContent(LINE_STATE_LABEL.no_history);
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });
});

describe("системная строка", () => {
  const family = (): OurPriceLine[] => [
    ourPriceLineFixture({
      position_item_id: 5001,
      title: "Система 1",
      kind: "system",
      group: { lines: 2, share: "0.4", kp_per_sqm: pair("500", "610") },
    }),
    ourPriceLineFixture({
      position_item_id: 5002,
      title: "Система 2",
      kind: "system",
      group: { lines: 2, share: "0.6", kp_per_sqm: pair("500", "610") },
    }),
    ourPriceLineFixture({ position_item_id: 5003, title: "Система 3", kind: "system", group: null }),
  ];

  it("подпись «N строк КП вместе» и вся семья: свои строки с группой, чужая вне группы, открытая отмечена", async () => {
    serveView(() => view({ kind: "system", group: { lines: 2, share: "0.4", kp_per_sqm: pair("500", "610") } }, { group_lines: family() }));
    renderSheet();
    expect(await screen.findByTestId("group-caption")).toHaveTextContent("2 строки КП вместе");
    expect(screen.getAllByTestId(/^group-line-/)).toHaveLength(3);
    expect(screen.getByTestId("group-line-5001")).toHaveTextContent("Система 1");
    expect(screen.getByTestId("group-line-5001")).toHaveTextContent(GROUP_THIS_LINE);
    expect(screen.getByTestId("group-line-5002")).not.toHaveTextContent(GROUP_THIS_LINE);
    expect(screen.getByTestId("group-line-5002")).toHaveTextContent("группа из 2 стр.");
    expect(screen.getByTestId("group-line-5003")).toHaveTextContent(GROUP_OUTSIDE);
    expect(screen.getByTestId("group-line-5001")).not.toHaveTextContent(GROUP_OUTSIDE);
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("500,00 ₽/м²");
  });

  it("строка другой группы семьи подписана размером СВОЕЙ группы, а не группы открытой строки", async () => {
    const other = ourPriceLineFixture({
      position_item_id: 5004,
      title: "Система 4",
      kind: "system",
      group: { lines: 3, share: "0.2", kp_per_sqm: pair("700", "854") },
    });
    serveView(() =>
      view(
        { kind: "system", group: { lines: 2, share: "0.4", kp_per_sqm: pair("500", "610") } },
        { group_lines: [...family(), other] }
      )
    );
    renderSheet();
    expect(await screen.findByTestId("group-line-5004")).toHaveTextContent("группа из 3 стр.");
    expect(screen.getByTestId("group-line-5002")).toHaveTextContent("группа из 2 стр.");
  });

  it("суммы строк семьи — в выбранном виде НДС", async () => {
    serveView(() =>
      view({ kind: "system", group: { lines: 2, share: "0.4", kp_per_sqm: pair("500", "610") } }, { group_lines: family() })
    );
    renderSheet({ vat: "shown", vatRate: "22" });
    const member = await screen.findByTestId("group-line-5002");
    expect(member).toHaveTextContent("146 400,00");
    expect(member).not.toHaveTextContent("120 000,00");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("610,00");
  });

  it("выбор ступени меняет семью в панели: список берётся из ответа на выбранную ступень", async () => {
    serveView((a) =>
      view(
        onRung(a, { kind: "system", group: { lines: 2, share: "0.4", kp_per_sqm: pair("500", "610") } }),
        { group_lines: a.level === null ? family() : family().slice(0, 2) }
      )
    );
    renderSheet();
    await screen.findByTestId("group-line-5003");
    fireEvent.click(rung("f", "all"));
    await waitFor(() => expect(screen.queryByTestId("group-line-5003")).not.toBeInTheDocument());
    expect(screen.getByTestId("group-line-5001")).toBeInTheDocument();
    expect(screen.getByTestId("group-line-5002")).toBeInTheDocument();
  });

  it("у работы (group_lines пуст, group = null) блока семьи нет", async () => {
    serveView(() => view({ kind: "work", group: null }, { group_lines: [] }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByTestId("group-caption")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sheet-family")).not.toBeInTheDocument();
  });
});

describe("договоры ступени", () => {
  it("объект, год, класс, номер, ставка; нижняя граница, «договор с этим подрядчиком» и кратность — каждое только у своего договора", async () => {
    serveView(() =>
      view(
        {},
        {
          contracts: [
            contract(101, { is_lower_bound: true, with_this_contractor: true, rate: pair("900.123", "1098.15") }),
            contract(102, { ratio: "7.5", rate: pair("9000", "10980") }),
            contract(103),
            // Каждая пометка по отдельности: подмена одного флага другим на 101 (оба true) не видна.
            contract(104, { with_this_contractor: true }),
            contract(105, { is_lower_bound: true }),
          ],
        }
      )
    );
    renderSheet();
    const first = await screen.findByTestId("contract-101");
    expect(first).toHaveTextContent("Объект 101");
    expect(first).toHaveTextContent("2021");
    expect(first).toHaveTextContent("Класс 2");
    expect(first).toHaveTextContent("Договор 101");
    expect(first).toHaveTextContent("Подрядчик 501");
    expect(first).not.toHaveTextContent("Подрядчик 502");
    expect(first).toHaveTextContent("900,12");
    expect(first).toHaveTextContent(CONTRACT_LOWER_BOUND);
    expect(first).toHaveTextContent(CONTRACT_WITH_CONTRACTOR);
    expect(first).not.toHaveTextContent("проверьте единицу учёта");

    const odd = screen.getByTestId("contract-102");
    expect(odd).toHaveTextContent("×7,5 к остальным договорам — проверьте единицу учёта");
    expect(odd).not.toHaveTextContent(CONTRACT_LOWER_BOUND);
    expect(odd).not.toHaveTextContent(CONTRACT_WITH_CONTRACTOR);

    const plain = screen.getByTestId("contract-103");
    expect(plain).not.toHaveTextContent(CONTRACT_LOWER_BOUND);
    expect(plain).not.toHaveTextContent(CONTRACT_WITH_CONTRACTOR);
    expect(plain).not.toHaveTextContent("проверьте единицу учёта");
    expect(plain).toHaveAttribute("data-lower-bound", "false");
    expect(first).toHaveAttribute("data-lower-bound", "true");

    const withContractor = screen.getByTestId("contract-104");
    expect(withContractor).toHaveTextContent(CONTRACT_WITH_CONTRACTOR);
    expect(withContractor).not.toHaveTextContent(CONTRACT_LOWER_BOUND);
    const lowerOnly = screen.getByTestId("contract-105");
    expect(lowerOnly).toHaveTextContent(CONTRACT_LOWER_BOUND);
    expect(lowerOnly).not.toHaveTextContent(CONTRACT_WITH_CONTRACTOR);

    // Выделение — цветом карточки: нижняя граница — info, кратность — warning, прочие — без подсветки.
    expect(lowerOnly.className).toContain("bg-info-soft");
    expect(odd.className).toContain("bg-warning-soft");
    expect(odd.className).not.toContain("bg-info-soft");
    for (const row of [plain, withContractor]) {
      expect(row.className).not.toContain("bg-info-soft");
      expect(row.className).not.toContain("bg-warning-soft");
    }
  });

  it("ставка договора — в выбранном виде: с НДС берётся показанная", async () => {
    serveView(() => view({}, { contracts: [contract(101, { rate: pair("900.123", "1098.15") })] }));
    renderSheet({ vat: "shown", vatRate: "22" });
    expect(await screen.findByTestId("contract-101")).toHaveTextContent("1 098,15");
    expect(screen.getByTestId("contract-101")).not.toHaveTextContent("900,123");
  });

  it("выпавшие из-за базы НДС — отдельными строками; без них секции нет", async () => {
    serveView(() =>
      view(
        {},
        {
          vat_dropped: [
            { contract_id: 201, object_title: "Объект 201", number: "Договор 201", year: 2019 },
            { contract_id: 202, object_title: "Объект 202", number: "Договор 202", year: 2018 },
          ],
        }
      )
    );
    renderSheet();
    const row = await screen.findByTestId("vat-dropped-201");
    expect(row).toHaveTextContent("Объект 201");
    expect(row).toHaveTextContent("Договор 201");
    expect(row).toHaveTextContent("2019");
    expect(row).toHaveTextContent(VAT_DROPPED_NOTE);
    expect(screen.getByTestId("vat-dropped-202")).toHaveTextContent(VAT_DROPPED_NOTE);
    // Выпавший договор не попадает в список договоров ступени.
    expect(screen.queryByTestId("contract-201")).not.toBeInTheDocument();
  });

  it("без ступени (нет истории) выпавшие договоры всё равно названы", async () => {
    serveView(() =>
      view(
        { state: "no_history", position: null, to_ours: null, ours: null, range: null, rung: null, rungs: [] },
        {
          contracts: [],
          vat_dropped: [{ contract_id: 201, object_title: "Объект 201", number: "Договор 201", year: 2019 }],
        }
      )
    );
    renderSheet();
    const row = await screen.findByTestId("vat-dropped-201");
    expect(row).toHaveTextContent(VAT_DROPPED_NOTE);
    expect(screen.getByTestId("sheet-state")).toHaveTextContent(LINE_STATE_LABEL.no_history);
  });

  it("выпавших нет — секции нет", async () => {
    serveView(() => view({}, { vat_dropped: [] }));
    renderSheet();
    await screen.findByTestId("contract-101");
    expect(screen.queryByTestId("sheet-vat-dropped")).not.toBeInTheDocument();
  });
});

describe("плашка «Разные единицы учёта?»", () => {
  it("цена КП и договоры расходятся: объяснение на цифрах — кратность КП, медиана, кратности договоров", async () => {
    serveView(() =>
      view({
        state: "unit_mismatch",
        position: null,
        unit_mismatch: { kp_ratio: "12.5", contracts: [{ contract_id: 101, ratio: "7.5" }] },
      })
    );
    renderSheet();
    const warn = await screen.findByTestId("unit-warn");
    expect(warn).toHaveTextContent(UNIT_WARN_TITLE);
    expect(warn).toHaveTextContent("в 12,5 раз");
    expect(warn).toHaveTextContent("1 100,00");
    expect(warn).toHaveTextContent("×7,5");
  });

  it("расходятся только договоры (kp_ratio = null): про цену КП в плашке нет ни слова", async () => {
    serveView(() =>
      view({
        state: "unit_mismatch",
        position: null,
        unit_mismatch: { kp_ratio: null, contracts: [{ contract_id: 101, ratio: "9" }] },
      })
    );
    renderSheet();
    const warn = await screen.findByTestId("unit-warn");
    expect(warn).toHaveTextContent("×9");
    expect(warn).not.toHaveTextContent("Цена КП отличается");
  });

  it("расходится только цена КП (договоров с кратностью нет): строки про договоры нет", async () => {
    serveView(() =>
      view({ state: "unit_mismatch", position: null, unit_mismatch: { kp_ratio: "6", contracts: [] } })
    );
    renderSheet();
    const warn = await screen.findByTestId("unit-warn");
    expect(warn).toHaveTextContent("в 6 раз");
    expect(warn).not.toHaveTextContent("Наши договоры расходятся");
  });

  it("у сопоставимой строки (unit_mismatch = null) плашки нет", async () => {
    serveView(() => view({ unit_mismatch: null }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByTestId("unit-warn")).not.toBeInTheDocument();
    expect(screen.queryByText(UNIT_WARN_TITLE)).not.toBeInTheDocument();
  });
});

describe("панель как окно", () => {
  it("без строки панели нет и запроса нет", async () => {
    const asked = serveView(() => view());
    renderSheet({ positionItemId: null });
    expect(screen.queryByTestId("line-sheet")).not.toBeInTheDocument();
    expect(asked).toHaveLength(0);
  });

  it("крестик зовёт onClose", async () => {
    serveView(() => view());
    const onClose = vi.fn();
    renderSheet({ onClose });
    await screen.findByTestId("sheet-prices");
    fireEvent.click(within(screen.getByTestId("line-sheet")).getByRole("button", { name: "Закрыть" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("перезапрос той же ступени (новый ответ разбора) — без серого слоя: слой только у предпросмотра", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    server.use(
      http.get(LINE_PATH, async () => {
        calls += 1;
        if (calls === 1) return HttpResponse.json(view());
        await hold;
        return HttpResponse.json(view({ to_ours: pair("-300.00", "-366.00") }));
      })
    );
    const queryClient = createTestQueryClient();
    renderWithProviders(<LineSheet {...baseProps} />, { queryClient });
    expect(await screen.findByTestId("sheet-to-ours")).toHaveTextContent("+20 000,00");

    act(() => {
      void queryClient.invalidateQueries({
        queryKey: qk.tenders.ourPriceLinesOfOffer(OUR_PRICE_TENDER_ID, OUR_PRICE_ROUND_ID, OUR_PRICE_OFFER_ID),
      });
    });
    await waitFor(() => expect(calls).toBe(2));
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("sheet-to-ours")).toHaveTextContent("+20 000,00");

    release();
    await waitFor(() => expect(screen.getByTestId("sheet-to-ours")).toHaveTextContent("−300,00"));
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
  });

  it("ширина — тем же вариантным префиксом, что у базового Sheet", async () => {
    serveView(() => view());
    renderSheet();
    expect((await opened()).className).toContain("data-[side=right]:sm:max-w-lg");
  });

  it("отказ сервера — подпись отказа, а не пустая панель", async () => {
    server.use(http.get(LINE_PATH, () => HttpResponse.json({ detail: "x" }, { status: 404 })));
    renderSheet();
    expect(await screen.findByText(SHEET_ERROR_LABEL)).toBeInTheDocument();
    expect(screen.queryByTestId("sheet-prices")).not.toBeInTheDocument();
  });

  it("первая загрузка — скелет, цифр нет", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(LINE_PATH, async () => {
        await hold;
        return HttpResponse.json(view());
      })
    );
    renderSheet();
    await opened();
    expect(screen.queryByTestId("sheet-prices")).not.toBeInTheDocument();
    expect(screen.getByTestId("line-sheet").querySelector(".animate-pulse")).not.toBeNull();
    release();
    expect(await screen.findByTestId("sheet-prices")).toBeInTheDocument();
  });
});

describe("в составе страницы разбора", () => {
  function renderPage(search = "?article=11") {
    return renderWithProviders(
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
      </Routes>,
      { initialRoute: ROUTE + search, initialUser: { id: 1, email: "u@example.com", role: "admin" } }
    );
  }
  const requestsOf = (kind: string) => handlerState.ourPriceRequests.filter((r) => r.kind === kind);

  it("щелчок по строке открывает панель этой строки; закрытие снимает её", async () => {
    renderPage();
    await screen.findByTestId("tile-per_sqm");
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    await screen.findByTestId("line-sheet");
    expect(new URL(requestsOf("line")[0].url).pathname).toContain(`/lines/${OUR_PRICE_LINE_ID}`);
    fireEvent.click(within(screen.getByTestId("line-sheet")).getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(screen.queryByTestId("line-sheet")).not.toBeInTheDocument());
    expect(screen.getByTestId("offer-page")).not.toHaveAttribute("data-open-line");
  });

  it("предпросмотр ступени не меняет цифры страницы разбора и не повторяет её запрос", async () => {
    renderPage();
    await screen.findByTestId("tile-per_sqm");
    const tiles = ["tile-per_sqm", "tile-above_ours", "tile-below_paid"].map((id) => screen.getByTestId(id).textContent);
    const toOurs = screen.getByTestId(`to-ours-${OUR_PRICE_LINE_ID}`).textContent;
    const offerCalls = requestsOf("offer").length;

    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    await screen.findByTestId("rung-f-all");
    fireEvent.click(rung("f", "all"));
    await waitFor(() => expect(rung("f", "all")).toHaveAttribute("aria-checked", "true"));
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());

    const preview = requestsOf("line").map((r) => new URL(r.url).searchParams.get("level"));
    expect(preview).toContain("f");
    expect(["tile-per_sqm", "tile-above_ours", "tile-below_paid"].map((id) => screen.getByTestId(id).textContent)).toEqual(tiles);
    expect(screen.getByTestId(`to-ours-${OUR_PRICE_LINE_ID}`).textContent).toBe(toOurs);
    expect(requestsOf("offer")).toHaveLength(offerCalls);
  });

  it("с рядом ИПЦ панель запрашивается за тем же рядом", async () => {
    renderPage("?inflation=3&article=11");
    await screen.findByTestId("tile-per_sqm");
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    await screen.findByTestId("sheet-prices");
    expect(requestsOf("line").map((r) => new URL(r.url).searchParams.get("inflation_series_id"))).toEqual(["3"]);
  });

  it("при отказе ИПЦ панель идёт за номинальными цифрами экрана, а не за отказавшим рядом", async () => {
    handlerState.ourPriceRefusal = "amendment_date_missing";
    renderPage("?inflation=3&article=11");
    await screen.findByTestId("tile-per_sqm");
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    expect(await screen.findByTestId("sheet-prices")).toBeInTheDocument();
    expect(screen.queryByText(SHEET_ERROR_LABEL)).not.toBeInTheDocument();
    const lines = requestsOf("line");
    expect(lines.length).toBeGreaterThan(0);
    for (const r of lines) expect(new URL(r.url).searchParams.has("inflation_series_id")).toBe(false);
  });

  it("вид НДС и ставка участника доходят до панели: «с НДС» — цифры и подпись в ставке КП", async () => {
    renderPage("?vat=shown&article=11");
    await screen.findByTestId("tile-per_sqm");
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    expect(await screen.findByTestId("sheet-prices")).toHaveTextContent("(договоры — по ставке КП)");
    expect(screen.getByTestId("sheet-kp-price")).toHaveTextContent("1 464,00");
  });

  it("каталог доразобрал строки: открытая панель перезапрошена и уже не «в разборе», без закрытия", async () => {
    handlerState.ourPriceOfferInProgress = [1, 0];
    handlerState.ourPriceLineStates = ["in_progress", "comparable"];
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage();
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));
    await vi.waitFor(() => expect(screen.getByTestId("sheet-state")).toHaveTextContent(LINE_STATE_LABEL.in_progress));

    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument());
    await vi.waitFor(() => expect(screen.getByTestId("sheet-state")).not.toHaveTextContent(LINE_STATE_LABEL.in_progress));
    await act(() => vi.advanceTimersByTimeAsync(50));
    expect(requestsOf("offer")).toHaveLength(2);
    expect(requestsOf("line")).toHaveLength(2);
    expect(screen.getByTestId("line-sheet")).toBeInTheDocument();
  });
});

describe("LineSheet: подпись «ждёт решения администратора»", () => {
  const waitingView = () => view({ state: "no_variant", waiting_admin: true, context_id: 777, rungs: [], rung: null }, { contracts: [] });

  it("открытое предложение: ссылка на очередь «Предложения» с единицей и контекстом", async () => {
    serveView(() =>
      view(
        { state: "no_variant", waiting_admin: true, suggestion_queue: "list", context_id: 777, unit_id: 5, rungs: [], rung: null },
        { contracts: [] }
      )
    );
    renderSheet({ canResolve: true });

    const state = await screen.findByTestId("sheet-state");

    expect(within(state).getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute(
      "href",
      "/families?tab=suggestions&queue=list&unit=5&context=777"
    );
  });

  it("открытое предложение новой семьи: ссылка на очередь «Новая»", async () => {
    serveView(() =>
      view(
        { state: "no_variant", waiting_admin: true, suggestion_queue: "new", context_id: 777, unit_id: 5, rungs: [], rung: null },
        { contracts: [] }
      )
    );
    renderSheet({ canResolve: true });

    const state = await screen.findByTestId("sheet-state");

    expect(within(state).getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute(
      "href",
      "/families?tab=suggestions&queue=new&unit=5&context=777"
    );
  });

  it("admin: подпись состояния — ссылка на карточку контекста", async () => {
    serveView(waitingView);
    renderSheet({ canResolve: true });

    const state = await screen.findByTestId("sheet-state");

    expect(within(state).getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute(
      "href",
      "/families?context=777"
    );
  });

  it("member: тот же текст без ссылки", async () => {
    serveView(waitingView);
    renderSheet({ canResolve: false });

    const state = await screen.findByTestId("sheet-state");

    expect(state).toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(within(state).queryByRole("link")).not.toBeInTheDocument();
  });

  // Роль доходит до панели со страницы разбора: проводка `canResolve` страницы,
  // а не только проп панели в изоляции.
  it.each([
    ["admin", true],
    ["member", false],
  ] as const)("на странице разбора: %s — ссылка в подписи панели: %s", async (role, linked) => {
    serveView(waitingView);
    renderWithProviders(
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId" element={<OurPriceOfferPage />} />
      </Routes>,
      { initialRoute: `${ROUTE}?article=11`, initialUser: { id: 1, email: "u@example.com", role } }
    );
    await screen.findByTestId("tile-per_sqm");
    fireEvent.click(screen.getByTestId(`line-${OUR_PRICE_LINE_ID}`));

    const state = await screen.findByTestId("sheet-state");

    expect(state).toHaveTextContent(WAITING_ADMIN_LABEL);
    if (linked) {
      expect(within(state).getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute("href", "/families?context=777");
    } else {
      expect(within(state).queryByRole("link")).not.toBeInTheDocument();
    }
  });
});

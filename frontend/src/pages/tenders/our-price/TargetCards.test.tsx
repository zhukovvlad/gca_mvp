import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  contractorFigureFixture,
  KP_FILE_AUTHOR,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceAnalogFixture,
  workLineFixture,
  workLineViewFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { MoneyPair, OurPriceLine, TargetChoice } from "@/types/domain";

import { LineSheet } from "./LineSheet";
import { TargetCards, type TargetCardsProps } from "./TargetCards";

const pair = (net: string, shown: string | null = net): MoneyPair => ({ net, shown });

const analogs = [
  ourPriceAnalogFixture({ analog_id: 8001, object_title: "Объект Ж", number: "ГП-08", period: "2025-03", is_lowest: true }),
  ourPriceAnalogFixture({
    analog_id: 8002,
    contract_id: 102,
    object_title: "Объект З",
    number: "Д-112",
    period: "2026-08",
    is_freshest: true,
    price: pair("1100", "1342"),
  }),
];

const choice = (over: Partial<TargetChoice> = {}): TargetChoice => ({
  kind: "freshest",
  analog_id: null,
  active: true,
  reset_reason: null,
  set_by: KP_FILE_AUTHOR,
  set_at: "2026-03-14T09:30:00+00:00",
  ...over,
});

function renderCards(
  line: OurPriceLine = workLineFixture(),
  props: Partial<Omit<TargetCardsProps, "line">> = {}
) {
  const onChoose = vi.fn<TargetCardsProps["onChoose"]>();
  render(
    <TargetCards
      line={line}
      analogs={props.analogs ?? analogs}
      vat={props.vat ?? "net"}
      canEdit={props.canEdit ?? true}
      busy={props.busy ?? false}
      onChoose={onChoose}
    />
  );
  return onChoose;
}

const card = (kind: "lowest" | "freshest" | "contractor") => screen.getByTestId(`figure-${kind}`);

describe("карточки цели: состав", () => {
  it("по карточке на стратегию: самая низкая, самая свежая, с этим подрядчиком; у каждой своя цена и договор", () => {
    renderCards(
      workLineFixture({ contractor: contractorFigureFixture({ analog_id: 8001, contract_id: 101, period: "2026-01" }) })
    );
    expect(card("lowest")).toHaveTextContent("самая низкая");
    expect(card("lowest")).toHaveTextContent("1 000,00");
    expect(card("lowest")).toHaveTextContent("Объект Ж · дог. ГП-08");
    expect(card("freshest")).toHaveTextContent("самая свежая");
    expect(card("freshest")).toHaveTextContent("1 100,00");
    expect(card("freshest")).toHaveTextContent("Объект З · дог. Д-112 · 08.2026");
    expect(card("contractor")).toHaveTextContent("с этим подрядчиком");
    expect(card("contractor")).toHaveTextContent("1 050,00");
  });

  it("«с этим подрядчиком»: та же строка «КП выше / ниже», что у «самой свежей», текст из ответа", () => {
    renderCards(
      workLineFixture({
        unit: "м3",
        contractor: contractorFigureFixture(),
        to_contractor: pair("300.00", "366.00"),
        to_contractor_unit: pair("30", "36.6"),
      })
    );
    expect(screen.getByTestId("figure-contractor-diff")).toHaveTextContent(/^КП выше на 30,00 за м3 · \+300,00/);
    expect(screen.getByTestId("figure-freshest-diff")).toHaveTextContent(/^КП выше на 100,00 за м3 · \+10 000,00/);
  });

  it("«с этим подрядчиком»: КП ниже — минус в итоге, без знака у единицы; разницы нет — прочерк", () => {
    renderCards(
      workLineFixture({
        unit: "м3",
        contractor: contractorFigureFixture(),
        to_contractor: pair("-100.00"),
        to_contractor_unit: pair("-10"),
      })
    );
    expect(screen.getByTestId("figure-contractor-diff")).toHaveTextContent(/^КП ниже на 10,00 за м3 · −100,00/);
  });

  it("«с этим подрядчиком»: разницы нет (null) — прочерк", () => {
    renderCards(workLineFixture({ contractor: contractorFigureFixture() }));
    expect(screen.getByTestId("figure-contractor-diff")).toHaveTextContent("—");
  });

  it("договора с этим подрядчиком среди аналогов нет — карточки нет", () => {
    renderCards(workLineFixture({ contractor: null }));
    expect(screen.queryByTestId("figure-contractor")).not.toBeInTheDocument();
    expect(card("lowest")).toBeInTheDocument();
    expect(card("freshest")).toBeInTheDocument();
  });

  it("цифр нет вовсе (все аналоги — выбросы): карточек нет", () => {
    renderCards(workLineFixture({ lowest: null, freshest: null, contractor: null }));
    expect(screen.queryByTestId("figure-lowest")).not.toBeInTheDocument();
    expect(screen.queryByTestId("figure-freshest")).not.toBeInTheDocument();
  });

  it("карточки не объединяются: «самая свежая» и «с этим подрядчиком» на одном договоре — две карточки", () => {
    renderCards(
      workLineFixture({
        freshest: { value: pair("1100", "1342"), analog_id: 8002, contract_id: 102, period: "2026-08" },
        contractor: contractorFigureFixture({ analog_id: 8002, contract_id: 102, value: pair("1100", "1342") }),
      })
    );
    expect(screen.getAllByTestId(/^figure-(lowest|freshest|contractor)$/)).toHaveLength(3);
    expect(card("contractor")).toHaveTextContent("тот же договор, что «самая свежая»");
    expect(card("freshest")).not.toHaveTextContent("тот же договор");
    expect(card("lowest")).not.toHaveTextContent("тот же договор");
  });

  it("договоры разные — подписи «тот же договор» нет ни у одной", () => {
    renderCards(workLineFixture({ contractor: contractorFigureFixture({ contract_id: 105 }) }));
    expect(screen.queryByText(/тот же договор/)).not.toBeInTheDocument();
  });

  it("самой свежей нет: у карточки подпись, а не ноль и не пустота", () => {
    renderCards(workLineFixture({ freshest: null, to_freshest: null }));
    expect(card("freshest")).toHaveTextContent("нет аналога с датой цены");
    expect(card("freshest")).not.toHaveTextContent("0,00");
  });
});

describe("карточки цели: что нажато", () => {
  it("выбора нет — нажата «самая низкая» (авто)", () => {
    renderCards(workLineFixture({ target_kind: "lowest", choice: null, contractor: contractorFigureFixture() }));
    expect(card("lowest")).toHaveAttribute("data-selected", "true");
    expect(card("lowest")).toHaveTextContent("цель · авто");
    expect(card("freshest")).toHaveAttribute("data-selected", "false");
    expect(card("contractor")).toHaveAttribute("data-selected", "false");
  });

  it.each(["freshest", "contractor"] as const)("выбор «%s» действует — нажата только его карточка", (kind) => {
    renderCards(
      workLineFixture({ target_kind: kind, choice: choice({ kind }), contractor: contractorFigureFixture() })
    );
    for (const k of ["lowest", "freshest", "contractor"] as const) {
      expect(card(k)).toHaveAttribute("data-selected", String(k === kind));
    }
    expect(card(kind)).toHaveTextContent("цель");
    expect(card(kind)).not.toHaveTextContent("авто");
  });

  it("договорились перекрывает цель, но карточка остаётся на выбранной стратегии", () => {
    renderCards(workLineFixture({ target_kind: "agreed", choice: choice({ kind: "freshest" }) }));
    expect(card("freshest")).toHaveAttribute("data-selected", "true");
    expect(card("lowest")).toHaveAttribute("data-selected", "false");
  });

  it("закреплён конкретный аналог: ни одна стратегия не нажата, есть карточка «выбранный аналог» с его ценой", () => {
    renderCards(
      workLineFixture({
        target_kind: "analog",
        choice: choice({ kind: "analog", analog_id: 8002 }),
        contractor: contractorFigureFixture(),
      })
    );
    for (const k of ["lowest", "freshest", "contractor"] as const) {
      expect(card(k)).toHaveAttribute("data-selected", "false");
    }
    const pinned = screen.getByTestId("figure-pinned");
    expect(pinned).toHaveAttribute("data-selected", "true");
    expect(pinned).toHaveTextContent("выбранный аналог");
    expect(pinned).toHaveTextContent("1 100,00");
    expect(pinned).toHaveTextContent("Объект З");
  });

  it("выбор не действует: плашка с причиной, нажата «самая низкая»", () => {
    renderCards(
      workLineFixture({
        target_kind: "lowest",
        choice: choice({ kind: "contractor", active: false, reset_reason: "no_contractor" }),
      })
    );
    expect(screen.getByTestId("target-inactive")).toHaveTextContent("Выбор не действует:");
    expect(card("lowest")).toHaveAttribute("data-selected", "true");
  });

  it("цель сброшена причиной lump_sum: «комплект вместо ставки»", () => {
    renderCards(
      workLineFixture({
        target_kind: "lowest",
        choice: choice({ kind: "analog", analog_id: 8001, active: false, reset_reason: "lump_sum" }),
      })
    );
    expect(screen.getByTestId("target-inactive")).toHaveTextContent("Выбор не действует: комплект вместо ставки");
  });

  it("цель сброшена причиной excluded: «строка исключена из аналогов»", () => {
    renderCards(
      workLineFixture({
        target_kind: "lowest",
        choice: choice({ kind: "analog", analog_id: 8001, active: false, reset_reason: "excluded" }),
      })
    );
    expect(screen.getByTestId("target-inactive")).toHaveTextContent("Выбор не действует: строка исключена из аналогов");
  });

  it("строка не сравнима (цели нет): карточки видны, но ни одна не нажата и нажать нельзя", () => {
    renderCards(workLineFixture({ state: "unit_mismatch", target_kind: null, position: null }));
    expect(card("lowest")).toHaveAttribute("data-selected", "false");
    expect(within(card("lowest")).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("карточки цели: нажатие", () => {
  it("нажатие отдаёт вид стратегии: lowest, freshest, contractor", async () => {
    const onChoose = renderCards(
      workLineFixture({ target_kind: "analog", choice: choice({ kind: "analog", analog_id: 8002 }), contractor: contractorFigureFixture() })
    );
    await userEvent.click(within(card("lowest")).getByRole("button"));
    await userEvent.click(within(card("freshest")).getByRole("button"));
    await userEvent.click(within(card("contractor")).getByRole("button"));
    expect(onChoose.mock.calls).toEqual([["lowest"], ["freshest"], ["contractor"]]);
  });

  it("нажатая карточка повторного нажатия не принимает", async () => {
    const onChoose = renderCards(workLineFixture({ target_kind: "lowest", choice: null }));
    await userEvent.click(within(card("lowest")).getByRole("button", { pressed: true }));
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("не admin: карточки цифрами, без кнопок", () => {
    renderCards(workLineFixture({ contractor: contractorFigureFixture() }), { canEdit: false });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(card("lowest")).toHaveAttribute("data-selected", "true");
  });

  it("пока цель сохраняется — кнопки погашены", () => {
    renderCards(workLineFixture({ target_kind: "analog", choice: choice({ kind: "analog", analog_id: 8002 }) }), {
      busy: true,
    });
    expect(within(card("lowest")).getByRole("button")).toBeDisabled();
  });
});

describe("карточки цели: цифры (вид «КП выше / ниже»)", () => {
  it("«КП ниже»: отрицательная разница печатается с минусом, а слова — «ниже»", () => {
    renderCards(workLineFixture({ to_lowest: pair("-300.00"), position: "below" }));
    expect(card("lowest")).toHaveTextContent("КП ниже");
    expect(card("lowest")).toHaveTextContent("−300,00");
  });

  it("сначала разница за единицу, затем итог", () => {
    renderCards(workLineFixture({ unit: "м3" }));
    expect(screen.getByTestId("figure-lowest-diff")).toHaveTextContent(/^КП выше на 200,00 за м3 · \+20 000,00/);
  });

  it("«ниже»: разница за единицу без знака после «на», итог со знаком", () => {
    renderCards(workLineFixture({ unit: "м3", to_lowest: pair("-300.00"), to_lowest_unit: pair("-30", "-36.6") }));
    expect(screen.getByTestId("figure-lowest-diff")).toHaveTextContent(/^КП ниже на 30,00 за м3 · −300,00/);
  });

  it("вид «с НДС»: цена и разница из shown", () => {
    renderCards(workLineFixture({ unit: "м3" }), { vat: "shown" });
    expect(card("lowest")).toHaveTextContent("1 220,00");
    expect(screen.getByTestId("figure-lowest-diff")).toHaveTextContent("КП выше на 244,00 за м3");
    expect(card("lowest")).toHaveTextContent("+24 400,00");
  });

  it("без разницы за единицу (null): «КП выше: +…»", () => {
    renderCards(workLineFixture({ unit: "м3", to_lowest_unit: null }));
    const diff = screen.getByTestId("figure-lowest-diff");
    expect(diff).toHaveTextContent(/^КП выше: \+20 000,00/);
    expect(diff).not.toHaveTextContent("за м3");
  });

  it("нулевая разница: «КП на уровне расценки», без знака", () => {
    renderCards(workLineFixture({ to_lowest: pair("0.00") }));
    expect(card("lowest")).toHaveTextContent("КП на уровне расценки");
    expect(card("lowest")).not.toHaveTextContent("+0,00");
  });

  it("разницы нет (null): прочерк, а не «+0,00»", () => {
    renderCards(workLineFixture({ to_lowest: null }));
    expect(screen.getByTestId("figure-lowest-diff")).toHaveTextContent("—");
    expect(card("lowest")).not.toHaveTextContent("КП выше");
    expect(card("lowest")).not.toHaveTextContent("КП ниже");
  });

  it("аналога нет среди справки: цена остаётся, объект не выдумывается", () => {
    renderCards(workLineFixture(), { analogs: [] });
    expect(card("lowest")).toHaveTextContent("1 000,00");
    expect(card("lowest")).not.toHaveTextContent("Объект");
  });

  it("у карточки с датой цены без даты в справке дата не печатается", () => {
    renderCards(
      workLineFixture({ freshest: { value: pair("1100"), analog_id: 8002, contract_id: 102, period: null } }),
      { analogs: [analogs[0], { ...analogs[1], period: null }] }
    );
    expect(card("freshest")).not.toHaveTextContent("08.2026");
  });

  it("цена с длинным хвостом печатается строкой, без округления через Number", () => {
    renderCards(
      workLineFixture({
        lowest: {
          value: pair("1234.56789012345678901234", "1506.17"),
          analog_id: 8001,
          contract_id: 101,
          from_all_classes: false,
        },
      })
    );
    expect(card("lowest")).toHaveTextContent("1 234,57");
  });
});

// ---------------------------------------------------------------------------
//  Нажатие карточки в панели: на провод уходит ВИД, а не аналог.
// ---------------------------------------------------------------------------

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ADMIN = { id: 1, email: "admin@example.test", role: "admin" } as const;
const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));

function renderPanel(make: () => ReturnType<typeof workLineViewFixture>, seriesId: number | null = null) {
  server.use(http.get(LINE_PATH, () => HttpResponse.json(make())));
  return renderWithProviders(
    <LineSheet
      tenderId={OUR_PRICE_TENDER_ID}
      roundId={OUR_PRICE_ROUND_ID}
      offerId={OUR_PRICE_OFFER_ID}
      positionItemId={OUR_PRICE_LINE_ID}
      seriesId={seriesId}
      vat="net"
      vatRate={null}
      onClose={() => {}}
    />,
    { initialUser: ADMIN }
  );
}

/** Договор подрядчика совпадает с договором самой свежей: оба указывают на один аналог. */
const sameAnalogView = (over: Partial<OurPriceLine> = {}) =>
  workLineViewFixture({
    line: {
      contractor: contractorFigureFixture({ analog_id: 8002, contract_id: 102, value: pair("1100", "1342") }),
      ...over,
    },
  });

describe("нажатие карточки в панели: вид, а не аналог", () => {
  it("«с этим подрядчиком» — PUT {kind: contractor} и ничего больше, хотя аналог цели известен", async () => {
    renderPanel(() => sameAnalogView(), 3);
    await userEvent.click(within(await screen.findByTestId("figure-contractor")).getByRole("button"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].method).toBe("PUT");
    expect(targets()[0].body).toEqual({ kind: "contractor" });
    expect(new URL(targets()[0].url).searchParams.get("inflation_series_id")).toBe("3");
  });

  it("«самая свежая» — PUT {kind: freshest}, без analog_id, хотя у карточки есть аналог", async () => {
    renderPanel(() => sameAnalogView());
    await userEvent.click(within(await screen.findByTestId("figure-freshest")).getByRole("button"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].method).toBe("PUT");
    expect(targets()[0].body).toEqual({ kind: "freshest" });
  });

  it("«самая низкая» при действующем выборе — DELETE .../target, без тела", async () => {
    renderPanel(() => sameAnalogView({ target_kind: "freshest", choice: choice() }));
    await userEvent.click(within(await screen.findByTestId("figure-lowest")).getByRole("button"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].method).toBe("DELETE");
    expect(targets()[0].body).toBeNull();
  });

  it("«сделать целью» у аналога — единственный путь с analog_id", async () => {
    renderPanel(() => sameAnalogView());
    const row = await screen.findByTestId("analog-8002");
    await userEvent.click(within(row).getByRole("button", { name: "сделать целью" }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({ kind: "analog", analog_id: 8002 });
  });

  it("422 no_contractor_analog: тост с текстом сервера, панель не ломается", async () => {
    handlerState.kpFileDecisionOutcome = "no_contractor_analog";
    renderPanel(() => sameAnalogView());
    await userEvent.click(within(await screen.findByTestId("figure-contractor")).getByRole("button"));
    expect(await screen.findByText("Среди аналогов остатка нет договора с этим подрядчиком.")).toBeInTheDocument();
    expect(screen.getByTestId("figure-lowest")).toBeInTheDocument();
  });
});

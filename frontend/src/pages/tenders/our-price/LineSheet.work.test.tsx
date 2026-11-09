import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  KP_FILE_AUTHOR,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceAnalogFixture,
  ourPriceLineFixture,
  workLineViewFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { ANALOG_UNAVAILABLE_TOAST } from "@/services/queries";
import type { MoneyPair, OurPriceLine, OurPriceLineView, TargetChoice } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";
import {
  ANALOGS_COLUMN,
  SAVING_LABEL,
  TARGET_BACK_TO_LOWEST,
  TARGET_MAKE_FRESHEST,
  UNIT_WARN_ALL_OUTLIERS,
  zoneQueueHref,
} from "./ourPriceCopy";

const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ADMIN = { id: 1, email: "admin@example.test", role: "admin" } as const;
const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;
const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });

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
  const asked: string[] = [];
  server.use(
    http.get(LINE_PATH, ({ request }) => {
      asked.push(request.url);
      return HttpResponse.json(make());
    })
  );
  return asked;
}

function renderSheet(props: Partial<LineSheetProps> = {}, user: typeof ADMIN | typeof MEMBER = ADMIN) {
  return renderWithProviders(<LineSheet {...baseProps} {...props} />, { initialUser: user });
}

const choice = (over: Partial<TargetChoice> = {}): TargetChoice => ({
  kind: "freshest",
  analog_id: null,
  active: true,
  reset_reason: null,
  set_by: KP_FILE_AUTHOR,
  set_at: "2026-03-14T09:30:00+00:00",
  ...over,
});

const targets = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision" && r.url.includes("/target"));

describe("панель строки-работы: сравнение", () => {
  it("подпись ступени, зона и число аналогов в договорах", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const comparison = await screen.findByTestId("sheet-comparison");
    expect(comparison).toHaveTextContent("тот же вариант");
    expect(comparison).toHaveTextContent("2 аналога в 2 договорах");
    expect(screen.getByTestId("sheet-zone")).toHaveTextContent("надземная часть");
  });

  it("ступень 2 с другим классом: «без …» и «другой класс»", async () => {
    serve(() =>
      workLineViewFixture({ line: { work_rung: { no: 4, released: ["Марка W", "Толщина"], other_class: true } } })
    );
    renderSheet();
    expect(await screen.findByTestId("sheet-comparison")).toHaveTextContent("без Марка W, Толщина · другой класс");
  });

  it("две карточки: цена, объект и договор, дата цены, разница КП к ней со знаком", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const low = await screen.findByTestId("figure-lowest");
    expect(low).toHaveTextContent("Самая низкая расценка");
    expect(low).toHaveTextContent("1 000,00");
    expect(low).toHaveTextContent("Объект Ж");
    expect(low).toHaveTextContent("ГП-08");
    expect(low).toHaveTextContent("03.2025");
    expect(low).toHaveTextContent("КП выше");
    expect(low).toHaveTextContent("+20 000,00");
    const fresh = screen.getByTestId("figure-freshest");
    expect(fresh).toHaveTextContent("Самая свежая расценка");
    expect(fresh).toHaveTextContent("1 100,00");
    expect(fresh).toHaveTextContent("Объект З");
    expect(fresh).toHaveTextContent("08.2026");
    expect(fresh).toHaveTextContent("+10 000,00");
  });

  it("у работы нет лестницы ступеней, договоров ступени и ручной ступени", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    await screen.findByTestId("sheet-comparison");
    expect(screen.queryByTestId("rung-v-cls")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sheet-contracts")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить эту ступень" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуть авто" })).not.toBeInTheDocument();
  });

  it("у системы лестница на месте, а справки аналогов нет", async () => {
    serve(() => ({ ...workLineViewFixture(), line: ourPriceLineFixture({ kind: "system" }), analogs: [] }));
    renderSheet();
    expect(await screen.findByTestId("rung-v-cls")).toBeInTheDocument();
    expect(screen.queryByTestId("sheet-comparison")).not.toBeInTheDocument();
    expect(screen.queryByTestId("analogs-table")).not.toBeInTheDocument();
  });

  it("самой свежей нет: у карточки подпись, а не ноль и не пустота", async () => {
    serve(() => workLineViewFixture({ line: { freshest: null, to_freshest: null } }));
    renderSheet();
    const fresh = await screen.findByTestId("figure-freshest");
    expect(fresh).toHaveTextContent("нет аналога с датой цены");
    expect(fresh).not.toHaveTextContent("0,00");
  });

  it("все аналоги — выбросы: цифр нет, плашка «Разные единицы учёта?», справка помечена", async () => {
    serve(() =>
      workLineViewFixture({
        line: {
          state: "unit_mismatch",
          position: null,
          lowest: null,
          freshest: null,
          to_lowest: null,
          to_freshest: null,
          over_lowest: null,
          over_freshest: null,
          target_kind: null,
          unit_mismatch: {
            kp_ratio: null,
            all_outliers: true,
            analogs: [
              { analog_id: 8001, ratio: "10" },
              { analog_id: 8002, ratio: "0.1" },
            ],
            contracts: [],
          },
        },
        analogs: [
          ourPriceAnalogFixture({ analog_id: 8001, outlier_ratio: "10", selectable: false }),
          ourPriceAnalogFixture({ analog_id: 8002, outlier_ratio: "0.1", selectable: false }),
        ],
      })
    );
    renderSheet();
    const warn = await screen.findByTestId("unit-warn");
    expect(warn).toHaveTextContent(UNIT_WARN_ALL_OUTLIERS);
    expect(screen.queryByTestId("figure-lowest")).not.toBeInTheDocument();
    expect(screen.getByTestId("analog-8001")).toHaveTextContent("×10 к остальным — проверьте единицу учёта");
    expect(screen.getByTestId("analog-8002")).toHaveTextContent("в 10 раз меньше остальных — проверьте единицу учёта");
  });

  it("справка: колонки и строки аналогов; свежая и низкая помечены", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const table = await screen.findByTestId("analogs-table");
    for (const title of Object.values(ANALOGS_COLUMN)) expect(table).toHaveTextContent(title);
    expect(within(screen.getByTestId("analog-8001")).getByText("самая низкая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8002")).getByText("свежая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8001")).getByText("договор с этим подрядчиком")).toBeInTheDocument();
  });
});

describe("причины «не с чем сравнить» у работы", () => {
  const empty: Partial<OurPriceLine> = {
    state: "no_history",
    position: null,
    work_rung: null,
    lowest: null,
    freshest: null,
    to_lowest: null,
    to_freshest: null,
    over_lowest: null,
    over_freshest: null,
    range: null,
    target_kind: null,
    analogs_count: 0,
  };

  it.each([
    ["no_family", "в договорах нет работ этой семьи"],
    ["path_conflict", "нет аналога: параметры расходятся по разделам сметы"],
    ["no_params", "нет аналога с этими параметрами"],
    ["no_zone_match", "нет аналога в зоне «надземная часть»"],
  ] as const)("no_history · %s: причина текстом", async (reason, text) => {
    serve(() => workLineViewFixture({ line: { ...empty, no_history_reason: reason }, analogs: [] }));
    renderSheet();
    expect(await screen.findByTestId("sheet-state")).toHaveTextContent(text);
  });

  it("no_zone у admin: «ждёт решения администратора» — ссылка в очередь «Зоны» с цепочкой", async () => {
    serve(() =>
      workLineViewFixture({
        line: { ...empty, state: "no_zone", zone: null, waiting_admin: true, section_chain_id: 91 },
        analogs: [],
      })
    );
    renderSheet({ canResolve: true }, ADMIN);
    const link = await within(await screen.findByTestId("sheet-state")).findByRole("link", {
      name: "ждёт решения администратора",
    });
    expect(link).toHaveAttribute("href", zoneQueueHref(91));
    expect(link).toHaveAttribute("href", "/families?tab=suggestions&queue=zones&chain=91");
  });

  it("no_zone не у admin: тот же текст без ссылки", async () => {
    serve(() =>
      workLineViewFixture({
        line: { ...empty, state: "no_zone", zone: null, waiting_admin: true, section_chain_id: 91 },
        analogs: [],
      })
    );
    renderSheet({}, MEMBER);
    const state = await screen.findByTestId("sheet-state");
    expect(state).toHaveTextContent("ждёт решения администратора");
    expect(within(state).queryByRole("link")).not.toBeInTheDocument();
  });

  it("no_zone без ожидания администратора: подпись состояния без ссылки", async () => {
    serve(() =>
      workLineViewFixture({
        line: { ...empty, state: "no_zone", zone: null, waiting_admin: false, section_chain_id: 91 },
        analogs: [],
      })
    );
    renderSheet({}, ADMIN);
    const state = await screen.findByTestId("sheet-state");
    expect(state).toHaveTextContent("зона раздела не определена");
    expect(within(state).queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("«Для выгрузки»: цель строки-работы", () => {
  it("по умолчанию — самая низкая (авто); admin видит «Сделать целью самую свежую», «Вернуть» нет", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const row = await screen.findByTestId("target-row");
    expect(row).toHaveTextContent("Цель: самая низкая расценка");
    expect(row).toHaveTextContent("авто");
    expect(screen.getByRole("button", { name: TARGET_MAKE_FRESHEST })).toBeEnabled();
    expect(screen.queryByRole("button", { name: TARGET_BACK_TO_LOWEST })).not.toBeInTheDocument();
  });

  it("не admin: строка цели есть, кнопок и «сделать целью» нет", async () => {
    serve(() => workLineViewFixture());
    renderSheet({}, MEMBER);
    expect(await screen.findByTestId("target-row")).toHaveTextContent("Цель: самая низкая расценка");
    expect(screen.queryByRole("button", { name: TARGET_MAKE_FRESHEST })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
  });

  it("«сделать целью» стоит только у аналога с selectable и только у admin", async () => {
    serve(() =>
      workLineViewFixture({
        analogs: [
          ourPriceAnalogFixture({ analog_id: 8001, selectable: true }),
          ourPriceAnalogFixture({ analog_id: 8002, selectable: false, outlier_ratio: "9" }),
        ],
      })
    );
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(within(screen.getByTestId("analog-8001")).getByRole("button", { name: "сделать целью" })).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8002")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("unit_mismatch с selectable=true: выбор цели только у сравнимой строки — кнопок нет", async () => {
    serve(() =>
      workLineViewFixture({
        line: {
          state: "unit_mismatch",
          position: null,
          unit_mismatch: { kp_ratio: "9", all_outliers: false, analogs: [], contracts: [] },
          target_kind: null,
        },
      })
    );
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(screen.queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TARGET_MAKE_FRESHEST })).not.toBeInTheDocument();
  });

  it("цель — самая свежая: строка цели, «Вернуть к самой низкой»; кнопки «самую свежую» нет", async () => {
    serve(() => workLineViewFixture({ line: { target_kind: "freshest", choice: choice() } }));
    renderSheet();
    expect(await screen.findByTestId("target-row")).toHaveTextContent("Цель: самая свежая расценка");
    expect(screen.queryByRole("button", { name: TARGET_MAKE_FRESHEST })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: TARGET_BACK_TO_LOWEST })).toBeInTheDocument();
  });

  it("цель — аналог: названы объект и договор, а у этого аналога вместо кнопки метка «цель»", async () => {
    serve(() =>
      workLineViewFixture({ line: { target_kind: "analog", choice: choice({ kind: "analog", analog_id: 8002 }) } })
    );
    renderSheet();
    expect(await screen.findByTestId("target-row")).toHaveTextContent("Цель: аналог — Объект З, дог. Д-112");
    const mine = screen.getByTestId("analog-8002");
    expect(within(mine).queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
    expect(within(mine).getByText("цель")).toBeInTheDocument();
  });

  it("«договорились»: цель названа договорённой ценой", async () => {
    serve(() => workLineViewFixture({ line: { target_kind: "agreed" } }));
    renderSheet();
    expect(await screen.findByTestId("target-row")).toHaveTextContent("Цель: договорённая цена");
  });

  it("выбор не действует: плашка с причиной, цель — самая низкая, «Вернуть» остаётся", async () => {
    serve(() =>
      workLineViewFixture({
        line: {
          target_kind: "lowest",
          choice: choice({ kind: "analog", analog_id: 8003, active: false, reset_reason: "analog_unavailable" }),
        },
      })
    );
    renderSheet();
    expect(await screen.findByTestId("target-inactive")).toHaveTextContent(
      "Выбор не действует: аналога больше нет на ступени"
    );
    expect(screen.getByTestId("target-row")).toHaveTextContent("Цель: самая низкая расценка");
    expect(screen.getByRole("button", { name: TARGET_BACK_TO_LOWEST })).toBeInTheDocument();
  });

  it("у строки не сравнимой цели нет: строки цели и кнопок нет", async () => {
    serve(() =>
      workLineViewFixture({
        line: { state: "no_history", no_history_reason: "no_family", target_kind: null, freshest: null, lowest: null },
        analogs: [],
      })
    );
    renderSheet();
    await screen.findByTestId("sheet-state");
    expect(screen.queryByTestId("target-row")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: TARGET_MAKE_FRESHEST })).not.toBeInTheDocument();
  });
});

describe("«Для выгрузки»: сохранение цели", () => {
  it("«Сделать целью самую свежую»: PUT с kind freshest и рядом ИПЦ экрана", async () => {
    serve(() => workLineViewFixture());
    renderSheet({ seriesId: 3 });
    await userEvent.click(await screen.findByRole("button", { name: TARGET_MAKE_FRESHEST }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    const request = targets()[0];
    expect(request.method).toBe("PUT");
    expect(request.body).toEqual({ kind: "freshest" });
    expect(new URL(request.url).searchParams.get("inflation_series_id")).toBe("3");
  });

  it("без ряда ИПЦ параметр в адрес не попадает", async () => {
    serve(() => workLineViewFixture());
    renderSheet({ seriesId: null });
    await userEvent.click(await screen.findByRole("button", { name: TARGET_MAKE_FRESHEST }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(new URL(targets()[0].url).searchParams.has("inflation_series_id")).toBe(false);
  });

  it("«сделать целью» у аналога: PUT с kind analog и его analog_id", async () => {
    serve(() => workLineViewFixture());
    renderSheet({ seriesId: 3 });
    await screen.findByTestId("analogs-table");
    await userEvent.click(within(screen.getByTestId("analog-8002")).getByRole("button", { name: "сделать целью" }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({ kind: "analog", analog_id: 8002 });
  });

  it("«Вернуть к самой низкой»: DELETE .../target", async () => {
    serve(() => workLineViewFixture({ line: { target_kind: "freshest", choice: choice() } }));
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: TARGET_BACK_TO_LOWEST }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].method).toBe("DELETE");
  });

  it("после сохранения панель перезапрошена и показывает новую цель", async () => {
    let saved = false;
    const asked = serve(() =>
      saved ? workLineViewFixture({ line: { target_kind: "freshest", choice: choice() } }) : workLineViewFixture()
    );
    server.use(
      http.put(`${LINE_PATH}/target`, () => {
        saved = true;
        return new HttpResponse(null, { status: 204 });
      })
    );
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: TARGET_MAKE_FRESHEST }));
    await waitFor(() => expect(screen.getByTestId("target-row")).toHaveTextContent("Цель: самая свежая расценка"));
    expect(asked.length).toBeGreaterThanOrEqual(2);
  });

  it("пока цель сохраняется — серый слой с подписью сохранения над старыми цифрами", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    serve(() => workLineViewFixture());
    server.use(
      http.put(`${LINE_PATH}/target`, async () => {
        await hold;
        return new HttpResponse(null, { status: 204 });
      })
    );
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: TARGET_MAKE_FRESHEST }));
    const overlay = await screen.findByTestId("recalc-overlay");
    expect(overlay).toHaveTextContent(SAVING_LABEL);
    expect(screen.getByTestId("figure-lowest")).toHaveTextContent("1 000,00");
    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
  });

  it("422 analog_unavailable: тост «аналог больше не в остатке» и перезапрос панели", async () => {
    handlerState.kpFileDecisionOutcome = "analog_unavailable";
    const asked = serve(() => workLineViewFixture());
    renderSheet({ seriesId: 3 });
    await screen.findByTestId("analogs-table");
    const before = asked.length;
    await userEvent.click(within(screen.getByTestId("analog-8002")).getByRole("button", { name: "сделать целью" }));
    expect(await screen.findByText(ANALOG_UNAVAILABLE_TOAST)).toBeInTheDocument();
    await waitFor(() => expect(asked.length).toBeGreaterThan(before));
  });
});

describe("деньги панели работы — строками, без арифметики", () => {
  it("копеечный хвост цены печатается по правилам форматтера, а не по Number", async () => {
    serve(() =>
      workLineViewFixture({
        line: {
          lowest: {
            value: pair("1234.56789012345678901234", "1506.17"),
            analog_id: 8001,
            contract_id: 101,
            from_all_classes: false,
          },
          to_lowest: pair("0.004", "0.005"),
        },
      })
    );
    renderSheet();
    const low = await screen.findByTestId("figure-lowest");
    expect(low).toHaveTextContent("1 234,57");
  });
});

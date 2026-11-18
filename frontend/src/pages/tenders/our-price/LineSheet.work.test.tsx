import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  agreedFixture,
  articleRefFixture,
  chapterPathFixture,
  compositeLineViewFixture,
  contractorFigureFixture,
  KP_FILE_AUTHOR,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceAnalogFixture,
  ourPriceLineFixture,
  ourPriceLineViewFixture,
  ourPriceParamFixture,
  workLineViewFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { ANALOG_UNAVAILABLE_TOAST } from "@/services/queries";
import type { MoneyPair, OurPriceLine, OurPriceLineView, TargetChoice } from "@/types/domain";

import { LineSheet, type LineSheetProps } from "./LineSheet";
import { PARAMS_COPY } from "./paramsCopy";
import { REF_SEARCH, SAVING_LABEL, SHEET_SECTION, UNIT_WARN_ALL_OUTLIERS, zoneQueueHref } from "./ourPriceCopy";

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
    expect(await screen.findByTestId("sheet-comparison")).toHaveTextContent("без учёта: марка W, толщина · другой класс");
  });

  it("две карточки: цена, объект и договор, дата цены, разница КП к ней со знаком", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const low = await screen.findByTestId("figure-lowest");
    expect(low).toHaveTextContent("самая низкая");
    expect(low).toHaveTextContent("1 000,00");
    expect(low).toHaveTextContent("Объект Ж");
    expect(low).toHaveTextContent("ГП-08");
    expect(low).toHaveTextContent("03.2025");
    expect(low).toHaveTextContent("КП выше");
    expect(low).toHaveTextContent("+20 000,00");
    const fresh = screen.getByTestId("figure-freshest");
    expect(fresh).toHaveTextContent("самая свежая");
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
    await screen.findByTestId("analogs-table");
    expect(within(screen.getByTestId("analog-8001")).getByText("самая низкая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8002")).getByText("самая свежая")).toBeInTheDocument();
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

describe("панель работы: ширина и порядок секций", () => {
  it("ширина панели 680 px вместо базовой lg", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    await screen.findByTestId("sheet-comparison");
    const sheet = screen.getByTestId("line-sheet");
    expect(sheet.className).toContain("max-w-[680px]");
    expect(sheet.className).not.toContain("max-w-lg");
  });

  const titles = () =>
    within(screen.getByTestId("line-sheet"))
      .getAllByRole("heading", { level: 4 })
      .map((h) => h.textContent);

  it("работа: где строка, цены, работа, сравнение, аналоги, поиск по договорам, в файл подрядчику", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    await screen.findByTestId("sheet-comparison");
    expect(titles()).toEqual([
      SHEET_SECTION.where,
      SHEET_SECTION.prices,
      PARAMS_COPY.sectionTitle,
      SHEET_SECTION.comparison,
      SHEET_SECTION.analogs,
      REF_SEARCH.title,
      SHEET_SECTION.export,
    ]);
  });

  it("сборная строка: где строка, цены, работа, сравнение, по частям, поиск по договорам, в файл подрядчику", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet();
    await screen.findByTestId("composite-no-target");
    expect(titles()).toEqual([
      SHEET_SECTION.where,
      SHEET_SECTION.prices,
      PARAMS_COPY.sectionTitle,
      SHEET_SECTION.comparison,
      SHEET_SECTION.parts,
      REF_SEARCH.title,
      SHEET_SECTION.export,
    ]);
  });
});

describe("панель работы: «где строка»", () => {
  it("строка КП: цепочка статей и полный путь по смете КП с номерами", async () => {
    serve(() =>
      workLineViewFixture({
        line: { chapter_path: chapterPathFixture("Корпус 2", "Плиты", "Перекрытия") },
      })
    );
    renderSheet();
    const where = await screen.findByTestId("sheet-where");
    expect(within(where).getByTestId("where-article")).toHaveTextContent("4 Несущие конструкции");
    expect(within(where).getByTestId("where-article")).toHaveTextContent("4.2.4 Вертикальные конструкции");
    expect(within(where).getByTestId("where-path")).toHaveTextContent("п. 4 — Корпус 2");
    expect(within(where).getByTestId("where-path")).toHaveTextContent("п. 4.1.2 — Перекрытия");
    expect(where).toHaveTextContent("в смете КП");
  });

  it("карточка аналога из другой статьи показывает свою статью и свой путь, а не статью строки КП", async () => {
    const other = articleRefFixture({
      id: 32,
      code: "4.2.2",
      title: "Первый этаж",
      chain: [
        { id: 11, code: "4", title: "Несущие конструкции" },
        { id: 32, code: "4.2.2", title: "Первый этаж" },
      ],
    });
    serve(() =>
      workLineViewFixture({
        analogs: [
          ourPriceAnalogFixture({ analog_id: 8001, is_lowest: true }),
          ourPriceAnalogFixture({
            analog_id: 8002,
            article: other,
            chapter_path: chapterPathFixture("Смета договора", "Корпус 3"),
          }),
        ],
      })
    );
    renderSheet();
    await screen.findByTestId("analog-8002");
    const mine = within(screen.getByTestId("analog-8002")).getByTestId("line-where");
    expect(mine).toHaveTextContent("4.2.2 Первый этаж");
    expect(mine).not.toHaveTextContent("4.2.4");
    expect(mine).toHaveTextContent("в смете договора");
    expect(mine).toHaveTextContent("п. 4.1 — Корпус 3");
    const same = within(screen.getByTestId("analog-8001")).getByTestId("line-where");
    expect(same).toHaveTextContent("4.2.4 Вертикальные конструкции");
    expect(within(screen.getByTestId("sheet-where")).getByTestId("where-article")).not.toHaveTextContent("4.2.2");
  });

  it("пути нет и статьи нет: блок «где строка» не рисуется, панель открывается", async () => {
    serve(() =>
      workLineViewFixture({
        line: { article: null, chapter_path: [] },
        analogs: [ourPriceAnalogFixture({ analog_id: 8001, article: null, chapter_path: [] })],
      })
    );
    renderSheet();
    expect(await screen.findByTestId("sheet-comparison")).toBeInTheDocument();
    expect(screen.queryByTestId("sheet-where")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8001")).queryByTestId("line-where")).not.toBeInTheDocument();
  });
});

describe("панель работы: не больше пяти аналогов", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      ourPriceAnalogFixture({
        analog_id: 9000 + i,
        contract_id: 200 + i,
        object_title: `Объект ${i}`,
        price: pair(String(1000 + i * 10), String(1220 + i * 10)),
      })
    );
  const shownIds = () =>
    screen.getAllByTestId(/^analog-\d+$/).map((r) => Number(r.getAttribute("data-testid")!.slice(7)));

  it("девять аналогов: пять карточек и «ещё 4 аналога из 9»", async () => {
    serve(() => workLineViewFixture({ analogs: many(9) }));
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(shownIds()).toHaveLength(5);
    expect(screen.getByRole("button", { name: "ещё 4 аналога из 9" })).toBeInTheDocument();
  });

  it("сначала аналоги с метками цели (низкая, свежая, с подрядчиком), затем по цене", async () => {
    const list = many(9);
    list[7] = { ...list[7], is_lowest: true, price: pair("5000", "6100") };
    list[8] = { ...list[8], is_freshest: true, price: pair("6000", "7320") };
    serve(() =>
      workLineViewFixture({
        line: {
          lowest: { value: pair("5000", "6100"), analog_id: 9007, contract_id: 207, from_all_classes: false },
          freshest: { value: pair("6000", "7320"), analog_id: 9008, contract_id: 208, period: "2026-08" },
          contractor: contractorFigureFixture({ analog_id: 9006, contract_id: 206 }),
        },
        analogs: list,
      })
    );
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(shownIds()).toEqual([9007, 9008, 9006, 9000, 9001]);
  });

  it("закреплённый аналог из хвоста после выбора попадает в пятёрку, а кнопки у него нет", async () => {
    serve(() =>
      workLineViewFixture({
        line: { target_kind: "analog", choice: choice({ kind: "analog", analog_id: 9008 }) },
        analogs: many(9),
      })
    );
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(shownIds()[0]).toBe(9008);
    expect(shownIds()).toHaveLength(5);
    expect(within(screen.getByTestId("analog-9008")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("analog-9008")).getByText("цель")).toBeInTheDocument();
  });

  it("«ещё N» раскрывает остальные, «свернуть» возвращает пять", async () => {
    serve(() => workLineViewFixture({ analogs: many(7) }));
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: "ещё 2 аналога из 7" }));
    expect(shownIds()).toHaveLength(7);
    await userEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(shownIds()).toHaveLength(5);
  });
});

describe("карточки цели в панели", () => {
  it("по умолчанию — самая низкая (авто); у admin кнопки на карточках", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    const low = await screen.findByTestId("figure-lowest");
    expect(low).toHaveAttribute("data-selected", "true");
    expect(low).toHaveTextContent("цель · авто");
    expect(within(screen.getByTestId("figure-freshest")).getByRole("button")).toBeEnabled();
  });

  it("не admin: карточки с цифрами, ни кнопок на них, ни «сделать целью» у аналогов", async () => {
    serve(() => workLineViewFixture());
    renderSheet({}, MEMBER);
    expect(await screen.findByTestId("figure-lowest")).toHaveAttribute("data-selected", "true");
    expect(within(screen.getByTestId("figure-lowest")).queryByRole("button")).not.toBeInTheDocument();
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

  it("«сделать целью» есть и у аналога, на котором уже стоит метка стратегии", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    await screen.findByTestId("analogs-table");
    expect(within(screen.getByTestId("analog-8001")).getByText("самая низкая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-8001")).getByRole("button", { name: "сделать целью" })).toBeInTheDocument();
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
    expect(within(screen.getByTestId("figure-freshest")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("цель — аналог: карточка «выбранный аналог», у аналога метка «цель» вместо кнопки", async () => {
    serve(() =>
      workLineViewFixture({ line: { target_kind: "analog", choice: choice({ kind: "analog", analog_id: 8002 }) } })
    );
    renderSheet();
    expect(await screen.findByTestId("figure-pinned")).toHaveTextContent("выбранный аналог");
    const mine = screen.getByTestId("analog-8002");
    expect(within(mine).queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
    expect(within(mine).getByText("цель")).toBeInTheDocument();
  });

  it("цель — стратегия: у аналога, на которого она сейчас указывает, метка «цель», а кнопка остаётся", async () => {
    serve(() => workLineViewFixture({ line: { target_kind: "freshest", choice: choice() } }));
    renderSheet();
    await screen.findByTestId("analogs-table");
    const fresh = screen.getByTestId("analog-8002");
    expect(within(fresh).getByText("цель")).toBeInTheDocument();
    expect(within(fresh).getByRole("button", { name: "сделать целью" })).toBeInTheDocument();
  });

  it("выбор не действует: плашка с причиной, самая низкая нажата", async () => {
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
    expect(screen.getByTestId("figure-lowest")).toHaveAttribute("data-selected", "true");
  });

  it("у строки без сравнения карточек и кнопок нет", async () => {
    serve(() =>
      workLineViewFixture({
        line: { state: "no_history", no_history_reason: "no_family", target_kind: null, freshest: null, lowest: null },
        analogs: [],
      })
    );
    renderSheet();
    await screen.findByTestId("sheet-state");
    expect(screen.queryByTestId("figure-lowest")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
  });
});

describe("«В файл подрядчику»: сохранение цели", () => {
  it("без ряда ИПЦ параметр в адрес не попадает", async () => {
    serve(() => workLineViewFixture());
    renderSheet({ seriesId: null });
    await userEvent.click(within(await screen.findByTestId("figure-freshest")).getByRole("button"));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(new URL(targets()[0].url).searchParams.has("inflation_series_id")).toBe(false);
  });

  it("«сделать целью» у аналога: PUT с kind analog и его analog_id, рядом ИПЦ экрана", async () => {
    serve(() => workLineViewFixture());
    renderSheet({ seriesId: 3 });
    await screen.findByTestId("analogs-table");
    await userEvent.click(within(screen.getByTestId("analog-8002")).getByRole("button", { name: "сделать целью" }));
    await waitFor(() => expect(targets()).toHaveLength(1));
    expect(targets()[0].body).toEqual({ kind: "analog", analog_id: 8002 });
    expect(new URL(targets()[0].url).searchParams.get("inflation_series_id")).toBe("3");
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
    await userEvent.click(within(await screen.findByTestId("figure-freshest")).getByRole("button"));
    await waitFor(() => expect(screen.getByTestId("figure-freshest")).toHaveAttribute("data-selected", "true"));
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
    await userEvent.click(within(await screen.findByTestId("figure-freshest")).getByRole("button"));
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

describe("сборная строка", () => {
  it("состояние: нет аналога, в строке несколько значений параметра с его именем", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet();
    expect(await screen.findByTestId("sheet-state")).toHaveTextContent(
      "нет аналога: в строке несколько значений параметра «Тип конструкции»"
    );
  });

  it("пояснение «цели нет» с именем параметра; карточек цели нет", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet();
    const why = await screen.findByTestId("composite-no-target");
    expect(why).toHaveTextContent("Цели нет");
    expect(why).toHaveTextContent("Тип конструкции");
    expect(screen.queryByTestId("figure-lowest")).not.toBeInTheDocument();
  });

  it("«по частям»: части со своими цифрами, без кнопок цели", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet();
    const parts = await screen.findByTestId("parts-reference");
    expect(within(parts).getByTestId("part-501")).toHaveTextContent("36 200,12");
    expect(within(parts).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
  });

  it("«в файл подрядчику»: число «—», «в файл ничего не идёт», шаблона комментария нет", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet();
    const block = await screen.findByTestId("export-block");
    expect(within(block).getByTestId("export-ours")).toHaveTextContent("—");
    expect(within(block).getByTestId("export-source")).toHaveTextContent("в файл ничего не идёт");
    expect(within(block).getByLabelText("Комментарий для подрядчика")).toHaveValue("");
  });

  it("«Договорились»: число уходит в файл, источник — «договорились · вписано вручную»", async () => {
    serve(() =>
      compositeLineViewFixture({
        line: { agreed: agreedFixture({ net: "43000.00", shown: "52460.00" }), target_kind: "agreed" },
      })
    );
    renderSheet();
    const block = await screen.findByTestId("export-block");
    expect(within(block).getByTestId("export-ours")).toHaveTextContent("43 000,00");
    expect(within(block).getByTestId("export-source")).toHaveTextContent("договорились · вписано вручную");
    expect(within(block).getByTestId("export-source")).not.toHaveTextContent("перекрывает цель");
  });

  it("сборная строка в виде «с НДС»: справка по частям берёт shown", async () => {
    serve(() => compositeLineViewFixture());
    renderSheet({ vat: "shown", vatRate: "22.00" });
    expect(await screen.findByTestId("part-501")).toHaveTextContent("44 164,15");
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

describe("панель строки: параметры варианта", () => {
  const withParams = (line: Partial<OurPriceLine>): OurPriceLineView => {
    const base = ourPriceLineViewFixture();
    return { ...base, line: { ...base.line, ...line } };
  };

  it("подзаголовок — семья, без склейки со значениями и подписью варианта", async () => {
    serve(() => withParams({ family: "Семья Т", variant_label: "Семья Т · вариант 1" }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.getByText("Семья Т")).toBeInTheDocument();
    expect(screen.queryByText(/вариант 1/)).not.toBeInTheDocument();
  });

  it("без семьи — прежняя подпись панели", async () => {
    serve(() => withParams({ family: null, variant_label: null, params: [] }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.getByText("Сравнение строки КП с нашими договорами")).toBeInTheDocument();
  });

  it("в «Ценах» нет строки «Второстепенные»", async () => {
    serve(() => withParams({ params: [ourPriceParamFixture({ name: "Цвет", role: "secondary", value: "серый" })] }));
    renderSheet();
    expect(await screen.findByTestId("sheet-prices")).not.toHaveTextContent("Второстепенные");
  });

  it("раздел «Работа» стоит сразу после «Цен», работа и система показывают его при непустом params", async () => {
    serve(() => withParams({ params: [ourPriceParamFixture({ name: "Параметр Т", value: "значение Т" })] }));
    renderSheet();
    const prices = await screen.findByTestId("sheet-prices");
    const work = screen.getByTestId("sheet-work-params");
    expect(prices.nextElementSibling).toBe(work);
    expect(work).toHaveTextContent("Параметр Т");
    expect(work).toHaveTextContent("значение Т");
  });

  it("params пуст — раздела «Работа» нет", async () => {
    serve(() => withParams({ params: [] }));
    renderSheet();
    await screen.findByTestId("sheet-prices");
    expect(screen.queryByTestId("sheet-work-params")).not.toBeInTheDocument();
  });

  it("основание catalog доходит до заголовка колонки «При подборе»", async () => {
    serve(() => withParams({ params_basis: "catalog", params: [ourPriceParamFixture({ match: "ignored" })] }));
    renderSheet();
    const work = await screen.findByTestId("sheet-work-params");
    expect(work).toHaveTextContent("(аналогов нет — настройка каталога)");
    expect(work).toHaveTextContent("можно не учитывать");
  });

  it("колонки аналогов: имя из analog_columns, значение и подсветка у аналога", async () => {
    serve(() => {
      const view = workLineViewFixture({
        line: { work_rung: { no: 2, released: ["Марка W"], other_class: false } },
        analogs: [
          ourPriceAnalogFixture({
            analog_id: 8001,
            released_values: [{ value: "W8", conflict: false, differs: true }],
          }),
        ],
      });
      return { ...view, analog_columns: [{ name: "Марка W" }] };
    });
    renderSheet();
    const card = await screen.findByTestId("analog-released-8001");
    expect(card).toHaveTextContent("Марка W");
    const value = within(card).getByTestId("analog-released-value");
    expect(value).toHaveTextContent("W8");
    expect(value).toHaveAttribute("data-differs", "true");
  });

  it("analog_columns пуст — у аналогов колонок нет", async () => {
    serve(() => workLineViewFixture());
    renderSheet();
    await screen.findByTestId("analog-8001");
    expect(screen.queryByTestId("analog-released-8001")).not.toBeInTheDocument();
  });
});

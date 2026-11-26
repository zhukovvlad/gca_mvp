import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { describe, expect, it, vi } from "vitest";

import { qk } from "@/services/queryKeys";
import {
  analogSuspicionFixture,
  excludedAnalogFixture,
  notErrorDecisionOutlier,
  ourPriceAnalogFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { LineIds, OurPriceAnalog, ZoneScope } from "@/types/domain";

import { AnalogsTable, type AnalogsTableProps } from "./AnalogsTable";
import { ExcludedAnalogs } from "./ExcludedAnalogs";

const many = (n: number): OurPriceAnalog[] =>
  Array.from({ length: n }, (_, i) =>
    ourPriceAnalogFixture({
      analog_id: 9000 + i,
      contract_id: 200 + i,
      title: `Строка ${i}`,
      object_title: `Объект ${i}`,
      price: { net: String(1000 + i), shown: String(1220 + i) },
    })
  );

const IDS: LineIds = { tenderId: 6, roundId: 2, offerId: 64, kpItemId: 5001 };
const FORM = "«Двутавр 20Б1», т";

function renderTable(props: Partial<AnalogsTableProps> = {}) {
  return renderWithProviders(
    <AnalogsTable
      analogs={props.analogs ?? many(2)}
      vat={props.vat ?? "net"}
      canChoose={props.canChoose ?? true}
      canLump={props.canLump}
      targetAnalogId={props.targetAnalogId ?? null}
      pinnedAnalogId={props.pinnedAnalogId ?? null}
      contractorAnalogId={props.contractorAnalogId ?? null}
      busy={props.busy ?? false}
      onChoose={props.onChoose ?? (() => undefined)}
      columns={props.columns ?? []}
      zoneScope={props.zoneScope ?? "own"}
      ids={IDS}
      formLabel={FORM}
    />
  );
}

const ids = () => screen.getAllByTestId(/^analog-\d+$/).map((r) => Number(r.getAttribute("data-testid")!.slice(7)));

describe("справка аналогов", () => {
  it("первые пять, остальные за «ещё N аналога из M»", () => {
    renderTable({ analogs: many(8) });
    expect(ids()).toEqual([9000, 9001, 9002, 9003, 9004]);
    fireEvent.click(screen.getByRole("button", { name: "ещё 3 аналога из 8" }));
    expect(ids()).toHaveLength(8);
    expect(ids().slice(5)).toEqual([9005, 9006, 9007]);
    fireEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(ids()).toHaveLength(5);
  });

  it("склонение: 1 аналог, 2 аналога, 5 аналогов", () => {
    const one = renderTable({ analogs: many(6) });
    expect(screen.getByRole("button", { name: "ещё 1 аналог из 6" })).toBeInTheDocument();
    one.unmount();
    const five = renderTable({ analogs: many(10) });
    expect(screen.getByRole("button", { name: "ещё 5 аналогов из 10" })).toBeInTheDocument();
    five.unmount();
  });

  it("область не own: у каждого аналога метка его зоны, у аналога без зоны — «зона: не определена»", () => {
    const list = [
      ourPriceAnalogFixture({ analog_id: 1, zone: "aboveground" }),
      ourPriceAnalogFixture({ analog_id: 2, zone: "whole" }),
      ourPriceAnalogFixture({ analog_id: 3, zone: null }),
    ];
    for (const scope of ["other", "any"] as ZoneScope[]) {
      const view = renderTable({ analogs: list, zoneScope: scope });
      expect(within(screen.getByTestId("analog-1")).getByText("зона: Надземная")).toBeInTheDocument();
      expect(within(screen.getByTestId("analog-2")).getByText("зона: Весь объект")).toBeInTheDocument();
      expect(within(screen.getByTestId("analog-3")).getByText("зона: не определена")).toBeInTheDocument();
      view.unmount();
    }
  });

  it("область own: меток зоны нет, даже если зона аналога известна", () => {
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 1, zone: "aboveground" }), ourPriceAnalogFixture({ analog_id: 2, zone: null })],
      zoneScope: "own",
    });
    expect(screen.queryByText(/^зона:/)).not.toBeInTheDocument();
  });

  describe("«это комплект — убрать из сравнения»", () => {
    const PATH = "/api/v1/position-items/:id/lump-sum";
    const ACTION = "это комплект — убрать из сравнения";

    function serveLump() {
      const asked: Array<{ method: string; id: string; body: unknown }> = [];
      server.use(
        http.put(PATH, async ({ request, params }) => {
          asked.push({ method: "PUT", id: String(params.id), body: await request.json() });
          return HttpResponse.json({});
        })
      );
      return asked;
    }

    it("admin: меню у каждого аналога, в нём три пункта; у member меню нет", async () => {
      const admin = renderTable({ canLump: true });
      expect(screen.getAllByRole("button", { name: "Действия со строкой договора" })).toHaveLength(2);
      await userEvent.click(screen.getByTestId("analog-menu-9000"));
      const items = (await screen.findAllByRole("menuitem")).map((i) => i.textContent);
      expect(items).toEqual([ACTION, "Не аналог в этом тендере…", "Ошибка в цене — не аналог нигде…"]);
      admin.unmount();
      renderTable({ canLump: false });
      expect(screen.queryByRole("button", { name: "Действия со строкой договора" })).not.toBeInTheDocument();
      expect(screen.queryByTestId("analog-menu-9000")).not.toBeInTheDocument();
    });

    it("подтверждение называет строку договора и не шлёт запрос до «да»; «Отмена» ничего не шлёт", async () => {
      const asked = serveLump();
      renderTable({ canLump: true, analogs: [ourPriceAnalogFixture({ analog_id: 9100, title: "Монтаж вентиляции" })] });
      await userEvent.click(screen.getByTestId("analog-menu-9100"));
      await userEvent.click(await screen.findByTestId("analog-lump-9100"));
      expect(await screen.findByTestId("analog-lump-confirm")).toHaveTextContent(
        "Строка договора «Монтаж вентиляции» перестанет служить аналогом во всех КП. Вернуть — в «Найти в договорах»."
      );
      expect(asked).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
      await waitFor(() => expect(screen.queryByTestId("analog-lump-confirm")).not.toBeInTheDocument());
      expect(asked).toHaveLength(0);
    });

    it("после подтверждения: PUT verdict=lump по analog_id и сброс ourPriceAll", async () => {
      const asked = serveLump();
      const queryClient = createTestQueryClient();
      const spy = vi.spyOn(queryClient, "invalidateQueries");
      renderWithProviders(
        <AnalogsTable
          analogs={[ourPriceAnalogFixture({ analog_id: 9100 }), ourPriceAnalogFixture({ analog_id: 9101 })]}
          vat="net"
          canChoose
          canLump
          targetAnalogId={null}
          pinnedAnalogId={null}
          contractorAnalogId={null}
          busy={false}
          onChoose={() => undefined}
          columns={[]}
          zoneScope="own"
          ids={IDS}
          formLabel={FORM}
        />,
        { queryClient }
      );
      await userEvent.click(screen.getByTestId("analog-menu-9101"));
      await userEvent.click(await screen.findByTestId("analog-lump-9101"));
      await userEvent.click(await screen.findByTestId("analog-lump-confirm-ok"));
      await waitFor(() => expect(asked).toHaveLength(1));
      expect(asked[0]).toEqual({ method: "PUT", id: "9101", body: { verdict: "lump" } });
      await waitFor(() => {
        const keys = spy.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
        expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
      });
    });
  });

  describe("отметки «не аналог»", () => {
    const NOT_ANALOG = "/api/v1/tenders/6/rounds/2/our-price/offers/64/lines/5001/not-analog/:positionItemId";
    const PRICE_ERROR = "/api/v1/position-items/:id/price-error";
    const row = ourPriceAnalogFixture({ analog_id: 9200, title: "Монтаж кровли", object_title: "TOP TOWER" });

    function serveMarks(delay?: Promise<void>) {
      const asked: Array<{ url: string; body: unknown }> = [];
      const handler = (path: string) =>
        http.put(path, async ({ request }) => {
          asked.push({ url: new URL(request.url).pathname, body: await request.json() });
          if (delay) await delay;
          return HttpResponse.json({});
        });
      server.use(handler(NOT_ANALOG), handler(PRICE_ERROR));
      return asked;
    }

    async function openItem(testId: string) {
      await userEvent.click(screen.getByTestId("analog-menu-9200"));
      await userEvent.click(await screen.findByTestId(testId));
    }

    it("тендерная: окно называет строку договора, строку формы и тендер; причина обязательна; PUT по строке КП", async () => {
      const asked = serveMarks();
      const queryClient = createTestQueryClient();
      const spy = vi.spyOn(queryClient, "invalidateQueries");
      renderWithProviders(
        <AnalogsTable
          analogs={[row]}
          vat="net"
          canChoose={false}
          canLump
          targetAnalogId={null}
          pinnedAnalogId={null}
          contractorAnalogId={null}
          busy={false}
          onChoose={() => undefined}
          columns={[]}
          zoneScope="own"
          ids={IDS}
          formLabel={FORM}
        />,
        { queryClient }
      );
      await openItem("analog-mark-tender-9200");
      expect(await screen.findByTestId("mark-scope")).toHaveTextContent(
        "Строка «Монтаж кровли» договора TOP TOWER не будет аналогом для строки формы «Двутавр 20Б1», т у всех участников и во всех этапах тендера 6. В других тендерах она остаётся аналогом."
      );
      expect(screen.getByRole("button", { name: "Исключить" })).toBeDisabled();
      expect(asked).toHaveLength(0);
      await userEvent.type(screen.getByLabelText("Причина"), "в цене огнезащита");
      await userEvent.click(screen.getByRole("button", { name: "Исключить" }));
      await waitFor(() => expect(asked).toHaveLength(1));
      expect(asked[0]).toEqual({
        url: "/api/v1/tenders/6/rounds/2/our-price/offers/64/lines/5001/not-analog/9200",
        body: { reason: "в цене огнезащита" },
      });
      await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
      const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
      expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
      expect(keys).toContain(JSON.stringify(qk.tenders.kpFilePreviewsAll));
    });

    it("«ошибка в цене»: окно про все тендеры и обычную ставку; PUT по самой строке договора", async () => {
      const asked = serveMarks();
      renderTable({ canLump: true, analogs: [row] });
      await openItem("analog-mark-price-error-9200");
      expect(await screen.findByTestId("mark-scope")).toHaveTextContent(
        "Строка «Монтаж кровли» договора TOP TOWER перестанет быть аналогом во всех тендерах и выпадет из обычной ставки семьи. Отметку можно снять."
      );
      await userEvent.type(screen.getByLabelText("Причина"), "цена за м²");
      await userEvent.click(screen.getByRole("button", { name: "Исключить" }));
      await waitFor(() => expect(asked).toHaveLength(1));
      expect(asked[0]).toEqual({ url: "/api/v1/position-items/9200/price-error", body: { reason: "цена за м²" } });
    });

    it("занято до ответа: пока запрос не вернулся, «Исключить» погашено", async () => {
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      const asked = serveMarks(gate);
      renderTable({ canLump: true, analogs: [row] });
      await openItem("analog-mark-price-error-9200");
      await userEvent.type(await screen.findByLabelText("Причина"), "причина");
      await userEvent.click(screen.getByRole("button", { name: "Исключить" }));
      await waitFor(() => expect(asked).toHaveLength(1));
      expect(screen.getByRole("button", { name: "Исключить" })).toBeDisabled();
      release();
      await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
    });

    it("«Отмена» в окне отметки ничего не шлёт", async () => {
      const asked = serveMarks();
      renderTable({ canLump: true, analogs: [row] });
      await openItem("analog-mark-tender-9200");
      await userEvent.type(await screen.findByLabelText("Причина"), "причина");
      await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
      await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
      expect(asked).toHaveLength(0);
    });
  });

  it("пять и меньше: кнопки «ещё» нет", () => {
    renderTable({ analogs: many(5) });
    expect(screen.queryByRole("button", { name: /ещё/ })).not.toBeInTheDocument();
  });

  it("порядок: аналоги с метками цели — первыми (низкая, свежая, с подрядчиком), остальные — по цене от низкой", () => {
    const list = many(8);
    list[6] = { ...list[6], is_freshest: true, price: { net: "5000", shown: "6100" } };
    list[7] = { ...list[7], price: { net: "900", shown: "1098" } };
    renderTable({ analogs: list, contractorAnalogId: 9005 });
    // свежая (9006, дорогая) идёт раньше «с подрядчиком» (9005, дешёвая): порядок меток, а не цены;
    // дальше без меток — 9007 за 900, затем 9000 и 9001
    expect(ids()).toEqual([9006, 9005, 9007, 9000, 9001]);
  });

  it("порядок по цене сравнивает строки как числа: 900 ниже 1000, хотя «9» больше «1»", () => {
    const list = [
      ourPriceAnalogFixture({ analog_id: 1, price: { net: "1000.50", shown: null } }),
      ourPriceAnalogFixture({ analog_id: 2, price: { net: "900.25", shown: null } }),
    ];
    renderTable({ analogs: list });
    expect(ids()).toEqual([2, 1]);
  });

  it("метки: самая низкая, самая свежая, с этим подрядчиком — у своих аналогов", () => {
    renderTable({
      analogs: [
        ourPriceAnalogFixture({ analog_id: 1, is_lowest: true }),
        ourPriceAnalogFixture({ analog_id: 2, is_freshest: true }),
        ourPriceAnalogFixture({ analog_id: 3 }),
      ],
      contractorAnalogId: 3,
    });
    expect(within(screen.getByTestId("analog-1")).getByText("самая низкая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-2")).getByText("самая свежая")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-3")).getByText("с этим подрядчиком")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-1")).queryByText("самая свежая")).not.toBeInTheDocument();
  });

  it("строка: объект, договор, подрядчик, название строки договора, дата, цена", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1, price: { net: "66800", shown: "81496" } })] });
    const row = screen.getByTestId("analog-1");
    expect(row).toHaveTextContent("Объект Ж");
    expect(row).toHaveTextContent("ГП-08");
    expect(row).toHaveTextContent("Подрядчик А");
    expect(row).toHaveTextContent("Стены монолитные B40, 200 мм");
    expect(row).toHaveTextContent("03.2025");
    expect(row).toHaveTextContent("66 800,00");
  });

  it("у карточки аналога — «где строка»: статья его раздела и путь по смете договора", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1 })] });
    const where = within(screen.getByTestId("analog-1")).getByTestId("line-where");
    expect(where).toHaveTextContent("4.2.4 Вертикальные конструкции");
    expect(within(where).getByText("статья классификатора")).toBeInTheDocument();
    expect(where).toHaveTextContent("в смете договора");
    expect(where).toHaveTextContent("п. 4 — Корпус 1");
  });

  it("вид «с НДС»: цена из shown", () => {
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 1, price: { net: "100", shown: "122" } })],
      vat: "shown",
      canChoose: false,
    });
    expect(screen.getByTestId("analog-1")).toHaveTextContent("122,00");
  });

  it("даты цены нет: прочерк", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1, period: null })] });
    expect(within(screen.getByTestId("analog-1")).getByTestId("analog-date-1")).toHaveTextContent("—");
  });

  it("с ИПЦ цена приведена, а номинал в подсказке", () => {
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 1, price: { net: "120", shown: null }, nominal: { net: "100", shown: null } })],
    });
    expect(screen.getByTestId("analog-price-1")).toHaveAttribute("title", expect.stringContaining("100,00"));
  });

  it("договор с этим подрядчиком — тихая подпись у карточки, отдельно от метки цели", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1, with_this_contractor: true })] });
    expect(within(screen.getByTestId("analog-1")).getByText("договор с этим подрядчиком")).toBeInTheDocument();
    expect(within(screen.getByTestId("analog-1")).queryByText("с этим подрядчиком")).not.toBeInTheDocument();
  });

  it("выброс: строка выделена, метка кратности, «сделать целью» нет", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1, outlier_ratio: "0.1", selectable: false })] });
    const row = screen.getByTestId("analog-1");
    expect(row).toHaveAttribute("data-outlier", "true");
    expect(row).toHaveTextContent("в 10 раз меньше остальных — проверьте единицу учёта");
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });

  it("«сделать целью»: у selectable только для canChoose; клик отдаёт analog_id", () => {
    const onChoose = vi.fn();
    const { unmount } = renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], onChoose });
    fireEvent.click(screen.getByRole("button", { name: "сделать целью" }));
    expect(onChoose).toHaveBeenCalledWith(7);
    unmount();
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], canChoose: false });
    expect(screen.queryByRole("button", { name: "сделать целью" })).not.toBeInTheDocument();
  });

  it("пока цель сохраняется, кнопки выбора заблокированы", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], busy: true });
    expect(screen.getByRole("button", { name: "сделать целью" })).toBeDisabled();
  });

  it("закреплённый аналог: метка «цель» вместо кнопки", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], targetAnalogId: 7, pinnedAnalogId: 7 });
    const row = screen.getByTestId("analog-7");
    expect(within(row).getByText("цель")).toBeInTheDocument();
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });

  it("цель — стратегия, указывающая на аналог: метка «цель», кнопка закрепить остаётся", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], targetAnalogId: 7, pinnedAnalogId: null });
    const row = screen.getByTestId("analog-7");
    expect(within(row).getByText("цель")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "сделать целью" })).toBeInTheDocument();
  });

  it("раскладка: числа моноширинные, название строки с переносом, как в карточках экрана", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1 })] });
    expect(screen.getByTestId("analog-price-1").className).toMatch(/tabular-nums/);
    expect(screen.getByTestId("analog-title-1").className).toMatch(/break-words/);
  });

  it("пустая справка: подпись вместо списка", () => {
    renderTable({ analogs: [] });
    expect(screen.getByTestId("analogs-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("analogs-table")).not.toBeInTheDocument();
  });
});

describe("подсказка «подозрительная цена» у аналога", () => {
  const NOT_ERROR = "/api/v1/position-items/:id/not-error";
  const hinted = () =>
    ourPriceAnalogFixture({
      analog_id: 9300,
      title: "Утепление стен - пеноплэкс t-100 мм",
      unit: "м²",
      suspicion: analogSuspicionFixture(),
    });
  const hintOf = (id = 9300) => screen.getByTestId(`analog-hint-${id}`);
  /** Текст подсказки без полосы: подписи шкалы recharts в jsdom зависят от размеров, а не от данных. */
  const hintText = () => {
    const copy = hintOf().cloneNode(true) as HTMLElement;
    copy.querySelector("[data-testid=price-strip]")?.remove();
    return copy.textContent;
  };

  it("множитель, предложение с ценой строки и обычной ценой, полоса, подпись про номинал", () => {
    renderTable({ analogs: [hinted()], canLump: true });
    const hint = hintOf();
    expect(within(hint).getByText("Подозрительная цена")).toBeInTheDocument();
    expect(within(hint).getByTestId("analog-hint-factor")).toHaveTextContent("×10,6");
    expect(hint).toHaveTextContent(
      "в 10,6 раза дороже обычного: эта строка 20 550,98 ₽ за м², обычная цена «Устройство плитной теплоизоляции» — 1 929,93 ₽, середина цен 7 договоров."
    );
    expect(within(hint).getByTestId("price-strip")).toBeInTheDocument();
    expect(hint).toHaveTextContent("Цены подсказки — номинальные, без НДС, без индексации.");
  });

  it("подсказка стоит сразу после наименования аналога, раньше статьи и пути в смете", () => {
    renderTable({ analogs: [hinted()], columns: [{ name: "Марка" }] });
    const card = screen.getByTestId("analog-9300");
    const order = Array.from(card.children).map((el) => el.getAttribute("data-testid") ?? el.tagName);
    const title = order.indexOf("analog-title-9300");
    expect(title).toBeGreaterThanOrEqual(0);
    expect(order[title + 1]).toBe("analog-hint-9300");
    expect(card.querySelector("[data-testid=analog-hint-9300]")!.compareDocumentPosition(card.lastElementChild!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING
    );
  });

  it("серая строчка решения стоит сразу после наименования, раньше колонок, статьи и пути", () => {
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 9304, suspicion: null, not_error: [notErrorDecisionOutlier] })],
      columns: [{ name: "Марка" }],
    });
    const order = Array.from(screen.getByTestId("analog-9304").children).map(
      (el) => el.getAttribute("data-testid") ?? el.tagName
    );
    const title = order.indexOf("analog-title-9304");
    expect(title).toBeGreaterThanOrEqual(0);
    expect(order[title + 1]).toBe("analog-checked");
    expect(order.indexOf("analog-released-9304")).toBeGreaterThan(title + 1);
  });

  it("тон: выброс — тёплый (warning), двойник — тон пары двойника (mark-contractor)", () => {
    renderTable({ analogs: [hinted()] });
    expect(hintOf()).toHaveClass("bg-warning-soft", "border-warning-border");
  });

  it("двойник: заголовок «та же цена, другая единица», множителя нет", () => {
    const twin = analogSuspicionFixture({
      facts: [
        {
          basis: { kind: "twin", neighbor_item_id: 9400 },
          kind: "twin",
          neighbor: {
            position_item_id: 9400,
            item_number: "12",
            title: "Утепление стен, рулон",
            unit: "м",
            quantity: "5",
            unit_price: "100",
            materials: null,
            works: null,
            total: "500",
            gross: false,
            is_self: false,
            is_twin: true,
          },
        },
      ],
      prices: null,
    });
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 9300, unit: "м²", suspicion: twin })] });
    const hint = hintOf();
    expect(hint).toHaveTextContent("Подозрительная цена — та же цена, другая единица");
    expect(within(hint).queryByTestId("analog-hint-factor")).not.toBeInTheDocument();
    expect(hint).toHaveClass("bg-mark-contractor-soft", "border-mark-contractor");
    expect(hint).toHaveTextContent("но там за м, а здесь за м²");
    expect(within(hint).queryByTestId("price-strip")).not.toBeInTheDocument();
  });

  it("admin: ссылка в новой вкладке и три кнопки; member: подсказка без ссылки и кнопок", () => {
    const admin = renderTable({ analogs: [hinted()], canLump: true });
    const link = within(hintOf()).getByRole("link", { name: "Разобрать в списке ↗" });
    expect(link).toHaveAttribute("href", "/contracts?tab=suspicious&line=9300");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(hintOf()).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Ошибка в цене…",
      "Не аналог в этом тендере…",
      "Не ошибка…",
    ]);
    admin.unmount();
    renderTable({ analogs: [hinted()], canLump: false });
    expect(within(hintOf()).queryByRole("link")).not.toBeInTheDocument();
    expect(within(hintOf()).queryByRole("button")).not.toBeInTheDocument();
    expect(hintOf()).toHaveTextContent("Подозрительная цена");
  });

  it("цифры подсказки — из suspicion: НДС и цена аналога панели их не меняют", () => {
    const net = renderTable({
      analogs: [{ ...hinted(), price: { net: "24661.18", shown: "30000.00" }, nominal: { net: "1", shown: "2" } }],
      vat: "net",
    });
    const before = hintText();
    net.unmount();
    renderTable({
      analogs: [{ ...hinted(), price: { net: "7.00", shown: "9.00" }, nominal: { net: "7.00", shown: "9.00" } }],
      vat: "shown",
    });
    expect(hintText()).toBe(before);
    const flat = (before ?? "").replace(/\s/g, " ");
    expect(flat).toContain("20 550,98");
    expect(flat).not.toContain("24 661,18");
  });

  it("suspicion = null и решения есть: серая строчка на каждое решение, подсказки нет", () => {
    const second = {
      ...notErrorDecisionOutlier,
      reason: "вторая причина",
      basis: { kind: "twin" as const, neighbor_item_id: 1, neighbor: { item_number: "5", title: "Сосед" } },
    };
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 9301, suspicion: null, not_error: [notErrorDecisionOutlier, second] })],
      canLump: true,
    });
    expect(screen.queryByTestId("analog-hint-9301")).not.toBeInTheDocument();
    const lines = screen.getAllByTestId("analog-checked");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toHaveTextContent(/^проверено, не ошибка — выброс в «Двери», дороже обычного: «автоматические раздвижные двери/);
    expect(lines[0]).toHaveTextContent("admin@example.test");
    expect(lines[1]).toHaveTextContent("«вторая причина»");
  });

  it("без suspicion и решений ничего не рисуется; в «Исключены» подсказки нет, даже если поле пришло", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 9302 })] });
    expect(screen.queryByTestId("analog-hint-9302")).not.toBeInTheDocument();
    expect(screen.queryByTestId("analog-checked")).not.toBeInTheDocument();
    const excluded = { ...excludedAnalogFixture({ analog_id: 9303 }), suspicion: analogSuspicionFixture() };
    renderWithProviders(
      <ExcludedAnalogs items={[excluded]} workRung={null} vat="net" canEdit ids={IDS} />
    );
    expect(screen.queryByText("Подозрительная цена")).not.toBeInTheDocument();
    expect(screen.queryByTestId("analog-hint-9303")).not.toBeInTheDocument();
  });

  it("узкий экран: прокручивается только полоса, остальное — в ширине карточки", () => {
    renderTable({ analogs: [hinted()], canLump: true });
    const hint = hintOf();
    expect(hint).toHaveClass("min-w-0");
    const scrollers = Array.from(hint.querySelectorAll(".overflow-x-auto"));
    expect(scrollers).toHaveLength(1);
    expect(within(scrollers[0] as HTMLElement).getByRole("img", { name: "цены договоров и эта строка" })).toBeInTheDocument();
    expect(within(hint).getByTestId("analog-hint-actions")).toHaveClass("flex-wrap");
  });

  it("«Ошибка в цене…» и «Не аналог в этом тендере…» открывают окна меню «…»", async () => {
    renderTable({ analogs: [hinted()], canLump: true });
    await userEvent.click(within(hintOf()).getByRole("button", { name: "Ошибка в цене…" }));
    expect(await screen.findByTestId("mark-scope")).toHaveTextContent("перестанет быть аналогом во всех тендерах");
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
    await userEvent.click(within(hintOf()).getByRole("button", { name: "Не аналог в этом тендере…" }));
    expect(await screen.findByTestId("mark-scope")).toHaveTextContent("у всех участников и во всех этапах тендера 6");
  });

  it("«Не ошибка…»: окно с основанием, PUT с bases фактов подсказки, окно закрыто", async () => {
    const asked: Array<{ url: string; body: unknown }> = [];
    server.use(
      http.put(NOT_ERROR, async ({ request }) => {
        asked.push({ url: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json([]);
      })
    );
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    renderWithProviders(
      <AnalogsTable
        analogs={[hinted()]}
        vat="net"
        canChoose={false}
        canLump
        targetAnalogId={null}
        pinnedAnalogId={null}
        contractorAnalogId={null}
        busy={false}
        onChoose={() => undefined}
        columns={[]}
        zoneScope="own"
        ids={IDS}
        formLabel={FORM}
      />,
      { queryClient }
    );
    await userEvent.click(within(hintOf()).getByRole("button", { name: "Не ошибка…" }));
    expect(await screen.findByTestId("mark-scope")).toHaveTextContent("выброс в «Устройство плитной теплоизоляции»");
    await userEvent.type(screen.getByLabelText("Причина"), "это комплект с монтажом");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toEqual({
      url: "/api/v1/position-items/9300/not-error",
      body: {
        reason: "это комплект с монтажом",
        bases: [{ kind: "outlier", family_id: 11, unit_id: 3, direction: "up" }],
      },
    });
    await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
  });

  it("409 suspicion_changed: окно закрыто, панель перечитана, сообщение «проверьте заново»", async () => {
    server.use(
      http.put(NOT_ERROR, () =>
        HttpResponse.json(
          { detail: { code: "suspicion_changed", message: "Подозрение по строке изменилось — проверьте заново." } },
          { status: 409 }
        )
      )
    );
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    renderWithProviders(
      <AnalogsTable
        analogs={[hinted()]}
        vat="net"
        canChoose={false}
        canLump
        targetAnalogId={null}
        pinnedAnalogId={null}
        contractorAnalogId={null}
        busy={false}
        onChoose={() => undefined}
        columns={[]}
        zoneScope="own"
        ids={IDS}
        formLabel={FORM}
      />,
      { queryClient }
    );
    await userEvent.click(within(hintOf()).getByRole("button", { name: "Не ошибка…" }));
    await userEvent.type(await screen.findByLabelText("Причина"), "причина");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(error).toHaveBeenCalledWith("Подозрение по строке изменилось — проверьте заново"));
    await waitFor(() => expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument());
    expect(error).toHaveBeenCalledTimes(1);
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
    error.mockRestore();
  });
});

describe("колонки отпущенных параметров", () => {
  const columns = [{ name: "Марка W" }, { name: "Толщина" }];
  const released = (value: string | null, differs = false, conflict = false) => ({ value, conflict, differs, side: null });

  it("колонка на каждый элемент columns: имя и значение у каждого аналога, в порядке columns", () => {
    renderTable({
      columns,
      analogs: [
        ourPriceAnalogFixture({ analog_id: 1, released_values: [released("W8", true), released("200", false)] }),
        ourPriceAnalogFixture({ analog_id: 2, released_values: [released("W12", true), released("250", true)] }),
      ],
    });
    const first = screen.getByTestId("analog-released-1");
    expect(within(first).getAllByTestId("analog-released-name").map((n) => n.textContent)).toEqual(["Марка W", "Толщина"]);
    expect(within(first).getAllByTestId("analog-released-value").map((n) => n.textContent)).toEqual(["W8", "200"]);
    const second = screen.getByTestId("analog-released-2");
    expect(within(second).getAllByTestId("analog-released-value").map((n) => n.textContent)).toEqual(["W12", "250"]);
  });

  it("значение без текста — «не уточнено», конфликт — «расходится по разделам»", () => {
    renderTable({
      columns,
      analogs: [ourPriceAnalogFixture({ analog_id: 1, released_values: [released(null), released(null, true, true)] })],
    });
    const values = within(screen.getByTestId("analog-released-1")).getAllByTestId("analog-released-value");
    expect(values[0]).toHaveTextContent("не уточнено");
    expect(values[1]).toHaveTextContent("расходится по разделам");
  });

  it("подсветка — только у значения с differs", () => {
    renderTable({
      columns,
      analogs: [ourPriceAnalogFixture({ analog_id: 1, released_values: [released("W8", true), released("200", false)] })],
    });
    const [differs, same] = within(screen.getByTestId("analog-released-1")).getAllByTestId("analog-released-value");
    expect(differs).toHaveAttribute("data-differs", "true");
    expect(differs.className).toContain("text-warning-text");
    expect(same).not.toHaveAttribute("data-differs");
    expect(same.className).not.toContain("text-warning-text");
  });

  it("columns пуст — колонок нет, даже если у аналога есть значения", () => {
    renderTable({
      columns: [],
      analogs: [ourPriceAnalogFixture({ analog_id: 1, released_values: [released("W8", true)] })],
    });
    expect(screen.queryByTestId("analog-released-1")).not.toBeInTheDocument();
  });
});

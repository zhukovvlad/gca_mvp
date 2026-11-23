import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { qk } from "@/services/queryKeys";
import { excludedAnalogFixture, markViewFixture } from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { ExcludedAnalog, LineIds, WorkRung } from "@/types/domain";

import { ExcludedAnalogs } from "./ExcludedAnalogs";
import { movedOnText } from "./excludedModel";

const IDS: LineIds = { tenderId: 6, roundId: 2, offerId: 64, kpItemId: 5001 };
const NOT_ANALOG = "/api/v1/tenders/6/rounds/2/our-price/offers/64/lines/5001/not-analog/:positionItemId";
const PRICE_ERROR = "/api/v1/position-items/:id/price-error";
const work = (no: 1 | 2 | 3 | 4, zone_scope: WorkRung["zone_scope"]): WorkRung => ({
  no,
  released: [],
  other_class: false,
  zone_scope,
});
const tender = (over = {}) => markViewFixture({ kind: "tender", reason: "в цене огнезащита", ...over });
const item = (id: number, over: Partial<ExcludedAnalog> = {}) =>
  excludedAnalogFixture({ analog_id: id, title: `Строка ${id}`, object_title: `Объект ${id}`, ...over });

function renderList(
  items: ExcludedAnalog[],
  props: Partial<React.ComponentProps<typeof ExcludedAnalogs>> = {},
  queryClient = createTestQueryClient()
) {
  return renderWithProviders(
    <ExcludedAnalogs items={items} workRung={work(1, "own")} vat="net" canEdit ids={IDS} {...props} />,
    { queryClient }
  );
}
const open = () => fireEvent.click(screen.getByRole("button", { name: /Исключены \(/ }));

describe("список «Исключены»", () => {
  it("пустой список — ничего не рисуется", () => {
    renderList([]);
    expect(screen.queryByTestId("excluded-list")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Исключены/ })).not.toBeInTheDocument();
  });

  it("свёрнут при строке со ступенью: заголовок с числом есть, карточек нет; раскрывается", () => {
    renderList([item(1), item(2)]);
    expect(screen.getByRole("button", { name: "Исключены (2)" })).toBeInTheDocument();
    expect(screen.queryByTestId("excluded-1")).not.toBeInTheDocument();
    open();
    expect(screen.getByTestId("excluded-1")).toBeInTheDocument();
    expect(screen.getByTestId("excluded-2")).toBeInTheDocument();
  });

  it("раскрыт сразу, когда строка без ступени (причина «исключено вручную»)", () => {
    renderList([item(1)], { workRung: null });
    expect(screen.getByTestId("excluded-1")).toBeInTheDocument();
  });

  it("карточка: договор, цена в ставке показа, наименование, «где строка» договора", () => {
    renderList([item(1, { price: { net: "5622", shown: "6859" } })], { vat: "shown" });
    open();
    const card = screen.getByTestId("excluded-1");
    expect(card).toHaveTextContent("Объект 1");
    expect(card).toHaveTextContent("Строка 1");
    const price = within(card).getByTestId("excluded-price-1");
    expect(price).toHaveTextContent("6");
    expect(price.textContent).toContain("859");
    expect(price.textContent).not.toContain("622");
    expect(within(card).getByTestId("line-where")).toBeInTheDocument();
  });

  describe("подписи пар и метка «был бы на ступени»", () => {
    const mixed = () => [
      item(1, { step: { rung: 4, zone_scope: "own" } }),
      item(2, { step: { rung: 4, zone_scope: "other" } }),
      item(3, { step: { rung: 4, zone_scope: "other" } }),
    ];

    it("несколько пар — подпись над каждой группой", () => {
      renderList(mixed(), { workRung: null });
      expect(screen.getAllByTestId("excluded-pair")).toHaveLength(2);
      expect(screen.getAllByTestId("excluded-pair")[0]).toHaveTextContent("ступень 4 · своя зона");
      expect(screen.getAllByTestId("excluded-pair")[1]).toHaveTextContent("ступень 4 · другие зоны");
    });

    it("одна пара: подписи пары нет", () => {
      renderList([item(1), item(2)], { workRung: null });
      expect(screen.queryByTestId("excluded-pair")).not.toBeInTheDocument();
    });

    it("метка есть у строки не с пары work_rung и нет у строки с неё", () => {
      renderList(mixed(), { workRung: work(4, "other") });
      open();
      expect(within(screen.getByTestId("excluded-1")).getByText("был бы на ступени 4 · своя зона")).toBeInTheDocument();
      expect(within(screen.getByTestId("excluded-2")).queryByText(/был бы на ступени/)).not.toBeInTheDocument();
      expect(within(screen.getByTestId("excluded-3")).queryByText(/был бы на ступени/)).not.toBeInTheDocument();
    });

    it("та же ступень, другая область — другая пара: метка есть", () => {
      renderList([item(1, { step: { rung: 4, zone_scope: "any" } })], { workRung: work(4, "own") });
      open();
      expect(screen.getByText("был бы на ступени 4 · любые зоны")).toBeInTheDocument();
    });
  });

  describe("отметки", () => {
    it("две отметки — отдельными блоками, каждая со своей областью, причиной, автором, датой и «Снять»", () => {
      renderList([item(1, { marks: [markViewFixture(), tender({ set_at: "2026-11-20T09:30:00+00:00" })] })]);
      open();
      const blocks = screen.getAllByTestId("excluded-mark-1");
      expect(blocks).toHaveLength(2);
      expect(blocks[0]).toHaveTextContent("ошибка в цене · во всех тендерах");
      expect(blocks[0]).toHaveTextContent("цена м² плитки на погонном метре плинтуса");
      expect(blocks[0]).toHaveTextContent("admin@example.com");
      expect(blocks[0]).toHaveTextContent("23.11.2026");
      expect(within(blocks[0]).getByRole("button", { name: "Снять во всех тендерах…" })).toBeInTheDocument();
      expect(blocks[1]).toHaveTextContent("не аналог в этом тендере");
      expect(blocks[1]).toHaveTextContent("20.11.2026");
      expect(within(blocks[1]).getByRole("button", { name: "Снять в этом тендере" })).toBeInTheDocument();
    });

    it("member: отметки видны, кнопок «Снять» нет", () => {
      renderList([item(1, { marks: [markViewFixture(), tender()] })], { canEdit: false });
      open();
      expect(screen.getAllByTestId("excluded-mark-1")).toHaveLength(2);
      expect(screen.queryByRole("button", { name: /Снять/ })).not.toBeInTheDocument();
    });

    it("«Снять в этом тендере» уходит сразу: DELETE по строке КП, сброс ourPriceAll и предпросмотров файла", async () => {
      const asked: string[] = [];
      server.use(
        http.delete(NOT_ANALOG, ({ params }) => {
          asked.push(String(params.positionItemId));
          return new HttpResponse(null, { status: 204 });
        })
      );
      const queryClient = createTestQueryClient();
      const spy = vi.spyOn(queryClient, "invalidateQueries");
      renderList([item(7, { marks: [tender()] })], {}, queryClient);
      open();
      await userEvent.click(screen.getByRole("button", { name: "Снять в этом тендере" }));
      await waitFor(() => expect(asked).toEqual(["7"]));
      await waitFor(() => {
        const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
        expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
        expect(keys).toContain(JSON.stringify(qk.tenders.kpFilePreviewsAll));
      });
    });

    it("«Снять во всех тендерах…» — через подтверждение: до «Снять везде» запроса нет, «Отмена» ничего не шлёт", async () => {
      const asked: string[] = [];
      server.use(
        http.delete(PRICE_ERROR, ({ params }) => {
          asked.push(String(params.id));
          return new HttpResponse(null, { status: 204 });
        })
      );
      renderList([item(8, { title: "Плинтус", object_title: "VEER", marks: [markViewFixture(), tender()] })], {
        family: "Устройство плинтуса",
      });
      open();
      await userEvent.click(screen.getByRole("button", { name: "Снять во всех тендерах…" }));
      expect(await screen.findByTestId("excluded-clear-confirm")).toHaveTextContent(
        "Строка «Плинтус» договора VEER вернётся в аналоги во всех тендерах, кроме тех, где на ней стоит отметка «не аналог в этом тендере» (здесь — тендер 6), и снова войдёт в обычную ставку семьи «Устройство плинтуса»."
      );
      expect(asked).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
      expect(asked).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Снять во всех тендерах…" }));
      await userEvent.click(await screen.findByRole("button", { name: "Снять везде" }));
      await waitFor(() => expect(asked).toEqual(["8"]));
    });

    it("без тендерной отметки в подтверждении нет оговорки «здесь — тендер»", async () => {
      renderList([item(8, { title: "Плинтус", object_title: "VEER", marks: [markViewFixture()] })], { family: null });
      open();
      await userEvent.click(screen.getByRole("button", { name: "Снять во всех тендерах…" }));
      expect(await screen.findByTestId("excluded-clear-confirm")).toHaveTextContent(
        "Строка «Плинтус» договора VEER вернётся в аналоги во всех тендерах, и снова войдёт в обычную ставку семьи."
      );
    });
  });
});

describe("movedOnText", () => {
  const own4 = { rung: 4 as const, zone_scope: "own" as const };

  it("пара следа раньше пары work_rung", () => {
    expect(movedOnText([item(1, { step: own4 })], work(4, "other"))).toBe(
      "Ступень 4, другие зоны: на ступени 4 своей зоны аналоги исключены вручную (1)"
    );
  });

  it("несколько пар — через «;», в порядке следа, с числом по каждой", () => {
    const items = [
      item(1, { step: { rung: 4, zone_scope: "own" } }),
      item(2, { step: { rung: 4, zone_scope: "own" } }),
      item(3, { step: { rung: 4, zone_scope: "other" } }),
      item(4, { step: { rung: 2, zone_scope: "any" } }),
    ];
    expect(movedOnText(items, work(2, "any"))).toBe(
      "Ступень 2, любые зоны: на ступени 4 своей зоны аналоги исключены вручную (2); на ступени 4 других зон аналоги исключены вручную (1)"
    );
  });

  it("пара самой work_rung и стоящие после неё не считаются", () => {
    expect(movedOnText([item(1, { step: own4 })], work(4, "own"))).toBeNull();
    const after = [item(1, { step: own4 }), item(2, { step: { rung: 3, zone_scope: "any" } })];
    expect(movedOnText(after, work(4, "own"))).toBeNull();
  });

  it("строка без ступени, пустой список — null", () => {
    expect(movedOnText([item(1)], null)).toBeNull();
    expect(movedOnText([], work(1, "own"))).toBeNull();
  });
});

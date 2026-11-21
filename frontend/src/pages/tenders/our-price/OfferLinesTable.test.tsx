import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { agreedFixture, ourPriceLineFixture, ourPriceParamFixture, workLineFixture } from "@/test/fixtures";
import type { MoneyPair, OurPriceLine } from "@/types/domain";

import { OfferLinesTable } from "./OfferLinesTable";
import { LINE_STATE_LABEL, POSITION_LABEL, WAITING_ADMIN_LABEL } from "./ourPriceCopy";

const pair = (net: string, shown: string | null): MoneyPair => ({ net, shown });

function line(id: number, overrides: Partial<OurPriceLine> = {}): OurPriceLine {
  return ourPriceLineFixture({ position_item_id: id, title: `Работа ${id}`, ...overrides });
}

function renderTable(
  lines: OurPriceLine[],
  options: { vat?: "net" | "shown"; onOpenLine?: (id: number) => void; canResolve?: boolean } = {}
) {
  return render(
    <OfferLinesTable
      lines={lines}
      vat={options.vat ?? "net"}
      canResolve={options.canResolve}
      rateClass="Класс 2"
      onOpenLine={options.onOpenLine ?? (() => undefined)}
    />
  );
}

/** Порядок строк в DOM — по `data-testid="line-<id>"`. */
function order(): number[] {
  return screen
    .getAllByTestId(/^line-\d+$/)
    .map((row) => Number(row.getAttribute("data-testid")?.slice("line-".length)));
}

function toOurs(id: number, net: string | null, shown: string | null = net): Partial<OurPriceLine> {
  return { position_item_id: id, to_lowest: net === null ? null : pair(net, shown) };
}

describe("сортировка по умолчанию — по модулю «к нашей расценке»", () => {
  it("−300, 200, −50, null идут в этом порядке: знак не влияет, пустое — в конце", () => {
    renderTable([
      line(1, toOurs(1, "-50.00")),
      line(2, toOurs(2, null)),
      line(3, toOurs(3, "200.00")),
      line(4, toOurs(4, "-300.00")),
    ]);
    expect(order()).toEqual([4, 3, 1, 2]);
  });

  it("модуль сравнивается как десятичная строка: 9 меньше 10, а не больше", () => {
    renderTable([line(1, toOurs(1, "9.00")), line(2, toOurs(2, "-10.00")), line(3, toOurs(3, "9.50"))]);
    expect(order()).toEqual([2, 3, 1]);
  });

  it("в виде «с НДС» сортирует по показанной сумме; нет ставки — в конец, как пустое", () => {
    renderTable(
      [line(1, toOurs(1, "500.00", null)), line(2, toOurs(2, "10.00", "12.20")), line(3, toOurs(3, "20.00", "24.40"))],
      { vat: "shown" }
    );
    expect(order()).toEqual([3, 2, 1]);
  });

  it("повторный щелчок по заголовку меняет направление, пустое остаётся в конце в обоих", () => {
    renderTable([
      line(1, toOurs(1, "-50.00")),
      line(2, toOurs(2, null)),
      line(3, toOurs(3, "200.00")),
    ]);
    const head = screen.getByRole("button", { name: /К самой низкой/ });
    fireEvent.click(head);
    expect(order()).toEqual([1, 3, 2]);
    fireEvent.click(head);
    expect(order()).toEqual([3, 1, 2]);
  });
});

describe("сортировка по другим колонкам", () => {
  it("объём — десятичной строкой: 9 раньше 10 по возрастанию, и наоборот по убыванию", () => {
    renderTable([
      line(1, { quantity: "10" }),
      line(2, { quantity: "9" }),
      line(3, { quantity: null }),
      line(4, { quantity: "100.5" }),
    ]);
    const head = screen.getByRole("button", { name: "Объём" });
    fireEvent.click(head);
    expect(order()).toEqual([2, 1, 4, 3]);
    fireEvent.click(head);
    expect(order()).toEqual([4, 1, 2, 3]);
  });

  it("цена КП — по выбранному виду, без перевода в число", () => {
    renderTable(
      [
        line(1, { kp: { unit_price: pair("9", "20"), total: null } }),
        line(2, { kp: { unit_price: pair("10", "5"), total: null } }),
      ],
      { vat: "shown" }
    );
    fireEvent.click(screen.getByRole("button", { name: "Цена КП" }));
    expect(order()).toEqual([2, 1]);
  });

  it("работа — по названию, единица — по тексту; пустая единица в конце", () => {
    renderTable([
      line(1, { title: "Бетон", unit: "м3" }),
      line(2, { title: "Арматура", unit: null }),
      line(3, { title: "Монтаж", unit: "м2" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: /Работа в КП/ }));
    expect(order()).toEqual([2, 1, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Ед." }));
    expect(order()).toEqual([3, 1, 2]);
  });

  it("вилка — по нижней границе, наша расценка — по значению; без вилки и без расценки — в конце", () => {
    renderTable([
      line(1, { range: { min: pair("90", "90"), max: pair("100", "100"), median: pair("95", "95") }, lowest: { value: pair("300", "300"), analog_id: null, contract_id: null, from_all_classes: false } }),
      line(2, { range: null, lowest: null }),
      line(3, { range: { min: pair("8", "8"), max: pair("100", "100"), median: pair("95", "95") }, lowest: { value: pair("1000", "1000"), analog_id: null, contract_id: null, from_all_classes: false } }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: /Наши договоры: вилка/ }));
    expect(order()).toEqual([3, 1, 2]);
    fireEvent.click(screen.getByRole("button", { name: "Самая низкая" }));
    expect(order()).toEqual([1, 3, 2]);
  });

  it("равные и пустые значения сохраняют исходный порядок строк", () => {
    renderTable([
      line(1, { unit: null, quantity: null }),
      line(2, { unit: "м2", quantity: "5" }),
      line(3, { unit: null, quantity: null }),
      line(4, { unit: "м2", quantity: "5" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Ед." }));
    expect(order()).toEqual([2, 4, 1, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Объём" }));
    expect(order()).toEqual([2, 4, 1, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Объём" }));
    expect(order()).toEqual([2, 4, 1, 3]);
  });

  it("заголовок активной колонки несёт aria-sort", () => {
    renderTable([line(1), line(2)]);
    expect(screen.getByRole("columnheader", { name: /К самой низкой/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Ед." })).toHaveAttribute("aria-sort", "none");
  });

  it("стрелка направления — только у активной колонки: вниз при убывании, вверх при возрастании", () => {
    renderTable([line(1), line(2)]);
    const toOursHead = screen.getByRole("button", { name: /К самой низкой/ });
    const unitHead = screen.getByRole("button", { name: "Ед." });
    expect(toOursHead.querySelector("svg")?.getAttribute("class")).toMatch(/arrow-down/);
    expect(unitHead.querySelector("svg")).toBeNull();
    fireEvent.click(unitHead);
    expect(unitHead.querySelector("svg")?.getAttribute("class")).toMatch(/arrow-up/);
    expect(toOursHead.querySelector("svg")).toBeNull();
  });
});

describe("колонки строки", () => {
  it("«к нашей расценке» печатает минус и значок состояния, не пусто и не «0»", () => {
    renderTable([line(1, { ...toOurs(1, "-50.00"), state: "comparable", position: "below" })]);
    const row = screen.getByTestId("line-1");
    expect(within(row).getByTestId("to-ours-1")).toHaveTextContent("−50,00");
    expect(within(row).getByTestId("to-ours-1")).toHaveTextContent("ниже всего, что платили");
  });

  it("плюс печатается явно; положение «в вилке» — своя подпись", () => {
    renderTable([line(1, { ...toOurs(1, "200.00"), position: "in" }), line(2, { ...toOurs(2, "150.00"), position: "above" })]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("+200,00");
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("в нашей вилке");
    expect(screen.getByTestId("to-ours-2")).toHaveTextContent("выше всего, что платили");
  });

  it("разные единицы учёта: разница 900 и −90 печатаются со знаком и подписью состояния", () => {
    renderTable([
      line(1, { ...toOurs(1, "900.00"), state: "unit_mismatch", position: null }),
      line(2, { ...toOurs(2, "-90.00"), state: "unit_mismatch", position: null }),
    ]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("+900,00");
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(LINE_STATE_LABEL.unit_mismatch);
    expect(screen.getByTestId("to-ours-2")).toHaveTextContent("−90,00");
    expect(screen.getByTestId("to-ours-2")).toHaveTextContent(LINE_STATE_LABEL.unit_mismatch);
  });

  it("сопоставимая строка без положения — разница без значка", () => {
    renderTable([line(1, { ...toOurs(1, "5.00"), state: "comparable", position: null })]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("+5,00");
    expect(screen.getByTestId("to-ours-1").querySelector("[data-slot='badge']")).toBeNull();
  });

  it("нет разницы — прочерк и подпись состояния", () => {
    renderTable([line(1, { ...toOurs(1, null), state: "no_history", position: null, range: null, lowest: null })]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("—");
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(LINE_STATE_LABEL.no_history);
  });

  it("no_variant: подпись «ждёт решения администратора» только при waiting_admin", () => {
    renderTable([
      line(1, { ...toOurs(1, null), state: "no_variant", waiting_admin: true }),
      line(2, { ...toOurs(2, null), state: "no_variant", waiting_admin: false }),
    ]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(screen.getByTestId("to-ours-1")).not.toHaveTextContent(LINE_STATE_LABEL.no_variant);
    expect(screen.getByTestId("to-ours-2")).toHaveTextContent(LINE_STATE_LABEL.no_variant);
    expect(screen.getByTestId("to-ours-2")).not.toHaveTextContent(WAITING_ADMIN_LABEL);
  });

  it("waiting_admin у другого состояния подпись не меняет", () => {
    renderTable([line(1, { ...toOurs(1, null), state: "in_progress", waiting_admin: true })]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(LINE_STATE_LABEL.in_progress);
    expect(screen.getByTestId("to-ours-1")).not.toHaveTextContent(WAITING_ADMIN_LABEL);
  });

  it("ячейка «Работа»: наименование, семья отдельной строкой, под ней параметры столбиком", () => {
    renderTable([
      line(1, {
        title: "Устройство стяжки",
        family: "Стяжка",
        params: [
          ourPriceParamFixture({ name: "Толщина", value: "50 мм" }),
          ourPriceParamFixture({ name: "Армирование", value: "сетка" }),
        ],
      }),
    ]);
    const work = screen.getByTestId("work-1");
    expect(work).toHaveTextContent("Устройство стяжки");
    expect(within(work).getByTestId("params-family")).toHaveTextContent(/^Стяжка$/);
    const rows = within(work).getAllByTestId("params-row");
    expect(rows[0]).toHaveTextContent("Толщина: 50 мм");
    expect(rows[1]).toHaveTextContent("Армирование: сетка");
    expect(screen.queryByTestId("secondary-1")).not.toBeInTheDocument();
  });

  it("без семьи и без параметров в ячейке только наименование", () => {
    renderTable([line(1, { family: null, params: [], variant_label: null })]);
    expect(screen.queryByTestId("params-family")).not.toBeInTheDocument();
    expect(screen.queryAllByTestId("params-row")).toHaveLength(0);
    expect(screen.getByTestId("work-1")).not.toHaveTextContent("·");
  });

  it("«подробнее» не открывает панель строки, щелчок по параметру — открывает", () => {
    const onOpenLine = vi.fn();
    const four = [1, 2, 3, 4].map((i) => ourPriceParamFixture({ name: `П${i}`, value: `з${i}` }));
    renderTable([line(1, { params: four })], { onOpenLine });
    fireEvent.click(screen.getByRole("button", { name: "подробнее · ещё 1" }));
    expect(onOpenLine).not.toHaveBeenCalled();
    expect(within(screen.getByTestId("work-1")).getAllByTestId("params-row")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(onOpenLine).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByTestId("params-row")[0]);
    expect(onOpenLine).toHaveBeenCalledWith(1);
  });

  it("раскрытие одной строки не раскрывает соседнюю", () => {
    const four = [1, 2, 3, 4].map((i) => ourPriceParamFixture({ name: `П${i}`, value: `з${i}` }));
    renderTable([line(1, { params: four }), line(2, { params: four })]);
    fireEvent.click(within(screen.getByTestId("work-1")).getByRole("button", { name: /подробнее/ }));
    expect(within(screen.getByTestId("work-1")).getAllByTestId("params-row")).toHaveLength(4);
    expect(within(screen.getByTestId("work-2")).getAllByTestId("params-row")).toHaveLength(3);
  });

  it("раскрытие остаётся у своей строки после смены сортировки", () => {
    const four = [1, 2, 3, 4].map((i) => ourPriceParamFixture({ name: `П${i}`, value: `з${i}` }));
    renderTable([
      line(1, { ...toOurs(1, "-50.00"), params: four }),
      line(2, { ...toOurs(2, "200.00"), params: four }),
    ]);
    expect(order()).toEqual([2, 1]);
    fireEvent.click(within(screen.getByTestId("work-1")).getByRole("button", { name: /подробнее/ }));
    fireEvent.click(screen.getByRole("button", { name: /К самой низкой/ }));
    expect(order()).toEqual([1, 2]);
    expect(within(screen.getByTestId("work-1")).getAllByTestId("params-row")).toHaveLength(4);
    expect(within(screen.getByTestId("work-2")).getAllByTestId("params-row")).toHaveLength(3);
  });

  it("нет объёма и нет цены КП — прочерки", () => {
    renderTable([line(1, { quantity: null, kp: { unit_price: null, total: null } })]);
    expect(screen.getByTestId("qty-1")).toHaveTextContent("—");
    expect(screen.getByTestId("kp-1")).toHaveTextContent("—");
  });

  it("семья без значения варианта печатается без хвоста", () => {
    renderTable([line(1, { family: "Стяжка", variant_label: null })]);
    expect(screen.getByTestId("work-1")).toHaveTextContent("Стяжка");
    expect(screen.getByTestId("work-1")).not.toHaveTextContent("·");
  });

  it("единица, объём и цена КП", () => {
    renderTable([
      line(1, {
        unit: "м2",
        quantity: "1234.5",
        kp: { unit_price: pair("1200", "1464"), total: null },
      }),
    ]);
    const row = screen.getByTestId("line-1");
    expect(row).toHaveTextContent("м2");
    expect(within(row).getByTestId("qty-1")).toHaveTextContent("1 234,50");
    expect(within(row).getByTestId("kp-1")).toHaveTextContent("1 200,00");
  });

  it("в виде «с НДС» цена КП и наша расценка берутся из shown", () => {
    renderTable([line(1)], { vat: "shown" });
    expect(screen.getByTestId("kp-1")).toHaveTextContent("1 464,00");
    expect(screen.getByTestId("ours-1")).toHaveTextContent("1 220,00");
  });

  it("копеечная цена — «< 0,01», а не «0»; ровно 0,01 — как есть; ноль — как есть", () => {
    renderTable([
      line(1, { kp: { unit_price: pair("0.004", null), total: null } }),
      line(2, { kp: { unit_price: pair("0.01", null), total: null } }),
      line(3, { kp: { unit_price: pair("0.02", null), total: null } }),
      line(4, { kp: { unit_price: pair("0", null), total: null } }),
    ]);
    expect(screen.getByTestId("kp-1")).toHaveTextContent("< 0,01");
    expect(screen.getByTestId("kp-2")).not.toHaveTextContent("<");
    expect(screen.getByTestId("kp-2")).toHaveTextContent("0,01");
    expect(screen.getByTestId("kp-3")).toHaveTextContent("0,02");
    expect(screen.getByTestId("kp-4")).not.toHaveTextContent("<");
    expect(screen.getByTestId("kp-4")).toHaveTextContent("0,00");
  });

  it("копеечная расценка и копеечная нижняя граница вилки — тоже «< 0,01»", () => {
    renderTable([
      line(1, {
        lowest: { value: pair("0.003", null), analog_id: null, contract_id: null, from_all_classes: false },
        range: { min: pair("0.001", null), max: pair("0.5", null), median: pair("0.2", null) },
      }),
    ]);
    expect(screen.getByTestId("ours-1")).toHaveTextContent("< 0,01");
    expect(screen.getByTestId("fork-1")).toHaveTextContent("< 0,01 – 0,50");
  });

  it("вилка с подписью ступени: уровень, класс и число договоров", () => {
    renderTable([
      line(1, {
        range: { min: pair("900", "1098"), max: pair("1300", "1586"), median: pair("1100", "1342") },
        rung: { level: "v", scope: "cls", auto: true, threshold_met: true },
        rungs: [
          { level: "v", scope: "cls", contracts: 3, selectable: true },
          { level: "f", scope: "all", contracts: 9, selectable: true },
        ],
      }),
      line(2, {
        range: { min: pair("5", "6"), max: pair("7", "8"), median: pair("6", "7") },
        rung: { level: "f", scope: "all", auto: true, threshold_met: true },
        rungs: [
          { level: "v", scope: "cls", contracts: 3, selectable: true },
          { level: "f", scope: "all", contracts: 9, selectable: true },
        ],
      }),
    ]);
    expect(screen.getByTestId("fork-1")).toHaveTextContent("900,00 – 1 300,00");
    expect(screen.getByTestId("rung-1")).toHaveTextContent("вариант · Класс 2 · 3 дог.");
    expect(screen.getByTestId("rung-2")).toHaveTextContent("вся семья · все кл. · 9 дог.");
  });

  it("ступени нет среди доступных строке — подпись без числа договоров", () => {
    renderTable([
      line(1, {
        rung: { level: "v", scope: "cls", auto: true, threshold_met: true },
        rungs: [{ level: "f", scope: "cls", contracts: 7, selectable: true }],
      }),
    ]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("вариант · Класс 2");
    expect(screen.getByTestId("rung-1")).not.toHaveTextContent("дог.");
  });

  it("число договоров берётся по уровню и охвату вместе: «вариант · все кл.» — не число своего класса", () => {
    // Фикстура несёт v/cls (3) раньше v/all (5): поиск по одному уровню нашёл бы 3.
    renderTable([line(1, { rung: { level: "v", scope: "all", auto: true, threshold_met: true } })]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("вариант · все кл. · 5 дог.");
  });

  it("уровень по параметру", () => {
    renderTable([
      line(1, {
        rung: { level: "p", scope: "cls", auto: true, threshold_met: true },
        rungs: [{ level: "p", scope: "cls", contracts: 4, selectable: true }],
      }),
    ]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("по параметру · Класс 2 · 4 дог.");
  });

  it("строка без ступени: вилки и подписи нет", () => {
    renderTable([line(1, { rung: null, rungs: [], range: null })]);
    expect(screen.getByTestId("fork-1")).toHaveTextContent("—");
    expect(screen.queryByTestId("rung-1")).not.toBeInTheDocument();
  });

  it("наша расценка с пометкой «все кл.» только когда взята по всем классам", () => {
    renderTable([
      line(1, { lowest: { value: pair("1000", "1220"), analog_id: null, contract_id: null, from_all_classes: true } }),
      line(2, { lowest: { value: pair("1000", "1220"), analog_id: null, contract_id: null, from_all_classes: false } }),
      line(3, { lowest: null }),
    ]);
    expect(screen.getByTestId("ours-1")).toHaveTextContent("все кл.");
    expect(screen.getByTestId("ours-2")).not.toHaveTextContent("все кл.");
    expect(screen.getByTestId("ours-3")).toHaveTextContent("—");
  });
});

describe("открытие строки и пустота", () => {
  it("щелчок по строке отдаёт position_item_id; кнопка названия работы доступна с клавиатуры", () => {
    const onOpenLine = vi.fn();
    renderTable([line(11), line(12)], { onOpenLine });
    fireEvent.click(screen.getByTestId("line-12"));
    expect(onOpenLine).toHaveBeenCalledTimes(1);
    expect(onOpenLine).toHaveBeenCalledWith(12);
    fireEvent.click(screen.getByRole("button", { name: "Работа 11" }));
    expect(onOpenLine).toHaveBeenCalledTimes(2);
    expect(onOpenLine).toHaveBeenLastCalledWith(11);
  });

  it("строк нет — подпись, а не пустая таблица", () => {
    renderTable([]);
    expect(screen.getByTestId("lines-empty")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("вёрстка: текст переносится в своей колонке, числа — нет", () => {
  it("ячейка работы переносит строки, числовые — нет", () => {
    renderTable([line(1)]);
    expect(screen.getByTestId("work-1").closest("td")).toHaveClass("whitespace-normal");
    for (const id of ["qty-1", "kp-1"]) {
      expect(screen.getByTestId(id).closest("td")).toHaveClass("whitespace-nowrap");
    }
  });

  it("подпись состояния — плашка без обрезки: высота по тексту, перенос разрешён", () => {
    renderTable([line(1)]);
    const chip = within(screen.getByTestId("to-ours-1")).getByText(POSITION_LABEL.in);
    expect(chip).toHaveClass("h-auto", "whitespace-normal");
  });
});

describe("единица цены", () => {
  it("цена КП обычной строки подписана рублями за единицу измерения", () => {
    renderTable([line(1, { unit: "м3" })]);
    expect(screen.getByTestId("kp-1")).toHaveTextContent("1 200,00₽/м3");
  });

  it("у системы в колонке цены — цена группы за м² объекта, а не цена за комплекс; вилка и расценка — тоже ₽/м²", () => {
    const system = line(2, {
      kind: "system",
      unit: "компл",
      kp: { unit_price: pair("39230327.87", "47860000.00"), total: pair("39230327.87", "47860000.00") },
      group: { lines: 3, share: "1", kp_per_sqm: pair("786", "959") },
    });
    renderTable([system]);
    expect(screen.getByTestId("kp-2")).toHaveTextContent("786,00₽/м²");
    expect(screen.getByTestId("kp-2")).not.toHaveTextContent("39 230 327");
    expect(screen.getByTestId("fork-2")).toHaveTextContent("₽/м²");
    expect(screen.getByTestId("ours-2")).toHaveTextContent("₽/м²");
  });

  it("у системы цена в виде «с НДС» — показанная цена группы; без группы — прочерк", () => {
    const base = { kind: "system" as const, unit: "компл" };
    renderTable(
      [
        line(1, { ...base, group: { lines: 2, share: "1", kp_per_sqm: pair("786", "959") } }),
        line(2, { ...base, group: null }),
      ],
      { vat: "shown" }
    );
    expect(screen.getByTestId("kp-1")).toHaveTextContent("959,00₽/м²");
    expect(screen.getByTestId("kp-2")).toHaveTextContent("—");
    expect(screen.getByTestId("kp-2")).not.toHaveTextContent("₽");
  });

  it("сортировка по цене КП идёт по показанному: у системы — по цене группы", () => {
    const system = (id: number, perSqm: string) =>
      line(id, {
        kind: "system",
        kp: { unit_price: pair("1000000", "1000000"), total: null },
        group: { lines: 1, share: "1", kp_per_sqm: pair(perSqm, perSqm) },
      });
    renderTable([system(1, "900"), system(2, "100")]);
    fireEvent.click(screen.getByRole("button", { name: "Цена КП" }));
    expect(order()).toEqual([2, 1]);
  });

  it("сортировка по цене КП — по полной строке сервера: на экране обе 100,00, порядок задаёт тысячная", () => {
    const priced = (id: number, price: string) => line(id, { kp: { unit_price: pair(price, price), total: null } });
    renderTable([priced(1, "100.004"), priced(2, "100.001")]);
    expect(screen.getByTestId("kp-1")).toHaveTextContent("100,00");
    expect(screen.getByTestId("kp-2")).toHaveTextContent("100,00");
    fireEvent.click(screen.getByRole("button", { name: "Цена КП" }));
    expect(order()).toEqual([2, 1]);
  });
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

describe("OfferLinesTable: подпись «ждёт решения администратора» как ссылка", () => {
  const waiting = (id: number, over: Partial<OurPriceLine> = {}) =>
    line(id, { ...toOurs(id, null), state: "no_variant", waiting_admin: true, context_id: 700 + id, ...over });

  function renderLinked(lines: OurPriceLine[], onOpenLine: (id: number) => void = () => undefined, canResolve = true) {
    return render(
      <MemoryRouter initialEntries={["/tenders/1"]}>
        <OfferLinesTable lines={lines} vat="net" rateClass="Класс 2" canResolve={canResolve} onOpenLine={onOpenLine} />
        <LocationProbe />
      </MemoryRouter>
    );
  }

  it("admin: подпись — ссылка на карточку контекста строки", () => {
    renderLinked([waiting(1)]);

    const link = within(screen.getByTestId("to-ours-1")).getByRole("link", { name: WAITING_ADMIN_LABEL });
    expect(link).toHaveAttribute("href", "/families?context=701");
  });

  it("открытое предложение: ссылка на очередь «Предложения» с единицей и контекстом", () => {
    renderLinked([waiting(1, { suggestion_queue: "list", unit_id: 3 })]);

    expect(screen.getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute(
      "href",
      "/families?tab=suggestions&queue=list&unit=3&context=701"
    );
  });

  it("открытое предложение у строки без единицы: фильтр «без единицы»", () => {
    renderLinked([waiting(1, { suggestion_queue: "new", unit_id: null })]);

    expect(screen.getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute(
      "href",
      "/families?tab=suggestions&queue=new&unit=none&context=701"
    );
  });

  it("щелчок по ссылке переходит и не открывает панель строки", () => {
    const onOpenLine = vi.fn();
    renderLinked([waiting(1)], onOpenLine);

    fireEvent.click(screen.getByRole("link", { name: WAITING_ADMIN_LABEL }));

    expect(screen.getByTestId("loc")).toHaveTextContent("/families?context=701");
    expect(onOpenLine).not.toHaveBeenCalled();
  });

  it("member: тот же текст без ссылки", () => {
    renderLinked([waiting(1)], () => undefined, false);

    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("без контекста у строки ссылки нет даже у admin", () => {
    renderLinked([waiting(1, { context_id: null })]);

    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("права, пришедшие после первого рендера, превращают подпись в ссылку", () => {
    const table = (canResolve: boolean) => (
      <MemoryRouter>
        <OfferLinesTable lines={[waiting(1)]} vat="net" rateClass="Класс 2" canResolve={canResolve} onOpenLine={() => undefined} />
      </MemoryRouter>
    );
    const { rerender } = render(table(false));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();

    rerender(table(true));

    expect(screen.getByRole("link", { name: WAITING_ADMIN_LABEL })).toHaveAttribute("href", "/families?context=701");
  });

  it("другие подписи у admin остаются текстом", () => {
    renderLinked([
      line(1, { ...toOurs(1, null), state: "no_variant", waiting_admin: false }),
      line(2, { ...toOurs(2, null), state: "in_progress", waiting_admin: true }),
    ]);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("решения 4б в строках таблицы", () => {
  const author = { id: 3, email: "admin@example.test" };
  const manual = (over: Partial<NonNullable<OurPriceLine["manual_rung"]>> = {}): NonNullable<OurPriceLine["manual_rung"]> => ({
    level: "v",
    scope: "all",
    active: true,
    reset_reason: null,
    target: "line",
    set_by: author,
    set_at: "2026-03-14T09:30:00+00:00",
    ...over,
  });

  it("под нашей расценкой — «договорились {X}» в выбранном виде НДС; без решения метки нет", () => {
    const agreed = agreedFixture();
    const net = renderTable([line(1, { agreed }), line(2)]);
    expect(screen.getByTestId("agreed-1")).toHaveTextContent("договорились 1 234,57");
    expect(screen.queryByTestId("agreed-2")).not.toBeInTheDocument();
    net.unmount();
    renderTable([line(1, { agreed })], { vat: "shown" });
    expect(screen.getByTestId("agreed-1")).toHaveTextContent("договорились 1 506,17");
  });

  it("вид чисел: 30 знаков в ответе печатаются двумя знаками", () => {
    renderTable([line(1, { agreed: agreedFixture({ net: "1234.56789012345678901234567890" }) })]);
    const text = screen.getByTestId("agreed-1").textContent ?? "";
    expect(text).toContain("1 234,57");
    expect(text).not.toMatch(/\d[.,]\d{3,}/);
  });

  it("ручная ступень: у подписи ступени «ручная»; недействующая — «ручная не действует»; авто — без метки", () => {
    renderTable([
      line(1, { manual_rung: manual() }),
      line(2, { manual_rung: manual({ active: false, reset_reason: "key_changed" }) }),
      line(3),
    ]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent(/ручная$/);
    expect(screen.getByTestId("rung-1")).not.toHaveTextContent("ручная не действует");
    expect(screen.getByTestId("rung-2")).toHaveTextContent("ручная не действует");
    expect(screen.getByTestId("rung-3")).not.toHaveTextContent("ручная");
  });

  it("у строки без ступени и с недействующей ручной метка всё равно видна", () => {
    renderTable([line(1, { rung: null, manual_rung: manual({ active: false, reset_reason: "rung_empty" }) })]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("ручная не действует");
  });
});

describe("лестница аналогов: колонки и подписи строки-работы", () => {
  const workRow = (id: number, over: Partial<OurPriceLine> = {}) => line(id, workLineFixture({ position_item_id: id, ...over }));

  it("заголовки «Самая низкая» и «К самой низкой»; старых «Наша расценка» и «К нашей расценке» нет", () => {
    renderTable([workRow(1)]);
    expect(screen.getByRole("button", { name: "Самая низкая" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /К самой низкой/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Наша расценка" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /К нашей расценке/ })).not.toBeInTheDocument();
  });

  it("подпись под ценой: ступень §2.8 и «N аналогов»", () => {
    renderTable([
      workRow(1, { work_rung: { no: 1, released: [], other_class: false, zone_scope: "own" }, analogs_count: 7 }),
      workRow(2, { work_rung: { no: 4, released: ["Марка W"], other_class: true, zone_scope: "own" }, analogs_count: 1 }),
      workRow(3, { work_rung: { no: 2, released: ["Толщина"], other_class: false, zone_scope: "own" }, analogs_count: 2 }),
    ]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("тот же вариант · 7 аналогов");
    expect(screen.getByTestId("rung-2")).toHaveTextContent("без учёта: марка W · другой класс · 1 аналог");
    expect(screen.getByTestId("rung-3")).toHaveTextContent("без учёта: толщина · 2 аналога");
  });

  it("подпись под ценой: «другая зона» и «зона не определена» — по области, у own меток нет", () => {
    const scoped = (zone_scope: "own" | "other" | "any") => ({
      work_rung: { no: 1 as const, released: [], other_class: false, zone_scope },
      analogs_count: 3,
    });
    renderTable([workRow(1, scoped("own")), workRow(2, scoped("other")), workRow(3, scoped("any"))]);
    expect(screen.getByTestId("rung-1")).toHaveTextContent("тот же вариант · 3 аналога");
    expect(screen.getByTestId("rung-1")).not.toHaveTextContent("зона");
    expect(screen.getByTestId("rung-2")).toHaveTextContent("тот же вариант · 3 аналога · другая зона");
    expect(screen.getByTestId("rung-3")).toHaveTextContent("тот же вариант · 3 аналога · зона не определена");
  });

  it("метка цели: у «самой свежей» и «аналога» есть, у «самой низкой» и «договорились» нет", () => {
    renderTable([
      workRow(1, { target_kind: "freshest" }),
      workRow(2, { target_kind: "analog" }),
      workRow(3, { target_kind: "lowest" }),
      workRow(4, { target_kind: "agreed" }),
    ]);
    expect(screen.getByTestId("ours-1")).toHaveTextContent("цель: самая свежая");
    expect(screen.getByTestId("ours-2")).toHaveTextContent("цель: аналог");
    expect(screen.getByTestId("ours-3")).not.toHaveTextContent("цель:");
    expect(screen.getByTestId("ours-4")).not.toHaveTextContent("цель:");
  });

  it("сумма «самой низкой» — расценка аналога, а не вилка; разница со знаком", () => {
    renderTable([workRow(1)]);
    expect(screen.getByTestId("ours-1")).toHaveTextContent("1 000,00");
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("+20 000,00");
  });

  it("причина no_history текстом в колонке разницы; no_zone — «ждёт решения» ссылкой в очередь «Зоны»", () => {
    render(
      <MemoryRouter initialEntries={["/tenders/1"]}>
        <OfferLinesTable
          lines={[
        workRow(1, { state: "no_history", no_history_reason: "no_family", lowest: null, to_lowest: null, work_rung: null }),
        workRow(2, { state: "no_zone", waiting_admin: true, zone: null, section_chain_id: 91, lowest: null, to_lowest: null, work_rung: null }),
          ]}
          vat="net"
          rateClass="Класс 2"
          canResolve
          onOpenLine={() => undefined}
        />
      </MemoryRouter>
    );
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("в договорах нет работ этой семьи");
    const link = within(screen.getByTestId("to-ours-2")).getByRole("link", { name: WAITING_ADMIN_LABEL });
    expect(link).toHaveAttribute("href", "/families?tab=suggestions&queue=zones&chain=91");
  });
  it("строка без аналога в своей зоне, но с аналогами в других: подпись называет зоны и размер групп", () => {
    renderTable([
      line(1, {
        state: "no_history",
        no_history_reason: "no_zone_match",
        zone: "aboveground",
        other_zones: [
          { zone: "underground", lines: 3, contracts: 2 },
          { zone: "territory", lines: 1, contracts: 1 },
        ],
      }),
    ]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent(
      "нет аналога в зоне «Надземная» · есть в зоне «Подземная»: 3 строки, 2 договора, «Территория»: 1 строка, 1 договор"
    );
  });

  it("без other_zones подпись прежняя", () => {
    renderTable([line(1, { state: "no_history", no_history_reason: "no_zone_match", zone: "aboveground" })]);
    expect(screen.getByTestId("to-ours-1")).toHaveTextContent("нет аналога в зоне «надземная часть»");
  });

  it("метка цели «справочная»", () => {
    renderTable([line(1, { target_kind: "reference" })]);
    expect(screen.getByTestId("target-mark-1")).toHaveTextContent("цель: справочная");
  });

});

import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ourPriceAnalogFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceAnalog } from "@/types/domain";

import { AnalogsTable, type AnalogsTableProps } from "./AnalogsTable";

const many = (n: number): OurPriceAnalog[] =>
  Array.from({ length: n }, (_, i) =>
    ourPriceAnalogFixture({ analog_id: 9000 + i, contract_id: 200 + i, title: `Строка ${i}`, object_title: `Объект ${i}` })
  );

function renderTable(props: Partial<AnalogsTableProps> = {}) {
  return renderWithProviders(
    <AnalogsTable
      analogs={props.analogs ?? many(2)}
      vat="net"
      canChoose={props.canChoose ?? true}
      targetAnalogId={props.targetAnalogId ?? null}
      busy={props.busy ?? false}
      onChoose={props.onChoose ?? (() => undefined)}
    />
  );
}

const ids = () => screen.getAllByTestId(/^analog-\d+$/).map((r) => Number(r.getAttribute("data-testid")!.slice(7)));

describe("справка аналогов", () => {
  it("первые пять строк, остальные за «ещё N»; порядок сервера сохранён", () => {
    renderTable({ analogs: many(8) });
    expect(ids()).toEqual([9000, 9001, 9002, 9003, 9004]);
    const more = screen.getByRole("button", { name: "ещё 3 аналога" });
    fireEvent.click(more);
    expect(ids()).toHaveLength(8);
    expect(ids().slice(5)).toEqual([9005, 9006, 9007]);
    fireEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(ids()).toHaveLength(5);
  });

  it("пять и меньше: кнопки «ещё» нет", () => {
    renderTable({ analogs: many(5) });
    expect(screen.queryByRole("button", { name: /ещё/ })).not.toBeInTheDocument();
  });

  it("строка: объект, договор, подрядчик, строка договора с цепочкой разделов, дата, цена", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 1, price: { net: "66800", shown: "81496" } })] });
    const row = screen.getByTestId("analog-1");
    expect(row).toHaveTextContent("Объект Ж");
    expect(row).toHaveTextContent("ГП-08");
    expect(row).toHaveTextContent("Подрядчик А");
    expect(row).toHaveTextContent("Стены монолитные B40, 200 мм");
    expect(row).toHaveTextContent("Корпус 1 / Надземная часть / Монолит");
    expect(row).toHaveTextContent("03.2025");
    expect(row).toHaveTextContent("66 800,00");
  });

  it("вид «с НДС»: цена из shown", () => {
    renderWithProviders(
      <AnalogsTable
        analogs={[ourPriceAnalogFixture({ analog_id: 1, price: { net: "100", shown: "122" } })]}
        vat="shown"
        canChoose={false}
        targetAnalogId={null}
        busy={false}
        onChoose={() => undefined}
      />
    );
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

  it("метки: самая низкая, самая свежая, договор с этим подрядчиком", () => {
    renderTable({
      analogs: [ourPriceAnalogFixture({ analog_id: 1, is_lowest: true, is_freshest: true, with_this_contractor: true })],
    });
    const row = screen.getByTestId("analog-1");
    expect(within(row).getByText("самая низкая")).toBeInTheDocument();
    expect(within(row).getByText("самая свежая")).toBeInTheDocument();
    expect(within(row).getByText("договор с этим подрядчиком")).toBeInTheDocument();
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

  it("аналог — текущая цель: метка «цель» вместо кнопки", () => {
    renderTable({ analogs: [ourPriceAnalogFixture({ analog_id: 7 })], targetAnalogId: 7 });
    const row = screen.getByTestId("analog-7");
    expect(within(row).getByText("цель")).toBeInTheDocument();
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });

  it("раскладка: числа справа, ячейки с переносом, как в таблицах экрана", () => {
    renderTable();
    const price = screen.getByTestId("analog-price-9000");
    expect(price.className).toMatch(/tabular-nums/);
    expect(price.closest("td")!.className).toMatch(/text-right/);
    expect(screen.getByTestId("analog-9000").querySelector("td")!.className).toMatch(/whitespace-normal/);
  });

  it("пустая справка: подпись вместо таблицы", () => {
    renderTable({ analogs: [] });
    expect(screen.getByTestId("analogs-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("analogs-table")).not.toBeInTheDocument();
  });
});

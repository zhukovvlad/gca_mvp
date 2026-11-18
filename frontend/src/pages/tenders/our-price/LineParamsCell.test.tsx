import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ourPriceParamFixture } from "@/test/fixtures";
import type { LineParam, ParamsBasis } from "@/types/domain";

import { LineParamsCell } from "./LineParamsCell";

function params(count: number): LineParam[] {
  return Array.from({ length: count }, (_, i) =>
    ourPriceParamFixture({ name: `Параметр ${i + 1}`, value: `значение ${i + 1}` })
  );
}

function renderCell(
  list: LineParam[],
  options: { family?: string | null; basis?: ParamsBasis | null; onClick?: () => void } = {}
) {
  const family = options.family === undefined ? "Семья 1" : options.family;
  return render(
    <div data-testid="host" onClick={options.onClick}>
      <LineParamsCell family={family} params={list} basis={options.basis ?? "rung"} />
    </div>
  );
}

describe("LineParamsCell: состав ячейки", () => {
  it("семья — отдельной строкой, параметры — «название: значение» столбиком в порядке params", () => {
    renderCell(params(2));
    expect(screen.getByTestId("params-family")).toHaveTextContent(/^Семья 1$/);
    const rows = screen.getAllByTestId("params-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Параметр 1: значение 1");
    expect(rows[1]).toHaveTextContent("Параметр 2: значение 2");
  });

  it("параметров нет — только семья, кнопки нет", () => {
    renderCell([]);
    expect(screen.getByTestId("params-family")).toHaveTextContent("Семья 1");
    expect(screen.queryAllByTestId("params-row")).toHaveLength(0);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("нет семьи и нет параметров — ячейка пуста", () => {
    renderCell([], { family: null });
    expect(screen.getByTestId("host")).toBeEmptyDOMElement();
  });

  it("семьи нет, параметры есть — строки параметров без строки семьи", () => {
    renderCell(params(1), { family: null });
    expect(screen.queryByTestId("params-family")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("params-row")).toHaveLength(1);
  });
});

describe("LineParamsCell: «подробнее» и «свернуть»", () => {
  it("ровно три параметра — все видны, кнопки нет", () => {
    renderCell(params(3));
    expect(screen.getAllByTestId("params-row")).toHaveLength(3);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("четыре — видны три, кнопка «подробнее · ещё 1»", () => {
    renderCell(params(4));
    expect(screen.getAllByTestId("params-row")).toHaveLength(3);
    expect(screen.queryByText("Параметр 4:", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "подробнее · ещё 1" })).toBeInTheDocument();
  });

  it("шесть — «ещё 3»; по нажатию видны все, кнопка «свернуть»; по ней снова три", () => {
    renderCell(params(6));
    fireEvent.click(screen.getByRole("button", { name: "подробнее · ещё 3" }));
    expect(screen.getAllByTestId("params-row")).toHaveLength(6);
    expect(screen.queryByRole("button", { name: /подробнее/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(screen.getAllByTestId("params-row")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "подробнее · ещё 3" })).toBeInTheDocument();
  });

  it("нажатие «подробнее» и «свернуть» не доходит до обработчика клика объемлющего элемента", () => {
    const onClick = vi.fn();
    renderCell(params(4), { onClick });
    fireEvent.click(screen.getByRole("button", { name: "подробнее · ещё 1" }));
    fireEvent.click(screen.getByRole("button", { name: "свернуть" }));
    expect(onClick).not.toHaveBeenCalled();
    // Соседний вход: клик по тексту параметра обработчик объемлющего элемента получает.
    fireEvent.click(screen.getAllByTestId("params-row")[0]);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("раскрытие одного экземпляра не раскрывает другой", () => {
    render(
      <>
        <div data-testid="a">
          <LineParamsCell family="Семья А" params={params(4)} basis="rung" />
        </div>
        <div data-testid="b">
          <LineParamsCell family="Семья Б" params={params(4)} basis="rung" />
        </div>
      </>
    );
    fireEvent.click(within(screen.getByTestId("a")).getByRole("button"));
    expect(within(screen.getByTestId("a")).getAllByTestId("params-row")).toHaveLength(4);
    expect(within(screen.getByTestId("b")).getAllByTestId("params-row")).toHaveLength(3);
  });
});

describe("LineParamsCell: значения, цвет, приписки", () => {
  it("refreshing без значения — «не уточнено — уточняется» в таблице, цвет предупреждающий", () => {
    renderCell([ourPriceParamFixture({ name: "Марка", value: null, refreshing: true })]);
    const value = screen.getByTestId("params-value");
    expect(value).toHaveTextContent("не уточнено — уточняется");
    expect(value.className).toContain("text-warning-text");
  });

  it("refreshing вне списка — «не из списка: «…» — уточняется»", () => {
    renderCell([
      ourPriceParamFixture({ name: "Марка", source: "off_list", value: null, off_list: "Х9", refreshing: true }),
    ]);
    expect(screen.getByTestId("params-value")).toHaveTextContent("не из списка: «Х9» — уточняется");
  });

  it("обычное значение — без предупреждающего цвета; не уточнено без refreshing — с ним", () => {
    renderCell([
      ourPriceParamFixture({ name: "А", value: "ок" }),
      ourPriceParamFixture({ name: "Б", value: null }),
    ]);
    const [ok, unknown] = screen.getAllByTestId("params-value");
    expect(ok.className).not.toContain("text-warning-text");
    expect(unknown).toHaveTextContent("не уточнено");
    expect(unknown.className).toContain("text-warning-text");
  });

  it("основание rung: у главного «не учитывается» и «такой же набор» — приписки; у required — нет", () => {
    renderCell(
      [
        ourPriceParamFixture({ name: "А", match: "ignored" }),
        ourPriceParamFixture({ name: "Б", match: "same_set" }),
        ourPriceParamFixture({ name: "В", match: "required" }),
      ],
      { basis: "rung" }
    );
    const rows = screen.getAllByTestId("params-row");
    expect(rows[0]).toHaveTextContent("— не учитывается при подборе");
    expect(rows[1]).toHaveTextContent("— при подборе нужен такой же набор");
    expect(rows[2]).not.toHaveTextContent("при подборе");
  });

  it("основание catalog: приписки о подборе нет даже у ignored", () => {
    renderCell([ourPriceParamFixture({ name: "А", match: "ignored" })], { basis: "catalog" });
    expect(screen.getByTestId("params-row")).not.toHaveTextContent("при подборе");
  });

  it("значение из разделов — приписка «(из разделов)»; из наименования — нет", () => {
    renderCell([
      ourPriceParamFixture({ name: "А", source: "path" }),
      ourPriceParamFixture({ name: "Б", source: "name" }),
    ]);
    const rows = screen.getAllByTestId("params-row");
    expect(rows[0]).toHaveTextContent("(из разделов)");
    expect(rows[1]).not.toHaveTextContent("(из разделов)");
  });
});

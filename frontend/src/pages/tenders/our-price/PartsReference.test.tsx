import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { compositeLineViewFixture } from "@/test/fixtures";
import type { OurPricePartsGroup } from "@/types/domain";

import { PartsReference } from "./PartsReference";

const groups = (): OurPricePartsGroup[] => compositeLineViewFixture().parts_reference;

describe("справка «по частям»", () => {
  it("по группе на составной параметр, по блоку на часть; части названы своими значениями", () => {
    render(<PartsReference groups={groups()} vat="net" unit="м3" />);
    expect(screen.getByTestId("parts-group-41")).toHaveTextContent("Тип конструкции");
    expect(screen.getByTestId("part-501")).toHaveTextContent("плиты");
    expect(screen.getByTestId("part-502")).toHaveTextContent("балки");
  });

  it("часть: число аналогов и договоров, вилка, самая низкая с объектом, договором и датой", () => {
    render(<PartsReference groups={groups()} vat="net" unit="м3" />);
    const part = screen.getByTestId("part-501");
    expect(part).toHaveTextContent("6 аналогов в 3 договорах");
    expect(part).toHaveTextContent("36 200,12 – 42 100,00");
    expect(part).toHaveTextContent("₽/м3");
    expect(part).toHaveTextContent("самая низкая 36 200,12");
    expect(part).toHaveTextContent("Объект Б · дог. Б-1 · 03.2025");
  });

  it("класс части: «тот же класс объекта» и «другой класс объекта» — по other_class", () => {
    render(<PartsReference groups={groups()} vat="net" />);
    expect(screen.getByTestId("part-501")).toHaveTextContent("тот же класс объекта");
    expect(screen.getByTestId("part-501")).not.toHaveTextContent("другой класс");
    expect(screen.getByTestId("part-502")).toHaveTextContent("другой класс объекта");
  });

  it("целью части не становятся: ни одной кнопки", () => {
    render(<PartsReference groups={groups()} vat="net" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("parts-reference")).toHaveTextContent("Целью части не становятся");
  });

  it("вид «с НДС»: вилка и самая низкая — из shown", () => {
    render(<PartsReference groups={groups()} vat="shown" />);
    const part = screen.getByTestId("part-501");
    expect(part).toHaveTextContent("44 164,15 – 51 362,00");
    expect(part).toHaveTextContent("самая низкая 44 164,15");
  });

  it("деньги строками: хвост цены не теряется и не округляется через Number", () => {
    const tail = groups();
    tail[0].parts[0].min = { net: "36200.995", shown: null };
    tail[0].parts[0].lowest!.value = { net: "36200.995", shown: null };
    render(<PartsReference groups={tail} vat="net" />);
    expect(screen.getByTestId("part-501")).toHaveTextContent("36 201,00");
  });

  it("в виде «с НДС» без ставки (shown = null) — прочерк, а не нетто под чужой подписью", () => {
    const noRate = groups();
    noRate[0].parts[0].min = { net: "36200", shown: null };
    noRate[0].parts[0].max = { net: "42100", shown: null };
    render(<PartsReference groups={noRate} vat="shown" />);
    expect(screen.getByTestId("part-501")).not.toHaveTextContent("36 200,00");
    expect(screen.getByTestId("part-501")).toHaveTextContent("—");
  });

  it("аналогов у части нет: «аналогов нет», вилки и самой низкой нет", () => {
    const empty = groups();
    empty[0].parts[1] = { ...empty[0].parts[1], analogs: 0, contracts: 0, min: null, max: null, lowest: null, other_class: false };
    render(<PartsReference groups={empty} vat="net" />);
    const part = screen.getByTestId("part-502");
    expect(part).toHaveTextContent("аналогов нет");
    expect(part).not.toHaveTextContent("самая низкая");
    expect(part).not.toHaveTextContent("другой класс");
  });

  it("аналоги есть, остатка нет (все выбросы): вилки нет, и это сказано", () => {
    const outliers = groups();
    outliers[0].parts[1] = { ...outliers[0].parts[1], min: null, max: null, lowest: null };
    render(<PartsReference groups={outliers} vat="net" />);
    const part = screen.getByTestId("part-502");
    expect(part).toHaveTextContent("3 аналога в 2 договорах");
    expect(part).toHaveTextContent("вилки нет");
    expect(part).not.toHaveTextContent("аналогов нет");
  });

  it("групп нет — ничего не рисуется", () => {
    const { container } = render(<PartsReference groups={[]} vat="net" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("две группы: каждая со своими частями", () => {
    const two = [...groups(), { ...groups()[0], parameter_id: 42, parameter_name: "Армирование", parts: [{ ...groups()[0].parts[0], value_id: 601, value: "сетка" }] }];
    render(<PartsReference groups={two} vat="net" />);
    expect(within(screen.getByTestId("parts-group-42")).getByTestId("part-601")).toHaveTextContent("сетка");
    expect(within(screen.getByTestId("parts-group-41")).queryByTestId("part-601")).not.toBeInTheDocument();
  });
});

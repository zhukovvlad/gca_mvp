import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ourPriceAnalogFixture, workLineFixture } from "@/test/fixtures";
import type { MoneyPair } from "@/types/domain";

import { FigureCards } from "./FigureCards";

const pair = (net: string, shown: string | null = net): MoneyPair => ({ net, shown });

const analogs = [
  ourPriceAnalogFixture({ analog_id: 8001, object_title: "Объект Ж", number: "ГП-08", period: "2025-03" }),
  ourPriceAnalogFixture({ analog_id: 8002, object_title: "Объект З", number: "Д-112", period: "2026-08" }),
];

function renderCards(line = workLineFixture(), vat: "net" | "shown" = "net") {
  return render(<FigureCards line={line} analogs={analogs} vat={vat} />);
}

describe("карточки цифр работы", () => {
  it("«КП ниже»: отрицательная разница печатается с минусом, а слова — «ниже»", () => {
    renderCards(workLineFixture({ to_lowest: pair("-300.00"), position: "below" }));
    const card = screen.getByTestId("figure-lowest");
    expect(card).toHaveTextContent("КП ниже");
    expect(card).toHaveTextContent("−300,00");
  });

  it("нулевая разница: «КП на уровне расценки», без знака", () => {
    renderCards(workLineFixture({ to_lowest: pair("0.00") }));
    const card = screen.getByTestId("figure-lowest");
    expect(card).toHaveTextContent("КП на уровне расценки");
    expect(card).not.toHaveTextContent("+0,00");
  });

  it("вид «с НДС»: цена и разница из shown", () => {
    renderCards(workLineFixture(), "shown");
    expect(screen.getByTestId("figure-lowest")).toHaveTextContent("1 220,00");
    expect(screen.getByTestId("figure-lowest")).toHaveTextContent("+24 400,00");
  });

  it("разницы нет (null): прочерк, а не «+0,00»", () => {
    renderCards(workLineFixture({ to_lowest: null }));
    expect(screen.getByTestId("figure-lowest-diff")).toHaveTextContent("—");
    expect(screen.getByTestId("figure-lowest")).not.toHaveTextContent("КП выше");
    expect(screen.getByTestId("figure-lowest")).not.toHaveTextContent("КП ниже");
  });

  it("аналога нет среди справки (например, в списке не пришёл): цена остаётся, объект не выдумывается", () => {
    render(<FigureCards line={workLineFixture()} analogs={[]} vat="net" />);
    const card = screen.getByTestId("figure-lowest");
    expect(card).toHaveTextContent("1 000,00");
    expect(card).not.toHaveTextContent("Объект");
  });

  it("у самой свежей без даты цены дата не печатается", () => {
    const undated = [analogs[0], { ...analogs[1], period: null }];
    render(
      <FigureCards
        line={workLineFixture({ freshest: { value: pair("1100"), analog_id: 8002, contract_id: 102, period: null } })}
        analogs={undated}
        vat="net"
      />
    );
    expect(screen.getByTestId("figure-freshest")).not.toHaveTextContent("08.2026");
  });
});

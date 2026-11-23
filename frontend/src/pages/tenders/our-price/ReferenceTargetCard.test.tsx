import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  referenceChoiceFixture,
  refLinkRowFixture,
  refRowFixture,
  workLineFixture,
} from "@/test/fixtures";
import type { OurPriceLine, TargetChoice } from "@/types/domain";

import { ReferenceTargetCard, TargetCards } from "./TargetCards";

function renderCard(choice: TargetChoice, opts: { canEdit?: boolean; vat?: "net" | "shown"; line?: Partial<OurPriceLine> } = {}) {
  const onReset = vi.fn();
  const line = workLineFixture({ unit: "м3", choice, target_kind: "reference", ...opts.line });
  render(
    <ReferenceTargetCard line={line} vat={opts.vat ?? "net"} canEdit={opts.canEdit ?? true} onReset={onReset} />
  );
  return onReset;
}

const linkChoice = (over: Partial<TargetChoice> = {}) =>
  referenceChoiceFixture({
    origin: "link",
    analog_id: 9101,
    reference: refLinkRowFixture(),
    conversion: { method: "link", link_id: 41, factor: "0.100", factor_shown: "0,1", basis: "толщина 100 мм" },
    ...over,
  });

describe("карточка справочной цели: откуда и цифры", () => {
  it.each([
    ["zone", "справочная · другая зона"],
    ["search", "справочная · из поиска"],
    ["link", "справочная · по связи"],
  ] as const)("происхождение %s называет словами", (origin, text) => {
    renderCard(referenceChoiceFixture({ origin }));
    expect(screen.getByTestId("reference-target-origin")).toHaveTextContent(text);
  });

  it("без пересчёта: договор, раздел, цена в единице строки и отклонение; блока пересчёта нет", () => {
    renderCard(referenceChoiceFixture());
    expect(screen.getByTestId("reference-target-where")).toHaveTextContent("Объект Т, договор № Д-27, 2026 г.");
    expect(screen.getByTestId("reference-target-where")).toHaveTextContent("Благоустройство → МАФ");
    expect(screen.getByTestId("reference-target-price")).toHaveTextContent("8 372,92 ₽/м3");
    expect(screen.getByTestId("reference-target-deviation")).toHaveTextContent("КП +182 %");
    expect(screen.queryByTestId("reference-target-conversion")).not.toBeInTheDocument();
  });

  it("пометка выброса — по текущему расчёту: есть при кратности, нет при null", () => {
    const { unmount } = render(
      <ReferenceTargetCard
        line={workLineFixture({
          unit: "м3",
          choice: referenceChoiceFixture({ reference: refRowFixture({ outlier_ratio: "3.2" }) }),
          target_kind: "reference",
        })}
        vat="net"
        canEdit
        onReset={vi.fn()}
      />
    );
    expect(screen.getByTestId("reference-target-outlier")).toHaveTextContent("3,2");
    unmount();
    renderCard(referenceChoiceFixture({ reference: refRowFixture({ outlier_ratio: null }) }));
    expect(screen.queryByTestId("reference-target-outlier")).not.toBeInTheDocument();
  });

  it("при пересчёте: «пересчёт из м2», основание, исходная и пересчитанная цена", () => {
    renderCard(linkChoice());
    const conversion = screen.getByTestId("reference-target-conversion");
    expect(conversion).toHaveTextContent("пересчёт из м2");
    expect(conversion).toHaveTextContent("1 м2 = 0,1 м3 — толщина 100 мм");
    expect(conversion).toHaveTextContent("в договоре: 1 107,24 ₽/м2");
    expect(conversion).toHaveTextContent("в единице строки КП: 11 072,40 ₽/м3");
    expect(screen.getByTestId("reference-target-price")).toHaveTextContent("11 072,40 ₽/м3");
  });

  it("цена берётся в выбранной базе НДС, не пересчитывается на клиенте", () => {
    renderCard(referenceChoiceFixture(), { vat: "shown" });
    expect(screen.getByTestId("reference-target-price")).toHaveTextContent("10 215,96 ₽/м3");
  });

  it("автор и дата выбора", () => {
    renderCard(referenceChoiceFixture());
    expect(screen.getByText(/Выбрал admin@example\.test, 15\.11\.2026/)).toBeInTheDocument();
  });
});

describe("карточка справочной цели: подсказки", () => {
  it("link_now: новый множитель в единицах строки, цифра цели прежняя", () => {
    renderCard(linkChoice({ hints: { link_now: { factor: "0.125", factor_shown: "0,125", basis: "толщина 125 мм" }, link_gone: false, own_analogs: false } }));
    expect(screen.getByTestId("reference-hint-link-now")).toHaveTextContent(
      "По связи сейчас 1 м2 = 0,125 м3; чтобы пересчитать — выберите цель заново."
    );
    expect(screen.getByTestId("reference-target-price")).toHaveTextContent("11 072,40 ₽/м3");
  });

  it("link_gone: связь удалена или не действует, цифра по снимку", () => {
    renderCard(linkChoice({ hints: { link_now: null, link_gone: true, own_analogs: false } }));
    expect(screen.getByTestId("reference-hint-link-gone")).toHaveTextContent(
      "Связь удалена или не действует; цифра — по снимку."
    );
  });

  it("own_analogs: у строки появились свои аналоги", () => {
    renderCard(referenceChoiceFixture({ hints: { link_now: null, link_gone: false, own_analogs: true } }));
    expect(screen.getByTestId("reference-hint-own-analogs")).toHaveTextContent("У строки появились свои аналоги.");
  });

  it("без подсказок сервера ни одна не печатается", () => {
    renderCard(referenceChoiceFixture());
    expect(screen.queryByTestId("reference-hint-link-now")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reference-hint-link-gone")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reference-hint-own-analogs")).not.toBeInTheDocument();
  });
});

describe("карточка справочной цели: возврат и сброс", () => {
  it("«вернуть самую низкую» — у admin, нажатие снимает выбор", async () => {
    const onReset = renderCard(referenceChoiceFixture());
    await userEvent.click(screen.getByRole("button", { name: "вернуть самую низкую" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("у member кнопки нет, а цифры те же", () => {
    renderCard(referenceChoiceFixture(), { canEdit: false });
    expect(screen.queryByRole("button", { name: "вернуть самую низкую" })).not.toBeInTheDocument();
    expect(screen.getByTestId("reference-target-price")).toBeInTheDocument();
  });

  it("сброшенная цель называет причину сброса и не печатает цифру", () => {
    renderCard(referenceChoiceFixture({ active: false, reset_reason: "reference_unavailable", reference: null }));
    expect(screen.getByTestId("reference-target-inactive")).toHaveTextContent(
      "Справочная цель не действует: строка договора больше не годится как справочная"
    );
    expect(screen.queryByTestId("reference-target")).not.toBeInTheDocument();
  });

  it("справочная цель на комплект: причина «комплект вместо ставки»", () => {
    renderCard(referenceChoiceFixture({ active: false, reset_reason: "lump_sum", reference: null }));
    expect(screen.getByTestId("reference-target-inactive")).toHaveTextContent(
      "Справочная цель не действует: комплект вместо ставки"
    );
  });

  it("сброшенная справочная цель не порождает плашку «самая низкая» у карточек стратегий", () => {
    const line = workLineFixture({
      choice: referenceChoiceFixture({ active: false, reset_reason: "kind_changed", reference: refRowFixture() }),
    });
    render(<TargetCards line={line} analogs={[]} vat="net" canEdit onChoose={() => {}} />);
    expect(screen.queryByTestId("target-inactive")).not.toBeInTheDocument();
  });
});

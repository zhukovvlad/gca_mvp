import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecalcOverlay } from "./RecalcOverlay";

/** Серый слой пересчёта (спека 4а §2.15): поверх прежних цифр, не вместо них. */
describe("RecalcOverlay", () => {
  it("active: слой со спиннером и подписью, прежние цифры остаются под ним", () => {
    render(
      <RecalcOverlay active label="Пересчитываем">
        <p>старые цифры</p>
      </RecalcOverlay>
    );
    const overlay = screen.getByTestId("recalc-overlay");
    expect(overlay).toHaveTextContent("Пересчитываем");
    expect(overlay).toHaveAttribute("role", "status");
    expect(screen.getByText("старые цифры")).toBeInTheDocument();
  });

  it("не active: слоя нет, дети на месте", () => {
    render(
      <RecalcOverlay active={false} label="Пересчитываем">
        <p>цифры</p>
      </RecalcOverlay>
    );
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(screen.queryByText("Пересчитываем")).not.toBeInTheDocument();
    expect(screen.getByText("цифры")).toBeInTheDocument();
  });
});

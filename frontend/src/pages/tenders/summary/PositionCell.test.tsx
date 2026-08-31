import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatDecimalMoney } from "@/lib/format";
import type { StagePositionsCell } from "@/types/domain";

import { KIND_LABEL, STATE_LABEL } from "./cellCopy";
import { PositionCell } from "./PositionCell";

function cell(overrides: Partial<StagePositionsCell>): StagePositionsCell {
  return {
    state: "amount",
    amount: "1000.00",
    amount_unavailable_reason: null,
    quantity: null,
    quantity_unit: null,
    quantity_changed: false,
    estimate_rows: 1,
    change: { kind: "none", value: null, direction: null, reason: "first_column" },
    ...overrides,
  };
}

function renderCell(c: StagePositionsCell) {
  return render(
    <table>
      <tbody>
        <tr>
          <PositionCell cell={c} />
        </tr>
      </tbody>
    </table>
  );
}

describe("PositionCell — три этажа (§2.4)", () => {
  it("сумма, объём и изменение стоят в одной ячейке", () => {
    renderCell(
      cell({
        quantity: "8726.397168",
        quantity_unit: "м²",
        change: { kind: "percent", value: "-5.0", direction: "down", reason: null },
      })
    );
    const td = screen.getByRole("cell");
    expect(td).toHaveTextContent("1 000,00");
    expect(td).toHaveTextContent("8 726,4");
    expect(td).toHaveTextContent("м²");
    expect(td).toHaveTextContent("-5");
  });

  it("изменившийся объём выделен тоном, неизменившийся — нет (§6.3)", () => {
    const { rerender } = renderCell(cell({ quantity: "6+11", quantity_unit: "шт", quantity_changed: true }));
    expect(screen.getByTestId("cell-quantity")).toHaveClass("text-warning-text");
    rerender(
      <table>
        <tbody>
          <tr>
            <PositionCell cell={cell({ quantity: "6", quantity_unit: "шт" })} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByTestId("cell-quantity")).not.toHaveClass("text-warning-text");
  });

  it("у ячейки без объёма третьего этажа нет (§6.3)", () => {
    renderCell(cell({}));
    expect(screen.queryByTestId("cell-quantity")).toBeNull();
  });

  it("состояние без суммы печатает подпись из STATE_LABEL, не число", () => {
    renderCell(cell({ state: "absent", amount: null, estimate_rows: 0 }));
    expect(screen.getByRole("cell")).toHaveTextContent(STATE_LABEL.absent);
  });

  it("исчезнувшая печатает «нет в файле», а не «снято» (§6.3)", () => {
    renderCell(
      cell({
        state: "absent",
        amount: null,
        estimate_rows: 0,
        change: { kind: "disappeared", value: null, direction: null, reason: null },
      })
    );
    expect(screen.getByRole("cell")).toHaveTextContent(KIND_LABEL.disappeared);
    expect(screen.getByRole("cell")).not.toHaveTextContent(KIND_LABEL.removed);
  });

  it("«снято» состоянием не повторяется видом изменения (§2.5)", () => {
    renderCell(
      cell({
        state: "removed",
        amount: null,
        change: { kind: "removed", value: null, direction: null, reason: null },
      })
    );
    const matches = screen.getByRole("cell").textContent?.match(/снято/g) ?? [];
    expect(matches).toHaveLength(1);
  });

  it("неизвестная база НДС гасит и число, и значок изменения — печатается только причина (§2.8)", () => {
    renderCell(
      cell({
        amount: null,
        amount_unavailable_reason: "unknown_vat_base",
        // Вид изменения выбран заведомо ВИДИМЫМ (процент), чтобы третья
        // проверка что-то значила: если гашение сломать, здесь появится
        // «-5,0%», и assertion его поймает.
        change: { kind: "percent", value: "-5.0", direction: "down", reason: null },
      })
    );
    const td = screen.getByRole("cell");
    expect(td).toHaveTextContent("нет базы НДС");
    // Отдельное от пилюли утверждение: числа НЕТ вовсе, а не просто пилюля
    // где-то рядом с числом.
    expect(td).not.toHaveTextContent(formatDecimalMoney(null));
    expect(screen.queryByTestId("change")).toBeNull();
  });
});

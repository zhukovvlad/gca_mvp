import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { MatrixCellDialog } from "./MatrixCellDialog";
import { sampleMatrixColumns, sampleMatrixRows } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { MatrixCellDetail, MatrixCellItem } from "@/types/domain";

/**
 * Drill-down по ячейке (спека пересчёта §2.5, задача 9): валовое из файла, нетто и
 * база НДС между ними — три РАЗНЫЕ подписи, а не одна «Ставка». До этой задачи
 * `deviation_reason` доезжал до типа, но не до экрана: `DeviationCell` подписывал
 * ЛЮБОЙ пустой результат как «нет норматива», и «неизвестна база НДС» визуально
 * превращалась в неверное «нет норматива».
 */

const itemFixture: MatrixCellItem = {
  position_item_id: 9001,
  job_title: "Кладка кирпичная наружных стен",
  unit_code: "M3",
  weight: "30",
  unit_cost_total: "120.00",
  unit_cost_net: "100.00",
  vat_rate_base: "20",
  total_cost_total: "3600.00",
  standard_unit_rate: "100.00",
  deviation_pct: "0",
  deviation_reason: null,
};

function renderDialog(overrides: { items: MatrixCellItem[] }) {
  const detail: MatrixCellDetail = {
    contract_id: sampleMatrixColumns[0].contract_id,
    catalog_position_id: sampleMatrixRows[0].catalog_position_id,
    estimate_id: sampleMatrixColumns[0].estimate_id,
    amendment_no: sampleMatrixColumns[0].amendment_no,
    items: overrides.items,
  };
  server.use(http.get("/api/v1/analytics/matrix/cell", () => HttpResponse.json(detail)));
  return renderWithProviders(
    <MatrixCellDialog
      open
      contractId={sampleMatrixColumns[0].contract_id}
      row={sampleMatrixRows[0]}
      column={sampleMatrixColumns[0]}
      onClose={() => {}}
    />
  );
}

describe("MatrixCellDialog: валовое, нетто и база НДС", () => {
  it("показывает валовую ставку, нетто и базу отдельными подписями", async () => {
    renderDialog({
      items: [
        {
          ...itemFixture,
          unit_cost_total: "120.00",
          unit_cost_net: "100.00",
          vat_rate_base: "20",
          deviation_pct: "0",
          deviation_reason: null,
        },
      ],
    });

    expect(await screen.findByTestId("item-gross")).toHaveTextContent("120");
    expect(screen.getByTestId("item-net")).toHaveTextContent("100");
    expect(screen.getByTestId("item-vat-base")).toHaveTextContent("20");
  });

  it("нетто отсутствует, когда база неизвестна, и причина названа верно", async () => {
    renderDialog({
      items: [
        {
          ...itemFixture,
          unit_cost_total: "120.00",
          unit_cost_net: null,
          vat_rate_base: null,
          deviation_pct: null,
          deviation_reason: "unknown_vat_base",
        },
      ],
    });

    expect(await screen.findByTestId("item-net")).toHaveTextContent("—");
    expect(screen.getByText("неизвестна база НДС")).toBeInTheDocument();
    expect(screen.queryByText("нет норматива")).not.toBeInTheDocument();
  });
});

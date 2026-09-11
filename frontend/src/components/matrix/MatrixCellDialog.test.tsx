import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { MatrixCellDialog } from "./MatrixCellDialog";
import { sampleMatrixColumns, sampleMatrixRows } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { CELL_ITEM_EXCLUDED_REASONS } from "@/types/domain";
import type {
  CellItemExcludedReason,
  MatrixCell,
  MatrixCellDetail,
  MatrixCellItem,
  MatrixRow,
} from "@/types/domain";

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
  included: true,
  excluded_reason: null,
};

/**
 * Невошедшая строка — носитель для теста четырёх причин ниже (спека §2.8,
 * задача 9 плана правила цены; `_row_exclusion_reason`,
 * `backend/crud/analytics.py`). `included` ложно, отклонение пусто ОБА поля
 * (носитель факта — `excluded_reason`, а не «нет норматива»).
 *
 * **Каждая ветка строит ДОСТИЖИМОЕ состояние (ревью задачи 9), а не только
 * `included: false` с нетронутыми цифрой price/weight из `itemFixture`.**
 * Прежняя версия оставляла `unit_cost_total: "120.00"`/`weight: "30"` (оба
 * годные) при ЛЮБОЙ причине — а `included` на бэкенде считается ровно этими
 * двумя величинами (`is_price(unit_cost_total) && is_weight(weight)`), так
 * что «годная цена и вес, но `included: false`» бэкенд никогда не производит.
 */
function excludedItem(reason: CellItemExcludedReason): MatrixCellItem {
  const base = { ...itemFixture, unit_cost_net: null, deviation_pct: null, deviation_reason: null };
  switch (reason) {
    case "no_price":
      // Цена ноль (не отрицательна, не нефинитна) — не проходит `is_price`
      // (`> 0`), вес остаётся годным.
      return { ...base, unit_cost_total: "0.00", weight: "30", included: false, excluded_reason: reason };
    case "negative":
      // Цена отрицательна (конечна) — `is_price` тоже не проходит, но по
      // другой причине; вес остаётся годным.
      return { ...base, unit_cost_total: "-50.00", weight: "30", included: false, excluded_reason: reason };
    case "not_finite":
      // Цена — не число: `_row_exclusion_reason` проверяет конечность ПЕРВОЙ,
      // выше всех остальных причин.
      return { ...base, unit_cost_total: "NaN", weight: "30", included: false, excluded_reason: reason };
    case "no_weight":
      // Цена годная (> 0, конечна), а вес — ноль: `is_price` истинна,
      // `is_weight` ложна.
      return { ...base, unit_cost_total: "120.00", weight: "0", included: false, excluded_reason: reason };
  }
}

function renderDialog(overrides: { items: MatrixCellItem[]; row?: MatrixRow; contractId?: number }) {
  const contractId = overrides.contractId ?? sampleMatrixColumns[0].contract_id;
  const row = overrides.row ?? sampleMatrixRows[0];
  const detail: MatrixCellDetail = {
    contract_id: contractId,
    catalog_position_id: row.catalog_position_id,
    estimate_id: sampleMatrixColumns[0].estimate_id,
    amendment_no: sampleMatrixColumns[0].amendment_no,
    items: overrides.items,
  };
  server.use(http.get("/api/v1/analytics/matrix/cell", () => HttpResponse.json(detail)));
  return renderWithProviders(
    <MatrixCellDialog
      open
      contractId={contractId}
      row={row}
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

/**
 * Невошедшие строки drill-down (правило цены, спека §2.8; задача 3/9 плана
 * 2026-09-09). Носитель списка сменился на `_all_positions_select` — строка
 * без пригодной цены или веса теперь тоже видна, с `included: false` и
 * причиной `excluded_reason`. Без различителя `included` компонент подписал
 * бы такую строку как «нет норматива» (`deviation_pct`/`deviation_reason` у
 * неё пусты ОБА, а `DeviationCell` без `reason` по умолчанию читает пустоту
 * как «нет норматива») — факт неверный: норматив у строки мог и БЫТЬ.
 */
describe("MatrixCellDialog: невошедшие строки — причина невхождения", () => {
  it("невошедшая строка не выдаёт себя за «нет норматива»", async () => {
    renderDialog({ items: [excludedItem("no_price")] });

    expect(await screen.findByTestId("item-deviation")).toHaveTextContent("цены нет");
    expect(screen.queryByText("нет норматива")).not.toBeInTheDocument();
  });

  it("четыре причины невхождения различны и видны РОВНО своим текстом", async () => {
    /**
     * Список причин — из `CELL_ITEM_EXCLUDED_REASONS` (`types/domain.ts`), не
     * рукописный литерал (ревью задачи 9): та же защита, что у `CELL_RATE_
     * REASONS` в `MatrixPage.test.tsx` — построен из `Record<
     * CellItemExcludedReason, true>`, чью полноту стережёт `tsc`.
     */
    const EXPECTED_LABEL: Record<CellItemExcludedReason, string> = {
      no_price: "цены нет",
      negative: "цена отрицательна",
      not_finite: "не число",
      no_weight: "нет веса",
    };
    const seenLabels = new Set<string>();

    for (const reason of CELL_ITEM_EXCLUDED_REASONS) {
      const { unmount } = renderDialog({ items: [excludedItem(reason)] });
      const label = (await screen.findByTestId("item-deviation")).textContent ?? "";
      // Точное совпадение, а не «длина больше нуля» (ревью задачи 9).
      expect(label).toBe(EXPECTED_LABEL[reason]);
      seenLabels.add(label);
      unmount();
    }

    expect(seenLabels.size).toBe(CELL_ITEM_EXCLUDED_REASONS.length);
  });

  it("вошедшая и невошедшая строки одной ячейки различимы одновременно", async () => {
    renderDialog({
      items: [
        itemFixture,
        { ...excludedItem("negative"), position_item_id: 9002, job_title: "Кладка кирпичная внутренних стен" },
      ],
    });

    expect(await screen.findByText("Кладка кирпичная наружных стен")).toBeInTheDocument();
    expect(screen.getByText("Кладка кирпичная внутренних стен")).toBeInTheDocument();
    // Вошедшая строка несёт цифру отклонения (itemFixture: deviation_pct "0").
    expect(screen.getByText("0,0%")).toBeInTheDocument();
    // Невошедшая — причину, а не отклонение.
    expect(screen.getByText("цена отрицательна")).toBeInTheDocument();
  });

  /**
   * Дефект данных (найдено ревью задачи 9): контракт обещает, что
   * `excluded_reason` не `null` тогда и только тогда, когда `included` ложно
   * (`types/domain.ts::MatrixCellItem`). Если сервер его всё же нарушит,
   * компонент не вправе МОЛЧА выдумать причину («цены нет» по умолчанию —
   * прежняя редакция `?? "no_price"`) — это утверждало бы факт, которого
   * сервер не называл. Показывается нейтральный прочерк.
   */
  it("нарушенный контракт (excluded_reason: null при included: false) не выдумывает причину", async () => {
    renderDialog({
      items: [{ ...itemFixture, included: false, excluded_reason: null, deviation_pct: null, deviation_reason: null }],
    });

    const cell = await screen.findByTestId("item-deviation");
    expect(cell).toHaveTextContent("—");
    expect(cell).not.toHaveTextContent("цены нет");
  });
});

/**
 * Сноска «средневзвешенная ставка» (§6; ревью задачи 9). Носитель списка
 * несёт и невошедшие строки — сноска обязана считать ВОШЕДШИЕ, а не
 * `detail.items.length`, и не обещать среднюю ставку там, где у ЯЧЕЙКИ (не у
 * отдельной строки) её нет вовсе (`rate_reason` непуст).
 */
describe("MatrixCellDialog: сноска «средневзвешенная ставка» считает вошедшие строки", () => {
  it("две строки, но вошла одна — сноска не появляется", async () => {
    renderDialog({
      items: [
        itemFixture,
        { ...excludedItem("no_price"), position_item_id: 9002, job_title: "Кладка кирпичная внутренних стен" },
      ],
    });

    await screen.findByText("Кладка кирпичная наружных стен");
    expect(screen.queryByText(/средневзвешенная ставка/)).not.toBeInTheDocument();
  });

  it("две строки вошли, но у ячейки нет ставки (rate_reason непуст) — сноска не обещает среднюю", async () => {
    const cellWithoutRate: MatrixCell = {
      contract_id: sampleMatrixColumns[0].contract_id,
      rate: null,
      amount: null,
      standard_unit_rate: null,
      deviation_pct: null,
      rate_reason: "unknown_vat_base",
      deviation_reason: "no_rate",
    };
    const row: MatrixRow = { ...sampleMatrixRows[0], cells: [cellWithoutRate] };

    renderDialog({
      items: [
        itemFixture,
        { ...itemFixture, position_item_id: 9002, job_title: "Кладка кирпичная внутренних стен" },
      ],
      row,
    });

    await screen.findByText("Кладка кирпичная наружных стен");
    expect(screen.queryByText(/средневзвешенная ставка/)).not.toBeInTheDocument();
  });

  it("две строки вошли и у ячейки есть ставка — сноска считает именно вошедшие", async () => {
    renderDialog({
      items: [
        itemFixture,
        { ...itemFixture, position_item_id: 9002, job_title: "Кладка кирпичная внутренних стен" },
      ],
    });

    expect(await screen.findByText(/сложена из 2 строк сметы/)).toBeInTheDocument();
  });
});

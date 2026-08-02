import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { ReviewItemsTable } from "./ReviewItemsTable";
import type { InvoiceItem } from "@/types/invoice";

function makeItem(over: Partial<InvoiceItem> = {}): InvoiceItem {
  return {
    id: 1, raw_name: "Бетон В25", item_type: "material",
    material_class: null, material_class_id: null,
    quantity: 5, raw_unit: "м3", unit_price: 8000, amount: 40000, vat_amount: 8000,
    ...over,
  };
}

describe("ReviewItemsTable — raw_unit", () => {
  it("renders and edits raw_unit", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ReviewItemsTable items={[makeItem()]} onChange={onChange} />);
    const unitInput = screen.getByDisplayValue("м3");
    await user.clear(unitInput);
    await user.type(unitInput, "т");
    expect(onChange).toHaveBeenCalled();
    const last = onChange.mock.calls.at(-1)![0] as InvoiceItem[];
    expect(last[0].raw_unit).toContain("т");
  });
});

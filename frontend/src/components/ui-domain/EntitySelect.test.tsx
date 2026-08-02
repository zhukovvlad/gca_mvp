import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { EntitySelect } from "./EntitySelect";

describe("EntitySelect", () => {
  const items = [
    { id: 1, name: "ЖК Радуга" },
    { id: 2, name: "ЖК Звезда" },
  ];

  it("renders placeholder when no value", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={() => {}}
        getLabel={(i) => i.name}
        placeholder="Выберите проект"
      />
    );
    expect(screen.getByText("Выберите проект")).toBeInTheDocument();
  });

  it("displays human label, not id, for selected value", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={1}
        onChange={() => {}}
        getLabel={(i) => i.name}
      />
    );
    // Trigger показывает label, не id
    expect(screen.getByText("ЖК Радуга")).toBeInTheDocument();
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("calls onChange with numeric id when item is selected", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={onChange}
        getLabel={(i) => i.name}
      />
    );
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByText("ЖК Звезда"));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("disables interaction when disabled prop is true", () => {
    renderWithProviders(
      <EntitySelect
        items={items}
        value={null}
        onChange={() => {}}
        getLabel={(i) => i.name}
        disabled
      />
    );
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
});

import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { DirectionSwitcher } from "./DirectionSwitcher";

const DIRECTIONS = [
  { code: "concrete", name: "Бетон" },
  { code: "rebar", name: "Арматура" },
];

describe("DirectionSwitcher", () => {
  it("renders «Все направления» first, then directions in order", () => {
    renderWithProviders(<DirectionSwitcher directions={DIRECTIONS} value="all" onChange={() => {}} />);
    const items = screen.getAllByRole("button");
    expect(items.map((t) => t.textContent)).toEqual(["Все направления", "Бетон", "Арматура"]);
  });

  it("marks active segment with aria-pressed (toggle semantics)", () => {
    renderWithProviders(<DirectionSwitcher directions={DIRECTIONS} value="rebar" onChange={() => {}} />);
    expect(screen.getByTestId("direction-rebar")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onChange with code on click", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<DirectionSwitcher directions={DIRECTIONS} value="all" onChange={onChange} />);
    await user.click(screen.getByTestId("direction-rebar"));
    expect(onChange).toHaveBeenCalledWith("rebar");
  });

  it("ignores click on the already-active segment (filter keeps one selection)", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<DirectionSwitcher directions={DIRECTIONS} value="rebar" onChange={onChange} />);
    await user.click(screen.getByTestId("direction-rebar"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders nothing when directions is empty (legacy mode, ADR #11)", () => {
    renderWithProviders(<DirectionSwitcher directions={[]} value="all" onChange={() => {}} />);
    expect(screen.queryByTestId("direction-switcher")).not.toBeInTheDocument();
  });
});

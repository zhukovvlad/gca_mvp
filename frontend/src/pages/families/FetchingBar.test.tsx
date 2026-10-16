import { act, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FETCHING_BAR_DELAY_MS, FetchingBar } from "./pending";

const bar = () => screen.queryByRole("progressbar", { name: "Обновление данных" });

describe("FetchingBar — задержка показа", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("перечитывание короче задержки: полоса не показывается", () => {
    const { rerender } = render(<FetchingBar active={true} />);
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS - 1));
    expect(bar()).not.toBeInTheDocument();
    rerender(<FetchingBar active={false} />);
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS * 2));
    expect(bar()).not.toBeInTheDocument();
  });

  it("перечитывание дольше задержки: полоса появляется после неё и пропадает по окончании", () => {
    const { rerender } = render(<FetchingBar active={true} />);
    expect(bar()).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS));
    expect(bar()).toBeInTheDocument();
    rerender(<FetchingBar active={false} />);
    expect(bar()).not.toBeInTheDocument();
  });

  it("по окончании перечитывания полоса пропадает в том же кадре, а не кадром позже", () => {
    const seen: boolean[] = [];
    // Проба рядом с полосой: её layout-эффект видит DOM сразу после того же коммита.
    function Probe({ active }: { active: boolean }) {
      useLayoutEffect(() => {
        seen.push(bar() !== null);
      }, [active]);
      return null;
    }
    const { rerender } = render(
      <>
        <FetchingBar active={true} />
        <Probe active={true} />
      </>
    );
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS));
    expect(bar()).toBeInTheDocument();
    seen.length = 0;

    rerender(
      <>
        <FetchingBar active={false} />
        <Probe active={false} />
      </>
    );

    expect(seen).toEqual([false]);
  });

  it("после долгого перечитывания следующее короткое снова ждёт задержку: полосы нет", () => {
    const { rerender } = render(<FetchingBar active={true} />);
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS));
    expect(bar()).toBeInTheDocument();
    rerender(<FetchingBar active={false} />);
    rerender(<FetchingBar active={true} />);
    expect(bar()).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(FETCHING_BAR_DELAY_MS - 1));
    expect(bar()).not.toBeInTheDocument();
    rerender(<FetchingBar active={false} />);
    expect(bar()).not.toBeInTheDocument();
  });
});

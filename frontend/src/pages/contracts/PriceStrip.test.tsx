/// <reference types="node" />
import { readFileSync } from "node:fs";

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { suspiciousGroupPrices } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

import { logDomain } from "./logDomain";
import { PriceStrip } from "./PriceStrip";
import stripSource from "./PriceStrip.tsx?raw";

describe("PriceStrip", () => {
  it("ось — степени десяти вокруг точек, обычной цены и самой строки", () => {
    // цены договоров 9 590…239 133, обычная 16 500, строка 239 133 → 1 000 … 1 000 000 не нужен: 10 000 … 1 000 000
    expect(logDomain([9590.45, 16385.42, 16500, 239133.5, 239133.5, 16500])).toEqual({
      lo: 1000,
      hi: 1000000,
      ticks: [1000, 10000, 100000, 1000000],
    });
    // все значения в одной декаде — ось всё равно покрывает хотя бы одно деление
    expect(logDomain([2000, 3000])).toEqual({ lo: 1000, hi: 10000, ticks: [1000, 10000] });
    // значения ровно на степенях
    expect(logDomain([100, 1000])).toEqual({ lo: 100, hi: 1000, ticks: [100, 1000] });
  });

  it("рисует полосу с подписью и легендой; compact сужает минимальную ширину", () => {
    const { rerender } = renderWithProviders(<PriceStrip prices={suspiciousGroupPrices} net="239133.50" />);
    const strip = screen.getByTestId("price-strip");
    expect(within(strip).getByRole("img", { name: "цены договоров и эта строка" })).toBeInTheDocument();
    for (const text of ["цена договора", "этот же договор", "обычная — середина договоров", "эта строка", "разброс других договоров"]) {
      expect(strip).toHaveTextContent(text);
    }
    expect(strip.querySelector(".min-w-\\[460px\\]")).not.toBeNull();

    rerender(<PriceStrip prices={suspiciousGroupPrices} net="239133.50" compact />);
    expect(screen.getByTestId("price-strip").querySelector(".min-w-\\[320px\\]")).not.toBeNull();
  });
});

/**
 * Каждая CSS-переменная полосы обязана существовать в палитре приложения (`docs/pitfalls/frontend.md`:
 * неразрешимая `var()` молча даёт чёрный `fill`; в jsdom каскада нет, поэтому проверяем текстом).
 */
describe("токены цвета полосы цен", () => {
  it("все var(--…) объявлены в index.css", () => {
    const css = readFileSync("src/index.css", "utf8");
    const used = [...new Set([...stripSource.matchAll(/var\((--[a-z-]+)\)/g)].map((m) => m[1]))];
    expect(used.length).toBeGreaterThan(0);
    const missing = used.filter((name) => !css.includes(`${name}:`));
    expect(missing).toEqual([]);
  });
});

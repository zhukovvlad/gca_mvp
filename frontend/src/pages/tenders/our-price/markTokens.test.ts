/**
 * @vitest-environment node
 *
 * Цвет в jsdom не считается (каскад не наблюдается), поэтому тест проверяет то, что видно текстом:
 * у каждой метки цели есть пара «светлая / тёмная» в `index.css`, и каждый класс `*-mark-*`, который
 * пишут компоненты, имеет имя в `@theme`. Без этого Tailwind молча не создаёт класс, и метка
 * остаётся без заливки — это видно только в браузере.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import analogsSource from "./AnalogsTable.tsx?raw";
import cardsSource from "./TargetCards.tsx?raw";

const css = readFileSync("src/index.css", "utf8");
const MARKS = ["lowest", "freshest", "contractor", "contractor-soft", "on"];

/** Значения `--mark-<имя>` в порядке объявления: сначала светлая тема, затем тёмная. */
function declared(name: string): string[] {
  const found = [...css.matchAll(new RegExp(`--mark-${name}:\\s*([^;]+);`, "g"))];
  return found.map((m) => m[1].trim());
}

describe("метки цели — токены оформления", () => {
  it("предпосылка: палитра прочитана с диска, а не пустая заглушка", () => {
    expect(css).toContain("--accent-primary:");
  });

  it.each(MARKS)("--mark-%s объявлена дважды: светлая и тёмная тема, значения разные", (name) => {
    const values = declared(name);
    expect(values).toHaveLength(2);
    expect(values[0]).not.toBe(values[1]);
  });

  it("классы *-mark-* из компонентов есть в @theme", () => {
    const used = [...(cardsSource + analogsSource).matchAll(/(?:bg|text|border-t|border-l)-mark-([a-z-]+)/g)].map(
      (m) => m[1]
    );
    expect(used.length).toBeGreaterThan(0);
    const missing = [...new Set(used)].filter((name) => !css.includes(`--color-mark-${name}:`));
    expect(missing).toEqual([]);
  });
});

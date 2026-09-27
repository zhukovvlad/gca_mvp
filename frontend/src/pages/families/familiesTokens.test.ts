/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import contextsTabSource from "./ContextsTab.tsx?raw";
import familiesTabSource from "./FamiliesTab.tsx?raw";
import sourceChipSource from "./SourceChip.tsx?raw";

/**
 * Каждый цветовой класс заливки плашек экрана семей (`SOURCE_TINT`,
 * `FAMILY_STATUS_TINT`, плашка «система») обязан иметь токен `--color-…` в
 * `index.css` (ревью задачи 9, сверка с макетом 27.09.2026).
 *
 * Зачем: Tailwind не генерирует утилиту `bg-source-classifier-soft`, если
 * токена нет, — плашка молча остаётся прозрачной, а тесты `SourceChip`/
 * `FamiliesTab` сравнивают только СТРОКИ классов и остаются зелёными
 * (`docs/pitfalls/frontend.md`: неразрешимое имя меняет цвет молча). Цвет в
 * jsdom ненаблюдаем, существование токена — текст, он проверяется точно; по
 * образцу `src/pages/compare/chartTokens.test.ts`. Замер цвета в браузере
 * этот тест не заменяет.
 */
const css = readFileSync("src/index.css", "utf8");

function tintClasses(source: string, recordName: string): string[] {
  const block = source.match(new RegExp(`const ${recordName}[^=]*=\\s*\\{([\\s\\S]*?)\\};`));
  if (!block) return [];
  return [...block[1].matchAll(/"([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/));
}

const SYSTEM_BADGE = contextsTabSource.match(/className="(border-info-border[^"]*)"/)?.[1] ?? "";

const CLASSES = [
  ...tintClasses(sourceChipSource, "SOURCE_TINT"),
  ...tintClasses(familiesTabSource, "FAMILY_STATUS_TINT"),
  ...SYSTEM_BADGE.split(/\s+/).filter(Boolean),
];

function tokenOf(cls: string): string {
  return `--color-${cls.replace(/^(bg|text|border)-/, "")}`;
}

describe("экран семей — токены заливки плашек", () => {
  it("предпосылка: палитра прочитана, классы найдены во всех трёх источниках", () => {
    expect(css).toContain("--color-info-soft:");
    expect(tintClasses(sourceChipSource, "SOURCE_TINT").length).toBeGreaterThanOrEqual(6);
    expect(tintClasses(familiesTabSource, "FAMILY_STATUS_TINT").length).toBeGreaterThanOrEqual(9);
    expect(SYSTEM_BADGE).not.toBe("");
    expect(CLASSES.every((cls) => /^(bg|text|border)-/.test(cls))).toBe(true);
  });

  it("каждый класс заливки плашки ссылается на объявленный токен --color-…", () => {
    const missing = CLASSES.map(tokenOf).filter((token) => !css.includes(`${token}:`));
    expect(missing).toEqual([]);
  });
});

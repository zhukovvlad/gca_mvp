/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import pageSource from "./StageSummaryPage.tsx?raw";
import tableSource from "./StageSummaryTable.tsx?raw";
import trackSource from "./StageSummaryTrack.tsx?raw";
import cellSource from "./SummaryCell.tsx?raw";

/**
 * Каждая `var(--…)`, на которую ссылаются компоненты свода, обязана
 * существовать в палитре приложения.
 *
 * **По образцу** `frontend/src/pages/compare/chartTokens.test.ts` — та же
 * ловушка уже стоила диаграмме сравнения всей палитры (см. докстроку там):
 * компонент был написан с именами ИЗ МАКЕТА (`--accent-text`, `--accent-soft`,
 * `--accent-border`, `--action-text` — буквально из
 * `docs/superpowers/specs/2026-08-27-stage-summary-mockup.html`), а в теме
 * приложения (`frontend/src/index.css`) раздельные `--accent-primary-*`/
 * `--action-primary-*` объявлены НАПРЯМУЮ, и алиас без префикса `-primary-`
 * существует только внутри `@theme inline` как `--color-accent-text` и т.п. —
 * доступный Tailwind-классам (`text-accent-text`), но НЕ как голый
 * `var(--accent-text)` в инлайновом стиле. Опечатка такого рода не роняет ни
 * типы, ни сборку — `var()` c неразрешимым именем просто берёт начальное
 * значение свойства, и брошенная тень штриховки была бы видна только в
 * браузере.
 *
 * Цвет здесь ненаблюдаем в принципе (jsdom не считает каскад,
 * `docs/insights/unobservable-in-the-runner.md`) — тест проверяет ровно
 * существование ИМЕНИ переменной, что наблюдаемо как обычный текст в двух
 * файлах. Он не заменяет замер в браузере (задача 9, layout) — тот проверяет,
 * что штриховка и правда видна и читаема в обеих темах.
 *
 * Стилевик читается С ДИСКА, а не `?raw`-импортом: `?raw` на CSS под vitest
 * отдаёт ПУСТУЮ строку (подмена стилевых импортов заглушкой), и «все
 * переменные объявлены» выполнялось бы тогда на любом входе — эту дыру
 * закрывает премисный тест ниже.
 */
const css = readFileSync("src/index.css", "utf8");
const SOURCES: Record<string, string> = {
  "StageSummaryPage.tsx": pageSource,
  "StageSummaryTable.tsx": tableSource,
  "StageSummaryTrack.tsx": trackSource,
  "SummaryCell.tsx": cellSource,
};

function cssVarsUsed(): string[] {
  const found = Object.values(SOURCES).flatMap((source) => source.match(/var\(--[a-z0-9-]+\)/g) ?? []);
  return [...new Set(found)].map((v) => v.slice("var(".length, -1));
}

describe("свод по этапам — токены оформления", () => {
  it("предпосылка: палитра прочитана, компоненты действительно ссылаются на переменные", () => {
    // Читается ИМЕННО палитра приложения — без этой проверки опечатка в пути
    // дала бы пустую строку и «всё объявлено» на любом входе.
    expect(css).toContain("--border-subtle:");
    const used = cssVarsUsed();
    expect(used.length).toBeGreaterThan(0);
  });

  it("каждая переменная, на которую ссылаются компоненты свода, объявлена в index.css", () => {
    const missing = cssVarsUsed().filter((name) => !css.includes(`${name}:`));
    expect(missing).toEqual([]);
  });

  it("имена из МАКЕТА, которых в приложении нет напрямую, не должны появиться снова", () => {
    // Закрытый список — про конкретную ловушку (см. докстроку выше), не про
    // запрет вообще: общий запрет уже даёт тест выше.
    const fromMockup = ["--accent-text", "--accent-soft", "--accent-border", "--action-text"];
    const used = new Set(cssVarsUsed());
    expect(fromMockup.filter((name) => used.has(name))).toEqual([]);
  });
});

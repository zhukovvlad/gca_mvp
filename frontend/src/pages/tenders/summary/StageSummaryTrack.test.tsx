import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { sampleStageSummary } from "@/test/fixtures";
import type { StageSummary } from "@/types/domain";
import { StageSummaryTrack } from "./StageSummaryTrack";

/**
 * Трасса торга — высота столбика читается из `bar_height_pct` (спека
 * 2026-08-27-stage-summary-design.md §2.3–§2.4, §2.11; Global 13), а не
 * вычисляется делением `total` на максимум клиентом. Fix round 2, п.1 и п.5.
 */
describe("Трасса торга — высота из данных, не из деления клиентом (fix round 2)", () => {
  /**
   * Проверяет ТОЛЬКО что высоты фикстуры отрисовались как заявлено —
   * `sampleStageSummary.columns[*].bar_height_pct`. САМА ПО СЕБЕ эта проверка
   * НЕ различает «взято из поля» от «посчитано делением total/max клиентом»:
   * в реальной фикстуре `bar_height_pct` ВСЕГДА пропорционален `total`
   * (инвариант `fixtures.test.ts`, «bar_height_pct ratio equals total /
   * max_total» — таким его обязан прислать сервер), поэтому клиентское
   * деление дало бы ТУ ЖЕ высоту, что и поле. Различающий тест — ниже.
   */
  it("честный прогон: высоты фикстуры совпадают с bar_height_pct (не различает источник сама по себе)", () => {
    render(<StageSummaryTrack summary={sampleStageSummary} />);
    const bars = screen.getAllByTestId("track-bar");
    expect(bars.map((b) => b.style.height)).toEqual(
      sampleStageSummary.columns.map((c) => `${Number(c.bar_height_pct)}%`)
    );
  });

  /**
   * Различающий тест. Вход ЗАВЕДОМО НЕВОЗМОЖЕН в реальном ответе: у трёх
   * колонок РАВНЫЕ `total` ("100.00" у каждой), а значит любой сервер,
   * честно соблюдающий свой же контракт (bar_height_pct = total/max_total*100,
   * `fixtures.test.ts`), обязан был бы прислать ОДИНАКОВУЮ высоту у всех трёх
   * (100.0 у каждой, все три — максимум). Здесь `bar_height_pct` заявлены
   * РАЗНЫМИ (10.0 / 50.0 / 90.0) — намеренное противоречие тому, что дало бы
   * деление на клиенте (которое при равных total нарисовало бы три
   * ОДИНАКОВЫХ столбика по 100%). Если компонент рисует по 10/50/90 —
   * значит он читает `bar_height_pct`, а не пересчитывает отношение сам;
   * если бы он делил сам, все три столбика оказались бы 100% независимо от
   * заявленных чисел, и тест поймал бы это здесь — тем же приёмом, что
   * `StageSummaryTable.test.tsx` уже применяет к `state` против `amount`.
   */
  it("различающий вход: заявленная высота побеждает деление total/max — невозможная для сервера комбинация равных total и разных bar_height_pct", () => {
    const summary: StageSummary = structuredClone(sampleStageSummary);
    summary.columns[0].total = "100.00";
    summary.columns[0].bar_height_pct = "10.0";
    summary.columns[1].total = "100.00";
    summary.columns[1].bar_height_pct = "50.0";
    summary.columns[2].total = "100.00";
    summary.columns[2].bar_height_pct = "90.0";

    render(<StageSummaryTrack summary={summary} />);
    const bars = screen.getAllByTestId("track-bar");
    expect(bars.map((b) => b.style.height)).toEqual(["10%", "50%", "90%"]);
  });

  /**
   * Fix round 2, п.5. `bar_height_pct: null` на известной (не
   * `unknown_vat_base`) колонке при доступной трассе — комбинация, которую
   * сегодняшний бэкенд прислать не может (инвариант `fixtures.test.ts`), но
   * контракт поля обязан различать «нет высоты» и «высота — число ноль»
   * НЕЗАВИСИМО от того, случается ли это сегодня. `Number(null) === 0` дал
   * бы столбик высотой 0%, тождественный НАСТОЯЩЕМУ нулевому итогу — тест
   * проверяет, что вместо этого столбик просто не рисуется.
   */
  it("bar_height_pct=null на известной колонке — без столбика, а не столбик высотой 0% (искусственный вход)", () => {
    const summary: StageSummary = structuredClone(sampleStageSummary);
    summary.columns[1].bar_height_pct = null;

    render(<StageSummaryTrack summary={summary} />);
    const bars = screen.getAllByTestId("track-bar");
    // Остались столбики только у колонок 0 и 2 — у колонки 1 столбика нет вовсе.
    expect(bars).toHaveLength(2);
  });
});

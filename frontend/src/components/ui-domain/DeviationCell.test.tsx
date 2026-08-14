import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DeviationCell } from "./DeviationCell";

/**
 * Различение причин пустого отклонения (спека пересчёта §2.5, §10, задача 9).
 *
 * `DeviationCell` подписывал ЛЮБОЙ пустой результат как «нет норматива» — и это
 * стирало разницу между «норматива вовсе нет» (`no_standard`) и «норматив есть,
 * но база НДС строки неизвестна, сравнивать не с чем» (`unknown_vat_base`). Та же
 * потеря различия, за которую §10 уже платила на «нет норматива» против «0 %».
 */
describe("DeviationCell: различение причин пустого отклонения", () => {
  it("различает «нет норматива» и «неизвестна база НДС»", () => {
    const { rerender } = render(<DeviationCell value={null} reason="no_standard" />);
    expect(screen.getByText("нет норматива")).toBeInTheDocument();

    rerender(<DeviationCell value={null} reason="unknown_vat_base" />);
    expect(screen.getByText("неизвестна база НДС")).toBeInTheDocument();
    // Перерисовка меняет причину — прежний текст обязан уйти, а не остаться рядом.
    expect(screen.queryByText("нет норматива")).not.toBeInTheDocument();
  });

  it("подсказка компактного варианта тоже различает причины", () => {
    const { rerender } = render(
      <DeviationCell value={null} reason="unknown_vat_base" variant="compact" />
    );
    expect(screen.getByTitle(/база НДС не заявлена/i)).toBeInTheDocument();

    rerender(<DeviationCell value={null} reason="no_standard" variant="compact" />);
    expect(screen.queryByTitle(/база НДС не заявлена/i)).not.toBeInTheDocument();
    expect(screen.getByTitle(/Нет норматива на дату сметы/)).toBeInTheDocument();
  });

  it("без причины ведёт себя как раньше — «нет норматива»", () => {
    render(<DeviationCell value={null} />);
    expect(screen.getByText("нет норматива")).toBeInTheDocument();
  });

  /**
   * Райдер задачи 10 пересчёта НДС (найдено ревью задачи 9): `_fold_cell`
   * (`backend/crud/analytics.py:743`) умеет вернуть `deviation_reason:
   * "no_weight"` — защитная ветка на случай потери единственной строки с
   * положительным весом, недостижимая сегодня (CTE фильтрует `weight > 0`), но
   * код причины мог прийти с сервера уже сейчас. Без записи в типе и словаре
   * `REASON_TEXT["no_weight"]` был бы `undefined`, и деструктуризация
   * `{ full, title }` бросала бы `TypeError` вместо показа подписи.
   */
  it("различает «нет веса» — райдер задачи 9, защитная ветка _fold_cell", () => {
    expect(() => render(<DeviationCell value={null} reason="no_weight" />)).not.toThrow();
    expect(screen.getByText("нет веса")).toBeInTheDocument();
    expect(screen.queryByText("нет норматива")).not.toBeInTheDocument();
  });
});

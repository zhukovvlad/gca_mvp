import { roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

interface DeviationCellProps {
  /**
   * Отклонение из API — точная десятичная **строка** (§3, §4). `number`
   * принимается для совместимости, но серверные значения приходят строками:
   * `deviation_pct` в VIEW — `numeric`, и §4 требует округлять его только на слое
   * представления. Округление здесь целочисленное (`roundDecimalPercent`).
   *
   * `null` означает «нет норматива», а не «ноль»: §10 требует, чтобы эти два случая
   * были различимы, и здесь они различимы по построению — у нуля есть цифра, у
   * отсутствия норматива её нет.
   */
  value: string | number | null | undefined;
  /**
   * `full` — словами «нет норматива» (паспорт, drill-down: место есть, и человек
   * читает документ, а не таблицу). `compact` — прочерк с подсказкой (ячейка
   * матрицы, где на счету каждый символ).
   */
  variant?: "full" | "compact";
  className?: string;
}

const NO_STANDARD_TITLE = "Нет норматива на дату сметы — сравнивать не с чем";

export function DeviationCell({ value, variant = "full", className }: DeviationCellProps) {
  const rounded = roundDecimalPercent(value);

  if (rounded === null) {
    return (
      <span
        className={cn("text-fg-tertiary", variant === "full" && "text-xs", className)}
        title={NO_STANDARD_TITLE}
      >
        {variant === "full" ? "нет норматива" : "—"}
      </span>
    );
  }

  const tone =
    rounded.sign > 0
      ? "text-warning-text"
      : rounded.sign < 0
        ? "text-accent-text"
        : "text-fg-tertiary";

  return (
    <span className={cn("font-mono tabular-nums font-medium", tone, className)}>
      {rounded.text}
    </span>
  );
}

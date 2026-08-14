import { roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Почему отклонения нет (спека пересчёта §2.5, §10). Два разных факта нельзя
 * сводить к одному прочерку:
 * - `no_standard` — норматива на дату сметы вовсе нет, сравнивать не с чем;
 * - `unknown_vat_base` — норматив есть, но база НДС строки неизвестна, и
 *   нетто, с которым его сравнивают, вывести не из чего.
 *
 * `undefined` (проп `reason` не передан) — старые вызовы вне этой фичи; для
 * них поведение прежнее — «нет норматива».
 */
export type DeviationReason = "no_standard" | "unknown_vat_base";

const REASON_TEXT: Record<DeviationReason, { full: string; title: string }> = {
  no_standard: {
    full: "нет норматива",
    title: "Нет норматива на дату сметы — сравнивать не с чем",
  },
  unknown_vat_base: {
    full: "неизвестна база НДС",
    title: "База НДС не заявлена в файле и не назначена — нетто вывести не из чего",
  },
};

interface DeviationCellProps {
  /**
   * Отклонение из API — точная десятичная **строка** (§3, §4). `number`
   * принимается для совместимости, но серверные значения приходят строками:
   * `deviation_pct` в VIEW — `numeric`, и §4 требует округлять его только на слое
   * представления. Округление здесь целочисленное (`roundDecimalPercent`).
   *
   * `null` означает «отклонения нет» — по КАКОЙ причине, называет `reason`, а не
   * «ноль»: §10 требует, чтобы эти два случая были различимы, и здесь они
   * различимы по построению — у нуля есть цифра, у отсутствия отклонения её нет.
   */
  value: string | number | null | undefined;
  /** Почему отклонения нет, когда `value` пуст. См. {@link DeviationReason}. */
  reason?: DeviationReason;
  /**
   * `full` — словами «нет норматива» (паспорт, drill-down: место есть, и человек
   * читает документ, а не таблицу). `compact` — прочерк с подсказкой (ячейка
   * матрицы, где на счету каждый символ).
   */
  variant?: "full" | "compact";
  className?: string;
}

export function DeviationCell({ value, reason, variant = "full", className }: DeviationCellProps) {
  const rounded = roundDecimalPercent(value);

  if (rounded === null) {
    const { full, title } = REASON_TEXT[reason ?? "no_standard"];
    return (
      <span
        className={cn("text-fg-tertiary", variant === "full" && "text-xs", className)}
        title={title}
      >
        {variant === "full" ? full : "—"}
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

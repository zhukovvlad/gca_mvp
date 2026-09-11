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
 *
 * `no_weight` — ПЕРЕЖИТОК: с фичи правила цены агрегированная ячейка матрицы
 * этот код в `deviation_reason` не отдаёт вовсе — он переехал в `rate_reason`
 * вместе с остальными причинами отсутствия САМОЙ ставки (спека правила цены
 * §2.7, `docs/superpowers/specs/2026-09-09-price-predicate-design.md`), и тип
 * `MatrixCell.deviation_reason` такого значения уже не допускает. Запись
 * оставлена, потому что компонент общий и вызывается не только матрицей;
 * сегодня её достигает только собственный юнит-тест этого файла. Прежний
 * довод — «защитная ветка на случай, если группа ячейки лишится единственной
 * строки с положительным весом» — описывал механизм, которого больше нет.
 *
 * `not_finite` — Дефект 1, ре-ревью Codex (PR #21): норматив может БЫТЬ, база
 * НДС может быть ИЗВЕСТНА, а сама величина просто не число, и смешивать это с
 * «нет норматива» или «неизвестна база» значило бы солгать о причине ровно тем
 * способом, против которого заведена вся эта пара кодов. Носителей у кода
 * сегодня два, и они РАЗНЫЕ:
 * - `_net_deviation` (паспорт, drill-down) — нефинитна собственная цена строки;
 * - агрегированная ячейка — нефинитен САМ НОРМАТИВ при живой ставке. Прежний
 *   механизм «`SUM`/деление распространяют нефинитность на средневзвешенную»
 *   после правила цены даёт `rate_reason`, а не эту причину: нефинитная цена
 *   до расчёта ставки больше не доезжает.
 */
export type DeviationReason = "no_standard" | "unknown_vat_base" | "no_weight" | "not_finite";

const REASON_TEXT: Record<DeviationReason, { full: string; title: string }> = {
  no_standard: {
    full: "нет норматива",
    title: "Нет норматива на дату сметы — сравнивать не с чем",
  },
  unknown_vat_base: {
    full: "неизвестна база НДС",
    title: "База НДС не заявлена в файле и не назначена — нетто вывести не из чего",
  },
  no_weight: {
    full: "нет веса",
    title: "Ни одна строка ячейки не несёт положительного веса — средневзвешенную ставку вывести не из чего",
  },
  not_finite: {
    full: "не число",
    title: "Величина — NaN/Infinity, а не число: сравнивать с нормативом нечего",
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

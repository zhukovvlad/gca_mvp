import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

interface MoneyCellProps {
  /**
   * Десятичная строка из API (AGENTS.md §3) либо число. Строка форматируется
   * без перевода в `number` — иначе последний разряд суммы терялся бы молча.
   */
  value: string | number | null | undefined;
  currency?: string;
  className?: string;
}

export function MoneyCell({ value, currency, className }: MoneyCellProps) {
  return (
    <span className={cn("font-mono tabular-nums", className)}>
      {formatDecimalMoney(value, currency)}
    </span>
  );
}

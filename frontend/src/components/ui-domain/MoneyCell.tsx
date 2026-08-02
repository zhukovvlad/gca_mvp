import { formatMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

interface MoneyCellProps {
  value: number | null | undefined;
  currency?: string;
  className?: string;
}

export function MoneyCell({ value, currency, className }: MoneyCellProps) {
  return (
    <span className={cn("font-mono tabular-nums", className)}>
      {formatMoney(value, currency)}
    </span>
  );
}

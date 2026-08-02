import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

interface DeviationCellProps {
  value: number | null | undefined;
  className?: string;
}

export function DeviationCell({ value, className }: DeviationCellProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn("text-fg-tertiary", className)}>—</span>;
  }
  const tone =
    value > 0 ? "text-warning-text" : value < 0 ? "text-accent-text" : "text-fg-tertiary";
  return (
    <span className={cn("font-mono tabular-nums font-medium", tone, className)}>
      {formatPercent(value, true)}
    </span>
  );
}

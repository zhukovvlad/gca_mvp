import { StatusPill, type StatusTone } from "./StatusPill";

interface ConfidenceBadgeProps {
  value: number | null | undefined;
}

export function ConfidenceBadge({ value }: ConfidenceBadgeProps) {
  if (value === null || value === undefined) {
    return <span className="text-fg-tertiary">—</span>;
  }
  const pct = Math.round(value * 100);
  const tone: StatusTone =
    value >= 0.85 ? "success" : value >= 0.7 ? "warning" : "danger";
  return <StatusPill tone={tone} label={`${pct}%`} />;
}

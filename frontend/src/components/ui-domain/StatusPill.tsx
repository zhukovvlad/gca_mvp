import { cn } from "@/lib/utils";

export type StatusTone =
  | "success"
  | "warning"
  | "danger"
  | "neutral"
  | "info"
  | "accent";

interface StatusPillProps {
  tone: StatusTone;
  label: string;
  dot?: boolean;
  className?: string;
}

const TONE: Record<StatusTone, { bg: string; border: string; text: string; dot: string }> = {
  success: {
    bg: "bg-accent-soft",
    border: "border-accent-border",
    text: "text-accent-text",
    dot: "bg-accent",
  },
  warning: {
    bg: "bg-warning-soft",
    border: "border-warning-border",
    text: "text-warning-text",
    dot: "bg-warning",
  },
  danger: {
    bg: "bg-danger-soft",
    border: "border-danger-border",
    text: "text-danger-text",
    dot: "bg-danger",
  },
  neutral: {
    bg: "bg-neutral-soft",
    border: "border-neutral-border",
    text: "text-neutral-text",
    dot: "bg-neutral-dot",
  },
  info: {
    bg: "bg-info-soft",
    border: "border-info-border",
    text: "text-info-text",
    dot: "bg-info",
  },
  accent: {
    bg: "bg-accent-soft",
    border: "border-accent-border",
    text: "text-accent-text",
    dot: "bg-accent",
  },
};

export function StatusPill({ tone, label, dot, className }: StatusPillProps) {
  const c = TONE[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium",
        c.bg,
        c.border,
        c.text,
        className
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", c.dot)} />}
      {label}
    </span>
  );
}

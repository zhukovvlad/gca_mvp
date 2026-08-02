import { cn } from "@/lib/utils";

interface FilterPillProps {
  active: boolean;
  label: string;
  count?: number;
  onClick: () => void;
  tone?: "default" | "warning";
}

export function FilterPill({
  active,
  label,
  count,
  onClick,
  tone = "default",
}: FilterPillProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus-ring",
        active
          ? tone === "warning"
            ? "border-warning-border bg-warning-soft text-warning-text"
            : "border-accent-border bg-accent-soft text-accent-text"
          : "border-border-subtle bg-transparent text-fg-secondary hover:bg-surface-hover hover:text-fg"
      )}
    >
      {label}
      {count !== undefined && (
        <span className="ml-1.5 text-fg-tertiary">· {count}</span>
      )}
    </button>
  );
}

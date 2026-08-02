import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-border-subtle bg-surface px-6 py-16 text-center",
        className
      )}
    >
      {icon && (
        <div className="mb-3 grid h-10 w-10 place-items-center rounded-md bg-surface-sunken text-fg-tertiary">
          {icon}
        </div>
      )}
      <h3 className="text-md font-medium text-fg">{title}</h3>
      {description && (
        <p className="mt-1 max-w-md text-sm text-fg-secondary">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

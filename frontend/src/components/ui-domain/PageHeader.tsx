import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  serif?: boolean;
}

export function PageHeader({ title, subtitle, actions, serif }: PageHeaderProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border-subtle pb-4">
      <div className="min-w-0">
        <h1
          className={cn(
            "text-3xl text-fg",
            serif ? "font-serif font-medium tracking-tight" : "font-medium"
          )}
        >
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-fg-secondary">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TabItem<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface TabsProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  tabs: TabItem<T>[];
  children?: ReactNode;
  className?: string;
}

export function Tabs<T extends string>({
  value,
  onValueChange,
  tabs,
  children,
  className,
}: TabsProps<T>) {
  return (
    <div className={className}>
      <div className="flex gap-1 border-b border-border-subtle">
        {tabs.map((tab) => {
          const active = tab.value === value;
          return (
            <button
              key={tab.value}
              type="button"
              onClick={() => onValueChange(tab.value)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150",
                active
                  ? "border-accent text-fg"
                  : "border-transparent text-fg-secondary hover:text-fg"
              )}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span className="ml-1.5 text-fg-tertiary">· {tab.count}</span>
              )}
            </button>
          );
        })}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}

import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface SurfaceProps extends HTMLAttributes<HTMLDivElement> {
  tone?: "default" | "sunken";
  padding?: "none" | "sm" | "md" | "lg";
}

const TONE = {
  default: "bg-surface",
  sunken: "bg-surface-sunken",
};

const PADDING = {
  none: "",
  sm: "p-4",
  md: "p-6",
  lg: "p-8",
};

export function Surface({
  tone = "default",
  padding = "md",
  className,
  ...rest
}: SurfaceProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle",
        TONE[tone],
        PADDING[padding],
        className
      )}
      {...rest}
    />
  );
}

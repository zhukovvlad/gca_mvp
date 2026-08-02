import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  loading?: boolean;
}

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-action text-action-text hover:bg-action-hover disabled:opacity-50",
  secondary:
    "border border-border-default bg-surface text-fg hover:bg-surface-hover disabled:opacity-50",
  ghost:
    "text-fg-secondary hover:bg-surface-hover hover:text-fg disabled:opacity-50",
  danger:
    "bg-danger-soft text-danger-text border border-danger-border hover:bg-danger/10 disabled:opacity-50",
};

const SIZE: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1",
  md: "h-8 px-3 text-sm gap-1.5",
  lg: "h-10 px-4 text-sm gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "primary",
      size = "md",
      leftIcon,
      rightIcon,
      loading,
      disabled,
      className,
      children,
      ...rest
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center rounded-md font-medium transition-colors duration-150 focus-ring disabled:cursor-not-allowed",
          VARIANT[variant],
          SIZE[size],
          className
        )}
        {...rest}
      >
        {loading ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          leftIcon
        )}
        {children}
        {rightIcon}
      </button>
    );
  }
);
Button.displayName = "Button";

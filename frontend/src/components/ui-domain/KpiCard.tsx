import type { ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  label: string;
  value?: string;
  delta?: { value: string; tone: "up" | "down" | "neutral" };
  suffix?: ReactNode;
  /** Подпись под основным значением, отдельной строкой (в один ряд, без переноса) —
   * для длинных пояснений вроде «70% оборота объекта», которые ломаются как inline-suffix. */
  caption?: ReactNode;
  valueClassName?: string;
  className?: string;
  breakdown?: { label: string; value: string; onClick?: () => void }[];
  /** Мультизначный вариант (KPI «Объёмы»): строка на направление — имя слева,
   * значение в родной единице справа. Имя в именительном падеже («Бетон — 5 677,5 м³»):
   * никаких склонений в коде — формат масштабируется на любое будущее направление. */
  values?: { label: string; value: string }[];
}

export function KpiCard({ label, value, delta, suffix, caption, valueClassName, className, breakdown, values }: KpiCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle bg-surface p-5",
        className
      )}
    >
      <div className="text-2xs uppercase tracking-wider text-fg-tertiary">
        {label}
      </div>
      {values && values.length > 0 ? (
        <div className="mt-2 space-y-1">
          {values.map((v) => (
            <div key={v.label} className="flex items-baseline justify-between gap-2 leading-snug">
              <span className="text-sm text-fg-secondary">{v.label}</span>
              <span className="font-mono text-lg text-fg">{v.value}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className={cn("mt-2 font-mono text-2xl text-fg", valueClassName)}>
          {value}
          {suffix && <span className="ml-1.5 text-sm font-normal text-fg-secondary">{suffix}</span>}
        </div>
      )}
      {caption && (
        <div className="mt-1 whitespace-nowrap text-xs text-fg-tertiary">{caption}</div>
      )}
      {breakdown && breakdown.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {breakdown.map((b) =>
            b.onClick ? (
              <button
                key={b.label}
                type="button"
                onClick={b.onClick}
                className="flex w-full items-center justify-between gap-2 text-xs text-accent-text hover:underline"
              >
                <span>{b.label}</span>
                <span className="font-mono">{b.value}</span>
              </button>
            ) : (
              <div key={b.label} className="flex items-center justify-between gap-2 text-xs text-fg-tertiary">
                <span>{b.label}</span>
                <span className="font-mono">{b.value}</span>
              </div>
            ),
          )}
        </div>
      )}
      {delta && (
        <div
          className={cn(
            "mt-1 inline-flex items-center gap-1 text-xs",
            delta.tone === "up" && "text-warning",
            delta.tone === "down" && "text-accent",
            delta.tone === "neutral" && "text-fg-tertiary"
          )}
        >
          {delta.tone === "up" && <ArrowUp size={12} />}
          {delta.tone === "down" && <ArrowDown size={12} />}
          {delta.value}
        </div>
      )}
    </div>
  );
}

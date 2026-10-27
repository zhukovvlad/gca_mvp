import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";

/**
 * Серый слой пересчёта поверх прежних цифр (спека 4а §2.15): ряд ИПЦ сменился,
 * новый ответ ещё идёт, а старые числа остаются видны под слоем. Слой гасит ввод
 * и называет, чего ждём; без запроса (переключатель НДС) его нет.
 */
export function RecalcOverlay({
  active,
  label,
  children,
}: {
  active: boolean;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="relative" aria-busy={active || undefined}>
      {children}
      {active && (
        <div
          role="status"
          aria-live="polite"
          data-testid="recalc-overlay"
          className="absolute inset-0 z-10 flex cursor-progress items-start justify-center rounded-lg bg-fg-secondary/30 pt-24"
        >
          <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-sm shadow-md">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span>{label}</span>
          </div>
        </div>
      )}
    </div>
  );
}

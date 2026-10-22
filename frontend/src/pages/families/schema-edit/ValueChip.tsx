import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface ValueChipProps {
  text: string;
  /** Число строк каталога на значении; `null` — значения ещё нет в схеме (новое). */
  rows: number | null;
  /** Значение, дописанное моделью: оранжевая плашка. */
  extension?: boolean;
  /** Действия справа от числа: меню «⋯» или «убрать». */
  children?: ReactNode;
}

/**
 * Плашка значения (спека 3в §2.11): текст и число строк; пунктир — значение без строк, оранжевая —
 * дописанное моделью, зелёная «новое» — ещё не сохранённое. Основа — `Badge`.
 */
export function ValueChip({ text, rows, extension = false, children }: ValueChipProps) {
  const isNew = rows === null;
  return (
    <Badge
      variant="outline"
      data-origin={extension ? "extension" : undefined}
      className={cn(
        "h-6.5 gap-1.5 rounded-md bg-surface-sunken pr-1 pl-2 font-normal",
        !isNew && rows === 0 && "border-dashed bg-transparent text-fg-tertiary",
        extension && "border-warning-border bg-warning-soft text-warning-text",
        isNew && "border-accent-border bg-accent-soft text-accent-text"
      )}
    >
      {text}
      {isNew ? (
        <span className="text-[11px] opacity-80">новое</span>
      ) : (
        <span className="font-mono text-[11px] opacity-70">{rows.toLocaleString("ru-RU")}</span>
      )}
      {extension && <span className="text-[11px]">дописано моделью</span>}
      {children}
    </Badge>
  );
}

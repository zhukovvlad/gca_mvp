import { useState } from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { MarkKind } from "@/types/domain";

import {
  MARK_CANCEL,
  MARK_REASON_LABEL,
  MARK_REASON_MAX,
  MARK_SUBMIT,
  markReasonCounter,
} from "./ourPriceCopy";

/** Вид окна: отметка «не аналог» либо «не ошибка» подозрительной строки (спека подозрительных строк, раздел 5 п. 2). */
export type MarkDialogKind = MarkKind | "not_error";

export interface MarkDialogProps {
  kind: MarkDialogKind;
  title: string;
  /** Область действия отметки: какая строка, для кого и где перестаёт быть аналогом. */
  scopeText: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Причина без пробелов по краям: 1-300 символов. */
  onSubmit: (reason: string) => void;
  /** Запрос идёт: кнопка подтверждения занята до ответа. */
  busy: boolean;
  /** Подпись кнопки подтверждения; по умолчанию «Исключить». */
  submitLabel?: string;
}

/** Поле причины живёт внутри содержимого окна: закрытое окно размонтировано, причина не залипает. */
function MarkForm({ kind, title, scopeText, onSubmit, busy, submitLabel = MARK_SUBMIT }: Omit<MarkDialogProps, "open" | "onOpenChange">) {
  const [value, setValue] = useState("");
  const reason = value.trim();
  const valid = reason.length >= 1 && reason.length <= MARK_REASON_MAX;
  const fieldId = `mark-reason-${kind}`;
  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>{title}</AlertDialogTitle>
        <AlertDialogDescription data-testid="mark-scope">{scopeText}</AlertDialogDescription>
      </AlertDialogHeader>
      <div className="space-y-1">
        <Label htmlFor={fieldId}>{MARK_REASON_LABEL}</Label>
        <Textarea
          id={fieldId}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          aria-invalid={reason.length > MARK_REASON_MAX}
          rows={3}
        />
        <p
          data-testid="mark-counter"
          className={reason.length > MARK_REASON_MAX ? "text-xs text-destructive" : "text-xs text-fg-secondary"}
        >
          {markReasonCounter(reason.length)}
        </p>
      </div>
      <AlertDialogFooter>
        <AlertDialogCancel render={<Button variant="outline">{MARK_CANCEL}</Button>} />
        <Button type="button" data-testid="mark-submit" disabled={!valid || busy} onClick={() => onSubmit(reason)}>
          {submitLabel}
        </Button>
      </AlertDialogFooter>
    </>
  );
}

/**
 * Окно отметки строки договора (спека «Не аналог» §8 п. 1): текст области действия, причина
 * обязательная, 1-300 символов после обрезки краёв, счётчик; «Исключить» занято до ответа.
 */
export function MarkDialog({ open, onOpenChange, ...form }: MarkDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <MarkForm {...form} />
      </AlertDialogContent>
    </AlertDialog>
  );
}

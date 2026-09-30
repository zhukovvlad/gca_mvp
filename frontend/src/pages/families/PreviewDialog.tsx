import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatUsd } from "@/lib/format";
import { apiErrorCode, useReaskConfirm, useReaskPreview } from "@/services/queries";
import type { PreviewTarget } from "@/types/domain";

import { BATCH_SOURCE_LABEL } from "./labels";

const PREVIEW_CHANGED_NOTICE =
  "Оценка изменилась, пока окно было открыто. Проверьте новые числа и подтвердите ещё раз.";

function titleOf(target: PreviewTarget, unitLabel: string): string {
  switch (target.kind) {
    case "unit":
      return `Перезапросить ${unitLabel}?`;
    case "config":
      return "Перезапросить всё?";
    case "batch":
      return "Поставить удержанную пачку?";
  }
}

function descriptionOf(target: PreviewTarget, unitLabel: string): string {
  switch (target.kind) {
    case "unit":
      return `Модель заново выберет семью для контекстов единицы ${unitLabel}, у которых нет семьи, назначенной человеком. Подтверждённые контексты не трогаются.`;
    case "config":
      return "Промпт, модель или параметры изменились: заново будут запрошены все применимые контексты. Подтверждённые человеком не трогаются.";
    case "batch":
      return "Задания ставятся по текущим отпечаткам входа, а не по сохранённым в момент импорта. Если бюджета не хватит, часть заданий подождёт следующих суток.";
  }
}

function confirmLabelOf(target: PreviewTarget): string {
  return target.kind === "batch" ? "Поставить" : "Поставить в очередь";
}

interface PreviewDialogProps {
  /** Что перезапрашивается; `null` — диалог закрыт. */
  target: PreviewTarget | null;
  /** Читаемая подпись единицы для заголовка (`м²`); нужна только для `kind: "unit"`. */
  unitLabel?: string;
  onClose: () => void;
}

/**
 * Общий диалог трёх действий, ставящих задания на деньги: перезапрос единицы,
 * перезапрос по конфигурации, постановка удержанной пачки (спека
 * semantic-suggestions §2.10). Открытие запрашивает preview; подтверждение
 * шлёт `preview_hash` ИЗ ПОКАЗАННОГО preview. Ответ `409 preview_changed` не
 * повторяет подтверждение молча: диалог сообщает «оценка изменилась» и
 * запрашивает preview заново, человек видит новые числа и решает сам.
 */
export function PreviewDialog({ target, unitLabel = "", onClose }: PreviewDialogProps) {
  const preview = useReaskPreview();
  const confirm = useReaskConfirm();
  const [notice, setNotice] = useState<string | null>(null);

  const { mutate: requestPreview, reset: resetPreview } = preview;
  const { reset: resetConfirm } = confirm;

  useEffect(() => {
    if (target === null) return;
    requestPreview(target);
    return () => {
      resetPreview();
      resetConfirm();
    };
  }, [target, requestPreview, resetPreview, resetConfirm]);

  function handleConfirm() {
    if (target === null || !preview.data) return;
    confirm.mutate(
      { target, previewHash: preview.data.preview_hash },
      {
        onSuccess: () => {
          setNotice(null);
          onClose();
        },
        onError: (error) => {
          if (apiErrorCode(error) === "preview_changed") {
            setNotice(PREVIEW_CHANGED_NOTICE);
            requestPreview(target);
          }
        },
      }
    );
  }

  function handleOpenChange(open: boolean) {
    if (open) return;
    setNotice(null);
    onClose();
  }

  const data = preview.data;
  const busy = preview.isPending || confirm.isPending;

  return (
    <Dialog open={target !== null} onOpenChange={handleOpenChange}>
      {target !== null && (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{titleOf(target, unitLabel)}</DialogTitle>
            <DialogDescription>
              {target.kind === "batch" ? BATCH_SOURCE_LABEL[target.source] : descriptionOf(target, unitLabel)}
            </DialogDescription>
          </DialogHeader>

          {notice && (
            <p role="status" className="rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text">
              {notice}
            </p>
          )}

          {preview.isError && (
            <p role="alert" className="text-sm text-danger-text">
              Не удалось получить оценку. Закройте окно и откройте его заново.
            </p>
          )}

          {preview.isPending && !data && <Skeleton className="h-20 w-full" />}

          {data && (
            <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
              <dt className="text-fg-secondary">Контекстов</dt>
              <dd className="text-right font-medium tabular-nums">{data.context_count.toLocaleString("ru-RU")}</dd>
              <dt className="text-fg-secondary">Резерв</dt>
              <dd className="text-right font-medium tabular-nums">{formatUsd(data.reserve_usd)}</dd>
              <dt className="text-fg-secondary">Ожидаемая цена при попадании в кэш</dt>
              <dd className="text-right font-medium tabular-nums">≈ {formatUsd(data.expected_cached_usd)}</dd>
            </dl>
          )}

          {target.kind === "batch" && (
            <p className="text-sm text-fg-secondary">{descriptionOf(target, unitLabel)}</p>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Отмена
            </Button>
            <Button disabled={busy || !data} onClick={handleConfirm}>
              {confirmLabelOf(target)}
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}

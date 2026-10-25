import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import type { OurPriceRound } from "@/types/domain";

import { PROGRESS_ARIA_LABEL, progressCaption, waitingAdminCaption } from "./ourPriceCopy";

/**
 * Плашка «каталог ещё разбирает» (спека 4а §2.11): видна, пока есть строки
 * `in_progress`. Прогресс — `(строки − in_progress) / строки`: счётчики строк, не
 * деньги. Опрашивает не она, а хук экрана; погасла — значит, ответ уже без сигнала.
 */
export function CatalogProgressAlert({ progress }: { progress: OurPriceRound["progress"] }) {
  const { lines_total: total, in_progress: inProgress, waiting_admin: waiting } = progress;
  if (total <= 0 || inProgress <= 0) return null;
  const done = total - inProgress;
  const percent = Math.round((done / total) * 100);
  return (
    <Alert data-testid="catalog-progress" className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
        <span>{progressCaption(done, total)}</span>
        {waiting > 0 && <span className="text-fg-secondary">· {waitingAdminCaption(waiting)}</span>}
      </div>
      <Progress value={percent} aria-label={PROGRESS_ARIA_LABEL} />
    </Alert>
  );
}

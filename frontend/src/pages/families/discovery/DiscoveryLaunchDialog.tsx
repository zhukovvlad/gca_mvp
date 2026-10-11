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
import { apiErrorCode, useDiscoveryPreview, useLaunchDiscovery } from "@/services/queries";

const PREVIEW_CHANGED_NOTICE =
  "Оценка изменилась, пока окно было открыто. Проверьте новые числа и подтвердите ещё раз.";

/** Что уходит наружу (спека 3б §2.13): имена, статьи, пути разделов, активные семьи и категории — без цен и объёмов. */
const OUTBOUND_TEXT = "имена, статьи, пути разделов, активные семьи и категории";

export interface DiscoveryTarget {
  unitId: number | null;
  /** Читаемая подпись единицы (`компл`) для заголовка и сообщений. */
  unitLabel: string;
}

interface DiscoveryLaunchDialogProps {
  /** Единица, для которой открываются семьи; `null` — окно закрыто. Ссылка обязана быть стабильной. */
  target: DiscoveryTarget | null;
  onClose: () => void;
}

function formatCount(n: number): string {
  return n.toLocaleString("ru-RU");
}

/**
 * Окно «Открыть семьи» (экран 2 макета): сначала цена и что уходит наружу, потом запуск —
 * тот же протокол, что у {@link PreviewDialog}. Открытие запрашивает preview; запуск шлёт
 * `preview_hash` ИЗ ПОКАЗАННОГО preview. `409 preview_changed` не повторяется молча: окно
 * сообщает «оценка изменилась» и запрашивает preview заново — человек видит новые числа и
 * решает сам. Ничего не активируется само: модель присылает черновики на просмотр.
 */
export function DiscoveryLaunchDialog({ target, onClose }: DiscoveryLaunchDialogProps) {
  const preview = useDiscoveryPreview();
  const launch = useLaunchDiscovery();
  const [notice, setNotice] = useState<string | null>(null);

  const { mutate: requestPreview, reset: resetPreview } = preview;
  const { reset: resetLaunch } = launch;

  useEffect(() => {
    if (target === null) return;
    requestPreview(target.unitId);
    return () => {
      resetPreview();
      resetLaunch();
    };
  }, [target, requestPreview, resetPreview, resetLaunch]);

  function handleConfirm() {
    if (target === null || !preview.data) return;
    launch.mutate(
      {
        unitId: target.unitId,
        unitLabel: target.unitLabel,
        previewHash: preview.data.preview_hash,
      },
      {
        onSuccess: () => {
          setNotice(null);
          onClose();
        },
        onError: (error) => {
          if (apiErrorCode(error) !== "preview_changed") return;
          setNotice(PREVIEW_CHANGED_NOTICE);
          requestPreview(target.unitId);
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
  const busy = preview.isPending || launch.isPending;

  return (
    <Dialog open={target !== null} onOpenChange={handleOpenChange}>
      {target !== null && (
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Открыть семьи · {target.unitLabel}</DialogTitle>
            <DialogDescription>
              Модель сгруппирует имена без семьи в черновики семей с определениями. Ничего не
              активируется само: черновики придут на просмотр.
            </DialogDescription>
          </DialogHeader>

          {notice && (
            <p
              role="status"
              className="rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
            >
              {notice}
            </p>
          )}

          {preview.isError && (
            <p role="alert" className="text-sm text-danger-text">
              Не удалось получить оценку. Закройте окно и откройте его заново.
            </p>
          )}

          {preview.isPending && !data && <Skeleton className="h-24 w-full" />}

          {data && (
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
              {data.counts.systems > 0 && (
                <>
                  <dt className="text-fg-secondary">Систем без семьи</dt>
                  <dd className="font-medium tabular-nums">
                    {formatCount(data.counts.systems)}
                  </dd>
                </>
              )}
              {data.counts.new_family + data.counts.bare > 0 && (
                <>
                  <dt className="text-fg-secondary">Строк без семьи</dt>
                  <dd className="font-medium tabular-nums">
                    {formatCount(data.counts.new_family + data.counts.bare)}
                  </dd>
                </>
              )}
              <dt className="text-fg-secondary">Различных наименований</dt>
              <dd className="font-medium tabular-nums">{formatCount(data.counts.names)}</dd>
              <dt className="text-fg-secondary">Активных семей единицы</dt>
              <dd className="font-medium tabular-nums">
                {formatCount(data.active_families)}
                <span className="font-normal text-fg-tertiary"> — модель их видит</span>
              </dd>
              {data.counts.uncategorized_families > 0 && (
                <>
                  <dt className="text-fg-secondary">Семей без категории</dt>
                  <dd className="font-medium tabular-nums">
                    {formatCount(data.counts.uncategorized_families)}
                  </dd>
                </>
              )}
              <dt className="text-fg-secondary">Уходит наружу</dt>
              <dd>{OUTBOUND_TEXT}</dd>
              <dt className="text-fg-secondary">Резерв</dt>
              <dd className="font-medium tabular-nums">{formatUsd(data.reserve_usd)}</dd>
              <dt className="text-fg-secondary">Ожидаемая цена при попадании в кэш</dt>
              <dd className="font-medium tabular-nums">
                ≈ {formatUsd(data.expected_cached_usd)}
              </dd>
            </dl>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Отмена
            </Button>
            <Button disabled={busy || !data} onClick={handleConfirm}>
              Открыть семьи
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}

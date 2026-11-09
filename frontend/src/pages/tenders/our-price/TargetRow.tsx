import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeleteTarget, usePutTarget } from "@/services/queries";
import type { OurPriceAnalog, OurPriceLine } from "@/types/domain";

import {
  TARGET_AUTO_TAG,
  TARGET_BACK_TO_LOWEST,
  TARGET_HINT,
  TARGET_LABEL,
  TARGET_MAKE_FRESHEST,
  TARGET_TEXT,
  targetAnalogText,
  targetInactiveNote,
} from "./ourPriceCopy";

export interface TargetRowProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  /** Ряд ИПЦ экрана: уходит в `PUT …/target`, остаток аналогов от него зависит. */
  seriesId: number | null;
  line: OurPriceLine;
  analogs: OurPriceAnalog[];
  canEdit: boolean;
}

/**
 * Цель строки-работы в блоке «Для выгрузки» (спека лестницы аналогов §2.14, §2.20): чем сейчас
 * считается наша расценка, кнопки «Сделать целью самую свежую» и «Вернуть к самой низкой» у
 * `admin` и плашка «Выбор не действует: {причина}». Выбор цели есть только у сравнимой строки.
 */
export function TargetRow({ tenderId, roundId, offerId, seriesId, line, analogs, canEdit }: TargetRowProps) {
  const put = usePutTarget(tenderId, roundId, offerId, seriesId);
  const del = useDeleteTarget(tenderId, roundId, offerId);
  const busy = put.isPending || del.isPending;
  const choice = line.choice;
  const kind = line.target_kind;
  const activeChoice = choice !== null && choice.active ? choice : null;
  const comparable = line.state === "comparable";

  if (kind === null && choice === null) return null;

  let text: string = kind === null ? "" : TARGET_TEXT[kind];
  if (kind === "analog") {
    const analog = analogs.find((a) => a.analog_id === activeChoice?.analog_id);
    text = targetAnalogText(analog?.object_title ?? null, analog?.number ?? null);
  }
  const canFreshest = canEdit && comparable && line.freshest !== null && activeChoice?.kind !== "freshest";
  const canBack = canEdit && choice !== null;

  return (
    <div data-testid="target-row" className="space-y-2 rounded-lg border border-accent-border bg-accent-soft px-3 py-2.5">
      {kind !== null && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span>
            <b>{TARGET_LABEL}:</b> {text}
          </span>
          {kind === "lowest" && <Badge variant="outline">{TARGET_AUTO_TAG}</Badge>}
        </p>
      )}
      {choice !== null && !choice.active && (
        <div
          role="note"
          data-testid="target-inactive"
          className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
        >
          {targetInactiveNote(choice.reset_reason)}
        </div>
      )}
      {(canFreshest || canBack) && (
        <div className="flex flex-wrap items-center gap-2">
          {canFreshest && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => put.mutate({ positionItemId: line.position_item_id, kind: "freshest" })}
            >
              {TARGET_MAKE_FRESHEST}
            </Button>
          )}
          {canBack && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => del.mutate({ positionItemId: line.position_item_id })}
            >
              {TARGET_BACK_TO_LOWEST}
            </Button>
          )}
          {canEdit && comparable && <span className="text-xs text-fg-secondary">{TARGET_HINT}</span>}
        </div>
      )}
    </div>
  );
}

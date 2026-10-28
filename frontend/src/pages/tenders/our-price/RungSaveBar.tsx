import { Button } from "@/components/ui/button";
import { useDeleteRung, usePutRung } from "@/services/queries";
import type { OurPriceLine, RungLevel, RungScope } from "@/types/domain";

import {
  manualRungTag,
  RUNG_BACK_TO_AUTO_LABEL,
  RUNG_LEVEL_LABEL,
  RUNG_SAVE_LABEL,
  RUNG_SCOPE_LABEL,
  rungInactiveNote,
} from "./ourPriceCopy";

type Rung = { level: RungLevel; scope: RungScope };

export interface RungSaveBarProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  /** Строка на действующей ступени (без предпросмотра): её ручная ступень и лестница. */
  line: OurPriceLine;
  /** Ступень, выбранная в панели для просмотра; `null` — просмотра нет. */
  previewRung: Rung | null;
  /** Сохранять и возвращать авто может только `admin`. */
  canEdit: boolean;
  /** Решение сохранено или снято: выбор ступени в панели больше не нужен. */
  onSaved: () => void;
}

const sameRung = (a: Rung, b: Rung) => a.level === b.level && a.scope === b.scope;

/**
 * Под лестницей: «Сохранить эту ступень» при просмотре ступени, отличной от действующей, и
 * «Вернуть авто» при сохранённой ручной (спека 4б §2.5, §2.14). Ручная ступень, переставшая
 * действовать, объяснена плашкой с причиной.
 */
export function RungSaveBar({ tenderId, roundId, offerId, line, previewRung, canEdit, onSaved }: RungSaveBarProps) {
  const putRung = usePutRung(tenderId, roundId, offerId);
  const deleteRung = useDeleteRung(tenderId, roundId, offerId);
  const manual = line.manual_rung;
  const busy = putRung.isPending || deleteRung.isPending;

  const effective: Rung | null = line.rung === null ? null : { level: line.rung.level, scope: line.rung.scope };
  const preview =
    previewRung !== null && (effective === null || !sameRung(previewRung, effective))
      ? line.rungs.find((r) => sameRung(r, previewRung))
      : undefined;
  const canSave = canEdit && preview !== undefined && preview.selectable;

  if (manual === null && !canSave) return null;

  const save = () => {
    if (previewRung === null) return;
    putRung.mutate(
      { positionItemId: line.position_item_id, level: previewRung.level, scope: previewRung.scope },
      { onSuccess: onSaved }
    );
  };
  const backToAuto = () => {
    deleteRung.mutate({ positionItemId: line.position_item_id }, { onSuccess: onSaved });
  };

  return (
    <div className="space-y-2 text-sm">
      {manual !== null && manual.active && (
        <span data-testid="rung-manual-tag" className="text-xs font-medium text-fg-secondary">
          {manualRungTag(manual.target)}
        </span>
      )}
      {manual !== null && !manual.active && (
        <div
          role="note"
          data-testid="rung-inactive"
          className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-warning-text"
        >
          {rungInactiveNote(`${RUNG_LEVEL_LABEL[manual.level]} · ${RUNG_SCOPE_LABEL[manual.scope]}`, manual.reset_reason)}
        </div>
      )}
      {canEdit && (canSave || manual !== null) && (
        <div className="flex flex-wrap gap-2">
          {canSave && (
            <Button type="button" size="sm" disabled={busy} onClick={save}>
              {RUNG_SAVE_LABEL}
            </Button>
          )}
          {manual !== null && (
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={backToAuto}>
              {RUNG_BACK_TO_AUTO_LABEL}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

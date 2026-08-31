import { StatusPill, type StatusTone } from "@/components/ui-domain/StatusPill";
import { TableCell } from "@/components/ui/table";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CellState, StagePositionsCell, StageSummaryChange } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL, STATE_LABEL } from "./cellCopy";
import { formatQuantity } from "./drilldownData";
import { ChangeBadge } from "./SummaryCell";

/**
 * Ячейка работы попозиционного раскрытия — три этажа: сумма (или подпись
 * состояния), объём заказчика, изменение к предыдущему выбранному этапу
 * (спека 2026-08-30-position-drilldown-design.md §2.4, §2.5, §2.7, §6.3;
 * задача 9 плана).
 *
 * Словари подписей и `ChangeBadge` — те же, что у ячейки свода (`cellCopy.ts`,
 * `SummaryCell.tsx`): контракт свода берётся ДОСЛОВНО (§2.5, §6.3), второй
 * словарь тех же состояний и видов изменения здесь не заводится.
 */

/** Тон пилюли состояния — то же деление, что у ячейки свода (`SummaryCell.tsx`,
 *  `CELL_STATE_TONE`): «снято» тревожно, «отсутствует» — плоский факт о файле,
 *  не о цене, и пилюли не получает вовсе. */
const CELL_STATE_TONE: Record<Exclude<CellState, "amount" | "absent">, StatusTone> = {
  removed: "warning",
  not_evaluated: "neutral",
};

/**
 * Повторяет ли значок изменения слово, которое уже сказало состояние ячейки
 * (§2.5) — тот же приём, что и у `SummaryCell` свода: совпало слово по
 * словарям — второй раз не печатаем. Там функция (`changeRepeatsState`) не
 * экспортирована, поэтому приём повторён здесь тем же способом — сравнением
 * по СУЩЕСТВУЮЩИМ словарям, а не собственным списком пар.
 */
function repeatsState(state: CellState, change: StageSummaryChange): boolean {
  if (state === "amount") return false;
  if (change.kind === "percent" || change.kind === "abs_only" || change.kind === "none") return false;
  return KIND_LABEL[change.kind] === STATE_LABEL[state];
}

export function PositionCell({ cell }: { cell: StagePositionsCell }) {
  const { state, amount, amount_unavailable_reason, quantity, quantity_unit, quantity_changed, change } = cell;
  // Число печатается только в состоянии `amount` и только когда сумма не
  // погашена неизвестной базой НДС средней колонки (§2.8) — как у ячейки свода.
  const showsNumber = state === "amount" && !amount_unavailable_reason;
  const formattedQuantity = formatQuantity(quantity);
  const hideChange = amount_unavailable_reason !== null || repeatsState(state, change);

  return (
    <TableCell
      className="text-right tabular-nums"
      title={amount_unavailable_reason ? REASON_LABEL[amount_unavailable_reason] : undefined}
    >
      <div className="flex items-center justify-end gap-1">
        {state === "amount" ? (
          showsNumber ? (
            <span>{formatDecimalMoney(amount)}</span>
          ) : (
            <StatusPill tone="neutral" label="нет базы НДС" />
          )
        ) : state === "absent" ? (
          <span className="text-fg-tertiary">{STATE_LABEL.absent}</span>
        ) : (
          <StatusPill tone={CELL_STATE_TONE[state]} label={STATE_LABEL[state]} />
        )}
      </div>
      {/* Третий этаж есть только у строки, у которой вообще есть объём (§2.4,
          §6.3) — у допработы и диагностической строки его нет структурно. */}
      {formattedQuantity !== null && (
        <div
          data-testid="cell-quantity"
          className={cn("mt-0.5 text-2xs", quantity_changed && "text-warning-text")}
        >
          {formattedQuantity} {quantity_unit}
        </div>
      )}
      {!hideChange && (
        <div className="mt-0.5 text-2xs font-normal">
          <ChangeBadge change={change} />
        </div>
      )}
    </TableCell>
  );
}

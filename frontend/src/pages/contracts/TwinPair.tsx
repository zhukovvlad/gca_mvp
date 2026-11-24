import { formatMoney } from "@/pages/tenders/our-price/money";
import type { NeighborRow } from "@/types/domain";

import { NEIGHBOR_SELF, NEIGHBOR_TWIN } from "./suspiciousCopy";

export interface TwinPairProps {
  /** Подозрительная строка (строка окна `neighbors` с флагом `is_self`). */
  self: NeighborRow;
  /** Соседняя строка с той же ценой. */
  neighbor: NeighborRow;
}

function Card({ row, caption, mine }: { row: NeighborRow; caption: string; mine: boolean }) {
  const number = row.item_number === null ? "" : `п. ${row.item_number} · `;
  return (
    <div
      data-testid={mine ? "twin-self" : "twin-neighbor"}
      className={
        mine
          ? "grid min-w-0 gap-1 rounded-[10px] border border-mark-contractor bg-mark-contractor-soft px-3 py-2 text-[13px]"
          : "grid min-w-0 gap-1 rounded-[10px] border border-border-default bg-surface px-3 py-2 text-[13px]"
      }
    >
      <div className="text-2xs text-fg-tertiary">
        {number}
        {caption}
      </div>
      <div className="break-words">{row.title}</div>
      <div className="text-[15px]">
        <b className="font-mono text-lg whitespace-nowrap">{formatMoney(row.unit_price, "")}</b> ₽ за{" "}
        <span
          data-testid="twin-unit"
          className="border-b-2 border-mark-contractor px-0.5 text-base font-bold text-mark-contractor"
        >
          {row.unit ?? "—"}
        </span>
      </div>
      <div className="font-mono text-2xs whitespace-nowrap text-fg-secondary">
        мат. {formatMoney(row.materials, "")} · раб. {formatMoney(row.works, "")}
      </div>
    </div>
  );
}

/**
 * Двойник (макет, раздел 1): две карточки через «=» — цена одна, единицы разные и выделены.
 * Узкий экран: карточки друг под другом.
 */
export function TwinPair({ self, neighbor }: TwinPairProps) {
  return (
    <div data-testid="twin-pair" className="grid max-w-[640px] min-w-0 items-stretch gap-y-1 sm:grid-cols-[minmax(0,1fr)_40px_minmax(0,1fr)]">
      <Card row={neighbor} caption={NEIGHBOR_TWIN} mine={false} />
      <div className="flex items-center justify-center py-1 text-3xl leading-none font-semibold text-mark-contractor">=</div>
      <Card row={self} caption={NEIGHBOR_SELF} mine />
    </div>
  );
}

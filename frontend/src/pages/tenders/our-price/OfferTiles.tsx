import { Card, CardContent } from "@/components/ui/card";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { MoneyPair, OurPriceOffer } from "@/types/domain";

import { formatBelow, formatSigned, pick, type VatView } from "./money";
import { matchesFilter, type OfferFilter } from "./offerFilter";
import {
  EMPTY_REASON_LABEL,
  emptyReasonOf,
  FILTER_HINT,
  FILTER_LABEL,
  linesCount,
  medianCaption,
  PER_SQM_TILE_LABEL,
  PER_SQM_UNIT,
} from "./ourPriceCopy";

export type { OfferFilter } from "./offerFilter";

export interface OfferTilesProps {
  offer: OurPriceOffer;
  vat: VatView;
  filter: OfferFilter;
  onFilter: (f: OfferFilter) => void;
}

type FilterTile = Exclude<OfferFilter, "all">;
const FILTER_TILES: FilterTile[] = ["above_ours", "below_paid", "unit_mismatch", "not_comparable"];

const TONE_CLASS: Partial<Record<FilterTile, string>> = {
  above_ours: "text-warning-text",
  below_paid: "text-info-text",
};

/**
 * Плитки разбора участника (спека 4а §2.14): «КП на м² объекта» и четыре плитки-фильтра
 * строк. Сумма плитки — из `totals` ответа; число строк — по тому же предикату, что и
 * фильтр, поэтому оно всегда равно числу строк под фильтром. Повторный щелчок по
 * нажатой плитке и «Все строки» снимают фильтр.
 */
export function OfferTiles({ offer, vat, filter, onFilter }: OfferTilesProps) {
  const participant = offer.participants[0];
  const totals = participant.totals;
  const perSqm = pick(participant.per_sqm, vat);
  const reason = emptyReasonOf(participant);

  const amount = (filterKey: FilterTile): MoneyPair | null => {
    if (totals === null) return null;
    switch (filterKey) {
      case "above_ours":
        return totals.bargain;
      case "below_paid":
        return totals.below;
      case "unit_mismatch":
        return totals.unit_mismatch_amount;
      case "not_comparable":
        return totals.not_comparable.total;
    }
  };

  const money = (filterKey: FilterTile): string => {
    const value = pick(amount(filterKey), vat);
    if (value === null) return "—";
    if (filterKey === "above_ours") return formatSigned(value);
    return filterKey === "below_paid" ? formatBelow(value) : formatDecimalMoney(value);
  };

  const count = (key: OfferFilter) => offer.lines.filter((line) => matchesFilter(line, key)).length;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
      <Card size="sm" data-testid="tile-per_sqm" className="col-span-2">
        <CardContent className="space-y-1 text-sm">
          <p className="text-xs text-fg-secondary">{PER_SQM_TILE_LABEL}</p>
          {perSqm === null ? (
            <p className="text-fg-secondary">{reason === null ? "—" : EMPTY_REASON_LABEL[reason]}</p>
          ) : (
            <p>
              <span className="font-mono text-xl tabular-nums">{formatDecimalMoney(perSqm, "")}</span>
              <span className="text-xs text-fg-secondary"> {PER_SQM_UNIT}</span>
              {participant.class_median_pct !== null && (
                <span
                  className={cn(
                    "ml-2 font-mono text-xs",
                    participant.class_median_pct.startsWith("-") ? "text-info-text" : "text-warning-text"
                  )}
                >
                  {medianCaption(participant.class_median_pct)}
                </span>
              )}
            </p>
          )}
        </CardContent>
      </Card>
      {FILTER_TILES.map((key) => {
        const pressed = filter === key;
        const n = count(key);
        return (
          <button
            key={key}
            type="button"
            data-testid={`tile-${key}`}
            data-count={n}
            aria-pressed={pressed}
            onClick={() => onFilter(pressed ? "all" : key)}
            className={cn(
              "rounded-xl border border-border-subtle bg-surface p-3 text-left text-sm transition-colors hover:bg-surface-sunken",
              pressed && "border-fg bg-surface-sunken ring-1 ring-fg"
            )}
          >
            <span className="block text-xs text-fg-secondary">{FILTER_LABEL[key]}</span>
            <span className={cn("block font-mono text-lg tabular-nums", TONE_CLASS[key])}>{money(key)}</span>
            <span className="block text-xs text-fg-secondary">
              {linesCount(n)} · {FILTER_HINT[key]}
            </span>
          </button>
        );
      })}
      <button
        type="button"
        data-testid="tile-all"
        data-count={offer.lines.length}
        aria-pressed={filter === "all"}
        onClick={() => onFilter("all")}
        className={cn(
          "rounded-xl border border-border-subtle bg-surface p-3 text-left text-sm transition-colors hover:bg-surface-sunken",
          filter === "all" && "border-fg bg-surface-sunken ring-1 ring-fg"
        )}
      >
        <span className="block text-xs text-fg-secondary">{FILTER_LABEL.all}</span>
        <span className="block font-mono text-lg tabular-nums">{offer.lines.length}</span>
      </button>
    </div>
  );
}

import type { CSSProperties } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { compareDecimalStrings } from "@/lib/decimal";
import { cn } from "@/lib/utils";
import type { Decimal, OurPriceOffer } from "@/types/domain";

import { formatMoney, pick, type VatView } from "./money";
import { PER_SQM_UNIT, STRIP_LEGEND_CLASS, STRIP_LEGEND_OTHER, STRIP_THIS_OFFER, STRIP_TITLE } from "./ourPriceCopy";

export interface PerSqmStripProps {
  offer: OurPriceOffer;
  vat: VatView;
}

interface Point {
  contractId: number;
  label: string;
  value: Decimal;
  sameClass: boolean;
}

/**
 * Положение точки на оси считает CSS над строками `--v`, `--lo`, `--hi`: числа в JS не
 * переводятся и не вычитаются. Все значения равны — ось вырождена, точки стоят посередине.
 */
const LEFT_ON_AXIS = "left-[calc((var(--v)_-_var(--lo))_/_(var(--hi)_-_var(--lo))_*_100%)]";
const LEFT_CENTER = "left-1/2";

function extremes(values: Decimal[]): { lo: Decimal; hi: Decimal } {
  return values.reduce(
    (acc, v) => ({
      lo: compareDecimalStrings(v, acc.lo) === -1 ? v : acc.lo,
      hi: compareDecimalStrings(v, acc.hi) === 1 ? v : acc.hi,
    }),
    { lo: values[0], hi: values[0] }
  );
}

/**
 * Полоса «цена на м² объекта против наших договоров» (дизайн 3.3): точки договоров —
 * темнее у класса объекта — и метка этого КП. Ось общая у всех точек: в «показанном»
 * виде она только при одной ставке участников (`history.shown_rate`), иначе — в нетто.
 */
export function PerSqmStrip({ offer, vat }: PerSqmStripProps) {
  const view: VatView = vat === "shown" && offer.history.shown_rate !== null ? "shown" : "net";
  const me = pick(offer.participants[0].per_sqm, view);
  if (me === null) return null;

  const classId = offer.tender.rate_class.id;
  const points = offer.history.contracts
    .flatMap((contract): Point[] => {
      const value = pick(offer.history.per_sqm.find((row) => row.contract_id === contract.id) ?? null, view);
      if (value === null) return [];
      return [
        {
          contractId: contract.id,
          label: `${contract.object_title} · ${contract.year} · ${contract.rate_class.title}`,
          value,
          sameClass: contract.rate_class.id === classId,
        },
      ];
    })
    .sort((a, b) => compareDecimalStrings(a.value, b.value) ?? 0);

  const { lo, hi } = extremes([...points.map((p) => p.value), me]);
  const degenerate = compareDecimalStrings(lo, hi) === 0;
  const left = degenerate ? LEFT_CENTER : LEFT_ON_AXIS;
  const at = (value: Decimal) => ({ "--v": value }) as CSSProperties;

  return (
    <Card data-testid="per-sqm-strip" style={{ "--lo": lo, "--hi": hi } as CSSProperties}>
      <CardHeader>
        <CardTitle>{STRIP_TITLE}</CardTitle>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-secondary">
          <span>● {STRIP_LEGEND_CLASS}</span>
          <span>○ {STRIP_LEGEND_OTHER}</span>
          <span className="text-warning-text">▮ {STRIP_THIS_OFFER}</span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="mx-4 mt-6 h-6">
          <div className="relative h-full">
            <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
            {points.map((point) => (
              <div
                key={point.contractId}
                data-testid={`strip-dot-${point.contractId}`}
                data-same-class={point.sameClass}
                title={`${point.label}: ${formatMoney(point.value, "")} ${PER_SQM_UNIT}`}
                style={at(point.value)}
                className={cn(
                  "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full",
                  left,
                  point.sameClass ? "bg-fg" : "bg-fg-muted opacity-50"
                )}
              />
            ))}
            <div
              data-testid="strip-me"
              style={at(me)}
              className={cn(
                "absolute top-0 h-full w-1 -translate-x-1/2 rounded-sm bg-warning-text",
                left
              )}
            >
              <span className="absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs font-semibold text-warning-text">
                {STRIP_THIS_OFFER} · {formatMoney(me, "")}
              </span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {points.map((point) => (
            <span
              key={point.contractId}
              data-testid={`strip-chip-${point.contractId}`}
              className={cn(
                "rounded-md border border-border-subtle px-2 py-0.5",
                point.sameClass ? "bg-surface-sunken" : "text-fg-secondary"
              )}
            >
              {point.label}: <b className="font-mono">{formatMoney(point.value, "")}</b>
            </span>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

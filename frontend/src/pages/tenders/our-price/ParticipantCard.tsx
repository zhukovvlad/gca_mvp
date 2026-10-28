import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Decimal, MoneyPair, OurPriceParticipant } from "@/types/domain";

import { VatBaseDialog } from "./VatBaseDialog";
import { formatBelow, formatMoney, pick, type VatView } from "./money";
import {
  CARD_LABEL,
  CHEAPEST_PER_SQM_LABEL,
  contractsWithContractor,
  DETAIL_LINK_LABEL,
  EMPTY_REASON_LABEL,
  emptyReasonOf,
  kpFileSentLink,
  linesCount,
  medianCaption,
  PER_SQM_UNIT,
  VAT_NET_LABEL,
  VAT_UNKNOWN_LABEL,
  vatShownLabel,
} from "./ourPriceCopy";

export interface ParticipantCardProps {
  participant: OurPriceParticipant;
  view: VatView;
  /** Минимальный ₽/м² среди участников этапа — решает страница по нетто. */
  cheapest: boolean;
  /** Адрес разбора; `null` — разбирать нечего (КП не загружено). */
  href: string | null;
  tenderId: number;
  roundId: number;
}

function vatCaption(participant: OurPriceParticipant, view: VatView): string {
  if (view === "net") return VAT_NET_LABEL;
  return participant.vat_rate === null ? VAT_UNKNOWN_LABEL : vatShownLabel(participant.vat_rate);
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "dear" | "cheap" }) {
  return (
    <>
      <dt className="text-fg-secondary">{label}</dt>
      <dd
        className={cn(
          "text-right font-mono tabular-nums",
          tone === "dear" && "text-warning-text",
          tone === "cheap" && "text-info-text"
        )}
      >
        {value}
      </dd>
    </>
  );
}

/**
 * Карточка участника экрана этапа (спека 4а §2.13): ₽/м² и отклонение от медианы
 * наших договоров класса, суммы «выторговать / ниже всего, что платили / разные
 * единицы / не с чем сравнить» и вход в разбор. Числа выбирает `pick`.
 */
export function ParticipantCard({ participant, view, cheapest, href, tenderId, roundId }: ParticipantCardProps) {
  const reason = emptyReasonOf(participant);
  const perSqm = pick(participant.per_sqm, view);
  const totals = participant.totals;
  const money = (pair: MoneyPair | null, format: (v: Decimal) => string = formatMoney): string => {
    const value: Decimal | null = pick(pair, view);
    return value === null ? "—" : format(value);
  };
  return (
    <Card
      data-testid={`card-${participant.offer_id}`}
      data-reason={reason ?? undefined}
      size="sm"
    >
      <CardHeader>
        <CardTitle className="flex flex-wrap items-baseline justify-between gap-2">
          <span>{participant.contractor.title}</span>
          {cheapest && <Badge variant="secondary">{CHEAPEST_PER_SQM_LABEL}</Badge>}
        </CardTitle>
        {participant.state !== "no_estimate" && (
          <p className="text-xs text-fg-secondary">{vatCaption(participant, view)}</p>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {reason !== null ? (
          <>
            <p className="text-fg-secondary">{EMPTY_REASON_LABEL[reason]}</p>
            {reason === "unknown_vat_base" && participant.estimate_id !== null && (
              <VatBaseDialog estimateId={participant.estimate_id} tenderId={tenderId} roundId={roundId} />
            )}
          </>
        ) : (
          <>
            <p>
              <span className="font-mono text-xl tabular-nums">
                {perSqm === null ? "—" : formatMoney(perSqm, "")}
              </span>
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
            {totals !== null && (
              <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
                <Row label={CARD_LABEL.bargain} value={money(totals.bargain)} tone="dear" />
                <Row
                  label={CARD_LABEL.below}
                  value={`${money(totals.below, formatBelow)} · ${linesCount(totals.below_lines)}`}
                  tone="cheap"
                />
                <Row label={CARD_LABEL.unitMismatch} value={linesCount(totals.unit_mismatch_lines)} />
                <Row label={CARD_LABEL.notComparable} value={money(totals.not_comparable.total)} />
              </dl>
            )}
          </>
        )}
        {participant.ours_with_contractor > 0 && (
          <Badge variant="outline">{contractsWithContractor(participant.ours_with_contractor)}</Badge>
        )}
        {participant.state === "no_estimate" && participant.sent_files_count > 0 && (
          <div>
            <Button
              variant="outline"
              size="sm"
              render={<Link to={`/tenders/${tenderId}/rounds/${roundId}/our-price/offers/${participant.offer_id}/file`} />}
            >
              {kpFileSentLink(participant.sent_files_count)}
            </Button>
          </div>
        )}
        {href !== null && (
          <div>
            <Button variant="outline" size="sm" render={<Link to={href} />}>
              {DETAIL_LINK_LABEL}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

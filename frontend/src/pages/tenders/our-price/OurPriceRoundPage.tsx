import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Link, useParams } from "react-router-dom";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { compareDecimalStrings } from "@/lib/decimal";
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorDetail, useOurPriceRound } from "@/services/queries";
import { qk } from "@/services/queryKeys";
import type { Decimal, OurPriceRound } from "@/types/domain";

import { ArticlesMatrix } from "./ArticlesMatrix";
import { CatalogProgressAlert } from "./CatalogProgressAlert";
import { InflationRefusalSection } from "./InflationRefusalSection";
import type { VatView } from "./money";
import { OurPriceHeader } from "./OurPriceHeader";
import {
  BACK_LABEL,
  BREADCRUMB_OUR_PRICE,
  BREADCRUMB_TENDERS,
  ERROR_TITLE,
  errorLabel,
  inflationCaptionOf,
  NO_INFLATION_CAPTION,
  RECALC_LABEL,
  roundSubtitle,
  roundTitle,
  UNALLOCATED_ARTICLE,
} from "./ourPriceCopy";
import {
  inflationRefusalCode,
  useKeptData,
  useUrlSwitches,
  vatCaption,
  withNominalFallback,
} from "./ourPriceScreen";
import { ParticipantCard } from "./ParticipantCard";
import { RecalcOverlay } from "./RecalcOverlay";

/** Минимальный ₽/м² среди участников по нетто: числа сравниваются как десятичные строки. */
function cheapestNet(participants: OurPriceRound["participants"]): Decimal | null {
  return participants.reduce<Decimal | null>((best, p) => {
    const net = p.per_sqm?.net ?? null;
    if (net === null) return best;
    return best === null || compareDecimalStrings(net, best) === -1 ? net : best;
  }, null);
}

/**
 * Экран этапа «Против нашей цены» (спека 4а §2.13): карточки участников и таблица
 * «статьи × участники». Состояние — в адресе: `?vat=shown` (по умолчанию нетто) и
 * `?inflation=<id>` (по умолчанию без приведения). Отказ приведения не роняет
 * экран: показываются номинальные цифры второго запроса и баннер отказа.
 */
export default function OurPriceRoundPage() {
  const params = useParams();
  const tenderId = Number(params.tenderId);
  const roundId = Number(params.roundId);
  const { searchParams, urlView, seriesId, onVat, onSeries } = useUrlSwitches();
  const qc = useQueryClient();
  const { data: currentUser } = useCurrentUser();

  const roundQ = useOurPriceRound(tenderId, roundId, seriesId);
  const refusalCode = inflationRefusalCode(roundQ.error);
  const refused = refusalCode !== undefined;
  // Номинальный запрос идёт вторым и только при отказе; ряд в адресе остаётся.
  const nominalQ = useOurPriceRound(tenderId, roundId, null, { enabled: refused });

  const { current, failure } = withNominalFallback(refused, roundQ, nominalQ);
  const data = useKeptData(current, (kept) => kept.tender.id === tenderId && kept.round.id === roundId);

  /** Адрес разбора участника: переключатели экрана едут с ним, статья — по ячейке. */
  function offerHref(offerId: number, article: number | typeof UNALLOCATED_ARTICLE | null): string {
    const next = new URLSearchParams(searchParams);
    if (article !== null) next.set("article", String(article));
    const query = next.toString();
    return `/tenders/${tenderId}/rounds/${roundId}/our-price/offers/${offerId}${query ? `?${query}` : ""}`;
  }

  const backLink = <Button render={<Link to={`/tenders/${tenderId}`} />}>{BACK_LABEL}</Button>;

  if (failure !== null) {
    const status = isAxiosError(failure) ? failure.response?.status : undefined;
    return (
      <div className="container-page py-8">
        <EmptyState title={ERROR_TITLE} description={errorLabel(status, seriesId !== null, apiErrorDetail(failure))} action={backLink} />
      </div>
    );
  }

  if (data === undefined) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-96" />
      </div>
    );
  }

  const { tender, round, participants } = data;
  // Переключатель недоступен (ни у кого нет известной ставки) — показ нетто, что бы ни стояло в адресе.
  const vatToggleEnabled = participants.some((p) => p.state === "ok" && p.vat_rate !== null);
  const view: VatView = vatToggleEnabled ? urlView : "net";
  const inflationCaption = refused
    ? NO_INFLATION_CAPTION
    : data.inflation === undefined
      ? null
      : inflationCaptionOf(data.inflation.sentence);
  const best = cheapestNet(participants);

  const subtitle = roundSubtitle({
    heldOn: round.held_on,
    objectTitle: tender.object.title,
    area: tender.object.area_total_sp,
    rateClass: tender.rate_class.title,
  });

  return (
    <div className="container-page space-y-4 py-8">
      <Breadcrumbs
        items={[
          { label: BREADCRUMB_TENDERS, to: "/tenders" },
          { label: tender.title, to: `/tenders/${tender.id}` },
          { label: BREADCRUMB_OUR_PRICE },
        ]}
      />
      <OurPriceHeader
        title={
          <>
            <h1 className="font-serif text-3xl font-medium tracking-tight text-fg">
              {roundTitle(round.stage_no, participants.length)}
            </h1>
            <p className="mt-1 text-sm text-fg-secondary">{subtitle}</p>
          </>
        }
        vatCaption={vatCaption(view, data.history.shown_rate)}
        inflationCaption={inflationCaption}
        vat={view}
        onVat={onVat}
        seriesId={seriesId}
        onSeries={onSeries}
        vatToggleEnabled={vatToggleEnabled}
      />

      <InflationRefusalSection
        refusalCode={refusalCode}
        error={roundQ.error}
        seriesId={seriesId}
        canEdit={currentUser?.role === "admin"}
        // Ряд мог измениться: отказ и цифры этого этапа перезапрашиваются.
        onSeriesEdited={() => qc.invalidateQueries({ queryKey: qk.tenders.ourPriceRound(tenderId, roundId, seriesId) })}
      />

      <CatalogProgressAlert progress={data.progress} />

      <RecalcOverlay active={roundQ.isPlaceholderData || current === undefined} label={RECALC_LABEL}>
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            {participants.map((participant) => (
              <ParticipantCard
                key={participant.offer_id}
                participant={participant}
                view={view}
                cheapest={
                  best !== null &&
                  participant.per_sqm !== null &&
                  compareDecimalStrings(participant.per_sqm.net, best) === 0
                }
                href={participant.estimate_id === null ? null : offerHref(participant.offer_id, null)}
                tenderId={tenderId}
                roundId={roundId}
              />
            ))}
          </div>
          <ArticlesMatrix round={data} view={view} offerHref={offerHref} />
        </div>
      </RecalcOverlay>
    </div>
  );
}

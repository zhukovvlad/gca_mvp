import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  InflationSeriesDialog,
  type InflationSeriesTarget,
} from "@/components/inflation/InflationSeriesDialog";
import { InflationRefusalBanner } from "@/components/inflation/InflationRefusalBanner";
import { isInflationRefusal } from "@/components/inflation/refusalCodes";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { compareDecimalStrings } from "@/lib/decimal";
import { useCurrentUser } from "@/hooks/useAuth";
import {
  apiErrorCode,
  apiErrorContext,
  apiErrorDetail,
  useInflationSeries,
  useOurPriceRound,
} from "@/services/queries";
import { qk } from "@/services/queryKeys";
import type { Decimal, OurPriceRound } from "@/types/domain";

import { ArticlesMatrix } from "./ArticlesMatrix";
import { CatalogProgressAlert } from "./CatalogProgressAlert";
import type { VatView } from "./money";
import { OurPriceHeader } from "./OurPriceHeader";
import {
  BACK_LABEL,
  BREADCRUMB_OUR_PRICE,
  BREADCRUMB_TENDERS,
  ERROR_TITLE,
  errorLabel,
  NO_INFLATION_CAPTION,
  PRICES_CAPTION_NET,
  PRICES_CAPTION_SHOWN_MIXED,
  pricesCaptionShown,
  RECALC_LABEL,
  REFUSAL_REASON_UNNAMED,
  roundSubtitle,
  roundTitle,
  UNALLOCATED_ARTICLE,
} from "./ourPriceCopy";
import { ParticipantCard } from "./ParticipantCard";
import { RecalcOverlay } from "./RecalcOverlay";

/** Целое положительное из адреса; всё остальное — `null` (ряда нет). */
function parseId(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return id > 0 ? id : null;
}

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
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();

  const urlView: VatView = searchParams.get("vat") === "shown" ? "shown" : "net";
  const seriesId = parseId(searchParams.get("inflation"));

  const { data: currentUser } = useCurrentUser();
  const seriesListQ = useInflationSeries(true);
  const [editingSeries, setEditingSeries] = useState<number | null>(null);
  const [missingYears, setMissingYears] = useState<number[] | undefined>(undefined);

  const roundQ = useOurPriceRound(tenderId, roundId, seriesId);
  const refusalCode = apiErrorCode(roundQ.error);
  const refused = isInflationRefusal(refusalCode);
  // Номинальный запрос идёт вторым и только при отказе; ряд в адресе остаётся.
  const nominalQ = useOurPriceRound(tenderId, roundId, null, { enabled: refused });

  const current = refused ? nominalQ.data : roundQ.data;
  const failure = refused ? (nominalQ.isError ? nominalQ.error : null) : roundQ.isError ? roundQ.error : null;

  // Цифры, которые экран показывал последними: пока идёт новый запрос (в том числе после
  // отказа приведения, когда у упавшего запроса данных нет), они остаются под серым слоем.
  const [kept, setKept] = useState<OurPriceRound | null>(null);
  if (current !== undefined && current !== kept) setKept(current);
  const keptHere = kept !== null && kept.tender.id === tenderId && kept.round.id === roundId ? kept : undefined;
  const data = current ?? keptHere;

  function update(mutate: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  }

  function onVat(next: VatView) {
    update((p) => (next === "shown" ? p.set("vat", "shown") : p.delete("vat")));
  }

  function onSeries(id: number | null) {
    update((p) => (id === null ? p.delete("inflation") : p.set("inflation", String(id))));
  }

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
  const shownRate = data.history.shown_rate;
  const vatCaption =
    view === "net"
      ? PRICES_CAPTION_NET
      : shownRate === null
        ? PRICES_CAPTION_SHOWN_MIXED
        : pricesCaptionShown(shownRate);
  const inflationCaption = refused ? NO_INFLATION_CAPTION : (data.inflation?.sentence ?? null);
  const best = cheapestNet(participants);

  const editedSeries = seriesListQ.data?.find((row) => row.id === editingSeries) ?? null;
  const dialogTarget: InflationSeriesTarget = editedSeries
    ? { mode: "edit", series: editedSeries }
    : seriesListQ.isError
      ? { mode: "edit", series: null, reason: "failed", onRetry: () => void seriesListQ.refetch() }
      : { mode: "edit", series: null, reason: "pending" };

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
        vatCaption={vatCaption}
        inflationCaption={inflationCaption}
        vat={view}
        onVat={onVat}
        seriesId={seriesId}
        onSeries={onSeries}
        vatToggleEnabled={vatToggleEnabled}
      />

      {refused && refusalCode !== undefined && (
        <InflationRefusalBanner
          code={refusalCode}
          message={apiErrorDetail(roundQ.error) ?? REFUSAL_REASON_UNNAMED}
          seriesName={seriesListQ.data?.find((row) => row.id === seriesId)?.name}
          missingYears={apiErrorContext<{ missing_years?: number[] }>(roundQ.error)?.missing_years}
          canEdit={currentUser?.role === "admin"}
          monthInAddress={false}
          onFillMissingYears={(years) => {
            setMissingYears(years);
            setEditingSeries(seriesId);
          }}
        />
      )}

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
              />
            ))}
          </div>
          <ArticlesMatrix round={data} view={view} offerHref={offerHref} />
        </div>
      </RecalcOverlay>

      <InflationSeriesDialog
        open={editingSeries !== null}
        target={dialogTarget}
        missingYears={missingYears}
        onOpenChange={(open) => {
          if (open) return;
          setEditingSeries(null);
          setMissingYears(undefined);
          // Ряд мог измениться: отказ и цифры этого этапа перезапрашиваются.
          qc.invalidateQueries({ queryKey: qk.tenders.ourPriceRound(tenderId, roundId, seriesId) });
        }}
      />
    </div>
  );
}

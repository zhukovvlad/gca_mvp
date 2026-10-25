import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Link, useParams } from "react-router-dom";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorDetail, useOurPriceOffer, useTender } from "@/services/queries";
import { qk } from "@/services/queryKeys";

import { CatalogProgressAlert } from "./CatalogProgressAlert";
import { InflationRefusalSection } from "./InflationRefusalSection";
import { LineSheet } from "./LineSheet";
import type { VatView } from "./money";
import { OfferArticlesTable } from "./OfferArticlesTable";
import { OfferTiles, type OfferFilter } from "./OfferTiles";
import { OurPriceHeader } from "./OurPriceHeader";
import {
  BACK_LABEL,
  BREADCRUMB_TENDERS,
  ERROR_TITLE,
  errorLabel,
  inflationCaptionOf,
  NO_INFLATION_CAPTION,
  OFFER_SWITCHER_LABEL,
  offerCrumbStage,
  offerTitle,
  RECALC_LABEL,
  roundSubtitle,
} from "./ourPriceCopy";
import {
  inflationRefusalCode,
  useKeptData,
  useUrlSwitches,
  vatCaption,
  withNominalFallback,
} from "./ourPriceScreen";
import { PerSqmStrip } from "./PerSqmStrip";
import { RecalcOverlay } from "./RecalcOverlay";

/**
 * Разбор участника «Против нашей цены» (спека 4а §2.14): путь и переключатель
 * участников, плитки-фильтры, полоса ₽/м², статьи с раскрытием по строкам. Состояние —
 * в адресе: `?vat=`, `?inflation=` (как на экране этапа) и `?article=` — статья,
 * раскрытая при входе. Кнопки других участников берутся из карточки тендера: эндпоинт
 * этапа тяжёлый, и звать его ради имён значило бы удвоить нагрузку.
 */
export default function OurPriceOfferPage() {
  const params = useParams();
  const tenderId = Number(params.tenderId);
  const roundId = Number(params.roundId);
  const offerId = Number(params.offerId);
  const { searchParams, urlView, seriesId, onVat, onSeries } = useUrlSwitches();
  const qc = useQueryClient();
  const { data: currentUser } = useCurrentUser();
  const cardQ = useTender(tenderId);

  const offerQ = useOurPriceOffer(tenderId, roundId, offerId, seriesId);
  const refusalCode = inflationRefusalCode(offerQ.error);
  const refused = refusalCode !== undefined;
  // Номинальный запрос идёт вторым и только при отказе; ряд в адресе остаётся.
  const nominalQ = useOurPriceOffer(tenderId, roundId, offerId, null, { enabled: refused });

  const { current, failure } = withNominalFallback(refused, offerQ, nominalQ);
  const data = useKeptData(
    current,
    (kept) =>
      kept.tender.id === tenderId && kept.round.id === roundId && kept.participants[0].offer_id === offerId
  );

  const [filter, setFilter] = useState<OfferFilter>("all");
  const [openLine, setOpenLine] = useState<number | null>(null);
  // Страница не пересоздаётся при смене участника: фильтр плиток и открытая строка — его, не соседа.
  const [stateOfOffer, setStateOfOffer] = useState(offerId);
  if (stateOfOffer !== offerId) {
    setStateOfOffer(offerId);
    setFilter("all");
    setOpenLine(null);
  }

  const backLink = <Button render={<Link to={`/tenders/${tenderId}`} />}>{BACK_LABEL}</Button>;

  if (failure !== null) {
    const status = isAxiosError(failure) ? failure.response?.status : undefined;
    return (
      <div className="container-page py-8">
        <EmptyState
          title={ERROR_TITLE}
          description={errorLabel(status, seriesId !== null, apiErrorDetail(failure))}
          action={backLink}
        />
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

  const { tender, round } = data;
  const participant = data.participants[0];
  // Переключатель недоступен (база НДС участника неизвестна) — показ нетто, что бы ни стояло в адресе.
  const vatToggleEnabled = participant.vat_rate !== null;
  const view: VatView = vatToggleEnabled ? urlView : "net";
  const inflationCaption = refused
    ? NO_INFLATION_CAPTION
    : data.inflation === undefined
      ? null
      : inflationCaptionOf(data.inflation.sentence);

  // Переключатели адреса едут в соседние экраны; раскрытая статья — нет.
  const switches = new URLSearchParams();
  if (urlView === "shown") switches.set("vat", "shown");
  if (seriesId !== null) switches.set("inflation", String(seriesId));
  const query = switches.toString() === "" ? "" : `?${switches.toString()}`;
  const stagePath = `/tenders/${tenderId}/rounds/${roundId}/our-price`;
  const stageHref = `${stagePath}${query}`;
  const offerHref = (id: number) => `${stagePath}/offers/${id}${query}`;

  const card = cardQ.data;
  const switcher =
    card === undefined
      ? []
      : card.participants.flatMap((p) => {
          const cell = card.cells.find((c) => c.round_id === roundId && c.package_id === p.package_id);
          return cell !== undefined && cell.offer_id !== null && cell.estimate_id !== null
            ? [{ offerId: cell.offer_id, title: p.title }]
            : [];
        });

  const subtitle = roundSubtitle({
    heldOn: round.held_on,
    objectTitle: tender.object.title,
    area: tender.object.area_total_sp,
    rateClass: tender.rate_class.title,
  });

  return (
    <div
      data-testid="offer-page"
      data-open-line={openLine ?? undefined}
      className="container-page space-y-4 py-8"
    >
      <Breadcrumbs
        items={[
          { label: BREADCRUMB_TENDERS, to: "/tenders" },
          { label: tender.title, to: `/tenders/${tender.id}` },
          { label: offerCrumbStage(round.stage_no), to: stageHref },
          { label: participant.contractor.title },
        ]}
      />
      {switcher.length > 1 && (
        <div data-testid="participant-switcher" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-fg-secondary">{OFFER_SWITCHER_LABEL}</span>
          {switcher.map((item) =>
            item.offerId === offerId ? (
              <Button key={item.offerId} type="button" size="sm" aria-current="true">
                {item.title}
              </Button>
            ) : (
              <Button key={item.offerId} variant="outline" size="sm" render={<Link to={offerHref(item.offerId)} />}>
                {item.title}
              </Button>
            )
          )}
        </div>
      )}
      <OurPriceHeader
        title={
          <>
            <h1 className="font-serif text-3xl font-medium tracking-tight text-fg">
              {offerTitle(participant.contractor.title)}
            </h1>
            <p className="mt-1 text-sm text-fg-secondary">
              {offerCrumbStage(round.stage_no)} · {subtitle}
            </p>
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
        error={offerQ.error}
        seriesId={seriesId}
        canEdit={currentUser?.role === "admin"}
        // Ряд мог измениться: отказ и цифры этого участника перезапрашиваются.
        onSeriesEdited={() =>
          qc.invalidateQueries({ queryKey: qk.tenders.ourPriceOffer(tenderId, roundId, offerId, seriesId) })
        }
      />

      <CatalogProgressAlert progress={data.progress} />

      <RecalcOverlay active={offerQ.isPlaceholderData || current === undefined} label={RECALC_LABEL}>
        <div className="space-y-4">
          <OfferTiles offer={data} vat={view} filter={filter} onFilter={setFilter} />
          <PerSqmStrip offer={data} vat={view} />
          <OfferArticlesTable
            key={`${offerId}:${searchParams.get("article") ?? ""}`}
            offer={data}
            vat={view}
            filter={filter}
            initialOpen={searchParams.get("article")}
            rateClass={tender.rate_class.title}
            canResolve={currentUser?.role === "admin"}
            onOpenLine={setOpenLine}
          />
        </div>
      </RecalcOverlay>

      <LineSheet
        tenderId={tenderId}
        roundId={roundId}
        offerId={offerId}
        positionItemId={openLine}
        // При отказе ИПЦ экран стоит на номинальных цифрах — панель за тем же рядом получила бы тот же отказ.
        seriesId={refused ? null : seriesId}
        vat={view}
        vatRate={participant.vat_rate}
        canResolve={currentUser?.role === "admin"}
        onClose={() => setOpenLine(null)}
      />
    </div>
  );
}

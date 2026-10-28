import { useIsMutating } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Download, Loader2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/useAuth";
import {
  apiErrorDetail,
  kpDecisionMutationKey,
  useKpFileExport,
  useKpFilePreview,
} from "@/services/queries";

import { InflationRefusalSection } from "./InflationRefusalSection";
import { KpFileTable } from "./KpFileTable";
import {
  BACK_LABEL,
  BREADCRUMB_TENDERS,
  KP_FILE_DOWNLOAD_LABEL,
  KP_FILE_ERROR_TITLE,
  KP_FILE_TITLE,
  kpFileErrorLabel,
  kpFileInProgressNote,
  NO_INFLATION_CAPTION,
  RECALC_LABEL,
} from "./ourPriceCopy";
import { inflationRefusalCode, useKeptData, useUrlSwitches, withNominalFallback } from "./ourPriceScreen";
import { OurPriceHeader } from "./OurPriceHeader";
import { RecalcOverlay } from "./RecalcOverlay";
import { SentFilesList } from "./SentFilesList";

/**
 * Страница файла подрядчику (спека 4б §2.9, §2.14): предпросмотр того, что ляжет в книгу,
 * кнопка «Скачать xlsx» и список уже отправленных файлов. Ряд ИПЦ - в адресе (`?inflation=`),
 * как у разбора; при отказе приведения цифры берёт второй запрос без ряда, и выгрузка идёт
 * с тем же рядом, что показан. Страница пересоздаётся при смене участника, поэтому прежние
 * цифры соседа не остаются под новым заголовком.
 */
export default function KpFilePage() {
  const params = useParams();
  const tenderId = Number(params.tenderId);
  const roundId = Number(params.roundId);
  const offerId = Number(params.offerId);
  return <KpFileScreen key={`${tenderId}:${roundId}:${offerId}`} tenderId={tenderId} roundId={roundId} offerId={offerId} />;
}

function KpFileScreen({ tenderId, roundId, offerId }: { tenderId: number; roundId: number; offerId: number }) {
  const { seriesId, onSeries } = useUrlSwitches();
  const { data: currentUser } = useCurrentUser();

  const previewQ = useKpFilePreview(tenderId, roundId, offerId, seriesId);
  const refusalCode = inflationRefusalCode(previewQ.error);
  const refused = refusalCode !== undefined;
  // Номинальный запрос идёт вторым и только при отказе; ряд в адресе остаётся.
  const nominalQ = useKpFilePreview(tenderId, roundId, offerId, null, { enabled: refused });
  const { current, failure } = withNominalFallback(refused, previewQ, nominalQ);
  const preview = useKeptData(current, () => true);

  // Ряд, с которым запрошен показанный предпросмотр: выгрузка читает имя файла из его кеша.
  const shownSeries = refused ? null : seriesId;
  const exportFile = useKpFileExport(tenderId, roundId, offerId, shownSeries);
  const decisionsRunning = useIsMutating({ mutationKey: kpDecisionMutationKey(tenderId, roundId, offerId) }) > 0;
  const refetching = previewQ.isFetching || nominalQ.isFetching;
  const busy = current === undefined || refetching || decisionsRunning;

  const backLink = <Button render={<Link to={`/tenders/${tenderId}`} />}>{BACK_LABEL}</Button>;

  if (failure !== null) {
    const status = isAxiosError(failure) ? failure.response?.status : undefined;
    return (
      <div data-testid="kp-file-error" className="container-page py-8">
        <EmptyState
          title={KP_FILE_ERROR_TITLE}
          description={kpFileErrorLabel(status, seriesId !== null, apiErrorDetail(failure))}
          action={backLink}
        />
      </div>
    );
  }

  if (preview === undefined) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-96" />
      </div>
    );
  }

  const { header } = preview;
  const query = seriesId === null ? "" : `?inflation=${seriesId}`;
  const offerPath = `/tenders/${tenderId}/rounds/${roundId}/our-price/offers/${offerId}${query}`;

  return (
    <div data-testid="kp-file-page" className="container-page space-y-4 py-8">
      <Breadcrumbs
        items={[
          { label: BREADCRUMB_TENDERS, to: "/tenders" },
          { label: header.tender, to: `/tenders/${tenderId}` },
          { label: header.contractor, to: offerPath },
          { label: KP_FILE_TITLE },
        ]}
      />
      <OurPriceHeader
        title={
          <>
            <h1 className="font-serif text-3xl font-medium tracking-tight text-fg">{KP_FILE_TITLE}</h1>
            <p className="mt-1 text-sm text-fg-secondary">
              {header.contractor} · {header.round}
            </p>
          </>
        }
        vatCaption={null}
        inflationCaption={refused ? NO_INFLATION_CAPTION : null}
        vat="net"
        onVat={() => {}}
        seriesId={seriesId}
        onSeries={onSeries}
        vatToggleEnabled={false}
        hideVatSwitch
      />

      <InflationRefusalSection
        refusalCode={refusalCode}
        error={previewQ.error}
        seriesId={seriesId}
        canEdit={currentUser?.role === "admin"}
        // Ряд мог измениться: отказ и предпросмотр перезапрашиваются.
        onSeriesEdited={() => void previewQ.refetch()}
      />

      {header.in_progress_lines > 0 && (
        <Alert data-testid="kp-file-in-progress" className="flex items-center gap-2 text-sm">
          <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
          <span>{kpFileInProgressNote(header.in_progress_lines)}</span>
        </Alert>
      )}

      <div className="flex justify-end">
        <Button type="button" disabled={busy} loading={exportFile.isPending} onClick={() => exportFile.mutate()}>
          <Download aria-hidden />
          {KP_FILE_DOWNLOAD_LABEL}
        </Button>
      </div>

      <RecalcOverlay active={busy} label={RECALC_LABEL}>
        <KpFileTable
          tenderId={tenderId}
          roundId={roundId}
          offerId={offerId}
          preview={preview}
          canEdit={currentUser?.role === "admin"}
        />
      </RecalcOverlay>

      <SentFilesList tenderId={tenderId} roundId={roundId} offerId={offerId} />
    </div>
  );
}

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { formatDate } from "@/lib/format";
import { useDownloadSentFile, useKpSentFiles } from "@/services/queries";

import {
  KP_FILE_SENT_DOWNLOAD,
  KP_FILE_SENT_EMPTY,
  KP_FILE_SENT_ERROR,
  KP_FILE_SENT_TITLE,
  vatShownLabel,
} from "./ourPriceCopy";

export interface SentFilesListProps {
  tenderId: number;
  roundId: number;
  offerId: number;
}

/**
 * «Отправлено подрядчику» (спека 4б §2.10): файлы, выгруженные по этому КП. Порядок -
 * как ответил сервер (новые сверху), клиент его не меняет. Состав НДС и уровень - то, что
 * было в файле на момент выгрузки, а не то, что на экране сейчас.
 */
export function SentFilesList({ tenderId, roundId, offerId }: SentFilesListProps) {
  const filesQ = useKpSentFiles(tenderId, roundId, offerId);
  const download = useDownloadSentFile(tenderId, roundId, offerId);
  const files = filesQ.data;

  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium text-fg">{KP_FILE_SENT_TITLE}</h2>
      {filesQ.isError ? (
        <p data-testid="sent-files-error" className="text-sm text-danger-text">
          {KP_FILE_SENT_ERROR}
        </p>
      ) : files === undefined ? (
        <Skeleton className="h-16" />
      ) : files.length === 0 ? (
        <p className="text-sm text-fg-secondary">{KP_FILE_SENT_EMPTY}</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border text-sm">
          {files.map((file) => (
            <li
              key={file.id}
              data-testid={`sent-file-${file.id}`}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2"
            >
              <span className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <span className="tabular-nums">{formatDate(file.created_at)}</span>
                <span>{file.created_by.email}</span>
                <span className="text-fg-secondary">
                  {vatShownLabel(file.vat_rate)} · {file.level_label}
                </span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={download.isPending}
                onClick={() => download.mutate({ fileId: file.id, filename: file.filename })}
              >
                <Download aria-hidden />
                {KP_FILE_SENT_DOWNLOAD}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

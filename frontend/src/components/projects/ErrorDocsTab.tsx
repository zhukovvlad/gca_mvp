import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Crop, ExternalLink, FileEdit, RefreshCw, Trash2 } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui-domain/Button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { formatDate } from "@/lib/format";
import { useReparseDocument, useDeskewReparseDocument, useDeleteDocument } from "@/services/queries";
import { invoicesApi } from "@/services/api/invoices";
import type { DocumentSummary } from "@/types/invoice";

interface ErrorDocsTabProps {
  docs: DocumentSummary[];
}

export function ErrorDocsTab({ docs }: ErrorDocsTabProps) {
  const errorDocs = docs.filter((d) => d.status === "error" || d.has_issues);

  const reparse = useReparseDocument();
  const deskew = useDeskewReparseDocument();
  const deleteDoc = useDeleteDocument();

  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);

  if (errorDocs.length === 0) {
    return (
      <div className="p-6">
        <div className="flex items-center gap-3 rounded-lg border border-accent-border bg-accent-soft px-4 py-3">
          <CheckCircle2 size={18} className="shrink-0 text-accent-text" />
          <div>
            <p className="text-sm font-medium text-accent-text">Все документы разобраны успешно</p>
            <p className="text-xs text-fg-secondary">Ошибок парсинга и проблем в СФ не обнаружено</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="overflow-x-auto">
        <Table className="min-w-[780px] table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead>Документ</TableHead>
              <TableHead>Загружен</TableHead>
              <TableHead>Статус</TableHead>
              <TableHead className="text-right">СФ</TableHead>
              <TableHead className="text-right">Уверенность</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {errorDocs.map((doc) => {
              const isReparsing = reparse.isPending && reparse.variables === doc.id;
              const tone = doc.status === "error" ? "danger" : "neutral";
              const statusLabel =
                doc.status === "error"
                  ? doc.last_error || "Ошибка парсинга"
                  : "Проблемы в СФ";
              const confidencePct =
                doc.ai_confidence != null
                  ? `${Math.round(doc.ai_confidence * 100)}%`
                  : "—";

              return (
                <TableRow key={doc.id} className="hover:bg-surface-hover">
                  <TableCell>
                    <div className="flex flex-col gap-0.5">
                      <span className="font-medium text-sm">{doc.filename}</span>
                      <span className="text-xs text-fg-tertiary">{doc.doc_type}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-fg-secondary tabular-nums text-sm">
                    {formatDate(doc.uploaded_at)}
                  </TableCell>
                  <TableCell>
                    <StatusPill tone={tone} label={statusLabel} dot />
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-sm">
                    {doc.invoice_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-sm text-fg-secondary">
                    {confidencePct}
                  </TableCell>
                  <TableCell className="pr-3">
                    <div className="flex items-center justify-end gap-1">
                      {/* Open PDF */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <a
                              href={invoicesApi.documentPdfUrl(doc.id)}
                              target="_blank"
                              rel="noopener noreferrer"
                              aria-label="Открыть PDF"
                            >
                              <Button variant="ghost" size="sm" tabIndex={-1}>
                                <ExternalLink size={14} />
                              </Button>
                            </a>
                          }
                        />
                        <TooltipContent>Открыть PDF</TooltipContent>
                      </Tooltip>

                      {/* Review */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Link to={`/documents/${doc.id}`} aria-label="Разобрать">
                              <Button variant="ghost" size="sm" tabIndex={-1}>
                                <FileEdit size={14} />
                              </Button>
                            </Link>
                          }
                        />
                        <TooltipContent>Разобрать</TooltipContent>
                      </Tooltip>

                      {/* Reparse */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Переразобрать"
                              disabled={reparse.isPending}
                              onClick={() => reparse.mutate(doc.id)}
                            >
                              <RefreshCw
                                size={14}
                                className={isReparsing ? "animate-spin" : undefined}
                              />
                            </Button>
                          }
                        />
                        <TooltipContent>Переразобрать</TooltipContent>
                      </Tooltip>

                      {/* Deskew + reparse */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Выпрямить и переразобрать"
                              disabled={deskew.isPending || reparse.isPending}
                              onClick={() => deskew.mutate(doc.id)}
                            >
                              <Crop
                                size={14}
                                className={deskew.isPending && deskew.variables === doc.id ? "animate-pulse" : undefined}
                              />
                            </Button>
                          }
                        />
                        <TooltipContent>Выпрямить и переразобрать</TooltipContent>
                      </Tooltip>

                      {/* Delete */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Удалить"
                              className="text-fg-tertiary hover:text-danger"
                              onClick={() => setPendingDelete(doc)}
                            >
                              <Trash2 size={14} />
                            </Button>
                          }
                        />
                        <TooltipContent>Удалить</TooltipContent>
                      </Tooltip>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Delete confirmation */}
      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(open) => { if (!open) setPendingDelete(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить «{pendingDelete?.filename}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Документ и все его счета-фактуры будут удалены без возможности восстановления.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteDoc.isPending}
              onClick={() => {
                if (pendingDelete) {
                  deleteDoc.mutate(pendingDelete.id, {
                    onSuccess: () => setPendingDelete(null),
                    onError: () => setPendingDelete(null),
                  });
                }
              }}
            >
              {deleteDoc.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

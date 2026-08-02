import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, FileText, Loader2 } from "lucide-react";

import { Surface } from "@/components/ui-domain/Surface";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { Button } from "@/components/ui-domain/Button";
import { useDocument } from "@/services/queries";
import { isDocBusy } from "@/services/processingRefetchInterval";
import type { UploadResponse } from "@/types/invoice";

export interface JobState {
  id: string;
  file: File;
  status: "pending" | "uploading" | "ready" | "error";
  progress: number;
  result?: UploadResponse;
  error?: string;
}

/**
 * Строка задания загрузки: локальный этап (pending|uploading|error загрузки)
 * + серверный этап после 202 — статус документа из query-кэша (polling S1-5).
 * Терминальное состояние строки привязано к статусу документа, СФ рендерятся
 * из данных квери, не из снапшота ответа (S1-6). Дубликат — нейтральный бейдж.
 */
export function UploadJobRow({ job }: { job: JobState }) {
  // enabled: хук не дёргает сеть, пока 202 не принят (нет result.id).
  const docQ = useDocument(job.result?.id ?? null);
  const doc = docQ.data ?? job.result;
  const isDuplicate = job.result?.duplicate === true;
  const serverBusy = doc != null && isDocBusy(doc.status);
  const serverError = doc?.status === "error";

  return (
    <Surface padding="sm">
      <div className="flex items-start gap-4">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-surface-sunken">
          {(job.status === "uploading" || serverBusy) && (
            <Loader2 size={16} className="animate-spin text-accent" />
          )}
          {job.status === "ready" && !serverBusy && !serverError && (
            <CheckCircle2 size={16} className="text-accent" />
          )}
          {(job.status === "error" || serverError) && (
            <AlertTriangle size={16} className="text-danger" />
          )}
          {job.status === "pending" && <FileText size={16} className="text-fg-tertiary" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-fg">{job.file.name}</span>
            {job.status === "uploading" && <StatusPill tone="info" label={`${job.progress}%`} />}
            {isDuplicate && <StatusPill tone="info" label="Файл уже был загружен" />}
            {job.status === "ready" && serverBusy && (
              <StatusPill tone="info" label="обрабатывается" dot />
            )}
            {job.status === "ready" && doc?.status === "parsed" && (
              <StatusPill tone="success" label="готово" dot />
            )}
            {serverError && <StatusPill tone="danger" label={doc?.last_error || "ошибка"} dot />}
            {job.status === "error" && <StatusPill tone="danger" label="ошибка" dot />}
          </div>
          {doc != null && "invoices" in doc && doc.invoices.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-fg-secondary">
              {doc.invoices.map((inv) => (
                <span key={inv.id} className="flex items-center gap-1.5">
                  СФ № {inv.number} · {inv.items.length} позиций
                  <ConfidenceBadge value={inv.ai_confidence} />
                </span>
              ))}
            </div>
          )}
          {job.error && <div className="mt-1 text-xs text-danger-text">{job.error}</div>}
        </div>
        {job.result && doc?.status === "parsed" && (
          <Link to={`/documents/${job.result.id}`}>
            <Button variant="secondary" size="sm">Проверить</Button>
          </Link>
        )}
        {job.result && isDuplicate && serverError && (
          // Дубликат упавшего файла: байты уже в S3, ретрай — reparse на
          // error-документе во вкладке «Ошибки» проекта (спека S1, Codex P2,
          // fix 3). Ссылка на /documents/:id вела бы в «Документ не найден»
          // (см. комментарий про serverBusy ниже), поэтому ведём в проект.
          // Три параметра нужны, чтобы работать в ЛЮБОМ режиме ProjectPage:
          // direction=all + view=errors — путь через ErrorsView/ErrorDocsTab
          // в режиме «Все направления» (моно- и мульти-); tab=errors — тот же
          // ErrorDocsTab в legacy-режиме (пустые directions / упавший summary),
          // где view игнорируется, а табы рендерятся напрямую.
          <Link to={`/projects/${job.result.project_id}?direction=all&view=errors&tab=errors`}>
            <Button variant="secondary" size="sm">К ошибкам проекта</Button>
          </Link>
        )}
        {job.result && serverBusy && (
          // Свежий 202 содержит invoices: [] — Review трактует их отсутствие как
          // «Документ не найден». Пока документ не дошёл до parsed, ссылка на
          // /documents/:id вела бы на ложную ошибку (Codex P2, fix 2). Кнопка
          // остаётся на месте (визуальная непрерывность), но задизейблена.
          <Button
            variant="secondary"
            size="sm"
            disabled
            title="Документ обрабатывается — дождитесь завершения"
          >
            Проверить
          </Button>
        )}
      </div>
    </Surface>
  );
}

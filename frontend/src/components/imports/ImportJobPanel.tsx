import type { ReactNode } from "react";
import { AlertTriangle, FileSpreadsheet, Loader2 } from "lucide-react";

import { Dropzone } from "@/components/ui-domain/Dropzone";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { isRunning, statusTone, STATUS_LABEL, XLSX_ACCEPT } from "./jobStatus";
import type { ImportJob } from "@/types/domain";

/**
 * Общая часть загрузки задания импорта (спека §2.14, задача 11): dropzone,
 * состояние передачи, статус job с поллингом, счётчики, предупреждения, отказ.
 *
 * Вынесена из `EstimateUploadPanel` — раунд тендера использует тот же экран
 * (`RoundUploadPanel`), и вторая копия этого JSX разошлась бы с первой при
 * первой же правке. `job` намеренно принимает ШИРОКИЙ `ImportJob`: панель
 * читает только поля, общие обоим владельцам (`filename`, `status`,
 * `error_text`, `warnings`, `counters`) — она не знает и не обязана знать, чей
 * это job. У узкоспециализированных вызывающих (`EstimateUploadPanel`,
 * `RoundUploadPanel`) свои `ContractImportJob`/`RoundImportJob` в своих
 * состояниях; сюда они передают то же значение, просто под широким именем
 * пропа.
 *
 * `XLSX_ACCEPT`/`STATUS_LABEL`/`statusTone`/`isRunning` живут в `./jobStatus`,
 * а не здесь и не реэкспортированы отсюда: `react-refresh/only-export-
 * components` запрещает файлу с экспортом компонента экспортировать функцию
 * (даже сквозным `export … from`) — та же находка, что уже разводила похожие
 * пары в `pages/passport/rateLabels.ts` и `pages/compare/deviationTone.ts`.
 * Брифинг задачи 11 называл местом экспорта именно `ImportJobPanel.tsx`, но
 * ЭТИМ он расходится с собственным правилом линта проекта; вызывающие
 * (`EstimateUploadPanel`, `RoundUploadPanel`, тесты) импортируют `statusTone`/
 * `isRunning`/`STATUS_LABEL`/`XLSX_ACCEPT` из `./jobStatus` напрямую — второй
 * копии этих имён здесь нет.
 */

export function CounterCell({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-fg-tertiary">{label}</dt>
      <dd className="font-mono tabular-nums text-fg">{value}</dd>
    </div>
  );
}

interface ImportJobPanelProps {
  job: ImportJob | undefined;
  uploading: boolean;
  idempotent: boolean;
  /**
   * Текст подписи «файл уже был загружен», показанной при `idempotent`.
   * Панель не знает своего владельца (см. докстроку выше) и потому не может
   * сама решить, идёт ли речь о смете или о раунде — предложение целиком
   * даёт вызывающий: `EstimateUploadPanel` несёт формулировку про смету,
   * `RoundUploadPanel` — про раунд (спека §2.6: замена раунда касается смет
   * ВСЕХ участников, а не «этой сметы»).
   */
  idempotentNote: string;
  rejection: string | null;
  disabled: boolean;
  hint: string;
  onDrop: (files: File[]) => void;
  /** Слот НАД dropzone — например поле номера допсоглашения у договора. */
  children?: ReactNode;
}

export function ImportJobPanel({
  job,
  uploading,
  idempotent,
  idempotentNote,
  rejection,
  disabled,
  hint,
  onDrop,
  children,
}: ImportJobPanelProps) {
  return (
    <>
      {children}

      <Dropzone onDrop={onDrop} accept={XLSX_ACCEPT} multiple={false} disabled={disabled} hint={hint} />

      {uploading && (
        <p className="flex items-center gap-2 text-sm text-fg-secondary">
          <Loader2 className="size-4 animate-spin" /> Файл передаётся…
        </p>
      )}

      {rejection && (
        // `data-testid` рядом с ролью — не дубль: открытый `AlertDialog`
        // модален и помечает фон `aria-hidden`, из-за чего запрос по РОЛИ
        // перестаёт видеть этот текст ровно тогда, когда диалог всё-таки
        // открылся (см. комментарий в `EstimateUploadPanel.test.tsx`).
        <p
          data-testid="upload-rejection"
          role="alert"
          className="flex items-start gap-2 text-sm text-danger-text"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {rejection}
        </p>
      )}

      {job && (
        <div className="grid gap-3 rounded-md border border-border-subtle p-3">
          <div className="flex flex-wrap items-center gap-2">
            <FileSpreadsheet className="size-4 text-fg-tertiary" />
            <span className="text-sm font-medium text-fg">{job.filename}</span>
            <StatusPill
              tone={statusTone(job.status)}
              label={STATUS_LABEL[job.status]}
              dot={isRunning(job.status)}
            />
            {isRunning(job.status) && (
              <span className="text-xs text-fg-secondary">
                Смета появится в карточке после завершения — страницу закрывать не нужно
              </span>
            )}
          </div>

          {idempotent && <p className="text-sm text-fg-secondary">{idempotentNote}</p>}

          {job.status === "error" && job.error_text && (
            <p role="alert" className="text-sm text-danger-text">
              {job.error_text}
            </p>
          )}

          {job.status === "done" && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-5">
              <CounterCell label="Позиций" value={job.counters.positions_total} />
              <CounterCell label="Из кэша" value={job.counters.matched_cache} />
              <CounterCell label="Точно" value={job.counters.matched_exact} />
              <CounterCell label="Не работы" value={job.counters.matched_nonposition} />
              <CounterCell label="На разбор" value={job.counters.to_review} />
            </dl>
          )}

          {job.warnings.length > 0 && (
            <div className="grid gap-1">
              <p className="text-xs font-medium text-warning-text">
                Предупреждения ({job.warnings.length})
              </p>
              <ul className="grid gap-1 text-xs text-fg-secondary">
                {job.warnings.map((warning, index) => (
                  <li key={index} className="flex items-start gap-1.5">
                    <AlertTriangle className="mt-0.5 size-3 shrink-0 text-warning-text" />
                    {warning}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </>
  );
}

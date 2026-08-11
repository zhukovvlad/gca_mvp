import { useState } from "react";
import { AlertTriangle, FileSpreadsheet, Loader2 } from "lucide-react";

import { Dropzone } from "@/components/ui-domain/Dropzone";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorDetail, apiErrorStatus, useImportJob, useUploadEstimate } from "@/services/queries";
import type { EstimateRow, ImportJob, ImportJobStatus } from "@/types/domain";

const XLSX_ACCEPT = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel.sheet.macroEnabled.12": [".xlsm"],
};

const STATUS_LABEL: Record<ImportJobStatus, string> = {
  pending: "В очереди",
  parsing: "Разбор файла",
  importing: "Запись сметы",
  matching: "Матчинг работ",
  done: "Готово",
  error: "Ошибка",
};

function statusTone(status: ImportJobStatus): "success" | "danger" | "info" {
  if (status === "done") return "success";
  if (status === "error") return "danger";
  return "info";
}

/** Идёт ли работа — по этому же признаку останавливается поллинг. */
function isRunning(status: ImportJobStatus): boolean {
  return status !== "done" && status !== "error";
}

interface EstimateUploadPanelProps {
  contractId: number;
  /**
   * Сметы договора — та же строка, что карточка уже загрузила `useContract`
   * (спека §2.9 п. 2, задача 6). Нужна ради `category_overrides_count`
   * ЗАМЕНЯЕМОЙ пары: паспорт для этого не годится (он всегда про смету с
   * `amendment_no IS NULL`, а заменить можно любое допсоглашение), а второй
   * запрос карточка не делает — этот список у неё уже есть.
   */
  estimates: EstimateRow[];
}

/** Решений, сгорающих вместе со сметой пары `amendmentNo` — 0, если пары нет
 * в списке или решений на ней не было. */
function lostDecisionsFor(estimates: EstimateRow[], amendmentNo: number | null): number {
  return estimates.find((e) => e.amendment_no === amendmentNo)?.category_overrides_count ?? 0;
}

/**
 * Загрузка сметы к договору: drag-and-drop, номер допсоглашения, статус задания
 * с поллингом, предупреждения и развилка замены (§7.1, §5).
 *
 * Три вещи, которые экран обязан различать (грабли §7 брифинга):
 *
 * * `202` — задание создано, но смета появится **позже**. Пока задание не в
 *   `done`, панель показывает этап, а не «загружено»;
 * * `200` — идемпотентный ответ: этот же файл уже загружен, ничего не делалось;
 * * `409` — не ошибка, а развилка «файл другой, нужна замена». Замена — право
 *   `admin` (§5), поэтому у `member` предложения заменить нет вовсе.
 */
export function EstimateUploadPanel({ contractId, estimates }: EstimateUploadPanelProps) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [amendmentNo, setAmendmentNo] = useState("");
  const [jobId, setJobId] = useState<number | undefined>(undefined);
  const [idempotent, setIdempotent] = useState(false);
  // `amendmentNo` заморожен в момент конфликта, а не читается заново из
  // инпута при показе диалога: пара, которую увидит подтверждение, обязана
  // быть той же самой, что дала 409, — а не тем, что пользователь успел
  // подправить в поле, пока диалог уже открыт.
  const [conflict, setConflict] = useState<{
    file: File;
    detail: string;
    amendmentNo: number | null;
  } | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);

  const upload = useUploadEstimate();
  const jobQ = useImportJob(jobId, contractId);
  const job: ImportJob | undefined = jobQ.data;
  const lostOnReplace = conflict ? lostDecisionsFor(estimates, conflict.amendmentNo) : 0;

  function parsedAmendment(): number | null {
    const raw = amendmentNo.trim();
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }

  // `amendmentNo` — параметр, не внутреннее чтение `parsedAmendment()`:
  // номер, который уходит в запрос, и номер, который показан в предупреждении
  // диалога (`conflict.amendmentNo`), обязаны быть ОДНИМ и тем же значением
  // с ОДНИМ источником у вызывающего, а не двумя независимыми чтениями поля
  // в разные моменты. Сегодня разъехаться им не даёт модальность диалога
  // (инпут не в фокус-трапе, пока он открыт), но эта гарантия — свойство
  // диалога, а не сигнатуры; если он когда-нибудь станет немодальным, разъезд
  // не должен стать тихой возможностью.
  async function send(file: File, replace: boolean, amendmentNo: number | null) {
    setRejection(null);
    setIdempotent(false);
    try {
      const created = await upload.mutateAsync({
        file,
        contract_id: contractId,
        amendment_no: amendmentNo,
        replace,
      });
      setJobId(created.id);
      // Задание уже терминальное и без работы — это правило 1 §5: текущая смета
      // пары загружена ЭТИМ же файлом, ничего не запускалось.
      setIdempotent(created.status === "done");
      setConflict(null);
    } catch (error) {
      const status = apiErrorStatus(error);
      const detail = apiErrorDetail(error) ?? "Не удалось загрузить файл.";
      if (status === 409 && isAdmin && !replace) {
        setConflict({ file, detail, amendmentNo });
        return;
      }
      setRejection(detail);
    }
  }

  function handleDrop(files: File[]) {
    const file = files[0];
    if (file) void send(file, false, parsedAmendment());
  }

  return (
    <Surface className="grid gap-4">
      <div className="grid gap-2 sm:max-w-xs">
        <Label htmlFor="amendment-no">Номер допсоглашения</Label>
        <Input
          id="amendment-no"
          inputMode="numeric"
          placeholder="пусто — исходная смета"
          value={amendmentNo}
          onChange={(e) => setAmendmentNo(e.target.value)}
        />
      </div>

      <Dropzone
        onDrop={handleDrop}
        accept={XLSX_ACCEPT}
        multiple={false}
        disabled={upload.isPending || (job !== undefined && isRunning(job.status))}
        hint="XLSX или XLSM, до 25 МБ"
      />

      {upload.isPending && (
        <p className="flex items-center gap-2 text-sm text-fg-secondary">
          <Loader2 className="size-4 animate-spin" /> Файл передаётся…
        </p>
      )}

      {rejection && (
        <p role="alert" className="flex items-start gap-2 text-sm text-danger-text">
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

          {idempotent && (
            <p className="text-sm text-fg-secondary">
              Этот файл уже был загружен для этой сметы — ничего не изменилось,
              показано прежнее задание.
            </p>
          )}

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

      <AlertDialog open={conflict !== null} onOpenChange={(open) => !open && setConflict(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Смета уже загружена</AlertDialogTitle>
            <AlertDialogDescription>
              {conflict?.detail} Замена удалит текущую смету и загрузит новую. Прежние
              задания импорта и их файлы останутся в истории — это аудит.
            </AlertDialogDescription>
          </AlertDialogHeader>

          {lostOnReplace > 0 && (
            // Число — после отдельного двоеточия, не перед существительным:
            // «N решений» не согласуется на N=1 («решение», не «решений») —
            // тот же приём, что и в тексте предупреждения `import_jobs`.
            <p role="alert" className="flex items-start gap-2 text-sm text-danger-text">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              Замена уничтожит ручной разнос заменяемой сметы; решений будет потеряно:{" "}
              {lostOnReplace}.
            </p>
          )}

          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  onClick={() => {
                    const pending = conflict;
                    setConflict(null);
                    // `pending.amendmentNo` — тот же номер, что показан в
                    // предупреждении выше: запрос обязан заменить РОВНО ту
                    // пару, число решений которой аналитик только что видел.
                    if (pending) void send(pending.file, true, pending.amendmentNo);
                  }}
                >
                  Заменить смету
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Surface>
  );
}

function CounterCell({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-fg-tertiary">{label}</dt>
      <dd className="font-mono tabular-nums text-fg">{value}</dd>
    </div>
  );
}

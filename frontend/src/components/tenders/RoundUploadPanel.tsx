import { useState } from "react";

import { ImportJobPanel } from "@/components/imports/ImportJobPanel";
import { isRunning } from "@/components/imports/jobStatus";
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
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorCode, apiErrorDetail, useImportJob, useUploadRound } from "@/services/queries";
import type { RoundImportJob, TenderRoundRow } from "@/types/domain";

/**
 * Загрузка сводной таблицы раунда (спека §2.5, §2.14). Замена — ЦЕЛИКОМ,
 * всех участников и расчётной стоимости разом (§2.6), поэтому диалог говорит
 * именно это; замена — право admin.
 */
export function RoundUploadPanel({
  tenderId,
  roundId,
  round,
}: {
  tenderId: number;
  roundId: number;
  round: TenderRoundRow;
}) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  // Поллинг стартует с ПОСЛЕДНЕГО job раунда, а не с текущего: current_job_id
  // есть только у полного done-набора (§2.12), и после перезагрузки страницы
  // идущий импорт или его ошибка исчезли бы с панели. latest_job — то, что
  // человек видел бы, не перезагружая.
  //
  // useState читает проп ОДИН раз, при монтировании. Смена раунда обязана
  // пересоздать панель целиком — родитель ставит `key={round.id}` (см.
  // TenderCardPage); иначе после `?round=` панель продолжала бы опрашивать job
  // ПРЕЖНЕГО раунда. Эффект с setState здесь не годится: правило
  // `react-hooks/set-state-in-effect` в этом проекте его запрещает (см.
  // комментарий в ContractDeleteDialog).
  const [jobId, setJobId] = useState<number | undefined>(round.latest_job?.id);
  const [idempotent, setIdempotent] = useState(false);
  const [conflict, setConflict] = useState<{ file: File; detail: string } | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const upload = useUploadRound();
  const jobQ = useImportJob(jobId, { tenderId, roundId });
  // `useImportJob` возвращает широкий `ImportJob` (см. комментарий в
  // `EstimateUploadPanel.tsx`), но здесь владелец известен заранее: эта
  // панель поллит только job'ы, созданные `useUploadRound` для РАУНДА.
  const job = jobQ.data as RoundImportJob | undefined;

  async function send(file: File, replace: boolean) {
    setRejection(null);
    setIdempotent(false);
    try {
      const created = await upload.mutateAsync({ file, tender_id: tenderId, round_id: roundId, replace });
      setJobId(created.id);
      setIdempotent(created.status === "done");
      setConflict(null);
    } catch (error) {
      const detail = apiErrorDetail(error) ?? "Не удалось загрузить файл.";
      // Диалог замены открывается ТОЛЬКО по коду `replace_required` (находка
      // внешнего ревью PR #33, finding 2), а не по голому статусу 409:
      // сервер отвечает 409 ещё в двух случаях — идёт чужой импорт этого
      // раунда (`active_import`) и гонка на индексе (тот же код), — и оба
      // означают «подождите», а не «замените». Замена уничтожает сметы
      // раунда, и предлагать её по неверной причине опасно.
      if (apiErrorCode(error) === "replace_required" && isAdmin && !replace) {
        setConflict({ file, detail });
        return;
      }
      setRejection(detail);
    }
  }

  return (
    <Surface className="grid gap-4">
      <ImportJobPanel
        job={job}
        uploading={upload.isPending}
        idempotent={idempotent}
        // Раунд-специфичная формулировка (ревью задачи 11, finding 2): замена
        // раунда касается смет ВСЕХ участников (§2.6), а не «этой сметы» —
        // унаследованный текст `EstimateUploadPanel` здесь был бы неверен.
        idempotentNote="Этот файл уже был загружен для этого раунда — ничего не изменилось, показано прежнее задание."
        // Раунд-специфичная формулировка (тот же приём, что у `idempotentNote`
        // выше): загрузка раунда создаёт сметы ВСЕХ участников (§2.6), а не
        // одну — унаследованный контрактный текст «Смета появится…» был бы
        // здесь неверен числом.
        runningHint="Сметы участников появятся в решётке после завершения — страницу закрывать не нужно"
        rejection={rejection}
        disabled={upload.isPending || (job !== undefined && isRunning(job.status))}
        hint="Сводная таблица раунда: XLSX или XLSM, до 25 МБ"
        onDrop={(files) => {
          const f = files[0];
          if (f) void send(f, false);
        }}
      />
      <AlertDialog open={conflict !== null} onOpenChange={(open) => !open && setConflict(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Раунд уже загружен</AlertDialogTitle>
            <AlertDialogDescription>
              {conflict?.detail} Замена удалит сметы всех участников и расчётной стоимости этого раунда и загрузит
              новый файл. Участники в решётке останутся; прежние задания импорта и их файлы — тоже, это аудит.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  onClick={() => {
                    const p = conflict;
                    setConflict(null);
                    if (p) void send(p.file, true);
                  }}
                >
                  Заменить раунд
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Surface>
  );
}

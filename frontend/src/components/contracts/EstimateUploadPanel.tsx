import { useState } from "react";
import { AlertTriangle } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser } from "@/hooks/useAuth";
import {
  apiErrorDetail,
  apiErrorStatus,
  useContract,
  useImportJob,
  useUploadEstimate,
} from "@/services/queries";
import type { ContractImportJob, EstimateRow } from "@/types/domain";

interface EstimateUploadPanelProps {
  contractId: number;
}

// Раньше здесь был проп `estimates` (карточка договора, которую уже загрузил
// родитель) — панель брала из него `category_overrides_count` ЗАМЕНЯЕМОЙ пары
// для диалога (§2.9 п. 2). Находка ревью PR #16 (finding 3) закрыла на нём
// последнюю дверь: панель сама держит `useContract` и на 409 ждёт СВЕЖИЙ
// ответ сервера, а числу из чужого пропа — как и устаревшему кэшу — доверять
// нельзя (не только «может устареть», а именно «непроверено»). Как только
// фолбэк на проп убрали, у пропа не осталось ни одного читателя — он снят
// целиком, а не оставлен неиспользуемым ради формы: у карточки договора
// (`ContractCardPage.tsx`) он тоже снят.

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
export function EstimateUploadPanel({ contractId }: EstimateUploadPanelProps) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [amendmentNo, setAmendmentNo] = useState("");
  const [jobId, setJobId] = useState<number | undefined>(undefined);
  const [idempotent, setIdempotent] = useState(false);
  // `amendmentNo` заморожен в момент конфликта, а не читается заново из
  // инпута при показе диалога: пара, которую увидит подтверждение, обязана
  // быть той же самой, что дала 409, — а не тем, что пользователь успел
  // подправить в поле, пока диалог уже открыт. `estimates` заморожен там же и
  // по той же причине, но источник у него ЕДИНСТВЕННЫЙ (находка ревью PR #16,
  // finding 2а и 3): результат явного рефетча карточки на 409, дождавшийся
  // ответа сервера ДО того, как диалог открылся, — см. `send` ниже. Диалог
  // открывается ТОЛЬКО когда этот рефетч успешен; фолбэка на кэш или на что
  // бы то ни было ещё здесь нет и не должно быть.
  const [conflict, setConflict] = useState<{
    file: File;
    detail: string;
    amendmentNo: number | null;
    estimates: EstimateRow[];
  } | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);

  const upload = useUploadEstimate();
  const jobQ = useImportJob(jobId, { contractId });
  // `useImportJob` возвращает широкий `ImportJob` (владелец не различён в
  // типе — см. докстроку `ImportJob` в `types/domain.ts`), но ЗДЕСЬ владелец
  // известен заранее: эта панель поллит только job'ы, созданные
  // `useUploadEstimate` для ДОГОВОРА. Сужение к `ContractImportJob` в точке
  // вызова (задача 11) — не изменение поведения, а называние того, что уже
  // истинно.
  const job = jobQ.data as ContractImportJob | undefined;
  const contractQ = useContract(contractId);
  const lostOnReplace = conflict ? lostDecisionsFor(conflict.estimates, conflict.amendmentNo) : 0;

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
        // Число решений в диалоге обязано быть тем, что сервер держит В
        // МОМЕНТ конфликта (находка ревью PR #16): рефетч карточки — и
        // ДОЖДАТЬСЯ его — ПЕРЕД открытием диалога, а не подстановка того,
        // что успел закэшировать `useContract` до этого 409. Диалог не
        // открывается на заведомо устаревшем счёте.
        const fresh = await contractQ.refetch();
        // Находка ревью (finding 3): в TanStack Query v5 `refetch()` НЕ
        // бросает по умолчанию (`throwOnError: false`) — упавший запрос
        // спокойно резолвится с `isError: true`, а `data` при этом держит
        // ПОСЛЕДНЕЕ УСПЕШНОЕ значение (устаревшее) либо вовсе `undefined`,
        // если успеха не было НИКОГДА. `fresh.data?.estimates ?? estimates`
        // тогда подставил бы в диалог непроверенное число одним из двух
        // путей — оба хуже отсутствия диалога: обязательство спеки §2.9 —
        // объявить утрату ДО неё, а непроверенный счёт способен её только
        // ЗАНИЗИТЬ молча. Поэтому при ошибке или отсутствии данных диалог
        // не открывается вовсе — отказ идёт через `rejection` и явно зовёт
        // повторить загрузку, чтобы аналитик не остался в тупике.
        if (fresh.isError || fresh.data === undefined) {
          setRejection(
            "Смета уже загружена, но проверить текущее число ручных решений по ней " +
              "не удалось — замена сейчас не предлагается. Повторите загрузку."
          );
          return;
        }
        setConflict({ file, detail, amendmentNo, estimates: fresh.data.estimates });
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
      <ImportJobPanel
        job={job}
        uploading={upload.isPending}
        idempotent={idempotent}
        // Формулировка — БУКВА В БУКВУ прежний, вшитый в панель текст:
        // существующие тесты (`EstimateUploadPanel.test.tsx`,
        // `ContractCardPage.test.tsx`) ищут именно эту строку, и правка
        // задачи 11 (ревью, finding 2) не имеет права менять их утверждения.
        idempotentNote="Этот файл уже был загружен для этой сметы — ничего не изменилось, показано прежнее задание."
        // БУКВА В БУКВУ прежний зашитый текст панели (ревью финального fix
        // wave, finding 5) — `ContractCardPage.test.tsx` ищет именно эту
        // строку, правка не имеет права сдвинуть её утверждение.
        runningHint="Смета появится в карточке после завершения — страницу закрывать не нужно"
        rejection={rejection}
        disabled={upload.isPending || (job !== undefined && isRunning(job.status))}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={handleDrop}
      >
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
      </ImportJobPanel>

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

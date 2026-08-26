import { useEffect } from "react";

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
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
import { apiErrorCode, apiErrorContext, useDeleteParticipant } from "@/services/queries";
import type { ParticipantDeletionPreview, TenderParticipant } from "@/types/domain";

interface ParticipantDeleteDialogProps {
  tenderId: number;
  participant: TenderParticipant | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Удаление участника — протокол `confirmation_token` (спека §2.11).
 *
 * ОТКРЫТИЕ ДИАЛОГА — это уже первый запрос: без токена сервер ВСЕГДА отвечает
 * 409 `confirmation_required` со свежим preview состава (сколько раундов,
 * смет, позиций и ручных решений уйдёт вместе с участником). Человек не может
 * подтвердить состав, которого не видел, — поэтому preview показывается ДО
 * кнопки удаления, а не вместо неё.
 *
 * Кнопка «Удалить участника» шлёт ВТОРОЙ запрос, уже с токеном из preview.
 * Если состав успел измениться между первым запросом и нажатием (кто-то
 * загрузил новый раунд), сервер отвечает НОВЫМ 409 с ДРУГИМ токеном — тот же
 * код `confirmation_required`, и preview на экране просто обновляется. Диалог
 * специально не закрывается в этом случае: закрыть его значило бы утверждать
 * успех там, где сервер его не подтвердил.
 */
export function ParticipantDeleteDialog({ tenderId, participant, onOpenChange }: ParticipantDeleteDialogProps) {
  return (
    <AlertDialog open={participant !== null} onOpenChange={(open) => !open && onOpenChange(false)}>
      <AlertDialogContent>
        {participant && (
          <ParticipantDeleteBody
            key={participant.package_id}
            tenderId={tenderId}
            participant={participant}
            onOpenChange={onOpenChange}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ParticipantDeleteBody({
  tenderId,
  participant,
  onOpenChange,
}: {
  tenderId: number;
  participant: TenderParticipant;
  onOpenChange: (open: boolean) => void;
}) {
  const remove = useDeleteParticipant();
  // `mutate` — отдельная переменная, а не `remove.mutate` внутри эффекта: так
  // `exhaustive-deps` видит ровно то, что использует эффект, и не требует
  // добавить в зависимости весь `remove` (он — новый объект на каждый рендер,
  // `mutate` внутри него — `useCallback` от стабильного наблюдателя, заведённого
  // один раз через `useState` в `useMutation`, node_modules/@tanstack/
  // react-query/build/legacy/useMutation.js).
  const { mutate } = remove;

  // Первый запрос — БЕЗ токена, ровно один раз на открытие: родитель ставит
  // `key={participant.package_id}` на этот компонент (см. `ParticipantDeleteDialog`
  // выше), поэтому смена участника пересоздаёт его целиком, а не переиспользует
  // эффект со старым замыканием.
  useEffect(() => {
    mutate({ tenderId, packageId: participant.package_id });
  }, [mutate, tenderId, participant.package_id]);

  const code = apiErrorCode(remove.error);
  const isActiveImport = code === "active_import";
  const preview =
    code === "confirmation_required" ? apiErrorContext<ParticipantDeletionPreview>(remove.error) : undefined;

  function handleConfirm() {
    if (!preview) return;
    remove.mutate(
      { tenderId, packageId: participant.package_id, confirmationToken: preview.confirmation_token },
      { onSuccess: () => onOpenChange(false) }
    );
  }

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>Удалить участника «{participant.title}»?</AlertDialogTitle>
        <AlertDialogDescription>
          {isActiveImport && "Импорт раунда выполняется — дождитесь его завершения и повторите."}
          {!isActiveImport && !preview && "Проверяем состав удаляемого…"}
          {preview && (
            <>
              {preview.message} Раундов: {preview.rounds_count} · Смет: {preview.estimates_count} · Позиций:{" "}
              {formatNumber(preview.positions_count)} · Решений вручную: {preview.overrides_count}.
            </>
          )}
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
        {!isActiveImport && (
          <AlertDialogAction
            render={
              <Button
                variant="destructive"
                disabled={!preview || remove.isPending}
                onClick={handleConfirm}
              >
                Удалить участника
              </Button>
            }
          />
        )}
      </AlertDialogFooter>
    </>
  );
}

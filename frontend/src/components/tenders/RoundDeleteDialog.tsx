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
import { useDeleteRound } from "@/services/queries";
import type { TenderRoundRow } from "@/types/domain";

interface RoundDeleteDialogProps {
  tenderId: number;
  round: TenderRoundRow | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Удаление этапа (спека §2.13, §2.6): уносит сметы ВСЕХ участников этого
 * раунда и историю его загрузок с файлами — то же, что говорит `RoundUploadPanel`
 * про замену раунда целиком, только необратимо.
 */
export function RoundDeleteDialog({ tenderId, round, onOpenChange }: RoundDeleteDialogProps) {
  const remove = useDeleteRound();

  return (
    <AlertDialog open={round !== null} onOpenChange={(open) => !open && onOpenChange(false)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Удалить этап {round?.stage_no}?</AlertDialogTitle>
          <AlertDialogDescription>
            Уйдут сметы всех участников этого раунда и история его загрузок с файлами. Действие
            необратимо.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <AlertDialogAction
            render={
              <Button
                variant="destructive"
                disabled={remove.isPending}
                onClick={() => {
                  if (!round) return;
                  remove.mutate(
                    { tenderId, roundId: round.id },
                    { onSuccess: () => onOpenChange(false) }
                  );
                }}
              >
                Удалить этап
              </Button>
            }
          />
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

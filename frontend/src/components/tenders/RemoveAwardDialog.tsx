import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useRemoveAward } from "@/services/queries";
import type { TenderAward } from "@/types/domain";

interface RemoveAwardDialogProps {
  tenderId: number;
  award: TenderAward;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Снять отметку?» (спека Б2 §2.8, макет экран 3): исправление ошибки, следа в
 * истории не остаётся. Подтверждение — обычная кнопка, а не `AlertDialogAction`:
 * та закрывает окно сразу, а при отказе сервера окно должно остаться.
 */
export function RemoveAwardDialog({ tenderId, award, onOpenChange }: RemoveAwardDialogProps) {
  const remove = useRemoveAward(tenderId);

  return (
    <AlertDialog open onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Снять отметку?</AlertDialogTitle>
          <AlertDialogDescription>
            Отметка удаляется без следа — это для исправления ошибки. Если договор с победителем не
            заключили, закройте окно и выберите «Договор не заключён», чтобы это осталось в истории.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <Button
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(award.id, {
                onSuccess: () => onOpenChange(false),
              })
            }
          >
            Снять отметку
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

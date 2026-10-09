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
import { useUnlinkTenderAward } from "@/services/queries";
import type { ContractCard } from "@/types/domain";

/**
 * Подтверждение «Отвязать от тендера» (спека Б2 §2.8). Снимается только
 * основание: договор и его смета остаются, а отметка победителя в тендере
 * снова стоит без договора. Кто и когда вправе отвязать, решает сервер, окно
 * показывает его отказ текстом (`toastApiError`) и остаётся открытым.
 */
export function UnlinkTenderDialog({
  contract,
  onOpenChange,
}: {
  contract: ContractCard | null;
  onOpenChange: (open: boolean) => void;
}) {
  const unlink = useUnlinkTenderAward();
  const basis = contract?.tender_basis ?? null;

  return (
    <AlertDialog
      open={contract !== null && basis !== null}
      onOpenChange={(open) => !open && onOpenChange(false)}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Отвязать договор «{contract?.contract_number}» от тендера № {basis?.tender_number}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            Договор и его смета останутся, пропадёт только основание «по тендеру». Отметка
            победителя в тендере снова будет стоять без договора, а объект и подрядчика договора
            можно будет править как обычно.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <AlertDialogAction
            render={
              <Button
                disabled={unlink.isPending}
                onClick={() => {
                  if (!contract || !basis) return;
                  unlink.mutate(
                    { contractId: contract.id, tenderId: basis.tender_id },
                    { onSuccess: () => onOpenChange(false) }
                  );
                }}
              >
                Отвязать
              </Button>
            }
          />
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

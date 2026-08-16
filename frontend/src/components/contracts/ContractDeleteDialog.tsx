import { useState } from "react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useContractImportJobs, useDeleteContract } from "@/services/queries";

type Target = { id: number; contract_number: string; estimates_count?: number };

/**
 * Подтверждение каскадного удаления договора (спека §2.6).
 *
 * Кнопка разблокируется только точным вводом номера договора: операция уносит
 * сметы вместе с историей загрузок и необратима, а «Вы уверены?» нажимается
 * мимо. Состав удаляемого называется вслух — число заданий берётся из
 * `GET /contracts/:id/import-jobs`, то есть из того же источника, что и история
 * на карточке.
 */
export function ContractDeleteDialog({
  contract,
  onOpenChange,
  onDeleted,
}: {
  contract: Target | null;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const remove = useDeleteContract();
  const jobsQ = useContractImportJobs(contract?.id);

  // Сброс поля при смене договора — БЕЗ `useEffect` (react-hooks/set-state-in-effect):
  // это официально задокументированный паттерн "adjusting state when a prop
  // changes" (react.dev/learn/you-might-not-need-an-effect), обновление состояния
  // происходит синхронно в теле рендера и не даёт лишнего кадра со старым значением.
  const [renderedContractId, setRenderedContractId] = useState(contract?.id);
  if (renderedContractId !== contract?.id) {
    setRenderedContractId(contract?.id);
    setTyped("");
  }

  // Сверка ТОЧНАЯ, без `trim()`: «разрешим пробелы по краям» — это уже не тот
  // номер, который человека просили набрать, а операция необратима.
  const confirmed = contract !== null && typed === contract.contract_number;
  // Пока история загрузок не пришла, состав удаляемого ещё не назван — кнопка
  // не может быть доступна: иначе подтверждают вслепую. Ошибка запроса — то же
  // самое, только хуже: число заданий неизвестно и уже не придёт.
  const compositionKnown = jobsQ.isSuccess;

  return (
    <AlertDialog open={contract !== null} onOpenChange={(open) => !open && onOpenChange(false)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Удалить договор «{contract?.contract_number}»?</AlertDialogTitle>
          <AlertDialogDescription>
            Вместе с договором будут удалены его сметы
            {contract?.estimates_count !== undefined ? ` (${contract.estimates_count})` : ""},
            задания импорта ({jobsQ.data?.length ?? "…"}) и загруженные файлы. Действие
            необратимо: восстановить их из приложения будет нельзя.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <Label htmlFor="confirm-contract-number">
            Введите номер договора, чтобы подтвердить
          </Label>
          <Input
            id="confirm-contract-number"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <AlertDialogAction
            render={
              <Button
                variant="destructive"
                disabled={!confirmed || !compositionKnown || remove.isPending}
                onClick={() => {
                  if (!contract || !confirmed) return;
                  remove.mutate(contract.id, {
                    onSuccess: () => {
                      onOpenChange(false);
                      onDeleted?.();
                    },
                  });
                }}
              >
                Удалить договор
              </Button>
            }
          />
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

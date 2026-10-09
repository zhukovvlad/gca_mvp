import { useState } from "react";

import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { formatDate, formatMillionsVat } from "@/lib/format";
import { useContractCandidates, useLinkContract } from "@/services/queries";
import type { TenderAward } from "@/types/domain";

interface LinkContractDialogProps {
  tenderId: number;
  award: TenderAward;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Привязать существующий договор» (спека Б2 §2.8, макет экран 5): кандидаты —
 * договоры того же объекта с тем же подрядчиком и без основания, один выбор
 * радиогруппой. Сумма — «… млн с НДС», `null` — «итог недоступен». Смета
 * договора не меняется. Отказ сервера уже показан тостом — окно и выбор
 * остаются.
 */
export function LinkContractDialog({ tenderId, award, onOpenChange }: LinkContractDialogProps) {
  const [selected, setSelected] = useState<string>("");
  const candidatesQ = useContractCandidates(tenderId, award.id);
  const link = useLinkContract(tenderId);
  const candidates = candidatesQ.data;

  async function handleLink() {
    try {
      await link.mutateAsync({ awardId: award.id, contractId: Number(selected) });
      onOpenChange(false);
    } catch {
      // Причина уже в тосте; окно остаётся открытым.
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Привязать существующий договор</DialogTitle>
          <DialogDescription>
            Только договоры <b>того же объекта</b> с <b>тем же подрядчиком</b>. Смета договора не меняется —
            договор просто получает основание «по тендеру».
          </DialogDescription>
        </DialogHeader>

        {candidatesQ.isPending && <Skeleton className="h-24 w-full" />}
        {candidatesQ.isError && (
          <p className="text-sm text-danger-text">Не удалось загрузить список договоров.</p>
        )}
        {candidates && candidates.length === 0 && (
          <div className="py-4 text-center text-sm text-fg-secondary">
            <b className="block text-base text-fg">Нет подходящих договоров</b>
            У подрядчика нет договоров на этом объекте: договор можно создать из КП.
          </div>
        )}
        {candidates && candidates.length > 0 && (
          <>
            <RadioGroup value={selected} onValueChange={(value) => setSelected(String(value))}>
              {candidates.map((candidate) => (
                <Label
                  key={candidate.id}
                  htmlFor={`link-candidate-${candidate.id}`}
                  className="flex cursor-pointer items-start gap-3 rounded-lg border border-border-subtle p-3 has-data-checked:border-accent-border has-data-checked:bg-accent-soft"
                >
                  <RadioGroupItem
                    id={`link-candidate-${candidate.id}`}
                    value={String(candidate.id)}
                    className="mt-0.5"
                  />
                  <span className="grid gap-0.5">
                    <span className="text-sm font-semibold">
                      № {candidate.contract_number} от {formatDate(candidate.signed_date)}
                    </span>
                    <span className="text-xs font-normal text-fg-secondary">
                      {candidate.object_title} · {candidate.contractor_title} ·{" "}
                      {formatMillionsVat(candidate.base_total_including_vat)}
                    </span>
                  </span>
                </Label>
              ))}
            </RadioGroup>
            {candidates.some((c) => c.base_total_including_vat === null) && (
              <p className="text-xs text-fg-tertiary">
                «Итог недоступен» — у договора нет сметы либо итог «с НДС» в файле не определён (лоты
                расходятся).
              </p>
            )}
          </>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="button" disabled={selected === "" || link.isPending} onClick={handleLink}>
            Привязать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

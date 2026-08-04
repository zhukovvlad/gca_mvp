import { useState } from "react";

import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { multiplyDecimalStrings, normalizeDecimalInput } from "@/lib/decimal";
import { useReapproveRateStandard } from "@/services/queries";
import type { RateStandard } from "@/types/domain";

interface ReapproveDialogProps {
  standard: RateStandard | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Переутверждение норматива (§4, §7.3).
 *
 * Это не правка ставки: прежний период закрывается датой начала нового, а новая
 * ставка ложится отдельной строкой. Поэтому отклонения смет, датированных до
 * переутверждения, не меняются — и об этом сказано прямо в диалоге, потому что
 * иначе оператор ждал бы от кнопки другого.
 */
export function ReapproveDialog({ standard, onOpenChange }: ReapproveDialogProps) {
  return (
    <Dialog open={standard !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        {/*
          Форма — отдельный компонент, и в дереве её нет, пока `standard` пуст.
          Значит на каждое открытие она монтируется заново, а начальные значения
          задаёт `useState`. Так не нужен эффект, сбрасывающий поля: setState в
          эффекте вызывает каскадный рендер, и React прямо его не рекомендует.
        */}
        {standard && (
          <ReapproveForm key={standard.id} standard={standard} onOpenChange={onOpenChange} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ReapproveForm({
  standard,
  onOpenChange,
}: {
  standard: RateStandard;
  onOpenChange: (open: boolean) => void;
}) {
  const [validFrom, setValidFrom] = useState("");
  const [index, setIndex] = useState("");
  const [rate, setRate] = useState("");
  const [rateTouched, setRateTouched] = useState(false);
  const [approvedBy, setApprovedBy] = useState("");
  const [note, setNote] = useState("");

  const reapprove = useReapproveRateStandard();

  function handleIndexChange(value: string) {
    setIndex(value);
    // Предзаполнение «прежняя × индекс» (§7.3) считается точно, без float, и
    // перестаёт работать, как только человек правил ставку сам: перетирать
    // введённое значило бы отменять его решение.
    if (!rateTouched && value.trim()) {
      const computed = multiplyDecimalStrings(
        standard.standard_unit_rate,
        normalizeDecimalInput(value)
      );
      if (computed !== null) setRate(computed);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!validFrom) return;
    try {
      await reapprove.mutateAsync({
        id: standard.id,
        input: {
          valid_from: validFrom,
          standard_unit_rate: normalizeDecimalInput(rate) || null,
          inflation_index: normalizeDecimalInput(index) || null,
          approved_by: approvedBy.trim() || null,
          note: note.trim() || null,
        },
      });
      onOpenChange(false);
    } catch {
      // Отказ уже в тосте: дата не позже начала прежнего периода, либо он закрыт.
    }
  }

  const canSubmit = validFrom !== "" && (rate.trim() !== "" || index.trim() !== "");

  return (
    <>
      <DialogHeader>
        <DialogTitle>Переутвердить норматив</DialogTitle>
        <DialogDescription>
          «{standard.catalog_position_title}» · {standard.rate_class_title}. Прежний период
          закроется датой начала нового, прежняя ставка останется в истории — отклонения
          смет до этой даты не изменятся.
        </DialogDescription>
      </DialogHeader>

      <p className="text-sm text-fg-secondary">
        Действующая ставка: <MoneyCell value={standard.standard_unit_rate} /> с{" "}
        {standard.valid_from}
      </p>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="grid gap-2">
            <Label htmlFor="reapprove-from">Действует с</Label>
            <Input
              id="reapprove-from"
              type="date"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="reapprove-index">Индекс инфляции</Label>
            <Input
              id="reapprove-index"
              inputMode="decimal"
              placeholder="1.07"
              value={index}
              onChange={(e) => handleIndexChange(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="reapprove-rate">Новая ставка</Label>
            <Input
              id="reapprove-rate"
              inputMode="decimal"
              value={rate}
              onChange={(e) => {
                setRate(e.target.value);
                setRateTouched(true);
              }}
            />
          </div>
        </div>
        <p className="text-xs text-fg-tertiary">
          Ставка предзаполняется как «прежняя × индекс» и остаётся редактируемой. Индекс
          сохранится как обоснование, даже если ставку задать вручную.
        </p>

        <div className="grid gap-2">
          <Label htmlFor="reapprove-approved-by">Утвердил</Label>
          <Input
            id="reapprove-approved-by"
            value={approvedBy}
            onChange={(e) => setApprovedBy(e.target.value)}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="reapprove-note">Примечание</Label>
          <Textarea
            id="reapprove-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || reapprove.isPending}>
            Переутвердить
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}

import { useState, type FormEvent } from "react";

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
import { useMarkNotConcluded } from "@/services/queries";
import type { TenderAward } from "@/types/domain";

interface NotConcludedDialogProps {
  tenderId: number;
  award: TenderAward;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Договор не заключён» (спека Б2 §2.8, макет экран 3): отметка закрывается и
 * остаётся в истории тендера. Дата обязательна; пустой комментарий уходит
 * `null`, а не пустой строкой. Отказ сервера уже показан тостом
 * (`toastApiError`) — окно остаётся открытым.
 */
export function NotConcludedDialog({ tenderId, award, onOpenChange }: NotConcludedDialogProps) {
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const mark = useMarkNotConcluded(tenderId);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (date === "" || mark.isPending) return;
    try {
      await mark.mutateAsync({
        awardId: award.id,
        notConcludedOn: date,
        note: note.trim() === "" ? null : note.trim(),
      });
      onOpenChange(false);
    } catch {
      // Причина уже в тосте; окно остаётся открытым, введённое не теряется.
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Договор не заключён</DialogTitle>
          <DialogDescription>
            {award.contractor_title} перестаёт быть победителем. Запись останется в истории тендера, после
            чего можно отметить другого участника.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="not-concluded-date">Дата</Label>
            <Input
              id="not-concluded-date"
              type="date"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="not-concluded-note">Комментарий</Label>
            <Textarea
              id="not-concluded-note"
              rows={3}
              placeholder="необязательно"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={date === "" || mark.isPending}>
              Записать
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

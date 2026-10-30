import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorDetail, useSetOfferVatBase } from "@/services/queries";
import type { ID } from "@/types/common";

import {
  VAT_BASE_ASSIGN_LABEL,
  VAT_BASE_DIALOG_DESCRIPTION,
  VAT_BASE_DIALOG_TITLE,
  VAT_BASE_FIELD_LABEL,
  VAT_BASE_SAVE_FALLBACK,
  VAT_BASE_SAVE_LABEL,
} from "./ourPriceCopy";

/**
 * «Назначить базу НДС» КП с неизвестной базой (спека 4б §2.12). Ставка идёт на сервер
 * строкой (запятая заменяется точкой): `Number()` над ставкой - это `float`, запрещённый
 * `AGENTS.md` §3. Диапазон 0-100 проверяет сервер, отказ показывается здесь же.
 *
 * Право - `admin`: диалог сам читает `useCurrentUser()` и у остальных не рисуется.
 */
export function VatBaseDialog({
  estimateId,
  tenderId,
  roundId,
}: {
  estimateId: ID;
  tenderId: ID;
  roundId: ID;
}) {
  const { data: user } = useCurrentUser();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const mutation = useSetOfferVatBase(tenderId, roundId);

  if (user?.role !== "admin") return null;

  const rate = draft.trim().replace(",", ".");

  const onOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setDraft("");
      mutation.reset();
    }
  };

  const save = () =>
    mutation.mutate(
      { estimateId, baseOverride: rate },
      {
        onSuccess: () => onOpenChange(false),
      }
    );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>{VAT_BASE_ASSIGN_LABEL}</DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{VAT_BASE_DIALOG_TITLE}</DialogTitle>
          <DialogDescription>{VAT_BASE_DIALOG_DESCRIPTION}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <Label htmlFor="kp-vat-base">{VAT_BASE_FIELD_LABEL}</Label>
          <Input
            id="kp-vat-base"
            inputMode="decimal"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          {mutation.isError && (
            <p role="alert" className="text-sm text-danger-text">
              {apiErrorDetail(mutation.error) ?? VAT_BASE_SAVE_FALLBACK}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button type="button" disabled={rate === "" || mutation.isPending} onClick={save}>
            {VAT_BASE_SAVE_LABEL}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

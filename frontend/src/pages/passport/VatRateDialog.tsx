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
import { apiErrorDetail, useSetEstimateVat } from "@/services/queries";
import type { ID } from "@/types/common";
import type { Decimal, ProjectPassportEstimate } from "@/types/domain";

/**
 * Правка ставок НДС сметы (спека пересчёта §2.7, задача 10).
 *
 * Поля СТРОКОВЫЕ и уезжают строками: `Number()` над ставкой — это `float`,
 * запрещённый §3 на всех слоях. Отсутствующее поле и `null` в теле запроса
 * различаются намеренно: первое значит «не менять», второе — «снять», и
 * слать оба сразу нельзя, иначе снятие цели затирало бы базу вместе с ней
 * (`changedOnly` ниже шлёт только реально изменённое поле).
 *
 * **Право — `admin`** (`backend/routers/estimate_vat.py`: `require_admin`,
 * тот же вес, что у замены смет и нормативов): диалог не принимает `canEdit`
 * снаружи, а сам читает `useCurrentUser()` — тот же приём, что у
 * `EstimateUploadPanel`. Внешний проп дублировал бы источник и разошёлся бы с
 * ним при первой же смене роли пользователя посреди сессии.
 */
export function VatRateDialog({
  estimate,
  contractId,
}: {
  estimate: ProjectPassportEstimate;
  contractId: ID;
}) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  // Базовая ставка (какой соответствуют суммы файла): назначенная вручную,
  // иначе заявленная файлом. `null` — ни файл, ни человек её не знают, и
  // пересчитывать не от чего (спека §2.7).
  const base = estimate.vat_rate_base_override ?? estimate.vat_rate;

  const [baseDraft, setBaseDraft] = useState(estimate.vat_rate_base_override ?? "");
  const [targetDraft, setTargetDraft] = useState(estimate.vat_rate_target ?? "");
  const mutation = useSetEstimateVat();

  if (!isAdmin) return null;

  const submit = (input: { base_override?: Decimal | null; target?: Decimal | null }) =>
    mutation.mutate({ estimateId: estimate.id, contractId, input });

  return (
    <Dialog>
      {/*
        `@base-ui/react` не поддерживает Radix-приём `asChild`: он передал бы
        неизвестный проп сквозь и оставил бы триггер собственной кнопкой —
        `<Button>` внутри отрисовался бы ВТОРЫМ, вложенным `<button>`. Приём
        этого примитива — `render` (см. `DialogClose` в `dialog.tsx`).
      */}
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        Изменить ставку
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Ставка НДС сметы</DialogTitle>
          <DialogDescription>
            Базовая ставка — та, которой соответствуют суммы файла. Ставка показа
            пересчитывает деньги паспорта, не трогая файл.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="vat-base">Базовая ставка (какой соответствуют суммы файла)</Label>
            <Input
              id="vat-base"
              inputMode="decimal"
              value={baseDraft}
              onChange={(e) => setBaseDraft(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="vat-target">Ставка показа</Label>
            <Input
              id="vat-target"
              inputMode="decimal"
              value={targetDraft}
              disabled={base === null}
              onChange={(e) => setTargetDraft(e.target.value)}
            />
            {base === null && (
              <p role="note" className="text-xs text-fg-tertiary">
                Сначала объявите базовую ставку — пересчитывать не от чего.
              </p>
            )}
          </div>

          {mutation.isError && (
            <p role="alert" className="text-sm text-danger-text">
              {apiErrorDetail(mutation.error) ?? "Не удалось сохранить ставку НДС."}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            disabled={estimate.vat_rate_target === null || mutation.isPending}
            onClick={() => submit({ target: null })}
          >
            Снять ставку показа
          </Button>
          <Button
            type="button"
            disabled={mutation.isPending}
            onClick={() => submit(changedOnly(estimate, baseDraft, targetDraft))}
          >
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Только изменённые поля: неизменённое не отправляется вовсе (§2.7) —
 * отсутствующее поле значит «не менять», и подмешивать его текущим значением
 * значило бы слать то же самое под видом правки.
 */
function changedOnly(
  estimate: ProjectPassportEstimate,
  baseDraft: string,
  targetDraft: string
): { base_override?: Decimal | null; target?: Decimal | null } {
  const input: { base_override?: Decimal | null; target?: Decimal | null } = {};
  const asValue = (draft: string): Decimal | null => (draft.trim() === "" ? null : draft.trim());

  if (asValue(baseDraft) !== (estimate.vat_rate_base_override ?? null)) {
    input.base_override = asValue(baseDraft);
  }
  if (asValue(targetDraft) !== (estimate.vat_rate_target ?? null)) {
    input.target = asValue(targetDraft);
  }
  return input;
}

import { useState } from "react";

import { EntityCombobox } from "@/components/domain/EntityCombobox";
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
import { useDebounce } from "@/lib/useDebounce";
import { useCreateTender, useObjects, useRateClasses, useUpdateTender } from "@/services/queries";
import type { TenderCard, TenderInput } from "@/types/domain";

interface TenderFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Задан — правка карточки, иначе создание. */
  tender?: TenderCard;
  onCreated?: (tender: TenderCard) => void;
}

/**
 * Форма тендера (спека §2.13), по образцу `ContractFormDialog`.
 *
 * Объект, номер тендера и класс фиксируются при заведении и снимком на момент
 * торга (§4) — `useUpdateTender` принимает только `title`/`notes`, поэтому в
 * режиме правки эти поля не редактируются вовсе, а не просто задизейблены:
 * задизейбленное поле подразумевало бы, что сервер его всё же примет.
 */
export function TenderFormDialog({ open, onOpenChange, tender, onCreated }: TenderFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        {open && (
          <TenderForm key={tender?.id ?? "new"} tender={tender} onOpenChange={onOpenChange} onCreated={onCreated} />
        )}
      </DialogContent>
    </Dialog>
  );
}

interface FormState {
  object_id: number | null;
  rate_class_id: number | null;
  tender_number: string;
  title: string;
  notes: string;
}

const EMPTY: FormState = {
  object_id: null,
  rate_class_id: null,
  tender_number: "",
  title: "",
  notes: "",
};

function fromTender(tender: TenderCard): FormState {
  return {
    object_id: tender.object_id,
    rate_class_id: tender.rate_class_id,
    tender_number: tender.tender_number,
    title: tender.title,
    notes: tender.notes ?? "",
  };
}

function TenderForm({
  tender,
  onOpenChange,
  onCreated,
}: {
  tender?: TenderCard;
  onOpenChange: (open: boolean) => void;
  onCreated?: (tender: TenderCard) => void;
}) {
  const isEdit = tender !== undefined;
  const [form, setForm] = useState<FormState>(tender ? fromTender(tender) : EMPTY);

  const [objectQuery, setObjectQuery] = useState("");
  const objectSearch = useDebounce(objectQuery, 300);
  const [objectLabel, setObjectLabel] = useState(tender?.object_title ?? "");
  const [classLabel, setClassLabel] = useState(tender?.rate_class_title ?? "");
  const [classQuery, setClassQuery] = useState("");

  const objectsQ = useObjects({ q: objectSearch || undefined, page_size: 20 });
  // Классы приходят одним списком без пагинации, как у ContractFormDialog —
  // серверный `q` здесь не нужен, страницы нет.
  const classesQ = useRateClasses();

  const createTender = useCreateTender();
  const updateTender = useUpdateTender();

  function patch(fields: Partial<FormState>) {
    setForm((prev) => ({ ...prev, ...fields }));
  }

  const classNeedle = classQuery.trim().toLowerCase();
  const visibleClasses = (classesQ.data ?? []).filter(
    (rateClass) => !classNeedle || rateClass.title.toLowerCase().includes(classNeedle)
  );

  const canSubmit = isEdit
    ? form.title.trim().length > 0
    : form.object_id !== null && form.tender_number.trim().length > 0 && form.title.trim().length > 0;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;

    try {
      if (isEdit) {
        await updateTender.mutateAsync({
          id: tender.id,
          input: { title: form.title.trim(), notes: form.notes.trim() || null },
        });
      } else {
        const payload: TenderInput = {
          object_id: form.object_id as number,
          title: form.title.trim(),
          tender_number: form.tender_number.trim(),
          rate_class_id: form.rate_class_id,
          notes: form.notes.trim() || null,
        };
        const created = await createTender.mutateAsync(payload);
        onCreated?.(created);
      }
      onOpenChange(false);
    } catch {
      // Текст отказа уже показан тостом (`toastApiError`); диалог оставляем
      // открытым, чтобы человек исправил введённое.
    }
  }

  const pending = createTender.isPending || updateTender.isPending;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{isEdit ? "Правка тендера" : "Новый тендер"}</DialogTitle>
        <DialogDescription>
          {isEdit
            ? "Меняются только предмет и примечания — объект, номер и класс зафиксированы при заведении и не меняются."
            : "Объект, номер тендера и класс фиксируются при заведении и позже не меняются."}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        {!isEdit && (
          <>
            <div className="grid gap-2">
              <Label htmlFor="tender-object">Объект</Label>
              <EntityCombobox
                id="tender-object"
                items={objectsQ.data?.items ?? []}
                value={form.object_id}
                onChange={(item) => {
                  setObjectLabel(item?.title ?? "");
                  patch({ object_id: item?.id ?? null });
                }}
                getLabel={(item) => item.title}
                getHint={(item) => item.rate_class_title ?? undefined}
                placeholder="Выберите объект"
                searchPlaceholder="Название или адрес объекта"
                emptyText="Объект не найден"
                onQueryChange={setObjectQuery}
                selectedLabel={objectLabel}
                loading={objectsQ.isFetching}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="tender-number">Номер тендера</Label>
              <Input
                id="tender-number"
                value={form.tender_number}
                onChange={(e) => patch({ tender_number: e.target.value })}
                required
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="tender-rate-class">Класс объектов</Label>
              <EntityCombobox
                id="tender-rate-class"
                items={visibleClasses}
                value={form.rate_class_id}
                onChange={(item) => {
                  setClassLabel(item?.title ?? "");
                  patch({ rate_class_id: item?.id ?? null });
                }}
                getLabel={(item) => item.title}
                getHint={(item) => item.description ?? undefined}
                placeholder="Как у объекта"
                searchPlaceholder="Название класса"
                emptyText="Класс не найден"
                onQueryChange={setClassQuery}
                selectedLabel={classLabel}
                loading={classesQ.isFetching}
              />
              <p className="text-xs text-fg-tertiary">
                Не выбран — возьмётся класс объекта: класс тендера фиксируется снимком на момент
                торга, позже переклассификация объекта его не изменит.
              </p>
            </div>
          </>
        )}

        <div className="grid gap-2">
          <Label htmlFor="tender-title">Предмет тендера</Label>
          <Input
            id="tender-title"
            value={form.title}
            onChange={(e) => patch({ title: e.target.value })}
            required
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="tender-notes">Примечания</Label>
          <Textarea
            id="tender-notes"
            rows={2}
            value={form.notes}
            onChange={(e) => patch({ notes: e.target.value })}
          />
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || pending}>
            {isEdit ? "Сохранить" : "Создать тендер"}
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}

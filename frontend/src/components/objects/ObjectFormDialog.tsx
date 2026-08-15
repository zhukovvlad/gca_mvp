import { useState } from "react";
import { Loader2 } from "lucide-react";

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
import { addDecimalStrings, normalizeDecimalInput } from "@/lib/decimal";
import { useObject, useRateClasses, useUpdateObject } from "@/services/queries";
import type { ObjectInput, ObjectItem } from "@/types/domain";

interface ObjectFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  objectId: number;
}

/**
 * Диалог правки объекта — единственная поверхность ввода ТЭП (спека §2.9).
 *
 * Правит объект **целиком**: название, адрес, класс, обе площади. ТЭП — повод
 * завести диалог, но не весь его состав.
 *
 * Открывается с карточки договора (Ф5 не строит отдельного раздела «Объекты» —
 * критерий пользователя «скорость», §2.9). Объект приходит отдельным запросом
 * `GET /v1/objects/{id}`, а не полями, подмешанными в карточку договора: там
 * уже есть `rate_class_id` — снимок договора, и класс объекта рядом с ним дал
 * бы два поля с одним именем и разным смыслом.
 */
export function ObjectFormDialog({ open, onOpenChange, objectId }: ObjectFormDialogProps) {
  const objectQ = useObject(objectId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        {objectQ.isPending && (
          <div role="status" className="flex items-center gap-2 p-6 text-sm text-fg-secondary">
            <Loader2 className="size-4 animate-spin" /> Загрузка объекта…
          </div>
        )}
        {objectQ.isError && (
          <p role="alert" className="p-6 text-sm text-danger-text">
            Не удалось загрузить объект.
          </p>
        )}
        {/*
          Тело формы монтируется ТОЛЬКО когда данные пришли — это обязательное,
          а не стилистическое решение. Приём из ContractFormDialog (начальные
          значения через useState без эффекта) верен там лишь потому, что
          `contract` приходит готовым пропом. Здесь источник асинхронный:
          тело, смонтированное до ответа, увидело бы undefined в useState и
          осталось бы пустым навсегда — эффекта, который дозаполнил бы поля,
          в этой конструкции нет. `key` пересоздаёт состояние формы, если
          объект сменился.
        */}
        {objectQ.data && (
          <ObjectForm key={objectQ.data.id} object={objectQ.data} onOpenChange={onOpenChange} />
        )}
      </DialogContent>
    </Dialog>
  );
}

interface FormState {
  title: string;
  address: string;
  rate_class_id: number | null;
  area_aboveground_sp: string;
  area_underground_sp: string;
  area_useful_sp: string;
}

function fromObject(object: ObjectItem): FormState {
  return {
    title: object.title,
    address: object.address,
    rate_class_id: object.rate_class_id,
    area_aboveground_sp: object.area_aboveground_sp ?? "",
    area_underground_sp: object.area_underground_sp ?? "",
    area_useful_sp: object.area_useful_sp ?? "",
  };
}

function ObjectForm({
  object,
  onOpenChange,
}: {
  object: ObjectItem;
  onOpenChange: (open: boolean) => void;
}) {
  const [form, setForm] = useState<FormState>(fromObject(object));
  const [classLabel, setClassLabel] = useState(object.rate_class_title ?? "");
  const [classQuery, setClassQuery] = useState("");

  const classesQ = useRateClasses();
  const updateObject = useUpdateObject();

  function patch(fields: Partial<FormState>) {
    setForm((prev) => ({ ...prev, ...fields }));
  }

  // Классы приходят одним списком без пагинации — фильтруем локально, как в
  // ContractFormDialog. Создание класса по месту здесь не нужно: объект уже
  // существует, а класс договора — его отдельный снимок (§2.9).
  const classNeedle = classQuery.trim().toLowerCase();
  const visibleClasses = (classesQ.data ?? []).filter(
    (rateClass) => !classNeedle || rateClass.title.toLowerCase().includes(classNeedle)
  );

  /**
   * Живая общая площадь (спека §2.10) — точным сложением, не `Number`: она
   * станет знаменателем руб/м² в паспорте. Показывается только когда ОБЕ
   * площади дают валидное десятичное число — `addDecimalStrings` возвращает
   * `null` иначе (пустое поле включительно).
   *
   * Полезная площадь в сумму НЕ входит: она часть общей, а не третье слагаемое
   * (спека 2026-08-15 §2.2). Знаменатель руб/м² не меняется этой фичей нигде.
   */
  const totalPreview = addDecimalStrings(
    normalizeDecimalInput(form.area_aboveground_sp),
    normalizeDecimalInput(form.area_underground_sp)
  );

  const canSubmit = form.title.trim().length > 0;
  const pending = updateObject.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;

    // Площади — строками, а не через <input type="number">: последний отдал
    // бы float, а площадь идёт в знаменатель руб/м² (спека §2.10).
    const payload: Partial<ObjectInput> = {
      title: form.title.trim(),
      address: form.address.trim() || null,
      rate_class_id: form.rate_class_id,
      area_aboveground_sp: normalizeDecimalInput(form.area_aboveground_sp) || null,
      area_underground_sp: normalizeDecimalInput(form.area_underground_sp) || null,
      area_useful_sp: normalizeDecimalInput(form.area_useful_sp) || null,
    };

    try {
      await updateObject.mutateAsync({ id: object.id, input: payload });
      onOpenChange(false);
    } catch {
      // Причина отказа уже показана тостом (toastApiError) — диалог остаётся
      // открытым, чтобы человек исправил введённое, а не заполнял заново.
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Правка объекта</DialogTitle>
        <DialogDescription>
          Площади — единственная сверка с порталом СУИП: заводящий сверяет
          вычисленную общую с тем, что перед ним на экране.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="object-title">Название</Label>
          <Input
            id="object-title"
            value={form.title}
            onChange={(e) => patch({ title: e.target.value })}
            required
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="object-address">Адрес</Label>
          <Input
            id="object-address"
            value={form.address}
            onChange={(e) => patch({ address: e.target.value })}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="object-rate-class">Класс объекта</Label>
          <EntityCombobox
            id="object-rate-class"
            items={visibleClasses}
            value={form.rate_class_id}
            onChange={(item) => {
              setClassLabel(item?.title ?? "");
              patch({ rate_class_id: item?.id ?? null });
            }}
            getLabel={(item) => item.title}
            getHint={(item) => item.description ?? undefined}
            placeholder="Класс не выбран"
            searchPlaceholder="Название класса"
            emptyText="Класс не найден."
            onQueryChange={setClassQuery}
            selectedLabel={classLabel}
            loading={classesQ.isFetching}
          />
        </div>

        <div className="grid gap-2 sm:grid-cols-2 sm:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="object-area-aboveground">Наземная площадь, м²</Label>
            <Input
              id="object-area-aboveground"
              inputMode="decimal"
              placeholder="62399.70"
              value={form.area_aboveground_sp}
              onChange={(e) => patch({ area_aboveground_sp: e.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="object-area-underground">Подземная площадь, м²</Label>
            <Input
              id="object-area-underground"
              inputMode="decimal"
              placeholder="13341.30"
              value={form.area_underground_sp}
              onChange={(e) => patch({ area_underground_sp: e.target.value })}
            />
          </div>
        </div>

        {/*
          Полезная — отдельной строкой под парой, а не третьей колонкой в той
          же сетке: она не слагаемое общей (спека 2026-08-15 §2.2), и стоять
          в одном ряду со слагаемыми означало бы обратное. Парой с ними она не
          связана — заводится и очищается независимо (§2.4).
        */}
        <div className="grid gap-2">
          <Label htmlFor="object-area-useful">Полезная площадь, м²</Label>
          <Input
            id="object-area-useful"
            inputMode="decimal"
            placeholder="54210.00"
            value={form.area_useful_sp}
            onChange={(e) => patch({ area_useful_sp: e.target.value })}
          />
          <p className="text-xs text-fg-secondary">
            Часть общей площади. В общую не входит и в руб/м² не участвует.
          </p>
        </div>

        {totalPreview !== null && (
          <p className="text-xs text-fg-secondary">
            Общая площадь:{" "}
            <span data-testid="area-total-preview" className="font-medium text-fg-primary">
              {totalPreview}
            </span>{" "}
            м²
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || pending}>
            Сохранить
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}

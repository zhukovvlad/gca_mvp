import { useState } from "react";

import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Alert } from "@/components/ui/alert";
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
import {
  apiErrorContext,
  apiErrorCode,
  useCreateFamilyFromSuggestion,
  useFamilyCategories,
} from "@/services/queries";
import type { FamilyExistsContext, NewRow } from "@/types/domain";

interface CreateFamilyFormProps {
  row: NewRow & { suggestion_id: number };
  unitLabel: string;
  onClose: () => void;
  onOpenFamily?: (familyId: number) => void;
}

function CreateFamilyForm({ row, unitLabel, onClose, onOpenFamily }: CreateFamilyFormProps) {
  const create = useCreateFamilyFromSuggestion();
  const categoriesQ = useFamilyCategories();
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [title, setTitle] = useState(
    // Ответ «СИСТЕМА» — не имя семьи: имя вводит admin.
    row.is_system ? "" : (row.new_family_name ?? "")
  );
  const [definition, setDefinition] = useState("");
  const [existing, setExisting] = useState<FamilyExistsContext | null>(null);

  // Категория обязательна (спека 3б §2.9): без неё сервер отказал бы `422`.
  const canSave =
    title.trim() !== "" && definition.trim() !== "" && categoryId !== null && !create.isPending;

  function submit() {
    if (categoryId === null) return;
    setExisting(null);
    create.mutate(
      {
        suggestionId: row.suggestion_id,
        input: {
          title: title.trim(),
          definition: definition.trim(),
          family_category_id: categoryId,
        },
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          if (apiErrorCode(error) === "family_exists") {
            setExisting(apiErrorContext<FamilyExistsContext>(error) ?? { family_id: null });
          }
        },
      }
    );
  }

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Новая семья</DialogTitle>
        <DialogDescription>Из строки: «{row.title}»</DialogDescription>
      </DialogHeader>
      <div className="grid gap-3 py-1">
        <div className="grid gap-1.5">
          <Label htmlFor="create-family-title">Имя</Label>
          <Input
            id="create-family-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3 text-[13px] text-fg-secondary">
          Единица
          <span
            data-testid="create-family-unit"
            className="rounded-full border border-border-subtle bg-surface-hover px-2.5 py-0.5"
          >
            {unitLabel}
          </span>
          <span className="text-xs text-fg-tertiary">берётся из строки, одна на семью</span>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="create-family-definition">Определение — обязательно для активации</Label>
          <Textarea
            id="create-family-definition"
            rows={4}
            placeholder="Входит: … Не входит: …"
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
          />
          <p className="text-xs text-fg-tertiary">
            Что входит и что НЕ входит: по определению модель отличает семью от соседних.
          </p>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="create-family-category">Категория</Label>
          <EntitySelect
            id="create-family-category"
            items={categoriesQ.data}
            value={categoryId}
            onChange={setCategoryId}
            getLabel={(c) => c.title}
            placeholder="Выбрать категорию"
          />
          <p className="text-xs text-fg-tertiary">
            Обязательна: природа семьи — работа, инженерная система или затраты и услуги.
          </p>
        </div>
        {existing && (
          <Alert
            data-testid="family-exists"
            className="flex items-center gap-3 border-warning-border bg-warning-soft px-3 py-2 text-[13px] text-warning-text"
          >
            <span className="min-w-0 flex-1">
              Такая семья уже есть: имя и единица совпали с активной семьёй.
            </span>
            {existing.family_id !== null && onOpenFamily && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  onOpenFamily(existing.family_id as number);
                  onClose();
                }}
              >
                Открыть семью
              </Button>
            )}
          </Alert>
        )}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button disabled={!canSave} onClick={submit}>
          Сохранить и активировать
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

interface CreateFamilyDialogProps {
  /** Строка очереди «Новая» с ответом модели; `null` — окно закрыто. */
  row: (NewRow & { suggestion_id: number }) | null;
  unitLabel: string;
  onClose: () => void;
  /** Перейти к существующей семье (ссылка из отказа «такая семья уже есть»). */
  onOpenFamily?: (familyId: number) => void;
}

/**
 * «Завести семью…» (спека semantic-suggestions §2.9): имя подставлено из ответа
 * модели и редактируется, единица — единица контекста, определение и категория обязательны.
 * Одним действием сервер заводит семью, активирует её и назначает контексту.
 */
export function CreateFamilyDialog({ row, unitLabel, onClose, onOpenFamily }: CreateFamilyDialogProps) {
  return (
    <Dialog open={row !== null} onOpenChange={(open) => !open && onClose()}>
      {row !== null && (
        <CreateFamilyForm
          key={row.suggestion_id}
          row={row}
          unitLabel={unitLabel}
          onClose={onClose}
          onOpenFamily={onOpenFamily}
        />
      )}
    </Dialog>
  );
}

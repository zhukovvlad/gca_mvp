import { useState } from "react";

import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
  useDiscardDraft,
  useEditDraft,
  useMergeDraft,
  useWorkFamilies,
} from "@/services/queries";
import type { DraftEditInput, DraftView, FamilyCategory } from "@/types/domain";

import { FAMILY_STATUS_LABEL, pluralRu } from "../labels";

interface EditDraftDialogProps {
  draft: DraftView;
  categories: FamilyCategory[] | undefined;
  onClose: () => void;
}

/** «Править…»: имя, определение и категория черновика; уходят только изменённые поля. */
function EditDraftDialog({ draft, categories, onClose }: EditDraftDialogProps) {
  const edit = useEditDraft();
  const [title, setTitle] = useState(draft.title);
  const [definition, setDefinition] = useState(draft.definition);
  const [categoryId, setCategoryId] = useState<number | null>(draft.family_category_id);

  const changes: DraftEditInput = {};
  if (title.trim() !== draft.title) changes.title = title.trim();
  if (definition.trim() !== draft.definition) changes.definition = definition.trim();
  if (categoryId !== null && categoryId !== draft.family_category_id) {
    changes.family_category_id = categoryId;
  }
  const canSave =
    title.trim() !== "" &&
    definition.trim() !== "" &&
    Object.keys(changes).length > 0 &&
    !edit.isPending;

  return (
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Править черновик</DialogTitle>
        <DialogDescription>
          Имя и определение станут именем и определением семьи при активации.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-3 py-1">
        <div className="grid gap-1.5">
          <Label htmlFor="draft-edit-title">Имя</Label>
          <Input id="draft-edit-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="draft-edit-definition">Определение</Label>
          <Textarea
            id="draft-edit-definition"
            rows={4}
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="draft-edit-category">Категория</Label>
          <EntitySelect
            id="draft-edit-category"
            items={categories}
            value={categoryId}
            onChange={setCategoryId}
            getLabel={(c) => c.title}
            placeholder="Выбрать категорию"
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button
          disabled={!canSave}
          onClick={() =>
            edit.mutate({ draftId: draft.id, name: draft.title, input: changes }, { onSuccess: onClose })
          }
        >
          Сохранить
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

/** Цель слияния в выборе: `draft:<id>` — открытый черновик, `family:<id>` — активная семья единицы. */
interface MergeOption {
  id: string;
  label: string;
}

interface MergeDraftDialogProps {
  draft: DraftView;
  unitId: number | null;
  /** Открытые черновики того же открытия, кроме самого источника. */
  otherDrafts: DraftView[];
  onClose: () => void;
}

/**
 * «Слить с…»: цель — другой открытый черновик или активная семья той же единицы. Члены
 * остаются при источнике; семья при слиянии не меняется ни полем — её строки придут
 * предложениями при перезапросе (спека 3б §2.4).
 */
function MergeDraftDialog({ draft, unitId, otherDrafts, onClose }: MergeDraftDialogProps) {
  const merge = useMergeDraft();
  const familiesQ = useWorkFamilies("active");
  const [choice, setChoice] = useState<string | null>(null);

  const options: MergeOption[] = [
    ...otherDrafts.map((d) => ({ id: `draft:${d.id}`, label: `Черновик «${d.title}»` })),
    ...(familiesQ.data ?? [])
      .filter((f) => f.unit_id === unitId)
      .map((f) => ({ id: `family:${f.id}`, label: `Активная семья «${f.title}»` })),
  ];

  function submit() {
    if (choice === null) return;
    const [kind, rawId] = choice.split(":");
    const target =
      kind === "draft" ? { target_draft_id: Number(rawId) } : { target_family_id: Number(rawId) };
    merge.mutate({ draftId: draft.id, name: draft.title, target }, { onSuccess: onClose });
  }

  return (
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>Слить «{draft.title}» с…</DialogTitle>
        <DialogDescription>
          Строки черновика останутся при нём и будут показаны у цели. Имя, определение и категория
          цели не меняются; семья не меняется ни полем.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5 py-1">
        <Label htmlFor="draft-merge-target">Цель слияния</Label>
        <EntitySelect
          id="draft-merge-target"
          items={options}
          value={choice}
          onChange={setChoice}
          getLabel={(o) => o.label}
          placeholder="Выбрать черновик или семью"
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button disabled={choice === null || merge.isPending} onClick={submit}>
          Слить
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

interface DraftCardProps {
  draft: DraftView;
  unitId: number | null;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  categories: FamilyCategory[] | undefined;
  /** Открытые черновики того же открытия, кроме этого: цели слияния. */
  otherDrafts: DraftView[];
  /** Подпись отказа активации у этого черновика; отметки при отказе не сбрасываются. */
  refusal?: string;
}

/**
 * Черновик новой семьи (экран 3 макета): отметка, имя с «похоже на активную «…»», определение,
 * три примера наименований, выбор категории, число строк и действия «Править…», «Слить с…»,
 * «Отбросить». Предложение слить с похожей активной семьёй — кнопкой в одно нажатие.
 */
export function DraftCard({
  draft,
  unitId,
  checked,
  onCheckedChange,
  categories,
  otherDrafts,
  refusal,
}: DraftCardProps) {
  const edit = useEditDraft();
  const merge = useMergeDraft();
  const discard = useDiscardDraft();
  const [dialog, setDialog] = useState<"edit" | "merge" | null>(null);

  const similarActive = draft.similar_family_id !== null && draft.similar_family_status === "active";
  // «+ N» — как в макете: строки черновика минус показанные примеры.
  const extra = draft.rows - draft.examples.length;

  return (
    <div
      data-testid="draft-card"
      className="flex gap-3.5 border-b border-border-subtle px-4 py-3.5 last:border-b-0"
    >
      <Checkbox
        className="mt-1"
        aria-label={`Отметить черновик «${draft.title}»`}
        checked={checked}
        onCheckedChange={(next) => onCheckedChange(next === true)}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[15px] font-semibold text-fg">{draft.title}</span>
          {draft.similar_family_id !== null && (
            <Badge variant="outline" className="border-warning-border bg-warning-soft text-warning-text">
              похоже на активную «{draft.similar_family_title}»
              {draft.similar_family_status !== "active" &&
                draft.similar_family_status !== null &&
                ` (${FAMILY_STATUS_LABEL[draft.similar_family_status]})`}
            </Badge>
          )}
        </div>
        <p className="mt-0.5 mb-1.5 text-[13px] text-fg-secondary">{draft.definition}</p>
        {draft.examples.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 text-xs text-fg-secondary">
            {draft.examples.map((example) => (
              <span
                key={example}
                className="rounded-md border border-border-subtle bg-surface-hover px-1.5"
              >
                {example}
              </span>
            ))}
            {extra > 0 && <span>+ {extra}</span>}
          </div>
        )}
        <div className="mt-2 flex items-center gap-2 text-[13px]">
          <span className="text-fg-secondary">Категория:</span>
          <EntitySelect
            className="w-52"
            items={categories}
            value={draft.family_category_id}
            onChange={(id) =>
              id !== null &&
              edit.mutate({ draftId: draft.id, name: draft.title, input: { family_category_id: id } })
            }
            getLabel={(c) => c.title}
            placeholder="Выбрать категорию"
          />
        </div>
        {refusal && (
          <p role="alert" data-testid="draft-refusal" className="mt-2 text-[13px] text-danger-text">
            {refusal}
          </p>
        )}
      </div>
      <div className="w-28 flex-none text-right text-[13px] tabular-nums">
        <b>{draft.rows}</b> {pluralRu(draft.rows, "строка", "строки", "строк")}
      </div>
      <div className="flex flex-none flex-col items-stretch gap-1.5">
        <Button variant="outline" size="sm" onClick={() => setDialog("edit")}>
          Править…
        </Button>
        {similarActive && (
          <Button
            size="sm"
            disabled={merge.isPending}
            onClick={() =>
              merge.mutate({
                draftId: draft.id,
                name: draft.title,
                target: { target_family_id: draft.similar_family_id as number },
              })
            }
          >
            Слить с «{draft.similar_family_title}»
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={() => setDialog("merge")}>
          Слить с…
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={discard.isPending}
          onClick={() => discard.mutate({ draftId: draft.id, name: draft.title })}
        >
          Отбросить
        </Button>
      </div>

      <Dialog open={dialog !== null} onOpenChange={(open) => !open && setDialog(null)}>
        {dialog === "edit" && (
          <EditDraftDialog draft={draft} categories={categories} onClose={() => setDialog(null)} />
        )}
        {dialog === "merge" && (
          <MergeDraftDialog
            draft={draft}
            unitId={unitId}
            otherDrafts={otherDrafts}
            onClose={() => setDialog(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

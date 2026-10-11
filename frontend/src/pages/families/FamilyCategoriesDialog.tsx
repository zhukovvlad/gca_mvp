import { Plus } from "lucide-react";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateFamilyCategory,
  useDeleteFamilyCategory,
  useFamilyCategories,
  useUpdateFamilyCategory,
} from "@/services/queries";
import type { FamilyCategory } from "@/types/domain";

/** Форма под таблицей: новая категория или правка выбранной. */
type FormState = { mode: "create" } | { mode: "edit"; category: FamilyCategory };

interface CategoryFormProps {
  state: FormState;
  onDone: () => void;
}

function CategoryForm({ state, onDone }: CategoryFormProps) {
  const create = useCreateFamilyCategory();
  const update = useUpdateFamilyCategory();
  const [title, setTitle] = useState(state.mode === "edit" ? state.category.title : "");
  const [definition, setDefinition] = useState(
    state.mode === "edit" ? state.category.definition : ""
  );

  const pending = create.isPending || update.isPending;
  const canSave = title.trim() !== "" && definition.trim() !== "" && !pending;

  async function submit() {
    const input = { title: title.trim(), definition: definition.trim() };
    try {
      if (state.mode === "edit") {
        await update.mutateAsync({ id: state.category.id, input });
      } else {
        await create.mutateAsync(input);
      }
      onDone();
    } catch {
      // Отказ — подписью в тосте мутации; форма остаётся, чтобы поправить ввод.
    }
  }

  return (
    <div className="grid gap-3 rounded-[10px] border border-border-subtle bg-surface-hover p-3">
      <div className="grid gap-1.5">
        <Label htmlFor="category-title">Имя категории</Label>
        <Input id="category-title" value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="category-definition">Определение категории</Label>
        <Textarea
          id="category-definition"
          rows={3}
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
        />
        <p className="text-xs text-fg-tertiary">
          Определение видит модель, когда предлагает категорию черновику.
        </p>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onDone}>
          Отмена
        </Button>
        <Button disabled={!canSave} onClick={() => void submit()}>
          {state.mode === "edit" ? "Сохранить" : "Добавить"}
        </Button>
      </div>
    </div>
  );
}

interface FamilyCategoriesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Окно «Категории семей» (спека 3б §2.9, §2.13, макет К2): справочник, который admin пополняет
 * сам. Переименовать можно всегда, удалить — только категорию без семей: кнопка неактивна при
 * `family_count > 0`, а отказ сервера `category_in_use` (семья успела сослаться) печатается
 * подписью.
 */
export function FamilyCategoriesDialog({ open, onOpenChange }: FamilyCategoriesDialogProps) {
  const categoriesQ = useFamilyCategories();
  const remove = useDeleteFamilyCategory();
  const [form, setForm] = useState<FormState | null>(null);
  const [deleting, setDeleting] = useState<FamilyCategory | null>(null);

  const categories = categoriesQ.data ?? [];

  function handleOpenChange(next: boolean) {
    if (!next) setForm(null);
    onOpenChange(next);
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Категории семей</DialogTitle>
            <DialogDescription>
              Категория — природа семьи (работа, инженерная система, затраты и услуги). Код при
              переименовании не меняется.
            </DialogDescription>
          </DialogHeader>

          {categoriesQ.isError && (
            <p className="text-sm text-destructive">Не удалось получить справочник категорий.</p>
          )}

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs font-normal text-fg-tertiary">Категория</TableHead>
                <TableHead className="text-xs font-normal text-fg-tertiary">
                  Определение (видит модель)
                </TableHead>
                <TableHead className="text-right text-xs font-normal text-fg-tertiary">Семей</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {categories.map((category) => (
                <TableRow key={category.id}>
                  <TableCell className="font-medium text-fg">{category.title}</TableCell>
                  <TableCell className="whitespace-normal text-fg-secondary">
                    {category.definition}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{category.family_count}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setForm({ mode: "edit", category })}
                      >
                        Править
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={category.family_count > 0 || remove.isPending}
                        title={
                          category.family_count > 0
                            ? "Удалить можно только категорию без семей"
                            : undefined
                        }
                        onClick={() => setDeleting(category)}
                      >
                        Удалить
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {form === null ? (
            <div>
              <Button variant="outline" size="sm" onClick={() => setForm({ mode: "create" })}>
                <Plus className="size-4" /> Добавить категорию
              </Button>
            </div>
          ) : (
            <CategoryForm
              key={form.mode === "edit" ? form.category.id : "create"}
              state={form}
              onDone={() => setForm(null)}
            />
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Закрыть
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(next) => !next && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить категорию «{deleting?.title}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Удалить можно только категорию без семей. Черновики открытия, в которых она была
              выбрана, останутся без категории.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (deleting) remove.mutate(deleting.id);
                    setDeleting(null);
                  }}
                >
                  Удалить категорию
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

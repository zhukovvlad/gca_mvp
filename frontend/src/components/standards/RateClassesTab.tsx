import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
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
import { useCreateRateClass, useDeleteRateClass, useRateClasses } from "@/services/queries";
import type { RateClass } from "@/types/domain";

/**
 * Классы объектов — вкладка экрана «Нормативы» (решение §6.1).
 *
 * Отдельного экрана у классов нет намеренно: `AGENTS.md` §7.3 описывает экран
 * нормативов как «классы, ставки, периоды», то есть классы уже отданы этому
 * экрану источником истины. Классов мало, меняются они редко, право — `admin`,
 * ровно как у нормативов.
 *
 * Удаление показывает, чем класс занят: сервер отказывает, если на него ссылаются
 * договоры или нормативы, потому что класс договора — снимок, держащий историю
 * отклонений.
 */
export function RateClassesTab() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [toDelete, setToDelete] = useState<RateClass | null>(null);

  const classesQ = useRateClasses();
  const create = useCreateRateClass();
  const remove = useDeleteRateClass();

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    try {
      await create.mutateAsync({ title: title.trim(), description: description.trim() || null });
      setTitle("");
      setDescription("");
    } catch {
      // Причина в тосте — как правило, название занято.
    }
  }

  return (
    <div className="grid gap-4">
      <Surface>
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
          <div className="grid gap-2">
            <Label htmlFor="rate-class-title">Название класса</Label>
            <Input
              id="rate-class-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Жилые дома"
            />
          </div>
          <div className="grid flex-1 gap-2">
            <Label htmlFor="rate-class-description">Описание</Label>
            <Input
              id="rate-class-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={!title.trim() || create.isPending}>
            <Plus className="size-4" /> Добавить класс
          </Button>
        </form>
      </Surface>

      {classesQ.isPending && <Skeleton className="h-32 w-full" />}

      {classesQ.data?.length === 0 && (
        <EmptyState
          title="Классов нет"
          description="Класс нужен договору: по нему сравниваются нормативы."
        />
      )}

      {classesQ.data && classesQ.data.length > 0 && (
        <Surface padding="none" className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Класс</TableHead>
                <TableHead>Описание</TableHead>
                <TableHead className="text-right">Договоров</TableHead>
                <TableHead className="text-right">Объектов</TableHead>
                <TableHead className="text-right">Нормативов</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {classesQ.data.map((rateClass) => (
                <TableRow key={rateClass.id}>
                  <TableCell className="font-medium text-fg">{rateClass.title}</TableCell>
                  <TableCell className="text-fg-secondary">
                    {rateClass.description ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {rateClass.contracts_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {rateClass.objects_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {rateClass.standards_count}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Удалить класс ${rateClass.title}`}
                      onClick={() => setToDelete(rateClass)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Surface>
      )}

      <AlertDialog open={toDelete !== null} onOpenChange={(open) => !open && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить класс «{toDelete?.title}»?</AlertDialogTitle>
            <AlertDialogDescription>
              {toDelete && (toDelete.contracts_count > 0 || toDelete.standards_count > 0)
                ? `Класс используется: договоров — ${toDelete.contracts_count}, нормативов — ${toDelete.standards_count}. Сервер откажет: класс в договоре это снимок, который держит историю отклонений.`
                : "Объекты, у которых этот класс стоит значением по умолчанию, потеряют дефолт. История договоров не изменится — в них класс зафиксирован снимком."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (toDelete) remove.mutate(toDelete.id);
                    setToDelete(null);
                  }}
                >
                  Удалить
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

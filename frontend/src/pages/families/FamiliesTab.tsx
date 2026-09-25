import { useState } from "react";
import { Plus } from "lucide-react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
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
import { Badge } from "@/components/ui/badge";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  useActivateWorkFamily,
  useArchiveWorkFamily,
  useCreateWorkFamily,
  useMergeWorkFamilies,
  useUnits,
  useUpdateWorkFamily,
  useWorkFamilies,
} from "@/services/queries";
import type { WorkFamily, WorkFamilyStatus } from "@/types/domain";

const ANY = "any";
const STATUS_OPTIONS: WorkFamilyStatus[] = ["draft", "active", "archived"];

/**
 * Семьи работ — вкладка «Семьи» (спека §2.7, §2.10).
 *
 * Фильтр по статусу открывается на `draft`: после seed это штатное первое
 * состояние экрана — 42 черновика, путь «дописать определение →
 * активировать» проходит здесь (план задачи 13, «Утверждения»).
 */
export function FamiliesTab() {
  const [statusFilter, setStatusFilter] = useState<string>("draft");
  const [unitFilter, setUnitFilter] = useState<string>(ANY);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<WorkFamily | null>(null);
  const [merging, setMerging] = useState<WorkFamily | null>(null);
  const [archiving, setArchiving] = useState<WorkFamily | null>(null);

  const unitsQ = useUnits();
  const familiesQ = useWorkFamilies(
    statusFilter === ANY ? undefined : (statusFilter as WorkFamilyStatus),
    unitFilter === ANY ? undefined : Number(unitFilter)
  );
  const activate = useActivateWorkFamily();
  const archive = useArchiveWorkFamily();

  const items = familiesQ.data ?? [];

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor="family-status-filter" className="text-xs text-fg-tertiary">Статус</Label>
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v ?? ANY)}>
              <SelectTrigger id="family-status-filter" className="w-48">
                <SelectValue>{(raw) => (!raw || raw === ANY ? "Любой статус" : raw)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любой статус</SelectItem>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="family-unit-filter" className="text-xs text-fg-tertiary">Единица (фильтр)</Label>
            <Select value={unitFilter} onValueChange={(v) => setUnitFilter(v ?? ANY)}>
              <SelectTrigger id="family-unit-filter" className="w-48">
                <SelectValue>
                  {(raw) =>
                    !raw || raw === ANY
                      ? "Любая единица"
                      : (unitsQ.data?.find((u) => String(u.id) === raw)?.name ?? "Любая единица")
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любая единица</SelectItem>
                {(unitsQ.data ?? []).map((u) => (
                  <SelectItem key={u.id} value={String(u.id)}>{u.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" /> Новая семья
        </Button>
      </div>

      {familiesQ.isPending && <Skeleton className="h-40 w-full" />}

      {familiesQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить семьи." />
      )}

      {familiesQ.isSuccess && items.length === 0 && (
        <EmptyState title="Семей нет" description="Семьи заводятся здесь либо загружаются seed-файлом." />
      )}

      {familiesQ.isSuccess && items.length > 0 && (
        <Surface padding="none" className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Название</TableHead>
                <TableHead>Единица</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Определение</TableHead>
                <TableHead className="text-right">Контекстов</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((family) => {
                const hasDefinition = Boolean(family.definition && family.definition.trim());
                return (
                  <TableRow key={family.id}>
                    <TableCell className="font-medium text-fg">{family.title}</TableCell>
                    <TableCell>{family.unit_code ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{family.status}</Badge>
                    </TableCell>
                    <TableCell>
                      {hasDefinition ? "есть" : "нет"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{family.context_count}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button size="xs" variant="outline" onClick={() => setEditing(family)}>
                          Правка
                        </Button>
                        {family.status === "draft" && (
                          <Button
                            size="xs"
                            variant="outline"
                            aria-label={`Активировать семью ${family.title}`}
                            disabled={!hasDefinition || activate.isPending}
                            onClick={() => activate.mutate(family.id)}
                          >
                            Активировать
                          </Button>
                        )}
                        {family.status === "active" && (
                          <Button size="xs" variant="outline" onClick={() => setMerging(family)}>
                            Слить
                          </Button>
                        )}
                        {family.status !== "archived" && (
                          <Button
                            size="xs"
                            variant="ghost"
                            aria-label={`Архивировать семью ${family.title}`}
                            onClick={() => setArchiving(family)}
                          >
                            Архивировать
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Surface>
      )}

      <CreateFamilyDialog open={createOpen} onOpenChange={setCreateOpen} />
      <EditFamilyDialog family={editing} onOpenChange={(open) => !open && setEditing(null)} />
      <MergeFamilyDialog family={merging} onOpenChange={(open) => !open && setMerging(null)} />

      <AlertDialog open={archiving !== null} onOpenChange={(open) => !open && setArchiving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Архивировать семью «{archiving?.title}»?</AlertDialogTitle>
            <AlertDialogDescription>
              {archiving && archiving.context_count > 0
                ? `У семьи есть привязанные контексты: ${archiving.context_count}. Сервер откажет — сначала нужно снять привязки.`
                : "Архивная семья перестаёт предлагаться для назначения контексту."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (archiving) archive.mutate(archiving.id);
                    setArchiving(null);
                  }}
                >
                  Архивировать
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function CreateFamilyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [title, setTitle] = useState("");
  const [unitName, setUnitName] = useState("");
  const [definition, setDefinition] = useState("");
  const create = useCreateWorkFamily();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      await create.mutateAsync({
        title: title.trim(),
        unit_name: unitName.trim() || null,
        definition: definition.trim() || null,
      });
      setTitle("");
      setUnitName("");
      setDefinition("");
      onOpenChange(false);
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Новая семья работ</DialogTitle>
            <DialogDescription>
              Одна единица на семью (§1.14). Активация потребует определения.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-4">
            <div className="grid gap-2">
              <Label htmlFor="new-family-title">Название (обязательно)</Label>
              <Input id="new-family-title" value={title} onChange={(e) => setTitle(e.target.value)} required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="new-family-unit">Единица</Label>
              <Input id="new-family-unit" value={unitName} onChange={(e) => setUnitName(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="new-family-definition">Определение</Label>
              <Textarea id="new-family-definition" value={definition} onChange={(e) => setDefinition(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!title.trim() || create.isPending}>
              Создать
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditFamilyDialog({
  family,
  onOpenChange,
}: {
  family: WorkFamily | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={family !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        {family && <EditFamilyForm family={family} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function EditFamilyForm({ family, onDone }: { family: WorkFamily; onDone: () => void }) {
  const [title, setTitle] = useState(family.title);
  const [unitName, setUnitName] = useState(family.unit_code ?? "");
  const [definition, setDefinition] = useState(family.definition ?? "");
  const update = useUpdateWorkFamily();

  const unitLocked = family.context_count > 0;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await update.mutateAsync({
        id: family.id,
        input: {
          title: title.trim(),
          definition: definition.trim() || null,
          // `unit_name` отсутствует в теле, пока привязки есть — «не трогать»,
          // а не «снять единицу» (решение оркестратора, план задачи 13).
          ...(unitLocked ? {} : { unit_name: unitName.trim() || null }),
        },
      });
      onDone();
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Правка семьи «{family.title}»</DialogTitle>
      </DialogHeader>
      <div className="grid gap-3 py-4">
        <div className="grid gap-2">
          <Label htmlFor="edit-family-title">Название</Label>
          <Input id="edit-family-title" value={title} onChange={(e) => setTitle(e.target.value)} required />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="edit-family-unit">Единица</Label>
          <Input
            id="edit-family-unit"
            value={unitLocked ? (family.unit_code ?? "") : unitName}
            onChange={(e) => setUnitName(e.target.value)}
            disabled={unitLocked}
          />
          {unitLocked && (
            <p className="text-xs text-fg-tertiary">
              Единица недоступна: привязано контекстов — {family.context_count}
            </p>
          )}
        </div>
        <div className="grid gap-2">
          <Label htmlFor="edit-family-definition">Определение</Label>
          <Textarea id="edit-family-definition" value={definition} onChange={(e) => setDefinition(e.target.value)} />
        </div>
      </div>
      <DialogFooter>
        <Button type="submit" disabled={!title.trim() || update.isPending}>
          Сохранить
        </Button>
      </DialogFooter>
    </form>
  );
}

function MergeFamilyDialog({
  family,
  onOpenChange,
}: {
  family: WorkFamily | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [targetId, setTargetId] = useState<number | null>(null);
  const activeFamilies = useWorkFamilies("active");
  const merge = useMergeWorkFamilies();

  const candidates = (activeFamilies.data ?? []).filter((f) => f.id !== family?.id);

  async function handleMerge() {
    if (!family || targetId === null) return;
    try {
      await merge.mutateAsync({ id: family.id, targetFamilyId: targetId });
      setTargetId(null);
      onOpenChange(false);
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <Dialog open={family !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Слить семью «{family?.title}»</DialogTitle>
          <DialogDescription>
            Контексты источника переезжают в целевую семью; источник архивируется.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 py-4">
          <Label htmlFor="merge-target-family">Целевая семья</Label>
          <EntitySelect
            id="merge-target-family"
            items={candidates}
            value={targetId}
            onChange={(v) => setTargetId(v as number | null)}
            getLabel={(f) => f.title}
            placeholder="Выбрать семью"
          />
        </div>
        <DialogFooter>
          <Button disabled={targetId === null || merge.isPending} onClick={handleMerge}>
            Слить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
